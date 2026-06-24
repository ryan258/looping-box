import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from looping_box.phase1 import run_phase1
from looping_box.worker import run_worker


def _make_sop(root: Path) -> None:
    (root / "config" / "sops").mkdir(parents=True)
    (root / "inbox").mkdir()
    (root / "config" / "sops" / "phase1_ingestion.json").write_text(
        json.dumps(
            {
                "name": "Phase 1 Ingestion",
                "allowed_extensions": [".md", ".txt"],
                "routes": [{"label": "docs", "keywords": ["docs"]}],
                "boundary_gate": {
                    "requires_review_keywords": ["deploy"],
                    "notification_message": "Review required",
                },
                "max_excerpt_chars": 120,
            }
        ),
        encoding="utf-8",
    )


class WorkerTests(unittest.TestCase):
    def test_context_builder_consumes_new_phase1_delta_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "notes.md").write_text("docs note", encoding="utf-8")
            delta = run_phase1(root, now="2026-06-24T12:00:00Z")

            result = run_worker(root, "context_builder", now="2026-06-24T12:01:00Z")

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["inputs"]["source_deltas"], [delta["delta_path"]])
            context_path = root / "cache" / "workers" / "context_builder" / "context_package.json"
            context = json.loads(context_path.read_text(encoding="utf-8"))
            self.assertEqual(context["status"], "complete")
            self.assertEqual(context["items"][0]["relative_path"], "inbox/notes.md")
            self.assertEqual(context["items"][0]["source_delta"], delta["delta_path"])

            second = run_worker(root, "context_builder", now="2026-06-24T12:02:00Z")
            self.assertEqual(second["status"], "idle")
            self.assertEqual(second["inputs"]["source_deltas"], [])

    def test_context_builder_keeps_boundary_gated_inputs_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "release.txt").write_text("please deploy", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")

            result = run_worker(root, "context_builder", now="2026-06-24T12:01:00Z")

            self.assertEqual(result["status"], "blocked")
            context_path = root / "cache" / "workers" / "context_builder" / "context_package.json"
            context = json.loads(context_path.read_text(encoding="utf-8"))
            self.assertEqual(context["status"], "blocked")
            self.assertEqual(context["blocked_inputs"][0]["relative_path"], "inbox/release.txt")

    def test_execution_engine_writes_deterministic_draft_from_context_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "notes.md").write_text("docs note", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")
            run_worker(root, "context_builder", now="2026-06-24T12:01:00Z")
            (root / "inbox" / "notes.md").unlink()

            result = run_worker(root, "execution_engine", now="2026-06-24T12:02:00Z")

            self.assertEqual(result["status"], "complete")
            draft_path = root / "cache" / "workers" / "execution_engine" / "draft.json"
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
            self.assertEqual(draft["schema"], "looping-box.execution-draft.v1")
            self.assertEqual(draft["items"][0]["relative_path"], "inbox/notes.md")
            self.assertEqual(draft["source_context"], "cache/workers/context_builder/context_package.json")
            self.assertTrue(draft["source_context_sha256"])

    def test_execution_engine_refuses_blocked_or_missing_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            missing = run_worker(root, "execution_engine", now="2026-06-24T12:00:00Z")
            self.assertEqual(missing["status"], "blocked")
            self.assertEqual(missing["errors"][0]["code"], "missing_context")

            (root / "inbox" / "release.txt").write_text("please deploy", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:01:00Z")
            run_worker(root, "context_builder", now="2026-06-24T12:02:00Z")

            blocked = run_worker(root, "execution_engine", now="2026-06-24T12:03:00Z")
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["errors"][0]["code"], "blocked_context")


if __name__ == "__main__":
    unittest.main()
