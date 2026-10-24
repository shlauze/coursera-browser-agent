import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from v2.groq_client import GroqClient, GroqError, question_key
from v2.providers import settings, save_settings, ollama_transport, validate_schema, apply_pending_settings, cancel_pending_settings
from v2.dashboard import Dashboard, runtime_status, runtime_html
from v2.control import Control
from v2.store import Store


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'state.sqlite3')
        self.question = {'text': 'What is 2 plus 2?', 'kind': 'number'}
        self.answer = {'cannot_answer': False, 'text': '4'}
        self.local = Mock(return_value=self.answer)

    def configure(self, mode='auto'):
        self.store.set_meta('provider_settings', {'mode': mode, 'ollama_model': 'local:latest', 'num_ctx': 8192})

    def client(self, transport, **kw):
        return GroqClient(self.store, key_loader=lambda: 'SECRET', transport=transport,
                          local_transport=self.local, clock=lambda: 100, **kw)

    def test_saved_cooldown_uses_local_without_groq_or_key(self):
        self.configure()
        self.store.set_meta('groq_retry_until', 250)
        remote = Mock(side_effect=AssertionError('No remote request'))
        client = self.client(remote)
        client.key_loader = Mock(side_effect=AssertionError('No key read'))
        self.assertEqual(client.answer(self.question), self.answer)
        self.assertEqual(self.store.get_meta('groq_retry_until'), 250)
        self.assertIsNotNone(self.store.get_meta('answer_draft:' + question_key(self.question)))
        self.assertEqual(client.answer(self.question), self.answer)
        self.local.assert_called_once()
        with self.store._connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM groq_usage').fetchone()[0], 0)

    def test_429_switch_and_next_request_returns_to_groq_after_deadline(self):
        self.configure()
        remote = Mock(side_effect=[(429, {'Retry-After': '60'}, {}),
            (200, {}, {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.answer)}}]})])
        client = self.client(remote)
        payload = {'model': client.model}
        self.assertEqual(client._request(payload), self.answer)
        self.assertEqual(self.store.get_meta('groq_retry_until'), 160)
        self.assertEqual(client._request(payload), self.answer)
        self.assertEqual(remote.call_count, 1)
        client.clock = lambda: 161
        self.assertEqual(client._request(payload), self.answer)
        self.assertEqual(remote.call_count, 2)
        self.assertEqual(self.store.get_meta('provider_active')['provider'], 'groq')

    def test_network_retry_after_preserved_during_fallback(self):
        self.configure()
        remote = Mock(return_value=(503, {'Retry-After': '80'}, {}))
        client = self.client(remote)
        client._request({})
        client._request({})
        self.assertEqual(remote.call_count, 1)
        self.assertEqual(self.store.get_meta('groq_wait')['retry_at'], 180)

    def test_short_limit_waits_without_slow_local_generation(self):
        self.configure()
        remote = Mock(side_effect=[(429, {'Retry-After': '5'}, {}),
            (200, {}, {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.answer)}}]})])
        elapsed = [100.0]
        client = self.client(remote, sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
        client.clock = lambda: elapsed[0]
        self.assertEqual(client._request({}), self.answer)
        self.assertEqual(elapsed[0], 105)
        self.local.assert_not_called()

    def test_failed_local_generation_does_not_repeat_expired_cooldown(self):
        self.configure()
        remote = Mock(side_effect=[(429, {'Retry-After': '60'}, {}),
            (200, {}, {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.answer)}}]})])
        elapsed = [100.0]
        def local(*args):
            elapsed[0] += 180
            raise GroqError('local timeout')
        self.local.side_effect = local
        sleep = Mock(side_effect=AssertionError('Cooldown already expired'))
        client = self.client(remote, sleep=sleep)
        client.clock = lambda: elapsed[0]
        self.assertEqual(client._request({}), self.answer)
        self.assertEqual(remote.call_count, 2)
        sleep.assert_not_called()

    def test_local_failure_keeps_groq_wait_and_honors_stop(self):
        self.configure()
        self.local.side_effect = GroqError('unavailable')
        self.store.set_meta('groq_retry_until', 200)
        remote = Mock()
        client = self.client(remote)
        count = [0]
        def checkpoint():
            count[0] += 1
            if count[0] > 1:
                raise RuntimeError('user stop')
        client.checkpoint = checkpoint
        with self.assertRaisesRegex(RuntimeError, 'user stop'):
            client.answer(self.question)
        remote.assert_not_called()
        self.assertEqual(self.store.get_meta('groq_retry_until'), 200)

    def test_manual_local_never_falls_back_to_paid_remote(self):
        self.configure('ollama')
        self.local.side_effect = GroqError('local failed')
        remote = Mock()
        with self.assertRaisesRegex(GroqError, 'local failed'):
            self.client(remote).answer(self.question)
        remote.assert_not_called()

    def test_authentication_error_does_not_trigger_fallback(self):
        self.configure()
        with self.assertRaisesRegex(GroqError, 'access error'):
            self.client(Mock(return_value=(401, {}, {}))).answer(self.question)
        self.local.assert_not_called()

    def test_invalid_local_answer_is_not_saved(self):
        self.configure('ollama')
        self.local.return_value = {'cannot_answer': False, 'text': 'NaN'}
        with self.assertRaises(GroqError):
            self.client(Mock()).answer(self.question)
        with self.store._connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM metadata WHERE key LIKE 'answer_draft:%'").fetchone()[0], 0)

    def test_schema_rejects_extra_fields_wrong_types_and_unknown_options(self):
        schema = {'type': 'object', 'properties': {'selected': {'type': 'array', 'items': {'type': 'string', 'enum': ['a']}}},
                  'required': ['selected'], 'additionalProperties': False}
        validate_schema({'selected': ['a']}, schema)
        for value in [{}, {'selected': 'a'}, {'selected': ['b']}, {'selected': ['a'], 'extra': True}]:
            with self.assertRaises(ValueError):
                validate_schema(value, schema)

    def test_settings_require_stopped_worker_and_installed_model(self):
        self.assertEqual(settings(self.store)['mode'], 'groq')
        with patch('v2.providers.models', return_value=['local:latest']):
            save_settings(self.store, 'auto', 'local:latest', 8192)
            with self.assertRaises(ValueError):
                save_settings(self.store, 'auto', 'missing', 8192)
            self.store.set_control_state('running')
            with self.assertRaises(ValueError):
                save_settings(self.store, 'groq', '', 8192)
        self.assertEqual(settings(self.store)['mode'], 'auto')

    def test_provider_page_has_no_refresh_and_shows_local_models(self):
        self.configure()
        panel = Dashboard(self.store, Control(self.store))
        with patch('v2.dashboard.ollama_models', return_value=['local:latest']):
            html = panel._providers_page()
        self.assertIn('local:latest', html)
        self.assertIn('name="csrf"', html)
        self.assertNotIn('http-equiv="refresh"', html)

    def test_running_selection_applies_only_on_next_request(self):
        self.configure('groq')
        self.store.set_control_state('running')
        def remote(*args):
            with patch('v2.providers.models', return_value=['local:latest']):
                save_settings(self.store, 'ollama', 'local:latest', 8192, defer=True)
            self.assertEqual(settings(self.store)['mode'], 'groq')
            return (200, {}, {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.answer)}}]})
        client = self.client(Mock(side_effect=remote))
        self.assertEqual(client._request({}), self.answer)
        self.local.assert_not_called()
        self.assertIsNotNone(self.store.get_meta('provider_pending'))
        self.assertEqual(client._request({}), self.answer)
        self.local.assert_called_once()
        self.assertIsNone(self.store.get_meta('provider_pending'))
        self.assertEqual(settings(self.store)['mode'], 'ollama')

    def test_queue_latest_selection_cancel_and_pause_preserved(self):
        self.configure('groq')
        self.store.set_control_state('paused')
        with patch('v2.providers.models', return_value=['local:latest']):
            save_settings(self.store, 'auto', 'local:latest', 8192, defer=True)
            save_settings(self.store, 'ollama', 'local:latest', 16384, defer=True)
        self.assertEqual(self.store.get_meta('provider_pending')['num_ctx'], 16384)
        self.assertEqual(self.store.get_control_state(), 'paused')
        self.assertEqual(settings(self.store)['mode'], 'groq')
        cancel_pending_settings(self.store)
        self.assertFalse(apply_pending_settings(self.store))
        self.assertEqual(settings(self.store)['mode'], 'groq')

    def test_stopped_save_clears_pending_without_starting(self):
        self.store.set_meta('provider_pending', {'mode': 'ollama'})
        save_settings(self.store, 'groq', '', 8192, defer=True)
        self.assertIsNone(self.store.get_meta('provider_pending'))
        self.assertEqual(self.store.get_control_state(), 'stopped')

    def test_provider_form_escapes_errors_and_retains_attempted_model(self):
        panel = Dashboard(self.store, Control(self.store))
        with patch('v2.dashboard.ollama_models', side_effect=OSError()):
            html = panel._providers_page(error='<script>bad</script>', form_values={
                'mode': 'auto', 'ollama_model': 'missing:latest', 'num_ctx': '16384'})
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('value="missing:latest" selected', html)
        self.assertIn('value="16384" selected', html)
        self.assertIn('Ollama не отвечает', html)

    def test_local_generation_is_not_displayed_as_groq_wait(self):
        self.configure()
        self.store.set_control_state('running')
        self.store.set_meta('groq_retry_until', 200)
        self.store.set_meta('provider_active', {'provider': 'ollama', 'model': 'local:latest'})
        status = runtime_status(self.store, now=100)
        self.assertFalse(status['waiting'])
        self.assertIn('локальная генерация', runtime_html(status))

    def test_local_transport_never_sends_key_and_validates_response(self):
        client = self.client(Mock())
        payload = {'messages': [{'role': 'user', 'content': '2+2?'}], 'max_completion_tokens': 64,
                   'response_format': client._format('answer', {'cannot_answer': {'type': 'boolean'}, 'text': {'type': 'string'}})}
        body = {'done': True, 'done_reason': 'stop', 'message': {'content': json.dumps(self.answer)}}
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(body).encode()
        with patch('v2.providers.models', return_value=['local:latest']), patch('v2.providers.urllib.request.urlopen', return_value=response) as send:
            self.assertEqual(ollama_transport({'ollama_model': 'local:latest', 'num_ctx': 8192}, payload, lambda: None), self.answer)
            req = send.call_args.args[0]
            self.assertEqual(send.call_args.kwargs['timeout'], 900)
            self.assertEqual(req.full_url, 'http://127.0.0.1:11434/api/chat')
            self.assertNotIn('Authorization', req.headers)
            self.assertEqual(json.loads(req.data)['format'], payload['response_format']['json_schema']['schema'])
            with self.assertRaisesRegex(GroqError, 'context budget'):
                ollama_transport({'ollama_model': 'local:latest', 'num_ctx': 10}, payload, lambda: None)

    def test_stop_before_local_generation_never_sends_request(self):
        payload = {'messages': [{'role': 'user', 'content': '2+2?'}]}
        def stop():
            raise RuntimeError('user stop')
        with patch('v2.providers.models', return_value=['local:latest']), patch('v2.providers.urllib.request.urlopen') as send:
            with self.assertRaisesRegex(RuntimeError, 'user stop'):
                ollama_transport({'ollama_model': 'local:latest', 'num_ctx': 8192}, payload, stop)
            send.assert_not_called()

    def test_stop_interrupts_slow_local_wait_without_accepting_late_response(self):
        import threading
        started, release = threading.Event(), threading.Event()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({'done': True, 'done_reason': 'stop',
            'message': {'content': json.dumps(self.answer)}}).encode()
        def send(*args, **kwargs):
            started.set()
            release.wait(3)
            return response
        checks = [0]
        def checkpoint():
            checks[0] += 1
            if checks[0] > 1:
                self.assertTrue(started.wait(2))
                raise RuntimeError('user stop')
        try:
            with patch('v2.providers.models', return_value=['local:latest']), patch('v2.providers.urllib.request.urlopen', side_effect=send):
                with self.assertRaisesRegex(RuntimeError, 'user stop'):
                    ollama_transport({'ollama_model': 'local:latest', 'num_ctx': 8192},
                        {'messages': [{'role': 'user', 'content': '2+2?'}]}, checkpoint)
        finally:
            release.set()

    def test_provider_settings_http_requires_csrf_and_preserves_progress(self):
        import threading
        from urllib.request import urlopen, Request
        from urllib.error import HTTPError
        from urllib.parse import urlencode
        self.store.upsert_course('saved', 'Existing course', status='started')
        panel = Dashboard(self.store, Control(self.store), port=0)
        server = panel.build_server()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{panel.port}/action/providers'
            def post(token):
                return urlopen(Request(url, data=urlencode({'csrf': token, 'mode': 'auto',
                    'model': 'local:latest', 'num_ctx': 8192}).encode(),
                    headers={'Content-Type': 'application/x-www-form-urlencoded'}), timeout=3)
            with patch('v2.providers.models', return_value=['local:latest']):
                with self.assertRaises(HTTPError) as exc:
                    post('wrong')
                self.assertEqual(exc.exception.code, 403)
                exc.exception.close()
                self.assertEqual(settings(self.store)['mode'], 'groq')
                with post(panel.csrf_token) as response:
                    self.assertEqual(response.status, 200)
                self.assertEqual(settings(self.store)['mode'], 'auto')
                self.assertIsNotNone(self.store.get_course('saved'))
                self.store.set_control_state('running')
                with post(panel.csrf_token) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn('/providers', response.url)
                self.assertEqual(self.store.get_meta('provider_pending')['mode'], 'auto')
                self.assertEqual(self.store.get_control_state(), 'running')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_chrome_form_origin_and_cross_origin_protection(self):
        import threading
        from playwright.sync_api import sync_playwright
        from urllib.request import Request, urlopen
        from urllib.parse import urlencode
        from urllib.error import HTTPError
        self.store.set_control_state('running')
        panel = Dashboard(self.store, Control(self.store), port=0)
        server = panel.build_server()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{panel.port}'
        origins = []
        try:
            with patch('v2.dashboard.ollama_models', return_value=[]), sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, channel='chrome')
                try:
                    page = browser.new_page()
                    page.on('request', lambda request: origins.append(request.headers.get('origin')) if request.method == 'POST' else None)
                    page.goto(base + '/providers')
                    with page.expect_response(lambda r: r.url.endswith('/action/providers') and r.request.method == 'POST') as response:
                        page.locator('button[type=submit]').click()
                    self.assertEqual(response.value.status, 303)
                    page.wait_for_url('**/providers?notice=*')
                    self.assertEqual(origins, [base])
                    self.assertEqual(self.store.get_meta('provider_pending')['mode'], 'groq')
                    self.assertEqual(self.store.get_control_state(), 'running')
                    self.assertIn('Ожидает применения', page.locator('body').inner_text())
                finally:
                    browser.close()
            for origin in ('null', 'https://example.org', 'http://127.0.0.1:1'):
                request = Request(base + '/action/providers/cancel',
                    data=urlencode({'csrf': panel.csrf_token}).encode(),
                    headers={'Origin': origin, 'Content-Type': 'application/x-www-form-urlencoded'})
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=3)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
            self.assertIsNotNone(self.store.get_meta('provider_pending'))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
