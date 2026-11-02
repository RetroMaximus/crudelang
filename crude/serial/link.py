"""The one object a script holds onto: a port, a codec, a reader thread, an inbox.

A :class:`SerialLink` is what ``SERIAL_OPEN`` returns and what
``SERIAL_WRITE``/``SERIAL_CLOSE``/``ON_SERIAL`` take. It owns:

* the port (from :func:`crude.serial.base.open_port`, or handed in directly by
  a test - a duck-typed port is fine as long as it speaks the protocol),
* the codec that decides what a *frame* is,
* the reader thread that turns ``port.read`` into inbox pushes,
* a :class:`~crude.serial.inbox.SerialInbox` the runtime drains once per tick,
* the reconnect state machine the plan asks for (exponential backoff, capped,
  with a maximum attempt count so a permanently dead device stops spinning).

Two execution paths matter for tests:

* **threaded** - :meth:`start` spawns the daemon reader; :meth:`stop` signals
  the ``stop_event``, joins with a short timeout, and closes the port;
* **manual** - :meth:`pump_once` does one non-blocking read + codec feed +
  inbox push, and :meth:`drain` empties the inbox. A test calls those directly
  and never needs ``sleep`` or a timing margin, which is what the plan's "no
  sleep, no network" test rule asks for.

The reader thread calls :meth:`pump_once` on a fixed interval, so the same
code path runs with or without a thread; a test that never calls ``start`` is
just choosing not to run that loop.

Reconnect is deliberately *not* automatic on a manual path: only the thread
reopens a dropped port, so :meth:`pump_once` failing on a closed port raises
and the caller knows they lost the device.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, List, Optional

from .base import SerialError, SerialUnavailable, check_port, open_port
from .codecs import Codec, codec_for
from .inbox import SerialEvent, SerialInbox


#: the plan's sizing for S10 backoff: short enough for a USB rescan, capped so
#  a permanently unplugged device does not hammer the OS.
DEFAULT_BACKOFF_BASE = 0.1
DEFAULT_BACKOFF_MAX = 5.0
DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_POLL_INTERVAL = 0.005
DEFAULT_READ_SIZE = 1024


class SerialLink:
    """One declared serial link: the port, the codec, the reader, the inbox."""

    def __init__(self, decl: Any, *, port: Any = None,
                 codec: Optional[Codec] = None,
                 inbox: Optional[SerialInbox] = None,
                 log: Optional[Callable[[str], None]] = None,
                 backoff_base: float = DEFAULT_BACKOFF_BASE,
                 backoff_max: float = DEFAULT_BACKOFF_MAX,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 poll_interval: float = DEFAULT_POLL_INTERVAL,
                 read_size: int = DEFAULT_READ_SIZE,
                 reconnect: bool = True):
        #: the manifest's :class:`~crude.project.manifest.SerialDecl`, or a
        #  stand-in with the same fields (``id``, ``port``, ``baud``, ``codec``,
        #  ``optional``); a link does not require the dataclass
        self.decl = decl
        self.id = getattr(decl, "id", None) or "link"
        self.uri = getattr(decl, "port", "") or ""
        self.baud = int(getattr(decl, "baud", 115200) or 115200)
        self.codec_name = getattr(decl, "codec", "line") or "line"
        self.optional = bool(getattr(decl, "optional", True))

        #: a caller-provided port (a test, an injected fake); when given, the
        #  URI is not opened - the caller is promising this *is* the device
        self._port = port
        self._owns_port = port is None
        self.codec = codec if codec is not None else codec_for(self.codec_name)
        self.inbox = inbox if inbox is not None else SerialInbox()

        self.log: Callable[[str], None] = log or (lambda message: None)
        self.backoff_base = float(backoff_base)
        self.backoff_max = float(backoff_max)
        self.max_attempts = int(max_attempts)
        self.poll_interval = float(poll_interval)
        self.read_size = int(read_size)
        self.reconnect = bool(reconnect)

        # -- state read by tests and the runtime ------------------------
        self.opened: bool = False
        self.closed: bool = False
        self.errors: List[str] = []
        self.reconnects: int = 0
        self.attempts: int = 0
        self.bytes_read: int = 0
        self.bytes_written: int = 0
        self.frames_read: int = 0

        # -- threading --------------------------------------------------
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

    # -- the port -------------------------------------------------------
    @property
    def port(self) -> Any:
        return self._port

    def describe(self) -> str:
        return (f"{self.id} {self.uri or 'injected'} "
                f"{self.baud} {self.codec_name}")

    def __repr__(self):
        return f"<SerialLink {self.describe()} open={self.opened}>"

    # -- lifecycle ------------------------------------------------------
    def open(self) -> Any:
        """Open the port (unless one was injected) and mark the link live.

        Idempotent: calling ``open`` on an already-open link returns the port
        without re-opening, so a test that hands in a port and the runtime
        that also calls ``open`` see the same object.
        """
        with self._lock:
            if self.opened and self._port is not None:
                return self._port
            if self._port is None:
                self._port = open_port(self.uri, self.baud)
                self._owns_port = True
            check_port(self._port)
            if not getattr(self._port, "is_open", False):
                self._port.open()
            self.opened = True
            self.closed = False
            self.attempts = 0
            return self._port

    def close(self) -> None:
        """Stop the reader thread and close the port; safe to call twice."""
        self.stop()
        with self._lock:
            self.opened = False
            self.closed = True
            port, self._port = self._port, None
        # Only a port we opened is ours to close; an injected one belongs to
        # whoever gave it (the test usually closes it themselves).
        if port is not None and self._owns_port:
            try:
                port.close()
            except Exception as exc:  # pragma: no cover - a device that fights back
                self.log(f"{self.id}: close raised {exc!r}")

    # -- the writer -----------------------------------------------------
    def write(self, data) -> int:
        """Send raw bytes, or let the codec add framing for a script value."""
        if not self.opened or self._port is None:
            raise SerialError(f"link '{self.id}' is not open; call SERIAL_OPEN first")
        payload = data if isinstance(data, (bytes, bytearray)) else self.codec.encode(data)
        with self._lock:
            written = int(self._port.write(payload))
        self.bytes_written += written
        return written

    # -- the reader (thread body is _loop) ------------------------------
    def pump_once(self) -> List[Any]:
        """One non-blocking read + a codec feed; the frames this call produced.

        Pushes each frame onto the inbox and returns them for a caller that
        wants to look without draining (a test that also calls
        :meth:`drain` right after gets the same list back).
        """
        port = self._port
        if port is None or not self.opened:
            return []
        try:
            waiting = int(port.in_waiting())
        except Exception:
            waiting = 0
        if waiting <= 0:
            return []
        size = min(waiting, self.read_size)
        chunk = port.read(size)
        if not chunk:
            return []
        self.bytes_read += len(chunk)
        frames = self.codec.feed(chunk)
        if not frames:
            return []
        for frame in frames:
            self.inbox.push(_event_for(frame, chunk))
            self.frames_read += 1
        return list(frames)

    def drain(self) -> List[SerialEvent]:
        """Everything that arrived since the last call, oldest first."""
        return self.inbox.drain()

    def start(self) -> threading.Thread:
        """Begin the reader thread; a no-op if one is already running."""
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name=f"crude-serial-{self.id}", daemon=True,
        )
        self._thread.start()
        return self._thread

    def stop(self, timeout: float = None) -> bool:
        """Signal the reader to stop; whether it actually joined.

        A timeout is deliberately short: the point of the plan's shutdown rule
        is that closing never hangs the process, so a thread that will not
        come back within one poll interval is abandoned (it is a daemon).
        """
        self._stop.set()
        held = self._thread
        if held is None:
            return False
        try:
            held.join(timeout if timeout is not None else max(self.poll_interval * 4, 0.05))
        except RuntimeError:
            # joining a thread that never started is fine, we just did not need to
            return False
        alive = held.is_alive()
        if not alive:
            self._thread = None
        return not alive

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.opened:
                    self.open()
                self.pump_once()
            except SerialError as exc:
                self._handle_failure(exc)
            except Exception as exc:  # pragma: no cover - a port that throws wide
                self._handle_failure(SerialError(f"unexpected: {exc!r}"))
            self._stop.wait(self.poll_interval)

    def _handle_failure(self, error: Exception) -> None:
        """Log the drop, close the port, then decide whether to try again.

        The plan's reconnect/backoff line: exponential up to a ceiling, then
        the link gives up so a permanently missing device does not spin.
        """
        message = f"{self.id}: {error}"
        self.errors.append(message)
        self.log(message)
        # Close the broken port so a reopen gets a fresh one.
        if self._port is not None and self._owns_port:
            try:
                self._port.close()
            except Exception:  # pragma: no cover
                pass
            self._port = None
        self.opened = False
        if not self.reconnect:
            self._stop.set()
            return
        self.attempts += 1
        if self.attempts > self.max_attempts:
            self.log(f"{self.id}: giving up after {self.max_attempts} attempts")
            self._stop.set()
            return
        delay = self.backoff_for(self.attempts)
        self.reconnects += 1
        # the reader thread sleeps with the stop event so shutdown is immediate
        if not self._stop.wait(delay):
            return

    def backoff_for(self, attempt: int) -> float:
        """The plan's exponential-with-cap, in one place so a test can pin it."""
        if attempt <= 0:
            return 0.0
        seconds = self.backoff_base * (2 ** (attempt - 1))
        return min(seconds, self.backoff_max)

    # -- GET()-visible properties --------------------------------------
    def get(self, name: str) -> Any:
        """One property from the link; used by ``GET(link, "prop")``."""
        try:
            return _LINK_PROPS[name](self)
        except KeyError:
            raise SerialError(
                f"link '{self.id}' has no property '{name}' "
                f"(known: {', '.join(sorted(_LINK_PROPS))})"
            )

    def get_prop(self, name: str) -> Any:
        """The duck-typed path :mod:`crude.bindings.core` uses for ``GET``.

        Same table as :meth:`get`, but a miss raises ``KeyError`` so ``GET``
        answers with its own "no property '<name>'" line rather than leaking a
        serial-specific message into a script error that should look like every
        other handle's.
        """
        if name not in _LINK_PROPS:
            raise KeyError(name)
        return _LINK_PROPS[name](self)

    def props(self) -> dict:
        return {key: _LINK_PROPS[key](self) for key in sorted(_LINK_PROPS)}


# -- the GET table ------------------------------------------------------

def _frames(link: SerialLink) -> float:
    return float(link.frames_read)


def _bytes_read(link: SerialLink) -> float:
    return float(link.bytes_read)


def _bytes_written(link: SerialLink) -> float:
    return float(link.bytes_written)


def _pending(link: SerialLink) -> float:
    return float(link.inbox.pending)


def _dropped(link: SerialLink) -> float:
    return float(link.inbox.dropped)


def _reconnects(link: SerialLink) -> float:
    return float(link.reconnects)


def _opened(link: SerialLink) -> float:
    return 1.0 if link.opened else 0.0


#: Every property ``GET(link, "...")`` reads. Values are floats so the
#  language sees a ``Number``; the strings are exposed as their own props too.
_LINK_PROPS = {
    "id": lambda link: link.id,
    "port": lambda link: link.uri,
    "baud": lambda link: float(link.baud),
    "codec": lambda link: link.codec_name,
    "opened": _opened,
    "frames": _frames,
    "bytes_read": _bytes_read,
    "bytes_written": _bytes_written,
    "pending": _pending,
    "dropped": _dropped,
    "reconnects": _reconnects,
}


def _event_for(frame: Any, raw: bytes) -> SerialEvent:
    """One inbox entry for one decoded frame; the raw bytes ride along."""
    if isinstance(frame, (bytes, bytearray)):
        return SerialEvent(kind="bytes", value=bytes(frame), raw=bytes(frame))
    return SerialEvent(kind="frame", value=frame, raw=raw)


__all__ = [
    "SerialLink",
    "DEFAULT_BACKOFF_BASE",
    "DEFAULT_BACKOFF_MAX",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_READ_SIZE",
    "SerialError",
    "SerialUnavailable",
]
