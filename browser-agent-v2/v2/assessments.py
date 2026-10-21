"""Observed-form adapter. Unknown editors and uncertain submissions are deferred."""
from __future__ import annotations

from pathlib import Path
import re
import time
from contextvars import ContextVar

_CONFIRMATION = ContextVar("assessment_confirmation", default=None)

from .groq_client import GroqError, question_key, validate_answer
from .handlers import HandlerError, HandlerResult, _click_one_of
from .ui import dismiss_learning_overlay


DOM = (Path(__file__).with_name("assessment_dom.js")).read_text(encoding="utf-8")


def inspect(page):
    result = page.evaluate(DOM, {"action": "inspect"})
    while result.get("attestation"):
        callback = _CONFIRMATION.get()
        if callback is None:
            raise HandlerError("Personal authorship confirmation requires the learner")
        callback(page)
        result = page.evaluate(DOM, {"action": "inspect"})
    if result.get("error"):
        raise HandlerError(result["error"])
    return result


def act(page, snapshot, **request):
    inspect(page)  # A late notice must pause before any mutation.
    result = page.evaluate(DOM, {"snapshot": snapshot["snapshot"], "expected_path": snapshot["path"], **request})
    if not result.get("ok"):
        raise HandlerError(result.get("error", "Assignment action was not confirmed"))


def _wait(page, checkpoint, predicate, *, seconds=25):
    deadline = time.monotonic() + seconds
    last_error = None
    previous_snapshot = None
    while time.monotonic() < deadline:
        checkpoint()
        dismiss_learning_overlay(page)
        inspected_at = time.monotonic()
        try:
            value = inspect(page)
            deadline += time.monotonic() - inspected_at
        except HandlerError as exc:
            # React can mount inputs before their labels/formulas arrive.
            # Never use partial choices; retry only extraction failures here.
            if str(exc) not in {"Ambiguous answer options", "Question text is missing",
                                "Cannot associate every answer field with a question"}:
                raise
            last_error = str(exc)
            previous_snapshot = None
            page.wait_for_timeout(500)
            continue
        if predicate(value):
            if not value.get("questions") or value.get("snapshot") == previous_snapshot:
                return value
            previous_snapshot = value.get("snapshot")
        else:
            previous_snapshot = None
        last_error = None
        page.wait_for_timeout(500)
    raise HandlerError(last_error or "Assignment did not reach a recognized state before timeout")


def _entry_names(snapshot):
    return [name for name in snapshot.get("buttons", []) if re.fullmatch(
        r"(?:Start|Resume) (?:assignment|quiz|attempt)|Retry(?: assignment)?|Try again", name, re.I)]


def _confirmation_observed(page, checkpoint, *, attempts=5):
    """Read the current mounted form; never click a stale/replacement checkbox."""
    for index in range(attempts):
        checkpoint()
        state = page.evaluate(DOM, {"action": "inspect"})
        if not state.get("error") and state.get("attestation") is False:
            return True
        if index + 1 < attempts:
            page.wait_for_timeout(400)
    return False


def handle_assessment(page, checkpoint, *, groq, store, action_id, max_attempts=3):
    from .control import Control
    def wait_for_learner(current_page):
        checkpoint()
        path = current_page.url
        if _confirmation_observed(current_page, checkpoint, attempts=1):
            return
        if store.get_meta("auto_confirm_coursera", False):
            from urllib.parse import urlsplit
            result = current_page.evaluate(DOM, {"action":"confirm_authorship", "expected_path":urlsplit(path).path})
            if result.get("ok"):
                store.record_event("INFO", "CONFIRMATION_AUTOMATIC", "Coursera checkbox confirmed under the user's explicit authorization")
                return
            if _confirmation_observed(current_page, checkpoint):
                store.record_event("INFO", "CONFIRMATION_OBSERVED", "Coursera confirmation observed after form rerender")
                return
        notice = {"reason": "Подтверждение Coursera не распознано или не принято. Проверьте форму перед продолжением.",
                  "item": store.get_meta("current_item", "Квиз"), "url": path}
        store.set_meta("confirmation_required", notice)
        store.record_event("WARNING", "CONFIRMATION_REQUIRED", notice["reason"])
        Control(store).request_pause()
        checkpoint()  # Cooperative wait: Stop and Skip remain runner controls.
        if current_page.url != path:
            raise HandlerError("Assignment page changed while waiting for confirmation")
        if not current_page.evaluate(DOM, {"action": "inspect"}).get("attestation"):
            store.set_meta("confirmation_required", None)
            store.record_event("INFO", "CONFIRMATION_OBSERVED", "Learner confirmation observed on Coursera; continuing the same assessment")
    enabled = store is not None and store.get_control_state() in {"running", "paused"}
    token = _CONFIRMATION.set(wait_for_learner if enabled else None)
    try:
        return _handle_assessment(page, checkpoint, groq=groq, store=store,
                                  action_id=action_id, max_attempts=max_attempts)
    finally:
        _CONFIRMATION.reset(token)
        if enabled:
            store.set_meta("confirmation_required", None)


def _handle_assessment(page, checkpoint, *, groq, store, action_id, max_attempts=3):
    if groq is None or store is None or action_id is None:
        raise HandlerError("Assignment service is not configured")
    submission_key = f"submission:{action_id}"
    if store.get_meta(submission_key) in {"submitting", "passed", "pending"}:
        raise HandlerError("Previous submission must be reconciled before another attempt")
    store.set_meta(submission_key, "not_submitted")
    for attempt in range(max_attempts):
        checkpoint()
        current = _wait_entry(page, checkpoint)
        if current.get("start_confirmation") or current.get("retry_confirmation"):
            # Restore the landing page first to inspect the actual previous result.
            act(page, current, action="button", label="Cancel")
            current = _wait_entry(page, checkpoint)
        if current.get("passed") and not current.get("questions"):
            return _return_to_outline(page, current, checkpoint)
        if not current.get("questions"):
            if current.get("failed") and attempt == 0:
                review = _read_failed_feedback(page, current, checkpoint)
                rows = review.get("feedback_questions", [])
                if not rows or any(not _scored_feedback(row) for row in rows):
                    raise HandlerError("Previous failed attempt has incomplete scored feedback; retry blocked")
                _recover_feedback_answers(rows, groq.memory)
                store.set_meta(f"feedback_review:{action_id}", {"status":"existing_failure_reviewed", "questions":rows})
                store.record_event("INFO", "FEEDBACK_REVIEWED", "Existing failed attempt inspected before opening a new attempt")
                base = re.sub(r"/(?:attempt|view-feedback)(?:[/?#].*)?$", "", page.url)
                page.goto(base, wait_until="domcontentloaded", timeout=30000)
                current = _wait_entry(page, checkpoint)
                if current.get("passed") and not current.get("questions"):
                    return _return_to_outline(page, current, checkpoint)
            names = _entry_names(current)
            if len(names) != 1:
                raise HandlerError("No unique Start/Resume control for this assignment")
            act(page, current, action="button", label=names[0])
            current = _await_attempt(page, checkpoint, failed_verified=current.get("failed", False), start_verified=True)
        questions = current["questions"]
        original = current["snapshot"]
        plans = []
        for question in questions:
            checkpoint()
            # Check again before the API call and before filling: an authorship
            # notice can appear late while the page hydrates.
            fresh = inspect(page)
            if fresh["snapshot"] != original:
                raise HandlerError("Question form changed while preparing answers")
            answer = groq.answer(question)
            store.record_stage(action_id, "answer_received", question=question_key(question), attempt=attempt + 1)
            fresh = inspect(page)
            if fresh["snapshot"] != original:
                raise HandlerError("Question form changed during the model request")
            act(page, fresh, action="fill", question_id=question["id"], answer=answer)
            store.record_stage(action_id, "answer_filled", question=question_key(question), attempt=attempt + 1)
            plans.append((question, answer))
        checkpoint()
        current = _wait_submit(page, checkpoint, original, plans)
        # Durable flag comes before the click, including the confirmation click.
        store.record_stage(action_id, "submitting", attempt=attempt + 1)
        act(page, current, action="button", label="Submit", answers=plans)
        current = _wait(page, checkpoint, lambda s: s.get("confirmation") or s.get("passed") or s.get("failed") or s.get("pending"), seconds=60)
        if current.get("confirmation"):
            checkpoint()
            act(page, current, action="button", label="Submit")
        store.record_stage(action_id, "submitted", attempt=attempt + 1)
        current = _wait(page, checkpoint, lambda s: (s.get("passed") or s.get("failed")) and not s.get("questions"), seconds=120)
        if current.get("passed"):
            store.record_stage(action_id, "passed", attempt=attempt + 1)
            # A whole-assignment grade is not proof that each answer was right.
            for question, answer in plans:
                groq.memory.record(question, answer)
            return _return_to_outline(page, current, checkpoint)
        store.record_stage(action_id, "failed", attempt=attempt + 1)
        feedback = _read_failed_feedback(page, current, checkpoint)
        store.set_meta(f"feedback:{action_id}", feedback.get("feedback", ""))
        _record_reviewed_feedback(feedback, plans, groq.memory, store, action_id)
        store.record_event("WARNING", "ASSIGNMENT_FAILED", f"Assignment attempt {attempt + 1} failed; scored feedback reviewed before retry")
        retries = [x for x in feedback["buttons"] if x in {"Retry", "Retry assignment", "Try again"}]
        if attempt + 1 == max_attempts or len(retries) != 1:
            raise HandlerError("Assignment failed; no verified retry flow is available within the attempt budget")
        act(page, feedback, action="button", label=retries[0])
        _await_attempt(page, checkpoint, failed_verified=True, start_verified=True)
        store.set_meta(submission_key, "not_submitted")
    raise HandlerError("Assignment attempt budget exhausted")


def _wait_entry(page, checkpoint):
    ready = lambda s: s.get("retry_confirmation") or s.get("start_confirmation") or s.get("questions") or _entry_names(s) or s.get("passed")
    try:
        return _wait(page, checkpoint, ready, seconds=45)
    except HandlerError as exc:
        if str(exc) != "Assignment did not reach a recognized state before timeout":
            raise
        current = inspect(page)
        # Reload only an empty landing page, before opening/filling/submitting.
        # Never reload an attempt or a feedback page to recover a submission.
        if (ready(current) or current.get("failed") or current.get("pending") or current.get("confirmation")
                or re.search(r"/(attempt|view-feedback)(?:/|$)", current.get("path", ""))):
            raise
        checkpoint()
        page.reload(wait_until="domcontentloaded", timeout=60000)
        return _wait(page, checkpoint, ready, seconds=90)


def _await_attempt(page, checkpoint, *, failed_verified=False, start_verified=False, seconds=60):
    current = _wait(page, checkpoint, lambda s: bool(s.get("questions")) or s.get("retry_confirmation") or s.get("start_confirmation"), seconds=seconds)
    if current.get("start_confirmation"):
        if not start_verified:
            raise HandlerError("Timed attempt requires a verified Start/Resume flow")
        act(page, current, action="button", label="Continue", start_attempt_verified=True)
        return _wait(page, checkpoint, lambda s: bool(s.get("questions")), seconds=seconds)
    if current.get("retry_confirmation"):
        if not failed_verified:
            raise HandlerError("Retry requires a verified failed attempt")
        act(page, current, action="button", label="Continue", failed_attempt_verified=True)
        current = _wait(page, checkpoint, lambda s: bool(s.get("questions")), seconds=seconds)
    return current


def _wait_submit(page, checkpoint, original, plans, *, seconds=20):
    def ready(state):
        if state["snapshot"] != original:
            raise HandlerError("Question form changed before submission")
        values = {v["id"]: v for v in state.get("values", [])}
        for question, answer in plans:
            value = values.get(question["id"], {})
            if "text" in answer:
                if value.get("text") != answer["text"]:
                    return False
            elif sorted(value.get("selected", [])) != sorted(answer["selected"]):
                return False
        return state["buttons"].count("Submit") == 1
    return _wait(page, checkpoint, ready, seconds=seconds)


def _read_failed_feedback(page, current, checkpoint, *, seconds=30):
    # Coursera may open the feedback overlay immediately after submission.
    # Its grade header arrives before the individual question results.
    if not current.get("path", "").endswith("/view-feedback"):
        names = [x for x in current["buttons"] if x in {"View feedback", "Review feedback", "View submission", "Feedback"}]
        if len(names) != 1:
            raise HandlerError("Assignment failed; full feedback is unavailable, so no automatic retry")
        act(page, current, action="button", label=names[0])
    return _wait(page, checkpoint,
                 lambda s: bool(s.get("feedback_questions")) and not s.get("questions"),
                 seconds=seconds)


def _return_to_outline(page, current, checkpoint):
    checkpoint()
    if "Go to next item" in current.get("buttons", []):
        act(page, current, action="button", label="Go to next item")
    elif "Back" in current.get("buttons", []):
        act(page, current, action="button", label="Back")
    else:
        # /attempt and /view-feedback are children of the observed assignment.
        url = re.sub(r"/(?:attempt|view-feedback)(?:[/?#].*)?$", "", page.url)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1000)
    return HandlerResult("Coursera reported a pass; the runner will verify the outline mark.")




def _scored_feedback(row):
    earned, possible = row.get("earned"), row.get("possible")
    return (type(earned) in (int, float) and type(possible) in (int, float)
            and 0 <= earned <= possible and possible > 0)


def _record_reviewed_feedback(feedback, plans, memory, store, action_id):
    """Validate the entire review before recording any answer as scored."""
    reviewed = []
    for question, answer in plans:
        matches = [row for row in feedback.get("feedback_questions", [])
                   if question_key(row["question"]) == question_key(question)]
        if len(matches) != 1 or not _scored_feedback(matches[0]):
            raise HandlerError("Failed attempt feedback could not be matched with scored questions; automatic retry blocked")
        row = matches[0]
        reviewed.append((question, answer, row))
    if not reviewed:
        raise HandlerError("No attempted questions to reconcile; automatic retry blocked")
    for question, answer, row in reviewed:
        memory.record(question, answer, earned=row["earned"], possible=row["possible"], feedback=row.get("feedback", ""))
    store.set_meta(f"feedback_review:{action_id}", {
        "status": "scored_questions_matched", "questions": [
            {"question_key": question_key(q), "earned": row["earned"], "possible": row["possible"],
             "feedback": row.get("feedback", "")} for q, answer, row in reviewed]})
    store.record_event("INFO", "FEEDBACK_REVIEWED", f"Matched scored feedback for {len(reviewed)} questions; retry may proceed")


def _recover_feedback_answers(rows, memory):
    """Recover only selections actually shown on the scored review page."""
    recovered = []
    for row in rows:
        if not _scored_feedback(row):
            raise HandlerError("Previous failed attempt score is unknown; reconcile before retry")
        try:
            answer = validate_answer(row["question"], row.get("answer"))
        except GroqError as exc:
            raise HandlerError("Previous failed attempt selections are unknown; reconcile before retry") from exc
        recovered.append((row, answer))
    if not recovered:
        raise HandlerError("Previous failed attempt has no question review")
    for row, answer in recovered:
        memory.record(row["question"], answer, earned=row["earned"], possible=row["possible"], feedback=row.get("feedback", ""))
