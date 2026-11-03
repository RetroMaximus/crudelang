"""The cells of a grid: one record the arranger and a drop both have to agree on.

:mod:`crude.ui.layout` arranges a panel's children into cells and owned the
arithmetic that says how big each one is. A group dragged into a cell has to land
on *exactly* the rectangle the arranger would have given it, and two copies of one
formula is how those two facts drift apart - so the arithmetic lives here, once,
and the layout pass calls into it rather than keeping its own.

Two rules hold the module together:

* **Nothing here is imported from** :mod:`crude.ui.layout`. The pass imports this
  module, not the other way round: a cell is a box, and knowing a box needs no
  policy, no skin and no ordering, while arranging children does. The same knot is
  avoided in :mod:`crude.ui.scroll` for the same reason.
* **A cell is counted, not named.** ``GridSpec`` holds ``columns``, ``rows`` and
  the ``gap`` in pixels, and every answer is a function of the box it is asked
  against, so the same spec tiles a panel's content box for the arranger and a
  group's drop target for a gesture without either caller owning a coordinate.

The rows a grid answers with are the bridge to a script. ``grid_columns``,
``grid_rows`` and ``grid_gap`` are written by the pass that arranged the cells
(:func:`record`) and read back by :func:`spec_of`, so a face that has to draw the
cells and a drop that has to find one are both reading what the arrangement
decided rather than re-deciding it. ``docking`` is the same trick for a fact a
painter cannot otherwise see: a face is given a widget, a renderer and a skin, and
the pointer dragging a group across this panel belongs to none of them - so the
frame says so on the panel itself, and the outline appears and goes with the paint
key, the way ``hovered`` does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Tuple

from .base import UIError
from .geometry import Rect

__all__ = ["GridError", "GridSpec", "auto_columns", "rows_for", "ROWS",
           "COLUMNS", "GAP", "DOCKABLE", "DOCKING", "MEASURED", "spec_of",
           "record", "cells", "reconcile"]

#: the rows the layout pass answers for a panel that arranges its children into
#  cells. They are named apart from ``columns`` (a radio's own Pd argument) and
#  ``rows`` (a viewport's row height in pixels), which a flat props table cannot
#  share a name with however different the two meanings are.
COLUMNS = "grid_columns"
ROWS = "grid_rows"
GAP = "grid_gap"

#: the one row that is not a measurement: whether this panel may be split into
#  cells a group can be dropped into. ``docking`` below is its read-only partner.
DOCKABLE = "dockable"

#: read-only: a drag is in progress in this window, which is the only moment the
#  cells of a ``dockable`` panel are worth drawing
DOCKING = "docking"

#: every row this module writes. They are four of the rows
#  :data:`crude.ui.props.MEASURED` lists as the family a pass owns rather than the
#  pointer, and a test proves the names here are rows the table declares, so the
#  pass and the table cannot drift apart while both still spell them out.
MEASURED = (COLUMNS, ROWS, GAP, DOCKING)


class GridError(UIError):
    """The grid asked for has no cells, or the index is not one of them."""


def auto_columns(count: int, columns: int = 0) -> int:
    """How many columns a grid of ``count`` children gets.

    The rule the layout pass has always used: what the script named, or as square
    as the child count allows, and never more columns than there are children to
    fill them. It is here rather than there because a drop target has to be
    counted the same way as an arranged child, and a grid a script named three
    columns of is a grid of three columns whether the pass has drawn it yet or not.
    """
    total = max(0, int(count))
    wanted = int(columns or 0)
    if wanted <= 0:
        wanted = int(math.ceil(math.sqrt(total)))
    return max(1, min(wanted, max(1, total)))


def rows_for(count: int, columns: int) -> int:
    """How many rows ``count`` children need at ``columns`` to a row."""
    return int(math.ceil(max(0, int(count)) / float(max(1, int(columns)))))


@dataclass(frozen=True)
class GridSpec:
    """``columns`` x ``rows`` cells with ``gap`` logical pixels between them.

    The gap is a resolved number of pixels, never a ``"pad.md"`` token, because a
    token means whatever the skin says it means *this frame* and a cell a group is
    dropped into has to be the same cell the pass drew. Whoever measures the
    grid resolves the token first (:func:`crude.ui.layout.metric` does it on every
    pass) and hands the pixels here.

    ``rows`` is the number of rows the grid *has*, which is not always the number
    its children fill: three cells in a grid of two rows are proved below to leave
    the remainder as a real, empty, droppable cell rather than as nothing at all.
    """

    columns: int = 1
    rows: int = 1
    gap: float = 0.0

    def __post_init__(self) -> None:
        for name in ("columns", "rows"):
            value = getattr(self, name)
            if int(value) != value or int(value) < 1:
                raise GridError(f"a grid's '{name}' has to be a whole number of "
                                f"cells at least 1, not {value!r}")
        if float(self.gap) < 0.0:
            raise GridError(f"a grid's gap cannot be negative, and "
                            f"{self.gap!r} is")

    # -- building one ----------------------------------------------------
    @classmethod
    def of(cls, count: int, columns: int = 0, gap: float = 0.0,
           rows: int = 0) -> "GridSpec":
        """The grid ``count`` children need: ``columns`` named, or as square as they.

        ``rows`` of ``0`` takes the rows the children fill; a caller that is laying
        a dock out ahead of its content names the rows it wants and gets the cells
        it promised, empty ones included.
        """
        wide = auto_columns(count, columns)
        return cls(wide, int(rows) or rows_for(count, wide), float(gap or 0.0))

    # -- the two numbers a cell is made of -------------------------------
    @property
    def count(self) -> int:
        """How many cells there are, the empty ones included."""
        return self.columns * self.rows

    @property
    def cells(self) -> int:
        """Another word for :attr:`count`, for a caller that reads it as a noun."""
        return self.count

    def cell(self, index: int) -> Tuple[int, int]:
        """``(column, row)`` of one cell, counted from the top left."""
        if not 0 <= int(index) < self.count:
            raise GridError(f"a grid of {self.columns} x {self.rows} has no cell "
                            f"{index}; it has {self.count}, counted from 0")
        return int(index) % self.columns, int(index) // self.columns

    def size(self, box: Rect) -> Tuple[float, float]:
        """How big one cell is inside ``box``: the box less every gap, shared out."""
        return (max(0.0, (float(box[2]) - self.gap * (self.columns - 1))
                    / self.columns),
                max(0.0, (float(box[3]) - self.gap * (self.rows - 1)) / self.rows))

    def rect_of(self, index: int, box: Rect) -> Rect:
        """The cell ``index`` occupies, in ``box``'s own coordinates.

        The one answer both halves of the grid need: the layout pass calls it with
        the panel's content box taken back to its origin, because every box it
        writes is parent-relative, and a face or a drop calls it with the content
        box as it stands, because those are screen pixels. The arithmetic is the
        same either way, which is the whole reason this function exists.
        """
        col, row = self.cell(index)
        width, height = self.size(box)
        return (float(box[0]) + col * (width + self.gap),
                float(box[1]) + row * (height + self.gap), width, height)

    def index_at(self, x: Any, y: Any, box: Rect) -> Optional[int]:
        """Which cell holds ``x, y`` in ``box``'s coordinates; ``None`` for a gap.

        ``None`` is the honest answer rather than a nearest-cell guess: the space
        between two cells belongs to neither, and a drop that lands there has to be
        refused in words rather than quietly snapped somewhere the pointer was not.
        A point outside the box is the same answer.
        """
        if not (float(box[0]) <= float(x) < float(box[0]) + float(box[2])
                and float(box[1]) <= float(y) < float(box[1]) + float(box[3])):
            return None
        width, height = self.size(box)
        col = _lane(float(x) - float(box[0]), width, self.gap, self.columns)
        row = _lane(float(y) - float(box[1]), height, self.gap, self.rows)
        if col is None or row is None:
            return None
        return row * self.columns + col

    def every(self, box: Rect) -> List[Rect]:
        """Every cell in order: what a face outlines and a dock offers."""
        return [self.rect_of(index, box) for index in range(self.count)]


def _lane(there: float, length: float, gap: float, lanes: int) -> Optional[int]:
    """Which lane of one axis a distance falls in, or ``None`` for its gap.

    ``there`` is already measured from the box's own near edge, so the axis is just
    ``lanes`` repeats of ``length`` with ``gap`` between them - the far edge of the
    last one has no gap after it, which is why the answer is a lane number and not
    a division: ``there // (length + gap)`` alone puts a point in the gap *after* a
    cell in the cell it was trying to leave out.
    """
    if length <= 0.0 or there < 0.0:
        return None
    step = length + gap
    index = int(there // step) if step > 0.0 else 0
    if index >= lanes:
        return None
    if gap > 0.0 and there - index * step > length:
        return None
    return index


# --------------------------------------------------------------------------
# the rows a panel answers with
# --------------------------------------------------------------------------


def record(panel: Any, spec: Optional[GridSpec]) -> bool:
    """Answer the three rows this panel's cells are read back from.

    Called by the layout pass with the grid it just arranged, or with ``None`` for
    a panel that arranges nothing, so the rows go back to zero rather than staying
    at the shape of a stack the script has since undone. A measurement and nothing
    else: no ``ON_CHANGE``, no ``changed`` entry - a window being resized is not an
    edit a person made.
    """
    props = getattr(panel, "kind", None)
    if props is None or COLUMNS not in props.props:
        return False
    moved = False
    for name, value in ((COLUMNS, spec.columns if spec else 0),
                        (ROWS, spec.rows if spec else 0),
                        (GAP, float(spec.gap) if spec else 0.0)):
        if panel._measure(name, value):
            moved = True
    return moved


def spec_of(panel: Any) -> Optional[GridSpec]:
    """The cells this panel was arranged into last time the pass measured it.

    ``None`` for a panel with no grid, which is the same answer a gesture wants:
    there is nowhere to drop. Read off the rows rather than off the panel's own
    layout policy so that the face, the drop and a script's ``GET`` all answer from
    the one measurement the pass wrote.
    """
    props = getattr(panel, "kind", None)
    if props is None or COLUMNS not in props.props:
        return None
    columns = int(panel.get(COLUMNS) or 0)
    rows = int(panel.get(ROWS) or 0)
    if columns < 1 or rows < 1:
        return None
    return GridSpec(columns, rows, float(panel.get(GAP) or 0.0))


def cells(panel: Any) -> Tuple[Rect, ...]:
    """Every cell of this panel's grid, in its content box's own coordinates."""
    spec = spec_of(panel)
    if spec is None:
        return ()
    return tuple(spec.every(panel.content_box()))


def reconcile(root: Any, dragging: bool = False) -> int:
    """Tell every panel under ``root`` whether a drag is in progress now.

    The fact a ``dockable`` panel's face cannot see for itself - a face is handed
    a widget, a renderer and a skin, and the pointer is none of them - so the frame
    writes it where the painter can read it, exactly as the input system writes
    ``hovered``. Returns how many panels the answer actually moved, which is how
    many paint keys changed: an idle window writes nothing at all.
    """
    moved = 0
    for widget in _walk(root):
        props = getattr(widget, "kind", None)
        if props is None or DOCKING not in props.props:
            continue
        if widget._measure(DOCKING, bool(dragging)):
            moved += 1
    return moved


def _walk(root: Any) -> Iterable[Any]:
    """This widget and everything under it, tolerating a bare list of them."""
    if root is None:
        return []
    walk = getattr(root, "walk", None)
    if callable(walk):
        return walk()
    return [root]
