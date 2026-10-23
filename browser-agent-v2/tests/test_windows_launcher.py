import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import windows_launcher as app


class PortableLauncherTests(unittest.TestCase):
    def test_persistent_paths_point_outside_bundle_and_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / 'portable').resolve()
            with patch.multiple(app.cli, ROOT=None, DATA=None, PROFILE=None, LOGS=None), patch.object(app.credentials, 'KEY_FILE', None):
                app.configure_paths(root)
                self.assertEqual(app.cli.DATA, root / 'data')
                self.assertEqual(app.cli.PROFILE, root / 'profile')
                self.assertEqual(app.cli.LOGS, root / 'logs')
                self.assertEqual(app.credentials.KEY_FILE, root / 'data/.groq-key.dpapi')
                self.assertFalse(root.exists())

    def test_frozen_cli_uses_executable_parent_without_prompt_or_autostart(self):
        with patch.object(app.sys, 'frozen', True, create=True), patch.object(app.sys, 'executable', 'C:/Portable/agent.exe'), patch.object(app, 'configure_paths') as paths, patch.object(app.cli, 'main', return_value=0) as run, patch.object(app.getpass, 'getpass') as prompt:
            self.assertEqual(app.main(['status']), 0)
            paths.assert_called_once_with(Path('C:/Portable'))
            run.assert_called_once_with(['status'])
            prompt.assert_not_called()

    def test_bound_panel_opens_browser_without_starting_worker(self):
        from v2.store import Store
        from v2.control import Control
        with tempfile.TemporaryDirectory() as temp, patch.object(app.webbrowser, 'open') as opened:
            store = Store(Path(temp) / 'state.sqlite3')
            panel = app.DesktopDashboard(store, Control(store), port=0)
            with panel.build_server() as server:
                opened.assert_called_once_with(f'http://127.0.0.1:{server.server_port}/')
                self.assertEqual(store.get_control_state(), 'stopped')


if __name__ == '__main__':
    unittest.main()
