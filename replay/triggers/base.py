"""Trigger interface and debounce. Triggers know nothing about video."""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime

# Called with (court_id, triggered_at in UTC). Must return quickly.
TriggerCallback = Callable[[str, datetime], None]


class TriggerError(Exception):
    """Trigger could not be started."""


class Trigger(ABC):
    @abstractmethod
    def start(self, callback: TriggerCallback) -> None:
        """Starts listening in the background."""

    @abstractmethod
    def stop(self) -> None:
        """Stops listening."""


class Debouncer:
    """Accepts at most one event per court every `interval_s` seconds."""

    def __init__(self, interval_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._interval_s = interval_s
        self._clock = clock
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, court_id: str) -> bool:
        now = self._clock()
        with self._lock:
            last = self._last.get(court_id)
            if last is not None and now - last < self._interval_s:
                return False
            self._last[court_id] = now
            return True
