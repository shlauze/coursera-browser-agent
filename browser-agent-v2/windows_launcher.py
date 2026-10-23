"""Portable Windows entry point; bundled resources and persistent data stay separate."""
from pathlib import Path
import getpass
import json
import os
import sys
import tempfile
import threading
import urllib.request
import webbrowser

from v2 import __version__, cli, credentials


def configure_paths(root):
    """Keep progress beside the EXE, never in PyInstaller's temporary directory."""
    root = Path(root).resolve()
    cli.ROOT = root
    cli.DATA = root / 'data'
    cli.PROFILE = root / 'profile'
    cli.LOGS = root / 'logs'
    credentials.KEY_FILE = cli.DATA / '.groq-key.dpapi'


class DesktopDashboard(cli.Dashboard):
    def build_server(self):
        server = super().build_server()
        # Only open the page after this process has successfully bound its port.
        try:
            webbrowser.open(f'http://127.0.0.1:{server.server_port}/')
        except webbrowser.Error:
            pass  # The URL is also printed in the console.
        return server


def self_test():
    """Offline packaged-runtime check: no Coursera/Groq traffic or browser tabs."""
    from playwright.sync_api import sync_playwright
    from v2.assessments import DOM
    from v2.notebooks import BRIDGE
    from v2.store import Store
    from v2.control import Control
    from v2.dashboard import Dashboard
    assert DOM and BRIDGE
    with tempfile.TemporaryDirectory(prefix='coursera-exe-check-') as folder:
        store = Store(Path(folder) / 'state.sqlite3')
        store.set_meta('packaged_check', True)
        assert store.get_meta('packaged_check') is True
        server = Dashboard(store, Control(store), port=0).build_server()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/status', timeout=5) as response:
                assert json.load(response)['version'] == __version__
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        if os.name == 'nt':
            sample = b'packaged-runtime-check-not-a-key'
            assert credentials._protect(credentials._protect(sample), decrypt=True) == sample
        with sync_playwright() as playwright:
            assert playwright.chromium.name == 'chromium'
    print(json.dumps({'version': __version__, 'frozen': bool(getattr(sys, 'frozen', False)),
                      'sqlite': 'ok', 'dashboard': 'ok', 'resources': 'ok',
                      'playwright_driver': 'ok', 'credential_encryption': 'ok'}))
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    if args == ['--version']:
        print(__version__)
        return 0
    if args == ['--self-test']:
        return self_test()
    root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    configure_paths(root)
    if not args:
        print(f'Coursera Browser Agent {__version__}')
        print(f'Progress and settings: {root}')
        print('Keep this window open. Ctrl+C stops the panel gracefully.')
        if not os.environ.get('GROQ_API_KEY') and not credentials.load_key():
            key = getpass.getpass('Groq API key (hidden; Enter to configure later): ').strip()
            if key:
                credentials.save_key(key)
                print('Key encrypted for this Windows user.')
        cli.Dashboard = DesktopDashboard
    return cli.main(args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f'Cannot start: {error}', file=sys.stderr)
        if not sys.argv[1:] and sys.stdin.isatty():
            input('Press Enter to close...')
        raise SystemExit(1)
