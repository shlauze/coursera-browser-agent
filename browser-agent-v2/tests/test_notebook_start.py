import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from v2.handlers import HandlerError
from v2.notebooks import NotebookExecutor
from v2.store import Store


class NotebookStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'state.db')
        self.now = 100.0
        timer = patch('v2.notebooks.time.monotonic', side_effect=lambda: self.now)
        timer.start()
        self.addCleanup(timer.stop)
        self.frame = Mock()
        self.frame.page.wait_for_timeout.side_effect = self.wait
        self.executor = NotebookExecutor(self.frame, Mock(), self.store, 1, lambda: None)
        self.executor.execution_key = 'lab_execution:fixture'
        self.executor.path = 'fixture.ipynb'
        self.snapshot = {'signature': 'fixed'}
        self.runs = 0
        self.sent = 0

    def wait(self, ms):
        self.now += ms / 1000

    def bridge(self, busy=1, unknown=False):
        def evaluate(script, request):
            if request['action'] == 'run':
                self.runs += 1
                if unknown:
                    raise RuntimeError('connection lost after sending request')
                if self.runs <= busy:
                    return {'error': 'Kernel is already busy; will not duplicate execution', 'execution_started': False}
                self.sent += 1
                return {'ok': True}
            if request['action'] == 'poll':
                return {'reply': {'status': 'ok'}, 'idle': True, 'kernel': 'idle'}
            raise AssertionError(request['action'])
        self.frame.evaluate.side_effect = evaluate

    def test_busy_before_start_waits_and_executes_once(self):
        self.bridge(busy=3)
        result = self.executor.run_cell(self.snapshot, 0)
        self.assertEqual(result['reply']['status'], 'ok')
        self.assertEqual(self.sent, 1)
        self.assertEqual(self.runs, 4)
        self.assertEqual(self.store.get_meta(self.executor.execution_key)['status'], 'finished')

    def test_busy_timeout_records_not_started(self):
        self.bridge(busy=1000)
        with self.assertRaisesRegex(HandlerError, 'no cell was started'):
            self.executor.run_cell(self.snapshot, 0)
        self.assertEqual(self.now, 160)
        self.assertEqual(self.sent, 0)
        self.assertEqual(self.store.get_meta(self.executor.execution_key)['status'], 'not_started')

    def test_uncertain_transport_is_not_retried_or_cleared(self):
        self.bridge(unknown=True)
        with self.assertRaisesRegex(RuntimeError, 'connection lost'):
            self.executor.run_cell(self.snapshot, 0)
        self.assertEqual(self.runs, 1)
        self.assertEqual(self.store.get_meta(self.executor.execution_key)['status'], 'starting')

    def test_stop_during_idle_wait_does_not_send_code(self):
        class Stop(Exception):
            pass
        self.bridge(busy=5)
        self.executor.checkpoint = Mock(side_effect=[None, Stop()])
        with self.assertRaises(Stop):
            self.executor.run_cell(self.snapshot, 0)
        self.assertEqual(self.sent, 0)
        self.assertEqual(self.store.get_meta(self.executor.execution_key)['status'], 'not_started')


if __name__ == '__main__':
    unittest.main()
