from __future__ import annotations

import contextlib
import getpass
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import locking
from .validation import ValidationError, validate_data
from .paths import UnsafePathError, contained_path, directory_path, file_path, read_validated, read_validated_snapshot

PENDING_REVIEW_INDEX_SCHEMA = "looping-box.pending-review-index.v1"
AUDIT_EVENT_SCHEMA = "looping-box.audit-event.v1"
DEFAULT_STALE_LOCK_SECONDS = 300


def default_root() -> str:
    """Workspace root for the CLIs: $LOOPING_BOX_ROOT, else the current directory."""
    return os.environ.get("LOOPING_BOX_ROOT", ".")


def resolve_under_root(root: Path, value: Path | str) -> Path:
    return contained_path(root, value)


def rel(root: Path, path: Path) -> str:
    return Path(os.path.relpath(path, root)).as_posix()


def retained_phase1_delta_dirs(state: dict[str, Any]) -> set[str]:
    """Collect alternate queues from the registry and older Phase 1 states."""
    directories = set(state.get("custom_delta_dirs", []))
    references = [state.get("last_delta_path"), *(run["delta_path"] for run in state.get("runs", []))]
    for reference in references:
        if reference is not None:
            parent = Path(reference).parent.as_posix()
            if parent == ".":
                raise ValueError(f"{reference}: Phase 1 delta must be inside a workspace queue directory")
            directories.add(parent)
    directories.discard("cache/deltas")
    return directories


def read_json(path: Path) -> dict[str, Any]:
    path = file_path(path)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_bytes(path: Path, data: bytes) -> None:
    """Replace one file atomically; retain the prior file on any pre-rename failure."""
    path = file_path(path)
    # Keep rejecting unsafe remnants from versions that used a predictable name.
    file_path(path.with_name(f"{path.name}.{os.getpid()}.tmp"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f"{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temp_path.replace(path)


def write_text(path: Path, text: str) -> None:
    write_bytes(path, text.encode("utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def sha256_file(path: Path) -> str:
    path = file_path(path)
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
    path = file_path(path)
    if not path.exists():
        return _empty_pending_review_index()
    return read_validated(path, "pending_review_index")


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
    # The operator-selected key base is an intentional external trust anchor.
    return file_path(Path(base).resolve() / "looping-box" / "review.key")


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


def review_identity(items: list[dict[str, Any]]) -> str:
    """Preserve the original review-ID serialization exactly."""
    basis = json.dumps({"items": [
        {"relative_path": item.get("relative_path"), "sha256": item.get("sha256"),
         "review_reasons": item.get("review_reasons", [])} for item in items
    ]}, sort_keys=True)
    return "review-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def review_evidence_digest(payload: dict[str, Any]) -> str:
    fields = ("action_class", "risk_reasons", "source_items", "source_sop")
    evidence = {field: payload[field] for field in fields if field in payload}
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode("utf-8")).hexdigest()


def read_review_payload(path: Path) -> dict[str, Any]:
    return read_review_payload_snapshot(path)[0]


def read_review_payload_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload, raw = read_validated_snapshot(path, "review_payload")
        item = payload["source_items"][0]
        if (payload["review_id"] != review_identity(payload["source_items"])
                or path.name != payload["review_id"] + ".json"
                or not item["review_reasons"] or payload["risk_reasons"] != item["review_reasons"]):
            raise ValueError("identity or risk reasons do not agree")
        stamp = payload.get("source_evidence_sha256")
        if stamp is not None and not hmac.compare_digest(stamp, review_evidence_digest(payload)):
            raise ValueError("staged source evidence was edited")
        return payload, hashlib.sha256(raw).hexdigest()
    except UnsafePathError as exc:
        raise UnsafePathError(path, f"review_evidence_invalid: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise ValueError(f"review_evidence_invalid: {path}: {exc}") from exc


def decision_state(staging_dir: Path, review_id: str) -> tuple[str | None, bool]:
    """Return (decision, unverifiable).

    decision is 'approved' / 'rejected' for a verified record, else None.
    unverifiable is True when a record file exists but did not verify (no usable
    key, edited, forged, or signed with another key) so callers can say why the
    gate is still closed. Rejections are checked first so a conflict fails safe.
    """
    directory_path(staging_dir)
    # Validate the ID before it becomes any part of a filename.
    from .validation import REVIEW_ID
    from .schema import validate
    validate(review_id, REVIEW_ID, "$.review_id")
    key = review_key()
    present = False
    for directory, decision in (("rejections", "rejected"), ("approvals", "approved")):
        path = file_path(staging_dir / directory / f"{review_id}.json")
        if not path.exists():
            continue
        present = True
        if key is None:
            continue
        try:
            record = read_validated(path, "review_record")
        except (OSError, ValidationError):
            continue
        signature = record.get("signature") if isinstance(record, dict) else None
        if (
            isinstance(signature, str)
            and record.get("review_id") == review_id
            and record.get("decision") == decision
            and hmac.compare_digest(signature, sign_record(record, key))
        ):
            payload_path = file_path(staging_dir / "reviews" / f"{review_id}.json")
            try:
                payload, payload_sha256 = read_review_payload_snapshot(payload_path)
                if not hmac.compare_digest(record["payload_sha256"], payload_sha256):
                    continue
                if decision == "approved":
                    reference = record["verifier_result"]
                    if (reference != f"cache/verifiers/{review_id}.json"
                            or payload["verifier"]["status"] != "passed"
                            or payload["verifier"]["result"] != reference):
                        continue
                    verifier, verifier_bytes = read_validated_snapshot(
                        resolve_under_root(staging_dir.parent, reference), "verifier_result"
                    )
                    verifier_digest = record.get("verifier_sha256")
                    if verifier_digest is not None and not hmac.compare_digest(
                        verifier_digest, hashlib.sha256(verifier_bytes).hexdigest()
                    ):
                        continue
                    if (verifier["review_id"] != review_id or verifier["status"] != "passed"
                            or not all(check["status"] == "passed" for check in verifier["checks"])):
                        continue
                elif record["verifier_result"] is not None:
                    continue
            except UnsafePathError:
                raise
            except (OSError, ValueError):
                continue
            return decision, False
    return None, present


def signed_rejections(
    staging_dir: Path, source_hashes: dict[str, str],
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Find active signed rejections bound to the supplied source bytes."""
    directory = directory_path(staging_dir / "rejections")
    try:
        paths = sorted(directory.iterdir())
    except FileNotFoundError:
        return
    for path in paths:
        if not (path.name.startswith("review-") and path.suffix == ".json"):
            continue
        try:
            record = read_validated(path, "review_record")
            review_id = record["review_id"]
            if path.stem != review_id or record["decision"] != "rejected":
                continue
            payload = read_review_payload(staging_dir / "reviews" / path.name)
        except UnsafePathError:
            raise
        except FileNotFoundError:
            continue
        except ValueError as exc:
            # Missing or invalid payloads cannot bind a rejection. An I/O
            # failure reading a present payload means evidence is unavailable.
            if isinstance(exc.__cause__, OSError) and not isinstance(exc.__cause__, FileNotFoundError):
                raise
            continue
        item = payload["source_items"][0]
        if (source_hashes.get(item["relative_path"]) == item["sha256"]
                and decision_state(staging_dir, review_id)[0] == "rejected"):
            yield record, payload


# --- audit log (append-only, hash-chained) -----------------------------------


@dataclass
class AuditInspection:
    events: list[dict[str, Any]]
    broken_at: int | None = None


def audit_log_paths(root: Path) -> list[Path]:
    directory = directory_path(resolve_under_root(root, "logs/transactions"))
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return []
    return [file_path(path) for path in sorted(entries) if path.name.endswith(".jsonl")]


def inspect_audit_log(path: Path) -> AuditInspection:
    """Inspect the entire log; retain valid-shaped events and the first broken line.

    A chain has no external anchor: full recomputation, suffix removal, or log
    deletion cannot be detected. Empty/missing logs provide no activity evidence.
    """
    path = file_path(path)
    try:
        lines = path.read_bytes().splitlines()
    except FileNotFoundError:
        return AuditInspection([])
    result = AuditInspection([])
    previous = ""
    for number, line in enumerate(lines, 1):
        try:
            entry = validate_data(json.loads(line.decode("utf-8")), "audit_event", f"{path}:{number}")
        except (ValueError, RecursionError):
            if result.broken_at is None:
                result.broken_at = number
            continue
        result.events.append(entry)
        body = {name: value for name, value in entry.items() if name != "hash"}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
        if entry["prev"] != previous or entry["hash"] != digest:
            if result.broken_at is None:
                result.broken_at = number
        previous = entry["hash"]
    return result


def _check_audit_inode(path: Path, metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise UnsafePathError(path, "audit log must be a regular file with exactly one link")


def audit_destination_writable(path: Path) -> bool:
    """Check the permissions and inode needed to append without opening for write."""
    path = file_path(path)
    try:
        _check_audit_inode(path, path.lstat())
    except FileNotFoundError:
        parent = path.parent
        while not parent.exists():
            parent = parent.parent
        return os.access(parent, os.W_OK | os.X_OK)
    return os.access(path, os.W_OK)


def preflight_audit(root: Path, log: str) -> AuditInspection:
    """Check the log under the caller's project lock without creating it."""
    path = resolve_under_root(root, f"logs/transactions/{log}.jsonl")
    writable = audit_destination_writable(path)
    inspected = inspect_audit_log(path)
    if inspected.broken_at is not None:
        raise ValidationError(str(path), f"line {inspected.broken_at}: broken audit chain", code="audit_invalid")
    if not writable:
        raise PermissionError(f"audit_unwritable: {path}: check audit log and directory permissions")
    return inspected


_REVIEW_AUDIT_FIELDS = ("generated_at", "review_id", "approver", "interactive", "note", "payload_sha256")


def review_audit_event(record: dict[str, Any]) -> dict[str, Any]:
    event = {"event": f"review.{record['decision']}", **{name: record[name] for name in _REVIEW_AUDIT_FIELDS}}
    if record.get("audit_binds_record") is True:
        signed_record = json.dumps(record, sort_keys=True).encode("utf-8")
        event["review_record_sha256"] = hashlib.sha256(signed_record).hexdigest()
    return event


def missing_review_audit_events(root: Path, inspection: AuditInspection | None) -> list[str]:
    """Identify retained decision records that expect a missing review event."""
    if inspection is not None and inspection.broken_at is not None:
        return []  # The chain failure already identifies unavailable audit evidence.
    fields = ("event", *_REVIEW_AUDIT_FIELDS, "review_record_sha256")
    events = inspection.events if inspection is not None else ()
    logged = {tuple(event.get(name) for name in fields) for event in events
              if event["event"] in ("review.approved", "review.rejected")}
    missing = {}
    for folder, suffix, decision in (
        ("approvals", ".json", "approved"),
        ("rejections", ".json", "rejected"),
        ("archive", ".approval.json", "approved"),
        ("archive", ".rejection.json", "rejected"),
    ):
        directory = directory_path(resolve_under_root(root, f"staging/{folder}"))
        try:
            paths = sorted(directory.iterdir())
        except FileNotFoundError:
            continue
        for path in paths:
            if not path.name.endswith(suffix):
                continue
            try:
                record, raw = read_validated_snapshot(path, "review_record")
            except ValidationError:
                continue  # Historical archives may contain malformed decision evidence.
            if record.get("audit_expected") is not True or record["decision"] != decision:
                continue
            name = record["review_id"]
            if folder == "archive":
                name += "-" + hashlib.sha256(raw).hexdigest()
            if path.name != name + suffix:
                continue
            event = review_audit_event(record)
            event_key = tuple(event.get(name) for name in fields)
            if event_key not in logged:
                missing.setdefault(event_key, rel(root, path))
    return sorted(missing.values())


def append_audit(root: Path, log: str, event: dict[str, Any]) -> None:
    """Validate the entire existing chain, then append/flush/fsync under the lock."""
    root = Path(root).resolve()
    with project_lock(root, event.get("generated_at", utc_now())):
        inspected = preflight_audit(root, log)
        path = file_path(resolve_under_root(root, f"logs/transactions/{log}.jsonl"))
        previous = inspected.events[-1]["hash"] if inspected.events else ""
        body = {"schema": AUDIT_EVENT_SCHEMA, **event, "prev": previous}
        body.pop("hash", None)
        body["hash"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
        validate_data(body, "audit_event", str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        # A valid last JSON record need not already have a terminating newline.
        separator = "\n" if path.exists() and path.stat().st_size and not path.read_bytes().endswith(b"\n") else ""
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(path, flags, 0o666)
        try:
            _check_audit_inode(path, os.fstat(fd))
            with os.fdopen(fd, "a", encoding="utf-8", closefd=False) as handle:
                handle.write(separator + json.dumps(body, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(fd)
        finally:
            os.close(fd)


def verify_audit_chain(path: Path) -> int | None:
    """Return None for intact/no evidence, else the first invalid one-based line."""
    return inspect_audit_log(path).broken_at


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
    # Deprecated compatibility argument: timestamps never confer ownership.
    locking.acquire(lock_path, generated_at)


def release_lock(lock_path: Path, expected_pid: int) -> None:
    locking.release(lock_path, expected_pid)
