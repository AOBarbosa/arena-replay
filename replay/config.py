"""Carrega e valida a configuração: parâmetros em YAML e segredos no .env."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_PATH = Path("config.yaml")
CONFIG_PATH_ENV = "REPLAY_CONFIG"


class ConfigError(Exception):
    """Configuração ausente ou inválida."""


class Encoder(StrEnum):
    COPY = "copy"
    LIBX264 = "libx264"
    H264_VAAPI = "h264_vaapi"
    H264_VIDEOTOOLBOX = "h264_videotoolbox"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CourtConfig(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1)
    stream_url: str = Field(min_length=1)
    trigger_key: str = Field(min_length=1)


class CaptureConfig(_Strict):
    segment_s: float = Field(default=2, gt=0)
    buffer_minutes: float = Field(default=10, gt=0)
    latency_offset_s: float = Field(default=1.0, ge=0)
    audio: bool = True
    rtsp_transport: str = "tcp"
    reconnect_min_s: float = Field(default=1, gt=0)
    reconnect_max_s: float = Field(default=30, gt=0)
    stall_timeout_s: float = Field(default=15, gt=0)

    @model_validator(mode="after")
    def _check_backoff(self) -> Self:
        if self.reconnect_max_s < self.reconnect_min_s:
            raise ValueError("reconnect_max_s deve ser >= reconnect_min_s")
        return self


class ClipConfig(_Strict):
    duration_s: float = Field(default=30, gt=0)
    post_roll_s: float = Field(default=3, ge=0)
    encoder: Encoder = Encoder.COPY
    vaapi_device: str = "/dev/dri/renderD128"


class TriggerConfig(_Strict):
    debounce_s: float = Field(default=5, ge=0)


class PathsConfig(_Strict):
    segments_dir: Path = Path("data/segments")
    clips_dir: Path = Path("data/clips")


class ApiConfig(_Strict):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=list)


class AppConfig(_Strict):
    timezone: str = "America/Fortaleza"
    courts: list[CourtConfig] = Field(min_length=1)
    capture: CaptureConfig = CaptureConfig()
    clip: ClipConfig = ClipConfig()
    trigger: TriggerConfig = TriggerConfig()
    paths: PathsConfig = PathsConfig()
    api: ApiConfig = ApiConfig()

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"fuso horário desconhecido: {value}") from exc
        return value

    @model_validator(mode="after")
    def _check_courts(self) -> Self:
        ids = [c.id for c in self.courts]
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        if duplicated:
            raise ValueError(f"ids de quadra repetidos: {', '.join(duplicated)}")
        keys = [c.trigger_key for c in self.courts]
        duplicated = sorted({k for k in keys if keys.count(k) > 1})
        if duplicated:
            raise ValueError(f"teclas de gatilho repetidas: {', '.join(duplicated)}")
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def court(self, court_id: str) -> CourtConfig:
        for court in self.courts:
            if court.id == court_id:
                return court
        raise KeyError(court_id)


class EnvSettings(BaseSettings):
    """Segredos vindos do ambiente ou do arquivo .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(min_length=1)


class Settings(BaseModel):
    """Tudo o que um serviço precisa para iniciar."""

    model_config = ConfigDict(frozen=True)

    app: AppConfig
    env: EnvSettings


def load_app_config(path: Path | None = None) -> AppConfig:
    """Lê o YAML, valida e resolve caminhos relativos à pasta do arquivo."""
    path = path or Path(os.environ.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH))
    if not path.is_file():
        raise ConfigError(
            f"arquivo de configuração não encontrado: {path} "
            "(copie config.example.yaml para config.yaml)"
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        config = AppConfig.model_validate(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML inválido em {path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"configuração inválida em {path}:\n{exc}") from exc
    return _resolve_paths(config, path.resolve().parent)


def load_env_settings(env_file: Path | None = None) -> EnvSettings:
    try:
        if env_file is None:
            return EnvSettings()
        return EnvSettings(_env_file=env_file)
    except ValidationError as exc:
        raise ConfigError(f"variáveis de ambiente inválidas (confira o .env):\n{exc}") from exc


def load_settings(config_path: Path | None = None, env_file: Path | None = None) -> Settings:
    return Settings(app=load_app_config(config_path), env=load_env_settings(env_file))


def _resolve_paths(config: AppConfig, base_dir: Path) -> AppConfig:
    def resolve(p: Path) -> Path:
        return p if p.is_absolute() else (base_dir / p).resolve()

    paths = PathsConfig(
        segments_dir=resolve(config.paths.segments_dir),
        clips_dir=resolve(config.paths.clips_dir),
    )
    return config.model_copy(update={"paths": paths})
