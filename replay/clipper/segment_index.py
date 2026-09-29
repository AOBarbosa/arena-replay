"""Finds the segments that cover a time window and reports gaps. Pure functions, no I/O.

All times here are "stream times": the UTC time in the segment names.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from replay.models import Segment

# Differences smaller than this are not gaps: names have 1 s resolution
GAP_TOLERANCE = timedelta(seconds=1.5)


@dataclass(frozen=True, slots=True)
class Gap:
    start_at: datetime
    end_at: datetime

    @property
    def seconds(self) -> float:
        return (self.end_at - self.start_at).total_seconds()


@dataclass(frozen=True, slots=True)
class WindowSelection:
    segments: list[Segment]
    gaps: list[Gap]

    @property
    def start_at(self) -> datetime:
        return self.segments[0].start_at

    @property
    def end_at(self) -> datetime:
        end = self.segments[-1].end_at
        assert end is not None
        return end

    @property
    def gap_seconds(self) -> float:
        return sum(gap.seconds for gap in self.gaps)


def candidate_segments(
    segments: Sequence[Segment], start_at: datetime, end_at: datetime
) -> list[Segment]:
    """Closed segments that may overlap the window, using the listing's
    (upper-bound) ends. Only these need their real duration probed."""
    return [
        s for s in segments if s.end_at is not None and s.start_at < end_at and s.end_at > start_at
    ]


def resolve_ends(
    segments: Sequence[Segment], probe_duration: Callable[[Path], float | None]
) -> list[Segment]:
    """Replaces each `end_at` (next segment's start) with start + real duration.

    After a stream outage, "next start" would make the last segment before the gap
    look like it covers the whole gap. The result is capped at the listing's end,
    because names only have 1 s resolution. Without a duration, the listing's end
    is kept."""
    resolved: list[Segment] = []
    for segment in segments:
        duration = probe_duration(segment.path)
        if duration is None or segment.end_at is None:
            resolved.append(segment)
            continue
        real_end = segment.start_at + timedelta(seconds=duration)
        resolved.append(replace(segment, end_at=min(real_end, segment.end_at)))
    return resolved


def select_window(
    segments: Sequence[Segment], start_at: datetime, end_at: datetime
) -> WindowSelection | None:
    """Segments (with real ends) that overlap [start_at, end_at), in order,
    plus the gaps inside the window. None if nothing overlaps."""
    chosen = [
        s
        for s in sorted(segments, key=lambda s: s.start_at)
        if s.end_at is not None and s.start_at < end_at and s.end_at > start_at
    ]
    if not chosen:
        return None

    gaps: list[Gap] = []
    cursor = start_at
    for segment in chosen:
        assert segment.end_at is not None
        if segment.start_at - cursor > GAP_TOLERANCE:
            gaps.append(Gap(cursor, segment.start_at))
        cursor = max(cursor, segment.end_at)
    if end_at - cursor > GAP_TOLERANCE:
        gaps.append(Gap(cursor, end_at))
    return WindowSelection(segments=chosen, gaps=gaps)
