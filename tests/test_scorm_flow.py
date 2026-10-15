"""Offline checks for the audited static SCORM widget path."""

import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
flow = importlib.import_module("scorm_flow")
plugins = importlib.import_module("plugin_flow")
engine = importlib.import_module("coursera_start")
COURSE = "https://www.coursera.org/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
ITEM = {"key": "ungradedWidget:3z0Im", "title": "Threat model Ungraded Plugin • 15 min",
        "url": "https://www.coursera.org" + flow.THREAT_MODEL_PATH}
REMARKS_ITEM = {"key": "ungradedWidget:2sbQI",
                "title": "Adversarial inputs Ungraded Plugin \uFFFD 25 min Resume",
                "url": "https://www.coursera.org" + flow.ADVERSARIAL_INPUTS_PATH}


class OuterTab:
    def __init__(self, *, confirm=True, frame=flow.THREAT_MODEL_FRAME):
        self.confirm = confirm
        self.frame = frame
        self.marked = False
        self.calls = []

    async def evaluate(self, expression):
        self.calls.append(expression)
        if '"action": "mark"' in expression:
            self.marked = True
            return {"clicked": "mark"}
        return {"ready": "complete", "frame": self.frame,
                "mark_count": 1, "mark_disabled": False, "modal": False, "other": []}


class InnerTab:
    def __init__(self, *, changed=False):
        self.top = 0
        self.changed = changed
        self.closed = False

    async def evaluate(self, expression):
        if '"action": "scroll"' in expression:
            self.top = min(900, self.top + 450)
        return {"url": flow.THREAT_MODEL_FRAME, "lesson": "LESSON 1 OF 1",
                "text_length": 5339 + int(self.changed and self.top > 0),
                "top": self.top, "max": 900, "progress": "0% COMPLETE"}

    async def close(self):
        self.closed = True


class RemarksTab(InnerTab):
    def __init__(self, *, interactive=False):
        super().__init__()
        self.expanded = False
        self.interactive = interactive
        self.actions = []

    async def evaluate(self, expression):
        if self.interactive:
            return {"error": "The SCORM lesson contains an interactive activity."}
        action = ("expand" if '"action": "expand"' in expression else
                  "scroll" if '"action": "scroll"' in expression else "inspect")
        self.actions.append(action)
        if action == "expand":
            self.expanded = True
        elif action == "scroll":
            self.top = min(3330, self.top + 600)
        return {"url": flow.ADVERSARIAL_INPUTS_FRAME, "lesson": "LESSON 1 OF 1",
                "text_length": 2464 if self.expanded else 2229,
                "top": self.top, "max": 3330 if self.expanded else 3100,
                "progress": "0% COMPLETE", "accordion_expanded": self.expanded,
                "clicked": "Remarks" if action == "expand" else None}


class Runner:
    def __init__(self, tab):
        self.tab = tab
        self.course = {"url": COURSE}
        self.events = []

    async def verified_item(self, item, wait=0):
        return self.tab.marked and self.tab.confirm

    def event(self, name, **data):
        self.events.append((name, data))


class StaticScormTests(unittest.IsolatedAsyncioTestCase):
    def test_only_audited_exact_lesson_can_enter_static_flow(self):
        self.assertTrue(flow.is_static_scorm(flow.THREAT_MODEL_PATH, "Threat model"))
        self.assertTrue(flow.is_static_scorm(flow.ADVERSARIAL_INPUTS_PATH,
                                             "Adversarial inputs"))
        self.assertFalse(flow.is_static_scorm(flow.THREAT_MODEL_PATH, "Harmful outputs"))
        self.assertFalse(flow.is_static_scorm(
            flow.ADVERSARIAL_INPUTS_PATH.replace("2sbQI", "other"), "Adversarial inputs"))
        self.assertFalse(flow.is_static_scorm(flow.THREAT_MODEL_PATH.replace("3z0Im", "other"),
                                             "Threat model"))
        self.assertEqual(plugins.plugin_path(ITEM, COURSE), flow.THREAT_MODEL_PATH)
        self.assertEqual(plugins.plugin_path(REMARKS_ITEM, COURSE), flow.ADVERSARIAL_INPUTS_PATH)

    async def test_iframe_connection_requires_exact_parent_and_asset(self):
        class Owner:
            address = "ws://127.0.0.1:9223/devtools/page/EXPECTED"

            def __init__(self, parent):
                self.parent = parent

            async def call(self, method):
                self.assert_method = method
                return {"targetInfos": [{"type": "iframe", "parentId": self.parent,
                                         "targetId": "FRAME", "url": flow.THREAT_MODEL_FRAME}]}

        class FrameSocket:
            connected = False

            async def connect(self):
                self.connected = True

        socket = FrameSocket()
        pages = [{"type": "iframe", "id": "FRAME", "url": flow.THREAT_MODEL_FRAME,
                  "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/FRAME"}]
        with patch.object(engine, "local_json", return_value=pages), \
             patch.object(engine, "ChromeTab", return_value=socket):
            with self.assertRaisesRegex(engine.StopRun, "unique audited SCORM iframe"):
                await flow.open_scorm_frame(Owner("ANOTHER_TAB"))
            result = await flow.open_scorm_frame(Owner("EXPECTED"))
        self.assertIs(result, socket)
        self.assertTrue(socket.connected)

    async def test_plugin_routing_reads_static_widget_before_mark(self):
        tab, frame = OuterTab(), InnerTab()
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), ITEM)
        self.assertTrue(frame.closed)
        self.assertEqual(frame.top, 900)
        self.assertTrue(tab.marked)
        self.assertEqual(len(tab.calls), 2)

    async def test_changed_content_stops_before_coursera_mark(self):
        tab, frame = OuterTab(), InnerTab(changed=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "reading area changed"):
                await plugins.complete_plugin(Runner(tab), ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_remarks_are_expanded_and_read_before_mark(self):
        tab = OuterTab(frame=flow.ADVERSARIAL_INPUTS_FRAME)
        frame = RemarksTab()
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), REMARKS_ITEM)
        self.assertEqual(frame.actions[:3], ["inspect", "expand", "inspect"])
        self.assertEqual(frame.top, 3330)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)

    async def test_interactive_remarks_widget_cannot_be_marked(self):
        tab = OuterTab(frame=flow.ADVERSARIAL_INPUTS_FRAME)
        frame = RemarksTab(interactive=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)):
            with self.assertRaisesRegex(engine.StopRun, "interactive activity"):
                await plugins.complete_plugin(Runner(tab), REMARKS_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_coursera_green_mark_is_required_after_click(self):
        tab, frame = OuterTab(confirm=False), InnerTab()
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "green completion mark"):
                await plugins.complete_plugin(Runner(tab), ITEM)
        self.assertTrue(tab.marked)

    def test_inner_script_accepts_only_static_one_lesson_content(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const wrap={innerText:'A'.repeat(5339),scrollHeight:1500,clientHeight:600,scrollTop:0};
const page={readyState:'complete',querySelectorAll:selector=>
  selector==='div.page-wrap'?[wrap]:
  selector==='button,[role="button"]' && data.interactive?[{}]:[],
  querySelector:selector=>null};
const frame={contentDocument:page};
const labels=['SKIP TO LESSON','Open navigation menu','Open search menu','Close search menu'];
if(data.continue) labels.push('CONTINUE');
const controls=labels.map(innerText=>({innerText,textContent:innerText}));
const document={readyState:'complete',body:{innerText:'LESSON 1 OF 1'},
  querySelectorAll:selector=>selector==='iframe#content-frame'?[frame]:
    selector==='button,[role="button"]'?controls:
    selector==='input'?[{type:'search'}]:[],
  querySelector:selector=>selector==='.nav-sidebar-header__progress-text'?{innerText:'0% COMPLETE'}:null};
const result=vm.runInNewContext(data.script+'('+JSON.stringify(data.request)+')',
  {document,location:{href:data.url}});
process.stdout.write(JSON.stringify({result,top:wrap.scrollTop}));
'''
        for case in ({"interactive": False, "continue": False, "accepted": True},
                     {"interactive": True, "continue": False, "accepted": False},
                     {"interactive": False, "continue": True, "accepted": False}):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.INNER_JS, "url": flow.THREAT_MODEL_FRAME,
                    "request": {"frame": flow.THREAT_MODEL_FRAME, "action": "scroll",
                                "accordion": None}}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                self.assertEqual("error" not in value["result"], case["accepted"])
                self.assertEqual(value["top"] > 0, case["accepted"])

    def test_remarks_script_rejects_other_controls_and_checks_expanded_panel(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
let expanded=false;
const wrap={clientHeight:500,scrollTop:0,
  get innerText(){return 'A'.repeat(expanded?2464:2229)},
  get scrollHeight(){return expanded?3830:3600}};
const panel={innerText:'B'.repeat(data.shortPanel?20:233),
  matches:selector=>selector==='section.blocks-accordion__content',
  getAttribute:name=>name==='aria-labelledby'?'title-dynamic':null,
  querySelector:()=>null};
const remarks={innerText:'Remarks',type:'button',closest:selector=>selector==='h2'?{}:null,
  getAttribute:name=>name==='aria-controls'?'panel-dynamic':
    name==='aria-expanded'?(expanded?'true':'false'):null,
  click:()=>{expanded=true}};
const page={readyState:'complete',getElementById:id=>id==='panel-dynamic'?panel:null,
  querySelectorAll:selector=>selector==='div.page-wrap'?[wrap]:
    selector==='button.blocks-accordion__header'?[remarks]:
    selector==='button,[role="button"]'?[remarks,...(data.extra?[{}]:[])]:[],
  querySelector:()=>null};
const frame={contentDocument:page};
const controls=['SKIP TO LESSON','Open navigation menu','Open search menu','Close search menu']
  .map(innerText=>({innerText}));
const document={readyState:'complete',body:{innerText:'LESSON 1 OF 1'},
  querySelectorAll:selector=>selector==='iframe#content-frame'?[frame]:
    selector==='button,[role="button"]'?controls:
    selector==='input'?[{type:'search',disabled:true}]:[],
  querySelector:selector=>selector==='.nav-sidebar-header__progress-text'?{innerText:'0% COMPLETE'}:null};
const context={document,location:{href:data.frame}};
const run=action=>vm.runInNewContext(data.script+'('+JSON.stringify({
  frame:data.frame,accordion:'Remarks',action})+')',context);
const before=run('inspect'),open=run('expand'),after=run('inspect'),read=run('scroll');
process.stdout.write(JSON.stringify({before,open,after,read,top:wrap.scrollTop}));
'''
        for case in ({"extra": False, "shortPanel": False, "accepted": True},
                     {"extra": True, "shortPanel": False, "accepted": False},
                     {"extra": False, "shortPanel": True, "accepted": False}):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.INNER_JS, "frame": flow.ADVERSARIAL_INPUTS_FRAME}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                if case["accepted"]:
                    self.assertEqual(value["before"]["text_length"], 2229)
                    self.assertEqual(value["open"]["clicked"], "Remarks")
                    self.assertTrue(value["after"]["accordion_expanded"])
                    self.assertEqual(value["after"]["text_length"], 2464)
                    self.assertGreater(value["top"], 0)
                else:
                    self.assertTrue(any("error" in value[action] for action in
                                        ("before", "open", "after", "read")))
                    self.assertEqual(value["top"], 0)


if __name__ == "__main__":
    unittest.main()
