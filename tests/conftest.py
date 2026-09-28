"""Fixtures compartilhadas. Os testes de banco usam o TEST_DATABASE_URL do .env."""

from __future__ import annotations

from collections.abc import Iterator
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
        pytest.skip("TEST_DATABASE_URL não definido")
    engine = create_engine(url)
    try:
        with engine.connect():
            pass
    except Exception as exc:  # noqa: BLE001 - qualquer falha de conexão vira skip
        pytest.skip(f"banco de testes indisponível ({exc.__class__.__name__})")
    finally:
        engine.dispose()
    return url


@pytest.fixture(scope="session")
def migrated_db(test_database_url: str) -> str:
    """Recria o schema do zero com as migrations (também testa o downgrade)."""
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
