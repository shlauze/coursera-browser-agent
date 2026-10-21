import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v2 import cli
from v2.control import Control
from v2.store import Store


class CliRecoveryTests(unittest.TestCase):
    def test_process_lock_blocks_second_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner.lock"
            with cli.single_runner_lock(path):
                with self.assertRaises(RuntimeError):
                    with cli.single_runner_lock(path):
                        pass

    def test_rejected_second_worker_cannot_stop_existing_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'state.sqlite3');control=Control(store)
            with patch.object(cli, 'DATA', root), cli.single_runner_lock(root/'runner.lock'):
                store.set_control_state('running')
                supervisor=cli.WorkerSupervisor(store,control)
                with patch.object(cli, '_logger'):
                    supervisor._worker(scan_only=False)
                self.assertIsInstance(supervisor.last_error,RuntimeError)
                self.assertEqual(control.get_state(),'running')
                self.assertNotIn('RUNNER_STOPPED',[e['code'] for e in store.recent_events(10)])

    def test_stale_control_state_is_recovered_only_when_lock_is_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            control = Control(store)
            store.set_control_state("paused")
            with patch.object(cli, "DATA", root):
                cli.WorkerSupervisor(store, control)
            self.assertEqual(control.get_state(), "stopped")
            self.assertEqual(store.recent_events(1)[0]["code"], "STALE_RUNNER_RECOVERED")


if __name__ == "__main__":
    unittest.main()
