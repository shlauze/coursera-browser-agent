"""Offline regression checks for Coursera programming-assignment status."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import course_adapter as adapter
import coursera_start as engine


COURSE = "https://www.coursera.org/learn/philosophy"
PROGRAMMING = COURSE + "/programming/capstone/multimodal-deep-learning-challenge"
KEY = "programming:capstone"


def page_row(label, checked=False):
    return {"url": PROGRAMMING, "type": "programming", "identity": "capstone",
            "title": "Multimodal Deep Learning Challenge", "aria_label": label,
            "evidence": ["aria-label: " + label], "checked": checked}


class FakeTab:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.index = 0

    async def evaluate(self, expression):
        if expression != engine.PAGE_JS:
            raise AssertionError("Unexpected browser action in offline test")
        result = self.snapshots[min(self.index, len(self.snapshots) - 1)]
        self.index += 1
        return copy.deepcopy(result)


class ProgrammingStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_course = engine.COURSE_URL
        engine.configure_course(COURSE)

    def tearDown(self):
        engine.configure_course(self.previous_course)

    def test_programming_is_distinct_and_unsupported(self):
        self.assertEqual(engine.item_key(PROGRAMMING), KEY)
        self.assertEqual(engine.item_key(COURSE + "/assignment-submission/capstone/quiz"),
                         "assignment:capstone")
        self.assertIsNone(engine.item_key("https://example.org/learn/philosophy/programming/capstone"))
        roster = adapter._roster_rows({"items": [page_row("Programming Assignment, Failed")]})
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster[0]["key"], KEY)
        self.assertTrue(roster[0]["unsupported"])
        self.assertIn("No automatic handler", roster[0]["unsupported_reason"])

    def test_failed_grade_cannot_be_completed(self):
        failed = page_row("Lab - Multimodal, Programming Assignment • 2h • Grade: 31% • Failed Graded",
                          checked=True)
        row = engine.rows_by_key({"items": [failed]})[KEY]
        self.assertEqual(row["status"], "not_completed")
        self.assertEqual(engine.status_from_labels(["Completed", failed["aria_label"]]), "not_completed")
        self.assertEqual(engine.status_from_labels(["Mark as completed"]), "unknown")

    async def test_only_passed_with_green_icon_can_be_verified(self):
        item = {"key": KEY, "url": PROGRAMMING, "title": "Multimodal Deep Learning Challenge"}
        snapshots = [
            {"url": PROGRAMMING, "items": [page_row("Programming Assignment, Failed", True)]},
            {"url": PROGRAMMING, "items": [page_row("Programming Assignment, Passed", False)]},
            {"url": PROGRAMMING, "items": [page_row("Programming Assignment, Passed", True)]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            runner = engine.CourseRunner(FakeTab(snapshots), None, Path(directory))
            self.assertFalse(await runner.verified_item(item))
            self.assertFalse(await runner.verified_item(item))
            self.assertTrue(await runner.verified_item(item))

    def test_sidebar_requires_green_and_positive_status(self):
        sidebar = {"rows": {4: {"items": [{"key": KEY, "checked": True, "completed": False}]}}}
        self.assertIsNone(adapter.verified_sidebar_module(sidebar, 4, expected_roster=[{"key": KEY}]))
        sidebar["rows"][4]["items"][0]["completed"] = True
        self.assertEqual(len(adapter.verified_sidebar_module(sidebar, 4,
                                                              expected_roster=[{"key": KEY}])), 1)


class ProgrammingDomTests(unittest.TestCase):
    def setUp(self):
        self.previous_course = engine.COURSE_URL
        engine.configure_course(COURSE)

    def tearDown(self):
        engine.configure_course(self.previous_course)

    def test_programming_link_is_discovered_but_failed_is_not_green(self):
        harness = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=JSON.parse(fs.readFileSync(0,'utf8'));
const course='https://www.coursera.org/learn/philosophy';
const location=new URL(course+'/home/module/4');
class E {
  constructor(tag,text='',attrs={},children=[]) {
    this.tagName=tag.toUpperCase();this.text=text;this.attrs=attrs;this.children=children;
    this.parentElement=null;this.hidden=false;this.inert=false;this.disabled=false;
    for(const child of children)child.parentElement=this;
  }
  get innerText(){return this.text||this.children.map(x=>x.innerText).join('\n');}
  get href(){return new URL(this.attrs.href,location.href).href;}
  getClientRects(){return [{}];}
  getAttribute(name){return this.attrs[name]??null;}
  matches(selector){return selector.split(',').some(part=>{
    part=part.trim();
    if(part.startsWith('.'))return (this.attrs.class||'').split(/\s+/).includes(part.slice(1));
    const tag=part.match(/^[\w-]+/);
    if(tag&&tag[0].toLowerCase()!==this.tagName.toLowerCase())return false;
    for(const attr of part.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)){
      if(!(attr[1] in this.attrs))return false;
      if(attr[2]!==undefined&&this.attrs[attr[1]]!==attr[2])return false;
    }
    return true;
  });}
  querySelectorAll(selector){return this.children.flatMap(e=>[
    ...(e.matches(selector)?[e]:[]),...e.querySelectorAll(selector)]);}
  closest(selector){for(let p=this;p;p=p.parentElement)if(p.matches(selector))return p;return null;}
  contains(e){return this===e||this.children.some(child=>child.contains(e));}
}
const href=course+'/programming/capstone/multimodal-deep-learning-challenge';
const failed='Lab - Multimodal, Programming Assignment • 2h • Grade: 31% • Failed Graded';
const passed='Lab - Multimodal, Programming Assignment • 2h • Passed';
const link=(label,green)=>new E('a','Lab - Multimodal',{href,'aria-label':label},
  green?[new E('span','',{'data-testid':'learn-item-success-icon'})]:[]);
function documentFor(label,green){
  const lesson=link(label,green);
  const heading=new E('span','Module 4',{'data-testid':'module-number-heading'});
  const sidebar=new E('div','',{'class':'cds-AccordionRoot-container'},[heading,lesson]);
  const main=new E('main','',{},[new E('nav','',{'aria-label':'Lessons'},[link(label,green)])]);
  const body=new E('body','',{},[sidebar,main]);
  return {body,readyState:'complete',title:'Course',querySelectorAll:s=>body.querySelectorAll(s),
    querySelector:s=>body.querySelectorAll(s)[0]||null};
}
function run(script,label,green){
  const document=documentFor(label,green);
  const ctx={URL,location,document,getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'})};
  return vm.runInNewContext('('+source[script]+')'+(script==='page'?'':'({course_path:"/learn/philosophy",number:4})'),ctx);
}
let row=run('sidebar',failed,true).modules[0].items[0];
assert.equal(row.key,'programming:capstone');
assert.equal(row.checked,true);
assert.equal(row.completed,false);
row=run('sidebar',passed,false).modules[0].items[0];
assert.equal(row.completed,false);
row=run('sidebar',passed,true).modules[0].items[0];
assert.equal(row.completed,true);
let roster=run('roster',failed,false);
assert.equal(roster.items[0].type,'programming');
assert.equal(roster.items[0].identity,'capstone');
const page=run('page',failed,false);
assert.equal(page.items.filter(x=>x.type==='programming').length,2);
assert.equal(page.items[0].checked,false);
process.stdout.write('Programming DOM status checks passed');
"""
        sources = {"sidebar": adapter.SIDEBAR_JS, "roster": adapter.ROSTER_JS,
                   "page": engine.PAGE_JS}
        result = subprocess.run(["node", "-e", harness], input=json.dumps(sources),
                                text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Programming DOM status checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
