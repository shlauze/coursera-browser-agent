"""Offline tests for the requested direct End Dialogue flow."""

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import coursera_start as engine
import dialogue_flow as dialogue


ITEM = {"key": "coach:Pos8A",
        "url": "https://www.coursera.org/learn/deep-learning-with-pytorch/coach/Pos8A/deep-learning-in-context",
        "title": "Deep Learning in Context"}


def modal(reason="No reason provided"):
    return {"text": dialogue.SESSION_PREFIX + reason + " \u200b Yes, end the Dialogue Cancel",
            "buttons": [{"label": "Close", "disabled": False},
                        {"label": reason, "disabled": False},
                        {"label": "Yes, end the Dialogue", "disabled": False},
                        {"label": "Cancel", "disabled": False}]}


class DialogueTab:
    def __init__(self, *, started=True, reason="No reason provided", green=True,
                 unknown_modal=False, lose_end_result=False, persist_modal=False,
                 drawer_open=False, sticky_drawer=False, drawer_offscreen=False,
                 hidden=False, stay_hidden=False, frozen_modal=False, expanded=False,
                 mobile_menu=False, outline_collapsed=False):
        self.started = started
        self.reason = reason
        self.green = green
        self.unknown_modal = unknown_modal
        self.lose_end_result = lose_end_result
        self.persist_modal = persist_modal
        self.modal_open = False
        self.menu_open = False
        self.drawer_open = drawer_open
        self.sticky_drawer = sticky_drawer
        self.drawer_offscreen = drawer_offscreen
        self.hidden = hidden
        self.stay_hidden = stay_hidden
        self.frozen_modal = frozen_modal
        self.expanded = expanded
        self.mobile_menu = mobile_menu
        self.outline_open = not outline_collapsed
        self.confirmed = False
        self.actions = []
        self.mouse_events = []

    async def evaluate(self, expression):
        if expression.startswith(engine.LESSON_OUTLINE_JS):
            self.actions.append("expand_outline")
            if self.outline_open:
                return {"clicked": False, "matches": 0}
            self.outline_open = True
            return {"clicked": True, "label": "Expand course outline"}
        request = json.loads(expression[len(dialogue.DIALOGUE_JS) + 1:-1])
        action = request["action"]
        self.actions.append(action)
        if action == "start":
            self.started = True
            return self.state(clicked="Start Dialogue")
        if action == "end":
            self.modal_open = True
            if self.lose_end_result:
                self.lose_end_result = False
                raise OSError("websocket closed after End Dialogue")
            return self.state(clicked="End Dialogue")
        if action == "reason_target":
            return self.state(reason_target={"x": 320, "y": 240})
        if action == "drawer_close_target":
            if self.drawer_offscreen:
                return self.state(error="The Coursera course drawer Close is offscreen.")
            return self.state(drawer_close_target={"x": 508, "y": 28})
        if action == "drawer_close_offscreen":
            if not self.sticky_drawer:
                self.drawer_open = False
            return self.state(clicked="Close", strategy="offscreen_dom")
        if action == "finish_frozen_end_modal":
            if self.frozen_modal:
                self.frozen_modal = False
                if self.expanded:
                    self.menu_open = True
                return self.state(recovered=True)
            return self.state(error="The Dialogue end modal is not the known frozen slide-in.")
        if action == "activate_reason_hidden":
            self.expanded = True
            self.menu_open = True
            return self.state(dispatched="mousedown")
        if action == "choose_no_reason":
            if not self.menu_open:
                return self.state(error="No reason provided option is not visible.")
            self.reason = "No reason provided"
            self.menu_open = False
            return self.state(clicked="No reason provided")
        if action == "confirm_end":
            self.confirmed = True
            if not self.persist_modal:
                self.modal_open = False
            return self.state(clicked="Confirm End Dialogue")
        return self.state()

    async def call(self, method, params):
        if method == "Page.bringToFront":
            if not self.stay_hidden:
                self.hidden = False
            return {}
        self.mouse_events.append((method, params))
        if method != "Input.dispatchMouseEvent":
            raise AssertionError(method)
        if params["type"] == "mouseReleased":
            if params["x"] == 508:
                if not self.sticky_drawer:
                    self.drawer_open = False
            else:
                self.expanded = True
                self.menu_open = True
        return {}

    def state(self, **extra):
        current = modal(self.reason)
        if self.unknown_modal:
            current["text"] = "End Dialogue and discard your work?"
        return {"url": ITEM["url"], "ready": "complete",
                "visibility_state": "hidden" if self.hidden else "visible",
                "start_count": int(not self.started and not self.drawer_open),
                "end_count": int(self.started and not self.drawer_open),
                "course_drawer_count": int(self.drawer_open),
                "course_drawer_close_count": int(self.drawer_open),
                "course_drawer_text": "Deep Learning with PyTorch" if self.drawer_open else "",
                "dialogs": ([{"text": "Deep Learning with PyTorch", "buttons": [
                    {"label": "Close", "disabled": False}]}] if self.drawer_open else
                    [current] if self.modal_open and not self.menu_open else []),
                "reason_menu_open": self.menu_open,
                "reason_menu_kind": "mobile_menu" if self.menu_open and self.mobile_menu else
                    "listbox" if self.menu_open else None,
                "reason_expanded": self.expanded or self.menu_open,
                "reason_modal_text": current["text"] if self.menu_open else "", **extra}


class DialogueRunner:
    def __init__(self, tab):
        self.tab = tab
        self.events = []
        self.checkpoint = {"dialogues": {}}

    async def verified_item(self, item):
        return self.tab.confirmed and self.tab.green and self.tab.outline_open

    def event(self, kind, **fields):
        self.events.append((kind, fields))


async def immediate_sleep(*args, **kwargs):
    return None


class DialogueValidationTests(unittest.TestCase):
    def test_only_observed_modal_with_explicit_reason_can_confirm(self):
        self.assertEqual(dialogue._modal_kind(modal()), "confirm")
        self.assertEqual(dialogue._modal_kind(modal("Select a reason")), "choose_reason")
        expanded = modal("Select a reason")
        expanded["text"] = expanded["text"].replace(
            "Select a reason", "Select a reason No reason provided I feel stuck")
        expanded["buttons"].append({"label": "I feel stuck", "disabled": False})
        self.assertEqual(dialogue._modal_kind(expanded), "choose_reason")
        self.assertIsNone(dialogue._modal_kind({**modal(), "text": "End Dialogue and discard work?"}))
        bad = modal()
        bad["buttons"].append({"label": "Submit assignment", "disabled": False})
        self.assertIsNone(dialogue._modal_kind(bad))
        self.assertIsNone(dialogue._modal_kind({**modal(), "text": modal()["text"].replace(
            "Would you like", "Could you")}))

    def test_url_and_identity_must_match(self):
        self.assertEqual(dialogue._lesson_url(ITEM), ITEM["url"])
        with self.assertRaisesRegex(engine.StopRun, "invalid"):
            dialogue._lesson_url({**ITEM, "key": "coach:wrong"})

    def test_dialogue_javascript_is_valid(self):
        result = subprocess.run(["node", "--check", "-"], input="const f=" + dialogue.DIALOGUE_JS + ";",
                                text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_course_drawer_target_is_found_while_start_is_aria_hidden(self):
        script = "const run=" + dialogue.DIALOGUE_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/deep-learning-with-pytorch/coach/Pos8A/deep-learning-in-context';
const element=(text='')=>({innerText:text,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null}});
const hidden=element();hidden.getAttribute=name=>name==='aria-hidden'?'true':null;
const start=element('Start Dialogue');
const root=element();root.parentElement=hidden;start.parentElement=root;
root.querySelectorAll=()=>[start];
const drawer=element('Deep Learning with PyTorch');
const close=element('');close.parentElement=drawer;
close.getAttribute=name=>name==='aria-label'?'Close':null;
close.getBoundingClientRect=()=>({left:496,top:16,width:24,height:24});
close.contains=()=>false;
drawer.querySelectorAll=()=>[close];
global.location=new URL(url);global.innerWidth=1920;global.innerHeight=1080;
global.document={readyState:'complete',querySelector:()=>root,elementFromPoint:()=>close,
  querySelectorAll(selector){
    if(selector==='[role="dialog"],[aria-modal="true"]')return [drawer];
    if(selector==='[role="dialog"][aria-modal="true"][data-testid="drawer-toggle-drawer"]')return [drawer];
    return [];
  }};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const before=run({url,action:'inspect'});
drawer.innerText='Deep Learning with PyTorch Module 3 just loaded';
const target=run({url,action:'drawer_close_target',expected_dialog:before.course_drawer_text});
process.stdout.write(JSON.stringify({start:before.start_count,drawer:before.course_drawer_count,
  close:before.course_drawer_close_count,target:target.drawer_close_target}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "start": 0, "drawer": 1, "close": 1,
            "target": {"x": 508, "y": 28}})

    def test_translated_drawer_uses_exact_dom_close_and_rejects_extra_modal(self):
        script = "const run=" + dialogue.DIALOGUE_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/deep-learning-with-pytorch/coach/Pos8A/deep-learning-in-context';
let clicks=0,extra=false;
const element=(text='')=>({innerText:text,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null}});
const drawer=element('Deep Learning with PyTorch');
const close=element('');close.parentElement=drawer;
close.getAttribute=name=>name==='aria-label'?'Close':null;
close.getBoundingClientRect=()=>({left:496,top:1008,width:24,height:24});
close.click=()=>{clicks++};drawer.querySelectorAll=()=>[close];
const unknown=element('Unknown modal');unknown.querySelectorAll=()=>[];
const root={querySelectorAll:()=>[]};
global.location=new URL(url);global.innerWidth=1920;global.innerHeight=992;
global.document={readyState:'complete',querySelector:()=>root,querySelectorAll(selector){
  if(selector==='[role="dialog"],[aria-modal="true"]')return extra?[drawer,unknown]:[drawer];
  if(selector==='[role="dialog"][aria-modal="true"][data-testid="drawer-toggle-drawer"]')return [drawer];
  return [];
}};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const target=run({url,action:'drawer_close_target'});
const result=run({url,action:'drawer_close_offscreen'});
extra=true;
const blocked=run({url,action:'drawer_close_offscreen'});
process.stdout.write(JSON.stringify({targetError:target.error,clicked:result.clicked,
  strategy:result.strategy,blocked:!!blocked.error,clicks}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "targetError": "The Coursera course drawer Close is offscreen.",
            "clicked": "Close", "strategy": "offscreen_dom",
            "blocked": True, "clicks": 1})

    def test_portaled_listbox_works_while_modal_is_aria_hidden(self):
        script = "const run=" + dialogue.DIALOGUE_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/deep-learning-with-pytorch/coach/Pos8A/deep-learning-in-context';
const prefix='Would you like to end this session? Please note that you will not be able to resume it, but you can always start a new one from the beginning. Reason for ending (Optional) We would appreciate your feedback to help us improve the experience. ';
let menuOpen=false, confirmed=0;
const element=(label,click)=>({innerText:label,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null},click:click||(()=>{})});
const hiddenParent=element('');
hiddenParent.getAttribute=name=>name==='aria-hidden'&&menuOpen?'true':null;
const select=element('Select a reason'),chosen=element('No reason provided');
select.getAttribute=name=>name==='aria-expanded'?(menuOpen?'true':'false'):null;
select.getBoundingClientRect=()=>({left:100,top:200,width:20,height:20});
select.contains=()=>false;
const option=element('No reason provided',()=>{menuOpen=false;reason='No reason provided'});
option.getAttribute=name=>name==='data-value'?'DEFAULT':null;
const yes=element('Yes, end the Dialogue',()=>{confirmed++}),cancel=element('Cancel'),close=element('Close');
let reason='Select a reason';
const dialog=element('');
dialog.parentElement=hiddenParent;
for(const button of [select,chosen,yes,cancel,close])button.parentElement=dialog;
Object.defineProperty(dialog,'innerText',{get(){return prefix+reason+' \u200b Yes, end the Dialogue Cancel'}});
dialog.querySelectorAll=selector=>selector==='[data-testid="end-dialogue-reason-select-field"] [role="button"][aria-haspopup="listbox"]'?
  [select]:[close,reason==='Select a reason'?select:chosen,yes,cancel];
const listbox=element(''); option.parentElement=listbox;
listbox.querySelectorAll=()=>[option];
const root={querySelectorAll:()=>[element('End Dialogue')]};
global.location=new URL(url);
global.innerWidth=1920;global.innerHeight=1080;
global.document={readyState:'complete',querySelector:()=>root,elementFromPoint:()=>select,querySelectorAll(selector){
  if(selector==='[role="dialog"],[aria-modal="true"]')return [dialog];
  if(selector==='[role="dialog"][aria-modal="true"][data-testid="scroll-container"]')return [dialog];
  if(selector==='[role="listbox"]')return menuOpen?[listbox]:[];
  return [];
}};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const before=run({url,action:'inspect'});
const target=run({url,action:'reason_target',expected_dialog:before.dialogs[0].text});
menuOpen=true; // the trusted CDP mousePressed/mouseReleased pair opens this portal
const opened=run({url,action:'inspect'});
const choice=run({url,action:'choose_no_reason',expected_dialog:opened.reason_modal_text});
const selected=run({url,action:'inspect'});
const confirmedResult=run({url,action:'confirm_end',expected_dialog:selected.dialogs[0].text});
process.stdout.write(JSON.stringify({target:target.reason_target,hiddenDialogs:opened.dialogs.length,
  reasonMenuOpen:opened.reason_menu_open,choice:choice.clicked,
  confirmation:confirmedResult.clicked,confirmed}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "target": {"x": 110, "y": 210}, "hiddenDialogs": 0,
            "reasonMenuOpen": True, "choice": "No reason provided",
            "confirmation": "Confirm End Dialogue", "confirmed": 1})

    def test_mobile_reason_menu_can_be_selected_with_modal_aria_hidden(self):
        script = "const run=" + dialogue.DIALOGUE_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/deep-learning-with-pytorch/coach/Pos8A/deep-learning-in-context';
const prefix='Would you like to end this session? Please note that you will not be able to resume it, but you can always start a new one from the beginning. Reason for ending (Optional) We would appreciate your feedback to help us improve the experience. ';
let menuOpen=true,reason='Select a reason',confirmed=0;
const e=(text='')=>({innerText:text,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null},click(){}});
const hiddenParent=e();hiddenParent.getAttribute=n=>n==='aria-hidden'&&menuOpen?'true':null;
const dialog=e();dialog.parentElement=hiddenParent;
Object.defineProperty(dialog,'innerText',{get(){return prefix+reason+' \u200b Yes, end the Dialogue Cancel'}});
const select=e('Select a reason'),yes=e('Yes, end the Dialogue'),cancel=e('Cancel'),close=e('Close');
select.getAttribute=n=>n==='aria-expanded'?(menuOpen?'true':'false'):null;
for(const b of [select,yes,cancel,close])b.parentElement=dialog;
yes.click=()=>confirmed++;
dialog.querySelectorAll=s=>s==='[data-testid="end-dialogue-reason-select-field"] [role="button"][aria-haspopup="listbox"]'?
  [select]:[select,yes,cancel,close];
const menu=e(),option=e('No reason provided');option.parentElement=menu;
option.getAttribute=n=>n==='aria-disabled'?'false':null;
option.click=()=>{reason='No reason provided';menuOpen=false;select.innerText=reason};
menu.querySelectorAll=()=>[option];
const root={querySelectorAll:()=>[e('End Dialogue')]};
global.location=new URL(url);global.innerWidth=536;global.innerHeight=992;
global.document={readyState:'complete',visibilityState:'hidden',querySelector:()=>root,querySelectorAll(s){
  if(s==='[role="dialog"],[aria-modal="true"]')return [dialog];
  if(s==='[role="dialog"][aria-modal="true"][data-testid="scroll-container"]')return [dialog];
  if(s==='ul[role="menu"][aria-label="Reason for ending"]')return menuOpen?[menu]:[];
  return [];
}};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const opened=run({url,action:'inspect'});
const choice=run({url,action:'choose_no_reason',expected_dialog:opened.reason_modal_text});
const selected=run({url,action:'inspect'});
const submit=run({url,action:'confirm_end',expected_dialog:selected.dialogs[0].text});
process.stdout.write(JSON.stringify({kind:opened.reason_menu_kind,dialogs:opened.dialogs.length,
  choice:choice.clicked,submit:submit.clicked,confirmed}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "kind": "mobile_menu", "dialogs": 0,
            "choice": "No reason provided", "submit": "Confirm End Dialogue", "confirmed": 1})

    def test_only_exact_frozen_end_modal_animation_is_finished(self):
        script = "const run=" + dialogue.DIALOGUE_JS + ";\n" + r'''
const url='https://www.coursera.org/learn/deep-learning-with-pytorch/coach/Pos8A/deep-learning-in-context';
const prefix='Would you like to end this session? Please note that you will not be able to resume it, but you can always start a new one from the beginning. Reason for ending (Optional) We would appreciate your feedback to help us improve the experience. ';
let finishes=0;
const e=(text='')=>({innerText:text,hidden:false,inert:false,parentElement:null,disabled:false,
  getClientRects(){return [1]},getAttribute(){return null}});
const dialog=e(prefix+'Select a reason \u200b Yes, end the Dialogue Cancel');
const select=e('Select a reason'),yes=e('Yes, end the Dialogue'),cancel=e('Cancel'),close=e('Close');
select.getAttribute=n=>n==='aria-expanded'?'false':null;
for(const b of [select,yes,cancel,close])b.parentElement=dialog;
dialog.querySelectorAll=s=>s==='[data-testid="end-dialogue-reason-select-field"] [role="button"][aria-haspopup="listbox"]'?
  [select]:[select,yes,cancel,close];
const animation={playState:'running',currentTime:0,effect:{
  getComputedTiming:()=>({endTime:225}),getKeyframes:()=>[
    {transform:'translateY(992px) translateY(0px)'},{transform:'none'}]},
  finish(){finishes++;this.playState='finished';this.currentTime=225}};
const container={style:{transform:'none'},contains:n=>n===dialog,
  getAnimations:()=>[animation],getBoundingClientRect:()=>({top:animation.playState==='finished'?0:992,
    bottom:animation.playState==='finished'?992:1984})};
const root={querySelectorAll:()=>[e('End Dialogue')]};
global.location=new URL(url);global.innerWidth=536;global.innerHeight=992;
global.document={readyState:'complete',visibilityState:'hidden',querySelector:()=>root,querySelectorAll(s){
  if(s==='[role="dialog"],[aria-modal="true"]')return [dialog];
  if(s==='[role="dialog"][aria-modal="true"][data-testid="scroll-container"]')return [dialog];
  if(s==='.cds-Modal-container')return [container];
  return [];
}};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const before=run({url,action:'inspect'});
const first=run({url,action:'finish_frozen_end_modal',expected_dialog:before.dialogs[0].text});
const second=run({url,action:'finish_frozen_end_modal',expected_dialog:before.dialogs[0].text});
process.stdout.write(JSON.stringify({recovered:first.recovered,y:first.current_y,finishes,
  blocked:!!second.error}));
'''
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "recovered": True, "y": 0, "finishes": 1, "blocked": True})


class DialogueFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_mobile_dialogue_reopens_outline_before_green_verification(self):
        tab = DialogueTab(started=True, outline_collapsed=True)
        tab.confirmed = True
        runner = DialogueRunner(tab)
        runner.checkpoint["dialogues"][ITEM["key"]] = {"phase": "confirmation_requested"}
        with patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            await dialogue.run_dialogue(runner, ITEM)
        self.assertEqual(tab.actions.count("expand_outline"), 1)
        self.assertEqual(runner.checkpoint["dialogues"][ITEM["key"]]["phase"], "completed")
        self.assertTrue(any(name == "dialogue_course_outline_opened" for name, _ in runner.events))
        self.assertNotIn("end", tab.actions)
        self.assertNotIn("confirm_end", tab.actions)

    async def test_background_tab_is_brought_forward_before_dialogue(self):
        tab = DialogueTab(started=True, hidden=True, reason="Select a reason")
        runner = DialogueRunner(tab)
        with patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            await dialogue.run_dialogue(runner, ITEM)
        self.assertTrue(any(event == "dialogue_tab_brought_to_front" for event, _ in runner.events))
        self.assertEqual(tab.actions.count("reason_target"), 1)
        self.assertNotIn("activate_reason_hidden", tab.actions)

    async def test_hidden_frozen_modal_recovers_before_synthetic_mousedown(self):
        tab = DialogueTab(started=True, hidden=True, stay_hidden=True,
                          frozen_modal=True, reason="Select a reason", mobile_menu=True)
        runner = DialogueRunner(tab)
        with patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            await dialogue.run_dialogue(runner, ITEM)
        self.assertEqual(tab.actions.count("finish_frozen_end_modal"), 1)
        self.assertEqual(tab.actions.count("activate_reason_hidden"), 1)
        self.assertEqual(tab.actions.count("choose_no_reason"), 1)
        self.assertTrue(any(event == "dialogue_end_modal_recovered" for event, _ in runner.events))
        self.assertEqual(tab.mouse_events, [])

    async def test_frozen_modal_with_expanded_reason_waits_for_mobile_portal(self):
        tab = DialogueTab(started=True, hidden=True, stay_hidden=True,
                          frozen_modal=True, reason="Select a reason", expanded=True,
                          mobile_menu=True)
        runner = DialogueRunner(tab)
        with patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            await dialogue.run_dialogue(runner, ITEM)
        self.assertEqual(tab.actions.count("finish_frozen_end_modal"), 1)
        self.assertNotIn("activate_reason_hidden", tab.actions)
        self.assertEqual(tab.actions.count("choose_no_reason"), 1)

    async def test_course_drawer_is_closed_once_before_start_and_end(self):
        tab = DialogueTab(started=False, drawer_open=True)
        runner = DialogueRunner(tab)
        with patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            await dialogue.run_dialogue(runner, ITEM)
        self.assertEqual(tab.actions.count("drawer_close_target"), 1)
        self.assertEqual(tab.actions.count("start"), 1)
        self.assertEqual(tab.actions.count("end"), 1)
        self.assertEqual([params["type"] for _, params in tab.mouse_events],
                         ["mousePressed", "mouseReleased"])
        self.assertTrue(any(event == "dialogue_course_drawer_closed" for event, _ in runner.events))

    async def test_offscreen_translated_course_drawer_uses_guarded_dom_close(self):
        tab = DialogueTab(started=False, drawer_open=True, drawer_offscreen=True)
        runner = DialogueRunner(tab)
        with patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            await dialogue.run_dialogue(runner, ITEM)
        self.assertEqual(tab.actions.count("drawer_close_target"), 1)
        self.assertEqual(tab.actions.count("drawer_close_offscreen"), 1)
        self.assertEqual(tab.actions.count("start"), 1)
        self.assertEqual(tab.actions.count("end"), 1)
        self.assertEqual(tab.mouse_events, [])


    async def test_course_drawer_does_not_get_clicked_twice_if_it_remains_open(self):
        tab = DialogueTab(started=False, drawer_open=True, sticky_drawer=True)
        runner = DialogueRunner(tab)
        with patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            with self.assertRaisesRegex(engine.StopRun, "sidebar remained open"):
                await dialogue.run_dialogue(runner, ITEM)
        self.assertEqual(tab.actions.count("drawer_close_target"), 1)
        self.assertEqual([params["type"] for _, params in tab.mouse_events],
                         ["mousePressed", "mouseReleased"])
        self.assertNotIn("start", tab.actions)
        self.assertNotIn("end", tab.actions)

    async def test_course_drawer_with_ambiguous_close_stops_without_mouse(self):
        class AmbiguousDrawer(DialogueTab):
            def state(self, **extra):
                return {**super().state(**extra), "course_drawer_close_count": 2}

        tab = AmbiguousDrawer(started=False, drawer_open=True)
        runner = DialogueRunner(tab)
        with self.assertRaisesRegex(engine.StopRun, "no unique Close"):
            await dialogue.run_dialogue(runner, ITEM)
        self.assertEqual(tab.mouse_events, [])

    async def test_intro_starts_then_ends_without_sending_messages(self):
        tab = DialogueTab(started=False, reason="Select a reason")
        runner = DialogueRunner(tab)
        with patch.object(engine, "request_groq_answers", side_effect=AssertionError("no Groq call")), \
                patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            await dialogue.run_dialogue(runner, ITEM)
        self.assertEqual(tab.actions.count("start"), 1)
        self.assertEqual(tab.actions.count("end"), 1)
        self.assertEqual(tab.actions.count("reason_target"), 1)
        self.assertEqual(tab.actions.count("choose_no_reason"), 1)
        self.assertEqual([parameters["type"] for _, parameters in tab.mouse_events],
                         ["mousePressed", "mouseReleased"])
        self.assertEqual(tab.actions.count("confirm_end"), 1)
        self.assertNotIn("send", tab.actions)
        self.assertNotIn("fill", tab.actions)
        self.assertEqual(runner.checkpoint["dialogues"][ITEM["key"]]["phase"], "completed")

    async def test_existing_no_reason_selection_confirms_without_opening_menu(self):
        tab = DialogueTab()
        runner = DialogueRunner(tab)
        with patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            await dialogue.run_dialogue(runner, ITEM)
        self.assertEqual(tab.actions.count("end"), 1)
        self.assertEqual(tab.actions.count("confirm_end"), 1)
        self.assertNotIn("reason_target", tab.actions)
        self.assertNotIn("choose_no_reason", tab.actions)
        self.assertEqual(tab.mouse_events, [])

    async def test_already_green_skips_all_actions(self):
        tab = DialogueTab()
        tab.confirmed = True
        runner = DialogueRunner(tab)
        await dialogue.run_dialogue(runner, ITEM)
        self.assertEqual(tab.actions, [])

    async def test_unknown_modal_does_not_confirm_or_advance(self):
        tab = DialogueTab(unknown_modal=True)
        runner = DialogueRunner(tab)
        with self.assertRaisesRegex(engine.StopRun, "unfamiliar confirmation"):
            await dialogue.run_dialogue(runner, ITEM)
        self.assertNotIn("confirm_end", tab.actions)

    async def test_missing_green_mark_stops_queue(self):
        tab = DialogueTab(green=False)
        runner = DialogueRunner(tab)
        with patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            with self.assertRaisesRegex(engine.StopRun, "green mark"):
                await dialogue.run_dialogue(runner, ITEM)
        self.assertEqual(tab.actions.count("end"), 1)
        self.assertEqual(tab.actions.count("confirm_end"), 1)

    async def test_restart_does_not_repeat_end_or_confirmation(self):
        tab = DialogueTab(green=False, persist_modal=True)
        first = DialogueRunner(tab)
        with patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            with self.assertRaisesRegex(engine.StopRun, "green mark"):
                await dialogue.run_dialogue(first, ITEM)
        second = DialogueRunner(tab)
        second.checkpoint = first.checkpoint
        with patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            with self.assertRaisesRegex(engine.StopRun, "green mark"):
                await dialogue.run_dialogue(second, ITEM)
        self.assertEqual(tab.actions.count("end"), 1)
        self.assertEqual(tab.actions.count("confirm_end"), 1)

    async def test_lost_end_click_result_reconciles_modal_on_restart(self):
        tab = DialogueTab(lose_end_result=True)
        first = DialogueRunner(tab)
        with self.assertRaisesRegex(OSError, "websocket closed"):
            await dialogue.run_dialogue(first, ITEM)
        self.assertEqual(first.checkpoint["dialogues"][ITEM["key"]]["phase"], "end_requested")
        second = DialogueRunner(tab)
        second.checkpoint = first.checkpoint
        with patch.object(dialogue.asyncio, "sleep", immediate_sleep):
            await dialogue.run_dialogue(second, ITEM)
        self.assertEqual(tab.actions.count("end"), 1)
        self.assertEqual(tab.actions.count("confirm_end"), 1)


if __name__ == "__main__":
    unittest.main()
