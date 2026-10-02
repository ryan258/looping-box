#!/usr/bin/env bash
# Demo 2 — "It stops on risky action language." (the money demo)
# Drops a file with real-world action language and shows the boundary gate
# hold it for a human.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/demo-env.sh"
demo_reset

echo "DEMO 2: Risky work is HELD until a human decides."
echo "------------------------------------------------------------"
echo "Dropping a file that asks to deploy and send things..."
echo "Please deploy the release and send the announcement email." > "${LOOPING_BOX_ROOT}/inbox/release.txt"

echo "Running the loop..."
echo
startday

echo
echo "------------------------------------------------------------"
echo "It stopped at the BOUNDARY GATE. Nothing is deployed or sent: this tool"
echo "never performs outward actions, it only holds them for a person."
echo "Here is what is now waiting for a human decision:"
echo
lb review list

echo
echo "------------------------------------------------------------"
echo "That item stays flagged on every run until a person approves or rejects it."
