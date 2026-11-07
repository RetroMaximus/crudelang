"""Shared helpers for the ``tests/ui`` suite.

Nothing in here draws anything itself: it is the little pile of calls a widget
would make, so a test of a renderer can say "draw a knob" and mean the same four
ops in every file that says it.

The name is unique across the test tree because it has to be: pytest keeps one
module per basename, so a plain ``helpers.py`` here would be shadowed by the one
the lang suite collects first.
"""

from __future__ import annotations

from typing import Any


def draw_a_knob(renderer: Any, x: float = 10.0, y: float = 10.0,
                size: float = 40.0, value: float = 0.5) -> None:
    """A stand-in for the S7 knob: a face, a value arc, a pointer and a label."""
    half = size / 2
    renderer.rect(x, y, size, size, color="#222222", radius=half)
    renderer.arc(x + half, y + half, half - 4, 3, start=0.75, end=2.35)
    renderer.circle(x + half, y + half, 4)
    renderer.text(x + half, y + size + 2, f"{value:.2f}", 10, align="center")


#: the ops :func:`draw_a_knob` emits, in the order it emits them
KNOB_OPS = ("rect", "arc", "circle", "text")
