"""Offline discovery/completion regressions; no Chrome or provider requests."""
import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
adapter = importlib.import_module("course_adapter")
engine = importlib.import_module("coursera_start")
COURSE = "https://www.coursera.org/learn/philosophy"
LESSON_FIXTURE = ROOT / "tests/fixtures/roster_lesson_navigation.json"


class FakeTab:
    def __init__(self, results):
        self.results = list(results)
        self.navigations = []
        self.expressions = []

    async def navigate(self, url):
        self.navigations.append(url)

    async def evaluate(self, expression):
        self.expressions.append(expression)
        if len(self.results) > 1:
            return copy.deepcopy(self.results.pop(0))
        return copy.deepcopy(self.results[0])


def modules(values):
    return {"url":COURSE + "/home/module/1", "ready":"complete", "modules":values}


def roster(*types):
    return {"url":COURSE + "/home/module/1", "ready":"complete", "collapsed":0, "warnings":[],
            "scope":"lesson_navigation", "lesson_navigation_count":1,
            "items":[{"type":kind, "identity":"item" + str(index), "title":"Item " + str(index),
                      "url":COURSE + "/" + kind + "/item" + str(index) + "/lesson",
                      "aria_label":"Completed", "evidence":["aria-label: Completed"]}
                     for index, kind in enumerate(types)]}


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sleep = patch.object(adapter.asyncio, "sleep", new=AsyncMock())
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.probes = patch.object(adapter, "PROBES", 6)
        self.probes.start()
        self.addCleanup(self.probes.stop)

    async def test_modules_wait_for_two_equal_complete_snapshots(self):
        tab = FakeTab([modules([]), modules([1]), modules([1, 2]), modules([1, 2])])
        self.assertEqual(await adapter.discover_modules(tab, COURSE), [1, 2])
        self.assertEqual(len(tab.expressions), 10)
        self.assertEqual(tab.navigations, [COURSE + "/home/module/1", COURSE + "/home"])

    async def test_partial_one_module_outline_cannot_hide_four_module_course(self):
        partial = modules([1])
        full = modules([1, 2, 3, 4])
        tab = FakeTab([partial] * 4 + [full] * 4)
        self.assertEqual(await adapter.discover_modules(tab, COURSE), [1, 2, 3, 4])
        self.assertEqual(tab.navigations, [COURSE + "/home/module/1", COURSE + "/home"])

    async def test_prior_module_floor_rejects_shrunken_outline(self):
        tab = FakeTab([modules([1])])
        with self.assertRaisesRegex(engine.StopRun, "minimum previously seen: 3"):
            await adapter.discover_modules(tab, COURSE, minimum_modules=3)
        self.assertIn(COURSE + "/home/module/3", tab.navigations)

    async def test_one_module_course_requires_independent_outline_confirmation(self):
        one = {**modules([1]), "outline_count":1}
        tab = FakeTab([one])
        self.assertEqual(await adapter.discover_modules(tab, COURSE), [1])
        self.assertEqual(tab.navigations, [COURSE + "/home/module/1", COURSE + "/home"])

    async def test_configuration_cannot_bypass_missing_modules(self):
        with self.assertRaisesRegex(engine.StopRun, "full module list"):
            await adapter.discover_modules(FakeTab([modules([1])]), COURSE, [1, 2])

    async def test_module_gap_rejected(self):
        with self.assertRaisesRegex(engine.StopRun, "without gaps"):
            await adapter.discover_modules(FakeTab([modules([1, 3])]), COURSE)

    async def test_wrong_course_stops_immediately(self):
        tab = FakeTab([{"blocked":True, "error":"wrong course"}])
        with self.assertRaisesRegex(engine.StopRun, "wrong course"):
            await adapter.discover_modules(tab, COURSE)
        self.assertEqual(len(tab.expressions), 1)

    async def test_roster_keeps_unknown_items_and_waits_three_stable_reads(self):
        tab = FakeTab([roster("supplement"), roster("supplement", "ungradedLti", "peer")])
        result = await adapter.discover_roster(tab, COURSE, 1)
        self.assertEqual(len(tab.expressions), 4)
        self.assertEqual([row["key"] for row in result], ["supplement:item0", "ungradedLti:item1", "peer:item2"])
        self.assertEqual([row["unsupported"] for row in result], [False, False, True])
        self.assertEqual(result[0]["status"], "completed")

    async def test_lab_and_dialogue_are_supported_and_keep_sidebar_keys(self):
        engine.configure_course(COURSE)
        result = await adapter.discover_roster(FakeTab([roster("ungradedLab", "coach")]), COURSE, 1)
        self.assertEqual([row["key"] for row in result], ["ungradedLab:item0", "coach:item1"])
        self.assertEqual([row["unsupported"] for row in result], [False, False])
        self.assertEqual([engine.item_key(row["url"]) for row in result],
                         ["ungradedLab:item0", "coach:item1"])
        sidebar = engine.rows_by_key({"items":result})
        self.assertEqual(set(sidebar), {"ungradedLab:item0", "coach:item1"})

    async def test_roster_ignores_stable_timeline_cards_until_lessons_load(self):
        interim = {**roster("team", "team"), "scope":"main", "lesson_navigation_count":0}
        lessons = roster("supplement", "supplement")
        tab = FakeTab([interim, interim, interim, lessons, lessons, lessons])
        result = await adapter.discover_roster(tab, COURSE, 1, expected_count=2)
        self.assertEqual([row["key"] for row in result], ["supplement:item0", "supplement:item1"])
        self.assertEqual(len(tab.expressions), 6)

    async def test_roster_with_only_timeline_cards_stops_with_diagnostic(self):
        interim = {**roster("team", "team"), "scope":"main", "lesson_navigation_count":0}
        tab = FakeTab([interim])
        with self.assertRaisesRegex(engine.StopRun, "(?i)lessons") as caught:
            await adapter.discover_roster(tab, COURSE, 1, expected_count=2)
        self.assertEqual(caught.exception.diagnostic["snapshot"]["scope"], "main")
        self.assertEqual(len(tab.expressions), adapter.PROBES)

    async def test_duplicate_links_do_not_inflate_item_count(self):
        state = roster("lecture")
        state["items"].append(copy.deepcopy(state["items"][0]))
        result = await adapter.discover_roster(FakeTab([state]), COURSE, 1, expected_count=1)
        self.assertEqual(len(result), 1)

    async def test_incomplete_known_roster_stops(self):
        with self.assertRaisesRegex(engine.StopRun, "expected items: 2") as caught:
            await adapter.discover_roster(FakeTab([roster("lecture")]), COURSE, 1, expected_count=2)
        self.assertEqual(caught.exception.diagnostic["module"], 1)
        self.assertEqual(caught.exception.diagnostic["expected_count"], 2)
        self.assertEqual(len(caught.exception.diagnostic["snapshot"]["items"]), 1)

    async def test_prior_roster_cannot_shrink_on_restart_without_configured_count(self):
        full = roster("supplement", "lecture")
        prior = adapter._roster_rows(full)
        partial = copy.deepcopy(full)
        partial["items"] = partial["items"][1:]
        with self.assertRaisesRegex(engine.StopRun, "Previously discovered lessons are missing") as caught:
            await adapter.discover_roster(FakeTab([partial]), COURSE, 1, expected_roster=prior)
        self.assertEqual(caught.exception.diagnostic["missing_previous_keys"], ["supplement:item0"])

    async def test_prior_roster_waits_for_collapsed_sections_to_reappear(self):
        full = roster("supplement", "lecture")
        prior = adapter._roster_rows(full)
        partial = copy.deepcopy(full)
        partial["items"] = partial["items"][1:]
        result = await adapter.discover_roster(FakeTab([partial, partial, full]), COURSE, 1,
                                               expected_roster=prior)
        self.assertEqual([item["key"] for item in result], ["supplement:item0", "lecture:item1"])

    async def test_unknown_layout_and_locked_items_do_not_succeed(self):
        for state in ({"error":"main missing"}, {**roster("lecture"), "warnings":["Locked lesson"]}):
            with self.subTest(state=state):
                with self.assertRaises(engine.StopRun):
                    await adapter.discover_roster(FakeTab([state]), COURSE, 1)

    async def test_expansion_resets_roster_stability(self):
        tab = FakeTab([{**roster("lecture"), "collapsed":1}, {"clicked":["Lesson 1"]}, roster("lecture")])
        result = await adapter.discover_roster(tab, COURSE, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(tab.expressions), 5)
        self.assertIn('"action": "expand"', tab.expressions[1])

    async def test_congratulations_without_course_completion_is_attention(self):
        state = {**modules([1, 2]), "evidence":[], "congratulations":["Congratulations! You completed Module 2"]}
        result = await adapter.verify_course_completion(FakeTab([state]), COURSE, [1, 2])
        self.assertFalse(result["verified"])
        self.assertIn("whole-course", result["reason"])

    async def test_course_completion_requires_same_final_modules(self):
        state = {**modules([1, 2, 3]), "evidence":["You completed this course!"]}
        result = await adapter.verify_course_completion(FakeTab([state]), COURSE, [1, 2])
        self.assertFalse(result["verified"])
        self.assertIn("module list differs", result["reason"])

    async def test_explicit_completion_verified_twice(self):
        state = {**modules([1, 2]), "evidence":["You completed this course!"]}
        tab = FakeTab([state])
        result = await adapter.verify_course_completion(tab, COURSE, [1, 2])
        self.assertTrue(result["verified"])
        self.assertEqual(len(tab.expressions), 2)
        self.assertEqual(tab.navigations, [COURSE + "/home"])

    async def test_sidebar_requires_stable_completed_labels_icons_and_full_roster(self):
        state = {"url":COURSE + "/assignment-submission/x/attempt", "ready":"complete",
                 "modules":[{"number":1, "items":[
                     {"key":"lecture:a", "checked":True, "completed":True},
                     {"key":"assignment:b", "checked":True, "completed":True}]},
                            {"number":2, "items":[
                     {"key":"lecture:c", "checked":False, "completed":False}]}]}
        tab = FakeTab([state])
        sidebar = await adapter.inspect_sidebar_progress(tab, COURSE)
        self.assertEqual(sidebar["modules"], [1, 2])
        self.assertEqual(len(tab.expressions), 2)
        self.assertEqual(len(adapter.verified_sidebar_module(sidebar, 1, expected_count=2)), 2)
        self.assertIsNone(adapter.verified_sidebar_module(sidebar, 1, expected_count=3))
        self.assertIsNone(adapter.verified_sidebar_module(sidebar, 1))
        self.assertIsNone(adapter.verified_sidebar_module(sidebar, 2, expected_count=1))
        self.assertIsNone(adapter.verified_sidebar_module(sidebar, 1,
            expected_roster=[{"key":"lecture:a"}, {"key":"assignment:other"}]))

    async def test_final_green_outline_verifies_course_without_navigation(self):
        state = {"url":COURSE + "/supplement/last/lesson", "ready":"complete",
                 "modules":[{"number":1, "items":[
                     {"key":"lecture:a", "checked":True, "completed":True}]},
                            {"number":2, "items":[
                     {"key":"assignment:b", "checked":True, "completed":True}]}]}
        tab = FakeTab([state])
        result = await adapter.verify_course_completion(tab, COURSE, [1, 2],
            {1:[{"key":"lecture:a"}], 2:[{"key":"assignment:b"}]})
        self.assertTrue(result["verified"])
        self.assertEqual(result["method"], "sidebar")
        self.assertEqual(tab.navigations, [])

    async def test_untrusted_course_urls_never_navigate(self):
        for url in ("http://www.coursera.org/learn/philosophy", "https://example.com/learn/philosophy",
                    COURSE + "?next=anything", "https://evil@www.coursera.org/learn/philosophy",
                    "https://www.coursera.org:invalid/learn/philosophy"):
            tab = FakeTab([modules([1])])
            with self.subTest(url=url):
                with self.assertRaises(engine.StopRun):
                    await adapter.discover_modules(tab, url)
                self.assertEqual(tab.navigations, [])


class ActualJavascriptTests(unittest.TestCase):
    def test_visible_dom_scope_unknown_materials_and_completion_phrases(self):
        harness = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=JSON.parse(fs.readFileSync(0,'utf8'));
let location;
class E{
  constructor(tag,text='',attrs={},children=[]){this.tagName=tag.toUpperCase();this.text=text;this.attrs=attrs;this.children=children;
    this.parentElement=null;this.hidden=!!attrs.hidden;this.inert=false;this.disabled=!!attrs.disabled;this.clicks=0;
    for(const child of children)child.parentElement=this;}
  get innerText(){return this.text || this.children.filter(c=>!c.hidden).map(c=>c.innerText).join('\n');}
  get href(){return new URL(this.attrs.href,location.href).href;}
  getClientRects(){return this.hidden?[]:[{width:100,height:30}];}
  getAttribute(name){return this.attrs[name]??null;}
  matches(selector){return selector.split(',').some(part=>{
    part=part.trim();const tag=part.match(/^[\w-]+/);if(tag && tag[0].toLowerCase()!==this.tagName.toLowerCase())return false;
    for(const attr of part.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)){
      if(!(attr[1] in this.attrs))return false;if(attr[2]!==undefined && this.attrs[attr[1]]!==attr[2])return false;
    }return true;});}
  querySelectorAll(selector){return this.children.flatMap(e=>[...(e.matches(selector)?[e]:[]),...e.querySelectorAll(selector)]);}
  closest(selector){for(let p=this;p;p=p.parentElement)if(p.matches(selector))return p;return null;}
  contains(e){return this===e || this.children.some(c=>c.contains(e));}
  click(){this.clicks++;if(this.attrs['aria-expanded']==='false')this.attrs['aria-expanded']='true';}
}
function run(script,{url='https://www.coursera.org/learn/philosophy/home/module/1',children=[],request={}}={}){
  location=new URL(url);const root=new E('body','',{},children);
  const document={readyState:'complete',querySelectorAll:s=>root.querySelectorAll(s)};
  return vm.runInNewContext('('+source[script]+')('+JSON.stringify({course_path:'/learn/philosophy',number:1,...request})+')',
    {URL,location,document,getComputedStyle:e=>({display:e.hidden?'none':'block',visibility:'visible',opacity:'1'})});
}
const a=(type,id,text,attrs={})=>new E('a',text,{href:'/learn/philosophy/'+type+'/'+id,...attrs});
const moduleLink=n=>a('home','module/'+n,'Module '+n);
let result=run('modules',{children:[moduleLink(1),new E('button','Module 2\nTitle'),new E('button','Module 99', {hidden:true}),new E('a','Module 3',{href:'/learn/other/home/module/3'})]});
assert.equal(JSON.stringify(result.modules),'[1,2]');
result=run('modules',{children:[new E('a','Skip to main content',{href:'/learn/philosophy/home/module/1'})]});
assert.equal(JSON.stringify(result.modules),'[]');
result=run('modules',{children:[new E('nav','',{'data-e2e':'courseNavigation',hidden:true},
  [moduleLink(1),moduleLink(2),moduleLink(3),moduleLink(4)]),
  new E('a','Module 99',{href:'/learn/philosophy/home/module/99',hidden:true})]});
assert.equal(JSON.stringify(result.modules),'[1,2,3,4]');
assert.equal(run('modules',{url:'https://www.coursera.org/learn/other/home'}).blocked,true);
assert.equal(run('modules',{url:'https://example.org/learn/philosophy/home'}).blocked,true);
const timeline=new E('div','',{'data-testid':'course-timeline-card'},[
  a('team','later1','Later assignment'),a('team','later2','Another later assignment')]);
result=run('roster',{children:[new E('main','',{},[timeline])]});
assert.equal(result.error_code,'lessons_not_ready');
const main=new E('main','',{},[new E('nav','',{'aria-label':'Lessons'},[
  a('lecture','v','Video'),a('supplement','r','Reading'),a('ungradedLti','lab','Lab'),a('peer','p','Peer review')]),
  timeline,new E('aside','',{},[a('lecture','outside','Sidebar video')]),a('quiz','hidden','Hidden quiz',{hidden:true}),
  a('discussions','topic','Discussion'),new E('a','External',{href:'https://example.org/learn/philosophy/lab/x'})]);
result=run('roster',{children:[main]});
assert.equal(JSON.stringify(result.items.map(x=>x.type)), '["lecture","supplement","ungradedLti","peer"]');
assert.match(run('roster',{children:[]}).error,/main/);
assert.equal(run('roster',{url:'https://www.coursera.org/learn/philosophy/home/module/2',children:[main]}).error_code,'wrong_path');
// Rebuild actual captured ancestor paths: main > module wrappers > nav Lessons
// > lesson groups > item links. Two observed /team deadline cards are outside.
const attrsFrom=node=>Object.fromEntries([['role',node.role],['aria-label',node.aria],['class',node.cls]].filter(x=>x[1]));
function capturedBranch(row){
  const ancestors=source.fixture.lesson_ancestors;
  const navIndex=ancestors.findIndex(node=>node.tag==='NAV' && node.aria==='Lessons');
  assert.ok(navIndex>0,'Captured lesson has its real Lessons ancestor');
  let leaf=new E('a',row.title,{href:row.url,'aria-label':row.aria_label});
  for(let index=0;index<navIndex;index++){
    const node=ancestors[index];leaf=new E(node.tag,'',attrsFrom(node),[leaf]);
  }
  return leaf;
}
const capturedRows=source.fixture.links;
assert.equal(capturedRows.length,9);
const capturedNav=new E('nav','',{'aria-label':'Lessons'},capturedRows.map(capturedBranch));
let capturedSection=capturedNav;
const ancestors=source.fixture.lesson_ancestors;
const navIndex=ancestors.findIndex(node=>node.tag==='NAV' && node.aria==='Lessons');
const mainIndex=ancestors.findIndex(node=>node.tag==='MAIN');
assert.ok(mainIndex>navIndex);
for(let index=navIndex+1;index<mainIndex;index++){
  const node=ancestors[index];capturedSection=new E(node.tag,'',attrsFrom(node),[capturedSection]);
}
const sideCards=source.fixture.side_cards.map(row=>new E('a',row.title,{href:row.url}));
assert.equal(sideCards.length,2);
const capturedMain=new E('main','',attrsFrom(ancestors[mainIndex]),[capturedSection,...sideCards]);
result=run('roster',{children:[capturedMain]});
assert.equal(result.scope,'lesson_navigation');
assert.equal(result.items.length,9);
assert.equal(JSON.stringify(result.items.map(row=>row.url)),JSON.stringify(capturedRows.map(row=>row.url)));
assert.ok(result.items.every(row=>row.type!=='team'));
const attach=(parent,child)=>{child.parentElement=parent;parent.children.push(child);};
attach(capturedNav,new E('nav','',{'aria-label':'Other navigation'},[a('lecture','not-a-lesson','Other navigation link')]));
attach(capturedNav,new E('aside','',{},[a('lecture','aside-link','Aside link')]));
attach(capturedNav,new E('div','',{'role':'navigation','aria-label':'Related'},[a('lecture','role-nav-link','Related link')]));
attach(capturedNav,a('ungradedLti','real-lab','Actual unsupported lab'));
result=run('roster',{children:[capturedMain]});
assert.equal(result.items.length,10);
assert.equal(result.items.at(-1).type,'ungradedLti');
assert.ok(result.items.every(row=>!['not-a-lesson','aside-link','role-nav-link'].includes(row.identity)));
const insideCollapse=new E('button','Lesson 3',{'aria-expanded':'false'});
const namedCollapse=new E('button','Course Overview',{'aria-expanded':'false'});
const introCollapse=new E('button','Introduction to Explainable AI',{'aria-expanded':'false'});
const outsideCollapse=new E('button','Lesson 99',{'aria-expanded':'false'});
const lessonLinkWithControl=a('lecture','linked-control','Lesson link');
const nestedControl=new E('button','More options',{'aria-expanded':'false'});
attach(lessonLinkWithControl,nestedControl);
attach(capturedNav,insideCollapse);attach(capturedNav,namedCollapse);attach(capturedNav,introCollapse);
attach(capturedNav,lessonLinkWithControl);attach(capturedMain,outsideCollapse);
result=run('roster',{children:[capturedMain]});assert.equal(result.collapsed,3);
result=run('roster',{children:[capturedMain],request:{action:'expand'}});
assert.equal(insideCollapse.clicks,1);assert.equal(namedCollapse.clicks,1);assert.equal(introCollapse.clicks,1);
assert.equal(outsideCollapse.clicks,0);assert.equal(nestedControl.clicks,0);
const duplicateLessonNav=new E('nav','',{'aria-label':'Lessons'},[a('lecture','other-list','Ambiguous second lesson list')]);
attach(capturedMain,duplicateLessonNav);
assert.match(run('roster',{children:[capturedMain]}).error,/Multiple visible lesson/);
const button=new E('button','Lesson 1',{'aria-expanded':'false'});
result=run('roster',{children:[new E('main','',{},[new E('nav','',{'aria-label':'Lessons'},[button])])],request:{action:'expand'}});
assert.equal(button.clicks,1);assert.equal(JSON.stringify(result.clicked),'["Lesson 1"]');
result=run('roster',{children:[new E('main','',{},[new E('nav','',{'aria-label':'Lessons'},[
  a('lecture','v','Video',{'aria-label':'Locked lesson'})])])]});
assert.equal(result.warnings.length,1);
const completion=text=>run('completion',{url:'https://www.coursera.org/learn/philosophy/home',children:[new E('main',text),moduleLink(1)]});
for(const text of ['Congratulations!','Congratulations! You completed Module 1','100% complete','You completed this lesson!',
  'When you have completed this course, you can download a certificate.','Course completion requirements'])assert.equal(completion(text).evidence.length,0,text);
for(const text of ["You've completed this course!",'You have completed the course.','Congratulations! You completed this course!',
  'Course completed','This course is complete.'])assert.equal(completion(text).evidence.length,1,text);
assert.equal(completion('Congratulations!\nYou completed this course!').congratulations.length,1);
assert.equal(JSON.stringify(completion('Course completed').modules),'[1]');
process.stdout.write('Visible DOM discovery and completion checks passed');
"""
        sources = {"modules":adapter.MODULES_JS, "roster":adapter.ROSTER_JS, "completion":adapter.COMPLETION_JS,
                   "fixture":json.loads(LESSON_FIXTURE.read_text(encoding="utf-8"))}
        result = subprocess.run(["node", "-e", harness], input=json.dumps(sources), text=True,
                                capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
