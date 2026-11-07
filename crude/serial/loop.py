"""The ``loop://`` port: what the script writes is what the script reads.

The plan lists ``loop://`` as one of S10's two acceptance fakes (the other
being :class:`~crude.serial.fake.MemorySerialPort`). A loop port is a device
that always answers with exactly what it was told, so a bridge example can
demonstrate the whole *serial* half of a project without pyserial, a real
Arduino, or a second process - and an ``ON_SERIAL`` handler can be shown to
fire on what ``SERIAL_WRITE`` just put on the wire.

The wire is a single ``deque``: :meth:`write` appends to it and :meth:`read`
pops from the same one. There is no separate ``tx`` because the point of the
loop is that the two sides are the same side.
"""

from __future__ import annotations

from collections import deque
from typing import Deque

from .base import SerialError
from .fake import _as_bytes


class LoopSerialPort:
    """An echo port: a write is a queued read, in order."""

    name = "loop"

    def __init__(self, label: str = "loop"):
        self.label = label or "loop"
        self._buffer: Deque[int] = deque()
        self.opened = False
        self.open_times = 0
        self.closed_times = 0

    # -- protocol -------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self.opened

    def open(self) -> None:
        self.opened = True
        self.open_times += 1

    def close(self) -> None:
        self.opened = False
        self.closed_times += 1

    def write(self, data) -> int:
        if not self.opened:
            raise SerialError(f"loop:{self.label}: write on a closed port")
        payload = _as_bytes(data)
        self._buffer.extend(payload)
        return len(payload)

    def read(self, size: int = 1) -> bytes:
        if not self.opened:
            raise SerialError(f"loop:{self.label}: read on a closed port")
        size = max(int(size), 0)
        out = []
        while len(out) < size and self._buffer:
            out.append(self._buffer.popleft())
        return bytes(out)

    def in_waiting(self) -> int:
        return len(self._buffer)

    # -- test-facing helpers -------------------------------------------
    def push(self, data) -> int:
        """Same as :meth:`write`, without the "must be open" rule.

        A test that seeds a loop with bytes before a link has opened it still
        wants those bytes to come back on the first read; ``push`` is that.
        """
        payload = _as_bytes(data)
        self._buffer.extend(payload)
        return len(payload)

    def drain(self) -> bytes:
        """Read everything waiting, and clear the buffer."""
        out = bytes(self._buffer)
        self._buffer.clear()
        return out

    def describe(self) -> str:
        return f"loop://{self.label}"

    def __repr__(self):
        return (f"<LoopSerialPort {self.label} open={self.opened} "
                f"waiting={self.in_waiting()}>")


def _factory(rest: str, baud: int) -> LoopSerialPort:
    """Register-against-the-scheme factory: ``loop://name`` -> a loop named ``name``."""
    return LoopSerialPort(rest or "loop")


def register() -> None:
    """Add ``loop://`` to the port registry; idempotent."""
    from .base import register_port

    register_port("loop", _factory)


__all__ = ["LoopSerialPort", "register"]
