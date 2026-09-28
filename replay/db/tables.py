"""Modelos ORM. Toda alteração aqui precisa de uma migration do Alembic."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from replay.models import ClipStatus


class Base(DeclarativeBase):
    pass


class CourtRow(Base):
    __tablename__ = "courts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClipRow(Base):
    __tablename__ = "clips"
    __table_args__ = (Index("ix_clips_court_id_triggered_at", "court_id", "triggered_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    court_id: Mapped[str] = mapped_column(ForeignKey("courts.id"))
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Janela planejada no gatilho; atualizada com a janela real quando o clipe fica pronto
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[float | None] = mapped_column(Float)
    file_key: Mapped[str | None] = mapped_column(Text)
    thumb_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ClipStatus] = mapped_column(
        Enum(
            ClipStatus,
            name="clip_status",
            values_callable=lambda enum: [member.value for member in enum],
        )
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
