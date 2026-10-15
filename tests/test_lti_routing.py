"""Offline routing checks for Coursera ungraded LTI lab lessons."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import course_adapter as adapter
import coursera_start as engine
import lab_flow
from tests.test_course_queue import make_runner

COURSE = "https://www.coursera.org/learn/philosophy"
LESSON = COURSE + "/ungradedLti/LTI123/interactive-lab"
ITEM = {"key": "ungradedLti:LTI123", "type": "ungradedLti", "title": "Interactive lab", "url": LESSON}


class LtiRoutingTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        engine.configure_course(COURSE)

    async def test_roster_and_sidebar_recognize_lti_without_aliasing_to_other_lab(self):
        engine.configure_course(COURSE)
        snapshot = {"items": [{"type": "ungradedLti", "identity": "LTI123", "title": ITEM["title"],
                              "url": LESSON, "aria_label": "Not started", "checked": False}]}
        rows = adapter._roster_rows(snapshot)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], ITEM["key"])
        self.assertFalse(rows[0]["unsupported"])
        self.assertEqual(engine.item_key(LESSON), ITEM["key"])
        self.assertEqual(engine.rows_by_key(snapshot)[ITEM["key"]]["status"], "not_completed")
        self.assertIn("'ungradedLti'", adapter.SIDEBAR_JS)
        self.assertIn("'ungradedLti'", engine.PAGE_JS)

    async def test_same_verified_launch_path_accepts_lti_lab(self):
        self.assertEqual(lab_flow._lab_path(ITEM, COURSE),
                         "/learn/philosophy/ungradedLti/LTI123/interactive-lab")
        bad = dict(ITEM, key="ungradedLab:LTI123")
        with self.assertRaises(engine.StopRun):
            lab_flow._lab_path(bad, COURSE)

    async def test_queue_routes_unfinished_lti_to_lab_and_requires_green_mark(self):
        engine.configure_course(COURSE)
        with tempfile.TemporaryDirectory() as directory:
            runner, store = make_runner(directory)
            green = {"url": LESSON, "aria_label": "Completed", "checked": True}
            with patch.object(runner, "open_item", AsyncMock()) as opened, \
                 patch.object(runner, "verified_item", AsyncMock(side_effect=[False, False, True])) as verified, \
                 patch.object(runner, "lab", AsyncMock()) as run_lab, \
                 patch.object(runner, "snapshot", AsyncMock(return_value={"url": LESSON, "items": [green]})):
                await runner.process_module(1, [ITEM])
            opened.assert_awaited_once_with(ITEM)
            run_lab.assert_awaited_once_with(ITEM)
            self.assertEqual(verified.await_count, 3)
            self.assertTrue(runner.checkpoint["modules"]["1"]["verified_complete"])
            self.assertEqual(store.course("philosophy")["items_completed"], 1)

    async def test_lti_without_green_mark_stops_before_next_lesson(self):
        engine.configure_course(COURSE)
        with tempfile.TemporaryDirectory() as directory:
            runner, store = make_runner(directory)
            with patch.object(runner, "open_item", AsyncMock()), \
                 patch.object(runner, "verified_item", AsyncMock(return_value=False)), \
                 patch.object(runner, "lab", AsyncMock()) as run_lab:
                with self.assertRaisesRegex(engine.StopRun, "green completion mark"):
                    await runner.process_module(1, [ITEM])
            run_lab.assert_awaited_once_with(ITEM)
            self.assertEqual(store.course("philosophy")["items_completed"], 0)
