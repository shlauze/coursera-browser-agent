"""Dismiss only recognized, nonessential Coursera overlays."""
import re
from .recovery import read_retry


class UnknownOverlay(RuntimeError):
    pass


@read_retry
def dismiss_learning_overlay(page):
    dismissed = False
    for dialog in page.locator('[role="dialog"],[role="alertdialog"],dialog[open],.cds-Modal-container').element_handles():
        if not dialog.is_visible():
            continue
        text = " ".join(dialog.inner_text().split()).replace("’", "'")
        if not text:
            continue
        if "You've completed today's goals!" in text:
            close = [button for button in dialog.query_selector_all('button,[role="button"]')
                     if button.is_visible() and re.fullmatch(r"Close", button.get_attribute("aria-label") or button.inner_text().strip(), re.I)]
            if len(close) == 1:
                close[0].click(timeout=5000)
                dismissed = True
    return dismissed


def require_clear_outline(page):
    dismiss_learning_overlay(page)
    for dialog in page.locator('[role="dialog"],[role="alertdialog"],dialog[open],.cds-Modal-container').element_handles():
        if dialog.is_visible() and dialog.inner_text().strip():
            raise UnknownOverlay("Unknown modal blocks course outline; learner review required")
