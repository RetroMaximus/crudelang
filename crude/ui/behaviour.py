"""What a gesture means: the table of kinds and the value each one moves.

:mod:`crude.ui.widget` keeps the pointer's events straight and deliberately moves
nothing; :mod:`crude.ui.props` says what a kind *can* hold. This module is the
third answer, the one both of those files point here for: what happens when a
person presses, drags, scrolls or types on a thing of a given kind. It is a table
from kind name to event name to callable, and it is the same table a script's
``ON_CLICK`` is written against - which is why a ``bng`` bouncing and a project's
own handler are not two mechanisms.

Everything here is arithmetic against Pd's own boxes, read out of the vendored
0.54 source (``third-party/pure-data/src/g_slider.c``) rather than remembered: a
slider **jumps** to where it was clicked and is then dragged **relatively**
(``delta = 100 * delta / zoom``, and ``-dy`` for the vertical box), and its value
is scaled by ``g = (f - min) / k`` or, when ``log`` is set, by
``g = log(f / min) / k``. Two things follow from that and are the reason the
numbers in :mod:`tests.ui.test_behaviour` are what they are:

* a drag is a *movement*, so it is measured against the value the widget holds at
  the moment, not against where the pointer started - which is also why a slider
  whose press jumped lands its first drag increment on that jump;
* the box is the ruler. A range's whole span is one box width, so a widget that
  has never been laid out has no span to be measured on and says so by moving
  nothing.

One rule is worth stating because two documents in this repository each say half
of it (:meth:`crude.ui.handlers.Handlers.adopt`, and the plan's line that a
script's ``ON_DRAG`` "overrides base behaviour only when defined"): **the
behaviour runs first and the script's handler runs after it, and may write over
it.** That is the only reading that keeps both sentences true. A script that
registers ``ON_CLICK`` on a toggle sees the value the click produced - and can
replace it, which is what "override" means for the final value. A kind with no
entry in :data:`BEHAVIOURS` - every kind a script defines with no base, the
plan's own ``stepper`` among them - has no behaviour to run at all, so its
handlers are the whole story and nothing has to be suppressed to prove it.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional, Tuple

from .geometry import clamp
from .input import SCROLL_NOTCH
from .props import get_kind

__all__ = ["BEHAVIOURS", "behaviour_for", "install_behaviour", "on_event",
           "GESTURE_BUTTON", "PAGE_FACTOR", "span_of", "logarithmic",
           "fraction_of", "value_at", "snap_to_step", "point_fraction",
           "typable"]

#: the only button that moves a value. A right click is a script's to mean
#: something, and a behaviour that guessed at it would be stealing a binding
GESTURE_BUTTON = 0

#: how many of the small move one page is - the wheel over a field, ``PageUp``,
#: and the coarse half of a range's keyboard
PAGE_FACTOR = 10.0

#: the words :func:`~crude.ui.input.key_name` answers for the keys that carry no
#: character of their own. ``" "`` is space: GLFW's printable range falls through
#: to ``chr``, so a space arrives as the character it is and not as a name
_ENTER = ("enter", "keypad enter")
_ESCAPE = ("escape",)
_ERASE = ("backspace", "delete")
_ACTIVATE = (" ", "enter", "keypad enter")
_PAGE_UP, _PAGE_DOWN = ("page up", "page down")
_DIGITS = "0123456789.-"


# --------------------------------------------------------------------------
# the maths a range is measured with
# --------------------------------------------------------------------------

def span_of(widget: Any) -> Tuple[float, float]:
    """The two ends of one widget's range, as the table and the widget agree on them.

    ``value`` bounds itself by two *sibling props* - which is how a slider is
    20..300 without a single number in the table saying so - and a kind that has
    no ``min`` row takes its ends from the ``value`` row's own declared limits.
    A ``tgl`` and a ``bng`` are exactly that second case: the table gave them a
    0..1 and closed the bounds, so nothing here has to know they are switches.
    """
    low, high = 0.0, 1.0
    prop = widget.kind.props.get("value")
    if prop is not None:
        if prop.minimum is not None:
            low = float(prop.minimum)
        if prop.maximum is not None:
            high = float(prop.maximum)
    held = widget.values()
    if "min" in widget.kind.props:
        low = float(held["min"])
    if "max" in widget.kind.props:
        high = float(held["max"])
    return low, high


def logarithmic(widget: Any) -> bool:
    """Whether this range is scaled by ratio, and whether it is allowed to be.

    A log scale is a division, so a range that reaches zero or crosses it has no
    ratio to take. Pd keeps the value in that case and so do we: the ``log`` prop
    is then simply not a fact about this widget's geometry, and the widget still
    drags rather than refusing to be used.
    """
    if not widget.kind.has("log") or not widget.get("log"):
        return False
    low, high = span_of(widget)
    return low > 0.0 and high > low


def fraction_of(widget: Any, value: Any) -> float:
    """Where a value sits along its range, as 0..1.

    The inverse of :func:`value_at`, and unreadable on its own for a log range -
    which is exactly why it exists as a function rather than as a line of
    arithmetic in the drag handler: a drag adds a fraction to the fraction the
    widget already holds, and that only works if both are the same kind of number.
    """
    low, high = span_of(widget)
    if high == low:
        return 0.0
    wanted = float(value)
    if logarithmic(widget):
        if wanted <= 0.0:
            return 0.0
        return math.log(wanted / low) / math.log(high / low)
    return (wanted - low) / (high - low)


def snap_to_step(widget: Any, value: Any) -> float:
    """Bring a number onto the step the widget was given, anchored at its low end.

    Not ``snap``: :func:`crude.ui.geometry.snap` is the device grid and shares
    this package's namespace, and the two round different things - a box onto
    pixels, a value onto the widget's own ladder - too easily mixed up for one
    word to serve both.

    Anchored at ``min`` and not at zero, because a 20..300 slider with a step of
    25 is supposed to stop at 45 and 70. A step of 0 - the default, and the whole
    answer for a kind that never asked for the row - is continuous, and the value
    is only tidied.
    """
    step = float(widget.get("step")) if widget.kind.has("step") else 0.0
    found = float(value)
    if step > 0.0:
        low, _high = span_of(widget)
        found = low + round((found - low) / step) * step
    return _tidy(found)


def value_at(widget: Any, fraction: Any) -> float:
    """The value a point of the box means, snapped and clamped like anything else.

    The fraction is brought inside 0..1 first, so a drag that leaves the widget
    cannot wind the value past the ends the way a hand-held rubber band would:
    the thumb stops at the stop, which is what the box drawn under it claims.

    The two ends are then returned exactly and are *not* put on the step ladder.
    A ladder anchored at ``min`` has no rung at ``max`` unless the step divides
    the span, and a range whose top could not be reached by clicking it - while
    ``End`` reached it - would be two widgets wearing one box.
    """
    low, high = span_of(widget)
    held = clamp(float(fraction), 0.0, 1.0)
    if held <= 0.0:
        return _tidy(low)
    if held >= 1.0:
        return _tidy(high)
    if high == low:
        return low
    if logarithmic(widget):
        return snap_to_step(widget, low * math.exp(math.log(high / low) * held))
    return snap_to_step(widget, low + (high - low) * held)


def point_fraction(widget: Any, x: Any, y: Any,
                   axis: str = "x") -> float:
    """Where along its own box a point is, as the fraction of a range it means.

    Measured in the *content* box, because that is the box the face is drawn in
    and the one the skin's padding left: a click on the border of a slider is the
    person reaching for the end of it, not for a coordinate outside the track.

    A vertical range runs bottom-up - the top of the box is its maximum - which is
    the one thing that makes a ``vslider`` a different widget from an
    ``hslider``, and the one thing here that knows it.
    """
    left, top, width, height = widget.content_box()
    if axis == "y":
        return (top + height - float(y)) / height if height > 0.0 else 0.0
    return (float(x) - left) / width if width > 0.0 else 0.0


def typable(widget: Any) -> bool:
    """Whether a value may be typed into this widget, which its table says.

    Not a list of kinds: the ``editable`` row is the fact, and a kind that never
    asked for the row - a ``meter``, whose hint says no typing, a ``radio`` whose
    value is a choice rather than a quantity - has nothing to check.
    """
    return widget.kind.has("editable") and bool(widget.get("editable"))


def _extent(widget: Any, axis: str) -> float:
    """How long the box is in the direction a movement is measured on."""
    _left, _top, width, height = widget.content_box()
    return float(height if axis == "y" else width)


def _tidy(value: float) -> float:
    """A float at the place a person would write it: ``90.0``, not ``90.000004``.

    Every mapping above is a multiply and a divide, so the tenth place is where
    the binary answer stops being interesting. It matters past the drawing: this
    number goes out through ``ON_CHANGE`` to a ``PD_SEND``, and a value with
    eleven digits of noise behind it is a bug report nobody can read and a patch
    that never settles.
    """
    found = round(float(value), 10)
    return 0.0 if found == 0 else found


def _write(widget: Any, value: Any) -> bool:
    """The one door. A behaviour writes the way a ``SET`` does, and is clamped.

    It is never ``widget.value = ...`` and never the state map: :meth:`set` is
    where ``Prop.settle`` keeps the value inside ``min``/``max``, where
    :meth:`~crude.ui.widget.Widget._sweep` re-settles the rows that read their
    range from it, and where :attr:`changed`, ``ON_CHANGE`` and a ``LINK_VALUE``
    all hear that something moved. A behaviour that wrote the slot directly would
    be a slider whose mirror stops agreeing with it.
    """
    return widget.set("value", value)


def _held(widget: Any) -> Dict[str, Any]:
    """The scratch one gesture in progress needs.

    :func:`install_behaviour` is what puts the dict there, so a behaviour never has
    to create state on a widget nobody built - and never has to check for it either.
    """
    found = widget._behaviour
    if found is None:
        found = {}
        widget._behaviour = found
    return found


def _args(args: Tuple[Any, ...], index: int, default: Any = 0) -> Any:
    """One argument, tolerant of a caller who sent a short list by hand."""
    return args[index] if len(args) > index else default


def _notches(args: Tuple[Any, ...]) -> float:
    """How many wheel notches an event carries, and which way the person pushed it.

    The router has already multiplied GLFW's notch count by
    :data:`~crude.ui.input.SCROLL_NOTCH` to make it look like a pixel movement, so
    the first thing a scroll does is divide it back out. The sign is then the
    toolkit's own and is left alone: ``+dy`` is the wheel pushed away from you,
    which raises a level - the same way every other control in every other
    application reads a wheel, and the reason a scroll gets its own entry in the
    table instead of falling through to the drag one, where the same pixel count
    means "the pointer moved down the screen" and has to go the other way.
    """
    return float(_args(args, 1)) / SCROLL_NOTCH


def _is_gesture(args: Tuple[Any, ...], button: int = 2) -> bool:
    """Whether this pointer event came from the button that means "use it"."""
    return int(_args(args, button, GESTURE_BUTTON)) == GESTURE_BUTTON


# --------------------------------------------------------------------------
# the behaviours, as factories over the table
# --------------------------------------------------------------------------

def _nudge(widget: Any) -> float:
    """How far one arrow key or one wheel notch moves a range.

    The step if the widget has one - which is why a script that wants a coarse
    control sets ``step`` rather than wrapping every key - and a hundredth of the
    span if it does not, so an arrow on a continuous slider still does something
    a person can see and repeat.
    """
    step = float(widget.get("step")) if widget.kind.has("step") else 0.0
    if step > 0.0:
        return step
    low, high = span_of(widget)
    return (high - low) / 100.0 if high != low else 1.0


def _keyed_range(widget: Any, name: str) -> Optional[float]:
    """The value one key means for a range, or ``None`` for a key it does not.

    The catalog's own hint is what promises the keyboard: a slider is "a
    horizontal range; drag, click, or arrow it", so arrows, ``Home``, ``End`` and
    the page keys are part of the widget a script was told it built.
    """
    step = _nudge(widget)
    if name in ("up", "right"):
        return snap_to_step(widget, widget.value + step)
    if name in ("down", "left"):
        return snap_to_step(widget, widget.value - step)
    if name in _PAGE_UP:
        return snap_to_step(widget, widget.value + step * PAGE_FACTOR)
    if name in _PAGE_DOWN:
        return snap_to_step(widget, widget.value - step * PAGE_FACTOR)
    low, high = span_of(widget)
    if name == "home":
        return low
    if name == "end":
        return high
    return None


def _commit(widget: Any) -> bool:
    """Land a typed number and end the edit; whether there was an edit to land.

    ``float()`` of the buffer and nothing else: the value goes through the same
    :func:`_write` a drag goes through, so a typed ``500`` into a 20..300 field
    lands on 300 and reports 300, which is the plan's answer for an overshoot
    rather than a rejection.
    """
    buffer = str(_held(widget).pop("typing", "") or "")
    if not buffer:
        return False
    try:
        wanted = float(buffer)
    except ValueError:
        return True  # a lone "-" was typed, and a lone "-" is not a number to keep
    return _write(widget, wanted)


def _typed(widget: Any, args: Tuple[Any, ...]) -> None:
    """One character of a number being typed in."""
    if not typable(widget):
        return
    said = str(_args(args, 0, ""))
    if not said or said not in _DIGITS:
        return  # a field does not eat a letter, and does not swallow the beep either
    scratch = _held(widget)
    buffer = str(scratch.get("typing", ""))
    if said == "-" and buffer:
        return
    if said == "." and "." in buffer:
        return
    scratch["typing"] = buffer + said


def _keys_range(widget: Any, args: Tuple[Any, ...]) -> None:
    """The keyboard on a range.

    An edit being typed owns Enter - a half-written number commits before the
    arrow it happened to press has anything to say about it.
    """
    name = str(_args(args, 0, ""))
    scratch = _held(widget)
    if name in _ENTER:
        if _commit(widget):
            return
    if name in _ESCAPE:
        scratch.pop("typing", None)
        return
    if name in _ERASE and scratch.get("typing") is not None:
        left = str(scratch["typing"])[:-1]
        if left:
            scratch["typing"] = left
        else:
            scratch.pop("typing")
        return
    moved = _keyed_range(widget, name)
    if moved is not None:
        _write(widget, moved)


def _range(axis: str, sign: float, jump: bool = True
           ) -> Dict[str, Callable[..., Any]]:
    """The gestures of a range: jump, relative drag, wheel, arrows, typing.

    ``axis`` is which pixel a movement is measured on and ``sign`` which way that
    pixel runs, so a ``vslider`` is ``slider`` with ``("y", -1)`` - the same
    props, the same maths, the same behaviour table, read one way round. That is
    the entire difference between the two, which is why the catalog is allowed to
    declare ``vslider`` as a variant of ``slider`` in one line.

    ``jump`` is the one real difference between a slider and a number box. A
    slider puts its thumb under the pointer, because that is what a thumb is for.
    A field must not: the click that is about to type into the box would
    otherwise overwrite the number the person is reaching to correct.
    """

    def pressed(widget: Any, args: Tuple[Any, ...]) -> None:
        if not _is_gesture(args) or _extent(widget, axis) <= 0.0:
            # no box is not a box of zero width: there is no ruler here to read
            return
        _held(widget).pop("typing", None)
        if jump:
            _write(widget, value_at(
                widget, point_fraction(widget, _args(args, 0), _args(args, 1),
                                        axis)))

    def dragged(widget: Any, args: Tuple[Any, ...]) -> None:
        # the router hands a drag a *movement*, never a position: the widget that
        # captured the pointer keeps it after the cursor leaves the box, so the
        # only honest arithmetic left is to move from where the value is now
        length = _extent(widget, axis)
        if length <= 0.0:
            return
        moved = float(_args(args, 1) if axis == "y" else _args(args, 0))
        _write(widget, value_at(
            widget,
            fraction_of(widget, widget.value) + sign * moved / length))

    def scrolled(widget: Any, args: Tuple[Any, ...]) -> None:
        notches = _notches(args)
        if notches:
            _write(widget, snap_to_step(widget, widget.value
                                        + notches * _nudge(widget)))

    return {"on_press": pressed, "on_drag": dragged,
            "on_scroll": scrolled, "on_key": _keys_range,
            "on_char": _typed}


def _flip(widget: Any) -> Any:
    """The one thing a switch does, from whichever door asked for it."""
    low, high = span_of(widget)
    return _write(widget, low if float(widget.value) > low else high)


def _latch() -> Dict[str, Callable[..., Any]]:
    """A ``tgl``: a click, or space or Enter, and it stays where it was left."""

    def clicked(widget: Any, args: Tuple[Any, ...]) -> None:
        if _is_gesture(args):
            _flip(widget)

    def keyed(widget: Any, args: Tuple[Any, ...]) -> None:
        if str(_args(args, 0, "")) in _ACTIVATE:
            _flip(widget)

    return {"on_click": clicked, "on_key": keyed}


def _momentary() -> Dict[str, Callable[..., Any]]:
    """A ``bng``: up on the press, down on the release, and a pulse from the keyboard.

    The press and the release are two changes and therefore two ``ON_CHANGE``
    runs, which is the plan's "pulse 1 to 0" rather than a timed flash: nothing
    here needs a clock, and a frame that wants an animation can read the
    ``pressed`` state the tree already keeps. A key has no release of its own -
    the router sends one event per press - so it does both at once.
    """

    def pressed(widget: Any, args: Tuple[Any, ...]) -> None:
        if _is_gesture(args):
            _write(widget, span_of(widget)[1])

    def released(widget: Any, args: Tuple[Any, ...]) -> None:
        if _is_gesture(args):
            _write(widget, span_of(widget)[0])

    def pulsed(widget: Any, args: Tuple[Any, ...]) -> None:
        if str(_args(args, 0, "")) not in _ACTIVATE:
            return
        low, high = span_of(widget)
        _write(widget, high)
        _write(widget, low)

    return {"on_press": pressed, "on_release": released, "on_key": pulsed}


def _rows(widget: Any, words: int, columns: int) -> int:
    """How many rows a list of choices is laid out in."""
    return -(-words // columns)  # ceiling, with no math import for one division


def _columns(widget: Any) -> int:
    """The column count the widget declares, 0 read as one row per entry.

    ``columns 0`` is Pd's own way of saying "as many rows as you need", and a
    default of 1 cell per row is what makes a plain three-button radio a stack.
    """
    held = int(widget.get("columns")) if widget.kind.has("columns") else 0
    return held if held > 0 else 1


def _cell_at(widget: Any, x: Any, y: Any) -> Optional[float]:
    """Which choice the point is over, in the grid the widget's own rows declare.

    The box is divided by ``columns`` across and by as many rows as the labels
    need: a three-entry radio at ``columns 0`` is three rows of one, and at
    ``columns 2`` it is two cells wide with the third alone on the second row -
    the layout a person sees and the index they get being the same arithmetic.
    """
    words = widget.get("labels") or ()
    if not words:
        return None
    left, top, width, height = widget.content_box()
    columns, points = _columns(widget), len(words)
    rows = _rows(widget, points, columns)
    wanted, across = float(x), float(y)
    if not (left <= wanted < left + width and top <= across < top + height):
        return None
    if width <= 0.0 or height <= 0.0 or rows <= 0:
        return None
    col = int((wanted - left) / (width / columns))
    row = int((across - top) / (height / rows))
    held = min(row * columns + col, points - 1)
    return float(held) if held >= 0 else None


def _index_by(widget: Any, args: Tuple[Any, ...]) -> None:
    """A key or a notch of the wheel, turned into a step through a list of choices.

    Up and down move by a *row* rather than by one, because a radio drawn as a
    grid is a grid to the person using it: two steps left and one up is where
    their eye went, and an index that jumped one cell would not be there.
    """
    name = str(_args(args, 0, ""))
    words = widget.get("labels") or ()
    step = _columns(widget) if name in ("up", "down") else 1
    held = float(widget.value)
    if name in ("right", "down", "page down"):
        _write(widget, held + step)
    elif name in ("left", "up", "page up"):
        _write(widget, held - step)
    elif name == "home":
        _write(widget, 0)
    elif name == "end" and words:
        _write(widget, len(words) - 1)


def _cells() -> Dict[str, Callable[..., Any]]:
    """A ``radio``: the pressed cell is the choice, and the keys move by cell."""

    def pressed(widget: Any, args: Tuple[Any, ...]) -> None:
        if not _is_gesture(args):
            return
        _held(widget).pop("typing", None)
        held = _cell_at(widget, _args(args, 0), _args(args, 1))
        if held is not None:
            _write(widget, held)

    def keyed(widget: Any, args: Tuple[Any, ...]) -> None:
        _index_by(widget, args)

    def scrolled(widget: Any, args: Tuple[Any, ...]) -> None:
        notches = _notches(args)
        if notches:
            _write(widget, float(widget.value) + notches)

    return {"on_press": pressed, "on_key": keyed, "on_scroll": scrolled}


def _cycle(widget: Any, delta: float) -> Any:
    """Move a folded choice by ``delta``, wrapping at both ends.

    Wrapping is what makes a cycle possible with no geometry to be outside of: a
    dropdown's value is an index, and the index has no end a person can reach by
    going the wrong way.
    """
    words = widget.get("labels") or ()
    if not words:
        return None
    count = len(words)
    held = int(round(float(widget.value))) % count
    return _write(widget, (held + int(delta)) % count)


def _folded() -> Dict[str, Callable[..., Any]]:
    """A ``dropdown``: one choice, and every gesture that can mean it without a popup.

    There is no list widget to open yet - a folded menu is renderer work this
    table deliberately does not pretend to have done - so a click advances by one
    and the keys and the wheel go both ways. The router sends a click without its
    modifiers, which is why a click has exactly one direction to go in.
    """

    def keyed(widget: Any, args: Tuple[Any, ...]) -> None:
        name = str(_args(args, 0, ""))
        if name in ("right", "down", "page down"):
            _cycle(widget, 1 if name != "page down" else PAGE_FACTOR)
        elif name in ("left", "up", "page up"):
            _cycle(widget, -1 if name != "page up" else -PAGE_FACTOR)
        elif name == "home":
            _cycle(widget, -int(round(float(widget.value))))
        elif name == "end":
            words = widget.get("labels") or ()
            _cycle(widget, len(words) - 1 - int(round(float(widget.value)))
                   if words else 0)

    def clicked(widget: Any, args: Tuple[Any, ...]) -> None:
        if _is_gesture(args):
            _cycle(widget, 1)

    def scrolled(widget: Any, args: Tuple[Any, ...]) -> None:
        notches = _notches(args)
        if notches:
            _cycle(widget, notches)

    return {"on_click": clicked, "on_key": keyed, "on_scroll": scrolled}


# --------------------------------------------------------------------------
# the synth widgets: gestures against a picture, not a thumb
# --------------------------------------------------------------------------

def _param(widget: Any, name: str, value: Any) -> bool:
    """Write one named prop the way :func:`_write` writes the value.

    Not the slot directly, for the same reasons: ``Prop.settle`` keeps
    ``sustain`` inside 0..1 and ``tension`` inside -1..1, and :meth:`set` is what
    rings :attr:`changed`, ``ON_CHANGE`` and any ``LINK_VALUE`` so a script hears
    the drag while it happens. A graph widget's stage - which row a drag means -
    is still kept in ``value``, so an ``adsr`` still has one number for a preset
    to save and a patch to read; the rows it moves are named here rather than
    reached through the state map.
    """
    return widget.set(name, value)


def _row_span(widget: Any, name: str) -> Tuple[float, float]:
    """The two ends of a *named* row, which is not what :func:`span_of` reads.

    :func:`span_of` is built for a ``value`` prop bounded by sibling ``min``/``max``
    rows, and a synth widget keeps its quantity somewhere else: a ``curve`` holds
    ``tension`` (bounded -1..1 by its own row) and has no ``value`` at all, so
    asking ``span_of`` for its ends answers the table's default of 0..1 and a press
    could never reach a negative bend. This reads the row that actually owns the
    range, and a row that declared no limits falls back to 0..1 like anything else.
    """
    prop = widget.kind.props.get(name)
    low = (float(prop.minimum) if prop is not None and prop.minimum is not None
           else 0.0)
    high = (float(prop.maximum) if prop is not None and prop.maximum is not None
            else 1.0)
    return low, high


def _row_at(widget: Any, name: str, fraction: Any) -> float:
    """The value one of a named row's own ends a fraction between them means.

    The ``value``-row :func:`value_at` with :func:`_row_span` in place of
    :func:`span_of`: a fraction is clamped inside the box first, so a drag that
    leaves the widget stops at the end of the range rather than winding past it.
    The two ends are returned exactly, which is the same promise :func:`value_at`
    makes - a top you can click is a top you can name.
    """
    low, high = _row_span(widget, name)
    held = clamp(float(fraction), 0.0, 1.0)
    if held <= 0.0:
        return _tidy(low)
    if held >= 1.0:
        return _tidy(high)
    if high == low:
        return low
    return _tidy(low + (high - low) * held)


def _row_fraction(widget: Any, name: str, value: Any) -> float:
    """Where a named row's value sits along its own ends, as 0..1."""
    low, high = _row_span(widget, name)
    if high == low:
        return 0.0
    return (float(value) - low) / (high - low)


def _updown(widget: Any, x: Any, y: Any) -> float:
    """How high a point is over a widget's own box, 0 at the floor, 1 at the ceiling.

    :func:`point_fraction` on the ``y`` axis, guarded the way every other
    measurement here is: a widget that has never been laid out has no ruler, and
    the honest answer for a zero-height box is that the click meant nothing.
    """
    _left, _top, _width, height = widget.content_box()
    if height <= 0.0:
        return -1.0
    return clamp(point_fraction(widget, x, y, "y"), 0.0, 1.0)


#: the four stages of an envelope, in the order the graph draws them and the
#  order the value indexes them: 0 attack through 3 release
_STAGES = ("attack", "decay", "sustain", "release")

#: which semitones of an octave are the black keys - the same fact
#  ``faces._draw_keybed`` draws with, spelled once here rather than imported
#  from a drawing module, because which key a click lands on is not a picture
_KEY_BLACK = (1, 3, 6, 8, 10)


def _envelope() -> Dict[str, Callable[..., Any]]:
    """An ``adsr``: click a stage to pick it, drag vertically to move it.

    The press does both at once - the quarter of the box says which stage, the
    height says how much of it - because that is how every hardware envelope
    editor works and because the two answers are the same gesture read on two
    axes. The drag then belongs to the held stage alone and is relative, like
    every other drag here: the press landed on a number, and the pointer that
    leaves the box must not fall off a cliff the width of the widget.

    The quarters are the box's, not the graph's segments: with all four times at
    zero - the default widget, before a patch has said anything - the segments
    collapse to a line and no stage is clickable at all. Four equal quarters
    always answer, and the dot the face draws says which one is held.
    """

    def pressed(widget: Any, args: Tuple[Any, ...]) -> None:
        if not _is_gesture(args):
            return
        left, _top, width, _height = widget.content_box()
        if width <= 0.0:
            return
        wanted = _args(args, 0)
        if not (left <= float(wanted) < left + width):
            return  # outside the plate is not a stage of this envelope
        stage = int(clamp((float(wanted) - left) / width * 4.0, 0.0, 3.0))
        _write(widget, float(stage))
        level = _updown(widget, wanted, _args(args, 1))
        if level >= 0.0:
            _param(widget, _STAGES[stage], level)

    def dragged(widget: Any, args: Tuple[Any, ...]) -> None:
        if _extent(widget, "y") <= 0.0:
            return
        stage = int(clamp(float(widget.value), 0.0, 3.0))
        held = float(widget.get(_STAGES[stage]))
        # a movement up the screen is negative dy and raises the stage, the way
        # it raises a vertical slider: one convention for every drag in the kit
        _param(widget, _STAGES[stage], held - float(_args(args, 1))
               / _extent(widget, "y"))

    def keyed(widget: Any, args: Tuple[Any, ...]) -> None:
        name = str(_args(args, 0, ""))
        held = float(widget.value)
        if name in ("right", "down"):
            _write(widget, clamp(held + 1.0, 0.0, 3.0))
        elif name in ("left", "up"):
            _write(widget, clamp(held - 1.0, 0.0, 3.0))
        elif name == "home":
            _write(widget, 0.0)
        elif name == "end":
            _write(widget, 3.0)

    return {"on_press": pressed, "on_drag": dragged, "on_key": keyed}


def _bend() -> Dict[str, Callable[..., Any]]:
    """A ``curve``: the vertical position is the tension, absolutely.

    The same range maths as a slider, but read off the *row* the widget owns -
    :func:`_row_at` measures ``tension`` between its own -1 and 1, where
    :func:`value_at` would ask a ``value`` row this kind never declared - and
    writing ``tension`` rather than ``value``, because nothing here holds a value.
    A jump on press is right here the way it is right on a slider: the person is
    reaching for the shape they can see, and the face redraws the bend from the
    same number the pointer wrote.
    """

    def pressed(widget: Any, args: Tuple[Any, ...]) -> None:
        if not _is_gesture(args) or _extent(widget, "y") <= 0.0:
            return
        held = point_fraction(widget, _args(args, 0), _args(args, 1), "y")
        _param(widget, "tension", _row_at(widget, "tension", held))

    def dragged(widget: Any, args: Tuple[Any, ...]) -> None:
        length = _extent(widget, "y")
        if length <= 0.0:
            return
        held = _row_fraction(widget, "tension", float(widget.get("tension")))
        _param(widget, "tension", _row_at(
            widget, "tension", held - float(_args(args, 1)) / length))

    return {"on_press": pressed, "on_drag": dragged}


def _pad() -> Dict[str, Callable[..., Any]]:
    """An ``xy`` pad: both axes at once, absolute on press, relative on drag.

    Two independent ranges sharing a box, so there is nothing to compose and no
    axis to pick: x reads off the width and y off the height, upside down
    because 0 is the *bottom* of the pad - which is also how the face draws it,
    and the reason both live in the same fraction helpers rather than a second
    copy of the flip.
    """

    def _place(widget: Any, x: Any, y: Any) -> None:
        across = point_fraction(widget, x, y, "x")
        up = point_fraction(widget, x, y, "y")
        _param(widget, "x", clamp(across, 0.0, 1.0))
        _param(widget, "y", clamp(up, 0.0, 1.0))

    def pressed(widget: Any, args: Tuple[Any, ...]) -> None:
        if not _is_gesture(args):
            return
        left, top, width, height = widget.content_box()
        wanted = float(_args(args, 0))
        down = float(_args(args, 1))
        if not (left <= wanted < left + width and top <= down < top + height):
            return  # the router only presses inside, but a hand-built event may not
        _place(widget, wanted, down)

    def dragged(widget: Any, args: Tuple[Any, ...]) -> None:
        width, height = _extent(widget, "x"), _extent(widget, "y")
        if width <= 0.0 or height <= 0.0:
            return
        held_x = float(widget.get("x")) + float(_args(args, 0)) / width
        held_y = float(widget.get("y")) - float(_args(args, 1)) / height
        _param(widget, "x", clamp(held_x, 0.0, 1.0))
        _param(widget, "y", clamp(held_y, 0.0, 1.0))

    return {"on_press": pressed, "on_drag": dragged}


def _keyboard() -> Dict[str, Callable[..., Any]]:
    """A ``keybed``: press the pitch, release the note.

    Which key the point is over is the *face's* arithmetic - naturals take the
    width and the accidentals sit on boundaries between them - so this walks the
    same list of pitches ``_draw_keybed`` draws and counts naturals, which is
    the only way a click and a picture cannot drift apart.

    The value is the held pitch and 0 is no key, so a press on the bottom-most
    key of a bed starting at MIDI 0 writes 0 and means it: the prop's 0..127
    range says a value is a pitch, and the script that plays the note listens
    for the change and calls ``PD_MIDI_NOTEON`` - a note-off being Pd's own
    note-on at velocity 0, which is why release is not special-cased here.
    """

    def _pitch(widget: Any, wanted: float) -> Optional[int]:
        left, _top, width, _height = widget.content_box()
        if width <= 0.0:
            return None
        low = int(clamp(float(widget.get("low") or 0.0), 0.0, 127.0))
        octaves = max(1, int(round(float(widget.get("octaves") or 1.0))))
        count = min(octaves * 12, max(127 - low + 1, 1))
        naturals = [pitch for pitch in range(low, low + count)
                    if (pitch % 12) not in _KEY_BLACK]
        if not naturals:
            return None
        index = int(clamp((wanted - left) / width * len(naturals),
                          0.0, len(naturals) - 1.0))
        return naturals[index]

    def pressed(widget: Any, args: Tuple[Any, ...]) -> None:
        if not _is_gesture(args):
            return
        held = _pitch(widget, float(_args(args, 0)))
        if held is not None:
            _write(widget, float(held))

    def released(widget: Any, args: Tuple[Any, ...]) -> None:
        if _is_gesture(args):
            _write(widget, 0.0)

    return {"on_press": pressed, "on_release": released}


# --------------------------------------------------------------------------
# the table
# --------------------------------------------------------------------------

#: every kind that answers a gesture, and what it answers each one with.
#:
#: A kind that is not here is not broken: a ``panel``, a ``text``, an ``icon`` and
#: a ``meter`` hold still, which is the plan's own description of them ("a level
#: read out, not set: no typing, no dragging"), and a kind a script defines with
#: no base has no entry because it has no behaviour to inherit until it says so
#: with ``ON_CLICK``.
BEHAVIOURS: Dict[str, Dict[str, Callable[..., Any]]] = {
    "slider": _range("x", 1.0),
    "vslider": _range("y", -1.0),
    # a knob drags like a vslider and measures like one, but never jumps: the
    # pointer of a dial has no position on it to teleport to, so the press that
    # lands must leave the value exactly where it was - jump=False, the same
    # answer a number box gives for a different reason
    "knob": _range("y", -1.0, jump=False),
    "number": _range("x", 1.0, jump=False),
    "toggle": _latch(),
    "button": _momentary(),
    "radio": _cells(),
    "dropdown": _folded(),
    # the synth widgets. waveform and spectrum are deliberately absent: they
    # show what a script pushes into ``data`` and hold no value to move, so a
    # gesture on a scope means nothing until a script says otherwise with an
    # ON_CLICK. preset_bar gets its own row rather than relying on the chain:
    # the table is the list of what answers, and an inherited "it's a radio"
    # is a lookup answer, not a declaration - and a declaration is what the
    # parity test reads.
    "adsr": _envelope(),
    "curve": _bend(),
    "xy": _pad(),
    "keybed": _keyboard(),
    "preset_bar": _cells(),
}


def behaviour_for(kind: Any) -> Dict[str, Callable[..., Any]]:
    """The gestures one kind answers, found by walking its inheritance chain.

    The chain and nothing else: a ``WIDGET_DEFINE("bpm_slider", "slider")`` gets
    the slider's behaviour the same way it gets the slider's props and its
    ``hslider`` box, which is the plan's "inherits input handling" statement made
    true without a second table to keep in step with the first.
    """
    owner = get_kind(kind)
    for name in owner.chain():
        held = BEHAVIOURS.get(name)
        if held is not None:
            return held
    return {}


def install_behaviour(widget: Any) -> Optional[Callable[..., Any]]:
    """Give a widget its kind's own behaviour; the callable that was put in the slot.

    :class:`~crude.ui.widget.Widget` calls this once, from its constructor, which
    is why a bare widget with no bindings and no :class:`~crude.ui.handlers
    .Handlers` anywhere still moves its value when it is dragged - and why the
    numeric tests are arithmetic on a widget rather than on a running program.
    ``None`` is returned for a kind with nothing to do, leaving the slot exactly as
    it was found.
    """
    if not behaviour_for(widget.kind):
        return None
    if widget._behaviour is None:
        widget._behaviour = {}
    widget.on_event = on_event
    return on_event


def on_event(widget: Any, name: Any, *args: Any) -> None:
    """What the tree's ``on_*`` calls reach: this kind's answer for that event.

    The signature is the slot's - ``(widget, name, *args)``, as
    :meth:`~crude.ui.widget.Widget._fire` sends it and as
    :meth:`~crude.ui.handlers.Handlers.adopt` chains it - so a behaviour is
    indistinguishable from any other thing that lives in the slot.

    A widget that is switched off or hidden moves nothing, whatever the table
    says: the router's hit test would never have reached it, and a behaviour
    reached by hand around a ``SET`` is not a second route into a control the
    person is not looking at. Losing the keyboard discards a half-typed number -
    a blur is a person leaving the field, not a person pressing Enter.
    """
    said = str(name or "")
    if said == "on_blur":
        _held(widget).pop("typing", None)
    doing = behaviour_for(widget.kind).get(said)
    if doing is None or not (widget.visible and widget.enabled):
        return
    doing(widget, args)
