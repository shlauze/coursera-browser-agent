"""Focused offline checks for optional text-only Practice Assignments."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import coursera_start as engine
import reflection_flow as reflection


ITEM = {
    "key": "assignment:GhNSw",
    "url": "https://www.coursera.org/learn/explainable-machine-learning-xai/assignment-submission/GhNSw/interests-and-aspirations-in-xai-optional",
    "title": "Interests and Aspirations in XAI (Optional) Practice Assignment • 10 min • Grade: --",
}
QUESTIONS = [
    "What are you most interested in learning about in this course?",
    "What problem or problems are you hoping to solve with XAI?",
]
ANSWERS = [
    "Understanding how explanations make model predictions easier to interpret would be a useful learning goal.",
    "An example is checking whether a model relies on relevant features when making a consequential prediction.",
]


NODE_CONFIRM_DOM = r"""
const fs=require('node:fs');
const vm=require('node:vm');
const payload=JSON.parse(fs.readFileSync(0,'utf8'));
const clicks=[];
const rect=()=>[{}];
const style={display:'block',visibility:'visible'};
const text=(id,value,parent,shown=true)=>({id,value,parentElement:parent,readOnly:false,
  disabled:false,maxLength:10000,labels:[],placeholder:'What do you think?',
  getClientRects:()=>shown?rect():[],getAttribute:key=>key==='aria-labelledby'?'prompt':null,
  closest:()=>null});
const body={innerText:payload.title+' Practice Assignment '+payload.question+' '+
  (payload.attestation||'')+' Ready to submit? Submit Cancel',
  parentElement:null,getAttribute:()=>null};
const hidden={parentElement:body,getAttribute:key=>key==='aria-hidden'?'true':null};
const answer=text('text-area-autoGradableResponseId~test',payload.answer,body);
const chat=text('boostai-chat-panel-composer','',hidden);
const clone=text('autosize-clone','x',hidden);
clone.readOnly=true;
const button=(name,testid,parent)=>({innerText:name,parentElement:parent,disabled:false,readOnly:false,
  getClientRects:rect,getAttribute:key=>key==='data-testid'?testid:null,
  closest:()=>null,click:()=>clicks.push(name)});
const dialog={innerText:'Ready to submit? Submit Cancel',parentElement:body,
  getClientRects:rect,getAttribute:key=>({role:'alertdialog','aria-modal':'true',
    'data-testid':'scroll-container'})[key]||null,
  querySelector:()=>null,
  querySelectorAll:selector=>selector==='button,[role="button"]'&&!payload.processing?[confirm,cancel]:[]};
const confirm=button('Submit','dialog-submit-button',dialog);
const cancel=button('Cancel','dialog-cancel-button',dialog);
const firstSubmit=button('Submit','main-submit-button',body);
body.querySelectorAll=selector=>{
  if(selector==='textarea') return [chat,clone,answer];
  if(selector==='textarea[id^="text-area-autoGradableResponseId~"]') return [answer];
  if(selector==='button,[role="button"],input[type="submit"]') return [firstSubmit,confirm,cancel];
  return [];
};
const document={body,readyState:'complete',getElementById:id=>id==='prompt'?{innerText:payload.question}:null,
  querySelectorAll:selector=>{
    if(selector==='h1,h2,[role="heading"]') return [];
    if(selector==='[role="alertdialog"][aria-modal="true"][data-testid="scroll-container"]') return [dialog];
    return [];
  }};
const context={document,location:new URL(payload.url+'/attempt'),URL,getComputedStyle:()=>style};
const fn=vm.runInNewContext(payload.code,context);
const common={url:payload.url,title:payload.title,authorized_notice:''};
const before=fn({...common,action:'inspect'});
const result=payload.action==='fill'
  ? fn({...common,action:'fill',snapshot_id:before.snapshot_id,
      answers:[{id:'t0',reply:payload.reply}]})
  : fn({...common,action:'confirm_submit',snapshot_id:before.snapshot_id,
      expected_confirmation:before.confirmation,answers:[{id:'t0',reply:payload.answer}]});
process.stdout.write(JSON.stringify({before,result,clicks}));
"""


class ReflectionTab:
    def __init__(self, *, confirmation=False, green=True, mixed=False, existing=False,
                 ambiguous_entry=False, pending=False, entry_delay=0,
                 recover_confirmation=False, incomplete_recovery=False,
                 processing_confirmation=False, result_delay=0, form_delay=0,
                 offscreen=False, submit_error=False, confirm_error=False, authorship=False,
                 submit_transport_error=False):
        self.started = recover_confirmation
        self.confirmation = confirmation or recover_confirmation
        self.processing_confirmation = processing_confirmation
        self.green = green
        self.mixed = mixed
        self.ambiguous_entry = ambiguous_entry
        self.pending = pending
        self.result_delay = result_delay
        self.entry_delay = entry_delay
        self.form_delay = form_delay
        self.offscreen = offscreen
        self.submit_error = submit_error
        self.confirm_error = confirm_error
        self.authorship = authorship
        self.submit_transport_error = submit_transport_error
        self.values = ([ANSWERS[0], "" if incomplete_recovery else ANSWERS[1]]
                       if recover_confirmation else
                       ["Saved learner answer", ""] if existing else ["", ""])
        self.submitted = recover_confirmation
        self.confirmed = False
        self.actions = []
        self.marker_provider = None
        self.marker_at_submit = None
        self.marker_at_confirm = None

    def _state(self, **fields):
        attempt = self.started
        return {"url": ITEM["url"] + ("/attempt" if attempt else ""),
                "ready": "complete", "title_visible": True, "practice": True,
                "attempt": attempt,
                "fields": [{"id": f"t{i}", "question": q, "value": self.values[i],
                            "disabled": False, "maxLength": 10000}
                           for i, q in enumerate(QUESTIONS)] if attempt and not self.form_delay else [],
                "offscreen_form": self.offscreen and attempt and not self.form_delay,
                "offscreen_draft": self.offscreen and any(self.values),
                "unsupported": [{"type": "radio", "label": "Other"}] if self.mixed and attempt else [],
                "notice_count": 0, "notice_checked": None,
                "manual_submission_required": self.authorship,
                "authorship_statement": (
                    "By clicking Submit, you confirm this work is your own."
                    if self.authorship else None),
                "entry_labels": (["Start assignment", "Resume assignment"]
                                 if self.ambiguous_entry else
                                 ([] if self.entry_delay else ["Start assignment"])) if not attempt else [],
                "submit_count": 1 if attempt and not self.submitted else 0,
                "confirmation": "Ready to submit? Submit Cancel" if self.submitted and self.confirmation and not self.confirmed and not self.processing_confirmation else None,
                "submission_dialog": self.submitted and self.confirmation and not self.confirmed,
                "result_page": self.submitted and not self.pending and (not self.confirmation or self.confirmed),
                "snapshot_id": "stable-form", **fields}

    async def evaluate(self, expression):
        request = json.loads(expression[len(reflection.REFLECTION_JS) + 1:-1])
        action = request["action"]
        self.actions.append(action)
        if action == "open":
            self.started = True
            return self._state(clicked="Start assignment")
        if action == "fill":
            self.values = [row["reply"] for row in request["answers"]]
            return self._state(filled=True)
        if action == "submit":
            if self.marker_provider:
                self.marker_at_submit = self.marker_provider()
            if self.submit_transport_error:
                raise RuntimeError("Chrome connection closed after dispatch outcome became unknown")
            if self.submit_error:
                return self._state(error="The reflection form changed before click.")
            self.submitted = True
            return self._state(clicked="Submit")
        if action == "confirm_submit":
            if self.marker_provider:
                self.marker_at_confirm = self.marker_provider()
            if self.confirm_error:
                return self._state(error="The Submit confirmation changed before click.")
            self.confirmed = True
            return self._state(clicked="Confirm Submit")
        if action == "finish_frozen_modal":
            self.offscreen = False
            return {"recovered": True, "previous_x": 1920, "current_x": 0}
        state = self._state()
        if action == "inspect" and self.submitted and self.result_delay:
            self.result_delay -= 1
            if not self.result_delay:
                self.pending = False
        if action == "inspect" and not self.started and self.entry_delay:
            self.entry_delay -= 1
        if action == "inspect" and self.started and self.form_delay:
            self.form_delay -= 1
        return state


class ReflectionRunner:
    def __init__(self, tab):
        self.tab = tab
        self.events = []
        self.opened = []
        self.checkpoint = {}
        self.saved = []
        self.store = SimpleNamespace(save=self._save)

    def _save(self):
        self.saved.append(copy.deepcopy(self.checkpoint))

    async def snapshot(self):
        done = (self.tab.submitted and not self.tab.pending and
                (not self.tab.confirmation or self.tab.confirmed) and self.tab.green)
        return {"url": ITEM["url"], "items": [{"url": ITEM["url"],
                "aria_label": "Completed" if done else "Not started", "evidence": [],
                "checked": done}]}

    async def verified_item(self, item):
        return (self.tab.submitted and not self.tab.pending and
                (not self.tab.confirmation or self.tab.confirmed) and self.tab.green)

    async def open_item(self, item):
        self.opened.append(item["key"])

    def event(self, kind, **fields):
        self.events.append((kind, fields))


async def model_answers(*args, **kwargs):
    return {"message": {"content": json.dumps({"answers": [
        {"id": f"t{i}", "reply": a} for i, a in enumerate(ANSWERS)]})},
        "prompt_tokens": 30, "output_tokens": 70}


class ReflectionValidationTests(unittest.TestCase):
    def test_javascript_syntax_and_guarded_actions(self):
        result = subprocess.run(["node", "--check", "-"],
                                input="const f=" + reflection.REFLECTION_JS + ";",
                                text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Wrong optional practice assignment page", reflection.REFLECTION_JS)
        self.assertIn("request.snapshot_id!==snapshotId", reflection.REFLECTION_JS)
        self.assertIn("x.reply===fields[i].value", reflection.REFLECTION_JS)

    def test_confirmation_ignores_chat_and_autosize_textareas(self):
        payload = {"code": reflection.REFLECTION_JS, "url": ITEM["url"],
                   "title": "Interests and Aspirations in XAI (Optional)",
                   "question": QUESTIONS[0], "answer": ANSWERS[0]}
        result = subprocess.run(["node", "-e", NODE_CONFIRM_DOM],
                                input=json.dumps(payload), text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(len(state["before"]["fields"]), 1)
        self.assertEqual(state["before"]["confirmation"], "Ready to submit? Submit Cancel")
        self.assertEqual(state["result"].get("clicked"), "Confirm Submit")
        self.assertEqual(state["clicks"], ["Submit"])

    def test_authorship_statement_blocks_recovered_confirmation(self):
        payload = {"code": reflection.REFLECTION_JS, "url": ITEM["url"],
                   "title": "Interests and Aspirations in XAI (Optional)",
                   "question": QUESTIONS[0], "answer": ANSWERS[0],
                   "attestation": "By clicking Submit, you confirm this work is your own. "
                                  "Submitting work created with AI tools may result in course failure."}
        result = subprocess.run(["node", "-e", NODE_CONFIRM_DOM],
                                input=json.dumps(payload), text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertTrue(state["before"]["manual_submission_required"])
        self.assertIn("work is your own", state["before"]["authorship_statement"])
        self.assertIn("authorship", state["result"]["error"])
        self.assertEqual(state["clicks"], [])

    def test_authorship_statement_blocks_fill_action(self):
        payload = {"code": reflection.REFLECTION_JS, "url": ITEM["url"],
                   "title": "Interests and Aspirations in XAI (Optional)",
                   "question": QUESTIONS[0], "answer": "", "action": "fill",
                   "reply": ANSWERS[0],
                   "attestation": "By clicking Submit, you confirm this work is your own."}
        result = subprocess.run(["node", "-e", NODE_CONFIRM_DOM],
                                input=json.dumps(payload), text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertIn("authorship", state["result"]["error"])
        self.assertEqual(state["before"]["fields"][0]["value"], "")
        self.assertEqual(state["clicks"], [])

    def test_processing_confirmation_without_buttons_is_not_clicked(self):
        payload = {"code": reflection.REFLECTION_JS, "url": ITEM["url"],
                   "title": "Interests and Aspirations in XAI (Optional)",
                   "question": QUESTIONS[0], "answer": ANSWERS[0], "processing": True}
        result = subprocess.run(["node", "-e", NODE_CONFIRM_DOM],
                                input=json.dumps(payload), text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertTrue(state["before"]["submission_dialog"])
        self.assertIsNone(state["before"]["confirmation"])
        self.assertNotIn("clicked", state["result"])
        self.assertEqual(state["clicks"], [])

    def test_only_optional_practice_assignment_is_intercepted(self):
        self.assertTrue(reflection._candidate(ITEM))
        self.assertFalse(reflection._candidate({**ITEM, "title": "Graded Assignment • 30 min"}))
        self.assertFalse(reflection._candidate({**ITEM, "title": "Reflection Practice Assignment"}))
        self.assertFalse(reflection._candidate({**ITEM, "key": "lecture:GhNSw"}))

    def test_strict_lesson_url_and_identity(self):
        self.assertEqual(reflection._item_identity(ITEM)[0], ITEM["url"])
        for bad in [{**ITEM, "key": "assignment:other"},
                    {**ITEM, "url": ITEM["url"] + "?x=1"},
                    {**ITEM, "url": ITEM["url"].replace("coursera.org", "coursera.example")},
                    {**ITEM, "url": ITEM["url"].replace("assignment-submission", "exam")}]:
            with self.subTest(bad=bad), self.assertRaises(engine.StopRun):
                reflection._item_identity(bad)

    def test_generated_responses_cannot_claim_personal_history(self):
        fields = [{"id": f"t{i}", "question": q, "maxLength": 10000}
                  for i, q in enumerate(QUESTIONS)]
        valid = json.dumps({"answers": [{"id": f"t{i}", "reply": a}
                                         for i, a in enumerate(ANSWERS)]})
        self.assertEqual(len(reflection._validate_answers(valid, fields)), 2)
        invented = json.dumps({"answers": [{"id": "t0", "reply":
            "I have worked as a machine learning engineer for many years."},
            {"id": "t1", "reply": ANSWERS[1]}]})
        with self.assertRaisesRegex(engine.StopRun, "personal facts"):
            reflection._validate_answers(invented, fields)
        with self.assertRaisesRegex(engine.StopRun, "question id"):
            reflection._validate_answers(json.dumps({"answers": [
                {"id": "t1", "reply": ANSWERS[0]}, {"id": "t0", "reply": ANSWERS[1]}]}), fields)

    def test_saved_pending_reflection_exposes_exact_assignment_base_url(self):
        course = {"id": "explainable-machine-learning-xai", "checkpoint": {
            "pending_reflections": {ITEM["key"]: {
                "url": ITEM["url"], "title": ITEM["title"], "phase": "first_clicked"}}}}
        store = SimpleNamespace(data={"courses": [course]})
        self.assertEqual(reflection.pending_reflections(store), [{
            "course_id": course["id"], "key": ITEM["key"], "url": ITEM["url"],
            "title": ITEM["title"], "phase": "first_clicked"}])
        course["checkpoint"]["pending_reflections"][ITEM["key"]]["url"] += "/attempt"
        with self.assertRaisesRegex(engine.StopRun, "URL or identity is invalid"):
            reflection.pending_reflections(store)


class ReflectionFlowTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, tab):
        runner = ReflectionRunner(tab)
        with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"), \
             patch.object(engine, "request_groq_answers", side_effect=model_answers) as model, \
             patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
            result = await reflection.maybe_complete_reflection(runner, ITEM)
        return result, runner, model

    async def test_authorship_attestation_stops_before_model_or_submit(self):
        tab = ReflectionTab(authorship=True)
        runner = ReflectionRunner(tab)
        with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"), \
             patch.object(engine, "request_groq_answers", side_effect=model_answers) as model, \
             patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "own work"):
                await reflection.maybe_complete_reflection(runner, ITEM)
        model.assert_not_awaited()
        self.assertNotIn("fill", tab.actions)
        self.assertNotIn("submit", tab.actions)
        self.assertNotIn("confirm_submit", tab.actions)

    async def test_late_authorship_hydration_stops_before_fill(self):
        tab = ReflectionTab()
        runner = ReflectionRunner(tab)

        async def hydrate_after_model(*args, **kwargs):
            tab.authorship = True
            return await model_answers(*args, **kwargs)

        with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"), \
             patch.object(engine, "request_groq_answers", side_effect=hydrate_after_model) as model, \
             patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "own work"):
                await reflection.maybe_complete_reflection(runner, ITEM)
        model.assert_awaited_once()
        self.assertNotIn("fill", tab.actions)
        self.assertNotIn("submit", tab.actions)

    async def test_text_only_optional_practice_submits_once_and_requires_green_mark(self):
        tab = ReflectionTab()
        result, runner, model = await self._run(tab)
        self.assertTrue(result)
        self.assertEqual(tab.actions.count("open"), 1)
        self.assertEqual(tab.actions.count("fill"), 1)
        self.assertEqual(tab.actions.count("submit"), 1)
        self.assertEqual(tab.actions.count("confirm_submit"), 0)
        self.assertEqual([kind for kind, _ in runner.events if kind.startswith("reflection_")],
                         ["reflection_opened", "reflection_answers_prepared", "reflection_filled",
                          "reflection_submit_clicked"])
        model.assert_awaited_once()

    async def test_first_submit_is_reserved_durably_before_click_then_cleared_on_green(self):
        tab = ReflectionTab()
        runner = ReflectionRunner(tab)
        tab.marker_provider = lambda: copy.deepcopy(runner.checkpoint)
        with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"), \
             patch.object(engine, "request_groq_answers", side_effect=model_answers), \
             patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
            self.assertTrue(await reflection.maybe_complete_reflection(runner, ITEM))
        marker = tab.marker_at_submit["pending_reflections"][ITEM["key"]]
        self.assertEqual((marker["url"], marker["phase"]), (ITEM["url"], "first_dispatch"))
        self.assertTrue(any(saved.get("pending_reflections", {}).get(ITEM["key"], {}).get("phase") ==
                            "first_dispatch" for saved in runner.saved))
        self.assertEqual(runner.checkpoint["pending_reflections"], {})

    async def test_second_submit_is_reserved_before_confirmation_click(self):
        tab = ReflectionTab(confirmation=True)
        runner = ReflectionRunner(tab)
        tab.marker_provider = lambda: copy.deepcopy(runner.checkpoint)
        with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"), \
             patch.object(engine, "request_groq_answers", side_effect=model_answers), \
             patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
            self.assertTrue(await reflection.maybe_complete_reflection(runner, ITEM))
        self.assertEqual(tab.marker_at_confirm["pending_reflections"][ITEM["key"]]["phase"],
                         "confirm_dispatch")
        self.assertEqual(runner.checkpoint["pending_reflections"], {})

    async def test_unknown_result_keeps_durable_pending_marker(self):
        tab = ReflectionTab(pending=True)
        runner = ReflectionRunner(tab)
        with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"), \
             patch.object(engine, "request_groq_answers", side_effect=model_answers), \
             patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "outcome is still unknown"):
                await reflection.maybe_complete_reflection(runner, ITEM)
        self.assertEqual(runner.checkpoint["pending_reflections"][ITEM["key"]]["phase"],
                         "first_clicked")
        self.assertEqual(tab.actions.count("submit"), 1)

    async def test_first_submit_guard_error_refunds_marker_only_when_no_click_proven(self):
        tab = ReflectionTab(submit_error=True)
        runner = ReflectionRunner(tab)
        with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"), \
             patch.object(engine, "request_groq_answers", side_effect=model_answers), \
             patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "was not clicked"):
                await reflection.maybe_complete_reflection(runner, ITEM)
        self.assertTrue(any(saved.get("pending_reflections", {}).get(ITEM["key"], {}).get("phase") ==
                            "first_dispatch" for saved in runner.saved))
        self.assertEqual(runner.checkpoint["pending_reflections"], {})

    async def test_first_submit_transport_error_keeps_marker(self):
        tab = ReflectionTab(submit_transport_error=True)
        runner = ReflectionRunner(tab)
        with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"), \
             patch.object(engine, "request_groq_answers", side_effect=model_answers), \
             patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "connection closed"):
                await reflection.maybe_complete_reflection(runner, ITEM)
        self.assertEqual(runner.checkpoint["pending_reflections"][ITEM["key"]]["phase"],
                         "first_dispatch")

    async def test_confirm_guard_error_restores_first_submit_reservation(self):
        tab = ReflectionTab(confirmation=True, confirm_error=True)
        runner = ReflectionRunner(tab)
        with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"), \
             patch.object(engine, "request_groq_answers", side_effect=model_answers), \
             patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "no second Submit"):
                await reflection.maybe_complete_reflection(runner, ITEM)
        self.assertEqual(tab.actions.count("submit"), 1)
        self.assertEqual(tab.actions.count("confirm_submit"), 1)
        self.assertEqual(runner.checkpoint["pending_reflections"][ITEM["key"]]["phase"],
                         "first_clicked")

    async def test_saved_marker_prevents_new_submit_without_observed_result(self):
        tab = ReflectionTab(recover_confirmation=True, pending=True)
        tab.confirmation = False
        runner = ReflectionRunner(tab)
        runner.checkpoint["pending_reflections"] = {ITEM["key"]: {
            "url": ITEM["url"], "title": ITEM["title"], "phase": "first_clicked"}}
        with self.assertRaisesRegex(engine.StopRun, "no confirmed result"):
            await reflection.maybe_complete_reflection(runner, ITEM)
        self.assertNotIn("submit", tab.actions)
        self.assertNotIn("confirm_submit", tab.actions)
        self.assertIn(ITEM["key"], runner.checkpoint["pending_reflections"])

    async def test_exact_confirmation_is_clicked_once(self):
        tab = ReflectionTab(confirmation=True)
        result, _, _ = await self._run(tab)
        self.assertTrue(result)
        self.assertEqual(tab.actions.count("submit"), 1)
        self.assertEqual(tab.actions.count("confirm_submit"), 1)

    async def test_restart_confirms_pending_modal_without_model_fill_or_first_submit(self):
        tab = ReflectionTab(recover_confirmation=True)
        result, runner, model = await self._run(tab)
        self.assertTrue(result)
        model.assert_not_awaited()
        self.assertEqual(tab.actions.count("fill"), 0)
        self.assertEqual(tab.actions.count("submit"), 0)
        self.assertEqual(tab.actions.count("confirm_submit"), 1)
        self.assertIn(("reflection_submit_confirmed", {"key": ITEM["key"], "recovered": True}),
                      runner.events)

    async def test_restart_does_not_confirm_incomplete_pending_answers(self):
        tab = ReflectionTab(recover_confirmation=True, incomplete_recovery=True)
        runner = ReflectionRunner(tab)
        with patch.object(engine, "request_groq_answers", side_effect=model_answers) as model:
            with self.assertRaisesRegex(engine.StopRun, "incomplete or changed"):
                await reflection.maybe_complete_reflection(runner, ITEM)
        model.assert_not_awaited()
        self.assertNotIn("confirm_submit", tab.actions)
        self.assertNotIn("submit", tab.actions)

    async def test_preflight_stops_on_processing_dialog_without_a_second_click(self):
        tab = ReflectionTab(recover_confirmation=True, processing_confirmation=True)
        runner = ReflectionRunner(tab)
        with self.assertRaisesRegex(engine.StopRun, "still processing"):
            await reflection.recover_pending_reflection(runner, ITEM)
        self.assertEqual(tab.actions.count("confirm_submit"), 0)

    async def test_preflight_preserves_filled_attempt_after_dialog_disappears(self):
        tab = ReflectionTab(recover_confirmation=True)
        tab.confirmation = False
        tab.pending = True
        runner = ReflectionRunner(tab)
        with self.assertRaisesRegex(engine.StopRun, "saved draft or an unresolved Submit"):
            await reflection.recover_pending_reflection(runner, ITEM)
        self.assertEqual(tab.actions.count("confirm_submit"), 0)

    async def test_missing_green_mark_stops_after_one_submit(self):
        tab = ReflectionTab(green=False)
        runner = ReflectionRunner(tab)
        with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"), \
             patch.object(engine, "request_groq_answers", side_effect=model_answers), \
             patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "green course mark"):
                await reflection.maybe_complete_reflection(runner, ITEM)
        self.assertEqual(tab.actions.count("submit"), 1)
        self.assertIn(ITEM["key"], runner.checkpoint["pending_reflections"])

    async def test_unavailable_submit_result_is_not_retried(self):
        tab = ReflectionTab(pending=True)
        with self.assertRaisesRegex(engine.StopRun, "outcome is still unknown"):
            await self._run(tab)
        self.assertEqual(tab.actions.count("submit"), 1)
        self.assertEqual(tab.actions.count("confirm_submit"), 0)

    async def test_slow_coursera_result_is_waited_for_without_resubmitting(self):
        tab = ReflectionTab(pending=True, result_delay=130)
        result, _, _ = await self._run(tab)
        self.assertTrue(result)
        self.assertEqual(tab.actions.count("submit"), 1)
        self.assertGreaterEqual(tab.actions.count("inspect"), 130)

    async def test_mixed_control_or_existing_draft_never_reaches_model(self):
        for tab in (ReflectionTab(mixed=True), ReflectionTab(existing=True)):
            with self.subTest(tab=tab):
                runner = ReflectionRunner(tab)
                with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"), \
                     patch.object(engine, "request_groq_answers", side_effect=model_answers) as model, \
                     patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
                    with self.assertRaises(engine.StopRun):
                        await reflection.maybe_complete_reflection(runner, ITEM)
                model.assert_not_awaited()
                self.assertNotIn("fill", tab.actions)

    async def test_ambiguous_start_stops_without_click(self):
        tab = ReflectionTab(ambiguous_entry=True)
        runner = ReflectionRunner(tab)
        with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"):
            with self.assertRaisesRegex(engine.StopRun, "ambiguous Start or Resume"):
                await reflection.maybe_complete_reflection(runner, ITEM)
        self.assertEqual(tab.actions, ["inspect"])

    async def test_cover_hydration_waits_for_one_entry_then_opens_once(self):
        tab = ReflectionTab(entry_delay=3)
        result, _, model = await self._run(tab)
        self.assertTrue(result)
        self.assertEqual(tab.actions.count("open"), 1)
        self.assertGreaterEqual(tab.actions.index("open"), 3)
        model.assert_awaited_once()

    async def test_attempt_route_waits_for_text_form_hydration(self):
        tab = ReflectionTab(form_delay=4)
        result, _, model = await self._run(tab)
        self.assertTrue(result)
        self.assertGreaterEqual(tab.actions.count("inspect"), 7)
        self.assertEqual(tab.actions.count("submit"), 1)
        model.assert_awaited_once()

    async def test_frozen_empty_reflection_modal_is_recovered_before_fill(self):
        tab = ReflectionTab(offscreen=True)
        result, runner, model = await self._run(tab)
        self.assertTrue(result)
        self.assertEqual(tab.actions.count("finish_frozen_modal"), 1)
        self.assertLess(tab.actions.index("finish_frozen_modal"), tab.actions.index("fill"))
        self.assertTrue(any(kind == "reflection_offscreen_panel_recovered"
                            for kind, _ in runner.events))
        model.assert_awaited_once()

    async def test_offscreen_existing_draft_is_preserved(self):
        tab = ReflectionTab(existing=True, offscreen=True)
        runner = ReflectionRunner(tab)
        with patch.object(engine, "request_groq_answers", side_effect=model_answers) as model, \
             patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "offscreen and contains a draft"):
                await reflection.maybe_complete_reflection(runner, ITEM)
        self.assertNotIn("finish_frozen_modal", tab.actions)
        self.assertNotIn("fill", tab.actions)
        model.assert_not_awaited()

    async def test_missing_entry_stops_after_bounded_probes_without_click(self):
        tab = ReflectionTab(entry_delay=100)
        runner = ReflectionRunner(tab)
        with patch.object(engine, "COURSE_SLUG", "explainable-machine-learning-xai"), \
             patch.object(engine, "request_groq_answers", side_effect=model_answers) as model, \
             patch.object(reflection.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "after loading"):
                await reflection.maybe_complete_reflection(runner, ITEM)
        self.assertEqual(tab.actions.count("inspect"), reflection.ENTRY_PROBES + 1)
        self.assertNotIn("open", tab.actions)
        model.assert_not_awaited()

    async def test_non_candidate_returns_false_without_browser_or_model(self):
        runner = ReflectionRunner(ReflectionTab())
        self.assertFalse(await reflection.maybe_complete_reflection(
            runner, {**ITEM, "title": "A graded quiz Graded Assignment"}))
        self.assertEqual(runner.tab.actions, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
