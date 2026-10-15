"""Conservative retries for repeatable Coursera page reads.

Callers must wrap only operations that can safely be repeated. In particular,
this helper must never surround Submit, answer selection, feedback mutation, or
any action that consumes an attempt. It does not turn an ambiguous result into
success; the original exception is raised after a small number of retries.
"""

from __future__ import annotations

import asyncio
import inspect


READ_OPERATIONS = frozenset({
    "discover_modules",
    "discover_roster",
    "open_item",
    "inspect_sidebar",
    "verify_mark",
    "my_learning",
})

_UNSAFE_MESSAGES = (
    "submit", "feedback", "grade", "certificate", "manual", "authorization",
    "sign in", "log in", "honor code", "attempt", "permission", "forbidden",
)

_DISCONNECT_MESSAGES = (
    "no close frame", "connection closed", "websocket connection is closed",
    "websocket connection closed", "connection reset by peer",
    "cannot find context with specified id", "execution context was destroyed",
)


def classify_transient(exc: BaseException, operation: str) -> str | None:
    """Return a stable retry reason for a safe read, or ``None``.

    Diagnostic fields are inspected only for the known module-roster hydration
    race. A generic ``needs review`` error is not evidence of a temporary DOM.
    """
    if operation not in READ_OPERATIONS:
        return None
    message = str(exc).lower()
    # These are errors emitted by the read-only lesson opener. The text after
    # the colon is a lesson title and may itself contain words like "Graded".
    if operation == "open_item" and (
        message.startswith("the lesson did not load:") or
        message.startswith("the lesson loaded, but its course outline did not show the item")
    ):
        return "dom_hydration"
    if any(marker in message for marker in _UNSAFE_MESSAGES):
        return None
    if any(marker in message for marker in _DISCONNECT_MESSAGES):
        return "cdp_disconnect"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, OSError):
        return "io_error"

    if operation == "discover_modules" and "could not verify the full module list" in message:
        return "dom_hydration"
    if operation == "discover_roster" and "roster needs review" in message:
        diagnostic = getattr(exc, "diagnostic", None)
        if isinstance(diagnostic, dict):
            snapshot = diagnostic.get("snapshot")
            if isinstance(snapshot, dict) and snapshot.get("error_code") in {
                "lessons_not_ready", "wrong_path"
            }:
                return "dom_hydration"
            if isinstance(snapshot, dict) and not snapshot.get("warnings") and (
                "the lessons navigation was incomplete or unstable" in message
            ):
                return "dom_hydration"
        return None
    if operation == "my_learning" and (
        "did not show a stable course list and pagination" in message or
        "pagination changed before" in message or
        "page did not load" in message
    ):
        return "dom_hydration"
    return None


async def retry_read(action, *, operation: str, tab=None, event=None,
                     attempts: int = 3, sleep=asyncio.sleep):
    """Run an idempotent read with at most ``attempts`` total tries.

    ``event`` receives only stable, non-sensitive keyword fields:
    ``operation``, ``code``, ``attempt`` (failed try, starting at 1), and
    ``remaining``. A successful retry emits ``code='recovered'`` with its
    successful attempt number. Event and reconnect failures do not erase the
    original read error. Delays are 1, 2, and 4 seconds, bounded to eight.
    """
    if operation not in READ_OPERATIONS:
        raise ValueError("The operation is not approved for read retries.")
    if type(attempts) is not int or not 1 <= attempts <= 5:
        raise ValueError("Read retry attempts must be an integer from 1 to 5.")

    async def report(code, attempt):
        if event is None:
            return
        try:
            recorded = event(operation=operation, code=code,
                             attempt=attempt, remaining=attempts - attempt)
            if inspect.isawaitable(recorded):
                await recorded
        except Exception:
            # Telemetry must not change the outcome of a course read.
            pass

    for attempt in range(1, attempts + 1):
        try:
            result = await action()
            if attempt > 1:
                await report("recovered", attempt)
            return result
        except Exception as exc:
            code = classify_transient(exc, operation)
            if code is None or attempt == attempts:
                raise
            await report(code, attempt)
            if code == "cdp_disconnect" and tab is not None and callable(getattr(tab, "connect", None)):
                try:
                    await tab.connect()
                except Exception:
                    pass
            await sleep(min(2 ** (attempt - 1), 8))
