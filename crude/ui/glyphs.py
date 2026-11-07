"""Icons that are drawn rather than shipped: paths in, one atlas out.

Every icon in this program is a string of drawing commands in a normalised
``0..100`` box, rasterized by Pillow at the size it is needed and packed into one
texture. Nothing here needs a GPU or a window - numpy and Pillow are the whole
dependency - which is what keeps :mod:`crude.ui` importable on a machine that has
no GL at all, and lets a test ask for the pixels of a ``play`` triangle and get
an answer (plan "Glyphs, icons & skinning", and the headless-parity rule).

Three rules hold the design together:

* **A glyph is a path, not a file.** :data:`GLYPHS` is a table of :class:`GlyphDef`
  values - the same picture a project could ship as PNG art, but it scales to any
  size, tints to any colour and costs nothing to distribute.
* **There is one texture.** :class:`GlyphAtlas` shelf-packs every raster it is
  asked for into a single RGBA array, so a screen full of icons is one texture
  bind and one quad each, and tinting happens per draw in the shader
  (``v_color * tex``) rather than per icon on disk. The texture keeps one key for
  its whole life and is refilled in place, the way :class:`~crude.ui.ring.RingTexture`
  is, so a new icon costs an upload and not a reallocation.
* **Nothing is re-rasterized.** The cache key is ``(name, size, weight)`` and the
  size is snapped up to the ``12/16/20/24/32`` ladder, so a hundred widgets asking
  for a 20 px icon get one raster. The atlas version - and with it the one upload
  the renderer owes - only moves when a cell is genuinely added, and the texture
  only grows when a cell genuinely will not fit.
* **A cell never moves.** Growth widens or lengthens the atlas and leaves every
  cell where it is, because the UV box of a glyph already drawn this frame is a
  number a renderer is holding in a batch, and pixels that moved underneath it are
  drawn in the wrong place until the frame is over. A resize still reaches the GPU
  as a refill of the one texture; see :meth:`~crude.ui.base.Renderer.on_upload`
  for what a backend owes when that size changes.

A name that is not in the set draws a placeholder rather than raising, and is
remembered once in :attr:`GlyphAtlas.missing`: a typo in a script is a visible
empty box in the widget, and a line the report can print, not a crash mid-load.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw

from .base import UIError, WHITE

__all__ = ["GLYPHS", "GlyphAtlas", "GlyphDef", "GlyphError", "Layer", "Cell",
           "Command", "LADDER", "SUPER_SAMPLE", "PADDING", "FALLBACK",
           "ATLAS_BASE", "BOX", "BLEED",
           "parse_path", "flatten", "raster", "snap_size", "icon_names",
           "has_icon", "dump_icons", "flatten_pixels"]

Point = Tuple[float, float]

#: the sizes a glyph is rasterized at; anything between rungs rounds up
LADDER: Tuple[int, ...] = (12, 16, 20, 24, 32)
#: how many times bigger than needed an icon is drawn, then shrunk back for its
#: anti-aliased edges - Pillow fills polygons without smoothing of its own
SUPER_SAMPLE = 3
#: device pixels of clear space around a cell, so linear filtering between two
#: glyphs in the same atlas cannot pull in its neighbour
PADDING = 1
#: the width of the atlas before it has to grow
ATLAS_BASE = 128
#: what is drawn, and what is reported, when a name is not in the set
FALLBACK = "missing"

#: the whole coordinate space of a path, and how far from an edge a mark sits
BOX = 100.0

#: path units of room left outside the box, so a stroke that sits on the edge of
#: it still has its own width and its round cap inside the picture. Every glyph is
#: drawn through this one mapping, which is why a mark at ``M50 4`` - the tip of an
#: arrow - is not cut in half at 12 px and looks no different at 32.
BLEED = 8.0


# ---------------------------------------------------------------------------
# the path language
# ---------------------------------------------------------------------------

class GlyphError(UIError):
    """A path that cannot be read, or an icon that cannot be made."""


#: how many numbers each command takes; ``None`` means "any multiple"
_ARGS: Dict[str, Optional[int]] = {"M": 2, "L": 2, "C": 6, "Q": 4, "A": 7,
                                   "Z": 0}

#: a letter is a command whatever it is, so a misspelled one can be named in the
#: error rather than being read as a number with no command in front of it
_TOKEN = re.compile(r"([A-Za-z])|"
                    r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


@dataclass(frozen=True)
class Command:
    """One drawing instruction, already upper-cased, with its own raw arguments.

    ``relative`` is whether the author wrote the lower-case form: a relative
    segment is measured from the current point, which is how a long polyline of
    the same shape can be written once and moved about the box.
    """

    name: str
    args: Tuple[float, ...]
    relative: bool = False

    def __str__(self) -> str:
        said = " ".join(f"{value:g}" for value in self.args)
        return f"{self.name} {said}".strip()


def parse_path(text: str) -> List[Command]:
    """Read a path string into commands. No dependencies, no exceptions of
    Python's own - a bad path in the table is a :class:`GlyphError` with the text
    that caused it, because the author of that string is reading this at 2am.

    The grammar is the SVG one, narrowed: ``M L C Q A Z``, upper case absolute or
    lower case relative, numbers separated by whitespace or commas (or nothing at
    all where a sign makes it unambiguous), and repeated coordinate pairs after
    ``M`` continuing as line segments.
    """
    commands: List[Command] = []
    pending: Optional[Command] = None
    numbers: List[float] = []
    for match in _TOKEN.finditer(str(text)):
        letter, value = match.group(1), match.group(2)
        if letter is not None:
            pending = _flush(pending, numbers, commands)
            numbers = []
            name = letter.upper()
            if name not in _ARGS:
                raise GlyphError(f"'{letter}' is not a glyph command "
                                 f"(known: {' '.join(sorted(_ARGS))})")
            pending = Command(name, (), letter.islower())
            continue
        if pending is None:
            raise GlyphError(f"a path has to start with a command, not "
                             f"with '{value}'")
        numbers.append(float(value))
    _flush(pending, numbers, commands)
    if not commands:
        raise GlyphError("a path cannot be empty")
    if commands[0].name != "M":
        raise GlyphError(f"a path starts at a point: '{commands[0].name}' "
                         f"cannot be the first command")
    return commands


def _flush(pending: Optional[Command], numbers: List[float],
           into: List[Command]) -> Optional[Command]:
    """Give the collected numbers to the command waiting for them."""
    if pending is None:
        return None
    want = _ARGS[pending.name]
    if want == 0:
        if numbers:
            raise GlyphError(f"'{pending.name}' takes no numbers, not "
                             f"{len(numbers)}")
        into.append(pending)
        return None
    if len(numbers) % want:
        raise GlyphError(f"'{pending.name}' takes groups of {want} numbers "
                         f"and was given {len(numbers)}")
    if not numbers:
        raise GlyphError(f"'{pending.name}' was given no numbers at all")
    for start in range(0, len(numbers), want):
        args = tuple(numbers[start:start + want])
        # "M 10 10 20 20" is a move and a line: after a moveto, extra pairs are
        # lineto, which is what SVG does and what makes a polygon readable
        name = "L" if (pending.name == "M" and start) else pending.name
        into.append(Command(name, args, pending.relative))
    return pending


# ---------------------------------------------------------------------------
# paths as points
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Subpath:
    """One continuous run of points, and whether its last joins its first."""

    points: Tuple[Point, ...]
    closed: bool = False

    def bounds(self) -> Tuple[float, float, float, float]:
        if not self.points:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [point[0] for point in self.points]
        ys = [point[1] for point in self.points]
        return (min(xs), min(ys), max(xs), max(ys))


def flatten(commands: Sequence[Command], *, scale: float = 2.0
            ) -> List[Subpath]:
    """Turn commands into polylines, sampling the curves.

    ``scale`` is device pixels per path unit: a curve is cut into pieces no longer
    than a couple of pixels, so a 16 px icon is not given the same number of
    vertices as a 320 px one, and a straight line stays two points whatever it is
    drawn at.
    """
    steps = max(1.0, float(scale))
    subpaths: List[Subpath] = []
    current: List[Point] = []
    start: Point = (0.0, 0.0)
    here: Point = (0.0, 0.0)

    def absolute(args: Sequence[float], *, shift_from: int = 0) -> List[float]:
        """The command's numbers as positions in the box.

        A relative command measures its coordinates from the pen, but not every
        number in every command is a coordinate: an arc's radii, rotation and two
        flags are plain numbers and only its end pair moves, which is what
        ``shift_from`` is for.
        """
        if not _relative_now[0]:
            return [float(value) for value in args]
        out = [float(value) for value in args]
        for index in range(shift_from, len(out), 2):
            out[index] += here[0]
            out[index + 1] += here[1]
        return out

    _relative_now = [False]
    for command in commands:
        _relative_now[0] = command.relative
        name, args = command.name, command.args
        if name == "M":
            if current:
                _close(subpaths, current, closed=False, start=start)
            where = absolute(args)[0:2]
            start = here = (where[0], where[1])
            current = [start]
        elif name == "L":
            x, y = absolute(args)[0:2]
            here = (x, y)
            current.append(here)
        elif name == "C":
            x1, y1, x2, y2, end_x, end_y = absolute(args)
            end = (end_x, end_y)
            for point in _cubic(here, x1, y1, x2, y2, end, steps):
                current.append(point)
            here = end
        elif name == "Q":
            control_x, control_y, end_x, end_y = absolute(args)
            control = (control_x, control_y)
            end = (end_x, end_y)
            # a quadratic as the cubic it is equivalent to, so one sampler serves
            # both kinds of curve
            c1 = (here[0] + 2 * (control[0] - here[0]) / 3,
                  here[1] + 2 * (control[1] - here[1]) / 3)
            c2 = (end[0] + 2 * (control[0] - end[0]) / 3,
                  end[1] + 2 * (control[1] - end[1]) / 3)
            for point in _cubic(here, c1[0], c1[1], c2[0], c2[1], end, steps):
                current.append(point)
            here = end
        elif name == "A":
            values = absolute(args, shift_from=5)
            rx, ry, rotation, large, sweep = (values[0], values[1], values[2],
                                             values[3], values[4])
            end = (values[5], values[6])
            for point in _arc(here, rx, ry, rotation, large, sweep, end, steps):
                current.append(point)
            here = end
        elif name == "Z":
            if current:
                current.append(current[0])
                _close(subpaths, current, closed=True, start=start)
                current = []
                here = start
    if current:
        _close(subpaths, current, closed=False, start=start)
    return [subpath for subpath in subpaths if len(subpath.points) > 1]


def _close(into: List[Subpath], points: List[Point], *, closed: bool,
           start: Point) -> None:
    into.append(Subpath(tuple(points), closed))


def _steps(p1: Point, p2: Point, steps: float) -> int:
    """How many pieces a run of about this much length should be cut into."""
    length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    return max(3, int(math.ceil(length * steps / 2.0)))


def _cubic(here: Point, x1: float, y1: float, x2: float, y2: float,
           end: Point, steps: float, /) -> List[Point]:
    """The points along one cubic, leaving out its first - the caller has that."""
    count = _steps(here, end, steps)
    out: List[Point] = []
    for index in range(1, count + 1):
        t = index / count
        mt = 1.0 - t
        a = mt * mt * mt
        b = 3.0 * mt * mt * t
        c = 3.0 * mt * t * t
        d = t * t * t
        out.append((a * here[0] + b * x1 + c * x2 + d * end[0],
                    a * here[1] + b * y1 + c * y2 + d * end[1]))
    return out


def _arc(here: Point, rx: float, ry: float, rotation: float, large: float,
         sweep: float, end: Point, steps: float) -> List[Point]:
    """An SVG arc: centre-parameterised, then sampled the same way a curve is.

    The radii correction and the far/near choice are the ones the SVG spec
    prescribes, so an arc written here is the arc a browser would draw from the
    same numbers - which is the only reason the path format is worth keeping.
    """
    if here == end:
        return []
    rx, ry = abs(float(rx)), abs(float(ry))
    if rx <= 0 or ry <= 0:
        return [end]
    phi = math.radians(float(rotation))
    cos, sin = math.cos(phi), math.sin(phi)
    half_dx, half_dy = (here[0] - end[0]) / 2.0, (here[1] - end[1]) / 2.0
    x1 = cos * half_dx + sin * half_dy
    y1 = -sin * half_dx + cos * half_dy
    lambda_ = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lambda_ > 1.0:
        grow = math.sqrt(lambda_)
        rx, ry = rx * grow, ry * grow
    sign = -1.0 if bool(large) == bool(sweep) else 1.0
    top = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    bottom = rx * rx * y1 * y1 + ry * ry * x1 * x1
    factor = sign * math.sqrt(max(0.0, top / bottom)) if bottom else 0.0
    centre_x = factor * rx * y1 / ry
    centre_y = -factor * ry * x1 / rx
    cx = cos * centre_x - sin * centre_y + (here[0] + end[0]) / 2.0
    cy = sin * centre_x + cos * centre_y + (here[1] + end[1]) / 2.0

    def angle(from_x: float, from_y: float, to_x: float, to_y: float) -> float:
        dot = from_x * to_x + from_y * to_y
        length = math.hypot(from_x, from_y) * math.hypot(to_x, to_y)
        found = math.acos(max(-1.0, min(1.0, dot / length))) if length else 0.0
        return -found if from_x * to_y - from_y * to_x < 0 else found

    theta = angle(1.0, 0.0, (x1 - centre_x) / rx, (y1 - centre_y) / ry)
    delta = angle((x1 - centre_x) / rx, (y1 - centre_y) / ry,
                  (-x1 - centre_x) / rx, (-y1 - centre_y) / ry)
    if not bool(sweep) and delta > 0:
        delta -= 2.0 * math.pi
    elif bool(sweep) and delta < 0:
        delta += 2.0 * math.pi
    radius = max(rx, ry)
    count = max(4, int(math.ceil(abs(delta) * radius * steps / 2.0)))
    out: List[Point] = []
    for index in range(1, count + 1):
        at = theta + delta * index / count
        point_x = rx * math.cos(at)
        point_y = ry * math.sin(at)
        out.append((cos * point_x - sin * point_y + cx,
                    sin * point_x + cos * point_y + cy))
    return out


# ---------------------------------------------------------------------------
# one glyph, rasterized
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Layer:
    """One path, drawn one way: filled, outlined, or both.

    Two layers make a picture with a mark in it - a loudspeaker and its sound, a
    circle and its cross - and both are the *same* tint, because an atlas is white
    and the colour is multiplied in later. A layer that needs its own colour would
    need its own draw, which is the thing the atlas exists to avoid.
    """

    path: str
    fill: bool = True
    stroke: bool = False
    width: float = 8.0

    def scaled(self, weight: float) -> "Layer":
        """The same layer with its line width multiplied - for a bolder icon."""
        return Layer(self.path, self.fill, self.stroke, self.width * weight)


def F(path: str) -> Layer:
    """A filled layer."""
    return Layer(path, fill=True, stroke=False)


def S(path: str, width: float = 8.0) -> Layer:
    """A stroked layer.

    An outline is the only way a glyph can have detail in it, because everything in
    one glyph is the same tint: a line drawn *inside* a filled shape is white on
    white and shows nothing at all.
    """
    return Layer(path, fill=False, stroke=True, width=width)


@dataclass(frozen=True)
class GlyphDef:
    """An icon: a name, and the layers that make it up."""

    name: str
    layers: Tuple[Layer, ...]

    @property
    def path(self) -> str:
        return self.layers[0].path if self.layers else ""

    def __str__(self) -> str:
        return f"Glyph '{self.name}' ({len(self.layers)} layer(s))"


def _def(name: str, *layers: Layer) -> GlyphDef:
    """One row of the table."""
    return GlyphDef(name, layers)


#: icon set v1: the plan's list, in the plan's order, plus the placeholder.
#: Everything lives in a 0..100 box with about a tenth of it left as margin, so
#: one icon is the same size as the next one whatever it draws.
GLYPHS: Dict[str, GlyphDef] = {
    found.name: found for found in (
        _def("play", F("M28 18 L82 50 L28 82 Z")),
        _def("pause", F("M26 18 L40 18 L40 82 L26 82 Z"),
             F("M60 18 L74 18 L74 82 L60 82 Z")),
        _def("stop", F("M24 24 L76 24 L76 76 L24 76 Z")),
        _def("record", S("M16 50 A34 34 0 1 1 84 50 A34 34 0 1 1 16 50", 9),
             F("M50 38 A12 12 0 1 1 50 62 A12 12 0 1 1 50 38")),
        _def("power", S("M30 26 A28 28 0 1 0 70 26", 9), S("M50 14 L50 50", 9)),
        _def("dsp", S("M14 62 C30 62 30 34 46 34 C62 34 62 62 78 62", 9),
             S("M14 82 L86 82", 7)),
        _def("beat", F("M50 14 L66 80 L34 80 Z"), S("M24 88 L76 88", 8)),
        _def("gear", S("M50 10 L50 28 M50 90 L50 72 M10 50 L28 50 M90 50 L72 50 "
                       "M78 22 L66 34 M22 78 L34 66 M22 22 L34 34 M78 78 L66 64",
                       12),
             S("M50 26 A24 24 0 1 1 50 74 A24 24 0 1 1 50 26", 9)),
        _def("plus", S("M50 18 L50 82 M18 50 L82 50", 11)),
        _def("minus", S("M18 50 L82 50", 11)),
        _def("chevron-l", S("M62 22 L34 50 L62 78", 11)),
        _def("chevron-r", S("M38 22 L66 50 L38 78", 11)),
        _def("chevron-u", S("M22 62 L50 34 L78 62", 11)),
        _def("chevron-d", S("M22 38 L50 66 L78 38", 11)),
        _def("folder", F("M10 28 L38 28 L46 40 L90 40 L90 82 L10 82 Z")),
        _def("file", S("M24 10 L60 10 L78 28 L78 90 L24 90 Z", 8),
             S("M58 12 L58 30 L76 30", 6)),
        _def("save", S("M12 12 L72 12 L88 28 L88 88 L12 88 Z", 7),
             S("M26 12 L26 36 L66 36 L66 12", 6),
             S("M28 54 L72 54 L72 86 L28 86 Z", 6)),
        _def("undo", S("M32 36 C58 24 80 42 74 64 C70 80 54 86 40 80", 9),
             F("M10 36 L38 22 L38 50 Z")),
        _def("redo", S("M68 36 C42 24 20 42 26 64 C30 80 46 86 60 80", 9),
             F("M90 36 L62 22 L62 50 Z")),
        _def("trash", S("M26 34 L31 90 L69 90 L74 34", 8),
             S("M16 26 L84 26", 8), S("M40 26 L40 15 L60 15 L60 26", 7),
             S("M43 44 L44 78 M57 44 L56 78", 6)),
        _def("warning", S("M50 12 L90 84 L10 84 Z", 9),
             S("M50 36 L50 62", 9), S("M50 72 L50 75", 9)),
        _def("info", S("M50 12 A38 38 0 1 1 50 88 A38 38 0 1 1 50 12", 8),
             S("M50 44 L50 70", 9), S("M50 28 L50 31", 9)),
        _def("error", S("M50 12 A38 38 0 1 1 50 88 A38 38 0 1 1 50 12", 8),
             S("M34 34 L66 66 M66 34 L34 66", 9)),
        _def("knob", S("M50 22 A28 28 0 1 1 50 78 A28 28 0 1 1 50 22", 9),
             S("M50 50 L50 26", 10)),
        _def("waveform", S("M6 50 L18 50 L27 20 L39 80 L51 28 L61 68 L71 40 "
                           "L80 50 L94 50", 8)),
        _def("grid", S("M14 14 L86 14 L86 86 L14 86 Z M14 38 L86 38 M14 62 L86 62 "
                       "M38 14 L38 86 M62 14 L62 86", 7)),
        _def("piano", S("M10 24 L90 24 L90 76 L10 76 Z", 7),
             S("M30 24 L30 52 M50 24 L50 52 M70 24 L70 52", 7),
             S("M10 52 L90 52", 5)),
        _def("usb", S("M50 90 L50 24 M50 46 L72 32 L72 18 M50 64 L28 50 L28 34",
                      8),
             F("M50 4 L61 22 L39 22 Z"), F("M22 20 L34 20 L34 32 L22 32 Z"),
             S("M64 12 L80 12", 8)),
        _def("speaker", F("M12 40 L32 40 L56 20 L56 80 L32 60 L12 60 Z"),
             S("M68 38 Q84 50 68 62", 8)),
        _def("mute", F("M12 40 L32 40 L56 20 L56 80 L32 60 L12 60 Z"),
             S("M68 38 L88 58 M88 38 L68 58", 8)),
        _def("sliders", S("M10 30 L90 30 M10 50 L90 50 M10 70 L90 70", 7),
             F("M34 20 A10 10 0 1 1 34 40 A10 10 0 1 1 34 20"),
             F("M62 40 A10 10 0 1 1 62 60 A10 10 0 1 1 62 40"),
             F("M40 60 A10 10 0 1 1 40 80 A10 10 0 1 1 40 60")),
        _def(FALLBACK, S("M18 18 L82 18 L82 82 L18 82 Z M34 34 L66 66 M66 34 L34 66",
                         8)),
    )
}


def icon_names() -> Tuple[str, ...]:
    """Every icon this program can draw, in table order."""
    return tuple(GLYPHS)


def has_icon(name: str) -> bool:
    return str(name) in GLYPHS


def snap_size(value: float, ladder: Sequence[int] = LADDER) -> int:
    """The size a glyph is actually rasterized at.

    The smallest rung that is at least as big as what was asked for: a glyph is
    never drawn small and blown up, and a project that uses eighteen 20 px icons
    pays for one raster. Above the ladder there is nothing to snap to, so the
    number itself is used, rounded up.
    """
    wanted = max(1.0, float(value))
    for rung in ladder:
        if wanted <= rung:
            return int(rung)
    return int(math.ceil(wanted))


def raster(defn: GlyphDef, side: int, *, supersample: int = SUPER_SAMPLE,
           weight: float = 1.0) -> np.ndarray:
    """One icon as RGBA pixels: white, with the coverage in its alpha.

    ``side`` is the box in device pixels. It is drawn ``supersample`` times bigger
    and shrunk back, which is the whole of the anti-aliasing story - Pillow's
    polygon fill has no smoothing of its own, and a 16 px circle is a list of
    straight edges without this.
    """
    side = max(1, int(side))
    supersample = max(1, int(supersample))
    big = side * supersample
    scale = big / (BOX + 2 * BLEED)
    offset = BLEED * scale
    mask = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask)
    for layer in defn.layers:
        for subpath in flatten(parse_path(layer.path), scale=scale):
            points = [(x * scale + offset, y * scale + offset)
                      for x, y in subpath.points]
            if len(points) < 2:
                continue
            if layer.fill and len(points) > 2:
                draw.polygon(points, fill=255)
            if layer.stroke:
                width = max(1, int(round(layer.width * weight * scale)))
                draw.line(points, fill=255, width=width, joint="curve")
                if not subpath.closed:
                    _cap(draw, points[0], width)
                    _cap(draw, points[-1], width)
    if supersample > 1:
        mask = mask.resize((side, side), Image.Resampling.LANCZOS)
    alpha = np.asarray(mask, dtype=np.uint8)
    if alpha.ndim != 2:  # pragma: no cover - a Pillow 'L' image is one band
        alpha = alpha[:, :, 0]
    white = np.full((side, side, 3), 255, dtype=np.uint8)
    return np.dstack([white, alpha])


def _cap(draw: ImageDraw.ImageDraw, point: Point, width: int) -> None:
    """A round end on an open stroke, so a line does not stop in a square."""
    radius = width / 2.0
    draw.ellipse([point[0] - radius, point[1] - radius,
                  point[0] + radius, point[1] + radius], fill=255)


# ---------------------------------------------------------------------------
# the atlas
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Cell:
    """Where one glyph ended up in the atlas, and what it maps to."""

    name: str
    size: int
    weight: float
    x: int
    y: int
    width: int
    height: int

    def uv(self, atlas_width: int, atlas_height: int) -> Tuple[float, float,
                                                               float, float]:
        """``(u0, v0, u1, v1)``, with ``v`` measured from the top.

        The atlas is a numpy array whose first row is its top edge, and that is
        also the first row the renderer uploads, so v = 0 is the top of the
        picture and a glyph lands the way up it was drawn.
        """
        return (self.x / atlas_width, self.y / atlas_height,
                (self.x + self.width) / atlas_width,
                (self.y + self.height) / atlas_height)

    def __str__(self) -> str:
        return (f"Cell({self.name} {self.size}px @ {self.weight}w "
                f"{self.x},{self.y} {self.width}x{self.height})")


class _Shelf:
    """One row of the atlas, and the free space left in it."""

    __slots__ = ("y", "height", "cursor")

    def __init__(self, y: int, height: int) -> None:
        self.y = y
        self.height = height
        self.cursor = PADDING


class GlyphAtlas:
    """Every glyph ever asked for, in one texture, uploaded once per change.

    The renderer holds one of these and asks for a cell by name and size; the
    atlas rasterizes it on the first request, finds room for it, and says where it
    is. :attr:`version` goes up when a cell is added, while :attr:`texture_key`
    stays the same for the atlas's whole life, so "upload this again" is a thing
    that happens only when there is something new to upload and never costs a new
    texture.
    """

    def __init__(self, *, dpr: float = 1.0, width: int = ATLAS_BASE,
                 supersample: int = SUPER_SAMPLE,
                 ladder: Sequence[int] = LADDER) -> None:
        self.dpr = max(0.1, float(dpr))
        self.supersample = max(1, int(supersample))
        self.ladder = tuple(ladder)
        self._width = max(16, int(width))
        self._height = self._width
        #: ``(name, size, weight)`` -> the cell that holds it
        self._cells: Dict[Tuple[str, int, float], Cell] = {}
        self._shelves: List[_Shelf] = []
        #: ``(name, size, weight)`` -> the raster, kept so a growth can refill
        self._rasters: Dict[Tuple[str, int, float], np.ndarray] = {}
        self._array: Optional[np.ndarray] = None
        #: names asked for that are not in the table, each once
        self.missing: List[str] = []
        #: how many glyphs have ever been added - the "is it stale?" number
        self.version = 0
        #: the version last handed to a renderer, and how often that happened
        self.uploaded = -1
        self.uploads = 0
        self.rasters_made = 0
        self.growths = 0

    # -- what it is -------------------------------------------------------
    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def texture_key(self) -> str:
        """The key this atlas's texture lives under, and never changes.

        One name for one texture, refilled in place: :attr:`version` is what says
        the pixels moved. A key that changed with the contents would mean a new
        GPU allocation for every icon a project ever drew.
        """
        return f"crude:glyphs@{self.dpr:g}"

    def names(self) -> Tuple[str, ...]:
        """The glyphs already in the atlas - not the ones it could still make.

        One name once, whatever sizes of it were asked for, in the order they first
        appeared.
        """
        seen: List[str] = []
        for name, _, _ in self._cells:
            if name not in seen:
                seen.append(name)
        return tuple(seen)

    def cells(self) -> Tuple[Cell, ...]:
        """Every cell in the texture, in the order they were added.

        The report and ``crude glyphs --dump`` both want to walk the atlas, and a
        caller should not have to know what the packer keys itself by.
        """
        return tuple(self._cells.values())

    def cell(self, name: str, size: float, weight: float = 1.0) -> Optional[Cell]:
        """The cell for an icon, making it if it is not there yet.

        ``size`` is in logical pixels; the raster is ``size × dpr`` device pixels,
        snapped up the ladder. Returns ``None`` only for a name that has no
        picture at all - which, as long as the placeholder exists, it never does.
        """
        key = self._key(name, size, weight)
        found = self._cells.get(key)
        if found is None:
            found = self._add(*key)
        return found

    def uv(self, name: str, size: float, weight: float = 1.0
            ) -> Tuple[float, float, float, float]:
        found = self.cell(name, size, weight)
        if found is None:
            return (0.0, 0.0, 0.0, 0.0)
        return found.uv(self._width, self._height)

    def stats(self) -> Dict[str, Any]:
        return {"cells": len(self._cells), "version": self.version,
                "rasters": self.rasters_made, "missing": len(self.missing),
                "dpr": round(self.dpr, 3), "width": self._width,
                "height": self._height, "growths": self.growths,
                "uploads": self.uploads}

    def __str__(self) -> str:
        return (f"GlyphAtlas({len(self._cells)} of {len(GLYPHS)} icons, "
                f"{self._width}x{self._height}, v{self.version}, dpr "
                f"{self.dpr:g})")

    # -- the renderer -----------------------------------------------------
    def upload(self, renderer: Any) -> str:
        """Give the texture to ``renderer``, only if it has gained a glyph.

        The same shape :meth:`crude.ui.ring.RingTexture.upload` is: one key, refilled
        in place through :meth:`~crude.ui.base.Renderer.upload_texture`, and a
        renderer that already has the current version is not asked twice. A
        renderer that has never seen the key - a second window, say - is detected by
        the key being missing from it rather than by the version being stale.
        """
        key = self.texture_key
        if self.uploaded != self.version or key not in renderer.texture_keys():
            renderer.upload_texture(key, self.array())
            self.uploaded = self.version
            self.uploads += 1
        return key

    def draw(self, renderer: Any, x: float, y: float, width: float, height: float,
             name: str, *, color: Any = WHITE, rotation: float = 0.0,
             size: Optional[float] = None, weight: float = 1.0) -> Optional[Cell]:
        """Blit one icon into a box with one quad, and return the cell it came from.

        ``size`` is the side to rasterize at, for a box that is not square; left
        unsaid it is the shorter side, so an icon is never drawn from a raster
        bigger than the space it is shown in. The colour is the draw's, not the
        texture's - see :meth:`crude.ui.base.Renderer.glyph`, which is the way
        every caller in this program actually draws one.
        """
        # the box goes to the renderer exactly as it was told: an inside-out one
        # is moved by ``atlas`` the way an inside-out rect is, rather than flipped
        # here, which would make an icon the one draw call that answers a corner
        # differently from every other shape
        wanted = (min(abs(float(width)), abs(float(height))) if size is None
                  else abs(float(size)))
        cell = self.cell(name, wanted, weight)
        key = self.upload(renderer)
        renderer.atlas(x, y, width, height, key,
                       (cell.uv(self._width, self._height) if cell
                        else (0.0, 0.0, 0.0, 0.0)), color, rotation)
        return cell

    # -- the pixels -------------------------------------------------------
    def array(self) -> np.ndarray:
        """The atlas as one RGBA image: white everywhere, alpha where the ink is.

        Built once and cached until a cell is added or the texture grows, because
        the renderer uploads it again at most once per change and never asks for
        it twice in a frame.

        A cell's ``(x, y)`` is where its *ink* starts, which is also where its UV
        box starts; the clear ``PADDING`` ring around it is the space the packer
        left and never samples.
        """
        if self._array is None:
            canvas = np.zeros((self._height, self._width, 4), dtype=np.uint8)
            canvas[:, :, :3] = 255
            for found in self._cells.values():
                raster_ = self._rasters[(found.name, found.size, found.weight)]
                # only the alpha is copied: the rest of the atlas is already white,
                # and a black background between glyphs is what makes a filtered
                # edge go dark when the two are averaged
                canvas[found.y:found.y + found.height,
                       found.x:found.x + found.width, 3] = raster_[..., 3]
            self._array = canvas
        return self._array

    def pixels(self, name: str, size: float, weight: float = 1.0) -> np.ndarray:
        """One glyph on its own, for ``crude glyphs --dump`` and for a test.

        The same pixels that are in the atlas, cut back out of it - so what the
        tool writes and what the screen shows cannot disagree.
        """
        found = self.cell(name, size, weight)
        if found is None:
            return np.zeros((1, 1, 4), dtype=np.uint8)
        return np.ascontiguousarray(
            self.array()[found.y:found.y + found.height,
                         found.x:found.x + found.width])

    # -- the inside -------------------------------------------------------
    def _key(self, name: str, size: float,
             weight: float) -> Tuple[str, int, float]:
        rung = snap_size(max(1.0, float(size)) * self.dpr, self.ladder)
        return (str(name), int(rung), round(float(weight), 3))

    def _add(self, name: str, size: int, weight: float) -> Optional[Cell]:
        """Rasterize one glyph and find room for it."""
        defn = GLYPHS.get(name)
        if defn is None:
            if name not in self.missing:
                self.missing.append(name)
            return self._add(FALLBACK, size, weight)
        side = int(size)
        self._rasters[(name, size, weight)] = raster(
            defn, side, supersample=self.supersample, weight=weight)
        self.rasters_made += 1
        cell = self._reserve(name, size, weight, side)
        self._cells[(name, size, weight)] = cell
        self._array = None
        self.version += 1
        return cell

    def _reserve(self, name: str, size: int, weight: float,
                 side: int) -> Cell:
        """Put a ``side`` x ``side`` glyph in the first shelf with room for it.

        A shelf is a band of one height; a cell sits ``PADDING`` in from the band's
        top-left, so the ring of empty pixels around the ink belongs to the cell and
        no neighbour's UV box can reach it.
        """
        need = side + 2 * PADDING
        while need > self._width:
            # the atlas widens and everything already in it stays exactly where it
            # is. Re-packing would be tidier and would break a frame: the vertices
            # of a glyph drawn earlier are sitting in a batch with the *old* UV
            # numbers, and a refill that moved its pixels would sample the wrong
            # part of the atlas for the rest of that frame
            self._width *= 2
            self.growths += 1
        for shelf in self._shelves:
            if shelf.height >= need and shelf.cursor + need <= self._width:
                cell = Cell(name, size, weight, shelf.cursor + PADDING,
                            shelf.y + PADDING, side, side)
                shelf.cursor += need
                return cell
        top = sum(shelf.height for shelf in self._shelves)
        while top + need > self._height:
            self._height *= 2
            self.growths += 1
        self._shelves.append(_Shelf(top, need))
        cell = Cell(name, size, weight, PADDING, top + PADDING, side, side)
        self._shelves[-1].cursor = need
        return cell


def dump_icons(atlas: GlyphAtlas, directory: str, *, size: int = 32,
               names: Optional[Sequence[str]] = None,
               over: Any = None, ink: Any = WHITE) -> List[str]:
    """Write every glyph out as its own PNG; the paths written.

    This is ``crude glyphs --dump``: a person who wants to look at the generated
    art, or to replace it, can do both without a window.

    ``over`` is a colour to flatten each icon onto and ``ink`` the colour of its
    marks. The atlas's own pixels are white ink in a transparent box - exactly
    what the shader multiplies a tint against, and exactly what an image viewer
    shows as nothing at all - so a dump meant to be *looked at* asks for the
    theme's surface and its icon colour and gets one opaque picture per icon.
    Left unsaid the file holds the texture's pixels unchanged, which is what a
    test of "the PNG is the atlas" wants.
    """
    import os

    os.makedirs(directory, exist_ok=True)
    written: List[str] = []
    for name in list(names if names is not None else icon_names()):
        image = Image.fromarray(flatten_pixels(atlas.pixels(name, size),
                                               over, ink),
                                mode="RGBA")
        path = os.path.join(directory, f"{name}.png")
        image.save(path)
        written.append(path)
    return written


def flatten_pixels(pixels: np.ndarray, over: Any,
                   ink: Any = WHITE) -> np.ndarray:
    """One solid colour with the icon's marks laid on it, as one opaque picture.

    Both ``crude glyphs --dump`` and ``crude glyphs --atlas`` want this: a cell
    carries no colour of its own, only coverage, so the flatten is a mix of the
    two requested colours by that alpha - which is why an anti-aliased edge
    survives it and a 12 px chevron is still a chevron in the PNG.

    ``over`` of ``None`` hands back exactly the pixels it was given, because the
    texture *is* the answer for a caller that wants the atlas itself as a file.
    """
    if over is None:
        return pixels
    cover = pixels[..., 3:4].astype(np.float32) / 255.0
    back = _rgb(over)
    front = _rgb(ink)
    rgb = np.rint(back * (1.0 - cover) + front * cover).astype(np.uint8)
    # the result is opaque by definition: a plate has no transparency of its own
    plate = np.full(pixels.shape[:2] + (1,), 255, dtype=np.uint8)
    return np.concatenate([rgb, plate], axis=2)


def _rgb(colour: Any) -> np.ndarray:
    """A colour as three 0..255 channels, in the arithmetic's own precision."""
    from .base import as_color

    rgba = as_color(colour)
    return np.array([255.0 * channel for channel in rgba[:3]], dtype=np.float32)
