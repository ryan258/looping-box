"""T03 / A24: real process contention, persistent inodes, and descriptor lifetime."""
import contextlib
import json
import os
import selectors
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from looping_box import _util, doctor, locking, model, phase1, review, scaffold, supervisor, worker

STAMP = "2026-10-13T12:00:00Z"
HOLDER = '''
import sys
from pathlib import Path
from looping_box._util import project_lock
with project_lock(Path(sys.argv[1]), "1900-01-01T00:00:00Z", 1):
    print("ready", flush=True)
    sys.stdin.readline()
'''


class KernelLockTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "workspace"
        self.env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT / "src"),
                    "XDG_CONFIG_HOME": str(Path(temp.name) / "keys"), "LOOPING_BOX_REVIEW_KEY": "fake-test-key"}
        env = patch.dict(os.environ, self.env, clear=True)
        env.start()
        self.addCleanup(env.stop)
        network = patch("looping_box.model._transport", side_effect=AssertionError("network forbidden"))
        self.transport = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(model._env_loaded_for.clear)
        scaffold.init_workspace(self.root)
        self.lock = self.root / ".looping_box.lock"

    @contextlib.contextmanager
    def holder(self):
        proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(self.root)], env=self.env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(5), "holder did not become ready")
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            yield proc
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)

    def test_every_public_mutator_refuses_a_real_holder_and_ancient_metadata(self):
        (self.root / "inbox/held.md").write_text("please deploy")
        phase1.run_phase1(self.root, now=STAMP)
        rid = review.list_reviews(self.root)[0]["review_id"]
        operations = [partial(phase1.run_phase1, self.root), partial(supervisor.run_supervisor, self.root),
                      partial(worker.run_worker, self.root, "context_builder"),
                      partial(worker.run_worker, self.root, "execution_engine"),
                      partial(review.record_review, self.root, rid, "rejected", note="checked"),
                      partial(review.run_verifier, self.root, rid), partial(scaffold.init_workspace, self.root)]
        with self.holder():
            self.lock.write_text(json.dumps({"created_at": "1800-01-01T00:00:00Z", "pid": -1}))
            before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
            for operation in operations:
                with self.subTest(operation=operation.func.__name__), self.assertRaisesRegex(RuntimeError, "busy"):
                    operation()
            after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
            self.assertEqual(after, before)
        self.transport.assert_not_called()

    def test_cli_mutators_return_busy_exit_one(self):
        commands = [["phase1", "--root", str(self.root)], ["supervisor", "--root", str(self.root), "--once"],
                    ["worker", "context_builder", "--root", str(self.root)],
                    ["review", "--root", str(self.root), "reject", "review-" + "a" * 16, "--note", "checked"],
                    ["init", str(self.root)], ["run", "--root", str(self.root)]]
        with self.holder():
            for command in commands:
                with self.subTest(command=command[0]):
                    result = subprocess.run([sys.executable, "-m", "looping_box", *command], env=self.env,
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("busy", result.stderr)

    def test_process_death_releases_lock_without_replacing_inode(self):
        inode = self.lock.stat().st_ino
        with self.holder() as proc:
            self.assertTrue(locking.is_held(self.lock))
            proc.kill()
            proc.wait(timeout=5)
            self.assertFalse(locking.is_held(self.lock))
            self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "idle")
        self.assertEqual(self.lock.stat().st_ino, inode)

    def test_metadata_is_not_ownership_and_unowned_release_cannot_delete(self):
        for metadata in ("broken JSON", '{"pid":999,"created_at":"2999-01-01T00:00:00Z"}'):
            self.lock.write_text(metadata)
            inode = self.lock.stat().st_ino
            _util.release_lock(self.lock, os.getpid())
            self.assertEqual(self.lock.read_text(), metadata)
            with _util.project_lock(self.root, STAMP, 0):
                self.assertTrue(locking.is_held(self.lock))
            self.assertFalse(locking.is_held(self.lock))
            self.assertEqual(self.lock.stat().st_ino, inode)

    def test_nested_owner_retains_outer_lock_and_other_thread_is_busy(self):
        failures = []
        def competing_thread():
            try:
                with _util.project_lock(self.root, STAMP):
                    failures.append("entered")
            except RuntimeError:
                failures.append("busy")
        with _util.project_lock(self.root, STAMP):
            with _util.project_lock(self.root, STAMP):
                self.assertTrue(locking.is_held(self.lock))
            self.assertTrue(locking.is_held(self.lock))
            thread = threading.Thread(target=competing_thread)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, ["busy"])
            _util.release_lock(self.lock, os.getpid() + 1)
            self.assertTrue(locking.is_held(self.lock))
        self.assertFalse(locking.is_held(self.lock))

    def test_forked_child_does_not_inherit_registry_ownership(self):
        reader, writer = os.pipe()
        with _util.project_lock(self.root, STAMP):
            pid = os.fork()
            if pid == 0:
                os.close(reader)
                try:
                    with _util.project_lock(self.root, STAMP):
                        os.write(writer, b"entered")
                except RuntimeError:
                    os.write(writer, b"busy")
                finally:
                    os.close(writer)
                    os._exit(0)
            os.close(writer)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(reader, selectors.EVENT_READ)
                    self.assertTrue(selector.select(5), "forked child hung")
                self.assertEqual(os.read(reader, 20), b"busy")
            finally:
                os.close(reader)
                os.waitpid(pid, 0)
            self.assertTrue(locking.is_held(self.lock))

    def test_fork_waits_for_descriptor_registration_in_another_thread(self):
        script = r"""
import os, sys, threading
from pathlib import Path
from looping_box import locking
path = Path(sys.argv[1]) / ".looping_box.lock"
inside_fsync = threading.Event()
allow_fsync = threading.Event()
fork_at_guard = threading.Event()
fork_returned = threading.Event()
release_owner = threading.Event()
original_fsync = os.fsync
errors = []
original_guard = locking._guard
class ObservedGuard:
    def __enter__(self):
        return original_guard.__enter__()
    def __exit__(self, *args):
        return original_guard.__exit__(*args)
    def acquire(self):
        fork_at_guard.set()
        return original_guard.acquire()
    def release(self):
        return original_guard.release()
locking._guard = ObservedGuard()
def controlled_fsync(fd):
    inside_fsync.set()
    if not allow_fsync.wait(5):
        raise RuntimeError("fsync wait timed out")
    original_fsync(fd)
os.fsync = controlled_fsync
def own():
    try:
        locking.acquire(path, "2026-10-13T12:00:00Z")
        if not release_owner.wait(5):
            raise RuntimeError("release wait timed out")
        locking.release(path, os.getpid())
    except Exception as exc:
        errors.append(str(exc))
owner = threading.Thread(target=own)
owner.start()
assert inside_fsync.wait(5)
# Observe the production at-fork guard independently of thread scheduling.
reader, writer = os.pipe()
def fork_once():
    pid = os.fork()
    if pid == 0:
        os.close(reader)
        try:
            locking.acquire(path, "2026-10-13T12:00:00Z")
            os.write(writer, b"entered")
        except RuntimeError:
            os.write(writer, b"busy")
        finally:
            os._exit(0)
    fork_returned.set()
    os.close(writer)
    result = os.read(reader, 20)
    os.close(reader)
    os.waitpid(pid, 0)
    if result != b"busy":
        errors.append(f"child unexpectedly acquired inherited lock: {result!r}")
def fork_child():
    try:
        fork_once()
    except BaseException as exc:
        errors.append(f"fork thread failed: {exc!r}")
forker = threading.Thread(target=fork_child)
forker.start()
try:
    assert fork_at_guard.wait(5), "fork did not serialize against descriptor registration"
    assert not fork_returned.is_set(), "fork crossed the unfinished metadata write"
finally:
    allow_fsync.set()
forker.join(5)
assert not forker.is_alive()
release_owner.set()
owner.join(5)
assert not owner.is_alive()
assert not errors, errors
assert not locking.is_held(path)
print("fork registration protected")
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.root)], env=self.env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fork registration protected", result.stdout)

    def test_dry_run_and_read_only_commands_do_not_create_lock(self):
        self.lock.unlink()  # isolated unlocked fixture: simulate no previous mutator
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        for worker_id in ("context_builder", "execution_engine"):
            worker.run_worker(self.root, worker_id, dry_run=True)
        supervisor.status_summary(self.root)
        doctor.run_doctor(self.root)
        self.assertFalse(self.lock.exists())
        self.assertEqual(sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")), before)
        self.transport.assert_not_called()

    def test_doctor_uses_kernel_probe_without_changing_metadata(self):
        with self.holder():
            before = self.lock.read_bytes()
            findings = doctor.run_doctor(self.root)
            self.assertTrue(any(level == "ok" and "kernel lock held" in message for level, message, _ in findings))
            self.assertEqual(self.lock.read_bytes(), before)
        self.assertTrue(any(message == "no lock held" for _, message, _ in doctor.run_doctor(self.root)))

    def test_combined_run_nests_workers_and_releases_after_error(self):
        (self.root / "inbox/note.md").write_text("docs note")
        phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "complete")
        with self.assertRaisesRegex(ValueError, "injected"), _util.project_lock(self.root, STAMP):
            raise ValueError("injected")
        self.assertFalse(locking.is_held(self.lock))
        self.assertTrue(self.lock.is_file())


if __name__ == "__main__":
    unittest.main()
