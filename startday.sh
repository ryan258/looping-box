#!/usr/bin/env bash
# One full pass: ingest the inbox (phase 1), then run the worker supervisor once.
# Arguments are passed to phase 1 only. Set LOOPING_BOX_ROOT to use another workspace.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${LOOPING_BOX_ROOT:-${ROOT_DIR}}"

run() {
  local tool="$1"; shift
  if command -v "looping-box-${tool}" >/dev/null 2>&1; then
    "looping-box-${tool}" --root "${ROOT}" "$@"
  else
    PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" python3 -m "looping_box.${tool}" --root "${ROOT}" "$@"
  fi
}

run phase1 "$@"
run supervisor --once
