import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from v2.handlers import HandlerError
from v2.notebooks import NotebookExecutor, find_notebook
from v2.store import Store


class GradedProposalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'state.db')
        self.store.get_action = Mock(return_value={'course_id': 'fixture'})
        self.original = 'def solve(x):\n    # START CODE HERE\n    pass\n    # END CODE HERE\n'
        self.cell = {'cell_type': 'code', 'source': self.original}
        self.snapshot = {'signature': 'unchanged', 'document': {'cells': [self.cell]}}
        self.model = Mock()
        self.good = {'index': 0, 'regions': [{'number': 0, 'code': 'return x * 2'}]}
        self.bad = {'index': 0, 'regions': [{'number': 0, 'code': 'return ('}]}

    def executor(self, action=1):
        executor = NotebookExecutor(Mock(), self.model, self.store, action, lambda: None)
        executor.path = 'fixture.ipynb'
        executor.call = Mock(return_value=self.snapshot)
        return executor

    def test_invalid_syntax_repaired_before_first_write_with_feedback(self):
        executor = self.executor()
        self.model.notebook_patch.side_effect = [self.bad, self.good]
        executor.patch(self.snapshot, {0: self.cell}, target=0)
        feedback = self.model.notebook_patch.call_args_list[1].args[0]['proposal_validation']
        self.assertIn('syntax error', feedback['error'])
        self.assertEqual(feedback['rejected_proposal'], self.bad)
        self.assertEqual([c.args[0] for c in executor.call.call_args_list], ['edit', 'save', 'snapshot'])
        code = executor.call.call_args_list[0].kwargs['code']
        self.assertEqual(code, self.original.replace('pass', 'return x * 2'))

    def test_identical_bad_proposal_stops_and_survives_restart(self):
        self.model.notebook_patch.return_value = self.bad
        for action in (1, 2):
            executor = self.executor(action)
            with self.assertRaisesRegex(HandlerError, 'syntax repair budget exhausted'):
                executor.patch(self.snapshot, {0: self.cell}, target=0)
            executor.call.assert_not_called()
        self.assertEqual(self.model.notebook_patch.call_count, 2)

    def test_three_distinct_invalid_proposals_exhaust_budget(self):
        self.model.notebook_patch.side_effect = [
            {'index': 0, 'regions': [{'number': 0, 'code': text}]} for text in ('return (', 'return [', 'return {')]
        executor = self.executor()
        with self.assertRaisesRegex(HandlerError, 'syntax repair budget exhausted'):
            executor.patch(self.snapshot, {0: self.cell}, target=0)
        self.assertEqual(self.model.notebook_patch.call_count, 3)
        executor.call.assert_not_called()

    def test_provider_change_allows_a_new_proposal_budget(self):
        self.model.notebook_patch.return_value = self.bad
        with self.assertRaises(HandlerError):
            self.executor().patch(self.snapshot, {0: self.cell}, target=0)
        self.store.set_meta('provider_settings', {'mode': 'ollama', 'ollama_model': 'different:latest'})
        self.model.notebook_patch.return_value = self.good
        self.executor(2).patch(self.snapshot, {0: self.cell}, target=0)
        self.assertEqual(self.model.notebook_patch.call_count, 3)

    def test_instructor_change_and_unrelated_cell_are_not_applied_or_retried(self):
        for proposal in ({'index': 9, 'code': 'x=1'},
                         {'index': 0, 'code': self.original.replace('solve(x)', 'solve(x,y)').replace('pass', 'return 0')}):
            self.model.notebook_patch.reset_mock()
            self.model.notebook_patch.return_value = proposal
            executor = self.executor()
            with self.assertRaises(HandlerError):
                executor.patch(self.snapshot, {0: self.cell}, target=0)
            executor.call.assert_not_called()
            self.model.notebook_patch.assert_called_once()

    def test_stop_before_repair_keeps_feedback_without_edit(self):
        executor = self.executor()
        class Stop(Exception):
            pass
        def model(_):
            executor.checkpoint = Mock(side_effect=Stop())
            return self.bad
        self.model.notebook_patch.side_effect = model
        with self.assertRaises(Stop):
            executor.patch(self.snapshot, {0: self.cell}, target=0)
        executor.call.assert_not_called()
        self.model.notebook_patch.side_effect = None
        self.model.notebook_patch.return_value = self.good
        self.executor(2).patch(self.snapshot, {0: self.cell}, target=0)
        self.assertEqual(self.model.notebook_patch.call_args.args[0]['proposal_validation']['rejected_proposal'], self.bad)

    def test_unchanged_repair_is_not_executed_again(self):
        executor = self.executor()
        self.model.notebook_patch.return_value = {'index': 0, 'code': self.original}
        with self.assertRaisesRegex(HandlerError, 'no blind re-execution'):
            executor.patch(self.snapshot, {0: self.cell}, failure={'index': 0, 'reply': {'status': 'error'}})
        executor.call.assert_not_called()

    def test_execute_repairs_from_test_feedback_and_preserves_instructor_test(self):
        test_source = 'assert solve(3) == 6'
        document = {'metadata': {'language_info': {'name': 'python'}}, 'cells': [
            copy.deepcopy(self.cell), {'cell_type': 'code', 'source': test_source,
            'metadata': {'nbgrader': {'grade': True, 'locked': True}}}]}
        state = {'signature': 'before', 'document': document, 'path': 'fixture.ipynb', 'kernel': 'idle'}
        executor = self.executor()
        restarts, edits = [], []
        running = [None]
        def call(action, **values):
            if action == 'snapshot':
                return copy.deepcopy(state)
            if action == 'edit':
                edits.append(values['index'])
                self.assertEqual(values['index'], 0)
                state['document']['cells'][0]['source'] = values['code']
                state['signature'] = values['code']
            if action == 'restart':
                restarts.append(True)
            if action == 'run':
                running[0] = values['index']
            if action == 'poll':
                failed = running[0] == 1 and 'x * 2' not in state['document']['cells'][0]['source']
                return {'kernel': 'idle', 'idle': True, 'reply': {
                    'status': 'error' if failed else 'ok', 'ename': 'AssertionError' if failed else '',
                    'traceback': ['assert solve(3) == 6'] if failed else []}}
            return {'ok': True}
        executor.call = Mock(side_effect=call)
        self.model.notebook_patch.side_effect = [
            {'index': 0, 'regions': [{'number': 0, 'code': 'return x + 1'}]}, self.good]
        report = executor.execute()
        self.assertEqual(report['status'], 'executed')
        self.assertEqual(report['repairs'], 1)
        self.assertEqual(len(restarts), 2)
        self.assertEqual(edits, [0, 0])
        self.assertEqual(state['document']['cells'][1]['source'], test_source)
        feedback = self.model.notebook_patch.call_args_list[1].args[0]['failure']
        self.assertEqual(feedback['reply']['ename'], 'AssertionError')
        self.assertIsNone(self.store.get_meta('submission:1'))


class NotebookSelectionTests(unittest.TestCase):
    def page(self, identity):
        page = Mock()
        page.is_closed.return_value = False
        frame = Mock()
        frame.evaluate.return_value = identity
        page.frames = [frame]
        return page

    def identity(self):
        return {'origin': 'https://fixture.labs.coursera.org', 'path': 'lab.ipynb',
                'kernel': 'fixture-kernel', 'cells': [['code', 'x=1', {}, 'cell-1']]}

    def test_identical_notebook_views_are_reused_without_closing_tabs(self):
        a, b = self.page(self.identity()), self.page(self.identity())
        self.assertEqual(find_notebook([a, b]), (a, a.frames[0]))
        a.close.assert_not_called()
        b.close.assert_not_called()

    def test_different_notebook_kernel_origin_and_unsaved_source_are_ambiguous(self):
        for field, value in [('origin', 'https://unrelated.example'), ('path', 'other.ipynb'),
                             ('kernel', 'other-kernel'), ('cells', [['code', 'x=2', {}, 'cell-1']]),
                             ('origin', 'null')]:
            original = self.identity()
            altered = {**copy.deepcopy(original), field: value}
            with self.assertRaisesRegex(HandlerError, 'Multiple different'):
                find_notebook([self.page(original), self.page(altered)])

    def test_disconnected_frames_are_ignored(self):
        ready = self.page(self.identity())
        self.assertEqual(find_notebook([self.page(None), ready]), (ready, ready.frames[0]))

    def test_real_browser_duplicate_views_and_unsaved_difference(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                context = browser.new_context()
                context.route('**/*', lambda route: route.fulfill(body='<html><body>Notebook fixture</body></html>', content_type='text/html'))
                pages = [context.new_page(), context.new_page()]
                for page in pages:
                    page.goto('http://127.0.0.1:1/lab')
                    page.evaluate('''() => {
                        globalThis.fixtureSource = 'x=1';
                        globalThis.Jupyter = {notebook: {
                            _fully_loaded: true, notebook_path: 'fixture.ipynb',
                            kernel: {id: 'same-kernel', is_connected: () => true},
                            toJSON: () => ({cells: [{cell_type:'code', source:globalThis.fixtureSource,
                                                    metadata:{}, id:'cell-1'}]})
                        }};
                    }''')
                self.assertEqual(find_notebook(pages), (pages[0], pages[0].main_frame))
                pages[1].evaluate("globalThis.fixtureSource = 'unsaved change'")
                with self.assertRaisesRegex(HandlerError, 'Multiple different'):
                    find_notebook(pages)
                self.assertFalse(any(page.is_closed() for page in pages))
            finally:
                browser.close()
