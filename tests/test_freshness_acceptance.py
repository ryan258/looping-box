"""T07 / A17–A21: current evidence before disclosure, bounded offline model stubs."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from looping_box import model, phase1, review, scaffold, worker
from looping_box.eligibility import SourceReingestRequired
from looping_box.validation import validate_data

STAMP = "2026-10-13T12:00:00Z"
KEY = "fake-api-key-SECRET-SENTINEL"


class FreshnessAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.number = 0
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.base / "keys"),
                                      "LOOPING_BOX_REVIEW_KEY": "fake-review-key"}, clear=True)
        env.start(); self.addCleanup(env.stop)
        model._env_loaded_for.clear()
        self.addCleanup(model._env_loaded_for.clear)
        self.calls = []
        transport = patch.object(model, "_transport", side_effect=AssertionError("live provider forbidden"))
        transport.start(); self.addCleanup(transport.stop)
        self.new_workspace()

    def new_workspace(self):
        self.number += 1
        self.root = self.base / f"workspace-{self.number}"
        scaffold.init_workspace(self.root)
        return self.root

    def ingest(self, name="one.txt", text="Project docs one.", now=STAMP):
        (self.root / "inbox" / name).write_text(text)
        return phase1.run_phase1(self.root, now=now)

    def run_worker(self, name="context_builder", **kwargs):
        out = worker.run_worker(self.root, name, now=STAMP, **kwargs)
        validate_data(out, "worker_output", "output", worker_id=name)
        return out

    def seed(self, count=1, execute=False):
        self.ingest()
        if count == 2: self.ingest("two.txt", "Project docs two.")
        self.assertEqual(self.run_worker()["status"], "complete")
        if execute: self.assertEqual(self.run_worker("execution_engine")["status"], "complete")

    def enable(self, role="execution_engine", model_id="stub/execution"):
        os.environ["OPENROUTER_API_KEY"] = KEY
        os.environ["MODEL_" + role.upper()] = model_id

    def transport(self, content="MODEL DRAFT"):
        def send(url, headers, body, timeout):
            data = json.loads(body)
            self.calls.append(data)
            self.assertEqual(headers["Authorization"], "Bearer " + os.environ["OPENROUTER_API_KEY"])
            self.assertTrue(url.endswith("/chat/completions"))
            self.assertEqual(data["temperature"], 0.0)
            if isinstance(content, Exception): raise content
            value = content(data) if callable(content) else content
            return json.dumps({"model": data["model"], "choices": [{"message": {"content": value}}]})
        return patch.object(model, "_transport", side_effect=send)

    def batch(self, *names):
        return json.dumps({"drafts": [{"relative_path": "inbox/" + name, "draft": "DRAFT " + name} for name in names]})

    def saved(self, wid="execution_engine"):
        directory = self.root / "cache/workers" / wid
        return {name: (directory / name).read_bytes() if (directory / name).exists() else None
                for name in (("draft.json", "draft.md", "state.json") if wid == "execution_engine"
                             else ("context_package.json", "context_package.md", "state.json"))}

    def package(self):
        return json.loads((self.root / "cache/workers/context_builder/context_package.json").read_text())

    def test_execution_outage_then_new_file_preserves_both_once(self):
        self.seed(); self.enable()
        with self.transport(RuntimeError("offline outage")):
            self.assertEqual(self.run_worker("execution_engine")["status"], "failed")
        self.ingest("two.txt", "Project docs two.")
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual([x["relative_path"] for x in self.package()["items"]], ["inbox/one.txt", "inbox/two.txt"])
        self.calls.clear()
        with self.transport(self.batch("one.txt", "two.txt")):
            self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
            self.assertEqual(self.run_worker("execution_engine")["status"], "idle")
        self.assertEqual(len(self.calls), 1)

    def test_latest_numeric_suffix_beats_lexical_filename_order(self):
        for number in range(11): self.ingest(text=f"Project docs revision {number}.")
        self.assertEqual(self.run_worker()["status"], "complete")
        items = self.package()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["excerpt"], "Project docs revision 10.")
        self.assertTrue(items[0]["source_delta"].endswith("-11.json"))

    def test_latest_timestamp_beats_file_order_and_removal_can_be_superseded(self):
        self.ingest(text="Project docs later.", now="2026-10-13T13:00:00Z")
        self.ingest(text="Project docs older.", now=STAMP)
        (self.root / "inbox/one.txt").write_text("Project docs later.")
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual(self.package()["items"][0]["excerpt"], "Project docs later.")
        self.new_workspace()
        self.ingest(); (self.root / "inbox/one.txt").unlink()
        phase1.run_phase1(self.root, now=STAMP)
        self.ingest(text="Project docs restored new version.")
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual(self.package()["items"][0]["excerpt"], "Project docs restored new version.")

    def test_fresh_same_path_replaces_undrafted_excerpt(self):
        self.seed()
        self.ingest(text="Project docs replacement.")
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual([x["excerpt"] for x in self.package()["items"]], ["Project docs replacement."])

    def test_new_hold_suppresses_queued_and_carried_clean_content(self):
        for carry in (False, True):
            self.new_workspace()
            self.ingest(text="Project docs OLD-CLEAN-SENTINEL.")
            if carry: self.run_worker()
            self.ingest(text="please deploy HELD-SENTINEL")
            self.ingest("two.txt", "Project docs clean survivor.")
            self.enable("context_builder", "stub/context"); self.enable()
            self.calls.clear()
            with self.transport():
                self.assertEqual(self.run_worker()["status"], "blocked")
                self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
            self.assertEqual(self.package()["items"][0]["relative_path"], "inbox/two.txt")
            self.assertEqual(self.package()["blocked_inputs"][0]["relative_path"], "inbox/one.txt")
            self.assertEqual(len(self.calls), 1)
            sent = json.dumps(self.calls)
            self.assertNotIn("OLD-CLEAN-SENTINEL", sent); self.assertNotIn("HELD-SENTINEL", sent)
            draft = json.loads((self.root / "cache/workers/execution_engine/draft.json").read_text())
            self.assertEqual(draft["held_for_review"], ["inbox/one.txt"])
            os.environ.pop("MODEL_CONTEXT_BUILDER")

    def test_carried_changed_missing_or_newly_gated_source_blocks_new_batch(self):
        for change in ("bytes", "missing", "policy"):
            self.new_workspace(); self.seed()
            prior = self.saved("context_builder")
            if change == "bytes": (self.root / "inbox/one.txt").write_text("different source")
            if change == "missing": (self.root / "inbox/one.txt").unlink()
            if change == "policy": self.gate_docs()
            # Inject a valid unrelated delta without rescanning the carried source.
            fresh = self.root / "cache/deltas/fresh.json"
            data = json.loads(next((self.root / "cache/deltas").glob("*.json")).read_text())
            data["changes"] = []; data["reconciliation"] = {"scanned_paths": [], "removed_paths": []}
            data["summary"] = {"scanned": 0, "changed": 0, "skipped": 0, "requires_review": False}
            data["skipped"] = []
            data["delta_path"] = "cache/deltas/fresh.json"
            fresh.write_text(json.dumps(data))
            with patch.object(model, "generate_if_enabled") as generate:
                output = self.run_worker()
            self.assertEqual(output["status"], "failed", change)
            self.assertEqual(output["errors"][-1]["code"], "source_reingest_required")
            self.assertEqual(self.saved("context_builder"), prior)
            self.assertTrue(fresh.exists()); generate.assert_not_called()

    def gate_docs(self):
        path = self.root / "config/sops/phase1_ingestion.json"
        sop = json.loads(path.read_text()); sop["boundary_gate"]["requires_review_keywords"].append("docs")
        path.write_text(json.dumps(sop))
        path = self.root / "config/action_classes.json"
        policy = json.loads(path.read_text())
        policy["classes"]["safe_local_transform"].remove("docs")
        policy["classes"]["review_required"].append("docs")
        validate_data(policy, "action_classes", str(path))
        path.write_text(json.dumps(policy))

    def test_policy_reingestion_and_removal_reconcile_undrafted_work(self):
        self.seed(); self.gate_docs()
        self.assertEqual(self.run_worker()["status"], "failed")
        phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(self.run_worker()["status"], "blocked")
        self.assertEqual(self.package()["items"], [])
        self.new_workspace(); self.seed()
        (self.root / "inbox/one.txt").unlink()
        self.assertEqual(self.run_worker()["status"], "failed")
        delta = phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(delta["changes"], [])
        self.assertEqual(delta["reconciliation"]["removed_paths"], ["inbox/one.txt"])
        with patch.object(model, "generate_if_enabled") as generate:
            self.assertEqual(self.run_worker()["status"], "complete")
            self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
        self.assertEqual(self.package()["items"], []); generate.assert_not_called()

    def test_direct_worker_and_shared_predicate_reject_stale_before_idle(self):
        for change in ("bytes", "missing", "policy", "excerpt", "routes"):
            self.new_workspace(); self.seed(execute=True)
            prior = self.saved()
            if change == "bytes": (self.root / "inbox/one.txt").write_text("changed")
            if change == "missing": (self.root / "inbox/one.txt").unlink()
            if change == "policy": self.gate_docs()
            if change in {"excerpt", "routes"}:
                path = self.root / "cache/workers/context_builder/context_package.json"
                data = json.loads(path.read_text())
                data["items"][0]["excerpt" if change == "excerpt" else "matched_routes"] = "forged" if change == "excerpt" else []
                path.write_text(json.dumps(data))
            with self.assertRaises(SourceReingestRequired): worker.execution_needs_run(self.root)
            with patch.object(model, "generate_if_enabled") as generate:
                output = self.run_worker("execution_engine")
            self.assertEqual(output["status"], "failed", change)
            self.assertEqual(output["errors"][0]["code"], "source_reingest_required")
            self.assertEqual(self.saved(), prior); generate.assert_not_called()

    def test_all_sources_are_checked_before_first_request(self):
        self.seed(count=2); self.enable()
        (self.root / "inbox/two.txt").write_text("please deploy stale second item")
        with patch.object(model, "generate_if_enabled") as generate:
            output = self.run_worker("execution_engine")
        self.assertEqual(output["status"], "failed"); generate.assert_not_called()

    def test_model_id_invalidates_but_api_key_does_not(self):
        self.seed(execute=True)
        self.assertFalse(worker.execution_needs_run(self.root))
        self.enable()
        self.assertTrue(worker.execution_needs_run(self.root))
        with self.transport():
            self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
            self.assertFalse(worker.execution_needs_run(self.root))
            os.environ["OPENROUTER_API_KEY"] = "different-fake-key"
            self.assertFalse(worker.execution_needs_run(self.root))
            self.assertEqual(self.run_worker("execution_engine")["status"], "idle")
            os.environ["MODEL_EXECUTION_ENGINE"] = "stub/second"
            self.assertTrue(worker.execution_needs_run(self.root))
            self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
        self.assertEqual(len(self.calls), 2)

    def test_strict_batch_mapping_never_publishes_partial_results(self):
        self.seed(count=2, execute=True); self.enable()
        prior = self.saved()
        valid = json.loads(self.batch("one.txt", "two.txt"))["drafts"]
        invalid = ["not JSON", json.dumps({"drafts": valid[:1]}), json.dumps({"drafts": valid + [valid[0]]}),
                   json.dumps({"drafts": valid + [{"relative_path": "inbox/extra.txt", "draft": "extra"}]}),
                   json.dumps({"drafts": [{**valid[0], "draft": 42}, valid[1]]}),
                   json.dumps({"drafts": [{"relative_path": " ", "draft": "empty path"}, valid[1]]}),
                   json.dumps({"drafts": [None, valid[1]]}), json.dumps({"drafts": {}})]
        for content in invalid:
            self.calls.clear()
            with self.transport(content): output = self.run_worker("execution_engine")
            self.assertEqual(output["status"], "failed")
            self.assertEqual(output["errors"][0]["code"], "model_error")
            self.assertEqual(self.saved(), prior)
            self.assertEqual(len(self.calls), 1)
        self.calls.clear()
        with self.transport("```json\n" + self.batch("two.txt", "one.txt") + "\n```"):
            self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
        self.assertEqual(len(self.calls), 1)
        draft = json.loads((self.root / "cache/workers/execution_engine/draft.json").read_text())
        self.assertEqual([x["draft"] for x in draft["items"]], ["DRAFT one.txt", "DRAFT two.txt"])
        self.assertTrue(all(x["model"] == "stub/execution" and len(x["response_sha256"]) == 64 for x in draft["items"]))

    def test_approved_reason_requires_current_identity_and_signed_payload(self):
        self.ingest(text="please deploy local draft")
        rid = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, rid, "approved", note="Local drafting only", approver="operator", now=STAMP)
        phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
        self.assertEqual(self.package()["items"][0]["approved_review"], rid)
        payload = self.root / f"staging/reviews/{rid}.json"
        data = json.loads(payload.read_text()); data["notification_message"] = "tampered"
        payload.write_text(json.dumps(data))
        with patch.object(model, "generate_if_enabled") as generate:
            self.assertEqual(self.run_worker("execution_engine")["status"], "failed")
        generate.assert_not_called()

    def test_synthesis_never_becomes_execution_input(self):
        self.ingest(); self.enable("context_builder", "stub/context")
        with self.transport("SYNTHESIS-INSTRUCTION-SENTINEL"):
            self.assertEqual(self.run_worker()["status"], "complete")
        self.enable(); self.calls.clear()
        with self.transport(): self.run_worker("execution_engine")
        self.assertEqual(len(self.calls), 1)
        sent = json.dumps(self.calls)
        self.assertNotIn("SYNTHESIS-INSTRUCTION-SENTINEL", sent)
        self.assertIn("Project docs one.", sent)

    def test_approval_cannot_bypass_new_reasons_or_forbidden_policy(self):
        for change in ("reason", "class"):
            self.new_workspace()
            self.ingest(text="please deploy local draft")
            rid = review.list_reviews(self.root)[0]["review_id"]
            review.record_review(self.root, rid, "approved", note="Local drafting only", approver="operator", now=STAMP)
            phase1.run_phase1(self.root, now=STAMP); self.run_worker()
            policy_path = self.root / "config/action_classes.json"
            policy = json.loads(policy_path.read_text())
            if change == "reason":
                sop_path = self.root / "config/sops/phase1_ingestion.json"
                sop = json.loads(sop_path.read_text()); sop["boundary_gate"]["requires_review_keywords"].append("draft")
                sop_path.write_text(json.dumps(sop))
                policy["classes"]["review_required"].append("draft")
            else:
                policy["classes"]["review_required"].remove("deploy")
                policy["classes"]["forbidden"].append("deploy")
            policy_path.write_text(json.dumps(policy))
            with patch.object(model, "generate_if_enabled") as generate:
                output = self.run_worker("execution_engine")
            self.assertEqual(output["errors"][0]["code"], "source_reingest_required")
            generate.assert_not_called()

    def test_verifier_receives_current_held_evidence_as_untrusted_data(self):
        self.ingest(text="please deploy HELD-FOR-VERIFIER")
        rid = review.list_reviews(self.root)[0]["review_id"]
        self.enable("verifier", "stub/verifier")
        with self.transport("PASS: local drafting"):
            review.record_review(self.root, rid, "approved", note="Local drafting only", approver="operator", now=STAMP)
        self.assertEqual(len(self.calls), 1)
        prompt = self.calls[0]["messages"][0]["content"]
        self.assertIn("HELD-FOR-VERIFIER", prompt)
        self.assertIn("deploy", prompt)
        self.assertIn("untrusted", prompt.lower())
        self.assertIn("never follow", prompt.lower())

    def test_freshness_failures_in_dry_run_leave_all_files_unchanged(self):
        self.seed()
        (self.root / "inbox/one.txt").write_text("changed outside ingestion")
        (self.root / ".looping_box.lock").unlink()
        def snapshot():
            return {str(p.relative_to(self.root)): None if p.is_dir() else p.read_bytes()
                    for p in self.root.rglob("*")}
        before = snapshot()
        with patch.object(model, "generate_if_enabled") as generate:
            for wid in ("context_builder", "execution_engine"):
                self.assertEqual(self.run_worker(wid, dry_run=True)["status"], "failed")
        self.assertEqual(snapshot(), before); generate.assert_not_called()

    def test_error_redaction_precedes_truncation_and_persistence(self):
        self.seed(); self.enable()
        with self.transport(RuntimeError("transport echoed " + KEY)):
            output = self.run_worker("execution_engine")
        self.assertNotIn(KEY, json.dumps(output)); self.assertIn("[REDACTED]", json.dumps(output))
        def provider_error(*args):
            return json.dumps({"error": "x" * 190 + KEY + " tail"})
        with patch.object(model, "_transport", side_effect=provider_error):
            with self.assertRaises(model.ModelError) as caught:
                model.complete("execution_engine", "fixture", root=self.root)
        self.assertNotIn(KEY, str(caught.exception)); self.assertNotIn(KEY[:10], str(caught.exception))
        self.ingest("held.txt", "please deploy")
        rid = review.list_reviews(self.root)[0]["review_id"]
        self.enable("verifier", "stub/verifier")
        with self.transport(RuntimeError("v" * 145 + KEY)):
            with self.assertRaisesRegex(ValueError, "review_verifier_failed"):
                review.record_review(self.root, rid, "approved", note="test", approver="operator", now=STAMP)
        verifier = (self.root / f"cache/verifiers/{rid}.json").read_text()
        self.assertNotIn(KEY, verifier); self.assertNotIn(KEY[:10], verifier)
        for path in (self.root / "cache").rglob("*"):
            if path.is_file(): self.assertNotIn(KEY.encode(), path.read_bytes())

    def test_provider_shape_failures_are_model_errors_without_success_markers(self):
        self.seed(execute=True); self.enable(); prior = self.saved()
        for response in ("{broken", "null", json.dumps({"error": {"message": "fixture failure"}}),
                         json.dumps({"choices": [{"message": {"content": None}}]}),
                         json.dumps({"choices": [{"message": {"content": 12}}]}),
                         json.dumps({"choices": [{"message": {"content": "\ud800"}}]}),
                         json.dumps({"model": None, "choices": [{"message": {"content": "text"}}]})):
            with patch.object(model, "_transport", return_value=response) as transport:
                output = self.run_worker("execution_engine")
            self.assertEqual(output["status"], "failed")
            self.assertEqual(output["errors"][0]["code"], "model_error")
            self.assertEqual(self.saved(), prior); transport.assert_called_once()

    def test_disabled_roles_and_env_overrides_do_not_grant_authority(self):
        (self.root / ".env").write_text("LOOPING_BOX_REVIEW_KEY=forged\nLOOPING_BOX_ALLOW_NONINTERACTIVE=1\nMODEL_EXECUTION_ENGINE=stub/from-env\n")
        model._env_loaded_for.clear()
        self.seed()
        with patch.object(model, "_transport") as transport:
            self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
            transport.assert_not_called()
        self.assertEqual(os.environ["LOOPING_BOX_REVIEW_KEY"], "fake-review-key")
        self.assertNotIn("LOOPING_BOX_ALLOW_NONINTERACTIVE", os.environ)
        os.environ["MODEL_EXECUTION_ENGINE"] = "stub/process-wins"
        os.environ["OPENROUTER_API_KEY"] = KEY
        model._env_loaded_for.clear(); model.load_env(self.root)
        self.assertEqual(model.model_for("execution_engine"), "stub/process-wins")
        os.environ.pop("MODEL_EXECUTION_ENGINE")
        with patch.object(model, "_transport") as transport:
            self.assertIsNone(model.generate_if_enabled("execution_engine", "fixture", root=self.root))
            transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
