"""The queue must inspect a pending optional Submit before any navigation."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import course_queue as queue
import coursera_start as engine


COURSE = "explainable-machine-learning-xai"
URL = ("https://www.coursera.org/learn/" + COURSE +
       "/assignment-submission/o897v/write-your-own-counterfactuals-optional")
ITEM = {"key": "assignment:o897v", "url": URL,
        "title": "Write your own Counterfactuals (Optional) Practice Assignment • Started"}


class Store:
    def __init__(self, course):
        self.data = {"courses": [course], "status": "pending", "current_course": None}
        self.events = []

    def update(self, identity, **fields):
        self.data["courses"][0].update(fields)

    def event(self, kind, message, identity=None):
        self.events.append((kind, message, identity))

    def save(self):
        pass


class ReflectionPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_roster_recovery_runs_before_course_navigation(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "logs" / "queue_previous"
            directory.mkdir(parents=True)
            (directory / "events.jsonl").write_text(json.dumps({
                "event": "module_roster", "module": 1, "roster": [ITEM]}) + "\n", encoding="utf-8")
            course = {"id": COURSE, "url": "https://www.coursera.org/learn/" + COURSE,
                      "title": "Explainable Machine Learning (XAI)", "checkpoint": {"modules": {}},
                      "run_directory": str(directory)}
            store = Store(course)
            tab = SimpleNamespace(evaluate=AsyncMock(return_value={"url": URL + "/attempt"}))
            args = SimpleNamespace(quiz_attempts=0)
            with patch.object(queue, "ROOT", root), \
                 patch.object(engine, "configure_course"), \
                 patch.object(queue, "recover_pending_reflection", new_callable=AsyncMock,
                              return_value=True) as recover:
                self.assertTrue(await queue._recover_visible_optional_reflection(store, args, tab))
            recover.assert_awaited_once()
            self.assertEqual(recover.await_args.args[1], ITEM)
            self.assertTrue(any(event[0] == "reflection_preflight_recovered" for event in store.events))

    async def test_unresolved_modal_stops_queue_before_saved_certificate_navigation(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "logs" / "queue_previous"
            directory.mkdir(parents=True)
            (directory / "events.jsonl").write_text(json.dumps({
                "event": "module_roster", "module": 1, "roster": [ITEM]}) + "\n", encoding="utf-8")
            course = {"id": COURSE, "url": "https://www.coursera.org/learn/" + COURSE,
                      "title": "Explainable Machine Learning (XAI)", "checkpoint": {"modules": {}},
                      "run_directory": str(directory)}
            store = Store(course)
            tab = SimpleNamespace(evaluate=AsyncMock(return_value={"url": URL + "/attempt"}))
            args = SimpleNamespace(quiz_attempts=0, auto_my_learning=True)
            with patch.object(queue, "ROOT", root), \
                 patch.object(engine, "configure_course"), \
                 patch.object(queue, "recover_pending_reflection", new_callable=AsyncMock,
                              side_effect=engine.StopRun("Submit outcome unknown")):
                self.assertEqual(await queue.run_queue(store, args, tab), 2)
            self.assertEqual(store.data["status"], "attention")
            self.assertIn("Submit outcome unknown", course["message"])
            self.assertEqual(course["current_item"], ITEM["key"])

    def test_mismatched_item_url_is_not_selected_from_saved_log(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "logs" / "queue_previous"
            directory.mkdir(parents=True)
            (directory / "events.jsonl").write_text(json.dumps({
                "event": "module_roster", "module": 1, "roster": [ITEM]}) + "\n", encoding="utf-8")
            course = {"checkpoint": {"modules": {}}, "run_directory": str(directory)}
            with patch.object(queue, "ROOT", root):
                self.assertIsNone(queue._saved_reflection_item(
                    course, ITEM["key"], URL + "-another/attempt"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
