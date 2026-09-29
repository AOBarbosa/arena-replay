"""Global keyboard trigger using pynput (requires an Xorg session on Linux)."""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from replay.triggers.base import Debouncer, Trigger, TriggerCallback, TriggerError

log = logging.getLogger(__name__)


def normalize_key_name(name: str) -> str:
    """Config key name -> identifier: single characters are case-insensitive."""
    name = name.strip()
    return name.lower() if len(name) == 1 else name.lower().replace(" ", "_")


def pressed_key_identifier(key: Any) -> str | None:
    """Identifier of a pynput key event: `Key.space` -> "space", `KeyCode('A')` -> "a"."""
    name = getattr(key, "name", None)
    if isinstance(name, str):
        return name
    char = getattr(key, "char", None)
    if isinstance(char, str) and char:
        return char.lower()
    return None


class KeyboardTrigger(Trigger):
    def __init__(self, keys: Mapping[str, str], debounce_s: float) -> None:
        """`keys` maps a configured key name to its court_id."""
        self._courts_by_key = {normalize_key_name(k): court for k, court in keys.items()}
        self._debouncer = Debouncer(debounce_s)
        self._listener: Any = None
        self._callback: TriggerCallback | None = None

    def start(self, callback: TriggerCallback) -> None:
        try:
            from pynput import keyboard
        except Exception as exc:  # noqa: BLE001 - pynput fails in many ways without a display
            raise TriggerError(
                f"pynput is unavailable ({exc}); on Linux this needs an Xorg session "
                "(DISPLAY set). Use --stdin to trigger from the terminal instead."
            ) from exc
        self._validate_key_names({key.name for key in keyboard.Key})
        self._callback = callback
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.start()
        log.info("keyboard trigger ready: %s", self._describe())
        if sys.platform == "darwin":
            log.info("macOS: the terminal needs Input Monitoring permission to see key presses")

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def handle_key(self, key: Any) -> None:
        """Handles a key press (public so it can be tested without a keyboard)."""
        identifier = pressed_key_identifier(key)
        court_id = self._courts_by_key.get(identifier) if identifier else None
        if court_id is None or self._callback is None:
            return
        if not self._debouncer.allow(court_id):
            log.info("[%s] trigger ignored (debounce)", court_id)
            return
        triggered_at = datetime.now(UTC)
        log.info("[%s] trigger fired by key %r", court_id, identifier)
        self._callback(court_id, triggered_at)

    def _on_press(self, key: Any) -> None:
        try:
            self.handle_key(key)
        except Exception:
            # An exception here would kill the pynput listener thread
            log.exception("error handling key press")

    def _validate_key_names(self, special_keys: set[str]) -> None:
        invalid = [k for k in self._courts_by_key if len(k) > 1 and k not in special_keys]
        if invalid:
            raise TriggerError(
                f"unknown trigger key(s): {', '.join(invalid)}. "
                f"Use a single character or one of: {', '.join(sorted(special_keys))}"
            )

    def _describe(self) -> str:
        return ", ".join(f"{key} -> {court}" for key, court in self._courts_by_key.items())
