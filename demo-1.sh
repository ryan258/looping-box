#!/usr/bin/env bash
# Demo 1 — "It just works on safe stuff."
# Drops a harmless note in the inbox and shows it getting handled automatically.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/demo-env.sh"
demo_reset

echo "DEMO 1: Safe, everyday work gets handled instantly."
echo "------------------------------------------------------------"
echo "Dropping a harmless note into the inbox..."
echo "Project docs: summarize the readme and the backlog." > "${LOOPING_BOX_ROOT}/inbox/notes.txt"

echo "Running the loop..."
echo
"${ROOT_DIR}/startday.sh"

echo
echo "------------------------------------------------------------"
echo "Look for 'review=clear'. Translation:"
echo "  \"Nothing risky here, so it went straight through to a local draft.\""
echo "  Draft: ${LOOPING_BOX_ROOT}/cache/workers/execution_engine/draft.md"
