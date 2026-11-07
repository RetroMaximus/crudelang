"""The backend chain: ``open_backend`` always lands somewhere, and only a strict
ask can fail.

A missing ``python-rtmidi`` is the ordinary case, not an error - the fallback
ends on the null backend, which reports no devices and opens nothing. Only a
*strict* request (a manifest that says ``"optional": false``) raises, and even
then only after naming every backend it tried. These tests never install the
real library; a couple stub it in to prove the chain prefers it.
"""

from __future__ import annotations

import pytest

from crude.midi import (
    BACKENDS,
    MidiUnavailable,
    available_backends,
    backend_class,
    backend_names,
    list_devices,
    midi_chain,
    open_backend,
    register_backend,
)
from crude.midi.base import MidiError
from crude.midi.null_backend import NullMidiBackend
from crude.midi.rtmidi_backend import RtMidiBackend


# -- the registry ------------------------------------------------------

def test_the_two_shipped_backends_are_registered():
    assert "rtmidi" in BACKENDS and "null" in BACKENDS
    assert backend_names()[:2] == ("rtmidi", "null")
    assert backend_class("null") is NullMidiBackend


def test_an_unknown_backend_is_named_alongside_the_known_ones():
    with pytest.raises(MidiError) as excinfo:
        backend_class("notreal")
    assert "notreal" in str(excinfo.value)
    assert "rtmidi" in str(excinfo.value)


def test_the_chain_orders_preferred_first_then_the_rest_then_null():
    assert midi_chain(None) == ("rtmidi", "null")
    assert midi_chain("null") == ("null", "rtmidi")
    assert midi_chain("null", strict=True) == ("null",)


def test_a_strict_chain_must_name_something():
    with pytest.raises(MidiError):
        midi_chain(None, strict=True)


def test_registering_a_backend_accepts_the_decorator_form():
    class Custom(NullMidiBackend):
        name = "custom-test"

    register_backend(Custom)
    try:
        assert backend_class("custom-test") is Custom
        assert "custom-test" in backend_names()
    finally:
        BACKENDS.pop("custom-test", None)


# -- availability ------------------------------------------------------

def test_the_null_backend_alone_is_available_when_rtmidi_is_missing():
    # autouse fixture has rtmidi absent
    assert RtMidiBackend.available() is False
    assert available_backends() == ["null"]


def test_a_stubbed_rtmidi_makes_it_the_available_first_choice(stub_rtmidi):
    stub_rtmidi()
    assert available_backends()[0] == "rtmidi"


# -- the fallback chain ------------------------------------------------

def test_the_chain_falls_back_to_null_without_the_library():
    notes = []
    backend = open_backend(log=notes.append)
    assert isinstance(backend, NullMidiBackend)
    assert any("rtmidi is not usable" in note for note in notes)


def test_a_strict_ask_raises_after_naming_every_attempt():
    with pytest.raises(MidiUnavailable) as excinfo:
        open_backend(preferred="rtmidi", strict=True)
    assert "rtmidi" in str(excinfo.value)


def test_a_preferred_real_backend_is_used_when_present(stub_rtmidi):
    stub_rtmidi()
    backend = open_backend(preferred="rtmidi")
    assert isinstance(backend, RtMidiBackend)


def test_an_unknown_preferred_name_is_skipped_not_fatal():
    backend = open_backend(preferred="ghost")
    assert isinstance(backend, NullMidiBackend)


# -- discovery ---------------------------------------------------------

def test_list_devices_sweeps_every_usable_backend(stub_rtmidi):
    stub_rtmidi(inputs=["Keyboard"], outputs=["Synth"])
    found = list_devices()
    assert {device.direction for device in found} == {"input", "output"}
    assert {device.name for device in found} == {"Keyboard", "Synth"}


def test_list_devices_one_direction(stub_rtmidi):
    stub_rtmidi(inputs=["Keyboard"], outputs=["Synth"])
    inputs = list_devices(direction="input")
    assert [device.name for device in inputs] == ["Keyboard"]


def test_a_backend_that_cannot_enumerate_contributes_nothing():
    # no library installed: rtmidi is skipped, null has no ports -> empty
    assert list_devices() == []
