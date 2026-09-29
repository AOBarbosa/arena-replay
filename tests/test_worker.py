"""Clipper worker end to end: real ffmpeg segments, test database, local storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from replay.buffer import leases_dir
from replay.clipper.worker import ClipWorker
from replay.config import AppConfig, CaptureConfig, ClipConfig, CourtConfig, PathsConfig
from replay.db.repository import Repository
from replay.models import ClipStatus, Court
from replay.storage.local import LocalClipStorage
from tests.conftest import make_segment_files, requires_ffmpeg

T0 = datetime(2026, 9, 28, 15, 0, 0, tzinfo=UTC)


def make_config(tmp_path: Path, latency_offset_s: float = 0) -> AppConfig:
    return AppConfig(
        courts=[CourtConfig(id="court1", name="Court 1", stream_url="x", trigger_key="space")],
        capture=CaptureConfig(latency_offset_s=latency_offset_s),
        clip=ClipConfig(duration_s=20, post_roll_s=3),
        paths=PathsConfig(segments_dir=tmp_path / "segments", clips_dir=tmp_path / "clips"),
    )


def make_worker(config: AppConfig, repo: Repository, now: datetime) -> ClipWorker:
    def no_sleep(_s: float) -> None:
        raise AssertionError("the window is already closed; the worker must not wait")

    storage = LocalClipStorage(config.paths.clips_dir)
    return ClipWorker(config, repo, storage, now=lambda: now, sleep=no_sleep)


def test_job_window_applies_latency_offset(tmp_path: Path) -> None:
    config = make_config(tmp_path, latency_offset_s=1.5)
    worker = ClipWorker(config, repo=None, storage=None)  # type: ignore[arg-type]
    job = worker.make_job("court1", T0)
    assert job.trigger_stream_at == T0 + timedelta(seconds=1.5)
    assert job.window_start == T0 - timedelta(seconds=18.5)
    assert job.window_end == T0 + timedelta(seconds=4.5)
    assert job.lease.path.is_file()


@requires_ffmpeg
def test_process_builds_and_registers_clip(tmp_path: Path, repo: Repository) -> None:
    config = make_config(tmp_path)
    repo.sync_courts([Court("court1", "Court 1")])
    make_segment_files(config.paths.segments_dir, "court1", T0, count=20)  # 0..40 s
    worker = make_worker(config, repo, now=T0 + timedelta(minutes=5))

    job = worker.make_job("court1", T0 + timedelta(seconds=30))
    worker.process(job)

    clip = repo.get_clip(job.clip_id)
    assert clip is not None
    assert clip.status is ClipStatus.READY, clip.error
    assert clip.duration_s == pytest.approx(23, abs=2.1)
    assert clip.file_key == f"court1/2026/09/28/{job.clip_id}.mp4"
    assert clip.thumb_key == f"court1/2026/09/28/{job.clip_id}.jpg"
    storage = LocalClipStorage(config.paths.clips_dir)
    assert storage.local_path(clip.file_key).is_file()  # type: ignore[union-attr]
    assert storage.local_path(clip.thumb_key).is_file()  # type: ignore[arg-type,union-attr]
    assert list(leases_dir(config.paths.segments_dir, "court1").iterdir()) == []
    assert not (config.paths.clips_dir / ".work" / str(job.clip_id)).exists()


@requires_ffmpeg
def test_process_without_segments_marks_failed(tmp_path: Path, repo: Repository) -> None:
    config = make_config(tmp_path)
    repo.sync_courts([Court("court1", "Court 1")])
    make_segment_files(config.paths.segments_dir, "court1", T0, count=5)
    worker = make_worker(config, repo, now=T0 + timedelta(hours=2))

    job = worker.make_job("court1", T0 + timedelta(hours=1))
    worker.process(job)

    clip = repo.get_clip(job.clip_id)
    assert clip is not None
    assert clip.status is ClipStatus.FAILED
    assert "no segments" in (clip.error or "")
    assert not job.lease.path.exists()


def test_start_fails_orphans(tmp_path: Path, repo: Repository) -> None:
    repo.sync_courts([Court("court1", "Court 1")])
    stuck = repo.create_clip("court1", T0, T0, T0)
    worker = ClipWorker(make_config(tmp_path), repo, LocalClipStorage(tmp_path / "clips"))
    worker.start()
    worker.stop(timeout_s=2)
    clip = repo.get_clip(stuck.id)
    assert clip is not None and clip.status is ClipStatus.FAILED
