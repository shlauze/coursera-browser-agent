"""Resume-lab selector and persisted-completion regressions, with no browser or network."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lab_flow as lab
import coursera_start as engine
from tests.test_lab_flow import FakeTab, ITEM, COURSE, LESSON


class ResumeTab(FakeTab):
    def __init__(self, *args, visible_launches=("Resume lab",), **kwargs):
        super().__init__(*args, **kwargs)
        self.visible_launches = visible_launches

    async def evaluate(self, expression):
        result = await super().evaluate(expression)
        if expression.startswith(lab.LAB_PAGE_JS) and isinstance(result, dict) and "launch_count" in result:
            result["launch_count"] = len(self.visible_launches)
            result["launch_label"] = self.visible_launches[0] if len(self.visible_launches) == 1 else None
        return result


class ResumeLabTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sleep = patch.object(lab.asyncio, "sleep", new=AsyncMock())
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.probes = patch.multiple(lab, PAGE_PROBES=3, LAUNCH_PROBES=3, READY_PROBES=3)
        self.probes.start()
        self.addCleanup(self.probes.stop)

    async def test_resume_lab_waits_for_external_site_then_verified_mark(self):
        tab = ResumeTab()
        loaded = {"url": tab.popup_url, "ready": "complete", "rendered": True, "browser_error": False}
        events = []
        async def verify(item, wait=0):
            return tab.marked
        with patch.object(lab, "_external_target_snapshot", new=AsyncMock(return_value=loaded)):
            await lab.complete_lab(tab, ITEM, COURSE, verify_completion=verify,
                                   event=lambda kind, **fields: events.append((kind, fields)))
        self.assertEqual(tab.actions, ["launch", ("close", "popup"), ("activate", "source"), "mark"])
        self.assertEqual(events[0], ("lab_launched", {"key": ITEM["key"], "action": "Resume lab"}))
        self.assertTrue(tab.marked)

    async def test_launch_and_resume_together_are_ambiguous_and_never_clicked(self):
        tab = ResumeTab(visible_launches=("Launch lab", "Resume lab"))
        with self.assertRaisesRegex(engine.StopRun, "exactly one"):
            await lab.complete_lab(tab, ITEM, COURSE, verify_completion=AsyncMock(return_value=False))
        self.assertEqual(tab.actions, [])

    async def test_resume_button_without_persisted_green_mark_stops(self):
        tab = ResumeTab(hide_actions_when_marked=True)
        loaded = {"url": tab.popup_url, "ready": "complete", "rendered": True, "browser_error": False}
        with patch.object(lab, "_external_target_snapshot", new=AsyncMock(return_value=loaded)):
            with self.assertRaisesRegex(engine.StopRun, "green Coursera completion check"):
                await lab.complete_lab(tab, ITEM, COURSE,
                                       verify_completion=AsyncMock(return_value=False))
        self.assertEqual(tab.actions.count("mark"), 1)

    async def test_dom_accepts_only_one_launch_or_resume_button(self):
        script = r'''
const fs=require('node:fs'),vm=require('node:vm');
const p=JSON.parse(fs.readFileSync(0,'utf8'));
const body={parentElement:null,hidden:false,inert:false,getAttribute:()=>null};
const element=(text)=>({innerText:text,parentElement:body,hidden:false,inert:false,
  disabled:false,getClientRects:()=>[1],getAttribute:()=>null,
  matches:()=>false,closest:()=>null});
const heading=element(p.heading),buttons=p.buttons.map(element);
body.querySelectorAll=s=>s==='h1'?[heading]:s==='button,a[href],[role="button"]'?buttons:[];
const document={body,readyState:'complete',querySelectorAll:()=>[]};
const context={document,URL,location:{href:p.url},getComputedStyle:()=>({display:'block',visibility:'visible'})};
const result=vm.runInNewContext(p.script+'('+JSON.stringify(p.request)+')',context);
process.stdout.write(JSON.stringify(result));
'''
        path = "/learn/deep-learning-with-pytorch/ungradedLab/VZXic/lab-build-and-visualize-a-perceptron-from-scratch"
        for buttons, count, label in [
            (["Resume lab", "Mark as completed"], 1, "Resume lab"),
            (["Launch lab", "Resume lab"], 2, None),
            (["Resume notebook", "Mark as completed"], 0, None),
        ]:
            with self.subTest(buttons=buttons):
                payload={"script":lab.LAB_PAGE_JS,"url":LESSON,"heading":ITEM["title"],
                         "buttons":buttons,"request":{"path":path,"heading":ITEM["title"],"action":"inspect"}}
                output=subprocess.run(["node","-e",script],input=json.dumps(payload),text=True,
                                      capture_output=True,check=True)
                result=json.loads(output.stdout)
                self.assertEqual(result["launch_count"],count)
                self.assertEqual(result["launch_label"],label)
