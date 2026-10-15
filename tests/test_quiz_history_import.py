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


def short_text_attempt(answer="Dropout", *, text_control="c0", key=KEY):
    stem = "Name one way to reduce overfitting."
    number_stem = "How many letters are in CNN?"
    return [
        {"event":"quiz_questions", "key":key, "questions":[
            {"id":"q1", "text":"1. Question 1 " + stem, "selection":"short_text",
             "control_id":text_control, "options":[], "min_length":"1", "max_length":"100"},
            {"id":"q2", "text":"2. Question 2 " + number_stem, "selection":"number",
             "control_id":"c1", "options":[]},
            {"id":"q3", "text":"3. Question 3 " + STEM, "selection":"one", "options":[
                {"id":"c2", "text":"A"}, {"id":"c3", "text":"B"}]}]},
        {"event":"quiz_answer_plan", "key":key, "plan":{"cannot_answer":False, "answers":[
            {"question_id":"q1", "selected_controls":[], "numeric_answer":"",
             "text_answer":answer, "reason":"Example"},
            {"question_id":"q2", "selected_controls":[], "numeric_answer":"3",
             "text_answer":"", "reason":"Example"},
            {"question_id":"q3", "selected_controls":["c2"], "numeric_answer":"",
             "text_answer":"", "reason":"Example"}]}},
        {"event":"quiz_submitted_result", "key":key, "question_feedback":[
            {"question_number":1, "text":"Question 1\n" + stem + "\n" + answer + "\n1 / 1 point"},
            {"question_number":2, "text":"Question 2\n" + number_stem + "\n3\n1 / 1 point"},
            {"question_number":3, "text":"Question 3\n" + STEM + "\nA\n1 / 1 point"}]},
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

    def test_short_text_answers_import_with_numeric_and_choice_feedback(self):
        self.write_events(short_text_attempt())
        history = {}
        self.assertEqual(importer.import_previous_history(self.root, self.run, history), 1)
        questions = history[KEY][0]["questions"]
        self.assertEqual([question["selection"] for question in questions],
                         ["short_text", "number", "one"])
        self.assertEqual([question["selected_answers"] for question in questions],
                         [["Dropout"], ["3"], ["A"]])
        self.assertTrue(all(question["full_score"] for question in questions))
        self.assertNotIn('"control_id"', json.dumps(history))

    def test_short_text_history_matches_question_after_control_id_changes(self):
        first = short_text_attempt("Dropout", text_control="old-control")
        second = short_text_attempt("Weight decay", text_control="new-control")
        self.write_events(first + second)
        history = {}
        self.assertEqual(importer.import_previous_history(self.root, self.run, history), 2)
        from quiz_memory import context_for_questions
        context = context_for_questions(history[KEY], second[0]["questions"])
        text_entry = next(entry for entry in context if entry["selection"] == "short_text")
        self.assertEqual([record["selected_answers"] for record in text_entry["attempts"]],
                         [["Dropout"], ["Weight decay"]])

    def test_invalid_short_text_plans_do_not_import_partial_history(self):
        variants = []
        missing = short_text_attempt()
        missing[1]["plan"]["answers"][0].pop("text_answer")
        variants.append(missing)
        whitespace = short_text_attempt("  Dropout  ")
        variants.append(whitespace)
        overlong = short_text_attempt("x" * 4001)
        variants.append(overlong)
        below_minimum = short_text_attempt("A")
        below_minimum[0]["questions"][0]["min_length"] = "2"
        variants.append(below_minimum)
        duplicate_stem = short_text_attempt()
        duplicate_stem[0]["questions"][1]["text"] = duplicate_stem[0]["questions"][0]["text"]
        variants.append(duplicate_stem)
        contaminated_number = short_text_attempt()
        contaminated_number[1]["plan"]["answers"][1]["text_answer"] = "wrong"
        variants.append(contaminated_number)
        lone_surrogate = short_text_attempt("\ud800")
        variants.append(lone_surrogate)
        for events in variants:
            with self.subTest(variant=events[1]["plan"]["answers"][0].get("text_answer")):
                self.write_events(events)
                history = {}
                self.assertEqual(importer.import_previous_history(self.root, self.run, history), 0)
                self.assertEqual(history, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
