"""Regression tests for the trust-boundary fixes (gate matching, signed decisions,
approval release, retry after model failure, status truth, audit chain, locks)."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Decision records are HMAC-signed; tests must never touch ~/.config.
os.environ["LOOPING_BOX_REVIEW_KEY"] = "test-review-key"

from looping_box import model
from looping_box._util import append_audit, verify_audit_chain
from looping_box.action_policy import DEFAULT_ACTION_CLASSES, match_keywords
from looping_box.phase1 import run_phase1
from looping_box._util import key_status, review_key
from looping_box.review import list_reviews, record_review, verify_audit_logs
from looping_box.supervisor import _restore_worker_dir, _snapshot_worker_dir, run_supervisor, status_summary

_GATE = ["deploy", "commit", "send", "secret", "production", "remove", "push", "publish", "rm -rf", "drop table"]


def _make_sop(root: Path, **extra) -> None:
    (root / "config" / "sops").mkdir(parents=True)
    (root / "inbox").mkdir()
    sop = {
        "name": "t",
        "allowed_extensions": [".md", ".txt"],
        "routes": [{"label": "docs", "keywords": ["docs"]}],
        "boundary_gate": {"requires_review_keywords": ["deploy", "production"], "notification_message": "x"},
        "max_excerpt_chars": 120,
    }
    sop.update(extra)
    (root / "config" / "sops" / "phase1_ingestion.json").write_text(json.dumps(sop), encoding="utf-8")


class MatcherTests(unittest.TestCase):
    def test_evasions_and_inflections_trip_the_gate(self):
        for text in [
            "Run rm -rf /tmp/x",
            "DROP   TABLE users;",
            "please de​ploy now",  # zero-width space
            "please d e p l o y now",  # spaced out
            "please dеploy now",  # Cyrillic e
            "ｄｅｐｌｏｙ it",  # fullwidth
            "send_email()",
            "git push --force",
            "we committed it",
            "Sending the report",
            "it was deployed yesterday",
        ]:
            with self.subTest(text=text):
                self.assertTrue(match_keywords(text, _GATE))

    def test_ordinary_words_do_not_trip_the_gate(self):
        for text in [
            "The committee made a commitment.",
            "Ask the secretary to resend the sender list.",
            "Reproduction of the bug in staging.",
            "Pushback from the publisher was expected.",
        ]:
            with self.subTest(text=text):
                self.assertEqual(match_keywords(text, _GATE), [])

    def test_every_sop_keyword_has_an_explicit_action_class(self):
        sop = json.loads((ROOT / "config" / "sops" / "phase1_ingestion.json").read_text())
        config = json.loads((ROOT / "config" / "action_classes.json").read_text())
        listed = {term.lower() for terms in config["classes"].values() for term in terms}
        missing = [k for k in sop["boundary_gate"]["requires_review_keywords"] if k.lower() not in listed]
        self.assertEqual(missing, [])
        # The built-in fallback must not drift from the shipped config.
        self.assertEqual(config["classes"], DEFAULT_ACTION_CLASSES["classes"])


class DecisionIntegrityTests(unittest.TestCase):
    def _gated(self, root: Path):
        _make_sop(root)
        (root / "inbox" / "r.txt").write_text("please deploy", encoding="utf-8")
        run_phase1(root, now="2026-06-24T12:00:00Z")
        return list_reviews(root)[0]

    def test_hand_written_approval_does_not_clear_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = self._gated(root)
            forged = root / "staging" / "approvals" / f"{review['review_id']}.json"
            forged.parent.mkdir(parents=True, exist_ok=True)
            forged.write_text("{}", encoding="utf-8")

            result = run_phase1(root, now="2026-06-24T12:01:00Z")

            self.assertEqual(result["boundary_gate"]["status"], "pending_review")
            self.assertEqual(list_reviews(root)[0]["review_id"], review["review_id"])

    def test_editing_a_signed_record_invalidates_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = self._gated(root)
            record_review(root, review["review_id"], "approved", note="ok", approver="ryan")
            path = root / "staging" / "approvals" / f"{review['review_id']}.json"
            record = json.loads(path.read_text())
            self.assertEqual(record["approver"], "ryan")
            record["note"] = "edited after the fact"
            path.write_text(json.dumps(record), encoding="utf-8")

            # The index entry was removed at approval; the next run must re-gate it.
            result = run_phase1(root, now="2026-06-24T12:02:00Z")
            self.assertEqual(result["boundary_gate"]["status"], "pending_review")
            self.assertEqual(list_reviews(root)[0]["review_id"], review["review_id"])

    def test_record_without_the_key_is_not_a_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = self._gated(root)
            record_review(root, review["review_id"], "rejected", note="no")
            saved = os.environ.pop("LOOPING_BOX_REVIEW_KEY")
            os.environ["XDG_CONFIG_HOME"] = tmp  # no key file here either
            try:
                result = run_phase1(root, now="2026-06-24T12:02:00Z")
                self.assertEqual(result["boundary_gate"]["status"], "pending_review")
                self.assertEqual(len(list_reviews(root)), 1)
            finally:
                os.environ["LOOPING_BOX_REVIEW_KEY"] = saved
                os.environ.pop("XDG_CONFIG_HOME", None)

    def test_blocked_class_needs_explicit_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "p.txt").write_text("push to production", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")
            review = list_reviews(root)[0]
            self.assertEqual(review["action_class"], "blocked")

            with self.assertRaises(ValueError):
                record_review(root, review["review_id"], "approved", note="x")
            record = record_review(root, review["review_id"], "approved", note="x", allow_blocked=True)
            self.assertEqual(record["decision"], "approved")


class KeyAndWarningTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("LOOPING_BOX_REVIEW_KEY", None)
        self._saved_xdg = os.environ.get("XDG_CONFIG_HOME")

    def tearDown(self):
        os.environ["LOOPING_BOX_REVIEW_KEY"] = self._saved or "test-review-key"
        if self._saved_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._saved_xdg

    @unittest.skipUnless(os.name == "posix", "permission bits are POSIX-only")
    def test_key_file_readable_by_others_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_CONFIG_HOME"] = tmp
            key_file = Path(tmp) / "looping-box" / "review.key"
            key_file.parent.mkdir()
            key_file.write_text("secret-key")
            key_file.chmod(0o644)
            self.assertIsNone(review_key())
            self.assertIn("chmod 600", key_status())
            key_file.chmod(0o600)
            self.assertEqual(review_key(), b"secret-key")
            self.assertIn("(ok)", key_status())

    @unittest.skipUnless(os.name == "posix", "permission bits are POSIX-only")
    def test_created_key_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_CONFIG_HOME"] = tmp
            self.assertIsNotNone(review_key(create=True))
            mode = (Path(tmp) / "looping-box" / "review.key").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_unverifiable_decision_is_reported_not_silent(self):
        os.environ["LOOPING_BOX_REVIEW_KEY"] = "test-review-key"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "r.txt").write_text("please deploy", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")
            review = list_reviews(root)[0]
            forged = root / "staging" / "approvals" / f"{review['review_id']}.json"
            forged.parent.mkdir(parents=True, exist_ok=True)
            forged.write_text("{}", encoding="utf-8")

            result = run_phase1(root, now="2026-06-24T12:01:00Z")

            self.assertEqual(result["warnings"][0]["code"], "decision_unverifiable")
            self.assertEqual(result["warnings"][0]["review_id"], review["review_id"])
            self.assertTrue(list_reviews(root)[0]["unverifiable_decision"])
            self.assertIn("did not verify", status_summary(root))


class SnapshotTests(unittest.TestCase):
    def test_restore_rewinds_files_but_keeps_prior_history_and_drops_new_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            worker = root / "cache" / "workers" / "execution_engine"
            (worker / "history").mkdir(parents=True)
            (worker / "state.json").write_text("before")
            (worker / "history" / "old.json").write_text("old")
            snapshot = _snapshot_worker_dir(root, "execution_engine")
            self.assertEqual(snapshot[0], {"state.json": b"before"})  # history bytes not held
            self.assertEqual(snapshot[1], {"history/old.json"})

            (worker / "state.json").write_text("after")
            (worker / "draft.json").write_text("new")
            (worker / "history" / "new.json").write_text("new")
            _restore_worker_dir(root, "execution_engine", snapshot)

            self.assertEqual((worker / "state.json").read_text(), "before")
            self.assertFalse((worker / "draft.json").exists())
            self.assertTrue((worker / "history" / "old.json").exists())
            self.assertFalse((worker / "history" / "new.json").exists())

    def test_restore_of_a_dir_that_did_not_exist_removes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            snapshot = _snapshot_worker_dir(root, "context_builder")
            self.assertIsNone(snapshot)
            worker = root / "cache" / "workers" / "context_builder"
            (worker / "history").mkdir(parents=True)
            (worker / "x.json").write_text("x")
            _restore_worker_dir(root, "context_builder", snapshot)
            self.assertFalse(worker.exists())


class PipelineTests(unittest.TestCase):
    def test_approval_releases_item_and_clean_siblings_are_not_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            for i in range(3):
                (root / "inbox" / f"clean{i}.md").write_text(f"docs note {i}", encoding="utf-8")
            (root / "inbox" / "risky.txt").write_text("please deploy", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")

            first = run_supervisor(root, now="2026-06-24T12:01:00Z")

            self.assertEqual(first["status"], "blocked")  # a human decision is still owed
            out = root / "cache" / "workers" / "execution_engine"
            drafted = json.loads((out / "draft.json").read_text())
            self.assertEqual(len(drafted["items"]), 3)  # clean siblings flow
            self.assertEqual(drafted["held_for_review"], ["inbox/risky.txt"])

            review_id = list_reviews(root)[0]["review_id"]
            record_review(root, review_id, "approved", note="ok")
            run_phase1(root, now="2026-06-24T12:02:00Z")
            second = run_supervisor(root, now="2026-06-24T12:03:00Z")

            self.assertEqual(second["status"], "complete")
            released = json.loads((out / "draft.json").read_text())
            self.assertEqual([i["relative_path"] for i in released["items"]], ["inbox/risky.txt"])
            self.assertEqual(released["items"][0]["approved_review"], review_id)  # decision trail kept
            self.assertIn(review_id, (out / "draft.md").read_text())
            self.assertEqual(len(list((out / "history").glob("*-draft.json"))), 1)  # earlier batch kept

    def test_execution_retries_after_a_model_outage(self):
        saved_transport = model._transport
        for name, value in (("OPENROUTER_API_KEY", "k"), ("MODEL_EXECUTION_ENGINE", "v/m")):
            os.environ[name] = value
            self.addCleanup(os.environ.pop, name, None)
        self.addCleanup(setattr, model, "_transport", saved_transport)

        def down(url, headers, body, timeout):
            raise RuntimeError("HTTP 429")

        def up(url, headers, body, timeout):
            return json.dumps({"model": "v/m", "choices": [{"message": {"content": "MODEL DRAFT"}}]})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model._env_loaded_for.add(str(root.resolve()))
            _make_sop(root)
            (root / "inbox" / "a.md").write_text("docs note", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")

            model._transport = down
            failed = run_supervisor(root, now="2026-06-24T12:01:00Z")
            self.assertEqual(failed["status"], "failed")

            model._transport = up
            retried = run_supervisor(root, now="2026-06-24T12:02:00Z")

            self.assertEqual(retried["status"], "complete")
            self.assertEqual(retried["plan"], ["context_builder", "execution_engine"])
            draft = json.loads((root / "cache" / "workers" / "execution_engine" / "draft.json").read_text())
            self.assertEqual(draft["items"][0]["draft"], "MODEL DRAFT")
            self.assertIn("clear", status_summary(root))

    def test_status_shows_pending_reviews_before_the_supervisor_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "r.txt").write_text("please deploy", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")

            summary = status_summary(root)

            self.assertIn("operator action required", summary)
            self.assertIn("pending reviews: 1", summary)

    def test_oversized_input_is_held_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root, max_file_bytes=10)
            (root / "inbox" / "big.txt").write_text("x" * 50, encoding="utf-8")

            result = run_phase1(root, now="2026-06-24T12:00:00Z")

            self.assertEqual(result["boundary_gate"]["status"], "pending_review")
            self.assertEqual(result["changes"][0]["review_reasons"], ["oversized_input"])
            self.assertEqual(result["changes"][0]["excerpt"], "")


class ModelAndLockTests(unittest.TestCase):
    def test_null_content_response_is_a_model_error(self):
        saved_transport = model._transport
        for name, value in (("OPENROUTER_API_KEY", "k"), ("MODEL_X", "v/m")):
            os.environ[name] = value
            self.addCleanup(os.environ.pop, name, None)
        self.addCleanup(setattr, model, "_transport", saved_transport)
        model._transport = lambda *a: json.dumps({"choices": [{"message": {"content": None}}]})
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(model.ModelError):
            model.complete("x", "hi", root=tmp)  # root=tmp: never load the repo's real .env

    def test_phase1_respects_the_project_lock_and_recovers_a_corrupt_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            lock = root / ".looping_box.lock"
            lock.write_text(json.dumps({"created_at": "2026-06-24T12:00:00Z", "pid": 1}) + "\n")
            with self.assertRaises(RuntimeError):
                run_phase1(root, now="2026-06-24T12:00:01Z")

            lock.write_text("not json", encoding="utf-8")
            os.utime(lock, (0, 0))  # ancient mtime => stale
            run_phase1(root, now="2026-06-24T12:00:02Z")
            self.assertFalse(lock.exists())


class AuditTests(unittest.TestCase):
    def test_audit_chain_detects_tampering_and_phase1_is_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "r.txt").write_text("please deploy", encoding="utf-8")
            result = run_phase1(root, now="2026-06-24T12:00:00Z")
            (root / "inbox" / "n.md").write_text("docs note", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:01:00Z")

            log = root / "logs" / "transactions" / "phase1.jsonl"
            events = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["gate"], "pending_review")
            self.assertEqual(events[0]["review_ids"], [list_reviews(root)[0]["review_id"]])
            self.assertEqual(events[0]["run_id"], result["run_id"])
            self.assertIsNone(verify_audit_chain(log))

            self.assertEqual(verify_audit_logs(root), {"phase1.jsonl": None})
            events[0]["changed"] = 99
            log.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
            self.assertEqual(verify_audit_chain(log), 1)
            self.assertEqual(verify_audit_logs(root), {"phase1.jsonl": 1})

    def test_review_decisions_record_approver_and_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "r.txt").write_text("please deploy", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")
            review_id = list_reviews(root)[0]["review_id"]
            record_review(root, review_id, "rejected", note="not now", approver="ryan")

            log = root / "logs" / "transactions" / "review.jsonl"
            event = json.loads(log.read_text().splitlines()[0])
            self.assertEqual((event["approver"], event["note"]), ("ryan", "not now"))
            self.assertIsNone(verify_audit_chain(log))
            append_audit(root.resolve(), "review", {"event": "x"})  # resolved: macOS /var -> /private/var
            self.assertIsNone(verify_audit_chain(log))


if __name__ == "__main__":
    unittest.main()
