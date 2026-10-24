"""Exercise the real HTTP transport against a local Ollama-shaped server."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest
from unittest.mock import patch

from v2.groq_client import GroqError, TruncatedResponse
from v2.providers import ollama_transport


class OllamaStreamTests(unittest.TestCase):
    config = {'ollama_model': 'local:latest', 'num_ctx': 8192}
    payload = {'messages': [{'role': 'user', 'content': '2+2?'}],
               'max_completion_tokens': 64, 'response_format': {'json_schema': {'schema': {
                   'type': 'object', 'properties': {'text': {'type': 'string'}},
                   'required': ['text'], 'additionalProperties': False}}}}

    def serve(self, script):
        test = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                test.headers = dict(self.headers)
                test.request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                test.path = self.path
                script(self)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        real_connection = http.client.HTTPConnection
        patcher = patch('v2.providers.http.client.HTTPConnection',
                        side_effect=lambda *args, **kw: real_connection('127.0.0.1', server.server_port, **kw))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        models = patch('v2.providers.models', return_value=['local:latest'])
        models.start()
        self.addCleanup(models.stop)

    def stream(self, chunks, delay=0):
        def script(handler):
            handler.send_response(200)
            handler.end_headers()
            for chunk in chunks:
                handler.wfile.write(json.dumps(chunk).encode() + b'\n')
                handler.wfile.flush()
                if delay:
                    time.sleep(delay)
        self.serve(script)

    def test_assemble_and_validate_only_complete_response(self):
        self.stream([{'message': {'content': '{"text":'}},
                     {'message': {'content': '"4"}'}},
                     {'done': True, 'done_reason': 'stop', 'eval_count': 7}])
        progress = []
        answer = ollama_transport(self.config, self.payload, lambda: None, progress.append)
        self.assertEqual(answer, {'text': '4'})
        self.assertTrue(self.request['stream'])
        self.assertEqual(self.path, '/api/chat')
        self.assertNotIn('Authorization', self.headers)
        self.assertEqual(self.request['format'], self.payload['response_format']['json_schema']['schema'])
        self.assertEqual(progress[-1]['response_characters'], len('{"text":"4"}'))
        self.assertEqual(progress[-1]['eval_count'], 7)
        self.assertNotIn('content', progress[-1])
        with self.assertRaisesRegex(GroqError, 'context budget'):
            ollama_transport({**self.config, 'num_ctx': 10}, self.payload, lambda: None)

    def test_missing_completion_is_not_accepted_even_if_json_is_valid(self):
        self.stream([{'message': {'content': '{"text":"4"}'}}])
        with self.assertRaises(GroqError):
            ollama_transport(self.config, self.payload, lambda: None)

    def test_truncated_output_not_accepted(self):
        self.stream([{'message': {'content': '{"text":"4"}'}, 'done': True, 'done_reason': 'length'}])
        with self.assertRaises(TruncatedResponse):
            ollama_transport(self.config, self.payload, lambda: None)

    def test_invalid_schema_not_accepted(self):
        self.stream([{'message': {'content': '{"text":4}'}, 'done': True, 'done_reason': 'stop'}])
        with self.assertRaisesRegex(GroqError, 'invalid JSON/schema'):
            ollama_transport(self.config, self.payload, lambda: None)

    def test_active_stream_outlives_per_read_timeout(self):
        self.stream([{'message': {'content': part}} for part in ('{', '"text"', ':', '"4"', '}')] +
                    [{'done': True, 'done_reason': 'stop'}], delay=0.15)
        with patch('v2.providers.LOCAL_TIMEOUT_SECONDS', 0.6):
            started = time.monotonic()
            self.assertEqual(ollama_transport(self.config, self.payload, lambda: None), {'text': '4'})
            self.assertGreater(time.monotonic() - started, 0.6)

    def check_cancel(self, before_headers=False, deadline=False):
        started, disconnected = threading.Event(), threading.Event()
        def script(handler):
            if not before_headers:
                handler.send_response(200)
                handler.end_headers()
                handler.wfile.write(b'{"message":{"content":"{\\\"text\\\":"}}\n')
                handler.wfile.flush()
            started.set()
            handler.connection.settimeout(3)
            if handler.connection.recv(1) == b'':
                disconnected.set()
        self.serve(script)
        def checkpoint():
            if started.is_set() and not deadline:
                raise RuntimeError('user stop')
        with patch('v2.providers.LOCAL_DEADLINE_SECONDS', 0.5 if deadline else 60):
            with self.assertRaisesRegex(GroqError if deadline else RuntimeError,
                                        'deadline' if deadline else 'user stop'):
                ollama_transport(self.config, self.payload, checkpoint)
        self.assertTrue(disconnected.wait(2), 'server must observe disconnect and cancel generation')

    def test_stop_disconnects_during_stream(self):
        self.check_cancel()

    def test_stop_disconnects_before_headers(self):
        self.check_cancel(before_headers=True)

    def test_deadline_disconnects_without_accepting_partial_result(self):
        self.check_cancel(deadline=True)
