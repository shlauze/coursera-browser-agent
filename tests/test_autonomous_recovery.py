"""Queue recovery checks with fake Coursera pages and no submissions."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import course_queue as queue
import coursera_start as engine
from course_state import QueueStore


COURSE = "https://www.coursera.org/learn/example-course"
CERTIFICATE = "https://www.coursera.org/account/accomplishments/verify/EXAMPLE"
CONFIG = [{"id":"example-course", "url":COURSE, "title":"Example Course", "modules":[1]}]
ARGS = SimpleNamespace(minutes=1, max_steps=60, quiz_attempts=2, continue_on_attention=False)


def make_store(directory):
    return QueueStore(Path(directory) / "state.json", copy.deepcopy(CONFIG))


def item():
    return {"key":"supplement:intro", "title":"Introduction Reading",
            "url":COURSE + "/supplement/intro/introduction"}


async def mark_module_complete(runner, number, roster):
    record = {"verified_complete":True, "roster":roster,
              "items":{row["key"]:{"title":row["title"], "verified_complete":True} for row in roster}}
    runner.checkpoint["modules"][str(number)] = record
    runner.data["modules"][str(number)] = record
    runner.update_counts()


class AutonomousRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        engine.configure_course("https://www.coursera.org/learn/philosophy")

    async def test_course_budget_moves_only_after_confirmed_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            store = make_store(directory)
            runner = queue.QueueRunner(SimpleNamespace(), copy.copy(ARGS),
                                       Path(directory), store, store.course("example-course"))
            timeout = Mock()
            runner._course_timeout = timeout
            runner.checkpoint["modules"]["1"] = {
                "verified_complete":False,
                "items":{item()["key"]:{"verified_complete":True}}}
            runner.update_counts()
            timeout.reschedule.assert_called_once()
            self.assertGreater(timeout.reschedule.call_args.args[0],
                               asyncio.get_running_loop().time())
            runner.update_counts()
            timeout.reschedule.assert_called_once()

    async def test_transient_roster_failure_recovers_and_still_requires_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = make_store(directory)
            tab = SimpleNamespace()
            async def finish_course(_tab, _url, _modules, _rosters):
                store.course("example-course")["certificate_page_url"] = CERTIFICATE
                return {"verified":True, "evidence":["Course complete"]}
            async def verify_certificate(_tab, url, identity, title=None):
                self.assertEqual((url, identity), (CERTIFICATE, "example-course"))
                return {"verified":True, "method":"certificate",
                        "course_id":identity, "url":url, "evidence":["Completed by Learner"]}
            with patch.object(queue, "ROOT", Path(directory)), \
                 patch.object(queue, "discover_modules", AsyncMock(return_value=[1])), \
                 patch.object(queue, "discover_roster", AsyncMock(side_effect=[
                     TimeoutError("temporary browser read"), [item()]])) as roster, \
                 patch.object(queue.QueueRunner, "process_module", new=mark_module_complete), \
                 patch.object(queue, "verify_course_completion", new=finish_course), \
                 patch.object(queue, "observed_certificate_url", AsyncMock(return_value=None)), \
                 patch.object(queue, "open_certificate_page", AsyncMock(return_value=CERTIFICATE)), \
                 patch.object(queue, "verify_saved_certificate", new=verify_certificate):
                result = await queue.run_queue(store, copy.copy(ARGS), tab)
            self.assertEqual(result, 0)
            self.assertEqual(roster.await_count, 2)
            course = store.course("example-course")
            self.assertEqual(course["status"], "completed")
            self.assertEqual(course["completion_method"], "certificate")
            self.assertEqual(course["checkpoint"]["read_recovery"]["discover_roster"],
                             {"retries":1, "recoveries":1})

    async def test_unknown_course_error_keeps_queue_stopped_and_writes_private_incident(self):
        with tempfile.TemporaryDirectory() as directory:
            store = make_store(directory)
            private = "gsk_private123 Question: personal assessment text"
            with patch.object(queue, "ROOT", Path(directory)), \
                 patch.object(queue, "discover_modules", AsyncMock(
                     side_effect=RuntimeError(private))) as modules, \
                 patch.object(queue, "discover_roster", AsyncMock()) as roster, \
                 patch.object(queue, "observed_certificate_url", AsyncMock(return_value=None)):
                result = await queue.run_queue(store, copy.copy(ARGS), SimpleNamespace())
            self.assertEqual(result, 2)
            modules.assert_awaited_once()
            roster.assert_not_awaited()
            self.assertEqual(store.course("example-course")["status"], "attention")
            incidents = list((Path(directory) / "logs").glob("queue_*/incident.json"))
            self.assertEqual(len(incidents), 1)
            raw = incidents[0].read_text(encoding="utf-8")
            self.assertNotIn("gsk_private123", raw)
            self.assertNotIn("personal assessment text", raw)
            report = json.loads(raw)
            self.assertEqual(report["operation"], "discover_modules")
            self.assertEqual(report["error_class"], "RuntimeError")

    async def test_exhausted_read_retry_requests_supervisor_resume_of_same_course(self):
        with tempfile.TemporaryDirectory() as directory:
            store = make_store(directory)
            with patch.object(queue, "ROOT", Path(directory)), \
                 patch.object(queue, "discover_modules", AsyncMock(
                     side_effect=TimeoutError("Chrome read timed out"))) as modules, \
                 patch.object(queue, "discover_roster", AsyncMock()) as roster, \
                 patch.object(queue, "observed_certificate_url", AsyncMock(return_value=None)):
                result = await queue.run_queue(store, copy.copy(ARGS), SimpleNamespace())
            self.assertEqual(result, 3)
            self.assertEqual(modules.await_count, 3)
            roster.assert_not_awaited()
            self.assertEqual(store.course("example-course")["status"], "attention")
            incidents = list((Path(directory) / "logs").glob("queue_*/incident.json"))
            self.assertEqual(len(incidents), 1)
            self.assertEqual(json.loads(incidents[0].read_text(encoding="utf-8"))["retry_code"],
                             "timeout")


if __name__ == "__main__":
    unittest.main()
