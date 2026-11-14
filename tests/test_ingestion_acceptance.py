"""T04 / A03, A06–A08, A10: snapshots, reconciliation and sequential ingestion."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from looping_box import model, phase1, review, scaffold, supervisor, worker
from looping_box.action_policy import match_keywords
from looping_box.validation import validate_data

STAMP = "2026-10-13T12:00:00Z"


class IngestionAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "workspace"
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.base / "keys"),
                                      "LOOPING_BOX_REVIEW_KEY": "fake-test-key"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        network = patch("looping_box.model._transport", side_effect=AssertionError("network forbidden"))
        self.transport = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(model._env_loaded_for.clear)
        scaffold.init_workspace(self.root)
        self.sop = self.root / "config/sops/phase1_ingestion.json"
        self.state = self.root / "cache/state/phase1_state.json"

    def configure(self, **changes):
        sop = json.loads(self.sop.read_text())
        sop.update(changes)
        self.sop.write_text(json.dumps(sop))

    def source(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        return path

    def ingest(self, **kwargs):
        result = phase1.run_phase1(self.root, now=STAMP, **kwargs)
        if result["delta_path"] is not None:
            validate_data(json.loads((self.root / result["delta_path"]).read_text()), "phase1_delta", "generated delta")
        validate_data(json.loads(self.state.read_text()), "phase1_state", "generated state")
        self.transport.assert_not_called()
        return result

    def test_one_bounded_snapshot_pairs_hash_text_and_size_after_replacement(self):
        path = self.source("inbox/note.md", b"docs original snapshot")
        original_open = Path.open
        reads = []
        class ReplacingReader:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.handle.close()
            def fileno(self):
                return self.handle.fileno()
            def read(self, size):
                reads.append(size)
                data = self.handle.read(size)
                replacement = path.with_suffix(".replacement")
                with original_open(replacement, "wb") as out:
                    out.write(b"please deploy changed bytes")
                replacement.replace(path)
                return data
        def opening(candidate, *args, **kwargs):
            handle = original_open(candidate, *args, **kwargs)
            return ReplacingReader(handle) if candidate == path and args == ("rb",) else handle
        with patch.object(Path, "open", opening):
            result = self.ingest()
        change = result["changes"][0]
        self.assertEqual(reads, [1_000_001])
        self.assertEqual(change["sha256"], hashlib.sha256(b"docs original snapshot").hexdigest())
        self.assertEqual(change["size_bytes"], len(b"docs original snapshot"))
        self.assertEqual(change["excerpt"], "docs original snapshot")
        self.assertEqual(change["review_reasons"], [])
        self.assertIn("deploy", self.ingest()["changes"][0]["review_reasons"])

    def test_growth_beyond_cap_switches_to_undecoded_stream(self):
        self.configure(max_file_bytes=10)
        path = self.source("inbox/growth.txt", b"docs")
        original_open = Path.open
        read_sizes = []
        class GrowingReader:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.handle.close()
            def fileno(self):
                return self.handle.fileno()
            def seek(self, *args):
                return self.handle.seek(*args)
            def read(self, size):
                first_read = not read_sizes
                if first_read:
                    with original_open(path, "ab") as out:
                        out.write(b"\xff" * 20)
                read_sizes.append(size)
                data = self.handle.read(size)
                if first_read:
                    # Change already-read bytes before the guarded fallback.
                    # A buffered seek could otherwise hash the old prefix.
                    with original_open(path, "r+b") as out:
                        out.write(b"new!")
                return data
        def opening(candidate, *args, **kwargs):
            handle = original_open(candidate, *args, **kwargs)
            return GrowingReader(handle) if candidate == path and args == ("rb",) else handle
        with patch.object(Path, "open", opening), patch.object(phase1, "_decode_bytes", side_effect=AssertionError("oversize decode")):
            result = self.ingest()
        change = result["changes"][0]
        self.assertEqual(read_sizes[0], 11)
        self.assertTrue(all(size <= 65536 for size in read_sizes))
        self.assertEqual(change["review_reasons"], ["oversized_input"])
        self.assertEqual(change["size_bytes"], 24)
        self.assertEqual(change["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual((change["excerpt"], change["matched_routes"]), ("", []))

    def test_cap_boundary_and_stable_oversized_approval(self):
        self.configure(max_file_bytes=10)
        self.source("inbox/exact.txt", b"abcdefghij")
        large = self.source("inbox/large.txt", b"\xff" * 11)
        result = self.ingest()
        exact, oversized = result["changes"]
        self.assertEqual(exact["review_reasons"], [])
        self.assertEqual(exact["excerpt"], "abcdefghij")
        self.assertEqual(oversized["sha256"], hashlib.sha256(large.read_bytes()).hexdigest())
        rid = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, rid, "approved", note="Allow empty local excerpt", now=STAMP)
        released = self.ingest()["changes"][0]
        self.assertEqual(released["approved_review"], rid)
        self.assertEqual((released["excerpt"], released["matched_routes"], released["review_reasons"]), ("", [], []))

    def test_changed_oversized_stream_fails_without_advancing_state(self):
        self.configure(max_file_bytes=10)
        self.ingest()
        before = self.state.read_bytes()
        path = self.source("inbox/large.txt", b"x" * 70000)
        original_open = Path.open
        changed = []
        class ChangingReader:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.handle.close()
            def fileno(self):
                return self.handle.fileno()
            def seek(self, *args):
                return self.handle.seek(*args)
            def read(self, size):
                data = self.handle.read(size)
                if not changed:
                    with original_open(path, "ab") as out:
                        out.write(b"extra")
                    changed.append(True)
                return data
        def opening(candidate, *args, **kwargs):
            handle = original_open(candidate, *args, **kwargs)
            return ChangingReader(handle) if candidate == path and args == ("rb",) else handle
        with patch.object(Path, "open", opening), self.assertRaisesRegex(ValueError, "input_changed"):
            self.ingest()
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(list((self.root / "cache/deltas").glob("*.json")), [])

    def test_discovery_routes_content_sorted_and_keeps_unrouted_input(self):
        self.source("inbox/z.MD", "docs note")
        self.source("inbox/a.txt", "qwerty")
        self.source("inbox/.hidden.md", "deploy")
        self.source("inbox/.hidden/inside.md", "deploy")
        self.source("inbox/unsupported.csv", "deploy")
        self.source("inbox/deploy.md", "ordinary neutral wording")
        result = self.ingest()
        self.assertEqual(result["reconciliation"]["scanned_paths"], ["inbox/a.txt", "inbox/deploy.md", "inbox/z.MD"])
        self.assertEqual(result["changes"][0]["matched_routes"], [])
        self.assertEqual(result["boundary_gate"]["status"], "clear")

    def test_utf16_json_and_full_text_gate_before_excerpt(self):
        cases = [("utf16.txt", "deploy".encode("utf-16"), "deploy"),
                 ("escaped.json", b'{"value":"\\u0064eploy"}', "deploy"),
                 ("invalid.json", b'{docs: deploy', "deploy"),
                 ("nul.txt", b"de\x00ploy", "deploy"),
                 ("long.md", b"docs " * 120 + b"deploy", "deploy")]
        for name, data, _ in cases:
            self.source("inbox/" + name, data)
        result = self.ingest()
        self.assertEqual(len(result["changes"]), len(cases))
        self.assertTrue(all("deploy" in item["review_reasons"] for item in result["changes"]))
        escaped = next(item for item in result["changes"] if item["relative_path"].endswith("escaped.json"))
        self.assertEqual(escaped["matched_routes"], [])
        long = next(item for item in result["changes"] if item["relative_path"].endswith("long.md"))
        self.assertEqual(len(long["excerpt"]), 500)
        self.assertTrue(long["excerpt"].endswith("..."))
        self.assertNotIn("deploy", long["excerpt"])

    def test_decode_replacement_and_excerpt_whitespace(self):
        self.configure(max_excerpt_chars=9)
        self.source("inbox/note.md", b"a\xff  b\n c\t d\x00")
        item = self.ingest()["changes"][0]
        self.assertEqual(item["excerpt"], "a\ufffd b c d")
        self.source("inbox/note.md", b"one  two\nthree")
        self.assertEqual(self.ingest()["changes"][0]["excerpt"], "one tw...")

    def test_required_obfuscations_are_gated_when_ingested(self):
        for text, reason in (("deployed", "deploy"), ("committing", "commit"), ("send_email", "email"),
                             ("api_key", "api key"), ("d e p l o y", "deploy"), ("dеploy", "deploy"),
                             ("x|sh", "| sh")):
            with self.subTest(text=text):
                self.source("inbox/example.md", text)
                item = self.ingest()["changes"][0]
                self.assertIn(reason, item["review_reasons"])
        for text in ("committee", "reproduction", "token count"):
            with self.subTest(benign=text):
                self.source("inbox/example.md", text)
                self.assertEqual(self.ingest()["boundary_gate"]["status"], "clear")

    def test_nonempty_cross_path_and_empty_path_only_dedup(self):
        for name, data in (("a.md", "docs same"), ("b.md", "docs same"), ("c.md", ""), ("d.md", "")):
            self.source("inbox/" + name, data)
        first = self.ingest()
        self.assertEqual((first["summary"]["changed"], first["summary"]["skipped"]), (3, 1))
        self.assertEqual(self.ingest()["summary"]["skipped"], 4)
        self.source("inbox/e.md", "")
        self.assertEqual([item["relative_path"] for item in self.ingest()["changes"]], ["inbox/e.md"])

    def test_removal_only_delta_then_empty_pass_without_audit(self):
        path = self.source("inbox/note.md", "docs note")
        self.ingest()
        path.unlink()
        removed = self.ingest()
        self.assertEqual(removed["reconciliation"], {"scanned_paths": [], "removed_paths": ["inbox/note.md"]})
        self.assertEqual(removed["summary"], {"scanned": 0, "changed": 0, "skipped": 0, "requires_review": False})
        self.assertIsNotNone(removed["delta_path"])
        audit = self.root / "logs/transactions/phase1.jsonl"
        before = audit.read_bytes()
        empty = self.ingest()
        self.assertIsNone(empty["delta_path"])
        self.assertEqual(audit.read_bytes(), before)
        self.assertEqual(json.loads(self.state.read_text())["present_paths"], [])

    def test_removal_write_failure_does_not_advance_presence(self):
        path = self.source("inbox/note.md", "docs note")
        self.ingest()
        path.unlink()
        before = self.state.read_bytes()
        original = Path.replace
        def replace(source, target):
            if Path(target).parent == self.root / "cache/deltas":
                raise OSError("injected delta replace failure")
            return original(source, target)
        with patch.object(Path, "replace", replace), self.assertRaisesRegex(OSError, "delta replace"):
            self.ingest()
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.ingest()["reconciliation"]["removed_paths"], ["inbox/note.md"])

    def test_delta_write_failure_leaves_new_and_revised_reviews_unpublished(self):
        self.source("inbox/held.md", "please deploy this release after inspection")
        index = self.root / "staging/pending_review.json"
        original = phase1._write_json

        def fail_delta(path, data):
            if path.parent == self.root / "cache/deltas":
                raise OSError("fixture delta publication failure")
            return original(path, data)

        with patch.object(phase1, "_write_json", side_effect=fail_delta):
            with self.assertRaisesRegex(OSError, "delta publication failure"):
                self.ingest()
        self.assertFalse(index.exists())
        self.assertEqual(review.list_reviews(self.root), [])

        self.ingest()
        pending = review.list_reviews(self.root)[0]
        payload = self.root / pending["path"]
        old_payload, old_index = payload.read_bytes(), index.read_bytes()
        self.configure(max_excerpt_chars=12)
        with patch.object(phase1, "_write_json", side_effect=fail_delta):
            with self.assertRaisesRegex(OSError, "delta publication failure"):
                self.ingest()
        self.assertEqual(payload.read_bytes(), old_payload)
        self.assertEqual(index.read_bytes(), old_index)

        self.ingest()
        revised = review.show_review(self.root, pending["review_id"])
        self.assertEqual(revised["source_items"][0]["excerpt"], "please de...")

    def test_legacy_empty_hash_does_not_deduplicate_a_different_empty_path(self):
        self.source("inbox/a.md", "")
        self.ingest()
        state = json.loads(self.state.read_text())
        state["processed_hashes"][hashlib.sha256(b"").hexdigest()] = {
            "first_seen_path": "inbox/a.md", "first_seen_at": STAMP}
        self.state.write_text(json.dumps(state))
        self.source("inbox/b.md", "")
        self.assertEqual([item["relative_path"] for item in self.ingest()["changes"]], ["inbox/b.md"])

    def test_custom_scan_replaces_only_its_presence_subset(self):
        a = self.source("inbox/a.md", "docs a")
        b = self.source("other/b.md", "docs b")
        self.ingest()
        self.ingest(input_dir="other")
        a.unlink()
        custom = self.ingest(input_dir="other")
        self.assertEqual(custom["reconciliation"]["removed_paths"], [])
        self.assertEqual(json.loads(self.state.read_text())["present_paths"], ["inbox/a.md", "other/b.md"])
        self.assertEqual(self.ingest()["reconciliation"]["removed_paths"], ["inbox/a.md"])
        self.assertTrue(b.exists())

    def test_custom_scan_preserves_holds_outside_its_scope(self):
        inbox = self.source("inbox/hold.md", "deploy from the inbox")
        other = self.source("other/hold.md", "deploy from other")
        self.ingest()
        inbox_id = review.list_reviews(self.root)[0]["review_id"]
        self.ingest(input_dir="other")
        self.assertEqual(len(review.list_reviews(self.root)), 2)
        other.unlink()
        self.assertEqual(self.ingest(input_dir="other")["boundary_gate"]["status"], "pending_review")
        self.assertEqual([item["review_id"] for item in review.list_reviews(self.root)], [inbox_id])
        inbox.unlink()
        self.ingest()
        self.assertEqual(review.list_reviews(self.root), [])

    def test_missing_index_scoped_rebuild_cannot_hide_an_archived_hold(self):
        self.source("inbox/held.md", "please deploy from inbox")
        self.ingest()
        inbox_review = review.list_reviews(self.root)[0]
        self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "blocked")
        index = self.root / "staging/pending_review.json"
        index.unlink()
        self.source("other/held.md", "please deploy from other")

        before = {path: path.read_bytes() for directory in ("staging", "cache/state", "cache/deltas", "logs")
                  for path in (self.root / directory).rglob("*") if path.is_file()}
        with self.assertRaisesRegex(ValueError, "review_state_unavailable: .*outside the scanned directory"):
            self.ingest(input_dir="other")
        self.assertFalse(index.exists())
        self.assertEqual({path: path.read_bytes() for directory in ("staging", "cache/state", "cache/deltas", "logs")
                          for path in (self.root / directory).rglob("*") if path.is_file()}, before)
        with self.assertRaisesRegex(ValueError, "review_state_unavailable"):
            supervisor.status_summary(self.root)

        self.ingest()
        self.assertEqual(review.list_reviews(self.root), [inbox_review])
        self.ingest(input_dir="other")
        self.assertEqual(len(review.list_reviews(self.root)), 2)
        review.record_review(self.root, inbox_review["review_id"], "rejected", note="Fixture decision", now=STAMP)
        index.unlink()
        self.ingest(input_dir="other")
        self.assertEqual(len(review.list_reviews(self.root)), 1)

    def test_covering_scan_rebuilds_empty_index_for_removed_or_defused_hold(self):
        for disposition in ("removed", "defused"):
            with self.subTest(disposition=disposition):
                root = self.base / disposition
                scaffold.init_workspace(root)
                source = root / "inbox/held.md"
                source.write_text("please deploy")
                phase1.run_phase1(root, now=STAMP)
                retained = root / review.list_reviews(root)[0]["path"]
                original = retained.read_bytes()
                self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "blocked")
                index = root / "staging/pending_review.json"
                index.unlink()
                if disposition == "removed":
                    source.unlink()
                else:
                    source.write_text("docs for the next pass")

                result = phase1.run_phase1(root, now=STAMP)
                self.assertEqual(result["boundary_gate"]["status"], "clear")
                restored = json.loads(index.read_text())
                validate_data(restored, "pending_review_index", str(index))
                self.assertEqual((restored["reviews"], restored["latest"]), ([], ""))
                self.assertEqual(retained.read_bytes(), original)
                self.assertEqual(review.list_reviews(root), [])
                self.assertIn(supervisor.run_supervisor(root, now=STAMP)["status"], {"complete", "idle"})

    def test_failed_scan_and_invalid_filename_preserve_queue_and_state(self):
        self.source("inbox/nested/held.md", "please deploy")
        self.ingest()
        before = {p: p.read_bytes() for directory in ("staging", "cache/state", "cache/deltas", "logs")
                  for p in (self.root / directory).rglob("*") if p.is_file()}
        real_scan = os.scandir

        def inaccessible(path):
            if Path(path) == self.root / "inbox/nested":
                raise PermissionError("fixture subtree inaccessible")
            return real_scan(path)

        with patch.object(os, "scandir", side_effect=inaccessible), self.assertRaisesRegex(OSError, "input_scan_failed"):
            self.ingest()
        for name in ("bad\\path.md", "bad\npath.md"):
            path = self.source("inbox/" + name, "docs note")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "rename this input"):
                self.ingest()
            path.unlink()
        after = {p: p.read_bytes() for directory in ("staging", "cache/state", "cache/deltas", "logs")
                 for p in (self.root / directory).rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_legacy_pending_policy_revision_archives_exact_payload(self):
        self.source("inbox/held.md", "please deploy")
        self.ingest()
        rid = review.list_reviews(self.root)[0]["review_id"]
        path = self.root / "staging/reviews" / (rid + ".json")
        payload = json.loads(path.read_text())
        del payload["source_evidence_sha256"]
        path.write_text(json.dumps(payload))
        before = path.read_bytes()
        policy_path = self.root / "config/action_classes.json"
        policy = json.loads(policy_path.read_text())
        policy["classes"]["review_required"].remove("deploy")
        policy["classes"]["forbidden"].append("deploy")
        policy_path.write_text(json.dumps(policy))

        self.ingest()
        self.assertEqual(review.show_review(self.root, rid)["action_class"], "forbidden")
        saved = list((self.root / "staging/archive").glob("*.payload.json"))
        self.assertEqual([item.read_bytes() for item in saved], [before])

    def test_legacy_review_revision_finds_archived_evidence_in_prior_queue(self):
        self.source("inbox/held.md", "please deploy this release")
        prior = self.ingest(delta_dir="cache/legacy-deltas")
        delta_path = self.root / prior["delta_path"]
        archive = delta_path.parent / "archive"
        archive.mkdir()
        delta_path.rename(archive / delta_path.name)
        state = json.loads(self.state.read_text())
        del state["custom_delta_dirs"]
        self.state.write_text(json.dumps(state))

        rid = review.list_reviews(self.root)[0]["review_id"]
        path = self.root / "staging/reviews" / f"{rid}.json"
        payload = json.loads(path.read_text())
        del payload["source_evidence_sha256"]
        path.write_text(json.dumps(payload))
        before = path.read_bytes()

        self.configure(max_excerpt_chars=12)
        self.ingest(delta_dir="cache/selected-deltas")
        self.assertEqual(review.show_review(self.root, rid)["source_items"][0]["excerpt"], "please de...")
        saved = list((self.root / "staging/archive").glob("*.payload.json"))
        self.assertEqual([item.read_bytes() for item in saved], [before])

    def test_legacy_review_revision_uses_default_archive_with_shared_staging(self):
        self.source("inbox/held.md", "please deploy this release")
        prior = self.ingest()
        delta_path = self.root / prior["delta_path"]
        archive = delta_path.parent / "archive"
        archive.mkdir()
        delta_path.rename(archive / delta_path.name)
        rid = review.list_reviews(self.root)[0]["review_id"]
        path = self.root / "staging/reviews" / f"{rid}.json"
        payload = json.loads(path.read_text())
        del payload["source_evidence_sha256"]
        path.write_text(json.dumps(payload))
        before = path.read_bytes()
        sop = json.loads(self.sop.read_text())
        sop["max_excerpt_chars"] = 12
        self.source("config/sops/revised.json", json.dumps(sop))
        self.ingest(sop_path="config/sops/revised.json", state_path="alternate/state.json",
                    delta_dir="alternate/deltas")
        self.assertEqual(review.show_review(self.root, rid)["source_items"][0]["excerpt"], "please de...")
        saved = list((self.root / "staging/archive").glob("*.payload.json"))
        self.assertEqual([item.read_bytes() for item in saved], [before])

    def test_missing_payload_is_reconstructed_only_for_matching_scanned_source(self):
        self.source("inbox/held.md", "please deploy")
        self.ingest()
        rid = review.list_reviews(self.root)[0]["review_id"]
        path = self.root / "staging/reviews" / (rid + ".json")
        path.unlink()
        with self.assertRaisesRegex(ValueError, "review_evidence_invalid"):
            self.ingest(input_dir="other")
        self.ingest()
        self.assertTrue(path.is_file())
        self.assertEqual(review.show_review(self.root, rid)["review_id"], rid)

    def test_queued_approval_survives_approved_skip_but_rejected_hold_clears(self):
        self.source("inbox/approved.md", "deploy once after approval")
        self.source("inbox/rejected.md", "deploy a declined change")
        self.ingest()
        for item in review.list_reviews(self.root):
            rid = item["review_id"]
            path = review.show_review(self.root, rid)["source_items"][0]["relative_path"]
            decision = "approved" if path == "inbox/approved.md" else "rejected"
            review.record_review(self.root, rid, decision, note="Fixture decision", now=STAMP)
        self.ingest()
        self.ingest()
        self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "complete")
        draft = json.loads((self.root / "cache/workers/execution_engine/draft.json").read_text())
        self.assertEqual([item["relative_path"] for item in draft["items"]], ["inbox/approved.md"])
        self.assertEqual(draft["held_for_review"], [])

    def test_policy_refresh_requires_new_approval_and_releases_undrafted_work(self):
        for run_before in (None, "held", "drafted", "interrupted"):
            with self.subTest(run_before=run_before):
                root = self.base / f"policy-refresh-{run_before}"
                scaffold.init_workspace(root)
                (root / "inbox/held.md").write_text("please deploy this old release")
                phase1.run_phase1(root, now=STAMP)
                rid = review.list_reviews(root)[0]["review_id"]
                review.record_review(root, rid, "approved", note="Original approval", now=STAMP)
                original = root / "staging/approvals" / (rid + ".json")
                decision_bytes = original.read_bytes()
                self.assertEqual(len(phase1.run_phase1(root, now=STAMP)["changes"]), 1)
                if run_before == "drafted":
                    self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "complete")
                sop_path = root / "config/sops/phase1_ingestion.json"
                sop = json.loads(sop_path.read_text())
                sop["max_excerpt_chars"] = 12
                sop_path.write_text(json.dumps(sop))

                if run_before == "interrupted":
                    write_json = phase1._write_json

                    def interrupt(path, data, root=root, write_json=write_json):
                        if path == root / "cache/state/phase1_state.json":
                            raise OSError("fixture state write failure")
                        return write_json(path, data)

                    with patch.object(phase1, "_write_json", side_effect=interrupt), self.assertRaisesRegex(OSError, "state write failure"):
                        phase1.run_phase1(root, now=STAMP)
                    self.assertTrue(original.exists())
                    self.assertEqual(len(review.list_reviews(root)), 1)

                held = phase1.run_phase1(root, now=STAMP)
                self.assertEqual(held["boundary_gate"]["status"], "pending_review")
                self.assertEqual(phase1.run_phase1(root, now=STAMP)["warnings"], [])
                self.assertEqual(review.show_review(root, rid)["source_items"][0]["excerpt"], "please de...")
                self.assertFalse(original.exists())
                backups = list((root / "staging/archive").glob("*.approval.json"))
                self.assertEqual([path.read_bytes() for path in backups], [decision_bytes])
                if run_before == "held":
                    self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "blocked")
                review.record_review(root, rid, "approved", note="Updated approval", now=STAMP)
                release = phase1.run_phase1(root, now=STAMP)
                self.assertEqual(len(release["changes"]), int(run_before != "drafted"))
                self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "complete")
                draft = json.loads((root / "cache/workers/execution_engine/draft.json").read_text())
                self.assertEqual([item["draft"] for item in draft["items"]],
                                 [] if run_before == "drafted" else ["please de..."])

    def test_changed_review_id_resumes_only_undrafted_release(self):
        for earlier_run in ("queued", "held_archived", "drafted", "rejected", "removed"):
            with self.subTest(earlier_run=earlier_run):
                root = self.base / f"changed-id-{earlier_run}"
                scaffold.init_workspace(root)
                source = root / "inbox/held.md"
                text = "please deploy and upload this release"
                source.write_text(text)
                sop_path = root / "config/sops/phase1_ingestion.json"
                sop = json.loads(sop_path.read_text())
                keywords = sop["boundary_gate"]["requires_review_keywords"]
                keywords.remove("upload")
                sop_path.write_text(json.dumps(sop))
                phase1.run_phase1(root, now=STAMP)
                old_id = review.list_reviews(root)[0]["review_id"]
                review.record_review(root, old_id, "approved", note="Original approval", now=STAMP)
                original = phase1.run_phase1(root, now=STAMP)
                self.assertEqual(original["changes"][0]["approved_review"], old_id)

                if earlier_run == "drafted":
                    self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "complete")
                if earlier_run == "removed":
                    source.unlink()
                    phase1.run_phase1(root, now=STAMP)
                    source.write_text(text)
                keywords.append("upload")
                sop_path.write_text(json.dumps(sop))
                phase1.run_phase1(root, now=STAMP)
                current_id = review.list_reviews(root)[0]["review_id"]
                self.assertNotEqual(current_id, old_id)
                if earlier_run == "held_archived":
                    self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "blocked")
                    self.assertFalse((root / original["delta_path"]).exists())
                if earlier_run == "rejected":
                    review.record_review(root, current_id, "rejected", note="Discard release", now=STAMP)
                    # Revise the matching reasons again before Phase 1 can emit a
                    # rejection skip. The signed rejection still cancels the work.
                    keywords.remove("deploy")
                    sop_path.write_text(json.dumps(sop))
                    phase1.run_phase1(root, now=STAMP)
                    current_id = review.list_reviews(root)[0]["review_id"]
                review.record_review(root, current_id, "approved", note="Current approval", now=STAMP)
                released = phase1.run_phase1(root, now=STAMP)
                expected = earlier_run in {"queued", "held_archived"}
                self.assertEqual([item["approved_review"] for item in released["changes"]],
                                 [current_id] if expected else [])
                self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "complete")
                draft = json.loads((root / "cache/workers/execution_engine/draft.json").read_text())
                self.assertEqual([item["draft"] for item in draft["items"]], [text] if expected else [])

    def test_changed_review_id_resumes_after_partial_draft_publication(self):
        for name, writer in (("draft.md", "_write_text"), ("state.json", "_write_json")):
            with self.subTest(interrupted_file=name):
                root = self.base / f"partial-{name}"
                scaffold.init_workspace(root)
                text = "please deploy and upload this release"
                (root / "inbox/held.md").write_text(text)
                sop_path = root / "config/sops/phase1_ingestion.json"
                sop = json.loads(sop_path.read_text())
                keywords = sop["boundary_gate"]["requires_review_keywords"]
                keywords.remove("upload")
                sop_path.write_text(json.dumps(sop))
                phase1.run_phase1(root, now=STAMP)
                original_id = review.list_reviews(root)[0]["review_id"]
                review.record_review(root, original_id, "approved", note="Original approval", now=STAMP)
                phase1.run_phase1(root, now=STAMP)
                worker.run_worker(root, "context_builder", now=STAMP)
                execution_dir = root / "cache/workers/execution_engine"
                original_write = getattr(worker, writer)

                def interrupt(path, content, target=execution_dir / name, write=original_write):
                    if path == target:
                        raise OSError("fixture interrupted draft publication")
                    return write(path, content)

                with patch.object(worker, writer, side_effect=interrupt), self.assertRaisesRegex(OSError, "interrupted draft"):
                    worker.run_worker(root, "execution_engine", now=STAMP)
                partial = (execution_dir / "draft.json").read_bytes()
                self.assertFalse((execution_dir / "state.json").exists())
                self.assertEqual((execution_dir / "draft.md").exists(), name == "state.json")

                keywords.append("upload")
                sop_path.write_text(json.dumps(sop))
                phase1.run_phase1(root, now=STAMP)
                revised_id = review.list_reviews(root)[0]["review_id"]
                self.assertNotEqual(revised_id, original_id)
                review.record_review(root, revised_id, "approved", note="Revised approval", now=STAMP)
                released = phase1.run_phase1(root, now=STAMP)
                self.assertEqual([item["approved_review"] for item in released["changes"]], [revised_id])
                self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "complete")
                draft_path = execution_dir / "draft.json"
                self.assertEqual([item["draft"] for item in json.loads(draft_path.read_text())["items"]], [text])
                history = execution_dir / "history"
                self.assertEqual([p.read_bytes() for p in history.glob("*-draft.incomplete.json")], [partial])
                state = json.loads((execution_dir / "state.json").read_text())
                self.assertEqual(state["last_draft_sha256"], hashlib.sha256(draft_path.read_bytes()).hexdigest())

                # A later deletion of the derivative does not undo that commit.
                (execution_dir / "draft.md").unlink()
                keywords.remove("upload")
                sop_path.write_text(json.dumps(sop))
                reverted = phase1.run_phase1(root, now=STAMP)
                self.assertEqual(reverted["changes"], [])
                self.assertEqual(reverted["skipped"][0]["reason"], "review_decision_recorded")

    def test_custom_sop_reapproval_carries_undrafted_release_across_queues(self):
        source_text = "please deploy and upload this release"
        self.source("inbox/held.md", source_text)
        original_sop = json.loads(self.sop.read_text())
        original_sop["boundary_gate"]["requires_review_keywords"].remove("upload")
        original_sop_path = "config/sops/original.json"
        revised_sop_path = "config/sops/revised.json"
        self.source(revised_sop_path, self.sop.read_bytes())
        self.sop.write_text(json.dumps(original_sop))
        self.source(original_sop_path, self.sop.read_bytes())

        self.ingest(sop_path=original_sop_path)
        original_id = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, original_id, "approved", note="Original approval", now=STAMP)
        self.ingest(sop_path=original_sop_path)
        self.assertEqual(worker.run_worker(self.root, "context_builder", now=STAMP)["status"], "complete")

        alternate = {"sop_path": revised_sop_path, "delta_dir": "cache/alternate-deltas"}
        self.ingest(**alternate)
        revised_id = review.list_reviews(self.root)[0]["review_id"]
        self.assertNotEqual(original_id, revised_id)
        review.record_review(self.root, revised_id, "approved", note="Revised approval", now=STAMP)
        revised = self.ingest(**alternate)
        self.assertEqual([item["approved_review"] for item in revised["changes"]], [revised_id])
        self.assertEqual(self.ingest(**alternate)["changes"], [])

        returned = self.ingest(sop_path=revised_sop_path)
        self.assertEqual([item["approved_review"] for item in returned["changes"]], [revised_id])
        self.assertEqual(self.ingest(sop_path=revised_sop_path)["changes"], [])
        self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "complete")
        draft = json.loads((self.root / "cache/workers/execution_engine/draft.json").read_text())
        self.assertEqual([(item["approved_review"], item["draft"]) for item in draft["items"]],
                         [(revised_id, source_text)])
        self.assertEqual(self.ingest(**alternate)["changes"], [])

    def test_deferred_release_requires_approval_after_alternate_rejection(self):
        for approval_after_rejection in (False, True):
            with self.subTest(approval_after_rejection=approval_after_rejection):
                root = self.base / f"deferred-release-{approval_after_rejection}"
                scaffold.init_workspace(root)
                text = "Please deploy, upload, and send this release."
                (root / "inbox/held.md").write_text(text)
                default = root / "config/sops/phase1_ingestion.json"
                sop = json.loads(default.read_text())
                sop["boundary_gate"]["requires_review_keywords"] = ["deploy"]
                default.write_text(json.dumps(sop))
                phase1.run_phase1(root, now=STAMP)
                original = review.list_reviews(root)[0]["review_id"]
                review.record_review(root, original, "approved", note="First approval", now=STAMP)
                phase1.run_phase1(root, now=STAMP)
                self.assertEqual(worker.run_worker(root, "context_builder", now=STAMP)["status"], "complete")

                keywords = sop["boundary_gate"]["requires_review_keywords"]
                keywords.append("upload")
                default.write_text(json.dumps(sop))
                phase1.run_phase1(root, now=STAMP)
                deferred = review.list_reviews(root)[0]["review_id"]
                state_path = root / "cache/state/phase1_state.json"
                self.assertEqual(json.loads(state_path.read_text())["processed_files"]["inbox/held.md"]
                                 ["pending_review_release"], deferred)
                if not approval_after_rejection:
                    review.record_review(root, deferred, "approved", note="Deferred approval", now=STAMP)

                keywords.append("send")
                (default.parent / "restrictive.json").write_text(json.dumps(sop))
                hold = phase1.run_phase1(root, now=STAMP, sop_path="config/sops/restrictive.json",
                                         state_path="alternate/state.json", delta_dir="alternate/deltas")
                veto, = [item["review_id"] for item in review.list_reviews(root) if item["review_id"] != deferred]
                review.record_review(root, veto, "rejected", note="Alternate rejection", now=STAMP)
                path = root / hold["delta_path"]
                archive = path.parent / "archive"
                archive.mkdir()
                path.rename(archive / path.name)

                if not approval_after_rejection:
                    retry = phase1.run_phase1(root, now=STAMP)
                    self.assertFalse(any(change.get("approved_review") for change in retry["changes"]))
                    self.assertEqual([item["review_id"] for item in review.list_reviews(root)], [deferred])
                    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fixture-key",
                                                 "MODEL_EXECUTION_ENGINE": "stub/execution"}):
                        self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "blocked")
                    self.transport.assert_not_called()
                else:
                    # The alternate scan replaces the shared pending index. Revisit the
                    # original SOP to make its pending review selectable again.
                    phase1.run_phase1(root, now=STAMP)
                review.record_review(root, deferred, "approved", note="Approval after rejection", now=STAMP)
                release = phase1.run_phase1(root, now=STAMP)
                self.assertEqual([change.get("approved_review") for change in release["changes"]], [deferred])
                self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "complete")
                draft = json.loads((root / "cache/workers/execution_engine/draft.json").read_text())
                self.assertEqual([(item["approved_review"], item["draft"]) for item in draft["items"]],
                                 [(deferred, text)])

    def test_unchanged_alternate_scan_preserves_canonical_approved_context(self):
        self.source("inbox/held.md", "please deploy this release")
        self.ingest()
        rid = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, rid, "approved", note="Original approval", now=STAMP)
        self.ingest()
        self.assertEqual(worker.run_worker(self.root, "context_builder", now=STAMP)["status"], "complete")

        self.assertEqual(self.ingest(delta_dir="cache/alternate-deltas")["changes"], [])
        self.assertEqual(self.ingest()["changes"], [])
        self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "complete")
        draft = json.loads((self.root / "cache/workers/execution_engine/draft.json").read_text())
        self.assertEqual([item["approved_review"] for item in draft["items"]], [rid])

    def test_canonical_queue_resumes_latest_same_id_release_from_alternate(self):
        self.source("inbox/held.md", "please deploy this release")
        self.ingest()
        rid = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, rid, "approved", note="Original approval", now=STAMP)
        self.ingest()
        self.assertEqual(worker.run_worker(self.root, "context_builder", now=STAMP)["status"], "complete")
        alternate = self.ingest(delta_dir="cache/alternate-deltas", state_path="cache/state/alternate-phase1.json")
        self.assertEqual([item["approved_review"] for item in alternate["changes"]], [rid])

        self.assertEqual([item["approved_review"] for item in self.ingest()["changes"]], [rid])
        self.assertEqual(self.ingest()["changes"], [])
        self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "complete")
        draft = json.loads((self.root / "cache/workers/execution_engine/draft.json").read_text())
        self.assertEqual([item["approved_review"] for item in draft["items"]], [rid])

    def test_alternate_state_uses_shared_order_to_hand_off_a_revised_approval(self):
        text = "please deploy and upload this release"
        self.source("inbox/held.md", text)
        original_sop = json.loads(self.sop.read_text())
        original_sop["boundary_gate"]["requires_review_keywords"].remove("upload")
        revised = "config/sops/revised.json"
        self.source(revised, self.sop.read_bytes())
        self.sop.write_text(json.dumps(original_sop))
        self.ingest()
        rid = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, rid, "approved", note="Original approval", now=STAMP)
        self.ingest()
        self.assertEqual(worker.run_worker(self.root, "context_builder", now=STAMP)["status"], "complete")

        settings = {"sop_path": revised, "state_path": "alternate/state.json"}
        self.ingest(delta_dir="alternate/deltas", **settings)
        current = review.list_reviews(self.root)[0]["review_id"]
        self.assertNotEqual(rid, current)
        review.record_review(self.root, current, "approved", note="Revised approval", now=STAMP)
        alternate_release = self.ingest(delta_dir="alternate/deltas", **settings)
        self.assertEqual([item["approved_review"] for item in alternate_release["changes"]], [current])
        self.assertEqual([item["approved_review"] for item in self.ingest(**settings)["changes"]], [current])
        self.assertEqual(self.ingest(**settings)["changes"], [])
        self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "complete")
        draft = json.loads((self.root / "cache/workers/execution_engine/draft.json").read_text())
        self.assertEqual([(item["approved_review"], item["draft"]) for item in draft["items"]], [(current, text)])

    def test_review_release_waits_for_missing_newer_queue_history(self):
        reference = "inbox/held.md"
        self.source(reference, "please deploy and upload this release")
        sop = json.loads(self.sop.read_text())
        keywords = sop["boundary_gate"]["requires_review_keywords"]
        keywords.remove("upload")
        self.sop.write_text(json.dumps(sop))
        self.ingest()
        original_id = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, original_id, "approved", note="Original approval", now=STAMP)
        self.ingest()
        newer = self.ingest(delta_dir="cache/alternate-deltas")
        newer_path = self.root / newer["delta_path"]
        newer_bytes = newer_path.read_bytes()
        newer_path.unlink()

        keywords.append("upload")
        self.sop.write_text(json.dumps(sop))
        self.ingest()
        current_id = review.list_reviews(self.root)[0]["review_id"]
        processed = json.loads(self.state.read_text())["processed_files"][reference]
        self.assertNotIn("pending_review_release", processed)

        newer_path.write_bytes(newer_bytes)
        self.ingest()
        processed = json.loads(self.state.read_text())["processed_files"][reference]
        self.assertEqual(processed["pending_review_release"], current_id)
        review.record_review(self.root, current_id, "approved", note="Updated approval", now=STAMP)
        self.assertEqual([item["approved_review"] for item in self.ingest()["changes"]], [current_id])

    def test_aged_missing_cancellation_does_not_revive_an_old_release(self):
        reference = "inbox/held.md"
        self.source(reference, "please deploy and upload this release")
        sop = json.loads(self.sop.read_text())
        keywords = sop["boundary_gate"]["requires_review_keywords"]
        keywords.remove("upload")
        self.sop.write_text(json.dumps(sop))
        self.ingest()
        original_id = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, original_id, "approved", note="Original approval", now=STAMP)
        self.ingest()

        keywords.remove("deploy")
        self.sop.write_text(json.dumps(sop))
        cancellation = self.ingest()
        self.assertEqual(cancellation["skipped"][0]["reason"], "already_processed")
        (self.root / cancellation["delta_path"]).unlink()
        for _ in range(50):
            self.ingest(input_dir="other")

        keywords.extend(("deploy", "upload"))
        self.sop.write_text(json.dumps(sop))
        self.ingest()
        current_id = review.list_reviews(self.root)[0]["review_id"]
        processed = json.loads(self.state.read_text())["processed_files"][reference]
        self.assertIsNone(processed["last_released_review"])
        self.assertNotIn("pending_review_release", processed)
        review.record_review(self.root, current_id, "approved", note="Updated approval", now=STAMP)
        self.assertEqual(self.ingest()["changes"], [])

    def test_regating_reads_each_retained_delta_once_per_pass(self):
        sop = json.loads(self.sop.read_text())
        keywords = sop["boundary_gate"]["requires_review_keywords"]
        keywords.remove("deploy")
        self.sop.write_text(json.dumps(sop))
        for index in range(4):
            self.source(f"inbox/{index}.md", f"please deploy item {index}")
        self.ingest()
        self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "complete")
        self.ingest(delta_dir="cache/alternate-deltas")
        self.ingest()

        keywords.append("deploy")
        self.sop.write_text(json.dumps(sop))
        for pass_number in range(2):
            expected = [path for queue in ("cache/deltas", "cache/alternate-deltas")
                        for path in (self.root / queue).rglob("*.json")]
            with self.subTest(pass_number=pass_number), patch.object(
                phase1, "read_validated", wraps=phase1.read_validated
            ) as read:
                self.assertEqual(len(self.ingest()["changes"]), 4)
                actual = [call.args[0] for call in read.call_args_list if call.args[1] == "phase1_delta"]
                self.assertCountEqual(actual, expected)

    def test_policy_reversion_reuses_existing_approval_for_undrafted_release(self):
        for prior in ("undrafted", "drafted", "legacy_state"):
            with self.subTest(prior=prior):
                root = self.base / f"revert-id-{prior}"
                scaffold.init_workspace(root)
                text = "please deploy and upload this release"
                (root / "inbox/held.md").write_text(text)
                sop_path = root / "config/sops/phase1_ingestion.json"
                sop = json.loads(sop_path.read_text())
                keywords = sop["boundary_gate"]["requires_review_keywords"]
                keywords.remove("upload")
                sop_path.write_text(json.dumps(sop))
                phase1.run_phase1(root, now=STAMP)
                original_id = review.list_reviews(root)[0]["review_id"]
                review.record_review(root, original_id, "approved", note="Original approval", now=STAMP)
                phase1.run_phase1(root, now=STAMP)

                keywords.append("upload")
                sop_path.write_text(json.dumps(sop))
                phase1.run_phase1(root, now=STAMP)
                second_id = review.list_reviews(root)[0]["review_id"]
                review.record_review(root, second_id, "approved", note="Second approval", now=STAMP)
                phase1.run_phase1(root, now=STAMP)
                if prior == "drafted":
                    self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "complete")
                if prior == "legacy_state":
                    state_path = root / "cache/state/phase1_state.json"
                    state = json.loads(state_path.read_text())
                    del state["processed_files"]["inbox/held.md"]["last_released_review"]
                    state_path.write_text(json.dumps(state))

                keywords.remove("upload")
                sop_path.write_text(json.dumps(sop))
                reverted = phase1.run_phase1(root, now=STAMP)
                expected = prior != "drafted"
                self.assertEqual([item["approved_review"] for item in reverted["changes"]],
                                 [original_id] if expected else [])
                self.assertEqual(phase1.run_phase1(root, now=STAMP)["changes"], [])
                self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "complete")
                draft = json.loads((root / "cache/workers/execution_engine/draft.json").read_text())
                self.assertEqual([item["draft"] for item in draft["items"]], [text] if expected else [])

    def test_removal_discards_a_deferred_release(self):
        source = self.source("inbox/held.md", "please deploy this old release")
        self.ingest()
        rid = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, rid, "approved", note="Original approval", now=STAMP)
        self.ingest()
        self.configure(max_excerpt_chars=12)
        self.ingest()
        self.assertEqual(json.loads(self.state.read_text())["processed_files"]["inbox/held.md"]["pending_review_release"], rid)
        source.unlink()
        self.ingest()
        self.assertNotIn("pending_review_release", json.loads(self.state.read_text())["processed_files"]["inbox/held.md"])

    def test_standalone_context_hands_off_to_first_supervisor(self):
        self.source("inbox/note.md", "docs standalone handoff")
        self.ingest()
        self.assertEqual(worker.run_worker(self.root, "context_builder", now=STAMP)["status"], "complete")
        self.assertIn("last run: never", supervisor.status_summary(self.root))
        # Legacy standalone output lacks the new never-supervised baseline.
        (self.root / ".world_state.json").unlink()
        self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "complete")
        audit = self.root / "logs/transactions/supervisor.jsonl"
        audit.rename(audit.with_suffix(".saved"))
        (self.root / ".world_state.json").unlink()
        with self.assertRaisesRegex(ValueError, "prior supervisor evidence"):
            supervisor.status_summary(self.root)

    def test_legacy_presence_seeds_processed_paths_under_scan_root(self):
        a = self.source("inbox/a.md", "docs a")
        self.source("other/b.md", "docs b")
        self.ingest()
        self.ingest(input_dir="other")
        state = json.loads(self.state.read_text())
        del state["present_paths"]
        self.state.write_text(json.dumps(state))
        a.unlink()
        result = self.ingest()
        self.assertEqual(result["reconciliation"]["removed_paths"], ["inbox/a.md"])
        self.assertNotIn("other/b.md", result["reconciliation"]["removed_paths"])

    def test_held_presence_removal_and_defusing_reconcile_index_keep_evidence(self):
        held = self.source("inbox/held.md", "please deploy")
        self.ingest()
        payload = self.root / json.loads((self.root / "staging/pending_review.json").read_text())["latest"]
        evidence = payload.read_bytes()
        held.unlink()
        result = self.ingest()
        self.assertEqual(result["reconciliation"]["removed_paths"], ["inbox/held.md"])
        self.assertEqual(review.list_reviews(self.root), [])
        self.assertEqual(payload.read_bytes(), evidence)
        self.source("inbox/held.md", "please deploy")
        self.ingest()
        self.source("inbox/held.md", "docs safe now")
        self.ingest()
        self.assertEqual(review.list_reviews(self.root), [])
        self.assertEqual(payload.read_bytes(), evidence)

    def test_approval_releases_once_and_move_needs_new_review(self):
        self.source("inbox/held.md", "please deploy")
        self.ingest()
        first = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, first, "approved", note="Local drafting only", now=STAMP)
        release = self.ingest()["changes"][0]
        self.assertEqual((release["review_reasons"], release["approved_review"]), ([], first))
        self.assertEqual(self.ingest()["skipped"][0]["reason"], "review_decision_recorded")
        (self.root / "inbox/held.md").rename(self.root / "inbox/moved.md")
        moved = self.ingest()
        self.assertEqual(moved["reconciliation"]["removed_paths"], ["inbox/held.md"])
        self.assertNotEqual(review.list_reviews(self.root)[0]["review_id"], first)

    def test_mixed_clean_and_held_batch_drafts_only_clean_then_releases_approval(self):
        self.source("inbox/clean.md", "docs ordinary note")
        self.source("inbox/held.md", "please deploy")
        self.ingest()
        first = supervisor.run_supervisor(self.root, now=STAMP)
        self.assertEqual(first["status"], "blocked")
        draft_path = self.root / "cache/workers/execution_engine/draft.json"
        self.assertEqual([item["relative_path"] for item in json.loads(draft_path.read_text())["items"]], ["inbox/clean.md"])
        rid = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, rid, "approved", note="Allow local drafting", now=STAMP)
        result = self.ingest()
        self.assertEqual([item["relative_path"] for item in result["changes"]], ["inbox/held.md"])
        self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "complete")
        drafted = json.loads(draft_path.read_text())["items"]
        self.assertEqual([(item["relative_path"], item["approved_review"]) for item in drafted], [("inbox/held.md", rid)])
        self.assertEqual(self.ingest()["changes"], [])
        self.transport.assert_not_called()

    def test_wider_policy_approval_does_not_redraft_processed_bytes(self):
        sop = json.loads(self.sop.read_text())
        gate = sop["boundary_gate"]
        self.configure(boundary_gate={**gate, "requires_review_keywords": []})
        self.source("inbox/note.md", "docs deploy")
        self.ingest()
        self.configure(boundary_gate=gate)
        self.ingest()
        rid = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, rid, "approved", note="Already ingested", now=STAMP)
        result = self.ingest()
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["skipped"][0]["reason"], "review_decision_recorded")

    def test_rejection_skips_only_current_held_identity(self):
        path = self.source("inbox/note.md", "deploy this")
        self.ingest()
        rid = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, rid, "rejected", note="Declined", now=STAMP)
        self.assertEqual(self.ingest()["skipped"][0]["reason"], "review_decision_recorded")
        path.write_text("deploy different bytes")
        self.assertEqual(self.ingest()["summary"]["changed"], 1)
        self.assertNotEqual(review.list_reviews(self.root)[0]["review_id"], rid)

    def test_tampered_reused_payload_is_preserved_and_fails(self):
        self.source("inbox/held.md", "deploy this")
        self.ingest()
        payload = self.root / json.loads((self.root / "staging/pending_review.json").read_text())["latest"]
        data = json.loads(payload.read_text())
        data["source_items"][0]["excerpt"] = "forged display content"
        payload.write_text(json.dumps(data))
        before, state = payload.read_bytes(), self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, "review_evidence_invalid"):
            self.ingest()
        self.assertEqual(payload.read_bytes(), before)
        self.assertEqual(self.state.read_bytes(), state)

    def test_run_ids_check_active_archive_and_keep_fifty_runs(self):
        self.source("inbox/note.md", "docs note")
        first = self.ingest()
        self.assertNotIn("custom_delta_dirs", json.loads(self.state.read_text()))
        custom_dirs = ("custom/deltas", "another/deltas", "custom/deltas")
        for directory in custom_dirs:
            self.assertEqual(Path(self.ingest(delta_dir=directory)["delta_path"]).parent.as_posix(), directory)
        archive = self.root / "cache/deltas/archive"
        archive.mkdir()
        (self.root / first["delta_path"]).rename(archive / Path(first["delta_path"]).name)
        second = self.ingest()
        self.assertTrue(second["run_id"].endswith("-2"))
        self.assertTrue(self.ingest()["run_id"].endswith("-3"))
        for _ in range(48):
            self.ingest()
        state = json.loads(self.state.read_text())
        self.assertEqual(len(state["runs"]), 50)
        self.assertEqual(state["custom_delta_dirs"], sorted(set(custom_dirs)))
        self.assertTrue(all(Path(run["delta_path"]).parent.as_posix() == "cache/deltas" for run in state["runs"]))
        self.assertEqual(len(list((self.root / "cache/deltas").glob("*.json"))), 50)
        self.assertEqual(len(list(archive.glob("*.json"))), 1)

    def test_same_time_queue_order_survives_first_final_state_failure(self):
        for alternate_first in (False, True):
            with self.subTest(alternate_first=alternate_first):
                root = self.base / f"interrupted-first-publication-{alternate_first}"
                scaffold.init_workspace(root)
                (root / "inbox/held.md").write_text("please deploy this release")
                sop_path = root / "config/sops/phase1_ingestion.json"
                restrictive = "config/sops/restrictive.json"
                (root / restrictive).write_bytes(sop_path.read_bytes())
                sop = json.loads(sop_path.read_text())
                sop["boundary_gate"]["requires_review_keywords"].remove("deploy")
                sop_path.write_text(json.dumps(sop))
                state_path = root / "cache/state/phase1_state.json"
                write_json = phase1._write_json

                def interrupt(path, data, *, target=state_path, write=write_json):
                    if path == target and data.get("last_delta_path") is not None:
                        raise OSError("fixture final state write failure")
                    return write(path, data)

                alternate = {"sop_path": restrictive, "delta_dir": "alternate/deltas"}
                with patch.object(phase1, "_write_json", side_effect=interrupt):
                    with self.assertRaisesRegex(OSError, "final state write failure"):
                        phase1.run_phase1(root, now=STAMP, **(alternate if alternate_first else {}))
                first_dir = root / ("alternate/deltas" if alternate_first else "cache/deltas")
                first, = first_dir.glob("*.json")
                if not alternate_first:
                    self.assertEqual(worker.run_worker(root, "context_builder", now=STAMP)["status"], "complete")
                    second = phase1.run_phase1(root, now=STAMP, **alternate)
                rid = review.list_reviews(root)[0]["review_id"]
                review.record_review(root, rid, "rejected", note="Fixture rejection", now=STAMP)
                if alternate_first:
                    second = phase1.run_phase1(root, now=STAMP)

                order = json.loads(state_path.read_text())["same_time_delta_order"][STAMP]
                self.assertEqual(order, [first.relative_to(root).as_posix(), second["delta_path"]])
                if alternate_first:
                    self.assertEqual(supervisor.run_supervisor(root, now=STAMP)["status"], "complete")
                else:
                    direct = worker.run_worker(root, "execution_engine", now=STAMP)
                    self.assertEqual(direct["status"], "failed")
                    self.assertIn("superseded", direct["errors"][0]["message"])

    def test_same_second_run_id_does_not_reuse_a_pruned_lower_suffix(self):
        self.source("inbox/note.md", "docs note")
        first, second = self.ingest(), self.ingest()
        (self.root / first["delta_path"]).unlink()
        state = json.loads(self.state.read_text())
        state["runs"] = [state["runs"][-1]]
        self.state.write_text(json.dumps(state))
        self.assertTrue(second["run_id"].endswith("-2"))
        self.assertTrue(self.ingest()["run_id"].endswith("-3"))


class MatchingContractTests(unittest.TestCase):
    def test_inflections_obfuscation_and_word_boundaries(self):
        for text, keyword in (("deployed", "deploy"), ("committing", "commit"), ("send_email", "email"),
                              ("api_key", "api key"), ("d e p l o y", "deploy"), ("d.e.p.l.o.y", "deploy"),
                              ("dеploy", "deploy"), ("ＤＥＰＬＯＹ", "deploy"), ("de\u200bploy", "deploy"),
                              ("x|sh", "| sh"), ("x|deleting", "| delete")):
            with self.subTest(text=text):
                self.assertEqual(match_keywords(text, [keyword]), [keyword])
        self.assertEqual(match_keywords("committee reproduction token count", ["commit", "production", "access token"]), [])
        self.assertEqual(match_keywords("deploy send deploy", ["send", "deploy", "send"]), ["send", "deploy"])


if __name__ == "__main__":
    unittest.main()
