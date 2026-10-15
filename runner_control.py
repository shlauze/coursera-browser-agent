"""Durable, local-only commands for the Coursera queue runner.

The monitor writes this file, while the runner remains the sole writer of
queue.json and its checkpoints. A command revision prevents a stale browser
panel from overwriting a newer request.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import re

from course_state import MAX_COURSES, RunLock, atomic_json, now
from shared_io import open_read_shared


class ControlConflict(ValueError):
    """The control file changed since the dashboard last read it."""


_DESIRED = {"running", "paused", "stopped"}
_ACTIONS = {"start", "pause", "resume", "stop", "skip", "unskip"}
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,159}\Z")


class ControlStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / "state" / "control.json"
        self.lock_path = self.root / "state" / "control.lock"

    @staticmethod
    def _default():
        return {"revision": 0, "desired": "running", "skipped_courses": [],
                "updated_at": None}

    def _read(self):
        if not self.path.exists():
            return self._default()
        with open_read_shared(self.path) as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("Файл команд управления слишком большой.")
        value = json.loads(raw.decode("utf-8"))
        if (not isinstance(value, dict) or set(value) != set(self._default()) or
                type(value.get("revision")) is not int or value["revision"] < 0 or
                not isinstance(value.get("desired"), str) or value["desired"] not in _DESIRED or
                not isinstance(value.get("skipped_courses"), list) or
                len(value["skipped_courses"]) > MAX_COURSES or
                any(not isinstance(course, str) or not _SLUG.fullmatch(course)
                    for course in value["skipped_courses"]) or
                value["skipped_courses"] != sorted(set(value["skipped_courses"])) or
                not isinstance(value.get("updated_at"), str) or
                len(value["updated_at"]) > 80):
            raise ValueError("Повреждено состояние управления исполнителем; исходный файл сохранён.")
        try:
            datetime.fromisoformat(value["updated_at"])
        except ValueError:
            raise ValueError("Повреждено время команды управления; исходный файл сохранён.") from None
        return value

    def snapshot(self):
        """Read an atomic snapshot without contending with the runner lock."""
        return self._read()

    def change(self, action, course_id=None, revision=None):
        if not isinstance(action, str) or action not in _ACTIONS:
            raise ValueError("Неизвестная команда управления.")
        if revision is not None and (type(revision) is not int or revision < 0):
            raise ValueError("Некорректная версия команды управления.")
        if action in {"skip", "unskip"}:
            if not isinstance(course_id, str) or not _SLUG.fullmatch(course_id):
                raise ValueError("Укажите идентификатор курса Coursera.")
        elif course_id is not None:
            raise ValueError("Для этой команды курс не требуется.")
        with RunLock(self.lock_path):
            current = self._read()
            if revision is not None and revision != current["revision"]:
                raise ControlConflict("Команды изменились. Обновите панель и повторите действие.")
            updated = {**current, "skipped_courses": list(current["skipped_courses"])}
            if action in {"start", "resume"}:
                updated["desired"] = "running"
            elif action == "pause":
                updated["desired"] = "paused"
            elif action == "stop":
                updated["desired"] = "stopped"
            elif action == "skip":
                updated["skipped_courses"] = sorted(set(updated["skipped_courses"]) | {course_id})
            else:
                updated["skipped_courses"] = [identity for identity in updated["skipped_courses"]
                                              if identity != course_id]
            if (updated["desired"] == current["desired"] and
                    updated["skipped_courses"] == current["skipped_courses"]):
                return current
            updated["revision"] += 1
            updated["updated_at"] = now()
            atomic_json(self.path, updated)
            return updated


def runner_active(root):
    """Ask the OS lock whether a runner owns this workspace right now."""
    try:
        with RunLock(Path(root).resolve() / "state" / "runner.lock"):
            return False
    except RuntimeError as exc:
        if "Скрипт уже запущен" in str(exc):
            return True
        raise
