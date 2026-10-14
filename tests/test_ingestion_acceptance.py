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
from looping_box import model, phase1, review, scaffold, supervisor
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
        self.assertEqual(len(list((self.root / "cache/deltas").glob("*.json"))), 50)
        self.assertEqual(len(list(archive.glob("*.json"))), 1)


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
