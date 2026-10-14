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
            self.assertTrue(path.with_name(f"{path.name}.{os.getpid()}.tmp").is_file())
            writer(path, data)
            self.assertNotEqual(path.read_bytes(), b"prior complete bytes\n")

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
