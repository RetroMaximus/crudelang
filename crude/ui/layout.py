"""Where every box comes from: place, pack, anchor, stack, and the device grid.

:mod:`crude.ui.widget` stores a box and turns it into a screen rect. This says how
a box should *change*: when the window grows, when a panel's padding should follow
the theme rather than a number, when two widgets are told to keep a distance from
each other, and when a row of controls is told to fill the width it was given. The
plan's five verbs - ``PLACE``, ``ANCHOR``, ``HSTACK``/``VSTACK``/``GRID`` and
``PACK`` - are the five functions here that a script can reach, and the pass that
acts on them is :func:`resolve`, which is what ``UIContext.layout()`` calls once a
frame between ``begin`` and the first widget drawn.

Three rules hold the module together:

* **Logical pixels only.** Every number in and out of this file is a logical pixel,
  kept as a float and never rounded. Risk 20 of the plan is that a layout rounded
  twice drifts, so the rounding happens once, at draw time, through
  :func:`snap_box`, and no function here calls it on the tree it just laid out.
* **A policy is recorded, not applied at the moment it is said.** ``ANCHOR(w,
  "right", target, "right", -16)`` and ``PACK(w, "grow")`` both describe a future
  resize, so each verb only stores what it was told and :func:`resolve` reads those
  records back in one fixed order every frame: padding, then pack and stacks, then
  anchors. The last word is always the edge a script glued, because a glued edge is
  the more specific claim about where a thing belongs - and the only one, since an
  axis a glue owns is taken off the row and off ``PACK`` rather than argued with on
  every frame.
* **The tree stays the only geometry.** Every box written here goes through
  :meth:`crude.ui.widget.Widget.place`, which re-resolves the subtree it touched, so
  a pass cannot leave a widget hit-testable where it is not drawn - and a window
  that is the same size as it was last frame moves nothing, changes no paint key,
  and costs one walk of the tree.

Like the rest of the GL-free half of this package it knows nothing about the
language layer: a ``LayoutError`` is a :class:`~crude.ui.base.UIError`, which is
what ``crude/runtime`` turns into an ``RTError`` and what a widget test can assert
on directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .base import UIError
from .geometry import Rect, snap, snap_box
from .skin import Skin
from .widget import Widget

__all__ = ["AXIS", "DEFAULT_GAP", "EDGES", "LAYOUT_TOKENS", "PACK_POLICIES",
           "STACK_ALIGNMENTS", "STACK_DIRECTIONS", "TOLERANCE", "Anchor",
           "Layout", "LayoutError", "Stack", "anchor", "anchors_of", "arrange",
           "as_alignment", "as_direction", "as_edge", "as_policy",
           "clear_layout", "describe_layout", "grid", "hstack", "layout_of",
           "metric", "pack", "pack_of", "pad", "place", "resolve", "snap",
           "snap_box", "stack", "unanchor", "unstack", "vstack"]

#: how far a size has to move before it counts as "someone changed this by hand"
TOLERANCE = 1e-6

#: the slack a stack leaves between two children when the script named none
DEFAULT_GAP = "pad.md"

#: the three resize policies, in the order a report lists them
PACK_POLICIES = ("fixed", "grow", "fill")

#: the three ways a container may arrange its children
STACK_DIRECTIONS = ("horizontal", "vertical", "grid")

#: the four ways a child may sit across the space it was given
STACK_ALIGNMENTS = ("start", "center", "end", "stretch")

#: every edge a widget may be glued by, and the axis it moves on
AXIS: Dict[str, str] = {
    "left": "x", "right": "x", "center-x": "x",
    "top": "y", "bottom": "y", "center-y": "y",
}
EDGES: Tuple[str, ...] = ("left", "right", "top", "bottom", "center-x",
                          "center-y")

#: the short forms a script is likely to reach for first
_EDGE_WORDS = {
    "l": "left", "r": "right", "t": "top", "b": "bottom",
    "x": "left", "y": "top", "x0": "left", "x1": "right",
    "y0": "top", "y1": "bottom", "w": "right", "h": "bottom",
    "cx": "center-x", "cy": "center-y", "center": "center-x",
    "centre": "center-x", "middle": "center-y", "hcenter": "center-x",
    "vcenter": "center-y", "center-x": "center-x", "center-y": "center-y",
    "left": "left", "right": "right", "top": "top", "bottom": "bottom",
}
_DIRECTION_WORDS = {
    "h": "horizontal", "horizontal": "horizontal", "row": "horizontal",
    "x": "horizontal", "v": "vertical", "vertical": "vertical",
    "col": "vertical", "column": "vertical", "y": "vertical",
    "g": "grid", "grid": "grid",
}
_ALIGN_WORDS = {
    "start": "start", "begin": "start", "left": "start", "top": "start",
    "center": "center", "centre": "center", "middle": "center",
    "end": "end", "right": "end", "bottom": "end", "far": "end",
    "stretch": "stretch", "fill": "stretch", "both": "stretch",
}
_POLICY_WORDS = {
    "fixed": "fixed", "keep": "fixed", "none": "fixed", "static": "fixed",
    "grow": "grow", "scale": "grow", "proportional": "grow",
    "fill": "fill", "expand": "fill", "stretch": "fill",
}

#: the two numeric ends of an axis, and what a lone edge rule means for them
_NEAR = {"x": "left", "y": "top"}
_FAR = {"x": "right", "y": "bottom"}
_ORDER = ("x", "y")

#: the metric tokens a length may be named by, which is the only part of the
#  skin's number table that means a distance - a radius is not a gap
LAYOUT_TOKENS = ("pad.none", "pad.xs", "pad.sm", "pad.md", "pad.lg")


class LayoutError(UIError):
    """The placement asked for cannot be solved: a cycle, an outsider, a clash."""


# --------------------------------------------------------------------------
# the records a verb leaves behind
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    """One edge of one widget, glued to one edge of one other thing.

    ``target`` is the widget, or ``None`` for "whatever my parent is" - which is
    the useful half of the plan's example, because a widget added to a panel that
    is later moved keeps its distance from the panel it lives in rather than from
    the panel that happened to own it when the call was made.

    ``offset`` is in logical pixels along the axis the edge moves on: positive is
    right and down, so ``ANCHOR(w, "right", target, "right", -16)`` parks ``w``
    sixteen pixels inside the target's right edge.
    """

    edge: str
    target: Optional[Widget] = None
    target_edge: str = ""
    offset: float = 0.0

    @property
    def axis(self) -> str:
        """``"x"`` or ``"y"``, from the edge this rule was written with."""
        return AXIS[self.edge]

    def as_dict(self) -> Dict[str, Any]:
        """The rule as a report prints it, naming the target by path."""
        return {"edge": self.edge,
                "target": self.target.path() if self.target else None,
                "target_edge": self.target_edge or self.edge,
                "offset": self.offset}

    def __str__(self) -> str:
        said = f"{self.edge} -> "
        said += (self.target.path() if self.target else "parent")
        said += f".{self.target_edge or self.edge}"
        if self.offset:
            said += f" {self.offset:+g}"
        return said


@dataclass(frozen=True)
class Stack:
    """A container's way of laying its children out, as one frozen record.

    ``gap`` and ``pad`` may be a number of logical pixels or the name of a skin
    metric token (``"md"``, ``"pad.lg"``), and a token is resolved on every pass by
    :func:`metric` - so a stack whose gap was named ``"sm"`` moves when the theme
    does, and one named ``6`` does not. ``columns`` means nothing outside a
    ``"grid"``, where ``0`` means "as square as the child count allows".
    """

    direction: str = "horizontal"
    gap: Any = DEFAULT_GAP
    pad: Any = None
    align: str = "start"
    columns: int = 0

    @property
    def is_grid(self) -> bool:
        return self.direction == "grid"

    def as_dict(self) -> Dict[str, Any]:
        return {"direction": self.direction, "gap": self.gap,
                "pad": self.pad, "align": self.align, "columns": self.columns}

    def __str__(self) -> str:
        said = f"Stack({self.direction}, gap={self.gap}"
        if self.is_grid:
            said += f", columns={self.columns or 'auto'}"
        else:
            said += f", align={self.align}"
        if self.pad is not None:
            said += f", pad={self.pad}"
        return said + ")"


class Layout:
    """Everything one widget was told about where it goes.

    The object exists only for a widget that was told something, which is why
    :func:`layout_of` creates it on first use instead of every widget carrying an
    empty one: a five-hundred-widget project with three anchored corners should not
    pay for four hundred and ninety seven policies it will never consult.

    ``_reference`` remembers the pair ``grow`` is measured against - the size the
    script last sized this widget at, and how long the container was at that
    moment - and ``_last`` remembers what the last pass left it at. The second is
    what lets a hand-written ``PLACE`` between two frames re-take the reference
    without the layout layer having to be told about it: a size that is not the one
    this pass produced was written by somebody else.
    """

    __slots__ = ("pack_x", "pack_y", "anchors", "stack", "pad",
                 "_reference", "_last")

    def __init__(self, *, pack_x: str = "fixed", pack_y: str = "fixed",
                 stack: Optional[Stack] = None,
                 pad: Optional[float] = None) -> None:
        self.pack_x = as_policy(pack_x)
        self.pack_y = as_policy(pack_y)
        #: edge -> rule, so gluing the same edge twice replaces rather than adds
        self.anchors: Dict[str, Anchor] = {}
        self.stack = stack
        self.pad = None if pad is None else float(pad)
        self._reference: Dict[str, Tuple[float, float]] = {}
        self._last: Dict[str, float] = {}

    # -- the two axes ----------------------------------------------------
    def axis(self, name: str) -> str:
        """This widget's policy along ``"x"`` or ``"y"``."""
        return getattr(self, f"pack_{name}")

    def set_axis(self, name: str, policy: Any) -> None:
        """Say the policy again; the next pass uses the new one."""
        setattr(self, f"pack_{name}", as_policy(policy))

    # -- the numbers ``grow`` is measured against ------------------------
    def reference(self, name: str) -> Optional[Tuple[float, float]]:
        """``(size, container length)`` the reference was taken at."""
        return self._reference.get(name)

    def remember_reference(self, name: str, size: float, length: float) -> None:
        """Take a new reference, which is what a hand-written ``PLACE`` means."""
        self._reference[name] = (float(size), float(length))

    def produced(self, name: str) -> Optional[float]:
        """The length the last pass left this axis at, if it ever ran."""
        return self._last.get(name)

    def remember_produced(self, name: str, size: float) -> None:
        """Record what this pass wrote, so the next one can notice a change."""
        self._last[name] = float(size)

    # -- describing itself ---------------------------------------------
    @property
    def stacked(self) -> bool:
        return self.stack is not None

    @property
    def anchored(self) -> bool:
        return bool(self.anchors)

    def as_dict(self) -> Dict[str, Any]:
        """The policy as a document - what ``crude widgets --json`` will print."""
        found: Dict[str, Any] = {"pack": [self.pack_x, self.pack_y]}
        if self.pad is not None:
            found["pad"] = self.pad
        if self.anchors:
            found["anchors"] = [rule.as_dict()
                                for _edge, rule in sorted(self.anchors.items())]
        if self.stack is not None:
            found["stack"] = self.stack.as_dict()
        if self._reference:
            found["reference"] = {name: list(value) for name, value in
                                  sorted(self._reference.items())}
        return found

    def __str__(self) -> str:
        parts = [f"pack {self.pack_x}/{self.pack_y}"]
        if self.anchors:
            parts.append(f"{len(self.anchors)} anchor(s)")
        if self.stack is not None:
            parts.append(str(self.stack))
        if self.pad is not None:
            parts.append(f"pad {self.pad:g}")
        return f"Layout({', '.join(parts)})"

    def __repr__(self) -> str:
        return f"<Layout {self.pack_x}/{self.pack_y}>"


# --------------------------------------------------------------------------
# what a script's word means
# --------------------------------------------------------------------------


def as_policy(value: Any) -> str:
    """One of :data:`PACK_POLICIES`; the word a script used, checked."""
    if value is None:
        return "fixed"
    said = str(value).strip().lower()
    found = _POLICY_WORDS.get(said)
    if found is None:
        raise LayoutError(f"'{value}' is not a pack policy (known: "
                          f"{', '.join(PACK_POLICIES)})")
    return found


def as_edge(value: Any) -> str:
    """One of :data:`EDGES`, following the short forms too; ``"edge"`` is checked."""
    if value is None:
        raise LayoutError("an anchor has to be given an edge")
    said = str(value).strip().lower()
    found = _EDGE_WORDS.get(said)
    if found is None:
        raise LayoutError(f"'{value}' is not an edge (known: "
                          f"{', '.join(EDGES)})")
    return found


def as_direction(value: Any) -> str:
    """One of :data:`STACK_DIRECTIONS`."""
    said = str(value or "horizontal").strip().lower()
    found = _DIRECTION_WORDS.get(said)
    if found is None:
        raise LayoutError(f"'{value}' is not a stack direction (known: "
                          f"{', '.join(STACK_DIRECTIONS)})")
    return found


def as_alignment(value: Any, default: str = "start") -> str:
    """One of :data:`STACK_ALIGNMENTS`; ``"centre"`` and ``"middle"`` are centre."""
    if value is None:
        return default
    said = str(value).strip().lower()
    found = _ALIGN_WORDS.get(said)
    if found is None:
        raise LayoutError(f"'{value}' is not an alignment (known: "
                          f"{', '.join(STACK_ALIGNMENTS)})")
    return found


def metric(skin: Skin, name: Any) -> float:
    """A length a layout verb was given, or the length its skin token means.

    A number passes through untouched, so ``hstack(box, gap=6)`` is six logical
    pixels whatever the theme says. A name is looked up in the skin's metric table
    - bare (``"md"``) or qualified (``"pad.md"``) - and scaled by the skin's size
    factor exactly the way a role's padding is, so a row that used the theme's gap
    gets a bigger gap at ``xl`` and a smaller one at ``sm`` without the script
    knowing the numbers. Only the ``pad.`` tokens name a length, and the answer is
    cached per skin digest and token, because ``Skin.metrics()`` builds a fresh
    table every time it is asked and a pass asks the same questions every frame.
    """
    if isinstance(name, bool):
        raise LayoutError(f"'{name}' is not a length; use a number of pixels "
                          "or a metric token")
    if isinstance(name, (int, float)):
        return max(0.0, float(name))
    if not isinstance(skin, Skin):
        raise LayoutError(f"a skin measures a token, not a "
                          f"{type(skin).__name__}")
    said = str(name or "").strip().lower()
    if not said:
        raise LayoutError("there is no length named ''")
    if "." not in said:
        said = f"pad.{said}"
    if said not in LAYOUT_TOKENS:
        raise LayoutError(f"'{name}' is not a length (known: "
                          f"{', '.join(LAYOUT_TOKENS)})")
    found = _METRICS.get((skin.digest, said))
    if found is None:
        found = _METRICS[(skin.digest, said)] = max(
            0.0, skin.metrics()[said] * skin.scale)
    return found


#: resolved token lengths, per skin digest: a pass asks the same questions every
#  frame and ``Skin.metrics()`` builds a fresh table each time it is asked
_METRICS: Dict[tuple, float] = {}


def _widget(value: Any, wanted: str) -> Widget:
    """The widget a script meant, or an error naming what actually arrived."""
    if isinstance(value, Widget):
        return value
    raise LayoutError(f"{wanted} is not a widget; it is a "
                      f"{type(value).__name__}")


def _container(value: Any, wanted: str) -> Widget:
    """A widget that can hold children, which is what every stack needs."""
    found = _widget(value, wanted)
    if not found.is_container:
        raise LayoutError(f"a '{found.kind.name}' holds no children, so it "
                          f"cannot be arranged; use '{wanted}' on a panel or on "
                          "a kind defined on top of one")
    return found


def layout_of(held: Any) -> Layout:
    """This widget's policy, created on first ask; the widget keeps it."""
    found = _widget(held, "the widget")
    policy = found.layout
    if policy is None:
        # the slot belongs to the widget and is written here and only here, which
        # is the one thing that keeps ``crude.ui.widget`` from importing this module
        found._layout = policy = Layout()
    return policy


def clear_layout(held: Any) -> None:
    """Forget every policy: the widget keeps its box and goes back to the skin.

    The padding a ``pad()`` call overrode comes back on the next pass, because
    :class:`~crude.ui.widget.Widget` measures it from the skin it was synced with
    and there is no longer an override to measure from.
    """
    found = _widget(held, "the widget")
    found._layout = None


def describe_layout(held: Any) -> Dict[str, Any]:
    """The policy as a document, or ``{}`` for a widget that was never told."""
    found = _widget(held, "the widget").layout
    return found.as_dict() if found is not None else {}


# --------------------------------------------------------------------------
# the five verbs
# --------------------------------------------------------------------------


def place(held: Any, x: Any = None, y: Any = None, width: Any = None,
          height: Any = None) -> bool:
    """``PLACE(w, x, y, width, height)``: the box, in logical pixels.

    Relative to the parent's content box, which for a widget in the window's root
    is the window's client rect - the plan's "``PLACE`` is window-relative; inside
    a ``PANEL`` the same call becomes parent-relative" is one call, not two, because
    the root's content box *is* the client rect. Anything left out stays where it
    was, and the answer says whether the box moved at all, which is whether the
    paint key changed.

    Inside a stack the position is the stack's to decide (see :func:`hstack`), so a
    ``PLACE`` there sizes the child and the next pass puts it back in the row;
    the size it names is what a ``grow`` policy is measured against afterwards.
    """
    return _widget(held, "the widget").place(x, y, width, height)


def pack(held: Any, policy: Any, policy_y: Any = None) -> Tuple[str, str]:
    """``PACK(w, "fill")``: what this widget does when its container changes size.

    One word means both axes; a second word names the y axis on its own, so
    ``PACK(slider, "grow", "fixed")`` is a slider that gets longer without getting
    taller. The three answers are:

    ``fixed``
        keeps the size the script gave it. This is the default, and it is what a
        widget with no ``PACK`` call does.
    ``grow``
        keeps the *fraction* of its container the size it was last placed at: put a
        100 px control in a 200 px box and it is half the box, and when the box is
        400 px the control is 200 px. Siblings are not consulted, which is what
        makes the answer the same inside a stack and outside one.
    ``fill``
        takes the space nothing else used - inside a stack that is the slack after
        every sibling and every gap, split between the fillers; outside one it is
        the container's length from where the widget stands, so its far edge lands
        on the container's far edge.

    Returns the two policies now stored, ``(x, y)``, which is what a report checks
    and what ``GET(w, "pack")`` will read in S8.
    """
    found = layout_of(held)
    first = as_policy(policy)
    found.set_axis("x", first)
    found.set_axis("y", first if policy_y is None else policy_y)
    return (found.pack_x, found.pack_y)


def pack_of(held: Any) -> Tuple[str, str]:
    """The two policies currently stored, ``(x, y)``; ``("fixed", "fixed")`` if none."""
    found = _widget(held, "the widget").layout
    return (found.pack_x, found.pack_y) if found is not None else ("fixed",
                                                                   "fixed")


def _target_of_word(value: Any) -> Optional[Widget]:
    """An anchor's target: a widget, or the word ``"parent"`` for none given.

    A script reaching for ``ANCHOR(w, "right", "parent", "right", -16)`` means the
    same thing as leaving the target out, and saying so in words is what a person
    who has never read this file will do first.
    """
    if value is None:
        return None
    if isinstance(value, Widget):
        return value
    if isinstance(value, str) and value.strip().lower() in ("", "parent", "own"):
        return None
    return _widget(value, "an anchor target")


def anchor(held: Any, edge: Any, target: Any = None, target_edge: Any = None,
           offset: Any = 0.0) -> Anchor:
    """``ANCHOR(w, "right", target, "right", -16)``: glue one edge to one edge.

    ``target`` is another widget, or ``None`` (or ``"parent"``) for the panel this
    one lives in. With no ``target_edge`` the same edge is used on both sides,
    which is the common case - a widget's right edge sixteen pixels inside its
    parent's right edge.

    Two rules on the same axis are not a contradiction but a stretch: gluing
    ``left`` *and* ``right`` makes the widget exactly as wide as the distance
    between them, which is how a bar pinned to both window edges survives a resize.
    A centre rule and an edge rule on one axis is a contradiction, and says so.

    Which box the edges come out of follows the box the widget's own coordinates
    are in: gluing to the panel a widget lives in glues to the space that panel
    gives its children, because that is the space ``PLACE`` measured it in, and
    gluing to anything else - a sibling, a cousin in another panel - glues to that
    widget's own edges, border included, which is the line a person can see.

    An anchor is applied after pack and after any stack, and it owns the axis it
    names for the rest of the tree's life: a widget glued on ``x`` is sized and
    placed on ``x`` by its glue alone, so a ``PACK(w, "fill")`` or a row that also
    spoke for that axis is overruled rather than fought with. The widget is still
    arranged on the axes nobody glued, which is the behaviour a script that wrote
    both almost certainly wants - a button in a row, pinned to the bottom of it.
    """
    found = _widget(held, "the widget")
    first = as_edge(edge)
    wanted = _target_of_word(target)
    if wanted is found:
        raise LayoutError(f"'{found.path()}' cannot be anchored to itself")
    if wanted is None:
        # "the parent" is stored as None rather than looked up now, so a widget that
        # is moved between panels keeps its margin from whichever panel owns it
        if found.parent is None:
            raise LayoutError(f"'{found.path()}' has no parent to anchor to; "
                              "add it to a panel first, or name a target")
    else:
        if not found.is_in(wanted) and not wanted.is_in(found) and \
                wanted.root() is not found.root():
            raise LayoutError(f"'{found.path()}' cannot anchor to "
                              f"'{wanted.path()}': the two are not in the same "
                              "tree, so one of them has no place to be glued at")
    rule = Anchor(edge=first, target=wanted,
                  target_edge=(as_edge(target_edge) if target_edge is not None
                               else first),
                  offset=float(offset or 0.0))
    policy = layout_of(found)
    policy.anchors[first] = rule
    return rule


def unanchor(held: Any, edge: Any = None) -> int:
    """Take one glue away, or all of them; how many went."""
    found = _widget(held, "the widget").layout
    if found is None:
        return 0
    if edge is None:
        gone = len(found.anchors)
        found.anchors.clear()
        return gone
    return 1 if found.anchors.pop(as_edge(edge), None) is not None else 0


def anchors_of(held: Any) -> Tuple[Anchor, ...]:
    """Every glue on a widget, in :data:`EDGES` order."""
    found = _widget(held, "the widget").layout
    if found is None:
        return ()
    return tuple(found.anchors[name] for name in EDGES if name in found.anchors)


def pad(held: Any, value: Any) -> float:
    """``PAD(w, 16)``: how far this container's children start inside it.

    A number of logical pixels, replacing the skin's own padding for this widget;
    the override is measured into the content box, so it moves where children are
    *placed*, what they are *clipped* to, and where a hit on the inner edge stops
    counting as the panel. ``None`` gives the skin's answer back.

    A token name (``"lg"``, ``"pad.lg"``) is measured against the skin this tree
    was last resolved with, which is the one thing ``pad`` cannot know before the
    first :func:`resolve` has run - and says so rather than guessing - while a
    number of pixels always works. A widget that owns a stack stops owning that
    stack's padding at the same time, so the last word written here is the one that
    survives the next pass.
    """
    found = _container(held, "a padded widget")
    policy = layout_of(found)
    if isinstance(value, bool):
        raise LayoutError(f"'{value}' is not a padding; use a number of pixels, "
                          "a metric token, or None for the skin's own")
    number = None
    if value is not None:
        number = (metric(_skin_of(found), value) if isinstance(value, str)
                  else max(0.0, float(value)))
    policy.pad = number
    if policy.stacked and policy.stack.pad is not None:
        before = policy.stack
        policy.stack = Stack(before.direction, before.gap, None, before.align,
                             before.columns)
    return 0.0 if number is None else number


def _skin_of(held: Widget) -> Skin:
    """The skin this widget's tree was last resolved with, which ``pad`` needs."""
    found = held.root().layout_skin()
    if found is None:
        raise LayoutError(f"no skin has been resolved for '{held.path()}' yet, "
                          "so a metric token cannot be measured here; lay the "
                          "tree out first or give a number of pixels")
    return found


def stack(panel: Any, direction: Any = "horizontal", *, gap: Any = None,
          pad: Any = None, align: Any = None, columns: Any = 0) -> Stack:
    """``HSTACK``/``VSTACK``/``GRID`` as one call: arrange the children of a panel.

    A stack owns the *positions* of its children and shares their container's
    *length* out between them; a child's size stays what ``PLACE`` said unless a
    :func:`pack` policy or this call's ``align`` changes it. Children are laid out
    in z-order, which is insertion order unless a ``RAISE`` or ``LOWER`` said
    otherwise - so raising a button moves it to the end of its row, which is the
    same fact two ways round.

    ``gap`` and ``pad`` are pixels or a skin token (default
    ``"pad.md"``/the skin's own padding); ``align`` is how a child sits across the
    row - ``"start"``, ``"center"``, ``"end"`` or ``"stretch"`` for the whole
    height - and is overridden per child by that child's own cross-axis policy.
    Laying out replaces any stack this panel had.
    """
    found = _container(panel, "a stacked widget")
    wanted = as_direction(direction)
    held = as_alignment(align, "center" if wanted == "grid" else "start")
    spec = Stack(wanted, DEFAULT_GAP if gap is None else gap, pad, held,
                 max(0, int(columns or 0)))
    layout_of(found).stack = spec
    return spec


def hstack(panel: Any, gap: Any = None, *, pad: Any = None,
           align: Any = None) -> Stack:
    """Arrange left to right: ``HSTACK(box, 8)``. See :func:`stack`."""
    return stack(panel, "horizontal", gap=gap, pad=pad, align=align)


def vstack(panel: Any, gap: Any = None, *, pad: Any = None,
           align: Any = None) -> Stack:
    """Arrange top to bottom: ``VSTACK(box, 8)``. See :func:`stack`."""
    return stack(panel, "vertical", gap=gap, pad=pad, align=align)


def grid(panel: Any, columns: Any = 0, *, gap: Any = None, pad: Any = None,
         align: Any = None) -> Stack:
    """Arrange in cells of one size, left to right then top to bottom.

    ``columns`` of ``0`` means "as square as the child count allows", and a cell is
    ``length / columns`` with the gaps taken out, so a grid of nine icons in a 200
    px panel is nine equal cells rather than nine widths. A child whose policy is
    ``fill`` takes its whole cell; anything else sits inside it at ``align``, which
    a grid defaults to ``"center"`` because a grid is how you centre things.
    """
    return stack(panel, "grid", gap=gap, pad=pad, align=align,
                 columns=columns)


def arrange(panel: Any, spec: Mapping[str, Any]) -> Stack:
    """A stack from one document, for a caller whose arguments arrive as a list.

    ``{"direction": "vertical", "gap": "sm", "align": "end"}`` - the keys are the
    same words :func:`stack` takes, and anything missing keeps its default. The
    bindings layer is the intended user: a crude list is flat, and this is the
    shortest honest way to hand a mapping-shaped call over.
    """
    if not isinstance(spec, Mapping):
        raise LayoutError("arrange wants a map of the words stack takes; "
                          "use hstack, vstack or grid for the usual calls")
    held = dict(spec)
    return stack(panel, held.pop("direction", "horizontal"),
                 gap=held.pop("gap", None), pad=held.pop("pad", None),
                 align=held.pop("align", None),
                 columns=held.pop("columns", 0))


def unstack(panel: Any) -> bool:
    """Stop arranging a panel; its children stay where the last pass left them."""
    found = _container(panel, "a stacked widget").layout
    if found is None or found.stack is None:
        return False
    found.stack = None
    return True


# --------------------------------------------------------------------------
# the pass
# --------------------------------------------------------------------------


def resolve(root: Any, skin: Any, *, width: Any = None,
            height: Any = None, dpr: Any = None) -> int:
    """Lay the whole tree out; how many boxes the pass ended up writing.

    ``ctx.layout()`` per frame, in the plan's order - after ``r.begin``, before the
    first widget is drawn - and safe to call on a frame nothing changed: the walk
    happens, nothing moves, no paint key changes. ``width``/``height`` re-place the
    root first, which is what an ``on_resize`` handler is for, so a caller can do
    ``resolve(root, skin, width=w, height=h)`` and be done with it; the root's own
    box is not counted, because the caller is the one who asked for that one.
    A widget moved twice in one pass - arranged, then glued - counts twice, since
    what a caller wants to know is whether anything was rewritten at all.

    ``dpr`` is the device grid the *resolved* geometry is snapped to, and leaving it
    out leaves every number logical: the plan's answer to rounding is that the
    layout is solved in logical pixels and rounded once at draw, which is what
    :meth:`~crude.ui.widget.Widget.sync` does with the ratio the frame was begun at.
    The box a script was given is never touched by it - only the screen boxes that
    painting and hit testing read - so an anchor or a ``grow`` fraction cannot drift
    a frame at a time.

    Three sweeps, in this order, and the order is the whole design:

    1. **padding** - a stack's ``pad`` token is measured against ``skin`` and
       written, then :meth:`~crude.ui.widget.Widget.sync` turns every padding into
       a content box, an origin and a clip. Nothing below can be counted until the
       lengths it divides are known.
    2. **pack and stacks** - parents before children, because a child's size is a
       fraction of the space its parent has. Every box is written in the parent's
       own space, so no absolute number is needed and a moved subtree re-resolves
       itself as it goes.
    3. **anchors** - in absolute space, and last, for two reasons: the target may
       live in a different panel, so its screen rect has to exist before its edges
       can be read; and an edge a script glued is a more specific claim than any
       row sizing. Glued widgets are applied in dependency order, so a badge
       anchored to a button that is itself anchored lands on the button's final
       place rather than its pre-anchor one.
    """
    found = _widget(root, "a layout root")
    if not isinstance(skin, Skin):
        raise LayoutError(f"resolve needs the skin to lay a tree out with, not "
                          f"a {type(skin).__name__}")
    moved = 0
    if width is not None or height is not None:
        found.place(0, 0, float(width if width is not None else found.width),
                    float(height if height is not None else found.height))
    for held in found.walk():
        _note_pad(held, skin)
    found.sync(skin, dpr=dpr)
    moved += _box_pass(found, skin)
    moved += _anchor_pass(found)
    return moved


def _note_pad(held: Widget, skin: Any) -> None:
    """Sweep 1: a stack's ``pad`` becomes a number, for this frame."""
    policy = held.layout
    if policy is None or policy.stack is None or policy.stack.pad is None:
        return
    policy.pad = metric(skin, policy.stack.pad)


def _box_pass(panel: Widget, skin: Any) -> int:
    """Sweep 2, one container at a time: sizes first, then the children's own."""
    moved = 0
    kids = panel.children
    if not kids:
        return 0
    inner = panel.content_box()
    policy = panel.layout
    if policy is not None and policy.stacked:
        moved += _arrange(panel, policy.stack, inner, skin)
    else:
        for kid in kids:
            moved += _fit(kid, inner)
    for kid in kids:
        if kid.is_container:
            moved += _box_pass(kid, skin)
    return moved


def _fit(held: Widget, inner: Rect) -> int:
    """Sizing without arranging: what ``PACK`` says about a placed widget.

    The position is left exactly where ``PLACE`` put it, and a ``fill`` grows from
    that position to the container's far edge rather than by the container's whole
    length: a panel placed at ``20, 20`` and packed ``fill`` ends at the window's
    right and bottom edges, which is the same answer a left-and-right anchor pair
    gives and what "fills the rest of the window" means when nothing arranged it.
    """
    policy = held.layout
    if policy is None:
        return 0
    size = [held.width, held.height]
    for index, name in enumerate(_ORDER):
        length = inner[2 + index]
        wanted = policy.axis(name)
        if wanted == "fill":
            size[index] = max(0.0, length - (held.x, held.y)[index])
        elif wanted == "grow":
            size[index] = _grow(policy, name, size[index], length)
    return _write(held, *_arranged(held, policy, held.x, held.y, size[0],
                                   size[1]), policy)


def _grow(policy: Layout, name: str, wanted: float, length: float) -> float:
    """``grow``: the fraction of the container the size was last placed at.

    The reference is taken the first time the container has a length to compare
    against, which is why the first pass after a ``PLACE`` leaves the size alone and
    a resize after it does not: a widget placed at 100 px in a container that has
    not been measured yet is 100 px, and becomes 200 px when that container is
    twice as long as it was the moment the script said 100.

    A size that is not the one this pass left is a size somebody wrote by hand, so
    the reference is retaken rather than scaled from a number the script no longer
    means - which is what makes ``PLACE`` followed by a resize behave like the
    resize followed the place.
    """
    if length <= 0.0:
        return wanted
    was = policy.reference(name)
    left = policy.produced(name)
    if was is None or (left is not None and abs(wanted - left) > TOLERANCE):
        policy.remember_reference(name, wanted, length)
        return wanted
    base, floor = was
    if floor <= 0.0:
        return wanted
    return base * (length / floor)


def _arrange(panel: Widget, spec: Stack, inner: Rect, skin: Any) -> int:
    """Sweep 2, arranged: a row, a column or a grid of cells."""
    kids = panel.children
    if spec.is_grid:
        return _arrange_grid(kids, spec, inner, skin)
    gap = metric(skin, spec.gap)
    flat = spec.direction == "horizontal"
    main = "x" if flat else "y"
    across = "y" if flat else "x"
    length = inner[2] if flat else inner[3]
    span = inner[3] if flat else inner[2]
    # the fixed and growing children are measured first so the fillers can be
    # given what is left of the container rather than what is left of the row
    sizes: List[Optional[float]] = []
    slack = length - gap * (len(kids) - 1)
    fillers = 0
    for kid in kids:
        policy = kid.layout
        wanted = policy.axis(main) if policy is not None else "fixed"
        size = kid.width if flat else kid.height
        if wanted == "fill":
            fillers += 1
            sizes.append(None)
            continue
        if wanted == "grow" and policy is not None:
            size = _grow(policy, main, size, length)
        sizes.append(size)
        slack -= size
    share = max(0.0, slack) / fillers if fillers else 0.0
    moved = 0
    cursor = 0.0
    for index, kid in enumerate(kids):
        policy = kid.layout
        size = share if sizes[index] is None else sizes[index]
        at = cursor
        cursor += size + gap
        wanted = policy.axis(across) if policy is not None else "fixed"
        thick = kid.height if flat else kid.width
        if wanted == "fill" or spec.align == "stretch":
            thick, there = max(0.0, span), 0.0
        else:
            if wanted == "grow" and policy is not None:
                thick = _grow(policy, across, thick, span)
            there = _across(span, thick, spec.align)
        if flat:
            moved += _write(kid, *_arranged(kid, policy, at, there, size,
                                            thick), policy)
        else:
            moved += _write(kid, *_arranged(kid, policy, there, at, thick,
                                            size), policy)
    return moved


def _arrange_grid(kids: Sequence[Widget], spec: Stack, inner: Rect,
                  skin: Any) -> int:
    """Sweep 2, in cells: every child gets a box of the same shape."""
    total = len(kids)
    gap = metric(skin, spec.gap)
    columns = spec.columns or int(math.ceil(math.sqrt(total)))
    columns = max(1, min(columns, total))
    rows = int(math.ceil(total / float(columns)))
    cell_w = max(0.0, (inner[2] - gap * (columns - 1)) / columns)
    cell_h = max(0.0, (inner[3] - gap * (rows - 1)) / rows)
    moved = 0
    for index, kid in enumerate(kids):
        policy = kid.layout
        x, width = _cell(policy, kid.width, cell_w, "x", spec.align)
        y, height = _cell(policy, kid.height, cell_h, "y", spec.align)
        moved += _write(kid, *_arranged(kid, policy,
                                        (index % columns) * (cell_w + gap) + x,
                                        (index // columns) * (cell_h + gap) + y,
                                        width, height), policy)
    return moved


def _cell(policy: Optional[Layout], wanted: float, length: float, name: str,
          align: str) -> Tuple[float, float]:
    """Where a child of ``wanted`` px sits in a cell ``length`` px long."""
    held = policy.axis(name) if policy is not None else "fixed"
    if held == "fill":
        return 0.0, max(0.0, length)
    size = wanted
    if held == "grow" and policy is not None:
        size = _grow(policy, name, wanted, length)
    return _across(length, size, align), size


def _across(length: float, size: float, align: str) -> float:
    """Cross-axis placement: the child keeps its size and moves within the row."""
    if align == "center":
        return (length - size) * 0.5
    if align == "end":
        return length - size
    return 0.0


def _glued(policy: Optional[Layout], name: str) -> bool:
    """Whether an anchor owns this axis, which makes it nobody else's to size."""
    return policy is not None and any(AXIS[edge] == name for edge in
                                      policy.anchors)


def _arranged(held: Widget, policy: Optional[Layout], x: float, y: float,
              width: float, height: float) -> Tuple[float, float, float, float]:
    """The box a row or a pack decided, minus the axes a glue is going to decide.

    Without this the two would fight every frame: the row would put the widget back
    at its own answer, the glue would move it again, and the paint key would change
    twice on an idle window - the one thing the plan's cache cannot tolerate. A
    glued axis is therefore the glue's alone, and a ``PACK`` or a stack that also
    speaks for it is quietly overruled, which is what "an anchor is the last word"
    has to mean to be true on the second frame as well as the first.
    """
    if policy is None or not policy.anchors:
        return x, y, width, height
    if _glued(policy, "x"):
        x, width = held.x, held.width
    if _glued(policy, "y"):
        y, height = held.y, held.height
    return x, y, width, height


def _write(held: Widget, x: float, y: float, width: float, height: float,
           policy: Optional[Layout]) -> int:
    """The only box write in the pass: record what was produced, then move it.

    Every number is relative to the container's content box, which is what
    :meth:`~crude.ui.widget.Widget.place` means, and ``place`` re-resolves this
    widget's subtree - so the children about to be visited by the next ``_box_pass``
    already see the content box they will be measured against.

    What is recorded is what the widget ends up holding rather than what this verb
    asked for, because ``grow`` tells a hand-written ``PLACE`` apart from its own
    last answer by exactly that comparison.
    """
    if policy is not None:
        policy.remember_produced("x", max(0.0, width))
        policy.remember_produced("y", max(0.0, height))
    return 1 if held.place(x, y, max(0.0, width), max(0.0, height)) else 0


def _anchor_pass(root: Widget) -> int:
    """Sweep 3: every glued edge, in the order its dependents need."""
    moved = 0
    for held in _glued_order(root):
        moved += _apply(held)
    return moved


def _glued_order(root: Widget) -> List[Widget]:
    """The anchored widgets of ``root``, each before anything anchored to it.

    Gluing is a dependency: a badge whose left edge is a button's right edge has to
    be moved after the button was. Walking in tree order would get that wrong
    whenever the target is a later sibling, so the graph is sorted instead, and a
    cycle says so here rather than leaving a widget at whatever place the last frame
    happened to give it.
    """
    out: List[Widget] = []
    state: Dict[int, int] = {}

    def visit(held: Widget, trail: Tuple[str, ...]) -> None:
        mark = state.get(id(held))
        if mark == 2:
            return
        if mark == 1:
            raise LayoutError("these anchors refer to each other in a circle: "
                              f"{' -> '.join((*trail, held.path()))}, and no "
                              "place solves that")
        state[id(held)] = 1
        policy = held.layout
        for rule in (policy.anchors.values() if policy is not None else ()):
            there = _target(held, rule)
            if there is not None:
                visit(there, trail + (held.path(),))
        state[id(held)] = 2
        # a plain target is visited so that it lands *before* whatever is glued to
        # it, but it has nothing of its own to move and must not be written twice
        if policy is not None and policy.anchors:
            out.append(held)

    for held in root.walk():
        if held.layout is not None and held.layout.anchors:
            visit(held, ())
    return out


def _target(held: Widget, rule: Anchor) -> Optional[Widget]:
    """The widget a glue reads, resolving "the parent" only now."""
    return rule.target if rule.target is not None else held.parent


def _apply(held: Widget) -> int:
    """Move one widget to where its glued edges say it belongs."""
    policy = held.layout
    if policy is None or not policy.anchors:
        return 0
    box = held.screen_box
    # the parent's origin, recovered from the two numbers the last resolve produced
    # for this widget: its screen box and its own relative box differ by exactly it
    origin = (box[0] - held.x, box[1] - held.y)
    out = [box[0], box[1], box[2], box[3]]
    for name in _ORDER:
        near = far = middle = None
        for rule in policy.anchors.values():
            if AXIS[rule.edge] != name:
                continue
            there = _target(held, rule)
            if there is None:
                continue
            # the panel a widget lives in is read as its content box, because that
            # is the space the widget's own x and y are measured in; anything else
            # is read as its edges, which is the line on the screen
            rect = (there.content_box() if there is held.parent
                    else there.screen_box)
            value = _edge_of(rect, rule.target_edge or rule.edge)
            value += rule.offset
            if rule.edge == _NEAR[name]:
                near = value
            elif rule.edge == _FAR[name]:
                far = value
            else:
                middle = value
        if middle is not None and (near is not None or far is not None):
            raise LayoutError(f"'{held.path()}' is glued to the middle and to an "
                              f"edge on its {name} axis; either one of those "
                              "decides where it goes")
        index = 0 if name == "x" else 1
        other = index + 2
        if near is not None and far is not None:
            out[index] = near
            out[other] = max(0.0, far - near)
        elif near is not None:
            out[index] = near
        elif far is not None:
            out[index] = far - out[other]
        elif middle is not None:
            out[index] = middle - out[other] * 0.5
    return _write(held, out[0] - origin[0], out[1] - origin[1], out[2], out[3],
                  policy)


def _edge_of(rect: Rect, edge: str) -> float:
    """The absolute position of one edge of one resolved box."""
    left, top, width, height = rect
    if edge == "left":
        return left
    if edge == "right":
        return left + width
    if edge == "top":
        return top
    if edge == "bottom":
        return top + height
    if edge == "center-x":
        return left + width * 0.5
    return top + height * 0.5


# --------------------------------------------------------------------------
# the device grid
# --------------------------------------------------------------------------
# ``snap`` and ``snap_box`` are imported from :mod:`crude.ui.geometry` and
# re-exported here: they are the rounding this pass is allowed to hand to a draw,
# and a widget measuring its own screen box needs the same two functions without
# being allowed to import this module - which it cannot, because this one reads
# widgets. See :func:`~crude.ui.geometry.snap_box` for why an edge is rounded
# rather than a length.
