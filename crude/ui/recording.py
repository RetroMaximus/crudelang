"""A renderer whose only job is to remember, so a widget can be tested on paper.

:class:`RecordingRenderer` is the plan's fake-first answer for the UI: it takes
the whole :class:`~crude.ui.base.Renderer` contract and does nothing but keep the
:class:`~crude.ui.base.DrawOp` list. Placement, clipping, z-order and every
widget's look are then ordinary assertions on that list, which is what the
plan's acceptance lines for S7-S9 ("`RecordingRenderer` assertions on a
script-built tree") and the metronome tests are written against.

It is deliberately literal: the ops arrive exactly as the contract normalised
them, in the order they were emitted, with the clip that was open at the time
attached. Nothing is merged, sorted or dropped, because a renderer that tidied up
what it was told would hide the one bug that matters in a layout - a widget
drawing where it was not told to.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .base import DrawOp, Renderer
from .geometry import Rect

#: a frame's size as recorded: logical, then device
FrameSize = Tuple[float, float, int, int]


class RecordingRenderer(Renderer):
    """Every draw call kept as a :class:`~crude.ui.base.DrawOp`, in order.

    ``ops`` is the whole run; ``frames`` is the same list cut up per finished
    frame, which is the one a test of "what the third frame looked like" wants.
    A fake that throws away the frame boundaries could not tell whether a widget
    drew once per frame or once per frame per parent.
    """

    def __init__(self, name: str = "recording") -> None:
        super().__init__(name)
        #: every op since construction, across frames
        self.ops: List[DrawOp] = []
        #: one list of ops per :meth:`end`, oldest first
        self.frames: List[List[DrawOp]] = []
        #: one ``(width, height, fb_width, fb_height)`` per :meth:`begin`
        self.sizes: List[FrameSize] = []
        #: ``(key, path)`` for every file this renderer was asked to load
        self.loaded: List[Tuple[str, str]] = []
        #: ``(key, (width, height))`` for every upload, in order
        self.uploaded: List[Tuple[str, Tuple[float, float]]] = []
        #: every read_pixels call's arguments, since only a real GL can answer
        self.reads: List[Tuple[float, float, float, float, bool]] = []
        self._current: List[DrawOp] = []
        #: what to hand back from :meth:`read_pixels`, so a test can fake a pixel
        self.pixels: Any = None

    # -- the contract -----------------------------------------------------
    def emit(self, op: DrawOp) -> None:
        self._current.append(op)
        self.ops.append(op)

    def on_frame_begin(self) -> None:
        self.sizes.append((self.width, self.height, self.fb_width, self.fb_height))
        self._current = []

    def on_frame_end(self) -> None:
        self.frames.append(self._current)
        self._current = []

    def on_texture(self, key: str, path: str) -> None:
        self.loaded.append((key, path))

    def on_upload(self, key: str, array: Any) -> None:
        self.uploaded.append((key, self.texture_size(key)))

    def on_read_pixels(self, x: float, y: float, width: float, height: float,
                       premultiplied: bool) -> Any:
        self.reads.append((x, y, width, height, premultiplied))
        return self.pixels

    # -- reading what was drawn ------------------------------------------
    def names(self) -> List[str]:
        """The op names, in order - the shape of a frame at a glance."""
        return [op.name for op in self.ops]

    def ops_named(self, name: str) -> List[DrawOp]:
        """Every op of one kind, in the order they came."""
        return [op for op in self.ops if op.name == name]

    def count(self, name: str) -> int:
        return len(self.ops_named(name))

    def last(self, name: str = None) -> Optional[DrawOp]:
        """The most recent op, or the most recent of one kind; ``None`` for none."""
        pool = self.ops if name is None else self.ops_named(name)
        return pool[-1] if pool else None

    def first(self, name: str = None) -> Optional[DrawOp]:
        pool = self.ops if name is None else self.ops_named(name)
        return pool[0] if pool else None

    def texts(self) -> List[str]:
        """Every string drawn, in order - what a test of a label or a value says."""
        return [op.get("text", "") for op in self.ops_named("text")]

    def rects(self) -> List[Rect]:
        """Every ``rect`` and ``atlas`` box, in order, for a layout assertion."""
        out: List[Rect] = []
        for op in self.ops:
            if op.name in ("rect", "atlas"):
                out.append((op.get("x", 0.0), op.get("y", 0.0),
                            op.get("width", 0.0), op.get("height", 0.0)))
        return out

    def frame_ops(self, index: int) -> Sequence[DrawOp]:
        """The ops of one finished frame, counted from zero."""
        return self.frames[index]

    def reset(self) -> None:
        """Forget everything, to look at the next frame on its own."""
        self.ops.clear()
        self.frames.clear()
        self.sizes.clear()
        self.loaded.clear()
        self.uploaded.clear()
        self.reads.clear()
        self._current = []

    def stats(self) -> Dict[str, Any]:
        report = super().stats()
        report.update({
            "recorded": len(self.ops),
            "recorded_frames": len(self.frames),
            "loaded": len(self.loaded),
            "uploaded": len(self.uploaded),
        })
        return report

    def __str__(self) -> str:
        return (f"RecordingRenderer {len(self.ops)} op(s) in "
                f"{len(self.frames)} frame(s)")


__all__ = ["RecordingRenderer", "FrameSize"]
