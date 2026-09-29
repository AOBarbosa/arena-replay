from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from replay.buffer import court_dir, list_segments
from replay.capture.supervisor import CaptureSupervisor, backoff_delay, build_capture_command
from replay.config import CaptureConfig, CourtConfig

FAKE_FFMPEG = Path(__file__).with_name("fake_ffmpeg.py")


def court(url: str = "rtsp://192.168.0.50:8080/h264_ulaw.sdp") -> CourtConfig:
    return CourtConfig(id="court1", name="Court 1", stream_url=url, trigger_key="space")


# --- command building ---------------------------------------------------------


def test_command_rtsp_with_audio(tmp_path: Path) -> None:
    cmd = build_capture_command(court(), CaptureConfig(), tmp_path)
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-rtsp_transport") + 1] == "tcp"
    assert cmd[cmd.index("-timeout") + 1] == "15000000"
    assert cmd[cmd.index("-i") + 1] == "rtsp://192.168.0.50:8080/h264_ulaw.sdp"
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-ar") + 1] == "48000"
    assert "0:a:0?" in cmd
    assert cmd[cmd.index("-segment_time") + 1] == "2"
    assert cmd[cmd.index("-segment_format") + 1] == "mpegts"
    assert cmd[cmd.index("-strftime") + 1] == "1"
    assert cmd[cmd.index("-reset_timestamps") + 1] == "1"
    assert cmd[-1] == str(tmp_path / "court1" / "court1_%Y%m%d_%H%M%S.ts")


def test_command_without_audio(tmp_path: Path) -> None:
    cmd = build_capture_command(court(), CaptureConfig(audio=False, segment_s=2.5), tmp_path)
    assert "-an" in cmd
    assert "-c:a" not in cmd
    assert cmd[cmd.index("-segment_time") + 1] == "2.5"


def test_command_http_source_uses_rw_timeout(tmp_path: Path) -> None:
    cmd = build_capture_command(court("http://10.0.0.2/stream"), CaptureConfig(), tmp_path)
    assert "-rtsp_transport" not in cmd
    assert cmd[cmd.index("-rw_timeout") + 1] == "15000000"


# --- backoff -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("failures", "expected_base"), [(0, 1), (1, 1), (2, 2), (3, 4), (5, 16), (6, 30), (50, 30)]
)
def test_backoff_grows_and_caps(failures: int, expected_base: float) -> None:
    assert backoff_delay(failures, 1, 30, rng=lambda: 1.0) == expected_base
    assert backoff_delay(failures, 1, 30, rng=lambda: 0.0) == expected_base / 2


# --- supervisor with fake ffmpeg --------------------------------------------------

FAST = CaptureConfig(reconnect_min_s=0.05, reconnect_max_s=0.1, stall_timeout_s=0.5)


def make_supervisor(tmp_path: Path, mode: str) -> CaptureSupervisor:
    command = [sys.executable, str(FAKE_FFMPEG), mode, str(tmp_path / "court1"), "court1"]
    return CaptureSupervisor(
        "court1", command, tmp_path, FAST, poll_interval_s=0.05, stop_timeout_s=1
    )


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def stop_and_join(supervisor: CaptureSupervisor) -> None:
    supervisor.stop()
    supervisor.join(timeout=5)
    assert not supervisor.is_alive()


def test_restarts_when_ffmpeg_exits(tmp_path: Path) -> None:
    supervisor = make_supervisor(tmp_path, "exit")
    supervisor.start()
    try:
        assert wait_until(lambda: supervisor.starts >= 3)
        status = supervisor.status()
        assert status["restarts"] >= 2
        assert status["last_error"].startswith("ffmpeg exited (code 1)")
    finally:
        stop_and_join(supervisor)
    assert court_dir(tmp_path, "court1").is_dir()
    assert supervisor.status()["state"] == "stopped"


def test_restarts_when_stream_stalls(tmp_path: Path) -> None:
    supervisor = make_supervisor(tmp_path, "hang")
    supervisor.start()
    try:
        assert wait_until(lambda: supervisor.starts >= 2)
        assert "stalled" in supervisor.status()["last_error"]
    finally:
        stop_and_join(supervisor)


def test_healthy_stream_is_not_restarted(tmp_path: Path) -> None:
    supervisor = make_supervisor(tmp_path, "produce")
    supervisor.start()
    try:
        time.sleep(1.5)  # three times the stall timeout
        assert supervisor.starts == 1
        assert len(list_segments(tmp_path, "court1")) >= 5
        status = supervisor.status()
        assert status["state"] == "recording"
        assert status["restarts"] == 0
        assert status["last_segment_at"] is not None
        assert status["last_error"] is None
    finally:
        stop_and_join(supervisor)


def test_missing_binary_keeps_retrying_and_stops(tmp_path: Path) -> None:
    supervisor = CaptureSupervisor(
        "court1", ["/does/not/exist/ffmpeg"], tmp_path, FAST, poll_interval_s=0.05
    )
    supervisor.start()
    time.sleep(0.3)
    stop_and_join(supervisor)
    assert supervisor.starts == 0
    assert "failed to start ffmpeg" in supervisor.status()["last_error"]
