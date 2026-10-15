"""Offline tests; all HTTP servers bind loopback ephemeral ports and are stopped."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import shared_io
from course_state import atomic_json
spec = importlib.util.spec_from_file_location("progress_monitor", PROJECT / "progress_monitor.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def queue(**overrides):
    value = {"schema_version": 1, "status": "running", "updated_at": "2026-10-14T15:00:00+05:00",
             "current_course": "philosophy", "courses": [
                 {"id": "philosophy", "url": "https://www.coursera.org/learn/philosophy", "title": "Philosophy",
                  "status": "running", "modules_total": 14, "modules_completed": 1,
                  "modules_discovered": 2, "inventory_complete": False,
                  "items_total": 16, "items_completed": 14, "current_module": 2,
                  "current_item": "Practice: Morality", "message": "Retrying", "run_directory": "SECRET_PATH"},
                 {"id": "second", "url": "https://www.coursera.org/learn/second", "title": "Second",
                  "status": "pending", "modules_total": None, "modules_completed": 0,
                  "modules_discovered": 0, "inventory_complete": False,
                  "items_total": 0, "items_completed": 0}], "events": []}
    value.update(overrides)
    return value


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")
        return target

    def test_no_data_is_not_completion(self):
        data = monitor.ProgressReader(self.root).snapshot()
        self.assertFalse(data["has_data"])
        self.assertEqual(data["status"], "idle")
        self.assertEqual(data["totals"]["courses"], 0)

    def test_configured_courses_appear_before_queue_and_preserve_started_history(self):
        (self.root / "courses.txt").write_text(
            "# Selected courses\nhttps://www.coursera.org/learn/second\n"
            "https://www.coursera.org/learn/third\n", encoding="utf-8")
        raw = queue()
        raw["courses"][0]["status"] = "completed"
        raw["courses"][0]["inventory_complete"] = True
        self.put("state/queue.json", raw)
        data = monitor.ProgressReader(self.root).snapshot()
        self.assertEqual([c["id"] for c in data["courses"]], ["second", "third", "philosophy"])
        self.assertTrue(data["courses"][0]["in_list"])
        self.assertEqual(data["courses"][1]["status"], "pending")
        self.assertFalse(data["courses"][2]["in_list"])
        self.assertEqual(data["totals"]["courses"], 3)
        self.assertEqual(len(data["configuration"]["revision"]), 64)

    def test_course_added_after_completed_queue_waits_without_error(self):
        (self.root / "courses.txt").write_text(
            "https://www.coursera.org/learn/philosophy\n"
            "https://www.coursera.org/learn/new-course\n", encoding="utf-8")
        raw = queue(status="completed", current_course=None)
        raw["courses"] = [raw["courses"][0]]
        raw["courses"][0]["status"] = "completed"
        raw["courses"][0]["inventory_complete"] = True
        self.put("state/queue.json", raw)
        data = monitor.ProgressReader(self.root).snapshot()
        self.assertEqual(data["status"], "idle")
        self.assertEqual(data["totals"]["courses"], 2)
        self.assertEqual(data["totals"]["completed_courses"], 1)

    def test_limits_are_allowlisted_and_unknown_when_unavailable(self):
        raw = queue(limits={"groq_requests_remaining": 6, "groq_requests_limit": 30,
                            "groq_tokens_remaining": 1350, "groq_tokens_limit": 6000,
                            "groq_retry_after_seconds": 12.5,
                            "groq_updated_at": "2026-10-14T15:00:00+05:00",
                            "key": "DO_NOT_EXPOSE"})
        self.put("state/queue.json", raw)
        limits = monitor.ProgressReader(self.root).snapshot()["limits"]
        self.assertEqual(limits["groq_requests_remaining"], 6)
        self.assertEqual(limits["groq_retry_after_seconds"], 12.5)
        self.assertNotIn("DO_NOT_EXPOSE", json.dumps(limits))
        self.put("state/queue.json", queue(limits={"groq_requests_remaining": -1,
                                              "groq_tokens_remaining": "100"}))
        limits = monitor.ProgressReader(self.root).snapshot()["limits"]
        self.assertIsNone(limits["groq_requests_remaining"])
        self.assertIsNone(limits["groq_tokens_remaining"])

    def test_exact_counts_unknown_totals_and_allowlist(self):
        raw = queue()
        raw["api_key"] = "EXTRA_SECRET"
        raw["courses"][0]["answer_plan"] = "ANSWER_SECRET"
        raw["courses"][0]["checkpoint"] = {"attempts": {"selected": "CHECKPOINT_SECRET"}}
        self.put("state/queue.json", raw)
        data = monitor.ProgressReader(self.root).snapshot()
        self.assertEqual(data["totals"]["items"], 16)
        self.assertEqual(data["totals"]["completed_items"], 14)
        self.assertEqual(data["totals"]["modules"], None)
        self.assertEqual(data["totals"]["known_modules"], 14)
        self.assertEqual(data["totals"]["modules_discovered"], 2)
        self.assertFalse(data["totals"]["inventory_complete"])
        self.assertEqual(data["courses"][0]["modules_discovered"], 2)
        self.assertFalse(data["courses"][0]["inventory_complete"])
        self.assertEqual(data["current_course"], "philosophy")
        serialized = json.dumps(data)
        for secret in ["EXTRA_SECRET", "ANSWER_SECRET", "SECRET_PATH", "CHECKPOINT_SECRET"]:
            self.assertNotIn(secret, serialized)

    def test_empty_and_inconsistent_completion_rejected(self):
        empty = monitor.summarize_queue(queue(status="completed", courses=[]))
        self.assertEqual(empty["status"], "idle")
        invalid = monitor.summarize_queue(queue(status="completed"))
        self.assertEqual(invalid["status"], "attention")

    def test_valid_completed_queue(self):
        raw = queue(status="completed", current_course=None)
        for course in raw["courses"]:
            course["status"] = "completed"
            course["inventory_complete"] = True
        self.put("state/queue.json", raw)
        value = monitor.ProgressReader(self.root).snapshot()
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["totals"]["completed_courses"], 2)
        self.assertTrue(value["totals"]["inventory_complete"])
        self.assertIsNone(value["current_course"])

    def test_discovered_nine_of_nine_does_not_mean_course_complete(self):
        raw = queue()
        raw["courses"] = [raw["courses"][0]]
        raw["courses"][0].update(items_total=9, items_completed=9, modules_total=None,
                                  modules_completed=1, modules_discovered=1, inventory_complete=False)
        self.put("state/queue.json", raw)
        value = monitor.ProgressReader(self.root).snapshot()
        self.assertEqual(value["totals"]["items"], value["totals"]["completed_items"])
        self.assertEqual(value["totals"]["completed_courses"], 0)
        self.assertFalse(value["totals"]["inventory_complete"])
        self.assertEqual(value["status"], "running")

    def test_inconsistent_completion_without_inventory_requires_attention(self):
        for inventory in (False, None, "true", 1):
            with self.subTest(inventory=inventory):
                raw = queue(status="completed")
                for course in raw["courses"]:
                    course["status"] = "completed"
                    if inventory is None:
                        course.pop("inventory_complete")
                    else:
                        course["inventory_complete"] = inventory
                self.put("state/queue.json", raw)
                value = monitor.ProgressReader(self.root).snapshot()
                self.assertEqual(value["status"], "attention")
                self.assertEqual(value["totals"]["completed_courses"], 0)
                self.assertFalse(value["totals"]["inventory_complete"])

    def test_verified_certificate_confirms_completion_without_module_inventory(self):
        raw = queue(status="completed", current_course=None)
        raw["courses"] = [raw["courses"][0]]
        raw["courses"][0].update(
            status="completed", inventory_complete=False,
            completion_method="certificate", certificate_verified_at="2026-10-14T21:00:00+05:00",
            certificate_page_url="https://www.coursera.org/account/accomplishments/verify/ABC12345")
        self.put("state/queue.json", raw)
        value = monitor.ProgressReader(self.root).snapshot()
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["totals"]["completed_courses"], 1)
        self.assertTrue(value["courses"][0]["certificate_verified"])
        self.assertFalse(value["totals"]["inventory_complete"])

    def test_inventory_aggregate_requires_all_courses_discovered(self):
        raw = queue()
        raw["courses"][0]["inventory_complete"] = True
        self.put("state/queue.json", raw)
        value = monitor.ProgressReader(self.root).snapshot()
        self.assertTrue(value["courses"][0]["inventory_complete"])
        self.assertFalse(value["totals"]["inventory_complete"])

    def test_partial_write_keeps_last_good_and_recovers(self):
        path = self.put("state/queue.json", queue())
        reader = monitor.ProgressReader(self.root)
        self.assertEqual(reader.snapshot()["totals"]["completed_items"], 14)
        path.write_text('{"schema_version":', encoding="utf-8")
        snapshot = reader.snapshot()
        self.assertTrue(snapshot["issue"])
        self.assertTrue(snapshot["stale"])
        self.assertEqual(snapshot["totals"]["completed_items"], 14)
        self.put("state/queue.json", queue(status="paused"))
        self.assertIsNone(reader.snapshot()["issue"])
        self.assertEqual(reader.snapshot()["status"], "paused")

    def test_bad_initial_queue_does_not_claim_fallback_complete(self):
        path = self.put("state/queue.json", {})
        path.write_text("null", encoding="utf-8")
        self.put("logs/course_old/progress.json", {"finished": "2026-10-14T15:00:00", "modules": {}})
        data = monitor.ProgressReader(self.root).snapshot()
        self.assertFalse(data["has_data"])
        self.assertEqual(data["status"], "attention")

    def test_legacy_uses_roster_and_never_infers_whole_course_complete(self):
        self.put("logs/course_run/progress.json", {
            "course": "https://www.coursera.org/learn/philosophy", "finished": "2026-10-14T15:30:01",
            "modules": {"1": {"verified_complete": True, "roster": [{"key": "a"}, {"key": "b"}],
                               "items": {"a": {"title": "Reading", "verified_complete": True}}}},
            "latest": {"answers": "DONT_EXPOSE_ANSWERS"},
            "events": [{"event": "quiz_answer_plan", "plan": "DONT_EXPOSE_PLAN"},
                       {"event": "item_finished", "module": 1, "time": "2026-10-14T15:00:00"}]})
        data = monitor.ProgressReader(self.root).snapshot()
        self.assertEqual(data["source"], "legacy")
        self.assertEqual(data["status"], "paused")
        self.assertEqual(data["totals"]["items"], 2)
        self.assertEqual(data["totals"]["completed_items"], 1)
        self.assertIsNone(data["totals"]["modules"])
        self.assertFalse(data["totals"]["inventory_complete"])
        self.assertEqual(data["totals"]["modules_discovered"], 1)
        self.assertEqual(len(data["events"]), 1)
        self.assertNotIn("DONT_EXPOSE", json.dumps(data))

    def test_secret_redaction_and_event_bounds(self):
        raw = queue(events=[{"kind": "course_completed", "message": "ok"}] * 100)
        raw["events"].append({"kind": "quiz_answer_plan", "message": "SECRET_ANSWER"})
        raw["courses"][0]["message"] = "gsk_FAKESECRET sk-FAKESECRET test@example.com I, User Fullname, understand and agree."
        raw["courses"][0]["url"] = "https://evil.invalid/learn/x?key=SECRET"
        self.put("state/queue.json", raw)
        data = monitor.ProgressReader(self.root).snapshot()
        text = json.dumps(data)
        for word in ["FAKESECRET", "test@example.com", "User Fullname", "SECRET_ANSWER", "evil.invalid"]:
            self.assertNotIn(word, text)
        self.assertLessEqual(len(data["events"]), 60)

    def test_queue_scan_attention_reason_is_visible(self):
        raw = queue(status="attention", events=[
            {"kind": "queue_attention", "message": "My Learning pagination is unreadable"}])
        self.put("state/queue.json", raw)
        data = monitor.ProgressReader(self.root).snapshot()
        self.assertEqual(data["status"], "attention")
        self.assertEqual(data["events"][0]["message"], "My Learning pagination is unreadable")

    def test_overlarge_state_is_not_read(self):
        path = self.put("state/queue.json", {})
        with path.open("wb") as stream:
            stream.truncate(monitor.MAX_BYTES + 1)
        self.assertTrue(monitor.ProgressReader(self.root).snapshot()["issue"])

    def test_second_server_cannot_bind_same_port(self):
        server = monitor.create_server(self.root, port=0)
        try:
            with self.assertRaises(OSError):
                duplicate = monitor.create_server(self.root, port=server.server_address[1])
                duplicate.server_close()
        finally:
            server.server_close()

    def test_shared_reader_allows_renaming_its_open_file(self):
        original = self.put("state/queue.json", queue(status="running"))
        replacement = self.put("state/queue.next.json", queue(status="paused"))
        moved = self.root / "state" / "queue.old.json"
        with shared_io.open_read_shared(original) as opened:
            # Windows allows renaming a shared-delete source while open. Some
            # versions still deny overwriting an open destination; that separate
            # case uses the actual writer retry integration test below.
            os.replace(original, moved)
            os.replace(replacement, original)
            self.assertEqual(json.load(opened)["status"], "running")
            self.assertEqual(monitor.read_json(original, self.root)["status"], "paused")

    def test_atomic_writer_recovers_when_open_reader_is_released(self):
        original = self.put("state/queue.json", queue(status="running"))
        attempted = threading.Event()
        failures = []
        real_replace = os.replace

        def observed_replace(*args, **kwargs):
            try:
                return real_replace(*args, **kwargs)
            finally:
                attempted.set()

        def writer():
            try:
                atomic_json(original, queue(status="paused"))
            except BaseException as error:
                failures.append(error)

        with patch("course_state.os.replace", side_effect=observed_replace):
            with shared_io.open_read_shared(original) as opened:
                thread = threading.Thread(target=writer, daemon=True)
                thread.start()
                self.assertTrue(attempted.wait(timeout=3))
                self.assertEqual(json.load(opened)["status"], "running")
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(monitor.read_json(original, self.root)["status"], "paused")

    def test_monitor_reads_valid_json_during_atomic_replacement(self):
        path = self.put("state/queue.json", queue(status="running"))
        reader = monitor.ProgressReader(self.root)
        failures = []
        start = threading.Event()

        def writer():
            start.wait(timeout=3)
            try:
                for index in range(50):
                    atomic_json(path, queue(status="paused" if index % 2 else "running"))
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        start.set()
        try:
            for _ in range(50):
                result = reader.snapshot()
                self.assertIsNone(result["issue"])
                self.assertIn(result["status"], {"running", "paused"})
                self.assertEqual(result["totals"]["items"], 16)
        finally:
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

    @unittest.skipUnless(os.name == "nt", "Windows handle ownership regression")
    def test_windows_shared_reader_closes_handles_success_and_failures(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.argtypes = []
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessHandleCount.restype = wintypes.BOOL

        def handle_count():
            count = wintypes.DWORD()
            if not kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count)):
                raise ctypes.WinError(ctypes.get_last_error())
            return count.value

        path = self.put("shared.json", {"value": 1})
        before = handle_count()
        for _ in range(100):
            with shared_io.open_read_shared(path) as stream:
                self.assertEqual(json.load(stream), {"value": 1})
        self.assertLessEqual(handle_count(), before + 2)
        for target in (shared_io.msvcrt, shared_io.os):
            method = "open_osfhandle" if target is shared_io.msvcrt else "fdopen"
            before = handle_count()
            with patch.object(target, method, side_effect=OSError("Injected wrapper failure")):
                for _ in range(25):
                    with self.assertRaises(OSError):
                        with shared_io.open_read_shared(path):
                            self.fail("Wrapper failure should prevent entering the context")
            self.assertLessEqual(handle_count(), before + 2)

    def test_http_is_read_only_and_rejects_foreign_host_origin(self):
        self.put("state/queue.json", queue())
        server = monitor.create_server(self.root, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(base + "/api/progress", timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response)["totals"]["items"], 16)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertNotIn("Access-Control-Allow-Origin", response.headers)
            with urlopen(base, timeout=3) as response:
                self.assertIn("sha256-", response.headers["Content-Security-Policy"])
                self.assertIn("Прогресс курсов", response.read().decode())
            for path, headers, method, code in [
                ("/api/progress", {"Host": "evil.invalid"}, "GET", 403),
                ("/api/progress", {"Origin": "https://evil.invalid"}, "GET", 403),
                ("/api/progress", {"Origin": "null"}, "GET", 403),
                ("/api/progress", {"Origin": "http://127.0.0.1:1234"}, "GET", 403),
                ("/../.groq-key.dpapi", {}, "GET", 404),
                ("/state/queue.json", {}, "GET", 404),
                ("/api/progress", {}, "POST", 405)]:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(base + path, headers=headers, method=method), timeout=3)
                self.assertEqual(caught.exception.code, code)
                caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

    def test_course_editor_add_remove_and_conflict(self):
        original = "# Keep this comment\nhttps://www.coursera.org/learn/philosophy\n"
        (self.root / "courses.txt").write_text(original, encoding="utf-8")
        editor = monitor.CoursesEditor(self.root)
        rows, revision, _ = monitor.read_courses(self.root)
        self.assertEqual(len(rows), 1)
        with self.assertRaisesRegex(ValueError, "хотя бы один"):
            editor.change("remove", "philosophy", revision)
        added = editor.change("add", "https://coursera.org/learn/second?tracking=ignored", revision)
        self.assertEqual([row["id"] for row in added["courses"]], ["philosophy", "second"])
        with self.assertRaises(monitor.CourseListConflict):
            editor.change("remove", "philosophy", revision)
        removed = editor.change("remove", "philosophy", added["revision"])
        self.assertEqual([row["id"] for row in removed["courses"]], ["second"])
        saved = (self.root / "courses.txt").read_text(encoding="utf-8")
        self.assertIn("# Keep this comment", saved)
        self.assertNotIn("tracking=ignored", saved)
        self.assertNotIn("/learn/philosophy", saved)

    def test_http_course_editor_requires_same_origin_and_json_header(self):
        (self.root / "courses.txt").write_text("https://www.coursera.org/learn/philosophy\n", encoding="utf-8")
        server = monitor.create_server(self.root, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(base + "/api/progress", timeout=3) as response:
                revision = json.load(response)["configuration"]["revision"]
            payload = json.dumps({"action": "add", "value": "https://www.coursera.org/learn/second",
                                  "revision": revision}).encode("utf-8")
            for headers, code in [
                ({"Content-Type": "application/json", "X-Monitor-Action": "courses"}, 403),
                ({"Content-Type": "application/json", "X-Monitor-Action": "courses",
                  "Origin": "https://evil.invalid"}, 403),
                ({"Content-Type": "text/plain", "X-Monitor-Action": "courses",
                  "Origin": base}, 403),
            ]:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(base + "/api/courses", data=payload, headers=headers,
                                    method="POST"), timeout=3)
                self.assertEqual(caught.exception.code, code)
                caught.exception.close()
            request = Request(base + "/api/courses", data=payload, method="POST",
                              headers={"Content-Type": "application/json", "X-Monitor-Action": "courses",
                                       "Origin": base})
            with urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(len(json.load(response)["courses"]), 2)
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=3)
            self.assertEqual(caught.exception.code, 409)
            caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main(verbosity=2)
