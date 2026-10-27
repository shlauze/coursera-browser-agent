"""Offline DOM regressions from the observed TUleL peer upload form.

The minimal fixture retains the observed Uppy marker and information text; it
is not a saved copy of Coursera. All network requests are intercepted.
"""
import json
from pathlib import Path
import tempfile
import unittest

from playwright.sync_api import sync_playwright

from v2.assessments import FileUploadAssignmentSkipped
from v2.handlers import HandlerError
from v2.peer import handle_peer
from v2.store import Store


BASE = 'https://www.coursera.org/learn/example-course/peer/example/strategic-analysis'
PROMPT = 'Write your own strategic analysis.'
TEXT = ' '.join(f'learnerword{i}' for i in range(30))
SOURCE = {'authorship': 'learner', 'title': 'My analysis', 'prompt': PROMPT, 'text': TEXT}
TITLE = '<label>Project Title<input id="title" type="text"></label>'
UPLOAD = '''<p>Загрузите сюда свой стратегический анализ.</p><h3>Upload File</h3>
<div>Information: The following file types can be graded by AI (up to 20 MB for audio and video):
.txt, .pdf, .docx. If you upload another file type, your assignment will be graded by your peers instead.</div>
<div class="rc-UppyFileUploader link-button primary c-upload-button"><button type="button"
onclick="record('chooser')">Add File</button></div>'''
ESSAY = f'<p id="prompt">{PROMPT}</p><div id="essay" data-slate-editor="true" contenteditable="true" aria-labelledby="prompt"></div>'
CONTROLS = '''<label><input type="checkbox" id="agreement-checkbox-base" onchange="record('authorship')">I confirm this is my own work.</label>
<button onclick="record('save');localStorage.setItem('title',document.getElementById('title').value);localStorage.setItem('essay',document.getElementById('essay')?.innerText || '')">Save draft</button>
<button onclick="record('submit')">Submit</button>'''
SCRIPT = '''<script>
function record(event) { const events=JSON.parse(localStorage.getItem('events') || '[]');events.push(event);localStorage.setItem('events',JSON.stringify(events)); }
document.getElementById('title').value=localStorage.getItem('title') || '';
const essay=document.getElementById('essay');if(essay)essay.innerText=localStorage.getItem('essay') || '';
document.addEventListener('input', () => record('input'));
</script>'''


class PeerFormGuardsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'state.sqlite3')
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.context.route('**/*', lambda route: route.abort())
        self.addCleanup(self.context.close)
        self.store.set_meta('skip_file_upload_assignments', True)

    def load(self, body, *, path='/submit'):
        html = '<html><body><main>' + TITLE + body + CONTROLS + '</main>' + SCRIPT + '</body></html>'
        self.page.route(BASE + '**', lambda route: route.fulfill(body=html, content_type='text/html'))
        self.page.goto(BASE + path)

    def run_handler(self):
        return handle_peer(self.page, lambda: None, store=self.store, action_id=7)

    def events(self):
        return self.page.evaluate("JSON.parse(localStorage.getItem('events') || '[]')")

    def assert_untouched(self):
        self.assertEqual(self.events(), [])
        self.assertFalse(self.page.locator('#agreement-checkbox-base').is_checked())
        self.assertEqual(self.page.locator('#title').input_value(), '')

    def test_observed_uppy_upload_skips_before_slate_wait_or_missing_source(self):
        self.load(UPLOAD)
        self.assertEqual(self.page.locator('input[type=file]').count(), 0)
        self.assertEqual(self.page.locator('[data-slate-editor]').count(), 0)
        with self.assertRaises(FileUploadAssignmentSkipped):
            self.run_handler()
        self.assert_untouched()
        self.assertEqual(self.store.get_meta('submission:7'), 'not_submitted')
        self.assertIsNone(self.store.get_meta('peer_state:' + BASE))

    def test_upload_skip_precedes_even_a_supplied_learner_essay(self):
        self.load(UPLOAD)
        self.store.set_meta('peer_source:' + BASE, SOURCE)
        with self.assertRaises(FileUploadAssignmentSkipped):
            self.run_handler()
        self.assert_untouched()

    def test_upload_without_enabled_skip_is_deferred_without_file_chooser(self):
        self.load(UPLOAD)
        self.store.set_meta('skip_file_upload_assignments', False)
        with self.assertRaisesRegex(HandlerError, 'dedicated adapter'):
            self.run_handler()
        self.assert_untouched()

    def test_incomplete_or_ambiguous_upload_contract_is_not_silently_excluded(self):
        variants = (
            UPLOAD.replace('The following file types can be graded by AI', 'Upload instructions changed'),
            UPLOAD.replace('If you upload another file type, your assignment will be graded by your peers instead.', ''),
            UPLOAD.replace('rc-UppyFileUploader', 'unknown-uploader'),
            UPLOAD + '<button>Add File</button>',
            UPLOAD.replace('type="button"', 'type="button" disabled'),
        )
        for body in variants:
            with self.subTest(body=body):
                self.page.unroute_all()
                self.load(body)
                with self.assertRaisesRegex(HandlerError, 'Learner-authored essay required'):
                    self.run_handler()
                self.assert_untouched()

    def test_missing_source_is_stable_block_even_without_slate_editor(self):
        self.load('<p>Observed form needs learner content.</p>')
        with self.assertRaisesRegex(HandlerError, 'Learner-authored essay required'):
            self.run_handler()
        self.assert_untouched()

    def test_generated_source_never_fills_or_confirms_personal_work(self):
        self.load(ESSAY)
        self.store.set_meta('peer_source:' + BASE, {**SOURCE, 'authorship': 'ai'})
        with self.assertRaisesRegex(HandlerError, 'Learner-authored essay required'):
            self.run_handler()
        self.assert_untouched()
        self.assertEqual(self.page.locator('#essay').inner_text(), '')

    def test_existing_remote_title_and_body_are_preserved(self):
        for field in ('title', 'essay'):
            with self.subTest(field=field):
                self.page.unroute_all()
                self.load(ESSAY)
                self.page.evaluate("localStorage.clear()")
                self.page.reload()
                self.store.set_meta('peer_source:' + BASE, SOURCE)
                self.page.evaluate("([field,value]) => {const e=document.getElementById(field);if(field==='title')e.value=value;else e.innerText=value;}",
                                   [field, 'A different remote draft'])
                with self.assertRaisesRegex(HandlerError, 'preserve it for reconciliation'):
                    self.run_handler()
                self.assertEqual(self.events(), [])
                observed = self.page.locator('#' + field)
                self.assertEqual(observed.input_value() if field == 'title' else observed.inner_text(), 'A different remote draft')

    def test_verified_learner_draft_stops_before_authorship_or_submit(self):
        self.load(ESSAY)
        self.store.set_meta('peer_source:' + BASE, SOURCE)
        with self.assertRaisesRegex(HandlerError, 'Submission requires personal authorship confirmation'):
            self.run_handler()
        self.assertEqual(self.page.locator('#title').input_value(), SOURCE['title'])
        self.assertEqual(self.page.locator('#essay').inner_text(), SOURCE['text'])
        self.assertEqual(self.store.get_meta('peer_state:' + BASE), {'status': 'draft_verified'})
        self.assertEqual(self.store.get_meta('submission:7'), 'not_submitted')
        self.assertEqual(self.events().count('save'), 1)
        self.assertNotIn('authorship', self.events())
        self.assertNotIn('submit', self.events())
        self.assertFalse(self.page.locator('#agreement-checkbox-base').is_checked())

    def test_uncertain_peer_state_is_not_reclassified_as_upload_skip(self):
        self.load(UPLOAD)
        state = {'status': 'submitting', 'action_id': 4}
        self.store.set_meta('peer_state:' + BASE, state)
        self.store.set_meta('submission:7', 'submitting')
        with self.assertRaisesRegex(HandlerError, 'already sent or uncertain'):
            self.run_handler()
        self.assertEqual(self.store.get_meta('peer_state:' + BASE), state)
        self.assertEqual(self.store.get_meta('submission:7'), 'submitting')
        self.assert_untouched()


if __name__ == '__main__':
    unittest.main()
