import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from v2.notebooks import NotebookExecutor, optional_tail_cells
from v2.handlers import HandlerError
from v2.store import Store


def code(text, **meta):
    return {'cell_type': 'code', 'source': text, 'metadata': meta}


def markdown(text):
    return {'cell_type': 'markdown', 'source': text, 'metadata': {}}


class OptionalTailTests(unittest.TestCase):
    def setUp(self):
        self.doc = {'metadata': {'language_info': {'name': 'python'}}, 'cells': [
            code('# GRADED FUNCTION: answer\ndef answer(): return 1', nbgrader={'solution': True}),
            code('assert answer()==1', nbgrader={'grade': True, 'locked': True}),
            markdown('## 3 - Backpropagation (OPTIONAL / UNGRADED)'),
            markdown('### Exercise 5'),
            code('def extra():\n # YOUR CODE HERE\n pass'),
            code('assert extra()==2'),
            markdown('## References')]}

    def test_only_explicit_terminal_ungraded_code_excluded(self):
        before = copy.deepcopy(self.doc)
        self.assertEqual(optional_tail_cells(self.doc), {4, 5})
        self.assertEqual(self.doc, before)

    def test_toc_prose_optional_only_and_fenced_heading_do_not_exclude(self):
        for heading in ['- [3 Optional / Ungraded](#3)', 'This section is optional and ungraded.',
                        '## Optional practice', '```\n## OPTIONAL / UNGRADED\n```',
                        '## OPTIONAL / UNGRADED\n## Required exercise']:
            d = copy.deepcopy(self.doc); d['cells'][2] = markdown(heading)
            self.assertEqual(optional_tail_cells(d), set())

    def test_later_code_section_blocks_exclusion(self):
        self.doc['cells'] += [code('assert extra()==2')]
        self.assertEqual(optional_tail_cells(self.doc), set())

    def test_grade_or_solution_inside_optional_section_blocks_exclusion(self):
        for cell in [code('# GRADED FUNCTION: extra\ndef extra(): pass'),
                     code('assert extra()', nbgrader={'grade': True}),
                     code('x=1', nbgrader={'solution': True})]:
            d = copy.deepcopy(self.doc); d['cells'][4] = cell
            self.assertEqual(optional_tail_cells(d), set())

    def test_no_graded_marker_before_heading_keeps_all_cells(self):
        self.doc['cells'] = self.doc['cells'][2:]
        self.assertEqual(optional_tail_cells(self.doc), set())

    def executor(self, store, failed=False):
        e = NotebookExecutor(Mock(), Mock(), store, 1, lambda: None, max_repairs=0)
        state = {'path': 'lab.ipynb', 'document': copy.deepcopy(self.doc), 'kernel': 'idle', 'signature': 'unchanged'}
        e.call = Mock(side_effect=lambda action, **kw: copy.deepcopy(state) if action == 'snapshot' else {'ok': True})
        e.run_cell = Mock(side_effect=lambda snap, index: {'reply': {'status': 'error' if failed and index == 1 else 'ok'}})
        return e

    def test_required_tests_execute_optional_code_stays_untouched_no_grade_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.db'); e = self.executor(store)
            report = e.execute()
            self.assertEqual([call.args[1] for call in e.run_cell.call_args_list], [0, 1])
            self.assertEqual(report['excluded_optional_cells'], [4, 5])
            self.assertEqual(report['status'], 'executed')
            self.assertEqual(report['cells_run'], 2)
            e.groq.notebook_patch.assert_not_called()
            self.assertFalse(any(call.args[0] == 'edit' for call in e.call.call_args_list))
            self.assertIsNone(store.get_meta('submission:1'))

    def test_required_test_failure_still_blocks_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.db'); e = self.executor(store, failed=True)
            with self.assertRaisesRegex(HandlerError, 'repair budget exhausted'):
                e.execute()
            self.assertEqual([call.args[1] for call in e.run_cell.call_args_list], [0, 1])
            self.assertEqual(store.get_meta('lab_report:1')['status'], 'failed')
            self.assertIsNone(store.get_meta('submission:1'))
