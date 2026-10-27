"""Dismiss only recognized, nonessential Coursera overlays."""
import re
import logging
from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout


class UnknownOverlay(RuntimeError):
    pass


_TRANSLATION_SURVEY_JS = r"""({dismiss = false} = {}) => {
    const norm = text => (text || '').replace(/\s+/g, ' ').trim();
    const visible = e => {
        const s = getComputedStyle(e), r = e.getBoundingClientRect();
        for (let parent = e.parentElement; parent; parent = parent.parentElement) {
            if (Number(getComputedStyle(parent).opacity) === 0) return false;
        }
        return s.display !== 'none' && s.visibility !== 'hidden' &&
            Number(s.opacity) !== 0 && r.width > 0 && r.height > 0;
    };
    const roots = [...document.querySelectorAll('#pendo-base,._pendo-step-container')]
        .filter(e => visible(e) || [...e.querySelectorAll('button,[role="button"]')].some(visible));
    if (!roots.length) return {present: false, dismissed: false};
    const blocked = {present: true, blocked: true, dismissed: false};
    if (location.hostname !== 'www.coursera.org' || roots.length !== 1) return blocked;
    const root = roots[0];
    // These root and question markers were recorded in the pointer-interception
    // failure. The rest of the question may vary; never infer a survey response.
    if (!root.matches('#pendo-base._pendo-step-container._pendo-guide-walkthrough_[data-layout="lightboxBlank"]'))
        return blocked;
    const prompts = [...root.querySelectorAll('#pendo-text-1ecb6615._pendo-text-plain')].filter(visible);
    if (prompts.length !== 1 ||
        !/^How effective are the translations in this course(?:[\s?.,\u2026:]|$)/.test(norm(prompts[0].innerText)))
        return blocked;
    // A changed guide asking for consent or a file is not this optional survey.
    if (root.querySelector('input[type="checkbox"],input[type="file"],[role="checkbox"],[role="switch"],[contenteditable="true"]') ||
        /\b(?:I agree|I certify|I confirm|I consent|authorship|honou?r code|terms|payment|purchase)/i.test(norm(root.innerText)) ||
        [...root.querySelectorAll('[role="dialog"],[role="alertdialog"],dialog[open]')].some(visible))
        return blocked;
    const closeName = e => {
        const labelled = e.getAttribute('aria-labelledby');
        if (labelled) {
            const labels = labelled.trim().split(/\s+/).map(id => document.getElementById(id));
            return labels.every(Boolean) ? norm(labels.map(label => label.textContent).join(' ')) : '';
        }
        return norm(e.getAttribute('aria-label') || e.innerText || e.getAttribute('title'));
    };
    const closes = [...root.querySelectorAll('button,[role="button"]')].filter(e => visible(e) &&
        /^(?:Close(?: guide| survey)?|Dismiss)$/i.test(closeName(e)) &&
        /^(?:Close(?: guide| survey)?|Dismiss|[xX\u00d7\u2715\u2716])?$/i.test(norm(e.innerText)));
    if (closes.length !== 1) return blocked;
    const close = closes[0];
    if (close.disabled || close.matches(':disabled') || close.closest('[inert]') ||
        close.getAttribute('aria-disabled') === 'true' || close.hasAttribute('href') ||
        (close.closest('form') && close.tagName === 'BUTTON' && close.type !== 'button')) return blocked;
    const rect = close.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    if (!hit || !(hit === close || close.contains(hit))) return blocked;
    // Recognition, hit testing and dismissal share one DOM turn. No rating,
    // feedback, Submit, global Close or hidden application API is used.
    if (dismiss) close.click();
    return {present: true, recognized: true, dismissed: dismiss};
}"""


_NO_VISIBLE_PENDO_JS = r"""() => ![...document.querySelectorAll('#pendo-base,._pendo-step-container')]
    .some(root => [root, ...root.querySelectorAll('button,[role="button"]')].some(e => {
        const s = getComputedStyle(e), r = e.getBoundingClientRect();
        for (let parent = e.parentElement; parent; parent = parent.parentElement) {
            if (Number(getComputedStyle(parent).opacity) === 0) return false;
        }
        return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity) !== 0 &&
            r.width > 0 && r.height > 0;
    }))"""


def dismiss_translation_survey(page):
    """Dismiss only the identified optional survey; unknown Pendo guides block."""
    try:
        result = page.evaluate(_TRANSLATION_SURVEY_JS, {"dismiss": True})
        if result.get("blocked") is True:
            raise UnknownOverlay("Unrecognized or obscured Pendo survey; learner review required")
        if result.get("dismissed") is not True:
            return False
        page.wait_for_function(_NO_VISIBLE_PENDO_JS, timeout=2500)
    except BrowserError as exc:
        # A dismissal might already have happened. Never let read_retry replay it.
        raise UnknownOverlay("Translation survey dismissal could not be verified; inspect the current page") from exc
    logging.getLogger("browser_agent_v2.ui").info(
        "Dismissed recognized translation-quality survey without answering it"
    )
    return True


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
    dismissed = dismiss_translation_survey(page)
    if dismiss_deadline_overlay(page):
        return True
    try:
        return _dismiss_goal_overlay(page) or dismissed
    except BrowserError as exc:
        # Pendo can appear after the first check and intercept the goal's Close.
        # Only that demonstrated pre-click failure permits one fresh lookup.
        if isinstance(exc, BrowserTimeout) and "intercepts pointer events" in str(exc):
            if dismiss_translation_survey(page):
                try:
                    _dismiss_goal_overlay(page)
                    return True
                except BrowserError as retry_exc:
                    raise UnknownOverlay("Goal overlay dismissal could not be verified") from retry_exc
        raise UnknownOverlay("Goal overlay dismissal could not be verified") from exc


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
