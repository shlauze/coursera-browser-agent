"""Offline evidence that queue restarts reuse only runner-owned Chrome tabs."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import course_queue as queue
from my_learning import MY_LEARNING_URL


class Browser:
    def __init__(self, pages):
        self.browser_address = "ws://127.0.0.1:9223/devtools/browser/process-one"
        self.pages = {page["id"]: page for page in pages}
        self.created = []
        self.navigated = []
        self.activated = []
        self.closed_targets = []

    def page(self, identity, url):
        return {"type": "page", "id": identity, "url": url,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/" + identity}

    def local_json(self, url):
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": self.browser_address}
        if url.endswith("/json/list"):
            return list(self.pages.values())
        raise AssertionError(url)

    def my_learning_tabs(self):
        return [page for page in self.pages.values() if
                queue._my_learning_url(page["url"])]

    def learning_tabs(self):
        return [page for page in self.pages.values() if
                queue._learning_url(page["url"])]

    def tab(self, address):
        browser = self

        class FakeTab:
            async def connect(self):
                pass

            async def close(self):
                pass

            async def call(self, method, params=None):
                params = params or {}
                if method == "Target.createTarget":
                    identity = "created-" + str(len(browser.created) + 1)
                    browser.pages[identity] = browser.page(identity, params["url"])
                    browser.created.append(identity)
                    return {"targetId": identity}
                if method == "Target.activateTarget":
                    browser.activated.append(params["targetId"])
                    return {}
                if method == "Target.closeTarget":
                    browser.closed_targets.append(params["targetId"])
                    raise AssertionError("Owned tab recovery must not close a browser page")
                if method == "Page.navigate":
                    identity = address.rsplit("/", 1)[-1]
                    browser.pages[identity]["url"] = params["url"]
                    browser.navigated.append(identity)
                    return {}
                raise AssertionError(method)

        return FakeTab()


class OwnedTabTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_path = Path(self.directory.name) / "state" / "runner_tabs.json"
        self.state_patch = patch.object(queue, "OWNED_TABS_FILE", self.state_path)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def attach(self, browser):
        patches = [
            patch.object(queue.engine, "local_json", side_effect=browser.local_json),
            patch.object(queue.engine, "ChromeTab", side_effect=browser.tab),
            patch.object(queue, "my_learning_tabs", side_effect=browser.my_learning_tabs),
            patch.object(queue, "learning_tabs", side_effect=browser.learning_tabs),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    async def test_start_tab_is_reserved_and_worker_reused_across_restarts(self):
        browser = Browser([])
        browser.pages["user-learning"] = browser.page("user-learning", MY_LEARNING_URL)
        self.attach(browser)

        start_id = await queue._show_my_learning_start_page(manage_owned=True)
        first_worker = await queue._ensure_owned_worker_page()
        self.assertEqual(start_id, "user-learning")
        self.assertEqual(first_worker["id"], "created-1")
        self.assertEqual(browser.created, ["created-1"])
        self.assertEqual(browser.pages[start_id]["url"], MY_LEARNING_URL)

        browser.pages["created-1"]["url"] = (
            "https://www.coursera.org/learn/a-course/home/module/2")
        self.assertEqual(await queue._show_my_learning_start_page(manage_owned=True), start_id)
        second_worker = await queue._ensure_owned_worker_page()
        self.assertEqual(second_worker["id"], first_worker["id"])
        self.assertEqual(browser.created, ["created-1"])
        self.assertEqual(browser.navigated, ["created-1"])
        self.assertEqual(browser.closed_targets, [])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["start_target_id"], start_id)
        self.assertEqual(state["worker_target_id"], first_worker["id"])

    async def test_active_coach_is_preserved_and_new_worker_allocated(self):
        browser = Browser([])
        browser.pages["start"] = browser.page("start", MY_LEARNING_URL)
        self.attach(browser)
        await queue._show_my_learning_start_page(manage_owned=True)
        await queue._ensure_owned_worker_page()
        coach = "https://www.coursera.org/learn/a-course/coach/ABC/role-play"
        browser.pages["created-1"]["url"] = coach

        new_worker = await queue._ensure_owned_worker_page()
        self.assertEqual(new_worker["id"], "created-2")
        self.assertEqual(browser.pages["created-1"]["url"], coach)
        self.assertEqual(browser.closed_targets, [])
        self.assertNotIn("created-1", browser.navigated)

    async def test_session_uses_reserved_worker_instead_of_start_tab(self):
        worker = {"type": "page", "id": "worker", "url": MY_LEARNING_URL,
                  "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/worker"}
        start = {**worker, "id": "start", "webSocketDebuggerUrl":
                 "ws://127.0.0.1:9223/devtools/page/start"}
        tab = SimpleNamespace(connect=AsyncMock(),
                              evaluate=AsyncMock(return_value={"url": MY_LEARNING_URL}),
                              close=AsyncMock())
        store = SimpleNamespace(data={"courses": []})
        with patch.object(queue, "learning_tabs", return_value=[]), \
             patch.object(queue, "my_learning_tabs", return_value=[start]), \
             patch.object(queue, "_ensure_owned_worker_page", new=AsyncMock(return_value=worker)) as owned, \
             patch.object(queue.engine, "ChromeTab", return_value=tab) as factory, \
             patch.object(queue, "run_queue", new=AsyncMock(return_value=0)) as run:
            result = await queue.session(store, SimpleNamespace(owned_tabs=True, auto_my_learning=True))
        self.assertEqual(result, 0)
        owned.assert_awaited_once()
        factory.assert_called_once_with(worker["webSocketDebuggerUrl"])
        run.assert_awaited_once()

    async def test_pending_attempt_precedes_worker_creation(self):
        attempt = "https://www.coursera.org/learn/a-course/assignment-submission/Q1/quiz/attempt"
        page = {"type": "page", "id": "pending", "url": attempt,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/pending"}
        store = SimpleNamespace(data={"courses": [{"id": "a-course", "checkpoint": {
            "attempts": {"assignment:Q1": {"pending": True}}}}]})
        tab = SimpleNamespace(connect=AsyncMock(),
                              evaluate=AsyncMock(return_value={"url": attempt}),
                              close=AsyncMock())
        with patch.object(queue, "learning_tabs", return_value=[page]), \
             patch.object(queue, "my_learning_tabs", return_value=[]), \
             patch.object(queue, "_ensure_owned_worker_page", new=AsyncMock()) as owned, \
             patch.object(queue.engine, "ChromeTab", return_value=tab) as factory, \
             patch.object(queue, "run_queue", new=AsyncMock(return_value=0)):
            result = await queue.session(store, SimpleNamespace(owned_tabs=True, auto_my_learning=True))
        self.assertEqual(result, 0)
        owned.assert_not_awaited()
        factory.assert_called_once_with(page["webSocketDebuggerUrl"])

    async def test_unresolved_attempt_worker_is_preserved(self):
        browser = Browser([])
        browser.pages["start"] = browser.page("start", MY_LEARNING_URL)
        self.attach(browser)
        await queue._show_my_learning_start_page(manage_owned=True)
        await queue._ensure_owned_worker_page()
        attempt = "https://www.coursera.org/learn/a-course/assignment-submission/ABC/quiz/attempt"
        browser.pages["created-1"]["url"] = attempt
        new_worker = await queue._ensure_owned_worker_page()
        self.assertEqual(new_worker["id"], "created-2")
        self.assertEqual(browser.pages["created-1"]["url"], attempt)
        self.assertEqual(browser.closed_targets, [])

    async def test_unknown_or_changed_browser_owner_never_reuses_old_worker(self):
        browser = Browser([])
        browser.pages["start"] = browser.page("start", MY_LEARNING_URL)
        self.attach(browser)
        await queue._show_my_learning_start_page(manage_owned=True)
        await queue._ensure_owned_worker_page()
        browser.browser_address = "ws://127.0.0.1:9223/devtools/browser/process-two"
        browser.pages["created-1"]["url"] = "https://www.coursera.org/learn/a-course/home/module/2"
        new_worker = await queue._ensure_owned_worker_page()
        self.assertEqual(new_worker["id"], "created-2")
        self.assertNotIn("created-1", browser.navigated)


if __name__ == "__main__":
    unittest.main()
