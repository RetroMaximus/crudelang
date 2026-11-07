"""A group: a header, a body, an order, and a cell it can be dropped into.

A panel holds widgets. A group holds a *section*: something with a name on it that
can be folded away, moved up the list, and put into the cell the pointer was over.
None of that is a new kind of box - it is a panel with two panels inside it, built
by the tree rather than by the script, which is why :mod:`crude.ui.widget` routes a
group's children into its body exactly as it routes a viewport's into its rows and
why everything below is arithmetic on boxes rather than a new arrangement.

Three rules hold the module together:

* **The header is invisible on purpose.** ``#header`` is a real panel with
  ``visible`` off and a role that has no face, because the group's own face paints
  the plate, the title and the mark - and :meth:`~crude.ui.widget.Widget.hit` skips
  an invisible child, so a press in the header band is claimed by the *group*, the
  one widget with a behaviour for it. That is why there is no event forwarding
  anywhere in this file: the panel exists so the fold has a place on the screen to
  be measured against, not so it can be clicked on its own account.
* **A fold is a measurement, not a memory.** A group is as tall as its header plus
  what its body holds - the bottom of the rows inside it, which is how
  :mod:`crude.ui.scroll` measures a viewport - so folding takes the body's room away
  and opening gives it back without any module remembering the number it overwrote.
  A group whose height belongs to its container (packed ``fill`` or ``grow``, or
  glued on ``y``) keeps that height when it is folded: the body is hidden and
  collapsed, the cell is not. Both answers are the same rule, "the box is sized by
  whoever owns the axis", said twice.
* **One cell formula.** :func:`cells_of` is what the layout pass uses to decide
  which cell a child is arranged into *and* what a drop uses to decide which cell
  is free. Two copies of that arithmetic is how a group lands in one cell and is
  drawn in another, so there is one, and it honours a cell a group was docked into
  rather than counting the list.

:mod:`crude.ui.layout` and :mod:`crude.ui.widget` are not imported here and must not
be: this module is imported *by* them (the tree names its panels from these
constants, the pass settles a fold from these functions), and a cycle between a box
and the thing that arranges it is a knot rather than a nuisance. The one thing taken
from outside is :mod:`crude.ui.grid` - a cell is the grid module's arithmetic,
exactly as the layout pass reads it.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from .base import UIError
from .geometry import Rect
from .grid import GridSpec, spec_of

__all__ = ["GroupError", "GROUP_KIND", "GROUP_HEADER_SUFFIX",
           "GROUP_BODY_SUFFIX", "HEADER_HEIGHT", "CHEVRON_PAD", "TITLE_PAD",
           "Y_EDGES", "is_group", "held_by", "header_of", "body_of",
           "header_box", "body_box", "chevron_box", "on_mark", "on_header",
           "title_of", "folded_of", "foldable_of", "fold", "fit_group",
           "groups_of", "order_of", "move_to", "move_by", "assigned",
           "cells_of", "assign", "occupant", "cells_offered", "dock", "undock",
           "dock_target", "drop", "measure"]

#: the kind that is a header and a body. The name belongs to the prop table, which
#  declares ``group`` as a variant of ``panel``; it is written here because this
#  module is the one that answers questions about that kind and cannot import the
#  tree it hangs off. A test in ``tests/ui/test_groups.py`` pins it against
#  :data:`crude.ui.widget.GROUP_KIND`, the way the grid rows are pinned.
GROUP_KIND = "group"

#: the two panels a group is built out of, named the way a viewport's rows are so
#  ``find("carrier#body")`` answers. Owned here and imported by
#  :mod:`crude.ui.widget`, which is the only thing that builds them.
GROUP_HEADER_SUFFIX = "#header"
GROUP_BODY_SUFFIX = "#body"

#: how tall the header band is. A caption line plus room to put a finger next to it:
#  smaller and the mark is a dot nobody can aim at, larger and a folded group is a
#  bar that has started to look like the thing it is holding.
HEADER_HEIGHT = 24.0

#: the mark's square is this much inside the band on every side, so the fold is a
#  target a hand can miss rather than a pixel it has to hit.
CHEVRON_PAD = 6.0

#: how far the title starts inside the band's left edge.
TITLE_PAD = 8.0

#: the edges that own the vertical axis. :mod:`crude.ui.layout` keeps the real
#  table (``AXIS``); this is the same fact read by a group that needs to know
#  whether its height is its own without importing the pass that would import it.
#  A test pins the two against each other.
Y_EDGES = ("top", "bottom", "center-y")

#: the chevron for an open group and the one for a folded one. Both are in the
#  atlas; the pair is what makes ``folded`` readable at a glance rather than a
#  word a script has to ``GET`` to find out.
MARK_OPEN = "chevron-d"
MARK_FOLDED = "chevron-r"

#: how far a box has to disagree before writing it again: below this the two
#  numbers are the same answer, and an idle frame must report that nothing moved.
#: The same promise :mod:`crude.ui.scroll` makes about a viewport's width.
_SETTLED = 1e-6


class GroupError(UIError):
    """The box a group was asked to move into does not exist, or is not free."""


# --------------------------------------------------------------------------
# what a widget is, and what it holds
# --------------------------------------------------------------------------


def is_group(held: Any) -> bool:
    """Whether this widget is a group, or a script kind defined on top of one.

    The chain and not the name, because the structure is inherited the way the
    props are: ``WIDGET_DEFINE("section", "group")`` gets a header, a body and the
    fold, and a question answered by name alone would say no to the one widget the
    script actually built.
    """
    kind = getattr(held, "kind", None)
    return kind is not None and GROUP_KIND in kind.chain()


def _structural(held: Any, suffix: str) -> Optional[Any]:
    """The already-built panel a group was constructed with, or ``None``.

    Never builds one: a face and a gesture are allowed to ask about a widget that
    has not been laid out yet, and an answer must not be paid for with a side
    effect at the middle of a draw.
    """
    if not is_group(held):
        return None
    for kid in getattr(held, "children", ()):
        if kid.name.endswith(suffix) and "panel" in kid.kind.chain():
            return kid
    return None


def header_of(group: Any) -> Optional[Any]:
    """The band's panel, if this group's frame has been built."""
    return _structural(group, GROUP_HEADER_SUFFIX)


def body_of(group: Any) -> Optional[Any]:
    """The panel a group's children live in, if the frame has been built."""
    return _structural(group, GROUP_BODY_SUFFIX)


def held_by(panel: Any) -> List[Any]:
    """The children a panel holds, looking through the frame it was built with.

    A plain panel's children are the ones a script added; a group's and a
    viewport's are inside the panel :attr:`~crude.ui.widget.Widget.content_child`
    answers, because ``add`` handed them there. Asking the question the other way
    round - the raw list - would tell a caller that a group holding three knobs
    holds two panels and nothing else, so :func:`groups_of` and every reorder in
    this module are written against this one.
    """
    inner = getattr(panel, "content_child", None)
    if inner is not None and inner is not panel:
        return list(inner.children)
    return list(getattr(panel, "children", ()))


def groups_of(panel: Any) -> List[Any]:
    """The groups this panel holds, in the order they are drawn."""
    return [kid for kid in held_by(panel) if is_group(kid)]


# --------------------------------------------------------------------------
# the three boxes a group is measured by
# --------------------------------------------------------------------------


def header_box(group: Any) -> Rect:
    """The band the header is drawn in, in screen coordinates.

    Read off the header panel once the pass has given it a box, and off the group's
    own content box before then - so a face, a press and a paper test all answer
    one number for one widget, and a group that has never been laid out still has a
    header to click on. The panel is the truth whenever it exists because it is
    glued to the group's edges by the same pass the face is drawn after; the
    fallback is only ever a box that has not been arranged yet.
    """
    head = header_of(group)
    if head is not None and head.height > 0.0:
        return head.screen_box
    left, top, width, height = group.content_box()
    return (left, top, max(0.0, width),
            min(HEADER_HEIGHT, max(0.0, height)))


def body_box(group: Any) -> Rect:
    """What is left under the header; a band of nothing while the group is folded.

    The body is glued to the header's bottom and to the group's own bottom, so this
    is the same rectangle the pass produced rather than a second arithmetic: a
    folded group is a header-tall group, and two edges of a header-tall group have
    nothing between them.
    """
    body = body_of(group)
    if body is not None:
        return body.screen_box
    left, top, width, height = header_box(group)
    there, floor, wide, high = group.content_box()
    return (there, top + height, max(0.0, wide),
            max(0.0, floor + high - (top + height)))


def chevron_box(group: Any) -> Rect:
    """The mark's square at the far end of the band - the fold's whole target.

    One function for the face that draws it and the gesture that tests a press
    against it, the way :func:`~crude.ui.scroll.thumb_rect` is one answer for a bar
    that is drawn and grabbed: the mark a hand can hit is exactly the mark the eye
    was shown, and the two cannot drift apart between builds.
    """
    left, top, width, height = header_box(group)
    side = max(0.0, min(height - 2.0 * CHEVRON_PAD if height > 0.0 else 0.0,
                        width))
    if side <= 0.0:
        return (left, top, 0.0, 0.0)
    return (left + width - side - CHEVRON_PAD,
            top + (height - side) * 0.5, side, side)


def _there(rect: Rect, x: Any, y: Any) -> bool:
    """Whether a point is inside a box, half-open the way every hit test is."""
    left, top, width, height = rect
    return (left <= float(x) < left + width
            and top <= float(y) < top + height)


def on_mark(group: Any, x: Any, y: Any) -> bool:
    """Whether the point is on this group's fold mark."""
    if not foldable_of(group):
        return False
    return _there(chevron_box(group), x, y)


def on_header(group: Any, x: Any, y: Any) -> bool:
    """Whether the point is on the band, the mark included."""
    return _there(header_box(group), x, y)


def title_of(group: Any) -> str:
    """The word in the header: ``title``, or the label a script called it by."""
    said = str(group.get("title") or "")
    return said or str(group.label or "")


# --------------------------------------------------------------------------
# the fold
# --------------------------------------------------------------------------


def folded_of(group: Any) -> bool:
    """Whether only the header is shown - the prop, which is the fact."""
    return bool(group.get("folded"))


def foldable_of(group: Any) -> bool:
    """Whether the header offers the fold at all.

    A kind that was never given the row answers ``True``: a variant of a plain
    panel has no mark to hide, and every caller of this is already a group.
    """
    if not group.kind.has("foldable"):
        return True
    return bool(group.get("foldable"))


def fold(group: Any, wanted: Any = None) -> bool:
    """Fold or unfold one group; whether the prop moved.

    ``wanted`` left out means "the other one", which is what a click on a chevron
    is. The prop is written and nothing else: the body's visibility and the box are
    answered by :func:`fit_group` on the next pass, so a script's
    ``SET(g, "folded", 1)`` and this call and a hand on the mark are one gesture
    with three spellings rather than three implementations.
    """
    if not group.kind.has("folded"):
        raise GroupError(f"a '{group.kind.name}' has no fold; '{group.path()}' "
                         "is not a group, so there is nothing here to close")
    state = (not folded_of(group)) if wanted is None else bool(wanted)
    if state != folded_of(group) and not foldable_of(group):
        raise GroupError(f"'{group.path()}' says it is not foldable; "
                         "SET(g, \"foldable\", 1) gives the mark back")
    return group.set("folded", state)


def _owns_height(group: Any) -> bool:
    """Whether this group's height is its own to decide.

    ``fill`` and ``grow`` are a container's promise about the room nothing else
    used, and an edge glued on ``y`` is a glue's; a group under either of those is
    the size its box was given, folded or not. Everything else - the default
    ``fixed``, with nothing glued - is the group's own, and is measured from what
    its body holds.
    """
    policy = getattr(group, "layout", None)
    if policy is None:
        return True
    if policy.pack_y != "fixed":
        return False
    return not any(edge in Y_EDGES for edge in policy.anchors)


def _content_height(body: Any) -> float:
    """How tall the rows inside a body are: the bottom of the last one.

    :func:`crude.ui.scroll.content_height` for a viewport's rows, and deliberately
    the same rule - every child counts, hidden ones included, because a row a
    script switched off is still a row the page is made of and its place should not
    close up when nobody is looking at it.
    """
    bottom = 0.0
    for kid in getattr(body, "children", ()):
        bottom = max(bottom, float(kid.y) + float(kid.height))
    return max(0.0, bottom)


def _wanted_height(group: Any, open_now: bool) -> Optional[float]:
    """How tall this frame says the group is, or ``None`` for a box not its own.

    A folded group is exactly as tall as its header; an open one is its header plus
    what it holds. The insets are read off the group rather than assumed, because
    ``PAD(g, 16)`` moves where the children start without moving where the box ends,
    and a header band that ignored the difference would either clip its own title
    or leave a gap a person would have to explain.

    They are read off ``_inset`` - the padding the last :meth:`sync` measured -
    rather than as ``height - content_box().height``, which is the same number only
    once the box has already been settled to it. That difference is a fixed point:
    a group inside a group would take one pass per level of nesting to reach its
    own height, and a group that is briefly 0 px wide - before the row or cell that
    sizes it has run - would answer with no insets at all. The inset the padding
    produced does not depend on either, so the first pass is the last one.
    """
    if not _owns_height(group):
        return None
    insets = 2.0 * max(0.0, float(getattr(group, "_inset", 0.0) or 0.0))
    band = HEADER_HEIGHT + insets
    if open_now:
        body = body_of(group)
        band += _content_height(body) if body is not None else 0.0
    return band


def _accordion(group: Any) -> None:
    """Opening one group closes the others in the same box, when the box says so.

    Read on the panel that holds the groups, because "accordion" is a rule about a
    set of them and not about one. Called for the one group that has just been
    opened, which is why it can answer with "fold the rest" rather than with
    "make exactly one of these open" - the second is a rule that would fold the
    group the person clicked on a frame later, and a fold nobody asked for is the
    kind of defect only a person with a mouse finds.
    """
    holder = group.parent
    if holder is None or not holder.kind.has("accordion"):
        return
    if not bool(holder.get("accordion")):
        return
    for there in groups_of(holder):
        if there is group or folded_of(there):
            continue
        there.set("folded", True)


def fit_group(group: Any) -> int:
    """Settle one group's own geometry before anything is arranged; boxes written.

    The half of a fold that has to happen in the layout pass's first sweep, for the
    reason the plan learned from a viewport's width: a stack and a grid read a
    child's thickness to decide what it gets, so a group that shrank *after* the
    arrangement was sized for the band it no longer wanted and the fold would show
    up a frame late.

    Two answers, both from the props rather than from anything kept here. The box
    comes from :func:`_wanted_height`. The body is shown when the group is open and
    hidden when it is not, which is what makes a row inside a folded group neither
    painted nor clickable rather than painted and refusing to answer - and the
    comparison that decides whether to write it is also the only honest answer to
    "did this group just open", which is the question :func:`_accordion` needs
    asked before the answer is written rather than after it.
    """
    if not is_group(group):
        return 0
    body = group.content_child
    open_now = not bool(group.get("folded"))
    moved = 0
    wanted = _wanted_height(group, open_now)
    if wanted is not None and abs(float(group.height) - wanted) > _SETTLED:
        moved += 1 if group.place(None, None, None, wanted) else 0
    if bool(body.visible) != open_now:
        if open_now:
            _accordion(group)
        if body.set("visible", open_now):
            moved += 1
    return moved


# --------------------------------------------------------------------------
# the order, and the cell
# --------------------------------------------------------------------------


def order_of(group: Any) -> int:
    """Where this group stands among the children it is drawn with, from 0."""
    return int(group.z)


def move_to(group: Any, position: Any) -> Any:
    """Re-order a group within whatever holds it; the group.

    z-order *is* the arrangement: a stack, a column and a grid all enumerate the
    children in the order they are drawn, so moving in the list is the re-arrange -
    the next pass lays the boxes out in the new order and nobody has to be told
    that a row moved. A grid is the one case with a number of its own to keep up
    with, so the cells are re-declared along with the order rather than left
    pointing at where the group used to be.
    """
    group._move_z(int(position))
    _renumber(group.parent)
    return group


def move_by(group: Any, steps: Any) -> Any:
    """Up or down the list: ``GROUP_UP``/``GROUP_DOWN`` are this at ``±1``."""
    return move_to(group, order_of(group) + int(steps))


def _renumber(holder: Any) -> None:
    """Re-declare the cells of a grid after its children were shuffled."""
    if holder is None or spec_of(holder) is None:
        return
    for index, kid in enumerate(getattr(holder, "children", ())):
        assign(kid, index)


def assigned(kid: Any) -> int:
    """The cell this child told the pass to put it in, or ``-1`` for "in order".

    The read-only ``cell`` row is the answer, which is what lets the arrangement,
    the face that outlines a cell and a script's ``GET(g, "cell")`` all read one
    number rather than three that happen to agree.
    """
    kind = getattr(kid, "kind", None)
    if kind is None or not kind.has("cell"):
        return -1
    return int(kid.get("cell") or 0)


def cells_of(kids: Sequence[Any], count: Any = 0) -> Tuple[List[int], int]:
    """``(the cell each child occupies, how many cells the row of them needs)``.

    A child that was docked somewhere names its own cell and gets it; every other
    child takes the next free one, in the order they are drawn. Two claimants for
    one cell is not a contradiction to solve but a thing to refuse, and the refusal
    is :func:`dock`'s - here the first claim in child order simply wins and the
    other takes a free cell, which is the same answer the pass would have given had
    nobody been dropped on top of it.

    ``count`` is the number of cells the caller already knows about (a face and a
    drop read it off the grid the last pass measured, the arranger passes the child
    count). The returned total is at least that, at least the child count, and big
    enough to hold every named cell - which is what lets a group be dropped into
    the far cell of an empty row without the grid being redrawn smaller than the
    cell it just promised.
    """
    asked = [assigned(kid) for kid in kids]
    total = max(int(count or 0), len(kids), 1)
    for there in asked:
        if there >= total:
            total = there + 1
    taken: dict = {}
    for index, there in enumerate(asked):
        if there >= 0 and there not in taken:
            taken[there] = index
    free = [cell for cell in range(total) if cell not in taken]
    out: List[int] = []
    cursor = 0
    for index, there in enumerate(asked):
        if there >= 0 and taken.get(there) == index:
            out.append(there)
            continue
        if cursor < len(free):
            out.append(free[cursor])
            cursor += 1
        else:
            # more children than cells: it is drawn over the last one rather than
            # given a rectangle that does not exist, which is what a grid with no
            # room for its content has always done
            out.append(total - 1)
    return out, total


def assign(kid: Any, cell: Any) -> bool:
    """Record the cell this child was arranged into; whether the row moved.

    A measurement and nothing else: no ``ON_CHANGE``, nothing in ``changed``. A
    group that is drawn into cell two because the panel it lives in was widened is
    not a group a person moved.
    """
    kind = getattr(kid, "kind", None)
    if kind is None or not kind.has("cell"):
        return False
    return bool(kid._measure("cell", float(int(cell))))


def occupant(panel: Any, cell: Any) -> Optional[Any]:
    """The child already occupying one cell of this panel, or ``None`` for free."""
    count = cells_offered(panel)
    if count is None:
        return None
    kids = list(getattr(panel, "children", ()))
    out, _total = cells_of(kids, count)
    wanted = int(cell)
    for index, there in enumerate(out):
        if there == wanted:
            return kids[index]
    return None


def cells_offered(panel: Any) -> Optional[int]:
    """How many cells this panel offers a dock, whether a frame has been drawn or not.

    :func:`~crude.ui.grid.spec_of` answers from the rows the pass measured, which is
    the right source for a face and for a drop - both work in rectangles, and a
    rectangle only exists once the pass produced it. A script that docks on the line
    after its ``GRID`` has had no frame yet, and the shape it named is already a
    fact: the columns it said, and the rows its children fill. Refusing that with
    "this panel has no cells" would be describing a thing the script did do, so the
    declaration is read instead. ``None`` stays the honest answer for a panel that
    was never arranged into a grid at all.

    Only the count is taken from the declaration, because it is the one number that
    does not need the skin: a gap named ``"md"`` is pixels only once a pass has
    resolved it, and :class:`~crude.ui.grid.GridSpec` counts cells without dividing
    a box. Whoever needs the rectangles asks :func:`~crude.ui.grid.cells` after a
    frame, which is the same order a person drops in - the pointer is on the screen
    only once something has been drawn.
    """
    spec = spec_of(panel)
    if spec is not None:
        return spec.count
    stack = getattr(getattr(panel, "layout", None), "stack", None)
    if stack is None or not stack.is_grid:
        return None
    _claims, total = cells_of(list(getattr(panel, "children", ())), 0)
    return GridSpec.of(total, stack.columns).count


# --------------------------------------------------------------------------
# the dock
# --------------------------------------------------------------------------


def dock(group: Any, cell: Any, panel: Any = None) -> Any:
    """Put a group in one cell of a dockable grid; the panel it landed in.

    Every refusal says what was refused and names the thing that would answer it,
    because "why didn't my group move" is a question about three separate facts -
    whether the group may be picked up, whether the panel has cells, whether the
    one under the pointer is free - and a single error that could mean any of them
    is a bug report a person writes instead of a bug they fix.

    The move is a re-parent followed by a cell assignment, in that order: ``add``
    takes the group out of wherever it was and routes it into whatever the panel
    holds its children in, the assignment is what :func:`cells_of` reads back, and
    the next pass draws it at exactly the rectangle the drop said - through the one
    formula, which is the whole point of this module having one.
    """
    holder = panel if panel is not None else group.parent
    if holder is None:
        raise GroupError(f"'{group.path()}' is not in a tree, so there is no "
                         "cell to dock it into; add it to a panel first or name "
                         "the one to put it in")
    if not group.kind.has("dockable") or not bool(group.get("dockable")):
        raise GroupError(f"'{group.path()}' is not dockable; SET(g, "
                         "\"dockable\", 1) is what lets it be picked up")
    count = cells_offered(holder)
    if count is None:
        raise GroupError(f"'{holder.path()}' has no cells to dock into; "
                         "GRID(panel, columns, gap, ...) with \"dockable\" 1 "
                         "is what gives a panel some")
    wanted = int(cell)
    if not 0 <= wanted < count:
        raise GroupError(f"'{holder.path()}' has {count} cells, counted "
                         f"from 0, and {cell} is not one of them")
    there = occupant(holder, wanted)
    if there is not None and there is not group:
        raise GroupError(f"cell {wanted} of '{holder.path()}' already holds "
                         f"'{there.path()}'; it is a cell, not a stack")
    group.detach()
    holder.add(group)
    assign(group, wanted)
    group._move_z(min(wanted, max(0, len(holder.children) - 1)))
    return holder


def undock(group: Any) -> Any:
    """Give up the cell: the group goes back to the end of the panel it is in.

    Only the claim is dropped - the panel keeps it, because where a group *lives*
    and which cell it is drawn in are two facts and the second is the one a dock
    wrote. ``raise_to_top`` then puts it after its siblings, which is what "back at
    the panel's end" means to a grid: the free cells are taken in order, so the
    last child in the list is the last cell it fills.
    """
    assign(group, -1)
    group.raise_to_top()
    return group


def dock_target(group: Any, x: Any, y: Any) -> Tuple[Any, int]:
    """``(panel, cell)`` the point means for this group, or ``(None, -1)``.

    Every dockable panel with measured cells that the point is inside is a
    candidate and the innermost one wins - a cell in a panel inside a panel is the
    cell the pointer was on, and a drop answered by the outer one would move the
    group somewhere the hand never went. A point over no dockable panel, or over
    the gap between two cells, is no answer at all: the honest refusal of a drop
    that would land a group where the pointer was not.
    """
    found = group.root() if hasattr(group, "root") else None
    best: Any = None
    best_cell = -1
    depth = -1
    for panel in (found.walk() if found is not None else ()):
        if not panel.kind.has("dockable") or not bool(panel.get("dockable")):
            continue
        spec = spec_of(panel)
        if spec is None:
            continue
        cell = spec.index_at(float(x), float(y), panel.content_box())
        if cell is None:
            continue
        there = _depth(panel)
        if there > depth:
            best, best_cell, depth = panel, cell, there
    return best, best_cell


def _depth(panel: Any) -> int:
    """How far down the tree this panel is; how strictly it encloses a point."""
    held, out = panel, 0
    while getattr(held, "parent", None) is not None:
        held = held.parent
        out += 1
    return out


def drop(group: Any, x: Any, y: Any) -> Optional[Any]:
    """Answer a released drag: the panel the group was dropped into, or ``None``.

    Nothing is raised here, whatever the reason for not moving. A pointer is not a
    request: a hand that lets go over a taken cell or over nothing at all is told
    by the group staying exactly where it was, and the same situation reached from
    a script is :func:`dock`, which names the refusal, because a script does have a
    thing to do with a reason.
    """
    panel, cell = dock_target(group, x, y)
    if panel is None:
        return None
    if not bool(group.get("dockable")):
        # the press refused to pick it up, so nothing should have come here; the
        # check is repeated rather than trusted because a script may have written
        # the row between the two, and a gesture must not raise on a release
        return None
    there = occupant(panel, cell)
    if there is not None and there is not group:
        return None
    return dock(group, cell, panel)


# --------------------------------------------------------------------------
# the rows the pass answers for
# --------------------------------------------------------------------------


def measure(root: Any) -> int:
    """Answer every group's ``order`` and ``cell``; how many rows moved.

    Run after the arrangement and the anchors, because both numbers are answers
    about where the pass put a group rather than about what the script asked for:
    ``order`` is its place in the list that list was arranged from, and ``cell`` is
    the rectangle it was given. The arranger owns the second one for a child of a
    grid - it is the one that knows which cell was actually drawn - so this only
    resets the groups nobody arranged into a cell, which is what makes ``-1`` mean
    "this panel is not a grid" and not "nobody has told me yet".
    """
    moved = 0
    for held in root.walk():
        if not is_group(held):
            continue
        if held._measure("order", float(order_of(held))):
            moved += 1
        there = held.parent
        if there is None or spec_of(there) is None:
            if held._measure("cell", -1.0):
                moved += 1
    return moved
