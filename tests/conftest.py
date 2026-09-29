"""Shared fixtures. Database tests use TEST_DATABASE_URL from .env."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text

from replay.db.repository import Repository

ROOT = Path(__file__).resolve().parent.parent


class _TestEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    test_database_url: str | None = None


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = _TestEnv().test_database_url
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_engine(url)
    try:
        with engine.connect():
            pass
    except Exception as exc:  # noqa: BLE001 - any connection failure becomes a skip
        pytest.skip(f"test database unavailable ({exc.__class__.__name__})")
    finally:
        engine.dispose()
    return url


@pytest.fixture(scope="session")
def migrated_db(test_database_url: str) -> str:
    """Rebuilds the schema from scratch with migrations (also exercises downgrade)."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", test_database_url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return test_database_url


@pytest.fixture
def repo(migrated_db: str) -> Iterator[Repository]:
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE clips, courts"))
    engine.dispose()
    repository = Repository.from_url(migrated_db)
    yield repository
    repository.dispose()


def make_segment_files(
    segments_dir: Path, court_id: str, start: datetime, count: int, segment_s: int = 2
) -> list[Path]:
    """Generates `count` real .ts segments (video + AAC audio, keyframe every segment)
    named like the capture service would, starting at `start` (UTC)."""
    court_dir = segments_dir / court_id
    court_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = court_dir / ".raw"
    raw_dir.mkdir(exist_ok=True)
    fps = 15
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate={fps}",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", str(count * segment_s),
            "-c:v", "libx264", "-preset", "ultrafast", "-g", str(segment_s * fps),
            "-c:a", "aac",
            "-f", "segment", "-segment_time", str(segment_s), "-reset_timestamps", "1",
            str(raw_dir / "%03d.ts"),
        ],
        check=True,
    )  # fmt: skip
    paths = []
    for i, raw in enumerate(sorted(raw_dir.glob("*.ts"))):
        name = start + timedelta(seconds=i * segment_s)
        target = court_dir / f"{court_id}_{name:%Y%m%d_%H%M%S}.ts"
        raw.replace(target)
        paths.append(target)
    raw_dir.rmdir()
    return paths


requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)
