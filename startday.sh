#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v looping-box-phase1 >/dev/null 2>&1; then
  looping-box-phase1 --root "${ROOT_DIR}" "$@"
else
  export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
  python3 -m looping_box.phase1 --root "${ROOT_DIR}" "$@"
fi
