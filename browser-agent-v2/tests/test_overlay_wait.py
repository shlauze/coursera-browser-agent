import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from v2.control import Control
from v2.discovery import CourseCard
from v2.runner import Runner, RunnerAttention, StopRequested, course_id
from v2.store import Store
from v2.ui import UnknownOverlay

class OverlayWaitTests(unittest.TestCase):
    def runner(self):
        r=object.__new__(Runner)
        r.store=Mock(); r.control=Mock(); r.checkpoint=Mock()
        return r
    def test_disappearance_continues_without_global_resume(self):
        r=self.runner(); p=Mock(url="https://www.coursera.org/learn/example")
        with patch("v2.ui.require_clear_outline",side_effect=[UnknownOverlay("modal"),None]):
            r._wait_for_overlay(p)
        self.assertEqual(r.checkpoint.call_count,2)
        r.control.request_resume.assert_not_called()
        r.control.request_pause.assert_not_called()
        p.wait_for_timeout.assert_called_once_with(1000)
        r.store.set_meta.assert_called_with("confirmation_required",None)
    def test_stop_prevents_further_page_reads(self):
        r=self.runner();r.checkpoint.side_effect=StopRequested()
        with patch("v2.ui.require_clear_outline") as read:
            with self.assertRaises(StopRequested):r._wait_for_overlay(Mock())
        read.assert_not_called()
        r.store.set_meta.assert_called_with("confirmation_required",None)

    def clock_page(self):
        elapsed = [0.0]
        page = Mock(url="https://www.coursera.org/learn/example")
        page.wait_for_timeout.side_effect = lambda milliseconds: elapsed.__setitem__(
            0, elapsed[0] + milliseconds / 1000)
        return elapsed, page

    def test_persistent_unknown_overlay_has_bounded_wait(self):
        r = self.runner()
        elapsed, page = self.clock_page()
        with patch("v2.runner.time.monotonic", side_effect=lambda: elapsed[0]), \
             patch("v2.ui.require_clear_outline", side_effect=UnknownOverlay("Review required")):
            with self.assertRaises(RunnerAttention):
                r._wait_for_overlay(page, timeout_seconds=2.5)
        self.assertEqual(elapsed[0], 2.5)
        waits = [call.args[0] for call in page.wait_for_timeout.call_args_list]
        self.assertEqual(waits, [1000, 1000, 500])
        page.goto.assert_not_called()
        page.close.assert_not_called()
        page.evaluate.assert_not_called()
        r.control.request_pause.assert_not_called()
        r.store.set_meta.assert_called_with("confirmation_required", None)

    def test_stop_interrupts_an_existing_overlay_wait(self):
        r = self.runner()
        r.checkpoint.side_effect = [None, StopRequested()]
        elapsed, page = self.clock_page()
        with patch("v2.runner.time.monotonic", side_effect=lambda: elapsed[0]), \
             patch("v2.ui.require_clear_outline", side_effect=UnknownOverlay("Review required")) as read:
            with self.assertRaises(StopRequested):
                r._wait_for_overlay(page, timeout_seconds=2.5)
        read.assert_called_once_with(page)
        page.wait_for_timeout.assert_called_once_with(1000)
        r.store.set_meta.assert_called_with("confirmation_required", None)

    def test_persistent_overlay_defers_course_and_continues_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "run.sqlite3")
            runner = Runner(store, Control(store))
            blocked = CourseCard("Blocked Course", "Resume", 10, 1, 0)
            accessible = CourseCard("Accessible Course", "Resume", 20, 1, 1)
            for card in (blocked, accessible):
                store.upsert_course(course_id(card), card.title)
            elapsed, page = self.clock_page()
            visited = []

            def process(queue_page, card):
                visited.append(card.title)
                if card == blocked:
                    runner._wait_for_overlay(page, timeout_seconds=2.5)

            with patch("v2.runner.time.monotonic", side_effect=lambda: elapsed[0]), \
                 patch("v2.ui.require_clear_outline", side_effect=UnknownOverlay("Review required")), \
                 patch.object(runner, "_cards", return_value=[blocked, accessible]), \
                 patch.object(runner, "_reconcile"), \
                 patch.object(runner, "_process_course", side_effect=process):
                summary = runner.run(Mock(page=page))

            self.assertEqual(visited, [blocked.title, accessible.title])
            self.assertEqual(summary.deferred_courses, 1)
            self.assertEqual(summary.completed_courses, 0)
            self.assertEqual(summary.remaining_courses, 2)
            row = store.get_course(course_id(blocked))
            self.assertEqual(row["status"], "attention")
            self.assertIsNone(row["certificate_url"])
            self.assertTrue(store.get_meta("course_attention:" + course_id(blocked)))
            self.assertIsNone(store.get_meta("confirmation_required"))
            self.assertTrue(any(event["code"] == "COURSE_DEFERRED" for event in store.recent_events()))
            page.goto.assert_not_called()
            page.close.assert_not_called()
