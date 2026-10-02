# Sourced by demo-*.sh. Demos run in a throwaway workspace so they are repeatable
# and never touch this repo's inbox, state, or logs.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LOOPING_BOX_ROOT="${TMPDIR:-/tmp}/looping-box-demo"
# Demo-only signing key (real use: a private key outside the workspace).
export LOOPING_BOX_REVIEW_KEY="demo-key-not-a-secret"

lb() {  # lb <phase1|supervisor|review|worker> [args...]
  local tool="$1"; shift
  if command -v "looping-box-${tool}" >/dev/null 2>&1; then
    "looping-box-${tool}" "$@"
  else
    PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" python3 -m "looping_box.${tool}" "$@"
  fi
}

demo_reset() {
  rm -rf "${LOOPING_BOX_ROOT}"
  mkdir -p "${LOOPING_BOX_ROOT}/inbox"
  cp -R "${ROOT_DIR}/config" "${LOOPING_BOX_ROOT}/config"
}
