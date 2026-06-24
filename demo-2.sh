#!/usr/bin/env bash
# Demo 2 — "It refuses to do the dangerous thing." (the money demo)
# Drops a file with real-world action language and shows the boundary gate
# stop everything and wait for a human.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"
export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"

echo "DEMO 2: Risky work is BLOCKED until a human approves it."
echo "------------------------------------------------------------"
echo "Dropping a file that asks to deploy and send things..."
echo "Please deploy the release and send the announcement email." > inbox/release.txt

echo "Running the loop..."
echo
./startday.sh

echo
echo "------------------------------------------------------------"
echo "It stopped at the BOUNDARY GATE. It will NOT deploy or send on its own."
echo "Here is what is now waiting for a human decision:"
echo
python3 -m looping_box.review list

echo
echo "------------------------------------------------------------"
echo "That item stays flagged on every run until a person approves,"
echo "rejects, or removes it. Nothing slips through."
