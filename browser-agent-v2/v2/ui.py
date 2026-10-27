"""Dismiss only recognized, nonessential Coursera overlays."""
import re
import logging
from playwright.sync_api import Error as BrowserError
from .recovery import read_retry


class UnknownOverlay(RuntimeError):
    pass


_DEADLINE_DIALOG_JS = r"""({dismiss = false} = {}) => {
    const norm = text => (text || '').replace(/\s+/g, ' ').trim();
    const visible = e => {
        const style = getComputedStyle(e), rect = e.getBoundingClientRect();
        return style.visibility !== 'hidden' && style.display !== 'none' &&
            rect.width > 0 && rect.height > 0;
    };
    // Coursera's legacy role=dialog wrapper can have a zero-size rectangle,
    // while its absolutely positioned .c-modal-content is visible.
    const all = [...document.querySelectorAll(
        '[role="dialog"],[role="alertdialog"],dialog[open],.cds-Modal-container,.c-modal-content'
    )].filter(e => norm(e.innerText) && (visible(e) ||
        [...e.querySelectorAll('button,[role="button"],.c-modal-content')].some(visible)));
    const scopes = all.filter(e => !all.some(other => other !== e && e.contains(other)));
    if (scopes.length !== 1) return {recognized: false, dismissed: false};
    const scope = scopes[0];
    // A shell is redundant only when it adds no separate terms or controls.
    // Otherwise selecting its inner content would hide an unknown consent.
    if (all.some(parent => parent !== scope && parent.contains(scope) &&
        (norm(parent.innerText) !== norm(scope.innerText) ||
         [...parent.querySelectorAll('button,[role="button"],input,textarea,select,[contenteditable="true"]')]
             .some(e => visible(e) && !scope.contains(e)))))
        return {recognized: false, dismissed: false};
    const buttons = [...scope.querySelectorAll('button,[role="button"]')].filter(visible);
    const title = 'This item is due in less than 5 minutes';
    const when = String.raw`(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (?:[1-9]|[12]\d|3[01]), (?:[1-9]|1[0-2]):[0-5]\d (?:AM|PM) [+-](?:0\d|1[0-4])(?::[0-5]\d)?`;
    const body = new RegExp('^' + title + String.raw` This item is due at ` + when +
        String.raw`\. You may submit after the deadline without a late penalty\. Ok$`);
    if (!body.test(norm(scope.innerText)) || buttons.length !== 1 ||
        norm(buttons[0].innerText) !== 'Ok' || buttons[0].disabled ||
        buttons[0].getAttribute('aria-disabled') === 'true' ||
        scope.querySelector('input,textarea,select,[contenteditable="true"]'))
        return {recognized: false, dismissed: false};
    // Validate and acknowledge in the same DOM turn; never click a global OK.
    if (dismiss) buttons[0].click();
    return {recognized: true, dismissed: dismiss};
}"""


def dismiss_deadline_overlay(page):
    try:
        result = page.evaluate(_DEADLINE_DIALOG_JS, {"dismiss": True})
    except BrowserError as exc:
        # The click may already have happened. Do not let read_retry replay it.
        raise UnknownOverlay("Deadline warning dismissal could not be verified; inspect the current page") from exc
    if result.get("dismissed"):
        logging.getLogger("browser_agent_v2.ui").info(
            "Dismissed recognized Coursera deadline warning: no late penalty"
        )
        return True
    return False


def dismiss_learning_overlay(page):
    if dismiss_deadline_overlay(page):
        return True
    return _dismiss_goal_overlay(page)


@read_retry
def _dismiss_goal_overlay(page):
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
    for dialog in page.locator('[role="dialog"],[role="alertdialog"],dialog[open],.cds-Modal-container,.c-modal-content').element_handles():
        if dialog.is_visible() and dialog.inner_text().strip():
            if dialog.evaluate(_IS_OUTLINE_DRAWER_JS):
                continue
            raise UnknownOverlay("Unknown modal blocks course outline; learner review required")
