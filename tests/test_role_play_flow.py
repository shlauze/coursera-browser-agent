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

    def __init__(self, *, ended=False, feedback="", progress=0, advance=True,
                 goal_modal=False, goal_dismiss_lag=0):
        self.ended = ended
        self.feedback = feedback
        self.progress = progress
        self.advance = advance
        self.goal_modal = goal_modal
        self.goal_dismiss_lag = goal_dismiss_lag
        self.goal_dismiss_remaining = 0
        self.goal_inspects_after_dismiss = 0
        self.phase = "feedback" if ended else "active"
        self.composer = ""
        self.messages = []
        self.actions = []

    def state(self, **extra):
        transcript = [FIXTURE["opening_prompt"]]
        for message in self.messages:
            transcript.extend((message,
                               "Please continue the investigation and address the remaining tasks."))
        return {"url": ITEM["url"], "ready": "complete", "role_play": True,
                "phase": self.phase, "task_total": len(FIXTURE["tasks"]),
                "task_complete": self.progress, "tasks": FIXTURE["tasks"],
                "latest_prompt": FIXTURE["opening_prompt"] if not self.messages else
                    "Please continue the investigation and address the remaining tasks.",
                "last_coach_message": FIXTURE["opening_prompt"] if not self.messages else
                    "Please continue the investigation and address the remaining tasks.",
                "last_user_message": self.messages[-1] if self.messages else "",
                "user_message_count": len(self.messages), "composer_value": self.composer,
                "last_user_followed_by_coach": bool(self.messages),
                "transcript_valid": True,
                "conversation_text": " ".join(transcript),
                "start_count": 0, "end_count": int(self.phase == "active"),
                "send_count": int(self.phase == "active"),
                "feedback_text": self.feedback, "confirm_count": int(self.phase == "confirm"),
                "practice_again_count": int(self.phase == "feedback" and not self.goal_modal),
                "goal_modal_count": int(self.goal_modal),
                "goal_continue_count": int(self.goal_modal),
                "modal_text": "End the Role Play? Reason not provided Yes, end the Role Play Cancel"
                    if self.phase == "confirm" else "",
                **extra}

    async def evaluate(self, expression):
        prefix = role_play.ROLE_PLAY_JS + "("
        assert expression.startswith(prefix), expression[:100]
        request = json.loads(expression[len(prefix):-1])
        action = request.get("action", "inspect")
        self.actions.append(action)
        if action == "inspect" and self.goal_dismiss_remaining:
            self.goal_inspects_after_dismiss += 1
            self.goal_dismiss_remaining -= 1
            if self.goal_dismiss_remaining == 0:
                self.goal_modal = False
        if action == "dismiss_goal":
            if not self.goal_modal:
                return self.state(error="The exact daily-goal modal was absent.")
            if self.goal_dismiss_lag:
                self.goal_dismiss_remaining = self.goal_dismiss_lag
            else:
                self.goal_modal = False
            return self.state(clicked="dismiss_goal")
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


class TextChoiceRolePlayTab(RolePlayTab):
    """An introduction or retry that shows a text/voice selection first."""

    def __init__(self, *, initial_phase="intro", stalled=False,
                 active_without_composer=False):
        super().__init__(ended=initial_phase == "feedback",
                         feedback=FIXTURE["premature_feedback"] if
                         initial_phase == "feedback" else "")
        self.phase = initial_phase
        self.stalled = stalled
        self.active_without_composer = active_without_composer
        self.voice_clicks = 0

    def state(self, **extra):
        active = self.phase == "active"
        return super().state(
            start_count=int(self.phase == "intro"),
            text_choice_count=int(self.phase == "text_choice"),
            voice_choice_count=int(self.phase == "text_choice"),
            composer_count=int(active and not self.active_without_composer),
            send_count=int(active and not self.active_without_composer),
            end_count=int(active),
            **extra,
        )

    async def evaluate(self, expression):
        prefix = role_play.ROLE_PLAY_JS + "("
        request = json.loads(expression[len(prefix):-1])
        operation = request.get("action", "inspect")
        if operation == "start":
            self.actions.append(operation)
            assert self.phase == "intro"
            self.phase = "text_choice"
            return self.state(clicked="Start Role Play")
        if operation == "use_text_chat":
            self.actions.append(operation)
            assert self.phase == "text_choice"
            if not self.stalled:
                self.phase = "active"
            return self.state(clicked="Use text chat")
        if operation == "practice_again":
            result = await super().evaluate(expression)
            self.phase = "text_choice"
            return self.state(clicked=result["clicked"])
        if operation in ("fill", "send"):
            assert self.phase == "active", "No message before text chat is active"
        return await super().evaluate(expression)


class MissingReplyAfterSendTab(RolePlayTab):
    """A sent reply and task counter both disappear from the next render."""

    def __init__(self):
        super().__init__(progress=0, advance=False)
        self.uncertain = False

    async def evaluate(self, expression):
        prefix = role_play.ROLE_PLAY_JS + "("
        request = json.loads(expression[len(prefix):-1])
        operation = request.get("action", "inspect")
        if operation == "inspect" and self.uncertain:
            self.actions.append(operation)
            return self.state(task_total=None, task_complete=None, tasks=[],
                              conversation_text="", last_user_message="",
                              user_message_count=0,
                              last_user_followed_by_coach=False,
                              transcript_valid=False)
        result = await super().evaluate(expression)
        if operation == "send":
            self.uncertain = True
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

    def test_product_team_role_play_feedback_accepts_five_affirmative_sections(self):
        feedback = (
            "Task 1: Explain risk to the product team. You effectively communicated the model's "
            "security risks in plain language and kept the team focused on observable behavior. "
            "Task 2: Make the concepts accessible. You used excellent analogies to explain "
            "model drift and monitoring so the stakeholders could follow the discussion. "
            "Task 3: Connect risk and impact. You successfully linked technical model behavior "
            "to concrete product outcomes and gave stakeholders a useful basis for decisions. "
            "Task 4: Recommend action. You provided a practical sequence of review, validation, "
            "and measured deployment steps with clear ownership. "
            "Task 5: Communicate professionally. You maintained a professional tone, invited "
            "questions, and kept uncertainty visible while requesting appropriate support."
        )
        self.assertTrue(role_play._review_feedback(feedback, 5))

    def test_product_team_feedback_does_not_accept_unqualified_used_jargon(self):
        feedback = (
            "Task 1: Explain risk to the product team. You effectively communicated the model's "
            "security risks in plain language and kept the team focused on observable behavior. "
            "Task 2: Make the concepts accessible. You used jargon without explaining the terms, "
            "which confused stakeholders and obscured the risk rather than clarifying it. "
            "Task 3: Connect risk and impact. You successfully linked technical model behavior "
            "to concrete product outcomes and gave stakeholders a useful basis for decisions. "
            "Task 4: Recommend action. You provided a practical sequence of review, validation, "
            "and measured deployment steps with clear ownership. "
            "Task 5: Communicate professionally. You maintained a professional tone, invited "
            "questions, and kept uncertainty visible while requesting appropriate support."
        )
        self.assertFalse(role_play._review_feedback(feedback, 5))

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
const rolePlayItem=e('Role Play');rolePlayItem.querySelectorAll=()=>[];
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

    def test_dom_text_chat_choice_is_exact_and_never_uses_voice(self):
        script = "const run=" + role_play.ROLE_PLAY_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa/coach/T6QZ8/the-insider-threat-investigating-suspected-data-poisoning';
const state={invalid:null,textClicks:0,voiceClicks:0,sendClicks:0};
const e=text=>({innerText:text,hidden:false,inert:false,parentElement:null,
 disabled:false,getClientRects(){return [{}]},getAttribute(){return null}});
const textButton=e('Use text chat');textButton.click=()=>state.textClicks++;
const voiceButton=e('Join voice chat');voiceButton.click=()=>state.voiceClicks++;
const duplicateText=e('Use text chat');duplicateText.click=()=>state.textClicks++;
const duplicateVoice=e('Join voice chat');duplicateVoice.click=()=>state.voiceClicks++;
const tasks=['Present evidence','Discuss alternatives','Recommend steps',
 'Address threats','Request resources'];
const rows=tasks.map((task,index)=>e((index+1)+' '+task));
const panel=e('');
Object.defineProperty(panel,'innerText',{get(){return 'Tasks '+
 (state.invalid==='changed_tasks'?'1':'0')+'/5 complete '+
 rows.map(row=>row.innerText).join(' ')+' Ask for a hint'}});
panel.querySelectorAll=selector=>selector==='li'?rows:[];
const root=e('Chief Information Security Officer is ready to begin. Review the tasks and join the chat when ready.');
root.contains=node=>node===panel;
root.querySelectorAll=selector=>
 selector==='[data-testid="use-text-chat-button"]'?
  (state.invalid==='missing_text'?[]:
   state.invalid==='duplicate_text'?[textButton,duplicateText]:[textButton]):
 selector==='[data-testid="join-conversation-button"]'?
  (state.invalid==='missing_voice'?[]:
   state.invalid==='duplicate_voice'?[voiceButton,duplicateVoice]:
   state.invalid==='wrong_voice_label'?[e('Voice only')]:[voiceButton]):[];
Object.defineProperty(root,'innerText',{get(){return state.invalid==='changed_root'?
 'This is an unrelated page':
 'Chief Information Security Officer is ready to begin. Review the tasks and join the chat when ready.'}});
global.location=new URL(url);
global.document={readyState:'complete',body:e('Role Play Tasks'),
 querySelector:selector=>selector==='main,[role="main"]'?root:null,
 querySelectorAll:selector=>selector==='button,[role="button"]'?
  [textButton,voiceButton]:
  selector==='[data-testid="learner-role-play-item"]'?[root]:
  selector==='aside,section,[role="complementary"],div'?[panel]:[]};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const before=run({url,action:'inspect'});
const blockedFill=run({url,action:'fill',reply:'A substantive response'.repeat(10),
 expected_complete:0,expected_total:5});
const blockedSend=run({url,action:'send',reply:'A substantive response'.repeat(10),
 expected_complete:0,expected_total:5});
const chosen=run({url,action:'use_text_chat'});
const valid={phase:before.phase,kind:before.kind,total:before.task_total,
 complete:before.task_complete,textCount:before.text_choice_count,
 voiceCount:before.voice_choice_count,composerCount:before.composer_count,
 fillBlocked:!!blockedFill.error,sendBlocked:!!blockedSend.error,
 clicked:chosen.clicked,textClicks:state.textClicks,
 voiceClicks:state.voiceClicks,sendClicks:state.sendClicks};
const invalid={};
for(const kind of ['missing_text','duplicate_text','missing_voice',
 'duplicate_voice','wrong_voice_label','changed_tasks','changed_root']){
 state.invalid=kind;
 const observed=run({url,action:'inspect'});
 const attempted=run({url,action:'use_text_chat'});
 invalid[kind]={phase:observed.phase,rolePlay:observed.role_play,
  error:attempted.error||null};
 state.invalid=null;
}
const wrongPage=(()=>{global.location=new URL('https://www.coursera.org/learn/other/coach/T6QZ8/lesson');
 return run({url,action:'use_text_chat'})})();
process.stdout.write(JSON.stringify({valid,invalid,wrongPage,
 textClicks:state.textClicks,voiceClicks:state.voiceClicks,
 sendClicks:state.sendClicks}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshots = json.loads(result.stdout)
        self.assertEqual(snapshots["valid"], {
            "phase": "text_choice", "kind": "role_play", "total": 5,
            "complete": 0, "textCount": 1, "voiceCount": 1,
            "composerCount": 0, "fillBlocked": True,
            "sendBlocked": True, "clicked": "Use text chat",
            "textClicks": 1, "voiceClicks": 0, "sendClicks": 0,
        })
        for kind, snapshot in snapshots["invalid"].items():
            with self.subTest(kind=kind):
                self.assertNotEqual(snapshot["phase"], "text_choice")
                self.assertIsNotNone(snapshot["error"])
        self.assertIn("error", snapshots["wrongPage"])
        self.assertEqual(snapshots["textClicks"], 1)
        self.assertEqual(snapshots["voiceClicks"], 0)
        self.assertEqual(snapshots["sendClicks"], 0)

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

    def test_completed_dialogue_is_classified_under_daily_goal_modal(self):
        script = "const run=" + role_play.ROLE_PLAY_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/example-course/coach/DIALOGUE/lesson';
const e=text=>({innerText:text,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null}});
const hiddenAncestor=e('');hiddenAncestor.getAttribute=name=>
  name==='aria-hidden'?'true':null;
const again=e('Try again');again.parentElement=hiddenAncestor;
const panel=e('Try again');panel.parentElement=hiddenAncestor;
panel.querySelectorAll=selector=>selector==='button,[role="button"]'?[again]:[];
const completed=e('The Dialogue has ended. Try again');
completed.parentElement=hiddenAncestor;
completed.querySelectorAll=selector=>
  selector==='[data-testid="chat-end-buttons"]'?[panel]:[];
const modal=e("You've completed today's goals!");
modal.querySelectorAll=()=>[];
global.location=new URL(url);
global.document={readyState:'complete',body:e('The Dialogue has ended.'),
  querySelector:()=>null,querySelectorAll:s=>{
    if(s==='button,[role="button"]')return [again];
    if(s==='[data-testid="dialogue-content"]')return [completed];
    if(s==='[role="dialog"],[aria-modal="true"]')return [modal];
    return [];
  }};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const state=run({url,action:'inspect'});
process.stdout.write(JSON.stringify({kind:state.kind,rolePlay:state.role_play,
  endCount:state.end_count,startCount:state.start_count}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "kind": "dialogue", "rolePlay": False, "endCount": 0, "startCount": 0})

    def test_completed_role_play_feedback_is_read_only_under_daily_goal_modal(self):
        script = "const run=" + role_play.ROLE_PLAY_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/example-course/coach/ROLE/lesson';
const e=text=>({innerText:text,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null}});
const hiddenAncestor=e('');hiddenAncestor.getAttribute=name=>
  name==='aria-hidden'?'true':null;
let clicks=0;
const again=e('Practice again');again.parentElement=hiddenAncestor;
again.click=()=>clicks++;
const panel=e('Practice again');panel.parentElement=hiddenAncestor;
panel.querySelectorAll=selector=>selector==='button,[role="button"]'?[again]:[];
const completed=e('The Role Play activity has ended. Task 1: You described the evidence clearly.');
completed.parentElement=hiddenAncestor;
completed.querySelectorAll=selector=>
  selector==='[data-testid="chat-end-buttons"]'?[panel]:[];
const modal=e("You've completed today's goals!");modal.querySelectorAll=()=>[];
global.location=new URL(url);
global.document={readyState:'complete',body:e('The Role Play activity has ended.'),
  querySelector:()=>null,querySelectorAll:s=>{
    if(s==='button,[role="button"]')return [again];
    if(s==='[data-testid="learner-role-play-item"]')return [completed];
    if(s==='[role="dialog"],[aria-modal="true"]')return [modal];
    return [];
  }};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const state=run({url,action:'inspect'});
const refused=run({url,action:'practice_again'});
process.stdout.write(JSON.stringify({kind:state.kind,phase:state.phase,
  feedback:state.feedback_text,practiceCount:state.practice_again_count,
  refused:!!refused.error,clicks}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["kind"], "role_play")
        self.assertEqual(value["phase"], "feedback")
        self.assertIn("Task 1:", value["feedback"])
        self.assertEqual(value["practiceCount"], 0)
        self.assertTrue(value["refused"])
        self.assertEqual(value["clicks"], 0)

    def test_daily_goal_modal_is_dismissed_only_with_exact_continue_learning_button(self):
        script = "const run=" + role_play.ROLE_PLAY_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/example-course/coach/ROLE/lesson';
const e=text=>({innerText:text,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null}});
let hidden=true,goalClicks=0,practiceClicks=0;
const backdrop=e('');backdrop.getAttribute=name=>name==='aria-hidden'&&hidden?'true':null;
const again=e('Practice again');again.parentElement=backdrop;
again.click=()=>practiceClicks++;
const endButtons=e('Practice again');endButtons.parentElement=backdrop;
endButtons.querySelectorAll=s=>s==='button,[role="button"]'?[again]:[];
const completed=e('The Role Play activity has ended. Task 1: No evidence was given.');
completed.parentElement=backdrop;
completed.querySelectorAll=s=>s==='[data-testid="chat-end-buttons"]'?[endButtons]:[];
const continueButton=e('Continue learning');
continueButton.click=()=>{goalClicks++;modal.hidden=true;hidden=false};
const modal=e("You've completed today's goals! Wrong wording. Continue learning");
modal.querySelectorAll=s=>s==='button,[role="button"]'?[continueButton]:[];
global.location=new URL(url);
global.document={readyState:'complete',body:e('The Role Play activity has ended.'),
  querySelector:()=>null,querySelectorAll:s=>{
    if(s==='button,[role="button"]')return [again,continueButton];
    if(s==='[data-testid="learner-role-play-item"]')return [completed];
    if(s==='[role="dialog"],[aria-modal="true"]')return [modal];
    if(s==='[role="dialog"][aria-modal="true"][data-testid="scroll-container"]')return [modal];
    return [];
  }};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const wrong=run({url,action:'dismiss_goal'});
const before=run({url,action:'inspect'});
modal.innerText="You've completed today's goals! Continue to use this momentum to get you closer to completing this course. Continue learning";
const dismissed=run({url,action:'dismiss_goal'});
const after=run({url,action:'inspect'});
const retried=run({url,action:'practice_again'});
process.stdout.write(JSON.stringify({wrong:!!wrong.error,initialGoalClicks:wrong.goal_modal_count,
  beforePractice:before.practice_again_count,dismissed:dismissed.clicked,
  afterPractice:after.practice_again_count,afterGoal:after.goal_modal_count,
  retried:retried.clicked,goalClicks,practiceClicks}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "wrong": True, "initialGoalClicks": 0, "beforePractice": 0,
            "dismissed": "dismiss_goal", "afterPractice": 1, "afterGoal": 0,
            "retried": "Practice again", "goalClicks": 1, "practiceClicks": 1})

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

    def test_semantic_task_rows_validate_order_status_and_counter(self):
        script = "const run=" + role_play.ROLE_PLAY_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa/coach/RyZW9/after-the-red-team-presenting-security-test-findings-to-leadership';
const names=[
 'Present methodology: Explain testing methods and attack vectors.',
 'Report findings: State vulnerabilities with severity ratings.',
 'Explain findings: Translate technical details for executives.',
 'Recommend remediation: Propose prioritized security improvements.',
 'Address questions: Defend testing rigor and confidence.'];
const state={invalid:null,sendClicks:0};
const e=text=>({innerText:text,hidden:false,inert:false,parentElement:null,
 disabled:false,getClientRects(){return [{}]},getAttribute(){return null}});
const rows=names.map((name,index)=>{
 const row=e(index===1?'2\n'+name:name);
 row.getAttribute=key=>key==='aria-label'?
  (state.invalid==='wrong_status'&&index===0?'Finished task: '+name:
   state.invalid==='wrong_number'&&index===1?'Task 3: '+name:
   state.invalid==='wrong_description'&&index===2?
     'Completed task: Different description':
   (index===1?'Task 2: ':'Completed task: ')+name):null;
 const child=e(name);
 child.getAttribute=key=>key==='data-testid'?
  (index===1?'todo-task-':'completed-task-')+name:null;
 row.querySelectorAll=selector=>selector==='span,[data-testid]'?[child]:[];
 return row;
});
const list=e('');
rows.forEach(row=>row.parentElement=list);
const extraRow=e('Unexpected task');extraRow.parentElement=list;
list.querySelectorAll=selector=>selector.includes('listitem')?
 (state.invalid==='extra_row'?[...rows,extraRow]:
  state.invalid==='duplicate_row'?[rows[0],rows[1],rows[0],rows[3],rows[4]]:rows):[];
const panel=e('');
list.parentElement=panel;
Object.defineProperty(panel,'innerText',{get(){return rows.map(row=>row.innerText).join(' ')}});
panel.querySelectorAll=selector=>selector.includes('role="list"')||
 selector.includes('role=list')?[list]:
 selector.includes('listitem')?list.querySelectorAll(selector):
 selector==='li'?[]:[];
panel.querySelector=selector=>selector.includes('role="list"')||
 selector.includes('role=list')?list:null;
const taskTab=e('');
panel.parentElement=taskTab;
taskTab.contains=node=>node===panel||node===list||rows.includes(node);
Object.defineProperty(taskTab,'innerText',{get(){return 'Tasks '+
  (state.invalid==='counter_mismatch'?'3':'4')+'/5 complete '+panel.innerText}});
const root=e('Role Play');
root.contains=node=>node===taskTab||taskTab.contains(node);
taskTab.parentElement=root;
Object.defineProperty(root,'innerText',{get(){return 'Role Play '+taskTab.innerText}});
root.querySelectorAll=selector=>selector==='[data-testid="task-list"]'?
 [panel]:selector==='[role="tabpanel"]#tabpanel-tasks[aria-labelledby="tab-tasks"]'?
 [taskTab]:[];
const end=e('End Role Play');
const send=e('Send message');send.type='button';send.click=()=>state.sendClicks++;
const composer=e('');composer.placeholder='Message Chief Information Security Officer';
composer.value='A substantive reply explaining testing methods and priorities.';
composer.closest=selector=>selector==='form'?{
 querySelectorAll:query=>query==='button,[role="button"]'?[send]:[]}:null;
global.location=new URL(url);
global.document={readyState:'complete',body:e('Role Play Tasks'),
 querySelector:selector=>selector==='main,[role="main"]'?root:null,
 querySelectorAll:selector=>selector==='button,[role="button"]'?[end,send]:
  selector==='[data-testid="learner-role-play-item"]'?[root]:
  selector==='[data-testid="task-list"]'?[panel]:
  selector==='aside,section,[role="complementary"],div'?[panel]:
  selector==='textarea'?[composer]:[]};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const valid=run({url,action:'inspect'});
const invalid={};
for(const kind of ['counter_mismatch','extra_row','duplicate_row','wrong_status',
 'wrong_number','wrong_description']){
 state.invalid=kind;
 const observed=run({url,action:'inspect'});
 const attempted=run({url,action:'send',reply:composer.value,
  expected_complete:4,expected_total:5});
 invalid[kind]={total:observed.task_total,complete:observed.task_complete,
  tasks:observed.tasks,error:attempted.error||null};
 state.invalid=null;
}
process.stdout.write(JSON.stringify({valid:{total:valid.task_total,
 complete:valid.task_complete,tasks:valid.tasks},invalid,
 sendClicks:state.sendClicks}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshots = json.loads(result.stdout)
        self.assertEqual(snapshots["valid"], {
            "total": 5, "complete": 4,
            "tasks": [
                "Present methodology: Explain testing methods and attack vectors.",
                "Report findings: State vulnerabilities with severity ratings.",
                "Explain findings: Translate technical details for executives.",
                "Recommend remediation: Propose prioritized security improvements.",
                "Address questions: Defend testing rigor and confidence.",
            ],
        })
        for kind, snapshot in snapshots["invalid"].items():
            with self.subTest(kind=kind):
                self.assertNotEqual(snapshot["tasks"], snapshots["valid"]["tasks"])
                self.assertIsNotNone(snapshot["error"])
        self.assertEqual(snapshots["sendClicks"], 0)

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
    async def test_start_activity_selects_text_before_sending(self):
        tab = TextChoiceRolePlayTab(initial_phase="intro")
        runner = RolePlayRunner(tab)
        with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)), \
             patch.object(role_play.asyncio, "sleep", immediate_sleep):
            await role_play.run_role_play(runner, ITEM, max_seconds=30)
        self.assertLess(tab.actions.index("start"), tab.actions.index("use_text_chat"))
        self.assertLess(tab.actions.index("use_text_chat"), tab.actions.index("fill"))
        self.assertLess(tab.actions.index("use_text_chat"), tab.actions.index("send"))
        self.assertEqual(tab.voice_clicks, 0)
        self.assertEqual(len(tab.messages), len(FIXTURE["tasks"]))
        self.assertEqual(runner.checkpoint["role_plays"][ITEM["key"]]["phase"],
                         "completed")

    async def test_resumed_text_choice_and_feedback_retry_select_text(self):
        for initial_phase in ("text_choice", "feedback"):
            with self.subTest(initial_phase=initial_phase):
                tab = TextChoiceRolePlayTab(initial_phase=initial_phase)
                runner = RolePlayRunner(tab)
                with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)), \
                     patch.object(role_play.asyncio, "sleep", immediate_sleep):
                    await role_play.run_role_play(runner, ITEM, max_seconds=30)
                self.assertEqual(tab.actions.count("use_text_chat"), 1)
                self.assertNotIn("start", tab.actions)
                if initial_phase == "feedback":
                    self.assertLess(tab.actions.index("practice_again"),
                                    tab.actions.index("use_text_chat"))
                self.assertLess(tab.actions.index("use_text_chat"),
                                tab.actions.index("send"))
                self.assertEqual(tab.voice_clicks, 0)

    async def test_text_choice_wait_is_bounded_and_sends_nothing(self):
        for failure in ("stalled", "active_without_composer"):
            with self.subTest(failure=failure):
                tab = TextChoiceRolePlayTab(
                    initial_phase="text_choice",
                    stalled=failure == "stalled",
                    active_without_composer=failure == "active_without_composer",
                )
                runner = RolePlayRunner(tab)
                with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)) as draft, \
                     patch.object(role_play.asyncio, "sleep", immediate_sleep):
                    with self.assertRaisesRegex(engine.StopRun, "bounded wait"):
                        await role_play.run_role_play(runner, ITEM, max_seconds=5)
                draft.assert_not_awaited()
                self.assertNotIn("fill", tab.actions)
                self.assertNotIn("send", tab.actions)
                self.assertEqual(tab.voice_clicks, 0)

    async def test_incomplete_feedback_under_daily_goal_modal_dismisses_then_retries(self):
        tab = RolePlayTab(ended=True, feedback=FIXTURE["premature_feedback"],
                          progress=0, goal_modal=True)
        runner = RolePlayRunner(tab)
        with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)), \
             patch.object(role_play.asyncio, "sleep", immediate_sleep):
            await role_play.run_role_play(runner, ITEM, max_seconds=30)
        self.assertFalse(tab.goal_modal)
        self.assertEqual(tab.progress, len(FIXTURE["tasks"]))
        self.assertLess(tab.actions.index("dismiss_goal"), tab.actions.index("practice_again"))
        self.assertLess(tab.actions.index("practice_again"), tab.actions.index("send"))
        self.assertEqual(tab.actions.count("confirm_end"), 1)
        self.assertEqual(runner.checkpoint["role_plays"][ITEM["key"]]["phase"], "completed")

    async def test_retry_waits_for_daily_goal_modal_to_disappear(self):
        tab = RolePlayTab(ended=True, feedback=FIXTURE["premature_feedback"],
                          progress=0, goal_modal=True, goal_dismiss_lag=3)
        runner = RolePlayRunner(tab)
        with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)), \
             patch.object(role_play.asyncio, "sleep", immediate_sleep):
            await role_play.run_role_play(runner, ITEM, max_seconds=30)
        self.assertEqual(tab.goal_inspects_after_dismiss, 3)
        self.assertFalse(tab.goal_modal)
        self.assertLess(tab.actions.index("dismiss_goal"), tab.actions.index("practice_again"))
        self.assertEqual(tab.actions.count("practice_again"), 1)
        self.assertEqual(runner.checkpoint["role_plays"][ITEM["key"]]["phase"], "completed")

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

    async def test_missing_post_send_reply_and_counter_preserve_reservation(self):
        tab = MissingReplyAfterSendTab()
        runner = RolePlayRunner(tab)
        with patch.object(role_play, "_draft_reply", AsyncMock(side_effect=substantive_reply)), \
             patch.object(role_play.asyncio, "sleep", immediate_sleep):
            with self.assertRaises(engine.StopRun):
                await role_play.run_role_play(runner, ITEM, max_seconds=30)
        ledger = runner.checkpoint["role_plays"][ITEM["key"]]
        self.assertEqual(ledger["phase"], "message_pending")
        self.assertTrue(ledger["pending_reply"])
        self.assertEqual(tab.actions.count("send"), 1)
        self.assertEqual(len(tab.messages), 1)
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
