"""Time for a running project: what ``TIME``, ``SLEEP`` and ``FPS`` answer to.

A script should not be able to tell a real second from a second a test handed it,
so the clock is one small class with one fake over it rather than ``time`` calls
spread through the bindings. Keeping it out of :mod:`crude.project` also keeps it
importable by anything that needs to know how long something took - the loader,
the loop, a widget animating a value - without dragging the manifest along.

The frame counter belongs here too, for the same reason: ``FPS`` has to say
something on a machine where the loop that would measure it does not exist yet
(stage 11 adds that loop), and a clock that counts frames it was told about can
already answer honestly.

``runtime`` and ``modes`` join this package when the loop is built; ``inbox``
already exists under :mod:`crude.pd`, where the thing that fills it lives.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

#: the frame rate a project gets when neither ``--fps`` nor the manifest says
DEFAULT_FPS = 60


class Clock:
    """Elapsed time, a frame count, and the pacing a loop asks for.

    Everything is measured *from the moment this clock was made*, so a script's
    ``TIME()`` is a duration and never a wall-calendar number a test would have to
    guess. The two things a real clock borrows from the process - where the seconds
    come from and how waiting is done - are both injectable, which is how
    :class:`FakeClock` below can be the same class with different answers.
    """

    #: a live clock measures the machine, so what ``fps()`` says is observed
    live = True

    def __init__(self, *, target_fps: int = DEFAULT_FPS,
                 source: Optional[Callable[[], float]] = None,
                 wait: Optional[Callable[[float], None]] = None) -> None:
        self.target_fps = int(target_fps)
        self._source = source if source is not None else time.monotonic
        self._wait = wait if wait is not None else time.sleep
        self._origin = self._source()
        #: frames marked by :meth:`frame`
        self.frames = 0
        #: total seconds this clock has been asked to hold
        self.slept = 0.0

    def now(self) -> float:
        """Seconds since this clock started."""
        return self._source() - self._origin

    def sleep(self, seconds: float) -> float:
        """Hold for ``seconds``, and say how long was actually held.

        A negative delay is clamped here because a clock must never run backwards;
        a *script* asking for one is refused by the ``SLEEP`` binding first, so the
        clamp stays a safety net for host callers rather than a silent forgiveness.
        """
        delay = max(0.0, float(seconds))
        self.slept += delay
        self._wait(delay)
        return delay

    def frame(self) -> int:
        """Mark one frame done; returns how many this clock has counted."""
        self.frames += 1
        return self.frames

    def fps(self) -> float:
        """Frames per second so far - ``0`` before there is a span to measure."""
        elapsed = self.now()
        if not self.frames or elapsed <= 0:
            return 0.0
        return self.frames / elapsed

    def __str__(self) -> str:
        return (f"{'Clock' if self.live else 'FakeClock'} {self.frames} frame(s) in "
                f"{self.now():.3f}s, aiming at {self.target_fps} fps")


class FakeClock(Clock):
    """A clock a test drives: the same answers, and nothing ever waits.

    ``frame()`` moves time on by exactly one frame at the rate the clock aims at,
    so a test can say "run ten frames" and assert on the time that passed without
    a single real millisecond going by. That is the whole reason the frame counter
    lives on the clock: it makes virtual time advanceable instead of guessable.
    """

    live = False

    def __init__(self, *, target_fps: int = DEFAULT_FPS, start: float = 0.0) -> None:
        super().__init__(target_fps=target_fps, source=lambda: 0.0,
                         wait=self._hold)
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def _hold(self, delay: float) -> None:
        """``time.sleep`` replaced: the delay is added to virtual time instead."""
        self._now += delay

    def advance(self, seconds: float) -> float:
        """Move time on without a frame and without holding anything."""
        step = max(0.0, float(seconds))
        self._now += step
        return step

    def frame(self) -> int:
        """One frame at the aimed rate: the count, and the time it would have taken."""
        self.frames += 1
        if self.target_fps > 0:
            self._now += 1.0 / self.target_fps
        return self.frames
