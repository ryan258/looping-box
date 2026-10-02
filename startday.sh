#!/usr/bin/env bash
# One full pass: ingest the inbox (phase 1), then run the worker supervisor once.
# Arguments are passed to phase 1 only. Set LOOPING_BOX_ROOT to use another workspace.
# Exit: 0 ok, 1 failed/busy, 2 blocked (a review or limit needs the operator).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${LOOPING_BOX_ROOT:-${ROOT_DIR}}"

run() {
  local tool="$1"; shift
  # Inside a checkout, run its own source; an installed copy could be stale.
  if [ -d "${ROOT_DIR}/src/looping_box" ]; then
    PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" python3 -m "looping_box.${tool}" --root "${ROOT}" "$@"
  else
    "looping-box-${tool}" --root "${ROOT}" "$@"
  fi
}

run phase1 "$@"
run supervisor --once
