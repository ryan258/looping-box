from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .phase1 import DELTA_SCHEMA, _rel, _resolve_under_root, _sha256_file, _utc_now, _write_json
from .worker import run_worker


WORLD_STATE_SCHEMA = "looping-box.world-state.v1"
SUPERVISOR_BLOCKED_SCHEMA = "looping-box.supervisor.blocked.v1"

DEFAULT_CONFIG = {
    "schema": "looping-box.super-loop.config.v1",
    "max_files_per_cycle": 20,
    "max_payload_bytes": 65536,
    "max_worker_runtime_seconds": 30,
    "stale_lock_seconds": 300,
    "workers": ["context_builder", "execution_engine"],
    "dependencies": [
        {"from": "phase1_delta", "to": "context_builder"},
        {"from": "context_builder", "to": "execution_engine"},
    ],
}


def run_supervisor(root: Path | str, *, now: str | None = None) -> dict[str, Any]:
    """Run one deterministic supervisor pass."""
    root_path = Path(root).resolve()
    generated_at = now or _utc_now()
    config = _read_config(root_path)
    lock_path = _resolve_under_root(root_path, ".looping_box.lock")
    _acquire_lock(lock_path, generated_at, int(config.get("stale_lock_seconds", 300)))
    try:
        result = _run_supervisor_locked(root_path, generated_at, config)
    finally:
        if lock_path.exists():
            lock_path.unlink()
    return result


def load_world_state(root: Path | str) -> dict[str, Any]:
    root_path = Path(root).resolve()
    state_path = _resolve_under_root(root_path, ".world_state.json")
    if not state_path.exists():
        return _default_world_state()
    state = _read_json(state_path)
    if state.get("schema") != WORLD_STATE_SCHEMA:
        raise ValueError(f"unsupported world state schema: {state.get('schema')!r}")
    state.setdefault("registered_loops", ["phase1", "context_builder", "execution_engine"])
    state.setdefault("worker_states", {})
    state.setdefault("observed_deltas", [])
    state.setdefault("dirty", False)
    state.setdefault("dependencies", list(DEFAULT_CONFIG["dependencies"]))
    state.setdefault("recovery", _clear_recovery())
    state.setdefault("runs", [])
    state.setdefault("leases", {})
    return state


_RESOURCE_LIMIT_REASONS = {"file_count_limit", "payload_size_limit", "worker_timeout"}


def status_summary(root: Path | str) -> str:
    root_path = Path(root).resolve()
    state = load_world_state(root_path)
    recovery = state["recovery"]
    if recovery.get("operator_action_required"):
        payload = recovery.get("pending_review_payload") or "cache/supervisor/blocked.json"
        # Resource-limit blocks (see docs/RECOVERY.md) have no source file to
        # handle; the fix is a config/super_loop.json change or a smaller batch.
        # Content-review blocks point at staging/pending_review.json instead.
        if recovery.get("blocked_reason") in _RESOURCE_LIMIT_REASONS:
            next_step = (
                "inspect the pending payload, then raise the matching limit in "
                "config/super_loop.json or shrink the batch, then run ./startday.sh"
            )
        else:
            next_step = "inspect the pending payload, handle the source file, then run ./startday.sh"
        return "\n".join(
            [
                "status: operator action required",
                f"pending: {payload}",
                f"Next: {next_step}",
            ]
        )
    if recovery.get("last_error"):
        return "\n".join(
            [
                "status: failed",
                f"error: {recovery['last_error']}",
                "Next: fix the error and run python3 -m looping_box.supervisor --once",
            ]
        )
    return "\n".join(
        [
            "status: clear",
            f"last run: {state.get('last_run_at', 'never')}",
            "Next: ./startday.sh",
        ]
    )


def _run_supervisor_locked(
    root: Path,
    generated_at: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    state = load_world_state(root)
    delta_paths = _phase1_delta_paths(root)
    observed = set(state["observed_deltas"])
    new_deltas = [path for path in delta_paths if _rel(root, path) not in observed]
    new_delta_refs = [_rel(root, path) for path in new_deltas]
    changed_file_count = _changed_file_count(new_deltas)

    max_files = int(config.get("max_files_per_cycle", DEFAULT_CONFIG["max_files_per_cycle"]))
    if changed_file_count > max_files:
        return _blocked(
            root,
            state,
            generated_at,
            "file_count_limit",
            f"{changed_file_count} changed files exceeds limit {max_files}",
            new_delta_refs,
        )

    plan: list[str] = []
    worker_outputs: list[dict[str, Any]] = []
    # Resource-limit blocks (worker_timeout, payload_size_limit) must be as durable
    # as file_count_limit: a worker call already persists its own state (consumed
    # inputs, last-seen hash) as a side effect, so a post-hoc block that doesn't
    # undo that would silently self-heal on the next run with no operator action.
    # Snapshot state before each worker runs and restore it if we end up blocking.
    ran_workers: dict[str, str | None] = {}
    if new_deltas and _can_route(config, "phase1_delta", "context_builder"):
        plan.append("context_builder")

    context_needs_execution_before_run = _execution_needs_context(root)
    if (
        context_needs_execution_before_run
        and "context_builder" not in plan
        and _can_route(config, "context_builder", "execution_engine")
    ):
        plan.append("execution_engine")

    if "context_builder" in plan:
        ran_workers["context_builder"] = _snapshot_worker_state(root, "context_builder")
        context_output = _run_worker_with_runtime(root, "context_builder", generated_at, config)
        worker_outputs.append(context_output)
        if _is_worker_timeout(context_output):
            return _blocked_with_rollback(
                root, state, generated_at, "worker_timeout",
                context_output["errors"][0]["message"], new_delta_refs, ran_workers,
            )
        if (
            context_output["status"] in {"complete", "blocked"}
            and _can_route(config, "context_builder", "execution_engine")
            and "execution_engine" not in plan
        ):
            plan.append("execution_engine")

    if "execution_engine" in plan:
        ran_workers["execution_engine"] = _snapshot_worker_state(root, "execution_engine")
        execution_output = _run_worker_with_runtime(root, "execution_engine", generated_at, config)
        worker_outputs.append(execution_output)
        if _is_worker_timeout(execution_output):
            return _blocked_with_rollback(
                root, state, generated_at, "worker_timeout",
                execution_output["errors"][0]["message"], new_delta_refs, ran_workers,
            )

    payload_limit = int(config.get("max_payload_bytes", DEFAULT_CONFIG["max_payload_bytes"]))
    oversized = _oversized_artifact(root, worker_outputs, payload_limit)
    if oversized is not None:
        return _blocked_with_rollback(
            root, state, generated_at, "payload_size_limit",
            f"{oversized} exceeds limit {payload_limit} bytes", new_delta_refs, ran_workers,
        )

    status = _overall_status(worker_outputs)
    state["registered_loops"] = ["phase1"] + list(config.get("workers", DEFAULT_CONFIG["workers"]))
    state["dependencies"] = list(config.get("dependencies", DEFAULT_CONFIG["dependencies"]))
    state["dirty"] = bool(plan)
    state["last_run_at"] = generated_at
    if status in {"complete", "blocked", "idle"}:
        state["observed_deltas"] = sorted(observed.union(new_delta_refs))
    state["worker_states"].update(_worker_states(root, worker_outputs))
    state["recovery"] = _recovery_from_outputs(root, worker_outputs)
    state["runs"].append(
        {
            "generated_at": generated_at,
            "status": status,
            "plan": plan,
            "source_deltas": new_delta_refs,
        }
    )
    state["runs"] = state["runs"][-50:]
    _write_json(_resolve_under_root(root, ".world_state.json"), state)
    result = {
        "schema": "looping-box.supervisor.run.v1",
        "generated_at": generated_at,
        "status": status,
        "plan": plan,
        "worker_outputs": worker_outputs,
        "recovery": state["recovery"],
    }
    _append_audit(root, generated_at, result)
    return result


def _worker_state_path(root: Path, worker_id: str) -> Path:
    return _resolve_under_root(root, f"cache/workers/{worker_id}/state.json")


def _snapshot_worker_state(root: Path, worker_id: str) -> str | None:
    path = _worker_state_path(root, worker_id)
    return path.read_text(encoding="utf-8") if path.exists() else None


def _restore_worker_state(root: Path, worker_id: str, snapshot: str | None) -> None:
    path = _worker_state_path(root, worker_id)
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(snapshot, encoding="utf-8")


def _is_worker_timeout(output: dict[str, Any]) -> bool:
    return output["status"] == "blocked" and any(
        error.get("code") == "worker_timeout" for error in output["errors"]
    )


def _blocked_with_rollback(
    root: Path,
    state: dict[str, Any],
    generated_at: str,
    reason: str,
    message: str,
    source_deltas: list[str],
    ran_workers: dict[str, str | None],
) -> dict[str, Any]:
    # Undo the state each ran worker already persisted so the exact same work
    # is retried next run instead of the block silently clearing itself.
    for worker_id, snapshot in ran_workers.items():
        _restore_worker_state(root, worker_id, snapshot)
    return _blocked(root, state, generated_at, reason, message, source_deltas)


def _blocked(
    root: Path,
    state: dict[str, Any],
    generated_at: str,
    reason: str,
    message: str,
    source_deltas: list[str],
) -> dict[str, Any]:
    payload_path = _resolve_under_root(root, "cache/supervisor/blocked.json")
    payload = {
        "schema": SUPERVISOR_BLOCKED_SCHEMA,
        "generated_at": generated_at,
        "reason": reason,
        "message": message,
        "source_deltas": source_deltas,
    }
    _write_json(payload_path, payload)
    state["dirty"] = True
    state["last_run_at"] = generated_at
    state["recovery"] = {
        "last_error": None,
        "blocked_reason": reason,
        "pending_review_payload": _rel(root, payload_path),
        "operator_action_required": True,
    }
    state["runs"].append(
        {
            "generated_at": generated_at,
            "status": "blocked",
            "plan": [],
            "source_deltas": source_deltas,
        }
    )
    state["runs"] = state["runs"][-50:]
    _write_json(_resolve_under_root(root, ".world_state.json"), state)
    result = {
        "schema": "looping-box.supervisor.run.v1",
        "generated_at": generated_at,
        "status": "blocked",
        "plan": [],
        "worker_outputs": [],
        "recovery": state["recovery"],
    }
    _append_audit(root, generated_at, result)
    return result


def _default_world_state() -> dict[str, Any]:
    return {
        "schema": WORLD_STATE_SCHEMA,
        "registered_loops": ["phase1", "context_builder", "execution_engine"],
        "worker_states": {},
        "observed_deltas": [],
        "dirty": False,
        "dependencies": list(DEFAULT_CONFIG["dependencies"]),
        "recovery": _clear_recovery(),
        "runs": [],
        "leases": {},
    }


def _clear_recovery() -> dict[str, Any]:
    return {
        "last_error": None,
        "blocked_reason": None,
        "pending_review_payload": None,
        "operator_action_required": False,
    }


def _read_config(root: Path) -> dict[str, Any]:
    config_path = _resolve_under_root(root, "config/super_loop.json")
    config = dict(DEFAULT_CONFIG)
    if config_path.exists():
        config.update(_read_json(config_path))
    return config


def _phase1_delta_paths(root: Path) -> list[Path]:
    delta_dir = _resolve_under_root(root, "cache/deltas")
    return sorted(delta_dir.glob("*.json")) if delta_dir.exists() else []


def _can_route(config: dict[str, Any], source: str, target: str) -> bool:
    workers = set(config.get("workers", DEFAULT_CONFIG["workers"]))
    dependencies = {
        (edge.get("from"), edge.get("to"))
        for edge in config.get("dependencies", DEFAULT_CONFIG["dependencies"])
    }
    return target in workers and (source, target) in dependencies


def _changed_file_count(delta_paths: list[Path]) -> int:
    count = 0
    for path in delta_paths:
        try:
            delta = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if delta.get("schema") == DELTA_SCHEMA:
            count += len(delta.get("changes", []))
    return count


def _execution_needs_context(root: Path) -> bool:
    context_path = _resolve_under_root(root, "cache/workers/context_builder/context_package.json")
    if not context_path.exists():
        return False
    state_path = _resolve_under_root(root, "cache/workers/execution_engine/state.json")
    if not state_path.exists():
        return True
    state = _read_json(state_path)
    return state.get("last_context_sha256") != _sha256_file(context_path)


def _oversized_artifact(
    root: Path,
    worker_outputs: list[dict[str, Any]],
    max_payload_bytes: int,
) -> str | None:
    for output in worker_outputs:
        for artifact in output["outputs"]["artifacts"]:
            path = _resolve_under_root(root, artifact)
            if path.exists() and path.stat().st_size > max_payload_bytes:
                return artifact
    return None


def _overall_status(worker_outputs: list[dict[str, Any]]) -> str:
    if not worker_outputs:
        return "idle"
    statuses = [output["status"] for output in worker_outputs]
    if "failed" in statuses:
        return "failed"
    if "blocked" in statuses:
        return "blocked"
    if any(status == "complete" for status in statuses):
        return "complete"
    return "idle"


def _worker_states(root: Path, worker_outputs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for output in worker_outputs:
        worker_id = output["worker_id"]
        states[worker_id] = {
            "status": output["status"],
            "last_run_at": output["generated_at"],
            "last_output": f"cache/workers/{worker_id}/last_output.json",
        }
    return states


def _run_worker_with_runtime(
    root: Path,
    worker_id: str,
    generated_at: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    started = time.monotonic()
    output = run_worker(root, worker_id, now=generated_at)
    runtime_seconds = time.monotonic() - started
    output["runtime_seconds"] = round(runtime_seconds, 6)
    max_runtime = float(config.get("max_worker_runtime_seconds", DEFAULT_CONFIG["max_worker_runtime_seconds"]))
    if runtime_seconds > max_runtime:
        output["status"] = "blocked"
        output["errors"] = [
            {
                "code": "worker_timeout",
                "message": f"{worker_id} exceeded {max_runtime} seconds",
            }
        ]
    return output


def _recovery_from_outputs(root: Path, worker_outputs: list[dict[str, Any]]) -> dict[str, Any]:
    for output in worker_outputs:
        if output["status"] == "failed":
            return {
                "last_error": output["errors"][0]["message"] if output["errors"] else "worker failed",
                "blocked_reason": None,
                "pending_review_payload": None,
                "operator_action_required": False,
            }
    for output in worker_outputs:
        if output["status"] == "blocked":
            payload = _pending_payload_from_context(root)
            code = output["errors"][0]["code"] if output["errors"] else "blocked"
            return {
                "last_error": None,
                "blocked_reason": code,
                "pending_review_payload": payload,
                "operator_action_required": True,
            }
    return _clear_recovery()


def _pending_payload_from_context(root: Path) -> str | None:
    context_path = _resolve_under_root(root, "cache/workers/context_builder/context_package.json")
    if not context_path.exists():
        return None
    context = _read_json(context_path)
    for item in context.get("blocked_inputs", []):
        payload = item.get("pending_review_payload")
        if payload:
            return payload
    return None


def _append_audit(root: Path, generated_at: str, result: dict[str, Any]) -> None:
    audit_path = _resolve_under_root(root, "logs/transactions/supervisor.jsonl")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "schema": "looping-box.audit-event.v1",
        "generated_at": generated_at,
        "event": "supervisor.run",
        "status": result["status"],
        "plan": result["plan"],
    }
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _acquire_lock(lock_path: Path, generated_at: str, stale_lock_seconds: int) -> None:
    try:
        _write_lock_exclusive(lock_path, generated_at)
        return
    except FileExistsError:
        pass

    try:
        lock = _read_json(lock_path)
        age = (_parse_time(generated_at) - _parse_time(lock["created_at"])).total_seconds()
    except (KeyError, ValueError, json.JSONDecodeError):
        age = 0
    if age <= stale_lock_seconds:
        raise RuntimeError(f"active supervisor lock: {lock_path}")

    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass
    try:
        _write_lock_exclusive(lock_path, generated_at)
    except FileExistsError as exc:
        raise RuntimeError(f"active supervisor lock: {lock_path}") from exc


def _write_lock_exclusive(lock_path: Path, generated_at: str) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("x", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"created_at": generated_at, "pid": os.getpid()}, sort_keys=True) + "\n"
        )


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or inspect the Looping Box supervisor.")
    parser.add_argument("--root", default=".", help="Project root. Defaults to the current directory.")
    parser.add_argument("--once", action="store_true", help="Run one supervisor pass.")
    parser.add_argument("--status", action="store_true", help="Print supervisor status.")
    args = parser.parse_args()

    if args.status:
        print(status_summary(args.root))
        return 0
    result = run_supervisor(args.root)
    print(f"status: {result['status']}")
    print(f"plan: {', '.join(result['plan']) if result['plan'] else 'none'}")
    if result["recovery"]["operator_action_required"]:
        print(f"pending: {result['recovery']['pending_review_payload']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
