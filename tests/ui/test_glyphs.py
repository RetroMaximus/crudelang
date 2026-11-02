"""Icons as paths, the pixels they become, and the one texture they share.

The plan's list of what can go wrong here is specific - "path-parser units,
deterministic raster hashes, shelf packing without overlap, UV bounds, tint
independence, per-DPR cache keys, missing-glyph fallback" - and every one of those
is a mistake that a window would show and a layout assertion would miss. None of
them needs a GPU, so they are all judged here, in the default run, on the same
numpy and Pillow the renderer uses.

The last item on that line - one glyph drawing exactly one textured quad - belongs
to the renderers and is tested in ``test_recording_renderer.py`` and
``test_gl_renderer.py``, where the quad is counted.
"""

from __future__ import annotations

import hashlib
import os

import numpy as np
import pytest

from crude.ui.glyphs import (
    BLEED, BOX, FALLBACK, GLYPHS, LADDER, PADDING, Cell, Command, GlyphAtlas,
    GlyphDef, GlyphError, Layer, dump_icons, flatten, flatten_pixels, has_icon,
    icon_names, parse_path, raster, snap_size,
)


@pytest.fixture(scope="module")
def crowded() -> GlyphAtlas:
    """Every icon in the set, at the biggest size the ladder allows, packed into a
    texture that had to grow to hold them."""
    atlas = GlyphAtlas(width=64)
    for name in GLYPHS:
        atlas.cell(name, 32)
    return atlas


def sha(pixels: np.ndarray) -> str:
    """A short fingerprint of a picture, so a test can say "unchanged"."""
    return hashlib.sha1(np.ascontiguousarray(pixels).tobytes()).hexdigest()


def ink(pixels: np.ndarray, threshold: int = 8) -> int:
    """How many pixels of a raster are visibly painted."""
    return int((pixels[..., 3] > threshold).sum())


def box_of(pixels: np.ndarray) -> tuple:
    """The corners of the ink's bounding box, or ``None`` if there is no ink."""
    alpha = pixels[..., 3]
    columns = np.nonzero(alpha.max(axis=0) > 8)[0]
    rows = np.nonzero(alpha.max(axis=1) > 8)[0]
    if not len(columns) or not len(rows):
        return None
    return (columns.min(), rows.min(), columns.max(), rows.max())


# -- the path language -----------------------------------------------------


def test_a_command_takes_the_numbers_it_needs_and_keeps_its_own():
    assert parse_path("M28 18 L82 50 L28 82 Z") == [
        Command("M", (28.0, 18.0)),
        Command("L", (82.0, 50.0)),
        Command("L", (28.0, 82.0)),
        Command("Z", ()),
    ]


@pytest.mark.parametrize("text", [
    "M1 2 L3 4",
    "M1,2 L3,4",
    "M 1 2 L 3 4",
    "M1 2L3 4",          # a letter needs no space before it
    "M1 2 l3 4",         # reported as L, marked as relative
])
def test_numbers_and_letters_separate_themselves_however_they_are_spaced(text):
    found = parse_path(text)
    assert [command.name for command in found] == ["M", "L"]
    assert found[0].args == (1.0, 2.0)
    # the arguments are kept as written: whether they are offsets from the pen is
    # the flatten step's business, not the reader's
    assert found[1].args == (3.0, 4.0)
    assert found[1].relative == ("l" in text)


def test_a_number_can_be_signed_a_decimal_or_an_exponent():
    assert parse_path("M-1.5.5 L+1e2 -50")[1].args == (100.0, -50.0)


def test_a_lower_case_command_is_kept_as_a_separate_thing():
    found = parse_path("m10 10 l5 5")
    assert [command.name for command in found] == ["M", "L"]
    assert [command.relative for command in found] == [True, True]


def test_extra_pairs_after_a_move_are_lines_from_there():
    # "M 10 10 20 20 30 30" is a move and two lines, which is what makes a
    # polygon writable as one command
    found = parse_path("M10 10 20 20 30 30")
    assert [command.name for command in found] == ["M", "L", "L"]
    assert found[-1].args == (30.0, 30.0)


@pytest.mark.parametrize("text,about", [
    ("M10 10 L20", "'L' takes groups of 2"),
    ("M10 10 C1 2 3", "'C' takes groups of 6"),
    ("M10 10 A5 5 0 0 1 20", "'A' takes groups of 7"),
    ("M10 10 Q1 2", "'Q' takes groups of 4"),
])
def test_a_command_that_is_short_on_numbers_says_which_one(text, about):
    with pytest.raises(GlyphError) as raised:
        parse_path(text)
    assert about in str(raised.value)


def test_a_path_has_to_start_at_a_point():
    with pytest.raises(GlyphError, match="starts at a point"):
        parse_path("L10 10 L50 50")


@pytest.mark.parametrize("text", ["", "   ", "\t"])
def test_an_empty_path_is_not_a_picture(text):
    with pytest.raises(GlyphError, match="cannot be empty"):
        parse_path(text)


def test_a_number_before_any_command_is_not_a_path():
    with pytest.raises(GlyphError, match="start with a command"):
        parse_path("10 10 M20 20")


def test_close_takes_no_numbers():
    with pytest.raises(GlyphError, match="takes no numbers"):
        parse_path("M1 1 L2 2 Z 5")


def test_an_unknown_letter_is_answered_with_the_ones_that_exist():
    with pytest.raises(GlyphError) as raised:
        parse_path("X10 10")
    said = str(raised.value)
    assert "'X'" in said
    for letter in "MLCAZ":
        assert letter in said


def test_a_command_letter_is_a_letter_rather_than_a_typo_in_a_number():
    with pytest.raises(GlyphError, match="'h'"):
        parse_path("M10 10 h40")


@pytest.mark.parametrize("name", list(GLYPHS))
def test_every_icon_in_the_set_is_a_path_that_can_be_read(name):
    for layer in GLYPHS[name].layers:
        found = parse_path(layer.path)
        assert found[0].name == "M"
        assert all(command.name in "MLCQAZ" for command in found)


# -- paths as points -------------------------------------------------------


def test_a_straight_line_stays_two_points_however_it_is_measured():
    for scale in (0.5, 2.0, 40.0):
        only = flatten(parse_path("M0 0 L100 100"), scale=scale)
        assert len(only) == 1
        assert only[0].points == ((0.0, 0.0), (100.0, 100.0))


def test_a_curve_is_cut_at_the_pixel_rather_than_at_the_path():
    path = parse_path("M10 50 C30 10 70 90 90 50")
    coarse = flatten(path, scale=1.0)[0].points
    fine = flatten(path, scale=10.0)[0].points
    assert len(fine) > len(coarse) > 3
    # and it still arrives, because the sampler is only about the middle
    assert fine[-1] == pytest.approx((90.0, 50.0))


@pytest.mark.parametrize("text,last", [
    ("M10 10 Q50 90 90 10", (90.0, 10.0)),
    ("M10 50 A40 40 0 0 1 90 50", (90.0, 50.0)),
    ("m10 10 c40 -40 40 40 80 0", (90.0, 10.0)),
])
def test_every_kind_of_segment_ends_exactly_where_it_said_it_would(text, last):
    points = flatten(parse_path(text))[0].points
    assert points[-1] == pytest.approx(last, abs=1e-9)


def test_a_relative_path_lands_where_the_absolute_one_does():
    assert (flatten(parse_path("M10 10 l40 40"))[0].points
            == flatten(parse_path("M10 10 L50 50"))[0].points)


def test_only_the_end_of_a_relative_arc_is_a_position():
    # an arc's radii and flags are numbers, not places, so a relative `a` moves
    # two of its seven and leaves the rest alone
    relative = flatten(parse_path("M10 10 a40 40 0 0 1 40 40"))[0].points
    absolute = flatten(parse_path("M10 10 A40 40 0 0 1 50 50"))[0].points
    assert len(relative) == len(absolute)
    assert all(one == pytest.approx(other)
               for one, other in zip(relative, absolute))


def test_a_closed_path_says_so_and_ends_where_it_began():
    only = flatten(parse_path("M10 10 L90 10 L50 90 Z"))[0]
    assert only.closed is True
    assert only.points[0] == only.points[-1]


def test_a_move_starts_a_new_line_rather_than_joining_the_old_one():
    found = flatten(parse_path("M10 10 L20 20 M30 30 L40 40"))
    assert len(found) == 2
    assert found[0].points[-1] == (20.0, 20.0)
    assert found[1].points[0] == (30.0, 30.0)


def test_a_subpath_of_one_point_is_not_a_shape():
    # a lone moveto has nothing to fill and nothing to stroke, so it is left out
    # rather than being drawn as a dot nobody asked for
    assert flatten(parse_path("M50 50")) == []


def test_an_arc_back_to_where_it_started_draws_nothing():
    assert flatten(parse_path("M50 50 A20 20 0 1 1 50 50")) == []


def test_an_arc_with_no_radius_is_a_straight_jump():
    points = flatten(parse_path("M10 10 A0 0 0 0 1 60 40"))[0].points
    assert points == ((10.0, 10.0), (60.0, 40.0))


def test_radii_too_small_for_the_ends_are_grown_rather_than_ignored():
    # SVG's rule: an arc that cannot reach gets rounder, not shorter
    points = flatten(parse_path("M0 50 A1 1 0 0 1 100 50"))[0].points
    assert points[-1] == pytest.approx((100.0, 50.0))
    assert min(point[1] for point in points) < 1.0
    assert all(0.0 <= point[1] <= 100.0 for point in points)


def test_the_sweep_flag_picks_which_way_round_the_arc_goes():
    up = flatten(parse_path("M16 50 A34 34 0 1 1 84 50"))[0].points
    down = flatten(parse_path("M16 50 A34 34 0 1 0 84 50"))[0].points
    # the same two ends and the same radius, told the other way round: one is the
    # top of a circle and the other its bottom, which is how `record` and `power`
    # are drawn at all
    assert min(point[1] for point in up) < 20.0
    assert max(point[1] for point in up) < 55.0
    assert max(point[1] for point in down) > 80.0
    assert min(point[1] for point in down) > 45.0


def test_a_subpath_reports_the_extremes_it_went_through():
    only = flatten(parse_path("M10 20 L90 20 L50 80"))[0]
    assert only.bounds() == (10.0, 20.0, 90.0, 80.0)


def test_an_empty_subpath_has_no_bounds_to_report():
    assert flatten([]) == []


# -- one glyph, rasterized -------------------------------------------------


def test_a_raster_is_white_with_its_shape_carried_in_the_alpha():
    pixels = raster(GLYPHS["play"], 32)
    assert pixels.shape == (32, 32, 4)
    assert pixels.dtype == np.uint8
    assert (pixels[..., :3] == 255).all()
    assert pixels[..., 3].min() == 0 and pixels[..., 3].max() == 255


def test_the_same_glyph_twice_is_the_same_picture_byte_for_byte():
    once = raster(GLYPHS["record"], 24)
    twice = raster(GlyphDef("record", GLYPHS["record"].layers), 24)
    assert sha(once) == sha(twice)


def test_a_bigger_box_is_more_pixels_of_the_same_shape():
    small = raster(GLYPHS["stop"], 16)
    large = raster(GLYPHS["stop"], 32)
    assert all(big == pytest.approx(little * 2, abs=1)
               for big, little in zip(box_of(large), box_of(small)))
    # the ink quadruples with the area, within the rounding of small pictures
    assert ink(large) == pytest.approx(ink(small) * 4, rel=0.25)


def test_a_heavier_weight_is_a_thicker_line_and_nothing_else():
    thin = raster(GLYPHS["minus"], 32)
    bold = raster(GLYPHS["minus"], 32, weight=2.0)
    assert ink(bold) > ink(thin) * 1.5
    # the same line, fatter: still centred, still the same length
    assert (box_of(thin)[0] + box_of(thin)[2]) == pytest.approx(
        box_of(bold)[0] + box_of(bold)[2], abs=2)


@pytest.mark.parametrize("name", list(GLYPHS))
@pytest.mark.parametrize("side", LADDER)
def test_no_glyph_is_cut_by_the_edge_of_its_own_box(name, side):
    pixels = raster(GLYPHS[name], side)
    assert ink(pixels) > 0, name
    # a shape that overran the box would leave a whole side of solid ink behind
    edge = max(pixels[..., 3][0].max(), pixels[..., 3][-1].max(),
               pixels[..., 3][:, 0].max(), pixels[..., 3][:, -1].max())
    assert edge < 128, f"{name} at {side}px touches the frame with {edge}"


def test_every_icon_is_its_own_picture():
    """Two icons with the same pixels are one icon written down twice."""
    made = [sha(raster(GLYPHS[name], 32)) for name in GLYPHS]
    assert len(set(made)) == len(made)


def test_a_glyph_with_nothing_in_it_is_an_empty_picture():
    pixels = raster(GlyphDef("blank", ()), 16)
    assert pixels.shape == (16, 16, 4)
    assert (pixels[..., 3] == 0).all()


def test_the_whole_picture_is_scaled_together_so_sizes_agree():
    # BLEED is the only thing between a mark at M50 4 and the edge of the box, so
    # the mapping has to leave room for it at every size, not just at a large one
    small = box_of(raster(GLYPHS["usb"], 12))
    large = box_of(raster(GLYPHS["usb"], 48))
    assert small[0] >= 1 and small[2] <= 10
    assert large[0] >= 4 and large[2] <= 43


# -- sizes --------------------------------------------------------------


@pytest.mark.parametrize("wanted,rung", [
    (0, 12), (1, 12), (11.9, 12), (12, 12),
    (13, 16), (18, 20), (20, 20), (21, 24), (25, 32), (32, 32),
    # above the ladder there is nothing left to snap to
    (33, 33), (47.2, 48), (100, 100),
])
def test_a_size_becomes_the_nearest_rung_up(wanted, rung):
    assert snap_size(wanted) == rung


def test_the_ladder_is_the_one_the_plan_asked_for():
    assert LADDER == (12, 16, 20, 24, 32)


# -- the atlas ----------------------------------------------------------


def test_asking_twice_gets_the_same_cell_and_one_raster():
    atlas = GlyphAtlas()
    first = atlas.cell("play", 20)
    again = atlas.cell("play", 20)
    assert first == again
    assert atlas.stats()["rasters"] == 1
    assert atlas.version == 1


def test_a_cell_remembers_the_device_size_it_was_made_at():
    atlas = GlyphAtlas(dpr=2.0)
    # 18 logical px on a 2x display is 36 device px, which is off the ladder
    assert atlas.cell("play", 18).size == 36
    assert GlyphAtlas(dpr=1.0).cell("play", 18).size == 20


def test_the_texture_keeps_its_key_and_says_when_the_pixels_moved():
    # one key for one texture, refilled in place: a versioned key would mean a new
    # GPU allocation for every icon a project ever drew
    atlas = GlyphAtlas()
    key = atlas.texture_key
    assert atlas.version == 0 and atlas.uploaded == -1
    atlas.cell("play", 20)
    assert atlas.version == 1
    atlas.cell("play", 20)
    assert atlas.version == 1, "a repeat ask must not make work"
    atlas.cell("stop", 20)
    assert atlas.version == 2
    assert atlas.texture_key == key


def test_two_displays_of_different_scale_get_different_textures():
    plain = GlyphAtlas(dpr=1.0)
    sharp = GlyphAtlas(dpr=2.0)
    plain.cell("play", 20)
    sharp.cell("play", 20)
    assert plain.texture_key != sharp.texture_key
    assert plain.texture_key == "crude:glyphs@1" and \
        sharp.texture_key == "crude:glyphs@2"


def test_a_weight_is_part_of_the_key_so_a_bold_icon_is_its_own_cell():
    atlas = GlyphAtlas()
    normal = atlas.cell("minus", 24)
    bold = atlas.cell("minus", 24, weight=1.5)
    assert normal != bold
    assert atlas.stats()["rasters"] == 2
    assert atlas.cell("minus", 24) == normal


def test_the_whole_set_packs_without_anyone_overlapping_anyone(crowded):
    """One atlas, every icon, and a texture that had to grow to hold them."""
    cells = crowded.cells()
    assert len(cells) == len(GLYPHS)
    assert crowded.stats()["rasters"] == len(GLYPHS), "a re-raster is a re-do"
    assert crowded.stats()["growths"] > 0, "64px was never going to be enough"
    for one in cells:
        assert 0 <= one.x and one.x + one.width <= crowded.width
        assert 0 <= one.y and one.y + one.height <= crowded.height
        for other in cells:
            if other is one:
                continue
            shares_x = one.x - PADDING < other.x + other.width and \
                other.x - PADDING < one.x + one.width
            shares_y = one.y - PADDING < other.y + other.height and \
                other.y - PADDING < one.y + one.height
            assert not (shares_x and shares_y), f"{one} sits on {other}"


def test_every_cell_is_walled_off_from_its_neighbours_by_clear_pixels(crowded):
    # linear filtering reads up to a pixel outside the UV box; that ring has to
    # belong to nobody, or a glyph would arrive with a sliver of another in it
    plane = crowded.array()[..., 3]
    for cell in crowded.cells():
        left, right = cell.x - PADDING, cell.x + cell.width + PADDING - 1
        top, bottom = cell.y - PADDING, cell.y + cell.height + PADDING - 1
        assert (plane[top, left:right + 1] == 0).all()
        assert (plane[bottom + 1, left:right + 1] == 0).all()
        assert (plane[top:bottom + 1, left] == 0).all()
        assert (plane[top:bottom + 1, right + 1] == 0).all()


def test_a_cell_is_in_the_texture_exactly_where_it_says_it_is():
    atlas = GlyphAtlas(width=64)
    cell = atlas.cell("play", 32)
    plane = atlas.array()
    cut = plane[cell.y:cell.y + cell.height, cell.x:cell.x + cell.width]
    assert sha(cut) == sha(raster(GLYPHS["play"], 32))
    assert sha(atlas.pixels("play", 32)) == sha(cut)


def test_the_uv_box_is_the_cell_and_v_starts_at_the_top():
    atlas = GlyphAtlas(width=64)
    atlas.cell("play", 32)
    cell = atlas.cell("stop", 32)
    u0, v0, u1, v1 = cell.uv(atlas.width, atlas.height)
    assert (u0, v0, u1, v1) == (cell.x / 64, cell.y / atlas.height,
                                (cell.x + 32) / 64, (cell.y + 32) / atlas.height)
    assert 0.0 <= u0 < u1 <= 1.0 and 0.0 <= v0 < v1 <= 1.0
    # the first row of the array is the top of the picture, so a cell near it has
    # a small v - which is the one convention a wrong renderer could not undo
    assert v0 < 0.5
    assert atlas.uv("stop", 32) == (u0, v0, u1, v1)


def test_the_texture_is_white_so_only_the_draw_can_colour_it():
    atlas = GlyphAtlas()
    for name in list(GLYPHS)[:6]:
        atlas.cell(name, 20)
    plane = atlas.array()
    assert (plane[..., :3] == 255).all()
    # a tint is therefore a multiply of one number, not a per-glyph edit
    assert sha(plane[..., :3]) == sha(np.full_like(plane[..., :3], 255))


def test_the_pixels_are_built_once_and_rebuilt_when_something_is_added():
    atlas = GlyphAtlas()
    atlas.cell("play", 20)
    plane = atlas.array()
    assert atlas.array() is plane
    atlas.cell("stop", 20)
    assert atlas.array() is not plane


def test_the_texture_grows_rather_than_dropping_a_glyph():
    atlas = GlyphAtlas(width=16)
    first = atlas.cell("play", 32)
    before = sha(raster(GLYPHS["play"], 32))
    for name in GLYPHS:
        atlas.cell(name, 32)
    assert len(atlas.cells()) == len(GLYPHS)
    assert atlas.stats()["growths"] > 0
    assert all(cell.x + cell.width <= atlas.width
               and cell.y + cell.height <= atlas.height for cell in atlas.cells())
    # growth gave the glyph more room, but did not move it or draw it again
    assert atlas.cell("play", 32) == first
    assert atlas.stats()["rasters"] == len(GLYPHS)
    assert sha(atlas.pixels("play", 32)) == before


def test_nothing_that_was_already_placed_moves_when_the_texture_widens():
    # the growth that matters is the one after a draw: a quad batched earlier in
    # the frame carries the UV box it was given at the time, so a cell that moved
    # would be shown from the wrong part of the atlas until the frame was over
    atlas = GlyphAtlas(width=32)
    few = list(GLYPHS)[:4]
    some = [atlas.cell(name, 24) for name in few]
    where = [(cell.x, cell.y) for cell in some]
    big = atlas.cell("waveform", 200)          # forces the width to double
    assert atlas.width > 200
    # asked for again, so a cell that had been moved would show it
    placed = [atlas.cell(name, 24) for name in few]
    assert [(cell.x, cell.y) for cell in placed] == where
    assert all(new is old for new, old in zip(placed, some))
    assert atlas.cell("waveform", 200) == big
    plane = atlas.array()
    assert plane.shape == (atlas.height, atlas.width, 4)
    for cell in placed:
        cut = plane[cell.y:cell.y + cell.height, cell.x:cell.x + cell.width]
        assert sha(cut) == sha(raster(GLYPHS[cell.name], cell.size))


def test_a_name_that_is_not_in_the_set_becomes_the_placeholder():
    atlas = GlyphAtlas()
    cell = atlas.cell("loudness-war", 20)
    assert cell.name == FALLBACK
    assert sha(atlas.pixels("loudness-war", 20)) == \
        sha(raster(GLYPHS[FALLBACK], 20))
    # reported once, so a script with a typo in ten widgets makes one complaint
    atlas.cell("loudness-war", 20)
    atlas.cell("another-typo", 20)
    assert atlas.missing == ["loudness-war", "another-typo"]


def test_an_empty_atlas_is_still_a_texture():
    atlas = GlyphAtlas()
    assert atlas.stats()["cells"] == 0
    assert atlas.names() == ()
    assert atlas.array().shape == (atlas.height, atlas.width, 4)
    assert (atlas.array()[..., 3] == 0).all()


def test_a_name_is_listed_once_however_many_sizes_of_it_were_asked():
    atlas = GlyphAtlas()
    atlas.cell("play", 16)
    atlas.cell("play", 32)
    atlas.cell("stop", 20)
    assert atlas.names() == ("play", "stop")
    assert atlas.stats()["cells"] == 3


def test_the_atlas_describes_itself_in_the_numbers_a_report_needs():
    atlas = GlyphAtlas(dpr=1.5)
    atlas.cell("play", 20)
    assert atlas.stats() == {
        "cells": 1, "version": 1, "rasters": 1, "missing": 0, "dpr": 1.5,
        "width": atlas.width, "height": atlas.height, "growths": 0,
        "uploads": 0,
    }
    assert "1 of 32" in str(atlas)


def test_a_cell_knows_its_own_corners_in_the_texture():
    cell = Cell("knob", 24, 1.0, 8, 4, 24, 24)
    assert cell.uv(64, 32) == (0.125, 0.125, 0.5, 0.875)
    assert "knob" in str(cell) and "24px" in str(cell)


def test_a_layer_can_be_asked_for_bolder_without_changing_the_glyph():
    layer = Layer("M50 10 L50 90", fill=False, stroke=True, width=8.0)
    assert layer.scaled(2.0).width == 16.0
    assert layer.scaled(2.0).path == layer.path
    assert layer.scaled(1.0) == layer


# -- the set itself -----------------------------------------------------


def test_the_set_is_the_one_the_plan_asked_for_and_a_placeholder():
    assert has_icon("play") and has_icon("sliders")
    assert has_icon("missing") and not has_icon("nope")
    assert icon_names() == tuple(GLYPHS)
    assert len(GLYPHS) == 32
    # the names a script will type, in the order a person reads a list
    assert list(GLYPHS)[:8] == ["play", "pause", "stop", "record", "power",
                                "dsp", "beat", "gear"]


def test_the_box_and_its_allowance_are_the_same_for_every_glyph():
    assert BOX == 100.0 and BLEED == 8.0


# -- the dump tool ------------------------------------------------------


def test_dumping_writes_one_png_per_icon_and_matches_the_texture(tmp_path):
    atlas = GlyphAtlas()
    written = dump_icons(atlas, str(tmp_path), size=24)
    assert len(written) == len(GLYPHS)
    assert all(os.path.basename(path) == f"{name}.png"
               for path, name in zip(written, icon_names()))
    from PIL import Image
    with Image.open(written[0]) as opened:
        assert opened.mode == "RGBA"
        assert sha(np.asarray(opened)) == sha(atlas.pixels("play", 24))


def test_dumping_can_be_told_which_icons_are_wanted(tmp_path):
    atlas = GlyphAtlas()
    written = dump_icons(atlas, str(tmp_path / "some"), size=20,
                         names=["play", "stop"])
    assert [os.path.basename(path) for path in written] == ["play.png",
                                                            "stop.png"]
    assert not (tmp_path / "some" / "pause.png").exists()


def test_a_dump_left_unflattened_is_the_texture_it_was_given(tmp_path):
    """No ``over`` says "write the atlas", which is what a build wants."""
    atlas = GlyphAtlas()
    written = dump_icons(atlas, str(tmp_path), size=16, names=["play"])
    from PIL import Image

    with Image.open(written[0]) as opened:
        pixels = np.asarray(opened)
    assert sha(pixels) == sha(atlas.pixels("play", 16))
    # and the ink really is white on nothing, which is why a viewer shows a hole
    assert (pixels[..., :3] == 255).all()
    assert pixels[0, 0, 3] == 0


def test_a_flattened_dump_is_two_colours_and_opaque_everywhere(tmp_path):
    atlas = GlyphAtlas()
    written = dump_icons(atlas, str(tmp_path), size=24, names=["stop"],
                         over="#202020", ink="#ff0000")
    from PIL import Image

    with Image.open(written[0]) as opened:
        pixels = np.asarray(opened)
    assert (pixels[..., 3] == 255).all()
    middle = pixels.shape[0] // 2
    assert tuple(pixels[middle, middle]) == (255, 0, 0, 255)
    assert tuple(pixels[0, 0]) == (32, 32, 32, 255)


def test_flattening_onto_nothing_hands_back_the_array_it_was_given():
    pixels = GlyphAtlas().pixels("play", 16)

    assert flatten_pixels(pixels, None) is pixels


def test_a_flattened_edge_is_the_mix_of_the_two_colours_it_was_made_from():
    """Coverage is the only thing an icon carries, so a flatten keeps it."""
    pixels = GlyphAtlas().pixels("chevron-l", 24)
    flat = flatten_pixels(pixels, "#000000", "#ffffff")
    partial = (pixels[..., 3] > 8) & (pixels[..., 3] < 247)

    assert partial.any(), "a 24 px chevron has an anti-aliased edge"
    # black plate, white ink: what came through is exactly the coverage
    assert np.array_equal(flat[partial, 0], pixels[..., 3][partial])
