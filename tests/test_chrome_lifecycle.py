"""Offline checks for managed Chrome lifecycle logging."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import coursera_start as engine


def fake_socket(connected):
    probe = MagicMock()
    probe.connect_ex.return_value = 0 if connected else 1
    context = MagicMock()
    context.__enter__.return_value = probe
    return context


class ChromeLifecycleTests(unittest.TestCase):
    def test_spawn_record_has_process_id_and_creation_time_only(self):
        output = StringIO()
        process = SimpleNamespace(pid=43210, poll=lambda: None)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(engine, "find_chrome", return_value=Path(directory) / "chrome.exe"), \
             patch.object(engine, "PROFILE", Path(directory) / "profile"), \
             patch.object(engine.socket, "socket", return_value=fake_socket(False)), \
             patch.object(engine.subprocess, "Popen", return_value=process), \
             patch.object(engine, "profile_process_running", return_value=True), \
             patch.object(engine, "local_json", return_value={"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/test"}), \
             patch.object(engine, "_chrome_created_at_utc", return_value="2026-10-15T19:57:51.505+00:00"), \
             redirect_stdout(output):
            engine.ensure_browser()
        line = output.getvalue().strip()
        self.assertTrue(line.startswith("CHROME_LIFECYCLE: "))
        record = json.loads(line.split(": ", 1)[1])
        self.assertEqual(record, {"event": "spawned", "pid": 43210,
                                  "created_at_utc": "2026-10-15T19:57:51.505+00:00"})
        self.assertNotIn(directory, line)

    def test_exit_record_distinguishes_dead_spawn_from_live_cdp(self):
        output = StringIO()
        process = SimpleNamespace(pid=43210, poll=lambda: 17)
        with patch.object(engine, "_BROWSER_START_ATTEMPTED", True), \
             patch.object(engine, "_MANAGED_CHROME_PROCESS", process), \
             patch.object(engine, "_MANAGED_CHROME_CREATED_AT_UTC", "2026-10-15T19:57:51.505+00:00"), \
             patch.object(engine.socket, "socket", return_value=fake_socket(True)), \
             patch.object(engine, "local_json", return_value={"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/test"}), \
             patch.object(engine, "profile_process_running", return_value=True), \
             patch.object(engine, "_managed_chrome_identity", return_value={"pid": 98765,
                 "created_at_utc": "2026-10-14T08:00:00.000+00:00"}), \
             redirect_stdout(output):
            engine.log_browser_lifecycle_exit()
        record = json.loads(output.getvalue().split(": ", 1)[1])
        self.assertEqual(record["event"], "runner_exit")
        self.assertEqual(datetime.fromisoformat(record["observed_at_utc"]).tzinfo, timezone.utc)
        self.assertEqual(record["spawned_pid"], 43210)
        self.assertEqual(record["spawned_exit_code"], 17)
        self.assertTrue(record["port_9223_listening"])
        self.assertTrue(record["cdp_reachable"])
        self.assertTrue(record["managed_process_running"])
        self.assertEqual(record["managed_pid"], 98765)
        self.assertEqual(record["managed_created_at_utc"], "2026-10-14T08:00:00.000+00:00")

    def test_exit_record_skips_cdp_request_when_port_is_down(self):
        output = StringIO()
        with patch.object(engine, "_BROWSER_START_ATTEMPTED", True), \
             patch.object(engine, "_MANAGED_CHROME_PROCESS", None), \
             patch.object(engine, "_MANAGED_CHROME_CREATED_AT_UTC", None), \
             patch.object(engine.socket, "socket", return_value=fake_socket(False)), \
             patch.object(engine, "local_json") as cdp, \
             patch.object(engine, "profile_process_running", return_value=False), \
             patch.object(engine, "_managed_chrome_identity", return_value=None), \
             redirect_stdout(output):
            engine.log_browser_lifecycle_exit()
        cdp.assert_not_called()
        record = json.loads(output.getvalue().split(": ", 1)[1])
        self.assertFalse(record["port_9223_listening"])
        self.assertFalse(record["cdp_reachable"])
        self.assertFalse(record["managed_process_running"])
        self.assertIsNone(record["managed_pid"])
        self.assertIsNone(record["managed_created_at_utc"])

    def test_reused_profile_exit_logs_owned_process_identity_without_spawn(self):
        output = StringIO()
        with patch.object(engine, "_BROWSER_START_ATTEMPTED", True), \
             patch.object(engine, "_MANAGED_CHROME_PROCESS", None), \
             patch.object(engine, "_MANAGED_CHROME_CREATED_AT_UTC", None), \
             patch.object(engine.socket, "socket", return_value=fake_socket(True)), \
             patch.object(engine, "local_json", return_value={"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/test"}), \
             patch.object(engine, "profile_process_running", return_value=True), \
             patch.object(engine, "_managed_chrome_identity", return_value={"pid": 54321,
                 "created_at_utc": "2026-10-13T07:30:00.000+00:00"}), \
             redirect_stdout(output):
            engine.log_browser_lifecycle_exit()
        record = json.loads(output.getvalue().split(": ", 1)[1])
        self.assertIsNone(record["spawned_pid"])
        self.assertEqual(record["managed_pid"], 54321)
        self.assertEqual(record["managed_created_at_utc"], "2026-10-13T07:30:00.000+00:00")
        self.assertTrue(record["managed_process_running"])

    def test_process_identity_requires_exact_owned_profile_and_port(self):
        created = datetime(2026, 10, 15, 19, 57, 51, 505000, tzinfo=timezone.utc)
        timestamp = created.timestamp()
        profile = Path("C:/managed-profile")
        processes = [
            SimpleNamespace(info={"name": "chrome.exe", "cmdline": [
                f"--remote-debugging-port={engine.PORT}", "--user-data-dir=C:/other-profile"],
                "pid": 1001, "create_time": timestamp}),
            SimpleNamespace(info={"name": "chrome.exe", "cmdline": [
                f"--remote-debugging-port={engine.PORT + 1}", f"--user-data-dir={profile}"],
                "pid": 1002, "create_time": timestamp}),
            SimpleNamespace(info={"name": "chrome.exe", "cmdline": [
                f"--remote-debugging-port={engine.PORT}", f"--user-data-dir={profile}"],
                "pid": 1003, "create_time": timestamp}),
        ]
        with patch.object(engine, "PROFILE", profile), \
             patch("psutil.process_iter", return_value=iter(processes)):
            identity = engine._managed_chrome_identity()
        self.assertEqual(identity, {"pid": 1003,
                                    "created_at_utc": "2026-10-15T19:57:51.505+00:00"})
        self.assertNotIn(str(profile), json.dumps(identity))

    def test_inherited_flags_choose_browser_parent_among_child_processes(self):
        profile = Path("C:/managed-profile")
        args = [f"--remote-debugging-port={engine.PORT}", f"--user-data-dir={profile}"]
        processes = [
            SimpleNamespace(info={"name": "chrome.exe", "cmdline": args,
                                  "pid": 2100, "ppid": 1000, "create_time": 1790000002.0}),
            SimpleNamespace(info={"name": "chrome.exe", "cmdline": args,
                                  "pid": 1000, "ppid": 500, "create_time": 1790000001.0}),
            SimpleNamespace(info={"name": "chrome.exe", "cmdline": args,
                                  "pid": 2200, "ppid": 1000, "create_time": 1790000003.0}),
        ]
        with patch.object(engine, "PROFILE", profile), \
             patch("psutil.process_iter", return_value=iter(processes)):
            identity = engine._managed_chrome_identity()
        self.assertEqual(identity["pid"], 1000)
        self.assertEqual(identity["created_at_utc"],
                         datetime.fromtimestamp(1790000001.0, timezone.utc).isoformat(timespec="milliseconds"))

    def test_multiple_exact_roots_choose_oldest_deterministically(self):
        profile = Path("C:/managed-profile")
        args = [f"--remote-debugging-port={engine.PORT}", f"--user-data-dir={profile}"]
        newer = SimpleNamespace(info={"name": "chrome.exe", "cmdline": args,
                                      "pid": 3000, "ppid": 700, "create_time": 1790000003.0})
        older = SimpleNamespace(info={"name": "chrome.exe", "cmdline": args,
                                      "pid": 2000, "ppid": 600, "create_time": 1790000001.0})
        for processes in ([newer, older], [older, newer]):
            with self.subTest(pids=[row.info["pid"] for row in processes]), \
                 patch.object(engine, "PROFILE", profile), \
                 patch("psutil.process_iter", return_value=iter(processes)):
                self.assertEqual(engine._managed_chrome_identity()["pid"], 2000)


if __name__ == "__main__":
    unittest.main()
