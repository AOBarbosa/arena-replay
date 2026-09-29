"""Logging setup shared by all services."""

from __future__ import annotations

import logging
import os


def setup_logging(service: str) -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format=f"%(asctime)s %(levelname)-7s {service} %(name)s: %(message)s",
    )
