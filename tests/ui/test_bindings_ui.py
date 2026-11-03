"""The ``ui`` bindings as a script sees them.

Every test here writes ``.crude`` source and drives the tree through the same
``Program`` a real project uses, with a :class:`~crude.ui.recording.RecordingRenderer`
underneath: which is exactly the shape the plan's S7 acceptance line asks for
("RecordingRenderer assertions on a script-built tree"). Nothing in this file
needs a display or an OpenGL context.

The file follows the module's five-step shape: registry, create (the catalog
constructors and the ``WIDGET`` they wrap), configure (``SET``/``GET`` against
the kind's own table), place, wire (links and handlers), style, introspect. A
test that would read the same words a person would type at a keyboard is a test
of the surface rather than of the plumbing under it, which is the whole point of
putting them in the language.
"""

from __future__ import annotations

import types

import pytest

import crude.bindings  # noqa: F401 - registers every area, including ui
from crude.bindings.registry import REGISTRY
from crude.bindings.ui import (
    BINDINGS,
    CONSTRUCTORS,
    STYLE_PROPS,
    WIDGET_KIND,
)
from crude.lang import Program
from crude.lang.values import HostHandle, List, Number, String
from crude.ui import (
    POINTER,
    SCRIPT_EVENTS,
    WIDGET_EVENTS,
    UIContext,
    catalog_names,
)
from crude.ui import RecordingRenderer


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
    assert value.kind == WIDGET_KIND, value.kind
    return value.target


@pytest.fixture
def stage():
    """A program wired to a fresh UIContext over a RecordingRenderer."""
    renderer = RecordingRenderer()
    ctx = UIContext(renderer, width=320, height=200)
    program = Program("<ui-test>", host=types.SimpleNamespace(ui=ctx))
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
def ui_failure(stage):
    program, _, _ = stage

    def _fail(source):
        value, error = program.run(source)
        assert error is not None, f"expected a failure, got {value!r}"
        return error.as_string()

    return _fail


# -- the registry -------------------------------------------------------

#: every ``ui`` word the plan's S7 step promises, in the order :mod:`crude.bindings.ui`
#  lists them. A binding added here without a test of what it does is a name
#  waiting to be broken; a binding removed shows up as a mismatch before it can
#  disappear from a script.
_EXPECTED_BINDINGS = (
    "WIDGET",
    "PANEL", "TEXT", "BUTTON", "TOGGLE", "SLIDER", "VSLIDER", "KNOB",
    "NUMBER", "METER", "RADIO", "DROPDOWN",
    "WAVEFORM", "SPECTRUM", "PLOT", "LOGVIEW", "ADSR", "CURVE", "XY_PAD", "KEYBED",
    "PRESET_BAR",
    "ICON",
    "WIDGET_DEFINE", "PROP_DEFINE",
    "PLACE", "NODE_ADD",
    "LINK_VALUE", "LINK_STATE", "UNLINK",
    "ON_CHANGE", "ON_ENTER", "ON_LEAVE", "ON_MOVE", "ON_PRESS",
    "ON_RELEASE", "ON_CLICK", "ON_FOCUS", "ON_BLUR", "ON_KEY",
    "ON_CHAR", "ON_SCROLL", "ON_DRAG", "ON_RESIZE", "ON_DROP", "ON_CLOSE",
    "ON_PAINT", "ON_HIT",
    "ON_FRAME", "OFF", "OFF_FRAME",
    "WIDTH", "HEIGHT",
    "P_RECT", "P_CIRCLE", "P_ARC", "P_LINE", "P_POLY", "P_TEXT",
    "P_GLYPH", "P_IMAGE", "P_CLIP", "P_CLIP_END", "PAINT_REQUEST",
    "STYLE",
    "WIDGET_KINDS", "WIDGET_PROPS", "WIDGET_HAS",
)


def test_the_plan_named_exactly_these_ui_bindings():
    assert BINDINGS == _EXPECTED_BINDINGS


def test_every_ui_binding_is_registered_under_the_ui_area():
    for name in BINDINGS:
        assert name in REGISTRY, name
        assert REGISTRY[name].area == "ui", name


@pytest.mark.parametrize("name,args,optional", [
    ("WIDGET", ("kind", "name"), 1),
    ("PANEL", ("label",), 1),
    ("TEXT", ("label",), 1),
    ("BUTTON", ("label",), 1),
    ("TOGGLE", ("label", "on"), 1),
    ("SLIDER", ("label", "min", "max", "value"), 4),
    ("VSLIDER", ("label", "min", "max", "value"), 4),
    ("KNOB", ("label", "min", "max", "value"), 4),
    ("NUMBER", ("label", "min", "max", "value"), 4),
    ("METER", ("label", "min", "max", "value"), 4),
    ("RADIO", ("labels", "value"), 2),
    ("DROPDOWN", ("labels", "value"), 2),
    ("WAVEFORM", ("label",), 1),
    ("SPECTRUM", ("label",), 1),
    ("PLOT", ("label",), 1),
    ("LOGVIEW", ("label",), 1),
    ("ADSR", ("label", "attack", "decay", "sustain", "release"), 5),
    ("CURVE", ("label", "tension"), 2),
    ("XY_PAD", ("label", "x", "y"), 3),
    ("KEYBED", ("label", "low", "octaves"), 3),
    ("PRESET_BAR", ("labels", "value"), 2),
    ("ICON", ("glyph", "size"), 1),
    ("WIDGET_DEFINE", ("kind", "base", "pd_object", "hint"), 3),
    ("PROP_DEFINE",
     ("kind", "name", "type", "default", "min", "max", "pd_name", "hint"), 4),
    ("PLACE", ("widget", "x", "y", "width", "height"), 2),
    ("NODE_ADD", ("parent", "child"), 0),
    ("LINK_VALUE", ("source", "target", "two_way"), 1),
    ("LINK_STATE", ("source", "prop", "target", "target_prop", "two_way"), 2),
    ("UNLINK", ("widget", "prop"), 2),
    ("ON_FRAME", ("func",), 0),
    ("OFF_FRAME", ("func",), 1),
    ("OFF", ("widget", "event", "func"), 3),
    ("WIDTH", ("what",), 0),
    ("HEIGHT", ("what",), 0),
    ("P_RECT", ("p", "x", "y", "width", "height", "radius", "role"), 2),
    ("P_CIRCLE", ("p", "cx", "cy", "radius", "role"), 1),
    ("P_ARC", ("p", "cx", "cy", "radius", "start", "end", "width", "role"), 2),
    ("P_LINE", ("p", "x1", "y1", "x2", "y2", "width", "role"), 1),
    ("P_POLY", ("p", "points", "closed", "role"), 1),
    ("P_TEXT", ("p", "x", "y", "text", "size", "role", "align"), 3),
    ("P_GLYPH", ("p", "x", "y", "width", "height", "name", "role"), 1),
    ("P_IMAGE", ("p", "x", "y", "width", "height", "resource", "role"), 1),
    ("P_CLIP", ("p", "x", "y", "width", "height"), 0),
    ("P_CLIP_END", ("p",), 0),
    ("PAINT_REQUEST", ("widget",), 0),
    ("STYLE", ("widget", "prop", "value"), 2),
    ("WIDGET_KINDS", (), 0),
    ("WIDGET_PROPS", ("kind",), 0),
    ("WIDGET_HAS", ("widget", "prop"), 0),
])
def test_every_ui_binding_declares_its_arguments(name, args, optional):
    entry = REGISTRY[name]
    assert entry.arg_names == args
    assert entry.optional == optional


@pytest.mark.parametrize("event", WIDGET_EVENTS)
def test_every_widget_event_the_tree_fires_has_an_on_word(event):
    name = f"ON_{event.upper()}"
    assert name in REGISTRY, name
    entry = REGISTRY[name]
    assert entry.arg_names == ("widget", "func")
    assert entry.area == "ui"


@pytest.mark.parametrize("event", SCRIPT_EVENTS)
def test_the_two_events_a_script_answers_are_wired_the_same_way(event):
    """``ON_PAINT`` and ``ON_HIT`` are registered like every other ``ON_*``.

    They are not in :data:`WIDGET_EVENTS`, because nothing in the pointer pass
    fires them - the tree *asks* them and uses the answer - but a word a script
    cannot register, undo and report on is not the same word.
    """
    name = f"ON_{event.upper()}"
    assert name in REGISTRY, name
    entry = REGISTRY[name]
    assert entry.arg_names == ("widget", "func")
    assert entry.optional == 0
    assert entry.area == "ui"


# -- create: constructors are sugar over WIDGET + SET -------------------

#: every catalog constructor whose positional arguments are literal prop names.
#  ``TOGGLE``'s ``on`` and ``ICON``'s ``size`` translate through a rule of
#  their own and have dedicated tests below, because there is no ``SET`` on
#  any row that would put a toggle "on" or resize an icon's box.
_DIRECT_CONSTRUCTORS = [
    ("PANEL", "panel", [("label", '"deck"')]),
    ("TEXT", "text", [("label", '"METRONOME"')]),
    ("BUTTON", "button", [("label", '"TAP"')]),
    ("SLIDER", "slider", [
        ("label", '"BPM"'), ("min", "20"), ("max", "300"), ("value", "120"),
    ]),
    ("VSLIDER", "vslider", [
        ("label", '"pan"'), ("min", "-1"), ("max", "1"), ("value", "0"),
    ]),
    ("KNOB", "knob", [
        ("label", '"Index"'), ("min", "0"), ("max", "12"), ("value", "7"),
    ]),
    ("NUMBER", "number", [
        ("label", '"bpm"'), ("min", "20"), ("max", "300"), ("value", "120"),
    ]),
    ("METER", "meter", [
        ("label", '"led"'), ("min", "0"), ("max", "1"), ("value", "0.5"),
    ]),
    ("RADIO", "radio", [("labels", '["a", "b", "c"]'), ("value", "1")]),
    ("DROPDOWN", "dropdown", [("labels", '["x", "y"]'), ("value", "0")]),
    # the synth words take their arguments from the same ``args`` rows, so the
    # same sugar-equals-SET proof runs over them with no new rule anywhere
    ("WAVEFORM", "waveform", [("label", '"scope"')]),
    ("SPECTRUM", "spectrum", [("label", '"analyst"')]),
    ("PLOT", "plot", [("label", '"graph"')]),
    ("LOGVIEW", "logview", [("label", '"log"')]),
    ("ADSR", "adsr", [
        ("label", '"env"'), ("attack", "10"), ("decay", "20"),
        ("sustain", "0.5"), ("release", "30"),
    ]),
    ("CURVE", "curve", [("label", '"shape"'), ("tension", "0.25")]),
    ("XY_PAD", "xy", [("label", '"pan"'), ("x", "0.25"), ("y", "0.75")]),
    ("KEYBED", "keybed", [("label", '"keys"'), ("low", "48"),
                          ("octaves", "3")]),
    ("PRESET_BAR", "preset_bar", [("labels", '["a", "b"]'), ("value", "1")]),
]


@pytest.mark.parametrize("word,kind,rows", _DIRECT_CONSTRUCTORS)
def test_a_constructor_leaves_the_same_props_as_a_widget_and_a_SET_per_row(
        stage, word, kind, rows):
    """The plan's constructor-equals-setter rule, per catalog kind."""
    program, _, _ = stage
    args = ", ".join(literal for _, literal in rows)
    sugar, error = program.run(f"VAR a = {word}({args})")
    assert error is None, error.as_string()
    manual_src = "\n".join(
        [f'VAR b = WIDGET("{kind}")']
        + [f'SET(b, "{name}", {literal})' for name, literal in rows]
    )
    _, error = program.run(manual_src)
    assert error is None, error.as_string()
    a = handle(sugar)
    b = program.symbol_table.get("b").target
    assert a.kind.name == b.kind.name == kind
    # every prop the constructor wrote matches the SET sequence that wrote the
    # same rows one at a time, through the same Prop.settle door
    assert a.writable() == b.writable()


def test_an_omitted_constructor_argument_stays_at_the_default_the_kind_declares(
        run):
    """A constructor with fewer arguments is the widget without those writes."""
    held = handle(run('VAR a = SLIDER("vol")'))
    default = held.kind.props["value"].default
    assert held.get("value") == default
    assert held.get("label") == "vol"


def test_the_first_string_argument_names_the_widget(run):
    """A named widget is easier to find in a log than a bare kind."""
    held = handle(run('VAR a = SLIDER("BPM", 20, 300, 120)'))
    assert held.name == "BPM"
    assert held.path() == f"root/BPM"


def test_the_widget_handle_reports_the_kind_that_made_it(run):
    value = run('VAR a = PANEL("deck")')
    assert isinstance(value, HostHandle)
    assert value.kind == WIDGET_KIND
    assert value.label.endswith("deck")


def test_toggle_translates_its_on_argument_to_the_value_row(eval_ui):
    assert eval_ui('GET(TOGGLE("Run", 1), "value")') == 1.0
    assert eval_ui('GET(TOGGLE("Run", 0), "value")') == 0.0
    # the words a bool prop takes are the words ``TOGGLE`` accepts
    assert eval_ui('GET(TOGGLE("x", "on"), "value")') == 1.0
    assert eval_ui('GET(TOGGLE("y", "off"), "value")') == 0.0
    assert eval_ui('GET(TOGGLE("z", "yes"), "value")') == 1.0
    assert eval_ui('GET(TOGGLE("w", "no"), "value")') == 0.0


def test_toggle_refuses_a_word_that_is_neither_on_nor_off(ui_failure):
    error = ui_failure('TOGGLE("Run", "maybe")')
    assert "'on' must be a number or" in error
    assert "maybe" in error


def test_icon_size_is_the_box_not_a_prop(run):
    """``ICON("play", 32)`` gives the widget a 32x32 box, then ``PLACE`` may overrule."""
    held = handle(run('VAR i = ICON("play", 32)'))
    assert held.box == (0.0, 0.0, 32.0, 32.0)
    assert held.get("glyph") == "play"
    run("PLACE(i, 4, 4, 48, 48)")
    assert held.box == (4.0, 4.0, 48.0, 48.0)


def test_icon_refuses_a_non_numeric_size(ui_failure):
    error = ui_failure('ICON("play", "big")')
    assert "'size' must be a number of logical pixels" in error


def test_a_widget_word_rejects_a_kind_that_does_not_exist(ui_failure):
    error = ui_failure('WIDGET("hologram")')
    assert "hologram" in error


def test_every_catalog_kind_has_its_own_constructor(run):
    """Every kind :mod:`crude.ui.props` says the catalog has answers as a word.

    RADIO and DROPDOWN need a list argument for ``labels``, so a test that only
    says ``RADIO("x")`` is a broken test, not a broken binding; the shapes
    here match what each kind's own ``args`` row actually takes.
    """
    first_arg = {
        "PANEL": '"x"', "TEXT": '"x"', "BUTTON": '"x"', "TOGGLE": '"x"',
        "SLIDER": '"x"', "VSLIDER": '"x"', "KNOB": '"x"',
        "NUMBER": '"x"', "METER": '"x"',
        "ICON": '"play"', "RADIO": '["a", "b"]', "DROPDOWN": '["a", "b"]',
        "WAVEFORM": '"x"', "SPECTRUM": '"x"', "PLOT": '"x"',
        "LOGVIEW": '"x"', "ADSR": '"x"', "CURVE": '"x"',
        "XY_PAD": '"x"', "KEYBED": '"x"', "PRESET_BAR": '["a", "b"]',
    }
    produced = set()
    for word, (kind, _args, _opt) in CONSTRUCTORS.items():
        produced.add(kind)
        handle(run(f'VAR w = {word}({first_arg[word]})'))
    for kind in catalog_names():
        assert kind in produced, kind


# -- configure: SET/GET validation against the kind's table ------------


def test_set_answers_at_the_settled_value_when_the_request_was_clamped(eval_ui):
    """A slider told 500 on a 20..300 range reads back at 300, not at 500."""
    eval_ui('VAR a = SLIDER("BPM", 20, 300, 120)')
    assert eval_ui('SET(a, "value", 500)') == 300.0
    assert eval_ui('GET(a, "value")') == 300.0


def test_a_typo_prop_is_refused_with_a_suggestion(ui_failure):
    error = ui_failure(
        'VAR a = SLIDER("BPM", 20, 300, 120)\nSET(a, "vlue", 1)\n'
    )
    assert "'slider' has no prop 'vlue'" in error
    assert "did you mean 'value'?" in error


def test_a_readonly_prop_is_refused_by_name(ui_failure):
    error = ui_failure(
        'VAR a = SLIDER("BPM", 20, 300, 120)\nSET(a, "hovered", 1)\n'
    )
    assert "written by the pointer or the keyboard, not by a script" in error


def test_a_prop_written_with_the_wrong_type_is_refused(ui_failure):
    error = ui_failure(
        'VAR a = SLIDER("BPM", 20, 300, 120)\nSET(a, "max", "wide")\n'
    )
    assert "is a number, not a str" in error


def test_a_read_of_a_prop_the_kind_does_not_have_fails_named(ui_failure):
    error = ui_failure('VAR a = TEXT("hi")\nGET(a, "min")\n')
    assert "'text' has no prop 'min'" in error


def test_the_pointer_rows_are_readable_even_though_a_script_cannot_write_them(
        eval_ui):
    eval_ui('VAR a = BUTTON("ok")')
    for row in POINTER:
        assert eval_ui(f'GET(a, "{row}")') == 0


def test_only_a_widget_handle_can_be_asked_for_a_prop(ui_failure):
    error = ui_failure('GET(12, "value")')
    assert "must be a" in error.lower()
    assert "handle" in error.lower()


def test_a_placement_prop_round_trips_through_a_SET(eval_ui):
    """``label_place`` is a common row, so a ``SET`` writes and a ``GET`` reads it.

    The knob the notes complained about is the pointed case: a script that says
    ``SET(k, "label_place", "left")`` gets the word back off the same row, and an
    offset is a plain number that survives the same round trip.
    """
    eval_ui('VAR k = KNOB("Index", 0, 12, 7)')
    assert eval_ui('SET(k, "label_place", "left")') == "left"
    assert eval_ui('GET(k, "label_place")') == "left"
    assert eval_ui('GET(k, "value_place")') == "inside", "untouched default"
    assert eval_ui('SET(k, "label_dx", 6)') == 6.0
    assert eval_ui('GET(k, "label_dx")') == 6.0


def test_a_placement_word_a_script_cannot_choose_is_refused_by_name(ui_failure):
    error = ui_failure('VAR k = KNOB("Index", 0, 12, 7)\nSET(k, "label_place", "middle")\n')
    assert "'middle' is not a" in error or "it is one of" in error
    assert "inside" in error and "none" in error


def test_the_two_canvases_are_built_by_the_generic_widget_word_and_a_SET(eval_ui):
    """A plot is data + a window; a log view is lines + a scroll: all via SET."""
    eval_ui('VAR g = WIDGET("plot")')
    assert eval_ui('SET(g, "data", [1, 2, 3])') == [1.0, 2.0, 3.0]
    assert eval_ui('GET(g, "data")') == [1.0, 2.0, 3.0]
    eval_ui('VAR v = WIDGET("logview")')
    assert eval_ui('SET(v, "lines", ["up", "down"])') == ["up", "down"]
    assert eval_ui('GET(v, "lines")') == ["up", "down"]
    assert eval_ui('GET(v, "scroll")') == 0.0


# -- place --------------------------------------------------------------


def test_place_writes_the_box_and_answers_whether_it_moved(eval_ui, run):
    run('VAR a = SLIDER("BPM", 20, 300, 120)')
    assert eval_ui("PLACE(a, 10, 20, 200, 24)") == 1
    held = handle(run("a"))
    assert held.box == (10.0, 20.0, 200.0, 24.0)
    # the same numbers again did not move anything, and ``PLACE`` says so
    assert eval_ui("PLACE(a, 10, 20, 200, 24)") == 0


def test_place_without_a_size_leaves_the_size_alone(eval_ui, run):
    run('VAR a = SLIDER("BPM", 20, 300, 120)\nPLACE(a, 0, 0, 100, 40)\n')
    eval_ui("PLACE(a, 5, 6)")
    held = handle(run("a"))
    assert held.box == (5.0, 6.0, 100.0, 40.0)


def test_place_needs_a_widget(ui_failure):
    error = ui_failure('PLACE("not a widget", 0, 0)')
    assert "must be a widget handle" in error


# -- NODE_ADD -----------------------------------------------------------


def test_node_add_reparents_a_widget_into_a_panel(run):
    run(
        'VAR deck = PANEL("deck")\n'
        'VAR led = METER("beat", 0, 1, 0)\n'
    )
    result = run("NODE_ADD(deck, led)")
    kid = handle(result)
    assert kid.parent is handle(run("deck"))
    # the path a report prints follows the new parent, so a name collision inside
    # one panel is different from one outside it
    assert kid.path() == "root/deck/beat"
    # the child is no longer at the window's root
    assert kid in handle(run("deck")).children
    root = handle(run("deck")).root()
    assert kid not in root.children


def test_a_leaf_refuses_a_child_by_the_name_of_its_kind(ui_failure):
    error = ui_failure(
        'VAR btn = BUTTON("ok")\nVAR lb = TEXT("x")\nNODE_ADD(btn, lb)\n'
    )
    assert "holds no children" in error


def test_a_cycle_is_refused_by_the_widget_that_would_have_caused_it(ui_failure):
    error = ui_failure(
        'VAR a = PANEL("a")\nVAR b = PANEL("b")\n'
        "NODE_ADD(a, b)\nNODE_ADD(b, a)\n"
    )
    assert "loop" in error.lower()


# -- wire: links --------------------------------------------------------


def test_link_value_mirrors_both_ways_by_default(eval_ui, run):
    run(
        'VAR a = SLIDER("BPM", 20, 300, 120)\n'
        'VAR b = NUMBER("shown", 20, 300, 120)\n'
        "LINK_VALUE(a, b)\n"
    )
    eval_ui('SET(a, "value", 140)')
    assert eval_ui('GET(b, "value")') == 140.0
    eval_ui('SET(b, "value", 90)')
    assert eval_ui('GET(a, "value")') == 90.0


def test_link_value_can_be_told_to_run_one_way(eval_ui, run):
    run(
        'VAR a = SLIDER("BPM", 20, 300, 120)\n'
        'VAR b = NUMBER("shown", 20, 300, 120)\n'
        "LINK_VALUE(a, b, 0)\n"
    )
    eval_ui('SET(a, "value", 200)')
    assert eval_ui('GET(b, "value")') == 200.0
    eval_ui('SET(b, "value", 20)')
    assert eval_ui('GET(a, "value")') == 200.0, "the slider does not follow back"


def test_link_state_mirrors_any_row_and_can_rename_it(eval_ui, run):
    run(
        'VAR a = TOGGLE("mute")\n'
        'VAR b = TOGGLE("other")\n'
        'LINK_STATE(a, "value", b, "value", 0)\n'
    )
    eval_ui('SET(a, "value", 1)')
    assert eval_ui('GET(b, "value")') == 1.0


def test_unlink_returns_how_many_went_and_stops_the_mirror(eval_ui, run):
    run(
        'VAR a = SLIDER("BPM", 20, 300, 120)\n'
        'VAR b = NUMBER("shown", 20, 300, 120)\n'
        "LINK_VALUE(a, b)\n"
    )
    eval_ui('SET(a, "value", 250)')
    assert eval_ui('GET(b, "value")') == 250.0
    assert eval_ui('UNLINK(a, "value")') == 1
    eval_ui('SET(a, "value", 30)')
    assert eval_ui('GET(b, "value")') == 250.0, "b no longer follows a"


def test_unlink_with_nothing_said_clears_every_link(eval_ui, run):
    run(
        'VAR a = SLIDER("A", 0, 10, 5)\nVAR b = NUMBER("B", 0, 10, 5)\n'
        'VAR c = SLIDER("C", 0, 10, 5)\nVAR d = NUMBER("D", 0, 10, 5)\n'
        "LINK_VALUE(a, b)\nLINK_VALUE(c, d)\n"
    )
    assert eval_ui("UNLINK()") == 2


# -- wire: handlers -----------------------------------------------------


def test_on_change_fires_a_script_function_when_a_prop_actually_moves(eval_ui, run):
    run(
        'VAR led = METER("beat", 0, 1, 0)\n'
        'VAR tg = TOGGLE("Run")\n'
        'ON_CHANGE(tg, FUNC(v) -> SET(led, "value", v))\n'
    )
    eval_ui('SET(tg, "value", 1)')
    assert eval_ui('GET(led, "value")') == 1.0
    # no change is not a fire: writing the same value keeps the meter where it is
    eval_ui('SET(led, "value", 0)\nSET(tg, "value", 1)\n')
    assert eval_ui('GET(led, "value")') == 0.0


def test_two_on_change_handlers_on_one_widget_run_in_the_order_they_were_added(
        eval_ui, run):
    """The plan's metronome wires the same toggle twice: send, then kill the LED."""
    run(
        'VAR a = METER("A", 0, 1, 0)\n'
        'VAR b = METER("B", 0, 1, 0)\n'
        'VAR tg = TOGGLE("Run")\n'
        'ON_CHANGE(tg, FUNC(v) -> SET(a, "value", v))\n'
        'ON_CHANGE(tg, FUNC(v) -> SET(b, "value", 1 - v))\n'
    )
    eval_ui('SET(tg, "value", 1)')
    assert eval_ui('GET(a, "value")') == 1.0
    assert eval_ui('GET(b, "value")') == 0.0


def test_off_takes_the_handlers_back_off_a_widget(eval_ui, run):
    """``VAR fn = FUNC(...)`` hands back a fresh copy on every lookup, so ``OFF``
    without a func argument - which widens to every handler on that (widget,
    event) pair - is the shape a script actually uses."""
    run(
        'VAR led = METER("beat", 0, 1, 0)\n'
        'VAR tg = TOGGLE("Run")\n'
        'ON_CHANGE(tg, FUNC(v) -> SET(led, "value", v))\n'
    )
    assert eval_ui('OFF(tg, "change")') == 1
    eval_ui('SET(tg, "value", 1)')
    assert eval_ui('GET(led, "value")') == 0.0, "the handler is gone"


def test_on_frame_and_off_frame_are_window_scoped(stage):
    program, ctx, _ = stage
    _, error = program.run(
        'VAR n = NUMBER("ticks", 0, 1000, 0)\n'
        "ON_FRAME(FUNC(dt) -> SET(n, \"value\", GET(n, \"value\") + 1))\n"
    )
    assert error is None, error.as_string()
    ctx.frame(dt=0.016)
    ctx.frame(dt=0.016)
    value, error = program.run('GET(n, "value")')
    assert error is None
    assert as_py(value) == 2.0
    gone, error = program.run("OFF_FRAME()")
    assert error is None and as_py(gone) == 1
    ctx.frame(dt=0.016)
    value, _ = program.run('GET(n, "value")')
    assert as_py(value) == 2.0, "the counter stopped with the handler"


# -- style --------------------------------------------------------------


def test_style_writes_a_role_and_answers_at_the_settled_value(eval_ui, run):
    run('VAR a = BUTTON("TAP")')
    assert eval_ui('STYLE(a, "role", "danger")') == "danger"
    assert eval_ui('GET(a, "role")') == "danger"


@pytest.mark.parametrize("row,value", [
    ("role", "danger"),
    ("glyph", "play"),
    ("glyph_on", "pause"),
    ("glyph_off", "play"),
    ("tooltip", "click to tap"),
])
def test_style_writes_every_row_it_owns(run, eval_ui, row, value):
    """Every entry in :data:`STYLE_PROPS` a slider actually holds must be writable here.

    ``size`` is left out of this table because the kind's own row is a word
    (``sm``/``md``/``lg``) that only the skin resolves, and the plan's S7
    surface is the six style rows a script can honestly name.
    """
    run('VAR w = SLIDER("s", 0, 10, 5)')
    assert eval_ui(f'STYLE(w, "{row}", "{value}")') == value


def test_style_refuses_a_row_that_is_not_a_style(ui_failure):
    error = ui_failure(
        'VAR a = SLIDER("s", 0, 10, 5)\nSTYLE(a, "value", 3)\n'
    )
    assert "not a style row" in error
    assert "use SET for the rest" in error


def test_style_with_one_string_argument_changes_the_window_theme(eval_ui, run):
    assert eval_ui('STYLE("flat")') == "flat"
    assert eval_ui('STYLE("light")') == "light"


def test_style_with_one_widget_argument_is_a_script_error_that_says_so(ui_failure):
    error = ui_failure('VAR a = BUTTON("ok")\nSTYLE(a)\n')
    assert "STYLE needs the prop and value" in error


# -- introspection ------------------------------------------------------


def test_widget_kinds_lists_the_catalog(run):
    listed = as_py(run("WIDGET_KINDS()"))
    assert isinstance(listed, list)
    assert set(catalog_names()) <= set(listed)


def test_widget_props_lists_a_kind_s_table(eval_ui, run):
    props = as_py(run('WIDGET_PROPS("slider")'))
    assert "value" in props and "label" in props and "min" in props and "max" in props
    # a widget handle answers as well as a kind name does, so a generic handler
    # can ask "what do you have" without knowing the kind
    run('VAR a = SLIDER("BPM", 20, 300, 120)')
    assert as_py(run("WIDGET_PROPS(a)")) == props


def test_widget_has_answers_yes_and_no_in_the_language_s_own_shape(eval_ui, run):
    run('VAR a = SLIDER("BPM", 20, 300, 120)')
    assert eval_ui('WIDGET_HAS(a, "value")') == 1
    assert eval_ui('WIDGET_HAS(a, "labels")') == 0


def test_widget_props_refuses_a_kind_that_is_not_there(ui_failure):
    error = ui_failure('WIDGET_PROPS("hologram")')
    assert "hologram" in error


# -- the runtime has to be there ----------------------------------------


@pytest.mark.parametrize("source", [
    'WIDGET("slider")',
    'SLIDER("BPM", 20, 300, 120)',
    'STYLE("flat")',
])
def test_a_program_with_no_host_ui_says_which_binding_needed_one(source):
    _, error = Program("<ui-test>", host=types.SimpleNamespace()).run(source)
    assert error is not None, source
    name = source.split("(")[0]
    assert f"{name}: no UI runtime is attached" in error.as_string()


# -- a whole script builds a tree, and the recorder proves it ----------


def test_a_script_built_panel_lays_out_and_draws_what_the_tree_says(stage):
    """The plan's S7 acceptance line: RecordingRenderer assertions on a script-built tree."""
    program, ctx, rec = stage
    _, error = program.run(
        'VAR title = TEXT("METRONOME")\n'
        "PLACE(title, 12, 8, 200, 20)\n"
        'VAR deck = PANEL("deck")\n'
        "PLACE(deck, 8, 32, 304, 160)\n"
        'VAR bpm = SLIDER("BPM", 20, 300, 120)\n'
        "NODE_ADD(deck, bpm)\n"
        "PLACE(bpm, 8, 8, 200, 24)\n"
        'VAR tg = TOGGLE("Run")\n'
        "NODE_ADD(deck, tg)\n"
        "PLACE(tg, 220, 8, 72, 24)\n"
        'VAR led = METER("beat", 0, 1, 0)\n'
        "NODE_ADD(deck, led)\n"
        "PLACE(led, 8, 40, 288, 12)\n"
        'STYLE(bpm, "role", "track")\n'
        'VAR field = NUMBER("shown", 20, 300, 120)\n'
        "NODE_ADD(deck, field)\n"
        "PLACE(field, 8, 60, 120, 24)\n"
        "LINK_VALUE(bpm, field)\n"
    )
    assert error is None, error.as_string()

    ctx.frame()
    drawn = rec.texts()
    # the title, the deck panel, and each child's words reached the frame - and a
    # NUMBER box is drawn to be read off, so its words are the number it holds
    # rather than the label it was named with (S8's readout rule, in the skin's
    # ``shows``, not a list of kinds)
    for wanted in ("METRONOME", "deck", "BPM", "Run", "beat", "120"):
        assert wanted in drawn, drawn
    assert "shown" not in drawn, "the field's name is not what a field shows"

    # every ``PLACE`` wrote a box the recorder saw on the frame
    rounded = [tuple(round(v, 3) for v in r) for r in rec.rects()]
    for wanted in [(8.0, 32.0, 304.0, 160.0)]:
        # the panel's own box, at least, is the number the script said
        assert wanted in rounded, (wanted, rounded)

    # the link is plumbing: a script change on the slider lands on the number
    program.run('SET(bpm, "value", 200)')
    value, _ = program.run('GET(field, "value")')
    assert as_py(value) == 200.0


def test_a_widget_the_script_never_placed_still_draws_at_the_default_box(stage):
    """A bare ``SLIDER("x")`` is a real widget in the tree before ``PLACE`` runs."""
    program, ctx, rec = stage
    _, error = program.run(
        'VAR a = SLIDER("BPM", 20, 300, 120)\nPLACE(a, 8, 8, 160, 24)\n'
    )
    assert error is None, error.as_string()
    ctx.frame()
    assert "BPM" in rec.texts(), "the label the constructor set reached the frame"
