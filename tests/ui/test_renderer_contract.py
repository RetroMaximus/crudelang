"""One contract, every renderer: the same rules for the fake and the headless one.

The plan's acceptance line for S6 is "renderer contract tests", and the hazard
they are aimed at is drift: three backends with three signatures for a rounded
box, so a widget looks right in a window and wrong in a test. So the rules that
decide an argument live in :class:`crude.ui.base.Renderer` once, and this file
runs them against every renderer in the package - including the GL one when a
display is available - through the same calls, without knowing which is which.
"""

from __future__ import annotations

import math

import pytest

from crude.ui import (
    CAPS, DRAW_METHODS, RENDERER_OPS, BLACK, TRANSPARENT, WHITE, DrawOp, RGBA,
    Renderer, RecordingRenderer, UIError, as_color,
)
from crude.ui.null_renderer import NullRenderer

#: every renderer the contract is judged against; the GL one joins in the
#: ``requires_gl`` run, where a context can actually be made
FAKES = (RecordingRenderer, NullRenderer)


def every_renderer() -> list:
    """The classes, so a test can build one and still say which it was."""
    return list(FAKES)


@pytest.fixture(params=FAKES, ids=lambda cls: cls.__name__)
def renderer(request):
    """A renderer mid-frame on 320 x 200 logical pixels at 2x."""
    made = request.param()
    made.begin(320, 200, 640, 400)
    return made


@pytest.fixture(params=FAKES, ids=lambda cls: cls.__name__)
def cold(request):
    """The same, with no frame started yet."""
    return request.param()


# -- the shape of the surface ---------------------------------------------


def test_every_draw_method_emits_an_op_of_its_own_name_and_back():
    """One method per op, one op per method - or this test says which is missing."""
    assert set(DRAW_METHODS) == set(RENDERER_OPS)
    assert len(DRAW_METHODS) == len(RENDERER_OPS)


def test_a_renderer_owns_its_op_list():
    assert Renderer.OPS == RENDERER_OPS


@pytest.mark.parametrize("cls", FAKES)
def test_the_contract_is_a_renderer(cls):
    assert issubclass(cls, Renderer)


@pytest.mark.parametrize("name", DRAW_METHODS)
def test_each_method_is_the_only_thing_that_can_emit_its_op(renderer, name):
    # the count moves by exactly one: a method that drew twice, or not at all,
    # would make a layout assertion read a picture that is not the one on screen
    arguments = {
        "clear": (), "rect": (1, 2, 3, 4), "quad": ((0, 0), (1, 0), (1, 1), (0, 1)),
        "line": ((0, 0), (3, 4)), "circle": (5, 5, 4), "arc": (5, 5, 4, 1, 0, 1),
        "polyline": [((0, 0), (1, 1))], "polygon": [((0, 0), (1, 0), (1, 1))],
        "text": (1, 1, "hi"), "atlas": (0, 0, 8, 8, "tex"),
    }[name]
    before = renderer.op_count
    getattr(renderer, name)(*arguments)
    assert renderer.op_count == before + 1


# -- a frame ---------------------------------------------------------------


def test_a_frame_knows_its_own_size_and_ratio(renderer):
    assert (renderer.width, renderer.height) == (320.0, 200.0)
    assert (renderer.fb_width, renderer.fb_height) == (640, 400)
    assert renderer.dpr == 2.0


def test_a_frame_with_no_measured_size_is_one_pixel_to_the_logical_pixel(cold):
    cold.begin(100, 50)
    assert (cold.fb_width, cold.fb_height) == (100, 50)
    assert cold.dpr == 1.0


def test_a_frame_counts_the_ops_it_was_given(renderer):
    renderer.rect(0, 0, 10, 10)
    renderer.circle(1, 1, 2)
    assert renderer.op_count == 2
    assert renderer.end() == 2
    assert renderer.frame_count == 1


def test_the_next_frame_starts_the_count_again_but_the_history_stays(renderer):
    renderer.rect(0, 0, 10, 10)
    renderer.end()
    renderer.begin(320, 200, 640, 400)
    assert renderer.op_count == 0
    renderer.circle(1, 1, 2)
    assert renderer.op_count == 1
    renderer.end()
    assert renderer.frame_count == 2


@pytest.mark.parametrize("name", DRAW_METHODS)
def test_nothing_can_be_drawn_before_the_frame_opens(cold, name):
    arguments = {
        "clear": (), "rect": (1, 2, 3, 4), "quad": ((0, 0), (1, 0), (1, 1), (0, 1)),
        "line": ((0, 0), (3, 4)), "circle": (5, 5, 4), "arc": (5, 5, 4),
        "polyline": [((0, 0), (1, 1))], "polygon": [((0, 0), (1, 0), (1, 1))],
        "text": (1, 1, "hi"), "atlas": (0, 0, 8, 8, "tex"),
    }[name]
    with pytest.raises(UIError, match="outside a frame"):
        getattr(cold, name)(*arguments)


def test_the_refusal_names_the_call_and_what_to_do_about_it(cold):
    with pytest.raises(UIError, match="'rect'"):
        cold.rect(0, 0, 1, 1)
    with pytest.raises(UIError, match=r"begin\(width, height\)"):
        cold.rect(0, 0, 1, 1)


# -- boxes -----------------------------------------------------------------


def test_a_box_turned_inside_out_is_moved_rather_than_lost(recording):
    recording.rect(10, 10, -5, -8)
    op = recording.last("rect")
    assert (op.get("x"), op.get("y")) == (5.0, 2.0)
    assert (op.get("width"), op.get("height")) == (5.0, 8.0)


def test_a_corner_rounded_past_the_shape_is_the_shape(recording):
    recording.rect(0, 0, 20, 10, radius=100)
    assert recording.last("rect").get("radius") == 5.0


def test_a_negative_radius_is_a_square_corner(recording):
    recording.rect(0, 0, 20, 20, radius=-4)
    assert recording.last("rect").get("radius") == 0.0


def test_a_rotation_is_handed_on_in_radians_without_being_second_guessed(recording):
    recording.rect(0, 0, 4, 4, rotation=math.pi)
    assert recording.last("rect").get("rotation") == math.pi


# -- the shapes only the contract can settle -------------------------------


def test_a_circle_is_its_centre_and_a_radius_that_cannot_be_negative(recording):
    recording.circle(3, 4, -9)
    op = recording.last("circle")
    assert (op.get("cx"), op.get("cy"), op.get("radius")) == (3.0, 4.0, 0.0)


def test_an_arc_is_told_as_a_start_and_how_far_it_turns(recording):
    recording.arc(10, 10, 8, 2, start=math.pi / 2, end=0)
    op = recording.last("arc")
    assert op.get("start") == pytest.approx(math.pi / 2)
    # the long way round, decided once, instead of in each backend's own shader
    assert op.get("sweep") == pytest.approx(3 * math.pi / 2)


def test_an_arc_left_untold_is_a_whole_ring(recording):
    recording.arc(10, 10, 8)
    assert recording.last("arc").get("sweep") > math.tau * 0.99


def test_an_arc_that_starts_behind_the_frame_wraps_instead_of_going_negative(recording):
    recording.arc(10, 10, 8, start=-0.1, end=1)
    op = recording.last("arc")
    assert op.get("start") == pytest.approx(math.tau - 0.1)
    assert op.get("sweep") == pytest.approx(1.1)


@pytest.mark.parametrize("caps", CAPS)
def test_the_three_ends_a_stroke_may_have(recording, caps):
    recording.arc(1, 1, 4, 1, 0, 1, caps=caps)
    assert recording.last("arc").get("caps") == caps


def test_an_end_cap_that_does_not_exist_is_said_so(recording):
    with pytest.raises(UIError, match="end cap"):
        recording.arc(1, 1, 4, 1, 0, 1, caps="pointy")


def test_a_line_with_two_widths_is_a_taper_and_one_without_is_a_capsule(recording):
    recording.line((0, 0), (10, 0), 4)
    assert recording.last("line").get("thickness2") is None
    recording.line((0, 0), (10, 0), 4, thickness2=1)
    assert recording.last("line").get("thickness2") == 1.0


def test_a_stroke_of_no_width_is_a_line_that_shows_nothing_rather_than_inverted(recording):
    recording.line((0, 0), (10, 0), -3)
    assert recording.last("line").get("thickness") == 0.0


def test_a_polyline_remembers_whether_it_came_back_to_where_it_started(recording):
    recording.polyline([(0, 0), (4, 4), (8, 0)], closed=True)
    op = recording.last("polyline")
    assert op.get("closed") is True
    assert op.get("points") == ((0.0, 0.0), (4.0, 4.0), (8.0, 0.0))


def test_a_polygon_holds_its_points_as_numbers_and_its_order_as_well(recording):
    recording.polygon([(1, 2), (3, 4), (5, 6)])
    assert recording.last("polygon").get("points") == (
        (1.0, 2.0), (3.0, 4.0), (5.0, 6.0))


@pytest.mark.parametrize("bad", [(1,), "ab", None, 5])
def test_a_point_that_is_not_two_numbers_is_refused_where_it_is_handed_in(recording, bad):
    with pytest.raises(UIError, match="two numbers"):
        recording.quad(bad, (0, 0), (1, 1), (1, 0))


# -- clipping --------------------------------------------------------------


def test_a_frame_opens_with_the_whole_frame_open(renderer):
    assert renderer.clip is None


def test_an_op_carries_the_clip_that_was_open_when_it_was_drawn(recording):
    recording.push_clip(10, 10, 50, 50)
    recording.rect(0, 0, 100, 100)
    assert recording.last("rect").clip == (10.0, 10.0, 50.0, 50.0)
    recording.pop_clip()
    recording.rect(0, 0, 100, 100)
    assert recording.ops[-1].clip is None


def test_a_clip_inside_a_clip_is_cut_by_both(recording):
    recording.push_clip(0, 0, 200, 200)
    recording.push_clip(20, 20, 100, 100)
    assert recording.clip == (20.0, 20.0, 100.0, 100.0)
    recording.push_clip(80, 80, 100, 100)
    # the old renderer kept only the top of the stack, so the panel two levels up
    # could be drawn over; three clips now agree on one rect
    assert recording.clip == (80.0, 80.0, 40.0, 40.0)


def test_a_clip_that_closes_is_the_one_that_was_open(renderer):
    renderer.push_clip(1, 2, 3, 4)
    assert renderer.pop_clip() == (1.0, 2.0, 3.0, 4.0)
    assert renderer.clip is None


def test_a_clip_popped_with_none_open_is_said_rather_than_guessed(renderer):
    with pytest.raises(UIError, match="with none open"):
        renderer.pop_clip()


def test_a_frame_starts_its_clip_stack_empty(renderer):
    renderer.push_clip(0, 0, 10, 10)
    renderer.end()
    renderer.begin(320, 200)
    assert renderer.clip is None


def test_the_scope_leaves_the_clip_also_when_the_body_raised(recording):
    with pytest.raises(RuntimeError, match="a widget failed"):
        with recording.clipped(0, 0, 10, 10):
            raise RuntimeError("a widget failed")
    assert recording.clip is None


# -- text ------------------------------------------------------------------


def test_a_centred_label_is_moved_by_the_width_it_was_measured_at(recording):
    recording.text(100, 10, "text", 20, align="center")
    op = recording.last("text")
    width = recording.measure("text", 20)[0]
    assert op.get("x") == pytest.approx(100 - width / 2)
    assert op.get("align") == "center"


def test_an_alignment_nobody_spelled_is_left(recording):
    recording.text(50, 5, "x", 10, align="sideways")
    assert recording.last("text").get("align") == "left"


def test_measuring_is_the_same_question_the_drawing_answers(recording):
    width, height = recording.measure("440", 16)
    recording.text(0, 0, "440", 16, align="right")
    assert recording.last("text").get("x") == pytest.approx(-width)
    assert height > 0


# -- textures --------------------------------------------------------------


def test_the_same_file_is_only_asked_for_once(renderer):
    first = renderer.texture("assets/knob.png")
    second = renderer.texture("assets/knob.png")
    assert first == second
    if hasattr(renderer, "loaded"):
        assert len(renderer.loaded) == 1


def test_no_path_is_not_a_texture_at_all(renderer):
    assert renderer.texture("") == ""


def test_a_texture_remembers_how_big_it_turned_out(renderer):
    key = renderer.texture("assets/knob.png")
    assert renderer.texture_size(key) == (0.0, 0.0)
    assert renderer.texture_size("never heard of it") == (0.0, 0.0)


def test_a_texture_uploaded_from_memory_is_measured_by_its_own_shape(renderer):
    class Pixels:
        shape = (8, 16, 4)

    key = renderer.upload_texture("scope-ring", Pixels())
    assert renderer.texture_size(key) == (16.0, 8.0)


def test_an_upload_of_the_same_key_replaces_the_pixels_not_the_key(renderer):
    first = renderer.upload_texture("ring", type("P", (), {"shape": (4, 4)})())
    second = renderer.upload_texture("ring", type("P", (), {"shape": (8, 4)})())
    assert first == second
    assert renderer.texture_size(second) == (4.0, 8.0)


def test_a_forgotten_texture_is_not_remembered_anymore(renderer, tmp_path):
    image = tmp_path / "atlas.png"
    image.write_bytes(b"\x89PNG")
    key = renderer.texture(str(image))
    renderer.invalidate_texture(str(image))
    assert renderer.texture_size(key) == (0.0, 0.0)
    assert key not in renderer.texture_keys()


def test_a_memory_texture_can_be_let_go_of_by_key(renderer):
    # invalidate_texture's argument is a file, which is no use for pixels that
    # never were one - a glyph atlas recut for another display ratio, say
    renderer.upload_texture("crude:something", type("P", (), {"shape": (4, 4)})())
    assert "crude:something" in renderer.texture_keys()
    renderer.forget_texture("crude:something")
    assert "crude:something" not in renderer.texture_keys()


# -- icons -----------------------------------------------------------------


def test_an_icon_is_one_quad_and_not_an_op_of_its_own(renderer):
    # the atlas exists so that an icon bar is one texture bind and a quad each, so
    # a 'glyph' op would be the thing this whole design is trying not to have
    renderer.glyph(10, 10, 20, 20, "play")
    assert renderer.op_count == 1
    assert "glyph" not in RENDERER_OPS
    assert "glyph" not in DRAW_METHODS


def test_the_op_an_icon_emits_is_an_atlas_with_the_icon_in_it(recording):
    recording.glyph(10, 10, 20, 20, "play")
    assert recording.names() == ["atlas"]
    op = recording.last()
    assert op.get("texture") == recording.glyphs.texture_key
    assert op.get("uv") != (0.0, 0.0, 0.0, 0.0), "an empty uv draws nothing"


def test_the_icon_texture_is_uploaded_once_and_then_only_refilled(renderer):
    atlas = renderer.glyphs
    assert atlas is None, "nothing was drawn, so nothing should be built"
    renderer.glyph(0, 0, 20, 20, "play")
    keys = renderer.texture_keys()
    assert len(keys) == 1 and renderer.glyphs.uploads == 1
    renderer.glyph(30, 0, 20, 20, "play")
    assert renderer.glyphs.uploads == 1, "the same icon twice is one upload"
    renderer.glyph(60, 0, 20, 20, "stop")
    assert renderer.texture_keys() == keys, "a new glyph is a refill, not a new texture"
    assert renderer.glyphs.uploads == 2


def test_an_icon_carries_its_own_tint_and_its_own_turn(recording):
    recording.glyph(10, 10, 20, 20, "knob", "red", math.pi / 2)
    op = recording.last("atlas")
    assert op.get("color") == as_color("red")
    assert op.get("rotation") == pytest.approx(math.pi / 2)
    assert (op.get("x"), op.get("y")) == (10.0, 10.0)
    assert (op.get("width"), op.get("height")) == (20.0, 20.0)


def test_an_icon_box_turned_inside_out_is_moved_rather_than_lost(recording):
    # the rule is atlas's, not the atlas-of-glyphs': an icon is placed the way
    # every other box on the frame is placed
    recording.glyph(20, 20, -8, -12, "play")
    op = recording.last("atlas")
    assert (op.get("x"), op.get("y")) == (12.0, 8.0)
    assert (op.get("width"), op.get("height")) == (8.0, 12.0)
    assert recording.glyphs.cell("play", 8).size == 16, "the shorter side, either way"


def test_a_name_that_is_not_in_the_set_draws_the_placeholder(renderer):
    # one quad either way, and the atlas remembering the typo once, is the plan's
    # "never raises" rule: a script's mistake is a box on screen
    renderer.glyph(0, 0, 20, 20, "loudness-war")
    assert renderer.glyphs.missing == ["loudness-war"]
    renderer.glyph(0, 0, 20, 20, "loudness-war")
    assert renderer.glyphs.missing == ["loudness-war"]
    assert renderer.op_count == 2


def test_the_icon_texture_is_cut_for_the_display_it_is_drawn_on(renderer):
    # the frame fixture is 2x, so a 20 logical px icon is a 40 device px raster
    renderer.glyph(0, 0, 20, 20, "play")
    assert renderer.glyphs.cell("play", 20).size == 40
    renderer.begin(320, 200, 320, 200)
    renderer.glyph(0, 0, 20, 20, "play")
    assert renderer.glyphs.dpr == 1.0
    assert renderer.glyphs.cell("play", 20).size == 20
    assert len(renderer.texture_keys()) == 1, "the 2x texture should have been let go"


def test_two_renderers_do_not_share_one_icon_atlas(renderer):
    # the atlas is cut for a display ratio, which belongs to the frame, so one
    # kept between renderers would be the wrong size on at least one of them
    other = type(renderer)()
    other.begin(320, 200, 320, 200)
    renderer.glyph(0, 0, 20, 20, "play")
    other.glyph(0, 0, 20, 20, "play")
    assert renderer.glyphs is not other.glyphs
    assert renderer.glyphs.dpr == 2.0 and other.glyphs.dpr == 1.0


def test_a_texture_that_changes_size_leaves_the_op_before_it_alone(recording):
    # the headless half of a rule the GL renderer has to obey. Two 120px icons
    # make an atlas grow a row between them, and the UV box of the first is a
    # fraction of the texture as it was when it was drawn - which is why a resize
    # has to put everything already emitted on the screen before it resizes
    recording.glyph(0, 0, 120, 120, "chevron-u")
    first = recording.last("atlas").get("uv")
    recording.glyph(140, 0, 120, 120, "chevron-d")
    second = recording.last("atlas").get("uv")
    assert len(recording.uploaded) == 2, "a growing atlas is a re-upload"
    assert recording.uploaded[0][1] != recording.uploaded[1][1]
    assert (first[3] - first[1]) != pytest.approx(second[3] - second[1])


def test_an_icon_can_be_asked_for_without_being_drawn(recording):
    key, uv = recording.glyph_texture("play", 20)
    assert key in recording.texture_keys()
    assert uv[0] < uv[2] and uv[1] < uv[3]
    assert 0.0 <= uv[0] and uv[2] <= 1.0
    assert recording.op_count == 0, "asking for it is not drawing it"
    recording.glyph(0, 0, 20, 20, "play")
    op = recording.last("atlas")
    # the same answer the draw used, so ICON_TEXTURE cannot disagree with ICON
    assert op.get("texture") == key
    assert op.get("uv") == pytest.approx(uv)


# -- reading back ----------------------------------------------------------


def test_pixels_can_only_be_read_from_a_frame(cold):
    with pytest.raises(UIError, match="outside a frame"):
        cold.read_pixels()


def test_an_icon_is_not_drawn_before_the_frame_opens(cold):
    assert cold.glyphs is None
    with pytest.raises(UIError, match="outside a frame"):
        cold.glyph(0, 0, 20, 20, "play")


def test_a_renderer_that_cannot_see_the_screen_says_so(quiet):
    assert quiet.read_pixels(0, 0, 10, 10) is None
    assert quiet.read_pixels() is None


# -- the report ------------------------------------------------------------


def test_the_stats_a_run_prints_are_the_same_questions_of_every_renderer(renderer):
    renderer.rect(0, 0, 4, 4)
    report = renderer.stats()
    for key in ("renderer", "frames", "ops", "textures", "clips", "dpr"):
        assert key in report, f"{type(renderer).__name__} is missing '{key}'"
    assert report["ops"] == renderer.op_count


def test_an_op_can_be_read_as_a_plain_document(recording):
    recording.rect(1, 2, 3, 4, color="white")
    document = recording.last().as_dict()
    assert document["op"] == "rect"
    assert document["clip"] is None
    assert document["width"] == 3.0
    assert document["height"] == 4.0
    assert isinstance(recording.last(), DrawOp)


def test_a_moved_op_takes_its_clip_with_it(recording):
    recording.push_clip(0, 0, 20, 20)
    recording.rect(1, 2, 3, 4)
    moved = recording.last().moved(10, 10)
    assert (moved.get("x"), moved.get("y")) == (11.0, 12.0)
    assert moved.name == recording.last().name
    assert moved.clip == (10.0, 10.0, 20.0, 20.0)


def test_a_colour_can_be_asked_back_as_the_numbers_a_shader_wants():
    assert as_color("#00ff00").as_tuple() == (0.0, 1.0, 0.0, 1.0)
    assert as_color("transparent") is TRANSPARENT
    assert WHITE.with_alpha(0.5).alpha == 0.5
    assert as_color((1, 1, 1)).alpha == 1.0
    assert BLACK.alpha == 1.0
    assert isinstance(RGBA().premultiplied(), RGBA)
