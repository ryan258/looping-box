#!/usr/bin/env bash
# Demo 3 — "The human gives the green light."
# Takes the item that Demo 2 left waiting, approves it with a note, and shows
# the loop go quiet. Run ./demo-2.sh first so there is something to approve.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

echo "DEMO 3: A human approves the held item, and the nagging stops."
echo "------------------------------------------------------------"

REVIEW_ID="$(looping-box-review list | head -1 | cut -d' ' -f1)"
if [ -z "${REVIEW_ID}" ]; then
  echo "Nothing is waiting for review. Run ./demo-2.sh first, then try again."
  exit 0
fi

echo "Found the item waiting for a decision: ${REVIEW_ID}"
echo
echo "What is being asked (the full record a reviewer would read):"
looping-box-review show "${REVIEW_ID}"

echo
echo "A person approves it, leaving a note for the record..."
if looping-box-review approve "${REVIEW_ID}" --note "Checked with the team, good to go"; then
  echo
  echo "Running the loop one more time..."
  echo
  ./startday.sh
  echo
  echo "------------------------------------------------------------"
  echo "It now reads 'review=clear'. The item was approved, so it stops nagging."
  echo "Every approval is signed, dated, and saved — a full audit trail."
else
  echo
  echo "------------------------------------------------------------"
  echo "The safety verifier REFUSED to record this approval."
  echo "Even after a person says yes, a deterministic (and optional model)"
  echo "check gets the final say on risky actions. The item stays held."
fi
