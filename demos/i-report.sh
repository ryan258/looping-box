#!/usr/bin/env bash
# Section I winner: `looping-box report`, the quarterly "what did we hold" summary (was a roadmap item).
set -euo pipefail
export DEMO_NAME=i
export LOOPING_BOX_APPROVER=ryan
source "$(dirname "${BASH_SOURCE[0]}")/../demo-env.sh"
demo_reset

echo "DEMO I: a week of activity, then one markdown report."
echo "------------------------------------------------------------"
IN="${LOOPING_BOX_ROOT}/inbox"
echo "Docs summary for the week." > "${IN}/week.md"
echo "Please deploy the hotfix." > "${IN}/hotfix.md"
echo "Delete the old backups." > "${IN}/backups.md"
echo "Send the pricing email." > "${IN}/pricing.md"
startday
approve "$(demo_review_id hotfix)" --note "Reviewed with Sam" >/dev/null
reject "$(demo_review_id backups)" --note "Legal hold, keep them" >/dev/null
startday

say_step "looping-box report"
lb report
echo
echo "Winner because: it turns the audit trail into something you can paste into a status update."
