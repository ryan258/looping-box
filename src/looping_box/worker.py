from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import model
from .phase1 import (
    DELTA_SCHEMA,
    _rel,
    _resolve_under_root,
    _sha256_file,
    _utc_now,
    _write_json,
)


WORKER_OUTPUT_SCHEMA = "looping-box.worker.output.v1"
WORKER_STATE_SCHEMA = "looping-box.worker.state.v1"
CONTEXT_PACKAGE_SCHEMA = "looping-box.worker.context-package.v1"
EXECUTION_DRAFT_SCHEMA = "looping-box.execution-draft.v1"


def run_worker(
    root: Path | str,
    worker_id: str,
    *,
    now: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run one deterministic worker pass."""
    if worker_id == "context_builder":
        return _run_context_builder(root, now=now, dry_run=dry_run)
    if worker_id == "execution_engine":
        return _run_execution_engine(root, now=now, dry_run=dry_run)
    raise ValueError(f"unknown worker: {worker_id}")


def _run_context_builder(
    root: Path | str,
    *,
    now: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    generated_at = now or _utc_now()
    delta_dir = _resolve_under_root(root_path, "cache/deltas")
    output_dir = _resolve_under_root(root_path, "cache/workers/context_builder")
    state_path = output_dir / "state.json"

    state = _read_worker_state(state_path, "context_builder")
    consumed = set(state["consumed_inputs"])
    source_deltas: list[str] = []
    items: list[dict[str, Any]] = []
    blocked_inputs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for delta_path in sorted(delta_dir.glob("*.json")):
        relative_delta = _rel(root_path, delta_path)
        if relative_delta in consumed:
            continue
        try:
            delta = _read_json(delta_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append({"code": "malformed_delta", "message": f"{relative_delta}: {exc}"})
            continue
        if delta.get("schema") != DELTA_SCHEMA:
            errors.append(
                {
                    "code": "unsupported_delta_schema",
                    "message": f"{relative_delta}: {delta.get('schema')!r}",
                }
            )
            continue

        source_deltas.append(relative_delta)
        for change in delta.get("changes", []):
            normalized = {
                "relative_path": change.get("relative_path", ""),
                "sha256": change.get("sha256", ""),
                "matched_routes": list(change.get("matched_routes", [])),
                "excerpt": change.get("excerpt", ""),
                "source_delta": relative_delta,
            }
            review_reasons = list(change.get("review_reasons", []))
            if review_reasons:
                blocked = dict(normalized)
                blocked["review_reasons"] = review_reasons
                blocked["pending_review_payload"] = delta.get("boundary_gate", {}).get("payload")
                blocked_inputs.append(blocked)
            else:
                items.append(normalized)

    if errors:
        output = _worker_output(
            "context_builder",
            generated_at,
            "failed",
            source_deltas,
            [],
            errors,
        )
        _persist_worker_output(root_path, output_dir, output, dry_run)
        return output

    if not source_deltas:
        output = _worker_output("context_builder", generated_at, "idle", [], [], [])
        _persist_worker_output(root_path, output_dir, output, dry_run)
        return output

    status = "blocked" if blocked_inputs else "complete"
    context_path = output_dir / "context_package.json"
    markdown_path = output_dir / "context_package.md"
    context = {
        "schema": CONTEXT_PACKAGE_SCHEMA,
        "worker_id": "context_builder",
        "generated_at": generated_at,
        "status": status,
        "source_deltas": source_deltas,
        "items": items,
        "blocked_inputs": blocked_inputs,
    }
    # Dry run must have no side effects: a model call costs money and ships file
    # excerpts to OpenRouter, so it is gated behind `not dry_run`.
    if items and not dry_run:
        prompt = "Summarize these routed inputs into a short context briefing:\n\n" + "\n".join(
            f"- {item['relative_path']}: {item['excerpt']}" for item in items
        )
        completion = _try_model("context_builder", prompt, root_path)
        if completion is not None:
            context["synthesis"] = {
                "text": completion["text"],
                "model": completion["model"],
                "response_sha256": completion["response_sha256"],
            }
    artifacts = [_rel(root_path, context_path), _rel(root_path, markdown_path)]
    output = _worker_output(
        "context_builder",
        generated_at,
        status,
        source_deltas,
        artifacts,
        [],
    )

    if not dry_run:
        _write_json(context_path, context)
        markdown_path.write_text(_context_markdown(context), encoding="utf-8")
        state["consumed_inputs"].extend(source_deltas)
        state["last_run_at"] = generated_at
        _write_json(state_path, state)
    _persist_worker_output(root_path, output_dir, output, dry_run)
    return output


def _run_execution_engine(
    root: Path | str,
    *,
    now: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    generated_at = now or _utc_now()
    context_path = _resolve_under_root(root_path, "cache/workers/context_builder/context_package.json")
    output_dir = _resolve_under_root(root_path, "cache/workers/execution_engine")
    state_path = output_dir / "state.json"
    state = _read_worker_state(state_path, "execution_engine")

    if not context_path.exists():
        output = _worker_output(
            "execution_engine",
            generated_at,
            "blocked",
            [],
            [],
            [{"code": "missing_context", "message": "context_builder output is missing"}],
        )
        _persist_worker_output(root_path, output_dir, output, dry_run)
        return output

    context_hash = _sha256_file(context_path)
    # Load .env before reading the role's model, or a freshly configured model
    # would be missed and the run would wrongly report idle.
    model.load_env(root_path)
    model_id = model.model_for("execution_engine")
    # Idempotency is keyed on (context hash + model id): swapping the model in
    # .env re-runs even when the context is unchanged.
    if (
        state.get("last_context_sha256") == context_hash
        and state.get("last_model") == model_id
    ):
        output = _worker_output("execution_engine", generated_at, "idle", [], [], [])
        _persist_worker_output(root_path, output_dir, output, dry_run)
        return output

    context = _read_json(context_path)
    source_deltas = list(context.get("source_deltas", []))
    if context.get("status") == "blocked":
        output = _worker_output(
            "execution_engine",
            generated_at,
            "blocked",
            source_deltas,
            [],
            [{"code": "blocked_context", "message": "context package has blocked inputs"}],
        )
        _persist_worker_output(root_path, output_dir, output, dry_run)
        return output

    draft_path = output_dir / "draft.json"
    draft_markdown_path = output_dir / "draft.md"
    draft = {
        "schema": EXECUTION_DRAFT_SCHEMA,
        "generated_at": generated_at,
        "source_context": _rel(root_path, context_path),
        "source_context_sha256": context_hash,
        "items": [_draft_item(root_path, item, dry_run) for item in context.get("items", [])],
    }
    artifacts = [_rel(root_path, draft_path), _rel(root_path, draft_markdown_path)]
    output = _worker_output(
        "execution_engine",
        generated_at,
        "complete",
        source_deltas,
        artifacts,
        [],
    )

    if not dry_run:
        _write_json(draft_path, draft)
        draft_markdown_path.write_text(_draft_markdown(draft), encoding="utf-8")
        state["last_context_sha256"] = context_hash
        state["last_model"] = model_id
        state["last_run_at"] = generated_at
        _write_json(state_path, state)
    _persist_worker_output(root_path, output_dir, output, dry_run)
    return output


def _draft_item(root: Path, item: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """Draft one context item. Uses the execution_engine model when configured,
    otherwise falls back to the item excerpt (deterministic, offline). A model
    call is skipped entirely during a dry run (no network, no spend)."""
    drafted = {
        "relative_path": item.get("relative_path", ""),
        "matched_routes": list(item.get("matched_routes", [])),
        "draft": item.get("excerpt", ""),
    }
    if dry_run:
        return drafted
    prompt = (
        "Draft a short, local working note for this routed input. "
        f"Routes: {', '.join(item.get('matched_routes', [])) or 'none'}.\n\n"
        f"{item.get('excerpt', '')}"
    )
    completion = _try_model("execution_engine", prompt, root)
    if completion is not None:
        drafted["draft"] = completion["text"]
        drafted["model"] = completion["model"]
        drafted["response_sha256"] = completion["response_sha256"]
    return drafted


def _try_model(role: str, prompt: str, root: Path) -> dict[str, Any] | None:
    """Call the role's model if configured, degrading to deterministic output
    (return None) on any model/network/provider error. Used only for the worker
    enhancement paths where the deterministic fallback is acceptable; the review
    verifier fails closed instead."""
    try:
        return model.generate_if_enabled(role, prompt, root=root)
    except Exception:
        # ponytail: a transient OpenRouter failure must not crash a worker pass;
        # fall back to the deterministic excerpt rather than fail the pipeline.
        return None


def _worker_output(
    worker_id: str,
    generated_at: str,
    status: str,
    source_deltas: list[str],
    artifacts: list[str],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "schema": WORKER_OUTPUT_SCHEMA,
        "worker_id": worker_id,
        "run_id": "worker-" + worker_id.replace("_", "-") + "-" + _run_suffix(generated_at),
        "generated_at": generated_at,
        "status": status,
        "inputs": {"source_deltas": source_deltas},
        "outputs": {"artifacts": artifacts},
        "errors": errors,
    }


def _persist_worker_output(
    root: Path,
    output_dir: Path,
    output: dict[str, Any],
    dry_run: bool,
) -> None:
    if dry_run:
        return
    _write_json(output_dir / "last_output.json", output)


def _read_worker_state(path: Path, worker_id: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": WORKER_STATE_SCHEMA,
            "worker_id": worker_id,
            "consumed_inputs": [],
        }
    state = _read_json(path)
    state.setdefault("schema", WORKER_STATE_SCHEMA)
    state.setdefault("worker_id", worker_id)
    state.setdefault("consumed_inputs", [])
    return state


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _context_markdown(context: dict[str, Any]) -> str:
    lines = [
        "# Context Package",
        "",
        f"Status: {context['status']}",
        f"Source deltas: {len(context['source_deltas'])}",
        f"Items: {len(context['items'])}",
        f"Blocked inputs: {len(context['blocked_inputs'])}",
        "",
    ]
    for item in context["items"]:
        lines.append(f"- {item['relative_path']} ({', '.join(item['matched_routes']) or 'unrouted'})")
    for item in context["blocked_inputs"]:
        lines.append(f"- BLOCKED {item['relative_path']} ({', '.join(item['review_reasons'])})")
    return "\n".join(lines) + "\n"


def _draft_markdown(draft: dict[str, Any]) -> str:
    lines = ["# Execution Draft", "", f"Source context: {draft['source_context']}", ""]
    for item in draft["items"]:
        lines.append(f"## {item['relative_path']}")
        lines.append("")
        lines.append(item["draft"])
        lines.append("")
    return "\n".join(lines)


def _run_suffix(generated_at: str) -> str:
    return "".join(character for character in generated_at if character.isalnum())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Looping Box worker pass.")
    parser.add_argument("worker_id", choices=["context_builder", "execution_engine"])
    parser.add_argument("--root", default=".", help="Project root. Defaults to the current directory.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned work without writing files.")
    args = parser.parse_args()

    output = run_worker(args.root, args.worker_id, dry_run=args.dry_run)
    print(f"worker: {output['worker_id']}")
    print(f"status: {output['status']}")
    for artifact in output["outputs"]["artifacts"]:
        print(f"artifact: {artifact}")
    for error in output["errors"]:
        print(f"error: {error['code']}: {error['message']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
