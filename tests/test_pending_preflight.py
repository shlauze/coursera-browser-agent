"""Startup quiz recovery must finish a saved result without a fresh attempt."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import coursera_start as engine
from pending_preflight import recover_pending_quiz


COURSE_ID = "sample-course"
COURSE_URL = "https://www.coursera.org/learn/" + COURSE_ID
ITEM_URL = COURSE_URL + "/assignment-submission/QUIZ1/graded-quiz"
FEEDBACK_URL = ITEM_URL + "/view-feedback"
KEY = "assignment:QUIZ1"
ITEM = {"key": KEY, "url": ITEM_URL, "title": "Graded quiz"}


def result(url=FEEDBACK_URL, *, grade=50, details=True, attempt=None):
    text = [f"Your grade: {grade}%", "To pass: 80%"]
    if attempt is not None:
        text.insert(0, f"Attempt {attempt} of 3")
    if details:
        text.extend(["Question 1", "Which answer?", "Incorrect" if grade < 80 else "Correct",
                     "0 / 1 point" if grade < 80 else "1 / 1 point"])
    return {"url": url, "result_page": True, "blocking_dialog": False,
            "editable_form": False, "page_text": "\n".join(text),
            "buttons": [], "snapshot_id": "snap-1"}


class Store:
    def __init__(self, ledger):
        self.course = {"id": COURSE_ID, "url": COURSE_URL, "title": "Sample Course",
                       "checkpoint": {"attempts": {KEY: copy.deepcopy(ledger)}, "feedback": {},
                                      "modules": {"1": {"roster": [ITEM], "items": {}}}}}
        self.data = {"courses": [self.course]}
        self.saves = 0
        self.events = []

    def save(self):
        self.saves += 1

    def event(self, kind, message, course_id=None):
        self.events.append((kind, message, course_id))


class Tab:
    def __init__(self, url, ui, feedback_ui=None):
        self.url = url
        self.ui = ui
        self.feedback_ui = feedback_ui
        self.actions = []

    async def navigate(self, url):
        self.actions.append(("navigate", url))
        self.url = url

    async def evaluate(self, expression):
        if expression == "({url:location.href})":
            return {"url": self.url}
        if '"action": "button"' in expression:
            # The preflight is only permitted to open an observed review link.
            assert '"label": "View feedback"' in expression
            self.actions.append(("feedback", "View feedback"))
            self.url = FEEDBACK_URL
            return {"clicked": "View feedback", "url": FEEDBACK_URL}
        if '"action": "inspect"' in expression:
            self.actions.append(("inspect", self.url))
            value = self.feedback_ui if self.url == FEEDBACK_URL and self.feedback_ui else self.ui
            return {**value, "url": self.url}
        raise AssertionError("Unexpected browser action: " + expression[-100:])


class PendingPreflightTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        engine.configure_course("https://www.coursera.org/learn/philosophy")

    async def test_no_saved_result_makes_no_browser_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store({"count": 0, "pending": False})
            tab = Tab(ITEM_URL, result())
            self.assertIsNone(await recover_pending_quiz(tab, store, root=Path(directory)))
            self.assertEqual(tab.actions, [])
            self.assertEqual(store.saves, 0)

    async def test_exact_pending_failed_result_saves_complete_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store({"count": 1, "pending": True, "submit_phase": "confirm_clicked",
                           "question_count": 1, "platform_attempt": 2})
            tab = Tab(FEEDBACK_URL, result(attempt=2))
            outcome = await recover_pending_quiz(tab, store, root=Path(directory))
            self.assertEqual(outcome, {"course_id": COURSE_ID, "key": KEY,
                                       "passed": False, "feedback_ready": True})
            ledger = store.course["checkpoint"]["attempts"][KEY]
            self.assertFalse(ledger["pending"])
            self.assertFalse(ledger["passed"])
            self.assertEqual(ledger["feedback_ready_count"], 1)
            self.assertIn("Question 1", store.course["checkpoint"]["feedback"][KEY])
            self.assertEqual([action for action, _ in tab.actions if action != "inspect"], [])
            self.assertEqual(store.events[0][0], "quiz_preflight_reconciled")

    async def test_exact_pending_passed_result_does_not_need_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store({"count": 1, "pending": True, "submit_phase": "first_clicked",
                           "platform_attempt": 2})
            tab = Tab(FEEDBACK_URL, result(grade=100, details=False, attempt=2))
            outcome = await recover_pending_quiz(tab, store, root=Path(directory))
            self.assertTrue(outcome["passed"])
            self.assertFalse(store.course["checkpoint"]["attempts"][KEY]["pending"])
            self.assertFalse(store.course["checkpoint"]["attempts"][KEY]["feedback_ready"])
            self.assertEqual([action for action, _ in tab.actions if action != "inspect"], [])

    async def test_pending_result_in_other_tab_stays_reserved(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store({"count": 1, "pending": True, "question_count": 1})
            tab = Tab("https://www.coursera.org/my-learning", result())
            with self.assertRaisesRegex(engine.StopRun, "not open in Chrome") as caught:
                await recover_pending_quiz(tab, store, root=Path(directory))
            self.assertEqual(caught.exception.course_id, COURSE_ID)
            self.assertEqual(caught.exception.item_key, KEY)
            self.assertEqual(tab.actions, [])
            self.assertTrue(store.course["checkpoint"]["attempts"][KEY]["pending"])
            self.assertEqual(store.saves, 0)

    async def test_observed_but_unsubmitted_confirmation_never_claims_old_grade(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store({"count": 1, "pending": True, "submit_phase": "confirmation_observed",
                           "question_count": 1})
            tab = Tab(FEEDBACK_URL, result(grade=100))
            with self.assertRaisesRegex(engine.StopRun, "final Submit was not clicked"):
                await recover_pending_quiz(tab, store, root=Path(directory))
            self.assertEqual(tab.actions, [])
            self.assertTrue(store.course["checkpoint"]["attempts"][KEY]["pending"])
            self.assertEqual(store.saves, 0)

    async def test_pending_result_without_platform_attempt_identity_stays_reserved(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store({"count": 2, "pending": True, "question_count": 1})
            tab = Tab(FEEDBACK_URL, result())
            with self.assertRaisesRegex(engine.StopRun, "no Coursera attempt number"):
                await recover_pending_quiz(tab, store, root=Path(directory))
            self.assertTrue(store.course["checkpoint"]["attempts"][KEY]["pending"])
            self.assertEqual(store.saves, 0)

    async def test_local_attempt_count_does_not_define_platform_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store({"count": 2, "pending": True, "platform_attempt": 3})
            tab = Tab(FEEDBACK_URL, result(grade=100, details=False, attempt=3))
            outcome = await recover_pending_quiz(tab, store, root=Path(directory))
            self.assertTrue(outcome["passed"])
            self.assertFalse(store.course["checkpoint"]["attempts"][KEY]["pending"])

    async def test_older_result_is_not_accepted_for_first_local_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store({"count": 1, "pending": True, "platform_attempt": 2})
            tab = Tab(FEEDBACK_URL, result(grade=100, details=False, attempt=1))
            with self.assertRaisesRegex(engine.StopRun, "does not identify"):
                await recover_pending_quiz(tab, store, root=Path(directory))
            self.assertTrue(store.course["checkpoint"]["attempts"][KEY]["pending"])

    async def test_failed_review_opens_only_feedback_control(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store({"count": 1, "pending": False, "passed": False,
                           "feedback_ready": False, "question_count": 1,
                           "result_url": ITEM_URL})
            landing = result(ITEM_URL, details=False)
            landing["buttons"] = [{"label": "View feedback", "disabled": False}]
            tab = Tab("https://www.coursera.org/my-learning", landing,
                      feedback_ui=result())
            outcome = await recover_pending_quiz(tab, store, root=Path(directory))
            self.assertFalse(outcome["passed"])
            self.assertEqual(tab.actions[0], ("navigate", ITEM_URL))
            self.assertIn(("feedback", "View feedback"), tab.actions)
            self.assertNotIn(("navigate", "https://www.coursera.org/my-learning"), tab.actions)
            self.assertEqual(store.course["checkpoint"]["attempts"][KEY]["result_url"], FEEDBACK_URL)

    async def test_missing_question_feedback_preserves_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store({"count": 1, "pending": True, "question_count": 1,
                           "platform_attempt": 2})
            tab = Tab(FEEDBACK_URL, result(details=False, attempt=2))
            with patch("pending_preflight.asyncio.sleep", new_callable=AsyncMock), \
                 self.assertRaisesRegex(engine.StopRun, "did not load completely"):
                await recover_pending_quiz(tab, store, root=Path(directory))
            self.assertTrue(store.course["checkpoint"]["attempts"][KEY]["pending"])
            self.assertEqual(store.saves, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
