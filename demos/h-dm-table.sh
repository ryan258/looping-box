#!/usr/bin/env bash
# Section H winner: the Dungeon Master's table. Players propose, the DM rules, the log is the campaign.
set -euo pipefail
export DEMO_NAME=h
export LOOPING_BOX_APPROVER=DM
source "$(dirname "${BASH_SOURCE[0]}")/../demo-env.sh"
demo_reset

echo "DEMO H: every player turn is a file. Risky ones wait for the Dungeon Master."
echo "------------------------------------------------------------"
demo_add_keywords review_required attack steal summon
demo_add_keywords forbidden resurrect
IN="${LOOPING_BOX_ROOT}/inbox"
echo "Mira rests at the inn and writes in her journal." > "${IN}/mira.md"
echo "Tobin will attack the guard and steal the keys." > "${IN}/tobin.md"
echo "Wren casts resurrect on the dragon." > "${IN}/wren.md"
startday
say_step "Waiting on the DM:"
demo_holds

say_step "The DM rules:"
approve "$(demo_review_id tobin)" --note "Roll for stealth, DC 15"
approve "$(demo_review_id wren)" --note "sure, why not" || echo "  -> declined: 'resurrect' is on the forbidden list, the rules lawyer wins"
reject "$(demo_review_id wren)" --note "The gods are not amused."
startday

say_step "Tobin's released turn:"
grep -A2 "tobin" "${LOOPING_BOX_ROOT}/cache/workers/execution_engine/draft.md" | head -4

say_step "The campaign log:"
python3 - "${LOOPING_BOX_ROOT}" <<'PY'
import json, sys
from pathlib import Path
for line in (Path(sys.argv[1]) / "logs/transactions/review.jsonl").read_text().splitlines():
    e = json.loads(line)
    print(f"  {e['approver']} {e['event'].split('.')[1]}: {e['note']}")
PY
echo
echo "Winner because: approvals with notes and a tamper-evident log are the whole game here."
