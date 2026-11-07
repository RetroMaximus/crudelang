"""A script's own face: the painter's canvas, and the cache its ops land in.

:mod:`crude.ui.skin` decides what a catalog kind looks like and
:mod:`crude.ui.widget` draws it. This module is for the widget neither of them
can cover - the one a project defines for itself with ``WIDGET_DEFINE`` and paints
with ``ON_PAINT``: a stepper, a beat grid, a tape transport, a phase knob whose
pointer is a triangle nobody has a role for.

Three rules carry it:

* **The painter is run once, and its ops are kept.** A script's ``ON_PAINT`` runs
  on the interpreter thread, so a frame that re-ran every painter would be a frame
  whose cost is the sum of the project's Python instead of the sum of the GPU's
  batches. :class:`PaintList` records what the painter emitted into a
  :class:`_Sponge` and replays the list until the widget's props, its box, the
  skin or the device ratio move - which is exactly what
  :meth:`~crude.ui.widget.Widget.paint_key` names, plus the DPI. The first frame
  takes the same path as every cached one - record, then
  :meth:`~crude.ui.base.Renderer.replay` - so a cached frame can never disagree
  with the frame that produced it.
* **A painter works in its own box.** ``P_RECT(p, 0, 0, 10, 10)`` is the widget's
  top-left corner, not the window's, and everything it draws is clipped to the
  widget. That is what :class:`Canvas` is for: it translates, it clips, and it
  resolves a *role* through the skin so a script never names a colour and still
  gets the right one on both themes.
* **A painter that fails is not cached and is not silent.** A half-recorded list
  of ops would be a widget drawn with the bottom half of its face forever, so a
  failed paint is thrown away and the box is drawn in the ``placeholder`` role -
  red-edged, deliberate - and the handler bridge's three-strike mute still
  counts. Nothing is raised into the frame.

:func:`region_of` lives here rather than in :mod:`crude.ui.handlers` because it is
the answer to "what did the script say about where it is", which is a geometry
question with a language-shaped hole in it: the value may be a plain Python one
or whatever the interpreter's own number, string and list happen to be, and this
layer is not allowed to import them.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .base import DrawOp, RGBA, Renderer, UIError, as_color
from .geometry import Point, Rect, TAU, clip_overlap
from .skin import Appearance, Skin, role_names
from .widget import Widget

__all__ = ["Canvas", "PaintList", "region_of", "in_region",
           "MAX_PAINT_WIDGETS", "MAX_PAINT_OPS", "FILL", "INK", "LINE"]

#: how many widgets may be painted by a script before the plan's guard rail is
#  said out loud. The number is not a limit - nothing is refused - and it is the
#  point where a frame's cost stops being the renderer's and starts being the
#  project's own Python, which is the thing a person on a laptop notices first.
MAX_PAINT_WIDGETS = 64

#: ops one widget may emit in one frame before the same warning is raised about
#  it. Two hundred rounded rectangles is a picture rather than a control, and a
#  project that means it can say so by raising the number on its own PaintList.
MAX_PAINT_OPS = 200

#: every word a colour question may be answered with, roles and aliases together
_ROLE_WORDS = frozenset(role_names())

#: the three channels a role has, and the primitives that ask for each. A filled
#  shape wants the part's own ``fill``, a stroke wants the line somebody drew
#  round it, and a word or an icon wants the ``ink`` that reads on top of it.
FILL, INK, LINE = "fill", "ink", "border"

_CHANNEL_OF = {
    "rect": FILL, "circle": FILL, "image": FILL,
    "line": LINE, "arc": LINE, "poly": LINE,
    "text": INK, "glyph": INK,
}


def _is_role(value: Any) -> bool:
    """Whether a script's word means a role rather than a colour."""
    said = str(value or "").strip().lower()
    return said in _ROLE_WORDS or said.replace(" ", "-").replace("_", "-") \
        in _ROLE_WORDS


# --------------------------------------------------------------------------
# the recorder
# --------------------------------------------------------------------------


class _Sponge(Renderer):
    """A renderer that keeps what it is handed instead of drawing it.

    Being a real :class:`~crude.ui.base.Renderer` is the point: the numbers a
    painter's calls produce are normalised, validated and clipped by the same
    contract the window will later replay them through, so a cached op is exactly
    the op that was meant. Text is the exception worth naming - :meth:`measure`
    is delegated to the renderer the frame is actually being drawn with, because
    a centred label baked against an estimate would not be centred once the real
    face arrived.
    """

    def __init__(self, delegate: Optional[Renderer] = None,
                 name: str = "painter") -> None:
        super().__init__(name)
        #: the ops recorded, in order, each carrying the clip it was drawn under
        self.ops: List[DrawOp] = []
        #: the renderer the frame is going to, for text widths and textures
        self.delegate = delegate

    def emit(self, op: DrawOp) -> None:
        self.ops.append(op)

    def measure(self, string: str, size: float) -> Tuple[float, float]:
        held = self.delegate
        if held is not None:
            return held.measure(string, size)
        return super().measure(string, size)

    def reset(self) -> None:
        self.ops.clear()


# --------------------------------------------------------------------------
# what a painter draws with
# --------------------------------------------------------------------------


class Canvas:
    """One widget's own box, in its own coordinates, with its colours resolved.

    The object an ``ON_PAINT`` handler is handed as ``p``, and the only thing the
    ``P_*`` primitives accept. ``(0, 0)`` is the widget's top-left, y goes down,
    and everything lands inside the widget: the base clip is the intersection of
    its box and the clip its parents left it with, so a painter cannot paint over
    the panel next to it any more than a widget can.

    A colour is a role. ``"accent"`` is the accent *as this widget is wearing it*,
    resolved at the widget's own size and against its own state, so a script-drawn
    toggle answers the pointer without containing an ``if hovered``. A role may be
    left out, and then the widget's own role is used; anything that is not a role
    is read as a colour (``"#ff0000"``, ``"red"``), which is how a one-off marker
    gets drawn without a theme being forked for it.
    """

    def __init__(self, target: Renderer, widget: Widget,
                 renderer: Renderer, skin: Skin,
                 clip: Optional[Rect] = None) -> None:
        #: where the ops go: a :class:`_Sponge` while the paint is being recorded
        self.target = target
        #: the widget whose face is being drawn
        self.widget = widget
        #: where textures and text widths come from - the renderer of the frame
        self.renderer = renderer
        self.skin = skin
        #: the skin at this widget's own ``size``, which is what every role here
        #  resolves through, so a painter's font matches the label next to it
        self.styled = widget.style(skin)
        left, top, width, height = widget.screen_box
        #: the widget's absolute origin: what a local number is added to
        self.left = float(left)
        self.top = float(top)
        #: the widget's box, which is the coordinate space a script is working in
        self.width = float(width)
        self.height = float(height)
        #: the state the widget is in right now, already as the skin spells it
        self.state = widget.state_name()
        #: the role a painter gets when it does not name one
        self.role = widget.role
        #: the rect everything here is clipped to, absolute. Named apart from the
        #  :meth:`clip` verb below on purpose: an attribute that shadows a method is
        #  how ``P_CLIP`` becomes a ``TypeError`` on a tuple
        self.base_clip = clip if clip is not None else widget.clip
        self._depth = 0

    # -- the arithmetic ---------------------------------------------------
    @property
    def depth(self) -> int:
        """How many ``P_CLIP`` boxes this painter has opened and not closed."""
        return self._depth

    @property
    def ops(self) -> int:
        """How many ops this canvas has recorded or drawn so far."""
        return self.target.op_count

    def point(self, x: float, y: float) -> Point:
        """Local to absolute - the one translation a painter should not repeat."""
        return (self.left + float(x), self.top + float(y))

    def place(self, x: float, y: float,
              width: float, height: float) -> Tuple[float, float, float, float]:
        """A local box as an absolute one."""
        return (self.left + float(x), self.top + float(y),
                float(width), float(height))

    # -- the colours ------------------------------------------------------
    def _role(self, role: Any) -> Any:
        """Which role a primitive's colour question is really about.

        A word that is not a role is a colour, and a colour still needs metrics:
        a painter that says ``P_LINE(p, ..., "#ff0000")`` wants the width of the
        thing it is drawing on, not a default someone hardcoded here.
        """
        if role is None or not _is_role(role):
            return self.role or "surface"
        return role

    def look(self, role: Any = None) -> Appearance:
        """The whole resolved appearance behind one primitive."""
        return self.styled.resolve(self._role(role), self.state)

    def colour(self, role: Any = None, channel: str = FILL) -> RGBA:
        """One colour for one primitive, from a role word or from a colour.

        A role is asked in the widget's own state, so ``P_RECT(p, 0, 0, 8, 8,
        "toggle")`` over a hovered toggle is the hovered colour. A word that is
        not a role is a colour, and a colour is taken literally - which is the
        only honest exception to "a script never names a colour", and the one a
        project's own marker needs.
        """
        if role is not None and not _is_role(role):
            if isinstance(role, str) and not role.strip():
                role = None
            else:
                return as_color(role)
        return getattr(self.look(role), channel)

    def stroke(self, role: Any, width: Any) -> float:
        """A painter's number, or the role's own line width if it said nothing."""
        return self.look(role).width if width is None else max(0.0, float(width))

    def font(self, role: Any = None) -> float:
        """The line height a role is drawn at, so ``P_TEXT`` can be told nothing."""
        return self.look(role).font

    def radius(self, role: Any = None) -> float:
        return self.look(role).radius

    # -- the primitives ---------------------------------------------------
    def rect(self, x: float, y: float, width: float, height: float,
             radius: float = 0.0, role: Any = None, channel: str = FILL) -> None:
        """A box in the widget's own coordinates; ``radius`` rounds it."""
        self.target.rect(*self.place(x, y, width, height),
                         self.colour(role, channel), radius=float(radius))

    def circle(self, cx: float, cy: float, radius: float, role: Any = None,
               channel: str = FILL) -> None:
        """A filled disc at a local centre."""
        self.target.circle(*self.point(cx, cy), max(0.0, float(radius)),
                           self.colour(role, channel))

    def arc(self, cx: float, cy: float, radius: float, start: float = 0.0,
            end: float = None, width: float = None, role: Any = None) -> None:
        """A ring segment, in radians, counter-clockwise from ``start``.

        ``end`` left out is a whole ring, and ``width`` left out is the role's own
        border - which is what makes ``P_ARC(p, cx, cy, r, a, b, "knob")`` read as
        the knob it is drawn on rather than as a hairline.
        """
        self.target.arc(*self.point(cx, cy), max(0.0, float(radius)),
                        self.stroke(role, width), float(start),
                        TAU if end is None else float(end),
                        self.colour(role, LINE))

    def line(self, x1: float, y1: float, x2: float, y2: float,
             width: float = None, role: Any = None) -> None:
        """A stroke between two local points."""
        self.target.line(self.point(x1, y1), self.point(x2, y2),
                         self.stroke(role, width), self.colour(role, LINE))

    def poly(self, points: Sequence[Any], closed: bool = False,
             width: float = None, role: Any = None) -> None:
        """A stroke - or, told to, a shape - through local points.

        ``points`` is a flat list of numbers - ``[x1, y1, x2, y2, ...]`` - because
        a script's list is flatter than a tuple, and a list of pairs is accepted
        as well. A ``width`` of ``0`` fills the shape instead of stroking it, which
        is the one door a script needs for a triangle and needs no new argument.
        """
        said = tuple(self.point(*pair) for pair in _pairs(points))
        if width is not None and float(width) <= 0.0:
            self.target.polygon(said, self.colour(role, FILL))
            return
        self.target.polyline(said, self.stroke(role, width),
                             self.colour(role, LINE), closed=bool(closed))

    def text(self, x: float, y: float, string: Any, size: float = None,
             role: Any = None, align: str = "left") -> None:
        """One line of words, at a local position, in the role's own ink.

        ``size`` left out takes the role's font, so ``P_TEXT(p, 4, 4, "120")`` is
        the same height as the label beside it - which is the whole reason a
        painter is handed a skin rather than a number.
        """
        self.target.text(*self.point(x, y), "" if string is None else str(string),
                         self.look(role).font if size is None else float(size),
                         self.colour(role, INK), align)

    def glyph(self, x: float, y: float, width: float, height: float, name: str,
              role: Any = None, weight: float = 1.0) -> None:
        """One generated icon, from the frame's own atlas, at the role's ink.

        The texture key and the UV box are taken from the renderer the frame is
        being drawn with, not from the recorder, so a cached paint holds the live
        atlas. That is also why a device-ratio change is a paint-key change: an
        atlas that recuts itself invalidates every glyph baked out of the old one.
        """
        key, uv = self.renderer.glyph_texture(str(name or ""),
                                              max(1.0, min(float(width),
                                                           float(height))),
                                              float(weight))
        self.target.atlas(*self.place(x, y, width, height), key, uv,
                          self.colour(role, INK))

    def image(self, x: float, y: float, width: float, height: float,
              resource: Any, role: Any = None,
              uv: Tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
              ) -> None:
        """An image file, loaded once by the renderer and replayed by key.

        ``resource`` is a path; nothing here reads it. A renderer with no decoder
        answers an empty key and the draw is skipped, which is the same silence a
        missing file produces on the plate path rather than a crash at 60 Hz.
        """
        key = self.renderer.texture(str(resource or ""))
        if not key:
            return
        self.target.atlas(*self.place(x, y, width, height), key, uv,
                          self.colour(role, FILL))

    # -- clipping ---------------------------------------------------------
    def clip(self, x: float, y: float, width: float, height: float) -> Rect:
        """Cut the painter to a local box; closed by :meth:`clip_end`.

        Every clip is an intersection, so a ``P_CLIP`` inside the widget's box can
        only ever make the drawable area smaller - a painter that says
        ``P_CLIP(p, -10, -10, 1000, 1000)`` is still drawing one widget.
        """
        found = self.target.push_clip(*self.place(x, y, width, height))
        self._depth += 1
        return found

    def clip_end(self) -> Optional[Rect]:
        """Leave the innermost ``P_CLIP``; ``None`` when the painter left none."""
        if not self._depth:
            return None
        self._depth -= 1
        return self.target.pop_clip()

    # -- describing itself ------------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        return {"widget": self.widget.path(), "kind": self.widget.kind.name,
                "box": [round(value, 2) for value in
                        (0.0, 0.0, self.width, self.height)],
                "origin": (round(self.left, 2), round(self.top, 2)),
                "state": self.state, "role": self.role,
                "ops": self.target.op_count, "clips": self._depth}

    def __str__(self) -> str:
        return (f"Canvas({self.widget.path()} {self.width:g}x{self.height:g} "
                f"@ {self.left:g},{self.top:g} {self.state}, "
                f"{self.target.op_count} op(s))")

    def __repr__(self) -> str:
        return f"<Canvas {self.widget.path()!r}>"


def _pairs(points: Sequence[Any]) -> List[Point]:
    """A flat list of numbers, or a list of pairs, as points.

    Both spellings are a script's: a stepper's arrow is ``[0, 6, 4, 0, 8, 6]``
    and a shape a loop built is ``[[x, y], [x, y], ...]``. Anything else is an
    error that says what a point is, because a half-point is a shape with a
    corner missing and nobody means that.
    """
    said = list(points or ())
    if not said:
        raise UIError("a shape needs at least one point")
    if any(isinstance(part, (list, tuple)) or hasattr(part, "elements")
           for part in said):
        out: List[Point] = []
        for pair in said:
            held = _numbers(pair)
            if len(held) != 2:
                raise UIError(f"a point is two numbers, not {len(held)}")
            out.append((held[0], held[1]))
        return out
    flat = _numbers(said)
    if len(flat) % 2:
        raise UIError(f"a shape needs an even list of x,y numbers, not "
                      f"{len(flat)}")
    return [(flat[index], flat[index + 1]) for index in range(0, len(flat), 2)]


def _numbers(seq: Any) -> Tuple[float, ...]:
    """Every number in a sequence, language values unwrapped by duck-typing."""
    items = getattr(seq, "elements", seq)
    out: List[float] = []
    for item in items or ():
        held = getattr(item, "value", item)
        if isinstance(held, bool) or not isinstance(held, (int, float)):
            raise UIError(f"'{item}' is not a number, and a shape is made of "
                          "numbers")
        out.append(float(held))
    return tuple(out)


# --------------------------------------------------------------------------
# the cache
# --------------------------------------------------------------------------


class PaintList:
    """Every script painter's ops, kept until the widget says otherwise.

    One per :class:`~crude.ui.context.UIContext`, reached through its handler
    bridge. The key is :meth:`~crude.ui.widget.Widget.paint_key` - the widget,
    its props, its resolved box and the skin's digest - plus the device ratio,
    because the glyph atlas recuts itself when that moves and a cached
    :class:`~crude.ui.base.DrawOp` holding the old texture would sample an
    uploaded-then-forgotten image.

    Nothing is dropped on a rule: a repaint retires the entry the same widget had
    before, so a slider being dragged holds exactly one cached face at a time
    rather than one per pixel it has travelled. A widget leaving the tree is told
    here by name, and the whole cache goes with :meth:`clear`.
    """

    def __init__(self, log: Optional[Callable[[str], Any]] = None, *,
                 name: str = "ui", max_widgets: int = MAX_PAINT_WIDGETS,
                 max_ops: int = MAX_PAINT_OPS) -> None:
        self.name = str(name or "ui")
        self.log = log or (lambda message: None)
        #: per-widget op budget before the warning; a project may raise it
        self.max_ops = int(max_ops)
        #: how many widgets may be script-painted before the warning
        self.max_widgets = int(max_widgets)
        #: ``paint key -> recorded ops``, the cache itself
        self._cache: Dict[tuple, List[DrawOp]] = {}
        #: ``widget uid -> its current key``, so a repaint retires its own past
        self._keys: Dict[int, tuple] = {}
        #: uids the op budget has been said about already: once per widget
        self._loud: Set[int] = set()
        #: whether the widget-count warning has been said at all
        self._wide = False
        #: how many times a painter actually ran, and how many frames it did not
        self.records = 0
        self.replays = 0
        #: paints that were thrown away for a raising or muted handler
        self.failures = 0
        self.placeholders = 0

    # -- the one question -------------------------------------------------
    def key_of(self, widget: Widget, skin: Skin, renderer: Renderer) -> tuple:
        """The key this widget's ops are kept under for this frame."""
        return widget.paint_key(skin) + (round(renderer.dpr, 3),)

    def cached(self, widget: Widget) -> bool:
        """Whether this widget has ops sitting here right now."""
        return getattr(widget, "uid", widget) in self._keys

    def ops_for(self, widget: Widget) -> Tuple[DrawOp, ...]:
        """The recorded ops, for a report or a test; empty when uncached."""
        held = self._cache.get(self._keys.get(getattr(widget, "uid", widget)))
        return tuple(held) if held else ()

    def paint(self, widget: Widget, renderer: Renderer, skin: Skin,
              draw: Callable[[Canvas], Any]) -> int:
        """Draw one script-painted face; how many ops the frame got.

        ``draw`` is the handler bridge's job - it runs the registered ``ON_PAINT``
        functions into the :class:`Canvas` it is handed and answers whether every
        one of them succeeded. This method owns the rest: the cache, the record,
        the replay, and the box that is drawn when the painter could not.
        """
        key = self.key_of(widget, skin, renderer)
        held = self._cache.get(key)
        if held is not None:
            self.replays += 1
            return renderer.replay(held)
        return self.record(widget, renderer, skin, key, draw)

    def record(self, widget: Widget, renderer: Renderer, skin: Skin,
               key: tuple, draw: Callable[[Canvas], Any]) -> int:
        """Run the painter into a recorder, then cache it or throw it away."""
        if widget.width <= 0 or widget.height <= 0:
            return 0
        base = clip_overlap(widget.clip, widget.screen_box)
        sponge = _Sponge(renderer)
        sponge.begin(renderer.width, renderer.height,
                     renderer.fb_width, renderer.fb_height)
        sponge.push_clip(*base)
        canvas = Canvas(sponge, widget, renderer, skin, base)
        try:
            ok = bool(draw(canvas))
        except Exception as exc:  # a painter must not take the window down either
            # the handler bridge turns a script's own error into ``False`` before it
            # reaches here, so this is a painter that raised in Python - a bad role
            # word, a shape with one point - which is the same placeholder and the
            # same log line rather than a frame with half its widgets missing
            ok = False
            self.log(f"{self.name}: painting '{widget.path()}' failed: {exc}")
        finally:
            # a painter that opened a clip and then raised must not leave the
            # recorder clipped, because the list it holds is the one being cached
            while canvas.depth:
                canvas.clip_end()
        held = list(sponge.ops)
        if not ok:
            # nothing half-drawn is ever cached: the widget retries next frame,
            # and the placeholder is the picture of a face nobody could finish
            self.failures += 1
            return self.placeholder(widget, renderer, skin)
        self.store(key, widget, held)
        self.records += 1
        self.warn(widget, len(held))
        return renderer.replay(held)

    def placeholder(self, widget: Widget, renderer: Renderer,
                    skin: Skin) -> int:
        """The red-edged box a failing painter leaves in its place.

        The ``placeholder`` role rather than a hard-coded red, because the plan's
        point about roles is that a project can decide what a failure looks like -
        and its line token is the ``bad`` colour in every theme that ships.
        """
        if widget.width <= 0 or widget.height <= 0:
            return 0
        found = widget.style(skin).resolve("placeholder", "normal")
        left, top, width, height = widget.screen_box
        before = renderer.op_count
        edge = min(found.width, width * 0.5, height * 0.5)
        if edge > 0:
            renderer.rect(left, top, width, height, found.border,
                          radius=found.radius)
            renderer.rect(left + edge, top + edge,
                          max(0.0, width - 2 * edge),
                          max(0.0, height - 2 * edge), found.fill,
                          radius=found.radius - edge if found.radius else 0.0)
        else:
            renderer.rect(left, top, width, height, found.border,
                          radius=found.radius)
        self.placeholders += 1
        return renderer.op_count - before

    # -- keeping it tidy ---------------------------------------------------
    def store(self, key: tuple, widget: Widget, ops: List[DrawOp]) -> None:
        """Put one finished paint in the cache, retiring what it replaces."""
        was = self._keys.get(widget.uid)
        if was is not None and was != key:
            self._cache.pop(was, None)
        self._cache[key] = ops
        self._keys[widget.uid] = key

    def invalidate(self, held: Any) -> int:
        """Forget a widget's cached face, so the next frame repaints it.

        ``PAINT_REQUEST(w)`` is this call, and a script that draws something the
        cache cannot see - a value that lives outside its own props, a picture
        that changes with the audio - has to say so here rather than wait for a
        prop it does not have. Takes a widget or a bare uid.
        """
        uid = getattr(held, "uid", held)
        key = self._keys.pop(uid, None)
        self._loud.discard(uid)
        if key is None:
            return 0
        self._cache.pop(key, None)
        return 1

    def clear(self) -> int:
        """Drop every cached face; how many went."""
        gone = len(self._cache)
        self._cache.clear()
        self._keys.clear()
        self._loud.clear()
        self._wide = False
        return gone

    # -- the plan's guard rails --------------------------------------------
    def warn(self, widget: Widget, ops: int) -> bool:
        """Say the two warnings the plan asks for, each of them once.

        Once per widget for the op count, once per list for the number of
        script-painted widgets: a frame that is over budget is over budget every
        frame, and a log line per frame is how a warning stops being read.
        """
        said = False
        if ops > self.max_ops and widget.uid not in self._loud:
            self._loud.add(widget.uid)
            message = (f"{self.name}: '{widget.path()}' painted {ops} op(s) in "
                       f"one frame; the budget is {self.max_ops}. Cache it, "
                       "draw less, or raise PaintList.max_ops")
            self.log(message)
            said = True
        if len(self._keys) > self.max_widgets and not self._wide:
            self._wide = True
            message = (f"{self.name}: {len(self._keys)} widgets paint "
                       f"themselves; the budget is {self.max_widgets}. Each "
                       "one is script code on the frame's critical path")
            self.log(message)
            said = True
        return said

    # -- describing itself -------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        return {"cached": len(self._cache), "widgets": len(self._keys),
                "ops": sum(len(ops) for ops in self._cache.values()),
                "records": self.records, "replays": self.replays,
                "failures": self.failures,
                "placeholders": self.placeholders,
                "max_ops": self.max_ops, "max_widgets": self.max_widgets}

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, held: Any) -> bool:
        return self.cached(held)

    def __str__(self) -> str:
        return (f"PaintList({len(self._cache)} cached face(s) on "
                f"{len(self._keys)} widget(s), {self.records} painted, "
                f"{self.replays} replayed"
                + (f", {self.failures} failed" if self.failures else "") + ")")

    def __repr__(self) -> str:
        return f"<PaintList {len(self._cache)} cached>"


# --------------------------------------------------------------------------
# what an ON_HIT answer means
# --------------------------------------------------------------------------


def _bare(value: Any) -> Any:
    """A language value as the Python object it stands for, without importing them.

    A number answers ``value``, a list answers ``elements``, and a plain Python
    object from a test's lambda answers neither - which is the whole reason a
    widget tree can be driven without an interpreter. An empty string and an
    empty list are *there*, so the test is against ``None`` and not against truth.
    """
    for attr in ("value", "elements"):
        found = getattr(value, attr, None)
        if found is not None:
            return found
    return value


def region_of(answer: Any) -> Any:
    """A hit handler's answer as the region it names: ``None``, a word, or a box.

    Three shapes, because the plan's stepper wants two different things from one
    handler. A **word** (or a non-zero number) is a *name*: the whole box still
    takes the click, and the word is what ``ON_CLICK`` gets back as its first
    argument - which is how one widget has an "up" half and a "down" half without
    two widgets in it. A **box** of four numbers is a *narrowing*: the click counts
    only when the point is inside it, in the widget's own coordinates, the same
    numbers its painter drew with. And ``NULL`` - which is what this language's
    ``0`` is, and what a handler that fell off the end returns - is the whole box
    with no name, the same answer as having registered no handler at all.

    A box with no area is the one answer that refuses a click outright, and is
    still returned as the box: :func:`in_region` is what turns it into a refusal.
    Four numbers that are not numbers is a script that said "yes" in the shape it
    had to hand, and refusing its click on a grammar is worse than taking it.
    """
    held = _bare(answer)
    if isinstance(held, (list, tuple)):
        if len(held) != 4:
            return None
        try:
            return tuple(float(_bare(part)) for part in held)
        except (TypeError, ValueError):
            return held or None
    if held is None or held is False:
        return None
    if isinstance(held, str):
        return held.strip() or None
    if isinstance(held, (bool, int, float)):
        # 0 is NULL here, which is "the whole box, unnamed"; anything a script went
        # out of its way to answer with is a name it expects to get back
        return held or None
    return None


def in_region(region: Any, x: float, y: float, widget: Widget) -> bool:
    """Whether a window point is where the widget said its click lives.

    Everything that is not a box is the whole box - a name narrows what a click
    *means*, not where it may land. A box with no area is the exception, and the
    only refusal there is: ``[0, 0, 0, 0]`` is a widget saying "not on me", which
    is how a script-drawn control with an inactive half stops taking clicks.

    The point arrives in the coordinates the pointer reports and is moved here,
    so a handler can be written in the same numbers its painter used.
    """
    if not isinstance(region, tuple):
        return True
    left, top, width, height = (float(part) for part in region)
    if width <= 0 or height <= 0:
        return False
    said_x = float(x) - widget.screen_box[0]
    said_y = float(y) - widget.screen_box[1]
    return left <= said_x < left + width and top <= said_y < top + height
