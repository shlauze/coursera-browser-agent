import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v2 import credentials
from v2.groq_client import GroqClient
from v2.groq_limits import key_id, register_key, observe, key_status, state_key, limits_html
from v2.store import Store


SUCCESS = (200, {'X-RateLimit-Remaining-Tokens': '7000', 'X-RateLimit-Limit-Tokens': '8000'},
           {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]})


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'state.db')

    def test_independent_observations_do_not_invent_unused_quota_or_refresh(self):
        one = register_key(self.store, 'fixture-one', 'Primary')
        two = register_key(self.store, 'fixture-two', 'Secondary')
        observe(self.store, one, 'm', 429, {'x-ratelimit-remaining-tokens': '0',
            'authorization': 'PRIVATE', 'x-ratelimit-reset-tokens': '1m2.5s'},
            {'error': {'message': 'TPD Limit 200000 Used 199900 Requested 500 PRIVATE'}}, now=100, delay=60)
        rows = key_status(self.store, model='m', now=110)
        self.assertEqual([r['state'] for r in rows], ['waiting', 'unknown'])
        self.assertEqual(rows[0]['remaining_seconds'], 50)
        self.assertNotIn('headers', rows[1])
        self.assertEqual(key_status(self.store, model='m', now=161)[0]['state'], 'retry_due')
        observe(self.store, two, 'm', 200, {}, {}, now=170, delay=0)
        self.assertEqual(self.store.get_meta(state_key(one, 'm'))['retry_at'], 160)
        self.assertNotIn('PRIVATE', json.dumps(rows))
        self.assertNotIn('fixture', json.dumps(rows))
        self.assertEqual(key_status(self.store, model='different', now=170)[0]['state'], 'unknown')

    def test_restart_preserves_wait_then_resumes_same_key_without_rotation(self):
        model = 'openai/gpt-oss-120b'
        one = register_key(self.store, 'fixture-one', 'Primary')
        register_key(self.store, 'fixture-two', 'Secondary')
        now = [100.0]
        calls = []
        def sleep(seconds): now[0] += seconds
        def transport(key, payload):
            calls.append((key, now[0]))
            if len(calls) == 1:
                return 429, {'Retry-After': '3'}, {}
            return SUCCESS
        def stop():
            if self.store.get_meta(state_key(one, model)):
                raise RuntimeError('manual stop')
        client = GroqClient(self.store, stop, key_loader=lambda: 'fixture-one', transport=transport,
                            clock=lambda: now[0], sleep=sleep)
        with self.assertRaisesRegex(RuntimeError, 'manual stop'): client._request({})
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.get_meta(state_key(one, model))['retry_at'], 103)
        # Simulate an interrupted write of legacy wait metadata: per-key deadline still protects the request.
        self.store.set_meta('groq_retry_until', 0)
        self.store.set_meta('groq_wait', None)
        self.store.set_meta('answer_draft:fixture', {'text': 'preserved'})
        restarted = GroqClient(Store(self.store.path), key_loader=lambda: 'fixture-one',
                               transport=transport, clock=lambda: now[0], sleep=sleep)
        self.assertEqual(restarted._request({}), {'ok': True})
        self.assertEqual(calls, [('fixture-one', 100), ('fixture-one', 103)])
        self.assertEqual(self.store.get_meta('answer_draft:fixture'), {'text': 'preserved'})
        self.assertEqual(key_status(self.store, now=104)[1]['state'], 'unknown')

    def test_http_date_retry_after_is_not_shortened(self):
        from email.utils import format_datetime
        from datetime import datetime, timezone, timedelta
        from v2.groq_client import retry_seconds
        future = datetime.now(timezone.utc) + timedelta(seconds=90)
        self.assertGreater(retry_seconds({'retry-after': format_datetime(future)}, 2), 88)

    def test_secrets_and_invalid_headers_never_enter_quota_panel(self):
        identity = register_key(self.store, 'gsk_FIXTURE_SECRET', 'Name')
        observe(self.store, identity, 'openai/gpt-oss-120b', 401,
                {'x-ratelimit-remaining-tokens': 'gsk_FIXTURE_SECRET', 'set-cookie': 'PRIVATE'}, {}, now=1, delay=0)
        html = limits_html(key_status(self.store, now=2))
        self.assertNotIn('SECRET', html)
        self.assertNotIn('PRIVATE', html)
        self.assertEqual(key_status(self.store, now=2)[0]['state'], 'access_error')
        with self.assertRaises(ValueError): register_key(self.store, 'x', 'gsk_ASECRET')

    def test_named_registry_is_encrypted_and_does_not_replace_active_key(self):
        path = Path(self.tmp.name) / '.groq-key.dpapi'
        def protect(data, *, decrypt=False):
            return data[4:][::-1] if decrypt else b'ENC:' + data[::-1]
        with patch.object(credentials, 'KEY_FILE', path), patch.object(credentials, '_protect', side_effect=protect):
            credentials.save_key('fixture-active')
            credentials.save_monitored_key('Other', 'fixture-other', self.store)
            credentials.save_monitored_key('Third', 'fixture-third', self.store)
            self.assertEqual(credentials.load_key(), 'fixture-active')
            encrypted = path.with_name('.groq-monitor-keys.dpapi').read_bytes()
            self.assertNotIn(b'fixture-other', encrypted)
            self.assertEqual(len(json.loads(protect(encrypted, decrypt=True))), 2)
            self.assertEqual(len(key_status(self.store)), 2)


if __name__ == '__main__':
    unittest.main()
