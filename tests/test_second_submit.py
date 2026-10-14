"""Offline regression for nested Coursera 'Ready to submit?' confirmation."""
import importlib.util
import copy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'course_second_submit', ROOT / 'coursera_start.py')
course = importlib.util.module_from_spec(spec)
spec.loader.exec_module(course)


def javascript(*cases):
    result = subprocess.run(['node', str(ROOT / 'tests/second_submit_dom.js')],
        input=json.dumps({'code': course.QUIZ_UI_JS,
                         'notice': course.AUTHORIZED_QUIZ_NOTICE, 'cases': cases}),
        capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(result.stdout)


CONFIRM = {'action': 'confirm_submit', 'label': 'Submit'}


class SecondSubmitTests(unittest.TestCase):
    def test_captured_failure_has_two_submits_in_fullscreen_form(self):
        path = ROOT / 'tests/fixtures/logs/course_v3_20261014_151049_008909/progress.json'
        captured = json.loads(path.read_text(encoding='utf-8'))
        states = [e['result'] for e in captured['events']
                  if e['event'] == 'quiz_controls' and e['action'] == 'inspect']
        final = states[-1]
        self.assertEqual(final['dialog_kind'], 'quiz_form')
        self.assertFalse(final['blocking_dialog'])
        self.assertEqual(final['available_buttons'], ['Submit', 'Submit'])
        self.assertTrue(final['page_text'].endswith('Ready to submit?\nSubmit\nCancel'))
        self.assertEqual(sum(d['active'] for d in final['dialog_candidates']), 1)

    def test_nested_unmarked_confirmation_owns_submit_scope(self):
        value = javascript({})[0]
        result = value['result']
        self.assertEqual(result['dialog_kind'], 'submit_confirmation')
        self.assertTrue(result['blocking_dialog'])
        self.assertFalse(result['editable_form'])
        self.assertEqual(result['available_buttons'], ['Submit'])
        self.assertFalse(any(c['in_scope'] for c in result['controls']))
        self.assertEqual(value['clicks'], [])

    def test_guarded_confirmation_clicks_only_popup_once(self):
        for role in [None, 'dialog', 'alertdialog']:
            with self.subTest(role=role):
                value = javascript({'role': role, 'followup': CONFIRM})[0]
                self.assertEqual(value['before']['dialog_kind'], 'submit_confirmation')
                self.assertEqual(value['result']['clicked'], 'Submit')
                self.assertEqual(value['clicks'], ['popup-submit-0'])

    def test_confirmation_text_changed_between_inspect_and_click_refuses(self):
        value = javascript({'followup': CONFIRM,
                            'mutateText': 'Confirm another operation?'})[0]
        self.assertEqual(value['before']['dialog_kind'], 'submit_confirmation')
        self.assertIn('error', value['result'])
        self.assertEqual(value['clicks'], [])

    def test_disappeared_confirmation_never_clicks_background_submit(self):
        value = javascript({'followup': CONFIRM, 'removePopup': True})[0]
        self.assertEqual(value['before']['dialog_kind'], 'submit_confirmation')
        self.assertIn('error', value['result'])
        self.assertEqual(value['clicks'], [])

    def test_ambiguous_or_extra_content_confirmation_refuses(self):
        for modification in [{'duplicatePopup': True}, {'extraSubmit': True},
                             {'extraInput': True}, {'text': 'Ready to delete?'}]:
            with self.subTest(modification=modification):
                value = javascript({**modification, 'followup': CONFIRM})[0]
                self.assertIn('error', value['result'])
                self.assertEqual(value['clicks'], [])

    def test_hidden_confirmation_cannot_capture_background_submit(self):
        value = javascript({'hidden': True, 'followup': CONFIRM})[0]
        self.assertNotEqual(value['before'].get('dialog_kind'), 'submit_confirmation')
        self.assertIn('error', value['result'])
        self.assertEqual(value['clicks'], [])

    def test_disabled_confirmation_does_not_fall_back_to_background_submit(self):
        value = javascript({'disabled': True, 'followup': CONFIRM})[0]
        self.assertIn('error', value['result'])
        self.assertEqual(value['clicks'], [])

    def test_first_submit_without_confirmation_remains_available(self):
        value = javascript({'noPopup': True,
                            'followup': {'action': 'button', 'label': 'Submit'}})[0]
        self.assertEqual(value['before']['dialog_kind'], 'quiz_form')
        self.assertEqual(value['result']['clicked'], 'Submit')
        self.assertEqual(value['clicks'], ['background-submit'])

    def test_late_certificate_overlay_is_closed_before_submit_confirmation(self):
        value = javascript({'certificateOverlay': True,
            'followup': {'action': 'dismiss_course_certificate'},
            'inspectAfter': True, 'secondFollowup': CONFIRM})[0]
        self.assertEqual(value['before']['dialog_kind'], 'course_certificate')
        self.assertTrue(value['before']['blocking_dialog'])
        self.assertTrue(value['before']['hidden_submit_confirmation'])
        self.assertEqual(value['clicks'], ['certificate-close', 'popup-submit-0'])
        self.assertEqual(value['after']['dialog_kind'], 'submit_confirmation')
        self.assertEqual(value['after']['available_buttons'], ['Submit'])
        self.assertEqual(value['secondResult']['clicked'], 'Submit')

    def test_certificate_overlay_cannot_be_bypassed_by_submit_action(self):
        value = javascript({'certificateOverlay': True, 'followup': CONFIRM})[0]
        self.assertEqual(value['before']['dialog_kind'], 'course_certificate')
        self.assertIn('error', value['result'])
        self.assertEqual(value['clicks'], [])

    def test_unrelated_or_ambiguous_overlay_is_not_dismissed(self):
        for change in ({'certificateText': 'Confirm deletion?'},
                       {'extraCertificateClose': True}):
            with self.subTest(change=change):
                value = javascript({'certificateOverlay': True, **change,
                    'followup': {'action': 'dismiss_course_certificate'}})[0]
                self.assertIn('error', value['result'])
                self.assertEqual(value['clicks'], [])


class SecondSubmitWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_workflow_confirms_once_then_waits_for_verified_result(self):
        form, popup = [x['result'] for x in javascript({'noPopup': True}, {})]
        pending = {**popup, 'dialog_kind': 'submission_pending', 'blocking_dialog': False,
                   'buttons': [], 'available_buttons': [], 'result_page': False,
                   'page_text': 'Reviewing your submission...'}
        result = {**pending, 'dialog_kind': 'quiz_result', 'result_page': True,
                  'page_text': 'Your grade: 100%\nTo pass: 80%'}
        transitions = iter([popup, popup, pending, result])
        phase = 'form'
        calls = []

        async def evaluate(expression):
            nonlocal phase
            request = json.loads(expression[len(course.QUIZ_UI_JS) + 1:-1])
            calls.append(request)
            action = request['action']
            if action == 'inspect':
                return copy.deepcopy(form if phase == 'form' else popup if phase == 'popup'
                                     else next(transitions))
            if action == 'set_answers':
                self.assertEqual(phase, 'form')
                return {**copy.deepcopy(form), 'verified': True}
            if action == 'button':
                self.assertEqual(phase, 'form')
                self.assertEqual(request['label'], 'Submit')
                phase = 'popup'
                return {'clicked': 'Submit'}
            if action == 'confirm_submit':
                self.assertEqual(phase, 'popup')
                self.assertEqual(request['snapshot_id'], popup['snapshot_id'])
                phase = 'submitted'
                return {'clicked': 'Submit'}
            self.fail('Unexpected mutation: ' + action)

        item = {'key': 'assignment:UAOb6', 'title': 'Offline assignment fixture',
                'url': 'https://www.coursera.org/learn/philosophy/assignment-submission/UAOb6/what-is-philosophy'}
        selected = [c['id'] for c in form['controls'] if c['kind'] == 'answer' and c['selected']]
        grading = {'url': item['url'] + '/view-feedback', 'feedback': ['Your grade: 100%', 'To pass: 80%']}
        completion = {'url': item['url'], 'items': [{**item, 'type': 'assignment-submission',
            'aria_label': 'Graded Assignment, Completed, Grade: 100%',
            'evidence': ['aria-label: Graded Assignment, Completed, Grade: 100%']}]}
        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(SimpleNamespace(evaluate=evaluate),
                SimpleNamespace(max_steps=40), Path(directory))
            runner.prepare_quiz = AsyncMock(return_value=form)
            runner.solve_quiz_questions = AsyncMock(return_value=selected)
            runner.snapshot = AsyncMock(side_effect=[grading, completion])
            runner.open_item = AsyncMock()
            with patch.object(course.asyncio, 'sleep', new=AsyncMock()):
                await runner.quiz(item)
            saved = json.loads((Path(directory) / 'progress.json').read_text(encoding='utf-8'))
        self.assertEqual([r['action'] for r in calls if r['action'] != 'inspect'],
                         ['set_answers', 'button', 'confirm_submit'])
        self.assertEqual(runner.solve_quiz_questions.await_count, 1)
        self.assertEqual(runner.snapshot.await_count, 2)
        runner.open_item.assert_awaited_once_with(item)
        self.assertEqual(sum(e['event'] == 'quiz_submitted_result' and e['attempt'] == 1
                             for e in saved['events']), 1)
        self.assertTrue(any(e['event'] == 'item_verified' and e['key'] == item['key']
                            for e in saved['events']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
