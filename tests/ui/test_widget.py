"""A widget tree on paper: where it is, what is on top, and what the pointer did.

:mod:`crude.ui.widget` is the layer the plan's S7 acceptance line puts numbers on -
"anchor/DPI/z-order/hit-test math tests" - so almost everything here is arithmetic
on boxes and ordering, run against a :class:`~crude.ui.recording.RecordingRenderer`
with no display anywhere. The interesting failures are the ones a single widget
cannot show on its own: a child that moves but is measured against its parent's old
content box, a hit test that answers the widget *under* the one on screen, a panel
whose own padding silently shifts every ``PLACE`` in the project.

Three decisions get tested hardest, because they are the three that are easy to
make quietly wrong:

* the root's content box **is** the window - the plan places its metronome title
  at ``16, 12`` and means sixteen pixels from the edge, not from an inset;
* the four pointer props are state, so a hover changes the paint key and does not
  count as a change a script's ``ON_CHANGE`` asked for;
* a range is a prop like any other, so writing ``max`` moves the value that was
  reading its limits from it, through the same door a ``SET`` goes through.
"""

from __future__ import annotations

from typing import Any

import pytest

from crude.ui import (CONTAINER_KIND, InputRouter, MODS, NullRenderer, POINTER,
                      Panel, PropError, RecordingRenderer, Skin, Widget,
                      WidgetError, define, define_prop, format_value,
                      install_behaviour, reset, root_panel, top_at)

#: the nineteen kinds the catalog ships, same list as :mod:`test_props`
CATALOG = ("panel", "text", "icon", "button", "toggle", "slider", "vslider",
           "knob", "number", "meter", "radio", "dropdown",
           "waveform", "spectrum", "adsr", "curve", "xy", "keybed",
           "preset_bar")


@pytest.fixture(autouse=True)
def clean_registry():
    """Script kinds are global; hand the table back the way it was found."""
    reset()
    yield
    reset()


@pytest.fixture
def skin() -> Skin:
    return Skin("dark")


@pytest.fixture
def root(skin) -> Widget:
    """A synced window root: the plan's 360 x 220 metronome client rect."""
    held = root_panel(360, 220)
    held.sync(skin)
    return held


def build(kind, values=None, *, name="", parent=None, skin=None) -> Widget:
    """A widget, placed at a size, in a tree, resolved - the way a frame does.

    Construction and placement are two steps in the language and one requirement
    in the maths, so every geometry test goes through here and none of them has to
    remember to sync.
    """
    held = Widget(kind, values, name=name, parent=parent)
    held.place(0, 0, 100, 24)
    if parent is None:
        held.sync(skin or Skin("dark"))
    return held


# -- holding props ----------------------------------------------------------


def test_a_widget_holds_what_the_table_said_it_holds():
    held = Widget("slider", {"label": "BPM", "min": 20, "max": 300,
                             "value": 120})
    assert held.get("label") == "BPM"
    assert held.value == 120.0
    assert held.value_text() == "120"
    assert held.role == "track" and held.size == "md"
    assert held.pd_object == "hslider"


def test_a_constructor_and_a_set_go_through_the_same_door():
    built = Widget("slider", {"value": 500, "min": 20, "max": 300})
    assert built.value == 300.0, "clamped before it was ever held"
    other = Widget("slider", {"min": 20, "max": 300})
    assert other.set("value", 500) is True
    assert other.value == 300.0


def test_the_arguments_a_constructor_takes_are_the_props_a_setter_takes():
    for kind in CATALOG:
        held = Widget(kind)
        for word in held.kind.args:
            assert word in held.values(), (kind, word)
            assert not held.kind.props[word].readonly, (kind, word)


def test_a_widget_reports_the_names_it_does_have_when_a_prop_is_misspelled():
    held = Widget("slider")
    with pytest.raises(PropError) as raised:
        held.set("widht", 10)
    assert "has no prop 'widht'" in str(raised.value)
    assert "width" not in str(raised.value) or "value" in str(raised.value)


@pytest.mark.parametrize("word", POINTER)
def test_a_script_cannot_write_what_the_pointer_writes(word):
    held = Widget("button")
    with pytest.raises(PropError) as raised:
        held.set(word, True)
    assert "written by the pointer or the keyboard" in str(raised.value)
    assert held.get(word) is False, "the refusal left the value alone"


@pytest.mark.parametrize("kind", CATALOG)
def test_the_pointer_rows_are_exactly_the_rows_the_table_closed_to_a_script(kind):
    held = Widget(kind)
    closed = {name for name, prop in held.kind.props.items() if prop.readonly}
    assert closed == set(POINTER), kind
    assert set(held.writable()) == set(held.values()) - set(POINTER), kind


def test_a_prop_is_read_the_way_it_was_written_even_though_names_are_lowered():
    held = Widget("slider")
    held.set("Label", "BPM")
    assert held.get("label") == "BPM"
    assert held.get(" LABEL ") == "BPM"


def test_writing_the_same_value_back_is_not_a_change_at_all():
    held = Widget("slider", {"min": 0, "max": 10, "value": 4})
    assert held.set("value", 4) is False
    assert held.take_changes() == []
    assert held.set("value", 4.0) is False, "4 and 4.0 are one position"


def test_an_overshoot_that_lands_where_it_already_was_is_not_a_change():
    # this is what stops a two-way LINK_VALUE from echoing itself forever: the
    # mirror writes the clamped value, the widget already holds it, nothing moves
    held = Widget("slider", {"min": 0, "max": 10, "value": 10})
    assert held.set("value", 99) is False
    assert held.value == 10.0


# -- a range that moves under a value ----------------------------------------


def test_moving_the_max_takes_the_value_that_was_past_it():
    held = Widget("slider", {"min": 0, "max": 300, "value": 120})
    assert held.set("max", 100) is True
    assert held.value == 100.0, "a thumb outside its track is a drawn lie"


def test_moving_an_end_past_the_other_takes_the_other_end_with_it():
    held = Widget("slider", {"min": 0, "max": 100, "value": 50})
    held.set("min", 60)
    assert (held.get("min"), held.value) == (60.0, 60.0)
    # the range cannot be inverted, and the number the script asked for is the
    # one it gets: the end nobody touched is the one that moves
    held.set("max", 40)
    assert (held.get("min"), held.get("max")) == (40.0, 40.0)
    assert held.value == 40.0, "a thumb has one place to sit in a closed range"
    held.set("min", 10)
    assert (held.get("min"), held.get("max"), held.value) == (10.0, 40.0, 40.0)


def test_the_sweep_reports_the_value_as_a_change_a_script_asked_about():
    held = Widget("slider", {"min": 0, "max": 300, "value": 120})
    seen = []
    held.on_change = lambda who, name, value: seen.append((name, value))
    held.set("max", 100)
    assert seen == [("max", 100.0), ("value", 100.0)]
    assert held.take_changes() == ["max", "value"]


def test_a_sweep_only_touches_the_rows_that_read_their_range_from_the_one():
    held = Widget("slider", {"min": 0, "max": 300, "value": 120, "step": 10})
    held.set("label", "BPM")
    assert held.take_changes() == ["label"], "a label bounds nothing"
    held.set("step", 5)
    assert held.value == 120.0


def test_a_row_bounded_by_a_list_is_swept_when_the_list_changes():
    held = Widget("radio", {"labels": ["sine", "tri", "saw"], "value": 2})
    assert held.value == 2.0
    held.set("labels", ["sine"])
    assert held.value == 0.0, "an index cannot point past the end of its words"


def test_a_script_kinds_chain_of_bounds_converges_in_one_write():
    define("deck")
    define_prop("deck", "floor", "number", 0)
    define_prop("deck", "ceil", "number", 100)
    define_prop("deck", "min", "number", 10, 0, 100, bounds=("floor", "ceil"))
    define_prop("deck", "level", "number", 50, bounds=("min", "max"))
    define_prop("deck", "max", "number", 90, 0, 100, bounds=("floor", "ceil"))
    held = Widget("deck")
    # ceil bounds min, min bounds level: writing the top bound has to travel both
    held.set("ceil", 20)
    assert (held.get("min"), held.get("max"), held.get("level")) == (10.0, 20.0,
                                                                     20.0)


# -- the box, and where it lands ---------------------------------------------


def test_the_root_is_the_window_so_a_place_is_a_place(root, skin):
    title = Widget("text", {"label": "METRONOME"}, name="title", parent=root)
    title.place(16, 12, 200, 20)
    assert title.screen_box == (16.0, 12.0, 200.0, 20.0)
    assert title.clip == (16.0, 12.0, 200.0, 20.0)
    # the plan's own picture, to the pixel: no padding in front of the first widget
    assert root.content_box() == root.screen_box


def test_a_child_is_measured_from_its_parents_content_box(root, skin):
    outer = Panel(name="card", parent=root)
    outer.place(0, 0, 300, 200)
    inner = Widget("button", {"label": "Go"}, name="go", parent=outer)
    inner.place(8, 8, 90, 24)
    root.sync(skin)
    found = skin.resolve("panel")
    inset = found.padding + found.width
    assert inner.screen_box[0] == pytest.approx(8 + inset)
    assert inner.screen_box[1] == pytest.approx(8 + inset)
    # a leaf has a content box too - its own, inset by its own plate, because the
    # same rule decides where a child of a button would go if it had one
    plate = skin.resolve("button").padding + skin.resolve("button").width
    assert inner.content_box()[0] == pytest.approx(8 + inset + plate)


def test_a_panel_inside_a_panel_is_measured_twice_and_both_times_by_a_skin(
        root, skin):
    outer = Panel(name="outer", parent=root)
    outer.place(0, 0, 400, 300)
    inner = Panel(name="inner", parent=outer)
    inner.place(10, 10, 200, 100)
    leaf = Widget("button", {"label": "x"}, name="leaf", parent=inner)
    leaf.place(4, 4, 40, 20)
    root.sync(skin)
    pad = skin.resolve("panel").padding + skin.resolve("panel").width
    assert leaf.screen_box[:2] == pytest.approx((10 + pad + 4 + pad,
                                                 10 + pad + 4 + pad))
    assert inner.clip == pytest.approx(inner.screen_box)


def test_a_child_that_overflows_its_parent_is_clipped_not_redrawn(root, skin):
    outer = Panel(name="outer", parent=root)
    outer.place(0, 0, 100, 100)
    inner = Widget("button", name="wide", parent=outer)
    inner.place(0, 0, 300, 40)
    root.sync(skin)
    pad = skin.resolve("panel").padding + skin.resolve("panel").width
    assert inner.clip == pytest.approx((pad, pad, 100 - 2 * pad, 40))
    assert inner.screen_box[2] == 300.0, "it is still where it was placed"
    assert outer.hit(250, 20) is None, "and it is not clickable past the edge"


def test_a_box_may_be_moved_without_another_sync(root, skin):
    held = Widget("button", name="go", parent=root)
    held.place(20, 20, 60, 24)
    assert held.screen_box == (20.0, 20.0, 60.0, 24.0)
    assert held.place(20, 20, 60, 24) is False, "nothing said, nothing moved"
    assert held.place(x=100) is True
    assert held.screen_box == (100.0, 20.0, 60.0, 24.0)
    assert held.box == (100.0, 20.0, 60.0, 24.0)


def test_only_what_is_said_about_the_box_is_changed(root):
    held = Widget("slider", name="s", parent=root)
    held.place(10, 10, 50, 20)
    held.place(height=40)
    assert held.box == (10.0, 10.0, 50.0, 40.0)


def test_a_negative_size_is_a_box_the_widget_refuses_to_have(root):
    held = Widget("slider", name="s", parent=root)
    held.place(0, 0, -40, -40)
    assert (held.width, held.height) == (0.0, 0.0)
    assert held.hit(0, 0) is None


def test_the_children_of_a_widget_too_small_for_its_padding_have_no_room(root,
                                                                         skin):
    outer = Panel(name="tiny", parent=root)
    outer.place(0, 0, 6, 6)
    inner = Widget("button", name="gone", parent=outer)
    inner.place(0, 0, 30, 10)
    root.sync(skin)
    pad = skin.resolve("panel").padding + skin.resolve("panel").width
    assert outer.content_box()[2] == 0.0
    assert inner.clip[2] == 0.0 and outer.hit(4, 4) is outer


def test_the_paint_key_follows_the_box_the_widget_was_drawn_at(root, skin):
    held = Widget("button", name="go", parent=root)
    held.place(10, 10, 60, 24)
    before = held.paint_key(skin)
    assert held.paint_key(skin) == before
    held.place(x=80)
    assert held.paint_key(skin) != before, "a move is a repaint"


# -- the shape of the tree ----------------------------------------------------


def test_a_leaf_refuses_a_child_by_naming_what_it_is():
    held = Widget("slider")
    with pytest.raises(WidgetError) as raised:
        held.add(Widget("button"))
    assert "a 'slider' holds no children" in str(raised.value)
    assert CONTAINER_KIND in str(raised.value)


def test_container_is_a_fact_about_the_table_not_about_the_class(skin):
    define("deck", CONTAINER_KIND)
    held = Widget("deck")
    assert held.is_container is True
    assert isinstance(held, Panel) is False
    held.add(Widget("button", name="go"))
    assert held.count() == 2
    define("podium", "button")
    assert Widget("podium").is_container is False


def test_a_panel_is_a_panel_kind_and_a_panel_kind_is_a_container():
    held = Panel()
    assert held.kind.name == CONTAINER_KIND
    assert held.is_container is True
    assert str(held.kind) == "Kind(panel, 16 props, emits [cnv])"
    # a Panel built on a script kind is still a panel, and the kind is its own
    define("card", CONTAINER_KIND)
    assert Panel(kind="card").kind.name == "card"
    assert Panel(kind="card").is_container is True


def test_a_child_has_one_parent_and_moves_rather_than_duplicating(root):
    other = Panel(name="other")
    other.place(0, 0, 100, 100)
    held = Widget("button", name="go", parent=root)
    assert root.count() == 2
    other.add(held)
    assert held.parent is other
    assert root.count() == 1, "it left, it was not copied"
    assert other.count() == 2
    # the path is the tree it is in now, from that tree's own root
    assert held.path() == "other/go"
    assert root.find("go") is None


def test_a_widget_cannot_be_put_inside_itself_or_inside_its_own_child(root):
    held = Panel(name="box")
    root.add(held)
    with pytest.raises(WidgetError, match="inside itself"):
        held.add(held)
    inner = Panel(name="inner", parent=held)
    with pytest.raises(WidgetError) as raised:
        held.add(root)
    assert "loop" in str(raised.value)
    assert inner.is_in(held) and not held.is_in(inner)


def test_adding_returns_the_child_and_a_parent_takes_several_at_once(root):
    first, second = Widget("button"), Widget("toggle")
    assert root.add(first) is first
    assert root.add_all(second, Widget("text")) is root
    assert root.children == [first, second, root.children[-1]]


def test_children_come_out_as_a_copy_nobody_can_reorder_by_accident(root):
    root.add(Widget("button", name="go"))
    held = root.children
    held.clear()
    assert len(root.children) == 1


def test_removal_leaves_the_props_and_the_box_alone_but_takes_the_pointer_off(root):
    held = Widget("button", {"label": "Go", "value": 1}, name="go", parent=root)
    held.place(4, 4, 40, 20)
    held.on_enter()
    assert root.remove(held) is held
    assert held.parent is None
    assert held.label == "Go" and held.value == 1.0
    assert (held.hovered, held.box) == (False, (4.0, 4.0, 40.0, 20.0))
    assert held.screen_box == (4.0, 4.0, 40.0, 20.0), "detached, so back to itself"
    with pytest.raises(WidgetError) as raised:
        root.remove(held)
    assert "not inside" in str(raised.value)


def test_detach_is_the_childs_way_of_saying_the_same_thing(root):
    held = Widget("button", parent=root)
    assert held.detach() is held
    assert root.children == []


def test_clear_empties_a_panel_and_hands_the_children_back_in_order(root):
    held = Panel(name="box", parent=root)
    held.add_all(Widget("button", name="a"), Widget("toggle", name="b"))
    gone = held.clear()
    assert [kid.name for kid in gone] == ["a", "b"]
    assert held.children == [] and held.count() == 1
    assert all(kid.parent is None for kid in gone)


def test_the_walk_is_the_draw_order_and_the_count_includes_the_receiver(root):
    held = Panel(name="box", parent=root)
    held.add_all(Widget("button", name="a"), Widget("toggle", name="b"))
    root.add(Widget("slider", name="s"))
    assert [item.name for item in root.walk()] == ["root", "box", "a", "b", "s"]
    assert [item.name for item in root.descendants()] == ["box", "a", "b", "s"]
    assert root.count() == 5 and held.count() == 3


def test_a_name_is_looked_for_before_a_kind_and_never_matches_nothing(root):
    held = Widget("slider", name="Tempo", parent=root)
    anonymous = Widget("meter", parent=root)
    assert root.find("tempo") is held
    assert root.find("Tempo") is held, "the name is stored the way the table is"
    assert root.find("slider") is held, "a kind is only a fallback"
    assert root.find("meter") is anonymous
    assert root.find("nonsense") is None
    assert root.find("") is None
    # a name wins even when a kind could answer the same word
    named = Widget("button", name="meter", parent=root)
    assert root.find("meter") is named


def test_path_and_depth_name_a_widget_the_way_a_report_has_to(root):
    held = Panel(name="box", parent=root)
    inner = Widget("button", name="go", parent=held)
    inner.place(0, 0, 10, 10)
    assert (inner.path(), inner.depth) == ("root/box/go", 2)
    assert (held.path(), held.depth, root.depth) == ("root/box", 1, 0)
    assert root.path(sep=" > ").endswith("root")


def test_an_anonymous_widget_is_named_by_its_kind_in_a_path(root):
    held = Widget("meter", parent=root)
    assert held.path() == "root/meter"


def test_the_uid_is_unique_enough_to_key_a_cache_and_is_not_a_name(root):
    first, second = Widget("button", parent=root), Widget("button", parent=root)
    assert first.uid != second.uid
    assert isinstance(first.uid, int) and first.uid > 0


# -- z-order ------------------------------------------------------------------


def test_the_stack_starts_in_the_order_the_widgets_were_built(root):
    first, second, third = (Widget("button", name=word, parent=root)
                            for word in ("a", "b", "c"))
    assert [kid.name for kid in root.children] == ["a", "b", "c"]
    assert (first.z, second.z, third.z) == (0, 1, 2)


def test_raise_and_lower_move_only_among_the_siblings_they_have(root):
    box = Panel(name="box", parent=root)
    first, second, third = (Widget("button", name=word, parent=box)
                            for word in ("a", "b", "c"))
    assert third.raise_to_top() is third and [kid.name for kid in box.children] \
        == ["a", "b", "c"], "already on top"
    first.raise_to_top()
    assert [kid.name for kid in box.children] == ["b", "c", "a"]
    second.lower_to_bottom()
    assert [kid.name for kid in box.children] == ["b", "c", "a"]
    second.lift(2)
    assert [kid.name for kid in box.children] == ["c", "a", "b"]
    second.sink(2)
    assert [kid.name for kid in box.children] == ["b", "c", "a"]


def test_moving_further_than_there_is_room_for_stops_at_the_end(root):
    box = Panel(name="box", parent=root)
    first, second = (Widget("button", name=word, parent=box)
                     for word in ("a", "b"))
    first.sink(99)
    assert [kid.name for kid in box.children] == ["a", "b"]
    first.lift(99)
    assert [kid.name for kid in box.children] == ["b", "a"]
    assert first.z == 1


def test_a_root_and_an_only_child_have_nothing_to_move_between(root):
    only = Widget("button", name="only", parent=root)
    assert root.z == 0
    assert only.raise_to_top() is only and only.lift(3) is only
    assert only.lower_to_bottom() is only and only.sink(1) is only


def test_the_hit_order_is_the_stack_reversed_and_the_draw_order_is_not(root):
    low = Widget("button", name="low", parent=root)
    low.place(0, 0, 100, 100)
    high = Widget("toggle", name="high", parent=root)
    high.place(20, 20, 40, 40)
    front = root.front_to_back()
    assert [item.name for item in front[:2]] == ["high", "low"]
    assert "root" in [item.name for item in front]
    assert root.hit(30, 30) is high
    assert root.hit(90, 90) is low
    high.lower_to_bottom()
    assert root.hit(30, 30) is low, "the top of the stack is what a click means"


def test_a_child_on_top_of_its_parent_beats_the_parent(root):
    box = Panel(name="box", parent=root)
    box.place(0, 0, 200, 100)
    held = Widget("button", name="go", parent=box)
    held.place(10, 10, 60, 30)
    assert box.hit(40, 40) is held
    assert box.hit(150, 40) is box


def test_a_repainted_stack_keeps_its_props_not_its_place_in_the_table(root):
    first = Widget("button", {"label": "a"}, name="one", parent=root)
    second = Widget("button", {"label": "b"}, name="two", parent=root)
    assert second.z == 1
    root.remove(second)
    root.add(second)
    assert (first.z, second.z) == (0, 1)


# -- what the pointer can reach ------------------------------------------------


def metronome(root, skin) -> dict:
    """The plan's front panel, in the plan's own coordinates.

    Returned as a map rather than as a tree because almost every test below wants
    one widget out of it, and the positions are the evidence: a hit test, a focus
    ring and a paint key are all asserted against the same five boxes the spec
    draws.
    """
    parts = {}
    for word, kind, values, box in (
            ("title", "text", {"label": "METRONOME", "role": "title"},
             (16, 12, 200, 20)),
            ("beat", "meter", {"label": "", "min": 0, "max": 1}, (16, 40, 24, 24)),
            ("bpm", "slider", {"label": "BPM", "min": 20, "max": 300,
                               "value": 120}, (56, 40, 200, 24)),
            ("tempo", "number", {"min": 20, "max": 300, "value": 120},
             (264, 40, 80, 24)),
            ("run", "toggle", {"label": "Run", "glyph_off": "play",
                               "glyph_on": "pause"}, (16, 84, 96, 28)),
    ):
        held = Widget(kind, values, name=word, parent=root)
        held.place(*box)
        parts[word] = held
    root.sync(skin)
    return parts


def inside(held) -> tuple:
    """A point comfortably inside a widget - never on an edge it shares."""
    left, top, width, height = held.screen_box
    return (left + width * 0.5, top + height * 0.5)


def test_a_hidden_widget_is_not_hit_and_not_painted(root, skin):
    held = Widget("button", name="go", parent=root)
    held.place(10, 10, 40, 20)
    assert root.hit(*inside(held)) is held
    held.set("visible", False)
    assert root.hit(*inside(held)) is root, "the pixel belongs to the window now"
    assert held.paint(RecordingRenderer(), skin) == 0


def test_a_disabled_panel_takes_everything_inside_it_out_of_the_pointers_reach(
        root, skin):
    box = Panel(name="box", parent=root)
    box.place(0, 0, 120, 60)
    inner = Widget("button", name="go", parent=box)
    inner.place(4, 4, 60, 20)
    root.sync(skin)
    assert root.hit(*inside(inner)) is inner
    box.set("enabled", False)
    assert root.hit(*inside(inner)) is root
    assert inner.hit(*inside(inner)) is inner, \
        "asked on its own it is still itself; the tree is what switches it off"


def test_a_point_outside_the_clip_is_not_a_hit_even_inside_the_box(root, skin):
    box = Panel(name="box", parent=root)
    box.place(0, 0, 60, 60)
    inner = Widget("button", name="wide", parent=box)
    inner.place(0, 0, 200, 40)
    root.sync(skin)
    left, top, width, height = inner.screen_box
    assert width == 200.0
    assert root.hit(100, 20) is root, "not drawn there, so not clickable there"
    cut = inner.clip
    assert cut[2] < width, "clipped to the panel's content box, not to its own 200"
    assert root.hit(cut[0] + 1, cut[1] + 1) is inner, \
        "the part of it that is inside is still a widget"
    assert root.hit(cut[0] + cut[2] + 1, cut[1] + 1) is box, \
        "past the clip is the panel's own padding, which is still the panel"


def test_the_widget_on_top_owns_the_edge_two_of_them_share(root, skin):
    left = Widget("button", name="left", parent=root)
    left.place(0, 0, 40, 20)
    right = Widget("toggle", name="right", parent=root)
    right.place(40, 0, 40, 20)
    assert root.hit(40, 10) is right, "one pixel is claimed by one widget"
    assert root.hit(39.5, 10) is left
    assert left.hit(40, 10) is None
    assert right.hit(40, 10) is right


def test_a_hit_path_is_the_widget_and_everything_it_is_sitting_in(root, skin):
    box = Panel(name="box", parent=root)
    box.place(0, 0, 120, 60)
    inner = Widget("button", name="go", parent=box)
    inner.place(4, 4, 60, 20)
    root.sync(skin)
    assert [held.name for held in root.hit_path(*inside(inner))] \
        == ["go", "box", "root"]
    assert root.hit_path(350, 210) == [root]


def test_top_at_is_the_callback_a_router_wants(root, skin):
    held = Widget("button", name="go", parent=root)
    held.place(10, 10, 40, 20)
    assert top_at(root, *inside(held)) is held
    assert top_at(root, 350, 210) is root
    assert top_at(held, *inside(held)) is held, "a widget answers for itself"
    assert top_at(held, 1, 1) is None, "and only for the box it is in"


# -- the keyboard ---------------------------------------------------------------


def test_what_can_be_tabbed_to_is_what_can_show_a_focus_ring(root, skin):
    parts = metronome(root, skin)
    assert root.tabbables() == [parts["bpm"], parts["tempo"], parts["run"]]
    assert parts["title"].focusable() is False, "a caption takes no keyboard"
    assert parts["beat"].focusable() is False, "a level read out is not set in"
    assert Panel(parent=root).focusable() is False


def test_only_one_widget_in_a_tree_holds_the_keyboard(root, skin):
    parts = metronome(root, skin)
    assert parts["bpm"].grab_focus() is True
    assert parts["bpm"].focused is True and root.find_focus() is parts["bpm"]
    assert parts["tempo"].grab_focus() is True
    assert parts["bpm"].focused is False, "the one that had it let go"
    assert root.find_focus() is parts["tempo"]
    assert parts["tempo"].grab_focus() is False, "already the one it was"


def test_a_widget_that_cannot_be_focused_refuses_and_says_nothing(root, skin):
    parts = metronome(root, skin)
    assert parts["title"].grab_focus() is False
    assert parts["title"].focused is False and root.find_focus() is None


def test_tab_goes_forwards_and_wraps_and_shift_goes_back(root, skin):
    parts = metronome(root, skin)
    assert root.next_focus() is parts["bpm"], "nothing focused: the first one"
    assert parts["bpm"].next_focus(1) is parts["tempo"]
    assert parts["run"].next_focus(1) is parts["bpm"], "the list is a ring"
    assert parts["bpm"].next_focus(-1) is parts["run"]
    assert parts["tempo"].next_focus(2) is parts["bpm"]


def test_losing_the_tree_loses_the_keyboard_with_it(root, skin):
    parts = metronome(root, skin)
    parts["bpm"].grab_focus()
    parts["run"].grab_focus()
    gone = root.remove(parts["run"])
    assert gone.focused is False
    assert parts["bpm"].focused is False, "it had already let go, not twice"
    assert root.find_focus() is None


def test_switching_a_widget_off_takes_the_keyboard_off_it(root, skin):
    parts = metronome(root, skin)
    parts["bpm"].grab_focus()
    parts["bpm"].set("enabled", False)
    assert parts["bpm"].focused is False and root.find_focus() is None
    parts["bpm"].on_enter()
    assert parts["bpm"].hovered is False, "a switched-off widget is not lit"
    parts["bpm"].set("enabled", True)
    parts["bpm"].on_enter()
    assert parts["bpm"].hovered is True


def test_blur_all_takes_the_pointer_and_the_keyboard_off_the_whole_subtree(
        root, skin):
    parts = metronome(root, skin)
    parts["run"].grab_focus()
    parts["bpm"].on_enter()
    parts["tempo"].on_press(1, 1)
    assert root.blur_all() is root
    for held in (parts["bpm"], parts["tempo"], parts["run"]):
        assert (held.hovered, held.pressed, held.focused) == (False,) * 3, held


# -- what the pointer's calls do ------------------------------------------------


def test_what_the_pointer_writes_is_state_and_not_a_change_a_script_asked_for(
        root, skin):
    # a toggle and not a bang, because a bang's press *is* a value the script asked
    # to hear about; all four of these gestures write the state rows and nothing else
    held = Widget("toggle", {"label": "Run"}, name="go", parent=root)
    held.place(0, 0, 40, 20)
    seen = []
    held.on_change = lambda who, name, value: seen.append(name)
    held.on_enter()
    assert held.hovered is True
    held.on_press(5, 5)
    assert held.pressed is True
    held.on_release(5, 5)
    assert held.pressed is False
    held.on_leave()
    assert held.hovered is False
    assert seen == [] and held.take_changes() == []


def test_a_listener_that_takes_the_slot_over_takes_the_kinds_behaviour_with_it(
        root, skin):
    """The slot is one writer deep, which is what makes chaining worth testing.

    :attr:`Widget.on_event` is the tree's whole interface to what an event means,
    so a listener that replaces it - as a test does, and as
    :meth:`~crude.ui.handlers.Handlers.adopt` pointedly does *not* - is also
    switching the kind's own behaviour off. That is the mechanism both halves of
    the event layer are built on, and the reason ``ON_CLICK`` on a toggle can be
    written without reimplementing the flip: :func:`~crude.ui.behaviour
    .install_behaviour` puts it back.
    """
    held = Widget("toggle", {"label": "Run", "value": 0}, name="run", parent=root)
    heard = []
    held.on_event = lambda who, name, *args: heard.append((name, args))
    held.on_press(10, 10, 0)
    held.on_release(10, 10, 0)
    held.on_click(10, 10, 0)
    assert heard == [("on_press", (10, 10, 0, 0)),
                     ("on_release", (10, 10, 0, 0)),
                     ("on_click", (10, 10, 0))]
    assert held.value == 0.0, "the listener is the only answer this click got"
    assert held.take_changes() == []
    install_behaviour(held)
    held.on_click(10, 10, 0)
    assert held.value == 1.0, "and re-installed, it is the flip the table says"
    # the listener is not chained back in: one writer deep is what makes chaining
    # worth testing, and chaining is what Handlers.adopt is for rather than this
    assert heard[-1] == ("on_click", (10, 10, 0)), "no new entry reached the listener"
    held.on_event = lambda who, name, *args: heard.append((name, args))
    held.on_click(10, 10, 0)
    assert heard[-1] == ("on_click", (10, 10, 0)) and held.value == 1.0, \
        "and a bare assignment switches it off again, exactly as the first one did"


def test_a_focus_a_widget_could_not_take_is_not_reported_as_one(root, skin):
    heard = []
    held = Widget("text", {"label": "METRONOME", "role": "title"}, parent=root)
    held.on_event = lambda who, name, *args: heard.append(name)
    held.on_focus()
    assert heard == [] and held.focused is False
    other = Widget("slider", parent=root)
    other.on_event = lambda who, name, *args: heard.append(name)
    other.on_focus()
    assert heard == ["on_focus"] and other.focused is True


def test_the_state_a_widget_reports_is_its_value_and_the_pointer_together(root):
    held = Widget("toggle", {"label": "Run"}, parent=root)
    assert held.state_name() == "normal"
    held.on_enter()
    assert held.state_name() == "hover"
    held.set("value", 1)
    assert held.state_name() == "on hover"
    held.on_press(1, 1)
    assert held.state_name() == "on pressed", "a press is the surer fact"
    held.on_release(1, 1)
    held.on_leave()
    assert held.state_name() == "on"
    held.grab_focus()
    assert held.state_name() == "on focus"
    held.set("enabled", False)
    assert held.state_name() == "on disabled"


def test_a_track_is_never_on_however_full_it_is(root):
    held = Widget("slider", {"min": 0, "max": 100, "value": 100}, parent=root)
    assert held.value == 100.0
    assert held.state_name() == "normal", "a range has no on; a switch does"


# -- what a frame is made of ---------------------------------------------------


def frame(root, skin) -> tuple:
    """One draw pass in the plan's own order: begin, clear, the tree, end.

    Every painting test goes through here or through a ``begin`` of its own,
    because the renderer contract refuses an op outside a frame: a test that
    painted without one would be asserting that nothing was asserted.
    """
    drawn = RecordingRenderer()
    drawn.begin(360, 220, 360, 220)
    drawn.clear(skin.colour("window"))
    painted = root.paint(drawn, skin)
    drawn.end()
    return drawn, painted


def look(held, skin) -> Any:
    """The appearance the skin hands this widget for the state it is in now."""
    return held.style(skin).resolve(held.role, held.state_name())


def onepaint(held, skin) -> RecordingRenderer:
    """One widget, alone, in a frame whose layout was resolved in the same skin.

    The sync is not ceremony: ``sync(skin)`` measures the padding a label is
    centred in and ``paint`` draws into that measurement, so a test that handed
    the two halves different skins would be asserting on a frame no real pass can
    produce - and the border-width tests are exactly where it would show.
    """
    held.root().sync(skin)
    drawn = RecordingRenderer()
    drawn.begin(360, 220, 360, 220)
    held.paint(drawn, skin)
    return drawn


def test_a_frame_is_one_clear_plus_everything_the_tree_drew(root, skin):
    parts = metronome(root, skin)
    drawn, painted = frame(root, skin)
    assert drawn.sizes == [(360.0, 220.0, 360, 220)]
    assert drawn.count("clear") == 1
    assert len(drawn.frames) == 1
    assert len(drawn.frames[0]) == painted + 1
    assert drawn.clip is None, "every clip the pass opened it closed again"
    assert drawn.names()[0] == "clear"


def test_a_caption_is_words_and_no_plate(root, skin):
    parts = metronome(root, skin)
    held = parts["title"]
    drawn = onepaint(held, skin)
    assert drawn.names() == ["text"]
    assert held.paint(onepaint(held, skin), skin) == 1
    found = look(held, skin)
    said = drawn.last("text")
    assert said.get("text") == "METRONOME"
    assert said.get("size") == found.font
    assert said.get("color") == found.ink
    assert said.get("align") == "center"
    assert said.clip == held.clip


def test_a_field_draws_the_number_it_holds_and_not_the_name_it_was_given(root, skin):
    """The readout rule is the skin's ``shows``, so no kind is named in the painter."""
    held = Widget("number", {"label": "BPM", "min": 20, "max": 300, "value": 120},
                  name="field", parent=root)
    held.place(264, 40, 80, 24)
    assert held.role == "field" and held.readout() == "120"
    assert held.label == "BPM", "the name it was given is not what it shows"
    assert onepaint(held, skin).last("text").get("text") == "120"
    held.set("value", 140)
    assert onepaint(held, skin).last("text").get("text") == "140"
    held.set("value", 122.5)
    assert onepaint(held, skin).last("text").get("text") == "122.5", \
        "the same formatter a GET hands the script wrote through"


def test_a_range_that_is_not_a_field_still_draws_its_label(root, skin):
    """A slider is read as its name and its track; the number belongs to the box."""
    for kind, values in (("slider", {"label": "BPM", "min": 20, "max": 300,
                                     "value": 120}),
                         ("meter", {"label": "beat", "min": 0, "max": 1}),
                         ("button", {"label": "go", "value": 1})):
        held = Widget(kind, values, name=kind, parent=root)
        held.place(10, 10, 100, 24)
        assert held.role in ("track", "meter", "button")
        assert onepaint(held, skin).last("text").get("text") == held.label, kind
        assert held.readout() == held.value_text(), \
            kind + " knows its number; it is just not drawn with it"


def test_a_half_typed_edit_shows_what_has_been_typed_so_far(root, skin):
    """A field that echoes nothing back is a field nobody can read.

    The buffer is the behaviour's scratch and not a prop, so this is the one thing
    a base face draws that a :meth:`paint_key` does not cover - which is why the
    catalog face is never cached and only a script's painter is.
    """
    held = Widget("number", {"min": 20, "max": 300, "value": 120}, name="field",
                  parent=root)
    held.place(264, 40, 80, 24)
    held.on_press(280, 52, 0, 0)
    # a press opens the edit rather than jumping the box, so what is drawn now is
    # neither the number that was there nor the number that is coming
    for character in "75":
        held.on_char(character)
    assert onepaint(held, skin).last("text").get("text") == "75"
    assert held.value == 120.0, "and nothing has been written until the edit lands"
    held.on_key("enter")
    assert held.value == 75.0 and \
        onepaint(held, skin).last("text").get("text") == "75"
    # an overshoot reads back what it was clamped to, which is the number the box
    # holds and not the digits that were typed at it
    held.on_press(280, 52, 0, 0)
    for character in "500":
        held.on_char(character)
    held.on_key("enter")
    assert held.value == 300.0 and \
        onepaint(held, skin).last("text").get("text") == "300", \
        "the buffer is gone, so the value is what is left to read"


def test_a_folded_list_draws_the_choice_it_is_pointing_at(root, skin):
    """A ``dropdown`` shares the field role, so it is read as a word and not an index."""
    held = Widget("dropdown", {"labels": ["sine", "tri", "saw"], "value": 1},
                  name="wave", parent=root)
    held.place(140, 140, 80, 20)
    assert held.readout() == "tri"
    assert onepaint(held, skin).last("text").get("text") == "tri"
    held.set("value", 0)
    assert onepaint(held, skin).last("text").get("text") == "sine"


def test_a_box_with_nothing_to_read_out_falls_back_to_its_label(root, skin):
    """``shows`` is a role, and a script may put any widget in one."""
    held = Panel({"label": "card"}, name="box", parent=root)
    held.place(0, 0, 120, 60)
    held.set("role", "field")
    assert held.has_value is False and held.readout() == "card"
    assert onepaint(held, skin).last("text").get("text") == "card"


def test_a_face_is_two_plates_when_it_has_a_border(root, skin):
    held = Widget("button", {"label": "Run"}, name="go", parent=root)
    held.place(10, 10, 100, 24)
    drawn = onepaint(held, skin)
    assert drawn.names() == ["rect", "rect", "text"]
    found = look(held, skin)
    assert found.width > 0
    ring = drawn.ops[0]
    assert (ring.get("x"), ring.get("y")) == held.screen_box[:2]
    assert (ring.get("width"), ring.get("height")) == held.screen_box[2:]
    assert ring.get("color") == found.border
    assert drawn.ops[1].get("color") == found.fill


@pytest.mark.parametrize("edge, ops", ((0.0, 2), (1.0, 3), (4.0, 3)))
def test_the_plate_is_what_is_left_after_the_border_took_its_share(root, edge, ops):
    flat = Skin("dark", overrides={"border.thin": edge})
    held = Widget("button", {"label": "Run"}, name="go", parent=root)
    held.place(10, 10, 100, 24)
    drawn = onepaint(held, flat)
    found = flat.resolve("button", "normal")
    assert found.width == edge
    assert len(drawn.ops) == ops
    left, top, width, height = held.screen_box
    outer = drawn.ops[0]
    assert (outer.get("x"), outer.get("y")) == (left, top)
    assert (outer.get("width"), outer.get("height")) == (width, height)
    # a border of nothing is the one case where the first plate *is* the face
    assert outer.get("color") == (found.fill if edge == 0.0 else found.border)
    share = min(edge, width * 0.5, height * 0.5)
    plate = drawn.last("rect")
    assert (plate.get("x"), plate.get("y")) == (left + share, top + share)
    assert (plate.get("width"), plate.get("height")) == (width - 2 * share,
                                                         height - 2 * share)
    assert plate.get("color") == found.fill


def test_a_border_thicker_than_the_widget_leaves_nothing_to_draw_in(root, skin):
    fat = Skin("dark", overrides={"border.thin": 40.0})
    held = Widget("button", {"label": "Run"}, name="go", parent=root)
    held.place(10, 10, 100, 24)
    drawn = onepaint(held, fat)
    assert drawn.names() == ["rect", "rect"], "the word has no room left in it"
    assert drawn.last("rect").get("height") == 0.0
    assert held.content_box()[3] == 0.0, "the padding and the border ate the box"


def test_relaying_the_tree_in_a_new_skin_re_measures_everything_in_it(root, skin):
    box = Panel(name="box", parent=root)
    box.place(200, 120, 140, 60)
    kid = Widget("button", {"label": "in"}, name="in", parent=box)
    kid.place(0, 0, 60, 20)
    root.sync(skin)
    before = kid.content_box()
    fat = Skin("dark", overrides={"border.thin": 40.0})
    assert root.sync(fat) == root.count()
    assert kid.content_box() != before, "a child two levels down is laid out in it"
    assert kid.content_box()[3] == 0.0
    assert kid.screen_box[0] > box.screen_box[0], "and still inside its panel"
    root.sync(skin)


def test_hovering_changes_the_colour_and_nothing_else(root, skin):
    held = Widget("button", {"label": "Run"}, name="go", parent=root)
    held.place(10, 10, 100, 24)
    calm = onepaint(held, skin)
    held.on_enter()
    lit = onepaint(held, skin)
    assert lit.names() == calm.names()
    assert calm.last("rect").get("color") != lit.last("rect").get("color")
    assert lit.last("rect").get("color") == skin.resolve("button", "hover").fill
    assert lit.ops[0].get("color") == skin.resolve("button", "hover").border


def test_a_mark_is_one_quad_out_of_the_shared_texture(root, skin):
    parts = metronome(root, skin)
    held = parts["run"]
    drawn = onepaint(held, skin)
    assert drawn.count("atlas") == 1
    found = look(held, skin)
    key, uv = drawn.glyph_texture("play", found.font)
    mark = drawn.last("atlas")
    assert (mark.get("texture"), mark.get("uv")) == (key, uv)
    assert mark.get("color") == found.ink, "an icon is a tint, not a file"
    assert held.glyph_name(found) == "play"


def test_switching_the_value_is_a_different_part_of_the_same_texture(root, skin):
    parts = metronome(root, skin)
    held = parts["run"]
    found = look(held, skin)
    drawn = RecordingRenderer()
    drawn.begin(360, 220, 360, 220)
    held.paint(drawn, skin)
    before = drawn.last("atlas")
    held.set("value", 1)
    drawn.begin(360, 220, 360, 220)
    held.paint(drawn, skin)
    after = drawn.last("atlas")
    assert after.get("texture") == before.get("texture"), "no second upload"
    # the two cells have to come out of one atlas, or a fresh atlas per frame
    # would pack both icons into the same first slot and the uvs would agree
    key, uv = drawn.glyph_texture("pause", found.font)
    assert (after.get("texture"), after.get("uv")) == (key, uv)
    assert after.get("uv") != before.get("uv")
    assert held.glyph_name(found) == "pause"


def test_a_mark_and_its_word_are_one_group_centred_in_the_box(root, skin):
    parts = metronome(root, skin)
    held = parts["run"]
    drawn = onepaint(held, skin)
    found = look(held, skin)
    mark, word = drawn.last("atlas"), drawn.last("text")
    inner = held.content_box()
    wide = drawn.measure("Run", found.font)[0]
    assert mark.get("width") == found.font
    assert word.get("x") == pytest.approx(mark.get("x") + found.font * 1.25)
    assert mark.get("x") - inner[0] == pytest.approx(
        inner[0] + inner[2] - (word.get("x") + wide)), "the pair is centred"
    tall = drawn.measure("Run", found.font)[1]
    assert mark.get("y") == word.get("y") == pytest.approx(inner[1] + (inner[3] - tall) * 0.5)


def test_a_mark_on_its_own_is_an_icon_at_the_size_of_its_box(root, skin):
    held = Widget("icon", {"glyph": "play", "label": ""}, name="logo", parent=root)
    held.place(300, 150, 40, 40)
    drawn = onepaint(held, skin)
    assert drawn.names() == ["rect", "rect", "atlas"], "an ICON has no word to carry"
    mark = drawn.last("atlas")
    left, top, width, height = held.content_box()
    side = min(width, height)
    assert (mark.get("width"), mark.get("height")) == (side, side)
    assert (mark.get("x"), mark.get("y")) == (left + (width - side) * 0.5,
                                             top + (height - side) * 0.5)
    assert mark.get("uv") == drawn.glyph_texture("play", side)[1], "rasterized to fit"


def test_the_count_a_widget_returns_is_the_count_it_added(root, skin):
    parts = metronome(root, skin)
    box = Panel(name="card", parent=root)
    box.place(200, 120, 140, 60)
    deep = Widget("button", {"label": "in"}, name="in", parent=box)
    deep.place(0, 0, 60, 20)
    root.sync(skin)
    drawn = RecordingRenderer()
    drawn.begin(360, 220, 360, 220)
    alone = deep.paint(drawn, skin)
    whole = box.paint(drawn, skin)
    assert drawn.op_count == alone + whole
    assert whole > alone, "a panel's count is its face and everything inside it"
    assert drawn.clip is None, "and it hands the clip stack back as it found it"


def test_a_child_inside_a_panel_is_clipped_to_the_panel(root, skin):
    box = Panel(name="box", parent=root)
    box.place(10, 10, 120, 60)
    kid = Widget("button", {"label": "wide"}, name="kid", parent=box)
    kid.place(0, 0, 200, 20)
    root.sync(skin)
    drawn = onepaint(box, skin)
    own, inside = box.clip, kid.clip
    assert own == box.screen_box
    assert inside[2] < kid.screen_box[2], "clipped to the panel, not its own 200"
    faces, kids = drawn.ops[:2], drawn.ops[2:]
    assert len(kids) == 3, "the child's two plates and its word"
    assert [op.clip for op in faces] == [own, own]
    assert [op.clip for op in kids] == [inside, inside, inside]
    assert inside[0] + inside[2] <= own[0] + own[2], "never outside the parent"
    assert drawn.clip is None


# -- the key a cached repaint is kept under -------------------------------------


def test_the_paint_key_is_exactly_what_a_repaint_has_to_depend_on(root, skin):
    parts = metronome(root, skin)
    held = parts["bpm"]
    key = held.paint_key(skin)
    assert held.paint_key(skin) == key, "the same widget, the same picture"
    held.on_enter()
    assert held.paint_key(skin) != key, "the pointer's state is in the props hash"
    held.on_leave()
    assert held.paint_key(skin) == key, "back to the picture already cached"
    held.set("label", "TEMPO")
    moved = held.paint_key(skin)
    assert moved != key
    held.place(56, 60, 200, 24)
    assert held.paint_key(skin) != moved, "a cached painter emits absolute pixels"
    assert held.paint_key(skin.with_theme("light")) != held.paint_key(skin)


def test_two_widgets_with_the_same_props_still_do_not_share_a_cache_row(root, skin):
    one = Widget("slider", {"min": 0, "max": 10, "value": 5}, name="one", parent=root)
    one.place(0, 0, 100, 24)
    other = Widget("slider", {"min": 0, "max": 10, "value": 5}, name="two", parent=root)
    other.place(0, 0, 100, 24)  # the same box on purpose: they are stacked
    assert one.props_hash() == other.props_hash()
    assert one.paint_key(skin)[1:] == other.paint_key(skin)[1:]
    assert one.uid != other.uid
    assert one.paint_key(skin) != other.paint_key(skin), "a row is per widget"
    assert one.paint_key(skin)[0] == one.uid


def test_painting_the_same_tree_twice_costs_the_same_twice(root, skin):
    parts = metronome(root, skin)
    first, painted = frame(root, skin)
    second, again = frame(root, skin)
    assert again == painted
    assert [op.name for op in first.frames[0]] == [op.name for op in second.frames[0]]


def test_a_headless_run_is_told_the_same_thing_as_a_recorded_one(root, skin):
    parts = metronome(root, skin)
    drawn, painted = frame(root, skin)
    quiet = NullRenderer()
    quiet.begin(360, 220, 360, 220)
    quiet.clear(skin.colour("window"))
    said = root.paint(quiet, skin)
    quiet.end()
    assert said == painted, "no script may need a mode branch"
    assert quiet.total() == len(drawn.ops)
    # the title, the slider's and the toggle's labels, and the number box's readout
    assert quiet.count("text") == drawn.count("text") == 4
    assert quiet.begins == quiet.ends == 1


# -- the router and the tree, together ----------------------------------------


def router_for(root, **extra) -> InputRouter:
    """The wiring the plan describes: GLFW -> router -> hit test -> widgets."""
    return InputRouter(lambda x, y: top_at(root, x, y), width=360, height=220,
                       root=root, **extra)


def hear(*kids) -> list:
    """A record of every event each of these widgets was told, with its name.

    Chained rather than dropped in: whatever the widget already had - its kind's
    own behaviour, since S8 - still runs, and runs first, which is what
    :meth:`~crude.ui.handlers.Handlers.adopt` does for a script's ``ON_CLICK``.
    A listener that replaced the slot would be listening to a widget no frame can
    produce, and would be quietly switching the behaviour off while doing it.
    """
    heard = []
    for held in kids:
        before = held.on_event

        def relay(who, name, *args, prev=before, sink=heard):
            if callable(prev):
                prev(who, name, *args)
            sink.append((who.name, name, args))

        held.on_event = relay
    return heard


def test_the_pointer_arrives_before_it_moves_and_leaves_when_it_goes(root, skin):
    parts = metronome(root, skin)
    router = router_for(root)
    router.move_to(*inside(parts["run"]))
    assert router.names() == ["enter", "move"]
    assert router.hover is parts["run"]
    router.move_to(350, 210)
    assert router.names()[-3:] == ["leave", "enter", "move"]
    assert router.hover is root, "the window is a widget, so nothing is left dangling"
    assert parts["run"].hovered is False


def test_a_click_reaches_the_widget_in_the_order_the_person_made_it(root, skin):
    parts = metronome(root, skin)
    held = parts["run"]
    heard = hear(held)
    router = router_for(root)
    assert router.click(*inside(held)) is held
    assert router.names() == ["enter", "move", "focus", "press", "release", "click"]
    assert [word for _, word, _ in heard] == [
        "on_enter", "on_move", "on_focus", "on_press", "on_release", "on_click"]
    assert (held.hovered, held.focused, held.pressed) == (True, True, False)
    assert held.value == 1.0, "a toggle flips on a click, from the behaviour table"
    assert held.take_changes() == ["value"]


def test_a_drag_belongs_to_the_widget_that_took_it_not_the_one_under_it(root, skin):
    parts = metronome(root, skin)
    held = parts["bpm"]
    heard = hear(held)
    router = router_for(root)
    router.move_to(*inside(held))
    router.press()
    assert held.pressed is True and router.capture is held
    router.move_to(350, 210)
    assert router.hover is root and router.capture is held
    assert held.pressed is True, "it is still the thumb's owner while the button is down"
    assert [word for _, word, _ in heard] == [
        "on_enter", "on_move", "on_focus", "on_press", "on_drag", "on_leave"]
    assert "drag" in router.names()
    router.release()
    assert held.pressed is False and router.capture is None
    assert "click" not in router.names()


def test_keys_and_characters_go_to_the_one_widget_holding_the_keyboard(root, skin):
    parts = metronome(root, skin)
    heard = hear(parts["bpm"], parts["tempo"])
    router = router_for(root)
    router.click(*inside(parts["bpm"]))
    del heard[:]
    router.key_typed("up")
    assert heard == [("bpm", "on_key", ("up", 0))]
    assert router.names()[-1] == "key"
    router.type_text("12")
    assert [args[0] for _, word, args in heard[-2:]] == ["1", "2"]
    assert router.names()[-2:] == ["text", "text"]
    assert heard[1][1] == "on_char"


def test_a_hotkey_is_answered_before_the_field_with_the_keyboard_hears_it(root, skin):
    parts = metronome(root, skin)
    heard = hear(parts["tempo"])
    saved = []
    router = router_for(root, hotkeys={"control+s": lambda: saved.append(1)})
    router.click(*inside(parts["tempo"]))
    del heard[:]
    router.key_typed("s", mods=MODS["control"])
    assert saved == [1], "the combo is every modifier word, in a fixed order"
    assert heard == [] and router.names()[-1] == "hotkey"
    router.key_typed("s")
    assert [word for _, word, _ in heard] == ["on_key"], "plain s is not control+s"


def test_the_wheel_is_a_scroll_for_a_widget_that_listens(root, skin):
    parts = metronome(root, skin)
    heard = hear(parts["bpm"])
    router = router_for(root)
    router.move_to(*inside(parts["bpm"]))
    router.scroll_at(-1)
    assert router.names()[-1] == "scroll", "a widget always answers for its own"
    assert [word for _, word, _ in heard][-1] == "on_scroll"
    assert heard[-1][2][1] < 0, "the notch it was given, in fine pixels"


def test_a_widget_that_left_under_the_pointer_is_told_to_go(root, skin):
    parts = metronome(root, skin)
    router = router_for(root)
    router.move_to(*inside(parts["run"]))
    assert router.hover is parts["run"]
    parts["run"].detach()
    router.move_to(*inside(parts["bpm"]))
    assert router.hover is parts["bpm"]
    assert parts["run"].hovered is False, "it was still told, even with no tree"
    assert router.names()[-3:] == ["leave", "enter", "move"]


def test_a_window_that_changed_size_moves_the_root_and_re_measures_the_tree(
        root, skin):
    parts = metronome(root, skin)
    heard = hear(root)
    router = router_for(root)
    router.on_resize(600, 400)
    assert router.dispatch() == 1
    assert router.names() == ["resize"]
    assert root.screen_box == (0.0, 0.0, 600.0, 400.0)
    assert (router.width, router.height) == (600.0, 400.0)
    # the root has no inset of its own, so a placement is a placement in the window
    assert parts["title"].screen_box == (16.0, 12.0, 200.0, 20.0)
    assert parts["title"].clip == parts["title"].screen_box
    assert heard[-1][1] == "on_resize" and heard[-1][2] == (600.0, 400.0)


def test_only_the_root_of_a_tree_answers_for_the_window(root, skin):
    parts = metronome(root, skin)
    assert parts["bpm"].on_resize(10, 10) is False, "it is the size its layout made it"
    assert parts["bpm"].screen_box == (56.0, 40.0, 200.0, 24.0)
    loose = Widget("button", name="loose")
    assert loose.on_resize(20, 20) is False, "and nothing has laid it out yet"


def test_the_windows_own_events_are_the_roots_to_answer(root, skin):
    parts = metronome(root, skin)
    heard = hear(root)
    router = router_for(root)
    assert router.on_close_request() is None, "a tree with no bindings lets it go"
    assert heard == [("root", "on_close", ())]
    del heard[:]
    router.on_drop(["demo.wav"])
    router.dispatch()
    assert heard == [("root", "on_drop", (("demo.wav",),))]
    assert router.names() == ["close", "drop"]
    assert router.close_allowed is True


# -- how a widget describes itself ---------------------------------------------


def test_a_widget_can_be_read_without_being_touched(root, skin):
    parts = metronome(root, skin)
    held = parts["bpm"]
    said = held.as_dict()
    assert set(said) == {"path", "uid", "kind", "container", "box", "screen_box",
                         "clip", "z", "children", "state", "focusable", "pd_object",
                         "props"}
    assert said["path"] == "root/bpm" and said["kind"] == "slider"
    assert said["container"] is False and said["children"] == 0
    assert said["box"] == [56.0, 40.0, 200.0, 24.0]
    assert said["screen_box"] == [56.0, 40.0, 200.0, 24.0]
    assert said["z"] == 2, "title, meter, slider: the order the script placed them"
    assert said["state"] == "normal" and said["focusable"] is True
    assert said["pd_object"] == "hslider"
    assert said["props"]["value"] == 120.0
    assert "hovered" in said["props"], "a report shows the state the pointer is in"
    assert "props" not in held.as_dict(props=False)


def test_a_panel_reports_the_room_it_holds(root, skin):
    box = Panel(name="box", parent=root)
    box.place(10, 10, 120, 60)
    Widget("button", name="in", parent=box)
    root.sync(skin)
    said = box.as_dict()
    assert said["container"] is True and said["children"] == 1
    assert said["path"] == "root/box" and said["focusable"] is False
    assert said["pd_object"] is None or said["pd_object"] == "cnv"


def test_the_words_a_widget_gives_are_the_ones_a_report_prints(root, skin):
    parts = metronome(root, skin)
    held = parts["bpm"]
    assert str(held) == "Widget(slider 'bpm' 200x24 @ 56,40, normal)"
    assert repr(held) == "<Widget root/bpm slider>"
    lines = root.tree()
    assert lines[0] == "root 360x220 @ 0,0 (5)"
    assert lines[1] == "  root/title 200x20 @ 16,12"
    assert lines[-1] == "  root/run 96x28 @ 16,84"
    assert len(lines) == root.count()


@pytest.mark.parametrize("value, said", (
        (None, ""), (True, "true"), (False, "false"), (120.0, "120"),
        (0.25, "0.25"), (1234567.8, "1234567.8"), (1e20, "1e+20"),
        (-0.5, "-0.5"), (7, "7"), ("BPM", "BPM"), (["sine", "tri"], "sine tri"),
        ((), ""),
))
def test_a_value_is_written_the_way_a_person_would_type_it(value, said):
    assert format_value(value) == said


def test_a_number_box_and_a_slider_read_the_same_number_the_same_way(root, skin):
    parts = metronome(root, skin)
    assert parts["bpm"].value_text() == parts["tempo"].value_text() == "120"
    parts["bpm"].set("value", 137.5)
    parts["tempo"].set("value", 137.5)
    assert parts["bpm"].value_text() == parts["tempo"].value_text() == "137.5"
    parts["bpm"].set("min", 0)
    parts["bpm"].set("value", 0.25)
    assert parts["bpm"].value_text() == "0.25", "a range widened before it fitted"
