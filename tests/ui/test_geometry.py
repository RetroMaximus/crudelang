"""The shape maths, on its own, with no renderer and no display in sight.

The plan puts the placement work in the "pure-math tests" column: anchors, DPI
rounding, clip rects and winding can all be wrong in a way a GL context would
never notice and a layout assertion would. So they are judged here, once, and the
renderers are only allowed to obey.
"""

from __future__ import annotations

import math

import pytest

from crude.ui import geometry
from crude.ui.geometry import (
    ALIGNMENTS, ARC_TOLERANCE, FULL_SWEEP, TAU,
    align_x, align_x_offset, alignment, arc_points, arc_segments, arc_sweep,
    capsule, circle_bounds, clamp, clip_overlap, polygon_bounds, polygon_fan,
    polyline_pairs, rect_corners, scale_of, tapered_quad, text_bounds, wrap_angle,
)


# -- the frame's scale ----------------------------------------------------


@pytest.mark.parametrize("value,low,high,expected", [
    (5, 0, 10, 5),
    (-1, 0, 10, 0),
    (11, 0, 10, 10),
    # the bounds the wrong way round are still the same bounds
    (5, 10, 0, 5),
    (-1, 10, 0, 0),
    (11, 10, 0, 10),
])
def test_clamp_keeps_a_number_inside_whichever_way_the_bounds_came(
        value, low, high, expected):
    assert clamp(value, low, high) == expected


@pytest.mark.parametrize("framebuffer,logical,expected", [
    (640, 320, 2.0),
    (320, 320, 1.0),
    (480, 320, 1.5),
    # a renderer that has not been told either size cannot divide by nothing
    (0, 320, 1.0),
    (640, 0, 1.0),
    (None, None, 1.0),
])
def test_the_device_ratio_comes_from_the_two_sizes_or_is_one(
        framebuffer, logical, expected):
    assert scale_of(framebuffer, logical) == expected


# -- boxes -----------------------------------------------------------------


def test_an_unrotated_box_gives_its_corners_clockwise_from_the_top_left():
    assert rect_corners(10, 20, 30, 40) == (
        (10, 20), (40, 20), (40, 60), (10, 60))


def test_rotation_turns_the_box_about_its_own_centre():
    corners = rect_corners(0, 0, 20, 10, math.pi / 2)
    # a quarter turn: the long side lies where the short one was, about (10, 5)
    assert corners[0] == pytest.approx((15.0, -5.0))
    assert corners[2] == pytest.approx((5.0, 15.0))


def test_rotation_moves_the_corners_but_not_the_centre_or_the_diagonal():
    corners = rect_corners(4, 6, 30, 40, 1.1)
    cx = sum(point[0] for point in corners) / 4
    cy = sum(point[1] for point in corners) / 4
    assert (cx, cy) == pytest.approx((19.0, 26.0))
    diagonal = math.dist(corners[0], corners[2])
    assert diagonal == pytest.approx(math.hypot(30, 40))


# -- lines -----------------------------------------------------------------


def test_a_segment_becomes_a_capsule_of_its_own_length_and_angle():
    cx, cy, length, thickness, angle = capsule(0, 0, 30, 40, 6)
    assert (cx, cy) == (15, 20)
    assert length == pytest.approx(50)
    assert thickness == 6
    assert angle == pytest.approx(math.atan2(40, 30))


def test_a_segment_too_short_to_draw_answers_a_length_of_zero():
    # 0.3 px is under half the rounding of one device pixel at any sane DPI
    _, _, length, _, _ = capsule(0, 0, 0.2, 0.2, 4)
    assert length == 0.0


def test_a_line_of_no_thickness_is_not_a_capsule_at_all():
    _, _, length, thickness, _ = capsule(0, 0, 100, 0, 0)
    assert (length, thickness) == (0.0, 0.0)


def test_a_negative_thickness_is_read_as_none_rather_than_as_an_inside_out_line():
    assert capsule(0, 0, 10, 0, -3)[3] == 0.0


def test_a_taper_stands_off_the_segment_at_half_each_ends_width():
    p1, p2, p3, p4 = tapered_quad(0, 0, 100, 0, 4, 2)
    assert p1 == pytest.approx((0, 2))
    assert p2 == pytest.approx((100, 1))
    assert p3 == pytest.approx((100, -1))
    assert p4 == pytest.approx((0, -2))


def test_a_taper_that_is_not_tapering_is_the_same_quad_a_capsule_covers():
    p1, p2, p3, p4 = tapered_quad(10, 20, 10, 60, 4, 4)
    assert p1 == pytest.approx((8, 20))
    assert p3 == pytest.approx((12, 60))
    assert math.dist(p1, p4) == pytest.approx(4)


# -- angles and arcs -------------------------------------------------------


@pytest.mark.parametrize("angle,expected", [
    (0, 0),
    (TAU, 0.0),
    (-0.001, pytest.approx(TAU - 0.001)),
    (TAU + 1, 1.0),
    (-TAU, 0.0),
])
def test_an_angle_is_a_direction_so_it_folds_around(angle, expected):
    assert wrap_angle(angle) == expected


@pytest.mark.parametrize("start,end,expected", [
    (0, math.pi / 2, math.pi / 2),
    # an end behind the start is the long way round, not a negative arc
    (math.pi / 2, 0, pytest.approx(3 * math.pi / 2)),
    (1, 1, pytest.approx(0.0)),
    (0, 0, pytest.approx(0.0)),
    # the same direction twice is a ring, not nothing
    (0, TAU, pytest.approx(FULL_SWEEP)),
    (0, TAU + 1, pytest.approx(FULL_SWEEP)),
    (0, -TAU, pytest.approx(FULL_SWEEP)),
])
def test_an_arc_turns_counter_clockwise_from_where_it_starts(start, end, expected):
    assert arc_sweep(start, end) == expected


def test_the_usual_way_to_ask_for_a_ring_is_not_nothing():
    # arc()'s own defaults are 0 to TAU, so the default shape has to be a ring
    assert arc_sweep(0, TAU) > TAU * 0.99


def test_an_arc_that_closes_on_itself_would_have_a_segment_of_no_length():
    points = arc_points(0, 0, 20, 0, TAU)
    assert points[0] != points[-1]
    assert math.dist(points[0], points[-1]) < 0.01


def test_a_sweep_never_quite_closes_on_itself():
    # a last point landing on the first would make a segment of zero length
    assert arc_sweep(0, TAU) < TAU
    assert FULL_SWEEP < TAU


def test_a_big_circle_needs_fewer_segments_than_a_small_one_for_the_same_arc():
    # the same tolerance in pixels is a wider angle on a radius of 200 than on 20
    assert arc_segments(math.pi, tolerance=0.5, radius=20) < \
        arc_segments(math.pi, tolerance=0.5, radius=200)


def test_a_longer_sweep_needs_more_segments_than_a_short_one():
    assert arc_segments(math.pi / 4, radius=50) < arc_segments(math.pi, radius=50)


def test_a_looser_tolerance_needs_fewer_segments():
    assert arc_segments(math.pi, tolerance=0.1, radius=40) > \
        arc_segments(math.pi, tolerance=2.0, radius=40)


@pytest.mark.parametrize("sweep", [0.0001, 0.01, 0.2])
def test_the_shortest_arc_is_still_three_segments(sweep):
    assert arc_segments(sweep, radius=100) == geometry.MIN_ARC_SEGMENTS


def test_no_radius_stalls_a_frame_however_small_it_is():
    assert arc_segments(TAU, tolerance=0.0001, radius=0.01) <= 180
    assert arc_segments(TAU, tolerance=0.5, radius=1e9) <= 180


def test_a_sweep_of_nothing_needs_nothing():
    assert arc_segments(0) == 0
    assert arc_segments(-1) == 0


@pytest.mark.parametrize("radius,tolerance", [
    (0, 0.5), (-5, 0.5), (50, 0), (50, -1), (50, 100),
])
def test_a_measurement_that_cannot_be_trusted_falls_back_to_a_sane_step(
        radius, tolerance):
    # acos(1 - t/r) is only defined for 0 < t < r; anything else still has to
    # answer a number, because a shape that cannot be sampled is still asked for
    assert 3 <= arc_segments(math.pi, tolerance=tolerance, radius=radius) <= 180


def test_an_arc_is_sampled_with_both_its_ends_on_the_circle():
    points = arc_points(100, 100, 50, 0, math.pi / 2, segments=4)
    assert len(points) == 5
    assert points[0] == pytest.approx((150.0, 100.0))
    assert points[-1] == pytest.approx((100.0, 150.0), abs=1e-6)
    for point in points:
        assert math.dist(point, (100, 100)) == pytest.approx(50)


def test_an_arc_follows_the_sweep_a_renderer_was_given():
    # a 3/4 turn from 90 degrees: every point is on the circle and in between
    points = arc_points(0, 0, 10, math.pi / 2, math.pi, segments=8)
    angles = [math.atan2(y, x) % TAU for x, y in points]
    assert angles == sorted(angles)
    assert angles[0] == pytest.approx(math.pi / 2, abs=1e-9)


def test_a_circle_with_no_radius_or_no_sweep_has_no_points():
    assert arc_points(0, 0, 0, 0, TAU) == []
    assert arc_points(0, 0, -5, 0, TAU) == []
    assert arc_points(0, 0, 10, 1, 1) == []


def test_an_arcs_default_count_of_points_comes_from_its_own_tolerance():
    points = arc_points(0, 0, 40, 0, math.pi)
    assert len(points) == arc_segments(arc_sweep(0, math.pi),
                                       ARC_TOLERANCE, 40) + 1


# -- circles, polylines, polygons -----------------------------------------


def test_a_filled_circle_is_a_box_whose_radius_is_half_its_shorter_side():
    x, y, width, height = circle_bounds(30, 40, 12)
    assert (width, height) == (24.0, 24.0)
    assert (x, y) == (18.0, 28.0)
    assert min(width, height) / 2 == 12


def test_a_circle_of_no_radius_is_a_point_at_its_centre():
    assert circle_bounds(7, 8, 0) == (7.0, 8.0, 0.0, 0.0)


def test_a_polyline_of_two_points_is_one_pair_and_a_closed_one_is_two():
    points = [(0, 0), (5, 5)]
    assert polyline_pairs(points) == [((0, 0), (5, 5))]
    assert polyline_pairs(points, closed=True) == [
        ((0, 0), (5, 5)), ((5, 5), (0, 0))]


def test_a_polyline_that_already_closed_itself_is_not_joined_twice():
    points = [(0, 0), (5, 5), (0, 0)]
    assert polyline_pairs(points, closed=True) == [
        ((0, 0), (5, 5)), ((5, 5), (0, 0))]


def test_a_line_needs_two_points_and_nothing_less():
    assert polyline_pairs([]) == []
    assert polyline_pairs([(1, 1)]) == []
    assert polyline_pairs([(1, 1)], closed=True) == []


def test_a_polygon_is_fanned_from_its_first_vertex():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert polygon_fan(square) == [
        ((0, 0), (10, 0), (10, 10)),
        ((0, 0), (10, 10), (0, 10)),
    ]


def test_a_fan_has_exactly_two_fewer_triangles_than_the_shape_has_points():
    for count in (3, 4, 7, 20):
        points = [(math.cos(index), math.sin(index)) for index in range(count)]
        assert len(polygon_fan(points)) == count - 2


def test_a_fan_keeps_the_winding_it_was_handed():
    clockwise = [(0, 0), (0, 10), (10, 10)]
    anti = [(0, 0), (10, 10), (0, 10)]
    assert polygon_fan(clockwise)[0] == (clockwise[0], clockwise[1], clockwise[2])
    assert polygon_fan(anti)[0] == (anti[0], anti[1], anti[2])


def test_a_shape_with_no_area_has_no_triangles():
    assert polygon_fan([]) == []
    assert polygon_fan([(0, 0), (1, 1)]) == []


def test_the_box_around_points_is_the_smallest_one_that_holds_them():
    assert polygon_bounds([(-3, 5), (2, -1), (0, 3)]) == (-3.0, -1.0, 5.0, 6.0)


def test_no_points_have_no_box_rather_than_an_infinitely_large_one():
    assert polygon_bounds([]) == (0.0, 0.0, 0.0, 0.0)


# -- clipping -------------------------------------------------------------


def test_two_clips_agree_on_the_area_they_both_cover():
    assert clip_overlap((0, 0, 100, 100), (50, 50, 100, 100)) == (50, 50, 50, 50)


def test_a_clip_inside_another_one_is_the_inner_one():
    assert clip_overlap((0, 0, 100, 100), (20, 20, 10, 10)) == (20, 20, 10, 10)


def test_two_clips_that_never_meet_clip_nothing_rather_than_a_negative_box():
    x, y, width, height = clip_overlap((0, 0, 10, 10), (50, 50, 10, 10))
    assert (width, height) == (0.0, 0.0)
    # the empty rect sits where the two came closest, which is all a scissor needs
    assert (x, y) == (50, 50)


def test_a_second_clip_inside_two_panels_cuts_by_both_at_once():
    """The reason the intersection is on the way down, not on the way out."""
    outer = clip_overlap((0, 0, 200, 200), (20, 20, 100, 100))
    inner = clip_overlap(outer, (80, 80, 100, 100))
    assert inner == (80, 80, 40, 40)


# -- text -----------------------------------------------------------------


def test_text_without_a_font_still_has_a_size_a_layout_can_use():
    width, height = text_bounds("hello", 20)
    assert width == pytest.approx(5 * 20 * geometry.TEXT_ADVANCE)
    assert height == pytest.approx(20 * geometry.TEXT_HEIGHT)


@pytest.mark.parametrize("text,size", [("", 20), ("hi", 0), ("hi", -5), (None, 20)])
def test_text_that_cannot_be_measured_is_zero_by_zero(text, size):
    assert text_bounds(text, size) == (0.0, 0.0)


def test_a_measured_width_is_stable_enough_to_layout_against():
    # two decimals: a layout that depends on the third will not survive a DPI
    # change, and an estimate that drifts between runs is worse than a rough one
    assert text_bounds("knob", 13) == text_bounds("knob", 13)
    assert text_bounds("knob", 13)[0] == round(4 * 13 * geometry.TEXT_ADVANCE, 2)


@pytest.mark.parametrize("value,expected", [
    ("left", "left"), ("RIGHT", "right"), ("center", "center"),
    ("Centre", "center"), ("middle", "center"), ("", "left"),
    (None, "left"), ("sideways", "left"),
])
def test_an_alignment_says_one_of_three_things_or_means_left(value, expected):
    assert alignment(value) == expected
    assert expected in ALIGNMENTS


@pytest.mark.parametrize("align,expected", [
    ("left", 0.0), ("center", 50.0), ("right", 100.0),
])
def test_the_offset_is_the_part_of_the_width_that_moves_left_of_the_mark(
        align, expected):
    assert align_x_offset(100, align) == expected


def test_the_left_edge_and_the_offset_are_the_same_answer():
    for align in ALIGNMENTS:
        assert align_x(200, 64, align) == 200 - align_x_offset(64, align)


def test_a_centre_is_a_centre_whichever_spelling_it_came_in():
    assert align_x(200, 64, "centre") == align_x(200, 64, "center") == 168.0
