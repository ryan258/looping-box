import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from looping_box.phase1 import run_phase1
from looping_box.supervisor import load_world_state, run_supervisor, status_summary


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


class SupervisorTests(unittest.TestCase):
    def test_supervisor_runs_worker_chain_once_then_noops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "notes.md").write_text("docs note", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")

            result = run_supervisor(root, now="2026-06-24T12:01:00Z")

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["plan"], ["context_builder", "execution_engine"])
            state = load_world_state(root)
            self.assertEqual(state["worker_states"]["context_builder"]["status"], "complete")
            self.assertEqual(state["worker_states"]["execution_engine"]["status"], "complete")
            self.assertTrue((root / "logs" / "transactions" / "supervisor.jsonl").exists())

            second = run_supervisor(root, now="2026-06-24T12:02:00Z")
            self.assertEqual(second["status"], "idle")
            self.assertEqual(second["plan"], [])

    def test_supervisor_surfaces_pending_review_as_operator_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "release.txt").write_text("please deploy", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")

            result = run_supervisor(root, now="2026-06-24T12:01:00Z")

            self.assertEqual(result["status"], "blocked")
            state = load_world_state(root)
            self.assertTrue(state["recovery"]["operator_action_required"])
            self.assertEqual(state["recovery"]["pending_review_payload"], "staging/pending_review.json")
            summary = status_summary(root)
            self.assertIn("operator action required", summary)
            self.assertIn("staging/pending_review.json", summary)
            self.assertIn("Next:", summary)

    def test_unknown_world_state_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".world_state.json").write_text('{"schema": "wrong"}\n', encoding="utf-8")

            with self.assertRaises(ValueError):
                load_world_state(root)

    def test_active_lock_blocks_and_stale_lock_is_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            lock_path = root / ".looping_box.lock"
            lock_path.write_text(
                json.dumps({"created_at": "2026-06-24T12:00:00Z", "pid": 999}) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(RuntimeError):
                run_supervisor(root, now="2026-06-24T12:00:01Z")

            lock_path.write_text(
                json.dumps({"created_at": "2026-06-24T11:00:00Z", "pid": 999}) + "\n",
                encoding="utf-8",
            )
            result = run_supervisor(root, now="2026-06-24T12:00:01Z")
            self.assertEqual(result["status"], "idle")
            self.assertFalse(lock_path.exists())

    def test_dependency_config_controls_worker_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "config" / "super_loop.json").write_text(
                json.dumps(
                    {
                        "workers": ["context_builder", "execution_engine"],
                        "dependencies": [{"from": "phase1_delta", "to": "context_builder"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "inbox" / "notes.md").write_text("docs note", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")

            result = run_supervisor(root, now="2026-06-24T12:01:00Z")

            self.assertEqual(result["plan"], ["context_builder"])
            self.assertTrue((root / "cache" / "workers" / "context_builder" / "context_package.json").exists())
            self.assertFalse((root / "cache" / "workers" / "execution_engine" / "draft.json").exists())

    def test_file_count_limit_writes_blocked_recovery_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "config").mkdir(exist_ok=True)
            (root / "config" / "super_loop.json").write_text(
                json.dumps({"max_files_per_cycle": 1}) + "\n",
                encoding="utf-8",
            )
            (root / "inbox" / "a.md").write_text("docs a", encoding="utf-8")
            (root / "inbox" / "b.md").write_text("docs b", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")

            result = run_supervisor(root, now="2026-06-24T12:01:00Z")

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["recovery"]["blocked_reason"], "file_count_limit")
            self.assertTrue((root / "cache" / "supervisor" / "blocked.json").exists())
            self.assertFalse(
                (root / "cache" / "workers" / "context_builder" / "context_package.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
