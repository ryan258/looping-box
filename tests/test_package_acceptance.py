"""A01/A02/A31: actual installed wheel; never substitute the source checkout."""
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEAN = 'Project docs: summarize the backlog.'


def selected_cli(value):
    path = Path(value)
    if not value or not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError('LOOPING_BOX_TEST_CLI must name an absolute executable installed CLI')
    return path


class PackageSelectorTests(unittest.TestCase):
    def test_invalid_selection_fails_instead_of_falling_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            plain = base / 'not-executable'
            plain.write_text('fixture')
            plain.chmod(0o600)
            for value in ('', 'looping-box', str(base), str(base / 'missing'), str(plain)):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'absolute executable'):
                    selected_cli(value)


class InstalledPackageAcceptanceTests(unittest.TestCase):
    def test_a01_a02_a31_installed_workspace_resources_aliases_and_offline_draft(self):
        if 'LOOPING_BOX_TEST_CLI' not in os.environ:
            self.skipTest('installed-wheel acceptance unverified: set absolute LOOPING_BOX_TEST_CLI')
        executable = selected_cli(os.environ['LOOPING_BOX_TEST_CLI'])
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, elsewhere, guard = (base / name for name in ('workspace', 'cwd', 'guard'))
            for directory in (root, elsewhere, guard):
                directory.mkdir()
            marker, provenance = base / 'network-attempt', base / 'provenance.json'
            # PYTHONPATH contains only a guard, never checkout source. Fail even
            # when product code catches a forbidden-network exception.
            (guard / 'sitecustomize.py').write_text('''import atexit, importlib.metadata, json, os, sys
from pathlib import Path

def audit(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo'):
        Path(os.environ['PACKAGE_NETWORK_MARKER']).write_text(event)
        raise AssertionError('live network forbidden')
sys.addaudithook(audit)

def receipt():
    package = sys.modules.get('looping_box')
    if package is not None:
        Path(os.environ['PACKAGE_PROVENANCE']).write_text(json.dumps({
            'origin': package.__file__, 'version': importlib.metadata.version('looping-box')}))
atexit.register(receipt)
''', encoding='utf-8')
            env = {
                'HOME': str(base), 'XDG_CONFIG_HOME': str(base / 'keys'),
                'PATH': str(executable.parent) + os.pathsep + os.defpath,
                'PYTHONPATH': str(guard), 'PYTHONNOUSERSITE': '1',
                'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1',
                'LOOPING_BOX_REVIEW_KEY': 'fixture-key-not-a-secret',
                'LOOPING_BOX_APPROVER': 'fixture-operator',
                'PACKAGE_NETWORK_MARKER': str(marker), 'PACKAGE_PROVENANCE': str(provenance),
            }

            def invoke(*args, cli=executable, cwd=root, expected=0, extra_env=None):
                provenance.unlink(missing_ok=True)
                result = subprocess.run([str(cli), *args], cwd=cwd, env={**env, **(extra_env or {})},
                                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, expected, (args, result.stdout, result.stderr))
                self.assertNotIn('Traceback (most recent call last)', result.stderr)
                self.assertFalse(marker.exists(), 'network attempted by installed package')
                self.assertTrue(provenance.exists(), 'installed Python entry point/guard did not run')
                receipt = json.loads(provenance.read_text())
                self.assertEqual(receipt['version'], '0.2.0')
                origin = Path(receipt['origin']).resolve()
                self.assertNotIn(ROOT, origin.parents, 'editable/checkout import is not wheel acceptance')
                return result

            self.assertIn('created', invoke('init').stdout)
            config = root / 'config/super_loop.json'
            custom = json.loads(config.read_text())
            custom['max_files_per_cycle'] = 3
            config.write_text(json.dumps(custom) + '\n')
            saved = config.read_bytes()
            self.assertIn('kept', invoke('init').stdout)
            self.assertEqual(config.read_bytes(), saved)
            for relative in ('inbox', 'cache/state', 'cache/deltas', 'cache/supervisor', 'cache/verifiers',
                             'cache/workers/context_builder', 'cache/workers/execution_engine',
                             'staging/approvals', 'staging/rejections', 'staging/reviews', 'logs/transactions'):
                self.assertTrue((root / relative).is_dir(), relative)
            for relative in ('config/action_classes.json', 'config/sops/phase1_ingestion.json',
                             'config/sops/phase1_ingestion.md', '.env.example', '.gitignore'):
                self.assertTrue((root / relative).is_file(), relative)
            invoke('doctor')
            (root / 'inbox/notes.txt').write_text(CLEAN, encoding='utf-8')
            invoke('run', '--root', str(root), cwd=elsewhere,
                   extra_env={'LOOPING_BOX_ROOT': str(elsewhere)})
            draft_dir = root / 'cache/workers/execution_engine'
            draft = json.loads((draft_dir / 'draft.json').read_text())
            self.assertEqual(len(draft['items']), 1)
            self.assertEqual(draft['items'][0]['draft'], CLEAN)
            self.assertEqual(draft['items'][0]['relative_path'], 'inbox/notes.txt')
            self.assertEqual(draft['items'][0]['matched_routes'], ['documentation', 'task_backlog'])
            context = root / draft['source_context']
            self.assertEqual(draft['source_context_sha256'], hashlib.sha256(context.read_bytes()).hexdigest())
            archived = list((root / 'cache/deltas/archive').glob('*.json'))
            self.assertEqual(len(archived), 1)
            change = json.loads(archived[0].read_text())['changes'][0]
            self.assertEqual(change['sha256'], hashlib.sha256(CLEAN.encode()).hexdigest())
            self.assertEqual(change['excerpt'], CLEAN)
            self.assertEqual(change['matched_routes'], ['documentation', 'task_backlog'])
            markdown = (draft_dir / 'draft.md').read_text()
            self.assertIn('inbox/notes.txt', markdown)
            self.assertIn(CLEAN, markdown)
            self.assertFalse((elsewhere / '.world_state.json').exists())
            self.assertIn('status: clear', invoke('status', cwd=elsewhere,
                          extra_env={'LOOPING_BOX_ROOT': str(root)}).stdout)
            for command, args in (
                ('phase1', ['--root', str(root)]),
                ('supervisor', ['--root', str(root), '--status']),
                ('worker', ['execution_engine', '--root', str(root), '--dry-run']),
                ('review', ['--root', str(root), 'list']),
            ):
                alias = selected_cli(str(executable.parent / ('looping-box-' + command)))
                invoke(*args, cli=alias, cwd=elsewhere)
            invoke('review', 'list', '--root', str(root), expected=2)
            invoke('--help')
            invoke('unknown', expected=2)
            invoke('run', '--input-dir', 'custom', expected=2)
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
