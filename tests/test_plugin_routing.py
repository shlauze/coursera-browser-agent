"""Plugin lesson identities must reach the guarded completion handler."""

import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import course_adapter as adapter
import coursera_start as engine
from tests.test_course_queue import URL, make_runner


class PluginRoutingTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        engine.configure_course(URL)

    async def test_roster_and_sidebar_recognize_plugin_identity(self):
        engine.configure_course(URL)
        for kind in ("ungradedPlugin", "ungradedWidget"):
            with self.subTest(kind=kind):
                address = URL + "/" + kind + "/sample/overview"
                self.assertEqual(engine.item_key(address), kind + ":sample")
                snapshot = {"items": [{"url": address, "type": kind,
                                       "identity": "sample", "title": "Overview",
                                       "aria_label": "Not completed", "evidence": [],
                                       "checked": False}]}
                rows = adapter._roster_rows(snapshot)
                self.assertEqual(rows[0]["key"], kind + ":sample")
                self.assertFalse(rows[0]["unsupported"])

    async def test_module_routes_plugins_and_still_requires_green_mark(self):
        engine.configure_course(URL)
        for kind in ("ungradedPlugin", "ungradedWidget"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                runner, store = make_runner(directory)
                item = {"key": kind + ":sample", "title": "Overview",
                        "url": URL + "/" + kind + "/sample/overview", "unsupported": False}
                runner.open_item = AsyncMock()
                completed = set()
                runner.verified_item = AsyncMock(side_effect=lambda target, wait=0:
                                                 target["key"] in completed)

                async def finish(target):
                    completed.add(target["key"])

                runner.plugin = AsyncMock(side_effect=finish)
                runner.snapshot = AsyncMock(return_value={"url": item["url"]})
                with patch.object(engine, "rows_by_key", return_value={
                        item["key"]: {"status": "completed", "checked": True}}):
                    await runner.process_module(1, [item])
                runner.plugin.assert_awaited_once_with(item)
                self.assertTrue(runner.checkpoint["modules"]["1"]["verified_complete"])
                self.assertEqual(store.course("philosophy")["items_completed"], 1)


if __name__ == "__main__":
    unittest.main()
