# Sourced by demo-*.sh. Demos run in a throwaway workspace so they are repeatable
# and never touch this repo's inbox, state, or logs.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# DEMO_NAME (set before sourcing) gives each demo its own throwaway workspace.
export LOOPING_BOX_ROOT="${TMPDIR:-/tmp}/looping-box-demo${DEMO_NAME:+-${DEMO_NAME}}"
# Demo-only signing key (real use: a private key outside the workspace).
export LOOPING_BOX_REVIEW_KEY="demo-key-not-a-secret"

lb() {  # lb <phase1|supervisor|review|worker> [args...]
  local tool="$1"; shift
  PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" python3 -m "looping_box.${tool}" "$@"
}

# startday.sh exits 2 when something is held for a human; that is the expected outcome here.
startday() {
  "${ROOT_DIR}/startday.sh" || [ $? -eq 2 ]
}

demo_reset() {
  rm -rf "${LOOPING_BOX_ROOT}"
  mkdir -p "${LOOPING_BOX_ROOT}/inbox"
  cp -R "${ROOT_DIR}/config" "${LOOPING_BOX_ROOT}/config"
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
