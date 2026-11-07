"""A face a script paints for itself: the canvas it draws on and the cache it lands in.

:mod:`crude.ui.paint` is the half of S7i a person never sees in a screenshot - the
translation from a widget's own coordinates to the frame's, the record-once/replay-
always rule that keeps a project's Python off the critical path, and the placeholder
that is drawn when a painter could not finish. Everything here is GL-free and
interpreter-free on purpose: the language surface is
:mod:`tests.ui.test_bindings_paint`, and this file tests the layer underneath it
against plain Python callables, so a bug in the cache is not confused with a bug in
the parser.

The box every test works in is ``BOX`` - a widget detached from any tree, whose own
screen box is therefore its own rect, at ``(10, 20)`` and ``80 x 40``. Local ``(0,
0)`` means absolute ``(10, 20)`` in every assertion below.
"""

from __future__ import annotations

from typing import Any

import pytest

from crude.lang.values import List, Number, String
from crude.ui import (
    DEFAULT_THEME,
    FILL,
    INK,
    LINE,
    MAX_PAINT_OPS,
    MAX_PAINT_WIDGETS,
    Canvas,
    DrawOp,
    PaintList,
    Skin,
    UIError,
    Widget,
    as_color,
    in_region,
    region_of,
    theme_names,
    texture_key,
)

#: where the test widget is, and how big: local numbers are added to its corner
BOX = (10.0, 20.0, 80.0, 40.0)

#: a second theme, for the test that says a theme switch is a repaint
OTHER_THEME = next(word for word in theme_names() if word != DEFAULT_THEME)


@pytest.fixture
def skin() -> Skin:
    """The skin a painter is handed: the default theme at the default size."""
    return Skin(DEFAULT_THEME)


def control(kind: str = "button", **values: Any) -> Widget:
    """A placed widget, detached from any tree, with its own box as its own rect."""
    held = Widget(kind, values, name=str(values.get("label") or "face"))
    held.place(*BOX)
    return held


def canvas_for(held: Widget, renderer: Any, skin: Skin) -> Canvas:
    """A canvas clipped exactly the way :meth:`PaintList.record` clips one.

    The recorder is its own target rather than a :class:`_Sponge`, which is the one
    liberty this file takes: it lets a test read the ops straight off the
    :class:`~crude.ui.recording.RecordingRenderer` instead of reaching through the
    cache, and the clip that is opened first is what every op then carries.
    """
    renderer.push_clip(*held.clip)
    return Canvas(renderer, held, renderer, skin, held.clip)


class Job:
    """A painter's job, shaped the way the handler bridge hands one over.

    ``PaintList.paint`` takes a callable that is given the canvas and answers
    whether every ``ON_PAINT`` it ran succeeded; this is that callable, with the
    count of runs and the canvases it was handed kept where a test can read them.
    """

    def __init__(self, ops: int = 1, ok: bool = True,
                 error: Exception = None) -> None:
        self.ops = ops
        self.ok = ok
        self.error = error
        self.runs = 0
        self.canvases: list[Canvas] = []

    def __call__(self, canvas: Canvas) -> bool:
        self.runs += 1
        self.canvases.append(canvas)
        for index in range(self.ops):
            canvas.rect(index, index, 4, 4)
        if self.error is not None:
            raise self.error
        return self.ok


# -- the canvas: local numbers, absolute ops --------------------------------


def test_a_local_box_lands_at_the_widgets_own_corner(recording, skin):
    canvas = canvas_for(control(), recording, skin)
    canvas.rect(0, 0, 12, 8)
    assert recording.rects() == [(10.0, 20.0, 12.0, 8.0)]


def test_every_primitive_that_takes_a_point_moves_it_the_same_way(recording, skin):
    canvas = canvas_for(control(), recording, skin)
    canvas.circle(40, 20, 5)
    canvas.line(0, 0, 80, 40)
    canvas.text(5, 5, "120")
    assert recording.last("circle").get("cx") == 50.0
    assert recording.last("circle").get("cy") == 40.0
    stroke = recording.last("line")
    assert (stroke.get("x1"), stroke.get("y1")) == (10.0, 20.0)
    assert (stroke.get("x2"), stroke.get("y2")) == (90.0, 60.0)
    assert recording.last("text").get("x") == 15.0
    assert recording.last("text").get("y") == 25.0


def test_what_a_painter_draws_carries_the_widgets_own_clip(recording, skin):
    held = control()
    canvas = canvas_for(held, recording, skin)
    canvas.rect(0, 0, 8, 8)
    assert recording.ops[0].clip == BOX


def test_a_painters_clip_can_only_ever_make_the_box_smaller(recording, skin):
    """``P_CLIP(p, -10, -10, 1000, 1000)`` is still one widget's drawable area."""
    canvas = canvas_for(control(), recording, skin)
    canvas.clip(-10, -10, 1000, 1000)
    canvas.rect(0, 0, 8, 8)
    assert canvas.depth == 1
    assert recording.last("rect").clip == BOX
    canvas.clip_end()
    assert canvas.depth == 0


def test_closing_a_clip_nobody_opened_answers_instead_of_raising(recording, skin):
    canvas = canvas_for(control(), recording, skin)
    assert canvas.clip_end() is None
    assert canvas.depth == 0


def test_the_canvas_counts_its_own_ops_and_says_what_it_is(recording, skin):
    held = control()
    canvas = canvas_for(held, recording, skin)
    canvas.rect(0, 0, 8, 8)
    canvas.circle(4, 4, 2)
    assert canvas.ops == 2
    said = canvas.as_dict()
    assert said["kind"] == "button"
    assert said["origin"] == (10.0, 20.0)
    assert said["box"] == [0.0, 0.0, 80.0, 40.0]
    assert said["state"] == "normal" and said["clips"] == 0
    assert str(canvas).startswith("Canvas(face 80x40 @ 10,20 normal, 2 op")


# -- the canvas: colours come from roles ------------------------------------


def test_a_role_is_resolved_in_the_state_the_widget_is_in(recording, skin):
    """No ``if hovered`` in a painter: the same word answers a different colour."""
    held = control(role="accent")
    plain = canvas_for(held, recording, skin).colour("accent", FILL)
    held.on_enter()
    lit = canvas_for(held, recording, skin).colour("accent", FILL)
    assert plain != lit
    assert lit == skin.resolve("accent", "hover").fill


def test_a_role_that_does_not_answer_the_pointer_does_not_change_colour(recording,
                                                                       skin):
    held = control(role="surface")
    held.on_enter()
    assert (canvas_for(held, recording, skin).colour("surface", FILL)
            == skin.resolve("surface", "normal").fill)


def test_a_word_that_is_not_a_role_is_read_as_a_colour(recording, skin):
    canvas = canvas_for(control(), recording, skin)
    assert canvas.colour("#ff0000") == as_color("#ff0000")
    canvas.rect(0, 0, 4, 4, role="red")
    assert recording.last("rect").get("color") == as_color("red")


def test_the_three_channels_a_role_has_are_what_the_three_asks_get(recording, skin):
    canvas = canvas_for(control(), recording, skin)
    look = skin.resolve("button", "normal")
    assert canvas.colour("button", FILL) == look.fill
    assert canvas.colour("button", LINE) == look.border
    assert canvas.colour("button", INK) == look.ink


def test_a_painter_says_nothing_about_width_and_gets_the_roles_own(recording, skin):
    canvas = canvas_for(control(), recording, skin)
    look = skin.resolve("accent", "normal")
    assert canvas.stroke("accent", None) == look.width
    assert canvas.stroke("accent", 3) == 3.0
    assert canvas.stroke("accent", -3) == 0.0, "a negative stroke is a hairline"
    assert canvas.font("caption") == skin.resolve("caption", "normal").font
    assert canvas.radius("card") == skin.resolve("card", "normal").radius


def test_a_word_that_is_not_a_role_still_borrows_the_metrics_it_is_drawn_on(
        recording, skin):
    """``P_LINE(p, ..., "#ff0000")`` wants the box's width, not a default."""
    canvas = canvas_for(control(), recording, skin)
    assert canvas.stroke("#ff0000", None) == skin.resolve("surface", "normal").width


def test_a_label_told_nothing_is_drawn_at_the_roles_line_height(recording, skin):
    canvas = canvas_for(control(), recording, skin)
    canvas.text(0, 0, "120")
    assert recording.last("text").get("size") == skin.resolve("surface",
                                                              "normal").font


# -- the canvas: shapes, icons, images --------------------------------------


def test_a_closed_shape_is_filled_and_an_open_one_is_stroked(recording, skin):
    canvas = canvas_for(control(), recording, skin)
    canvas.poly([(0, 0), (10, 0), (10, 10)])
    assert recording.names() == ["polyline"]
    assert recording.last("polyline").get("closed") is False
    canvas.poly([(0, 0), (10, 0), (10, 10)], closed=True, width=0.0)
    assert recording.names() == ["polyline", "polygon"]


def test_a_point_list_may_be_flat_or_a_list_of_pairs(recording, skin):
    canvas = canvas_for(control(), recording, skin)
    canvas.poly([0, 0, 4, 0, 4, 4], closed=True, width=0.0)
    canvas.poly([(0, 0), (4, 0), (4, 4)], closed=True, width=0.0)
    assert len(recording.ops) == 2
    assert recording.ops[0].get("points") == recording.ops[1].get("points")


def test_a_shape_that_is_not_a_shape_is_refused_in_the_words_that_fix_it(
        recording, skin):
    canvas = canvas_for(control(), recording, skin)
    with pytest.raises(UIError, match="an even list of x,y numbers"):
        canvas.poly([0, 0, 4])
    with pytest.raises(UIError, match="at least one point"):
        canvas.poly([])
    with pytest.raises(UIError, match="a point is two numbers"):
        canvas.poly([(0, 0, 4)])
    with pytest.raises(UIError, match="is not a number"):
        canvas.poly(["wide", 0])


def test_a_glyph_is_one_quad_out_of_the_frames_own_atlas(recording, skin):
    canvas = canvas_for(control(), recording, skin)
    canvas.glyph(0, 0, 16, 16, "play", "accent")
    op = recording.last("atlas")
    assert op.get("x") == 10.0 and op.get("width") == 16.0
    assert op.get("texture"), "an icon with no texture key draws nothing"
    assert max(op.get("uv")) > 0.0, "the whole texture is not one glyph"
    assert op.get("color") == skin.resolve("accent", "normal").ink
    assert recording.uploaded, "the atlas was cut for this renderer"


def test_an_image_is_loaded_once_and_a_missing_one_is_silent(recording, skin):
    canvas = canvas_for(control(), recording, skin)
    canvas.image(0, 0, 20, 20, "")
    assert recording.ops == [], "nothing to load is nothing to draw"
    canvas.image(0, 0, 20, 20, "assets/cover.png")
    key = texture_key("assets/cover.png")
    assert recording.loaded == [(key, "assets/cover.png")]
    assert recording.last("atlas").get("texture") == key


# -- replay ------------------------------------------------------------------


def test_a_replayed_label_is_not_shifted_twice(recording):
    """The reason replay goes through ``_emit`` and not back through ``text``."""
    recording.text(160, 10, "120 BPM", 12, align="center")
    before = list(recording.ops)
    assert before[0].get("x") < 160.0, "a centred label was already moved left"
    assert recording.replay(before) == 1
    assert recording.ops[1] == before[0], "replayed exactly as it was recorded"


def test_replay_groups_the_clip_and_leaves_the_frame_as_it_found_it(recording):
    recording.push_clip(*BOX)
    canvas = Canvas(recording, control(), recording, Skin())
    canvas.rect(0, 0, 8, 8)
    canvas.rect(2, 2, 8, 8)
    held = list(recording.ops)
    assert [op.clip for op in held] == [BOX, BOX]
    recording.pop_clip()
    recording.replay(held)
    assert recording.clip is None
    assert [op.clip for op in recording.ops[-2:]] == [BOX, BOX]


def test_replay_refuses_a_name_a_renderer_does_not_know(recording):
    with pytest.raises(UIError, match="is not an op a renderer can replay"):
        recording.replay([DrawOp("squiggle", {}, None)])
    assert recording.ops == []
    assert recording.clip is None, "a refused list left nothing open"


# -- the cache ---------------------------------------------------------------


def test_a_painter_runs_once_and_every_frame_after_that_is_a_replay(recording,
                                                                    skin):
    paints = PaintList()
    held = control()
    job = Job(ops=3)
    assert paints.paint(held, recording, skin, job) == 3
    assert paints.paint(held, recording, skin, job) == 3
    assert (job.runs, paints.records, paints.replays) == (1, 1, 1)
    assert recording.count("rect") == 6
    assert paints.cached(held) and held in paints


def test_a_prop_change_costs_a_repaint_and_retires_the_face_it_replaces(
        recording, skin):
    paints = PaintList()
    held = control(label="one")
    paints.paint(held, recording, skin, Job())
    held.set("label", "two")
    paints.paint(held, recording, skin, Job())
    assert (paints.records, paints.replays) == (2, 0)
    assert len(paints) == 1, "one widget keeps one face, not one per change"


@pytest.mark.parametrize("change", ["box", "theme", "dpr"])
def test_moving_the_box_the_theme_or_the_device_ratio_costs_a_repaint(
        recording, skin, change):
    paints = PaintList()
    held = control()
    job = Job()
    paints.paint(held, recording, skin, job)
    if change == "box":
        held.place(30, 20, 80, 40)
        other = skin
    elif change == "theme":
        other = Skin(OTHER_THEME)
    else:
        recording.begin(320, 200, 320, 200)
        other = skin
    paints.paint(held, recording, other, job)
    assert job.runs == 2


def test_the_same_skin_asked_for_twice_is_not_a_repaint(recording, skin):
    paints = PaintList()
    held = control()
    job = Job()
    paints.paint(held, recording, skin, job)
    paints.paint(held, recording, Skin(DEFAULT_THEME), job)
    assert job.runs == 1, "an equal digest is the same face"


def test_a_face_that_was_recorded_is_the_face_that_is_replayed(recording, skin):
    """The plan's promise: the first frame and the cached one cannot disagree."""
    paints = PaintList()
    held = control()
    paints.paint(held, recording, skin, Job(ops=4))
    first = recording.ops[:4]
    paints.paint(held, recording, skin, Job(ops=4))
    assert recording.ops[4:8] == first


def test_a_painter_that_fails_is_not_cached_and_draws_the_placeholder(recording,
                                                                      skin):
    paints = PaintList()
    held = control()
    before = recording.op_count
    paints.paint(held, recording, skin, Job(ops=3, ok=False))
    assert not paints.cached(held) and paints.records == 0
    assert paints.failures == 1 and paints.placeholders == 1
    found = skin.resolve("placeholder", "normal")
    edge = recording.ops[before]
    assert edge.get("color") == found.border, "a failure is meant to be looked at"


def test_a_painter_that_raises_in_python_costs_the_frame_nothing(recording, skin):
    """A bad role word is the same placeholder and a log line, not a dead window."""
    lines: list[str] = []
    paints = PaintList(log=lines.append)
    held = control()
    paints.paint(held, recording, skin, Job(error=UIError("no role 'banana'")))
    assert paints.failures == 1 and len(paints) == 0
    assert "'face'" in lines[0] and "banana" in lines[0]


def test_a_painter_that_left_a_clip_open_is_wound_back_before_the_ops_are_kept(
        recording, skin):
    """The list that is cached is the one the frame will replay, clip-balanced."""
    paints = PaintList()
    held = control()
    kept: list[Canvas] = []

    def ragged(canvas: Canvas) -> bool:
        kept.append(canvas)
        canvas.clip(0, 0, 10, 10)
        canvas.rect(0, 0, 4, 4)
        return True

    paints.paint(held, recording, skin, ragged)
    assert kept[0].depth == 0, "an open clip is closed before the list is kept"
    assert held in paints
    assert [op.clip for op in paints.ops_for(held)] == [(10.0, 20.0, 10.0, 10.0)]


# -- the plan's guard rails --------------------------------------------------


def test_the_op_budget_is_said_once_per_widget(recording, skin):
    lines: list[str] = []
    paints = PaintList(log=lines.append, max_ops=2)
    held = control()
    job = Job(ops=3)
    paints.paint(held, recording, skin, job)
    assert len(lines) == 1 and "painted 3 op(s)" in lines[0]
    held.set("label", "again")
    paints.paint(held, recording, skin, job)
    assert len(lines) == 1, "over budget every frame is not warned at every frame"
    paints.invalidate(held)
    paints.paint(held, recording, skin, job)
    assert len(lines) == 2, "a face that was asked for again may be warned again"


def test_the_widget_budget_is_said_once_per_paint_list(recording, skin):
    lines: list[str] = []
    paints = PaintList(log=lines.append, max_widgets=0)
    paints.paint(control(label="a"), recording, skin, Job())
    paints.paint(control(label="b"), recording, skin, Job())
    assert len(lines) == 1 and "budget is 0" in lines[0]


def test_the_budgets_a_project_can_raise_are_the_ones_the_plan_named():
    assert (MAX_PAINT_WIDGETS, MAX_PAINT_OPS) == (64, 200)


def test_a_widget_with_no_box_costs_nothing_and_runs_no_painter(recording, skin):
    paints = PaintList()
    held = control()
    held.place(10, 20, 0, 0)
    job = Job()
    assert paints.paint(held, recording, skin, job) == 0
    assert job.runs == 0
    assert paints.placeholder(held, recording, skin) == 0
    assert recording.ops == []


# -- invalidation ------------------------------------------------------------


def test_requesting_a_paint_reports_whether_there_was_one_to_drop(recording, skin):
    paints = PaintList()
    held = control()
    paints.paint(held, recording, skin, Job())
    assert paints.invalidate(held) == 1
    assert paints.invalidate(held) == 0
    assert paints.invalidate(held.uid) == 0, "a uid is an accepted way to name it"
    paints.paint(held, recording, skin, Job())
    assert paints.invalidate(held) == 1


def test_clearing_the_cache_retires_every_face_at_once(recording, skin):
    paints = PaintList()
    first, second = control(label="a"), control(label="b")
    paints.paint(first, recording, skin, Job())
    paints.paint(second, recording, skin, Job())
    assert len(paints) == 2
    assert paints.clear() == 2
    assert first not in paints and paints.ops_for(first) == ()
    assert "0 cached face(s) on 0 widget(s), 2 painted, 0 replayed" in str(paints)


def test_the_numbers_a_report_prints_are_the_ones_the_cache_kept(recording, skin):
    paints = PaintList()
    held = control()
    paints.paint(held, recording, skin, Job(ops=5))
    paints.paint(held, recording, skin, Job(ops=5))
    said = paints.stats()
    assert said["cached"] == 1 and said["widgets"] == 1 and said["ops"] == 5
    assert said["records"] == 1 and said["replays"] == 1
    assert said["max_ops"] == MAX_PAINT_OPS


# -- what an ON_HIT answer means ---------------------------------------------


@pytest.mark.parametrize("said, wanted", [
    (None, None),
    ("", None),
    ("   ", None),
    (0, None),
    (False, None),
    ("up", "up"),
    ("  down  ", "down"),
    (1, 1),
    ([0, 0, 80, 20], (0.0, 0.0, 80.0, 20.0)),
    ((0, 0, 0, 0), (0.0, 0.0, 0.0, 0.0)),
    ([1, 2], None),
    ([1, 2, "a", "b"], [1, 2, "a", "b"]),
])
def test_a_hit_handlers_plain_python_answer_becomes_the_region_it_meant(
        said, wanted):
    found = region_of(said)
    assert found == wanted
    assert type(found) is type(wanted) or found is wanted


def test_a_language_value_is_read_as_the_region_the_script_handed_back():
    """The bridge cannot import the language, so unwrapping is by duck-typing."""
    assert region_of(String("up")) == "up"
    assert region_of(Number(0)) is None, "this language's NULL is its 0"
    assert region_of(Number(7)) == 7
    assert region_of(List([Number(0), Number(0), Number(40), Number(40)])) \
        == (0.0, 0.0, 40.0, 40.0)
    assert region_of(List([Number(1), Number(2)])) is None


def test_a_name_narrows_what_a_click_means_never_where_it_may_land():
    held = control()
    assert in_region("up", 999.0, 999.0, held) is True
    assert in_region(None, 999.0, 999.0, held) is True


def test_a_box_is_the_widgets_own_numbers_and_an_empty_one_is_the_only_refusal():
    held = control()
    # local (20, 20) is absolute (30, 40)
    assert in_region((0, 0, 40, 40), 30.0, 40.0, held) is True
    assert in_region((0, 0, 40, 40), 70.0, 50.0, held) is False
    assert in_region((0, 0, 40, 40), 50.0, 60.0, held) is False, "half-open"
    assert in_region((0, 0, 0, 0), 10.0, 20.0, held) is False
    # a box that is not at the corner: local (40, 25) is absolute (50, 45)
    assert in_region((40, 20, 40, 20), 50.0, 45.0, held) is True
    assert in_region((40, 20, 40, 20), 50.0, 15.0, held) is False
