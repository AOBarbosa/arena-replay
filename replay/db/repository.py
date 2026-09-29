"""The only layer that runs queries. The rest of the code uses only this class."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from replay.db.engine import make_engine, make_session_factory
from replay.db.tables import ClipRow, CourtRow
from replay.models import Clip, ClipStatus, Court

log = logging.getLogger(__name__)

# Listing position: (triggered_at, id) of the last clip on the previous page
Cursor = tuple[datetime, UUID]


class Repository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    @classmethod
    def from_url(cls, database_url: str) -> Repository:
        return cls(make_session_factory(make_engine(database_url)))

    def dispose(self) -> None:
        bind = self._sessions.kw.get("bind")
        if bind is not None:
            bind.dispose()

    # --- courts ------------------------------------------------------------

    def sync_courts(self, courts: Iterable[Court]) -> None:
        """Inserts or updates the configured courts. Never deletes courts."""
        rows = [{"id": c.id, "name": c.name} for c in courts]
        if not rows:
            return
        stmt = insert(CourtRow).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[CourtRow.id], set_={"name": stmt.excluded.name}
        )
        with self._sessions.begin() as session:
            session.execute(stmt)

    def list_courts(self) -> list[Court]:
        with self._sessions() as session:
            rows = session.scalars(select(CourtRow).order_by(CourtRow.id))
            return [_court(r) for r in rows]

    # --- clips -------------------------------------------------------------

    def create_clip(
        self,
        court_id: str,
        triggered_at: datetime,
        start_at: datetime,
        end_at: datetime,
        clip_id: UUID | None = None,
    ) -> Clip:
        for value in (triggered_at, start_at, end_at):
            _require_aware(value)
        row = ClipRow(
            id=clip_id or uuid4(),
            court_id=court_id,
            triggered_at=triggered_at,
            start_at=start_at,
            end_at=end_at,
            status=ClipStatus.PROCESSING,
        )
        with self._sessions.begin() as session:
            session.add(row)
            session.flush()
            session.refresh(row)
            return _clip(row)

    def mark_clip_ready(
        self,
        clip_id: UUID,
        *,
        start_at: datetime,
        end_at: datetime,
        duration_s: float,
        file_key: str,
        thumb_key: str | None,
    ) -> None:
        _require_aware(start_at)
        _require_aware(end_at)
        self._update_clip(
            clip_id,
            status=ClipStatus.READY,
            start_at=start_at,
            end_at=end_at,
            duration_s=duration_s,
            file_key=file_key,
            thumb_key=thumb_key,
            error=None,
        )

    def mark_clip_failed(self, clip_id: UUID, error: str) -> None:
        self._update_clip(clip_id, status=ClipStatus.FAILED, error=error)

    def fail_orphan_clips(self, reason: str) -> int:
        """Marks clips stuck in `processing` as `failed` (e.g. the worker died)."""
        stmt = (
            update(ClipRow)
            .where(ClipRow.status == ClipStatus.PROCESSING)
            .values(status=ClipStatus.FAILED, error=reason)
        )
        with self._sessions.begin() as session:
            return session.execute(stmt).rowcount

    def get_clip(self, clip_id: UUID) -> Clip | None:
        with self._sessions() as session:
            row = session.get(ClipRow, clip_id)
            return _clip(row) if row else None

    def list_ready_clips(
        self,
        *,
        limit: int,
        court_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        cursor: Cursor | None = None,
    ) -> list[Clip]:
        """Ready clips, newest first. `since` is inclusive, `until` is exclusive."""
        stmt = select(ClipRow).where(ClipRow.status == ClipStatus.READY)
        if court_id is not None:
            stmt = stmt.where(ClipRow.court_id == court_id)
        if since is not None:
            stmt = stmt.where(ClipRow.triggered_at >= _require_aware(since))
        if until is not None:
            stmt = stmt.where(ClipRow.triggered_at < _require_aware(until))
        if cursor is not None:
            cursor_at, cursor_id = cursor
            stmt = stmt.where(
                tuple_(ClipRow.triggered_at, ClipRow.id)
                < tuple_(_require_aware(cursor_at), cursor_id)
            )
        stmt = stmt.order_by(ClipRow.triggered_at.desc(), ClipRow.id.desc()).limit(
            limit
        )
        with self._sessions() as session:
            return [_clip(r) for r in session.scalars(stmt)]

    def list_recent_clips(self, *, limit: int) -> list[Clip]:
        """Latest clips in any status, newest first (development tools)."""
        stmt = (
            select(ClipRow)
            .order_by(ClipRow.triggered_at.desc(), ClipRow.id.desc())
            .limit(limit)
        )
        with self._sessions() as session:
            return [_clip(r) for r in session.scalars(stmt)]

    def _update_clip(self, clip_id: UUID, **values: object) -> None:
        stmt = update(ClipRow).where(ClipRow.id == clip_id).values(**values)
        with self._sessions.begin() as session:
            if session.execute(stmt).rowcount == 0:
                log.warning(
                    "clip %s not found when updating to %s", clip_id, values
                )


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"naive datetime (no timezone): {value!r}")
    return value


def _court(row: CourtRow) -> Court:
    return Court(id=row.id, name=row.name, created_at=row.created_at)


def _clip(row: ClipRow) -> Clip:
    return Clip(
        id=row.id,
        court_id=row.court_id,
        triggered_at=row.triggered_at,
        start_at=row.start_at,
        end_at=row.end_at,
        status=row.status,
        duration_s=row.duration_s,
        file_key=row.file_key,
        thumb_key=row.thumb_key,
        error=row.error,
        created_at=row.created_at,
    )
