"""The GL renderer on a real context: what it draws, and where it lands.

Everything in the rest of ``tests/ui`` is paper. This file is the answer to the
one question paper cannot ask - *do the pixels come out where the widget said
they would* - and it is therefore marked ``requires_gl``, deselected by the
default run and skipped, not failed, on a machine that cannot open a window.

The window is invisible, so a test run never flashes anything on screen, and the
asserts are made by reading the framebuffer back: the y-flip, the device pixel
ratio, the scissor that belongs to a clipped batch, the SDF that makes a circle
round, where an icon's own pixels land, and the one rule a batched renderer owes
its callers - that a pixel read half way through a frame reports *this* frame.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pytest

from crude.ui.base import DRAW_METHODS, DrawOp, RendererUnavailable

pytestmark = pytest.mark.requires_gl

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)


@pytest.fixture(scope="module")
def display():
    """One invisible window and its context, for the whole file."""
    from crude.ui.gl import open_display, window_available

    if not window_available():
        pytest.skip("this machine has no GLFW, so there is no context to draw in")
    try:
        made = open_display(320, 240, "crude test", visible=False)
    except Exception as exc:
        pytest.skip(f"this machine will not hand out a GL context: {exc}")
    yield made
    made.close()


@pytest.fixture
def renderer(display):
    """A fresh renderer in the window's context, closed again afterwards."""
    from crude.ui.gl import make_renderer

    made = make_renderer()
    yield made
    made.close()


@pytest.fixture
def frame(display, renderer):
    """A renderer already begun on the window's whole area."""
    return begin(display, renderer)


def begin(display, renderer):
    """Begin a frame on a renderer that is not the display's own.

    Every test gets a fresh one, because the counters and the caches it is asked
    about are its own, so the two sizes have to be handed over by hand - exactly
    what :meth:`Display.frame` does for the renderer a display made.
    """
    width, height = display.window.size()
    fb_width, fb_height = display.window.framebuffer_size()
    renderer.begin(width, height, fb_width, fb_height)
    return renderer


def spot(renderer, x: float, y: float) -> Tuple:
    """The RGB of one logical pixel, read back."""
    plane = renderer.read_pixels(x, y, 1, 1)
    return tuple(int(value) for value in plane[0, 0][:3])


def lit(renderer, x: float, y: float) -> bool:
    red, green, blue = spot(renderer, x, y)
    return red > 200 and green > 200 and blue > 200


def dark(renderer, x: float, y: float) -> bool:
    red, green, blue = spot(renderer, x, y)
    return red < 30 and green < 30 and blue < 30


def strong_spot(renderer, name: str, x: float, y: float,
                box: float) -> Tuple[float, float]:
    """One logical pixel that is certainly on an icon's own solid ink.

    Aimed from the raster the atlas made rather than guessed at from the path, so
    the assertion is about where the pixels landed at whatever display ratio the
    window happens to have, and not about somebody's arithmetic in this file.
    """
    cell = renderer.glyphs.cell(name, box)
    cover = renderer.glyphs.pixels(name, box)[..., 3]
    rows, columns = np.nonzero(cover >= 250)
    assert len(rows), f"{name} has no solid ink to aim at"
    middle_row, middle_column = rows.mean(), columns.mean()
    index = int(np.argmin((rows - middle_row) ** 2
                          + (columns - middle_column) ** 2))
    ratio = float(box) / cell.size
    return (x + (int(columns[index]) + 0.5) * ratio,
            y + (int(rows[index]) + 0.5) * ratio)


# -- the context ------------------------------------------------------------

def test_the_context_is_only_touched_when_a_frame_begins(renderer, display):
    assert renderer.stats()["gl"] is False
    begin(display, renderer)
    assert renderer.stats()["gl"] is True
    renderer.end()


def test_a_frame_of_every_op_in_the_contract_is_drawn(frame):
    frame.clear("#000000")
    frame.rect(10, 10, 40, 30, "white")
    frame.quad((60, 10), (90, 10), (90, 40), (60, 40), "white")
    frame.line((10, 60), (60, 80), 3, "white")
    frame.circle(90, 70, 12, "white")
    frame.arc(120, 70, 20, 5, start=0.2, end=2.4, color="white")
    frame.polyline([(10, 100), (40, 130), (80, 100)], 4, "white")
    frame.polygon([(100, 100), (140, 120), (110, 150)], "white")
    frame.text(10, 160, "crude", 18, "white")
    key = frame.upload_texture("plane", np.full((8, 8, 4), 255, np.uint8))
    frame.atlas(160, 160, 40, 40, key)
    ops = frame.end()

    assert ops == len(DRAW_METHODS)
    assert frame.last_triangles > 0
    assert frame.last_batches >= 1


def test_an_op_the_renderer_does_not_know_is_refused_not_ignored(frame):
    with pytest.raises(RendererUnavailable):
        frame.emit(DrawOp("wobble", {}))


# -- where the pixels land ---------------------------------------------------

def test_the_origin_is_the_top_left_and_y_grows_down(frame):
    frame.clear("#000000")
    frame.rect(0, 0, 20, 20, "white")
    assert lit(frame, 5, 5)
    assert dark(frame, 300, 220)
    assert dark(frame, 5, 220)


def test_a_logical_pixel_is_the_same_thing_a_widget_placed(frame):
    frame.clear("#000000")
    frame.rect(150, 100, 20, 20, "white")
    assert lit(frame, 160, 110)
    assert dark(frame, 145, 110)
    assert dark(frame, 175, 95)


def test_a_circle_is_round_because_it_is_a_distance_field(frame):
    frame.clear("#000000")
    frame.circle(160, 120, 40, "white")
    assert lit(frame, 160, 120)                      # the middle
    assert lit(frame, 160, 120 - 38)                 # almost the full radius up
    assert dark(frame, 160, 120 - 44)                # and past it
    # the diagonal at 45 degrees is at radius 40 * 0.707 from each edge
    assert dark(frame, 160 + 35, 120 + 35)


def test_a_rounded_box_is_a_box_with_soft_corners(frame):
    frame.clear("#000000")
    frame.rect(100, 80, 60, 40, "white", radius=20)
    assert lit(frame, 130, 100)                     # the middle of the box
    assert lit(frame, 130, 82)                      # the top edge, inside the caps
    assert dark(frame, 101, 81)                     # the corner it rounded away


def test_a_line_is_drawn_where_its_two_endpoints_are(frame):
    frame.clear("#000000")
    frame.line((20, 200), (300, 200), 4, "white")
    assert lit(frame, 160, 200)
    assert lit(frame, 20, 200)                      # the cap reaches the endpoint
    assert dark(frame, 160, 190)


# -- clipping, batching and reading back ------------------------------------

def test_a_clipped_batch_is_cut_to_the_rect_it_was_drawn_inside(frame):
    frame.clear("#000000")
    with frame.clipped(100, 50, 40, 40):
        frame.rect(0, 0, 320, 240, "white")
    # and the shapes drawn after the clip closed are cut by nothing at all: the
    # scissor is per batch, so a clip has to end one
    frame.rect(0, 0, 60, 120, "white")
    assert lit(frame, 110, 60)
    assert dark(frame, 50, 130)
    assert lit(frame, 50, 60)
    assert dark(frame, 150, 60)
    assert dark(frame, 110, 100)


def test_a_pixel_read_draws_the_frame_it_was_asked_inside(frame):
    # a batch still in a Python list has not reached the GPU, so the renderer has
    # to put it there before reading - otherwise this would be last frame's black
    frame.clear("#000000")
    frame.rect(20, 20, 30, 30, "white")
    assert lit(frame, 30, 30)
    assert frame.last_batches >= 1


def test_the_pixels_read_are_the_size_of_the_rect_asked_for(frame):
    frame.clear("#000000")
    plane = frame.read_pixels(10, 10, 20, 30)
    assert plane.shape == (30, 20, 4)


def test_a_device_pixel_ratio_splits_the_logical_size_from_the_framebuffer(
        display, renderer):
    renderer.begin(160, 120, 320, 240)
    renderer.clear("#000000")
    renderer.rect(0, 0, 20, 20, "white")
    assert renderer.dpr == 2.0
    plane = renderer.read_pixels(0, 0, 10, 10)
    assert plane.shape == (20, 20, 4)               # device pixels, both ways
    assert int(plane[0, 0, 0]) > 200
    renderer.end()


def test_a_batch_is_one_draw_call_and_a_change_of_texture_starts_another(frame):
    plane = np.full((4, 4, 4), 255, np.uint8)
    first = frame.upload_texture("first", plane)
    second = frame.upload_texture("second", plane)
    frame.clear("#000000")
    frame.atlas(10, 10, 20, 20, first)
    frame.atlas(40, 10, 20, 20, second)
    frame.atlas(70, 10, 20, 20, first)
    frame.end()
    assert frame.last_batches >= 3


# -- text --------------------------------------------------------------------

def test_text_is_measured_baked_and_drawn_once_per_string(frame):
    width, height = frame.measure("crude", 24)
    assert width > 0 and height > 0
    frame.clear("#000000")
    frame.text(10, 10, "crude", 24, "white")
    frame.text(10, 60, "crude", 24, "white")
    frame.end()
    assert frame.stats()["text_bakes"] == 1


def test_a_centred_label_is_measured_rather_than_guessed(frame):
    frame.clear("#000000")
    width, height = frame.measure("WWWW", 24)
    frame.text(160, 40, "WWWW", 24, "white", align="center")
    row = 40 + height / 2
    # the ink, not the box: what a centred label owes the middle it was given
    ink = [column for column in range(int(160 - width), int(160 + width) + 1)
           if lit(frame, column + 0.5, row)]
    assert ink, "nothing was drawn, so nothing was centred"
    assert (min(ink) + max(ink)) / 2 == pytest.approx(160, abs=2)
    # the box a measure returns carries the side bearings the ink does not, so
    # what it has to agree about is the middle, and the width being no more
    assert width * 0.5 <= max(ink) - min(ink) <= width


def test_a_bigger_size_is_a_bigger_picture(frame):
    small = frame.measure("crude", 12)[0]
    large = frame.measure("crude", 36)[0]
    assert large > small * 2


# -- textures and rings ------------------------------------------------------

def test_a_coverage_plane_becomes_a_white_texture_with_that_alpha(frame):
    cover = np.zeros((16, 16), dtype=np.uint8)
    cover[4:12, 4:12] = 255
    key = frame.upload_texture("cover", cover)
    frame.clear("#000000")
    frame.atlas(0, 0, 160, 160, key, color="#ff0000")
    assert spot(frame, 80, 80)[:3] == (255, 0, 0)    # tinted, and covered
    assert spot(frame, 10, 10) == BLACK              # the empty corner


def test_a_scope_ring_is_a_picture_on_the_screen(frame):
    from crude.ui.ring import RingTexture

    ring = RingTexture(40.0, band=4.0, deflection=12.0, segments=180,
                       key="scope", scale=1.0)
    ring.set_values([1.0])
    frame.clear("#000000")
    ring.draw_centred(frame, 160, 120, colour="#ffffff")
    # the trace was pushed out by the full-scale deflection: 52 from the middle,
    # and nothing at the rest radius it would have sat at with no signal
    assert lit(frame, 160 + 52, 120)
    assert dark(frame, 160 + 40, 120)
    assert dark(frame, 160, 120)


# -- icons -------------------------------------------------------------------

def test_an_icon_lands_the_right_way_up_on_the_screen(frame):
    # the atlas's first row of pixels is uploaded as t = 0 and the frame is drawn
    # with y growing down, so the two conventions have to agree or every icon in
    # the program arrives mirrored. Two icons that are each other's mirror make
    # that a thing the screen answers, not a thing a reader has to check
    frame.clear("#000000")
    frame.glyph(20, 40, 120, 120, "chevron-u")
    frame.glyph(180, 40, 120, 120, "chevron-d")
    up = frame.read_pixels(20, 40, 120, 120)[..., 0].astype(np.int16)
    down = frame.read_pixels(180, 40, 120, 120)[..., 0].astype(np.int16)
    assert up.max() > 200 and down.max() > 200, "nothing was drawn"
    assert np.mean(np.abs(up - up[::-1])) > 20, "the glyph is a symmetric blob"
    assert np.mean(np.abs(up - down[::-1])) < 8, "an icon came out mirrored"
    assert lit(frame, *strong_spot(frame, "chevron-u", 20, 40, 120))


def test_an_atlas_that_grew_mid_frame_keeps_the_box_it_already_drew(frame):
    # the second icon makes the texture taller, which changes what the first icon's
    # UV numbers mean. The renderer has to put the first one on the screen before
    # the resize takes the ground away from under it, or its box ends up holding
    # both glyphs squeezed into one - which is what this reads back
    frame.clear("#000000")
    frame.glyph(20, 40, 120, 120, "chevron-u")
    assert frame.glyphs.stats()["growths"] == 0
    frame.glyph(180, 40, 120, 120, "chevron-d")
    assert frame.glyphs.stats()["growths"] > 0, "nothing grew, so nothing was tested"
    up = frame.read_pixels(20, 40, 120, 120)[..., 0]
    rows = np.nonzero((up > 200).any(axis=1))[0]
    assert len(rows), "the first icon vanished with the resize"
    span = (int(rows.max()) - int(rows.min()) + 1) / up.shape[0]
    # one chevron is a third of its box tall; two stacked fill most of it
    assert span < 0.55, f"the box reads {span:.0%} tall, so it holds two glyphs"
    assert lit(frame, *strong_spot(frame, "chevron-u", 20, 40, 120))
    assert lit(frame, *strong_spot(frame, "chevron-d", 180, 40, 120))


def test_an_icon_is_coloured_by_the_draw_and_not_by_the_texture(frame):
    frame.clear("#000000")
    frame.glyph(40, 40, 100, 100, "play", "#ff0000")
    assert spot(frame, *strong_spot(frame, "play", 40, 40, 100)) == (255, 0, 0)
    # the same pixels from the same atlas cell, tinted the other way: a colour is
    # one multiply, which is why one texture can carry a whole toolbar
    frame.glyph(160, 40, 100, 100, "play", "#00ff00")
    assert spot(frame, *strong_spot(frame, "play", 160, 40, 100)) == (0, 255, 0)


def test_four_icons_are_four_quads_out_of_one_texture_in_one_batch(frame):
    frame.clear("#000000")
    for step, name in enumerate(("play", "stop", "knob", "folder")):
        frame.glyph(10 + step * 60, 20, 50, 50, name)
    assert frame.end() == 5
    assert frame.last_triangles == 24, "six vertices each, and no more"
    # the whole point of an atlas: the icon bar is one state change
    assert frame.last_batches == 1
    assert len(frame.texture_keys()) == 1


def test_a_texture_key_the_renderer_does_not_have_draws_nothing(frame):
    frame.clear("#000000")
    frame.atlas(10, 10, 40, 40, "there is no such image")
    assert dark(frame, 30, 30)
    assert "there is no such image" in frame.missing
    assert frame.stats()["missing_textures"] == 1


def test_an_image_file_is_read_and_can_be_drawn(frame, tmp_path):
    from PIL import Image

    path = str(tmp_path / "tile.png")
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(path)
    key = frame.texture(path)
    frame.clear("#000000")
    frame.atlas(10, 10, 32, 32, key)
    assert spot(frame, 20, 20)[:3] == (255, 0, 0)
    assert frame.texture_size(key) == (8.0, 8.0)


def test_closing_a_renderer_lets_go_and_the_next_frame_still_works(display,
                                                                   frame):
    frame.clear("#000000")
    frame.rect(10, 10, 20, 20, "white")
    frame.end()
    frame.close()
    assert frame.stats()["gl"] is False
    begin(display, frame)
    frame.clear("#000000")
    frame.rect(10, 10, 20, 20, "white")
    assert lit(frame, 20, 20)
