"""The script-facing MIDI runtime: ports, handlers, and the tick's drain.

:class:`MidiRuntime` is what the ``MIDI_*`` bindings talk to. It mirrors
:class:`crude.serial.runtime.SerialRuntime` down to the mute budget: a message
arrives on the backend's own thread, is queued, and is drained here on the
interpreter tick - a handler that keeps raising is muted after the same
``MAX_HANDLER_ERRORS`` strikes, so one broken ``ON_MIDI`` cannot spam the log
the way one broken frame handler can.

Nothing here opens a backend by itself unless asked to: the loader's
``midi.init`` phase decides whether this project wants MIDI at all (see
:func:`crude.midi.open_backend` for what "does not want it" looks like -
:class:`~crude.midi.null_backend.NullMidiBackend`, always available, never
holds a port).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import MidiError, MidiMessage, MidiUnavailable, check_direction
from .null_backend import NullMidiBackend

#: A handler that keeps raising is muted after this many consecutive errors,
#  the same rule :class:`crude.serial.runtime.SerialRuntime` uses.
MAX_HANDLER_ERRORS = 3


class MidiPort:
    """One open port: what ``MIDI_OPEN`` returns and ``ON_MIDI``/``MIDI_SEND`` take."""

    def __init__(self, runtime: "MidiRuntime", port_id: int, direction: str,
                 index: int, name: str = ""):
        self.runtime = runtime
        self.id = port_id
        self.direction = direction
        self.index = index
        self.name = name
        self.opened = True
        self.messages_in = 0
        self.bytes_out = 0

    # -- the one thing an output does -----------------------------------
    def send(self, message: Tuple[int, ...]) -> int:
        if not self.opened:
            raise MidiError(f"MIDI port {self.describe()} is closed")
        if self.direction != "output":
            raise MidiError(
                f"MIDI port {self.describe()} is an input, it cannot send"
            )
        written = self.runtime.backend.send(self.id, message)
        self.bytes_out += written
        return written

    def close(self) -> None:
        if not self.opened:
            return
        self.runtime._close_port(self)

    # -- GET()-visible properties ---------------------------------------
    def get_prop(self, name: str) -> Any:
        if name not in _PORT_PROPS:
            raise KeyError(name)
        return _PORT_PROPS[name](self)

    def props(self) -> Dict[str, Any]:
        return {key: _PORT_PROPS[key](self) for key in sorted(_PORT_PROPS)}

    def describe(self) -> str:
        label = self.name or f"port {self.index}"
        return f"{self.direction} {label} #{self.id}"

    def __repr__(self) -> str:
        return f"<MidiPort {self.describe()} opened={self.opened}>"


_PORT_PROPS = {
    "id": lambda port: float(port.id),
    "direction": lambda port: port.direction,
    "index": lambda port: float(port.index),
    "name": lambda port: port.name,
    "opened": lambda port: 1.0 if port.opened else 0.0,
    "messages": lambda port: float(port.messages_in),
    "bytes_sent": lambda port: float(port.bytes_out),
}


class MidiHandler:
    """One ``ON_MIDI`` registration against one port."""

    def __init__(self, port_id: int, func):
        self.port_id = port_id
        self.func = func
        self.errors = 0
        self.muted = False
        self.calls = 0

    def __repr__(self) -> str:
        return f"<MidiHandler port={self.port_id} {self.func!r}>"


class MidiRuntime:
    """Owns the backend plus everything the ``MIDI_*`` bindings need."""

    def __init__(self, backend: Any = None, program: Any = None,
                 log: Optional[Callable[[str], None]] = None):
        self.backend = backend if backend is not None else NullMidiBackend(log=log)
        self.program = program
        self.log = log or (lambda message: None)
        #: ``port_id -> MidiPort``, in the order they were opened
        self.ports: Dict[int, MidiPort] = {}
        self.handlers: List[MidiHandler] = []
        self.errors: List[str] = []
        self.notes: List[str] = []
        self.dispatched = 0

    # -- wiring ----------------------------------------------------------
    def attach_program(self, program: Any) -> None:
        """Bind this runtime to the program whose functions the handlers call."""
        self.program = program

    # -- devices ---------------------------------------------------------
    def devices(self, direction: str) -> List:
        """Every port for one direction; a missing library is an empty list.

        The same rule that keeps ``--no-audio`` silent rather than fatal: a
        backend that cannot enumerate (no module, no API) contributes nothing
        instead of failing the call, so the File menu shows "no MIDI devices"
        instead of crashing the frame.
        """
        check_direction(direction)
        try:
            return list(self.backend.devices(direction))
        except MidiError as exc:
            self.notes.append(f"midi: {exc}")
            self.log(f"midi: {exc}")
            return []

    # -- ports -----------------------------------------------------------
    def open(self, direction: str, index: int, name: str = "") -> MidiPort:
        """Open one port by direction and the index ``MIDI_DEVICES`` reported."""
        check_direction(direction)
        if direction == "input":
            port_id = self.backend.open_input(index, self._on_message)
        else:
            port_id = self.backend.open_output(index)
        port = MidiPort(self, port_id, direction, index, name)
        self.ports[port_id] = port
        return port

    def _close_port(self, port: MidiPort) -> None:
        self.handlers = [h for h in self.handlers if h.port_id != port.id]
        self.ports.pop(port.id, None)
        port.opened = False
        try:
            self.backend.close(port.id)
        except MidiError as exc:
            self.log(f"midi: closing {port.describe()} raised {exc}")

    def close(self, port_or_id: Any) -> bool:
        """Close one port, by handle or id; whether it was open before."""
        port = self._port(port_or_id)
        was_open = port.opened
        if was_open:
            self._close_port(port)
        return was_open

    def _port(self, port_or_id: Any) -> MidiPort:
        from ..lang.values import HostHandle

        if isinstance(port_or_id, HostHandle):
            port_or_id = port_or_id.target
        if isinstance(port_or_id, MidiPort):
            return port_or_id
        held = self.ports.get(int(port_or_id))
        if held is None:
            raise MidiError(
                f"there is no MIDI port {port_or_id} open for this project "
                f"(known: {', '.join(str(key) for key in sorted(self.ports)) or 'none'})"
            )
        return held

    def send(self, port_or_id: Any, message: Tuple[int, ...]) -> int:
        return self._port(port_or_id).send(message)

    # -- callbacks -------------------------------------------------------
    def on_midi(self, port_or_id: Any, func) -> MidiHandler:
        port = self._port(port_or_id)
        handler = MidiHandler(port.id, func)
        self.handlers.append(handler)
        return handler

    def off_midi(self, port_or_id: Any = None, func=None) -> int:
        """Drop the handlers for one port, or every handler with no port given."""
        wanted = None if port_or_id is None else self._port(port_or_id).id
        before = len(self.handlers)
        self.handlers = [
            handler for handler in self.handlers
            if not (wanted is None or handler.port_id == wanted)
            or not (func is None or handler.func is func)
        ]
        return before - len(self.handlers)

    # -- the tick --------------------------------------------------------
    def _on_message(self, message: MidiMessage) -> None:
        """Called by the backend's ``poll``, on the interpreter thread."""
        port = self.ports.get(message.port)
        if port is not None:
            port.messages_in += 1
        for handler in list(self.handlers):
            if handler.muted or handler.port_id != message.port:
                continue
            self._call(handler, message)

    def dispatch(self) -> int:
        """Drain every open input and call the handlers; how many calls fired."""
        delivered = 0
        for port in list(self.ports.values()):
            if port.direction != "input":
                continue
            before = sum(handler.calls for handler in self.handlers
                         if handler.port_id == port.id)
            try:
                self.backend.poll(port.id)
            except MidiUnavailable as exc:
                self.notes.append(f"midi: {port.describe()} went away: {exc}")
                self.log(self.notes[-1])
                continue
            after = sum(handler.calls for handler in self.handlers
                        if handler.port_id == port.id)
            delivered += after - before
        self.dispatched += delivered
        return delivered

    def _call(self, handler: MidiHandler, message: MidiMessage) -> None:
        handler.calls += 1
        if self.program is None:
            return
        args = [_wrap(message)]
        _, error = self.program.invoke(handler.func, args)
        if error is None:
            handler.errors = 0
            return
        handler.errors += 1
        details = getattr(error, "details", None) or str(error)
        text = f"ON_MIDI port {handler.port_id} failed: {details}"
        self.errors.append(text)
        self.log(text)
        if handler.errors >= MAX_HANDLER_ERRORS:
            handler.muted = True
            self.log(f"ON_MIDI port {handler.port_id} muted after "
                     f"{MAX_HANDLER_ERRORS} consecutive errors")

    # -- batch -----------------------------------------------------------
    def close_all(self) -> None:
        for port in list(self.ports.values()):
            try:
                self._close_port(port)
            except Exception as exc:  # pragma: no cover - closing is best-effort
                self.log(f"midi: {port.describe()} would not close: {exc}")
        self.handlers = []

    def __repr__(self) -> str:
        return (f"<MidiRuntime backend={getattr(self.backend, 'name', '?')} "
                f"ports={sorted(self.ports)} handlers={len(self.handlers)}>")


def _wrap(message: MidiMessage) -> Any:
    """Turn a raw packet into the language value an ``ON_MIDI`` handler is given.

    A list of the bytes, so a script can index it the way it would index any
    other list; the delta is not passed along, only the packet itself.
    """
    from ..lang.values import List as CrudeList, Number

    return CrudeList([Number(float(byte)) for byte in message.bytes])


__all__ = ["MidiRuntime", "MidiPort", "MidiHandler", "MAX_HANDLER_ERRORS"]
