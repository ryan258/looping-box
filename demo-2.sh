#!/usr/bin/env bash
# Demo 2 — A configured term holds a source for review.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/demo-env.sh"
demo_reset

echo "DEMO 2: A configured term holds a source for review."
echo "------------------------------------------------------------"
echo "Adding a sample file containing 'deploy' and 'send'."
demo_add_held_release

echo "Running one offline pass."
echo
startday 2

echo
echo "------------------------------------------------------------"
echo "Held for review. The list shows the review ID and the saved record."
echo
lb review list

echo
echo "Source path and matched terms:"
demo_holds
echo "A review decision controls whether this source can enter drafting."
echo "Sending and deploying happen separately in your own tools."
