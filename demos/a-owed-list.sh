#!/usr/bin/env bash
# Section A winner: "What do I owe people?" Meeting notes in, a follow-up list out.
set -euo pipefail
export DEMO_NAME=a
source "$(dirname "${BASH_SOURCE[0]}")/../demo-env.sh"
demo_reset

echo "DEMO A: your notes sort themselves into 'done' and 'you owe someone'."
echo "------------------------------------------------------------"
IN="${LOOPING_BOX_ROOT}/inbox"
echo "Meeting notes: next step is the architecture decision. Action item: write the team summary." > "${IN}/standup.md"
echo "Meeting notes. Action item: send the revised quote to Dana and email the contract." > "${IN}/client-call.md"
echo "Action item: pay the invoice from the vendor by Friday." > "${IN}/vendor.md"
echo "Three meeting notes dropped in inbox/. Running the loop..."
startday

say_step "Safe note, drafted locally:"
sed -n 1,8p "${LOOPING_BOX_ROOT}/cache/workers/execution_engine/draft.md"
say_step "Your owed list (held, not acted on; nothing was sent or paid):"
demo_holds
echo
echo "Winner because: zero setup, works offline, and 'what do I owe?' is a list you actually want."
