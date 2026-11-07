"""The audio-thread boundary: the bounded inbox and the hook bridge.

Both units exist for one reason - an audio callback may only copy and enqueue -
so the tests pin the copy semantics, the drop-oldest policy and the print line
assembler rather than anything about sound.
"""

from __future__ import annotations

import pytest

from crude.pd import FakeLibpd, HookBridge, PdEvent, PdInbox, PrintAssembler


# -- inbox --------------------------------------------------------------


def test_inbox_is_fifo():
    inbox = PdInbox()
    inbox.push(PdEvent("float", "a", (1.0,)))
    inbox.push(PdEvent("bang", "b"))

    assert inbox.drain() == [PdEvent("float", "a", (1.0,)), PdEvent("bang", "b")]
    assert inbox.drain() == []


def test_inbox_drops_the_oldest_event_under_pressure():
    inbox = PdInbox(capacity=3)
    for index in range(5):
        inbox.push(PdEvent("float", "x", (float(index),)))

    assert inbox.dropped == 2
    assert inbox.total == 5
    assert [event.args[0] for event in inbox.drain()] == [2.0, 3.0, 4.0]


def test_inbox_push_reports_the_drop():
    inbox = PdInbox(capacity=1)
    assert inbox.push(PdEvent("bang", "a")) is True
    assert inbox.push(PdEvent("bang", "b")) is False
    assert inbox.pending == 1


def test_inbox_peek_and_clear_do_not_consume():
    inbox = PdInbox()
    inbox.push(PdEvent("symbol", "s", ("hi",)))

    assert inbox.peek() == [PdEvent("symbol", "s", ("hi",))]
    assert inbox.peek(1)[0].recv == "s"
    assert inbox.pending == 1

    inbox.clear()
    assert len(inbox) == 0


def test_inbox_rejects_a_useless_capacity():
    with pytest.raises(ValueError):
        PdInbox(capacity=0)


# -- print assembler ----------------------------------------------------


def test_print_fragments_become_lines():
    assembler = PrintAssembler()
    assert assembler.feed("hel") == []
    assert assembler.feed("lo\nwor") == ["hello"]
    assert assembler.pending == "wor"
    assert assembler.feed("ld\n") == ["world"]
    assert assembler.pending == ""


def test_print_assembler_handles_several_lines_in_one_fragment():
    assembler = PrintAssembler()
    assert assembler.feed("a\nb\n") == ["a", "b"]
    assert assembler.pending == ""


def test_print_assembler_flush_emits_a_partial_line():
    assembler = PrintAssembler()
    assembler.feed("no newline")
    assert assembler.flush() == ["no newline"]
    assert assembler.flush() == []


def test_print_assembler_refuses_to_grow_forever():
    assembler = PrintAssembler(max_length=8)
    assembler.feed("0123456789")
    assert assembler.pending == "23456789"
    assert assembler.pending.__len__() == 8


# -- hook bridge --------------------------------------------------------


def test_bridge_installs_and_removes_every_hook():
    lib = FakeLibpd()
    bridge = HookBridge(lib)

    bridge.attach()
    assert bridge.installed is True
    assert all(lib.has_hook(kind) for kind in bridge._callbacks)

    bridge.detach()
    assert bridge.installed is False
    assert not any(lib.has_hook(kind) for kind in ("print", "float", "bang"))


def test_bridge_copies_hook_arguments_into_the_inbox():
    lib = FakeLibpd()
    bridge = HookBridge(lib)
    bridge.attach()

    lib.emit("bang", "tick")
    lib.emit("float", "freq", 440.0)
    lib.emit("symbol", "note", "c4")

    assert bridge.inbox.drain() == [
        PdEvent("bang", "tick"),
        PdEvent("float", "freq", (440.0,)),
        PdEvent("symbol", "note", ("c4",)),
    ]


def test_bridge_decodes_bytes_immediately():
    """Hook strings are transient; the bridge must decode inside the callback."""
    lib = FakeLibpd()
    bridge = HookBridge(lib)
    raw = bytearray(b"freq")

    bridge._on_bang(raw)
    (event,) = bridge.inbox.drain()
    raw.extend(b"junk")

    assert event.recv == "freq"


def test_bridge_assembles_print_fragments():
    lib = FakeLibpd()
    bridge = HookBridge(lib)
    bridge.attach()

    lib.emit("print", "hel")
    lib.emit("print", "lo\n")

    assert bridge.inbox.drain() == [PdEvent("print", "hello")]


def test_bridge_reads_list_atoms_through_the_library():
    lib = FakeLibpd()
    bridge = HookBridge(lib)
    bridge.attach()

    lib.emit("list", "seq", 1.0, 2.0)
    lib.emit("message", "seq", "set", 3.0)

    assert bridge.inbox.drain() == [
        PdEvent("list", "seq", (1.0, 2.0)),
        PdEvent("message", "seq", ("set", 3.0)),
    ]


def test_bridge_survives_a_broken_atom_read():
    lib = FakeLibpd()

    def explode(atoms, argc):
        raise RuntimeError("bad atom")

    lib.atom_read = explode
    bridge = HookBridge(lib)
    bridge._on_list(b"seq", 1, None)

    assert bridge.inbox.drain() == [PdEvent("list", "seq", ())]


def test_bridge_routes_midi_hooks():
    lib = FakeLibpd()
    bridge = HookBridge(lib)
    bridge.attach()

    lib.emit("noteon", 0, 60, 100)
    lib.emit("controlchange", 1, 7, 42)

    kinds = [event.kind for event in bridge.inbox.drain()]
    assert kinds == ["noteon", "controlchange"]
