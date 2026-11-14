from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from ._util import (
    append_audit as _append_audit_event,
    default_root as _default_root,
    inspect_audit_log,
    preflight_audit as _preflight_audit,
    project_lock as _project_lock,
    rel as _rel,
    resolve_under_root as _resolve_under_root,
    read_json as _read_json,
    utc_now as _utc_now,
    write_json as _write_json,
    write_bytes as _write_bytes,
)
from .validation import validate_data
from .paths import UnsafePathError, contained_path, directory_path, file_path, read_validated, tree_entries
from .phase1 import _validated_sop
from .review import list_reviews
from .worker import (
    _delta_keeps_blocked_inputs,
    _validate_existing_worker_artifacts,
    _validated_delta,
    execution_needs_run,
    run_worker,
)
from .eligibility import SourceReingestRequired


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
    # Preserve side-effect-free rejection of malformed configuration; read it
    # again under the lock so planning never relies on an unlocked snapshot.
    _read_config(root_path)
    _validated_sop(root_path, _resolve_under_root(root_path, "config/sops/phase1_ingestion.json"))
    with _project_lock(root_path, generated_at, DEFAULT_CONFIG["stale_lock_seconds"]):
        config = _read_config(root_path)
        _validated_sop(root_path, _resolve_under_root(root_path, "config/sops/phase1_ingestion.json"))
        # Validate the whole worker set while holding the same lock as writers.
        for worker_id in ("context_builder", "execution_engine"):
            _validate_existing_worker_artifacts(root_path, worker_id)
        return _run_supervisor_locked(root_path, generated_at, config)


def load_world_state(root: Path | str) -> dict[str, Any]:
    root_path = Path(root).resolve()
    state_path = _resolve_under_root(root_path, ".world_state.json")
    if not state_path.exists():
        block = file_path(_resolve_under_root(root_path, "cache/supervisor/blocked.json"))
        audit_path = _resolve_under_root(root_path, "logs/transactions/supervisor.jsonl")
        audit = inspect_audit_log(audit_path)
        if audit.broken_at is not None:
            raise ValueError(f"audit_invalid: {audit_path}: line {audit.broken_at}: broken audit chain")
        archive = directory_path(_resolve_under_root(root_path, "cache/deltas/archive"))
        try:
            archived_deltas = any(path.name.endswith(".json") for path in archive.iterdir())
        except FileNotFoundError:
            archived_deltas = False
        if block.exists() or audit.events:
            raise ValueError(f"state_inconsistent: {state_path}: missing with prior supervisor evidence")
        # Legacy standalone workers predate initialization of world state. Allow
        # their coherent artifacts only when there is no prior supervisor evidence.
        for worker_id in ("context_builder", "execution_engine"):
            _validate_existing_worker_artifacts(root_path, worker_id)
            worker_dir = _resolve_under_root(root_path, f"cache/workers/{worker_id}")
            marker = file_path(worker_dir / "state.json")
            output_path = file_path(worker_dir / "last_output.json")
            if archived_deltas and (marker.exists() or output_path.exists()):
                raise ValueError(f"state_inconsistent: {state_path}: missing with prior supervisor evidence")
            if marker.exists() and not output_path.exists():
                raise ValueError(f"state_inconsistent: {state_path}: missing with incomplete {worker_id} evidence")
            if output_path.exists():
                output = read_validated(output_path, "worker_output", worker_id=worker_id)
                if output["outputs"]["artifacts"] and not marker.exists():
                    raise ValueError(f"state_inconsistent: {state_path}: missing with incomplete {worker_id} evidence")
        return _default_world_state()
    state = read_validated(state_path, "world_state")
    for worker_id in ("context_builder", "execution_engine"):
        _validate_existing_worker_artifacts(root_path, worker_id)
    for worker_id, summary in state["worker_states"].items():
        output = file_path(_resolve_under_root(root_path, summary["last_output"]))
        if not output.exists():
            raise ValueError(f"state_inconsistent: {state_path}: required worker output {output} is missing")
        read_validated(output, "worker_output", worker_id=worker_id)
    return state


def _initialize_standalone_world(root: Path) -> None:
    """Initialize the never-supervised baseline under the existing worker lock."""
    path = file_path(_resolve_under_root(root, ".world_state.json"))
    if not path.exists():
        _write_json(path, load_world_state(root))


_RESOURCE_LIMIT_REASONS = {"file_count_limit", "payload_size_limit", "worker_timeout"}


def _next_action(reason: str) -> str:
    if "source_reingest_required" in reason:
        return "ingest the changed source with looping-box phase1, then retry the supervisor"
    if "model" in reason:
        return "fix model configuration/provider availability, then run looping-box-supervisor --once"
    if "routing_disabled" in reason:
        return "enable the required workers/dependency in config/super_loop.json, then run looping-box-supervisor --once"
    if any(code in reason for code in _RESOURCE_LIMIT_REASONS):
        return "inspect retained work and raise the matching limit in config/super_loop.json, then run looping-box-supervisor --once"
    if "schema" in reason:
        return "preserve evidence; use a compatible version or deliberately migrate the named artifact"
    return "preserve and inspect/restore the named evidence, then run looping-box-supervisor --once"


def status_summary(root: Path | str) -> str:
    root_path = Path(root).resolve()
    state = load_world_state(root_path)
    recovery = state["recovery"]
    pending = list_reviews(root_path)
    details = [f"last run: {state.get('last_run_at') or 'never (no run evidence)'}",
               f"pending reviews: {len(pending)} (looping-box-review list)"]
    unverifiable = sum(bool(review.get("unverifiable_decision")) for review in pending)
    if unverifiable:
        details.append(f"warning: {unverifiable} decision record(s) did not verify (run: looping-box-review key)")
    if recovery.get("last_error"):
        return "\n".join(["status: failed", f"error: {recovery['last_error']}", *details,
                          "Next: " + _next_action(recovery["last_error"])])
    if recovery.get("operator_action_required"):
        payload = recovery.get("pending_review_payload") or "cache/supervisor/blocked.json"
        reason = recovery.get("blocked_reason") or "review"
        if reason in _RESOURCE_LIMIT_REASONS or reason == "routing_disabled":
            next_step = _next_action(reason)
        else:
            next_step = "inspect/decide pending reviews (looping-box-review approve|reject), or deliberately edit/remove source then ingest"
        return "\n".join(["status: operator action required", f"pending: {payload}", *details, f"Next: {next_step}"])
    if pending:
        return "\n".join(["status: operator action required", *details,
                          "Next: inspect/decide each pending review, or deliberately edit/remove source then ingest"])
    return "\n".join(["status: clear", *details, "Next: ./startday.sh"])


def _run_supervisor_locked(
    root: Path,
    generated_at: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    _preflight_audit(root, "supervisor")
    state = load_world_state(root)
    list_reviews(root)  # Validate live evidence before resource checks or worker side effects.
    state_path = file_path(_resolve_under_root(root, ".world_state.json"))
    file_path(state_path.with_name(f"{state_path.name}.{os.getpid()}.tmp"))
    state["registered_loops"] = ["phase1"] + list(config["workers"])
    state["dependencies"] = list(config["dependencies"])
    delta_paths = _phase1_delta_paths(root)
    consumed = _consumed_delta_refs(root)
    new_deltas = []
    for path in delta_paths:
        if _rel(root, path) in consumed:
            try:
                _validated_delta(_read_json(path), path)
            except UnsafePathError:
                raise
            except (OSError, ValueError, RecursionError):
                pass  # Previously consumed but now invalid evidence still needs intake.
            else:
                continue
        new_deltas.append(path)
    new_delta_refs = [_rel(root, path) for path in new_deltas]
    changed_file_count = _changed_file_count(new_deltas)
    max_files = config["max_files_per_cycle"]
    if state.get("last_run_at") is None and not state["runs"] and not state["recovery"]["last_error"]:
        # A standalone worker can initialize the world without supervising a run.
        # Record the first supervisor attempt before workers run or inputs are archived.
        initial = {**state, "dirty": True, "recovery": {
            **_clear_recovery(),
            "last_error": "state_incomplete: the first supervisor pass has no recorded outcome. "
                          "If the pass has stopped, resolve any storage error and run it again.",
        }}
        _write_json(state_path, initial)
    if changed_file_count > max_files:
        return _blocked(root, state, generated_at, "file_count_limit",
                        f"{changed_file_count} changed files exceeds limit {max_files}", new_delta_refs)

    plan: list[str] = []
    outputs: list[dict[str, Any]] = []
    snapshots: dict[str, tuple[dict[str, bytes], set[str]] | None] = {}
    if new_deltas:
        if not _can_route(config, "phase1_delta", "context_builder"):
            return _blocked(root, state, generated_at, "routing_disabled",
                            "phase1_delta -> context_builder is disabled; intake remains queued", new_delta_refs)
        plan.append("context_builder")
        snapshots["context_builder"] = _snapshot_worker_dir(root, "context_builder")
        output = _run_worker_with_runtime(root, "context_builder", generated_at, config)
        outputs.append(output)
        if _is_worker_timeout(output):
            return _blocked_with_rollback(root, state, generated_at, "worker_timeout",
                                          "context_builder exceeded its runtime limit", new_delta_refs, snapshots,
                                          plan=plan, worker_outputs=outputs)

    context_failed = any(output["status"] == "failed" for output in outputs)
    needs_execution = False
    if not context_failed:
        try:
            needs_execution = _execution_needs_context(root)
        except SourceReingestRequired:
            if not _can_route(config, "context_builder", "execution_engine"):
                raise  # Invalid evidence takes precedence over disabled routing.
            # Let the worker persist the structured failure, using the same predicate.
            needs_execution = True
        needs_execution = needs_execution or any(output["status"] in {"complete", "blocked"} for output in outputs)
        if needs_execution and _can_route(config, "context_builder", "execution_engine"):
            plan.append("execution_engine")
            snapshots["execution_engine"] = _snapshot_worker_dir(root, "execution_engine")
            output = _run_worker_with_runtime(root, "execution_engine", generated_at, config)
            outputs.append(output)
            if _is_worker_timeout(output):
                return _blocked_with_rollback(root, state, generated_at, "worker_timeout",
                                              "execution_engine exceeded its runtime limit", new_delta_refs, snapshots,
                                              plan=plan, worker_outputs=outputs)

    limit = config["max_payload_bytes"]
    oversized = _oversized_artifact(root, outputs, limit)
    if oversized is not None:
        return _blocked_with_rollback(root, state, generated_at, "payload_size_limit",
                                      f"{oversized} exceeds limit {limit} bytes", new_delta_refs, snapshots,
                                      plan=plan, worker_outputs=outputs)
    if needs_execution and not _can_route(config, "context_builder", "execution_engine"):
        return _blocked(root, state, generated_at, "routing_disabled",
                        "context_builder -> execution_engine is disabled; context remains queued", new_delta_refs,
                        plan=plan, worker_outputs=outputs)

    status = _overall_status(outputs)
    recovery = _recovery_from_outputs(root, outputs)
    if recovery["operator_action_required"] and status != "failed":
        status = "blocked"
    if status != "failed":
        acknowledgements = _acknowledged_delta_refs(root, delta_paths)
        remaining = _archive_observed_deltas(root, acknowledgements)
        _prune_worker_consumed_inputs(root, set(acknowledgements) - set(remaining))
        state["observed_deltas"] = remaining
        if remaining:
            status = "failed"
            recovery = {**_clear_recovery(), "last_error": "delta_archive_error: could not archive " + ", ".join(remaining)}
    return _finish_supervisor(root, state, generated_at, status, plan, outputs, recovery, new_delta_refs)


def _consumed_delta_refs(root: Path) -> set[str]:
    path = _resolve_under_root(root, "cache/workers/context_builder/state.json")
    return set(read_validated(path, "worker_state", worker_id="context_builder")["consumed_inputs"]) if path.exists() else set()


def _acknowledged_delta_refs(root: Path, paths: list[Path]) -> list[str]:
    context_path = _resolve_under_root(root, "cache/workers/context_builder/context_package.json")
    if not context_path.exists():
        return []
    context = read_validated(context_path, "context_package")
    retained = set(context["source_deltas"])
    blocked_by_path = {item["relative_path"]: item for item in context["blocked_inputs"]}
    consumed = _consumed_delta_refs(root)
    acknowledged = []
    for path in paths:
        reference = _rel(root, path)
        if reference not in consumed or not path.exists():
            continue
        try:
            delta = _validated_delta(_read_json(path), path)
        except UnsafePathError:
            raise
        except (OSError, ValueError, RecursionError):
            continue
        # Skips and repeated holds can be consumed without replacing the context.
        if reference in retained or _delta_keeps_blocked_inputs(delta, blocked_by_path):
            acknowledged.append(reference)
    return acknowledged


def _finish_supervisor(root: Path, state: dict[str, Any], generated_at: str, status: str,
                       plan: list[str], outputs: list[dict[str, Any]], recovery: dict[str, Any],
                       source_deltas: list[str], *, update_workers: bool = True) -> dict[str, Any]:
    state["dirty"] = bool(plan) or status in {"blocked", "failed"}
    state["last_run_at"] = generated_at
    if update_workers:
        state["worker_states"].update(_worker_states(root, outputs))
    state["recovery"] = recovery
    state["runs"].append({"generated_at": generated_at, "status": status, "plan": list(plan), "source_deltas": source_deltas})
    state["runs"] = state["runs"][-50:]
    result = {"schema": "looping-box.supervisor.run.v1", "generated_at": generated_at, "status": status,
              "plan": list(plan), "worker_outputs": outputs, "recovery": recovery}
    state_path = _resolve_under_root(root, ".world_state.json")
    try:
        _write_json(state_path, state)
    except OSError as exc:
        raise OSError(
            f"state_incomplete: supervisor left local results in place. "
            f"Resolve the state write error and retry: {exc}"
        ) from exc
    try:
        _append_audit(root, generated_at, result, source_deltas)
    except (OSError, ValueError) as exc:
        message = f"audit_incomplete: supervisor left local results in place; inspect the supervisor audit log: {exc}"
        state["dirty"] = True
        state["recovery"] = {**recovery, "last_error": message}
        state["runs"][-1]["status"] = "failed"
        try:
            _write_json(state_path, state)
        except (OSError, ValueError) as state_error:
            raise ValueError(f"{message}; could not save supervisor recovery: {state_error}") from state_error
        raise ValueError(message) from exc
    return result


def _worker_dir(root: Path, worker_id: str) -> Path:
    return _resolve_under_root(root, f"cache/workers/{worker_id}")


def _snapshot_worker_dir(
    root: Path, worker_id: str
) -> tuple[dict[str, bytes], set[str]] | None:
    """Capture a worker dir: file bytes for everything except history/, and only the
    *names* of history files (history only ever grows, so it is never rewound and
    never held in memory)."""
    path = _worker_dir(root, worker_id)
    if not path.exists():
        return None
    files: dict[str, bytes] = {}
    history: set[str] = set()
    for child in tree_entries(path):
        if child.is_file():
            relative = child.relative_to(path).as_posix()
            if relative.startswith("history/"):
                history.add(relative)
            else:
                files[relative] = child.read_bytes()
    return files, history


def _restore_worker_dir(
    root: Path, worker_id: str, snapshot: tuple[dict[str, bytes], set[str]] | None
) -> None:
    path = directory_path(_worker_dir(root, worker_id))
    entries = tree_entries(path)  # fail before deleting or restoring any entry
    if snapshot is not None:
        for reference in snapshot[0]:
            file_path(contained_path(path, reference))
        for reference in snapshot[1]:
            file_path(contained_path(path, reference))
    if snapshot is None:
        if path.exists():
            shutil.rmtree(path)
        return
    files, history = snapshot
    # Restore complete artifacts before their state markers, without first
    # deleting the current version. A failed replace preserves that file.
    ordered = sorted(files, key=lambda name: (name == "state.json", name == "last_output.json", name))
    for relative_path in ordered:
        target = file_path(contained_path(path, relative_path))
        _write_bytes(target, files[relative_path])
    if path.exists():
        for child in sorted(entries, reverse=True):
            if child.is_dir():
                try:
                    child.rmdir()
                except OSError:
                    pass  # still holds preserved files/history
            elif child.relative_to(path).as_posix() not in history | files.keys():
                child.unlink()


def _is_worker_timeout(output: dict[str, Any]) -> bool:
    return any(
        error.get("code") == "worker_timeout" for error in output["errors"]
    )


def _blocked_with_rollback(
    root: Path,
    state: dict[str, Any],
    generated_at: str,
    reason: str,
    message: str,
    source_deltas: list[str],
    ran_workers: dict[str, tuple[dict[str, bytes], set[str]] | None],
    *, plan: list[str] | None = None, worker_outputs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Undo state and artifacts each ran worker already persisted so the audit
    # trail matches the rolled-back world state.
    for worker_id, snapshot in ran_workers.items():
        _restore_worker_dir(root, worker_id, snapshot)
    attempted = []
    for output in worker_outputs or []:
        attempted.append({**output, "status": "failed" if output["status"] == "failed" else "blocked",
                          "outputs": {"artifacts": []},
                          "errors": [*output["errors"], {"code": reason, "message": "worker files restored: " + message}]})
    return _blocked(root, state, generated_at, reason, message, source_deltas,
                    plan=plan, worker_outputs=attempted, update_workers=False)


def _blocked(
    root: Path, state: dict[str, Any], generated_at: str, reason: str, message: str,
    source_deltas: list[str], *, plan: list[str] | None = None,
    worker_outputs: list[dict[str, Any]] | None = None, update_workers: bool = True,
) -> dict[str, Any]:
    payload_path = _resolve_under_root(root, "cache/supervisor/blocked.json")
    payload = {"schema": SUPERVISOR_BLOCKED_SCHEMA, "generated_at": generated_at,
               "reason": reason, "message": message, "source_deltas": source_deltas}
    _write_json(payload_path, payload)
    outputs = worker_outputs or []
    failure = next((output for output in outputs if output["status"] == "failed"), None)
    recovery = {"last_error": (failure["errors"][0]["message"] if failure["errors"] else "worker failed") if failure else None,
                "blocked_reason": reason, "pending_review_payload": _rel(root, payload_path),
                "operator_action_required": True}
    return _finish_supervisor(root, state, generated_at, "failed" if failure else "blocked", plan or [],
                              outputs, recovery, source_deltas, update_workers=update_workers)


def _default_world_state() -> dict[str, Any]:
    # ponytail: registered_loops, dirty, and leases are reserved for daemon and
    # multi-loop coordination; current one-shot supervisor behavior does not
    # read them.
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
        config.update(read_validated(config_path, "super_loop"))
    return validate_data(config, "super_loop", str(config_path))


def _phase1_delta_paths(root: Path) -> list[Path]:
    delta_dir = directory_path(_resolve_under_root(root, "cache/deltas"))
    try:
        entries = list(delta_dir.iterdir())
    except FileNotFoundError:
        return []
    return [file_path(path) for path in sorted(entries) if path.name.endswith(".json")]


def _archive_observed_deltas(root: Path, observed_refs: list[str]) -> list[str]:
    archive_dir = directory_path(_resolve_under_root(root, "cache/deltas/archive"))
    active_refs: list[str] = []
    for delta_ref in observed_refs:
        delta_path = _resolve_under_root(root, delta_ref)
        if not delta_path.exists():
            active_refs.append(delta_ref)  # Disappearance is not an archive acknowledgement.
            continue
        try:
            archive_path = _unique_archive_path(archive_dir / delta_path.name)
            _validated_delta(_read_json(delta_path), delta_path)
            archive_dir.mkdir(parents=True, exist_ok=True)
            delta_path.replace(archive_path)
        except UnsafePathError:
            raise
        except (OSError, ValueError, RecursionError):
            active_refs.append(delta_ref)
    return active_refs


def _unique_archive_path(path: Path) -> Path:
    candidate = path
    suffix = 2
    # Keep the .json extension on collision renames so archive globs and the
    # delta's embedded delta_path stay meaningful.
    while file_path(candidate).exists():
        candidate = path.with_name(f"{path.stem}-{suffix}{path.suffix}")
        suffix += 1
    return candidate


def _prune_worker_consumed_inputs(root: Path, archived_delta_refs: set[str]) -> None:
    state_path = _resolve_under_root(root, "cache/workers/context_builder/state.json")
    if not state_path.exists() or not archived_delta_refs:
        return
    state = read_validated(state_path, "worker_state", worker_id="context_builder")
    state["consumed_inputs"] = [
        delta_ref
        for delta_ref in state.get("consumed_inputs", [])
        if delta_ref not in archived_delta_refs
    ]
    _write_json(state_path, state)


def _can_route(config: dict[str, Any], source: str, target: str) -> bool:
    workers = set(config.get("workers", DEFAULT_CONFIG["workers"]))
    dependencies = {
        (edge.get("from"), edge.get("to"))
        for edge in config.get("dependencies", DEFAULT_CONFIG["dependencies"])
    }
    return target in workers and (source, target) in dependencies


def _changed_file_count(delta_paths: list[Path]) -> int:
    """Changed files the workers would actually process. Items held for review are
    excluded: they re-surface every run and are not worked, so a pile of held files
    must not wedge the loop behind a limit that deciding them cannot clear."""
    count = 0
    for path in delta_paths:
        try:
            delta = _validated_delta(_read_json(path), path)
        except UnsafePathError:
            raise
        except (OSError, ValueError, RecursionError):
            continue  # Intake owns quarantine/error classification; invalid data is never counted.
        count += sum(1 for change in delta["changes"] if not change["review_reasons"])
    return count


def _execution_needs_context(root: Path) -> bool:
    return execution_needs_run(root)


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
    max_runtime = float(config.get("max_worker_runtime_seconds", DEFAULT_CONFIG["max_worker_runtime_seconds"]))
    model_timeout = max(max_runtime, 0.001)
    output = run_worker(
        root,
        worker_id,
        now=generated_at,
        model_timeout_seconds=model_timeout,
        _supervised=True,
    )
    runtime_seconds = time.monotonic() - started
    output["runtime_seconds"] = round(runtime_seconds, 6)
    if runtime_seconds > max_runtime:
        if output["status"] != "failed":
            output["status"] = "blocked"
        output["errors"] = [*output["errors"],
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
    if list_reviews(root):
        return {"last_error": None, "blocked_reason": "pending_review",
                "pending_review_payload": "staging/pending_review.json", "operator_action_required": True}
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
    context = read_validated(context_path, "context_package")
    for item in context.get("blocked_inputs", []):
        payload = item.get("pending_review_payload")
        if payload:
            return payload
    return None


def _append_audit(root: Path, generated_at: str, result: dict[str, Any], source_deltas: list[str]) -> None:
    _append_audit_event(
        root,
        "supervisor",
        {
            "event": "supervisor.run",
            "generated_at": generated_at,
            "status": result["status"],
            "plan": result["plan"],
            "source_deltas": source_deltas,
            "worker_statuses": {o["worker_id"]: o["status"] for o in result["worker_outputs"]},
            "blocked_reason": result["recovery"].get("blocked_reason"),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or inspect the Looping Box supervisor.")
    parser.add_argument(
        "--root",
        default=_default_root(),
        help="Project root. Defaults to $LOOPING_BOX_ROOT, else the current directory.",
    )
    parser.add_argument("--once", action="store_true", help="Run one supervisor pass.")
    parser.add_argument("--status", action="store_true", help="Print supervisor status.")
    args = parser.parse_args()

    if not args.status and not args.once:
        parser.error("pass --once to run a supervisor pass, or --status to inspect state")
    try:
        args.root = Path(args.root).expanduser().resolve()
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: invalid_root: {args.root}: {exc}", file=sys.stderr)
        return 1
    try:
        if args.status:
            print(status_summary(args.root))
            return 0
        result = run_supervisor(args.root)
    except RuntimeError as exc:  # another phase1/supervisor/review holds the lock
        print(f"busy: {exc}\nNext: wait for the current lock owner, then retry", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:  # corrupt world state/config JSON, unknown schema, ...
        print(f"error: evidence unavailable: {exc}\nNext: {_next_action(str(exc))}", file=sys.stderr)
        return 1
    print(f"status: {result['status']}")
    print(f"plan: {', '.join(result['plan']) if result['plan'] else 'none'}")
    if result["recovery"]["operator_action_required"]:
        print(f"pending: {result['recovery']['pending_review_payload']}")
    # Exit codes: 0 ok, 1 failed (retry after fixing) or busy, 2 blocked (operator action required).
    return {"failed": 1, "blocked": 2}.get(result["status"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
