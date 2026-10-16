"""Offline regression of captured post-submit and Honor Code dialog transitions."""
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
spec = importlib.util.spec_from_file_location('course_quiz_transitions', ROOT / 'coursera_start.py')
course = importlib.util.module_from_spec(spec)
spec.loader.exec_module(course)


def states(run):
    path = ROOT / f'tests/fixtures/logs/{run}/progress.json'
    return [e['result'] for e in json.loads(path.read_text(encoding='utf-8'))['events']
            if e['event'] == 'quiz_controls']


CAPTURED = states('course_v3_20261014_150351_106092')
PENDING = next(ui for ui in CAPTURED if 'Reviewing your submission...' in str(ui.get('dialog', '')))
RESULT = next(ui for ui in CAPTURED if ui.get('result_page') and len(ui.get('controls', [])) == 10)
FORM = next(ui for ui in CAPTURED if ui.get('editable_form') and len(ui.get('controls', [])) == 11)
INTRO = states('course_v3_20261014_150503_837593')[-1]
WRITTEN_REFLECTION = {
    'url': 'https://www.coursera.org/learn/philosophy/assignment-submission/GhNSw/interests-and-aspirations-in-xai-optional/attempt',
    'dialog': 'Interests and Aspirations in XAI (Optional) Question 1 Question 2 Coursera Honor Code Submit',
    'page_text': 'Interests and Aspirations in XAI (Optional) Practice Assignment',
    'controls': [],
    'observed_controls': [{'tag': 'button', 'label': 'Submit', 'disabled': True}],
}
REFLECTION_FIELDS = [
    {'label': 'What are you most interested in learning about in this course?'},
    {'label': 'What problem or problems are you hoping to solve with XAI?'},
]


def javascript(*cases):
    result = subprocess.run(['node', str(ROOT / 'tests/quiz_transition_dom.js')],
        input=json.dumps({'code':course.QUIZ_UI_JS,'notice':course.AUTHORIZED_QUIZ_NOTICE,'cases':cases}),
        capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(result.stdout)


class CapturedDialogTests(unittest.TestCase):
    def test_written_reflection_is_an_attempt_form_not_a_separate_popup(self):
        value = javascript({'ui': WRITTEN_REFLECTION, 'key': 'assignment:GhNSw',
                            'textareas': REFLECTION_FIELDS})[0]
        result = value['result']
        self.assertEqual(result['dialog_kind'], 'quiz_form')
        self.assertFalse(result['blocking_dialog'])
        self.assertTrue(result['text_response_required'])
        self.assertEqual([field['label'] for field in result['unsupported_inputs']],
                         [field['label'] for field in REFLECTION_FIELDS])
        self.assertFalse(result['editable_form'])
        self.assertEqual(value['clicks'], [])

    def test_actual_processing_dialog_is_pending_without_extra_submit(self):
        value = javascript({'ui':PENDING})[0]
        self.assertEqual(value['result']['dialog_kind'], 'submission_pending')
        self.assertFalse(value['result']['blocking_dialog'])
        self.assertFalse(value['result']['result_page'])
        self.assertFalse(value['result']['editable_form'])
        self.assertEqual(value['clicks'], [])

    def test_actual_feedback_dialog_is_nonblocking_result(self):
        value = javascript({'ui':RESULT})[0]
        self.assertEqual(value['result']['dialog_kind'], 'quiz_result')
        self.assertFalse(value['result']['blocking_dialog'])
        self.assertTrue(value['result']['result_page'])
        self.assertFalse(value['result']['editable_form'])
        self.assertEqual(value['clicks'], [])

    def test_processing_cannot_inherit_old_grade_from_page_behind(self):
        pending = copy.deepcopy(PENDING)
        pending['page_text'] += '\nYour grade: 40%'
        value = javascript({'ui':pending})[0]
        self.assertFalse(value['result']['result_page'])
        self.assertEqual(value['result']['dialog_kind'], 'submission_pending')

    def test_separate_modal_on_feedback_stays_blocking(self):
        overlay = copy.deepcopy(RESULT)
        overlay['dialog'] = 'Confirm your identity to continue'
        overlay['controls'] = []
        overlay['observed_controls'] = [{'tag':'button','label':'Continue','disabled':False}]
        value = javascript({'ui':overlay})[0]
        self.assertEqual(value['result']['dialog_kind'], 'modal')
        self.assertTrue(value['result']['blocking_dialog'])
        self.assertEqual(value['clicks'], [])

    def test_another_assignment_still_fails_before_interaction(self):
        value = javascript({'ui':RESULT,'key':'assignment:UAOb6'})[0]
        self.assertEqual(value['result']['error'], 'Wrong quiz page.')
        self.assertEqual(value['clicks'], [])

    def test_exact_intro_is_classified_and_guarded_continue_clicks_once(self):
        value = javascript({'ui':INTRO,'key':'assignment:UAOb6'})[0]
        self.assertEqual(value['result']['dialog_kind'], 'honor_code_intro')
        self.assertTrue(value['result']['blocking_dialog'])
        self.assertEqual(value['clicks'], [])
        request = {'key':'assignment:UAOb6','action':'continue_honor_intro',
                   'snapshot_id':value['result']['snapshot_id']}
        clicked = javascript({'ui':INTRO,'request':request})[0]
        self.assertEqual(clicked['result']['clicked'], 'Continue')
        self.assertEqual(clicked['clicks'], ['Continue'])

    def test_changed_intro_or_input_or_stale_snapshot_never_clicks(self):
        value = javascript({'ui':INTRO,'key':'assignment:UAOb6'})[0]['result']
        request = {'key':'assignment:UAOb6','action':'continue_honor_intro','snapshot_id':value['snapshot_id']}
        changed = copy.deepcopy(INTRO)
        changed['dialog'] += ' I certify that this is my own work.'
        duplicate = copy.deepcopy(INTRO)
        duplicate['observed_controls'].append({'tag':'button','label':'Continue','disabled':False})
        stale = dict(request, snapshot_id='old-snapshot')
        for output in javascript({'ui':changed,'request':request},
                {'ui':INTRO,'request':request,'extraInput':True},
                {'ui':duplicate,'request':request}, {'ui':INTRO,'request':stale}):
            self.assertIn('error', output['result'])
            self.assertEqual(output['clicks'], [])

    def test_checked_name_does_not_allow_submit_with_own_work_statement(self):
        form = copy.deepcopy(FORM)
        for control in form['controls']:
            if control['kind'] == 'notice':
                control['selected'] = True
        for action in form['observed_controls']:
            if action['label'] == 'Submit':
                action['disabled'] = False
        observed = javascript({'ui':form})[0]['result']
        self.assertTrue(observed['manual_submission_required'])
        request = {'key':'assignment:oVRfd','action':'button','label':'Submit',
                   'authorized_notice':course.AUTHORIZED_QUIZ_NOTICE,
                   'authorized_notice_confirmed':True,
                   'snapshot_id':observed['snapshot_id']}
        attempt = javascript({'ui':form,'request':request})[0]
        self.assertIn('authorship', attempt['result']['error'])
        self.assertEqual(attempt['clicks'], [])


class WorkflowTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_written_reflection_pauses_before_model_or_browser_action(self):
        view = javascript({'ui': WRITTEN_REFLECTION, 'key': 'assignment:GhNSw',
                           'textareas': REFLECTION_FIELDS})[0]['result']
        calls = []

        async def quiz_ui(action='inspect', **params):
            calls.append(action)
            self.assertEqual(action, 'inspect')
            return copy.deepcopy(view)

        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(max_steps=40), Path(directory))
            runner.solve_quiz_questions = AsyncMock()
            with self.assertRaisesRegex(course.StopRun, "learner's own written responses"):
                await runner.prepare_quiz({'key': 'assignment:GhNSw'}, quiz_ui)
            runner.solve_quiz_questions.assert_not_awaited()
            self.assertEqual([event['event'] for event in runner.data['events']],
                             ['quiz_manual_input_required'])
        self.assertEqual(calls, ['inspect'])

    async def test_intro_only_clicked_once_while_it_persists(self):
        intro = javascript({'ui':INTRO,'key':'assignment:UAOb6'})[0]['result']
        states = iter([intro,intro,FORM])
        calls = []

        async def quiz_ui(action='inspect', **params):
            calls.append((action,params))
            if action == 'continue_honor_intro':
                return {'clicked':'Continue'}
            return copy.deepcopy(next(states))

        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None,SimpleNamespace(max_steps=40),Path(directory))
            with patch.object(course.asyncio,'sleep',new=AsyncMock()):
                result = await runner.prepare_quiz({'key':'assignment:UAOb6'},quiz_ui)
        self.assertTrue(result['editable_form'])
        self.assertEqual(sum(action=='continue_honor_intro' for action,_ in calls),1)

    async def test_submit_processing_result_performs_no_extra_submit(self):
        pending,result = [output['result'] for output in javascript({'ui':PENDING},{'ui':RESULT})]
        selected = [c['id'] for c in FORM['controls'] if c['kind']=='answer' and c['selected']]
        form = copy.deepcopy(FORM)
        # This test covers delayed processing for a routine notice. The
        # captured source also has an own-work attestation, tested separately.
        form['authorship_statement'] = None
        form['manual_submission_required'] = False
        for control in form['controls']:
            if control['kind']=='notice':
                control['selected']=True
        form['answer_snapshot_id']='fixed-answer-form'
        after_submit = False
        transitions = iter([pending,pending,result])
        calls = []

        async def evaluate(expression):
            request = json.loads(expression[len(course.QUIZ_UI_JS)+1:-1])
            calls.append(request)
            if after_submit:
                self.assertEqual(request['action'],'inspect')
                return copy.deepcopy(next(transitions))
            value = copy.deepcopy(form)
            if request['action']=='set_answers':
                value['verified']=True
            return value

        async def click_once(quiz_ui,label,validate,**kwargs):
            nonlocal after_submit
            self.assertFalse(after_submit, 'A second Submit helper must not be called for processing')
            validate(await quiz_ui())
            after_submit=True
            return {'clicked':'Submit'}

        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(SimpleNamespace(evaluate=evaluate),SimpleNamespace(max_steps=40),Path(directory))
            runner.prepare_quiz=AsyncMock(return_value=form)
            runner.solve_quiz_questions=AsyncMock(return_value=selected)
            runner.snapshot=AsyncMock(return_value={'feedback':['Your grade: 100%']})
            runner.open_item=AsyncMock()
            runner.verified_item=AsyncMock(return_value=True)
            with patch.object(course,'press_quiz_button_when_ready',side_effect=click_once) as submit, \
                    patch.object(course,'quiz_passed',return_value=True) as passed, \
                    patch.object(course.asyncio,'sleep',new=AsyncMock()):
                await runner.quiz({'key':'assignment:oVRfd'})
            self.assertEqual(submit.await_count,1)
            passed.assert_called_once()
            runner.verified_item.assert_awaited_once()
            self.assertEqual(runner.solve_quiz_questions.await_count,1)
            self.assertTrue(any(e['event']=='quiz_submitted_result' and e['attempt']==1 for e in runner.data['events']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
