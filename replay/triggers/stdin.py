"""Terminal trigger for development: Enter fires the first court, or type a court_id."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TextIO

from replay.triggers.base import Debouncer, Trigger, TriggerCallback

log = logging.getLogger(__name__)


class StdinTrigger(Trigger):
    def __init__(
        self, court_ids: Sequence[str], debounce_s: float, stream: TextIO | None = None
    ) -> None:
        self._court_ids = list(court_ids)
        self._debouncer = Debouncer(debounce_s)
        self._stream = stream or sys.stdin
        self._thread: threading.Thread | None = None

    def start(self, callback: TriggerCallback) -> None:
        self._thread = threading.Thread(
            target=self._read_loop, args=(callback,), name="trigger-stdin", daemon=True
        )
        self._thread.start()
        log.info(
            "stdin trigger ready: press Enter for %s or type a court id (%s)",
            self._court_ids[0],
            ", ".join(self._court_ids),
        )

    def stop(self) -> None:
        # Reading stdin cannot be interrupted; the daemon thread dies with the process
        pass

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _read_loop(self, callback: TriggerCallback) -> None:
        for line in self._stream:
            court_id = line.strip() or self._court_ids[0]
            if court_id not in self._court_ids:
                log.warning("unknown court %r (valid: %s)", court_id, ", ".join(self._court_ids))
                continue
            if not self._debouncer.allow(court_id):
                log.info("[%s] trigger ignored (debounce)", court_id)
                continue
            log.info("[%s] trigger fired from stdin", court_id)
            callback(court_id, datetime.now(UTC))
