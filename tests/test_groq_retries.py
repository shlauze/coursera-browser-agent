"""Mocked Groq transport only; no credentials, real API, browser or waiting."""
import asyncio
import contextlib
from email.utils import formatdate
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
spec = importlib.util.spec_from_file_location("retry_runner", PROJECT / "coursera_start.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
SUCCESS = {"choices": [{"finish_reason": "stop", "message": {"content": '{"answers":[]}'}}],
           "usage": {"prompt_tokens": 12, "completion_tokens": 3}}


class RetryTests(unittest.TestCase):
    def call(self, outcomes, now=None, observer=None):
        captured = []
        stream = io.StringIO()
        sleep = AsyncMock()
        self.requests, self.sleep, self.output = captured, sleep, stream

        def handler(request):
            captured.append(request)
            outcome = outcomes[min(len(captured) - 1, len(outcomes) - 1)]
            if isinstance(outcome, BaseException):
                raise outcome
            status, headers = outcome if isinstance(outcome, tuple) else (outcome, {})
            return httpx.Response(status, headers=headers,
                                  json=SUCCESS if status == 200 else {"error": "SENSITIVE_ERROR_BODY"})

        original = httpx.AsyncClient

        def client(**kwargs):
            self.client_options = kwargs.copy()
            return original(**kwargs, transport=httpx.MockTransport(handler))

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(runner, "groq_key", return_value="FAKE_TEST_KEY"))
            stack.enter_context(patch.object(httpx, "AsyncClient", side_effect=client))
            stack.enter_context(patch.object(runner.asyncio, "sleep", sleep))
            stack.enter_context(contextlib.redirect_stdout(stream))
            if now is not None:
                stack.enter_context(patch.object(runner.time, "time", return_value=now))
            return asyncio.run(runner.request_groq_answers(
                {"messages": [], "model": "fixture"}, limit_observer=observer))

    def delays(self):
        return [call.args[0] for call in self.sleep.await_args_list]

    def test_success_keeps_return_contract(self):
        result = self.call([200])
        self.assertEqual(result["message"]["content"], '{"answers":[]}')
        self.assertEqual(result["prompt_tokens"], 12)
        self.assertEqual(result["output_tokens"], 3)
        self.assertGreaterEqual(result["elapsed_seconds"], 0)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.delays(), [])
        self.assertFalse(self.client_options["trust_env"])
        self.assertFalse(self.client_options["follow_redirects"])

    def test_transient_http_then_success(self):
        for code in (408, 429, 500, 502, 503, 504, 599):
            with self.subTest(status=code):
                self.call([code, 200])
                self.assertEqual(len(self.requests), 2)
                self.assertEqual(self.delays(), [1.0])
                self.assertEqual(self.requests[0].content, self.requests[1].content)

    def test_network_timeout_remote_protocol_then_success(self):
        for error in (httpx.ConnectError("SENSITIVE_EXCEPTION"), httpx.ReadTimeout("SENSITIVE_EXCEPTION"),
                      httpx.ReadError("SENSITIVE_EXCEPTION"), httpx.RemoteProtocolError("SENSITIVE_EXCEPTION")):
            with self.subTest(error=type(error).__name__):
                self.call([error, 200])
                self.assertEqual(len(self.requests), 2)
                self.assertNotIn("SENSITIVE_EXCEPTION", self.output.getvalue())

    def test_three_requests_maximum(self):
        with self.assertRaises(runner.StopRun):
            self.call([503])
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.delays(), [1.0, 2.0])

    def test_terminal_http_never_retried(self):
        for status in (400, 401, 403, 404, 413, 422, 301, 307):
            with self.subTest(status=status), self.assertRaises(runner.StopRun) as caught:
                self.call([status])
            self.assertIn(str(status), str(caught.exception))
            self.assertEqual(len(self.requests), 1)
            self.assertEqual(self.delays(), [])

    def test_local_protocol_failure_not_retried(self):
        with self.assertRaises(runner.StopRun):
            self.call([httpx.LocalProtocolError("SENSITIVE_EXCEPTION")])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.delays(), [])

    def test_numeric_retry_after_respected(self):
        self.call([(429, {"Retry-After": "4.25"}), (503, {"Retry-After": "6"}), 200])
        self.assertEqual(self.delays(), [4.25, 6.0])
        self.assertEqual(len(self.requests), 3)

    def test_observed_413_with_retry_after_retries_without_browser_action(self):
        snapshots = []
        self.call([(413, {"Retry-After": "3"}), 200], observer=snapshots.append)
        self.assertEqual(self.delays(), [3.0])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(snapshots[0]["groq_retry_after_seconds"], 3.0)

    def test_rate_headers_are_sanitized_for_monitor(self):
        snapshots = []
        self.call([(200, {"x-ratelimit-limit-requests":"1000",
                          "x-ratelimit-remaining-requests":"997",
                          "x-ratelimit-limit-tokens":"8000",
                          "x-ratelimit-remaining-tokens":"not-a-number"})],
                  observer=snapshots.append)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["groq_requests_remaining"], 997)
        self.assertEqual(snapshots[0]["groq_tokens_limit"], 8000)
        self.assertNotIn("groq_tokens_remaining", snapshots[0])

    def test_date_retry_after_respected(self):
        now = 1_800_000_000
        self.call([(429, {"Retry-After": formatdate(now + 30, usegmt=True)}), 200], now=now)
        self.assertEqual(self.delays(), [30.0])

    def test_excess_retry_after_stops_without_early_request(self):
        for header in ("61", "120", "9999999999999999999999999999999999999999999999999999", formatdate(1_800_000_120, usegmt=True)):
            with self.subTest(header=header), self.assertRaises(runner.StopRun) as caught:
                self.call([(429, {"Retry-After": header})], now=1_800_000_000)
            self.assertEqual(len(self.requests), 1)
            self.assertEqual(self.delays(), [])
            self.assertIn("60 seconds", str(caught.exception))

    def test_remaining_wait_budget_never_shortens_provider_wait(self):
        with self.assertRaises(runner.StopRun):
            self.call([(429, {"Retry-After": "50"}), (429, {"Retry-After": "20"}), 200])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.delays(), [50.0])

    def test_exact_wait_budget_is_allowed(self):
        self.call([(429, {"Retry-After": "30"}), (429, {"Retry-After": "30"}), 200])
        self.assertEqual(sum(self.delays()), 60)
        self.assertEqual(len(self.requests), 3)

    def test_invalid_header_uses_backoff_without_echo(self):
        for header in ("SENSITIVE_HEADER", "NaN", "Infinity", "-1"):
            with self.subTest(header=header):
                self.call([(503, {"Retry-After": header}), 200])
                self.assertEqual(self.delays(), [1.0])
                self.assertNotIn(header, self.output.getvalue())

    def test_error_output_is_sanitized_and_context_suppressed(self):
        for outcome in (500, httpx.ConnectError("SENSITIVE_EXCEPTION")):
            with self.subTest(outcome=type(outcome).__name__), self.assertRaises(runner.StopRun) as caught:
                self.call([outcome])
            combined = str(caught.exception) + self.output.getvalue()
            for sensitive in ("FAKE_TEST_KEY", "SENSITIVE_ERROR_BODY", "SENSITIVE_EXCEPTION"):
                self.assertNotIn(sensitive, combined)
            self.assertTrue(caught.exception.__suppress_context__)

    def test_retry_after_parser_past_date_and_missing(self):
        self.assertEqual(runner.groq_retry_after_seconds(formatdate(1_799_999_000, usegmt=True), now=1_800_000_000), 0)
        for value in (None, "", "not a date", "-10"):
            self.assertIsNone(runner.groq_retry_after_seconds(value))


if __name__ == "__main__":
    unittest.main(verbosity=2)
