from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from replay.buffer import Lease, list_segments, write_lease
from replay.capture.cleanup import cleanup_court, select_expired
from replay.models import Segment

T0 = datetime(2026, 9, 28, 15, 0, 0, tzinfo=UTC)
KEEP = timedelta(minutes=5)


def segments(count: int, step_s: int = 2) -> list[Segment]:
    starts = [T0 + timedelta(seconds=i * step_s) for i in range(count)]
    return [
        Segment("court1", Path(f"s{i}.ts"), start, starts[i + 1] if i + 1 < count else None)
        for i, start in enumerate(starts)
    ]


def lease(start: datetime, end: datetime) -> Lease:
    return Lease("job", "court1", start, end, Path("job.json"))


def test_expires_only_segments_older_than_buffer() -> None:
    segs = segments(10)  # 15:00:00 .. 15:00:18
    now = T0 + KEEP + timedelta(seconds=7)  # cutoff at 15:00:07
    expired = select_expired(segs, now, KEEP, [])
    # Segments ending by 15:00:06 (s0, s1, s2)
    assert [s.path.name for s in expired] == ["s0.ts", "s1.ts", "s2.ts"]


def test_last_segment_is_never_deleted() -> None:
    segs = segments(3)
    now = T0 + timedelta(hours=5)
    assert [s.path.name for s in select_expired(segs, now, KEEP, [])] == ["s0.ts", "s1.ts"]


def test_lease_protects_overlapping_segments_with_margin() -> None:
    segs = segments(60)  # 2 minutes of segments
    now = T0 + timedelta(hours=1)
    protected = lease(T0 + timedelta(seconds=60), T0 + timedelta(seconds=70))
    expired = {s.path.name for s in select_expired(segs, now, KEEP, [protected])}
    # 60-70 s window with a 10 s margin => protects segments overlapping [50, 80);
    # s24 = [48, 50) and s40 = [80, 82) only touch the window
    kept = {s.path.name for s in segs} - expired
    assert kept == {f"s{i}.ts" for i in range(25, 40)} | {"s59.ts"}


def test_cleanup_court_deletes_files_and_respects_leases(tmp_path: Path) -> None:
    directory = tmp_path / "court1"
    directory.mkdir()
    for i in range(10):
        start = T0 + timedelta(seconds=i * 2)
        (directory / f"court1_{start:%Y%m%d_%H%M%S}.ts").write_bytes(b"x")
    write_lease(tmp_path, "court1", "job-1", T0, T0 + timedelta(seconds=1))

    now = T0 + KEEP + timedelta(minutes=1)
    removed = cleanup_court(tmp_path, "court1", now, KEEP)

    remaining = [s.start_at for s in list_segments(tmp_path, "court1")]
    # The 10 s margin protects up to 15:00:11; the rest goes, except the last one
    assert remaining == [T0 + timedelta(seconds=i * 2) for i in (0, 1, 2, 3, 4, 5, 9)]
    assert removed == 3
