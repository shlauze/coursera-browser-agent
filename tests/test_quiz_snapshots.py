"""Offline regression for the logged late notice; no browser or API access."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "coursera_start.py"
spec = importlib.util.spec_from_file_location("course_quiz_snapshots", SOURCE)
course = importlib.util.module_from_spec(spec)
spec.loader.exec_module(course)
LOG = ROOT / "tests/fixtures/logs/course_v3_20261014_145620_693392/progress.json"
events = json.loads(LOG.read_text(encoding="utf-8"))["events"]
BEFORE, AFTER = [e["result"] for e in events if e["event"] == "quiz_controls" and e["result"].get("controls")]
ITEM = {"key":"assignment:oVRfd"}
SELECTED = [c["id"] for c in BEFORE["controls"] if c["selected"]]


def javascript(*cases):
    payload = {"code":course.QUIZ_UI_JS,"notice":course.AUTHORIZED_QUIZ_NOTICE,"cases":cases}
    result = subprocess.run(["node",str(ROOT / "tests/quiz_snapshot_dom.js")],
        input=json.dumps(payload),capture_output=True,text=True,encoding="utf-8",check=True)
    return json.loads(result.stdout)


class JavascriptFingerprintTests(unittest.TestCase):
    def test_no_visible_dialog_is_reported_as_empty_text(self):
        inspected = javascript({"ui":BEFORE})[0]["result"]
        self.assertEqual(inspected["dialog"], "")

    def test_exact_logged_late_notice_preserves_answer_fingerprint(self):
        before, after = javascript({"ui":BEFORE},{"ui":AFTER})
        self.assertEqual(before["result"]["snapshot_id"], BEFORE["snapshot_id"])
        self.assertEqual(after["result"]["snapshot_id"], AFTER["snapshot_id"])
        self.assertNotEqual(before["result"]["snapshot_id"],after["result"]["snapshot_id"])
        self.assertEqual(before["result"]["answer_snapshot_id"],after["result"]["answer_snapshot_id"])
        self.assertTrue(after["result"]["authorized_notice_present"])
        self.assertEqual(before["clicks"]+after["clicks"],[])

    def test_changed_answer_identity_or_content_changes_fingerprint(self):
        baseline = javascript({"ui":AFTER})[0]["result"]["answer_snapshot_id"]
        variants = []
        for field in ("type","name","value","label","question"):
            changed = copy.deepcopy(AFTER)
            changed["controls"][0][field] += " changed"
            variants.append({"ui":changed})
        changed = copy.deepcopy(AFTER)
        changed["controls"][0],changed["controls"][1] = changed["controls"][1],changed["controls"][0]
        variants.append({"ui":changed})
        for result in javascript(*variants):
            self.assertNotEqual(result["result"]["answer_snapshot_id"],baseline)

    def test_full_stale_guard_rejects_before_click_but_fresh_snapshot_works(self):
        request = {"key":ITEM["key"],"action":"set_answers", "selected":SELECTED,
                   "authorized_notice":course.AUTHORIZED_QUIZ_NOTICE,"snapshot_id":BEFORE["snapshot_id"]}
        stale = javascript({"ui":AFTER,"request":request})[0]
        self.assertEqual(stale["result"]["error_code"],"stale_quiz_snapshot")
        self.assertEqual(stale["clicks"],[])
        request["snapshot_id"] = AFTER["snapshot_id"]
        fresh = javascript({"ui":AFTER,"request":request})[0]
        self.assertTrue(fresh["result"]["verified"])
        self.assertEqual(fresh["clicks"],[])


class ReachedSubmit(Exception):
    pass


class QuizWorkflowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        values = javascript({"ui":BEFORE},{"ui":AFTER})
        cls.before, cls.after = (copy.deepcopy(BEFORE),copy.deepcopy(AFTER))
        for fixture,value in zip((cls.before,cls.after),values):
            fixture["answer_snapshot_id"] = value["result"]["answer_snapshot_id"]

    async def execute(self, stale_count=0, changed_answer=False, partial_error=False, changed_before_submit=False):
        calls = []
        current = copy.deepcopy(self.after)
        applied = 0
        checked = False

        async def evaluate(expression):
            nonlocal applied, checked
            request = json.loads(expression[len(course.QUIZ_UI_JS)+1:-1])
            calls.append(request)
            value = copy.deepcopy(current)
            if changed_answer:
                value["answer_snapshot_id"] = "changed-answer"
            if checked:
                value["controls"][-1]["selected"] = True
            if request["action"] == "set_answers":
                applied += 1
                if applied <= stale_count:
                    return {"error":"stale before clicks","error_code":"stale_quiz_snapshot"}
                if partial_error:
                    return {"error":"The form rerendered while answers were set."}
                value["verified"] = True
            elif request["action"] == "acknowledge_notice":
                checked = True
                value["controls"][-1]["selected"] = True
            return value

        async def submit(ui, label, validator):
            self.assertEqual(label,"Submit")
            value = await ui()
            if changed_before_submit:
                value["answer_snapshot_id"] = "changed-before-submit"
            validator(value)
            raise ReachedSubmit("Offline validation reached Submit; never clicked")

        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(SimpleNamespace(evaluate=evaluate),SimpleNamespace(max_steps=40),Path(directory))
            runner.prepare_quiz = AsyncMock(return_value=copy.deepcopy(self.before))
            runner.solve_quiz_questions = AsyncMock(return_value=SELECTED)
            with patch.object(course,"press_quiz_button_when_ready",side_effect=submit):
                try:
                    await runner.quiz(ITEM)
                except (course.StopRun, ReachedSubmit) as error:
                    return error,calls,runner.solve_quiz_questions.await_count
        self.fail("Workflow did not stop at the offline boundary")

    async def test_delayed_notice_uses_fresh_full_fingerprint_and_reaches_submit_validation(self):
        error,calls,models = await self.execute()
        self.assertIsInstance(error,ReachedSubmit)
        self.assertEqual(models,1)
        answer = next(c for c in calls if c["action"]=="set_answers")
        self.assertEqual(answer["snapshot_id"],self.after["snapshot_id"])
        self.assertTrue(any(c["action"]=="acknowledge_notice" for c in calls))

    async def test_only_preclick_stale_error_retries_without_new_model_call(self):
        error,calls,models = await self.execute(stale_count=2)
        self.assertIsInstance(error,ReachedSubmit)
        self.assertEqual(models,1)
        self.assertEqual(sum(c["action"]=="set_answers" for c in calls),3)

    async def test_stale_retries_are_bounded(self):
        error,calls,_ = await self.execute(stale_count=9)
        self.assertIsInstance(error,course.StopRun)
        self.assertIn("kept changing",str(error))
        self.assertEqual(sum(c["action"]=="set_answers" for c in calls),3)

    async def test_answer_change_replans_from_fresh_form_before_mutation(self):
        error,calls,models = await self.execute(changed_answer=True)
        self.assertIsInstance(error,ReachedSubmit)
        self.assertEqual(models,2)
        self.assertEqual(sum(c["action"]=="set_answers" for c in calls),1)

    async def test_partial_mutation_error_is_never_retried(self):
        error,calls,_ = await self.execute(partial_error=True)
        self.assertIsInstance(error,course.StopRun)
        self.assertEqual(sum(c["action"]=="set_answers" for c in calls),1)
        self.assertFalse(any(c["action"]=="acknowledge_notice" for c in calls))

    async def test_answer_change_before_submission_still_stops(self):
        error,_,_ = await self.execute(changed_before_submit=True)
        self.assertIsInstance(error,course.StopRun)
        self.assertIn("selected answers changed",str(error))


if __name__ == "__main__":
    unittest.main(verbosity=2)
