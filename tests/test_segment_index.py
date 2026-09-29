from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from replay.clipper.segment_index import candidate_segments, resolve_ends, select_window
from replay.models import Segment

T0 = datetime(2026, 9, 28, 15, 0, 0, tzinfo=UTC)


def s(offset_s: float) -> datetime:
    return T0 + timedelta(seconds=offset_s)


def seg(start_s: float, end_s: float | None, name: str | None = None) -> Segment:
    return Segment(
        "court1", Path(name or f"{start_s:g}.ts"), s(start_s), None if end_s is None else s(end_s)
    )


def contiguous(count: int, step: float = 2) -> list[Segment]:
    return [seg(i * step, (i + 1) * step if i + 1 < count else None) for i in range(count)]


def test_full_coverage_without_gaps() -> None:
    segments = [seg(i * 2, i * 2 + 2) for i in range(20)]  # 0..40 s, all closed
    selection = select_window(segments, s(5), s(35))
    assert selection is not None
    assert selection.start_at == s(4)
    assert selection.end_at == s(36)
    assert [x.start_at for x in selection.segments] == [s(t) for t in range(4, 35, 2)]
    assert selection.gaps == []


def test_nothing_in_window() -> None:
    segments = [seg(0, 2), seg(2, 4)]
    assert select_window(segments, s(100), s(130)) is None
    assert select_window([], s(0), s(30)) is None


def test_gap_in_the_middle_and_edges() -> None:
    # Recording from 10 to 20 s, then outage, then 26 to 32 s
    segments = [seg(10, 12), seg(12, 14), seg(14, 16), seg(16, 18), seg(18, 20)]
    segments += [seg(26, 28), seg(28, 30), seg(30, 32)]
    selection = select_window(segments, s(5), s(35))
    assert selection is not None
    gaps = [(g.start_at, g.end_at) for g in selection.gaps]
    assert gaps == [(s(5), s(10)), (s(20), s(26)), (s(32), s(35))]
    assert selection.gap_seconds == 14


def test_small_misalignment_is_not_a_gap() -> None:
    # Names have 1 s resolution: 1 s holes are rounding, not outages
    segments = [seg(0, 2), seg(3, 5), seg(5, 7)]
    selection = select_window(segments, s(0), s(7))
    assert selection is not None
    assert selection.gaps == []


def test_candidates_skip_open_segment() -> None:
    segments = contiguous(5)  # the last one (8 s) is still recording
    candidates = candidate_segments(segments, s(0), s(20))
    assert [c.start_at for c in candidates] == [s(0), s(2), s(4), s(6)]


def test_resolve_ends_uses_real_duration_to_expose_outage() -> None:
    # The listing says the 4 s segment ends at 20 s (next start), but it only has 2 s
    segments = [seg(0, 2, "a"), seg(2, 4, "b"), seg(4, 20, "c"), seg(20, 22, "d")]
    durations = {"a": 2.0, "b": 2.1, "c": 2.0, "d": None}
    resolved = resolve_ends(segments, lambda p: durations[p.name])
    assert [r.end_at for r in resolved] == [s(2), s(4), s(6), s(22)]

    selection = select_window(resolved, s(0), s(22))
    assert selection is not None
    assert [(g.start_at, g.end_at) for g in selection.gaps] == [(s(6), s(20))]


def test_resolve_ends_is_capped_by_next_start() -> None:
    # Duration a little longer than the name spacing (1 s name resolution)
    resolved = resolve_ends([seg(0, 2)], lambda _: 2.6)
    assert resolved[0].end_at == s(2)
