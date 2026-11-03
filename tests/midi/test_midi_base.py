"""The MIDI vocabulary: ``base.py`` and the null backend, no library involved.

These pin the pieces every backend and the runtime depend on - the direction
guard, the message decode, the device description ``MIDI_DEVICES`` turns into a
list line, and the always-available null backend a ``--no-midi`` run lands on.
"""

from __future__ import annotations

import pytest

from crude.midi.base import (
    DIRECTIONS,
    MidiBackend,
    MidiDeviceInfo,
    MidiError,
    MidiMessage,
    MidiUnavailable,
    check_direction,
)
from crude.midi.null_backend import NullMidiBackend


# -- directions ---------------------------------------------------------

def test_only_the_two_directions_are_accepted():
    assert DIRECTIONS == ("input", "output")
    assert check_direction("input") == "input"
    assert check_direction("output") == "output"


@pytest.mark.parametrize("bad", ["in", "both", "", "midi"])
def test_a_wrong_direction_names_the_words_it_expected(bad):
    with pytest.raises(MidiError) as excinfo:
        check_direction(bad)
    assert "input, output" in str(excinfo.value)


# -- a message --------------------------------------------------------

def test_a_message_decodes_its_command_and_channel():
    note_on = MidiMessage(bytes=(0x90, 60, 100))
    assert note_on.status == 0x90
    assert note_on.command == 0x90
    assert note_on.channel == 0

    assert MidiMessage(bytes=(0xA3, 60, 100)).channel == 3


def test_a_system_message_has_no_channel():
    assert MidiMessage(bytes=(0xF8,)).channel == -1


def test_an_empty_message_is_harmless():
    empty = MidiMessage(bytes=())
    assert empty.status == 0
    assert empty.describe() == ""


def test_a_message_describes_itself_as_hex_bytes():
    assert MidiMessage(bytes=(0x90, 0x3C, 0x64)).describe() == "90 3C 64"


# -- a device ---------------------------------------------------------

def test_a_device_line_is_what_a_dropdown_shows():
    device = MidiDeviceInfo(name="Keyboard", index=2, direction="input", api="ALSA")
    assert device.describe() == "[2] Keyboard (input, ALSA)"


def test_a_device_without_an_api_still_reads_cleanly():
    device = MidiDeviceInfo(name="Synth", index=0, direction="output")
    assert device.describe() == "[0] Synth (output)"


# -- the null backend -------------------------------------------------

def test_the_null_backend_is_always_available_with_no_ports():
    assert NullMidiBackend.available() is True
    assert NullMidiBackend.devices("input") == []
    assert NullMidiBackend.devices("output") == []


def test_the_null_backend_refuses_an_input_but_records_an_output():
    backend = NullMidiBackend()
    with pytest.raises(MidiUnavailable):
        backend.open_input(0, lambda message: None)

    port_id = backend.open_output(0)
    assert backend.send(port_id, (0x90, 60, 100)) == 3
    assert backend.sent == [(port_id, (0x90, 60, 100))]


def test_sending_to_a_port_the_null_backend_never_opened_fails():
    backend = NullMidiBackend()
    with pytest.raises(MidiError):
        backend.send(99, (0x90, 60, 100))


# -- the abstract surface ---------------------------------------------

def test_a_backend_must_answer_every_verb():
    with pytest.raises(TypeError):
        MidiBackend()  # abstract: cannot be instantiated as-is


def test_the_abc_hands_out_a_default_poll_and_describe():
    class Bare(NullMidiBackend):
        pass

    backend = Bare()
    assert backend.poll(0) is None  # the base no-op survives a subclass
    assert backend.describe().startswith("midi")
