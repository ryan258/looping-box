#!/usr/bin/env bash
# Section C winner: tamper-evident approvals. Edit the evidence and watch it fail.
set -euo pipefail
export DEMO_NAME=c
export LOOPING_BOX_APPROVER=ryan
source "$(dirname "${BASH_SOURCE[0]}")/../demo-env.sh"
demo_reset

echo "DEMO C: who approved what, and can anyone quietly change it?"
echo "------------------------------------------------------------"
echo "Please deploy the hotfix." > "${LOOPING_BOX_ROOT}/inbox/change.md"
startday
ID="$(demo_review_id change.md)"
approve "${ID}" --note "Verified the diff with Sam"
startday

say_step "The record of the decision (hash-chained):"
python3 - "${LOOPING_BOX_ROOT}" <<'PY'
import json, sys
from pathlib import Path
for line in (Path(sys.argv[1]) / "logs/transactions/review.jsonl").read_text().splitlines():
    e = json.loads(line)
    print(f"  {e['event']}  by {e['approver']}  note={e['note']!r}  hash={e['hash'][:12]}...")
PY
say_step "Audit check on untouched logs:"
lb review audit

say_step "Now someone rewrites the note in the log..."
python3 - "${LOOPING_BOX_ROOT}" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1]) / "logs/transactions/review.jsonl"
entry = json.loads(path.read_text().splitlines()[0]); entry["note"] = "Verified nothing, honestly"
path.write_text(json.dumps(entry) + "\n")
PY
lb review audit || echo "  -> exit code $? (broken chain detected)"

say_step "...and edits the signed approval record to change what was agreed:"
python3 - "${LOOPING_BOX_ROOT}" "${ID}" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1]) / "staging/approvals" / f"{sys.argv[2]}.json"
record = json.loads(path.read_text()); record["note"] = "edited after the fact"
path.write_text(json.dumps(record))
PY
startday 2>&1 | grep -E "warning|review=" || true
demo_holds
echo
echo "Winner because: it is the one claim you can demonstrate in 60 seconds. (Tamper-evident, not tamper-proof.)"
