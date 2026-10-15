"""Read-only diagnosis for graded Coursera classic-Jupyter assignments.

An AI-authored notebook cannot be auto-submitted through Coursera's personal
Honor Code attestation. This module opens the lab and records enough evidence
for a human handoff; it never edits cells, checks agreements, submits, or marks
an item complete. The queue keeps the material manual_required until Coursera
itself displays a persisted passing completion mark.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

import coursera_start as engine
import lab_flow


PAGE_PROBES = 20
FRAME_PROBES = 40
POLL_SECONDS = 1.5
MAX_CELLS = 80
MAX_CELL_CHARS = 24000
MAX_NOTEBOOK_CHARS = 360000
MAX_SOLUTION_CELLS = 12
MAX_TEST_CELLS = 30


PROGRAMMING_PAGE_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = element => {
    if (!element || !element.getClientRects().length) return false;
    for (let node=element; node; node=node.parentElement) {
      const style=getComputedStyle(node);
      if (node.hidden || node.inert || node.getAttribute('aria-hidden')==='true' ||
          style.display==='none' || ['hidden','collapse'].includes(style.visibility)) return false;
    }
    return true;
  };
  const url=new URL(location.href);
  if (url.protocol!=='https:' || !['coursera.org','www.coursera.org'].includes(url.hostname) ||
      url.username || url.password || (url.port && url.port!=='443') ||
      ![request.path,request.path+'/submission'].includes(url.pathname.replace(/\/$/,'')))
    return {error:'The selected tab is not the expected Programming Assignment.',url:location.href};
  const roots=[...document.querySelectorAll('main,[role="main"]')].filter(visible);
  const main=roots.find(element => !roots.some(other => other!==element && other.contains(element))) || document.body;
  const headings=[...main.querySelectorAll('h1')].filter(visible).map(element=>clean(element.innerText));
  // Instructions may contain their own h1 headings. Only the assignment
  // title must be unique; other section headings do not change its identity.
  if (headings.filter(value=>value===request.heading).length!==1)
    return {error:'The Programming Assignment heading is missing or ambiguous.',url:location.href,
      ready:document.readyState,headings};
  const controls=[...main.querySelectorAll('button,a[href],[role="button"]')].filter(visible)
    .map(element=>({element,label:clean(element.innerText || element.getAttribute('aria-label')),
      disabled:!!element.disabled || element.getAttribute('aria-disabled')==='true'}));
  const labels=controls.map(control=>({label:control.label,disabled:control.disabled}));
  const find=label=>controls.filter(control=>new RegExp('^'+label+'$','i').test(control.label));
  const counts={launch:find('Launch lab').length,resume:find('Resume lab').length,
    submit:find('Submit assignment').length};
  const pageText=clean(main.innerText).slice(0,12000);
  const result={url:location.href,ready:document.readyState,heading:request.heading,
    counts,buttons:labels.slice(0,40),page_text:pageText};
  if (!request.action || request.action==='inspect') return result;
  const selected=request.action==='launch' ? find('Launch lab') :
    request.action==='resume' ? find('Resume lab') : [];
  if (document.readyState!=='complete' || selected.length!==1 || selected[0].disabled)
    return {...result,error:'The requested Programming Assignment action is missing, ambiguous or disabled.'};
  selected[0].element.scrollIntoView({block:'center'});
  selected[0].element.click();
  const href=selected[0].element.matches('a[href]') ? selected[0].element.href :
    selected[0].element.closest('a[href]')?.href || null;
  return {...result,clicked:request.action,action_href:href};
})"""


PROGRAMMING_FEEDBACK_JS = r"""(async request => {
  const clean=value => String(value || '').replace(/\s+/g,' ').trim();
  const here=new URL(location.href);
  if (here.protocol!=='https:' || !['coursera.org','www.coursera.org'].includes(here.hostname) ||
      here.username || here.password || (here.port && here.port!=='443') ||
      here.pathname.replace(/\/$/,'')!==request.path+'/submission')
    return {error:'The Programming Assignment submission page changed.'};
  const links=[...document.querySelectorAll('a[href]')].filter(link =>
    clean(link.innerText)==='View grader feedback' && link.getClientRects().length);
  if (!links.length) {
    const buttons=[...document.querySelectorAll('button')].filter(button =>
      clean(button.innerText)==='Show grader output' && button.getClientRects().length);
    if (buttons.length>1) return {error:'Multiple grader output controls need review.'};
    if (buttons.length===1 && !buttons[0].disabled) buttons[0].click();
    // React may report document.readyState='complete' before it loads the
    // submissions or reveals the feedback link. Keep polling the page.
    return {available:false,ready:'pending'};
  }
  if (links.length!==1) return {error:'Multiple grader feedback links need review.'};
  const target=new URL(links[0].href);
  const ids=target.searchParams.getAll('id');
  if (target.origin!==here.origin || target.pathname!=='/api/rest/v1/executorruns/richfeedback' ||
      ids.length!==1 || !/^[A-Za-z0-9_-]{8,128}$/.test(ids[0]) ||
      target.searchParams.get('feedbackType')!=='HTML')
    return {error:'The grader feedback link is not a verified Coursera result.'};
  const response=await fetch(target.href,{credentials:'same-origin',cache:'no-store'});
  if (!response.ok) return {error:'Coursera did not return grader feedback.',status:response.status};
  const html=await response.text();
  if (html.length>2_000_000) return {error:'The grader feedback is too large to inspect.'};
  const doc=new DOMParser().parseFromString(html,'text/html');
  doc.querySelectorAll('script,style').forEach(node=>node.remove());
  return {available:true,text:clean(doc.body?.textContent).slice(0,60000)};
})"""


NOTEBOOK_JS = r"""(async request => {
  const nb=globalThis.Jupyter && Jupyter.notebook;
  if (!nb || typeof nb.get_cells!=='function' || typeof nb.save_notebook!=='function')
    return {error:'The lab is not a supported classic Jupyter notebook.'};
  const path=String(nb.notebook_path || '');
  if (!path || path.includes('..') || path.startsWith('/') || !path.endsWith('.ipynb'))
    return {error:'The notebook path is unreadable.'};
  const cells=nb.get_cells();
  if (!Array.isArray(cells) || !cells.length || cells.length>request.max_cells)
    return {error:'The notebook has an unexpected number of cells.',cell_count:cells?.length};
  const source=cell=>String(cell.get_text ? cell.get_text() : '');
  const status=()=>({url:location.href,path,dirty:!!nb.dirty,
    kernel_status:String(nb.kernel?.status || ''),
    cells:cells.map((cell,index)=>({index,type:String(cell.cell_type || ''),
      source:source(cell).slice(0,request.max_cell_chars+1),
      metadata:{nbgrader:{solution:!!cell.metadata?.nbgrader?.solution,
        locked:!!cell.metadata?.nbgrader?.locked,grade:!!cell.metadata?.nbgrader?.grade}},
      execution_count:cell.execution_count ?? null,
      outputs:(cell.output_area?.outputs || []).slice(0,10).map(output=>({
        type:String(output.output_type || ''),ename:String(output.ename || ''),
        text:String(output.text || output.data?.['text/plain'] || '').slice(0,3000)}))}))});
  if (request.action==='snapshot') return status();
  if (request.action==='server') {
    const encoded=path.split('/').map(encodeURIComponent).join('/');
    const response=await fetch('/api/contents/'+encoded+'?content=1&nonce='+Date.now(),
      {cache:'no-store',credentials:'same-origin'});
    if (!response.ok) return {error:'The notebook server did not confirm its saved contents.',status:response.status};
    const result=await response.json();
    if (result?.type!=='notebook' || !Array.isArray(result.content?.cells))
      return {error:'The notebook server returned an unexpected document.'};
    if (!result.content.cells.length || result.content.cells.length>request.max_cells)
      return {error:'The saved notebook has an unexpected number of cells.'};
    if (result.content.cells.some(cell =>
      (Array.isArray(cell.source)?cell.source.join(''):String(cell.source || '')).length>request.max_cell_chars))
      return {error:'A saved notebook cell is too large to compare.'};
    return {server:true,path,cells:result.content.cells.map((cell,index)=>({
      index,type:cell.cell_type,source:Array.isArray(cell.source)?cell.source.join(''):String(cell.source || '')}))};
  }
  return {error:'Unknown notebook action.'};
})"""


def _path_and_heading(item, course_url):
    course = urlsplit(course_url)
    lesson = urlsplit(item.get('url', ''))
    if (course.scheme != 'https' or course.hostname not in {'coursera.org', 'www.coursera.org'} or
            course.username or course.password or course.port not in {None, 443} or
            not re.fullmatch(r'/learn/[A-Za-z0-9][A-Za-z0-9_-]*', course.path.rstrip('/')) or
            course.query or course.fragment):
        raise engine.StopRun('The configured Coursera course URL is invalid for a Programming Assignment.')
    path = lesson.path.rstrip('/')
    match = re.fullmatch(re.escape(course.path.rstrip('/')) +
                         r'/programming/([A-Za-z0-9_-]+)(?:/[A-Za-z0-9_-]+)?', path)
    if (lesson.scheme != 'https' or lesson.hostname not in {'coursera.org', 'www.coursera.org'} or
            lesson.username or lesson.password or lesson.port not in {None, 443} or
            lesson.query or lesson.fragment or not match or item.get('key') != 'programming:' + match[1]):
        raise engine.StopRun('The Programming Assignment identity does not match the configured course.')
    heading = (item.get('aria_label') or item.get('title') or '').split(' Programming Assignment')[0].strip()
    heading = re.sub(r'(?:,\s*(?:Failed|Graded|Completed|Passed|Not started|In progress))+$',
                     '', heading, flags=re.I).strip()
    if not heading:
        raise engine.StopRun('The Programming Assignment has no trusted heading in the course roster.')
    return path, heading


def _notebook_frame_url(url):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (parsed.scheme == 'https' and bool(parsed.hostname) and
            bool(re.fullmatch(r'[a-z0-9-]+\.labs\.coursera\.org', parsed.hostname)) and
            not parsed.username and not parsed.password and port in {None, 443} and
            parsed.path.startswith('/notebooks/') and parsed.path.endswith('.ipynb'))


def _flatten_frames(tree):
    rows = []
    if isinstance(tree, dict):
        frame = tree.get('frame', {})
        if isinstance(frame, dict):
            rows.append(frame)
        for child in tree.get('childFrames', []) or []:
            rows.extend(_flatten_frames(child))
    return rows


def _solution_cells(snapshot):
    cells = snapshot.get('cells')
    if not isinstance(cells, list) or not 1 <= len(cells) <= MAX_CELLS:
        raise engine.StopRun('The Jupyter notebook cell list is incomplete or too large.')
    total = 0
    solutions, tests = [], []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict) or cell.get('index') != index or cell.get('type') not in {'code', 'markdown', 'raw'}:
            raise engine.StopRun('The Jupyter notebook cell identity changed.')
        source = cell.get('source')
        if not isinstance(source, str) or len(source) > MAX_CELL_CHARS:
            raise engine.StopRun('A Jupyter notebook cell is unreadable or too large.')
        total += len(source)
        metadata = cell.get('metadata', {}).get('nbgrader', {})
        if cell['type'] == 'code' and metadata.get('solution') and not metadata.get('locked') and not metadata.get('grade'):
            solutions.append(cell)
        if cell['type'] == 'code' and metadata.get('locked') and re.search(r'###\s*BEGIN\s+TESTS', source):
            tests.append(cell)
    if total > MAX_NOTEBOOK_CHARS or not 1 <= len(solutions) <= MAX_SOLUTION_CELLS or not 1 <= len(tests) <= MAX_TEST_CELLS:
        raise engine.StopRun('The notebook has no bounded nbgrader solution/test set for automatic verification.')
    return solutions, tests


def _unfinished(source):
    return bool(re.search(r'#\s*(?:YOUR CODE HERE|TODO)|raise\s+NotImplementedError|\bpass\s*(?:#.*)?$', source, re.I | re.M))


class FrameConnection:
    """A second CDP socket keeps Runtime context events the ordinary tab drops."""

    def __init__(self, address):
        engine.ChromeTab(address)  # Reuse the existing localhost/port validation.
        self.address = address
        self.socket = None
        self.sequence = 0
        self.context_events = []
        self.session_id = None
        self.context_id = None
        self.frame_url = None

    async def connect(self):
        from websockets.asyncio.client import connect
        self.socket = await connect(self.address, proxy=None, max_size=8 * 1024 * 1024,
                                    open_timeout=10, close_timeout=3)

    async def close(self):
        if self.socket:
            await self.socket.close()

    def _remember(self, message):
        if message.get('method') == 'Runtime.executionContextCreated':
            context = message.get('params', {}).get('context')
            if isinstance(context, dict):
                self.context_events.append((message.get('sessionId'), context))
                self.context_events = self.context_events[-100:]

    async def call(self, method, params=None, session_id=None):
        self.sequence += 1
        identity = self.sequence
        request = {'id': identity, 'method': method, 'params': params or {}}
        if session_id:
            request['sessionId'] = session_id
        async with asyncio.timeout(25):
            await self.socket.send(json.dumps(request))
            while True:
                message = json.loads(await self.socket.recv())
                self._remember(message)
                if message.get('id') != identity:
                    continue
                if message.get('error'):
                    raise engine.StopRun('Chrome could not inspect the Jupyter frame through CDP.')
                return message.get('result', {})

    async def _next_context_event(self, timeout=1):
        try:
            async with asyncio.timeout(timeout):
                self._remember(json.loads(await self.socket.recv()))
        except TimeoutError:
            pass

    def _context(self, frame_id, frame_url, session_id=None):
        origin = urlsplit(frame_url).scheme + '://' + urlsplit(frame_url).netloc
        matches = [(context.get('id'), context) for session, context in self.context_events
                   if session == session_id and context.get('auxData', {}).get('isDefault') is True
                   and (context.get('auxData', {}).get('frameId') == frame_id or
                        (session_id and context.get('origin') == origin))]
        ids = {identity for identity, _ in matches if type(identity) is int}
        return next(iter(ids)) if len(ids) == 1 else None

    async def find_notebook(self, expected_lab_path=None):
        """Bind one default Runtime context to one labs.coursera.org notebook iframe."""
        await self.call('Page.enable')
        await self.call('Runtime.enable')
        attached = set()
        for _ in range(FRAME_PROBES):
            tree = await self.call('Page.getFrameTree')
            top_url = urlsplit(tree.get('frameTree', {}).get('frame', {}).get('url', ''))
            if expected_lab_path is not None:
                paths = parse_qs(top_url.query).get('path', [])
                if (top_url.scheme != 'https' or top_url.hostname not in {'coursera.org', 'www.coursera.org'}
                        or top_url.path.rstrip('/') != expected_lab_path or len(paths) != 1):
                    raise engine.StopRun('The launched lab page does not match the selected assignment and notebook path.')
            frames = [frame for frame in _flatten_frames(tree.get('frameTree', {}))
                      if _notebook_frame_url(frame.get('url'))]
            if len(frames) > 1:
                raise engine.StopRun('The lab has multiple Jupyter notebook iframes; manual review is required.')
            if len(frames) == 1:
                frame = frames[0]
                frame_id, frame_url = frame.get('id'), frame.get('url')
                if expected_lab_path is not None and urlsplit(frame_url).path != paths[0]:
                    raise engine.StopRun('The lab iframe notebook differs from the Coursera launch path.')
                if isinstance(frame_id, str) and frame_id:
                    context_id = self._context(frame_id, frame_url)
                    if context_id is not None:
                        self.context_id, self.frame_url = context_id, frame_url
                        return
                    targets = await self.call('Target.getTargets')
                    matches = [row for row in targets.get('targetInfos', [])
                               if isinstance(row, dict) and row.get('type') == 'iframe' and
                               row.get('url') == frame_url and isinstance(row.get('targetId'), str)]
                    if len(matches) > 1:
                        raise engine.StopRun('The lab has ambiguous Jupyter iframe targets.')
                    if len(matches) == 1 and matches[0]['targetId'] not in attached:
                        attached.add(matches[0]['targetId'])
                        result = await self.call('Target.attachToTarget',
                                                 {'targetId': matches[0]['targetId'], 'flatten': True})
                        session_id = result.get('sessionId')
                        if not isinstance(session_id, str) or not session_id:
                            raise engine.StopRun('Chrome could not attach to the Jupyter iframe target.')
                        await self.call('Runtime.enable', session_id=session_id)
                        self.session_id = session_id
                    if self.session_id:
                        context_id = self._context(frame_id, frame_url, self.session_id)
                        if context_id is not None:
                            self.context_id, self.frame_url = context_id, frame_url
                            return
            await self._next_context_event(timeout=0.25)
            await asyncio.sleep(POLL_SECONDS)
        raise engine.StopRun('The classic Jupyter iframe did not load with a unique Runtime context.')

    async def evaluate(self, script, request):
        if self.context_id is None or not _notebook_frame_url(self.frame_url):
            raise engine.StopRun('The Jupyter frame was not verified before inspection.')
        expression = script + '(' + json.dumps(request, ensure_ascii=False) + ')'
        response = await self.call('Runtime.evaluate',
                                   {'expression': expression, 'contextId': self.context_id,
                                    'returnByValue': True, 'awaitPromise': True, 'userGesture': False},
                                   session_id=self.session_id)
        if response.get('exceptionDetails'):
            raise engine.StopRun('The Jupyter notebook rejected a browser inspection action.')
        value = response.get('result', {}).get('value')
        if not isinstance(value, dict):
            raise engine.StopRun('The Jupyter notebook returned an unreadable response.')
        return value


async def _page_snapshot(tab, path, heading, action='inspect'):
    value = await tab.evaluate(PROGRAMMING_PAGE_JS + '(' + json.dumps({'path': path, 'heading': heading,
                                                                      'action': action}) + ')')
    if not isinstance(value, dict):
        raise engine.StopRun('The Programming Assignment page returned an unreadable snapshot.')
    return value


async def _wait_page(tab, path, heading):
    last = {}
    for _ in range(PAGE_PROBES):
        last = await _page_snapshot(tab, path, heading)
        if last.get('error') == 'The selected tab is not the expected Programming Assignment.':
            raise engine.StopRun(last['error'])
        controls = last.get('counts') or {}
        lab_controls = controls.get('launch', 0) + controls.get('resume', 0)
        if (not last.get('error') and last.get('ready') == 'complete' and
                last.get('heading') == heading and lab_controls == 1):
            return last
        await asyncio.sleep(POLL_SECONDS)
    raise engine.StopRun('The Programming Assignment page did not show its expected heading and controls.')


def _lab_target_ids(targets, path):
    rows = []
    for target_id, info in targets.items():
        address = urlsplit(info.get('url', ''))
        if (address.scheme == 'https' and address.hostname in {'coursera.org', 'www.coursera.org'}
                and address.path.rstrip('/') == path + '/lab'):
            rows.append(target_id)
    if len(rows) > 1:
        raise engine.StopRun('Multiple copies of this Programming Assignment lab are open.')
    return rows


async def _lab_address(target_id):
    pages = engine.local_json(engine.CDP_URL + '/json/list')
    matches = [row for row in pages if row.get('type') == 'page' and row.get('id') == target_id
               and isinstance(row.get('webSocketDebuggerUrl'), str)]
    if len(matches) != 1:
        raise engine.StopRun('The launched lab tab has no unique Chrome debugging target.')
    return matches[0]['webSocketDebuggerUrl']


async def _open_lab(tab, path, heading):
    targets = await lab_flow._page_targets(tab)
    existing = _lab_target_ids(targets, path)
    if existing:
        return existing[0], await _lab_address(existing[0]), True
    page = await _wait_page(tab, path, heading)
    options = [(kind, page['counts'].get(kind, 0)) for kind in ('resume', 'launch')]
    available = [kind for kind, count in options if count == 1]
    if len(available) != 1 or any(count > 1 for _, count in options):
        raise engine.StopRun('The Programming Assignment has no unique Launch lab or Resume lab action.')
    source_id = lab_flow._source_target(tab, targets, path)
    launch = await _page_snapshot(tab, path, heading, available[0])
    if launch.get('clicked') != available[0]:
        raise engine.StopRun('The Programming Assignment lab did not open from its expected control.')
    mode, target_id, _ = await lab_flow._wait_launched_site(tab, path, source_id, targets,
                                                            launch.get('action_href'))
    if mode == 'popup':
        return target_id, await _lab_address(target_id), False
    return source_id, tab.address, False


def _test_summary(snapshot, tests):
    passed, errored, not_run = [], [], []
    for cell in tests:
        outputs = cell.get('outputs', [])
        if any(output.get('type') == 'error' or output.get('ename') for output in outputs):
            errored.append(cell['index'])
        elif any('TEST PASSED!' in output.get('text', '') for output in outputs):
            passed.append(cell['index'])
        elif cell.get('execution_count') is None:
            not_run.append(cell['index'])
    return {'total': len(tests), 'passed': passed, 'errored': errored, 'not_run': not_run,
            'unconfirmed': sorted({cell['index'] for cell in tests} - set(passed) - set(errored) - set(not_run))}


def _saved_sources_match(snapshot, server):
    if server.get('error') or server.get('server') is not True:
        return False
    observed = snapshot.get('cells', [])
    saved = server.get('cells', [])
    return (len(observed) == len(saved) and
            all(a.get('index') == b.get('index') and a.get('type') == b.get('type') and
                a.get('source') == b.get('source') for a, b in zip(observed, saved)))


def _grade_text(page_text):
    text = ' '.join(str(page_text or '').split())
    # The page shows the passing threshold before the actual score, e.g.
    # "To pass you need at least 70/100 points. 31/100". Never report the
    # threshold as the learner's grade.
    result = re.search(r'\bto pass you need at least\s+\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\s+points?[.!]?\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)', text, re.I)
    if result:
        grade = f'{result[1]}/{result[2]}'
    else:
        # A course outline can list grades for unrelated assignments. Without
        # a specific result sentence, multiple generic grades are ambiguous.
        matches = list(re.finditer(r'\b(?:your grade|your score|grade|score)\s*:\s*(\d+(?:\.\d+)?)\s*(%|/\s*\d+(?:\.\d+)?)', text, re.I))
        grade = matches[0][1] + re.sub(r'\s+', '', matches[0][2]) if len(matches) == 1 else None
    failed = bool(re.search(r'\bfailed\b|\b(?:did not|didn[\'’]t) pass\b', text, re.I))
    return grade, failed


def _feedback_summary(text):
    """Summarize the grader's previous submission, not the live notebook."""
    if not isinstance(text, str) or not text:
        return {'available': False}
    test_pattern = r'\bTest cell\s*\(Score:\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\)'
    tests = [{'name': 'Test cell', 'score': f'{match[1]}/{match[2]}'}
             for match in re.finditer(test_pattern, text, re.I)]
    # The HTML text is flattened, so a test-cell score can appear to be the
    # first "Score:" token. Remove those labels before reading the overall
    # grade; a page containing only test scores has no known total.
    without_tests = re.sub(test_pattern, '', text, flags=re.I)
    title = re.search(r'\bScore:\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)', without_tests, re.I)
    blank = bool(re.search(r"Student['’]s answer.{0,400}?(?:#\s*your code here|raise\s+NotImplementedError)",
                           text, re.I))
    return {'available': True,
            'raw_score': f'{title[1]}/{title[2]}' if title else None,
            'blank_submission': blank,
            'test_scores': tests[:MAX_TEST_CELLS],
            'failed_test_count': sum(float(earned) < float(possible)
                                     for earned, possible in (score['score'].split('/') for score in tests)),
            'excerpt': text[:12000]}


async def _read_grader_feedback(tab, path):
    """Read an existing result from Coursera, then restore the assignment tab."""
    assignment_url = 'https://www.coursera.org' + path
    try:
        await tab.navigate(assignment_url + '/submission')
        observed = {'available': False}
        for _ in range(40):
            observed = await tab.evaluate(PROGRAMMING_FEEDBACK_JS + '(' + json.dumps({'path': path}) + ')')
            if not isinstance(observed, dict):
                raise engine.StopRun('The grader feedback page returned an unreadable result.')
            if observed.get('error'):
                raise engine.StopRun(observed['error'])
            if observed.get('available') or observed.get('ready') == 'complete':
                break
            await asyncio.sleep(0.5)
        return _feedback_summary(observed.get('text')) if observed.get('available') else {'available': False}
    finally:
        await tab.navigate(assignment_url)


async def diagnose_programming(runner, item):
    """Inspect a graded lab and return a human-action reason, never success.

    The source course tab stays available to the queue. A newly opened lab tab
    stays open so the learner can inspect/finish the notebook. The ignored
    state report contains the full cell sources but is never sent to Groq.
    """
    path, heading = _path_and_heading(item, runner.course['url'])
    page = await _wait_page(runner.tab, path, heading)
    grade, failed = _grade_text(page.get('page_text'))
    previous_feedback = {'available': False}
    if grade or failed:
        try:
            previous_feedback = await _read_grader_feedback(runner.tab, path)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as error:
            previous_feedback = {'available': False, 'error': str(error)[:300]}
    lab_id, address, existing = await _open_lab(runner.tab, path, heading)
    frame = FrameConnection(address)
    try:
        await frame.connect()
        await frame.find_notebook(path + '/lab')
        request = {'max_cells': MAX_CELLS, 'max_cell_chars': MAX_CELL_CHARS}
        snapshot = await frame.evaluate(NOTEBOOK_JS, {**request, 'action': 'snapshot'})
        if snapshot.get('error'):
            raise engine.StopRun(snapshot['error'])
        solutions, tests = _solution_cells(snapshot)
        server = await frame.evaluate(NOTEBOOK_JS, {**request, 'action': 'server'})
        saved = _saved_sources_match(snapshot, server)
        result = _test_summary(snapshot, tests)
        unfinished = [cell['index'] for cell in solutions if _unfinished(cell['source'])]
        if snapshot.get('dirty'):
            saved = False
        ready = (saved and not unfinished and result['total'] > 0 and
                 len(result['passed']) == result['total'] and
                 not result['errored'] and not result['not_run'] and not result['unconfirmed'])
        content = json.dumps(snapshot.get('cells', []), ensure_ascii=False, sort_keys=True)
        fingerprint = hashlib.sha256(content.encode('utf-8')).hexdigest()
        report = {'recorded_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                  'course': runner.course['id'], 'item_key': item['key'], 'item_url': item['url'],
                  'parent_grade': grade, 'parent_failed': failed,
                  'notebook_host': urlsplit(frame.frame_url).hostname,
                  'notebook_path': snapshot.get('path'), 'notebook_hash': fingerprint,
                  'kernel_status': snapshot.get('kernel_status'), 'dirty': snapshot.get('dirty'),
                  'server_saved': saved, 'solution_cells': [cell['index'] for cell in solutions],
                  'unfinished_solution_cells': unfinished, 'tests': result,
                  'ready_for_owner_review': ready, 'previous_feedback': previous_feedback,
                  'cells': snapshot['cells']}
        directory = engine.ROOT / 'state' / 'programming_diagnostics'
        directory.mkdir(parents=True, exist_ok=True)
        filename = re.sub(r'[^A-Za-z0-9_-]+', '_', runner.course['id'] + '_' + item['key']) + '.json'
        location = directory / filename
        engine.save_json(location, report)
        summary = {key: report[key] for key in ('recorded_at', 'parent_grade', 'parent_failed',
                   'notebook_hash', 'dirty', 'server_saved', 'solution_cells',
                   'unfinished_solution_cells', 'tests', 'ready_for_owner_review')}
        runner.checkpoint.setdefault('programming_diagnostics', {})[item['key']] = summary
        runner.store.save()
        runner.event('programming_inspected', key=item['key'], grade=grade, failed=failed,
                     solution_count=len(solutions), unfinished_count=len(unfinished),
                     tests_passed=len(result['passed']), tests_total=result['total'],
                     server_saved=saved, lab_was_open=existing)
        status = (f"{len(result['passed'])}/{result['total']} visible tests display pass output; "
                  f"{len(unfinished)} unfinished solution cells; "
                  + ('server copy confirmed' if saved else 'server copy needs checking'))
        previous = (f" Previous submission: {grade} failed; grader feedback "
                    + ('shows unfinished answers.' if previous_feedback.get('blank_submission') else
                       'is saved in the local diagnostic.' if previous_feedback.get('available') else
                       'was unavailable.')) if failed else ''
        if ready:
            next_step = ('The current notebook is saved and its visible tests display passes; '
                         'a fresh run and the hidden grader are not confirmed. '
                         'Review it and submit the assignment personally if it is your own work; '
                         'Coursera will determine the actual grade.')
        else:
            next_step = ('The notebook still needs work or a fresh validation before personal submission. '
                         'Review its incomplete cells, test results and server copy.')
        return ('Programming Assignment requires your review before any Coursera Honor Code attestation. '
                + status + '.' + previous + ' ' + next_step + ' The lab tab remains open. '
                'Local diagnostic: ' + str(location))
    finally:
        with suppress(Exception):
            await frame.close()
        # If Launch/Resume navigated the one managed course tab, return it to
        # the known lesson. A separate lab popup remains open for the learner.
        if lab_id == urlsplit(runner.tab.address).path.rstrip('/').split('/')[-1]:
            with suppress(Exception):
                await runner.tab.navigate(item['url'])
