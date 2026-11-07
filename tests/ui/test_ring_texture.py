"""The ring a scope is drawn from: a texture that numpy redraws, no display needed.

Two things are being pinned here that a GL context would never notice. The first
is the *picture*: whether the trace lands where the signal says it should, at the
right angle, at the right radius, at whatever device pixel ratio the window
happens to have. The second is the *discipline*: that a frame of silence costs no
upload, that a texture is refilled rather than reallocated, and that the whole
thing reaches the renderer as one atlas op - which is the only reason a thousand
samples can be in a build.

Both are answered against :class:`~crude.ui.recording.RecordingRenderer` and
plain numbers, so the default run covers a scope end to end.

``coverage()`` is indexed ``[row, column]`` - y then x - and the helpers below
read it the way a person reads a circle: an angle and a distance from the middle.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from crude.ui import NullRenderer, RecordingRenderer
from crude.ui.ring import (DEFAULT_BAND, DEFAULT_RADIUS, DEFAULT_SEGMENTS,
                           MAX_SIZE, RingTexture, resample)

TAU = math.tau


def pixel(ring: RingTexture, angle: float, distance: float) -> int:
    """How covered the device pixel at this angle and radius is, 0..255.

    ``distance`` is in logical pixels, which is what a caller thinks in; the
    texture is its own ``scale`` times bigger, so the multiplication happens here
    rather than in every test that asks.
    """
    size = ring.pixel_size[0]
    centre = size * 0.5
    away = distance * ring.scale
    column = int(round(centre + away * math.cos(angle) - 0.5))
    row = int(round(centre + away * math.sin(angle) - 0.5))
    return int(ring.coverage()[row, column])


def bright(ring: RingTexture, angle: float, distance: float) -> bool:
    return pixel(ring, angle, distance) > 200


def dark(ring: RingTexture, angle: float, distance: float) -> bool:
    return pixel(ring, angle, distance) == 0


@pytest.fixture
def ring() -> RingTexture:
    """A small ring, sized so the arithmetic in these tests stays readable."""
    return RingTexture(20.0, band=2.0, deflection=6.0, segments=64, key="ring")


# -- the box it needs ------------------------------------------------------


def test_a_ring_is_sized_to_hold_the_trace_at_its_furthest():
    ring = RingTexture(20.0, band=2.0, deflection=6.0)
    # radius plus full-scale deflection plus the trace's own half width, both sides
    assert ring.bounds == pytest.approx((56.0, 56.0))
    assert ring.size == 56.0


def test_the_default_deflection_leaves_room_for_the_trace_to_move():
    ring = RingTexture(40.0, band=4.0)
    assert ring.deflection == 8.0
    assert ring.bounds[0] == pytest.approx((40.0 + 8.0 + 4.0) * 2)


def test_the_texture_is_the_logical_box_at_the_device_ratio(ring):
    ring.set_scale(2.0)
    assert ring.pixel_size == (112, 112)
    # and the box a widget is placed in has not moved at all
    assert ring.bounds == pytest.approx((56.0, 56.0))


def test_a_ring_too_big_for_a_texture_loses_scale_not_geometry():
    ring = RingTexture(900.0, band=6.0, deflection=100.0, scale=4.0)
    assert ring.pixel_size[0] <= MAX_SIZE
    # the scale came back rather than the picture being cut: the trace still lands
    # where the signal puts it, at every angle, not only near the middle
    ring.set_values([1.0])
    assert bright(ring, 0.0, ring.radius + ring.deflection)
    assert dark(ring, 0.0, ring.radius)


def test_an_empty_ring_still_has_a_picture_to_upload(ring):
    assert ring.pixel_size[0] >= 1
    assert ring.coverage().shape == (ring.pixel_size[0], ring.pixel_size[1])


# -- the flat ring ---------------------------------------------------------


def test_silence_is_a_circle_at_the_radius_it_was_given(ring):
    for angle in (0.0, math.pi / 4, math.pi / 2, TAU - 0.1):
        assert bright(ring, angle, 20.0), angle


def test_the_trace_is_only_as_thick_as_it_was_told(ring):
    assert bright(ring, 0.0, 20.0)
    assert pixel(ring, 0.0, 21.0) > 0          # still on the edge of the band
    assert dark(ring, 0.0, 24.0)               # outside band plus feather
    assert dark(ring, 0.0, 16.0)


def test_nothing_outside_the_ring_is_covered_at_all(ring):
    size = ring.pixel_size[0]
    plane = ring.coverage()
    corner = int(plane[size - 1, size - 1])
    # the diagonal corner is further out than the whole box was built to reach
    assert corner == 0
    assert plane[0, size // 2] == 0


# -- the signal ------------------------------------------------------------


def test_a_full_scale_sample_pushes_the_trace_out_by_the_deflection(ring):
    ring.set_values([1.0])
    assert ring.target_radius(0.0) == pytest.approx(26.0)
    assert bright(ring, 0.0, 26.0)
    assert dark(ring, 0.0, 20.0)


def test_a_negative_sample_pulls_the_trace_in(ring):
    ring.set_values([-1.0])
    assert ring.target_radius(TAU / 2) == pytest.approx(14.0)
    assert bright(ring, TAU / 2, 14.0)


def test_the_first_sample_is_at_angle_zero_and_they_go_counter_clockwise(ring):
    ring.set_values([1.0, 0.0, -1.0, 0.0])
    assert ring.target_radius(0.0) == pytest.approx(26.0)
    assert ring.target_radius(TAU / 2) == pytest.approx(14.0)
    # the seam: the point before angle zero is the last sample, not a cliff
    assert ring.target_radius(TAU - 0.01) < 26.0


def test_a_sample_is_interpolated_between_its_neighbours(ring):
    ring.set_values([1.0, -1.0])
    # halfway between the two points is halfway between the two radii
    assert ring.target_radius(TAU / 4) == pytest.approx(20.0, abs=0.6)


def test_a_value_past_full_scale_is_clamped_rather_than_drawn_outside(ring):
    ring.set_values([9.0])
    assert ring.values[0] == 1.0
    assert ring.target_radius(0.0) == pytest.approx(26.0)


def test_a_range_other_than_a_waveform_is_the_callers_to_name(ring):
    ring.set_values([0.5], low=0.0, high=1.0)
    # half of a 0..1 level is the middle of the sweep, which is the rest radius
    assert ring.values[0] == pytest.approx(0.0)
    ring.set_values([1.0], low=0.0, high=1.0)
    assert ring.target_radius(0.0) == pytest.approx(26.0)


def test_a_level_goes_in_as_a_level(ring):
    ring.set_level(0.0)
    assert ring.target_radius(0.0) == pytest.approx(14.0)
    ring.set_level(1.0)
    assert ring.target_radius(0.0) == pytest.approx(26.0)


def test_a_device_that_has_not_answered_yet_is_silence():
    # segments matches the block, so each point reads the sample under it rather
    # than a mix of two neighbours
    ring = RingTexture(20.0, band=2.0, deflection=6.0, segments=4)
    ring.set_values([float("nan"), float("inf"), -float("inf"), 0.25])
    assert ring.values == (0.0, 0.0, 0.0, 0.25)
    assert None not in ring.values


def test_no_signal_at_all_is_the_same_as_silence(ring):
    for empty in ([], None, ()):
        ring.set_values([1.0])
        ring.set_values(empty)
        assert ring.values == tuple([0.0] * ring.segments)


def test_more_samples_than_the_ring_has_points_are_squeezed(ring):
    ring.set_values([0.0] * 512 + [1.0] * 512)
    assert len(ring.values) == ring.segments
    # the block still reads as a step: the first half in, the second half out
    assert set(ring.values[:ring.segments // 2]) == {0.0}
    assert set(ring.values[ring.segments // 2:]) == {1.0}


def test_fewer_samples_than_the_ring_has_points_are_stretched(ring):
    ring.set_values([1.0, -1.0])
    assert max(ring.values) == pytest.approx(1.0)
    assert min(ring.values) == pytest.approx(-1.0)


def test_a_numpy_block_from_an_audio_callback_goes_straight_in(ring):
    ring.set_values(np.linspace(-1.0, 1.0, 256))
    assert len(ring.values) == ring.segments
    assert ring.values[-1] == pytest.approx(1.0, abs=0.05)


def test_pushing_rolls_the_ring_and_puts_the_new_sample_first(ring):
    ring.clear()
    ring.push(1.0)
    assert ring.values[0] == 1.0
    assert ring.values[1] == 0.0
    ring.push(0.5)
    assert ring.values[:2] == (0.5, 1.0)


def test_a_push_past_full_scale_is_clamped_like_any_value(ring):
    ring.push(4.0)
    assert ring.values[0] == 1.0


def test_clearing_is_a_frame_of_silence_that_still_needs_uploading(ring):
    ring.set_values([1.0])
    ring.clear()
    assert ring.dirty is True
    assert set(ring.values) == {0.0}


# -- the resample the whole model stands on --------------------------------


def test_resample_closes_the_seam_so_a_ring_has_no_end():
    found = resample([0.0, 1.0, 0.0, -1.0], 8)
    assert found[0] == 0.0
    assert found[2] == 1.0
    assert found[6] == -1.0
    # the step into angle zero is the same size as the step out of it
    assert found[0] - found[7] == pytest.approx(found[1] - found[0])


@pytest.mark.parametrize("segments,expected", [(1, 1), (8, 8), (64, 64)])
def test_resample_answers_exactly_the_count_asked_for(segments, expected):
    assert len(resample([1.0, 2.0, 3.0], segments)) == expected


def test_one_sample_fills_the_ring_because_a_flat_trace_is_still_a_trace():
    assert set(resample([0.75], 16)) == {0.75}


def test_nothing_resampled_is_a_flat_zero_ring():
    assert set(resample([], 16)) == {0.0}


# -- one upload per change --------------------------------------------------


def test_a_ring_uploads_its_first_frame_and_needs_no_more(ring):
    recorder = RecordingRenderer()
    recorder.begin(200, 200)
    assert ring.upload(recorder) == "ring"
    assert ring.upload(recorder) == "ring"
    assert len(recorder.uploaded) == 1


def test_a_ring_that_changed_uploads_again(ring):
    recorder = RecordingRenderer()
    recorder.begin(200, 200)
    ring.upload(recorder)
    ring.push(1.0)
    ring.upload(recorder)
    assert len(recorder.uploaded) == 2
    assert ring.uploads == 2


def test_a_ring_that_meets_a_new_renderer_tells_it_again(ring):
    ring.upload(RecordingRenderer())
    recorder = RecordingRenderer()
    recorder.begin(200, 200)
    # nothing has moved since the first upload, but this renderer has never been
    # told the key, so a quiet frame is not a reason to stay silent
    ring.dirty = False
    ring.upload(recorder)
    assert len(recorder.uploaded) == 1


def test_the_renderer_is_told_how_big_the_ring_is(ring):
    recorder = RecordingRenderer()
    recorder.begin(200, 200)
    ring.upload(recorder)
    assert recorder.texture_size("ring") == (56.0, 56.0)


def test_a_headless_run_can_carry_a_ring_too(ring):
    quiet = NullRenderer()
    quiet.begin(200, 200)
    ring.draw(quiet, 10, 10)
    assert quiet.count("atlas") == 1
    assert quiet.count() == 1


# -- the one op it takes to draw -------------------------------------------


def test_drawing_a_ring_is_one_atlas_op(ring):
    recorder = RecordingRenderer()
    recorder.begin(200, 200)
    ring.draw(recorder, 10, 20)
    assert recorder.names() == ["atlas"]
    params = recorder.last().params
    assert params["texture"] == "ring"
    assert (params["x"], params["y"]) == (10.0, 20.0)
    assert (params["width"], params["height"]) == pytest.approx((56.0, 56.0))
    assert params["uv"] == (0.0, 0.0, 1.0, 1.0)


def test_a_ring_tints_itself_at_draw_time_not_in_the_texture(ring):
    recorder = RecordingRenderer()
    recorder.begin(200, 200)
    ring.draw(recorder, 0, 0, colour="#ff0000")
    assert str(recorder.last().params["color"]) == "#ff0000ff"
    plane = ring.pixels()
    assert set(plane[:, :, 0].ravel().tolist()) == {255}


def test_a_ring_can_be_scaled_down_to_the_box_a_layout_got_it(ring):
    recorder = RecordingRenderer()
    recorder.begin(200, 200)
    ring.draw(recorder, 0, 0, width=28.0, height=28.0)
    assert recorder.last().params["width"] == 28.0


def test_a_centred_ring_lands_where_the_widget_says_its_middle_is(ring):
    recorder = RecordingRenderer()
    recorder.begin(200, 200)
    ring.draw_centred(recorder, 100, 50)
    params = recorder.last().params
    assert (params["x"], params["y"]) == pytest.approx((72.0, 22.0))


def test_drawing_uploads_when_it_has_to(ring):
    recorder = RecordingRenderer()
    recorder.begin(200, 200)
    ring.draw(recorder, 0, 0)
    ring.draw(recorder, 0, 0)
    assert len(recorder.uploaded) == 1
    assert recorder.count("atlas") == 2


def test_the_pixels_are_a_white_plane_with_the_coverage_for_alpha(ring):
    plane = ring.pixels()
    assert plane.shape == (ring.pixel_size[0], ring.pixel_size[1], 4)
    assert plane.dtype == np.uint8
    assert np.array_equal(plane[:, :, 3], ring.coverage())
    assert plane[ring.pixel_size[0] // 2, ring.pixel_size[0] // 2, 3] == 0


def test_an_upload_handed_a_plane_is_still_a_square_texture(ring):
    recorder = RecordingRenderer()
    recorder.begin(200, 200)
    recorder.upload_texture("other", ring.coverage())
    assert recorder.texture_size("other") == ring.pixel_size


# -- a resized widget ------------------------------------------------------


def test_a_wider_radius_rebuilds_the_picture(ring):
    before = ring.pixel_size
    ring.set_shape(radius=40.0)
    assert ring.pixel_size[0] > before[0]
    assert ring.dirty is True
    assert bright(ring, 0.0, 40.0)


def test_a_resize_that_did_not_change_anything_touches_nothing(ring):
    ring.upload(RecordingRenderer())
    before = ring.pixel_size
    ring.set_shape(radius=20.0, band=2.0)
    assert ring.pixel_size == before
    assert ring.dirty is False


def test_fewer_points_resamples_what_was_already_measured(ring):
    ring.set_values([1.0, -1.0, 1.0, -1.0])
    ring.set_shape(segments=8)
    assert len(ring.values) == 8
    assert max(ring.values) == pytest.approx(1.0, abs=0.3)


def test_a_band_of_a_fraction_of_a_pixel_is_still_a_band(ring):
    ring.set_shape(band=0.1)
    assert ring.band == 0.5
    assert bright(ring, 0.0, ring.radius)


def test_two_uploads_at_two_scales_are_two_sizes(ring):
    recorder = RecordingRenderer()
    recorder.begin(200, 200)
    ring.upload(recorder)
    ring.set_scale(1.5)
    ring.upload(recorder)
    assert [size for _, size in recorder.uploaded] == [(56.0, 56.0), (84.0, 84.0)]


def test_a_scale_of_nothing_is_one_rather_than_a_ring_of_no_pixels():
    ring = RingTexture(10.0, scale=0.0)
    assert ring.scale == 0.01
    ring.set_scale(0.0)
    assert ring.pixel_size[0] >= 1


# -- the name it is known by ------------------------------------------------


def test_every_ring_has_its_own_key_unless_the_caller_names_one():
    assert RingTexture().key != RingTexture().key
    assert RingTexture(key="scope").key == "scope"


def test_the_defaults_are_the_ones_the_widget_layer_will_ask_for():
    ring = RingTexture()
    assert ring.radius == DEFAULT_RADIUS
    assert ring.band == DEFAULT_BAND
    assert ring.segments == DEFAULT_SEGMENTS


def test_a_ring_says_what_it_is_holding(ring):
    said = str(ring)
    assert "ring" in said
    assert "needs upload" in said
    ring.upload(RecordingRenderer())
    assert "holding upload" in str(ring)
