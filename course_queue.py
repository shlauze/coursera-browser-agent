"""Sequential, checkpointed Coursera queue. Browser actions remain in the engine."""
from __future__ import annotations

import argparse
import asyncio
import ctypes
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import coursera_start as engine
from course_state import MAX_COURSES, QueueStore, RunLock, course_identity, load_courses, now, safe_limits
from course_adapter import (discover_modules, discover_roster, inspect_sidebar_progress,
                            verified_sidebar_module, verify_course_completion)
from my_learning import (MY_LEARNING_URL, MyLearningSelectionChanged,
                         scan_in_progress, open_scanned_course,
                         find_completed_certificate,
                         observed_certificate_url, open_certificate_page,
                         open_my_learning, valid_certificate_url, verify_saved_certificate)
from lab_flow import complete_lab
from programming_flow import diagnose_programming
from dialogue_flow import run_dialogue
from reflection_flow import maybe_complete_reflection, recover_pending_reflection, pending_reflections
from pending_preflight import recover_pending_quizzes, _unresolved, explicit_platform_attempt

ROOT = Path(__file__).resolve().parent
CERTIFICATE_RECHECKS = 10
CERTIFICATE_RETRY_DELAY = 20


def _known_module_floor(course, logs_root):
    """Preserve every module number previously reached, including old runs.

    A transient React outline can shrink to only the current module. An
    earlier run's progress file is useful here because the current checkpoint
    may already have been rebuilt from that transient outline.
    """
    values = []
    for field in ("modules_max_seen", "modules_total", "modules_discovered"):
        value = course.get(field)
        if type(value) is int and 1 <= value <= 1000:
            values.append(value)
    configured = course.get("modules")
    if isinstance(configured, list) and configured:
        values.append(max(configured))

    def remember_modules(record):
        if not isinstance(record, dict):
            return
        module_rows = record.get("modules")
        if not isinstance(module_rows, dict):
            return
        for key in module_rows:
            if isinstance(key, str) and key.isdecimal():
                number = int(key)
                if 1 <= number <= 1000:
                    values.append(number)

    remember_modules(course.get("checkpoint"))
    logs_root = Path(logs_root)
    for progress_file in logs_root.glob("queue_" + course["id"] + "_*/progress.json"):
        try:
            previous = json.loads(progress_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        remember_modules(previous)
    return max(values, default=1)


@contextmanager
def reported_store(path, courses):
    store = QueueStore(path, courses)
    try:
        yield store
    except (KeyboardInterrupt, asyncio.CancelledError):
        if store.data["status"] != "paused":
            store.data["status"] = "paused"
            store.event("queue_paused", "Остановлено пользователем до начала выполнения.")
        raise
    except Exception as exc:
        store.data["status"] = "attention"
        store.event("course_attention", "Ошибка запуска: " + (str(exc) or type(exc).__name__))
        raise


class QueueRunner(engine.CourseRunner):
    def __init__(self, tab, args, directory, store, course):
        super().__init__(tab, args, directory)
        self.store, self.course = store, course
        self.checkpoint = course.setdefault("checkpoint", {})
        for name in ("attempts", "feedback", "modules", "answer_history"):
            self.checkpoint.setdefault(name, {})
        self.answer_history = self.checkpoint["answer_history"]
        self._course_timeout = None
        self._resuming_quiz_draft_key = None
        self._quiz_answer_plans.update({key:ledger["last_plan"] for key, ledger in self.checkpoint["attempts"].items()
                                       if isinstance(ledger.get("last_plan"), dict)})

    def previous_quiz_feedback(self, key):
        return self.checkpoint["feedback"].get(key, "")

    async def on_quiz_cooldown(self, seconds):
        """Exclude Coursera's imposed retry wait from the course work budget."""
        if not isinstance(seconds, (int, float)) or seconds <= 0:
            return
        if self._course_timeout:
            deadline = self._course_timeout.when()
            if deadline is not None:
                self._course_timeout.reschedule(deadline + seconds)
        retry_at = self.course.get("retry_at")
        self.course["message"] = (f"Ожидание повторной попытки до {retry_at}"
                                  if retry_at else f"Ожидание разрешённой повторной попытки: {int(seconds)} с")
        self.store.save()

    def quiz_attempt_limit(self, key):
        ledger = self.checkpoint["attempts"].get(key, {})
        if (self._resuming_quiz_draft_key == key and ledger.get("pending") and
                ledger.get("submit_phase") == "confirmation_observed"):
            # The site has a resumable draft from this already counted attempt.
            # Include that attempt in the engine's in-run budget.
            return None if self.args.quiz_attempts == 0 else max(1, self.args.quiz_attempts - ledger.get("count", 0) + 1)
        if ledger.get("pending"):
            raise engine.StopRun("The previous Submit outcome is uncertain. Check the saved site result before another attempt.")
        if ledger.get("count", 0) and not ledger.get("passed") and not ledger.get("feedback_ready"):
            raise engine.StopRun("The previous attempt has no detailed feedback. Review it before spending another attempt.")
        return None if self.args.quiz_attempts == 0 else max(0, self.args.quiz_attempts - ledger.get("count", 0))

    def quiz_attempt_number(self, key, session_attempts):
        ledger = self.checkpoint["attempts"].get(key, {})
        if self._resuming_quiz_draft_key == key and ledger.get("pending"):
            return max(0, ledger.get("count", 0) - 1)
        return ledger.get("count", 0)

    async def before_quiz_action(self, key, action, parameters, observed_ui=None):
        if action == "button" and parameters.get("label", "").casefold() == "submit":
            ledger = self.checkpoint["attempts"].setdefault(key, {"count":0})
            platform_attempt = (explicit_platform_attempt(observed_ui.get("page_text", ""))
                                if isinstance(observed_ui, dict) and
                                engine.item_key(observed_ui.get("url", "")) == key and
                                observed_ui.get("editable_form") and
                                not observed_ui.get("blocking_dialog") else None)
            if (self._resuming_quiz_draft_key == key and ledger.get("pending") and
                    ledger.get("submit_phase") == "confirmation_observed"):
                ledger.update(submit_phase="resumed_first_dispatch", resumed_at=now())
                if platform_attempt is not None:
                    ledger["platform_attempt"] = platform_attempt
                self.store.save()
                return
            if ledger.get("pending") or (self.args.quiz_attempts > 0 and ledger["count"] >= self.args.quiz_attempts):
                raise engine.StopRun("The saved quiz attempt budget is exhausted or a prior Submit is unresolved.")
            ledger["pre_submit_feedback"] = {"ready":bool(ledger.get("feedback_ready")),
                                             "ready_count":ledger.get("feedback_ready_count")}
            # A previous result link cannot identify the attempt reserved now.
            ledger.pop("result_url", None)
            ledger.pop("platform_attempt", None)
            if platform_attempt is not None:
                ledger["platform_attempt"] = platform_attempt
            ledger.update(count=ledger["count"] + 1, pending=True, feedback_ready=False,
                          reserved_at=now(), submit_phase="first_dispatch")
            # Commit before dispatch; a crash can never silently reset the limit.
            self.store.save()
        elif action == "confirm_submit":
            ledger = self.checkpoint["attempts"].get(key, {})
            if ledger.get("pending"):
                ledger["submit_phase"] = "confirm_dispatch"
                self.store.save()

    async def after_quiz_action(self, key, action, parameters, result):
        certificate_url = result.get("certificate_page_url") if isinstance(result, dict) else None
        certificate_text = (result.get("dialog") or "") if isinstance(result, dict) else ""
        certificate_title = re.search(r"You've now completed (.+?) and earned the certificate!", certificate_text)
        title_id = (re.sub(r"[^a-z0-9]+", "-", certificate_title[1].lower()).strip("-")
                    if certificate_title else None)
        page = engine.urlsplit(result.get("url", "")) if isinstance(result, dict) else None
        same_course = (page and page.scheme == "https" and page.hostname in {"coursera.org", "www.coursera.org"}
                       and page.path.startswith("/learn/" + self.course["id"] + "/"))
        expected_titles = {self.course["id"],
                           re.sub(r"[^a-z0-9]+", "-", self.course.get("title", "").lower()).strip("-")}
        if valid_certificate_url(certificate_url) and same_course and title_id in expected_titles:
            self.course["certificate_page_url"] = certificate_url
            self.store.save()
        ledger = self.checkpoint["attempts"].get(key)
        if not ledger:
            return
        if action == "inspect" and ledger.get("pending") and (
                result.get("dialog_kind") == "submit_confirmation" or
                result.get("hidden_submit_confirmation")):
            if ledger.get("submit_phase") not in {"confirm_dispatch", "confirm_clicked"}:
                ledger["submit_phase"] = "confirmation_observed"
                self.store.save()
        elif action == "button" and parameters.get("label", "").casefold() == "submit":
            if result.get("error") and not result.get("clicked"):
                # A JS error proves no click. A resumed draft retains its one
                # reservation; a fresh failed dispatch can be refunded.
                if ledger.get("submit_phase") == "resumed_first_dispatch":
                    ledger["submit_phase"] = "confirmation_observed"
                else:
                    ledger.update(count=max(0, ledger["count"] - 1), pending=False)
                    ledger.pop("submit_phase", None)
                    ledger.pop("platform_attempt", None)
                    previous = ledger.pop("pre_submit_feedback", None)
                    if isinstance(previous, dict):
                        ledger["feedback_ready"] = bool(previous.get("ready"))
                        if previous.get("ready_count") is None:
                            ledger.pop("feedback_ready_count", None)
                        else:
                            ledger["feedback_ready_count"] = previous["ready_count"]
                self.store.save()
            elif result.get("clicked") == "Submit":
                ledger["submit_phase"] = "first_clicked"
                ledger.pop("pre_submit_feedback", None)
                self.store.save()
        elif action == "confirm_submit":
            if result.get("error") and not result.get("clicked"):
                ledger["submit_phase"] = "confirmation_observed"
                self.store.save()
            elif result.get("clicked") == "Submit":
                ledger["submit_phase"] = "confirm_clicked"
                self.store.save()

    async def quiz(self, item):
        """Reconcile observed grading before checking the durable retry budget."""
        if await maybe_complete_reflection(self, item):
            return
        key = item["key"]
        ledger = self.checkpoint["attempts"].get(key, {})
        required = bool(ledger.get("pending") or (ledger.get("count", 0) and
                        not ledger.get("passed") and not ledger.get("feedback_ready")))
        observed_ui = None

        async def quiz_ui(action="inspect", **fields):
            nonlocal observed_ui
            request = {"key":key, "action":action, "authorized_notice":engine.AUTHORIZED_QUIZ_NOTICE,
                       "authorized_notice_confirmed":False, **fields}
            await self.before_quiz_action(key, action, fields, observed_ui=observed_ui)
            current = await self.tab.evaluate(engine.QUIZ_UI_JS + "(" + json.dumps(request) + ")")
            await self.after_quiz_action(key, action, fields, current)
            if current.get("error") or engine.item_key(current.get("url", "")) != key:
                raise engine.StopRun("Cannot reconcile this quiz's saved result: the current assignment is different or unreadable.")
            observed_ui = current
            return current

        async def inspect():
            return await quiz_ui()

        current = await inspect()
        if (ledger.get("pending") and ledger.get("submit_phase") == "confirmation_observed" and
                current.get("result_page")):
            raise engine.StopRun("A previous result is visible, but this draft's final Submit was never clicked. Its reservation was preserved.")
        # A previous graded attempt can still expose View feedback while a
        # newer draft is open. Never use that older review to clear a pending
        # reservation; only non-pending attempts may open it here.
        if required and not ledger.get("pending") and not current.get("result_page") and any(re.fullmatch(r"(?:View|Review) feedback", b.get("label", ""), re.I)
                            for b in current.get("buttons", [])):
            current = await self.open_quiz_feedback(item, quiz_ui, current)
        if ledger.get("pending") and not current.get("result_page"):
            # A visible or certificate-obscured confirmation proves that the
            # first Submit only opened a prompt. A draft may use its existing
            # reservation; a clicked final confirmation remains uncertain.
            if current.get("dialog_kind") == "course_certificate" and ledger.get("submit_phase") == "confirmation_observed":
                closed = await quiz_ui("dismiss_course_certificate", snapshot_id=current.get("snapshot_id"))
                if closed.get("clicked") != "Close":
                    raise engine.StopRun("The course certificate overlay could not be closed during recovery.")
                await asyncio.sleep(0.5)
                current = await inspect()
            if ledger.get("submit_phase") == "confirmation_observed" and current.get("dialog_kind") == "submit_confirmation":
                plan = ledger.get("last_plan") or {}
                selected = plan.get("selected_controls")
                numeric = plan.get("numeric_answers")
                if not isinstance(selected, list) or not isinstance(numeric, dict):
                    raise engine.StopRun("The saved answer plan is missing; the visible Submit confirmation needs review.")
                submitted = await quiz_ui("confirm_submit", snapshot_id=current.get("snapshot_id"),
                                          expected_selected=selected, expected_numeric=numeric)
                if submitted.get("clicked") != "Submit":
                    raise engine.StopRun("The saved submission confirmation changed before recovery.")
                self.event("quiz_confirmation_recovered", key=key, count=ledger.get("count", 0))
                for _ in range(40):
                    await asyncio.sleep(1)
                    current = await inspect()
                    if current.get("result_page") and not current.get("blocking_dialog"):
                        break
                else:
                    raise engine.StopRun("The recovered Submit has no verified result yet. Its attempt remains reserved.")
            elif ledger.get("submit_phase") == "confirmation_observed":
                resume = [b for b in current.get("buttons", [])
                          if b.get("label", "").casefold() == "resume assignment" and
                          b.get("workflow") == "entry" and not b.get("disabled")]
                entries = [b for b in current.get("buttons", []) if b.get("workflow") == "entry" and not b.get("disabled")]
                draft = current.get("editable_form") and "/attempt" in engine.urlsplit(current.get("url", "")).path
                no_grade = not re.search(r"\bYour (?:grade|latest|highest)\s*:", current.get("page_text", ""), re.I)
                if no_grade and not current.get("blocking_dialog") and (draft or (len(resume) == len(entries) == 1)):
                    self._resuming_quiz_draft_key = key
                    self.event("quiz_draft_recovered", key=key, count=ledger.get("count", 0))
                    try:
                        return await super().quiz(item)
                    finally:
                        self._resuming_quiz_draft_key = None
        # An old v3.7 result can supply feedback without inventing a count of
        # historical attempts. The durable v4 budget starts at zero in that case.
        if not required and (not current.get("result_page") or current.get("blocking_dialog")):
            return await super().quiz(item)
        for probe in range(8):
            if current.get("result_page") and not current.get("blocking_dialog") and not current.get("editable_form"):
                break
            if not required:
                return await super().quiz(item)
            if current.get("blocking_dialog") or current.get("editable_form"):
                raise engine.StopRun("The previous Submit is unresolved. No completed result is visible; no new attempt was started.")
            if probe == 7:
                raise engine.StopRun("The previous Submit result is still unavailable. Its reserved attempt was preserved.")
            await asyncio.sleep(1)
            current = await inspect()

        snapshot = await self.snapshot()
        if engine.item_key(snapshot.get("url", "")) != key:
            raise engine.StopRun("The assignment changed while its saved result was checked.")
        passed = engine.quiz_passed(snapshot, key)
        grade_text = "\n".join(snapshot.get("feedback", []))
        grade = re.search(r"(?:your grade|your score|grade\s*:|score\s*:)\s*[:\s]*(\d+(?:\.\d+)?)\s*%", grade_text, re.I)
        threshold = re.search(r"(?:to pass|passing grade)[^\n\d]*(\d+(?:\.\d+)?)\s*%", grade_text, re.I)
        failed = bool((grade and threshold and float(grade[1]) < float(threshold[1])) or
                      re.search(r"\byou (?:failed|did not pass|didn['’]t pass)\b", current.get("page_text", ""), re.I))
        if not passed and not failed:
            raise engine.StopRun("The saved quiz result has no verified pass/fail evidence. No new attempt was started.")
        ledger = self.checkpoint["attempts"].setdefault(key, {"count":0})
        # Keep the reservation counted, even if a crash makes the identity of
        # the last submitted attempt uncertain. Never refund an uncertain click.
        ledger.update(pending=False, passed=passed, result_at=now(), reconciled_at=now())
        self.store.save()
        if passed:
            if not await self.verified_item(item, wait=2):
                await self.open_item(item)
                if not await self.verified_item(item, wait=8):
                    raise engine.StopRun("A passing result is visible, but the site's saved completion mark is not confirmed.")
            self.event("quiz_reconciled", key=key, passed=True, count=ledger["count"])
            return

        expected = ledger.get("question_count", 0)
        if not expected:
            stems = {c.get("name") or engine.question_text(c.get("question", ""))
                     for c in current.get("controls", []) if c.get("kind") == "answer" and c.get("in_scope", True)}
            stems.discard("")
            stems.discard(None)
            expected = len(stems)
        if not expected:
            ledger["feedback_ready"] = False
            self.store.save()
            raise engine.StopRun("The prior result is confirmed, but its question feedback is not readable. Open the site's feedback page and resume.")
        try:
            await self.wait_for_quiz_feedback(item, quiz_ui, current, expected)
        except engine.StopRun:
            ledger["feedback_ready"] = False
            self.store.save()
            raise
        self.event("quiz_reconciled", key=key, passed=False, count=ledger["count"])
        # Resume the existing bounded workflow, using the feedback just saved.
        await self.open_item(item)
        return await super().quiz(item)

    def event(self, kind, **fields):
        super().event(kind, **fields)
        key = fields.get("key")
        if kind == "quiz_attempt_started":
            self.course.pop("retry_at", None)
            self.course["message"] = f"Тест: попытка {fields['attempt']}. Исправление по обратной связи до проходного результата."
        if kind == "quiz_cooldown_started":
            retry_at = fields.get("retry_at")
            if isinstance(retry_at, str):
                self.course["retry_at"] = retry_at
                self.course["message"] = f"Ожидание повторной попытки до {retry_at}"
                self.store.event("quiz_cooldown", self.course["message"], self.course["id"])
        if kind == "quiz_questions":
            ledger = self.checkpoint["attempts"].setdefault(key, {"count":0})
            ledger["question_count"] = len(fields.get("questions", []))
        if kind == "quiz_answer_plan" and key in self._quiz_answer_plans:
            self.checkpoint["attempts"].setdefault(key, {"count":0})["last_plan"] = self._quiz_answer_plans[key]
        if kind == "quiz_controls":
            result = fields.get("result", {})
            url = result.get("url", "")
            if result.get("result_page") and engine.item_key(url) == key and "view-feedback" in engine.urlsplit(url).path.split("/"):
                self.checkpoint["attempts"].setdefault(key, {"count":0})["result_url"] = url
        if kind == "quiz_feedback_ready":
            self.checkpoint["feedback"][key] = fields.get("feedback_text", "")[:16000]
            ledger = self.checkpoint["attempts"].setdefault(key, {"count":0})
            ledger.update(feedback_ready=True, feedback_ready_count=ledger["count"])
        if kind == "quiz_submitted_result":
            ledger = self.checkpoint["attempts"].setdefault(key, {"count":fields.get("attempt", 1)})
            ledger.update(pending=False, passed=bool(fields.get("passed")),
                          feedback_ready=bool(fields.get("question_feedback")) and ledger.get("feedback_ready_count") == ledger["count"], result_at=now())
            if ledger["feedback_ready"]:
                self.checkpoint["feedback"][key] = fields.get("feedback_text", "")[:16000]
            self.course["message"] = (f"Тест: попытка {ledger['count']}. " +
                                      ("Проход подтверждён." if ledger["passed"] else "Разбор ошибок перед повтором."))
        if kind == "groq_limits" and isinstance(fields.get("limits"), dict):
            self.store.data["limits"] = safe_limits(fields["limits"])
        self.store.save()

    async def process_module(self, number, roster):
        print(f"МОДУЛЬ {number}: проверка и выполнение {len(roster)} материалов", flush=True)
        record = {"verified_complete":False, "items":{}, "roster":roster}
        self.data["modules"][str(number)] = record
        self.checkpoint["modules"][str(number)] = record
        self.store.update(self.course["id"], current_module=number, message="Проверка модуля")
        self.store.event("module_started", f"Модуль {number}", self.course["id"])
        def supported(item):
            return (not item.get("unsupported") and
                    item["key"].split(":", 1)[0] in {"supplement", "lecture", "assignment", "ungradedLab", "coach"})

        anchor = next((item for item in roster if supported(item) and engine.item_key(item["url"])), None)
        if anchor is None and any(supported(item) for item in roster):
            raise engine.StopRun("A supported item has no validated lesson URL for the final completion check.")
        for item_index, item in enumerate(roster, 1):
            print(f"МАТЕРИАЛ {item_index}/{len(roster)}: {item['title']}", flush=True)
            self.store.update(self.course["id"], current_item=item["title"], message="Проверка материала")
            self.store.event("item_started", item["title"], self.course["id"])
            record["items"][item["key"]] = {"title":item["title"], "verified_complete":False}
            already_completed = await self.verified_item(item)
            if already_completed:
                print("Уже выполнен: подтверждено на сайте", flush=True)
            else:
                if not supported(item):
                    record["items"][item["key"]]["manual_required"] = True
                    reason = item.get("unsupported_reason", "No automatic handler for this lesson type")
                    if item["key"].startswith("programming:"):
                        await self.open_item(item)
                        try:
                            reason = await asyncio.wait_for(diagnose_programming(self, item), timeout=600)
                        except (engine.StopRun, TimeoutError) as error:
                            reason = "Programming Assignment inspection needs manual review: " + str(error)
                            self.store.event("programming_inspection_attention", reason, self.course["id"])
                        self.course["message"] = reason
                        self.store.event("programming_manual_review", reason, self.course["id"])
                        print("PROGRAMMING_REVIEW:", reason, flush=True)
                    record["items"][item["key"]]["reason"] = reason
                    self.store.event("item_manual_required", item["title"], self.course["id"])
                    print("МАТЕРИАЛ ТРЕБУЕТ РУЧНОГО ВЫПОЛНЕНИЯ:", item["title"], flush=True)
                    continue
                kind = item["key"].split(":", 1)[0]
                await self.open_item(item)
                if not await self.verified_item(item):
                    operation, timeout = {"supplement":(self.reading, 120), "lecture":(self.video, 180),
                                          "assignment":(self.quiz, None), "ungradedLab":(self.lab, 240),
                                          "coach":(self.dialogue, 1300)}[kind]
                    await asyncio.wait_for(operation(item), timeout=timeout)
                if not await self.verified_item(item, wait=10):
                    raise engine.StopRun("Coursera did not show a green completion mark after this lesson: "
                                         + item["title"])
            record["items"][item["key"]]["verified_complete"] = True
            self.update_counts()
            self.store.event("item_completed", item["title"], self.course["id"])
        # Re-read every mark after navigation, not just the action result. An
        # unsupported lesson can still be site-completed, but it cannot make
        # this module green unless the final outline shows every roster row.
        manual = [item["title"] for item in roster
                  if record["items"][item["key"]].get("manual_required")]
        if anchor is not None:
            # The item loop may already have a fully rendered, green outline.
            # Reopening its first video can temporarily unmount all sidebar
            # links while media loads, so inspect the current page first.
            try:
                rows = engine.rows_by_key(await self.snapshot())
            except engine.StopRun as exc:
                if not str(exc).startswith("The selected tab left the course: "):
                    raise
                # A completion overlay may have replaced the lesson. The
                # previous workflow reopened its anchor in this situation.
                rows = {}
            pending = [item["title"] for item in roster if supported(item) and
                       not (rows.get(item["key"], {}).get("status") == "completed" and
                            rows.get(item["key"], {}).get("checked") is True)]
            if pending:
                await self.open_item(anchor)
                rows = engine.rows_by_key(await self.snapshot())
                pending = [item["title"] for item in roster if supported(item) and
                           not (rows.get(item["key"], {}).get("status") == "completed" and
                                rows.get(item["key"], {}).get("checked") is True)]
                if pending:
                    raise engine.StopRun("Persisted completion is missing: " + "; ".join(pending))
        elif not manual:
            rows = engine.rows_by_key(await self.snapshot())
        else:
            rows = {}
        if manual:
            record["manual_required"] = manual
            self.store.event("module_manual_required", "; ".join(manual), self.course["id"])
            print(f"МОДУЛЬ {number}: поддерживаемые материалы проверены; вручную: " + "; ".join(manual), flush=True)
            self.update_counts()
            return
        missing = [item["title"] for item in roster
                   if not (rows.get(item["key"], {}).get("status") == "completed" and
                           rows.get(item["key"], {}).get("checked") is True)]
        if missing:
            record["outline_unverified"] = missing
            self.store.event("module_outline_unverified", "; ".join(missing), self.course["id"])
            self.update_counts()
            return
        record["verified_complete"] = True
        self.update_counts()
        self.store.event("module_completed", f"Модуль {number} подтверждён", self.course["id"])
        print(f"МОДУЛЬ {number}: завершение подтверждено", flush=True)

    async def lab(self, item):
        await complete_lab(self.tab, item, self.course["url"],
                           verify_completion=self.verified_item, event=self.event)

    async def dialogue(self, item):
        await run_dialogue(self, item)

    def update_counts(self):
        modules = self.checkpoint["modules"].values()
        self.store.update(self.course["id"],
            modules_completed=sum(bool(m.get("verified_complete")) for m in modules),
            items_completed=sum(bool(i.get("verified_complete"))
                                for m in modules for i in m.get("items", {}).values()))


async def heartbeat(store):
    while True:
        store.save()
        await asyncio.sleep(10)


def _configured_course_ids(store, args):
    """Apply monitor edits only between courses, preserving started history."""
    course_file = getattr(args, "courses", None)
    if course_file is None:
        return [course["id"] for course in store.data["courses"]]
    configured = load_courses(course_file)
    active = {course["id"]:course for course in configured}
    known = {course["id"]:course for course in store.data["courses"]}
    ordered = []
    additions, removals = [], []
    for config in configured:
        record = known.get(config["id"])
        if record is None:
            record = {"status":"pending", "modules_total":None, "modules_completed":0,
                      "modules_discovered":0, "inventory_complete":False,
                      "items_total":0, "items_completed":0, "current_module":None,
                      "current_item":None, "message":"", "checkpoint":{}, **config}
            additions.append(config["id"])
        else:
            record.update(config)
            if record.get("source") == "my_learning" and record.get("my_learning_title"):
                record["title"] = record["my_learning_title"]
            record.pop("removed_from_queue", None)
        ordered.append(record)
    for record in store.data["courses"]:
        if record["id"] in active:
            continue
        if not record.get("removed_from_queue"):
            removals.append(record["id"])
        if record.get("status") != "pending" or record.get("run_directory"):
            record["removed_from_queue"] = True
            ordered.append(record)
    if [row["id"] for row in ordered] != [row["id"] for row in store.data["courses"]] or additions or removals:
        store.data["courses"] = ordered
        for identity in additions:
            store.event("course_added", "Курс добавлен в очередь", identity)
        for identity in removals:
            store.event("course_removed", "Курс удалён из очереди", identity)
    return [course["id"] for course in configured]


def _append_my_learning_course(store, args, discovered):
    """Durably add one site-observed course without losing monitor edits."""
    from progress_monitor import atomic_text
    identity, url = course_identity(discovered["url"])
    if identity != discovered.get("id"):
        raise engine.StopRun("My Learning returned an inconsistent course link.")
    path = Path(args.courses)
    if path.suffix.lower() != ".txt":
        raise engine.StopRun("Automatic My Learning discovery needs a text courses.txt queue.")
    with RunLock(ROOT / "state" / "courses.lock"):
        configured = load_courses(path)
        if any(row["id"] == identity for row in configured):
            # The monitor may have added this course during the full page
            # scan. Refresh the in-memory queue before using its saved state.
            _configured_course_ids(store, args)
            return False
        if len(configured) >= MAX_COURSES:
            raise engine.StopRun(f"The course queue has reached its {MAX_COURSES}-course limit.")
        source = path.read_text(encoding="utf-8-sig")
        content = source + ("\n" if source and not source.endswith(("\n", "\r")) else "") + url + "\n"
        atomic_text(path, content)
    _configured_course_ids(store, args)
    record = store.course(identity)
    title = discovered.get("title")
    if isinstance(title, str) and title.strip():
        record["my_learning_title"] = title.strip()[:300]
        record["title"] = record["my_learning_title"]
    record["source"] = "my_learning"
    store.event("course_discovered", record["title"], identity)
    print("COURSE_DISCOVERED:", record["title"], url, flush=True)
    return True


def _record_sidebar_completion(runner, number, roster):
    record = {"verified_complete":True, "source":"live_sidebar", "roster":roster,
              "items":{item["key"]:{"title":item["title"], "verified_complete":True,
                                   "evidence":item.get("label", "")}
                       for item in roster}}
    runner.data["modules"][str(number)] = record
    runner.checkpoint["modules"][str(number)] = record
    runner.update_counts()
    runner.event("module_verified_from_sidebar", module=number, count=len(roster))
    runner.store.event("module_completed", f"Модуль {number}: все материалы имеют зелёные отметки", runner.course["id"])


def _mark_course_completed(store, runner, course, proof):
    if (not isinstance(proof, dict) or proof.get("verified") is not True or
            proof.get("method") != "certificate" or proof.get("course_id") != course["id"]):
        raise engine.StopRun("A matching certificate page must be verified before completing this course.")
    runner.data.update(status="completed", course_completion=proof)
    fields = {"status":"completed", "current_item":None,
              "message":"Сертификат курса подтверждён сайтом",
              "completion_evidence":proof.get("evidence", []),
              "completion_method":"certificate", "certificate_verified_at":now()}
    store.update(course["id"], **fields)
    store.event("course_completed", course["title"], course["id"])
    store.data["current_course"] = None


async def _remember_visible_certificate(store, tab, course):
    if not hasattr(tab, "evaluate"):
        return None
    certificate_url = await observed_certificate_url(tab, course["id"], course.get("title"))
    if certificate_url:
        course["certificate_page_url"] = certificate_url
        store.save()
    return certificate_url


async def _remember_completed_certificate(store, tab, course):
    """Recover a certificate that Coursera now shows only in My Learning."""
    if not hasattr(tab, "evaluate"):
        return None
    try:
        certificate_url = await find_completed_certificate(tab, course["id"], course.get("title"))
    except (TimeoutError, OSError, RuntimeError) as exc:
        store.event("certificate_lookup_attention", str(exc)[:500], course["id"])
        return None
    if certificate_url:
        course["certificate_page_url"] = certificate_url
        store.save()
        store.event("certificate_found_in_completed", course["title"], course["id"])
    return certificate_url


def _all_discovered_items_confirmed(course):
    total_modules = course.get("modules_total")
    total_items = course.get("items_total")
    return (course.get("inventory_complete") is True and
            type(total_modules) is int and total_modules > 0 and
            type(total_items) is int and total_items > 0 and
            course.get("modules_discovered") == total_modules and
            course.get("modules_completed") == total_modules and
            course.get("items_completed") == total_items)


async def _wait_for_completed_certificate(store, tab, course):
    """Allow Coursera time to publish a certificate after the last green item."""
    checks = CERTIFICATE_RECHECKS if _all_discovered_items_confirmed(course) else 1
    for attempt in range(checks):
        found = await _remember_completed_certificate(store, tab, course)
        if found:
            return found
        if attempt + 1 < checks:
            if attempt == 0:
                store.update(course["id"], message="Ожидание сертификата Coursera после завершения материалов")
                store.event("certificate_wait", "Все материалы подтверждены; ожидаем выдачу сертификата", course["id"])
            await asyncio.sleep(CERTIFICATE_RETRY_DELAY)
    return None


async def _verify_course_certificate(store, tab, course, certificate_url=None):
    certificate_url = certificate_url or course.get("certificate_page_url")
    if not valid_certificate_url(certificate_url):
        raise engine.StopRun("A matching Coursera certificate page is not available yet; this course remains unfinished.")
    opened = await open_certificate_page(tab, certificate_url)
    if opened != certificate_url:
        raise engine.StopRun("The matching Coursera certificate page could not be opened.")
    store.event("certificate_opened", course["title"], course["id"])
    proof = await verify_saved_certificate(tab, certificate_url, course["id"], course.get("title"))
    if (not isinstance(proof, dict) or proof.get("verified") is not True or
            proof.get("method") != "certificate" or proof.get("course_id") != course["id"]):
        raise engine.StopRun("The certificate page did not verify completion of this course.")
    return proof


async def _scan_with_retry(store, tab, excluded):
    """Retry only transient reads of every My Learning page, without opening a course."""
    for attempt in range(3):
        try:
            return await scan_in_progress(tab, excluded)
        except Exception as exc:
            message = str(exc).lower()
            disconnected = any(part in message for part in (
                "no close frame", "connection closed", "websocket connection is closed"))
            transient = (isinstance(exc, (TimeoutError, OSError)) or
                         disconnected or
                         any(part in message for part in (
                             "stable course list", "pagination changed", "page did not load",
                             "chrome runtime.evaluate", "navigation failed")))
            if not transient or attempt == 2:
                raise
            store.event("my_learning_retry", f"Повтор чтения My Learning ({attempt + 1}/2): {exc}")
            if disconnected and hasattr(tab, "connect"):
                await tab.connect()
            await asyncio.sleep(2 ** attempt)


async def _open_my_learning_with_retry(store, tab, course_id):
    """Retry a course-list hydration failure after a verified certificate."""
    for attempt in range(3):
        try:
            return await open_my_learning(tab)
        except Exception as exc:
            message = str(exc).lower()
            disconnected = any(part in message for part in (
                "no close frame", "connection closed", "websocket connection is closed"))
            transient = (isinstance(exc, (TimeoutError, OSError)) or
                         disconnected or
                         any(part in message for part in (
                             "stable course list", "page did not load",
                             "chrome runtime.evaluate", "navigation failed")))
            if not transient or attempt == 2:
                raise
            store.event("my_learning_retry", f"Повтор загрузки My Learning ({attempt + 1}/2): {exc}", course_id)
            if disconnected and hasattr(tab, "connect"):
                await tab.connect()
            await asyncio.sleep(2 ** attempt)


async def _next_course_id(store, args, tab, processed, auto_my_learning):
    """Open the selected In Progress card before considering local fallbacks.

    Automatic startup does not browse saved certificates. The current course's
    certificate is verified only after its outline has been processed.
    """
    active_ids = _configured_course_ids(store, args)
    remaining = [identity for identity in active_ids if identity not in processed]
    if auto_my_learning:
        # Coursera can reorder or update a card between the complete inventory
        # and the final click. In that case select again from all pages instead
        # of trusting an old page number or accidentally opening another card.
        for selection_attempt in range(3):
            if selection_attempt:
                active_ids = _configured_course_ids(store, args)
                remaining = [identity for identity in active_ids if identity not in processed]
            selected = await _scan_with_retry(store, tab, set(processed))
            store.event("my_learning_scanned", "Все страницы In Progress прочитаны; " +
                        ("выбран " + selected["id"] if selected else "незавершённых карточек не найдено"))
            if selected is None:
                break
            identity, url = course_identity(selected.get("url", ""))
            if identity != selected.get("id"):
                raise engine.StopRun("My Learning returned an inconsistent course link.")
            if identity not in active_ids:
                _append_my_learning_course(store, args, selected)
                # A course removed from courses.txt may still be In Progress on
                # Coursera. Rejoining it activates its old Submit markers, so
                # reconcile them before clicking its course card.
                await _recover_visible_optional_reflection(store, args, tab)
                await recover_pending_quizzes(tab, store, root=ROOT)
            elif store.course(identity)["url"] != url:
                raise engine.StopRun("My Learning returned a different link for a configured course.")
            try:
                discovered = await open_scanned_course(tab, selected)
            except MyLearningSelectionChanged as exc:
                if selection_attempt == 2:
                    raise RuntimeError("My Learning changed during three course selections: " + str(exc)) from exc
                store.event("my_learning_retry",
                            f"Карточка курса обновилась; повторное чтение всех страниц ({selection_attempt + 1}/2): {exc}",
                            identity)
                await asyncio.sleep(selection_attempt + 1)
                continue
            opened_id, opened_url = course_identity(discovered.get("url", ""))
            if opened_id != identity or opened_url != url or discovered.get("id") != identity:
                raise engine.StopRun("My Learning returned an inconsistent course link.")
            return identity
        # The site has no actionable course. Start only a genuinely new local
        # entry; an old checkpoint or saved certificate is not a startup task.
        for identity in remaining:
            course = store.course(identity)
            checkpoint = course.get("checkpoint")
            if (course.get("status") == "pending" and course.get("previous_status", "pending") == "pending" and
                    not valid_certificate_url(course.get("certificate_page_url")) and
                    not course.get("run_directory") and course.get("current_module") is None and
                    not any(type(course.get(field)) is int and course[field] > 0
                            for field in ("modules_discovered", "items_completed")) and
                    (not isinstance(checkpoint, dict) or not any(checkpoint.get(field)
                        for field in ("modules", "attempts", "feedback", "answer_history")))):
                return identity
        return None
    for identity in remaining:
        course = store.course(identity)
        if course.get("previous_status") == "completed":
            continue
        checkpoint = course.get("checkpoint")
        saved_work = isinstance(checkpoint, dict) and any(
            bool(checkpoint.get(field)) for field in ("modules", "attempts", "feedback", "answer_history"))
        saved_counts = any(type(course.get(field)) is int and course[field] > 0
                           for field in ("modules_discovered", "items_completed"))
        if (course.get("previous_status") in {"running", "attention"} or
                course.get("current_module") is not None or saved_counts or saved_work or
                (course.get("run_directory") and course.get("previous_status") != "completed")):
            if not auto_my_learning:
                return identity
    for identity in remaining:
        if valid_certificate_url(store.course(identity).get("certificate_page_url")):
            return identity
    return remaining[0] if remaining else None


def _saved_reflection_item(course, key, attempt_url):
    """Find the exact current lesson in saved roster evidence, without browsing."""
    def matches(item):
        return (isinstance(item, dict) and item.get("key") == key and
                isinstance(item.get("url"), str) and
                attempt_url == item["url"].rstrip("/") + "/attempt")

    checkpoint = course.get("checkpoint", {})
    modules = checkpoint.get("modules", {}) if isinstance(checkpoint, dict) else {}
    rosters = [record.get("roster", []) for record in modules.values()
               if isinstance(record, dict) and isinstance(record.get("roster"), list)]
    found = [item for roster in rosters for item in roster if matches(item)]
    if len(found) == 1:
        return found[0]
    if found:
        return None

    # Module records are rebuilt from the live sidebar on each run. An error
    # before process_module can leave them empty, while the module_roster event
    # remains durable in the previous run's append-only log.
    previous = course.get("run_directory")
    if not isinstance(previous, str):
        return None
    directory = Path(previous)
    if directory.resolve().parent != (ROOT / "logs").resolve():
        return None
    path = directory / "events.jsonl"
    if not path.is_file() or path.stat().st_size > 10_000_000:
        return None
    try:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError):
        return None
    for event in reversed(events):
        if not isinstance(event, dict) or event.get("event") != "module_roster":
            continue
        roster = event.get("roster")
        if not isinstance(roster, list):
            continue
        found = [item for item in roster if matches(item)]
        if len(found) == 1:
            return found[0]
        if found:
            return None
    return None


async def _recover_visible_optional_reflection(store, args, tab):
    """Preserve a pending Submit modal before certificate or roster navigation."""
    saved = pending_reflections(store)
    if not hasattr(tab, "evaluate"):
        if saved:
            raise engine.StopRun("A saved optional reflection Submit cannot be checked: Chrome is unavailable.")
        return False
    location = await tab.evaluate("({url:location.href})")
    url = location.get("url", "") if isinstance(location, dict) else ""
    if len(saved) > 1:
        raise engine.StopRun("Multiple optional reflection submissions remain unresolved.")
    if saved:
        marker = saved[0]
        base = marker["url"].rstrip("/")
        if not re.fullmatch(re.escape(base) + r"/attempt(?:/view-feedback)?/?", url):
            error = engine.StopRun("The saved optional reflection attempt is not open in Chrome; "
                                   "its Submit result remains unresolved.")
            error.course_id, error.item_key = marker["course_id"], marker["key"]
            raise error
        course = store.course(marker["course_id"])
        item = _saved_reflection_item(course, marker["key"], base + "/attempt")
        if item is None:
            raise engine.StopRun("The saved optional reflection is missing its exact course roster row.")
        engine.configure_course(course["url"])
        directory = ROOT / "logs" / ("queue_recovery_" + course["id"] + "_" +
                                      engine.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        directory.mkdir(parents=True)
        runner = QueueRunner(tab, args, directory, store, course)
        try:
            recovered = await recover_pending_reflection(runner, item)
            if not recovered:
                raise engine.StopRun("The saved optional reflection Submit has no confirmed result yet.")
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            store.update(course["id"], status="attention", current_item=item["key"], message=message[:900])
            store.event("course_attention", message, course["id"])
            print("COURSE_ATTENTION:", course["id"], message, flush=True)
            raise
        store.event("reflection_preflight_recovered", item["title"], course["id"])
        return True
    parsed = engine.urlsplit(url)
    match = re.fullmatch(
        r"/learn/([A-Za-z0-9_-]+)/assignment-submission/([A-Za-z0-9_-]+)/[A-Za-z0-9_-]+/attempt/?",
        parsed.path)
    if (parsed.scheme != "https" or parsed.hostname not in {"coursera.org", "www.coursera.org"}
            or not match):
        return False
    course = next((row for row in store.data["courses"]
                   if row["id"] == match.group(1) and not row.get("removed_from_queue")), None)
    if course is None:
        return False
    key = "assignment:" + match.group(2)
    item = _saved_reflection_item(course, key, url.rstrip("/"))
    if item is None:
        return False
    engine.configure_course(course["url"])
    directory = ROOT / "logs" / ("queue_recovery_" + course["id"] + "_" +
                                  engine.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    directory.mkdir(parents=True)
    runner = QueueRunner(tab, args, directory, store, course)
    try:
        recovered = await recover_pending_reflection(runner, item)
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        store.update(course["id"], status="attention", current_item=item["key"], message=message[:900])
        store.event("course_attention", message, course["id"])
        print("COURSE_ATTENTION:", course["id"], message, flush=True)
        raise
    if recovered:
        store.event("reflection_preflight_recovered", item["title"], course["id"])
    return recovered


async def run_queue(store, args, tab):
    auto_my_learning = bool(getattr(args, "auto_my_learning", False))
    discovery_complete = True
    store.data.update(status="running", current_course=None)
    store.event("queue_started", "Очередь запущена; завершение будет сверено с сайтом.")
    pulse = asyncio.create_task(heartbeat(store))
    try:
        try:
            await _recover_visible_optional_reflection(store, args, tab)
            await recover_pending_quizzes(tab, store, root=ROOT)
        except Exception as exc:
            store.data.update(status="attention", current_course=None)
            message = str(exc) or type(exc).__name__
            course_id = getattr(exc, "course_id", None)
            if course_id and any(row["id"] == course_id for row in store.data["courses"]):
                store.update(course_id, status="attention",
                             current_item=getattr(exc, "item_key", None), message=message[:900])
            store.event("queue_attention", "Восстановление отправки: " + message)
            print("QUEUE_ATTENTION:", message, flush=True)
            return 2
        processed = set()
        while True:
            try:
                next_id = await _next_course_id(store, args, tab, processed, auto_my_learning)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                store.data.update(status="attention", current_course=None)
                store.event("queue_attention", "Не удалось однозначно прочитать очередь: " + message)
                print("QUEUE_ATTENTION:", message, flush=True)
                return 2
            if next_id is None:
                break
            processed.add(next_id)
            course = store.course(next_id)
            engine.configure_course(course["url"])
            from quiz_history_import import import_previous_history
            history = course.setdefault("checkpoint", {}).setdefault("answer_history", {})
            recovered = import_previous_history(ROOT, course.get("run_directory"), history)
            if recovered:
                print("QUIZ_HISTORY_RECOVERED:", recovered, "attempts", flush=True)
            directory = ROOT / "logs" / ("queue_" + course["id"] + "_" + engine.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            directory.mkdir(parents=True)
            store.data["current_course"] = course["id"]
            store.update(course["id"], status="running", run_directory=str(directory), message="Изучение структуры курса")
            store.event("course_started", course["title"], course["id"])
            runner = QueueRunner(tab, args, directory, store, course)
            course_completed = False
            try:
                if not auto_my_learning:
                    certificate_url = await _remember_visible_certificate(store, tab, course)
                    if (certificate_url and course.get("inventory_complete") is True and
                            type(course.get("modules_total")) is int and course["modules_total"] > 0 and
                            course.get("modules_discovered") == course["modules_total"]):
                        proof = await _verify_course_certificate(store, tab, course, certificate_url)
                        _mark_course_completed(store, runner, course, proof)
                        course_completed = True
                        runner.data["finished"] = now()
                        engine.save_json(directory / "progress.json", runner.data)
                        continue
                async with asyncio.timeout(args.minutes * 60) as course_timeout:
                    runner._course_timeout = course_timeout
                    expectation = course.get("modules")
                    if expectation is None and engine.MODULE_ITEM_COUNTS:
                        expectation = sorted(engine.MODULE_ITEM_COUNTS)
                    module_floor = _known_module_floor(course, ROOT / "logs")
                    if module_floor > course.get("modules_max_seen", 0):
                        store.update(course["id"], modules_max_seen=module_floor)
                    previous_rosters = {int(n):record.get("roster") for n,record in runner.checkpoint["modules"].items()
                                        if str(n).isdigit() and isinstance(record.get("roster"), list)}
                    sidebar = await inspect_sidebar_progress(tab, course["url"]) if hasattr(tab, "evaluate") else None
                    if sidebar and expectation is not None and sidebar["modules"] == expectation:
                        modules = sidebar["modules"]
                    else:
                        if module_floor > 1:
                            modules = await discover_modules(tab, course["url"], expectation,
                                                             minimum_modules=module_floor)
                        else:
                            modules = await discover_modules(tab, course["url"], expectation)
                        sidebar = await inspect_sidebar_progress(tab, course["url"]) if hasattr(tab, "evaluate") else None
                        if sidebar and sidebar["modules"] != modules:
                            sidebar = None
                        if sidebar is None and hasattr(tab, "navigate"):
                            cached_anchor = next((item.get("url") for number in modules
                                for item in (previous_rosters.get(number) or [])
                                if engine.item_key(item.get("url", ""))), None)
                            if cached_anchor:
                                await tab.navigate(cached_anchor)
                                sidebar = await inspect_sidebar_progress(tab, course["url"])
                                if sidebar and sidebar["modules"] != modules:
                                    sidebar = None
                    if len(modules) < module_floor:
                        raise engine.StopRun(
                            f"The live outline lists {len(modules)} modules, but earlier runs reached "
                            f"module {module_floor}. The course inventory may still be loading.")
                    store.update(course["id"], modules_max_seen=max(module_floor, len(modules)))
                    # Attempt/feedback ledgers stay durable; module progress is
                    # rebuilt only from live marks during this invocation.
                    runner.checkpoint["modules"] = {}
                    store.update(course["id"], modules_total=len(modules), modules_discovered=0,
                                 inventory_complete=False, items_total=0, items_completed=0,
                                 modules_completed=0, current_item=None)
                    discovered_items = 0
                    proof = None
                    for module_index, number in enumerate(modules, 1):
                        expected_count = engine.MODULE_ITEM_COUNTS.get(number)
                        verified = verified_sidebar_module(sidebar, number, expected_count,
                                                           previous_rosters.get(number))
                        if verified:
                            roster = verified
                            print(f"МОДУЛЬ {number} ({module_index}/{len(modules)}): зелёные отметки подтверждены", flush=True)
                        else:
                            print(f"МОДУЛЬ {number} ({module_index}/{len(modules)}): чтение списка материалов", flush=True)
                        store.update(course["id"], current_module=number, current_item=None,
                                     message=f"Проверка модуля {number}")
                        if not verified:
                            prior_roster = previous_rosters.get(number)
                            if prior_roster is None:
                                roster = await discover_roster(tab, course["url"], number, expected_count)
                            else:
                                roster = await discover_roster(tab, course["url"], number, expected_count,
                                                               prior_roster)
                            if sidebar is None and hasattr(tab, "navigate") and hasattr(tab, "evaluate"):
                                anchor = next((item.get("url") for item in roster
                                               if engine.item_key(item.get("url", ""))), None)
                                if anchor:
                                    await tab.navigate(anchor)
                                    sidebar = await inspect_sidebar_progress(tab, course["url"])
                                    if sidebar and sidebar["modules"] != modules:
                                        sidebar = None
                        if not roster:
                            raise engine.StopRun(f"Module {number} has no verified item list.")
                        discovered_items += len(roster)
                        store.update(course["id"], items_total=discovered_items,
                                     modules_discovered=module_index,
                                     inventory_complete=module_index == len(modules))
                        runner.event("module_roster", module=number, roster=roster)
                        if not verified:
                            verified = verified_sidebar_module(sidebar, number, expected_roster=roster)
                        if verified:
                            _record_sidebar_completion(runner, number, roster)
                        else:
                            if any(item.get("unsupported") for item in roster):
                                # A course may grant completion after its final
                                # graded test while an optional peer task remains.
                                # The site statement may skip an optional item,
                                # but a matching certificate is still required.
                                await _remember_visible_certificate(store, tab, course)
                                early = await verify_course_completion(tab, course["url"], modules)
                                if early.get("verified"):
                                    proof = early
                                    break
                            await runner.process_module(number, roster)
                        if sidebar is None and hasattr(tab, "evaluate") and module_index < len(modules):
                            sidebar = await inspect_sidebar_progress(tab, course["url"])
                            if sidebar and sidebar["modules"] != modules:
                                sidebar = None
                    verified_rosters = {int(n):record["roster"] for n,record in runner.checkpoint["modules"].items()
                                        if record.get("verified_complete") and isinstance(record.get("roster"), list)}
                    if proof is None:
                        await _remember_visible_certificate(store, tab, course)
                        proof = await verify_course_completion(tab, course["url"], modules, verified_rosters)
                    programming_reviews = [item.get("reason") for module in runner.checkpoint["modules"].values()
                        for key, item in module.get("items", {}).items()
                        if key.startswith("programming:") and item.get("manual_required") and item.get("reason")]
                    if (programming_reviews and not proof.get("verified") and
                            not valid_certificate_url(course.get("certificate_page_url"))):
                        raise engine.StopRun("Manual work still required: " + programming_reviews[0])
                    if auto_my_learning and not valid_certificate_url(course.get("certificate_page_url")):
                        await _wait_for_completed_certificate(store, tab, course)
                    if not valid_certificate_url(course.get("certificate_page_url")):
                        manual = [title for module in runner.checkpoint["modules"].values()
                                  for title in module.get("manual_required", [])]
                        reason = "A matching Coursera certificate page is not available yet."
                        if not proof.get("verified"):
                            reason += " " + str(proof.get("reason", "No explicit course completion evidence."))
                        if manual:
                            reason += " Manual work still required: " + "; ".join(manual)
                        raise engine.StopRun("All discovered modules were checked, but course completion is unconfirmed: " + reason)
                    certificate_proof = await _verify_course_certificate(store, tab, course)
                    _mark_course_completed(store, runner, course, certificate_proof)
                    course_completed = True
            except (asyncio.CancelledError, KeyboardInterrupt):
                runner.data.update(status="interrupted", stop_reason="Interrupted by user")
                store.update(course["id"], status="attention", message="Запуск прерван. Прогресс сохранён.")
                raise
            except Exception as exc:
                if course_completed:
                    store.event("course_transition_attention", str(exc)[:500], course["id"])
                    discovery_complete = False
                    break
                diagnostic = getattr(exc, "diagnostic", None)
                if isinstance(diagnostic, dict):
                    runner.event("discovery_error", diagnostic=diagnostic)
                message = str(exc) or type(exc).__name__
                if isinstance(exc, TimeoutError):
                    message = "Достигнут лимит ожидания или времени курса. Прогресс сохранён."
                runner.data.update(status="attention", stop_reason=message)
                store.update(course["id"], status="attention", message=message[:900])
                store.event("course_attention", message, course["id"])
                print("COURSE_ATTENTION:", course["id"], message, flush=True)
                break
            finally:
                runner._course_timeout = None
                runner.data["finished"] = now()
                engine.save_json(directory / "progress.json", runner.data)
            if course_completed and auto_my_learning:
                try:
                    await _open_my_learning_with_retry(store, tab, course["id"])
                except Exception as exc:
                    store.event("certificate_attention", str(exc)[:500], course["id"])
                    discovery_complete = False
                    break
        active_ids = _configured_course_ids(store, args)
        complete = discovery_complete and all(store.course(identity)["status"] == "completed" for identity in active_ids)
        store.data.update(status="completed" if complete else "attention", current_course=None)
        store.event("queue_completed" if complete else "course_attention",
                    "Все курсы завершены и проверены." if complete else "Есть незавершённые курсы; см. причину в мониторе.")
        return 0 if complete else 2
    except (asyncio.CancelledError, KeyboardInterrupt):
        store.data["status"] = "paused"
        store.event("queue_paused", "Остановлено пользователем; состояние сохранено.")
        raise
    finally:
        pulse.cancel()
        try:
            await pulse
        except asyncio.CancelledError:
            pass
        store.save()


def learning_tabs():
    return [p for p in engine.local_json(engine.CDP_URL + "/json/list")
            if p.get("type") == "page" and _learning_url(p.get("url", ""))]


def _preferred_learning_tabs(pages, store):
    current = next((course["id"] for course in store.data["courses"]
                    if not course.get("removed_from_queue") and
                    not valid_certificate_url(course.get("certificate_page_url"))), None)
    if current:
        matching = [page for page in pages if re.match(
            r"^/learn/" + re.escape(current) + r"(?:/|$)",
            engine.urlsplit(page.get("url", "")).path)]
        if len(matching) == 1:
            return matching
    return []


def _pending_submission_tabs(pages, store):
    """Prefer an exact open attempt over My Learning during recovery."""
    targets = []
    for marker in pending_reflections(store):
        targets.append(("reflection", marker["url"]))
    for course, key, ledger in _unresolved(store):
        if ledger.get("pending") and re.fullmatch(r"assignment:([A-Za-z0-9_-]+)", key):
            targets.append(("quiz", f"/learn/{course['id']}/assignment-submission/{key.split(':', 1)[1]}/"))
    chosen = []
    for kind, target in targets:
        if kind == "reflection":
            target = engine.urlsplit(target).path.rstrip("/")
            matches = [page for page in pages if re.fullmatch(
                re.escape(target) + r"/attempt(?:/view-feedback)?/?",
                engine.urlsplit(page.get("url", "")).path)]
        else:
            matches = [page for page in pages if engine.urlsplit(page.get("url", "")).path.startswith(target)]
        if len(matches) > 1:
            raise engine.StopRun("Several Chrome tabs show the same unresolved assignment; select one exact result tab.")
        chosen.extend(matches)
    if len(chosen) > 1:
        raise engine.StopRun("Several different unresolved assignments are open; close all but one result tab.")
    return chosen


def my_learning_tabs():
    return [p for p in engine.local_json(engine.CDP_URL + "/json/list")
            if p.get("type") == "page" and _my_learning_url(p.get("url", ""))]


def _my_learning_url(url):
    parts = engine.urlsplit(url)
    return (parts.scheme == "https" and parts.hostname in {"www.coursera.org", "coursera.org"}
            and parts.path == "/my-learning")


def _learning_url(url):
    parts = engine.urlsplit(url)
    return parts.scheme == "https" and parts.hostname in {"www.coursera.org", "coursera.org"} and parts.path.startswith("/learn/")


def _wait_for_start_tab(seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if learning_tabs() or my_learning_tabs():
            return
        time.sleep(0.5)
    raise engine.StopRun("Chrome открылся, но вкладка Coursera не появилась за 20 секунд.")


async def _show_my_learning_start_page(*, allow_site_redirect=False):
    """Show In Progress without disturbing a pending Submit tab.

    Before the login prompt, a Coursera sign-in redirect is allowed. After
    Enter, the caller requires the exact In Progress page.
    """
    metadata = engine.local_json(engine.CDP_URL + "/json/version")
    address = metadata.get("webSocketDebuggerUrl") if isinstance(metadata, dict) else None
    if not isinstance(address, str):
        raise engine.StopRun("Chrome did not expose its managed browser target.")
    owner = engine.ChromeTab(address)
    await owner.connect()
    try:
        pages = sorted(my_learning_tabs(), key=lambda page: page.get("id", ""))
        if not pages and not learning_tabs():
            # A newly launched Chrome target may still be about:blank while
            # its requested My Learning URL is loading.
            for _ in range(10):
                await asyncio.sleep(0.5)
                pages = sorted(my_learning_tabs(), key=lambda page: page.get("id", ""))
                if pages:
                    break
        exact = next((page for page in pages if page.get("url") == MY_LEARNING_URL), None)
        target = exact or next((page for page in pages
                                if isinstance(page.get("webSocketDebuggerUrl"), str)), None)
        if target is not None:
            target_id = target.get("id")
            if not isinstance(target_id, str) or not target_id:
                raise engine.StopRun("The My Learning tab has no Chrome target ID.")
            if exact is None:
                tab = engine.ChromeTab(target["webSocketDebuggerUrl"])
                await tab.connect()
                try:
                    result = await tab.call("Page.navigate", {"url": MY_LEARNING_URL})
                    if result.get("errorText"):
                        raise engine.StopRun("My Learning navigation failed: " + str(result["errorText"]))
                finally:
                    await tab.close()
        else:
            result = await owner.call("Target.createTarget", {"url": MY_LEARNING_URL})
            target_id = result.get("targetId")
            if not isinstance(target_id, str) or not target_id:
                raise engine.StopRun("Chrome did not create a My Learning tab.")
        await owner.call("Target.activateTarget", {"targetId": target_id})
        for probe in range(40):
            if any(page.get("id") == target_id and page.get("url") == MY_LEARNING_URL
                   for page in my_learning_tabs()):
                return target_id
            if allow_site_redirect and probe >= 20:
                targets = engine.local_json(engine.CDP_URL + "/json/list")
                current = next((page.get("url", "") for page in targets
                                if page.get("type") == "page" and page.get("id") == target_id), "")
                parsed = engine.urlsplit(current)
                if parsed.scheme == "https" and parsed.hostname in {"www.coursera.org", "coursera.org"}:
                    return target_id
            await asyncio.sleep(0.5)
        raise engine.StopRun("My Learning In Progress did not open. Sign in to Coursera in the managed Chrome window and retry.")
    finally:
        await owner.close()


async def session(store, args):
    course_pages = learning_tabs()
    learning_pages = sorted(my_learning_tabs(),
                            key=lambda row: ("myLearningTab=IN_PROGRESS" not in row.get("url", ""),
                                             row.get("id", "")))
    try:
        pending_pages = _pending_submission_tabs(course_pages, store)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        store.data.update(status="attention", current_course=None)
        store.event("queue_attention", "Не удалось выбрать вкладку для сохранённой отправки: " + message)
        print("QUEUE_ATTENTION:", message, flush=True)
        return 2
    if pending_pages:
        pages = pending_pages
    elif getattr(args, "auto_my_learning", True):
        pages = learning_pages or _preferred_learning_tabs(course_pages, store)
    else:
        pages = _preferred_learning_tabs(course_pages, store) or learning_pages
    if not pages and course_pages:
        # The automatic route immediately navigates its selected tab to My
        # Learning. Other Coursera tabs can remain open, including old labs.
        pages = sorted(course_pages, key=lambda row: (row.get("url", ""), row.get("id", "")))
    if not pages:
        raise engine.StopRun("Откройте вкладку курса или My Learning в отдельном окне Chrome и войдите в Coursera.")
    last_error = None
    for page in pages:
        tab = engine.ChromeTab(page["webSocketDebuggerUrl"])
        try:
            await tab.connect()
            await asyncio.wait_for(tab.evaluate("({url:location.href})"), timeout=8)
        except Exception as exc:
            last_error = exc
            await tab.close()
            continue
        try:
            return await run_queue(store, args, tab)
        finally:
            await tab.close()
    raise engine.StopRun("Все подходящие вкладки Coursera не отвечают через Chrome: " + str(last_error))


def main():
    parser = argparse.ArgumentParser(description="Очередь курсов Coursera с сохранением прогресса")
    parser.add_argument("--courses", type=Path, default=ROOT / "courses.txt")
    parser.add_argument("--minutes", type=int, default=180, help="Лимит минут на один курс, включая загрузку структуры")
    parser.add_argument("--max-steps", type=int, default=60, help="Лимит действий в одной попытке теста")
    parser.add_argument("--quiz-attempts", type=int, default=0, help="0: повторять до прохода; 1..100: общий лимит попыток на тест, включая прошлые запуски")
    parser.add_argument("--continue-on-attention", action="store_true",
                        help="Флаг совместимости: при отсутствии подтверждённого сертификата переход к следующему курсу запрещён")
    parser.add_argument("--validate", action="store_true", help="Только проверить список, без браузера и API")
    parser.add_argument("--monitor", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Начать без ожидания Enter после проверки браузера")
    parser.add_argument("--manual-queue", action="store_false", dest="auto_my_learning",
                        help="Выполнять только courses.txt, без поиска курсов во вкладке In Progress")
    parser.set_defaults(auto_my_learning=True)
    args = parser.parse_args()
    if not 1 <= args.minutes <= 1440 or not 1 <= args.max_steps <= 200 or not 0 <= args.quiz_attempts <= 100:
        parser.error("minutes:1..1440, max-steps:1..200, quiz-attempts:0..100 (0 = до прохода)")
    courses = load_courses(args.courses)
    if args.validate:
        for index, course in enumerate(courses, 1):
            print(index, course["url"])
        return 0
    if sys.platform != "win32":
        raise RuntimeError("Запускайте очередь на этом Windows-компьютере.")
    with RunLock(ROOT / "state" / "runner.lock"), reported_store(ROOT / "state" / "queue.json", courses) as store:
        from configure_new_pc import load_notice
        engine.configure_course(courses[0]["url"], notice=load_notice())
        engine.groq_key()
        engine.ensure_browser(start_url=MY_LEARNING_URL)
        asyncio.run(_show_my_learning_start_page(allow_site_redirect=True))
        if args.monitor:
            subprocess.Popen([sys.executable, str(ROOT / "progress_monitor.py")],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print("RUNNER_VERSION:", engine.RUNNER_VERSION)
        print("Повторы тестов:", "до проходного результата Coursera" if args.quiz_attempts == 0 else f"до {args.quiz_attempts} попыток всего")
        print(f"Курсов в списке: {len(courses)}. Далее: My Learning → In Progress, все страницы.")
        if not args.yes and input("Войдите в Coursera, затем нажмите Enter для запуска или q для выхода: ").strip().lower() == "q":
            return 0
        asyncio.run(_show_my_learning_start_page())
        _wait_for_start_tab()
        set_state = ctypes.windll.kernel32.SetThreadExecutionState
        set_state.argtypes, set_state.restype = [ctypes.c_uint], ctypes.c_uint
        previous = set_state(0x80000001)
        try:
            return asyncio.run(session(store, args))
        finally:
            if previous:
                set_state(previous)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Остановлено. Сохранённое состояние будет проверено при следующем запуске.")
        raise SystemExit(130)
    except Exception as exc:
        print("QUEUE_ERROR:", str(exc) or type(exc).__name__)
        raise SystemExit(2)
