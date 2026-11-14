"""Read-only activity report with structured evidence status and safe Markdown rendering."""
from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ._util import audit_log_paths, default_root, inspect_audit_log, missing_review_audit_events, resolve_under_root, utc_now
from .review import list_reviews
from .validation import ValidationError, validate_data


@dataclass
class ReportResult:
    text: str
    failed: bool


def _since_prefix(value: str) -> str:
    formats = {4: (r"[0-9]{4}", "%Y"), 7: (r"[0-9]{4}-[0-9]{2}", "%Y-%m"),
               10: (r"[0-9]{4}-[0-9]{2}-[0-9]{2}", "%Y-%m-%d"),
               20: (r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", "%Y-%m-%dT%H:%M:%SZ")}
    if value == "":
        return value
    pattern, fmt = formats.get(len(value), ("(?!)", ""))
    try:
        if re.fullmatch(pattern, value) is None:
            raise ValueError
        datetime.strptime(value, fmt)
    except ValueError as exc:
        raise ValueError("since must be a valid YYYY, YYYY-MM, YYYY-MM-DD, or YYYY-MM-DDTHH:MM:SSZ") from exc
    return value


def _events(root: Path, log: str, since: str) -> list[dict[str, Any]]:
    since = _since_prefix(since)
    path = resolve_under_root(root, f"logs/transactions/{log}.jsonl")
    if not path.exists():
        return []
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except (ValueError, RecursionError) as exc:
            raise ValidationError(f"{path}:{number}", "$: invalid JSON") from exc
        event = validate_data(event, "audit_event", f"{path}:{number}")
        if event["generated_at"] >= since:
            events.append(event)
    return events


def _cell(text: Any) -> str:
    # Escape backslashes before pipes so input cannot undo the delimiter escape.
    return html.escape(str(text), quote=True).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def report_result(root: Path | str, *, since: str = "") -> ReportResult:
    root_path = Path(root).resolve()
    since = _since_prefix(since)
    logs = {}
    errors = {}
    try:
        paths = audit_log_paths(root_path)
    except (OSError, ValueError) as exc:
        paths = []
        errors["audit directory"] = str(exc)
    for path in paths:
        try:
            logs[path.name] = inspect_audit_log(path)
        except (OSError, ValueError) as exc:
            errors[path.name] = str(exc)
    missing_review_events = []
    if "audit directory" not in errors and "review.jsonl" not in errors:
        try:
            missing_review_events = missing_review_audit_events(root_path, logs.get("review.jsonl"))
        except (OSError, ValueError) as exc:
            errors["review audit coverage"] = str(exc)
    try:
        pending: int | None = len(list_reviews(root_path))
        pending_error = None
    except (OSError, ValueError) as exc:
        pending = None
        pending_error = str(exc)

    def period(log):
        return [event for event in logs[log].events if event["generated_at"] >= since] if log in logs else []

    phase1, supervisor, decisions = (period(name + ".jsonl") for name in ("phase1", "supervisor", "review"))
    changed = sum(event.get("changed", 0) for event in phase1)
    held_runs = sum(event.get("gate") == "pending_review" for event in phase1)
    statuses = Counter(event.get("status", "?") for event in supervisor)
    status_text = ", ".join(f"{count} {_cell(name)}" for name, count in sorted(statuses.items())) or "none"

    def count_text(log, text):
        return "unavailable" if "audit directory" in errors or log + ".jsonl" in errors else text

    lines = ["# Looping Box report", "", f"Period: {'since ' + since if since else 'all time'} (generated {utc_now()})",
             "", "## Activity", "",
             "- phase 1 runs: " + count_text("phase1", f"{len(phase1)} ({changed} files changed, {held_runs} run(s) with items held)"),
             "- supervisor runs: " + count_text("supervisor", f"{len(supervisor)} ({status_text})"),
             "- decisions: " + count_text("review", str(len(decisions))),
             f"- still pending: {pending if pending is not None else 'unavailable'}", "",
             "Counts measure logged events/change entries, not unique files. Malformed events are omitted.",
             "", "## Decisions", "", "Rows summarize logged decisions; historical signed files are not re-verified here.", ""]
    if pending_error:
        lines += ["Pending evidence unavailable: " + _cell(pending_error), "Next: preserve and inspect/restore the review evidence.", ""]
    if decisions:
        lines += ["| When | Decision | Review | Approver | Interactive | Note |", "|---|---|---|---|---|---|"]
        for event in decisions:
            lines.append("| {} | {} | {} | {} | {} | {} |".format(
                _cell(event["generated_at"]), _cell(event["event"].removeprefix("review.")),
                _cell(event.get("review_id", "")), _cell(event.get("approver", "")),
                "yes" if event.get("interactive") else "no", _cell(event.get("note", ""))))
    else:
        lines.append("Decision evidence unavailable." if "review.jsonl" in errors or "audit directory" in errors else "None in this period.")
    lines += ["", "## Integrity", ""]
    if not logs and not errors:
        lines.append("No audit logs yet: no activity evidence.")
    for name, inspection in logs.items():
        status = (f"BROKEN at line {inspection.broken_at}" if inspection.broken_at is not None
                  else "intact" if inspection.events else "empty; no activity evidence")
        lines.append(f"- {_cell(name)}: {status}")
    for name, error in errors.items():
        lines.append(f"- {_cell(name)}: unavailable ({_cell(error)})")
    for reference in missing_review_events:
        lines.append(f"- audit_incomplete: missing review event for {_cell(reference)}")
    lines += ["", "Integrity covers full logs regardless of the period filter. Hash chains cannot detect complete recomputation, valid-suffix removal, or deletion of all logs."]
    audit_failed = bool(errors or missing_review_events or any(log.broken_at is not None for log in logs.values()))
    if audit_failed:
        lines.append("Next: preserve and inspect/restore audit evidence; do not rewrite history to clear the warning.")
    return ReportResult("\n".join(lines) + "\n", audit_failed or pending_error is not None)


def build_report(root: Path | str, *, since: str = "") -> str:
    """Compatibility API; callers needing an exit status use report_result."""
    for log in ("phase1", "supervisor", "review"):
        _events(Path(root).resolve(), log, since)  # Preserve strict legacy API shape errors.
    return report_result(root, since=since).text


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize audit logs and review decisions as markdown.")
    parser.add_argument("--root", default=default_root(), help="Workspace root ($LOOPING_BOX_ROOT, else cwd).")
    parser.add_argument("--since", default="", help="YYYY, YYYY-MM, YYYY-MM-DD, or full UTC timestamp.")
    args = parser.parse_args()
    try:
        _since_prefix(args.since)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        args.root = Path(args.root).expanduser().resolve()
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: invalid_root: {args.root}: {exc}")
        return 1
    try:
        result = report_result(args.root, since=args.since)
    except (ValueError, OSError) as exc:
        print(f"error: report_error: {exc}")
        return 1
    print(result.text, end="")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
