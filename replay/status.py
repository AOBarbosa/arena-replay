"""Service heartbeats, shared by capture/clipper (writers) and the API (reader).

Each service rewrites `<status_dir>/<service>.json` every few seconds. A heartbeat
older than STALE_AFTER means the process is gone (crash, kill -9, power loss); a
clean shutdown writes `state: "stopped"` instead.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 5.0
STALE_AFTER = timedelta(seconds=15)

RUNNING = "running"
STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class ServiceHeartbeat:
    service: str
    state: str  # running | stopped (as written by the service)
    pid: int
    started_at: datetime
    updated_at: datetime
    details: dict[str, Any]

    def is_stale(self, now: datetime) -> bool:
        return self.state == RUNNING and now - self.updated_at > STALE_AFTER


def status_path(status_dir: Path, service: str) -> Path:
    return status_dir / f"{service}.json"


def write_heartbeat(
    status_dir: Path,
    service: str,
    state: str,
    started_at: datetime,
    details: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Atomic write (temporary file + rename): readers never see half a file."""
    status_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "service": service,
        "state": state,
        "pid": os.getpid(),
        "started_at": started_at.isoformat(),
        "updated_at": (now or datetime.now(UTC)).isoformat(),
        "details": details,
    }
    path = status_path(status_dir, service)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, default=_json_default), encoding="utf-8")
    tmp.replace(path)


def read_heartbeat(status_dir: Path, service: str) -> ServiceHeartbeat | None:
    """None if the service never ran here or the file is unreadable."""
    path = status_path(status_dir, service)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ServiceHeartbeat(
            service=data["service"],
            state=data["state"],
            pid=int(data["pid"]),
            started_at=datetime.fromisoformat(data["started_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            details=data.get("details") or {},
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("unreadable heartbeat %s: %s", path.name, exc)
        return None


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class Heartbeat(threading.Thread):
    """Writes `collect()` as the service's details every HEARTBEAT_INTERVAL_S."""

    def __init__(
        self,
        status_dir: Path,
        service: str,
        collect: Callable[[], dict[str, Any]],
        interval_s: float = HEARTBEAT_INTERVAL_S,
    ) -> None:
        super().__init__(name=f"heartbeat-{service}", daemon=True)
        self._status_dir = status_dir
        self._service = service
        self._collect = collect
        self._interval_s = interval_s
        self._stop_event = threading.Event()
        self._started_at = datetime.now(UTC)

    def run(self) -> None:
        while True:
            self._write(RUNNING)
            if self._stop_event.wait(self._interval_s):
                return

    def stop(self) -> None:
        """Stops the loop and records a clean shutdown."""
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=self._interval_s + 1)
        self._write(STOPPED)

    def _write(self, state: str) -> None:
        try:
            write_heartbeat(
                self._status_dir, self._service, state, self._started_at, self._collect()
            )
        except Exception:
            log.exception("could not write the %s heartbeat", self._service)
