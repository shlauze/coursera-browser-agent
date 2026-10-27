"""Opt-in graded scope must never open or clear excluded coursework intents."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import sync_playwright
from v2.control import Control
from v2.discovery import CourseCard
from v2.outline import ItemRecord, OutlineSnapshot, scan_outline
from v2.policy import ItemAction, decide, is_graded_coursework
from v2.runner import Runner, RunnerAttention, course_id, item_id
from v2.store import Store


class GradedOnlyRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        self.store = Store(self.path)
        self.store.set_meta('graded_only', True)
        self.store.set_meta('user_skipped_item_kinds', ['Programming Assignment'])
        self.control = Control(self.store)
        self.control.mark_running()
        self.runner = Runner(self.store, self.control)
        self.card = CourseCard('Scope course', 'Resume', 40, 1, 0)
        self.cid = course_id(self.card)
        self.store.upsert_course(self.cid, self.card.title, status='started')
        self.graded = ItemRecord('Required exam', 'Graded Assignment', False,
            'https://www.coursera.org/learn/demo/quiz/graded', 'Module 1', 0)

    def item(self, kind, **kwargs):
        return replace(self.graded, title=kind, kind=kind,
                       url='https://www.coursera.org/learn/demo/quiz/' + kind.replace(' ', '-'), **kwargs)

    def test_mode_is_opt_in_and_persists_across_runner_instances(self):
        ambiguous = self.item('Quiz')
        self.store.set_meta('graded_only', False)
        self.assertEqual(self.runner._next_coursework(self.cid, [ambiguous]), ambiguous)
        self.store.set_meta('graded_only', True)
        store = Store(self.path)
        fresh = Runner(store, Control(store))
        self.assertIsNone(fresh._next_coursework(self.cid, [ambiguous]))
        self.assertEqual(fresh._next_coursework(self.cid, [ambiguous, self.graded]), self.graded)

    def test_type_proof_does_not_come_from_title_or_quiz_url(self):
        for kind in ['Quiz', 'Assignment', 'Lab', 'Review Your Peers', 'Unknown', 'Ungraded Quiz',
                     'Practice Peer-graded Assignment', 'Practice Graded Exam', 'Optional Graded Quiz',
                     'Non-graded Assignment', 'Not graded Assignment',
                     'Ungraded App Item', 'Practice Graded App Item']:
            with self.subTest(kind=kind):
                item = replace(self.item(kind), title='Graded exam')
                self.assertIsNone(self.runner._next_coursework(self.cid, [item]))
        for kind in ['Graded Assignment', 'Graded Quiz', 'Graded Assessment', 'Graded Exam',
                     'Graded Lab', 'Peer-graded Assignment', 'Graded App Item']:
            with self.subTest(kind=kind):
                item = self.item(kind)
                self.assertEqual(self.runner._next_coursework(self.cid, [item]), item)

    def test_user_skips_dominate_explicit_graded_label(self):
        for kind in ['Programming Assignment', 'Graded Programming Assignment', 'Practice Programming Assignment']:
            item = self.item(kind)
            self.assertTrue(self.runner._user_kind_excluded(item))
            self.assertIsNone(self.runner._next_coursework(self.cid, [item]))
        self.store.set_meta('user_skipped_file_assignments', [{
            'course_id': self.cid, 'url': self.graded.url, 'authorized': True}])
        self.assertIsNone(self.runner._next_coursework(self.cid, [self.graded]))
        optional = replace(self.graded, title='Optional: Graded exam')
        self.assertIsNone(self.runner._next_coursework(self.cid, [optional]))

    def test_graded_app_item_is_visible_but_requires_review(self):
        item = self.item('Graded App Item')
        self.assertEqual(self.runner._next_coursework(self.cid, [item]), item)
        self.assertEqual(decide(item.kind, item.title).action, ItemAction.REVIEW_REQUIRED)
        self.assertIsNone(self.runner._next_coursework(self.cid, [self.item('Ungraded App Item')]))

    def test_ungraded_review_hold_does_not_block_other_graded_work(self):
        item = self.item('Practice Assignment')
        self.runner._retain_item(self.cid, item, 'Learner-authored essay required', 'not_submitted')
        self.assertEqual(self.runner._next_coursework(self.cid, [item, self.graded]), self.graded)
        self.assertIsNotNone(self.store.get_meta('item_attention:' + item_id(item)))

    def test_excluded_only_course_does_not_open_items_or_claim_completion(self):
        items = tuple(self.item(kind) for kind in ['Quiz', 'Lab', 'Practice Assignment', 'Ungraded Quiz'])
        self.runner._retain_item(self.cid, items[-1], 'Learner-authored essay required')
        page = Mock(url=self.graded.url)
        with patch('v2.runner.open_course', return_value=page), \
             patch('v2.runner.scan_outline', return_value=OutlineSnapshot(self.card.title, items, page.url)), \
             patch('v2.runner.open_item') as opened, patch('v2.runner.handle') as handled, \
             patch('v2.runner._verified_certificate_url', return_value=None) as certificate:
            with self.assertRaisesRegex(RunnerAttention, 'No further eligible graded items'):
                self.runner._process_course(page, self.card)
        opened.assert_not_called()
        handled.assert_not_called()
        certificate.assert_called_once()
        self.assertEqual(self.store.pending_actions(), [])
        self.assertNotEqual(self.store.get_course(self.cid)['status'], 'complete')
        self.assertEqual(self.runner.completed_items, 0)

    def test_ungraded_pending_keeps_status_stage_even_if_outline_completed(self):
        for completed in [False, True]:
            for stage in ['not_submitted', 'submitting']:
                with self.subTest(completed=completed, stage=stage):
                    suffix = f'{completed}-{stage}'
                    item = replace(self.item('Quiz', completed=completed), title='Quiz ' + suffix,
                                   url=self.graded.url + suffix)
                    aid = self.store.begin_action(self.cid, item_id(item), 'assessment', {'url': item.url})
                    self.store.set_meta(f'submission:{aid}', stage)
                    before = self.store.get_action(aid)
                    page = Mock(url=item.url)
                    with patch('v2.runner.open_course', return_value=page), \
                         patch('v2.runner.scan_outline', return_value=OutlineSnapshot(self.card.title, (item,), page.url)):
                        self.runner._reconcile_course(page, [self.card], self.cid)
                    self.assertEqual(self.store.get_action(aid), before)
                    self.assertEqual(self.store.get_meta(f'submission:{aid}'), stage)
                    self.assertNotIn((self.cid, item.url), self.runner.blocked_items)

    def test_old_media_intent_is_retained_without_course_navigation(self):
        aid = self.store.begin_action(self.cid, 'old-media', 'video', {'url': 'https://www.coursera.org/learn/demo/lecture/a'})
        before = self.store.get_action(aid)
        with patch('v2.runner.open_course') as opened:
            self.runner._reconcile(Mock(), [self.card])
        opened.assert_not_called()
        self.assertEqual(self.store.get_action(aid), before)

    def test_missing_course_or_item_preserves_unclassified_intent(self):
        aid = self.store.begin_action(self.cid, 'missing', 'assessment', {'url': self.graded.url + '-missing'})
        before = self.store.get_action(aid)
        with patch('v2.runner._verified_certificate_url') as certificate:
            self.runner._reconcile_course(Mock(), [], self.cid)
        certificate.assert_not_called()
        page = Mock(url=self.graded.url)
        with patch('v2.runner.open_course', return_value=page), \
             patch('v2.runner.scan_outline', return_value=OutlineSnapshot(self.card.title, (self.graded,), page.url)):
            self.runner._reconcile_course(page, [self.card], self.cid)
        self.assertEqual(self.store.get_action(aid), before)
        self.assertEqual(self.runner._next_coursework(self.cid, [self.graded]), self.graded)

    def test_completed_graded_pending_reconciles_only_from_exact_live_item(self):
        aid = self.store.begin_action(self.cid, item_id(self.graded), 'assessment', {'url': self.graded.url})
        self.store.set_meta(f'submission:{aid}', 'submitting')
        page = Mock(url=self.graded.url)
        with patch('v2.runner.open_course', return_value=page), \
             patch('v2.runner.scan_outline', return_value=OutlineSnapshot(self.card.title,
                 (replace(self.graded, completed=True),), page.url)):
            self.runner._reconcile_course(page, [self.card], self.cid)
        action = self.store.get_action(aid)
        self.assertEqual(action['outcome'], 'confirmed')
        self.assertEqual(action['evidence']['url'], self.graded.url)

    def test_uncertain_graded_intent_remains_blocked_without_replay(self):
        aid = self.store.begin_action(self.cid, item_id(self.graded), 'assessment', {'url': self.graded.url})
        self.store.set_meta(f'submission:{aid}', 'submitting')
        before = self.store.get_action(aid)
        page = Mock(url=self.graded.url)
        with patch('v2.runner.open_course', return_value=page), \
             patch('v2.runner.scan_outline', return_value=OutlineSnapshot(self.card.title, (self.graded,), page.url)):
            self.runner._reconcile_course(page, [self.card], self.cid)
        self.assertEqual(self.store.get_action(aid), before)
        self.assertIsNone(self.runner._next_coursework(self.cid, [self.graded]))


class GradedTypeDomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_real_outline_parser_preserves_grade_types_and_negative_qualifiers(self):
        labels = ['Graded Quiz', 'Graded Lab', 'Graded Assessment', 'Graded Exam', 'Graded App Item',
                  'Graded AssignmentGrade: 100%', 'Peer-graded AssignmentResults pending',
                  'Practice Peer-graded Assignment', 'Practice Graded Exam', 'Practice Graded Quiz',
                  'Optional: Graded Assignment', 'Ungraded Quiz', 'Non-graded Assignment',
                  'Not graded Assignment', 'Quiz', 'Lab', 'Ungraded App Item']
        expected = [s.replace('Grade: 100%', '').replace('Results pending', '') for s in labels]
        html = '<aside style="position:absolute;left:0;width:300px;height:600px"><h2>Scope Course</h2><button aria-expanded="true">Module 1</button>'
        for index, label in enumerate(labels):
            html += f'<div><a href="/learn/demo/quiz/i{index}">Item {index}</a><small style="display:block">{label}</small></div>'
        html += '<div><a href="/learn/demo/quiz/title-only">Graded Quiz</a><small style="display:block">10 minutes</small></div></aside>'
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        page.route('https://www.coursera.org/**', lambda route: route.fulfill(body=html))
        page.goto('https://www.coursera.org/learn/demo/home')
        outline = scan_outline(page)
        self.assertEqual([x.kind for x in outline.items], expected + ['Quiz'])
        self.assertEqual([is_graded_coursework(x.kind) for x in outline.items],
                         [True] * 7 + [False] * 11)

    def test_graded_lti_route_needs_visible_graded_app_label(self):
        html = ('<aside style="position:absolute;left:0;width:300px;height:600px">'
                '<h2>Scope Course</h2><button aria-expanded="true">Module 1</button>'
                '<div><a href="/learn/demo/gradedLti/yes/final-project">Final Project'
                '<span style="display:block">Graded App Item</span></a></div>'
                '<div><a href="/learn/demo/gradedLti/no/other-project">Other Project</a></div>'
                '</aside>')
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        page.route('https://www.coursera.org/**', lambda route: route.fulfill(body=html))
        page.goto('https://www.coursera.org/learn/demo/home')
        items = scan_outline(page).items
        self.assertEqual([item.kind for item in items], ['Graded App Item', 'Unknown'])
        self.assertEqual([is_graded_coursework(item.kind) for item in items], [True, False])


if __name__ == '__main__':
    unittest.main()
