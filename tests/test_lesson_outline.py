"""Offline regression tests; no Chrome, Coursera or model requests are made."""

import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "coursera_start.py"
spec = importlib.util.spec_from_file_location("course_outline_regression", SOURCE)
course = importlib.util.module_from_spec(spec)
spec.loader.exec_module(course)
FIXTURE = json.loads((ROOT / "tests/fixtures/collapsed_course_outline.json").read_text(encoding="utf-8"))
ITEM = FIXTURE["item"]
COLLAPSED = FIXTURE["snapshot"]


def with_row(key=None, status="Not completed"):
    value = copy.deepcopy(COLLAPSED)
    row = copy.deepcopy(ITEM)
    if key:
        row["url"] = "https://www.coursera.org/learn/philosophy/supplement/" + key + "/other"
    row["aria_label"] = "About this Course, " + status
    row["evidence"] = ["aria-label: " + row["aria_label"]]
    value["items"] = [row]
    value["buttons"] = [{"label": "Collapse course outline", "disabled": False}]
    return value


class FakeTab:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.index = 0
        self.outline_calls = 0
        self.outline_expressions = []
        self.navigation = []

    async def navigate(self, url):
        self.navigation.append(url)

    async def evaluate(self, expression):
        if expression == course.PAGE_JS:
            value = self.snapshots[min(self.index, len(self.snapshots) - 1)]
            self.index += 1
            return copy.deepcopy(value)
        if expression.startswith(course.LESSON_OUTLINE_JS):
            self.outline_calls += 1
            self.outline_expressions.append(expression)
            return {"clicked": True, "label": "Expand course outline"}
        raise AssertionError("Unexpected browser action in offline test")


class OutlineRegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.sleeper = patch.object(course.asyncio, "sleep", new=AsyncMock())
        self.sleeper.start()
        self.addCleanup(self.sleeper.stop)

    def runner(self, *snapshots):
        tab = FakeTab(snapshots)
        return course.CourseRunner(tab, None, Path(self.directory.name))

    async def test_real_collapsed_snapshot_expands_then_waits_for_current_row(self):
        runner = self.runner(COLLAPSED, COLLAPSED, with_row())
        value = await runner.open_item(ITEM)
        self.assertIn(ITEM["key"], course.rows_by_key(value))
        self.assertEqual(runner.tab.outline_calls, 1)
        self.assertEqual(runner.tab.index, 3)
        self.assertEqual([e["event"] for e in runner.data["events"]], ["lesson_outline_opened"])
        self.assertFalse(await runner.verified_item(ITEM))

    async def test_already_open_outline_is_not_clicked(self):
        runner = self.runner(with_row())
        await runner.open_item(ITEM)
        self.assertEqual(runner.tab.outline_calls, 0)

    async def test_retries_module_outline_actions_until_green_row_is_visible(self):
        runner = self.runner(COLLAPSED, COLLAPSED, COLLAPSED, with_row(status="Completed"))
        runner._current_module = 1
        await runner.open_item(ITEM)
        self.assertEqual(runner.tab.outline_calls, 2)
        self.assertTrue(all(expression.endswith('("supplement:BvU8W",1)')
                            for expression in runner.tab.outline_expressions))
        self.assertTrue(await runner.verified_item(ITEM))

    async def test_queue_runner_uses_current_course_module_to_open_outline(self):
        runner = self.runner(COLLAPSED, with_row(status="Completed"))
        runner.course = {"current_module": 3}
        await runner.open_item(ITEM)
        self.assertEqual(runner.tab.outline_calls, 1)
        self.assertTrue(runner.tab.outline_expressions[0].endswith(
            '("supplement:BvU8W",3)'))

    async def test_captured_interactive_lesson_is_ready_but_not_completed(self):
        log = ROOT / "tests/fixtures/logs/course_v3_20261014_151755_619347/progress.json"
        snapshot = json.loads(log.read_text(encoding="utf-8"))["latest"]
        item = course.rows_by_key(snapshot)["lecture:WgOhW"]
        runner = self.runner(snapshot)
        value = await runner.open_item(item)
        self.assertEqual(value["ready"], "interactive")
        self.assertEqual(runner.tab.index, 1)
        self.assertEqual(runner.tab.outline_calls, 0)
        self.assertFalse(await runner.verified_item(item))

    async def test_interactive_lab_and_dialogue_heading_can_load(self):
        for kind, heading, suffix in (
            ("ungradedLab", "Build a Perceptron", "Lab"),
            ("coach", "Deep Learning in Context", "Dialogue"),
        ):
            with self.subTest(kind=kind):
                url = f"https://www.coursera.org/learn/philosophy/{kind}/sample/lesson"
                item = {"url":url, "key":f"{kind}:sample", "title":f"{heading} {suffix} • 15 min"}
                snapshot = {"url":url, "ready":"interactive", "heading":heading,
                            "items":[{"url":url, "aria_label":f"{heading}, Not completed",
                                      "evidence":[f"aria-label: {heading}, Not completed"]}]}
                runner = self.runner(snapshot)
                await runner.open_item(item)
                self.assertEqual(runner.tab.index, 1)
                self.assertEqual(runner.tab.outline_calls, 0)

    async def test_interactive_document_still_requires_target_heading_and_outline(self):
        for mutation in ({"heading":"Another lesson"}, {"heading":"About"},
                         {"items":[]}, {"ready":"loading"}):
            with self.subTest(mutation=mutation):
                snapshot = with_row()
                snapshot["ready"] = "interactive"
                snapshot.update(mutation)
                runner = self.runner(snapshot)
                with self.assertRaisesRegex(course.StopRun, "lesson did not load"):
                    await runner.open_item(ITEM)
                self.assertEqual(runner.tab.outline_calls, 0)
                self.assertNotIn("item_verified", [e["event"] for e in runner.data["events"]])

    async def test_loaded_lesson_without_outline_has_specific_error_and_no_completion(self):
        runner = self.runner(COLLAPSED)
        with self.assertRaisesRegex(course.StopRun, "lesson loaded, but its course outline"):
            await runner.open_item(ITEM)
        self.assertFalse(await runner.verified_item(ITEM))
        self.assertNotIn("item_verified", [e["event"] for e in runner.data["events"]])

    async def test_sibling_row_does_not_count_as_loaded_target(self):
        runner = self.runner(with_row("sibling", "Completed"))
        with self.assertRaisesRegex(course.StopRun, "course outline"):
            await runner.open_item(ITEM)
        self.assertFalse(await runner.verified_item(ITEM))

    async def test_delayed_navigation_does_not_expand_previous_page(self):
        old = with_row("sibling")
        old["url"] = old["items"][0]["url"]
        runner = self.runner(old, with_row())
        await runner.open_item(ITEM)
        self.assertEqual(runner.tab.outline_calls, 0)

    async def test_wrong_url_reports_loading_error_without_outline_action(self):
        wrong = copy.deepcopy(COLLAPSED)
        wrong["url"] = "https://www.coursera.org/learn/philosophy/home/module/1"
        runner = self.runner(wrong)
        with self.assertRaisesRegex(course.StopRun, "lesson did not load"):
            await runner.open_item(ITEM)
        self.assertEqual(runner.tab.outline_calls, 0)

    async def test_expanded_row_still_requires_explicit_completed_label(self):
        runner = self.runner(with_row(status="Not completed"), with_row(status="Completed"))
        self.assertFalse(await runner.verified_item(ITEM))
        self.assertTrue(await runner.verified_item(ITEM))


class OutlineJavascriptTests(unittest.TestCase):
    def test_actual_dom_selector_and_page_guards(self):
        harness = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=JSON.parse(fs.readFileSync(0,'utf8'));
function run({path='/learn/philosophy/supplement/BvU8W/about-this-course',
              hostname='www.coursera.org', key='supplement:BvU8W', buttons=[]}) {
  const clicked=[];
  const elements=buttons.map((b,i)=>({
    innerText:b.text || '', disabled:!!b.disabled,
    getClientRects:()=>b.hidden ? [] : [{}],
    getAttribute:name=>name==='aria-label' ? (b.aria || '') : name==='aria-disabled' ? (b.ariaDisabled || '') : '',
    click:()=>clicked.push(i), style:{visibility:b.visibility || 'visible'}
  }));
  const ctx={location:{hostname,pathname:path},
    document:{querySelectorAll:()=>elements},getComputedStyle:e=>e.style};
  const result=vm.runInNewContext('('+source+')('+JSON.stringify(key)+')',ctx);
  return {result,clicked};
}
const expand={aria:'Expand course outline'};
assert.deepEqual(run({buttons:[expand]}).clicked,[0]);
assert.deepEqual(run({buttons:[{aria:'Expand course outline',text:'menu icon'}]}).clicked,[0]);
assert.deepEqual(run({buttons:[{text:'  Expand   course outline  '}]}).clicked,[0]);
assert.deepEqual(run({buttons:[{aria:'Collapse course outline'}]}).clicked,[]);
assert.deepEqual(run({buttons:[expand,expand]}).clicked,[]);
assert.deepEqual(run({buttons:[{...expand,hidden:true}]}).clicked,[]);
assert.deepEqual(run({buttons:[{...expand,visibility:'hidden'}]}).clicked,[]);
assert.deepEqual(run({buttons:[{...expand,disabled:true}]}).clicked,[]);
assert.deepEqual(run({buttons:[{...expand,ariaDisabled:'true'}]}).clicked,[]);
assert.deepEqual(run({path:'/learn/philosophy/supplement/other/item',buttons:[expand]}).clicked,[]);
assert.deepEqual(run({path:'/learn/philosophy/home/module/1',buttons:[expand]}).clicked,[]);
assert.deepEqual(run({hostname:'example.org',buttons:[expand]}).clicked,[]);
assert.deepEqual(run({path:'/learn/other/supplement/BvU8W/item',buttons:[expand]}).clicked,[]);
assert.deepEqual(run({path:'/learn/philosophy/exam/quiz-id/item',key:'assignment:quiz-id',buttons:[expand]}).clicked,[0]);
process.stdout.write('14 DOM selector/guard checks passed');
"""
        result = subprocess.run(["node", "-e", harness], input=json.dumps(course.LESSON_OUTLINE_JS),
                                text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("14 DOM selector/guard checks passed", result.stdout)

    def test_module_controls_and_scroll_only_target_course_outline(self):
        harness = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=JSON.parse(fs.readFileSync(0,'utf8'));
function run({path='/learn/philosophy/supplement/BvU8W/about-this-course',
              moduleExpanded='true', atTop=false, lessonExpanded='true',
              moduleNumber=1, hideModule1=false}) {
  const clicked=[];
  const scroll={scrollHeight:900,clientHeight:350,scrollTop:atTop ? 0 : 650,
    parentElement:null,style:{visibility:'visible',overflowY:'auto'}};
  const specs=(hideModule1 ? [] : [['Module 1: Introduction',moduleExpanded]]).concat([
    ['Lesson 1: Getting Started',lessonExpanded],['Module 2: Next','false'],
    ['Lesson 1: Different module','false']]);
  const elements=specs.map(([title,expanded],i)=>({
    innerText:title,disabled:false,parentElement:scroll,
    getClientRects:()=>[{}],
    getAttribute:name=>name==='aria-label' ? '' :
      name==='aria-expanded' ? expanded : name==='aria-disabled' ? '' : '',
    click:()=>clicked.push(i),
    scrollIntoView:()=>{scroll.scrollTop=0;},
    style:{visibility:'visible'}
  }));
  const ctx={location:{hostname:'www.coursera.org',pathname:path},
    document:{querySelectorAll:()=>elements},getComputedStyle:e=>e.style};
  const result=vm.runInNewContext('('+source+')('+JSON.stringify('supplement:BvU8W')+','+
    JSON.stringify(moduleNumber)+')',ctx);
  return {result,clicked,scrollTop:scroll.scrollTop};
}
assert.deepEqual(run({moduleExpanded:'false'}).clicked,[0]);
const moved=run({});
assert.equal(moved.scrollTop,0);
assert.equal(moved.result.scrolled,true);
assert.deepEqual(moved.clicked,[]);
assert.deepEqual(run({atTop:true,lessonExpanded:'false'}).clicked,[1]);
assert.deepEqual(run({atTop:true}).clicked,[]);
assert.deepEqual(run({moduleNumber:3,atTop:true}).clicked,[]);
assert.deepEqual(run({path:'/learn/other/supplement/BvU8W/item'}).clicked,[]);
const virtualized=run({hideModule1:true});
assert.equal(virtualized.result.scrolled,true);
assert.equal(virtualized.scrollTop,370);
assert.deepEqual(virtualized.clicked,[]);
process.stdout.write('8 module expansion/scroll checks passed');
"""
        result = subprocess.run(["node", "-e", harness], input=json.dumps(course.LESSON_OUTLINE_JS),
                                text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("8 module expansion/scroll checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
