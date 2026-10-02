#!/usr/bin/env bash
# Section D winner: an overclaim tripwire for marketing copy. Your own words, your own gate.
set -euo pipefail
export DEMO_NAME=d
source "$(dirname "${BASH_SOURCE[0]}")/../demo-env.sh"
demo_reset

echo "DEMO D: hold copy that overclaims until a human looks at it."
echo "------------------------------------------------------------"
echo "Adding house rules: 'guarantee', 'unlimited', 'revolutionary' are review words."
demo_add_keywords review_required guarantee unlimited revolutionary
IN="${LOOPING_BOX_ROOT}/inbox"
echo "Docs summary: the onboarding guide covers setup, login, and first project." > "${IN}/guide.md"
echo "Launch copy: our revolutionary platform offers unlimited storage and a guaranteed 10x ROI." > "${IN}/launch.md"
startday
say_step "Held:"
demo_holds

say_step "The writer tones it down and saves the file..."
echo "Launch copy: our platform offers 1 TB of storage and early customers saw faster reporting." > "${IN}/launch.md"
startday
say_step "Held now:"
demo_holds
echo
echo "Winner because: the rule list is yours, and editing the text clears the hold with no approval needed."
