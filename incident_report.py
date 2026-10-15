"""Small, private incident records for a stopped course run.

Callers pass the run's gitignored log directory.  The report deliberately
excludes exception messages, page contents, URLs, and tracebacks: those may
contain assessment text or credentials.
"""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from course_state import atomic_json
from recovery_policy import READ_OPERATIONS


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9_:-]{1,160}\Z", re.ASCII)
_SENSITIVE_CODE = re.compile(
    r"(?:\A(?:sk|gsk)[-_]|\Agh[pousr]_|\Agithub_pat_|\AAIza|"
    r"(?:\A|[-_:])(?:api[-_]?key|token|secret|password|credential|bearer)(?:[-_:]|\Z))",
    re.IGNORECASE,
)
_OPERATIONS = READ_OPERATIONS | frozenset({
    "course_start", "supplement", "lecture", "assignment", "ungradedLab",
    "coach", "programming", "peer", "certificate", "course_completion",
})
_RETRY_CODES = frozenset({"dom_hydration", "cdp_disconnect", "timeout", "io_error"})
_ERROR_CLASSES = frozenset({
    "StopRun", "TimeoutError", "OSError", "PermissionError", "ConnectionError",
    "RuntimeError", "ValueError", "KeyError", "TypeError", "LookupError",
    "FileNotFoundError", "IndexError", "AssertionError", "JSONDecodeError",
    "CancelledError", "Error",
})


def _identifier_fingerprint(value):
    """Correlate course/items without persisting their untrusted raw values."""
    if (not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value) or
            _SENSITIVE_CODE.search(value)):
        return "unknown"
    return "sha256:" + hashlib.sha256(value.encode("ascii")).hexdigest()[:16]


def _allowed_code(value, allowed):
    return value if isinstance(value, str) and value in allowed else "unknown"


def write_incident(directory: Path, *, course_id, operation, exc,
                   retry_code=None, item_key=None) -> Path:
    """Atomically replace ``incident.json`` with a redacted failure summary.

    Only allowlisted operation/retry codes are copied verbatim. Course and
    item identities are short fingerprints; invalid or URL-shaped inputs are
    replaced with ``unknown`` instead of partially copying sensitive text.
    """
    path = Path(directory) / "incident.json"
    report = {
        "schema_version": 1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "course_id": _identifier_fingerprint(course_id),
        "operation": _allowed_code(operation, _OPERATIONS),
        "error_class": _allowed_code(type(exc).__name__, _ERROR_CLASSES),
        "retry_code": _allowed_code(retry_code, _RETRY_CODES) if retry_code is not None else None,
        "item_key": _identifier_fingerprint(item_key) if item_key is not None else None,
        "recovery": "Check the current course page and saved checkpoint, then resume the run. Review any ambiguous submission before retrying.",
    }
    atomic_json(path, report)
    return path
