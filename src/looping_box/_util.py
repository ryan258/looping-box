from __future__ import annotations

import contextlib
import getpass
import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PENDING_REVIEW_INDEX_SCHEMA = "looping-box.pending-review-index.v1"
AUDIT_EVENT_SCHEMA = "looping-box.audit-event.v1"
DEFAULT_STALE_LOCK_SECONDS = 300


def default_root() -> str:
    """Workspace root for the CLIs: $LOOPING_BOX_ROOT, else the current directory."""
    return os.environ.get("LOOPING_BOX_ROOT", ".")


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
    # Per-process temp name so concurrent writers never share a temp file.
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        handle.flush()
        # ponytail: fsyncs the file, not the directory entry; add a dir fsync
        # if power-loss durability of the rename itself ever matters.
        os.fsync(handle.fileno())
    temp_path.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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


def _empty_pending_review_index() -> dict[str, Any]:
    return {
        "schema": PENDING_REVIEW_INDEX_SCHEMA,
        "generated_at": "",
        "latest": "",
        "reviews": [],
    }


# --- signed review decisions -------------------------------------------------
#
# A decision record only counts if its HMAC verifies. This stops a hand-written
# or edited file from clearing the gate. It is NOT a defence against a process
# that can read the key (same user): keep the key out of the workspace.


def _key_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "looping-box" / "review.key"


def _loose_permissions(path: Path) -> bool:
    return os.name == "posix" and bool(path.stat().st_mode & 0o077)


def review_key(*, create: bool = False) -> bytes | None:
    """$LOOPING_BOX_REVIEW_KEY, else ~/.config/looping-box/review.key (0600).

    A key file readable by group/other is refused (returns None): decisions then
    fail closed instead of trusting a key anyone could have read.
    """
    from_env = os.environ.get("LOOPING_BOX_REVIEW_KEY")
    if from_env:
        return from_env.encode("utf-8")
    path = _key_file()
    if not path.exists() and create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(secrets.token_hex(32).encode("ascii"))
    try:
        if _loose_permissions(path):
            return None
        return path.read_bytes().strip() or None
    except OSError:
        return None


def key_status() -> str:
    """One human line saying where the signing key comes from and whether it is usable."""
    if os.environ.get("LOOPING_BOX_REVIEW_KEY"):
        return "key: $LOOPING_BOX_REVIEW_KEY (environment)"
    path = _key_file()
    if not path.exists():
        return f"key: none yet ({path}); created on the first decision"
    try:
        if _loose_permissions(path):
            return f"key: {path} is readable by others and is refused; run: chmod 600 {path}"
        if not path.read_bytes().strip():
            return f"key: {path} is empty and is refused"
    except OSError as exc:
        return f"key: {path} unreadable ({exc})"
    return f"key: {path} (ok)"


def default_approver() -> str:
    explicit = os.environ.get("LOOPING_BOX_APPROVER")
    if explicit:
        return explicit
    try:
        return getpass.getuser()
    except Exception:  # no passwd entry / no env in some containers
        return "unknown"


def sign_record(record: dict[str, Any], key: bytes) -> str:
    body = {name: value for name, value in record.items() if name != "signature"}
    return hmac.new(key, json.dumps(body, sort_keys=True).encode("utf-8"), hashlib.sha256).hexdigest()


def decision_state(staging_dir: Path, review_id: str) -> tuple[str | None, bool]:
    """Return (decision, unverifiable).

    decision is 'approved' / 'rejected' for a verified record, else None.
    unverifiable is True when a record file exists but did not verify (no usable
    key, edited, forged, or signed with another key) so callers can say why the
    gate is still closed. Rejections are checked first so a conflict fails safe.
    """
    key = review_key()
    present = False
    for directory, decision in (("rejections", "rejected"), ("approvals", "approved")):
        path = staging_dir / directory / f"{review_id}.json"
        if not path.exists():
            continue
        present = True
        if key is None:
            continue
        try:
            record = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        signature = record.get("signature") if isinstance(record, dict) else None
        if (
            isinstance(signature, str)
            and record.get("review_id") == review_id
            and record.get("decision") == decision
            and hmac.compare_digest(signature, sign_record(record, key))
        ):
            return decision, False
    return None, present


def decision_for(staging_dir: Path, review_id: str) -> str | None:
    return decision_state(staging_dir, review_id)[0]


def decision_record_exists(staging_dir: Path, review_id: str) -> bool:
    return decision_for(staging_dir, review_id) is not None


# --- audit log (append-only, hash-chained) -----------------------------------


def append_audit(root: Path, log: str, event: dict[str, Any]) -> None:
    """Append one event to logs/transactions/<log>.jsonl, chained to the last one.

    Each line carries `prev` (the previous line's `hash`) and its own `hash`, so
    editing or deleting a middle line is detectable via verify_audit_chain.
    """
    path = resolve_under_root(root, f"logs/transactions/{log}.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = ""
    if path.exists():
        # ponytail: reads the whole log to find the tail; seek from the end if
        # logs ever get large.
        lines = path.read_text(encoding="utf-8").splitlines()
        if lines:
            with contextlib.suppress(ValueError, AttributeError):
                prev = json.loads(lines[-1]).get("hash", "")
    body = {"schema": AUDIT_EVENT_SCHEMA, **event, "prev": prev}
    body["hash"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(body, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def verify_audit_chain(path: Path) -> int | None:
    """Return None if the chain is intact, else the 1-based line of the first break.

    Lines written before chaining existed have no hash and report as a break.
    """
    prev = ""
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            entry = json.loads(line)
        except ValueError:
            return number
        if not isinstance(entry, dict):
            return number
        claimed = entry.pop("hash", None)
        recomputed = hashlib.sha256(json.dumps(entry, sort_keys=True).encode("utf-8")).hexdigest()
        if entry.get("prev") != prev or claimed != recomputed:
            return number
        prev = claimed
    return None


# --- project lock (shared by phase1, supervisor, and review) -----------------


@contextlib.contextmanager
def project_lock(
    root: Path, generated_at: str, stale_lock_seconds: int = DEFAULT_STALE_LOCK_SECONDS
) -> Iterator[None]:
    lock_path = resolve_under_root(root, ".looping_box.lock")
    acquire_lock(lock_path, generated_at, stale_lock_seconds)
    try:
        yield
    finally:
        release_lock(lock_path, os.getpid())


def acquire_lock(lock_path: Path, generated_at: str, stale_lock_seconds: int) -> None:
    try:
        _write_lock_exclusive(lock_path, generated_at)
        return
    except FileExistsError:
        pass

    try:
        lock = read_json(lock_path)
        age = (parse_time(generated_at) - parse_time(lock["created_at"])).total_seconds()
    except FileNotFoundError:
        age = stale_lock_seconds + 1  # released between our two attempts
    except (KeyError, ValueError, TypeError, AttributeError):
        # Unreadable lock: age it by mtime so a corrupt file cannot wedge the loop.
        try:
            age = time.time() - lock_path.stat().st_mtime
        except FileNotFoundError:
            age = stale_lock_seconds + 1
    if age <= stale_lock_seconds:
        raise RuntimeError(f"active supervisor lock: {lock_path}")

    lock_path.unlink(missing_ok=True)
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


def release_lock(lock_path: Path, expected_pid: int) -> None:
    try:
        lock = read_json(lock_path)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return
    if isinstance(lock, dict) and lock.get("pid") == expected_pid:
        lock_path.unlink(missing_ok=True)
