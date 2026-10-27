"""Offline contract checks for the narrowly recognized translation survey.

Only the root markers and prompt prefix were captured from the live page.
The remaining markup, text, controls, and handlers below are synthetic fixtures;
they are not a reproduction of the complete historical Pendo survey.
"""

import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import (
    ElementHandle,
    Error as BrowserError,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from tests.test_browser_discovery_outline import OUTLINE_HTML
from tests.test_deadline_overlay import FORM, warning
from v2.assessments import _wait
from v2.outline import scan_outline
from v2.recovery import read_retry
from v2.ui import (
    UnknownOverlay,
    dismiss_learning_overlay,
    dismiss_translation_survey,
    require_clear_outline,
)


PROMPT = "How effective are the translations in this course?"
GOAL = """<div id="goal" role="dialog"
style="position:fixed;inset:20px;background:white;z-index:1000">
You've completed today's goals!
<button id="goal-close" type="button" aria-label="Close"
onclick="window.goalClicks=(window.goalClicks||0)+1;this.parentElement.remove()">X</button>
</div>"""


def survey(*, prompt=PROMPT, close_name="Close", close_extra="", extra="",
           root_extra="", close_handler=None):
    """Build a synthetic survey satisfying the intended recognition contract."""
    if close_handler is None:
        close_handler = """window.surveyClicks=(window.surveyClicks||0)+1;
window.surveyBeforeClose={checked:this.parentElement.querySelectorAll('input:checked').length,
feedback:this.parentElement.querySelector('textarea').value};
if(window.replaceGoalOnDismiss){const goal=document.getElementById('goal');
if(goal)goal.replaceWith(goal.cloneNode(true));}
this.parentElement.remove()"""
    return f"""<div id="pendo-base" data-layout="lightboxBlank"
class="_pendo-step-container _pendo-guide-walkthrough_" {root_extra}
style="position:fixed;left:100px;top:100px;width:600px;padding:50px 25px 25px;background:white;z-index:2000">
<p id="pendo-text-1ecb6615" class="_pendo-text-plain">{prompt}</p>
<form onsubmit="event.preventDefault();window.surveySubmits=(window.surveySubmits||0)+1">
<label><input type="radio" name="translation-rating" value="1"
onchange="window.ratingChanges=(window.ratingChanges||0)+1">One</label>
<label><input type="radio" name="translation-rating" value="5"
onchange="window.ratingChanges=(window.ratingChanges||0)+1">Five</label>
<textarea aria-label="Optional feedback"
oninput="window.feedbackChanges=(window.feedbackChanges||0)+1"></textarea>
<button type="submit">Submit</button></form>{extra}
<button id="survey-close" type="button" aria-label="{close_name}" {close_extra}
style="position:absolute;right:10px;top:10px;width:90px;height:30px"
onclick="{close_handler}">X</button></div>"""


class TranslationSurveyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        # All fixture navigations are fulfilled locally, including hostile hosts.
        self.page.route("**/*", lambda route: route.fulfill(body="<html><body></body></html>"))
        self.page.goto("https://www.coursera.org/learn/example-course/assignment-submission/exam")

    def tearDown(self):
        self.context.close()

    def assert_no_survey_answer_or_submission(self):
        for counter in ("ratingChanges", "feedbackChanges", "surveySubmits", "submits"):
            self.assertIsNone(self.page.evaluate(f"window.{counter}"), counter)
        self.assertEqual(self.page.locator("main input:checked").count(), 0)

    def assert_blocked(self, html):
        self.page.set_content(html)
        self.page.evaluate("window.surveyClicks=undefined")
        with self.assertRaises(UnknownOverlay):
            dismiss_translation_survey(self.page)
        self.assertIsNone(self.page.evaluate("window.surveyClicks"))
        self.assert_no_survey_answer_or_submission()

    def test_recognized_survey_dismisses_once_without_answering_or_submitting(self):
        self.page.set_content(FORM + survey())
        self.assertTrue(dismiss_translation_survey(self.page))
        self.assertFalse(dismiss_translation_survey(self.page))
        self.assertEqual(self.page.evaluate("window.surveyClicks"), 1)
        self.assertEqual(self.page.evaluate("window.surveyBeforeClose"), {"checked": 0, "feedback": ""})
        self.assertEqual(self.page.locator("#pendo-base").count(), 0)
        self.assert_no_survey_answer_or_submission()

    def test_supported_accessible_close_names(self):
        for name in ("Close", "Close guide", "Close survey", "Dismiss"):
            with self.subTest(name=name):
                self.page.set_content(survey(close_name=name))
                self.page.evaluate("window.surveyClicks=undefined")
                self.assertTrue(dismiss_translation_survey(self.page))
                self.assertEqual(self.page.evaluate("window.surveyClicks"), 1)

    def test_native_role_button_can_dismiss_without_filling_survey(self):
        html = survey().replace('<button id="survey-close"', '<div role="button" tabindex="0" id="survey-close"')
        html = html.replace('>X</button></div>', '>X</div></div>')
        self.page.set_content(html)
        self.assertTrue(dismiss_translation_survey(self.page))
        self.assertEqual(self.page.evaluate("window.surveyClicks"), 1)
        self.assert_no_survey_answer_or_submission()

    def test_aria_labelledby_close_name_is_supported(self):
        html = survey(extra='<span id="close-label">Close survey</span>')
        html = html.replace('aria-label="Close"', 'aria-labelledby="close-label"')
        self.page.set_content(html)
        self.assertTrue(dismiss_translation_survey(self.page))
        self.assertEqual(self.page.evaluate("window.surveyClicks"), 1)

    def test_title_cannot_override_visible_submit_name(self):
        html = survey(close_name="", close_extra='title="Close"')
        self.assert_blocked(html.replace('>X</button></div>', '>Submit</button></div>'))

    def test_no_pendo_does_not_click_plain_page_lookalikes(self):
        self.page.set_content(f'<main><p>{PROMPT}</p><button aria-label="Close" '
                              'onclick="window.backgroundClicks=1">X</button></main>')
        self.assertFalse(dismiss_translation_survey(self.page))
        self.assertIsNone(self.page.evaluate("window.backgroundClicks"))

    def test_hidden_survey_is_not_clicked(self):
        self.page.set_content(survey())
        self.page.locator("#pendo-base").evaluate("root => root.style.display='none'")
        self.assertFalse(dismiss_translation_survey(self.page))
        self.assertIsNone(self.page.evaluate("window.surveyClicks"))

    def test_transparent_root_or_ancestor_is_not_clicked(self):
        for selector in ("#pendo-base", "#survey-wrapper"):
            with self.subTest(selector=selector):
                self.page.set_content('<div id="survey-wrapper">' + survey() + '</div>')
                self.page.evaluate("window.surveyClicks=undefined")
                self.page.locator(selector).evaluate("root => root.style.opacity='0'")
                self.assertFalse(dismiss_translation_survey(self.page))
                self.assertIsNone(self.page.evaluate("window.surveyClicks"))

    def test_dismissal_hiding_root_with_opacity_is_verified(self):
        self.page.set_content(survey(close_handler="window.surveyClicks=1;this.parentElement.style.opacity='0'"))
        self.assertTrue(dismiss_translation_survey(self.page))
        self.assertFalse(dismiss_translation_survey(self.page))
        self.assertEqual(self.page.evaluate("window.surveyClicks"), 1)

    def test_changed_root_or_prompt_markers_are_blocked(self):
        original = survey()
        cases = (
            original.replace('data-layout="lightboxBlank"', 'data-layout="other"'),
            original.replace('_pendo-guide-walkthrough_', '_pendo-other-guide_'),
            original.replace('_pendo-step-container', '_pendo-other-container'),
            original.replace('id="pendo-text-1ecb6615"', 'id="pendo-text-other"'),
            original.replace('class="_pendo-text-plain"', 'class="other-prompt"'),
            survey(prompt="How much would you pay for this course?"),
        )
        for html in cases:
            with self.subTest(html=html[:180]):
                self.assert_blocked(html)

    def test_changed_unknown_or_duplicate_close_controls_are_blocked(self):
        cases = (
            survey(close_name="Continue"),
            survey(close_extra="disabled"),
            survey(close_extra='aria-disabled="true"'),
            survey(extra='<button type="button" aria-label="Close survey">X</button>'),
            survey().replace('<button id="survey-close"', '<a href="https://example.test/" id="survey-close"')
                    .replace('>X</button></div>', '>X</a></div>'),
        )
        for html in cases:
            with self.subTest(html=html[-300:]):
                self.assert_blocked(html)

    def test_extra_consent_or_authored_terms_are_blocked(self):
        for extra in (
            '<label><input type="checkbox">I agree to the terms</label>',
            '<p>I agree to new terms and payment.</p>',
            '<p>Please accept updated terms.</p>',
            '<p>I certify that this work is my own.</p>',
            '<div role="dialog">Confirm authorship<button>Accept</button></div>',
        ):
            with self.subTest(extra=extra):
                self.assert_blocked(survey(extra=extra))

    def test_obscured_close_is_blocked(self):
        blocker = '<div style="position:fixed;inset:0;z-index:9999;background:white">Unknown overlay</div>'
        self.assert_blocked(survey() + blocker)

    def test_duplicate_visible_pendo_roots_are_blocked(self):
        self.assert_blocked(survey() + survey())

    def test_hostile_origin_is_not_dismissed(self):
        self.page.goto("https://www.coursera.org.example.test/learn/course/quiz/example")
        self.assert_blocked(survey())

    def test_close_that_leaves_survey_visible_is_not_reported_as_success(self):
        self.page.set_content(survey(close_handler="window.surveyClicks=(window.surveyClicks||0)+1"))
        with self.assertRaises(UnknownOverlay):
            dismiss_translation_survey(self.page)
        self.assertEqual(self.page.evaluate("window.surveyClicks"), 1)
        self.assertTrue(self.page.locator("#pendo-base").is_visible())

    def test_survey_above_goal_is_dismissed_before_goal_in_same_call(self):
        self.page.set_content(FORM + GOAL + survey())
        self.assertTrue(dismiss_learning_overlay(self.page))
        self.assertEqual(self.page.evaluate("window.surveyClicks"), 1)
        self.assertEqual(self.page.evaluate("window.goalClicks"), 1)
        self.assertEqual(self.page.locator("#pendo-base,#goal").count(), 0)
        self.assert_no_survey_answer_or_submission()

    def test_survey_above_deadline_is_dismissed_in_same_call(self):
        self.page.set_content(FORM + warning() + survey())
        self.assertTrue(dismiss_learning_overlay(self.page))
        self.assertEqual(self.page.evaluate("window.surveyClicks"), 1)
        self.assertEqual(self.page.evaluate("window.deadlineClicks"), 1)
        self.assert_no_survey_answer_or_submission()

    def test_late_survey_interception_rechecks_survey_and_reacquires_goal_once(self):
        self.page.set_content(FORM + GOAL)
        original_click = ElementHandle.click
        attempts = []

        def intercepted_click(handle, *args, **kwargs):
            if handle.get_attribute("id") == "goal-close":
                attempts.append(True)
                if len(attempts) == 1:
                    self.page.evaluate("html => {window.replaceGoalOnDismiss=true;"
                                       "document.body.insertAdjacentHTML('beforeend', html)}", survey())
                    raise PlaywrightTimeout("<div id=\"pendo-base\"> intercepts pointer events")
            return original_click(handle, *args, **kwargs)

        with patch.object(ElementHandle, "click", intercepted_click):
            self.assertTrue(dismiss_learning_overlay(self.page))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.page.evaluate("window.surveyClicks"), 1)
        self.assertEqual(self.page.evaluate("window.goalClicks"), 1)
        self.assert_no_survey_answer_or_submission()

    def test_unknown_goal_click_outcome_is_not_replayed(self):
        self.page.set_content(GOAL)
        attempts = []

        def uncertain_click(handle, *args, **kwargs):
            attempts.append(True)
            raise PlaywrightTimeout("Timeout after the click may have reached the page")

        with patch.object(ElementHandle, "click", uncertain_click):
            with self.assertRaises(UnknownOverlay):
                read_retry(dismiss_learning_overlay)(self.page)
        self.assertEqual(len(attempts), 1)
        self.assertIsNone(self.page.evaluate("window.goalClicks"))

    def test_roleless_unknown_pendo_blocks_outline_guard(self):
        self.page.set_content(OUTLINE_HTML.replace("</body>",
            survey(prompt="Review and accept a new agreement") + "</body>"))
        with self.assertRaises(UnknownOverlay):
            require_clear_outline(self.page)
        self.assertIsNone(self.page.evaluate("window.surveyClicks"))

    def test_outline_scan_preserves_completion_evidence(self):
        self.page.set_content(OUTLINE_HTML.replace("</body>", survey() + "</body>"))
        snapshot = scan_outline(self.page)
        self.assertEqual(len(snapshot.items), 3)
        self.assertEqual([item.completed for item in snapshot.items], [True, True, False])
        self.assertEqual(self.page.evaluate("window.surveyClicks"), 1)

    def test_assessment_wait_dismisses_survey_without_answer_or_submission(self):
        self.page.set_content(FORM + survey())
        result = _wait(self.page, lambda: None, lambda value: bool(value.get("questions")), seconds=3)
        self.assertEqual(len(result["questions"]), 1)
        self.assertEqual(self.page.evaluate("window.surveyClicks"), 1)
        self.assert_no_survey_answer_or_submission()

    def test_unknown_roleless_pendo_blocks_assessment_wait(self):
        self.page.set_content(FORM + survey(prompt="Confirm your purchase"))
        with self.assertRaises(UnknownOverlay):
            _wait(self.page, lambda: None, lambda value: bool(value.get("questions")), seconds=3)
        self.assert_no_survey_answer_or_submission()


class TranslationSurveyRetryTests(unittest.TestCase):
    def test_browser_error_during_survey_dismissal_is_not_replayed(self):
        page = Mock(url="https://www.coursera.org/learn/example-course/quiz/example")
        page.evaluate.side_effect = BrowserError("Execution context was destroyed after possible click")
        with self.assertRaises(UnknownOverlay):
            read_retry(dismiss_translation_survey)(page)
        self.assertEqual(page.evaluate.call_count, 1)
        page.wait_for_timeout.assert_not_called()


if __name__ == "__main__":
    unittest.main()
