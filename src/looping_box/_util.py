from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PENDING_REVIEW_INDEX_SCHEMA = "looping-box.pending-review-index.v1"


def resolve_under_root(root: Path, value: Path | str) -> Path:
    path = Path(value)
    candidate = (path if path.is_absolute() else root / path).resolve()
    # The repo boundary is part of the safety model: refuse to read or write
    # outside root (incl. via absolute paths or `..` traversal). Fail closed.
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes project root: {value!r} -> {candidate}")
    return candidate


def rel(root: Path, path: Path) -> str:
    return Path(os.path.relpath(path, root)).as_posix()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_pending_review_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_pending_review_index()
    try:
        index = read_json(path)
    except (OSError, json.JSONDecodeError):
        return _empty_pending_review_index()
    if index.get("schema") != PENDING_REVIEW_INDEX_SCHEMA:
        return _empty_pending_review_index()
    index.setdefault("reviews", [])
    index.setdefault("latest", "")
    return index


def decision_record_exists(staging_dir: Path, review_id: str) -> bool:
    return (
        (staging_dir / "approvals" / f"{review_id}.json").exists()
        or (staging_dir / "rejections" / f"{review_id}.json").exists()
    )


def _empty_pending_review_index() -> dict[str, Any]:
    return {
        "schema": PENDING_REVIEW_INDEX_SCHEMA,
        "generated_at": "",
        "latest": "",
        "reviews": [],
    }
