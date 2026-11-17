"""The ./box launcher forwards to sibling checkouts and reports a missing one."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

BOX = Path(__file__).resolve().parents[1] / "box"


def box(*args, **env):
    # Only PATH from the operator: no keys, roots, or sibling homes leak in.
    return subprocess.run(["sh", str(BOX), *args], capture_output=True, text=True,
                          env={"PATH": os.environ.get("PATH", os.defpath), **env}, timeout=30)


class BoxLauncherTests(unittest.TestCase):
    def test_forwards_arguments_and_exit_code(self):
        with tempfile.TemporaryDirectory() as home:
            Path(home, "aop.py").write_text("import sys; print('aop:' + ' '.join(sys.argv[1:])); sys.exit(3)\n")
            result = box("build", "add", "--flag", AOP_HOME=home)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("aop:add --flag", result.stdout)

    def test_missing_sibling_and_unknown_mode_exit_2(self):
        with tempfile.TemporaryDirectory() as home:
            missing = box("crew", "doctor", MING_HOME=home)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("MING_HOME", missing.stderr)
        self.assertEqual(box("nope").returncode, 2)

    def test_crew_forwards_to_ming_home(self):
        with tempfile.TemporaryDirectory() as home:
            run = Path(home, "run.sh")
            run.write_text('#!/bin/sh\necho "ming:$*"\nexit 4\n')
            run.chmod(0o755)
            result = box("crew", "brief", "x", MING_HOME=home)
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("ming:brief x", result.stdout)

    def test_draft_runs_this_checkout(self):
        result = box("draft", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("looping", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
