import unittest
from playwright.sync_api import sync_playwright
from v2.handlers import handle_reading, handle_ungraded_lab


class HandlerNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def test_reading_ignores_hidden_duplicate_completion_control(self):
        self.page.set_content('''<button style="display:none">Mark as complete</button>
        <button onclick="document.body.dataset.done='yes'">Mark as complete</button>''')
        handle_reading(self.page, lambda: None)
        self.assertEqual(self.page.locator('body').get_attribute('data-done'), 'yes')

    def test_lab_can_return_from_same_window_launch(self):
        self.context.route('https://www.coursera.org/lab-engine', lambda r:r.fulfill(body='<body>Lab environment ready</body>'))
        self.context.route('https://www.coursera.org/learn/demo/ungradedLti/lab', lambda r:r.fulfill(body='''
        <a href="/lab-engine">Launch lab</a><button onclick="document.body.dataset.done='yes'">Mark as completed</button>'''))
        source = 'https://www.coursera.org/learn/demo/ungradedLti/lab'
        self.page.goto(source)
        handle_ungraded_lab(self.page, lambda: None)
        self.assertEqual(self.page.url, source)
        self.assertEqual(self.page.locator('body').get_attribute('data-done'), 'yes')

    def test_resume_can_reuse_exact_linked_lab_tab(self):
        self.context.route('https://www.coursera.org/lab-engine', lambda r:r.fulfill(body='<body>Lab ready</body>'))
        existing = self.context.new_page()
        existing.goto('https://www.coursera.org/lab-engine')
        self.page.set_content('''<a href="https://www.coursera.org/lab-engine" onclick="event.preventDefault()">Resume lab</a>
        <button onclick="document.body.dataset.done='yes'">Mark as complete</button>''')
        handle_ungraded_lab(self.page, lambda: None)
        self.assertEqual(len(self.context.pages), 2)
        self.assertEqual(self.page.locator('body').get_attribute('data-done'), 'yes')
