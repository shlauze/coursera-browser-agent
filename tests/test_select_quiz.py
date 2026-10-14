"""Guard the CDS fill-in-the-blank listbox seen in the XAI quiz."""

import json
import subprocess
import unittest

import coursera_start as engine
import quiz_memory


STEM = "2. Question 2 Fill in the blank: A [blank] explanation changes a prediction."


def mixed_ui():
    return {
        "page_text": "Question 1\nChoose a letter\nA\nB\nQuestion 2\nFill in the blank",
        "controls": [
            {"id":"c0", "type":"radio", "name":"letters", "question":"1. Question 1 Choose a letter",
             "label":"A", "kind":"answer", "disabled":False},
            {"id":"c1", "type":"radio", "name":"letters", "question":"1. Question 1 Choose a letter",
             "label":"B", "kind":"answer", "disabled":False},
            {"id":"c2", "type":"select", "name":"response-1", "question":STEM,
             "label":"Response area number 1.", "kind":"answer", "disabled":False},
        ],
        "select_options":{"c2":[
            {"id":"c2:o0", "text":"Prototype-based"},
            {"id":"c2:o1", "text":"Counterfactual"},
            {"id":"c2:o2", "text":"Influential Instances"},
        ]},
    }


def plan():
    return {"cannot_answer":False,"answers":[
        {"question_id":"q1", "selected_controls":["c0"], "reason":"Fixture"},
        {"question_id":"q2", "selected_controls":["c2:o1"], "reason":"Fixture"},
    ]}


SELECT_DOM = r"""
const fs=require('node:fs'), vm=require('node:vm');
const payload=JSON.parse(fs.readFileSync(0,'utf8'));
const clicks=[];
const rect=()=>[{width:120,height:24}];
const box=(x,y)=>({x,y,left:x,top:y,width:120,height:24,right:x+120,bottom:y+24});
const style={display:'block',visibility:'visible',opacity:'1'};
const attrs=(values,key)=>values[key]??null;
const body={tagName:'BODY',parentElement:null,innerText:'',getClientRects:rect,
  getAttribute:()=>null,matches:s=>s==='body,main,form',querySelectorAll:()=>[]};
const p={tagName:'P',parentElement:body,getClientRects:rect,getAttribute:()=>null,
  matches:()=>false};
const blank={tagName:'SPAN',parentElement:p,getClientRects:rect,getAttribute:()=>null,
  matches:()=>false};
let selected='',expanded=false;
const trigger={tagName:'DIV',parentElement:blank,labels:[],id:'fixture-value',isConnected:true,
  disabled:false,hidden:false,getClientRects:rect,
  getBoundingClientRect:()=>box(100,100),contains:e=>e===trigger,
  get innerText(){return selected||'Select answer';},
  getAttribute:key=>attrs({'aria-label':'Response area number 1. Select an answer.',
    'aria-expanded':expanded?'true':'false','aria-haspopup':'listbox'},key),
  matches:s=>s==='[role="button"]' || s==='button,[role="button"]',
  closest:s=>s==='span.rc-FillableBlank'?blank:null,
  scrollIntoView(){},click(){clicks.push('unhandled-click');},
  dispatchEvent(event){if(event.type==='mousedown'){expanded=true;clicks.push('open');}return true;}};
Object.defineProperty(p,'innerText',{get(){return '1. Question 1 Fill in the blank: A '+
  trigger.innerText+' explanation changes the prediction.';}});
const optionLabels=['Prototype-based','Counterfactual','Influential Instances'];
const options=optionLabels.map((name,index)=>({tagName:'LI',parentElement:null,
  innerText:name,disabled:false,getClientRects:rect,
  isConnected:true,getBoundingClientRect:()=>box(100,150+index*25),contains:e=>e===options[index],
  getAttribute:key=>key==='aria-disabled'?'false':null,
  scrollIntoView(){},click(){selected=name;expanded=false;clicks.push('option:'+index);}}));
const listbox={tagName:'UL',parentElement:body,innerText:optionLabels.join(' '),
  getClientRects:()=>expanded?rect():[],getAttribute:()=>null,
  querySelectorAll:s=>s==='[role="option"]'?options:[]};
for(const option of options) option.parentElement=listbox;
const submit={tagName:'BUTTON',parentElement:body,innerText:'Submit',disabled:false,
  getClientRects:rect,getAttribute:()=>null,matches:s=>s==='button',
  closest:()=>null,scrollIntoView(){},click(){clicks.push('submit');}};
const actionSelector='button,[role="button"],a[href],[role="link"],input[type="submit"],input[type="button"]';
const document={body,getElementById:()=>null,contains:()=>true,
  elementFromPoint:(x,y)=>y>=150&&expanded?options[Math.min(2,Math.floor((y-150)/25))]:trigger,
  querySelector:s=>s==='main'?body:null,
  querySelectorAll:s=>{
    if(s==='span.rc-FillableBlank [role="button"][aria-haspopup="listbox"]')return [trigger];
    if(s==='[role="listbox"]')return expanded?[listbox]:[];
    if(s===actionSelector)return [trigger,submit];
    return [];
  }};
const location=new URL('https://www.coursera.org/learn/philosophy/assignment-submission/fixture/example/attempt');
const context={document,location,innerWidth:1920,innerHeight:991,Node:{DOCUMENT_POSITION_FOLLOWING:4},
  getComputedStyle:()=>style,setTimeout:callback=>callback(),URL,
  MouseEvent:class MouseEvent{constructor(type,options){this.type=type;this.options=options;}}};
async function main(){
  const run=vm.runInNewContext(payload.code,context);
  const base={key:'assignment:fixture'};
  const initial=await run({...base,action:'inspect'});
  const closed=await run({...base,action:'inspect_select_options',snapshot_id:initial.snapshot_id});
  const triggerLocator=await run({...base,action:'locate_select_trigger',
    snapshot_id:initial.snapshot_id,control_id:'c0'});
  const triggerAction=await run({...base,action:'activate_select_trigger',
    snapshot_id:initial.snapshot_id,control_id:'c0'});
  const optionsRead=await run({...base,action:'inspect_select_options',snapshot_id:initial.snapshot_id});
  const invalid=await run({...base,action:'locate_select_option',
    snapshot_id:optionsRead.snapshot_id,control_id:'c0',option_id:'c0:o1',
    option_text:'Counterfactual',expected_options:['Changed','Counterfactual','Influential Instances']});
  const set=await run({...base,action:'set_answers',snapshot_id:optionsRead.snapshot_id,
    selected:[],numeric:{},select:{c0:{id:'c0:o1',text:'Counterfactual',options:optionLabels}}});
  const optionLocator=await run({...base,action:'locate_select_option',
    snapshot_id:optionsRead.snapshot_id,control_id:'c0',option_id:'c0:o1',
    option_text:'Counterfactual',expected_options:optionLabels});
  const optionAction=await run({...base,action:'activate_select_option',
    snapshot_id:optionsRead.snapshot_id,control_id:'c0',option_id:'c0:o1',
    option_text:'Counterfactual',expected_options:optionLabels});
  const after=await run({...base,action:'inspect'});
  const wrong=await run({...base,action:'button',label:'Submit',snapshot_id:after.snapshot_id,
    expected_selected:[],expected_numeric:{},expected_select:{c0:'Prototype-based'}});
  const good=await run({...base,action:'button',label:'Submit',snapshot_id:after.snapshot_id,
    expected_selected:[],expected_numeric:{},expected_select:{c0:'Counterfactual'}});
  process.stdout.write(JSON.stringify({initial,closed,triggerLocator,triggerAction,optionsRead,
    invalid,set,optionLocator,optionAction,after,wrong,good,clicks}));
}
main().catch(error=>{console.error(error);process.exit(1)});
"""


FROZEN_MODAL_DOM = (SELECT_DOM.split("async function main(){", 1)[0]
    .replace("s==='span.rc-FillableBlank'?blank:null",
             "s==='span.rc-FillableBlank'?blank:s==='.cds-Modal-container'?modal:null")
    .replace("if(s==='[role=\"listbox\"]')return expanded?[listbox]:[];",
             "if(s==='[role=\"listbox\"]')return expanded?[listbox]:[];\n"
             "    if(s==='.cds-Modal-container')return [modal];")
    .replace("const location=new URL(", """let modalX=1920;
const animation={playState:'running',currentTime:0,
  effect:{getComputedTiming:()=>({endTime:225}),getKeyframes:()=>[
    {transform:'translateX(1920px) translateX(0px)'},{transform:'none'}]},
  finish(){this.playState='finished';this.currentTime=225;modalX=0;}};
const modal={style:{transform:'none'},contains:e=>e===trigger,
  getAnimations:()=>[animation],getBoundingClientRect:()=>({left:modalX,right:modalX+1920,x:modalX})};
const location=new URL(""")
    + """
async function main(){
 const run=vm.runInNewContext(payload.code,context),base={key:'assignment:fixture'};
 const before=await run({...base,action:'inspect'});
 animation.currentTime=5;
 const unsafe=await run({...base,action:'finish_frozen_modal'});
 animation.currentTime=0;
 const recovered=await run({...base,action:'finish_frozen_modal'});
 const after=await run({...base,action:'inspect'});
 process.stdout.write(JSON.stringify({before,unsafe,recovered,after,modalX}));
}
main().catch(error=>{console.error(error);process.exit(1)});
""")


class SelectQuizTests(unittest.TestCase):
    def test_only_exact_frozen_empty_modal_transition_is_finished(self):
        result=subprocess.run(["node","-e",FROZEN_MODAL_DOM],
                              input=json.dumps({"code":engine.QUIZ_UI_JS}),
                              capture_output=True,text=True,encoding="utf-8")
        self.assertEqual(result.returncode,0,result.stderr[-2000:])
        view=json.loads(result.stdout)
        self.assertTrue(view["before"]["offscreen_quiz_form"])
        self.assertEqual(view["before"]["controls"],[])
        self.assertIn("error",view["unsafe"])
        self.assertTrue(view["recovered"]["recovered"])
        self.assertEqual(view["modalX"],0)
        self.assertFalse(view["after"]["offscreen_quiz_form"])
        self.assertEqual(len(view["after"]["controls"]),1)

    def test_dom_discovers_selects_sets_value_and_guards_submit(self):
        payload={"code":engine.QUIZ_UI_JS}
        result=subprocess.run(["node","-e",SELECT_DOM],input=json.dumps(payload),
                              capture_output=True,text=True,encoding="utf-8")
        self.assertEqual(result.returncode,0,result.stderr[-2000:])
        view=json.loads(result.stdout)
        self.assertEqual(view["initial"]["controls"][0]["type"],"select")
        self.assertEqual(view["closed"]["error_code"],"select_closed")
        self.assertEqual(view["triggerLocator"]["target"]["x"],160)
        self.assertEqual(view["triggerAction"]["dispatched"],"mousedown")
        self.assertEqual([o["text"] for o in view["optionsRead"]["select_options"]["c0"]],
                         ["Prototype-based","Counterfactual","Influential Instances"])
        self.assertIn("error",view["invalid"])
        self.assertTrue(view["set"]["verified"])
        self.assertEqual(view["optionLocator"]["target"]["y"],187)
        self.assertEqual(view["optionAction"]["dispatched"],"click")
        self.assertEqual(view["after"]["controls"][0]["value"],"Counterfactual")
        self.assertEqual(view["after"]["answer_snapshot_id"],view["initial"]["answer_snapshot_id"])
        self.assertIn("error",view["wrong"])
        self.assertEqual(view["good"]["clicked"],"Submit")
        self.assertEqual(view["clicks"].count("option:1"),1)
        self.assertNotIn("unhandled-click",view["clicks"])
        self.assertEqual(view["clicks"].count("submit"),1)

    def test_builds_radio_and_fillable_blank_as_two_questions(self):
        questions=engine.build_quiz_questions(mixed_ui())
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[1]["control_type"], "select")
        self.assertEqual(questions[1]["control_id"], "c2")
        self.assertEqual(questions[1]["selection"], "one")
        self.assertEqual(len(questions[1]["options"]), 3)
        self.assertNotIn("numeric_answer", engine.answer_schema(questions)["properties"]["answers"]["items"]["required"])

    def test_plan_maps_synthetic_option_to_exact_live_option_text(self):
        questions=engine.build_quiz_questions(mixed_ui())
        self.assertEqual(engine.validate_answer_plan(plan(), questions), {
            "selected":["c0"], "numeric":{},
            "select":{"c2":{"id":"c2:o1", "text":"Counterfactual",
                            "options":["Prototype-based", "Counterfactual", "Influential Instances"]}},
        })

    def test_missing_ambiguous_or_changed_options_stop_before_answering(self):
        for bad in (
            None,
            [{"id":"c2:o0", "text":"Only one"}],
            [{"id":"c2:o0", "text":"Same"},{"id":"c2:o1", "text":"same"}],
            [{"id":"c2:o0", "text":"A"},{"id":"c2:o5", "text":"B"}],
        ):
            ui=mixed_ui()
            if bad is None:
                ui.pop("select_options")
            else:
                ui["select_options"]["c2"]=bad
            with self.subTest(bad=bad), self.assertRaises(engine.StopRun):
                engine.build_quiz_questions(ui)

    def test_plan_rejects_wrong_or_multiple_listbox_choices(self):
        questions=engine.build_quiz_questions(mixed_ui())
        for selected in ([], ["c2"], ["c2:o9"], ["c2:o0", "c2:o1"]):
            candidate=plan()
            candidate["answers"][1]["selected_controls"]=selected
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                engine.validate_answer_plan(candidate, questions)

    def test_attempt_memory_preserves_exact_option_text(self):
        questions=engine.build_quiz_questions(mixed_ui())
        history=quiz_memory.record_attempt([], questions, ["c0","c2:o1"], [])
        selected=next(q for q in history[0]["questions"] if "Fill in the blank" in q["text"])
        self.assertEqual(selected["selected_answers"], ["Counterfactual"])


if __name__ == "__main__":
    unittest.main()
