import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from looping_box.phase1 import run_phase1


class Phase1IngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "config" / "sops").mkdir(parents=True)
        (self.root / "inbox").mkdir()

        self.sop_path = self.root / "config" / "sops" / "phase1_ingestion.json"
        self.sop_path.write_text(
            json.dumps(
                {
                    "name": "Phase 1 Ingestion",
                    "allowed_extensions": [".md", ".txt", ".json"],
                    "routes": [
                        {"label": "documentation", "keywords": ["docs", "readme"]},
                        {"label": "task_backlog", "keywords": ["todo", "backlog"]},
                    ],
                    "boundary_gate": {
                        "requires_review_keywords": ["deploy", "commit", "send"],
                        "notification_message": "Review required",
                    },
                    "max_excerpt_chars": 120,
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_new_input_file_writes_delta_and_updates_state(self):
        (self.root / "inbox" / "notes.md").write_text(
            "# Docs\n\nTODO: summarize the readme.",
            encoding="utf-8",
        )

        result = run_phase1(self.root, now="2026-06-24T12:00:00Z")

        self.assertEqual(result["summary"]["scanned"], 1)
        self.assertEqual(result["summary"]["changed"], 1)
        self.assertEqual(result["boundary_gate"]["status"], "clear")
        self.assertEqual(result["changes"][0]["relative_path"], "inbox/notes.md")
        self.assertEqual(result["changes"][0]["matched_routes"], ["documentation", "task_backlog"])

        delta_path = self.root / result["delta_path"]
        state_path = self.root / "cache" / "state" / "phase1_state.json"
        self.assertTrue(delta_path.exists())
        self.assertTrue(state_path.exists())

    def test_empty_run_updates_state_without_writing_delta(self):
        result = run_phase1(self.root, now="2026-06-24T12:00:00Z")

        self.assertEqual(result["summary"]["scanned"], 0)
        self.assertIsNone(result["delta_path"])
        self.assertEqual(list((self.root / "cache" / "deltas").glob("*.json")), [])
        state = json.loads((self.root / "cache" / "state" / "phase1_state.json").read_text())
        self.assertIsNone(state["last_delta_path"])
        self.assertIsNone(state["runs"][-1]["delta_path"])

    def test_run_id_does_not_collide_with_archived_deltas(self):
        archive_dir = self.root / "cache" / "deltas" / "archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "phase1-delta-20260624T120000Z.json").write_text("{}", encoding="utf-8")
        (self.root / "inbox" / "notes.md").write_text("docs note", encoding="utf-8")

        result = run_phase1(self.root, now="2026-06-24T12:00:00Z")

        self.assertEqual(result["run_id"], "phase1-delta-20260624T120000Z-2")

    def test_repeated_run_skips_identical_content(self):
        (self.root / "inbox" / "notes.md").write_text(
            "Backlog item for docs.",
            encoding="utf-8",
        )

        run_phase1(self.root, now="2026-06-24T12:00:00Z")
        result = run_phase1(self.root, now="2026-06-24T12:01:00Z")

        self.assertEqual(result["summary"]["scanned"], 1)
        self.assertEqual(result["summary"]["changed"], 0)
        self.assertEqual(result["summary"]["skipped"], 1)
        self.assertEqual(result["skipped"][0]["reason"], "already_processed")

    def test_review_keywords_materialize_pending_review_payload(self):
        (self.root / "inbox" / "release.txt").write_text(
            "Please deploy this change and send the announcement.",
            encoding="utf-8",
        )

        result = run_phase1(self.root, now="2026-06-24T12:00:00Z")

        self.assertEqual(result["boundary_gate"]["status"], "pending_review")
        payload_path = self.root / "staging" / "pending_review.json"
        self.assertTrue(payload_path.exists())

        index = json.loads(payload_path.read_text(encoding="utf-8"))
        self.assertEqual(index["schema"], "looping-box.pending-review-index.v1")
        payload = json.loads((self.root / index["latest"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "looping-box.review-payload.v1")
        self.assertEqual(payload["risk_reasons"], ["deploy", "send"])
        self.assertEqual(payload["source_items"][0]["relative_path"], "inbox/release.txt")

    def test_review_files_resurface_until_handled(self):
        (self.root / "inbox" / "release.txt").write_text(
            "Please deploy this change.",
            encoding="utf-8",
        )

        run_phase1(self.root, now="2026-06-24T12:00:00Z")
        result = run_phase1(self.root, now="2026-06-24T12:01:00Z")

        # Not marked processed, so it re-surfaces instead of being skipped.
        self.assertEqual(result["summary"]["changed"], 1)
        self.assertEqual(result["summary"]["skipped"], 0)
        self.assertEqual(result["boundary_gate"]["status"], "pending_review")
        self.assertTrue((self.root / "staging" / "pending_review.json").exists())

    def test_distinct_empty_files_are_each_processed(self):
        (self.root / "inbox" / "a.md").write_text("", encoding="utf-8")
        (self.root / "inbox" / "b.md").write_text("", encoding="utf-8")

        result = run_phase1(self.root, now="2026-06-24T12:00:00Z")

        # Identical (empty) content must not dedup distinct files away.
        self.assertEqual(result["summary"]["changed"], 2)

    def test_input_outside_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "leak.md").write_text("secret", encoding="utf-8")
            with self.assertRaises(ValueError):
                run_phase1(self.root, now="2026-06-24T12:00:00Z", input_dir=outside)

    def test_dotdot_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            run_phase1(self.root, now="2026-06-24T12:00:00Z", input_dir="../escape")

    def test_symlink_escaping_root_is_not_ingested(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "outside.md"
            target.write_text("secret outside content", encoding="utf-8")
            (self.root / "inbox" / "link.md").symlink_to(target)

            result = run_phase1(self.root, now="2026-06-24T12:00:00Z")

            self.assertEqual(result["summary"]["changed"], 0)
            self.assertEqual(result["changes"], [])

    def test_in_root_symlink_is_not_ingested(self):
        secret_path = self.root / ".env"
        secret_path.write_text("OPENROUTER_API_KEY=sk-live-test\n", encoding="utf-8")
        (self.root / "inbox" / "leak.md").symlink_to("../.env")

        result = run_phase1(self.root, now="2026-06-24T12:00:00Z")

        self.assertEqual(result["summary"]["changed"], 0)
        self.assertEqual(result["changes"], [])


if __name__ == "__main__":
    unittest.main()
