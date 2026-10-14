import contextlib
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

from looping_box import cli
from looping_box.doctor import run_doctor
from looping_box.scaffold import TEMPLATES, init_workspace, template_text


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": temp.name,
                                     "LOOPING_BOX_REVIEW_KEY": "fixture-review-key"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        transport = patch("looping_box.model._transport", side_effect=AssertionError("live provider forbidden"))
        transport.start()
        self.addCleanup(transport.stop)

    def test_init_creates_a_workspace_that_is_healthy_and_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = init_workspace(root)
            self.assertTrue(all(status == "created" for status, _ in results))
            self.assertTrue((root / "inbox").is_dir())
            self.assertTrue((root / "staging" / "reviews").is_dir())
            self.assertEqual([f for f in run_doctor(root) if f[0] != "ok"], [])

            (root / "inbox" / "notes.txt").write_text("Project docs: summarize the backlog.")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = cli.main(["run", "--root", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("review=clear", out.getvalue())
            self.assertTrue((root / "cache" / "workers" / "execution_engine" / "draft.json").exists())

    def test_init_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_workspace(root)
            custom = root / "config" / "super_loop.json"
            config = json.loads(custom.read_text())
            config["max_files_per_cycle"] = 3
            expected = json.dumps(config) + "\n"
            custom.write_text(expected)

            results = init_workspace(root)

            self.assertTrue(all(status == "kept" for status, _ in results))
            self.assertEqual(custom.read_text(), expected)

    def test_bundled_templates_match_this_repos_own_config(self):
        # The repo's config/ is a workspace instance; the package templates are the
        # source of truth for new workspaces. They must not drift apart.
        for template, destination in TEMPLATES.items():
            if destination.startswith("config/"):
                with self.subTest(template=template):
                    self.assertEqual(template_text(template), (ROOT / destination).read_text())

    def test_run_in_an_uninitialized_directory_says_how_to_fix_it(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(["run", "--root", tmp])
        self.assertEqual(code, 1)
        self.assertIn("looping-box init", err.getvalue())


if __name__ == "__main__":
    unittest.main()
