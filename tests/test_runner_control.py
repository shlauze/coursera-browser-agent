"""Offline regressions for the dashboard's durable runner commands."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import course_queue as queue
import coursera_start as engine
from course_state import QueueStore, load_courses
from runner_control import ControlConflict, ControlStore


def make_queue(root, *course_ids):
    course_file = root / "courses.txt"
    course_file.write_text("".join(
        f"https://www.coursera.org/learn/{course_id}\n" for course_id in course_ids),
        encoding="utf-8")
    store = QueueStore(root / "state" / "queue.json", load_courses(course_file))
    args = SimpleNamespace(courses=course_file, auto_my_learning=True,
                           minutes=1, max_steps=60, quiz_attempts=0)
    return store, args


class ControlStoreTests(unittest.TestCase):
    def test_commands_persist_and_stale_revision_cannot_override(self):
        with tempfile.TemporaryDirectory() as directory:
            control = ControlStore(directory)
            self.assertEqual(control.snapshot(),
                             {"revision": 0, "desired": "running", "skipped_courses": [],
                              "updated_at": None})
            paused = control.change("pause", revision=0)
            self.assertEqual(paused["desired"], "paused")
            self.assertEqual(paused["revision"], 1)
            with self.assertRaises(ControlConflict):
                control.change("stop", revision=0)
            skipped = control.change("skip", "first-course", revision=1)
            self.assertEqual(skipped["skipped_courses"], ["first-course"])
            self.assertEqual(ControlStore(directory).snapshot(), skipped)
            self.assertEqual(control.change("skip", "first-course", revision=2), skipped)
            resumed = control.change("resume", revision=2)
            self.assertEqual(resumed["desired"], "running")
            self.assertEqual(resumed["skipped_courses"], ["first-course"])

    def test_managed_start_keeps_pause_and_manual_start_clears_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            control = ControlStore(directory)
            control.change("pause")
            queue._prepare_control(directory, control_managed=True)
            self.assertEqual(control.snapshot()["desired"], "paused")
            control.change("stop")
            queue._prepare_control(directory, control_managed=False)
            self.assertEqual(control.snapshot()["desired"], "running")

    def test_bad_slug_and_corrupt_file_are_rejected_without_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            control = ControlStore(directory)
            with self.assertRaises(ValueError):
                control.change("skip", "https://example.org/course")
            control.path.parent.mkdir(parents=True, exist_ok=True)
            control.path.write_text('{"revision": false}', encoding="utf-8")
            with self.assertRaises(ValueError):
                control.change("start")
            self.assertEqual(control.path.read_text(encoding="utf-8"),
                             '{"revision": false}')


class QueueControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_my_learning_scan_and_local_fallback_exclude_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, args = make_queue(root, "first-course", "second-course")
            ControlStore(root).change("skip", "first-course")
            selected = {"id": "second-course", "url": "https://www.coursera.org/learn/second-course",
                        "title": "Second", "action": "resume"}
            with patch.object(queue, "_scan_with_retry", AsyncMock(return_value=selected)) as scan, \
                 patch.object(queue, "open_scanned_course", AsyncMock(return_value=selected)):
                found = await queue._next_course_id(store, args, SimpleNamespace(), set(), True)
            self.assertEqual(found, "second-course")
            self.assertEqual(scan.await_args.args[2], {"first-course"})
            with patch.object(queue, "_scan_with_retry", AsyncMock(return_value=None)):
                self.assertIsNone(await queue._next_course_id(
                    store, args, SimpleNamespace(), {"second-course"}, True))

    async def test_preflight_runs_before_stop_and_my_learning_is_not_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, args = make_queue(root, "first-course")
            ControlStore(root).change("stop")
            order = []

            async def recover(*_args, **_kwargs):
                order.append("reconcile")

            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "_recover_visible_optional_reflection", AsyncMock()), \
                 patch.object(queue, "recover_pending_quizzes", new=recover), \
                 patch.object(queue, "_scan_with_retry", AsyncMock()) as scan:
                exit_code = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(exit_code, 130)
            self.assertEqual(order, ["reconcile"])
            self.assertEqual(store.data["status"], "paused")
            scan.assert_not_awaited()

    async def test_managed_pause_waits_after_preflight_before_scanning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, args = make_queue(root, "first-course")
            control = ControlStore(root)
            control.change("skip", "first-course")
            control.change("pause")
            order = []
            ordinary_sleep = asyncio.sleep

            async def recover(*_args, **_kwargs):
                order.append("reconcile")

            async def scan(*_args):
                order.append("scan")
                return None

            async def sleep(seconds):
                if seconds == 1:
                    order.append("paused")
                    control.change("resume")
                else:
                    await ordinary_sleep(3600)

            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "_recover_visible_optional_reflection", AsyncMock()), \
                 patch.object(queue, "recover_pending_quizzes", new=recover), \
                 patch.object(queue, "_scan_with_retry", new=scan), \
                 patch.object(queue.asyncio, "sleep", new=sleep):
                exit_code = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(exit_code, 0)
            self.assertEqual(order, ["reconcile", "paused", "scan"])
            self.assertTrue(any(event["kind"] == "queue_resumed"
                                for event in store.data["events"]))

    async def test_pause_waits_for_resume_and_preserves_course_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, _args = make_queue(root, "first-course")
            control = ControlStore(root)
            control.change("pause")

            class Timeout:
                def __init__(self):
                    self.deadline = asyncio.get_running_loop().time() + 10
                    self.calls = []

                def when(self):
                    return self.deadline

                def reschedule(self, deadline):
                    self.calls.append(deadline)
                    self.deadline = deadline

            timeout = Timeout()

            async def resume_after_poll(_seconds):
                control.change("resume")

            with patch.object(queue.asyncio, "sleep", new=resume_after_poll):
                await queue._control_point(store, "first-course",
                                           SimpleNamespace(_course_timeout=timeout))
            self.assertEqual(store.data["status"], "running")
            self.assertIsNone(timeout.calls[0])
            self.assertGreater(timeout.calls[1], 0)

    async def test_skip_waits_until_first_material_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, args = make_queue(root, "first-course")
            course = store.course("first-course")
            runner = queue.QueueRunner(SimpleNamespace(), args, root, store, course)
            first = {"key": "supplement:AAA", "title": "First",
                     "url": "https://www.coursera.org/learn/first-course/supplement/AAA/first"}
            second = {"key": "supplement:BBB", "title": "Second",
                      "url": "https://www.coursera.org/learn/first-course/supplement/BBB/second"}

            async def verified(item, **_kwargs):
                if item["key"] == first["key"]:
                    ControlStore(root).change("skip", "first-course")
                    return True
                self.fail("The second material must not be opened after Skip")

            runner.verified_item = verified
            with patch.object(queue.engine, "is_course_url", return_value=True):
                with self.assertRaises(queue.CourseSkipped):
                    await runner.process_module(1, [first, second])
            items = runner.checkpoint["modules"]["1"]["items"]
            self.assertTrue(items[first["key"]]["verified_complete"])
            self.assertNotIn(second["key"], items)

    async def test_all_skipped_courses_end_paused_without_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, args = make_queue(root, "first-course", "second-course")
            control = ControlStore(root)
            control.change("skip", "first-course")
            control.change("skip", "second-course")
            with patch.object(queue, "ROOT", root), \
                 patch.object(queue, "_recover_visible_optional_reflection", AsyncMock()), \
                 patch.object(queue, "recover_pending_quizzes", AsyncMock()), \
                 patch.object(queue, "_scan_with_retry", AsyncMock(return_value=None)):
                exit_code = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(exit_code, 0)
            self.assertEqual(store.data["status"], "paused")
            self.assertTrue(all(row["status"] != "completed" for row in store.data["courses"]))

    async def test_skipping_current_course_advances_to_next_without_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, args = make_queue(root, "first-course", "second-course")
            first = {"key": "supplement:AAA", "title": "Reading",
                     "url": "https://www.coursera.org/learn/first-course/supplement/AAA/reading"}
            visited = []
            scans = []
            cards = {
                identity: {"id": identity, "url": f"https://www.coursera.org/learn/{identity}",
                           "title": identity, "action": "resume"}
                for identity in ("first-course", "second-course")}

            async def scan(_store, _tab, excluded):
                scans.append(set(excluded))
                return cards["second-course" if "first-course" in excluded else "first-course"]

            async def process(runner, _number, _roster):
                visited.append(runner.course["id"])
                if runner.course["id"] == "first-course":
                    ControlStore(root).change("skip", "first-course")
                    await queue._control_point(runner.store, runner.course["id"], runner)
                raise engine.StopRun("Second course reached")

            with patch.object(queue, "ROOT", root), \
                 patch.object(queue.engine, "configure_course"), \
                 patch.object(queue, "_recover_visible_optional_reflection", AsyncMock()), \
                 patch.object(queue, "recover_pending_quizzes", AsyncMock()), \
                 patch.object(queue, "_scan_with_retry", new=scan), \
                 patch.object(queue, "open_scanned_course", AsyncMock(
                     side_effect=lambda _tab, selected: selected)), \
                 patch.object(queue, "discover_modules", AsyncMock(return_value=[1])), \
                 patch.object(queue, "discover_roster", AsyncMock(return_value=[first])), \
                 patch.object(queue.QueueRunner, "process_module", new=process):
                result = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(result, 2)
            self.assertEqual(visited, ["first-course", "second-course"])
            self.assertEqual(scans, [set(), {"first-course"}])
            self.assertEqual(store.course("first-course")["status"], "skipped")
            self.assertNotEqual(store.course("first-course").get("completion_method"), "certificate")
