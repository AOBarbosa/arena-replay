"""Clip job queue. `submit()` never blocks: the trigger only enqueues the job."""

from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from replay.buffer import Lease, list_segments, remove_lease, write_lease
from replay.clipper.builder import BuildError, build_clip, probe_duration
from replay.clipper.segment_index import (
    WindowSelection,
    candidate_segments,
    resolve_ends,
    select_window,
)
from replay.config import AppConfig
from replay.db.repository import Repository
from replay.storage.base import ClipStorage

log = logging.getLogger(__name__)

ORPHAN_REASON = "the clipper stopped before this clip was finished"
WORK_DIRNAME = ".work"
# How long to wait past the window end for the stream to close the last segment
MIN_CLOSE_WAIT = timedelta(seconds=10)


class ClipError(Exception):
    """The clip cannot be produced (e.g. no segments in the window)."""


@dataclass(frozen=True, slots=True)
class ClipJob:
    clip_id: UUID
    court_id: str
    triggered_at: datetime  # real time (UTC)
    # Window in stream time (segment-name clock = real time + latency offset)
    window_start: datetime
    window_end: datetime
    trigger_stream_at: datetime
    lease: Lease


class ClipWorker:
    def __init__(
        self,
        config: AppConfig,
        repo: Repository,
        storage: ClipStorage,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        probe: Callable[[Path], float | None] = probe_duration,
        poll_interval_s: float = 0.5,
    ) -> None:
        self._config = config
        self._repo = repo
        self._storage = storage
        self._now = now
        self._sleep = sleep
        self._probe = probe
        self._poll_interval_s = poll_interval_s
        self._offset = timedelta(seconds=config.capture.latency_offset_s)
        self._queue: queue.Queue[ClipJob] = queue.Queue()
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="clip-worker", daemon=True)
        # Counters for the heartbeat (written only by the worker thread)
        self._current: ClipJob | None = None
        self._ready_count = 0
        self._failed_count = 0
        self._last_ready_at: datetime | None = None
        self._last_error: str | None = None
        self._last_error_at: datetime | None = None

    def status(self) -> dict[str, Any]:
        current = self._current
        return {
            "queue_size": self._queue.qsize(),
            "processing": str(current.clip_id) if current else None,
            "processing_court": current.court_id if current else None,
            "ready_count": self._ready_count,
            "failed_count": self._failed_count,
            "last_ready_at": self._last_ready_at,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
        }

    # --- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        orphans = self._repo.fail_orphan_clips(ORPHAN_REASON)
        if orphans:
            log.warning("marked %d unfinished clip(s) from a previous run as failed", orphans)
        self._thread.start()

    def stop(self, timeout_s: float = 60) -> None:
        """Finishes the current job; jobs still in the queue are dropped."""
        self._stopping.set()
        self._thread.join(timeout_s)
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                break
            remove_lease(job.lease)
            log.warning("[%s] dropped queued clip %s on shutdown", job.court_id, job.clip_id)

    # --- producer side (trigger thread) ----------------------------------------------

    def submit(self, court_id: str, triggered_at: datetime) -> UUID:
        """Enqueues a clip and protects its segments with a lease. Returns the clip id."""
        job = self.make_job(court_id, triggered_at)
        self._queue.put(job)
        log.info("[%s] clip %s queued (%d waiting)", court_id, job.clip_id, self._queue.qsize())
        return job.clip_id

    def make_job(self, court_id: str, triggered_at: datetime) -> ClipJob:
        clip = self._config.clip
        clip_id = uuid4()
        trigger_stream_at = triggered_at + self._offset
        window_start = trigger_stream_at - timedelta(seconds=clip.duration_s)
        window_end = trigger_stream_at + timedelta(seconds=clip.post_roll_s)
        lease = write_lease(
            self._config.paths.segments_dir, court_id, str(clip_id), window_start, window_end
        )
        return ClipJob(
            clip_id, court_id, triggered_at, window_start, window_end, trigger_stream_at, lease
        )

    # --- consumer side (worker thread) -------------------------------------------

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._current = job
            try:
                self.process(job)
            finally:
                self._current = None

    def process(self, job: ClipJob) -> None:
        """Produces one clip end to end. Never raises: failures are stored on the clip."""
        work_dir = self._config.paths.clips_dir / WORK_DIRNAME / str(job.clip_id)
        try:
            self._repo.create_clip(
                job.court_id,
                job.triggered_at,
                job.window_start - self._offset,
                job.window_end - self._offset,
                clip_id=job.clip_id,
            )
        except Exception as exc:
            log.exception("[%s] could not register clip %s", job.court_id, job.clip_id)
            self._note_failure(job, f"could not register the clip in the database: {exc}")
            remove_lease(job.lease)
            return
        try:
            self._build_and_store(job, work_dir)
        except (ClipError, BuildError) as exc:
            log.error("[%s] clip %s failed: %s", job.court_id, job.clip_id, exc)
            self._mark_failed(job, str(exc))
        except Exception as exc:
            log.exception("[%s] clip %s failed unexpectedly", job.court_id, job.clip_id)
            self._mark_failed(job, f"unexpected error: {exc}")
        finally:
            remove_lease(job.lease)
            shutil.rmtree(work_dir, ignore_errors=True)

    def _build_and_store(self, job: ClipJob, work_dir: Path) -> None:
        selection = self._wait_and_select(job)
        if selection is None:
            raise ClipError("no segments recorded in the clip window (camera offline?)")
        if selection.gaps:
            log.warning(
                "[%s] clip %s has %.1f s missing from the buffer (%d gap(s)); "
                "building it with what exists",
                job.court_id,
                job.clip_id,
                selection.gap_seconds,
                len(selection.gaps),
            )
        clip_start = max(job.window_start, selection.start_at)
        thumbnail_at_s = (job.trigger_stream_at - clip_start).total_seconds() - 1
        built = build_clip(
            selection,
            job.window_start,
            job.window_end,
            work_dir,
            self._config.clip,
            thumbnail_at_s,
        )

        base_key = f"{job.court_id}/{job.triggered_at:%Y/%m/%d}/{job.clip_id}"
        file_key = f"{base_key}.mp4"
        self._storage.save(built.video, file_key)
        thumb_key: str | None = None
        if built.thumbnail is not None:
            thumb_key = f"{base_key}.jpg"
            self._storage.save(built.thumbnail, thumb_key)

        self._repo.mark_clip_ready(
            job.clip_id,
            start_at=built.start_at - self._offset,
            end_at=built.end_at - self._offset,
            duration_s=round(built.duration_s, 3),
            file_key=file_key,
            thumb_key=thumb_key,
        )
        self._ready_count += 1
        self._last_ready_at = datetime.now(UTC)
        log.info(
            "[%s] clip %s ready (%.1f s, %s)",
            job.court_id,
            job.clip_id,
            built.duration_s,
            self._config.clip.encoder.value,
        )

    def _wait_and_select(self, job: ClipJob) -> WindowSelection | None:
        """Waits until the segment covering the window end is closed (a newer
        segment exists) or a deadline passes, then selects the segments."""
        segments_dir = self._config.paths.segments_dir
        close_wait = max(MIN_CLOSE_WAIT, timedelta(seconds=3 * self._config.capture.segment_s))
        deadline = job.window_end + close_wait
        while True:
            segments = list_segments(segments_dir, job.court_id)
            if any(s.start_at >= job.window_end for s in segments):
                break
            if self._now() >= deadline:
                log.warning(
                    "[%s] stream did not reach the end of the clip window; "
                    "using the segments that exist",
                    job.court_id,
                )
                break
            self._sleep(self._poll_interval_s)
        candidates = candidate_segments(segments, job.window_start, job.window_end)
        return select_window(
            resolve_ends(candidates, self._probe), job.window_start, job.window_end
        )

    def _note_failure(self, job: ClipJob, error: str) -> None:
        self._failed_count += 1
        self._last_error = f"[{job.court_id}] {error}"
        self._last_error_at = datetime.now(UTC)

    def _mark_failed(self, job: ClipJob, error: str) -> None:
        self._note_failure(job, error)
        try:
            self._repo.mark_clip_failed(job.clip_id, error)
        except Exception:
            log.exception("[%s] could not mark clip %s as failed", job.court_id, job.clip_id)
