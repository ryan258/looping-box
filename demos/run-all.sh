#!/usr/bin/env bash
# Run every section demo in order. Each uses its own throwaway workspace.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
for demo in a-owed-list b-agent-preflight c-tamper-evidence d-overclaim-tripwire e-ci-gate \
            f-evasion-lab g-gate-tuning h-dm-table i-report; do
  echo; echo "################ ${demo} ################"
  ./"${demo}.sh"
done
