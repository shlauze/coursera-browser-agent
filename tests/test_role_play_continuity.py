"""Offline recovery checks for a Role Play shared across stale Chrome tabs."""

from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import coursera_start as engine
import role_play_flow as role_play


URL = ("https://www.coursera.org/learn/"
       "adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa/"
       "coach/RyZW9/after-the-red-team-presenting-security-test-findings-to-leadership")
ITEM = {"key": "coach:RyZW9", "url": URL, "title": "After the Red Team"}
TASKS = [
    "Present methodology: Explain testing methods and attack vectors.",
    "Report findings: State vulnerabilities with severity ratings.",
    "Explain findings: Translate technical details for executives.",
    "Recommend remediation: Propose prioritized security improvements.",
    "Address questions: Defend testing rigor and confidence.",
]
ANSWER = "I would rate the bypasses by reach and impact, and validate each rating with the vendor."


def state(*, user=ANSWER, complete=4, coach_after=True, phase="active"):
    return {"role_play": True, "kind": "role_play", "phase": phase,
            "task_total": 5, "task_complete": complete, "tasks": TASKS[:],
            "transcript_valid": True, "last_user_message": user,
            "user_message_count": int(bool(user)),
            "last_user_followed_by_coach": bool(user and coach_after)}


def ledger(*, digest=False, sent_turns=None, complete=0):
    value = {"phase": "active", "task_total": 5,
             "task_complete": complete, "tasks": TASKS[:]}
    if digest:
        value.update(last_sent_digest=role_play._turn_digest(ANSWER),
                     last_sent_user_count=1, sent_turns=1)
    elif sent_turns is not None:
        value["sent_turns"] = sent_turns
    return value


class FakeChromeTab:
    states = {}
    instances = []

    def __init__(self, address):
        self.address = address
        self.closed = False
        self.navigated = []
        self.instances.append(self)

    async def connect(self):
        return None

    async def close(self):
        self.closed = True

    async def evaluate(self, expression):
        assert expression.startswith(role_play.ROLE_PLAY_JS + "(")
        return self.states[self.address].copy()

    async def navigate(self, url):
        self.navigated.append(url)


class FakeRunner:
    def __init__(self, tab, saved):
        self.tab = tab
        self.checkpoint = {"role_plays": {ITEM["key"]: saved}}
        self.events = []

    def event(self, name, **fields):
        self.events.append((name, fields))


def page(address):
    return {"type": "page", "url": URL, "webSocketDebuggerUrl": address}


class ConversationEvidenceTests(unittest.TestCase):
    def test_saved_digest_requires_same_user_turn_count_and_newer_coach(self):
        saved = ledger(digest=True)
        self.assertEqual(role_play._conversation_evidence(
            saved, state(), require_coach=True),
            (role_play._turn_digest(ANSWER), 1))
        self.assertIsNone(role_play._conversation_evidence(
            saved, state(user="Different response"), require_coach=True))
        self.assertIsNone(role_play._conversation_evidence(
            saved, state(coach_after=False), require_coach=True))
        extra = state()
        extra["user_message_count"] = 2
        self.assertIsNone(role_play._conversation_evidence(saved, extra, require_coach=True))
        self.assertIsNone(role_play._conversation_evidence(
            saved, state(user=""), require_coach=True))

    def test_legacy_turn_requires_new_progress_and_one_complete_exchange(self):
        old = ledger()
        self.assertIsNotNone(role_play._conversation_evidence(
            old, state(), require_coach=True))
        self.assertIsNone(role_play._conversation_evidence(
            old, state(complete=0), require_coach=True))
        self.assertIsNone(role_play._conversation_evidence(
            old, state(coach_after=False), require_coach=True))
        extra = state()
        extra["user_message_count"] = 2
        self.assertIsNone(role_play._conversation_evidence(old, extra, require_coach=True))

    def test_multiline_pending_reply_requires_rendered_turn_and_coach(self):
        pending = ledger()
        pending.update(phase="message_pending", pending_reply="First paragraph.\n\nSecond paragraph.",
                       pending_user_count_before=0)
        rendered = state(user="First paragraph. Second paragraph.")
        self.assertEqual(role_play._conversation_evidence(
            pending, rendered, require_coach=True),
            (role_play._turn_digest(pending["pending_reply"]), 1))
        self.assertIsNone(role_play._conversation_evidence(
            pending, state(user="First paragraph. Second paragraph.", coach_after=False),
            require_coach=True))

    def test_proven_fresh_zero_turn_needs_explicit_saved_zero(self):
        fresh = ledger(sent_turns=0)
        fresh_view = state(user="", complete=0)
        self.assertTrue(role_play._proven_zero_turn(fresh, fresh_view))
        fresh.pop("sent_turns")
        self.assertFalse(role_play._proven_zero_turn(fresh, fresh_view))
        self.assertFalse(role_play._proven_zero_turn(
            ledger(sent_turns=0), state(user="", complete=4)))


class TabRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeChromeTab.states = {}
        FakeChromeTab.instances = []

    async def test_unique_matching_old_tab_runs_then_refreshes_queue_tab(self):
        FakeChromeTab.states.update(queue=state(user=""), original=state(), stale=state(user=""))
        queue = FakeChromeTab("queue")
        runner = FakeRunner(queue, ledger())
        pages = [page("queue"), page("original"), page("stale")]
        async def selected_handler(current_runner, _item, **_kwargs):
            self.assertEqual(current_runner.tab.address, "original")
            return "done"
        with patch.object(engine, "ChromeTab", FakeChromeTab), \
             patch.object(engine, "local_json", return_value=pages), \
             patch.object(role_play, "_run_role_play_current_tab",
                          AsyncMock(side_effect=selected_handler)):
            result = await role_play.run_role_play(runner, ITEM)
        self.assertEqual(result, "done")
        self.assertIs(runner.tab, queue)
        self.assertEqual(queue.navigated, [URL])
        selected = [tab for tab in FakeChromeTab.instances if tab.address == "original"]
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].closed)
        self.assertFalse(queue.closed)

    async def test_two_matching_tabs_stop_without_handler_or_navigation(self):
        FakeChromeTab.states.update(queue=state(user=""), one=state(), two=state())
        queue = FakeChromeTab("queue")
        runner = FakeRunner(queue, ledger())
        with patch.object(engine, "ChromeTab", FakeChromeTab), \
             patch.object(engine, "local_json", return_value=[page("queue"), page("one"), page("two")]), \
             patch.object(role_play, "_run_role_play_current_tab", AsyncMock()) as handler:
            with self.assertRaisesRegex(engine.StopRun, "one unique tab"):
                await role_play.run_role_play(runner, ITEM)
        handler.assert_not_awaited()
        self.assertEqual(queue.navigated, [])

    async def test_saved_active_turn_ignores_stale_feedback_and_finds_live_chat(self):
        FakeChromeTab.states.update(queue=state(phase="feedback"), original=state())
        queue = FakeChromeTab("queue")
        runner = FakeRunner(queue, ledger(digest=True))
        async def selected_handler(current_runner, _item, **_kwargs):
            self.assertEqual(current_runner.tab.address, "original")
        with patch.object(engine, "ChromeTab", FakeChromeTab), \
             patch.object(engine, "local_json", return_value=[page("queue"), page("original")]), \
             patch.object(role_play, "_run_role_play_current_tab",
                          AsyncMock(side_effect=selected_handler)):
            await role_play.run_role_play(runner, ITEM)
        self.assertIs(runner.tab, queue)

    async def test_terminal_checkpoint_and_proven_zero_turn_bypass_tab_search(self):
        terminal = ledger(digest=True)
        terminal["phase"] = "end_requested"
        for saved, current in (
                (terminal, state(phase="feedback")),
                (ledger(sent_turns=0), state(user="", complete=0))):
            with self.subTest(phase=current["phase"], turns=saved.get("sent_turns")):
                FakeChromeTab.states = {"queue": current}
                queue = FakeChromeTab("queue")
                runner = FakeRunner(queue, saved)
                with patch.object(engine, "ChromeTab", FakeChromeTab), \
                     patch.object(engine, "local_json", side_effect=AssertionError("scan")), \
                     patch.object(role_play, "_run_role_play_current_tab",
                                  AsyncMock(return_value="done")):
                    self.assertEqual(await role_play.run_role_play(runner, ITEM), "done")
                self.assertEqual(queue.navigated, [])

    async def test_recovery_event_failure_restores_original_tab(self):
        FakeChromeTab.states.update(queue=state(user=""), original=state())
        queue = FakeChromeTab("queue")
        runner = FakeRunner(queue, ledger())
        def reject_event(_name, **_fields):
            raise OSError("checkpoint write failed")
        runner.event = reject_event
        with patch.object(engine, "ChromeTab", FakeChromeTab), \
             patch.object(engine, "local_json", return_value=[page("queue"), page("original")]), \
             patch.object(role_play, "_run_role_play_current_tab", AsyncMock()) as handler:
            with self.assertRaisesRegex(OSError, "checkpoint write failed"):
                await role_play.run_role_play(runner, ITEM)
        handler.assert_not_awaited()
        self.assertIs(runner.tab, queue)
        selected = [tab for tab in FakeChromeTab.instances if tab.address == "original"]
        self.assertTrue(selected[0].closed)


if __name__ == "__main__":
    unittest.main()
