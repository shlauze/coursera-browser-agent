"""Regressions observed on live Coursera on 2026-10-19."""
import unittest
from unittest.mock import patch
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from v2.discovery import _next_page, _read_page_cards
from v2.outline import scan_outline
from test_browser_discovery_outline import OUTLINE_HTML, QUEUE_HTML


class NavigationRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def load(self, html):
        self.page.route('https://www.coursera.org/**', lambda r: r.fulfill(body=html))
        self.page.goto('https://www.coursera.org/learn/example-course/lecture/intro')

    def test_week_outline_opens_two_collapsed_modules_without_stale_markers(self):
        html = OUTLINE_HTML.replace('Module', 'Week').replace('</aside>', '''
        <button aria-expanded="false" onclick="this.setAttribute('aria-expanded','true');document.getElementById('three').style.display='block'">Week 3</button>
        <div id="three" style="display:none"><div><a href="/learn/example-course/ungradedWidget/test/overview">Widget Overview</a><small>Ungraded Plugin</small></div></div></aside>''')
        self.load(html)
        snapshot = scan_outline(self.page)
        self.assertEqual(len(snapshot.items), 4)
        self.assertEqual(snapshot.items[-1].module, 'Week 3')
        self.assertEqual(snapshot.items[-1].kind, 'Ungraded Plugin')
        self.assertEqual(snapshot.first_incomplete.title, 'Knowledge Check')

    def test_delayed_expand_course_outline_button(self):
        html = OUTLINE_HTML.replace('<aside ', '<aside id="outline" hidden ').replace('</body>', '''
        <script>setTimeout(()=>{let b=document.createElement('button');b.setAttribute('aria-label','Expand course outline');b.onclick=()=>{setTimeout(()=>document.getElementById('outline').hidden=false,500);b.remove()};document.body.append(b)},500)</script></body>''')
        self.load(html)
        self.assertEqual(len(scan_outline(self.page).items), 3)

    def test_completed_item_is_not_opened(self):
        from v2.outline import open_item, OutlineError
        self.load(OUTLINE_HTML)
        item=next(i for i in scan_outline(self.page).items if i.completed)
        before=self.page.url
        with self.assertRaisesRegex(OutlineError,'complete before opening'):
            open_item(self.page,item)
        self.assertEqual(self.page.url,before)

    def test_unknown_modal_is_not_clicked(self):
        from v2.ui import UnknownOverlay
        self.load(OUTLINE_HTML.replace('</body>', '<div role="dialog" style="position:fixed;inset:0;background:white;z-index:1000">Accept new terms?<button onclick="window.accepted=true">Continue</button></div></body>'))
        with self.assertRaises(UnknownOverlay): scan_outline(self.page)
        self.assertIsNone(self.page.evaluate('window.accepted'))

    def test_curly_goal_modal_without_dialog_role(self):
        self.load(OUTLINE_HTML.replace('</body>', '<div class="cds-Modal-container" style="position:fixed;inset:0;background:white;z-index:1000">You&#8217;ve completed today&#8217;s goals!<button aria-label="Close" onclick="this.parentElement.remove()">X</button></div></body>'))
        self.assertEqual(len(scan_outline(self.page).items),3)

    def test_goal_modal_is_dismissed_before_expansion(self):
        self.load(OUTLINE_HTML.replace('</body>', '''<div role="dialog" style="position:fixed;inset:0;background:white;z-index:1000">You've completed today's goals!<button aria-label="Close" onclick="this.parentElement.remove()">X</button></div></body>'''))
        self.assertEqual(len(scan_outline(self.page).items), 3)
        self.assertEqual(self.page.get_by_role('dialog').count(), 0)

    def test_click_timeout_after_accepted_pagination_does_not_click_again(self):
        self.load(QUEUE_HTML)
        cards = _read_page_cards(self.page, 1)
        original = self.page.locator
        calls = []
        class ClickAfterTimeout:
            def click(_self, **kwargs):
                calls.append(True)
                original('[data-v2-pagination-next="true"]').click()
                raise PlaywrightTimeout('timed out after click reached site')
        with patch.object(self.page, 'locator', side_effect=lambda selector: ClickAfterTimeout() if 'data-v2-pagination-next' in selector else original(selector)):
            self.assertTrue(_next_page(self.page, cards))
        self.assertEqual(len(calls), 1)

    def test_short_incomplete_row_does_not_borrow_completed_sibling_icon(self):
        html = OUTLINE_HTML.replace('<div class="row"><span aria-label="Completed"></span>', '<div class="row"><span aria-label="Completed"></span>').replace('</aside>', '<div><a href="/learn/example-course/lecture/plain">Plain item</a></div></aside>')
        self.load(html)
        item = next(i for i in scan_outline(self.page).items if i.title == 'Plain item')
        self.assertFalse(item.completed)
