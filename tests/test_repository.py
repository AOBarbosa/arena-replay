from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from replay.db.repository import Repository
from replay.models import ClipStatus, Court

T0 = datetime(2026, 9, 28, 15, 30, tzinfo=UTC)


def make_ready_clip(repo: Repository, court_id: str, triggered_at: datetime):
    clip = repo.create_clip(
        court_id, triggered_at, triggered_at - timedelta(seconds=30), triggered_at
    )
    repo.mark_clip_ready(
        clip.id,
        start_at=clip.start_at,
        end_at=clip.end_at,
        duration_s=30.0,
        file_key=f"{court_id}/{clip.id}.mp4",
        thumb_key=f"{court_id}/{clip.id}.jpg",
    )
    return clip


@pytest.fixture
def courts(repo: Repository) -> None:
    repo.sync_courts([Court("court1", "Court 1"), Court("court2", "Court 2")])


def test_sync_courts_is_idempotent_and_updates_name(repo: Repository) -> None:
    repo.sync_courts([Court("court1", "Court 1")])
    repo.sync_courts([Court("court1", "Center Court"), Court("court2", "Court 2")])
    courts = repo.list_courts()
    assert [(c.id, c.name) for c in courts] == [
        ("court1", "Center Court"),
        ("court2", "Court 2"),
    ]
    assert courts[0].created_at is not None


@pytest.mark.usefixtures("courts")
def test_clip_lifecycle(repo: Repository) -> None:
    clip = repo.create_clip("court1", T0, T0 - timedelta(seconds=30), T0 + timedelta(seconds=3))
    assert clip.status is ClipStatus.PROCESSING
    assert clip.created_at is not None

    repo.mark_clip_ready(
        clip.id,
        start_at=T0 - timedelta(seconds=31),
        end_at=T0 + timedelta(seconds=3),
        duration_s=34.0,
        file_key="court1/a.mp4",
        thumb_key="court1/a.jpg",
    )
    stored = repo.get_clip(clip.id)
    assert stored is not None
    assert stored.status is ClipStatus.READY
    assert stored.duration_s == 34.0
    assert stored.start_at == T0 - timedelta(seconds=31)
    assert stored.triggered_at == T0


@pytest.mark.usefixtures("courts")
def test_failed_and_orphans(repo: Repository) -> None:
    a = repo.create_clip("court1", T0, T0, T0)
    b = repo.create_clip("court1", T0, T0, T0)
    repo.mark_clip_failed(a.id, "no segments")
    assert repo.fail_orphan_clips("worker restarted") == 1
    assert repo.get_clip(a.id).error == "no segments"
    assert repo.get_clip(b.id).status is ClipStatus.FAILED
    assert repo.get_clip(b.id).error == "worker restarted"


def test_get_missing_clip(repo: Repository) -> None:
    assert repo.get_clip(uuid4()) is None


@pytest.mark.usefixtures("courts")
def test_naive_datetime_rejected(repo: Repository) -> None:
    with pytest.raises(ValueError, match="timezone"):
        repo.create_clip("court1", datetime(2026, 1, 1), T0, T0)


@pytest.mark.usefixtures("courts")
def test_list_ready_clips_filters_and_order(repo: Repository) -> None:
    c1 = make_ready_clip(repo, "court1", T0)
    c2 = make_ready_clip(repo, "court1", T0 + timedelta(minutes=1))
    make_ready_clip(repo, "court2", T0 + timedelta(minutes=2))
    repo.create_clip("court1", T0 + timedelta(minutes=3), T0, T0)  # processing: not listed

    all_clips = repo.list_ready_clips(limit=10)
    assert len(all_clips) == 3
    assert all_clips[0].court_id == "court2"

    court1 = repo.list_ready_clips(limit=10, court_id="court1")
    assert [c.id for c in court1] == [c2.id, c1.id]

    window = repo.list_ready_clips(
        limit=10, since=T0 + timedelta(seconds=30), until=T0 + timedelta(minutes=2)
    )
    assert [c.id for c in window] == [c2.id]


@pytest.mark.usefixtures("courts")
def test_cursor_pagination_with_equal_timestamps(repo: Repository) -> None:
    ids = {make_ready_clip(repo, "court1", T0).id for _ in range(3)}
    ids |= {make_ready_clip(repo, "court1", T0 + timedelta(seconds=1)).id for _ in range(2)}

    seen = []
    cursor = None
    while True:
        page = repo.list_ready_clips(limit=2, cursor=cursor)
        if not page:
            break
        seen.extend(c.id for c in page)
        cursor = (page[-1].triggered_at, page[-1].id)

    assert len(seen) == 5
    assert set(seen) == ids
