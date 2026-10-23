"""Bounded classic-Jupyter execution. Generated code runs only in the lab kernel."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import time
import textwrap
import uuid
from urllib.parse import urlsplit

from .handlers import HandlerError, HandlerResult, _click_one_of, _named_control
from .notebook_regions import solution_indent

BRIDGE = Path(__file__).with_name('notebook_bridge.js').read_text(encoding='utf-8')
PLACEHOLDER = re.compile(r'YOUR CODE HERE|YOUR CODE STARTS HERE|START CODE HERE|START SOLUTION HERE|BEGIN SOLUTION|raise\s+NotImplementedError|#\s*TODO\b', re.I)
SOLUTION_REGIONS = re.compile(
    r'(?m)^[^\n]*(?:START CODE HERE|START SOLUTION HERE|BEGIN SOLUTION|YOUR CODE STARTS HERE)[^\n]*\n'
    r'(?P<body>.*?)^[^\n]*(?:END CODE HERE|END SOLUTION HERE|END SOLUTION|YOUR CODE ENDS HERE)[^\n]*$', re.S)


def needs_solution(code):
    """A preserved answer delimiter does not mean an answer is still missing."""
    regions = list(SOLUTION_REGIONS.finditer(code))
    if not regions:
        return bool(PLACEHOLDER.search(code))
    for region in regions:
        body = textwrap.dedent(region.group('body'))
        try:
            tree = ast.parse(body)
        except SyntaxError:
            return True
        if not tree.body or re.search(r'raise\s+NotImplementedError|#\s*TODO\b', body, re.I):
            return True
        for node in ast.walk(tree):
            if isinstance(node, ast.Pass):
                return True
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and node.value.value is Ellipsis:
                return True
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Constant) and node.value.value is None:
                return True
    return False


def apply_solution_regions(original, replacements):
    regions = list(SOLUTION_REGIONS.finditer(original))
    if not regions or not isinstance(replacements, list) or len(replacements) != len(regions):
        raise HandlerError('Model must supply every solution region exactly once')
    mapped = {}
    for replacement in replacements:
        if not isinstance(replacement, dict):
            raise HandlerError('Invalid solution region replacement')
        number, code = replacement.get('number'), replacement.get('code')
        if (type(number) is not int or number not in range(len(regions)) or number in mapped
                or not isinstance(code, str) or not code.strip() or len(code) > 40000):
            raise HandlerError('Invalid or duplicate solution region replacement')
        if PLACEHOLDER.search(code):
            raise HandlerError('Solution region still contains a placeholder or delimiter')
        mapped[number] = code
    result = original
    for number in reversed(range(len(regions))):
        region = regions[number]
        indent = solution_indent(original[:region.start()], region.group().splitlines()[0])
        body = textwrap.indent(textwrap.dedent(mapped[number]).strip(), indent) + '\n'
        result = result[:region.start('body')] + body + result[region.end('body'):]
    return validate_code(original, result)


def source(cell):
    value = cell.get('source', '')
    return ''.join(value) if isinstance(value, list) else value


def editable(cell):
    meta = cell.get('metadata', {})
    grader = meta.get('nbgrader', {})
    return (cell.get('cell_type') == 'code' and meta.get('editable') is not False
            and not grader.get('locked') and not grader.get('grade')
            and (grader.get('solution') is True or bool(PLACEHOLDER.search(source(cell)))))


def cell_identity(cell):
    meta = {k: v for k, v in cell.get('metadata', {}).items()
            if k not in {'trusted', 'collapsed', 'scrolled', 'execution', 'ExecuteTime'}}
    return {'id': cell.get('id'), 'cell_type': cell['cell_type'], 'metadata': meta}


def solution_manifest(document, previous=None):
    """Remember designated solution cells after their placeholder comments disappear."""
    cells = document['cells']
    if previous:
        if len(cells) != len(previous['cells']):
            raise HandlerError('Notebook structure differs from the saved solution manifest')
        for index, cell in enumerate(cells):
            saved = previous['cells'][index]
            if cell_identity(cell) != cell_identity(saved) or (
                str(index) not in previous['solutions'] and source(cell) != source(saved)
            ):
                raise HandlerError('Notebook instructor cells or permissions changed since the saved manifest')
        return {int(i): c for i, c in previous['solutions'].items()}, previous
    originals = {i: c for i, c in enumerate(cells) if editable(c)}
    manifest = {'cells': [{**cell_identity(c), 'source': source(c)} for c in cells],
                'solutions': {str(i): {**cell_identity(c), 'source': source(c)} for i, c in originals.items()}}
    return originals, manifest


def validate_code(original, code):
    if not isinstance(code, str) or not code.strip() or len(code) > 40000:
        raise HandlerError('Generated cell source is empty or too large')
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise HandlerError(f'Generated Python has a syntax error on line {exc.lineno}') from exc
    # A conservative sanity check, not a sandbox. Execution remains remote.
    forbidden = {'eval', 'exec', '__import__', 'compile', 'input', 'breakpoint'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', '')
            if name in forbidden or name in {'system', 'popen', 'rmtree', 'unlink', 'remove', 'exit', 'kill'}:
                raise HandlerError('Generated code contains an unsupported interactive or destructive operation')
    # Keep instructor scaffolding outside explicit answer delimiters byte-for-byte.
    pattern = SOLUTION_REGIONS
    regions = list(pattern.finditer(original))
    if regions:
        replacements = list(pattern.finditer(code))
        if len(replacements) != len(regions):
            raise HandlerError('Solution delimiters were changed')
        def outside(text, matches):
            parts, pos = [], 0
            for match in matches:
                block = match.group()
                parts.append(text[pos:match.start()] + block.split('\n', 1)[0] + '\n' + block.rsplit('\n', 1)[-1])
                pos = match.end()
            return parts + [text[pos:]]
        if outside(original, regions) != outside(code, replacements):
            raise HandlerError('Instructor code outside the solution region was changed')
    try:
        prior = ast.parse(original)
    except SyntaxError:
        prior = None
    if prior:
        def signatures(root):
            return {(type(n).__name__, n.name, ast.dump(n.args)) for n in ast.walk(root)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if not signatures(prior).issubset(signatures(tree)):
            raise HandlerError('An existing function signature was changed')
    return code


class NotebookExecutor:
    def __init__(self, frame, groq, store, action_id, checkpoint, *, cell_timeout=900,
                 total_timeout=3600, max_repairs=3):
        self.frame, self.groq, self.store = frame, groq, store
        self.action_id, self.checkpoint = action_id, checkpoint
        self.cell_timeout, self.total_timeout, self.max_repairs = cell_timeout, total_timeout, max_repairs
        self.path = None
        self.deadline = time.monotonic() + total_timeout
        self.directory = store.path.parent / 'labs' / str(action_id)
        self.directory.mkdir(parents=True, exist_ok=True)

    def call(self, action, **values):
        result = self.frame.evaluate(BRIDGE, {'action': action, 'path': self.path, **values})
        if result.get('error'):
            raise HandlerError(result['error'])
        return result

    def check(self):
        self.checkpoint()
        if time.monotonic() >= self.deadline:
            raise HandlerError('Lab execution time budget exhausted')

    def unchanged_snapshot(self, prior):
        current = self.call('snapshot')
        if current['signature'] != prior['signature']:
            raise HandlerError('Notebook source or metadata changed during execution')
        return current

    def artifact(self, name, value, *, exclusive=False):
        path = self.directory / name
        with path.open('x' if exclusive else 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        return path

    def patch(self, snapshot, originals, *, target=None, failure=None):
        self.check()
        context = {'notebook': [{'index': i, 'type': c['cell_type'], 'source': source(c)}
                                for i, c in enumerate(snapshot['document']['cells'])],
                   'editable_cells': sorted(originals), 'target': target, 'failure': failure,
                   'grader_feedback': self.store.get_meta(f'feedback:{self.action_id}') or ''}
        context['solution_regions'] = {
            str(i): [{'number': n, 'source': region.group('body')} for n, region in enumerate(regions)]
            for i in originals
            if (regions := list(SOLUTION_REGIONS.finditer(source(snapshot['document']['cells'][i]))))}
        if len(json.dumps(context)) > 120000:
            raise HandlerError('Notebook context exceeds the supported Groq request size')
        # The execution budget bounds notebook work. Provider generation,
        # Retry-After and network backoff have their own limits and checkpoints.
        # Charging that wait here discards a valid response before it can be saved.
        request_started = time.monotonic()
        try:
            result = self.groq.notebook_patch(context)
        finally:
            self.deadline += max(0, time.monotonic() - request_started)
        index = result.get('index')
        if type(index) is not int or index not in originals or (target is not None and index != target):
            raise HandlerError('Model selected a protected or unrelated cell')
        proposed = result.get('code')
        if 'regions' in result:
            proposed = apply_solution_regions(source(snapshot['document']['cells'][index]), result['regions'])
        code = validate_code(source(originals[index]), proposed)
        self.check()
        self.call('edit', signature=snapshot['signature'], index=index, code=code)
        self.call('save')
        self.store.record_event('INFO', 'LAB_CELL_UPDATED', f'Lab solution cell {index + 1} updated and saved')
        return self.call('snapshot')

    def run_cell(self, snapshot, index):
        token = uuid.uuid4().hex
        key = self.execution_key
        self.store.set_meta(key, {'status': 'starting', 'path': self.path, 'index': index, 'token': token})
        self.call('run', signature=snapshot['signature'], index=index, token=token)
        deadline = min(self.deadline, time.monotonic() + self.cell_timeout)
        try:
            while time.monotonic() < deadline:
                self.check()
                result = self.call('poll', token=token)
                if result.get('reply') and result.get('idle') and result.get('kernel') == 'idle':
                    self.store.set_meta(key, {'status': 'finished', 'path': self.path, 'index': index})
                    return result
                self.frame.page.wait_for_timeout(500)
            raise HandlerError(f'Lab cell {index + 1} exceeded its execution time limit')
        except BaseException:
            # Interrupt only our own tracked execution; never an unrelated busy kernel.
            try:
                self.call('interrupt', token=token)
            except Exception:
                pass
            self.store.set_meta(key, {'status': 'uncertain', 'path': self.path, 'index': index, 'token': token})
            raise

    def ready_snapshot(self, timeout=60):
        snapshot = self.call('snapshot')
        deadline = min(self.deadline, time.monotonic() + timeout)
        announced = False
        while snapshot['kernel'] != 'idle':
            self.check()
            if time.monotonic() >= deadline:
                raise HandlerError('Lab kernel did not become idle within readiness timeout; no execution started')
            if not announced:
                self.store.record_event('INFO', 'LAB_KERNEL_WAIT', 'Waiting for kernel readiness without running or interrupting cells')
                announced = True
            self.frame.page.wait_for_timeout(500)
            current = self.call('snapshot')
            if current['path'] != snapshot['path'] or current['signature'] != snapshot['signature']:
                raise HandlerError('Notebook changed while waiting for kernel readiness')
            snapshot = current
        return snapshot

    def execute(self):
        self.check()
        snapshot = self.ready_snapshot()
        self.path = snapshot['path']
        action = self.store.get_action(self.action_id) or {}
        identity = str(action.get('course_id', self.action_id)) + ':' + self.path
        self.execution_key = 'lab_execution:' + hashlib.sha256(identity.encode()).hexdigest()
        if snapshot['kernel'] != 'idle':
            raise HandlerError('Lab kernel is not idle; existing work will not be executed twice')
        language = snapshot['document'].get('metadata', {}).get('language_info', {}).get('name', '')
        if language.lower() != 'python':
            raise HandlerError('Only Python classic Jupyter labs are currently supported')
        pending = self.store.get_meta(self.execution_key)
        if pending and pending.get('status') != 'finished':
            raise HandlerError('A previous lab execution is unresolved; inspect its saved report')
        original = snapshot['document']
        manifest_key = self.execution_key.replace('lab_execution:', 'lab_manifest:', 1)
        originals, manifest = solution_manifest(original, self.store.get_meta(manifest_key))
        if not originals:
            raise HandlerError('Notebook has no explicitly editable solution cells')
        self.artifact('original-' + uuid.uuid4().hex + '.ipynb', original, exclusive=True)
        self.store.set_meta(manifest_key, manifest)
        self.store.record_event('INFO', 'LAB_BACKUP', 'Original notebook backed up before editing')
        for index, cell in originals.items():
            if needs_solution(source(snapshot['document']['cells'][index])):
                snapshot = self.patch(snapshot, originals, target=index)
        for attempt in range(self.max_repairs + 1):
            self.check()
            self.call('restart', signature=snapshot['signature'])
            ready_deadline = min(self.deadline, time.monotonic() + 60)
            while time.monotonic() < ready_deadline:
                self.check()
                snapshot = self.unchanged_snapshot(snapshot)
                if snapshot['kernel'] == 'idle':
                    break
                self.frame.page.wait_for_timeout(500)
            else:
                raise HandlerError('Lab kernel did not become ready after restart')
            failure = None
            results = []
            for index, cell in enumerate(snapshot['document']['cells']):
                if cell['cell_type'] != 'code' or not source(cell).strip():
                    continue
                result = self.run_cell(snapshot, index)
                results.append({'index': index, **result})
                self.artifact('execution.json', results)
                if result['reply'].get('status') != 'ok':
                    failure = {'index': index, 'reply': result['reply'], 'outputs': result.get('outputs', [])}
                    break
                snapshot = self.unchanged_snapshot(snapshot)
            self.call('save')
            snapshot = self.unchanged_snapshot(snapshot)
            self.artifact('working.ipynb', snapshot['document'])
            if failure is None:
                report = {'status': 'executed', 'path': self.path, 'cells_run': len(results), 'repairs': attempt,
                          'source_sha256': hashlib.sha256(snapshot['signature'].encode()).hexdigest()}
                self.store.set_meta(f'lab_report:{self.action_id}', report)
                self.store.record_event('INFO', 'LAB_EXECUTED', f'Executed {len(results)} code cells; saved notebook verified')
                return report
            self.store.set_meta(f'lab_report:{self.action_id}', {'status': 'failed', 'failure': failure})
            if attempt == self.max_repairs:
                raise HandlerError('Lab repair budget exhausted; notebook and execution feedback saved')
            self.store.record_event('WARNING', 'LAB_REPAIR', f'Repair {attempt + 1}: error in cell {failure["index"] + 1}')
            snapshot = self.patch(snapshot, originals, failure=failure)


def find_notebook(pages):
    matches = []
    for page in pages:
        if page.is_closed():
            continue
        for frame in page.frames:
            try:
                if frame.evaluate('() => !!(globalThis.Jupyter?.notebook?._fully_loaded && Jupyter.notebook.kernel?.is_connected())'):
                    matches.append((page, frame))
            except Exception:
                continue
    if len(matches) > 1:
        raise HandlerError('Multiple notebook editors opened; unable to select the assignment safely')
    return matches[0] if matches else None


def wait_for_course_mark(page, expected_url, checkpoint, *, checks=24, interval_ms=5000):
    from .outline import scan_outline, OutlineError
    for attempt in range(checks):
        checkpoint()
        try:
            matches = [item for item in scan_outline(page).items
                       if item.url and urlsplit(item.url).path.rstrip('/') == urlsplit(expected_url).path.rstrip('/')]
            if len(matches) == 1 and matches[0].completed:
                return
        except OutlineError:
            pass  # The course UI may still be rendering after returning from the lab.
        if attempt + 1 < checks:
            for delay in range(0, interval_ms, 500):
                checkpoint()
                page.wait_for_timeout(min(500, interval_ms - delay))
            page.reload(wait_until='domcontentloaded', timeout=30000)
    raise HandlerError('Lab submitted but Coursera has not confirmed completion; no automatic resubmission')


def wait_for_notebook(page, checkpoint, store, before, origin, assignment_path):
    # A slow React assignment page may expose Launch lab well after two seconds.
    # Do not wait for an editor unless launch actually happened or /lab is open.
    launched = origin.path.rstrip('/').endswith('/lab')
    deadline = time.monotonic() + (120 if launched else 30)
    while time.monotonic() < deadline:
        checkpoint()
        candidates = [page] + [p for p in page.context.pages if p is not page and (
            p not in before or (urlsplit(p.url).netloc == origin.netloc
                                and urlsplit(p.url).path == assignment_path + '/lab'))]
        match = find_notebook(candidates)
        if match:
            store.record_event('INFO', 'LAB_EDITOR_READY', 'Classic Jupyter editor and connected kernel detected')
            return match
        if not launched:
            controls = [_named_control(page, name) for name in ('Launch lab', 'Resume lab')]
            count = sum(control.count() for control in controls)
            if count > 1:
                raise HandlerError('Multiple lab launch controls; assignment selection is ambiguous')
            if count == 1:
                control = next(control for control in controls if control.count())
                if control.is_enabled():
                    checkpoint()
                    control.click(timeout=10000)
                    launched = True
                    deadline = time.monotonic() + 120
                    store.record_event('INFO', 'LAB_LAUNCHED', 'Launch lab clicked once; waiting for Jupyter and kernel')
        page.wait_for_timeout(500)
    if not launched:
        raise HandlerError('Lab launch control did not become available within 30 seconds; editor was not opened')
    raise HandlerError('Lab launched but no supported classic Jupyter editor with connected kernel loaded within 120 seconds')


def handle_programming_lab(page, checkpoint, *, groq, store, action_id):
    if groq is None or store is None or action_id is None:
        raise HandlerError('Lab executor requires Groq and durable action state')
    if store.get_meta(f'submission:{action_id}') in {'submitting', 'submitted'}:
        raise HandlerError('Lab submission is awaiting reconciliation; will not submit again')
    store.set_meta(f'submission:{action_id}', 'not_submitted')
    checkpoint()
    before = set(page.context.pages)
    return_url = page.url
    origin = urlsplit(page.url)
    assignment_path = re.sub(r'/(?:lab|submission)/?$', '', origin.path).rstrip('/')
    if origin.path.rstrip('/').endswith('/lab'):
        return_url = origin._replace(path=assignment_path, query='', fragment='').geturl()
    match = wait_for_notebook(page, checkpoint, store, before, origin, assignment_path)
    lab, frame = match
    executor = NotebookExecutor(frame, groq, store, action_id, checkpoint)
    report = executor.execute()
    checkpoint()
    current = executor.call('snapshot')
    if hashlib.sha256(current['signature'].encode()).hexdigest() != report['source_sha256']:
        raise HandlerError('Notebook changed after validation; submission cancelled')
    executor.call('save', signature=current['signature'])
    # Submission is distinct from local execution success. The runner checks the
    # actual Coursera outline afterward and retains ambiguous submission intent.
    scopes = [lab] if frame == lab.main_frame else [lab, frame]
    text = '\n'.join(scope.locator('body').inner_text(timeout=10000) for scope in scopes)
    if re.search(r'(?:work is your own|work is my own|I.*understand and agree)', text, re.I):
        raise HandlerError('Notebook prepared; personal authorship confirmation requires the learner')
    buttons = [scope.get_by_role('button', name=re.compile(r'^Submit assignment$', re.I)).locator('visible=true') for scope in scopes]
    if sum(button.count() for button in buttons) != 1:
        raise HandlerError('Notebook prepared; no unique enabled Submit assignment control')
    button = next(button for button in buttons if button.count())
    if not button.is_enabled():
        raise HandlerError('Notebook prepared; no unique enabled Submit assignment control')
    store.set_meta(f'submission:{action_id}', 'submitting')
    button.click(timeout=10000)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        checkpoint()
        text = '\n'.join(scope.locator('body').inner_text(timeout=10000) for scope in scopes)
        if re.search(r'(?:work is your own|work is my own|I.*understand and agree)', text, re.I):
            raise HandlerError('Submission dialog requires personal authorship confirmation')
        if re.search(r'(?:successfully submitted|submission received|assignment submitted|programming workspace submission succeeded)', text, re.I):
            store.set_meta(f'submission:{action_id}', 'submitted')
            page.bring_to_front()
            page.goto(return_url, wait_until='domcontentloaded', timeout=30000)
            action = store.get_action(action_id)
            if action and (action.get('payload') or {}).get('url'):
                store.record_event('INFO', 'LAB_GRADING_WAIT', 'Lab submitted; waiting for the Coursera completion mark')
                wait_for_course_mark(page, action['payload']['url'], checkpoint)
                store.set_meta(f'submission:{action_id}', 'passed')
            return HandlerResult('Notebook executed, saved and submitted; awaiting Coursera outline verification')
        lab.wait_for_timeout(500)
    raise HandlerError('Lab submission result is unclear; intent saved without a duplicate submission')
