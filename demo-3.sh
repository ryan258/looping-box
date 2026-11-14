#!/usr/bin/env bash
# Demo 3 — A scripted fixture decision releases a source into drafting.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/demo-env.sh"
demo_reset

echo "DEMO 3: Approve a held source for drafting."
echo "------------------------------------------------------------"

demo_add_held_release
startday 2
REVIEW_ID="$(demo_review_id release.txt)"

echo "Found the item waiting for a decision: ${REVIEW_ID}"
echo
echo "Review details, including the source and matched terms:"
lb review show "${REVIEW_ID}"

echo
echo "Recording a scripted fixture approval with a note."
echo "See Demo 3 in docs/sales-demos.md for the interactive prompt."
approve "${REVIEW_ID}" --note "Reviewed the fixture for local drafting"
echo "Running the default next step with the CLI from this checkout."
startday 0

echo
draft="${LOOPING_BOX_ROOT}/cache/workers/execution_engine/draft.md"
echo "Draft saved: ${draft}"
cat "${draft}"
echo "Signed decision: ${LOOPING_BOX_ROOT}/staging/approvals/${REVIEW_ID}.json"
echo "Hash-linked review log: ${LOOPING_BOX_ROOT}/logs/transactions/review.jsonl"
echo "Sending and deploying happen separately in your own tools."
