"""T09 / A12, A21, A26–A28: offline diagnostics and audit/report integrity."""
import contextlib
import hashlib
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
from looping_box import _util, cli, doctor, model, phase1, report, scaffold, supervisor
from looping_box.validation import ValidationError
from looping_box.paths import UnsafePathError

STAMP = "2026-10-03T12:00:00Z"


class OperationsAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "workspace"
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.base / "keys"),
                                     "LOOPING_BOX_REVIEW_KEY": "fixture-review-key"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        model._env_loaded_for.clear()
        self.addCleanup(model._env_loaded_for.clear)
        for target in ("looping_box.model._transport", "urllib.request.urlopen"):
            guard = patch(target, side_effect=AssertionError("live provider forbidden"))
            guard.start()
            self.addCleanup(guard.stop)
        scaffold.init_workspace(self.root)

    def snapshot(self):
        return {path.relative_to(self.base).as_posix(): ("file", path.read_bytes(), path.stat().st_mode)
                if path.is_file() else ("directory",) for path in self.base.rglob("*")}

    def command(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main([args[0], "--root", str(self.root), *args[1:]])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue() + err.getvalue()

    def event(self, now=STAMP, changed=1):
        return {"event": "phase1.run", "generated_at": now, "run_id": "phase1-delta-20261003T120000Z",
                "changed": changed, "skipped": 0, "gate": "clear", "review_ids": [],
                "released_review_ids": [], "warnings": []}

    def chain(self):
        for day in (1, 2, 3):
            _util.append_audit(self.root, "phase1", self.event(f"2026-10-{day:02d}T12:00:00Z", changed=2))
        return self.root / "logs/transactions/phase1.jsonl"

    def ingest(self, text="Project docs one."):
        (self.root / "inbox/one.txt").write_text(text)
        return phase1.run_phase1(self.root, now=STAMP)

    def test_a26_chain_edit_delete_malformed_shape_and_utf8_fail_at_first_line(self):
        path = self.chain()
        original = path.read_bytes().splitlines()
        altered = json.loads(original[1]); altered["changed"] = 99
        shape = json.loads(original[1]); shape["changed"] = True
        shape["hash"] = hashlib.sha256(json.dumps({k: v for k, v in shape.items() if k != "hash"}, sort_keys=True).encode()).hexdigest()
        variants = ([original[0], json.dumps(altered).encode(), original[2]],
                    [original[0], original[2]], [original[0], b"{", original[2]],
                    [original[0], json.dumps(shape).encode(), original[2]], [original[0], b"\xff", original[2]])
        for lines in variants:
            with self.subTest(lines=lines):
                path.write_bytes(b"\n".join(lines) + b"\n")
                before = path.read_bytes()
                self.assertEqual(_util.verify_audit_chain(path), 2)
                for args in (("review", "audit"), ("report", "--since", "2099")):
                    code, text = self.command(*args)
                    self.assertEqual(code, 1)
                    self.assertIn("BROKEN at line 2", text)
                with self.assertRaisesRegex(ValidationError, "audit_invalid.*line 2"):
                    _util.append_audit(self.root, "phase1", self.event())
                self.assertEqual(path.read_bytes(), before)

    def test_a26_empty_missing_and_no_newline_logs(self):
        for args in (("review", "audit"), ("report",)):
            code, text = self.command(*args)
            self.assertEqual(code, 0)
            self.assertIn("no activity evidence", text)
        path = self.root / "logs/transactions/phase1.jsonl"
        path.write_bytes(b"")
        self.assertIn("no activity evidence", self.command("review", "audit")[1])
        _util.append_audit(self.root, "phase1", self.event())
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
        _util.append_audit(self.root, "phase1", self.event())
        self.assertEqual(len(path.read_bytes().splitlines()), 2)
        self.assertIsNone(_util.verify_audit_chain(path))

    def test_a26_append_uses_existing_lock_and_default_hash_serialization(self):
        with _util.project_lock(self.root, STAMP):
            _util.append_audit(self.root, "phase1", self.event())
        entry = json.loads((self.root / "logs/transactions/phase1.jsonl").read_text())
        claimed = entry.pop("hash")
        self.assertEqual(claimed, hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest())
        self.assertEqual(entry["prev"], "")

    def test_a26_append_does_not_hide_write_errors(self):
        path = self.chain()
        original = Path.open

        def denied(target, mode="r", *args, **kwargs):
            if target == path and mode == "a":
                raise PermissionError("fixture audit write denied")
            return original(target, mode, *args, **kwargs)

        before = path.read_bytes()
        with patch.object(Path, "open", new=denied), self.assertRaises(PermissionError):
            _util.append_audit(self.root, "phase1", self.event())
        self.assertEqual(path.read_bytes(), before)

    def test_a28_literal_broken_note_and_html_remain_text_with_exit_zero(self):
        note = "BROKEN at line 1 | <script>x</script>\nsecond\rline & tail"
        _util.append_audit(self.root, "review", {"event": "review.rejected", "generated_at": STAMP,
                           "review_id": "review-" + "a" * 16, "approver": "<b>operator|name</b>",
                           "interactive": False, "note": note, "payload_sha256": "b" * 64})
        before = self.snapshot()
        code, text = self.command("report")
        self.assertEqual(code, 0)
        self.assertIn("BROKEN at line 1 \\| &lt;script&gt;x&lt;/script&gt; second line &amp; tail", text)
        self.assertNotIn("<script>", text)
        self.assertNotIn("<b>", text)
        self.assertFalse(report.report_result(self.root).failed)
        self.assertEqual(self.snapshot(), before)

    def test_a28_since_filters_events_but_not_live_pending_or_full_integrity(self):
        path = self.chain()
        self.ingest("Please deploy this release.")
        code, text = self.command("report", "--since", "2026-10-02")
        self.assertEqual(code, 0)
        self.assertIn("phase 1 runs: 3 (5 files changed", text)
        self.assertIn("still pending: 1", text)
        code, text = self.command("report", "--since", "2099")
        self.assertEqual(code, 0)
        self.assertIn("phase 1 runs: 0", text)
        self.assertIn("still pending: 1", text)
        lines = path.read_bytes().splitlines(); lines[0] = b"{"
        path.write_bytes(b"\n".join(lines) + b"\n")
        code, text = self.command("report", "--since", "2099")
        self.assertEqual(code, 1)
        self.assertIn("BROKEN at line 1", text)

    def test_a28_supported_since_prefixes_and_calendar_rejections(self):
        for value in ("2026", "2026-10", "2024-02-29", STAMP, "0001", "9999-12-31T23:59:59Z"):
            with self.subTest(valid=value):
                self.assertEqual(self.command("report", "--since", value)[0], 0)
        for value in ("26", "0000", "2026-1", "2026-13", "2026-02-29", "2026-04-31", "2026-10-03T12:00",
                      "2026-10-03T12:00:00+00:00", "2026-10-03T24:00:00Z", "2026-10-03T12:00:60Z", "2026\n", "<script>"):
            with self.subTest(invalid=value):
                self.assertEqual(self.command("report", "--since", value)[0], 2)

    def test_a28_unavailable_pending_evidence_never_displays_zero(self):
        self.ingest("Please deploy this release.")
        (self.root / "staging/pending_review.json").unlink()
        before = self.snapshot()
        for args in (("report",), ("status",), ("doctor",)):
            code, text = self.command(*args)
            self.assertEqual(code, 1)
            self.assertIn("unavailable", text)
            self.assertNotIn("still pending: 0", text)
        self.assertEqual(self.snapshot(), before)

    def test_a28_unreadable_audit_is_unavailable_not_zero(self):
        path = self.chain()
        read = Path.read_bytes

        def denied(target):
            if target == path:
                raise PermissionError("fixture read denied")
            return read(target)

        with patch.object(Path, "read_bytes", new=denied):
            result = report.report_result(self.root)
        self.assertTrue(result.failed)
        self.assertIn("phase 1 runs: unavailable", result.text)
        self.assertIn("phase1.jsonl: unavailable", result.text)

    def test_a27_fresh_doctor_is_filesystem_and_network_read_only(self):
        before, environment = self.snapshot(), dict(os.environ)
        code, text = self.command("doctor")
        self.assertEqual(code, 0)
        self.assertIn("supervisor never run", text)
        self.assertIn("no activity evidence", text)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(dict(os.environ), environment)

    def test_a27_held_kernel_lock_is_informational(self):
        with _util.project_lock(self.root, STAMP):
            before = self.snapshot()
            code, text = self.command("doctor")
            self.assertEqual(code, 0)
            self.assertIn("kernel lock held", text)
            self.assertEqual(self.snapshot(), before)

    def test_doctor_preserves_unsafe_path_rejection_and_external_bytes(self):
        outside = self.base / "outside.env"
        outside.write_text("fixture outside sentinel")
        (self.root / ".env").symlink_to(outside)
        with self.assertRaises(UnsafePathError):
            doctor.run_doctor(self.root)
        self.assertEqual(self.command("doctor")[0], 1)
        self.assertEqual(outside.read_text(), "fixture outside sentinel")
        self.assertTrue((self.root / ".env").is_symlink())

    def test_a27_missing_unused_key_warns_without_creating_it(self):
        os.environ.pop("LOOPING_BOX_REVIEW_KEY")
        before = self.snapshot()
        findings = doctor.run_doctor(self.root)
        self.assertTrue(any(level == "warn" and "key: none yet" in message for level, message, _ in findings))
        self.assertFalse(any(level == "fail" for level, _, _ in findings))
        self.assertEqual(self.snapshot(), before)

    def test_a27_thresholds_only_warn_above_bounds(self):
        delta = self.ingest()
        path = self.root / "cache/state/phase1_state.json"
        state = json.loads(path.read_text())
        first = {"first_seen_path": "inbox/one.txt", "first_seen_at": STAMP}
        state["processed_hashes"] = {f"{n:064x}": first for n in range(5000)}
        path.write_text(json.dumps(state))
        archive = self.root / "cache/deltas/archive"
        archive.mkdir(exist_ok=True)
        raw = (self.root / delta["delta_path"]).read_bytes()
        for n in range(1000):
            (archive / f"delta-{n}.json").write_bytes(raw)
        self.assertFalse(any(level == "warn" and ("hashes" in message or "archived deltas" in message)
                             for level, message, _ in doctor.run_doctor(self.root)))
        state["processed_hashes"][f"{5000:064x}"] = first
        path.write_text(json.dumps(state))
        (archive / "delta-1000.json").write_bytes(raw)
        before = self.snapshot()
        findings = doctor.run_doctor(self.root)
        for fragment in ("5001 hashes", "1001 archived deltas"):
            self.assertTrue(any(level == "warn" and fragment in message for level, message, _ in findings))
        self.assertFalse(any(level == "fail" for level, _, _ in findings))
        self.assertEqual(self.snapshot(), before)

    def test_a21_a27_env_endpoint_roles_permissions_and_no_gate_warnings(self):
        sop = self.root / "config/sops/phase1_ingestion.json"
        value = json.loads(sop.read_text()); value["boundary_gate"]["requires_review_keywords"] = []
        sop.write_text(json.dumps(value))
        env = self.root / ".env"
        env.write_text('OPENROUTER_API_KEY=fixture-secret\nMODEL_EXECUTION_ENGINE=fixture/model\nOPENROUTER_BASE_URL=http://localhost:1234/v1\nLOOPING_BOX_REVIEW_KEY=ignored\n')
        env.chmod(0o644)
        before, process = self.snapshot(), dict(os.environ)
        code, text = self.command("doctor")
        self.assertEqual(code, 0)
        for fragment in ("no gate keywords", "not HTTPS", "group/other permissions", "execution_engine", "stale_lock_seconds is deprecated"):
            self.assertIn(fragment, text)
        self.assertNotIn("fixture-secret", text)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(dict(os.environ), process)
        self.assertNotIn(str(self.root), model._env_loaded_for)
        os.environ["MODEL_EXECUTION_ENGINE"] = ""
        self.assertIn("model roles: none", self.command("doctor")[1])

    def test_a27_invalid_endpoint_and_required_artifacts_fail(self):
        for value in ("file:///tmp/private", "https://host:bad/v1", "https://"):
            with self.subTest(endpoint=value), patch.dict(os.environ, {"OPENROUTER_BASE_URL": value}):
                self.assertEqual(self.command("doctor")[0], 1)
        for name in ("config/super_loop.json", "cache/workers/context_builder/context_package.json",
                     "cache/workers/execution_engine/draft.json"):
            with self.subTest(artifact=name):
                path = self.root / name
                previous = path.read_bytes() if path.exists() else None
                path.parent.mkdir(parents=True, exist_ok=True); path.write_text("[]")
                before = self.snapshot()
                self.assertEqual(self.command("doctor")[0], 1)
                self.assertEqual(self.snapshot(), before)
                if previous is None:
                    path.unlink()
                else:
                    path.write_bytes(previous)

    def test_a27_corrupt_review_and_audit_fail_without_repair(self):
        self.ingest("Please deploy this release.")
        index = self.root / "staging/pending_review.json"
        index.write_text("[]")
        log = self.root / "logs/transactions/phase1.jsonl"
        log.write_text("{\n")
        before = self.snapshot()
        code, text = self.command("doctor")
        self.assertEqual(code, 1)
        self.assertIn("pending review evidence unavailable", text)
        self.assertIn("audit chain broken", text)
        self.assertEqual(self.snapshot(), before)

    def test_status_shows_last_run_known_pending_and_error_specific_action(self):
        self.ingest()
        supervisor.run_supervisor(self.root, now=STAMP)
        state_path = self.root / ".world_state.json"
        state = json.loads(state_path.read_text())
        for reason, action in (("source_reingest_required: changed", "ingest"),
                               ("execution_engine model call failed", "configuration/provider"),
                               ("unknown schema", "compatible version")):
            with self.subTest(reason=reason):
                state["recovery"]["last_error"] = reason
                state_path.write_text(json.dumps(state))
                code, text = self.command("status")
                self.assertEqual(code, 0)
                self.assertIn("status: failed", text)
                self.assertIn(action, text)
                self.assertIn("pending reviews: 0", text)
                self.assertIn("last run: " + STAMP, text)


if __name__ == "__main__":
    unittest.main()
