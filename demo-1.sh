#!/usr/bin/env bash
# Demo 1 — From a local note to a traceable draft.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/demo-env.sh"
demo_reset

echo "DEMO 1: From a local note to a traceable draft."
echo "------------------------------------------------------------"
echo "Adding a sample backlog note to the local inbox."
echo "Project docs: summarize the readme and the backlog." > "${LOOPING_BOX_ROOT}/inbox/notes.txt"

echo "Running one offline pass."
echo
startday 0

echo
echo "------------------------------------------------------------"
draft="${LOOPING_BOX_ROOT}/cache/workers/execution_engine/draft.md"
echo "Draft saved: ${draft}"
echo "The Markdown shows the source path and the original excerpt."
cat "${draft}"
