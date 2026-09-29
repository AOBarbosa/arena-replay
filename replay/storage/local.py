"""ClipStorage on the local disk."""

from __future__ import annotations

import shutil
from pathlib import Path

from replay.storage.base import ClipStorage, StorageError


class LocalClipStorage(ClipStorage):
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def save(self, source: Path, key: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Same filesystem (work dir inside root) => atomic rename
        shutil.move(source, target)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def url_for(self, key: str) -> str:
        return self._path(key).as_uri()

    def local_path(self, key: str) -> Path | None:
        return self._path(key)

    def _path(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key:
            raise StorageError(f"invalid key: {key!r}")
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise StorageError(f"key escapes storage root: {key!r}")
        return path
