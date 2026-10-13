import logging
import re
import sys

from tellyard import logfmt


def record(msg: str, level: int = logging.INFO, **extra: object) -> logging.LogRecord:
    rec = logging.makeLogRecord({"msg": msg, "levelno": level, "levelname": logging.getLevelName(level)})
    rec.__dict__.update(extra)
    return rec


def test_a_record_is_one_logfmt_line() -> None:
    line = logfmt.Formatter().format(record("stream started", channel="101", title="News One", height=1080))
    assert re.fullmatch(
        r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z INF msg="stream started" channel=101 title="News One" height=1080',
        line,
    )


def test_levels_use_three_letters() -> None:
    fmt = logfmt.Formatter()
    levels = [fmt.format(record("x", level)).split()[1] for level in (5, 10, 20, 30, 40, 50)]
    assert levels == ["DBG", "DBG", "INF", "WRN", "ERR", "ERR"]


def test_values_are_quoted_and_escaped() -> None:
    line = logfmt.Formatter().format(record("source failed", reason='said "no"\nINF msg=forged', empty="", on=True))
    assert line.endswith(r'reason="said \"no\"\nINF msg=forged" empty="" on=true')
    assert "\n" not in line
    assert logfmt.value("a=b") == '"a=b"'
    assert logfmt.value("\x07") == '"\\u0007"'
    assert logfmt.value(None) == ""


def test_an_exception_stays_on_one_line() -> None:
    try:
        raise ValueError("bad\nvalue")
    except ValueError:
        rec = logging.getLogger("t").makeRecord("t", logging.ERROR, "f", 1, "guide rebuild failed", (), sys.exc_info())
    line = logfmt.Formatter().format(rec)
    assert "\n" not in line
    assert 'error="ValueError: bad\\nvalue"' in line
    assert 'stack="Traceback' in line


def test_setup_installs_one_handler() -> None:
    logfmt.setup("debug")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, logfmt.Formatter)
    assert root.level == logging.DEBUG


def test_the_banner_aligns_labels() -> None:
    assert logfmt.banner("Tellyard 1.0", [("Tuners", "4"), ("Heartbeat", "off")]).splitlines() == [
        "",
        "  Tellyard 1.0",
        "  Tuners     4",
        "  Heartbeat  off",
    ]
