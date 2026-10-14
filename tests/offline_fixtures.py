"""Unittest module isolation for legacy fixtures; no operator environment or HTTP."""
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


def isolate_module():
    stack = ExitStack()
    unittest.addModuleCleanup(stack.close)
    base = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
    stack.enter_context(patch.dict(os.environ, {
        'HOME': str(base), 'XDG_CONFIG_HOME': str(base / 'keys'),
        'PATH': os.defpath, 'LOOPING_BOX_REVIEW_KEY': 'test-review-key',
        'LOOPING_BOX_APPROVER': 'fixture-operator',
    }, clear=True))
    for target in ('urllib.request.urlopen', 'socket.create_connection', 'socket.socket.connect'):
        stack.enter_context(patch(target, side_effect=AssertionError('live network forbidden')))
