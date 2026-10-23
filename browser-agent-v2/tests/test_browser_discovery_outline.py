"""Offline DOM tests. Network requests to Coursera are intercepted."""

from __future__ import annotations

import unittest

from playwright.sync_api import sync_playwright

from v2.browser import MY_LEARNING_URL
from v2.discovery import discover_in_progress, open_course
from v2.outline import open_item, scan_outline


QUEUE_HTML = """<!doctype html><html><body>
<span>In Progress</span>
<section id="one">
  <div class="rc-PathwayCardModule"><h3>Deep Learning</h3>
    <div class="rc-PathwayCardCourseItemModule"><div>Deep Learning with PyTorch</div>
      <div>Course 1 of 3</div><div>·</div><div>99% complete</div><div>Lab - Multimodal Challenge</div>
      <a href="/learn/deep-learning-with-pytorch/lecture/intro">Resume</a></div></div>
  <div class="rc-CourseCardModule"><div>University</div><div>Offered by Program</div><div>New Course</div>
    <div>Course</div><div>·</div><div>0% complete</div><div>Overview</div>
    <a href="/learn/new-course/lecture/overview">Get started</a></div>
</section>
<section id="two" style="display:none">
  <div class="rc-CourseCardModule"><div>University</div><div>Second Started Course</div>
    <div>Course</div><div>·</div><div>12% complete</div><div>Lecture</div>
    <a href="/learn/second-course/lecture/one">Resume</a></div>
</section>
<div id="pagination"><span>Results per page: 8</span>
  <button id="p1" aria-current="page">1</button><button id="p2">2</button>
  <button id="next" onclick="showTwo()">&gt;</button>
</div>
<script>function showTwo() {
  document.getElementById('one').style.display='none';
  setTimeout(() => {
    document.getElementById('two').style.display='block';
    document.getElementById('p1').removeAttribute('aria-current');
    document.getElementById('p2').setAttribute('aria-current','page');
    document.getElementById('next').disabled=true;
  }, 350);
}</script>
</body></html>"""


OUTLINE_HTML = """<!doctype html><html><body>
<aside style="position:absolute;left:0;width:300px;height:600px;overflow:auto">
  <h2>Example Course</h2>
  <div>Today's goal</div>
  <button aria-expanded="true">Module 1</button>
  <a href="/learn/example-course/home/module/1">Module 1 home</a>
  <div class="row"><svg style="fill:rgb(25,145,30)"><path d="M0 0"/></svg>
    <a href="/learn/example-course/lecture/intro">Introduction</a><small>Video · 2 min</small></div>
  <div class="row"><span aria-label="Completed"></span>
    <a href="/learn/example-course/supplement/read">A Reading</a><small>Reading · 5 min</small></div>
  <button aria-expanded="false" onclick="openModule(this)">Module 2</button>
  <div id="module2" style="display:none">
    <div class="row"><svg style="fill:rgb(210,215,220)"><circle cx="1" cy="1" r="1"/></svg>
      <a href="/learn/example-course/quiz/check">Knowledge Check</a><small>Quiz · 5 min</small></div>
  </div>
</aside>
<script>function openModule(btn) { btn.setAttribute('aria-expanded','true');
 document.getElementById('module2').style.display='block'; }</script>
</body></html>"""


class BrowserDomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self) -> None:
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

    def tearDown(self) -> None:
        self.context.close()

    def test_live_grade_suffixes_and_peer_review_types_are_not_unknown(self):
        rows = [
            ('graded', 'Graded AssignmentGrade: 100%', 'Graded Assignment'),
            ('pending', 'Peer-graded AssignmentResults pending', 'Peer-graded Assignment'),
            ('optional', 'Practice Peer-graded Assignment', 'Practice Peer-graded Assignment'),
            ('review', 'Review Your Peers', 'Review Your Peers'),
            ('programming-practice', 'Practice Programming Assignment', 'Practice Programming Assignment'),
            ('quiz-practice', 'Practice Quiz', 'Practice Quiz'),
            ('lab-practice', 'Practice Lab', 'Practice Lab'),
            ('mandatory-lab', 'Programming Assignment', 'Programming Assignment'),
        ]
        html = '<aside style="position:absolute;left:0;width:300px;height:600px"><h2>Course</h2><button aria-expanded="true">Module 1</button>'
        for key, label, _ in rows:
            html += f'<div><a href="/learn/example-course/peer/{key}/essay">{key}</a><small style="display:block">{label}</small></div>'
        html += '</aside>'
        self.page.route('https://www.coursera.org/**', lambda route: route.fulfill(body=html))
        self.page.goto('https://www.coursera.org/learn/example-course/home')
        snapshot = scan_outline(self.page)
        self.assertEqual([x.kind for x in snapshot.items], [x[2] for x in rows])
        self.assertFalse(any(x.completed for x in snapshot.items))

    def test_dismissing_nested_goal_dialog_does_not_retarget_stale_index(self):
        from v2.ui import dismiss_learning_overlay, require_clear_outline, UnknownOverlay
        self.page.set_content("<div role='dialog'><div class='cds-Modal-container'>You've completed today's goals!<button onclick=\"this.closest('[role=dialog]').remove()\">Close</button></div></div><div role='dialog'>Unrecognized confirmation</div>")
        self.assertTrue(dismiss_learning_overlay(self.page))
        with self.assertRaises(UnknownOverlay):
            require_clear_outline(self.page)
        self.assertIn('Unrecognized confirmation', self.page.inner_text('body'))

    def test_discovery_reads_every_page_and_prefers_resume(self) -> None:
        self.page.route("https://www.coursera.org/my-learning*", lambda route: route.fulfill(body=QUEUE_HTML))
        cards = discover_in_progress(self.page)
        self.assertEqual([c.title for c in cards], [
            "Deep Learning with PyTorch", "Second Started Course", "New Course"
        ])
        self.assertEqual([c.page_number for c in cards], [1, 2, 1])
        self.assertEqual(cards[0].group_title, "Deep Learning")
        self.assertEqual([c.course_slug for c in cards], [
            "deep-learning-with-pytorch", "second-course", "new-course"
        ])

    def test_open_course_refinds_selected_card(self) -> None:
        self.page.route("https://www.coursera.org/my-learning*", lambda route: route.fulfill(body=QUEUE_HTML))
        self.page.route("https://www.coursera.org/learn/deep-learning-with-pytorch/lecture/intro",
                        lambda route: route.fulfill(body="<html><body>Course opened</body></html>"))
        card = discover_in_progress(self.page)[0]
        opened = open_course(self.page, card)
        self.assertIs(opened, self.page)
        self.assertIn("/learn/deep-learning-with-pytorch/", opened.url)

    def test_open_course_on_later_queue_page(self) -> None:
        self.page.route("https://www.coursera.org/my-learning*", lambda route: route.fulfill(body=QUEUE_HTML))
        self.page.route("https://www.coursera.org/learn/second-course/lecture/one",
                        lambda route: route.fulfill(body="<html><body>Second course opened</body></html>"))
        card = discover_in_progress(self.page)[1]
        self.assertEqual(card.page_number, 2)
        opened = open_course(self.page, card)
        self.assertTrue(opened.url.endswith("/learn/second-course/lecture/one"))

    def test_outline_expands_modules_and_requires_completion_evidence(self) -> None:
        self.page.route("https://www.coursera.org/learn/example-course/**", lambda route: route.fulfill(body=OUTLINE_HTML))
        self.page.goto("https://www.coursera.org/learn/example-course/lecture/intro")
        snapshot = scan_outline(self.page)
        self.assertEqual(snapshot.course_title, "Example Course")
        self.assertEqual(len(snapshot.items), 3)
        self.assertEqual([item.completed for item in snapshot.items], [True, True, False])
        self.assertEqual(snapshot.first_incomplete.title, "Knowledge Check")
        self.assertEqual(snapshot.first_incomplete.kind, "Quiz")
        opened = open_item(self.page, snapshot.first_incomplete)
        self.assertIs(opened, self.page)
        self.assertTrue(opened.url.endswith("/quiz/check"))


if __name__ == "__main__":
    unittest.main()
