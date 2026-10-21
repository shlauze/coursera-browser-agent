"""Text peer submissions with durable duplicate protection; no invented reviews."""
import re
import time
from urllib.parse import urlsplit
from .handlers import HandlerError, HandlerResult, _click_unique


def assignment_base(url):
    u = urlsplit(url)
    m = re.fullmatch(r'(/learn/[^/]+/peer/[^/]+/[^/]+)(?:/(?:submit|give-feedback))?/?', u.path)
    if u.hostname not in {'www.coursera.org', 'coursera.org'} or not m:
        raise HandlerError('Unsupported peer assignment route')
    return 'https://www.coursera.org' + m[1]


def require_source(source, prompt):
    if not isinstance(source, dict) or source.get('authorship') != 'learner':
        raise HandlerError('Learner-authored essay required for the personal authorship confirmation')
    if source.get('prompt') != prompt:
        raise HandlerError('Essay prompt does not match this assignment')
    if not isinstance(source.get('title'), str) or not source['title'].strip():
        raise HandlerError('Essay title is missing')
    if not isinstance(source.get('text'), str) or len(source['text'].split()) < 20:
        raise HandlerError('Essay text is missing or too short')
    return source


def guard_submission(store, base):
    state = store.get_meta('peer_state:' + base, {})
    if state.get('status') in {'submitting', 'submitted', 'pending', 'complete'}:
        raise HandlerError('Peer submission already sent or uncertain; inspect its remote result before any repeat')


def review_status(text):
    counts = re.findall(r'\b(\d+) left to complete\b', text)
    if 'Submit your project before reviewing classmates.' in text:
        return {'state': 'submission_required', 'remaining': int(counts[0]) if len(counts) == 1 else None}
    if len(counts) == 1:
        return {'state': 'reviews_required', 'remaining': int(counts[0])}
    return {'state': 'unknown', 'remaining': None}


def handle_peer(page, checkpoint, *, store, action_id):
    base = assignment_base(page.url)
    checkpoint()
    if page.url.rstrip('/').endswith('/give-feedback'):
        text = page.locator('body').inner_text()
        state = review_status(text)
        store.set_meta('peer_reviews:' + base, state)
        if state['state'] == 'submission_required':
            raise HandlerError('Coursera requires the project submission before the required peer reviews can be opened')
        raise HandlerError('Peer review rubric and submission identity must be verified before scoring or sending a review')
    guard_submission(store, base)
    if not page.url.rstrip('/').endswith('/submit'):
        _click_unique(page, 'My submission')
    title = page.get_by_role('textbox', name='Project Title', exact=True)
    editor = page.locator('[data-slate-editor="true"][contenteditable="true"]:visible')
    title.wait_for(state='visible', timeout=30000)
    editor.first.wait_for(state='visible', timeout=30000)
    if title.count() != 1 or editor.count() != 1:
        raise HandlerError('Peer form is not a unique text essay form')
    prompt = editor.evaluate('(e)=>document.getElementById(e.getAttribute("aria-labelledby"))?.innerText || ""').strip()
    store.set_meta('peer_requirements:' + base, {'prompt': prompt, 'source_key': 'peer_source:' + base})
    source = require_source(store.get_meta('peer_source:' + base), prompt)
    checkpoint()
    # Never overwrite a remote draft with unrelated or newer content.
    existing = editor.inner_text().replace('\ufeff', '').replace('Enter text here', '').strip()
    if existing and existing != source['text'].strip():
        raise HandlerError('Existing essay draft differs; preserve it for reconciliation')
    title.fill(source['title'])
    editor.fill(source['text'])
    if editor.inner_text().strip() != source['text'].strip():
        raise HandlerError('Rich text editor did not retain the exact essay')
    checkpoint()
    _click_unique(page, 'Save draft')
    page.reload(wait_until='domcontentloaded', timeout=30000)
    title.wait_for(state='visible', timeout=30000)
    if title.input_value() != source['title'] or editor.inner_text().strip() != source['text'].strip():
        raise HandlerError('Saved essay draft was not verified after reload')
    store.set_meta('peer_state:' + base, {'status': 'draft_verified'})
    box = page.locator('input#agreement-checkbox-base')
    if box.count() != 1:
        raise HandlerError('Unknown peer confirmation; review required')
    checkpoint()
    box.check()
    submit = page.get_by_role('button', name='Submit', exact=True)
    if submit.count() != 1 or not submit.is_enabled():
        raise HandlerError('Peer Submit remains unavailable')
    # Persist uncertainty before the click. A timeout never permits resending.
    store.set_meta('peer_state:' + base, {'status': 'submitting', 'action_id': action_id})
    store.set_meta('submission:' + str(action_id), 'submitting')
    submit.click(timeout=10000)
    store.record_event('INFO', 'PEER_SUBMIT_CLICKED', 'Peer essay sent once; waiting for remote confirmation')
    # Keep durable intent pending until the exact remote receipt/grade is inspected.
    raise HandlerError('Peer submission sent once; remote receipt and review workflow require reconciliation')
