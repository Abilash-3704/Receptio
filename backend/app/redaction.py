"""Keep Patient names and phone numbers out of log output.

`install_log_redaction()` wraps the logging record factory, so EVERY log record of the process is
cleaned as it is created, whichever logger wrote it (ours, uvicorn, httpx, LiveKit) and whichever
handler is attached later: the message with its arguments filled in, a traceback, a stack.

What is recognised:
- phone numbers: any run of 7 to 15 digits, optionally with a leading +, and spaces, hyphens or
  brackets between digits. Calendar dates are left alone.
- names: the names this process was told about with `remember_name` (the backend does it for every
  name it validates; the agent for every name it identifies), whole and by part (3+ letters).

What is NOT covered, said plainly:
- the database and the stored trace keep everything in full on purpose: staff need it;
- a name nobody told this process about is not recognised in free text, and a phone number
  spoken as words is not a digit run. The first-party code does not log what was said at all
  (Call ids, counts and timings only); this is the safety net for what it did not foresee;
- the agent and the backend are separate processes, each with its own list of names;
- `print()` and code that writes to a file directly bypass logging.
The registry is bounded and lives in memory only."""

import logging
import re
import threading
from collections import OrderedDict
from typing import Any

PHONE_PLACEHOLDER = "[phone]"
NAME_PLACEHOLDER = "[name]"
MAX_NAMES = 500
MIN_NAME_PART = 3

# A date comes first so that 2026-11-02 is never taken for a phone number.
_NUMBER = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})"
    r"|(?<![\w.])(?P<phone>\(?\+?\d(?:[ ()\-]{0,2}\d){6,14})(?!\w)"
)

_lock = threading.Lock()
_names: OrderedDict[str, None] = OrderedDict()
_name_pattern: re.Pattern[str] | None = None


def remember_name(full_name: str) -> None:
    """Tell the redactor a Patient name: it is removed from every log line from now on."""
    global _name_pattern
    name = " ".join(full_name.split())
    if not name:
        return
    with _lock:
        _names.pop(name.casefold(), None)
        _names[name.casefold()] = None
        while len(_names) > MAX_NAMES:
            _names.popitem(last=False)
        _name_pattern = None


def forget_names() -> None:
    global _name_pattern
    with _lock:
        _names.clear()
        _name_pattern = None


def remembered_count() -> int:
    with _lock:
        return len(_names)


def _pattern() -> re.Pattern[str] | None:
    global _name_pattern
    with _lock:
        if _name_pattern is None and _names:
            terms: set[str] = set()
            for name in _names:
                terms.add(name)
                terms.update(p for p in re.split(r"[\s,]+", name) if len(p) >= MIN_NAME_PART)
            # Longest first, so "asha menon" wins over "asha".
            ordered = sorted(terms, key=len, reverse=True)
            alternatives = "|".join(re.escape(t).replace(r"\ ", r"\s+") for t in ordered)
            _name_pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)
        return _name_pattern


def redact_text(text: str) -> str:
    text = _NUMBER.sub(lambda m: m.group("date") or PHONE_PLACEHOLDER, text)
    pattern = _pattern()
    return text if pattern is None else pattern.sub(NAME_PLACEHOLDER, text)


_installed = False


def install_log_redaction() -> None:
    """Idempotent. Call once at the start of every process that logs (backend, agent, voice
    worker, web service, eval command)."""
    global _installed
    if _installed:
        return
    _installed = True
    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not become a different error
            message = str(record.msg)
        record.msg, record.args = redact_text(message), None
        if record.exc_info and record.exc_info[0] is not None:
            record.exc_text = redact_text(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        return record

    logging.setLogRecordFactory(factory)
