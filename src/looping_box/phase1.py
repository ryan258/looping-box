from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .action_policy import classify_reasons


STATE_SCHEMA = "looping-box.phase1.state.v1"
DELTA_SCHEMA = "looping-box.phase1.delta.v1"
BOUNDARY_SCHEMA = "looping-box.boundary-review.v1"
PENDING_REVIEW_INDEX_SCHEMA = "looping-box.pending-review-index.v1"
REVIEW_PAYLOAD_SCHEMA = "looping-box.review-payload.v1"


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
    """Run one deterministic local ingestion pass."""
    root_path = Path(root).resolve()
    generated_at = now or _utc_now()

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

    changes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    all_review_reasons: list[str] = []

    for file_path in _iter_input_files(resolved_input_dir, root_path, allowed_extensions):
        relative_path = _rel(root_path, file_path)
        content_hash = _sha256_file(file_path)
        file_stat = file_path.stat()

        if _has_processed(state, relative_path, content_hash):
            skipped.append(
                {
                    "relative_path": relative_path,
                    "sha256": content_hash,
                    "reason": "already_processed",
                }
            )
            continue

        text = file_path.read_text(encoding="utf-8", errors="replace")
        matched_routes = _match_routes(text, sop.get("routes", []))
        review_reasons = _match_keywords(
            text,
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
            if _decision_record_exists(resolved_staging_dir, review_id):
                skipped.append(
                    {
                        "relative_path": relative_path,
                        "sha256": content_hash,
                        "reason": "review_decision_recorded",
                    }
                )
                _record_processed(state, relative_path, content_hash, file_stat.st_size, generated_at)
                continue
            changes.append(change)
            review_items.append(change)
            for reason in review_reasons:
                if reason not in all_review_reasons:
                    all_review_reasons.append(reason)
            # Pending-review files are NOT recorded as processed: a file that
            # trips the boundary gate keeps re-surfacing (and regenerates the
            # staging payload) every run until a human handles/removes it.
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
        "summary": {
            "scanned": len(changes) + len(skipped),
            "changed": len(changes),
            "skipped": len(skipped),
            "requires_review": bool(review_items),
        },
        "changes": changes,
        "skipped": skipped,
        "boundary_gate": boundary_gate,
    }

    delta_path = resolved_delta_dir / f"{run_id}.json"
    delta["delta_path"] = _rel(root_path, delta_path)
    _write_json(delta_path, delta)

    _record_run(state, generated_at, delta["delta_path"], delta["summary"])
    _write_json(resolved_state_path, state)

    return delta


def _resolve_under_root(root: Path, value: Path | str) -> Path:
    path = Path(value)
    candidate = (path if path.is_absolute() else root / path).resolve()
    # The repo boundary is part of the safety model: refuse to read or write
    # outside root (incl. via absolute paths or `..` traversal). Fail closed.
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes project root: {value!r} -> {candidate}")
    return candidate


def _ensure_layout(*directories: Path) -> None:
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(input_dir).parts):
            continue
        if path.suffix.lower() not in allowed_extensions:
            continue
        # is_file()/read/hash all follow symlinks, so a symlink inside the input
        # dir can point outside the repo. Skip any file whose real target escapes
        # the boundary. Fail-closed, consistent with _resolve_under_root.
        real = path.resolve()
        if real != root and root not in real.parents:
            continue
        files.append(path)
    return sorted(files)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_processed(state: dict[str, Any], relative_path: str, content_hash: str) -> bool:
    processed_file = state["processed_files"].get(relative_path)
    if processed_file and processed_file.get("sha256") == content_hash:
        return True
    return content_hash in state["processed_hashes"]


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
    delta_path: str,
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


def _match_keywords(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    matches: list[str] = []
    for keyword in keywords:
        normalized_keyword = str(keyword).lower()
        if normalized_keyword and normalized_keyword in lowered:
            matches.append(str(keyword))
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
    if not review_items:
        return {
            "status": "clear",
            "payload": None,
            "reasons": [],
        }

    index_path = staging_dir / "pending_review.json"
    index = _read_pending_review_index(index_path)
    reviews = _active_review_refs(root, staging_dir, index["reviews"])
    latest = ""
    for item in review_items:
        review_id = _review_id([item])
        if _decision_record_exists(staging_dir, review_id):
            continue
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

        review_ref = _rel(root, review_payload_path)
        reviews = [existing for existing in reviews if existing != review_ref]
        reviews.append(review_ref)
        latest = review_ref

    if not latest:
        _write_json(
            index_path,
            {
                "schema": PENDING_REVIEW_INDEX_SCHEMA,
                "generated_at": generated_at,
                "latest": reviews[-1] if reviews else "",
                "reviews": reviews,
            },
        )
        return {
            "status": "clear",
            "payload": None,
            "reasons": [],
        }

    _write_json(
        index_path,
        {
            "schema": PENDING_REVIEW_INDEX_SCHEMA,
            "generated_at": generated_at,
            "latest": latest,
            "reviews": reviews,
        },
    )

    return {
        "status": "pending_review",
        "payload": _rel(root, index_path),
        "reasons": reasons,
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _read_pending_review_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": PENDING_REVIEW_INDEX_SCHEMA,
            "generated_at": "",
            "latest": "",
            "reviews": [],
        }
    try:
        index = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {
            "schema": PENDING_REVIEW_INDEX_SCHEMA,
            "generated_at": "",
            "latest": "",
            "reviews": [],
        }
    if index.get("schema") != PENDING_REVIEW_INDEX_SCHEMA:
        return {
            "schema": PENDING_REVIEW_INDEX_SCHEMA,
            "generated_at": "",
            "latest": "",
            "reviews": [],
        }
    index.setdefault("reviews", [])
    index.setdefault("latest", "")
    return index


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


def _decision_record_exists(staging_dir: Path, review_id: str) -> bool:
    return (
        (staging_dir / "approvals" / f"{review_id}.json").exists()
        or (staging_dir / "rejections" / f"{review_id}.json").exists()
    )


def _active_review_refs(root: Path, staging_dir: Path, review_refs: list[str]) -> list[str]:
    active: list[str] = []
    for review_ref in review_refs:
        try:
            review_path = _resolve_under_root(root, review_ref)
        except ValueError:
            continue
        if not review_path.exists():
            continue
        try:
            payload = _read_json(review_path)
        except (OSError, json.JSONDecodeError):
            continue
        review_id = payload.get("review_id")
        if review_id and not _decision_record_exists(staging_dir, str(review_id)):
            active.append(review_ref)
    return active


def _rel(root: Path, path: Path) -> str:
    # Callers pass containment-checked paths (config via _resolve_under_root,
    # discovered files via _iter_input_files), so this is always under root;
    # relpath just gives a stable posix-style relative string.
    return Path(os.path.relpath(path, root)).as_posix()


def _run_id(generated_at: str) -> str:
    return "phase1-delta-" + "".join(character for character in generated_at if character.isalnum())


def _unique_run_id(delta_dir: Path, base: str) -> str:
    run_id = base
    suffix = 2
    while (delta_dir / f"{run_id}.json").exists():
        run_id = f"{base}-{suffix}"
        suffix += 1
    return run_id


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Phase 1 local ingestion pass.")
    parser.add_argument("--root", default=".", help="Project root. Defaults to the current directory.")
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

    delta = run_phase1(
        args.root,
        input_dir=args.input_dir,
        sop_path=args.sop,
        state_path=args.state,
        delta_dir=args.delta_dir,
        staging_dir=args.staging_dir,
    )

    print(f"delta: {delta['delta_path']}")
    print(
        "summary: "
        f"{delta['summary']['changed']} changed, "
        f"{delta['summary']['skipped']} skipped, "
        f"review={delta['boundary_gate']['status']}"
    )
    if delta["boundary_gate"]["status"] == "pending_review":
        print("\aBOUNDARY GATE: review required before outward action.")
        print(f"payload: {delta['boundary_gate']['payload']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
