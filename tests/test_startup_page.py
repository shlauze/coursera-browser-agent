"""Offline checks for the first browser page shown by the queue runner."""
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import coursera_start as engine
import course_queue as queue
from my_learning import MY_LEARNING_URL


class StartupBrowserPageTests(unittest.TestCase):
    @staticmethod
    def _cdp_json(url):
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/test"}
        if url.endswith("/json/list"):
            return [{"type": "page", "url": MY_LEARNING_URL}]
        raise AssertionError(url)

    def _socket(self, occupied):
        probe = Mock()
        probe.connect_ex.return_value = 0 if occupied else 1
        context = MagicMock()
        context.__enter__.return_value = probe
        return context

    def test_fresh_owned_chrome_opens_in_progress_as_its_first_page(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(engine, "find_chrome", return_value=Path(directory) / "chrome.exe"), \
             patch.object(engine, "PROFILE", Path(directory) / "profile"), \
             patch.object(engine.socket, "socket", return_value=self._socket(False)), \
             patch.object(engine.subprocess, "Popen") as launch, \
             patch.object(engine, "profile_process_running", return_value=True), \
             patch.object(engine, "local_json", side_effect=self._cdp_json):
            engine.ensure_browser(start_url=MY_LEARNING_URL)
        launch.assert_called_once()
        command = launch.call_args.args[0]
        self.assertEqual(command[-1], MY_LEARNING_URL)
        self.assertNotIn(engine.COURSE_URL, command)

    def test_existing_owned_chrome_is_reused_for_explicit_start_page(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(engine, "find_chrome", return_value=Path(directory) / "chrome.exe"), \
             patch.object(engine, "PROFILE", Path(directory) / "profile"), \
             patch.object(engine.socket, "socket", return_value=self._socket(True)), \
             patch.object(engine.subprocess, "Popen") as launch, \
             patch.object(engine, "profile_process_running", return_value=True), \
             patch.object(engine, "local_json", side_effect=self._cdp_json):
            engine.ensure_browser(start_url=MY_LEARNING_URL)
        launch.assert_not_called()

    def test_fresh_chrome_start_does_not_wait_for_login_redirect_to_finish(self):
        def cdp_json(url):
            if url.endswith("/json/version"):
                return {"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/test"}
            raise AssertionError("Startup must not require the exact page after Coursera redirects to login")

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(engine, "find_chrome", return_value=Path(directory) / "chrome.exe"), \
             patch.object(engine, "PROFILE", Path(directory) / "profile"), \
             patch.object(engine.socket, "socket", return_value=self._socket(False)), \
             patch.object(engine.subprocess, "Popen") as launch, \
             patch.object(engine, "profile_process_running", return_value=True), \
             patch.object(engine, "local_json", side_effect=cdp_json):
            engine.ensure_browser(start_url=MY_LEARNING_URL)
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0][-1], MY_LEARNING_URL)

    def test_queue_shows_in_progress_before_enter_prompt(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            course_file = Path(directory) / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/sample-course\n", encoding="utf-8")
            lock = MagicMock()
            stored = MagicMock()
            stored.__enter__.return_value = SimpleStore()

            async def show_page(*, allow_site_redirect=False):
                self.assertTrue(allow_site_redirect)
                events.append("show My Learning")

            def prompt(_message):
                self.assertEqual(events, ["launch browser", "show My Learning"])
                return "q"

            with patch.object(queue.sys, "argv", ["course_queue.py", "--courses", str(course_file)]), \
                 patch.object(queue, "RunLock", return_value=lock), \
                 patch.object(queue, "reported_store", return_value=stored), \
                 patch("configure_new_pc.load_notice", return_value="Example learner agrees"), \
                 patch.object(queue.engine, "configure_course"), \
                 patch.object(queue.engine, "groq_key", return_value="test-key"), \
                 patch.object(queue.engine, "ensure_browser", side_effect=lambda **_: events.append("launch browser")) as launch, \
                 patch.object(queue, "_show_my_learning_start_page", side_effect=show_page), \
                 patch.object(queue, "_wait_for_start_tab", side_effect=lambda: events.append("wait for tab")), \
                 patch("builtins.input", side_effect=prompt):
                with redirect_stdout(StringIO()):
                    self.assertEqual(queue.main(), 0)
        launch.assert_called_once_with(start_url=MY_LEARNING_URL)

    def test_after_enter_exact_in_progress_is_required_before_queue_session(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            course_file = Path(directory) / "courses.txt"
            course_file.write_text("https://www.coursera.org/learn/sample-course\n", encoding="utf-8")
            lock = MagicMock()
            stored = MagicMock()
            stored.__enter__.return_value = SimpleStore()
            set_state = Mock(return_value=0)

            async def show_page(*, allow_site_redirect=False):
                events.append("login redirect allowed" if allow_site_redirect else "exact My Learning")

            async def session(_store, _args):
                events.append("queue session")
                return 0

            def prompt(_message):
                self.assertEqual(events, ["launch browser", "login redirect allowed"])
                return ""

            with patch.object(queue.sys, "argv", ["course_queue.py", "--courses", str(course_file)]), \
                 patch.object(queue, "RunLock", return_value=lock), \
                 patch.object(queue, "reported_store", return_value=stored), \
                 patch("configure_new_pc.load_notice", return_value="Example learner agrees"), \
                 patch.object(queue.engine, "configure_course"), \
                 patch.object(queue.engine, "groq_key", return_value="test-key"), \
                 patch.object(queue.engine, "ensure_browser", side_effect=lambda **_: events.append("launch browser")), \
                 patch.object(queue, "_show_my_learning_start_page", side_effect=show_page), \
                 patch.object(queue, "_wait_for_start_tab", side_effect=lambda: events.append("wait for tab")), \
                 patch.object(queue, "session", side_effect=session), \
                 patch.object(queue.ctypes, "windll", SimpleNamespace(
                     kernel32=SimpleNamespace(SetThreadExecutionState=set_state))), \
                 patch("builtins.input", side_effect=prompt):
                with redirect_stdout(StringIO()):
                    self.assertEqual(queue.main(), 0)
        self.assertEqual(events, ["launch browser", "login redirect allowed",
                                  "exact My Learning", "wait for tab", "queue session"])


class SimpleStore:
    pass


class StartupTargetTests(unittest.IsolatedAsyncioTestCase):
    def _tabs(self, owner_call=None):
        owner = MagicMock()
        owner.connect = AsyncMock()
        owner.close = AsyncMock()
        owner.call = AsyncMock(side_effect=owner_call)
        return owner

    async def test_existing_exact_in_progress_target_is_activated_without_navigation(self):
        page = {"id": "my-learning", "url": MY_LEARNING_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/my-learning"}
        owner = self._tabs(owner_call=lambda *_: {})
        with patch.object(queue.engine, "local_json", return_value={
                "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/owner"}), \
             patch.object(queue, "my_learning_tabs", return_value=[page]), \
             patch.object(queue.engine, "ChromeTab", return_value=owner) as tab_factory:
            target = await queue._show_my_learning_start_page()
        self.assertEqual(target, "my-learning")
        tab_factory.assert_called_once_with("ws://127.0.0.1:9223/devtools/browser/owner")
        owner.call.assert_awaited_once_with("Target.activateTarget", {"targetId": "my-learning"})
        owner.close.assert_awaited_once()

    async def test_new_in_progress_target_preserves_open_pending_quiz(self):
        pending = {"id": "pending", "url":
                   "https://www.coursera.org/learn/sample-course/assignment-submission/Q1/quiz/attempt",
                   "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/pending"}
        shown = {"id": "new-my-learning", "url": MY_LEARNING_URL,
                 "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/new-my-learning"}
        calls = []

        async def owner_call(method, params):
            calls.append((method, params))
            return {"targetId": shown["id"]} if method == "Target.createTarget" else {}

        owner = self._tabs(owner_call=owner_call)
        with patch.object(queue.engine, "local_json", return_value={
                "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/owner"}), \
             patch.object(queue, "my_learning_tabs", side_effect=[[], [shown]]), \
             patch.object(queue, "learning_tabs", return_value=[pending]), \
             patch.object(queue.engine, "ChromeTab", return_value=owner) as tab_factory:
            target = await queue._show_my_learning_start_page()
        self.assertEqual(target, shown["id"])
        self.assertEqual(calls, [
            ("Target.createTarget", {"url": MY_LEARNING_URL}),
            ("Target.activateTarget", {"targetId": shown["id"]}),
        ])
        tab_factory.assert_called_once_with("ws://127.0.0.1:9223/devtools/browser/owner")
        owner.close.assert_awaited_once()

    async def test_existing_other_my_learning_tab_is_navigated_to_in_progress(self):
        previous = {"id": "my-learning", "url":
                    "https://www.coursera.org/my-learning?myLearningTab=COMPLETED",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/my-learning"}
        shown = {**previous, "url": MY_LEARNING_URL}
        owner = self._tabs(owner_call=lambda *_: {})
        page_tab = self._tabs(owner_call=lambda *_: {})
        addresses = {"ws://127.0.0.1:9223/devtools/browser/owner": owner,
                     previous["webSocketDebuggerUrl"]: page_tab}
        with patch.object(queue.engine, "local_json", return_value={
                "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/owner"}), \
             patch.object(queue, "my_learning_tabs", side_effect=[[previous], [shown]]), \
             patch.object(queue.engine, "ChromeTab", side_effect=lambda address: addresses[address]):
            target = await queue._show_my_learning_start_page()
        self.assertEqual(target, "my-learning")
        page_tab.call.assert_awaited_once_with("Page.navigate", {"url": MY_LEARNING_URL})
        owner.call.assert_awaited_once_with("Target.activateTarget", {"targetId": "my-learning"})
        page_tab.close.assert_awaited_once()
        owner.close.assert_awaited_once()

    async def test_initial_display_allows_same_site_login_redirect(self):
        login_url = "https://www.coursera.org/?authMode=login"
        target_id = "login-redirect"
        pending = {"id": "pending", "url":
                   "https://www.coursera.org/learn/sample-course/assignment-submission/Q1/quiz/attempt"}

        def cdp_json(url):
            if url.endswith("/json/version"):
                return {"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/owner"}
            if url.endswith("/json/list"):
                return [{"type": "page", "id": target_id, "url": login_url}]
            raise AssertionError(url)

        async def owner_call(method, _params):
            return {"targetId": target_id} if method == "Target.createTarget" else {}

        owner = self._tabs(owner_call=owner_call)
        with patch.object(queue.engine, "local_json", side_effect=cdp_json), \
             patch.object(queue, "my_learning_tabs", return_value=[]), \
             patch.object(queue, "learning_tabs", return_value=[pending]), \
             patch.object(queue.engine, "ChromeTab", return_value=owner), \
             patch.object(queue.asyncio, "sleep", new_callable=AsyncMock):
            found = await queue._show_my_learning_start_page(allow_site_redirect=True)
        self.assertEqual(found, target_id)
        self.assertEqual(owner.call.await_args_list[0].args[0], "Target.createTarget")
        owner.close.assert_awaited_once()

    async def test_after_enter_login_redirect_does_not_count_as_in_progress(self):
        login_url = "https://www.coursera.org/?authMode=login"
        target_id = "login-redirect"
        pending = {"id": "pending", "url":
                   "https://www.coursera.org/learn/sample-course/assignment-submission/Q1/quiz/attempt"}

        def cdp_json(url):
            if url.endswith("/json/version"):
                return {"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/owner"}
            if url.endswith("/json/list"):
                return [{"type": "page", "id": target_id, "url": login_url}]
            raise AssertionError(url)

        async def owner_call(method, _params):
            return {"targetId": target_id} if method == "Target.createTarget" else {}

        owner = self._tabs(owner_call=owner_call)
        with patch.object(queue.engine, "local_json", side_effect=cdp_json), \
             patch.object(queue, "my_learning_tabs", return_value=[]), \
             patch.object(queue, "learning_tabs", return_value=[pending]), \
             patch.object(queue.engine, "ChromeTab", return_value=owner), \
             patch.object(queue.asyncio, "sleep", new_callable=AsyncMock):
            with self.assertRaises(engine.StopRun):
                await queue._show_my_learning_start_page()
        owner.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
