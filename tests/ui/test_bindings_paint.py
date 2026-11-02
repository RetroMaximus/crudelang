"""A kind, a face and a hit region, written in ``.crude``.

This is :mod:`crude.ui.paint` and :mod:`crude.ui.handlers` seen from the keyboard:
every test here runs a script through :class:`~crude.lang.Program`, with a
:class:`~crude.ui.recording.RecordingRenderer` under the window, and asserts on the
ops that reach it. That split is the point of the two files -
:mod:`tests.ui.test_paint` proves the canvas and the cache are right in plain
Python, and this one proves the words a project actually types are:
``WIDGET_DEFINE``, ``PROP_DEFINE``, ``ON_PAINT``, the ``P_*`` primitives,
``PAINT_REQUEST``, ``ON_HIT`` and ``ON_CLICK``.

Nothing here needs a display. The one thing a script cannot fake is a GPU's clock,
so the frame budget is asserted on the *op counts* a painter produced rather than on
milliseconds.

Script kinds live in a process-global table - a project's ``WIDGET_DEFINE`` is
visible to every window in the interpreter, not just the one that said it - so the
autouse fixture below hands that table back between tests, the same way
:mod:`tests.ui.test_props` does.
"""

from __future__ import annotations

import types

import pytest

import crude.bindings  # noqa: F401 - registers every area, including ui
from crude.lang import Program
from crude.lang.values import HostHandle, List, Number, String
from crude.ui import (
    MAX_HANDLER_ERRORS,
    RecordingRenderer,
    UIContext,
    get_kind,
    reset,
    texture_key,
)
from crude.ui.props import registered_names


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
    """A ``widget`` handle as the :class:`~crude.ui.widget.Widget` under it."""
    assert isinstance(value, HostHandle), f"expected a handle, got {value!r}"
    return value.target


@pytest.fixture(autouse=True)
def clean_kinds():
    """No script kind survives a test, because the table they live in is global."""
    reset()
    yield
    reset()


@pytest.fixture
def stage():
    """A program wired to a fresh UIContext over a RecordingRenderer, at 1x."""
    renderer = RecordingRenderer()
    ctx = UIContext(renderer, width=320, height=200)
    program = Program("<paint-test>", host=types.SimpleNamespace(ui=ctx))
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


#: the box every painter below draws in: a script kind of its own, placed by hand
STEPPER = (
    'WIDGET_DEFINE("stepper")\n'
    'PROP_DEFINE("stepper", "value", "number", 0, 0, 100)\n'
    'VAR w = WIDGET("stepper")\n'
    "PLACE(w, 10, 10, 80, 40)\n"
)


# -- WIDGET_DEFINE: a kind the catalog does not have ---------------------


def test_a_script_kind_is_a_kind_the_language_can_build(run, eval_ui):
    """``WIDGET_DEFINE`` then ``WIDGET`` - the two halves of one sentence."""
    assert eval_ui('WIDGET_DEFINE("stepper")') == "stepper"
    held = handle(run("VAR w = WIDGET(\"stepper\")"))
    assert held.kind.name == "stepper"
    # the new word is in the list a report prints, after the catalog's own
    listed = eval_ui("WIDGET_KINDS()")
    assert "stepper" in listed and listed.index("stepper") > listed.index("slider")
    assert "stepper" in registered_names()
    # and it starts life with the rows every widget has, and nothing else
    props = eval_ui('WIDGET_PROPS("stepper")')
    assert {"label", "role", "visible"} <= set(props)
    assert "value" not in props, "a bare kind holds no value until told to"


def test_a_kind_defined_on_a_base_is_that_base_with_its_defaults_restated(
        run, eval_ui):
    """A variant *is* the base: its rows, its clamping, its pointer behaviour."""
    run(
        'WIDGET_DEFINE("bpm", "slider")\n'
        'VAR v = WIDGET("bpm")\n'
        'SET(v, "max", 300)\n'
        'SET(v, "min", 20)\n'
    )
    held = handle(run("v"))
    assert held.kind.base == "slider"
    # the base's rows came along, so a write clamps exactly the way a slider does
    assert eval_ui('SET(v, "value", 500)') == 300.0
    for row in ("value", "min", "max", "label"):
        assert eval_ui(f'WIDGET_HAS(v, "{row}")') == 1
    # and the catalog word still builds the catalog kind, untouched
    assert handle(run('VAR s = SLIDER("plain")')).kind.name == "slider"


def test_a_variant_keeps_the_pd_twin_of_the_kind_it_was_built_on(eval_ui, run):
    """The plan's promise: a project's variant still talks to the same Pd box."""
    run(
        'VAR s = SLIDER("plain")\n'
        'WIDGET_DEFINE("bpm", "slider")\n'
        'VAR v = WIDGET("bpm")\n'
    )
    assert eval_ui('GET(v, "pd_object")') == eval_ui('GET(s, "pd_object")')


def test_a_pd_object_that_is_not_a_pd_box_is_refused_with_the_list(ui_failure):
    error = ui_failure('WIDGET_DEFINE("dial", "", "cnv~dial")')
    assert "cannot default to 'cnv~dial'" in error
    assert "'hslider'" in error, "the refusal should say what is allowed"


def test_a_kind_name_the_catalog_already_uses_is_refused(ui_failure):
    """A project defines a kind *beside* ``slider``, never over it."""
    error = ui_failure('WIDGET_DEFINE("slider")')
    assert "built-in widget kind" in error
    assert "beside it, not over it" in error


def test_a_kind_the_catalog_already_has_is_refused_a_second_time(run, ui_failure):
    run('WIDGET_DEFINE("stepper")')
    error = ui_failure('WIDGET_DEFINE("stepper")')
    assert "'stepper' is already defined by this program" in error


def test_an_unknown_kind_says_which_ones_there_are(run, ui_failure):
    """The refusal a typo gets names the catalog *and* this program's own kinds."""
    run('WIDGET_DEFINE("stepper")')
    error = ui_failure('WIDGET("hologram")')
    assert "hologram" in error
    assert "stepper" in error and "slider" in error


def test_a_variant_of_a_container_holds_children_like_the_container_does(run):
    """``WIDGET_DEFINE("deck", "panel")`` is a panel, not a picture of one."""
    run(
        'WIDGET_DEFINE("deck", "panel")\n'
        'VAR d = WIDGET("deck")\n'
        'VAR s = SLIDER("BPM", 20, 300, 120)\n'
    )
    held = handle(run("NODE_ADD(d, s)"))
    assert held.parent is handle(run("d"))
    assert held.path() == "root/deck/BPM"
    assert held.kind.name == "slider", "the child is unchanged by its parent's kind"


# -- PROP_DEFINE: the rows of a kind a script owns ----------------------


def test_a_new_row_is_a_prop_the_widget_holds_and_settles(eval_ui, run):
    """``PROP_DEFINE`` gives a kind a ``value``; the clamping comes free."""
    eval_ui(STEPPER + 'SET(w, "value", 42)')
    assert eval_ui('GET(w, "value")') == 42.0
    # a row nobody bound by hand takes the kind's own min/max, which is what makes
    # a script-drawn stepper clamp to the bounds the script declared above it
    assert eval_ui('SET(w, "value", 500)') == 100.0
    assert eval_ui('SET(w, "value", -5)') == 0.0
    assert eval_ui('WIDGET_HAS(w, "value")') == 1
    assert "value" in eval_ui('WIDGET_PROPS("stepper")')


def test_a_row_is_refused_on_a_kind_the_program_did_not_write(ui_failure):
    error = ui_failure('PROP_DEFINE("slider", "ticks", "number", 1)')
    assert "built-in kind" in error
    assert "a kind it defined itself" in error


def test_a_row_is_declared_once_per_kind(run, ui_failure):
    run(STEPPER)
    error = ui_failure('PROP_DEFINE("stepper", "value", "number", 9)')
    assert "already defined" in error
    assert "declared once per kind" in error


def test_a_type_that_is_not_a_prop_type_is_refused_with_the_list(run, ui_failure):
    run('WIDGET_DEFINE("stepper")')
    error = ui_failure('PROP_DEFINE("stepper", "mode", "colour", 1)')
    assert "'colour' is not a prop type" in error
    assert "bool, list, number, string" in error


def test_a_string_row_holds_the_words_a_script_gives_it(eval_ui, run):
    eval_ui('WIDGET_DEFINE("stepper")')
    eval_ui('PROP_DEFINE("stepper", "side", "string", "up")')
    eval_ui('VAR w = WIDGET("stepper")')
    assert eval_ui('GET(w, "side")') == "up"
    assert eval_ui('SET(w, "side", "down")') == "down"


def test_restating_an_inherited_row_keeps_everything_this_call_left_out(
        eval_ui, run):
    """A variant changes one number, not the hint, the Pd name or the bounds.

    This is the plan's three lines that give ``bpm`` a ``min``: a variant that had
    to restate the whole row to move a default would be a variant that cannot be
    written.
    """
    run(
        'WIDGET_DEFINE("bpm", "slider")\n'
        'PROP_DEFINE("bpm", "min", "number", 40)\n'
        'VAR v = WIDGET("bpm")\n'
        'VAR s = SLIDER("plain", 20, 300, 120)\n'
    )
    assert eval_ui('GET(v, "min")') == 40.0
    held = handle(run("v"))
    original = get_kind("slider").props["min"]
    row = held.kind.props["min"]
    assert row.hint == original.hint
    assert row.pd_name == original.pd_name
    assert row.type == original.type
    # the bounds came along too, so a value is still clamped between the two ends
    assert eval_ui('SET(v, "max", 200)') == 200.0
    assert eval_ui('SET(v, "value", 500)') == 200.0


def test_a_row_added_to_a_kind_reaches_the_kinds_below_it(eval_ui, run):
    """A prop declared after a variant was built is not a row the variant misses."""
    run(
        'WIDGET_DEFINE("dial")\n'
        'WIDGET_DEFINE("big", "dial")\n'
        'VAR b = WIDGET("big")\n'
        'PROP_DEFINE("dial", "angle", "number", 0)\n'
    )
    assert eval_ui('WIDGET_HAS(b, "angle")') == 1
    # a row that arrived after the widget was built starts at its own default, so
    # the first thing a script reads on it is the number PROP_DEFINE declared
    assert eval_ui('GET(b, "angle")') == 0.0
    assert eval_ui('SET(b, "angle", 30)') == 30.0


# -- ON_PAINT: the face a script draws for itself ------------------------


def test_a_script_painter_draws_the_face_the_recorder_sees(stage):
    """The plan's S7i acceptance line: a kind defined and drawn from a script."""
    program, ctx, rec = stage
    _, error = program.run(
        STEPPER +
        'ON_PAINT(w, FUNC(p) -> P_RECT(p, 0, 0, WIDTH(p), HEIGHT(p), 4, "accent"))\n'
    )
    assert error is None, error.as_string()
    held = program.symbol_table.get("w").target
    ctx.frame()
    plate = rec.last("rect")
    left, top, width, height = held.screen_box
    # local (0, 0) is the widget's own corner, and the box is the whole of it
    assert (plate.get("x"), plate.get("y")) == (left, top)
    assert (plate.get("width"), plate.get("height")) == (width, height)
    assert plate.get("radius") == 4.0
    assert plate.get("color") == ctx.skin.resolve("accent", "normal").fill
    # everything a painter draws is clipped to the widget it paints
    assert plate.clip == held.clip
    assert rec.names() == ["clear", "rect", "rect", "rect"], (
        "the root's own plate, then the one the script drew")


def test_a_painter_may_read_the_widget_it_was_handed(stage):
    """``FUNC(p, k)`` and a ``GET`` on ``k`` - the only way a face shows a value.

    The widget arrives as a *handler* argument, which the language spells as a
    plain handle rather than as a ``widget`` handle, and ``GET`` has to answer for
    it all the same: a painter that could not read its own props could only ever
    draw a still life.
    """
    program, ctx, rec = stage
    _, error = program.run(
        STEPPER +
        'ON_PAINT(w, FUNC(p, k) -> '
        'P_TEXT(p, 4, 4, STR(GET(k, "value")), 12, "text"))\n'
        'SET(w, "value", 42)\n'
    )
    assert error is None, error.as_string()
    assert ctx.handlers.errors == []
    ctx.frame()
    assert rec.texts() == ["42.0"]
    held = program.symbol_table.get("w").target
    label = rec.last("text")
    assert (label.get("x"), label.get("y")) == (held.screen_box[0] + 4,
                                               held.screen_box[1] + 4)
    assert label.get("size") == 12.0


def test_a_painter_that_names_no_role_is_drawn_in_the_widgets_own(stage):
    """``STYLE(w, "role", "danger")`` reaches a painter that said nothing."""
    program, ctx, rec = stage
    program.run(STEPPER + 'ON_PAINT(w, FUNC(p) -> P_RECT(p, 0, 0, 8, 8))')
    ctx.frame()
    assert rec.last("rect").get("color") == ctx.skin.resolve("surface",
                                                            "normal").fill
    program.run('STYLE(w, "role", "danger")')
    rec.reset()
    ctx.frame()
    assert rec.last("rect").get("color") == ctx.skin.resolve("danger",
                                                           "normal").fill


def test_the_pointer_changes_the_colour_without_the_painter_saying_so(stage):
    """No ``if hovered`` in a face: the same role word answers a different colour."""
    program, ctx, rec = stage
    program.run(
        STEPPER + 'ON_PAINT(w, FUNC(p) -> P_RECT(p, 0, 0, 80, 40, 4, "accent"))'
    )
    ctx.frame()
    plain = rec.last("rect").get("color")
    ctx.router.move_to(30, 30)
    rec.reset()
    ctx.frame()
    lit = rec.last("rect").get("color")
    assert plain != lit
    held = program.symbol_table.get("w").target
    assert lit == ctx.skin.resolve("accent", "hover").fill


def test_a_script_face_replaces_the_whole_face_of_a_catalog_widget(stage):
    """Plate, border and label included - which is why there is one branch, not two."""
    program, ctx, rec = stage
    program.run('VAR s = SLIDER("BPM", 20, 300, 120)\nPLACE(s, 10, 10, 160, 24)')
    ctx.frame()
    assert rec.texts() == ["BPM"], "the catalog face, label and all"
    program.run('ON_PAINT(s, FUNC(p) -> P_RECT(p, 0, 10, WIDTH(p), 4, 2, "track"))')
    rec.reset()
    ctx.frame()
    assert rec.texts() == [], "the script's face is the widget's whole face now"
    assert rec.count("rect") == 3, "the window's own plate, then the one painted"


def test_two_painters_on_one_widget_become_one_face(stage):
    """Several ``ON_PAINT`` handlers are layers of the same picture, not two faces."""
    program, ctx, rec = stage
    program.run(
        STEPPER +
        'ON_PAINT(w, FUNC(p) -> P_RECT(p, 0, 0, 10, 10, 0, "accent"))\n'
        'ON_PAINT(w, FUNC(p) -> P_CIRCLE(p, 40, 20, 5, "ok"))\n'
    )
    ctx.frame()
    assert rec.names()[-2:] == ["rect", "circle"]
    said = ctx.paints.stats()
    assert said["records"] == 1 and said["cached"] == 1, "one face, one entry"
    assert said["ops"] == 2


def test_every_primitive_reaches_the_recorder_as_the_op_it_names(stage):
    """One call, one op, in the order the painter said them."""
    program, ctx, rec = stage
    _, error = program.run(
        STEPPER +
        'FUNC face(p)\n'
        '    P_POLY(p, [0, 0, 10, 0, 10, 10], 1, "accent")\n'
        '    P_POLY(p, [0, 0, 10, 0, 10, 10], 0, "accent")\n'
        '    P_ARC(p, 40, 20, 8, 0, 3, 2, "danger")\n'
        '    P_LINE(p, 0, 0, 80, 40, 2, "border")\n'
        '    P_IMAGE(p, 0, 0, 20, 20, "")\n'
        '    P_IMAGE(p, 0, 0, 20, 20, "assets/cover.png")\n'
        '    P_GLYPH(p, 4, 4, 16, 16, "play", "text")\n'
        'END\n'
        'ON_PAINT(w, face)\n'
    )
    assert error is None, error.as_string()
    ctx.frame()
    drawn = rec.names()[-6:]
    assert drawn == ["polygon", "polyline", "arc", "line", "atlas", "atlas"], (
        "a closed shape is filled, an open one is stroked, and an empty image "
        "path draws nothing")
    # an image nobody gave a path to is no op and no load
    assert rec.loaded == [(texture_key("assets/cover.png"), "assets/cover.png")]
    ring = rec.last("arc")
    assert ring.get("start") == 0.0 and ring.get("sweep") == pytest.approx(3.0)
    assert ring.get("color") == ctx.skin.resolve("danger", "normal").border
    assert rec.uploaded, "the glyph was cut out of this renderer's own atlas"
    icon = [o for o in rec.ops if o.name == "atlas"][-1]
    assert icon.get("texture"), "an icon with no texture key draws nothing"
    assert max(icon.get("uv")) > 0.0


def test_a_child_painted_by_a_script_is_clipped_by_the_panel_it_lives_in(
        stage):
    """A painter cannot reach outside its box, and a box can be smaller than that."""
    program, ctx, rec = stage
    _, error = program.run(
        'WIDGET_DEFINE("pad")\n'
        'VAR deck = PANEL("deck")\n'
        "PLACE(deck, 100, 100, 50, 50)\n"
        'VAR kid = WIDGET("pad")\n'
        "NODE_ADD(deck, kid)\n"
        "PLACE(kid, -10, -10, 80, 40)\n"
        'ON_PAINT(kid, FUNC(p) -> P_RECT(p, 0, 0, 80, 40, 0, "accent"))\n'
    )
    assert error is None, error.as_string()
    ctx.frame()
    held = program.symbol_table.get("kid").target
    assert held.width == 80.0, "the widget is still its own size"
    plate = rec.last("rect")
    # ... but everything it drew carries the panel's content box as its clip
    assert plate.clip == held.clip
    assert plate.clip[2] < 80.0 and plate.clip[3] < 40.0


def test_a_clip_a_painter_forgot_to_close_is_closed_for_it(stage):
    """An unbalanced ``P_CLIP`` costs the ops their own boxes, not the frame."""
    program, ctx, rec = stage
    _, error = program.run(
        STEPPER +
        'FUNC leaky(p)\n'
        '    P_CLIP(p, 10, 10, 20, 10)\n'
        '    P_RECT(p, 0, 0, 80, 40, 0, "surface")\n'
        'END\n'
        'ON_PAINT(w, leaky)\n'
    )
    assert error is None, error.as_string()
    ctx.frame()
    held = program.symbol_table.get("w").target
    box = held.clip
    inside = [o for o in rec.ops if o.name == "rect"][-1]
    assert inside.clip == (box[0] + 10, box[1] + 10, 20.0, 10.0)
    assert rec.clip is None, "the frame was left as the painter found it"
    assert ctx.paints.stats()["records"] == 1, "the face was still worth caching"


def test_a_clip_can_only_ever_make_a_widget_smaller(stage):
    """``P_CLIP(p, -10, -10, 1000, 1000)`` is still one widget's drawable area."""
    program, ctx, rec = stage
    program.run(
        STEPPER +
        'FUNC big(p)\n'
        '    P_CLIP(p, -10, -10, 1000, 1000)\n'
        '    P_RECT(p, 0, 0, 8, 8, 0, "surface")\n'
        '    P_CLIP_END(p)\n'
        '    P_RECT(p, 60, 30, 8, 8, 0, "surface")\n'
        'END\n'
        'ON_PAINT(w, big)\n'
    )
    ctx.frame()
    held = program.symbol_table.get("w").target
    marks = [o for o in rec.ops if o.name == "rect"][-2:]
    assert marks[0].clip == held.clip, "the intersection is the widget, not the ask"
    assert marks[1].clip == held.clip, "P_CLIP_END came back out to the same box"


# -- the cache: the script runs once, the picture runs every frame --------


def test_the_painter_runs_once_and_every_frame_after_that_replays_it(stage):
    """Two frames, one record - the whole reason :class:`PaintList` exists."""
    program, ctx, rec = stage
    program.run(
        STEPPER + 'ON_PAINT(w, FUNC(p) -> P_RECT(p, 0, 0, 80, 40, 0, "accent"))')
    ctx.frame()
    said = rec.names()
    rec.reset()
    ctx.frame()
    assert rec.names() == said, "the second frame is the first one replayed"
    stats = ctx.paints.stats()
    assert stats["records"] == 1 and stats["replays"] == 1
    assert stats["cached"] == 1 and stats["widgets"] == 1
    assert stats["ops"] == 1, "the face is one op, kept once"


def test_a_face_being_repainted_does_not_leave_the_old_one_behind(stage):
    """Twenty drags, one entry: a repaint retires the key this widget had before."""
    program, ctx, _ = stage
    program.run(
        STEPPER +
        'FUNC face(p, k)\n'
        '    P_RECT(p, 0, 0, WIDTH(p), HEIGHT(p), GET(k, "value"), "accent")\n'
        'END\n'
        'ON_PAINT(w, face)\n')
    ctx.frame()
    for step in range(1, 20):
        program.run(f'SET(w, "value", {step})')
        ctx.frame()
    stats = ctx.paints.stats()
    assert stats["records"] == 20, "one paint per value the widget was asked to hold"
    assert stats["replays"] == 0
    assert stats["cached"] == 1 and stats["widgets"] == 1, (
        "and not one cached face per pixel it travelled")


def test_a_theme_change_repaints_a_script_face_once(stage):
    """The skin's digest is in the key, so ``STYLE(theme)`` reaches a painted face."""
    program, ctx, rec = stage
    program.run(STEPPER +
                'ON_PAINT(w, FUNC(p) -> P_RECT(p, 0, 0, 80, 40, 0, "surface"))')
    ctx.frame()
    dark = rec.last("rect").get("color")
    program.run('STYLE("light")')
    rec.reset()
    ctx.frame()
    assert rec.last("rect").get("color") != dark
    stats = ctx.paints.stats()
    assert stats["records"] == 2 and stats["cached"] == 1


def test_paint_request_drops_one_face_and_says_whether_there_was_one(stage,
                                                                     eval_ui):
    """``PAINT_REQUEST(w)`` for a face drawn from something outside the props table."""
    program, ctx, rec = stage
    program.run(STEPPER + 'ON_PAINT(w, FUNC(p) -> P_RECT(p, 0, 0, 80, 40, 0, "accent"))')
    ctx.frame()
    assert eval_ui("PAINT_REQUEST(w)") == 1
    assert ctx.paints.stats()["cached"] == 0
    assert eval_ui("PAINT_REQUEST(w)") == 0, "there was no face left to drop"
    rec.reset()
    ctx.frame()
    stats = ctx.paints.stats()
    assert stats["records"] == 2 and stats["replays"] == 0
    assert stats["cached"] == 1


def test_giving_a_widget_a_new_painter_retires_the_face_the_old_one_recorded(
        stage):
    """``ON_PAINT`` twice is a changed face, not a second one waiting its turn."""
    program, ctx, rec = stage
    program.run(STEPPER + 'ON_PAINT(w, FUNC(p) -> P_RECT(p, 0, 0, 80, 40, 0, "accent"))')
    ctx.frame()
    program.run('ON_PAINT(w, FUNC(p) -> P_CIRCLE(p, 40, 20, 8, "ok"))')
    assert ctx.paints.stats()["cached"] == 0, "registration retired it"
    rec.reset()
    ctx.frame()
    assert rec.names()[-1] == "circle"
    stats = ctx.paints.stats()
    assert stats["records"] == 2 and stats["cached"] == 1


def test_a_widget_that_stops_painting_itself_gets_the_face_its_kind_had(
        stage, eval_ui):
    """``OFF(w, "paint")`` is the other half of ``ON_PAINT``, including the cache."""
    program, ctx, rec = stage
    program.run('VAR s = SLIDER("BPM", 20, 300, 120)\nPLACE(s, 10, 10, 160, 24)\n'
                'ON_PAINT(s, FUNC(p) -> P_RECT(p, 0, 10, WIDTH(p), 4, 2, "track"))')
    ctx.frame()
    assert rec.texts() == []
    assert eval_ui('OFF(s, "paint")') == 1
    assert ctx.paints.stats()["cached"] == 0
    rec.reset()
    ctx.frame()
    assert rec.texts() == ["BPM"], "the catalog face came back with its label"


def test_a_painter_that_draws_nothing_is_still_one_face_the_cache_keeps(stage):
    """An empty list is an answer: the painter is not asked again every frame."""
    program, ctx, rec = stage
    program.run(STEPPER + 'ON_PAINT(w, FUNC(p) -> 0)')
    ctx.frame()
    ctx.frame()
    assert [op.name for op in rec.frames[-1]] == ["clear", "rect", "rect"], (
        "the window's own plate and nothing else, on each of the two frames")
    stats = ctx.paints.stats()
    assert stats["records"] == 1 and stats["replays"] == 1
    assert stats["ops"] == 0 and stats["cached"] == 1


# -- when a painter fails ---------------------------------------------------


def test_a_failing_painter_leaves_the_placeholder_and_not_a_half_face(stage):
    """A face that could not be finished is thrown away, and its box says so."""
    program, ctx, rec = stage
    _, error = program.run(
        STEPPER +
        'FUNC broken(p)\n'
        '    P_RECT(p, 0, 0, 80, 40, 0, "accent")\n'
        '    P_RECT(p, 0, 0, "wide", 40)\n'
        'END\n'
        'ON_PAINT(w, broken)\n')
    assert error is None, error.as_string()
    ctx.frame()  # the mistake is the painter's, so it must not reach the caller
    marks = rec.ops_named("rect")
    assert len(marks) == 4, "the window's own plate, then the placeholder's two"
    said = ctx.skin.resolve("placeholder", "normal")
    assert marks[-2].get("color") == said.border
    assert marks[-1].get("color") == said.fill
    held = program.symbol_table.get("w").target
    assert marks[2].clip == held.clip
    stats = ctx.paints.stats()
    assert stats["records"] == 0 and stats["cached"] == 0, (
        "the op the painter got to before failing was never shown")
    assert stats["failures"] == 1 and stats["placeholders"] == 1
    assert len(ctx.handlers.errors) == 1
    assert "ON_PAINT" in ctx.handlers.errors[0]
    assert "root/stepper" in ctx.handlers.errors[0], "and it said which one failed"


def test_a_painter_that_keeps_failing_is_muted_and_the_window_keeps_running(
        stage):
    """The plan's three strikes, at the paint layer: a log line, not a dead UI."""
    program, ctx, rec = stage
    program.run(STEPPER + 'ON_PAINT(w, FUNC(p) -> P_RECT(p, 0, 0, "wide", 40))')
    for _ in range(MAX_HANDLER_ERRORS + 1):
        rec.reset()
        ctx.frame()
    said = ctx.handlers.stats()
    assert said["errors"] == MAX_HANDLER_ERRORS
    assert said["muted"] == 1
    assert said["fired"] == MAX_HANDLER_ERRORS + 1, (
        "a muted handler is still a call that was made and turned down")
    paints = ctx.paints.stats()
    assert paints["records"] == 0
    assert paints["placeholders"] == MAX_HANDLER_ERRORS + 1, (
        "and the box it failed in is drawn every frame all the same")
    assert rec.count("rect") == 4, (
        "the last frame is an ordinary frame with a placeholder")


def test_a_muted_painter_is_replaced_by_the_next_one_a_script_registers(stage):
    """The mute is on the handler, never on the widget or on the whole window."""
    program, ctx, rec = stage
    program.run(STEPPER + 'ON_PAINT(w, FUNC(p) -> P_RECT(p, 0, 0, "wide", 40))')
    for _ in range(MAX_HANDLER_ERRORS):
        ctx.frame()
    assert ctx.paints.stats()["records"] == 0
    program.run('OFF(w, "paint")\n'
                'ON_PAINT(w, FUNC(p) -> P_RECT(p, 0, 0, 80, 40, 0, "accent"))')
    rec.reset()
    ctx.frame()
    assert rec.last("rect").get("color") == ctx.skin.resolve("accent",
                                                            "normal").fill
    assert ctx.paints.stats()["records"] == 1


# -- ON_HIT: which part of a widget takes the click -----------------------

#: the plan's stepper: one widget, two halves, both painted and hit-tested by the
#  script - and the ``"up"``/``"down"`` it answers is what ``ON_CLICK`` is handed
STEP = (
    STEPPER +
    'FUNC hit(x, y)\n'
    '    IF y < 20 THEN RETURN "up"\n'
    '    RETURN "down"\n'
    'END\n'
    'ON_HIT(w, hit)\n'
    'ON_CLICK(w, FUNC(r) -> SET(w, "label", r))\n'
)


def test_a_click_nobody_answered_arrives_with_the_region_the_script_left_out(
        stage, eval_ui):
    """No ``ON_HIT`` is the whole box, unnamed - a ``NULL`` a handler may ignore."""
    program, ctx, _ = stage
    program.run(
        'VAR b = BUTTON("go")\n'
        "PLACE(b, 10, 10, 60, 24)\n"
        'ON_CLICK(b, FUNC(r, x, y) -> SET(b, "tooltip", STR(r)))\n')
    ctx.router.click(20, 20)
    assert ctx.handlers.errors == []
    assert eval_ui('GET(b, "tooltip")') == "0", "0 is this language's NULL"


def test_a_click_carries_the_point_the_pointer_was_at(stage, eval_ui):
    """The window's numbers, unchanged - which is what a drag-out check needs."""
    program, ctx, _ = stage
    program.run(
        'VAR b = BUTTON("go")\n'
        "PLACE(b, 10, 10, 60, 24)\n"
        'ON_CLICK(b, FUNC(r, x, y, btn) -> SET(b, "tooltip", STR(x) + " " + STR(y)))\n')
    ctx.router.click(20, 18)
    assert eval_ui('GET(b, "tooltip")') == "20.0 18.0"


def test_one_hit_handler_can_be_two_buttons(stage, eval_ui):
    """``"up"`` and ``"down"`` in one widget, without putting two widgets in it."""
    program, ctx, _ = stage
    program.run(STEP)
    ctx.router.click(30, 20)  # local (20, 10): the upper half
    assert eval_ui('GET(w, "label")') == "up"
    ctx.router.click(30, 45)  # local (20, 35): the lower half
    assert eval_ui('GET(w, "label")') == "down"
    assert ctx.handlers.errors == []


def test_a_hit_handler_can_narrow_the_box_that_takes_the_click(stage, eval_ui):
    """A box of four numbers is *its own* coordinates, and outside it is no click."""
    program, ctx, _ = stage
    program.run(
        STEPPER +
        'ON_HIT(w, FUNC(x, y) -> [0, 0, 40, 40])\n'
        'ON_CLICK(w, FUNC(r) -> SET(w, "label", "took"))\n')
    ctx.router.click(70, 30)  # local (60, 20): past the half it claimed
    assert eval_ui('GET(w, "label")') == ""
    assert ctx.hit(70, 30) is program.symbol_table.get("w").target, (
        "the refusal is about the click, not about what the pointer is over")
    ctx.router.click(30, 30)  # local (20, 20): inside it
    assert eval_ui('GET(w, "label")') == "took"


def test_a_region_with_no_area_is_the_one_answer_that_refuses_everything(
        stage, eval_ui):
    """``[0, 0, 0, 0]`` is a widget saying "not on me", and it is taken literally."""
    program, ctx, _ = stage
    program.run(
        STEPPER +
        'ON_HIT(w, FUNC(x, y) -> [0, 0, 0, 0])\n'
        'ON_CLICK(w, FUNC(r) -> SET(w, "label", "took"))\n')
    for point in ((20, 20), (50, 30), (85, 45)):
        ctx.router.click(*point)
    assert eval_ui('GET(w, "label")') == ""
    assert ctx.handlers.errors == []


def test_a_hit_handler_answers_in_the_numbers_its_painter_drew_with(stage,
                                                                    eval_ui):
    """``(0, 0)`` is the widget's corner for a painter *and* for a hit handler."""
    program, ctx, _ = stage
    program.run(
        STEPPER +
        'FUNC hit(x, y)\n'
        '    SET(w, "tooltip", STR(x))\n'
        '    RETURN [x - 1, y - 1, 2, 2]\n'
        'END\n'
        'ON_HIT(w, hit)\n'
        'ON_CLICK(w, FUNC(r) -> SET(w, "label", "took"))\n')
    ctx.router.click(30, 30)
    held = program.symbol_table.get("w").target
    assert held.screen_box == (10.0, 10.0, 80.0, 40.0)
    assert eval_ui('GET(w, "tooltip")') == "20.0", "local, not window"
    assert eval_ui('GET(w, "label")') == "took", "and the box was drawn round that point"


def test_a_hit_handler_that_raises_cannot_stop_a_click_by_being_wrong(stage,
                                                                       run, eval_ui):
    """A bug in a hit handler is logged, and the control keeps working."""
    program, ctx, _ = stage
    run(
        STEPPER +
        'ON_HIT(w, FUNC(x, y) -> WIDTH("nope"))\n'
        'ON_CLICK(w, FUNC(r) -> SET(w, "label", "took"))\n')
    ctx.router.click(30, 30)
    assert eval_ui('GET(w, "label")') == "took"
    assert len(ctx.handlers.errors) == 1
    assert "ON_HIT" in ctx.handlers.errors[0]


def test_the_last_hit_handler_that_said_something_is_the_region_a_click_gets(
        stage, run, eval_ui):
    """Two registrations are two chances to be more specific, in order."""
    program, ctx, _ = stage
    run(
        STEPPER +
        'ON_HIT(w, FUNC(x, y) -> "anywhere")\n'
        'FUNC halves(x, y)\n'
        '    IF x < 20 THEN RETURN "left"\n'
        '    RETURN "right"\n'
        'END\n'
        'ON_HIT(w, halves)\n'
        'ON_CLICK(w, FUNC(r) -> SET(w, "label", r))\n')
    ctx.router.click(70, 30)
    assert eval_ui('GET(w, "label")') == "right"
    ctx.router.click(15, 30)
    assert eval_ui('GET(w, "label")') == "left"


# -- the whole thing, in one script ---------------------------------------


def test_a_project_can_define_a_kind_draw_it_and_click_it(stage, eval_ui):
    """S7i's acceptance line, in one file's worth of ``.crude``."""
    program, ctx, rec = stage
    _, error = program.run(
        'WIDGET_DEFINE("stepper")\n'
        'PROP_DEFINE("stepper", "value", "number", 10, 0, 100)\n'
        'VAR w = WIDGET("stepper")\n'
        "PLACE(w, 10, 10, 80, 40)\n"
        'FUNC face(p, k)\n'
        '    P_RECT(p, 0, 0, WIDTH(p), HEIGHT(p), 6, "surface")\n'
        '    P_LINE(p, 40, 6, 40, 34, 2, "border")\n'
        '    P_TEXT(p, 4, 4, STR(GET(k, "value")), 13, "text")\n'
        'END\n'
        'ON_PAINT(w, face)\n'
        'FUNC hit(x, y)\n'
        '    IF y < 20 THEN RETURN "up"\n'
        '    RETURN "down"\n'
        'END\n'
        'ON_HIT(w, hit)\n'
        'ON_CLICK(w, FUNC(r) -> SET(w, "label", r))\n')
    assert error is None, error.as_string()
    ctx.frame()
    assert rec.texts() == ["10.0"], "the face is the script's, value and all"
    assert rec.names()[-3:] == ["rect", "line", "text"]
    assert ctx.paints.stats()["records"] == 1

    ctx.router.click(30, 20)
    assert eval_ui('GET(w, "label")') == "up"

    program.run('SET(w, "value", 11)')
    rec.reset()
    ctx.frame()
    assert rec.texts() == ["11.0"]
    stats = ctx.paints.stats()
    assert stats["records"] == 2 and stats["cached"] == 1, (
        "the new value cost one repaint, not a second cached face")

    ctx.router.click(30, 45)
    assert eval_ui('GET(w, "label")') == "down"
    assert ctx.handlers.errors == []
