"""Ordering shared by Phase 1 history and worker freshness checks."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def delta_order(
    reference: str, generated_at: str | None = None, *, run_id: str | None = None, archived_name: bool = False,
) -> tuple[str, int, str]:
    """Recover the original delta order when only its reference is retained."""
    name = f"{run_id}.json" if run_id is not None else Path(reference).name
    collision_suffix = r"(?:-\d+)*" if archived_name else ""
    match = re.fullmatch(rf"phase1-delta-(\d{{8}}T\d{{6}}Z)(?:-(\d+))?{collision_suffix}\.json", name)
    if match:
        if generated_at is not None:
            return generated_at, int(match[2] or "1"), reference
        try:
            stamp = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ")
        except ValueError:
            pass
        else:
            return stamp.isoformat() + "Z", int(match[2] or "1"), reference
    return generated_at or "", 0, reference


def compare_delta_order(
    left: tuple[str, int, str], right: tuple[str, int, str], persisted_order: dict[str, int],
) -> int | None:
    """Return the comparison, or None for a tie whose order was never recorded."""
    if left[0] != right[0] or Path(left[2]).parent == Path(right[2]).parent:
        return (left > right) - (left < right)
    if left[2] in persisted_order and right[2] in persisted_order:
        return (persisted_order[left[2]] > persisted_order[right[2]]) - (
            persisted_order[left[2]] < persisted_order[right[2]]
        )
    return None


def _recent_delta_orders(state: dict[str, Any]) -> dict[str, list[str]]:
    groups = defaultdict(list)
    for run in state.get("runs", []):
        reference = run["delta_path"]
        if reference and reference not in groups[run["generated_at"]]:
            groups[run["generated_at"]].append(reference)
    return groups


def persisted_delta_order(state: dict[str, Any]) -> dict[str, int]:
    groups = {**_recent_delta_orders(state), **state.get("same_time_delta_order", {})}
    return {
        reference: number
        for stamp, references in groups.items()
        for number, reference in enumerate(references)
        if delta_order(reference)[0] == stamp
    }


def remember_delta_order(
    state: dict[str, Any], generated_at: str, reference: str | None, *, reserve_first: bool = False,
) -> bool:
    """Preserve known cross-queue ties before rolling the recent run history."""
    groups = _recent_delta_orders(state)
    if reference:
        groups[generated_at].append(reference)
    recorded = state.get("same_time_delta_order", {})
    updated = {stamp: references for stamp, references in recorded.items()
               if stamp == generated_at or len({Path(path).parent for path in references}) > 1}
    for stamp, references in groups.items():
        if (stamp in updated or len({Path(path).parent for path in references}) > 1
                or (reserve_first and stamp == generated_at and reference is not None)):
            updated[stamp] = list(dict.fromkeys([*recorded.get(stamp, []), *references]))
    if updated == recorded:
        return False
    state["same_time_delta_order"] = updated
    return True
