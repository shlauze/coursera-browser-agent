"""Offline checks for read-only graded Jupyter inspection and queue handoff."""

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, call, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import coursera_start as engine
import course_queue
import programming_flow as flow


COURSE = 'https://www.coursera.org/learn/deep-learning-with-pytorch'
TITLE = 'Lab - Multimodal Deep Learning Challenge: Image, Text & Optimization in PyTorch'
ITEM = {'key': 'programming:W3x2U', 'type': 'programming',
        'url': COURSE + '/programming/W3x2U/lab-multimodal-deep-learning-challenge-image-text-optimization-in-pytorch',
        'title': TITLE + ' Programming Assignment • Grade: 31% • Failed',
        'aria_label': TITLE + ', Failed, Graded', 'unsupported': True,
        'unsupported_reason': 'No automatic handler for lesson type: programming'}


def notebook_snapshot():
    return {'url': 'https://lab.labs.coursera.org/notebooks/release/Task.ipynb',
            'path': 'release/Task.ipynb', 'dirty': False, 'kernel_status': 'idle', 'cells': [
                {'index': 0, 'type': 'code', 'source': 'def answer():\n    return 42\n',
                 'metadata': {'nbgrader': {'solution': True, 'locked': False, 'grade': False}},
                 'execution_count': 1, 'outputs': []},
                {'index': 1, 'type': 'code', 'source': '### BEGIN TESTS\nassert answer() == 42\n### END TESTS',
                 'metadata': {'nbgrader': {'solution': False, 'locked': True, 'grade': True}},
                 'execution_count': None,
                 'outputs': [{'type': 'stream', 'ename': '', 'text': '✅ TEST PASSED!'}]},
            ]}


class PureProgrammingChecks(unittest.TestCase):
    def test_grader_feedback_describes_previous_blank_submission(self):
        feedback = ("Score: 31/100. Test cell (Score: 0/10). "
                    "Test cell (Score: 5/10). Test cell (Score: 10/10). "
                    "Student's answer: # YOUR CODE HERE")
        summary = flow._feedback_summary(feedback)
        self.assertEqual(summary['raw_score'], '31/100')
        self.assertTrue(summary['blank_submission'])
        self.assertEqual(summary['failed_test_count'], 2)
        self.assertEqual([row['score'] for row in summary['test_scores']], ['0/10', '5/10', '10/10'])

    def test_grader_feedback_preserves_each_test_and_complete_text(self):
        detail = 'The expected output differs from the submitted tensor shape. ' + 'x' * 13000
        feedback = ('Score: 10/20. Test cell (Score: 0/10) ' + detail +
                    ' Test cell (Score: 10/10) This check passed.')
        summary = flow._feedback_summary(feedback)
        self.assertEqual(summary['full_text'], feedback)
        self.assertEqual(summary['failed_test_count'], 1)
        self.assertEqual([row['index'] for row in summary['test_scores']], [1, 2])
        self.assertIn('tensor shape', summary['test_scores'][0]['details'])
        self.assertFalse(summary['test_scores'][0]['passed'])
        self.assertTrue(summary['test_scores'][1]['passed'])
        self.assertGreater(len(summary['full_text']), len(summary['excerpt']))

    def test_test_cell_score_is_not_an_overall_grade(self):
        summary = flow._feedback_summary('Test cell (Score: 0/10)')
        self.assertTrue(summary['available'])
        self.assertIsNone(summary['raw_score'])
        self.assertEqual(summary['failed_test_count'], 1)
        self.assertEqual(flow._feedback_summary(''), {'available': False})

    def test_live_failure_sentence_reports_grade_and_failed_state(self):
        page = "You didn't pass. To pass you need at least 70/100 points. 31/100"
        self.assertEqual(flow._grade_text(page), ('31/100', True))
        self.assertEqual(flow._grade_text('Grade: 31% • Failed'), ('31%', True))
        self.assertEqual(flow._grade_text('Grade: 31% Failed Grade: 100%'), (None, True))

    def test_programming_page_accepts_its_heading_among_section_headings(self):
        harness = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const script=JSON.parse(fs.readFileSync(0,'utf8'));
const path='/learn/deep-learning-with-pytorch/programming/W3x2U/lab-multimodal-deep-learning-challenge-image-text-optimization-in-pytorch';
const title='Lab - Multimodal Deep Learning Challenge: Image, Text & Optimization in PyTorch';
const location=new URL('https://www.coursera.org'+path);
class E {
  constructor(tag,text='',children=[]) {
    this.tagName=tag.toUpperCase();this.text=text;this.children=children;
    this.parentElement=null;this.hidden=false;this.inert=false;this.disabled=false;
    for(const child of children)child.parentElement=this;
  }
  get innerText(){return this.text||this.children.map(x=>x.innerText).join('\n');}
  getClientRects(){return [{}];}
  getAttribute(){return null;}
  scrollIntoView(){}
  click(){this.clicked=true;}
  closest(){return null;}
  contains(other){return this===other||this.children.some(child=>child.contains(other));}
  matches(selector){return selector.split(',').some(part=>part.trim().toUpperCase()===this.tagName);}
  querySelectorAll(selector){return this.children.flatMap(child=>[
    ...(child.matches(selector)?[child]:[]),...child.querySelectorAll(selector)]);}
}
const run=(headings,action='inspect')=>{
  const main=new E('main','',[
    ...headings.map(heading=>new E('h1',heading)),
    new E('button','Resume lab'),
    new E('button','My submissions'),
    new E('button','Submit assignment'),
    new E('p',"You didn't pass. To pass you need at least 70/100 points. 31/100")]);
  const document={body:new E('body','',[main]),readyState:'complete',
    querySelectorAll:selector=>[main].filter(element=>element.matches(selector))};
  return vm.runInNewContext('('+script+')({path,heading:title,action})',
    {URL,location,document,path,title,action,getComputedStyle:()=>({display:'block',visibility:'visible'})});
};
let result=run([title,'Instructions','Lab submission and grades']);
assert.equal(result.error,undefined);
assert.equal(result.heading,title);
assert.equal(result.counts.resume,1);
assert.equal(result.counts.submissions,1);
assert.match(result.page_text,/31\/100/);
result=run([title,'Instructions'],'submissions');
assert.equal(result.clicked,'submissions');
result=run([title,title,'Instructions']);
assert.match(result.error,/ambiguous/i);
process.stdout.write('Programming page heading checks passed');
"""
        result = subprocess.run(['node', '-e', harness], input=json.dumps(flow.PROGRAMMING_PAGE_JS),
                                text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Programming page heading checks passed', result.stdout)

    def test_feedback_expansion_requires_explicit_one_time_request(self):
        harness = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const script=JSON.parse(fs.readFileSync(0,'utf8'));
const path='/learn/deep-learning-with-pytorch/programming/W3x2U/task';
const location=new URL('https://www.coursera.org'+path+'/submission');
let clicks=0;
const button={innerText:'Show grader output',disabled:false,
  getClientRects:()=>[{}],click:()=>{clicks++;}};
const row={innerText:'Submitted at: Oct 15, 5:22 AM +05 31/100',getClientRects:()=>[{}],
  querySelectorAll:selector=>selector==='button'?[button]:[]};
const document={querySelectorAll:selector=>selector==='.rc-SubmissionRow'?[row]:[]};
const run=async expand=>vm.runInNewContext('('+script+')({path,expand})',
  {URL,location,document,path,expand});
(async()=>{
  const first=await run(true);
  assert.equal(first.expanded,true);
  assert.equal(clicks,1);
  const second=await run(false);
  assert.equal(second.expanded,false);
  assert.equal(clicks,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(['node', '-e', harness], input=json.dumps(flow.PROGRAMMING_FEEDBACK_JS),
                                text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_html_success_without_nbgrader_feedback_is_rejected(self):
        harness = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const script=JSON.parse(fs.readFileSync(0,'utf8'));
const path='/learn/deep-learning-with-pytorch/programming/W3x2U/task';
const location=new URL('https://www.coursera.org'+path+'/submission');
const link={innerText:'View grader feedback',getClientRects:()=>[{}],
  href:'https://www.coursera.org/api/rest/v1/executorruns/richfeedback?id=abcdefgh&feedbackType=HTML'};
const row={innerText:'Submitted at: Oct 15, 5:22 AM +05 31/100',getClientRects:()=>[{}],
  querySelectorAll:selector=>selector==='a[href]'?[link]:[]};
const document={querySelectorAll:selector=>selector==='.rc-SubmissionRow'?[row]:[]};
class DOMParser {
  parseFromString(html){return {body:{textContent:html},querySelectorAll:()=>[]};}
}
let requested='';
const run=async html=>vm.runInNewContext('('+script+')({path,expand:false})',
  {URL,location,document,path,DOMParser,fetch:async url=>{
    requested=url;return {ok:true,text:async()=>html};}});
(async()=>{
  let result=await run('Sign in to Coursera');
  assert.match(result.error,/without recognizable nbgrader/i);
  result=await run('Test cell (Score: 0 / 10) Wrong output');
  assert.match(result.error,/without recognizable nbgrader/i);
  result=await run('Notebook (Score: 10 / 20) Test cell (Score: 0 / 10) Wrong output');
  assert.equal(result.available,true);
  assert.equal(result.submission_count,1);
  const oldLink={...link,href:link.href.replace('abcdefgh','oldrun12')};
  const duplicate={...row,innerText:'October 14, 2026 11:00 PM +05 Submitted at: Oct 14, 11:00 PM +05 45/100',
    querySelectorAll:selector=>selector==='a[href]'?[oldLink]:[]};
  row.innerText='October 15, 2026 5:22 AM +05 Submitted at: Oct 15, 5:22 AM +05 31/100';
  document.querySelectorAll=selector=>selector==='.rc-SubmissionRow'?[row,duplicate]:[];
  result=await run('Notebook (Score: 10 / 20) Test cell (Score: 0 / 10)');
  assert.equal(result.available,true);
  assert.equal(result.submission_count,2);
  assert.match(result.submission_record,/31\/100/);
  assert.match(requested,/id=abcdefgh/);
  row.innerText='October 14, 2026 11:00 PM +05 Submitted at: Oct 14, 11:00 PM +05 31/100';
  result=await run('Notebook (Score: 10 / 20) Test cell (Score: 0 / 10)');
  assert.match(result.error,/ambiguous latest/i);
  row.innerText='Submitted at: Oct 15, 5:22 AM +05 31/100';
  result=await run('Notebook (Score: 10 / 20) Test cell (Score: 0 / 10)');
  assert.match(result.error,/unreadable timestamps/i);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(['node', '-e', harness], input=json.dumps(flow.PROGRAMMING_FEEDBACK_JS),
                                text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exact_course_identity_and_title_with_commas(self):
        path, heading = flow._path_and_heading(ITEM, COURSE)
        self.assertTrue(path.endswith('/programming/W3x2U/lab-multimodal-deep-learning-challenge-image-text-optimization-in-pytorch'))
        self.assertEqual(heading, TITLE)
        with self.assertRaises(engine.StopRun):
            flow._path_and_heading({**ITEM, 'key': 'programming:other'}, COURSE)
        with self.assertRaises(engine.StopRun):
            flow._path_and_heading({**ITEM, 'url': ITEM['url'].replace('coursera.org', 'example.com')}, COURSE)

    def test_notebook_frame_origin_is_narrow(self):
        self.assertTrue(flow._notebook_frame_url('https://sbogzlberdio.labs.coursera.org/notebooks/release/Task.ipynb'))
        self.assertFalse(flow._notebook_frame_url('https://evil.example.org/notebooks/release/Task.ipynb'))
        self.assertFalse(flow._notebook_frame_url('http://sbogzlberdio.labs.coursera.org/notebooks/release/Task.ipynb'))
        self.assertFalse(flow._notebook_frame_url('https://sbogzlberdio.labs.coursera.org.evil.org/notebooks/release/Task.ipynb'))

    def test_visible_test_output_can_confirm_even_without_execution_count(self):
        snapshot = notebook_snapshot()
        snapshot['cells'][1]['source'] = '### BEGIN   TESTS\nassert answer() == 42\n### END TESTS'
        solutions, tests = flow._solution_cells(snapshot)
        self.assertEqual([cell['index'] for cell in solutions], [0])
        self.assertEqual(flow._test_summary(snapshot, tests)['passed'], [1])
        snapshot['cells'][1]['outputs'] = [{'type': 'error', 'ename': 'AssertionError', 'text': 'TEST PASSED!'}]
        self.assertEqual(flow._test_summary(snapshot, tests)['errored'], [1])

    def test_server_match_rejects_stale_or_missing_save(self):
        snapshot = notebook_snapshot()
        server = {'server': True, 'cells': [{'index': c['index'], 'type': c['type'], 'source': c['source']}
                                            for c in snapshot['cells']]}
        self.assertTrue(flow._saved_sources_match(snapshot, server))
        server['cells'][0]['source'] = 'old draft'
        self.assertFalse(flow._saved_sources_match(snapshot, server))
        self.assertFalse(flow._saved_sources_match(snapshot, {'error': 'offline'}))

    def test_browser_scripts_have_no_submission_or_notebook_write_action(self):
        self.assertNotIn("request.action==='submit'", flow.PROGRAMMING_PAGE_JS)
        for action in ("request.action==='edit'", "request.action==='save'", "request.action==='run'"):
            self.assertNotIn(action, flow.NOTEBOOK_JS)
        self.assertIn("cache:'no-store'", flow.NOTEBOOK_JS)

    def test_duplicate_lab_tabs_with_same_verified_notebook_are_readable(self):
        path = ITEM['url'].replace(COURSE, '/learn/deep-learning-with-pytorch')
        notebook = '/notebooks/release/FinalProject/ImageTextClassificationPyTorch.ipynb'
        url = 'https://www.coursera.org' + path + '/lab?path=' + notebook
        targets = {'first': {'url': url}, 'second': {'url': url},
                   'other': {'url': 'https://www.coursera.org' + path.replace('W3x2U', 'OTHER') +
                             '/lab?path=' + notebook}}
        self.assertEqual(flow._lab_target_ids(targets, path), ['first', 'second'])
        targets['second']['url'] = url.replace('ImageTextClassificationPyTorch', 'OtherNotebook')
        with self.assertRaisesRegex(engine.StopRun, 'different notebooks'):
            flow._lab_target_ids(targets, path)

    def test_classic_jupyter_prompt_number_is_observed(self):
        harness = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const script=JSON.parse(fs.readFileSync(0,'utf8'));
const cells=[
  {cell_type:'code',get_text:()=> 'a=1',get_input_prompt_number:()=>4,
    input_prompt_number:8,metadata:{},output_area:{outputs:[]}},
  {cell_type:'code',get_text:()=> 'b=2',get_input_prompt_number:()=>null,
    input_prompt_number:7,metadata:{},output_area:{outputs:[]}},
  {cell_type:'code',get_text:()=> 'c=3',execution_count:9,metadata:{},output_area:{outputs:[]}},
  {cell_type:'code',get_text:()=> 'd=4',metadata:{},output_area:{outputs:[]}}];
const Jupyter={notebook:{notebook_path:'release/Task.ipynb',dirty:false,
  get_cells:()=>cells,save_notebook:()=>{},kernel:{status:'idle'}}};
const location={href:'https://lab.labs.coursera.org/notebooks/release/Task.ipynb'};
(async()=>{
  const result=await vm.runInNewContext('('+script+')({action:"snapshot",max_cells:80,max_cell_chars:24000})',
    {Jupyter,location});
  assert.deepEqual(Array.from(result.cells,x=>x.execution_count),[4,7,9,null]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(['node', '-e', harness], input=json.dumps(flow.NOTEBOOK_JS),
                                text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


class FakeRunner:
    def __init__(self):
        self.tab = type('Tab', (), {'address': 'ws://127.0.0.1:9223/devtools/page/source'})()
        self.course = {'id': 'deep-learning-with-pytorch', 'url': COURSE}
        self.checkpoint = {}
        self.store = type('Store', (), {'save': lambda self: None})()
        self.events = []

    def event(self, kind, **fields):
        self.events.append((kind, fields))


class DiagnosticWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_assignment_waits_for_react_to_load_lab_control(self):
        pages = [
            {'ready': 'complete', 'heading': TITLE, 'counts': {'launch': 0, 'resume': 0}},
            {'ready': 'complete', 'heading': TITLE, 'counts': {'launch': 1, 'resume': 0},
             'page_text': "You didn't pass. To pass you need at least 70/100 points. 31/100"},
        ]
        with patch.object(flow, '_page_snapshot', new=AsyncMock(side_effect=pages)) as snapshot, \
             patch.object(flow.asyncio, 'sleep', new=AsyncMock()):
            result = await flow._wait_page(object(),
                '/learn/deep-learning-with-pytorch/programming/W3x2U/task', TITLE)
        self.assertEqual(snapshot.await_count, 2)
        self.assertEqual(flow._grade_text(result['page_text']), ('31/100', True))

    async def test_feedback_waits_for_expanded_output_and_restores_page(self):
        class FakeTab:
            def __init__(self):
                self.navigations = []
                self.observed = [
                    {'available': False, 'ready': 'pending'},
                    {'available': False, 'ready': 'pending'},
                    {'available': True, 'text': 'Score: 100/320 Test cell (Score: 0/20)'},
                ]

            async def navigate(self, url):
                self.navigations.append(url)

            async def evaluate(self, expression):
                return self.observed.pop(0)

        tab = FakeTab()
        with patch.object(flow.asyncio, 'sleep', new=AsyncMock()):
            result = await flow._read_grader_feedback(tab,
                '/learn/deep-learning-with-pytorch/programming/W3x2U/task')
        self.assertEqual(result['raw_score'], '100/320')
        self.assertEqual(result['failed_test_count'], 1)
        self.assertEqual(tab.navigations, [
            'https://www.coursera.org/learn/deep-learning-with-pytorch/programming/W3x2U/task/submission',
            'https://www.coursera.org/learn/deep-learning-with-pytorch/programming/W3x2U/task'])

    async def test_feedback_uses_my_submissions_and_expands_once(self):
        class FakeTab:
            def __init__(self):
                self.navigations = []
                self.evaluations = []
                self.results = [
                    {'available': False, 'ready': 'pending', 'expanded': True,
                     'submission_page': True},
                    {'available': False, 'ready': 'pending', 'expanded': False,
                     'submission_page': True},
                    {'available': True, 'text': 'Score: 10/20 Test cell (Score: 0/10) Wrong output',
                     'submission_page': True},
                ]

            async def navigate(self, url):
                self.navigations.append(url)

            async def evaluate(self, expression):
                self.evaluations.append(expression)
                return self.results.pop(0)

        tab = FakeTab()
        path = '/learn/deep-learning-with-pytorch/programming/W3x2U/task'
        with patch.object(flow, '_wait_page', new=AsyncMock(return_value={
                'counts': {'launch': 1, 'resume': 0, 'submissions': 1}})) as wait_page, \
             patch.object(flow, '_page_snapshot', new=AsyncMock(return_value={
                'clicked': 'submissions'})) as snapshot, \
             patch.object(flow.asyncio, 'sleep', new=AsyncMock()):
            result = await flow._read_grader_feedback(tab, path, TITLE)
        self.assertEqual(wait_page.await_args_list,
                         [call(tab, path, TITLE), call(tab, path, TITLE)])
        snapshot.assert_awaited_once_with(tab, path, TITLE, 'submissions')
        self.assertEqual(result['raw_score'], '10/20')
        self.assertTrue(result['assignment_restored'])
        self.assertEqual(result['reviewed_via'], 'Coursera View grader feedback')
        self.assertEqual([json.loads(expression.split('(')[-1][:-1])['expand']
                          for expression in tab.evaluations], [True, False, False])
        self.assertEqual(tab.navigations, ['https://www.coursera.org' + path])

    async def test_feedback_uses_canonical_page_when_submissions_tab_is_absent(self):
        class FakeTab:
            def __init__(self): self.navigations = []
            async def navigate(self, url): self.navigations.append(url)
            async def evaluate(self, expression):
                return {'available': True, 'text': 'Score: 31/100 Test cell (Score: 0/10)',
                        'submission_page': True}

        tab = FakeTab()
        path = '/learn/deep-learning-with-pytorch/programming/W3x2U/task'
        with patch.object(flow, '_wait_page', new=AsyncMock(return_value={
                'counts': {'launch': 1, 'resume': 0, 'submissions': 0}})):
            await flow._read_grader_feedback(tab, path, TITLE)
        self.assertEqual(tab.navigations, ['https://www.coursera.org' + path + '/submission',
                                           'https://www.coursera.org' + path])

    async def test_submissions_click_falls_back_after_navigation_stalls(self):
        class FakeTab:
            def __init__(self): self.navigations = []; self.probes = 0
            async def navigate(self, url): self.navigations.append(url)
            async def evaluate(self, expression):
                self.probes += 1
                if self.probes <= 4:
                    return {'available': False, 'ready': 'pending', 'on_assignment': True}
                return {'available': True, 'submission_page': True,
                        'text': 'Score: 31/100 Test cell (Score: 0/10)'}

        tab = FakeTab()
        path = '/learn/deep-learning-with-pytorch/programming/W3x2U/task'
        with patch.object(flow, '_wait_page', new=AsyncMock(return_value={
                'counts': {'launch': 1, 'resume': 0, 'submissions': 1}})), \
             patch.object(flow, '_page_snapshot', new=AsyncMock(return_value={
                'clicked': 'submissions'})), \
             patch.object(flow.asyncio, 'sleep', new=AsyncMock()):
            result = await flow._read_grader_feedback(tab, path, TITLE)
        self.assertTrue(result['assignment_restored'])
        self.assertEqual(tab.navigations, ['https://www.coursera.org' + path + '/submission',
                                           'https://www.coursera.org' + path])

    async def test_duplicate_verified_lab_tabs_reuse_one_without_new_launch(self):
        path = ITEM['url'].replace(COURSE, '/learn/deep-learning-with-pytorch')
        url = ('https://www.coursera.org' + path +
               '/lab?path=%2Fnotebooks%2Frelease%2FFinalProject%2FImageTextClassificationPyTorch.ipynb')
        targets = {'first': {'url': url}, 'second': {'url': url}}
        with patch.object(flow.lab_flow, '_page_targets', new=AsyncMock(return_value=targets)), \
             patch.object(flow, '_choose_existing_lab', new=AsyncMock(return_value=(
                 'first', 'ws://127.0.0.1:9223/devtools/page/first',
                 {'count': 2, 'verified': 2, 'display_variance': False}))) as choose, \
             patch.object(flow, '_wait_page', new=AsyncMock()) as wait_page:
            result = await flow._open_lab(object(), path, TITLE)
        self.assertEqual(result, ('first', 'ws://127.0.0.1:9223/devtools/page/first', True,
                                  {'count': 2, 'verified': 2, 'display_variance': False}))
        choose.assert_awaited_once_with(['first', 'second'], path)
        wait_page.assert_not_awaited()

    async def test_duplicate_lab_selection_requires_unambiguous_saved_state(self):
        snapshots = {'first': notebook_snapshot(), 'second': notebook_snapshot()}
        address = lambda target_id: 'ws://127.0.0.1:9223/devtools/page/' + target_id

        class FakeFrame:
            def __init__(self, url): self.target_id = url.rsplit('/', 1)[-1]
            async def connect(self): pass
            async def close(self): pass
            async def find_notebook(self, expected):
                self_test.assertTrue(expected.endswith('/lab'))
            async def evaluate(self, script, request):
                snapshot = snapshots[self.target_id]
                if request['action'] == 'snapshot': return snapshot
                return {'server': True, 'cells': [
                    {'index': cell['index'], 'type': cell['type'], 'source': cell['source']}
                    for cell in snapshot['cells']]}

        self_test = self
        path = ITEM['url'].replace(COURSE, '/learn/deep-learning-with-pytorch')
        with patch.object(flow, '_lab_address', new=AsyncMock(side_effect=address)), \
             patch.object(flow, 'FrameConnection', side_effect=FakeFrame):
            self.assertEqual(await flow._choose_existing_lab(['first', 'second'], path),
                             ('first', address('first'),
                              {'count': 2, 'verified': 2, 'display_variance': False}))
            snapshots['first']['dirty'] = True
            with self.assertRaisesRegex(engine.StopRun, 'unsaved or unreadable'):
                await flow._choose_existing_lab(['first', 'second'], path)
            snapshots['first']['dirty'] = False
            snapshots['first']['error'] = 'The notebook is still loading.'
            with self.assertRaisesRegex(engine.StopRun, 'unsaved or unreadable'):
                await flow._choose_existing_lab(['first', 'second'], path)
            snapshots['first'].pop('error')
            snapshots['first']['cells'][1]['outputs'] = []
            chosen = await flow._choose_existing_lab(['first', 'second'], path)
            self.assertEqual(chosen[0], 'first')
            self.assertTrue(chosen[2]['display_variance'])
            snapshots['second']['cells'][0]['source'] = 'def answer():\n    return 0\n'
            with self.assertRaisesRegex(engine.StopRun, 'ambiguous saved notebook state'):
                await flow._choose_existing_lab(['first', 'second'], path)

    async def test_cdp_binds_only_the_notebook_iframe_default_context(self):
        iframe = 'https://sbogzlberdio.labs.coursera.org/notebooks/release/Task.ipynb'

        class Socket:
            def __init__(self):
                self.messages = asyncio.Queue()
                self.requests = []

            async def send(self, raw):
                request = json.loads(raw)
                self.requests.append(request)
                method = request['method']
                if method == 'Runtime.enable':
                    event = {'method': 'Runtime.executionContextCreated', 'params': {'context': {
                        'id': 17, 'origin': 'https://sbogzlberdio.labs.coursera.org',
                        'auxData': {'isDefault': True, 'frameId': 'notebook-frame'}}}}
                    await self.messages.put(json.dumps(event))
                result = ({'frameTree': {'frame': {'id': 'main', 'url': COURSE}, 'childFrames': [
                    {'frame': {'id': 'notebook-frame', 'url': iframe}}]}}
                          if method == 'Page.getFrameTree' else
                          {'result': {'value': {'ok': True}}} if method == 'Runtime.evaluate' else {})
                await self.messages.put(json.dumps({'id': request['id'], 'result': result}))

            async def recv(self):
                return await self.messages.get()

        connection = flow.FrameConnection('ws://127.0.0.1:9223/devtools/page/lab')
        connection.socket = Socket()
        await connection.find_notebook()
        self.assertEqual(connection.context_id, 17)
        self.assertEqual(await connection.evaluate('(request => ({ok:true}))', {}), {'ok': True})
        evaluation = connection.socket.requests[-1]
        self.assertEqual(evaluation['params']['contextId'], 17)
        self.assertNotIn('sessionId', evaluation)

    async def test_cdp_attaches_unique_out_of_process_iframe_target(self):
        iframe = 'https://sbogzlberdio.labs.coursera.org/notebooks/release/Task.ipynb'

        class Socket:
            def __init__(self):
                self.messages = asyncio.Queue()
                self.requests = []

            async def send(self, raw):
                request = json.loads(raw)
                self.requests.append(request)
                method = request['method']
                if method == 'Runtime.enable' and request.get('sessionId') == 'iframe-session':
                    await self.messages.put(json.dumps({'method': 'Runtime.executionContextCreated',
                        'sessionId': 'iframe-session', 'params': {'context': {'id': 23,
                        'origin': 'https://sbogzlberdio.labs.coursera.org',
                        'auxData': {'isDefault': True, 'frameId': 'notebook-frame'}}}}))
                result = ({'frameTree': {'frame': {'id': 'main', 'url': COURSE}, 'childFrames': [
                    {'frame': {'id': 'notebook-frame', 'url': iframe}}]}}
                          if method == 'Page.getFrameTree' else
                          {'targetInfos': [{'type': 'iframe', 'targetId': 'target-2', 'url': iframe}]}
                          if method == 'Target.getTargets' else
                          {'sessionId': 'iframe-session'} if method == 'Target.attachToTarget' else
                          {'result': {'value': {'ok': True}}} if method == 'Runtime.evaluate' else {})
                await self.messages.put(json.dumps({'id': request['id'], 'result': result}))

            async def recv(self):
                return await self.messages.get()

        connection = flow.FrameConnection('ws://127.0.0.1:9223/devtools/page/lab')
        connection.socket = Socket()
        await connection.find_notebook()
        self.assertEqual((connection.session_id, connection.context_id), ('iframe-session', 23))
        self.assertEqual(await connection.evaluate('(request => ({ok:true}))', {}), {'ok': True})
        self.assertEqual(connection.socket.requests[-1]['sessionId'], 'iframe-session')

    async def test_new_lab_tab_waits_for_exact_url_and_notebook_frame(self):
        lab_path = ITEM['url'] + '/lab'
        notebook_path = '/notebooks/release/FinalProject/ImageTextClassificationPyTorch.ipynb'
        notebook_url = 'https://sbogzlberdio.labs.coursera.org' + notebook_path
        final_url = 'https://www.coursera.org' + lab_path.replace(
            'https://www.coursera.org', '') + '?path=%2Fnotebooks%2Frelease%2FFinalProject%2FImageTextClassificationPyTorch.ipynb'

        class Socket:
            def __init__(self):
                self.messages = asyncio.Queue()
                self.frame_probes = 0

            async def send(self, raw):
                request = json.loads(raw)
                method = request['method']
                if method == 'Runtime.enable':
                    await self.messages.put(json.dumps({'method': 'Runtime.executionContextCreated',
                        'params': {'context': {'id': 17,
                        'origin': 'https://sbogzlberdio.labs.coursera.org',
                        'auxData': {'isDefault': True, 'frameId': 'notebook-frame'}}}}))
                result = {}
                if method == 'Page.getFrameTree':
                    self.frame_probes += 1
                    if self.frame_probes == 1:
                        result = {'frameTree': {'frame': {'id': 'main', 'url': 'about:blank'}}}
                    elif self.frame_probes == 2:
                        result = {'frameTree': {'frame': {'id': 'main', 'url': ITEM['url'] + '/lab'}}}
                    else:
                        result = {'frameTree': {'frame': {'id': 'main', 'url': final_url},
                            'childFrames': [{'frame': {'id': 'notebook-frame', 'url': notebook_url}}]}}
                await self.messages.put(json.dumps({'id': request['id'], 'result': result}))

            async def recv(self):
                return await self.messages.get()

        connection = flow.FrameConnection('ws://127.0.0.1:9223/devtools/page/lab')
        connection.socket = Socket()
        with patch.object(flow, 'FRAME_PROBES', 4), \
             patch.object(flow.asyncio, 'sleep', new=AsyncMock()):
            await connection.find_notebook(ITEM['url'].replace(COURSE, '/learn/deep-learning-with-pytorch') + '/lab')
        self.assertEqual(connection.socket.frame_probes, 3)
        self.assertEqual(connection.frame_url, notebook_url)
        self.assertEqual(connection.context_id, 17)

    async def test_wrong_lab_assignment_or_notebook_path_never_binds_frame(self):
        path = ITEM['url'].replace(COURSE, '/learn/deep-learning-with-pytorch') + '/lab'
        notebook = '/notebooks/release/FinalProject/ImageTextClassificationPyTorch.ipynb'
        iframe = 'https://sbogzlberdio.labs.coursera.org' + notebook
        cases = [
            ('wrong assignment', 'https://www.coursera.org/learn/deep-learning-with-pytorch/programming/OTHER/lab?path=' + notebook, iframe),
            ('wrong notebook', 'https://www.coursera.org' + path + '?path=/notebooks/release/Other.ipynb', iframe),
        ]
        for label, top_url, frame_url in cases:
            with self.subTest(label=label):
                class Socket:
                    def __init__(self):
                        self.messages = asyncio.Queue()
                        self.frame_probes = 0

                    async def send(self, raw):
                        request = json.loads(raw)
                        if request['method'] == 'Runtime.enable':
                            await self.messages.put(json.dumps({'method': 'Runtime.executionContextCreated',
                                'params': {'context': {'id': 17,
                                'origin': 'https://sbogzlberdio.labs.coursera.org',
                                'auxData': {'isDefault': True, 'frameId': 'notebook-frame'}}}}))
                        result = {}
                        if request['method'] == 'Page.getFrameTree':
                            self.frame_probes += 1
                            result = {'frameTree': {'frame': {'id': 'main', 'url': top_url},
                                'childFrames': [{'frame': {'id': 'notebook-frame', 'url': frame_url}}]}}
                        await self.messages.put(json.dumps({'id': request['id'], 'result': result}))

                    async def recv(self):
                        return await self.messages.get()

                connection = flow.FrameConnection('ws://127.0.0.1:9223/devtools/page/lab')
                connection.socket = Socket()
                with patch.object(flow, 'FRAME_PROBES', 3), \
                     patch.object(flow.asyncio, 'sleep', new=AsyncMock()):
                    with self.assertRaises(engine.StopRun):
                        await connection.find_notebook(path)
                self.assertLessEqual(connection.socket.frame_probes, 3)
                self.assertIsNone(connection.context_id)

    async def test_inspection_records_local_report_and_stays_manual(self):
        runner = FakeRunner()
        snapshot = notebook_snapshot()
        previous_feedback = {'grade': '31/100', 'raw_score': '100/320',
                             'blank_submission': True, 'failed_test_count': 1,
                             'test_scores': [{'name': 'Image classification', 'score': '0/20'}]}
        server = {'server': True, 'cells': [{'index': c['index'], 'type': c['type'], 'source': c['source']}
                                            for c in snapshot['cells']]}

        class FakeFrame:
            frame_url = 'https://sbogzlberdio.labs.coursera.org/notebooks/release/Task.ipynb'

            async def connect(self): pass
            async def find_notebook(self, expected_lab_path):
                assert expected_lab_path.endswith('/programming/W3x2U/lab-multimodal-deep-learning-challenge-image-text-optimization-in-pytorch/lab')
            async def close(self): pass
            async def evaluate(self, script, request):
                self_assertion = request['action']
                if self_assertion == 'snapshot': return snapshot
                if self_assertion == 'server': return server
                raise AssertionError('A mutation was requested')

        with tempfile.TemporaryDirectory() as temp, \
             patch.object(engine, 'ROOT', Path(temp)), \
             patch.object(flow, '_wait_page', new=AsyncMock(return_value={
                 'page_text': "You didn't pass. To pass you need at least 70/100 points. 31/100",
                 'ready': 'complete'})), \
             patch.object(flow, '_open_lab', new=AsyncMock(return_value=(
                 'popup', 'ws://127.0.0.1:9223/devtools/page/popup', True,
                 {'count': 2, 'verified': 2, 'display_variance': False}))), \
             patch.object(flow, '_read_grader_feedback', new=AsyncMock(return_value=previous_feedback), create=True), \
             patch.object(flow, 'FrameConnection', return_value=FakeFrame()):
            reason = await flow.diagnose_programming(runner, ITEM)
            report_path = Path(temp) / 'state' / 'programming_diagnostics' / 'deep-learning-with-pytorch_programming_W3x2U.json'
            report = json.loads(report_path.read_text(encoding='utf-8'))
        self.assertIn('requires your review', reason)
        self.assertIn('Prior graded attempt: 31/100 (grader raw 100/320)', reason)
        self.assertIn('1/1 grader tests failed, placeholders found', reason)
        self.assertIn('Current notebook: 1 solution cells, 0 unfinished, saved sources confirmed', reason)
        self.assertIn('1/1 visible tests report pass', reason)
        self.assertTrue(report['server_saved'])
        self.assertEqual(report['tests']['passed'], [1])
        self.assertEqual(report['parent_grade'], '31/100')
        self.assertTrue(report['parent_failed'])
        self.assertEqual(report['previous_feedback'], previous_feedback)
        self.assertTrue(report['ready_for_owner_review'])
        self.assertRegex(reason.lower(), r'review.*submit|submit.*review')
        self.assertEqual(runner.checkpoint['programming_diagnostics'][ITEM['key']]['tests']['passed'], [1])
        self.assertEqual([name for name, _ in runner.events], ['programming_inspected'])

    async def test_unready_notebook_never_claims_owner_review_ready(self):
        cases = {
            'unsaved changes': lambda snapshot, server: snapshot.update(dirty=True),
            'stale server copy': lambda snapshot, server: server['cells'][0].update(source='older saved code'),
            'unfinished solution': lambda snapshot, server: (
                snapshot['cells'][0].update(source='def answer():\n    # YOUR CODE HERE\n    pass'),
                server['cells'][0].update(source='def answer():\n    # YOUR CODE HERE\n    pass')),
            'test error': lambda snapshot, server: snapshot['cells'][1].update(
                outputs=[{'type': 'error', 'ename': 'AssertionError', 'text': 'failed'}]),
            'test not run': lambda snapshot, server: snapshot['cells'][1].update(
                outputs=[], execution_count=None),
            'test unconfirmed': lambda snapshot, server: snapshot['cells'][1].update(
                outputs=[], execution_count=9),
        }
        for name, change in cases.items():
            with self.subTest(name=name):
                runner = FakeRunner()
                snapshot = notebook_snapshot()
                server = {'server': True, 'cells': [
                    {'index': c['index'], 'type': c['type'], 'source': c['source']}
                    for c in snapshot['cells']]}
                change(snapshot, server)

                class FakeFrame:
                    frame_url = 'https://sbogzlberdio.labs.coursera.org/notebooks/release/Task.ipynb'

                    async def connect(self): pass
                    async def find_notebook(self, expected_lab_path): pass
                    async def close(self): pass
                    async def evaluate(self, script, request):
                        if request['action'] == 'snapshot': return snapshot
                        if request['action'] == 'server': return server
                        raise AssertionError('The diagnostic tried to modify the notebook')

                with tempfile.TemporaryDirectory() as temp, \
                     patch.object(engine, 'ROOT', Path(temp)), \
                     patch.object(flow, '_wait_page', new=AsyncMock(return_value={
                         'page_text': "You didn't pass. To pass you need at least 70/100 points. 31/100",
                         'ready': 'complete'})), \
                     patch.object(flow, '_open_lab', new=AsyncMock(return_value=(
                         'popup', 'ws://127.0.0.1:9223/devtools/page/popup', True,
                         {'count': 1, 'verified': 1, 'display_variance': False}))), \
                     patch.object(flow, '_read_grader_feedback', new=AsyncMock(return_value={
                         'grade': '31/100', 'blank_submission': True}), create=True), \
                     patch.object(flow, 'FrameConnection', return_value=FakeFrame()):
                    await flow.diagnose_programming(runner, ITEM)
                    report_path = Path(temp) / 'state' / 'programming_diagnostics' / 'deep-learning-with-pytorch_programming_W3x2U.json'
                    report = json.loads(report_path.read_text(encoding='utf-8'))
                self.assertFalse(report['ready_for_owner_review'])
                self.assertNotIn('verified_complete', runner.checkpoint)

    async def test_course_queue_keeps_programming_unfinished_after_diagnosis(self):
        from tests.test_course_queue import make_runner
        previous = engine.COURSE_URL
        try:
            with tempfile.TemporaryDirectory() as temp:
                engine.configure_course(COURSE)
                runner, _ = make_runner(temp)
                runner.open_item = AsyncMock()
                runner.verified_item = AsyncMock(return_value=False)
                with patch.object(course_queue, 'diagnose_programming', new=AsyncMock(return_value='Manual Honor Code review required')):
                    await runner.process_module(1, [ITEM])
                module = runner.checkpoint['modules']['1']
                self.assertTrue(module['items'][ITEM['key']]['manual_required'])
                self.assertFalse(module['items'][ITEM['key']]['verified_complete'])
                self.assertFalse(module['verified_complete'])
                self.assertIn('Manual Honor Code review', module['items'][ITEM['key']]['reason'])
        finally:
            engine.configure_course(previous)


if __name__ == '__main__':
    unittest.main()
