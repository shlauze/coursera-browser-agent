import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from playwright.sync_api import sync_playwright
from v2.groq_client import (GroqClient, RequestTooLarge, ResponseValidationError,
    focused_notebook_context, deduplicate_notebook_context, validate_notebook_response)
from v2.notebooks import validate_code, wait_for_submit_control, BRIDGE
from v2.handlers import HandlerError
from v2.store import Store


class RequestRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'state.db')
        self.context = {'notebook': [{'index': 1, 'type': 'code', 'source': 'x=0'}],
                        'editable_cells': [1], 'target': 1}
        self.answer = {'cannot_complete': False, 'index': 1, 'code': 'x=1'}

    def client(self, replies):
        self.payloads = []
        def transport(key, payload):
            self.payloads.append(copy.deepcopy(payload))
            return replies.pop(0)
        return GroqClient(self.store, key_loader=lambda: 'fixture', transport=transport)

    def ok(self, answer=None):
        return 200, {}, {'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(self.answer if answer is None else answer)}}]}

    def test_tuple_template_locals_do_not_pull_other_exercises(self):
        target = ('def backward(cache):\n # (inputs, params) = None\n'
                  ' # rows, cols = None\n # YOUR CODE STARTS HERE\n\n'
                  ' # YOUR CODE ENDS HERE\n return inputs, params, rows, cols')
        context = {'notebook': [
            {'index': 0, 'type': 'code', 'source': 'inputs = unrelated_training()'},
            {'index': 1, 'type': 'markdown', 'source': 'Required instructions'},
            {'index': 2, 'type': 'code', 'source': target}], 'editable_cells': [2], 'target': 2}
        result = focused_notebook_context(context)
        self.assertEqual([c['index'] for c in result['notebook']], [1, 2])
        self.assertEqual(result['notebook'][-1]['source'], target)

    def test_import_does_not_pull_later_training_and_its_dependencies(self):
        context = {'notebook': [
            {'index': 0, 'type': 'code', 'source': 'def answer(): return 1'},
            {'index': 1, 'type': 'code', 'source': 'def training(): return 42'},
            {'index': 2, 'type': 'code', 'source': 'from outputs import expected\nassert training()==expected'},
            {'index': 3, 'type': 'code', 'source': '%%javascript\nif (confirm("Clear?")) {}'}],
            'editable_cells': [0, 1], 'target': 0}
        result = focused_notebook_context(context)
        self.assertEqual(result['editable_cells'], [0])
        self.assertEqual([c['index'] for c in result['notebook']], [0, 2])
        self.assertEqual(result['notebook'][1]['source'], 'from outputs import expected')
        self.assertIn('assert training()', context['notebook'][2]['source'])

    def test_deduplication_preserves_original_cell_and_region_numbers(self):
        context = {**self.context, 'solution_regions': {'1': [{'number': 0, 'source': 'x=0'}]}}
        result = deduplicate_notebook_context(context)
        self.assertEqual(result['notebook'], context['notebook'])
        self.assertEqual(result['solution_regions']['1'], [{'number': 0}])
        self.assertEqual(context['solution_regions']['1'][0]['source'], 'x=0')

    def test_python_cell_magic_dependency_is_retained(self):
        context = {'notebook': [
            {'index': 0, 'type': 'code', 'source': '%%capture\ndef helper(x): return x+1'},
            {'index': 1, 'type': 'code', 'source': 'def answer(x): return helper(x)'}],
            'editable_cells': [1], 'target': 1}
        self.assertEqual([c['index'] for c in focused_notebook_context(context)['notebook']], [0, 1])

    def test_schema_error_changes_wire_format_but_validates_locally(self):
        client = self.client([(400, {}, {'error': {'code': 'json_validate_failed'}}), self.ok()])
        self.assertEqual(client.notebook_patch(self.context), self.answer)
        self.assertEqual(self.payloads[0]['response_format']['type'], 'json_schema')
        self.assertEqual(self.payloads[1]['response_format']['type'], 'json_object')

    def test_invalid_json_is_bounded_and_rejected(self):
        invalid = (200, {}, {'choices': [{'finish_reason': 'stop', 'message': {'content': '{bad'}}]})
        client = self.client([invalid, invalid])
        with self.assertRaises(ResponseValidationError):
            client.notebook_patch(self.context)
        self.assertEqual(len(self.payloads), 2)

    def test_unrelated_cell_in_json_fallback_is_not_accepted(self):
        bad = {**self.answer, 'index': 99}
        client = self.client([(400, {}, {'error': {'code': 'json_validate_failed'}}), self.ok(bad)])
        with self.assertRaises(ResponseValidationError):
            client.notebook_patch(self.context)
        self.assertEqual(len(self.payloads), 2)

    def test_reduction_updates_context_not_format_reminder(self):
        context = {'notebook': [{'index': 1, 'type': 'code', 'source': 'x=0'},
                               {'index': 2, 'type': 'markdown', 'source': 'unrelated prose ' * 100}], 'editable_cells': [1]}
        client = self.client([(400, {}, {'error': {'code': 'json_validate_failed'}}),
                              (413, {}, {}), self.ok()])
        client.notebook_patch(context)
        self.assertEqual(self.payloads[1]['messages'][2], self.payloads[2]['messages'][2])
        self.assertLess(len(self.payloads[2]['messages'][1]['content']),
                        len(self.payloads[1]['messages'][1]['content']))

    def test_identical_oversize_request_is_not_replayed(self):
        client = self.client([(413, {}, {'error': {'message': 'Limit 8,000 Requested 9,000'}})])
        with self.assertRaises(RequestTooLarge):
            client.notebook_patch(self.context)
        self.assertEqual(len(self.payloads), 1)
        self.assertEqual(self.store.get_meta('groq_request_size')['requested'], 9000)

    def test_region_shape_boolean_index_duplicates_and_extra_keys_rejected(self):
        ctx = {**self.context, 'solution_regions': {'1': [{'number': 0}, {'number': 1}]}}
        good = {'cannot_complete': False, 'index': 1, 'regions': [
            {'number': 0, 'code': 'x=1'}, {'number': 1, 'code': 'y=2'}]}
        self.assertEqual(validate_notebook_response(good, ctx, True), good)
        for bad in [{**good, 'index': True}, {**good, 'extra': 1},
                    {**good, 'regions': [good['regions'][0]] * 2}]:
            with self.assertRaises(ResponseValidationError):
                validate_notebook_response(bad, ctx, True)

    def test_keras_compile_allowed_but_python_compilation_and_shell_blocked(self):
        code = "model.compile(optimizer=adam, loss='categorical_crossentropy', metrics=['accuracy'])"
        self.assertEqual(validate_code(code, code), code)
        for code in ["compile('x=1','file','exec')", "builtins.compile(source='x',filename='x',mode='exec')",
                     "os.system('command')", "model.compile(optimizer=eval('x'))"]:
            with self.assertRaises(HandlerError):
                validate_code(code, code)


class SubmissionLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 958, 'height': 992})
        self.addCleanup(self.page.close)

    def test_responsive_hidden_button_is_revealed_without_clicking(self):
        self.page.set_content('''<style>@media(max-width:1023px){header{display:none}}</style>
            <header><button onclick="window.clicked=true">Submit assignment</button></header>''')
        button = wait_for_submit_control(self.page, [self.page], lambda: None, Mock(), timeout=2)
        self.assertTrue(button.is_visible())
        self.assertFalse(self.page.evaluate('!!window.clicked'))
        self.assertEqual(self.page.viewport_size['width'], 1280)

    def test_ambiguous_buttons_are_not_clicked(self):
        self.page.set_content('<button>Submit assignment</button><button>Submit assignment</button>')
        with self.assertRaisesRegex(HandlerError, 'ambiguous'):
            wait_for_submit_control(self.page, [self.page], lambda: None, Mock(), timeout=0)

    def test_disabled_and_permanently_hidden_controls_stay_blocked(self):
        for html in ['<button disabled>Submit assignment</button>',
                     '<button style="display:none">Submit assignment</button>']:
            self.page.set_content(html)
            with self.assertRaises(HandlerError):
                wait_for_submit_control(self.page, [self.page], lambda: None, Mock(), timeout=0)

    def test_stop_is_observed_before_viewport_change(self):
        self.page.set_content('<button hidden>Submit assignment</button>')
        def stop(): raise RuntimeError('stopped')
        with self.assertRaisesRegex(RuntimeError, 'stopped'):
            wait_for_submit_control(self.page, [self.page], stop, Mock(), timeout=0)
        self.assertEqual(self.page.viewport_size['width'], 958)

    def test_save_timeout_checks_server_without_repeating_write(self):
        self.page.route('**/*', lambda route: route.fulfill(body='<html></html>', content_type='text/html'))
        self.page.goto('https://notebook.test/')
        self.page.evaluate('''() => {
            const doc={cells:[{cell_type:'code',source:'x=1',metadata:{}}]};
            window.saves=0; window.reads=0;
            window.Jupyter={notebook:{notebook_path:'lab.ipynb',base_url:'/',get_cells:()=>[{}],
                toJSON:()=>doc,save_notebook:()=>{saves++;return Promise.reject(new Error('Notebook request timed out'))}}};
            window.fetch=async()=>{reads++;return new Response(JSON.stringify({type:'notebook',content:doc}),{status:200})};
        }''')
        self.assertEqual(self.page.evaluate(BRIDGE, {'action': 'save'}), {'ok': True})
        self.assertEqual(self.page.evaluate('[saves,reads]'), [1, 1])
