"""Supervises one ffmpeg process per court: starts, monitors and reconnects with backoff."""

from __future__ import annotations

import logging
import os
import random
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from replay.buffer import court_dir, latest_segment_name, segment_output_pattern
from replay.config import CaptureConfig, CourtConfig

log = logging.getLogger(__name__)


def build_capture_command(
    court: CourtConfig, capture: CaptureConfig, segments_dir: Path
) -> list[str]:
    """ffmpeg command that records the stream into .ts segments without re-encoding video."""
    # Socket read timeout in microseconds: if the camera goes silent, ffmpeg exits on its own
    timeout_us = str(int(capture.stall_timeout_s * 1_000_000))
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "warning"]
    if court.stream_url.startswith(("rtsp://", "rtsps://")):
        cmd += ["-rtsp_transport", capture.rtsp_transport, "-timeout", timeout_us]
    else:
        cmd += ["-rw_timeout", timeout_us]
    cmd += ["-i", court.stream_url, "-map", "0:v:0", "-c:v", "copy"]
    if capture.audio:
        # '?' makes audio optional. AAC because MPEG-TS does not accept μ-law (IP Webcam);
        # 48 kHz because μ-law comes at 8 kHz, which browsers handle poorly
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-ar", "48000", "-b:a", "64k"]
    else:
        cmd += ["-an"]
    cmd += [
        "-f", "segment",
        "-segment_time", f"{capture.segment_s:g}",
        "-segment_format", "mpegts",
        "-strftime", "1",
        "-reset_timestamps", "1",
        str(segment_output_pattern(segments_dir, court.id)),
    ]  # fmt: skip
    return cmd


def backoff_delay(
    failures: int, min_s: float, max_s: float, rng: Callable[[], float] = random.random
) -> float:
    """Exponential backoff with jitter (50% to 100% of the base value)."""
    base = min(max_s, min_s * 2 ** max(failures - 1, 0))
    return base * (0.5 + 0.5 * rng())


class CaptureSupervisor(threading.Thread):
    """Keeps a court's ffmpeg running until `stop()` is called."""

    def __init__(
        self,
        court_id: str,
        command: list[str],
        segments_dir: Path,
        capture: CaptureConfig,
        *,
        poll_interval_s: float = 1.0,
        stop_timeout_s: float = 5.0,
    ) -> None:
        super().__init__(name=f"capture-{court_id}", daemon=True)
        self.court_id = court_id
        self._command = command
        self._segments_dir = segments_dir
        self._capture = capture
        self._poll_interval_s = poll_interval_s
        self._stop_timeout_s = stop_timeout_s
        self._stop_event = threading.Event()
        self._env = {**os.environ, "TZ": "UTC"}  # segment names in UTC
        self.starts = 0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        court_dir(self._segments_dir, self.court_id).mkdir(parents=True, exist_ok=True)
        failures = 0
        while not self._stop_event.is_set():
            produced = self._run_once()
            if self._stop_event.is_set():
                break
            # If it recorded anything, the connection was good: restart backoff from the minimum
            failures = 1 if produced else failures + 1
            delay = backoff_delay(
                failures, self._capture.reconnect_min_s, self._capture.reconnect_max_s
            )
            log.warning(
                "[%s] reconnecting in %.1f s (consecutive failure %d)",
                self.court_id,
                delay,
                failures,
            )
            self._stop_event.wait(delay)
        log.info("[%s] capture stopped", self.court_id)

    def _run_once(self) -> bool:
        """Runs ffmpeg until it exits, stalls or a stop is requested.
        Returns True if any new segment was recorded."""
        log.info("[%s] starting ffmpeg", self.court_id)
        try:
            proc = subprocess.Popen(
                self._command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=self._env,
                text=True,
                errors="replace",
                start_new_session=True,  # Ctrl+C in the terminal does not reach ffmpeg directly
            )
        except OSError as exc:
            log.error("[%s] failed to start ffmpeg: %s", self.court_id, exc)
            return False
        self.starts += 1
        reader = threading.Thread(target=self._log_stderr, args=(proc,), daemon=True)
        reader.start()

        last_name = latest_segment_name(self._segments_dir, self.court_id)
        last_progress = time.monotonic()
        produced = False
        while True:
            if self._stop_event.wait(self._poll_interval_s):
                self._terminate(proc)
                break
            name = latest_segment_name(self._segments_dir, self.court_id)
            if name != last_name:
                last_name = name
                last_progress = time.monotonic()
                if not produced:
                    log.info("[%s] receiving segments", self.court_id)
                    produced = True
            code = proc.poll()
            if code is not None:
                log.warning("[%s] ffmpeg exited (code %s)", self.court_id, code)
                break
            stalled_for = time.monotonic() - last_progress
            if stalled_for > self._capture.stall_timeout_s:
                log.warning(
                    "[%s] no new segment for %.0f s; restarting ffmpeg",
                    self.court_id,
                    stalled_for,
                )
                self._terminate(proc)
                break
        reader.join(timeout=2)
        return produced

    def _terminate(self, proc: subprocess.Popen[str]) -> None:
        """SIGTERM lets ffmpeg close the current segment; SIGKILL if it does not respond."""
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=self._stop_timeout_s)
        except subprocess.TimeoutExpired:
            log.warning("[%s] ffmpeg ignored SIGTERM; killing it", self.court_id)
            proc.kill()
            proc.wait()

    def _log_stderr(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                log.warning("[%s] ffmpeg: %s", self.court_id, line)
