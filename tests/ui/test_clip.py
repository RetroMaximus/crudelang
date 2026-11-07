"""A clip as arithmetic: samples, pixels, a window, and the marks inside it.

:mod:`crude.ui.clip` is the module the plan's Stage 2 turns on, and it makes one
promise that everything else about the kind depends on: **a sample and a pixel are
the same place seen from two sides**. So most of this file is round trips - the
mapping forward through :func:`sample_at` and back through :func:`pixel_of`, at
several points along the plate - because the face, the gesture and a script's
``GET`` all read that one ruler, and any of them being a few samples out would be
three different clips wearing one widget. The rest is the four rules the module
states about itself:

* the props are the fact, so :func:`window_of` answers what a ``count`` of ``0``
  turned out to be *this frame* and :func:`set_window` is the only door that writes
  the pair - clamped, and in the order that lets the first be measured against the
  width the clip is about to have;
* nothing is resolved until it is asked for, so a take that is still growing is
  still fully shown, and a clip with no ``data`` has a window of nothing rather
  than a division by zero;
* a painter never writes a prop, which is why :func:`envelope` is pure and
  :func:`follow_all` is a pass the layout runs - :func:`apply_follow` writes
  ``start``, and a draw pass that did that would be a frame repainting itself;
* the pixels the face draws with are the pixels a press is measured against, so
  :func:`nearest_marker` searches the list :func:`marker_pixels` returns and a mark
  a hand can grab is exactly the mark the eye was shown.

Two numbers are load-bearing and are proved here rather than left to the gestures:
``count`` ``0`` means *everything, including what arrives next*, so no pan and no
zoom may rewrite it as today's length; and a sample is **whole**, so a marker
dragged across two hundred pixels lands on a number a script can compare with
the knob wired to it.

Every distance is counted from :func:`plot_box` rather than from the ``200`` the
widget is placed at, because a clip keeps the skin's inset: the plate is the
*content* box, and a test that assumed the outer edge would be measuring the take
from a pixel the face never draws in.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

import crude.bindings  # noqa: F401 - registers every area, including ui
from crude.lang import Program
from crude.ui import Skin, UIContext, Widget, define, reset, root_panel
from crude.ui.clip import (GRAB, apply_follow, clamp_window, clip_of, columns_of,
                           envelope, follow_all, frames_of, marker_pixels,
                           nearest_marker, pan_from, pixel_of, plot_box,
                           sample_at, samples_of, samples_per_pixel, set_marker,
                           set_window, window_of, zoom_at)

#: the take every test here is measured against, and the plate it is drawn on
TAKE = 200.0
BOX = (0.0, 0.0, 200.0, 60.0)


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
    """The plan's 360 x 220 client rect, synced, so a box means a pixel."""
    held = root_panel(360, 220)
    held.sync(skin)
    return held


def clip(root: Widget, values: Optional[Dict[str, Any]] = None, *,
         name: str = "take", box: Tuple[float, ...] = BOX) -> Widget:
    """A clip in the tree at a size, resolved, so its pixels mean samples.

    Laid out in the dark skin the plan's example uses: the inset the plate keeps is
    what decides where the first sample of a window sits, and a test that synced in
    one theme and measured in another would be asserting on a mapping no frame has.
    """
    held = Widget("clip", values, name=name, parent=root)
    held.place(*box)
    root.sync(Skin("dark"))
    return held


def across(held: Widget, share: float) -> float:
    """One point along the plate, as the face and the gesture both measure it."""
    left, _top, width, _height = plot_box(held)
    return left + width * share


def held_window(held: Widget) -> Tuple[float, float]:
    """The two rows as a script's ``GET`` would answer them, not as drawn."""
    return float(held.get("start")), float(held.get("count"))


# -- the ruler ---------------------------------------------------------------


def test_a_sample_and_a_pixel_are_the_same_place_seen_from_two_sides(root):
    """Every mapping in the module is this one, and everything reads it the same."""
    held = clip(root, {"data": [0.0] * 200, "start": 40, "count": 80})
    assert window_of(held) == (40.0, 80.0)
    assert pixel_of(held, 40.0) == pytest.approx(across(held, 0.0))
    assert pixel_of(held, 120.0) == pytest.approx(across(held, 1.0))
    assert sample_at(held, across(held, 0.25)) == pytest.approx(60.0)
    for share in (0.0, 0.13, 0.5, 0.87, 1.0):
        wanted = 40.0 + share * 80.0
        assert sample_at(held, pixel_of(held, wanted)) == pytest.approx(wanted)
    _left, _top, width, _height = plot_box(held)
    assert samples_per_pixel(held) == pytest.approx(80.0 / width)


def test_a_clip_that_has_nothing_yet_has_a_window_of_nothing(root):
    """An empty ``data`` is a real state, and it answers instead of dividing by zero."""
    held = clip(root)
    assert frames_of(held) == 0.0
    assert window_of(held) == (0.0, 0.0)
    assert samples_per_pixel(held) == 0.0, "there is no ruler here to read"
    assert envelope(held) == [], "and nothing to fold into columns"
    assert pixel_of(held, 100.0) == pytest.approx(across(held, 0.0))
    # a gesture cannot move a window that has no span, and says so by writing nothing
    assert zoom_at(held, across(held, 0.5), 1) == (0.0, 0.0)
    assert pan_from(held, 0.0, 25.0) == (0.0, 0.0)
    assert held_window(held) == (0.0, 0.0)
    assert held.take_changes() == [], "an idle clip stays idle"


def test_a_list_that_is_not_numbers_is_folded_to_silence_and_keeps_its_places(root):
    """``data`` is one ``SET`` away from anything, so the fold chooses a sample.

    A non-number becomes the sample of silence rather than dropping out: a marker
    that moved up the list because a row was skipped would mark a different part of
    the take than the number in ``markers`` says it does.
    """
    held = clip(root, {"data": ["a", None, 0.5]})
    assert samples_of(held) == [0.0, 0.0, 0.5]
    assert frames_of(held) == 3.0, "three samples, and none of them dropped out"
    assert pixel_of(held, 2.0) == pytest.approx(across(held, 2.0 / 3.0))
    assert pixel_of(held, 3.0) == pytest.approx(across(held, 1.0))


# -- the window: two props and one fact --------------------------------------


def test_count_zero_is_the_whole_take_and_stays_written_as_zero(root):
    """``0`` is "everything, including what has not been recorded yet".

    Neither the clamp nor a gesture may turn that into today's length: a clip
    showing a take that is being recorded has to keep showing its end as it arrives,
    which is the promise the recorder's viewport is built on.
    """
    held = clip(root, {"data": [0.0] * 200})
    assert window_of(held) == (0.0, TAKE)
    assert clamp_window(held, 0.0, 0.0) == (0.0, 0.0)
    assert clamp_window(held, 0.0, -5.0) == (0.0, 0.0), "less than nothing is nothing"
    assert set_window(held, 0.0, 0.0) == (0.0, 0.0)
    assert held.get("count") == 0.0


def test_a_window_wider_than_the_take_is_resolved_at_draw_time_not_shortened(root):
    """The row keeps the number the script wrote; ``window_of`` keeps the truth.

    Clamping a 240-sample request down to the 200 there are would be a row that
    goes stale the moment the take grows, and a ``GET`` that answers something other
    than what was written is a prop nobody can wire a knob to.
    """
    held = clip(root, {"data": [0.0] * 200})
    assert set_window(held, 0.0, TAKE + 40) == (0.0, 240.0)
    assert window_of(held) == (0.0, TAKE), "resolved, not rewritten"
    assert held.get("count") == 240.0


def test_the_first_sample_is_clamped_against_the_width_the_clip_is_about_to_have(
        root):
    """Order is the whole of the arithmetic: the count lands before the start.

    A window that starts where it cannot fit is slid back to the last position it
    could hold - and measured against the *old* width it would be a frame showing
    the wrong half of the take between the two writes.
    """
    held = clip(root, {"data": [0.0] * 200, "start": 150, "count": 50})
    assert set_window(held, 150.0, 100.0) == (100.0, 100.0)
    assert held_window(held) == (100.0, 100.0)
    assert held.take_changes() == ["count", "start"]
    assert set_window(held, -20.0, 50.0) == (0.0, 50.0), "the head of the take"
    assert clamp_window(held, 190.0, 50.0) == (150.0, 50.0)


def test_a_window_the_gesture_left_where_it_was_reports_no_change(root):
    """A pan that hit the end of the take writes the number it already held.

    This is the rule that keeps a two-way wiring from echoing itself forever, and a
    hand dragging past the head of a recording is the most ordinary gesture there is.
    """
    held = clip(root, {"data": [0.0] * 200, "start": 0, "count": 50})
    assert set_window(held, 0.0, 50.0) == (0.0, 50.0)
    assert held.take_changes() == []
    assert pan_from(held, 0.0, 25.0) == (0.0, 50.0), "clamped at the head"
    assert held.take_changes() == []


# -- the columns: how a file is drawn without a point per sample -------------


def test_the_columns_are_the_pixels_the_box_has_or_the_budget_the_script_named(root):
    held = clip(root, {"data": [0.0] * 200, "points": 20})
    assert columns_of(held) == 20, "``points`` is a drawing budget, not a sample count"
    assert len(envelope(held)) == 20
    held.set("points", 0)
    assert columns_of(held) == int(across(held, 1.0) - across(held, 0.0))
    held.set("points", 5000)
    assert columns_of(held) == int(plot_box(held)[2]), "a budget past the box is the box"
    held.set("points", 4)
    left = across(held, 0.0)
    width = across(held, 1.0) - left
    assert [bar[0] for bar in envelope(held)] == [
        pytest.approx(left + (slot + 0.5) * width / 4.0) for slot in range(4)]


def test_the_envelope_folds_each_column_to_its_lowest_and_highest_sample(root):
    """Min and max per strip, because what a person aims at on a take is its peaks.

    A polyline of one point per sample would be slow and would hide exactly the thing
    a marker is being dragged to. The numbers stay at full scale and unclamped:
    putting a sample inside the plate is the drawing's decision, and clamping it in
    the fold too would put the limit in two places.
    """
    held = clip(root, {"data": [0.2, 0.9, -0.4, -0.8], "points": 2})
    assert [bar[1:] for bar in envelope(held)] == [(0.2, 0.9), (-0.8, -0.4)]
    loud = clip(root, {"data": [2.0, -2.0], "points": 2}, name="loud")
    assert [bar[1:] for bar in envelope(loud)] == [(2.0, 2.0), (-2.0, -2.0)]


def test_the_window_decides_which_samples_get_folded(root):
    held = clip(root, {"data": [-1.0, -1.0, 1.0, 1.0, -1.0, -1.0, 1.0, 1.0],
                       "start": 2, "count": 4, "points": 2})
    assert [bar[1:] for bar in envelope(held)] == [(1.0, 1.0), (-1.0, -1.0)]


# -- the marks ---------------------------------------------------------------


def test_a_marker_outside_the_window_is_neither_drawn_nor_grabbable(root):
    """A mark clamped to the nearest edge would claim the strip's edge is the take's.

    Someone who has scrolled away from their loop points wants the plate to say so,
    and a press must not be able to move a mark it cannot see.
    """
    held = clip(root, {"data": [0.0] * 200, "start": 100, "count": 50,
                       "markers": [20, 100, 125, 150, 160]})
    shown = marker_pixels(held)
    assert [index for index, _x in shown] == [1.0, 2.0, 3.0]
    assert shown[0][1] == pytest.approx(across(held, 0.0)), "100 is the strip's first"
    assert shown[-1][1] == pytest.approx(across(held, 1.0)), "150 is its last"
    assert nearest_marker(held, across(held, 0.0) - GRAB) == 1.0
    assert nearest_marker(held, across(held, 0.5)) == 2.0
    assert nearest_marker(held, across(held, 1.0) + GRAB) == 3.0
    assert nearest_marker(held, across(held, 1.0) + GRAB + 1.0) is None, \
        "the tolerance is six pixels and not a claim on the whole plate"


def test_the_nearest_mark_wins_when_two_are_a_pixel_apart(root):
    """Two markers a pixel apart are settled by which one the pointer was nearer.

    First-found would make the grab depend on the order of the list rather than on
    where the hand was, and a rough and a fine start two samples apart is the normal
    state of a panel with four markers on it.
    """
    held = clip(root, {"data": [0.0] * 200, "start": 96, "count": 8,
                       "markers": [100, 101]})
    assert nearest_marker(held, across(held, 0.5)) == 0.0
    assert nearest_marker(held, across(held, 0.6)) == 1.0
    assert nearest_marker(held, across(held, 0.05)) is None, "nobody is that near"


def test_a_marker_lands_on_a_whole_sample_inside_the_take(root):
    """The rounding happens at the door, so every caller gets a number it can compare.

    A marker of ``199999.9999997`` is not information about a take that holds two
    hundred thousand samples, and a marker on a fractional sample is a position no
    ``GET`` and no knob can agree with.
    """
    held = clip(root, {"data": [0.0] * 200, "markers": [10.0, 20.5]})
    assert set_marker(held, 0, 25.4) == 25.0
    assert set_marker(held, 1, 199.6) == 200.0, "the end of the take is a sample"
    assert set_marker(held, 1, -5.0) == 0.0, "and so is the beginning"
    assert list(held.get("markers")) == [25.0, 0.0]
    assert set_marker(held, 7, 10.0) == 0.0, "no such mark, and nothing written"
    assert list(held.get("markers")) == [25.0, 0.0]


def test_a_marker_moved_back_to_where_it_was_says_nothing(root):
    """An unchanged write is silent, which is what keeps a knob from echoing itself."""
    held = clip(root, {"data": [0.0] * 200, "markers": [40.0]})
    assert set_marker(held, 0, 40.0) == 40.0
    assert held.take_changes() == []
    assert set_marker(held, 0, 40.4) == 40.0, "and 40.4 *is* sample 40"
    assert held.take_changes() == []
    assert set_marker(held, 0, 41.0) == 41.0
    assert held.take_changes() == ["markers"]
    bare = clip(root, {"data": [0.0] * 200}, name="bare")
    assert set_marker(bare, 0, 10.0) == 0.0
    assert bare.take_changes() == [], "a clip with no marks is not handed one"


# -- the zoom and the pan ----------------------------------------------------


def test_the_wheel_halves_and_doubles_about_the_sample_under_the_pointer(root):
    """A zoom the pointer does not ride along with is a window that runs away.

    The step is a ratio, so two hundred thousand samples to a hundred thousand is as
    big a move as two hundred to a hundred: a fixed subtraction would either never
    reach a sample or never leave one.
    """
    held = clip(root, {"data": [0.0] * 200, "start": 0, "count": 100})
    aimed = across(held, 0.5)
    assert zoom_at(held, aimed, 1) == (25.0, 50.0)
    assert pixel_of(held, 50.0) == pytest.approx(aimed), "the sample did not move"
    assert zoom_at(held, aimed, -1) == (0.0, 100.0), \
        "the same notch back is the same window it was"
    assert zoom_at(held, aimed, 0) == (0.0, 100.0), "a wheel that did not move is not a zoom"
    assert zoom_at(held, aimed, -4) == (0.0, 0.0), \
        "out past the take *is* the take, and it is written as 0"
    close = clip(root, {"data": [0.0] * 200, "count": 8}, name="close")
    assert zoom_at(close, across(close, 0.0), 3) == (0.0, 1.0)
    assert zoom_at(close, across(close, 0.0), 4) == (0.0, 1.0), \
        "one sample is the narrowest window there is"


def test_a_zoom_cannot_slip_between_two_samples(root):
    """Half a sample is where the arithmetic lands and a whole one is where it stays."""
    held = clip(root, {"data": [0.0] * 200, "start": 0, "count": 5})
    assert zoom_at(held, across(held, 0.5), 1) == (1.0, 2.0)
    assert held_window(held) == (1.0, 2.0)
    assert zoom_at(held, across(held, 0.5), 1) == (2.0, 1.0), \
        "and the last half-sample left is a window of one"


def test_a_grab_slides_the_take_and_a_window_that_is_the_take_has_nowhere_to_go(root):
    """The hand holds the sample, so a drag right moves the view left along it.

    The anchor is the window's first sample at the press and the pull is everything
    since, so the gesture cannot drift by an increment the way a total added up from
    increments would. At full zoom-out it refuses outright: ``count`` ``0`` is not a
    number to freeze into today's length.
    """
    held = clip(root, {"data": [0.0] * 200, "start": 0, "count": 50})
    assert pan_from(held, 0.0, -25.0) == (25.0, 50.0)
    assert pan_from(held, 25.0, 25.0) == (0.0, 50.0)
    assert held.get("count") == 50.0, "a pan never changes the width of the window"
    whole = clip(root, {"data": [0.0] * 200}, name="whole")
    assert pan_from(whole, 0.0, 30.0) == (0.0, TAKE)
    assert whole.get("count") == 0.0, "and nothing was written to make it a number"
    whole.set("data", [0.0] * 400)
    assert window_of(whole) == (0.0, 400.0), "the take it shows grew underneath it"


# -- the follow pass ---------------------------------------------------------


def test_follow_centres_the_cursor_and_stops_at_the_ends_of_the_take(root):
    """The rule every timeline editor uses, and the reason it is a pass, not a prop.

    Centring is *clamped*: the view stops sliding at the start of the take and at its
    end instead of inventing samples that are not there, which is what lets a cursor
    parked at sample 10 keep the head of the recording on the plate.
    """
    held = clip(root, {"data": [0.0] * 200, "count": 50, "playhead": 100.0,
                       "follow": True})
    assert apply_follow(held) is True
    assert held.get("start") == 75.0, "100 in the middle of a fifty-sample window"
    assert apply_follow(held) is False, "an idle frame writes nothing, so it counts zero"
    held.set("playhead", 10.0)
    assert apply_follow(held) is True
    assert held.get("start") == 0.0, "the head of the take, and no further back"
    held.set("playhead", 195.0)
    assert apply_follow(held) is True
    assert held.get("start") == 150.0, "and the tail, with the window ending on it"


def test_follow_needs_a_reason_to_move(root):
    """A cursor switched off, a row switched off, and a plate that already shows all."""
    held = clip(root, {"data": [0.0] * 200, "count": 50, "playhead": 100.0})
    assert apply_follow(held) is False, "the row is off, so the view is the script's"
    held.set("follow", True)
    assert apply_follow(held) is True
    held.set("playhead", -1.0)
    assert apply_follow(held) is False, "-1 is no cursor, so there is nothing to follow"
    wide = clip(root, {"data": [0.0] * 200, "playhead": 100.0, "follow": True},
                name="wide")
    assert apply_follow(wide) is False, "a take already fully shown cannot be slid"
    assert apply_follow(clip(root, name="bare")) is False, "no data, no window"
    assert apply_follow(Widget("panel", name="box")) is False


def test_the_pass_walks_the_tree_and_counts_the_windows_it_rewrote(root):
    """One walk per layout pass, and a frame with nothing to follow costs no writes."""
    first = clip(root, {"data": [0.0] * 200, "count": 50, "playhead": 100.0,
                        "follow": True}, name="a", box=(0.0, 0.0, 100.0, 40.0))
    second = clip(root, {"data": [0.0] * 200, "count": 50, "playhead": 20.0,
                         "follow": True}, name="b", box=(100.0, 0.0, 100.0, 40.0))
    Widget("panel", name="box", parent=root).place(0.0, 60.0, 100.0, 40.0)
    root.sync(Skin("dark"))
    assert follow_all(root) == 1, "the second was already centred at sample 0"
    assert (first.get("start"), second.get("start")) == (75.0, 0.0)
    assert follow_all(root) == 0, "and the next frame has nothing left to move"
    assert clip_of(root) is None and clip_of(first) is first
    assert clip_of(Widget("panel", name="box")) is None


def test_a_variant_of_a_clip_inherits_the_arithmetic(root):
    """``WIDGET_DEFINE("loop", "clip")`` is navigated by this module too.

    A chain is checked rather than a name, for the reason every other lookup here
    checks it: a variant inherits the props, the face and the gestures, and it would
    be a strange inheritance that lost the ruler.
    """
    define("loop", "clip")
    held = Widget("loop", {"data": [0.0] * 200, "start": 50, "count": 25},
                  name="bar", parent=root)
    held.place(*BOX)
    root.sync(Skin("dark"))
    assert clip_of(held) is held
    assert window_of(held) == (50.0, 25.0)
    assert set_marker(held, 0, 12.6) == 0.0, "no marks yet, so nothing to move"
    held.set("markers", [60.0])
    assert set_marker(held, 0, 12.6) == 13.0


# -- through the language ----------------------------------------------------


def _stage():
    """A real context and a real interpreter over it, as a script would see them."""
    ctx = UIContext(width=360, height=220)
    program = Program("<clip-test>", host=types.SimpleNamespace(ui=ctx))

    def say(source: str) -> Any:
        value, error = program.run(source)
        assert error is None, error.as_string()
        return value

    return ctx, say


def test_a_script_writes_the_window_and_the_layout_pass_follows_the_cursor():
    """``SET(c, "start", 2)`` and a hand on the take are the same two props.

    Asserted over a :class:`~crude.lang.Program` because the follow rule is a promise
    about the frame: a script parks the playhead and the *pass* - not the painter -
    slides the view behind it. A test that called :func:`follow_all` by hand would
    have proved the function and not the widget.
    """
    ctx, say = _stage()
    say('VAR c = CLIP("take")\n'
        'PLACE(c, 0, 0, 200, 60)\n'
        'SET(c, "data", [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])\n'
        'SET(c, "count", 4)\n'
        'SET(c, "playhead", 10)\n'
        'SET(c, "follow", "yes")\n')
    held = ctx.find("take")
    assert held_window(held) == (0.0, 4.0), "the write alone has not moved anything"
    ctx.layout()
    assert held_window(held) == (8.0, 4.0), "the pass put sample 10 mid-window of 4"
    # and a ``SET`` of the window is read straight back, the way the face drew it
    say('SET(c, "follow", "no")\nSET(c, "start", 2)\n')
    ctx.layout()
    assert held_window(held) == (2.0, 4.0), "with follow off the script owns the view"
    assert window_of(held) == (2.0, 4.0)
    # the marks a script writes are the marks a hand could have grabbed
    say('SET(c, "markers", [2, 5])\n')
    assert [index for index, _x in marker_pixels(held)] == [0.0, 1.0]
    assert nearest_marker(held, pixel_of(held, 5.0)) == 1.0


def test_the_clip_module_asks_the_layout_for_nothing(root):
    """``clip`` stands alone, so a recorder, a sampler and a DAW row can all ask it.

    Spelled out of the source rather than trusted from memory, because the knot this
    prevents is invisible: layout resolves widgets, widgets import the clip, and
    importing it back would make the import order a bug instead of a fact.
    """
    import crude.ui.clip as module

    said = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("from .layout", "import layout", "from .widget",
                      "import widget"):
        assert forbidden not in said, forbidden
