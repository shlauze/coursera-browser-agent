"""Offline queue and engine-hook tests: no Chrome, provider calls or keys."""
import copy
import json
from pathlib import Path
import subprocess
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
KEY = 'assignment:test'
ARGS = SimpleNamespace(minutes=1, max_steps=60, quiz_attempts=2, continue_on_attention=False)
CERTIFICATE_ROOT = 'https://www.coursera.org/account/accomplishments/verify/'


def certificate_url(identity):
    return CERTIFICATE_ROOT + 'TEST-' + identity.upper()


async def opened_certificate(tab, url=None):
    return url


async def matching_certificate(tab, url, identity, title=None):
    return {'verified':True, 'method':'certificate', 'course_id':identity,
            'url':url, 'evidence':[identity, 'Completed by Learner']}


def configs():
    return [{'id':slug, 'url':'https://www.coursera.org/learn/' + slug,
             'title':slug, 'modules':None} for slug in ['philosophy','another-course']]


def make_runner(directory, args=None, tab=None):
    store = QueueStore(Path(directory) / 'state.json', configs()[:1])
    runner = queue.QueueRunner(tab, args or copy.copy(ARGS), Path(directory), store, store.course('philosophy'))
    return runner, store


def persisted_ledger(store):
    reloaded = QueueStore(store.path, configs()[:1])
    return reloaded.course('philosophy')['checkpoint']['attempts'][KEY]


class EngineConfigTests(unittest.TestCase):
    def tearDown(self):
        engine.configure_course(URL)

    def test_all_templates_switch_guards_and_restore_without_accumulation(self):
        originals = dict(engine._COURSE_SCRIPT_TEMPLATES)
        engine.configure_course('https://coursera.org/learn/another-course/')
        self.assertTrue(engine.is_course_url('https://www.coursera.org/learn/another-course/home'))
        self.assertFalse(engine.is_course_url(URL + '/home'))
        self.assertEqual(engine.MODULE_ITEM_COUNTS, {})
        for name, original in originals.items():
            actual = getattr(engine, name)
            self.assertNotIn('/learn/philosophy/', actual, name)
            self.assertNotIn("'philosophy'", actual, name)
        output = subprocess.run(['node', str(ROOT / 'tests/quiz_transition_dom.js')],
            input=json.dumps({'code':engine.QUIZ_UI_JS, 'notice':engine.AUTHORIZED_QUIZ_NOTICE,
                'cases':[{'ui':{'url':f'https://www.coursera.org/learn/{slug}/assignment-submission/UAOb6/attempt'},
                          'key':'assignment:UAOb6'} for slug in ['philosophy','another-course']]}),
            capture_output=True, text=True, encoding='utf-8', check=True)
        old, current = json.loads(output.stdout)
        self.assertEqual(old['result']['error'], 'Wrong quiz page.')
        self.assertNotIn('error', current['result'])
        engine.configure_course(URL)
        self.assertEqual(engine.MODULE_ITEM_COUNTS, engine._PHILOSOPHY_COUNTS)
        for name, original in originals.items():
            self.assertEqual(getattr(engine, name), original)

    def test_first_submit_refuses_selection_race_even_with_unchanged_form_hash(self):
        request = {'action':'button', 'label':'Submit', 'expected_selected':['c1']}
        output = subprocess.run(['node', str(ROOT / 'tests/second_submit_dom.js')],
            input=json.dumps({'code':engine.QUIZ_UI_JS, 'notice':engine.AUTHORIZED_QUIZ_NOTICE,
                             'cases':[{'noPopup':True, 'followup':request}]}),
            capture_output=True, text=True, encoding='utf-8', check=True)
        result = json.loads(output.stdout)[0]
        self.assertIn('Selected answers changed', result['result']['error'])
        self.assertEqual(result['clicks'], [])


class AttemptLedgerTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        engine.configure_course(URL)

    async def test_lab_and_dialogue_require_green_icon_as_well_as_completed_label(self):
        engine.configure_course(URL)
        for kind in ('ungradedLab', 'coach'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                item = {'key':kind + ':sample', 'title':'Sample',
                        'url':URL + '/' + kind + '/sample/lesson'}
                row = {'url':item['url'], 'aria_label':'Completed',
                       'evidence':['aria-label: Completed'], 'checked':False}
                tab = SimpleNamespace(evaluate=AsyncMock(side_effect=lambda _: {
                    'url':item['url'], 'items':[dict(row)], 'ready':'complete'}))
                runner, _ = make_runner(directory, tab=tab)
                self.assertFalse(await runner.verified_item(item))
                row['checked'] = True
                self.assertTrue(await runner.verified_item(item))

    async def test_reserve_is_durable_and_confirmation_does_not_double_count(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store = make_runner(directory)
            await runner.before_quiz_action(KEY, 'button', {'label':'Submit'})
            ledger = persisted_ledger(store)
            self.assertEqual(ledger['count'], 1)
            self.assertTrue(ledger['pending'])
            await runner.before_quiz_action(KEY, 'confirm_submit', {'label':'Submit'})
            self.assertEqual(runner.checkpoint['attempts'][KEY]['count'], 1)
            with self.assertRaisesRegex(engine.StopRun, 'uncertain'):
                runner.quiz_attempt_limit(KEY)

    async def test_resumed_draft_first_submit_confirmation_and_result_use_one_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store = make_runner(directory)
            runner.checkpoint['attempts'][KEY] = {
                'count':1, 'pending':True, 'submit_phase':'confirmation_observed'}
            runner._resuming_quiz_draft_key = KEY
            store.save()

            await runner.before_quiz_action(KEY, 'button', {'label':'Submit'})
            self.assertEqual(persisted_ledger(store)['count'], 1)
            await runner.after_quiz_action(KEY, 'button', {'label':'Submit'}, {'clicked':'Submit'})
            self.assertEqual(persisted_ledger(store)['count'], 1)
            await runner.after_quiz_action(KEY, 'inspect', {}, {'dialog_kind':'submit_confirmation'})
            self.assertEqual(persisted_ledger(store)['submit_phase'], 'confirmation_observed')
            await runner.before_quiz_action(KEY, 'confirm_submit', {'label':'Submit'})
            await runner.after_quiz_action(KEY, 'confirm_submit', {'label':'Submit'}, {'clicked':'Submit'})
            self.assertEqual(persisted_ledger(store)['count'], 1)
            self.assertEqual(persisted_ledger(store)['submit_phase'], 'confirm_clicked')

            runner.event('quiz_submitted_result', key=KEY, attempt=1, passed=True,
                         feedback_text='Your grade: 100%', question_feedback=[])
            ledger = persisted_ledger(store)
            self.assertEqual(ledger['count'], 1)
            self.assertFalse(ledger['pending'])
            self.assertTrue(ledger['passed'])

    async def test_preclick_error_restores_reviewed_feedback_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store = make_runner(directory)
            runner.checkpoint['attempts'][KEY] = {
                'count':1, 'pending':False, 'passed':False,
                'feedback_ready':True, 'feedback_ready_count':1}
            store.save()
            await runner.before_quiz_action(KEY, 'button', {'label':'Submit'})
            self.assertEqual(persisted_ledger(store)['count'], 2)
            await runner.after_quiz_action(KEY, 'button', {'label':'Submit'},
                                           {'error':'Submit changed before the click'})
            ledger = persisted_ledger(store)
            self.assertEqual(ledger['count'], 1)
            self.assertFalse(ledger['pending'])
            self.assertTrue(ledger['feedback_ready'])
            self.assertEqual(ledger['feedback_ready_count'], 1)
            self.assertEqual(runner.quiz_attempt_limit(KEY), 1)

    async def test_known_preclick_error_releases_but_uncertain_dispatch_keeps_reservation(self):
        for uncertain in [False, True]:
            with self.subTest(uncertain=uncertain), tempfile.TemporaryDirectory() as directory:
                runner, store = make_runner(directory)
                form = {'url':URL + '/assignment-submission/test/attempt', 'snapshot_id':'form',
                    'answer_snapshot_id':'answers', 'editable_form':True, 'blocking_dialog':False,
                    'controls':[{'id':'c0', 'kind':'answer', 'type':'radio', 'name':'q1',
                                 'question':'Question one', 'label':'Option A', 'selected':True}],
                    'buttons':[{'label':'Submit', 'workflow':'submit', 'disabled':False}], 'page_text':'Question 1'}
                runner.prepare_quiz = AsyncMock(return_value=form)
                runner.solve_quiz_questions = AsyncMock(return_value=['c0'])
                async def evaluate(expression):
                    request = json.loads(expression[len(engine.QUIZ_UI_JS) + 1:-1])
                    if request['action'] == 'button':
                        reserved = persisted_ledger(store)
                        self.assertTrue(reserved['pending'])
                        self.assertEqual(reserved['count'], 1)
                        self.assertEqual(request['expected_selected'], ['c0'])
                        self.assertEqual(request['snapshot_id'], 'form')
                        if uncertain:
                            raise RuntimeError('connection lost after dispatch')
                        return {'error':'Selection changed before click', 'error_code':'stale_selection'}
                    return {**copy.deepcopy(form), 'verified':True}
                runner.tab = SimpleNamespace(evaluate=evaluate)
                with self.assertRaises((engine.StopRun, RuntimeError)):
                    await runner.quiz({'key':KEY})
                ledger = persisted_ledger(store)
                self.assertEqual(ledger['pending'], uncertain)
                self.assertEqual(ledger['count'], int(uncertain))
                self.assertEqual(runner.solve_quiz_questions.await_count, 1)

    async def test_restart_preserves_feedback_remaining_budget_and_missing_feedback_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store = make_runner(directory)
            await runner.before_quiz_action(KEY, 'button', {'label':'Submit'})
            runner.event('quiz_feedback_ready', key=KEY, feedback_text='Question 1\nIncorrect')
            runner.event('quiz_submitted_result', key=KEY, attempt=1, passed=False,
                         feedback_text='Question 1\nIncorrect', question_feedback=[{'question_number':1, 'text':'Incorrect'}])
            restarted = QueueStore(store.path, configs()[:1])
            again = queue.QueueRunner(None, copy.copy(ARGS), Path(directory), restarted, restarted.course('philosophy'))
            self.assertEqual(again.previous_quiz_feedback(KEY), 'Question 1\nIncorrect')
            self.assertEqual(again.quiz_attempt_limit(KEY), 1)
            await again.before_quiz_action(KEY, 'button', {'label':'Submit'})
            again.event('quiz_submitted_result', key=KEY, attempt=1, passed=False, question_feedback=[])
            with self.assertRaisesRegex(engine.StopRun, 'no detailed feedback'):
                again.quiz_attempt_limit(KEY)
            self.assertEqual(again.checkpoint['attempts'][KEY]['count'], 2)

    async def test_unsupported_assignment_row_is_never_dispatched(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, _ = make_runner(directory)
            row = {'key':KEY, 'title':'Team assignment', 'url':URL + '/team/test',
                   'type':'team', 'unsupported':True, 'unsupported_reason':'Team assignment requires manual work'}
            runner.open_item = AsyncMock()
            runner.verified_item = AsyncMock(return_value=False)
            runner.quiz = AsyncMock(side_effect=AssertionError('unsupported assignment dispatched'))
            await runner.process_module(1, [row])
            runner.quiz.assert_not_awaited()
            runner.open_item.assert_not_awaited()
            module = runner.checkpoint['modules']['1']
            self.assertFalse(module['verified_complete'])
            self.assertFalse(module['items'][KEY]['verified_complete'])
            self.assertEqual(module['manual_required'], ['Team assignment'])

    async def test_unsupported_lab_does_not_block_later_supported_quiz(self):
        with tempfile.TemporaryDirectory() as directory:
            engine.configure_course(URL)
            runner, store = make_runner(directory)
            reading = {'key':'supplement:intro', 'title':'Introduction Reading',
                       'url':URL + '/supplement/intro/reading'}
            lab = {'key':'assignment:lab', 'title':'Practice Lab',
                   'url':URL + '/team/lab', 'type':'team', 'unsupported':True,
                   'unsupported_reason':'Practice Lab needs manual work'}
            graded = {'key':'assignment:final', 'title':'Final Graded Assignment',
                      'url':URL + '/assignment-submission/final/quiz'}
            runner.open_item = AsyncMock()
            runner.verified_item = AsyncMock(return_value=False)
            runner.reading = AsyncMock()
            runner.quiz = AsyncMock()
            runner.snapshot = AsyncMock(return_value={'url':URL + '/home/module/1'})
            final_rows = {reading['key']:{'status':'completed'}, graded['key']:{'status':'completed'}}
            with patch.object(engine, 'rows_by_key', return_value=final_rows):
                await runner.process_module(1, [reading, lab, graded])
            runner.reading.assert_awaited_once_with(reading)
            runner.quiz.assert_awaited_once_with(graded)
            self.assertNotIn(lab, [call.args[0] for call in runner.open_item.await_args_list])
            module = runner.checkpoint['modules']['1']
            self.assertFalse(module['verified_complete'])
            self.assertFalse(module['items'][lab['key']]['verified_complete'])
            self.assertTrue(module['items'][reading['key']]['verified_complete'])
            self.assertTrue(module['items'][graded['key']]['verified_complete'])
            self.assertEqual(module['manual_required'], ['Practice Lab'])
            self.assertEqual(store.course('philosophy')['modules_completed'], 0)
            self.assertEqual(store.course('philosophy')['items_completed'], 2)
            self.assertNotIn('module_completed', [event['kind'] for event in store.data['events']])

    async def test_lab_and_dialogue_dispatch_require_final_sidebar_mark(self):
        for kind, method in [('ungradedLab', 'lab'), ('coach', 'dialogue')]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                engine.configure_course(URL)
                runner, store = make_runner(directory)
                item = {'key':kind + ':sample', 'title':'Sample lesson',
                        'url':URL + '/' + kind + '/sample/lesson', 'unsupported':False}
                runner.open_item = AsyncMock()
                runner.verified_item = AsyncMock(return_value=False)
                setattr(runner, method, AsyncMock())
                runner.snapshot = AsyncMock(return_value={'url':item['url']})
                with patch.object(engine, 'rows_by_key', return_value={item['key']:{'status':'completed'}}):
                    await runner.process_module(1, [item])
                getattr(runner, method).assert_awaited_once_with(item)
                self.assertTrue(runner.checkpoint['modules']['1']['verified_complete'])
                self.assertEqual(store.course('philosophy')['items_completed'], 1)

    async def test_unsupported_item_status_alone_does_not_claim_green_module(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store = make_runner(directory)
            peer = {'key':'peer:review', 'title':'Peer review',
                    'url':URL + '/peer/review', 'unsupported':True}
            runner.open_item = AsyncMock()
            runner.verified_item = AsyncMock(return_value=True)
            runner.snapshot = AsyncMock(return_value={'url':URL + '/home/module/1'})
            with patch.object(engine, 'rows_by_key', return_value={}):
                await runner.process_module(1, [peer])
            runner.open_item.assert_not_awaited()
            module = runner.checkpoint['modules']['1']
            self.assertTrue(module['items'][peer['key']]['verified_complete'])
            self.assertFalse(module['verified_complete'])
            self.assertEqual(store.course('philosophy')['modules_completed'], 0)

    async def test_already_green_unsupported_item_requires_final_outline_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store = make_runner(directory)
            peer = {'key':'peer:review', 'title':'Peer review',
                    'url':URL + '/peer/review', 'unsupported':True}
            runner.open_item = AsyncMock()
            runner.verified_item = AsyncMock(return_value=True)
            runner.snapshot = AsyncMock(return_value={'url':URL + '/home/module/1'})
            with patch.object(engine, 'rows_by_key', return_value={peer['key']:{'status':'completed'}}):
                await runner.process_module(1, [peer])
            self.assertTrue(runner.checkpoint['modules']['1']['verified_complete'])
            self.assertEqual(store.course('philosophy')['modules_completed'], 1)

    async def test_site_cooldown_extends_course_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store = make_runner(directory)
            async with engine.asyncio.timeout(0.04) as budget:
                runner._course_timeout = budget
                await runner.on_quiz_cooldown(0.10)
                await engine.asyncio.sleep(0.06)
            self.assertIn('Ожидание', store.course('philosophy')['message'])


class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        engine.configure_course(URL)

    def prepare(self, directory, ledger=None, passed=False):
        feedback = 'Question 1\nA question\nIncorrect\n0 / 1 point'
        current = {'url':URL + '/assignment-submission/test/attempt/view-feedback',
                   'result_page':True, 'blocking_dialog':False, 'editable_form':False,
                   'page_text':feedback,
                   'controls':[{'kind':'answer', 'name':'q1', 'question':'A question', 'disabled':True}],
                   'buttons':[]}
        tab = SimpleNamespace(evaluate=AsyncMock(return_value=current), navigate=AsyncMock())
        runner, store = make_runner(directory, tab=tab)
        if ledger is not None:
            runner.checkpoint['attempts'][KEY] = copy.deepcopy(ledger)
            store.save()
        runner.snapshot = AsyncMock(return_value={'url':current['url'],
            'feedback':['Your grade: ' + ('100%' if passed else '0%'), 'To pass you need at least 80%.']})
        runner.open_item = AsyncMock()
        runner.verified_item = AsyncMock(return_value=passed)
        return runner, store, current

    async def test_crash_pending_failed_result_reconciles_without_clicks_or_refunding_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store, _ = self.prepare(directory, {'count':1, 'pending':True, 'question_count':1})
            with patch.object(engine.CourseRunner, 'quiz', new=AsyncMock()) as resume:
                await runner.quiz({'key':KEY})
            ledger = persisted_ledger(store)
            self.assertEqual(ledger['count'], 1)
            self.assertFalse(ledger['pending'])
            self.assertTrue(ledger['feedback_ready'])
            self.assertEqual(runner.quiz_attempt_limit(KEY), 1)
            self.assertIn('Incorrect', runner.previous_quiz_feedback(KEY))
            resume.assert_awaited_once()
            for call in runner.tab.evaluate.await_args_list:
                request = json.loads(call.args[0][len(engine.QUIZ_UI_JS) + 1:-1])
                self.assertEqual(request['action'], 'inspect')

    async def test_unresolved_editable_form_never_clears_pending_or_starts_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store, current = self.prepare(directory, {'count':1, 'pending':True})
            current.update(result_page=False, editable_form=True)
            with patch.object(engine.CourseRunner, 'quiz', new=AsyncMock()) as resume:
                with self.assertRaisesRegex(engine.StopRun, 'unresolved'):
                    await runner.quiz({'key':KEY})
            self.assertTrue(persisted_ledger(store)['pending'])
            self.assertEqual(persisted_ledger(store)['count'], 1)
            resume.assert_not_awaited()
            runner.open_item.assert_not_awaited()

    async def test_restart_resumes_confirmed_unsubmitted_draft_without_new_reservation(self):
        for landing in (False, True):
            with self.subTest(landing=landing), tempfile.TemporaryDirectory() as directory:
                runner, store, current = self.prepare(directory,
                    {'count':1, 'pending':True, 'submit_phase':'confirmation_observed'})
                current.update(url=URL + '/assignment-submission/test' + ('' if landing else '/attempt'),
                    result_page=False, editable_form=not landing, blocking_dialog=False,
                    page_text='Resume assignment\n2 of 3 attempts' if landing else 'Question 1\nSubmit',
                    buttons=([{'label':'Resume assignment', 'workflow':'entry', 'disabled':False}]
                             if landing else [{'label':'Submit', 'workflow':'submit', 'disabled':False}]))
                restarted = QueueStore(store.path, configs()[:1])
                again = queue.QueueRunner(runner.tab, copy.copy(ARGS), Path(directory), restarted,
                    restarted.course('philosophy'))
                with patch.object(engine.CourseRunner, 'quiz', new=AsyncMock()) as resume:
                    await again.quiz({'key':KEY})
                resume.assert_awaited_once()
                ledger = persisted_ledger(restarted)
                self.assertEqual(ledger['count'], 1)
                self.assertTrue(ledger['pending'])
                self.assertEqual(ledger['submit_phase'], 'confirmation_observed')

    async def test_previous_feedback_link_does_not_replace_pending_draft_result(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store, current = self.prepare(directory,
                {'count':2, 'pending':True, 'submit_phase':'confirmation_observed'})
            current.update(url=URL + '/assignment-submission/test', result_page=False,
                editable_form=False, blocking_dialog=False,
                page_text='Resume assignment\nView feedback\n1 of 3 attempts',
                buttons=[{'label':'Resume assignment', 'workflow':'entry', 'disabled':False},
                         {'label':'View feedback', 'workflow':'other', 'disabled':False}])
            with patch.object(engine.CourseRunner, 'quiz', new=AsyncMock()) as resume:
                await runner.quiz({'key':KEY})
            resume.assert_awaited_once()
            requests = [json.loads(call.args[0][len(engine.QUIZ_UI_JS) + 1:-1])
                        for call in runner.tab.evaluate.await_args_list]
            self.assertEqual([request['action'] for request in requests], ['inspect'])
            self.assertEqual(persisted_ledger(store)['count'], 2)
            self.assertTrue(persisted_ledger(store)['pending'])

    async def test_old_result_cannot_clear_draft_without_final_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store, _ = self.prepare(directory,
                {'count':2, 'pending':True, 'submit_phase':'confirmation_observed'})
            with patch.object(engine.CourseRunner, 'quiz', new=AsyncMock()) as resume:
                with self.assertRaisesRegex(engine.StopRun, "final Submit was never clicked"):
                    await runner.quiz({'key':KEY})
            resume.assert_not_awaited()
            self.assertEqual(persisted_ledger(store)['count'], 2)
            self.assertTrue(persisted_ledger(store)['pending'])

    async def test_restart_does_not_assume_first_click_finished_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store, current = self.prepare(directory,
                {'count':1, 'pending':True, 'submit_phase':'first_clicked'})
            current.update(url=URL + '/assignment-submission/test/attempt',
                result_page=False, editable_form=True, blocking_dialog=False)
            with patch.object(engine.CourseRunner, 'quiz', new=AsyncMock()) as resume:
                with self.assertRaises(engine.StopRun):
                    await runner.quiz({'key':KEY})
            resume.assert_not_awaited()
            self.assertEqual(persisted_ledger(store)['count'], 1)
            self.assertTrue(persisted_ledger(store)['pending'])

    async def test_feedback_timeout_from_prior_run_can_recover_when_details_appear(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store, _ = self.prepare(directory, {'count':1, 'pending':False,
                'passed':False, 'feedback_ready':False, 'question_count':1})
            with patch.object(engine.CourseRunner, 'quiz', new=AsyncMock()) as resume:
                await runner.quiz({'key':KEY})
            self.assertTrue(persisted_ledger(store)['feedback_ready'])
            self.assertEqual(persisted_ledger(store)['count'], 1)
            resume.assert_awaited_once()

    async def test_restart_opens_observed_feedback_control_before_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store, current = self.prepare(directory,
                {'count':1, 'pending':False, 'passed':False, 'feedback_ready':False,
                 'question_count':1})
            result_url = current['url'].replace('/view-feedback', '')
            result_page = {**current, 'url':result_url,
                           'buttons':[{'label':'Review feedback', 'disabled':False}]}
            actions = []
            async def evaluate(expression):
                request = json.loads(expression[len(engine.QUIZ_UI_JS) + 1:-1])
                actions.append(request['action'])
                if request['action'] == 'button':
                    self.assertEqual(request['label'], 'Review feedback')
                    return {'url':result_url, 'clicked':'Review feedback'}
                return result_page if 'button' not in actions else current
            runner.tab.evaluate = evaluate
            with patch.object(engine.asyncio, 'sleep', new=AsyncMock()), \
                 patch.object(engine.CourseRunner, 'quiz', new=AsyncMock()) as resume:
                await runner.quiz({'key':KEY})
            self.assertEqual(actions.count('button'), 1)
            runner.tab.navigate.assert_not_awaited()
            self.assertTrue(persisted_ledger(store)['feedback_ready'])
            resume.assert_awaited_once()

    async def test_partial_question_records_do_not_mark_full_feedback_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store = make_runner(directory)
            await runner.before_quiz_action(KEY, 'button', {'label':'Submit'})
            runner.event('quiz_submitted_result', key=KEY, attempt=1, passed=False,
                         feedback_text='Question 1\nIncorrect', question_feedback=[{'question_number':1}])
            self.assertFalse(persisted_ledger(store)['feedback_ready'])

    async def test_partial_records_without_known_question_total_do_not_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store, current = self.prepare(directory, {'count':1, 'pending':True})
            current['controls'] = []
            current['page_text'] += '\nQuestion 2\nAnother question\nIncorrect\n0 / 1 point'
            with patch.object(engine.CourseRunner, 'quiz', new=AsyncMock()) as resume:
                with self.assertRaisesRegex(engine.StopRun, 'question feedback is not readable'):
                    await runner.quiz({'key':KEY})
            ledger = persisted_ledger(store)
            self.assertFalse(ledger['feedback_ready'])
            self.assertEqual(ledger['count'], 1)
            resume.assert_not_awaited()
            runner.open_item.assert_not_awaited()

    async def test_legacy_visible_result_supplies_feedback_without_invented_prior_count(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, store, _ = self.prepare(directory)
            with patch.object(engine.CourseRunner, 'quiz', new=AsyncMock()) as resume:
                await runner.quiz({'key':KEY})
            ledger = persisted_ledger(store)
            self.assertEqual(ledger['count'], 0)
            self.assertTrue(ledger['feedback_ready'])
            self.assertEqual(runner.quiz_attempt_limit(KEY), 2)
            resume.assert_awaited_once()

    async def test_passing_result_requires_saved_site_completion_and_never_resubmits(self):
        for marked in (False, True):
            with self.subTest(marked=marked), tempfile.TemporaryDirectory() as directory:
                runner, store, _ = self.prepare(directory, {'count':1, 'pending':True}, passed=True)
                runner.verified_item.return_value = marked
                with patch.object(engine.CourseRunner, 'quiz', new=AsyncMock()) as resume:
                    if marked:
                        await runner.quiz({'key':KEY})
                    else:
                        with self.assertRaisesRegex(engine.StopRun, 'completion mark'):
                            await runner.quiz({'key':KEY})
                resume.assert_not_awaited()
                self.assertEqual(persisted_ledger(store)['count'], 1)


class QueueLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        engine.configure_course(URL)

    async def test_roster_failure_preserves_current_module_and_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QueueStore(Path(directory) / 'state.json', configs()[:1])
            failure = engine.StopRun('Module outline needs review')
            failure.diagnostic = {'items':[{'title':'Observed reading'}, {'title':'Observed quiz'}],
                                  'ready':'complete', 'module':1}
            with patch.object(queue, 'ROOT', Path(directory)), \
                 patch.object(queue, 'discover_modules', AsyncMock(return_value=[1])), \
                 patch.object(queue, 'discover_roster', AsyncMock(side_effect=failure)), \
                 patch.object(queue.QueueRunner, 'process_module', AsyncMock()) as process, \
                 patch.object(queue, 'verify_course_completion', AsyncMock()) as completion:
                result = await queue.run_queue(store, copy.copy(ARGS), SimpleNamespace())
            self.assertEqual(result, 2)
            self.assertEqual(store.data['status'], 'attention')
            self.assertEqual(store.course('philosophy')['current_module'], 1)
            self.assertEqual(store.course('philosophy')['status'], 'attention')
            run = Path(store.course('philosophy')['run_directory'])
            progress = json.loads((run / 'progress.json').read_text(encoding='utf-8'))
            events = [json.loads(line) for line in (run / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            for recorded in [progress['events'], events]:
                saved = next(event for event in recorded if event['event'] == 'discovery_error')
                self.assertEqual(saved['diagnostic'], failure.diagnostic)
                self.assertEqual(len(saved['diagnostic']['items']), 2)
            process.assert_not_awaited()
            completion.assert_not_awaited()

    async def run_case(self, proofs, continue_attention=False, cancelled=False,
                       auto_my_learning=False, certificates=False):
        with tempfile.TemporaryDirectory() as directory:
            store = QueueStore(Path(directory) / 'state.json', configs())
            if certificates:
                for course in store.data['courses']:
                    course['certificate_page_url'] = certificate_url(course['id'])
                store.save()
            args = copy.copy(ARGS)
            args.continue_on_attention = continue_attention
            args.auto_my_learning = auto_my_learning
            process = AsyncMock(side_effect=engine.asyncio.CancelledError() if cancelled else None)
            discover = AsyncMock(return_value=[1])
            verify = AsyncMock(side_effect=proofs)
            with patch.object(queue, 'ROOT', Path(directory)), \
                 patch.object(queue, 'discover_modules', discover), \
                 patch.object(queue, 'discover_roster', AsyncMock(return_value=[{'key':'supplement:test', 'title':'Reading'}])), \
                 patch.object(queue.QueueRunner, 'process_module', process), \
                 patch.object(queue, 'verify_course_completion', verify), \
                 patch.object(queue, 'open_certificate_page', new=opened_certificate), \
                 patch.object(queue, 'verify_saved_certificate', new=matching_certificate), \
                 patch.object(queue, 'open_my_learning', AsyncMock()), \
                 patch.object(queue, '_discover_with_retry', AsyncMock(return_value=None)):
                if cancelled:
                    with self.assertRaises(engine.asyncio.CancelledError):
                        await queue.run_queue(store, args, SimpleNamespace())
                    result = None
                else:
                    result = await queue.run_queue(store, args, SimpleNamespace())
            return result, copy.deepcopy(store.data), discover.await_count, verify.await_count

    async def test_advances_after_matching_certificates_are_verified(self):
        result, data, discoveries, verifications = await self.run_case([
            {'verified':True, 'evidence':['You completed this course']},
            {'verified':True, 'evidence':['You completed this course']}],
            certificates=True)
        self.assertEqual((result, discoveries, verifications), (0,2,2))
        self.assertEqual(data['status'], 'completed')
        self.assertTrue(all(c['status'] == 'completed' for c in data['courses']))
        self.assertTrue(all(c['completion_method'] == 'certificate' for c in data['courses']))
        self.assertEqual([e['kind'] for e in data['events'] if e['kind'] in ['course_completed', 'course_started']],
                         ['course_started', 'course_completed', 'course_started', 'course_completed'])

    async def test_unverified_completion_stops_before_next_course(self):
        result, data, discoveries, _ = await self.run_case([{'verified':False, 'reason':'no whole-course proof'}])
        self.assertEqual(result, 2)
        self.assertEqual(discoveries, 1)
        self.assertEqual([c['status'] for c in data['courses']], ['attention','pending'])
        self.assertNotIn('queue_completed', [e['kind'] for e in data['events']])

    async def test_green_outline_and_completion_text_without_certificate_do_not_advance(self):
        result, data, discoveries, verifications = await self.run_case([
            {'verified':True, 'method':'sidebar', 'evidence':['All lessons completed']}])
        self.assertEqual((result, discoveries, verifications), (2, 1, 1))
        self.assertEqual([c['status'] for c in data['courses']], ['attention', 'pending'])
        self.assertIn('certificate page is not available', data['courses'][0]['message'])
        self.assertNotIn('course_completed', [e['kind'] for e in data['events']])

    async def test_matching_certificate_can_complete_after_course_text_lags(self):
        result, data, discoveries, verifications = await self.run_case([
            {'verified':False, 'reason':'Course page has not refreshed'},
            {'verified':False, 'reason':'Course page has not refreshed'}],
            certificates=True)
        self.assertEqual((result, discoveries, verifications), (0, 2, 2))
        self.assertTrue(all(c['status'] == 'completed' for c in data['courses']))
        self.assertTrue(all(c['completion_method'] == 'certificate' for c in data['courses']))

    async def test_continue_flag_cannot_skip_missing_certificate(self):
        result, data, discoveries, _ = await self.run_case([
            {'verified':False, 'reason':'manual task'}, {'verified':True, 'evidence':['Course completed']}], True)
        self.assertEqual((result, discoveries), (2,1))
        self.assertEqual([c['status'] for c in data['courses']], ['attention','pending'])
        self.assertEqual(data['status'], 'attention')
        self.assertNotIn('queue_completed', [e['kind'] for e in data['events']])

    async def test_auto_my_learning_stops_on_attention_without_explicit_continue(self):
        result, data, discoveries, verifications = await self.run_case(
            [{'verified':False, 'reason':'Optional assignment needs review'}],
            auto_my_learning=True)
        self.assertEqual((result, discoveries, verifications), (2, 1, 1))
        self.assertEqual([c['status'] for c in data['courses']], ['attention', 'pending'])
        self.assertEqual(data['status'], 'attention')
        self.assertNotIn('queue_completed', [e['kind'] for e in data['events']])

    async def test_auto_my_learning_continue_flag_cannot_bypass_certificate(self):
        result, data, discoveries, verifications = await self.run_case([
            {'verified':False, 'reason':'Optional assignment needs review'},
            {'verified':True, 'evidence':['Course completed']}],
            continue_attention=True, auto_my_learning=True)
        self.assertEqual((result, discoveries, verifications), (2, 1, 1))
        self.assertEqual([c['status'] for c in data['courses']], ['attention', 'pending'])
        self.assertEqual(data['status'], 'attention')
        self.assertNotIn('queue_completed', [e['kind'] for e in data['events']])

    async def test_cancellation_saves_paused_and_never_dispatches_next_course(self):
        _, data, discoveries, verifications = await self.run_case([], cancelled=True)
        self.assertEqual((discoveries, verifications), (1,0))
        self.assertEqual(data['status'], 'paused')
        self.assertEqual(data['courses'][1]['status'], 'pending')
        self.assertIn('queue_paused', [e['kind'] for e in data['events']])

    async def test_new_course_added_during_current_course_runs_next(self):
        with tempfile.TemporaryDirectory() as directory:
            course_file = Path(directory) / 'courses.txt'
            course_file.write_text(URL + '\n', encoding='utf-8')
            store = QueueStore(Path(directory) / 'state.json', configs()[:1])
            args = copy.copy(ARGS)
            args.courses = course_file
            order = []
            async def proof(tab, url, modules, rosters):
                order.append(url)
                if url == URL:
                    course_file.write_text(URL + '\nhttps://www.coursera.org/learn/another-course\n', encoding='utf-8')
                return {'verified':True, 'evidence':['Course completed']}
            async def process(runner, number, roster):
                runner.course['certificate_page_url'] = certificate_url(runner.course['id'])
            with patch.object(queue, 'ROOT', Path(directory)), \
                 patch.object(queue, 'discover_modules', AsyncMock(return_value=[1])), \
                 patch.object(queue, 'discover_roster', AsyncMock(return_value=[{'key':'supplement:test','title':'Reading'}])), \
                 patch.object(queue.QueueRunner, 'process_module', process), \
                 patch.object(queue, 'verify_course_completion', proof), \
                 patch.object(queue, 'open_certificate_page', new=opened_certificate), \
                 patch.object(queue, 'verify_saved_certificate', new=matching_certificate):
                result = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(result, 0)
            self.assertEqual(order, [URL, 'https://www.coursera.org/learn/another-course'])
            self.assertEqual([c['status'] for c in store.data['courses']], ['completed', 'completed'])

    async def test_removed_pending_course_is_not_started(self):
        with tempfile.TemporaryDirectory() as directory:
            course_file = Path(directory) / 'courses.txt'
            course_file.write_text(URL + '\nhttps://www.coursera.org/learn/another-course\n', encoding='utf-8')
            store = QueueStore(Path(directory) / 'state.json', configs())
            args = copy.copy(ARGS)
            args.courses = course_file
            async def proof(tab, url, modules, rosters):
                course_file.write_text(URL + '\n', encoding='utf-8')
                return {'verified':True, 'evidence':['Course completed']}
            async def process(runner, number, roster):
                runner.course['certificate_page_url'] = certificate_url(runner.course['id'])
            with patch.object(queue, 'ROOT', Path(directory)), \
                 patch.object(queue, 'discover_modules', AsyncMock(return_value=[1])) as discover, \
                 patch.object(queue, 'discover_roster', AsyncMock(return_value=[{'key':'supplement:test','title':'Reading'}])), \
                 patch.object(queue.QueueRunner, 'process_module', process), \
                 patch.object(queue, 'verify_course_completion', proof), \
                 patch.object(queue, 'open_certificate_page', new=opened_certificate), \
                 patch.object(queue, 'verify_saved_certificate', new=matching_certificate):
                result = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(result, 0)
            self.assertEqual(discover.await_count, 1)
            self.assertEqual([c['id'] for c in store.data['courses']], ['philosophy'])

    async def test_verified_certificate_skips_optional_unsupported_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QueueStore(Path(directory) / 'state.json', configs()[:1])
            async def roster(tab, url, number, expected):
                if number == 1:
                    return [{'key':'supplement:first', 'title':'Reading'}]
                return [{'key':'peer:optional', 'title':'Optional peer review', 'unsupported':True}]
            async def process(runner, number, roster):
                runner.course['certificate_page_url'] = certificate_url(runner.course['id'])
            with patch.object(queue, 'ROOT', Path(directory)), \
                 patch.object(queue, 'discover_modules', AsyncMock(return_value=[1,2])), \
                 patch.object(queue, 'discover_roster', roster), \
                 patch.object(queue.QueueRunner, 'process_module', new=process), \
                 patch.object(queue, 'verify_course_completion', AsyncMock(return_value={
                     'verified':True, 'evidence':['You completed this course']})) as completion, \
                 patch.object(queue, 'open_certificate_page', new=opened_certificate), \
                 patch.object(queue, 'verify_saved_certificate', new=matching_certificate):
                result = await queue.run_queue(store, copy.copy(ARGS), SimpleNamespace())
            self.assertEqual(result, 0)
            self.assertEqual(store.course('philosophy')['modules_discovered'], 2)
            completion.assert_awaited_once()
            self.assertEqual(store.course('philosophy')['status'], 'completed')
            self.assertEqual(store.course('philosophy')['completion_method'], 'certificate')

    async def run_practice_lab_case(self, final_proof, certificate=False, auto=False):
        with tempfile.TemporaryDirectory() as directory:
            store = QueueStore(Path(directory) / 'state.json', configs()[:1])
            reading = {'key':'supplement:intro', 'title':'Introduction Reading',
                       'url':URL + '/supplement/intro/reading'}
            lab = {'key':'assignment:lab', 'title':'Practice Lab',
                   'url':URL + '/team/lab', 'type':'team', 'unsupported':True}
            graded = {'key':'assignment:final', 'title':'Final Graded Assignment',
                      'url':URL + '/assignment-submission/final/quiz'}
            certificate_url = 'https://www.coursera.org/account/accomplishments/verify/TEST1234'
            async def finish_quiz(runner, item):
                self.assertEqual(item, graded)
                if certificate:
                    runner.course['certificate_page_url'] = certificate_url
                    runner.store.save()
            site_proof = AsyncMock(side_effect=[
                {'verified':False, 'reason':'Not completed before final test'}, final_proof])
            final_rows = {reading['key']:{'status':'completed'}, graded['key']:{'status':'completed'}}
            with patch.object(queue, 'ROOT', Path(directory)), \
                 patch.object(queue, 'discover_modules', AsyncMock(return_value=[1])), \
                 patch.object(queue, 'discover_roster', AsyncMock(return_value=[reading, lab, graded])), \
                 patch.object(queue.QueueRunner, 'open_item', new=AsyncMock()), \
                 patch.object(queue.QueueRunner, 'verified_item', new=AsyncMock(return_value=False)), \
                 patch.object(queue.QueueRunner, 'reading', new=AsyncMock()) as read, \
                 patch.object(queue.QueueRunner, 'quiz', new=finish_quiz), \
                 patch.object(queue.QueueRunner, 'snapshot', new=AsyncMock(return_value={'url':URL + '/home/module/1'})), \
                 patch.object(engine, 'rows_by_key', return_value=final_rows), \
                 patch.object(queue, 'verify_course_completion', site_proof), \
                 patch.object(queue, 'open_certificate_page', new=AsyncMock(return_value=certificate_url)) as open_certificate, \
                 patch.object(queue, 'verify_saved_certificate', new=AsyncMock(return_value={
                     'verified':True, 'method':'certificate', 'course_id':'philosophy',
                     'evidence':['Verified certificate']})) as verify_certificate, \
                 patch.object(queue, 'open_my_learning', new=AsyncMock()) as learning, \
                 patch.object(queue, 'discover_next_course', new=AsyncMock(return_value=None)):
                args = copy.copy(ARGS)
                args.auto_my_learning = auto
                result = await queue.run_queue(store, args, SimpleNamespace())
            return result, copy.deepcopy(store.data), read.await_count, site_proof.await_count, \
                open_certificate.await_count, verify_certificate.await_count, learning.await_count

    async def test_practice_lab_site_proof_without_certificate_stays_attention(self):
        result, data, reads, proofs, cert_opened, cert_verified, learning_opened = await self.run_practice_lab_case(
            {'verified':True, 'evidence':['You completed this course']})
        self.assertEqual((result, reads, proofs, cert_opened, cert_verified, learning_opened), (2,1,2,0,0,0))
        course = data['courses'][0]
        self.assertEqual(course['status'], 'attention')
        self.assertIn('certificate page is not available', course['message'])
        self.assertEqual((course['items_completed'], course['modules_completed']), (2,0))
        module = course['checkpoint']['modules']['1']
        self.assertFalse(module['verified_complete'])
        self.assertFalse(module['items']['assignment:lab']['verified_complete'])
        self.assertEqual(module['manual_required'], ['Practice Lab'])

    async def test_practice_lab_before_final_quiz_uses_matching_certificate(self):
        result, data, reads, proofs, cert_opened, cert_verified, learning_opened = await self.run_practice_lab_case(
            {'verified':False, 'reason':'Course page has not updated'}, certificate=True, auto=True)
        self.assertEqual((result, reads, proofs, cert_opened, cert_verified, learning_opened), (0,1,2,1,1,1))
        course = data['courses'][0]
        self.assertEqual(course['status'], 'completed')
        self.assertEqual(course['completion_method'], 'certificate')
        self.assertFalse(course['checkpoint']['modules']['1']['verified_complete'])
        self.assertFalse(course['checkpoint']['modules']['1']['items']['assignment:lab']['verified_complete'])

    async def test_practice_lab_without_course_proof_reports_manual_task(self):
        result, data, reads, proofs, cert_opened, cert_verified, learning_opened = await self.run_practice_lab_case(
            {'verified':False, 'reason':'No whole-course completion evidence'})
        self.assertEqual((result, reads, proofs, cert_opened, cert_verified, learning_opened), (2,1,2,0,0,0))
        course = data['courses'][0]
        self.assertEqual(course['status'], 'attention')
        self.assertIn('Practice Lab', course['message'])
        self.assertEqual((course['items_completed'], course['modules_completed']), (2,0))
        self.assertNotIn('module_completed', [event['kind'] for event in data['events']])


class InterleavedModuleTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        engine.configure_course(URL)

    def roster(self, number):
        return [{'key':f'supplement:{number}-{index}', 'title':f'Module {number} reading {index}',
                 'url':f'{URL}/supplement/{number}-{index}/reading'} for index in range(number)]

    def mark_verified(self, runner, number, roster):
        runner.checkpoint['modules'][str(number)] = {
            'verified_complete':True, 'items':{item['key']:{'verified_complete':True} for item in roster}}
        runner.update_counts()

    async def test_archived_module_roster_prevents_one_module_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = 'deep-learning-with-pytorch'
            course_url = 'https://www.coursera.org/learn/' + identity
            config = [{'id':identity, 'url':course_url, 'title':'Deep Learning with PyTorch',
                       'modules':None}]
            old_log = root / 'logs' / ('queue_' + identity + '_older')
            old_log.mkdir(parents=True)
            (old_log / 'progress.json').write_text(json.dumps({
                'modules':{'1':{}, '2':{}, '3':{}}}), encoding='utf-8')
            store = QueueStore(root / 'state' / 'queue.json', config)
            store.update(identity, modules_total=1, modules_discovered=1,
                         checkpoint={'modules':{'1':{'roster':[]}}})
            sidebar = {'modules':[1], 'rows':{1:{'number':1, 'items':[]}}}
            roster = AsyncMock()
            discover = AsyncMock(return_value=[1])
            tab = SimpleNamespace(evaluate=AsyncMock())
            with patch.object(queue, 'ROOT', root), \
                 patch.object(queue, 'inspect_sidebar_progress', AsyncMock(return_value=sidebar)), \
                 patch.object(queue, 'discover_modules', discover), \
                 patch.object(queue, 'discover_roster', roster), \
                 patch.object(queue, 'observed_certificate_url', AsyncMock(return_value=None)), \
                 patch.object(queue, 'open_certificate_page', AsyncMock()):
                result = await queue.run_queue(store, copy.copy(ARGS), tab)
            self.assertEqual(result, 2)
            discover.assert_awaited_once_with(tab, course_url, None, minimum_modules=3)
            roster.assert_not_awaited()
            row = store.course(identity)
            self.assertEqual(row['modules_max_seen'], 3)
            self.assertEqual(row['modules_total'], 1)
            self.assertEqual(set(row['checkpoint']['modules']), {'1'})
            self.assertIn('earlier runs reached module 3', row['message'])

    async def test_start_skips_only_fully_checked_sidebar_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            one_course = configs()[:1]
            one_course[0]["modules"] = [1, 2]
            store = QueueStore(Path(directory) / 'state.json', one_course)
            store.course('philosophy')['certificate_page_url'] = certificate_url('philosophy')
            rows = {}
            for number, count in ((1, 9), (2, 7)):
                rows[number] = {"number":number, "items":[
                    {"key":f"lecture:{number}-{index}", "title":f"Video {index}",
                     "url":f"{URL}/lecture/{number}-{index}/video", "label":"Completed",
                     "checked":number == 1, "completed":number == 1}
                    for index in range(count)]}
            sidebar = {"modules":[1, 2], "rows":rows}
            scan = AsyncMock(return_value=self.roster(2))
            processed = []
            async def process_second(runner, number, roster):
                processed.append(number)
                self.assertEqual(number, 2)
                self.mark_verified(runner, number, roster)
            tab = SimpleNamespace(evaluate=AsyncMock())
            with patch.object(queue, 'ROOT', Path(directory)), \
                 patch.object(queue, 'inspect_sidebar_progress', AsyncMock(return_value=sidebar)), \
                 patch.object(queue, 'discover_modules', AsyncMock()) as modules, \
                 patch.object(queue, 'discover_roster', scan), \
                 patch.object(queue.QueueRunner, 'process_module', new=process_second), \
                 patch.object(queue, 'verify_course_completion', AsyncMock(return_value={
                     'verified':True, 'evidence':['All expected course-outline lessons complete']})), \
                 patch.object(queue, 'observed_certificate_url', AsyncMock(return_value=None)), \
                 patch.object(queue, 'open_certificate_page', new=opened_certificate), \
                 patch.object(queue, 'verify_saved_certificate', new=matching_certificate):
                result = await queue.run_queue(store, copy.copy(ARGS), tab)
            self.assertEqual(result, 0)
            modules.assert_not_awaited()
            scan.assert_awaited_once()
            self.assertEqual(scan.await_args.args[2], 2)
            self.assertEqual(processed, [2])
            self.assertEqual(store.course('philosophy')['checkpoint']['modules']['1']['source'], 'live_sidebar')
            self.assertEqual(store.course('philosophy')['items_completed'], 11)

    async def test_previous_course_tab_uses_cached_anchor_before_resuming(self):
        with tempfile.TemporaryDirectory() as directory:
            one_course = configs()[:1]
            one_course[0]['modules'] = [1, 2]
            store = QueueStore(Path(directory) / 'state.json', one_course)
            store.course('philosophy')['certificate_page_url'] = certificate_url('philosophy')
            prior = [{'key':f'lecture:1-{index}', 'title':f'Video {index}',
                      'url':f'{URL}/lecture/1-{index}/video', 'label':'Completed',
                      'checked':True, 'completed':True} for index in range(9)]
            store.update('philosophy', checkpoint={'modules':{'1':{'roster':prior, 'items':{}}}})
            sidebar = {'modules':[1, 2], 'rows':{
                1:{'number':1, 'items':prior},
                2:{'number':2, 'items':[{'key':'supplement:pending', 'checked':False,
                                         'completed':False}]}}}
            tab = SimpleNamespace(evaluate=AsyncMock(), navigate=AsyncMock())
            processed = []
            async def process_second(runner, number, roster):
                processed.append(number)
                self.mark_verified(runner, number, roster)
            with patch.object(queue, 'ROOT', Path(directory)), \
                 patch.object(queue, 'inspect_sidebar_progress', AsyncMock(
                     side_effect=[None, None, sidebar])) as inspect, \
                 patch.object(queue, 'discover_modules', AsyncMock(return_value=[1,2])), \
                 patch.object(queue, 'discover_roster', AsyncMock(return_value=self.roster(2))), \
                 patch.object(queue.QueueRunner, 'process_module', new=process_second), \
                 patch.object(queue, 'verify_course_completion', AsyncMock(return_value={
                     'verified':True, 'evidence':['Course completed']})), \
                 patch.object(queue, 'observed_certificate_url', AsyncMock(return_value=None)), \
                 patch.object(queue, 'open_certificate_page', new=opened_certificate), \
                 patch.object(queue, 'verify_saved_certificate', new=matching_certificate):
                result = await queue.run_queue(store, copy.copy(ARGS), tab)
            self.assertEqual(result, 0)
            tab.navigate.assert_awaited_once_with(prior[0]['url'])
            self.assertEqual(inspect.await_count, 3)
            self.assertEqual(processed, [2])

    async def test_each_roster_is_processed_before_next_scan_and_final_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QueueStore(Path(directory) / 'state.json', configs()[:1])
            store.course('philosophy')['certificate_page_url'] = certificate_url('philosophy')
            order = []
            test = self
            async def scan(tab, url, number, expected):
                order.append(f'scan{number}')
                row = store.course('philosophy')
                self.assertFalse(row['inventory_complete'])
                self.assertEqual(row['modules_discovered'], number - 1)
                self.assertEqual(row['items_total'], sum(range(number)))
                return self.roster(number)
            async def process(runner, number, roster):
                order.append(f'process{number}')
                row = store.course('philosophy')
                test.assertEqual(row['modules_discovered'], number)
                test.assertEqual(row['items_total'], sum(range(1, number + 1)))
                test.assertEqual(row['inventory_complete'], number == 2)
                test.mark_verified(runner, number, roster)
            async def proof(*args):
                order.append('proof')
                return {'verified':True, 'evidence':['You completed this course']}
            with patch.object(queue, 'ROOT', Path(directory)), \
                 patch.object(queue, 'discover_modules', AsyncMock(return_value=[1,2])), \
                 patch.object(queue, 'discover_roster', scan), \
                 patch.object(queue.QueueRunner, 'process_module', process), \
                 patch.object(queue, 'verify_course_completion', proof), \
                 patch.object(queue, 'open_certificate_page', new=opened_certificate), \
                 patch.object(queue, 'verify_saved_certificate', new=matching_certificate):
                result = await queue.run_queue(store, copy.copy(ARGS), SimpleNamespace())
            self.assertEqual(order, ['scan1','process1','scan2','process2','proof'])
            self.assertEqual(result, 0)
            row = store.course('philosophy')
            self.assertTrue(row['inventory_complete'])
            self.assertEqual((row['modules_discovered'], row['items_total'], row['items_completed']), (2,3,3))

    async def test_failure_processing_module_two_does_not_scan_module_three(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QueueStore(Path(directory) / 'state.json', configs()[:1])
            order = []
            test = self
            async def scan(tab, url, number, expected):
                order.append(f'scan{number}')
                return self.roster(number)
            async def process(runner, number, roster):
                order.append(f'process{number}')
                if number == 2:
                    raise engine.StopRun('Fixture module two needs attention')
                test.mark_verified(runner, number, roster)
            completion = AsyncMock()
            with patch.object(queue, 'ROOT', Path(directory)), \
                 patch.object(queue, 'discover_modules', AsyncMock(return_value=[1,2,3])), \
                 patch.object(queue, 'discover_roster', scan), \
                 patch.object(queue.QueueRunner, 'process_module', process), \
                 patch.object(queue, 'verify_course_completion', completion):
                result = await queue.run_queue(store, copy.copy(ARGS), SimpleNamespace())
            self.assertEqual(order, ['scan1','process1','scan2','process2'])
            self.assertEqual(result, 2)
            row = store.course('philosophy')
            self.assertFalse(row['inventory_complete'])
            self.assertEqual((row['modules_discovered'], row['items_total'], row['items_completed']), (2,3,1))
            self.assertEqual(row['status'], 'attention')
            completion.assert_not_awaited()

    async def test_restart_rechecks_counts_without_resetting_attempts_or_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            saved = QueueStore(path, configs()[:1])
            attempts = {KEY:{'count':1, 'pending':False, 'feedback_ready':True, 'feedback_ready_count':1}}
            feedback = {KEY:'Question 1\nIncorrect\nObserved explanation'}
            old_modules = {str(n):{'verified_complete':True,
                'items':{f'supplement:{n}-0':{'verified_complete':True}}} for n in [1,2]}
            saved.update('philosophy', items_total=2, items_completed=2, modules_completed=2,
                         modules_discovered=2, inventory_complete=True,
                         checkpoint={'attempts':attempts, 'feedback':feedback, 'modules':old_modules})
            store = QueueStore(path, configs()[:1])
            test = self
            async def scan(tab, url, number, expected):
                row = store.course('philosophy')
                self.assertEqual(row['checkpoint']['attempts'], attempts)
                self.assertEqual(row['checkpoint']['feedback'], feedback)
                self.assertEqual(row['modules_completed'], number - 1)
                self.assertEqual(row['items_completed'], number - 1)
                self.assertEqual(set(row['checkpoint']['modules']), set() if number == 1 else {'1'})
                if number == 2:
                    raise engine.StopRun('Fixture stops after rechecking module one')
                return self.roster(number)
            async def process(runner, number, roster):
                test.assertEqual(runner.quiz_attempt_limit(KEY), 1)
                test.assertEqual(runner.previous_quiz_feedback(KEY), feedback[KEY])
                test.mark_verified(runner, number, roster)
            with patch.object(queue, 'ROOT', Path(directory)), \
                 patch.object(queue, 'discover_modules', AsyncMock(return_value=[1,2])), \
                 patch.object(queue, 'discover_roster', scan), \
                 patch.object(queue.QueueRunner, 'process_module', process), \
                 patch.object(queue, 'verify_course_completion', AsyncMock()) as completion:
                result = await queue.run_queue(store, copy.copy(ARGS), SimpleNamespace())
            self.assertEqual(result, 2)
            reloaded = QueueStore(path, configs()[:1]).course('philosophy')
            self.assertEqual(reloaded['checkpoint']['attempts'], attempts)
            self.assertEqual(reloaded['checkpoint']['feedback'], feedback)
            self.assertEqual((reloaded['items_completed'], reloaded['modules_completed']), (1,1))
            self.assertFalse(reloaded['inventory_complete'])
            self.assertEqual(reloaded['modules_discovered'], 1)
            completion.assert_not_awaited()


class StartupReportingTests(unittest.TestCase):
    def prepared_store(self, directory):
        path = Path(directory) / 'state/queue.json'
        store = QueueStore(path, configs()[:1])
        checkpoint = {'attempts':{KEY:{'count':1, 'pending':True}},
                      'feedback':{KEY:'Question 1\nIncorrect'}, 'modules':{}}
        store.update('philosophy', checkpoint=checkpoint)
        return path, copy.deepcopy(checkpoint)

    def checkpoint_on_disk(self, path):
        return json.loads((path.parent / 'checkpoints/philosophy.json').read_text(encoding='utf-8'))

    def test_error_before_queue_start_is_reported_and_ledger_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path, checkpoint = self.prepared_store(directory)
            original = RuntimeError('Chrome startup failed in offline fixture')
            with self.assertRaises(RuntimeError) as caught:
                with queue.reported_store(path, configs()[:1]):
                    raise original
            self.assertIs(caught.exception, original)
            summary = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(summary['status'], 'attention')
            self.assertEqual(summary['events'][-1]['kind'], 'course_attention')
            self.assertIn('Chrome startup failed', summary['events'][-1]['message'])
            self.assertEqual(self.checkpoint_on_disk(path), checkpoint)

    def test_keyboard_interrupt_and_cancellation_before_start_are_paused(self):
        for error_type in [KeyboardInterrupt, engine.asyncio.CancelledError]:
            with self.subTest(error_type=error_type), tempfile.TemporaryDirectory() as directory:
                path, checkpoint = self.prepared_store(directory)
                original = error_type()
                with self.assertRaises(error_type) as caught:
                    with queue.reported_store(path, configs()[:1]):
                        raise original
                self.assertIs(caught.exception, original)
                summary = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(summary['status'], 'paused')
                self.assertEqual(summary['events'][-1]['kind'], 'queue_paused')
                self.assertEqual(self.checkpoint_on_disk(path), checkpoint)

    def test_already_recorded_cancellation_keeps_original_reason_without_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            path, checkpoint = self.prepared_store(directory)
            with self.assertRaises(engine.asyncio.CancelledError):
                with queue.reported_store(path, configs()[:1]) as store:
                    store.data['status'] = 'paused'
                    store.event('queue_paused', 'Stopped during module two')
                    raise engine.asyncio.CancelledError()
            summary = json.loads(path.read_text(encoding='utf-8'))
            events = [event for event in summary['events'] if event['kind'] == 'queue_paused']
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['message'], 'Stopped during module two')
            self.assertEqual(self.checkpoint_on_disk(path), checkpoint)


if __name__ == '__main__':
    unittest.main(verbosity=2)
