"""A viewport as a widget: what is inside it, how far it can slide, where the thumb is.

A scroll area is the one container in :mod:`crude.ui` whose children are not really
its own: they belong to a panel *inside* it, and the panel moves while the frame
stays. Until now that arrangement was private to the settings dialog, which kept its
own viewport, its own offset and its own travel and recomputed all three whenever a
device list changed - so a dialog could be scrollable and nothing else could be, and
a group or a workspace page that wanted the same answer had to write the arithmetic
again. This module is that arithmetic once.

Three rules carry it:

* **The prop is the fact, the box is its shadow.** ``offset`` is how far the content
  has been slid, and it is the only number a caller writes. The inner panel's ``y``
  is derived from it here, so ``SET(s, "offset", 0)`` scrolls to the top exactly as
  the wheel does, and a layout pass that re-arranges the content cannot lose where
  the person had scrolled to - the answer is read back off the prop every frame.
* **Nothing here is measured until it is asked for.** ``content`` and ``travel`` are
  read out of the content's own children - :func:`measure` walks them - and copied
  into the widget's two read-only rows by :func:`reconcile`, which the layout pass
  calls *after* geometry has settled. A painter never writes a prop, and a script
  never writes these two; the table refuses both, and this module is the machine that
  answers them, the same one that answers ``hovered`` to the pointer.
* **The view is the viewport's content box, not its whole box.** A panel that wants
  its rows inset from its own border says so with ``PAD``; the inset stops being this
  module's business, and travel comes out the same as the arithmetic it replaced
  because the dialog now pads its viewport by the margin it used to subtract.

:mod:`crude.ui.layout` is not imported here, and must not be: layout resolves
widgets, widgets import this module's owner, and a cycle would be a knot rather than
a nuisance. The two verbs a viewport needs are imported where they run.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from .geometry import Rect

__all__ = ["BAR_WIDTH", "VIEWPORT_KIND", "MIN_THUMB", "content_of", "rows_in",
           "view_of",
           "view_width", "measure", "travel_of", "content_height", "slide",
           "scroll_by", "page", "top", "bottom", "thumb_rect", "fit_content",
           "reconcile", "restack"]

#: how wide the thumb is drawn, in logical pixels; a scroll bar is read, not aimed
#  at with precision, and a fifth of a device list's row is wide enough to see
BAR_WIDTH = 6.0

#: the kind whose content panel this module slides. Named here rather than imported
#  from :mod:`crude.ui.widget`, which imports this module.
VIEWPORT_KIND = "scroll"

#: the shortest thumb there is. A viewport showing a tenth of its content would
#  otherwise be given a two-pixel mark that cannot be grabbed at all, and a bar
#  nobody can hit is decoration rather than a control.
MIN_THUMB = 18.0


def content_of(scroll: Any) -> Optional[Any]:
    """The inner panel a viewport slides, or ``None`` if this is not one.

    Asked of a widget that is not a scroll area it answers ``None`` rather than
    raising, because :func:`reconcile` walks a whole tree and only some of it is
    made of viewports. The kind is asked first and the child second for that
    reason: ``content_child`` builds the frame it is asked about, so reading it off
    a group - which has a content child of its own, a body, that is not slid and has
    no ``offset`` to be slid by - would both construct a widget's panels from a
    module that has no business doing it and hand back a panel this module's
    arithmetic cannot answer for.
    """
    kind = getattr(scroll, "kind", None)
    if kind is None or VIEWPORT_KIND not in kind.chain():
        return None
    held = getattr(scroll, "content_child", None)
    if held is None or held is scroll:
        return None
    return held


def rows_in(scroll: Any) -> List[Any]:
    """The widgets a viewport is showing, in the order they are placed."""
    inner = content_of(scroll)
    return list(inner.children) if inner is not None else []


def view_of(scroll: Any) -> float:
    """How much of the content can be seen at once: the content box's height.

    The *resolved* box, because that is the box children are clipped to; a viewport
    that has never been laid out has no inset and its view is its own height, which
    is the same answer a ``PLACE`` alone would have produced.
    """
    return float(scroll.content_box()[3])


def view_width(scroll: Any) -> float:
    """How wide the content may be: the content box's width, inset by any border.

    The other half of the same box :func:`view_of` measures, and the reason the
    content panel is as wide as the room the viewport gives it rather than as wide
    as the frame: rows laid out edge to edge would otherwise put their text under
    the thumb.
    """
    return float(scroll.content_box()[2])


def content_height(scroll: Any) -> float:
    """How tall the content is: the bottom of the last thing inside it.

    Measured off the children rather than off the inner panel's own ``height``,
    because nobody has placed that yet at the point this is first asked - and
    because a caller that stacks its rows is free to leave the box at zero and let
    the rows say how much room they need.
    """
    inner = content_of(scroll)
    if inner is None:
        return 0.0
    bottom = 0.0
    for kid in inner.children:
        bottom = max(bottom, float(kid.y) + float(kid.height))
    return max(0.0, bottom)


def measure(scroll: Any) -> Tuple[float, float]:
    """``(content, travel)`` right now: what is there, and how far it can slide.

    Pure - it writes nothing - so a face, a gesture and a test can all ask the same
    question without deciding what the answer should be first.
    """
    content = content_height(scroll)
    return content, max(0.0, content - view_of(scroll))


def travel_of(scroll: Any) -> float:
    """How far the content still has to go; ``0.0`` when it all fits."""
    return measure(scroll)[1]


def slide(scroll: Any, offset: Any) -> float:
    """Slide the content to ``offset`` pixels up, clamped; the offset it landed at.

    The clamp is the whole of the scroll area's discipline: never past the top,
    never past the bottom, and never a positive number, so a widget that has been
    given more room than its content needs simply stays put. Writing the prop first
    and the box second is what keeps the two in step - a caller that only ever reads
    ``GET(s, "offset")`` gets the number the screen is drawn with.
    """
    travel = travel_of(scroll)
    wanted = min(0.0, max(-travel, float(offset or 0.0)))
    scroll.set("offset", wanted)
    # the box and the two read-only rows follow the prop, in one place, so a scroll
    # that a hand never touched is measured exactly like one that was
    reconcile(scroll)
    return wanted


def scroll_by(scroll: Any, notches: Any) -> float:
    """Move by ``notches`` wheel steps of the widget's own ``notch``; the new offset.

    The sign is the wheel's: a downward notch slides the content down the window,
    which reveals what is below, so it *adds* to a negative offset's magnitude in
    the direction the person is looking.
    """
    notch = float(scroll.get("notch") or 0.0)
    return slide(scroll, float(scroll.get("offset") or 0.0)
                 + float(notches) * notch)


def page(scroll: Any, direction: Any = 1) -> float:
    """Move by a screenful; ``-1`` towards the top, ``1`` towards the bottom.

    A page is the view, not the view minus a row: a person paging through a device
    list wants the row they were reading to become the row at the top, and one that
    overlaps by a line is a list that never lands where it was looked at.

    Going down the content means the window's edge moves down it, which is a
    *smaller* offset - so the view is subtracted for ``1`` and added for ``-1``, and
    not the other way round the arithmetic reads at a glance. Both call sites (the
    page keys and a click on the empty track) are written against this docstring.
    """
    bound = view_of(scroll) * (1.0 if int(direction) >= 0 else -1.0)
    return slide(scroll, float(scroll.get("offset") or 0.0) - bound)


def top(scroll: Any) -> float:
    """Scroll to the first row: ``offset`` 0."""
    return slide(scroll, 0.0)


def bottom(scroll: Any) -> float:
    """Scroll to the last row: the whole of the travel."""
    return slide(scroll, -travel_of(scroll))


def thumb_rect(scroll: Any) -> Optional[Rect]:
    """The bar's box in the viewport's own coordinates, or ``None`` if nothing to show.

    ``None`` is an honest answer rather than a convenience: content that fits has
    nothing to scroll, and a thumb that would fill the whole track says so better by
    not being drawn. The rect is relative to the viewport's content box, which is
    where its children live, so a face can draw it straight and a gesture can test a
    press against it without either of them knowing where the window is.
    """
    content, travel = measure(scroll)
    if travel <= 0.0 or content <= 0.0:
        return None
    left, top_edge, width, height = scroll.content_box()
    track = height
    share = max(MIN_THUMB, min(track, track * (track / content)))
    room = max(0.0, track - share)
    offset = float(scroll.get("offset") or 0.0)
    at = (abs(offset) / travel) * room if travel > 0.0 else 0.0
    across = max(0.0, width - BAR_WIDTH - 1.0)
    return left + across, top_edge + at, BAR_WIDTH, share


#: how far the view's width has to change before the pre-pass writes the content
#  panel again: below this the two numbers are the same answer, and an idle frame
#  must report that nothing moved
_SETTLED = 1e-6


def fit_content(scroll: Any) -> int:
    """Give the content panel this frame's view width, before its rows are arranged.

    The half of a settle that has to happen first. The panel's width is a question
    about the viewport alone - the width of its content box - while its height is the
    bottom of the last row and so cannot be known until the rows have been arranged
    inside it. Leaving the width to be written after the arrangement as well lets a
    row packed ``fill`` be sized against the window the *last* frame had, so one
    resize costs two passes before the page is right: the first lays the rows out at
    the old width, the second notices the content panel is now a different one.

    Answers whether a box was written, because a layout pass counts what it moved.
    """
    inner = content_of(scroll)
    if inner is None:
        return 0
    width = max(0.0, view_width(scroll))
    if abs(float(inner.width) - width) <= _SETTLED:
        return 0
    return 1 if inner.place(None, None, width, None) else 0


def reconcile(scroll: Any) -> Tuple[float, float]:
    """Settle one viewport: measure it, place its content, answer its two rows.

    This is the whole of a scroll area's geometry in one call, from both directions:
    the layout pass runs it after it has arranged the tree, and a gesture runs it
    after it has written the offset, so a list scrolled in a paper test is clipped
    exactly like one scrolled by a wheel. The content panel's height is the content -
    which is what makes the clip the viewport owes its children the band that is
    actually on screen, and why a row scrolled past the bottom is neither painted nor
    clickable rather than being painted and refusing to answer.
    """
    inner = content_of(scroll)
    if inner is None:
        return 0.0, 0.0
    content, travel = measure(scroll)
    # the width travels with the view: the content panel is glued to the viewport's
    # own edges by the layout pass, and a settle that left it at whatever width it
    # was built at would keep a resized window's rows as narrow as the old one
    inner.place(None, None, max(0.0, view_width(scroll)), content)
    scroll._measure("content", content)
    scroll._measure("travel", travel)
    wanted = float(scroll.get("offset") or 0.0)
    held = min(0.0, max(-travel, wanted))
    if held != wanted:
        scroll.set("offset", held)
    # the box moves whether or not the prop did: a content panel that has just been
    # built has never been resolved at the offset the script asked for, and its
    # children are clipped against wherever it stands
    inner.place(None, held, None, None)
    scroll.remeasure()
    return content, travel


def restack(root: Any) -> int:
    """Every viewport under ``root``, reconciled; how many there were.

    Named for what the layout pass is doing rather than for this module: a caller
    that has just resolved a tree wants the whole tree settled, and a scroll area
    measured before its rows were arranged would report a content of nothing.

    :func:`content_of` is the only question asked, so the kind test that decides
    whether a widget is a viewport lives in one place and this walk cannot settle a
    panel that is not one.
    """
    held = 0
    for widget in root.walk():
        if content_of(widget) is not None:
            reconcile(widget)
            held += 1
    return held


#: ``restack`` is the name of the walk; ``reconcile`` is the name of one viewport.
#: The layout pass wants the first and a caller with a widget in hand wants the
#: second, and the two are the same two doors, not two answers.
