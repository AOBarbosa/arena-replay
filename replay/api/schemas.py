"""API response models: the contract consumed by the future Next.js app."""

from __future__ import annotations

from datetime import datetime
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
