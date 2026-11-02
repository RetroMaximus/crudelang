"""Colours: what a script, a skin token and a shader each mean by one.

``STYLE(x, "bg", "#1a1a2e")`` and ``P_RECT(..., 0.5)`` both have to land on the
same four floats, and a colour that cannot be read has to say so in the terms the
project used to write it - not raise a ``ValueError`` from three modules away.
"""

from __future__ import annotations

import pytest

from crude.ui import BLACK, NAMED, TRANSPARENT, WHITE, RGBA, UIError, as_color

RED = RGBA(1.0, 0.0, 0.0, 1.0)


@pytest.mark.parametrize("value,expected", [
    ("#fff", (1.0, 1.0, 1.0, 1.0)),
    ("#000", (0.0, 0.0, 0.0, 1.0)),
    ("#f00", (1.0, 0.0, 0.0, 1.0)),
    ("#ff0000", (1.0, 0.0, 0.0, 1.0)),
    ("#ff000080", (1.0, 0.0, 0.0, pytest.approx(128 / 255, abs=1e-3))),
    ("#1a1a2e", (26 / 255, 26 / 255, 46 / 255, 1.0)),
    ("  #0F0  ", (0.0, 1.0, 0.0, 1.0)),
])
def test_a_hex_colour_is_read_however_few_digits_it_was_given(value, expected):
    red, green, blue, alpha = expected
    assert as_color(value) == RGBA(red, green, blue, alpha)


def test_the_four_hex_lengths_are_the_only_ones_that_mean_a_colour():
    for bad in ("#", "#ff", "#fffff", "#gggggg", "#1234567", "ff0000"):
        with pytest.raises(UIError, match="not a colour"):
            as_color(bad)


@pytest.mark.parametrize("name", sorted(NAMED))
def test_every_named_colour_is_a_colour(name):
    assert isinstance(as_color(name), RGBA)
    assert as_color(name) is NAMED[name]


def test_a_name_is_read_whichever_way_it_was_capitalised():
    assert as_color("White") == as_color(" WHITE ") == WHITE


@pytest.mark.parametrize("value,expected", [
    ((1, 0, 0), (1.0, 0.0, 0.0, 1.0)),
    ([0, 0, 1, 0.5], (0.0, 0.0, 1.0, 0.5)),
    ((0.25, 0.5, 0.75), (0.25, 0.5, 0.75, 1.0)),
])
def test_two_or_three_numbers_are_a_colour_and_the_fourth_is_full_alpha(
        value, expected):
    assert as_color(value) == RGBA(*expected)


@pytest.mark.parametrize("value,expected", [
    (0, (0.0, 0.0, 0.0, 1.0)),
    (1, (1.0, 1.0, 1.0, 1.0)),
    (0.5, (0.5, 0.5, 0.5, 1.0)),
    # a big number is not a grey; it is the black a script meant by "0"
    (255, (0.0, 0.0, 0.0, 1.0)),
])
def test_a_single_number_is_grey_and_not_an_index(value, expected):
    assert as_color(value) == RGBA(*expected)


def test_an_omitted_colour_is_the_one_that_shows_everything():
    assert as_color(None) == WHITE


@pytest.mark.parametrize("bad", [(1, 2), (1, 2, 3, 4, 5), object(), {"r": 1}])
def test_something_that_cannot_be_a_colour_says_what_it_was_handed(bad):
    with pytest.raises(UIError):
        as_color(bad)


def test_an_alpha_that_was_told_twice_is_the_second_one_kept():
    assert RED.with_alpha(0.25).alpha == 0.25
    assert RED.with_alpha(-1).alpha == 0.0
    assert RED.with_alpha(4).alpha == 1.0


def test_a_premultiplied_colour_carries_its_own_alpha_into_its_channels():
    half = RED.with_alpha(0.5)
    red, green, blue, alpha = half.premultiplied()
    assert (red, alpha) == pytest.approx((0.5, 0.5))
    assert (green, blue) == (0.0, 0.0)
    # an opaque colour is unchanged, which is the case almost every draw is in
    assert RED.premultiplied() == RED


def test_a_colour_written_back_out_is_the_colour_it_came_in_as():
    colour = RGBA(0.2, 0.4, 0.6, 0.8)
    assert str(colour) == "#336699cc"
    assert as_color(str(colour)) == pytest.approx(colour)
    assert colour.as_bytes() == (51, 102, 153, 204)


def test_the_colours_a_skin_starts_from_are_opaque_except_the_one_that_is_not():
    assert WHITE.alpha == BLACK.alpha == 1.0
    assert TRANSPARENT.alpha == 0.0
    assert TRANSPARENT != BLACK


def test_a_colour_is_a_tuple_because_a_shader_wants_a_tuple():
    assert tuple(as_color("#ff8000")) == (1.0, 128 / 255, 0.0, 1.0)
