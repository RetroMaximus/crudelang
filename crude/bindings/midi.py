"""``MIDI_*`` bindings: the script-facing MIDI surface.

Everything here is a thin adapter over :class:`crude.midi.runtime.MidiRuntime`;
the whole point of the layer is that a script cannot tell whether it is talking
to a real controller or to a machine with no ``python-rtmidi`` at all, and
neither can the tests. So nothing here imports ``rtmidi`` and nothing here
starts a thread - the backend does, and the runtime's per-frame ``dispatch``
does the handing over.

The verbs:

* ``MIDI_DEVICES(direction)`` -> the ports a direction has, one line each,
* ``MIDI_OPEN(direction, index)`` -> a port handle,
* ``MIDI_SEND(port, bytes)`` -> the bytes the port accepted,
* ``MIDI_CLOSE(port)`` -> whether it was open,
* ``ON_MIDI(port, fn)`` -> a handler handle (so ``OFF_MIDI`` can reach it).

A port handle is ``GET``-able (see :data:`crude.midi.runtime._PORT_PROPS`); it
is deliberately not writable, the same rule an audio stream uses - a port's
direction and index are decided when it is opened.
"""

from __future__ import annotations

from ..lang.values import HostHandle, List, Number, String
from ..midi.base import DIRECTIONS, MidiError, check_direction
from .registry import binding

AREA = "midi"

#: what ``GET``/``SET`` see on a MIDI port handle
MIDI_KIND = "midi"


# -- helpers ------------------------------------------------------------


def _runtime(ctx):
    return ctx.subsystem("midi", "midi")


def _fail(ctx, exc) -> None:
    raise ctx.fail(f"{ctx.name}: {exc}")


def _handle(port) -> HostHandle:
    return HostHandle(port, kind=MIDI_KIND, label=port.describe())


def _port(ctx, arg_name="port"):
    value = ctx.get(arg_name)
    if not isinstance(value, HostHandle) or value.kind != MIDI_KIND:
        ctx.bad_type(arg_name, "MIDI port", value)
    return value.target


def _bytes(ctx, arg_name="data"):
    value = ctx.get(arg_name)
    if isinstance(value, Number):
        return (int(value.value),)
    if isinstance(value, String):
        return tuple(byte for byte in value.value.encode("utf-8"))
    if isinstance(value, List):
        packet = []
        for element in value.elements:
            if not isinstance(element, Number):
                ctx.bad_type(arg_name, "list of numbers", element)
            packet.append(int(element.value))
        return tuple(packet)
    ctx.bad_type(arg_name, "list of numbers", value)


# -- the ports ----------------------------------------------------------


@binding("MIDI_DEVICES", ["direction"], area=AREA, optional=1)
def midi_devices(ctx):
    """Every port this machine has, for one direction (``input``/``output``).

    With no direction both are listed. A machine with no MIDI library says so
    with an empty list, never an error - the File menu's MIDI dialog turns
    that into one disabled "no MIDI devices" row rather than a crash.
    """
    runtime = _runtime(ctx)
    if ctx.has("direction"):
        direction = ctx.as_string("direction")
        try:
            check_direction(direction)
        except MidiError as exc:
            _fail(ctx, exc)
        ways = (direction,)
    else:
        ways = DIRECTIONS

    found = []
    for way in ways:
        found.extend(runtime.devices(way))
    return List([String(device.describe()) for device in found])


@binding("MIDI_OPEN", ["direction", "index"], area=AREA)
def midi_open(ctx):
    """Open one port by direction and the index ``MIDI_DEVICES`` reported."""
    runtime = _runtime(ctx)
    direction = ctx.as_string("direction")
    try:
        check_direction(direction)
    except MidiError as exc:
        _fail(ctx, exc)
    index = ctx.as_int("index")
    try:
        port = runtime.open(direction, index)
    except MidiError as exc:
        _fail(ctx, exc)
    return _handle(port)


@binding("MIDI_SEND", ["port", "data"], area=AREA)
def midi_send(ctx):
    """Write one packet - a list of MIDI bytes - to an open output port."""
    port = _port(ctx)
    message = _bytes(ctx, "data")
    try:
        written = port.send(message)
    except MidiError as exc:
        _fail(ctx, exc)
    return Number(float(written))


@binding("MIDI_CLOSE", ["port"], area=AREA)
def midi_close(ctx):
    """Close one port; whether it was open before."""
    runtime = _runtime(ctx)
    port = _port(ctx)
    try:
        was_open = runtime.close(port)
    except MidiError as exc:
        _fail(ctx, exc)
    return Number(1 if was_open else 0)


# -- callbacks ----------------------------------------------------------


@binding("ON_MIDI", ["port", "fn"], area=AREA)
def on_midi(ctx):
    """Call ``fn`` with every message that arrives on ``port``."""
    runtime = _runtime(ctx)
    port = _port(ctx)
    func = ctx.as_function("fn")
    handler = runtime.on_midi(port, func)
    return HostHandle(handler, kind="midi_handler", label=port.describe())


@binding("OFF_MIDI", ["port", "fn"], area=AREA, optional=1)
def off_midi(ctx):
    """Remove the handler(s) for ``port``; with ``fn`` only that one."""
    runtime = _runtime(ctx)
    port = _port(ctx) if ctx.has("port") else None
    func = ctx.as_function("fn") if ctx.has("fn") else None
    return Number(float(runtime.off_midi(port, func)))


#: Names this module registers, used by the binding-help generator and tests.
BINDINGS = (
    "MIDI_DEVICES", "MIDI_OPEN", "MIDI_SEND", "MIDI_CLOSE",
    "ON_MIDI", "OFF_MIDI",
)
