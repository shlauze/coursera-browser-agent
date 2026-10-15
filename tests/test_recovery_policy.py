"""Offline checks that retries never repeat a non-idempotent operation."""

from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recovery_policy import READ_OPERATIONS, classify_transient, retry_read


def roster_error(code=None, *, message=None, warnings=None):
    error = RuntimeError(message or "Module 2 roster needs review: The Lessons navigation has not loaded.")
    error.diagnostic = {"snapshot": {"error_code": code, "warnings": warnings or []}}
    return error


class ClassificationTests(unittest.TestCase):
    def test_timeout_and_io_are_only_read_retries(self):
        for operation in READ_OPERATIONS:
            with self.subTest(operation=operation):
                self.assertEqual(classify_transient(TimeoutError("timed out"), operation), "timeout")
                self.assertEqual(classify_transient(OSError("socket reset"), operation), "io_error")
        for operation in ("submit", "feedback", "grade", "certificate", "manual", "unknown"):
            self.assertIsNone(classify_transient(TimeoutError("timed out"), operation))

    def test_disconnect_requires_recognized_transport_message(self):
        self.assertEqual(classify_transient(RuntimeError("no close frame received or sent"), "open_item"),
                         "cdp_disconnect")
        self.assertEqual(classify_transient(RuntimeError("Chrome Runtime.evaluate: execution context was destroyed"),
                                            "discover_modules"), "cdp_disconnect")
        self.assertIsNone(classify_transient(RuntimeError("Chrome Runtime.evaluate: invalid selector"),
                                             "discover_modules"))

    def test_known_dom_hydration_errors(self):
        self.assertEqual(classify_transient(RuntimeError("Could not verify the full module list. Observed: [1]"),
                                            "discover_modules"), "dom_hydration")
        self.assertEqual(classify_transient(RuntimeError("The lesson did not load: Welcome video"),
                                            "open_item"), "dom_hydration")
        self.assertEqual(classify_transient(RuntimeError(
            "The lesson did not load: Graded Assignment • 10 min"),
            "open_item"), "dom_hydration")
        self.assertEqual(classify_transient(RuntimeError(
            "The lesson loaded, but its course outline did not show the item needed to check completion: Welcome video"),
            "open_item"), "dom_hydration")
        self.assertEqual(classify_transient(RuntimeError(
            "My Learning In Progress did not show a stable course list and pagination."),
            "my_learning"), "dom_hydration")
        self.assertEqual(classify_transient(RuntimeError("My Learning pagination changed before the next page."),
                                            "my_learning"), "dom_hydration")

    def test_roster_requires_specific_diagnostic(self):
        for code in ("lessons_not_ready", "wrong_path"):
            self.assertEqual(classify_transient(roster_error(code), "discover_roster"), "dom_hydration")
        self.assertEqual(classify_transient(roster_error(message=
            "Module 2 roster needs review: the Lessons navigation was incomplete or unstable. Observed links: 2"),
            "discover_roster"), "dom_hydration")
        self.assertIsNone(classify_transient(roster_error(message=
            "Module 2 roster needs review: A lesson link is disabled."), "discover_roster"))
        self.assertIsNone(classify_transient(roster_error(message=
            "Module 2 roster needs review: the Lessons navigation was incomplete or unstable.",
            warnings=["A lesson link is disabled"]), "discover_roster"))
        self.assertIsNone(classify_transient(RuntimeError(
            "Module 2 roster needs review: The Lessons navigation has not loaded."), "discover_roster"))

    def test_never_retry_sensitive_or_manual_results(self):
        messages = (
            "No submitted result appeared within 40 seconds.",
            "The submitted grade is not confirmed yet.",
            "Review feedback did not open within 20 seconds.",
            "A matching Coursera certificate page is not available yet.",
            "Manual work still required: programming assignment",
            "Authorization required to continue.",
            "Sign in to Coursera in the managed Chrome window.",
        )
        for operation in READ_OPERATIONS:
            for message in messages:
                with self.subTest(operation=operation, message=message):
                    self.assertIsNone(classify_transient(RuntimeError(message), operation))
        self.assertIsNone(classify_transient(TimeoutError("Timeout waiting for a grade"), "open_item"))


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_safe_read_with_bounded_backoff_and_metadata_only(self):
        action = AsyncMock(side_effect=[TimeoutError("private question text"),
                                        RuntimeError("The lesson did not load: private title"), "done"])
        sleep = AsyncMock()
        event = Mock()
        result = await retry_read(action, operation="open_item", event=event, sleep=sleep)
        self.assertEqual(result, "done")
        self.assertEqual(action.await_count, 3)
        self.assertEqual(sleep.await_args_list[0].args, (1,))
        self.assertEqual(sleep.await_args_list[1].args, (2,))
        self.assertEqual([call.kwargs for call in event.call_args_list], [
            {"operation":"open_item", "code":"timeout", "attempt":1, "remaining":2},
            {"operation":"open_item", "code":"dom_hydration", "attempt":2, "remaining":1},
            {"operation":"open_item", "code":"recovered", "attempt":3, "remaining":0},
        ])
        self.assertNotIn("private", str(event.call_args_list))

    async def test_disconnect_reconnects_before_retry(self):
        order = []

        async def action():
            order.append("read")
            if order.count("read") == 1:
                raise RuntimeError("WebSocket connection is closed")
            return 42

        async def connect():
            order.append("connect")

        async def event(**fields):
            order.append(fields["code"])

        async def sleep(seconds):
            order.append(("sleep", seconds))

        tab = Mock(connect=AsyncMock(side_effect=connect))
        self.assertEqual(await retry_read(action, operation="my_learning", tab=tab,
                                          event=event, sleep=sleep), 42)
        self.assertEqual(order, ["read", "cdp_disconnect", "connect", ("sleep", 1), "read", "recovered"])

    async def test_permanent_failure_is_not_repeated(self):
        error = RuntimeError("Manual work still required: programming assignment")
        action = AsyncMock(side_effect=error)
        sleep = AsyncMock()
        with self.assertRaises(RuntimeError) as raised:
            await retry_read(action, operation="open_item", sleep=sleep)
        self.assertIs(raised.exception, error)
        action.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_exhausted_retries_raise_original_exception_object(self):
        first = TimeoutError("first")
        last = TimeoutError("last")
        action = AsyncMock(side_effect=[first, last])
        sleep = AsyncMock()
        with self.assertRaises(TimeoutError) as raised:
            await retry_read(action, operation="verify_mark", attempts=2, sleep=sleep)
        self.assertIs(raised.exception, last)
        self.assertEqual(action.await_count, 2)
        sleep.assert_awaited_once_with(1)

    async def test_event_and_connect_failure_do_not_hide_read_failure(self):
        error = RuntimeError("no close frame received or sent")
        action = AsyncMock(side_effect=error)
        tab = Mock(connect=AsyncMock(side_effect=OSError("connect failed")))
        event = Mock(side_effect=RuntimeError("metrics failed"))
        with self.assertRaises(RuntimeError) as raised:
            await retry_read(action, operation="inspect_sidebar", attempts=2,
                             tab=tab, event=event, sleep=AsyncMock())
        self.assertIs(raised.exception, error)
        self.assertEqual(tab.connect.await_count, 1)

    async def test_invalid_operation_or_retry_budget_is_rejected_before_action(self):
        action = AsyncMock(return_value="done")
        for operation, attempts in (("submit", 3), ("my_learning", 0),
                                    ("my_learning", 6), ("my_learning", True)):
            with self.subTest(operation=operation, attempts=attempts):
                with self.assertRaises(ValueError):
                    await retry_read(action, operation=operation, attempts=attempts)
        action.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
