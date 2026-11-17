from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator
from functools import cache
from itertools import groupby
from pathlib import Path
from typing import Any, NamedTuple

from ._util import (
    PENDING_REVIEW_INDEX_SCHEMA,
    append_audit as _append_audit,
    decision_state as _decision_state,
    inspect_audit_log,
    key_status as _key_status,
    default_root as _default_root,
    preflight_audit as _preflight_audit,
    project_lock as _project_lock,
    read_pending_review_index as _read_pending_review_index,
    read_review_payload as _read_review_payload,
    rel as _rel,
    retained_phase1_delta_dirs,
    review_audit_event,
    review_evidence_digest as _review_evidence_digest,
    review_identity as _review_identity,
    resolve_under_root as _resolve_under_root,
    signed_rejections,
    utc_now as _utc_now,
    write_bytes as _write_bytes,
    write_json as _write_json,
)
from .action_policy import _read_action_config, classify_reasons, match_keywords as _match_keywords
from .paths import directory_path, file_path as _file_path, matching_files, read_validated, read_validated_snapshot, source_files
from .queue_order import compare_delta_order, delta_order, persisted_delta_order, remember_delta_order
from .validation import ValidationError, validate_data, validate_policy


STATE_SCHEMA = "looping-box.phase1.state.v1"
DELTA_SCHEMA = "looping-box.phase1.delta.v1"
REVIEW_PAYLOAD_SCHEMA = "looping-box.review-payload.v1"
DEFAULT_MAX_FILE_BYTES = 1_000_000
DEFAULT_SOP_PATH = "config/sops/phase1_ingestion.json"
DEFAULT_STATE_PATH = "cache/state/phase1_state.json"


class _SourceEvents(NamedTuple):
    order: tuple[str, int, str]
    changes: list[dict[str, Any]]
    skips: list[dict[str, Any]]
    removed: bool


class _RetainedHistory(NamedTuple):
    events: dict[str, list[_SourceEvents]]
    persisted_order: dict[str, int]
    unavailable: list[tuple[str, int, str]]


class _UndraftedRelease(NamedTuple):
    review_id: str
    directory: str


def run_phase1(
    root: Path | str,
    *,
    now: str | None = None,
    input_dir: Path | str = "inbox",
    sop_path: Path | str = DEFAULT_SOP_PATH,
    state_path: Path | str = DEFAULT_STATE_PATH,
    delta_dir: Path | str = "cache/deltas",
    staging_dir: Path | str = "staging",
) -> dict[str, Any]:
    """Run one deterministic local ingestion pass (serialized by the project lock)."""
    root_path = Path(root).resolve()
    generated_at = now or _utc_now()
    _validated_source_sop(root_path, _resolve_under_root(root_path, sop_path))
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

    sop, sop_sha256 = _validated_source_sop(root_path, resolved_sop_path)
    sop_reference = _rel(root_path, resolved_sop_path)
    sop_record = {"path": sop_reference, "sha256": sop_sha256, "name": sop["name"]}
    source_sop = sop_record if sop_reference != DEFAULT_SOP_PATH else None
    state = _read_state(resolved_state_path, input_dir=_rel(root_path, resolved_input_dir))
    _preflight_audit(root_path, "phase1")

    shared_staging = resolved_staging_dir == root_path / "staging"
    registration_path = root_path / DEFAULT_STATE_PATH if shared_staging else resolved_state_path
    registration = _read_state(registration_path, normalize=False)
    history_states = (state,) if registration_path == resolved_state_path else (state, registration)
    selected_delta_directory = _rel(root_path, resolved_delta_dir)
    is_canonical_delta_dir = selected_delta_directory == "cache/deltas"
    delta_directories = {selected_delta_directory}
    for snapshot in history_states:
        delta_directories.update(retained_phase1_delta_dirs(snapshot))
    if shared_staging or resolved_state_path == root_path / DEFAULT_STATE_PATH or any(
        Path(reference).parent.as_posix() == "cache/deltas"
        for reference in (state.get("last_delta_path"), *(run["delta_path"] for run in state["runs"]))
        if reference is not None
    ):
        delta_directories.add("cache/deltas")

    @cache
    def retained_history() -> _RetainedHistory:
        return _retained_source_events(root_path, delta_directories, history_states)

    @cache
    def review_audit_order() -> dict[str, int]:
        audit = inspect_audit_log(_resolve_under_root(root_path, "logs/transactions/review.jsonl"))
        if audit.broken_at is not None:
            return {}
        return {_review_event_key(event): index for index, event in enumerate(audit.events)}

    allowed_extensions = {
        extension.lower() for extension in sop.get("allowed_extensions", [".md", ".txt", ".json"])
    }

    changes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    released_ids: list[str] = []
    warnings: list[dict[str, str]] = []
    stale_approvals: set[str] = set()
    retired_records: list[Path] = []

    input_prefix = _rel(root_path, resolved_input_dir) + "/"
    prior_present = set(state["present_paths"])
    scanned_paths: list[str] = []
    for file_path in _iter_input_files(resolved_input_dir, root_path, allowed_extensions):
        relative_path = _rel(root_path, file_path)
        scanned_paths.append(relative_path)
        change = _derive_change(file_path, relative_path, sop)
        content_hash, size_bytes, review_reasons = (
            change["sha256"], change["size_bytes"], change["review_reasons"]
        )
        processed = state["processed_files"].get(relative_path, {})

        if review_reasons:
            review_id = _review_id([change])
            prior_pending_id = processed.get("pending_review_release")
            last_released_review = processed.get("last_released_review")
            if prior_pending_id != review_id:
                processed.pop("pending_review_release", None)
            decision, unverifiable = _decision_state(resolved_staging_dir, review_id)
            same_bytes = _processed_at_path(state, relative_path, content_hash)
            if decision == "approved":
                payload = _read_review_payload(resolved_staging_dir / "reviews" / f"{review_id}.json")
                action_class = classify_reasons(root_path, review_reasons)
                approval_sop = payload.get("source_sop", {}).get("path", DEFAULT_SOP_PATH)
                if (approval_sop != sop_reference or action_class == "forbidden"
                        or not _review_matches(payload, change, action_class)):
                    stale_approvals.add(review_id)
                    decision = None
            # An explicit null records cancellation. An absent field is legacy state.
            release_may_need_reconciliation = prior_pending_id is not None or (
                (last_released_review is not None or "last_released_review" not in processed)
                and (decision is None or last_released_review != review_id)
            )
            check_queue_delivery = (is_canonical_delta_dir and decision == "approved"
                                    and last_released_review == review_id and len(delta_directories) > 1)
            if (decision != "rejected" and same_bytes and relative_path in prior_present
                    and processed.get("pending_review_release") != review_id
                    and (release_may_need_reconciliation or check_queue_delivery)):
                undrafted = _undrafted_release(
                    root_path, resolved_staging_dir, retained_history(), change, last_released_review
                )
                if undrafted is not None:
                    last_released_review = undrafted.review_id
                    if (decision is None or prior_pending_id is not None or last_released_review != review_id
                            or (is_canonical_delta_dir and undrafted.directory != selected_delta_directory)):
                        # Carry an undrafted release through a review revision or a queue change.
                        processed["pending_review_release"] = review_id
            release_due = not same_bytes or processed.get("pending_review_release") == review_id
            if (decision == "approved" and release_due
                    and _approval_superseded(resolved_staging_dir, review_id, change, review_audit_order)):
                # A deferred release does not carry authority past a newer signed veto.
                stale_approvals.add(review_id)
                decision = None
            if decision == "approved" and release_due:
                # Retained processed state makes this a sequential one-time
                # release. Deleting or rolling back that state can permit replay.
                released = dict(change, review_reasons=[], approved_review=review_id)
                changes.append(released)
                released_ids.append(review_id)
                _record_processed(state, relative_path, content_hash, size_bytes, generated_at)
                state["processed_files"][relative_path]["last_released_review"] = review_id
                continue
            if decision is not None:
                skip = {"relative_path": relative_path, "sha256": content_hash, "reason": "review_decision_recorded"}
                if decision == "approved":
                    skip["current"] = {"matched_routes": change["matched_routes"], "excerpt": change["excerpt"],
                                       "approved_review": review_id}
                skipped.append(skip)
                _record_processed(state, relative_path, content_hash, size_bytes, generated_at)
                state["processed_files"][relative_path]["last_released_review"] = (
                    last_released_review if decision == "approved" else None
                )
                continue
            if unverifiable:
                unarchived = False
                for kind in ("approval", "rejection"):
                    record = _file_path(resolved_staging_dir / f"{kind}s" / f"{review_id}.json")
                    if record.exists():
                        data = record.read_bytes()
                        archived = _archived_review_path(resolved_staging_dir, record, kind, data)
                        if archived.exists() and archived.read_bytes() == data:
                            retired_records.append(record)
                        else:
                            unarchived = True
                if unarchived:
                    warnings.append({
                        "code": "decision_unverifiable", "review_id": review_id, "relative_path": relative_path,
                        "message": "a decision record exists but did not verify "
                        f"(edited, forged, or signed with a different key); {_key_status()}",
                    })
            changes.append(change)
            review_items.append(change)
            # Pending-review files are NOT recorded as processed: a file that
            # trips the boundary gate keeps re-surfacing (and regenerates the
            # staging payload) every run until a human handles/removes it.
            continue

        processed.pop("pending_review_release", None)
        if "last_released_review" in processed:
            processed["last_released_review"] = None
        if _has_processed(state, relative_path, content_hash):
            skipped.append(
                {
                    "relative_path": relative_path,
                    "sha256": content_hash,
                    "reason": "already_processed",
                    "current": {"matched_routes": change["matched_routes"], "excerpt": change["excerpt"]},
                }
            )
            continue

        changes.append(change)
        _record_processed(state, relative_path, content_hash, size_bytes, generated_at)

    scanned_set = set(scanned_paths)
    removed_paths = sorted(path for path in prior_present if path.startswith(input_prefix) and path not in scanned_set)
    state["present_paths"] = sorted({path for path in prior_present if not path.startswith(input_prefix)} | scanned_set)
    for path in removed_paths:
        processed = state["processed_files"].get(path, {})
        processed.pop("pending_review_release", None)
        if "last_released_review" in processed:
            processed["last_released_review"] = None

    boundary_gate, pending_index, review_writes, replaced_records = _build_boundary_gate(
        root_path, resolved_staging_dir, retained_history, input_prefix, sop,
        generated_at, review_items, stale_approvals, source_sop,
    )

    summary = {
        "scanned": len(changes) + len(skipped),
        "changed": len(changes),
        "skipped": len(skipped),
        "requires_review": boundary_gate["status"] == "pending_review",
    }
    should_write_delta = summary["scanned"] > 0 or bool(removed_paths)
    reserved_names = {
        Path(reference).stem
        for snapshot in (state, registration)
        for reference in (*snapshot.get("same_time_delta_order", {}).get(generated_at, []),
                          *(run["delta_path"] for run in snapshot["runs"] if run["generated_at"] == generated_at))
        if reference and Path(reference).parent.as_posix() == selected_delta_directory
    }
    run_id = _unique_run_id(resolved_delta_dir, _run_id(generated_at), reserved_names)
    delta = {
        "schema": DELTA_SCHEMA,
        "run_id": run_id,
        "generated_at": generated_at,
        "sop": sop_record,
        "inputs": {
            "root": str(root_path),
            "input_dir": _rel(root_path, resolved_input_dir),
        },
        "summary": summary,
        "changes": changes,
        "skipped": skipped,
        "warnings": warnings,
        "reconciliation": {"scanned_paths": scanned_paths, "removed_paths": removed_paths},
        "boundary_gate": boundary_gate,
    }
    released_hashes = {change["relative_path"]: change["sha256"] for change in changes
                       if not change["review_reasons"]}
    if released_hashes:
        # Resolve signed decision ties without relying on wall-clock seconds.
        delta["prior_rejection_signatures"] = sorted({
            record["signature"] for record, _ in signed_rejections(resolved_staging_dir, released_hashes)
        })

    # Upgrade legacy directory references while they are still in run history.
    custom_directories = retained_phase1_delta_dirs(state)
    if should_write_delta:
        delta_path = resolved_delta_dir / f"{run_id}.json"
        delta["delta_path"] = _rel(root_path, delta_path)
        validate_data(delta, "phase1_delta", str(delta_path))
        delta_directory = _rel(root_path, resolved_delta_dir)
        if delta_directory != "cache/deltas":
            custom_directories.add(delta_directory)
    else:
        delta["delta_path"] = None

    prior_directories = retained_phase1_delta_dirs(registration)
    if should_write_delta and shared_staging and not is_canonical_delta_dir and not prior_directories:
        # A default delta can survive an interrupted final state write before the
        # first alternate queue is registered. Its local suffix orders it with
        # other default publications, all of which precede this alternate scan.
        stamp = generated_at
        known = {run["delta_path"] for run in registration["runs"]
                 if run["generated_at"] == stamp and run["delta_path"]}
        known.update(registration.get("same_time_delta_order", {}).get(stamp, []))
        if all(Path(reference).parent.as_posix() == "cache/deltas" for reference in known):
            preceding = {delta["delta_path"] for delta in _retained_deltas(
                root_path, {"cache/deltas"}, pattern=f"{_run_id(stamp)}*.json"
            ) if delta["generated_at"] == stamp}
            if preceding - known:
                registration.setdefault("same_time_delta_order", {})[stamp] = sorted(known | preceding, key=delta_order)
    registered_directories = prior_directories | custom_directories
    registry_changed = set(registration.get("custom_delta_dirs", [])) != registered_directories
    if registered_directories:
        registration["custom_delta_dirs"] = sorted(registered_directories)
    order_changed = remember_delta_order(
        registration, generated_at, delta["delta_path"],
        reserve_first=shared_staging and (registration_path != resolved_state_path or bool(registered_directories)),
    )
    if registration_path == resolved_state_path:
        for field in ("custom_delta_dirs", "same_time_delta_order"):
            if field in registration:
                state[field] = registration[field]
    else:
        if custom_directories:
            state["custom_delta_dirs"] = sorted(custom_directories)
        remember_delta_order(state, generated_at, delta["delta_path"])
    _record_run(state, generated_at, delta["delta_path"], delta["summary"])
    validate_data(state, "phase1_state", str(resolved_state_path))
    _ensure_layout(
        resolved_input_dir, resolved_sop_path.parent, resolved_state_path.parent,
        resolved_delta_dir, resolved_staging_dir,
    )
    if registry_changed or order_changed:
        validate_data(registration, "phase1_state", str(registration_path))
        _write_json(registration_path, registration)
    if should_write_delta:
        # Make the queue evidence discoverable before exposing review evidence,
        # including a replacement payload already referenced by the live index.
        _write_json(delta_path, delta)
    for path, content in review_writes:
        _write_bytes(path, content)
    if pending_index is not None:
        _write_json(resolved_staging_dir / "pending_review.json", pending_index)
    _write_json(resolved_state_path, state)
    if should_write_delta:
        try:
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
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"audit_incomplete: ingestion saved {delta['delta_path']} and processed state; "
                f"inspect the phase1 audit log before retrying: {exc}"
            ) from exc

    for path in sorted(set(retired_records + replaced_records)):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ValueError(
                f"review_retirement_incomplete: ingestion saved current evidence; "
                f"retry to retire the archived decision at {path}: {exc}"
            ) from exc
    return delta


def _derive_change(path: Path, reference: str, sop: dict[str, Any]) -> dict[str, Any]:
    digest, size, text = _snapshot_input(path, sop["max_file_bytes"])
    if text is None:
        routes, reasons, excerpt = [], ["oversized_input"], ""
    else:
        routes = _match_routes(text, sop["routes"])
        reasons = _match_keywords(_gate_text(path, text), sop["boundary_gate"]["requires_review_keywords"])
        excerpt = _excerpt(text, sop["max_excerpt_chars"])
    return {"relative_path": reference, "sha256": digest, "size_bytes": size,
            "matched_routes": routes, "review_reasons": reasons, "excerpt": excerpt}


def _snapshot_input(path: Path, max_bytes: int) -> tuple[str, int, str | None]:
    """Hash and decode one bounded snapshot; stream oversized files undecoded.

    None denotes oversized input. Its hash is guarded by metadata checks around
    the stream; a changing stream is evidence of input_changed, never success.
    """
    path = _file_path(path)
    # No read-ahead buffer may survive the growth fallback's seek: the guarded
    # stream must see bytes read after its initial metadata observation.
    with path.open("rb", buffering=0) as handle:
        if os.fstat(handle.fileno()).st_size <= max_bytes:
            data = handle.read(max_bytes + 1)
            if len(data) <= max_bytes:
                return hashlib.sha256(data).hexdigest(), len(data), _decode_bytes(data)
            del data  # grew beyond the cap: do not decode or keep the buffer
        handle.seek(0)
        before = os.fstat(handle.fileno())
        digest = hashlib.sha256()
        size_bytes = 0
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size_bytes += len(chunk)
        after = os.fstat(handle.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size_bytes != after.st_size:
            raise ValueError(f"input_changed: {path}: size or modification time changed during oversized read")
        return digest.hexdigest(), size_bytes, None


def _decode_bytes(data: bytes) -> str:
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
        directory_path(directory).mkdir(parents=True, exist_ok=True)


def _validated_sop(root: Path, path: Path) -> dict[str, Any]:
    return _validated_source_sop(root, path)[0]


def _validated_source_sop(root: Path, path: Path) -> tuple[dict[str, Any], str]:
    """Derive the selected SOP with the workspace's mandatory gate and size cap."""
    path = _file_path(path)
    if not path.exists():
        raise ValidationError(str(path), "$: required SOP is missing; run looping-box init", code="workspace_uninitialized")
    sop, raw = read_validated_snapshot(path, "phase1_ingestion")
    # Configuration must fail before any mutating entry point starts work.
    for relative, kind in (("config/action_classes.json", "action_classes"),
                           ("config/super_loop.json", "super_loop")):
        optional = _resolve_under_root(root, relative)
        if optional.exists():
            read_validated(optional, kind)
    classes = _read_action_config(root)
    validate_policy(sop, classes, str(path))
    workspace_path = _file_path(_resolve_under_root(root, DEFAULT_SOP_PATH))
    if path != workspace_path and workspace_path.exists():
        workspace = read_validated(workspace_path, "phase1_ingestion")
        validate_policy(workspace, classes, str(workspace_path))
        gate = sop["boundary_gate"]
        # Keep custom reason order and add any mandatory workspace reasons.
        gate["requires_review_keywords"] = list(dict.fromkeys(
            gate["requires_review_keywords"] + workspace["boundary_gate"]["requires_review_keywords"]
        ))
        sop["max_file_bytes"] = min(sop["max_file_bytes"], workspace["max_file_bytes"])
    return sop, hashlib.sha256(raw).hexdigest()


def _read_state(path: Path, *, input_dir: str = "inbox", normalize: bool = True) -> dict[str, Any]:
    path = _file_path(path)
    if not path.exists():
        return {
            "schema": STATE_SCHEMA,
            "processed_files": {},
            "processed_hashes": {},
            "present_paths": [],
            "runs": [],
        }
    state, raw = read_validated_snapshot(path, "phase1_state", input_dir=input_dir)
    return state if normalize else json.loads(raw)


def _iter_input_files(
    input_dir: Path, root: Path, allowed_extensions: set[str]
) -> list[Path]:
    # Resolve only the trusted root, never a source descendant. The source walker
    # excludes links before examining their target type or entering directories.
    relative = input_dir.relative_to(root).as_posix()
    return source_files(_resolve_under_root(root, relative), allowed_extensions)


def _has_processed(state: dict[str, Any], relative_path: str, content_hash: str) -> bool:
    processed_file = state["processed_files"].get(relative_path)
    if processed_file and processed_file.get("sha256") == content_hash:
        return True
    return content_hash != hashlib.sha256(b"").hexdigest() and content_hash in state["processed_hashes"]


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
    retained_history: Callable[[], _RetainedHistory],
    input_prefix: str,
    sop: dict[str, Any],
    generated_at: str,
    review_items: list[dict[str, Any]],
    stale_approvals: set[str],
    source_sop: dict[str, str] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[tuple[Path, bytes]], list[Path]]:
    """Plan current review writes, preserving holds outside the scanned directory."""
    index_path = _file_path(staging_dir / "pending_review.json")
    index_exists = index_path.exists()
    retained_reviews = not index_exists and _check_missing_index_scope(root, staging_dir, index_path, input_prefix)
    prior = _read_pending_review_index(index_path)
    reviews: list[str] = []
    reasons: list[str] = []
    writes: list[tuple[Path, bytes]] = []
    retired_records: list[Path] = []
    current_paths = {staging_dir / "reviews" / f"{_review_id([item])}.json" for item in review_items}
    for reference in prior["reviews"]:
        path = _resolve_under_root(root, reference)
        if path in current_paths and not path.exists():
            continue  # This scan can reconstruct the missing payload from its source.
        payload = _read_review_payload(path)
        if path != staging_dir / "reviews" / f"{payload['review_id']}.json":
            raise ValueError(f"review_evidence_invalid: {reference}: unexpected review location")
        if not payload["source_items"][0]["relative_path"].startswith(input_prefix):
            if _decision_state(staging_dir, payload["review_id"])[0] is None:
                reviews.append(reference)
                reasons.extend(payload["risk_reasons"])

    for item in review_items:
        review_id = _review_id([item])
        path = _file_path(staging_dir / "reviews" / f"{review_id}.json")
        action_class = classify_reasons(root, item["review_reasons"])
        previous = _read_review_payload(path) if path.exists() else None
        reviews.append(_rel(root, path))
        reasons.extend(item["review_reasons"])
        if previous is not None:
            stamp = previous.get("source_evidence_sha256")
            previous_sop = previous.get("source_sop", {}).get("path", DEFAULT_SOP_PATH)
            selected_sop = source_sop["path"] if source_sop is not None else DEFAULT_SOP_PATH
            if (review_id not in stale_approvals and previous_sop == selected_sop
                    and _review_matches(previous, item, action_class)):
                continue
            # Older payloads lack an evidence digest. Confirm their source fields
            # against the same retained events used to reconcile prior releases.
            if (stamp is None and review_id not in stale_approvals
                    and not any(previous["source_items"][0] in event.changes
                                for event in retained_history().events.get(item["relative_path"], ()))):
                raise ValueError(
                    f"review_evidence_invalid: {path}: prior source evidence has no matching ingestion record; "
                    "restore its matching delta or inspect and archive the old evidence"
                )
            archive_writes, old_records = _archive_review_revision(root, staging_dir, path)
            writes.extend(archive_writes)
            retired_records.extend(old_records)

        payload = {
            "schema": REVIEW_PAYLOAD_SCHEMA,
            "review_id": review_id,
            "generated_at": generated_at,
            "source": "phase1",
            "notification_message": sop["boundary_gate"]["notification_message"],
            "action_class": action_class,
            "risk_reasons": item["review_reasons"],
            "source_items": [item],
            "generated_artifacts": [],
            "verifier": {"required": True, "status": "pending", "result": None},
            "suggested_verification": [
                "Inspect the source item listed in source_items before taking any outward action.",
                "Confirm the action is intentional, safe, and still requested.",
            ],
        }
        if source_sop is not None:
            payload["source_sop"] = source_sop
        payload["source_evidence_sha256"] = _review_evidence_digest(payload)
        validate_data(payload, "review_payload", str(path))
        writes.append((path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")))

    index = None
    if reviews or index_exists or retained_reviews:
        index = {"schema": PENDING_REVIEW_INDEX_SCHEMA, "generated_at": generated_at,
                 "latest": reviews[-1] if reviews else "", "reviews": reviews}
        validate_data(index, "pending_review_index", str(index_path))
    return {
        "status": "pending_review" if reviews else "clear",
        "payload": _rel(root, index_path) if reviews else None,
        "reasons": list(dict.fromkeys(reasons)),
    }, index, writes, retired_records


def _check_missing_index_scope(root: Path, staging_dir: Path, index_path: Path, input_prefix: str) -> bool:
    """Check scope and report whether retained reviews require an explicit index."""
    directory = directory_path(staging_dir / "reviews")
    try:
        payloads = sorted(path for path in directory.iterdir() if path.suffix == ".json")
    except FileNotFoundError:
        return False
    for path in payloads:
        payload = _read_review_payload(path)
        reference = payload["source_items"][0]["relative_path"]
        if not reference.startswith(input_prefix) and _decision_state(staging_dir, payload["review_id"])[0] is None:
            raise ValueError(
                f"review_state_unavailable: {index_path}: retained review {_rel(root, path)} "
                f"is outside the scanned directory. Restore the index or scan a directory covering its unresolved reviews."
            )
    return bool(payloads)


def _review_matches(payload: dict[str, Any], item: dict[str, Any], action_class: str) -> bool:
    stored = payload["source_items"][0]
    return payload["action_class"] == action_class and all(stored.get(field) == value for field, value in item.items())


def _review_event_key(event: dict[str, Any]) -> str:
    return json.dumps({key: value for key, value in event.items() if key not in {"schema", "prev", "hash"}},
                      sort_keys=True)


def _approval_superseded(
    staging_dir: Path, review_id: str, item: dict[str, Any], review_audit_order: Callable[[], dict[str, int]],
) -> bool:
    rejections = [record for record, _ in signed_rejections(staging_dir, {item["relative_path"]: item["sha256"]})]
    if not rejections:
        return False
    approval = read_validated(staging_dir / "approvals" / f"{review_id}.json", "review_record")
    audit_order = review_audit_order()
    approved_at = audit_order.get(_review_event_key(review_audit_event(approval)))
    for rejection in rejections:
        rejected_at = audit_order.get(_review_event_key(review_audit_event(rejection)))
        if approved_at is not None and rejected_at is not None:
            if approved_at <= rejected_at:
                return True
        elif approval["generated_at"] <= rejection["generated_at"]:
            return True
    return False


def _undrafted_release(
    root: Path, staging_dir: Path, history: _RetainedHistory, item: dict[str, Any], last_released_review: str | None,
) -> _UndraftedRelease | None:
    """Check whether an earlier approved release for these bytes is still owed."""
    reference, digest = item["relative_path"], item["sha256"]
    events = history.events.get(reference, ())
    release_ids = {
        change["approved_review"] for event in events for change in event.changes
        if change["sha256"] == digest and change.get("approved_review")
    }
    if not release_ids or (last_released_review is not None and last_released_review not in release_ids):
        return None
    queued = None
    queued_order = None
    held_after_release = False
    rejected: dict[str, bool] = {}
    ordered = sorted(events, key=lambda event: event.order)
    for _, same_time in groupby(ordered, key=lambda event: event.order[0]):
        group = list(same_time)
        if len({Path(event.order[2]).parent for event in group}) > 1:
            if not all(event.order[2] in history.persisted_order for event in group):
                # A local suffix cannot order events from independent queues.
                queued, held_after_release = None, False
                continue
            group.sort(key=lambda event: history.persisted_order[event.order[2]])
        for order, changes, skips, removed in group:
            for change in changes:
                if change["sha256"] != digest:
                    queued, held_after_release = None, False
                elif approval := change.get("approved_review"):
                    queued, queued_order, held_after_release = approval, order, False
                elif change["review_reasons"] and queued is not None:
                    held_after_release = True
                    held_id = _review_id([change])
                    if held_id not in rejected:
                        rejected[held_id] = _decision_state(staging_dir, held_id)[0] == "rejected"
                    if rejected[held_id]:
                        queued = None
                elif not change["review_reasons"]:
                    queued, held_after_release = None, False
            for skip in skips:
                # Legacy approval skips omit "current". With no intervening hold
                # they must not cancel an earlier queued release for the same bytes.
                if (skip["sha256"] != digest or skip["reason"] == "already_processed"
                        or (held_after_release and not skip.get("current", {}).get("approved_review"))):
                    queued, held_after_release = None, False
            if removed:
                queued, held_after_release = None, False
    if (queued is None or queued_order is None
            or any(compare_delta_order(queued_order, missing, history.persisted_order) != 1 for missing in history.unavailable)
            or _has_drafted_review(root, reference, release_ids)):
        return None
    return _UndraftedRelease(queued, Path(queued_order[2]).parent.as_posix())


def _retained_source_events(root: Path, delta_dirs: set[str], states: tuple[dict[str, Any], ...]) -> _RetainedHistory:
    events: dict[str, list[_SourceEvents]] = defaultdict(list)
    retained: dict[str, dict[str, Any]] = {}
    unavailable = {}
    for delta in _retained_deltas(root, delta_dirs):
        reference = delta["delta_path"]
        if reference in unavailable:
            continue
        if retained.setdefault(reference, delta) != delta:
            unavailable[reference] = delta_order(reference, retained.pop(reference)["generated_at"])
    for state in states:
        for run in state["runs"]:
            reference = run["delta_path"]
            if reference is not None and reference not in retained:
                unavailable[reference] = delta_order(reference, run["generated_at"])
        reference = state.get("last_delta_path")
        if reference is not None and reference not in retained:
            unavailable[reference] = delta_order(reference, state.get("last_run_at"))

    for delta in retained.values():
        order = delta_order(delta["delta_path"], delta["generated_at"], run_id=delta["run_id"])
        changes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        skips: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for change in delta["changes"]:
            changes[change["relative_path"]].append(change)
        for skip in delta["skipped"]:
            skips[skip["relative_path"]].append(skip)
        removed = set(delta["reconciliation"]["removed_paths"])
        for reference in changes.keys() | skips.keys() | removed:
            events[reference].append(_SourceEvents(order, changes[reference], skips[reference], reference in removed))
    # The canonical state records shared-staging publication order, including
    # publications that update a separate processed-file state.
    return _RetainedHistory(events, persisted_delta_order(states[-1]), list(unavailable.values()))


def _retained_deltas(root: Path, delta_dirs: set[str], *, pattern: str = "*.json") -> Iterator[dict[str, Any]]:
    queues = {queue: _resolve_under_root(root, queue) for queue in sorted(delta_dirs)}
    seen_paths: set[Path] = set()
    for delta_dir in queues.values():
        for directory in (delta_dir, delta_dir / "archive"):
            for path in matching_files(directory, pattern):
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                try:
                    delta = read_validated(path, "phase1_delta")
                except (OSError, ValidationError):
                    continue
                reference = Path(delta["delta_path"])
                queue = queues.get(reference.parent.as_posix())
                if queue is not None and (path.parent == queue / "archive"
                                          or (path.parent == queue and path.name == reference.name)):
                    yield delta


def _has_drafted_review(root: Path, reference: str, review_ids: set[str]) -> bool:
    directory = _resolve_under_root(root, "cache/workers/execution_engine")
    history = directory_path(directory / "history")
    try:
        saved = sorted(path for path in history.iterdir() if path.name.endswith("-draft.json"))
    except FileNotFoundError:
        saved = []
    for path in (directory / "draft.json", *saved):
        try:
            draft, raw = read_validated_snapshot(path, "execution_draft")
        except FileNotFoundError:
            if path == directory / "draft.json":
                continue
            raise
        if (any(item.get("approved_review") in review_ids and item["relative_path"] == reference
                for item in draft["items"])
                and (path != directory / "draft.json" or _committed_execution_draft(directory, draft, raw))):
            return True
    return False


def _committed_execution_draft(output_dir: Path, draft: dict[str, Any], raw: bytes) -> bool:
    """Use committed state to distinguish completed drafts from partial output."""
    try:
        state = read_validated(output_dir / "state.json", "worker_state", worker_id="execution_engine")
    except FileNotFoundError:
        return False
    if (state.get("last_run_at") != draft["generated_at"]
            or state.get("last_context_sha256") != draft["source_context_sha256"]):
        return False
    digest = state.get("last_draft_sha256")
    if digest is not None:
        return digest == hashlib.sha256(raw).hexdigest()
    return _file_path(output_dir / "draft.md").exists()


def _archived_review_path(staging_dir: Path, path: Path, kind: str, data: bytes) -> Path:
    digest = hashlib.sha256(data).hexdigest()
    return _file_path(staging_dir / "archive" / f"{path.stem}-{digest}.{kind}.json")


def _archive_review_revision(
    root: Path, staging_dir: Path, payload_path: Path
) -> tuple[list[tuple[Path, bytes]], list[Path]]:
    # Copy original bytes before replacing the payload. Copies are idempotent if
    # an I/O interruption occurs between archiving and publishing its revision.
    review_id = payload_path.stem
    evidence_paths = {
        "payload": payload_path,
        "approval": staging_dir / "approvals" / payload_path.name,
        "rejection": staging_dir / "rejections" / payload_path.name,
        "verifier": _resolve_under_root(root, f"cache/verifiers/{review_id}.json"),
    }
    writes = []
    retired_records = []
    for kind, path in evidence_paths.items():
        path = _file_path(path)
        if path.exists():
            data = path.read_bytes()
            target = _archived_review_path(staging_dir, path, kind, data)
            if not target.exists():
                writes.append((target, data))
            elif target.read_bytes() != data:
                raise ValueError(f"review_evidence_invalid: {target}: archived evidence does not match")
            if kind in ("approval", "rejection"):
                retired_records.append(path)
    return writes, retired_records


def _review_id(review_items: list[dict[str, Any]]) -> str:
    return _review_identity(review_items)


def _run_id(generated_at: str) -> str:
    return "phase1-delta-" + "".join(character for character in generated_at if character.isalnum())


def _unique_run_id(delta_dir: Path, base: str, reserved_names: set[str]) -> str:
    # A prior id may be archived, pruned, or reserved before a failed write.
    # Allocate above the greatest known suffix rather than filling holes.
    names = reserved_names | {
        path.stem for directory in (delta_dir, delta_dir / "archive")
        for path in matching_files(directory, f"{base}*.json")
    }
    highest = int(base in names)
    for name in names:
        suffix = name.removeprefix(f"{base}-").partition("-")[0]
        if suffix.isascii() and suffix.isdecimal():
            highest = max(highest, int(suffix))
    return f"{base}-{highest + 1}" if highest else base


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
        default=DEFAULT_STATE_PATH,
        help="Persistent cache state file.",
    )
    parser.add_argument("--delta-dir", default="cache/deltas", help="Directory for delta JSON files.")
    parser.add_argument("--staging-dir", default="staging", help="Directory for pending review payloads.")
    args = parser.parse_args()

    try:
        args.root = Path(args.root).expanduser().resolve()
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: invalid_root: {args.root}: {exc}", file=sys.stderr)
        return 1
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
        print(f"error: workspace_uninitialized: {exc.filename} not found. Is this a workspace? Try: looping-box init", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:  # corrupt SOP/state JSON, path outside the root, ...
        print(f"error: phase1_error: {exc}", file=sys.stderr)
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
