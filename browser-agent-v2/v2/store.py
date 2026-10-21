"""Durable, process-safe state for the Coursera runner.

The database records the *intent* to perform an action before a browser click.
An unresolved action must be reconciled with Coursera after a restart; it must
never be submitted again merely because the process was interrupted.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: str | None) -> Any:
    return json.loads(value) if value else None


_SECRET_FIELD = re.compile(
    r"(?i)\b(authorization|cookie|set-cookie|password|api[_-]?key|token|secret)\s*[:=]\s*[^\s,;]+"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;]+")


def _safe_event_message(message: str) -> str:
    message = _SECRET_FIELD.sub(lambda match: match.group(1) + "=[redacted]", message)
    message = _BEARER.sub("Bearer [redacted]", message)
    return message[:1000]


class PendingActionError(RuntimeError):
    """This action already has an unresolved intent that needs reconciliation."""

    def __init__(self, action_id: int) -> None:
        self.action_id = action_id
        super().__init__(f"Action {action_id} is unresolved; inspect the remote result first")


class Store:
    """SQLite store. Each operation owns a connection, so UI and runner may coexist."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        # Retry only opening an existing database, before any transaction/action.
        # Never recreate a missing state file or replay a transaction body.
        for attempt in range(3):
            try:
                connection = sqlite3.connect(self.path, timeout=10)
                break
            except sqlite3.OperationalError as exc:
                if ("unable to open database file" not in str(exc).lower()
                        or attempt == 2 or not self.path.is_file()):
                    raise
                time.sleep(0.25 * (2 ** attempt))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS courses (
                    course_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT,
                    progress_percent REAL,
                    status TEXT NOT NULL DEFAULT 'new',
                    skipped INTEGER NOT NULL DEFAULT 0 CHECK(skipped IN (0,1)),
                    order_index INTEGER NOT NULL DEFAULT 0,
                    page_index INTEGER NOT NULL DEFAULT 1,
                    certificate_url TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    action_kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    started_at TEXT NOT NULL,
                    resolved_at TEXT,
                    outcome TEXT,
                    evidence_json TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_action
                    ON actions(course_id, item_id, action_kind)
                    WHERE status IN ('pending','uncertain');
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    code TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data_json TEXT
                );
                CREATE TABLE IF NOT EXISTS control (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO control(id, state, updated_at)
                    VALUES (1, 'stopped', datetime('now'));
                """
            )

    def set_meta(self, key: str, value: Any) -> None:
        with self._connection(write=True) as connection:
            connection.execute(
                "INSERT INTO metadata(key,value_json) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (key, _encode(value)),
            )

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM metadata WHERE key=?", (key,)
            ).fetchone()
        return _decode(row["value_json"]) if row else default

    def record_event(
        self, level: str, code: str, message: str, data: Any = None
    ) -> int:
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                "INSERT INTO events(at,level,code,message,data_json) VALUES(?,?,?,?,?)",
                (_now(), level.upper(), code, _safe_event_message(message),
                 _encode(data) if data is not None else None),
            )
            return int(cursor.lastrowid)

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(0, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id,at,level,code,message FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_course(
        self,
        course_id: str,
        title: str,
        *,
        url: str | None = None,
        progress_percent: float | None = None,
        status: str = "new",
        order_index: int = 0,
        page_index: int = 1,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not course_id or not title:
            raise ValueError("course_id and title are required")
        if progress_percent is not None and not 0 <= progress_percent <= 100:
            raise ValueError("progress_percent must be between 0 and 100")
        with self._connection(write=True) as connection:
            connection.execute(
                """
                INSERT INTO courses(course_id,title,url,progress_percent,status,
                                    order_index,page_index,details_json,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(course_id) DO UPDATE SET
                    title=excluded.title,
                    url=COALESCE(excluded.url,courses.url),
                    progress_percent=COALESCE(excluded.progress_percent,courses.progress_percent),
                    status=CASE WHEN courses.status IN ('complete','attention') THEN courses.status
                                ELSE excluded.status END,
                    order_index=excluded.order_index,
                    page_index=excluded.page_index,
                    details_json=excluded.details_json,
                    updated_at=excluded.updated_at
                """,
                (
                    course_id, title, url, progress_percent, status,
                    order_index, page_index, _encode(details or {}), _now(),
                ),
            )

    def get_course(self, course_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM courses WHERE course_id=?", (course_id,)
            ).fetchone()
        return self._course_dict(row) if row else None

    def list_courses(self, *, include_skipped: bool = True) -> list[dict[str, Any]]:
        where = "" if include_skipped else "WHERE skipped=0"
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM courses {where} ORDER BY page_index,order_index,course_id"
            ).fetchall()
        return [self._course_dict(row) for row in rows]

    @staticmethod
    def _course_dict(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["skipped"] = bool(value["skipped"])
        value["details"] = _decode(value.pop("details_json")) or {}
        return value

    def set_course_skipped(self, course_id: str, skipped: bool = True) -> None:
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                "UPDATE courses SET skipped=?,updated_at=? WHERE course_id=?",
                (int(skipped), _now(), course_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown course: {course_id}")
        self.record_event(
            "INFO", "COURSE_SKIP" if skipped else "COURSE_UNSKIP",
            f"Course {'skipped' if skipped else 'returned to queue'}: {course_id}",
        )

    def update_course_status(
        self,
        course_id: str,
        status: str,
        *,
        progress_percent: float | None = None,
        certificate_url: str | None = None,
    ) -> None:
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """UPDATE courses SET status=?,
                      progress_percent=COALESCE(?,progress_percent),
                      certificate_url=COALESCE(?,certificate_url),
                      updated_at=? WHERE course_id=?""",
                (status, progress_percent, certificate_url, _now(), course_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown course: {course_id}")

    def begin_action(
        self,
        course_id: str,
        item_id: str,
        action_kind: str,
        payload: Any = None,
    ) -> int:
        """Create intent before browser action; reject an unresolved retry."""
        if not all((course_id, item_id, action_kind)):
            raise ValueError("course_id, item_id and action_kind are required")
        with self._connection(write=True) as connection:
            existing = connection.execute(
                """SELECT id FROM actions WHERE course_id=? AND item_id=?
                   AND action_kind=? AND status IN ('pending','uncertain')""",
                (course_id, item_id, action_kind),
            ).fetchone()
            if existing:
                raise PendingActionError(int(existing["id"]))
            cursor = connection.execute(
                """INSERT INTO actions(course_id,item_id,action_kind,payload_json,
                                       status,started_at) VALUES(?,?,?,?,'pending',?)""",
                (course_id, item_id, action_kind, _encode(payload or {}), _now()),
            )
            return int(cursor.lastrowid)

    def resolve_action(
        self,
        action_id: int,
        outcome: str,
        evidence: Any = None,
    ) -> None:
        if outcome not in {"confirmed", "failed", "unclear", "cancelled"}:
            raise ValueError(f"Unsupported action outcome: {outcome}")
        status = "uncertain" if outcome == "unclear" else "resolved"
        resolved_at = None if status == "uncertain" else _now()
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """UPDATE actions SET status=?,resolved_at=?,outcome=?,
                       evidence_json=? WHERE id=? AND status IN ('pending','uncertain')""",
                (status, resolved_at, outcome,
                 _encode(evidence) if evidence is not None else None, action_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Action {action_id} is unknown or already resolved")
            if outcome == "confirmed":
                key = f"progress:{action_id}"
                row = connection.execute("SELECT value_json FROM metadata WHERE key=?", (key,)).fetchone()
                progress = _decode(row[0]) if row else {"questions": {}}
                progress.update(stage="result_confirmed", updated_at=_now())
                connection.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, _encode(progress)))

    def record_stage(self, action_id, stage, *, question=None, attempt=None):
        """Commit milestones before/after side effects; never store answer text here."""
        allowed = {"preparing", "answer_received", "answer_filled", "submitting", "submitted", "passed", "failed"}
        if stage not in allowed:
            raise ValueError(stage)
        with self._connection(write=True) as c:
            key = f"progress:{action_id}"
            old = c.execute("SELECT value_json FROM metadata WHERE key=?", (key,)).fetchone()
            progress = _decode(old[0]) if old else {"questions": {}}
            progress.update(stage=stage, updated_at=_now())
            progress.setdefault("history", []).append({"stage": stage, "at": _now(), "question": question, "attempt": attempt})
            if attempt is not None:
                progress["attempt"] = attempt
            if question:
                progress["questions"][question] = {"stage": stage, "at": _now(), "attempt": attempt}
            c.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, _encode(progress)))
            c.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", ("current_action_id", _encode(action_id)))
            if stage in {"submitting", "submitted", "passed", "failed"}:
                submission = "pending" if stage == "submitted" else stage
                c.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (f"submission:{action_id}", _encode(submission)))

    def pending_actions(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM actions WHERE status IN ('pending','uncertain') ORDER BY id"
            ).fetchall()
        return [self._action_dict(row) for row in rows]

    def get_action(self, action_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM actions WHERE id=?", (action_id,)
            ).fetchone()
        return self._action_dict(row) if row else None

    @staticmethod
    def _action_dict(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["payload"] = _decode(value.pop("payload_json")) or {}
        value["evidence"] = _decode(value.pop("evidence_json"))
        return value

    def get_control_state(self) -> str:
        with self._connection() as connection:
            row = connection.execute("SELECT state FROM control WHERE id=1").fetchone()
        return str(row["state"])

    def set_control_state(self, state: str) -> None:
        if state not in {"running", "paused", "stop_requested", "stopped"}:
            raise ValueError(f"Unknown control state: {state}")
        with self._connection(write=True) as connection:
            connection.execute(
                "UPDATE control SET state=?,updated_at=? WHERE id=1",
                (state, _now()),
            )

    def snapshot(self) -> dict[str, Any]:
        pending = self.pending_actions()
        return {
            "control_state": self.get_control_state(),
            "confirmation_required": self.get_meta("confirmation_required"),
            "courses": [
                {**{key: value for key, value in course.items()
                    if key not in {"details", "url", "certificate_url"}},
                 "attention_reason": self.get_meta("course_attention:" + course["course_id"], "")}
                for course in self.list_courses()
            ],
            "groq_limits": self.get_meta("groq_limits", {}),
            "pending_actions": [
                {key: action[key] for key in ("id", "course_id", "item_id", "action_kind", "started_at")}
                for action in pending
            ],
            "events": self.recent_events(50),
        }
