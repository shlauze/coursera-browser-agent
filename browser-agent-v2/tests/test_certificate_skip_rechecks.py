"""Keep certificate evidence while avoiding retries for learner-skipped work."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from v2.control import Control
from v2.discovery import CourseCard
from v2.outline import ItemRecord, OutlineSnapshot
from v2.runner import Runner, RunnerAttention, course_id
from v2.store import Store


CERTIFICATE = "https://www.coursera.org/account/accomplishments/verify/ABC123"


class CertificateSkipRecheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / "state.sqlite3")
        self.control = Control(self.store)
        self.control.mark_running()
        self.runner = Runner(self.store, self.control)
        self.card = CourseCard("Exact Course", "Resume", 8, 1, 0)
        self.cid = course_id(self.card)
        self.store.upsert_course(self.cid, self.card.title, status="started")
        self.item = ItemRecord(
            "Required project", "Programming Assignment", False,
            "https://www.coursera.org/learn/exact-course/assignment-submission/project",
            "Module 1", 0,
        )

    def attempt(self, items, certificate_results=None):
        page = Mock(url="https://www.coursera.org/learn/exact-course/home/welcome")
        outline = OutlineSnapshot(self.card.title, tuple(items), page.url)
        error = None
        with patch("v2.runner.open_course", return_value=page) as opened, \
             patch("v2.runner.scan_outline", return_value=outline) as scanned, \
             patch("v2.runner.open_item") as open_item, \
             patch("v2.runner.handle") as handler, \
             patch("v2.runner._verified_certificate_url", return_value=None,
                   side_effect=certificate_results) as certificate:
            try:
                self.runner._process_course(Mock(), self.card)
            except RunnerAttention as exc:
                error = str(exc)
        opened.assert_called_once()
        scanned.assert_called_once_with(page)
        open_item.assert_not_called()
        handler.assert_not_called()
        self.assertEqual(self.store.pending_actions(), [])
        return page, certificate, error

    def assert_incomplete(self):
        course = self.store.get_course(self.cid)
        self.assertNotEqual(course["status"], "complete")
        self.assertIsNone(course["certificate_url"])
        self.assertEqual(self.runner.completed_items, 0)
        self.assertEqual(self.runner.completed_courses, 0)

    def test_required_programming_skip_checks_once_without_reload_or_completion(self):
        self.store.set_meta("user_skipped_item_kinds", ["Programming Assignment"])
        page, certificate, error = self.attempt([self.item])
        certificate.assert_called_once_with(page, self.card.title)
        page.reload.assert_not_called()
        page.wait_for_timeout.assert_not_called()
        self.assertIn("remain incomplete and learner-skipped", error)
        self.assertNotIn("are complete", error)
        self.assertFalse(self.item.completed)
        self.assert_incomplete()

    def test_observed_authorized_upload_skip_checks_once(self):
        item = replace(self.item, kind="Graded Assignment")
        self.store.set_meta("user_skipped_file_assignments", [{
            "course_id": self.cid, "url": item.url, "authorized": True,
            "evidence": "live Upload File form",
        }])
        page, certificate, error = self.attempt([item])
        certificate.assert_called_once_with(page, self.card.title)
        page.reload.assert_not_called()
        self.assertIn("remain incomplete and learner-skipped", error)
        self.assert_incomplete()

    def test_optional_practice_and_materials_keep_normal_certificate_retries(self):
        self.store.set_meta("required_assessments_first", True)
        for item in (
            replace(self.item, title="Optional: project"),
            replace(self.item, kind="Practice Programming Assignment"),
            replace(self.item, kind="Video"),
            replace(self.item, kind="Reading"),
        ):
            with self.subTest(kind=item.kind, title=item.title):
                self.store.set_meta("user_skipped_item_kinds", [item.kind])
                page, certificate, error = self.attempt([item])
                self.assertEqual(certificate.call_count, 3)
                self.assertEqual(page.reload.call_count, 2)
                self.assertIn("matching certificate was not found", error)
                self.assert_incomplete()

    def test_verified_optional_programming_skip_keeps_normal_retries(self):
        self.store.set_meta("user_skipped_item_kinds", [self.item.kind])
        self.store.set_meta("verified_optional:" + self.cid, [{
            "url": self.item.url, "title": self.item.title,
            "evidence": "Explicit optional label", "authorized_skip": True,
        }])
        page, certificate, _ = self.attempt([self.item])
        self.assertEqual(certificate.call_count, 3)
        self.assertEqual(page.reload.call_count, 2)
        self.assert_incomplete()

    def test_completed_outline_keeps_normal_retries(self):
        page, certificate, _ = self.attempt([replace(self.item, completed=True)])
        self.assertEqual(certificate.call_count, 3)
        self.assertEqual(page.reload.call_count, 2)
        self.assert_incomplete()

    def test_verified_certificate_still_completes_with_skipped_outline_item(self):
        self.store.set_meta("user_skipped_item_kinds", [self.item.kind])
        page, certificate, error = self.attempt([self.item], [CERTIFICATE])
        certificate.assert_called_once_with(page, self.card.title)
        page.reload.assert_not_called()
        self.assertIsNone(error)
        course = self.store.get_course(self.cid)
        self.assertEqual(course["status"], "complete")
        self.assertEqual(course["certificate_url"], CERTIFICATE)
        self.assertEqual(self.runner.completed_courses, 1)
        self.assertEqual(self.runner.completed_items, 0)
        self.assertFalse(self.item.completed)

    def test_manual_completion_on_fresh_scan_restores_certificate_retries(self):
        self.store.set_meta("user_skipped_item_kinds", [self.item.kind])
        _, certificate, _ = self.attempt([self.item])
        self.assertEqual(certificate.call_count, 1)
        self.assert_incomplete()

        self.runner = Runner(self.store, self.control)
        page, certificate, error = self.attempt(
            [replace(self.item, completed=True)], [None, None, CERTIFICATE],
        )
        self.assertEqual(certificate.call_count, 3)
        self.assertEqual(page.reload.call_count, 2)
        self.assertIsNone(error)
        self.assertEqual(self.store.get_course(self.cid)["status"], "complete")

    def test_practice_in_title_does_not_make_skipped_graded_work_optional(self):
        item = replace(self.item, title="Practice exam", kind="Graded Assignment")
        self.store.set_meta("user_skipped_item_kinds", [item.kind])
        page, certificate, error = self.attempt([item])
        certificate.assert_called_once_with(page, self.card.title)
        page.reload.assert_not_called()
        self.assertIn("remain incomplete and learner-skipped", error)
        self.assert_incomplete()


if __name__ == "__main__":
    unittest.main()
