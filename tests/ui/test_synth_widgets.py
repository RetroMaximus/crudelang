"""The synth widgets, drawn and dragged on paper: the plan's S9 acceptance line.

:mod:`crude.ui.faces` says what these kinds look like and
:mod:`crude.ui.behaviour` says what a gesture on them means; the plan's S9 is
"ADSR/curve/XY/keybed/scope/spectrum/preset bar - deterministic frame tests + Pd
round-trips via ``FakeLibpd``", so this file puts a number on both halves. Every
face is painted into a :class:`~crude.ui.recording.RecordingRenderer` and read
back as an op list (a trace, a row of bars, one lit key), every editable kind is
pressed and dragged as raw arithmetic the way
:mod:`tests.ui.test_behaviour` does it, and the last section walks the real door -
a ``.crude`` script that builds a keybed, wires ``ON_CHANGE`` to
``PD_MIDI_NOTEON`` and is heard by ``FakeLibpd`` - because the keybed is the one
synth widget that promises Pd a note.

Two things make these tests S9 and not a repeat of S8:

* the plate is the skin's and the *inside* is the face's, so the assertions are
  about ops a plate-and-label face never emits (``polyline``, ``circle``, the
  per-bin ``rect``s) landing where the props say they should;
* a face draws exactly what a prop holds - nothing is sampled and no FFT is
  computed here - so an empty ``data`` is the deterministic flat line of silence
  a reviewer can check by hand, and preset_bar is proven to be a radio in both
  its paint (plate and word, no face) and its gesture (a cell index).
"""

from __future__ import annotations

import math
import types
from pathlib import Path
from typing import Any, List

import pytest

import crude.bindings  # noqa: F401 - registers every area, including ui and pd
from crude.lang import Program
from crude.lang.values import HostHandle, Number, String
from crude.lang.values import List as CrudeList
from crude.pd import FakeLibpd, PdRuntime, PdSession
from crude.ui import (BEHAVIOURS, FACES, RecordingRenderer, Skin, UIContext,
                      Widget, behaviour_for, define, face_for, get_kind,
                      pd_arguments, pd_object_for, reset, root_panel)

#: the kinds that own a face: every one of them draws a picture inside the plate,
#  from the eight synth displays and canvases to the ranges, the meter, the radio's
#  cells and the dial - while ``vslider`` and ``preset_bar`` are proved below to
#  reach a face through the chain instead of by owning one. ``scroll`` is the odd
#  one out among the controls: its plate is the panel it is built on and the only
#  mark it adds is the thumb, which is a picture inside the plate all the same.
#  ``panel`` is the odd one out among the containers: its face adds a label, which
#  is what a bare plate already drew, and outlines cells only while a drag is
#  looking for one - so an ordinary panel is still painted by the plate alone.
#  ``group`` draws no plate at all: the header band, its word and its mark are the
#  whole picture, and the body under them is the panel it is built on.
DISPLAY = ("waveform", "spectrum", "plot", "logview", "adsr", "curve", "xy",
           "keybed", "slider", "meter", "radio", "knob", "scroll", "group",
           "panel")

#: the kinds a bare plate and a word still describe: nothing here draws inside
#  the plate, because a toggle is the plate
NO_FACE = ("text", "icon", "button", "toggle", "dropdown")


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


def placed(root, skin, kind, values=None, *, name="",
           box=(0, 0, 100, 100)) -> Widget:
    """A synth widget in the tree at a size, resolved in the same skin it paints.

    The sync is not ceremony: ``content_box()`` is measured from the inset the
    skin's padding and the plate's border left, and every coordinate the faces
    and the behaviours read is the content box - a test that laid out in one skin
    and painted in another would be asserting on a frame no real pass produces.
    """
    held = Widget(kind, values, name=name, parent=root)
    held.place(*box)
    root.sync(skin)
    return held


def paint(held, skin) -> RecordingRenderer:
    """One widget, alone, inside a frame the renderer contract will accept."""
    held.root().sync(skin)
    drawn = RecordingRenderer()
    drawn.begin(360, 220, 360, 220)
    held.paint(drawn, skin)
    return drawn


def look(held, skin) -> Any:
    """The appearance the skin hands this widget for the state it is in now."""
    return held.style(skin).resolve(held.role, held.state_name())


def along(held, across: float, up: float = 0.5) -> tuple:
    """A point at a fraction of the widget's own *content* box, in pixels."""
    left, top, width, height = held.content_box()
    return left + width * across, top + height * up


def midi(stage) -> List[tuple]:
    """Every MIDI message the fake heard since the last flush, in order."""
    stage.runtime.flush()
    return list(stage.lib.midi)


# -- the tables themselves ------------------------------------------------

def test_a_face_is_registered_for_exactly_the_kinds_that_draw_a_picture():
    assert tuple(FACES) == DISPLAY
    for word in DISPLAY:
        assert face_for(word) is FACES[word], word
    for word in NO_FACE:
        assert face_for(word) is None, word


def test_a_variant_wears_its_parents_face_the_way_it_inherits_its_props():
    """``WIDGET_DEFINE("scope", "waveform")`` has no reason to be a bare plate."""
    define("scope", "waveform")
    assert face_for("scope") is FACES["waveform"]
    define("shaper", "curve")
    assert face_for("shaper") is FACES["curve"]
    # the catalog's own two chain wearers: a vslider is a slider read top-down,
    # and a preset bar is a radio wearing the selection role's accent
    assert face_for("vslider") is FACES["slider"]
    assert face_for("preset_bar") is FACES["radio"]


@pytest.mark.parametrize("kind", DISPLAY[:6])
def test_a_synth_display_kind_twins_to_no_pd_box_and_owns_no_emit_row(kind):
    """The six synth faces twin to no Pd box: ``pd_object`` is empty.

    ``PD_EMIT`` maps a widget to a real Pd control through ``pd_object``, and a
    scope, an analyser, an envelope, a curve and a pad have no vanilla
    equivalent - so they declare ``pd_object ""`` and no synth row carries a
    ``pd_name``. The data path is the script's own: an ``ON_CHANGE`` into a
    ``PD_SEND``, or ``PD_MIDI_NOTEON`` for the keybed. (The ``preset_bar`` is a
    ``radio`` and does inherit its box, so it is proved separately below.)
    """
    held = get_kind(kind)
    assert held.pd_object == "", kind
    assert pd_object_for(held, {}) == "", kind
    synth_rows = {"data", "points", "bins", "attack", "decay", "sustain",
                  "release", "tension", "x", "y", "low", "octaves",
                  "channel", "velocity"}
    # the synth rows are the widget's own bookkeeping; none of them is a Pd
    # inlet/outlet name, and PD_EMIT only fires on rows that carry one
    assert not any(row.pd_name for row in held.props.values()
                   if row.name in synth_rows), kind


def test_a_preset_bar_inherits_the_radio_box_it_twins_to():
    """The one synth kind that is a control: same cells, same Pd ``hradio``."""
    bar = get_kind("preset_bar")
    assert bar.pd_object == get_kind("radio").pd_object != ""


def test_the_faced_controls_still_twin_to_the_boxes_their_kind_names():
    """A face is a drawing, not a new Pd object: the range kinds keep their own.

    The knob is the pointed case - vanilla Pd 0.54 ships no dial, so the kind
    is a slider the skin draws round and its twin stays ``[hslider]``, the box
    ``PD_EMIT`` already knows how to lay out.
    """
    for kind, box in (("slider", "hslider"), ("vslider", "vslider"),
                      ("knob", "hslider"), ("meter", "vu"),
                      ("radio", "radio")):
        assert pd_object_for(get_kind(kind), {}) == box, kind


def test_the_adsr_and_keybed_carry_a_value_but_the_pads_and_graphs_do_not():
    for kind in ("adsr", "keybed", "preset_bar"):
        assert Widget(kind).has_value is True, kind
    for kind in ("curve", "xy", "waveform", "spectrum"):
        assert Widget(kind).has_value is False, kind
    assert pd_arguments("adsr", {"value": 2.0}) == {"value": 2.0}


# -- the displays: an empty table is silence, a filled one is the numbers ---

def test_a_waveform_with_nothing_in_it_is_the_flat_line_of_silence(root, skin):
    held = placed(root, skin, "waveform", name="scope")
    drawn = paint(held, skin)
    assert drawn.count("polyline") == 0
    said = drawn.last("line")
    left, top, width, height = held.content_box()
    assert (said.get("x1"), said.get("x2")) == (left, left + width)
    assert said.get("y1") == said.get("y2") == top + height * 0.5
    assert said.get("color") == look(held, skin).ink


def test_a_waveform_plots_the_samples_it_was_handed_across_the_plate(root, skin):
    """-1 is the floor, +1 the ceiling, and the samples spread over the width."""
    held = placed(root, skin, "waveform", {"data": [-1.0, 0.0, 1.0]})
    drawn = paint(held, skin)
    said = drawn.last("polyline")
    left, top, width, height = held.content_box()
    points = said.get("points")
    assert len(points) == 3
    assert points[0][0] == pytest.approx(left)
    assert points[-1][0] == pytest.approx(left + width)
    assert points[0][1] == pytest.approx(top + height), "-1 sits at the floor"
    assert points[-1][1] == pytest.approx(top), "+1 reaches the ceiling"
    assert points[1][1] == pytest.approx(top + height * 0.5)


def test_the_points_row_shows_only_a_window_of_the_buffer(root, skin):
    held = placed(root, skin, "waveform",
                  {"data": [0.5, -0.5, 0.5, -0.5], "points": 2})
    assert len(paint(held, skin).last("polyline").get("points")) == 2


def test_a_sample_past_full_scale_is_clamped_not_allowed_to_leave_the_plate(
        root, skin):
    """A ``data`` prop is a ``SET`` away from anything, so the face does not trust it."""
    held = placed(root, skin, "waveform", {"data": [2.0, -2.0]})
    points = paint(held, skin).last("polyline").get("points")
    left, top, width, height = held.content_box()
    assert points[0][1] == pytest.approx(top), "+2 is clamped to +1, the ceiling"
    assert points[1][1] == pytest.approx(top + height), "-2 to the floor"


def test_a_spectrum_grows_one_bar_per_magnitude_from_the_floor(root, skin):
    held = placed(root, skin, "spectrum", {"data": [0.0, 0.5, 1.0]})
    drawn = paint(held, skin)
    left, top, width, height = held.content_box()
    # a bar is a per-bin rect standing on the content floor and narrower than the
    # plate it grows in, which is what separates three bins from the plate's own rect
    bars = [op for op in drawn.ops_named("rect")
            if abs(op.get("y") + op.get("height") - (top + height)) < 1e-6
            and op.get("width", width) < width]
    assert len(bars) == 3, "one per bin, all standing on the same floor"
    assert bars[0].get("height") == 0.0
    assert bars[1].get("height") == pytest.approx(height * 0.5)
    assert bars[2].get("height") == pytest.approx(height)
    assert bars[0].get("x") >= left, "the first bar is inside the plate"
    assert bars[-1].get("x") < left + width


def test_a_spectrum_with_nothing_computed_draws_no_bars(root, skin):
    """Empty ``data`` is silence: only the plate, never an invented row of bars."""
    held = placed(root, skin, "spectrum", name="analyst")
    drawn = paint(held, skin)
    left, top, width, height = held.content_box()
    bars = [op for op in drawn.ops_named("rect")
            if abs(op.get("y") + op.get("height") - (top + height)) < 1e-6
            and op.get("width", width) < width]
    assert bars == [], "not one per-bin rect was drawn for a blank table"


def test_the_bins_row_caps_the_bars_at_the_count_the_widget_asks_for(root, skin):
    held = placed(root, skin, "spectrum", {"data": [1.0, 1.0, 1.0], "bins": 2})
    left, top, width, height = held.content_box()
    drawn = paint(held, skin)
    bars = [op for op in drawn.ops_named("rect")
            if abs(op.get("y") + op.get("height") - (top + height)) < 1e-6
            and op.get("width", width) < width]
    assert len(bars) == 2, "a row of empty bars would be a lie about a spectrum"


# -- the envelope, the curve and the pad ----------------------------------

def test_the_envelope_is_four_segments_off_the_props_with_the_stage_lit(
        root, skin):
    held = placed(root, skin, "adsr", {"attack": 1.0, "decay": 1.0,
                                       "sustain": 0.5, "release": 1.0})
    drawn = paint(held, skin)
    left, top, width, height = held.content_box()
    points = drawn.last("polyline").get("points")
    assert points[0] == pytest.approx((left, top + height)), "starts at the floor"
    assert points[1][1] == pytest.approx(top), "attack climbs to the peak"
    assert points[2][1] == pytest.approx(top + height * 0.5), "decay to sustain"
    # the sustain segment holds the level (y) but still advances along x, so the
    # widths are proportional to the stage times (a/d/s/r = 1/1/0.5/1 here)
    assert points[3][1] == pytest.approx(points[2][1]), "sustain holds the level"
    assert points[3][0] > points[2][0], "...and still marches across the plate"
    assert points[-1] == pytest.approx((left + width, top + height)), "r to floor"
    dot = drawn.last("circle")
    assert dot.get("color") == skin.resolve("selection").fill
    # value 0 is attack, and the attack runs x0 (1.0) to x1 (29.0): its midpoint
    # is where the stage dot sits, which is the whole reason a value is a stage
    assert dot.get("cx") == pytest.approx((points[0][0] + points[1][0]) / 2), \
        "the value picks which segment the dot lights"


def test_moving_the_envelope_value_moves_the_dot_along_its_stage(root, skin):
    held = placed(root, skin, "adsr", {"attack": 1.0, "decay": 1.0,
                                       "sustain": 0.5, "release": 1.0})
    left, top, width, height = held.content_box()
    # with 1,1,0.5,1 the widths fall at x = 1, 29, 57, 71, 99; release is the
    # 71..99 span, so its midpoint is 85 and it runs down to the floor
    held.set("value", 3.0)
    dot = paint(held, skin).last("circle")
    assert dot.get("cx") == pytest.approx(85.0), "the release stage, not the attack"
    # the dot rides the middle of the stage's segment: on release the segment
    # runs from the sustain level (50) down to the floor (99), so its midpoint
    # is (50 + 99) / 2 = 74.5 - not the floor itself
    assert dot.get("cy") == pytest.approx(74.5), "release sits mid-descent"


def test_the_curve_bends_toward_its_tension_and_is_flat_at_zero(root, skin):
    held = placed(root, skin, "curve", {"tension": 1.0})
    left, top, width, height = held.content_box()
    points = paint(held, skin).last("polyline").get("points")
    middle = top + height * 0.5
    assert points[0] == pytest.approx((left, middle)), "the ends are pinned"
    assert points[-1] == pytest.approx((left + width, middle))
    # a positive tension pushes the control point *down* the screen (y grows
    # downward), so the apex dips; the sign is the curve's and not a preference
    assert max(p[1] for p in points) > middle, "a positive bend dips the apex"

    held.set("tension", -1.0)
    points = paint(held, skin).last("polyline").get("points")
    assert min(p[1] for p in points) < middle, "and a negative one lifts it"

    held.set("tension", 0.0)
    points = paint(held, skin).last("polyline").get("points")
    assert all(p[1] == pytest.approx(middle) for p in points), "0 is a straight line"


def test_the_pad_draws_guides_through_the_dot_at_the_two_coordinates(root, skin):
    held = placed(root, skin, "xy", {"x": 0.25, "y": 0.75})
    drawn = paint(held, skin)
    left, top, width, height = held.content_box()
    dot = drawn.last("circle")
    assert dot.get("cx") == pytest.approx(left + width * 0.25)
    assert dot.get("cy") == pytest.approx(top + height * 0.25), "0 y is the floor"
    guides = drawn.ops_named("line")
    assert len(guides) == 2, "one down, one across, through the dot"
    assert guides[0].get("x1") == dot.get("cx")
    assert guides[1].get("y1") == dot.get("cy")
    assert guides[0].get("color") == skin.resolve("grid", "hover").ink


# -- the keybed: it is drawn as keys, not as stripes ----------------------

def test_a_keybed_draws_naturals_across_the_plate_with_no_word_over_them(
        root, skin):
    held = placed(root, skin, "keybed", {"low": 60, "octaves": 1})
    drawn = paint(held, skin)
    found = look(held, skin)
    left, top, width, height = held.content_box()
    assert drawn.count("text") == 0, "words over keys make the keys unreadable"
    naturals = [op for op in drawn.ops_named("rect")
                if op.get("color") == found.ink]
    assert len(naturals) == 7, "C D E F G A B above C4"
    assert naturals[0].get("height") == pytest.approx(height)
    assert naturals[0].get("x") == pytest.approx(left)
    assert naturals[-1].get("x") + naturals[-1].get("width") == \
        pytest.approx(left + width)
    # the accidentals hang on the boundaries and reach only 60% down; the plate
    # shares the fill colour, so a key is a *short* rect, not the full-height box
    blacks = [op for op in drawn.ops_named("rect")
              if op.get("color") == found.fill
              and op.get("height") < height * 0.9]
    assert len(blacks) == 5, "C# D# F# G# A# all have a natural to their left"
    assert blacks[0].get("height") == pytest.approx(height * 0.6)


def test_the_held_key_is_repainted_in_the_accent_before_pd_has_answered(root, skin):
    """The press must be visible without waiting for a round-trip through Pd."""
    held = placed(root, skin, "keybed", {"low": 60, "octaves": 1})
    held.set("value", 62.0)  # D, the second natural
    drawn = paint(held, skin)
    accent = skin.resolve("selection").fill
    left, top, width, height = held.content_box()
    lit = [op for op in drawn.ops_named("rect") if op.get("color") == accent]
    assert len(lit) == 1
    assert lit[0].get("x") == pytest.approx(left + width / 7.0)


def test_a_preset_bar_is_a_radio_in_its_paint_as_well_as_its_gesture(root, skin):
    """No face of its own: the radio's cells, on the selection role's plate."""
    held = placed(root, skin, "preset_bar", {"labels": ["a", "b", "c"]})
    assert face_for(held.kind) is FACES["radio"]
    assert held.role == "selection"
    drawn = paint(held, skin)
    assert drawn.count("polyline") == 0, "a preset bar draws no graph"
    assert drawn.count("circle") == 0
    # the accent *is* the selection role's fill, drawn by base_face's plate under
    # the cells: the bar changes what a radio looks like, never how it is painted
    plate = [op for op in drawn.ops_named("rect")
             if op.get("color") == skin.resolve("selection").fill]
    assert plate, "the plate is still the first thing on the frame"
    assert plate[0].get("x") == pytest.approx(held.screen_box[0])


# -- the faced controls: a picture the numbers can be read off of ----------


def test_a_slider_draws_its_thumb_at_its_value(root, skin):
    """The thumb is the value: the fill runs from the zero end (the left) to
    the thumb's near edge, and the word goes on over the track."""
    held = placed(root, skin, "slider", {"label": "BPM", "min": 0, "max": 100,
                                         "value": 25},
                  box=(56, 40, 200, 24))
    drawn = paint(held, skin)
    left, _top, width, _height = held.content_box()
    fill = skin.resolve("fill").fill
    thumbc = skin.resolve("thumb").fill
    rects = drawn.ops_named("rect")
    bar = [op for op in rects if op.get("color") == fill]
    thumb = [op for op in rects if op.get("color") == thumbc]
    assert len(bar) == 1 and len(thumb) == 1
    assert bar[0].get("x") == pytest.approx(left), "the fill starts at the left"
    assert 0.0 < bar[0].get("width") < width
    assert thumb[0].get("x") == pytest.approx(bar[0].get("x")
                                              + bar[0].get("width")), \
        "the thumb rides on the end of the fill"
    assert drawn.last("text").get("text") == "BPM"


def test_a_slider_thumb_runs_from_the_left_end_at_zero_to_the_right_at_full(
        root, skin):
    """Two paints, one number apart: the slab's near edge is the value's pixel."""
    held = placed(root, skin, "slider", {"min": 0, "max": 100, "value": 0},
                  box=(56, 40, 200, 24))
    left, _top, width, _height = held.content_box()
    thumbc = skin.resolve("thumb").fill

    def thumb():
        return [op for op in paint(held, skin).ops_named("rect")
                if op.get("color") == thumbc][0]

    low = thumb()
    assert low.get("x") == pytest.approx(left), "zero parks the thumb at the left"
    held.set("value", 100)
    high = thumb()
    assert low.get("x") < high.get("x"), "the thumb follows the value right"
    assert high.get("x") + high.get("width") == pytest.approx(left + width)


def test_a_vslider_is_the_same_picture_read_top_down(root, skin):
    """The chain gives it the slider's face; the face gives it its own axis.

    On a vertical box the zero end is the floor: the fill grows up from it and
    the thumb rides on top of the fill, so the fill's bottom is the box's bottom
    - the exact opposite of the horizontal case, proved in pixels.
    """
    held = placed(root, skin, "vslider", {"label": "pan", "min": -1, "max": 1,
                                          "value": 0},
                  box=(220, 60, 24, 100))
    drawn = paint(held, skin)
    _left, top, _width, height = held.content_box()
    fill = skin.resolve("fill").fill
    thumbc = skin.resolve("thumb").fill
    bar = [op for op in drawn.ops_named("rect") if op.get("color") == fill][0]
    thumb = [op for op in drawn.ops_named("rect")
             if op.get("color") == thumbc][0]
    assert bar.get("y") + bar.get("height") == pytest.approx(top + height), \
        "the fill is grown from the floor"
    assert bar.get("y") == pytest.approx(thumb.get("y") + thumb.get("height")), \
        "the thumb sits on the top edge of the fill"
    assert bar.get("height") > 0.0, "half of a -1..1 pan fills half the travel"


def test_a_meter_grows_from_its_zero_end_and_says_its_name(root, skin):
    held = placed(root, skin, "meter", {"label": "beat", "min": 0, "max": 1,
                                        "value": 0.5},
                  box=(20, 40, 24, 80))
    drawn = paint(held, skin)
    _left, top, _width, height = held.content_box()
    level = [op for op in drawn.ops_named("rect")
             if op.get("color") == skin.resolve("fill").fill]
    assert len(level) == 1, "a neutral plate, one level bar"
    assert level[0].get("height") == pytest.approx(height * 0.5, abs=1.0), \
        "a vertical strip fills from its floor upward"
    assert level[0].get("y") + level[0].get("height") == \
        pytest.approx(top + height)
    assert drawn.last("text").get("text") == "beat"


def test_a_radio_lights_the_cell_it_holds_in_the_grid_the_press_reads(root, skin):
    """Cell arithmetic shared with the hit test: the drawn cell is the pressable.

    The held index wears the accent; the rest stay raised. The face lays its own
    neutral plate first (a radio holding a non-first index would otherwise paint
    the whole accent checkbox plate and swallow the unlit cells), so the lit cell
    is found among the *cell-sized* rects, not the plate.
    """
    held = placed(root, skin, "radio", {"labels": ["sine", "tri", "saw"],
                                        "columns": 3, "value": 1},
                  box=(10, 10, 150, 40))
    drawn = paint(held, skin)
    left, _top, width, _height = held.content_box()
    accent = skin.resolve("fill").fill
    cells = [op for op in drawn.ops_named("rect") if op.get("width") < width * 0.6]
    lit = [op for op in cells if op.get("color") == accent]
    assert len(lit) == 1, "one held index, one lit cell"
    # the lit cell is the middle third, the same third a press at 0.5 of the
    # box lands in (behaviour._cell_at's arithmetic, in pixels)
    cell = lit[0]
    assert cell.get("x") <= left + width * 0.5 <= cell.get("x") + cell.get("width")
    said = {op.get("text") for op in drawn.ops_named("text")}
    assert said == {"sine", "tri", "saw"}, "every choice is written once"


def test_a_knob_turns_its_pointer_with_its_value(root, skin):
    """The dial: ticks outside, ring under the value, pointer at the angle.

    The sweep is three quarters of a turn starting at the lower left, so the
    pointer at rest points at 7 and at full swings to the lower right - and the
    ring is an arc, never a closed circle, because a closed ring has no gap to
    read the value off.
    """
    held = placed(root, skin, "knob", {"label": "Index", "min": 0, "max": 12,
                                       "value": 0},
                  box=(24, 24, 96, 96))
    drawn = paint(held, skin)
    assert drawn.count("circle") == 1, "the dial's face"
    assert drawn.count("arc") == 1, "the empty ring; no value arc at zero"
    ticks = drawn.ops_named("line")
    assert len(ticks) == 12, "ten tick intervals and the pointer"
    start, sweep = 0.375 * math.tau, 0.75 * math.tau
    held.set("value", 12)
    turned = paint(held, skin).ops_named("line")[-1]
    got = math.atan2(turned.get("y2") - turned.get("y1"),
                     turned.get("x2") - turned.get("x1")) % math.tau
    assert got == pytest.approx((start + sweep) % math.tau, abs=0.02)
    assert paint(held, skin).count("arc") == 2, "a full value arc at the top"


def test_a_knob_draws_its_label_and_its_readout(root, skin):
    held = placed(root, skin, "knob", {"label": "Index", "min": 0, "max": 12,
                                       "value": 7},
                  box=(24, 24, 96, 96))
    said = [op.get("text") for op in paint(held, skin).ops_named("text")]
    assert said == ["Index", "7"], "the word it was given, the number it holds"


# -- placement: a name pushed outside a box too small to hold it ------------


def _word(drawn, text):
    return [op for op in drawn.ops_named("text") if op.get("text") == text]


def test_a_tiny_knob_puts_its_name_above_the_box_and_still_turns(root, skin):
    """The 25x25 case the notes complained about, in pixels.

    With the name on ``top`` the dial keeps the whole box (no strip is reserved
    inside it), and the word is drawn above the screen box - yet still inside the
    widget's grown draw-clip, so a real scissor would not cut it. The hit rectangle
    stays the 25x25 box, so the grown clip never reaches into a neighbour.
    """
    held = placed(root, skin, "knob", {"label": "Index", "min": 0, "max": 12,
                                       "value": 7, "label_place": "top",
                                       "value_place": "none"},
                  box=(120, 120, 25, 25))
    drawn = paint(held, skin)
    sl, st, _sw, _sh = held.screen_box
    name = _word(drawn, "Index")
    assert len(name) == 1, "the name is drawn once, outside"
    label = name[0]
    assert label.get("y") < st, "the word sits above the box, not in it"
    # the draw-clip grew upward to reach the word: its top is at/above the word
    assert label.clip[1] <= label.get("y") + 1e-6, "not clipped away"
    # and a full-size dial is still there: the tiny box was not given up on
    assert drawn.count("circle") >= 1, "the dial keeps the whole box to turn in"
    assert _word(drawn, "7") == [], "value_place=none hides the readout"


def test_a_name_aimed_off_the_default_edge_moves_with_the_offset(root, skin):
    """``label_dx``/``label_dy`` are the escape hatch when a bare edge is close."""
    held = placed(root, skin, "knob", {"label": "Index", "min": 0, "max": 12,
                                       "value": 0, "label_place": "top",
                                       "value_place": "none"},
                  box=(120, 120, 40, 40))
    bare = _word(paint(held, skin), "Index")[0]
    held.set("label_dy", -12.0)
    held.set("label_dx", 9.0)
    nudged = _word(paint(held, skin), "Index")[0]
    assert nudged.get("y") == pytest.approx(bare.get("y") - 12.0), \
        "a negative dy lifts the word further above the box"
    assert nudged.get("x") == pytest.approx(bare.get("x") + 9.0), \
        "dx slides it along the top edge"


def test_an_inside_default_still_writes_the_word_in_the_box(root, skin):
    """Nobody opted out: the word is where it always was, above the dial, inside."""
    held = placed(root, skin, "knob", {"label": "Index", "min": 0, "max": 12,
                                       "value": 7},
                  box=(24, 24, 96, 96))
    left, top, width, height = held.content_box()
    name = _word(paint(held, skin), "Index")[0]
    assert top <= name.get("y") < top + height, "the caption stayed inside"
    assert name.clip == held._clip, "an inside word grows no clip"


def test_a_meter_lets_its_name_leave_a_narrow_box_to_the_left(root, skin):
    """The plate-and-label path agrees with the knob faces about ``left``."""
    held = placed(root, skin, "meter", {"label": "beat", "min": 0, "max": 1,
                                        "value": 0.5, "label_place": "left"},
                  box=(200, 40, 24, 80))
    drawn = paint(held, skin)
    sl, _st, _sw, _sh = held.screen_box
    name = _word(drawn, "beat")[0]
    assert name.get("x") < sl, "the word is drawn to the left of the strip"
    assert name.get("align") == "right"
    assert name.clip[0] <= name.get("x"), "and inside the grown clip"


# -- the canvases: a plot graphs any list, a log view scrolls text -----------


def test_a_plot_stretches_its_own_span_over_the_plate(root, skin):
    """A plot has no full scale: 0 is the floor, 12 the ceiling, because it says so.

    The modindex array the fm_synth graphs runs 0..12, nowhere near a waveform's
    -1..+1, so the tallest value reaches the top of the plate and the lowest the
    bottom - the array's own min and max are the axis.
    """
    held = placed(root, skin, "plot", {"data": [0.0, 6.0, 12.0]})
    left, top, width, height = held.content_box()
    points = paint(held, skin).last("polyline").get("points")
    assert len(points) == 3
    assert points[0][0] == pytest.approx(left)
    assert points[-1][0] == pytest.approx(left + width)
    assert points[0][1] == pytest.approx(top + height), "0 is the floor"
    assert points[-1][1] == pytest.approx(top), "the max reaches the ceiling"
    assert points[1][1] == pytest.approx(top + height * 0.5)


def test_a_plot_of_a_flat_series_is_a_line_and_not_a_divide_by_zero(root, skin):
    held = placed(root, skin, "plot", {"data": [5.0, 5.0, 5.0]})
    left, top, width, height = held.content_box()
    points = paint(held, skin).last("polyline").get("points")
    ys = [p[1] for p in points]
    assert all(y == pytest.approx(ys[0]) for y in ys), "a flat run is straight"
    assert top <= ys[0] <= top + height, "and stays inside the plate"


def test_a_plot_with_nothing_held_draws_no_trace(root, skin):
    held = placed(root, skin, "plot", name="graph")
    assert paint(held, skin).count("polyline") == 0, "silence is not a graph"


def test_a_logview_shows_the_newest_lines_and_the_wheel_older_ones(root, skin):
    rows = [f"line {i}" for i in range(40)]
    held = placed(root, skin, "logview", {"lines": rows},
                  box=(10, 10, 200, 60))
    shown = paint(held, skin).texts()
    assert shown, "a log with rows draws rows"
    assert shown[-1] == "line 39", "scroll 0 parks the newest line at the bottom"
    assert all(line in rows for line in shown), "only real rows are drawn"
    assert len(shown) <= len(rows)
    # scrolling up from the newest pulls older lines into the window
    held.set("scroll", 20)
    moved = paint(held, skin).texts()
    assert moved[-1] != "line 39", "the window left the newest behind"
    assert moved[-1] == "line 19", "scrolling counts whole lines up from the tail"


def test_the_logviews_arrow_keys_move_one_line_and_its_pages_move_ten(root, skin):
    """An arrow is a line and a page is ten, which a key cap cannot tell you.

    Asserted on a log view because this is the widget where the two are easiest to
    mix up: both are counted in the same ``scroll`` prop, and a press of ``up`` that
    answered the page instead would throw the window thirty-nine lines away from the
    line the person was reading without drawing a single pixel that says so.
    """
    rows = [f"line {i}" for i in range(40)]
    held = placed(root, skin, "logview", {"lines": rows}, box=(10, 10, 200, 60))
    assert float(held.get("scroll")) == 0.0, "the newest line is the window's bottom"
    held.on_key("up")
    assert float(held.get("scroll")) == 1.0
    held.on_key("up")
    assert float(held.get("scroll")) == 2.0, "one line per press, not one page"
    held.on_key("down")
    assert float(held.get("scroll")) == 1.0
    held.on_key("page up")
    assert float(held.get("scroll")) == 11.0
    held.on_key("page down")
    assert float(held.get("scroll")) == 1.0
    held.on_key("end")
    assert float(held.get("scroll")) == 39.0, "the oldest line, and no further"
    held.on_key("up")
    assert float(held.get("scroll")) == 39.0, "the end is a clamp, not an error"
    held.on_key("home")
    assert float(held.get("scroll")) == 0.0


# -- gestures: what pressing and dragging each picture means --------------

@pytest.mark.parametrize("across,stage", [(0.05, 0.0), (0.30, 1.0),
                                            (0.55, 2.0), (0.90, 3.0)])
def test_pressing_a_quarter_of_the_envelope_picks_that_stage(root, skin,
                                                              across, stage):
    """The four quarters always answer, even before a patch has set a time."""
    held = placed(root, skin, "adsr")
    held.on_press(*along(held, across))
    assert held.value == stage, "the quarter of the box names the stage"


def test_a_press_on_the_envelope_lands_on_the_stage_and_height_you_hit(root, skin):
    held = placed(root, skin, "adsr", {"sustain": 0.2})
    held.on_press(*along(held, 0.75))
    assert held.value == 3.0, "the release quarter"
    assert held.get("release") == pytest.approx(0.5), "the height of the press"
    assert held.take_changes() == ["value", "release"]


def test_a_vertical_drag_moves_only_the_stage_you_are_holding(root, skin):
    held = placed(root, skin, "adsr", {"attack": 1.0, "decay": 1.0,
                                       "sustain": 0.5, "release": 1.0})
    held.on_press(*along(held, 0.75, up=0.5))
    assert held.value == 3.0 and held.get("release") == pytest.approx(0.5)
    held.on_drag(0.0, -held.content_box()[3] * 0.25, 0)
    assert held.get("release") == pytest.approx(0.75), "up raises the held stage"
    assert held.get("attack") == 1.0, "the other stages were not touched"
    assert held.get("decay") == 1.0 and held.get("sustain") == pytest.approx(0.5)


def test_the_envelope_keys_step_through_the_stages_without_moving_a_level(
        root, skin):
    held = placed(root, skin, "adsr", {"attack": 1.0, "decay": 1.0,
                                       "sustain": 0.5, "release": 1.0})
    held.on_key("end")
    assert held.value == 3.0
    held.on_key("left")
    assert held.value == 2.0
    held.on_key("home")
    assert held.value == 0.0
    assert held.get("attack") == 1.0, "a stage change is not a level change"


def test_a_press_on_the_curve_sets_tension_from_the_height_it_hit(root, skin):
    held = placed(root, skin, "curve")
    held.on_press(*along(held, 0.5, up=0.0))
    assert held.get("tension") == pytest.approx(1.0), "the top of the box is +1"
    held.on_press(*along(held, 0.5, up=1.0))
    assert held.get("tension") == pytest.approx(-1.0), "the bottom is -1"
    held.on_press(*along(held, 0.5, up=0.5))
    assert held.get("tension") == pytest.approx(0.0)


def test_a_curve_drag_is_a_relative_move_and_not_a_jump(root, skin):
    held = placed(root, skin, "curve")
    held.on_press(*along(held, 0.5, up=0.5))
    assert held.get("tension") == pytest.approx(0.0)
    held.on_drag(0.0, -held.content_box()[3] * 0.25, 0)
    assert held.get("tension") == pytest.approx(0.5)


def test_a_press_on_the_pad_reads_both_axes_at_once(root, skin):
    held = placed(root, skin, "xy")
    held.on_press(*along(held, 0.25, up=0.75))
    assert (held.get("x"), held.get("y")) == (pytest.approx(0.25),
                                              pytest.approx(0.25))
    held.on_drag(10.0, -10.0, 0)
    length = held.content_box()
    assert held.get("x") == pytest.approx(0.25 + 10.0 / length[2])
    assert held.get("y") == pytest.approx(0.25 + 10.0 / length[3])


def test_a_press_on_the_keybed_is_a_pitch_and_a_release_is_silence(root, skin):
    held = placed(root, skin, "keybed", {"low": 60, "octaves": 1})
    held.on_press(*along(held, 0.05))
    assert held.value == 60.0, "the bottom-most natural is low"
    held.on_press(*along(held, 0.5))
    assert held.value == 65.0, "the middle of seven naturals is the fourth (F)"
    held.on_press(*along(held, 0.99))
    assert held.value == 71.0, "and the far end is B above middle C"
    held.on_release(*along(held, 0.99))
    assert held.value == 0.0, "no key, which is what a note-off reads as"


def test_a_preset_bar_press_is_a_cell_index_exactly_like_a_radio(root, skin):
    held = placed(root, skin, "preset_bar",
                  {"labels": ["a", "b", "c"], "columns": 3})
    held.on_press(*along(held, 0.10))
    assert held.value == 0.0
    held.on_press(*along(held, 0.50))
    assert held.value == 1.0
    held.on_press(*along(held, 0.90))
    assert held.value == 2.0
    # a declaration of its own rather than a re-use of radio's dict, yet the same
    # handlers: behaviour_for reaches it by name, and the cell arithmetic is equal
    assert behaviour_for("preset_bar") is BEHAVIOURS["preset_bar"]
    assert set(BEHAVIOURS["preset_bar"]) == set(BEHAVIOURS["radio"])


# -- the round trip: a keybed that promises Pd a note ---------------------

#: a synth front panel in the plan's own five-step shape, with no copy of the
#  metronome example anywhere: the widget half is the S9 kinds and the Pd half is
#  the one data path a keybed has - ``ON_CHANGE`` into ``PD_MIDI_NOTEON``.
KEYBED_SCRIPT = (
    "VAR keys = KEYBED(\"keys\", 60, 1)\n"
    "VAR env  = ADSR(\"env\", 10, 20, 0.5, 30)\n"
    "VAR pan  = XY_PAD(\"pan\")\n"
    "PLACE(keys, 16, 120, 320, 80)\n"
    "PLACE(env,  16, 16,  120, 60)\n"
    "PLACE(pan,  160, 16, 80, 60)\n"
    "FUNC on_pitch(v)\n"
    "    IF v > 0 THEN\n"
    "        PD_MIDI_NOTEON(GET(keys, \"channel\"), v, GET(keys, \"velocity\"))\n"
    "    ELSE\n"
    "        PD_MIDI_NOTEOFF(GET(keys, \"channel\"), 0)\n"
    "    END\n"
    "END\n"
    "ON_CHANGE(keys, on_pitch)\n"
)


def _panel(tmp_path, name: str, painting: bool = True):
    """Run a synth script once against FakeLibpd and a recording renderer."""
    home = tmp_path / name
    home.mkdir()
    lib = FakeLibpd()
    session = PdSession(lib)
    session.start()
    notes: List[str] = []
    runtime = PdRuntime(session, project_dir=str(home), log=notes.append)
    renderer = RecordingRenderer()
    ui = UIContext(renderer, width=360, height=220, painting=painting)
    program = Program("<synth>", host=types.SimpleNamespace(pd=runtime, ui=ui))

    def say(source: str) -> Any:
        value, error = program.run(source)
        assert error is None, error.as_string()
        return as_py(value)

    say(KEYBED_SCRIPT)
    ui.layout()
    yield types.SimpleNamespace(lib=lib, session=session, runtime=runtime,
                                renderer=renderer, ui=ui, program=program,
                                notes=notes, say=say)
    session.shutdown()


def as_py(value: Any) -> Any:
    """A language value as the plain Python object it stands for."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, CrudeList):
        return [as_py(item) for item in value.elements]
    if isinstance(value, HostHandle):
        return value.target
    return value


@pytest.fixture
def panel(tmp_path):
    """The synth panel in a window that is only a recording renderer."""
    yield from _panel(tmp_path, "windowed")


def test_the_script_builds_the_synth_kinds_the_words_name(panel):
    """A constructor word is sugar over the widget, and the tree is the proof."""
    keys, env, pan = (panel.say(n) for n in ("keys", "env", "pan"))
    assert [w.kind.name for w in (keys, env, pan)] == ["keybed", "adsr", "xy"]
    assert [w.path() for w in (keys, env, pan)] == ["root/keys", "root/env",
                                                    "root/pan"]
    assert keys.value == 0.0
    assert panel.say('GET(keys, "low")') == 60.0
    assert panel.say('GET(env, "sustain")') == 0.5


def test_the_synth_kinds_paint_their_faces_in_a_real_frame(panel):
    """A frame of the script's tree emits the picture, not just a plate."""
    keys = panel.say("keys")
    panel.renderer.reset()
    panel.ui.frame(dt=0.0)
    # the keybed drew its naturals as rects and no word over them
    assert any(op.name == "rect" for op in panel.renderer.ops)
    left, top, width, height = keys.screen_box
    inside = [op for op in panel.renderer.ops_named("rect")
              if op.get("x", 0.0) >= left - 1e-6
              and op.get("x") + op.get("width", 0.0) <= left + width + 1e-6]
    assert len(inside) >= 10, "seven naturals and three accidentals, plus plates"


def test_pressing_a_key_sends_a_note_on_to_fakepd(panel):
    """The plan's S9 Pd round-trip: a gesture lands on FakeLibpd as MIDI.

    The press is the round-trip that matters - a real patch remembers the last
    note-on and does the release itself, and the ``ON_CHANGE`` handler here only
    reads the value the widget reports (pitch on press, 0 on release). Proving
    the *pitch* channel works end-to-end is the S9 acceptance line; the release
    branch is checked for shape (a note-on at velocity 0 reached the fake).
    """
    keys = panel.say("keys")
    keys.on_press(*along(keys, 0.05))
    assert keys.value == 60.0
    assert midi(panel) == [("noteon", 1, 60, 100)], "channel 1, velocity 100"

    panel.lib.midi.clear()
    keys.on_release(*along(keys, 0.05))
    assert keys.value == 0.0, "no key - what a note-off reads as"
    said = midi(panel)
    assert len(said) == 1 and said[0][0] == "noteon" and said[0][3] == 0, \
        "Pd's note-off is a note-on at 0, whatever pitch the handler passed"


def test_a_note_on_a_headless_run_is_the_same_midi_and_no_pixels(tmp_path):
    """``--mode headless`` sends the identical numbers and draws nothing."""
    every = {}
    for label, painting in (("windowed", True), ("silent", False)):
        stage = next(_panel(tmp_path, f"keybed-{label}", painting=painting))
        keys = stage.say("keys")
        keys.on_press(*along(keys, 0.5))
        keys.on_release(*along(keys, 0.5))
        stage.ui.frame(dt=0.0)
        every[label] = stage

    assert every["silent"].lib.midi == every["windowed"].lib.midi
    assert every["windowed"].renderer.ops, "the window drew the keybed"
    assert every["silent"].renderer.ops == [], "headless drew nothing"
    assert every["silent"].say("keys").value == 0.0
