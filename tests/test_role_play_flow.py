"""Offline Role Play regressions based on the five-task Coursera example."""

import json
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import coursera_start as engine
import role_play_flow as role_play

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "role_play_insider_threat.json").read_text(
    encoding="utf-8"))
ITEM = {"key": FIXTURE["key"], "url": FIXTURE["url"], "title": FIXTURE["title"]}


class RolePlayTab:
    """A deterministic page exposing observable task progress, not a fake grade."""

    def __init__(self, *, ended=False, feedback="", progress=0, advance=True):
        self.ended = ended
        self.feedback = feedback
        self.progress = progress
        self.advance = advance
        self.phase = "feedback" if ended else "active"
        self.composer = ""
        self.messages = []
        self.actions = []

    def state(self, **extra):
        return {"url": ITEM["url"], "ready": "complete", "role_play": True,
                "phase": self.phase, "task_total": len(FIXTURE["tasks"]),
                "task_complete": self.progress, "tasks": FIXTURE["tasks"],
                "latest_prompt": FIXTURE["opening_prompt"] if not self.messages else
                    "Please continue the investigation and address the remaining tasks.",
                "last_user_message": self.messages[-1] if self.messages else "",
                "user_message_count": len(self.messages), "composer_value": self.composer,
                "conversation_text": " ".join(self.messages),
                "start_count": 0, "end_count": int(self.phase == "active"),
                "send_count": int(self.phase == "active"),
                "feedback_text": self.feedback, "confirm_count": int(self.phase == "confirm"),
                "practice_again_count": int(self.phase == "feedback"),
                "modal_text": "End the Role Play? Reason not provided Yes, end the Role Play Cancel"
                    if self.phase == "confirm" else "",
                **extra}

    async def evaluate(self, expression):
        prefix = role_play.ROLE_PLAY_JS + "("
        assert expression.startswith(prefix), expression[:100]
        request = json.loads(expression[len(prefix):-1])
        action = request.get("action", "inspect")
        self.actions.append(action)
        if action == "practice_again":
            self.ended = False
            self.phase = "active"
            self.progress = 0
            self.feedback = ""
            self.messages.clear()
            return self.state(clicked="Practice again")
        if action == "fill":
            self.composer = request.get("reply", "")
            return self.state(filled=True)
        if action == "send":
            if self.composer:
                self.messages.append(self.composer)
                self.composer = ""
                if self.advance:
                    self.progress = min(len(FIXTURE["tasks"]), self.progress + 1)
            return self.state(clicked="Send")
        if action == "end":
            self.phase = "confirm"
            return self.state(clicked="End Role Play")
        if action == "confirm_end":
            self.phase = "feedback"
            self.ended = True
            self.feedback = FIXTURE["successful_feedback"]
            return self.state(clicked="Confirm End Role Play")
        return self.state()

    async def call(self, method, params):
        if method == "Page.bringToFront":
            return {}
        raise AssertionError(method)


class AutoFinishRolePlayTab(RolePlayTab):
    """Coursera switches straight to final feedback after the first answer."""

    def __init__(self, feedback, *, transient_active=False):
        super().__init__(progress=0, advance=False)
        self.auto_feedback = feedback
        self.transient_active = transient_active
        self.pending_transient = 0

    async def evaluate(self, expression):
        prefix = role_play.ROLE_PLAY_JS + "("
        request = json.loads(expression[len(prefix):-1])
        if request.get("action", "inspect") == "inspect" and self.pending_transient:
            self.pending_transient -= 1
            # During the transition Coursera removes Tasks before the final
            # feedback view appears. Null is not proof of task progress.
            return self.state(phase="active", task_complete=None, tasks=[])
        result = await super().evaluate(expression)
        if request.get("action") == "send":
            self.phase = "feedback"
            self.ended = True
            self.feedback = self.auto_feedback
            # The Tasks panel had not shown 5/5 before disappearing.
            self.progress = 0
            self.pending_transient = int(self.transient_active)
            return self.state(clicked="Send")
        return result


class RolePlayRunner:
    def __init__(self, tab):
        self.tab = tab
        self.checkpoint = {"role_plays": {}}
        self.events = []

    async def verified_item(self, item, wait=0):
        # The bad screenshot is green despite 0/5 task coverage.
        return True

    def event(self, name, **fields):
        self.events.append((name, fields))


async def immediate_sleep(*args, **kwargs):
    return None


def substantive_reply(_runner, _item, state, turn):
    task = state["tasks"][min(state["task_complete"], len(state["tasks"]) - 1)]
    return (f"Regarding {task} I would compare timestamped accuracy by cohort, review data provenance "
            f"and audit logs, preserve evidence, avoid premature accusations, and request the security "
            f"team's support. Step {turn}.")


class RolePlayValidationTests(unittest.TestCase):
    def test_role_play_url_identity_is_pinned(self):
        self.assertEqual(role_play._lesson_url(ITEM), ITEM["url"])
        with self.assertRaises(engine.StopRun):
            role_play._lesson_url({**ITEM, "key": "coach:OTHER"})
        with self.assertRaises(engine.StopRun):
            role_play._lesson_url({**ITEM, "url": "https://example.com/coach/T6QZ8/lesson"})

    def test_fixture_has_five_distinct_tasks_and_initial_zero(self):
        self.assertEqual(FIXTURE["initial_task_complete"], 0)
        self.assertEqual(len(FIXTURE["tasks"]), 5)
        self.assertEqual(len(set(FIXTURE["tasks"])), 5)
        self.assertIn("data drift", FIXTURE["premature_feedback"])

    def test_affirmative_feedback_is_distinct_from_empty_task_feedback(self):
        self.assertTrue(role_play._review_feedback(FIXTURE["successful_feedback"], 5))
        self.assertFalse(role_play._review_feedback(FIXTURE["premature_feedback"], 5))

    def test_live_grounded_reply_feedback_verifies_every_task(self):
        feedback = (
            "Task 1: Present evidence objectively You maintained a high level of objectivity "
            "by focusing on the data rather than jumping to conclusions. You correctly noted "
            "that an accuracy decline alone is not evidence of poisoning. "
            "Task 2: Acknowledge alternative explanations You demonstrated a balanced "
            "perspective by considering multiple technical factors including drift and decay. "
            "Task 3: Recommend investigation steps Your proposed investigation was logical "
            "and well-sequenced. You outlined a clear path for review and retraining. "
            "Task 4: Address insider threat sensitivity You handled the potential human "
            "element with appropriate professional gravity and confidentiality. "
            "Task 5: Request appropriate resources You justified your resource needs by "
            "linking them directly to the investigation's requirements."
        )
        self.assertTrue(role_play._review_feedback(feedback, 5))

    def test_reply_request_contains_rubric_and_current_prompt(self):
        state = RolePlayTab().state()
        state["scenario"] = "The malware detector's accuracy fell; the cause is still unknown."
        state["goal"] = "Explain evidence, alternatives, investigation steps, and needed help."
        request = role_play._answer_payload(ITEM, state, 1)
        instructions = request["messages"][0]["content"]
        scenario = json.loads(request["messages"][1]["content"])
        self.assertEqual(scenario["tasks"], FIXTURE["tasks"])
        self.assertEqual(scenario["completed_tasks"], 0)
        self.assertEqual(scenario["scenario"], state["scenario"])
        self.assertEqual(scenario["goal"], state["goal"])
        self.assertIn(FIXTURE["opening_prompt"], scenario["coach_and_conversation_excerpt"])
        self.assertIn("Do not invent access to logs", instructions)
        self.assertNotIn("suspected data poisoning scenario", instructions.lower())

    def test_role_play_javascript_is_syntactically_valid(self):
        result = subprocess.run(["node", "--check", "-"],
                                input="const f=" + role_play.ROLE_PLAY_JS + ";",
                                text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dom_end_button_refuses_zero_of_five_tasks(self):
        script = "const run=" + role_play.ROLE_PLAY_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa/coach/T6QZ8/the-insider-threat-investigating-suspected-data-poisoning';
let complete=0,clicks=0;
const e=text=>({innerText:text,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null}});
const button=e('End Role Play');button.click=()=>clicks++;
const rows=['Present evidence','Discuss alternatives','Recommend steps','Address threats','Request resources'].map((x,i)=>e((i+1)+' '+x));
const panel=e('');
Object.defineProperty(panel,'innerText',{get(){return 'Tasks '+complete+'/5 complete '+rows.map(x=>x.innerText).join(' ')+' Ask for a hint'}});
panel.querySelectorAll=selector=>selector==='li'?rows:[];
const main=e('Role Play');
global.location=new URL(url);
global.document={readyState:'complete',body:e('Role Play Tasks'),
  querySelector:s=>s==='main,[role="main"]'?main:null,
  querySelectorAll:s=>{
    if(s==='button,[role="button"]')return [button];
    if(s==='aside,section,[role="complementary"],div')return [panel];
    return [];
  }};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const blocked=run({url,action:'end',expected_total:5});
complete=5;
const allowed=run({url,action:'end',expected_total:5});
process.stdout.write(JSON.stringify({blocked:!!blocked.error,allowed:allowed.clicked,clicks}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "blocked": True, "allowed": "End Role Play", "clicks": 1})

    def test_dom_live_start_activity_is_classified_as_role_play(self):
        script = "const run=" + role_play.ROLE_PLAY_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/example-course/coach/ROLE/lesson';
let clicks=0;
const e=text=>({innerText:text,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null}});
const rolePlayItem=e('Role Play');
const start=e('Start activity');start.click=()=>clicks++;
global.location=new URL(url);
global.document={readyState:'complete',body:e('Role Play Start activity'),
  querySelector:()=>null,querySelectorAll:s=>{
    if(s==='button,[role="button"]')return [start];
    if(s==='[data-testid="learner-role-play-item"]')return [rolePlayItem];
    if(s==='[data-testid="coach-item-start-button"]')return [start];
    return [];
  }};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const before=run({url,action:'inspect'});
const started=run({url,action:'start'});
process.stdout.write(JSON.stringify({kind:before.kind,phase:before.phase,
  startCount:before.start_count,clicked:started.clicked,clicks}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "kind": "role_play", "phase": "intro", "startCount": 1,
            "clicked": "Start Role Play", "clicks": 1})

    def test_dom_finished_dialogue_with_try_again_is_not_role_play(self):
        script = "const run=" + role_play.ROLE_PLAY_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/example-course/coach/DIALOGUE/lesson';
const e=text=>({innerText:text,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null}});
const again=e('Try again');
global.location=new URL(url);
global.document={readyState:'complete',body:e('The Dialogue has ended. Try again'),
  querySelector:()=>null,querySelectorAll:s=>s==='button,[role="button"]'?[again]:[]};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const state=run({url,action:'inspect'});
process.stdout.write(JSON.stringify({kind:state.kind,rolePlay:state.role_play}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"kind": "dialogue", "rolePlay": False})

    def test_plain_numbered_task_panel_is_read_without_list_elements(self):
        script = "const run=" + role_play.ROLE_PLAY_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa/coach/T6QZ8/the-insider-threat-investigating-suspected-data-poisoning';
const e=text=>({innerText:text,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null}});
const button=e('End Role Play');button.click=()=>{};
const panel=e('Tasks 0/5 complete 1 Present evidence: Describe anomalous behavior and poisoning evidence. '
  +'2 Discuss alternatives: Evaluate drift and decay possibilities. '
  +'3 Recommend steps: Propose audit and review actions. '
  +'4 Address threats: Navigate sensitive insider threat implications. '
  +'5 Request resources: Justify necessary investigation support resources. Ask for a hint');
panel.querySelectorAll=()=>[];
global.location=new URL(url);
global.document={readyState:'complete',body:e('Role Play'),
  querySelector:s=>s==='main,[role="main"]'?e('Role Play'):null,
  querySelectorAll:s=>s==='button,[role="button"]'?[button]:
    s==='aside,section,[role="complementary"],div'?[panel]:[]};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const state=run({url,action:'inspect'});
process.stdout.write(JSON.stringify({kind:state.kind,total:state.task_total,
  complete:state.task_complete,tasks:state.tasks.length,last:state.tasks.at(-1)}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "kind": "role_play", "total": 5, "complete": 0, "tasks": 5,
            "last": "Request resources: Justify necessary investigation support resources."})

    def test_dom_can_fill_before_live_send_message_button_becomes_enabled(self):
        script = "const run=" + role_play.ROLE_PLAY_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa/coach/T6QZ8/the-insider-threat-investigating-suspected-data-poisoning';
const reply='We should compare the observed accuracy decline against a baseline, inspect data provenance and audit logs, and distinguish drift from deliberate poisoning before making accusations.';
let sendClicks=0;
const e=text=>({innerText:text,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null}});
const end=e('End Role Play');
const send=e('Send message');send.disabled=true;send.type='button';send.click=()=>sendClicks++;
const form={querySelectorAll:()=>[]};
class Textarea{
  constructor(){this._value='';this.placeholder='Message Security Operations Manager';
    this.hidden=false;this.inert=false;this.parentElement=null;this.disabled=false;}
  getClientRects(){return [1]}
  getAttribute(){return null}
  closest(){return form}
  dispatchEvent(){send.disabled=false}
  get value(){return this._value}
  set value(v){this._value=v}
}
const composer=new Textarea();
const rows=['Present evidence','Discuss alternatives','Recommend steps','Address threats','Request resources'].map((x,i)=>e((i+1)+' '+x));
const panel=e('Tasks 0/5 complete '+rows.map(x=>x.innerText).join(' ')+' Ask for a hint');
panel.querySelectorAll=s=>s==='li'?rows:[];
const main=e('Role Play');
global.HTMLTextAreaElement=Textarea;global.Event=class Event{constructor(){}};
global.location=new URL(url);
global.document={readyState:'complete',body:e('Role Play Tasks'),
  querySelector:s=>s==='main,[role="main"]'?main:null,
  querySelectorAll:s=>{
    if(s==='button,[role="button"]')return [end,send];
    if(s==='[data-testid="coach-send-message-button"]')return [send];
    if(s==='aside,section,[role="complementary"],div')return [panel];
    if(s==='textarea')return [composer];
    return [];
  }};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const filled=run({url,action:'fill',reply,expected_complete:0,expected_total:5});
const sent=run({url,action:'send',reply,expected_complete:0,expected_total:5});
process.stdout.write(JSON.stringify({filled:filled.filled||false,send:sent.clicked||null,sendClicks}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "filled": True, "send": "Send", "sendClicks": 1})


class RolePlayDraftValidationTests(unittest.IsolatedAsyncioTestCase):
    async def _draft(self, reply):
        response = {"message": {"content": json.dumps({"reply": reply})}}
        runner = RolePlayRunner(RolePlayTab())
        with patch.object(engine, "request_groq_answers", AsyncMock(return_value=response)):
            return await role_play._draft_reply(runner, ITEM, runner.tab.state(), 1)

    def _assert_grounded_fallback(self, reply):
        self.assertIsInstance(reply, str)
        self.assertGreaterEqual(len(reply), 80)
        self.assertLessEqual(len(reply), 2400)
        self.assertIsNone(re.search(r"\d", reply))
        self.assertRegex(reply.lower(), r"(?:drift|decay)")
        self.assertRegex(reply.lower(), r"(?:audit|provenance|logs)")
        self.assertRegex(reply.lower(), r"(?:resource|support|security team)")
        self.assertNotRegex(reply.lower(),
                            r"(?:based on (?:the|our) (?:logs|data)|"
                            r"(?:i|we) (?:reviewed|checked|found|confirmed|ran|interviewed))")

    async def test_unsupported_measurements_use_grounded_fallback(self):
        reply = ("Accuracy fell from 94% to 71% on October 12, and exactly 47 records were "
                 "poisoned. These measurements prove the attack, so I recommend escalating "
                 "the case immediately to the security team.")
        self._assert_grounded_fallback(await self._draft(reply))

    async def test_claimed_log_review_uses_grounded_fallback(self):
        for reply in (
            "Based on the logs we've examined, the suspicious employee altered records. "
            "Our review establishes deliberate poisoning, so we should open a formal case.",
            "Based on the logs we’ve examined, the suspicious employee altered records. "
            "Our review establishes deliberate poisoning, so we should open a formal case.",
            "Manual static analysis confirms that a colleague changed the training set. "
            "We can now treat the performance drop as a deliberate insider attack.",
        ):
            with self.subTest(reply=reply[:35]):
                self._assert_grounded_fallback(await self._draft(reply))

    async def test_fallback_is_deterministic_and_uses_only_visible_scenario(self):
        state = RolePlayTab().state()
        state["scenario"] = ("The malware detector's accuracy declined. The cause is unknown; "
                             "data poisoning is only a hypothesis.")
        state["goal"] = "Discuss evidence, alternatives, investigation, sensitivity, and support."
        first = role_play._grounded_fallback(ITEM, state)
        second = role_play._grounded_fallback(ITEM, state)
        self.assertEqual(first, second)
        self._assert_grounded_fallback(first)
        self.assertNotIn("poisoning is confirmed", first.lower())
        self.assertNotIn("colleague changed", first.lower())

    async def test_three_rejected_drafts_fall_back_without_a_fourth_groq_request(self):
        fabricated = ("We checked the audit logs and confirmed 47 poisoned records on October 12. "
                      "The insider is responsible for the measured decline in model accuracy.")
        response = {"message": {"content": json.dumps({"reply": fabricated})}}
        runner = RolePlayRunner(RolePlayTab())
        with patch.object(engine, "request_groq_answers", AsyncMock(return_value=response)) as groq:
            answer = await role_play._draft_reply(runner, ITEM, runner.tab.state(), 1)
        self.assertEqual(groq.await_count, 3)
        self._assert_grounded_fallback(answer)

    async def test_unrecognized_role_play_does_not_reuse_insider_threat_fallback(self):
        state = RolePlayTab().state()
        other = {**ITEM, "title": "Unrelated Role Play"}
        self.assertIsNone(role_play._grounded_fallback(other, state))
        fabricated = "We reviewed 47 records and confirmed the cause of this unrelated case."
        response = {"message": {"content": json.dumps({"reply": fabricated})}}
        runner = RolePlayRunner(RolePlayTab())
        with patch.object(engine, "request_groq_answers", AsyncMock(return_value=response)):
            with self.assertRaises(engine.StopRun):
                await role_play._draft_reply(runner, other, state, 1)

    async def test_accepts_conditional_investigation_plan(self):
        reply = ("I would compare performance by cohort against an earlier baseline and request "
                 "access to data provenance and audit logs. If the changes align with a new data "
                 "source, drift may explain the drop; if unexpected edits appear, I would ask "
                 "the security team to preserve evidence before drawing conclusions.")
        self.assertEqual(await self._draft(reply), reply)


class RolePlayFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_finish_accepts_positive_five_task_feedback_and_green(self):
        tab = AutoFinishRolePlayTab(FIXTURE["successful_feedback"], transient_active=True)
        runner = RolePlayRunner(tab)
        with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)), \
             patch.object(role_play.asyncio, "sleep", immediate_sleep):
            await role_play.run_role_play(runner, ITEM, max_seconds=30)
        self.assertEqual(tab.progress, 0)
        self.assertEqual(tab.actions.count("send"), 1)
        self.assertNotIn("end", tab.actions)
        self.assertNotIn("confirm_end", tab.actions)
        self.assertNotIn("practice_again", tab.actions)
        self.assertEqual(runner.checkpoint["role_plays"][ITEM["key"]]["phase"], "completed")

    async def test_auto_finish_rejects_negative_feedback_despite_green(self):
        tab = AutoFinishRolePlayTab(FIXTURE["premature_feedback"])
        runner = RolePlayRunner(tab)
        with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)), \
             patch.object(role_play.asyncio, "sleep", immediate_sleep):
            with self.assertRaises(engine.StopRun):
                await role_play.run_role_play(runner, ITEM, max_seconds=5)
        self.assertNotEqual(runner.checkpoint["role_plays"][ITEM["key"]].get("phase"), "completed")
        self.assertNotIn("end", tab.actions)

    async def test_green_positive_feedback_confirms_tasks_without_saved_counter(self):
        tab = RolePlayTab(ended=True, feedback=FIXTURE["successful_feedback"],
                          progress=len(FIXTURE["tasks"]))
        runner = RolePlayRunner(tab)
        runner.checkpoint["role_plays"][ITEM["key"]] = {
            "phase": "feedback_pending", "task_total": len(FIXTURE["tasks"]),
            "task_complete": 0, "tasks": FIXTURE["tasks"]}
        with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)), \
             patch.object(role_play.asyncio, "sleep", immediate_sleep):
            await role_play.run_role_play(runner, ITEM, max_seconds=30)
        self.assertNotIn("practice_again", tab.actions)
        self.assertNotIn("confirm_end", tab.actions)
        self.assertEqual(runner.checkpoint["role_plays"][ITEM["key"]]["phase"], "completed")

    async def test_green_five_of_five_with_failed_feedback_is_not_accepted(self):
        tab = RolePlayTab(ended=True, feedback=FIXTURE["premature_feedback"],
                          progress=len(FIXTURE["tasks"]))
        runner = RolePlayRunner(tab)
        runner.checkpoint["role_plays"][ITEM["key"]] = {
            "phase": "completed", "task_total": len(FIXTURE["tasks"]),
            "task_complete": len(FIXTURE["tasks"]), "tasks": FIXTURE["tasks"]}
        with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)), \
             patch.object(role_play.asyncio, "sleep", immediate_sleep):
            await role_play.run_role_play(runner, ITEM, max_seconds=30)
        self.assertIn("practice_again", tab.actions)
        self.assertEqual(tab.actions.count("confirm_end"), 1)

    async def test_uncertain_reserved_message_is_not_replayed_after_restart(self):
        tab = RolePlayTab(progress=0)
        runner = RolePlayRunner(tab)
        runner.checkpoint["role_plays"][ITEM["key"]] = {
            "phase": "message_pending", "task_total": len(FIXTURE["tasks"]),
            "task_complete": 0, "tasks": FIXTURE["tasks"],
            "pending_reply": "A substantive reply reserved before the browser disconnected."}
        with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)) as draft, \
             patch.object(role_play.asyncio, "sleep", immediate_sleep):
            with self.assertRaisesRegex(engine.StopRun, "previous Role Play message"):
                await role_play.run_role_play(runner, ITEM, max_seconds=5)
        draft.assert_not_awaited()
        self.assertNotIn("send", tab.actions)
        self.assertNotIn("end", tab.actions)

    async def test_green_zero_of_five_is_replayed_before_completion(self):
        tab = RolePlayTab(ended=True, feedback=FIXTURE["premature_feedback"], progress=0)
        runner = RolePlayRunner(tab)
        with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)), \
             patch.object(role_play.asyncio, "sleep", immediate_sleep):
            await role_play.run_role_play(runner, ITEM, max_seconds=30)
        self.assertIn("practice_again", tab.actions)
        self.assertEqual(tab.progress, len(FIXTURE["tasks"]))
        self.assertEqual(len(tab.messages), len(FIXTURE["tasks"]))
        self.assertLess(tab.actions.index("practice_again"), tab.actions.index("end"))
        self.assertLess(tab.actions.index("send"), tab.actions.index("end"))
        self.assertEqual(tab.actions.count("end"), 1)
        self.assertEqual(tab.actions.count("confirm_end"), 1)

    async def test_stalled_zero_of_five_never_ends_even_with_green_icon(self):
        tab = RolePlayTab(progress=0, advance=False)
        runner = RolePlayRunner(tab)
        with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)), \
             patch.object(role_play.asyncio, "sleep", immediate_sleep):
            with self.assertRaises(engine.StopRun):
                await role_play.run_role_play(runner, ITEM, max_seconds=1)
        self.assertNotIn("end", tab.actions)
        self.assertNotIn("confirm_end", tab.actions)


if __name__ == "__main__":
    unittest.main()
