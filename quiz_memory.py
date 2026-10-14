"""Bounded, text-keyed quiz feedback context. Never applies or forces answers.

History is a list of attempt objects, each containing a ``questions`` list.
Only observed question/option text and unambiguously matched feedback survive.
Form IDs and question numbers never identify questions across attempts.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
import re
import unicodedata

MAX_HISTORY = 8
MAX_ATTEMPT_CHARS = 32_000
MAX_CONTEXT_CHARS = 16_000
MAX_QUESTIONS = 80
MAX_OPTIONS = 40
MAX_STEM_CHARS = 4_000
MAX_OPTION_CHARS = 2_000
MAX_FEEDBACK_CHARS = 2_000
_PUNCTUATION = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-"})
_SCORE = re.compile(r"(?im)^[ \t]*(\d{1,7}(?:[.,]\d{1,6})?)[ \t]*/[ \t]*"
                    r"(\d{1,7}(?:[.,]\d{1,6})?)[ \t]+points?[ \t]*$")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?\Z")


def _text(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def _normal(value):
    return _text(unicodedata.normalize("NFKC", value).translate(_PUNCTUATION)).casefold()


def _stem(value):
    # The form includes "1. Question 1 ..." while the result uses a separate
    # "Question 1" heading. UI ordinals must not become part of an identity.
    text = _text(value)
    text = re.sub(r"^\d{1,3}[.)]\s+", "", text)
    return re.sub(r"(?i)^Question\s+\d{1,3}(?:\s*[.:)]\s*|\s+)", "", text).strip()


def _size(value):
    return len(json.dumps(value, ensure_ascii=False))


def _crop_feedback(value, limit=MAX_FEEDBACK_CHARS):
    if len(value) <= limit:
        return value
    if limit < 40:
        return ""
    # Preserve the final question score and its surrounding explanation.
    head = min(400, limit // 3)
    tail_start = len(value) - (limit - head - 5)
    tail = value[tail_start:]
    if tail_start > 0 and value[tail_start - 1] != "\n":
        # Never turn the middle of an unscored line into a new score line.
        tail = tail.partition("\n")[2]
    return value[:head] + "\n[…]\n" + tail


def _score(feedback):
    lines = list(_SCORE.finditer(feedback))
    if not lines:
        return None, False, ""
    last = lines[-1]
    # A score embedded in an option's feedback is not a final question score.
    # The extracted page may include only the next question's ordinal after it.
    if not re.fullmatch(r"\s*(?:\d{1,3}\s*[.)]\s*)?", feedback[last.end():]):
        return None, False, ""
    try:
        earned = Decimal(last[1].replace(",", "."))
        possible = Decimal(last[2].replace(",", "."))
    except InvalidOperation:
        return None, False, ""
    if possible <= 0 or earned < 0 or earned > possible:
        return None, False, ""
    return {"earned": float(earned), "possible": float(possible)}, earned == possible, last[0].strip()


def _question(value, ids=False):
    if not isinstance(value, dict):
        return None
    text = _stem(value.get("text"))
    selection = value.get("selection")
    options = value.get("options")
    if not text or len(text) > MAX_STEM_CHARS or selection not in ("one", "many", "number"):
        return None
    if selection == "number":
        if options != []:
            return None
        result = {"text": text, "selection": "number", "options": []}
        if ids:
            identity = value.get("control_id")
            if not isinstance(identity, str) or not identity or len(identity) > 200:
                return None
            result["_ids"] = [identity]
        return result
    if not isinstance(options, list) or not 2 <= len(options) <= MAX_OPTIONS:
        return None
    labels, identities = [], []
    for option in options:
        label = _text(option.get("text")) if isinstance(option, dict) else _text(option)
        if not label or len(label) > MAX_OPTION_CHARS:
            return None
        labels.append(label)
        if ids:
            identity = option.get("id") if isinstance(option, dict) else None
            if not isinstance(identity, str) or not identity or len(identity) > 200:
                return None
            identities.append(identity)
    if len(set(map(_normal, labels))) != len(labels) or (ids and len(set(identities)) != len(identities)):
        return None
    result = {"text": text, "selection": selection, "options": labels}
    if ids:
        result["_ids"] = identities
    return result


def _signature(question):
    return (_normal(question["text"]), question["selection"], tuple(sorted(map(_normal, question["options"]))))


def _unique_questions(values, ids=False):
    if not isinstance(values, list):
        return []
    questions = [q for value in values[:MAX_QUESTIONS] if (q := _question(value, ids))]
    stems = Counter(_normal(q["text"]) for q in questions)
    identities = Counter(identity for q in questions for identity in q.get("_ids", []))
    return [q for q in questions if stems[_normal(q["text"])] == 1
            and all(identities[identity] == 1 for identity in q.get("_ids", []))]


def _stored_question(value):
    question = _question(value)
    if not question or not isinstance(value.get("selected_answers"), list):
        return None
    selected = [_text(answer) for answer in value["selected_answers"]]
    if question["selection"] == "number":
        if len(selected) != 1 or len(selected[0]) > 32 or not _NUMBER.fullmatch(selected[0]):
            return None
        feedback = _crop_feedback(value.get("feedback") if isinstance(value.get("feedback"), str) else "")
        score, full, _ = _score(feedback)
        question.update(selected_answers=selected, feedback=feedback, score=score, full_score=full)
        return question
    normalized = list(map(_normal, selected))
    option_labels = {_normal(label): label for label in question["options"]}
    if (not selected or len(set(normalized)) != len(normalized)
            or any(answer not in option_labels for answer in normalized)
            or (question["selection"] == "one" and len(selected) != 1)):
        return None
    feedback = value.get("feedback") if isinstance(value.get("feedback"), str) else ""
    feedback = _crop_feedback(feedback)
    score, full, _ = _score(feedback)
    question.update(selected_answers=[option_labels[answer] for answer in normalized],
                    feedback=feedback, score=score, full_score=full)
    return question


def _keep_history(history, limit):
    if not history or limit == 0:
        return []
    # Always retain the latest two attempts, then older full-score evidence
    # that would otherwise be lost, then the most recent remaining attempts.
    chosen = set(range(max(0, len(history) - min(2, limit)), len(history)))
    covered = {_signature(q) for i in chosen for q in history[i]["questions"] if q["full_score"]}
    for index in range(len(history) - 1, -1, -1):
        if len(chosen) >= limit:
            break
        full = {_signature(q) for q in history[index]["questions"] if q["full_score"]}
        if full - covered:
            chosen.add(index)
            covered.update(full)
    for index in range(len(history) - 1, -1, -1):
        if len(chosen) >= limit:
            break
        chosen.add(index)
    return [history[index] for index in sorted(chosen)]


def _history(value, limit=MAX_HISTORY):
    if not isinstance(value, list):
        return []
    attempts = []
    # Loading arbitrary oversized/corrupt state must remain bounded too.
    for attempt in value[-MAX_HISTORY:]:
        if not isinstance(attempt, dict) or not isinstance(attempt.get("questions"), list):
            continue
        entries = [q for v in attempt["questions"][:MAX_QUESTIONS] if (q := _stored_question(v))]
        stems = Counter(_normal(q["text"]) for q in entries)
        safe = []
        for question in entries:
            if stems[_normal(question["text"])] == 1 and _size({"questions": safe + [question]}) <= MAX_ATTEMPT_CHARS:
                safe.append(question)
        if safe:
            attempts.append({"questions": safe})
    return _keep_history(attempts, limit)


def record_attempt(history, questions, selected_controls, question_feedback_records, limit=MAX_HISTORY,
                   numeric_answers=None):
    """Return new bounded history; input objects are never mutated.

    Feedback must contain exactly one unique known stem and must be the only
    block matching it. A question_number field by itself is never sufficient.
    """
    limit = min(MAX_HISTORY, max(0, limit)) if type(limit) is int else MAX_HISTORY
    prior = _history(history)
    prepared = _unique_questions(questions, ids=True)
    if (not prepared or not isinstance(selected_controls, list)
            or any(not isinstance(identity, str) for identity in selected_controls)
            or len(set(selected_controls)) != len(selected_controls)):
        return _keep_history(prior, limit)
    known_ids = {identity for q in prepared for identity in q["_ids"]}
    if any(identity not in known_ids for identity in selected_controls):
        return _keep_history(prior, limit)
    numeric_answers = numeric_answers or {}
    number_ids = {q["_ids"][0] for q in prepared if q["selection"] == "number"}
    if (not isinstance(numeric_answers, dict) or
            any(identity not in number_ids or not isinstance(value, str) or
                len(value) > 32 or not _NUMBER.fullmatch(value)
                for identity, value in numeric_answers.items())):
        return _keep_history(prior, limit)
    selected_ids = set(selected_controls)
    feedback_by_stem = {}
    records = question_feedback_records if isinstance(question_feedback_records, list) else []
    for record in records[:MAX_QUESTIONS * 2]:
        block = record.get("text") if isinstance(record, dict) else None
        if not isinstance(block, str) or len(block) > 100_000:
            continue
        if len(re.findall(r"(?im)^\s*Question\s+\d+\s*$", block)) > 1:
            continue
        normalized = _normal(block)
        matches = [_normal(q["text"]) for q in prepared if _normal(q["text"]) in normalized]
        if len(matches) == 1:
            feedback_by_stem.setdefault(matches[0], []).append(block.strip())
    entries = []
    for question in prepared:
        identities = question.pop("_ids")
        if question["selection"] == "number":
            value = numeric_answers.get(identities[0])
            if value is None:
                continue
            selected = [value]
        else:
            selected = [label for identity, label in zip(identities, question["options"]) if identity in selected_ids]
            if not selected or (question["selection"] == "one" and len(selected) != 1):
                continue
        blocks = feedback_by_stem.get(_normal(question["text"]), [])
        feedback = _crop_feedback(blocks[0]) if len(blocks) == 1 else ""
        score, full, _ = _score(feedback)
        question.update(selected_answers=selected, feedback=feedback, score=score, full_score=full)
        if _size({"questions": entries + [question]}) <= MAX_ATTEMPT_CHARS:
            entries.append(question)
    if entries:
        prior.append({"questions": entries})
    return _keep_history(prior, limit)


def context_for_questions(history, current_questions):
    """Return <=16k JSON characters of observations matching the current form.

    Current question IDs are deliberately unnecessary. Matching requires the
    exact normalized stem, unique option text set, and unchanged selection mode.
    """
    attempts = _history(history)
    current = _unique_questions(current_questions)
    context, enrich, extras = [], [], []
    for question in current:
        matching = [(index, old) for index, attempt in enumerate(attempts)
                    for old in attempt["questions"] if _signature(old) == _signature(question)]
        if not matching:
            continue
        chosen = matching[-2:]
        older_full = next((entry for entry in reversed(matching[:-2]) if entry[1]["full_score"]), None)
        if older_full:
            chosen.insert(0, older_full)
        row = {"question": question["text"], "selection": question["selection"], "attempts": []}
        feedbacks = []
        for index, old in chosen:
            _, _, evidence = _score(old["feedback"])
            row["attempts"].append({"attempts_ago": len(attempts) - index - 1,
                                    "selected_answers": list(old["selected_answers"]),
                                    "score": old["score"], "full_score": old["full_score"],
                                    "score_evidence": evidence, "feedback": ""})
            feedbacks.append(old["feedback"])
        # First allocate compact evidence to as many questions as possible;
        # explanatory feedback cannot crowd out earlier confirmed selections.
        if _size(context + [row]) <= MAX_CONTEXT_CHARS:
            context.append(row)
            enrich.extend(zip(row["attempts"], feedbacks))
            chosen_indices = {index for index, _ in chosen}
            extras.append((row, [(index, old) for index, old in reversed(matching) if index not in chosen_indices]))
    # Keep older unsuccessful choices too, so a retry does not forget a known
    # earlier answer set merely because two more attempts have happened.
    # These compact records are allocated before any lengthy explanations.
    for row, remaining_attempts in extras:
        for index, old in remaining_attempts:
            record = {"attempts_ago": len(attempts) - index - 1,
                      "selected_answers": list(old["selected_answers"]),
                      "score": old["score"], "full_score": old["full_score"]}
            row["attempts"].append(record)
            if _size(context) > MAX_CONTEXT_CHARS:
                row["attempts"].pop()
        row["attempts"].sort(key=lambda record: record["attempts_ago"], reverse=True)
    for record, feedback in enrich:
        remaining = MAX_CONTEXT_CHARS - _size(context)
        candidate = _crop_feedback(feedback, min(900, remaining))
        while candidate:
            record["feedback"] = candidate
            if _size(context) <= MAX_CONTEXT_CHARS:
                break
            candidate = _crop_feedback(candidate, len(candidate) // 2)
        if not candidate:
            record["feedback"] = ""
    return context
