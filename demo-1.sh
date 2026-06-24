#!/usr/bin/env bash
# Demo 1 — "It just works on safe stuff."
# Drops a harmless note in the inbox and shows it getting handled automatically.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

echo "DEMO 1: Safe, everyday work gets handled instantly."
echo "------------------------------------------------------------"
echo "Dropping a harmless note into the inbox..."
echo "Project docs: summarize the readme and the backlog." > inbox/notes.txt

echo "Running the loop..."
echo
./startday.sh

echo
echo "------------------------------------------------------------"
echo "Look for 'review=clear'. Translation:"
echo "  \"Nothing risky here, so I just did it.\" No babysitting needed."
