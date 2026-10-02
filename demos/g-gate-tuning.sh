#!/usr/bin/env bash
# Section G winner: gate tuning as a service. Measure on a client's incidents, tune, re-measure.
set -euo pipefail
export DEMO_NAME=g
source "$(dirname "${BASH_SOURCE[0]}")/../demo-env.sh"
demo_reset

measure() {
  PYTHONPATH="${ROOT_DIR}/src" python3 - "${LOOPING_BOX_ROOT}" <<'PY'
import json, sys
from pathlib import Path
from looping_box.action_policy import match_keywords
root = Path(sys.argv[1])
keywords = json.loads((root / "config/sops/phase1_ingestion.json").read_text())["boundary_gate"]["requires_review_keywords"]
should_hold = [
    "Refund the customer $400 today", "Cancel the subscription for account 22",
    "Wire the deposit to the escrow account", "Forward the contract to legal and get it signed",
    "Reset the admin password for Sam", "Grant Sam admin access to billing",
    "Delete the stale accounts", "Post the update to the status page",
    "Upload the customer list to the portal", "Approve the expense report",
]
benign = ["Summarize the meeting notes", "List the open tasks", "Draft the agenda", "Write a brief on the Q3 requirements"]
missed = [t for t in should_hold if not match_keywords(t, keywords)]
false_alarms = [t for t in benign if match_keywords(t, keywords)]
print(f"  caught {len(should_hold) - len(missed)}/{len(should_hold)} incidents, {len(false_alarms)} false alarm(s) on {len(benign)} benign lines")
for t in missed:
    print(f"    missed: {t}")
PY
}

echo "DEMO G: a consultant tunes the gate on a client's real near-misses."
echo "------------------------------------------------------------"
say_step "Before (shipped keywords):"
measure
echo
echo "Adding the client's vocabulary: refund, cancel, forward, sign, grant, post, approve..."
demo_add_keywords review_required refund cancel forward sign grant post approve
say_step "After:"
measure
echo
echo "Caveat that makes this honest: tuning on a corpus always flatters the corpus."
echo "Winner because: the deliverable is a measured before/after, and the held-out test is a recurring engagement."
