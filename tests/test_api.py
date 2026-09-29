from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from replay.api.app import create_app, decode_cursor, encode_cursor
from replay.config import ApiConfig, AppConfig, CourtConfig, PathsConfig
from replay.db.repository import Repository
from replay.models import Clip, ClipStatus, Court
from replay.storage.local import LocalClipStorage

# 15:00 UTC = 12:00 in Fortaleza (UTC-3)
T0 = datetime(2026, 9, 28, 15, 0, 0, tzinfo=UTC)
VIDEO = bytes(range(256)) * 40  # 10 KiB of fake "video"


@pytest.fixture
def storage(tmp_path: Path) -> LocalClipStorage:
    return LocalClipStorage(tmp_path / "clips")


@pytest.fixture
def client(tmp_path: Path, repo: Repository, storage: LocalClipStorage) -> TestClient:
    config = AppConfig(
        courts=[CourtConfig(id="court1", name="Court 1", stream_url="x", trigger_key="a")],
        paths=PathsConfig(segments_dir=tmp_path / "seg", clips_dir=tmp_path / "clips"),
        api=ApiConfig(cors_origins=["http://localhost:3000"]),
    )
    repo.sync_courts([Court("court1", "Court 1"), Court("court2", "Court 2")])
    return TestClient(create_app(config, repo, storage))


def add_ready_clip(
    repo: Repository, storage: LocalClipStorage, court_id: str, at: datetime, thumb: bool = True
) -> Clip:
    clip = repo.create_clip(court_id, at, at - timedelta(seconds=30), at + timedelta(seconds=3))
    key = f"{court_id}/{clip.id}"
    work = storage.root / ".work"
    work.mkdir(exist_ok=True)
    (work / "v.mp4").write_bytes(VIDEO)
    storage.save(work / "v.mp4", f"{key}.mp4")
    if thumb:
        (work / "t.jpg").write_bytes(b"\xff\xd8jpeg")
        storage.save(work / "t.jpg", f"{key}.jpg")
    repo.mark_clip_ready(
        clip.id,
        start_at=clip.start_at,
        end_at=clip.end_at,
        duration_s=33.0,
        file_key=f"{key}.mp4",
        thumb_key=f"{key}.jpg" if thumb else None,
    )
    return clip


def test_courts(client: TestClient) -> None:
    assert client.get("/api/v1/courts").json() == [
        {"id": "court1", "name": "Court 1"},
        {"id": "court2", "name": "Court 2"},
    ]


def test_health(client: TestClient) -> None:
    assert client.get("/api/v1/health").json() == {"status": "ok", "database": True}


def test_list_clips_pagination_and_urls(
    client: TestClient, repo: Repository, storage: LocalClipStorage
) -> None:
    clips = [add_ready_clip(repo, storage, "court1", T0 + timedelta(minutes=i)) for i in range(5)]
    repo.create_clip("court1", T0 + timedelta(hours=1), T0, T0)  # processing: hidden

    first = client.get("/api/v1/clips", params={"limit": 2}).json()
    assert [c["id"] for c in first["items"]] == [str(clips[4].id), str(clips[3].id)]
    item = first["items"][0]
    assert item["status"] == "ready"
    assert item["video_url"] == f"/api/v1/clips/{clips[4].id}/video"
    assert item["thumbnail_url"] == f"/api/v1/clips/{clips[4].id}/thumbnail"
    assert item["download_url"] == f"/api/v1/clips/{clips[4].id}/download"
    assert item["triggered_at"].startswith("2026-09-28T15:04:00")

    seen = [c["id"] for c in first["items"]]
    cursor = first["next_cursor"]
    while cursor:
        page = client.get("/api/v1/clips", params={"limit": 2, "cursor": cursor}).json()
        seen += [c["id"] for c in page["items"]]
        cursor = page["next_cursor"]
    assert seen == [str(c.id) for c in reversed(clips)]


def test_list_clips_filters(
    client: TestClient, repo: Repository, storage: LocalClipStorage
) -> None:
    today = add_ready_clip(repo, storage, "court1", T0)
    other_court = add_ready_clip(repo, storage, "court2", T0)
    # 02:00 UTC on the 29th is still the 28th in Fortaleza (23:00)
    late_night = add_ready_clip(repo, storage, "court1", datetime(2026, 9, 29, 2, tzinfo=UTC))
    add_ready_clip(repo, storage, "court1", datetime(2026, 9, 29, 4, tzinfo=UTC))  # the 29th

    by_court = client.get("/api/v1/clips", params={"court_id": "court2"}).json()
    assert [c["id"] for c in by_court["items"]] == [str(other_court.id)]

    by_day = client.get("/api/v1/clips", params={"court_id": "court1", "date": "2026-09-28"})
    assert [c["id"] for c in by_day.json()["items"]] == [str(late_night.id), str(today.id)]


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"date": "28/09/2026"}, {"cursor": "not-a-cursor"}],
)
def test_list_clips_bad_params(client: TestClient, params: dict[str, object]) -> None:
    response = client.get("/api/v1/clips", params=params)
    assert response.status_code in (400, 422)


def test_get_clip_any_status(client: TestClient, repo: Repository) -> None:
    processing = repo.create_clip("court1", T0, T0, T0)
    body = client.get(f"/api/v1/clips/{processing.id}").json()
    assert body["status"] == "processing"
    assert body["video_url"] is None
    assert client.get(f"/api/v1/clips/{uuid4()}").status_code == 404
    assert client.get("/api/v1/clips/not-a-uuid").status_code == 422
    assert client.get(f"/api/v1/clips/{processing.id}/video").status_code == 404


def test_video_supports_range(
    client: TestClient, repo: Repository, storage: LocalClipStorage
) -> None:
    clip = add_ready_clip(repo, storage, "court1", T0)
    full = client.get(f"/api/v1/clips/{clip.id}/video")
    assert full.status_code == 200
    assert full.headers["content-type"] == "video/mp4"
    assert full.headers["accept-ranges"] == "bytes"
    assert full.content == VIDEO

    part = client.get(f"/api/v1/clips/{clip.id}/video", headers={"Range": "bytes=100-199"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 100-199/{len(VIDEO)}"
    assert part.content == VIDEO[100:200]


def test_download_and_thumbnail(
    client: TestClient, repo: Repository, storage: LocalClipStorage
) -> None:
    clip = add_ready_clip(repo, storage, "court1", T0)
    download = client.get(f"/api/v1/clips/{clip.id}/download")
    assert download.status_code == 200
    disposition = download.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert "court1_2026-09-28_12-00-00.mp4" in disposition  # local time in the name

    thumb = client.get(f"/api/v1/clips/{clip.id}/thumbnail")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"

    no_thumb = add_ready_clip(repo, storage, "court1", T0 + timedelta(minutes=1), thumb=False)
    assert client.get(f"/api/v1/clips/{no_thumb.id}/thumbnail").status_code == 404
    listed = client.get(f"/api/v1/clips/{no_thumb.id}").json()
    assert listed["thumbnail_url"] is None


def test_missing_file_is_404(
    client: TestClient, repo: Repository, storage: LocalClipStorage
) -> None:
    clip = add_ready_clip(repo, storage, "court1", T0)
    storage.delete(f"court1/{clip.id}.mp4")
    assert client.get(f"/api/v1/clips/{clip.id}/video").status_code == 404


def test_cors_allows_configured_origin(client: TestClient) -> None:
    allowed = client.get("/api/v1/courts", headers={"Origin": "http://localhost:3000"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    denied = client.get("/api/v1/courts", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in denied.headers


def test_dev_page_and_openapi(client: TestClient) -> None:
    assert client.get("/", follow_redirects=False).headers["location"] == "/dev"
    page = client.get("/dev")
    assert page.status_code == 200 and 'const API = "/api/v1"' in page.text
    schema = client.get("/api/v1/openapi.json").json()
    assert {"/api/v1/clips", "/api/v1/clips/{clip_id}/video"} <= set(schema["paths"])
    assert "ClipPage" in schema["components"]["schemas"]


def test_cursor_roundtrip() -> None:
    clip = Clip(uuid4(), "court1", T0, T0, T0, ClipStatus.READY)
    assert decode_cursor(encode_cursor(clip)) == (T0, clip.id)


def test_committed_openapi_matches_app(client: TestClient) -> None:
    """docs/openapi.json is the frontend contract: regenerate it on purpose with
    `python -m replay.api --export-openapi docs/openapi.json`."""
    committed = json.loads((Path(__file__).parent.parent / "docs" / "openapi.json").read_text())
    assert client.app.openapi() == committed
