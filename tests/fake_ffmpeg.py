"""ffmpeg stand-in for the supervisor tests.

Usage: python fake_ffmpeg.py <mode> <dir> <court_id>
  exit     exits immediately with an error (camera offline)
  hang     keeps running without recording anything (stalled stream)
  produce  creates a new segment every 0.1 s
"""

import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

mode, directory, court_id = sys.argv[1], Path(sys.argv[2]), sys.argv[3]

if mode == "exit":
    sys.exit(1)
if mode == "hang":
    while True:
        time.sleep(1)
if mode == "produce":
    base = datetime(2026, 1, 1, tzinfo=UTC)
    while True:
        # One fake "second" per file, unique even across restarts
        name = base + timedelta(seconds=time.time_ns() // 100_000_000)
        (directory / f"{court_id}_{name:%Y%m%d_%H%M%S}.ts").write_bytes(b"")
        time.sleep(0.1)
sys.exit(f"unknown mode: {mode}")
