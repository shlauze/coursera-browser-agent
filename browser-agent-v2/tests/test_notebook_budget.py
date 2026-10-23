"""Provider waits must not discard valid lab patches or weaken execution limits."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from v2.groq_client import GroqClient
from v2.handlers import HandlerError
from v2.notebooks import NotebookExecutor
from v2.store import Store


class NotebookBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'state.db')
        self.now = 100.0
        self.timer = patch('v2.notebooks.time.monotonic', side_effect=lambda: self.now)
        self.timer.start()
        self.addCleanup(self.timer.stop)
        self.cell = {'cell_type': 'code', 'source': 'def solve(x):\n    # YOUR CODE HERE\n    pass'}
        self.snapshot = {'signature': 'original', 'document': {'cells': [self.cell]}}
        self.answer = {'index': 0, 'cannot_complete': False, 'code': 'def solve(x):\n    return x + 1'}

    def advance(self, seconds):
        self.now += seconds

    def executor(self, groq, checkpoint=lambda: None):
        result = NotebookExecutor(Mock(), groq, self.store, 1, checkpoint, total_timeout=2)
        result.call = Mock(return_value=self.snapshot)
        return result

    def test_long_provider_wait_saves_response_but_active_budget_still_expires(self):
        groq = Mock()
        def response(_):
            self.advance(7200)
            return self.answer
        groq.notebook_patch.side_effect = response
        executor = self.executor(groq)
        executor.patch(self.snapshot, {0: self.cell}, target=0)
        self.assertEqual([c.args[0] for c in executor.call.call_args_list], ['edit', 'save', 'snapshot'])
        groq.notebook_patch.assert_called_once()
        self.advance(3)
        with self.assertRaisesRegex(HandlerError, 'time budget exhausted'):
            executor.check()

    def test_retry_after_is_fully_observed_without_consuming_execution_budget(self):
        request_times = []
        def transport(key, payload):
            request_times.append(self.now)
            if len(request_times) == 1:
                return 429, {'retry-after': '6'}, {'error': {'message': 'TPD limit'}}
            return 200, {}, {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.answer)}}]}
        checkpoint = Mock()
        groq = GroqClient(self.store, checkpoint, key_loader=lambda: 'fixture', transport=transport,
                          clock=lambda: self.now, sleep=self.advance)
        executor = self.executor(groq, checkpoint)
        executor.patch(self.snapshot, {0: self.cell}, target=0)
        self.assertEqual(request_times, [100, 106])
        self.assertGreaterEqual(checkpoint.call_count, 8)
        self.assertEqual(executor.deadline - self.now, 2)
        self.assertEqual([c.args[0] for c in executor.call.call_args_list], ['edit', 'save', 'snapshot'])

    def test_stop_during_provider_wait_propagates_without_edit(self):
        class Stop(Exception):
            pass
        groq = Mock()
        def stopped(_):
            self.advance(30)
            raise Stop()
        groq.notebook_patch.side_effect = stopped
        executor = self.executor(groq)
        with self.assertRaises(Stop):
            executor.patch(self.snapshot, {0: self.cell}, target=0)
        executor.call.assert_not_called()

    def test_stop_after_response_is_checked_before_edit(self):
        class Stop(Exception):
            pass
        checkpoint = Mock(side_effect=[None, Stop()])
        executor = self.executor(Mock(notebook_patch=Mock(return_value=self.answer)), checkpoint)
        with self.assertRaises(Stop):
            executor.patch(self.snapshot, {0: self.cell}, target=0)
        executor.call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
