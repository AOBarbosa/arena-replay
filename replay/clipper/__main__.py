"""Trigger/clipper service: `python -m replay.clipper [--stdin]`."""

from __future__ import annotations

import argparse
import logging
import shutil
import signal
import sys
import threading
from types import FrameType

from replay.clipper.worker import ClipWorker
from replay.config import ConfigError, load_settings
from replay.db.repository import Repository
from replay.logs import setup_logging
from replay.models import Court
from replay.storage.local import LocalClipStorage
from replay.triggers.base import Trigger, TriggerError
from replay.triggers.keyboard import KeyboardTrigger
from replay.triggers.stdin import StdinTrigger

log = logging.getLogger("replay.clipper")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m replay.clipper")
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="trigger from the terminal (Enter or a court id) instead of the global keyboard",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("clipper")
    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            log.error("%s not found in PATH", tool)
            return 2

    config = settings.app
    repo = Repository.from_url(settings.env.database_url)
    try:
        repo.sync_courts(Court(c.id, c.name) for c in config.courts)
    except Exception as exc:  # noqa: BLE001 - any DB failure is fatal at startup
        log.error("database unavailable (%s); is `docker compose up -d db` running?", exc)
        return 1

    worker = ClipWorker(config, repo, LocalClipStorage(config.paths.clips_dir))
    trigger: Trigger
    if args.stdin:
        trigger = StdinTrigger([c.id for c in config.courts], config.trigger.debounce_s)
    else:
        trigger = KeyboardTrigger(
            {c.trigger_key: c.id for c in config.courts}, config.trigger.debounce_s
        )

    stop = threading.Event()

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("received %s; shutting down", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    worker.start()
    try:
        trigger.start(worker.submit)
    except TriggerError as exc:
        log.error("%s", exc)
        worker.stop()
        return 2
    log.info(
        "clipper ready: %g s before + %g s after the trigger, encoder=%s",
        config.clip.duration_s,
        config.clip.post_roll_s,
        config.clip.encoder.value,
    )

    while not stop.wait(1):
        pass

    trigger.stop()
    worker.stop()
    repo.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
