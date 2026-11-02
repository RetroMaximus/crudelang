"""Where every box comes from: place, pack, anchor, stack, and the device grid.

:mod:`crude.ui.layout` is the arithmetic half of the plan's S7 acceptance line -
"anchor/DPI/z-order/hit-test math tests" - and it is all arithmetic, so every
number asserted here is written down as a number rather than compared against a
screenshot. Three things are worth the trouble because each is easy to get quietly
wrong and invisible when you do:

* a stack measures its children against the container's **content** box, so a
  panel's padding moves both where the row starts and where it is cut off;
* ``grow`` is a fraction of a container, so the *reference* has to be remembered
  and re-taken when a script places the widget again - a layout that scaled from
  the current size every frame would compound and run away;
* the pass runs in logical pixels, so a ``1.25`` display scale cannot make a row
  of stacked buttons gain a hairline of background between two of them;
* a glue owns the axis it names, because a row that re-asserted its own answer
  every frame would put the widget back and the glue would move it again - two
  box writes and two paint keys on a window that did nothing at all.

The verbs are tested twice over on purpose: once that they *record* what they were
told, and once that the next pass *acts* on it. A ``PACK`` call that stored nothing
and a ``PACK`` call that stored the wrong word both look identical until a resize
gives them an answer to be wrong about.
"""

from __future__ import annotations

from typing import Any, List

import pytest

from crude.ui import (AXIS, DEFAULT_GAP, EDGES, LAYOUT_TOKENS, Layout,
                      LayoutError, PACK_POLICIES, Panel, RecordingRenderer,
                      STACK_ALIGNMENTS, STACK_DIRECTIONS, TOLERANCE, Anchor,
                      Skin, Stack, Widget, anchor, anchors_of, arrange,
                      as_alignment, as_direction, as_edge, as_policy,
                      clear_layout, describe_layout, grid, hstack, layout_of,
                      metric, pack, pack_of, pad, place, resolve, root_panel,
                      snap, snap_box, stack, top_at, unanchor, unstack, vstack)

#: the panel inset the dark skin measures: ``pad.md`` plus a ``border.thin``
INSET = 9.0

#: the gap a stack gets when the script named none, at the ``md`` size
GAP = 8.0


@pytest.fixture
def skin() -> Skin:
    return Skin("dark")


@pytest.fixture
def root(skin) -> Widget:
    """The plan's 360 x 220 client rect, resolved once so its box is real."""
    held = root_panel(360, 220)
    held.sync(skin)
    return held


def card(parent: Widget, name: str, box=(16, 16, 200, 100),
         kind: Any = "panel") -> Widget:
    """A panel, placed where a script would have placed it."""
    held = Panel({"label": name}, name=name, kind=kind, parent=parent)
    held.place(*box)
    return held


def thing(parent: Widget, name: str, box=(0, 0, 60, 20),
          kind: Any = "button") -> Widget:
    """A leaf at a size - the thing a row or a grid arranges."""
    held = Widget(kind, {"label": name}, name=name, parent=parent)
    held.place(*box)
    return held


def row(parent: Widget, *names: str, size=(60, 20)) -> List[Widget]:
    """Several leaves in z-order, all the same size, at the row's start."""
    return [thing(parent, word, (0, 0, *size)) for word in names]


def spot(held: Widget) -> tuple:
    """The absolute box, to three places: what a test compares against a number."""
    return tuple(round(value, 3) for value in held.screen_box)


def rel(held: Widget) -> tuple:
    """The box as the pass left it, relative to the parent's content box."""
    return tuple(round(value, 3) for value in held.box)


def laid(root: Widget, skin: Skin, **size: Any) -> int:
    """One pass, with the moved count handed back for the tests that check it."""
    return resolve(root, skin, **size)


# -- what the verbs record --------------------------------------------------


def test_a_widget_nobody_told_where_to_go_carries_no_policy(root, skin):
    held = thing(root, "plain")
    assert held.layout is None
    assert layout_of(held) is held.layout, "asking creates it, once"
    assert held.layout is not None
    assert pack_of(held) == ("fixed", "fixed")
    assert anchors_of(held) == ()
    assert describe_layout(held) == {"pack": ["fixed", "fixed"]}
    assert str(held.layout) == "Layout(pack fixed/fixed)"


def test_clearing_the_policy_leaves_the_box_alone_and_the_skin_in_charge(root):
    held = card(root, "deck")
    pack(held, "fill")
    pad(held, 30)
    assert describe_layout(held)["pad"] == 30.0
    clear_layout(held)
    assert held.layout is None
    assert held.width == 200.0, "a forgotten policy does not resize anything"


def test_one_pack_word_means_both_axes_and_two_words_mean_one_each(root):
    held = thing(root, "s")
    assert pack(held, "fill") == ("fill", "fill")
    assert pack(held, "grow", "fixed") == ("grow", "fixed")
    assert pack_of(held) == ("grow", "fixed")
    assert held.layout.axis("x") == "grow" and held.layout.axis("y") == "fixed"


@pytest.mark.parametrize("word, policy", [
    ("expand", "fill"), ("stretch", "fill"), ("scale", "grow"),
    ("proportional", "grow"), ("keep", "fixed"), ("static", "fixed"),
    ("NONE", "fixed"),
])
def test_the_words_a_script_reaches_for_are_the_three_policies(root, word,
                                                              policy):
    assert pack(thing(root, "w"), word) == (policy, policy)


def test_a_policy_that_is_not_one_of_the_three_lists_the_three(root):
    with pytest.raises(LayoutError) as raised:
        pack(thing(root, "w"), "bogus")
    assert "'bogus' is not a pack policy (known: " in str(raised.value)
    assert ", ".join(PACK_POLICIES) in str(raised.value)


def test_an_anchor_remembers_the_edge_the_target_and_the_offset(root):
    left = card(root, "left", (16, 16, 100, 40))
    right = thing(root, "right", (0, 0, 40, 20))
    held = anchor(right, "right", left, "right", -16)
    assert held == Anchor("right", left, "right", -16.0)
    assert held.axis == "x"
    assert anchors_of(right) == (held,)
    assert held.as_dict() == {"edge": "right", "target": "root/left",
                             "target_edge": "right", "offset": -16.0}
    assert str(held) == "right -> root/left.right -16"
    assert describe_layout(right)["anchors"] == [held.as_dict()]


def test_naming_the_same_edge_twice_replaces_the_glue_rather_than_adding_it(root):
    left = card(root, "left", (16, 16, 100, 40))
    right = thing(root, "right")
    anchor(right, "right", left, "right", -16)
    anchor(right, "RIGHT", left, "right", -20)
    assert len(anchors_of(right)) == 1
    assert anchors_of(right)[0].offset == -20.0


def test_an_anchor_on_the_parent_is_stored_as_no_target_so_a_move_follows(root):
    box = card(root, "deck")
    held = thing(box, "in")
    anchor(held, "bottom")
    assert anchors_of(held)[0].target is None
    assert str(anchors_of(held)[0]) == "bottom -> parent.bottom"
    # and the same rule now reads the new panel's edge, not the old one's
    held.detach()
    root.add(held)
    laid(root, Skin("dark"))
    assert spot(held)[1] + spot(held)[3] == 220.0


@pytest.mark.parametrize("word", ["parent", "PARENT", "own", "", None])
def test_the_word_parent_means_what_leaving_the_target_out_means(root, word):
    box = card(root, "deck")
    held = thing(box, "in")
    assert anchor(held, "right", word).target is None


def test_an_anchor_needs_something_to_glue_to(root):
    loose = Widget("button", name="loose")
    with pytest.raises(LayoutError) as raised:
        anchor(loose, "right")
    assert "has no parent to anchor to" in str(raised.value)


def test_a_widget_cannot_be_glued_to_itself_or_to_an_outsider(root):
    held = thing(root, "a")
    with pytest.raises(LayoutError) as raised:
        anchor(held, "left", held)
    assert "cannot be anchored to itself" in str(raised.value)
    elsewhere = root_panel(10, 10, name="other")
    thing(elsewhere, "b")
    with pytest.raises(LayoutError) as raised:
        anchor(held, "left", elsewhere.find("b"))
    assert "not in the same tree" in str(raised.value)


def test_unanchor_takes_one_glue_or_every_one(root):
    left = card(root, "left")
    right = thing(root, "right")
    anchor(right, "right", left)
    anchor(right, "bottom", left)
    assert unanchor(right, "right") == 1
    assert len(anchors_of(right)) == 1
    assert unanchor(right) == 1
    assert anchors_of(right) == ()
    assert unanchor(right, "left") == 0, "nothing was glued that way to begin with"
    assert unanchor(Widget("button")) == 0, "a widget with no policy has no glues"


@pytest.mark.parametrize("edge", EDGES)
def test_every_edge_names_the_axis_it_moves(root, edge):
    box = card(root, "deck")
    held = thing(box, "in")
    assert anchor(held, edge).axis == AXIS[edge]


@pytest.mark.parametrize("word", ("l", "r", "t", "b", "cx", "cy", "middle",
                                  "bottom", "CENTER"))
def test_an_edge_spelled_shortly_is_the_edge_it_means(root, word):
    held = thing(card(root, "deck"), "in")
    rule = anchor(held, word)
    assert rule.edge in EDGES
    assert rule.target_edge == rule.edge, "the other side follows the same word"


def test_an_edge_that_is_not_an_edge_lists_the_six_that_are(root):
    with pytest.raises(LayoutError) as raised:
        anchor(thing(card(root, "deck"), "in"), "sideways")
    assert "is not an edge (known: " in str(raised.value)


@pytest.mark.parametrize("direction", STACK_DIRECTIONS)
@pytest.mark.parametrize("align", STACK_ALIGNMENTS)
def test_a_stack_is_one_frozen_document_of_the_words_it_was_given(root,
                                                                 direction,
                                                                 align):
    box = card(root, "deck")
    held = stack(box, direction, gap=6, pad=2, align=align, columns=3)
    assert held == Stack(direction, 6, 2, align, 3)
    assert box.layout.stack is held
    assert held.as_dict() == {"direction": direction, "gap": 6, "pad": 2,
                             "align": align, "columns": 3}
    assert str(held).startswith(f"Stack({direction}, gap=6")
    assert describe_layout(box)["stack"] == held.as_dict()


def test_a_stack_defaults_its_gap_and_aligns_a_grid_by_the_middle(root):
    box = card(root, "deck")
    held = stack(box, "h")
    assert (held.direction, held.gap, held.align, held.pad) == (
        "horizontal", DEFAULT_GAP, "start", None)
    assert grid(box).align == "center"
    assert grid(box).direction == "grid"


def test_arranging_something_that_holds_no_children_says_which_kind_it_is(root):
    knob = thing(root, "s", kind="slider")
    with pytest.raises(LayoutError) as raised:
        hstack(knob, 4)
    assert "holds no children" in str(raised.value)


@pytest.mark.parametrize("verb", (place, pack, pad, hstack, grid, anchor))
def test_a_verb_that_was_handed_a_number_hears_about_it(root, verb):
    with pytest.raises(LayoutError) as raised:
        verb(7, 1)
    assert "is not a widget" in str(raised.value)
    assert "int" in str(raised.value), "the message names what did arrive"


def test_arrange_takes_the_same_words_as_stack_from_one_document(root):
    box = card(root, "deck")
    held = arrange(box, {"direction": "vertical", "gap": "sm", "align": "end"})
    assert (held.direction, held.gap, held.align) == ("vertical", "sm", "end")
    with pytest.raises(LayoutError):
        arrange(box, ["vertical", 4])


def test_stopping_the_arrangement_leaves_the_children_where_they_were(root, skin):
    box = card(root, "deck")
    a, b = row(box, "a", "b")
    hstack(box, 8)
    laid(root, skin)
    before = (rel(a), rel(b))
    assert unstack(box) is True
    assert unstack(box) is False, "there was only one arrangement to stop"
    assert laid(root, skin) == 0
    assert (rel(a), rel(b)) == before


# -- what the metrics mean --------------------------------------------------


def test_a_number_of_pixels_is_already_a_length(skin):
    assert metric(skin, 6) == 6.0
    assert metric(skin, 0) == 0.0
    assert metric(skin, -4) == 0.0, "a negative gap is no gap"


@pytest.mark.parametrize("token, length", [
    ("pad.none", 0.0), ("none", 0.0), ("xs", 2.0), ("sm", 4.0), ("md", 8.0),
    ("pad.lg", 12.0),
])
def test_a_token_is_the_skin_s_own_number(skin, token, length):
    assert metric(skin, token) == length


@pytest.mark.parametrize("name, expected", [
    ("sm", 3.4), ("md", 6.8), ("pad.lg", 10.2),
])
def test_a_token_scales_with_the_size_the_skin_is_at(name, expected):
    assert metric(Skin("dark", "sm"), name) == pytest.approx(expected)


def test_a_token_follows_a_theme_that_overrode_it():
    said = Skin("dark", overrides={"pad.md": 20.0})
    assert metric(said, "md") == 20.0
    assert metric(said, "pad.md") == 20.0


@pytest.mark.parametrize("word", ["radius.md", "border.thin", "font.body",
                                  "surface", "wide"])
def test_only_the_numbers_that_mean_a_distance_can_be_named(skin, word):
    with pytest.raises(LayoutError) as raised:
        metric(skin, word)
    assert "is not a length" in str(raised.value)
    assert ", ".join(LAYOUT_TOKENS) in str(raised.value)


def test_a_yes_or_no_is_never_a_number_of_pixels(skin):
    with pytest.raises(LayoutError) as raised:
        metric(skin, True)
    assert "use a number of pixels or a metric token" in str(raised.value)


@pytest.mark.parametrize("word", ["", None])
def test_naming_no_length_at_all_says_there_is_no_such_thing(skin, word):
    with pytest.raises(LayoutError) as raised:
        metric(skin, word)
    assert "there is no length named" in str(raised.value)


def test_a_token_answer_is_cached_per_skin_so_an_override_cannot_be_stale():
    plain = Skin("dark")
    held = plain.digest
    assert metric(plain, "md") == 8.0
    over = Skin("dark", overrides={"pad.md": 20.0})
    assert over.digest != held, "an override is a different skin"
    assert metric(over, "md") == 20.0
    assert metric(plain, "md") == 8.0, "the cache did not mix the two up"


# -- the first pass ---------------------------------------------------------


def test_a_pass_lays_out_the_tree_and_reports_the_boxes_it_wrote(root, skin):
    box = card(root, "deck")
    a, b, c = row(box, "a", "b", "c")
    hstack(box, 8)
    assert laid(root, skin) == 2, "two of the three had to move"
    assert rel(a) == (0.0, 0.0, 60.0, 20.0)
    assert rel(b) == (68.0, 0.0, 60.0, 20.0)
    assert rel(c) == (136.0, 0.0, 60.0, 20.0)
    assert spot(a) == (25.0, 25.0, 60.0, 20.0), "the content box, not the screen"
    assert spot(c) == (161.0, 25.0, 60.0, 20.0)


def test_a_second_pass_on_an_unchanged_window_writes_nothing(root, skin):
    box = card(root, "deck")
    row(box, "a", "b")
    hstack(box, 8)
    pack(row(box, "c")[0], "fill")
    laid(root, skin)
    assert laid(root, skin) == 0
    assert laid(root, skin) == 0


def test_a_pass_can_be_the_thing_that_resizes_the_window(root, skin):
    bar = thing(root, "bar", (0, 0, 360, 20))
    anchor(bar, "right")
    anchor(bar, "bottom")
    laid(root, skin)
    assert spot(bar) == (0.0, 200.0, 360.0, 20.0)
    laid(root, skin, width=720, height=440)
    assert (root.width, root.height) == (720.0, 440.0)
    assert spot(bar) == (360.0, 420.0, 360.0, 20.0)
    assert spot(bar)[2] == 360.0, "only the corner was glued, so only it moved"


def test_a_pass_needs_a_skin_because_padding_is_a_skins_fact(root):
    with pytest.raises(LayoutError) as raised:
        resolve(root, None)
    assert "needs the skin" in str(raised.value)
    with pytest.raises(LayoutError) as raised:
        resolve(root, card(root, "deck"))
    assert "not a Panel" in str(raised.value), "the panel is not a skin"


def test_a_widget_with_no_policy_is_left_exactly_where_it_was_placed(root, skin):
    held = thing(root, "still", (40, 50, 20, 10))
    card(root, "deck")
    assert laid(root, skin) == 0
    assert spot(held) == (40.0, 50.0, 20.0, 10.0)


def test_place_is_the_parents_content_box_so_the_root_is_the_window(root, skin):
    title = thing(root, "title", (16, 12, 200, 20), kind="text")
    box = card(root, "deck")
    in_box = thing(box, "in", (16, 12, 20, 20))
    laid(root, skin)
    assert spot(title) == (16.0, 12.0, 200.0, 20.0)
    assert spot(in_box)[0] == 16.0 + 16.0 + INSET, "the plan's two ways of placing"
    assert place(title, y=30) is True
    assert place(title, y=30) is False


# -- what a pack policy does to a box ---------------------------------------


def test_the_default_is_to_keep_the_size_the_script_gave_it(root, skin):
    box = card(root, "deck")
    kid = thing(box, "a")
    laid(root, skin)
    laid(root, skin, width=900, height=600)
    assert rel(kid) == (0.0, 0.0, 60.0, 20.0)
    assert spot(kid) == (25.0, 25.0, 60.0, 20.0)


def test_a_fill_runs_from_where_it_stands_to_the_end_of_the_container(root,
                                                                     skin):
    bar = thing(root, "bar", (20, 20, 100, 40))
    pack(bar, "fill")
    assert laid(root, skin) == 1
    assert spot(bar) == (20.0, 20.0, 340.0, 200.0)
    assert spot(bar)[0] + spot(bar)[2] == 360.0, "the far edge lands on the far"
    assert spot(bar)[1] + spot(bar)[3] == 220.0
    assert laid(root, skin) == 0


def test_a_filler_inside_a_panel_stops_at_the_padding_not_the_edge(root,
                                                                  skin):
    box = card(root, "deck")
    bar = thing(box, "bar", (0, 0, 10, 10))
    pack(bar, "fill")
    laid(root, skin)
    assert rel(bar) == (0.0, 0.0, 182.0, 82.0)
    assert spot(bar) == (25.0, 25.0, 182.0, 82.0)


def test_grow_is_a_fraction_of_the_container_and_does_not_compound(root,
                                                                   skin):
    box = card(root, "deck", (16, 16, 218, 100))       # content 200 x 82
    kid = thing(box, "s", (0, 0, 100, 20))
    pack(kid, "grow", "fixed")
    assert laid(root, skin) == 0, "the first pass only takes the reference"
    assert kid.width == 100.0
    box.place(None, None, 418, None)                   # content 400
    laid(root, skin)
    assert kid.width == 200.0
    assert kid.height == 20.0, "the other axis was packed fixed"
    box.place(None, None, 818, None)                   # content 800
    laid(root, skin)
    assert kid.width == 400.0, "twice the container, not twice the last answer"


def test_a_place_between_two_frames_re_takes_the_fraction(root, skin):
    box = card(root, "deck", (16, 16, 218, 100))
    kid = thing(box, "s", (0, 0, 100, 20))
    pack(kid, "grow", "fixed")
    laid(root, skin)
    kid.place(None, None, 50, None)
    laid(root, skin)
    assert kid.width == 50.0, "the number the script wrote is the new reference"
    box.place(None, None, 418, None)
    laid(root, skin)
    assert kid.width == 100.0


def test_grow_needs_a_container_with_a_length_to_be_measured_at_all(root,
                                                                    skin):
    box = card(root, "deck", (16, 16, 18, 18))          # no content left
    kid = thing(box, "s", (0, 0, 60, 20))
    pack(kid, "grow", "fixed")
    laid(root, skin)
    assert kid.width == 60.0
    assert kid.layout.reference("x") is None, "nothing was measured against 0 px"
    box.place(None, None, 218, 118)                      # content 200 x 100
    laid(root, skin)
    assert kid.width == 60.0, "a pass that takes its reference does not resize"
    box.place(None, None, 418, 218)                      # content 400 x 200
    laid(root, skin)
    assert kid.width == 120.0


def test_one_axis_can_fill_while_the_other_keeps_its_size(root, skin):
    box = card(root, "deck", (16, 16, 218, 100))
    kid = thing(box, "s", (0, 0, 100, 50))
    pack(kid, "fill", "fixed")
    laid(root, skin)
    assert rel(kid) == (0.0, 0.0, 200.0, 50.0)
    box.place(None, None, 418, 218)
    laid(root, skin)
    assert rel(kid) == (0.0, 0.0, 400.0, 50.0)


def test_two_fillers_split_what_the_fixed_children_left(root, skin):
    box = card(root, "deck")                            # content 182 x 82
    a, b, c, d = row(box, "a", "b", "c", "d")
    hstack(box, gap=0)
    pack(b, "fill")
    pack(d, "fill")
    laid(root, skin)
    assert [rel(k)[0] for k in (a, b, c, d)] == [0.0, 60.0, 91.0, 151.0]
    assert [rel(k)[2] for k in (a, b, c, d)] == [60.0, 31.0, 60.0, 31.0]


def test_a_grower_is_measured_before_the_filler_gets_what_is_left(root, skin):
    box = card(root, "deck")
    a, b = row(box, "a", "b")
    hstack(box, 8)
    pack(a, "grow", "fixed")
    pack(b, "fill")
    laid(root, skin)
    assert rel(a) == (0.0, 0.0, 60.0, 20.0)
    assert (rel(b)[0], rel(b)[2]) == (68.0, 114.0)
    box.place(None, None, 400, 100)                     # content 382 x 82
    laid(root, skin)
    grown = 60 * 382 / 182
    assert a.width == pytest.approx(grown)
    assert rel(b)[2] == pytest.approx(382 - 8 - grown)
    assert spot(b)[0] == pytest.approx(25 + grown + 8)


# -- rows, columns and cells ------------------------------------------------


def test_a_row_is_the_children_in_z_order_and_a_raise_moves_the_end(root,
                                                                    skin):
    box = card(root, "deck")
    a, b, c = row(box, "a", "b", "c")
    hstack(box, 8)
    laid(root, skin)
    assert [rel(k)[0] for k in (a, b, c)] == [0.0, 68.0, 136.0]
    a.raise_to_top()
    laid(root, skin)
    assert box.children[-1] is a
    assert [rel(k)[0] for k in (b, c, a)] == [0.0, 68.0, 136.0]


@pytest.mark.parametrize("align, top, height", [
    ("start", 0.0, 40.0), ("center", 21.0, 40.0), ("end", 42.0, 40.0),
    ("stretch", 0.0, 82.0),
])
def test_a_child_sits_across_the_row_the_way_the_row_was_told(root, skin,
                                                             align, top,
                                                             height):
    box = card(root, "deck")                            # content 182 x 82
    kid = thing(box, "a", (0, 0, 60, 40))
    hstack(box, 8, align=align)
    laid(root, skin)
    assert rel(kid)[1] == top
    assert kid.height == height


def test_a_childs_own_cross_axis_policy_beats_the_rows_align(root, skin):
    box = card(root, "deck")
    kid = thing(box, "a", (0, 0, 60, 40))
    hstack(box, 8, align="center")
    pack(kid, "fixed", "fill")
    laid(root, skin)
    assert rel(kid) == (0.0, 0.0, 60.0, 82.0)


def test_the_rows_stretch_is_the_same_answer_as_the_childs_own_fill(root,
                                                                    skin):
    box = card(root, "deck")
    kid = thing(box, "a", (0, 0, 60, 40))
    vstack(box, 8, align="stretch")
    laid(root, skin)
    held = (kid.width, kid.height)
    assert held == (182.0, 40.0)
    unstack(box)
    vstack(box, 8)
    pack(kid, "fill", "fixed")
    laid(root, skin)
    assert (kid.width, kid.height) == held


def test_a_row_that_overflows_is_laid_out_and_the_panel_cuts_it(root, skin):
    box = card(root, "deck")
    a, b, c = row(box, "a", "b", "c")
    hstack(box, 8)
    laid(root, skin)
    assert (a.clip, b.clip) == ((25.0, 25.0, 60.0, 20.0),
                                (93.0, 25.0, 60.0, 20.0))
    assert c.clip == (161.0, 25.0, 46.0, 20.0)
    assert top_at(root, 200, 30) is c
    assert top_at(root, 210, 30) is box, "cut off is not clickable either"


def test_a_child_pushed_past_the_panel_is_nowhere_the_pointer_can_reach(root,
                                                                        skin):
    box = card(root, "deck", (16, 16, 100, 100))         # content 82 x 82
    kids = row(box, "a", "b", "c", "d", "e")
    hstack(box, 8)
    laid(root, skin)
    assert rel(kids[4])[0] == 272.0
    assert kids[4].clip == (297.0, 25.0, 0.0, 20.0)
    assert top_at(root, 300, 30) is root, "only the window is out there"


def test_a_grid_makes_every_cell_the_same_shape(root, skin):
    box = card(root, "deck")                            # content 182 x 82
    icons = row(box, "i", "j", "k", "l", size=(20, 20))
    grid(box, 2, gap=4)
    laid(root, skin)
    assert [rel(k)[:2] for k in icons] == [(34.5, 9.5), (127.5, 9.5),
                                           (34.5, 52.5), (127.5, 52.5)]
    for held in (icons[0], icons[3]):
        pack(held, "fill")
    laid(root, skin)
    assert rel(icons[0]) == (0.0, 0.0, 89.0, 39.0)
    assert rel(icons[3]) == (93.0, 43.0, 89.0, 39.0)


def test_an_auto_grid_is_as_square_as_the_child_count_allows(root, skin):
    box = card(root, "deck")
    icons = row(box, *[str(n) for n in range(6)], size=(20, 20))
    grid(box, 0, gap=0)
    laid(root, skin)
    cell_w, cell_h = 182 / 3, 82 / 2
    assert [round(rel(k)[0], 3) for k in icons] == [
        round((cell_w - 20) / 2 + column * cell_w, 3)
        for column in (0, 1, 2)
    ] * 2
    assert [rel(k)[1] for k in icons] == [(cell_h - 20) / 2] * 3 + \
        [cell_h + (cell_h - 20) / 2] * 3


def test_more_columns_than_children_collapse_to_one_row(root, skin):
    box = card(root, "deck")
    a, b, c = row(box, "a", "b", "c", size=(20, 20))
    grid(box, 10, gap=0)
    laid(root, skin)
    assert len({rel(k)[1] for k in (a, b, c)}) == 1, "one row of three cells"
    assert len({rel(k)[0] for k in (a, b, c)}) == 3
    assert [rel(k)[2] for k in (a, b, c)] == [20.0, 20.0, 20.0]
    assert round(rel(c)[0], 3) == round(2 * (182 / 3) + (182 / 3 - 20) / 2, 3)


def test_only_a_grid_centres_by_default(root, skin):
    box = card(root, "deck")
    assert hstack(box).align == "start"
    assert vstack(box).align == "start"
    assert grid(box).align == "center"


def test_a_nested_row_measures_its_own_panel_not_the_window(root, skin):
    outer = card(root, "deck", (16, 16, 218, 118))       # content 200 x 100
    vstack(outer, 10)
    inner = card(outer, "in", (0, 0, 100, 40))
    a, b = row(inner, "a", "b", size=(40, 20))
    hstack(inner, 4)
    laid(root, skin)
    assert spot(inner) == (25.0, 25.0, 100.0, 40.0)
    assert spot(a) == (34.0, 34.0, 40.0, 20.0)
    assert spot(b) == (78.0, 34.0, 40.0, 20.0)
    assert a.clip == (34.0, 34.0, 40.0, 20.0)
    assert b.clip == (78.0, 34.0, 38.0, 20.0), "the inner panel is the limit"


def test_a_padding_moves_both_where_the_row_starts_and_where_it_is_cut(root,
                                                                       skin):
    box = card(root, "deck")
    a, b = row(box, "a", "b")
    hstack(box, 8)
    laid(root, skin)
    assert spot(a) == (25.0, 25.0, 60.0, 20.0)
    assert pad(box, 40) == 40.0
    assert laid(root, skin) == 0, "the row moved with its panel, not on paper"
    assert spot(a) == (56.0, 56.0, 60.0, 20.0)
    assert spot(b) == (124.0, 56.0, 60.0, 20.0)
    assert b.clip == (124.0, 56.0, 52.0, 20.0)
    assert pad(box, None) == 0.0, "None means the skin's own answer again"
    laid(root, skin)
    assert spot(a) == (25.0, 25.0, 60.0, 20.0)
    assert pad(box, "lg") == 12.0
    laid(root, skin)
    assert spot(a) == (28.0, 28.0, 60.0, 20.0)


def test_a_stack_can_carry_its_own_padding_and_a_manual_pad_overrides_it(root,
                                                                         skin):
    box = card(root, "deck")
    a, _b = row(box, "a", "b")
    hstack(box, 8, pad=20)
    laid(root, skin)
    assert spot(a) == (36.0, 36.0, 60.0, 20.0)
    pad(box, 5)
    laid(root, skin)
    assert spot(a) == (21.0, 21.0, 60.0, 20.0)
    assert describe_layout(box)["stack"]["pad"] is None


def test_naming_a_pad_token_before_the_first_pass_says_so():
    bare = root_panel(200, 100, name="bare")
    box = card(bare, "deck")
    thing(box, "a")
    with pytest.raises(LayoutError) as raised:
        pad(box, "lg")
    assert "no skin has been resolved" in str(raised.value)
    assert pad(box, 12) == 12.0, "a number of pixels always works"


def test_a_gap_named_by_a_token_follows_the_skin_and_a_number_does_not(root):
    box = card(root, "deck")
    a, b = row(box, "a", "b")
    vstack(box, "md")
    laid(root, Skin("dark", "sm"))
    assert rel(b)[1] - (rel(a)[1] + rel(a)[3]) == pytest.approx(6.8)
    vstack(box, 8)
    laid(root, Skin("dark", "xl"))
    assert rel(b)[1] - (rel(a)[1] + rel(a)[3]) == 8.0


# -- glued edges ------------------------------------------------------------


def test_the_plans_own_example_parks_a_widget_inside_another_ones_edge(root,
                                                                       skin):
    box = card(root, "deck", (16, 16, 200, 100))
    badge = thing(root, "badge", (0, 0, 40, 20))
    anchor(badge, "right", box, "right", -16)
    anchor(badge, "bottom", box, "bottom", -4)
    laid(root, skin)
    assert spot(badge) == (160.0, 92.0, 40.0, 20.0)
    box.place(16, 16, 300, 100)
    laid(root, skin)
    assert spot(badge) == (260.0, 92.0, 40.0, 20.0), "the margin is the constant"


def test_a_glue_can_read_the_edge_the_script_named_rather_than_its_own(root,
                                                                       skin):
    left = card(root, "left", (16, 16, 100, 40))
    right = thing(root, "right", (0, 0, 40, 20))
    anchor(right, "left", left, "right", 8)
    laid(root, skin)
    assert spot(right)[0] == 124.0
    left.place(16, 16, 60, 40)
    laid(root, skin)
    assert spot(right)[0] == 84.0


def test_two_rules_on_one_axis_are_a_stretch_and_not_a_quarrel(root, skin):
    bar = thing(root, "bar", (0, 0, 100, 20))
    anchor(bar, "left", None, "left", 10)
    anchor(bar, "right", None, "right", -10)
    laid(root, skin)
    assert spot(bar) == (10.0, 0.0, 340.0, 20.0)
    laid(root, skin, width=560, height=220)
    assert spot(bar) == (10.0, 0.0, 540.0, 20.0)
    assert bar.height == 20.0, "no word was said about the other axis"


def test_a_glue_reads_the_space_a_parent_gives_and_a_fill_lands_on_it_too(root,
                                                                          skin):
    box = card(root, "deck")
    held = thing(box, "k", (0, 0, 50, 20))
    for edge in ("left", "right", "top", "bottom"):
        anchor(held, edge)
    laid(root, skin)
    assert spot(held) == (25.0, 25.0, 182.0, 82.0), "the content box, at no margin"
    other = thing(box, "o", (10, 10, 50, 20))
    pack(other, "fill")
    laid(root, skin)
    assert spot(other) == (35.0, 35.0, 172.0, 72.0)
    assert spot(other)[0] + spot(other)[2] == 25.0 + 182.0
    assert spot(other)[1] + spot(other)[3] == 25.0 + 82.0


def test_a_glue_to_a_sibling_reads_the_line_a_person_can_see(root, skin):
    star = card(root, "star", (100, 10, 60, 40))
    held = thing(root, "k", (0, 0, 20, 20))
    anchor(held, "center-x", star, "center-x")
    laid(root, skin)
    assert spot(held)[0] == 120.0, "the middle of the star's own box"


def test_centring_inside_the_panel_it_lives_in_uses_the_space_given(root, skin):
    box = card(root, "deck")
    held = thing(box, "a", (0, 0, 60, 20))
    anchor(held, "center-x")
    anchor(held, "middle")
    laid(root, skin)
    assert spot(held) == (86.0, 56.0, 60.0, 20.0)


def test_a_middle_rule_and_an_edge_rule_on_one_axis_is_a_question(root, skin):
    box = card(root, "deck")
    held = thing(box, "a")
    anchor(held, "center-x")
    anchor(held, "left")
    with pytest.raises(LayoutError) as raised:
        laid(root, skin)
    assert "glued to the middle and to an edge" in str(raised.value)


def test_a_glue_is_the_last_word_after_the_row_had_its_say(root, skin):
    box = card(root, "deck")
    a, b = row(box, "a", "b")
    hstack(box, 8)
    anchor(b, "bottom")
    laid(root, skin)
    assert spot(b) == (93.0, 87.0, 60.0, 20.0)
    assert rel(b) == (68.0, 62.0, 60.0, 20.0)
    assert a.clip == (25.0, 25.0, 60.0, 20.0), "the rest of the row is unchanged"
    assert laid(root, skin) == 0


def test_a_widget_glued_to_a_glued_one_lands_on_the_final_edge(root, skin):
    box = card(root, "deck", (16, 16, 200, 100))
    knob = thing(root, "knob", (0, 0, 40, 20))
    badge = thing(root, "badge", (0, 0, 10, 10))
    anchor(knob, "right", box, "right", -16)
    anchor(badge, "left", knob, "right", 4)
    knob.raise_to_top()
    # the tree is now walked badge first, and the sort has to put it second
    laid(root, skin)
    assert spot(knob) == (160.0, 0.0, 40.0, 20.0)
    assert spot(badge) == (204.0, 0.0, 10.0, 10.0)
    box.place(16, 16, 300, 100)
    laid(root, skin)
    assert spot(badge)[0] == 304.0


def test_anchors_that_point_at_each_other_say_so_instead_of_guessing(root,
                                                                     skin):
    a = thing(root, "a")
    b = thing(root, "b")
    anchor(a, "left", b, "right")
    anchor(b, "left", a, "right")
    with pytest.raises(LayoutError) as raised:
        laid(root, skin)
    assert "in a circle" in str(raised.value)
    assert "root/a -> root/b -> root/a" in str(raised.value)


def test_a_widget_can_be_glued_across_the_window_to_another_panel(root, skin):
    deck = card(root, "deck", (16, 16, 250, 60))
    other = card(root, "other", (200, 20, 60, 40))
    pin = thing(deck, "pin", (0, 0, 10, 10))
    anchor(pin, "right", other, "left", -4)
    laid(root, skin)
    assert spot(pin) == (186.0, 25.0, 10.0, 10.0)
    assert rel(pin)[0] == 161.0, "and it is still written in its own panel's space"
    assert pin.clip == pin.screen_box
    other.place(100, 20, 60, 40)
    laid(root, skin)
    assert spot(pin)[0] == 86.0
    assert rel(pin)[0] == 61.0


def test_a_resize_re_measures_the_glues_instead_of_rebuilding_the_tree(root,
                                                                       skin):
    box = card(root, "deck", (16, 16, 200, 100))
    pack(box, "grow", "fixed")
    badge = thing(root, "badge", (0, 0, 20, 20))
    anchor(badge, "right", box, "right", -8)
    laid(root, skin)
    assert spot(badge)[0] == 188.0
    before = [held.uid for held in root.walk()]
    laid(root, skin, width=720, height=440)
    assert box.width == 400.0, "the panel grew with the window"
    assert spot(badge)[0] == 388.0
    assert [held.uid for held in root.walk()] == before, "nothing was rebuilt"


def test_a_dpi_change_is_the_windows_size_and_the_layout_never_heard_of_it(root,
                                                                           skin):
    bar = thing(root, "bar", (0, 0, 300, 20))
    anchor(bar, "left", None, "left", 10)
    anchor(bar, "right", None, "right", -10)
    laid(root, skin, width=360, height=220)
    small = (bar.x, bar.width)
    laid(root, skin, width=720, height=440)
    assert bar.width == 700.0
    laid(root, skin, width=360, height=220)
    assert (bar.x, bar.width) == small, "the same logical size is the same answer"


# -- the device grid --------------------------------------------------------


@pytest.mark.parametrize("dpr, value, expected", [
    (1.0, 16.4, 16.0), (1.0, 16.6, 17.0), (1.0, -0.4, 0.0),
    (1.25, 16.0, 16.0), (1.25, 16.4, 16.8),
    (2.0, 16.3, 16.5), (2.0, 16.13, 16.0), (2.0, 0.25, 0.5),
    (3.0, 10.0, 10.0),
])
def test_one_number_moves_to_the_nearest_device_pixel(dpr, value, expected):
    assert snap(value, dpr) == pytest.approx(expected)


@pytest.mark.parametrize("dpr", [0, 0.0, None, -1, -2.5])
def test_a_scale_of_nothing_is_the_one_grid_everyone_already_has(dpr):
    assert snap(16.4, dpr) == 16.0
    assert snap_box((10.3, 4.7, 33.3, 12.1), dpr) == \
        snap_box((10.3, 4.7, 33.3, 12.1), 1.0)


def test_two_boxes_that_share_an_edge_still_share_it_after_the_snap():
    boxes = [(index * 100.4, 0.0, 100.4, 20.0) for index in range(5)]
    snapped = [snap_box(box, 1.25) for box in boxes]
    for held, next_one in zip(snapped, snapped[1:]):
        assert held[0] + held[2] == next_one[0]


def test_rounding_a_position_and_a_length_separately_is_the_drift_this_avoids():
    box = (0.25, 0.0, 10.25, 20.0)
    first = snap_box(box, 2.0)
    next_to_it = snap_box((10.5, 0.0, 10.25, 20.0), 2.0)
    assert first[0] + first[2] == next_to_it[0], "the two still share an edge"
    naive = (snap(box[0], 2.0), 0.0, snap(box[2], 2.0), 20.0)
    assert naive[0] + naive[2] != next_to_it[0]


def test_a_box_thinner_than_a_device_pixel_collapses_instead_of_borrowing():
    assert snap_box((10.0, 0.0, 0.2, 20.0), 2.0) == (10.0, 0.0, 0.0, 20.0)
    assert snap_box((0.0, 0.0, 30.0, 0.1), 2.0) == (0.0, 0.0, 30.0, 0.0)


def test_the_snap_stays_within_half_a_device_pixel_of_the_layouts_answer():
    box = (25.0, 25.0, 182.0, 82.0)
    held = snap_box(box, 1.25)
    slack = 0.5 / 1.25 + 1e-9
    for got, want in zip((held[0], held[1], held[0] + held[2], held[1] + held[3]),
                         (25.0, 25.0, 207.0, 107.0)):
        assert abs(got - want) <= slack


def test_the_pass_never_rounds_a_draw_pass_is_the_one_that_decides(root, skin):
    box = card(root, "deck")
    icons = row(box, "i", "j", "k", size=(20, 20))
    grid(box, 3, gap=0)
    pack(icons[0], "fill")
    laid(root, skin)
    assert icons[0].width == pytest.approx(182 / 3)
    assert icons[0].width * 3 == pytest.approx(182.0)
    assert icons[1].width == 20.0, "a fixed child keeps its size and centres in it"
    assert snap(icons[0].width, 1.0) == 61.0
    assert snap(icons[1].width, 1.0) == 20.0


# -- clips, boxes and the pointer -------------------------------------------


def test_a_widget_the_pass_collapsed_to_nothing_is_no_hit_and_no_face(root,
                                                                     skin,
                                                                     recording):
    box = card(root, "deck", (16, 16, 18, 18))          # no content left
    kid = thing(box, "k", (0, 0, 60, 20))
    pack(kid, "fill")
    laid(root, skin)
    assert (kid.width, kid.height) == (0.0, 0.0)
    assert top_at(root, 20, 20) is box
    assert kid.paint_face(recording, skin) == 0


def test_a_resize_moves_the_answer_of_a_hit_test_and_not_only_the_box(root,
                                                                      skin):
    box = card(root, "deck", (16, 16, 200, 100))
    knob = thing(box, "k", (0, 0, 40, 20))
    anchor(knob, "right", None, "right", -10)
    laid(root, skin)
    assert spot(knob) == (157.0, 25.0, 40.0, 20.0)
    assert top_at(root, 170, 30) is knob
    pack(box, "fill", "fixed")
    laid(root, skin, width=560, height=220)
    assert spot(knob)[0] == 501.0
    assert top_at(root, 170, 30) is box, "the knob is not where it used to be"
    assert top_at(root, 510, 30) is knob


def test_a_row_that_overlaps_itself_answers_for_whatever_is_on_top(root, skin):
    box = card(root, "deck")
    a, b = row(box, "a", "b", size=(120, 20))
    hstack(box, 8)
    laid(root, skin)
    assert top_at(root, 100, 30) is a
    assert top_at(root, 160, 30) is b
    assert top_at(root, 210, 30) is box
    b.sink()
    laid(root, skin)
    assert top_at(root, 100, 30) is b
    assert top_at(root, 200, 30) is a


def test_a_clip_is_what_the_panel_and_the_window_both_agree_on(root, skin):
    outer = card(root, "deck", (16, 16, 218, 118))
    inner = card(outer, "in", (60, 30, 100, 40))
    knob = thing(inner, "k", (0, 0, 100, 20))
    laid(root, skin)
    assert knob.clip == (94.0, 64.0, 82.0, 20.0), "cut by its own panel"
    outer.place(300, 16, 218, 118)
    laid(root, skin)
    assert inner.screen_box[0] > 360.0, "the panel took it off the window"
    assert knob.clip[2] == 0.0, "and a clip the window cannot show is no clip"
    assert top_at(root, 355, 60) is outer


# -- what a pass costs a frame ----------------------------------------------


def metronome(root: Widget) -> None:
    """The plan's screen, in the calls a script would use to say it."""
    thing(root, "title", (16, 12, 200, 20), kind="text")
    box = card(root, "deck")
    play, _stop, _tick = row(box, "play", "stop", "tick")
    hstack(box, 8)
    pack(play, "fixed", "fill")
    tempo = thing(root, "tempo", (16, 130, 200, 20), kind="slider")
    pack(tempo, "fill", "fixed")
    anchor(tempo, "bottom")


def test_a_pass_that_moves_nothing_paints_exactly_the_same_frame(root, skin):
    metronome(root)
    laid(root, skin)
    drawn = RecordingRenderer()
    drawn.begin(360, 220, 360, 220)
    drawn.clear(skin.colour("window"))
    total = root.paint(drawn, skin)
    drawn.end()
    assert laid(root, skin) == 0, "the second pass had nothing to say"
    again = RecordingRenderer()
    again.begin(360, 220, 360, 220)
    again.clear(skin.colour("window"))
    assert root.paint(again, skin) == total
    again.end()
    assert again.names() == drawn.names()
    assert again.rects() == drawn.rects()


def test_a_glued_row_does_not_change_its_mind_every_frame(root, skin):
    box = card(root, "deck")
    a, b = row(box, "a", "b")
    hstack(box, 8)
    anchor(b, "bottom")
    laid(root, skin)
    keys = [held.paint_key(skin) for held in root.walk()]
    assert laid(root, skin) == 0
    assert [held.paint_key(skin) for held in root.walk()] == keys


def test_the_headless_and_the_recording_renderer_agree_on_the_cost(recording,
                                                                  quiet,
                                                                  skin):
    held = root_panel(360, 220, name="window")
    metronome(held)
    laid(held, skin)
    said = held.paint(recording, skin)
    assert held.paint(quiet, skin) == said
    assert quiet.by_name() == {
        word: recording.count(word) for word in set(recording.names())
    }


def test_five_hundred_widgets_lay_out_once_and_then_never_again(root, skin):
    box = card(root, "deck", (0, 0, 360, 220))
    grid(box, 20, gap=0)
    kids = [thing(box, str(n), (0, 0, 10, 10)) for n in range(500)]
    assert laid(root, skin) == 500
    assert laid(root, skin) == 0
    assert len({spot(held) for held in kids}) == 500
    assert kids[0].width == 10.0, "a grid moves a child; only PACK sizes it"


def test_a_widget_arranged_and_glued_is_written_twice_and_counted_twice(root,
                                                                        skin):
    box = card(root, "deck")
    a, b = row(box, "a", "b")
    hstack(box, 8)
    anchor(b, "bottom")
    assert laid(root, skin) == 2, "once for the row, once for the glue"
    assert a.clip == (25.0, 25.0, 60.0, 20.0)


def test_a_policy_document_is_what_a_report_will_print():
    held = Layout(pack_x="grow", pad=4.0, stack=Stack("vertical", 6))
    assert held.as_dict() == {"pack": ["grow", "fixed"], "pad": 4.0,
                              "stack": Stack("vertical", 6).as_dict()}
    assert str(held) == ("Layout(pack grow/fixed, Stack(vertical, gap=6, "
                         "align=start), pad 4)")
    assert repr(held) == "<Layout grow/fixed>"
    assert Layout().as_dict() == {"pack": ["fixed", "fixed"]}
    assert Layout(pack_y="fill").axis("y") == "fill"
    assert Layout(pad="6").pad == 6.0


def test_the_reference_a_grow_rule_took_is_in_the_report(root, skin):
    box = card(root, "deck", (16, 16, 218, 100))
    kid = thing(box, "s", (0, 0, 100, 20))
    pack(kid, "grow", "fixed")
    laid(root, skin)
    assert describe_layout(kid) == {"pack": ["grow", "fixed"],
                                    "reference": {"x": [100.0, 200.0]}}
    assert kid.layout.produced("x") == 100.0
    assert kid.layout.produced("y") == 20.0


def test_the_pass_can_tell_its_own_answer_from_one_a_script_wrote(root, skin):
    box = card(root, "deck", (16, 16, 218, 100))
    kid = thing(box, "s", (0, 0, 100, 20))
    pack(kid, "grow", "fixed")
    laid(root, skin)
    assert abs(kid.width - kid.layout.produced("x")) <= TOLERANCE
    kid.place(None, None, 90, None)
    assert abs(kid.width - kid.layout.produced("x")) > TOLERANCE
    laid(root, skin)
    assert kid.width == 90.0
    assert kid.layout.reference("x") == (90.0, 200.0)


# -- the words on their own, which is what the bindings layer checks ----------


@pytest.mark.parametrize("word, policy", [
    ("fill", "fill"), (" EXPAND ", "fill"), ("grow", "grow"),
    ("Scale", "grow"), ("fixed", "fixed"), (None, "fixed"),
])
def test_a_policy_word_is_checked_to_one_of_the_three(policy, word):
    assert as_policy(word) == policy


@pytest.mark.parametrize("word, edge", [
    ("l", "left"), ("R", "right"), ("t", "top"), ("b", "bottom"),
    ("cx", "center-x"), ("vcenter", "center-y"), ("middle", "center-y"),
    (" centre ", "center-x"), ("right", "right"),
])
def test_an_edge_word_is_checked_to_one_of_the_six(word, edge):
    assert as_edge(word) == edge


@pytest.mark.parametrize("word, wanted", [
    ("h", "horizontal"), ("row", "horizontal"), ("x", "horizontal"),
    (None, "horizontal"), ("v", "vertical"), ("column", "vertical"),
    ("g", "grid"), ("GRID", "grid"),
])
def test_a_direction_word_is_checked_to_one_of_the_three(word, wanted):
    assert as_direction(word) == wanted


@pytest.mark.parametrize("word, wanted", [
    ("begin", "start"), ("left", "start"), ("center", "center"),
    ("middle", "center"), ("far", "end"), ("bottom", "end"),
    ("fill", "stretch"), (None, "center"),
])
def test_an_alignment_word_is_checked_to_one_of_the_four(word, wanted):
    assert as_alignment(word, "center") == wanted


def test_each_word_type_names_its_own_known_words_when_it_refuses():
    for call, message in ((lambda: as_policy("nope"), "is not a pack policy"),
                          (lambda: as_edge("nope"), "is not an edge"),
                          (lambda: as_direction("nope"),
                           "is not a stack direction"),
                          (lambda: as_alignment("nope"),
                           "is not an alignment")):
        with pytest.raises(LayoutError) as raised:
            call()
        assert message in str(raised.value)
    with pytest.raises(LayoutError) as raised:
        as_edge(None)
    assert "an anchor has to be given an edge" in str(raised.value)


@pytest.mark.parametrize("table, words", [
    ("policy", PACK_POLICIES), ("edge", EDGES),
    ("direction", STACK_DIRECTIONS), ("alignment", STACK_ALIGNMENTS),
])
def test_a_canonical_word_always_survives_its_own_checker(table, words):
    check = {"policy": as_policy, "edge": as_edge,
             "direction": as_direction, "alignment": as_alignment}[table]
    for said in words:
        assert check(said) == said


def test_every_name_the_package_promises_is_a_real_one():
    """The package's ``__all__`` is the bindings layer's shopping list, so a typo
    in it is a crash a script pays for rather than one this test pays for."""
    import crude.ui

    missing = [name for name in crude.ui.__all__
               if not hasattr(crude.ui, name)]
    assert missing == []


def test_the_layout_surface_is_reachable_from_the_package():
    """The bindings import from ``crude.ui``, not from the module behind it."""
    from crude import ui

    for name in ("place", "pack", "anchor", "pad", "hstack", "vstack", "grid",
                 "arrange", "resolve", "snap", "snap_box", "metric",
                 "as_policy", "as_edge", "as_direction", "as_alignment",
                 "Layout", "Stack", "Anchor", "LayoutError"):
        assert hasattr(ui, name), name
    assert ui.LayoutError is not None
    assert AXIS["center-y"] == "y"
    assert len(LAYOUT_TOKENS) == len(set(LAYOUT_TOKENS))
