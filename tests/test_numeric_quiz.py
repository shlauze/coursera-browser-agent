"""Offline regression for a quiz mixing choices with a numeric answer field.

The JavaScript fixture executes the runner's real DOM script in Node. It does not
open Chrome, contact Coursera or Groq, or submit an assignment.
"""

import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import quiz_history_import
import quiz_memory


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("numeric_quiz_engine", ROOT / "coursera_start.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

KEY = "assignment:fixture"
URL = "https://www.coursera.org/learn/philosophy/assignment-submission/fixture/example/attempt"
CHOICE_STEM = "1. Question 1 Which letter comes first?"
NUMBER_STEM = "2. Question 2 How many letters are in ABC?"


def mixed_ui():
    return {
        "url": URL,
        "editable_form": True,
        "page_text": "Question 1\nWhich letter comes first?\nA\nB\nQuestion 2\nHow many letters are in ABC?\nEnter answer here",
        "controls": [
            {"id": "c0", "type": "radio", "name": "letters", "value": "a",
             "label": "A", "question": CHOICE_STEM, "kind": "answer",
             "selected": False, "disabled": False, "in_scope": True},
            {"id": "c1", "type": "radio", "name": "letters", "value": "b",
             "label": "B", "question": CHOICE_STEM, "kind": "answer",
             "selected": False, "disabled": False, "in_scope": True},
            {"id": "c2", "type": "number", "name": "count", "value": "",
             "label": "Enter answer here", "question": NUMBER_STEM,
             "min": "0", "max": "10", "step": "1", "kind": "answer",
             "selected": False, "disabled": False, "in_scope": True},
        ],
    }


NODE_DOM = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));

async function execute(test) {
  const ui = test.ui;
  const clicks = [], events = [];
  const style = {display:'block', visibility:'visible', opacity:'1'};
  const rect = () => [{width:100, height:20}];
  const body = {innerText:ui.page_text, parentElement:null,
    matches:s=>s==='body' || s==='body,main,form',
    getClientRects:rect, getAttribute:()=>null,
    contains:()=>true};
  const groups = new Map();
  class Input {
    constructor(c, index, group) {
      this.tagName='INPUT'; this.type=c.type; this.name=c.name;
      this._value=c.value || ''; this.min=c.min || ''; this.max=c.max || '';
      this.step=c.step || ''; this.checked=!!c.selected;
      this.disabled=!!c.disabled; this.readOnly=false; this.isConnected=true;
      this.index=index; this.parentElement=group;
      this.labels=[{innerText:c.label, getClientRects:rect, scrollIntoView(){}}];
    }
    get value() { return this._value; }
    set value(value) { this._value=String(value); }
    getClientRects() { return rect(); }
    getAttribute(name) {
      if (name==='min' || name==='max' || name==='step') return this[name] || null;
      return null;
    }
    matches(selector) {
      if (selector==='input') return true;
      if (selector==='body,main,form') return false;
      return selector===`input[type="${this.type}"]`;
    }
    closest() { return null; }
    querySelector() { return null; }
    scrollIntoView() {}
    compareDocumentPosition(other) { return this.index<other.index?4:2; }
    click() { this.checked=true; clicks.push(this.index); }
    dispatchEvent(event) { events.push({index:this.index, type:event.type}); return true; }
  }
  const controls = (ui.controls || []).map((c,i) => {
    const key=c.name || c.question;
    let group=groups.get(key);
    if (!group) {
      group={innerText:c.question, textContent:c.question, parentElement:body,
        matches:()=>false, getClientRects:rect, getAttribute:()=>null,
        contains:e=>e.parentElement===group, querySelectorAll:()=>[]};
      groups.set(key,group);
    }
    return new Input(c,i,group);
  });
  const actionSelector='button,[role="button"],a[href],[role="link"],input[type="submit"],input[type="button"]';
  const actions=[];
  let confirmation=null;
  if (test.confirmation) {
    confirmation={innerText:'Ready to submit? Submit Cancel', parentElement:body,
      getClientRects:rect, getAttribute:()=>null, matches:()=>false,
      contains:e=>actions.includes(e),
      querySelectorAll:selector=>selector===actionSelector?actions:[]};
    for (const label of ['Submit','Cancel']) {
      actions.push({innerText:label, tagName:'BUTTON', parentElement:confirmation,
        disabled:false, getClientRects:rect, getAttribute:()=>null,
        matches:selector=>selector==='button,[role="button"]',
        closest:()=>null,
        scrollIntoView(){}, click(){clicks.push(label);}});
    }
  }
  const query = selector => {
    if (selector===actionSelector) return actions;
    if (selector.includes('textarea,select,input:not'))
      return controls.filter(c=>!['radio','checkbox','hidden','submit','button'].includes(c.type) &&
        !(selector.includes(':not([type="number"])') && c.type==='number'));
    if (selector==='input,textarea,select,[role="checkbox"],[role="radio"]' ||
        selector==='input,textarea,select' || selector==='input,textarea') return controls;
    if (selector==='input') return controls;
    const types=[...selector.matchAll(/input\[type=["']?([a-z]+)["']?\]/g)].map(match=>match[1]);
    if (types.length) return controls.filter(c=>types.includes(c.type));
    return [];
  };
  const document = {
    body, getElementById:()=>null, querySelector:()=>null, querySelectorAll:query,
    contains:()=>true
  };
  const location = new URL(ui.url);
  const context = {document, location, Node:{DOCUMENT_POSITION_FOLLOWING:4},
    HTMLInputElement:Input,
    Event:class Event { constructor(type,options) { this.type=type; this.bubbles=!!options?.bubbles; } },
    InputEvent:class InputEvent { constructor(type,options) { this.type=type; this.bubbles=!!options?.bubbles; } },
    getComputedStyle:()=>style, setTimeout:callback=>callback(), URL};
  const request=test.request || {key:payload.key,action:'inspect'};
  const result=await vm.runInNewContext(payload.code,context)(request);
  return {result, clicks, events,
    fields:controls.map(c=>({type:c.type,value:c.value,checked:c.checked}))};
}

Promise.all(payload.cases.map(execute))
  .then(values=>process.stdout.write(JSON.stringify(values)))
  .catch(error=>{console.error(error);process.exit(1);});
"""


def javascript(*cases):
    payload = {"code": engine.QUIZ_UI_JS, "key": KEY, "cases": cases}
    result = subprocess.run(
        ["node", "-e", NODE_DOM], input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise AssertionError(result.stderr[-2000:])
    return json.loads(result.stdout)


def mixed_plan():
    return {"cannot_answer": False, "answers": [
        {"question_id": "q1", "selected_controls": ["c0"],
         "numeric_answer": "", "reason": "Fixture choice"},
        {"question_id": "q2", "selected_controls": [],
         "numeric_answer": "3", "reason": "Fixture number"},
    ]}


def graded_feedback():
    return [
        {"question_number": 1,
         "text": "Question 1\nWhich letter comes first?\nA\n1 / 1 point"},
        {"question_number": 2,
         "text": "Question 2\nHow many letters are in ABC?\nYour answer: 3\n1 / 1 point"},
    ]


class NumericQuestionTests(unittest.TestCase):
    def test_mixed_question_extraction_and_schema(self):
        questions = engine.build_quiz_questions(mixed_ui())
        self.assertEqual([q["selection"] for q in questions], ["one", "number"])
        self.assertEqual(questions[1]["text"], NUMBER_STEM)
        self.assertEqual(questions[1]["control_id"], "c2")
        self.assertEqual(questions[1]["options"], [])
        for key, value in (("min", "0"), ("max", "10"), ("step", "1")):
            self.assertEqual(str(questions[1][key]), value)

        schema = engine.answer_schema(questions)
        item = schema["properties"]["answers"]["items"]
        self.assertIn("numeric_answer", item["required"])
        self.assertEqual(item["properties"]["numeric_answer"]["type"], "string")

    def test_plan_validation_accepts_one_choice_and_one_number(self):
        questions = engine.build_quiz_questions(mixed_ui())
        self.assertEqual(engine.validate_answer_plan(mixed_plan(), questions),
                         {"selected": ["c0"], "numeric": {"c2": "3"}})

    def test_plan_rejects_missing_or_invalid_numeric_values(self):
        questions = engine.build_quiz_questions(mixed_ui())
        changes = [
            lambda p: p["answers"][1].pop("numeric_answer"),
            lambda p: p["answers"][1].update(numeric_answer=""),
            lambda p: p["answers"][1].update(numeric_answer="not-a-number"),
            lambda p: p["answers"][1].update(numeric_answer="11"),
            lambda p: p["answers"][1].update(numeric_answer="3.5"),
            lambda p: p["answers"][1].update(selected_controls=["c2"]),
            lambda p: p["answers"][0].update(numeric_answer="3"),
        ]
        for change in changes:
            plan = mixed_plan()
            change(plan)
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                engine.validate_answer_plan(plan, questions)

    def test_long_decimal_has_a_controlled_result(self):
        value = "9" * 32
        questions = engine.build_quiz_questions(mixed_ui())
        plan = mixed_plan()
        plan["answers"][1]["numeric_answer"] = value
        with self.assertRaises(ValueError):
            engine.validate_answer_plan(plan, questions)  # Above the visible maximum.

        # Decimal step arithmetic must not leak InvalidOperation for a valid
        # 32-digit value when the field permits it.
        questions[1].update(min=None, max=None, step="1")
        self.assertEqual(engine.validate_answer_plan(plan, questions)["numeric"],
                         {"c2": value})
        questions[1]["step"] = None
        self.assertEqual(engine.validate_answer_plan(plan, questions)["numeric"],
                         {"c2": value})

    def test_choice_only_plan_retains_old_shape(self):
        ui = mixed_ui()
        ui["controls"] = ui["controls"][:2]
        ui["page_text"] = "Question 1\nWhich letter comes first?\nA\nB"
        questions = engine.build_quiz_questions(ui)
        schema = engine.answer_schema(questions)
        self.assertNotIn("numeric_answer", schema["properties"]["answers"]["items"]["required"])
        old_plan = {"cannot_answer": False, "answers": [
            {"question_id": "q1", "selected_controls": ["c0"], "reason": "Fixture"}]}
        self.assertEqual(engine.validate_answer_plan(old_plan, questions), ["c0"])

    def test_unknown_text_field_still_stops_before_answering(self):
        ui = mixed_ui()
        ui["unsupported_inputs"] = [{"tag": "input", "type": "text", "label": "Essay"}]
        with self.assertRaises(engine.StopRun):
            engine.build_quiz_questions(ui)


class NumericDomTests(unittest.TestCase):
    def test_extracts_number_with_question_and_constraints(self):
        view = javascript({"ui": mixed_ui()})[0]
        result = view["result"]
        self.assertTrue(result["editable_form"])
        self.assertFalse(result["unsupported_inputs"])
        numbers = [c for c in result["controls"] if c["type"] == "number"]
        self.assertEqual(len(numbers), 1)
        self.assertEqual(numbers[0]["id"], "c2")
        self.assertEqual(numbers[0]["question"], NUMBER_STEM)
        self.assertEqual(numbers[0]["label"], "Enter answer here")
        for key, value in (("min", "0"), ("max", "10"), ("step", "1")):
            self.assertEqual(str(numbers[0][key]), value)
        self.assertEqual(view["clicks"], [])
        self.assertEqual(view["events"], [])

    def test_fingerprint_tracks_number_identity_and_bounds_but_not_its_answer(self):
        blank = javascript({"ui": mixed_ui()})[0]["result"]["answer_snapshot_id"]
        filled = mixed_ui()
        filled["controls"][2]["value"] = "3"
        self.assertEqual(javascript({"ui": filled})[0]["result"]["answer_snapshot_id"], blank)
        changed = mixed_ui()
        changed["controls"][2]["max"] = "9"
        self.assertNotEqual(javascript({"ui": changed})[0]["result"]["answer_snapshot_id"], blank)

    def test_sets_and_verifies_both_answer_kinds(self):
        snapshot = javascript({"ui": mixed_ui()})[0]["result"]["snapshot_id"]
        request = {"key": KEY, "action": "set_answers", "snapshot_id": snapshot,
                   "selected": ["c0"], "numeric": {"c2": "3"}}
        view = javascript({"ui": mixed_ui(), "request": request})[0]
        self.assertTrue(view["result"]["verified"])
        self.assertTrue(view["fields"][0]["checked"])
        self.assertEqual(view["fields"][2]["value"], "3")
        self.assertTrue(any(event["index"] == 2 and event["type"] in {"input", "change"}
                            for event in view["events"]))

    def test_stale_or_invalid_request_cannot_mutate_form(self):
        snapshot = javascript({"ui": mixed_ui()})[0]["result"]["snapshot_id"]
        requests = [
            {"key": KEY, "action": "set_answers", "snapshot_id": "stale",
             "selected": ["c0"], "numeric": {"c2": "3"}},
            {"key": KEY, "action": "set_answers", "snapshot_id": snapshot,
             "selected": ["c0"], "numeric": {"missing": "3"}},
            {"key": KEY, "action": "set_answers", "snapshot_id": snapshot,
             "selected": ["c0"], "numeric": {"c2": "invalid"}},
        ]
        for request in requests:
            with self.subTest(request=request):
                view = javascript({"ui": mixed_ui(), "request": request})[0]
                self.assertIn("error", view["result"])
                self.assertFalse(view["clicks"])
                self.assertFalse(view["events"])
                self.assertFalse(view["fields"][0]["checked"])
                self.assertEqual(view["fields"][2]["value"], "")

    def test_final_confirmation_checks_answers_behind_popup(self):
        filled = mixed_ui()
        filled["controls"][0]["selected"] = True
        filled["controls"][2]["value"] = "3"
        inspected = javascript({"ui": filled, "confirmation": True})[0]["result"]
        self.assertEqual(inspected["dialog_kind"], "submit_confirmation")
        request = {"key": KEY, "action": "confirm_submit", "snapshot_id": inspected["snapshot_id"],
                   "expected_selected": ["c0"], "expected_numeric": {"c2": "3"}}

        good = javascript({"ui": filled, "confirmation": True, "request": request})[0]
        self.assertEqual(good["result"].get("clicked"), "Submit")
        self.assertEqual(good["clicks"], ["Submit"])

        changed_number = copy.deepcopy(filled)
        changed_number["controls"][2]["value"] = "4"
        changed_choice = copy.deepcopy(filled)
        changed_choice["controls"][0]["selected"] = False
        changed_choice["controls"][1]["selected"] = True
        for changed in (changed_number, changed_choice):
            with self.subTest(changed=changed["controls"]):
                view = javascript({"ui": changed, "confirmation": True, "request": request})[0]
                self.assertIn("error", view["result"])
                self.assertFalse(view["clicks"])
                self.assertFalse(view["events"])


class NumericWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_helper_forwards_expected_answer_maps(self):
        filled = mixed_ui()
        filled["controls"][0]["selected"] = True
        filled["controls"][2]["value"] = "3"
        current = javascript({"ui": filled, "confirmation": True})[0]["result"]
        calls = []

        async def quiz_ui(action="inspect", **parameters):
            calls.append((action, parameters))
            return copy.deepcopy(current) if action == "inspect" else {"clicked": "Submit"}

        result = await engine.press_quiz_button_when_ready(
            quiz_ui, "Submit", lambda ui: self.assertEqual(ui["dialog_kind"], "submit_confirmation"),
            action="confirm_submit", expected_answers={"selected": ["c0"], "numeric": {"c2": "3"}})
        self.assertEqual(result["clicked"], "Submit")
        self.assertEqual(calls, [
            ("inspect", {}),
            ("confirm_submit", {"label": "Submit", "snapshot_id": current["snapshot_id"],
                                "expected_selected": ["c0"], "expected_numeric": {"c2": "3"}}),
        ])

    async def test_changed_numeric_answer_stops_before_submit(self):
        original = javascript({"ui": mixed_ui()})[0]["result"]
        selected = copy.deepcopy(original)
        selected["controls"][0]["selected"] = True
        selected["controls"][2]["value"] = "3"
        drifted = copy.deepcopy(selected)
        drifted["controls"][2]["value"] = "4"
        calls = []

        async def evaluate(expression):
            request = json.loads(expression[len(engine.QUIZ_UI_JS) + 1:-1])
            calls.append(request)
            if request["action"] == "inspect":
                return copy.deepcopy(original if len(calls) == 1 else selected)
            if request["action"] == "set_answers":
                self.assertEqual(request["selected"], ["c0"])
                self.assertEqual(request["numeric"], {"c2": "3"})
                return {**copy.deepcopy(selected), "verified": True}
            self.fail("No Submit or other mutation should be dispatched")

        async def submit_boundary(quiz_ui, label, validator, **_kwargs):
            self.assertEqual(label, "Submit")
            validator(drifted)
            self.fail("The changed number must fail validation")

        with tempfile.TemporaryDirectory() as directory:
            runner = engine.CourseRunner(SimpleNamespace(evaluate=evaluate),
                                         SimpleNamespace(max_steps=40), Path(directory))
            runner.prepare_quiz = AsyncMock(return_value=copy.deepcopy(original))
            runner.solve_quiz_questions = AsyncMock(
                return_value={"selected": ["c0"], "numeric": {"c2": "3"}})
            with patch.object(engine, "press_quiz_button_when_ready", side_effect=submit_boundary), \
                    redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(engine.StopRun, "changed before submission"):
                    await runner.quiz({"key": KEY})
        self.assertEqual([call["action"] for call in calls],
                         ["inspect", "inspect", "set_answers", "inspect"])


class NumericHistoryTests(unittest.TestCase):
    def test_numeric_answer_is_recorded_and_matched_by_question_text(self):
        questions = engine.build_quiz_questions(mixed_ui())
        history = quiz_memory.record_attempt(
            [], questions, ["c0"], graded_feedback(), numeric_answers={"c2": "3"})
        self.assertEqual(len(history), 1)
        stored = history[0]["questions"]
        number = next(q for q in stored if q["selection"] == "number")
        self.assertEqual(number["selected_answers"], ["3"])
        self.assertTrue(number["full_score"])
        self.assertNotIn("control_id", number)
        self.assertNotIn("c2", json.dumps(history))

        shifted = copy.deepcopy(questions)
        shifted[1]["id"] = "q99"
        shifted[1]["control_id"] = "changed-control"
        shifted[1]["text"] = "9. Question 9 How many letters are in ABC?"
        context = quiz_memory.context_for_questions(history, shifted)
        observed = next(q for q in context if q["selection"] == "number")
        self.assertEqual(observed["attempts"][0]["selected_answers"], ["3"])
        self.assertTrue(observed["attempts"][0]["full_score"])

    def test_import_reconstructs_mixed_plan_from_bounded_local_events(self):
        questions = engine.build_quiz_questions(mixed_ui())
        events = [
            {"event": "quiz_questions", "key": KEY, "questions": questions},
            {"event": "quiz_answer_plan", "key": KEY, "plan": mixed_plan()},
            {"event": "quiz_submitted_result", "key": KEY,
             "question_feedback": graded_feedback()},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "logs" / "offline-run"
            run.mkdir(parents=True)
            (run / "events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            target = {}
            self.assertEqual(quiz_history_import.import_previous_history(root, run, target), 1)
        number = next(q for q in target[KEY][0]["questions"] if q["selection"] == "number")
        self.assertEqual(number["selected_answers"], ["3"])
        self.assertTrue(number["full_score"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
