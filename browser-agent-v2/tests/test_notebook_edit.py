import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from v2.handlers import HandlerError
from v2.notebooks import NotebookExecutor
from v2.store import Store


class NotebookEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'state.db')
        self.store.get_action = Mock(return_value={'course_id': 'fixture-course'})
        self.now = 0.0
        timer = patch('v2.notebooks.time.monotonic', side_effect=lambda: self.now)
        timer.start()
        self.addCleanup(timer.stop)
        self.frame = Mock()
        self.frame.page.wait_for_timeout.side_effect = self.wait
        self.cell = {'cell_type': 'code', 'source': 'def solve(x):\n    # YOUR CODE HERE\n    pass'}
        self.snapshot = {'signature': 'unchanged', 'document': {'cells': [self.cell]}}
        self.groq = Mock()
        self.groq.notebook_patch.return_value = {'index': 0, 'code': 'def solve(x):\n    return x + 1'}
        self.busy = 0
        self.edit_checks = 0
        self.edits = 0
        self.unknown = False
        self.frame.evaluate.side_effect = self.bridge

    def wait(self, ms):
        self.now += ms / 1000

    def bridge(self, script, request):
        if request['action'] == 'edit':
            self.edit_checks += 1
            if self.unknown:
                raise RuntimeError('write response lost')
            if self.edit_checks <= self.busy:
                return {'error': 'Kernel must be idle before editing', 'edit_started': False}
            self.edits += 1
        return self.snapshot if request['action'] == 'snapshot' else {'ok': True}

    def executor(self, action=1):
        executor = NotebookExecutor(self.frame, self.groq, self.store, action, lambda: None)
        executor.path = 'fixture.ipynb'
        return executor

    def test_busy_edit_waits_then_writes_once_without_more_generation(self):
        self.busy = 3
        self.executor().patch(self.snapshot, {0: self.cell}, target=0)
        self.assertEqual(self.edits, 1)
        self.assertEqual(self.edit_checks, 4)
        self.groq.notebook_patch.assert_called_once()

    def test_busy_timeout_reuses_draft_across_new_action(self):
        self.busy = 1000
        with self.assertRaisesRegex(HandlerError, 'validated draft saved'):
            self.executor().patch(self.snapshot, {0: self.cell}, target=0)
        self.assertEqual(self.edits, 0)
        self.assertEqual(self.now, 60)
        self.busy = 0
        self.executor(action=2).patch(self.snapshot, {0: self.cell}, target=0)
        self.groq.notebook_patch.assert_called_once()
        self.assertEqual(self.edits, 1)

    def test_changed_feedback_does_not_reuse_old_draft(self):
        self.busy = 1000
        with self.assertRaises(HandlerError):
            self.executor().patch(self.snapshot, {0: self.cell}, target=0)
        self.busy = 0
        self.executor(action=2).patch(self.snapshot, {0: self.cell}, target=0,
                                     failure={'index': 0, 'reply': {'ename': 'AssertionError'}})
        self.assertEqual(self.groq.notebook_patch.call_count, 2)

    def test_changed_notebook_does_not_reuse_old_draft(self):
        self.busy = 1000
        with self.assertRaises(HandlerError):
            self.executor().patch(self.snapshot, {0: self.cell}, target=0)
        self.busy = 0
        self.snapshot['signature'] = 'changed-by-user'
        self.executor(action=2).patch(self.snapshot, {0: self.cell}, target=0)
        self.assertEqual(self.groq.notebook_patch.call_count, 2)

    def test_unknown_write_error_is_not_retried(self):
        self.unknown = True
        with self.assertRaisesRegex(RuntimeError, 'response lost'):
            self.executor().patch(self.snapshot, {0: self.cell}, target=0)
        self.assertEqual(self.edit_checks, 1)
        self.assertEqual(self.edits, 0)


if __name__ == '__main__':
    unittest.main()
