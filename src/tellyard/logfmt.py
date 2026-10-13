import logging
import re
import sys
import traceback
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

LEVELS = {logging.DEBUG: "DBG", logging.INFO: "INF", logging.WARNING: "WRN", logging.ERROR: "ERR"}
RECORD_FIELDS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}
NEEDS_QUOTES = re.compile(r'^"|[\s="\\]|[\x00-\x1f\x7f]')
ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
LABEL_WIDTH = 11


def value(raw: object) -> str:
    # Escape control characters. A raw newline can forge a log record.
    if raw is None:
        return ""
    text = ("true" if raw else "false") if isinstance(raw, bool) else str(raw)
    if not text:
        return '""'
    if not NEEDS_QUOTES.search(text):
        return text
    escaped = "".join(ESCAPES.get(ch, f"\\u{ord(ch):04x}" if ord(ch) < 0x20 or ord(ch) == 0x7F else ch) for ch in text)
    return f'"{escaped}"'


def fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in RECORD_FIELDS and not k.startswith("_")}


class Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        level = LEVELS.get(record.levelno, "ERR" if record.levelno > logging.ERROR else "DBG")
        parts = [stamp, level, f"msg={value(record.getMessage())}"]
        parts += [f"{key}={value(raw)}" for key, raw in fields(record).items()]
        if record.exc_info and record.exc_info[1] is not None:
            parts.append(f"error={value(f'{type(record.exc_info[1]).__name__}: {record.exc_info[1]}')}")
            parts.append(f"stack={value(''.join(traceback.format_exception(*record.exc_info)).rstrip())}")
        return " ".join(parts)


def setup(level: str) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(Formatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


def banner(title: str, rows: Sequence[tuple[str, str]]) -> str:
    return "\n".join(["", f"  {title}", *(f"  {label.ljust(LABEL_WIDTH)}{text}" for label, text in rows)])
