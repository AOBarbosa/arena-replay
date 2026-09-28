from __future__ import annotations

from pathlib import Path

import pytest

from replay.config import ConfigError, Encoder, load_app_config, load_env_settings

ROOT = Path(__file__).resolve().parent.parent


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


MINIMAL = """
courts:
  - {id: court1, name: Quadra 1, stream_url: rtsp://x/1, trigger_key: space}
"""


def test_example_config_is_valid() -> None:
    config = load_app_config(ROOT / "config.example.yaml")
    assert config.courts[0].id == "court1"
    assert config.clip.duration_s == 30
    assert config.clip.encoder is Encoder.COPY


def test_defaults_and_relative_paths(tmp_path: Path) -> None:
    config = load_app_config(write(tmp_path, MINIMAL))
    assert config.capture.segment_s == 2
    assert config.paths.segments_dir == (tmp_path / "data/segments").resolve()
    assert config.tz.key == "America/Fortaleza"


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="não encontrado"):
        load_app_config(tmp_path / "nada.yaml")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("courts: []", "courts"),
        (MINIMAL + "clip: {encoder: nvenc}", "encoder"),
        (MINIMAL + "timezone: Marte/Olympus", "fuso"),
        (MINIMAL + "capture: {segment_s: 0}", "segment_s"),
        (MINIMAL + "desconhecido: 1", "desconhecido"),
        (
            """
courts:
  - {id: court1, name: A, stream_url: rtsp://x/1, trigger_key: a}
  - {id: court1, name: B, stream_url: rtsp://x/2, trigger_key: b}
""",
            "repetidos",
        ),
        (
            """
courts:
  - {id: court1, name: A, stream_url: rtsp://x/1, trigger_key: a}
  - {id: court2, name: B, stream_url: rtsp://x/2, trigger_key: a}
""",
            "teclas",
        ),
        ("courts:\n  - {id: 'Quadra 1', name: A, stream_url: x, trigger_key: a}", "id"),
    ],
)
def test_invalid_configs(tmp_path: Path, content: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_app_config(write(tmp_path, content))


def test_env_settings_from_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql+psycopg://u:p@h/db\n", encoding="utf-8")
    assert load_env_settings(env).database_url.endswith("/db")


def test_env_settings_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigError, match="DATABASE_URL|database_url"):
        load_env_settings(tmp_path / "inexistente.env")
