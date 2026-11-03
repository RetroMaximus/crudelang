"""The widget tree: one box, one z-order, one state map, and the pointer's effect on them.

:mod:`crude.ui.props` says what a widget *can* hold and :mod:`crude.ui.skin` says
what it *looks like*. This is the third thing a widget is: where it is, what is on
top of it, what the pointer is doing to it, and what it draws. It is deliberately
GL-free and language-free - no ``glfw``, no ``Number``, no ``RTError`` - because
the headless run builds this whole tree and paints it into
:class:`~crude.ui.null_renderer.NullRenderer`, and a test of a five-widget
metronome front panel should not need a display, a GPU or an interpreter.

Four rules carry the module:

* **Placement is stored as it was said.** ``x, y, width, height`` are logical
  pixels relative to the parent's content box, kept as floats and never rounded:
  the plan's DPI answer is that layout is solved in logical pixels and rounded
  once at draw, so a widget that rounded its own position would be rounded twice.
  The rounding happens once, in the resolution: a :meth:`Widget.sync` told the
  device pixel ratio snaps each *resolved* screen box onto that grid, which is
  what :meth:`hit` and :meth:`paint` both read, so a pointer can never be a
  half-pixel out of step with the picture.
* **Absolute boxes are resolved, not computed on demand.** :meth:`Widget.sync`
  walks the tree once - with a :class:`~crude.ui.skin.Skin`, because a child's
  origin is its parent's *content box* and only the skin knows what padding means -
  and stores each widget's screen box, its clip and the origin its children are
  placed at. Everything after that (hit tests, paints, anchors) reads those three
  numbers, so a frame cannot hit-test one geometry and paint another.
* **The pointer's four props are the widget's own state.** ``hovered``,
  ``pressed``, ``focused`` and ``selected`` live in the same map as ``value``,
  which is why they are in the paint key: a cached painter that replayed an
  unhovered widget over a hovered one would be a widget that does not answer the
  mouse. A script may read them and may not write them - :mod:`crude.ui.props`
  makes that refusal - and this module is the machine that writes them.
* **What a click means is not decided here.** :class:`Widget` keeps press,
  release and click straight - one event per gesture, to the right widget, with
  the state map honest - and takes the answer "a toggle flips, a button bounces"
  from :mod:`crude.ui.behaviour`, the table of kinds, which the constructor
  installs in :attr:`on_event` before anything else can. A base class that
  guessed per-kind behaviour is where a widget toolkit starts to disagree with
  itself; a table that a script's ``ON_CLICK`` is chained in front of is the same
  answer written down once.
"""

from __future__ import annotations

import itertools
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional
from typing import Tuple

from .base import Renderer, UIError
from .behaviour import install_behaviour
from .faces import SIDES, face_for, placed_text, placement
from .geometry import (Point, Rect, TEXT_HEIGHT, clip_overlap, snap_box,
                       text_bounds)
from .props import State, digest, get_kind, pd_arguments, pd_object_for
from .skin import ROLES, Skin, as_role

__all__ = ["Widget", "Panel", "WidgetError", "POINTER", "CONTAINER_KIND",
           "CONTENT_KIND", "CONTENT_SUFFIX", "format_value",
           "DEFAULT_DECIMALS", "root_panel", "top_at"]

#: the four rows only the pointer and the keyboard may write
POINTER = ("hovered", "pressed", "focused", "selected")

#: the states the pointer takes off a widget when it stops being able to take them
_DROPPED = ("hovered", "pressed", "focused")

#: the kind a container is a container by: ``NODE_ADD`` works on a panel and on
#  anything defined on top of one, and on nothing else. It is a fact about the
#  prop table rather than about a Python class, so ``WIDGET_DEFINE("deck",
#  "panel")`` is a container with no new prop and no new code.
CONTAINER_KIND = "panel"

#: the kind that hands every child it is given to one inner panel of its own, so
#: "scrollable" is a fact about a widget rather than about the code that built it.
#: The name is the prop table's, exactly like :data:`CONTAINER_KIND`, which is what
#: lets ``WIDGET_DEFINE("page", "scroll")`` inherit the routing too.
CONTENT_KIND = "scroll"

#: the inner panel's own name, hung off whichever name the script gave the viewport
#: - so ``find("list#rows")`` answers and a report can tell the two apart.
CONTENT_SUFFIX = "#rows"

#: a widget's ``size`` prop is a skin scale, and building a
#: :class:`~crude.ui.skin.Skin` resolves every role in it - so the five sizes a
#  project uses are built once between them, not once per widget per frame. The
#  key is the skin's own digest, which is its identity for caching purposes.
_STYLED: Dict[tuple, Skin] = {}

#: what a script never sees: the number behind "which widget is this"
_UIDS = itertools.count(1)


class WidgetError(UIError):
    """The tree was asked for a shape it cannot hold: a cycle, a leaf, an outsider."""


def _inside(rect: Rect, x: float, y: float) -> bool:
    """Whether ``x, y`` is in ``rect`` - which owns its top and left, not both.

    Half-open on purpose: two widgets that share an edge must not both claim the
    pixel on it, or a click on a boundary is a different answer in a different
    build. A box with no size contains nothing, so an unplaced widget is never a
    hit, and a layout that collapsed a widget to zero has taken it out of the
    pointer's reach by the same act.
    """
    left, top, width, height = rect
    return left <= x < left + width and top <= y < top + height


def _number(value: Any) -> bool:
    """Whether a prop holds something a range can be measured on.

    A ``bool`` is an ``int`` to Python and a switch is not a limit, so it is said
    here rather than letting a ``True`` order itself against a ``2.0``.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


#: how many digits a readout shows when the widget itself says nothing: a
#  person wants "80.11", not "80.11214023456789", and a meter that cannot make
#  up its mind between two frames is not showing information, only noise.
#  ``-1`` means "every digit there is", the old unrounded behaviour.
DEFAULT_DECIMALS = 2


def format_value(value: Any, decimals: int = DEFAULT_DECIMALS) -> str:
    """A prop value as a person would write it in a box: ``120``, not ``120.0``.

    One formatter for every widget that shows what it holds, because "a number
    box shows 120 and the slider beside it shows 120.0" is the kind of defect a
    screenshot does not catch and a test does. ``None`` is nothing to show, and a
    list is the words a radio or a dropdown holds. A float is rounded to
    ``decimals`` digits and trailing zeros go with it - ``120.00`` reads ``120``
    and ``0.70000001`` reads ``0.7`` - because a box that shows ten places is
    showing the binary representation, not the value.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # ``:g`` would be shorter but loses the plot past six digits, and a value
        # box that reads "1.23457e+06" for 1234567.8 is a widget lying about what
        # it holds. ``repr`` of a float is Python's shortest round-trip form, so
        # 0.25 stays "0.25", 120.0 becomes "120", and 1234567.8 stays itself -
        # after the rounding above, which is what a readout owes the person.
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        digits = int(decimals)
        if digits >= 0:
            value = round(value, digits)
        if value.is_integer() and abs(value) < 1e16:
            return str(int(value))
        return repr(value)
    if isinstance(value, (list, tuple)):
        return " ".join(format_value(item, decimals) for item in value)
    return str(value)


class Widget:
    """One thing in the tree: a kind's props, a box, a place in the z-order.

    Build one with :meth:`Panel.add` or by naming a parent, and hold it with
    ``TEXT("METRONOME")`` in a script: the bindings (S7's ``crude/bindings/ui.py``)
    pass the constructor's positional arguments through the prop table as
    ``Widget("slider", {"label": "BPM", "min": 20, "max": 300, "value": 120})``,
    which is the same door a ``SET`` walks through and therefore the same clamping,
    the same coercion and the same errors.

    ``name`` is the word a script called it by. It is only for reports and
    :meth:`find` - identity is the object itself, and :attr:`uid` is a number for
    logs, which is not stable across runs and is not meant to be.
    """

    __slots__ = ("kind", "name", "uid", "parent", "on_change", "on_event",
                 "on_paint",
                 "x", "y", "width", "height",
                 "_state", "_children", "_screen", "_clip", "_origin",
                 "_inset", "_skin", "_from", "_changed", "_layout", "_grid",
                 "_behaviour")

    def __init__(self, kind: Any, values: Optional[Mapping[str, Any]] = None,
                 *, name: str = "",
                 parent: Optional["Widget"] = None) -> None:
        #: the row table this widget was built from, resolved once
        self.kind = get_kind(kind)
        self.name = str(name or "")
        self.uid = next(_UIDS)
        #: what the language layer installs: ``on_change(widget, prop, value)``,
        #  ``on_event(widget, name, *args)`` and ``on_paint(widget, renderer,
        #  skin) -> int``. Left alone they are inert: a widget with no bindings
        #  still holds its props and still paints.
        self.on_change: Optional[Callable[..., Any]] = None
        self.on_event: Optional[Callable[..., Any]] = None
        #: the third slot, and the one the tree cannot answer for itself: a script
        #  painter owns this widget's whole face when it is installed, which is
        #  why :class:`~crude.ui.handlers.Handlers` falls back to
        #  :meth:`base_face` rather than to a second drawing path
        self.on_paint: Optional[Callable[..., Any]] = None
        #: the box, in logical pixels, relative to the parent's content box
        self.x = 0.0
        self.y = 0.0
        self.width = 0.0
        self.height = 0.0
        # every prop at its default or at what was written; the four rows the
        # pointer owns arrive at their defaults because a constructor that could
        # say "hovered" could arrive on screen already lit up
        self._state: Dict[str, State] = dict(self.kind.states(values))
        self.parent: Optional[Widget] = None
        self._children: List[Widget] = []
        # the resolved geometry, as a detached widget would resolve it: its own
        # box is its screen box, its children start at its top-left, and nothing
        # clips it. A sync with a skin puts the padding into ``_inset`` and the
        # window into ``_clip``, and every later answer comes from those.
        self._screen: Rect = (0.0, 0.0, 0.0, 0.0)
        self._clip: Rect = (0.0, 0.0, 0.0, 0.0)
        self._origin: Point = (0.0, 0.0)
        self._inset = 0.0
        # the skin the tree was last laid out with, which is the only way a widget
        # added *between* two syncs can measure its own padding at all
        self._skin: Optional[Skin] = None
        self._from: tuple = ((0.0, 0.0), None)
        # the device grid the resolved boxes are snapped to, which no widget has
        # until a frame with a framebuffer says one: ``None`` is "every number is
        # logical", which is what a test and a headless run want to see
        self._grid: Optional[float] = None
        self._changed: List[str] = []
        # the placement policy - pack, anchors, stack, padding override - which no
        # widget has until a layout verb says something, so it starts empty
        self._layout: Any = None
        # the scratch a gesture in progress needs - a half-typed number, nothing
        # else so far - which only a kind with a behaviour is ever given
        self._behaviour: Optional[Dict[str, Any]] = None
        # what a press and a drag mean, per kind: the table in
        # :mod:`crude.ui.behaviour`, installed before a script's handlers can chain
        # in front of it, and left alone for every kind that answers no gesture
        install_behaviour(self)
        # the props that hold a widget up outside a parent's box are the ones a
        # script can write, so a panel is placed before its children are added
        # and a child added to it resolves against whatever the parent had
        if parent is not None:
            parent.add(self)
        else:
            self._resolve(self._from[0], self._from[1])

    # -- the props --------------------------------------------------------
    def _slot(self, prop: Any) -> State:
        """The storage behind one row, made now if the row arrived after this widget.

        ``PROP_DEFINE`` hands a row down to the variants that were built before it
        (:func:`~crude.ui.props._below`), so a kind can gain a prop while its
        widgets are already on screen. The table then answers for the new row -
        which is what ``WIDGET_HAS`` just told the script - and the value it holds
        is the default the declaration gave it, so the honest thing to do is
        materialise that rather than refuse the write the script was promised.
        """
        held = self._state.get(prop.name)
        if held is None:
            held = State(prop.default)
            self._state[prop.name] = held
        return held

    def get(self, name: Any) -> Any:
        """What one prop holds, whether or not a script may write it."""
        return self._slot(self.kind.get(name)).value

    def set(self, name: Any, value: Any) -> bool:
        """Write one prop through the table; whether the widget moved.

        The same ``Prop.settle`` a constructor went through, so the value that
        arrives has already been coerced and clamped against whatever its
        siblings currently hold - a slider at 300 that is told ``max=100`` comes
        back at 100, which is the range it is now drawn in.

        Returning whether anything moved is what lets ``LINK_VALUE`` stop a
        two-way link from echoing itself, and :attr:`changed` is what lets a
        frame drain what moved without a callback per prop.
        """
        prop = self.kind.settable(name)
        found = prop.settle(value, values=self._state)
        if not self._write(prop, found):
            return False
        self._sweep(prop.name)
        return True

    def _sweep(self, name: str) -> List[str]:
        """Re-settle the rows whose range the row that just moved decides.

        ``min`` and ``max`` are props a widget draws with *and* the bounds another
        prop is clamped by, so writing ``max=100`` to a slider holding 300 cannot
        leave the value at 300: the thumb would sit outside the track it is drawn
        on, and a ``LINK_VALUE`` would never hear that the value moved. The rows
        are looked up in the table rather than named here, so a script kind whose
        ``min`` takes its own bound from a third row settles in the same pass - and
        the walk is transitive, which is the only way a chain of bounds converges
        without a second copy of this function in the layer above.

        The same walk keeps the two ends of a range ordered, because a range whose
        ends have crossed is the one state the bounds rows can be in that no widget
        can draw - see :meth:`_crossed`.
        """
        moved: List[str] = []
        queue = [name]
        seen = {name}
        while queue:
            found = queue.pop(0)
            for prop in self.kind.props.values():
                if found not in prop.bounds:
                    continue
                self._crossed(prop, found, moved, queue)
                if prop.name in seen:
                    continue
                seen.add(prop.name)
                settled = prop.settle(self._slot(prop).value,
                                      values=self._state)
                if self._write(prop, settled):
                    moved.append(prop.name)
                    queue.append(prop.name)
        return moved

    def _crossed(self, prop: Any, name: str, moved: List[str],
                 queue: List[str]) -> bool:
        """Un-cross the two ends of a range one of them just moved past.

        ``bounds`` is where the table says a row's range lives in two siblings,
        which makes it also the only place written down that those two siblings are
        the ends of *one* range. A widget cannot draw an inverted range - there is
        no position between a minimum of 60 and a maximum of 40 - and a script that
        wrote one meant the number it said, not "swap the ends". So the end nobody
        touched follows the end that moved, which is what a slider does when a
        handle is dragged past its partner, and which arrives without this module
        ever naming ``min`` or ``max``.
        """
        first, second = (self.kind.props.get(word) for word in prop.bounds)
        if first is None or second is None or not prop.bounds[0]:
            # one-end bounds - a list's length, say - are not a pair of ends
            return False
        low, high = self._slot(first).value, self._slot(second).value
        if not (_number(low) and _number(high)) or low <= high:
            return False
        # the end that stayed put is the one told; the mover keeps what it asked
        other = first if name == second.name else second
        wanted = self._slot(self.kind.props.get(name) or prop).value
        if self._write(other, other.settle(wanted, values=self._state)):
            moved.append(other.name)
            queue.append(other.name)
            return True
        return False

    def _write(self, prop: Any, found: Any) -> bool:
        """The one door: settle is done, so store, remember and tell.

        The pointer's four rows are stored and nothing more. They are not changes
        a script asked for, so they do not join :attr:`changed` and do not reach
        ``ON_CHANGE`` - a slider dragged across a panel would otherwise report
        sixty hover changes a frame - and the events that caused them already went
        out through ``on_event``, which is where ``ON_HOVER`` and the rest live.
        """
        held = self._slot(prop)
        if not prop.would_move(held.value, found):
            return False
        held.value = found
        if prop.name in POINTER:
            return True
        if prop.name not in self._changed:
            self._changed.append(prop.name)
        if prop.name in ("enabled", "visible") and not found:
            # a widget that cannot be used or cannot be seen is not something the
            # pointer is on, however it got that way
            self._clear_pointer()
        if self.on_change is not None:
            self.on_change(self, prop.name, found)
        return True

    def _machine(self, name: Any, value: Any) -> bool:
        """Write a prop the table says the pointer owns. Not for a script.

        ``Prop.readonly`` is a refusal aimed at ``SET``, not at the input system
        that has to be able to say "this one is hovered", so this is the door the
        handlers below use and the only one that bypasses :meth:`set`.

        It still cannot light up a widget that is hidden or switched off: the
        router's hit test would never have reached it, and a handler called by hand
        on the way past a ``SET`` is not a second route into a state the person is
        not looking at. Writing ``False`` is always allowed - letting go is not a
        claim about what is on screen.
        """
        prop = self.kind.get(name)
        wanted = prop.settle(value, values=self._state)
        if wanted and not (self.visible and self.enabled):
            return False
        return self._write(prop, wanted)

    def _measure(self, name: Any, value: Any) -> bool:
        """Store a read-only row the layout pass answers for; tell nobody.

        A scroll area's ``content`` and ``travel`` are the same kind of fact as the
        pointer's four rows - the machine worked it out, the script did not ask for
        it - but they cannot share :meth:`_machine`, which reports through
        ``ON_CHANGE``. A measurement that moved because a window grew is not an edit
        a person made, and an ``ON_CHANGE`` wired to a scroll area would fire once
        per frame of a resize. So this writes the slot and nothing else: ``GET``
        reads it, the paint key changes with it, ``changed`` stays empty.
        """
        prop = self.kind.get(name)
        held = self._state.get(prop.name)
        if held is None:
            return False
        found = prop.settle(value, values=self._state)
        if not prop.would_move(held.value, found):
            return False
        held.value = found
        return True

    def remeasure(self) -> "Widget":
        """Re-resolve this box and everything under it, where it stands.

        The one thing a widget built between two frames cannot do for itself: a
        padding overruled, an anchor added, a child adopted - each changes the space
        children are measured *in*, which is remembered as an inset and only
        refreshed by a resolve. A caller that has just written such a fact asks for
        this one, so the next ``hit`` and the next ``paint`` agree without waiting a
        frame for ``UIContext.layout`` to notice.
        """
        self._resolve(*self._from, dpr=self._grid)
        return self

    def values(self) -> Dict[str, Any]:
        """Every prop as a plain map - what :func:`digest` and ``PD_EMIT`` read."""
        return {name: held.value for name, held in self._state.items()}

    def writable(self) -> Dict[str, Any]:
        """The map a script's ``GET``/``SET`` pair sees: every row but the pointer's.

        The four state rows are still readable through :meth:`get` - a script may
        ask whether its widget is hovered - but a ``SET`` on them is refused by the
        table, so this is the honest answer to "what can I write on this widget"
        and the thing a preset serialiser wants.
        """
        return {name: held.value for name, held in self._state.items()
                if name not in POINTER}

    def take_changes(self) -> List[str]:
        """The props written since last time, in order, and an emptied list.

        Drained by the frame rather than acted on per write, so a slider dragged
        across sixty frames reports sixty changes without sixty Pd sends.
        """
        found, self._changed = self._changed, []
        return found

    @property
    def changed(self) -> List[str]:
        """The props waiting to be drained - what ``ON_CHANGE`` was told about."""
        return list(self._changed)

    # -- the props a painter asks for by name -----------------------------
    @property
    def label(self) -> str:
        return self._state["label"].value

    @property
    def role(self) -> str:
        return self._state["role"].value

    @property
    def size(self) -> str:
        return self._state["size"].value

    @property
    def visible(self) -> bool:
        return self._state["visible"].value

    @property
    def enabled(self) -> bool:
        return self._state["enabled"].value

    @property
    def hovered(self) -> bool:
        return self._state["hovered"].value

    @property
    def pressed(self) -> bool:
        return self._state["pressed"].value

    @property
    def focused(self) -> bool:
        return self._state["focused"].value

    @property
    def selected(self) -> bool:
        return self._state["selected"].value

    @property
    def has_value(self) -> bool:
        """Whether this kind holds a number at all - a label does not."""
        return "value" in self.kind.props

    @property
    def value(self) -> Any:
        """What the widget holds, for a kind that holds anything.

        A ``panel`` or a ``text`` has no value row, and asking for one is the
        script's mistake to hear about, with the names it could have asked for.
        """
        if not self.has_value:
            raise WidgetError(f"a '{self.kind.name}' holds nothing; "
                              f"'{self.name or self.kind.name}' has no 'value' "
                              "prop")
        return self._slot(self.kind.props["value"]).value

    @value.setter
    def value(self, value: Any) -> None:
        if not self.has_value:
            raise WidgetError(f"a '{self.kind.name}' cannot hold a value")
        self.set("value", value)

    def value_text(self) -> str:
        """The value as a person would write it in a box; ``""`` for a kind that
        holds nothing, which is what lets a painter ask the question of anything.

        The widget's own ``decimals`` row - every value-holding kind has one -
        says how many places to show; ``-1`` is every digit there is."""
        if not self.has_value:
            return ""
        held = self.kind.props.get("decimals")
        digits = int(self._slot(held).value) if held is not None \
            else DEFAULT_DECIMALS
        return format_value(self._slot(self.kind.props["value"]).value, digits)

    def readout(self) -> str:
        """The words a box that is drawn to be read off shows: its number, or its choice.

        A ``field``-role widget carries the thing it holds rather than the name it
        was given - that is what makes a number box read ``120`` and the slider
        beside it read ``BPM``, with no list of kinds anywhere. An index into
        ``labels`` is answered with the entry it points at, because a folded list
        is read as the choice it is showing and not as the number of that choice,
        and a half-typed edit is echoed while it is being typed, because a field
        that shows nothing back is a field nobody can read.

        Everything here is the same value the props table holds, through the same
        :func:`format_value` a committed edit walks, so the number in the box and
        the number a ``GET`` hands back cannot disagree.
        """
        said = (self._behaviour or {}).get("typing")
        if said:
            return str(said)
        if "labels" in self.kind.props and self.has_value:
            items = self._slot(self.kind.props["labels"]).value or ()
            wanted = int(self._slot(self.kind.props["value"]).value or 0)
            # a Python negative would read as "from the end", and an index that
            # only ever meant the first entry is not a licence to wrap the list
            return str(items[wanted]) if 0 <= wanted < len(items) else ""
        return self.value_text() or self.label

    # -- the Pd twin ------------------------------------------------------
    @property
    def pd_object(self) -> str:
        """The box this widget would write, following the table's own order.

        :func:`~crude.ui.props.pd_object_for` is the resolution - the widget's
        ``pd_object`` prop, else the kind's inherited box, else nothing - and is
        the same answer ``PD_EMIT`` will act on in S8, so a script can ask what a
        widget twins to without going through the emitter.
        """
        return pd_object_for(self.kind, self.values())

    def pd_arguments(self) -> Dict[str, Any]:
        """The named arguments that box wants, at the values held now."""
        return pd_arguments(self.kind, self.values())

    # -- the box ----------------------------------------------------------
    def place(self, x: Optional[float] = None, y: Optional[float] = None,
              width: Optional[float] = None,
              height: Optional[float] = None) -> bool:
        """Say where it goes, in logical pixels, relative to the parent's content.

        Anything left out stays as it was, because ``PLACE(w, 40, 90)`` moving
        only the y is a thing an anchor edit wants. Returns whether the box
        actually changed, which is whether the paint key did.
        """
        was = (self.x, self.y, self.width, self.height)
        if x is not None:
            self.x = float(x)
        if y is not None:
            self.y = float(y)
        if width is not None:
            self.width = max(0.0, float(width))
        if height is not None:
            self.height = max(0.0, float(height))
        if (self.x, self.y, self.width, self.height) != was:
            # the grid is this widget's own to keep: a box moved by hand between
            # two frames is drawn on the same device grid as the frame around it
            self._resolve(*self._from, dpr=self._grid)
            return True
        return False

    @property
    def box(self) -> Rect:
        """The box as it was placed: relative to the parent, floats, unrounded."""
        return (self.x, self.y, self.width, self.height)

    @property
    def screen_box(self) -> Rect:
        """The absolute box the last resolve produced - what is drawn into."""
        return self._screen

    @property
    def clip(self) -> Rect:
        """The absolute rect this widget may draw in, and be hit through."""
        return self._clip

    def content_box(self) -> Rect:
        """The absolute box children are placed in and clipped to.

        The screen box with the skin's padding and the plate's own border taken
        off every side, at the inset the last :meth:`sync` measured - which is
        why this needs no skin argument and why a panel that has never been
        synced says its content is its whole box. A widget too small for its own
        padding has no content box, and its children are placed and clipped
        accordingly: a panel that is 6 px wide is a panel with nothing in it.
        """
        left, top, width, height = self._screen
        inset = min(self._inset, width * 0.5, height * 0.5)
        return (left + inset, top + inset,
                max(0.0, width - 2 * inset), max(0.0, height - 2 * inset))

    @property
    def layout(self) -> Any:
        """The placement policy this widget was given, or ``None``.

        :mod:`crude.ui.layout` owns the object and is the only thing that creates
        one, because a widget nobody told where to go when the window grew has no
        policy to hold - and five hundred of them should not each pay for an empty
        one. The tree reads two facts off it: the padding override, which changes
        where children start, and nothing else.
        """
        return self._layout

    def layout_skin(self) -> Optional[Skin]:
        """The skin this tree was last resolved with, or ``None`` before that.

        A layout verb that wants a metric token rather than a raw number has to ask
        a skin, and the only skin it can meaningfully ask is the one the widget's
        own tree is being drawn with - which is this.
        """
        return self._skin

    # -- the tree ---------------------------------------------------------
    @property
    def is_container(self) -> bool:
        """Whether things may be put inside it - a panel, or anything built on one."""
        return CONTAINER_KIND in self.kind.chain()

    @property
    def children(self) -> List["Widget"]:
        """The children, in z-order: bottom first, top last."""
        return list(self._children)

    @property
    def depth(self) -> int:
        """How many panels it is inside: a window's child is 0."""
        return len(self.path_parts()) - 1

    def root(self) -> "Widget":
        """The window's root, which is the tree this widget belongs to."""
        held = self
        while held.parent is not None:
            held = held.parent
        return held

    def path_parts(self) -> List[str]:
        """This widget and every ancestor, from the root down."""
        parts, held = [], self
        seen = set()
        while held is not None and id(held) not in seen:
            seen.add(id(held))
            parts.append(held.name or held.kind.name)
            held = held.parent
        parts.reverse()
        return parts

    def path(self, sep: str = "/") -> str:
        """``"root/bpm"`` - how a report or a log names a widget without a pointer."""
        return sep.join(self.path_parts())

    def is_in(self, other: "Widget") -> bool:
        """Whether this widget is ``other`` or something inside it.

        The question a removed subtree and an anchored outsider both ask, which is
        why it walks up from here rather than down from ``other``: a widget knows
        its own parents and knows nothing about anyone else's children.
        """
        held: Optional[Widget] = self
        while held is not None:
            if held is other:
                return True
            held = held.parent
        return False

    def add(self, child: "Widget") -> "Widget":
        """Put ``child`` inside this one, on top of everything already in here.

        A widget has one parent, so a child that was somewhere else moves rather
        than appearing twice. A leaf refuses, by name and with the kind it
        actually is, because "why won't my slider take a child" is a question
        about the prop table and should be answered in those terms.

        A viewport is the one container that does not keep what it is handed: its
        children go to its :attr:`content_child`, because the thing that has to
        move when the wheel turns is the content and not the window onto it.
        """
        if child is self:
            raise WidgetError("a widget cannot be inside itself")
        if not self.is_container:
            raise WidgetError(f"a '{self.kind.name}' holds no children; use "
                              f"'{CONTAINER_KIND}', or WIDGET_DEFINE a kind on "
                              f"top of one, for a widget to live in "
                              f"'{self.path()}'")
        if self.is_in(child):
            # the child already contains the parent-to-be: a draw pass walking into
            # it would walk back out of it and into the same child again forever
            raise WidgetError(f"'{child.path()}' cannot hold '{self.path()}': "
                              "that would make a loop the draw pass could not leave")
        found = self.content_child
        if found is not None and found is not self:
            if found is child or child.is_in(found):
                # the inner panel is the viewport's own; putting it, or something
                # inside it, back into the viewport would hang the content off the
                # window it is already the content of
                raise WidgetError(f"'{child.path()}' is already the content of "
                                  f"'{self.path()}'")
            return found.add(child)
        child.detach()
        child.parent = self
        self._children.append(child)
        # the parent's own box is already resolved, and no child can change it, so
        # a widget added between two frames lands where the next sync would put it -
        # in this tree's skin, which an un-laid-out parent has nothing to say about
        child._resolve(*self._kid_place(), skin=self._skin, dpr=self._grid)
        return child

    @property
    def content_child(self) -> Optional["Widget"]:
        """Where this container's children actually go: itself, unless a viewport.

        A :data:`CONTENT_KIND` widget builds one inner panel the first time it is
        asked to hold something, and every later ``add`` walks through it, so a
        scroll area is a container with a content box that can be slid rather than
        a private convention inside whoever made it. The inner panel is a real
        child: :meth:`walk`, the clip a panel owes its children, hit testing and a
        removal all keep answering for it, and a script that wants the rows themselves
        can hold ``widget.content_child`` rather than guess at a name.
        """
        if CONTENT_KIND not in self.kind.chain():
            return None
        for kid in self._children:
            if kid.name.endswith(CONTENT_SUFFIX) and "panel" in kid.kind.chain():
                return kid
        return self._build_content()

    def _build_content(self) -> "Widget":
        """Make a viewport's inner panel, placed to be slid and to keep its width.

        Built here rather than through :meth:`add` because ``add`` is the function
        this one serves: a child created inside a viewport that then routed itself
        through ``add`` would ask the same question again, and the answer is the
        panel it is trying to build. The box is the three numbers the measure reads
        - ``x`` at the content edge, ``y`` at the current offset, the height nothing
        but the rows can answer for - and the two layout verbs are imported where
        they run, because :mod:`crude.ui.layout` resolves widgets and so cannot be
        imported by the module they are in.
        """
        from .layout import anchor, pack, pad as pad_children

        inner = Widget("panel", {"label": ""},
                       name=f"{self.name or 'scroll'}{CONTENT_SUFFIX}")
        inner.parent = self
        self._children.append(inner)
        # the viewport's own inset first, so the content is measured from the edge
        # children are actually placed at rather than from the box's corner
        if self._layout is not None and self._layout.pad is not None:
            self._inset = float(self._layout.pad)
        # the inner panel owns no padding of its own: the rows are measured from
        # the edge of the space the viewport gives them, and the viewport's own
        # padding is the inset that keeps them off its border
        pad_children(inner, 0.0)
        # nothing is stacked inside it - the content places its own rows, and a
        # filler child in a box the measure owns would be arranged against a height
        # nobody has worked out yet
        pack(inner, "fixed", "fixed")
        # glued to the viewport's own width so a window resize is what the rows are
        # measured against, and to nothing on y: the offset owns that axis. The
        # width is taken from the content box here as well, because a tree that has
        # never been resolved has never run the glue: a zero-width content panel
        # would clip its own rows to nothing and a paper test could neither paint a
        # row nor click one.
        anchor(inner, "left")
        anchor(inner, "right")
        inner.place(0.0, float(self.get("offset") or 0.0),
                    max(0.0, self.content_box()[2]), 0.0)
        self.remeasure()
        return inner

    #: ``PANEL``-and-``NODE_ADD`` spell it as a method too, for a caller that has
    #  the parent in hand rather than the child
    def node_add(self, child: "Widget") -> "Widget":
        return self.add(child)

    def add_all(self, *kids: "Widget") -> "Widget":
        """Several children at once, in the order they are given."""
        for kid in kids:
            self.add(kid)
        return self

    def remove(self, child: "Widget") -> "Widget":
        """Take one child out of the z-order; it keeps its props and its box."""
        found = self.content_child
        if (found is not None and found is not self and child is not found
                and child.parent is not self):
            # ``remove`` reads the other way round from ``add``: the caller holds a
            # row and points at the viewport, so a child of the content is this
            # viewport's to take away, and a leaf under it is not nothing
            return found.remove(child)
        if child.parent is not self:
            raise WidgetError(f"'{child.path()}' is not inside '{self.path()}', "
                              "so there is nothing here to remove")
        self._children.remove(child)
        child.parent = None
        child._clear_pointer()
        child._resolve((0.0, 0.0), None)
        return child

    def detach(self) -> "Widget":
        """Leave whatever tree this widget was in; a detached widget is rootable."""
        if self.parent is not None:
            self.parent.remove(self)
        return self

    def clear(self) -> List["Widget"]:
        """Take every child out, in z-order; what a rebuilt panel does to its own.

        A viewport clears its content, not itself - the inner panel is the one thing
        a rebuilt scroll area keeps, because the caller who says "empty this" means
        the rows and not the box that slides them.
        """
        found = self.content_child
        if found is not None and found is not self:
            return found.clear()
        gone = list(self._children)
        for kid in gone:
            self.remove(kid)
        return gone

    def walk(self) -> Iterator["Widget"]:
        """This widget and every one under it, depth-first, bottom of the stack first."""
        yield self
        for kid in self._children:
            yield from kid.walk()

    def descendants(self) -> Iterator["Widget"]:
        """:meth:`walk`, without the receiver."""
        return (held for held in self.walk() if held is not self)

    def count(self) -> int:
        """How many widgets are in this subtree, including this one."""
        return sum(1 for _ in self.walk())

    def find(self, name: Any) -> Optional["Widget"]:
        """The first widget in the subtree named ``name``, this one included.

        Names are what a script gave, so a lookup matches the name and then the
        kind - ``find("bpm")`` on an unnamed slider whose kind is ``bpm_slider``
        is a script reaching for the one widget it made.
        """
        said = str(name or "").strip().lower()
        if not said:
            return None
        for held in self.walk():
            if held.name.lower() == said:
                return held
        for held in self.walk():
            if held.kind.name.lower() == said:
                return held
        return None

    # -- the z-order ------------------------------------------------------
    @property
    def z(self) -> int:
        """Its place in the parent's stack: 0 is the bottom, and a root is ``0``."""
        if self.parent is None:
            return 0
        return self.parent._children.index(self)

    def raise_to_top(self) -> "Widget":
        """Last in the stack: drawn over its siblings, and hit before them."""
        return self._move_z(len(self._siblings()))

    def lower_to_bottom(self) -> "Widget":
        """First in the stack: behind every sibling."""
        return self._move_z(0)

    def lift(self, steps: int = 1) -> "Widget":
        """Towards the top by ``steps``, staying inside the siblings it has."""
        return self._move_z(self.z + int(steps))

    def sink(self, steps: int = 1) -> "Widget":
        """Towards the bottom by ``steps``."""
        return self._move_z(self.z - int(steps))

    def _move_z(self, wanted: int) -> "Widget":
        held = self._siblings()
        if not held:
            return self  # a root, or an only child, has nothing to move between
        index = held.index(self)
        target = max(0, min(int(wanted), len(held) - 1))
        if target != index:
            held.remove(self)
            held.insert(target, self)
            self.parent._children = held
        return self

    def _siblings(self) -> List["Widget"]:
        return list(self.parent._children) if self.parent is not None else []

    def front_to_back(self) -> List["Widget"]:
        """The subtree in the order a hit test should try it: topmost first."""
        out: List[Widget] = []
        for kid in reversed(self._children):
            out.extend(kid.front_to_back())
        out.append(self)
        return out

    # -- hit testing ------------------------------------------------------
    def hit(self, x: float, y: float) -> Optional["Widget"]:
        """The topmost widget at this point of the window, or ``None``.

        Three things take a widget out of the running before its box is even
        measured: an ancestor that is hidden, an ancestor that is switched off -
        a disabled panel takes everything inside it out, which is what a person
        reading "that whole block is off" expects - and a point outside the clip
        the last sync resolved, so a child that overflows its panel is not
        clickable where it is not drawn either.

        The box is then tested in the order the plan fixes: children topmost
        first, so whatever is on screen at that pixel is what is answered, and a
        container only claims the point if nothing inside it did.
        """
        if not self.visible:
            return None
        if not self.enabled:
            return None
        if not _inside(self._clip, x, y):
            return None
        for kid in reversed(self._children):
            found = kid.hit(x, y)
            if found is not None:
                return found
        return self if _inside(self._screen, x, y) else None

    def hit_path(self, x: float, y: float) -> List["Widget"]:
        """Every widget under the point, topmost first - what a hover cascade reads."""
        held = self.hit(x, y)
        chain: List[Widget] = []
        while held is not None:
            chain.append(held)
            held = held.parent
        return chain

    # -- focus ------------------------------------------------------------
    def focusable(self) -> bool:
        """Whether the keyboard can be aimed at it, which the skin decides.

        ``ROLES[role].responds("focus")`` is the one table that knows a caption
        does not take a focus ring and a field does, so "what is tabbable" cannot
        drift from "what looks focused" - the two would be the same question asked
        twice, in two files, about a role a project may rename at any time.
        """
        if not self.visible or not self.enabled:
            return False
        return ROLES[as_role(self.role)].responds("focus")

    def tabbables(self) -> List["Widget"]:
        """The focusable widgets in this subtree, in the order they are drawn.

        The receiver is included if it takes the keyboard itself, and a plain
        panel does not, so a root's list is its controls - in z-order, which is
        the order a person sees them in and the order ``RAISE`` edits.
        """
        return [held for held in self.walk() if held.focusable()]

    def grab_focus(self) -> bool:
        """Aim the keyboard at this widget; whether anything changed.

        One focus per tree, kept in the tree rather than in the router: the
        ``focused`` prop is the answer to "is this the widget with the keyboard",
        so a rebuild that loses the router's pointer cannot leave two widgets
        claiming it and a script's ``GET(w, "focused")`` reads the truth.
        """
        if not self.focusable():
            return False
        held = self.root().find_focus()
        if held is self:
            return False
        if held is not None:
            held._machine("focused", False)
        return self._machine("focused", True)

    def release_focus(self) -> bool:
        """Give up the keyboard, whoever is holding it."""
        return self._machine("focused", False)

    def find_focus(self) -> Optional["Widget"]:
        """The widget in this subtree that holds the keyboard, or ``None``."""
        return next((held for held in self.walk() if held.focused), None)

    def next_focus(self, step: int = 1) -> Optional["Widget"]:
        """The next tabbable widget, wrapping round; ``step`` may be negative.

        Order is the draw order of the root, which is insertion order and then
        whatever ``RAISE`` did to it - the same order the person sees, and the one
        that needs no separate tab-index prop the table does not have.
        """
        pool = self.root().tabbables()
        if not pool:
            return None
        if self not in pool:
            return pool[0]
        index = pool.index(self)
        return pool[(index + int(step)) % len(pool)]

    def blur_all(self) -> "Widget":
        """Take the pointer and the keyboard off everything in this subtree."""
        for held in self.walk():
            held._clear_pointer()
        return self

    def _clear_pointer(self) -> None:
        """The widget stops being the one the pointer is on, silently if need be."""
        for name in _DROPPED:
            self._machine(name, False)

    # -- the pointer's calls ----------------------------------------------
    # These are the names :class:`~crude.ui.input.InputRouter` looks for on
    # whatever its hit test returns, so a router wired to ``Widget.hit`` needs no
    # adapters. Each one keeps the state map honest and then tells
    # ``on_event``, which is where ``ON_CLICK`` and the rest live.

    def on_enter(self) -> None:
        if self._machine("hovered", True):
            self._fire("on_enter")

    def on_leave(self) -> None:
        if self._machine("hovered", False):
            self._fire("on_leave")

    def on_move(self, x: float, y: float) -> None:
        self._fire("on_move", x, y)

    def on_press(self, x: float, y: float, button: int = 0,
                 mods: int = 0) -> None:
        self._machine("pressed", True)
        self._fire("on_press", x, y, button, mods)

    def on_release(self, x: float, y: float, button: int = 0,
                   mods: int = 0) -> None:
        self._machine("pressed", False)
        self._fire("on_release", x, y, button, mods)

    def on_click(self, x: float, y: float, button: int = 0) -> None:
        """A press and a release on this widget - which is all a click means here.

        Whether the click then moves a value is the kind's own answer, in
        :data:`~crude.ui.behaviour.BEHAVIOURS`: a ``bng`` bounces, a ``tgl``
        flips, a slider has already jumped at the press. Nothing in this module
        knows any of that, which is the point - the table and a script's
        ``ON_CLICK`` are the one place a gesture's meaning is written down.
        """
        self._fire("on_click", x, y, button)

    def on_focus(self) -> None:
        if self.grab_focus():
            self._fire("on_focus")

    def on_blur(self) -> None:
        self._machine("focused", False)
        self._fire("on_blur")

    def on_key(self, name: str, mods: int = 0) -> None:
        self._fire("on_key", name, mods)

    def on_char(self, text: str) -> None:
        self._fire("on_char", text)

    def on_scroll(self, dx: float, dy: float, mods: int = 0) -> None:
        """The wheel, which the router already turned into fine or coarse steps."""
        self._fire("on_scroll", dx, dy, mods)

    def on_drag(self, dx: float, dy: float, mods: int = 0) -> None:
        """A movement of the pointer while this widget holds it."""
        self._fire("on_drag", dx, dy, mods)

    # -- the window's calls, which only the root ever gets ----------------
    def on_resize(self, width: float, height: float) -> bool:
        """The window changed size, so the client rect - this widget - did.

        The plan's one exception to "placement is a script's to say": a root *is*
        the client rect, so a resize re-places it and re-resolves the tree with the
        skin it was last laid out in, which is what lets an anchor, a stack and a
        scroll bar all be re-measured from a tree that is already in the right shape
        rather than from a rebuild. Only a root answers; a widget inside a panel is
        the size its layout made it and a resize has to go through that layout.
        """
        if self.parent is not None or self._skin is None:
            return False
        moved = self.place(0, 0, float(width), float(height))
        # the grid the tree was being drawn on is the one it is re-measured on:
        # a resize lands between two frames, not instead of one
        self.sync(self._skin, dpr=self._grid)
        self._fire("on_resize", float(width), float(height))
        return moved

    def on_drop(self, paths: Any) -> None:
        """Files were dropped on the window - a fact for a script, not a value."""
        self._fire("on_drop", tuple(paths or ()))

    def on_close_request(self) -> Optional[bool]:
        """Ask whether the window may go; ``None`` is "yes, and nothing objected".

        The answer a script gives belongs to the layer that holds its handlers
        (S7's context, S8's ``ON_CLOSE``), which installs :attr:`on_event` and can
        wrap this call; the tree's own answer is the permissive one, because a
        widget with no bindings at all has no reason to hold a window open.
        """
        self._fire("on_close")
        return None

    def _fire(self, name: str, *args: Any) -> None:
        if self.on_event is not None:
            self.on_event(self, name, *args)

    # -- geometry, resolved -----------------------------------------------
    def sync(self, skin: Skin, *, origin: Point = (0.0, 0.0),
             clip: Optional[Rect] = None, dpr: float = None) -> int:
        """Resolve this widget and its subtree against ``skin``; how many widgets.

        The layout pass (S7's ``UIContext``) calls this once per frame, after
        anchors and stacks have said where things go and before anything is
        drawn. Three numbers per widget come out of it - the screen box, the clip,
        and the origin its own children are measured from - and they are what
        :meth:`hit` and :meth:`paint` use, so a widget cannot be drawn at a place
        the pointer disagrees with.

        ``origin`` and ``clip`` are for a caller that knows better than the top of
        the tree: a panel inside a canvas hands its own corner and its own cut.

        ``dpr`` is the device pixel ratio the frame was begun at. Given one, each
        resolved screen box is moved onto that grid - the plan's one rounding, and
        the reason a row of buttons stacked at ``20.4`` and ``80.4`` does not gain a
        hairline of background between the two at a 125% scale. Left out, every
        number stays logical, which is what a paper test and a headless run want:
        the box a script wrote is never modified either way, only the box that is
        drawn.

        The skin is remembered on every widget it touches, because the padding a
        child is measured from is a fact about the skin and a widget added after
        this frame would otherwise resolve against none and land nine pixels off
        until the next sync - a click on it would mean the widget beside it.
        """
        self._skin = skin
        self._resolve(origin, clip, dpr=dpr)
        return self.count()

    def _plate(self, skin: Skin) -> float:
        """How far in a child starts: this widget's padding plus its own border.

        One number rather than four, because every token in the skin's metric
        table is square - a panel that wanted a different left padding would want
        a role that does not exist - and a ``pad.lg`` a project overrides to a
        rectangle is not this layer's problem to guess at.

        A root has no inset of its own, because the root *is* the window's client
        rect: a plan that says ``PLACE(title, 16, 12)`` means sixteen pixels from
        the edge of the window, and a root panel that applied its own ``pad.md``
        first would be a spec the example does not match. A project that wants a
        margin puts a panel inside the root and places things in that - unless it
        says so, which is what the ``pad`` override below is for: an explicit
        padding is the inset, border included, for any widget that was given one.
        """
        held = self._layout
        if held is not None and held.pad is not None:
            return held.pad
        if self.parent is None:
            return 0.0
        found = self.style(skin).resolve(self.role)
        return found.padding + found.width

    def _label_allowance(self) -> Tuple[float, float, float, float]:
        """Extra draw-clip on each side, for a word placed outside the box.

        Only the sides a placement actually names are grown, and by the measured
        text plus the gap :func:`~crude.ui.faces.placed_text` leaves it and any
        nudge - so a caption pushed ``top`` gets exactly the room it needs and a
        widget whose words are all ``inside`` is left with the box it had. This
        widens the draw clip and nothing else: ``_screen`` - the hit rectangle -
        is untouched, so grown margin never turns a label into a click target and
        can never reach into a neighbour's box (the final hit claim is always
        ``_screen``, so a grown clip alone returns nothing).
        """
        skin = self._skin
        if skin is None:
            return (0.0, 0.0, 0.0, 0.0)
        props = self.kind.props
        found = self.style(skin).resolve(self.role, "normal")
        font = found.font
        gap = max(found.width, 1.0)
        dl = dt = dr = db = 0.0

        def grow(which: str, text: Any) -> None:
            nonlocal dl, dt, dr, db
            if not text or f"{which}_place" not in props:
                return
            place = self.get(f"{which}_place") or "inside"
            if place not in SIDES:
                return
            dx = float(self.get(f"{which}_dx") or 0.0)
            dy = float(self.get(f"{which}_dy") or 0.0)
            wide, high = text_bounds(str(text), font)
            if place in ("top", "bottom"):
                # the word is centred on the box's horizontal middle, so one wider
                # than the box overhangs both sides and would be scissored there;
                # grow the cross axis to reach it too, plus any horizontal nudge
                across = max(0.0, wide - self.width) / 2.0 + abs(dx)
                dl = max(dl, across)
                dr = max(dr, across)
                if place == "top":
                    dt = max(dt, high + gap + abs(dy))
                else:
                    db = max(db, high + gap + abs(dy))
            else:  # left / right: centred vertically, so it can overhang top/bottom
                across = max(0.0, high - self.height) / 2.0 + abs(dy)
                dt = max(dt, across)
                db = max(db, across)
                if place == "left":
                    dl = max(dl, wide + gap + abs(dx))
                else:
                    dr = max(dr, wide + gap + abs(dx))

        grow("label", self.label)
        grow("value", self.readout() if self.has_value else "")
        return (dl, dt, dr, db)

    def _resolve(self, origin: Point, clip: Optional[Rect],
                 skin: Optional[Skin] = None, dpr: float = None) -> None:
        """The maths :meth:`sync` did with a skin, kept for a box that just moved.

        The inset is measured here rather than in :meth:`sync`, because every way a
        box can move - a ``place``, an ``add``, a removal - comes through this
        function and only this function, and a widget that re-resolved its own
        children with a stale padding would be hit-testable where it is not drawn.
        A widget that has never been synced has no skin to ask, and keeps the inset
        it has, which is ``0.0`` for anything built outside a laid-out tree.

        ``dpr`` is the device grid, and every caller states it: a ``place`` between
        two frames keeps the grid the frame has, a removal leaves a subtree with
        none, and a walk down to the children passes on what this widget was told -
        which is the only reason a snapped parent and a snapped child cannot end up
        measured against two different screens.

        ``skin`` is threaded down the subtree rather than remembered per widget
        because a theme change is one call on the root: a tree whose children kept
        the skin they were first added under would measure its paddings from the old
        theme and paint the new one, and the plan's hit test and its screen would
        part company by exactly the difference between the two.
        """
        if skin is not None:
            self._skin = skin
        held = self._layout
        if self._skin is not None:
            self._inset = self._plate(self._skin)
        elif held is not None and held.pad is not None:
            # an explicit padding is the inset and needs no skin to measure it, and
            # a container that was given one between two frames owes its children
            # the answer now rather than after the next resolve
            self._inset = float(held.pad)
        self._grid = float(dpr) if dpr and float(dpr) > 0 else None
        self._from = (origin, clip)
        box = (origin[0] + self.x, origin[1] + self.y, self.width, self.height)
        self._screen = snap_box(box, self._grid) if self._grid else box
        self._clip = (clip_overlap(clip, self._screen) if clip is not None
                      else self._screen)
        # grow only the draw clip on the sides a word is placed outside, so the
        # caption survives past the box; _screen is untouched, so a hit still
        # has to land on the widget and a grown clip cannot steal a neighbour's
        grown = self._label_allowance()
        if any(grown):
            cl, ct, cw, ch = self._clip
            dl, dt, dr, db = grown
            self._clip = (cl - dl, ct - dt, cw + dl + dr, ch + dt + db)
        place = self._kid_place()
        for kid in self._children:
            kid._resolve(place[0], place[1], self._skin, self._grid)

    def _kid_place(self) -> Tuple[Point, Optional[Rect]]:
        """The origin and the clip this widget's children get: its content box.

        One answer rather than two, because a child added between frames must be
        resolved with exactly what the next :meth:`sync` would give it - the same
        maths written in two places is how a widget ends up drawn in one place and
        clicked in another.
        """
        inner = self.content_box()
        return (inner[0], inner[1]), clip_overlap(self._clip, inner)

    # -- painting ---------------------------------------------------------
    def style(self, skin: Skin) -> Skin:
        """The skin at this widget's own ``size``, which is what both maths and
        painting ask for: a ``sm`` panel holds its children in closer than an
        ``xl`` one, so the scale has to be resolved before the padding is measured.

        ``STYLE(w, "size", "sm")`` is a per-widget thing in a project that has one
        skin, so the scaled skins are cached on the pair that decides them and a
        hundred small widgets share one object. The cache is keyed by the digest
        because two skins with equal digests are equal by :class:`Skin`'s own
        definition, so there is nothing for a scale to disagree about - and a
        widget at the skin's own size returns it untouched, which is the usual case
        and costs nothing.
        """
        if self.size == skin.scale_name:
            return skin
        key = (skin.digest, self.size)
        held = _STYLED.get(key)
        if held is None:
            held = _STYLED[key] = skin.with_scale(self.size)
        return held

    def state_name(self) -> str:
        """This widget's state as one of the strings :class:`Skin` answers.

        Value and pointer in one string, because a toggle that is on *and* hovered
        is one question and the skin composes the two halves. The interaction half
        is picked by what is happening most directly: switched off, else pushed,
        else hovered, else focused - and a widget that is both hovered and focused
        shows the hover, because the pointer is the thing the person is looking at.
        """
        if not self.enabled:
            acts = "disabled"
        elif self.pressed:
            acts = "pressed"
        elif self.hovered:
            acts = "hover"
        elif self.focused:
            acts = "focus"
        else:
            acts = ""
        role = ROLES[as_role(self.role)]
        if self.selected:
            holds = "selected"
        elif self.has_value and self._slot(self.kind.props["value"]).value \
                and role.holds("on"):
            holds = "on"
        else:
            holds = ""
        return " ".join(word for word in (holds, acts) if word) or "normal"

    def paint(self, renderer: Renderer, skin: Skin) -> int:
        """Draw this widget and everything inside it; how many ops it took.

        The op count is not decoration: the plan's ``ON_FRAME`` budget (500
        widgets under 2 ms) and ``crude run --stats`` are both counted in ops, and
        a widget that could not say how many it cost would leave that number to be
        guessed at from a frame total.
        """
        if not self.visible:
            return 0
        before = renderer.op_count
        inside = self._clip != renderer.clip
        if inside:
            renderer.push_clip(*self._clip)
        try:
            self.paint_face(renderer, skin)
            for kid in self._children:
                kid.paint(renderer, skin)
        finally:
            if inside:
                renderer.pop_clip()
        return renderer.op_count - before

    def paint_face(self, renderer: Renderer, skin: Skin) -> int:
        """The widget's own face - the script's, if it has one. Never its children.

        One branch, and it is the whole of what the tree knows about script
        painters: a widget whose ``on_paint`` slot is taken hands its face over,
        plate and border and label included, and the slot answers the op count for
        it. Everything else - every catalog kind, and every script kind nobody
        painted - comes from :meth:`base_face`, so the two cannot disagree about
        what a box or a state or a skin digest meant.

        Children are not this method's business either way, which is what lets a
        ``WIDGET_DEFINE("deck", "panel")`` paint its own plate and still show the
        sliders inside it.
        """
        if self.width <= 0 or self.height <= 0:
            return 0
        if self.on_paint is not None:
            return int(self.on_paint(self, renderer, skin) or 0)
        return self.base_face(renderer, skin)

    def base_face(self, renderer: Renderer, skin: Skin) -> int:
        """The catalog face: plate, border, mark, label. Never its children.

        ``bg + border + glyph + label`` in that order, all four from one
        :class:`~crude.ui.skin.Appearance`, which is the whole point of the skin:
        a new kind inherits the look by naming a role rather than by choosing
        colours, and a theme change reaches it through the table.

        A border is two plates rather than a stroke, because the renderer contract
        has no rounded stroke: the outline colour fills the box and the fill
        colour fills the box inset by the border's width, which is a ring with
        rounded corners on both sides of it.

        Everything the plate cannot say is handed to a
        :data:`~crude.ui.faces.FACES` painter, which owns the inside of the box
        and the word on it - a scope or a keyboard is not a value box with a
        caption, and says so by being in that table rather than by growing a
        branch here. The plate still belongs to :mod:`skin`, so a themed scope
        and a themed button agree about their frames either way.
        """
        if self.width <= 0 or self.height <= 0:
            return 0
        before = renderer.op_count
        found = self.style(skin).resolve(self.role, self.state_name())
        left, top, width, height = self._screen
        if ROLES[found.role].face:
            edge = min(found.width, width * 0.5, height * 0.5)
            if edge > 0:
                renderer.rect(left, top, width, height, found.border,
                              radius=found.radius)
                renderer.rect(left + edge, top + edge,
                              max(0.0, width - 2 * edge),
                              max(0.0, height - 2 * edge), found.fill,
                              radius=found.radius - edge if found.radius else 0.0)
            else:
                renderer.rect(left, top, width, height, found.fill,
                              radius=found.radius)
        face = face_for(self.kind)
        if face is None:
            self.paint_label(renderer, found)
        else:
            face(self, renderer, skin, found)
        return renderer.op_count - before

    def paint_label(self, renderer: Renderer, found: Any) -> int:
        """The mark and the words, measured once and centred as one group.

        A mark and a label are one thing to place: the pair is measured
        (``renderer.measure``, which is the real face's own answer when there is
        one) and the group is centred in the content box, so a button reads as a
        button rather than as an icon glued to the left of a floating word. A mark
        on its own is an icon, and an icon is drawn square in the middle of the box
        at the size the box is, because a glyph rasterized at the font size and
        then stretched to fill a forty-pixel ``ICON`` is a blurry one - which is
        also why its ``size`` is left for the renderer to take from the box. Vertical
        centring uses the line height the text will be drawn at, so a caption and a
        title sit on the same middle line.
        """
        before = renderer.op_count
        left, top, width, height = self.content_box()
        mark = self.glyph_name(found)
        # a box that is drawn to be read off shows what it holds; every other face
        # shows the words it was given, which is the whole of the question the
        # skin's ``shows`` answers and the reason no kind is named here
        which = "value" if ROLES[found.role].shows == "value" else "label"
        text = (self.readout() if ROLES[found.role].shows == "value"
                else self.label)
        size = found.font
        if width <= 0 or height <= 0 or (not mark and not text):
            return 0
        # a word pushed to a side leaves the box to its mark and is drawn outside
        # by the shared helper - the plate-and-label path agrees with the knob and
        # slider faces about what ``label_place='left'`` means, off one function
        place, dx, dy = placement(self, which)
        if text and place in SIDES:
            placed_text(self, renderer, found, text, place, dx, dy, size=size)
            if mark:
                side = min(width, height)
                renderer.glyph(left + (width - side) * 0.5,
                               top + (height - side) * 0.5, side, side, mark,
                               found.ink)
            return renderer.op_count - before
        if place == "none":
            text = ""
        middle = top + max(0.0, (height - size * TEXT_HEIGHT) * 0.5)
        if mark and not text:
            side = min(width, height)
            renderer.glyph(left + (width - side) * 0.5,
                           top + (height - side) * 0.5, side, side, mark,
                           found.ink)
            return renderer.op_count - before
        slot = size * 1.25 if mark else 0.0
        text_w = renderer.measure(text, size)[0]
        start = left + max(0.0, (width - slot - text_w) * 0.5)
        if mark:
            renderer.glyph(start, middle, size, size * TEXT_HEIGHT, mark,
                           found.ink, size=size)
        renderer.text(start + slot + text_w * 0.5, middle, text, size,
                      found.ink, align="center")
        return renderer.op_count - before

    def glyph_name(self, found: Any) -> str:
        """Which icon this widget shows now, or ``""`` for none.

        The props answer before the role does, and the on/off pair answers before
        either, so ``TOGGLE("Run")`` with ``glyph_off play`` and ``glyph_on
        pause`` shows play while it is stopped - the plan's example, and the
        reason ``glyph`` alone is not the whole question.
        """
        state = self._state
        on = bool(self.has_value and state["value"].value)
        on = on or bool(state["selected"].value)
        wanted = state["glyph_on"].value if on else state["glyph_off"].value
        return wanted or state["glyph"].value or (found.glyph or "")

    # -- describing itself -------------------------------------------------
    def as_dict(self, *, props: bool = True) -> Dict[str, Any]:
        """The widget as one document: what it is, where, and what it holds."""
        found: Dict[str, Any] = {
            "path": self.path(),
            "uid": self.uid,
            "kind": self.kind.name,
            "container": self.is_container,
            "box": [round(value, 2) for value in self.box],
            "screen_box": [round(value, 2) for value in self._screen],
            "clip": [round(value, 2) for value in self._clip],
            "z": self.z,
            "children": len(self._children),
            "state": self.state_name(),
            "focusable": self.focusable(),
            "pd_object": self.pd_object or None,
        }
        if props:
            found["props"] = self.values()
        return found

    def tree(self, *, indent: int = 0) -> List[str]:
        """The subtree as lines, z-order bottom up: what a report prints."""
        pad = "  " * indent
        said = f"{pad}{self.path()}"
        if self.width or self.height:
            said += f" {self.width:g}x{self.height:g} @ {self.x:g},{self.y:g}"
        if self._children:
            said += f" ({len(self._children)})"
        out = [said]
        for kid in self._children:
            out.extend(kid.tree(indent=indent + 1))
        return out

    def props_hash(self) -> str:
        """The ``props_hash`` of the plan's paint cache key.

        Every prop, the pointer's four included, because a cached repaint of an
        unhovered widget over a hovered one is a widget that does not answer the
        mouse, and no part of that is the painter's business to remember.
        """
        return digest(self.kind.name, self.values())

    def paint_key(self, skin: Skin) -> tuple:
        """The cache key a script painter's ops are kept under.

        ``(widget, props_hash, size, skin_hash)``, with the size taken as the
        whole resolved box rather than as width and height: a cached painter emits
        absolute coordinates, so a widget that moved without changing its props or
        its shape has to repaint as much as one that resized. The skin's digest is
        the last term, so a theme switch repaints everything exactly once.
        """
        return (self.uid, self.props_hash(),
                tuple(round(value, 2) for value in self._screen), skin.digest)

    def __str__(self) -> str:
        said = f"Widget({self.kind.name}"
        if self.name:
            said += f" '{self.name}'"
        if self.width or self.height:
            said += f" {self.width:g}x{self.height:g} @ {self.x:g},{self.y:g}"
        said += f", {self.state_name()}"
        if self._children:
            said += f", {len(self._children)} child(ren)"
        return said + ")"

    def __repr__(self) -> str:
        return f"<Widget {self.path()} {self.kind.name}>"


class Panel(Widget):
    """A widget that holds others: the window's root, a card, a group of controls.

    The class is a constructor's convenience - ``Panel()`` is a ``panel`` kind with
    a plate - and container-ness is not. Anything whose kind chain reaches
    ``panel``, including a ``WIDGET_DEFINE("deck", "panel")`` a script wrote, adds
    children exactly as this does, and a ``Panel`` built with another container
    kind is still a container because the table says so.
    """

    __slots__ = ()

    def __init__(self, values: Optional[Mapping[str, Any]] = None, *,
                 name: str = "", kind: Any = CONTAINER_KIND,
                 parent: Optional[Widget] = None) -> None:
        super().__init__(kind, values, name=name, parent=parent)


def root_panel(width: float, height: float, *, name: str = "root",
               kind: Any = CONTAINER_KIND) -> Panel:
    """The window's client rect as a widget, which is what the UI root is.

    The plan is exact about this - there is no ``WINDOW()`` call and the root *is*
    the client rect - so the context (S7's ``UIContext``) gets its one root from
    here and re-places it on every resize rather than rebuilding a tree.
    """
    held = Panel({"label": ""}, name=name, kind=kind)
    held.place(0, 0, float(width), float(height))
    return held


def top_at(root: Widget, x: float, y: float) -> Optional[Widget]:
    """The hit-test callback an :class:`~crude.ui.input.InputRouter` wants."""
    return root.hit(x, y)
