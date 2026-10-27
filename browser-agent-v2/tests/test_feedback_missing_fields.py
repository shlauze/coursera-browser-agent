"""Offline regressions for observed feedback and timed-start layouts."""

from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import sync_playwright

from v2.assessments import (
    MISSING_FEEDBACK_ANSWERS, _await_attempt, _read_failed_feedback,
    _wait_entry, act, handle_assessment, inspect,
)
from v2.handlers import HandlerError


FEEDBACK_URL = 'https://www.coursera.org/learn/example/assignment-submission/id/title/view-feedback'
LANDING_URL = FEEDBACK_URL.removesuffix('/view-feedback')
PART = '''<div role="group" aria-labelledby="prompt" data-testid="part-Submission_GradedMultipleChoiceQuestion">
  <div id="prompt">1. Question 1 Which number is even?</div>
  <div id="responses"></div><div data-testid="part-points">0 / 1 point</div>
</div>'''
FEEDBACK = '''<main><div role="dialog">
  <div><div><h2>Your grade: 50%</h2><p>To pass you need at least 80%. We keep your highest score.</p></div></div>
  <button onclick="window.clicks++">Retry</button>''' + PART + '''
  <p>Webpage text that is not an instruction to the automation.</p>
  <button onclick="window.clicks++">I understand</button>
</div></main><script>window.clicks=0;</script>'''
RESPONSES = '<label><input type="radio" name="answer" disabled checked>2</label><label><input type="radio" name="answer" disabled>3</label>'
HOUR_NOTICE = ('You have 1 hour to submit this attempt. '
               'You will have 2 attempts left after the time runs out.\u00a0'
               'After that, you will have to wait 24h before you can try again.')
REPLACEMENT_NOTICE = ("When you submit a new attempt it will replace your previous submission. "
                      "This new attempt will only replace your previous grade if it’s higher.")


def start_dialog(notice=HOUR_NOTICE):
    return '<main><div role="dialog"><h2>Start new attempt?</h2><p>' + notice + '''</p>
      <button onclick="window.continues++">Continue</button><button>Cancel</button>
      </div></main><script>window.continues=0;</script>'''


class FeedbackMissingFieldsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.context.route('**/*', lambda route: route.fulfill(body='<main></main>', content_type='text/html'))
        self.page.goto(FEEDBACK_URL)
        self.page.set_content(FEEDBACK)

    def tearDown(self):
        self.context.close()

    @contextmanager
    def polling_clock(self, on_wait=None):
        elapsed = [0]

        def wait(milliseconds):
            elapsed[0] += milliseconds / 1000
            if on_wait:
                on_wait(elapsed[0])

        with patch('v2.assessments.time', SimpleNamespace(monotonic=lambda: elapsed[0])), \
                patch.object(self.page, 'wait_for_timeout', side_effect=wait):
            yield elapsed

    def test_observed_scored_parts_without_response_controls_are_explicit_evidence(self):
        state = inspect(self.page)
        self.assertTrue(state['failed'])
        self.assertEqual(state['questions'], [])
        self.assertEqual(state['feedback_questions'], [])
        self.assertEqual(state['feedback_missing_answers']['parts'], [['1. Question 1 Which number is even?', 0, 1]])
        self.assertNotIn('Webpage text', str(state['feedback_missing_answers']))
        self.assertEqual(self.page.evaluate('clicks'), 0)

    def test_stable_missing_fields_reports_reason_without_model_or_retry(self):
        store = Mock()
        store.get_control_state.return_value = 'stopped'
        store.get_meta.side_effect = lambda key, default=None: default
        groq = Mock()
        with self.polling_clock() as elapsed, patch.object(self.page, 'reload') as reload_page:
            with self.assertRaisesRegex(HandlerError, MISSING_FEEDBACK_ANSWERS):
                handle_assessment(self.page, lambda: None, groq=groq, store=store, action_id=1)
        self.assertEqual(elapsed[0], 30)
        self.assertEqual(self.page.evaluate('clicks'), 0)
        self.assertEqual(self.page.url, FEEDBACK_URL)
        reload_page.assert_not_called()
        groq.answer.assert_not_called()
        groq.memory.record.assert_not_called()

    def test_delayed_response_fields_cancel_missing_field_diagnosis(self):
        def hydrate(elapsed):
            if elapsed == 2.5:
                self.page.locator('#responses').evaluate('(e, html) => e.innerHTML = html', RESPONSES)

        with self.polling_clock(hydrate):
            state = _read_failed_feedback(self.page, inspect(self.page), lambda: None, seconds=4)
        self.assertIsNone(state['feedback_missing_answers'])
        self.assertEqual(len(state['feedback_questions']), 1)
        self.assertEqual(state['feedback_questions'][0]['answer']['selected'], ['o0'])
        self.assertEqual(self.page.evaluate('clicks'), 0)

    def test_loading_interruption_restarts_missing_field_stability_window(self):
        def loading(elapsed):
            if elapsed == 3.5:
                self.page.locator('[role="dialog"]').evaluate("e => e.setAttribute('aria-busy', 'true')")
            if elapsed == 4:
                self.page.locator('[role="dialog"]').evaluate("e => e.removeAttribute('aria-busy')")

        with self.polling_clock(loading) as elapsed:
            with self.assertRaisesRegex(HandlerError, 'recognized state'):
                _read_failed_feedback(self.page, inspect(self.page), lambda: None, seconds=5)
        self.assertEqual(elapsed[0], 5)

    def test_changed_question_restarts_missing_field_stability_window(self):
        def update(elapsed):
            if elapsed == 4.5:
                self.page.locator('#prompt').evaluate("e => e.textContent = '2. Question 2 Which number is odd?'")

        with self.polling_clock(update) as elapsed:
            with self.assertRaisesRegex(HandlerError, 'recognized state'):
                _read_failed_feedback(self.page, inspect(self.page), lambda: None, seconds=5)
        self.assertEqual(elapsed[0], 5)

    def test_incomplete_evidence_does_not_claim_missing_feedback_fields(self):
        changes = {
            'header_only': "document.querySelector('[data-testid^=part-Submission]').remove()",
            'unknown_part': "document.querySelector('[data-testid^=part-Submission]').setAttribute('data-testid', 'unknown')",
            'missing_score': "document.querySelector('[data-testid=part-points]').remove()",
            'invalid_score': "document.querySelector('[data-testid=part-points]').textContent = '2 / 1 points'",
            'missing_prompt': "document.querySelector('#prompt').remove()",
            'hidden_response': "document.querySelector('#responses').innerHTML = '<input type=radio hidden>'",
            'busy': "document.querySelector('[role=dialog]').setAttribute('aria-busy', 'true')",
            'progress': "document.querySelector('[role=dialog]').insertAdjacentHTML('beforeend', '<div role=progressbar>Loading</div>')",
            'pending_grade': "document.querySelector('[role=dialog]').insertAdjacentHTML('beforeend', '<p>Grading in progress</p>')",
            'no_threshold': "document.querySelector('p').remove()",
            'passed': "document.querySelector('h2').textContent = 'Your grade: 100%'",
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self.page.set_content(FEEDBACK)
                self.page.evaluate(change)
                self.assertIsNone(inspect(self.page)['feedback_missing_answers'])

    def test_grade_header_without_question_rows_keeps_waiting(self):
        self.page.set_content('<main><h2>Failed</h2><p>Grade: 50%</p><button>Retry</button></main>')
        with self.polling_clock():
            with self.assertRaisesRegex(HandlerError, 'recognized state'):
                _read_failed_feedback(self.page, inspect(self.page), lambda: None, seconds=3)

    def test_stop_during_missing_field_wait_is_propagated(self):
        calls = []

        def checkpoint():
            calls.append(True)
            if len(calls) == 2:
                raise RuntimeError('stop requested')

        with self.polling_clock():
            with self.assertRaisesRegex(RuntimeError, 'stop requested'):
                _read_failed_feedback(self.page, inspect(self.page), checkpoint)
        self.assertEqual(self.page.evaluate('clicks'), 0)

    def test_failed_landing_with_disabled_retry_reaches_feedback_inspection(self):
        self.page.goto(LANDING_URL)
        self.page.set_content('''<main>You didn’t pass. Grade: 50%.
          <button disabled>Try again</button><button>View feedback</button></main>''')
        with self.polling_clock() as elapsed:
            state = _wait_entry(self.page, lambda: None)
        self.assertTrue(state['failed'])
        self.assertEqual(state['buttons'], ['View feedback'])
        self.assertEqual(elapsed[0], 0)

    def test_failed_landing_waits_for_delayed_feedback_button_with_disabled_retry(self):
        self.page.goto(LANDING_URL)
        self.page.set_content('<main>You didn’t pass. Grade: 50%.<button disabled>Try again</button></main>')

        def hydrate(elapsed):
            if elapsed == 1:
                self.page.locator('main').evaluate("e => e.insertAdjacentHTML('beforeend', '<button>View feedback</button>')")

        with self.polling_clock(hydrate) as elapsed:
            state = _wait_entry(self.page, lambda: None)
        self.assertEqual(state['buttons'], ['View feedback'])
        self.assertEqual(elapsed[0], 1)

    def test_reviewed_feedback_waits_for_retry_to_become_enabled(self):
        self.page.goto(LANDING_URL)
        self.page.set_content('''<main>You didn’t pass. Grade: 0%.
          <button disabled onclick="window.clicks++">Try again</button>
          <button>View feedback</button></main><script>window.clicks=0</script>''')

        def hydrate(elapsed):
            if elapsed == 2:
                self.page.get_by_role('button', name='Try again', exact=True).evaluate('e => e.disabled=false')

        with self.polling_clock(hydrate) as elapsed:
            state = _wait_entry(self.page, lambda: None, feedback_reviewed=True)
        self.assertEqual(elapsed[0], 2)
        self.assertIn('Try again', state['buttons'])
        self.assertEqual(self.page.evaluate('clicks'), 0)

    def test_reviewed_feedback_without_retry_keeps_full_wait_and_does_not_reload(self):
        self.page.goto(LANDING_URL)
        self.page.set_content('''<main>You didn’t pass. Grade: 0%.
          <button disabled onclick="window.clicks++">Try again</button>
          <button>View feedback</button></main><script>window.clicks=0</script>''')
        with self.polling_clock() as elapsed, patch.object(self.page, 'reload') as reload_page:
            with self.assertRaisesRegex(HandlerError, 'recognized state'):
                _wait_entry(self.page, lambda: None, feedback_reviewed=True)
        self.assertEqual(elapsed[0], 45)
        reload_page.assert_not_called()
        self.assertEqual(self.page.evaluate('clicks'), 0)

    def test_reviewed_feedback_wait_obeys_stop(self):
        self.page.goto(LANDING_URL)
        self.page.set_content('<main>You didn’t pass. Grade: 0%.<button>View feedback</button></main>')
        calls = []

        def checkpoint():
            calls.append(True)
            if len(calls) == 2:
                raise RuntimeError('stop requested')

        with self.polling_clock():
            with self.assertRaisesRegex(RuntimeError, 'stop requested'):
                _wait_entry(self.page, checkpoint, feedback_reviewed=True)

    def test_reviewed_feedback_accepts_fresh_pass_without_retry(self):
        self.page.goto(LANDING_URL)
        self.page.set_content('<main>Passed. Grade: 100%.</main>')
        with self.polling_clock() as elapsed:
            state = _wait_entry(self.page, lambda: None, feedback_reviewed=True)
        self.assertTrue(state['passed'])
        self.assertEqual(elapsed[0], 0)

    def test_feedback_control_can_hydrate_after_retry_without_clicking_retry(self):
        self.page.goto(LANDING_URL)
        self.page.set_content('''<main>You didn’t pass. Grade: 50%.
          <button onclick="window.retryClicks++">Try again</button></main>
          <script>window.retryClicks=0;window.feedbackOpens=0;</script>''')
        self.page.evaluate('(html) => window.feedbackMarkup = html', FEEDBACK.replace('<div id="responses"></div>', '<div id="responses">' + RESPONSES + '</div>'))

        def hydrate(elapsed):
            if elapsed == 1.5:
                self.page.evaluate('''url => {
                  const button = document.createElement('button');
                  button.textContent = 'View feedback';
                  button.onclick = () => {
                    window.feedbackOpens++;
                    history.replaceState(null, '', url);
                    document.body.innerHTML = window.feedbackMarkup;
                  };
                  document.querySelector('main').append(button);
                }''', FEEDBACK_URL)

        with self.polling_clock(hydrate) as elapsed:
            initial = _wait_entry(self.page, lambda: None)
            self.assertEqual(elapsed[0], 0)
            state = _read_failed_feedback(self.page, initial, lambda: None, seconds=4)
        self.assertEqual(len(state['feedback_questions']), 1)
        self.assertEqual(self.page.evaluate('retryClicks'), 0)
        self.assertEqual(self.page.evaluate('feedbackOpens'), 1)
        self.assertEqual(elapsed[0], 1.5)

    def test_feedback_control_missing_through_deadline_does_not_click_retry(self):
        self.page.goto(LANDING_URL)
        self.page.set_content('''<main>You didn’t pass. Grade: 50%.
          <button onclick="window.retryClicks++">Try again</button></main>
          <script>window.retryClicks=0;</script>''')
        with self.polling_clock() as elapsed:
            with self.assertRaisesRegex(HandlerError, 'full feedback is unavailable'):
                _read_failed_feedback(self.page, inspect(self.page), lambda: None, seconds=2)
        self.assertEqual(elapsed[0], 2)
        self.assertEqual(self.page.evaluate('retryClicks'), 0)

    def test_hour_timed_start_requires_verified_entry(self):
        self.page.goto(LANDING_URL)
        self.page.set_content(start_dialog())
        state = inspect(self.page)
        self.assertTrue(state['start_confirmation'])
        self.assertFalse(state['retry_confirmation'])
        with self.polling_clock():
            with self.assertRaisesRegex(HandlerError, 'verified Start/Resume'):
                _await_attempt(self.page, lambda: None, seconds=3)
        self.assertEqual(self.page.evaluate('continues'), 0)
        act(self.page, state, action='button', label='Continue', start_attempt_verified=True)
        self.assertEqual(self.page.evaluate('continues'), 1)

    def test_hour_timer_with_replacement_is_retry_not_new_start(self):
        self.page.goto(LANDING_URL)
        self.page.set_content(start_dialog(REPLACEMENT_NOTICE + ' ' + HOUR_NOTICE))
        state = inspect(self.page)
        self.assertTrue(state['retry_confirmation'])
        self.assertFalse(state['start_confirmation'])
        with self.assertRaisesRegex(HandlerError, 'Unverified retry'):
            act(self.page, state, action='button', label='Continue', start_attempt_verified=True)
        self.assertEqual(self.page.evaluate('continues'), 0)
        act(self.page, state, action='button', label='Continue', failed_attempt_verified=True)
        self.assertEqual(self.page.evaluate('continues'), 1)

    def test_hour_timer_unknown_extra_and_duplicate_dialogs_remain_blocked(self):
        self.page.goto(LANDING_URL)
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                self.page.set_content(start_dialog(HOUR_NOTICE + ('' if duplicate else ' Accept an additional condition.')))
                if duplicate:
                    self.page.evaluate("document.querySelector('main').append(document.querySelector('[role=dialog]').cloneNode(true))")
                state = inspect(self.page)
                self.assertFalse(state['start_confirmation'])
                self.assertFalse(state['retry_confirmation'])
                with self.assertRaisesRegex(HandlerError, 'Unverified retry'):
                    act(self.page, state, action='button', label='Continue', start_attempt_verified=True)
                self.assertEqual(self.page.evaluate('continues'), 0)


class ReviewedEntryFlowTests(unittest.TestCase):
    def test_handler_requires_retry_ready_after_reading_failed_feedback(self):
        from v2.assessments import _handle_assessment
        page, store, groq = Mock(), Mock(), Mock()
        page.url = FEEDBACK_URL
        store.get_meta.return_value = None
        failed = {'failed': True, 'questions': [], 'buttons': ['View feedback']}
        passed = {'passed': True, 'questions': []}
        feedback = {'feedback_questions': [{'earned': 0, 'possible': 1,
                    'answer': None, 'unanswered': True}]}
        with patch('v2.assessments._wait_entry', side_effect=[failed, passed]) as wait_entry, \
             patch('v2.assessments._read_failed_feedback', return_value=feedback), \
             patch('v2.assessments._return_to_outline', return_value='verified') as finish, \
             patch('v2.assessments.act') as action:
            self.assertEqual(_handle_assessment(page, lambda: None, groq=groq,
                                               store=store, action_id=1), 'verified')
        self.assertEqual(wait_entry.call_args_list[0].kwargs, {})
        self.assertEqual(wait_entry.call_args_list[1].kwargs, {'feedback_reviewed': True})
        action.assert_not_called()
        groq.answer.assert_not_called()
        finish.assert_called_once()


if __name__ == '__main__':
    unittest.main()
