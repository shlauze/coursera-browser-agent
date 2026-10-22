"""Small localhost-only control panel. It never displays credentials or action payloads."""

from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import time
import math
from datetime import datetime, timezone, timedelta
from typing import Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .control import Control, ControlError
from .store import Store
from . import __version__
from .token_usage import usage_summary, usage_html


_CSS = """
:root{color-scheme:light;font-family:Inter,Segoe UI,Arial,sans-serif;background:#f5f7fb;color:#152238}
*{box-sizing:border-box}body{margin:0}header{background:#142b58;color:white;padding:26px max(24px,calc((100vw - 1100px)/2))}
header h1{margin:0;font-size:25px}header p{margin:8px 0 0;color:#d4e2ff}main{max-width:1100px;margin:28px auto;padding:0 24px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.panel{background:#fff;border:1px solid #dde4f0;border-radius:15px;padding:20px;margin-bottom:18px;box-shadow:0 4px 18px #18336b0c}
h2{font-size:18px;margin:0 0 16px}h3{font-size:15px;margin:0 0 6px}.sub{color:#62718a;font-size:13px}
.status{display:inline-block;padding:7px 12px;border-radius:20px;background:#dceafc;color:#153f83;font-weight:700}
.status.paused{background:#fff0c2;color:#755400}.status.stopped,.status.stop_requested{background:#fce0e0;color:#8c3030}
.buttons{display:flex;gap:9px;flex-wrap:wrap;margin-top:16px}form{display:inline-block;margin:0}button{background:#1c5ed0;color:#fff;border:0;border-radius:8px;padding:9px 13px;font-weight:700;cursor:pointer}
button:hover{background:#144eae}button.secondary{background:#e9eff9;color:#1f477f}button.danger{background:#ab3644}button:disabled{background:#c4cbda;cursor:default}
.course{border-top:1px solid #edf1f7;padding:14px 0;display:flex;align-items:center;justify-content:space-between;gap:15px}
.course:first-of-type{border-top:0}.course.skipped{opacity:.55}.course small{display:block;color:#68768b;margin-top:5px}
.pill{background:#ecf1fa;border-radius:12px;padding:3px 8px;font-size:12px;margin-right:5px}.empty{color:#64748b;padding:12px 0}
.events{width:100%;border-collapse:collapse;font-size:13px}.events th,.events td{padding:9px;border-bottom:1px solid #eef1f6;text-align:left;vertical-align:top}
.events th{color:#60708a}.pending{background:#fff3d7;border:1px solid #f1d48a;padding:12px;border-radius:9px;font-size:13px}
.notice{background:#e4f2df;padding:10px 14px;border-radius:9px;margin-bottom:15px}.error{background:#ffebeb}
@media(max-width:700px){.grid{grid-template-columns:1fr}.course{align-items:flex-start;flex-direction:column}}
"""


def runtime_status(store, now=None):
    now = time.time() if now is None else now
    state = store.get_control_state()
    wait = store.get_meta("groq_wait") or {}
    retry_at = max(float(wait.get("retry_at", 0)), float(store.get_meta("groq_retry_until", 0)))
    remaining = max(0, math.ceil(retry_at - now))
    course = store.get_course(store.get_meta("current_course_id")) or {}
    action_id = store.get_meta("current_action_id")
    progress = store.get_meta(f"progress:{action_id}", {}) if action_id else {}
    return {"control_state": state, "course": course.get("title", ""),
            "item": store.get_meta("current_item", ""), "action_id": action_id,
            "stage": progress.get("stage", ""), "stage_updated_at": progress.get("updated_at"),
            "retry_at": retry_at or None, "remaining_seconds": remaining,
            "waiting": state == "running" and remaining > 0,
            "reason": wait.get("reason", "rate_limit" if remaining else None),
            "http_status": wait.get("status"), "limit": store.get_meta("groq_rate_limit", {})}


def runtime_html(status):
    stages = {"preparing": "Подготовка задания", "answer_received": "Ответ получен и сохранён", "answer_filled": "Ответ заполнен",
              "submitting": "Отправка начата — результат ещё неизвестен",
              "submitted": "Отправлено — ожидается результат", "passed": "Зачёт получен — проверяется отметка",
              "failed": "Неудачная попытка — требуется разбор", "result_confirmed": "Результат подтверждён"}
    title = "Ожидание Groq" if status["waiting"] else "Текущий этап"
    parts = [status["course"], status["item"], stages.get(status["stage"], status["stage"])]
    if status["remaining_seconds"]:
        reason = "Ограничение квоты" if status["reason"] == "rate_limit" else "Временная ошибка API"
        metric = (status.get("limit") or {}).get("metric", "") if status["reason"] == "rate_limit" else ""
        when = datetime.fromtimestamp(status["retry_at"], timezone(timedelta(hours=5))).strftime("%d.%m.%Y %H:%M:%S UTC+5")
        parts.append(f"{reason} {metric}. Следующий запрос не раньше {when}; осталось {status['remaining_seconds']} сек.")
        parts.append("Повтор будет автоматическим. Перезапуск не ускорит восстановление квоты." if status["waiting"] else
                     "Исполнитель остановлен или на паузе; автоматического запроса сейчас не будет.")
    elif status["stage"] in {"submitting", "submitted"}:
        parts.append("Перед повторной отправкой требуется сверка результата с Coursera.")
    return '<section class="panel"><h2>' + title + '</h2><p>' + '</p><p>'.join(escape(str(x)) for x in parts if x) + '</p></section>'


class Dashboard:
    def __init__(
        self,
        store: Store,
        control: Control,
        *,
        host: str = "127.0.0.1",
        port: int = 8766,
        start_callback: Callable[[], None] | None = None,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Dashboard must bind to localhost")
        self.store = store
        self.control = control
        self.host = host
        self.port = port
        self.start_callback = start_callback
        self.csrf_token = secrets.token_urlsafe(32)

    def build_server(self) -> ThreadingHTTPServer:
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "CourseraAgentV2/1.0"

            def log_message(self, format: str, *args: object) -> None:
                # Routine browser requests are already represented by explicit events.
                return

            def _send(self, code: int, content: bytes, content_type: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
                )
                self.end_headers()
                self.wfile.write(content)

            def _json(self, code: int, value: object) -> None:
                self._send(code, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")

            def _redirect(self, notice: str) -> None:
                self.send_response(303)
                self.send_header("Location", "/?notice=" + quote(notice))
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()

            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                if path == "/api/status":
                    self._json(200, {**dashboard.store.snapshot(), "version": __version__, "runtime": runtime_status(dashboard.store), "groq_usage": usage_summary(dashboard.store)})
                elif path == "/":
                    query = parse_qs(urlsplit(self.path).query)
                    notice = query.get("notice", [""])[0][:200]
                    self._send(200, dashboard._render(notice).encode("utf-8"),
                               "text/html; charset=utf-8")
                else:
                    self._send(404, b"Not found", "text/plain; charset=utf-8")

            def do_POST(self) -> None:
                path = urlsplit(self.path).path
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send(400, b"Invalid request", "text/plain; charset=utf-8")
                    return
                if length <= 0 or length > 8192:
                    self._send(400, b"Invalid request size", "text/plain; charset=utf-8")
                    return
                origin = self.headers.get("Origin")
                expected = f"http://{dashboard.host}:{dashboard.port}"
                if origin and origin != expected:
                    self._send(403, b"Invalid origin", "text/plain; charset=utf-8")
                    return
                if not self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
                    self._send(415, b"Unsupported content type", "text/plain; charset=utf-8")
                    return
                fields = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
                token = fields.get("csrf", [""])[0]
                if not secrets.compare_digest(token, dashboard.csrf_token):
                    self._send(403, b"Invalid CSRF token", "text/plain; charset=utf-8")
                    return
                try:
                    if path == "/action/start" and dashboard.start_callback:
                        if dashboard.control.get_state() != "stopped":
                            raise ControlError("Runner is already active")
                        dashboard.start_callback()
                        self._redirect("Запуск запрошен")
                        return
                    if path == "/action/pause":
                        dashboard.control.request_pause()
                        self._redirect("Пауза запрошена")
                        return
                    if path == "/action/resume":
                        dashboard.control.request_resume()
                        self._redirect("Продолжение запрошено")
                        return
                    if path == "/action/stop":
                        dashboard.control.request_stop()
                        self._redirect("Остановка запрошена")
                        return
                    if path.startswith("/course/"):
                        bits = path.split("/")
                        if len(bits) == 4 and bits[3] in {"skip", "unskip"}:
                            course_id = unquote(bits[2])
                            dashboard.store.set_course_skipped(course_id, bits[3] == "skip")
                            self._redirect("Очередь обновлена")
                            return
                except (ControlError, KeyError) as error:
                    self._send(409, str(error).encode("utf-8"), "text/plain; charset=utf-8")
                    return
                self._send(404, b"Not found", "text/plain; charset=utf-8")

        server = ThreadingHTTPServer((self.host, self.port), Handler)
        server.daemon_threads = True
        self.port = server.server_address[1]
        return server

    def serve_forever(self) -> None:
        with self.build_server() as server:
            server.serve_forever(poll_interval=0.25)

    def _form(self, path: str, title: str, css_class: str = "") -> str:
        return (
            f'<form method="post" action="{escape(path, quote=True)}">'
            f'<input type="hidden" name="csrf" value="{escape(self.csrf_token, quote=True)}">'
            f'<button class="{css_class}" type="submit">{escape(title)}</button></form>'
        )

    def _render(self, notice: str = "") -> str:
        snapshot = self.store.snapshot()
        state = snapshot["control_state"]
        runtime = runtime_html(runtime_status(self.store))
        usage = usage_html(usage_summary(self.store))
        labels = {
            "running": "Работает", "paused": "Пауза",
            "stop_requested": "Останавливается", "stopped": "Остановлен",
        }
        controls = []
        if state == "stopped" and self.start_callback:
            controls.append(self._form("/action/start", "Запустить"))
        if state == "running":
            controls.append(self._form("/action/pause", "Пауза", "secondary"))
        if state == "paused":
            controls.append(self._form("/action/resume", "Продолжить"))
        if state in {"running", "paused"}:
            controls.append(self._form("/action/stop", "Остановить", "danger"))
        if not controls:
            controls.append('<span class="sub">Управление доступно после запуска процесса.</span>')

        courses = []
        for course in snapshot["courses"]:
            course_id = str(course["course_id"])
            skipped = bool(course["skipped"])
            progress = course["progress_percent"]
            progress_text = "?" if progress is None else f"{float(progress):g}%"
            action = "unskip" if skipped else "skip"
            button = "Вернуть в очередь" if skipped else "Пропустить"
            class_name = "course skipped" if skipped else "course"
            courses.append(
                f'<div class="{class_name}"><div><h3>{escape(str(course["title"]))}</h3>'
                f'<small><span class="pill">{escape({"attention":"Требует внимания", "complete":"Сертификат подтверждён", "started":"Начат", "new":"Новый"}.get(str(course["status"]), str(course["status"])))}</span>'
                f'{escape(progress_text)} · страница {int(course["page_index"])}'
                + (" · пропущен" if skipped else "") + "</small>"
                + (f'<small>{escape(str(course["attention_reason"]))}</small>' if course.get("attention_reason") else "") + "</div>"
                + self._form(f"/course/{quote(course_id, safe='')}/{action}", button, "secondary")
                + "</div>"
            )
        confirmation = snapshot.get("confirmation_required")
        confirmation_html = (
            '<div class="pending"><strong>Требуется ваше подтверждение: '
            + escape(str(confirmation.get("item", "Квиз"))) + '</strong><p>'
            + escape(str(confirmation["reason"])) + '</p></div>'
        ) if confirmation else ""
        pending = snapshot["pending_actions"]
        pending_html = (
            '<div class="pending">Есть незавершённое действие. Перед повтором отправки нужно сверить результат на Coursera. '
            + ", ".join(escape(f"#{action['id']} {action['action_kind']}") for action in pending)
            + "</div>"
        ) if pending else '<p class="sub">Незавершённых действий нет.</p>'
        events = "".join(
            "<tr>"
            f'<td>{escape(str(event["at"]))}</td>'
            f'<td>{escape(str(event["level"]))}</td>'
            f'<td>{escape(str(event["code"]))}</td>'
            f'<td>{escape(str(event["message"]))}</td>'
            "</tr>"
            for event in snapshot["events"][:25]
        )
        notice_html = f'<div class="notice">{escape(notice)}</div>' if notice else ""
        limits = snapshot.get("groq_limits", {})
        limits_html = " · ".join(f"{escape(str(k))}: {escape(str(v))}" for k,v in limits.items()) or "Данных от Groq пока нет"
        return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="8">
<title>Coursera Agent · Панель управления</title><style>{_CSS}</style></head><body>
<header><h1>Coursera Agent</h1><p>Локальная панель управления · v2 {__version__}</p><p>Режим: тесты, задания и лабораторные. Видео и лекции пропускаются.</p></header>
<main>{notice_html}{confirmation_html}{runtime}{usage}<div class="grid"><section class="panel"><h2>Состояние</h2>
<span class="status {escape(state)}">{escape(labels.get(state,state))}</span>
<div class="buttons">{''.join(controls)}</div></section>
<section class="panel"><h2>Ожидающие действия</h2>{pending_html}</section></div>
<section class="panel"><h2>Очередь курсов</h2>{''.join(courses) if courses else '<p class="empty">Очередь ещё не считана.</p>'}</section>
<section class="panel"><h2>Groq: последний ответ API</h2><p class="sub">{limits_html}</p></section>
<section class="panel"><h2>Последние события</h2><table class="events"><thead><tr>
<th>Время UTC</th><th>Уровень</th><th>Код</th><th>Сообщение</th></tr></thead>
<tbody>{events or '<tr><td colspan="4" class="empty">Событий пока нет.</td></tr>'}</tbody></table></section>
</main></body></html>"""
