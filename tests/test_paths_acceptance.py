"""T02 / A05: preexisting path traps, with no live provider or operator key access."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from looping_box import _util, doctor, model, phase1, report, review, scaffold, supervisor, worker
from looping_box.paths import UnsafePathError, contained_path, file_path, read_validated

STAMP = "2026-10-13T12:00:00Z"
RID = "review-" + "a" * 16


def inventory(root):
    """Snapshot ordinary test sentinels, without following links."""
    result = {}
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(current) / name
            key = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[key] = ("link", os.readlink(path))
            elif path.is_file():
                result[key] = ("file", path.read_bytes())
            else:
                result[key] = ("directory",)
    return result


class ContainedAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "sentinel").write_text("must never be read or changed")
        (self.outside / "target.json").write_text('{"outside": true}')
        (self.outside / "target.md").write_text("secret external docs")
        self.sequence = 0
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.base / "keys"),
                                      "LOOPING_BOX_REVIEW_KEY": "t02-test-key"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        network = patch("looping_box.model._transport", side_effect=AssertionError("Live provider forbidden"))
        self.transport = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(model._env_loaded_for.clear)

    def workspace(self):
        self.sequence += 1
        root = self.base / f"workspace-{self.sequence}"
        scaffold.init_workspace(root)
        (root / "inbox/note.md").write_text("docs summary")
        return root

    def link(self, root, relative, variant="outside"):
        path = root / relative
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        if variant == "inside":
            target = root / "sentinel-target"
            target.mkdir(exist_ok=True)
            (target / "sentinel").write_text("internal target must be preserved")
        elif variant == "dangling":
            target = self.outside / "not-created"
        else:
            target = self.outside
        path.symlink_to(target, target_is_directory=True)
        return path

    @contextmanager
    def protect_outside(self):
        """Also detect attempted reads: unchanged sentinel bytes alone aren't enough."""
        before = inventory(self.outside)
        original = Path.open
        outside = self.outside
        def guarded(path, *args, **kwargs):
            resolved = path.resolve()
            if resolved == outside or outside in resolved.parents:
                raise AssertionError(f"attempted external read/write: {path}")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", guarded):
            yield
        self.assertEqual(inventory(self.outside), before)
        self.transport.assert_not_called()

    def expect_unsafe(self, operation, *args, **kwargs):
        with self.protect_outside(), self.assertRaisesRegex(UnsafePathError, "unsafe_path"):
            operation(*args, **kwargs)

    def test_relative_reference_syntax(self):
        root = self.workspace()
        for reference in ("../outside", "cache/../state", "/tmp/anything", str(root / "inbox/note.md"),
                          "C:/outside", "cache//state", "./inbox", "cache/", "", "a\\b", "a\n"):
            with self.subTest(reference=reference):
                self.expect_unsafe(contained_path, root, reference)
        self.assertEqual(contained_path(root, "cache/new/result.json"), root / "cache/new/result.json")
        self.assertFalse((root / "cache/new").exists())

    def test_ancestor_and_leaf_links_are_rejected(self):
        for variant in ("inside", "outside", "dangling"):
            for reference in ("cache/state", "cache/state/value.json"):
                with self.subTest(variant=variant, reference=reference):
                    root = self.workspace()
                    link = self.link(root, reference, variant)
                    self.expect_unsafe(contained_path, root, reference)
                    self.expect_unsafe(file_path, link / "child.json")
                    self.assertTrue(link.is_symlink())

    def test_source_file_and_directory_links_are_skipped(self):
        root = self.workspace()
        (root / "inbox/external.md").symlink_to(self.outside / "target.md")
        (root / "inbox/dangling.md").symlink_to(self.outside / "missing.md")
        (root / "inbox/local.md").symlink_to(root / "inbox/note.md")
        (root / "inbox/directory").symlink_to(self.outside, target_is_directory=True)
        local_dir = root / "elsewhere"
        local_dir.mkdir()
        (local_dir / "hidden-from-scan.md").write_text("other docs")
        (root / "inbox/local-directory").symlink_to(local_dir, target_is_directory=True)
        with self.protect_outside():
            result = phase1.run_phase1(root, now=STAMP)
        self.assertEqual([item["relative_path"] for item in result["changes"]], ["inbox/note.md"])
        self.assertEqual(result["summary"]["scanned"], 1)

    def test_ingestion_path_families_reject_all_link_kinds(self):
        references = ("inbox", "config", "config/sops", "config/sops/phase1_ingestion.json",
                      "config/action_classes.json", "config/super_loop.json", "cache/state",
                      "cache/state/phase1_state.json", "cache/deltas", "staging/pending_review.json",
                      "staging/reviews", "logs/transactions", "logs/transactions/phase1.jsonl", ".looping_box.lock")
        for reference in references:
            for variant in ("inside", "outside", "dangling"):
                with self.subTest(reference=reference, variant=variant):
                    root = self.workspace()
                    (root / "inbox/note.md").write_text("please deploy")
                    link = self.link(root, reference, variant)
                    self.expect_unsafe(phase1.run_phase1, root, now=STAMP)
                    self.assertTrue(link.is_symlink())

    def test_worker_artifact_and_history_links(self):
        for worker_id, primary in (("context_builder", "context_package"), ("execution_engine", "draft")):
            for name in ("state.json", "last_output.json", primary + ".json", primary + ".md",
                         "history", "history/old.json"):
                for variant in ("inside", "outside", "dangling"):
                    with self.subTest(worker=worker_id, name=name, variant=variant):
                        root = self.workspace()
                        link = self.link(root, f"cache/workers/{worker_id}/{name}", variant)
                        self.expect_unsafe(worker.run_worker, root, worker_id, now=STAMP)
                        self.assertTrue(link.is_symlink())

    def test_delta_links_and_quarantine_destinations(self):
        root = self.workspace()
        link = self.link(root, "cache/deltas/untrusted.json")
        self.expect_unsafe(worker.run_worker, root, "context_builder", now=STAMP)
        self.expect_unsafe(supervisor.run_supervisor, root, now=STAMP)
        self.assertTrue(link.is_symlink())
        for variant in ("inside", "outside", "dangling"):
            root = self.workspace()
            delta = root / "cache/deltas/broken.json"
            delta.write_text("{broken")
            link = self.link(root, "cache/deltas/broken.json.bad", variant)
            self.expect_unsafe(worker._quarantine_delta, delta)
            self.assertEqual(delta.read_text(), "{broken")
            self.assertTrue(link.is_symlink())

    def test_archive_collision_link_is_not_overwritten(self):
        root = self.workspace()
        delta = root / "cache/deltas/batch.json"
        delta.write_text("original")
        target = self.link(root, "cache/deltas/archive/batch.json", "dangling")
        self.expect_unsafe(supervisor._archive_observed_deltas, root, ["cache/deltas/batch.json"])
        self.assertEqual(delta.read_text(), "original")
        self.assertTrue(target.is_symlink())

    def test_json_destination_and_temp_links_are_not_overwritten(self):
        for temp in (False, True):
            for variant in ("inside", "outside", "dangling"):
                root = self.workspace()
                destination = root / "cache/state/value.json"
                destination.write_text("old bytes")
                ref = f"cache/state/value.json.{os.getpid()}.tmp" if temp else "cache/state/value.json"
                link = self.link(root, ref, variant)
                with self.subTest(temp=temp, variant=variant):
                    self.expect_unsafe(_util.write_json, destination, {"new": True})
                    self.assertTrue(link.is_symlink())
                    if temp:
                        self.assertEqual(destination.read_text(), "old bytes")

    def test_pending_reference_links_and_traversal(self):
        for variant in ("inside", "outside", "dangling"):
            root = self.workspace()
            reference = f"staging/reviews/{RID}.json"
            (root / "staging/pending_review.json").write_text(json.dumps({
                "schema": "looping-box.pending-review-index.v1", "generated_at": STAMP,
                "reviews": [reference], "latest": reference}))
            link = self.link(root, reference, variant)
            self.expect_unsafe(review.list_reviews, root)
            self.assertTrue(link.is_symlink())
        for reference in ("../outside/target.json", str(self.outside / "target.json")):
            root = self.workspace()
            (root / "staging/pending_review.json").write_text(json.dumps({
                "schema": "looping-box.pending-review-index.v1", "generated_at": STAMP,
                "reviews": [reference], "latest": reference}))
            self.expect_unsafe(review.list_reviews, root)

    def test_decision_file_and_parent_links_cannot_clear_gate(self):
        for directory in ("approvals", "rejections"):
            for suffix in ("", f"/{RID}.json"):
                for variant in ("inside", "outside", "dangling"):
                    root = self.workspace()
                    link = self.link(root, f"staging/{directory}{suffix}", variant)
                    with self.subTest(directory=directory, suffix=suffix, variant=variant):
                        self.expect_unsafe(_util.decision_state, root / "staging", RID)
                        self.assertTrue(link.is_symlink())

    def test_verifier_and_decision_destinations_are_checked_before_writing(self):
        for directory in ("cache/verifiers", "staging/rejections"):
            root = self.workspace()
            (root / "inbox/note.md").write_text("please deploy")
            phase1.run_phase1(root, now=STAMP)
            review_id = review.list_reviews(root)[0]["review_id"]
            payload_before = inventory(root / "staging/reviews")
            self.link(root, f"{directory}/{review_id}.json", "dangling")
            self.expect_unsafe(review.record_review, root, review_id, "rejected", note="Checked", now=STAMP)
            self.assertEqual(inventory(root / "staging/reviews"), payload_before)
            if directory == "cache/verifiers":
                self.expect_unsafe(review.run_verifier, root, review_id, now=STAMP)

    def test_env_links_rejected_even_after_load_cache_hit(self):
        for variant in ("inside", "outside", "dangling"):
            root = self.workspace()
            model.load_env(root)
            link = self.link(root, ".env", variant)
            self.expect_unsafe(model.load_env, root)
            self.assertTrue(link.is_symlink())

    def test_read_only_commands_preserve_unsafe_evidence(self):
        for reference, operation in (
            (".world_state.json", supervisor.status_summary),
            ("logs/transactions", review.verify_audit_logs),
            ("logs/transactions/phase1.jsonl", report.build_report),
            ("logs/transactions/other.jsonl", review.verify_audit_logs),
            (".env", doctor.run_doctor),
        ):
            for variant in ("inside", "outside", "dangling"):
                root = self.workspace()
                self.link(root, reference, variant)
                before = inventory(root)
                with self.subTest(reference=reference, variant=variant):
                    self.expect_unsafe(operation, root)
                    self.assertEqual(inventory(root), before)

    def test_scaffold_preflights_every_destination(self):
        for reference in ("config", "config/sops", "config/action_classes.json", ".env.example", ".gitignore",
                          "cache/state", "staging/reviews", "logs/transactions"):
            for variant in ("inside", "outside", "dangling"):
                self.sequence += 1
                root = self.base / f"fresh-{self.sequence}"
                root.mkdir()
                self.link(root, reference, variant)
                before = inventory(root)
                with self.subTest(reference=reference, variant=variant):
                    self.expect_unsafe(scaffold.init_workspace, root)
                    self.assertEqual(inventory(root), before)

    def test_scaffold_conflicts_and_root_link_fail_without_writes(self):
        root = self.base / "fresh-conflict"
        root.mkdir()
        (root / "config").write_text("operator file")
        before = inventory(root)
        self.expect_unsafe(scaffold.init_workspace, root)
        self.assertEqual(inventory(root), before)
        alias = self.base / "workspace-alias"
        alias.symlink_to(self.outside, target_is_directory=True)
        sentinel = (self.outside / "sentinel").read_bytes()
        scaffold.init_workspace(alias)
        self.assertTrue((self.outside / "config/super_loop.json").is_file())
        self.assertEqual((self.outside / "sentinel").read_bytes(), sentinel)

    def test_scaffold_rejects_linked_parents_of_nonexistent_workspace(self):
        for variant in ("inside", "outside", "dangling"):
            parent = self.workspace()
            link = self.link(parent, "linked-parent", variant)
            before = inventory(parent)
            with self.subTest(variant=variant):
                target = (link / "new-workspace").resolve()
                scaffold.init_workspace(link / "new-workspace")
                self.assertTrue(link.is_symlink())
                self.assertTrue((target / "config/super_loop.json").is_file())
                for name, value in before.items():
                    self.assertEqual(inventory(parent)[name], value)

    @unittest.skipUnless(sys.platform == "darwin", "macOS host aliases only")
    def test_scaffold_accepts_standard_macos_temp_alias(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary) / "workspace"
            scaffold.init_workspace(Path("/") / root.relative_to("/private"))
            self.assertTrue((root / "config/super_loop.json").is_file())

    def test_rollback_checks_entire_tree_before_deletion(self):
        for snapshot in (None, ({"draft.json": b"old"}, set())):
            root = self.workspace()
            directory = root / "cache/workers/execution_engine"
            (directory / "keep.md").write_text("keep")
            self.link(root, "cache/workers/execution_engine/history/trap", "outside")
            before = inventory(root)
            self.expect_unsafe(supervisor._restore_worker_dir, root, "execution_engine", snapshot)
            self.assertEqual(inventory(root), before)
        root = self.workspace()
        before = inventory(root)
        self.expect_unsafe(supervisor._restore_worker_dir, root, "execution_engine", ({"../../escape": b"bad"}, set()))
        self.assertEqual(inventory(root), before)

    def test_external_key_requires_private_regular_nonsymlink_file(self):
        os.environ.pop("LOOPING_BOX_REVIEW_KEY")
        key = self.base / "keys/looping-box/review.key"
        key.parent.mkdir(parents=True)
        for variant in ("inside", "outside", "dangling"):
            target = self.outside / ("missing-key" if variant == "dangling" else "sentinel")
            if variant == "inside":
                target = self.base / "local-key"
                target.write_text("internal secret")
            key.symlink_to(target)
            self.expect_unsafe(_util.review_key, create=True)
            self.expect_unsafe(_util.key_status)
            self.assertTrue(key.is_symlink())
            key.unlink()
        key.mkdir()
        self.expect_unsafe(_util.review_key)
        key.rmdir()
        key.write_text("private test key")
        key.chmod(0o644)
        self.assertIsNone(_util.review_key())
        key.chmod(0o600)
        self.assertEqual(_util.review_key(), b"private test key")
        key.unlink()
        self.assertTrue(_util.review_key(create=True))
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        shutil.rmtree(key.parent)
        key.parent.symlink_to(self.outside, target_is_directory=True)
        self.expect_unsafe(_util.review_key, create=True)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX special-file support")
    def test_special_files_fail_without_opening_them(self):
        root = self.workspace()
        fifo = root / "cache/state/fifo.json"
        os.mkfifo(fifo)
        self.expect_unsafe(_util.read_json, fifo)
        self.expect_unsafe(_util.write_json, fifo, {})
        self.expect_unsafe(read_validated, fifo, "phase1_state")

    def test_normal_offline_pipeline_still_uses_contained_files(self):
        root = self.workspace()
        with self.protect_outside():
            phase1.run_phase1(root, now=STAMP)
            result = supervisor.run_supervisor(root, now=STAMP)
        self.assertEqual(result["status"], "complete")
        self.assertTrue((root / "cache/workers/execution_engine/draft.json").is_file())
        self.assertIn("Activity", report.build_report(root))


if __name__ == "__main__":
    unittest.main()
