"""Prior-log backfill tests; all files are temporary, no browser or API calls."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import quiz_history_import as importer


KEY = "assignment:example"
STEM = "Which letter comes first?"


def attempt(selected="B", reordered=False, key=KEY):
    options = [{"id":"c8", "text":"B"}, {"id":"c9", "text":"A"}] if reordered else [
        {"id":"c0", "text":"A"}, {"id":"c1", "text":"B"}]
    control = next(option["id"] for option in options if option["text"] == selected)
    score = "1 / 1 point" if selected == "A" else "0 / 1 point"
    return [
        {"event":"quiz_questions", "key":key, "questions":[
            {"id":"q1", "text":"1. Question 1 " + STEM, "selection":"one", "options":options}]},
        {"event":"quiz_answer_plan", "key":key, "plan":{"cannot_answer":False, "answers":[
            {"question_id":"q1", "selected_controls":[control], "reason":"Example"}]}},
        {"event":"quiz_submitted_result", "key":key, "attempt":98, "passed":selected == "A",
         "count":999, "api_key":"TEST_SECRET_NOT_FOR_HISTORY", "question_feedback":[
            {"question_number":1, "text":"Question 1\n" + STEM + "\nA\nB\n" + score}]},
    ]


class HistoryImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.run = self.root / "logs/prior-run"
        self.run.mkdir(parents=True)

    def write_events(self, events, destination=None):
        path = (destination or self.run) / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(event) if not isinstance(event, str) else event
                                  for event in events) + "\n", encoding="utf-8")

    def test_two_attempts_keep_semantic_choices_across_reordered_controls_without_counts(self):
        self.write_events(attempt("B") + attempt("A", reordered=True))
        history = {}
        count = importer.import_previous_history(self.root, self.run, history)
        self.assertEqual(count, 2)
        self.assertEqual(len(history[KEY]), 2)
        first, second = [value["questions"][0] for value in history[KEY]]
        self.assertEqual(first["selected_answers"], ["B"])
        self.assertEqual(second["selected_answers"], ["A"])
        self.assertFalse(first["full_score"])
        self.assertTrue(second["full_score"])
        serialized = json.dumps(history)
        self.assertNotIn("TEST_SECRET_NOT_FOR_HISTORY", serialized)
        self.assertNotIn('"count"', serialized)
        self.assertNotIn('"attempt": 98', serialized)

    def test_existing_nonempty_history_is_untouched_and_restart_does_not_duplicate(self):
        self.write_events(attempt("B") + attempt("A", reordered=True))
        history = {KEY:[{"existing":True}], "assignment:other":[{"other":True}]}
        original = copy.deepcopy(history)
        self.assertEqual(importer.import_previous_history(self.root, self.run, history), 0)
        self.assertEqual(history, original)
        fresh = {KEY:[]}
        self.assertEqual(importer.import_previous_history(self.root, self.run, fresh), 2)
        saved = copy.deepcopy(fresh)
        self.assertEqual(importer.import_previous_history(self.root, self.run, fresh), 0)
        self.assertEqual(fresh, saved)

    def test_progress_fallback_only_when_events_file_is_missing(self):
        progress = self.run / "progress.json"
        progress.write_text(json.dumps({"events":attempt("A")}), encoding="utf-8")
        history = {}
        self.assertEqual(importer.import_previous_history(self.root, self.run, history), 1)
        self.write_events([])
        fresh = {}
        self.assertEqual(importer.import_previous_history(self.root, self.run, fresh), 0)
        self.assertEqual(fresh, {})

    def test_outside_missing_oversized_and_invalid_sources_are_ignored(self):
        outside = self.base / "outside"
        self.write_events(attempt("A"), outside)
        history = {}
        for path in (outside, self.root / "logs/../../outside", self.run / "missing", None):
            with self.subTest(path=path):
                self.assertEqual(importer.import_previous_history(self.root, path, history), 0)
        self.write_events(attempt("A"))
        with patch.object(importer, "MAX_LOG_BYTES", 30):
            self.assertEqual(importer.import_previous_history(self.root, self.run, history), 0)
        (self.run / "events.jsonl").unlink()
        (self.run / "progress.json").write_text("not JSON", encoding="utf-8")
        self.assertEqual(importer.import_previous_history(self.root, self.run, history), 0)
        self.assertEqual(history, {})

    def test_malformed_line_invalidates_pending_plan_before_next_fresh_attempt(self):
        first = attempt("B")
        self.write_events(first[:2] + ['{unfinished JSON'] + first[2:] + attempt("A", reordered=True))
        history = {}
        self.assertEqual(importer.import_previous_history(self.root, self.run, history), 1)
        self.assertEqual(history[KEY][0]["questions"][0]["selected_answers"], ["A"])

    def test_invalid_or_missing_sequences_never_guess_selected_answers(self):
        bad_plan = attempt("A")
        bad_plan[1]["plan"]["answers"][0]["selected_controls"] = ["not-an-option"]
        bad_questions = attempt("A")
        bad_questions[0]["questions"][0]["options"][1]["id"] = "c0"
        empty_feedback = attempt("A")
        empty_feedback[2]["question_feedback"] = []
        for events in (attempt("A")[1:], attempt("A")[:2], bad_plan, bad_questions, empty_feedback):
            with self.subTest(events=events):
                self.write_events(events)
                history = {}
                self.assertEqual(importer.import_previous_history(self.root, self.run, history), 0)
                self.assertEqual(history, {})

    def test_duplicate_result_is_consumed_once_and_payload_paths_are_never_followed(self):
        events = attempt("A")
        events[2]["feedback_file"] = str(self.base / "outside/credential.json")
        self.write_events(events + [copy.deepcopy(events[2])])
        history = {}
        self.assertEqual(importer.import_previous_history(self.root, "logs/prior-run", history), 1)
        self.assertEqual(len(history[KEY]), 1)

    def test_interleaved_quizzes_keep_their_own_question_and_plan_associations(self):
        first, second = attempt("A"), attempt("B", key="assignment:other")
        self.write_events(first[:1] + second[:2] + first[1:] + second[2:])
        history = {}
        self.assertEqual(importer.import_previous_history(self.root, self.run, history), 2)
        self.assertEqual(history[KEY][0]["questions"][0]["selected_answers"], ["A"])
        self.assertEqual(history["assignment:other"][0]["questions"][0]["selected_answers"], ["B"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
