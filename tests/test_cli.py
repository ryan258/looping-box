import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from looping_box import cli
from looping_box._util import append_audit
from looping_box.doctor import run_doctor


def _workspace(tmp: str) -> Path:
    root = Path(tmp).resolve()
    shutil.copytree(ROOT / "config", root / "config")  # the real shipped config
    (root / "inbox").mkdir()
    return root


def _run(*argv: str) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(list(argv))
    return code, buffer.getvalue()


class _IsolatedCliTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": temp.name,
                                     "LOOPING_BOX_REVIEW_KEY": "fixture-key"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        transport = patch("looping_box.model._transport", side_effect=AssertionError("live provider forbidden"))
        transport.start()
        self.addCleanup(transport.stop)


class CliTests(_IsolatedCliTest):
    def test_run_then_status_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            (root / "inbox" / "notes.txt").write_text("Project docs: summarize the backlog.")

            code, out = _run("run", "--root", str(root))

            self.assertEqual(code, 0)
            self.assertIn("review=clear", out)
            self.assertTrue((root / "cache" / "workers" / "execution_engine" / "draft.json").exists())
            code, out = _run("status", "--root", str(root))
            self.assertEqual(code, 0)
            self.assertIn("status: clear", out)

    def test_unknown_command_is_an_error_and_help_is_not(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["nope"]), 2)
        self.assertEqual(_run("--help")[0], 0)


class DoctorTests(_IsolatedCliTest):
    def _levels(self, root: Path):
        return [(level, message) for level, message, _ in run_doctor(root)]

    def test_fresh_workspace_with_shipped_config_has_no_failures_or_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            findings = self._levels(_workspace(tmp))
            self.assertEqual([f for f in findings if f[0] != "ok"], [])

    def test_flags_stale_lock_missing_action_class_and_pending_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            (root / "inbox" / "r.txt").write_text("please deploy the release")
            _run("phase1", "--root", str(root))
            (root / "config" / "action_classes.json").write_text(
                json.dumps({"schema": "looping-box.action-classes.v1", "default_class": "review_required", "classes": {}})
            )
            (root / ".looping_box.lock").write_text(
                json.dumps({"created_at": "2020-01-01T00:00:00Z", "pid": 1}) + "\n"
            )

            findings = self._levels(root)
            # T03/T09: age cannot establish kernel ownership; invalid config fails.
            self.assertIn(("ok", "no lock held"), findings)
            self.assertTrue(any(level == "fail" and "no explicit action class" in m for level, m in findings))
            self.assertTrue(any(level == "warn" and "pending review" in m for level, m in findings))

    def test_broken_audit_chain_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            event = {"event": "phase1.run", "generated_at": "2026-10-03T12:00:00Z", "run_id": "phase1-delta-20261003T120000Z",
                     "changed": 1, "skipped": 0, "gate": "clear", "review_ids": [], "released_review_ids": [], "warnings": []}
            append_audit(root, "phase1", event)
            append_audit(root, "phase1", event)
            log = root / "logs" / "transactions" / "phase1.jsonl"
            lines = [json.loads(line) for line in log.read_text().splitlines()]
            lines[0]["event"] = "tampered"
            log.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

            self.assertTrue(any(level == "fail" and "audit chain" in m for level, m in self._levels(root)))


if __name__ == "__main__":
    unittest.main()
