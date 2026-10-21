from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from v2.control import Control, ControlError
from v2.dashboard import Dashboard
from v2.store import PendingActionError, Store


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Path(self.temporary.name) / "state.sqlite3"
        self.store = Store(self.db)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_action_intent_survives_restart_and_never_duplicates_pending(self) -> None:
        first = self.store.begin_action("course-a", "quiz-1", "submit", {"attempt": 1})
        with self.assertRaises(PendingActionError) as pending:
            Store(self.db).begin_action("course-a", "quiz-1", "submit", {"attempt": 2})
        self.assertEqual(first, pending.exception.action_id)
        self.assertEqual(len(Store(self.db).pending_actions()), 1)
        self.store.resolve_action(first, "confirmed", {"grade": "passed"})
        self.assertEqual(self.store.pending_actions(), [])
        self.assertEqual(self.store.get_action(first)["evidence"], {"grade": "passed"})
        with self.assertRaises(KeyError):
            self.store.resolve_action(first, "confirmed")

    def test_unclear_action_stays_unresolved_until_reconciled(self) -> None:
        action = self.store.begin_action("course-a", "lab-1", "submit")
        self.store.resolve_action(action, "unclear", {"reason": "timeout"})
        self.assertEqual(self.store.pending_actions()[0]["status"], "uncertain")
        with self.assertRaises(PendingActionError):
            self.store.begin_action("course-a", "lab-1", "submit")
        self.store.resolve_action(action, "confirmed", {"proof": "grade page"})
        self.assertEqual(self.store.pending_actions(), [])

    def test_queue_skip_persists_without_changing_other_courses(self) -> None:
        self.store.upsert_course("a", "First", progress_percent=99, status="started")
        self.store.upsert_course("b", "Second", progress_percent=0)
        self.store.set_course_skipped("a", True)
        self.assertEqual([c["course_id"] for c in Store(self.db).list_courses(include_skipped=False)], ["b"])
        self.assertTrue(Store(self.db).get_course("a")["skipped"])
        self.store.upsert_course("a", "First renamed", progress_percent=99, status="started")
        self.assertTrue(self.store.get_course("a")["skipped"])
        self.store.set_course_skipped("a", False)
        self.assertFalse(self.store.get_course("a")["skipped"])

    def test_dashboard_snapshot_omits_sensitive_details_and_redacts_log(self) -> None:
        self.store.upsert_course("c", "Course", url="https://example/?token=private",
                                 details={"cookie": "private"})
        self.store.record_event("INFO", "AUTH", "token=private Bearer abc123",
                                {"raw": "private"})
        snapshot = self.store.snapshot()
        self.assertNotIn("details", snapshot["courses"][0])
        self.assertNotIn("url", snapshot["courses"][0])
        message = snapshot["events"][0]["message"]
        self.assertNotIn("private", message)
        self.assertNotIn("abc123", message)

    def test_control_commands_and_stop_checkpoint(self) -> None:
        control = Control(self.store)
        self.assertEqual(control.get_state(), "stopped")
        control.mark_running()
        control.request_pause()
        self.assertEqual(control.get_state(), "paused")
        with self.assertRaises(ControlError):
            control.request_pause()
        control.request_resume()
        self.assertTrue(control.wait_if_paused())
        control.request_stop()
        self.assertFalse(control.wait_if_paused())
        control.mark_stopped()
        self.assertEqual(Store(self.db).get_control_state(), "stopped")


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / "state.sqlite3")
        self.control = Control(self.store)
        self.store.upsert_course("xai", "<Explainable AI>", status="started")
        self.dashboard = Dashboard(self.store, self.control, port=0,
                                   start_callback=self.control.mark_running)
        self.server = self.dashboard.build_server()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.dashboard.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary.cleanup()

    def post(self, path: str, *, csrf: str | None = None, origin: str | None = None):
        body = urlencode({"csrf": csrf or "wrong"}).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if origin:
            headers["Origin"] = origin
        return urlopen(Request(self.base + path, data=body, headers=headers), timeout=3)

    def test_dashboard_escapes_course_titles_and_hides_action_payload(self) -> None:
        self.store.begin_action("xai", "quiz", "submit", {"secret": "do-not-render"})
        html = urlopen(self.base, timeout=3).read().decode()
        self.assertIn("&lt;Explainable AI&gt;", html)
        self.assertNotIn("<Explainable AI>", html)
        self.assertNotIn("do-not-render", html)
        self.assertIn("незавершённое", html)

    def test_post_requires_csrf_and_local_origin(self) -> None:
        with self.assertRaises(HTTPError) as wrong_token:
            self.post("/action/start")
        self.assertEqual(wrong_token.exception.code, 403)
        wrong_token.exception.close()
        with self.assertRaises(HTTPError) as wrong_origin:
            self.post("/action/start", csrf=self.dashboard.csrf_token,
                      origin="https://attacker.example")
        self.assertEqual(wrong_origin.exception.code, 403)
        wrong_origin.exception.close()
        self.assertEqual(self.control.get_state(), "stopped")

    def test_start_pause_resume_skip_stop_from_panel(self) -> None:
        token = self.dashboard.csrf_token
        self.post("/action/start", csrf=token)
        self.assertEqual(self.control.get_state(), "running")
        self.post("/action/pause", csrf=token)
        self.assertEqual(self.control.get_state(), "paused")
        self.post("/action/resume", csrf=token)
        self.post("/course/xai/skip", csrf=token)
        self.assertTrue(self.store.get_course("xai")["skipped"])
        self.post("/course/xai/unskip", csrf=token)
        self.assertFalse(self.store.get_course("xai")["skipped"])
        self.post("/action/stop", csrf=token)
        self.assertEqual(self.control.get_state(), "stop_requested")


if __name__ == "__main__":
    unittest.main()
