"""Recover semantic answer history from a prior local run, never attempt budgets."""
from __future__ import annotations

import json
from pathlib import Path
import re

from shared_io import open_read_shared


MAX_LOG_BYTES = 20 * 1024 * 1024
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?\Z")


def _bounded_read(path, run_directory, logs_directory):
    resolved = path.resolve(strict=True)
    if (not resolved.is_relative_to(run_directory) or
            not resolved.is_relative_to(logs_directory) or not resolved.is_file()):
        return None
    if resolved.stat().st_size > MAX_LOG_BYTES:
        return None
    with open_read_shared(resolved) as stream:
        data = stream.read(MAX_LOG_BYTES + 1)
    return data if len(data) <= MAX_LOG_BYTES else None


def _events(project_root, previous_run_directory):
    """Return bounded source events; None entries invalidate pending associations."""
    root = Path(project_root).resolve(strict=True)
    logs = (root / "logs").resolve(strict=True)
    if not logs.is_relative_to(root) or not logs.is_dir():
        return None
    previous = Path(previous_run_directory)
    if not previous.is_absolute():
        previous = root / previous
    previous = previous.resolve(strict=True)
    if previous == logs or not previous.is_relative_to(logs) or not previous.is_dir():
        return None
    json_lines = previous / "events.jsonl"
    if json_lines.exists():
        content = _bounded_read(json_lines, previous, logs)
        if content is None:
            return None
        events = []
        for line in content.decode("utf-8-sig").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                event = None
            events.append(event if isinstance(event, dict) else None)
        return events
    content = _bounded_read(previous / "progress.json", previous, logs)
    if content is None:
        return None
    progress = json.loads(content.decode("utf-8-sig"))
    if not isinstance(progress, dict) or not isinstance(progress.get("events"), list):
        return None
    return [event if isinstance(event, dict) else None for event in progress["events"]]


def _question_map(questions):
    if not isinstance(questions, list) or not 1 <= len(questions) <= 200:
        return None
    by_id, controls = {}, set()
    for question in questions:
        if not isinstance(question, dict):
            return None
        identity, options = question.get("id"), question.get("options")
        if (not isinstance(identity, str) or not identity or identity in by_id or
                not isinstance(question.get("text"), str) or not question["text"].strip() or
                question.get("selection") not in {"one", "many", "number"} or
                not isinstance(options, list)):
            return None
        if question["selection"] == "number":
            control_id = question.get("control_id")
            if options or not isinstance(control_id, str) or not control_id or control_id in controls:
                return None
            controls.add(control_id)
            by_id[identity] = {"choices":set(), "selection":"number", "control_id":control_id}
            continue
        if not 2 <= len(options) <= 200:
            return None
        choices = set()
        for option in options:
            if (not isinstance(option, dict) or not isinstance(option.get("id"), str) or
                    not option["id"] or option["id"] in controls or
                    not isinstance(option.get("text"), str) or not option["text"].strip()):
                return None
            choices.add(option["id"])
            controls.add(option["id"])
        by_id[identity] = {"choices":choices, "selection":question["selection"]}
    return by_id


def _selected(plan, questions):
    by_id = _question_map(questions)
    if (by_id is None or not isinstance(plan, dict) or plan.get("cannot_answer") is not False or
            not isinstance(plan.get("answers"), list) or len(plan["answers"]) != len(by_id)):
        return None
    selected, numeric, seen = [], {}, set()
    for answer in plan["answers"]:
        if not isinstance(answer, dict):
            return None
        identity, choices = answer.get("question_id"), answer.get("selected_controls")
        if (not isinstance(identity, str) or identity not in by_id or identity in seen or
                not isinstance(choices, list) or
                any(not isinstance(choice, str) for choice in choices)):
            return None
        valid = by_id[identity]
        if valid["selection"] == "number":
            value = answer.get("numeric_answer")
            if choices or not isinstance(value, str) or len(value) > 32 or not _NUMBER.fullmatch(value):
                return None
            numeric[valid["control_id"]] = value
            seen.add(identity)
            continue
        if not choices or ("numeric_answer" in answer and answer["numeric_answer"] != ""):
            return None
        if (len(set(choices)) != len(choices) or not set(choices) <= valid["choices"] or
                (valid["selection"] == "one" and len(choices) != 1)):
            return None
        selected.extend(choices)
        seen.add(identity)
    return {"selected":selected, "numeric":numeric}


def import_previous_history(project_root, previous_run_directory, target_history_dict):
    """Import newly reconstructed attempts for quiz keys without saved history.

    Only fixed files underneath project_root/logs are read. Existing nonempty
    histories are untouched. No counter, action, credential or source path from
    an event is copied, and malformed sequences cannot reuse an older answer plan.
    """
    if not isinstance(target_history_dict, dict) or not previous_run_directory:
        return 0
    try:
        events = _events(project_root, previous_run_directory)
    except (OSError, ValueError, TypeError, RuntimeError):
        return 0
    if events is None:
        return 0
    from quiz_memory import record_attempt

    excluded = {key for key, history in target_history_dict.items() if history}
    pending, staged = {}, {}
    imported = 0
    for event in events:
        if event is None:
            # A damaged line could have contained a newer question/answer event.
            # Discard associations until a fresh valid question event is seen.
            pending.clear()
            continue
        key = event.get("key")
        if (not isinstance(key, str) or not re.fullmatch(r"assignment:[A-Za-z0-9_-]{1,200}", key)
                or key in excluded):
            continue
        kind = event.get("event")
        if kind == "quiz_questions":
            questions = event.get("questions")
            pending.pop(key, None)
            if _question_map(questions) is not None:
                pending[key] = {"questions":questions, "selected":None}
        elif kind == "quiz_answer_plan" and key in pending:
            pending[key]["selected"] = _selected(event.get("plan"), pending[key]["questions"])
        elif kind == "quiz_submitted_result":
            attempt = pending.pop(key, None)
            feedback = event.get("question_feedback")
            if (not attempt or attempt["selected"] is None or not isinstance(feedback, list) or
                    not feedback or len(feedback) > 200 or
                    any(not isinstance(record, dict) or not isinstance(record.get("text"), str)
                        for record in feedback)):
                continue
            history = staged.get(key, [])
            try:
                updated = record_attempt(history, attempt["questions"], attempt["selected"]["selected"],
                                         feedback, numeric_answers=attempt["selected"]["numeric"])
            except (TypeError, ValueError, KeyError, IndexError):
                continue
            if isinstance(updated, list) and updated and updated != history:
                staged[key] = updated
                imported += 1
    target_history_dict.update(staged)
    return imported
