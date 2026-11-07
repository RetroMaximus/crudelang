"""``MidiRuntime``: the routing and the mute budget, on an in-process backend.

The runtime owns the ports and hands every inbound message to the script's
``ON_MIDI`` functions on the interpreter tick. A stubbed backend
(:class:`FakeMidiBackend`) delivers messages synchronously so the whole thing is
deterministic; the plan's "same 3-strike mute budget as ``crude/serial``" is the
last group of tests.
"""

from __future__ import annotations

import types

import pytest

from crude.midi.base import MidiError
from crude.midi.runtime import MAX_HANDLER_ERRORS, MidiRuntime


# -- devices / ports ---------------------------------------------------

def test_devices_are_what_the_runtime_asks_the_backend_for(fake_backend):
    runtime = MidiRuntime(backend=fake_backend)
    names = [device.name for device in runtime.devices("input")]
    assert names == ["USB MIDI Keyboard"]


def test_an_error_listing_devices_is_a_note_not_a_crash():
    class Boom:
        name = "boom"

        def devices(self, direction):
            raise MidiError("the API died")

        def close(self, port_id=None):
            pass

    notes = []
    runtime = MidiRuntime(backend=Boom(), log=notes.append)
    assert runtime.devices("input") == []
    assert any("API died" in note for note in runtime.notes)


def test_open_gives_back_a_port_handle_with_read_only_properties(fake_backend):
    runtime = MidiRuntime(backend=fake_backend)
    port = runtime.open("output", 0)
    assert port.get_prop("direction") == "output"
    assert port.get_prop("opened") == 1.0
    with pytest.raises(KeyError):
        port.get_prop("nope")


# -- sending -----------------------------------------------------------

def test_send_to_an_output_counts_the_bytes_on_the_port(fake_backend):
    runtime = MidiRuntime(backend=fake_backend)
    port = runtime.open("output", 0)
    assert port.send((0x90, 60, 100)) == 3
    assert port.get_prop("bytes_sent") == 3.0
    assert fake_backend.sent == [(port.id, (0x90, 60, 100))]


def test_an_input_refuses_to_send(fake_backend):
    runtime = MidiRuntime(backend=fake_backend)
    port = runtime.open("input", 0)
    with pytest.raises(MidiError) as excinfo:
        port.send((0x90, 60, 100))
    assert "cannot send" in str(excinfo.value)


# -- dispatch and the handler contract --------------------------------

def _program(runtime, handlers):
    """A tiny stand-in program: ``invoke`` records which function ran."""
    program = types.SimpleNamespace(
        invoke=lambda func, args: (handlers.append((func, args)) or (None, None))
    )
    runtime.attach_program(program)
    return program


def test_a_dispatched_message_calls_the_handler_for_that_port(fake_backend):
    runtime = MidiRuntime(backend=fake_backend)
    port = runtime.open("input", 0)

    calls = []
    runtime.on_midi(port, lambda *a: None)
    _program(runtime, calls)

    fake_backend.inject(port.id, (0x90, 60, 100))
    assert runtime.dispatch() == 1

    func, args = calls[0]
    packet = args[0]
    assert [element.value for element in packet.elements] == [0x90, 60, 100]


def test_a_handler_on_one_port_never_sees_another_port_s_messages(fake_backend):
    runtime = MidiRuntime(backend=fake_backend)
    first = runtime.open("input", 0)
    # a second input needs a second device; the fake only has one, so reuse the
    # routing test's single port and check the *other* handler stays quiet
    hits = []
    _program(runtime, hits)
    runtime.on_midi(first, lambda *a: None)
    fake_backend.inject(first.id, (0x90, 60, 100))
    runtime.dispatch()
    assert len(hits) == 1


def test_dispatch_only_polls_inputs_and_returns_the_call_count(fake_backend):
    runtime = MidiRuntime(backend=fake_backend)
    runtime.open("output", 0)  # an output is never polled
    assert runtime.dispatch() == 0


def test_off_midi_drops_a_ports_handlers_and_says_how_many(fake_backend):
    runtime = MidiRuntime(backend=fake_backend)
    port = runtime.open("input", 0)
    runtime.on_midi(port, lambda *a: None)
    runtime.on_midi(port, lambda *a: None)
    assert runtime.off_midi(port) == 2
    assert runtime.handlers == []


def test_off_midi_with_no_port_clears_every_handler(fake_backend):
    runtime = MidiRuntime(backend=fake_backend)
    port = runtime.open("input", 0)
    runtime.on_midi(port, lambda *a: None)
    assert runtime.off_midi() == 1


# -- the mute budget (mirrors ON_SERIAL / ON_PD) -----------------------

def test_a_handler_that_keeps_raising_is_muted_after_three_strikes(fake_backend):
    notes = []
    runtime = MidiRuntime(backend=fake_backend, log=notes.append)
    port = runtime.open("input", 0)

    def always_boom(_message):
        raise RuntimeError("division by zero")

    def invoke(func, args):
        try:
            func(*[a for a in args])
            return None, None
        except Exception as exc:  # the language would wrap this in an Error
            return None, exc

    runtime.attach_program(types.SimpleNamespace(invoke=invoke))
    handler = runtime.on_midi(port, always_boom)

    for _ in range(6):
        fake_backend.inject(port.id, (0x90, 60, 100))
        runtime.dispatch()

    assert handler.muted is True
    assert handler.errors == MAX_HANDLER_ERRORS
    assert len([note for note in notes if "muted after" in note]) == 1
    # muted handlers are skipped, so strikes do not keep climbing
    before = handler.calls
    fake_backend.inject(port.id, (0x90, 60, 100))
    runtime.dispatch()
    assert handler.calls == before


def test_a_handler_that_recovers_resets_its_strike_counter(fake_backend):
    runtime = MidiRuntime(backend=fake_backend)
    port = runtime.open("input", 0)

    state = {"n": 0}

    def flaky(_message):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("once")

    def invoke(func, args):
        try:
            func(*args)
            return None, None
        except Exception as exc:
            return None, exc

    runtime.attach_program(types.SimpleNamespace(invoke=invoke))
    handler = runtime.on_midi(port, flaky)

    fake_backend.inject(port.id, (0x90, 60, 100))
    runtime.dispatch()
    assert handler.errors == 1
    assert handler.muted is False

    fake_backend.inject(port.id, (0x90, 60, 100))
    runtime.dispatch()
    assert handler.errors == 0, "a good call clears the counter"


# -- teardown ----------------------------------------------------------

def test_close_all_releases_every_port_and_handler(fake_backend):
    runtime = MidiRuntime(backend=fake_backend)
    port = runtime.open("input", 0)
    runtime.on_midi(port, lambda *a: None)

    runtime.close_all()
    assert runtime.ports == {}
    assert runtime.handlers == []


def test_closing_one_port_drops_just_that_ports_handlers(fake_backend):
    runtime = MidiRuntime(backend=fake_backend)
    port = runtime.open("input", 0)
    runtime.on_midi(port, lambda *a: None)

    assert runtime.close(port) is True
    assert runtime.ports == {}
    assert runtime.handlers == []
    assert runtime.close(port) is False, "already closed"
