"""T10 command contracts. Subprocesses use isolated roots and forbid HTTP.

Console aliases are launched from the declared entry-point targets. Resource
checks use a staged package ZIP; installed-wheel acceptance belongs to T11.
"""

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLEAN = "Project docs: summarize the backlog."
CUSTOM_FLAGS = ("--input-dir", "--sop", "--state", "--delta-dir", "--staging-dir")


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
            for path in root.rglob("*") if not path.is_symlink()}


class CommandAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.cwd = self.base / "cwd"
        self.cwd.mkdir()
        self.guard = self.base / "guard"
        self.guard.mkdir()
        (self.guard / "sitecustomize.py").write_text(
            "import os, sys\n"
            "from pathlib import Path\n"
            "def no_network(event, args):\n"
            "    if event in ('socket.connect', 'socket.getaddrinfo'):\n"
            "        marker = os.environ.get('LOOPING_BOX_TEST_NETWORK_MARKER')\n"
            "        if marker:\n"
            "            Path(marker).write_text(event)\n"
            "        raise AssertionError('live provider forbidden')\n"
            "sys.addaudithook(no_network)\n", encoding="utf-8")
        self.bin = self.base / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        self.env = {
            "PATH": str(self.bin) + os.pathsep + os.defpath,
            "HOME": str(self.base), "XDG_CONFIG_HOME": str(self.base / "keys"),
            "PYTHONPATH": str(self.guard) + os.pathsep + str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
            "LOOPING_BOX_REVIEW_KEY": "fixture-key-not-a-secret",
            "LOOPING_BOX_APPROVER": "fixture-operator",
        }
        # Use exactly the targets declared for installed console scripts, without
        # installing or accidentally invoking an older package from the operator.
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.aliases = dict(re.findall(r'^(looping-box(?:-[a-z0-9]+)?) = "([^"]+)"', metadata, re.M))
        for name, target in self.aliases.items():
            module, function = target.split(":")
            launcher = self.bin / name
            launcher.write_text(f"#!{sys.executable}\nfrom {module} import {function}\n"
                                f"raise SystemExit({function}())\n", encoding="utf-8")
            launcher.chmod(0o755)

    def invoke(self, *args, code=0, cwd=None, env=None, executable=None):
        command = [str(executable)] if executable else [sys.executable, "-m", "looping_box"]
        result = subprocess.run(command + list(args), cwd=cwd or self.cwd, env=env or self.env,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, code, (command, args, result.stdout, result.stderr))
        self.assertNotIn("Traceback (most recent call last)", result.stderr)
        return result

    def workspace(self, name="workspace", text=CLEAN):
        root = self.base / name
        self.invoke("init", str(root))
        if text is not None:
            (root / "inbox" / "notes.txt").write_text(text, encoding="utf-8")
        return root

    def wrapper(self, name="checkout", with_source=True):
        checkout = self.base / name
        checkout.mkdir()
        shutil.copy2(ROOT / "startday.sh", checkout / "startday.sh")
        if with_source:
            shutil.copytree(ROOT / "src" / "looping_box", checkout / "src" / "looping_box",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return checkout / "startday.sh"

    def test_help_and_usage_exits(self):
        for args in ((), ("--help",), ("help",)):
            self.assertIn("usage:", self.invoke(*args).stdout)
        for args in (("unknown",), ("supervisor",), ("worker", "unknown"),
                     ("review",), ("report", "--since", "yesterday")):
            with self.subTest(args=args):
                self.invoke(*args, code=2)

    def test_run_rejects_every_custom_phase1_flag_before_mutation(self):
        root = self.workspace()
        before = snapshot(root)
        for flag in CUSTOM_FLAGS:
            with self.subTest(flag=flag):
                self.invoke("run", "--root", str(root), flag, "custom", code=2)
                self.invoke("run", "--root", str(root), flag + "=custom", code=2)
        self.assertEqual(snapshot(root), before)

    def test_cli_root_precedence_and_offline_draft(self):
        current = self.workspace("current")
        configured = self.workspace("configured")
        explicit = self.workspace("explicit")
        env = dict(self.env, LOOPING_BOX_ROOT=str(configured))
        self.invoke("run", "--root", str(explicit), cwd=current, env=env)
        draft_path = Path("cache/workers/execution_engine/draft.json")
        self.assertFalse((current / draft_path).exists())
        self.assertFalse((configured / draft_path).exists())
        self.invoke("run", cwd=current, env=env)
        self.assertTrue((configured / draft_path).exists())
        self.assertFalse((current / draft_path).exists())
        self.invoke("run", cwd=current)
        for root in (current, configured, explicit):
            draft = json.loads((root / draft_path).read_text())
            self.assertEqual(draft["items"][0]["draft"], CLEAN)
            self.assertEqual(draft["items"][0]["relative_path"], "inbox/notes.txt")
            context = root / draft["source_context"]
            self.assertEqual(draft["source_context_sha256"], hashlib.sha256(context.read_bytes()).hexdigest())

    def test_init_default_uses_cwd_and_positional_directory_over_environment(self):
        ignored = self.base / "ignored"
        env = dict(self.env, LOOPING_BOX_ROOT=str(ignored))
        self.invoke("init", env=env)
        self.assertTrue((self.cwd / "config/sops/phase1_ingestion.json").is_file())
        positional = self.base / "positional"
        self.invoke("init", str(positional), env=env)
        self.assertTrue((positional / "inbox").is_dir())
        self.assertFalse(ignored.exists())

    def test_expanded_root_and_selected_root_symlink(self):
        self.invoke("init", "~/workspace")
        root = self.base / "workspace"
        (root / "inbox/notes.txt").write_text(CLEAN)
        alias = self.base / "root-alias"
        alias.symlink_to(root, target_is_directory=True)
        self.invoke("run", "--root", "~/root-alias")
        for args in (("status",), ("phase1",), ("supervisor", "--status"),
                     ("doctor",), ("report",), ("worker", "execution_engine", "--dry-run")):
            with self.subTest(command=args):
                self.invoke(*args, "--root", "~/root-alias")
        self.invoke("review", "--root", "~/root-alias", "list")

    def test_unexpandable_root_has_diagnostic_without_traceback(self):
        bad = "~looping_box_nonexistent_fixture_user_472198"
        for args in (("run",), ("status",), ("phase1",), ("supervisor", "--once"),
                     ("worker", "context_builder"), ("doctor",), ("report",)):
            with self.subTest(command=args):
                result = self.invoke(*args, "--root", bad, code=1)
                self.assertIn("invalid_root", result.stdout + result.stderr)
        result = self.invoke("review", "--root", bad, "list", code=1)
        self.assertIn("invalid_root", result.stdout + result.stderr)
        self.invoke("init", bad, code=1)
        self.assertEqual(snapshot(self.cwd), {})

    def test_wrapper_root_precedence_source_preference_and_custom_flag_rejection(self):
        wrapper = self.wrapper()
        self.invoke("init", str(wrapper.parent))
        (wrapper.parent / "inbox/notes.txt").write_text(CLEAN)
        configured = self.workspace("configured")
        explicit = self.workspace("explicit")
        # A stale executable would fail; source preference must bypass it.
        (self.bin / "looping-box").write_text(f"#!{sys.executable}\nraise SystemExit(99)\n")
        before = snapshot(explicit)
        for flag in CUSTOM_FLAGS:
            self.invoke("--root", str(explicit), flag, "custom", executable=wrapper, code=2)
        self.assertEqual(snapshot(explicit), before)
        env = dict(self.env, LOOPING_BOX_ROOT=str(configured))
        self.invoke("--root", str(explicit), executable=wrapper, env=env)
        state = Path(".world_state.json")
        self.assertTrue((explicit / state).exists())
        self.assertFalse((configured / state).exists())
        self.assertFalse((wrapper.parent / state).exists())
        self.invoke(executable=wrapper, env=env)
        self.assertTrue((configured / state).exists())
        self.invoke(executable=wrapper)
        self.assertTrue((wrapper.parent / state).exists())
        self.assertFalse((self.cwd / state).exists())

    def test_wrapper_without_checkout_source_uses_console_entrypoint(self):
        root = self.workspace()
        self.invoke("--root", str(root), executable=self.wrapper(with_source=False))
        self.assertTrue((root / "cache/workers/execution_engine/draft.json").is_file())

    def test_demos_use_fresh_offline_workspaces_and_show_expected_outcomes(self):
        inherited = self.workspace("inherited")
        inherited_before = snapshot(inherited)
        scratch = self.base / "scratch"
        scratch.mkdir()
        old_demo = scratch / "looping-box-demo"
        old_demo.mkdir()
        (old_demo / "preserve.txt").write_text("legacy demo evidence")
        marker = self.base / "network-attempt"
        env = dict(self.env, TMPDIR=str(scratch), LOOPING_BOX_ROOT=str(inherited),
                   OPENROUTER_API_KEY="fixture-provider-key", OPENROUTER_BASE_URL="https://provider.example.test/v1",
                   MODEL_CONTEXT_BUILDER="fixture/context", MODEL_EXECUTION_ENGINE="fixture/draft",
                   MODEL_VERIFIER="fixture/review", LOOPING_BOX_TEST_NETWORK_MARKER=str(marker))

        one = self.invoke(executable=ROOT / "demo-1.sh", env=env).stdout
        two = self.invoke(executable=ROOT / "demo-2.sh", env=env).stdout
        three = self.invoke(executable=ROOT / "demo-3.sh", env=env).stdout
        self.assertIn("status: complete", one)
        self.assertIn("review_required staging/reviews/", two)
        self.assertIn("status: blocked", two)
        self.assertIn("approved: review-", three)
        self.assertIn("status: complete", three)

        sections = self.invoke(executable=ROOT / "demos/run-all.sh", env=env).stdout
        outcomes = (
            "Safe note, drafted locally:", "declined, as designed", "broken chain detected",
            "== Held now:\n  (nothing is held)", "CI exit code 2: inspect",
            "-> 10/10 caught", "caught 10/10 incidents", "DM approved", "## Decisions",
        )
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                self.assertIn(outcome, sections)
        self.assertEqual(sections.count("CI exit code 0: PASS"), 2)
        roots = [Path(path) for path in re.findall(r"^Demo workspace: (.+)$", one + two + three + sections, re.M)]
        self.assertEqual(len(roots), 11)
        self.assertEqual(len(set(roots)), len(roots))
        for root in roots:
            self.assertEqual(root.parent, scratch)
            self.assertTrue(root.is_dir())
        self.assertEqual(snapshot(inherited), inherited_before)
        self.assertEqual((old_demo / "preserve.txt").read_text(), "legacy demo evidence")
        self.assertFalse(marker.exists())

    def test_low_level_phase1_keeps_contained_custom_paths(self):
        root = self.workspace(text=None)
        (root / "custom").mkdir()
        (root / "custom/notes.txt").write_text(CLEAN)
        shutil.copyfile(root / "config/sops/phase1_ingestion.json", root / "sop.json")
        self.invoke("phase1", "--root", str(root), "--input-dir", "custom", "--sop", "sop.json",
                    "--state", "custom-state/state.json", "--delta-dir", "custom-deltas",
                    "--staging-dir", "custom-staging")
        delta = json.loads(next((root / "custom-deltas").glob("*.json")).read_text())
        self.assertEqual(delta["changes"][0]["relative_path"], "custom/notes.txt")
        self.assertTrue((root / "custom-state/state.json").exists())
        self.assertFalse((root / "cache/state/phase1_state.json").exists())
        self.assertFalse((root / ".world_state.json").exists())

    def test_held_exits_and_read_only_inspection(self):
        root = self.workspace(text="Deploy the site.")
        self.invoke("phase1", "--root", str(root))
        self.invoke("worker", "context_builder", "--root", str(root), "--dry-run", code=2)
        self.invoke("run", "--root", str(root), code=2)
        self.invoke("supervisor", "--once", "--root", str(root), code=2)
        before = snapshot(root)
        result = self.invoke("status", "--root", str(root))
        self.assertIn("pending", result.stdout)
        self.invoke("supervisor", "--once", "--status", "--root", str(root))
        self.invoke("review", "--root", str(root), "list")
        self.invoke("review", "--root", str(root), "key")
        self.invoke("doctor", "--root", str(root))
        self.invoke("report", "--root", str(root))
        self.invoke("review", "--root", str(root), "audit")
        self.assertEqual(snapshot(root), before)

    def test_review_flag_order_unknown_id_and_blank_note(self):
        root = self.workspace(text="Deploy the site.")
        self.invoke("phase1", "--root", str(root))
        output = self.invoke("review", "--root", str(root), "list").stdout
        review_id = output.split()[0]
        self.invoke("review", "--root", str(root), "show", review_id)
        self.invoke("review", "list", "--root", str(root), code=2)
        self.invoke("review", "--root", str(root), "show", "review-0000000000000000", code=1)
        before = snapshot(root)
        self.invoke("review", "--root", str(root), "reject", review_id, "--note", " \t", code=1)
        self.invoke("review", "--root", str(root), "approve", review_id, "--note", "inspect", code=1)
        self.assertEqual(snapshot(root), before)

    def test_legacy_console_aliases_match_umbrella_commands(self):
        self.assertEqual(set(self.aliases), {"looping-box", "looping-box-phase1", "looping-box-worker",
                                            "looping-box-supervisor", "looping-box-review"})
        root = self.workspace(text="Deploy the site.")
        self.invoke("--root", str(root), executable=self.bin / "looping-box-phase1")
        pairs = (("supervisor", ["--status", "--root", str(root)], 0),
                 ("review", ["--root", str(root), "list"], 0),
                 ("worker", ["context_builder", "--root", str(root), "--dry-run"], 2))
        for command, args, code in pairs:
            with self.subTest(command=command):
                umbrella = self.invoke(command, *args, code=code)
                alias = self.invoke(*args, code=code, executable=self.bin / ("looping-box-" + command))
                self.assertEqual(alias.stdout, umbrella.stdout)
                self.assertEqual(alias.stderr, umbrella.stderr)
        self.invoke("--root", str(root), "list", executable=self.bin / "looping-box-review")
        self.invoke("list", "--root", str(root), executable=self.bin / "looping-box-review", code=2)
        self.invoke("--once", "--root", str(root), executable=self.bin / "looping-box-supervisor", code=2)

    def test_failure_and_busy_exits_preserve_evidence(self):
        root = self.workspace()
        (root / "config/super_loop.json").write_text("{}")
        before = snapshot(root)
        result = self.invoke("init", str(root), code=1)
        self.assertIn("invalid_config", result.stderr)
        self.invoke("run", "--root", str(root), code=1)
        self.assertEqual(snapshot(root), before)
        fresh = self.base / "empty"
        fresh.mkdir()
        result = self.invoke("run", "--root", str(fresh), code=1)
        self.assertIn("workspace_uninitialized", result.stderr)
        self.assertEqual(snapshot(fresh), {})
        busy = self.workspace("busy")
        with (busy / ".looping_box.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = snapshot(busy)
            for args in (("run",), ("phase1",), ("supervisor", "--once"),
                         ("worker", "context_builder")):
                result = self.invoke(*args, "--root", str(busy), code=1)
                self.assertIn("busy", result.stdout + result.stderr)
            self.assertEqual(snapshot(busy), before)

    def test_init_preflight_conflicts_and_malformed_config_do_not_create_scaffold(self):
        for name in ("conflict", "malformed", "symlink"):
            with self.subTest(name=name):
                root = self.base / name
                root.mkdir()
                if name == "conflict":
                    (root / "inbox").write_text("keep")
                elif name == "malformed":
                    (root / "config").mkdir()
                    (root / "config/super_loop.json").write_text("{")
                else:
                    (root / "config").symlink_to(self.cwd, target_is_directory=True)
                before, outside = snapshot(root), snapshot(self.cwd)
                self.invoke("init", str(root), code=1)
                self.assertEqual(snapshot(root), before)
                self.assertEqual(snapshot(self.cwd), outside)
                self.assertFalse((root / ".looping_box.lock").exists())

    def test_package_metadata_template_parity_and_resources_without_source_directory(self):
        metadata = (ROOT / "pyproject.toml").read_text()
        self.assertIn('version = "0.2.0"', metadata)
        self.assertIn('requires-python = ">=3.9,<3.15"', metadata)
        self.assertIn('"templates/schemas/*.json"', metadata)
        self.assertEqual((ROOT / ".env.example").read_bytes(),
                         (ROOT / "src/looping_box/templates/env.example").read_bytes())
        for line in (ROOT / ".env.example").read_text().splitlines():
            if line.startswith(("MODEL_", "OPENROUTER_API_KEY=")):
                self.assertEqual(line.split("=", 1)[1], "")
        schemas = sorted((ROOT / "docs/schemas").glob("*.json"))
        self.assertTrue(schemas)
        for schema in schemas:
            self.assertEqual(schema.read_bytes(),
                             (ROOT / "src/looping_box/templates/schemas" / schema.name).read_bytes())
        # Stage exactly the declared data patterns alongside package Python files.
        archive = self.base / "package.zip"
        package = ROOT / "src/looping_box"
        patterns = ["*.py", "templates/*", "templates/sops/*", "templates/schemas/*.json"]
        with zipfile.ZipFile(archive, "w") as bundle:
            for path in sorted({p for pattern in patterns for p in package.glob(pattern) if p.is_file()}):
                bundle.write(path, "looping_box/" + path.relative_to(package).as_posix())
        env = dict(self.env, PYTHONPATH=str(self.guard) + os.pathsep + str(archive))
        root = self.base / "zip-workspace"
        self.invoke("init", str(root), env=env)
        self.invoke("init", str(root), env=env)
        self.invoke("doctor", "--root", str(root), env=env)
        (root / "inbox/notes.txt").write_text(CLEAN)
        self.invoke("run", "--root", str(root), env=env)
        self.assertTrue((root / "cache/workers/execution_engine/draft.json").is_file())


if __name__ == "__main__":
    unittest.main()
