"""A green outline mark alone is not proof that a Role Play met its tasks."""

from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import course_queue as queue
import coursera_start as engine
from course_state import QueueStore


FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "role_play_insider_threat.json").read_text(
    encoding="utf-8"))
COURSE = {"id": "adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa",
          "url": "https://www.coursera.org/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa",
          "title": FIXTURE["course"], "modules": None}


class RolePlayQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine.configure_course(COURSE["url"])

    def tearDown(self):
        engine.configure_course("https://www.coursera.org/learn/philosophy")

    def test_green_module_fast_path_requires_review_for_any_coach(self):
        role_play = {"key": FIXTURE["key"], "title": FIXTURE["title"],
                     "url": FIXTURE["url"]}
        reading = {"key": "supplement:READ", "title": "Reading",
                   "url": COURSE["url"] + "/supplement/READ/reading"}
        self.assertTrue(queue._requires_role_play_review([reading, role_play]))
        self.assertFalse(queue._requires_role_play_review([reading]))

    async def test_green_role_play_with_zero_tasks_still_runs_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QueueStore(Path(directory) / "state.json", [COURSE])
            args = SimpleNamespace(minutes=1, max_steps=60, quiz_attempts=2,
                                   continue_on_attention=False)
            runner = queue.QueueRunner(None, args, Path(directory), store,
                                       store.course(COURSE["id"]))
            # The heading itself omits the type; it appears on a separate
            # sidebar line, so a title-only classifier is insufficient.
            item = {"key": FIXTURE["key"], "url": FIXTURE["url"],
                    "title": FIXTURE["title"]}
            # Coursera showed a green sidebar check even though the feedback
            # said all five Role Play tasks were missing. Do not short-circuit
            # this lesson on the icon alone.
            row = {"url": item["url"], "key": item["key"],
                   "aria_label": "Completed", "checked": True}
            with patch.object(runner, "verified_item", AsyncMock(return_value=True)), \
                 patch.object(runner, "open_item", AsyncMock()) as opened, \
                 patch.object(queue, "probe_role_play", AsyncMock(return_value=True)), \
                 patch.object(runner, "role_play", AsyncMock()) as handled, \
                 patch.object(runner, "snapshot", AsyncMock(return_value={
                     "url": item["url"], "items": [row]})):
                await runner.process_module(1, [item])
            opened.assert_awaited_once_with(item)
            handled.assert_awaited_once_with(item)

    async def test_green_dialogue_without_tasks_remains_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QueueStore(Path(directory) / "state.json", [COURSE])
            args = SimpleNamespace(minutes=1, max_steps=60, quiz_attempts=2,
                                   continue_on_attention=False)
            runner = queue.QueueRunner(None, args, Path(directory), store,
                                       store.course(COURSE["id"]))
            item = {"key": "coach:OTHER", "url": COURSE["url"] + "/coach/OTHER/regular-dialogue",
                    "title": "A completed Dialogue Dialogue • 15 min"}
            row = {"url": item["url"], "key": item["key"],
                   "aria_label": "Completed", "checked": True}
            with patch.object(runner, "verified_item", AsyncMock(return_value=True)), \
                 patch.object(runner, "open_item", AsyncMock()) as opened, \
                 patch.object(queue, "probe_role_play", AsyncMock(return_value=False)), \
                 patch.object(runner, "dialogue", AsyncMock()) as handled, \
                 patch.object(runner, "snapshot", AsyncMock(return_value={
                     "url": item["url"], "items": [row]})):
                await runner.process_module(1, [item])
            opened.assert_awaited_once_with(item)
            handled.assert_not_awaited()

    async def test_role_play_timeout_does_not_recover_from_green_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QueueStore(Path(directory) / "state.json", [COURSE])
            args = SimpleNamespace(minutes=1, max_steps=60, quiz_attempts=2,
                                   continue_on_attention=False)
            runner = queue.QueueRunner(None, args, Path(directory), store,
                                       store.course(COURSE["id"]))
            item = {"key": FIXTURE["key"], "url": FIXTURE["url"],
                    "title": FIXTURE["title"]}
            with patch.object(runner, "verified_item", AsyncMock(return_value=True)), \
                 patch.object(runner, "open_item", AsyncMock()), \
                 patch.object(queue, "probe_role_play", AsyncMock(return_value=True)), \
                 patch.object(runner, "role_play", AsyncMock(side_effect=TimeoutError)):
                with self.assertRaisesRegex(engine.StopRun, "task and feedback verification"):
                    await runner.process_module(1, [item])


if __name__ == "__main__":
    unittest.main()
