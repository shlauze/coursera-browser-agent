"""Offline replay and negative cases for the observed new-attempt dialog."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'course_retry_dialog', ROOT / 'coursera_start.py')
course = importlib.util.module_from_spec(spec)
spec.loader.exec_module(course)
LOG = ROOT / 'tests/fixtures/logs/course_v3_20261014_152842_483495/progress.json'
captured = json.loads(LOG.read_text(encoding='utf-8'))
CAPTURED_DIALOG = next(e['result'] for e in reversed(captured['events'])
                       if e['event'] == 'quiz_controls' and e['result'].get('blocking_dialog'))
RETRY_BODY = CAPTURED_DIALOG['dialog'].removeprefix('Start new attempt? ').removesuffix(' Continue Cancel')
REPLACE_GRADE_BODY = 'When you submit a new attempt it will replace your previous submission and grade.'
TIMED_RETRY_BODY = ('You have 50 minutes to submit up to 1 time for this attempt. '
                    'You will have 2 attempts left after the time runs out. '
                    'After that, you will have to wait 24h before you can try again.')


def javascript(*cases):
    result = subprocess.run(['node', str(ROOT / 'tests/retry_dialog_dom.js')],
        input=json.dumps({'code': course.QUIZ_UI_JS, 'notice': course.AUTHORIZED_QUIZ_NOTICE,
                         'retry_body': RETRY_BODY, 'cases': cases}),
        capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(result.stdout)


CONTINUE = {'action': 'continue_new_attempt'}
LOADING_RESULT = next(e['result'] for e in captured['events']
                      if e['event'] == 'quiz_controls' and e['result'].get('result_page'))


def ready_feedback(count=6):
    value = copy.deepcopy(LOADING_RESULT)
    value['page_text'] = 'Your grade: 33.33%\n' + '\n'.join(
        f'Question {number}\nObserved question {number}\nObserved answer\nIncorrect\n'
        f'Explanation for question {number}\n0 / 1 point' for number in range(1, count + 1))
    return value


class RetryDialogTests(unittest.TestCase):
    def test_timed_start_attempt_modal_is_recognized_and_continued_once(self):
        for body in (TIMED_RETRY_BODY,
                     TIMED_RETRY_BODY.replace('50 minutes', '75 minutes')
                                     .replace('1 time', '2 times')
                                     .replace('2 attempts', '0 attempts')
                                     .replace('24h', '7h 36m')):
            with self.subTest(body=body):
                value=javascript({'description':body,'timed':True,'followup':CONTINUE})[0]
                self.assertEqual(value['before']['dialog_kind'],'new_attempt_confirmation')
                self.assertEqual(value['before']['retry_confirmation_candidates'],1)
                self.assertEqual(value['result']['clicked'],'Continue')
                self.assertEqual(value['clicks'],['popup-continue-0'])

    def test_timed_start_attempt_modal_rejects_changed_structure_or_text(self):
        for alteration in (
            {'missingTimedTestId':True},
            {'wrongTimedButtonTestId':True},
            {'text':'Start another attempt?'},
            {'description':TIMED_RETRY_BODY.replace('after the time runs out',
                                                    'after submitting')},
            {'extraSubmit':True},
            {'extraInput':True},
        ):
            with self.subTest(alteration=alteration):
                value=javascript({'description':TIMED_RETRY_BODY,'timed':True,
                                  'followup':CONTINUE,**alteration})[0]
                self.assertIn('error',value['result'])
                self.assertEqual(value['clicks'],[])

    def test_log_confirms_exact_failure_after_failed_result(self):
        self.assertEqual(CAPTURED_DIALOG['dialog_kind'], 'modal')
        self.assertEqual(CAPTURED_DIALOG['available_buttons'], ['Continue'])
        self.assertIn('only replace your previous grade if', RETRY_BODY)
        result = next(e for e in captured['events'] if e['event'] == 'quiz_submitted_result')
        self.assertFalse(result['passed'])
        self.assertEqual(result['attempt'], 1)

    def test_exact_observed_dialog_is_recognized_and_continue_is_scoped(self):
        for role in [None, 'dialog', 'alertdialog']:
            with self.subTest(role=role):
                value = javascript({'role': role, 'followup': CONTINUE})[0]
                self.assertEqual(value['before']['dialog_kind'], 'new_attempt_confirmation')
                self.assertTrue(value['before']['blocking_dialog'])
                self.assertFalse(value['before']['editable_form'])
                self.assertEqual(value['result']['clicked'], 'Continue')
                self.assertEqual(value['clicks'], ['popup-continue-0'])

    def test_typographic_and_plain_apostrophe_are_equivalent(self):
        for body in [RETRY_BODY.replace('\u2019', "'"), RETRY_BODY.replace("'", '\u2019')]:
            value = javascript({'description': body, 'followup': CONTINUE})[0]
            self.assertEqual(value['result']['clicked'], 'Continue')

    def test_observed_remaining_attempts_warning_is_recognized(self):
        body = (RETRY_BODY + ' You will have 0 attempts left after this one. '
                'After that, you will have to wait 7h 36m before you can try again.')
        value = javascript({'description': body, 'followup': CONTINUE})[0]
        self.assertEqual(value['before']['dialog_kind'], 'new_attempt_confirmation')
        self.assertEqual(value['result']['clicked'], 'Continue')
        self.assertEqual(value['clicks'], ['popup-continue-0'])

    def test_exact_submission_and_grade_variant_is_recognized(self):
        value = javascript({'description': REPLACE_GRADE_BODY, 'followup': CONTINUE})[0]
        self.assertEqual(value['before']['dialog_kind'], 'new_attempt_confirmation')
        self.assertTrue(value['before']['blocking_dialog'])
        self.assertFalse(value['before']['editable_form'])
        self.assertEqual(value['result']['clicked'], 'Continue')
        self.assertEqual(value['clicks'], ['popup-continue-0'])

    def test_submission_and_grade_variant_rejects_changed_text_or_extra_controls(self):
        for alteration in [
            {'description': REPLACE_GRADE_BODY.replace(' and grade.', ' and grade permanently.')},
            {'description': REPLACE_GRADE_BODY, 'extraInput': True},
            {'description': REPLACE_GRADE_BODY, 'extraSubmit': True},
        ]:
            with self.subTest(alteration=alteration):
                value = javascript({**alteration, 'followup': CONTINUE})[0]
                self.assertIn('error', value['result'])
                self.assertEqual(value['clicks'], [])

    def test_changed_disappeared_hidden_or_disabled_popup_never_clicks(self):
        for alteration in [{'mutateText': 'Confirm a new charge?'}, {'removePopup': True},
                           {'hidden': True}, {'disabled': True}, {'noPopup': True}]:
            with self.subTest(alteration=alteration):
                value = javascript({**alteration, 'followup': CONTINUE})[0]
                self.assertIn('error', value['result'])
                self.assertEqual(value['clicks'], [])

    def test_extra_content_unknown_text_or_ambiguous_popup_never_clicks(self):
        for alteration in [{'extraInput': True}, {'extraSubmit': True},
                           {'description': RETRY_BODY + ' A fee will be charged.'},
                           {'duplicatePopup': True}, {'role': 'dialog', 'duplicatePopup': True},
                           {'blockingOther': True}]:
            with self.subTest(alteration=alteration):
                value = javascript({**alteration, 'followup': CONTINUE})[0]
                self.assertIn('error', value['result'])
                self.assertEqual(value['clicks'], [])

    def test_click_time_course_and_assignment_guard(self):
        value = javascript({'followup': CONTINUE,
            'changeLocation': 'https://www.coursera.org/learn/philosophy/assignment-submission/OTHER/other'})[0]
        self.assertEqual(value['result']['error'], 'Wrong quiz page.')
        self.assertEqual(value['clicks'], [])


class RetryWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_try_again_continue_then_form_clicks_confirmation_once(self):
        popup = javascript({})[0]['result']
        form = javascript({'noPopup': True})[0]['result']
        landing = {**form, 'editable_form': False, 'controls': [],
                   'buttons': [{'label': 'Try again', 'workflow': 'entry', 'disabled': False}]}
        states = iter([landing, popup, popup, form])
        mutations = []
        async def quiz_ui(action='inspect', **kwargs):
            if action == 'inspect':
                return copy.deepcopy(next(states))
            mutations.append((action, kwargs))
            return {'clicked': 'Try again' if action == 'button' else 'Continue'}
        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(), Path(directory))
            with patch.object(course.asyncio, 'sleep', new=AsyncMock()):
                result = await runner.prepare_quiz({'key': 'assignment:pNJkH'}, quiz_ui)
        self.assertTrue(result['editable_form'])
        self.assertEqual([a for a, _ in mutations], ['button', 'continue_new_attempt'])
        self.assertEqual(mutations[1][1]['snapshot_id'], popup['snapshot_id'])

    async def test_disabled_continue_waits_until_enabled(self):
        disabled = javascript({'disabled': True})[0]['result']
        enabled = javascript({})[0]['result']
        form = javascript({'noPopup': True})[0]['result']
        states = iter([disabled, enabled, form])
        mutations = []
        async def quiz_ui(action='inspect', **kwargs):
            if action == 'inspect':
                return copy.deepcopy(next(states))
            mutations.append(action)
            return {'clicked': 'Continue'}
        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(), Path(directory))
            with patch.object(course.asyncio, 'sleep', new=AsyncMock()):
                await runner.prepare_quiz({'key': 'assignment:pNJkH'}, quiz_ui)
        self.assertEqual(mutations, ['continue_new_attempt'])


class FeedbackReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_landing_view_feedback_is_opened_before_review(self):
        landing = copy.deepcopy(LOADING_RESULT)
        landing['url'] = landing['url'].rsplit('/view-feedback', 1)[0]
        landing['result_page'] = False
        landing['buttons'] = [{'label':'View feedback', 'workflow':'other', 'disabled':False}]
        result = ready_feedback()
        calls = []
        async def ui(action='inspect', **kwargs):
            calls.append((action, kwargs))
            return {'clicked':'View feedback'} if action == 'button' else copy.deepcopy(result)
        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(), Path(directory))
            with patch.object(course.asyncio, 'sleep', new=AsyncMock()):
                text, records = await runner.wait_for_quiz_feedback(
                    {'key':'assignment:pNJkH'}, ui, landing, 6)
        self.assertEqual(len(records), 6)
        self.assertIn('Explanation for question 6', text)
        self.assertEqual([action for action, _ in calls].count('button'), 1)
        self.assertTrue(any(e['event'] == 'quiz_feedback_opened' for e in runner.data['events']))

    async def test_review_click_reads_new_page_even_if_summary_looks_like_result(self):
        summary = copy.deepcopy(LOADING_RESULT)
        summary['url'] = summary['url'].rsplit('/view-feedback', 1)[0]
        summary['result_page'] = True
        summary['buttons'] = [{'label':'Review feedback', 'workflow':'other', 'disabled':False}]
        detailed = ready_feedback()
        calls = []
        async def ui(action='inspect', **kwargs):
            calls.append(action)
            return {'clicked':'Review feedback'} if action == 'button' else copy.deepcopy(detailed)
        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(), Path(directory))
            with patch.object(course.asyncio, 'sleep', new=AsyncMock()):
                result = await runner.open_quiz_feedback({'key':'assignment:pNJkH'}, ui, summary)
        self.assertEqual(result['url'], detailed['url'])
        self.assertEqual(calls, ['button', 'inspect'])

    async def test_initial_captured_loading_waits_for_all_question_feedback(self):
        self.assertTrue(LOADING_RESULT['page_text'].endswith('Loading...'))
        ui = AsyncMock(side_effect=[ready_feedback(2), ready_feedback()])
        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(), Path(directory))
            with patch.object(course.asyncio, 'sleep', new=AsyncMock()):
                text, records = await runner.wait_for_quiz_feedback(
                    {'key': 'assignment:pNJkH'}, ui, LOADING_RESULT, 6)
        self.assertEqual(len(records), 6)
        self.assertEqual(ui.await_count, 2)
        self.assertIn('Explanation for question 6', text)
        self.assertTrue(any(e['event'] == 'quiz_feedback_ready' for e in runner.data['events']))
        self.assertTrue(all(not call.args and not call.kwargs for call in ui.await_args_list))

    async def test_loading_timeout_records_unavailable_without_mutation(self):
        ui = AsyncMock(return_value=LOADING_RESULT)
        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(), Path(directory))
            with patch.object(course.asyncio, 'sleep', new=AsyncMock()), \
                 patch.object(course.time, 'monotonic', side_effect=[0, 0, 0, 20]):
                with self.assertRaisesRegex(course.StopRun, 'No new attempt was started'):
                    await runner.wait_for_quiz_feedback({'key': 'assignment:pNJkH'}, ui, LOADING_RESULT, 6)
        self.assertEqual(ui.await_count, 1)
        self.assertTrue(any(e['event'] == 'quiz_feedback_unavailable' for e in runner.data['events']))

    async def test_different_quiz_cannot_supply_feedback(self):
        ui = AsyncMock()
        wrong = ready_feedback()
        wrong['url'] = wrong['url'].replace('pNJkH', 'another-assignment')
        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(), Path(directory))
            with self.assertRaisesRegex(course.StopRun, 'page changed'):
                await runner.wait_for_quiz_feedback({'key': 'assignment:pNJkH'}, ui, wrong, 6)
        ui.assert_not_awaited()

    def test_questions_without_feedback_verdict_are_not_ready(self):
        self.assertEqual(course.question_feedback('Question 1\nWhich answer is correct?\nAnswer 1'), [])

    def test_real_question_feedback_survives_banner_stripping_before_js_cap(self):
        ui = ready_feedback()
        banner = course.PAGE_AI_BANNER + (' injected page text ' * 250) + 'Do you understand?.\nI understand\n'
        ui['page_text'] = 'Your grade: 33.33%\n' + '\n'.join(
            f'Question {number}\nQuestion text {number}\n{banner}Answer\nIncorrect\n0 / 1 point'
            for number in range(1, 7))
        self.assertGreater(len(ui['page_text']), 16000)
        output = subprocess.run(['node', str(ROOT / 'tests/quiz_transition_dom.js')],
            input=json.dumps({'code': course.QUIZ_UI_JS, 'notice': course.AUTHORIZED_QUIZ_NOTICE,
                'cases': [{'ui': ui, 'key': 'assignment:pNJkH'}]}),
            capture_output=True, text=True, encoding='utf-8', check=True)
        text = json.loads(output.stdout)[0]['result']['page_text']
        self.assertNotIn(course.PAGE_AI_BANNER, text)
        self.assertIn('Question 6', text)
        self.assertEqual(len(course.question_feedback(text)), 6)

    async def test_repeated_popup_times_out_without_double_click(self):
        popup = javascript({})[0]['result']
        mutations = []
        async def quiz_ui(action='inspect', **kwargs):
            if action == 'inspect':
                return copy.deepcopy(popup)
            mutations.append(action)
            return {'clicked': 'Continue'}
        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(), Path(directory))
            with patch.object(course.asyncio, 'sleep', new=AsyncMock()), \
                 patch.object(course.time, 'monotonic', side_effect=[0, 0, 1, 2, 36]):
                with self.assertRaisesRegex(course.StopRun, 'did not load within'):
                    await runner.prepare_quiz({'key': 'assignment:pNJkH'}, quiz_ui)
        self.assertEqual(mutations, ['continue_new_attempt'])


class CooldownTests(unittest.IsolatedAsyncioTestCase):
    def test_only_active_countdowns_are_recognized(self):
        warning = ('You will have 0 attempts left after this one. After that, '
                   'you will have to wait 7h 36m before you can try again.')
        self.assertIsNone(course.active_quiz_cooldown_seconds(warning))
        self.assertEqual(course.active_quiz_cooldown_seconds('You can try again in 7h 36m.'), 27360)
        self.assertEqual(course.active_quiz_cooldown_seconds('Next attempt is available after 2 hours 15 minutes.'), 8100)
        self.assertIsNone(course.active_quiz_cooldown_seconds('Try again in 999 days.'))

    async def test_locked_attempt_waits_then_reopens_same_item(self):
        locked = {'url':LOADING_RESULT['url'].split('/attempt/', 1)[0],
                  'buttons':[{'label':'Try again', 'workflow':'entry', 'disabled':True}],
                  'controls':[], 'editable_form':False, 'page_text':'You can try again in 2s.'}
        form = javascript({'noPopup':True})[0]['result']
        states = iter([locked, form])
        clock = [0.0]
        async def sleep(seconds):
            clock[0] += seconds
        async def ui(action='inspect', **kwargs):
            self.assertEqual(action, 'inspect')
            return copy.deepcopy(next(states))
        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(), Path(directory))
            runner.open_item = AsyncMock()
            runner.on_quiz_cooldown = AsyncMock()
            with patch.object(course.asyncio, 'sleep', side_effect=sleep), \
                 patch.object(course.time, 'monotonic', side_effect=lambda:clock[0]):
                opened = await runner.prepare_quiz({'key':'assignment:pNJkH'}, ui)
        self.assertTrue(opened['editable_form'])
        runner.on_quiz_cooldown.assert_awaited_once_with(7)
        runner.open_item.assert_awaited_once()
        self.assertTrue(any(e['event'] == 'quiz_cooldown_started' for e in runner.data['events']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
