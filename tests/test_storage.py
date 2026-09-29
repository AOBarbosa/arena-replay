from __future__ import annotations

from pathlib import Path

import pytest

from replay.storage.base import StorageError
from replay.storage.local import LocalClipStorage


def test_save_url_and_delete(tmp_path: Path) -> None:
    storage = LocalClipStorage(tmp_path / "clips")
    source = tmp_path / "work" / "clip.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")

    storage.save(source, "court1/2026/09/28/abc.mp4")
    target = storage.local_path("court1/2026/09/28/abc.mp4")
    assert target is not None and target.read_bytes() == b"video"
    assert not source.exists()
    assert storage.url_for("court1/2026/09/28/abc.mp4") == target.as_uri()

    storage.delete("court1/2026/09/28/abc.mp4")
    storage.delete("court1/2026/09/28/abc.mp4")  # missing is fine
    assert not target.exists()


@pytest.mark.parametrize("key", ["", "/etc/passwd", "../outside.mp4", "a/../../x", "a\\b"])
def test_rejects_keys_outside_root(tmp_path: Path, key: str) -> None:
    storage = LocalClipStorage(tmp_path / "clips")
    with pytest.raises(StorageError):
        storage.url_for(key)
