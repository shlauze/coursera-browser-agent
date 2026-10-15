"""Offline regressions for following the Coursera In Progress queue."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import course_queue as queue
import coursera_start as engine
from course_state import QueueStore, load_courses

CERTIFICATE_ROOT = "https://www.coursera.org/account/accomplishments/verify/"


def certificate_url(identity):
    return CERTIFICATE_ROOT + "TEST-" + identity.upper()


async def opened_certificate(tab, url=None):
    return url


async def matching_certificate(tab, url, identity, title=None):
    return {"verified": True, "method": "certificate", "course_id": identity,
            "url": url, "evidence": [identity, "Completed by Learner"]}


class AutoQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.open_scanned = AsyncMock(side_effect=lambda _tab, selected: selected)
        self.open_scanned_patch = patch.object(queue, "open_scanned_course", self.open_scanned)
        self.open_scanned_patch.start()

    def tearDown(self):
        self.open_scanned_patch.stop()
        engine.configure_course("https://www.coursera.org/learn/philosophy")

    async def test_quiz_inspection_accepts_null_dialog_when_no_certificate_is_visible(self):
        runner = object.__new__(queue.QueueRunner)
        runner.course = {"id":"first-course", "title":"First Course"}
        runner.checkpoint = {"attempts":{}}
        await runner.after_quiz_action(
            "assignment:ABC123", "inspect", {},
            {"url":"https://www.coursera.org/learn/first-course/assignment-submission/ABC123/quiz",
             "dialog":None})

    async def test_transient_my_learning_load_retries_without_extra_course_click(self):
        store = SimpleNamespace(event=lambda *args: None)
        discovered = {"id": "next-course", "url": "https://www.coursera.org/learn/next-course"}
        with patch.object(queue, "scan_in_progress", AsyncMock(side_effect=[
                 TimeoutError("Chrome read timed out"), discovered])) as scan, \
             patch.object(queue.asyncio, "sleep", AsyncMock()) as wait:
            result = await queue._scan_with_retry(store, SimpleNamespace(), {"finished"})
        self.assertEqual(result, discovered)
        self.assertEqual(scan.await_count, 2)
        wait.assert_awaited_once_with(1)

    async def test_my_learning_discovery_reconnects_after_closed_websocket(self):
        store = SimpleNamespace(event=Mock())
        tab = SimpleNamespace(connect=AsyncMock())
        discovered = {"id":"next-course", "url":"https://www.coursera.org/learn/next-course"}
        with patch.object(queue, "scan_in_progress", AsyncMock(side_effect=[
                 RuntimeError("no close frame received or sent"), discovered])) as scan, \
             patch.object(queue.asyncio, "sleep", AsyncMock()):
            result = await queue._scan_with_retry(store, tab, set())
        self.assertEqual(result, discovered)
        self.assertEqual(scan.await_count, 2)
        tab.connect.assert_awaited_once()

    async def test_my_learning_empty_hydration_reloads_before_stopping(self):
        store = SimpleNamespace(event=Mock())
        tab = SimpleNamespace()
        page = {"page":1, "course_rows":[{"id":"next-course"}]}
        with patch.object(queue, "open_my_learning", AsyncMock(side_effect=[
                 RuntimeError("My Learning In Progress did not show a stable course list and pagination."),
                 page])) as opened, \
             patch.object(queue.asyncio, "sleep", AsyncMock()) as wait:
            result = await queue._open_my_learning_with_retry(store, tab, "first-course")
        self.assertEqual(result, page)
        self.assertEqual(opened.await_count, 2)
        wait.assert_awaited_once_with(1)
        store.event.assert_called_once()

    async def test_my_learning_reconnects_after_closed_websocket(self):
        store = SimpleNamespace(event=Mock())
        tab = SimpleNamespace(connect=AsyncMock())
        page = {"page":1, "course_rows":[]}
        with patch.object(queue, "open_my_learning", AsyncMock(side_effect=[
                 RuntimeError("no close frame received or sent"), page])) as opened, \
             patch.object(queue.asyncio, "sleep", AsyncMock()) as wait:
            result = await queue._open_my_learning_with_retry(store, tab, "first-course")
        self.assertEqual(result, page)
        self.assertEqual(opened.await_count, 2)
        tab.connect.assert_awaited_once()
        wait.assert_awaited_once_with(1)

    async def test_saved_partial_course_is_confirmed_by_my_learning_before_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/new-course\n"
                                   "https://www.coursera.org/learn/partly-done\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("partly-done", current_module=2, items_completed=1)
            args = SimpleNamespace(courses=course_file)
            observed = {"id":"partly-done", "url":"https://www.coursera.org/learn/partly-done",
                        "title":"Partly Done", "action":"resume"}
            with patch.object(queue, "_scan_with_retry", AsyncMock(return_value=observed)) as discover:
                selected = await queue._next_course_id(store, args, SimpleNamespace(), set(), True)
            self.assertEqual(selected, "partly-done")
            discover.assert_awaited_once_with(store, unittest.mock.ANY, set())

    async def test_site_resume_opens_before_any_saved_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/finished-course\n"
                                   "https://www.coursera.org/learn/active-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("finished-course", previous_status="completed", modules_discovered=3,
                         certificate_page_url=certificate_url("finished-course"))
            store.update("active-course", previous_status="pending", items_completed=1)
            observed = {"id":"active-course", "url":"https://www.coursera.org/learn/active-course",
                        "title":"Active Course", "action":"resume"}
            order = []

            async def scanned(*_args):
                order.append("scan")
                return observed

            async def opened(*_args):
                order.append("open")
                return observed

            with patch.object(queue, "open_my_learning", AsyncMock()) as learning, \
                 patch.object(queue, "_scan_with_retry", new=scanned), \
                 patch.object(queue, "open_scanned_course", new=opened):
                selected = await queue._next_course_id(
                    store, SimpleNamespace(courses=course_file), SimpleNamespace(), set(), True)
            self.assertEqual(selected, "active-course")
            self.assertEqual(order, ["scan", "open"])
            learning.assert_not_awaited()

    async def test_changed_my_learning_selection_rescans_and_opens_latest_course(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text(
                "https://www.coursera.org/learn/old-course\n"
                "https://www.coursera.org/learn/current-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            old = {"id": "old-course", "url": "https://www.coursera.org/learn/old-course",
                   "title": "Old Course", "action": "resume"}
            current = {"id": "current-course", "url": "https://www.coursera.org/learn/current-course",
                       "title": "Current Course", "action": "resume"}
            self.open_scanned.side_effect = [
                queue.MyLearningSelectionChanged("The selected My Learning card changed."),
                current,
            ]
            with patch.object(queue, "_scan_with_retry", AsyncMock(side_effect=[old, current])) as scan, \
                 patch.object(queue.asyncio, "sleep", AsyncMock()):
                selected = await queue._next_course_id(
                    store, SimpleNamespace(courses=course_file), SimpleNamespace(), set(), True)
            self.assertEqual(selected, "current-course")
            self.assertEqual(scan.await_count, 2)
            self.assertEqual([call.args[1]["id"] for call in self.open_scanned.await_args_list],
                             ["old-course", "current-course"])

    async def test_repeated_my_learning_selection_changes_stop_after_bounded_rescans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/active-course\n",
                                   encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            selected = {"id": "active-course", "url": "https://www.coursera.org/learn/active-course",
                        "title": "Active Course", "action": "resume"}
            self.open_scanned.side_effect = queue.MyLearningSelectionChanged(
                "The selected My Learning card changed.")
            with patch.object(queue, "_scan_with_retry", AsyncMock(return_value=selected)) as scan, \
                 patch.object(queue.asyncio, "sleep", AsyncMock()):
                with self.assertRaisesRegex(RuntimeError, "My Learning.*changed"):
                    await queue._next_course_id(
                        store, SimpleNamespace(courses=course_file), SimpleNamespace(), set(), True)
            self.assertEqual(scan.await_count, 3)
            self.assertEqual(self.open_scanned.await_count, 3)

    async def test_other_course_open_failure_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/active-course\n",
                                   encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            selected = {"id": "active-course", "url": "https://www.coursera.org/learn/active-course",
                        "title": "Active Course", "action": "resume"}
            self.open_scanned.side_effect = RuntimeError("Course opening result is uncertain")
            with patch.object(queue, "_scan_with_retry", AsyncMock(return_value=selected)) as scan:
                with self.assertRaisesRegex(RuntimeError, "result is uncertain"):
                    await queue._next_course_id(
                        store, SimpleNamespace(courses=course_file), SimpleNamespace(), set(), True)
            scan.assert_awaited_once()
            self.open_scanned.assert_awaited_once()

    async def test_empty_in_progress_does_not_browse_saved_certificates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/finished-course\n",
                                   encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("finished-course", previous_status="attention",
                         certificate_page_url=certificate_url("finished-course"))
            with patch.object(queue, "_scan_with_retry", AsyncMock(return_value=None)) as scan:
                selected = await queue._next_course_id(
                    store, SimpleNamespace(courses=course_file), SimpleNamespace(), set(), True)
            self.assertIsNone(selected)
            scan.assert_awaited_once()
            self.open_scanned.assert_not_awaited()

    async def test_resume_course_reaches_outline_before_its_saved_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/active-course\n",
                                   encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("active-course", previous_status="attention", current_module=2,
                         certificate_page_url=certificate_url("active-course"))
            args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                                   continue_on_attention=False, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            observed = {"id":"active-course", "url":"https://www.coursera.org/learn/active-course",
                        "title":"Active Course", "action":"resume"}
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "scan_in_progress", AsyncMock(return_value=observed)), \
                 patch.object(queue, "open_certificate_page", AsyncMock()) as certificate, \
                 patch.object(queue, "verify_saved_certificate", AsyncMock()) as verified, \
                 patch.object(queue, "discover_modules", AsyncMock(
                     side_effect=engine.StopRun("Fixture reached outline"))) as modules:
                result = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(result, 2)
            self.open_scanned.assert_awaited_once()
            modules.assert_awaited_once()
            certificate.assert_not_awaited()
            verified.assert_not_awaited()

    async def test_site_unfinished_course_precedes_stale_local_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/stale-course\n"
                                   "https://www.coursera.org/learn/site-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("stale-course", previous_status="attention", current_module=1,
                         items_completed=1, run_directory=str(root / "logs" / "old"))
            observed = {"id":"site-course", "url":"https://www.coursera.org/learn/site-course",
                        "title":"Site Course", "action":"resume"}
            with patch.object(queue, "_scan_with_retry", AsyncMock(return_value=observed)) as discover:
                selected = await queue._next_course_id(
                    store, SimpleNamespace(courses=course_file), SimpleNamespace(), set(), True)
            self.assertEqual(selected, "site-course")
            discover.assert_awaited_once_with(store, unittest.mock.ANY, set())

    async def test_site_scan_error_cannot_fall_back_to_stale_local_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/stale-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("stale-course", previous_status="attention", current_module=1)
            with patch.object(queue, "_scan_with_retry", AsyncMock(
                    side_effect=RuntimeError("My Learning pagination is unreadable"))) as discover:
                with self.assertRaisesRegex(RuntimeError, "pagination is unreadable"):
                    await queue._next_course_id(
                        store, SimpleNamespace(courses=course_file), SimpleNamespace(), set(), True)
            discover.assert_awaited_once()

    async def test_unreadable_in_progress_scan_saves_attention_without_opening_course(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/first-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                                   continue_on_attention=False, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "_scan_with_retry", AsyncMock(
                     side_effect=RuntimeError("My Learning pagination is unreadable"))) as scan, \
                 patch.object(queue, "discover_modules", AsyncMock()) as modules:
                result = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(result, 2)
            self.assertEqual(store.data["status"], "attention")
            self.assertIn("pagination is unreadable", store.data.get("message", "") +
                          " ".join(event.get("message", "") for event in store.data["events"]))
            scan.assert_awaited_once()
            self.open_scanned.assert_not_awaited()
            modules.assert_not_awaited()

    async def test_pending_quiz_recovery_precedes_my_learning_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/first-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                                   continue_on_attention=False, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            order = []

            async def recover(*_args, **_kwargs):
                order.append("recover")
                return False

            async def scan(*_args):
                order.append("scan")
                return None

            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "recover_pending_quizzes", new=recover), \
                 patch.object(queue, "_scan_with_retry", new=scan, create=True), \
                 patch.object(queue, "discover_modules", AsyncMock(
                     side_effect=engine.StopRun("Fixture stops after startup"))):
                result = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(result, 2)
            self.assertEqual(order[:2], ["recover", "scan"])

    async def test_unresolved_pending_quiz_stops_before_my_learning_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/first-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                                   continue_on_attention=False, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "recover_pending_quizzes", AsyncMock(
                     side_effect=engine.StopRun("Pending quiz result is uncertain"))), \
                 patch.object(queue, "_scan_with_retry", AsyncMock(return_value=None), create=True) as scan:
                result = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(result, 2)
            self.assertEqual(store.data["status"], "attention")
            self.assertTrue(any("Pending quiz result is uncertain" in event["message"]
                                for event in store.data["events"]))
            scan.assert_not_awaited()
            self.open_scanned.assert_not_awaited()

    async def test_known_in_progress_course_precedes_earlier_new_configured_course(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/new-course\n"
                                   "https://www.coursera.org/learn/site-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            args = SimpleNamespace(courses=course_file)
            observed = {"id":"site-course", "url":"https://www.coursera.org/learn/site-course",
                        "title":"Site Course", "action":"resume"}
            with patch.object(queue, "_scan_with_retry", AsyncMock(return_value=observed)) as discover:
                selected = await queue._next_course_id(store, args, SimpleNamespace(), set(), True)
            self.assertEqual(selected, "site-course")
            discover.assert_awaited_once_with(store, unittest.mock.ANY, set())
            self.assertEqual([c["id"] for c in load_courses(course_file)], ["new-course", "site-course"])

    async def test_removed_historical_course_rejoins_when_still_in_progress_on_site(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/new-course\n"
                                   "https://www.coursera.org/learn/older-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("older-course", status="attention", run_directory=str(root / "logs" / "old"))
            store.course("older-course").setdefault("checkpoint", {}).setdefault("attempts", {})["assignment:7jgHj"] = {
                "count": 1, "pending": True, "passed": False, "submit_phase": "confirm_clicked"}
            course_file.write_text("https://www.coursera.org/learn/new-course\n", encoding="utf-8")
            args = SimpleNamespace(courses=course_file)
            observed = {"id":"older-course", "url":"https://www.coursera.org/learn/older-course",
                        "title":"Older Course", "action":"resume"}
            async def check_reactivated_ledger(_tab, saved_store, *, root):
                self.assertEqual(root, Path(directory))
                self.open_scanned.assert_not_awaited()
                self.assertEqual([(course["id"], key) for course, key, _ in queue._unresolved(saved_store)],
                                 [("older-course", "assignment:7jgHj")])
                return []
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "_scan_with_retry", AsyncMock(return_value=observed)) as discover, \
                 patch.object(queue, "recover_pending_quizzes", new=AsyncMock(side_effect=check_reactivated_ledger)) as recover:
                selected = await queue._next_course_id(store, args, SimpleNamespace(), set(), True)
            self.assertEqual(selected, "older-course")
            self.assertFalse(store.course("older-course").get("removed_from_queue"))
            self.assertEqual([c["id"] for c in load_courses(course_file)], ["new-course", "older-course"])
            discover.assert_awaited_once()
            recover.assert_awaited_once()

    async def test_reactivated_reflection_is_checked_before_course_card_click(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/older-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("older-course", status="attention", run_directory=str(root / "logs" / "old"))
            store.course("older-course").setdefault("checkpoint", {})["pending_reflections"] = {
                "assignment:reflect123": {
                    "url": "https://www.coursera.org/learn/older-course/assignment-submission/reflect123/reflection",
                    "title": "Reflection (Optional) Practice Assignment", "phase": "first_clicked"}}
            course_file.write_text("https://www.coursera.org/learn/new-course\n", encoding="utf-8")
            args = SimpleNamespace(courses=course_file)
            observed = {"id": "older-course", "url": "https://www.coursera.org/learn/older-course",
                        "title": "Older Course", "action": "resume"}
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "_scan_with_retry", AsyncMock(return_value=observed)), \
                 patch.object(queue, "recover_pending_quizzes", new=AsyncMock()) as recover:
                with self.assertRaisesRegex(engine.StopRun, "reflection Submit cannot be checked"):
                    await queue._next_course_id(store, args, SimpleNamespace(), set(), True)
            self.open_scanned.assert_not_awaited()
            recover.assert_not_awaited()

    async def test_monitor_add_during_scan_refreshes_course_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/new-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            args = SimpleNamespace(courses=course_file)
            observed = {"id": "site-course", "url": "https://www.coursera.org/learn/site-course",
                        "title": "Site Course", "action": "resume"}
            async def scan(_store, _tab, _excluded):
                course_file.write_text("https://www.coursera.org/learn/new-course\n"
                                       "https://www.coursera.org/learn/site-course\n", encoding="utf-8")
                return observed
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "_scan_with_retry", new=scan):
                selected = await queue._next_course_id(store, args, SimpleNamespace(), set(), True)
            self.assertEqual(selected, "site-course")
            self.assertEqual(store.course("site-course")["id"], "site-course")
            self.assertEqual([row["id"] for row in load_courses(course_file)],
                             ["new-course", "site-course"])

    async def test_empty_in_progress_falls_back_to_configured_new_course(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/new-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            args = SimpleNamespace(courses=course_file)
            with patch.object(queue, "_scan_with_retry", AsyncMock(return_value=None)) as discover:
                selected = await queue._next_course_id(store, args, SimpleNamespace(), set(), True)
            self.assertEqual(selected, "new-course")
            discover.assert_awaited_once_with(store, unittest.mock.ANY, set())

    async def test_verified_empty_in_progress_ignores_stale_local_attention(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/stale-course\n"
                                   "https://www.coursera.org/learn/new-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("stale-course", previous_status="attention", current_module=1,
                         items_completed=1, run_directory=str(root / "logs" / "old"))
            with patch.object(queue, "_scan_with_retry", AsyncMock(return_value=None)) as discover:
                selected = await queue._next_course_id(
                    store, SimpleNamespace(courses=course_file), SimpleNamespace(), set(), True)
            self.assertEqual(selected, "new-course")
            discover.assert_awaited_once_with(store, unittest.mock.ANY, set())

    async def test_manual_queue_uses_configured_course_without_site_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/new-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            with patch.object(queue, "_scan_with_retry", AsyncMock()) as discover:
                selected = await queue._next_course_id(
                    store, SimpleNamespace(courses=course_file), SimpleNamespace(), set(), False)
            self.assertEqual(selected, "new-course")
            discover.assert_not_awaited()

    async def test_all_green_course_waits_for_delayed_certificate(self):
        course = {"id":"first-course", "title":"First Course", "inventory_complete":True,
                  "modules_total":2, "modules_discovered":2, "modules_completed":2,
                  "items_total":3, "items_completed":3}
        store = SimpleNamespace(update=Mock(), event=Mock())
        tab = SimpleNamespace(evaluate=AsyncMock())
        certificate = certificate_url("first-course")
        with patch.object(queue, "_remember_completed_certificate", AsyncMock(
                side_effect=[None, None, certificate])) as lookup, \
             patch.object(queue, "CERTIFICATE_RECHECKS", 3), \
             patch.object(queue, "CERTIFICATE_RETRY_DELAY", 0), \
             patch.object(queue.asyncio, "sleep", AsyncMock()) as sleep:
            found = await queue._wait_for_completed_certificate(store, tab, course)
        self.assertEqual(found, certificate)
        self.assertEqual(lookup.await_count, 3)
        self.assertEqual(sleep.await_count, 2)
        store.event.assert_called_once()

    async def test_unconfirmed_items_do_not_wait_or_enable_transition(self):
        course = {"id":"first-course", "title":"First Course", "inventory_complete":True,
                  "modules_total":2, "modules_discovered":2, "modules_completed":2,
                  "items_total":3, "items_completed":2}
        store = SimpleNamespace(update=Mock(), event=Mock())
        tab = SimpleNamespace(evaluate=AsyncMock())
        with patch.object(queue, "_remember_completed_certificate", AsyncMock(return_value=None)) as lookup, \
             patch.object(queue.asyncio, "sleep", AsyncMock()) as sleep:
            found = await queue._wait_for_completed_certificate(store, tab, course)
        self.assertIsNone(found)
        lookup.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_extra_course_tab_selects_exact_first_unfinished_course(self):
        pages = [
            {"url":"https://www.coursera.org/learn/deep-learning-with-pytorch/coach/A/dialogue"},
            {"url":"https://www.coursera.org/learn/securing-ai-and-advanced-topics/home/module/1"},
        ]
        store = SimpleNamespace(data={"courses":[
            {"id":"introduction-to-ai-for-cybersecurity", "status":"pending",
             "certificate_page_url":certificate_url("introduction-to-ai-for-cybersecurity")},
            {"id":"securing-ai-and-advanced-topics", "status":"attention"},
            {"id":"deep-learning-with-pytorch", "status":"pending"},
        ]})
        self.assertEqual(queue._preferred_learning_tabs(pages, store), [pages[1]])
        store.data["courses"][1]["certificate_page_url"] = certificate_url("securing-ai-and-advanced-topics")
        self.assertEqual(queue._preferred_learning_tabs(pages, store), [pages[0]])

    async def test_unrelated_course_tabs_fall_back_to_my_learning(self):
        store = SimpleNamespace(data={"courses":[{"id":"explainable-machine-learning-xai"}]})
        course_pages = [
            {"url":"https://www.coursera.org/learn/deep-learning-with-pytorch/coach/A/dialogue"},
            {"url":"https://www.coursera.org/learn/deep-learning-with-pytorch/home/module/1"},
        ]
        learning_page = {"url":"https://www.coursera.org/my-learning?myLearningTab=IN_PROGRESS",
                         "webSocketDebuggerUrl":"ws://learning"}
        tab = SimpleNamespace(connect=AsyncMock(),
                              evaluate=AsyncMock(return_value={"url":learning_page["url"]}),
                              close=AsyncMock())
        with patch.object(queue, "learning_tabs", return_value=course_pages), \
             patch.object(queue, "my_learning_tabs", return_value=[learning_page]), \
             patch.object(queue.engine, "ChromeTab", return_value=tab) as make_tab, \
             patch.object(queue, "run_queue", AsyncMock(return_value=0)) as run:
            result = await queue.session(store, SimpleNamespace())
        self.assertEqual(result, 0)
        make_tab.assert_called_once_with("ws://learning")
        run.assert_awaited_once_with(store, unittest.mock.ANY, tab)

    async def test_in_progress_tab_precedes_matching_saved_course_among_multiple_tabs(self):
        store = SimpleNamespace(data={"courses":[
            {"id":"deep-learning-with-pytorch", "status":"attention"}]})
        course_pages = [
            {"url":"https://www.coursera.org/learn/deep-learning-with-pytorch/lecture/intro",
             "webSocketDebuggerUrl":"ws://saved-course"},
            {"url":"https://www.coursera.org/learn/another-course/home/module/1",
             "webSocketDebuggerUrl":"ws://other-course"},
        ]
        learning_pages = [
            {"url":"https://www.coursera.org/my-learning?myLearningTab=COMPLETED",
             "webSocketDebuggerUrl":"ws://completed"},
            {"url":"https://www.coursera.org/my-learning?myLearningTab=IN_PROGRESS",
             "webSocketDebuggerUrl":"ws://in-progress"},
        ]
        tab = SimpleNamespace(connect=AsyncMock(),
                              evaluate=AsyncMock(return_value={"url":learning_pages[1]["url"]}),
                              close=AsyncMock())
        with patch.object(queue, "learning_tabs", return_value=course_pages), \
             patch.object(queue, "my_learning_tabs", return_value=learning_pages), \
             patch.object(queue.engine, "ChromeTab", return_value=tab) as make_tab, \
             patch.object(queue, "run_queue", AsyncMock(return_value=0)) as run:
            result = await queue.session(store, SimpleNamespace())
        self.assertEqual(result, 0)
        make_tab.assert_called_once_with("ws://in-progress")
        run.assert_awaited_once_with(store, unittest.mock.ANY, tab)

    async def test_saved_pending_quiz_tab_precedes_my_learning_for_recovery(self):
        course_url = "https://www.coursera.org/learn/first-course"
        quiz_page = {"url":course_url + "/assignment-submission/Q1/quiz/attempt",
                     "webSocketDebuggerUrl":"ws://pending-quiz"}
        learning_page = {"url":"https://www.coursera.org/my-learning?myLearningTab=IN_PROGRESS",
                         "webSocketDebuggerUrl":"ws://learning"}
        store = SimpleNamespace(data={"courses":[{"id":"first-course", "checkpoint":{
            "attempts":{"assignment:Q1":{"count":1, "pending":True}}}}]})
        tab = SimpleNamespace(connect=AsyncMock(),
                              evaluate=AsyncMock(return_value={"url":quiz_page["url"]}),
                              close=AsyncMock())
        with patch.object(queue, "learning_tabs", return_value=[quiz_page]), \
             patch.object(queue, "my_learning_tabs", return_value=[learning_page]), \
             patch.object(queue.engine, "ChromeTab", return_value=tab) as make_tab, \
             patch.object(queue, "run_queue", AsyncMock(return_value=0)):
            result = await queue.session(store, SimpleNamespace(auto_my_learning=True))
        self.assertEqual(result, 0)
        make_tab.assert_called_once_with("ws://pending-quiz")

    async def test_pending_quiz_tab_precedes_my_learning_with_other_failed_ledger(self):
        course_url = "https://www.coursera.org/learn/first-course"
        quiz_page = {"url":course_url + "/assignment-submission/Q1/quiz/attempt",
                     "webSocketDebuggerUrl":"ws://pending-quiz"}
        learning_page = {"url":"https://www.coursera.org/my-learning?myLearningTab=IN_PROGRESS",
                         "webSocketDebuggerUrl":"ws://learning"}
        store = SimpleNamespace(data={"courses":[{"id":"first-course", "checkpoint":{
            "attempts":{
                "assignment:Q1":{"count":2, "pending":True, "platform_attempt":2},
                "assignment:Q2":{"count":1, "pending":False, "passed":False,
                                 "feedback_ready":False}}}}]})
        tab = SimpleNamespace(connect=AsyncMock(),
                              evaluate=AsyncMock(return_value={"url":quiz_page["url"]}),
                              close=AsyncMock())
        with patch.object(queue, "learning_tabs", return_value=[quiz_page]), \
             patch.object(queue, "my_learning_tabs", return_value=[learning_page]), \
             patch.object(queue.engine, "ChromeTab", return_value=tab) as make_tab, \
             patch.object(queue, "run_queue", AsyncMock(return_value=0)) as run:
            result = await queue.session(store, SimpleNamespace(auto_my_learning=True))
        self.assertEqual(result, 0)
        make_tab.assert_called_once_with("ws://pending-quiz")
        run.assert_awaited_once_with(store, unittest.mock.ANY, tab)

    async def test_single_exact_tab_is_chosen_among_two_saved_pending_quizzes(self):
        course_url = "https://www.coursera.org/learn/first-course"
        quiz_page = {"url":course_url + "/assignment-submission/Q1/quiz/attempt",
                     "webSocketDebuggerUrl":"ws://pending-quiz"}
        learning_page = {"url":"https://www.coursera.org/my-learning?myLearningTab=IN_PROGRESS",
                         "webSocketDebuggerUrl":"ws://learning"}
        store = SimpleNamespace(data={"courses":[{"id":"first-course", "checkpoint":{
            "attempts":{
                "assignment:Q1":{"count":2, "pending":True, "submit_phase":"confirm_clicked"},
                "assignment:Q2":{"count":1, "pending":True,
                                 "submit_phase":"confirmation_observed"}}}}]})
        tab = SimpleNamespace(connect=AsyncMock(),
                              evaluate=AsyncMock(return_value={"url":quiz_page["url"]}),
                              close=AsyncMock())
        with patch.object(queue, "learning_tabs", return_value=[quiz_page]), \
             patch.object(queue, "my_learning_tabs", return_value=[learning_page]), \
             patch.object(queue.engine, "ChromeTab", return_value=tab) as make_tab, \
             patch.object(queue, "run_queue", AsyncMock(return_value=0)) as run:
            result = await queue.session(store, SimpleNamespace(auto_my_learning=True))
        self.assertEqual(result, 0)
        make_tab.assert_called_once_with("ws://pending-quiz")
        run.assert_awaited_once_with(store, unittest.mock.ANY, tab)

    async def test_duplicate_pending_result_tabs_save_attention_before_navigation(self):
        course_url = "https://www.coursera.org/learn/first-course"
        quiz_url = course_url + "/assignment-submission/Q1/quiz"
        tabs = [{"url":quiz_url + suffix, "webSocketDebuggerUrl":"ws://" + str(index)}
                for index, suffix in enumerate(("/attempt", "/view-feedback"), 1)]
        events = []
        store = SimpleNamespace(data={"courses":[{"id":"first-course", "checkpoint":{
            "attempts":{"assignment:Q1":{"count":1, "pending":True}}}}]},
            event=lambda *args: events.append(args))
        with patch.object(queue, "learning_tabs", return_value=tabs), \
             patch.object(queue, "my_learning_tabs", return_value=[]), \
             patch.object(queue.engine, "ChromeTab") as make_tab:
            result = await queue.session(store, SimpleNamespace(auto_my_learning=True))
        self.assertEqual(result, 2)
        self.assertEqual(store.data["status"], "attention")
        self.assertEqual(events[0][0], "queue_attention")
        make_tab.assert_not_called()

    async def test_saved_pending_reflection_tab_precedes_my_learning(self):
        course_url = "https://www.coursera.org/learn/first-course"
        base = course_url + "/assignment-submission/R1/reflection"
        reflection_page = {"url":base + "/attempt", "webSocketDebuggerUrl":"ws://reflection"}
        learning_page = {"url":"https://www.coursera.org/my-learning?myLearningTab=IN_PROGRESS",
                         "webSocketDebuggerUrl":"ws://learning"}
        store = SimpleNamespace(data={"courses":[]})
        tab = SimpleNamespace(connect=AsyncMock(),
                              evaluate=AsyncMock(return_value={"url":reflection_page["url"]}),
                              close=AsyncMock())
        marker = {"course_id":"first-course", "key":"assignment:R1", "url":base}
        with patch.object(queue, "pending_reflections", return_value=[marker]), \
             patch.object(queue, "learning_tabs", return_value=[reflection_page]), \
             patch.object(queue, "my_learning_tabs", return_value=[learning_page]), \
             patch.object(queue.engine, "ChromeTab", return_value=tab) as make_tab, \
             patch.object(queue, "run_queue", AsyncMock(return_value=0)):
            result = await queue.session(store, SimpleNamespace(auto_my_learning=True))
        self.assertEqual(result, 0)
        make_tab.assert_called_once_with("ws://reflection")

    async def test_multiple_course_tabs_without_my_learning_use_stable_candidate(self):
        store = SimpleNamespace(data={"courses":[{"id":"configured-course", "status":"pending"}]})
        course_pages = [
            {"id":"target-z", "url":"https://www.coursera.org/learn/z-course/home/module/1",
             "webSocketDebuggerUrl":"ws://z-course"},
            {"id":"target-a", "url":"https://www.coursera.org/learn/a-course/lecture/intro",
             "webSocketDebuggerUrl":"ws://a-course"},
        ]
        tab = SimpleNamespace(connect=AsyncMock(),
                              evaluate=AsyncMock(return_value={"url":course_pages[1]["url"]}),
                              close=AsyncMock())
        with patch.object(queue, "learning_tabs", return_value=course_pages), \
             patch.object(queue, "my_learning_tabs", return_value=[]), \
             patch.object(queue.engine, "ChromeTab", return_value=tab) as make_tab, \
             patch.object(queue, "run_queue", AsyncMock(return_value=0)) as run:
            result = await queue.session(store, SimpleNamespace(auto_my_learning=True))
        self.assertEqual(result, 0)
        make_tab.assert_called_once_with("ws://a-course")
        run.assert_awaited_once_with(store, unittest.mock.ANY, tab)

    async def test_new_chrome_tab_is_waited_for_before_session(self):
        with patch.object(queue, "learning_tabs", side_effect=[[], [{"url":"https://www.coursera.org/learn/first-course"}]]), \
             patch.object(queue, "my_learning_tabs", return_value=[]), \
             patch.object(queue.time, "sleep") as sleep:
            queue._wait_for_start_tab()
        sleep.assert_called_once_with(0.5)

    async def test_verified_course_restart_skips_certificate_browsing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/first-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            certificate = "https://www.coursera.org/account/accomplishments/records/ABC12345"
            store.update("first-course", status="completed", completion_method="certificate",
                         certificate_page_url=certificate,
                         certificate_verified_at="2026-10-15T12:00:00+05:00")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                                   continue_on_attention=False, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "open_certificate_page", AsyncMock(return_value=certificate)) as opened, \
                 patch.object(queue, "verify_saved_certificate", AsyncMock()) as verified, \
                 patch.object(queue, "open_my_learning", AsyncMock()) as learning, \
                 patch.object(queue, "scan_in_progress", AsyncMock(return_value=None)), \
                 patch.object(queue, "discover_modules", AsyncMock()) as modules:
                result = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(result, 0)
            self.assertEqual(store.course("first-course")["status"], "completed")
            self.assertEqual(store.course("first-course")["completion_method"], "certificate")
            self.assertTrue(store.course("first-course")["certificate_verified_at"])
            opened.assert_not_awaited()
            verified.assert_not_awaited()
            learning.assert_not_awaited()
            modules.assert_not_awaited()

    async def test_empty_in_progress_does_not_search_completed_certificates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/first-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("first-course", inventory_complete=True, modules_total=2,
                         modules_discovered=2, modules_completed=2,
                         items_total=3, items_completed=3)
            certificate = certificate_url("first-course")
            args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                                   continue_on_attention=False, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            tab = SimpleNamespace(evaluate=AsyncMock())
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "observed_certificate_url", AsyncMock(return_value=None)), \
                 patch.object(queue, "find_completed_certificate", AsyncMock(return_value=certificate)) as found, \
                 patch.object(queue, "open_certificate_page", AsyncMock(return_value=certificate)), \
                 patch.object(queue, "verify_saved_certificate", AsyncMock(side_effect=matching_certificate)) as verified, \
                 patch.object(queue, "open_my_learning", AsyncMock()) as learning, \
                 patch.object(queue, "scan_in_progress", AsyncMock(return_value=None)), \
                 patch.object(queue, "discover_modules", AsyncMock()) as modules:
                result = await queue.run_queue(store, args, tab)
            self.assertEqual(result, 2)
            self.assertEqual(store.course("first-course")["status"], "pending")
            self.assertFalse(store.course("first-course").get("certificate_page_url"))
            found.assert_not_awaited()
            verified.assert_not_awaited()
            learning.assert_not_awaited()
            modules.assert_not_awaited()

    async def test_unverified_local_inventory_is_not_treated_as_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/first-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("first-course", inventory_complete=True, modules_total=1,
                         modules_discovered=1, modules_completed=1,
                         items_total=1, items_completed=1)
            certificate = certificate_url("first-course")
            args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                                   continue_on_attention=False, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            tab = SimpleNamespace(evaluate=AsyncMock())
            wrong = {"verified": True, "method": "certificate", "course_id": "other-course"}
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "observed_certificate_url", AsyncMock(return_value=None)), \
                 patch.object(queue, "find_completed_certificate", AsyncMock(return_value=certificate)) as found, \
                 patch.object(queue, "open_certificate_page", AsyncMock(return_value=certificate)) as opened, \
                 patch.object(queue, "verify_saved_certificate", AsyncMock(return_value=wrong)) as verified, \
                 patch.object(queue, "discover_modules", AsyncMock()) as modules, \
                 patch.object(queue, "open_my_learning", AsyncMock()) as learning, \
                 patch.object(queue, "scan_in_progress", AsyncMock(return_value=None)) as discover:
                result = await queue.run_queue(store, args, tab)
            self.assertEqual(result, 2)
            self.assertEqual(store.course("first-course")["status"], "pending")
            found.assert_not_awaited()
            opened.assert_not_awaited()
            verified.assert_not_awaited()
            self.assertNotIn("course_completed", [event["kind"] for event in store.data["events"]])
            modules.assert_not_awaited()
            learning.assert_not_awaited()
            discover.assert_awaited_once()

    async def test_completed_tab_certificate_follows_false_home_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_file = root / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/first-course\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            certificate = certificate_url("first-course")
            args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                                   continue_on_attention=False, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            tab = SimpleNamespace(evaluate=AsyncMock())
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "observed_certificate_url", AsyncMock(return_value=None)), \
                 patch.object(queue, "inspect_sidebar_progress", AsyncMock(return_value=None)), \
                 patch.object(queue, "discover_modules", AsyncMock(return_value=[1])), \
                 patch.object(queue, "discover_roster", AsyncMock(return_value=[
                     {"key":"supplement:item", "title":"Overview",
                      "url":"https://www.coursera.org/learn/first-course/supplement/a/overview"}])), \
                 patch.object(queue.QueueRunner, "process_module", AsyncMock()), \
                 patch.object(queue, "verify_course_completion", AsyncMock(return_value={
                     "verified":False, "reason":"The course home page has not loaded."})) as home, \
                 patch.object(queue, "find_completed_certificate", AsyncMock(return_value=certificate)) as found, \
                 patch.object(queue, "open_certificate_page", AsyncMock(return_value=certificate)), \
                 patch.object(queue, "verify_saved_certificate", AsyncMock(side_effect=matching_certificate)), \
                 patch.object(queue, "open_my_learning", AsyncMock()), \
                 patch.object(queue, "scan_in_progress", AsyncMock(return_value=None)):
                result = await queue.run_queue(store, args, tab)
            self.assertEqual(result, 0)
            self.assertEqual(store.course("first-course")["status"], "completed")
            home.assert_awaited_once()
            found.assert_awaited_once_with(tab, "first-course", "first-course")

    async def test_unverified_saved_certificate_does_not_trigger_startup_visit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = "https://www.coursera.org/learn/first-course"
            course_file = root / "courses.txt"
            course_file.write_text(first + "\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            certificate = certificate_url("first-course")
            store.course("first-course")["certificate_page_url"] = certificate
            store.save()
            args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                                   continue_on_attention=True, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            wrong = {"verified":True, "method":"certificate", "course_id":"other-course"}
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "open_certificate_page", new=AsyncMock(return_value=certificate)) as opened, \
                 patch.object(queue, "verify_saved_certificate", new=AsyncMock(return_value=wrong)) as verified, \
                 patch.object(queue, "open_my_learning", AsyncMock()) as learning, \
                 patch.object(queue, "scan_in_progress", AsyncMock(return_value=None)) as discover, \
                 patch.object(queue, "discover_modules", AsyncMock()) as modules:
                result = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(result, 2)
            self.assertEqual(store.course("first-course")["status"], "pending")
            self.assertNotIn("course_completed", [event["kind"] for event in store.data["events"]])
            opened.assert_not_awaited()
            verified.assert_not_awaited()
            learning.assert_not_awaited()
            discover.assert_awaited_once()
            self.open_scanned.assert_not_awaited()
            modules.assert_not_awaited()

    async def test_get_started_opens_before_unrelated_saved_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = "https://www.coursera.org/learn/first-course"
            second = "https://www.coursera.org/learn/second-course"
            course_file = root / "courses.txt"
            course_file.write_text(first + "\n" + second + "\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            certificate = certificate_url("first-course")
            store.course("first-course")["certificate_page_url"] = certificate
            store.save()
            args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                                   continue_on_attention=True, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            observed_new = {"id":"second-course", "url":second,
                            "title":"Second Course", "action":"get_started"}
            wrong = {"verified":True, "method":"certificate", "course_id":"other-course"}
            calls = []

            async def scan(tab, excluded):
                calls.append("scan")
                return observed_new

            async def verify(tab, url, identity, title=None):
                calls.append("verify " + identity)
                return wrong

            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "scan_in_progress", new=scan), \
                 patch.object(queue, "open_certificate_page", AsyncMock(return_value=certificate)) as opened, \
                 patch.object(queue, "verify_saved_certificate", new=verify), \
                 patch.object(queue, "open_my_learning", AsyncMock()) as learning, \
                 patch.object(queue, "discover_modules", AsyncMock(
                     side_effect=engine.StopRun("Fixture stops after course selection"))) as modules:
                result = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(result, 2)
            self.assertEqual(calls, ["scan"])
            self.assertEqual(store.course("first-course")["status"], "pending")
            self.assertEqual(store.course("second-course")["status"], "attention")
            self.assertEqual([event["course_id"] for event in store.data["events"]
                              if event["kind"] == "course_started"], ["second-course"])
            learning.assert_not_awaited()
            self.open_scanned.assert_awaited_once()
            opened.assert_not_awaited()
            modules.assert_awaited_once()

    async def test_manual_queue_visible_certificate_is_verified_before_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = "https://www.coursera.org/learn/first-course"
            course_file = root / "courses.txt"
            course_file.write_text(first + "\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("first-course", inventory_complete=True, modules_total=1, modules_discovered=1)
            certificate = certificate_url("first-course")
            args = SimpleNamespace(courses=course_file, auto_my_learning=False,
                                   continue_on_attention=False, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            tab = SimpleNamespace(evaluate=AsyncMock())
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "observed_certificate_url", AsyncMock(return_value=certificate)) as observed, \
                 patch.object(queue, "open_certificate_page", new=AsyncMock(side_effect=opened_certificate)) as opened, \
                 patch.object(queue, "verify_saved_certificate", new=AsyncMock(side_effect=matching_certificate)) as verified, \
                 patch.object(queue, "open_my_learning", AsyncMock()) as learning, \
                 patch.object(queue, "scan_in_progress", AsyncMock(return_value=None)), \
                 patch.object(queue, "discover_modules", AsyncMock()) as modules:
                result = await queue.run_queue(store, args, tab)
            self.assertEqual(result, 0)
            self.assertEqual(store.course("first-course")["status"], "completed")
            self.assertEqual(store.course("first-course")["completion_method"], "certificate")
            self.assertEqual(store.course("first-course")["certificate_page_url"], certificate)
            observed.assert_awaited_once()
            opened.assert_awaited_once()
            verified.assert_awaited_once()
            learning.assert_not_awaited()
            modules.assert_not_awaited()

    async def test_manual_queue_visible_certificate_requires_matching_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = "https://www.coursera.org/learn/first-course"
            course_file = root / "courses.txt"
            course_file.write_text(first + "\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            store.update("first-course", inventory_complete=True, modules_total=1, modules_discovered=1)
            certificate = certificate_url("first-course")
            args = SimpleNamespace(courses=course_file, auto_my_learning=False,
                                   continue_on_attention=True, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            tab = SimpleNamespace(evaluate=AsyncMock())
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "observed_certificate_url", AsyncMock(return_value=certificate)), \
                 patch.object(queue, "open_certificate_page", new=AsyncMock(side_effect=opened_certificate)) as opened, \
                 patch.object(queue, "verify_saved_certificate", new=AsyncMock(side_effect=RuntimeError(
                     "Certificate page does not name this course"))) as verified, \
                 patch.object(queue, "open_my_learning", AsyncMock()) as learning, \
                 patch.object(queue, "scan_in_progress", AsyncMock(return_value=None)) as discover, \
                 patch.object(queue, "discover_modules", AsyncMock()) as modules:
                result = await queue.run_queue(store, args, tab)
            self.assertEqual(result, 2)
            self.assertEqual(store.course("first-course")["status"], "attention")
            self.assertNotIn("course_completed", [event["kind"] for event in store.data["events"]])
            opened.assert_awaited_once()
            verified.assert_awaited_once()
            learning.assert_not_awaited()
            discover.assert_not_awaited()
            modules.assert_not_awaited()

    async def test_new_course_is_saved_then_run_and_certificate_page_is_visited(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = "https://www.coursera.org/learn/first-course"
            second = "https://www.coursera.org/learn/second-course"
            course_file = root / "courses.txt"
            course_file.write_text(first + "\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                                   continue_on_attention=False, minutes=1,
                                   max_steps=60, quiz_attempts=0)
            selected = []

            async def finish_module(runner, number, roster):
                runner.course["certificate_page_url"] = certificate_url(runner.course["id"])

            async def discover(tab, excluded):
                selected.append(set(excluded))
                if len(selected) == 1:
                    return {"id": "second-course", "url": second,
                            "title": "Second Course", "action": "get_started"}
                return None

            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "discover_modules", AsyncMock(return_value=[1])), \
                 patch.object(queue, "discover_roster", AsyncMock(return_value=[
                     {"key": "supplement:item", "title": "Overview", "url": first + "/supplement/a/overview"}])), \
                 patch.object(queue.QueueRunner, "process_module", new=finish_module), \
                 patch.object(queue, "verify_course_completion", AsyncMock(return_value={
                     "verified": True, "evidence": ["Site confirmed completion"]})), \
                 patch.object(queue, "scan_in_progress", discover), \
                 patch.object(queue, "open_certificate_page", new=AsyncMock(side_effect=opened_certificate)) as certificate, \
                 patch.object(queue, "verify_saved_certificate", new=AsyncMock(side_effect=matching_certificate)) as verify_certificate, \
                 patch.object(queue, "open_my_learning", AsyncMock()) as learning:
                result = await queue.run_queue(store, args, SimpleNamespace())

            self.assertEqual(result, 0)
            self.assertEqual([c["id"] for c in load_courses(course_file)], ["first-course", "second-course"])
            self.assertEqual(store.course("second-course")["status"], "completed")
            self.assertEqual(store.course("second-course")["title"], "Second Course")
            self.assertEqual(selected, [set(), {"second-course"}, {"first-course", "second-course"}])
            self.assertEqual([event["course_id"] for event in store.data["events"]
                              if event["kind"] == "course_started"], ["second-course", "first-course"])
            self.assertEqual(certificate.await_count, 2)
            self.assertEqual(verify_certificate.await_count, 2)
            self.assertEqual(learning.await_count, 2)

    async def test_explicit_continue_on_attention_cannot_pass_course_without_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = "https://www.coursera.org/learn/first-course"
            second = "https://www.coursera.org/learn/second-course"
            course_file = root / "courses.txt"
            course_file.write_text(first + "\n", encoding="utf-8")
            store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
            args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                                   continue_on_attention=True, minutes=1,
                                   max_steps=60, quiz_attempts=0)

            async def module_list(tab, course_url, expectation):
                if course_url == first:
                    raise RuntimeError("Manual lab requires attention")
                return [1]

            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "discover_modules", module_list), \
                 patch.object(queue, "discover_roster", AsyncMock(return_value=[
                     {"key": "supplement:item", "title": "Overview", "url": second + "/supplement/a/overview"}])), \
                 patch.object(queue.QueueRunner, "process_module", AsyncMock()), \
                 patch.object(queue, "verify_course_completion", AsyncMock(return_value={
                     "verified": True, "evidence": ["Site confirmed completion"]})), \
                 patch.object(queue, "scan_in_progress", AsyncMock(return_value=None)) as discover, \
                 patch.object(queue, "open_certificate_page", AsyncMock()) as open_certificate, \
                 patch.object(queue, "open_my_learning", AsyncMock()):
                result = await queue.run_queue(store, args, SimpleNamespace())

            self.assertEqual(result, 2)
            self.assertEqual(store.course("first-course")["status"], "attention")
            self.assertEqual([course["id"] for course in store.data["courses"]], ["first-course"])
            discover.assert_awaited_once()
            open_certificate.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
