"""A Coach Role Play may end itself as soon as the fifth task is satisfied."""

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import coursera_start as engine
import role_play_flow as role_play


FIXTURE = json.loads((ROOT / "tests" / "fixtures" /
                      "role_play_insider_threat.json").read_text(encoding="utf-8"))
ITEM = {"key": FIXTURE["key"], "url": FIXTURE["url"], "title": FIXTURE["title"]}
TASKS = FIXTURE["tasks"]
ANSWER = "The evidence should be reviewed before any conclusion is presented."


def active_state():
    return {"role_play": True, "kind": "role_play", "phase": "active",
            "task_total": len(TASKS), "task_complete": len(TASKS),
            "tasks": TASKS[:], "transcript_valid": True,
            "last_user_message": ANSWER, "user_message_count": 1,
            "last_user_followed_by_coach": True}


def feedback_state(feedback=None):
    return {"role_play": True, "kind": "role_play", "phase": "feedback",
            "feedback_text": FIXTURE["successful_feedback"] if feedback is None else feedback,
            "practice_again_count": 1, "goal_modal_count": 0}


def saved_ledger():
    return {"phase": "active", "task_total": len(TASKS),
            "task_complete": len(TASKS), "tasks": TASKS[:],
            "sent_turns": 1, "last_sent_digest": role_play._turn_digest(ANSWER),
            "last_sent_user_count": 1}


def draft_active_state():
    return {**active_state(), "task_complete": 0,
            "url": ITEM["url"],
            "last_coach_message": "Please explain how you would address the remaining tasks."}


def draft_feedback_state(feedback=None):
    return {**feedback_state(feedback), "url": ITEM["url"],
            "transcript_valid": True, "last_user_message": ANSWER,
            "user_message_count": 1, "last_user_followed_by_coach": True}


class FakeTab:
    def __init__(self, address="queue"):
        self.address = address


class FakeRunner:
    def __init__(self):
        self.tab = FakeTab()
        self.checkpoint = {"role_plays": {ITEM["key"]: saved_ledger()}}
        self.events = []
        self.verified_item = AsyncMock(return_value=True)

    def event(self, name, **fields):
        self.events.append((name, fields))


class AutoEndedRolePlayTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_end_during_draft_uses_saved_turn_and_feedback(self):
        runner = FakeRunner()
        runner.checkpoint["role_plays"][ITEM["key"]]["task_complete"] = 0
        active, completed = draft_active_state(), draft_feedback_state()
        with patch.object(role_play, "_wait_state", AsyncMock(return_value=active)), \
             patch.object(role_play, "_await_tasks", AsyncMock(return_value=active)), \
             patch.object(role_play, "_await_coach_turn", AsyncMock(return_value=active)), \
             patch.object(role_play, "_draft_reply", AsyncMock(return_value="Stale draft.")), \
             patch.object(role_play, "_ui", AsyncMock(return_value=completed)) as ui:
            await role_play._run_role_play_current_tab(runner, ITEM, max_seconds=30)
        ui.assert_awaited_once_with(runner.tab, ITEM["url"])
        runner.verified_item.assert_awaited_once()
        self.assertGreater(runner.verified_item.await_args.kwargs["wait"], 0)
        self.assertIn("role_play_auto_finished_during_draft",
                      [name for name, _ in runner.events])
        self.assertEqual(runner.checkpoint["role_plays"][ITEM["key"]]["phase"],
                         "completed")

    async def test_auto_end_during_draft_rejects_wrong_transcript(self):
        runner = FakeRunner()
        runner.checkpoint["role_plays"][ITEM["key"]]["task_complete"] = 0
        active = draft_active_state()
        completed = {**draft_feedback_state(), "last_user_message": "Another learner reply."}
        with patch.object(role_play, "_wait_state", AsyncMock(return_value=active)), \
             patch.object(role_play, "_await_tasks", AsyncMock(return_value=active)), \
             patch.object(role_play, "_await_coach_turn", AsyncMock(return_value=active)), \
             patch.object(role_play, "_draft_reply", AsyncMock(return_value="Stale draft.")), \
             patch.object(role_play, "_ui", AsyncMock(return_value=completed)) as ui:
            with self.assertRaisesRegex(engine.StopRun, "prompt or task rubric changed"):
                await role_play._run_role_play_current_tab(runner, ITEM, max_seconds=30)
        ui.assert_awaited_once_with(runner.tab, ITEM["url"])
        runner.verified_item.assert_not_awaited()

    async def test_auto_end_during_draft_rejects_negative_feedback(self):
        runner = FakeRunner()
        runner.checkpoint["role_plays"][ITEM["key"]]["task_complete"] = 0
        active = draft_active_state()
        completed = draft_feedback_state(FIXTURE["premature_feedback"])
        with patch.object(role_play, "_wait_state", AsyncMock(return_value=active)), \
             patch.object(role_play, "_await_tasks", AsyncMock(return_value=active)), \
             patch.object(role_play, "_await_coach_turn", AsyncMock(return_value=active)), \
             patch.object(role_play, "_draft_reply", AsyncMock(return_value="Stale draft.")), \
             patch.object(role_play, "_ui", AsyncMock(return_value=completed)) as ui:
            with self.assertRaisesRegex(engine.StopRun, "prompt or task rubric changed"):
                await role_play._run_role_play_current_tab(runner, ITEM, max_seconds=30)
        ui.assert_awaited_once_with(runner.tab, ITEM["url"])
        runner.verified_item.assert_not_awaited()

    async def test_text_chat_terminal_dom_uses_header_retry_and_feedback_block(self):
        script = "const run=" + role_play.ROLE_PLAY_JS + ";\n" + r'''
const url="https://www.coursera.org/learn/example/coach/ROLE/lesson";
const el=text=>({innerText:text,hidden:false,inert:false,disabled:false,
  parentElement:null,getClientRects(){return [1]},
  getAttribute(){return null},querySelectorAll(){return []}});
const retry=el("Try again");
const panel=el("Go to next item");
const block=el("Great work! Task 1: Methodology You clearly explained the approach.");
const heading=el("Feedback session");
heading.parentElement={nextElementSibling:block};
const root=el("The Role Play activity has ended. Feedback session " + block.innerText);
retry.parentElement=root;
panel.parentElement=root;
block.parentElement=root;
root.querySelectorAll=selector=>{
  if(selector==='[data-testid="chat-end-buttons"]')return [panel];
  if(selector==='button,[role="button"]')return [retry];
  if(selector==='h2')return [heading];
  return [];
};
global.location=new URL(url);
global.document={readyState:"complete",body:root,
  querySelector:()=>null,querySelectorAll:selector=>{
    if(selector==='button,[role="button"]')return [retry];
    if(selector==='[data-testid="learner-role-play-item"]')return [root];
    return [];
  }};
global.getComputedStyle=()=>({display:"block",visibility:"visible"});
const s=run({url,action:"inspect"});
process.stdout.write(JSON.stringify({kind:s.kind,phase:s.phase,
  feedback:s.feedback_text,retry:s.practice_again_count}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["kind"], "role_play")
        self.assertEqual(observed["phase"], "feedback")
        self.assertEqual(observed["retry"], 1)
        self.assertIn("Task 1: Methodology", observed["feedback"])

    async def test_end_race_accepts_verified_feedback_without_second_click(self):
        runner = FakeRunner()
        initial, completed = active_state(), feedback_state()
        with patch.object(role_play, "_wait_state", AsyncMock(return_value=initial)), \
             patch.object(role_play, "_await_tasks", AsyncMock(return_value=initial)), \
             patch.object(role_play, "_ui", AsyncMock(return_value=completed)) as ui:
            await role_play._run_role_play_current_tab(runner, ITEM, max_seconds=30)
        ui.assert_awaited_once_with(runner.tab, ITEM["url"], "end",
                                    expected_total=len(TASKS))
        runner.verified_item.assert_awaited_once_with(ITEM)
        self.assertIn("role_play_auto_finished", [name for name, _ in runner.events])
        self.assertEqual(runner.checkpoint["role_plays"][ITEM["key"]]["phase"], "completed")

    async def test_restart_uses_positive_terminal_feedback_in_queue_tab(self):
        runner = FakeRunner()
        with patch.object(engine, "ChromeTab", FakeTab), \
             patch.object(role_play, "_ui", AsyncMock(return_value=feedback_state())), \
             patch.object(engine, "local_json", side_effect=AssertionError("tab scan")):
            self.assertIsNone(await role_play._resume_conversation_tab(
                runner, ITEM, runner.checkpoint["role_plays"][ITEM["key"]]))

    async def test_positive_feedback_waits_for_delayed_green_mark(self):
        runner = FakeRunner()
        runner.verified_item = AsyncMock(side_effect=lambda _item, wait=0: wait > 0)
        with patch.object(role_play, "_wait_state",
                          AsyncMock(return_value=feedback_state())), \
             patch.object(role_play, "_ui", AsyncMock()) as ui:
            await role_play._run_role_play_current_tab(runner, ITEM, max_seconds=30)
        ui.assert_not_awaited()  # No Practice again after affirmative feedback.
        runner.verified_item.assert_awaited_once()
        args, kwargs = runner.verified_item.await_args
        self.assertEqual(args, (ITEM,))
        self.assertGreater(kwargs["wait"], 0)
        self.assertLessEqual(kwargs["wait"], 20)
        self.assertEqual(runner.checkpoint["role_plays"][ITEM["key"]]["phase"],
                         "completed")

    async def test_positive_feedback_without_green_mark_stops_without_retry(self):
        runner = FakeRunner()
        runner.verified_item = AsyncMock(return_value=False)
        with patch.object(role_play, "_wait_state",
                          AsyncMock(return_value=feedback_state())), \
             patch.object(role_play, "_ui", AsyncMock()) as ui:
            with self.assertRaisesRegex(engine.StopRun, "green course mark"):
                await role_play._run_role_play_current_tab(runner, ITEM,
                                                           max_seconds=30)
        ui.assert_not_awaited()
        self.assertNotEqual(runner.checkpoint["role_plays"][ITEM["key"]]["phase"],
                            "completed")

    async def test_negative_terminal_feedback_does_not_bypass_recovery(self):
        runner = FakeRunner()
        with patch.object(engine, "ChromeTab", FakeTab), \
             patch.object(role_play, "_ui",
                          AsyncMock(return_value=feedback_state(
                              FIXTURE["premature_feedback"]))), \
             patch.object(engine, "local_json", return_value=[]):
            with self.assertRaisesRegex(engine.StopRun, "one unique tab"):
                await role_play._resume_conversation_tab(
                    runner, ITEM, runner.checkpoint["role_plays"][ITEM["key"]])

    async def test_end_race_rejects_negative_feedback(self):
        runner = FakeRunner()
        initial = active_state()
        with patch.object(role_play, "_wait_state", AsyncMock(return_value=initial)), \
             patch.object(role_play, "_await_tasks", AsyncMock(return_value=initial)), \
             patch.object(role_play, "_ui",
                          AsyncMock(return_value=feedback_state(
                              FIXTURE["premature_feedback"]))) as ui:
            with self.assertRaisesRegex(engine.StopRun,
                                        "End Role Play changed before click"):
                await role_play._run_role_play_current_tab(runner, ITEM,
                                                           max_seconds=30)
        self.assertEqual(ui.await_count, 1)
        self.assertNotEqual(runner.checkpoint["role_plays"][ITEM["key"]]["phase"],
                            "completed")


if __name__ == "__main__":
    unittest.main()
