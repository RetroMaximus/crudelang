"""The bounded queue that carries serial frames to the interpreter thread.

Mirrors :mod:`crude.pd.inbox` on purpose: the reader thread does one thing -
copy a decoded frame into a :class:`SerialEvent` and append it - and the main
loop drains the inbox once per frame. Under pressure the oldest event is
dropped, not the newest, because a stale byte from ten seconds ago is worth
less than the one that just arrived.

An event carries three fields:

* ``kind`` - ``"frame"`` for a codec-produced value, ``"bytes"`` for a raw
  chunk, ``"error"`` for a reconnect notice the runtime may want to log;
* ``value`` - the decoded frame (whatever the codec produced; ``bytes`` for
  the raw codec);
* ``raw`` - the bytes this frame was decoded from, so a script that wants to
  log a packet can see it exactly (kept small; a raw codec event's ``raw`` and
  ``value`` are the same object).
"""

from __future__ import annotations

from collections import deque
from typing import Any, Deque, List, NamedTuple, Optional


class SerialEvent(NamedTuple):
    """One decoded frame off the wire, ready for ``ON_SERIAL``."""

    kind: str = "frame"
    value: Any = None
    raw: Optional[bytes] = None

    def __repr__(self):
        shown = self.value if self.kind != "bytes" else f"<{len(self.value or b'')} bytes>"
        return f"<serial {self.kind} {shown!r}>"


class SerialInbox:
    """Drop-oldest bounded queue of :class:`SerialEvent`."""

    def __init__(self, capacity: int = 4096):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._events: Deque[SerialEvent] = deque()
        self.dropped = 0
        self.total = 0

    def push(self, event: SerialEvent) -> bool:
        """Append ``event``; ``False`` means the oldest was dropped."""
        self.total += 1
        self._events.append(event)
        if len(self._events) > self.capacity:
            self._events.popleft()
            self.dropped += 1
            return False
        return True

    def extend(self, events) -> None:
        for event in events:
            self.push(event)

    def drain(self) -> List[SerialEvent]:
        events = list(self._events)
        self._events.clear()
        return events

    def peek(self, limit: Optional[int] = None) -> List[SerialEvent]:
        if limit is None:
            return list(self._events)
        return list(self._events)[:limit]

    def clear(self) -> None:
        self._events.clear()

    @property
    def pending(self) -> int:
        return len(self._events)

    def __len__(self):
        return len(self._events)

    def __repr__(self):
        return f"<SerialInbox {len(self)}/{self.capacity} dropped={self.dropped}>"


__all__ = ["SerialEvent", "SerialInbox"]
