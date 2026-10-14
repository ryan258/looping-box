"""T08: SUP-01–06 / A19, A22, A23, A29; isolated workspaces and offline transport."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from looping_box import model, phase1, scaffold, supervisor
from looping_box.eligibility import SourceReingestRequired
from looping_box.validation import validate_data

STAMP = "2026-10-03T12:00:00Z"


class SupervisorAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "workspace"
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.base / "keys"),
                                     "LOOPING_BOX_REVIEW_KEY": "fixture-review-key",
                                     "LOOPING_BOX_APPROVER": "fixture-operator"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        model._env_loaded_for.clear()
        self.addCleanup(model._env_loaded_for.clear)
        transport = patch.object(model, "_transport", side_effect=AssertionError("live provider forbidden"))
        transport.start()
        self.addCleanup(transport.stop)
        scaffold.init_workspace(self.root)
        self.calls = []

    def ingest(self, name="one.txt", text="Project docs one."):
        (self.root / "inbox" / name).write_text(text, encoding="utf-8")
        return phase1.run_phase1(self.root, now=STAMP)["delta_path"]

    def run_pass(self, now=STAMP):
        result = supervisor.run_supervisor(self.root, now=now)
        validate_data(result, "supervisor_run", "result")
        validate_data(supervisor.load_world_state(self.root), "world_state", "world")
        return result

    def config(self, **values):
        config = {**supervisor.DEFAULT_CONFIG, **values}
        (self.root / "config/super_loop.json").write_text(json.dumps(config), encoding="utf-8")

    def saved(self, name):
        return json.loads((self.root / name).read_text())

    def worker_files(self):
        directory = self.root / "cache/workers"
        return {path.relative_to(directory).as_posix(): path.read_bytes()
                for path in directory.rglob("*") if path.is_file()}

    def enable(self, role="execution_engine", model_id="fixture/execution"):
        os.environ["OPENROUTER_API_KEY"] = "fixture-api-key"
        os.environ["MODEL_" + role.upper()] = model_id

    def transport(self, response="MODEL DRAFT"):
        def send(url, headers, body, timeout):
            data = json.loads(body)
            self.calls.append((data, timeout))
            if isinstance(response, Exception):
                raise response
            return json.dumps({"model": data["model"], "choices": [{"message": {"content": response}}]})
        return patch.object(model, "_transport", side_effect=send)

    def cli(self, mode):
        with patch.object(sys, "argv", ["supervisor", "--root", str(self.root), mode]), \
                patch("sys.stdout", new_callable=io.StringIO) as output, \
                patch("sys.stderr", new_callable=io.StringIO):
            code = supervisor.main()
        return code, output.getvalue()

    def test_a19_model_only_invalidation_and_key_only_idle(self):
        self.ingest()
        self.assertEqual(self.run_pass()["status"], "complete")
        self.enable()
        with self.transport():
            result = self.run_pass()
            self.assertEqual(result["plan"], ["execution_engine"])
            self.assertEqual(result["status"], "complete")
            self.assertEqual(self.run_pass()["status"], "idle")
            os.environ["OPENROUTER_API_KEY"] = "fixture-rotated-key"
            self.assertEqual(self.run_pass()["status"], "idle")
            self.enable(model_id="fixture/other")
            self.assertEqual(self.run_pass()["plan"], ["execution_engine"])
        self.assertEqual(len(self.calls), 2)
        draft = self.saved("cache/workers/execution_engine/draft.json")
        self.assertEqual(draft["items"][0]["draft"], "MODEL DRAFT")

    def test_a23_quiescent_pending_review_blocks_and_status_is_inspection(self):
        reference = self.ingest(text="Please deploy this release.")
        self.assertEqual(self.run_pass()["status"], "blocked")
        self.assertFalse((self.root / reference).exists())
        result = self.run_pass()
        self.assertEqual(result["plan"], [])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["recovery"]["blocked_reason"], "pending_review")
        self.assertEqual(self.cli("--once")[0], 2)
        code, output = self.cli("--status")
        self.assertEqual(code, 0)
        self.assertIn("pending reviews: 1", output)

    def test_missing_live_review_evidence_fails_before_workers(self):
        self.ingest(text="Please deploy this release.")
        (self.root / "staging/pending_review.json").unlink()
        with patch.object(supervisor, "run_worker") as run:
            self.assertEqual(self.cli("--once")[0], 1)
            run.assert_not_called()
        self.assertEqual(self.cli("--status")[0], 1)

    def test_invalid_config_rejection_preserves_workspace_and_lock_bytes(self):
        self.config(max_worker_runtime_seconds=-1)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch.object(supervisor, "run_worker") as run:
            self.assertEqual(self.cli("--once")[0], 1)
            run.assert_not_called()
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_context_failure_never_executes_prior_context_or_archives(self):
        self.ingest()
        self.run_pass()
        old_draft = (self.root / "cache/workers/execution_engine/draft.json").read_bytes()
        reference = self.ingest("two.txt", "Project docs two.")
        self.enable("context_builder", "fixture/context")
        with self.transport(RuntimeError("fixture context outage")):
            result = self.run_pass()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["plan"], ["context_builder"])
        self.assertTrue((self.root / reference).exists())
        self.assertEqual((self.root / "cache/workers/execution_engine/draft.json").read_bytes(), old_draft)
        self.assertEqual(len(self.calls), 1)
        with self.transport("CONTEXT SUMMARY"):
            self.assertEqual(self.run_pass()["status"], "complete")
        self.assertFalse((self.root / reference).exists())

    def test_a23_disabled_context_route_retains_unacknowledged_work(self):
        reference = self.ingest()
        # A remembered filename alone is not a context consumption acknowledgement.
        state = supervisor.load_world_state(self.root)
        state["observed_deltas"] = [reference]
        (self.root / ".world_state.json").write_text(json.dumps(state))
        self.config(workers=["execution_engine"], dependencies=[])
        with patch.object(supervisor, "run_worker") as run:
            for _ in range(2):
                result = self.run_pass()
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["recovery"]["blocked_reason"], "routing_disabled")
                self.assertTrue((self.root / reference).exists())
            run.assert_not_called()
        self.assertIn("config/super_loop.json", supervisor.status_summary(self.root))
        self.config()
        self.assertEqual(self.run_pass()["status"], "complete")
        self.assertFalse((self.root / reference).exists())

    def test_a23_disabled_execution_retains_context_then_resumes(self):
        reference = self.ingest()
        self.config(dependencies=[{"from": "phase1_delta", "to": "context_builder"}])
        first = self.run_pass()
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(first["plan"], ["context_builder"])
        context = (self.root / "cache/workers/context_builder/context_package.json").read_bytes()
        second = self.run_pass()
        self.assertEqual(second["plan"], [])
        self.assertEqual(second["recovery"]["blocked_reason"], "routing_disabled")
        self.assertTrue((self.root / reference).exists())
        self.config()
        result = self.run_pass()
        self.assertEqual(result["plan"], ["execution_engine"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual((self.root / "cache/workers/context_builder/context_package.json").read_bytes(), context)
        self.assertFalse((self.root / reference).exists())

    def test_invalid_deltas_are_quarantined_not_counted_or_archived(self):
        reference = self.ingest()
        directory = self.root / "cache/deltas"
        invalid = {"invalid-json.json": "{", "unknown-version.json": '{"schema":"wrong"}',
                   "wrong-shape.json": "[]"}
        for name, content in invalid.items():
            (directory / name).write_text(content)
        self.config(max_files_per_cycle=1)
        result = self.run_pass()
        self.assertEqual(result["status"], "complete")
        self.assertFalse((self.root / reference).exists())
        self.assertEqual(len(result["worker_outputs"][0]["errors"]), 3)
        for name, content in invalid.items():
            self.assertEqual((directory / (name + ".bad")).read_text(), content)
            self.assertFalse((directory / "archive" / name).exists())

    def test_invalid_only_failure_wins_over_live_pending_review(self):
        self.ingest(text="Please deploy this release.")
        self.run_pass()
        invalid = self.root / "cache/deltas/invalid.json"
        invalid.write_text("{")
        result = self.run_pass()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["plan"], ["context_builder"])
        self.assertTrue(invalid.with_name("invalid.json.bad").exists())
        self.assertIn("status: failed", supervisor.status_summary(self.root))

    def test_mutated_consumed_delta_is_revalidated_and_quarantined(self):
        reference = self.ingest()
        self.config(workers=["context_builder"], dependencies=[{"from": "phase1_delta", "to": "context_builder"}])
        self.run_pass()
        (self.root / reference).write_text("{")
        fresh = self.ingest("two.txt", "Project docs two.")
        self.config()
        self.assertEqual(self.run_pass()["status"], "complete")
        self.assertTrue((self.root / (reference + ".bad")).exists())
        self.assertFalse((self.root / "cache/deltas/archive" / Path(reference).name).exists())
        self.assertFalse((self.root / fresh).exists())
        # Quarantined references are not falsely pruned as archived.
        self.assertIn(reference, self.saved("cache/workers/context_builder/state.json")["consumed_inputs"])
        self.assertNotIn(reference, self.saved("cache/workers/context_builder/context_package.json")["source_deltas"])

    def test_a22_file_count_is_before_collapse_and_excludes_held(self):
        first = self.ingest()
        second = self.ingest(text="Project docs revision two.")
        for n in range(3):
            self.ingest(f"held-{n}.txt", "Please deploy this release.")
        self.config(max_files_per_cycle=1)
        with patch.object(supervisor, "run_worker") as run:
            result = self.run_pass()
            self.assertEqual(result["recovery"]["blocked_reason"], "file_count_limit")
            self.assertEqual(self.cli("--once")[0], 2)
            run.assert_not_called()
        self.assertTrue((self.root / first).exists())
        self.assertTrue((self.root / second).exists())
        self.config(max_files_per_cycle=2)
        result = self.run_pass()
        self.assertEqual(result["plan"], ["context_builder", "execution_engine"])
        self.assertEqual(result["recovery"]["blocked_reason"], "pending_review")

    def test_a22_payload_rollback_restores_both_workers_and_history(self):
        self.ingest()
        self.run_pass()
        self.ingest(text="Project docs second completed batch.")
        self.run_pass()
        before = self.worker_files()
        self.assertTrue(any("/history/" in name for name in before))
        reference = self.ingest(text="Project docs replacement.")
        invalid = self.root / "cache/deltas/invalid.json"
        invalid.write_text("{")
        self.config(max_payload_bytes=1)
        for _ in range(2):
            result = self.run_pass()
            self.assertEqual(result["recovery"]["blocked_reason"], "payload_size_limit")
            self.assertEqual(result["plan"], ["context_builder", "execution_engine"])
            self.assertEqual(self.worker_files(), before)
            self.assertTrue((self.root / reference).exists())
            self.assertTrue(all(not out["outputs"]["artifacts"] for out in result["worker_outputs"]))
        self.assertTrue(invalid.with_name("invalid.json.bad").exists())
        block = (self.root / "cache/supervisor/blocked.json").read_bytes()
        self.config()
        result = self.run_pass()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["recovery"], supervisor._clear_recovery())
        self.assertEqual((self.root / "cache/supervisor/blocked.json").read_bytes(), block)

    def test_a22_execution_timeout_rolls_back_whole_pass_but_not_traffic(self):
        self.ingest()
        self.run_pass()
        before = self.worker_files()
        reference = self.ingest(text="Project docs replacement.")
        self.enable()
        self.config(max_worker_runtime_seconds=1)
        with self.transport(), patch.object(supervisor.time, "monotonic", side_effect=[0, .1, 1, 3]):
            result = self.run_pass()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["recovery"]["blocked_reason"], "worker_timeout")
        self.assertEqual(self.worker_files(), before)
        self.assertTrue((self.root / reference).exists())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], 1)
        with self.transport(), patch.object(supervisor.time, "monotonic", side_effect=[0, .1, 1, 1.1]):
            self.assertEqual(self.run_pass()["status"], "complete")
        self.assertEqual(len(self.calls), 2)

    def test_transport_timeout_has_minimum_without_changing_elapsed_bound(self):
        self.ingest()
        self.enable("context_builder", "fixture/context")
        self.config(max_worker_runtime_seconds=.0001)
        with self.transport(), patch.object(supervisor.time, "monotonic", side_effect=[0, .0002]):
            result = self.run_pass()
        self.assertEqual(result["recovery"]["blocked_reason"], "worker_timeout")
        self.assertEqual(self.calls[0][1], .001)

    def test_failure_wins_over_runtime_block_and_is_preserved_in_result(self):
        reference = self.ingest()
        self.enable("context_builder", "fixture/context")
        self.config(max_worker_runtime_seconds=1)
        with self.transport(RuntimeError("fixture outage")), \
                patch.object(supervisor.time, "monotonic", side_effect=[0, 2]):
            result = self.run_pass()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["recovery"]["blocked_reason"], "worker_timeout")
        self.assertIn("fixture outage", result["recovery"]["last_error"])
        self.assertEqual(result["worker_outputs"][0]["errors"][0]["code"], "model_error")
        self.assertEqual(self.worker_files(), {})
        self.assertTrue((self.root / reference).exists())
        self.assertIn("status: failed", supervisor.status_summary(self.root))

    def test_failure_wins_over_payload_block_and_restores_context(self):
        reference = self.ingest()
        self.enable()
        self.config(max_payload_bytes=1)
        with self.transport(RuntimeError("fixture execution outage")):
            result = self.run_pass()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["recovery"]["blocked_reason"], "payload_size_limit")
        self.assertEqual(self.worker_files(), {})
        self.assertTrue((self.root / reference).exists())

    def test_retry_retains_prior_acknowledgements_in_successful_context(self):
        first = self.ingest()
        self.enable()
        with self.transport(RuntimeError("fixture outage")):
            self.assertEqual(self.run_pass()["status"], "failed")
        second = self.ingest("two.txt", "Project docs two.")
        batch = json.dumps({"drafts": [{"relative_path": "inbox/" + name, "draft": "DRAFT " + name}
                                       for name in ("one.txt", "two.txt")]})
        with self.transport(batch):
            result = self.run_pass()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["worker_outputs"][0]["inputs"]["source_deltas"], [second])
        package = self.saved("cache/workers/context_builder/context_package.json")
        self.assertEqual(set(package["source_deltas"]), {first, second})
        for ref in (first, second):
            self.assertFalse((self.root / ref).exists())
        self.assertEqual(self.saved("cache/workers/context_builder/state.json")["consumed_inputs"], [])

    def test_a29_archive_collision_preserves_both_original_json_bytes(self):
        reference = self.ingest()
        original = (self.root / reference).read_bytes()
        archive = self.root / "cache/deltas/archive"
        archive.mkdir(exist_ok=True)
        destination = archive / Path(reference).name
        destination.write_bytes(b'{"historical":"sentinel"}\n')
        self.assertEqual(self.run_pass()["status"], "complete")
        self.assertEqual(destination.read_bytes(), b'{"historical":"sentinel"}\n')
        self.assertEqual(destination.with_name(destination.stem + "-2.json").read_bytes(), original)

    def test_archive_failure_retains_acknowledgement_for_worker_free_retry(self):
        reference = self.ingest()
        original_replace = Path.replace

        def replace(path, target):
            if path == self.root / reference:
                raise OSError("fixture archive unavailable")
            return original_replace(path, target)

        with patch.object(Path, "replace", new=replace):
            result = self.run_pass()
        self.assertEqual(result["status"], "failed")
        self.assertIn("delta_archive_error", result["recovery"]["last_error"])
        self.assertIn(reference, self.saved("cache/workers/context_builder/state.json")["consumed_inputs"])
        result = self.run_pass()
        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["plan"], [])
        self.assertEqual(result["recovery"], supervisor._clear_recovery())
        self.assertFalse((self.root / reference).exists())
        self.assertEqual(self.saved("cache/workers/context_builder/state.json")["consumed_inputs"], [])

    def test_stale_source_failure_precedes_disabled_execution_routing(self):
        self.ingest()
        self.run_pass()
        (self.root / "inbox/one.txt").write_text("changed without ingestion")
        self.config(workers=["context_builder"], dependencies=[{"from": "phase1_delta", "to": "context_builder"}])
        with self.assertRaises(SourceReingestRequired):
            self.run_pass()
        self.assertEqual(self.cli("--once")[0], 1)

    def test_disappeared_acknowledged_delta_is_not_pruned_as_archived(self):
        reference = self.ingest()
        original_archive = supervisor._archive_observed_deltas

        def archive(root, references):
            (root / reference).unlink()
            return original_archive(root, references)

        with patch.object(supervisor, "_archive_observed_deltas", side_effect=archive):
            result = self.run_pass()
        self.assertEqual(result["status"], "failed")
        self.assertIn(reference, self.saved("cache/workers/context_builder/state.json")["consumed_inputs"])
        self.assertFalse((self.root / "cache/deltas/archive" / Path(reference).name).exists())

    def test_a29_world_state_retains_only_latest_fifty_runs(self):
        for number in range(51):
            self.run_pass(now=f"2026-10-03T12:00:{number:02d}Z")
        runs = self.saved(".world_state.json")["runs"]
        self.assertEqual(len(runs), 50)
        self.assertEqual(runs[0]["generated_at"], "2026-10-03T12:00:01Z")
        self.assertEqual(runs[-1]["generated_at"], "2026-10-03T12:00:50Z")


if __name__ == "__main__":
    unittest.main()
