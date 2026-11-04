"""What a gesture means, as numbers: the plan's S8 assertions on the kinds.

:mod:`crude.ui.behaviour` is the table that answers "what happens when a person
presses, drags, scrolls or types on a thing of a given kind", and this file is the
plan's acceptance line for it: "numeric assertions per widget (mapping, scaling,
snap, typed entry)". Four groups, in the order the arithmetic has to be right in -
a value has to be *placed* on its range before it can be scaled along it, and both
have to be settled before a step or a typed number can land.

Everything is run against a bare widget - no :class:`~crude.ui.handlers.Handlers`,
no interpreter, no renderer - because that is the point of where the table lives:
the constructor installs it in the widget's own ``on_event`` slot, so the mapping
is a property of the kind and not of a running program. The last section earns its
place by showing the same numbers arrive down the real path, through an
:class:`~crude.ui.input.InputRouter` that hit tests and captures and coalesces
moves exactly as a GLFW frame would, and through ``adopt``, which is where a
script's ``ON_CLICK`` meets the behaviour it is chained in front of.

The constants are Pd's and not ours. ``20..300`` clicked at a quarter of its box
is ``90.0`` because Pd measures a range on the box it is drawn in
(``val = 100 * x / zoom`` in ``g_slider.c``), and a log range's midpoint is the
geometric mean and not the average - the two numbers a reviewer would otherwise
check by eye and get wrong.
"""

from __future__ import annotations

import pytest

from crude.ui import (BEHAVIOURS, Handlers, InputRouter, MODS, Skin, Widget,
                      behaviour_for, define, define_prop, fraction_of,
                      get_kind, install_behaviour, logarithmic, reset, root_panel,
                      span_of, top_at, typable, value_at)

#: the kinds the table answers a gesture for: the eight faces and controls,
#: plus the four synth widgets a pointer can actually edit, plus the viewport -
#: which edits no value of its own and moves its content instead - the group,
#: which edits no value either and answers a fold on its mark and a pickup on its
#: header, and the clip, which moves no value and edits the window and the marks
#: it is drawn from
INTERACTIVE = ("slider", "vslider", "knob", "number", "toggle", "button",
               "radio", "dropdown", "adsr", "curve", "xy", "keybed",
               "preset_bar", "scroll", "group", "clip")

#: the six it deliberately leaves still: the four that always were, and the two
#: displays whose every pixel is whatever a script last pushed into ``data``
STILL = ("panel", "text", "icon", "meter", "waveform", "spectrum")


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


def placed(root, kind, values=None, *, name="", box=(0, 0, 100, 24)) -> Widget:
    """A widget in the tree at a size: the geometry every mapping is measured on.

    A range with no box has no span to be measured along, so the layout is not
    setup - it is most of what a mapping test is asserting about. The skin is named
    here rather than passed in because the plate it draws and the padding it leaves
    are what decide where a range's ends land in pixels, and every number in this
    file is written against the one theme the plan's example uses.
    """
    held = Widget(kind, values, name=name, parent=root)
    held.place(*box)
    root.sync(Skin("dark"))
    return held


def along(held, fraction: float, up: float = 0.5) -> tuple:
    """A point at ``fraction`` of the widget's own box, away from its edges.

    Asked of the *content* box because that is the box the behaviour reads: a test
    that computed the pixel from ``screen_box`` would be measuring the range from
    the outside edge and would silently disagree with the drawing.
    """
    left, top, width, height = held.content_box()
    return left + width * fraction, top + height * up


def press(held, x: float, y: float, button: int = 0, mods: int = 0) -> None:
    """The pointer down at a point, as the router would send it."""
    held.on_press(x, y, button, mods)


# -- the table itself ---------------------------------------------------------


@pytest.mark.parametrize("kind", INTERACTIVE)
def test_every_kind_the_table_answers_for_is_one_the_catalog_ships(kind):
    assert kind in BEHAVIOURS, kind
    assert behaviour_for(get_kind(kind)) is BEHAVIOURS[kind]


@pytest.mark.parametrize("kind", STILL + INTERACTIVE)
def test_a_kind_either_answers_a_gesture_or_says_it_does_not(root, kind):
    """The table is complete over the catalog it knows: nothing is unmentioned.

    A ``panel`` holding still is a decision written down, and a ``slider`` - or
    a ``keybed`` - with no entry would be a bug this test catches right here.
    """
    held = Widget(kind, name=kind, parent=root)
    found = behaviour_for(held.kind)
    assert bool(found) == (kind in INTERACTIVE), kind
    if kind in INTERACTIVE:
        assert found is BEHAVIOURS[kind], "one dict, not a copy to drift"
    assert (held.on_event is not None) == (kind in INTERACTIVE), \
        "the constructor installed exactly what the table says about this kind"


def test_the_still_kinds_move_nothing_however_they_are_prodded(root, skin):
    parts = {}
    for word in STILL:
        held = Widget(word, {"label": "x"}, name=word, parent=root)
        held.place(10, 10, 60, 20)
        parts[word] = held
    root.sync(skin)
    for held in parts.values():
        left, top, width, height = held.content_box()
        held.on_press(left + 1, top + 1, 0, 0)
        held.on_release(left + 2, top + 2, 0, 0)
        held.on_click(left + 2, top + 2, 0)
        held.on_drag(20, 0, 0)
        held.on_scroll(0, -16, 0)
        held.on_key("up")
        held.on_key(" ")
        held.on_char("9")
        held.on_key("enter")
        if held.kind.has("value"):
            assert held.value == 0.0, held.kind.name
        assert held.take_changes() == []
        assert held.on_event is None, "nothing was ever installed for it"


def test_install_leaves_a_kind_with_no_behaviour_exactly_as_it_found_it(root):
    held = Widget("meter", name="led", parent=root)
    before = held.on_event
    assert install_behaviour(held) is None
    assert held.on_event is before and held._behaviour is None
    assert install_behaviour(Widget("slider")) is not None


# -- mapping: where on the box a value sits ----------------------------------


def test_a_quarter_of_the_box_is_a_quarter_of_the_range(root):
    """``20..300`` pressed one quarter along is ``90``, the plan's own arithmetic."""
    held = placed(root, "slider", {"label": "BPM", "min": 20, "max": 300,
                                   "value": 120}, name="bpm",
                  box=(56, 40, 200, 24))
    assert span_of(held) == (20.0, 300.0)
    press(held, *along(held, 0.25))
    assert held.value == pytest.approx(90.0)
    assert held.take_changes() == ["value"]


def test_the_ends_of_the_box_are_the_ends_of_the_range_and_past_them_is_still_there(
        root):
    held = placed(root, "slider", {"min": 20, "max": 300, "value": 120},
                  name="bpm", box=(56, 40, 200, 24))
    press(held, *along(held, 0.0))
    assert held.value == pytest.approx(20.0)
    press(held, *along(held, 1.4))
    assert held.value == pytest.approx(300.0), "a thumb stops at the stop"
    press(held, *along(held, -0.5))
    assert held.value == pytest.approx(20.0)


def test_a_drag_is_a_movement_and_not_a_position(root):
    """The router hands a drag a ``dx``, so it adds to where the value already is.

    This is the fact that makes a slider's first increment land on its own press:
    the press jumped to a quarter, the drag of another quarter ends at half, and no
    number in either step was a position on the screen.
    """
    held = placed(root, "slider", {"min": 20, "max": 300, "value": 120},
                  name="bpm", box=(56, 40, 200, 24))
    press(held, *along(held, 0.25))
    assert held.value == pytest.approx(90.0)
    held.on_drag(0.25 * held.content_box()[2], 0.0, 0)
    assert held.value == pytest.approx(160.0)
    held.on_drag(-0.25 * held.content_box()[2], 0.0, 0)
    assert held.value == pytest.approx(90.0), "back to where the press had put it"


def test_a_drag_outside_the_widget_still_moves_it_because_it_owns_the_pointer(root):
    held = placed(root, "slider", {"min": 0, "max": 100, "value": 50},
                  name="s", box=(10, 10, 100, 24))
    held.on_drag(1000.0, 0.0, 0)
    assert held.value == pytest.approx(100.0), \
        "the value clamps; the drag does not stop"


def test_a_range_that_was_never_laid_out_moves_nothing(root):
    """No box, no span, no mapping - and no invented answer to cover it up.

    The width is zero, so a fraction of it is a division the behaviour refuses
    rather than a number it rounds to something plausible. A press still reads as
    a press for the props it does not measure, and the keyboard, which needs no
    ruler, still works.
    """
    held = Widget("slider", {"min": 20, "max": 300, "value": 120})
    assert held.content_box()[2] == 0.0
    press(held, 250.0, 12.0)
    held.on_drag(40.0, 0.0, 0)
    assert held.value == 120.0
    held.on_key("up")
    assert held.value == pytest.approx(122.8), "a keyboard needs no geometry"


def test_a_vertical_range_runs_bottom_up(root):
    """The one thing that makes a ``vslider`` a different widget from a slider."""
    held = placed(root, "vslider", {"min": 0, "max": 100, "value": 0}, name="v",
                  box=(220, 60, 24, 100))
    press(held, *along(held, 0.5, up=0.0))
    assert held.value == pytest.approx(100.0), "the top of the box is its maximum"
    press(held, *along(held, 0.5, up=1.0))
    assert held.value == pytest.approx(0.0)
    held.on_drag(0.0, -0.25 * held.content_box()[3], 0)
    assert held.value == pytest.approx(25.0), "dragging up raises a level"


def test_a_knob_never_jumps_at_the_press_because_a_dial_has_no_such_position(root):
    """The one real difference between a knob and the slider it is built on.

    A thumb has a place under the pointer; a dial's pointer has an *angle the
    value already chose*, and a press that teleported there would overwrite a
    number on its way to being nudged. The drag is still the vslider's own
    vertical movement, measured on the same box.
    """
    held = placed(root, "knob", {"min": 0, "max": 12, "value": 7}, name="idx",
                  box=(24, 24, 96, 96))
    press(held, *along(held, 0.5, up=1.0))
    assert held.value == 7.0, "the press that lands moves nothing"
    held.on_drag(0.0, -0.25 * held.content_box()[3], 0)
    assert held.value == pytest.approx(10.0), "a quarter of the box is a quarter"
    held.on_drag(0.0, 0.5 * held.content_box()[3], 0)
    assert held.value == pytest.approx(4.0), "and it runs back down the same way"


def test_a_knob_answers_the_wheel_and_the_keys_but_refuses_the_typing(root):
    """``editable False`` is the whole answer for a dial: no digits at it.

    The keyboard and the wheel need no digits to be typed and no geometry to
    be measured on, and a knob keeps both - what a knob refuses is the number
    box's own door, because "typing 7 into a rotary" has no meaning.
    """
    held = placed(root, "knob", {"min": 0, "max": 12, "value": 7}, name="idx",
                  box=(24, 24, 96, 96))
    assert typable(held) is False
    held.on_char("9")
    held.on_key("enter")
    assert held.value == 7.0, "no digit buffer was ever opened"
    held.on_key("up")
    assert held.value == pytest.approx(7.12), "an arrow is still a hundredth"
    held.on_scroll(0.0, 16.0, 0)
    assert held.value == pytest.approx(7.24), "one notch, one more hundredth"


def test_a_number_box_is_measured_on_its_box_even_though_it_does_not_jump(root):
    held = placed(root, "number", {"min": 20, "max": 300, "value": 120},
                  name="field", box=(264, 40, 80, 24))
    held.on_drag(0.25 * held.content_box()[2], 0.0, 0)
    assert held.value == pytest.approx(190.0)


def test_a_closed_range_has_one_value_and_sits_on_it(root):
    held = placed(root, "slider", {"min": 50, "max": 50, "value": 50}, name="stub",
                  box=(10, 10, 100, 24))
    press(held, *along(held, 0.75))
    assert held.value == 50.0


# -- scaling: linear, and by ratio -------------------------------------------


def test_a_log_range_maps_by_ratio_and_not_by_average(root):
    held = placed(root, "slider", {"min": 20, "max": 300, "value": 120, "log": 1},
                  name="logs", box=(0, 80, 200, 24))
    assert logarithmic(held) is True
    press(held, *along(held, 0.5))
    assert held.value == pytest.approx(77.4596669241, rel=1e-9)


def test_a_log_range_reads_its_own_mapping_backwards(root):
    """A drag adds a fraction to a fraction, so the inverse has to agree exactly."""
    held = placed(root, "slider", {"min": 20, "max": 300, "value": 120, "log": 1},
                  name="logs", box=(0, 80, 200, 24))
    assert fraction_of(held, 77.45966692414834) == pytest.approx(0.5, abs=1e-9)
    assert value_at(held, fraction_of(held, 200.0)) == pytest.approx(200.0)
    press(held, *along(held, 0.25))
    held.on_drag(0.25 * held.content_box()[2], 0.0, 0)
    assert fraction_of(held, held.value) == pytest.approx(0.5, abs=1e-9)


def test_a_log_range_that_reaches_zero_falls_back_to_linear_instead_of_bursting(root):
    """``log(f / 0)`` has no answer, and a widget is not allowed to have one.

    Pd keeps the value in this case and so do we: the prop stays set, the scale
    quietly becomes linear, and the person gets a working slider rather than a
    ``math domain error`` in the middle of a drag.
    """
    held = placed(root, "slider", {"min": 0, "max": 300, "value": 150, "log": 1},
                  name="broken-log", box=(0, 80, 200, 24))
    assert logarithmic(held) is False
    press(held, *along(held, 0.5))
    assert held.value == pytest.approx(150.0)


def test_a_log_range_below_zero_is_linear_too(root):
    held = placed(root, "slider", {"min": -10, "max": 300, "value": 0, "log": 1},
                  name="swinging", box=(0, 80, 200, 24))
    assert logarithmic(held) is False
    press(held, *along(held, 0.5))
    assert held.value == pytest.approx(145.0)


def test_a_log_range_sees_a_nonpositive_value_as_its_low_end(root):
    held = placed(root, "slider", {"min": 20, "max": 300, "value": 120, "log": 1},
                  name="logs", box=(0, 80, 200, 24))
    assert fraction_of(held, 0.0) == 0.0
    assert fraction_of(held, -50.0) == 0.0, \
        "no ratio exists, so the fraction is the start"


# -- snap: the ladder a value is allowed to sit on ---------------------------


def test_a_step_lands_a_point_on_the_nearest_rung(root):
    held = placed(root, "slider", {"min": 0, "max": 100, "value": 0, "step": 7},
                  name="steps", box=(0, 110, 100, 24))
    press(held, *along(held, 0.5))
    assert held.value == 49.0, "50 is two sevens away from 49 and five from 56"


def test_a_step_is_anchored_at_the_low_end_and_not_at_zero(root):
    """A 20..300 slider with a step of 25 stops at 45 and 70, never at 50.

    The plan's metronome is the reason this matters: an anchored ladder is what
    makes ``min`` a place a value can be, not just a clamp.
    """
    held = placed(root, "slider", {"min": 20, "max": 300, "value": 120, "step": 25},
                  name="bpm", box=(56, 40, 200, 24))
    press(held, *along(held, 0.5))
    assert held.value == 170.0
    press(held, *along(held, 0.0))
    assert held.value == 20.0
    press(held, *along(held, 1.0))
    assert held.value == 300.0, \
        "the top of the box is the top of the range, ladder or no ladder"


def test_the_keyboard_moves_by_the_step_and_the_page_by_ten_of_it(root):
    """Arrows walk the ladder, a page is ten of them, and the ends are the ends.

    The run starts on a rung (49 is seven sevens) because an arrow from a value a
    ``SET`` put off-ladder is a re-snap rather than a step, and that is the next
    test's subject and not this one's.
    """
    held = placed(root, "slider", {"min": 0, "max": 100, "value": 49, "step": 7},
                  name="steps", box=(0, 110, 100, 24))
    held.on_key("up")
    assert held.value == 56.0
    held.on_key("down")
    held.on_key("down")
    assert held.value == 42.0
    held.on_key("page up")
    assert held.value == 100.0, "70 along is past the top, and stops at it"
    held.on_key("page down")
    assert held.value == 28.0, "100 - 70 is 30, and the nearest rung to 30 is 28"
    held.on_key("home")
    assert held.value == 0.0
    held.on_key("end")
    assert held.value == 100.0


def test_an_arrow_from_a_value_off_the_ladder_brings_it_back_onto_one(root):
    """A ``SET`` can park a value anywhere; the first key press is the ladder's."""
    held = placed(root, "slider", {"min": 0, "max": 100, "value": 50, "step": 7},
                  name="steps", box=(0, 110, 100, 24))
    held.on_key("up")
    assert held.value == 56.0, "50 + 7 is 57, and the rung nearest 57 is 56"


def test_arrow_key_words_and_their_aliases_all_mean_the_same_move(root):
    held = placed(root, "slider", {"min": 0, "max": 100, "value": 50, "step": 1},
                  name="s", box=(0, 110, 100, 24))
    held.on_key("right")
    assert held.value == 51.0
    held.on_key("left")
    held.on_key("left")
    assert held.value == 49.0


def test_a_continuous_range_still_answers_the_arrow_by_a_hundredth_of_its_span(root):
    """``step 0`` is not "no keyboard"; it is "no ladder".

    A hundredth is a move a person can see on a two-hundred-pixel track and repeat
    exactly, which is what an arrow key has to be to be worth pressing.
    """
    held = placed(root, "slider", {"min": 20, "max": 300, "value": 120},
                  name="bpm", box=(56, 40, 200, 24))
    held.on_key("up")
    assert held.value == pytest.approx(122.8)
    held.on_key("page up")
    assert held.value == pytest.approx(150.8), "thirty, ten rungs of it at once"


def test_the_wheel_is_a_coarse_arrow_on_the_same_ladder(root):
    """The wheel pushed away from you raises a level, one notch at a time.

    That is the toolkit convention and not the drag's, and it is why a scroll has
    its own row in the table: handed on as a drag, the same ``+dy`` would mean
    "the pointer moved down the screen" and would have to go the other way.
    """
    held = placed(root, "slider", {"min": 0, "max": 100, "value": 50, "step": 5},
                  name="s", box=(0, 110, 100, 24))
    held.on_scroll(0.0, 16.0, 0)
    assert held.value == 55.0, "one notch up"
    held.on_scroll(0.0, -32.0, 0)
    assert held.value == 45.0, "two notches back down"


def test_a_step_of_zero_tidies_the_number_it_produces(root):
    """``90.00000000000001`` is not a value a script can compare to anything.

    Every mapping here is a multiply and a divide, so the noise is inherent; the
    tenth place is where it is swept off, and it matters past the drawing because
    this number goes out through ``ON_CHANGE`` to a ``PD_SEND``.
    """
    held = placed(root, "slider", {"min": 0, "max": 3, "value": 0}, name="thirds",
                  box=(0, 0, 300, 24))
    press(held, *along(held, 1 / 3))
    assert held.value == 1.0, "exactly, and not a repeating float"
    assert held.value_text() == "1"


# -- typed entry: the number box's other half --------------------------------


def test_a_number_box_does_not_jump_at_the_press_because_the_press_is_to_type(root):
    """The one real difference between a field and a slider.

    A click into a box to correct its number must not first overwrite the number
    being corrected. Dragging the field still works, which is why this is a flag on
    the press and not a different widget.
    """
    held = placed(root, "number", {"min": 20, "max": 300, "value": 120},
                  name="field", box=(264, 40, 80, 24))
    press(held, *along(held, 0.9))
    assert held.value == 120.0
    assert held.take_changes() == []


def test_typing_a_number_and_pressing_enter_lands_it(root):
    held = placed(root, "number", {"min": 20, "max": 300, "value": 120},
                  name="field", box=(264, 40, 80, 24))
    press(held, *along(held, 0.5))
    for character in "140":
        held.on_char(character)
    assert held.value == 120.0, "nothing lands until the edit is ended"
    held.on_key("enter")
    assert held.value == 140.0
    assert held.take_changes() == ["value"]
    assert held.value_text() == "140"


def test_a_typed_overshoot_lands_on_the_end_it_tried_to_pass(root):
    held = placed(root, "number", {"min": 20, "max": 300, "value": 120},
                  name="field", box=(264, 40, 80, 24))
    for character in "900":
        held.on_char(character)
    held.on_key("enter")
    assert held.value == 300.0, "clamped, and the script hears 300 - not a rejection"


def test_a_typed_number_is_not_snapped_to_the_ladder(root):
    """A step says where a drag may stop; a typed number says where a person meant it.

    Refusing ``23`` from a ladder of 10 would make the field unusable, and the
    clamps are the only bounds a typed value needs.
    """
    held = placed(root, "number", {"min": 0, "max": 100, "value": 0, "step": 10},
                  name="field", box=(264, 40, 80, 24))
    for character in "23":
        held.on_char(character)
    held.on_key("enter")
    assert held.value == 23.0
    held.on_key("up")
    assert held.value == 30.0, "the next rung, and not the 33 a plain add would say"


def test_a_negative_is_typable_and_a_second_one_is_not(root):
    held = placed(root, "number", {"min": -300, "max": 300, "value": 1},
                  name="field", box=(264, 40, 80, 24))
    for character in "-45":
        held.on_char(character)
    held.on_char("-")
    held.on_key("enter")
    assert held.value == -45.0


def test_a_dot_is_taken_once_and_a_digit_after_it_is_still_a_number(root):
    held = placed(root, "number", {"min": 0, "max": 10, "value": 1}, name="field",
                  box=(264, 40, 80, 24))
    for character in "2.75.":
        held.on_char(character)
    held.on_key("enter")
    assert held.value == pytest.approx(2.75)


def test_backspace_takes_the_last_character_off_the_buffer(root):
    held = placed(root, "number", {"min": 0, "max": 300, "value": 7}, name="field",
                  box=(264, 40, 80, 24))
    for character in "123":
        held.on_char(character)
    held.on_key("backspace")
    held.on_key("enter")
    assert held.value == 12.0
    for character in "4":
        held.on_char(character)
    held.on_key("backspace")
    held.on_key("backspace")
    held.on_key("backspace")
    held.on_key("enter")
    assert held.value == 12.0, "an emptied buffer is no edit at all"


def test_escape_and_losing_the_keyboard_both_throw_the_edit_away(root):
    held = placed(root, "number", {"min": 0, "max": 300, "value": 7}, name="field",
                  box=(264, 40, 80, 24))
    for character in "199":
        held.on_char(character)
    held.on_key("escape")
    held.on_key("enter")
    assert held.value == 7.0, "a person who left the field did not mean it"
    for character in "288":
        held.on_char(character)
    held.on_blur()
    held.on_key("enter")
    assert held.value == 7.0, "and a blur is leaving, not pressing Enter"


def test_a_letter_does_not_start_an_edit_and_does_not_swallow_the_beep(root):
    held = placed(root, "number", {"min": 0, "max": 300, "value": 7}, name="field",
                  box=(264, 40, 80, 24))
    for character in "abc":
        held.on_char(character)
    held.on_key("enter")
    assert held.value == 7.0
    assert held._behaviour.get("typing") is None


def test_digits_come_in_as_characters_and_never_as_keys_too(root):
    """Guards the double-type that a test alone cannot see.

    GLFW sends a key event *and* a character event for a printable key, so a
    behaviour that also appended digits in :meth:`~crude.ui.widget.Widget.on_key`
    would type every number twice in a real window while looking correct to a test
    that only used :meth:`~crude.ui.input.InputRouter.type_text`.
    """
    held = placed(root, "number", {"min": 0, "max": 300, "value": 7}, name="field",
                  box=(264, 40, 80, 24))
    held.on_key("9")
    assert held._behaviour.get("typing") is None
    held.on_key("enter")
    assert held.value == 7.0


def test_typing_is_refused_where_the_table_said_the_value_is_not_a_number(root):
    """The ``editable`` row is the whole rule, so no list of kinds is kept anywhere."""
    for kind in ("slider", "number"):
        held = placed(root, kind, {"min": 0, "max": 300, "value": 7},
                      name=kind, box=(264, 40, 80, 24))
        assert held.kind.has("editable") is True
        held.set("editable", False)
        for character in "14":
            held.on_char(character)
        held.on_key("enter")
        assert held.value == 7.0, kind
    for kind, values in (("radio", {"labels": ["a", "b"], "value": 0}),
                         ("dropdown", {"labels": ["a", "b"], "value": 0}),
                         ("meter", {"value": 0}), ("button", {"value": 0})):
        held = Widget(kind, values, name=kind, parent=root)
        assert held.kind.has("editable") is False, kind
        held.on_char("1")
        held.on_char("2")
        held.on_key("enter")
        assert held.value == 0.0, kind
        # ``enter`` on a switch means "activate", which is its own row of the table
        # and its own test above; here it must not be read as "commit the number"


def test_a_press_starts_a_new_edit_rather_than_joining_the_old_one(root):
    held = placed(root, "number", {"min": 0, "max": 300, "value": 7}, name="field",
                  box=(264, 40, 80, 24))
    held.on_char("1")
    press(held, *along(held, 0.5))
    held.on_char("9")
    held.on_key("enter")
    assert held.value == 9.0, "the buffer was dropped when the pointer came down"


# -- the switches ------------------------------------------------------------


def test_a_toggle_flips_and_carries_the_glyph_it_was_given_with_it(root):
    held = placed(root, "toggle", {"label": "Run", "glyph_off": "play",
                                   "glyph_on": "pause"}, name="run",
                  box=(16, 84, 96, 28))
    held.on_click(*along(held, 0.5), 0)
    assert held.value == 1.0
    assert held.glyph_name(held.value) == "pause"
    held.on_key(" ")
    assert held.value == 0.0
    assert held.glyph_name(held.value) == "play"
    assert held.take_changes() == ["value"], \
        "the list is the set of rows that moved, not one entry per write"


def test_a_press_or_a_drag_on_a_toggle_says_nothing_because_a_click_is_the_gesture(
        root):
    held = placed(root, "toggle", {"label": "Run"}, name="run",
                  box=(16, 84, 96, 28))
    press(held, *along(held, 0.5))
    held.on_drag(10, 4, 0)
    held.on_scroll(0, -16, 0)
    assert held.value == 0.0, "a latching switch has no continuous gesture"
    held.on_release(*along(held, 0.5), 0)
    assert held.value == 0.0, "the release belongs to the click that followed"


def test_enter_or_space_activates_a_switch_the_same_way_a_click_does(root):
    held = placed(root, "toggle", name="run", box=(16, 84, 96, 28))
    held.on_key("enter")
    assert held.value == 1.0
    held.on_key("keypad enter")
    assert held.value == 0.0


def test_a_button_is_one_on_the_press_zero_on_the_release_and_a_pulse_from_a_key(root):
    """Two changes and therefore two ``ON_CHANGE`` runs: Pd's behaviour, no clock."""
    held = placed(root, "button", {"label": "go"}, name="go", box=(120, 84, 40, 24))
    seen = []
    held.on_change = lambda who, name, value: seen.append(value)
    press(held, *along(held, 0.5))
    assert held.value == 1.0
    held.on_release(*along(held, 0.5), 0)
    assert held.value == 0.0
    assert seen == [1.0, 0.0]
    del seen[:]
    held.on_key(" ")
    assert seen == [1.0, 0.0], "a key has no release of its own, so it does both"
    assert held.value == 0.0, "and it is at rest, which is what a bang looks like"


def test_a_button_makes_no_third_change_when_the_click_arrives_after_the_release(root):
    """The router always sends both: an extra write would be an extra send to Pd."""
    held = placed(root, "button", name="go", box=(120, 84, 40, 24))
    held.on_click(*along(held, 0.5), 0)
    assert held.value == 0.0, "nothing was ever pressed, so nothing is held"
    assert held.take_changes() == []


# -- the lists ---------------------------------------------------------------


def test_a_radio_selects_the_cell_the_press_landed_in(root):
    held = placed(root, "radio", {"labels": ["sine", "tri", "saw"], "value": 0},
                  name="r", box=(0, 140, 120, 40))
    left, top, width, height = held.content_box()
    press(held, left + 1, top + 1)
    assert held.value == 0.0
    press(held, left + width - 1, top + height / 2)
    assert held.value == 1.0, "columns 0 is one entry per row, so middle is 1"
    press(held, left + width / 2, top + height - 1)
    assert held.value == 2.0


def test_a_radio_with_columns_divides_its_box_before_it_divides_its_list(root):
    """The layout a person sees and the index they get are the same arithmetic."""
    held = placed(root, "radio", {"labels": ["a", "b", "c", "d"], "value": 0},
                  name="r", box=(0, 140, 120, 40))
    held.set("columns", 2)
    left, top, width, height = held.content_box()
    press(held, left + width - 1, top + 1)
    assert held.value == 1.0, "top row, second cell"
    press(held, left + 1, top + height - 1)
    assert held.value == 2.0, "second row, first cell"
    press(held, left + width - 1, top + height - 1)
    assert held.value == 3.0
    held.set("labels", ["a", "b", "c"])
    press(held, left + width - 1, top + height - 1)
    assert held.value == 2.0, "the odd cell out still owns its half of the row"


def test_a_radio_key_moves_by_a_row_when_the_widget_is_drawn_in_rows(root):
    held = placed(root, "radio", {"labels": ["a", "b", "c", "d"], "value": 0},
                  name="r", box=(0, 140, 120, 40))
    held.set("columns", 2)
    held.on_key("down")
    assert held.value == 2.0, "up and down are rows; left and right are cells"
    held.on_key("left")
    assert held.value == 1.0
    held.on_key("home")
    assert held.value == 0.0
    held.on_key("end")
    assert held.value == 3.0
    held.on_scroll(0.0, -16.0, 0)
    assert held.value == 2.0


def test_a_press_outside_every_cell_selects_nothing(root):
    held = placed(root, "radio", {"labels": ["a", "b"], "value": 1}, name="r",
                  box=(0, 140, 120, 40))
    left, top, width, height = held.content_box()
    press(held, left + width + 40, top + 1)
    assert held.value == 1.0


def test_a_list_of_no_choices_has_no_index_to_find(root):
    held = placed(root, "radio", {"labels": [], "value": 0}, name="r",
                  box=(0, 140, 120, 40))
    press(held, *along(held, 0.5))
    held.on_key("end")
    assert held.value == 0.0


def test_a_dropdown_cycles_and_wraps_because_it_has_no_popup_to_point_in(root):
    held = placed(root, "dropdown", {"labels": ["a", "b", "c", "d"], "value": 0},
                  name="d", box=(140, 140, 80, 20))
    held.on_click(*along(held, 0.5), 0)
    assert held.value == 1.0
    held.on_key("left")
    held.on_key("left")
    assert held.value == 3.0, "the index has no end a person can walk off"
    held.on_key("right")
    assert held.value == 0.0
    held.on_key("end")
    assert held.value == 3.0
    held.on_scroll(0.0, -16.0, 0)
    assert held.value == 2.0


def test_a_dropdown_click_has_exactly_one_direction(root):
    """The router does not pass a click's modifiers, so a click cannot mean "back"."""
    held = placed(root, "dropdown", {"labels": ["a", "b", "c"], "value": 0},
                  name="d", box=(140, 140, 80, 20))
    for _ in range(3):
        held.on_click(*along(held, 0.5), 0)
    assert held.value == 0.0


# -- the clip: gestures that move a window, not a value --------------------


def test_a_press_on_a_marker_drags_that_marker_and_no_other(root):
    """The mark under the finger is the mark that moves, and it is held from there.

    Anchored at the sample the press closed on rather than at each increment: a
    drag is a movement, and a marker that accumulated deltas would land somewhere
    else depending on how the router happened to coalesce the hand.
    """
    held = placed(root, "clip", {"data": [0.0] * 200, "count": 100,
                                 "markers": [40, 80]}, name="take",
                  box=(0, 0, 200, 60))
    left, top, width, height = held.content_box()
    press(held, left + width * 0.4, top + height * 0.5)
    held.on_drag(width * 0.1, 0.0, 0)
    assert list(held.get("markers")) == [pytest.approx(50.0), pytest.approx(80.0)]
    assert held.take_changes() == ["markers"], "one row, and only the one list"
    held.on_release(0.0, 0.0, 0)
    held.on_drag(width * 0.1, 0.0, 0)
    assert float(held.get("markers")[0]) == 50.0, "the release ended the gesture"


def test_a_dragged_marker_lands_on_a_sample_a_script_can_name(root):
    """Pixels divide into fractions of samples; a marker is never one of those.

    Seven pixels at two samples per pixel is a sample and a half, and the number
    the row holds afterwards is the whole sample the face was drawn with - which is
    what lets a knob wired to the same marker agree with the mark on the plate.
    """
    held = placed(root, "clip", {"data": [0.0] * 200, "count": 100,
                                 "markers": [40]}, name="take",
                  box=(0, 0, 200, 60))
    left, top, width, _height = held.content_box()
    press(held, left + width * 0.4, top)
    held.on_drag(7.0, 0.0, 0)
    assert list(held.get("markers")) == [pytest.approx(44.0)]
    assert float(held.get("markers")[0]) % 1.0 == 0.0, "no float noise in a sample"


def test_a_press_on_empty_plate_grabs_the_take_and_not_the_playhead(root):
    """The hand is holding the sample, so a drag right slides the view left.

    The cursor is not moved by a press: a recorder parks it with a knob and the
    ``follow`` row brings the view along behind it, and a plate that jumped the
    cursor on every click would leave no way to pan without dragging a mark out of
    place. A project that wants the jump says so with ``ON_CLICK``, which runs after.
    """
    held = placed(root, "clip", {"data": [0.0] * 200, "start": 0, "count": 50},
                  name="take", box=(0, 0, 200, 60))
    left, top, width, height = held.content_box()
    press(held, left + width * 0.5, top + height * 0.5)
    held.on_drag(width * 0.5, 0.0, 0)
    assert (held.get("start"), held.get("count")) == (0.0, 50.0), \
        "already at the head of the take, and an unchanged write is silent"
    assert held.take_changes() == []
    held.on_drag(-width, 0.0, 0)
    assert held.get("start") == 25.0, "the total pull is -25 samples"
    assert held.get("playhead") == -1.0, "the cursor is the script's to place"


def test_a_window_that_is_the_whole_take_has_nothing_to_slide(root):
    """``count`` 0 means everything, including what has not been recorded yet.

    Writing the length the take happens to have today would freeze a recording that
    is still growing, so a pan refuses to be the door that closes it - the same 0 a
    zoom out lands on, and the same 0 a script wrote.
    """
    held = placed(root, "clip", {"data": [0.0] * 200}, name="take",
                  box=(0, 0, 200, 60))
    left, top, width, height = held.content_box()
    press(held, left + width * 0.25, top + height * 0.5)
    held.on_drag(-width * 0.25, 0.0, 0)
    held.on_drag(-width * 0.25, 0.0, 0)
    assert (held.get("start"), held.get("count")) == (0.0, 0.0)
    held.set("data", [0.0] * 400)
    assert held.get("count") == 0.0, "and the take it shows doubled underneath it"


def test_the_wheel_zooms_about_where_the_pointer_is(root):
    """The sample under the cursor stays under the cursor, which is the whole use.

    A window that scaled about its own left edge would slide the take out from
    under the wheel every notch, and the person would be chasing the peak they were
    reaching for. The pointer's pixel is remembered from the moves that ran before
    the scroll, because the wheel itself is a movement and carries no position.
    """
    held = placed(root, "clip", {"data": [0.0] * 200, "count": 100}, name="take",
                  box=(0, 0, 200, 60))
    left, top, width, height = held.content_box()
    aimed = left + width * 0.5
    held.on_move(aimed, top + height * 0.5)
    held.on_scroll(0.0, 16.0, 0)  # one notch in: the window halves about the pointer
    assert (held.get("start"), held.get("count")) == (25.0, 50.0)
    held.on_scroll(0.0, -16.0, 0)
    assert (held.get("start"), held.get("count")) == (0.0, 100.0), \
        "and the same notch back is the same window it was"
    held.on_scroll(0.0, -32.0, 0)
    assert held.get("count") == 0.0, "out past the take is the take, written as 0"


def test_a_wheel_the_pointer_never_told_about_still_has_an_answer(root):
    """No recorded position is not a missing number: it aims at the middle.

    A wheel pushed by a script, or a plate that moved under a cursor that never
    crossed it, is a zoom the toolkit still has to draw somewhere, and the one
    sample a person who has not moved the pointer can be said to be looking at is
    the centre of the box. Stated here because the alternative is a silent fallback.
    """
    held = placed(root, "clip", {"data": [0.0] * 200, "count": 100}, name="take",
                  box=(0, 0, 200, 60))
    held.on_scroll(0.0, 16.0, 0)
    assert (held.get("start"), held.get("count")) == (25.0, 50.0), \
        "the same window the pointer at the middle would have produced"


def test_the_zoom_has_a_floor_and_it_is_one_sample(root):
    held = placed(root, "clip", {"data": [0.0] * 200, "count": 50}, name="take",
                  box=(0, 0, 200, 60))
    left, top, width, height = held.content_box()
    held.on_move(left, top + height * 0.5)  # the pointer sits on sample 0
    for _ in range(8):
        held.on_scroll(0.0, 16.0, 0)
    assert held.get("count") == 1.0, "a window narrower than a sample is not a view"
    assert held.get("start") == 0.0, "and it closed on the sample it was aimed at"


def test_a_clip_gesture_writes_no_value_because_a_clip_holds_none(root):
    """A press, a drag and a wheel, and the one row a clip has no business touching.

    Every gesture on this kind lands on ``start``, ``count`` or ``markers``. The
    ``value`` row the catalog hands most kinds is not one of them, and a behaviour
    that wrote it would be a canvas with two opinions about where it is.
    """
    held = placed(root, "clip", {"data": [0.0] * 200, "count": 100,
                                 "markers": [30]}, name="take",
                  box=(0, 0, 200, 60))
    assert held.has_value is False
    left, top, width, height = held.content_box()
    press(held, left + width * 0.3, top + height * 0.5)
    held.on_drag(width * 0.2, 0.0, 0)
    held.on_release(left + width * 0.5, top + height * 0.5, 0)
    held.on_scroll(0.0, 16.0, 0)
    press(held, left + width * 0.3, top + height * 0.5, button=2)
    held.on_drag(width * 0.2, 0.0, 0)
    assert held.kind.has("value") is False, "the row is not even in the table"
    assert set(held.take_changes()) <= {"start", "count", "markers"}


# -- what must refuse --------------------------------------------------------


def test_a_switched_off_widget_moves_nothing(root):
    held = placed(root, "slider", {"min": 0, "max": 10, "value": 5}, name="off",
                  box=(260, 60, 80, 20))
    held.set("enabled", False)
    assert held.take_changes() == ["enabled"], "the switch itself was a change"
    press(held, *along(held, 1.0))
    held.on_click(*along(held, 0.5), 0)
    held.on_key("end")
    held.on_char("9")
    held.on_key("enter")
    assert held.value == 5.0
    assert held.take_changes() == []


def test_a_hidden_widget_moves_nothing(root):
    held = placed(root, "toggle", name="run", box=(16, 84, 96, 28))
    held.set("visible", False)
    held.on_click(*along(held, 0.5), 0)
    assert held.value == 0.0, "the router would never have reached it either way"


def test_only_the_left_button_moves_a_value(root):
    """A right click is a script's to mean something; a behaviour must not guess it."""
    slider = placed(root, "slider", {"min": 0, "max": 100, "value": 50},
                    name="s", box=(0, 0, 200, 24))
    press(slider, *along(slider, 1.0), button=1)
    assert slider.value == 50.0
    slider.on_drag(0.25 * slider.content_box()[2], 0.0, 0)
    assert slider.value == pytest.approx(75.0), "a drag has no button to ask"
    toggle = placed(root, "toggle", name="run", box=(16, 84, 96, 28))
    toggle.on_click(*along(toggle, 0.5), 1)
    assert toggle.value == 0.0
    radio = placed(root, "radio", {"labels": ["a", "b"], "value": 0}, name="r",
                   box=(0, 140, 120, 40))
    press(radio, *along(radio, 1.0, up=1.0), button=2)
    assert radio.value == 0.0


# -- kinds a script defines --------------------------------------------------


def test_a_variant_inherits_its_bases_behaviour_through_its_chain(root):
    """``WIDGET_DEFINE("bpm_slider", "slider")`` gets the drag, not a copy of it.

    One table, walked by inheritance, which is the plan's "inherits input
    handling" made true with no second list to keep in step.
    """
    define("bpm_slider", "slider")
    held = placed(root, "bpm_slider", {"min": 20, "max": 300, "value": 120},
                  name="bpm", box=(56, 40, 200, 24))
    assert behaviour_for(held.kind) is BEHAVIOURS["slider"]
    press(held, *along(held, 0.25))
    assert held.value == pytest.approx(90.0)
    held.on_key("end")
    assert held.value == 300.0
    held.on_scroll(0.0, -16.0, 0)
    assert held.value == pytest.approx(297.2), \
        "and the slider's whole wheel came with it, at a hundredth of the span"


def test_a_baseless_script_kind_has_no_behaviour_at_all(root):
    """The plan's ``stepper``: its handlers are the whole story.

    Nothing is suppressed to make that true - there is simply no row in the table
    for a kind that inherits from nothing - which is also why a script's ``ON_CLICK``
    on it cannot find a base behaviour writing under it.
    """
    define("stepper")
    define_prop("stepper", "value", "number", 0)
    held = placed(root, "stepper", {"value": 4}, name="step", box=(0, 0, 60, 24))
    assert behaviour_for(held.kind) == {}
    assert held.on_event is None
    press(held, *along(held, 0.9))
    held.on_click(*along(held, 0.9), 0)
    held.on_key("up")
    held.on_char("9")
    held.on_key("enter")
    assert held.value == 4.0


def test_a_custom_kind_may_declare_its_own_row_bounds_and_be_measured_on_them(root):
    """A script kind built on a slider gets its ``min``/``max`` props too."""
    define("fader", "slider")
    define_prop("fader", "min", "number", 0.0, 0.0, 1000.0)
    held = placed(root, "fader", {"min": 100, "max": 500, "value": 300},
                  name="f", box=(0, 0, 200, 24))
    assert span_of(held) == (100.0, 500.0)
    press(held, *along(held, 0.5))
    assert held.value == pytest.approx(300.0)
    press(held, *along(held, 0.25))
    assert held.value == pytest.approx(200.0)


# -- the table and a script's handlers, together -----------------------------


def test_the_behaviour_runs_before_a_scripts_click_handler_and_the_script_may_overrule(
        root):
    """The two halves of what this layer promises, in one test.

    :meth:`~crude.ui.handlers.Handlers.adopt` says the callable already in the slot
    "runs before the registrations"; the plan says a script's handler "overrides
    base behaviour only when defined". Only one ordering makes both true: the
    behaviour produces the value first, so the handler *sees* it - and may write its
    own over it, which is the whole of what "override" can mean for a final value.
    """
    bridge = Handlers(name="test")
    held = placed(root, "toggle", {"label": "Run"}, name="run",
                  box=(16, 84, 96, 28))
    seen = []
    bridge.add(held, "click", lambda *said: seen.append(held.value))
    held.on_click(*along(held, 0.5), 0)
    assert seen == [1.0], "the handler was told the value the click produced"
    assert held.value == 1.0

    bridge.add(held, "click", lambda *said: held.set("value", 0))
    held.on_click(*along(held, 0.5), 0)
    assert held.value == 0.0, "and a script that writes over it still wins"


def test_a_handler_that_takes_the_slot_without_chaining_switches_the_behaviour_off(
        root):
    """Why ``adopt`` chains instead of replacing, shown from the other side.

    A bare assignment to ``on_event`` is not a bug in itself - it is how a script
    takes a widget entirely over - but it does mean the kind stops answering
    gestures, and a test that installed a listener by hand should not be surprised
    by a slider that has stopped dragging.
    """
    held = placed(root, "toggle", name="run", box=(16, 84, 96, 28))
    held.on_event = lambda who, name, *args: None
    held.on_click(*along(held, 0.5), 0)
    assert held.value == 0.0
    install_behaviour(held)
    held.on_click(*along(held, 0.5), 0)
    assert held.value == 1.0


def test_a_released_widget_goes_back_to_answering_for_itself(root):
    bridge = Handlers(name="test")
    held = placed(root, "slider", {"min": 0, "max": 100, "value": 10}, name="s",
                  box=(0, 0, 200, 24))
    bridge.add(held, "click", lambda *said: None)
    assert bridge.release(held) is True
    press(held, *along(held, 0.5))
    assert held.value == pytest.approx(50.0), \
        "its own slot came back with the behaviour in it"


def test_a_link_carries_a_gesture_to_its_mirror_the_way_a_set_would(root):
    """The plan's "drag the slider, the number box reads 30" is one gesture."""
    bridge = Handlers(name="test")
    slider = placed(root, "slider", {"min": 0, "max": 100, "value": 0}, name="s",
                    box=(0, 0, 200, 24))
    field = placed(root, "number", {"min": 0, "max": 100, "value": 0}, name="n",
                   box=(264, 40, 80, 24))
    bridge.link(slider, "value", field, "value")
    press(slider, *along(slider, 0.3))
    assert slider.value == pytest.approx(30.0)
    assert field.value == pytest.approx(30.0)
    press(field, *along(field, 0.8))
    assert field.value == pytest.approx(30.0), "a field does not jump, so nothing moved"
    field.on_char("7")
    field.on_key("enter")
    assert field.value == 7.0 and slider.value == 7.0


def test_the_sender_stack_names_the_registration_answering_now(root):
    """The sender is a stack, not a slot, because a handler may fire a handler.

    A clip drag writes a knob, that knob's ``ON_CHANGE`` writes the clip back, and
    the inner call belongs to the widget the *inner* registration is on - while it
    runs. Read it as one slot and the outer widget would be blamed for a change it
    never made; read it as a stack and it comes back to the outer one on the way
    out, which is the order a person watching the screen would describe.
    """
    bridge = Handlers(name="test")
    outer = placed(root, "button", {"label": "one"}, name="one",
                   box=(0, 0, 100, 28))
    inner = placed(root, "button", {"label": "two"}, name="two",
                   box=(0, 40, 100, 28))
    seen = []

    def from_outer(*said):
        seen.append(bridge.sender())
        inner.on_click(*along(inner, 0.5), 0)
        seen.append(bridge.sender())

    bridge.add(outer, "click", from_outer)
    bridge.add(inner, "click", lambda *said: seen.append(bridge.sender()))
    outer.on_click(*along(outer, 0.5), 0)
    assert seen == [outer, inner, outer]
    assert bridge.sender() is None, "the stack unwound with the call"


def test_a_frame_handler_has_no_sender_because_it_is_registered_on_no_widget(root):
    """A frame handler is keyed on no widget, so it names no widget either.

    ``SENDER()`` answering something here would be a bridge inventing a sender: the
    tick covers the whole window, and the one honest answer is that no box made it.
    """
    bridge = Handlers(name="test")
    seen = []
    bridge.on_frame(lambda *said: seen.append(bridge.sender()))
    assert bridge.fire_frame(0.016) == 1
    assert seen == [None]
    assert bridge.sender() is None


def test_a_host_callable_registration_is_called_by_the_bridge_and_not_the_program(root):
    """The toolkit's own handlers are Python, and a project's script is attached anyway.

    :meth:`~crude.ui.menu.MenuDialog._watch_resize` registers a plain function on the
    window's root so a settings page sized from its window follows a resize. Which of
    the two kinds a registration is has to be read off the callable: asking
    ``Program.invoke`` first handed that function to the interpreter in every real run,
    and the interpreter answers "function is not callable" for anything with no
    ``execute``, so the watcher died, the page stopped following the window, and the
    bridge logged a bug that was its own rather than the project's.
    """
    import types

    from crude.lang import Program

    bridge = Handlers(name="test")
    bridge.attach_program(Program("<behaviour>",
                                  host=types.SimpleNamespace(ui=None)))
    seen = []
    bridge.add(root, "resize", lambda *said: seen.append(said))
    assert bridge.fire(root, "resize", 640, 480) == 1
    assert seen == [(640, 480, "resize")], "the tree's own arguments, event name last"
    assert bridge.errors == []
    assert bridge.handlers(root, "resize")[0].calls == 1


def test_a_script_function_registration_still_runs_through_the_interpreter(root):
    """The other half of the same choice: a ``FUNC`` is not callable from here.

    A script's function is only runnable by the interpreter, so a bridge holding both
    a script handler and a host handler on the same event has to tell them apart - and
    the way it tells them apart has to be the fact that decides, not the accident of
    which one was registered first.
    """
    import types

    from crude.lang import Program

    program = Program("<behaviour>", host=types.SimpleNamespace(ui=None))
    _, error = program.run("VAR HITS = [0.0]\n"
                           "FUNC note(v)\n"
                           "    SET_AT(HITS, 0, AT(HITS, 0) + v)\n"
                           "    RETURN v\n"
                           "END\n")
    assert error is None, error
    note = program.symbol_table.get("note")
    assert hasattr(note, "execute"), "a script function is not a Python callable"
    bridge = Handlers(name="test")
    bridge.attach_program(program)
    seen = []
    bridge.add(root, "resize", lambda *said: seen.append(said))
    bridge.add(root, "resize", note)
    assert bridge.fire(root, "resize", 12.0, 480) == 2
    assert seen == [(12.0, 480, "resize")], "the host callable ran here"
    hits = program.symbol_table.get("HITS")
    assert hits.elements[0].value == 12.0, "the script function ran there"
    assert bridge.errors == []


# -- the same numbers down the real path -------------------------------------


def router_for(root, **extra) -> InputRouter:
    """GLFW -> router -> hit test -> widget, with no display anywhere."""
    return InputRouter(lambda x, y: top_at(root, x, y), width=360, height=220,
                       root=root, **extra)


def point_of(held, fraction: float, up: float = 0.5) -> tuple:
    """A screen point at ``fraction`` of a widget's track, for a router to hit.

    Measured on the content box for the same reason :func:`along` is: the behaviour
    reads the range off the box it is drawn in, and a test that measured off the
    screen box would be asking the router to hit a pixel two insets away from the
    value it means.
    """
    left, top, width, height = held.content_box()
    return left + width * fraction, top + height * up


def test_a_slider_moves_when_a_person_clicks_it_through_the_router(root):
    """The mapping is the same number once the hit test and the capture are in between.

    A click through the router is a press, a release and then a click, so this also
    proves the sequence does not double-write: one jump, not a jump and a nudge.
    """
    held = placed(root, "slider", {"min": 20, "max": 300, "value": 120}, name="bpm",
                  box=(56, 40, 200, 24))
    router = router_for(root)
    assert router.click(*point_of(held, 0.25)) is held
    assert held.value == pytest.approx(90.0)
    assert held.take_changes() == ["value"]


def test_a_drag_from_the_press_to_a_third_over_is_a_relative_move(root):
    held = placed(root, "slider", {"min": 0, "max": 100, "value": 50}, name="s",
                  box=(0, 0, 200, 24))
    router = router_for(root)
    router.move_to(*point_of(held, 0.5))
    router.press()
    assert router.capture is held
    router.move_to(*point_of(held, 0.75))
    router.release()
    assert held.value == pytest.approx(75.0), "it jumped to 50 and moved by a quarter"
    # while the pointer is captured the widget is told both: the drag, which is the
    # movement it maps, and the move, which is only where the cursor now sits
    assert router.names()[3:] == ["press", "drag", "move", "release", "click"]


def test_a_fine_drag_moves_an_eighth_as_far(root):
    """Ctrl is the ruler's fine setting, and it belongs to the router, not here.

    The behaviour never sees a modifier it has to interpret: :func:`fine_delta` has
    already divided the movement by :data:`~crude.ui.input.DRAG_FINE` before the
    drag was sent, which is why one wheel, one trackpad and one Ctrl-drag all agree
    on what "fine" means for every widget in the tree.
    """
    held = placed(root, "slider", {"min": 0, "max": 100, "value": 0}, name="s",
                  box=(0, 0, 200, 24))
    router = router_for(root)
    router.move_to(*point_of(held, 0.0))
    router.press()
    router.move_to(*point_of(held, 0.5))
    router.release()
    assert held.value == pytest.approx(50.0)
    held.set("value", 0)
    router.move_to(*point_of(held, 0.0))
    router.press(mods=MODS["control"])
    router.move_to(*point_of(held, 0.5))
    router.release(mods=MODS["control"])
    assert held.value == pytest.approx(6.25), "an eighth of the same movement"


def test_typing_into_a_field_through_the_router_lands_the_number(root):
    held = placed(root, "number", {"min": 20, "max": 300, "value": 120}, name="field",
                  box=(264, 40, 80, 24))
    router = router_for(root)
    router.click(*point_of(held, 0.5))
    assert held.focused is True and held.value == 120.0, "it did not jump"
    router.type_text("140")
    router.key_typed("enter")
    assert held.value == 140.0
    assert held.value_text() == "140"


def test_the_wheel_on_a_slider_is_a_scroll_and_not_the_drag_fallback(root):
    held = placed(root, "slider", {"min": 0, "max": 100, "value": 50, "step": 2},
                  name="s", box=(0, 0, 200, 24))
    router = router_for(root)
    router.move_to(*point_of(held, 0.5))
    router.scroll_at(-1)
    assert router.names()[-1] == "scroll", "the behaviour does answer a scroll"
    assert held.value == 48.0


def test_a_switch_and_a_list_answer_the_router_the_way_they_answered_a_gesture(
        root):
    toggle = placed(root, "toggle", {"label": "Run", "glyph_off": "play",
                                     "glyph_on": "pause"}, name="run",
                    box=(16, 84, 96, 28))
    router = router_for(root)
    router.click(*point_of(toggle, 0.5))
    assert toggle.value == 1.0 and toggle.glyph_name(toggle.value) == "pause"
    router.click(*point_of(toggle, 0.5))
    assert toggle.value == 0.0
    assert router.hits("click") == [toggle, toggle]


def test_a_widget_that_left_the_tree_still_holds_its_props_and_its_behaviour(
        root):
    """Detach is a layout fact, not a switch off: the widget is still itself."""
    held = placed(root, "slider", {"min": 0, "max": 100, "value": 10}, name="s",
                  box=(0, 0, 200, 24))
    root.remove(held)
    press(held, *along(held, 0.5))
    assert held.value == pytest.approx(50.0)
