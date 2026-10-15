"""Two independent Coursera fill-in-the-blank listboxes in one quiz attempt."""

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import coursera_start as engine


MULTI_SELECT_DOM = r"""
const fs=require('node:fs'),vm=require('node:vm');
const payload=JSON.parse(fs.readFileSync(0,'utf8'));
const rect=()=>[{width:120,height:24}];
const box=(x,y)=>({x,y,left:x,top:y,right:x+120,bottom:y+24,width:120,height:24});
const style={display:'block',visibility:'visible',opacity:'1'};
const clicks=[];
const body={innerText:'Question 1 Select an image model. Question 2 Select a sequence model.',
  parentElement:null,getClientRects:rect,getAttribute:()=>null,contains:()=>true,
  matches:s=>s==='body,main,form',querySelectorAll:()=>[]};
let open=-1;
const values=['',''];
const labels=[['CNN','LSTM'],['RNN','Transformer']];
const controls=[];
const allOptions=[];
const boxes=[];
for(let index=0;index<2;index++){
  const question={parentElement:body,getClientRects:rect,getAttribute:()=>null,
    matches:()=>false};
  const blank={parentElement:question,getClientRects:rect,getAttribute:()=>null,
    matches:()=>false};
  const trigger={tagName:'DIV',parentElement:blank,id:'answer-'+index,labels:[],
    isConnected:true,disabled:false,getClientRects:rect,
    getBoundingClientRect:()=>box(100,100+index*40),contains:e=>e===trigger,
    get innerText(){return values[index]||'Select answer';},
    getAttribute:key=>key==='aria-label'?`Response area number ${index+1}. Select an answer.`:
      key==='aria-expanded'?(open===index?'true':'false'):null,
    matches:s=>s==='[role="button"]'||s==='button,[role="button"]',
    closest:s=>s==='span.rc-FillableBlank'?blank:null,
    scrollIntoView(){},
    compareDocumentPosition(other){return index<controls.indexOf(other)?4:2;},
    dispatchEvent(e){if(e.type==='mousedown'){open=index;clicks.push('open:'+index);}return true;}};
  Object.defineProperty(question,'innerText',{get(){return `Question ${index+1} Select a ${index?'sequence':'image'} model: `+trigger.innerText;}});
  controls.push(trigger);
  const options=labels[index].map((name,choice)=>({tagName:'LI',parentElement:null,
    innerText:name,disabled:false,isConnected:true,getClientRects:rect,
    getBoundingClientRect:()=>box(100,180+choice*25),contains:e=>e===options[choice],
    getAttribute:key=>key==='aria-disabled'?'false':null,
    scrollIntoView(){},click(){values[index]=name;open=-1;clicks.push('choose:'+index+':'+choice);}}));
  const listbox={tagName:'UL',parentElement:body,getClientRects:()=>open===index?rect():[],
    getAttribute:()=>null,querySelectorAll:s=>s==='[role="option"]'?options:[]};
  for(const option of options)option.parentElement=listbox;
  allOptions.push(options);boxes.push(listbox);
}
const submit={tagName:'BUTTON',parentElement:body,innerText:'Submit',disabled:false,
  getClientRects:rect,getAttribute:()=>null,matches:s=>s==='button',closest:()=>null,
  scrollIntoView(){},click(){clicks.push('submit');}};
const actionSelector='button,[role="button"],a[href],[role="link"],input[type="submit"],input[type="button"]';
const document={body,getElementById:()=>null,contains:()=>true,
  querySelector:s=>s==='main'?body:null,
  elementFromPoint:(x,y)=>y>=180&&open>=0?
    allOptions[open][Math.min(1,Math.floor((y-180)/25))]:y>=140?controls[1]:controls[0],
  querySelectorAll:s=>{
    if(s==='span.rc-FillableBlank [role="button"][aria-haspopup="listbox"]')return controls;
    if(s==='[role="listbox"]')return open<0?[]:[boxes[open]];
    if(s===actionSelector)return [...controls,submit];
    return [];
  }};
const location=new URL('https://www.coursera.org/learn/philosophy/assignment-submission/fixture/attempt');
const context={document,location,innerWidth:1920,innerHeight:991,
  Node:{DOCUMENT_POSITION_FOLLOWING:4},getComputedStyle:()=>style,
  setTimeout:callback=>callback(),URL,
  MouseEvent:class MouseEvent{constructor(type){this.type=type;}}};
async function main(){
  const run=vm.runInNewContext(payload.code,context),base={key:'assignment:fixture'};
  const initial=await run({...base,action:'inspect'});
  const ambiguous=await run({...base,action:'inspect_select_options',snapshot_id:initial.snapshot_id});
  const firstOpen=await run({...base,action:'activate_select_trigger',
    snapshot_id:initial.snapshot_id,control_id:'c0'});
  const first=await run({...base,action:'inspect_select_options',
    snapshot_id:initial.snapshot_id,control_id:'c0'});
  const secondOpen=await run({...base,action:'activate_select_trigger',
    snapshot_id:initial.snapshot_id,control_id:'c1'});
  const second=await run({...base,action:'inspect_select_options',
    snapshot_id:initial.snapshot_id,control_id:'c1'});
  const wrongTarget=await run({...base,action:'inspect_select_options',
    snapshot_id:initial.snapshot_id,control_id:'c0'});
  const selected={c0:{id:'c0:o0',text:'CNN',options:labels[0]},
    c1:{id:'c1:o1',text:'Transformer',options:labels[1]}};
  const set=await run({...base,action:'set_answers',snapshot_id:initial.snapshot_id,
    selected:[],numeric:{},select:selected});
  await run({...base,action:'activate_select_trigger',snapshot_id:initial.snapshot_id,control_id:'c0'});
  const chooseFirst=await run({...base,action:'activate_select_option',
    snapshot_id:initial.snapshot_id,control_id:'c0',option_id:'c0:o0',
    option_text:'CNN',expected_options:labels[0]});
  await run({...base,action:'activate_select_trigger',snapshot_id:initial.snapshot_id,control_id:'c1'});
  const chooseSecond=await run({...base,action:'activate_select_option',
    snapshot_id:initial.snapshot_id,control_id:'c1',option_id:'c1:o1',
    option_text:'Transformer',expected_options:labels[1]});
  const after=await run({...base,action:'inspect'});
  const wrong=await run({...base,action:'button',label:'Submit',snapshot_id:after.snapshot_id,
    expected_selected:[],expected_numeric:{},expected_select:{c0:'CNN',c1:'RNN'}});
  const good=await run({...base,action:'button',label:'Submit',snapshot_id:after.snapshot_id,
    expected_selected:[],expected_numeric:{},expected_select:{c0:'CNN',c1:'Transformer'}});
  process.stdout.write(JSON.stringify({initial,ambiguous,firstOpen,first,secondOpen,second,
    wrongTarget,set,chooseFirst,chooseSecond,after,wrong,good,clicks}));
}
main().catch(error=>{console.error(error);process.exit(1)});
"""


class MultiSelectQuizTests(unittest.TestCase):
    def test_each_dropdown_is_read_and_verified_independently(self):
        result = subprocess.run(["node", "-e", MULTI_SELECT_DOM],
                                input=json.dumps({"code": engine.QUIZ_UI_JS}),
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        view = json.loads(result.stdout)
        self.assertEqual([c["type"] for c in view["initial"]["controls"]], ["select", "select"])
        self.assertIn("error", view["ambiguous"])
        self.assertEqual(view["firstOpen"]["dispatched"], "mousedown")
        self.assertEqual(view["secondOpen"]["dispatched"], "mousedown")
        self.assertEqual([x["text"] for x in view["first"]["select_options"]["c0"]],
                         ["CNN", "LSTM"])
        self.assertEqual([x["text"] for x in view["second"]["select_options"]["c1"]],
                         ["RNN", "Transformer"])
        self.assertIn("error", view["wrongTarget"])
        self.assertEqual(view["wrongTarget"]["error_code"], "select_closed")
        self.assertTrue(view["set"]["verified"])
        self.assertEqual(view["chooseFirst"]["dispatched"], "click")
        self.assertEqual(view["chooseSecond"]["dispatched"], "click")
        self.assertEqual([c["value"] for c in view["after"]["controls"]],
                         ["CNN", "Transformer"])
        self.assertEqual(view["after"]["answer_snapshot_id"],
                         view["initial"]["answer_snapshot_id"])
        self.assertIn("error", view["wrong"])
        self.assertEqual(view["good"]["clicked"], "Submit")
        self.assertEqual(view["clicks"].count("submit"), 1)

        ui = {**view["initial"], "select_options": {
            **view["first"]["select_options"], **view["second"]["select_options"]}}
        questions = engine.build_quiz_questions(ui)
        self.assertEqual(len(questions), 2)
        plan = {"cannot_answer": False, "answers": [
            {"question_id": "q1", "selected_controls": ["c0:o0"], "reason": "Image"},
            {"question_id": "q2", "selected_controls": ["c1:o1"], "reason": "Sequence"}]}
        self.assertEqual(engine.validate_answer_plan(plan, questions)["select"], {
            "c0": {"id": "c0:o0", "text": "CNN", "options": ["CNN", "LSTM"]},
            "c1": {"id": "c1:o1", "text": "Transformer", "options": ["RNN", "Transformer"]}})


class MultiSelectRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_runner_collects_both_inventories_before_model_call(self):
        url = "https://www.coursera.org/learn/philosophy/assignment-submission/fixture/attempt"
        options = {"c0": [{"id": "c0:o0", "text": "CNN"},
                          {"id": "c0:o1", "text": "LSTM"}],
                   "c1": [{"id": "c1:o0", "text": "RNN"},
                          {"id": "c1:o1", "text": "Transformer"}]}
        state = {"open": None, "seen": []}

        class ProbeTab:
            async def evaluate(self, script):
                request = json.loads(script[len(engine.QUIZ_UI_JS) + 1:-1])
                action = request["action"]
                control = request.get("control_id")
                state["seen"].append((action, control))
                rows = [{"id": key, "type": "select", "kind": "answer",
                         "disabled": False, "in_scope": True,
                         "expanded": state["open"] == key, "name": key,
                         "question": f"Question {index} Choose a model [blank]",
                         "label": f"Response area number {index}."}
                        for index, key in enumerate(options, 1)]
                result = {"url": url, "snapshot_id": "form-1",
                          "answer_snapshot_id": "answers-1", "controls": rows,
                          "select_options": {}, "editable_form": True}
                if action == "inspect_select_options":
                    if state["open"] != control:
                        return {**result, "error": "closed", "error_code": "select_closed"}
                    return {**result, "select_options": {control: options[control]}}
                if action == "activate_select_trigger":
                    state["open"] = control
                    return {"dispatched": "mousedown", "control_id": control, "url": url}
                if action == "inspect":
                    return result
                raise AssertionError(action)

        class ReachedModel(Exception):
            pass

        class ProbeRunner(engine.CourseRunner):
            def event(self, *args, **kwargs):
                pass

            async def prepare_quiz(self, item, quiz_ui):
                return await quiz_ui()

            async def solve_quiz_questions(self, item, current, feedback):
                self.collected = current["select_options"]
                raise ReachedModel

        runner = ProbeRunner(ProbeTab(), SimpleNamespace(max_steps=100, quiz_attempts=1), Path("."))
        with patch.object(engine.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaises(ReachedModel):
                await runner.quiz({"key": "assignment:fixture", "url": url})
        self.assertEqual(runner.collected, options)
        self.assertEqual([control for action, control in state["seen"]
                          if action == "activate_select_trigger"], ["c0", "c1"])


if __name__ == "__main__":
    unittest.main()
