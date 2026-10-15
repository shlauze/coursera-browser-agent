"""Offline contract for numbered quiz questions using an HTML select element."""

import json
import subprocess
import unittest

import coursera_start as engine


STEM = "1. Question 1 Which model handles sequences?"
URL = "https://www.coursera.org/learn/philosophy/assignment-submission/fixture/example/attempt"


NATIVE_SELECT_DOM = r"""
const fs=require('node:fs'),vm=require('node:vm');
const payload=JSON.parse(fs.readFileSync(0,'utf8'));
const style={display:'block',visibility:'visible',opacity:'1'};
const rect=()=>[{width:120,height:24}];
const events=[],clicks=[];
const body={innerText:payload.stem||'Question 1 Which model handles sequences?',parentElement:null,
  matches:s=>s==='body,main,form',getClientRects:rect,getAttribute:()=>null,
  contains:()=>true,querySelectorAll:()=>[]};
const question={innerText:payload.stem||'1. Question 1 Which model handles sequences?',parentElement:body,
  matches:()=>false,getClientRects:rect,getAttribute:()=>null};
class NativeSelect {
  constructor(){this.tagName='SELECT';this.parentElement=question;this.name='model';
    this.labels=[];this.disabled=false;this.readOnly=false;this.isConnected=true;
    this.options=[{value:'',textContent:payload.enabled_empty?'None':'Choose one',
        disabled:!payload.enabled_empty},
      {value:'cnn',textContent:'CNN',disabled:false},
      {value:'lstm',textContent:payload.duplicate?'CNN':'LSTM',disabled:false}];
    if(payload.disabled_optgroup)this.options.splice(2,0,
      {value:'blocked',textContent:'Unavailable model',disabled:false,
        parentElement:{tagName:'OPTGROUP',disabled:true}});
    this._value='';}
  get value(){return this._value;}
  set value(x){this._value=String(x);}
  getClientRects(){return rect();}
  getAttribute(key){return key==='aria-label'?'Select a model':null;}
  matches(s){return s==='select';}
  closest(){return null;}
  scrollIntoView(){}
  compareDocumentPosition(){return 0;}
  dispatchEvent(e){events.push(e.type);return true;}
}
const select=new NativeSelect();
const submit={tagName:'BUTTON',innerText:'Submit',parentElement:body,disabled:false,
  getClientRects:rect,getAttribute:()=>null,matches:s=>s==='button',closest:()=>null,
  scrollIntoView(){},click(){clicks.push('submit');}};
const actionSelector='button,[role="button"],a[href],[role="link"],input[type="submit"],input[type="button"]';
const document={body,getElementById:()=>null,contains:()=>true,querySelector:s=>s==='main'?body:null,
  querySelectorAll:s=>{
    if(s==='select')return [select];
    if(s===actionSelector)return [submit];
    if(s.includes('textarea,select,input:not'))return [select];
    return [];
  }};
const location=new URL(payload.url);
const context={document,location,Node:{DOCUMENT_POSITION_FOLLOWING:4},
  HTMLSelectElement:NativeSelect,Event:class Event{constructor(type){this.type=type;}},
  getComputedStyle:()=>style,setTimeout:callback=>callback(),URL};
async function main(){
  const run=vm.runInNewContext(payload.code,context);
  const base={key:'assignment:fixture'};
  const initial=await run({...base,action:'inspect'});
  if(payload.only_inspect){process.stdout.write(JSON.stringify(initial));return;}
  const bad=await run({...base,action:'set_answers',snapshot_id:initial.snapshot_id,
    selected:[],numeric:{},select:{c0:{id:'c0:o1',text:'LSTM',options:['CNN','Changed']}}});
  const set=await run({...base,action:'set_answers',snapshot_id:initial.snapshot_id,
    selected:[],numeric:{},select:{c0:{id:'c0:o1',text:'LSTM',options:['CNN','LSTM']}}});
  const after=await run({...base,action:'inspect'});
  const wrong=await run({...base,action:'button',label:'Submit',snapshot_id:after.snapshot_id,
    expected_selected:[],expected_numeric:{},expected_select:{c0:'CNN'}});
  const good=await run({...base,action:'button',label:'Submit',snapshot_id:after.snapshot_id,
    expected_selected:[],expected_numeric:{},expected_select:{c0:'LSTM'}});
  process.stdout.write(JSON.stringify({initial,bad,set,after,wrong,good,events,clicks}));
}
main().catch(error=>{console.error(error);process.exit(1)});
"""


class NativeSelectQuizTests(unittest.TestCase):
    def test_extract_plan_and_verify_dom_value_before_submit(self):
        result = subprocess.run(
            ["node", "-e", NATIVE_SELECT_DOM],
            input=json.dumps({"code": engine.QUIZ_UI_JS, "url": URL}),
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        view = json.loads(result.stdout)
        first = view["initial"]
        self.assertTrue(first["editable_form"])
        self.assertEqual(first["controls"][0]["type"], "native_select")
        self.assertEqual(first["controls"][0]["question"], STEM)
        self.assertEqual(first["select_options"]["c0"], [
            {"id": "c0:o0", "text": "CNN"},
            {"id": "c0:o1", "text": "LSTM"}])
        self.assertEqual(first["unsupported_inputs"], [])
        self.assertIn("error", view["bad"])
        self.assertTrue(view["set"]["verified"])
        self.assertEqual(view["after"]["controls"][0]["value"], "LSTM")
        self.assertEqual(view["after"]["answer_snapshot_id"], first["answer_snapshot_id"])
        self.assertIn("error", view["wrong"])
        self.assertEqual(view["good"]["clicked"], "Submit")
        self.assertEqual(view["events"], ["input", "change"])
        self.assertEqual(view["clicks"], ["submit"])

        questions = engine.build_quiz_questions(first)
        self.assertEqual(questions[0]["control_type"], "native_select")
        plan = {"cannot_answer": False, "answers": [{
            "question_id": "q1", "selected_controls": ["c0:o1"], "reason": "Sequence model"}]}
        self.assertEqual(engine.validate_answer_plan(plan, questions), {
            "selected": [], "numeric": {}, "select": {"c0": {
                "id": "c0:o1", "text": "LSTM", "options": ["CNN", "LSTM"]}}})

    def test_missing_or_duplicate_options_are_not_planned(self):
        base = {"page_text": STEM, "controls": [{
            "id": "c0", "type": "native_select", "name": "model",
            "label": "Select a model", "question": STEM,
            "kind": "answer", "disabled": False, "in_scope": True}]}
        for options in (None,
                        [{"id": "c0:o0", "text": "Only one"}],
                        [{"id": "c0:o0", "text": "Same"}, {"id": "c0:o1", "text": "same"}]):
            ui = {**base, "select_options": {"c0": options}}
            with self.subTest(options=options), self.assertRaises(engine.StopRun):
                engine.build_quiz_questions(ui)

    def test_ambiguous_dom_is_left_unsupported(self):
        for variant in ({"duplicate": True},
                        {"enabled_empty": True},
                        {"stem": "Question 1 Choose a model. Question 2 Choose another model."}):
            result = subprocess.run(
                ["node", "-e", NATIVE_SELECT_DOM],
                input=json.dumps({"code": engine.QUIZ_UI_JS, "url": URL,
                                  "only_inspect": True, **variant}),
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            view = json.loads(result.stdout)
            self.assertEqual(view["controls"], [])
            self.assertFalse(view["editable_form"])
            self.assertEqual(view["unsupported_inputs"][0]["tag"], "select")
            with self.assertRaises(engine.StopRun):
                engine.build_quiz_questions(view)

    def test_disabled_optgroup_option_is_not_an_answer(self):
        result = subprocess.run(
            ["node", "-e", NATIVE_SELECT_DOM],
            input=json.dumps({"code": engine.QUIZ_UI_JS, "url": URL,
                              "disabled_optgroup": True}),
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        view = json.loads(result.stdout)
        self.assertEqual(view["initial"]["select_options"]["c0"], [
            {"id": "c0:o0", "text": "CNN"},
            {"id": "c0:o1", "text": "LSTM"}])
        self.assertTrue(view["set"]["verified"])
        self.assertEqual(view["after"]["controls"][0]["value"], "LSTM")
        self.assertEqual(view["good"]["clicked"], "Submit")
        self.assertEqual(view["clicks"], ["submit"])


if __name__ == "__main__":
    unittest.main()
