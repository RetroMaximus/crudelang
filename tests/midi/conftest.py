"""Fixtures for the MIDI suite: no controller, no ``python-rtmidi`` required.

Every test drives a stub in ``sys.modules["rtmidi"]`` (a faithful echo of the
calls :mod:`crude.midi.rtmidi_backend` makes) or a small in-process backend that
lets a test hand a message straight to the runtime. ``pytest.ini`` and the
autouse :func:`_no_real_rtmidi` fixture guarantee the real library is never
imported here, so a machine without a wheel still sees a full green run - the
same rule the audio suite runs on for ``sounddevice``/``pyaudio``.

The rules the rest of the repository follows hold here too: no ``sleep``, no
network, no threads leaking between tests, no state shared between cases.
"""

from __future__ import annotations

import sys
import types
from typing import List

import pytest

from crude.lang import Program
from crude.midi.base import (
    MidiDeviceInfo,
    MidiError,
    MidiMessage,
    check_direction,
)

#: what the stub reports by default: one input, one output.
DEFAULT_INPUTS = ["USB MIDI Keyboard"]
DEFAULT_OUTPUTS = ["USB MIDI Synth"]


# -- the stub rtmidi module --------------------------------------------


class _StubPort:
    """Shared behaviour of ``rtmidi.MidiIn``/``MidiOut``."""

    def __init__(self, module, direction):
        self.module = module
        self.direction = direction
        self.opened_index = None
        self.closed = False
        module.created.append(self)

    def get_ports(self):
        return list(self.module.names(self.direction))

    def get_current_api(self):
        return self.module.api

    def open_midiport(self, index):
        names = self.module.names(self.direction)
        if not (0 <= int(index) < len(names)):
            raise ValueError(f"no such {self.direction} port {index}")
        self.opened_index = int(index)

    def close_port(self):
        self.closed = True


class StubMidiIn(_StubPort):
    """``rtmidi.MidiIn``: stores the callback, lets a test fire it by hand."""

    def __init__(self, module):
        super().__init__(module, "input")
        self.callback = None
        self.cancelled = False

    def set_callback(self, callback):
        self.callback = callback

    def cancel_callback(self):
        self.cancelled = True
        self.callback = None

    def fire(self, packet, stamp=0.0):
        """Simulate the library's own thread delivering one packet."""
        if self.callback is not None:
            self.callback(stamp, list(packet))


class StubMidiOut(_StubPort):
    """``rtmidi.MidiOut``: records every ``send_message``."""

    def __init__(self, module):
        super().__init__(module, "output")
        self.sent: List[tuple] = []

    def send_message(self, packet):
        packet = tuple(int(byte) for byte in packet)
        self.sent.append(packet)
        self.module.sent.append(packet)


class StubRtMidi(types.ModuleType):
    """Just enough of ``rtmidi`` for the adapter, plus everything it recorded."""

    def __init__(self, inputs=None, outputs=None, api="Stub API"):
        super().__init__("rtmidi")
        self._inputs = list(DEFAULT_INPUTS if inputs is None else inputs)
        self._outputs = list(DEFAULT_OUTPUTS if outputs is None else outputs)
        self.api = api
        self.created: List[_StubPort] = []
        self.sent: List[tuple] = []

    def names(self, direction):
        return self._inputs if direction == "input" else self._outputs

    def MidiIn(self):  # noqa: N802 - the library spells it like this
        return StubMidiIn(self)

    def MidiOut(self):  # noqa: N802
        return StubMidiOut(self)

    # -- test helpers --------------------------------------------------
    def inputs(self):
        return [port for port in self.created if isinstance(port, StubMidiIn)]

    def outputs(self):
        return [port for port in self.created if isinstance(port, StubMidiOut)]


# -- an in-process backend for runtime/binding tests -------------------


class FakeMidiBackend:
    """A backend a test can drive directly: enumerate, open, inject, send.

    It answers the same five verbs as :class:`~crude.midi.base.MidiBackend` but
    holds no library and no queue - :meth:`inject` calls the runtime's input
    callback synchronously, which is the deterministic stand-in for "a message
    arrived on the native thread since the last frame".
    """

    name = "fake"
    live = False

    def __init__(self, log=None, inputs=None, outputs=None):
        self.log = log or (lambda message: None)
        self._inputs = list(DEFAULT_INPUTS if inputs is None else inputs)
        self._outputs = list(DEFAULT_OUTPUTS if outputs is None else outputs)
        self.callbacks = {}
        self.directions = {}
        self.inbox = {}
        self.sent: List[tuple] = []
        self.notes: List[str] = []
        self.closed: List[int] = []
        self._next = 0

    @classmethod
    def available(cls):
        return True

    def devices(self, direction):
        check_direction(direction)
        names = self._inputs if direction == "input" else self._outputs
        return [MidiDeviceInfo(name=title, index=index, direction=direction,
                               api="Fake")
                for index, title in enumerate(names)]

    def _new_id(self):
        self._next += 1
        return self._next

    def open_input(self, index, callback):
        if not (0 <= int(index) < len(self._inputs)):
            raise MidiError(f"there is no MIDI input port {index}")
        port_id = self._new_id()
        self.callbacks[port_id] = callback
        self.directions[port_id] = "input"
        self.inbox[port_id] = []
        return port_id

    def open_output(self, index):
        if not (0 <= int(index) < len(self._outputs)):
            raise MidiError(f"there is no MIDI output port {index}")
        port_id = self._new_id()
        self.directions[port_id] = "output"
        return port_id

    def send(self, port_id, message):
        if self.directions.get(port_id) != "output":
            raise MidiError(f"there is no open MIDI output port {port_id}")
        packet = tuple(int(byte) & 0xFF for byte in message)
        self.sent.append((port_id, packet))
        return len(packet)

    def poll(self, port_id):
        # deliver on the interpreter thread, exactly as RtMidiBackend.poll does:
        # whatever inject() queued since the last poll is handed to the runtime
        for packet in self.inbox.get(port_id, []):
            self.callbacks[port_id](
                MidiMessage(bytes=packet, delta=0.0, port=port_id)
            )
        self.inbox[port_id] = []
        return None

    def close(self, port_id=None):
        if port_id is None:
            self.callbacks.clear()
            self.directions.clear()
            self.inbox.clear()
        else:
            self.callbacks.pop(port_id, None)
            self.directions.pop(port_id, None)
            self.inbox.pop(port_id, None)
            self.closed.append(port_id)

    def inject(self, port_id, packet):
        """Queue one message as if it arrived on the library's own thread.

        Nothing reaches the runtime until ``poll`` runs on the interpreter tick,
        which is precisely the handover :meth:`MidiRuntime.dispatch` counts.
        """
        self.inbox.setdefault(port_id, []).append(tuple(packet))


# -- keeping the real library out --------------------------------------


@pytest.fixture(autouse=True)
def _no_real_rtmidi(monkeypatch):
    """This suite never touches the machine: ``rtmidi`` starts out missing.

    A test that wants the real adapter path calls :func:`stub_rtmidi`, which
    overrides this - so forgetting the stub fails loudly (the backend reports
    itself unavailable) instead of loading a controller driver.
    """
    monkeypatch.setitem(sys.modules, "rtmidi", None)


@pytest.fixture
def stub_rtmidi(monkeypatch):
    """``sys.modules["rtmidi"] = a stub``; the stub itself is returned."""

    def _install(inputs=None, outputs=None, api="Stub API"):
        stub = StubRtMidi(inputs, outputs, api)
        monkeypatch.setitem(sys.modules, "rtmidi", stub)
        return stub

    return _install


# -- the runtime a test drives -----------------------------------------


@pytest.fixture
def fake_backend():
    return FakeMidiBackend()


@pytest.fixture
def midi_runtime(fake_backend):
    from crude.midi.runtime import MidiRuntime

    notes = []
    runtime = MidiRuntime(backend=fake_backend, log=notes.append)
    runtime.notes_list = notes
    yield runtime
    runtime.close_all()


# -- a program with the MIDI surface wired -----------------------------


@pytest.fixture
def midi_program(midi_runtime):
    return Program("<midi-test>",
                   host=types.SimpleNamespace(midi=midi_runtime))


@pytest.fixture
def run_midi(midi_program, midi_runtime):
    """``run_midi(source) -> (value, error)`` against the wired program."""

    def _run(source):
        midi_runtime.attach_program(midi_program)
        return midi_program.run(source)

    return _run


@pytest.fixture
def eval_midi(run_midi):
    """``eval_midi(source) -> value``; fails the test on an error."""

    def _eval(source):
        value, error = run_midi(source)
        assert error is None, error.as_string()
        return value

    return _eval


@pytest.fixture
def midi_failure(run_midi):
    """``midi_failure(source) -> Error``; fails the test when it succeeds."""

    def _failure(source):
        value, error = run_midi(source)
        assert error is not None, f"expected an error, got {value!r}"
        return error

    return _failure
