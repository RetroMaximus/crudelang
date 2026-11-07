"""The port a test drives: in-memory bytes on both sides, no threads, no OS.

:class:`MemorySerialPort` is the plan's named fake (test strategy: "``FakeLibpd``
... ``MemorySerialPort``"). It behaves like a device that is always there: a
write goes into a ``tx`` buffer the test can inspect, and anything the test
pushes into ``rx`` is what the link's reader will find next. It never blocks,
never sleeps, and raises :class:`SerialError` only when told to - which is what
lets one test cover a *reconnect* without a real cable.

Two ports can be *paired* with :func:`pair`, which is how a bridge test says
"the script wrote here and a fake device answered there" without any socket,
thread or timing at all: :meth:`MemorySerialPort.pair` gives a peer whose
``rx`` is this port's ``tx`` and whose ``tx`` is this port's ``rx``.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, List, Optional

from .base import SerialError, SerialUnavailable


class MemorySerialPort:
    """A duck-typed port backed by two queues the test can read and write."""

    #: every instance is "the null serial": a name that says what it is in a report
    name = "memory"

    def __init__(self, label: str = "", rx: bytes = b"", *,
                 fail_open: bool = False, fail_read: bool = False):
        self.label = label or "memory"
        #: bytes the link will read next (what the other end "sent")
        self.rx: Deque[int] = deque(rx)
        #: bytes the link has written (what the other end "heard")
        self.tx: Deque[int] = deque()
        #: lifecycle flags - the port is closed until ``open()`` is called
        self.opened = False
        self.closed_times = 0
        self.open_times = 0
        #: test switches so a link can be shown to recover from a bad port
        self._fail_open = bool(fail_open)
        self._fail_read = bool(fail_read)
        #: the peer a :func:`pair` set up, or ``None``
        self.peer: Optional["MemorySerialPort"] = None

    # -- the port protocol ---------------------------------------------
    @property
    def is_open(self) -> bool:
        return self.opened

    def open(self) -> None:
        if self._fail_open:
            raise SerialUnavailable(f"{self.label}: the port refuses to open")
        self.opened = True
        self.open_times += 1

    def close(self) -> None:
        self.opened = False
        self.closed_times += 1

    def write(self, data) -> int:
        if not self.opened:
            raise SerialError(f"{self.label}: write on a closed port")
        payload = _as_bytes(data)
        self.tx.extend(payload)
        # A paired port's write is the peer's read; unpaired writes just land in
        # ``tx`` for the test to inspect.
        if self.peer is not None:
            self.peer.rx.extend(payload)
        return len(payload)

    def read(self, size: int = 1) -> bytes:
        if not self.opened:
            raise SerialError(f"{self.label}: read on a closed port")
        if self._fail_read:
            raise SerialError(f"{self.label}: the device stopped answering")
        size = max(int(size), 0)
        taken: List[int] = []
        while len(taken) < size and self.rx:
            taken.append(self.rx.popleft())
        return bytes(taken)

    def in_waiting(self) -> int:
        return len(self.rx)

    # -- test helpers ---------------------------------------------------
    def push_rx(self, data) -> int:
        """Say the other end sent ``data``: queue it for the next read."""
        payload = _as_bytes(data)
        self.rx.extend(payload)
        return len(payload)

    def take_tx(self) -> bytes:
        """The bytes written so far, and clear them - what a test asserts on."""
        out = bytes(self.tx)
        self.tx.clear()
        return out

    def peek_tx(self) -> bytes:
        return bytes(self.tx)

    def pair(self, other: "MemorySerialPort") -> "MemorySerialPort":
        """Give this port a peer whose tx/rx are swapped with ours."""
        self.peer = other
        other.peer = self
        return other

    def describe(self) -> str:
        return f"memory:{self.label}"

    def __repr__(self):
        return (f"<MemorySerialPort {self.label} open={self.opened} "
                f"rx={len(self.rx)} tx={len(self.tx)}>")


def pair(label: str = "pair", rx: bytes = b"") -> "tuple[MemorySerialPort, MemorySerialPort]":
    """Two ports already wired to each other; the ``rx`` starts on the first."""
    a = MemorySerialPort(f"{label}+a", rx=rx)
    b = MemorySerialPort(f"{label}+b")
    a.pair(b)
    return a, b


def _as_bytes(data) -> bytes:
    """Accept the shapes a script hands in: ``bytes``, ``str``, or a list of ints."""
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, str):
        return data.encode("utf-8")
    if isinstance(data, (list, tuple)):
        return bytes(int(b) & 0xFF for b in data)
    raise SerialError(f"a serial write wants bytes or a string, got {type(data).__name__}")


__all__ = ["MemorySerialPort", "pair"]
