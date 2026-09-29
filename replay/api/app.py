"""FastAPI app. Reads the database through the repository and serves files through
ClipStorage. Never calls ffmpeg."""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import date, datetime, time, timedelta
from importlib import resources
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from replay.api.schemas import ClipOut, ClipPage, CourtOut, HealthOut
from replay.config import AppConfig
from replay.db.repository import Cursor, Repository
from replay.models import Clip, ClipStatus
from replay.storage.base import ClipStorage

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
MAX_PAGE_SIZE = 100
# Clip files never change once ready (unique key per clip)
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


# --- cursor --------------------------------------------------------------------


def encode_cursor(clip: Clip) -> str:
    raw = f"{clip.triggered_at.isoformat()}|{clip.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(value: str) -> Cursor:
    try:
        padded = value + "=" * (-len(value) % 4)
        at, clip_id = base64.urlsafe_b64decode(padded).decode().split("|")
        triggered_at = datetime.fromisoformat(at)
        if triggered_at.tzinfo is None:
            raise ValueError("naive datetime")
        return triggered_at, UUID(clip_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


# --- dependencies -----------------------------------------------------------------


def get_repo(request: Request) -> Repository:
    return request.app.state.repo


def get_storage(request: Request) -> ClipStorage:
    return request.app.state.storage


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


RepoDep = Annotated[Repository, Depends(get_repo)]
StorageDep = Annotated[ClipStorage, Depends(get_storage)]
ConfigDep = Annotated[AppConfig, Depends(get_config)]


# --- helpers ------------------------------------------------------------------------


def to_clip_out(clip: Clip) -> ClipOut:
    ready = clip.status is ClipStatus.READY and clip.file_key is not None
    base = f"{API_PREFIX}/clips/{clip.id}"
    return ClipOut(
        id=clip.id,
        court_id=clip.court_id,
        status=clip.status,
        triggered_at=clip.triggered_at,
        start_at=clip.start_at,
        end_at=clip.end_at,
        duration_s=clip.duration_s,
        video_url=f"{base}/video" if ready else None,
        thumbnail_url=f"{base}/thumbnail" if ready and clip.thumb_key else None,
        download_url=f"{base}/download" if ready else None,
    )


def day_range(day: date, config: AppConfig) -> tuple[datetime, datetime]:
    """Local calendar day (config timezone) -> [start, end) as aware datetimes."""
    start = datetime.combine(day, time.min, tzinfo=config.tz)
    return start, start + timedelta(days=1)


def download_filename(clip: Clip, config: AppConfig) -> str:
    local = clip.triggered_at.astimezone(config.tz)
    return f"{clip.court_id}_{local:%Y-%m-%d_%H-%M-%S}.mp4"


def ready_clip(repo: Repository, clip_id: UUID) -> Clip:
    clip = repo.get_clip(clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    if clip.status is not ClipStatus.READY or clip.file_key is None:
        raise HTTPException(status_code=404, detail=f"clip is {clip.status.value}")
    return clip


def serve_file(
    storage: ClipStorage,
    key: str,
    media_type: str,
    *,
    download_name: str | None = None,
) -> Response:
    path = storage.local_path(key)
    if path is None:
        # Remote storage (future R2/S3): let the client fetch it directly
        return RedirectResponse(storage.url_for(key), status_code=307)
    if not path.is_file():
        log.error("file missing from storage: %s", key)
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(
        path,
        media_type=media_type,
        filename=download_name,
        content_disposition_type="attachment" if download_name else "inline",
        headers={"Cache-Control": IMMUTABLE_CACHE},
    )


# --- routes ---------------------------------------------------------------------------

router = APIRouter(prefix=API_PREFIX)


@router.get("/health", tags=["system"])
def health(repo: RepoDep) -> HealthOut:
    try:
        repo.list_courts()
        database = True
    except Exception:  # noqa: BLE001 - any failure means unhealthy
        log.exception("health check: database unavailable")
        database = False
    return HealthOut(status="ok" if database else "degraded", database=database)


@router.get("/courts", tags=["courts"])
def list_courts(repo: RepoDep) -> list[CourtOut]:
    return [CourtOut(id=c.id, name=c.name) for c in repo.list_courts()]


@router.get("/clips", tags=["clips"])
def list_clips(
    repo: RepoDep,
    config: ConfigDep,
    court_id: str | None = None,
    date: Annotated[
        date | None, Query(description="Local calendar day (YYYY-MM-DD) in the server timezone")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    cursor: Annotated[str | None, Query(description="next_cursor from the previous page")] = None,
) -> ClipPage:
    """Ready clips, newest first, with cursor pagination."""
    since, until = day_range(date, config) if date else (None, None)
    clips = repo.list_ready_clips(
        limit=limit + 1,  # one extra to know if there is a next page
        court_id=court_id,
        since=since,
        until=until,
        cursor=decode_cursor(cursor) if cursor else None,
    )
    page = clips[:limit]
    next_cursor = encode_cursor(page[-1]) if len(clips) > limit else None
    return ClipPage(items=[to_clip_out(c) for c in page], next_cursor=next_cursor)


@router.get("/clips/{clip_id}", tags=["clips"])
def get_clip(clip_id: UUID, repo: RepoDep) -> ClipOut:
    """Any status: lets a client poll a clip until it is ready."""
    clip = repo.get_clip(clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    return to_clip_out(clip)


@router.get(
    "/clips/{clip_id}/video",
    tags=["clips"],
    response_class=FileResponse,
    responses={200: {"content": {"video/mp4": {}}}, 206: {"description": "Partial content"}},
)
def clip_video(clip_id: UUID, repo: RepoDep, storage: StorageDep) -> Response:
    clip = ready_clip(repo, clip_id)
    assert clip.file_key is not None
    return serve_file(storage, clip.file_key, "video/mp4")


@router.get(
    "/clips/{clip_id}/thumbnail",
    tags=["clips"],
    response_class=FileResponse,
    responses={200: {"content": {"image/jpeg": {}}}},
)
def clip_thumbnail(clip_id: UUID, repo: RepoDep, storage: StorageDep) -> Response:
    clip = ready_clip(repo, clip_id)
    if clip.thumb_key is None:
        raise HTTPException(status_code=404, detail="clip has no thumbnail")
    return serve_file(storage, clip.thumb_key, "image/jpeg")


@router.get(
    "/clips/{clip_id}/download",
    tags=["clips"],
    response_class=FileResponse,
    responses={200: {"content": {"video/mp4": {}}}},
)
def clip_download(clip_id: UUID, repo: RepoDep, storage: StorageDep, config: ConfigDep) -> Response:
    clip = ready_clip(repo, clip_id)
    assert clip.file_key is not None
    return serve_file(
        storage, clip.file_key, "video/mp4", download_name=download_filename(clip, config)
    )


# --- app factory ------------------------------------------------------------------------


def create_app(config: AppConfig, repo: Repository, storage: ClipStorage) -> FastAPI:
    app = FastAPI(
        title="ArenaReplay API",
        version="1.0.0",
        description="Instant replay clips for beach courts. All times are UTC (ISO 8601).",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    app.state.config = config
    app.state.repo = repo
    app.state.storage = storage
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.api.cors_origins,
        allow_methods=["GET", "HEAD"],
        allow_headers=["*", "Range"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition"],
    )
    app.include_router(router)

    dev_page = resources.files("replay.api").joinpath("dev_page.html").read_text("utf-8")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/dev")

    @app.get("/dev", include_in_schema=False)
    def dev() -> HTMLResponse:
        """Minimal test page (disposable; not part of the API contract)."""
        return HTMLResponse(dev_page)

    return app
