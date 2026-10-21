import unittest
from unittest.mock import Mock, patch
from playwright.sync_api import sync_playwright
from v2.plugins import handle_plugin
from v2.handlers import HandlerError
from v2.legacy_widgets.compat import active, Tab, open_frame
from v2.legacy_widgets import plugin_flow, scorm_flow, rise_scorm_flow, rise_best_practices

HTML = '''<html><body>
<aside style="position:absolute;left:0;width:300px;height:600px"><h2>Example</h2><button aria-expanded="true">Module 1</button>
<div><a id="item" href="/learn/example/ungradedPlugin/abc12/overview">Overview</a><small>Ungraded Plugin</small></div></aside>
<main style="margin-left:400px"><h1>Overview</h1><p>This lesson explains the workflow.</p>
<button onclick="document.getElementById('item').setAttribute('aria-label','Completed')">Mark as complete</button></main></body></html>'''


class PluginBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def run_page(self, html):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        page.route('https://www.coursera.org/**', lambda r: r.fulfill(body=html))
        page.goto('https://www.coursera.org/learn/example/ungradedPlugin/abc12/overview')
        return page

    def test_passive_plugin_completes_and_requires_green_confirmation(self):
        page = self.run_page(HTML)
        result = handle_plugin(page, lambda: None)
        self.assertIn('verified', result.message)
        self.assertEqual(page.locator('#item').get_attribute('aria-label'), 'Completed')

    def test_unknown_embedded_exercise_is_not_marked_complete(self):
        page = self.run_page(HTML.replace('<p>', '<input aria-label="Answer"><p>'))
        with self.assertRaisesRegex(HandlerError, 'embedded activity'):
            handle_plugin(page, lambda: None)
        self.assertIsNone(page.locator('#item').get_attribute('aria-label'))

    def test_completion_click_without_green_mark_is_not_success(self):
        page = self.run_page(HTML.replace("document.getElementById('item').setAttribute('aria-label','Completed')", 'void 0'))
        runner = Mock(tab=Tab(page), course={'url': 'https://www.coursera.org/learn/example'})
        runner.verified_item.return_value = False
        token = active.set((page, lambda: None))
        try:
            with self.assertRaisesRegex(HandlerError, 'green completion mark'):
                plugin_flow.complete_plugin(runner, {'key': 'ungradedPlugin:abc12', 'url': page.url, 'title': 'Overview'})
        finally:
            active.reset(token)

    def test_scorm_frame_is_scoped_to_owner_and_ambiguous_frame_rejected(self):
        owner = Mock()
        owner.page.main_frame = Mock(url='course')
        frame = Mock(url='https://example.org/scorm')
        owner.page.frames = [owner.page.main_frame, frame]
        result = open_frame(owner, frame.url, probes=1)
        self.assertIs(result.frame, frame)
        owner.page.frames.append(Mock(url=frame.url))
        with self.assertRaisesRegex(HandlerError, 'multiple matching'):
            open_frame(owner, frame.url, probes=1)


class LegacyRoutingTests(unittest.TestCase):
    def test_missing_mark_allows_reading_but_not_completion_claim(self):
        request = {'heading': 'Introduction'}
        snapshot = {'ready':'complete', 'mark_count':0, 'mark_disabled':False,
                    'modal':False, 'other':[], 'frame_title':'Topic 1: Introduction',
                    'frame':'https://d10o6em2qtnr4q.cloudfront.net/assets/'+'a'*32+'/index.html'}
        self.assertEqual(rise_scorm_flow._outer_checked(snapshot, request, allow_missing_mark=True), snapshot)
        with self.assertRaises(HandlerError):
            rise_scorm_flow._outer_checked(snapshot, request)

    def test_audited_scorm_routes_to_preserved_reader(self):
        path, spec = next(iter(scorm_flow.AUDITED_SCORM.items()))
        # Derive the heading from the actual audited registry, not a guessed title.
        heading = spec.get('heading') or spec.get('title')
        self.assertIsNotNone(heading)
        course = 'https://www.coursera.org' + path.split('/ungradedWidget/')[0]
        runner = Mock(course={'url': course})
        identity = path.split('/ungradedWidget/')[1].split('/')[0]
        with patch.object(scorm_flow, 'complete_static_scorm') as complete:
            plugin_flow.complete_plugin(runner, {'key':'ungradedWidget:'+identity, 'url':'https://www.coursera.org'+path, 'title':heading})
        complete.assert_called_once()

    def test_best_practices_and_generic_rise_routes_are_preserved(self):
        for path, heading, module, function in [
            (rise_best_practices.PATH, 'Best practices', rise_best_practices, 'complete_best_practices'),
            (plugin_flow.ADVERSARIAL_AI_COURSE_PATH+'/ungradedWidget/new01/lesson', 'New lesson', rise_scorm_flow, 'complete_rise_scorm')]:
            course = 'https://www.coursera.org' + path.split('/ungradedWidget/')[0]
            identity = path.split('/ungradedWidget/')[1].split('/')[0]
            with patch.object(module, function) as complete:
                plugin_flow.complete_plugin(Mock(course={'url':course}), {'key':'ungradedWidget:'+identity, 'url':'https://www.coursera.org'+path, 'title':heading})
            complete.assert_called_once()
