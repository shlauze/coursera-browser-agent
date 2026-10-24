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


_IS_OUTLINE_DRAWER_JS = r"""element => {
    const selector = '[role="dialog"][data-testid="drawer-toggle-drawer"]';
    const drawers = element.matches(selector) ? [element] :
        element.matches('.cds-Modal-container:not([role="dialog"]):not([role="alertdialog"])')
            ? [...element.querySelectorAll(selector)] : [];
    if (drawers.length !== 1) return false;
    const drawer = drawers[0];
    const root = document.querySelector('[data-v2-outline-root]');
    if (!root || !drawer.contains(root)) return false;
    const module = [...root.querySelectorAll('button[aria-expanded],[role="button"][aria-expanded]')]
        .some(e => /(?:Module|Week)\s*\d+/i.test(e.innerText || e.getAttribute('aria-label') || ''));
    if (!module || !root.querySelector('a[href*="/learn/"]')) return false;
    // Only the drawer and its plain modal shell are exempt. Any nested or
    // separate dialog is still inspected independently by the caller.
    const norm = s => (s || '').replace(/\s+/g, ' ').trim();
    return norm(element.innerText) === norm(drawer.innerText);
}"""


def require_clear_outline(page):
    dismiss_learning_overlay(page)
    for dialog in page.locator('[role="dialog"],[role="alertdialog"],dialog[open],.cds-Modal-container').element_handles():
        if dialog.is_visible() and dialog.inner_text().strip():
            if dialog.evaluate(_IS_OUTLINE_DRAWER_JS):
                continue
            raise UnknownOverlay("Unknown modal blocks course outline; learner review required")
