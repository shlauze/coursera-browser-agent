"""Offline regression for numbered Coursera short-text quiz answers.

The DOM fixture runs the real quiz script in Node. It never opens Coursera or
submits an assessment.
"""

import copy
import json
import subprocess
import unittest

import coursera_start as engine


URL = "https://www.coursera.org/learn/philosophy/assignment-submission/fixture/example/attempt"
KEY = "assignment:fixture"
Q1 = "1. Question 1 Enter a lowercase security term."
Q2 = "2. Question 2 Pick a letter."
Q3 = "3. Question 3 Enter another lowercase security term."


def mixed_ui():
    return {
        "url": URL,
        "editable_form": True,
        "page_text": "\n".join((Q1, "Enter answer here", Q2, "A", "B", Q3, "Enter answer here")),
        "controls": [
            {"id": "c0", "type": "text", "name": "", "value": "",
             "label": "Enter answer here", "question": Q1, "kind": "answer",
             "selected": False, "disabled": False, "in_scope": True},
            {"id": "c1", "type": "radio", "name": "letters", "value": "a",
             "label": "A", "question": Q2, "kind": "answer",
             "selected": False, "disabled": False, "in_scope": True},
            {"id": "c2", "type": "radio", "name": "letters", "value": "b",
             "label": "B", "question": Q2, "kind": "answer",
             "selected": False, "disabled": False, "in_scope": True},
            {"id": "c3", "type": "text", "name": "", "value": "",
             "label": "Enter answer here", "question": Q3, "kind": "answer",
             "selected": False, "disabled": False, "in_scope": True},
        ],
    }


def mixed_plan():
    return {"cannot_answer": False, "answers": [
        {"question_id": "q1", "selected_controls": [], "text_answer": "attack",
         "reason": "Fixture"},
        {"question_id": "q2", "selected_controls": ["c1"], "text_answer": "",
         "reason": "Fixture"},
        {"question_id": "q3", "selected_controls": [], "text_answer": "audit",
         "reason": "Fixture"},
    ]}


NODE_DOM = r"""
const fs=require('node:fs'),vm=require('node:vm');
const payload=JSON.parse(fs.readFileSync(0,'utf8'));
const style={display:'block',visibility:'visible',opacity:'1'};
const rect=()=>[{width:120,height:24}];
const events=[],clicks=[];
const body={innerText:payload.page_text||'',parentElement:null,
  matches:s=>s==='body,main,form',getClientRects:rect,getAttribute:()=>null,
  contains:()=>true,querySelectorAll:()=>[]};
class Input {
  constructor(c,index,group){
    this.tagName=c.type==='textarea'?'TEXTAREA':'INPUT';
    this.type=c.type;this.name=c.name||'';this.index=index;this.parentElement=group;
    this._value=c.value||'';this.checked=!!c.selected;this.disabled=!!c.disabled;
    this.readOnly=false;this.isConnected=true;
    this.id=c.id_attribute===undefined?(c.type==='text'?`text-input-autoGradableResponseId~questionBlockQuestion!~fixture@1!!~${index}`:''):c.id_attribute;
    this.placeholder=c.placeholder===undefined?(c.type==='text'?'Enter answer here':''):c.placeholder;
    this.maxLength=c.max_length||-1;this.minLength=c.min_length||-1;
    this.labels=[{innerText:c.label||'',getClientRects:rect,scrollIntoView(){}}];
  }
  get value(){return this._value;}
  set value(value){this._value=String(value);}
  getClientRects(){return rect();}
  getAttribute(name){
    if(name==='placeholder')return this.placeholder||null;
    if(name==='aria-label')return this.type==='text'?'Enter answer here':null;
    if(name==='maxlength')return this.maxLength>=0?String(this.maxLength):null;
    return null;
  }
  matches(selector){
    if(selector==='input')return this.tagName==='INPUT';
    if(selector==='textarea')return this.tagName==='TEXTAREA';
    if(selector==='body,main,form')return false;
    return selector===`input[type="${this.type}"]` ||
      selector===`input[type="${this.type}"][placeholder="Enter answer here"]`;
  }
  closest(){return null;}
  cloneNode(){
    const probe=new Input({type:this.type,name:this.name,value:this.value,
      label:'Enter answer here'},this.index,this.parentElement);
    probe.maxLength=this.maxLength;probe.minLength=this.minLength;
    return probe;
  }
  checkValidity(){return true;}
  querySelector(){return null;}
  scrollIntoView(){}
  compareDocumentPosition(other){return this.index<other.index?4:2;}
  click(){this.checked=true;clicks.push(this.index);}
  dispatchEvent(event){events.push({index:this.index,type:event.type});return true;}
}
const groups=new Map();
const inputs=(payload.controls||[]).map((c,i)=>{
  const key=c.name||c.question||`unrelated${i}`;
  let group=groups.get(key);
  if(!group){group={innerText:c.question||'',parentElement:body,matches:()=>false,
    getClientRects:rect,getAttribute:()=>null,contains:e=>e.parentElement===group,
    querySelectorAll:()=>[]};groups.set(key,group);}
  return new Input(c,i,group);
});
const actionSelector='button,[role="button"],a[href],[role="link"],input[type="submit"],input[type="button"]';
const submit={tagName:'BUTTON',innerText:'Submit',parentElement:body,disabled:false,
  getClientRects:rect,getAttribute:()=>null,matches:s=>s==='button',closest:()=>null,
  scrollIntoView(){},click(){clicks.push('Submit');}};
const query=selector=>{
  if(selector===actionSelector)return [submit];
  if(selector.includes('textarea,select,input:not'))return inputs.filter(c=>
    c.type==='textarea' || (c.type==='text' && !selector.includes(':not([type="text"])')));
  if(selector==='input,textarea,select,[role="checkbox"],[role="radio"]' ||
     selector==='input,textarea,select' || selector==='input,textarea')return inputs;
  if(selector==='input')return inputs.filter(c=>c.tagName==='INPUT');
  if(selector==='textarea')return inputs.filter(c=>c.tagName==='TEXTAREA');
  const types=[...selector.matchAll(/input\[type=["']?([a-z]+)["']?\]/g)].map(m=>m[1]);
  if(types.length)return inputs.filter(c=>types.includes(c.type) && c.tagName==='INPUT');
  return [];
};
const document={body,getElementById:()=>null,contains:()=>true,
  querySelector:s=>s==='main'?body:null,querySelectorAll:query};
const location=new URL(payload.url);
const context={document,location,Node:{DOCUMENT_POSITION_FOLLOWING:4},
  HTMLInputElement:Input,Event:class Event{constructor(type){this.type=type;}},
  InputEvent:class InputEvent{constructor(type){this.type=type;}},
  getComputedStyle:()=>style,setTimeout:callback=>callback(),URL};
async function main(){
  const run=vm.runInNewContext(payload.code,context);
  const base={key:'assignment:fixture'};
  const initial=await run({...base,action:'inspect'});
  if(payload.only_inspect){process.stdout.write(JSON.stringify({initial,events,clicks}));return;}
  const selected=['c1'],text={c0:'attack',c3:'audit'};
  const stale=await run({...base,action:'set_answers',snapshot_id:'stale',selected,text,numeric:{}});
  const missing=await run({...base,action:'set_answers',snapshot_id:initial.snapshot_id,
    selected,text:{c0:'attack'},numeric:{}});
  const extra=await run({...base,action:'set_answers',snapshot_id:initial.snapshot_id,
    selected,text:{...text,c999:'extra'},numeric:{}});
  const before={fields:inputs.map(x=>({type:x.type,value:x.value,checked:x.checked})),events:events.slice(),clicks:clicks.slice()};
  const set=await run({...base,action:'set_answers',snapshot_id:initial.snapshot_id,selected,text,numeric:{}});
  const after=await run({...base,action:'inspect'});
  const wrong=await run({...base,action:'button',label:'Submit',snapshot_id:after.snapshot_id,
    expected_selected:selected,expected_numeric:{},expected_text:{c0:'attack',c3:'wrong'}});
  const good=await run({...base,action:'button',label:'Submit',snapshot_id:after.snapshot_id,
    expected_selected:selected,expected_numeric:{},expected_text:text});
  process.stdout.write(JSON.stringify({initial,stale,missing,extra,before,set,after,wrong,good,
    fields:inputs.map(x=>({type:x.type,value:x.value,checked:x.checked})),events,clicks}));
}
main().catch(error=>{console.error(error);process.exit(1)});
"""


def javascript(ui=None, *, only_inspect=False):
    ui = mixed_ui() if ui is None else ui
    result = subprocess.run(
        ["node", "-e", NODE_DOM],
        input=json.dumps({"code": engine.QUIZ_UI_JS, **ui, "only_inspect": only_inspect}),
        capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise AssertionError(result.stderr[-2500:])
    return json.loads(result.stdout)


class ShortTextQuizTests(unittest.TestCase):
    def test_extracts_two_short_text_questions_and_mixed_plan(self):
        questions = engine.build_quiz_questions(mixed_ui())
        self.assertEqual([q["selection"] for q in questions],
                         ["short_text", "one", "short_text"])
        self.assertEqual([questions[0]["control_id"], questions[2]["control_id"]],
                         ["c0", "c3"])
        self.assertEqual(questions[0]["options"], [])
        answer = engine.answer_schema(questions)["properties"]["answers"]["items"]
        self.assertIn("text_answer", answer["required"])
        self.assertEqual(engine.validate_answer_plan(mixed_plan(), questions),
                         {"selected": ["c1"], "numeric": {},
                          "text": {"c0": "attack", "c3": "audit"}})

    def test_missing_or_extra_text_plan_fails_before_mutation(self):
        questions = engine.build_quiz_questions(mixed_ui())
        variants = []
        no_field = mixed_plan()
        no_field["answers"][0].pop("text_answer")
        variants.append(no_field)
        empty = mixed_plan()
        empty["answers"][0]["text_answer"] = ""
        variants.append(empty)
        choice_text = mixed_plan()
        choice_text["answers"][1]["text_answer"] = "extra"
        variants.append(choice_text)
        bad_choice = mixed_plan()
        bad_choice["answers"][0]["selected_controls"] = ["c0"]
        variants.append(bad_choice)
        surrogate = mixed_plan()
        surrogate["answers"][0]["text_answer"] = "x\ud800"
        variants.append(surrogate)
        for plan in variants:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                engine.validate_answer_plan(plan, questions)

    def test_mixed_text_and_numeric_questions_require_both_answer_fields(self):
        ui = mixed_ui()
        ui["controls"][3]["type"] = "number"
        questions = engine.build_quiz_questions(ui)
        schema = engine.answer_schema(questions)["properties"]["answers"]["items"]
        self.assertIn("text_answer", schema["required"])
        self.assertIn("numeric_answer", schema["required"])
        plan = mixed_plan()
        for answer in plan["answers"]:
            answer["numeric_answer"] = ""
        plan["answers"][2].update(text_answer="", numeric_answer="7")
        self.assertEqual(engine.validate_answer_plan(plan, questions),
                         {"selected": ["c1"], "numeric": {"c3": "7"},
                          "text": {"c0": "attack"}})

    def test_duplicate_text_fields_under_one_question_are_ambiguous(self):
        ui = mixed_ui()
        ui["controls"][3]["question"] = Q1
        with self.assertRaises(engine.StopRun):
            engine.build_quiz_questions(ui)

    def test_dom_reads_fills_and_verifies_before_submit(self):
        result = javascript()
        first = result["initial"]
        self.assertTrue(first["editable_form"])
        self.assertEqual(first["unsupported_inputs"], [])
        self.assertEqual([c["type"] for c in first["controls"]],
                         ["text", "radio", "radio", "text"])
        self.assertEqual([first["controls"][0]["question"],
                          first["controls"][3]["question"]], [Q1, Q3])
        for name in ("stale", "missing", "extra"):
            self.assertIn("error", result[name])
        self.assertEqual(result["before"]["events"], [])
        self.assertEqual([x["value"] for x in result["before"]["fields"] if x["type"] == "text"],
                         ["", ""])
        self.assertTrue(result["set"]["verified"])
        self.assertEqual([x["value"] for x in result["fields"] if x["type"] == "text"],
                         ["attack", "audit"])
        self.assertTrue(result["fields"][1]["checked"])
        self.assertEqual(result["after"]["answer_snapshot_id"], first["answer_snapshot_id"])
        self.assertIn("error", result["wrong"])
        self.assertEqual(result["good"]["clicked"], "Submit")
        self.assertEqual(result["clicks"], [1, "Submit"])
        for field in (0, 3):
            self.assertTrue(any(e["index"] == field and e["type"] == "input"
                                for e in result["events"]))

    def test_text_outside_one_numbered_question_stays_unsupported(self):
        ui = mixed_ui()
        ui["controls"][0]["question"] = "Search this page"
        view = javascript(ui, only_inspect=True)["initial"]
        self.assertIn({"tag": "input", "type": "text", "label": "Enter answer here"},
                      view["unsupported_inputs"])
        with self.assertRaises(engine.StopRun):
            engine.build_quiz_questions(view)

    def test_unrelated_text_widget_inside_a_question_stays_unsupported(self):
        for field in ({"id_attribute": "search-filter", "placeholder": "Enter answer here"},
                      {"id_attribute": "text-input-autoGradableResponseId~questionBlockQuestion!~fixture",
                       "placeholder": "Search options"}):
            with self.subTest(field=field):
                ui = mixed_ui()
                ui["controls"][0].update(field)
                view = javascript(ui, only_inspect=True)["initial"]
                self.assertEqual([c["type"] for c in view["controls"]],
                                 ["radio", "radio", "text"])
                self.assertTrue(any(value["type"] == "text" for value in view["unsupported_inputs"]))
                with self.assertRaises(engine.StopRun):
                    engine.build_quiz_questions(view)

    def test_textarea_reflection_stays_unsupported(self):
        ui = mixed_ui()
        ui["controls"][0]["type"] = "textarea"
        view = javascript(ui, only_inspect=True)["initial"]
        self.assertTrue(view["text_response_required"])
        self.assertTrue(any(field["tag"] == "textarea" for field in view["unsupported_inputs"]))
        with self.assertRaises(engine.StopRun):
            engine.build_quiz_questions(view)

    def test_changed_attempt_format_requires_a_new_plan(self):
        first = mixed_ui()
        first["controls"][0]["type"] = "number"
        first["controls"][3]["type"] = "number"
        first_questions = engine.build_quiz_questions(first)
        self.assertEqual([q["selection"] for q in first_questions],
                         ["number", "one", "number"])
        next_questions = engine.build_quiz_questions(mixed_ui())
        old_plan = {"cannot_answer": False, "answers": [
            {"question_id": "q1", "selected_controls": [], "numeric_answer": "1", "reason": "Fixture"},
            {"question_id": "q2", "selected_controls": ["c1"], "numeric_answer": "", "reason": "Fixture"},
            {"question_id": "q3", "selected_controls": [], "numeric_answer": "2", "reason": "Fixture"},
        ]}
        self.assertEqual(engine.validate_answer_plan(old_plan, first_questions),
                         {"selected": ["c1"], "numeric": {"c0": "1", "c3": "2"}})
        with self.assertRaises(ValueError):
            engine.validate_answer_plan(old_plan, next_questions)
        self.assertNotEqual(javascript(first, only_inspect=True)["initial"]["answer_snapshot_id"],
                            javascript(only_inspect=True)["initial"]["answer_snapshot_id"])


if __name__ == "__main__":
    unittest.main()
