# Sourced by the demo scripts. Each fixture uses a fresh, offline workspace.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export OPENROUTER_API_KEY= MODEL_CONTEXT_BUILDER= MODEL_EXECUTION_ENGINE= MODEL_VERIFIER=
unset OPENROUTER_BASE_URL LOOPING_BOX_ALLOW_NONINTERACTIVE
export LOOPING_BOX_REVIEW_KEY="demo-key-not-a-secret"
export LOOPING_BOX_APPROVER="${DEMO_APPROVER:-demo-operator}"

lb() {  # lb <phase1|supervisor|review|worker> [args...]
  local tool="$1"; shift
  PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" python3 -m "looping_box.${tool}" "$@"
}

startday() {
  local expected="$1" code=0
  "${ROOT_DIR}/startday.sh" --root "${LOOPING_BOX_ROOT}" || code=$?
  if [ "${code}" -ne "${expected}" ]; then
    printf 'Expected run exit %s; got %s.\n' "${expected}" "${code}" >&2
    return 1
  fi
}

demo_reset() {
  LOOPING_BOX_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/looping-box-demo.XXXXXXXX")" || return
  export LOOPING_BOX_ROOT
  export XDG_CONFIG_HOME="${LOOPING_BOX_ROOT}/private-config"
  PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" python3 -m looping_box init "${LOOPING_BOX_ROOT}" >/dev/null || return
  printf 'Demo workspace: %s\n' "${LOOPING_BOX_ROOT}"
}

demo_add_held_release() {
  printf '%s\n' 'Please deploy the release and send the announcement email.' > "${LOOPING_BOX_ROOT}/inbox/release.txt"
}

approve() { LOOPING_BOX_ALLOW_NONINTERACTIVE=1 lb review approve "$@" < /dev/null; }  # demos opt out of the TTY on purpose
reject()  { lb review reject "$@"; }

demo_review_id() {  # demo_review_id <text in the held file's path> -> its review id
  python3 - "${LOOPING_BOX_ROOT}" "$1" <<'PY'
import json, sys
from pathlib import Path
root, needle = Path(sys.argv[1]), sys.argv[2]
index = root / "staging" / "pending_review.json"
for ref in json.loads(index.read_text())["reviews"] if index.exists() else []:
    payload = json.loads((root / ref).read_text())
    if needle in payload["source_items"][0]["relative_path"]:
        print(payload["review_id"])
        break
else:
    sys.exit(f"no pending review for {needle!r}")
PY
}

demo_holds() {  # what is waiting for a human right now, with the reasons
  python3 - "${LOOPING_BOX_ROOT}" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
index = root / "staging" / "pending_review.json"
refs = json.loads(index.read_text())["reviews"] if index.exists() else []
if not refs:
    print("  (nothing is held)")
for ref in refs:
    payload = json.loads((root / ref).read_text())
    print(f"  HELD [{payload['action_class']}] {payload['source_items'][0]['relative_path']}  <-  {', '.join(payload['risk_reasons'])}")
PY
}

demo_add_keywords() {  # demo_add_keywords <class> <word>... : new gate words, in the SOP and the action classes
  local cls="$1"; shift
  python3 - "${LOOPING_BOX_ROOT}" "${cls}" "$@" <<'PY'
import json, sys
from pathlib import Path
root, cls, words = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
sop_path = root / "config" / "sops" / "phase1_ingestion.json"
classes_path = root / "config" / "action_classes.json"
sop, classes = json.loads(sop_path.read_text()), json.loads(classes_path.read_text())
gate = sop["boundary_gate"]["requires_review_keywords"]
for word in words:
    if word not in gate:
        gate.append(word)
    classes["classes"].setdefault(cls, []).append(word)
sop_path.write_text(json.dumps(sop, indent=2) + "\n")
classes_path.write_text(json.dumps(classes, indent=2) + "\n")
PY
}

say_step() { echo; echo "== $*"; }
