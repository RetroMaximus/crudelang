"""A group: a header, a body, a fold, an order and a cell it can be dropped in.

Every number in this file was measured by running the scene it describes - the
probes that produced them are gone, the assertions they printed are here. That is
the plan's rule for this stage read literally: a group is a box whose geometry is
*derived* (the header's band, the body under it, the fold's height, the cell it was
dropped into), and a derived number asserted from arithmetic rather than from the
pass that produces it is a test that fails the day somebody reads the arithmetic
differently.

Three things the tests keep re-touching, so they are said once here:

* ``ctx.layout()`` and not ``widget.sync()`` is the pass. Stacks, grids, anchors,
  folds and the ``cell``/``order`` rows are all answered inside
  :func:`crude.ui.layout.resolve`, so any assertion about geometry runs it first.
* **a stack or a grid inside a group's body needs two frames**, and
  :func:`settle` is what every test below calls. The body's width comes from the
  anchors, which are the *last* sweep of a pass, while the row inside it is arranged
  in the second - so the first frame of a nested scene sizes its children against
  the box this frame has not written yet. The pass that answers "nothing moved" is
  the proof the scene settled rather than converged on a number the test trusts.
* **a dockable group wants ``PACK(g, "fill", "fixed")``.** A grid cell gives a
  ``fixed`` child only the size it already has, so a group with no width of its own
  is drawn 0 px wide, its band is 0 px wide, and a press in it is claimed by
  whatever is underneath. That is the grid's ordinary rule, not a group's, and the
  test for it is here rather than hidden in a fixture.

The insets every box below carries are the skin's own plate padding (``9`` logical
pixels at ``dark``/1x), written out as :data:`INSET` because three of the shapes are
easier to check against a named number than against a re-derivation.
"""

from __future__ import annotations

import types

import pytest

import crude.bindings  # noqa: F401 - registers every area, including ui
from crude.lang import Program
from crude.lang.values import HostHandle, List, Number, String
from crude.ui import (GROUP_KIND, HEADER_HEIGHT, RecordingRenderer, Skin,
                      UIContext, Widget, arrange, dock, fold, groups_of, is_group,
                      move_by, move_to, order_of, pack, pad, reset, undock)
from crude.ui import props
from crude.ui.grid import (COLUMNS, DOCKABLE, DOCKING, GAP, ROWS, GridSpec,
                           spec_of)
from crude.ui.group import (CHEVRON_PAD, GROUP_BODY_SUFFIX,
                            GROUP_HEADER_SUFFIX, MARK_FOLDED, MARK_OPEN,
                            TITLE_PAD, Y_EDGES, GroupError, assigned, body_box,
                            body_of, cells_offered, chevron_box, dock_target, drop,
                            foldable_of, folded_of, header_box, header_of,
                            occupant, title_of)
from crude.ui.layout import AXIS


@pytest.fixture(autouse=True)
def clean_registry():
    """Every kind a test defines with ``WIDGET_DEFINE`` dies with the test."""
    reset()
    yield
    reset()


# -- the measured scene, and the numbers it answers ---------------------

#: the window every fixture below is built in
ACROSS, DOWN = 360.0, 400.0

#: the panel the groups are put in: the whole page, no padding of its own
PAGE = (0.0, 0.0, 300.0, 300.0)

#: the skin's plate padding, which every box here is measured against
INSET = 9.0

#: one knob, and how tall the three of them stack up to
KNOB = (40.0, 30.0)

#: the gap between two cells, and the two rows of a group the tests share
CELL_GAP = 18.0
ROW_GAP = 10.0

#: an open group holding three knobs, and the same group folded
OPEN_HEIGHT = HEADER_HEIGHT + 2 * INSET + 3 * KNOB[1]
FOLDED_HEIGHT = HEADER_HEIGHT + 2 * INSET


def stage():
    """A context over a renderer that remembers, with the root's own padding off."""
    ctx = UIContext(RecordingRenderer(), width=ACROSS, height=DOWN)
    pad(ctx.root, 0.0)
    return ctx


def page(ctx, box=PAGE):
    """A panel to hold groups, placed and padded so every number is the group's."""
    held = Widget("panel", {"label": ""}, name="page", parent=ctx.root)
    pad(held, 0.0)
    held.place(*box)
    return held


def section(holder, name, rows=3, *, fill=True):
    """One group called ``name`` holding ``rows`` knobs, in its body.

    ``fill=True`` is the dockable recipe from the module docstring: the group is as
    wide as the row or cell that holds it and as tall as the knobs inside it. Left
    out, the group keeps whatever box it was placed at, which is what the tests
    about a container owning the height want.
    """
    there = Widget("group", {"title": name}, name=name, parent=holder)
    if fill:
        pack(there, "fill", "fixed")
    for index in range(rows):
        Widget("knob", {"label": f"k{index}"}, name=f"{name}k{index}",
               parent=there).place(0.0, index * KNOB[1], *KNOB)
    return there


def settle(ctx, times=3):
    """Lay the tree out until a frame answers that nothing moved.

    Returns the count each frame wrote, so a test that cares about *when* something
    settled can read it. The last is always ``0`` - that assertion is the point of
    the helper, and a scene that is still moving after three frames is a scene this
    file has to describe rather than a scene the test should paper over.
    """
    moved = [ctx.layout() for _ in range(times)]
    assert moved[-1] == 0, f"still moving after {times} frames: {moved}"
    return moved


def painted(tree):
    """One frame of ``tree`` drawn into a recorder that is thrown away after.

    A face is tested against the widget that drew it: the root and the page a group
    sits in each have a plate of their own, so an assertion written against the
    whole frame would be counting how many panels the fixture happened to need.
    """
    held = RecordingRenderer()
    held.begin(ACROSS, DOWN, ACROSS, DOWN)
    tree.paint(held, Skin("dark"))
    return held


def boxes(every):
    """The box each of these widgets is standing in, in the order asked."""
    return [held.box for held in every]


def grid_of_four(ctx, *, rows=1, dockable=True):
    """Four one-knob groups in a grid of three columns: two rows, six cells.

    Four in three is deliberate. The cell count of a stacked panel is the number of
    children extended by any cell somebody claimed, so a grid whose children divide
    evenly has no free cell and nowhere to drop - the honest answer of a panel that
    is full. One leftover is what makes this a dock rather than a display.
    """
    deck = page(ctx)
    held = [section(deck, word, rows) for word in ("a", "b", "c", "d")]
    for there in held:
        there.set(DOCKABLE, 1 if dockable else 0)
    arrange(deck, {"direction": "grid", "columns": 3, "gap": CELL_GAP})
    deck.set(DOCKABLE, 1)
    settle(ctx)
    return deck, held


# -- the shape of the thing ----------------------------------------------


def test_a_group_is_two_panels_the_tree_built_and_ones_the_script_never_sees():
    """``add`` handed the knobs to the body, not to the group's own child list."""
    ctx = stage()
    deck = page(ctx)
    carrier = section(deck, "carrier")
    settle(ctx)
    assert [kid.name for kid in carrier.children] == ["carrier#header",
                                                      "carrier#body"]
    head, body = carrier.children
    assert header_of(carrier) is head
    assert body_of(carrier) is body
    assert body is carrier.content_child
    assert not head.visible, "the band is painted by the group, not by a panel"
    assert head.role == "caption"
    assert head.kind.name == "panel" and body.kind.name == "panel"
    assert [kid.parent.name for kid in body.children] == ["carrier#body"] * 3


def test_the_three_boxes_a_face_and_a_gesture_share_are_one_arithmetic():
    """The band, the mark and the body: measured off the laid-out group.

    ``132`` is the header plus the skin's two insets plus the three knobs the body
    holds - the fold is a measurement, so the number is written down here as the
    sum the pass is made of rather than as a constant nothing explains.
    """
    ctx = stage()
    carrier = section(page(ctx), "carrier")
    settle(ctx)
    assert carrier.box == (0.0, 0.0, 300.0, OPEN_HEIGHT)
    assert OPEN_HEIGHT == 132.0
    assert header_box(carrier) == (INSET, INSET, 282.0, HEADER_HEIGHT)
    assert body_box(carrier) == (INSET, INSET + HEADER_HEIGHT, 282.0, 90.0)
    assert carrier.content_box() == (INSET, INSET, 282.0, 114.0)
    mark = chevron_box(carrier)
    assert mark == (273.0, 15.0, 12.0, 12.0)
    # the mark is at the far end of the band, one CHEVRON_PAD inside it
    assert mark[0] + mark[2] == INSET + 282.0 - CHEVRON_PAD
    assert mark[1] == INSET + (HEADER_HEIGHT - mark[3]) / 2.0


def test_a_folded_group_is_its_header_tall_and_its_body_is_gone():
    """Folding takes the body's room away; opening gives it back, unwritten."""
    ctx = stage()
    carrier = section(page(ctx), "carrier")
    settle(ctx)
    assert fold(carrier)
    settle(ctx)
    assert folded_of(carrier)
    assert carrier.box == (0.0, 0.0, 300.0, FOLDED_HEIGHT)
    assert FOLDED_HEIGHT == 42.0
    body = body_of(carrier)
    assert not body.visible
    assert body_box(carrier) == (INSET, INSET + HEADER_HEIGHT, 282.0, 0.0)
    # the knobs are still in there with the places they had: nothing to re-do on
    # the way back, which is the difference between a measurement and a memory
    assert [kid.y for kid in body.children] == [0.0, 30.0, 60.0]
    assert fold(carrier, False)
    settle(ctx)
    assert carrier.box == (0.0, 0.0, 300.0, OPEN_HEIGHT)
    assert body.visible


def test_a_group_whose_height_is_its_containers_keeps_it_when_it_folds():
    """``PACK(g, "fill", "fill")``: the body is hidden, the cell is not.

    The other half of the same rule the module docstring says twice. A group in a
    grid row that is meant to fill it does not get to decide its own height, folded
    or not - and a test that only ever folded ``fixed`` groups would never find out
    that the fold had become a second owner of an axis it was never given.
    """
    ctx = stage()
    tall = section(page(ctx), "tall", rows=2, fill=False)
    pack(tall, "fill", "fill")
    tall.place(0.0, 0.0, 10.0, 10.0)
    settle(ctx)
    assert tall.box == (0.0, 0.0, 300.0, 300.0)
    fold(tall)
    settle(ctx)
    assert tall.box == (0.0, 0.0, 300.0, 300.0), "the container owns the axis"
    assert not body_of(tall).visible


def test_the_header_is_invisible_so_the_group_is_what_a_press_claims():
    """``hit`` under the band answers the group, and the band is not a child to find.

    This is why there is no event forwarding anywhere in :mod:`crude.ui.group`: the
    panel exists so a fold has a place on the screen to be measured against, and the
    one widget with a behaviour for it is the group itself.
    """
    ctx = stage()
    carrier = section(page(ctx), "carrier")
    settle(ctx)
    head = header_box(carrier)
    point = (head[0] + 20.0, head[1] + head[3] / 2.0)
    assert ctx.root.hit(*point) is carrier
    assert not header_of(carrier).visible
    # a point in the body is the knob's, so the band is not swallowing the page
    assert ctx.root.hit(20.0, 50.0) is carrier.content_child.children[0]


# -- what it draws --------------------------------------------------------


def test_the_face_draws_the_plate_the_title_and_one_mark_per_state():
    """An open band has the rule under it; a folded one has nothing under it."""
    ctx = stage()
    carrier = section(page(ctx), "carrier")
    settle(ctx)
    held = painted(carrier)
    names = held.names()
    # the plate's two rects, the title, the mark, the rule: then the knobs' own
    assert names[:5] == ["rect", "rect", "text", "atlas", "line"]
    title = held.ops_named("text")[0]
    assert title.get("text") == "carrier"
    assert title.get("x") == INSET + TITLE_PAD
    assert title.get("align") == "left"
    assert title.get("size") == 11.0
    mark = held.ops_named("atlas")[0]
    assert (mark.get("x"), mark.get("y"),
            mark.get("width"), mark.get("height")) == chevron_box(carrier)
    rule = held.ops_named("line")[0]
    assert (rule.get("x1"), rule.get("y1")) == (INSET, INSET + HEADER_HEIGHT)
    assert rule.get("x2") == INSET + 282.0
    assert rule.get("y2") == rule.get("y1"), "the rule is flat under the band"

    fold(carrier)
    settle(ctx)
    closed = painted(carrier)
    assert closed.names() == ["rect", "rect", "text", "atlas"]
    assert closed.count("line") == 0, "nothing is under a band that has no body"
    assert closed.count("atlas") == 1, "the fold is still one click away"
    assert closed.texts() == ["carrier"], "a folded group still says what it is"
    assert closed.count("polyline") == 0


def test_the_two_states_draw_two_different_marks():
    """An open group and a folded one in one frame do not share a glyph.

    Asserted through one renderer rather than by comparing two frames: the fake
    builds its atlas as glyphs are first asked for, so a fresh recorder gives every
    name the same cell and the comparison would prove nothing about the face.
    """
    ctx = stage()
    deck = page(ctx)
    arrange(deck, {"direction": "vertical", "gap": ROW_GAP})
    open_now = section(deck, "open", rows=1)
    shut = section(deck, "shut", rows=1)
    fold(shut)
    settle(ctx)
    held = painted(ctx.root)
    marks = held.ops_named("atlas")
    assert len(marks) == 2
    for op, there in zip(marks, (open_now, shut)):
        assert (op.get("x"), op.get("y"),
                op.get("width"), op.get("height")) == chevron_box(there)
    assert marks[0].get("uv") != marks[1].get("uv")
    # and the two names really are two entries of the atlas, not a typo that
    # fell through to the placeholder box
    atlas = RecordingRenderer()
    atlas.begin(ACROSS, DOWN, ACROSS, DOWN)
    _, open_uv = atlas.glyph_texture(MARK_OPEN, 12.0)
    _, fold_uv = atlas.glyph_texture(MARK_FOLDED, 12.0)
    _, bogus_uv = atlas.glyph_texture("no-such-glyph", 12.0)
    assert open_uv != fold_uv
    assert bogus_uv not in (open_uv, fold_uv)


def test_the_title_is_the_header_word_and_the_label_is_the_fallback():
    """``title`` is what is drawn; with nothing in it the widget's own label is."""
    ctx = stage()
    deck = page(ctx)
    named = Widget("group", {"label": "ByLabel"}, name="named", parent=deck)
    bare = Widget("group", name="bare", parent=deck)
    pack(named, "fill", "fixed")
    settle(ctx)
    assert title_of(named) == "ByLabel"
    assert title_of(bare) == "", "a group with nothing to say draws no word"
    assert painted(ctx.root).texts() == ["ByLabel"]


# -- a group inside a group ------------------------------------------------


def test_a_nested_group_is_measured_by_the_same_rule_and_folds_on_its_own():
    """The outer group's height includes the inner's, folded or open.

    Three knobs of arithmetic in one place: the inner is two knobs tall (``102``),
    the outer holds the inner, a ``10`` gap and a ``20`` tall knob, so it is
    ``24 + 18 + 132``. This is the scene :func:`settle` exists for - the row inside
    the outer body cannot be arranged against a width the anchors have not written
    yet, so the first frame is late by one and the test says so out loud.
    """
    ctx = stage()
    deck = page(ctx)
    outer = section(deck, "outer", rows=0)
    inner = section(outer, "inner", rows=2)
    tail = Widget("knob", {"label": "t"}, name="tail", parent=outer)
    pack(tail, "fill", "fixed")
    tail.place(0.0, 0.0, 40.0, 20.0)
    arrange(outer, {"direction": "vertical", "gap": ROW_GAP})
    settle(ctx)
    assert boxes((outer, inner, tail)) == [(0.0, 0.0, 300.0, 174.0),
                                           (0.0, 0.0, 282.0, 102.0),
                                           (0.0, 112.0, 282.0, 20.0)]
    assert body_box(inner) == (2 * INSET, 66.0, 264.0, 60.0)
    assert header_box(outer) == (INSET, INSET, 282.0, HEADER_HEIGHT)
    assert chevron_box(inner) == (264.0, 48.0, 12.0, 12.0)

    fold(inner)
    settle(ctx)
    assert boxes((outer, inner)) == [(0.0, 0.0, 300.0, 114.0),
                                     (0.0, 0.0, 282.0, FOLDED_HEIGHT)]

    fold(outer)
    settle(ctx)
    assert outer.box == (0.0, 0.0, 300.0, FOLDED_HEIGHT)
    assert not body_of(outer).visible and not body_of(inner).visible

    fold(outer, False)
    settle(ctx)
    assert outer.box == (0.0, 0.0, 300.0, 114.0)
    assert folded_of(inner), "opening a parent does not open what it holds"


# -- the accordion ----------------------------------------------------------


def test_an_accordion_panel_is_always_one_section_tall():
    """Opening one group on a page that says ``accordion`` closes its siblings.

    The rule is read on the panel holding the groups, not on the groups: "opening
    one folds the others" is a statement about a set, and a nested group's parent is
    an ordinary body panel that was never asked to be one.
    """
    ctx = stage()
    deck = page(ctx)
    arrange(deck, {"direction": "vertical", "gap": ROW_GAP})
    deck.set("accordion", 1)
    first = section(deck, "carrier", rows=3)
    second = section(deck, "modulator", rows=1)
    settle(ctx)
    assert boxes((first, second)) == [(0.0, 0.0, 300.0, 132.0),
                                      (0.0, 142.0, 300.0, 72.0)]

    fold(first)
    settle(ctx)
    assert boxes((first, second)) == [(0.0, 0.0, 300.0, FOLDED_HEIGHT),
                                      (0.0, 52.0, 300.0, 72.0)]

    # asking an already-open group to open is not a gesture, so nothing folds
    fold(second, False)
    settle(ctx)
    assert folded_of(first) and not folded_of(second)

    fold(first, False)
    settle(ctx)
    assert not folded_of(first), "the one asked for is open"
    assert folded_of(second), "and its sibling was closed by the same frame"
    assert boxes((first, second)) == [(0.0, 0.0, 300.0, 132.0),
                                      (0.0, 142.0, 300.0, FOLDED_HEIGHT)]


def test_a_close_is_not_an_accordion_move():
    """Folding one group never folds another, with the rule on or off.

    Half of the reason :func:`crude.ui.group._accordion` is called from the group
    that has just been *opened*: a rule that read "make exactly one of these open"
    would fold the group the person clicked a frame later, and a fold nobody asked
    for is the kind of defect only a person with a mouse finds.
    """
    ctx = stage()
    deck = page(ctx)
    arrange(deck, {"direction": "vertical", "gap": ROW_GAP})
    deck.set("accordion", 1)
    first = section(deck, "carrier", rows=3)
    second = section(deck, "modulator", rows=1)
    settle(ctx)
    deck.set("accordion", 0)
    fold(first)
    settle(ctx)
    assert folded_of(first)
    assert not folded_of(second), "closing one left the other exactly as it was"


def test_opening_a_nested_group_does_not_reach_up_to_its_parent():
    """The page's ``accordion`` has nothing to say about a group inside a group."""
    ctx = stage()
    deck = page(ctx)
    arrange(deck, {"direction": "vertical", "gap": ROW_GAP})
    deck.set("accordion", 1)
    outer = section(deck, "outer", rows=0)
    inner = section(outer, "inner", rows=1)
    settle(ctx)
    assert boxes((outer, inner)) == [(0.0, 0.0, 300.0, 114.0),
                                     (0.0, 0.0, 282.0, 72.0)]
    fold(inner)
    fold(inner, False)
    settle(ctx)
    assert not folded_of(outer), "the parent was never a sibling of what opened"
    assert not folded_of(inner)
    assert outer.box == (0.0, 0.0, 300.0, 114.0)


# -- the order, which is the arrangement --------------------------------


def stack_of_three(ctx):
    """Three groups of one, two and three knobs in a vertical row, ``10`` apart.

    The three heights (``72``, ``102``, ``132``) are deliberately different: a test
    of a reorder among three equal boxes cannot tell whether the boxes moved or only
    the numbers under them did.
    """
    deck = page(ctx)
    arrange(deck, {"direction": "vertical", "gap": ROW_GAP})
    held = [section(deck, word, count) for word, count in
            (("one", 1), ("two", 2), ("three", 3))]
    settle(ctx)
    return deck, held


def test_the_order_a_stack_draws_is_the_order_a_script_moves():
    """``GROUP_MOVE`` re-orders the children; the next pass lays them out again.

    There is no second arrangement to keep in step: z-order *is* the order a stack,
    a column and a grid enumerate, so a move is a re-arrange and the boxes below are
    the ones the row produced in the new order.
    """
    ctx = stage()
    deck, (one, two, three) = stack_of_three(ctx)
    assert boxes((one, two, three)) == [(0.0, 0.0, 300.0, 72.0),
                                       (0.0, 82.0, 300.0, 102.0),
                                       (0.0, 194.0, 300.0, 132.0)]
    assert [order_of(g) for g in (one, two, three)] == [0, 1, 2]
    assert [g.get("order") for g in (one, two, three)] == [0.0, 1.0, 2.0]
    assert [g.name for g in groups_of(deck)] == ["one", "two", "three"]

    move_to(three, 0)
    settle(ctx)
    assert [g.name for g in groups_of(deck)] == ["three", "one", "two"]
    assert boxes(groups_of(deck)) == [(0.0, 0.0, 300.0, 132.0),
                                      (0.0, 142.0, 300.0, 72.0),
                                      (0.0, 224.0, 300.0, 102.0)]
    assert [order_of(g) for g in groups_of(deck)] == [0, 1, 2]


def test_a_position_past_the_end_is_answered_with_the_place_it_took():
    """``GROUP_MOVE`` clamps, and ``order`` and the row's answer are the same.

    The verb is used from a menu row that has no idea how many groups a page holds,
    so a number past the end is not an error - but a script that asked for nine and
    was put at three has to be able to read the three back.
    """
    ctx = stage()
    deck, (one, _two, _three) = stack_of_three(ctx)
    assert order_of(move_to(one, 9)) == 2
    settle(ctx)
    assert [g.name for g in groups_of(deck)] == ["two", "three", "one"]
    assert one.get("order") == 2.0
    assert move_by(one, 1) is one, "there is nowhere further down to go"
    assert order_of(one) == 2


def test_group_up_and_down_are_the_same_call_at_one_step():
    ctx = stage()
    deck, (one, two, three) = stack_of_three(ctx)
    move_by(two, -1)
    settle(ctx)
    assert [g.name for g in groups_of(deck)] == ["two", "one", "three"]
    assert boxes(groups_of(deck)) == [(0.0, 0.0, 300.0, 102.0),
                                      (0.0, 112.0, 300.0, 72.0),
                                      (0.0, 194.0, 300.0, 132.0)]
    move_by(two, 1)
    settle(ctx)
    assert [g.name for g in groups_of(deck)] == ["one", "two", "three"]


def test_a_move_survives_being_handed_to_another_panel():
    """Re-parenting keeps the order it was moved to, because the order is the list.

    ``order`` is read off where the widget stands in the list its container arranges
    from, so a group pulled out of one page and put in another keeps its place among
    the children it now has rather than being told to go back to the end.
    """
    ctx = stage()
    deck, (one, two, three) = stack_of_three(ctx)
    other = Widget("panel", {"label": ""}, name="other", parent=ctx.root)
    pad(other, 0.0)
    other.place(*PAGE)
    arrange(other, {"direction": "vertical", "gap": ROW_GAP})
    move_to(three, 0)
    settle(ctx)
    other.add(three)
    settle(ctx)
    assert three.parent is other
    assert [g.name for g in groups_of(other)] == ["three"]
    assert [g.name for g in groups_of(deck)] == ["one", "two"]
    assert order_of(one) == 0 and order_of(two) == 1
    assert boxes((one, two)) == [(0.0, 0.0, 300.0, 72.0),
                                 (0.0, 82.0, 300.0, 102.0)]


# -- the grid, the drop, and the cell they agree on ---------------------


def test_four_groups_in_three_columns_are_a_grid_with_a_cell_left_over():
    """The cell count is the children extended by whatever somebody claimed.

    Three columns and four groups is two rows of cells and six of them, so one is
    free: that leftover is the whole difference between a grid that displays and a
    grid that is a dock, and it is why every drop test here uses four in three.
    """
    ctx = stage()
    deck, (a, b, c, d) = grid_of_four(ctx)
    assert spec_of(deck) == GridSpec(3, 2, CELL_GAP)
    assert deck.get(COLUMNS) == 3.0 and deck.get(ROWS) == 2.0
    assert deck.get(GAP) == CELL_GAP
    assert boxes((a, b, c, d)) == [(0.0, 34.5, 88.0, 72.0),
                                   (106.0, 34.5, 88.0, 72.0),
                                   (212.0, 34.5, 88.0, 72.0),
                                   (0.0, 193.5, 88.0, 72.0)]
    assert [g.get("cell") for g in (a, b, c, d)] == [0.0, 1.0, 2.0, 3.0]
    assert [g.get("order") for g in (a, b, c, d)] == [0.0, 1.0, 2.0, 3.0]
    assert [occupant(deck, i) for i in range(6)] == [a, b, c, d, None, None]
    # the group is centred in its cell vertically because the row owns the height
    # and the cell owns the width: PACK(g, "fill", "fixed") in one picture
    assert a.width == 88.0, "the cell's width, because the group asked to fill it"
    assert a.height == 72.0, "the header and the knob, because the group owns it"


def test_the_band_and_the_mark_are_measured_in_the_cell_they_were_dropped_in():
    """The header box of a group in a cell is the cell's own screen rectangle.

    ``34.5`` is the logical box and the screen box is snapped to the device grid,
    so the two answers differ by the half pixel the pass refuses to round into the
    geometry a script reads. This is the pair a press has to agree on, so both are
    written down.
    """
    ctx = stage()
    _deck, (a, b, c, _d) = grid_of_four(ctx)
    assert a.screen_box == (0.0, 35.0, 88.0, 72.0)
    assert a.box == (0.0, 34.5, 88.0, 72.0)
    assert a.content_box() == (9.0, 44.0, 70.0, 54.0)
    assert header_box(a) == (9.0, 44.0, 70.0, HEADER_HEIGHT)
    assert chevron_box(a) == (61.0, 50.0, 12.0, 12.0)
    assert header_box(b)[0] == 115.0 and header_box(c)[0] == 221.0
    assert chevron_box(c) == (273.0, 50.0, 12.0, 12.0)


def test_the_cell_under_a_point_is_the_cell_the_pass_drew():
    """``index_at`` inverts ``rect_of``, including the gap, which is no answer.

    The cells are not the groups: a cell is ``88`` across and ``141`` tall and the
    group inside it is only as tall as its header and its knob, so the point asked
    about here is the cell's, measured from the cell's own corner rather than from
    the box the group was centred in.
    """
    ctx = stage()
    deck, (a, _b, _c, _d) = grid_of_four(ctx)
    assert deck.content_box() == (0.0, 0.0, 300.0, 300.0)
    assert GridSpec(3, 2, CELL_GAP).size((0.0, 0.0, 300.0, 300.0)) == (88.0, 141.0)
    assert dock_target(a, 250.0, 250.0) == (deck, 5)
    assert dock_target(a, 44.0, 229.5) == (deck, 3)
    assert dock_target(a, 44.0, 44.0) == (deck, 0)
    # the gap between two cells belongs to neither of them
    assert dock_target(a, 94.0, 150.0) == (None, -1)
    assert dock_target(a, 320.0, 320.0) == (None, -1)
    assert dock_target(a, -1.0, 40.0) == (None, -1)


def test_a_header_drag_lands_the_group_in_the_cell_the_pointer_was_over():
    """The gesture end to end: capture, the outlines, the drop, the new cell.

    ``capture`` is the router's answer rather than a widget's, because the band is
    invisible and the group under it is the one with a behaviour: this is the test
    that says the two facts are the same fact on the screen.
    """
    ctx = stage()
    deck, (a, b, c, d) = grid_of_four(ctx)
    head = header_box(a)
    ctx.router.move_to(head[0] + 20.0, head[1] + head[3] / 2.0)
    ctx.router.press()
    assert ctx.router.capture is a
    settle(ctx)
    assert deck.get(DOCKING), "a drag is held, so the cells are worth drawing"
    assert painted(ctx.root).count("polyline") == 6
    ctx.router.move_to(250.0, 250.0)
    ctx.router.release()
    settle(ctx)
    assert a.box == (212.0, 193.5, 88.0, 72.0)
    assert a.get("cell") == 5.0
    assert [g.get("cell") for g in (b, c, d)] == [1.0, 2.0, 3.0]
    assert [g.name for g in groups_of(deck)] == ["b", "c", "d", "a"]
    assert [occupant(deck, i) for i in range(6)] == [None, b, c, d, None, a]
    assert not deck.get(DOCKING), "the hand let go"
    assert painted(ctx.root).count("polyline") == 0


def test_a_group_that_is_not_dockable_cannot_be_picked_up():
    """The press is claimed - the group is what is under the band - and nothing moves.

    There is no refusal to read here: a pointer is not a request, and a hand that
    grabs something that does not come away is told by the thing staying where it
    was. The same situation reached from a script is the one that names the reason.
    """
    ctx = stage()
    deck, (_a, _b, _c, d) = grid_of_four(ctx, dockable=False)
    assert not bool(d.get(DOCKABLE))
    before = d.box
    head = header_box(d)
    ctx.router.move_to(head[0] + 20.0, head[1] + head[3] / 2.0)
    ctx.router.press()
    assert ctx.router.capture is d
    ctx.router.move_to(250.0, 250.0)
    ctx.router.release()
    settle(ctx)
    assert d.box == before
    assert d.get("cell") == 3.0
    with pytest.raises(GroupError) as said:
        dock(d, 5)
    assert "is not dockable" in str(said.value)
    # the fold is still there: what was refused is the carrying, not the box
    mark = chevron_box(d)
    ctx.router.click(mark[0] + mark[2] / 2.0, mark[1] + mark[3] / 2.0)
    settle(ctx)
    assert folded_of(d)


def test_the_cells_are_drawn_only_while_a_drag_is_held_and_only_when_dockable():
    """``dockable`` is the script's row and ``docking`` the frame's; a face needs both.

    A face is handed a widget, a renderer and a skin, and the pointer is none of
    them, so the fact that something is being dragged is written where the painter
    can read it, exactly as the input system writes ``hovered``. An ordinary grid is
    therefore invisible rather than decorated, and - said honestly - ``docking`` is
    true while *any* drag in the window is held, so holding a slider draws the
    cells too. That is why the outline is the cheap rectangle it is.
    """
    ctx = stage()
    deck, (a, _b, _c, _d) = grid_of_four(ctx)
    assert painted(ctx.root).count("polyline") == 0
    head = header_box(a)
    ctx.router.move_to(head[0] + 20.0, head[1] + head[3] / 2.0)
    ctx.router.press()
    settle(ctx)
    assert deck.get(DOCKING)
    held = painted(ctx.root)
    assert held.count("polyline") == 6
    corners = held.ops_named("polyline")[0].get("points")
    assert len(corners) == 4
    ctx.router.release()
    deck.set(DOCKABLE, 0)
    settle(ctx)
    ctx.router.move_to(head[0] + 20.0, head[1] + head[3] / 2.0)
    ctx.router.press()
    settle(ctx)
    assert deck.get(DOCKING), "a drag is still in progress in the window"
    assert painted(ctx.root).count("polyline") == 0, "and this panel draws none"
    ctx.router.release()


def test_undock_gives_up_the_cell_and_the_next_pass_finds_it_another():
    """Only the claim is dropped; where the group *lives* is a different fact.

    The group goes to the end of the list its panel arranges, which for a grid means
    the next free cell in that order - here the one at the far end was promised to
    ``a``, and giving the promise up hands it back.
    """
    ctx = stage()
    deck, (a, b, c, d) = grid_of_four(ctx)
    dock(a, 5)
    settle(ctx)
    assert a.get("cell") == 5.0
    assert a.box == (212.0, 193.5, 88.0, 72.0)
    assert undock(a) is a
    assert a.get("cell") == -1.0, "the row is written at once, the pass agrees next"
    settle(ctx)
    assert [g.get("cell") for g in (b, c, d, a)] == [1.0, 2.0, 3.0, 0.0]
    assert a.box == (0.0, 34.5, 88.0, 72.0)
    assert [g.name for g in groups_of(deck)] == ["b", "c", "d", "a"]


def test_a_declared_grid_has_cells_before_any_frame_has_drawn_them():
    """``GROUP_DOCK`` on the line after a ``GRID`` is not a panel with no cells.

    :func:`~crude.ui.grid.spec_of` answers from the rows the pass measured, which is
    right for a face and for a drop - both work in rectangles, and a rectangle only
    exists once the pass produced it. The *count* of cells is not a rectangle: it is
    the columns the script named and the rows its children fill, so the dock reads
    the declaration rather than refusing with a sentence about a verb the script has
    already called.
    """
    ctx = stage()
    deck = page(ctx)
    held = [section(deck, word, 1) for word in ("a", "b", "c", "d")]
    for there in held:
        there.set(DOCKABLE, 1)
    arrange(deck, {"direction": "grid", "columns": 3, "gap": CELL_GAP})
    a, b, c, d = held
    assert spec_of(deck) is None, "no pass has measured this panel's cells yet"
    assert cells_offered(deck) == 6
    assert dock(d, 5) is deck
    assert assigned(d) == 5
    settle(ctx)
    assert d.box == (212.0, 193.5, 88.0, 72.0)
    assert [g.get("cell") for g in (a, b, c, d)] == [0.0, 1.0, 2.0, 5.0]
    assert [g.name for g in groups_of(deck)] == ["a", "b", "c", "d"]


# -- why a dock was refused ---------------------------------------------


def test_every_refusal_names_the_thing_that_would_answer_it():
    """Four refusals, four different sentences, because there are four different facts.

    "Why didn't my group move" is a question about whether the group may be picked
    up, whether the panel has cells, whether the one asked for is one of them and
    whether it is free. One error that could mean any of them is a bug report a
    person writes instead of a bug they fix, so the sentences are pinned whole.
    """
    ctx = stage()
    deck, (a, _b, _c, d) = grid_of_four(ctx)

    with pytest.raises(GroupError) as said:
        dock(a, 9)
    assert str(said.value) == ("'root/page' has 6 cells, counted from 0, "
                               "and 9 is not one of them")

    with pytest.raises(GroupError) as said:
        dock(a, 1)
    assert str(said.value) == ("cell 1 of 'root/page' already holds "
                               "'root/page/b'; it is a cell, not a stack")

    d.set(DOCKABLE, 0)
    with pytest.raises(GroupError) as said:
        dock(d, 4)
    assert str(said.value) == ("'root/page/d' is not dockable; SET(g, "
                               "\"dockable\", 1) is what lets it be picked up")

    raw = Widget("panel", name="raw")
    with pytest.raises(GroupError) as said:
        dock(a, 0, raw)
    assert str(said.value) == ("'raw' has no cells to dock into; "
                               "GRID(panel, columns, gap, ...) with "
                               "\"dockable\" 1 is what gives a panel some")

    loose = Widget("group", {"title": "free"}, name="free")
    with pytest.raises(GroupError) as said:
        dock(loose, 0)
    assert "is not in a tree" in str(said.value)


def test_a_fold_asks_for_the_kind_that_has_one():
    """A plain panel has no ``folded`` row, and ``fold`` is not going to invent one."""
    ctx = stage()
    deck = page(ctx)
    settle(ctx)
    with pytest.raises(GroupError) as said:
        fold(deck)
    assert str(said.value) == ("a 'panel' has no fold; 'root/page' is not a group, "
                               "so there is nothing here to close")

    firm = section(deck, "firm", rows=1)
    firm.set("foldable", 0)
    settle(ctx)
    assert not foldable_of(firm)
    with pytest.raises(GroupError) as said:
        fold(firm)
    assert str(said.value) == ("'root/page/firm' says it is not foldable; "
                               "SET(g, \"foldable\", 1) gives the mark back")
    mark = chevron_box(firm)
    ctx.router.click(mark[0] + mark[2] / 2.0, mark[1] + mark[3] / 2.0)
    settle(ctx)
    assert not folded_of(firm), "and the mark a click landed on folds nothing"


@pytest.mark.parametrize("row", ("cell", "order"))
def test_the_two_rows_the_pass_answers_for_refuse_to_be_written(row):
    """``SET(g, "cell", 0)`` would be overruled by the next frame without being told.

    The same family as a viewport's ``travel``: the layout measured it, so a script
    that wants a group in cell two docks it, and the verb that docks it is the thing
    that says whether cell two was free.
    """
    ctx = stage()
    _deck, (a, _b, _c, _d) = grid_of_four(ctx)
    assert row in props.MEASURED
    assert a.kind.props[row].readonly
    with pytest.raises(props.PropError) as said:
        a.set(row, 3.0)
    assert f"'group.{row}' is measured by the layout pass" in str(said.value)


# -- what the modules owe each other ------------------------------------


def test_the_names_the_modules_share_are_one_fact_written_once():
    """The kind, the axis table and the measured rows are pinned across modules.

    :mod:`crude.ui.group` cannot import the pass that imports it, so it keeps its own
    copy of three things it needs - the kind's name, which edges are vertical, and
    which rows a pass owns. Each of the three is a promise that a rename in one file
    is a failure in the other rather than a group that quietly stops folding.
    """
    from crude.ui import widget as tree

    assert GROUP_KIND == tree.GROUP_KIND == "group"
    assert GROUP_HEADER_SUFFIX == tree.GROUP_HEADER_SUFFIX == "#header"
    assert GROUP_BODY_SUFFIX == tree.GROUP_BODY_SUFFIX == "#body"
    assert Y_EDGES == tuple(edge for edge, axis in AXIS.items() if axis == "y")
    assert set(props.MEASURED) >= {"cell", "order", "travel", "content"}
    assert "panel" in props.get_kind(GROUP_KIND).chain()
    assert props.get_kind(GROUP_KIND).args == ("title",)
    assert not bool(props.get_kind("panel").has("folded"))
    assert bool(props.get_kind(GROUP_KIND).has("folded"))
    assert bool(props.get_kind(GROUP_KIND).has("accordion")), \
        "the rule is inherited from the panel that holds the groups"


# -- the language ---------------------------------------------------------

#: a page of three columns with four groups in it: the same two rows and six cells
#  the Python fixtures above arrange, built with the seven verbs instead. Every
#  group is empty, so every one is ``42`` tall and the arithmetic is the box's.
BUILD = '\n'.join((
    'VAR deck = PANEL("deck")',
    "PLACE(deck, 0, 0, 300, 300)",
    "PAD(deck, 0)",
    'GRID(deck, 3, 18, ["dockable", 1])',
    'VAR one = GROUP("carrier", deck)',
    'VAR two = GROUP("modulator", deck)',
    'VAR three = GROUP("filter", deck)',
    'VAR four = GROUP("out", deck)',
    'PACK(one, "fill", "fixed")',
    'PACK(two, "fill", "fixed")',
    'PACK(three, "fill", "fixed")',
    'PACK(four, "fill", "fixed")',
    'SET(one, "dockable", 1)',
    'SET(two, "dockable", 1)',
    'SET(three, "dockable", 1)',
    'SET(four, "dockable", 1)',
))


@pytest.fixture
def scene():
    """A program said into the window every fixture above already uses."""
    held = stage()
    return Program("<groups-test>",
                   host=types.SimpleNamespace(ui=held)), held


@pytest.fixture
def deck(scene):
    """:data:`BUILD` run, laid out, and answered as the panel it made."""
    program, ctx = scene
    run(program, BUILD)
    built = handle(run(program, "deck"))
    settle(ctx)
    return program, ctx, built


def as_py(value):
    """A language value as the plain Python object it stands for."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, List):
        return [as_py(item) for item in value.elements]
    if isinstance(value, HostHandle):
        return value.target
    return value


def handle(value):
    assert isinstance(value, HostHandle), f"expected a handle, got {value!r}"
    assert value.kind == "widget", value.kind
    return value.target


def run(program, source):
    value, error = program.run(source)
    assert error is None, error.as_string()
    return value


def say(program, source):
    return as_py(run(program, source))


def failure(program, source):
    """Run one line that has to fail and hand back the sentence it answered."""
    value, error = program.run(source)
    assert error is not None, f"expected a failure, got {value!r}"
    return error.as_string()


# -- the verbs answer the number a person would check ---------------------


def test_group_builds_the_box_and_the_parent_argument_says_where_it_lives(deck):
    """``GROUP(title, parent)`` is sugar over ``WIDGET("group")`` plus two panels.

    The title doubles as the name, which is how ``carrier`` ends up in the path and
    in the header word from one argument. The script never names ``#header`` or
    ``#body`` and never has to dodge them: anything added to the group arrives in
    the body, which is the promise the Python fixture above makes the same way.
    """
    program, _ctx, built = deck
    assert built.kind.name == "panel"
    assert [g.name for g in groups_of(built)] == ["carrier", "modulator",
                                                  "filter", "out"]
    carrier = handle(run(program, "one"))
    assert carrier.path() == "root/deck/carrier"
    assert carrier.kind.name == "group"
    assert say(program, 'GET(one, "title")') == "carrier"
    assert [kid.name for kid in carrier.children] == ["carrier#header",
                                                      "carrier#body"]
    assert body_of(carrier).visible
    assert not header_of(carrier).visible


def test_a_group_added_to_a_group_lands_in_its_body_and_is_measured_there(deck):
    """``NODE_ADD`` hands the child to the body, and the parent grows around it.

    The height is the point: an empty group is ``42``, and the same group holding a
    group that is itself ``42`` is ``84`` - the fold rule and the arrangement rule
    are the one measurement, read through the language on a widget the script never
    placed anywhere. The width stays ``0`` because nothing told this one to fill,
    which is the same fact the cell test below makes.
    """
    program, ctx, built = deck
    carrier = handle(run(program, "one"))
    assert carrier.height == FOLDED_HEIGHT
    run(program, 'VAR late = GROUP("late")\nNODE_ADD(one, late)')
    late = handle(run(program, "late"))
    assert is_group(late)
    assert late.path() == "root/deck/carrier/carrier#body/late"
    assert [kid.name for kid in carrier.children] == ["carrier#header",
                                                      "carrier#body"]
    assert [kid.name for kid in body_of(carrier).children] == ["late"]
    settle(ctx)
    assert late.box == (0.0, 0.0, 0.0, FOLDED_HEIGHT)
    assert carrier.box == (0.0, 28.5, 88.0, 84.0)
    assert carrier.get("cell") == 0.0
    assert [g.name for g in groups_of(built)] == ["carrier", "modulator",
                                                 "filter", "out"]
    assert [g.name for g in groups_of(carrier)] == ["late"]


def test_the_fold_verb_answers_the_state_it_left_the_group_in(deck):
    """``GROUP_FOLD(g)`` is the click and ``GROUP_FOLD(g, 1)`` is the saying.

    Both answer the row rather than a success flag, because the row is the thing a
    script has to agree with: a fold that answered ``True`` both times would tell a
    menu nothing about whether its own caption should now read Close or Open.
    """
    program, ctx, _built = deck
    assert say(program, "GROUP_FOLD(two)") == 1.0
    settle(ctx)
    assert say(program, 'GET(two, "folded")') == 1.0
    assert handle(run(program, "two")).box == (106.0, 49.5, 88.0, 42.0)
    assert say(program, "GROUP_FOLD(two)") == 0.0
    assert say(program, 'GROUP_FOLD(two, 1)') == 1.0
    assert say(program, 'GROUP_FOLD(two, 1)') == 1.0, "already folded, still 1"
    assert say(program, 'GROUP_FOLD(two, 0)') == 0.0
    settle(ctx)


def test_the_order_verbs_answer_the_place_they_took_and_the_cells_follow(deck):
    """``GROUP_MOVE`` clamps to the end and answers that end, not the number given.

    A script restoring a saved layout has no idea how many groups a page holds, so
    asking for nine is not an error - but the answer has to be the three it became,
    the same number ``GET(g, "order")`` reads back.
    """
    program, ctx, built = deck
    assert say(program, "GROUP_MOVE(four, 9)") == 3.0
    assert say(program, "GROUP_UP(four)") == 2.0
    assert say(program, "GROUP_DOWN(four)") == 3.0
    assert [g.name for g in groups_of(built)] == ["carrier", "modulator",
                                                 "filter", "out"]
    assert say(program, "GROUP_MOVE(one, 2)") == 2.0
    settle(ctx)
    assert [g.name for g in groups_of(built)] == ["modulator", "filter",
                                                 "carrier", "out"]
    assert [g.get("cell") for g in groups_of(built)] == [0.0, 1.0, 2.0, 3.0]
    assert say(program, "GROUPS(one)") == []
    assert say(program, "GROUP_UP(one)") == 1.0


def test_docking_a_group_from_a_script_is_the_same_move_the_pointer_makes(deck):
    """``GROUP_DOCK`` writes the cell claim the drop writes, and the pass draws it.

    The cell ``5`` is the leftover of six, so the docked group ends up in the far
    corner of the second row rather than in the first free cell its place in the
    list would have put it in.
    """
    program, ctx, built = deck
    assert say(program, "GROUP_DOCK(four, 5)") == 5.0
    assert say(program, 'GET(four, "cell")') == 5.0
    settle(ctx)
    out = handle(run(program, "four"))
    assert out.box == (212.0, 208.5, 88.0, 42.0), "the cell the drop said"
    assert [g.name for g in groups_of(built)] == ["carrier", "modulator",
                                                 "filter", "out"]
    assert [g.get("cell") for g in groups_of(built)] == [0.0, 1.0, 2.0, 5.0]
    assert say(program, "GROUP_UNDOCK(four)") == -1.0
    assert out.get("cell") == -1.0, "the row is written at once"
    settle(ctx)
    # the claim is gone, so the pass puts the group in the last cell of the order
    # it stands in - the end of the list, which is cell three of six
    assert out.get("cell") == 3.0
    assert out.box == (0.0, 208.5, 88.0, 42.0)
    assert [g.get("cell") for g in groups_of(built)] == [0.0, 1.0, 2.0, 3.0]


def test_the_layout_the_verbs_asked_for_is_the_layout_the_pass_produced(deck):
    """Four empty groups in three columns: ``42`` tall, centred in ``141`` of cell.

    The numbers are the Python fixture's, reached through the language only: the
    verbs are one call each into the same engine, and a second geometry here would
    be a second implementation nobody would keep in step.
    """
    _program, ctx, built = deck
    assert boxes(groups_of(built)) == [(0.0, 49.5, 88.0, 42.0),
                                       (106.0, 49.5, 88.0, 42.0),
                                       (212.0, 49.5, 88.0, 42.0),
                                       (0.0, 208.5, 88.0, 42.0)]
    assert built.get(COLUMNS) == 3.0 and built.get(ROWS) == 2.0
    assert [g.get("order") for g in groups_of(built)] == [0.0, 1.0, 2.0, 3.0]


def test_a_group_with_no_width_of_its_own_is_drawn_at_no_width_in_the_cell(deck):
    """The dockable recipe, proved by the scene that skips it.

    A grid cell gives a ``fixed`` child only the size it already has, so a group
    that was never told ``PACK(g, "fill", "fixed")`` is drawn 0 px wide. That is the
    grid's ordinary rule rather than a group's, and it is here so the recipe above
    is a documented answer instead of a fixture nobody may question.
    """
    program, ctx, built = deck
    assert say(program, 'VAR sub = WIDGET_DEFINE("section", "group")') \
        == "section"
    made = handle(run(program, 'VAR made = WIDGET("section", "voice")'))
    assert made.path() == "root/voice", "the second argument is the name"
    run(program, "NODE_ADD(deck, made)")
    settle(ctx)
    assert made.kind.name == "section"
    assert is_group(made), "a variant of a group is a group"
    assert made.path() == "root/deck/voice"
    assert made.box == (150.0, 208.5, 0.0, 42.0)
    assert made.get("cell") == 4.0
    assert [g.name for g in groups_of(built)] == ["carrier", "modulator",
                                                 "filter", "out", "voice"]
    assert say(program, 'WIDGET_HAS(made, "cell")') == 1
    assert say(program, 'WIDGET_HAS(made, "folded")') == 1
    assert say(program, 'WIDGET_HAS(deck, "folded")') == 0
    # the fold reaches the variant through the base, so a project's own name for a
    # section is a section rather than a picture of one
    assert say(program, "GROUP_FOLD(made)") == 1.0
    settle(ctx)
    assert made.box == (150.0, 208.5, 0.0, 42.0)


def test_every_group_verb_that_refuses_says_what_it_refused(deck):
    """The module's own sentences, with the verb that was answered in front.

    A runtime error in a ``.crude`` file reports a line and not a call, so the
    bindings prefix the word that failed - and the rest of the sentence is raised by
    :mod:`crude.ui.group`, which means a script's refusal and the paper refusal above
    are one text rather than two that can drift.
    """
    program, _ctx, _built = deck
    run(program, 'SET(one, "dockable", 0)')
    said = failure(program, "GROUP_DOCK(one, 5)")
    assert "Runtime Error: GROUP_DOCK: " in said
    assert ("'root/deck/carrier' is not dockable; SET(g, \"dockable\", 1) "
            "is what lets it be picked up") in said

    said = failure(program, "GROUP_DOCK(two, 9)")
    assert "Runtime Error: GROUP_DOCK: " in said
    assert "'root/deck' has 6 cells, counted from 0, and 9 is not one of them" \
        in said

    said = failure(program, 'VAR raw = PANEL("raw")\nGROUP_DOCK(two, 0, raw)')
    assert ("'root/raw' has no cells to dock into; GRID(panel, columns, gap, ...) "
            "with \"dockable\" 1 is what gives a panel some") in said

    # a cell is one group's, and the second ask names the group that already has it
    run(program, 'SET(one, "dockable", 1)')
    assert say(program, "GROUP_DOCK(one, 5)") == 5.0
    said = failure(program, "GROUP_DOCK(two, 5)")
    assert ("cell 5 of 'root/deck' already holds 'root/deck/carrier'; "
            "it is a cell, not a stack") in said

    said = failure(program, "GROUP_FOLD(deck)")
    assert "Runtime Error: GROUP_FOLD: " in said
    assert "argument 'group' must be a group handle, and 'root/deck' is a 'panel'" \
        in said

    said = failure(program, "GROUP_UP(deck)")
    assert "Runtime Error: GROUP_UP: " in said

    said = failure(program, "GROUP_MOVE(two, 1.5)")
    assert "'position' counts things, so it has to be a whole number, not 1.5" \
        in said

    said = failure(program, 'SET(two, "cell", 0)')
    assert "'group.cell' is measured by the layout pass, not written by a script" \
        in said

    said = failure(program, "GROUPS(one, two)")
    assert "too many args passed into '<built-in function GROUPS>'" in said, said
