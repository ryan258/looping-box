from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATE_SCHEMA = "looping-box.phase1.state.v1"
DELTA_SCHEMA = "looping-box.phase1.delta.v1"
BOUNDARY_SCHEMA = "looping-box.boundary-review.v1"


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

    for file_path in _iter_input_files(resolved_input_dir, allowed_extensions):
        relative_path = file_path.relative_to(root_path).as_posix()
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
            "mtime_ns": file_stat.st_mtime_ns,
            "matched_routes": matched_routes,
            "review_reasons": review_reasons,
            "excerpt": _excerpt(text, int(sop.get("max_excerpt_chars", 500))),
        }
        changes.append(change)

        if review_reasons:
            review_items.append(change)
            for reason in review_reasons:
                if reason not in all_review_reasons:
                    all_review_reasons.append(reason)

        _record_processed(state, relative_path, content_hash, file_stat.st_size, generated_at)

    boundary_gate = _build_boundary_gate(
        root_path,
        resolved_staging_dir,
        sop,
        generated_at,
        all_review_reasons,
        review_items,
    )

    run_id = _run_id(generated_at)
    delta = {
        "schema": DELTA_SCHEMA,
        "run_id": run_id,
        "generated_at": generated_at,
        "sop": {
            "path": resolved_sop_path.relative_to(root_path).as_posix(),
            "sha256": _sha256_file(resolved_sop_path),
            "name": sop.get("name", "unnamed"),
        },
        "inputs": {
            "root": str(root_path),
            "input_dir": resolved_input_dir.relative_to(root_path).as_posix(),
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
    delta["delta_path"] = delta_path.relative_to(root_path).as_posix()
    _write_json(delta_path, delta)

    _record_run(state, generated_at, delta["delta_path"], delta["summary"])
    _write_json(resolved_state_path, state)

    return delta


def _resolve_under_root(root: Path, value: Path | str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


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


def _iter_input_files(input_dir: Path, allowed_extensions: set[str]) -> list[Path]:
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
        if _match_keywords(text, route.get("keywords", [])):
            matches.append(str(route["label"]))
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

    payload = {
        "schema": BOUNDARY_SCHEMA,
        "generated_at": generated_at,
        "notification_message": sop.get("boundary_gate", {}).get(
            "notification_message",
            "Boundary gate review required",
        ),
        "reasons": reasons,
        "items": review_items,
    }
    payload_path = staging_dir / "pending_review.json"
    _write_json(payload_path, payload)

    return {
        "status": "pending_review",
        "payload": payload_path.relative_to(root).as_posix(),
        "reasons": reasons,
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _run_id(generated_at: str) -> str:
    return "phase1-delta-" + "".join(character for character in generated_at if character.isalnum())


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
