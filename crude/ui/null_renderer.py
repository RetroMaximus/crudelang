"""The renderer a headless run draws on: it counts, and forgets at once.

The plan's headless parity rule is the reason this file exists. In ``headless``
and ``terminal`` modes the widget tree is still built, still laid out, still
handles ``ON_CHANGE`` and ``ON_FRAME`` - and still paints, because a widget that
skips its paint pass in one mode is a widget whose mode branch every script then
has to carry. A :class:`NullRenderer` is what that paint pass lands on: the same
calls, the same argument rules, and no memory of the ops afterwards.

Only the counts are kept, per op name. A test that wants to look at what was
drawn uses :class:`crude.ui.recording.RecordingRenderer`; a run that will never
look keeps nothing, which is the difference between a headless project that runs
for a week and one that fills the RAM with descriptions of an invisible frame.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict

from .base import DrawOp, Renderer


class NullRenderer(Renderer):
    """A renderer that accepts every draw call and holds nothing but tallies."""

    def __init__(self, name: str = "null") -> None:
        super().__init__(name)
        #: ops per name since construction, the only thing kept
        self.calls: Counter = Counter()
        #: how many times a frame was begun, ended, clipped or read
        self.begins = 0
        self.ends = 0
        self.clips = 0
        self.reads = 0

    # -- the contract -----------------------------------------------------
    def emit(self, op: DrawOp) -> None:
        self.calls[op.name] += 1

    def on_frame_begin(self) -> None:
        self.begins += 1

    def on_frame_end(self) -> None:
        self.ends += 1
        # the clip stack does not survive a frame, and a widget that left one
        # open would otherwise clip the next one forever
        self._clips = []

    def on_clip_pushed(self, rect) -> None:
        self.clips += 1

    def on_read_pixels(self, x: float, y: float, width: float, height: float,
                       premultiplied: bool) -> Any:
        self.reads += 1
        return None

    # -- what it remembered ----------------------------------------------
    def count(self, name: str = None) -> int:
        """Every op it was told about, or only the ones of one kind."""
        return self.op_count if name is None else self.calls.get(name, 0)

    def total(self) -> int:
        return sum(self.calls.values())

    def by_name(self) -> Dict[str, int]:
        return dict(self.calls)

    def reset(self) -> None:
        """Start the tallies again; the textures a project loaded stay known."""
        self.calls.clear()
        self.begins = self.ends = self.clips = self.reads = 0

    def stats(self) -> Dict[str, Any]:
        report = super().stats()
        report.update({"total": self.total(), "begins": self.begins,
                       "ends": self.ends, "calls": dict(self.calls)})
        return report

    def __str__(self) -> str:
        return f"NullRenderer {self.total()} op(s) in {self.frame_count} frame(s)"


__all__ = ["NullRenderer"]
