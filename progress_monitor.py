"""Local progress dashboard and course-list editor; never exposes raw quiz snapshots.

Prefer state/queue.json schema_version=1. If absent, summarize the newest legacy
logs/course_*/progress.json. All file reads stay beneath the installation root.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import webbrowser
from course_state import MAX_COURSES, RunLock, course_identity
from my_learning import valid_certificate_url
from runner_control import ControlConflict, ControlStore, runner_active
from shared_io import open_read_shared

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 20 * 1024 * 1024
MAX_EVENTS = 60
MAX_CONFIG_BYTES = 512 * 1024
MAX_ACTION_BYTES = 2048
QUEUE_STATUSES = {"idle", "running", "paused", "attention", "completed"}
COURSE_STATUSES = {"pending", "running", "completed", "attention", "failed", "skipped"}
QUEUE_EVENTS = {"queue_started", "course_started", "module_started", "item_started", "item_completed",
                "module_completed", "item_manual_required", "module_manual_required",
                "course_completed", "course_discovered", "certificate_opened",
                "certificate_attention", "course_transition_attention", "my_learning_retry", "course_attention",
                "queue_paused", "queue_resumed", "queue_completed", "queue_attention", "course_skipped",
                "my_learning_scanned", "quiz_preflight_reconciled", "read_recovery"}


def clean_text(value, limit=240):
    if not isinstance(value, str):
        return ""
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = re.sub(r"(?i)\b(?:gsk_|sk-)[A-Za-z0-9_-]+", "[ключ скрыт]", value)
    value = re.sub(r"(?i)bearer\s+\S+", "Bearer [скрыто]", value)
    value = re.sub(r"(?i)(?:api[_ -]?key|token|password)\s*[:=]\s*[^\s,;]+", "[секрет скрыт]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email скрыт]", value)
    value = re.sub(r"(?i)[a-z]:[\\/]users[\\/][^\r\n]*", "[локальный путь скрыт]", value)
    value = re.sub(r"(?i)I,\s*.+?,\s*understand and agree\.?", "[личное подтверждение]", value)
    return " ".join(value.split())[:limit]


def integer(value, default=0):
    return min(value, 1_000_000) if type(value) is int and value >= 0 else default


def slug(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,150}", value) else ""


def course_url(value):
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "https" and parsed.netloc in {"coursera.org", "www.coursera.org"}
                and re.fullmatch(r"/learn/[A-Za-z0-9_-]+/?", parsed.path)):
            return "https://www.coursera.org" + parsed.path.rstrip("/")
    except ValueError:
        pass
    return ""


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        return None


def _courses_path(root):
    path = root / "courses.txt"
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Course list is outside the installation")
    return path


def read_courses(root):
    """Return validated config, revision and original text without exposing it."""
    path = _courses_path(root)
    try:
        with open_read_shared(path) as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        raw = b""
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("Course list is too large")
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Course list is not UTF-8") from None
    rows, seen = [], set()
    for line in source.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        identity, url = course_identity(line)
        if identity in seen:
            raise ValueError("Duplicate course in list")
        seen.add(identity)
        rows.append({"id": identity, "url": url, "title": identity})
    if len(rows) > MAX_COURSES:
        raise ValueError(f"Course list is limited to {MAX_COURSES} courses")
    return rows, hashlib.sha256(raw).hexdigest(), source


def atomic_text(path, content):
    """Replace the list without exposing a truncated version to the queue."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=path.name + ".",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                return
            except OSError as error:
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 7:
                    raise
                time.sleep(min(0.025 * 2 ** attempt, 0.25))
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class CourseListConflict(ValueError):
    pass


class CoursesEditor:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def change(self, action, value, revision):
        if action not in {"add", "remove"} or not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
            raise ValueError("Некорректное изменение списка курсов.")
        with RunLock(self.root / "state" / "courses.lock"):
            rows, current_revision, source = read_courses(self.root)
            if revision != current_revision:
                raise CourseListConflict("Список изменился. Данные обновлены; повторите действие.")
            if action == "add":
                identity, url = course_identity(value)
                if any(row["id"] == identity for row in rows):
                    raise ValueError("Этот курс уже есть в списке.")
                if len(rows) >= MAX_COURSES:
                    raise ValueError(f"В списке может быть не больше {MAX_COURSES} курсов.")
                content = source + ("\n" if source and not source.endswith(("\n", "\r")) else "") + url + "\n"
            else:
                if not isinstance(value, str) or not slug(value):
                    raise ValueError("Укажите курс из списка.")
                if not any(row["id"] == value for row in rows):
                    raise ValueError("Курс уже удалён из списка.")
                if len(rows) == 1:
                    raise ValueError("Оставьте хотя бы один курс. Сначала добавьте новый, затем удалите этот.")
                kept = []
                for line in source.splitlines(keepends=True):
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        kept.append(line)
                        continue
                    identity, _ = course_identity(stripped)
                    if identity != value:
                        kept.append(line)
                content = "".join(kept)
            if len(content.encode("utf-8")) > MAX_CONFIG_BYTES:
                raise ValueError("Список курсов слишком большой.")
            atomic_text(_courses_path(self.root), content)
            updated, new_revision, _ = read_courses(self.root)
            return {"courses": updated, "revision": new_revision}


def read_json(path, root):
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("File is outside the installation")
    with open_read_shared(path) as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Progress file is too large")
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Progress must be a JSON object")
    return value


def sanitize_course(value):
    if not isinstance(value, dict):
        return None
    course_id = slug(value.get("id"))
    url = course_url(value.get("url"))
    if not course_id and url:
        course_id = url.rsplit("/", 1)[-1]
    if not course_id:
        return None
    modules_total = integer(value.get("modules_total"), None)
    modules_completed = integer(value.get("modules_completed"))
    modules_discovered = integer(value.get("modules_discovered"))
    inventory_complete = value.get("inventory_complete") is True
    items_total = integer(value.get("items_total"))
    items_completed = integer(value.get("items_completed"))
    # Invalid counters must not create a misleading percentage.
    if modules_total is not None:
        modules_total = max(modules_completed, modules_total)
    items_total = max(items_total, items_completed)
    status = value.get("status") if value.get("status") in COURSE_STATUSES else "attention"
    message = clean_text(value.get("message"), 500)
    certificate_verified = (value.get("completion_method") == "certificate"
                            and timestamp(value.get("certificate_verified_at")) is not None
                            and valid_certificate_url(value.get("certificate_page_url")))
    if status == "completed" and not (inventory_complete or certificate_verified):
        status = "attention"
        message = "Полный состав курса ещё не подтверждён. " + message
    return {
        "id": course_id, "url": url, "title": clean_text(value.get("title"), 180) or course_id,
        "status": status,
        "modules_total": modules_total, "modules_completed": modules_completed,
        "modules_discovered": modules_discovered, "inventory_complete": inventory_complete,
        "certificate_verified": certificate_verified,
        "items_total": items_total, "items_completed": items_completed,
        "current_module": integer(value.get("current_module"), None),
        "current_item": clean_text(value.get("current_item"), 240) or None,
        "message": message[:500],
        "updated_at": timestamp(value.get("updated_at")),
    }


def summarize_queue(raw):
    if raw.get("schema_version") != 1 or not isinstance(raw.get("courses"), list):
        raise ValueError("Unsupported queue format")
    if len(raw["courses"]) > MAX_COURSES:
        raise ValueError("Too many courses")
    courses, seen = [], set()
    for value in raw["courses"]:
        course = sanitize_course(value)
        if course and course["id"] not in seen:
            courses.append(course)
            seen.add(course["id"])
    events = []
    for value in (raw.get("events") if isinstance(raw.get("events"), list) else [])[-MAX_EVENTS:]:
        if not isinstance(value, dict):
            continue
        kind = clean_text(value.get("kind"), 80)
        # Only lifecycle events belong in a public progress view. Checkpoints,
        # quiz feedback, answers, and unrecognized event payloads stay private.
        if kind not in QUEUE_EVENTS:
            continue
        events.append({"time": timestamp(value.get("time")), "kind": kind,
                       "message": clean_text(value.get("message"), 300),
                       "course_id": slug(value.get("course_id")) or None})
    state = raw.get("status") if raw.get("status") in QUEUE_STATUSES else "attention"
    if not courses and state == "completed":
        state = "idle"
    if courses and state == "completed" and not all(c["status"] == "completed" for c in courses):
        state = "attention"
    return {"source": "queue", "status": state, "updated_at": timestamp(raw.get("updated_at")),
            "current_course": slug(raw.get("current_course")) if raw.get("current_course") in seen else None,
            "courses": courses, "events": events}


LEGACY_EVENTS = {
    "module_roster": "Получен список материалов модуля",
    "item_finished": "Материал завершён и проверен",
    "module_verified": "Модуль завершён и проверен",
    "quiz_passed": "Тест пройден",
    "reading_action": "Обрабатывается материал для чтения",
    "video_action": "Обрабатывается видео",
}


def summarize_legacy(raw, modified):
    modules = raw.get("modules") if isinstance(raw.get("modules"), dict) else {}
    total_items = completed_items = completed_modules = 0
    current_module = None
    current_item = None
    for number, module in list(modules.items())[:1000]:
        if not isinstance(module, dict):
            continue
        completed_modules += module.get("verified_complete") is True
        items = module.get("items") if isinstance(module.get("items"), dict) else {}
        roster = module.get("roster") if isinstance(module.get("roster"), list) else []
        roster_keys = {item.get("key") for item in roster if isinstance(item, dict) and isinstance(item.get("key"), str)}
        total_items += len(roster_keys | set(items))
        completed_items += sum(isinstance(item, dict) and item.get("verified_complete") is True for item in items.values())
        if str(number).isdigit():
            current_module = int(number)
        for item in items.values():
            if isinstance(item, dict):
                current_item = clean_text(item.get("title"), 240) or current_item
    url = course_url(raw.get("course"))
    course_id = url.rsplit("/", 1)[-1] if url else "legacy-course"
    reason = clean_text(raw.get("stop_reason"), 500)
    finished = timestamp(raw.get("finished"))
    # A successful run through a selected module range does not prove course completion.
    state = "attention" if reason else "paused" if finished else "running"
    message = reason or ("Запуск завершён. Полное завершение курса ещё не подтверждено." if finished else "Данные последнего запуска.")
    course = {"id": course_id, "url": url, "title": course_id, "status": "attention" if reason else "pending" if finished else "running",
              "modules_total": None, "modules_completed": completed_modules,
              "modules_discovered": sum(isinstance(module, dict) for module in modules.values()),
              "inventory_complete": False,
              "items_total": total_items, "items_completed": completed_items,
              "current_module": current_module, "current_item": current_item,
              "message": message, "updated_at": finished or modified}
    events = []
    raw_events = raw.get("events") if isinstance(raw.get("events"), list) else []
    for event in raw_events[-2000:]:
        if isinstance(event, dict) and event.get("event") in LEGACY_EVENTS:
            events.append({"time": timestamp(event.get("time")), "kind": event["event"],
                           "message": LEGACY_EVENTS[event["event"]], "course_id": course_id})
    return {"source": "legacy", "status": state, "updated_at": finished or modified,
            "current_course": course_id, "courses": [course], "events": events[-MAX_EVENTS:]}


def merge_configured_courses(result, configured):
    """Show edits immediately while retaining history of courses already started."""
    old = {course["id"]: course for course in result["courses"]}
    merged = []
    for entry in configured:
        course = old.pop(entry["id"], None)
        if course is None:
            course = {"id": entry["id"], "url": entry["url"], "title": entry["title"],
                      "status": "pending", "modules_total": None, "modules_completed": 0,
                      "modules_discovered": 0, "inventory_complete": False,
                      "items_total": 0, "items_completed": 0, "current_module": None,
                      "current_item": None, "message": "", "updated_at": None}
        else:
            course = dict(course)
            course["url"] = entry["url"]
        course["in_list"] = True
        merged.append(course)
    for course in result["courses"]:
        if course["id"] in old and course["status"] != "pending":
            kept = dict(course)
            kept["in_list"] = False
            merged.append(kept)
    result["courses"] = merged
    if result["status"] == "completed" and (not merged or any(c["status"] == "pending" for c in merged)):
        # Newly added courses wait for dispatch; the previous queue did not fail.
        result["status"] = "idle"
    return result


def sanitize_limits(raw):
    raw = raw if isinstance(raw, dict) else {}
    names = ("groq_requests_remaining", "groq_requests_limit", "groq_tokens_remaining",
             "groq_tokens_limit")
    result = {name: value if type(value) is int and 0 <= value <= 1_000_000_000_000 else None
              for name in names for value in [raw.get(name)]}
    wait = raw.get("groq_retry_after_seconds")
    result["groq_retry_after_seconds"] = round(float(wait), 1) if type(wait) in {int, float} and 0 <= wait <= 86400 else None
    result["groq_updated_at"] = timestamp(raw.get("groq_updated_at"))
    return result


def aggregate(value):
    courses = value["courses"]
    known_modules = sum(c["modules_total"] or 0 for c in courses)
    unknown_modules = any(c["modules_total"] is None for c in courses)
    value["totals"] = {
        "courses": len(courses), "completed_courses": sum(c["status"] == "completed" for c in courses),
        "attention_courses": sum(c["status"] in {"attention", "failed"} for c in courses),
        "modules": None if unknown_modules else known_modules,
        "known_modules": known_modules, "completed_modules": sum(c["modules_completed"] for c in courses),
        "modules_discovered": sum(c["modules_discovered"] for c in courses),
        "inventory_complete": bool(courses) and all(c["inventory_complete"] for c in courses),
        "items": sum(c["items_total"] for c in courses), "completed_items": sum(c["items_completed"] for c in courses),
    }
    value["has_data"] = bool(courses)
    value["stale"] = False
    updated = value.get("updated_at")
    if updated:
        date = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.astimezone()
        value["stale"] = (datetime.now(timezone.utc) - date).total_seconds() > 120 and value["status"] == "running"
    elif value["status"] == "running":
        value["stale"] = True
    return value


class ProgressReader:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.lock = threading.Lock()
        self.last_good = None

    def snapshot(self):
        with self.lock:
            issue = None
            limits = sanitize_limits(None)
            try:
                queue = self.root / "state" / "queue.json"
                if queue.exists():
                    queue_data = read_json(queue, self.root)
                    result = summarize_queue(queue_data)
                    limits = sanitize_limits(queue_data.get("limits"))
                else:
                    candidates = list((self.root / "logs").glob("course_*/progress.json"))
                    if candidates:
                        newest = max(candidates, key=lambda item: item.stat().st_mtime_ns)
                        modified = datetime.fromtimestamp(newest.stat().st_mtime, timezone.utc).isoformat()
                        result = summarize_legacy(read_json(newest, self.root), modified)
                    else:
                        result = {"source": "none", "status": "idle", "updated_at": None, "current_course": None,
                                  "courses": [], "events": []}
                self.last_good = result
            except (OSError, ValueError, TypeError, OverflowError):
                issue = "Файл прогресса временно недоступен или повреждён. Повторная проверка через 3 секунды."
                result = self.last_good or {"source": "none", "status": "attention", "updated_at": None,
                                           "current_course": None, "courses": [], "events": []}
            config_issue = None
            try:
                configured, revision, _ = read_courses(self.root)
                if (self.root / "courses.txt").exists():
                    result = merge_configured_courses(dict(result), configured)
            except (OSError, ValueError, TypeError, OverflowError):
                configured, revision = [], None
                config_issue = "Список courses.txt не удалось прочитать. Проверьте его формат и права доступа."
            result = aggregate(dict(result))
            result["issue"] = issue
            result["configuration"] = {"courses": configured, "revision": revision, "issue": config_issue}
            result["limits"] = limits
            try:
                result["control"] = ControlStore(self.root).snapshot()
                result["runner_active"] = runner_active(self.root)
            except (OSError, ValueError, RuntimeError):
                result["control"] = None
                result["runner_active"] = False
                result["issue"] = (result["issue"] or "") + " Команды управления временно недоступны."
            rate_events = [e["message"] for e in result["events"] if e["kind"] == "course_attention"
                           and "Groq" in e["message"] and "429" in e["message"]]
            result["limits"]["last_rate_limit"] = rate_events[-1] if rate_events else None
            if issue:
                result["stale"] = True
            result["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return result


def _process_alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        # On Windows os.kill(pid, 0) means CTRL_C_EVENT and would interrupt
        # our own console process. Query the process handle instead.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def launch_managed_runner(root):
    """Launch once across monitor instances; the runner owns runner.lock itself."""
    root = Path(root).resolve()
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    marker = state / "monitor_launch.json"
    try:
        with RunLock(state / "monitor_launch.lock"):
            if runner_active(root):
                return "already_running"
            try:
                pending = read_json(marker, root)
            except (OSError, ValueError, UnicodeDecodeError):
                pending = {}
            age = time.time() - pending.get("started_at", 0) if type(pending.get("started_at")) in {int, float} else 1e9
            if 0 <= age < 45 and _process_alive(pending.get("pid")):
                return "starting"
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            with (logs / "monitor_runner.log").open("ab") as output:
                process = subprocess.Popen(
                    [sys.executable, "-X", "utf8", "-X", "faulthandler", str(root / "course_queue.py"),
                     "--monitor", "--yes", "--control-managed"],
                    cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            atomic_text(marker, json.dumps({"pid": process.pid, "started_at": time.time()}))
            return "started"
    except RuntimeError as error:
        if "Скрипт уже запущен" in str(error):
            return "starting"
        raise


def create_server(root=ROOT, port=8765):
    root = Path(root).resolve()
    reader = ProgressReader(root)
    editor = CoursesEditor(root)
    controller = ControlStore(root)
    html_path = Path(__file__).with_name("monitor.html")
    html = html_path.read_bytes()
    scripts = re.findall(rb"<script>(.*?)</script>", html, re.DOTALL)
    import base64
    hashes = " ".join("'sha256-" + base64.b64encode(hashlib.sha256(script).digest()).decode() + "'" for script in scripts)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _local_request(self):
            hosts = {"127.0.0.1", "localhost"}
            port = self.server.server_address[1]
            if self.headers.get_all("Host") is None or len(self.headers.get_all("Host")) != 1:
                return False
            if self.headers.get("Host") not in {f"{host}:{port}" for host in hosts}:
                return False
            origins = self.headers.get_all("Origin") or []
            if len(origins) > 1:
                return False
            if origins and origins[0] not in {f"http://{host}:{port}" for host in hosts}:
                return False
            return True

        def _send(self, code, body, content_type):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src " + hashes + "; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._local_request():
                self._send(403, b"Local requests only", "text/plain; charset=utf-8")
                return
            if self.path == "/":
                self._send(200, html, "text/html; charset=utf-8")
            elif self.path == "/api/progress":
                body = json.dumps(reader.snapshot(), ensure_ascii=False, allow_nan=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            else:
                self._send(404, b"Not found", "text/plain; charset=utf-8")

        def do_POST(self):
            if not self._local_request():
                self._send(403, b"Local requests only", "text/plain; charset=utf-8")
                return
            if self.path not in {"/api/courses", "/api/control"}:
                self._send(405, b"Method not allowed", "text/plain; charset=utf-8")
                return
            action_type = "courses" if self.path == "/api/courses" else "control"
            origin = self.headers.get("Origin")
            port = self.server.server_address[1]
            if (origin not in {f"http://{host}:{port}" for host in ("127.0.0.1", "localhost")}
                    or self.headers.get("X-Monitor-Action") != action_type
                    or self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json"):
                self._send(403, b"Same-origin JSON action required", "text/plain; charset=utf-8")
                return
            size = self.headers.get("Content-Length", "")
            if not size.isdecimal() or not 0 < int(size) <= MAX_ACTION_BYTES:
                self._send(400, b"Invalid action size", "text/plain; charset=utf-8")
                return
            try:
                payload = json.loads(self.rfile.read(int(size)).decode("utf-8"))
                if action_type == "courses":
                    if not isinstance(payload, dict) or set(payload) != {"action", "value", "revision"}:
                        raise ValueError("Некорректное изменение списка курсов.")
                    result = editor.change(payload["action"], payload["value"], payload["revision"])
                else:
                    if (not isinstance(payload, dict) or set(payload) != {"action", "course_id", "revision"}
                            or payload["action"] not in {"start", "pause", "resume", "stop", "skip", "unskip"}
                            or type(payload["revision"]) is not int or payload["revision"] < 0):
                        raise ValueError("Некорректная команда управления.")
                    action, course_id = payload["action"], payload["course_id"]
                    if action in {"skip", "unskip"}:
                        if not slug(course_id):
                            raise ValueError("Укажите курс из очереди.")
                        if (action == "skip" and course_id not in
                                {course["id"] for course in reader.snapshot()["courses"]}):
                            raise ValueError("Такого курса нет в очереди.")
                    elif course_id is not None:
                        raise ValueError("Для этой команды курс не требуется.")
                    controller.change(action, course_id=course_id, revision=payload["revision"])
                    launch = None
                    if action in {"start", "resume"} and not runner_active(root):
                        launch = launch_managed_runner(root)
                    result = {"control": controller.snapshot(), "runner_active": runner_active(root),
                              "launch": launch}
            except (CourseListConflict, ControlConflict) as error:
                code, result = 409, {"error": str(error)}
            except (ValueError, TypeError, UnicodeDecodeError) as error:
                code, result = 400, {"error": clean_text(str(error), 250)}
            except (OSError, RuntimeError):
                code, result = 503, {"error": "Не удалось сохранить список курсов. Повторите попытку."}
            else:
                code = 200
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")

    class ExclusiveLocalServer(ThreadingHTTPServer):
        # Windows SO_REUSEADDR can permit multiple listeners on the same port.
        # Bind exclusively before activation so another monitor cannot compete.
        allow_reuse_address = False

        def server_bind(self):
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    server = ExclusiveLocalServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description="Local course progress monitor and course-list editor")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        server = create_server(port=args.port)
    except OSError as error:
        print(f"MONITOR_ERROR: Cannot open local port {args.port}. Close another monitor or use --port 8766. ({type(error).__name__})")
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"PROGRESS_MONITOR: {url}\nCourse list edits take effect at the next course boundary. Press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nProgress monitor stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
