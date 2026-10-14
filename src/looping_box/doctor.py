"""Read-only health check: says what is wrong and the matching remedy. Changes nothing."""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import model
from ._util import audit_log_paths, default_root, inspect_audit_log, key_status, review_key
from .locking import is_held
from .action_policy import _read_action_config
from .review import list_reviews
from .supervisor import _read_config, load_world_state
from .validation import validate_policy
from .paths import UnsafePathError, directory_path, file_path, read_validated

Finding = tuple[str, str, str]  # (level: ok|warn|fail, message, remedy)

_STATE_WARN_HASHES = 5000
_ARCHIVE_WARN_DELTAS = 1000


def _files(directory: Path, pattern: str) -> list[Path]:
    directory = directory_path(directory)
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return []
    return [file_path(path) for path in sorted(entries) if fnmatch.fnmatchcase(path.name, pattern)]


def run_doctor(root: Path | str) -> list[Finding]:
    root_path = Path(root).resolve()
    findings: list[Finding] = []

    def ok(message):
        findings.append(("ok", message, ""))

    def warn(message, remedy):
        findings.append(("warn", message, remedy))

    def fail(message, remedy):
        findings.append(("fail", message, remedy))

    sop = None
    try:
        sop = read_validated(root_path / "config/sops/phase1_ingestion.json", "phase1_ingestion")
        keywords = sop["boundary_gate"]["requires_review_keywords"]
        if keywords:
            ok(f"SOP readable ({len(keywords)} gate keywords)")
        else:
            warn("SOP has no gate keywords: nothing will be held for review", "add keywords to the SOP")
        classes = _read_action_config(root_path)
        validate_policy(sop, classes, "config/sops/phase1_ingestion.json")
        ok("action configuration/defaults available; every gate keyword has an explicit action class")
    except UnsafePathError:
        raise
    except (OSError, ValueError) as exc:
        fail(f"SOP/action configuration unreadable: {exc}", "preserve and inspect/restore config; init supplies missing defaults")
    try:
        _read_config(root_path)
        ok("super_loop configuration/defaults available; stale_lock_seconds is deprecated (kernel owns the lock)")
    except UnsafePathError:
        raise
    except (OSError, ValueError) as exc:
        fail(f"super_loop.json unreadable: {exc}", "preserve and inspect/restore config/super_loop.json")
    try:
        ok("kernel lock held (a run is in progress)" if is_held(root_path / ".looping_box.lock") else "no lock held")
    except UnsafePathError:
        raise
    except (OSError, ValueError) as exc:
        fail(f"lock ownership unavailable: {exc}", "inspect permissions; retain the lock file; wait for its owner")
    try:
        state = load_world_state(root_path)
        recovery = state["recovery"]
        if recovery["last_error"]:
            warn(f"last run failed: {recovery['last_error']}", "run: looping-box status")
        elif recovery["operator_action_required"]:
            warn(f"operator action required ({recovery['blocked_reason'] or 'review'})", "run: looping-box status")
        else:
            ok("supervisor state readable" if state.get("last_run_at") else "supervisor never run: no run evidence")
        for worker_id, artifact, kind in (("context_builder", "context_package.json", "context_package"),
                                          ("execution_engine", "draft.json", "execution_draft")):
            path = file_path(root_path / "cache/workers" / worker_id / artifact)
            if path.exists():
                read_validated(path, kind)
        block = file_path(root_path / "cache/supervisor/blocked.json")
        if block.exists():
            read_validated(block, "supervisor_blocked")
    except UnsafePathError:
        raise
    except (OSError, ValueError) as exc:
        fail(f"world/recovery state unavailable: {exc}", "preserve and inspect/restore evidence; use a compatible schema version")
    try:
        pending = list_reviews(root_path)
        if pending:
            unverifiable = sum(bool(review.get("unverifiable_decision")) for review in pending)
            warn(f"{len(pending)} pending review(s), {unverifiable} unverifiable decision(s)",
                 "looping-box review list; inspect/decide or deliberately edit/remove source then ingest")
        else:
            ok("no pending reviews")
    except UnsafePathError:
        raise
    except (ValueError, OSError) as exc:
        fail(f"pending review evidence unavailable: {exc}", "preserve and inspect/restore staging/pending_review.json and payloads")
    decisions = []
    try:
        for directory, kind in (("staging/approvals", "review_record"), ("staging/rejections", "review_record"),
                                ("cache/verifiers", "verifier_result")):
            for path in _files(root_path / directory, "*.json"):
                read_validated(path, kind)
                if kind == "review_record":
                    decisions.append(path)
        bad = _files(root_path / "cache/deltas", "*.bad*")
        if bad:
            warn(f"{len(bad)} quarantined malformed delta(s)", "inspect and preserve cache/deltas/*.bad* before deliberate cleanup")
        for path in _files(root_path / "cache/deltas", "*.json"):
            read_validated(path, "phase1_delta")
        archived = len(_files(root_path / "cache/deltas/archive", "*.json"))
        if archived > _ARCHIVE_WARN_DELTAS:
            warn(f"{archived} archived deltas", "inspect growth and deliberately archive elsewhere; retention is operator-managed")
        state_file = file_path(root_path / "cache/state/phase1_state.json")
        if state_file.exists():
            hashes = len(read_validated(state_file, "phase1_state")["processed_hashes"])
            if hashes > _STATE_WARN_HASHES:
                warn(f"phase 1 state tracks {hashes} hashes", "inspect growth; no automatic state compaction or total disk cap")
    except UnsafePathError:
        raise
    except (OSError, ValueError) as exc:
        fail(f"artifact evidence unavailable: {exc}", "preserve and inspect/restore the named artifact")
    try:
        key_line = key_status()
        if review_key(create=False) is None:
            if decisions:
                fail(key_line, "restore the existing decision key; do not replace it to bypass verification")
            else:
                warn(key_line, "no usable key yet; a private key is created on the first decision")
        else:
            ok(key_line)
    except UnsafePathError:
        raise
    except (OSError, ValueError) as exc:
        fail(f"review key unavailable: {exc}", "inspect key location and private permissions")
    try:
        env_file = file_path(root_path / ".env")
        if env_file.exists() and os.name == "posix" and env_file.stat().st_mode & 0o077:
            warn(".env has group/other permissions", "chmod 600 .env")
        settings = model.inspect_settings(root_path)
        enabled = [role for role in ("context_builder", "execution_engine", "verifier")
                   if settings.get("OPENROUTER_API_KEY") and settings.get("MODEL_" + role.upper())]
        ok(f"model roles enabled: {', '.join(enabled)}" if enabled else "model roles: none (fully offline)")
        endpoint = urlsplit(settings.get("OPENROUTER_BASE_URL", model.DEFAULT_BASE_URL))
        _ = endpoint.port  # Validate an explicit port without contacting the endpoint.
        if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
            fail("configured endpoint is not an HTTP(S) URL", "fix OPENROUTER_BASE_URL; doctor never contacts it")
        elif endpoint.scheme != "https":
            warn("configured HTTP endpoint is not HTTPS", "use HTTPS for provider traffic; endpoint was not contacted")
        else:
            ok("HTTPS model endpoint configured (not contacted)")
    except UnsafePathError:
        raise
    except (OSError, ValueError) as exc:
        fail(f"model settings unavailable: {exc}", "inspect .env and endpoint configuration")
    try:
        logs = {path.name: inspect_audit_log(path) for path in audit_log_paths(root_path)}
        broken = {name: log.broken_at for name, log in logs.items() if log.broken_at is not None}
        if broken:
            fail(f"audit chain broken: {broken}", "preserve and inspect/restore the logs; do not silently rewrite history")
        elif not any(log.events for log in logs.values()):
            ok("audit: no activity evidence (logs absent or empty)")
        else:
            ok(f"audit logs intact ({len(logs)} file(s))")
    except UnsafePathError:
        raise
    except (OSError, ValueError) as exc:
        fail(f"audit evidence unavailable: {exc}", "preserve and inspect permissions and logs")
    ok("accepted limits: keyword gating; unanchored audit chains; post-run resource checks; operator-managed retention; no cross-file transaction")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Looping Box health check.")
    parser.add_argument(
        "--root",
        default=default_root(),
        help="Project root. Defaults to $LOOPING_BOX_ROOT, else the current directory.",
    )
    args = parser.parse_args()
    try:
        args.root = Path(args.root).expanduser().resolve()
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: invalid_root: {args.root}: {exc}", file=sys.stderr)
        return 1
    try:
        findings = run_doctor(args.root)
    except (ValueError, OSError) as exc:
        print(f"error: doctor_error: {exc}", file=sys.stderr)
        return 1
    for level, message, remedy in findings:
        print(f"[{level.upper():4}] {message}")
        if remedy:
            print(f"       fix: {remedy}")
    failed = sum(1 for level, _, _ in findings if level == "fail")
    warned = sum(1 for level, _, _ in findings if level == "warn")
    print(f"{failed} failed, {warned} warning(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
