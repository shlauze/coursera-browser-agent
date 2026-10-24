from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import sync_playwright
from v2.assessments import DOM, inspect, act, handle_assessment, _wait, _read_failed_feedback, _wait_submit, _await_attempt
from v2.groq_client import AnswerMemory
from v2.handlers import HandlerError
from v2.store import Store


FORM='''<html><body><main>
<fieldset><legend>1. Which number is even?</legend>
<label><input type="radio" name="q1" value="2">2</label>
<label><input type="radio" name="q1" value="3">3</label></fieldset>
<fieldset><legend>2. Calculate 2 plus 2.</legend><input type="number"></fieldset>
<button onclick="window.submits=(window.submits||0)+1;document.querySelector('main').innerHTML='<h1>Passed</h1><p>Grade: 100%</p>'">Submit</button>
</main></body></html>'''


class AssessmentDomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright=sync_playwright().start()
        cls.browser=cls.playwright.chromium.launch(channel="chrome",headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.playwright.stop()

    def test_reviewed_visual_requires_exact_stem_image_and_dimensions(self):
        import copy
        svg = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"><rect width="120" height="80" fill="red"/></svg>'
        self.page.set_content(FORM.replace('</legend>', '</legend><img id="chart">', 1))
        self.page.locator('#chart').evaluate('(e,src)=>e.src=src', svg)
        self.page.wait_for_function('document.querySelector("img").complete && document.querySelector("img").naturalWidth > 0')
        blocked = self.page.evaluate(DOM, {'action':'inspect'})
        self.assertIn('visual content', blocked['error'])
        review = {**blocked['visual_context'], 'description':'A solid red rectangle.'}
        accepted = self.page.evaluate(DOM, {'action':'inspect', 'visual_reviews':[review]})
        self.assertIn('Reviewed diagram description: A solid red rectangle.', accepted['questions'][0]['text'])
        for field in ('stem', 'src', 'width', 'description'):
            wrong = copy.deepcopy(review)
            if field == 'stem': wrong['question_text'] += ' changed'
            elif field == 'description': wrong['description'] = ''
            else: wrong['images'][0][field] = 'changed'
            self.assertIn('visual content', self.page.evaluate(DOM, {'action':'inspect','visual_reviews':[wrong]})['error'])
        self.assertIn('visual content', self.page.evaluate(DOM, {'action':'inspect','visual_reviews':[review,review]})['error'])
        self.page.locator('#chart').evaluate('(e)=>document.querySelector("label").append(e)')
        self.assertIn('visual content', self.page.evaluate(DOM, {'action':'inspect','visual_reviews':[review]})['error'])

    def test_explicit_blank_feedback_is_distinguished_from_missing_selection(self):
        self.page.set_content('<main><fieldset disabled><legend>1. Which number is even?</legend><label><input type="radio" name="q">2</label><label><input type="radio" name="q">3</label><span class="points">0 / 1 point</span><div>You did not answer the question</div></fieldset></main>')
        row=inspect(self.page)['feedback_questions'][0]
        self.assertTrue(row['unanswered'])
        self.assertIsNone(row['answer'])
        self.page.get_by_text('You did not answer the question',exact=True).evaluate('(e)=>e.remove()')
        self.assertFalse(inspect(self.page)['feedback_questions'][0]['unanswered'])

    def test_coursera_image_signature_rotation_preserves_asset_identity(self):
        import copy
        src='https://d3c33hcgiwev3.cloudfront.net/immutable/chart.png?expiry=2&hmac=new&width=120'
        self.page.set_content(FORM.replace('</legend>', '</legend><img id="chart">', 1))
        self.page.locator('#chart').evaluate("""(e,src)=>{
            Object.defineProperty(e,'currentSrc',{value:src});
            Object.defineProperty(e,'complete',{value:true});
            Object.defineProperty(e,'naturalWidth',{value:120});
            Object.defineProperty(e,'naturalHeight',{value:80});
            e.style.width='120px';e.style.height='80px';
        }""",src)
        state=self.page.evaluate(DOM,{'action':'inspect'})
        review={**state['visual_context'],'description':'A red curve.'}
        review['images'][0]['src']=src.replace('expiry=2&hmac=new','expiry=1&hmac=old')
        self.assertNotIn('error',self.page.evaluate(DOM,{'action':'inspect','visual_reviews':[review]}))
        for replacement in (src.replace('chart.png','different.png'),src.replace('width=120','width=240'),src.replace('cloudfront.net','example.com')):
            wrong=copy.deepcopy(review);wrong['images'][0]['src']=replacement
            self.assertIn('visual content',self.page.evaluate(DOM,{'action':'inspect','visual_reviews':[wrong]})['error'])

    def test_waits_for_image_decoding_before_matching_review(self):
        from v2.assessments import _VISUAL_REVIEWS
        self.page.set_content(FORM.replace('</legend>', '</legend><img id="chart">', 1))
        self.page.locator('#chart').evaluate("""e=>{
            window.decoded=true;
            Object.defineProperty(e,'currentSrc',{value:'https://example.com/chart.png'});
            Object.defineProperty(e,'complete',{get:()=>window.decoded});
            Object.defineProperty(e,'naturalWidth',{get:()=>window.decoded?120:0});
            Object.defineProperty(e,'naturalHeight',{get:()=>window.decoded?80:0});
            e.style.width='120px';e.style.height='80px';
        }""")
        blocked=self.page.evaluate(DOM,{'action':'inspect'})
        review={**blocked['visual_context'],'description':'A red curve.'}
        token=_VISUAL_REVIEWS.set([review])
        try:
            self.page.evaluate('()=>{window.decoded=false;setTimeout(()=>window.decoded=true,700)}')
            self.assertEqual(self.page.evaluate(DOM,{'action':'inspect','visual_reviews':[review]})['error'],'Question image is still loading')
            state=_wait(self.page,lambda:None,lambda r:bool(r.get('questions')),seconds=3)
            self.assertIn('Reviewed diagram description',state['questions'][0]['text'])
            self.page.evaluate('()=>window.decoded=false')
            with self.assertRaisesRegex(HandlerError,'Question image is still loading'):
                _wait(self.page,lambda:None,lambda r:bool(r.get('questions')),seconds=0.6)
        finally:
            _VISUAL_REVIEWS.reset(token)

    def test_canvas_cannot_be_authorized_by_a_description(self):
        self.page.set_content(FORM.replace('</legend>', '</legend><canvas width="120" height="80"></canvas>', 1))
        state = self.page.evaluate(DOM, {'action':'inspect'})
        review = {**state['visual_context'], 'description':'A reviewed diagram.'}
        self.assertIn('visual content', self.page.evaluate(DOM, {'action':'inspect','visual_reviews':[review]})['error'])

    def test_file_feedback_retains_zero_rubric_without_quiz_fields(self):
        self.page.goto('https://www.coursera.org/learn/example/assignment-submission/abc/test/view-feedback')
        self.page.set_content('<main>No uploaded file<h2>How you were graded</h2>Rubric 1 Status: 0/5 points Rubric 2 Status: 0/3 points</main>')
        state=_read_failed_feedback(self.page,inspect(self.page),lambda:None,seconds=2)
        self.assertTrue(state['file_feedback']['missing_file'])
        self.assertEqual(len(state['file_feedback']['rubrics']),2)
        self.assertEqual(state['file_feedback']['rubrics'][0]['earned'],0)

    def test_file_form_recognized_without_invented_question(self):
        self.page.set_content('<main><input type="file"><button>Submit</button></main>')
        state=inspect(self.page)
        self.assertTrue(state['file_form'])
        self.assertFalse(state['questions'])

    def test_peer_submission_tab_is_supported(self):
        from v2.peer import handle_peer
        self.page.goto('https://www.coursera.org/learn/example/peer/abc/test')
        self.page.set_content('<button role="tab" onclick="document.querySelector(\'#form\').hidden=false">My submission</button><div id="form" hidden><input aria-label="Project Title"><div id="prompt">Essay topic</div><div data-slate-editor="true" contenteditable="true" aria-labelledby="prompt"></div></div>')
        with self.assertRaisesRegex(HandlerError,'Learner-authored essay'):
            handle_peer(self.page,lambda:None,store=self.store,action_id=1)
        self.assertTrue(self.page.get_by_role('textbox',name='Project Title').is_visible())

    def test_peer_submission_tab_waits_for_hydration(self):
        from v2.peer import handle_peer
        self.page.goto('https://www.coursera.org/learn/example/peer/abc/test')
        self.page.set_content('<button role="tab" onclick="document.querySelector(\'#form\').hidden=false">My submission</button><div id="form" hidden><input aria-label="Project Title"><div id="prompt">Essay topic</div><div data-slate-editor="true" contenteditable="true" aria-labelledby="prompt"></div></div>')
        self.page.evaluate("() => {const tab=document.querySelector('[role=tab]');tab.style.display='none';setTimeout(()=>tab.style.display='',800)}")
        with self.assertRaisesRegex(HandlerError,'Learner-authored essay'):
            handle_peer(self.page,lambda:None,store=self.store,action_id=1)
        self.assertTrue(self.page.get_by_role('textbox',name='Project Title').is_visible())

    def test_review_recovers_checked_answer_only(self):
        self.page.goto('https://www.coursera.org/learn/example/assignment-submission/abc/test/view-feedback')
        self.page.set_content('<main><fieldset disabled><legend>1. Which number is even?</legend><label><input type="radio" name="q">2</label><label><input type="radio" name="q" checked>3</label><span class="points">0 / 1 point</span></fieldset></main>')
        row=inspect(self.page)['feedback_questions'][0]
        self.assertEqual(row['answer']['selected'],['o1'])
        self.assertEqual(row['earned'],0)
        self.page.locator('input').evaluate_all('(es)=>es.forEach(e=>e.checked=false)')
        self.assertIsNone(inspect(self.page)['feedback_questions'][0]['answer'])

    def setUp(self):
        self.context=self.browser.new_context();self.addCleanup(self.context.close)
        self.page=self.context.new_page()
        self.page.route('**/*',lambda r:r.fulfill(body=FORM,content_type='text/html'))
        self.page.goto('https://www.coursera.org/learn/example/assignment-submission/abc/test/attempt')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'state.sqlite3')

    def test_missing_scored_feedback_never_clicks_retry(self):
        from v2.assessments import _handle_assessment
        q={"id":"q0","text":"An exact question", "kind":"single", "options":[{"id":"o0","text":"A"},{"id":"o1","text":"B"}]}
        current={"questions":[q],"snapshot":"same"}
        groq=Mock();groq.answer.return_value={"cannot_answer":False,"selected":["o0"]}
        feedback={"buttons":["Retry"],"feedback":"Saved result", "feedback_questions":[{"question":q,"earned":None,"possible":None,"feedback":"Unknown"}]}
        with patch('v2.assessments._wait_entry',return_value=current), patch('v2.assessments.inspect',return_value=current), patch('v2.assessments._wait_submit',return_value=current), patch('v2.assessments._wait',return_value={"failed":True}), patch('v2.assessments._read_failed_feedback',return_value=feedback), patch('v2.assessments.act') as action:
            with self.assertRaisesRegex(HandlerError,'automatic retry blocked'):
                _handle_assessment(Mock(),lambda:None,groq=groq,store=self.store,action_id=42)
        self.assertFalse(any(c.kwargs.get('label')=='Retry' for c in action.call_args_list))
        self.assertEqual(self.store.get_meta('submission:42'),'failed')
        self.assertEqual(groq.answer.call_count,1)

    def test_coursera_group_feedback_preserves_prompt_and_scores(self):
        self.page.set_content('<main><div role="group" aria-labelledby="legend"><div id="legend">Question 1<div id="prompt">Which number is even?</div></div><div role="radiogroup" aria-labelledby="prompt"><label><input type="radio" name="q">2</label><label><input type="radio" name="q">3</label></div></div></main>')
        before=inspect(self.page)['questions'][0]
        self.page.evaluate("""() => {
          document.querySelectorAll('input').forEach(e=>e.disabled=true);
          document.querySelector('[role=group]').insertAdjacentHTML('beforeend','<div id="q-grade-feedback">Incorrect. Review the definition.</div><div data-testid="part-points">0 / 1 point</div>');
        }""")
        row=inspect(self.page)['feedback_questions'][0]
        self.assertEqual(row['question'],before)
        self.assertEqual((row['earned'],row['possible']),(0,1))
        self.assertEqual(row['feedback'],'Incorrect. Review the definition.')

    def test_checkbox_feedback_preserves_prompt_and_scores(self):
        self.page.set_content('<main><div role="group" aria-labelledby="legend"><div id="legend">Question 1<div id="prompt">Which number is even?</div></div><div role="group" aria-labelledby="prompt"><label><input type="checkbox" name="q">2</label><label><input type="checkbox" name="q">3</label></div></div></main>')
        before=inspect(self.page)['questions'][0]
        self.page.evaluate("""() => {
          document.querySelectorAll('input').forEach(e=>e.disabled=true);
          document.querySelector('[role=group]').insertAdjacentHTML('beforeend','<div id="q-grade-feedback">Incorrect. Review the definition.</div><div data-testid="part-points">0 / 1 point</div>');
        }""")
        row=inspect(self.page)['feedback_questions'][0]
        self.assertEqual(row['question'],before)
        self.assertEqual((row['earned'],row['possible']),(0,1))
        self.assertEqual(row['feedback'],'Incorrect. Review the definition.')

    def test_retry_requires_real_individual_scores(self):
        from v2.assessments import _scored_feedback
        for row in [{}, {"earned":None,"possible":1}, {"earned":0,"possible":None},
                    {"earned":1,"possible":0}, {"earned":2,"possible":1}]:
            self.assertFalse(_scored_feedback(row))
        self.assertTrue(_scored_feedback({"earned":0,"possible":1}))
        self.assertTrue(_scored_feedback({"earned":1,"possible":1}))

    def test_empty_landing_reloads_once_but_attempt_does_not(self):
        from v2.assessments import _wait_entry
        page=Mock()
        error=HandlerError("Assignment did not reach a recognized state before timeout")
        with patch("v2.assessments._wait",side_effect=[error,{"passed":True}]), patch("v2.assessments.inspect",return_value={"path":"/learn/x/assignment-submission/id/title"}):
            self.assertTrue(_wait_entry(page,lambda:None)["passed"])
        page.reload.assert_called_once()
        page.reset_mock()
        with patch("v2.assessments._wait",side_effect=error), patch("v2.assessments.inspect",return_value={"path":"/learn/x/assignment-submission/id/title/attempt"}):
            with self.assertRaises(HandlerError): _wait_entry(page,lambda:None)
        page.reload.assert_not_called()

    def test_uncertain_submission_cannot_be_reset_and_repeated(self):
        self.store.set_meta('submission:99','submitting')
        with self.assertRaisesRegex(HandlerError,'reconciled'):
            handle_assessment(self.page,lambda:None,groq=Mock(),store=self.store,action_id=99)
        self.assertEqual(self.store.get_meta('submission:99'),'submitting')
        self.assertIsNone(self.page.evaluate('window.submits'))

    def test_submit_waits_for_saved_answers_and_enabled_button(self):
        state=inspect(self.page)
        plans=[(state['questions'][0],{'selected':['o0']}),(state['questions'][1],{'text':'4'})]
        for q,a in plans: act(self.page,inspect(self.page),action='fill',question_id=q['id'],answer=a)
        self.page.evaluate("document.querySelector('button').disabled=true;setTimeout(()=>document.querySelector('button').disabled=false,900)")
        ready=_wait_submit(self.page,lambda:None,state['snapshot'],plans,seconds=4)
        self.assertIn('Submit',ready['buttons'])
        self.assertIsNone(self.page.evaluate('window.submits'))

    def test_submit_rejects_changed_question(self):
        state=inspect(self.page)
        self.page.locator('legend').first.evaluate("e=>e.textContent='A different question?'")
        with self.assertRaisesRegex(HandlerError,'changed'):
            _wait_submit(self.page,lambda:None,state['snapshot'],[],seconds=1)
        self.assertIsNone(self.page.evaluate('window.submits'))

    def test_submit_never_accepts_unpersisted_answer(self):
        state=inspect(self.page)
        with self.assertRaises(HandlerError):
            _wait_submit(self.page,lambda:None,state['snapshot'],[(state['questions'][0],{'selected':['o0']})],seconds=1)
        self.assertIsNone(self.page.evaluate('window.submits'))

    def timed_dialog(self, extra=""):
        self.page.set_content("""<div role="dialog"><h2>Start new attempt?</h2><p>You have <b>30 minutes</b> to submit up to <b>1 time</b> for this attempt.</p><p>You will have <b>2 attempts left</b> after the timer runs out. After that, you will have to wait <b>24h</b> before you can try again.</p>""" + extra + """<button onclick="window.clicked=true;this.parentElement.outerHTML='<main><fieldset><legend>Which number is even?</legend><label><input type=radio name=q>2</label><label><input type=radio name=q>3</label></fieldset></main>'">Continue</button><button onclick="this.parentElement.remove()">Cancel</button></div>""")

    def test_live_time_wording_with_nbsp_is_recognized(self):
        self.timed_dialog()
        self.page.locator('[role=dialog]').evaluate("e => e.innerHTML=e.innerHTML.replace('timer runs out', 'time runs out').replace('out. After', 'out.&nbsp;After')")
        self.assertTrue(inspect(self.page)['start_confirmation'])
        result=_await_attempt(self.page,lambda:None,start_verified=True,seconds=3)
        self.assertTrue(result['questions'])
        self.assertTrue(self.page.evaluate('window.clicked'))

    def simple_timed_dialog(self, extra=""):
        self.timed_dialog(extra)
        self.page.locator('[role=dialog]').evaluate("""e => {
            const paragraphs = e.querySelectorAll('p');
            paragraphs[0].innerHTML = 'You have <b>20 minutes</b> to submit this attempt.';
            paragraphs[1].remove();
        }""")

    def test_simple_timed_start_requires_verified_entry(self):
        self.simple_timed_dialog()
        state = inspect(self.page)
        self.assertTrue(state['start_confirmation'])
        self.assertFalse(state['retry_confirmation'])
        with self.assertRaises(HandlerError):
            _await_attempt(self.page, lambda: None, seconds=2)
        self.assertIsNone(self.page.evaluate('window.clicked'))
        result = _await_attempt(self.page, lambda: None, start_verified=True, seconds=3)
        self.assertTrue(result['questions'])
        self.assertTrue(self.page.evaluate('window.clicked'))

    def test_simple_timed_unknown_terms_or_duplicate_button_blocked(self):
        for extra in ['<p>I agree to new terms and payment.</p>', '<button>Continue</button>']:
            with self.subTest(extra=extra):
                self.simple_timed_dialog(extra)
                state = inspect(self.page)
                self.assertFalse(state['start_confirmation'])
                with self.assertRaises(HandlerError):
                    act(self.page, state, action='button', label='Continue', start_attempt_verified=True)
                self.assertIsNone(self.page.evaluate('window.clicked'))

    def test_existing_simple_timed_dialog_cancelled_before_result_read(self):
        from v2.assessments import _handle_assessment
        self.simple_timed_dialog()
        with patch('v2.assessments._wait_entry', side_effect=[inspect(self.page), HandlerError('landing inspected')]):
            with self.assertRaisesRegex(HandlerError, 'landing inspected'):
                _handle_assessment(self.page, lambda: None, groq=Mock(), store=self.store, action_id=1)
        self.assertEqual(self.page.locator('[role=dialog]').count(), 0)
        self.assertIsNone(self.page.evaluate('window.clicked'))

    def test_simple_timer_with_replacement_requires_failed_result(self):
        self.simple_timed_dialog()
        self.page.locator('[role=dialog]').evaluate("e => e.innerHTML=e.innerHTML.replace('</h2>', '</h2><p>When you submit a new attempt it will replace your previous submission. This new attempt will only replace your previous grade if it’s higher.</p>')")
        state = inspect(self.page)
        self.assertTrue(state['retry_confirmation'])
        self.assertFalse(state['start_confirmation'])
        with self.assertRaises(HandlerError):
            _await_attempt(self.page, lambda: None, start_verified=True, seconds=2)
        self.assertIsNone(self.page.evaluate('window.clicked'))
        result = _await_attempt(self.page, lambda: None, failed_verified=True, seconds=3)
        self.assertTrue(result['questions'])

    def test_timed_start_requires_verified_entry(self):
        self.timed_dialog()
        self.assertTrue(inspect(self.page)['start_confirmation'])
        with self.assertRaises(HandlerError):
            _await_attempt(self.page,lambda:None,seconds=2)
        self.assertIsNone(self.page.evaluate('window.clicked'))
        state=_await_attempt(self.page,lambda:None,start_verified=True,seconds=3)
        self.assertTrue(state['questions'])
        self.assertTrue(self.page.evaluate('window.clicked'))

    def test_timed_unknown_extra_terms_never_authorize_continue(self):
        self.timed_dialog('<p>I agree to new terms and payment.</p>')
        state=inspect(self.page)
        self.assertFalse(state['start_confirmation'])
        with self.assertRaises(HandlerError):
            act(self.page,state,action='button',label='Continue',start_attempt_verified=True)
        self.assertIsNone(self.page.evaluate('window.clicked'))

    def test_timed_duplicate_continue_is_rejected(self):
        self.timed_dialog('<button>Continue</button>')
        with self.assertRaises(HandlerError):
            act(self.page,inspect(self.page),action='button',label='Continue',start_attempt_verified=True)
        self.assertIsNone(self.page.evaluate('window.clicked'))

    def test_existing_timed_dialog_is_cancelled_before_result_read(self):
        from v2.assessments import _handle_assessment
        self.timed_dialog()
        with patch('v2.assessments._wait_entry',side_effect=[inspect(self.page),HandlerError('landing inspected')]):
            with self.assertRaisesRegex(HandlerError,'landing inspected'):
                _handle_assessment(self.page,lambda:None,groq=Mock(),store=self.store,action_id=1)
        self.assertEqual(self.page.locator('[role=dialog]').count(),0)
        self.assertIsNone(self.page.evaluate('window.clicked'))

    def test_combined_retry_requires_feedback_even_with_start_permission(self):
        for duration in ['24h','23h 59m','1h 2m','59m']:
            self.timed_dialog()
            self.page.locator('[role=dialog]').evaluate("(e,d)=>{e.innerHTML=e.innerHTML.replace('</h2>', '</h2><p>When you submit a new attempt it will replace your previous submission. This new attempt will only replace your previous grade if it’s higher.</p>').replace('24h',d);}",duration)
            state=inspect(self.page)
            self.assertTrue(state['retry_confirmation'],duration)
            self.assertFalse(state['start_confirmation'])
            with self.assertRaises(HandlerError):
                _await_attempt(self.page,lambda:None,start_verified=True,seconds=2)
            self.assertIsNone(self.page.evaluate('window.clicked'))
            state=_await_attempt(self.page,lambda:None,failed_verified=True,seconds=3)
            self.assertTrue(state['questions'])
            self.page.evaluate('window.clicked=undefined')

    def test_existing_retry_dialog_cancelled_before_feedback_reread(self):
        from v2.assessments import _handle_assessment
        self.timed_dialog()
        self.page.locator('[role=dialog]').evaluate("e=>e.innerHTML=e.innerHTML.replace('</h2>', '</h2><p>When you submit a new attempt it will replace your previous submission. This new attempt will only replace your previous grade if it’s higher.</p>')")
        with patch('v2.assessments._wait_entry',side_effect=[inspect(self.page),HandlerError('reread result')]):
            with self.assertRaisesRegex(HandlerError,'reread result'):
                _handle_assessment(self.page,lambda:None,groq=Mock(),store=self.store,action_id=1)
        self.assertEqual(self.page.locator('[role=dialog]').count(),0)
        self.assertIsNone(self.page.evaluate('window.clicked'))

    def remaining_attempts_dialog(self, extra=''):
        self.simple_timed_dialog()
        self.page.locator('[role=dialog] p').evaluate("""e => e.outerHTML =
            "<p>When you submit a new attempt it will replace your previous submission. This new attempt will only replace your previous grade if it's higher.</p><p>You will have <b>2 attempts left</b> after this one. After that, you will have to wait <b>8h</b> before you can try again.</p>"
        """)
        self.page.locator('[role=dialog]').evaluate("(e, extra) => e.insertAdjacentHTML('beforeend', extra)", extra)

    def test_remaining_attempts_retry_requires_failed_result(self):
        self.remaining_attempts_dialog()
        state = inspect(self.page)
        self.assertTrue(state['retry_confirmation'])
        self.assertFalse(state['start_confirmation'])
        with self.assertRaisesRegex(HandlerError, 'verified failed'):
            _await_attempt(self.page, lambda: None, start_verified=True, seconds=2)
        self.assertIsNone(self.page.evaluate('window.clicked'))
        result = _await_attempt(self.page, lambda: None, failed_verified=True, seconds=3)
        self.assertTrue(result['questions'])
        self.assertTrue(self.page.evaluate('window.clicked'))

    def test_remaining_attempts_unknown_terms_duplicate_buttons_and_dialogs_blocked(self):
        for extra in ['<p>I accept new payment terms.</p>', '<button>Continue</button>',
                      '<div role="dialog">Other confirmation</div>']:
            with self.subTest(extra=extra):
                self.remaining_attempts_dialog(extra)
                state = inspect(self.page)
                self.assertFalse(state['retry_confirmation'])
                with self.assertRaises(HandlerError):
                    act(self.page, state, action='button', label='Continue', failed_attempt_verified=True)
                self.assertIsNone(self.page.evaluate('window.clicked'))

    def test_existing_remaining_attempts_dialog_cancelled_before_result_read(self):
        from v2.assessments import _handle_assessment
        self.remaining_attempts_dialog()
        with patch('v2.assessments._wait_entry', side_effect=[inspect(self.page), HandlerError('reread result')]):
            with self.assertRaisesRegex(HandlerError, 'reread result'):
                _handle_assessment(self.page, lambda: None, groq=Mock(), store=self.store, action_id=1)
        self.assertEqual(self.page.locator('[role=dialog]').count(), 0)
        self.assertIsNone(self.page.evaluate('window.clicked'))

    def test_verified_retry_dialog(self):
        self.page.set_content("""<div role="dialog">Start new attempt?<p>When you submit a new attempt it will replace your previous submission. This new attempt will only replace your previous grade if it’s higher.</p><button onclick="this.parentElement.outerHTML='<main><fieldset><legend>Which number is even?</legend><label><input type=radio name=q>2</label><label><input type=radio name=q>3</label></fieldset></main>'">Continue</button> <button>Cancel</button></div>""")
        with self.assertRaisesRegex(HandlerError,'verified failed'):
            _await_attempt(self.page,lambda:None,failed_verified=False,seconds=2)
        state=_await_attempt(self.page,lambda:None,failed_verified=True,seconds=3)
        self.assertEqual(len(state['questions']),1)
        self.assertEqual(self.page.locator('input:checked').count(),0)

    def test_curly_apostrophe_failure(self):
        self.page.set_content('<main>You didn’t pass. To pass you need a grade of at least 80%. 75%<button>Try again</button></main>')
        self.assertTrue(inspect(self.page)['failed'])
        self.assertFalse(inspect(self.page)['passed'])

    def test_delayed_option_labels_are_awaited(self):
        self.page.set_content('<main><fieldset><legend>1. Choose the variable.</legend><label><input type="radio" name="q"><span></span></label><label><input type="radio" name="q"><span></span></label></fieldset></main>')
        self.page.evaluate("setTimeout(()=>document.querySelectorAll('label span').forEach((e,i)=>e.textContent=i?'X':'x'),700)")
        state=_wait(self.page,lambda:None,lambda s:bool(s.get('questions')),seconds=4)
        self.assertEqual([o['text'] for o in state['questions'][0]['options']],['x','X'])
        self.assertEqual(self.page.locator('input:checked').count(),0)

    def test_persistent_ambiguity_times_out_without_selecting(self):
        self.page.set_content('<main><fieldset><legend>1. Choose the variable.</legend><label><input type="radio" name="q">x</label><label><input type="radio" name="q">x</label></fieldset></main>')
        with self.assertRaisesRegex(HandlerError,'Ambiguous answer options'):
            _wait(self.page,lambda:None,lambda s:bool(s.get('questions')),seconds=1)
        self.assertEqual(self.page.locator('input:checked').count(),0)

    def test_numeric_group_uses_sibling_prompt_not_answer_numbers(self):
        self.page.set_content('<main><section><p>1.</p><div id="prompt-one">Did you finish the activity?</div><textarea aria-labelledby="prompt-one"></textarea></section><section><p>2.</p><div id="prompt-two">Rate your understanding (1 least, 5 most).</div><div role="group" aria-labelledby="prompt-two"><label><input type="checkbox">1</label><label><input type="checkbox">2</label><label><input type="checkbox">3</label><label><input type="checkbox">4</label><label><input type="checkbox">5</label></div></section></main>')
        state=inspect(self.page)
        self.assertEqual(len(state['questions']),2)
        self.assertEqual(state['questions'][0]['text'],'Did you finish the activity?')
        self.assertIn('Rate your understanding',state['questions'][1]['text'])
        self.assertEqual([o['text'] for o in state['questions'][1]['options']],['1','2','3','4','5'])
        self.page.locator('#prompt-two').evaluate("e=>e.remove()")
        with self.assertRaises(HandlerError):
            inspect(self.page)

    def test_observed_coursera_math_labels(self):
        self.page.set_content((Path(__file__).parent/'fixtures'/'math_options.html').read_text(encoding='utf-8'))
        state=inspect(self.page)
        options=state['questions'][0]['options']
        self.assertEqual(len(options),4)
        self.assertEqual(len({o['text'] for o in options}),4)
        self.assertTrue(all(o['text'] for o in options))

    def test_hidden_content_math_and_case_are_preserved(self):
        self.page.set_content("""<main><fieldset><legend>1. Choose the variable.</legend>
        <div style="display:none">HIDDEN INSTRUCTION</div>
        <label><input type="radio" name="q"><span class="katex"><span>rendered x</span><annotation encoding="application/x-tex">x^{(l)}</annotation></span></label>
        <label><input type="radio" name="q"><span class="katex"><span>rendered X</span><annotation encoding="application/x-tex">X^{(l)}</annotation></span></label>
        </fieldset></main>""")
        q=inspect(self.page)["questions"][0]
        self.assertNotIn("HIDDEN",q["text"])
        self.assertEqual([o["text"] for o in q["options"]],["$x^{(l)}$","$X^{(l)}$"])

    def test_identical_options_remain_blocked(self):
        self.page.set_content('<main><fieldset><legend>1. Choose a variable.</legend><label><input type="radio" name="q">x</label><label><input type="radio" name="q">x</label></fieldset></main>')
        with self.assertRaisesRegex(HandlerError,"Ambiguous answer options"):
            inspect(self.page)

    def test_extracts_and_fills_independent_questions(self):
        state=inspect(self.page)
        self.assertEqual([q['kind'] for q in state['questions']],['single','number'])
        q=state['questions'][0]
        act(self.page,state,action='fill',question_id=q['id'],answer={'selected':['o0']})
        self.assertTrue(self.page.locator('input[value="2"]').is_checked())
        self.assertFalse(self.page.locator('input[value="3"]').is_checked())

    def test_delayed_start_assignment_is_awaited_despite_unrelated_button(self):
        self.page.set_content('<main><button>Help me practice</button></main>')
        self.page.evaluate('''form => {
          window.starts=0;
          setTimeout(() => {
            const button=document.createElement('button');
            button.textContent='Start assignment';
            button.onclick=()=>{window.starts++;document.body.innerHTML=form;};
            document.querySelector('main').append(button);
          }, 1000);
        }''', FORM)
        groq=Mock()
        groq.answer.side_effect=lambda q: {'selected':['o0']} if q['kind']=='single' else {'text':'4'}
        handle_assessment(self.page,lambda:None,groq=groq,store=self.store,action_id=1)
        self.assertEqual(groq.answer.call_count,2)
        self.assertEqual(self.store.get_meta('submission:1'),'passed')

    def test_feedback_already_open_waits_for_question_results(self):
        self.page.goto('https://www.coursera.org/learn/example/assignment-submission/abc/test/view-feedback')
        self.page.set_content('<main><h2>Failed</h2><p>Grade: 75%</p><button>Retry</button></main>')
        self.page.evaluate("setTimeout(()=>document.querySelector('main').insertAdjacentHTML('beforeend', '<fieldset disabled><legend>1. Which number is even?</legend><label><input type=radio name=q>2</label><label><input type=radio name=q>3</label><div class=points>0 / 1 points</div></fieldset>'),700)")
        result=_read_failed_feedback(self.page,inspect(self.page),lambda:None,seconds=3)
        self.assertEqual(len(result['feedback_questions']),1)
        self.assertEqual(result['feedback_questions'][0]['earned'],0)
        self.assertEqual(result['questions'],[])

    def test_feedback_header_alone_does_not_authorize_retry(self):
        self.page.goto('https://www.coursera.org/learn/example/assignment-submission/abc/test/view-feedback')
        self.page.set_content('<main><h2>Failed</h2><p>Grade: 75%</p><button>Retry</button></main>')
        with self.assertRaisesRegex(HandlerError,'recognized state'):
            _read_failed_feedback(self.page,inspect(self.page),lambda:None,seconds=1)

    def test_feedback_grade_and_threshold_confirm_result_without_passed_word(self):
        self.page.goto('https://www.coursera.org/learn/example/assignment-submission/abc/test/view-feedback')
        self.page.set_content('<main><div><div><h2>Your grade: 100%</h2><p>To pass you need at least 66%. We keep your highest score.</p></div></div><button>Back</button></main>')
        result=inspect(self.page)
        self.assertTrue(result['passed'])
        self.assertEqual(result['grade_evidence']['grade'],100)
        self.page.locator('h2').evaluate("e=>e.textContent='Your grade: 33%'")
        self.assertTrue(inspect(self.page)['failed'])
        self.page.locator('p').evaluate("e=>e.remove()")
        self.assertFalse(inspect(self.page)['passed'])
        self.assertFalse(inspect(self.page)['failed'])

    def test_explicit_authorization_confirms_only_coursera_checkbox(self):
        self.page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend', '<p>By clicking Submit, you confirm that this work is your own.</p><label><input id=honor type=checkbox>I, Learner, understand and agree.</label>')")
        self.store.set_control_state('running')
        self.store.set_meta('auto_confirm_coursera',True)
        groq=Mock()
        def answer(q):
            self.assertTrue(self.page.locator('#honor').is_checked())
            return {'cannot_answer':False,'selected':['o0']} if q['kind']=='single' else {'cannot_answer':False,'text':'4'}
        groq.answer.side_effect=answer
        handle_assessment(self.page,lambda:None,groq=groq,store=self.store,action_id=1)
        self.assertEqual(self.store.get_meta('submission:1'),'passed')
        self.assertEqual(self.store.get_control_state(),'running')

    def test_confirmation_action_rejects_unknown_and_changed_page(self):
        r=self.page.evaluate(DOM,{'action':'confirm_authorship'})
        self.assertFalse(r['ok'])
        r=self.page.evaluate(DOM,{'action':'confirm_authorship','expected_path':'/wrong'})
        self.assertIn('changed',r['error'])
        self.assertEqual(self.page.locator('input:checked').count(),0)

    def test_confirmation_pauses_and_resumes_same_quiz_only_after_manual_check(self):
        self.page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend', '<p>By clicking Submit, you confirm that this work is your own.</p><label><input id=honor type=checkbox>I, Learner, understand and agree.</label>')")
        self.store.set_control_state('running')
        pauses=[]
        groq=Mock()
        def checkpoint():
            if self.store.get_control_state()=='paused':
                pauses.append(self.store.get_meta('confirmation_required'))
                groq.answer.assert_not_called()
                if len(pauses)==2:
                    self.page.locator('#honor').check() # Simulated human action.
                self.store.set_control_state('running')
        groq.answer.side_effect=lambda q: {'cannot_answer':False,'selected':['o0']} if q['kind']=='single' else {'cannot_answer':False,'text':'4'}
        handle_assessment(self.page,checkpoint,groq=groq,store=self.store,action_id=1)
        self.assertEqual(len(pauses),2)
        self.assertEqual(groq.answer.call_count,2)
        self.assertEqual(self.store.get_meta('submission:1'),'passed')
        self.assertIsNone(self.store.get_meta('confirmation_required'))

    def test_stop_during_confirmation_does_not_call_model_or_submit(self):
        from v2.runner import StopRequested
        self.page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend','<p>By clicking Submit, you confirm that this work is your own.</p>')")
        self.store.set_control_state('running')
        groq=Mock()
        def checkpoint():
            if self.store.get_control_state()=='paused':
                self.store.set_control_state('stop_requested')
                raise StopRequested('user stop')
        with self.assertRaises(StopRequested):
            handle_assessment(self.page,checkpoint,groq=groq,store=self.store,action_id=1)
        groq.answer.assert_not_called()
        self.assertIsNone(self.page.evaluate('window.submits'))

    def test_late_authorship_notice_blocks_fill_and_model(self):
        state=inspect(self.page)
        self.page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend','<p>By clicking Submit, you confirm that this work is your own.</p>')")
        with self.assertRaises(HandlerError):
            act(self.page,state,action='fill',question_id='q0',answer={'selected':['o0']})
        groq=Mock()
        with self.assertRaises(HandlerError):
            handle_assessment(self.page,lambda:None,groq=groq,store=self.store,action_id=1)
        groq.answer.assert_not_called()
        self.assertFalse(self.page.locator('input[value="2"]').is_checked())

    def test_changed_form_during_model_request_blocks_action(self):
        def answer(q):
            self.page.locator('legend').first.evaluate("e=>e.innerText='1. Which number is odd?'")
            return {'cannot_answer':False,'selected':['o0']}
        groq=Mock(answer=answer)
        with self.assertRaisesRegex(HandlerError,'changed during'):
            handle_assessment(self.page,lambda:None,groq=groq,store=self.store,action_id=1)
        self.assertFalse(self.page.locator('input[value="2"]').is_checked())
        self.assertEqual(self.store.get_meta('submission:1'),'not_submitted')

    def test_submission_records_intent_before_click_and_does_not_cache_total_grade(self):
        questions=[]
        def answer(q):
            questions.append(q)
            return {'cannot_answer':False,'selected':['o0']} if q['kind']=='single' else {'cannot_answer':False,'text':'4'}
        groq=Mock(answer=answer,memory=AnswerMemory(self.store))
        result=handle_assessment(self.page,lambda:None,groq=groq,store=self.store,action_id=1)
        self.assertIn('reported a pass',result.message)
        self.assertEqual(len(questions),2)
        self.assertEqual(self.store.get_meta('submission:1'),'passed')
        self.assertTrue(all(groq.memory.confirmed(q) is None for q in questions))

    def test_submit_rejects_unpersisted_answer(self):
        state=inspect(self.page)
        plans=[(q,{'selected':['o0']} if q['kind']=='single' else {'text':'4'}) for q in state['questions']]
        with self.assertRaisesRegex(HandlerError,'persist'):
            act(self.page,state,action='button',label='Submit',answers=plans)
        self.assertIsNone(self.page.evaluate('window.submits'))

    def test_feedback_has_exact_question_score_not_overall_grade(self):
        before=inspect(self.page)['questions'][0]
        self.page.evaluate("""() => {
          document.querySelector('fieldset').disabled=true;
          document.querySelector('fieldset').insertAdjacentHTML('beforeend','<span class="points">1 / 1 point</span><p class="feedback">Correct</p>');
        }""")
        state=inspect(self.page)
        row=state['feedback_questions'][0]
        self.assertEqual(row['question'],before)
        self.assertEqual((row['earned'],row['possible']),(1,1))

    def test_unknown_file_upload_is_not_treated_as_quiz(self):
        self.page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend','<input type=file>')")
        with self.assertRaisesRegex(HandlerError,'dedicated adapter'): inspect(self.page)

    def test_text_multiple_and_select_controls_keep_independent_answers(self):
        self.page.set_content('''<main>
        <fieldset><legend>1. Select even numbers.</legend>
        <label><input type="checkbox">2</label><label><input type="checkbox">4</label><label><input type="checkbox">5</label></fieldset>
        <fieldset><legend>2. Choose a color.</legend><select><option value="">Choose</option><option value="r">Red</option><option value="b">Blue</option></select></fieldset>
        <fieldset><legend>3. Explain a counterfactual.</legend><textarea maxlength="500"></textarea></fieldset>
        <fieldset><legend>4. Enter a short answer.</legend><input type="text"></fieldset>
        <button>Submit</button></main>''')
        state=inspect(self.page)
        self.assertEqual([q['kind'] for q in state['questions']],['multiple','select','long_text','text'])
        answers=[{'selected':['o0','o1']},{'selected':['b']},{'text':'A changed premise can produce a different outcome.'},{'text':'Example'}]
        for q,a in zip(state['questions'],answers):
            act(self.page,inspect(self.page),action='fill',question_id=q['id'],answer=a)
        actual=inspect(self.page)['values']
        self.assertEqual(actual[0]['selected'],['o0','o1'])
        self.assertEqual(actual[1]['selected'],['b'])
        self.assertEqual(actual[2]['text'],answers[2]['text'])
        self.assertEqual(actual[3]['text'],'Example')

    def test_ambiguous_post_submit_does_not_click_twice(self):
        self.page.locator('button').evaluate("e=>e.onclick=()=>{window.submits=(window.submits||0)+1;}")
        groq=Mock(answer=lambda q: {'cannot_answer':False,'selected':['o0']} if q['kind']=='single' else {'cannot_answer':False,'text':'4'})
        from unittest.mock import patch
        from v2 import assessments
        real_wait=assessments._wait
        def short_wait(page,checkpoint,predicate,**kwargs):
            return real_wait(page,checkpoint,predicate,seconds=3)
        with patch.object(assessments,'_wait',side_effect=short_wait):
            with self.assertRaisesRegex(HandlerError,'timeout'):
                handle_assessment(self.page,lambda:None,groq=groq,store=self.store,action_id=1)
        self.assertEqual(self.page.evaluate('window.submits'),1)
        self.assertEqual(self.store.get_meta('submission:1'),'submitting')


if __name__=='__main__':unittest.main()
