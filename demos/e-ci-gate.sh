#!/usr/bin/env bash
# Section E winner: a CI forcing function. Inspect the reason for exit 2.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../demo-env.sh"
demo_reset

ci() {  # what a pipeline step would do: run, capture the exit code, branch on it
  local expected="$1" code=0
  "${ROOT_DIR}/startday.sh" --root "${LOOPING_BOX_ROOT}" >/dev/null || code=$?
  case "${code}" in
    0) echo "  CI exit code 0: PASS" ;;
    2) echo "  CI exit code 2: inspect for a hold, a resource block, or usage error" ;;
    *) echo "  CI exit code ${code}: FAILED" ;;
  esac
  if [ "${code}" -ne "${expected}" ]; then
    echo "Expected run exit ${expected}; got ${code}." >&2
    return 1
  fi
}

echo "DEMO E: use the exit code to make sign-off part of the pipeline."
echo "------------------------------------------------------------"
IN="${LOOPING_BOX_ROOT}/inbox"
echo "Release notes draft: docs and summary for the next version." > "${IN}/notes.md"
say_step "Run 1, ordinary content:"; ci 0
echo "Plan: deploy the new build to production tonight." > "${IN}/rollout.md"
say_step "Run 2, someone adds a rollout plan:"; ci 2
demo_holds
say_step "A person signs off (production is blocked-class, so the override is explicit):"
approve "$(demo_review_id rollout.md)" --note "Change window approved by ops" --allow-blocked
say_step "Run 3:"; ci 0
echo
echo "Winner because: it is three lines of shell in any CI system and needs no integration."
