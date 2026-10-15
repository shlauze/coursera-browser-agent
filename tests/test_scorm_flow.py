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


class OuterTab:
    def __init__(self, *, confirm=True):
        self.confirm = confirm
        self.marked = False
        self.calls = []

    async def evaluate(self, expression):
        self.calls.append(expression)
        if '"action": "mark"' in expression:
            self.marked = True
            return {"clicked": "mark"}
        return {"ready": "complete", "frame": flow.THREAT_MODEL_FRAME,
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
        self.assertFalse(flow.is_static_scorm(flow.THREAT_MODEL_PATH, "Harmful outputs"))
        self.assertFalse(flow.is_static_scorm(flow.THREAT_MODEL_PATH.replace("3z0Im", "other"),
                                             "Threat model"))
        self.assertEqual(plugins.plugin_path(ITEM, COURSE), flow.THREAT_MODEL_PATH)

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
  selector==='div.page-wrap'?[wrap]:selector==='iframe'?[]:[],
  querySelector:selector=>selector.startsWith('button') && data.interactive?{}:null};
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
                    "request": {"frame": flow.THREAT_MODEL_FRAME, "action": "scroll"}}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                self.assertEqual("error" not in value["result"], case["accepted"])
                self.assertEqual(value["top"] > 0, case["accepted"])


if __name__ == "__main__":
    unittest.main()
