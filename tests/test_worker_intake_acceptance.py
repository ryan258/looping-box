"""T06 / A15, A16, A29: offline intake, read-only planning, retained evidence."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from looping_box import model, phase1, scaffold, worker
from looping_box.paths import UnsafePathError
from looping_box.validation import ValidationError, validate_data

STAMP = "2026-10-13T12:00:00Z"


class WorkerIntakeAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "workspace"
        self.env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT / "src"),
                    "XDG_CONFIG_HOME": str(self.base / "keys"), "LOOPING_BOX_REVIEW_KEY": "fake-worker-key"}
        env = patch.dict(os.environ, self.env, clear=True)
        env.start()
        self.addCleanup(env.stop)
        network = patch.object(model, "_transport", side_effect=AssertionError("live provider forbidden"))
        self.transport = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(model._env_loaded_for.clear)
        scaffold.init_workspace(self.root)

    def ingest(self, name="note.md", text="Project docs note."):
        (self.root / "inbox" / name).write_text(text)
        return phase1.run_phase1(self.root, now=STAMP)

    def run_worker(self, worker_id="context_builder", **kwargs):
        result = worker.run_worker(self.root, worker_id, now=STAMP, **kwargs)
        validate_data(result, "worker_output", "returned output", worker_id=worker_id)
        return result

    def snapshot(self):
        return {str(p.relative_to(self.root)): ("directory" if p.is_dir() else p.read_bytes(),
                                               p.stat().st_mode, p.stat().st_mtime_ns)
                for p in [self.root, *sorted(self.root.rglob("*"))]}

    def bad(self, name, content):
        path = self.root / "cache/deltas" / name
        path.write_bytes(content)
        return path

    def test_mixed_intake_consumes_only_valid_deltas_and_keeps_invalid_bytes(self):
        delta = self.ingest()
        good = self.root / delta["delta_path"]
        data = json.loads(good.read_text())
        unsupported = {**data, "schema": "looping-box.phase1.delta.v99"}
        invalid = copy.deepcopy(data); invalid["changes"][0]["matched_routes"] = "docs"
        invalids = {"a-json.json": b"{broken", "b-version.json": json.dumps(unsupported).encode(),
                    "c-shape.json": json.dumps(invalid).encode()}
        for name, content in invalids.items(): self.bad(name, content)
        output = self.run_worker()
        self.assertEqual(output["status"], "complete")
        self.assertEqual([e["code"] for e in output["errors"]],
                         ["malformed_delta_quarantined", "unsupported_delta_schema", "invalid_delta"])
        self.assertEqual(output["inputs"]["source_deltas"], [delta["delta_path"]])
        state = json.loads((self.root / "cache/workers/context_builder/state.json").read_text())
        self.assertEqual(state["consumed_inputs"], [delta["delta_path"]])
        for name, content in invalids.items():
            self.assertFalse((good.parent / name).exists())
            self.assertEqual((good.parent / (name + ".bad")).read_bytes(), content)
        self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
        draft = json.loads((self.root / "cache/workers/execution_engine/draft.json").read_text())
        self.assertEqual(draft["items"][0]["draft"], "Project docs note.")
        self.transport.assert_not_called()

    def test_invalid_only_fails_without_advancing_existing_success(self):
        self.ingest(); self.run_worker()
        directory = self.root / "cache/workers/context_builder"
        before = {name: (directory / name).read_bytes() for name in ("state.json", "context_package.json", "context_package.md")}
        self.bad("invalid.json", b"[]")
        output = self.run_worker()
        self.assertEqual(output["status"], "failed")
        self.assertEqual(output["errors"][0]["code"], "invalid_delta")
        self.assertEqual(output["inputs"]["source_deltas"], [])
        self.assertEqual({name: (directory / name).read_bytes() for name in before}, before)
        self.assertFalse((directory / "history").exists())
        self.assertEqual(json.loads((directory / "last_output.json").read_text())["status"], "failed")

    def test_invalid_only_cli_exits_one_and_does_not_create_success_state(self):
        self.bad("invalid.json", b"null")
        result = subprocess.run([sys.executable, "-m", "looping_box", "worker", "context_builder",
                                 "--root", str(self.root)], env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((self.root / "cache/workers/context_builder/state.json").exists())
        self.assertEqual((self.root / "cache/deltas/invalid.json.bad").read_bytes(), b"null")

    def test_full_shape_and_json_errors_are_quarantined(self):
        delta = self.ingest()
        original = self.root / delta["delta_path"]
        data = json.loads(original.read_text()); original.unlink()
        cases = [(b"\xff", "malformed_delta_quarantined"), (b"[" * 1500, "malformed_delta_quarantined"),
                 (b"{}", "invalid_delta"), (b"false", "invalid_delta")]
        for field, value in (("changes", [None]), ("schema", None), ("generated_at", "yesterday"),
                             ("boundary_gate", []), ("warnings", "bad")):
            broken = copy.deepcopy(data); broken[field] = value
            cases.append((json.dumps(broken).encode(), "invalid_delta"))
        broken = copy.deepcopy(data); broken["summary"]["scanned"] = True
        cases.append((json.dumps(broken).encode(), "invalid_delta"))
        broken = copy.deepcopy(data); broken["changes"][0]["review_reasons"] = ["deploy"]
        cases.append((json.dumps(broken).encode(), "invalid_delta"))
        for i, (content, _) in enumerate(cases): self.bad(f"invalid-{i:02}.json", content)
        output = self.run_worker()
        self.assertEqual(output["status"], "failed")
        self.assertEqual([e["code"] for e in output["errors"]], [code for _, code in cases])
        for i, (content, _) in enumerate(cases):
            self.assertEqual((original.parent / f"invalid-{i:02}.json.bad").read_bytes(), content)
        self.assertFalse((self.root / "cache/workers/context_builder/state.json").exists())

    def test_quarantine_collisions_preserve_every_version(self):
        path = self.bad("broken.json", b"current")
        for suffix, content in ((".bad", b"first"), (".bad2", b"second")):
            path.with_name(path.name + suffix).write_bytes(content)
        output = self.run_worker()
        self.assertEqual(output["status"], "failed")
        self.assertEqual(path.with_name(path.name + ".bad3").read_bytes(), b"current")
        self.assertEqual(path.with_name(path.name + ".bad").read_bytes(), b"first")
        self.assertEqual(path.with_name(path.name + ".bad2").read_bytes(), b"second")

    def test_read_or_quarantine_failure_is_fatal_even_with_valid_work(self):
        self.ingest()
        bad = self.bad("broken.json", b"{broken")
        original_read = worker._read_json
        def read(path):
            if path == bad: raise PermissionError("denied reading delta")
            return original_read(path)
        for target, replacement in (("_read_json", read), ("_quarantine_delta", PermissionError("denied rename"))):
            with self.subTest(target=target), patch.object(worker, target, side_effect=replacement), patch.object(model, "generate_if_enabled") as generate:
                output = self.run_worker()
                self.assertEqual(output["status"], "failed")
                self.assertEqual(output["errors"][0]["code"], "delta_io_error")
                generate.assert_not_called()
                self.assertEqual(bad.read_bytes(), b"{broken")
                self.assertFalse(bad.with_name(bad.name + ".bad").exists())
                self.assertFalse((self.root / "cache/workers/context_builder/state.json").exists())
                self.assertFalse((self.root / "cache/workers/context_builder/context_package.json").exists())

    def test_quarantine_error_provenance_survives_subsequent_model_failure(self):
        self.ingest(); self.bad("broken.json", b"{broken")
        with patch.object(model, "generate_if_enabled", side_effect=model.ModelError("offline stub failed")):
            output = self.run_worker()
        self.assertEqual(output["status"], "failed")
        self.assertEqual([e["code"] for e in output["errors"]], ["malformed_delta_quarantined", "model_error"])
        self.assertFalse((self.root / "cache/workers/context_builder/state.json").exists())
        self.assertTrue((self.root / "cache/deltas/broken.json.bad").exists())

    def test_directory_read_failure_is_not_mistaken_for_idle(self):
        self.ingest()
        delta_dir = self.root / "cache/deltas"
        original = Path.iterdir
        def entries(path):
            if path == delta_dir: raise PermissionError("cannot list delta intake")
            return original(path)
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), patch.object(Path, "iterdir", entries), patch.object(model, "generate_if_enabled") as generate:
                output = self.run_worker(dry_run=dry_run)
            self.assertEqual(output["status"], "failed")
            self.assertEqual(output["errors"][0]["code"], "delta_io_error")
            generate.assert_not_called()
            self.assertFalse((self.root / "cache/workers/context_builder/state.json").exists())
        self.assertTrue(list(delta_dir.glob("*.json")))

    def test_dry_run_both_workers_preserves_complete_tree_and_sends_nothing(self):
        self.ingest(); self.run_worker(); self.run_worker("execution_engine")
        self.ingest("second.md", "Project docs second."); self.run_worker()
        self.ingest("third.md", "Project docs third.")
        bad = self.bad("broken.json", b"{broken")
        bad.with_name(bad.name + ".bad").write_bytes(b"retained")
        (self.root / ".looping_box.lock").unlink(missing_ok=True)
        # Enable roles without credentials: any attempted model call is a test failure.
        (self.root / ".env").write_text("MODEL_CONTEXT_BUILDER=offline-stub\nMODEL_EXECUTION_ENGINE=offline-stub\n")
        before = self.snapshot()
        with patch.object(model, "generate_if_enabled", side_effect=AssertionError("dry run called model")):
            context = self.run_worker(dry_run=True)
            execution = self.run_worker("execution_engine", dry_run=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(context["errors"][0]["code"], "malformed_delta")
        self.assertIn({"action": "quarantine", "source": "cache/deltas/broken.json",
                       "destination": "cache/deltas/broken.json.bad2"}, context["planned_actions"])
        for output, primary in ((context, "context_package"), (execution, "draft")):
            prefix = f"cache/workers/{output['worker_id']}/"
            for name in (primary + ".json", primary + ".md", "state.json", "last_output.json"):
                self.assertIn({"action": "write", "destination": prefix + name}, output["planned_actions"])
            self.assertTrue(any(a["action"] == "archive" for a in output["planned_actions"]))
        self.transport.assert_not_called()

    def test_invalid_only_and_pristine_dry_runs_are_read_only(self):
        (self.root / ".looping_box.lock").unlink(missing_ok=True)
        before = self.snapshot()
        self.assertEqual(self.run_worker(dry_run=True)["status"], "idle")
        self.assertEqual(self.run_worker("execution_engine", dry_run=True)["status"], "blocked")
        self.assertEqual(self.snapshot(), before)
        self.bad("broken.json", b"{broken")
        before = self.snapshot()
        output = self.run_worker(dry_run=True)
        self.assertEqual(output["status"], "failed")
        self.assertEqual(output["errors"][0]["code"], "malformed_delta")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / ".looping_box.lock").exists())

    def test_archived_source_references_do_not_require_active_delta_files(self):
        delta = self.ingest(); self.run_worker()
        path = self.root / delta["delta_path"]
        archive = path.parent / "archive"; archive.mkdir()
        path.rename(archive / path.name)
        self.assertEqual(self.run_worker()["status"], "idle")
        self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
        self.assertFalse(path.exists())

    def test_same_second_history_retains_both_batches_and_markdown(self):
        expected = {}
        for number in range(3):
            self.ingest(f"note-{number}.md", f"Project docs batch {number}.")
            self.run_worker(); self.run_worker("execution_engine")
            for wid, primary in (("context_builder", "context_package"), ("execution_engine", "draft")):
                directory = self.root / f"cache/workers/{wid}"
                for extension in ("json", "md"):
                    path = directory / f"{primary}.{extension}"
                    if number < 2:
                        expected[(wid, f"1000{'-2' if number else ''}-{path.name}")] = path.read_bytes()
                    os.utime(path, (1000, 1000))
        for (wid, name), content in expected.items():
            self.assertEqual((self.root / f"cache/workers/{wid}/history" / name).read_bytes(), content)

    def test_history_preserves_held_only_and_corrupt_evidence(self):
        self.ingest(text="please deploy")
        self.run_worker(); self.run_worker("execution_engine")
        for wid, primary in (("context_builder", "context_package"), ("execution_engine", "draft")):
            directory = self.root / f"cache/workers/{wid}"
            names = (primary + ".json", primary + ".md")
            for name in names: os.utime(directory / name, (1000, 1000))
            before = {name: (directory / name).read_bytes() for name in names}
            worker._archive_previous(directory, names)
            for name, content in before.items(): self.assertEqual((directory / "history" / f"1000-{name}").read_bytes(), content)
            (directory / names[0]).write_bytes(b"\xff corrupt evidence")
            os.utime(directory / names[0], (1000, 1000))
            worker._archive_previous(directory, names)
            self.assertEqual((directory / "history" / f"1000-2-{names[0]}").read_bytes(), b"\xff corrupt evidence")
            with self.assertRaises(ValidationError): self.run_worker(wid)
            self.assertEqual((directory / names[0]).read_bytes(), b"\xff corrupt evidence")

    def test_truly_empty_batches_do_not_grow_history(self):
        directory = self.root / "cache/workers/context_builder"
        context = {"schema": worker.CONTEXT_PACKAGE_SCHEMA, "worker_id": "context_builder", "generated_at": STAMP,
                   "status": "complete", "source_deltas": [], "items": [], "blocked_inputs": []}
        (directory / "context_package.json").write_text(json.dumps(context))
        (directory / "context_package.md").write_text("empty batch")
        worker._archive_previous(directory, ("context_package.json", "context_package.md"))
        self.assertFalse((directory / "history").exists())

    def test_quarantine_link_trap_preserves_source_and_external_sentinel(self):
        bad = self.bad("broken.json", b"{broken")
        sentinel = self.base / "sentinel"; sentinel.write_bytes(b"untouched")
        bad.with_name(bad.name + ".bad").symlink_to(sentinel)
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), self.assertRaisesRegex(UnsafePathError, "delta_io_error"):
                self.run_worker(dry_run=dry_run)
        self.assertEqual(bad.read_bytes(), b"{broken")
        self.assertEqual(sentinel.read_bytes(), b"untouched")


if __name__ == "__main__":
    unittest.main()
