"""Logging setup shared by all services: console + one rotated file per service.

The console colors the level name when it is a terminal (not under systemd/journald or
when redirected); `NO_COLOR=1` disables and `FORCE_COLOR=1` forces it. Files stay plain.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_RETENTION_DAYS = 14

RESET = "\033[0m"
LEVEL_COLORS = {
    logging.DEBUG: "\033[2m",  # dim
    logging.INFO: "\033[32m",  # green
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[1;41m",  # bold on red background
}


def _log_format(service: str) -> str:
    return f"%(asctime)s %(levelname)-7s {service} %(name)s: %(message)s"


def setup_logging(service: str) -> None:
    """Console logging. Call first, so configuration errors are visible."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format=_log_format(service))
    for handler in logging.getLogger().handlers:
        handler.addFilter(OnlyFailedRequests())
        if use_color(getattr(handler, "stream", None)):
            handler.setFormatter(ColorFormatter(_log_format(service)))


def use_color(stream: object, env: dict[str, str] | None = None) -> bool:
    """Colors only on a terminal, unless overridden by NO_COLOR / FORCE_COLOR."""
    env = dict(os.environ) if env is None else env
    if env.get("NO_COLOR"):
        return False
    if env.get("FORCE_COLOR"):
        return True
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty()) and env.get("TERM") != "dumb"


def add_file_logging(service: str, logs_dir: Path) -> Path:
    """Adds `<logs_dir>/<service>.log`, rotated at midnight, kept for 14 days."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"{service}.log"
    handler = TimedRotatingFileHandler(
        path, when="midnight", backupCount=LOG_RETENTION_DAYS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_log_format(service)))
    handler.addFilter(OnlyFailedRequests())
    logging.getLogger().addHandler(handler)
    return path


class ColorFormatter(logging.Formatter):
    """Colors the level name (padded, so columns stay aligned); ERROR and above also
    color the message."""

    def format(self, record: logging.LogRecord) -> str:
        color = LEVEL_COLORS.get(record.levelno)
        if color is None:
            return super().format(record)
        original_levelname, original_msg, original_args = (
            record.levelname,
            record.msg,
            record.args,
        )
        record.levelname = f"{color}{record.levelname:<7}{RESET}"
        if record.levelno >= logging.ERROR:
            record.msg = f"{color}{record.getMessage()}{RESET}"
            record.args = None
        try:
            return super().format(record)
        finally:
            record.levelname, record.msg, record.args = (
                original_levelname,
                original_msg,
                original_args,
            )


class OnlyFailedRequests(logging.Filter):
    """Drops uvicorn access logs unless the response is an error: the dev page
    polls the API every few seconds."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "uvicorn.access":
            return True
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            try:
                return int(args[4]) >= 400
            except (TypeError, ValueError):
                return True
        return True
