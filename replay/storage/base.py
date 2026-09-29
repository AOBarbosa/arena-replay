"""ClipStorage interface: the only layer that knows where final clips live."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class StorageError(Exception):
    """Invalid key or storage failure."""


class ClipStorage(ABC):
    @abstractmethod
    def save(self, source: Path, key: str) -> None:
        """Moves a local file into storage under `key`."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Removes `key`; missing keys are ignored."""

    @abstractmethod
    def url_for(self, key: str) -> str:
        """URL where the file can be fetched directly."""

    def local_path(self, key: str) -> Path | None:
        """Local file path, if this storage keeps files on disk (used to serve them)."""
        return None
