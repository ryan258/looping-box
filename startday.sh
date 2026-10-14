#!/usr/bin/env bash
# One complete pass; only --root is accepted. Custom paths belong to phase1.
# Explicit --root wins over LOOPING_BOX_ROOT, then this script's directory.
# Exit: 0 complete/idle, 1 failed/busy, 2 blocked/usage.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="${LOOPING_BOX_ROOT:-${SCRIPT_DIR}}"

# Inside a checkout, prefer its source to a potentially stale installed copy.
if [ -d "${SCRIPT_DIR}/src/looping_box" ]; then
  export PYTHONPATH="${SCRIPT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
  exec python3 -m looping_box run --root "${WORKSPACE_ROOT}" "$@"
fi
exec looping-box run --root "${WORKSPACE_ROOT}" "$@"
