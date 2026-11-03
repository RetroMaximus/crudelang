"""The cells of a grid: one spec, so the arranger and a drop cannot disagree.

:mod:`crude.ui.grid` exists because :mod:`crude.ui.layout` already knew how to
divide a panel into cells and a group dragged into a cell has to land on
*exactly* the rectangle the arranger would have given it. Two copies of one
formula is how those two facts drift apart, so the formula is here once and the
pass calls into it - which makes this file's central assertion the one the plan
asks for: after a real :func:`~crude.ui.layout.resolve`, the box a child holds is
the box ``GridSpec.rect_of`` answers for it, and the point a hand is over is the
cell that box owns.

The rest is the promise the rows make. ``grid_columns``, ``grid_rows`` and
``grid_gap`` are written by the pass and are read-only for a script, exactly as a
viewport's ``travel`` is, and they go back to zero when the arrangement is undone -
a row that kept describing a grid the script had already removed would be a face
drawing cells that are not there. ``dockable`` is the one row a script does write
and ``docking`` is the fact a painter cannot see for itself, so the outline of a
cell appears only while a drag is in progress: an ordinary grid is invisible
rather than decorated.

The arithmetic is pinned as arithmetic. Two columns and two rows with a ten pixel
gap in a ``110 x 60`` box are two fifty-wide, twenty-five-high cells at ``0`` and
``60`` across and ``0`` and ``35`` down, and every one of those numbers is written
down here rather than computed a second time by the test.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from crude.ui import (Panel, RecordingRenderer, Skin, Widget, anchor, face_for,
                      grid, pack, pad, resolve, root_panel, unstack)
from crude.ui.grid import (COLUMNS, DOCKABLE, DOCKING, GAP, GridError,
                           GridSpec, MEASURED, ROWS, auto_columns, cells,
                           record, rows_for, spec_of)
from crude.ui.grid import reconcile as reconcile_docking
from crude.ui.props import MEASURED as TABLE_MEASURED
from crude.ui.props import PropError

#: the module every name above came out of. ``import crude.ui.grid as module`` does
#  **not** give this: the package attribute of that name is the layout verb, which is
#  what the root re-exports, and the last test in the file pins that. The module is
#  what :func:`importlib.import_module` answers - it asks ``sys.modules`` rather than
#  the attribute - so that is how it is held here.
grid_of_cells = importlib.import_module("crude.ui.grid")

#: a box whose every division by two is a whole number once the gaps are out
WIDE, HIGH = 110.0, 60.0

#: the gap the pure-arithmetic tests use, so a cell is (110 - 10) / 2 across
STEP = 10.0

#: the gap ``"pad.md"`` measures at the ``md`` size, which the pass resolves
TOKEN_GAP = 8.0


@pytest.fixture
def skin() -> Skin:
    return Skin("dark")


@pytest.fixture
def root(skin) -> Widget:
    """The plan's 360 x 220 client rect, resolved once so its box is real."""
    held = root_panel(360, 220)
    held.sync(skin)
    return held


def deck(parent: Widget, name: str = "deck", box=(0, 0, WIDE, HIGH),
         kids: int = 0, columns: Any = None, gap: Any = STEP) -> Widget:
    """A panel with ``kids`` children in it, arranged as a grid if asked to.

    Each child is packed ``fill`` so it takes its whole cell and the box the pass
    wrote can be compared with the cell a drop would read, with nothing sitting in
    between. The padding is written as zero rather than left at the skin's because
    every number here is about the cells, not about the inset around them; ``pad``
    still reads the *content* box, which is the space the pass tiles, the space a
    face outlines and the space a gesture reads a pointer out of.
    """
    held = Panel({"label": name}, name=name, parent=parent)
    pad(held, 0.0)
    held.place(*box)
    for index in range(kids):
        kid = Widget("button", {"label": f"k{index}"}, name=f"k{index}",
                     parent=held)
        kid.place(0, 0, 10, 10)
        pack(kid, "fill")
    if columns is not None:
        grid(held, columns, gap=gap)
    return held


# -- the spec itself ------------------------------------------------------


def test_the_cells_are_counted_from_the_top_left_in_row_order():
    """``cell`` is the index as ``(column, row)``, and nothing else is."""
    held = GridSpec(3, 2, STEP)
    assert held.count == held.cells == 6
    assert [held.cell(index) for index in range(6)] == [
        (0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]
    with pytest.raises(GridError) as raised:
        held.cell(6)
    assert "has no cell 6; it has 6, counted from 0" in str(raised.value)
    with pytest.raises(GridError):
        held.cell(-1)


def test_the_cells_tile_the_box_with_every_gap_between_them():
    """Two columns and two rows of a ``110 x 60`` box less a ``10`` gap.

    The far edges are asserted as well as the near ones: a grid that left a strip
    of panel unpainted at the bottom, or ran past the box on the right, would still
    give every cell the same shape, which is the only thing a naive formula gets
    right.
    """
    held = GridSpec(2, 2, STEP)
    box = (0.0, 0.0, WIDE, HIGH)
    assert held.size(box) == (50.0, 25.0)
    assert held.every(box) == [
        (0.0, 0.0, 50.0, 25.0), (60.0, 0.0, 50.0, 25.0),
        (0.0, 35.0, 50.0, 25.0), (60.0, 35.0, 50.0, 25.0)]
    far = held.every(box)[-1]
    assert far[0] + far[2] == WIDE and far[1] + far[3] == HIGH
    # the box's own origin is carried through: a face outlines screen pixels
    assert held.rect_of(0, (16.0, 24.0, WIDE, HIGH)) == (16.0, 24.0, 50.0, 25.0)


def test_a_gap_is_a_gap_and_not_somewhere_to_drop():
    """``index_at`` is ``rect_of`` read backwards, and says ``None`` in between.

    The refusal matters more than the hit: the space between two cells belongs to
    neither, and a drop that snapped to the nearest one would move a group the
    person never let go of inside.
    """
    held = GridSpec(2, 2, STEP)
    box = (0.0, 0.0, WIDE, HIGH)
    for index, rect in enumerate(held.every(box)):
        middle = (rect[0] + rect[2] * 0.5, rect[1] + rect[3] * 0.5)
        assert held.index_at(*middle, box) == index
        # every cell answers for its own two corners, inside to the last pixel
        assert held.index_at(rect[0], rect[1], box) == index
        assert held.index_at(rect[0] + rect[2] - 0.01,
                             rect[1] + rect[3] - 0.01, box) == index
    assert held.index_at(55.0, 12.0, box) is None, "the vertical gap"
    assert held.index_at(25.0, 32.0, box) is None, "the horizontal gap"
    assert held.index_at(-1.0, 12.0, box) is None
    assert held.index_at(WIDE, 12.0, box) is None, "the far edge is not inside"
    assert held.index_at(25.0, HIGH + 5.0, box) is None


def test_a_grid_bigger_than_its_children_keeps_the_rest_as_real_cells():
    """Three children in two columns are a ``2 x 2`` grid with one empty cell.

    The empty cell is the half a dock needs and an arranger never uses: it has a
    rectangle, it answers ``index_at``, and a group can be dropped into it.
    """
    held = GridSpec.of(3, columns=2, gap=0.0)
    assert (held.columns, held.rows, held.count) == (2, 2, 4)
    box = (0.0, 0.0, WIDE, HIGH)
    assert held.size(box) == (WIDE / 2.0, HIGH / 2.0)
    assert held.every(box)[3] == (55.0, 30.0, 55.0, 30.0)
    assert held.index_at(82.0, 45.0, box) == 3, "the cell nothing is in"
    # columns are clamped to the children there are, and a dock laid out ahead of
    # its content names the cells it promises instead of asking for them
    assert GridSpec.of(2, columns=3).columns == 2
    assert GridSpec(3, 2, STEP).count == 6


@pytest.mark.parametrize("count, named, wide, deep", [
    (9, 0, 3, 3), (5, 0, 3, 2), (4, 0, 2, 2), (2, 0, 2, 1), (1, 0, 1, 1),
    (0, 0, 1, 0), (4, 10, 4, 1), (4, 3, 3, 2), (0, 5, 1, 0), (6, 2, 2, 3),
])
def test_the_columns_are_what_the_script_named_or_as_square_as_they_can_be(
        count, named, wide, deep):
    """``auto_columns`` never promises more columns than there are children."""
    assert auto_columns(count, named) == wide
    assert rows_for(count, wide) == deep


@pytest.mark.parametrize("bad", [0, -1, 1.5])
def test_a_grid_has_a_whole_number_of_cells_in_each_direction(bad):
    """A fractional column count is a divide nobody asked for, said before it is."""
    with pytest.raises(GridError):
        GridSpec(bad, 2)
    with pytest.raises(GridError) as raised:
        GridSpec(2, bad)
    assert "'rows' has to be a whole number" in str(raised.value)


def test_a_gap_cannot_be_negative():
    with pytest.raises(GridError) as raised:
        GridSpec(2, 2, -1.0)
    assert "cannot be negative" in str(raised.value)


# -- the rows a panel answers with ----------------------------------------


def test_record_and_spec_of_are_the_same_fact_written_and_read(root):
    """The rows are the bridge: written by whoever arranged, read by whoever asks."""
    held = deck(root, kids=4, columns=2)
    assert (held.get(COLUMNS), held.get(ROWS), held.get(GAP)) == (0, 0, 0)
    assert spec_of(root) is None, "a panel that arranged nothing has no answer"
    assert record(held, GridSpec(3, 2, STEP)) is True
    assert (float(held.get(COLUMNS)), float(held.get(ROWS)),
            float(held.get(GAP))) == (3.0, 2.0, STEP)
    assert spec_of(held) == GridSpec(3, 2, STEP)
    assert len(cells(held)) == 6
    # and writing zero back is how the pass reports "no grid here any more"
    assert record(held, None) is True
    assert spec_of(held) is None
    assert cells(held) == ()


def test_a_leaf_has_no_cells_to_answer_for(root):
    """A ``button`` holds no grid rows, so the pass's report is refused, not zeroed.

    ``record`` is called on whatever the walk reached; a panel that does not carry
    the rows is not an error, it is a widget the question was never about.
    """
    held = Widget("button", {"label": "tap"}, name="tap", parent=root)
    held.place(0, 0, 60, 20)
    assert record(held, GridSpec(2, 2, STEP)) is False
    assert spec_of(held) is None
    assert cells(held) == ()


def test_an_unchanged_measurement_reports_nothing_moved(root, skin):
    """A measurement that did not move is not a change, and returns ``False``.

    This is what keeps an idle window idle: the pass runs every frame and would
    otherwise claim a repaint it did not cause.
    """
    held = deck(root, kids=4, columns=2)
    resolve(root, skin)
    assert record(held, spec_of(held)) is False


def test_the_measured_rows_are_not_a_scripts_to_write(root):
    """Four rows the pass owns, one the script owns, and the table is the referee."""
    held = deck(root, kids=1, columns=1)
    for word in MEASURED:
        assert held.kind.props[word].readonly is True, word
        with pytest.raises(PropError):
            held.set(word, 3.0)
        with pytest.raises(PropError) as raised:
            held.kind.settable(word)
        assert "measured by the layout pass" in str(raised.value), word
    assert held.set(DOCKABLE, 1) is True, "this one is theirs to write"
    assert held.kind.settable(DOCKABLE) is held.kind.props[DOCKABLE]


def test_the_names_this_module_writes_are_rows_the_table_declares():
    """The module and the prop table cannot drift apart on a measurement's name.

    ``MEASURED`` here is four of the rows :data:`crude.ui.props.MEASURED` lists as
    the family a pass owns rather than the pointer; a row renamed in one of the two
    places would leave the other still asserting a fact nothing writes.
    """
    assert set(MEASURED) <= set(TABLE_MEASURED)
    assert DOCKABLE not in TABLE_MEASURED
    assert (COLUMNS, ROWS, GAP, DOCKING) == MEASURED


# -- the pass and the spec agree ------------------------------------------


def test_the_box_a_child_holds_is_the_box_its_cell_was_measured_as(root, skin):
    """The plan's acceptance line: one formula, two callers, no disagreement.

    Four children packed ``fill`` in a grid of two columns take their cells
    exactly, so the rectangle the pass wrote and the rectangle a drop would read
    are the same four tuples - not two arithmetic that happen to agree today.
    """
    held = deck(root, kids=4, columns=2, box=(16, 16, 200, 100))
    resolve(root, skin)
    spec = spec_of(held)
    assert spec == GridSpec(2, 2, STEP)
    inner = held.content_box()
    assert inner == pytest.approx((16.0, 16.0, 200.0, 100.0)), "no inset was said"
    assert spec.every(inner) == [
        (16.0, 16.0, 95.0, 45.0), (121.0, 16.0, 95.0, 45.0),
        (16.0, 71.0, 95.0, 45.0), (121.0, 71.0, 95.0, 45.0)]
    for index, kid in enumerate(held.children):
        assert kid.screen_box == pytest.approx(spec.rect_of(index, inner)), index
    # the gap is measured from the skin when the script names a token, and the row
    # holds the pixels that were used rather than the word that asked for them
    tokened = deck(root, name="tokens", kids=4, columns=2, gap="pad.md",
                   box=(0, 130, 200, 60))
    resolve(root, skin)
    assert float(tokened.get(GAP)) == TOKEN_GAP


def test_an_auto_grid_arranged_by_the_pass_leaves_no_row_undecided(root, skin):
    """Five children are three columns and two rows, the sixth cell still there."""
    held = deck(root, kids=5, columns=0, box=(16, 16, 200, 100))
    resolve(root, skin)
    spec = spec_of(held)
    assert (spec.columns, spec.rows, spec.count) == (3, 2, 6)
    assert len(held.children) == 5, "the pass arranged what was there"
    assert spec.every(held.content_box())[5][2] > 0.0, "a real sixth cell"


def test_undoing_the_arrangement_answers_zero_rows(root, skin):
    """A panel that stopped arranging has to stop describing the old cells.

    ``record(None)`` is the whole of it, but it is the half a face would otherwise
    never learn: an outline of a grid that is no longer there is a dock offering
    cells nothing measures.
    """
    held = deck(root, kids=4, columns=2)
    resolve(root, skin)
    assert spec_of(held) is not None
    assert unstack(held) is True
    resolve(root, skin)
    assert (float(held.get(COLUMNS)), float(held.get(ROWS)),
            float(held.get(GAP))) == (0.0, 0.0, 0.0)
    assert spec_of(held) is None


def test_a_child_glued_on_an_axis_is_placed_by_its_glue_and_not_by_its_cell(
        root, skin):
    """The pass's own rule, seen through a grid: an anchor is the last word.

    A child in a cell that is glued to its panel's right edge keeps the height the
    cell gave - the axis nobody glued - and takes its place from the glue, which is
    the one combination of the two engines a script is most likely to write by
    accident and must not get a fight out of.
    """
    held = deck(root, kids=4, columns=2, box=(16, 16, 200, 100))
    resolve(root, skin)
    kid = held.children[0]
    anchor(kid, "right", held, "right", -4.0)
    resolve(root, skin)
    cell = spec_of(held).rect_of(0, held.content_box())
    assert kid.height == pytest.approx(cell[3]), "nobody glued the y axis"
    # the glue reads the panel's content edge, not the cell's: the left-hand
    # column's cell ends short of it, so the child has left its own cell behind
    assert kid.screen_box[0] + kid.screen_box[2] == pytest.approx(
        held.content_box()[0] + held.content_box()[2] - 4.0)
    assert kid.screen_box != pytest.approx(cell), "the cell lost that argument"


# -- the drawing ---------------------------------------------------------


def paint(held: Widget, skin: Skin) -> RecordingRenderer:
    """The panel and everything under it, into a recorder that keeps every op."""
    held.root().sync(skin)
    drawn = RecordingRenderer()
    drawn.begin(360, 220, 360, 220)
    held.paint(drawn, skin)
    return drawn


def test_a_dockable_panel_outlines_its_cells_only_while_something_is_dragged(
        root, skin):
    """Idle draws nothing extra; a drag draws one outline per cell.

    The count is a difference rather than a total because the plate and the label
    draw their own ops, and the promise is only about the outlines.
    """
    held = deck(root, kids=4, columns=2, box=(16, 16, 200, 100))
    resolve(root, skin)
    assert held.set(DOCKABLE, 1) is True
    quiet = paint(held, skin)
    reconcile_docking(root, dragging=True)
    shown = paint(held, skin)
    idle = len(quiet.ops_named("polyline"))
    marks = shown.ops_named("polyline")
    assert len(marks) - idle == spec_of(held).count
    outlines = marks[idle:]
    assert all(op.clip is not None and op.clip[2] > 0.0 for op in outlines)
    # the outlines are the cells themselves: each one's top-left corner is the
    # rectangle the pass measured, in the panel's own pixels
    corners = [op.get("points")[0] for op in outlines]
    assert corners == [
        (16.0, 16.0), (121.0, 16.0), (16.0, 71.0), (121.0, 71.0)]


def test_an_ordinary_grid_is_invisible_rather_than_decorated(root, skin):
    """``dockable`` off keeps the cells measured and draws nothing on them."""
    held = deck(root, kids=4, columns=2)
    resolve(root, skin)
    reconcile_docking(root, dragging=True)
    before = paint(held, skin)
    held.set(DOCKABLE, 1)
    after = paint(held, skin)
    assert len(after.ops_named("polyline")) - len(
        before.ops_named("polyline")) == spec_of(held).count
    held.set(DOCKABLE, 0)
    assert paint(held, skin).count("polyline") == before.count("polyline")
    assert spec_of(held) is not None, "measured, and still not showing it"


def test_the_docking_row_is_the_only_thing_a_face_needs_to_change_its_mind(
        root, skin):
    """The frame writes the fact; the paint key follows it, and nobody is told.

    A face is handed a widget, a renderer and a skin, and the pointer dragging a
    group across this panel belongs to none of them - so :func:`reconcile` says so
    on the panel, exactly as the input system says ``hovered``.
    """
    held = deck(root, kids=4, columns=2)
    resolve(root, skin)
    held.set(DOCKABLE, 1)
    held.take_changes()  # the script's own write is the one change it owes
    assert bool(held.get(DOCKING)) is False
    # the window's root is a panel too, so the answer moves two widgets and no more
    assert reconcile_docking(root, dragging=True) == 2
    assert bool(held.get(DOCKING)) is True
    assert reconcile_docking(root, dragging=True) == 0, "and not twice"
    # a measurement reports through no ON_CHANGE, the way every row here works
    assert held.changed == []
    assert reconcile_docking(root, dragging=False) == 2, "both answers came back"


def test_a_panel_that_gained_a_face_still_draws_its_own_words(root, skin):
    """Registering a face replaces ``base_face``'s label call, so it makes it itself.

    A panel that lost its caption when it gained the ability to be a dock would be
    a regression dressed up as a feature, and it is the one thing here that a
    difference in op counts would not catch.
    """
    assert face_for("panel") is not None
    held = deck(root, kids=0)
    resolve(root, skin)
    assert "deck" in paint(held, skin).texts()


# -- the module stands alone --------------------------------------------


def test_the_grid_module_needs_the_layout_engine_for_nothing():
    """``grid`` is a box and nothing else, so anything may ask it a question.

    The pass imports this module, never the other way round: knowing a cell's
    rectangle needs no policy, no skin and no ordering, while arranging children
    needs all three. Spelled out of the source rather than trusted from memory,
    because an import that is not there cannot be checked by running it.
    """
    source = Path(str(grid_of_cells.__file__)).read_text(encoding="utf-8")
    assert "from .layout" not in source
    assert "import layout" not in source
    assert all(name in grid_of_cells.__all__ for name in
               ("GridSpec", "record", "spec_of", "cells", "reconcile",
                "MEASURED", "DOCKABLE"))


def test_the_package_says_which_of_the_two_names_it_means():
    """``crude.ui.grid`` is the layout verb; the module of that name is a submodule.

    Both are real and the package attribute can only hold one of them, so the answer
    is written down rather than discovered by a future author the hard way: a name
    imported out of :mod:`crude.ui.grid` is the module's, and a word read off the
    package is the verb the pass exports. Nothing in the tree may rely on the second
    one meaning the first.
    """
    import types

    import crude.ui

    assert isinstance(grid_of_cells, types.ModuleType)
    assert grid_of_cells.__name__ == "crude.ui.grid"
    assert getattr(crude.ui, "grid") is grid
    assert grid.__module__ == "crude.ui.layout"
