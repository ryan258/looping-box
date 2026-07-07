import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from looping_box.phase1 import run_phase1
from looping_box.schema import validate
from looping_box.supervisor import _release_lock, load_world_state, run_supervisor, status_summary
from looping_box import supervisor

SCHEMA_DIR = ROOT / "docs" / "schemas"
WORLD_STATE_SCHEMA = json.loads((SCHEMA_DIR / "world_state.schema.json").read_text())


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
            validate(state, WORLD_STATE_SCHEMA)

            second = run_supervisor(root, now="2026-06-24T12:02:00Z")
            self.assertEqual(second["status"], "idle")
            self.assertEqual(second["plan"], [])

    def test_supervisor_archives_observed_deltas_and_prunes_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "notes.md").write_text("docs note", encoding="utf-8")
            delta = run_phase1(root, now="2026-06-24T12:00:00Z")

            result = run_supervisor(root, now="2026-06-24T12:01:00Z")

            self.assertEqual(result["status"], "complete")
            self.assertFalse((root / delta["delta_path"]).exists())
            self.assertTrue((root / "cache" / "deltas" / "archive" / Path(delta["delta_path"]).name).exists())
            state = load_world_state(root)
            self.assertEqual(state["observed_deltas"], [])
            worker_state = json.loads(
                (root / "cache" / "workers" / "context_builder" / "state.json").read_text()
            )
            self.assertEqual(worker_state["consumed_inputs"], [])

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

    def test_release_lock_only_removes_current_process_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_path = root / ".looping_box.lock"
            lock_path.write_text(
                json.dumps({"created_at": "2026-06-24T12:00:00Z", "pid": os.getpid() + 1}) + "\n",
                encoding="utf-8",
            )

            _release_lock(lock_path, os.getpid())

            self.assertTrue(lock_path.exists())

            lock_path.write_text(
                json.dumps({"created_at": "2026-06-24T12:00:00Z", "pid": os.getpid()}) + "\n",
                encoding="utf-8",
            )
            _release_lock(lock_path, os.getpid())
            self.assertFalse(lock_path.exists())

    def test_supervisor_cli_requires_once_or_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)

            with patch.object(sys, "argv", ["supervisor", "--root", str(root)]), patch(
                "sys.stderr"
            ):
                with self.assertRaises(SystemExit) as raised:
                    supervisor.main()

            self.assertEqual(raised.exception.code, 2)
            self.assertFalse((root / ".world_state.json").exists())

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

    def test_payload_size_limit_stays_blocked_until_config_is_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "config" / "super_loop.json").write_text(
                json.dumps({"max_payload_bytes": 1}) + "\n", encoding="utf-8"
            )
            (root / "inbox" / "notes.md").write_text("docs note", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")

            first = run_supervisor(root, now="2026-06-24T12:01:00Z")
            self.assertEqual(first["status"], "blocked")
            self.assertEqual(first["recovery"]["blocked_reason"], "payload_size_limit")

            second = run_supervisor(root, now="2026-06-24T12:02:00Z")
            self.assertEqual(second["status"], "blocked")
            self.assertEqual(second["recovery"]["blocked_reason"], "payload_size_limit")

            (root / "config" / "super_loop.json").write_text(
                json.dumps({"max_payload_bytes": 65536}) + "\n", encoding="utf-8"
            )
            third = run_supervisor(root, now="2026-06-24T12:03:00Z")
            self.assertEqual(third["status"], "complete")

            summary = status_summary(root)
            self.assertIn("clear", summary)

    def test_status_summary_points_resource_limit_blocks_at_config_not_a_source_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "config" / "super_loop.json").write_text(
                json.dumps({"max_payload_bytes": 1}) + "\n", encoding="utf-8"
            )
            (root / "inbox" / "notes.md").write_text("docs note", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")
            run_supervisor(root, now="2026-06-24T12:01:00Z")

            summary = status_summary(root)
            self.assertIn("operator action required", summary)
            self.assertIn("config/super_loop.json", summary)
            self.assertIn("looping-box-supervisor --once", summary)
            self.assertNotIn("./startday.sh", summary)
            self.assertNotIn("source file", summary)

    def test_payload_size_limit_rolls_back_worker_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "config" / "super_loop.json").write_text(
                json.dumps({"max_payload_bytes": 1}) + "\n", encoding="utf-8"
            )
            (root / "inbox" / "notes.md").write_text("docs note", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")

            result = run_supervisor(root, now="2026-06-24T12:01:00Z")

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["recovery"]["blocked_reason"], "payload_size_limit")
            self.assertFalse(
                (root / "cache" / "workers" / "context_builder" / "context_package.json").exists()
            )
            self.assertFalse(
                (root / "cache" / "workers" / "context_builder" / "last_output.json").exists()
            )

    def test_worker_timeout_stays_blocked_until_config_is_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "config" / "super_loop.json").write_text(
                json.dumps({"max_worker_runtime_seconds": -1}) + "\n", encoding="utf-8"
            )
            (root / "inbox" / "notes.md").write_text("docs note", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")

            first = run_supervisor(root, now="2026-06-24T12:01:00Z")
            self.assertEqual(first["status"], "blocked")
            self.assertEqual(first["recovery"]["blocked_reason"], "worker_timeout")

            second = run_supervisor(root, now="2026-06-24T12:02:00Z")
            self.assertEqual(second["status"], "blocked")
            self.assertEqual(second["recovery"]["blocked_reason"], "worker_timeout")

            (root / "config" / "super_loop.json").write_text(
                json.dumps({"max_worker_runtime_seconds": 30}) + "\n", encoding="utf-8"
            )
            third = run_supervisor(root, now="2026-06-24T12:03:00Z")
            self.assertEqual(third["status"], "complete")


if __name__ == "__main__":
    unittest.main()
