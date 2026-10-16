"""The upload-project classifier is observational and never submits work."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import coursera_start as engine
import course_queue as queue
from course_state import QueueStore
from graded_upload_flow import inspect_graded_file_upload


COURSE = "https://www.coursera.org/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
ITEM = {
    "key": "assignment:CJAom",
    "title": "Project: Adversarial Attack Analysis & Defense Graded Assignment • 2h",
    "url": COURSE + "/assignment-submission/CJAom/project-adversarial-attack-analysis-defense",
}
ATTEMPT = ITEM["url"] + "/attempt"
MANUAL_REASON = "Это оцениваемый проект с загрузкой файла (Add File)."


class ReadOnlyTab:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def evaluate(self, expression):
        self.calls.append(expression)
        return self.result


class GradedUploadProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_file_upload_is_recognized_without_browser_actions(self):
        tab = ReadOnlyTab({"match": True, "url": ATTEMPT,
                           "upload_questions": 1, "add_file_buttons": 1,
                           "other_answer_controls": 0})
        reason = await inspect_graded_file_upload(tab, ITEM)
        self.assertIn("загрузкой файла", reason)
        self.assertIn("собственный", reason)
        self.assertEqual(len(tab.calls), 1)
        self.assertNotIn(".click(", tab.calls[0])
        self.assertNotIn(".submit(", tab.calls[0])
        self.assertNotIn(".dispatchEvent(", tab.calls[0])

    async def test_other_course_or_form_cannot_be_misclassified(self):
        for item in (
            {**ITEM, "key": "assignment:other"},
            {**ITEM, "url": ITEM["url"].replace("coursera.org", "elsewhere.test")},
            {**ITEM, "title": "Project Practice Assignment"},
            {**ITEM, "url": ITEM["url"] + "/attempt"},
            {**ITEM, "url": ITEM["url"] + "?unexpected=1"},
            {**ITEM, "url": ITEM["url"].replace("coursera.org", "coursera.org:invalid")},
        ):
            with self.subTest(item=item):
                tab = ReadOnlyTab({"match": True, "url": ATTEMPT})
                self.assertIsNone(await inspect_graded_file_upload(tab, item))
                self.assertEqual(tab.calls, [])
        for observed in ({"match": False, "url": ATTEMPT},
                         {"match": True, "url": ITEM["url"]},
                         {"match": True, "url": ATTEMPT + "?changed=1"}):
            with self.subTest(observed=observed):
                self.assertIsNone(await inspect_graded_file_upload(ReadOnlyTab(observed), ITEM))


class ManualCourseItemTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_upload_is_saved_as_incomplete_manual_item_without_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            engine.configure_course(COURSE)
            store = QueueStore(Path(directory) / "state.json", [{
                "id": COURSE.rsplit("/", 1)[-1], "url": COURSE,
                "title": "Adversarial AI", "modules": [1]}])
            course = store.course(COURSE.rsplit("/", 1)[-1])
            tab = ReadOnlyTab({"match": True, "url": ATTEMPT})
            runner = queue.QueueRunner(tab, SimpleNamespace(), Path(directory), store, course)
            runner.open_item = AsyncMock()
            runner.verified_item = AsyncMock(return_value=False)
            runner.snapshot = AsyncMock(return_value={"url": ATTEMPT, "items": []})
            runner.quiz = AsyncMock(side_effect=engine.StopRun(
                "A separate quiz dialog needs manual review: graded project"))
            with patch.object(queue, "_control_point", AsyncMock()):
                await runner.process_module(1, [ITEM])
            runner.quiz.assert_awaited_once_with(ITEM)
            self.assertEqual(len(tab.calls), 1)
            module = course["checkpoint"]["modules"]["1"]
            self.assertFalse(module["verified_complete"])
            self.assertFalse(module["items"][ITEM["key"]]["verified_complete"])
            self.assertTrue(module["items"][ITEM["key"]]["manual_required"])
            self.assertIn(MANUAL_REASON, module["items"][ITEM["key"]]["reason"])
            self.assertEqual(module["manual_required"], [ITEM["title"]])
            self.assertNotIn("quiz_submitted_result", [e["event"] for e in runner.data["events"]])

    async def test_unknown_dialog_still_stops_without_manual_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            engine.configure_course(COURSE)
            store = QueueStore(Path(directory) / "state.json", [{
                "id": COURSE.rsplit("/", 1)[-1], "url": COURSE,
                "title": "Adversarial AI", "modules": [1]}])
            course = store.course(COURSE.rsplit("/", 1)[-1])
            tab = ReadOnlyTab({"match": False, "url": ATTEMPT})
            runner = queue.QueueRunner(tab, SimpleNamespace(), Path(directory), store, course)
            runner.open_item = AsyncMock()
            runner.verified_item = AsyncMock(return_value=False)
            runner.quiz = AsyncMock(side_effect=engine.StopRun(
                "A separate quiz dialog needs manual review: unknown form"))
            with patch.object(queue, "_control_point", AsyncMock()):
                with self.assertRaisesRegex(engine.StopRun, "unknown form"):
                    await runner.process_module(1, [ITEM])
            self.assertFalse(course["checkpoint"]["modules"]["1"]["items"][ITEM["key"]]
                             .get("manual_required", False))

    async def test_authorship_guard_marks_manual_without_automatic_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            engine.configure_course(COURSE)
            store = QueueStore(Path(directory) / "state.json", [{
                "id": COURSE.rsplit("/", 1)[-1], "url": COURSE,
                "title": "Adversarial AI", "modules": [1]}])
            course = store.course(COURSE.rsplit("/", 1)[-1])
            tab = ReadOnlyTab({"match": False, "url": ATTEMPT})
            runner = queue.QueueRunner(tab, SimpleNamespace(), Path(directory), store, course)
            runner.open_item = AsyncMock()
            runner.verified_item = AsyncMock(return_value=False)
            runner.snapshot = AsyncMock(return_value={"url": ATTEMPT, "items": []})
            runner.quiz = AsyncMock(side_effect=engine.StopRun(
                "Coursera asks the learner to attest this is their own work. "
                "AI-drafted answers cannot be certified automatically; review and submit personally."))
            with patch.object(queue, "_control_point", AsyncMock()):
                await runner.process_module(1, [ITEM])
            self.assertEqual(tab.calls, [])
            item_state = course["checkpoint"]["modules"]["1"]["items"][ITEM["key"]]
            self.assertFalse(item_state["verified_complete"])
            self.assertTrue(item_state["manual_required"])
            self.assertIn("their own work", item_state["reason"])

    async def test_manual_course_stops_queue_before_next_course(self):
        with tempfile.TemporaryDirectory() as directory:
            first_id = COURSE.rsplit("/", 1)[-1]
            second_id = "another-learning-course"
            store = QueueStore(Path(directory) / "state.json", [
                {"id": first_id, "url": COURSE, "title": "Adversarial AI", "modules": [1]},
                {"id": second_id, "url": "https://www.coursera.org/learn/" + second_id,
                 "title": "Another course", "modules": [1]},
            ])
            selections = []

            async def next_course(_store, _args, _tab, processed, _auto):
                selections.append(set(processed))
                return first_id if not processed else second_id if processed == {first_id} else None

            async def record_manual(runner, number, roster):
                entry = {"roster": roster, "verified_complete": False,
                         "manual_required": [ITEM["title"]],
                         "items": {ITEM["key"]: {"title": ITEM["title"],
                             "verified_complete": False, "manual_required": True,
                             "reason": MANUAL_REASON + " Нужен файл учащегося."}}}
                runner.checkpoint["modules"][str(number)] = entry
                runner.data["modules"][str(number)] = entry

            args = SimpleNamespace(minutes=1, auto_my_learning=False)
            with patch.object(queue, "ROOT", Path(directory)), \
                 patch.object(queue, "_control_point", AsyncMock()), \
                 patch.object(queue, "_recover_visible_optional_reflection", AsyncMock()), \
                 patch.object(queue, "recover_pending_quizzes", AsyncMock()), \
                 patch.object(queue, "_next_course_id", new=next_course), \
                 patch.object(queue, "discover_modules", AsyncMock(return_value=[1])), \
                 patch.object(queue, "discover_roster", AsyncMock(return_value=[ITEM])), \
                 patch.object(queue.QueueRunner, "process_module", new=record_manual), \
                 patch.object(queue, "verify_course_completion", AsyncMock(return_value={
                     "verified": False, "reason": "The upload has no submitted grade"})):
                exit_code = await queue.run_queue(store, args, SimpleNamespace())
            self.assertEqual(exit_code, 2)
            self.assertEqual(selections, [set()])
            self.assertEqual(store.course(first_id)["status"], "attention")
            self.assertIn("загрузкой файла", store.course(first_id)["message"])
            self.assertNotEqual(store.course(first_id)["status"], "completed")
            self.assertEqual(store.course(second_id)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
