"""The maths every renderer shares, in logical pixels, top-left origin, y down.

Nothing here touches GL: each function turns a shape a script asked for into the
quads and points a renderer can emit. That is what makes the shape work testable
without a display - :mod:`tests.ui.test_geometry` judges angles, winding and
sampling, and the renderers only have to obey.

The coordinate space is the window's client rect as the plan fixes it: logical
pixels from GLFW, the same space :meth:`Renderer.begin` maps, with the device
pixel ratio folded into the projection rather than into every caller.
"""

from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence, Tuple

#: a full turn, in radians: an angle in this package is always radians
TAU = math.tau

#: ``((x, y), ...)`` - floats, not ints, because a rounded layout is still a layout
Point = Tuple[float, float]
#: a clip rect, the same four numbers as :meth:`Renderer.push_clip`
Rect = Tuple[float, float, float, float]

#: how far a capsule may stand from the shape it approximates, in logical pixels
#: (a 6-degree step on a 64 px radius circle is ~0.4 px off, which is one device px)
ARC_TOLERANCE = 0.5
#: the shortest arc worth a segment of its own
MIN_ARC_SEGMENTS = 3
#: an arc this long is a full ring: the last point would land on the first
FULL_SWEEP = TAU - 1e-6

#: the ratio of a glyph's advance to its size, for a renderer with no font loaded
TEXT_ADVANCE = 0.55
#: a line of text is this tall for its size, ascender and descender included
TEXT_HEIGHT = 1.25

#: the three ways a piece of text may be placed on the x it was given
ALIGNMENTS = ("left", "center", "right")
#: the type alias a caller may use when it wants to say "one of those three"
TextAlignment = str


def clamp(value: float, low: float, high: float) -> float:
    """``value`` kept between ``low`` and ``high``, whichever way round they came."""
    if low > high:
        low, high = high, low
    return max(low, min(value, high))


def scale_of(framebuffer: float, logical: float) -> float:
    """The device pixel ratio: how many pixels per logical pixel.

    A zero or missing measurement answers ``1.0`` rather than dividing by
    nothing: a renderer that has not begun a frame yet has no sizes to compare.
    """
    if not framebuffer or not logical:
        return 1.0
    return float(framebuffer) / float(logical)


# -- the device grid ------------------------------------------------------


def snap(value: Any, dpr: Any = 1.0) -> float:
    """One logical pixel moved to the nearest device pixel.

    The rounding the plan allows exactly once per frame, and the only place it
    happens: a draw pass that asks ``snap(rect, renderer.dpr)`` for the box it is
    about to fill gets a rect whose edges are whole device pixels, and the logical
    value it returns is still the layout's own answer to within half a device pixel.
    A ``dpr`` of ``1.0`` is plain rounding, and one of ``1.25`` - the ratio a 125%
    Windows scale hands out - puts a 16.0 px box at 16.0 and a 16.4 at 16.8,
    because 20.5 device pixels is not a thing a screen has.

    It lives here rather than in :mod:`crude.ui.layout` because both the layout
    pass and the tree's own geometry resolution need it, and neither of those two
    modules may import the other: the layout reads widgets, and a widget measures
    its screen box.
    """
    step = float(dpr) if dpr and float(dpr) > 0 else 1.0
    return math.floor(float(value) * step + 0.5) / step


def snap_box(box: Sequence[float], dpr: Any = 1.0) -> Rect:
    """A box with both of its edges on the device grid, for one draw.

    Each end is rounded on its own rather than rounding the position and the length,
    so two boxes that share an edge still share it after the snap - a row of stacked
    buttons cannot gain a hairline of background between two of them or lose the one
    it had - and a box too small for a device pixel keeps a length of ``0`` instead
    of borrowing one from its neighbour.
    """
    step = float(dpr) if dpr and float(dpr) > 0 else 1.0
    left = math.floor(float(box[0]) * step + 0.5)
    top = math.floor(float(box[1]) * step + 0.5)
    right = math.floor((float(box[0]) + float(box[2])) * step + 0.5)
    below = math.floor((float(box[1]) + float(box[3])) * step + 0.5)
    return (left / step, top / step, max(0, right - left) / step,
            max(0, below - top) / step)


# -- rectangles and lines -------------------------------------------------

def rect_corners(x: float, y: float, width: float, height: float,
                 rotation: float = 0.0) -> Tuple[Point, Point, Point, Point]:
    """The four corners of ``x, y, width, height``, rotated about its centre.

    Clockwise from the top-left, which is the order a triangle pair expects.
    A rotation of ``0`` returns the box as it was given.
    """
    cx, cy = x + width * 0.5, y + height * 0.5
    if not rotation:
        return ((x, y), (x + width, y), (x + width, y + height), (x, y + height))
    ca, sa = math.cos(rotation), math.sin(rotation)
    out: List[Point] = []
    for lx, ly in ((-width / 2, -height / 2), (width / 2, -height / 2),
                   (width / 2, height / 2), (-width / 2, height / 2)):
        out.append((cx + lx * ca - ly * sa, cy + lx * sa + ly * ca))
    return tuple(out)  # type: ignore[return-value]


def capsule(x1: float, y1: float, x2: float, y2: float,
            thickness: float) -> Tuple[float, float, float, float, float]:
    """A round-ended line as one rotated rounded rectangle.

    Returns ``(centre_x, centre_y, length, thickness, angle)``: the rectangle's
    radius is half its thickness, so the ends are round and the capsule covers
    the segment exactly. A segment shorter than half a pixel is not worth a
    triangle, and answers a length of ``0`` so the caller can skip it.
    """
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    thickness = max(0.0, float(thickness))
    if length < 0.5 or thickness <= 0:
        return (x1, y1, 0.0, thickness, 0.0)
    return ((x1 + x2) * 0.5, (y1 + y2) * 0.5, length, thickness,
            math.atan2(dy, dx))


def tapered_quad(x1: float, y1: float, x2: float, y2: float,
                 thickness1: float, thickness2: float) -> Tuple[Point, Point, Point, Point]:
    """The four corners of a line whose two ends have different widths.

    Used by a tapering stroke; the caps are drawn separately as two circles, so
    the corners sit *on* the endpoints rather than around them.
    """
    angle = math.atan2(y2 - y1, x2 - x1)
    nx, ny = -math.sin(angle), math.cos(angle)
    h1, h2 = thickness1 * 0.5, thickness2 * 0.5
    return ((x1 + nx * h1, y1 + ny * h1), (x2 + nx * h2, y2 + ny * h2),
            (x2 - nx * h2, y2 - ny * h2), (x1 - nx * h1, y1 - ny * h1))


# -- circles and arcs -----------------------------------------------------

def wrap_angle(angle: float) -> float:
    """An angle folded into ``[0, TAU)``; -0.001 and TAU are the same direction."""
    return angle % TAU


def arc_sweep(start: float, end: float) -> float:
    """How far an arc turns counter-clockwise from ``start`` to ``end``.

    Always ``[0, TAU)``, so ``end`` behind ``start`` means the long way round
    rather than a negative arc: an angle a script names is a direction, and the
    sweep is what it asks to paint. ``end == start`` is nothing at all, while a
    turn or more in either direction is a full ring - which is what lets
    ``arc(cx, cy, r, start=0, end=TAU)`` mean exactly what it says, and stay one
    segment short of closing on itself.
    """
    raw = float(end) - float(start)
    if raw == 0:
        return 0.0
    if abs(raw) >= TAU:
        return FULL_SWEEP
    if raw < 0:
        raw %= TAU
    return min(raw, FULL_SWEEP)


def arc_segments(sweep: float, tolerance: float = ARC_TOLERANCE,
                 radius: float = 0.0) -> int:
    """How many straight pieces an arc of this ``sweep`` needs.

    The count follows the sagitta a chord of a given radius may stray by:
    ``segments = ceil(sweep / 2·acos(1 - tolerance/radius))``, so a small circle
    is sampled finer than a big one and every segment stays within ``tolerance``
    logical pixels of the true curve. At least three, so a shape is never one
    segment, and no more than 180, so a silly radius cannot stall a frame.
    """
    if sweep <= 0:
        return 0
    if radius <= 0 or tolerance <= 0 or tolerance >= radius:
        step = TAU / 60.0
    else:
        step = 2.0 * math.acos(clamp(1.0 - tolerance / radius, -1.0, 1.0))
    return max(MIN_ARC_SEGMENTS, min(180, int(math.ceil(sweep / step))))


def arc_points(cx: float, cy: float, radius: float, start: float, end: float,
               segments: Optional[int] = None,
               tolerance: float = ARC_TOLERANCE) -> List[Point]:
    """Points along a circle from ``start`` to ``end``, ends included.

    ``segments`` overrides the tolerance-driven count. The list has
    ``segments + 1`` entries and its last point is the arc's end, so a caller
    can emit one capsule per pair without a gap at either tip.
    """
    sweep = arc_sweep(start, end)
    count = arc_segments(sweep, tolerance, radius) if segments is None else max(
        1, int(segments))
    if radius <= 0 or sweep <= 0:
        return []
    step = sweep / count
    return [(cx + radius * math.cos(start + index * step),
             cy + radius * math.sin(start + index * step))
            for index in range(count + 1)]


def circle_bounds(cx: float, cy: float, radius: float) -> Rect:
    """The box a filled circle of this radius needs: the rounded-rect SDF with
    ``radius`` clamped to half the box answers an exact disc."""
    size = max(0.0, float(radius)) * 2.0
    return (cx - size * 0.5, cy - size * 0.5, size, size)


# -- polylines and polygons ------------------------------------------------


def polyline_pairs(points: Sequence[Point], closed: bool = False) -> List[Tuple[Point, Point]]:
    """Each consecutive pair of points, the last joined to the first if closed."""
    if len(points) < 2:
        return []
    pairs = [(points[index], points[index + 1])
             for index in range(len(points) - 1)]
    if closed and points[0] != points[-1]:
        pairs.append((points[-1], points[0]))
    return pairs


def polygon_fan(points: Sequence[Point]) -> List[Tuple[Point, Point, Point]]:
    """A polygon as triangles fanned from its first vertex.

    Exactly ``len(points) - 2`` triangles, in the order the points came, so the
    winding a script chose (clockwise or not) is preserved rather than corrected:
    a flat-coloured fill does not care, and a caller that does can say so.
    """
    if len(points) < 3:
        return []
    first = points[0]
    return [(first, points[index], points[index + 1])
            for index in range(1, len(points) - 1)]


def polygon_bounds(points: Sequence[Point]) -> Rect:
    """The smallest box around a set of points; nothing about nothing is empty."""
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


# -- clipping --------------------------------------------------------------


def clip_overlap(a: Rect, b: Rect) -> Rect:
    """The area two clip rects agree on; an empty one when they do not meet.

    A clip stack could simply use the innermost rect, and the old renderer did,
    but a widget inside two panels should be cut by both: intersecting on the way
    down makes the answer independent of how many clips are open.
    """
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    return (x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))


# -- text ------------------------------------------------------------------


def text_bounds(text: str, size: float) -> Tuple[float, float]:
    """How wide and tall ``text`` is at ``size``, without a font loaded.

    An estimate, deliberately: a renderer with a real face answers its own
    metrics, and :class:`~crude.ui.recording.RecordingRenderer` and
    :class:`~crude.ui.null_renderer.NullRenderer` need something stable enough
    for a layout test to compare against. Two decimal places, because a layout
    that depends on the third is a layout that will not survive a DPI change.
    """
    if not text or size <= 0:
        return (0.0, 0.0)
    return (round(len(text) * size * TEXT_ADVANCE, 2),
            round(size * TEXT_HEIGHT, 2))


def alignment(value: Any) -> TextAlignment:
    """One of :data:`ALIGNMENTS`, guessing kindly: anything unknown is left.

    ``centre`` is accepted because a project written by a person who learned
    British spelling is not a mistake, and neither is ``""`` - which means "I did
    not say", and a label that was not told where to sit sits where it was put.
    """
    text = str(value or "left").strip().lower()
    if text in ("centre", "middle"):
        return "center"
    return text if text in ALIGNMENTS else "left"


def align_x_offset(width: float, align: str) -> float:
    """How far left of the given ``x`` a box of ``width`` actually starts."""
    align = alignment(align)
    if align == "center":
        return float(width) * 0.5
    if align == "right":
        return float(width)
    return 0.0


def align_x(x: float, width: float, align: str) -> float:
    """The left edge for a box of ``width`` placed at ``x`` with ``align``.

    ``center`` and ``right`` shift the drawing left by the width it was measured
    at, so a label stays where it was measured to stay. Anything else is left.
    """
    return float(x) - align_x_offset(width, align)


__all__ = [
    "ALIGNMENTS", "ARC_TOLERANCE", "FULL_SWEEP", "MIN_ARC_SEGMENTS", "Point",
    "Rect", "TAU", "TEXT_ADVANCE", "TEXT_HEIGHT", "TextAlignment",
    "align_x", "align_x_offset", "alignment", "arc_points", "arc_segments",
    "arc_sweep", "capsule", "circle_bounds", "clamp", "clip_overlap",
    "polygon_bounds", "polygon_fan", "polyline_pairs", "rect_corners",
    "scale_of", "snap", "snap_box", "tapered_quad", "text_bounds",
    "wrap_angle",
]
