"""A viewport as a widget: the ``scroll`` kind, measured, wheeled, clipped.

:mod:`crude.ui.scroll` is the arithmetic a settings dialog used to keep for itself -
a content panel, an offset, a travel - and this file is the plan's acceptance line
for promoting it to a kind: the inner panel receives the children, ``travel`` is
``content`` minus the view and is never negative, a wheel notch moves the offset by
the widget's own ``notch`` and stops at both ends, a script's ``SET(s, "offset", 0)``
moves it the same way the pointer does, the thumb's box lies inside the view it is a
picture of, and a row the viewport has slid out of sight is clipped - not drawn and
not clickable.

Two of those are asserted through the doors a real program uses rather than through
the helper: the wheel goes in the way :mod:`~crude.ui.behaviour` receives it (an
``on_scroll`` the router would call, in the toolkit's own notch units), and the
script's ``SET`` goes through a :class:`~crude.lang.Program` over a real
:class:`~crude.ui.UIContext`, so a layout pass has to have run for the box to follow
the prop - which is exactly the promise the module docstring makes.

The numbers are round on purpose. A 200 x 100 viewport padded to nothing has a view
of one hundred pixels; ten rows of twenty-two make two hundred and twenty of content
and one hundred and twenty of travel; the default ``notch`` is forty-four, which is
two of those rows, and the default ``rows`` is twenty-two.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

import crude.bindings  # noqa: F401 - registers every area, including ui
from crude.lang import Program
from crude.ui import (RecordingRenderer, Skin, UIContext, Widget, face_for, pad,
                      reset)
from crude.ui.props import PropError
from crude.ui.scroll import (BAR_WIDTH, MIN_THUMB, content_of, fit_content,
                              measure, reconcile, restack, rows_in, slide,
                              thumb_rect, top, travel_of, view_of)

#: the viewport every test in here is built on, and the row it slides
VIEW_ACROSS, VIEW_DOWN = 200.0, 100.0
ROW_HEIGHT = 22.0
ROW_COUNT = 10

#: one wheel notch as the router counts it, and the pixels the kind moves per notch
WHEEL_NOTCH = 16.0
NOTCH = 44.0


@pytest.fixture(autouse=True)
def clean_registry():
    """Script kinds are global; hand the table back the way it was found."""
    reset()
    yield
    reset()


@pytest.fixture
def skin() -> Skin:
    return Skin("dark")


@pytest.fixture
def root(skin) -> Widget:
    """The plan's client rect, synced, so a box in here means a pixel outside it."""
    held = Widget("panel", {"label": ""}, name="root")
    held.place(0, 0, 360, 220)
    held.sync(skin)
    return held


def viewport(root: Widget, *, rows: int = ROW_COUNT,
             box=(0.0, 0.0, VIEW_ACROSS, VIEW_DOWN)) -> Widget:
    """A scroll area in the tree, with ``rows`` equal rows of content inside it.

    The padding is written as zero rather than left at the skin's because every
    number in this file is about the two boxes, not about the inset between them;
    ``view_of`` still reads the *content* box, which is the rule the module is built
    on and the reason a dialog can pad its viewport instead of subtracting a margin.
    """
    held = Widget("scroll", {"label": ""}, name="page", parent=root)
    pad(held, 0.0)
    held.place(*box)
    for index in range(rows):
        row = Widget("toggle", {"label": f"r{index}"}, name=f"row{index}")
        held.add(row)
        row.place(0.0, index * ROW_HEIGHT, box[2], ROW_HEIGHT)
    root.sync(Skin("dark"))
    return held


def paint(held: Widget, skin: Skin):
    """The viewport and everything it is showing, into a recorder that keeps clips."""
    held.root().sync(skin)
    drawn = RecordingRenderer()
    drawn.begin(360, 220, 360, 220)
    held.paint(drawn, skin)
    return drawn


def wheeled(held: Widget, notches: float) -> None:
    """The wheel, as the router sends it: ``notches`` steps away from the person."""
    held.on_scroll(0.0, notches * WHEEL_NOTCH, 0)


# -- the structure --------------------------------------------------------


def test_a_scroll_area_is_a_panel_with_one_panel_it_slides(root):
    """The inner panel is a real child, and the children go to it, not to the frame."""
    held = viewport(root)
    inner = content_of(held)
    assert inner is not None and inner is not held
    assert inner.kind.name == "panel"
    assert inner.name == f"{held.name}#rows"
    assert inner.parent is held
    # ``add`` walked through it, so the rows are the content's and not the frame's:
    # the frame has exactly one child, and that child has the rows
    assert held.children == [inner]
    assert rows_in(held) == inner.children
    assert all(row.is_in(inner) for row in rows_in(held))
    # and a plain panel has no answer to this question
    assert content_of(Widget("panel", name="box")) is None


def test_a_script_may_hold_the_content_instead_of_guessing_at_a_name(root):
    """``content_child`` is the door; the name is an implementation detail."""
    held = viewport(root, rows=3)
    assert held.content_child is content_of(held)
    assert [row.name for row in held.content_child.children] == [
        "row0", "row1", "row2"]


# -- the two measures -----------------------------------------------------


def test_travel_is_the_content_less_the_view_and_never_negative(root):
    held = viewport(root)
    content, travel = measure(held)
    assert content == ROW_COUNT * ROW_HEIGHT == 220.0
    assert travel == 220.0 - VIEW_DOWN == 120.0
    assert travel_of(held) == travel
    assert view_of(held) == VIEW_DOWN
    # content that fits has nothing to slide, and the answer stays at zero rather
    # than going negative the way a raw subtraction would
    short = viewport(root, rows=2, box=(0.0, 0.0, VIEW_ACROSS, VIEW_DOWN))
    assert measure(short) == (2 * ROW_HEIGHT, 0.0)
    assert travel_of(short) == 0.0


def test_reconcile_answers_the_two_rows_the_layout_pass_reports(root):
    """``content`` and ``travel`` are read off the rows, then copied into the props."""
    held = viewport(root)
    assert float(held.get("content")) == 0.0, "nobody has measured it yet"
    content, travel = reconcile(held)
    assert (content, travel) == (220.0, 120.0)
    assert float(held.get("content")) == 220.0
    assert float(held.get("travel")) == 120.0
    # the content panel got the height its rows ask for and the width the view gives
    inner = held.content_child
    assert (inner.width, inner.height) == (VIEW_ACROSS, 220.0)


def test_the_views_width_is_taken_up_before_the_rows_are_arranged(root):
    """``fit_content`` is the half of the settle that cannot wait for the rows.

    A row packed ``fill`` is sized against the content panel's own box, so a panel
    whose width is only written *after* the arrangement leaves every row one window
    behind - which is the frame a resize would otherwise have to spend twice.
    """
    held = viewport(root, rows=3)
    inner = held.content_child
    assert inner.width == VIEW_ACROSS

    held.place(0, 0, 120, VIEW_DOWN)                      # the window narrowed
    assert inner.width == VIEW_ACROSS, "nobody has asked the question yet"
    assert fit_content(held) == 1
    assert inner.width == 120.0
    assert fit_content(held) == 0, "an idle frame writes nothing, so it counts zero"
    # the height is still the rows' answer, and this pass does not touch it
    assert inner.height != 120.0
    # and a box that is not a viewport has nothing to be asked
    assert fit_content(Widget("panel", name="box")) == 0


def test_the_two_measured_rows_are_not_a_scripts_to_write(root):
    """They are answers, and a value a script wrote there would be overwritten."""
    held = viewport(root)
    for word in ("content", "travel"):
        assert held.kind.props[word].readonly is True, word
        with pytest.raises(PropError):
            held.set(word, 5.0)
        # the refusal is the same one ``SET`` goes through, so a script is told the
        # row is the layout pass's rather than being handed a value that vanishes
        with pytest.raises(PropError):
            held.kind.settable(word)
    assert held.kind.settable("offset") is held.kind.props["offset"], \
        "this one is theirs to write"
    assert held.set("offset", -10.0) is True


# -- the wheel ------------------------------------------------------------


def test_a_notch_moves_the_notch_and_the_clamp_ends_the_journey(root):
    """One step is the ``notch`` prop, and both ends of the travel stop it there."""
    held = viewport(root)
    reconcile(held)
    assert (float(held.get("notch")), float(held.get("rows"))) == (NOTCH,
                                                                  ROW_HEIGHT)
    wheeled(held, -1)
    assert float(held.get("offset")) == pytest.approx(-NOTCH)
    # the box follows the prop: the content panel is placed at the offset
    assert held.content_child.y == pytest.approx(-NOTCH)
    wheeled(held, -1)
    wheeled(held, -1)
    assert float(held.get("offset")) == pytest.approx(-120.0), "the bottom holds"
    for _ in range(10):
        wheeled(held, 1)
    assert float(held.get("offset")) == 0.0, "and so does the top"
    assert held.content_child.y == 0.0


def test_the_notch_and_the_row_height_are_the_widgets_own_to_change(root):
    """A list that counts rows in sixteen pixels gets sixteen pixels per notch.

    The arrow and the page are then two different distances, which is the one thing
    about them a person can otherwise not tell from the key cap: an arrow is the
    ``rows`` prop and a page is the view, and a key press that answered the wrong
    one would either crawl or skip - so both are pressed from a known place here.
    """
    held = viewport(root)
    held.set("notch", 16.0)
    held.set("rows", 16.0)
    reconcile(held)
    wheeled(held, -1)
    assert float(held.get("offset")) == pytest.approx(-16.0)
    held.on_key("down")
    assert float(held.get("offset")) == pytest.approx(-32.0), "one row at a time"
    held.on_key("up")
    assert float(held.get("offset")) == pytest.approx(-16.0), "and back"
    held.on_key("home")
    held.on_key("page down")
    assert float(held.get("offset")) == pytest.approx(-VIEW_DOWN), \
        "a page is the view, not the view minus a row"
    held.on_key("page up")
    assert float(held.get("offset")) == 0.0, "and comes back the same distance"
    for _ in range(3):
        held.on_key("page down")
    assert float(held.get("offset")) == pytest.approx(-120.0), \
        "the third page ran out of travel and stopped there"
    held.on_key("end")
    assert float(held.get("offset")) == pytest.approx(-120.0), "the whole travel"
    held.on_key("home")
    assert float(held.get("offset")) == 0.0


def test_a_press_on_the_thumb_and_a_drag_slide_it_and_a_press_elsewhere_does_not(
        root):
    """The bar is the only part of a viewport a scroll gesture owns.

    A device list is a column of toggles, and grabbing one of them has to toggle it
    rather than move the list, so the press is tested against the very rectangle the
    face draws - :func:`thumb_rect`, one answer for the eye and the hand. The gesture
    is started in the middle of the travel rather than at the top, because a drag is
    measured from where the thumb was grabbed and a page already at the end of its
    travel would clamp every direction into the same number.
    """
    held = viewport(root)
    reconcile(held)
    slide(held, -60.0)
    bar = thumb_rect(held)
    assert bar is not None
    left, high, wide, tall = bar
    held.on_press(left + wide / 2.0, high + tall / 2.0, 0, 0)
    # pulled down the screen, the content comes back down with it: the offset is
    # the pixels the content has been slid *up*, so the number gets smaller
    held.on_drag(0.0, 30.0, 0)
    assert float(held.get("offset")) == pytest.approx(-30.0)
    held.on_release(left + wide / 2.0, high + tall / 2.0 + 30.0, 0, 0)
    assert float(held.get("offset")) == pytest.approx(-30.0), "a release is not a move"
    # a click in the empty track pages towards the click instead of grabbing air
    before = float(held.get("offset"))
    left, high, wide, tall = thumb_rect(held)
    held.on_press(left + wide / 2.0, high + tall + 20.0, 0, 0)
    assert float(held.get("offset")) == pytest.approx(max(-120.0,
                                                          before - VIEW_DOWN))
    held.on_release(left + wide / 2.0, high + tall + 20.0, 0, 0)
    # and a press left of the bar is the rows' to answer for
    before = float(held.get("offset"))
    held.on_press(10.0, 40.0, 0, 0)
    assert float(held.get("offset")) == pytest.approx(before), "the list stayed put"


def test_the_thumb_is_inside_the_view_it_stands_for(root):
    """Its box is in the viewport's own coordinates, and never leaves the track."""
    held = viewport(root)
    reconcile(held)
    left, high, wide, tall = thumb_rect(held)
    view = held.content_box()
    assert wide == BAR_WIDTH
    assert tall == pytest.approx(max(MIN_THUMB,
                                     VIEW_DOWN * (VIEW_DOWN / 220.0)))
    assert view[1] <= high <= view[1] + view[3]
    assert high + tall <= view[1] + view[3] + 1.0
    assert view[0] <= left and left + wide <= view[0] + view[2]
    # at the bottom of the travel it is at the bottom of the track, and a content
    # that fits has no thumb at all - a bar that cannot move is not information
    top(held)
    assert thumb_rect(held)[1] == pytest.approx(view[1])
    held.set("offset", -120.0)
    reconcile(held)
    assert thumb_rect(held)[1] + thumb_rect(held)[3] == pytest.approx(
        view[1] + view[3], abs=1.0)
    assert thumb_rect(viewport(root, rows=2)) is None


# -- the drawing and the clip ---------------------------------------------


def test_the_face_draws_one_bar_and_only_when_the_prop_says_to(root, skin):
    """``bar`` off leaves the wheel working and nothing painted."""
    held = viewport(root)
    reconcile(held)
    drawn = paint(held, skin)
    rects = drawn.ops_named("rect")
    # the thumb is one rect; switching it off takes exactly that one away
    held.set("bar", 0)
    quiet = paint(held, skin)
    assert len(rects) - len(quiet.ops_named("rect")) == 1
    assert face_for("scroll") is not None


def test_a_row_the_viewport_has_slid_out_of_is_clipped_and_not_clickable(root,
                                                                        skin):
    """The clip is the whole promise, and it is answered twice: draw and hit.

    The recorder keeps every op with the clip that was open when it was emitted and
    culls nothing - a real screen would show no pixel of an op whose clip has no
    height, which is what "clipped" means here, and is asserted as that rather than
    as an op list that silently lost a row.
    """
    held = viewport(root)
    reconcile(held)
    root.sync(skin)
    gone = rows_in(held)[9]
    seen = rows_in(held)[1]
    _left, high, _width, tall = held.content_box()
    assert gone.screen_box[1] > high + tall, "it is below the view"
    assert gone.clip[3] == 0.0, "so its clip is empty"
    assert seen.clip[3] > 0.0, "and a row inside the view is not"
    # the point asked of the hit test is the middle of the *row*, not of the view:
    # a row is twenty-two pixels high and the question is whether that row answers
    assert root.hit(gone.screen_box[0] + 5.0,
                    gone.screen_box[1] + gone.height * 0.5) is not gone
    assert root.hit(seen.screen_box[0] + 5.0,
                    seen.screen_box[1] + seen.height * 0.5) is seen
    drawn = paint(held, skin)
    marks = [op for op in drawn.ops if op.clip == gone.clip]
    assert marks, "the row did draw - under its own clip"
    assert all(op.clip[3] == 0.0 for op in marks)


# -- through the language -------------------------------------------------


def _stage():
    """A real context and a real interpreter over it, as a script would see them."""
    ctx = UIContext(width=360, height=220)
    program = Program("<scroll-test>", host=types.SimpleNamespace(ui=ctx))

    def say(source: str) -> Any:
        value, error = program.run(source)
        assert error is None, error.as_string()
        return value

    return ctx, say


def test_a_script_writes_the_offset_and_a_layout_pass_moves_the_content():
    """``SET(s, "offset", 0)`` is the same gesture as a hand on the wheel.

    The prop is the fact and the box is its shadow - so the write alone does not
    move anything and the next pass does, which is the order a frame runs in and the
    order this asserts. An offset past the end is clamped by the same call that
    answers a wheel, so a script cannot lose a viewport its rows do not have.

    Every distance here is counted from :func:`view_of` rather than from the ``60``
    the script places, because a viewport a script built keeps the skin's inset: the
    view is the *content* box, and a test that subtracted the height written would be
    asserting an inset this module never promised.
    """
    ctx, say = _stage()
    say('VAR s = SCROLL("page")\n'
        'PLACE(s, 0, 0, 200, 60)\n'
        'VAR a = WIDGET("toggle", "top")\n'
        'NODE_ADD(s, a)\n'
        'PLACE(a, 0, 0, 200, 22)\n'
        'VAR b = WIDGET("toggle", "mid")\n'
        'NODE_ADD(s, b)\n'
        'PLACE(b, 0, 22, 200, 22)\n'
        'VAR c = WIDGET("toggle", "new")\n'
        'NODE_ADD(s, c)\n'
        'PLACE(c, 0, 44, 200, 22)\n'
        'VAR d = WIDGET("toggle", "one")\n'
        'NODE_ADD(s, d)\n'
        'PLACE(d, 0, 66, 200, 22)\n'
        'VAR e = WIDGET("toggle", "two")\n'
        'NODE_ADD(s, e)\n'
        'PLACE(e, 0, 88, 200, 22)\n')
    held = ctx.find("page")
    ctx.layout()
    content, view = 5 * ROW_HEIGHT, view_of(held)
    assert view < 60.0, "the view is the content box, the skin's inset included"
    assert float(held.get("content")) == content
    limit = -(content - view)
    assert float(held.get("travel")) == pytest.approx(-limit)
    assert content - view > NOTCH, "the travel is longer than one notch"

    say('SET(s, "offset", -300)')
    ctx.layout()
    assert float(held.get("offset")) == pytest.approx(limit), "the travel is the limit"
    assert held.content_child.y == pytest.approx(limit)

    say('SET(s, "offset", 0)')
    ctx.layout()
    assert held.content_child.y == 0.0, "the newest row is the one at the top"

    # and the wheel a script never touched reaches the same numbers through the
    # router, because the behaviour is installed by the kind and not by the caller
    assert say('GET(s, "offset")') is not None
    held.on_scroll(0.0, -16.0, 0)
    assert float(held.get("offset")) == pytest.approx(-NOTCH), "one notch, not clamped"
    assert held.content_child.y == pytest.approx(-NOTCH)
    ctx.layout()
    assert held.content_child.y == pytest.approx(-NOTCH), "and the pass agrees"


def test_restack_settles_every_viewport_the_tree_holds(root):
    """One walk, one answer per viewport - what the layout pass runs each frame."""
    first = viewport(root, rows=6)
    second = viewport(root, rows=2, box=(0.0, 110.0, VIEW_ACROSS, 60.0))
    assert restack(root) == 2
    assert float(first.get("travel")) == 6 * ROW_HEIGHT - VIEW_DOWN
    assert float(second.get("travel")) == 0.0
    assert (float(first.get("content")), float(second.get("content"))) == (
        132.0, 44.0)


def test_the_viewport_module_needs_the_layout_engine_for_nothing(root):
    """``scroll`` stands alone, so a dialog, a group and a page can all ask it.

    Guarded because the module says so in its own docstring: layout resolves
    widgets, widgets import the viewport, and importing it back would be a knot
    rather than a nuisance - so the import is spelled out of the source instead of
    trusted from memory.
    """
    import crude.ui.scroll as module
    from pathlib import Path

    said = Path(module.__file__).read_text(encoding="utf-8")
    assert "from .layout" not in said and "import layout" not in said
    # the two verbs a viewport does need are imported where they run, in the widget
    assert view_of(viewport(root, rows=1)) == VIEW_DOWN
