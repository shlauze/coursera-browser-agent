"""Entry point for the independent v2 runner and localhost panel."""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading
import getpass
from contextlib import contextmanager

from .browser import BrowserSession
from .control import Control
from .dashboard import Dashboard
from .runner import Runner, RunnerAttention, SkipCurrent, StopRequested
from .store import Store


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PROFILE = ROOT / "profile"
LOGS = ROOT / "logs"


class RunnerAlreadyActive(RuntimeError):
    """A duplicate launch is not a failure of the existing worker."""


@contextmanager
def single_runner_lock(path: Path):
    """Prevent two local processes from controlling the same Chrome profile."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RunnerAlreadyActive("Another v2 runner already owns the profile") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RunnerAlreadyActive("Another v2 runner already owns the profile") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _logger() -> logging.Logger:
    LOGS.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("browser_agent_v2")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(LOGS / "runner.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.addHandler(logging.StreamHandler())
    return logger


class WorkerSupervisor:
    def __init__(self, store: Store, control: Control, *, port: int = 9224) -> None:
        self.store = store
        self.control = control
        self.port = port
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_error: Exception | None = None
        if control.get_state() != "stopped":
            try:
                with single_runner_lock(DATA / "runner.lock"):
                    store.set_control_state("stopped")
                    store.record_event("WARNING", "STALE_RUNNER_RECOVERED",
                                       "A previous runner exited without clearing its control state")
            except RuntimeError:
                # Another process still owns the profile and its state.
                pass

    def start(self, *, scan_only: bool = False) -> None:
        with self._guard:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("v2 is already running")
            self._thread = threading.Thread(target=self._worker, kwargs={"scan_only": scan_only}, daemon=False,
                                            name="coursera-v2-worker")
            self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _worker(self, *, scan_only: bool) -> None:
        logger = _logger()
        self.last_error = None
        try:
            with single_runner_lock(DATA / "runner.lock"):
                try:
                    from . import __version__
                    self.store.set_meta("worker_version", __version__)
                    self.store.record_event("INFO", "WORKER_VERSION", f"Worker code version {__version__}")
                    with BrowserSession(PROFILE, port=self.port) as browser:
                        summary = Runner(self.store, self.control).run(browser, scan_only=scan_only)
                        message = (f"Run complete: {summary.completed_courses} certificates, "
                                   f"{summary.completed_items} items, {summary.remaining_courses} courses remain, "
                                   f"{summary.deferred_courses} need attention")
                        self.store.record_event("INFO", "RUN_COMPLETE", message)
                        logger.info(message)
                finally:
                    # Only the lock owner may update the shared worker state,
                    # and cleanup must finish before releasing the lock.
                    if self.control.get_state() != "stopped":
                        self.control.mark_stopped()
        except RunnerAlreadyActive:
            self.store.record_event("INFO", "RUN_ALREADY_ACTIVE", "Duplicate launch ignored; existing worker and progress unchanged")
            logger.info("Исполнитель уже запущен. Второй экземпляр не нужен; состояние и прогресс сохранены. Откройте действующую панель управления.")
        except StopRequested:
            self.store.record_event("INFO", "RUN_STOPPED", "Stopped at a safe checkpoint")
            logger.info("Stopped at a safe checkpoint")
        except SkipCurrent as exc:
            # A skip during a startup reconciliation is not an ordinary queue
            # transition; retain the cause so the user can restart deliberately.
            self.store.record_event("WARNING", "SKIP_RESTART", str(exc))
            logger.warning("%s", exc)
            self.last_error = exc
        except Exception as exc:
            self.last_error = exc
            self.store.record_event("ERROR", "RUN_FAILED", str(exc))
            logger.exception("Runner stopped: %s", exc)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coursera browser agent v2")
    parser.add_argument("--chrome-port", type=int, default=9224, help="Dedicated Chrome CDP port")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="Start localhost dashboard")
    serve.add_argument("--autostart", action="store_true", help="Begin course run after panel starts")
    serve.add_argument("--panel-port", type=int, default=8766)
    commands.add_parser("run", help="Run one worker with the existing panel and shared exclusive lock")
    commands.add_parser("scan", help="Read My Learning without opening an item")
    commands.add_parser("status", help="Print saved status as JSON")
    configure = commands.add_parser("configure-groq", help="Save a Groq key with Windows encryption")
    configure.add_argument("--import-legacy", action="store_true", help="Copy the encrypted key from the sibling browser-agent")
    commands.add_parser("check-groq", help="Check Groq with one synthetic arithmetic question")
    commands.add_parser("pause", help="Pause at the next safe checkpoint")
    commands.add_parser("resume", help="Resume a paused runner")
    commands.add_parser("stop", help="Request a graceful stop")
    skip = commands.add_parser("skip", help="Skip a course by its dashboard ID")
    skip.add_argument("course_id")
    unskip = commands.add_parser("unskip", help="Return a skipped course to the queue")
    unskip.add_argument("course_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = Store(DATA / "state.sqlite3")
    control = Control(store)
    command = args.command or "serve"
    if command == "configure-groq":
        from .credentials import KEY_FILE, save_key, load_key
        if args.import_legacy:
            source = ROOT.parent / "browser-agent" / ".groq-key.dpapi"
            if KEY_FILE.exists():
                raise RuntimeError("v2 already has a key; use interactive configuration to replace it")
            encrypted = source.read_bytes()
            from .credentials import _protect
            _protect(encrypted, decrypt=True)  # Verify access without printing the key.
            KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            KEY_FILE.write_bytes(encrypted)
        else:
            save_key(getpass.getpass("Groq API key (hidden): "))
        print("Groq key saved for the current Windows user.")
        return 0
    if command == "check-groq":
        from .groq_client import GroqClient, GroqError
        try:
            answer = GroqClient(store).answer({"text":"What is 2 + 2?", "kind":"number", "options":[]})
        except GroqError as exc:
            store.record_event("ERROR", "GROQ_CHECK_FAILED", str(exc))
            print(f"GROQ_CHECK_FAILED: {exc}")
            return 2
        if float(answer["text"]) != 4:
            raise RuntimeError("Groq returned an unexpected test answer")
        print("Groq connection OK; synthetic question answered.")
        store.record_event("INFO", "GROQ_CHECK_OK", "Synthetic arithmetic question answered; no Coursera submission")
        return 0
    if command == "status":
        print(json.dumps(store.snapshot(), ensure_ascii=False, indent=2))
        return 0
    if command == "pause":
        control.request_pause()
        return 0
    if command == "resume":
        control.request_resume()
        return 0
    if command == "stop":
        control.request_stop()
        return 0
    if command in {"skip", "unskip"}:
        store.set_course_skipped(args.course_id, command == "skip")
        return 0
    supervisor = WorkerSupervisor(store, control, port=args.chrome_port)
    if command in {"scan", "run"}:
        supervisor.start(scan_only=command == "scan")
        supervisor.join()
        return 0 if supervisor.last_error is None else 2
    if command == "serve":
        dashboard = Dashboard(store, control, port=args.panel_port, start_callback=supervisor.start)
        if args.autostart:
            supervisor.start()
        print(f"Control panel: http://127.0.0.1:{dashboard.port}/")
        print("The first Coursera page opened by v2 is My Learning → In Progress.")
        try:
            dashboard.serve_forever()
        except KeyboardInterrupt:
            control.request_stop()
            supervisor.join(timeout=15)
        return 0
    raise AssertionError(command)


if __name__ == "__main__":
    raise SystemExit(main())
