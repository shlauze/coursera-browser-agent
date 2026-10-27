"""Unchanged review holds avoid browser/model replay across worker restarts."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from v2.control import Control
from v2.discovery import CourseCard
from v2.groq_client import (
    AnswerMemory, AnswerReviewRequired, GroqClient, GroqError,
    ResponseValidationError, answer_review_context, question_key,
)
from v2.outline import ItemRecord, OutlineSnapshot
from v2.runner import Runner, RunnerAttention, course_id, item_id
from v2.store import Store


class ReviewBlockerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.store = Store(self.path)
        self.control = Control(self.store)
        self.control.mark_running()
        self.card = CourseCard("Review course", "Resume", 50, 1, 0)
        self.cid = course_id(self.card)
        self.store.upsert_course(self.cid, self.card.title, status="started")
        self.item = ItemRecord("Quiz", "Graded Assignment", False,
                               "https://www.coursera.org/learn/demo/quiz/a", "Module 1", 0)
        self.question = {"text": "Choose a value", "kind": "single", "options": [
            {"id": "a", "text": "First"}, {"id": "b", "text": "Second"}]}
        self.answer = {"cannot_answer": False, "selected": ["a"]}
        self.runner = Runner(self.store, self.control)

    def fresh_runner(self):
        store = Store(self.path)
        return Runner(store, Control(store))

    def failure(self, result=None, question=None):
        client = GroqClient(self.store, key_loader=lambda: "unused")
        client._request = Mock(return_value=result if result is not None else {"cannot_answer": True})
        with self.assertRaises(AnswerReviewRequired) as raised:
            client.answer(question or self.question)
        return raised.exception

    def retain(self, error=None):
        error = error or self.failure()
        self.runner._retain_item(self.cid, self.item, str(error), "not_submitted",
                                 review_evidence=error.review_evidence)
        return error

    def legacy(self, reason, item=None):
        item = item or self.item
        self.store.set_meta("item_attention:" + item_id(item), {
            "course_id": self.cid, "url": item.url, "reason": reason})

    def process(self, runner, *, completed=False):
        page = Mock(url=self.item.url)
        outline = OutlineSnapshot(self.card.title, (replace(self.item, completed=completed),), page.url)
        with patch("v2.runner.open_course", return_value=page), \
             patch("v2.runner.scan_outline", return_value=outline), \
             patch("v2.runner.open_item") as opened, patch("v2.runner.handle") as handled, \
             patch.object(runner, "_finish_course") as finish:
            if completed:
                runner._process_course(page, self.card)
            else:
                with self.assertRaisesRegex(RunnerAttention, "await learner input"):
                    runner._process_course(page, self.card)
        opened.assert_not_called()
        handled.assert_not_called()
        return finish

    def test_typed_refusal_survives_restart_before_browser_or_new_action(self):
        self.retain()
        runner = self.fresh_runner()
        with patch.object(runner.store, "begin_action", wraps=runner.store.begin_action) as begin:
            self.process(runner).assert_not_called()
        begin.assert_not_called()
        self.assertFalse(self.item.completed)
        self.assertNotEqual(self.store.get_course(self.cid)["status"], "complete")

    def test_numeric_and_rejected_answers_carry_only_question_identity_and_digest(self):
        number = {"text": "Enter a quantity", "kind": "number"}
        numeric = self.failure({"cannot_answer": False, "text": "not numeric"}, number)
        AnswerMemory(self.store).record(self.question, self.answer, earned=0, possible=1)
        rejected = self.failure(self.answer)
        for error, question in ((numeric, number), (rejected, self.question)):
            with self.subTest(reason=str(error)):
                self.assertEqual(set(error.review_evidence), {"kind", "question_key", "context"})
                self.assertEqual(error.review_evidence["question_key"], question_key(question))
                self.assertRegex(error.review_evidence["context"], r"^[a-f0-9]{64}$")
                self.retain(error)
                self.assertTrue(self.fresh_runner()._input_blocked(self.cid, self.item))

    def test_new_reviewed_guidance_releases_only_matching_question(self):
        self.retain()
        self.store.set_meta("answer_guidance:" + "0" * 64,
                            {"source_url": "https://example.test/source", "explanation": "Unrelated"})
        self.assertTrue(self.fresh_runner()._input_blocked(self.cid, self.item))
        self.store.set_meta("answer_guidance:" + question_key(self.question),
                            {"source_url": "https://example.test/source", "explanation": "New evidence"})
        self.assertFalse(self.fresh_runner()._input_blocked(self.cid, self.item))

    def test_new_scored_feedback_releases_hold_without_removing_saved_work(self):
        self.store.set_meta("answer_draft:" + question_key(self.question), {"status": "ungraded_draft"})
        self.retain()
        AnswerMemory(self.store).record(self.question, self.answer, earned=1, possible=1,
                                        feedback="Observed scored result")
        self.assertFalse(self.fresh_runner()._input_blocked(self.cid, self.item))
        self.assertEqual(len(AnswerMemory(self.store).history(self.question)), 1)
        self.assertEqual(self.store.get_meta("answer_draft:" + question_key(self.question)),
                         {"status": "ungraded_draft"})

    def test_duplicate_feedback_ungraded_drafts_model_and_unrelated_metadata_do_not_release(self):
        memory = AnswerMemory(self.store)
        memory.record(self.question, self.answer, earned=0, possible=1, feedback="Observed")
        self.retain()
        memory.record(self.question, self.answer, earned=0, possible=1, feedback="Observed")
        memory.record(self.question, self.answer)
        self.store.set_meta("answer_draft:" + question_key(self.question), {"status": "ungraded_draft"})
        self.store.set_meta("unrelated_setting", "changed")
        runner = self.fresh_runner()
        runner.groq.model = "other-model"
        self.assertTrue(runner._input_blocked(self.cid, self.item))

    def test_guidance_metadata_and_incomplete_guidance_do_not_count_as_evidence(self):
        key = "answer_guidance:" + question_key(self.question)
        self.retain()
        self.store.set_meta(key, {"explanation": "No reviewed source"})
        self.assertTrue(self.fresh_runner()._input_blocked(self.cid, self.item))
        guidance = {"source_url": "https://example.test/source", "explanation": "Reviewed"}
        self.store.set_meta(key, guidance)
        self.retain()
        self.store.set_meta(key, {**guidance, "updated_at": "later"})
        self.assertTrue(self.fresh_runner()._input_blocked(self.cid, self.item))

    def test_legacy_explicit_review_reasons_block_until_explicit_retry(self):
        reasons = (
            "The model could not answer this question reliably",
            "A finite numeric answer is required",
            "Model repeated an answer with observed non-full score for this exact question; submission blocked",
            "File upload or rich-text editor needs a dedicated adapter",
            "Rich-text editor needs a dedicated adapter",
            "Previous failed attempt has incomplete scored feedback; retry blocked",
            "Ambiguous answer options",
        )
        self.store.set_meta("answer_guidance:" + question_key(self.question),
                            {"source_url": "https://example.test/source", "explanation": "New evidence"})
        for reason in reasons:
            with self.subTest(reason=reason):
                self.legacy(reason)
                runner = self.fresh_runner()
                self.assertTrue(runner._input_blocked(self.cid, self.item))
                self.store.set_meta("retry_item:" + item_id(self.item), True)
                self.assertFalse(runner._input_blocked(self.cid, self.item))
                self.store.set_meta("retry_item:" + item_id(self.item), False)

    def test_explicit_retry_is_consumed_and_typed_failure_saved_by_runner(self):
        error = self.retain()
        self.store.set_meta("retry_item:" + item_id(self.item), True)
        runner = self.fresh_runner()
        page = Mock(url=self.item.url)
        outline = OutlineSnapshot(self.card.title, (self.item,), page.url)
        def fail(*args, **kwargs):
            self.assertFalse(self.store.get_meta("retry_item:" + item_id(self.item)))
            self.store.set_meta(f"submission:{kwargs['action_id']}", "not_submitted")
            raise error
        with patch("v2.runner.open_course", return_value=page), \
             patch("v2.runner.scan_outline", return_value=outline), \
             patch("v2.runner.open_item", return_value=page) as opened, \
             patch("v2.runner.handle", side_effect=fail) as handled:
            with self.assertRaisesRegex(RunnerAttention, "await learner input"):
                runner._process_course(page, self.card)
        opened.assert_called_once()
        handled.assert_called_once()
        saved = self.store.get_meta("item_attention:" + item_id(self.item))
        self.assertEqual(saved["review_evidence"], error.review_evidence)
        self.assertEqual(saved["submission_state"], "not_submitted")
        self.assertEqual(self.store.pending_actions(), [])
        self.process(self.fresh_runner())

    def test_pending_submission_remains_isolated_even_with_retry_request(self):
        self.retain()
        action = self.store.begin_action(self.cid, item_id(self.item), "assessment", {"url": self.item.url})
        self.store.set_meta(f"submission:{action}", "submitting")
        self.store.set_meta("retry_item:" + item_id(self.item), True)
        self.process(self.fresh_runner())
        self.assertEqual(self.store.get_action(action)["status"], "pending")
        self.assertEqual(self.store.get_meta(f"submission:{action}"), "submitting")
        self.assertEqual([row["id"] for row in self.store.pending_actions()], [action])
        self.assertTrue(self.store.get_meta("retry_item:" + item_id(self.item)))

    def test_remote_completed_outline_reconciles_review_held_pending_action(self):
        self.retain()
        action = self.store.begin_action(self.cid, item_id(self.item), "assessment", {"url": self.item.url})
        self.store.set_meta(f"submission:{action}", "submitting")
        self.process(self.fresh_runner(), completed=True).assert_called_once()
        self.assertEqual(self.store.get_action(action)["outcome"], "confirmed")
        self.assertEqual(self.store.pending_actions(), [])
        self.assertIsNotNone(self.store.get_meta("item_attention:" + item_id(self.item)))

    def test_timeouts_transport_and_hydration_remain_retryable(self):
        for reason in (
            "Assignment did not reach a recognized state before timeout",
            "Question form changed during the model request",
            "Groq rejected the request: HTTP 503",
            "Network connection timed out", "Assignment control is missing or ambiguous",
        ):
            with self.subTest(reason=reason):
                self.legacy(reason)
                self.assertFalse(self.fresh_runner()._input_blocked(self.cid, self.item))
        client = GroqClient(self.store, key_loader=lambda: "unused")
        client._request = Mock(side_effect=GroqError("Network connection timed out"))
        with self.assertRaises(GroqError) as raised:
            client.answer(self.question)
        self.assertNotIsInstance(raised.exception, AnswerReviewRequired)
        self.assertFalse(hasattr(raised.exception, "review_evidence"))

    def test_malformed_provider_response_is_retryable_validation_failure(self):
        client = GroqClient(self.store, key_loader=lambda: "unused")
        client._request = Mock(return_value={"selected": ["a"]})
        with self.assertRaises(ResponseValidationError) as raised:
            client.answer(self.question)
        self.legacy(str(raised.exception))
        self.assertFalse(self.fresh_runner()._input_blocked(self.cid, self.item))

    def test_new_visual_review_releases_new_hold_but_not_unidentified_legacy_hold(self):
        reason = "Question includes visual content; text-only model cannot answer it"
        self.runner._retain_item(self.cid, self.item, reason, "not_submitted")
        self.assertTrue(self.fresh_runner()._input_blocked(self.cid, self.item))
        self.store.set_meta("visual_reviews:/learn/demo/quiz/a", [{"review": "New visual evidence"}])
        self.assertFalse(self.fresh_runner()._input_blocked(self.cid, self.item))
        self.legacy(reason)
        self.assertTrue(self.fresh_runner()._input_blocked(self.cid, self.item))

    def test_notebook_authorship_holds_are_not_released_by_quiz_authorization(self):
        self.store.set_meta("auto_confirm_coursera", True)
        for reason in (
            "Notebook prepared; personal authorship confirmation requires the learner",
            "Submission requires personal authorship confirmation",
            "Submission dialog requires personal authorship confirmation",
        ):
            with self.subTest(reason=reason):
                self.legacy(reason)
                self.assertTrue(self.fresh_runner()._input_blocked(self.cid, self.item))

    def test_changed_identity_does_not_apply_another_items_review_hold(self):
        self.retain()
        runner = self.fresh_runner()
        self.assertFalse(runner._input_blocked("different-course", self.item))
        other = replace(self.item, url=self.item.url + "other")
        self.assertFalse(runner._input_blocked(self.cid, other))


if __name__ == "__main__":
    unittest.main()
