"""Read-only health check: says what is wrong and the matching remedy. Changes nothing."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import model
from ._util import default_root, key_status, parse_time, read_json, utc_now
from .action_policy import _read_action_config
from .review import list_reviews, verify_audit_logs
from .supervisor import DEFAULT_CONFIG, load_world_state

Finding = tuple[str, str, str]  # (level: ok|warn|fail, message, remedy)

_STATE_WARN_HASHES = 5000


def run_doctor(root: Path | str) -> list[Finding]:
    root_path = Path(root).resolve()
    findings: list[Finding] = []

    def ok(message: str) -> None:
        findings.append(("ok", message, ""))

    def warn(message: str, remedy: str) -> None:
        findings.append(("warn", message, remedy))

    def fail(message: str, remedy: str) -> None:
        findings.append(("fail", message, remedy))

    # --- config
    keywords: list[str] = []
    try:
        sop = read_json(root_path / "config" / "sops" / "phase1_ingestion.json")
        keywords = list(sop.get("boundary_gate", {}).get("requires_review_keywords", []))
        if keywords:
            ok(f"SOP readable ({len(keywords)} gate keywords)")
        else:
            warn("SOP has no gate keywords: nothing will be held for review", "add keywords to the SOP")
    except (OSError, ValueError) as exc:
        fail(f"SOP unreadable: {exc}", "looping-box init (creates missing config, never overwrites)")
    try:
        classes = _read_action_config(root_path)["classes"]
        listed = {str(term).lower() for terms in classes.values() for term in terms}
        missing = [word for word in keywords if str(word).lower() not in listed]
        if missing:
            warn(
                f"gate keywords with no explicit action class: {missing}",
                "list them in config/action_classes.json",
            )
        else:
            ok("every gate keyword has an explicit action class")
    except (OSError, ValueError) as exc:
        fail(f"action classes unreadable: {exc}", "looping-box init (creates missing config, never overwrites)")

    stale_seconds = DEFAULT_CONFIG["stale_lock_seconds"]
    loop_config = root_path / "config" / "super_loop.json"
    try:
        if loop_config.exists():
            stale_seconds = int(read_json(loop_config).get("stale_lock_seconds", stale_seconds))
            ok("super_loop.json readable")
    except (OSError, ValueError) as exc:
        fail(f"super_loop.json unreadable: {exc}", "fix or restore config/super_loop.json")

    # --- runtime state
    lock = root_path / ".looping_box.lock"
    if lock.exists():
        try:
            lock_data = read_json(lock)
            age = (parse_time(utc_now()) - parse_time(lock_data["created_at"])).total_seconds()
            if age > stale_seconds:
                warn(
                    f"stale lock ({int(age)}s old, pid {lock_data.get('pid')})",
                    "nothing running? delete .looping_box.lock (it also self-recovers on the next run)",
                )
            else:
                ok(f"lock held by pid {lock_data.get('pid')} for {int(age)}s (a run is in progress)")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            warn("lock file unreadable", "delete .looping_box.lock if nothing is running")
    else:
        ok("no lock held")

    try:
        state = load_world_state(root_path)
        recovery = state["recovery"]
        if recovery.get("operator_action_required"):
            warn(
                f"operator action required ({recovery.get('blocked_reason') or 'review'})",
                "run: looping-box status",
            )
        elif recovery.get("last_error"):
            warn(f"last run failed: {recovery['last_error']}", "fix the cause, then: looping-box run")
        else:
            ok("supervisor state clear")
    except (OSError, ValueError) as exc:
        fail(f"world state unreadable: {exc}", "inspect .world_state.json (unknown schema fails closed)")

    pending = list_reviews(root_path)
    if pending:
        unverifiable = sum(1 for review in pending if review.get("unverifiable_decision"))
        warn(
            f"{len(pending)} pending review(s)" + (f", {unverifiable} with an unverifiable decision" if unverifiable else ""),
            "looping-box review list" + ("; looping-box review key" if unverifiable else ""),
        )
    else:
        ok("no pending reviews")

    bad = sorted((root_path / "cache" / "deltas").glob("*.bad*"))
    if bad:
        warn(f"{len(bad)} quarantined malformed delta(s)", "inspect cache/deltas/*.bad*, then archive or delete")

    state_file = root_path / "cache" / "state" / "phase1_state.json"
    try:
        if state_file.exists():
            hashes = len(read_json(state_file).get("processed_hashes", {}))
            if hashes > _STATE_WARN_HASHES:
                warn(f"phase 1 state tracks {hashes} hashes", "state compaction is on the roadmap; archive if slow")
    except (OSError, ValueError):
        fail("phase 1 state unreadable", "inspect cache/state/phase1_state.json")

    # --- security
    key_line = key_status()
    if any(word in key_line for word in ("refused", "unreadable")):
        warn(key_line, "fix the key file permissions or set LOOPING_BOX_REVIEW_KEY")
    else:
        ok(key_line)

    env_file = root_path / ".env"
    if env_file.exists() and os.name == "posix" and env_file.stat().st_mode & 0o077:
        warn(".env is readable by others", "chmod 600 .env")
    enabled = [role for role in ("context_builder", "execution_engine", "verifier") if model.is_enabled(role, root=root_path)]
    ok(f"model roles enabled: {', '.join(enabled)}" if enabled else "model roles: none (fully offline)")

    audit = verify_audit_logs(root_path)
    broken = {name: line for name, line in audit.items() if line is not None}
    if broken:
        fail(f"audit chain broken: {broken}", "preserve the logs for review; do not edit them")
    else:
        ok(f"audit logs intact ({len(audit)} file(s))")

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Looping Box health check.")
    parser.add_argument(
        "--root",
        default=default_root(),
        help="Project root. Defaults to $LOOPING_BOX_ROOT, else the current directory.",
    )
    args = parser.parse_args()
    findings = run_doctor(args.root)
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
