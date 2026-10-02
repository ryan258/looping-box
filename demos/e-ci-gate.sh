#!/usr/bin/env bash
# Section E winner: a CI forcing function. Exit code 2 means a human owes a decision.
set -euo pipefail
export DEMO_NAME=e
source "$(dirname "${BASH_SOURCE[0]}")/../demo-env.sh"
demo_reset

ci() {  # what a pipeline step would do: run, capture the exit code, branch on it
  local code=0
  "${ROOT_DIR}/startday.sh" >/dev/null 2>&1 || code=$?
  case "${code}" in
    0) echo "  CI exit code 0: PASS" ;;
    2) echo "  CI exit code 2: BLOCKED, human sign-off required" ;;
    *) echo "  CI exit code ${code}: FAILED" ;;
  esac
}

echo "DEMO E: use the exit code to make sign-off part of the pipeline."
echo "------------------------------------------------------------"
IN="${LOOPING_BOX_ROOT}/inbox"
echo "Release notes draft: docs and summary for the next version." > "${IN}/notes.md"
say_step "Run 1, ordinary content:"; ci
echo "Plan: deploy the new build to production tonight." > "${IN}/rollout.md"
say_step "Run 2, someone adds a rollout plan:"; ci
demo_holds
say_step "A person signs off (production is blocked-class, so the override is explicit):"
approve "$(demo_review_id rollout.md)" --note "Change window approved by ops" --allow-blocked
say_step "Run 3:"; ci
echo
echo "Winner because: it is three lines of shell in any CI system and needs no integration."
