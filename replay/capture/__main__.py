"""Capture service: `python -m replay.capture`."""

from __future__ import annotations

import logging
import shutil
import signal
import sys
import threading
from datetime import timedelta
from types import FrameType

from replay.capture.cleanup import CleanupLoop
from replay.capture.supervisor import CaptureSupervisor, build_capture_command
from replay.config import ConfigError, load_app_config
from replay.logs import setup_logging

log = logging.getLogger("replay.capture")


def main() -> int:
    setup_logging("capture")
    try:
        config = load_app_config()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    if shutil.which("ffmpeg") is None:
        log.error("ffmpeg not found in PATH")
        return 2

    segments_dir = config.paths.segments_dir
    supervisors = [
        CaptureSupervisor(
            court.id,
            build_capture_command(court, config.capture, segments_dir),
            segments_dir,
            config.capture,
        )
        for court in config.courts
    ]
    cleanup = CleanupLoop(
        segments_dir,
        [court.id for court in config.courts],
        keep=timedelta(minutes=config.capture.buffer_minutes),
    )

    stop = threading.Event()

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("received %s; shutting down", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    log.info(
        "capturing %d court(s) into %s (%g min buffer)",
        len(supervisors),
        segments_dir,
        config.capture.buffer_minutes,
    )
    for thread in (*supervisors, cleanup):
        thread.start()

    while not stop.wait(1):
        pass

    for thread in (*supervisors, cleanup):
        thread.stop()
    for thread in (*supervisors, cleanup):
        thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
