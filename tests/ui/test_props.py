"""One prop table, four readers, and the mistakes that only show up in the dump.

:mod:`crude.ui.props` is the layer the plan makes a single-source-of-truth claim
for: ``SET`` validation, the tooltip a hover shows, ``crude widgets --json`` /
``docs/widgets.md``, and the argument mapping ``PD_EMIT`` writes a real Pd box
from all read the same table. The interesting failures here are therefore not
"does ``settle`` clamp" but "did two of those four readers end up disagreeing" -
a row documented under a name that does not exist, a prop that reaches Pd with the
wrong limit, a variant that quietly edits the catalog kind it was built from.

The other class is arithmetic that is right for the wrong reason. A slider's
``value`` has no range written on it: it reads its bounds out of its own ``min``
and ``max`` props, so a constructor that settles ``value`` before ``min`` clamps
120 BPM to 1.0 and still passes every single-prop test. Those get tested by
number, over every kind, on a machine with no display and no widgets yet.

Script kinds are global, so the autouse fixture below hands the registry back the
way it was found.
"""

from __future__ import annotations

import json

import pytest

from crude.ui.props import PLACE_MODES
from crude.ui import (KINDS, PD_BOXES, ROLES, SCHEMA, SIZES, TYPES, Kind, Prop,
                      PropError, State, as_role, catalog_names, define,
                      define_prop, digest, document, exists, get_kind, has,
                      hint_for, icon_names, pd_arguments, pd_object_for,
                      prop_names, props_of, registered_names, reset, settle,
                      settled, states, UIError, value_of)

#: the twenty-two kinds the plan's widget list adds up to: the twelve faces and
#  controls, plus the nine synth widgets and canvases S9 put on the same
#  conventions, plus the viewport the settings dialog grew into a kind
CATALOG = ("panel", "text", "icon", "button", "toggle", "slider", "vslider",
           "knob", "number", "meter", "radio", "dropdown",
           "waveform", "spectrum", "plot", "logview", "scroll",
           "adsr", "curve", "xy",
           "keybed", "preset_bar")


@pytest.fixture(autouse=True)
def clean_registry():
    """Undo any kind a test defined, before the next one looks at the table."""
    reset()
    yield
    reset()


@pytest.fixture
def bpm() -> Kind:
    """The plan's variant: ``slider`` with three of its defaults restated."""
    kind = define("bpm_slider", "slider")
    for name, value in (("min", 20), ("max", 300), ("ticks", 8)):
        define_prop("bpm_slider", name, "number", value)
    return kind


class _Widget:
    """The shape ``crude/ui/widget.py`` will have: a kind, and nothing else.

    Every function in the table takes a kind as a name, a :class:`Kind`, or a
    widget that carries one, so the bindings can hand over what a script named.
    This is the stand-in that proves the third form works before there is one.
    """

    def __init__(self, kind) -> None:
        self.kind = kind


# -- the rows themselves ---------------------------------------------------


def test_a_row_names_its_type_and_the_four_types_are_the_ones_the_language_has():
    assert TYPES == ("number", "string", "bool", "list")
    row = Prop("knob", "value", "number", 0.5, 0.0, 1.0, "value", "a level")
    assert (row.kind, row.name, row.type, row.default) == ("knob", "value",
                                                          "number", 0.5)
    assert row.pd_name == "value" and row.hint == "a level"


def test_a_prop_type_that_is_not_one_of_the_four_is_refused_by_name():
    with pytest.raises(PropError) as raised:
        Prop("knob", "colour", "colour", "#ff0000")
    said = str(raised.value)
    assert "knob.colour" in said and "'colour'" in said
    for word in TYPES:
        assert word in said


def test_a_row_without_a_name_is_the_table_being_wrong_not_the_script():
    for nothing in ("", "   ", None):
        with pytest.raises(PropError, match="has no name"):
            Prop("knob", nothing, "number", 0)


def test_an_inverted_range_is_refused_wherever_it_was_written():
    with pytest.raises(PropError) as raised:
        Prop("knob", "min", "number", 0, 9, 2)
    assert "inverted range" in str(raised.value)
    assert "min 9 is above max 2" in str(raised.value)


def test_a_range_on_a_row_that_cannot_hold_one_is_refused_too():
    with pytest.raises(PropError, match="a string prop"):
        Prop("knob", "label", "string", "", 0, 10)
    # and the same rule for a range that lives in a sibling row
    with pytest.raises(PropError, match="a bool prop"):
        Prop("knob", "visible", "bool", True, bounds=("min", "max"))


@pytest.mark.parametrize("word,wanted", [(" Label ", "label"), ("MAX", "max"),
                                         ("glyph_on", "glyph_on")])
def test_a_row_stores_its_name_the_way_the_table_reads_it(word, wanted):
    assert Prop("slider", word, "string").name == wanted


def test_a_default_goes_through_the_same_door_as_a_set():
    # a min of 10 cannot be paired with a default of 5 and then report 5
    assert Prop("knob", "value", "number", 5, 10, 20).default == 10.0
    assert Prop("knob", "size", "string", 440).default == "440"
    assert Prop("knob", "items", "list", ("a", "b")).default == ["a", "b"]


def test_a_default_that_is_not_one_of_the_choices_is_caught_at_the_table():
    with pytest.raises(PropError) as raised:
        Prop("slider", "size", "string", "chunky", choices=tuple(sorted(SIZES)))
    assert "cannot default to" in str(raised.value)
    assert "'md'" in str(raised.value)


def test_a_row_is_frozen_and_says_what_to_do_instead_of_editing_it():
    row = Prop("knob", "value", "number", 0.0)
    with pytest.raises(PropError) as raised:
        row.default = 5.0
    assert "knob.value' is a row of the table" in str(raised.value)
    assert "copy(default=...)" in str(raised.value)
    assert row.default == 0.0
    assert repr(row) == "<Prop knob.value number>"


def test_a_row_describes_itself_in_the_words_a_report_uses():
    said = str(Prop("slider", "value", "number", 120.0, 20.0, 300.0, "value",
                    "a position"))
    assert said.startswith("slider.value: number = 120.0 [20.0, 300.0]")
    assert "-> Pd 'value'" in said
    assert "read only" not in said
    assert "(read only)" in str(Prop("slider", "hovered", "bool", readonly=True))
    # a range that lives in other rows is shown as those rows, not as numbers
    assert " [min, max]" in str(get_kind("slider").get("value"))


# -- copying a row -----------------------------------------------------------


def test_copying_a_row_makes_a_row_the_table_can_trust_again():
    row = Prop("slider", "value", "number", 0.0, 0.0, 10.0)
    moved = row.copy(default=5.0, maximum=20.0)
    assert (moved.default, moved.maximum) == (5.0, 20.0)
    assert (row.default, row.maximum) == (0.0, 10.0), "the original is untouched"


def test_a_copy_that_revalidates_can_still_be_given_a_field_that_is_not_there():
    row = Prop("slider", "value", "number", 0.0)
    with pytest.raises(PropError) as raised:
        row.copy(colour="red")
    assert "has no 'colour' to change" in str(raised.value)
    with pytest.raises(PropError, match="inverted range"):
        row.copy(minimum=9.0, maximum=1.0)


# -- what a value means ------------------------------------------------------


@pytest.mark.parametrize("value,wanted", [
    (12.5, 12.5),
    (3, 3.0),
    ("12.5", 12.5),
    (True, 1.0),
    (False, 0.0),
])
def test_a_number_takes_the_forms_a_widget_and_a_text_field_both_hand_over(
        value, wanted):
    assert Prop("knob", "v", "number").coerce(value) == wanted


@pytest.mark.parametrize("value", ["wide", [], None, ()])
def test_a_number_that_cannot_be_a_number_is_refused_naming_both_sides(value):
    with pytest.raises(PropError) as raised:
        Prop("knob", "v", "number").coerce(value)
    assert "is a number, not a" in str(raised.value)


@pytest.mark.parametrize("value,wanted", [
    ("440", "440"),
    (440.0, "440"),
    (440.5, "440.5"),
    (2, "2"),
    (True, "true"),
    (False, "false"),
])
def test_a_string_takes_a_number_without_writing_a_trailing_zero(value, wanted):
    # "440.0" in a label is the difference between a box that reads like a patch
    # and one that reads like a spreadsheet
    assert Prop("knob", "label", "string").coerce(value) == wanted


def test_a_list_takes_the_shapes_a_pd_symbol_list_or_a_row_of_labels_arrives_as():
    assert Prop("knob", "items", "list").coerce(("a", "b")) == ["a", "b"]
    assert Prop("knob", "items", "list").coerce([1, 2]) == [1, 2]
    for wrong in ("a b", 3, None):
        with pytest.raises(PropError):
            Prop("knob", "items", "list").coerce(wrong)


@pytest.mark.parametrize("word", ["true", "yes", "on", "1", "TRUE", " Off "])
def test_the_words_a_script_has_for_a_switch_all_mean_one_thing(word):
    row = Prop("knob", "flag", "bool")
    want = word.strip().lower() not in ("false", "no", "off", "0")
    assert row.coerce(word) is want


def test_a_bool_refuses_the_word_it_cannot_make_up_its_mind_from():
    with pytest.raises(PropError, match="is a bool, not a str"):
        Prop("knob", "flag", "bool").coerce("maybe")


def test_an_unset_value_is_empty_rather_than_missing_so_nobody_tests_for_none():
    assert Prop("k", "v", "number").settle(None) == 0.0
    assert Prop("k", "v", "string").settle(None) == ""
    assert Prop("k", "v", "bool").settle(None) is False
    assert Prop("k", "v", "list").settle(None) == []
    # and the empty list is a new one, not a row of the table shared out
    assert Prop("k", "v", "list").settle(None) is not \
        Prop("k", "v", "list").settle(None)


# -- what a wrong value costs ------------------------------------------------


def test_a_number_past_the_end_of_its_range_comes_back_at_the_end():
    row = Prop("knob", "v", "number", 0.0, 0.0, 1.0)
    assert row.settle(4.0) == 1.0
    assert row.settle(-4.0) == 0.0
    assert row.settle(0.25) == 0.25


def test_only_one_end_of_a_range_having_a_limit_is_allowed():
    row = Prop("knob", "v", "number", 0.0, 0.0, None)
    assert row.settle(9e9) == 9e9
    assert row.settle(-1.0) == 0.0


def test_a_word_that_is_not_one_of_the_ones_the_row_allows_lists_the_ones_it_allows(
        ):
    row = Prop("slider", "size", "string", "md", choices=tuple(sorted(SIZES)))
    with pytest.raises(PropError) as raised:
        row.settle("huge")
    said = str(raised.value)
    assert "huge" in said
    for word in SIZES:
        assert f"'{word}'" in said
    assert "''" not in said, "this row has no empty choice to offer"


def test_a_row_the_pointer_writes_is_documented_readable_and_not_settable():
    row = get_kind("slider").get("hovered")
    assert row.readonly is True and row.default is False
    assert Prop("slider", "hovered", "bool").settle(True) is True
    with pytest.raises(PropError) as raised:
        get_kind("slider").settable("hovered")
    assert "written by the pointer or the keyboard" in str(raised.value)


def test_a_prop_the_kind_does_not_have_names_the_ones_it_does_have():
    with pytest.raises(PropError) as raised:
        settle("slider", "nonsense", 1)
    said = str(raised.value)
    assert "has no prop 'nonsense'" in said
    for word in ("value", "min", "role", "size"):
        assert word in said


def test_a_settled_value_does_not_move_the_second_time():
    row = Prop("knob", "v", "number", 0.0, 0.0, 10.0)
    assert row.settle(row.settle(7)) == 7.0
    assert row.would_move(7.0, 7.4) is True
    assert row.would_move(7.0, 7.0) is False
    # an overshoot that lands where it already was is not a change at all, which
    # is what keeps a two-way LINK_VALUE from echoing itself forever
    assert row.would_move(10.0, 99.0) is False


# -- ranges that live in other rows ------------------------------------------


def test_a_sliders_range_is_two_of_its_own_props_not_a_number_in_the_table():
    built = settled("slider", {"label": "BPM", "min": 20, "max": 300,
                               "value": 120})
    assert (built["value"], built["min"], built["max"]) == (120.0, 20.0, 300.0)
    assert settle("slider", "value", 400, built) == 300.0
    assert settle("slider", "value", 5, built) == 20.0


@pytest.mark.parametrize("values", [
    {"value": 120, "min": 20, "max": 300},
    {"min": 20, "max": 300, "value": 120},
    {"max": 300, "value": 120, "min": 20},
])
def test_a_constructor_may_state_its_props_in_whichever_order_it_reads_best(
        values):
    # the range rows have to be settled before the value is, or 120 BPM arrives as
    # 1.0 - the biggest default in the table - because value was clamped against
    # the limits the defaults declared
    assert settled("slider", values)["value"] == 120.0


def test_moving_a_bound_moves_the_range_a_value_is_read_against():
    built = settled("slider", {"min": 0, "max": 1})
    assert settle("slider", "value", 0.5, built) == 0.5
    wider = dict(built, max=100)
    assert settle("slider", "value", 42, wider) == 42.0


def test_a_value_can_be_settled_into_a_widgets_own_state_map():
    held = states("slider", {"min": 10, "max": 20})
    assert held["min"].value == 10.0
    assert settle("slider", "value", 99, held) == 20.0


def test_an_indexed_value_is_bounded_by_the_list_it_points_into():
    colours = settled("radio", {"labels": ["sine", "tri", "saw", "noise"]})
    assert colours["value"] == 0.0
    assert settle("radio", "value", 9, colours) == 3.0
    assert settle("radio", "value", -2, colours) == 0.0
    # shortening the list pulls the index back inside it, the way a dropdown with
    # one fewer entry must not be left pointing past the end
    assert settle("radio", "value", 3, dict(colours, labels=["a"])) == 0.0


def test_a_button_holds_a_number_that_cannot_be_a_position():
    built = settled("button")
    assert built["value"] == 0.0
    assert settle("button", "value", 400, built) == 1.0


def test_a_kind_without_a_value_is_not_given_one_to_be_confused_by():
    for kind in ("panel", "text", "icon"):
        assert has(kind, "value") is False, kind
        with pytest.raises(PropError, match="has no prop 'value'"):
            settle(kind, "value", 1)


# -- the catalog -------------------------------------------------------------


def test_the_catalog_is_the_kinds_the_plan_listed_and_nothing_else():
    assert catalog_names() == CATALOG
    assert tuple(KINDS) == CATALOG
    assert registered_names() == ()


@pytest.mark.parametrize("kind", CATALOG)
def test_every_kind_starts_from_the_rows_it_needs_and_no_more(kind):
    names = set(prop_names(kind))
    for word in ("label", "send", "recv", "role", "size", "tooltip", "visible",
                 "enabled", "hovered", "pressed", "focused", "selected",
                 "pd_object", "glyph", "glyph_on", "glyph_off"):
        assert word in names, (kind, word)
    value_rows = names & set(("value", "min", "max", "step", "log", "ticks",
                              "editable", "labels", "columns"))
    if not has(kind, "value"):
        # a panel, an icon - and a scope or a curve, which show a buffer or a
        # shape and hold no value for a preset to save
        assert value_rows == set()
    else:
        assert "value" in value_rows, kind


@pytest.mark.parametrize("kind", CATALOG)
def test_every_kind_can_be_built_from_its_table_alone(kind):
    built = settled(kind)
    assert set(built) == set(prop_names(kind))
    assert {"label", "role", "size", "pd_object"} <= set(built), kind
    assert built["hovered"] is False and built["visible"] is True


@pytest.mark.parametrize("kind", CATALOG)
def test_a_kinds_default_style_is_a_role_the_skin_actually_has(kind):
    role = settled(kind)["role"]
    assert as_role(role) == role, (kind, role)
    assert role in ROLES


@pytest.mark.parametrize("kind", CATALOG)
def test_a_kinds_default_size_is_a_rung_and_its_default_glyphs_are_real_icons(
        kind):
    built = settled(kind)
    assert built["size"] in SIZES
    for word in ("glyph", "glyph_on", "glyph_off"):
        assert built[word] == "" or built[word] in icon_names(), (kind, word)


@pytest.mark.parametrize("kind", CATALOG)
def test_a_kinds_default_pd_box_is_a_box_or_nothing_it_twins_to(kind):
    found = pd_object_for(kind)
    assert found == "" or found in PD_BOXES, kind
    assert settled(kind)["pd_object"] == found


@pytest.mark.parametrize("kind", CATALOG)
def test_every_prop_of_every_kind_carries_the_sentence_a_tooltip_shows(kind):
    for prop in props_of(kind):
        assert hint_for(kind, prop.name) == prop.hint
        assert prop.hint, f"{kind}.{prop.name} is undocumented"


@pytest.mark.parametrize("kind", CATALOG)
def test_the_arguments_a_constructor_takes_are_props_a_setter_would_also_take(
        kind):
    for word in get_kind(kind).args:
        prop = get_kind(kind).get(word)
        assert prop.readonly is False, (kind, word)
    # and they are in the order the plan's examples spell them
    if kind in ("slider", "vslider", "number", "meter"):
        assert get_kind(kind).args == ("label", "min", "max", "value")


def test_the_two_kinds_that_show_a_choice_list_have_their_index_and_their_words():
    for kind in ("radio", "dropdown"):
        names = set(prop_names(kind))
        assert "labels" in names and "value" in names, kind
        assert get_kind(kind).get("value").bounds == ("", "labels")
    assert "columns" in prop_names("radio")
    assert "columns" not in prop_names("dropdown")


def test_the_plan_toggle_shows_play_when_off_and_pause_when_on_with_no_help():
    built = settled("toggle")
    assert built["glyph_off"] == "play" and built["glyph_on"] == "pause"
    assert built["role"] == "toggle"


def test_the_faces_a_window_is_made_of_point_at_their_own_roles():
    assert settled("panel")["role"] == "panel"
    assert settled("text")["role"] == "text"
    assert settled("icon")["role"] == "icon"
    assert pd_object_for("text") == "" and pd_object_for("icon") == ""
    assert pd_object_for("panel") == "cnv"


def test_the_kinds_that_agree_on_everything_except_their_box_are_one_line_apart():
    slider, vertical = get_kind("slider"), get_kind("vslider")
    assert vertical.base == "slider"
    assert prop_names("vslider") == prop_names("slider")
    assert slider.pd_object == "hslider" and vertical.pd_object == "vslider"
    # ...and it said nothing of its own, which is what a variant should look like
    assert vertical.declared == []
    assert settled("vslider")["pd_object"] == "vslider"
    assert settled("vslider")["role"] == settled("slider")["role"] == "track"


def test_a_meter_reads_a_level_and_a_number_box_takes_one_as_well():
    assert "editable" not in prop_names("meter")
    assert "step" not in prop_names("meter")
    assert "editable" in prop_names("number")
    assert settled("meter")["value"] == 0.0


# -- the placement rows: a name can leave the box for every kind -----------


def test_the_placement_modes_are_the_six_edges_a_word_can_take():
    assert PLACE_MODES == ("inside", "top", "bottom", "left", "right", "none")


@pytest.mark.parametrize("kind", CATALOG)
def test_every_kind_inherits_the_placement_rows_at_the_inside_default(kind):
    """A small control can push its name outside - and no kind opted out.

    These are ``_COMMON`` rows, so they land on every kind (the faces included)
    with the behaviour that keeps the current look: a word ``inside`` by default
    and zero nudges, and no offset on a kind that was never given a side.
    """
    built = settled(kind)
    assert built["label_place"] == "inside", kind
    assert built["value_place"] == "inside", kind
    for word in ("label_dx", "label_dy", "value_dx", "value_dy"):
        assert built[word] == 0.0, (kind, word)
    assert get_kind(kind).get("label_place").choices == PLACE_MODES
    assert get_kind(kind).get("value_place").choices == PLACE_MODES


@pytest.mark.parametrize("word", ("label_place", "value_place"))
def test_a_placement_word_outside_the_edge_list_is_refused_by_name(word):
    with pytest.raises(PropError) as raised:
        settle("knob", word, "above")
    assert "'above'" in str(raised.value)
    assert "inside" in str(raised.value) and "none" in str(raised.value)


@pytest.mark.parametrize("word,given", [("label_place", "top"),
                                        ("value_place", "top"),
                                        ("label_dx", 4.0),
                                        ("label_dy", 4.0),
                                        ("value_dx", 4.0),
                                        ("value_dy", 4.0)])
def test_no_placement_row_is_a_pd_box_argument(word, given):
    """A name's position is the drawing's business, never a Pd inlet.

    ``PD_EMIT`` only fires on rows that carry a ``pd_name``; the placement rows
    must have none, so setting one never invents an argument for a vanilla box.
    """
    row = get_kind("slider").get(word)
    assert row.pd_name == "", word
    assert word not in pd_arguments("slider", settled("slider", {word: given}))


# -- the two general canvases ---------------------------------------------


def test_the_plot_graphs_a_value_list_against_its_own_span():
    """A plot has no full scale, so it carries data and a window and no range."""
    plot = get_kind("plot")
    names = set(prop_names("plot"))
    assert {"data", "points"} <= names, plot
    assert plot.pd_object == "", "no vanilla [plot] box to twin to"
    # autoscaling reads the array's own min/max, so it must not carry value rows
    assert not names & set(("value", "min", "max", "step", "log")), plot
    assert plot.args == ("label",)
    assert settled("plot")["role"] == "grid"


def test_the_logview_is_a_scrollable_text_area_the_script_appends_to():
    view = get_kind("logview")
    names = set(prop_names("logview"))
    assert {"lines", "scroll"} <= names, view
    assert view.pd_object == "", "a log view is not a Pd box"
    assert view.args == ("label",)
    built = settled("logview", {"lines": ["a", "b"], "scroll": 3})
    assert built["lines"] == ["a", "b"]
    assert built["scroll"] == 3.0, "a row count is not a pixel offset"
    # scroll has a floor (never before the newest line) and no ceiling, because
    # how far the tail reaches is the box's height, decided by the face
    assert get_kind("logview").get("scroll").minimum == 0.0
    assert get_kind("logview").get("scroll").maximum is None
    assert settled("logview", {"scroll": -4})["scroll"] == 0.0


# -- inheritance -------------------------------------------------------------


def test_a_variant_overrides_the_defaults_it_stated_and_keeps_the_rest(bpm):
    assert bpm.base == "slider" and bpm.pd_object == "hslider"
    assert bpm.declared == ["min", "max", "ticks"]
    built = settled("bpm_slider", {"value": 500})
    assert (built["min"], built["max"], built["ticks"]) == (20.0, 300.0, 8.0)
    assert built["value"] == 300.0, "clamped to the range it inherited"
    assert built["role"] == "track", "inherited with nobody having said it"


def test_a_variants_defaults_do_not_reach_back_up_into_the_catalog(bpm):
    assert get_kind("slider").get("min").default == 0.0
    assert get_kind("bpm_slider").get("min").default == 20.0
    assert prop_names("slider") == prop_names("bpm_slider")


def test_an_inherited_row_says_which_kind_its_words_came_from(bpm):
    assert get_kind("bpm_slider").get("role").inherited == "slider"
    # restating a default does not mean the row was invented here
    assert get_kind("bpm_slider").get("min").inherited == "slider"
    assert bpm.chain() == ["bpm_slider", "slider"]


def test_a_variant_takes_its_bases_constructor_arguments_with_it(bpm):
    assert bpm.args == ("label", "min", "max", "value")
    assert define("wide", "bpm_slider").args == bpm.args


def test_a_row_added_to_a_base_reaches_the_variants_built_before_it(bpm):
    child = define("child", "bpm_slider")
    assert has("child", "swing") is False
    define_prop("bpm_slider", "swing", "number", 0.0)
    assert has("child", "swing") is True
    assert get_kind("child").get("swing").inherited == "bpm_slider"
    assert settled("child")["swing"] == 0.0


def test_a_variant_that_stated_a_row_wins_against_its_base_later(bpm):
    child = define("child", "bpm_slider")
    define_prop("child", "swing", "number", 5.0)
    define_prop("bpm_slider", "swing", "number", 0.0)
    assert settled("child")["swing"] == 5.0
    assert settled("bpm_slider")["swing"] == 0.0


# -- a script defining a kind ------------------------------------------------


def test_a_script_kind_starts_with_the_plumbing_and_no_value_rows():
    stepper = define("stepper")
    assert stepper.script is True
    assert "value" not in prop_names("stepper")
    assert has("stepper", "role") and has("stepper", "hovered")
    assert stepper.pd_object == "" and stepper.args == ()


def test_the_plan_stepper_is_four_rows_and_a_box_to_write_them_to():
    define("stepper")
    define_prop("stepper", "min", "number", 0)
    define_prop("stepper", "max", "number", 300)
    define_prop("stepper", "step", "number", 1)
    value = define_prop("stepper", "value", "number", 120, 0, 300, "value")
    assert value.bounds == ("min", "max"), "it found the bounds on its own"
    built = settled("stepper", {"value": 900})
    assert (built["value"], built["step"], built["pd_object"]) == (300.0, 1.0,
                                                                   "")
    # a row reaches Pd only when the table names it, which is the whole argument
    # for one table: min and max are real props the widget draws with, and this
    # kind's author never said they are box arguments
    assert pd_arguments("stepper", built) == {"value": 300.0}
    assert get_kind("stepper").get("min").pd_name == ""
    assert get_kind("stepper").get("value").pd_name == "value"
    define_prop("stepper", "bass", "number", 0, pd_name="bass")
    assert pd_arguments("stepper", built) == {"value": 300.0, "bass": 0.0}


def test_a_new_kind_can_be_given_a_pd_box_the_drawing_layer_does_not_know(bpm):
    assert define("filter", "slider", "vslider").pd_object == "vslider"
    assert settled("filter")["pd_object"] == "vslider"
    assert define("noisy", "", "", "").pd_object == ""


def test_redefining_a_builtin_kind_is_refused_rather_than_left_to_win():
    for word in CATALOG:
        with pytest.raises(PropError) as raised:
            define(word, "slider")
        assert "built-in widget kind" in str(raised.value), word


def test_redefining_a_kind_the_program_already_made_is_refused_too(bpm):
    with pytest.raises(PropError, match="already defined by this program"):
        define("bpm_slider")
    # and through the catalog's own door, which is the only other way in
    from crude.ui.props import _kind
    with pytest.raises(PropError, match="already a widget kind"):
        _kind("bpm_slider")
    with pytest.raises(PropError, match="already a widget kind"):
        _kind("slider", "cnv")


def test_a_base_that_is_not_there_is_answered_with_the_kinds_that_are():
    with pytest.raises(PropError) as raised:
        define("odd", "slir")
    assert "slir" in str(raised.value)
    for word in CATALOG:
        assert word in str(raised.value)


def test_a_nameless_kind_and_a_nameless_prop_are_both_the_table_being_wrong():
    with pytest.raises(PropError, match="widget kind has no name"):
        define("   ")
    define("stepper")
    with pytest.raises(PropError, match="has no name"):
        define_prop("stepper", "  ", "number", 0)


def test_a_script_cannot_add_a_prop_to_a_kind_it_did_not_define():
    for word in CATALOG:
        with pytest.raises(PropError, match="built-in kind"):
            define_prop(word, "extra", "number", 1)


def test_a_prop_declared_twice_on_one_kind_is_a_script_that_meant_to_say_it_once(
        bpm):
    with pytest.raises(PropError) as raised:
        define_prop("bpm_slider", "min", "number", 5)
    assert "bpm_slider.min' is already defined" in str(raised.value)


def test_a_duplicate_check_that_uses_the_stored_name_rather_than_the_spelling():
    define("stepper")
    define_prop("stepper", "Rate", "number", 1)
    assert has("stepper", "rate") is True
    with pytest.raises(PropError, match="already defined"):
        define_prop("stepper", "RATE", "number", 2)


def test_a_new_row_gets_the_range_and_the_kind_of_thing_it_claims_to_be():
    define("stepper")
    for call, wanted in (
            (("stepper", "n", "colour", 1), "a prop is number or string"),
            (("stepper", "n", "number", 1, 9, 2), "inverted range"),
    ):
        with pytest.raises(PropError) as raised:
            define_prop(*call)
        assert wanted in str(raised.value)


def test_a_row_that_takes_its_range_from_a_row_that_does_not_exist_is_refused():
    with pytest.raises(PropError) as raised:
        define("broken", rows=("value",))
    assert "takes its range from 'min'" in str(raised.value)
    assert "which 'broken' does not have" in str(raised.value)


def test_a_constructor_argument_no_row_can_hold_is_refused_at_the_declaration():
    with pytest.raises(PropError) as raised:
        define("odd", args=("width",))
    assert "no such prop" in str(raised.value)


def test_a_kind_can_be_asked_for_a_row_it_does_not_have_in_its_tweak_table():
    from crude.ui.props import _kind
    with pytest.raises(PropError, match="has no 'ticks' row to change"):
        _kind("odd", rows=(), ticks={"default": 4})


def test_a_tweak_that_is_not_a_mapping_of_fields_is_refused_rather_than_spread():
    from crude.ui.props import _kind
    with pytest.raises(PropError, match="a change is a mapping"):
        _kind("odd", rows=(), role="track")


def test_a_row_a_kind_asks_for_by_a_name_that_is_not_a_row_lists_the_ones_that_are(
        ):
    from crude.ui.props import _kind
    with pytest.raises(PropError) as raised:
        _kind("odd", rows=("value", "wobble"))
    assert "wobble" in str(raised.value)
    for word in ("value", "min", "labels", "columns"):
        assert word in str(raised.value)


def test_a_restated_row_keeps_the_words_it_was_built_from_unless_it_replaces_them(
        ):
    define("bpm_slider", "slider")
    define_prop("bpm_slider", "min", "number", 20)
    row = get_kind("bpm_slider").get("min")
    assert row.default == 20.0
    assert row.hint == get_kind("slider").get("min").hint
    assert row.pd_name == "min"
    assert row.type == "number"
    # a declaration that says nothing about the type keeps the inherited one
    define_prop("bpm_slider", "max")
    assert get_kind("bpm_slider").get("max").type == "number"
    assert get_kind("bpm_slider").get("max").default == 1.0


# -- the run boundary --------------------------------------------------------


def test_a_registered_kind_lives_for_one_run_and_the_catalog_outlives_it(bpm):
    assert registered_names() == ("bpm_slider",)
    assert reset() == 1
    assert registered_names() == ()
    assert tuple(KINDS) == CATALOG
    assert exists("bpm_slider") is False
    # asking a kind that is gone what it holds is still an error, so a preset
    # written by last run's script does not read as an empty widget
    with pytest.raises(PropError, match="not a widget kind"):
        has("bpm_slider", "min")
    # the name is free again, which is what lets a program define it a second time
    assert define("bpm_slider", "slider").declared == []


def test_resetting_loses_nothing_the_program_did_not_define(bpm):
    assert reset() == 1
    assert settled("slider")["min"] == 0.0
    assert settled("vslider")["pd_object"] == "vslider"
    assert document()["kinds"][0]["kind"] == "panel"


def test_a_kind_that_is_not_there_is_reported_as_one_and_a_check_can_ask_quietly(
        ):
    assert exists("slider") is True
    assert exists("SLIDER ") is True
    assert exists("slir") is False
    assert exists(None) is False
    assert exists(get_kind("slider")) is True
    with pytest.raises(PropError) as raised:
        get_kind("slir")
    assert "not a widget kind" in str(raised.value)


# -- the three forms a kind comes in -----------------------------------------


@pytest.mark.parametrize("value", ["slider", " Slider ", get_kind("slider"),
                                   _Widget("slider"), _Widget(get_kind("slider"))])
def test_a_kind_can_be_named_or_handed_over_or_worn_by_a_widget(value):
    assert get_kind(value).name == "slider"
    assert settle(value, "min", 5) == 5.0
    assert hint_for(value, "min") == hint_for("slider", "min")
    assert digest(value) == digest("slider")


def test_a_widget_carrying_a_kind_that_is_not_there_is_still_answered_with_a_no():
    assert exists(_Widget("slir")) is False
    with pytest.raises(PropError, match="not a widget kind"):
        get_kind(_Widget("slir"))


def test_a_prop_name_reaches_the_same_row_whichever_way_it_was_spelled():
    assert has("slider", "LABEL") is True
    assert prop_names("slider") == tuple(get_kind("slider").props)


# -- what Pd is told ---------------------------------------------------------


def test_the_pd_argument_names_are_the_rows_the_table_gave_them_to():
    built = settled("slider", {"label": "BPM", "min": 20, "max": 300,
                               "value": 120, "send": "bpm"})
    found = pd_arguments("slider", built)
    assert found == {"label": "BPM", "send": "bpm", "value": 120.0, "min": 20.0,
                     "max": 300.0, "step": 0.0, "log": False}
    # the rows a widget draws with and a box knows nothing about are not there
    for word in ("role", "size", "ticks", "editable", "tooltip", "visible",
                 "glyph", "hovered", "pressed", "focused", "selected"):
        assert word not in found, word


@pytest.mark.parametrize("kind", CATALOG)
def test_no_argument_is_ever_handed_over_unsettled(kind):
    # a hand-written map gets the same clamped, coerced values the widget itself
    # would report, so a patch never holds a number the widget would refuse
    given = {word: value for word, value in (
        ("value", 400), ("min", 20), ("max", 300), ("label", 440),
        ("log", 1), ("labels", ["a", "b"])) if has(kind, word)}
    found = pd_arguments(kind, given)
    for name, value in found.items():
        assert isinstance(value, (float, int, str, bool, list)), (kind, name)
    if "value" in found:
        assert found["value"] == settle(kind, "value", 400, given)
    if "label" in found:
        assert found["label"] == "440", "the label arrived as a number"
    if "log" in found:
        assert found["log"] is True


@pytest.mark.parametrize("kind", CATALOG)
def test_an_unused_symbol_is_left_out_of_the_patch_rather_than_written_blank(kind
                                                                              ):
    found = pd_arguments(kind, {"send": "", "recv": "", "label": "x"})
    assert "send" not in found and "recv" not in found
    assert found.get("label") == "x"


def test_pd_object_is_resolved_in_the_plans_own_order():
    assert pd_object_for("slider") == "hslider"
    # the prop first: a script that wrote it meant it
    assert pd_object_for("slider", {"pd_object": "nbx"}) == "nbx"
    assert pd_object_for("slider", {"label": "BPM"}) == "hslider"
    # and clearing it is an answer, not a fall-back to the kind
    assert pd_object_for("slider", {"pd_object": ""}) == ""
    assert pd_object_for(get_kind("vslider")) == "vslider"


def test_a_kind_with_no_pd_object_leaves_the_data_path_to_plain_wiring():
    define("stepper")
    assert pd_object_for("stepper") == ""
    built = settled("stepper", {"send": "bpm", "recv": "bpm"})
    assert pd_arguments("stepper", built) == {"send": "bpm", "recv": "bpm"}


# -- the paint cache key ------------------------------------------------------


def test_the_same_values_digest_to_the_same_string_whatever_order_they_arrived_in(
        ):
    one = settled("slider", {"value": 120, "min": 20, "max": 300})
    two = settled("slider", {"MAX": 300, "MIN": 20, "VALUE": 120})
    assert digest("slider", one) == digest("slider", two)
    assert len(digest("slider", one)) == 12


def test_a_different_kind_with_the_same_numbers_is_a_different_thing_to_draw():
    assert digest("button") != digest("toggle")
    assert digest("slider") != digest("vslider")


@pytest.mark.parametrize("kind", CATALOG)
def test_every_prop_on_its_own_changes_the_paint_key(kind):
    base = settled(kind)
    for prop in props_of(kind):
        moved = dict(base)
        moved[prop.name] = _other(prop, base)
        assert digest(kind, moved) != digest(kind, base), (kind, prop.name)


def _other(prop: Prop, held) -> object:
    """A value of the right shape and different from the one the row holds."""
    if prop.type == "number":
        return float(held[prop.name]) + 1.0
    if prop.type == "bool":
        return not held[prop.name]
    if prop.type == "list":
        # one entry *added*, not a fixed replacement: a ``data`` row defaults to
        # the empty list, and a settle of any non-empty list is that list, so a
        # replacement that happened to also be empty would digest the same and
        # this test would stop proving the row is in the paint key at all
        return [*held[prop.name], "different"]
    return "a" if held[prop.name] != "a" else "b"


def test_a_finished_widgets_map_is_a_valid_key_rather_than_an_invitation_to_raise(
        ):
    # the four pointer-written rows are in the map, and they have to be in the
    # hash: a hovered widget is not the widget that was drawn a frame ago
    held = states("slider", {"value": 120, "min": 0, "max": 300})
    assert isinstance(held["hovered"], State)
    hovered = dict(held, hovered=State(True))
    assert digest("slider", hovered) != digest("slider", held)
    assert digest("slider", hovered) == digest("slider", hovered)


# -- SET and a constructor being the same door -------------------------------


@pytest.mark.parametrize("kind", CATALOG)
def test_a_value_the_table_settled_once_does_not_move_the_second_time(kind):
    built = settled(kind)
    for prop in props_of(kind):
        if prop.readonly:
            continue
        assert settle(kind, prop.name, built[prop.name],
                      built) == built[prop.name], (kind, prop.name)


@pytest.mark.parametrize("kind", CATALOG)
def test_building_a_widget_and_setting_the_same_props_onto_it_agree(kind):
    sample = {"label": "x", "value": 0.5, "min": 0, "max": 2, "size": "lg",
              "enabled": False, "role": "accent", "labels": ["a", "b"]}
    given = {word: value for word, value in sample.items() if has(kind, word)}
    built = settled(kind, given)
    # the same rule the table follows internally: a row that another row takes a
    # range from has to be written before the one that reads it, or the second
    # answer depends on the order a dict happened to be built in
    bounds = {word for prop in props_of(kind) for word in prop.bounds if word}
    order = [word for word in given if word in bounds]
    order += [word for word in given if word not in order]
    by_hand = settled(kind)
    for word in order:
        by_hand[word] = settle(kind, word, given[word], by_hand)
    for word in given:
        assert built[word] == by_hand[word], (kind, word)


def test_a_value_is_clamped_by_the_rows_that_were_in_force_when_it_was_written():
    # which is what a live widget means: a dropdown with no entries cannot be
    # pointing at the third one, and it moves when the list grows into it
    held = settled("dropdown")
    assert settle("dropdown", "value", 2, held) == 0.0
    held["labels"] = ["a", "b", "c"]
    assert settle("dropdown", "value", 2, held) == 2.0


def test_a_name_no_row_answers_to_is_refused_by_the_builder_rather_than_dropped():
    with pytest.raises(PropError, match="has no prop 'width'"):
        settled("slider", {"width": 20})
    with pytest.raises(PropError, match="has no prop"):
        digest("slider", {"width": 20})


def test_a_constructor_cannot_be_used_to_arrive_at_a_hovered_widget():
    with pytest.raises(PropError, match="written by the pointer"):
        settled("slider", {"hovered": True})
    # a saved set of props, which is only the written ones, goes back in cleanly
    held = states("slider", {"min": 0, "max": 10, "value": 4})
    saved = {name: found.value for name, found in held.items() if found.written}
    assert saved == {"min": 0.0, "max": 10.0, "value": 4.0}
    assert settled("slider", saved)["value"] == 4.0


def test_states_marks_what_was_written_and_value_of_reads_either_half():
    held = states("button", {"label": "Run"})
    assert held["label"].written is True
    assert held["value"].written is False
    assert value_of("button", held, "label") == "Run"
    assert value_of("button", held, "value") == 0.0
    assert value_of("button", None, "value") == 0.0
    assert value_of("button", {"value": 1.0}, "value") == 1.0
    with pytest.raises(PropError, match="has no prop 'nope'"):
        value_of("button", {}, "nope")


def test_a_row_the_table_settles_is_the_row_a_report_can_name():
    row = get_kind("slider").get("min")
    assert row.settle(20) == 20.0
    assert row.pd_name == "min"
    with pytest.raises(PropError, match="slider.min' is a number"):
        row.settle("wide")


# -- describing itself -------------------------------------------------------


def test_the_dump_is_the_same_table_the_validator_read():
    found = document()
    assert found["schema"] == SCHEMA
    assert found["types"] == list(TYPES)
    assert found["pd_boxes"] == list(PD_BOXES)
    assert [kind["kind"] for kind in found["kinds"]] == list(KINDS)
    assert json.dumps(found)


def test_one_kinds_dump_carries_everything_a_docs_page_needs(bpm):
    found = document("bpm_slider")
    assert found["base"] == "slider" and found["script"] is True
    assert found["pd_object"] == "hslider"
    assert found["args"] == ["label", "min", "max", "value"]
    assert found["declared"] == ["min", "max", "ticks"]
    assert found["hint"] == ""
    rows = {row["name"]: row for row in found["props"]}
    assert rows["min"] == {"name": "min", "type": "number", "default": 20.0,
                           "read_only": False, "pd_name": "min",
                           "hint": rows["min"]["hint"], "inherited": "slider"}
    assert rows["value"]["bounds"] == ["min", "max"]
    assert rows["hovered"]["read_only"] is True
    assert rows["size"]["choices"] == sorted(SIZES)
    assert json.dumps(found)


def test_dumping_one_kind_and_the_whole_table_are_two_different_questions(bpm):
    assert document("slider")["kind"] == "slider"
    assert len(document()["kinds"]) == len(CATALOG) + 1
    with pytest.raises(PropError, match="not a widget kind"):
        document("slir")


def test_a_kind_and_a_prop_can_say_what_they_are_when_a_log_needs_to_say_it(bpm):
    assert str(bpm) == ("Kind(bpm_slider, 30 props, from slider, "
                        "emits [hslider], defined by script)")
    assert str(get_kind("panel")) == "Kind(panel, 22 props, emits [cnv])"
    assert repr(get_kind("slider")) == "<Kind slider>"
    assert str(get_kind("slider").get("size")).startswith(
        "slider.size: string = 'md'")


def test_the_ui_error_a_script_can_catch_is_the_one_this_layer_raises():
    assert issubclass(PropError, UIError)
    with pytest.raises(UIError):
        settle("slider", "max", "wide")


def test_prop_boxes_are_the_boxes_the_plan_named():
    assert PD_BOXES == ("bng", "cnv", "dropdown", "hslider", "nbx", "radio",
                        "tgl", "vu", "vslider")
    for kind in CATALOG:
        found = pd_object_for(kind)
        assert found in PD_BOXES or found == ""
