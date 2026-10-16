"""Offline regression tests for a Rise Topic summary learning-outcome list."""

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rise_scorm_flow as flow
from coursera_start import StopRun
from test_rise_scorm_flow import InnerTab, OuterTab, Runner, ITEM, FRAME, action


class SummaryChecklistTab(InnerTab):
    CONTENTS = [
        "Recommend guidelines and common defences against AI security attacks.",
        "Apply AI controls to mitigate security risks.",
        "Contrast the trade-offs between security, utility, and robustness of AI models.",
        "Plan processes to conduct AI security testing and benchmarking.",
    ]

    def __init__(self, *, prechecked=(), stuck=False, corrupt=False):
        super().__init__([{
            "accordions": 0, "cards": 0, "max": 700, "continue": False,
        }])
        self.checked = set(prechecked)
        self.stuck = stuck
        self.corrupt = corrupt
        self.check_calls = []

    async def evaluate(self, expression):
        operation = action(expression)
        if operation == "checklist_check":
            request = json.loads(expression[len(flow.INNER_JS) + 1:-1])
            index = request["checklist_index"]
            assert request["checklist_signature"] == json.dumps(self.CONTENTS)
            assert request["checklist_checked_count"] == len(self.checked)
            assert index not in self.checked
            self.check_calls.append(index)
            if not self.stuck:
                self.checked.add(index)
        snapshot = await super().evaluate(expression)
        snapshot.update({
            "checklist_count": len(self.CONTENTS) + int(self.corrupt),
            "checklist_checked_count": len(self.checked),
            "checklist": [
                {"content": content, "checked": index in self.checked}
                for index, content in enumerate(self.CONTENTS, 1)
            ],
            "checklist_signature": json.dumps(self.CONTENTS),
        })
        return snapshot


class CompletionGuard(OuterTab):
    def __init__(self, inner):
        super().__init__()
        self.inner = inner

    async def evaluate(self, expression):
        if action(expression) == "mark":
            assert self.inner.checked == {1, 2, 3, 4}
            assert self.inner.top == 700
        return await super().evaluate(expression)


class SummaryChecklistFlowTests(unittest.IsolatedAsyncioTestCase):
    async def run_flow(self, inner, runner):
        with patch.object(flow, "open_scorm_frame", return_value=inner) as opened:
            await flow.complete_rise_scorm(
                runner, ITEM, settle_delay=0, terminal_delay=.001
            )
            opened.assert_awaited_once_with(runner.tab, FRAME)

    async def test_all_four_outcomes_checked_before_coursera_mark(self):
        inner = SummaryChecklistTab()
        runner = Runner(CompletionGuard(inner))
        await self.run_flow(inner, runner)
        self.assertEqual(inner.check_calls, [1, 2, 3, 4])
        self.assertTrue(runner.tab.marked)
        self.assertTrue(inner.closed)

    async def test_resume_checks_only_unchecked_outcomes(self):
        inner = SummaryChecklistTab(prechecked={1, 3})
        runner = Runner(CompletionGuard(inner))
        await self.run_flow(inner, runner)
        self.assertEqual(inner.check_calls, [2, 4])
        self.assertTrue(runner.tab.marked)

    async def test_checkbox_that_does_not_stick_never_marks(self):
        inner = SummaryChecklistTab(stuck=True)
        runner = Runner()
        with self.assertRaisesRegex(StopRun, "did not advance"):
            await self.run_flow(inner, runner)
        self.assertFalse(runner.tab.marked)
        self.assertTrue(inner.closed)

    async def test_inconsistent_checklist_never_marks(self):
        inner = SummaryChecklistTab(corrupt=True)
        runner = Runner()
        with self.assertRaisesRegex(StopRun, "checklist is incomplete"):
            await self.run_flow(inner, runner)
        self.assertFalse(runner.tab.marked)
        self.assertTrue(inner.closed)


class SummaryChecklistDomTests(unittest.TestCase):
    def test_observed_topic_summary_dom_and_arbitrary_activities(self):
        harness = r'''
const fs=require('node:fs');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const guard=new Function('return '+data.guard)();
const clean=value=>(value||'').replace(/\s+/g,' ').trim();
function scene(kind, topic='Summary') {
  const labels=[],inputs=[];
  const contents=topic==='Overview'?data.overviewContents:data.contents;
  const items=contents.map((content,index)=>{
    const input={checked:index===0,disabled:false,required:false,
      hasAttribute:()=>false,closest:selector=>kind==='inForm'?{}:null};
    const contentNode={innerText:kind==='duplicate'?contents[0]:content};
    const item={matches:selector=>selector==='li.block-list__item--checkboxes[role="listitem"]',
      querySelector:selector=>selector==='label.block-list__option'?label:null,
      querySelectorAll:selector=>selector==='label'?[label]:selector==='input'?[input]:[]};
    const label={parentElement:item,getClientRects:()=>[{}],
      querySelector:selector=>selector==='input.block-list__checkbox-input[type="checkbox"]'?input:
        selector==='.block-list__content'?contentNode:null};
    labels.push(label);inputs.push(input);
    return item;
  });
  const list={children:items};
  const group={querySelector:selector=>selector==='ul.block-list__list[role="list"]'?list:
      selector.startsWith('button,[role="button"],form')&&
      ['quiz','form'].includes(kind)?{}:null,
    querySelectorAll:selector=>selector==='ul.block-list__list'?[list]:
      selector==='input'?inputs:[]};
  const heading={innerText:kind==='wrongHeading'?'Topic summary':
    topic==='Overview'?'Topic overview':'Topic summary'};
  const wrap={contains:node=>node===group,
    querySelector:selector=>selector==='h1.lesson-header__title'?heading:null};
  const page={querySelectorAll:selector=>
    selector==='div.block-list.block-list--checkboxes'?(kind==='noGroup'?[]:[group]):
    selector==='input.block-list__checkbox-input[type="checkbox"]'?inputs:[]};
  const result=guard(page,wrap,
    kind==='wrongText'?'Select the correct answer':
      topic==='Overview'?'Throughout this topic, you will:':'You are now able to:',
    kind==='wrongTitle'?'Topic 4: Quiz':'Topic 4: '+topic,clean,()=>null);
  return {error:result.error||null,checklist:result.checklist||[],
    inputCount:result.inputs?.length||0,signature:result.signature||null,
    checkedCount:result.checkedCount||0};
}
const result={};
for(const kind of ['valid','quiz','form','inForm','duplicate','wrongText',
                    'wrongTitle','noGroup'])result[kind]=scene(kind);
for(const kind of ['valid','quiz','form','inForm','duplicate','wrongText',
                    'wrongTitle','wrongHeading','noGroup'])
  result['overview_'+kind]=scene(kind,'Overview');
process.stdout.write(JSON.stringify(result));
'''
        result = subprocess.run(
            ["node", "-e", harness],
            input=json.dumps({
                "guard": flow.LEARNING_OUTCOME_CHECKLIST_JS,
                "contents": SummaryChecklistTab.CONTENTS,
                "overviewContents": [
                    "Identify the emerging need for responsible AI.",
                    "Devise examples of fairness-bias and trustworthy issues in AI.",
                    "Outline the demand for ethically labelled data for training AI models.",
                    "Summarise frameworks, policies, and Governance, Regulation, Compliance for responsible AI.",
                ],
            }),
            capture_output=True, text=True, check=False, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshots = json.loads(result.stdout)
        valid = snapshots["valid"]
        self.assertIsNone(valid["error"])
        self.assertEqual(valid["inputCount"], 4)
        self.assertEqual(valid["checkedCount"], 1)
        self.assertEqual(valid["signature"], json.dumps(
            SummaryChecklistTab.CONTENTS, separators=(",", ":")))
        for kind in ("quiz", "form", "inForm", "duplicate", "wrongText",
                     "wrongTitle", "noGroup"):
            with self.subTest(kind=kind):
                self.assertIsNotNone(snapshots[kind]["error"])
        overview = snapshots["overview_valid"]
        self.assertIsNone(overview["error"])
        self.assertEqual(overview["inputCount"], 4)
        self.assertEqual(overview["checkedCount"], 1)
        for kind in ("quiz", "form", "inForm", "duplicate", "wrongText",
                     "wrongTitle", "wrongHeading", "noGroup"):
            with self.subTest(kind="overview_" + kind):
                self.assertIsNotNone(snapshots["overview_" + kind]["error"])


if __name__ == "__main__":
    unittest.main()
