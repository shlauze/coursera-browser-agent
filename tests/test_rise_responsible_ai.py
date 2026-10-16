"""Offline DOM guards for the observed Responsible AI Rise reading controls."""

import json
import subprocess
import unittest

import rise_scorm_flow as flow


def run_node(source, *arguments):
    result = subprocess.run(
        ["node", "-e", source, *arguments],
        capture_output=True, text=True, check=False, timeout=10,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class ResponsibleAiDomTests(unittest.TestCase):
    def test_continue_prompt_requires_exact_observed_structure(self):
        script = r"""
const guard=new Function('return '+process.argv[1])();
const clean=x=>(x||'').replace(/\s+/g,' ').trim();
let state={label:'But what is bias?',disabled:false,visible:true,
  selector:true,parent:true,child:true,activity:false};
const button={type:'button',get disabled(){return state.disabled},
  get innerText(){return state.label},
  getAttribute:name=>name==='aria-disabled'?'false':null,
  getClientRects:()=>state.visible?[{}]:[],
  matches:()=>state.selector,
  closest:()=>state.parent?{}:null,
  querySelector:selector=>selector.startsWith('.fr-view')?
    (state.child?{}:null):(state.activity?{}:null)};
const check=(title='Responsible AI')=>Boolean(guard([button],title,clean));
let output={observed:check()};
state.label='CONTINUE';output.generic=check('Other lesson');
state.label='But what is bias?';output.wrongTitle=check('Other lesson');
state.selector=false;output.wrongStructure=check();state.selector=true;
state.child=false;output.noTextNode=check();state.child=true;
state.activity=true;output.nestedActivity=check();state.activity=false;
state.disabled=true;output.disabled=check();state.disabled=false;
state.visible=false;output.hidden=check();state.visible=true;
output.duplicate=Boolean(guard([button,button],'Responsible AI',clean));
process.stdout.write(JSON.stringify(output));
"""
        found = run_node(script, flow.STATIC_CONTINUE_JS)
        self.assertTrue(found["observed"])
        self.assertTrue(found["generic"])
        for key in ("wrongTitle", "wrongStructure", "noTextNode",
                    "nestedActivity", "disabled", "hidden", "duplicate"):
            self.assertFalse(found[key], key)

    def test_two_static_tabs_require_unique_content_and_visible_selection(self):
        script = r"""
const guard=new Function('return '+process.argv[1])();
const clean=x=>(x||'').replace(/\s+/g,' ').trim();
const state={active:1,invalid:null};
const labels=['Gender classification systems','Recidivism prediction tool'];
const contents=[
 'Several sensitive AI applications are riddled with examples of biased behaviour. '.repeat(2),
 'Fairness bias in AI models stems from biased training data and design. '.repeat(2)];
const holder={},header={children:[]};
const tabs=labels.map((label,index)=>({
  id:`tab-example-${index}`,type:'button',innerText:label,
  parentElement:header,disabled:false,
  get tabIndex(){return state.active===index+1?0:-1},
  getAttribute:key=>key==='aria-selected'?
    (state.active===index+1?'true':'false'):
    key==='aria-controls'?`tabpanel-example-${index}`:
    key==='aria-disabled'?'false':null,
  getClientRects:()=>[{}],
  matches:key=>key==='button.blocks-tabs__header-item[role="tab"]',
  click:()=>{state.active=index+1}}));
header.children=tabs;
const panels=contents.map((content,index)=>({
  id:`tabpanel-example-${index}`,
  get textContent(){return state.invalid==='duplicate_content'&&index===1?
    contents[0]:content},
  parentElement:holder,
  classList:{contains:()=>state.active===index+1},
  getAttribute:key=>key==='aria-labelledby'?`tab-example-${index}`:null,
  getClientRects:()=>state.invalid==='visible_inactive'?
    [{}]:(state.active===index+1?[{}]:[]),
  matches:key=>key==='div.blocks-tabs__content-item[role="tabpanel"]',
  querySelectorAll:()=>state.invalid==='button_activity'&&index===1?
    [{matches:()=>false}]:[]}));
const group={
  querySelector:key=>null,
  querySelectorAll:key=>key==='.blocks-tabs__header[role="tablist"]'?[header]:
    key==='div.blocks-tabs__content-item[role="tabpanel"]'?panels:[]};
const page={URL:'https://d10o6em2qtnr4q.cloudfront.net/',
  querySelectorAll:key=>key==='div.blocks-tabs'?[group]:
    key==='button.blocks-tabs__header-item[role="tab"]'?tabs:
    key==='div.blocks-tabs__content-item[role="tabpanel"]'?panels:[]};
const wrap={contains:node=>node===group};
const links=()=>state.invalid==='activity_link'?
  'The Rise reading contains an activity-like content link.':null;
const inspect=()=>{const result=guard(page,wrap,clean,links);
  delete result.buttons;return result};
const output={first:inspect()};
tabs[1].click();output.second=inspect();
for(const kind of ['duplicate_content','visible_inactive',
                    'button_activity','activity_link']){
 state.active=1;state.invalid=kind;output[kind]=inspect();state.invalid=null;
}
process.stdout.write(JSON.stringify(output));
"""
        found = run_node(script, flow.STATIC_TABS_JS)
        self.assertEqual(found["first"]["activeIndex"], 1)
        self.assertEqual(found["second"]["activeIndex"], 2)
        self.assertEqual(len(found["second"]["tabs"]), 2)
        self.assertEqual(found["first"]["signature"],
                         found["second"]["signature"])
        for key in ("duplicate_content", "visible_inactive",
                    "button_activity", "activity_link"):
            self.assertIn("error", found[key], key)


if __name__ == "__main__":
    unittest.main()
