"""The bounded queue that carries Pd callbacks to the interpreter thread.

Hooks run on the audio thread, so they do exactly one thing: copy whatever
they were given into an event and append it to a :class:`PdInbox`. The main
loop drains the inbox once per frame. Dropping the oldest event under pressure
is deliberate - the audio thread must never block or allocate unboundedly.
"""

from __future__ import annotations

from collections import deque
from typing import List, NamedTuple, Optional, Tuple


class PdEvent(NamedTuple):
    """One thing that happened inside Pd."""

    kind: str
    recv: str = ""
    args: Tuple = ()

    def __repr__(self):
        return f"<pd {self.kind} {self.recv!r} {self.args}>"


class PdInbox:
    """Drop-oldest bounded queue of :class:`PdEvent`."""

    def __init__(self, capacity: int = 4096):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._events = deque()
        self.dropped = 0
        self.total = 0

    def push(self, event: PdEvent) -> bool:
        """Append ``event``; returns ``False`` when the oldest one was dropped."""
        self.total += 1
        self._events.append(event)
        if len(self._events) > self.capacity:
            self._events.popleft()
            self.dropped += 1
            return False
        return True

    def drain(self) -> List[PdEvent]:
        events = list(self._events)
        self._events.clear()
        return events

    def peek(self, limit: Optional[int] = None) -> List[PdEvent]:
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
        return f"<PdInbox {len(self)}/{self.capacity} dropped={self.dropped}>"
