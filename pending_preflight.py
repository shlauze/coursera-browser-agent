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
        # Removed courses retain their checkpoints for a possible re-add, but
        # they are not part of the active startup queue. A visible confirmation
        # is still a draft, not a submitted result; the regular quiz workflow
        # resumes that reserved draft when its course is selected.
        if course.get("removed_from_queue"):
            continue
        checkpoint = course.get("checkpoint", {})
        attempts = checkpoint.get("attempts", {}) if isinstance(checkpoint, dict) else {}
        for key, ledger in attempts.items():
            if not isinstance(ledger, dict):
                continue
            if ledger.get("pending") and ledger.get("submit_phase") == "confirmation_observed":
                continue
            needs_review = bool(ledger.get("pending") or
                                (ledger.get("count", 0) > 0 and not ledger.get("passed") and
                                 not ledger.get("feedback_ready")))
            if needs_review:
                found.append((course, key, ledger))
    return found


def _saved_item(course, key, root):
    """Find the exact prior roster row; never invent a lesson URL from a key."""
    def valid(item):
        if not isinstance(item, dict) or item.get("key") != key or not isinstance(item.get("url"), str):
            return False
        parsed = urlsplit(item["url"])
        match = re.fullmatch(
            r"/learn/([A-Za-z0-9_-]+)/assignment-submission/([A-Za-z0-9_-]+)(?:/.*)?",
            parsed.path)
        return (parsed.scheme == "https" and parsed.hostname in {"coursera.org", "www.coursera.org"}
                and match is not None and match.group(1) == course.get("id") and
                key == "assignment:" + match.group(2) and engine.item_key(item["url"]) == key)

    checkpoint = course.get("checkpoint", {})
    modules = checkpoint.get("modules", {}) if isinstance(checkpoint, dict) else {}
    rosters = [record.get("roster", []) for record in modules.values()
               if isinstance(record, dict) and isinstance(record.get("roster"), list)]
    matches = [item for roster in rosters for item in roster if valid(item)]
    if not matches:
        previous = course.get("run_directory")
        directory = Path(previous) if isinstance(previous, str) else None
        logs_root = (Path(root) / "logs").resolve()
        directories = []
        if directory is not None and directory.resolve().parent == logs_root:
            directories.append(directory)
        # Module snapshots can be rebuilt after a restart. Search only a
        # bounded set of this course's older append-only rosters for the exact
        # key, and reject conflicting URLs rather than guessing a lesson.
        directories.extend(sorted(logs_root.glob("queue_" + course["id"] + "_*"),
                                  reverse=True)[:32])
        seen = set()
        for directory in directories:
            if directory in seen or directory.resolve().parent != logs_root:
                continue
            seen.add(directory)
            path = directory / "events.jsonl"
            try:
                if path.is_file() and path.stat().st_size <= 10_000_000:
                    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
                        event = json.loads(line)
                        if not isinstance(event, dict) or event.get("event") != "module_roster":
                            continue
                        roster = event.get("roster")
                        if isinstance(roster, list):
                            matches.extend(item for item in roster if valid(item))
            except (OSError, ValueError):
                continue
    if not matches:
        return None
    urls = {item.get("url") for item in matches}
    if len(urls) != 1:
        return None
    item = matches[0]
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
    has_attempt_number = type(platform_attempt) is int and platform_attempt > 0
    feedback_opened = False
    for _ in range(10):
        ui = await _read_ui(tab, key, course_id)
        if ui.get("blocking_dialog") or ui.get("editable_form"):
            _stop("The saved Submit still has a draft or blocking dialog; no new attempt was started.",
                  course_id, key)
        if ui.get("result_page"):
            passed, failed = _grade_status(ui, key)
            if passed != failed:
                observed_attempt = explicit_platform_attempt(ui.get("page_text", ""))
                if (pending and passed and
                        (not has_attempt_number or observed_attempt != platform_attempt) and
                        await _green_assignment(tab, course_id, key)):
                    # A current passing grade plus the site's green mark proves
                    # completion even when an older checkpoint has no platform
                    # attempt number. No new Submit is needed for this item.
                    return ui, True
                if pending and not has_attempt_number:
                    _stop("The saved Submit has no Coursera attempt number captured before dispatch, "
                          "and the site has no passing result with a green completion mark. "
                          "Its reservation was preserved.", course_id, key)
                if pending and observed_attempt != platform_attempt:
                    _stop("The result page does not identify the latest reserved attempt; its result remains unresolved.",
                          course_id, key)
                return ui, passed
        if pending and not feedback_opened:
            choices = [button for button in ui.get("buttons", [])
                       if re.fullmatch(r"(?:View|Review) feedback", button.get("label", ""), re.I)
                       and not button.get("disabled")]
            if len(choices) == 1:
                label = choices[0]["label"]
                opened = await tab.evaluate(_quiz_request(
                    key, "button", label=label, snapshot_id=ui.get("snapshot_id")))
                if not isinstance(opened, dict) or opened.get("clicked") != label:
                    _stop("The observed Feedback link changed; no new attempt was started.", course_id, key)
                feedback_opened = True
        await asyncio.sleep(0.5)
    _stop("The previous Submit result is unavailable or has no verified pass/fail grade. Its attempt remains reserved.",
          course_id, key)


async def _green_assignment(tab, course_id, key):
    """Read the exact assignment's current sidebar mark without changing it."""
    for _ in range(6):
        snapshot = await tab.evaluate(engine.PAGE_JS)
        if not isinstance(snapshot, dict) or snapshot.get("blocked") or not _same_assignment(
                snapshot.get("url", ""), course_id, key):
            return False
        row = engine.rows_by_key(snapshot).get(key)
        if row and row.get("status") == "completed" and row.get("checked") is True:
            return True
        await asyncio.sleep(0.5)
    return False


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
    """Compatibility helper for one unresolved saved result."""
    candidates = _unresolved(store)
    if not candidates:
        return None
    if len(candidates) != 1:
        _stop("Several active quiz results need sequential review; use the queue preflight.")
    return await _recover_candidate(tab, store, candidates[0], root=root)


async def recover_pending_quizzes(tab, store, *, root):
    """Review every active saved result in sequence before opening My Learning.

    A pending Submit is considered first. Settled failed results may be opened
    from their saved roster/Feedback URL, but no new attempt is started here.
    An uncertain result stops on that exact course/item with all reservations
    intact. Previously reviewed entries stay durable if a later one stops.
    """
    outcomes = []
    initial = _unresolved(store)
    for _ in range(len(initial)):
        candidates = _unresolved(store)
        if not candidates:
            return outcomes
        pending = [candidate for candidate in candidates if candidate[2].get("pending")]
        if pending:
            location = await tab.evaluate("({url:location.href})")
            url = location.get("url", "") if isinstance(location, dict) else ""
            exact = [candidate for candidate in pending
                     if _same_assignment(url, candidate[0].get("id"), candidate[1])]
            if len(exact) > 1:
                _stop("Several saved submissions identify the same open assignment; no result was changed.")
            selected = exact[0] if exact else pending[0]
        else:
            selected = candidates[0]
        outcomes.append(await _recover_candidate(tab, store, selected, root=root))
    if _unresolved(store):
        _stop("Saved quiz results remain after the bounded startup review.")
    return outcomes


def _same_assignment(url, course_id, key):
    parsed = urlsplit(url)
    match = re.fullmatch(
        r"/learn/([A-Za-z0-9_-]+)/assignment-submission/([A-Za-z0-9_-]+)(?:/.*)?",
        parsed.path)
    return (parsed.scheme == "https" and parsed.hostname in {"coursera.org", "www.coursera.org"}
            and match is not None and match.group(1) == course_id and
            key == "assignment:" + match.group(2))


async def _recover_candidate(tab, store, candidate, *, root):
    """Reconcile one exact saved result; never dispatch Submit or Retry.

    The caller must persist an attention status when StopRun is raised.  The
    exception carries ``course_id`` and ``item_key`` for that purpose.
    """
    course, key, ledger = candidate
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
    if not _same_assignment(current_url, course_id, key):
        if pending:
            # Reopening this validated assignment is read-only. A missing
            # attempt number is accepted only with a fresh passing grade and
            # the matching green sidebar mark.
            await tab.navigate(item["url"])
        result_url = ledger.get("result_url")
        if not pending:
            target = result_url if isinstance(result_url, str) and _same_assignment(result_url, course_id, key) else item["url"]
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
