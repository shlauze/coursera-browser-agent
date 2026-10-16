"""Validated course lists, atomic checkpoints and a process-wide run lock."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

MAX_COURSES = 500


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_limits(value):
    """Keep only the numeric Groq counters and their observation time."""
    if not isinstance(value, dict):
        return {}
    numeric = {"groq_requests_remaining", "groq_requests_limit", "groq_tokens_remaining",
               "groq_tokens_limit", "groq_retry_after_seconds"}
    result = {name:number for name,number in value.items()
              if name in numeric and type(number) in (int, float)
              and math.isfinite(number) and number >= 0}
    stamp = value.get("groq_updated_at")
    if isinstance(stamp, str) and len(stamp) <= 80:
        try:
            datetime.fromisoformat(stamp)
        except ValueError:
            pass
        else:
            result["groq_updated_at"] = stamp
    return result


def course_identity(url):
    parsed = urlsplit(str(url).strip())
    if (parsed.scheme != "https" or parsed.hostname not in {"coursera.org", "www.coursera.org"}
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise ValueError("Нужна HTTPS-ссылка на курс Coursera /learn/название-курса.")
    match = re.fullmatch(r"/learn/([a-z0-9][a-z0-9-]{0,159})/?", parsed.path)
    if not match:
        raise ValueError("Укажите страницу самого курса /learn/название, а не урок или специализацию.")
    slug = match[1]
    return slug, "https://www.coursera.org/learn/" + slug


def load_courses(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        raw = raw.get("courses") if isinstance(raw, dict) else raw
    else:
        raw = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_COURSES:
        raise ValueError(f"В списке должно быть от 1 до {MAX_COURSES} курсов.")
    result, seen = [], set()
    for entry in raw:
        if isinstance(entry, str):
            entry = {"url":entry}
        if not isinstance(entry, dict):
            raise ValueError("Каждый курс должен быть ссылкой или объектом с полем url.")
        slug, url = course_identity(entry.get("url", ""))
        if slug in seen:
            raise ValueError("Курс повторяется в списке: " + slug)
        seen.add(slug)
        modules = entry.get("modules")
        if modules is not None and (not isinstance(modules, list) or not modules or
                any(type(n) is not int or not 1 <= n <= 200 for n in modules) or
                modules != list(range(1, max(modules) + 1))):
            raise ValueError("modules должен содержать все номера модулей от 1, без пропусков.")
        result.append({"id":slug, "url":url, "title":str(entry.get("title") or slug)[:300],
                       "modules":modules})
    return result


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers/scanners can briefly deny rename/delete sharing.
        # Retry replacement of the same fully flushed file; never truncate the
        # old checkpoint or acknowledge an unsaved attempt reservation.
        for attempt in range(24):
            try:
                os.replace(temporary, path)
                return
            except OSError as error:
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 23:
                    raise
                time.sleep(min(0.025 * 2 ** attempt, 0.25))
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Cleanup cannot replace the actual write/replace failure.
                pass


class RunLock:
    """An OS lock is released after a crash; a stale PID is never authority."""
    def __init__(self, path):
        self.path, self.stream = Path(path), None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise RuntimeError("Скрипт уже запущен. Остановите прежний запуск перед новым.") from None
        self.stream = stream
        return self

    def __exit__(self, *unused):
        if self.stream:
            self.stream.close()
            self.stream = None


class QueueStore:
    def __init__(self, path, courses):
        self.path = Path(path)
        self._checkpoint_cache = {}
        previous = {}
        if self.path.exists():
            previous = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(previous, dict) or previous.get("schema_version") != 1:
                raise ValueError("Неизвестная версия состояния очереди. Сохраните файл и проверьте его.")
        if (not isinstance(previous.get("courses", []), list) or
                any(not isinstance(c, dict) or not isinstance(c.get("id"), str) for c in previous.get("courses", [])) or
                not isinstance(previous.get("events", []), list)):
            raise ValueError("Повреждена структура состояния очереди; исходный файл сохранён.")
        known = {c["id"]:c for c in previous.get("courses", [])}
        self.data = {"schema_version":1, "status":"idle", "updated_at":now(),
                     "current_course":None, "courses":[], "events":previous.get("events", [])[-200:],
                     "limits":safe_limits(previous.get("limits"))}
        for config in courses:
            row = known.get(config["id"], {})
            prior = row.get("status", "pending")
            # A previously verified certificate is durable completion evidence.
            # The live In Progress list can still reopen this course if the
            # site presents an actionable card on a later run.
            row = {"status":"pending", "modules_total":None, "modules_completed":0,
                   "modules_discovered":0, "inventory_complete":False,
                   "items_total":0, "items_completed":0, "current_module":None,
                   "current_item":None, "message":"", "checkpoint":{}, **row, **config}
            if row.get("source") == "my_learning" and row.get("my_learning_title"):
                row["title"] = row["my_learning_title"]
            checkpoint_file = self.path.parent / "checkpoints" / (config["id"] + ".json")
            if checkpoint_file.exists():
                row["checkpoint"] = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            elif row.get("checkpoint_saved"):
                raise ValueError("Отсутствует сохранённый журнал попыток курса " + config["id"] + "; автоматический сброс запрещён.")
            validate_checkpoint(row["checkpoint"])
            row["status"] = ("completed" if prior == "completed" and
                             row.get("completion_method") == "certificate" and
                             isinstance(row.get("certificate_verified_at"), str) and
                             bool(row["certificate_verified_at"]) and
                             isinstance(row.get("certificate_page_url"), str) and
                             bool(row["certificate_page_url"]) else "pending")
            row["previous_status"] = prior
            if prior == "running":
                row["message"] = "Предыдущий запуск прерван; состояние сайта будет проверено заново."
            self.data["courses"].append(row)
        configured_ids = {config["id"] for config in courses}
        for historical in previous.get("courses", []):
            if historical["id"] in configured_ids:
                continue
            # A never-started removed course has no historical work to show.
            if historical.get("status") == "pending" and not historical.get("run_directory"):
                continue
            row = dict(historical)
            try:
                historical_id, _ = course_identity(row.get("url", ""))
            except ValueError:
                raise ValueError("Повреждена ссылка исторического курса.") from None
            if historical_id != row["id"]:
                raise ValueError("Повреждён идентификатор исторического курса.")
            checkpoint_file = self.path.parent / "checkpoints" / (row["id"] + ".json")
            if checkpoint_file.exists():
                row["checkpoint"] = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            elif row.get("checkpoint_saved"):
                raise ValueError("Отсутствует сохранённое состояние курса " + row["id"])
            else:
                row["checkpoint"] = {}
            validate_checkpoint(row["checkpoint"])
            row["removed_from_queue"] = True
            if row.get("status") == "running":
                row["status"] = "attention"
                row["message"] = "Предыдущий запуск прерван; курс удалён из очереди."
            self.data["courses"].append(row)
        self.save()

    def course(self, identity):
        return next(c for c in self.data["courses"] if c["id"] == identity)

    def save(self):
        self.data["updated_at"] = now()
        summaries = []
        for course in self.data["courses"]:
            checkpoint = course.get("checkpoint", {})
            encoded = json.dumps(checkpoint, ensure_ascii=False, sort_keys=True)
            if self._checkpoint_cache.get(course["id"]) != encoded:
                atomic_json(self.path.parent / "checkpoints" / (course["id"] + ".json"), checkpoint)
                self._checkpoint_cache[course["id"]] = encoded
            summaries.append({**{k:v for k,v in course.items() if k != "checkpoint"}, "checkpoint_saved":True})
        atomic_json(self.path, {**self.data, "courses":summaries})

    def event(self, kind, message, identity=None):
        self.data["events"].append({"time":now(), "kind":kind, "message":str(message)[:700],
                                    "course_id":identity})
        self.data["events"] = self.data["events"][-200:]
        self.save()

    def update(self, identity, **fields):
        row = self.course(identity)
        row.update(fields)
        row["updated_at"] = now()
        self.save()


def validate_checkpoint(checkpoint):
    if not isinstance(checkpoint, dict):
        raise ValueError("Повреждено сохранённое состояние курса.")
    for field in ("attempts", "feedback", "modules", "answer_history"):
        if not isinstance(checkpoint.get(field, {}), dict):
            raise ValueError("Повреждено поле checkpoint: " + field)
    for key, ledger in checkpoint.get("attempts", {}).items():
        if (not isinstance(key, str) or not isinstance(ledger, dict) or
                type(ledger.get("count")) is not int or not 0 <= ledger["count"] <= 1000000 or
                any(name in ledger and type(ledger[name]) is not bool for name in ("pending", "passed", "feedback_ready"))):
            raise ValueError("Повреждён журнал попыток; автоматический сброс запрещён.")
    if any(not isinstance(k, str) or not isinstance(v, str) for k,v in checkpoint.get("feedback", {}).items()):
        raise ValueError("Повреждена сохранённая обратная связь.")
    if any(not isinstance(key, str) or not isinstance(history, list) or len(history) > 8 or
           any(not isinstance(attempt, dict) or not isinstance(attempt.get("questions"), list) for attempt in history)
           for key, history in checkpoint.get("answer_history", {}).items()):
        raise ValueError("Повреждена история ответов теста.")
    for ledger in checkpoint.get("attempts", {}).values():
        plan = ledger.get("last_plan")
        if plan is not None and (not isinstance(plan, dict) or not isinstance(plan.get("questions"), list) or
                                 not isinstance(plan.get("selected_controls"), list)):
            raise ValueError("Повреждён сохранённый план ответов.")
    for module in checkpoint.get("modules", {}).values():
        if not isinstance(module, dict) or not isinstance(module.get("items", {}), dict):
            raise ValueError("Повреждено состояние модуля.")
