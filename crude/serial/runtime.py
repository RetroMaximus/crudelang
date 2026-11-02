"""The script-facing serial runtime: links, handlers, and the tick's drain.

:class:`SerialRuntime` is the object the ``SERIAL_*`` bindings talk to; it
mirrors :class:`crude.pd.runtime.PdRuntime` so the plan's "everything is a
queue drained on the interpreter thread" rule looks the same from the outside
in both places:

* :meth:`open` builds a :class:`~crude.serial.link.SerialLink` from a manifest
  declaration (or a ``SERIAL_OPEN`` argument list), tracks it by id, and
  returns it; the loader phase and the binding both go through here;
* :meth:`on_serial` registers a handler against a link (optionally only one
  *kind* - ``"frame"`` vs ``"bytes"`` vs ``"error"``) exactly like ``ON_PD``;
* :meth:`dispatch` drains every link's inbox and calls the handlers on the
  interpreter thread. A runtime that was not given a ``program`` still drains
  but only updates the counters - a script without an ``ON_SERIAL`` still
  gets its links counted.

The link's *reader* thread is separate from *dispatch*: it turns bytes into
inbox pushes; the runtime turns pushes into script calls. Keeping them split
means a headless test can call ``link.pump_once()`` + ``runtime.dispatch()``
itself, and a windowed runtime can leave both to the frame tick.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import SerialError, SerialUnavailable
from .codecs import codec_for
from .inbox import SerialEvent
from .link import SerialLink


#: A handler that keeps raising is muted after this many consecutive errors,
#  the same rule :class:`crude.pd.runtime.PdRuntime` uses for ``ON_PD``.
MAX_HANDLER_ERRORS = 3


class SerialHandler:
    """One ``ON_SERIAL`` registration."""

    def __init__(self, link_id: str, func, kinds: Tuple[str, ...] = ()):
        self.link_id = link_id
        self.func = func
        self.kinds = tuple(kinds or ())
        self.errors = 0
        self.muted = False
        self.calls = 0

    def matches(self, event: SerialEvent) -> bool:
        if self.kinds and event.kind not in self.kinds:
            return False
        return True

    def __repr__(self):
        return f"<SerialHandler {self.link_id} {self.func!r}>"


class SerialRuntime:
    """Owns the links plus everything the ``SERIAL_*`` bindings need."""

    def __init__(self, program=None, log: Optional[Callable[[str], None]] = None,
                 links: Optional[Dict[str, SerialLink]] = None):
        self.program = program
        self.log = log or (lambda message: None)
        self.links: Dict[str, SerialLink] = dict(links or {})
        self.handlers: List[SerialHandler] = []
        self.errors: List[str] = []
        self.dispatched = 0
        self.notes: List[str] = []

    # -- wiring ---------------------------------------------------------
    def attach_program(self, program) -> None:
        """Bind this runtime to the program whose functions the handlers call."""
        self.program = program

    # -- links ----------------------------------------------------------
    def open(self, decl: Any, *, port: Any = None,
             codec: Optional[str] = None, reconnect: bool = True) -> SerialLink:
        """Open one declared link (the loader phase and ``SERIAL_OPEN`` both use this).

        ``port`` overrides the URI lookup - a test injects a
        :class:`~crude.serial.fake.MemorySerialPort` here instead of naming a
        device that does not exist. ``codec`` overrides the manifest's codec
        name when a script wants to say so directly.
        """
        link_id = getattr(decl, "id", None)
        if not link_id:
            raise SerialError("a serial link needs an id to be opened by")
        existing = self.links.get(link_id)
        if existing is not None and existing.opened:
            return existing

        if codec is not None and getattr(decl, "codec", None) != codec:
            # Replace just the codec field so ``SerialLink`` picks it up below.
            decl = _WithCodec(decl, codec)

        link = SerialLink(decl, port=port, reconnect=reconnect, log=self.log)
        if port is None:
            try:
                link.open()
            except SerialError as exc:
                if getattr(decl, "optional", True):
                    self.notes.append(f"serial link '{link_id}' skipped: {exc}")
                    self.log(f"serial link '{link_id}' skipped: {exc}")
                    # keep the link registered so a later SERIALIZATION retry
                    # (or a script that hands in a port) still finds it
                    self.links[link_id] = link
                    raise
                raise
            link.start()
        else:
            # An injected port is opened but its thread is not started unless
            # the caller asks: a test wants ``pump_once``, not a real thread.
            link.open()
        self.links[link_id] = link
        return link

    def link(self, id_or_link: Any) -> SerialLink:
        """Accept a link, an id, or a wrapper that has a link at ``.target``."""
        from ..lang.values import HostHandle

        if isinstance(id_or_link, HostHandle):
            id_or_link = id_or_link.target
        if isinstance(id_or_link, SerialLink):
            return id_or_link
        held = self.links.get(str(id_or_link))
        if held is None:
            raise SerialError(
                f"there is no serial link '{id_or_link}' "
                f"(known: {', '.join(sorted(self.links)) or 'none'})"
            )
        return held

    def close(self, id_or_link: Any) -> bool:
        link = self.link(id_or_link)
        was_open = link.opened
        link.close()
        self.handlers = [h for h in self.handlers if h.link_id != link.id]
        self.links.pop(link.id, None)
        return was_open

    # -- callbacks ------------------------------------------------------
    def on_serial(self, id_or_link: Any, func, kinds=None) -> SerialHandler:
        link = self.link(id_or_link)
        handler = SerialHandler(link.id, func, tuple(kinds) if kinds else ())
        self.handlers.append(handler)
        return handler

    def off_serial(self, id_or_link: Any, func=None) -> int:
        link = self.link(id_or_link)
        before = len(self.handlers)
        self.handlers = [
            h for h in self.handlers
            if not (h.link_id == link.id and (func is None or h.func is func))
        ]
        return before - len(self.handlers)

    # -- the tick -------------------------------------------------------
    def dispatch(self) -> int:
        """Drain every link's inbox and call the handlers; how many calls fired."""
        delivered = 0
        for link in list(self.links.values()):
            events = link.drain()
            for event in events:
                for handler in list(self.handlers):
                    if handler.muted or handler.link_id != link.id:
                        continue
                    if not handler.matches(event):
                        continue
                    self._call(handler, event)
                    delivered += 1
        self.dispatched += delivered
        return delivered

    def _call(self, handler: SerialHandler, event: SerialEvent) -> None:
        handler.calls += 1
        if self.program is None:
            return
        args = [_wrap(event.value)]
        _, error = self.program.invoke(handler.func, args)
        if error is None:
            handler.errors = 0
            return
        handler.errors += 1
        message = f"ON_SERIAL {handler.link_id} failed: {error.details}"
        self.errors.append(message)
        self.log(message)
        if handler.errors >= MAX_HANDLER_ERRORS:
            handler.muted = True
            self.log(f"ON_SERIAL {handler.link_id} muted after "
                     f"{MAX_HANDLER_ERRORS} consecutive errors")

    # -- batch ----------------------------------------------------------
    def stop_all(self) -> None:
        for link in list(self.links.values()):
            link.stop()

    def close_all(self) -> None:
        for link in list(self.links.values()):
            try:
                link.close()
            except Exception as exc:  # pragma: no cover - closing is best-effort
                self.log(f"{link.id}: close failed: {exc}")
        self.links.clear()

    def __repr__(self):
        return (f"<SerialRuntime links={sorted(self.links)} "
                f"handlers={len(self.handlers)}>")


# -- helpers ------------------------------------------------------------

def _wrap(value: Any):
    """Turn a decoded Python value into a language value the handler expects."""
    from ..lang.values import List as CrudeList, Number, String

    if isinstance(value, bool):
        return Number(1 if value else 0)
    if isinstance(value, (int, float)):
        return Number(float(value))
    if isinstance(value, str):
        return String(value)
    if isinstance(value, (bytes, bytearray)):
        return String(bytes(value).decode("utf-8", errors="replace"))
    if isinstance(value, (list, tuple)):
        return CrudeList([_wrap(item) for item in value])
    return String(str(value))


class _WithCodec:
    """A ``SerialDecl`` stand-in with one field overridden, kept for ``describe``."""

    def __init__(self, decl: Any, codec: str):
        self._decl = decl
        self.id = getattr(decl, "id", "")
        self.port = getattr(decl, "port", "")
        self.baud = getattr(decl, "baud", 115200)
        self.codec = codec
        self.optional = getattr(decl, "optional", True)

    def describe(self) -> str:
        return f"{self.id} {self.port} {self.baud} {self.codec}"


__all__ = ["SerialRuntime", "SerialHandler", "MAX_HANDLER_ERRORS"]
