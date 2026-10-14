"""T01 / A04, A13, A30: recursive contracts and validation at runtime boundaries.

Run explicitly with unittest discovery. All workspaces and keys are temporary;
model entry points are mocked, with a transport tripwire as a final safeguard.
T02 filesystem checks and T04–T09 behavioral changes are intentionally separate.
"""
import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from looping_box import doctor, phase1, report, review, supervisor, worker
from looping_box._util import decision_state, read_pending_review_index, sign_record, verify_audit_chain
from looping_box.action_policy import DEFAULT_ACTION_CLASSES, _read_action_config
from looping_box.scaffold import init_workspace
from looping_box.schema import validate
from looping_box.validation import SCHEMAS, ValidationError, read_validated, validate_data, validate_policy

STAMP = "2026-10-13T12:00:00Z"
HASH = "a" * 64
RID = "review-" + "b" * 16
DELTA = "cache/deltas/phase1-delta-20261013T120000Z.json"
PAYLOAD = f"staging/reviews/{RID}.json"
KEY = "test-only-t01-key"


def fixtures():
    """One valid, nested sample for every section 5 / 11 schema row."""
    summary = dict(scanned=1, changed=1, skipped=0, requires_review=False)
    change = dict(relative_path="inbox/a.md", sha256=HASH, size_bytes=4,
                  matched_routes=["docs"], review_reasons=[], excerpt="docs")
    item = dict(relative_path="inbox/a.md", sha256=HASH, matched_routes=["docs"],
                excerpt="docs", source_delta=DELTA)
    recovery = dict(last_error=None, blocked_reason=None, pending_review_payload=None,
                    operator_action_required=False)
    def artifact(marker, **fields):
        return dict(schema="looping-box." + marker, **fields)
    output = artifact("worker.output.v1", worker_id="context_builder", run_id="worker-run", generated_at=STAMP,
                      status="complete", inputs={"source_deltas": [DELTA]}, outputs={"artifacts": []}, errors=[])
    result = {
        "phase1_state": artifact("phase1.state.v1", processed_files={"inbox/a.md": dict(
            sha256=HASH, size_bytes=4, processed_at=STAMP)}, processed_hashes={HASH: dict(
            first_seen_path="inbox/a.md", first_seen_at=STAMP)}, runs=[dict(
                generated_at=STAMP, delta_path=DELTA, summary=summary)]),
        "phase1_delta": artifact("phase1.delta.v1", run_id="phase1-delta-20261013T120000Z", generated_at=STAMP,
            sop=dict(path="config/sops/phase1_ingestion.json", sha256=HASH, name="Ingestion"),
            inputs=dict(root="/workspace", input_dir="inbox"), summary=summary, changes=[change], skipped=[],
            boundary_gate=dict(status="clear", payload=None, reasons=[]), delta_path=DELTA),
        "pending_review_index": artifact("pending-review-index.v1", generated_at=STAMP, latest=PAYLOAD, reviews=[PAYLOAD]),
        "review_payload": artifact("review-payload.v1", review_id=RID, generated_at=STAMP, source="phase1",
            notification_message="Review", action_class="review_required", risk_reasons=["deploy"],
            source_items=[dict(change, review_reasons=["deploy"])], generated_artifacts=[],
            verifier=dict(required=True, status="pending", result=None), suggested_verification=[]),
        "review_record": artifact("review-record.v2", review_id=RID, generated_at=STAMP, decision="approved",
            note="Checked", approver="Operator", interactive=True, payload_sha256=HASH,
            verifier_result=None, signature=HASH),
        "verifier_result": artifact("verifier-result.v1", review_id=RID, generated_at=STAMP, status="passed",
            checks=[dict(name="schema", status="passed", message="Valid")]),
        "worker_state": artifact("worker.state.v1", worker_id="execution_engine", consumed_inputs=[DELTA]),
        "context_package": artifact("worker.context-package.v1", worker_id="context_builder", generated_at=STAMP,
            status="blocked", source_deltas=[DELTA], items=[item], blocked_inputs=[dict(
                item, review_reasons=["deploy"], pending_review_payload="staging/pending_review.json")]),
        "execution_draft": artifact("execution-draft.v1", generated_at=STAMP,
            source_context="cache/workers/context_builder/context_package.json", source_context_sha256=HASH,
            items=[dict(relative_path="inbox/a.md", matched_routes=["docs"], draft="Note")], held_for_review=[]),
        "worker_output": output,
        "world_state": artifact("world-state.v1", registered_loops=["phase1"],
            worker_states={"context_builder": dict(status="complete", last_run_at=STAMP,
                last_output="cache/workers/context_builder/last_output.json")}, observed_deltas=[DELTA], dirty=False,
            dependencies=[dict(edge) for edge in supervisor.DEFAULT_CONFIG["dependencies"]], recovery=recovery,
            runs=[dict(generated_at=STAMP, status="complete", plan=["context_builder"], source_deltas=[DELTA])], leases={}),
        "supervisor_blocked": artifact("supervisor.blocked.v1", generated_at=STAMP, reason="file_count_limit",
            message="Too many", source_deltas=[DELTA]),
        "supervisor_run": artifact("supervisor.run.v1", generated_at=STAMP, status="complete",
            plan=["context_builder"], worker_outputs=[output], recovery=recovery),
        "audit_event": artifact("audit-event.v1", generated_at=STAMP, event="phase1.run",
            run_id="phase1-delta-20261013T120000Z", changed=1, skipped=0, gate="clear", review_ids=[],
            released_review_ids=[], warnings=[], prev="", hash=HASH),
    }
    for kind, relative in (("phase1_ingestion", "sops/phase1_ingestion.json"),
                           ("action_classes", "action_classes.json"), ("super_loop", "super_loop.json")):
        result[kind] = json.loads((ROOT / "src/looping_box/templates" / relative).read_text())
    return {name: copy.deepcopy(value) for name, value in result.items()}


def set_field(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class ContractTests(unittest.TestCase):
    def test_every_row_and_mirrored_resource(self):
        samples = fixtures()
        self.assertEqual(set(samples), set(SCHEMAS))
        for directory in (ROOT / "docs/schemas", ROOT / "src/looping_box/templates/schemas"):
            self.assertEqual({p.name for p in directory.glob("*.json")}, {name + ".schema.json" for name in SCHEMAS})
            for kind, sample in samples.items():
                with self.subTest(directory=directory, kind=kind):
                    document = json.loads((directory / (kind + ".schema.json")).read_text())
                    self.assertEqual(document, SCHEMAS[kind])
                    validate(sample, document)
                    validate_data(sample, kind, "sample.json")

    def test_shipped_config_defaults_agree(self):
        samples = fixtures()
        self.assertEqual(samples["super_loop"], supervisor.DEFAULT_CONFIG)
        self.assertEqual(samples["action_classes"], dict(DEFAULT_ACTION_CLASSES, schema="looping-box.action-classes.v1"))
        for kind, relative in (("phase1_ingestion", "sops/phase1_ingestion.json"),
                               ("action_classes", "action_classes.json"), ("super_loop", "super_loop.json")):
            with self.subTest(kind=kind):
                self.assertEqual(samples[kind], json.loads((ROOT / "config" / relative).read_text()))

    def test_optional_nested_contracts(self):
        samples = fixtures()
        samples["phase1_delta"]["skipped"] = [dict(relative_path="inbox/old.md", sha256=HASH, reason="already_processed")]
        samples["phase1_delta"]["warnings"] = [dict(code="decision_unverifiable", review_id=RID,
            relative_path="inbox/old.md", message="Held")]
        samples["context_package"]["synthesis"] = dict(text="Summary", model="test", response_sha256=HASH)
        samples["worker_output"]["errors"] = [dict(code="model_error", message="Unavailable")]
        samples["worker_output"]["planned_actions"] = [dict(action="archive", source=DELTA,
            destination="cache/deltas/archive/a.json")]
        samples["execution_draft"]["items"][0].update(model="test", response_sha256=HASH, approved_review=RID)
        for kind in ("phase1_delta", "context_package", "worker_output", "execution_draft"):
            validate_data(samples[kind], kind, "optional.json")
        for kind, path, bad_value in (
            ("phase1_delta", ["skipped", 0, "reason"], "ignored"),
            ("phase1_delta", ["warnings", 0, "review_id"], "invalid"),
            ("context_package", ["synthesis", "response_sha256"], "invalid"),
            ("worker_output", ["errors", 0, "code"], " "),
            ("worker_output", ["planned_actions", 0, "destination"], "../archive"),
            ("execution_draft", ["items", 0, "approved_review"], "invalid"),
        ):
            value = copy.deepcopy(samples[kind])
            set_field(value, path, bad_value)
            with self.subTest(kind=kind, path=path), self.assertRaises(ValidationError):
                validate_data(value, kind, "optional.json")

    def test_required_fields_versions_and_object_types(self):
        for kind, sample in fixtures().items():
            for malformed in ([], None, True, 12, "object"):
                with self.subTest(kind=kind, malformed=malformed), self.assertRaisesRegex(ValidationError, r"sample.json: \$"):
                    validate_data(malformed, kind, "sample.json")
            for field in SCHEMAS[kind]["required"]:
                bad = copy.deepcopy(sample)
                del bad[field]
                with self.subTest(kind=kind, missing=field), self.assertRaisesRegex(ValidationError, r"\$\." + field):
                    validate_data(bad, kind, "sample.json")
            if "schema" in sample:
                bad = dict(sample, schema="looping-box.future.v99")
                with self.subTest(kind=kind), self.assertRaisesRegex(ValidationError, r"\$\.schema"):
                    validate_data(bad, kind, "sample.json")

    def test_nested_malformed_values(self):
        cases = [
            ("phase1_state", ["processed_files", "inbox/a.md", "size_bytes"], True),
            ("phase1_state", ["processed_hashes", HASH, "first_seen_at"], "2026-02-30T00:00:00Z"),
            ("phase1_state", ["runs", 0, "summary", "changed"], -1),
            ("phase1_delta", ["summary", "scanned"], False),
            ("phase1_delta", ["changes", 0, "sha256"], "A" * 64),
            ("phase1_delta", ["changes", 0, "sha256"], HASH + "\n"),
            ("phase1_delta", ["changes", 0, "matched_routes"], "docs"),
            ("phase1_delta", ["boundary_gate", "reasons"], ["held"]),
            ("review_payload", ["source_items"], []),
            ("review_payload", ["verifier", "required"], 1),
            ("review_record", ["note"], " \t\n"),
            ("review_record", ["review_id"], RID + "\n"),
            ("verifier_result", ["checks", 0, "status"], "pending"),
            ("worker_state", ["consumed_inputs"], [None]),
            ("context_package", ["blocked_inputs", 0, "review_reasons"], []),
            ("context_package", ["items", 0, "excerpt"], {}),
            ("execution_draft", ["items", 0, "draft"], []),
            ("worker_output", ["outputs", "artifacts"], "draft.json"),
            ("world_state", ["worker_states", "context_builder", "status"], "running"),
            ("world_state", ["recovery", "operator_action_required"], 0),
            ("supervisor_run", ["worker_outputs", 0, "errors"], "failed"),
            ("audit_event", ["changed"], True),
        ]
        for kind, field, value in cases:
            bad = fixtures()[kind]
            set_field(bad, field, value)
            with self.subTest(kind=kind, field=field, value=value), self.assertRaisesRegex(ValidationError, r"sample.json: \$"):
                validate_data(bad, kind, "sample.json")

    def test_paths_do_not_require_historical_files(self):
        for invalid in ("/tmp/file", "../file", "inbox/../file", "./file", "inbox//file", "inbox/", "", "C:/file", "a\\b", "a\n"):
            value = fixtures()["context_package"]
            value["items"][0]["source_delta"] = invalid
            with self.subTest(path=invalid), self.assertRaisesRegex(ValidationError, r"items\[0\].source_delta"):
                validate_data(value, "context_package", "context.json")
        valid = fixtures()["context_package"]
        valid["items"][0]["source_delta"] = "missing/archive/history.json"
        validate_data(valid, "context_package", "context.json")

    def test_finite_values_and_boolean_numeric_distinction(self):
        for value in (True, False, 0, -1, float("nan"), float("inf"), float("-inf"), 10**400, "30"):
            bad = fixtures()["super_loop"]
            bad["max_worker_runtime_seconds"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValidationError, "invalid_config"):
                validate_data(bad, "super_loop", "config.json")
        for field in ("max_files_per_cycle", "max_payload_bytes", "stale_lock_seconds"):
            for value in (True, 0, -1, 1.5):
                bad = fixtures()["super_loop"]
                bad[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    validate_data(bad, "super_loop", "config.json")
        sample = fixtures()["worker_output"]
        sample["extra_metadata"] = {"deep": [float("nan")]}
        with self.assertRaisesRegex(ValidationError, r"extra_metadata.deep\[0\]"):
            validate_data(sample, "worker_output", "output.json")

    def test_config_relationships_and_unknown_keys(self):
        cases = [
            ("super_loop", ["workers"], ["unknown"]),
            ("super_loop", ["workers"], ["context_builder", "context_builder"]),
            ("super_loop", ["workers"], []),  # existing edges must not reference disabled workers
            ("super_loop", ["dependencies"], [{"from": "execution_engine", "to": "context_builder"}]),
            ("super_loop", ["dependencies"], [supervisor.DEFAULT_CONFIG["dependencies"][0]] * 2),
            ("phase1_ingestion", ["routes"], [{"label": "docs", "keywords": []}] * 2),
            ("phase1_ingestion", ["allowed_extensions"], [".md", ".MD"]),
            ("phase1_ingestion", ["allowed_extensions"], ["md"]),
            ("phase1_ingestion", ["max_excerpt_chars"], 2),
            ("phase1_ingestion", ["max_file_bytes"], True),
            ("action_classes", ["default_class"], "safe_local_transform"),
            ("action_classes", ["classes", "forbidden"], ["DEPLOY"]),
            ("action_classes", ["classes", "blocked"], ["secret", "secret"]),
        ]
        for kind, field, value in cases:
            bad = fixtures()[kind]
            set_field(bad, field, value)
            with self.subTest(kind=kind, field=field), self.assertRaises(ValidationError):
                validate_data(bad, kind, "config.json")
        for kind, field in (("super_loop", []), ("phase1_ingestion", ["routes", 0]),
                            ("action_classes", ["classes"]), ("phase1_ingestion", ["boundary_gate"])):
            bad = fixtures()[kind]
            target = bad
            for key in field:
                target = target[key]
            target["typo"] = True
            with self.subTest(kind=kind, field=field), self.assertRaisesRegex(ValidationError, "unknown configuration key"):
                validate_data(bad, kind, "config.json")
        disabled = dict(fixtures()["super_loop"], workers=[], dependencies=[])
        validate_data(disabled, "super_loop", "disabled.json")
        sop = fixtures()["phase1_ingestion"]
        sop["boundary_gate"]["requires_review_keywords"] = ["unmapped"]
        with self.assertRaisesRegex(ValidationError, "no explicit action class"):
            validate_policy(sop, fixtures()["action_classes"], "sop.json")

    def test_cross_field_artifact_constraints(self):
        samples = fixtures()
        cases = [
            ("pending_review_index", dict(samples["pending_review_index"], latest="")),
            ("phase1_delta", dict(samples["phase1_delta"], reconciliation={"scanned_paths": ["a"], "removed_paths": ["a"]})),
            ("execution_draft", dict(samples["execution_draft"], items=[dict(samples["execution_draft"]["items"][0], model="test")])),
        ]
        for kind, value in cases:
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                validate_data(value, kind, "artifact.json")

    def test_legacy_normalization_is_exact_and_does_not_mutate(self):
        samples = fixtures()
        originals = copy.deepcopy(samples)
        expected = copy.deepcopy(samples)
        expected["phase1_delta"].update(warnings=[], reconciliation={"scanned_paths": [], "removed_paths": []})
        expected["phase1_state"]["present_paths"] = ["inbox/a.md"]
        expected["world_state"]["last_run_at"] = None
        expected["worker_output"]["planned_actions"] = []
        for kind, value in samples.items():
            with self.subTest(kind=kind):
                normalized = validate_data(value, kind, "legacy.json")
                self.assertEqual(normalized, expected[kind])
                self.assertEqual(value, originals[kind])
        custom = validate_data(samples["phase1_state"], "phase1_state", "state.json", input_dir="other")
        self.assertEqual(custom["present_paths"], [])
        extension = dict(samples["context_package"], future_metadata={"note": "retained"})
        self.assertEqual(validate_data(extension, "context_package", "context.json"), extension)
        # Explicit null/wrong fields are never interpreted as missing legacy options.
        for kind, field in (("phase1_state", "present_paths"), ("phase1_delta", "warnings"),
                            ("phase1_delta", "reconciliation"), ("worker_output", "planned_actions")):
            with self.subTest(kind=kind, field=field), self.assertRaises(ValidationError):
                validate_data(dict(samples[kind], **{field: None}), kind, "legacy.json")


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Private readers receive canonical roots from their production callers;
        # macOS temporary directories may otherwise retain the /var alias.
        self.root = (Path(self.temp.name) / "workspace").resolve()
        init_workspace(self.root)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(self.temp.name) / "keys"),
                               "LOOPING_BOX_REVIEW_KEY": KEY}, clear=True).start()
        self.generate = patch("looping_box.model.generate_if_enabled", return_value=None).start()
        self.complete = patch("looping_box.model.complete", side_effect=AssertionError("Unexpected provider call")).start()
        patch("looping_box.model.is_enabled", return_value=False).start()
        patch("urllib.request.urlopen", side_effect=AssertionError("Live network forbidden")).start()

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def test_invalid_configs_fail_before_mutation_or_models(self):
        actions = [lambda: phase1.run_phase1(self.root, now=STAMP),
                   lambda: supervisor.run_supervisor(self.root, now=STAMP),
                   lambda: worker.run_worker(self.root, "context_builder", now=STAMP),
                   lambda: review.record_review(self.root, RID, "approved", note="Checked", now=STAMP)]
        configs = [("config/sops/phase1_ingestion.json", "phase1_ingestion"),
                   ("config/action_classes.json", "action_classes"), ("config/super_loop.json", "super_loop")]
        for relative, kind in configs:
            valid = fixtures()[kind]
            for malformed in ([], {**valid, "unknown_option": True}, "{broken"):
                path = self.write(relative, malformed)
                if malformed == "{broken":
                    path.write_text(malformed)
                before = snapshot(self.root)
                for action in actions:
                    with self.subTest(config=relative, malformed=malformed, action=action):
                        with self.assertRaisesRegex(ValidationError, "invalid_config"):
                            action()
                        self.assertEqual(snapshot(self.root), before)
                self.write(relative, valid)
        self.generate.assert_not_called()
        self.complete.assert_not_called()

    def test_missing_sop_and_optional_defaults(self):
        for relative in ("config/action_classes.json", "config/super_loop.json"):
            (self.root / relative).unlink()
        self.assertEqual(supervisor._read_config(self.root), supervisor.DEFAULT_CONFIG)
        classes = _read_action_config(self.root)
        self.assertEqual(classes["classes"], DEFAULT_ACTION_CLASSES["classes"])
        (self.root / "config/sops/phase1_ingestion.json").unlink()
        before = snapshot(self.root)
        with self.assertRaisesRegex(ValidationError, "workspace_uninitialized.*init"):
            phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(snapshot(self.root), before)

    def test_state_readers_never_reset_malformed_data(self):
        for relative, reader in (
            ("cache/state/phase1_state.json", lambda: phase1.run_phase1(self.root, now=STAMP)),
            (".world_state.json", lambda: supervisor.run_supervisor(self.root, now=STAMP)),
            ("cache/workers/context_builder/state.json", lambda: worker.run_worker(self.root, "context_builder", now=STAMP)),
            ("staging/pending_review.json", lambda: review.list_reviews(self.root)),
        ):
            for malformed in ([], {}, {"schema": "looping-box.future.v99"}):
                path = self.write(relative, malformed)
                read_only = relative == "staging/pending_review.json"
                error_type = ValueError if read_only else ValidationError
                error_pattern = (r"review_evidence_invalid: .*pending_review.json: invalid_artifact: .*\$"
                                 if read_only else r"invalid_artifact: .*\$")
                before = snapshot(self.root)
                lock_inode = (self.root / ".looping_box.lock").stat().st_ino
                with self.subTest(path=relative, value=malformed), self.assertRaisesRegex(error_type, error_pattern):
                    reader()
                after = snapshot(self.root)
                if not read_only:
                    before.pop(".looping_box.lock")
                    after.pop(".looping_box.lock")
                self.assertEqual(after, before)
                self.assertEqual((self.root / ".looping_box.lock").stat().st_ino, lock_inode)
                path.unlink()
        self.generate.assert_not_called()

    def test_invalid_context_is_rejected_before_execution_model_or_idle(self):
        (self.root / "inbox/note.md").write_text("Project docs.")
        phase1.run_phase1(self.root, now=STAMP)
        worker.run_worker(self.root, "context_builder", now=STAMP)
        worker.run_worker(self.root, "execution_engine", now=STAMP)
        self.generate.reset_mock()
        context_path = self.write("cache/workers/context_builder/context_package.json", [])
        state_path = self.root / "cache/workers/execution_engine/state.json"
        state = json.loads(state_path.read_text())
        state["last_context_sha256"] = hashlib.sha256(context_path.read_bytes()).hexdigest()
        self.write("cache/workers/execution_engine/state.json", state)
        before = snapshot(self.root)
        with self.assertRaisesRegex(ValidationError, "context_package.json"):
            worker.run_worker(self.root, "execution_engine", now=STAMP)
        self.assertEqual(snapshot(self.root), before)
        self.generate.assert_not_called()

    def test_invalid_nested_delta_is_preserved(self):
        bad = fixtures()["phase1_delta"]
        bad["changes"][0]["matched_routes"] = "not an array"
        self.write(DELTA, bad)
        original = (self.root / DELTA).read_bytes()
        result = worker.run_worker(self.root, "context_builder", now=STAMP)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("matched_routes" in e["message"] for e in result["errors"]))
        self.assertEqual((self.root / (DELTA + ".bad")).read_bytes(), original)
        self.assertFalse((self.root / DELTA).exists())
        self.assertFalse((self.root / "cache/workers/context_builder/state.json").exists())
        self.generate.assert_not_called()

    def test_supervisor_checks_later_worker_state_before_first_worker_runs(self):
        self.write(DELTA, fixtures()["phase1_delta"])
        self.write("cache/workers/execution_engine/state.json", [])
        before = {k: v for k, v in snapshot(self.root).items() if k != ".looping_box.lock"}
        lock_inode = (self.root / ".looping_box.lock").stat().st_ino
        with self.assertRaisesRegex(ValidationError, "execution_engine/state.json"):
            supervisor.run_supervisor(self.root, now=STAMP)
        self.assertEqual({k: v for k, v in snapshot(self.root).items() if k != ".looping_box.lock"}, before)
        self.assertEqual((self.root / ".looping_box.lock").stat().st_ino, lock_inode)
        self.generate.assert_not_called()

    def test_review_checks_entire_queue_before_recording_first_decision(self):
        (self.root / "inbox/held.txt").write_text("Deploy the site.")
        phase1.run_phase1(self.root, now=STAMP)
        first = review.list_reviews(self.root)[0]
        bad_ref = "staging/reviews/review-" + "c" * 16 + ".json"
        self.write("staging/pending_review.json", dict(fixtures()["pending_review_index"],
            latest=bad_ref, reviews=[first["path"], bad_ref]))
        self.write(bad_ref, [])
        before = {k: v for k, v in snapshot(self.root).items() if k != ".looping_box.lock"}
        lock_inode = (self.root / ".looping_box.lock").stat().st_ino
        with self.assertRaisesRegex(ValueError, "review_evidence_invalid: .*review-cccc"):
            review.record_review(self.root, first["review_id"], "rejected", note="Checked", now=STAMP)
        self.assertEqual({k: v for k, v in snapshot(self.root).items() if k != ".looping_box.lock"}, before)
        self.assertEqual((self.root / ".looping_box.lock").stat().st_ino, lock_inode)
        self.complete.assert_not_called()

    def test_invalid_review_and_signed_record_cannot_clear_gate(self):
        self.write("staging/pending_review.json", fixtures()["pending_review_index"])
        self.write(PAYLOAD, [])
        before = snapshot(self.root)
        with self.assertRaisesRegex(ValueError, "review_evidence_invalid: .*review-.*json"):
            review.list_reviews(self.root)
        self.assertEqual(snapshot(self.root), before)
        # Restore a valid empty queue, then stage real identity-bound evidence.
        self.write("staging/pending_review.json", dict(fixtures()["pending_review_index"], reviews=[], latest=""))
        (self.root / "inbox/held.txt").write_text("Deploy the site.")
        phase1.run_phase1(self.root, now=STAMP)
        current = review.list_reviews(self.root)[0]
        record = review.record_review(self.root, current["review_id"], "rejected", note="fixture", now=STAMP)
        record["interactive"] = "yes"
        record["signature"] = sign_record(record, KEY.encode())
        self.write(f"staging/rejections/{current['review_id']}.json", record)
        self.assertEqual(decision_state(self.root / "staging", current["review_id"]), (None, True))
        phase1.run_phase1(self.root, now=STAMP)
        self.assertTrue(review.list_reviews(self.root)[0]["unverifiable_decision"])

    def test_blank_decision_note_rejected_before_key_or_payload_mutation(self):
        before = snapshot(self.root)
        with self.assertRaisesRegex(ValidationError, r"\$\.note"):
            review.record_review(self.root, RID, "rejected", note=" \n", now=STAMP)
        self.assertEqual(snapshot(self.root), before)
        self.complete.assert_not_called()

    def test_audit_shape_is_checked_even_with_valid_chain_hash(self):
        event = fixtures()["audit_event"]
        event["changed"] = True
        del event["hash"]
        event["hash"] = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
        path = self.root / "logs/transactions/phase1.jsonl"
        path.write_text(json.dumps(event) + "\n")
        self.assertEqual(verify_audit_chain(path), 1)
        with self.assertRaisesRegex(ValidationError, r"phase1.jsonl:1: \$.changed"):
            report.build_report(self.root)

    def test_review_cli_and_doctor_report_invalid_evidence(self):
        self.write("staging/pending_review.json", [])
        before = snapshot(self.root)
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["review", "--root", str(self.root), "list"]), redirect_stderr(stderr):
            self.assertEqual(review.main(), 1)
        self.assertIn("invalid_artifact", stderr.getvalue())
        findings = doctor.run_doctor(self.root)
        self.assertTrue(any(level == "fail" and "invalid_artifact" in message for level, message, _ in findings))
        self.assertEqual(snapshot(self.root), before)

    def test_real_generated_outputs_match_contracts(self):
        (self.root / "inbox/notes.md").write_text("docs summary")
        delta = phase1.run_phase1(self.root, now=STAMP)
        validate_data(delta, "phase1_delta", "generated delta")
        result = supervisor.run_supervisor(self.root, now=STAMP)
        validate_data(result, "supervisor_run", "generated supervisor")
        for relative, kind in (
            ("cache/state/phase1_state.json", "phase1_state"), (".world_state.json", "world_state"),
            ("cache/workers/context_builder/context_package.json", "context_package"),
            ("cache/workers/execution_engine/draft.json", "execution_draft"),
            ("cache/workers/context_builder/state.json", "worker_state"),
            ("cache/workers/execution_engine/last_output.json", "worker_output"),
        ):
            with self.subTest(kind=kind):
                read_validated(self.root / relative, kind)
        (self.root / "inbox/held.md").write_text("please deploy")
        phase1.run_phase1(self.root, now=STAMP)
        index = read_pending_review_index(self.root / "staging/pending_review.json")
        payload = read_validated(self.root / index["latest"], "review_payload")
        result = review.run_verifier(self.root, payload["review_id"], now=STAMP)
        validate_data(result, "verifier_result", "generated verifier")
        record = review.record_review(self.root, payload["review_id"], "rejected", note="Keep held", now=STAMP)
        validate_data(record, "review_record", "generated decision")
        for path in (self.root / "logs/transactions").glob("*.jsonl"):
            self.assertIsNone(verify_audit_chain(path))
            for line in path.read_text().splitlines():
                validate_data(json.loads(line), "audit_event", str(path))
        limits = fixtures()["super_loop"]
        limits["max_files_per_cycle"] = 1
        self.write("config/super_loop.json", limits)
        for name in ("one", "two"):
            (self.root / f"inbox/{name}.md").write_text(name)
        phase1.run_phase1(self.root, now=STAMP)
        supervisor.run_supervisor(self.root, now=STAMP)
        read_validated(self.root / "cache/supervisor/blocked.json", "supervisor_blocked")


if __name__ == "__main__":
    unittest.main()
