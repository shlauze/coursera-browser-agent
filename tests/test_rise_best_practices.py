"""Offline contract tests for the exact Best practices Rise scenario."""

import importlib
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
flow = importlib.import_module("rise_best_practices")
StopRun = importlib.import_module("coursera_start").StopRun


def scenario_snapshot(state, step=0):
    if state == "intro":
        text, prompt, options, answer = flow.INTRO, None, [], None
    elif state == "choice":
        entry = flow.CHOICES[step]
        text = entry["prompt"] + " " + " ".join(entry["options"])
        prompt, options, answer = (entry["prompt"], list(entry["options"]),
                                   entry["answer"])
    elif state == "continue":
        text, prompt, options, answer = flow.CONTINUATIONS[step], None, [], None
    else:
        step = len(flow.CHOICES)
        text, prompt, options, answer = flow.END, None, [], None
    return {
        "scenario_count": 1, "scenario_state": state, "scenario_step": step,
        "scenario_text": text, "scenario_prompt": prompt,
        "scenario_options": options, "scenario_answer": answer,
        "scenario_signature": json.dumps(
            [state, step, text, options], ensure_ascii=False,
            separators=(",", ":")),
    }


HARNESS = r"""
const fs = require('fs');
const data = JSON.parse(fs.readFileSync(0,'utf8'));
const flow = eval('(' + data.source + ')');
const clean = value => (value || '').replace(/\s+/g,' ').trim();
const visible = [{}];
let clicked = [];
function button(text, cls) {
  return {innerText:text,className:cls,type:'button',disabled:false,
    getClientRects:()=>visible,getAttribute:()=>null,
    matches:selector=>selector.split(',').some(part=>{
      const classes=[...part.matchAll(/\.([A-Za-z0-9_-]+)/g)].map(match=>match[1]);
      return classes.every(name=>cls.split(' ').includes(name));
    }),
    scrollIntoView:()=>{},click:()=>clicked.push(text)};
}
let scenarioButtons=[];
let prompt=null, text='';
const spec=data.spec;
if(spec.state==='intro') {
  text=data.intro;
  scenarioButtons=[button('CONTINUE','scenario-block__text__continue')];
} else if(spec.state==='terminal') {
  text=data.end;
  scenarioButtons=[button('START OVER','scenario-block__text__end')];
} else if(spec.state==='continue') {
  text=data.continuations[spec.step];
  scenarioButtons=[button('CONTINUE','scenario-block__dialogue__button')];
} else if(spec.state==='choice') {
  const choice=data.choices[spec.step];
  prompt=choice.prompt;
  text=prompt+' '+choice.options.join(' ');
  scenarioButtons=choice.options.map((option,index)=>
    button(String(index+1)+' '+option,
      'scenario-block__response scenario-block__response--'+index));
}
if(spec.lingeringPrompt && spec.state==='continue')
  prompt=data.choices[spec.step].prompt;
if(spec.mutateOption) scenarioButtons[0].innerText='1 Unknown changed option';
if(spec.transition) scenarioButtons=[];
const block={innerText:text,matches:()=>true,
  getClientRects:()=>visible,
  querySelectorAll:selector=>selector==='button,[role="button"]'?scenarioButtons:[],
  querySelector:selector=>selector==='.scenario-block__dialogue'&&prompt?
    {innerText:prompt}:null};
const headings=data.accordions.map(innerText=>({innerText}));
const page={URL:data.lessonUrl,
  querySelectorAll:selector=>selector==='.scenario-block'?[block]:
    selector==='button.blocks-accordion__header'?headings:[]};
const wrap={innerText:'Select the response you believe is the most appropriate',
  contains:node=>node===block};
const request={path:data.path,frame:data.frame,heading:'Best practices',
  frame_title:'Best practices',action:spec.action||'inspect',
  panels_read:true,
  scenario_signature:spec.signature,
  scenario_answer:spec.answer};
if(spec.wrongFrame) request.frame='https://unrelated.invalid/index.html';
const value=flow(page,wrap,request,clean);
const {buttons,...plain}=value;
process.stdout.write(JSON.stringify({value:plain,clicked}));
"""


def js_case(state, step=0, **changes):
    spec = {"state": state, "step": step, **changes}
    payload = {
        "source": flow.SCENARIO_JS, "path": flow.PATH, "frame": flow.FRAME,
        "lessonUrl": flow.FRAME.replace("index.html", "") +
        "scorm-content/scormcontent/index.html#/lessons/" + flow.LESSON,
        "intro": flow.INTRO, "end": flow.END,
        "choices": flow.CHOICES, "continuations": flow.CONTINUATIONS,
        "accordions": flow.ACCORDIONS, "spec": spec,
    }
    run = subprocess.run(["node", "-e", HARNESS], input=json.dumps(payload),
                         text=True, capture_output=True, cwd=ROOT, check=True)
    return json.loads(run.stdout)


class BestPracticesScenarioTests(unittest.TestCase):
    def test_all_visible_steps_are_recognized(self):
        for state, steps in (("intro", [0]),
                             ("choice", range(len(flow.CHOICES))),
                             ("continue", range(len(flow.CONTINUATIONS))),
                             ("terminal", [len(flow.CHOICES)])):
            for step in steps:
                with self.subTest(state=state, step=step):
                    result = js_case(state, step)["value"]
                    self.assertEqual(result["state"], state)
                    self.assertEqual(result["step"], step)
                    self.assertEqual(result["count"], 1)
                    mapped = {"scenario_" + key: result[key]
                              for key in ("count", "state", "step", "text", "prompt",
                                          "options", "answer", "signature")}
                    flow.validate_snapshot(mapped, {"path": flow.PATH})

    def test_choice_uses_exact_prompt_and_every_option(self):
        result = js_case("choice", 2, mutateOption=True)
        self.assertIn("error", result["value"])
        self.assertEqual(result["clicked"], [])

    def test_identity_and_transition_guards(self):
        self.assertIn("error", js_case("intro", wrongFrame=True)["value"])
        self.assertEqual(js_case("choice", 1, transition=True)["value"].get("pending"),
                         "The Best practices scenario is transitioning.")

    def test_feedback_normalizes_lingering_dialogue_prompt(self):
        result = js_case("continue", 3, lingeringPrompt=True)["value"]
        self.assertEqual(result["state"], "continue")
        self.assertIsNone(result["prompt"])
        mapped = {"scenario_" + key: result[key]
                  for key in ("count", "state", "step", "text", "prompt",
                              "options", "answer", "signature")}
        flow.validate_snapshot(mapped, {"path": flow.PATH})

    def test_action_requires_snapshot_signature(self):
        result = js_case("choice", 3, action="scenario_choice",
                         signature="wrong", answer=1)
        self.assertIn("error", result["value"])
        self.assertEqual(result["clicked"], [])

    def test_audited_choice_clicks_matching_text(self):
        baseline = js_case("choice", 3)["value"]
        result = js_case("choice", 3, action="scenario_choice",
                         signature=baseline["signature"], answer=1)
        self.assertEqual(result["value"]["clicked"], "scenario_choice")
        self.assertEqual(result["clicked"], [
            "2 " + flow.CHOICES[3]["options"][1]])

    def test_terminal_is_never_clicked(self):
        self.assertIsNone(flow.next_action(scenario_snapshot("terminal")))
        result = js_case("terminal", action="scenario_choice")
        self.assertIn("error", result["value"])
        self.assertEqual(result["clicked"], [])

    def test_python_contract_rejects_changed_visible_content(self):
        changed = scenario_snapshot("choice", 4)
        changed["scenario_options"][2] = "Consent is unnecessary"
        with self.assertRaises(StopRun):
            flow.validate_snapshot(changed, {"path": flow.PATH})
        self.assertTrue(flow._expected_transition(
            scenario_snapshot("choice", 4), scenario_snapshot("continue", 4)))
        self.assertFalse(flow._expected_transition(
            scenario_snapshot("choice", 4), scenario_snapshot("terminal")))


class BestPracticesRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_widget_routes_to_dedicated_handler(self):
        plugin = importlib.import_module("plugin_flow")
        runner = type("Runner", (), {"course": {"url":
            "https://www.coursera.org/learn/"
            "adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"}})()
        item = {"key": "ungradedWidget:WBdg2", "title":
                "Best practices Ungraded Plugin • 20 min", "url":
                "https://www.coursera.org" + flow.PATH}
        with patch.object(flow, "complete_best_practices", new_callable=AsyncMock) as done:
            await plugin.complete_plugin(runner, item)
        done.assert_awaited_once_with(runner, item)


class BestPracticesCompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.item = {"key": "ungradedWidget:WBdg2", "title":
                     "Best practices Ungraded Plugin • 20 min", "url":
                     "https://www.coursera.org" + flow.PATH}
        self.outer = {"ready": "complete", "frame": flow.FRAME,
                      "frame_title": "Best practices", "mark_count": 1,
                      "mark_disabled": False, "modal": False, "other": []}
        self.accordions = [
            {"id": "panel-" + str(index), "heading": heading,
             "content": ("Reviewed static reading content " * 8) + str(index),
             "expanded": True}
            for index, heading in enumerate(flow.ACCORDIONS)
        ]
        self.terminal = {
            "url": flow.FRAME,
            "lesson_url": flow.FRAME.replace("index.html", "") +
                "scorm-content/scormcontent/index.html#/lessons/" + flow.LESSON,
            "progress": "0% COMPLETE", "top": 500, "max": 500,
            "accordion_count": 8, "accordions": self.accordions,
            "accordion_signature": json.dumps([
                [entry["heading"], entry["content"]] for entry in self.accordions
            ]),
            **scenario_snapshot("terminal"),
        }

    async def _run(self, green):
        tab = type("Tab", (), {})()
        tab.evaluate = AsyncMock(side_effect=[
            dict(self.outer), dict(self.outer), {**self.outer, "clicked": "mark"}])
        runner = type("Runner", (), {})()
        runner.course = {"url":
            "https://www.coursera.org/learn/"
            "adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"}
        runner.tab = tab
        runner.verified_item = AsyncMock(side_effect=[False, False, green])
        runner.event = MagicMock()
        frame = type("Frame", (), {})()
        frame.evaluate = AsyncMock(return_value=self.terminal)
        frame.close = AsyncMock()
        with patch("scorm_flow.open_scorm_frame", new_callable=AsyncMock,
                   return_value=frame):
            if green:
                await flow.complete_best_practices(runner, self.item)
            else:
                with self.assertRaises(StopRun):
                    await flow.complete_best_practices(runner, self.item)
        frame.close.assert_awaited_once()
        self.assertEqual(tab.evaluate.await_count, 3)
        self.assertEqual(runner.verified_item.await_count, 3)
        self.assertEqual(runner.verified_item.await_args.kwargs, {"wait": 20})
        return runner

    async def test_mark_requires_green_coursera_confirmation(self):
        runner = await self._run(False)
        runner.event.assert_called_once_with(
            "best_practices_mark_clicked", key=self.item["key"])

    async def test_terminal_can_be_marked_only_after_evidence(self):
        await self._run(True)

    async def test_restart_reads_collapsed_panels_then_scrolls_before_one_mark(self):
        """A persisted Scenario End still requires fresh reading evidence."""
        state = deepcopy(self.terminal)
        state["top"] = 0
        for entry in state["accordions"]:
            entry["expanded"] = False
        actions = []

        async def inner(_frame, _request, action, *, args=None, **_kwargs):
            if action == "expand":
                panel = next(entry for entry in state["accordions"]
                             if entry["id"] == args["expand_id"])
                self.assertFalse(panel["expanded"])
                self.assertEqual(args["accordion_signature"],
                                 state["accordion_signature"])
                for entry in state["accordions"]:
                    entry["expanded"] = False
                panel["expanded"] = True
                actions.append((action, panel["heading"]))
            elif action == "scroll":
                self.assertTrue(args["panels_read"])
                state["top"] = state["max"]
                actions.append((action, None))
            else:
                self.assertEqual(action, "inspect")
            result = deepcopy(state)
            if action != "inspect":
                result["clicked"] = action
            return result

        tab = type("Tab", (), {})()
        tab.evaluate = AsyncMock(side_effect=[
            dict(self.outer), dict(self.outer), {**self.outer, "clicked": "mark"}])
        runner = type("Runner", (), {})()
        runner.course = {"url":
            "https://www.coursera.org/learn/"
            "adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"}
        runner.tab = tab
        runner.verified_item = AsyncMock(side_effect=[False, False, True])
        runner.event = MagicMock()
        frame = type("Frame", (), {})()
        frame.close = AsyncMock()
        with (patch("scorm_flow.open_scorm_frame", new_callable=AsyncMock,
                    return_value=frame),
              patch.object(flow, "_inner", side_effect=inner),
              patch.object(flow.asyncio, "sleep", new_callable=AsyncMock)):
            await flow.complete_best_practices(runner, self.item)
        self.assertEqual([name for name, _ in actions], ["expand"] * 8 + ["scroll"])
        self.assertEqual([heading for name, heading in actions if name == "expand"],
                         list(flow.ACCORDIONS))
        frame.close.assert_awaited_once()
        self.assertEqual(tab.evaluate.await_count, 3)
        runner.event.assert_called_once_with(
            "best_practices_mark_clicked", key=self.item["key"])
        self.assertEqual(runner.verified_item.await_args.kwargs, {"wait": 20})


if __name__ == "__main__":
    unittest.main()
