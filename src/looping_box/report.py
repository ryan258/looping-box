"""`looping-box report`: the audit logs and review decisions as one markdown summary. Read-only."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ._util import default_root, resolve_under_root, utc_now
from .review import list_reviews, verify_audit_logs


def _events(root: Path, log: str, since: str) -> list[dict[str, Any]]:
    path = resolve_under_root(root, f"logs/transactions/{log}.jsonl")
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a damaged line is reported by the integrity section, not here
        # ISO timestamps sort as strings, so "2026-10" or a full stamp both work.
        if isinstance(event, dict) and str(event.get("generated_at", "")) >= since:
            events.append(event)
    return events


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def build_report(root: Path | str, *, since: str = "") -> str:
    root_path = Path(root).resolve()
    phase1 = _events(root_path, "phase1", since)
    supervisor = _events(root_path, "supervisor", since)
    decisions = _events(root_path, "review", since)

    changed = sum(int(event.get("changed", 0)) for event in phase1)
    held_runs = sum(1 for event in phase1 if event.get("gate") == "pending_review")
    statuses = Counter(str(event.get("status", "?")) for event in supervisor)
    status_text = ", ".join(f"{count} {name}" for name, count in sorted(statuses.items())) or "none"

    lines = [
        "# Looping Box report",
        "",
        f"Period: {'since ' + since if since else 'all time'} (generated {utc_now()})",
        "",
        "## Activity",
        "",
        f"- phase 1 runs: {len(phase1)} ({changed} files changed, {held_runs} run(s) with items held)",
        f"- supervisor runs: {len(supervisor)} ({status_text})",
        f"- decisions: {len(decisions)}",
        f"- still pending: {len(list_reviews(root_path))}",
        "",
        "## Decisions",
        "",
    ]
    if decisions:
        lines += ["| When | Decision | Review | Approver | Interactive | Note |", "|---|---|---|---|---|---|"]
        for event in decisions:
            lines.append(
                "| {} | {} | {} | {} | {} | {} |".format(
                    _cell(event.get("generated_at", "")),
                    _cell(str(event.get("event", "")).removeprefix("review.")),
                    _cell(event.get("review_id", "")),
                    _cell(event.get("approver", "")),
                    "yes" if event.get("interactive") else "no",
                    _cell(event.get("note", "")),
                )
            )
    else:
        lines.append("None in this period.")

    lines += ["", "## Integrity", ""]
    audit = verify_audit_logs(root_path)
    if not audit:
        lines.append("No audit logs yet.")
    for name, broken_at in audit.items():
        lines.append(f"- {name}: " + ("intact" if broken_at is None else f"BROKEN at line {broken_at}"))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize audit logs and review decisions as markdown.")
    parser.add_argument(
        "--root",
        default=default_root(),
        help="Project root. Defaults to $LOOPING_BOX_ROOT, else the current directory.",
    )
    parser.add_argument("--since", default="", help="Only events at or after this ISO date/time, e.g. 2026-10-01.")
    args = parser.parse_args()
    try:
        report = build_report(args.root, since=args.since)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}")
        return 1
    print(report, end="")
    return 1 if "BROKEN at line" in report else 0  # same contract as `review audit`


if __name__ == "__main__":
    raise SystemExit(main())
