"""Offline checks for narrow Coursera ungraded plugin completion."""

import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
flow = importlib.import_module("plugin_flow")
engine = importlib.import_module("coursera_start")
COURSE = "https://www.coursera.org/learn/example-course"
LESSON = COURSE + "/ungradedPlugin/abc12/overview"
ITEM = {"key": "ungradedPlugin:abc12", "url": LESSON, "title": "Overview"}


class FakeTab:
    def __init__(self, *, activity=False, modal=False, confirm=True):
        self.activity = activity
        self.modal = modal
        self.confirm = confirm
        self.clicked = 0
        self.inspected = 0

    async def evaluate(self, expression):
        self.inspected += 1
        if '"action": "mark"' in expression:
            self.clicked += 1
            return {"clicked": "mark"}
        return {"url": LESSON, "ready": "complete", "heading": "Overview",
                "mark_count": 1, "mark_disabled": False,
                "activity_count": int(self.activity), "modal_count": int(self.modal),
                "other_actions": []}


class FakeRunner:
    def __init__(self, tab):
        self.tab = tab
        self.course = {"url": COURSE}
        self.events = []

    async def verified_item(self, item, wait=0):
        return self.tab.confirm and self.tab.clicked > 0

    def event(self, kind, **fields):
        self.events.append((kind, fields))


class PluginFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_outline_metadata_is_removed_from_exact_heading(self):
        self.assertEqual(flow.lesson_heading("Overview Ungraded Plugin • 5 min"), "Overview")
        self.assertEqual(flow.lesson_heading("Threat model Ungraded Widget · 15 minutes"),
                         "Threat model")
        self.assertEqual(flow.lesson_heading(
            "Overview Ungraded Plugin . Duration: 1 minute 1 min"), "Overview")
        self.assertEqual(flow.lesson_heading(
            "Adversarial inputs Ungraded Plugin • 25 min Resume"), "Adversarial inputs")
        self.assertEqual(flow.lesson_heading(
            "Adversarial inputs Ungraded Plugin \uFFFD 25 min Resume"), "Adversarial inputs")
        self.assertEqual(flow.lesson_heading(
            "Threat model Ungraded Widget · 15 minutes Get started"), "Threat model")
        self.assertEqual(flow.lesson_heading(
            "Overview Ungraded Plugin . Duration: 1 minute 1 min Resume"), "Overview")
        self.assertEqual(flow.lesson_heading("Overview"), "Overview")

    def test_unknown_outline_suffix_fails_closed(self):
        for title in ("Adversarial inputs Ungraded Plugin • 25 min Review",
                      "Adversarial inputs Ungraded Plugin \uFFFD 25 min Review",
                      "Adversarial inputs Ungraded Plugin \uFFFD Resume",
                      "Threat model Ungraded Widget · 15 minutes Resume now",
                      "Overview Ungraded Plugin • 5 min Submit"):
            with self.subTest(title=title):
                self.assertEqual(flow.lesson_heading(title), "")

    async def test_unknown_suffix_stops_before_browser_action(self):
        tab = FakeTab()
        item = {**ITEM, "title": "Overview Ungraded Plugin • 5 min Continue"}
        with self.assertRaisesRegex(engine.StopRun, "no roster heading"):
            await flow.complete_plugin(FakeRunner(tab), item)
        self.assertEqual(tab.inspected, 0)
        self.assertEqual(tab.clicked, 0)

    def test_exact_course_and_identity_are_required(self):
        self.assertEqual(flow.plugin_path(ITEM, COURSE),
                         "/learn/example-course/ungradedPlugin/abc12/overview")
        widget = {**ITEM, "key": "ungradedWidget:abc12",
                  "url": COURSE + "/ungradedWidget/abc12/overview"}
        self.assertEqual(flow.plugin_path(widget, COURSE),
                         "/learn/example-course/ungradedWidget/abc12/overview")
        for bad in ({**ITEM, "key": "ungradedPlugin:other"},
                    {**ITEM, "url": "https://example.org/learn/example-course/ungradedPlugin/abc12/overview"},
                    {**ITEM, "url": LESSON + "?redirect=https://example.org"}):
            with self.subTest(bad=bad), self.assertRaises(engine.StopRun):
                flow.plugin_path(bad, COURSE)

    async def test_explicit_mark_requires_green_site_confirmation(self):
        tab = FakeTab()
        runner = FakeRunner(tab)
        await flow.complete_plugin(runner, ITEM)
        self.assertEqual(tab.clicked, 1)
        self.assertEqual(runner.events[0][0], "plugin_mark_clicked")

    async def test_known_overview_frame_can_finish_loading_before_mark(self):
        class PendingFrameTab(FakeTab):
            async def evaluate(self, expression):
                result = await super().evaluate(expression)
                if '"action": "inspect"' in expression and self.inspected == 1:
                    result["activity_count"] = 1
                    result["frame_pending"] = True
                return result

        tab = PendingFrameTab()
        await flow.complete_plugin(FakeRunner(tab), ITEM, probe_delay=0)
        self.assertEqual(tab.inspected, 3)
        self.assertEqual(tab.clicked, 1)

    async def test_embedded_activity_never_receives_generic_mark(self):
        tab = FakeTab(activity=True)
        with self.assertRaisesRegex(engine.StopRun, "embedded activity"):
            await flow.complete_plugin(FakeRunner(tab), ITEM)
        self.assertEqual(tab.clicked, 0)

    async def test_modal_prevents_clicking_underneath(self):
        tab = FakeTab(modal=True)
        with self.assertRaisesRegex(engine.StopRun, "modal dialog"):
            await flow.complete_plugin(FakeRunner(tab), ITEM)
        self.assertEqual(tab.clicked, 0)

    async def test_mark_click_alone_is_not_completion(self):
        tab = FakeTab(confirm=False)
        with self.assertRaisesRegex(engine.StopRun, "green completion mark"):
            await flow.complete_plugin(FakeRunner(tab), ITEM)
        self.assertEqual(tab.clicked, 1)

    def test_page_script_rejects_other_actions_and_wrong_lesson(self):
        javascript = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
let clicks=0;
const main={hidden:false,inert:false,parentElement:null,getClientRects:()=>[1],
  getAttribute:()=>null,querySelectorAll:selector=>({
    h1:[heading],
    'button,[role="button"]':[mark,...(data.extraAction?[extra]:[])],
    'input,textarea,select,[contenteditable="true"],iframe':data.iframe?[frame]:[],
    iframe:data.iframe?[frame]:[]
  }[selector]||[])};
const element=text=>({innerText:text,hidden:false,inert:false,parentElement:main,
  getClientRects:()=>[1],getAttribute:()=>null,closest:()=>null});
const heading=element('Overview'),mark=element('Mark as complete');
mark.scrollIntoView=()=>{};mark.click=()=>clicks++;
const extra=element('Submit assignment'),frame=element('');
frame.src=data.frameSrc||'https://example.org/activity';frame.title=data.frameTitle||'Activity';
const modal=element('Blocking dialog');
const document={readyState:data.ready||'complete',querySelectorAll:selector=>
  selector==='main,[role="main"],#main-container'?[main]:
  selector==='[role="dialog"][aria-modal="true"]' && data.modal?[modal]:[]};
const context={document,URL,location:{href:data.url},
  getComputedStyle:()=>({display:'block',visibility:'visible'}),
  performance:{getEntriesByName:()=>data.frameLoaded?[{initiatorType:'iframe',responseEnd:10}]:[]}};
const result=vm.runInNewContext(data.script+'('+JSON.stringify(data.request)+')',context);
process.stdout.write(JSON.stringify({result,clicks}));
'''
        for case in (
            {"url": LESSON, "extraAction": False, "iframe": False, "modal": False, "clicked": 1},
            {"url": LESSON, "extraAction": True, "iframe": False, "modal": False, "clicked": 0},
            {"url": LESSON, "extraAction": False, "iframe": True, "modal": False, "clicked": 0},
            {"url": COURSE + "/ungradedWidget/abc12/overview", "extraAction": False,
             "iframe": True, "modal": False, "clicked": 1,
             "frameSrc": "https://d10o6em2qtnr4q.cloudfront.net/assets/5e2ef240d8d54159bd92c37815c44950/index.html",
             "frameTitle": "Topic overview", "frameLoaded": True},
            {"url": COURSE + "/ungradedWidget/abc12/overview", "extraAction": False,
             "iframe": True, "modal": False, "clicked": 1, "ready": "interactive",
             "frameSrc": "https://d10o6em2qtnr4q.cloudfront.net/assets/5e2ef240d8d54159bd92c37815c44950/index.html",
             "frameTitle": "Topic overview", "frameLoaded": True},
            {"url": COURSE + "/ungradedWidget/abc12/overview", "extraAction": False,
             "iframe": True, "modal": False, "clicked": 0,
             "frameSrc": "https://d10o6em2qtnr4q.cloudfront.net/assets/5e2ef240d8d54159bd92c37815c44950/index.html",
             "frameTitle": "Topic overview", "frameLoaded": False},
            {"url": LESSON, "extraAction": False, "iframe": False, "modal": True, "clicked": 0},
            {"url": COURSE + "/ungradedPlugin/other/overview", "extraAction": False,
             "iframe": False, "modal": False, "clicked": 0},
        ):
            with self.subTest(case=case):
                payload = {**case, "script": flow.PLUGIN_JS,
                           "request": {"path": "/learn/example-course/" +
                                       ("ungradedWidget" if "/ungradedWidget/" in case["url"]
                                        else "ungradedPlugin") + "/abc12/overview",
                                       "heading": "Overview", "action": "mark"}}
                result = subprocess.run(["node", "-e", javascript], input=json.dumps(payload),
                                        text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                self.assertEqual(value["clicks"], case["clicked"])
                if case["clicked"]:
                    self.assertEqual(value["result"]["clicked"], "mark")
                else:
                    self.assertIn("error", value["result"])
                    if case.get("frameLoaded") is False:
                        self.assertTrue(value["result"].get("frame_pending"))


if __name__ == "__main__":
    unittest.main()
