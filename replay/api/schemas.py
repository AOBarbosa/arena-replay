"""API response models: the contract consumed by the future Next.js app."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from replay.models import ClipStatus


class CourtOut(BaseModel):
    id: str = Field(examples=["court1"])
    name: str = Field(examples=["Court 1"])


class ClipOut(BaseModel):
    id: UUID
    court_id: str = Field(examples=["court1"])
    status: ClipStatus
    triggered_at: datetime = Field(description="When the trigger fired (UTC)")
    start_at: datetime = Field(description="Real time of the first frame (UTC)")
    end_at: datetime = Field(description="Real time of the last frame (UTC)")
    duration_s: float | None = Field(default=None, examples=[33.0])
    video_url: str | None = Field(
        default=None, description="Streamable MP4 (supports HTTP Range); null until ready"
    )
    thumbnail_url: str | None = Field(default=None, description="JPEG thumbnail, if any")
    download_url: str | None = Field(
        default=None, description="Same MP4 with Content-Disposition: attachment"
    )


class ClipPage(BaseModel):
    items: list[ClipOut]
    next_cursor: str | None = Field(
        default=None, description="Pass as ?cursor= to get the next (older) page; null at the end"
    )


class HealthOut(BaseModel):
    status: str = Field(examples=["ok"])
    database: bool


# --- system status -----------------------------------------------------------------

Level = Literal["ok", "warning", "error"]
ServiceState = Literal["running", "stopped", "down", "unknown"]
CaptureState = Literal[
    "connecting", "recording", "reconnecting", "starting", "stopped", "down", "unknown"
]


class ServiceStatusOut(BaseModel):
    name: str = Field(examples=["capture"])
    state: ServiceState = Field(
        description="running; stopped (clean shutdown); down (heartbeat lost: crash or "
        "killed); unknown (never ran here)"
    )
    pid: int | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = Field(default=None, description="Last heartbeat")


class CourtStatusOut(BaseModel):
    court_id: str
    name: str
    capture: CaptureState
    since: datetime | None = Field(default=None, description="When `capture` last changed")
    last_segment_at: datetime | None = None
    segment_age_s: float | None = Field(
        default=None, description="Seconds since the newest segment started"
    )
    restarts: int = 0
    last_error: str | None = None
    last_error_at: datetime | None = None


class ClipperStatusOut(BaseModel):
    trigger: str | None = Field(default=None, examples=["keyboard"])
    queue_size: int = 0
    processing: UUID | None = None
    processing_court: str | None = None
    ready_count: int = Field(default=0, description="Clips ready since the clipper started")
    failed_count: int = Field(default=0, description="Clips failed since the clipper started")
    last_ready_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None


class SystemStatusOut(BaseModel):
    level: Level = Field(
        description="error: a service or the database is down; warning: a court is not "
        "recording or a clip failed recently; ok otherwise"
    )
    problems: list[str] = Field(description="Human-readable reasons for the level")
    checked_at: datetime
    database: bool
    services: list[ServiceStatusOut]
    courts: list[CourtStatusOut]
    clipper: ClipperStatusOut | None = Field(
        default=None, description="Null when the clipper is not running"
    )
