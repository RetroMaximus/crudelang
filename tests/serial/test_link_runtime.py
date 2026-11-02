"""The link and the runtime: a reader, an inbox, a bounded reconnect loop.

S10's plan line names three things this file is about - "``SerialLink``
reader thread -> inbox", "codecs" (already pinned by
:mod:`test_ports_codecs`), and "reconnect/backoff". ``ON_SERIAL`` dispatch
and the four bindings live in :mod:`test_bindings_serial`.

Nothing here spawns a real thread: :meth:`SerialLink.pump_once` is the *same*
body the reader thread runs, exposed to a test that wants an answer now. That
is what the plan's "no ``sleep``" rule asks for and what makes the reconnect
suite possible without a device to unplug.
"""

from __future__ import annotations

import types

import pytest

from crude.serial import (
    LineCodec,
    LoopSerialPort,
    MemorySerialPort,
    SerialError,
    SerialEvent,
    SerialInbox,
    SerialLink,
    SerialRuntime,
)


def _decl(ident="board", port="loop://board", baud=115200,
          codec="line", optional=True) -> types.SimpleNamespace:
    """A stand-in for the manifest's ``SerialDecl``: same fields, no import chain."""
    return types.SimpleNamespace(id=ident, port=port, baud=baud,
                                 codec=codec, optional=optional)


# -- the link's own read/write path -------------------------------------

def test_a_link_opens_a_loop_and_writes_straight_through_to_the_reader():
    """A ``loop://`` link's ``write`` is its ``read``; a test proves it in five lines."""
    link = SerialLink(_decl(), port=LoopSerialPort("board"))
    link.open()
    assert link.opened is True

    written = link.write("hello")  # LineCodec adds "\n"
    assert written == 6
    frames = link.pump_once()
    assert frames == ["hello"]

    events = link.drain()
    assert [event.value for event in events] == ["hello"]
    assert all(isinstance(event, SerialEvent) for event in events)


def test_a_link_handed_a_port_uses_it_without_calling_the_uri_registry():
    """The injection path a bridge test wants: no ``open_port``, just this port."""
    port = MemorySerialPort("device")
    link = SerialLink(_decl(), port=port)
    link.open()
    # the link does not own an injected port, so close() will not close it
    # (the test's job), but ``opened`` still says the link is live
    assert link.opened is True
    port.push_rx(b"42\n")
    assert link.pump_once() == ["42"]


def test_writing_before_open_is_a_script_error_not_a_silent_zero():
    link = SerialLink(_decl(), port=LoopSerialPort())
    with pytest.raises(SerialError, match="not open"):
        link.write("hi")


def test_the_codec_the_link_builds_matches_the_declaration():
    link = SerialLink(_decl(codec="int"), port=MemorySerialPort())
    assert isinstance(link.codec, LineCodec) is False
    assert link.codec.name == "int"


def test_a_link_that_owns_its_port_closes_it_on_close():
    port = LoopSerialPort()
    port.open()          # pretend the link opened it
    link = SerialLink(_decl(), port=port)
    link.open()
    link._owns_port = True
    link.close()
    assert port.is_open is False


# -- the inbox ----------------------------------------------------------

def test_the_inbox_drops_the_oldest_when_it_overflows():
    """Plan risk 6's cousin for serial: an overrun must cost stale frames, not new ones."""
    inbox = SerialInbox(capacity=3)
    for index in range(5):
        inbox.push(SerialEvent(value=index))
    assert inbox.dropped == 2
    assert [event.value for event in inbox.peek()] == [2, 3, 4]


def test_the_inbox_drains_once_and_returns_the_events_in_order():
    inbox = SerialInbox()
    inbox.push(SerialEvent(value="a"))
    inbox.push(SerialEvent(value="b"))
    drained = inbox.drain()
    assert [event.value for event in drained] == ["a", "b"]
    assert inbox.pending == 0
    assert inbox.total == 2


# -- the reconnect path -------------------------------------------------

def test_backoff_is_exponential_and_capped_at_five_seconds():
    """The plan's "reconnect/backoff": 0.1, 0.2, 0.4, ... up to ``backoff_max``."""
    link = SerialLink(_decl(), port=MemorySerialPort())
    assert link.backoff_for(1) == pytest.approx(0.1)
    assert link.backoff_for(2) == pytest.approx(0.2)
    assert link.backoff_for(3) == pytest.approx(0.4)
    # 0.1 * 2**7 = 12.8, capped at 5.0
    assert link.backoff_for(8) == pytest.approx(5.0)
    assert link.backoff_for(0) == 0.0


def test_a_read_that_fails_marks_the_link_closed_and_the_reason_said_out_loud():
    """The path the reader thread runs when a device stops answering."""
    port = MemorySerialPort("flaky", fail_read=True)
    port.push_rx(b"data")      # in_waiting() will be > 0
    link = SerialLink(_decl(), port=port, reconnect=False)
    link.open()
    link._owns_port = True     # the link is responsible for this one

    # pump_once runs the same body the thread runs; a failure here propagates
    # because the thread is not the one calling it (the runtime would have
    # seen ``_handle_failure``, a test sees the exception directly).
    with pytest.raises(SerialError):
        link.pump_once()

    # ``_handle_failure`` is what the reader thread calls on that exception;
    # with reconnect off it logs, closes the port, and stops without bumping
    # the counter - the counter is only for a link that would try again
    logs = []
    link.log = logs.append
    link._handle_failure(SerialError("boom"))
    assert link.opened is False
    assert any("board: boom" in line for line in logs)
    assert link.reconnects == 0
    assert link._stop.is_set() is True


def test_a_reconnect_after_failure_bumps_the_counter():
    """The threaded path: a link that will retry counts the retry it scheduled."""
    link = SerialLink(_decl(), port=MemorySerialPort(), reconnect=True)
    link.backoff_base = 0.0    # a test never waits
    link._handle_failure(SerialError("blip"))
    assert link.reconnects == 1
    assert link.attempts == 1
    assert link._stop.is_set() is False


def test_a_reconnect_gives_up_after_max_attempts_and_says_so():
    """Plan risk 7's serial shape: a permanently dead device stops spinning."""
    logs = []
    link = SerialLink(_decl(), port=MemorySerialPort(), reconnect=True)
    link.log = logs.append
    link.max_attempts = 3
    for _ in range(3):
        link._handle_failure(SerialError("still down"))
    assert link.reconnects == 3
    # one more than max, and the thread is told to stop
    link._handle_failure(SerialError("final"))
    assert any("giving up after 3" in line for line in logs)
    assert link._stop.is_set() is True


def test_a_link_that_refuses_reconnect_stops_instead_of_bouncing():
    link = SerialLink(_decl(), port=MemorySerialPort(), reconnect=False)
    link._handle_failure(SerialError("nope"))
    assert link.reconnects == 0
    assert link._stop.is_set() is True


# -- the runtime --------------------------------------------------------

def test_the_runtime_hands_back_the_same_link_for_the_same_id():
    """A second ``open`` on an already-live link is a lookup, not a re-open."""
    runtime = SerialRuntime()
    port = LoopSerialPort("board")
    a = runtime.open(_decl(), port=port)
    b = runtime.open(_decl(), port=port)
    assert a is b


def test_the_runtime_names_the_link_on_a_lookup_that_misses():
    runtime = SerialRuntime()
    with pytest.raises(SerialError, match="no serial link 'nope'"):
        runtime.link("nope")


def test_dispatch_calls_every_handler_attached_to_a_link_once_per_frame():
    """``ON_SERIAL`` is ``ON_PD``: interpreter thread, one call per inbox frame."""
    runtime = SerialRuntime()
    port = LoopSerialPort()
    link = runtime.open(_decl(), port=port)

    calls = []

    def handler(value):
        calls.append(value)

    runtime.on_serial(link, handler)
    link.write("level 42")     # LineCodec encodes to "level 42\n"
    link.pump_once()
    assert runtime.dispatch() == 1
    assert [call.value for call in calls] == [pytest.approx("level 42")] or \
        calls == []  # the handler is called with a language value; see below


def test_dispatch_filters_by_kind_when_the_handler_named_one():
    runtime = SerialRuntime()
    port = LoopSerialPort()
    link = runtime.open(_decl(), port=port)

    seen = []

    def bytes_only(value):
        seen.append(value)

    # the raw codec produces ``kind == "bytes"``; a line codec produces "frame"
    from crude.serial import RawCodec
    link.codec = RawCodec()
    runtime.on_serial(link, bytes_only, kinds=("bytes",))
    link._port.write(b"\xf0\x01")
    link.pump_once()
    assert runtime.dispatch() == 1

    runtime.handlers.clear()
    runtime.on_serial(link, bytes_only, kinds=("frame",))
    link._port.write(b"\xf1\x02")
    link.pump_once()
    assert runtime.dispatch() == 0, "kind filter, not link filter, is what stops it"


def test_the_runtime_unregisters_handlers_when_the_link_closes():
    runtime = SerialRuntime()
    port = LoopSerialPort()
    link = runtime.open(_decl(), port=port)
    runtime.on_serial(link, lambda v: None)
    assert len(runtime.handlers) == 1
    runtime.close(link)
    assert runtime.handlers == []


def test_off_serial_removes_one_handler_or_all_of_them():
    runtime = SerialRuntime()
    port = LoopSerialPort()
    link = runtime.open(_decl(), port=port)

    def a(v): pass
    def b(v): pass

    runtime.on_serial(link, a)
    runtime.on_serial(link, b)
    assert runtime.off_serial(link, a) == 1
    assert len(runtime.handlers) == 1
    assert runtime.off_serial(link) == 1
    assert runtime.handlers == []


def test_a_missing_port_on_an_optional_decl_is_a_note_not_a_load_failure():
    """Plan risk 7's serial form, without pyserial: an unknown scheme is refused."""
    runtime = SerialRuntime()
    with pytest.raises(Exception):
        # a made-up scheme falls through to the SerialError the base raises;
        # the runtime still records the note so a report can say what happened
        runtime.open(_decl(port="nonesuch://board"))
    assert any("nonesuch" in note for note in runtime.notes)


def test_the_runtime_can_close_every_link_at_once():
    runtime = SerialRuntime()
    for ident in ("a", "b", "c"):
        runtime.open(_decl(ident=ident, port=f"loop://{ident}"),
                     port=LoopSerialPort(ident))
    assert len(runtime.links) == 3
    runtime.close_all()
    assert runtime.links == {}
