"""Reconcile a saved quiz Submit before leaving the current Coursera page.

This is deliberately narrower than the normal quiz runner.  It may inspect a
result and open its observed Feedback link, but it never starts, resumes or
submits an attempt.  A result that cannot be tied to the saved attempt remains
reserved for review by the ordinary course workflow.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import coursera_start as engine
from course_state import now


def _stop(message, course_id=None, item_key=None):
    error = engine.StopRun(message)
    error.course_id = course_id
    error.item_key = item_key
    raise error


def _unresolved(store):
    found = []
    for course in store.data.get("courses", []):
        checkpoint = course.get("checkpoint", {})
        attempts = checkpoint.get("attempts", {}) if isinstance(checkpoint, dict) else {}
        for key, ledger in attempts.items():
            if not isinstance(ledger, dict):
                continue
            needs_review = bool(ledger.get("pending") or
                                (ledger.get("count", 0) > 0 and not ledger.get("passed") and
                                 not ledger.get("feedback_ready")))
            if needs_review:
                found.append((course, key, ledger))
    return found


def _saved_item(course, key, root):
    """Find the exact prior roster row; never invent a lesson URL from a key."""
    checkpoint = course.get("checkpoint", {})
    modules = checkpoint.get("modules", {}) if isinstance(checkpoint, dict) else {}
    rosters = [record.get("roster", []) for record in modules.values()
               if isinstance(record, dict) and isinstance(record.get("roster"), list)]
    matches = [item for roster in rosters for item in roster
               if isinstance(item, dict) and item.get("key") == key]
    if not matches:
        previous = course.get("run_directory")
        directory = Path(previous) if isinstance(previous, str) else None
        logs_root = (Path(root) / "logs").resolve()
        if directory is not None and directory.resolve().parent == logs_root:
            path = directory / "events.jsonl"
            try:
                if path.is_file() and path.stat().st_size <= 10_000_000:
                    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
                        event = json.loads(line)
                        if not isinstance(event, dict) or event.get("event") != "module_roster":
                            continue
                        roster = event.get("roster")
                        if isinstance(roster, list):
                            matches = [item for item in roster
                                       if isinstance(item, dict) and item.get("key") == key]
                        if matches:
                            break
            except (OSError, ValueError):
                return None
    if not matches:
        return None
    urls = {item.get("url") for item in matches}
    if len(urls) != 1:
        return None
    item = matches[0]
    if not isinstance(item.get("url"), str) or engine.item_key(item["url"]) != key:
        return None
    return item


def _quiz_request(key, action="inspect", **fields):
    request = {"key": key, "action": action,
               "authorized_notice": engine.AUTHORIZED_QUIZ_NOTICE,
               "authorized_notice_confirmed": False, **fields}
    return engine.QUIZ_UI_JS + "(" + json.dumps(request) + ")"


async def _read_ui(tab, key, course_id):
    value = await tab.evaluate(_quiz_request(key))
    if (not isinstance(value, dict) or value.get("error") or
            engine.item_key(value.get("url", "")) != key):
        _stop("The saved assignment page is different or unreadable; its attempt remains reserved.",
              course_id, key)
    return value


def explicit_platform_attempt(text):
    values = {int(value) for value in re.findall(
        r"\bAttempt\s+(\d{1,3})\s+(?:of|out of)\s+\d{1,3}\b", text, re.I)}
    return next(iter(values)) if len(values) == 1 else None


def _grade_status(ui, key):
    text = ui.get("page_text", "")
    # A page showing two contradictory attempt summaries is not evidence for
    # which one belongs to the reserved Submit.
    if (re.search(r"\byou passed\b", text, re.I) and
            re.search(r"\byou (?:failed|did not pass|didn['’]t pass)\b", text, re.I)):
        return False, False
    snapshot = {"url": ui.get("url", ""), "feedback": text.splitlines()}
    passed = engine.quiz_passed(snapshot, key)
    failed = engine.quiz_failed(snapshot, key)
    return passed, failed


async def _result_ui(tab, key, course_id, pending, platform_attempt):
    if pending and (type(platform_attempt) is not int or platform_attempt < 1):
        _stop("The saved Submit has no Coursera attempt number captured before dispatch; "
              "an older grade cannot identify its result.", course_id, key)
    for _ in range(10):
        ui = await _read_ui(tab, key, course_id)
        if ui.get("blocking_dialog") or ui.get("editable_form"):
            _stop("The saved Submit still has a draft or blocking dialog; no new attempt was started.",
                  course_id, key)
        if ui.get("result_page"):
            if pending and explicit_platform_attempt(ui.get("page_text", "")) != platform_attempt:
                _stop("The result page does not identify the latest reserved attempt; its result remains unresolved.",
                      course_id, key)
            passed, failed = _grade_status(ui, key)
            if passed != failed:
                return ui, passed
        await asyncio.sleep(0.5)
    _stop("The previous Submit result is unavailable or has no verified pass/fail grade. Its attempt remains reserved.",
          course_id, key)


async def _detailed_feedback(tab, ui, key, course_id, expected):
    if type(expected) is not int or expected < 1:
        _stop("The failed attempt's saved question count is missing; complete Feedback cannot be verified.",
              course_id, key)
    if "view-feedback" not in urlsplit(ui["url"]).path.split("/"):
        choices = [button for button in ui.get("buttons", [])
                   if re.fullmatch(r"(?:View|Review) feedback", button.get("label", ""), re.I)
                   and not button.get("disabled")]
        if len(choices) != 1:
            _stop("The failed attempt's Feedback control is missing or ambiguous.", course_id, key)
        label = choices[0]["label"]
        result = await tab.evaluate(_quiz_request(
            key, "button", label=label, snapshot_id=ui.get("snapshot_id")))
        if not isinstance(result, dict) or result.get("clicked") != label:
            _stop("The observed Feedback control did not open; no retry was started.", course_id, key)
    for _ in range(40):
        ui = await _read_ui(tab, key, course_id)
        if ui.get("blocking_dialog") or not ui.get("result_page"):
            _stop("The Feedback page changed or is blocked; no retry was started.", course_id, key)
        text = engine.feedback_text(ui.get("page_text", ""), limit=16000)
        records = engine.question_feedback(text)
        numbers = {row["question_number"] for row in records}
        if ("view-feedback" in urlsplit(ui["url"]).path.split("/") and
                len(records) == expected and numbers == set(range(1, expected + 1))):
            return "\n\n".join(row["text"] for row in records), ui["url"]
        await asyncio.sleep(0.5)
    _stop("Detailed question Feedback did not load completely; no retry was started.", course_id, key)


async def recover_pending_quiz(tab, store, *, root):
    """Reconcile one unresolved saved attempt, returning a status dict or None.

    The caller must persist an attention status when StopRun is raised.  The
    exception carries ``course_id`` and ``item_key`` for that purpose.
    """
    candidates = _unresolved(store)
    if not candidates:
        return None
    if len(candidates) != 1:
        _stop("Multiple saved quiz results need review before My Learning can be opened.")
    course, key, ledger = candidates[0]
    course_id = course.get("id")
    if course.get("removed_from_queue"):
        _stop("A removed course still has an unresolved saved Submit; review its state first.", course_id, key)
    if not re.fullmatch(r"assignment:[A-Za-z0-9_-]+", key):
        _stop("The saved unresolved quiz key is invalid.", course_id, key)
    try:
        engine.configure_course(course["url"])
    except (KeyError, TypeError, ValueError):
        _stop("The saved unresolved quiz has no valid course URL.", course_id, key)
    item = _saved_item(course, key, root)
    if item is None:
        _stop("The unresolved quiz is missing its exact saved roster row.", course_id, key)
    pending = bool(ledger.get("pending"))
    count = ledger.get("count")
    if type(count) is not int or count < 1:
        _stop("The unresolved quiz has an invalid saved attempt count.", course_id, key)
    if pending and ledger.get("submit_phase") == "confirmation_observed":
        _stop("The saved quiz confirmation was observed but its final Submit was not clicked; "
              "the attempt remains reserved.", course_id, key)
    location = await tab.evaluate("({url:location.href})")
    current_url = location.get("url", "") if isinstance(location, dict) else ""
    if engine.item_key(current_url) != key:
        if pending:
            # A saved Feedback URL can refer to an older attempt and an
            # unsubmitted confirmation may be open on another tab.  Keep the
            # reservation until this exact attempt is visible in Chrome.
            _stop("The latest reserved quiz result is not open in Chrome; its attempt remains unresolved.",
                  course_id, key)
        result_url = ledger.get("result_url")
        target = result_url if isinstance(result_url, str) and engine.item_key(result_url) == key else item["url"]
        await tab.navigate(target)
    ui, passed = await _result_ui(tab, key, course_id, pending,
                                  ledger.get("platform_attempt"))
    feedback = ""
    feedback_url = None
    if not passed:
        feedback, feedback_url = await _detailed_feedback(
            tab, ui, key, course_id, ledger.get("question_count"))
    checkpoint = course.setdefault("checkpoint", {})
    if not passed:
        checkpoint.setdefault("feedback", {})[key] = feedback[:16000]
    ledger.update(pending=False, passed=passed, result_at=now(), reconciled_at=now(),
                  feedback_ready=not passed)
    if not passed:
        ledger["feedback_ready_count"] = count
    else:
        ledger.pop("feedback_ready_count", None)
    if feedback_url:
        ledger["result_url"] = feedback_url
    store.save()
    if hasattr(store, "event"):
        store.event("quiz_preflight_reconciled",
                    f"Saved attempt {count}: {'passed' if passed else 'failed; Feedback reviewed'}", course_id)
    return {"course_id": course_id, "key": key, "passed": passed,
            "feedback_ready": not passed}
