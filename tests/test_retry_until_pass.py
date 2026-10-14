"""Offline repeated-attempt workflow with fake browser/model and real ledger."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import coursera_start as engine
import course_queue as queue
from course_state import QueueStore

URL = 'https://www.coursera.org/learn/philosophy'
KEY = 'assignment:fixture'
ITEM = {'key':KEY, 'title':'Offline practice', 'url':URL + '/assignment-submission/fixture/practice'}
CONFIG = [{'id':'philosophy', 'title':'Philosophy', 'url':URL, 'modules':None}]


class FakeAttempts:
    """Each first Submit opens a confirmation, each second Submit grades once."""
    def __init__(self, test, failures=3, initial_count=0):
        self.test, self.failures, self.initial_count = test, failures, initial_count
        self.phase = 'form'
        self.submitted = 0
        self.calls = []
        self.feedback_seen = []
        self.reservations = []
        self.runner = None
        self.form = {'url':ITEM['url'] + '/attempt', 'snapshot_id':'form1',
            'answer_snapshot_id':'answer1', 'editable_form':True, 'blocking_dialog':False,
            'result_page':False, 'manual_submission_required':False,
            'controls':[{'id':'c0', 'kind':'answer', 'type':'radio', 'name':'q1',
                         'question':'Fixture question one', 'label':'Option A', 'selected':True, 'in_scope':True},
                        {'id':'c1', 'kind':'answer', 'type':'radio', 'name':'q1',
                         'question':'Fixture question one', 'label':'Option B', 'selected':False, 'in_scope':True}],
            'buttons':[{'label':'Submit', 'workflow':'submit', 'disabled':False}],
            'page_text':'Question 1\nFixture question one'}

    def result(self):
        passed = self.submitted > self.failures
        grade, verdict = ('100%', 'Correct') if passed else ('0%', 'Incorrect')
        return {'url':ITEM['url'] + '/attempt/view-feedback', 'result_page':True,
            'blocking_dialog':False, 'editable_form':False, 'controls':[], 'buttons':[],
            'page_text':f'Your grade: {grade}\nTo pass you need at least 80%.\nQuestion 1\n'
                        f'Fixture question one\nOption A\n{verdict}\n'
                        f'Feedback from attempt {self.submitted}\n' + ('1 / 1 point' if passed else '0 / 1 point'),
            'feedback':[f'Your grade: {grade}', 'To pass you need at least 80%.']}

    async def evaluate(self, expression):
        self.test.assertTrue(expression.startswith(engine.QUIZ_UI_JS + '('))
        request = json.loads(expression[len(engine.QUIZ_UI_JS) + 1:-1])
        action = request['action']
        self.calls.append(action)
        if action == 'inspect':
            if self.phase == 'form':
                return copy.deepcopy(self.form)
            if self.phase == 'popup':
                return {**copy.deepcopy(self.form), 'blocking_dialog':True,
                    'editable_form':False, 'dialog_kind':'submit_confirmation',
                    'controls':[], 'page_text':'Ready to submit? Submit Cancel'}
            return self.result()
        self.test.assertLessEqual(self.submitted, self.failures, 'Mutation after passing result')
        if action == 'set_answers':
            self.test.assertEqual(self.phase, 'form')
            return {**copy.deepcopy(self.form), 'verified':True}
        if action == 'button':
            self.test.assertEqual(request['label'], 'Submit')
            self.test.assertEqual(self.phase, 'form')
            checkpoint = json.loads((self.runner.store.path.parent / 'checkpoints/philosophy.json').read_text(encoding='utf-8'))
            reserved = checkpoint['attempts'][KEY]
            self.test.assertTrue(reserved['pending'])
            self.test.assertEqual(reserved['count'], self.initial_count + self.submitted + 1)
            self.reservations.append(copy.deepcopy(reserved))
            self.phase = 'popup'
            return {'clicked':'Submit'}
        if action == 'confirm_submit':
            self.test.assertEqual(self.phase, 'popup')
            self.submitted += 1
            self.phase = 'result'
            return {'clicked':'Submit'}
        self.test.fail('Unexpected fake browser mutation: ' + action)

    async def prepare(self, item, quiz_ui):
        self.test.assertLessEqual(self.submitted, self.failures)
        self.phase = 'form'
        return copy.deepcopy(self.form)

    async def solve(self, item, ui, feedback):
        self.feedback_seen.append(feedback)
        return ['c0']


class UntilPassTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        engine.configure_course(URL)

    async def exercise(self, attempts=0, failures=3, initial_count=0, max_steps=100, grading_sequence=None):
        engine.configure_course(URL)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            store = QueueStore(path, copy.deepcopy(CONFIG))
            if initial_count:
                store.update('philosophy', checkpoint={'attempts':{KEY:{'count':initial_count,
                    'pending':False, 'passed':False, 'feedback_ready':True, 'feedback_ready_count':initial_count}},
                    'feedback':{KEY:'Prior saved question feedback'}, 'modules':{}})
                store = QueueStore(path, copy.deepcopy(CONFIG))
            fixture = FakeAttempts(self, failures, initial_count)
            args = SimpleNamespace(quiz_attempts=attempts, max_steps=max_steps)
            runner = queue.QueueRunner(SimpleNamespace(evaluate=fixture.evaluate), args,
                Path(directory), store, store.course('philosophy'))
            fixture.runner = runner
            runner.prepare_quiz = AsyncMock(side_effect=fixture.prepare)
            runner.solve_quiz_questions = AsyncMock(side_effect=fixture.solve)
            runner.snapshot = AsyncMock(side_effect=grading_sequence if grading_sequence is not None else lambda:fixture.result())
            runner.open_item = AsyncMock()
            runner.verified_item = AsyncMock(return_value=True)
            error = None
            with patch.object(engine.asyncio, 'sleep', new=AsyncMock()), contextlib.redirect_stdout(io.StringIO()):
                try:
                    await runner.quiz(ITEM)
                except engine.StopRun as exc:
                    error = exc
            checkpoint = QueueStore(path, copy.deepcopy(CONFIG)).course('philosophy')['checkpoint']
            return fixture, runner, copy.deepcopy(checkpoint), error

    async def test_three_failed_attempts_then_pass_uses_feedback_and_stops(self):
        fixture, runner, checkpoint, error = await self.exercise()
        self.assertIsNone(error)
        self.assertEqual(fixture.submitted, 4)
        self.assertEqual(runner.prepare_quiz.await_count, 4)
        self.assertEqual(runner.solve_quiz_questions.await_count, 4)
        self.assertEqual(fixture.calls.count('button'), 4)
        self.assertEqual(fixture.calls.count('confirm_submit'), 4)
        self.assertEqual(fixture.calls.count('set_answers'), 4)
        self.assertEqual(fixture.feedback_seen[0], '')
        for number in (1,2,3):
            self.assertIn(f'Feedback from attempt {number}', fixture.feedback_seen[number])
        self.assertEqual(checkpoint['attempts'][KEY]['count'], 4)
        self.assertFalse(checkpoint['attempts'][KEY]['pending'])
        self.assertTrue(checkpoint['attempts'][KEY]['passed'])
        runner.verified_item.assert_awaited_once()
        submitted = [e for e in runner.data['events'] if e['event'] == 'quiz_submitted_result']
        self.assertEqual([e['attempt'] for e in submitted], [1,2,3,4])
        self.assertEqual([e['passed'] for e in submitted], [False,False,False,True])

    async def test_restart_count_two_does_not_exhaust_unlimited_mode(self):
        fixture, runner, checkpoint, error = await self.exercise(initial_count=2, failures=0)
        self.assertIsNone(error)
        self.assertEqual(fixture.submitted, 1)
        self.assertEqual(fixture.feedback_seen, ['Prior saved question feedback'])
        self.assertEqual(checkpoint['attempts'][KEY]['count'], 3)
        self.assertTrue(checkpoint['attempts'][KEY]['passed'])
        self.assertEqual(len(fixture.reservations), 1)
        self.assertEqual(runner.quiz_attempt_number(KEY, 1), 3)

    async def test_action_budget_resets_per_attempt_without_limiting_total_retries(self):
        fixture, runner, checkpoint, error = await self.exercise(max_steps=3)
        self.assertIsNone(error)
        self.assertEqual(fixture.submitted, 4)
        mutations = [action for action in fixture.calls if action != 'inspect']
        self.assertEqual(mutations, ['set_answers','button','confirm_submit'] * 4)
        self.assertEqual(checkpoint['attempts'][KEY]['count'], 4)
        self.assertTrue(checkpoint['attempts'][KEY]['passed'])

    async def test_single_attempt_action_limit_still_blocks_uncertain_extra_click(self):
        fixture, runner, checkpoint, error = await self.exercise(max_steps=2)
        self.assertIsInstance(error, engine.StopRun)
        self.assertIn('action limit', str(error))
        self.assertEqual(fixture.submitted, 0)
        self.assertNotIn('confirm_submit', fixture.calls)
        self.assertEqual(checkpoint['attempts'][KEY]['count'], 1)
        self.assertTrue(checkpoint['attempts'][KEY]['pending'])

    async def test_fresh_passing_grade_after_feedback_prevents_another_submission(self):
        for initial_feedback in (['Your grade: 0%', 'To pass you need at least 80%.'], ['Loading...']):
            with self.subTest(initial_feedback=initial_feedback):
                url = ITEM['url'] + '/attempt/view-feedback'
                grades = [{'url':url, 'feedback':initial_feedback},
                          {'url':url, 'feedback':['Your grade: 100%', 'To pass you need at least 80%.']}]
                fixture, runner, checkpoint, error = await self.exercise(grading_sequence=grades)
                self.assertIsNone(error)
                self.assertEqual(fixture.submitted, 1)
                self.assertEqual(runner.snapshot.await_count, 2)
                self.assertEqual(runner.solve_quiz_questions.await_count, 1)
                self.assertEqual([a for a in fixture.calls if a != 'inspect'],
                                 ['set_answers','button','confirm_submit'])
                self.assertTrue(checkpoint['attempts'][KEY]['passed'])
                self.assertFalse(checkpoint['attempts'][KEY]['pending'])
                submitted = [e for e in runner.data['events'] if e['event'] == 'quiz_submitted_result']
                self.assertEqual(len(submitted), 1)
                self.assertTrue(submitted[0]['passed'])
                runner.verified_item.assert_awaited_once()

    async def test_fresh_unknown_grade_stops_without_retry_and_preserves_reservation(self):
        url = ITEM['url'] + '/attempt/view-feedback'
        grades = [{'url':url, 'feedback':['Your grade: 0%', 'To pass you need at least 80%.']},
                  {'url':url, 'feedback':['To pass you need at least 80%.', 'Result is loading']}]
        fixture, runner, checkpoint, error = await self.exercise(grading_sequence=grades)
        self.assertIsInstance(error, engine.StopRun)
        self.assertEqual(fixture.submitted, 1)
        self.assertEqual(runner.snapshot.await_count, 2)
        self.assertEqual(runner.solve_quiz_questions.await_count, 1)
        self.assertEqual([a for a in fixture.calls if a != 'inspect'],
                         ['set_answers','button','confirm_submit'])
        self.assertEqual(checkpoint['attempts'][KEY]['count'], 1)
        self.assertTrue(checkpoint['attempts'][KEY]['pending'])
        self.assertFalse(any(e['event'] == 'quiz_submitted_result' for e in runner.data['events']))
        runner.verified_item.assert_not_awaited()
        runner.open_item.assert_not_awaited()

    async def test_positive_budget_remains_cumulative_across_restarts(self):
        fixture, runner, checkpoint, error = await self.exercise(attempts=2, initial_count=1, failures=3)
        self.assertIsInstance(error, engine.StopRun)
        self.assertEqual(fixture.submitted, 1)
        self.assertEqual(runner.solve_quiz_questions.await_count, 1)
        self.assertEqual(checkpoint['attempts'][KEY]['count'], 2)
        self.assertFalse(checkpoint['attempts'][KEY]['pending'])
        self.assertFalse(checkpoint['attempts'][KEY]['passed'])
        runner.verified_item.assert_not_awaited()

    async def test_unlimited_mode_never_bypasses_unresolved_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QueueStore(Path(directory) / 'queue.json', copy.deepcopy(CONFIG))
            fixture = FakeAttempts(self)
            args = SimpleNamespace(quiz_attempts=0, max_steps=100)
            runner = queue.QueueRunner(SimpleNamespace(evaluate=fixture.evaluate), args,
                Path(directory), store, store.course('philosophy'))
            fixture.runner = runner
            runner.checkpoint['attempts'][KEY] = {'count':2, 'pending':True}
            store.save()
            with self.assertRaisesRegex(engine.StopRun, 'uncertain'):
                runner.quiz_attempt_limit(KEY)
            with self.assertRaises(engine.StopRun):
                await runner.before_quiz_action(KEY, 'button', {'label':'Submit'})
            with self.assertRaisesRegex(engine.StopRun, 'unresolved'):
                await runner.quiz(ITEM)
            self.assertEqual(fixture.calls, ['inspect'])
            ledger = QueueStore(store.path, copy.deepcopy(CONFIG)).course('philosophy')['checkpoint']['attempts'][KEY]
            self.assertEqual(ledger, {'count':2, 'pending':True})

    async def test_actual_solver_reuses_persisted_text_and_score_after_control_reorder(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            store = QueueStore(path, copy.deepcopy(CONFIG))
            args = SimpleNamespace(quiz_attempts=2, max_steps=60)
            runner = queue.QueueRunner(None, args, Path(directory), store, store.course('philosophy'))
            stem = 'Which fixture option matches the observed concept?'
            def form(options):
                return {'page_text':'Question 1\n' + stem, 'controls':[
                    {'id':identity, 'type':'radio', 'name':'question-group', 'kind':'answer',
                     'question':stem, 'label':label, 'selected':False, 'in_scope':True}
                    for identity, label in options]}
            first = form([('old_control_alpha','First option'), ('old_control_beta','Second option')])
            second = form([('new_control_91','Second option'), ('new_control_92','First option')])
            payloads = []
            async def provider(payload, **kwargs):
                payloads.append(copy.deepcopy(payload))
                selected = 'old_control_alpha' if len(payloads) == 1 else 'new_control_91'
                return {'message':{'content':json.dumps({'cannot_answer':False, 'answers':[
                    {'question_id':'q1', 'selected_controls':[selected], 'reason':'Offline fixture'}]})}}
            with patch.object(engine, 'request_groq_answers', side_effect=provider) as api, \
                 contextlib.redirect_stdout(io.StringIO()):
                first_selected = await runner.solve_quiz_questions(ITEM, first, '')
                self.assertEqual(first_selected, ['old_control_alpha'])
                await runner.before_quiz_action(KEY, 'button', {'label':'Submit'})
                feedback = 'Question 1\n' + stem + '\nFirst option\nIncorrect\n0 / 1 point'
                records = engine.question_feedback(feedback)
                runner.event('quiz_feedback_ready', key=KEY, feedback_text=feedback, question_feedback=records)
                runner.event('quiz_submitted_result', key=KEY, attempt=1, passed=False,
                             feedback_text=feedback, question_feedback=records)
                reloaded = QueueStore(path, copy.deepcopy(CONFIG))
                again = queue.QueueRunner(None, args, Path(directory), reloaded, reloaded.course('philosophy'))
                self.assertEqual(again.quiz_attempt_limit(KEY), 1)
                selected = await again.solve_quiz_questions(ITEM, second, again.previous_quiz_feedback(KEY))
                self.assertEqual(selected, ['new_control_91'])
                self.assertEqual(api.await_count, 2)
            first_prompt = json.loads(payloads[0]['messages'][-1]['content'])
            second_prompt = json.loads(payloads[1]['messages'][-1]['content'])
            self.assertEqual(first_prompt['previous_attempts'], [])
            history = second_prompt['previous_attempts']
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]['question'], stem)
            prior = history[0]['attempts'][0]
            self.assertEqual(prior['selected_answers'], ['First option'])
            self.assertEqual(prior['score'], {'earned':0.0, 'possible':1.0})
            self.assertFalse(prior['full_score'])
            serialized = json.dumps(payloads[1])
            self.assertNotIn('old_control_alpha', serialized)
            self.assertNotIn('old_control_beta', serialized)
            self.assertEqual([option['id'] for option in second_prompt['questions'][0]['options']],
                             ['new_control_91','new_control_92'])
            persisted = QueueStore(path, copy.deepcopy(CONFIG)).course('philosophy')['checkpoint']
            self.assertEqual(persisted['attempts'][KEY]['count'], 1)
            self.assertFalse(persisted['attempts'][KEY]['pending'])
            self.assertEqual(len(persisted['answer_history'][KEY]), 1)
            prior_question = persisted['answer_history'][KEY][0]['questions'][0]
            self.assertEqual(prior_question['selected_answers'], ['First option'])
            self.assertEqual(prior_question['score'], {'earned':0.0, 'possible':1.0})


class UntilPassConfigurationTests(unittest.TestCase):
    def tearDown(self):
        engine.configure_course(URL)

    def test_zero_cli_budget_validates_without_browser_or_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'courses.txt'
            path.write_text(URL + '\n', encoding='utf-8')
            with patch.object(sys, 'argv', ['course_queue.py', '--quiz-attempts', '0', '--validate', '--courses', str(path)]), \
                 patch.object(engine, 'groq_key', side_effect=AssertionError('No provider key access')), \
                 patch.object(engine, 'ensure_browser', side_effect=AssertionError('No browser access')), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(queue.main(), 0)

    def test_legacy_engine_keeps_two_attempt_default_but_explicit_zero_is_unlimited(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = engine.CourseRunner(None, SimpleNamespace(max_steps=60), Path(directory))
            unlimited = engine.CourseRunner(None, SimpleNamespace(max_steps=60, quiz_attempts=0), Path(directory))
            self.assertEqual(legacy.quiz_attempt_limit(KEY), 2)
            self.assertIsNone(unlimited.quiz_attempt_limit(KEY))


if __name__ == '__main__':
    unittest.main(verbosity=2)
