#!/usr/bin/env bash
# Demo 3 — "The human gives the green light."
# Takes the item that Demo 2 left waiting, approves it with a note, and shows
# the item released into the pipeline. Run ./demo-2.sh first.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/demo-env.sh"

echo "DEMO 3: A human approves the held item and it is released."
echo "------------------------------------------------------------"

REVIEW_ID="$(lb review list | head -1 | cut -d' ' -f1 || true)"
if [ -z "${REVIEW_ID}" ]; then
  echo "Nothing is waiting for review. Run ./demo-2.sh first, then try again."
  exit 0
fi

echo "Found the item waiting for a decision: ${REVIEW_ID}"
echo
echo "What is being asked (the full record a reviewer would read):"
lb review show "${REVIEW_ID}"

echo
echo "A person approves it, leaving a note for the record."
echo "(Approvals normally need an interactive terminal; the demo opts out explicitly.)"
if LOOPING_BOX_ALLOW_NONINTERACTIVE=1 lb review approve "${REVIEW_ID}" --note "Checked with the team, good to go"; then
  echo
  echo "Running the loop one more time..."
  echo
  "${ROOT_DIR}/startday.sh"
  echo
  echo "------------------------------------------------------------"
  echo "It now reads 'review=clear' and the approved item was processed once."
  echo "The decision is HMAC-signed with the approver's name, and the audit log is"
  echo "hash-chained: ${LOOPING_BOX_ROOT}/logs/transactions/review.jsonl"
else
  echo
  echo "------------------------------------------------------------"
  echo "The approval was DECLINED (see the message above). The item stays held."
fi
