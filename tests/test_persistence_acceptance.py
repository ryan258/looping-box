"""T03 / A25: injected persistence failures and recognized partial-state evidence."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from looping_box import _util, doctor, locking, model, phase1, scaffold, supervisor, worker
from looping_box.paths import UnsafePathError

STAMP = "2026-10-13T12:00:00Z"
LATER = "2026-10-13T12:01:00Z"


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.base / "keys"),
                                      "LOOPING_BOX_REVIEW_KEY": "fake-test-key"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        network = patch("looping_box.model._transport", side_effect=AssertionError("network forbidden"))
        self.transport = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(model._env_loaded_for.clear)
        self.sequence = 0

    def workspace(self, *, seed=False):
        self.sequence += 1
        root = self.base / f"workspace-{self.sequence}"
        scaffold.init_workspace(root)
        if seed:
            (root / "inbox/note.md").write_text("docs initial")
            phase1.run_phase1(root, now=STAMP)
            self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "complete")
        return root

    @contextlib.contextmanager
    def fail_replace(self, destination):
        original = Path.replace
        def replace(source, target):
            if Path(target) == destination:
                raise OSError("injected replace failure")
            return original(source, target)
        with patch.object(Path, "replace", replace):
            yield

    def test_json_and_markdown_replace_failure_keeps_complete_prior_bytes(self):
        root = self.workspace()
        for name, writer, data in (("result.json", _util.write_json, {"new": True}),
                                   ("result.md", _util.write_text, "new markdown\n")):
            path = root / name
            path.write_bytes(b"prior complete bytes\n")
            with self.subTest(name=name), self.fail_replace(path), self.assertRaisesRegex(OSError, "injected"):
                writer(path, data)
            self.assertEqual(path.read_bytes(), b"prior complete bytes\n")
            remnants = list(path.parent.glob(f"{path.name}.*.tmp"))
            self.assertEqual(len(remnants), 1)
            writer(path, data)
            self.assertNotEqual(path.read_bytes(), b"prior complete bytes\n")
            self.assertTrue(remnants[0].is_file())

    def test_atomic_write_leaves_legacy_shared_temp_inode_untouched(self):
        root = self.workspace()
        outside = self.base / "outside.md"
        outside.write_text("outside evidence")
        destination = root / "document.md"
        legacy_temp = destination.with_name(f"{destination.name}.{os.getpid()}.tmp")
        os.link(outside, legacy_temp)
        _util.write_text(destination, "complete replacement")
        self.assertEqual(destination.read_text(), "complete replacement")
        self.assertEqual(outside.read_text(), "outside evidence")
        self.assertEqual(legacy_temp.stat().st_ino, outside.stat().st_ino)

    def test_fsync_failure_does_not_replace_destination(self):
        path = self.workspace() / "result.md"
        path.write_text("old complete document")
        with patch("looping_box._util.os.fsync", side_effect=OSError("injected fsync failure")), \
                self.assertRaisesRegex(OSError, "fsync"):
            _util.write_text(path, "new document")
        self.assertEqual(path.read_text(), "old complete document")

    def test_successful_write_flushes_and_fsyncs_before_same_directory_replace(self):
        path = self.workspace() / "document.md"
        events = []
        original_fsync, original_replace = os.fsync, Path.replace
        def fsync(fd):
            events.append("fsync")
            self.assertEqual(os.fstat(fd).st_size, len(b"complete document\n"))
            return original_fsync(fd)
        def replace(source, target):
            self.assertEqual(source.parent, path.parent)
            self.assertEqual(Path(target), path)
            self.assertEqual(events, ["fsync"])
            events.append("replace")
            return original_replace(source, target)
        with patch("looping_box._util.os.fsync", fsync), patch.object(Path, "replace", replace):
            _util.write_text(path, "complete document\n")
        self.assertEqual(events, ["fsync", "replace"])

    def test_worker_output_failures_preserve_target_and_success_marker(self):
        for worker_id, primary in (("context_builder", "context_package"), ("execution_engine", "draft")):
            for name in (primary + ".json", primary + ".md", "last_output.json", "state.json"):
                with self.subTest(worker=worker_id, target=name):
                    root = self.workspace(seed=True)
                    (root / "inbox/second.md").write_text("docs another batch")
                    phase1.run_phase1(root, now=LATER)
                    if worker_id == "execution_engine":
                        worker.run_worker(root, "context_builder", now=LATER)
                    directory = root / "cache/workers" / worker_id
                    target = directory / name
                    marker = directory / "state.json"
                    prior_target, prior_marker = target.read_bytes(), marker.read_bytes()
                    with self.fail_replace(target), self.assertRaisesRegex(OSError, "injected"):
                        worker.run_worker(root, worker_id, now=LATER)
                    self.assertEqual(target.read_bytes(), prior_target)
                    self.assertEqual(marker.read_bytes(), prior_marker)
                    self.assertFalse(locking.is_held(root / ".looping_box.lock"))
                    self.assertEqual(worker.run_worker(root, worker_id, now=LATER)["status"], "complete")
        self.transport.assert_not_called()

    def test_cli_output_failure_is_exit_one_and_does_not_consume(self):
        root = self.workspace(seed=True)
        (root / "inbox/new.md").write_text("docs new")
        phase1.run_phase1(root, now=LATER)
        marker = root / "cache/workers/context_builder/state.json"
        before = marker.read_bytes()
        target = root / "cache/workers/context_builder/context_package.md"
        with self.fail_replace(target), patch.object(sys, "argv", ["worker", "context_builder", "--root", str(root)]), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(worker.main(), 1)
        self.assertIn("injected replace failure", error.getvalue())
        self.assertEqual(marker.read_bytes(), before)

    def test_phase1_delta_failure_does_not_advance_processed_state(self):
        root = self.workspace(seed=True)
        (root / "inbox/new.md").write_text("docs new")
        marker = root / "cache/state/phase1_state.json"
        before = marker.read_bytes()
        original = Path.replace
        def replace(source, target):
            if Path(target).parent == root / "cache/deltas":
                raise OSError("injected delta failure")
            return original(source, target)
        with patch.object(Path, "replace", replace), self.assertRaisesRegex(OSError, "delta failure"):
            phase1.run_phase1(root, now=LATER)
        self.assertEqual(marker.read_bytes(), before)
        self.assertIn("inbox/new.md", [item["relative_path"] for item in phase1.run_phase1(root, now=LATER)["changes"]])

    def test_phase1_late_audit_failure_preserves_work_for_retry_without_duplicate_changes(self):
        root = self.workspace()
        (root / "inbox/note.md").write_text("docs initial")
        with patch.object(phase1, "_append_audit", side_effect=OSError("injected audit write failure")), \
                self.assertRaisesRegex(ValueError, "audit_incomplete.*processed state.*injected audit"):
            phase1.run_phase1(root, now=STAMP)
        pending = list((root / "cache/deltas").glob("*.json"))
        self.assertEqual(len(pending), 1)
        self.assertTrue((root / "cache/state/phase1_state.json").is_file())
        self.assertEqual(phase1.run_phase1(root, now=LATER)["changes"], [])
        self.assertEqual(supervisor.run_supervisor(root, now=LATER)["status"], "complete")
        draft = _util.read_json(root / "cache/workers/execution_engine/draft.json")
        self.assertEqual([item["relative_path"] for item in draft["items"]], ["inbox/note.md"])

    def test_supervisor_late_audit_failure_records_recoverable_partial_outcome(self):
        root = self.workspace()
        (root / "inbox/note.md").write_text("docs initial")
        phase1.run_phase1(root, now=STAMP)
        with patch.object(supervisor, "_append_audit", side_effect=OSError("injected audit write failure")), \
                self.assertRaisesRegex(ValueError, "audit_incomplete.*injected audit"):
            supervisor.run_supervisor(root, now=STAMP)
        self.assertIn("audit_incomplete", supervisor.status_summary(root))
        self.assertTrue((root / "cache/workers/execution_engine/draft.json").is_file())
        self.assertEqual(len(list((root / "cache/deltas/archive").glob("*.json"))), 1)
        with patch.object(supervisor, "run_worker", side_effect=AssertionError("replayed work")):
            self.assertEqual(supervisor.run_supervisor(root, now=LATER)["status"], "idle")
        self.assertNotIn("audit_incomplete", supervisor.status_summary(root))

    def test_orphan_worker_state_is_not_reset_or_treated_as_idle(self):
        for worker_id, missing in (("context_builder", "context_builder/context_package.json"),
                                   ("execution_engine", "execution_engine/draft.json"),
                                   ("execution_engine", "context_builder/context_package.json")):
            root = self.workspace(seed=True)
            (root / "cache/workers" / missing).unlink()
            marker = root / "cache/workers" / worker_id / "state.json"
            before = marker.read_bytes()
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "state_inconsistent"):
                worker.run_worker(root, worker_id)
            self.assertEqual(marker.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "state_inconsistent"):
                supervisor.run_supervisor(root)
            with self.assertRaisesRegex(ValueError, "state_inconsistent"):
                supervisor.status_summary(root)
            self.assertTrue(any(level == "fail" and "state_inconsistent" in message
                                for level, message, _ in doctor.run_doctor(root)))

    def test_missing_world_with_dependent_evidence_is_not_never_run(self):
        root = self.workspace(seed=True)
        (root / ".world_state.json").unlink()
        before = {p: p.read_bytes() for p in (root / "cache/workers").rglob("*") if p.is_file()}
        for operation in (supervisor.load_world_state, supervisor.status_summary, supervisor.run_supervisor):
            with self.subTest(operation=operation.__name__), self.assertRaisesRegex(ValueError, "state_inconsistent"):
                operation(root)
        self.assertFalse((root / ".world_state.json").exists())
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_first_supervisor_preflights_world_state_temp_path(self):
        root = self.workspace()
        (root / "inbox/note.md").write_text("docs initial")
        delta = root / phase1.run_phase1(root, now=STAMP)["delta_path"]
        before = delta.read_bytes()
        world = root / ".world_state.json"
        sentinel = self.base / "outside"
        sentinel.write_bytes(b"outside evidence")
        temp = world.with_name(f"{world.name}.{os.getpid()}.tmp")
        temp.symlink_to(sentinel)
        with patch.object(supervisor, "run_worker", side_effect=AssertionError("worker ran")), \
                self.assertRaisesRegex(UnsafePathError, "world_state"):
            supervisor.run_supervisor(root, now=STAMP)
        self.assertEqual(sentinel.read_bytes(), b"outside evidence")
        self.assertEqual(delta.read_bytes(), before)
        self.assertTrue(temp.is_symlink())
        self.assertFalse(world.exists())
        self.assertFalse((root / "logs/transactions/supervisor.jsonl").exists())
        temp.unlink()
        self.assertEqual(supervisor.run_supervisor(root, now=LATER)["status"], "complete")

    def test_first_supervisor_outcome_write_failure_does_not_log_success(self):
        root = self.workspace()
        (root / "inbox/note.md").write_text("docs initial")
        delta = root / phase1.run_phase1(root, now=STAMP)["delta_path"]
        world = root / ".world_state.json"
        audit = root / "logs/transactions/supervisor.jsonl"
        original_write = supervisor._write_json

        def write(path, data):
            if path == world and data.get("last_run_at") == STAMP:
                raise OSError("injected first outcome failure")
            original_write(path, data)

        with patch.object(supervisor, "_write_json", side_effect=write), \
                self.assertRaisesRegex(OSError, "state_incomplete.*injected first outcome"):
            supervisor.run_supervisor(root, now=STAMP)
        self.assertIn("state_incomplete", supervisor.status_summary(root))
        self.assertFalse(audit.exists())
        self.assertFalse(delta.exists())
        self.assertTrue((root / "cache/deltas/archive" / delta.name).is_file())
        draft_path = root / "cache/workers/execution_engine/draft.json"
        draft_before = draft_path.read_bytes()
        with patch.object(supervisor, "run_worker", side_effect=AssertionError("replayed work")):
            self.assertEqual(supervisor.run_supervisor(root, now=LATER)["status"], "idle")
        self.assertEqual(draft_path.read_bytes(), draft_before)
        self.assertEqual([event["status"] for event in _util.inspect_audit_log(audit).events], ["idle"])
        self.assertNotIn("state_incomplete", supervisor.status_summary(root))

    def test_first_supervisor_context_state_failure_can_retry(self):
        root = self.workspace()
        (root / "inbox/note.md").write_text("docs initial")
        delta = root / phase1.run_phase1(root, now=STAMP)["delta_path"]
        before = delta.read_bytes()
        context_dir = root / "cache/workers/context_builder"
        audit = root / "logs/transactions/supervisor.jsonl"
        with self.fail_replace(context_dir / "state.json"), self.assertRaisesRegex(OSError, "injected"):
            supervisor.run_supervisor(root, now=STAMP)
        self.assertIn("state_incomplete", supervisor.status_summary(root))
        self.assertTrue((context_dir / "context_package.json").is_file())
        self.assertEqual(delta.read_bytes(), before)
        self.assertFalse(audit.exists())
        self.assertEqual(supervisor.run_supervisor(root, now=LATER)["status"], "complete")
        draft = _util.read_json(root / "cache/workers/execution_engine/draft.json")
        self.assertEqual([item["relative_path"] for item in draft["items"]], ["inbox/note.md"])
        self.assertEqual([event["status"] for event in _util.inspect_audit_log(audit).events], ["complete"])

    def test_first_supervisor_checkpoint_after_standalone_context(self):
        root = self.workspace()
        (root / "inbox/note.md").write_text("docs initial")
        phase1.run_phase1(root, now=STAMP)
        self.assertEqual(worker.run_worker(root, "context_builder", now=STAMP)["status"], "complete")
        self.assertIsNone(supervisor.load_world_state(root)["last_run_at"])
        world = root / ".world_state.json"
        original_write = supervisor._write_json

        def write(path, data):
            if path == world and data.get("last_run_at") == STAMP:
                raise OSError("injected first outcome failure")
            original_write(path, data)

        with patch.object(supervisor, "_write_json", side_effect=write), self.assertRaisesRegex(OSError, "state_incomplete"):
            supervisor.run_supervisor(root, now=STAMP)
        self.assertIn("state_incomplete", supervisor.status_summary(root))
        self.assertFalse((root / "logs/transactions/supervisor.jsonl").exists())
        draft = root / "cache/workers/execution_engine/draft.json"
        before = draft.read_bytes()
        with patch.object(supervisor, "run_worker", side_effect=AssertionError("replayed work")):
            self.assertEqual(supervisor.run_supervisor(root, now=LATER)["status"], "idle")
        self.assertEqual(draft.read_bytes(), before)
        self.assertNotIn("state_incomplete", supervisor.status_summary(root))

    def test_world_marker_failure_keeps_prior_world_and_evidence(self):
        root = self.workspace(seed=True)
        (root / "inbox/new.md").write_text("docs new")
        phase1.run_phase1(root, now=LATER)
        world = root / ".world_state.json"
        before = world.read_bytes()
        with self.fail_replace(world), self.assertRaisesRegex(OSError, "injected"):
            supervisor.run_supervisor(root, now=LATER)
        self.assertEqual(world.read_bytes(), before)
        self.assertTrue((root / "cache/workers/execution_engine/draft.json").is_file())
        self.assertFalse(locking.is_held(root / ".looping_box.lock"))

    def test_rollback_replace_failure_retains_current_complete_file(self):
        root = self.workspace(seed=True)
        snapshot = supervisor._snapshot_worker_dir(root, "execution_engine")
        target = root / "cache/workers/execution_engine/draft.md"
        target.write_text("current complete document")
        with self.fail_replace(target), self.assertRaisesRegex(OSError, "injected"):
            supervisor._restore_worker_dir(root, "execution_engine", snapshot)
        self.assertEqual(target.read_text(), "current complete document")


if __name__ == "__main__":
    unittest.main()
