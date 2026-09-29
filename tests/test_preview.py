from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from replay.devtools.preview import render_page
from replay.models import Clip, ClipStatus
from replay.storage.local import LocalClipStorage

T0 = datetime(2026, 9, 28, 18, 0, 0, tzinfo=UTC)


def test_render_page_shows_statuses_and_escapes(tmp_path: Path) -> None:
    storage = LocalClipStorage(tmp_path)
    ready = Clip(
        uuid4(), "court1", T0, T0, T0, ClipStatus.READY, 33.2, "court1/a.mp4", "court1/a.jpg"
    )
    failed = Clip(uuid4(), "court1", T0, T0, T0, ClipStatus.FAILED, error="<no segments>")
    page = render_page(
        [ready, failed],
        storage,
        ZoneInfo("America/Fortaleza"),
        {"court1": "Court <1>"},
        T0,
        auto_reload=True,
    )
    assert storage.url_for("court1/a.mp4") in page
    assert 'poster="' in page
    assert "33.2 s" in page
    assert "&lt;no segments&gt;" in page and "<no segments>" not in page
    assert "Court &lt;1&gt;" in page
    assert "28/09 15:00:00" in page  # shown in local time (UTC-3)
    assert "location.reload" in page


def test_render_empty_page(tmp_path: Path) -> None:
    page = render_page([], LocalClipStorage(tmp_path), ZoneInfo("UTC"), {}, T0, auto_reload=False)
    assert "No clips yet" in page
    assert "location.reload" not in page
