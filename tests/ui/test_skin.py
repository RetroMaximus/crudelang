"""The skin: tokens in, roles out, and no widget allowed to own a colour.

The plan's rule for this layer is blunt - "style is a role, never a raw colour" -
and the failures it is guarding against are all silent ones. A theme that forgot
one token half-works until the widget that asks for it is built. A hover that
multiplies a near-white surface by 1.1 is a button that does not answer the
pointer, and only shows up on the one theme nobody ran. A state word that means
"the pointer is over it" *or* "it is switched on" is a toggle that draws itself
off the moment you hover it. None of those are catchable by a layout assertion,
so they are caught here, by number, on a machine with no display.

What is *not* tested here is the colour parsing itself -
:mod:`tests.ui.test_colors` owns that - and what a widget does with an
:class:`~crude.ui.skin.Appearance`, which arrives with the widget core.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from crude.ui import (COLLOUR_TOKENS, DEFAULT_THEME, NUMBER_TOKENS, ROLES,
                      SIZES, STATES, THEMES, WHITE, RGBA, Appearance, Role,
                      Skin, Theme, UIError, as_color, as_role, as_scale,
                      as_theme, colour_token_names, contrast, describe,
                      luminance, mix, number_token_names, role_names, shade,
                      state_names, theme_names)

#: the words a value-half of a state can be, and the pointer-half, spelled out
#: here so a test can ask for the pairs without reaching into the module
VALUES = ("on", "selected")
ACTIONS = ("hover", "pressed", "focus", "disabled")


@pytest.fixture(scope="module")
def dark() -> Skin:
    """The theme the reference UI already had."""
    return Skin("dark")


@pytest.fixture(scope="module")
def light() -> Skin:
    """The one that turns every rule above on its head."""
    return Skin("light")


@pytest.fixture(scope="module")
def flat() -> Skin:
    """Dark's colours with the corners and the shadows taken off."""
    return Skin("flat")


@pytest.fixture(params=sorted(THEMES))
def every_theme(request) -> Skin:
    """A skin over each theme in turn, because a rule that only works on one is
    a rule that is written in the wrong place."""
    return Skin(request.param)


# -- the palette -----------------------------------------------------------


def test_the_three_themes_the_plan_asked_for_are_the_three_themes_there_are():
    assert theme_names() == ("dark", "flat", "light")
    assert DEFAULT_THEME in THEMES


def test_a_theme_is_the_whole_palette_or_it_is_not_built():
    for theme in THEMES.values():
        assert set(theme.colours) == set(COLLOUR_TOKENS)
        assert set(theme.numbers) == set(NUMBER_TOKENS)


def test_a_theme_that_half_exists_is_refused_naming_everything_it_is_short():
    with pytest.raises(UIError) as raised:
        Theme("broken", {"bg": "#000000"}, {"radius.none": 0.0})
    said = str(raised.value)
    assert "broken" in said
    # one list, colours then numbers, so the author fixes the table in one pass
    assert "surface" in said and "radius.sm" in said
    assert "bg" not in said.split("has no value for")[1]


@pytest.mark.parametrize("token", COLLOUR_TOKENS)
def test_a_colour_token_reads_back_as_the_floats_a_shader_wants(token):
    for theme in THEMES.values():
        found = theme.colour(token)
        assert isinstance(found, RGBA)
        assert all(0.0 <= channel <= 1.0 for channel in found)
        assert found.alpha == 1.0, "a palette colour is not translucent"


def test_a_theme_knows_which_way_it_is_light_without_being_told():
    assert THEMES["dark"].is_light is False
    assert THEMES["flat"].is_light is False
    assert THEMES["light"].is_light is True
    assert replace(THEMES["dark"], name="grey").is_light is False


def test_asking_a_theme_for_a_token_it_does_not_have_names_the_token():
    with pytest.raises(UIError, match="no colour token 'nonsense'"):
        THEMES["dark"].colour("nonsense")
    with pytest.raises(UIError, match="no number token 'radius.huge'"):
        THEMES["dark"].number("radius.huge")


def test_a_theme_rolls_up_its_own_token_names():
    assert THEMES["dark"].token_names() == (*COLLOUR_TOKENS, *NUMBER_TOKENS)
    assert THEMES["dark"].names() == ("colours", "numbers")


# -- the colour arithmetic the palette cannot do for itself ----------------


def test_mix_blends_towards_the_other_colour_and_the_ends_are_the_ends():
    black, white = RGBA(0.0, 0.0, 0.0, 1.0), RGBA(1.0, 1.0, 1.0, 1.0)
    assert mix(black, white, 0.0) == black
    assert mix(black, white, 1.0) == white
    assert mix(black, white, 0.5) == RGBA(0.5, 0.5, 0.5, 1.0)


def test_a_mix_weight_outside_the_range_is_the_end_it_was_past():
    red = RGBA(1.0, 0.0, 0.0, 1.0)
    assert mix(red, WHITE, 4.0) == mix(red, WHITE, 1.0) == WHITE
    assert mix(red, WHITE, -4.0) == red


def test_mix_keeps_both_alphas_rather_than_choosing_one():
    half = RGBA(1.0, 0.0, 0.0, 0.5)
    assert mix(half, RGBA(0.0, 0.0, 1.0, 1.0), 0.5).alpha == pytest.approx(0.75)


def test_shade_multiplies_and_clips_rather_than_wrapping_around():
    blue = RGBA(0.2, 0.4, 0.9, 1.0)
    assert shade(blue, 0.5) == RGBA(0.1, 0.2, 0.45, 1.0)
    assert shade(blue, 3.0) == pytest.approx(RGBA(0.6, 1.0, 1.0, 1.0))
    assert shade(blue, -1.0) == RGBA(0.0, 0.0, 0.0, 1.0)
    assert shade(blue, 1.0) == blue


def test_luminance_runs_from_black_to_white_through_weights_that_sum_to_one():
    assert luminance("#000000") == 0.0
    assert luminance("#ffffff") == pytest.approx(1.0)
    assert luminance("#808080") == pytest.approx(128 / 255, abs=1e-3)
    assert luminance(WHITE) == pytest.approx(1.0)


def test_a_green_is_lighter_than_a_red_of_the_same_value():
    # the reason the states are measured in luminance rather than in "distance
    # between two hex strings": a bright red and a bright blue are not the same
    # brightness, and a highlight judged by the wrong one is half as visible
    assert luminance("#ff0000") < luminance("#00ff00")


def test_contrast_is_a_distance_that_does_not_care_which_way_it_was_asked():
    here, there = "#2b323d", "#dbe4f0"
    assert contrast(here, there) == contrast(there, here)
    assert contrast(here, here) == 0.0
    assert contrast("#000000", "#ffffff") == pytest.approx(1.0)


def test_a_number_is_a_grey_here_as_everywhere_else():
    assert contrast(0.5, 0.5) == 0.0
    assert luminance(0.0) == 0.0
    assert luminance(1.0) == pytest.approx(1.0)


# -- what a script's word means --------------------------------------------


def test_as_theme_takes_a_name_a_skin_or_a_theme_and_nothing_means_the_default():
    assert as_theme("DARK").name == "dark"
    assert as_theme(THEMES["light"]) is THEMES["light"]
    assert as_theme(Skin("flat")).name == "flat"
    assert as_theme(None).name == DEFAULT_THEME
    assert as_theme("  ").name == DEFAULT_THEME


def test_a_theme_that_is_not_one_of_them_is_reported_as_a_manifest_typo():
    with pytest.raises(UIError) as raised:
        as_theme("midnight")
    said = str(raised.value)
    assert "midnight" in said
    for name in theme_names():
        assert name in said


@pytest.mark.parametrize("word,wanted", [
    ("default", "surface"),
    ("bg", "window"),
    ("background", "window"),
    ("label", "text"),
    ("heading", "title"),
    ("slider", "track"),
    ("hslider", "track"),
    ("nbx", "field"),
    ("radio", "checkbox"),
    ("dropdown", "field"),
    ("destructive", "danger"),
    ("success", "ok"),
    ("caution", "warning"),
    ("scope", "grid"),
    ("button-toggle", "toggle"),
    ("panel-border", "border"),
])
def test_the_words_a_script_uses_before_it_knows_the_vocabulary_mean_the_role(
        word, wanted):
    assert as_role(word) == wanted


@pytest.mark.parametrize("word,wanted", [
    ("Button", "button"),
    ("  TOGGLE ", "toggle"),
    ("button_toggle", "toggle"),
    ("Button Toggle", "toggle"),
    ("PANEL", "panel"),
    ("panel_border", "border"),
    ("HSlider", "track"),
])
def test_a_role_name_survives_case_and_whichever_separator_was_used(word,
                                                                     wanted):
    assert as_role(word) == wanted


@pytest.mark.parametrize("nothing", [None, "", "   "])
def test_no_role_word_means_the_plain_surface_rather_than_an_error(nothing):
    assert as_role(nothing) == "surface"


def test_a_role_and_an_appearance_can_be_asked_for_their_own_names():
    skin = Skin("dark")
    assert as_role(ROLES["knob"]) == "knob"
    assert as_role(skin.resolve("knob", "hover")) == "knob"


@pytest.mark.parametrize("typo", ["serface", "buton", "trck", "danjeer"])
def test_a_role_that_is_not_there_is_answered_with_the_names_it_was_close_to(
        typo):
    with pytest.raises(UIError) as raised:
        as_role(typo)
    assert typo in str(raised.value)
    assert "not a style role" in str(raised.value)


def test_a_colour_is_not_a_role_and_says_so_rather_than_drawing_black():
    # STYLE(w, "role", "#ff0000") is exactly the mistake this layer exists to
    # stop, so it has to fail rather than quietly pick a surface and move on
    with pytest.raises(UIError, match="not a style role"):
        as_role("#ff0000")


def test_every_word_the_alias_table_knows_resolves_to_a_role_that_exists():
    for word in role_names():
        assert as_role(word) in ROLES, word


@pytest.mark.parametrize("value,wanted", [
    ("sm", "sm"),
    ("LG", "lg"),
    (1.0, "md"),
    (0.72, "xs"),
    (1.4, "xl"),
    (0.9, "sm"),
    (None, "md"),
])
def test_a_size_is_a_rung_and_a_number_becomes_the_nearest_one(value, wanted):
    assert as_scale(value) == wanted


def test_a_size_that_is_not_a_rung_lists_the_ones_that_are():
    with pytest.raises(UIError) as raised:
        as_scale("chunky")
    said = str(raised.value)
    assert "chunky" in said
    for name in SIZES:
        assert name in said


def test_the_size_rungs_go_from_small_to_large():
    assert sorted(SIZES, key=lambda name: SIZES[name]) == ["xs", "sm", "md",
                                                            "lg", "xl"]
    assert SIZES["md"] == 1.0


# -- the role table --------------------------------------------------------


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_every_role_resolves_in_every_state_without_raising(theme):
    skin = Skin(theme)
    for role in ROLES:
        for state in state_names():
            found = skin.resolve(role, state)
            assert isinstance(found, Appearance)
            assert found.role == role
            assert 0.0 <= min(found.fill) <= max(found.fill) <= 1.0
            assert found.radius >= 0.0 and found.width >= 0.0
            assert found.padding >= 0.0 and found.font >= 1.0


def test_every_token_a_role_names_is_a_token_the_theme_has():
    for role in ROLES.values():
        for token in (role.fill, role.ink, role.line, role.on, role.on_ink):
            if token:
                assert token in COLLOUR_TOKENS, (role.name, token)
        for token in (role.radius, role.border, role.padding, role.font):
            assert token in NUMBER_TOKENS, (role.name, token)


def test_no_token_in_the_palette_is_decorative():
    # a token nothing names is a token that will be asked for and get the wrong
    # answer, and a palette the widgets do not use is two tables out of step
    named = set()
    for role in ROLES.values():
        named.update(filter(None, (role.fill, role.ink, role.line, role.on,
                                   role.on_ink)))
        named.update((role.radius, role.border, role.padding, role.font))
    assert named == set(COLLOUR_TOKENS) | set(NUMBER_TOKENS)


def test_every_colour_is_the_answer_to_some_question_about_some_part(every_theme):
    seen = set()
    for role in ROLES:
        for state in state_names():
            found = every_theme.resolve(role, state)
            seen.update((found.fill, found.ink, found.border))
    for token in COLLOUR_TOKENS:
        assert every_theme.theme.colour(token) in seen, token


def test_the_faces_of_a_panel_do_not_answer_the_pointer(dark):
    for role in ("window", "panel", "card", "surface", "text", "caption",
                 "hint", "title", "muted"):
        assert not ROLES[role].responds("hover"), role
        assert dark.resolve(role, "hover").fill == dark.resolve(role).fill


def test_the_intents_a_script_is_given_are_the_ones_a_theme_can_paint(dark):
    wanted = {"accent": "accent", "ok": "good", "warning": "warn",
              "danger": "bad", "error": "bad", "info": "info"}
    for role, token in wanted.items():
        assert dark.resolve(role).fill == THEMES["dark"].colour(token), role


def test_a_text_role_is_ink_over_what_it_sits_on_rather_than_a_box(dark):
    for role in ("text", "caption", "hint", "title", "muted"):
        found = dark.resolve(role)
        assert found.radius == 0.0 and found.padding == 0.0, role
    assert dark.resolve("caption").ink == THEMES["dark"].colour("dim")
    assert dark.resolve("hint").ink == THEMES["dark"].colour("faint")
    assert dark.resolve("title").font > dark.resolve("text").font
    assert dark.resolve("hint").font < dark.resolve("caption").font


# -- states ----------------------------------------------------------------


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_a_hover_is_seen_on_every_part_that_says_it_can_be(theme):
    skin = Skin(theme)
    for role, found in ROLES.items():
        if not found.responds("hover"):
            continue
        plain = skin.resolve(role)
        assert contrast(plain.fill, skin.resolve(role, "hover").fill) > 0.04, \
            (theme, role)


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_a_press_is_seen_and_is_not_mistakable_for_a_hover(theme):
    skin = Skin(theme)
    for role, found in ROLES.items():
        if not (found.responds("pressed") and found.responds("hover")):
            continue
        plain, hovered = skin.resolve(role), skin.resolve(role, "hover")
        pressed = skin.resolve(role, "pressed")
        assert contrast(plain.fill, pressed.fill) > 0.04, (theme, role)
        assert contrast(hovered.fill, pressed.fill) > 0.02, (theme, role)


def test_a_dark_theme_lifts_on_hover_and_a_light_one_sinks(dark, light):
    here = dark.resolve("button")
    there = light.resolve("button")
    assert luminance(dark.resolve("button", "hover").fill) > luminance(here.fill)
    assert luminance(light.resolve("button", "hover").fill) < luminance(there.fill)


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_a_fill_with_no_room_the_other_way_moves_this_way(theme):
    # the whole reason the highlights are measured in steps of lightness: a
    # near-white part in a dark theme cannot lift, and would not answer the
    # pointer at all if "hover lifts" were a rule instead of an intention
    skin = Skin(theme, overrides={"raised": "#ffffff" if theme == "dark"
                                  else "#000000"})
    plain = skin.resolve("button")
    hovered = skin.resolve("button", "hover")
    assert contrast(plain.fill, hovered.fill) > 0.04
    going_up = luminance(hovered.fill) > luminance(plain.fill)
    assert going_up is (theme != "dark"), theme


def test_a_highlight_keeps_the_hue_it_was_painted_with(dark):
    # a pressed red that turned blue is a bug nobody describes as a colour bug,
    # so the ordering of the channels is the assertion that catches it
    for state in ACTIONS:
        if not ROLES["danger"].responds(state):
            continue
        found = dark.resolve("danger", state).fill
        assert found.red > found.blue > found.green or (
            found.red > found.green > found.blue), state


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_a_focus_ring_borrows_the_theme_own_edge(theme):
    skin = Skin(theme)
    edge = THEMES[theme].colour("edge")
    for role, found in ROLES.items():
        if not found.responds("focus"):
            continue
        plain = skin.resolve(role)
        focused = skin.resolve(role, "focus")
        assert contrast(plain.border, focused.border) > 0.04, (theme, role)
        assert contrast(focused.border, edge) < contrast(plain.border, edge), \
            (theme, role)


def test_disabled_washes_towards_grey_and_fades_the_label_too(dark):
    plain = dark.resolve("button")
    out = dark.resolve("button", "disabled")
    faint = THEMES["dark"].colour("faint")
    assert out.fill == mix(plain.fill, faint, 0.38)
    assert contrast(out.fill, faint) < contrast(plain.fill, faint)
    assert contrast(plain.ink, out.ink) > 0.04
    assert out.ink == mix(plain.ink, out.fill, 0.55)


@pytest.mark.parametrize("word", ["nonsense", "dragged", "spinning", ""])
def test_a_state_nobody_defined_is_the_part_drawn_normally(dark, word):
    found = dark.resolve("button", word)
    assert found.state == "normal"
    assert found.fill == dark.resolve("button").fill


def test_a_value_and_an_action_are_two_halves_of_one_state(every_theme):
    skin = every_theme
    on = skin.resolve("toggle", "on")
    both = skin.resolve("toggle", "on hover")
    assert both.state == "on hover"
    # the answer to "on" is not the answer to "on hover", because the toggle is
    # still lit *and* still under the pointer
    assert contrast(on.fill, both.fill) > 0.04


def test_the_two_halves_of_a_state_can_be_asked_in_either_order(dark):
    assert (dark.resolve("toggle", "on hover")
            is dark.resolve("toggle", "hover on"))
    assert dark.resolve("toggle", "ON Hover").state == "on hover"
    assert dark.resolve("toggle", "on+focus").state == "on focus"
    assert dark.resolve("toggle", "selected/on").state == "selected"
    assert dark.resolve("toggle", "selected/focus").state == "selected focus"


def test_a_part_that_cannot_be_on_does_not_claim_it_was(dark):
    found = dark.resolve("panel", "on hover")
    assert found.state == "hover", "a panel that is 'on' is a lie"
    assert found.fill == dark.resolve("panel").fill


def test_a_part_that_is_on_shows_its_on_token_and_its_own_mark(dark):
    accent = THEMES["dark"].colour("accent")
    on = dark.resolve("toggle", "on")
    assert on.fill == accent
    assert on.ink == THEMES["dark"].colour("ink"), "the label must read on accent"
    assert on.border == accent, "an active toggle is one shape, not one with a box"
    assert on.glyph == "power"


def test_a_checkbox_shows_a_different_mark_for_each_of_its_two_values(dark):
    assert dark.resolve("checkbox").glyph == "minus"
    assert dark.resolve("checkbox", "on").glyph == "plus"
    assert dark.resolve("checkbox", "on hover").glyph == "plus"
    assert dark.glyph("checkbox") == "minus"
    assert dark.glyph("checkbox", True) == "plus"
    assert dark.glyph("track") == "", "a track shows nothing in its middle"


def test_a_value_and_a_disable_are_also_two_halves(dark):
    on = dark.resolve("toggle", "on")
    dead = dark.resolve("toggle", "on disabled")
    assert dead.state == "on disabled"
    assert dead.fill != on.fill
    assert dead.fill == mix(on.fill, THEMES["dark"].colour("faint"), 0.38)


@pytest.mark.parametrize("state", STATES)
def test_the_plain_state_words_are_all_the_vocabulary_says_they_are(dark, state):
    assert state in state_names()
    assert dark.resolve("button", state).state in (state, "normal")


def test_the_state_vocabulary_is_the_words_plus_every_pair(dark):
    pairs = {f"{value} {action}" for value in VALUES for action in ACTIONS}
    assert pairs <= set(state_names())
    assert len(state_names()) == len(STATES) + len(pairs)
    assert dark.states() == state_names()


def test_two_actions_at_once_is_the_last_one_asked_for(dark):
    assert dark.resolve("button", "focus hover").state == "hover"
    assert dark.resolve("button", "hover focus").state == "focus"


def test_asking_the_same_question_twice_is_the_same_object(dark):
    assert (dark.resolve("button", "on hover")
            is dark.resolve("button", "hover on"))


# -- size ------------------------------------------------------------------


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_the_size_scale_moves_the_metrics_and_not_the_colours(theme):
    small, big = Skin(theme, "sm"), Skin(theme, "lg")
    assert small.resolve("button").fill == big.resolve("button").fill
    for part in ("button", "field", "tooltip", "title", "hint"):
        assert small.resolve(part).font < big.resolve(part).font, part
        assert small.resolve(part).padding <= big.resolve(part).padding, part


def test_the_size_rungs_are_ordered_for_every_role(every_theme):
    for role in ROLES:
        fonts = [every_theme.with_scale(name).resolve(role).font
                 for name in sorted(SIZES, key=lambda rung: SIZES[rung])]
        assert fonts == sorted(fonts), role
        assert fonts[len(fonts) // 2] == pytest.approx(
            every_theme.resolve(role).font)


def test_radius_full_is_a_ceiling_as_well_as_a_size(dark):
    for name, factor in SIZES.items():
        rung = dark.with_scale(name)
        assert rung.resolve("track").radius == min(999.0 * factor, 999.0), name
        assert rung.resolve("button").radius <= rung.resolve("track").radius


def test_a_font_never_scales_below_the_pixel_it_has_to_draw_on():
    tiny = Skin("dark", "xs", overrides={"font.micro": 0.4})
    assert tiny.resolve("hint").font == 1.0


def test_a_border_never_scales_below_nothing():
    thin = Skin("dark", "xs", overrides={"border.thick": -2.0})
    assert thin.resolve("switch").width == 0.0


def test_flat_is_the_dark_palette_with_the_corners_taken_off(dark, flat):
    assert {role: flat.resolve(role).radius for role in ROLES} == {
        role: 0.0 for role in ROLES}
    assert flat.resolve("button").radius == 0.0
    assert dark.resolve("button").radius > 0.0
    assert flat.resolve("switch").radius == 0.0
    assert flat.theme.colour("bg") == dark.theme.colour("bg")


# -- overrides -------------------------------------------------------------


def test_an_override_beats_the_theme_for_a_colour_and_for_a_number(dark):
    poked = Skin("dark", overrides={"accent": "#ff0000", "radius.md": 30.0})
    assert poked.resolve("button", "on").fill == as_color("#ff0000")
    assert poked.resolve("button").radius == 30.0
    assert poked.tokens()["accent"] == "#ff0000ff"
    assert poked.metrics()["radius.md"] == 30.0
    # and the theme it was built over is untouched
    assert dark.resolve("button", "on").fill != as_color("#ff0000")


def test_a_number_override_reaches_the_states_that_derive_from_the_colour(dark):
    wide = Skin("dark", overrides={"border.medium": 5.0})
    assert wide.resolve("checkbox").width == 5.0
    assert dark.resolve("checkbox").width == 2.0
    assert dark.resolve("button").width == 1.0, "the default is the thin rule"


def test_an_override_survives_a_theme_change_and_a_size_change():
    base = Skin("dark", "sm", overrides={"accent": "#ff0000"})
    moved = base.with_theme("light")
    grown = base.with_scale("lg")
    assert moved.resolve("toggle", "on").fill == as_color("#ff0000")
    assert grown.resolve("toggle", "on").fill == as_color("#ff0000")
    assert moved.scale_name == "sm" and grown.theme.name == "dark"
    assert base.scale_name == "sm", "with_* copies rather than rewrites"


def test_a_theme_a_project_wrote_gets_the_right_hover_direction():
    paper = replace(THEMES["light"], name="paper")
    skin = Skin(paper)
    assert skin.theme.name == "paper"
    assert contrast(skin.resolve("button").fill,
                    skin.resolve("button", "hover").fill) > 0.04
    assert (luminance(skin.resolve("button", "hover").fill)
            < luminance(skin.resolve("button").fill))


def test_an_override_is_a_token_and_not_a_role():
    # {"button": "#ff0000"} is a project trying to use the skin as a stylesheet;
    # it resolves to a token nobody asks for, so it must not change the button
    odd = Skin("dark", overrides={"button": "#ff0000"})
    assert odd.resolve("button").fill == Skin("dark").resolve("button").fill


# -- the digest, which is what a painter caches on -------------------------


def test_two_skins_built_the_same_way_have_one_digest(dark):
    assert dark.digest == Skin("dark", "md").digest
    assert len(dark.digest) == 12
    assert dark == Skin("dark")
    assert {dark, Skin("dark")} == {dark}, "equal skins are one dict key"


@pytest.mark.parametrize("other", [
    Skin("light"),
    Skin("flat"),
    Skin("dark", "lg"),
    Skin("dark", "xs"),
    Skin("dark", overrides={"accent": "#ff0000"}),
    Skin("dark", overrides={"radius.sm": 6.0}),
    Skin(replace(THEMES["dark"], name="darker"),
         overrides={"bg": "#101010"}),
])
def test_a_skin_that_can_answer_one_question_differently_has_its_own_digest(dark,
                                                                           other):
    assert dark.digest != other.digest
    assert dark != other


def test_the_order_the_overrides_came_in_is_not_part_of_the_digest(dark):
    one = Skin("dark", overrides={"accent": "#ff0000", "radius.md": 3.0})
    two = Skin("dark", overrides={"radius.md": 3.0, "accent": "#ff0000"})
    assert one.digest == two.digest


def test_a_painter_caches_on_the_digest_and_a_theme_change_misses_it():
    cache: dict = {}

    def paint(skin: Skin):
        if skin.digest not in cache:
            cache[skin.digest] = [str(skin.resolve(role, state))
                                  for role in ROLES for state in STATES]
        return cache[skin.digest]

    first = paint(Skin("dark"))
    assert paint(Skin("dark")) is first, "the same skin is a cache hit"
    assert paint(Skin("light")) is not first, "a theme switch repaints"
    assert len(cache) == 2


def test_a_digest_change_that_moves_one_shade_is_still_a_change(dark):
    near = Skin("dark", overrides={"accent": "#47adfe"})
    assert near.digest != dark.digest
    assert contrast(near.resolve("toggle", "on").fill,
                    dark.resolve("toggle", "on").fill) > 0.0


# -- describing itself -----------------------------------------------------


def test_the_palette_reports_the_way_a_colour_prints(dark):
    found = dark.tokens()
    assert set(found) == set(COLLOUR_TOKENS)
    assert found["accent"] == "#47adffff"
    assert as_color(found["accent"]) == THEMES["dark"].colour("accent")
    for token, value in found.items():
        assert value.startswith("#") and len(value) == 9, token


def test_the_numbers_stay_numbers(dark):
    found = dark.metrics()
    assert set(found) == set(NUMBER_TOKENS)
    assert found["radius.full"] == 999.0
    assert found["font.body"] == 13.0
    assert all(isinstance(value, float) for value in found.values())


def test_the_metrics_are_reported_before_the_size_scale_they_apply():
    assert Skin("dark", "lg").metrics() == Skin("dark", "xs").metrics()
    assert Skin("dark").tokens() == Skin("dark", "lg").tokens()


def test_the_document_has_every_role_in_every_plain_state(dark):
    found = dark.as_dict()
    assert sorted(found) == ["digest", "factor", "metrics", "overrides",
                             "roles", "scale", "theme", "tokens"]
    assert found["theme"] == "dark" and found["scale"] == "md"
    assert found["factor"] == 1.0 and found["digest"] == dark.digest
    assert set(found["roles"]) == set(ROLES)
    assert sorted(found["roles"]["button"]) == sorted(STATES)
    assert found["roles"]["toggle"]["on"]["glyph"] == "power"
    assert found["roles"]["button"]["on"]["glyph"] == ""
    assert set(found["roles"]["button"]["hover"]) >= {"fill", "ink", "border",
                                                      "radius", "font"}


def test_overrides_are_written_out_as_the_project_wrote_them():
    found = Skin("dark", overrides={"accent": "#ff0000"}).as_dict()
    assert found["overrides"] == {"accent": "#ff0000"}


def test_describe_shows_the_row_and_the_words_that_mean_it():
    found = describe("toggle")
    assert found["role"] == "toggle"
    assert found["fill"] == "raised" and found["on"] == "accent"
    assert found["glyph"] == "power" and found["glyph_on"] == "power"
    assert set(found["states"]) == set(ACTIONS)
    assert found["aliases"] == ["button-toggle"]
    assert describe("danger")["on"] is None
    assert describe("track")["glyph"] is None


def test_describe_follows_an_alias_to_the_row_it_points_at():
    assert describe("slider") == describe("track")
    assert describe("SUCCESS") == describe("ok")
    with pytest.raises(UIError, match="not a style role"):
        describe("nonsense")


def test_everything_about_a_skin_says_what_it_is(dark):
    assert "dark" in str(dark) and dark.digest in str(dark)
    assert "33 roles" in str(dark)
    said = str(Skin("dark", overrides={"bg": "#000000", "ink": "#ffffff"}))
    assert "+2" in said, "an overridden skin says so rather than looking plain"
    assert "+" not in str(dark)
    found = dark.resolve("button", "hover")
    assert str(found).startswith("button/hover")
    assert "fill" in str(found) and "ink" in str(found)
    assert "Theme(dark" in str(THEMES["dark"])


def test_the_names_the_package_lists_are_the_tables_it_built(dark):
    assert dark.names() == tuple(ROLES)
    assert colour_token_names() == COLLOUR_TOKENS
    assert number_token_names() == NUMBER_TOKENS
    assert set(role_names()) >= set(ROLES)
    assert theme_names() == tuple(THEMES)


# -- the package -----------------------------------------------------------


def test_the_skin_is_reachable_without_a_display():
    import crude.ui

    assert crude.ui.Skin is Skin
    assert crude.ui.THEMES is THEMES
    assert crude.ui.Role is Role and crude.ui.Theme is Theme
    assert crude.ui.describe("button")["role"] == "button"


def test_an_appearance_is_the_answer_to_the_whole_question_about_one_part(dark):
    # this is the object a widget's paint is built from: bg, border, glyph and
    # label at once, so the widget does four draws and makes no decisions
    found = dark.resolve("button", "on hover")
    assert found.as_dict()["role"] == "button"
    assert found.as_dict()["state"] == "on hover"
    assert set(found.as_dict()) == {"role", "state", "fill", "ink", "border",
                                    "radius", "width", "padding", "font",
                                    "glyph"}


def test_a_resolved_appearance_cannot_be_edited_underneath_the_skin(dark):
    found = dark.resolve("button")
    with pytest.raises(AttributeError):
        found.fill = RGBA(0.0, 0.0, 0.0, 1.0)
