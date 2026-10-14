"""T05 / A09–A14: offline evidence, source/policy, signed decisions and real TTYs."""
import hashlib
import hmac
import json
import os
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from looping_box import _util, doctor, model, phase1, report, review, scaffold, supervisor
from looping_box.validation import validate_data
from looping_box.paths import UnsafePathError

STAMP = "2026-10-13T12:00:00Z"
KEY = "fake-review-key"


class ReviewAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.sequence = 0
        self.env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT / "src"),
                    "XDG_CONFIG_HOME": str(self.base / "keys"), "LOOPING_BOX_REVIEW_KEY": KEY}
        env = patch.dict(os.environ, self.env, clear=True)
        env.start()
        self.addCleanup(env.stop)
        network = patch("looping_box.model._transport", side_effect=AssertionError("live provider forbidden"))
        self.transport = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(model._env_loaded_for.clear)

    def stage(self, text="please deploy"):
        self.sequence += 1
        root = self.base / f"workspace-{self.sequence}"
        scaffold.init_workspace(root)
        (root / "inbox/note.md").write_text(text)
        phase1.run_phase1(root, now=STAMP)
        item = review.list_reviews(root)[0]
        return root, item["review_id"], root / item["path"]

    def decide(self, root, rid, decision="approved", **kwargs):
        return review.record_review(root, rid, decision, note="Checked local drafting", approver="operator",
                                    interactive=False, now=STAMP, **kwargs)

    def command(self, root, rid):
        return [sys.executable, "-m", "looping_box", "review", "--root", str(root),
                "approve", rid, "--note", "Checked local drafting"]

    def test_absent_index_with_payload_is_unavailable_but_explicit_empty_is_clear(self):
        root, rid, payload = self.stage()
        index = root / "staging/pending_review.json"
        index.unlink()
        for operation in (review.list_reviews, supervisor.status_summary):
            with self.subTest(operation=operation.__name__), self.assertRaisesRegex(ValueError, "review_state_unavailable"):
                operation(root)
        result = report.report_result(root)
        self.assertTrue(result.failed)
        self.assertIn("review_state_unavailable", result.text)
        self.assertIn("unavailable", result.text)
        cli_result = subprocess.run([sys.executable, "-m", "looping_box", "report", "--root", str(root)],
                                    env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(cli_result.returncode, 1, cli_result.stdout + cli_result.stderr)
        self.assertTrue(any(level == "fail" and "review_state_unavailable" in message
                            for level, message, _ in doctor.run_doctor(root)))
        index.write_text(json.dumps({"schema": "looping-box.pending-review-index.v1", "generated_at": STAMP,
                                     "latest": "", "reviews": []}))
        self.assertEqual(review.list_reviews(root), [])
        self.assertTrue(payload.exists())
        with self.assertRaisesRegex(ValueError, "unknown review"):
            review.show_review(root, rid)

    def test_missing_corrupt_and_wrong_identity_evidence_never_disappears_from_queue(self):
        for variant in ("missing", "list", "version", "identity", "index_json", "index_version"):
            root, rid, payload = self.stage()
            index = root / "staging/pending_review.json"
            if variant == "missing":
                payload.unlink()
            elif variant == "list":
                payload.write_text("[]")
            elif variant in {"version", "identity"}:
                data = json.loads(payload.read_text())
                data["schema" if variant == "version" else "review_id"] = "wrong" if variant == "version" else "review-" + "a" * 16
                payload.write_text(json.dumps(data))
            elif variant == "index_json":
                index.write_text("{broken")
            else:
                data = json.loads(index.read_text()); data["schema"] = "wrong"
                index.write_text(json.dumps(data))
            before = {p: p.read_bytes() for p in (root / "staging").rglob("*") if p.is_file()}
            with self.subTest(variant=variant):
                for operation in (review.list_reviews, supervisor.status_summary):
                    with self.assertRaisesRegex(ValueError, "review_evidence_invalid"):
                        operation(root)
                report_result = report.report_result(root)
                self.assertTrue(report_result.failed)
                self.assertIn("review_evidence_invalid", report_result.text)
                self.assertIn("unavailable", report_result.text)
                with self.assertRaisesRegex(ValueError, "review_evidence_invalid"):
                    review.show_review(root, rid)
                for command in (["review", "--root", str(root), "list"], ["status", "--root", str(root)],
                                ["report", "--root", str(root)]):
                    result = subprocess.run([sys.executable, "-m", "looping_box", *command], env=self.env,
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_source_and_payload_changes_block_before_model_and_keep_pending(self):
        for change in ("excerpt", "routes", "size", "class", "reasons", "bytes", "missing", "sop"):
            root, rid, payload = self.stage()
            data = json.loads(payload.read_text())
            if change == "excerpt": data["source_items"][0]["excerpt"] = "forged excerpt"
            if change == "routes": data["source_items"][0]["matched_routes"] = ["invented"]
            if change == "size": data["source_items"][0]["size_bytes"] += 1
            if change == "class": data["action_class"] = "safe_local_transform"
            if change == "reasons": data["risk_reasons"] = ["send"]
            if change in {"excerpt", "routes", "size", "class", "reasons"}: payload.write_text(json.dumps(data))
            if change == "bytes": (root / "inbox/note.md").write_text("please deploy different bytes")
            if change == "missing": (root / "inbox/note.md").unlink()
            if change == "sop":
                sop = root / "config/sops/phase1_ingestion.json"
                config = json.loads(sop.read_text()); config["max_excerpt_chars"] = 3
                sop.write_text(json.dumps(config))
            before, index = payload.read_bytes(), (root / "staging/pending_review.json").read_bytes()
            with self.subTest(change=change), patch.object(review, "_model_review_check") as model_check:
                with self.assertRaisesRegex(ValueError, "review_(source_changed|evidence_invalid)"):
                    self.decide(root, rid)
                model_check.assert_not_called()
                self.assertFalse((root / f"staging/approvals/{rid}.json").exists())
                self.assertEqual(payload.read_bytes(), before)
                self.assertEqual((root / "staging/pending_review.json").read_bytes(), index)

    def test_rejection_can_record_removed_source(self):
        root, rid, payload = self.stage()
        (root / "inbox/note.md").unlink()
        record = self.decide(root, rid, "rejected")
        self.assertEqual(record["decision"], "rejected")
        self.assertEqual(json.loads(payload.read_text())["verifier"]["status"], "not_applicable")
        self.assertEqual(_util.decision_state(root / "staging", rid), ("rejected", False))

    def test_oversized_source_is_rechecked_without_decoding(self):
        root, _, _ = self.stage()
        sop = root / "config/sops/phase1_ingestion.json"
        config = json.loads(sop.read_text()); config["max_file_bytes"] = 10
        sop.write_text(json.dumps(config))
        (root / "inbox/note.md").write_bytes(b"\xff" * 11)
        phase1.run_phase1(root, now=STAMP)
        rid = review.list_reviews(root)[0]["review_id"]
        with patch.object(phase1, "_decode_bytes", side_effect=AssertionError("oversized decode")):
            self.assertEqual(self.decide(root, rid)["decision"], "approved")

    def test_model_failure_preserves_failed_result_and_pending_payload(self):
        for response in ("FAIL: not appropriate", model.ModelError("offline failure")):
            root, rid, payload = self.stage()
            before = payload.read_bytes()
            with patch.object(model, "is_enabled", return_value=True), patch.object(model, "complete") as complete:
                if isinstance(response, Exception): complete.side_effect = response
                else: complete.return_value = {"model": "stub", "text": response}
                with self.assertRaisesRegex(ValueError, "review_verifier_failed"):
                    self.decide(root, rid)
            result = json.loads((root / f"cache/verifiers/{rid}.json").read_text())
            validate_data(result, "verifier_result", "failed verifier")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(payload.read_bytes(), before)
            self.assertEqual(review.list_reviews(root)[0]["review_id"], rid)
            self.assertFalse((root / f"staging/approvals/{rid}.json").exists())
        self.transport.assert_not_called()

    def test_model_pass_is_advisory_after_current_source_checks(self):
        root, rid, _ = self.stage()
        with patch.object(model, "is_enabled", return_value=True), patch.object(model, "complete", return_value={
            "model": "offline-stub", "text": "  pAsS: local drafting is acceptable"}) as complete:
            record = self.decide(root, rid)
        complete.assert_called_once()
        self.assertEqual(record["decision"], "approved")
        self.assertEqual(_util.decision_state(root / "staging", rid), ("approved", False))
        self.transport.assert_not_called()

    def test_empty_pristine_review_queue_is_available_without_creating_files(self):
        root = self.base / "pristine"
        root.mkdir()
        self.assertEqual(review.list_reviews(root), [])
        self.assertEqual(list(root.iterdir()), [])

    def test_payload_modification_after_signing_restores_unverifiable_pending_warning(self):
        root, rid, payload = self.stage()
        self.decide(root, rid)
        data = json.loads(payload.read_text())
        data["notification_message"] = "modified after the decision"
        payload.write_text(json.dumps(data))
        before = payload.read_bytes()
        delta = phase1.run_phase1(root, now=STAMP)
        self.assertEqual(delta["boundary_gate"]["status"], "pending_review")
        self.assertEqual(delta["warnings"][0]["code"], "decision_unverifiable")
        self.assertTrue(review.list_reviews(root)[0]["unverifiable_decision"])
        self.assertEqual(payload.read_bytes(), before)

    def test_payload_hash_signature_and_verifier_evidence_are_all_required(self):
        for change in ("payload", "signature", "note", "key", "verifier_missing", "verifier_failed", "verifier_identity"):
            root, rid, payload = self.stage()
            record = self.decide(root, rid)
            path = root / f"staging/approvals/{rid}.json"
            if change == "payload":
                data = json.loads(payload.read_text()); data["notification_message"] = "modified after signing"
                payload.write_text(json.dumps(data))
            if change == "signature": record["signature"] = "0" * 64
            if change == "note": record["note"] = "edited after signing"
            if change in {"signature", "note"}: path.write_text(json.dumps(record))
            verifier = root / f"cache/verifiers/{rid}.json"
            if change == "verifier_missing": verifier.unlink()
            if change in {"verifier_failed", "verifier_identity"}:
                data = json.loads(verifier.read_text())
                if change == "verifier_failed": data["checks"][0]["status"] = "failed"
                else: data["review_id"] = "review-" + "a" * 16
                verifier.write_text(json.dumps(data))
            with self.subTest(change=change), patch.dict(os.environ, {"LOOPING_BOX_REVIEW_KEY": "wrong" if change == "key" else KEY}):
                self.assertEqual(_util.decision_state(root / "staging", rid), (None, True))

    def test_valid_rejection_wins_conflicting_signed_approval(self):
        root, rid, _ = self.stage()
        approval = self.decide(root, rid)
        rejection = {**approval, "decision": "rejected", "verifier_result": None}
        rejection["signature"] = _util.sign_record(rejection, KEY.encode())
        (root / f"staging/rejections/{rid}.json").write_text(json.dumps(rejection))
        self.assertEqual(_util.decision_state(root / "staging", rid), ("rejected", False))
        result = phase1.run_phase1(root, now=STAMP)
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["skipped"][0]["reason"], "review_decision_recorded")

    def test_identity_and_hmac_wire_format_are_unchanged(self):
        root, rid, payload = self.stage("please deploy café")
        items = json.loads(payload.read_text())["source_items"]
        basis = {"items": [{"relative_path": x["relative_path"], "sha256": x["sha256"], "review_reasons": x["review_reasons"]} for x in items]}
        expected = "review-" + hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()[:16]
        self.assertEqual(rid, expected)
        record = self.decide(root, rid)
        body = {key: value for key, value in record.items() if key != "signature"}
        expected_sig = hmac.new(KEY.encode(), json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()
        self.assertEqual(record["signature"], expected_sig)
        self.assertEqual(record["payload_sha256"], hashlib.sha256(payload.read_bytes()).hexdigest())
        validate_data(record, "review_record", "record")

    def test_notes_override_type_and_attribution_fail_before_mutation(self):
        root, rid, _ = self.stage()
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        for kwargs in ({"note": " "}, {"note": "ok", "approver": ""}, {"note": "ok", "interactive": "false"},
                       {"note": "ok", "allow_blocked": "false"}):
            for decision in ("approved", "rejected"):
                with self.subTest(kwargs=kwargs, decision=decision), self.assertRaises(ValueError):
                    review.record_review(root, rid, decision, **kwargs)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_blocked_requires_override_and_forbidden_never_approves(self):
        root, rid, _ = self.stage("production secret")
        with self.assertRaisesRegex(ValueError, "blocked review"):
            self.decide(root, rid)
        self.assertEqual(self.decide(root, rid, allow_blocked=True)["decision"], "approved")
        root, rid, _ = self.stage()
        classes = root / "config/action_classes.json"
        data = json.loads(classes.read_text())
        data["classes"]["review_required"].remove("deploy")
        data["classes"]["forbidden"].append("deploy")
        classes.write_text(json.dumps(data))
        with patch.object(review, "_model_review_check") as model_check, self.assertRaisesRegex(ValueError, "forbidden"):
            self.decide(root, rid, allow_blocked=True)
        model_check.assert_not_called()

    def test_private_external_key_creation_refusal_and_no_secret_output(self):
        root, rid, _ = self.stage()
        os.environ.pop("LOOPING_BOX_REVIEW_KEY")
        key = self.base / "keys/looping-box/review.key"
        self.decide(root, rid, "rejected")
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(key.read_bytes()), 64)
        self.assertNotIn(key.read_text(), _util.key_status())
        self.assertEqual(_util.decision_state(root / "staging", rid), ("rejected", False))
        for value, mode in ((b"", 0o600), (b"wrong", 0o600), (b"secret", 0o644)):
            key.write_bytes(value); key.chmod(mode)
            self.assertEqual(_util.decision_state(root / "staging", rid), (None, True))
        key.unlink()
        self.assertEqual(_util.decision_state(root / "staging", rid), (None, True))
        key.write_text("secret"); key.chmod(0o600)
        original = Path.open
        def opening(path, *args, **kwargs):
            if path == key: raise PermissionError("test key unreadable")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", opening):
            self.assertIsNone(_util.review_key())

    def test_temporary_path_trap_fails_before_verifier_or_payload_write(self):
        root, rid, payload = self.stage()
        sentinel = self.base / "sentinel"
        sentinel.write_text("retain")
        temporary = root / f"staging/approvals/{rid}.json.{os.getpid()}.tmp"
        temporary.symlink_to(sentinel)
        before = payload.read_bytes()
        with patch.object(review, "_model_review_check") as model_check, self.assertRaises(UnsafePathError):
            self.decide(root, rid)
        model_check.assert_not_called()
        self.assertEqual(payload.read_bytes(), before)
        self.assertEqual(sentinel.read_text(), "retain")

    def test_noninteractive_override_accepts_only_explicit_true_values(self):
        for flag in (None, "", "0", "false", "1", "true", "yes", "on", " TRUE "):
            root, rid, _ = self.stage()
            env = dict(self.env)
            if flag is not None: env["LOOPING_BOX_ALLOW_NONINTERACTIVE"] = flag
            result = subprocess.run(self.command(root, rid), input="", capture_output=True, text=True, env=env, timeout=5)
            allowed = flag in {"1", "true", "yes", "on", " TRUE "}
            with self.subTest(flag=flag):
                self.assertEqual(result.returncode, 0 if allowed else 1, result.stderr)
                path = root / f"staging/approvals/{rid}.json"
                self.assertEqual(path.exists(), allowed)
                if allowed: self.assertIs(json.loads(path.read_text())["interactive"], False)

    def test_real_pty_confirmation_records_actual_interactive_mode(self):
        for answer in (b"approve\n", b"Approve\n", b"no\n", b"\x04"):
            root, rid, _ = self.stage()
            master, slave = pty.openpty()
            proc = subprocess.Popen(self.command(root, rid), stdin=slave, stdout=slave, stderr=slave, env=self.env)
            os.close(slave)
            output = b""
            try:
                deadline = time.monotonic() + 5
                while b"Type 'approve':" not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], max(0, deadline - time.monotonic()))[0]:
                        output += os.read(master, 8192)
                self.assertIn(b"Type 'approve':", output)
                os.write(master, answer)
                code = proc.wait(timeout=5)
                with self.subTest(answer=answer):
                    self.assertEqual(code, 0 if answer == b"approve\n" else 1)
                    path = root / f"staging/approvals/{rid}.json"
                    self.assertEqual(path.exists(), code == 0)
                    if code == 0: self.assertIs(json.loads(path.read_text())["interactive"], True)
            finally:
                if proc.poll() is None: proc.kill()
                proc.wait(timeout=5)
                os.close(master)


if __name__ == "__main__":
    unittest.main()
