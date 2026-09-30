from __future__ import annotations

import io
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from replay.logs import (
    LEVEL_COLORS,
    RESET,
    ColorFormatter,
    OnlyFailedRequests,
    add_file_logging,
    use_color,
)
from replay.status import (
    RUNNING,
    STOPPED,
    Heartbeat,
    read_heartbeat,
    status_path,
    write_heartbeat,
)

T0 = datetime(2026, 9, 28, 15, 0, 0, tzinfo=UTC)


def test_heartbeat_roundtrip_and_staleness(tmp_path: Path) -> None:
    write_heartbeat(tmp_path, "capture", RUNNING, T0, {"courts": {"court1": {"at": T0}}}, now=T0)
    hb = read_heartbeat(tmp_path, "capture")
    assert hb is not None
    assert (hb.service, hb.state, hb.started_at, hb.updated_at) == ("capture", RUNNING, T0, T0)
    assert hb.details == {"courts": {"court1": {"at": T0.isoformat()}}}
    assert not hb.is_stale(T0 + timedelta(seconds=15))
    assert hb.is_stale(T0 + timedelta(seconds=16))


def test_stopped_heartbeat_is_never_stale(tmp_path: Path) -> None:
    write_heartbeat(tmp_path, "clipper", STOPPED, T0, {}, now=T0)
    hb = read_heartbeat(tmp_path, "clipper")
    assert hb is not None and not hb.is_stale(T0 + timedelta(days=1))


def test_missing_or_broken_heartbeat(tmp_path: Path) -> None:
    assert read_heartbeat(tmp_path, "capture") is None
    status_path(tmp_path, "capture").write_text("{half a file")
    assert read_heartbeat(tmp_path, "capture") is None


def test_heartbeat_thread_writes_running_then_stopped(tmp_path: Path) -> None:
    calls = []
    heartbeat = Heartbeat(tmp_path, "capture", lambda: {"n": len(calls) or calls.append(1)})
    heartbeat.start()
    for _ in range(50):
        hb = read_heartbeat(tmp_path, "capture")
        if hb is not None:
            break
        heartbeat.join(0.02)
    assert hb is not None and hb.state == RUNNING
    heartbeat.stop()
    assert not heartbeat.is_alive()
    stopped = read_heartbeat(tmp_path, "capture")
    assert stopped is not None and stopped.state == STOPPED


def test_heartbeat_survives_collect_errors(tmp_path: Path) -> None:
    def broken() -> dict:
        raise RuntimeError("boom")

    heartbeat = Heartbeat(tmp_path, "capture", broken)
    heartbeat.stop()  # never started: just writes the final state, which fails quietly
    assert read_heartbeat(tmp_path, "capture") is None


def test_file_logging(tmp_path: Path) -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = add_file_logging("capture", tmp_path / "logs")
        logging.getLogger("replay.test").warning("[court1] camera offline")
        for handler in root.handlers:
            handler.flush()
        assert "WARNING capture replay.test: [court1] camera offline" in path.read_text()
    finally:
        for handler in root.handlers[len(before) :]:
            handler.close()
            root.removeHandler(handler)


def access_record(status: int) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/v1/status", "1.1", status), None,
    )  # fmt: skip


def test_access_log_filter_keeps_only_errors() -> None:
    log_filter = OnlyFailedRequests()
    assert not log_filter.filter(access_record(200))
    assert not log_filter.filter(access_record(206))
    assert log_filter.filter(access_record(404))
    assert log_filter.filter(access_record(500))
    other = logging.LogRecord("replay.x", logging.INFO, __file__, 1, "hello", None, None)
    assert log_filter.filter(other)


def test_use_color_only_on_terminals() -> None:
    class Tty:
        def isatty(self) -> bool:
            return True

    assert use_color(Tty(), env={"TERM": "xterm"})
    assert not use_color(Tty(), env={"TERM": "xterm", "NO_COLOR": "1"})
    assert not use_color(Tty(), env={"TERM": "dumb"})
    assert not use_color(io.StringIO(), env={})
    assert use_color(io.StringIO(), env={"FORCE_COLOR": "1"})


def test_color_formatter_colors_level_and_keeps_record_intact() -> None:
    formatter = ColorFormatter("%(levelname)-7s %(message)s")
    warning = logging.LogRecord(
        "replay.x", logging.WARNING, __file__, 1, "[%s] slow", ("c1",), None
    )
    assert formatter.format(warning) == f"{LEVEL_COLORS[logging.WARNING]}WARNING{RESET} [c1] slow"
    error = logging.LogRecord("replay.x", logging.ERROR, __file__, 1, "[%s] down", ("c1",), None)
    red = LEVEL_COLORS[logging.ERROR]
    assert formatter.format(error) == f"{red}ERROR  {RESET} {red}[c1] down{RESET}"
    # The same record then reaches the file handler without color codes
    assert logging.Formatter("%(levelname)s %(message)s").format(error) == "ERROR [c1] down"
