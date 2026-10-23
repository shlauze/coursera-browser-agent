"""Regression tests, including a real local classic-Jupyter Python kernel.

The model is deterministic in these tests; no coursework or external API is used.
Optional integration dependencies: pip install -r requirements-dev.txt
"""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import uuid
from unittest.mock import Mock, patch

from v2.handlers import HandlerError, handle
from v2.notebooks import BRIDGE, NotebookExecutor, editable, validate_code, handle_programming_lab, solution_manifest, wait_for_course_mark, needs_solution, apply_solution_regions
from v2.policy import ItemAction
from v2.store import Store


class NotebookPolicyTests(unittest.TestCase):
    def test_region_assembly_preserves_scaffolding_and_all_regions(self):
        original = ('def solve(x):\n    y = x\n    # YOUR CODE STARTS HERE\n\n    # YOUR CODE ENDS HERE\n'
                    '    assert y > 0\n    # YOUR CODE STARTS HERE\n\n    # YOUR CODE ENDS HERE\n    return y\n')
        replacements = [{'number': 1, 'code': 'y += 1'}, {'number': 0, 'code': 'y *= 2'}]
        result = apply_solution_regions(original, replacements)
        self.assertIn('    assert y > 0\n', result)
        self.assertIn('    y *= 2\n    # YOUR CODE ENDS HERE', result)
        self.assertFalse(needs_solution(result))
        for invalid in (replacements[:1], [replacements[0], replacements[0]],
                        [{'number': 0, 'code': 'pass'}, {'number': 9, 'code': 'y=1'}]):
            with self.assertRaises(HandlerError):
                apply_solution_regions(original, invalid)

    def test_coursera_starts_ends_regions_are_prepared_and_protected(self):
        original = 'def solve(x):\n    # YOUR CODE STARTS HERE\n    \n    # YOUR CODE ENDS HERE\n    return result\n'
        self.assertTrue(editable({'cell_type': 'code', 'source': original}))
        self.assertTrue(needs_solution(original))
        good = original.replace('    \n', '    result = x * 2\n')
        self.assertFalse(needs_solution(good))
        self.assertEqual(validate_code(original, good), good)
        with self.assertRaises(HandlerError):
            validate_code(original, good.replace('return result', 'return 999'))

    def test_any_empty_region_triggers_preparation(self):
        one = '# YOUR CODE STARTS HERE\nx = 1\n# YOUR CODE ENDS HERE\n'
        two = '# YOUR CODE STARTS HERE\n# explanation only\n# YOUR CODE ENDS HERE\n'
        self.assertTrue(needs_solution(one + two))
        self.assertFalse(needs_solution(one + one))

    def test_placeholder_values_are_detected_in_solution_regions(self):
        for body in ('x = None', 'pass', '...', 'raise NotImplementedError'):
            self.assertTrue(needs_solution('# START CODE HERE\n' + body + '\n# END CODE HERE'))
        self.assertFalse(needs_solution('# START CODE HERE\nx = 42\n# END CODE HERE'))

    def test_delayed_course_mark_is_waited_for_without_resubmitting(self):
        url = 'https://www.coursera.org/learn/test/programming/id/lab-example'
        page = Mock()
        with patch('v2.outline.scan_outline', side_effect=[Mock(items=[Mock(url=url, completed=False)]),
                                                          Mock(items=[Mock(url=url, completed=True)])]):
            wait_for_course_mark(page, url, lambda: None, checks=2, interval_ms=0)
        page.reload.assert_called_once()
        page.get_by_role.assert_not_called()

    def test_saved_solution_cells_remain_known_after_markers_disappear(self):
        document = {'cells': [{'cell_type': 'code', 'source': '# YOUR CODE HERE'},
                              {'cell_type': 'code', 'source': 'assert x == 42'}]}
        _, manifest = solution_manifest(document)
        document['cells'][0]['source'] = 'x = 42'
        originals, _ = solution_manifest(document, manifest)
        self.assertEqual(list(originals), [0])
        self.assertEqual(originals[0]['source'], '# YOUR CODE HERE')
        document['cells'][1]['source'] = 'assert True'
        with self.assertRaises(HandlerError):
            solution_manifest(document, manifest)

    def test_locked_and_test_cells_are_never_editable(self):
        for metadata in ({'nbgrader': {'solution': True, 'locked': True}},
                         {'nbgrader': {'solution': True, 'grade': True}}, {'editable': False}):
            self.assertFalse(editable({'cell_type': 'code', 'source': '# YOUR CODE HERE', 'metadata': metadata}))
        self.assertTrue(editable({'cell_type': 'code', 'source': '# TODO: solution'}))
        self.assertFalse(editable({'cell_type': 'code', 'source': 'assert answer == 42'}))

    def test_solution_boundaries_and_signatures_are_preserved(self):
        original = 'def solve(x):\n    # START CODE HERE\n    raise NotImplementedError\n    # END CODE HERE\nassert solve(1) == 2\n'
        good = original.replace('raise NotImplementedError', 'return x + 1')
        self.assertEqual(validate_code(original, good), good)
        for bad in (good.replace('== 2', '== 3'), good.replace('solve(x)', 'solve(x,y)'),
                    good.replace('# END CODE HERE', '# done')):
            with self.assertRaises(HandlerError):
                validate_code(original, bad)

    def test_invalid_model_code_is_rejected(self):
        for code in ('', 'def broken(', 'eval("2+2")', 'import os\nos.remove("test.py")'):
            with self.assertRaises(HandlerError):
                validate_code('', code)

    def test_programming_dispatch_uses_executor(self):
        with patch('v2.notebooks.handle_programming_lab', return_value='handled') as impl:
            self.assertEqual(handle('page', ItemAction.PROGRAMMING_LAB, 'check',
                                    groq='groq', store='store', action_id=7), 'handled')
            impl.assert_called_once_with('page', 'check', groq='groq', store='store', action_id=7)


HAS_JUPYTER = all(importlib.util.find_spec(x) for x in ('nbclassic', 'ipykernel'))


@unittest.skipUnless(HAS_JUPYTER, 'Install requirements-dev.txt for real Jupyter integration tests')
class RealNotebookTests(unittest.TestCase):
    def prepared_executor(self):
        executor = Mock()
        executor.execute.return_value = {'source_sha256': hashlib.sha256(b'fixture').hexdigest()}
        executor.call.return_value = {'signature': 'fixture'}
        return executor

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        kernel_dir = cls.root / 'share' / 'jupyter' / 'kernels' / 'v2test'
        kernel_dir.mkdir(parents=True)
        (kernel_dir / 'kernel.json').write_text(json.dumps({
            'argv': [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
            'display_name': 'V2 Test Python', 'language': 'python'}), encoding='utf-8')
        cls.original = {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {
            'kernelspec': {'name': 'v2test', 'display_name': 'V2 Test Python', 'language': 'python'},
            'language_info': {'name': 'python'}}, 'cells': [
                {'id': 'instructions', 'cell_type': 'markdown', 'metadata': {}, 'source': 'Implement twice(x), returning x * 2.'},
                {'id': 'solution', 'cell_type': 'code', 'metadata': {'nbgrader': {'solution': True}},
                 'source': 'def twice(x):\n    # YOUR CODE HERE\n    raise NotImplementedError', 'outputs': [], 'execution_count': None},
                {'id': 'tests', 'cell_type': 'code', 'metadata': {'nbgrader': {'locked': True, 'grade': True}},
                 'source': 'assert twice(3) == 6, "twice must double"\nprint("local checks passed")', 'outputs': [], 'execution_count': None}]}
        (cls.root / 'lab.ipynb').write_text(json.dumps(cls.original), encoding='utf-8')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cls.token = uuid.uuid4().hex
        cls.url = f'http://127.0.0.1:{port}'
        env = dict(os.environ, JUPYTER_PATH=str(cls.root / 'share' / 'jupyter'),
                   JUPYTER_RUNTIME_DIR=str(cls.root / 'runtime'), JUPYTER_CONFIG_DIR=str(cls.root / 'config'))
        cls.log = (cls.root / 'server.log').open('w', encoding='utf-8')
        cls.server = subprocess.Popen([sys.executable, '-m', 'nbclassic', '--no-browser',
            f'--ServerApp.port={port}', '--ServerApp.port_retries=0', '--ServerApp.ip=127.0.0.1',
            f'--ServerApp.root_dir={cls.root}', f'--IdentityProvider.token={cls.token}'],
            env=env, stdout=cls.log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        cls.addClassCleanup(cls.server.terminate)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(cls.url + '/api?token=' + cls.token, timeout=1):
                    break
            except Exception:
                time.sleep(.25)
        else:
            raise RuntimeError('Test Jupyter server did not start: ' + (cls.root / 'server.log').read_text(encoding='utf-8')[-3000:])
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        # Server SIGTERM on Windows cannot reap kernels; shut them down by API.
        headers = {'Authorization': 'token ' + cls.token}
        try:
            req = urllib.request.Request(cls.url + '/api/kernels', headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                kernels = json.load(response)
            for kernel in kernels:
                urllib.request.urlopen(urllib.request.Request(cls.url + '/api/kernels/' + kernel['id'],
                    headers=headers, method='DELETE'), timeout=5).close()
        finally:
            cls.server.terminate()
            cls.server.wait(timeout=10)
            cls.log.close()
            cls.tmp.cleanup()

    def setUp(self):
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.goto(self.url + '/notebooks/lab.ipynb?token=' + self.token)
        self.page.wait_for_function('() => !!globalThis.Jupyter?.notebook?.kernel?.is_connected()', timeout=30000)
        self.frame = self.page.main_frame
        self.tmp_store = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_store.cleanup)
        self.store = Store(Path(self.tmp_store.name) / 'state.db')
        self.groq = Mock()

    def test_real_execution_repair_backup_and_protected_tests(self):
        self.groq.notebook_patch.side_effect = [
            {'index': 1, 'code': 'def twice(x):\n    return x * 3'},
            {'index': 1, 'code': 'def twice(x):\n    return x * 2'}]
        executor = NotebookExecutor(self.frame, self.groq, self.store, 1, lambda: None, cell_timeout=20, total_timeout=120)
        report = executor.execute()
        self.assertEqual(report['status'], 'executed')
        self.assertEqual(report['repairs'], 1)
        failure = self.groq.notebook_patch.call_args.args[0]['failure']
        self.assertEqual(failure['reply']['ename'], 'AssertionError')
        self.assertEqual(failure['index'], 2)
        saved = json.loads((self.root / 'lab.ipynb').read_text(encoding='utf-8'))
        self.assertEqual(''.join(saved['cells'][2]['source']), self.original['cells'][2]['source'])
        backups = list(executor.directory.glob('original-*.ipynb'))
        self.assertEqual(len(backups), 1)
        original = json.loads(backups[0].read_text(encoding='utf-8'))
        self.assertIn('NotImplementedError', original['cells'][1]['source'])
        self.assertEqual(saved['cells'][2]['execution_count'], 2)
        self.assertTrue((executor.directory / 'execution.json').exists())

    def test_four_empty_solutions_prepared_without_repair_budget_or_repeat_requests(self):
        document = json.loads(json.dumps(self.original))
        document['cells'] = []
        solutions = []
        for i in range(4):
            empty = f'def solve{i}(x):\n    # YOUR CODE STARTS HERE\n    \n    # YOUR CODE ENDS HERE\n    return result\n'
            solutions.append(empty.replace('    \n', f'    result = x + {i}\n'))
            document['cells'].append({'id': f'solution{i}', 'cell_type': 'code', 'metadata': {},
                                      'source': empty, 'outputs': [], 'execution_count': None})
        checks = '\n'.join(f'assert solve{i}(10) == {10 + i}' for i in range(4))
        document['cells'].append({'id': 'checks', 'cell_type': 'code',
                                  'metadata': {'nbgrader': {'grade': True, 'locked': True}},
                                  'source': checks, 'outputs': [], 'execution_count': None})
        (self.root / 'four.ipynb').write_text(json.dumps(document), encoding='utf-8')
        self.page.goto(self.url + '/notebooks/four.ipynb?token=' + self.token)
        self.page.wait_for_function('() => !!globalThis.Jupyter?.notebook?._fully_loaded && Jupyter.notebook.kernel?.is_connected()', timeout=30000)
        self.groq.notebook_patch.side_effect = [
            {'index': i, 'regions': [{'number': 0, 'code': f'result = x + {i}'}]} for i in range(4)]
        executor = NotebookExecutor(self.frame, self.groq, self.store, 2, lambda: None,
                                    cell_timeout=20, total_timeout=120, max_repairs=0)
        self.assertEqual(executor.execute()['repairs'], 0)
        self.assertEqual(self.groq.notebook_patch.call_count, 4)
        again = NotebookExecutor(self.frame, self.groq, self.store, 2, lambda: None,
                                 cell_timeout=20, total_timeout=120, max_repairs=0)
        self.assertEqual(again.execute()['repairs'], 0)
        self.assertEqual(self.groq.notebook_patch.call_count, 4)
        saved = json.loads((self.root / 'four.ipynb').read_text(encoding='utf-8'))
        self.assertEqual(''.join(saved['cells'][-1]['source']), checks)
        self.assertEqual(saved['cells'][-1]['execution_count'], 5)

    def test_stale_snapshot_cannot_overwrite_user_edit(self):
        snapshot = self.frame.evaluate(BRIDGE, {'action': 'snapshot'})
        self.frame.evaluate('() => Jupyter.notebook.get_cells()[1].set_text("def twice(x): return x * 2 # user edit")')
        result = self.frame.evaluate(BRIDGE, {'action': 'edit', 'path': snapshot['path'],
            'signature': snapshot['signature'], 'index': 1, 'code': 'x = 3'})
        self.assertIn('changed', result['error'])

    def test_submission_records_intent_and_does_not_double_submit(self):
        page = self.context.new_page()
        page.set_content('<button onclick="this.outerHTML=\'<p>Assignment submitted</p>\'">Submit assignment</button>')
        with patch('v2.notebooks.find_notebook', return_value=(page, self.frame)), patch('v2.notebooks.NotebookExecutor', return_value=self.prepared_executor()):
            result = handle_programming_lab(page, lambda: None, groq=self.groq, store=self.store, action_id=8)
            self.assertIn('submitted', result.message)
            self.assertEqual(self.store.get_meta('submission:8'), 'submitted')
            with self.assertRaisesRegex(HandlerError, 'reconciliation'):
                handle_programming_lab(page, lambda: None, groq=self.groq, store=self.store, action_id=8)

    def test_personal_confirmation_does_not_submit(self):
        page = self.context.new_page()
        page.set_content('<p>This work is your own.</p><button onclick="window.submitted=true">Submit assignment</button>')
        with patch('v2.notebooks.find_notebook', return_value=(page, self.frame)), patch('v2.notebooks.NotebookExecutor', return_value=self.prepared_executor()):
            with self.assertRaisesRegex(HandlerError, 'personal authorship'):
                handle_programming_lab(page, lambda: None, groq=self.groq, store=self.store, action_id=9)
        self.assertIsNone(page.evaluate('window.submitted'))

    def test_z_timeout_interrupts_owned_execution_and_prevents_replay(self):
        self.frame.evaluate('() => Jupyter.notebook.get_cells()[1].set_text("while True: pass")')
        executor = NotebookExecutor(self.frame, self.groq, self.store, 10, lambda: None, cell_timeout=.3)
        with self.assertRaisesRegex(HandlerError, 'time limit'):
            executor.execute()
        self.assertEqual(self.store.get_meta(executor.execution_key)['status'], 'uncertain')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if executor.call('snapshot')['kernel'] == 'idle':
                break
            self.page.wait_for_timeout(100)
        with self.assertRaisesRegex(HandlerError, 'unresolved'):
            executor.execute()
        self.groq.notebook_patch.assert_not_called()
