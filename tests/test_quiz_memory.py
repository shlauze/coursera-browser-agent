"""Text matching/history regression tests; no browser, API, or course answers."""
import copy
import json
from pathlib import Path
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import quiz_memory as memory


def question(stem="Which symbol is named alpha?", identity="q1", options=None, selection="one"):
    return {"id": identity, "text": stem, "selection": selection,
            "options": options or [{"id": identity + "a", "text": "Alpha"}, {"id": identity + "b", "text": "Beta"}]}


def feedback(q, score="1 / 1 point", number=1, extra=""):
    return {"question_number": number, "text": f"Question {number}\n{q['text']}\n{extra}\n{score}"}


def short_text_question(stem="Name one way to reduce overfitting.", identity="field-1"):
    return {"id": "q1", "text": stem, "selection": "short_text",
            "control_id": identity, "options": [], "min_length": 1, "max_length": 500}


class QuizMemoryTests(unittest.TestCase):
    def test_short_text_history_preserves_internal_spaces_exactly(self):
        q = short_text_question()
        answer = "weight  decay"
        history = memory.record_attempt([], [q], [], [feedback(q)],
                                        text_answers={"field-1": answer})
        self.assertEqual(history[0]["questions"][0]["selected_answers"], [answer])
        context = memory.context_for_questions(history, [short_text_question(identity="new-field")])
        self.assertEqual(context[0]["attempts"][0]["selected_answers"], [answer])
        self.assertTrue(context[0]["attempts"][0]["full_score"])

    def test_short_text_history_rejects_control_characters_without_normalizing(self):
        q = short_text_question()
        for answer in ("weight\n decay", "weight\x00decay", "weight\x7fdecay"):
            with self.subTest(answer=repr(answer)):
                self.assertEqual(memory.record_attempt([], [q], [], [],
                                 text_answers={"field-1": answer}), [])
                history = memory.record_attempt([], [q], [], [feedback(q)],
                                                text_answers={"field-1": "weight decay"})
                history[0]["questions"][0]["selected_answers"] = [answer]
                self.assertEqual(memory.context_for_questions(history, [q]), [])

    def test_short_text_answer_matches_stem_after_control_id_changes(self):
        original = short_text_question(identity="old-field")
        history = memory.record_attempt([], [original], [], [feedback(original)],
                                        text_answers={"old-field": "Dropout"})
        self.assertEqual(history[0]["questions"][0]["selected_answers"], ["Dropout"])
        current = short_text_question(identity="new-field")
        context = memory.context_for_questions(history, [current])
        self.assertEqual(context[0]["selection"], "short_text")
        self.assertEqual(context[0]["attempts"][0]["selected_answers"], ["Dropout"])
        self.assertTrue(context[0]["attempts"][0]["full_score"])
        self.assertNotIn("old-field", json.dumps(context))
        self.assertEqual(memory.context_for_questions(history,
                         [short_text_question("Name a different strategy.")]), [])
        self.assertEqual(memory.context_for_questions(history,
                         [{"text": original["text"], "selection": "number", "options": []}]), [])

    def test_short_text_answer_coexists_with_choices_and_numbers(self):
        choice = question()
        numeric = {"id": "n", "text": "How many layers?", "selection": "number",
                   "control_id": "n-field", "options": []}
        free = short_text_question(identity="text-field")
        history = memory.record_attempt([], [choice, numeric, free], ["q1a"],
                                        [feedback(choice), feedback(numeric, number=2),
                                         feedback(free, number=3)],
                                        numeric_answers={"n-field": "3"},
                                        text_answers={"text-field": "Use dropout."})
        entries = history[0]["questions"]
        self.assertEqual([entry["selection"] for entry in entries], ["one", "number", "short_text"])
        self.assertEqual([entry["selected_answers"] for entry in entries],
                         [["Alpha"], ["3"], ["Use dropout."]])

    def test_invalid_short_text_mapping_and_saved_answers_are_ignored(self):
        q = short_text_question()
        for answers in ({"wrong-control": "Dropout"}, {"field-1": " "},
                        {"field-1": " Dropout"}, {"field-1": "Dropout "},
                        {"field-1": "Drop\ud800out"},
                        {"field-1": "x" * (memory.MAX_TEXT_ANSWER_CHARS + 1)},
                        {"field-1": 123}, []):
            self.assertEqual(memory.record_attempt([], [q], [], [], text_answers=answers), [])
        history = memory.record_attempt([], [q], [], [feedback(q)],
                                        text_answers={"field-1": "Dropout"})
        corrupt = copy.deepcopy(history)
        corrupt[0]["questions"][0]["selected_answers"] = ["x" * (memory.MAX_TEXT_ANSWER_CHARS + 1)]
        self.assertEqual(memory.context_for_questions(corrupt, [q]), [])
        self.assertEqual(history[0]["questions"][0]["selected_answers"], ["Dropout"])

    def test_shuffled_ids_options_and_questions_match_text_only(self):
        a, b = question(), question("Which symbol is named gamma?", "q2")
        history = memory.record_attempt([], [a, b], ["q1a", "q2b"], [feedback(a), feedback(b, "0 / 1 point", 2)])
        current_b = question(b["text"], "new1", [{"id": "x", "text": "Beta"}, {"id": "y", "text": "Alpha"}])
        current_a = question(a["text"], "new2", [{"id": "z", "text": "Beta"}, {"id": "w", "text": "Alpha"}])
        context = memory.context_for_questions(history, [current_b, current_a])
        self.assertEqual(context[0]["question"], b["text"])
        self.assertEqual(context[0]["attempts"][0]["selected_answers"], ["Beta"])
        self.assertTrue(context[1]["attempts"][0]["full_score"])
        serialized = json.dumps(context)
        for old_id in ("q1a", "q2b", "q1", "q2"):
            self.assertNotIn(old_id, serialized)

    def test_feedback_numbers_cannot_override_stems(self):
        a, b = question(), question("Which symbol is named gamma?", "q2")
        history = memory.record_attempt([], [a, b], ["q1a", "q2b"], [feedback(a, number=99), feedback(b, "0 / 1 point", 1)])
        self.assertTrue(history[0]["questions"][0]["full_score"])
        self.assertFalse(history[0]["questions"][1]["full_score"])
        unknown = memory.record_attempt([], [a], ["q1a"], [{"question_number": 1, "text": "Question 1\nNice work\n1 / 1 point"}])
        self.assertEqual(unknown[0]["questions"][0]["feedback"], "")

    def test_ambiguous_feedback_blocks_fail_closed(self):
        a, b = question(), question("Which symbol is named gamma?", "q2")
        ambiguous = {"text": a["text"] + "\n" + b["text"] + "\n1 / 1 point"}
        history = memory.record_attempt([], [a, b], ["q1a", "q2a"], [ambiguous])
        self.assertTrue(all(not q["feedback"] for q in history[0]["questions"]))
        duplicate = memory.record_attempt([], [a], ["q1a"], [feedback(a), feedback(a)])
        self.assertFalse(duplicate[0]["questions"][0]["full_score"])

    def test_changed_options_stem_or_selection_omit_memory(self):
        original = question()
        history = memory.record_attempt([], [original], ["q1a"], [feedback(original)])
        changed_option = question(options=[{"id": "x", "text": "Alpha"}, {"id": "y", "text": "Delta"}])
        for current in (changed_option, question("Another question?"), question(selection="many")):
            self.assertEqual(memory.context_for_questions(history, [current]), [])

    def test_duplicate_option_labels_and_duplicate_stems_omit_memory(self):
        duplicate = question(options=[{"id": "a", "text": "Alpha"}, {"id": "b", "text": " ALPHA "}])
        self.assertEqual(memory.record_attempt([], [duplicate], ["a"], []), [])
        q = question()
        history = memory.record_attempt([], [q], ["q1a"], [feedback(q)])
        self.assertEqual(memory.context_for_questions(history, [q, copy.deepcopy(q)]), [])
        self.assertEqual(memory.context_for_questions(history, [duplicate]), [])

    def test_nice_work_and_per_option_scores_do_not_prove_full_score(self):
        q = question(selection="many")
        for block in ("Nice work", "Nice work\n1 / 1 point\nAnother option is incorrect", "Correct", "1 / 0 points", "2 / 1 points"):
            history = memory.record_attempt([], [q], ["q1a", "q1b"], [feedback(q, block)])
            self.assertFalse(history[0]["questions"][0]["full_score"])

    def test_last_score_line_classifies_partial_vs_full(self):
        q = question(selection="many")
        partial = memory.record_attempt([], [q], ["q1a"], [feedback(q, "Nice work\n1 / 1 point\n0.5 / 1 point\n2.")])
        item = partial[0]["questions"][0]
        self.assertFalse(item["full_score"])
        self.assertEqual(item["score"], {"earned": .5, "possible": 1.0})
        full = memory.record_attempt([], [q], ["q1a", "q1b"], [feedback(q, "0 / 1 point\n1,5 / 1,5 points")])
        self.assertTrue(full[0]["questions"][0]["full_score"])

    def test_latest_two_and_previous_full_score_preserved(self):
        q = question()
        history = memory.record_attempt([], [q], ["q1a"], [feedback(q)])
        for _ in range(12):
            history = memory.record_attempt(history, [q], ["q1b"], [feedback(q, "0 / 1 point")])
        self.assertLessEqual(len(history), 8)
        context = memory.context_for_questions(history, [q])
        attempts = context[0]["attempts"]
        self.assertEqual(len(attempts), 8)
        self.assertTrue(attempts[0]["full_score"])
        self.assertEqual(attempts[0]["selected_answers"], ["Alpha"])
        self.assertEqual([v["attempts_ago"] for v in attempts[-2:]], [1, 0])
        self.assertEqual(sum("feedback" in value for value in attempts), 3)

    def test_all_recent_incorrect_answer_sets_remain_in_compact_context(self):
        q = question(selection="many")
        history = []
        choices = [["q1a"], ["q1b"], ["q1a", "q1b"]]
        for i in range(8):
            history = memory.record_attempt(history, [q], choices[i % 3], [feedback(q, "0 / 1 point")])
        attempts = memory.context_for_questions(history, [q])[0]["attempts"]
        self.assertEqual(len(attempts), 8)
        self.assertEqual({tuple(a["selected_answers"]) for a in attempts}, {("Alpha",), ("Beta",), ("Alpha", "Beta")})
        self.assertEqual(sum("feedback" in value for value in attempts), 2)

    def test_history_and_context_are_bounded(self):
        questions = [question("Unique question number " + str(i) + "?", f"q{i}") for i in range(60)]
        selected = [q["options"][0]["id"] for q in questions]
        history = []
        for _ in range(10):
            records = [feedback(q, extra="Long feedback explanation. " * 500) for q in questions]
            history = memory.record_attempt(history, questions, selected, records)
        self.assertLessEqual(len(history), 8)
        for attempt in history:
            self.assertLessEqual(memory._size(attempt), memory.MAX_ATTEMPT_CHARS)
        context = memory.context_for_questions(history, questions)
        self.assertTrue(context)
        self.assertLessEqual(memory._size(context), 16_000)
        self.assertTrue(context[0]["attempts"][0]["full_score"])

    def test_input_objects_unchanged_and_invalid_ids_ignored(self):
        q = question()
        before = copy.deepcopy(q)
        history = memory.record_attempt([], [q], ["q1a"], [feedback(q)])
        saved = copy.deepcopy(history)
        self.assertEqual(memory.record_attempt(history, [q], ["old_id"], []), saved)
        self.assertEqual(q, before)
        self.assertEqual(history, saved)

    def test_unicode_and_whitespace_normalization(self):
        q = question("What does “alpha” mean?")
        history = memory.record_attempt([], [q], ["q1a"], [{"text": 'What does "alpha" mean?\n1 / 1 point'}])
        current = question(' What   does "alpha" mean? ')
        self.assertTrue(memory.context_for_questions(history, [current])[0]["attempts"][0]["full_score"])

    def test_real_form_prefixes_and_shuffled_question_numbers(self):
        q = question("1. Question 1 Which letter comes first?")
        records = [{"question_number": 1, "text": "Question 1\nWhich letter comes first?\nAlpha\nBeta\n1 / 1 point"}]
        history = memory.record_attempt([], [q], ["q1a"], records)
        self.assertTrue(history[0]["questions"][0]["full_score"])
        self.assertEqual(history[0]["questions"][0]["text"], "Which letter comes first?")
        for prefix in ("5. Question 5 ", "Question 7 ", "9. ", ""):
            current = question(prefix + "Which letter comes first?", identity="new")
            self.assertTrue(memory.context_for_questions(history, [current])[0]["attempts"][0]["full_score"])

    def test_feedback_crop_cannot_invent_final_score_line(self):
        q = question()
        malformed = "x" * 6_000 + "1 / 1 point"
        history = memory.record_attempt([], [q], ["q1a"], [feedback(q, malformed)])
        self.assertFalse(history[0]["questions"][0]["full_score"])
        self.assertFalse(memory.context_for_questions(history, [q])[0]["attempts"][0]["full_score"])

    def test_untrusted_saved_full_score_flag_not_accepted_without_score(self):
        q = question()
        history = memory.record_attempt([], [q], ["q1a"], [feedback(q, "Nice work")])
        history[0]["questions"][0]["full_score"] = True
        history[0]["questions"][0]["score"] = {"earned": 1, "possible": 1}
        self.assertFalse(memory.context_for_questions(history, [q])[0]["attempts"][0]["full_score"])

    def test_missing_feedback_and_corrupt_history_are_safe(self):
        q = question()
        history = memory.record_attempt({"bad": "format"}, [q], ["q1a"], None)
        self.assertFalse(history[0]["questions"][0]["full_score"])
        self.assertIsNone(history[0]["questions"][0]["score"])
        self.assertEqual(memory.record_attempt(history, [], [], [], limit=0), [])
        self.assertEqual(memory.context_for_questions([None, {}], [q]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
