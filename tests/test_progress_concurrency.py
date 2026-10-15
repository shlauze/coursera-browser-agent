"""Targeted real-file concurrency regressions, confined to temporary directories."""
import asyncio
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import coursera_start as engine
from course_queue import QueueRunner
from course_state import QueueStore
from progress_monitor import ProgressReader
from shared_io import open_read_shared


URL = "https://www.coursera.org/learn/philosophy"
CONFIG = [{"id":"philosophy", "url":URL, "title":"Philosophy", "modules":None}]
FIXTURE = ROOT / "tests/fixtures/completed_module1.json"


class ConcurrentReader:
    """Keep the actual monitor read path hot while a writer updates its file."""
    def __init__(self, root):
        self.reader = ProgressReader(root)
        self.samples = []
        self.errors = []
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.read, daemon=True)

    def read(self):
        try:
            while not self.stop.is_set():
                sample = self.reader.snapshot()
                if sample.get("issue"):
                    raise AssertionError(sample["issue"])
                # JSON round-trip ensures the public snapshot remains serializable.
                self.samples.append(json.loads(json.dumps(sample)))
                self.ready.set()
                self.stop.wait(0.0005)
        except BaseException as error:
            self.errors.append(error)
            self.ready.set()

    def __enter__(self):
        self.thread.start()
        if not self.ready.wait(5):
            raise AssertionError("The monitor reader did not start")
        if self.errors:
            raise self.errors[0]
        return self

    def __exit__(self, *unused):
        self.stop.set()
        self.thread.join(5)
        if self.thread.is_alive():
            raise AssertionError("The monitor reader did not stop")
        if self.errors:
            raise self.errors[0]


class CompletedTab:
    """Replay a captured completed sidebar; reject every possible UI mutation."""
    def __init__(self, snapshot):
        self.snapshot = copy.deepcopy(snapshot)
        self.navigations = []
        self.inspections = 0

    async def navigate(self, url):
        if url != self.snapshot["url"]:
            raise AssertionError("Unexpected lesson navigation")
        self.navigations.append(url)

    async def evaluate(self, expression):
        if expression != engine.PAGE_JS:
            raise AssertionError("Completed materials must not dispatch DOM actions")
        self.inspections += 1
        return copy.deepcopy(self.snapshot)


class ProgressConcurrencyTests(unittest.TestCase):
    def tearDown(self):
        engine.configure_course(URL)

    def make_store(self, root, total):
        store = QueueStore(root / "state/queue.json", CONFIG)
        store.data.update(status="running", current_course="philosophy")
        store.update("philosophy", status="running", modules_total=1, modules_discovered=1,
                     inventory_complete=True, items_total=total, items_completed=0,
                     message="revision:0")
        return store

    def assert_no_temporary_files(self, root):
        self.assertEqual(list(root.rglob("*.tmp")), [])

    def test_160_atomic_queue_updates_during_real_monitor_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.make_store(root, 160)
            with ConcurrentReader(root) as watcher:
                for revision in range(1, 161):
                    store.update("philosophy", items_completed=revision, message=f"revision:{revision}")
            self.assertGreaterEqual(len(watcher.samples), 2)
            for sample in watcher.samples:
                row = sample["courses"][0]
                # Each read must be an entire old or new version, never a mix.
                self.assertEqual(row["message"], f"revision:{row['items_completed']}")
                self.assertTrue(0 <= row["items_completed"] <= 160)
            current = ProgressReader(root).snapshot()
            self.assertIsNone(current["issue"])
            self.assertEqual(current["courses"][0]["items_completed"], 160)
            with open_read_shared(root / "state/queue.json") as stream:
                raw = json.load(stream)
            self.assertEqual(raw["courses"][0]["message"], "revision:160")
            self.assert_no_temporary_files(root)

    def test_held_monitor_handle_released_soon_allows_bounded_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.make_store(root, 1)
            path = root / "state/queue.json"
            with open_read_shared(path) as old_stream:
                old = json.load(old_stream)
                # Some Windows filesystems still delay replacing an existing
                # target until the shared handle closes. Exercise that bounded
                # wait without requiring an indefinitely held target to move.
                release = threading.Timer(0.075, old_stream.close)
                release.start()
                try:
                    store.update("philosophy", items_completed=1, message="revision:1")
                finally:
                    release.join(2)
                    if release.is_alive():
                        release.cancel()
                        self.fail("The held reader was not released")
            self.assertEqual(old["courses"][0]["message"], "revision:0")
            with open_read_shared(path) as new_stream:
                new = json.load(new_stream)
            self.assertEqual(new["courses"][0]["message"], "revision:1")
            self.assert_no_temporary_files(root)

    def test_captured_nine_completed_items_finish_module_during_monitor_reads(self):
        engine.configure_course(URL)
        captured = json.loads(FIXTURE.read_text(encoding="utf-8"))
        # This older capture predates the explicit success-icon field. Model
        # the observed green marks required by the current completion gate.
        for row in captured["items"]:
            row["checked"] = True
        roster = list(engine.rows_by_key(captured).values())
        self.assertEqual(len(roster), 9)
        self.assertTrue(all(row["status"] == "completed" for row in roster))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.make_store(root, 9)
            run_directory = root / "logs/test-run"
            run_directory.mkdir(parents=True)
            tab = CompletedTab(captured)
            args = SimpleNamespace(minutes=1, max_steps=60, quiz_attempts=2)
            runner = QueueRunner(tab, args, run_directory, store, store.course("philosophy"))
            for method in ("reading", "video", "quiz", "solve_quiz_questions"):
                setattr(runner, method, AsyncMock(side_effect=AssertionError("No new completion action is permitted")))
            with ConcurrentReader(root) as watcher, redirect_stdout(io.StringIO()):
                asyncio.run(runner.process_module(1, roster))
            self.assertGreaterEqual(len(watcher.samples), 2)
            self.assertEqual(len(tab.navigations), 0)
            self.assertGreaterEqual(tab.inspections, 10)
            for method in ("reading", "video", "quiz", "solve_quiz_questions"):
                getattr(runner, method).assert_not_awaited()
            self.assertTrue(runner.checkpoint["modules"]["1"]["verified_complete"])
            reloaded = QueueStore(root / "state/queue.json", CONFIG)
            module = reloaded.course("philosophy")["checkpoint"]["modules"]["1"]
            self.assertTrue(module["verified_complete"])
            self.assertEqual(sum(row["verified_complete"] for row in module["items"].values()), 9)
            final = ProgressReader(root).snapshot()
            self.assertIsNone(final["issue"])
            self.assertEqual(final["courses"][0]["items_completed"], 9)
            self.assertEqual(final["courses"][0]["modules_completed"], 1)
            self.assertEqual(final["totals"]["completed_courses"], 0)
            self.assert_no_temporary_files(root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
