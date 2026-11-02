"""What every draw call means, before any of them reaches a GPU.

This module is the renderer *contract*: one abstract :class:`Renderer` that turns
each call into a normalised :class:`DrawOp`, and nothing else. Three rules make it
worth having rather than leaving each backend to its own signature.

* **The numbers are decided once.** A negative width, an arc whose end is behind
  its start, a radius bigger than its own box, a ``center`` label: all of it is
  settled here, so the GL renderer, the recorder and the headless null renderer
  cannot drift apart. :mod:`tests.ui.test_base_contract` runs the same contract
  test against every renderer in the package, which is what keeps the three
  honest.
* **A frame is a list of ops, not a call stack.** :meth:`Renderer.emit` is the
  only thing a subclass has to implement, so a widget can be tested by reading
  the ops it emitted instead of by owning a display - the plan's
  ``RecordingRenderer`` and the reason the placement maths is testable at all.
* **Nothing here imports GL, GLFW or PyOpenGL.** Importing :mod:`crude.ui` must
  work on a machine with no display, which is what
  :class:`~crude.runtime.modes` and every fake test rely on. The GL backend sits
  in :mod:`crude.ui.gl` and is reached through a factory that fails into
  :class:`RendererUnavailable` instead of an ``ImportError``.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
)

from ..errors import CrudeError
from . import geometry
from .geometry import Point, Rect, TAU, clamp, scale_of

# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class UIError(CrudeError):
    """Something was asked of the UI that cannot be done."""

    kind = "runtime"


class RendererUnavailable(UIError):
    """No GL renderer can be made here: no library, no display, no context.

    The plan's answer is never "fail the run": a project that wanted a window
    gets a console progress report and a line saying why, and a headless project
    does not notice the difference at all.
    """


class WindowUnavailable(RendererUnavailable):
    """A window could not be opened - the same fallback, one step further out."""


# --------------------------------------------------------------------------
# colour
# --------------------------------------------------------------------------


class RGBA(NamedTuple):
    """A colour as four floats from 0.0 to 1.0, alpha last."""

    red: float = 1.0
    green: float = 1.0
    blue: float = 1.0
    alpha: float = 1.0

    def with_alpha(self, alpha: float) -> "RGBA":
        return RGBA(self.red, self.green, self.blue, clamp(float(alpha), 0.0, 1.0))

    def premultiplied(self) -> "RGBA":
        """Colour scaled by its own alpha, the form the shader expects."""
        return RGBA(self.red * self.alpha, self.green * self.alpha,
                    self.blue * self.alpha, self.alpha)

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.red, self.green, self.blue, self.alpha)

    def as_bytes(self) -> Tuple[int, int, int, int]:
        return tuple(round(value * 255) for value in self.as_tuple())  # type: ignore[return-value]

    def __str__(self) -> str:
        return "#" + "".join(f"{byte:02x}" for byte in self.as_bytes())


WHITE = RGBA(1.0, 1.0, 1.0, 1.0)
BLACK = RGBA(0.0, 0.0, 0.0, 1.0)
TRANSPARENT = RGBA(0.0, 0.0, 0.0, 0.0)

#: the names a script or a skin token may use instead of a hex string
NAMED: Dict[str, RGBA] = {
    "white": WHITE,
    "black": BLACK,
    "transparent": TRANSPARENT,
    "red": RGBA(1.0, 0.2, 0.2, 1.0),
    "green": RGBA(0.2, 0.8, 0.3, 1.0),
    "blue": RGBA(0.25, 0.5, 1.0, 1.0),
    "yellow": RGBA(1.0, 0.9, 0.3, 1.0),
    "cyan": RGBA(0.3, 0.9, 1.0, 1.0),
    "magenta": RGBA(1.0, 0.3, 0.9, 1.0),
    "orange": RGBA(1.0, 0.6, 0.2, 1.0),
    "grey": RGBA(0.55, 0.55, 0.55, 1.0),
    "gray": RGBA(0.55, 0.55, 0.55, 1.0),
}

_HEX_GROUPS = {3: 1, 4: 1, 6: 2, 8: 2}


def _channel(text: str, digits: int) -> float:
    """One hex group as a float: ``ff`` is 255/255, ``f`` is 15/15."""
    return int(text, 16) / ((1 << (4 * digits)) - 1)


def as_color(value: Any) -> RGBA:
    """Whatever a caller had, as an :class:`RGBA`.

    Accepts an ``RGBA``, a 3- or 4-length tuple/list, ``"#rgb"``, ``"#rgba"``,
    ``"#rrggbb"``, ``"#rrggbbaa"`` and the names in :data:`NAMED`. A number is
    grey-with-full-alpha, because a script that says ``STYLE(x, "bg", 0)`` means
    black, not invisible. Anything else is a :class:`UIError` that says what it
    was given - a wrong colour is a mistake in a project's own file.
    """
    if isinstance(value, RGBA):
        return value
    if value is None:
        return WHITE
    if isinstance(value, str):
        text = value.strip()
        named = NAMED.get(text.lower())
        if named is not None:
            return named
        if text.startswith("#"):
            body = text[1:]
            digits = _HEX_GROUPS.get(len(body))
            if digits and all(char in "0123456789abcdefABCDEF" for char in body):
                parts = [body[index * digits:(index + 1) * digits]
                         for index in range(len(body) // digits)]
                red, green, blue = (_channel(part, digits) for part in parts[:3])
                alpha = _channel(parts[3], digits) if len(parts) > 3 else 1.0
                return RGBA(red, green, blue, alpha)
        raise UIError(f"'{value}' is not a colour; use '#rrggbb' or a name "
                      f"like {', '.join(sorted(NAMED))}")
    if isinstance(value, (tuple, list)):
        if len(value) == 3:
            red, green, blue = (float(part) for part in value)
            return RGBA(red, green, blue, 1.0)
        if len(value) == 4:
            red, green, blue, alpha = (float(part) for part in value)
            return RGBA(red, green, blue, alpha)
        raise UIError(f"a colour is three or four numbers, not {len(value)}")
    if isinstance(value, (int, float)):
        grey = float(value)
        if 0.0 <= grey <= 1.0:
            return RGBA(grey, grey, grey, 1.0)
        return RGBA(0.0, 0.0, 0.0, 1.0)
    raise UIError(f"{type(value).__name__} is not something a colour can be made of")


# --------------------------------------------------------------------------
# one recorded draw
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DrawOp:
    """A single normalised draw, the unit every renderer emits and every test reads.

    ``params`` holds exactly the numbers the method was documented to settle on,
    never the caller's raw arguments, so two runs of the same layout compare equal.
    ``clip`` is the rect that was open when the op was emitted (``None`` for the
    whole frame), attached rather than implied by position: a recording can be
    replayed by a backend whose clip stack is a scissor, a mask or nothing at all.
    """

    #: one of :data:`RENDERER_OPS`
    name: str
    params: Mapping[str, Any] = field(default_factory=dict)
    #: ``(x, y, width, height)`` in logical pixels, or ``None`` for the frame
    clip: Optional[Rect] = None

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def moved(self, dx: float, dy: float) -> "DrawOp":
        """The same op shifted by a device offset, for a backend that batches by hand."""
        params = dict(self.params)
        for key in ("x", "y", "cx", "cy", "x1", "y1", "x2", "y2"):
            if key in params:
                params[key] = params[key] + (dx if key.startswith("x") else dy)
        clip = self.clip
        if clip is not None:
            clip = (clip[0] + dx, clip[1] + dy, clip[2], clip[3])
        return DrawOp(self.name, params, clip)

    def as_dict(self) -> Dict[str, Any]:
        return {"op": self.name, "clip": self.clip, **self.params}

    def __str__(self) -> str:
        return f"{self.name} {self.params}"


#: every op a renderer may emit; :data:`DRAW_METHODS` is the other half of the contract
RENDERER_OPS: Tuple[str, ...] = (
    "clear", "rect", "quad", "line", "circle", "arc", "polyline", "polygon",
    "text", "atlas",
)

#: the public draw methods, each of which must emit exactly one op of its own name
DRAW_METHODS: Tuple[str, ...] = (
    "clear", "rect", "quad", "line", "circle", "arc", "polyline", "polygon",
    "text", "atlas",
)

#: how a stroke ends: a half-disc of its own width, or square at the last point
CAPS = ("round", "flat")

DEFAULT_TEXT_SIZE = 12.0
DEFAULT_THICKNESS = 1.0


# --------------------------------------------------------------------------
# textures
# --------------------------------------------------------------------------


def texture_key(path: str, *, mtime: Optional[int] = None) -> str:
    """The cache key for an image file: its absolute path and its own freshness.

    A file that was rewritten in place gets a new key, so an atlas a project
    edited while a window was open cannot stay the old one forever. An unknown
    file still gets a key - a fake renderer has nothing to stat, and a missing
    file is the GL loader's to report, not the key's.
    """
    absolute = os.path.abspath(os.fspath(path))
    if mtime is None:
        try:
            mtime = os.stat(absolute).st_mtime_ns
        except OSError:
            mtime = 0
    return f"{absolute}#{mtime}"


def texture_size_of(array: Any) -> Tuple[float, float]:
    """``(width, height)`` of whatever was uploaded, numpy or PIL or nothing."""
    shape = getattr(array, "shape", None)
    if shape is not None and len(shape) >= 2:
        return (float(shape[1]), float(shape[0]))
    size = getattr(array, "size", None)
    if size is not None:
        return (float(size[0]), float(size[1]))
    return (0.0, 0.0)


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------


class Renderer(ABC):
    """The surface every backend implements and every widget is allowed to use.

    Subclasses implement :meth:`emit` and, when they need to, the ``on_*`` hooks;
    they should not redefine the draw methods, because those are where the
    argument rules live. Coordinates are logical pixels, top-left origin, y down -
    :mod:`crude.ui.geometry` states why, and GLFW's cursor position is in the
    same space, so a widget's hit test needs no conversion.
    """

    #: every op name a frame may hold; a contract test reads it with
    #: :data:`DRAW_METHODS` and fails if one of them grows without the other
    OPS = RENDERER_OPS

    def __init__(self, name: str = "renderer") -> None:
        self.name = name
        #: the frame's logical size, and its size in device pixels
        self.width = 0.0
        self.height = 0.0
        self.fb_width = 0
        self.fb_height = 0
        self._clips: List[Rect] = []
        self._frames = 0
        self._ops = 0
        self._begun = False
        #: texture key -> ``(width, height)``, the sizes a layout may ask for
        self._textures: Dict[str, Tuple[float, float]] = {}
        #: the icon texture, made on the first glyph and kept for this DPI
        self._glyphs: Optional["GlyphAtlas"] = None
        self._glyph_key = ""

    # -- a frame ----------------------------------------------------------
    def begin(self, width: float, height: float,
              fb_width: int = None, fb_height: int = None) -> None:
        """Start a frame of ``width`` x ``height`` logical pixels.

        ``fb_width``/``fb_height`` are the framebuffer's real size and only ever
        feed the viewport, the scissor and :attr:`dpr`; no caller scales its own
        coordinates by hand. Left unsaid, one device pixel is one logical pixel.
        """
        self.width = float(width)
        self.height = float(height)
        self.fb_width = int(fb_width or width)
        self.fb_height = int(fb_height or height)
        self._clips = []
        self._ops = 0
        self._begun = True
        self.on_frame_begin()

    def end(self) -> int:
        """Finish the frame and return how many ops it held."""
        self.on_frame_end()
        self._frames += 1
        self._begun = False
        return self._ops

    @property
    def dpr(self) -> float:
        """Device pixels per logical pixel, from the last :meth:`begin`."""
        return scale_of(self.fb_width, self.width)

    @property
    def frame_count(self) -> int:
        return self._frames

    @property
    def op_count(self) -> int:
        """Ops in the frame being drawn, or in the last one finished."""
        return self._ops

    # -- clipping ---------------------------------------------------------
    @property
    def clip(self) -> Optional[Rect]:
        """The rect currently open, or ``None`` for the whole frame."""
        return self._clips[-1] if self._clips else None

    def push_clip(self, x: float, y: float, width: float, height: float) -> Rect:
        """Cut the frame to ``width`` x ``height`` from ``x, y``, on top of any clip.

        Every clip is the intersection of the whole stack, so a widget inside two
        panels is cut by both - the old renderer kept only the top one, which made
        a panel inside a panel able to draw outside its parent.
        """
        rect = (float(x), float(y), float(width), float(height))
        if self._clips:
            rect = geometry.clip_overlap(self._clips[-1], rect)
        self._clips.append(rect)
        self.on_clip_pushed(rect)
        return rect

    def pop_clip(self) -> Rect:
        """Leave the innermost clip; the one it returns is the one that closed."""
        if not self._clips:
            raise UIError(f"{self.name}: a clip was popped with none open")
        rect = self._clips.pop()
        self.on_clip_popped(rect)
        return rect

    @contextmanager
    def clipped(self, x: float, y: float, width: float, height: float) -> Iterator[Rect]:
        """``with r.cliprect(box): draw`` - the clip closes even if the body raised."""
        rect = self.push_clip(x, y, width, height)
        try:
            yield rect
        finally:
            self.pop_clip()

    # -- the ops ----------------------------------------------------------
    def clear(self, color: Any = TRANSPARENT) -> None:
        """Wipe the frame to ``color``; every frame starts with one."""
        self._emit("clear", {"color": as_color(color)})

    def rect(self, x: float, y: float, width: float, height: float,
             color: Any = WHITE, radius: float = 0.0, rotation: float = 0.0) -> None:
        """A box, optionally rounded and turned about its own centre.

        A negative size is a box drawn the other way, not an error, so a layout
        that ends up inverted still shows: the corners move and the numbers go
        positive. ``radius`` cannot exceed half the shorter side, because past
        that a rounded box is two circles and a gap.
        """
        x, y, width, height = _box(x, y, width, height)
        radius = clamp(float(radius), 0.0, min(width, height) * 0.5)
        self._emit("rect", {
            "x": x, "y": y, "width": width, "height": height,
            "color": as_color(color), "radius": radius, "rotation": float(rotation),
        })

    def quad(self, p1: Point, p2: Point, p3: Point, p4: Point,
             color: Any = WHITE) -> None:
        """Four corners, filled - the one primitive everything else can be."""
        points = tuple(_point(point) for point in (p1, p2, p3, p4))
        self._emit("quad", {"points": points, "color": as_color(color)})

    def line(self, p1: Point, p2: Point, thickness: float = DEFAULT_THICKNESS,
             color: Any = WHITE, thickness2: float = None) -> None:
        """A stroke from ``p1`` to ``p2``, round-capped, or tapered when told so.

        ``thickness2`` makes the far end a different width - a meter needle, a
        velocity stroke - and a taper has no room for a capsule, so the renderer
        draws the quad plus two flat caps instead of one rounded box.
        """
        (x1, y1), (x2, y2) = _point(p1), _point(p2)
        thick = max(0.0, float(thickness))
        second = None if thickness2 is None else max(0.0, float(thickness2))
        self._emit("line", {
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "thickness": thick, "thickness2": second, "color": as_color(color),
        })

    def circle(self, cx: float, cy: float, radius: float,
               color: Any = WHITE) -> None:
        """A filled disc at ``cx, cy`` - the shape a knob, a dot and a lamp are."""
        self._emit("circle", {
            "cx": float(cx), "cy": float(cy),
            "radius": max(0.0, float(radius)), "color": as_color(color),
        })

    def arc(self, cx: float, cy: float, radius: float,
            thickness: float = DEFAULT_THICKNESS, start: float = 0.0,
            end: float = TAU, color: Any = WHITE, caps: str = "round") -> None:
        """A ring segment, counter-clockwise from ``start`` to ``end``, in radians.

        Angles are absolute directions with ``0`` to the right and ``y`` down, so a
        meter that reads "from 135 degrees to 45 degrees" gets the long way round:
        ``end`` is where the stroke stops, and the sweep is what is painted. A
        sweep of a full turn closes the ring. ``caps`` decides the two tips.
        """
        sweep = geometry.arc_sweep(float(start), float(end))
        self._emit("arc", {
            "cx": float(cx), "cy": float(cy), "radius": max(0.0, float(radius)),
            "thickness": max(0.0, float(thickness)),
            "start": geometry.wrap_angle(float(start)), "sweep": sweep,
            "color": as_color(color), "caps": _cap(caps),
        })

    def polyline(self, points: Sequence[Point], thickness: float = DEFAULT_THICKNESS,
                 color: Any = WHITE, closed: bool = False) -> None:
        """A stroke through every point; round joins, and closed joins back."""
        self._emit("polyline", {
            "points": tuple(_point(point) for point in points),
            "thickness": max(0.0, float(thickness)),
            "color": as_color(color), "closed": bool(closed),
        })

    def polygon(self, points: Sequence[Point], color: Any = WHITE) -> None:
        """A filled shape through every point - a triangle when there are three."""
        self._emit("polygon", {
            "points": tuple(_point(point) for point in points),
            "color": as_color(color),
        })

    def text(self, x: float, y: float, string: str, size: float = DEFAULT_TEXT_SIZE,
             color: Any = WHITE, align: str = "left") -> None:
        """One line of text, its left, middle or right edge at ``x``.

        ``align`` is resolved here, against :meth:`measure`, so a centred label
        lands in the same place whether the face is real, estimated or absent.
        ``y`` is the top of the line, because the whole UI is top-down.
        """
        text = "" if string is None else str(string)
        size = max(0.0, float(size))
        align = geometry.alignment(align)
        # only a label that is not left-placed has to be measured first, and a
        # renderer with a real face is asked for the width, not guessed at
        offset = (geometry.align_x_offset(self.measure(text, size)[0], align)
                  if align != "left" else 0.0)
        self._emit("text", {
            "x": float(x) - offset, "y": float(y),
            "text": text, "size": size, "color": as_color(color), "align": align,
        })

    def measure(self, string: str, size: float) -> Tuple[float, float]:
        """How wide and tall ``string`` would be: the estimate until a face is loaded."""
        return geometry.text_bounds(string, size)

    def atlas(self, x: float, y: float, width: float, height: float, texture: Any,
              uv: Tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
              color: Any = WHITE, rotation: float = 0.0) -> None:
        """Draw part of one texture into one box - every glyph, icon and image.

        ``uv`` is ``(u0, v0, u1, v1)`` in the texture's own 0..1 space, so a glyph
        is a quad and not a file, and ``color`` tints it: an atlas is stored as
        white-on-transparent and every icon is one texture with a different tint,
        which is why a toggle can change colour without touching the GPU again.
        """
        x, y, width, height = _box(x, y, width, height)
        u0, v0, u1, v1 = (clamp(float(value), 0.0, 1.0) for value in uv)
        self._emit("atlas", {
            "x": x, "y": y, "width": width, "height": height,
            "texture": texture, "uv": (min(u0, u1), min(v0, v1),
                                       max(u0, u1), max(v0, v1)),
            "color": as_color(color), "rotation": float(rotation),
        })

    # -- replay ----------------------------------------------------------
    def replay(self, ops: Sequence[DrawOp]) -> int:
        """Draw a recorded list of ops again, exactly as it was recorded.

        A cached paint is a list of finished :class:`DrawOp` objects, and replaying
        it must not go back through the draw methods: :meth:`text` resolves its
        alignment against :meth:`measure` and stores the *shifted* ``x``, so a
        second pass would shift the same label twice as soon as the text width
        moved under it. The numbers are already decided, so they are taken as they
        stand and only the clip is re-opened around them.

        Clips are grouped, not stacked: runs of ops that share one rect push it
        once, and each rect is intersected with whatever the caller already had
        open, so a cached widget paint cannot escape the box it was recorded in.
        Returns how many ops the frame was told about, which is what a widget's
        paint count and ``--stats`` read.
        """
        for op in ops:
            if op.name not in RENDERER_OPS:
                raise UIError(f"{self.name}: '{op.name}' is not an op a renderer "
                              f"can replay (known: {', '.join(RENDERER_OPS)})")
        before = self._ops
        depth = 0
        current: Optional[Rect] = None
        try:
            for op in ops:
                if op.clip != current:
                    while depth:
                        self.pop_clip()
                        depth -= 1
                    current = None
                    if op.clip is not None:
                        self.push_clip(*op.clip)
                        depth = 1
                        current = op.clip
                self._emit(op.name, dict(op.params))
        finally:
            # a failed draw must not leave the frame clipped forever, which is
            # the same promise :meth:`clipped` makes
            while depth:
                self.pop_clip()
                depth -= 1
        return self._ops - before

    # -- icons -----------------------------------------------------------
    @property
    def glyphs(self) -> Optional["GlyphAtlas"]:
        """The renderer's icon texture, or ``None`` until an icon was drawn.

        One atlas per renderer, cut for the device ratio of the last frame, because
        an icon is rasterized at the size it is shown at and a 1x texture stretched
        over a 2x display is a blurry icon. Nothing is built for a project that
        never asks for one.
        """
        return self._glyphs

    def _glyph_atlas(self) -> "GlyphAtlas":
        """The atlas for this frame's device ratio, rebuilt when the ratio moves."""
        # late, because :mod:`crude.ui.glyphs` imports :class:`UIError` from here:
        # the cycle only exists at import time and this is not import time
        from .glyphs import GlyphAtlas
        atlas = self._glyphs
        if atlas is None or atlas.dpr != self.dpr:
            if self._glyph_key:
                # the old texture is nobody's to draw again, and a monitor change
                # is the one thing that can leave it behind
                self.forget_texture(self._glyph_key)
            atlas = GlyphAtlas(dpr=self.dpr)
            self._glyphs = atlas
            self._glyph_key = atlas.texture_key
        return atlas

    def glyph(self, x: float, y: float, width: float, height: float, name: str,
              color: Any = WHITE, rotation: float = 0.0, *,
              size: Optional[float] = None, weight: float = 1.0) -> None:
        """Draw one generated icon into a box - one quad out of the shared texture.

        ``name`` is a glyph from :mod:`crude.ui.glyphs` (``"play"``,
        ``"chevron-r"``); ``size`` is the side to rasterize it at when the box is
        not square, and defaults to the box's shorter side; ``weight`` thickens every
        stroke in it, which is how a bolder icon costs no more than a number.

        This is not an op of its own: it is one :meth:`atlas` call, which is the
        point of the atlas - an icon bar is one texture bind and a quad each, and a
        tint is a multiply rather than a file. A name that is not in the set draws
        the placeholder and is reported once by the atlas, so a typo in a script is
        an empty box on screen, not a crash halfway through a load.
        """
        self._glyph_atlas().draw(self, x, y, width, height, name, color=color,
                                 rotation=rotation, size=size, weight=weight)

    def glyph_texture(self, name: str, size: float, weight: float = 1.0
                      ) -> Tuple[str, Tuple[float, float, float, float]]:
        """The texture key and UV box of an icon, for a caller that draws it itself.

        ``ICON_TEXTURE`` in a script, and anything that wants an icon inside a
        :meth:`quad` of its own rather than in a square box.
        """
        atlas = self._glyph_atlas()
        cell = atlas.cell(name, size, weight)
        key = atlas.upload(self)
        return (key, cell.uv(atlas.width, atlas.height) if cell
                else (0.0, 0.0, 0.0, 0.0))

    # -- textures ---------------------------------------------------------
    def texture(self, path: str) -> str:
        """The key for an image file, loading it once; ``""`` for nothing.

        Nothing in this module reads the file: :meth:`on_texture` is where a
        backend that can decode one does, and a fake backend simply does not.
        """
        if not path:
            return ""
        key = texture_key(path)
        if key not in self._textures:
            self._textures[key] = (0.0, 0.0)
            self.on_texture(key, path)
        return key

    def upload_texture(self, key: str, array: Any) -> str:
        """Give a texture its pixels from memory - how a scope's ring updates.

        The key is the caller's, not a file's, because the thing being drawn is
        not on disk: :class:`crude.ui.ring.RingTexture` owns one key and reuses
        it every frame, and an upload never re-decodes or re-allocates.
        """
        key = str(key)
        self._textures[key] = texture_size_of(array)
        self.on_upload(key, array)
        return key

    def invalidate_texture(self, path: str) -> None:
        """Forget an image, so the next :meth:`texture` reads the file again."""
        key = texture_key(path)
        if self._textures.pop(key, None) is not None:
            self.on_invalidate(key)

    def forget_texture(self, key: str) -> None:
        """Let go of a texture by the key it was uploaded under.

        :meth:`invalidate_texture` works from a file path, which is right for an
        image on disk and no use at all for pixels that never were one: a glyph
        atlas recuts itself for another device ratio, and the texture it was using
        is then memory nobody will ever bind again.
        """
        key = str(key)
        if self._textures.pop(key, None) is not None:
            self.on_invalidate(key)

    def texture_size(self, key: str) -> Tuple[float, float]:
        return self._textures.get(key, (0.0, 0.0))

    def texture_keys(self) -> Tuple[str, ...]:
        return tuple(self._textures)

    def read_pixels(self, x: float = 0.0, y: float = 0.0, width: float = 0.0,
                    height: float = 0.0, *, premultiplied: bool = False) -> Any:
        """Pixels back from the frame, or ``None`` where nothing can be read.

        Only a real context can answer, so the contract's default is silence
        rather than a failure: a test that wants to look at the picture is marked
        ``requires_gl``, and one that wants to know the renderer was asked is not.

        A read happens inside a frame, and a backend that batches its vertices
        asks itself to put what has been emitted so far on the GPU first -
        otherwise the answer would be about the frame before this one.
        """
        if not self._begun:
            raise UIError(f"{self.name}: pixels cannot be read outside a frame")
        width = width or self.width
        height = height or self.height
        return self.on_read_pixels(float(x), float(y), float(width), float(height),
                                   bool(premultiplied))

    # -- what a backend owes ---------------------------------------------
    @abstractmethod
    def emit(self, op: DrawOp) -> None:
        """Take one finished op. Every draw method above ends here, exactly once."""

    def on_frame_begin(self) -> None:
        """The frame's size is known and the clip stack is empty."""

    def on_frame_end(self) -> None:
        """Everything emitted has to be on screen now: flush, swap, count."""

    def on_clip_pushed(self, rect: Rect) -> None:
        """A clip opened; ``rect`` is already the intersection of the stack."""

    def on_clip_popped(self, rect: Rect) -> None:
        """A clip closed; an empty stack means the whole frame is open again."""

    def on_texture(self, key: str, path: str) -> None:
        """A file was asked for by key and has not been read yet."""

    def on_upload(self, key: str, array: Any) -> None:
        """Pixels were handed over for ``key``, which may already exist.

        A backend that batches its vertices has to draw what it has already been
        given before a texture *changes shape* under it: the UV numbers in flight
        are fractions of the size the texture had at the time, and a resized one
        means those quads sample the wrong part of the picture until the frame is
        over. Growing pixels is the point of the call; keeping the frame honest is
        the duty that comes with it.
        """

    def on_invalidate(self, key: str) -> None:
        """The key is gone; a backend holding GPU memory for it may let go.

        Only after anything that still binds it has been drawn, for the same reason.
        """

    def on_read_pixels(self, x: float, y: float, width: float, height: float,
                       premultiplied: bool) -> Any:
        return None

    def stats(self) -> Dict[str, Any]:
        """What :meth:`crude run --stats` prints: frame and op counts, texture count."""
        return {
            "renderer": self.name,
            "frames": self._frames,
            "ops": self._ops,
            "textures": len(self._textures),
            "clips": len(self._clips),
            "dpr": round(self.dpr, 3),
        }

    # -- the one internal path -------------------------------------------
    def _emit(self, name: str, params: Dict[str, Any]) -> None:
        """Normalise, refuse a draw outside a frame, and hand the op over."""
        if not self._begun:
            raise UIError(f"{self.name}: '{name}' was drawn outside a frame; "
                          f"begin(width, height) comes first")
        self._ops += 1
        self.emit(DrawOp(name, params, self.clip))


def _box(x: float, y: float, width: float, height: float) -> Tuple[float, float, float, float]:
    """A rect with positive size and its top-left corner wherever it should be."""
    x, y, width, height = (float(value) for value in (x, y, width, height))
    if width < 0:
        x, width = x + width, -width
    if height < 0:
        y, height = y + height, -height
    return (x, y, width, height)


def _point(point: Point) -> Tuple[float, float]:
    try:
        x, y = point
        return (float(x), float(y))
    except (TypeError, ValueError):
        raise UIError(f"'{point!r}' is not a point, which is two numbers")


def _cap(caps: Any) -> str:
    text = str(caps or "round").strip().lower()
    if text not in CAPS:
        raise UIError(f"there is no end cap '{text}' (known: {', '.join(CAPS)})")
    return text


__all__ = [
    "CAPS", "DRAW_METHODS", "DEFAULT_TEXT_SIZE", "DEFAULT_THICKNESS", "DrawOp",
    "NAMED", "RENDERER_OPS", "Renderer", "RendererUnavailable", "RGBA", "TAU",
    "UIError", "WHITE", "BLACK", "TRANSPARENT", "WindowUnavailable",
    "as_color", "clamp", "texture_key", "texture_size_of",
]
