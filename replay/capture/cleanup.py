"""Circular buffer: removes segments outside the window, honoring job leases."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from replay.buffer import Lease, list_segments, read_active_leases
from replay.models import Segment

log = logging.getLogger(__name__)

# A lease not updated for longer than this belongs to a dead job
LEASE_MAX_AGE = timedelta(minutes=10)
# Margin around the lease window (stream latency, keyframes)
LEASE_MARGIN = timedelta(seconds=10)


def select_expired(
    segments: Sequence[Segment], now: datetime, keep: timedelta, leases: Sequence[Lease]
) -> list[Segment]:
    """Segments that ended before `now - keep` and do not overlap any lease.
    The last segment (no `end_at`, possibly still recording) is never deleted."""
    cutoff = now - keep
    expired: list[Segment] = []
    for segment in segments:
        if segment.end_at is None or segment.end_at > cutoff:
            continue
        if any(_overlaps(segment, lease) for lease in leases):
            continue
        expired.append(segment)
    return expired


def _overlaps(segment: Segment, lease: Lease) -> bool:
    assert segment.end_at is not None
    return (
        segment.start_at < lease.end_at + LEASE_MARGIN
        and segment.end_at > lease.start_at - LEASE_MARGIN
    )


def cleanup_court(segments_dir: Path, court_id: str, now: datetime, keep: timedelta) -> int:
    """Deletes a court's expired segments. Returns how many were deleted."""
    leases = read_active_leases(segments_dir, court_id, now, LEASE_MAX_AGE)
    expired = select_expired(list_segments(segments_dir, court_id), now, keep, leases)
    removed = 0
    for segment in expired:
        try:
            segment.path.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            log.warning("[%s] could not delete %s: %s", court_id, segment.path.name, exc)
    if removed:
        log.debug("[%s] deleted %d old segment(s)", court_id, removed)
    return removed


class CleanupLoop(threading.Thread):
    """Periodically cleans up every court."""

    def __init__(
        self,
        segments_dir: Path,
        court_ids: Sequence[str],
        keep: timedelta,
        interval_s: float = 10.0,
    ) -> None:
        super().__init__(name="capture-cleanup", daemon=True)
        self._segments_dir = segments_dir
        self._court_ids = list(court_ids)
        self._keep = keep
        self._interval_s = interval_s
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while True:
            for court_id in self._court_ids:
                try:
                    cleanup_court(self._segments_dir, court_id, datetime.now(UTC), self._keep)
                except Exception:
                    log.exception("[%s] buffer cleanup error", court_id)
            if self._stop_event.wait(self._interval_s):
                return
