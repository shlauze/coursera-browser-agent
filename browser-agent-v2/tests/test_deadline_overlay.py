"""Offline checks for the observed Coursera deadline information modal."""

import unittest
from unittest.mock import Mock

from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright

from v2.assessments import _wait
from v2.outline import scan_outline
from v2.recovery import read_retry
from v2.ui import UnknownOverlay, dismiss_deadline_overlay, dismiss_learning_overlay
from tests.test_browser_discovery_outline import OUTLINE_HTML


TITLE = "This item is due in less than 5 minutes"
NOTICE = (
    "This item is due at Oct 26, 11:59 PM +05. "
    "You may submit after the deadline without a late penalty."
)
FORM = """<main><fieldset><legend>Which number is even?</legend>
<label><input type="radio" name="number" value="2">2</label>
<label><input type="radio" name="number" value="3">3</label></fieldset>
<button onclick="window.submits=(window.submits||0)+1">Submit</button></main>"""


def warning(*, title=TITLE, notice=NOTICE, extra="", button_extra="", zero_wrapper=False):
    # The live role=dialog wrapper has no box; its absolute content is visible.
    # Selecting only visible role elements would overlook this modal entirely.
    style = ' style="width:0;height:0;overflow:visible"' if zero_wrapper else ""
    return f"""<div class="rc-ItemDeadlineWarningModal">
<div class="rc-Modal box" role="dialog"{style}>
<div class="c-modal-overlay" role="presentation"></div>
<div class="c-modal-content" style="position:absolute;left:100px;top:100px;width:600px;background:white;z-index:1000">
<h2>{title}</h2><p><span>{notice}</span></p>{extra}
<div class="c-modal-footer"><span><span data-testid="tooltip-wrapper">
<button onclick="window.deadlineClicks=(window.deadlineClicks||0)+1;this.closest('.rc-ItemDeadlineWarningModal').remove()">Ok</button>
</span></span>{button_extra}</div></div></div></div>"""


class DeadlineOverlayTests(unittest.TestCase):
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
        self.page.route("https://www.coursera.org/**", lambda route: route.fulfill(body="<html><body></body></html>"))
        self.page.goto("https://www.coursera.org/learn/example-course/assignment-submission/exam")

    def tearDown(self):
        self.context.close()

    def test_exact_notice_is_dismissed_once(self):
        self.page.set_content(warning())
        self.assertTrue(dismiss_deadline_overlay(self.page))
        self.assertFalse(dismiss_deadline_overlay(self.page))
        self.assertEqual(self.page.evaluate("window.deadlineClicks"), 1)
        self.assertEqual(self.page.locator(".rc-ItemDeadlineWarningModal").count(), 0)

    def test_zero_size_role_wrapper_with_visible_absolute_content(self):
        self.page.set_content(warning(zero_wrapper=True))
        self.assertFalse(self.page.locator('[role="dialog"]').is_visible())
        self.assertTrue(self.page.locator(".c-modal-content").is_visible())
        self.assertTrue(dismiss_learning_overlay(self.page))
        self.assertEqual(self.page.evaluate("window.deadlineClicks"), 1)

    def test_modal_shell_and_content_are_one_confirmation(self):
        self.page.set_content(warning().replace('class="rc-Modal box"', 'class="rc-Modal box cds-Modal-container"'))
        self.assertTrue(dismiss_deadline_overlay(self.page))
        self.assertEqual(self.page.evaluate("window.deadlineClicks"), 1)

    def test_modal_wrapper_cannot_hide_additional_terms_or_controls(self):
        for extra in (
            '<p>I agree to new terms and payment.</p>',
            '<button>Accept</button>',
            '<div role="dialog">A separate confirmation<button>Ok</button></div>',
        ):
            with self.subTest(extra=extra):
                self.page.set_content(warning(zero_wrapper=True).replace(
                    '<div class="c-modal-content"', extra + '<div class="c-modal-content"'))
                self.page.evaluate("window.deadlineClicks=undefined")
                self.assertFalse(dismiss_deadline_overlay(self.page))
                self.assertIsNone(self.page.evaluate("window.deadlineClicks"))

    def test_changed_terms_and_unrecognized_title_are_not_clicked(self):
        cases = [
            {"notice": NOTICE.replace("without a late penalty", "with a late penalty")},
            {"extra": "<p>I agree to new terms and payment.</p>"},
            {"title": "Start new attempt?"},
            {"title": "This item is due in less than 10 minutes"},
            {"notice": "You may submit after the deadline without a late penalty."},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.page.set_content(warning(**case))
                self.assertFalse(dismiss_deadline_overlay(self.page))
                self.assertIsNone(self.page.evaluate("window.deadlineClicks"))
                self.assertEqual(self.page.locator(".rc-ItemDeadlineWarningModal").count(), 1)

    def test_duplicate_or_different_visible_buttons_are_not_clicked(self):
        for extra in ("<button>Ok</button>", "<button>Cancel</button>", "<button>I agree</button>"):
            with self.subTest(extra=extra):
                self.page.set_content(warning(button_extra=extra))
                self.assertFalse(dismiss_deadline_overlay(self.page))
                self.assertIsNone(self.page.evaluate("window.deadlineClicks"))

    def test_disabled_ok_is_not_clicked(self):
        self.page.set_content(warning())
        self.page.locator("button").evaluate("button => button.disabled=true")
        self.assertFalse(dismiss_deadline_overlay(self.page))
        self.assertIsNone(self.page.evaluate("window.deadlineClicks"))

    def test_another_dialog_prevents_dismissal(self):
        for extra in (
            '<div role="dialog"><p>Confirm authorship</p><button>Ok</button></div>',
            '<div role="alertdialog"><p>Accept additional terms</p></div>',
            warning(),
        ):
            with self.subTest(extra=extra[:80]):
                self.page.set_content(warning() + extra)
                self.assertFalse(dismiss_deadline_overlay(self.page))
                self.assertIsNone(self.page.evaluate("window.deadlineClicks"))

    def test_background_ok_is_never_targeted(self):
        background = '<button onclick="window.backgroundClicks=(window.backgroundClicks||0)+1">Ok</button>'
        self.page.set_content(background + warning())
        self.assertTrue(dismiss_deadline_overlay(self.page))
        self.assertEqual(self.page.evaluate("window.deadlineClicks"), 1)
        self.assertIsNone(self.page.evaluate("window.backgroundClicks"))
        self.assertFalse(dismiss_deadline_overlay(self.page))
        self.assertIsNone(self.page.evaluate("window.backgroundClicks"))

    def test_plain_page_with_same_words_and_ok_is_not_a_dialog(self):
        self.page.set_content(f'<main><h2>{TITLE}</h2><p>{NOTICE}</p><button onclick="window.backgroundClicks=1">Ok</button></main>')
        self.assertFalse(dismiss_deadline_overlay(self.page))
        self.assertIsNone(self.page.evaluate("window.backgroundClicks"))

    def test_assessment_wait_continues_without_answer_or_submission(self):
        self.page.set_content(FORM + warning(zero_wrapper=True))
        result = _wait(self.page, lambda: None, lambda value: bool(value.get("questions")), seconds=3)
        self.assertEqual(len(result["questions"]), 1)
        self.assertEqual(self.page.evaluate("window.deadlineClicks"), 1)
        self.assertEqual(self.page.locator("input:checked").count(), 0)
        self.assertIsNone(self.page.evaluate("window.submits"))

    def test_outline_scan_continues_and_preserves_completion_evidence(self):
        self.page.set_content(OUTLINE_HTML.replace("</body>", warning(zero_wrapper=True) + "</body>"))
        snapshot = scan_outline(self.page)
        self.assertEqual(len(snapshot.items), 3)
        self.assertEqual([item.completed for item in snapshot.items], [True, True, False])
        self.assertEqual(self.page.evaluate("window.deadlineClicks"), 1)

    def test_unknown_click_outcome_is_not_replayed_by_read_retry(self):
        page = Mock()
        page.evaluate.side_effect = PlaywrightTimeout("The click may have reached the page")
        with self.assertRaises(UnknownOverlay):
            read_retry(dismiss_deadline_overlay)(page)
        self.assertEqual(page.evaluate.call_count, 1)
        page.wait_for_timeout.assert_not_called()


if __name__ == "__main__":
    unittest.main()
