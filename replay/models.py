"""Domain dataclasses shared across services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID


class ClipStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Court:
    id: str
    name: str
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Clip:
    id: UUID
    court_id: str
    triggered_at: datetime
    start_at: datetime
    end_at: datetime
    status: ClipStatus
    duration_s: float | None = None
    file_key: str | None = None
    thumb_key: str | None = None
    error: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Segment:
    """A .ts file in the buffer. `end_at` is None while the end is still unknown."""

    court_id: str
    path: Path
    start_at: datetime
    end_at: datetime | None = None
