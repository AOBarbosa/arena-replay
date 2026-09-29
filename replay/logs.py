"""Logging setup shared by all services: console + one rotated file per service."""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_RETENTION_DAYS = 14


def _log_format(service: str) -> str:
    return f"%(asctime)s %(levelname)-7s {service} %(name)s: %(message)s"


def setup_logging(service: str) -> None:
    """Console logging. Call first, so configuration errors are visible."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format=_log_format(service))
    for handler in logging.getLogger().handlers:
        handler.addFilter(OnlyFailedRequests())


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
