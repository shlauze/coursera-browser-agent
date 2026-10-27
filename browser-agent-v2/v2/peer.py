"""Text peer submissions with durable duplicate protection; no invented reviews."""
import re
from urllib.parse import urlsplit
from .handlers import HandlerError, _click_unique


def assignment_base(url):
    u = urlsplit(url)
    m = re.fullmatch(r'(/learn/[^/]+/peer/[^/]+/[^/]+)(?:/(?:submit|give-feedback))?/?', u.path)
    if u.hostname not in {'www.coursera.org', 'coursera.org'} or not m:
        raise HandlerError('Unsupported peer assignment route')
    return 'https://www.coursera.org' + m[1]


def _require_learner_source(source):
    if not isinstance(source, dict) or source.get('authorship') != 'learner':
        raise HandlerError('Learner-authored essay required for the personal authorship confirmation')
    if not isinstance(source.get('title'), str) or not source['title'].strip():
        raise HandlerError('Essay title is missing')
    if not isinstance(source.get('text'), str) or len(source['text'].split()) < 20:
        raise HandlerError('Essay text is missing or too short')
    return source


def require_source(source, prompt):
    source = _require_learner_source(source)
    if source.get('prompt') != prompt:
        raise HandlerError('Essay prompt does not match this assignment')
    return source


_PEER_UPLOAD_JS = r"""() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = e => {
    if (!e || !e.getClientRects().length) return false;
    for (let p = e; p; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (p.matches('[hidden],[inert],[aria-hidden="true"]') || s.display === 'none'
          || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
    }
    return true;
  };
  const scope = document.querySelector('main') || document.body;
  const text = norm(scope.innerText);
  const add = [...scope.querySelectorAll('button,[role="button"]')]
    .filter(e => visible(e) && norm(e.innerText || e.getAttribute('aria-label')) === 'Add File');
  // Observed TUleL contract: Coursera's Uppy chooser has no input[type=file]
  // until opened. Recognize the complete information block without opening it.
  return add.length === 1 && !add[0].disabled && add[0].getAttribute('aria-disabled') !== 'true'
    && !!add[0].closest('.rc-UppyFileUploader') && /\bUpload File\b/.test(text)
    && /The following file types can be graded by AI \(up to 20 MB for audio and video\):/.test(text)
    && /If you upload another file type, your assignment will be graded by your peers instead\./.test(text);
}"""


def _requires_peer_upload(page, base, title):
    # A label on an overview/review page is not a live submission form.
    if page.url.rstrip('/') != base + '/submit' or title.count() != 1 or not title.is_visible():
        return False
    return bool(page.evaluate(_PEER_UPLOAD_JS))


def guard_submission(store, base):
    state = store.get_meta('peer_state:' + base, {})
    if state.get('status') in {'submitting', 'submitted', 'pending', 'complete'}:
        raise HandlerError('Peer submission already sent or uncertain; inspect its remote result before any repeat')


def review_status(text):
    counts = re.findall(r'\b(\d+)\s+(?:reviews?\s+)?left to complete\b', text, re.I)
    if re.search(r'submit your (?:project|assignment) before (?:reviewing|you can review)', text, re.I):
        return {'state': 'submission_required', 'remaining': int(counts[0]) if len(counts) == 1 else None}
    if len(counts) == 1:
        return {'state': 'reviews_required', 'remaining': int(counts[0])}
    return {'state': 'unknown', 'remaining': None}


def handle_peer(page, checkpoint, *, store, action_id):
    base = assignment_base(page.url)
    checkpoint()
    if page.url.rstrip('/').endswith('/give-feedback'):
        store.set_meta(f'submission:{action_id}', 'not_submitted')
        text = page.locator('body').inner_text()
        state = review_status(text)
        store.set_meta('peer_reviews:' + base, state)
        if state['state'] == 'submission_required':
            raise HandlerError('Coursera requires the project submission before the required peer reviews can be opened')
        store.set_meta('peer_review_context:' + base, {
            'url': page.url, 'state': state,
            'rubric': store.get_meta('peer_rubric:' + base),
            'status': 'awaiting_identified_peer_work'})
        raise HandlerError('Peer review needs the actual peer work and its submission identity; criteria preserved, no scores invented')
    from .peer_rubrics import parse_requirements
    requirements=parse_requirements(page.locator('body').inner_text())
    if requirements['criteria']:
        store.set_meta('peer_rubric:' + base, requirements)
        store.record_event('INFO','PEER_RUBRIC_READ',f"Read {len(requirements['criteria'])} criteria; word limit {requirements['word_limit']}")
    guard_submission(store, base)
    store.set_meta(f"submission:{action_id}", "not_submitted")
    if not page.url.rstrip('/').endswith('/submit'):
        target = page.get_by_role('tab', name='My submission', exact=True).or_(
            page.get_by_role('button', name='My submission', exact=True)).or_(
            page.get_by_role('link', name='My submission', exact=True)).locator('visible=true')
        target.first.wait_for(state='visible', timeout=30000)
        if target.count() != 1:
            raise HandlerError('My submission control is not unique')
        target.click(timeout=10000)
    title = page.get_by_role('textbox', name='Project Title', exact=True)
    title.wait_for(state='visible', timeout=30000)
    if _requires_peer_upload(page, base, title):
        if store.get_meta('skip_file_upload_assignments', False):
            from .assessments import FileUploadAssignmentSkipped
            raise FileUploadAssignmentSkipped('Observed peer Upload File form')
        raise HandlerError('File upload or rich-text editor needs a dedicated adapter')
    # Missing learner work cannot be repaired by waiting for a particular editor.
    # Preserve the stable learner-input block before attempting to fill any form.
    source = _require_learner_source(store.get_meta('peer_source:' + base))
    editor = page.locator('[data-slate-editor="true"][contenteditable="true"]:visible')
    editor.first.wait_for(state='visible', timeout=30000)
    if title.count() != 1 or editor.count() != 1:
        raise HandlerError('Peer form is not a unique text essay form')
    prompt = editor.evaluate('(e)=>document.getElementById(e.getAttribute("aria-labelledby"))?.innerText || ""').strip()
    store.set_meta('peer_requirements:' + base, {'prompt': prompt, 'source_key': 'peer_source:' + base})
    source = require_source(source, prompt)
    requirements=store.get_meta('peer_rubric:' + base, {})
    if requirements.get('word_limit') and len(source['text'].split()) > requirements['word_limit']:
        raise HandlerError(f"Essay exceeds observed word limit of {requirements['word_limit']}")
    checkpoint()
    # Never overwrite a remote draft with unrelated or newer content.
    existing = editor.inner_text().replace('\ufeff', '').replace('Enter text here', '').strip()
    if existing and existing != source['text'].strip():
        raise HandlerError('Existing essay draft differs; preserve it for reconciliation')
    existing_title = title.input_value().strip()
    if existing_title and existing_title != source['title'].strip():
        raise HandlerError('Existing essay draft title differs; preserve it for reconciliation')
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
    # Learner-source provenance does not authorize a personal authorship claim.
    # Leave the checkbox and final submission to the learner, even if prechecked.
    raise HandlerError('Submission requires personal authorship confirmation')
