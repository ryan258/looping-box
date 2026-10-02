from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from ._util import (
    PENDING_REVIEW_INDEX_SCHEMA,
    append_audit as _append_audit,
    decision_state as _decision_state,
    key_status as _key_status,
    default_root as _default_root,
    project_lock as _project_lock,
    read_json as _read_json,
    rel as _rel,
    resolve_under_root as _resolve_under_root,
    sha256_file as _sha256_file,
    utc_now as _utc_now,
    write_json as _write_json,
)
from .action_policy import classify_reasons, match_keywords as _match_keywords


STATE_SCHEMA = "looping-box.phase1.state.v1"
DELTA_SCHEMA = "looping-box.phase1.delta.v1"
REVIEW_PAYLOAD_SCHEMA = "looping-box.review-payload.v1"
DEFAULT_MAX_FILE_BYTES = 1_000_000


def run_phase1(
    root: Path | str,
    *,
    now: str | None = None,
    input_dir: Path | str = "inbox",
    sop_path: Path | str = "config/sops/phase1_ingestion.json",
    state_path: Path | str = "cache/state/phase1_state.json",
    delta_dir: Path | str = "cache/deltas",
    staging_dir: Path | str = "staging",
) -> dict[str, Any]:
    """Run one deterministic local ingestion pass (serialized by the project lock)."""
    root_path = Path(root).resolve()
    generated_at = now or _utc_now()
    with _project_lock(root_path, generated_at):
        return _run_phase1_locked(
            root_path,
            generated_at,
            input_dir=input_dir,
            sop_path=sop_path,
            state_path=state_path,
            delta_dir=delta_dir,
            staging_dir=staging_dir,
        )


def _run_phase1_locked(
    root_path: Path,
    generated_at: str,
    *,
    input_dir: Path | str,
    sop_path: Path | str,
    state_path: Path | str,
    delta_dir: Path | str,
    staging_dir: Path | str,
) -> dict[str, Any]:
    resolved_input_dir = _resolve_under_root(root_path, input_dir)
    resolved_sop_path = _resolve_under_root(root_path, sop_path)
    resolved_state_path = _resolve_under_root(root_path, state_path)
    resolved_delta_dir = _resolve_under_root(root_path, delta_dir)
    resolved_staging_dir = _resolve_under_root(root_path, staging_dir)

    _ensure_layout(
        resolved_input_dir,
        resolved_sop_path.parent,
        resolved_state_path.parent,
        resolved_delta_dir,
        resolved_staging_dir,
    )

    sop = _read_json(resolved_sop_path)
    state = _read_state(resolved_state_path)
    allowed_extensions = {
        extension.lower() for extension in sop.get("allowed_extensions", [".md", ".txt", ".json"])
    }

    max_file_bytes = int(sop.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES))

    changes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    released_ids: list[str] = []
    warnings: list[dict[str, str]] = []
    all_review_reasons: list[str] = []

    for file_path in _iter_input_files(resolved_input_dir, root_path, allowed_extensions):
        relative_path = _rel(root_path, file_path)
        content_hash = _sha256_file(file_path)
        file_stat = file_path.stat()

        if file_stat.st_size > max_file_bytes:
            # Fail closed: never read an oversized input into memory or a model
            # prompt; a human decides whether it is processed at all.
            text, matched_routes, review_reasons = "", [], ["oversized_input"]
        else:
            text = _read_text(file_path)
            matched_routes = _match_routes(text, sop.get("routes", []))
            review_reasons = _match_keywords(
                _gate_text(file_path, text),
                sop.get("boundary_gate", {}).get("requires_review_keywords", []),
            )

        change = {
            "relative_path": relative_path,
            "sha256": content_hash,
            "size_bytes": file_stat.st_size,
            "matched_routes": matched_routes,
            "review_reasons": review_reasons,
            "excerpt": _excerpt(text, int(sop.get("max_excerpt_chars", 500))),
        }

        if review_reasons:
            review_id = _review_id([change])
            decision, unverifiable = _decision_state(resolved_staging_dir, review_id)
            if decision == "approved" and not _processed_at_path(state, relative_path, content_hash):
                # An approval releases the item into the pipeline exactly once,
                # as ordinary work: without this, approving would only silence
                # the gate and the approved content would never be processed.
                released = dict(change, review_reasons=[], approved_review=review_id)
                changes.append(released)
                released_ids.append(review_id)
                _record_processed(state, relative_path, content_hash, file_stat.st_size, generated_at)
                continue
            if decision is not None:
                skipped.append(
                    {
                        "relative_path": relative_path,
                        "sha256": content_hash,
                        "reason": "review_decision_recorded",
                    }
                )
                _record_processed(state, relative_path, content_hash, file_stat.st_size, generated_at)
                continue
            if unverifiable:
                warnings.append(
                    {
                        "code": "decision_unverifiable",
                        "review_id": review_id,
                        "relative_path": relative_path,
                        "message": "a decision record exists but did not verify "
                        f"(edited, forged, or signed with a different key); {_key_status()}",
                    }
                )
            changes.append(change)
            review_items.append(change)
            for reason in review_reasons:
                if reason not in all_review_reasons:
                    all_review_reasons.append(reason)
            # Pending-review files are NOT recorded as processed: a file that
            # trips the boundary gate keeps re-surfacing (and regenerates the
            # staging payload) every run until a human handles/removes it.
            continue

        if _has_processed(state, relative_path, content_hash):
            skipped.append(
                {
                    "relative_path": relative_path,
                    "sha256": content_hash,
                    "reason": "already_processed",
                }
            )
            continue

        changes.append(change)
        _record_processed(state, relative_path, content_hash, file_stat.st_size, generated_at)

    boundary_gate = _build_boundary_gate(
        root_path,
        resolved_staging_dir,
        sop,
        generated_at,
        all_review_reasons,
        review_items,
    )

    summary = {
        "scanned": len(changes) + len(skipped),
        "changed": len(changes),
        "skipped": len(skipped),
        "requires_review": bool(review_items),
    }
    should_write_delta = summary["scanned"] > 0
    run_id = _unique_run_id(resolved_delta_dir, _run_id(generated_at))
    delta = {
        "schema": DELTA_SCHEMA,
        "run_id": run_id,
        "generated_at": generated_at,
        "sop": {
            "path": _rel(root_path, resolved_sop_path),
            "sha256": _sha256_file(resolved_sop_path),
            "name": sop.get("name", "unnamed"),
        },
        "inputs": {
            "root": str(root_path),
            "input_dir": _rel(root_path, resolved_input_dir),
        },
        "summary": summary,
        "changes": changes,
        "skipped": skipped,
        "warnings": warnings,
        "boundary_gate": boundary_gate,
    }

    if should_write_delta:
        delta_path = resolved_delta_dir / f"{run_id}.json"
        delta["delta_path"] = _rel(root_path, delta_path)
        _write_json(delta_path, delta)
        _append_audit(
            root_path,
            "phase1",
            {
                "event": "phase1.run",
                "generated_at": generated_at,
                "run_id": run_id,
                "changed": summary["changed"],
                "skipped": summary["skipped"],
                "gate": boundary_gate["status"],
                "review_ids": [_review_id([item]) for item in review_items],
                "released_review_ids": released_ids,
                "warnings": [w["code"] + ":" + w["review_id"] for w in warnings],
            },
        )
    else:
        delta["delta_path"] = None

    _record_run(state, generated_at, delta["delta_path"], delta["summary"])
    _write_json(resolved_state_path, state)

    return delta


def _read_text(path: Path) -> str:
    """Decode an input for gating and excerpts. UTF-16 (BOM) is decoded properly and
    stray NULs are dropped, so a UTF-16 file cannot hide its words from the gate."""
    data = path.read_bytes()
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = data.decode("utf-16", errors="replace")
    else:
        text = data.decode("utf-8", errors="replace")
    return text.replace("\x00", "")


def _gate_text(path: Path, text: str) -> str:
    """Text the keyword gate sees: the raw text, plus (for .json) the decoded string
    values, so `\\u0064eploy` in a JSON file reads as "deploy"."""
    if path.suffix.lower() != ".json":
        return text
    try:
        decoded = json.dumps(json.loads(text), ensure_ascii=False)
    except (ValueError, RecursionError):
        return text
    return text + "\n" + decoded


def _ensure_layout(*directories: Path) -> None:
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": STATE_SCHEMA,
            "processed_files": {},
            "processed_hashes": {},
            "runs": [],
        }
    state = _read_json(path)
    state.setdefault("schema", STATE_SCHEMA)
    state.setdefault("processed_files", {})
    state.setdefault("processed_hashes", {})
    state.setdefault("runs", [])
    return state


def _iter_input_files(
    input_dir: Path, root: Path, allowed_extensions: set[str]
) -> list[Path]:
    if not input_dir.exists():
        return []

    files: list[Path] = []
    for path in input_dir.rglob("*"):
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(input_dir).parts):
            continue
        if path.suffix.lower() not in allowed_extensions:
            continue
        # read/hash can still race with filesystem changes; keep the resolved
        # target inside the root as a final containment check.
        real = path.resolve()
        if real != root and root not in real.parents:
            continue
        files.append(path)
    return sorted(files)


def _has_processed(state: dict[str, Any], relative_path: str, content_hash: str) -> bool:
    processed_file = state["processed_files"].get(relative_path)
    if processed_file and processed_file.get("sha256") == content_hash:
        return True
    return content_hash in state["processed_hashes"]


def _processed_at_path(state: dict[str, Any], relative_path: str, content_hash: str) -> bool:
    processed_file = state["processed_files"].get(relative_path)
    return bool(processed_file) and processed_file.get("sha256") == content_hash


def _record_processed(
    state: dict[str, Any],
    relative_path: str,
    content_hash: str,
    size_bytes: int,
    processed_at: str,
) -> None:
    state["processed_files"][relative_path] = {
        "sha256": content_hash,
        "size_bytes": size_bytes,
        "processed_at": processed_at,
    }
    # Cross-path dedup is by content hash, but empty files all share one hash —
    # don't register it, or only the first empty file would ever be ingested.
    if size_bytes > 0:
        state["processed_hashes"].setdefault(
            content_hash,
            {
                "first_seen_path": relative_path,
                "first_seen_at": processed_at,
            },
        )


def _record_run(
    state: dict[str, Any],
    generated_at: str,
    delta_path: str | None,
    summary: dict[str, Any],
) -> None:
    state["last_run_at"] = generated_at
    state["last_delta_path"] = delta_path
    state["runs"].append(
        {
            "generated_at": generated_at,
            "delta_path": delta_path,
            "summary": summary,
        }
    )
    state["runs"] = state["runs"][-50:]


def _match_routes(text: str, routes: list[dict[str, Any]]) -> list[str]:
    matches: list[str] = []
    for route in routes:
        label = route.get("label")
        if label and _match_keywords(text, route.get("keywords", [])):
            matches.append(str(label))
    return matches


def _excerpt(text: str, max_chars: int) -> str:
    compacted = " ".join(text.split())
    if len(compacted) <= max_chars:
        return compacted
    return compacted[: max(0, max_chars - 3)] + "..."


def _build_boundary_gate(
    root: Path,
    staging_dir: Path,
    sop: dict[str, Any],
    generated_at: str,
    reasons: list[str],
    review_items: list[dict[str, Any]],
) -> dict[str, Any]:
    index_path = staging_dir / "pending_review.json"
    # Every pending review is re-derived from the inbox each run, so the index is
    # rebuilt from the currently gated items: a review whose file was removed or
    # defused drops out instead of lingering as a phantom "operator action required".
    reviews: list[str] = []
    for item in review_items:
        review_id = _review_id([item])
        review_payload_path = staging_dir / "reviews" / f"{review_id}.json"
        review_payload = {
            "schema": REVIEW_PAYLOAD_SCHEMA,
            "review_id": review_id,
            "generated_at": generated_at,
            "source": "phase1",
            "notification_message": sop.get("boundary_gate", {}).get(
                "notification_message",
                "Boundary gate review required",
            ),
            "action_class": classify_reasons(root, list(item.get("review_reasons", []))),
            "risk_reasons": list(item.get("review_reasons", [])),
            "source_items": [item],
            "generated_artifacts": [],
            "verifier": {
                "required": True,
                "status": "pending",
                "result": None,
            },
            "suggested_verification": [
                "Inspect the source item listed in source_items before taking any outward action.",
                "Confirm the action is intentional, safe, and still requested.",
            ],
        }
        if not review_payload_path.exists():
            _write_json(review_payload_path, review_payload)
        reviews.append(_rel(root, review_payload_path))

    if review_items or index_path.exists():
        _write_json(
            index_path,
            {
                "schema": PENDING_REVIEW_INDEX_SCHEMA,
                "generated_at": generated_at,
                "latest": reviews[-1] if reviews else "",
                "reviews": reviews,
            },
        )
    if not review_items:
        return {
            "status": "clear",
            "payload": None,
            "reasons": [],
        }
    return {
        "status": "pending_review",
        "payload": _rel(root, index_path),
        "reasons": reasons,
    }


def _review_id(review_items: list[dict[str, Any]]) -> str:
    basis = json.dumps(
        {
            "items": [
                {
                    "relative_path": item.get("relative_path"),
                    "sha256": item.get("sha256"),
                    "review_reasons": item.get("review_reasons", []),
                }
                for item in review_items
            ],
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    return f"review-{digest}"


def _run_id(generated_at: str) -> str:
    return "phase1-delta-" + "".join(character for character in generated_at if character.isalnum())


def _unique_run_id(delta_dir: Path, base: str) -> str:
    run_id = base
    suffix = 2
    # The supervisor archives observed deltas out of delta_dir, so check the
    # archive too, or a same-second run after archiving would reuse an existing
    # run id and make the audit trail ambiguous.
    while (delta_dir / f"{run_id}.json").exists() or (
        delta_dir / "archive" / f"{run_id}.json"
    ).exists():
        run_id = f"{base}-{suffix}"
        suffix += 1
    return run_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Phase 1 local ingestion pass.")
    parser.add_argument(
        "--root",
        default=_default_root(),
        help="Project root. Defaults to $LOOPING_BOX_ROOT, else the current directory.",
    )
    parser.add_argument("--input-dir", default="inbox", help="Directory to scan for local input files.")
    parser.add_argument(
        "--sop",
        default="config/sops/phase1_ingestion.json",
        help="Machine-readable SOP used for routing and boundary-gate matching.",
    )
    parser.add_argument(
        "--state",
        default="cache/state/phase1_state.json",
        help="Persistent cache state file.",
    )
    parser.add_argument("--delta-dir", default="cache/deltas", help="Directory for delta JSON files.")
    parser.add_argument("--staging-dir", default="staging", help="Directory for pending review payloads.")
    args = parser.parse_args()

    try:
        delta = run_phase1(
            args.root,
            input_dir=args.input_dir,
            sop_path=args.sop,
            state_path=args.state,
            delta_dir=args.delta_dir,
            staging_dir=args.staging_dir,
        )
    except RuntimeError as exc:  # another phase1/supervisor/review holds the lock
        print(f"busy: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"error: {exc.filename} not found. Is this a workspace? Try: looping-box init", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:  # corrupt SOP/state JSON, path outside the root, ...
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"delta: {delta['delta_path'] or 'none'}")
    print(
        "summary: "
        f"{delta['summary']['changed']} changed, "
        f"{delta['summary']['skipped']} skipped, "
        f"review={delta['boundary_gate']['status']}"
    )
    for warning in delta.get("warnings", []):
        print(f"warning: {warning['relative_path']}: {warning['message']}", file=sys.stderr)
    if delta["boundary_gate"]["status"] == "pending_review":
        bell = "\a" if sys.stdout.isatty() else ""  # no control chars in pipes/logs
        print(f"{bell}BOUNDARY GATE: review required before outward action.")
        print(f"payload: {delta['boundary_gate']['payload']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
