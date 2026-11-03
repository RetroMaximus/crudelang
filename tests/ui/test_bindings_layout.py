"""The placement pass, reachable from a ``.crude`` file.

:mod:`crude.ui.layout` has known anchors, pack policies, stacks and cells since the
first widget drew anywhere; until this stage ``PLACE`` was the only one of them a
script could say. The eight verbs here are therefore wrappers rather than a second
engine - one call each, with the words handed to the same ``as_edge`` / ``as_policy``
/ ``as_alignment`` doors the Python callers go through, so a bad word in a script
fails with the same sentence a paper test already asserts on.

What this file is actually about is the two promises that only become visible from
the language:

* a verb's answer is the same fact a ``GET`` reads back. ``ANCHOR`` hands over the
  sentence it was given and ``GET(w, "place")`` reads the list those sentences live
  in; ``PACK`` answers its two policies and ``GET(w, "pack")`` answers the same two.
  A script that cannot check what it just told the layout to do has one half of a
  layout and no way to read the other.
* an anchor is the last word, on the second frame as well as the first. The tests
  below arrange a row, glue one of its children, lay the tree out twice and compare
  the boxes, because "the glue wins" that is only true until the next pass runs is a
  widget that jitters and a paint key that never settles.

The geometry is read off the widgets themselves after a real
:meth:`~crude.ui.context.UIContext.layout` - the same call a frame makes - and the
numbers are written down rather than recomputed by the test. Every panel used here
has its padding written to ``0`` first, so a coordinate means the arrangement and
not an inset a reader would have to look up in the skin.
"""

from __future__ import annotations

import types

import pytest

import crude.bindings  # noqa: F401 - registers every area, including ui
from crude.lang import Program
from crude.lang.values import HostHandle, List, Number, String
from crude.ui import RecordingRenderer, UIContext
from crude.ui.grid import GridSpec, spec_of

# -- helpers ------------------------------------------------------------


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


@pytest.fixture
def stage():
    """A program wired to a fresh UIContext over a RecordingRenderer."""
    renderer = RecordingRenderer()
    ctx = UIContext(renderer, width=320, height=200)
    program = Program("<layout-test>", host=types.SimpleNamespace(ui=ctx))
    return program, ctx, renderer


@pytest.fixture
def run(stage):
    program, _, _ = stage

    def _run(source):
        value, error = program.run(source)
        assert error is None, error.as_string()
        return value

    return _run


@pytest.fixture
def eval_ui(run):
    def _ev(source):
        return as_py(run(source))

    return _ev


@pytest.fixture
def failure(stage):
    program, _, _ = stage

    def _fail(source):
        value, error = program.run(source)
        assert error is not None, f"expected a failure, got {value!r}"
        return error.as_string()

    return _fail


#: the names :func:`deck` gives its children, in the order it adds them
WORDS = ("a", "b", "c", "d", "e", "f")


def deck(run, kids=0, *, box=(0, 0, 200, 120), size=(30, 20)):
    """A panel called ``box`` with ``kids`` equal buttons added to it in order.

    The panel's padding is written as zero rather than left at the skin's, so every
    number the tests below pin is about the cells and the row and not about an inset
    that would have to be measured out of the theme first. The children are sized
    before they are handed to the panel because that is the order a person writes:
    make it, size it, put it in.

    Returns the panel and the children, in z-order, which is the order a stack reads.
    """
    run(f'VAR box = PANEL("box")\n'
        f"PLACE(box, {box[0]}, {box[1]}, {box[2]}, {box[3]})\n"
        "PAD(box, 0)\n")
    held = handle(run("box"))
    found = []
    for index in range(kids):
        word = WORDS[index]
        run(f'VAR {word} = BUTTON("{word}")\n'
            f"PLACE({word}, 0, 0, {size[0]}, {size[1]})\n"
            "NODE_ADD(box, " + word + ")\n")
        found.append(handle(run(word)))
    return held, found


def boxes(every):
    """The boxes a list of widgets currently holds, as plain tuples."""
    return [held.screen_box for held in every]


# -- ANCHOR, and GET(w, "place") reading it back ------------------------


def test_a_glue_is_answered_as_the_sentence_it_was_written_in(run, eval_ui):
    """The verb and the reader say the same words, so a script can check itself."""
    box, (a,) = deck(run, kids=1)
    assert eval_ui('ANCHOR(a, "right", box, "right", -8)') \
        == "right -> root/box.right -8"
    assert eval_ui('GET(a, "place")') == ["right -> root/box.right -8"]


def test_a_glue_with_no_target_names_the_parent_it_will_have(run, eval_ui, stage):
    """``ANCHOR(w, "bottom")`` is a margin from *whatever panel owns this now*.

    The target is resolved on the frame rather than remembered from the call, which
    is the only way a widget moved between two panels keeps its distance from the one
    it lives in instead of haunting the one it used to.
    """
    _, ctx, _ = stage
    box, (a,) = deck(run, kids=1)
    assert eval_ui('ANCHOR(a, "bottom")') == "bottom -> parent.bottom"
    ctx.layout()
    assert a.screen_box[1] + a.height == 120.0
    run('VAR other = PANEL("other")\n'
        "PLACE(other, 0, 0, 100, 60)\n"
        "PAD(other, 0)\n"
        "NODE_ADD(other, a)\n")
    ctx.layout()
    assert a.screen_box[1] + a.height == 60.0, "the glue followed the parent"


@pytest.mark.parametrize("edge,shown", [
    ("left", "left -> parent.left"),
    ("r", "right -> parent.right"),
    ("cx", "center-x -> parent.center-x"),
])
def test_the_short_forms_a_person_reaches_for_first_are_the_engines_own(
        run, eval_ui, edge, shown):
    """``"r"`` and ``"cx"`` mean the edge they always meant, and are read back so."""
    deck(run, kids=1)
    assert eval_ui(f'ANCHOR(a, "{edge}")') == shown


def test_gluing_the_two_ends_of_one_axis_is_a_stretch_not_a_fight(run, eval_ui,
                                                                  stage):
    """A bar pinned left *and* right is the window's width, and a resize keeps it.

    This is the plan's dialog and menu-bar rule in one pair of calls, and the resize
    is the second half of the test: a widget that only spans the window on the frame
    it was glued in is a widget that stops being a bar the moment the person drags
    the corner.
    """
    _, ctx, _ = stage
    deck(run)
    run('VAR bar = PANEL("bar")\nPLACE(bar, 10, 0, 100, 24)\n'
        'ANCHOR(bar, "left")\nANCHOR(bar, "right")\n')
    bar = handle(run("bar"))
    ctx.layout()
    inner = ctx.root.content_box()
    assert bar.screen_box[0] == pytest.approx(inner[0])
    assert bar.width == pytest.approx(inner[2])
    ctx.set_size(640, 400)
    ctx.layout()
    grown = ctx.root.content_box()
    assert grown[2] > inner[2]
    assert bar.width == pytest.approx(grown[2]), "the bar is the new width"
    assert bar.screen_box[0] + bar.width == pytest.approx(grown[0] + grown[2])


def test_a_glue_written_twice_replaces_the_first(run, eval_ui):
    """One edge, one rule: a second ``ANCHOR`` moves the margin, it does not add one."""
    box, (a,) = deck(run, kids=1)
    run('ANCHOR(a, "right", box, "right", -8)\n'
        'ANCHOR(a, "right", box, "right", -16)\n')
    assert eval_ui('GET(a, "place")') == ["right -> root/box.right -16"]


def test_a_widget_can_be_glued_to_a_sibling_and_lands_on_that_siblings_edge(
        run, eval_ui, stage):
    """The edges read are the line on the screen, not the neighbour's content box."""
    _, ctx, _ = stage
    box, (a, b) = deck(run, kids=2)
    assert eval_ui('ANCHOR(b, "left", a, "right", 6)') \
        == "left -> root/box/a.right +6"
    ctx.layout()
    assert a.screen_box == pytest.approx((0.0, 0.0, 30.0, 20.0))
    assert b.screen_box == pytest.approx((36.0, 0.0, 30.0, 20.0))
    assert b in box.children, "gluing does not reparent"


def test_unanchor_answers_how_many_glues_went_and_place_reads_the_emptiness(
        run, eval_ui):
    """The list is in edge order, and taking rules away is a countable act."""
    box, (a,) = deck(run, kids=1)
    run('ANCHOR(a, "left")\nANCHOR(a, "right")\n'
        'ANCHOR(a, "bottom", box, "bottom", -4)\n')
    assert eval_ui('GET(a, "place")') == [
        "left -> parent.left", "right -> parent.right",
        "bottom -> root/box.bottom -4",
    ]
    assert eval_ui('UNANCHOR(a, "right")') == 1
    assert eval_ui('UNANCHOR(a, "right")') == 0, "it is already gone"
    assert eval_ui("UNANCHOR(a)") == 2
    assert eval_ui('GET(a, "place")') == []
    assert eval_ui("UNANCHOR(a)") == 0, "and not a third time"


# -- PACK, and GET(w, "pack") reading it back ---------------------------


def test_a_widget_nobody_told_answers_two_fixed_axes(run, eval_ui):
    """``("fixed", "fixed")`` is the default, and a ``GET`` says it before any verb ran."""
    deck(run, kids=1)
    assert eval_ui('GET(a, "pack")') == ["fixed", "fixed"]
    assert eval_ui('PACK(a, "grow")') == ["grow", "grow"]
    assert eval_ui('GET(a, "pack")') == ["grow", "grow"]
    assert eval_ui('PACK(a, "grow", "fixed")') == ["grow", "fixed"]
    assert eval_ui('GET(a, "pack")') == ["grow", "fixed"]


@pytest.mark.parametrize("word,landed", [
    ("keep", "fixed"), ("scale", "grow"), ("expand", "fill"), ("stretch", "fill"),
])
def test_the_words_a_script_arrives_with_are_the_three_the_pass_understands(
        run, eval_ui, word, landed):
    """:func:`crude.ui.layout.as_policy` is the only translator, for both callers."""
    deck(run, kids=1)
    assert eval_ui(f'PACK(a, "{word}")') == [landed, landed]


def test_grow_keeps_the_fraction_of_the_panel_the_size_was_placed_at(run,
                                                                    eval_ui,
                                                                    stage):
    """One word of ``PACK`` and a resize is the plan's "the slider gets longer"."""
    _, ctx, _ = stage
    deck(run, kids=1)
    held = handle(run("a"))
    run('PACK(a, "grow", "fixed")\n')
    ctx.layout()
    assert held.width == 30.0, "the first pass only takes the reference"
    run("PLACE(box, 0, 0, 300, 120)\n")
    ctx.layout()
    assert held.width == 45.0, "30 of 200 becomes 45 of 300"
    assert held.height == 20.0, "the axis that was said fixed"


def test_fill_outside_a_stack_fills_to_the_far_edge_from_where_the_widget_stands(
        run, eval_ui, stage):
    """``PACK(w, "fill")`` without a row is the same answer a left-and-right pair gives."""
    _, ctx, _ = stage
    deck(run, kids=1)
    run('PLACE(a, 20, 20)\nPACK(a, "fill")\n')
    ctx.layout()
    assert handle(run("a")).screen_box == pytest.approx((20.0, 20.0, 180.0, 100.0))


def test_fill_inside_a_stack_takes_what_the_siblings_left(run, eval_ui, stage):
    """The slack is divided between the fillers *after* every gap and every fixed.

    The positions of the siblings are asserted as well as the filler's width, because
    a row that gave the filler its share and then forgot to move the next child is
    two widgets in one place, which is the kind of mistake a size-only test never
    sees.
    """
    _, ctx, _ = stage
    box, (a, b, c) = deck(run, kids=3)
    run('PACK(b, "fill", "fixed")\nHSTACK(box, 10)\n')
    ctx.layout()
    assert boxes([a, b, c]) == [
        (0.0, 0.0, 30.0, 20.0),
        (40.0, 0.0, 120.0, 20.0),
        (170.0, 0.0, 30.0, 20.0),
    ]


# -- PAD ----------------------------------------------------------------


def test_pad_answers_the_pixels_it_wrote_and_moves_the_space_children_get(
        run, eval_ui, stage):
    """Padding is not decoration: it is where children start and where a hit stops."""
    _, ctx, _ = stage
    box, (a,) = deck(run, kids=1)
    assert eval_ui("PAD(box, 0)") == 0.0
    ctx.layout()
    assert box.content_box() == pytest.approx((0.0, 0.0, 200.0, 120.0))
    assert eval_ui("PAD(box, 16)") == 16.0
    ctx.layout()
    assert box.content_box() == pytest.approx((16.0, 16.0, 168.0, 88.0))
    assert a.screen_box == pytest.approx((16.0, 16.0, 30.0, 20.0))


def test_pad_can_be_named_as_a_metric_and_gives_the_skin_back_by_being_omitted(
        run, eval_ui, stage):
    """A token is measured against the skin this tree is drawn with; ``None`` is "the skin's own"."""
    _, ctx, _ = stage
    box, (a,) = deck(run, kids=1)
    ctx.layout()
    assert eval_ui('PAD(box, "pad.lg")') == 12.0
    ctx.layout()
    assert box.content_box() == pytest.approx((12.0, 12.0, 176.0, 96.0))
    # the same number ``PAD(box, 0)`` answered, and the opposite meaning: the
    # override is gone, so the next pass measures the panel's own role again
    assert eval_ui("PAD(box)") == 0.0
    ctx.layout()
    inner = box.content_box()
    assert inner[0] > 0.0, "back to the skin's padding"
    assert a.screen_box[0] == pytest.approx(inner[0])


def test_pad_is_a_containers_question_and_a_leaf_says_so(run, failure):
    deck(run, kids=1)
    error = failure('PAD(a, 8)')
    assert "a 'button' holds no children" in error


# -- the arrange verbs --------------------------------------------------


def test_a_horizontal_stack_rows_the_children_in_the_order_they_were_added(
        run, eval_ui, stage):
    """Z-order is the row: three children, one gap, and no arithmetic left to the reader."""
    _, ctx, _ = stage
    box, kids = deck(run, kids=3)
    assert handle(run("HSTACK(box, 10)")) is box, "the verb answers the panel it arranged"
    ctx.layout()
    assert boxes(kids) == [
        (0.0, 0.0, 30.0, 20.0), (40.0, 0.0, 30.0, 20.0),
        (80.0, 0.0, 30.0, 20.0),
    ]


def test_a_vertical_stack_is_the_same_row_turned_and_starts_at_the_top(run,
                                                                       eval_ui,
                                                                       stage):
    _, ctx, _ = stage
    box, kids = deck(run, kids=3)
    run("VSTACK(box, 8)\n")
    ctx.layout()
    assert boxes(kids) == [
        (0.0, 0.0, 30.0, 20.0), (0.0, 28.0, 30.0, 20.0),
        (0.0, 56.0, 30.0, 20.0),
    ]


def test_an_omitted_gap_is_the_skins_answer_measured_into_the_row(run, eval_ui,
                                                                  stage):
    """``VSTACK(box)`` is ``VSTACK(box, "pad.md")``, and the pass measures it per frame."""
    _, ctx, _ = stage
    box, kids = deck(run, kids=3)
    run("VSTACK(box)\n")
    ctx.layout()
    assert [held.screen_box[1] for held in kids] == [0.0, 28.0, 56.0]
    run('HSTACK(box, "pad.lg")\n')
    ctx.layout()
    assert [held.screen_box[0] for held in kids] == [0.0, 42.0, 84.0]


def test_the_option_list_beats_the_positional_argument_that_answered_first(
        run, eval_ui, stage):
    """The last word written wins, which is what makes an option list worth having."""
    _, ctx, _ = stage
    box, kids = deck(run, kids=3)
    run('VSTACK(box, 10, ["gap", 4])\n')
    ctx.layout()
    assert [held.screen_box[1] for held in kids] == [0.0, 24.0, 48.0]


def test_a_stacks_own_pad_moves_where_the_children_start(run, eval_ui, stage):
    """``"pad"`` on a row is the container's padding for as long as the row lasts."""
    _, ctx, _ = stage
    box, kids = deck(run, kids=2)
    run('HSTACK(box, 10, ["pad", 4])\n')
    ctx.layout()
    assert box.content_box() == pytest.approx((4.0, 4.0, 192.0, 112.0))
    assert boxes(kids) == [(4.0, 4.0, 30.0, 20.0), (44.0, 4.0, 30.0, 20.0)]


@pytest.mark.parametrize("word,across", [("center", 50.0), ("centre", 50.0),
                                         ("end", 100.0), ("stretch", 0.0)])
def test_align_is_how_a_child_sits_across_the_space_it_was_given(run, eval_ui,
                                                                 stage, word,
                                                                 across):
    """The cross axis, one word, and the British spelling accepted like the engine does it."""
    _, ctx, _ = stage
    box, kids = deck(run, kids=3)
    run(f'HSTACK(box, 10, ["align", "{word}"])\n')
    ctx.layout()
    assert [held.screen_box[1] for held in kids] == [across] * 3
    if word == "stretch":
        assert kids[0].height == 120.0
    else:
        assert kids[0].height == 20.0, "align moves a child, it does not size it"


def test_a_grid_gives_every_child_a_cell_and_reports_the_cells_it_measured(
        run, eval_ui, stage):
    """The rows a panel answers with are the pass's own report of this frame."""
    _, ctx, _ = stage
    box, kids = deck(run, kids=4)
    assert eval_ui('GET(box, "grid_columns")') == 0.0, "nothing has arranged it yet"
    for word in WORDS[:4]:
        run(f'PACK({word}, "fill")\n')
    run("GRID(box, 2, 10)\n")
    ctx.layout()
    assert (eval_ui('GET(box, "grid_columns")'),
            eval_ui('GET(box, "grid_rows")'),
            eval_ui('GET(box, "grid_gap")')) == (2.0, 2.0, 10.0)
    assert boxes(kids) == [
        (0.0, 0.0, 95.0, 55.0), (105.0, 0.0, 95.0, 55.0),
        (0.0, 65.0, 95.0, 55.0), (105.0, 65.0, 95.0, 55.0),
    ]


def test_the_cells_a_GRID_arranged_are_the_cells_the_grid_module_measures(
        run, eval_ui, stage):
    """One formula, two callers: the arranger and a future drop cannot disagree.

    :class:`crude.ui.grid.GridSpec` is what stage 4 docks into, so the rectangle the
    verb produced has to be the rectangle the module answers for the same panel -
    checked here through the language, because it is the language's ``GRID`` that has
    to leave the rows in a state the module can read.
    """
    _, ctx, _ = stage
    box, kids = deck(run, kids=4)
    for word in WORDS[:4]:
        run(f'PACK({word}, "fill")\n')
    run('GRID(box, 2, 10, ["dockable", 1])\n')
    ctx.layout()
    assert eval_ui('GET(box, "dockable")') == 1, "the option list wrote the row"
    assert eval_ui('GET(box, "docking")') == 0, "and nothing is being dragged"
    spec = spec_of(box)
    assert spec == GridSpec(2, 2, 10.0)
    assert boxes(kids) == list(spec.every(box.content_box()))


def test_an_auto_grid_counts_its_columns_from_the_children_there_are(run,
                                                                    eval_ui,
                                                                    stage):
    """``GRID(box, 0)`` is as square as the child count allows, and says so in the rows."""
    _, ctx, _ = stage
    box, kids = deck(run, kids=5)
    run("GRID(box)\n")
    ctx.layout()
    assert (eval_ui('GET(box, "grid_columns")'),
            eval_ui('GET(box, "grid_rows")')) == (3.0, 2.0)
    assert len(kids) == 5, "the sixth cell is measured and empty, not invented"
    assert spec_of(box).count == 6


def test_the_rows_the_pass_measured_are_not_rows_a_script_can_write(run,
                                                                    eval_ui,
                                                                    failure,
                                                                    stage):
    """A script that set its own column count would be describing a grid nothing made."""
    _, ctx, _ = stage
    box, kids = deck(run, kids=4)
    run("GRID(box, 2, 10)\n")
    ctx.layout()
    assert eval_ui('GET(box, "grid_rows")') == 2.0
    for row in ("grid_columns", "grid_rows", "grid_gap", "docking"):
        error = failure(f'SET(box, "{row}", 3)')
        assert "layout pass, not written by a script" in error, row


def test_undoing_an_arrangement_reports_one_then_zero_and_stops_measuring_cells(
        run, eval_ui, stage):
    """A panel that no longer arranges has to stop describing the old cells.

    The children keep the boxes the last pass wrote: a script that undid an
    arrangement has not said where they belong instead, and inventing a place would
    be the layout layer overruling the person who called the verb.
    """
    _, ctx, _ = stage
    box, kids = deck(run, kids=4)
    run("GRID(box, 2, 10)\n")
    ctx.layout()
    before = boxes(kids)
    assert eval_ui("UNSTACK(box)") == 1
    ctx.layout()
    assert boxes(kids) == before, "nothing re-placed them"
    assert eval_ui('GET(box, "grid_columns")') == 0.0
    assert eval_ui('GET(box, "grid_rows")') == 0.0
    assert eval_ui('GET(box, "grid_gap")') == 0.0
    assert eval_ui("UNSTACK(box)") == 0


@pytest.mark.parametrize("call,why", [
    ('HSTACK(box, 8, ["algin", "center"])',
     "'algin' is not an option it knows (known: gap, pad, align)"),
    ('HSTACK(box, 8, ["dockable", 1])',
     "'dockable' is not an option it knows (known: gap, pad, align)"),
    ('GRID(box, 2, 10, ["align", "sideways"])',
     "'sideways' is not an alignment (known: start, center, end, stretch)"),
    ('VSTACK(box, 8, ["gap"])',
     "has 1 item(s), which leaves one without a name"),
    ('VSTACK(box, 8, [1, "gap"])',
     "an 'options' name must be a string"),
    ('GRID(box, 2.5)', "'columns' counts cells"),
    ('GRID(box, -2)', "is not a number of cells"),
    ('HSTACK(a, 8)', "holds no children"),
])
def test_a_list_or_a_word_that_is_not_one_of_this_verbs_words_is_refused_by_name(
        run, failure, call, why):
    """Nothing here is dropped quietly: an option nobody recognised is a different panel.

    :func:`crude.ui.layout.arrange` ignores a key it does not know, which is right for
    a document and wrong for a script's argument list - so the bindings check the
    words against the verb's own table before the call and name the table when one is
    out of it.
    """
    deck(run, kids=1)
    assert why in failure(call)


@pytest.mark.parametrize("call,why", [
    ('ANCHOR(a, "sideways")',
     "'sideways' is not an edge (known: left, right, top, bottom, "
     "center-x, center-y)"),
    ('ANCHOR(a, "right", a, "right")', "cannot be anchored to itself"),
    ('PACK(a, "stretchy")',
     "'stretchy' is not a pack policy (known: fixed, grow, fill)"),
])
def test_a_word_the_placement_engine_does_not_have_is_refused_with_the_words_it_does(
        run, failure, call, why):
    """The same ``LayoutError`` a paper test asserts on, in the bindings' own voice."""
    deck(run, kids=1)
    assert why in failure(call)


# -- what the glue wins -------------------------------------------------


def test_an_axis_a_widget_was_glued_to_stops_belonging_to_the_row_that_arranged_it(
        run, eval_ui, stage):
    """The plan's promise, tested on the second frame rather than the first.

    A row that went on sizing an axis the script had glued would put the widget back
    at its own answer and let the glue move it again - a box that changed twice per
    frame and a paint key that never settled. So the row hands the axis over, and
    keeps the one nobody glued.
    """
    _, ctx, _ = stage
    box, kids = deck(run, kids=3)
    run("VSTACK(box, 8)\n")
    ctx.layout()
    assert [held.screen_box[1] for held in kids] == [0.0, 28.0, 56.0]
    run('ANCHOR(c, "right", box, "right", -5)\n')
    ctx.layout()
    # x is the glue's: 200 - 5 - 30. y is still the row's: 2 * (20 + 8)
    assert kids[2].screen_box == pytest.approx((165.0, 56.0, 30.0, 20.0))
    assert kids[0].screen_box == pytest.approx((0.0, 0.0, 30.0, 20.0))
    before = boxes(kids)
    ctx.layout()
    assert boxes(kids) == before, "and the next frame agrees rather than fights"


def test_an_axis_a_widget_was_glued_to_stops_belonging_to_its_pack_policy(
        run, eval_ui, stage):
    """``PACK(w, "fill")`` and a bottom glue are not a contradiction: the glue is y, fill is x."""
    _, ctx, _ = stage
    box, (a,) = deck(run, kids=1)
    run('PACK(a, "fill")\nANCHOR(a, "bottom", box, "bottom", -4)\n')
    ctx.layout()
    assert a.screen_box == pytest.approx((0.0, 96.0, 200.0, 20.0))
    run("PLACE(box, 0, 0, 240, 160)\n")
    ctx.layout()
    assert a.screen_box == pytest.approx((0.0, 136.0, 240.0, 20.0))
    assert a.width == 240.0, "fill still owns the axis nobody glued"
    assert a.height == 20.0, "the margin owns this one"


# -- the two words GET reads, and what SET says about them --------------


def test_a_widget_that_has_never_been_placed_still_answers_both_layout_words(
        run, eval_ui):
    """An empty list and two defaults are answers, not errors - nothing was said yet."""
    deck(run, kids=1)
    assert eval_ui('GET(a, "place")') == []
    assert eval_ui('GET(a, "pack")') == ["fixed", "fixed"]
    assert eval_ui('WIDGET_HAS(a, "place")') == 1
    assert eval_ui('WIDGET_HAS(a, "pack")') == 1
    assert eval_ui('WIDGET_HAS(a, "plce")') == 0


def test_the_two_words_a_get_can_read_are_refused_by_a_set_that_names_the_verb(
        run, failure):
    """They are not rows, so a ``SET`` here would be a script editing a layout it cannot see."""
    deck(run, kids=1)
    assert "ANCHOR() and UNANCHOR() write gluing" in failure('SET(a, "place", 1)')
    assert "only PACK() writes that" in failure('SET(a, "pack", "grow")')


def test_a_widget_a_painter_was_handed_is_the_same_widget_the_verbs_take(stage,
                                                                        run,
                                                                        eval_ui):
    """A placement verb works on the widget an ``ON_PAINT`` was given, not only on one it made.

    :func:`crude.bindings.ui._as_python` accepts the handle a binding returned *and*
    the wrapper :meth:`Program.invoke` puts around a widget when a handler passes one
    on, and a verb that only took the first would fail for a reason the script has no
    way to see - the painter's ``k`` and the script's ``a`` are one widget.
    """
    _, ctx, _ = stage
    run(
        'VAR box = PANEL("box")\nPLACE(box, 0, 0, 200, 120)\nPAD(box, 0)\n'
        'VAR a = BUTTON("a")\nPLACE(a, 0, 0, 30, 20)\nNODE_ADD(box, a)\n'
        'ON_PAINT(a, FUNC(p, k) -> ANCHOR(k, "right", box, "right", -8))\n'
    )
    held, box = handle(run("a")), handle(run("box"))
    ctx.frame()
    assert ctx.handlers.errors == [], ctx.handlers.errors
    assert eval_ui('GET(a, "place")') == ["right -> root/box.right -8"]
    ctx.layout()
    inner = box.content_box()
    assert held.screen_box[0] + held.width == pytest.approx(
        inner[0] + inner[2] - 8.0)
