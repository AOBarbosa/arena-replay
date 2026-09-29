from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from replay.buffer import (
    latest_segment_name,
    list_segments,
    parse_segment_start,
    read_active_leases,
    remove_lease,
    write_lease,
)

T0 = datetime(2026, 9, 28, 15, 30, 0, tzinfo=UTC)


def touch_segments(root: Path, court_id: str, *starts: datetime) -> None:
    directory = root / court_id
    directory.mkdir(parents=True, exist_ok=True)
    for start in starts:
        (directory / f"{court_id}_{start:%Y%m%d_%H%M%S}.ts").write_bytes(b"x")


def test_parse_segment_start() -> None:
    assert parse_segment_start("court1_20260928_153012.ts", "court1") == datetime(
        2026, 9, 28, 15, 30, 12, tzinfo=UTC
    )
    assert parse_segment_start("court_a_20260928_153012.ts", "court_a") is not None
    assert parse_segment_start("court2_20260928_153012.ts", "court1") is None
    assert parse_segment_start("court1_20260928_153012.mp4", "court1") is None
    assert parse_segment_start("court1_20261399_153012.ts", "court1") is None


def test_list_segments_sorted_with_ends(tmp_path: Path) -> None:
    t1, t2, t3 = T0, T0 + timedelta(seconds=2), T0 + timedelta(seconds=5)
    touch_segments(tmp_path, "court1", t3, t1, t2)
    court_dir = tmp_path / "court1"
    (court_dir / "notes.txt").write_text("x")
    (court_dir / ".leases").mkdir()
    # A court with a similar prefix must not be mixed in
    (court_dir / "court10_20260928_153000.ts").write_bytes(b"x")

    segments = list_segments(tmp_path, "court1")
    assert [(s.start_at, s.end_at) for s in segments] == [(t1, t2), (t2, t3), (t3, None)]
    assert latest_segment_name(tmp_path, "court1") == f"court1_{t3:%Y%m%d_%H%M%S}.ts"


def test_list_segments_missing_dir(tmp_path: Path) -> None:
    assert list_segments(tmp_path, "court1") == []
    assert latest_segment_name(tmp_path, "court1") is None


def test_lease_roundtrip(tmp_path: Path) -> None:
    lease = write_lease(tmp_path, "court1", "job-1", T0, T0 + timedelta(seconds=33))
    active = read_active_leases(tmp_path, "court1", datetime.now(UTC), timedelta(minutes=10))
    assert [(le.job_id, le.start_at, le.end_at) for le in active] == [
        ("job-1", T0, T0 + timedelta(seconds=33))
    ]
    remove_lease(lease)
    assert read_active_leases(tmp_path, "court1", datetime.now(UTC), timedelta(minutes=10)) == []


def test_stale_and_broken_leases(tmp_path: Path) -> None:
    stale = write_lease(tmp_path, "court1", "old", T0, T0)
    old = time.time() - 3600
    os.utime(stale.path, (old, old))
    (stale.path.parent / "broken.json").write_text("{not json")

    active = read_active_leases(tmp_path, "court1", datetime.now(UTC), timedelta(minutes=10))
    assert active == []
    assert not stale.path.exists()
