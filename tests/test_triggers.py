from __future__ import annotations

import io
from datetime import datetime
from types import SimpleNamespace

import pytest

from replay.triggers.base import Debouncer, TriggerError
from replay.triggers.keyboard import KeyboardTrigger, normalize_key_name, pressed_key_identifier
from replay.triggers.stdin import StdinTrigger


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_debouncer_is_per_court() -> None:
    clock = FakeClock()
    debouncer = Debouncer(5, clock=clock)
    assert debouncer.allow("court1")
    assert debouncer.allow("court2")
    clock.now += 4.9
    assert not debouncer.allow("court1")
    clock.now += 0.1
    assert debouncer.allow("court1")


def test_debouncer_rejected_events_do_not_extend_the_window() -> None:
    clock = FakeClock()
    debouncer = Debouncer(5, clock=clock)
    assert debouncer.allow("court1")
    for _ in range(4):
        clock.now += 1
        assert not debouncer.allow("court1")
    clock.now += 1
    assert debouncer.allow("court1")


def test_key_names() -> None:
    assert normalize_key_name("Space") == "space"
    assert normalize_key_name("A") == "a"
    assert normalize_key_name(" f1 ") == "f1"
    assert pressed_key_identifier(SimpleNamespace(name="space")) == "space"
    assert pressed_key_identifier(SimpleNamespace(char="B")) == "b"
    assert pressed_key_identifier(SimpleNamespace(char=None)) is None


def keyboard_trigger(events: list[tuple[str, datetime]]) -> KeyboardTrigger:
    trigger = KeyboardTrigger({"space": "court1", "B": "court2"}, debounce_s=60)
    trigger._callback = lambda court, at: events.append((court, at))  # no real listener
    return trigger


def test_keyboard_trigger_maps_keys_and_debounces() -> None:
    events: list[tuple[str, datetime]] = []
    trigger = keyboard_trigger(events)
    trigger.handle_key(SimpleNamespace(name="space"))
    trigger.handle_key(SimpleNamespace(name="space"))  # debounced
    trigger.handle_key(SimpleNamespace(char="b"))
    trigger.handle_key(SimpleNamespace(char="x"))  # not mapped
    trigger.handle_key(SimpleNamespace(name="enter"))  # not mapped
    assert [court for court, _ in events] == ["court1", "court2"]
    assert all(at.tzinfo is not None for _, at in events)


def test_keyboard_trigger_rejects_unknown_key_names() -> None:
    trigger = KeyboardTrigger({"spcae": "court1"}, debounce_s=1)
    with pytest.raises(TriggerError, match="spcae"):
        trigger._validate_key_names({"space", "enter"})


def test_stdin_trigger() -> None:
    events: list[str] = []
    stream = io.StringIO("\ncourt2\nnope\ncourt2\n")
    trigger = StdinTrigger(["court1", "court2"], debounce_s=60, stream=stream)
    trigger.start(lambda court, _at: events.append(court))
    trigger.join(timeout=2)
    # Enter -> first court; unknown ignored; second court2 debounced
    assert events == ["court1", "court2"]
