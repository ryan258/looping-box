import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ENV_VARS = ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "MODEL_EXECUTION_ENGINE", "MODEL_VERIFIER")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from looping_box import model
from looping_box.phase1 import run_phase1
from looping_box.worker import run_worker


def _fake_transport(content: str):
    def transport(url, headers, body):
        assert url.endswith("/chat/completions")
        assert headers["Authorization"].startswith("Bearer ")
        sent = json.loads(body)
        assert sent["model"]  # a model id was selected for the role
        return json.dumps({"model": sent["model"], "choices": [{"message": {"content": content}}]})

    return transport


class ModelLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Each test loads .env fresh and restores the transport.
        model._env_loaded_for.clear()
        for key in _ENV_VARS:
            os.environ.pop(key, None)
        self._orig_transport = model._transport

    def tearDown(self):
        model._transport = self._orig_transport
        model._env_loaded_for.clear()
        for key in _ENV_VARS:
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def _write_env(self, body: str):
        (self.root / ".env").write_text(body, encoding="utf-8")

    def test_disabled_without_key(self):
        self._write_env("MODEL_EXECUTION_ENGINE=x/y\n")  # model set, no API key
        self.assertFalse(model.is_enabled("execution_engine", root=self.root))
        self.assertIsNone(model.generate_if_enabled("execution_engine", "hi", root=self.root))

    def test_enabled_and_records_model_and_hash(self):
        self._write_env("OPENROUTER_API_KEY=sk-test\nMODEL_EXECUTION_ENGINE=vendor/model-1\n")
        model._transport = _fake_transport("drafted text")
        result = model.generate_if_enabled("execution_engine", "draft this", root=self.root)
        self.assertEqual(result["text"], "drafted text")
        self.assertEqual(result["model"], "vendor/model-1")
        self.assertEqual(len(result["response_sha256"]), 64)

    def test_execution_engine_uses_model_when_enabled(self):
        # Phase 1 -> context_builder -> execution_engine, with the drafter model on.
        (self.root / "config" / "sops").mkdir(parents=True)
        (self.root / "inbox").mkdir()
        (self.root / "config" / "sops" / "phase1_ingestion.json").write_text(
            json.dumps(
                {
                    "name": "t",
                    "allowed_extensions": [".txt"],
                    "routes": [{"label": "docs", "keywords": ["docs"]}],
                    "boundary_gate": {"requires_review_keywords": [], "notification_message": "x"},
                }
            )
        )
        (self.root / "inbox" / "note.txt").write_text("docs: a note", encoding="utf-8")
        self._write_env("OPENROUTER_API_KEY=sk-test\nMODEL_EXECUTION_ENGINE=vendor/model-1\n")
        model._transport = _fake_transport("MODEL DRAFT")

        run_phase1(self.root, now="2026-06-24T12:00:00Z")
        run_worker(self.root, "context_builder", now="2026-06-24T12:00:01Z")
        out = run_worker(self.root, "execution_engine", now="2026-06-24T12:00:02Z")
        self.assertEqual(out["status"], "complete")

        draft = json.loads((self.root / "cache" / "workers" / "execution_engine" / "draft.json").read_text())
        item = draft["items"][0]
        self.assertEqual(item["draft"], "MODEL DRAFT")
        self.assertEqual(item["model"], "vendor/model-1")
        self.assertIn("response_sha256", item)

    def _seed_offline_context(self):
        """Phase 1 + context_builder + execution_engine with no model configured."""
        (self.root / "config" / "sops").mkdir(parents=True)
        (self.root / "inbox").mkdir()
        (self.root / "config" / "sops" / "phase1_ingestion.json").write_text(
            json.dumps(
                {
                    "name": "t",
                    "allowed_extensions": [".txt"],
                    "routes": [{"label": "docs", "keywords": ["docs"]}],
                    "boundary_gate": {"requires_review_keywords": [], "notification_message": "x"},
                }
            )
        )
        (self.root / "inbox" / "note.txt").write_text("docs: a note", encoding="utf-8")
        run_phase1(self.root, now="2026-06-24T12:00:00Z")
        run_worker(self.root, "context_builder", now="2026-06-24T12:00:01Z")
        run_worker(self.root, "execution_engine", now="2026-06-24T12:00:02Z")

    def test_dry_run_never_calls_model(self):
        self._seed_offline_context()
        self._write_env("OPENROUTER_API_KEY=sk-test\nMODEL_EXECUTION_ENGINE=vendor/model-1\n")

        def exploding(url, headers, body):
            raise AssertionError("dry run must not call the model")

        model._transport = exploding
        # New delta so context_builder has work; both workers dry-run.
        (self.root / "inbox" / "more.txt").write_text("docs: more", encoding="utf-8")
        run_phase1(self.root, now="2026-06-24T12:01:00Z")
        run_worker(self.root, "context_builder", now="2026-06-24T12:01:01Z", dry_run=True)
        run_worker(self.root, "execution_engine", now="2026-06-24T12:01:02Z", dry_run=True)
        # No AssertionError raised == model was never called.

    def test_newly_enabled_model_reruns_after_offline(self):
        self._seed_offline_context()  # last_model recorded as None
        # Simulate a fresh process that has not yet loaded .env for this root.
        self._write_env("OPENROUTER_API_KEY=sk-test\nMODEL_EXECUTION_ENGINE=vendor/model-1\n")
        model._env_loaded_for.clear()
        model._transport = _fake_transport("MODEL DRAFT")

        out = run_worker(self.root, "execution_engine", now="2026-06-24T12:02:00Z")
        self.assertEqual(out["status"], "complete")  # not idle
        draft = json.loads((self.root / "cache" / "workers" / "execution_engine" / "draft.json").read_text())
        self.assertEqual(draft["items"][0]["draft"], "MODEL DRAFT")

    def test_worker_contains_model_error(self):
        self._seed_offline_context()
        self._write_env("OPENROUTER_API_KEY=sk-test\nMODEL_EXECUTION_ENGINE=vendor/model-1\n")
        model._env_loaded_for.clear()

        def failing(url, headers, body):
            raise RuntimeError("openrouter is down")

        model._transport = failing
        out = run_worker(self.root, "execution_engine", now="2026-06-24T12:03:00Z")
        # Degrades to deterministic excerpt instead of crashing the pass.
        self.assertEqual(out["status"], "complete")
        draft = json.loads((self.root / "cache" / "workers" / "execution_engine" / "draft.json").read_text())
        self.assertNotIn("model", draft["items"][0])

    def test_verifier_fails_closed_on_model_error(self):
        from looping_box.review import list_reviews, run_verifier

        (self.root / "config" / "sops").mkdir(parents=True)
        (self.root / "inbox").mkdir()
        (self.root / "config" / "sops" / "phase1_ingestion.json").write_text(
            json.dumps(
                {
                    "name": "t",
                    "allowed_extensions": [".txt"],
                    "routes": [],
                    "boundary_gate": {"requires_review_keywords": ["deploy"], "notification_message": "x"},
                }
            )
        )
        (self.root / "inbox" / "r.txt").write_text("please deploy", encoding="utf-8")
        run_phase1(self.root, now="2026-06-24T12:00:00Z")
        review_id = list_reviews(self.root)[0]["review_id"]

        os.environ["OPENROUTER_API_KEY"] = "sk-test"
        os.environ["MODEL_VERIFIER"] = "vendor/judge"
        model._env_loaded_for.clear()

        def failing(url, headers, body):
            raise RuntimeError("judge unreachable")

        model._transport = failing
        result = run_verifier(self.root, review_id, now="2026-06-24T12:00:05Z")
        self.assertEqual(result["status"], "failed")  # fail closed, not silently pass
        self.assertTrue(any(c["name"] == "model_review" and c["status"] == "failed" for c in result["checks"]))


if __name__ == "__main__":
    unittest.main()
