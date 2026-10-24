"""Groq is the sole provider, even with older persisted preferences."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from v2 import providers
from v2.control import Control
from v2.dashboard import Dashboard, runtime_status
from v2.groq_client import GroqClient, GroqError
from v2.store import Store


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'state.db')
        self.answer = {'text': 'fixture'}
        self.success = (200, {}, {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.answer)}}]})

    def test_old_local_and_auto_preferences_cannot_enable_other_provider(self):
        for mode in ('ollama', 'auto'):
            self.store.set_meta('provider_settings', {'mode': mode, 'ollama_model': 'old:latest'})
            self.store.set_meta('provider_pending', {'mode': 'ollama'})
            self.store.set_meta('groq_retry_until', 102)
            now = [100]
            def sleep(seconds): now[0] += seconds
            transport = Mock(return_value=self.success)
            client = GroqClient(self.store, key_loader=lambda: 'fixture-key', transport=transport,
                                clock=lambda: now[0], sleep=sleep)
            with patch('urllib.request.urlopen', side_effect=AssertionError('unexpected local request')):
                self.assertEqual(client._request({}), self.answer)
            self.assertEqual(now[0], 102)
            transport.assert_called_once()
            self.assertEqual(providers.settings(self.store), {'mode': 'groq'})
            self.assertIsNone(self.store.get_meta('provider_pending'))
            self.assertEqual(self.store.get_meta('provider_active')['provider'], 'groq')

    def test_rate_limit_and_temporary_error_wait_without_fallback(self):
        for status in (429, 503):
            now = [100]
            calls = []
            def transport(key, payload):
                calls.append(now[0])
                return (status, {'Retry-After': '3'}, {}) if len(calls) == 1 else self.success
            def sleep(seconds): now[0] += seconds
            client = GroqClient(self.store, key_loader=lambda: 'fixture', transport=transport,
                                clock=lambda: now[0], sleep=sleep)
            self.assertEqual(client._request({}), self.answer)
            self.assertEqual(calls, [100, 103])
            self.assertFalse(hasattr(client, '_local_request'))
            self.assertFalse(hasattr(providers, 'ollama_transport'))

    def test_migration_preserves_progress_and_retry_after(self):
        self.store.upsert_course('existing', 'Existing', status='started')
        self.store.set_meta('groq_retry_until', 1234)
        self.store.set_meta('provider_settings', {'mode': 'ollama'})
        self.store.set_control_state('paused')
        providers.apply_pending_settings(self.store)
        self.assertEqual(self.store.get_meta('groq_retry_until'), 1234)
        self.assertEqual(self.store.get_control_state(), 'paused')
        self.assertIsNotNone(self.store.get_course('existing'))
        for mode in ('ollama', 'auto'):
            with self.assertRaises(ValueError): providers.save_settings(self.store, mode, defer=True)

    def test_saved_key_takes_priority_over_old_environment_key(self):
        with patch('v2.credentials.load_key', return_value='saved-fixture'), patch.dict('os.environ', {'GROQ_API_KEY': 'old-fixture'}):
            self.assertEqual(GroqClient(self.store)._key(), 'saved-fixture')
        with patch('v2.credentials.load_key', return_value=''), patch.dict('os.environ', {'GROQ_API_KEY': 'env-fixture'}):
            self.assertEqual(GroqClient(self.store)._key(), 'env-fixture')

    def test_auth_error_does_not_try_a_different_provider(self):
        transport = Mock(return_value=(401, {}, {}))
        with self.assertRaisesRegex(GroqError, '401'):
            GroqClient(self.store, key_loader=lambda: 'fixture', transport=transport)._request({})
        transport.assert_called_once()

    def test_panel_has_no_local_controls_or_local_health_requests(self):
        self.store.set_meta('provider_active', {'provider': 'ollama', 'phase': 'generating'})
        panel = Dashboard(self.store, Control(self.store))
        with patch('urllib.request.urlopen', side_effect=AssertionError('no local probe')):
            html = panel._providers_page()
        self.assertNotIn('Ollama', html)
        self.assertNotIn('type="radio"', html)
        self.assertIn('Groq', html)
        self.assertEqual(runtime_status(self.store)['provider'], {})

    def test_http_rejects_old_modes_and_keeps_origin_and_csrf_checks(self):
        panel = Dashboard(self.store, Control(self.store), port=0)
        server = panel.build_server()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{panel.port}'
            cases = [('ollama', panel.csrf_token, base, 409), ('auto', panel.csrf_token, base, 409),
                     ('groq', 'wrong', base, 403), ('groq', panel.csrf_token, 'null', 403),
                     ('groq', panel.csrf_token, 'https://foreign.example', 403)]
            for mode, csrf, origin, status in cases:
                request = Request(base + '/action/providers', data=urlencode({'mode': mode, 'csrf': csrf}).encode(),
                                  headers={'Origin': origin, 'Content-Type': 'application/x-www-form-urlencoded'})
                with self.assertRaises(HTTPError) as error: urlopen(request, timeout=3)
                self.assertEqual(error.exception.code, status)
                error.exception.close()
            self.assertEqual(self.store.get_control_state(), 'stopped')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
