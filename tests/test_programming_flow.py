"""Offline checks for read-only graded Jupyter inspection and queue handoff."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


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

    async def test_inspection_records_local_report_and_stays_manual(self):
        runner = FakeRunner()
        snapshot = notebook_snapshot()
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
             patch.object(flow, '_wait_page', new=AsyncMock(return_value={'page_text': 'Grade: 31/100 Failed', 'ready': 'complete'})), \
             patch.object(flow, '_open_lab', new=AsyncMock(return_value=('popup', 'ws://127.0.0.1:9223/devtools/page/popup', True))), \
             patch.object(flow, 'FrameConnection', return_value=FakeFrame()):
            reason = await flow.diagnose_programming(runner, ITEM)
            report_path = Path(temp) / 'state' / 'programming_diagnostics' / 'deep-learning-with-pytorch_programming_W3x2U.json'
            report = json.loads(report_path.read_text(encoding='utf-8'))
        self.assertIn('requires your review', reason)
        self.assertTrue(report['server_saved'])
        self.assertEqual(report['tests']['passed'], [1])
        self.assertEqual(report['parent_grade'], '31/100')
        self.assertEqual(runner.checkpoint['programming_diagnostics'][ITEM['key']]['tests']['passed'], [1])
        self.assertEqual([name for name, _ in runner.events], ['programming_inspected'])

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
