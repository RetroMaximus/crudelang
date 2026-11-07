"""Host-side pieces of a running project that are not a subsystem.

The clock came first - ``TIME``, ``SLEEP`` and ``FPS`` needed one place to
answer from and nothing else. Stage 11 added the two pieces that surround it:
:mod:`crude.runtime.modes`, which says what ``terminal``/``headless``/``window``
promise a run, and :class:`crude.runtime.runtime.Runtime`, the frame loop that
keeps those promises - the plan's tick order, paced by exactly this clock.

Keeping all three in their own package is what lets the loader, the bindings
and a future animation all take time, mode and pacing from the same place
without any of them importing the others; and nothing here imports GLFW, so
``import crude.runtime`` works on a machine with no driver at all.
"""

from __future__ import annotations

from .clock import DEFAULT_FPS, Clock, FakeClock
from .modes import (
    DOWN_ORDER,
    HEADLESS,
    TERMINAL,
    WINDOW,
    Mode,
    MODES,
    downgrade,
    explain,
    mode_of,
    possible,
    wants_loop,
)
from .runtime import RuntimeError_, Runtime, open_display

__all__ = [
    "DEFAULT_FPS",
    "DOWN_ORDER",
    "HEADLESS",
    "Mode",
    "MODES",
    "Clock",
    "FakeClock",
    "Runtime",
    "RuntimeError_",
    "TERMINAL",
    "WINDOW",
    "downgrade",
    "explain",
    "mode_of",
    "open_display",
    "possible",
    "wants_loop",
]
