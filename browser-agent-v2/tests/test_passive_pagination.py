"""Offline pagination regressions for enrolled cards without action buttons.

The browser renders real DOM fixtures. Only the polling clock is accelerated;
no existing browser, Coursera request, worker, or model provider is used.
"""

from contextlib import contextmanager
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from v2.browser import MY_LEARNING_URL
from v2.discovery import (
    DiscoveryError,
    _next_page,
    _read_page_cards,
    _read_passive_signature,
    discover_in_progress,
)


def actionable(slug="first-course", title="First Course"):
    return f'''<div class="rc-CourseCardModule">
      <a href="/learn/{slug}/home/welcome">{title}</a>
      <div>Course</div><div>12% complete</div>
      <a href="/learn/{slug}/lecture/intro">Resume</a>
    </div>'''


def passive(prefix="passive"):
    rows = []
    for index, status in enumerate(("Complete", "Not started", "Not started", "Not started")):
        rows.append(f'''<div class="rc-PathwayCardCourseItemModule">
          <a href="/learn/{prefix}-{index}/home/welcome">{prefix.title()} Course {index}</a>
          <div>Course {index + 1} of 4</div><span>{status}</span>
        </div>''')
    return '<div class="rc-PathwayCardModule"><h3>Example Pathway</h3>' + ''.join(rows) + '</div>'


def queue_html(pages, transition="renderPage(target);"):
    """Each fixture transition may schedule DOM changes on the polling clock."""
    return '''<!doctype html><html><body><span>In Progress</span>
      <main id="courses"></main>
      <div id="pagination"><span>Results per page: 8</span>
        <span id="numbers"></span><button id="next" onclick="goNext()">&gt;</button>
      </div><script>
      const fixturePages = ''' + json.dumps(pages) + ''';
      window.fixtureTime = 0;
      window.clickCount = 0;
      window.lastClickAt = 0;
      window.currentPage = 1;
      window.pending = [];
      function later(delay, callback) {
        pending.push({at: fixtureTime + delay, callback});
      }
      function advanceFixture(ms) {
        fixtureTime += ms;
        const due = pending.filter(item => item.at <= fixtureTime);
        pending = pending.filter(item => item.at > fixtureTime);
        due.sort((a, b) => a.at - b.at).forEach(item => item.callback());
      }
      function setNumber(number) {
        currentPage = number;
        document.getElementById('numbers').innerHTML = fixturePages.map((_, index) =>
          '<button' + (index + 1 === number ? ' aria-current="page"' : '') + '>' +
          (index + 1) + '</button>').join('');
        document.getElementById('next').disabled = number === fixturePages.length;
      }
      function renderPage(number) {
        document.getElementById('courses').innerHTML = fixturePages[number - 1];
        setNumber(number);
      }
      function goNext() {
        clickCount++;
        lastClickAt = fixtureTime;
        const target = currentPage + 1;
        ''' + transition + '''
      }
      renderPage(1);
      </script></body></html>'''


class PassivePaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.context.route("**/*", lambda route: route.abort())

    def tearDown(self):
        self.context.close()

    def load(self, pages, transition="renderPage(target);"):
        html = queue_html(pages, transition)
        self.page.route("https://www.coursera.org/my-learning*",
                        lambda route: route.fulfill(body=html, content_type="text/html"))
        self.page.goto(MY_LEARNING_URL)

    @contextmanager
    def polling_clock(self, *, minimum_step_ms=0):
        elapsed_ms = [0]

        def wait(milliseconds):
            step = max(milliseconds, minimum_step_ms)
            elapsed_ms[0] += step
            self.page.evaluate("ms => advanceFixture(ms)", step)

        clock = SimpleNamespace(monotonic=lambda: elapsed_ms[0] / 1000)
        with patch("v2.discovery.time", clock), patch.object(self.page, "wait_for_timeout", side_effect=wait):
            yield elapsed_ms

    def elapsed_after_click(self):
        return self.page.evaluate("fixtureTime - lastClickAt")

    def test_terminal_passive_page_finishes_full_scan_without_inventing_actions(self):
        self.load([actionable(), passive()])
        seen = []
        with self.polling_clock():
            cards = discover_in_progress(self.page, on_page=lambda number, count: seen.append((number, count)))
        self.assertEqual([card.title for card in cards], ["First Course"])
        self.assertEqual(seen, [(1, 1), (2, 0)])
        self.assertEqual(self.page.evaluate("clickCount"), 1)
        self.assertGreaterEqual(self.elapsed_after_click(), 1200)

    def test_intermediate_passive_page_keeps_scanning_to_later_actionable_course(self):
        self.load([actionable(), passive(), actionable("last-course", "Last Course")])
        seen = []
        with self.polling_clock():
            cards = discover_in_progress(self.page, on_page=lambda number, count: seen.append((number, count)))
        self.assertEqual([card.title for card in cards], ["First Course", "Last Course"])
        self.assertEqual([card.page_number for card in cards], [1, 3])
        self.assertEqual(seen, [(1, 1), (2, 0), (3, 1)])
        self.assertEqual(self.page.evaluate("clickCount"), 2)

    def test_stale_passive_cards_after_numeric_advance_are_rejected(self):
        self.load([passive("old"), passive("new")], "setNumber(target);")
        before = _read_passive_signature(self.page)
        self.assertTrue(before)
        with self.polling_clock(minimum_step_ms=15000):
            with self.assertRaises(DiscoveryError):
                _next_page(self.page, [])
        self.assertEqual(_read_passive_signature(self.page), before)
        self.assertEqual(self.page.evaluate("currentPage"), 2)
        self.assertEqual(self.page.evaluate("clickCount"), 1)

    def test_removed_action_on_old_rendered_cards_does_not_prove_new_page(self):
        old_content = passive("old").replace('</div></div>',
            '<a href="/learn/old-3/lecture/intro">Resume</a></div></div>')
        transition = '''setNumber(target);
          document.querySelector('a[href="/learn/old-3/lecture/intro"]').remove();'''
        self.load([old_content, passive("new")], transition)
        old_cards = _read_page_cards(self.page, 1)
        self.assertEqual(len(old_cards), 1)
        self.assertFalse(_read_passive_signature(self.page))
        with self.polling_clock(minimum_step_ms=15000):
            with self.assertRaises(DiscoveryError):
                _next_page(self.page, old_cards)
        self.assertTrue(_read_passive_signature(self.page))
        self.assertEqual(self.page.evaluate("clickCount"), 1)

    def test_passive_acceptance_requires_exact_numeric_advance(self):
        cases = {
            "unknown_previous": ("renderPage(target);", True),
            "unknown_current": ('''renderPage(target);
                document.querySelector('[aria-current]').removeAttribute('aria-current');''', False),
            "skipped_page": ("renderPage(target); setNumber(3);", False),
        }
        for label, (transition, clear_previous) in cases.items():
            with self.subTest(label=label):
                self.load([actionable(), passive(), passive("third")], transition)
                if clear_previous:
                    self.page.evaluate("document.querySelector('[aria-current]').removeAttribute('aria-current')")
                old_cards = _read_page_cards(self.page, 1)
                with self.polling_clock(minimum_step_ms=15000):
                    with self.assertRaises(DiscoveryError):
                        _next_page(self.page, old_cards)
                self.assertEqual(self.page.evaluate("clickCount"), 1)
                self.page.unroute_all()

    def test_blank_and_loading_pages_are_not_accepted_as_passive(self):
        cases = {
            "blank": "",
            "skeleton": '<div class="rc-PathwayCardCourseItemModule"><div role="progressbar">Loading</div></div>',
            "busy": '<section aria-busy="true">' + passive() + '</section>',
            "spinner_over_cards": passive() + '<div role="progressbar">Loading</div>',
        }
        for label, content in cases.items():
            with self.subTest(label=label):
                self.load([actionable(), content])
                old_cards = _read_page_cards(self.page, 1)
                with self.polling_clock(minimum_step_ms=15000):
                    with self.assertRaises(DiscoveryError):
                        _next_page(self.page, old_cards)
                self.assertFalse(_read_passive_signature(self.page))
                self.assertEqual(self.page.evaluate("clickCount"), 1)
                self.page.unroute_all()

    def test_passive_evidence_requires_known_status_and_course_link(self):
        self.load([passive()])
        self.assertTrue(_read_passive_signature(self.page))
        for change in (
            "document.querySelector('.rc-PathwayCardCourseItemModule span').textContent = 'Loading'",
            "document.querySelector('.rc-PathwayCardCourseItemModule a').href = 'https://example.com/learn/passive-0/home/welcome'",
            "document.querySelector('.rc-PathwayCardCourseItemModule a').remove()",
        ):
            with self.subTest(change=change):
                self.page.evaluate("renderPage(1)")
                self.page.evaluate(change)
                self.assertFalse(_read_passive_signature(self.page))

    def test_disabled_action_is_not_passive_evidence(self):
        self.load([passive()])
        self.page.evaluate("""() => {
          const button = document.createElement('button');
          button.disabled = true;
          button.textContent = 'Resume';
          document.querySelector('.rc-PathwayCardCourseItemModule').append(button);
        }""")
        self.assertEqual(_read_page_cards(self.page, 1), [])
        self.assertFalse(_read_passive_signature(self.page))

    def test_checkpoint_can_pause_before_click_and_stop_during_wait(self):
        self.load([actionable(), passive()])
        old_cards = _read_page_cards(self.page, 1)
        observations = []

        class StopRequested(Exception):
            pass

        with self.polling_clock():
            def checkpoint():
                observations.append(self.page.evaluate("clickCount"))
                if len(observations) == 1:
                    # Model a resumed pause: no click may precede this checkpoint.
                    self.assertEqual(observations[-1], 0)
                    self.page.wait_for_timeout(5000)
                if len(observations) == 3:
                    raise StopRequested("stopped while settling")

            with self.assertRaisesRegex(StopRequested, "stopped while settling"):
                _next_page(self.page, old_cards, checkpoint=checkpoint)
        self.assertEqual(observations, [0, 1, 1])
        self.assertEqual(self.page.evaluate("clickCount"), 1)

    def test_accepted_click_timeout_on_passive_transition_does_not_click_twice(self):
        self.load([actionable(), passive()])
        old_cards = _read_page_cards(self.page, 1)
        original_locator = self.page.locator

        class AcceptedClickTimeout:
            def click(_self, **kwargs):
                original_locator('[data-v2-pagination-next="true"]').click(**kwargs)
                raise PlaywrightTimeout("timeout after accepted click")

        def locator(selector, *args, **kwargs):
            if selector == '[data-v2-pagination-next="true"]':
                return AcceptedClickTimeout()
            return original_locator(selector, *args, **kwargs)

        with self.polling_clock(), patch.object(self.page, "locator", side_effect=locator):
            self.assertTrue(_next_page(self.page, old_cards))
        self.assertEqual(self.page.evaluate("clickCount"), 1)

    def test_delayed_action_controls_are_read_before_passive_acceptance(self):
        transition = '''renderPage(target);
          later(900, () => {
            const action = document.createElement('a');
            action.href = '/learn/passive-1/lecture/intro';
            action.textContent = 'Resume';
            document.querySelectorAll('.rc-PathwayCardCourseItemModule')[1].append(action);
          });'''
        self.load([actionable(), passive()], transition)
        old_cards = _read_page_cards(self.page, 1)
        with self.polling_clock():
            self.assertTrue(_next_page(self.page, old_cards))
        self.assertEqual(len(_read_page_cards(self.page, 2)), 1)
        self.assertGreaterEqual(self.elapsed_after_click(), 900)
        self.assertEqual(self.page.evaluate("clickCount"), 1)

    def test_transient_busy_state_resets_passive_stability_window(self):
        transition = '''renderPage(target);
          later(1000, () => document.getElementById('courses').setAttribute('aria-busy', 'true'));
          later(1400, () => document.getElementById('courses').removeAttribute('aria-busy'));'''
        self.load([actionable(), passive()], transition)
        old_cards = _read_page_cards(self.page, 1)
        with self.polling_clock():
            self.assertTrue(_next_page(self.page, old_cards))
        # The first clean sample after the interruption is at 1500 ms;
        # it must establish a new 1200 ms stability window.
        self.assertGreaterEqual(self.elapsed_after_click(), 2700)
        self.assertEqual(self.page.evaluate("clickCount"), 1)


if __name__ == "__main__":
    unittest.main()
