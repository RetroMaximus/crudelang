"""The frame: one layout, the pointer's events, the tree in z-order, then a swap.

:mod:`crude.ui.context` is the layer that knows a frame exists, and this file is
the plan's S7 acceptance line for it - "RecordingRenderer assertions on a
script-built tree; anchor/DPI/z-order/hit-test math tests; callbacks fire" - read
as four separate questions:

* **the order.** ``r.begin -> ctx.layout() -> widgets in z-order -> r.end ->
  window.swap``, with the input dispatch *before* the begin: a press has to be
  hit-tested against the geometry the person was looking at when they pressed, so
  the frame is the boundary at which a resize is taken up rather than something
  that happens underneath it;
* **the arithmetic.** :func:`~crude.ui.layout.resolve` is called per frame with
  the context's two sizes, and its one rounding is the *resolved* boxes only - a
  script's numbers are never rewritten, or a ``grow`` fraction would drift a frame
  at a time and a shared edge between two stacked buttons would gain a hairline of
  background at a 1.25 scale;
* **the pointer.** the router's hit test is this tree's, and the focus is the
  tree's rather than the router's, so a script aiming the keyboard at a widget has
  it aimed where the script thinks it is;
* **the parity.** the same pass on a :class:`~crude.ui.null_renderer.NullRenderer`
  with painting off moves the same boxes, fires the same handlers and records
  nothing - the plan's "the metronome LED blinks invisibly".

Nothing here needs a display: the window in these tests is an object with
``size``, ``framebuffer_size``, ``swap`` and ``set_input_listener``, which is
exactly the seam :meth:`~crude.ui.gl.window.Window.set_input_listener` copies.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from crude.ui import (ACTIONS, BUDGET_MS, DEFAULT_UI_SIZE, FrameReport, MODS,
                      MOUSE_BUTTONS, NullRenderer, Panel, RecordingRenderer,
                      Skin, UIContext, UIContextError, Widget, anchor, hstack,
                      pack, resolve, root_panel, vstack, window_context)

#: a client rect big enough for two controls and small enough to read in a
#  failure: nothing in this file needs the plan's 360 x 220 except the default
RECT = (200.0, 100.0)

#: the nine names :meth:`crude.ui.gl.window.Window.set_input_listener` copies off
#  whatever is installed as a listener. Written out here rather than imported, so
#  that this file stays in the GL-free half of the suite - and if the window ever
#  asks for a tenth, the test below is the one that says so.
LISTENER_NAMES = ("on_resize", "on_framebuffer_resize", "on_cursor",
                  "on_button", "on_key", "on_char", "on_scroll",
                  "on_close_request", "on_drop")


def stage(width: float = RECT[0], height: float = RECT[1],
          **wanted: Any) -> Tuple[UIContext, RecordingRenderer]:
    """A context on its own fresh recorder - the plan's front panel, at a size."""
    held = RecordingRenderer()
    return UIContext(held, width=width, height=height, **wanted), held


def labelled(ctx: UIContext, name: str, box=(4, 4, 80, 24),
             kind: str = "button") -> Widget:
    """A control with its own name on it, so a recorder can tell them apart."""
    held = Widget(kind, {"label": name}, name=name, parent=ctx.root)
    held.place(*box)
    return held


def heard(*widgets: Widget) -> List[Tuple[str, str, tuple]]:
    """Every handler call those widgets make, as ``(widget, event, args)``."""
    out: List[Tuple[str, str, tuple]] = []
    for held in widgets:
        held.on_event = lambda who, name, *args: out.append(
            (who.name, name, args))
    return out


def spot(held: Widget) -> tuple:
    """The box that is drawn and hit, to three places."""
    return tuple(round(value, 3) for value in held.screen_box)


def box(held: Widget) -> tuple:
    """The box the script wrote, untouched by the grid, to three places."""
    return tuple(round(value, 3) for value in held.box)


# --------------------------------------------------------------------------
# what a context is built from
# --------------------------------------------------------------------------


def test_a_context_with_nothing_said_has_the_plan_s_front_panel():
    ctx = UIContext()
    assert (ctx.width, ctx.height) == DEFAULT_UI_SIZE
    assert (ctx.fb_width, ctx.fb_height) == (360, 220)
    assert ctx.dpr == 1.0 and ctx.grid == 1.0
    assert ctx.root.path() == "root" and ctx.root.is_container
    assert ctx.widgets() == [ctx.root]
    assert isinstance(ctx.renderer, NullRenderer), "no renderer means no window"
    assert ctx.skin.theme.name == "dark"
    assert ctx.background == ctx.skin.colour("window")


def test_the_size_a_context_is_given_is_the_root_s_own_box():
    ctx, rec = stage(320, 240)
    assert box(ctx.root) == (0.0, 0.0, 320.0, 240.0)
    assert (ctx.fb_width, ctx.fb_height) == (320, 240), "one device pixel each"
    assert ctx.dpr == 1.0


def test_the_two_sizes_a_window_reports_are_the_two_the_grid_is_measured_from():
    ctx, rec = stage(320, 200, fb_width=640, fb_height=400)
    assert ctx.dpr == 2.0
    assert ctx.grid == 2.0, "snapping is on, so the layout gets the ratio"
    assert ctx.snapping is True


@pytest.mark.parametrize("word", ["dark", "flat", "light"])
def test_a_skin_name_is_a_theme_and_a_skin_object_is_kept_as_it_is(word):
    by_name = UIContext(skin=word)
    assert by_name.skin.theme.name == word
    built = Skin(word, "sm")
    assert UIContext(skin=built).skin is built, "a skin is not copied"


def test_a_root_that_holds_nothing_is_refused_by_the_name_of_what_it_is():
    with pytest.raises(UIContextError) as raised:
        UIContext(root=Widget("button", name="one"))
    assert "holds the other widgets" in str(raised.value)
    assert "'button'" in str(raised.value)


def test_a_root_that_is_inside_something_else_is_refused():
    inner = Panel(name="inner")
    root_panel(10, 10).add(inner)
    with pytest.raises(UIContextError) as raised:
        UIContext(root=inner)
    assert "the UI root *is* the window's client rect" in str(raised.value)


def test_a_context_takes_a_root_it_did_not_build_and_lays_it_out():
    root = root_panel(60, 40, name="panel")
    ctx = UIContext(root=root)
    assert ctx.root is root
    assert (ctx.width, ctx.height) == (60.0, 40.0), "the root's box is the size"
    ctx.layout()
    assert spot(root) == (0.0, 0.0, 60.0, 40.0)


def test_the_tab_keys_are_bound_unless_the_project_asked_otherwise():
    assert set(UIContext().router.hotkeys) == {"tab", "shift+tab"}
    assert UIContext(keys=False).router.hotkeys == {}


def test_an_error_names_the_window_it_was_asked_about():
    ctx, rec = stage(name="mixer")
    with pytest.raises(UIContextError) as raised:
        ctx.draw()
    assert "mixer" in str(raised.value)
    assert repr(ctx) == "<UIContext 'mixer' 200x100>"


# --------------------------------------------------------------------------
# the draw pass, in the plan's order
# --------------------------------------------------------------------------


def test_one_frame_begins_at_the_context_s_two_sizes_and_ends_once():
    ctx, rec = stage(320, 200, fb_width=640, fb_height=400)
    ctx.frame()
    assert rec.sizes == [(320.0, 200.0, 640, 400)]
    assert len(rec.frames) == 1
    assert ctx.frames == 1


def test_the_first_op_of_a_frame_is_the_clear_in_the_skin_s_window_colour():
    ctx, rec = stage(skin="light")
    ctx.frame()
    first = rec.ops[0]
    assert first.name == "clear"
    assert first.get("color") == ctx.background
    assert ctx.background == rec.last("clear").get("color")


def test_the_background_follows_the_skin_the_context_is_laid_out_with():
    ctx, rec = stage()
    dark = ctx.background
    ctx.set_theme("light")
    ctx.frame()
    assert ctx.background != dark
    assert rec.last("clear").get("color") == ctx.background


def test_a_frame_draws_the_root_first_and_then_its_children_in_z_order():
    ctx, rec = stage()
    lower = labelled(ctx, "one", (4, 4, 120, 24))
    upper = labelled(ctx, "two", (4, 4, 120, 24))
    ctx.frame()
    assert rec.texts() == ["one", "two"], "bottom of the stack is drawn first"
    assert rec.count("clear") == 1
    assert upper is not lower and ctx.hit(10, 10) is upper


def test_raising_a_widget_moves_it_in_the_picture_and_in_the_hit_test():
    ctx, rec = stage()
    lower = labelled(ctx, "one", (4, 4, 120, 24))
    labelled(ctx, "two", (4, 4, 120, 24))
    lower.raise_to_top()
    ctx.frame()
    assert rec.texts() == ["two", "one"]
    assert ctx.hit(10, 10) is lower
    assert ctx.top_down()[0] is lower


def test_a_widget_that_is_not_visible_is_laid_out_but_not_drawn():
    ctx, rec = stage()
    held = labelled(ctx, "one", (4, 4, 120, 24))
    held.set("visible", False)
    ctx.frame()
    assert rec.texts() == [], "the tree was laid out but not painted"
    assert ctx.hit(10, 10) is ctx.root
    held.set("visible", True)
    ctx.frame()
    assert rec.texts() == ["one"]


def test_the_report_s_op_count_is_the_count_the_recorder_kept():
    ctx, rec = stage()
    labelled(ctx, "one")
    report = ctx.frame()
    assert report.ops == len(rec.frames[0])
    assert report.ops == ctx.ops == len(rec.ops)
    assert report.widgets == ctx.root.count() == 2


def test_the_hand_steps_of_a_frame_draw_the_same_picture_as_frame_does():
    """``begin -> layout -> draw -> end`` is public in that order, on purpose: a
    runtime that has to drain a Pd inbox between the layout and the draw gets the
    same frame, not a second best."""
    manual, manual_rec = stage()
    labelled(manual, "one", (4, 4, 120, 24))
    manual.begin()
    manual.layout()
    manual.draw()
    manual.end()
    whole, whole_rec = stage()
    labelled(whole, "one", (4, 4, 120, 24))
    whole.frame()
    assert manual_rec.names() == whole_rec.names()
    assert manual_rec.rects() == whole_rec.rects()
    assert manual.ops == whole.ops


def test_a_frame_begun_twice_is_refused_before_anything_is_drawn():
    ctx, rec = stage()
    ctx.begin()
    with pytest.raises(UIContextError) as raised:
        ctx.begin()
    assert "was begun and never ended" in str(raised.value)
    assert rec.sizes == [(RECT[0], RECT[1], 200, 100)], "only one frame opened"


def test_drawing_into_a_frame_that_was_never_begun_is_refused():
    ctx, rec = stage()
    labelled(ctx, "one")
    with pytest.raises(UIContextError) as raised:
        ctx.draw()
    assert "no frame to draw into" in str(raised.value)
    assert rec.ops == []


def test_ending_a_frame_that_was_never_begun_is_refused():
    ctx, rec = stage()
    with pytest.raises(UIContextError) as raised:
        ctx.end()
    assert "ended that was never begun" in str(raised.value)
    assert rec.frames == []


def test_a_backend_that_falls_over_mid_frame_leaves_the_next_one_normal():
    """One raise must not wedge the window: the frame is closed, counted once, and
    :meth:`UIContext.abort` is the call a runtime's ``except`` makes."""
    ctx, rec = stage()
    labelled(ctx, "one")
    real = rec.clear

    def angry(color: Any) -> None:
        real(color)
        raise RuntimeError("the fell over part way through")

    rec.clear = angry
    with pytest.raises(RuntimeError):
        ctx.frame()
    assert ctx.stats()["begun"] is True
    with pytest.raises(UIContextError):
        ctx.begin()
    assert ctx.abort() is True
    assert ctx.abort() is False, "there was only the one frame to close"
    assert len(rec.frames) == 1
    assert ctx.frames == 0, "an unfinished frame is not a frame"
    rec.clear = real
    assert ctx.frame().ops == len(rec.frames[1])


# --------------------------------------------------------------------------
# the swap, which is a window's to hand in rather than this layer's to call
# --------------------------------------------------------------------------


def test_the_swap_is_called_once_per_frame_when_the_context_was_handed_one():
    seen: List[int] = []
    ctx, rec = stage(swap=lambda: seen.append(len(seen)))
    assert ctx.frame().swapped is True
    ctx.frame()
    assert seen == [0, 1]
    assert len(rec.frames) == 2


def test_no_swap_callable_means_the_report_says_the_frame_was_not_shown():
    ctx, rec = stage()
    report = ctx.frame()
    assert report.swapped is False
    assert "not swapped" in str(report)


def test_a_frame_can_hold_its_picture_back_for_one_call():
    seen: List[str] = []
    ctx, rec = stage(swap=lambda: seen.append("window"))
    assert ctx.frame(swap=False).swapped is False
    assert seen == [], "False beats the window's own callable for this frame"
    assert ctx.frame().swapped is True
    assert seen == ["window"]


def test_a_callable_handed_to_one_frame_is_used_instead_of_the_window_s():
    seen: List[str] = []
    ctx, rec = stage(swap=lambda: seen.append("window"))
    ctx.frame(swap=lambda: seen.append("given"))
    assert seen == ["given"]


def test_a_swap_that_raises_is_the_window_s_problem_not_the_frame_s():
    def broken() -> None:
        raise RuntimeError("the window went away")

    ctx, rec = stage(swap=broken)
    with pytest.raises(RuntimeError):
        ctx.frame()
    assert len(rec.frames) == 1, "the frame was closed before the swap was tried"
    assert ctx.stats()["begun"] is False


# --------------------------------------------------------------------------
# what a frame reports
# --------------------------------------------------------------------------


def test_the_report_counts_the_events_the_boxes_and_the_widgets():
    ctx, rec = stage()
    labelled(ctx, "one", (4, 4, 120, 24))
    ctx.router.on_cursor(10, 10)
    report = ctx.frame()
    assert report == FrameReport(events=1, moved=0, ops=report.ops,
                                 widgets=2, frames=1, dpr=1.0, swapped=False)
    assert report.as_dict()["events"] == 1
    assert report.as_dict()["dpr"] == 1.0


def test_a_report_describes_itself_in_the_words_the_budget_is_asked_in():
    ctx, rec = stage()
    labelled(ctx, "one")
    report = ctx.frame(swap=lambda: None)
    assert str(report) == (f"frame 1: {report.ops} op(s), 2 widget(s), "
                           f"0 box(es) moved, 0 event(s) in {report.ms:.2f} ms")
    assert "swapped" not in str(report), "the swap it did make is not said twice"


def test_a_frame_carries_the_milliseconds_its_own_pass_took():
    """The plan asks whether a frame is over 2 ms, and only this layer can say: the
    number is the frame, not the ``ON_FRAME`` handler that ran before it, and it is
    read once, at the end, so a report is a snapshot of a finished frame."""
    ctx, rec = stage()
    labelled(ctx, "one")
    assert ctx.last_ms == 0.0, "no frame has been run yet"
    report = ctx.frame()
    assert report.ms > 0.0
    assert report.ms == ctx.last_ms, "one number, read once"
    assert ctx.stats()["last_ms"] == round(report.ms, 3)
    assert report.over_budget(0.0) is True
    assert report.over_budget(report.ms + 1000.0) is False


def test_the_budget_the_report_is_measured_against_is_the_plan_s():
    assert BUDGET_MS == 2.0
    assert FrameReport(ms=2.5).over_budget() is True
    assert FrameReport(ms=1.5).over_budget() is False
    assert FrameReport().over_budget() is False, "an unmeasured report is not late"


def test_the_milliseconds_are_not_part_of_what_a_frame_drew():
    """Two frames that laid out and painted the same picture are equal even though
    no clock ever reads the same twice: a test compares facts, not timings."""
    ctx, rec = stage()
    labelled(ctx, "one")
    first, second = ctx.frame(), ctx.frame()
    assert first != second, "the frame number moved"
    assert FrameReport(ops=first.ops, frames=1, ms=0.1) == \
        FrameReport(ops=first.ops, frames=1, ms=99.0)
    assert first.as_dict()["ms"] == round(first.ms, 3)


def test_the_hand_steps_of_a_frame_do_not_claim_a_measurement_they_never_took():
    """Only :meth:`UIContext.frame` owns the clock: a runtime that begins a frame,
    drains a Pd inbox inside it and ends it is not being timed on this layer's
    authority, so :attr:`last_ms` stays at the whole frame that last ran."""
    ctx, rec = stage()
    labelled(ctx, "one")
    taken = ctx.frame().ms
    ctx.begin()
    ctx.layout()
    ctx.draw()
    ctx.end()
    assert ctx.last_ms == taken
    assert ctx.frames == 2, "the hand steps are still a frame to the counter"


def test_each_report_is_a_snapshot_of_the_frame_that_ended():
    ctx, rec = stage()
    first = ctx.frame()
    second = ctx.frame()
    assert (first.frames, second.frames) == (1, 2)
    assert first.ops == second.ops
    assert ctx.frames == 2


def test_the_frame_counters_only_move_when_a_frame_is_finished():
    ctx, rec = stage()
    ctx.begin()
    ctx.layout()
    assert ctx.frames == 0
    ctx.end()
    assert ctx.frames == 1
    assert ctx.stats()["begun"] is False


# --------------------------------------------------------------------------
# the layout pass, which is this layer's per-frame caller of resolve()
# --------------------------------------------------------------------------


def thirds(width: float, fb: int) -> Tuple[UIContext, RecordingRenderer, list]:
    """Three ``fill`` children of a zero-gap row: the layout's roundest fraction."""
    held = RecordingRenderer()
    ctx = UIContext(held, root=root_panel(width, 60), width=width, height=60,
                    fb_width=fb, fb_height=int(fb * 60 / width))
    pool = []
    for index in range(3):
        kid = Widget("button", name=f"k{index}", parent=ctx.root)
        kid.place(0, 0, 30, 10)
        pack(kid, "fill", "fixed")
        pool.append(kid)
    hstack(ctx.root, 0)
    return ctx, held, pool


def test_the_first_frame_is_what_makes_a_pack_rule_bite():
    ctx, rec = stage()
    held = labelled(ctx, "wide", (4, 4, 80, 24))
    pack(held, "fill")
    assert box(held) == (4.0, 4.0, 80.0, 24.0), "PLACE wrote it; nothing measured it"
    report = ctx.frame()
    assert box(held) == (4.0, 4.0, 196.0, 96.0)
    assert report.moved == 1 == ctx.moved


def test_a_resize_is_taken_up_by_the_frame_that_follows_it():
    """The context's size is the root's box, so a window resize needs no rebuild:
    the same widget, the same props, a new measurement."""
    ctx, rec = stage()
    held = labelled(ctx, "wide", (4, 4, 80, 24))
    pack(held, "fill")
    ctx.frame()
    ctx.set_size(400, 300)
    report = ctx.frame()
    assert spot(held) == (4.0, 4.0, 396.0, 296.0)
    assert report.moved == 1
    assert (ctx.width, ctx.height) == (400.0, 300.0)
    assert rec.sizes[-1] == (400.0, 300.0, 400, 300), "begun at the new size"


def test_a_window_resize_reaches_both_sizes_before_the_frame_is_begun():
    """The GLFW resize is stored at once rather than queued, because the frame that
    follows it still happens in the same tick: a picture laid out at the new size
    and begun at the old one is a frame stretched by the viewport."""
    ctx, rec = stage(200, 100, fb_width=400, fb_height=200)
    ctx.on_resize(400, 200)
    assert (ctx.width, ctx.height) == (400.0, 200.0)
    assert ctx.router.width == 400.0, "the pointer's space grew with it"
    ctx.frame()
    assert rec.sizes[-1] == (400.0, 200.0, 400, 200)
    assert ctx.dpr == 1.0


def test_a_framebuffer_resize_changes_the_grid_and_nothing_else():
    ctx, rec = stage(200, 100)
    ctx.on_framebuffer_resize(400, 200)
    assert (ctx.fb_width, ctx.fb_height) == (400, 200)
    assert ctx.dpr == 2.0
    assert (ctx.width, ctx.height) == (200.0, 100.0), "the logical rect is untouched"
    ctx.frame()
    assert spot(ctx.root) == (0.0, 0.0, 200.0, 100.0)
    assert rec.sizes[-1] == (200.0, 100.0, 400, 200)
    assert ctx.router.dpr == 2.0, "the router measured the same event"


def test_an_idle_frame_moves_nothing_and_changes_no_paint_key():
    ctx, rec = stage()
    labelled(ctx, "one", (4, 4, 120, 24))
    ctx.frame()
    keys = [held.paint_key(ctx.skin) for held in ctx.widgets()]
    report = ctx.frame()
    assert report.moved == 0
    assert [held.paint_key(ctx.skin) for held in ctx.widgets()] == keys


def test_layout_is_idempotent_inside_one_frame():
    ctx, rec = stage()
    held = labelled(ctx, "wide", (4, 4, 80, 24))
    pack(held, "fill")
    ctx.begin()
    assert ctx.layout() == 1
    assert ctx.layout() == 0, "the second pass in the same frame has nothing to say"
    ctx.end()


def test_the_context_s_layout_is_the_pass_resolve_would_do_alone():
    """``resolve(root, skin, width=, height=, dpr=)`` is the per-frame caller, so the
    context must not add arithmetic of its own: the same tree laid out by hand
    gives the same boxes, to the device pixel."""
    built, _rec, _kids = thirds(100, 125)
    built.layout()
    manual = root_panel(100, 60)
    for index in range(3):
        kid = Widget("button", name=f"k{index}", parent=manual)
        kid.place(0, 0, 30, 10)
        pack(kid, "fill", "fixed")
    hstack(manual, 0)
    resolve(manual, built.skin, width=100, height=60, dpr=1.25)
    assert [spot(held) for held in manual.walk()] == [
        spot(held) for held in built.widgets()]


def test_a_stack_re_measures_every_child_from_the_new_length():
    """The arithmetic alone, with the grid off: a row divides the *new* length,
    so a resize is one pass rather than a rebuild of the panel."""
    ctx, rec, kids = thirds(100, 100)
    ctx.snapping = False
    ctx.frame()
    assert spot(kids[1]) == pytest.approx((33.333, 0.0, 33.333, 10.0), abs=1e-3)
    ctx.set_size(200, 60)
    ctx.frame()
    assert spot(kids[0]) == pytest.approx((0.0, 0.0, 66.667, 10.0), abs=1e-3)
    assert spot(kids[2]) == pytest.approx((133.333, 0.0, 66.667, 10.0), abs=1e-3)


def test_an_anchor_is_re_resolved_when_the_thing_it_glued_to_grew():
    ctx, rec = stage()
    deck = Widget("panel", name="deck", parent=ctx.root)
    deck.place(8, 8, 100, 40)
    pack(deck, "grow", "fixed")
    deck.place(8, 8, 100, 40)
    badge = Widget("button", {"label": "b"}, name="badge", parent=ctx.root)
    badge.place(0, 0, 20, 20)
    anchor(badge, "left", deck, "right", 4)
    anchor(badge, "top", deck, "top", 0)
    ctx.frame()
    assert spot(badge) == (112.0, 8.0, 20.0, 20.0)
    ctx.set_size(400, 200)
    report = ctx.frame()
    assert spot(deck) == (8.0, 8.0, 200.0, 40.0), "half the wider window"
    assert spot(badge)[0] == 212.0, "glued to the deck's new right edge"
    assert report.moved == 2, "the deck grew and the badge followed: two writes"


def test_a_widget_added_between_two_frames_is_in_the_next_picture():
    ctx, rec = stage()
    labelled(ctx, "one", (4, 4, 120, 24))
    ctx.frame()
    assert rec.texts() == ["one"]
    rec.reset()
    two = ctx.add(Widget("button", {"label": "two"}, name="two"))
    two.place(4, 32, 120, 24)
    assert two.parent is ctx.root and two in ctx.widgets()
    report = ctx.frame()
    assert rec.texts() == ["one", "two"]
    assert report.widgets == 3


def test_the_root_box_is_the_client_rect_the_context_was_told():
    ctx, rec = stage(320, 240)
    ctx.frame()
    assert spot(ctx.root) == (0.0, 0.0, 320.0, 240.0)
    ctx.set_size(80, 60)
    ctx.frame()
    assert spot(ctx.root) == (0.0, 0.0, 80.0, 60.0)


def test_a_new_skin_is_the_next_frame_s_colours_and_metrics():
    ctx, rec = stage(skin="dark")
    labelled(ctx, "one", (4, 4, 120, 24))
    ctx.frame()
    dark = rec.last("clear").get("color")
    ctx.set_theme("light")
    ctx.frame()
    assert ctx.background != dark
    assert rec.last("clear").get("color") != dark
    assert ctx.skin.theme.name == "light"


def test_setting_the_size_to_what_it_already_is_says_so():
    ctx, rec = stage()
    assert ctx.set_size(200, 100) is False
    assert ctx.set_size(200, 120) is True
    assert ctx.height == 120.0


# --------------------------------------------------------------------------
# the device grid: the plan's one rounding, in the resolved geometry only
# --------------------------------------------------------------------------


def test_the_grid_the_boxes_are_snapped_to_is_the_frame_s_own_ratio():
    ctx, rec, kids = thirds(100, 125)
    assert ctx.dpr == 1.25 and ctx.grid == 1.25
    ctx.frame()
    assert spot(kids[0])[2] == 33.6, "42 device pixels, not 41.666 of one"


def test_every_resolved_edge_lands_on_a_whole_device_pixel():
    ctx, rec, kids = thirds(100, 125)
    ctx.frame()
    for held in ctx.widgets():
        left, top, width, height = held.screen_box
        for edge in (left, top, left + width, top + height):
            assert abs(edge * ctx.dpr - round(edge * ctx.dpr)) < 1e-9, held.name


def test_two_boxes_that_share_an_edge_still_share_it_after_the_rounding():
    """The reason the rounding is per edge and not per width: a row of stacked
    buttons whose second left edge rounds one way and whose first right edge
    rounds the other shows a hairline of background between them."""
    ctx, rec, kids = thirds(100, 125)
    ctx.frame()
    first, second, third = kids
    assert spot(first)[0] + spot(first)[2] == spot(second)[0]
    assert spot(second)[0] + spot(second)[2] == spot(third)[0]
    assert spot(third)[0] + spot(third)[2] == 100.0, "the row still ends at the edge"


def test_the_picture_and_the_hit_test_agree_at_a_fractional_scale():
    """A point between the logical third and the drawn one belongs to the widget
    that was *drawn* there: hit testing reads the same snapped box as painting."""
    ctx, rec, kids = thirds(100, 125)
    ctx.frame()
    first, second, _third = kids
    assert ctx.hit(33.5, 5) is first, "33.5 is inside the 33.6 that is on screen"
    assert ctx.hit(33.7, 5) is second
    assert ctx.router.click(33.5, 5) is first
    assert ctx.hover is first and ctx.focus is first


def test_the_numbers_a_script_wrote_are_never_touched_by_the_grid():
    """The rounding is a property of the drawn box, not of the layout's answer: a
    ``grow`` fraction measured from a snapped width would drift a frame at a time,
    and the person would watch a row of buttons slide out of the window."""
    ctx, rec, kids = thirds(100, 125)
    ctx.frame()
    wrote = [box(held) for held in kids]
    for _round in range(4):
        ctx.frame()
    assert [box(held) for held in kids] == wrote
    assert box(kids[0])[2] == pytest.approx(100 / 3, abs=1e-3)
    assert round(spot(kids[0])[2], 4) == 33.6


def test_five_frames_at_a_fractional_scale_settle_after_the_first():
    ctx, rec, kids = thirds(100, 125)
    assert [ctx.frame().moved for _round in range(5)] == [3, 0, 0, 0, 0]


def test_turning_snapping_off_leaves_every_resolved_number_logical():
    ctx, rec, kids = thirds(100, 125)
    ctx.snapping = False
    assert ctx.grid is None
    ctx.frame()
    assert spot(kids[1]) == pytest.approx((33.333, 0.0, 33.333, 10.0), abs=1e-3)
    # the border rect of the first child is the third op of the frame, after the
    # clear and the root's own two plates
    assert rec.rects()[2] == pytest.approx(kids[0].screen_box, abs=1e-3)


def test_a_one_x_frame_rounds_every_edge_to_a_whole_pixel():
    """One device pixel is one logical pixel, so a third of a 100 pixel row is 33
    and not 33.3333: the plan's rounding is the screen's, and a 1x screen has no
    subpixels to be faithful to. The row still ends where the window does."""
    ctx, rec, kids = thirds(100, 100)
    assert ctx.dpr == 1.0
    ctx.frame()
    assert spot(kids[0]) == (0.0, 0.0, 33.0, 10.0)
    assert spot(kids[1]) == (33.0, 0.0, 34.0, 10.0)
    first, second, third = kids
    assert spot(third)[0] + spot(third)[2] == 100.0
    assert spot(first)[0] + spot(first)[2] == spot(second)[0]


def test_the_report_carries_the_ratio_the_frame_was_measured_at():
    ctx, rec, _kids = thirds(100, 125)
    report = ctx.frame()
    assert report.dpr == 1.25
    assert report.as_dict()["dpr"] == 1.25
    assert ctx.stats()["dpr"] == 1.25


# --------------------------------------------------------------------------
# the pointer and the keyboard, aimed at this tree
# --------------------------------------------------------------------------


def test_the_router_s_hit_test_is_this_tree_s_own():
    ctx, rec = stage()
    one = labelled(ctx, "one")
    ctx.layout()
    assert ctx.router.hit_test.__self__ is ctx
    assert ctx.router.target_at(10, 10) is one is ctx.hit(10, 10)
    assert ctx.hit(190, 90) is ctx.root, "the root claims what no control does"


def test_the_events_a_frame_was_given_are_answered_before_it_draws():
    """The plan's tick order, in one assertion: a hover has to be in the picture
    the same frame asked for it, which is why ``dispatch`` is not inside ``begin``."""
    ctx, rec = stage()
    one = labelled(ctx, "one")
    heard(one)
    ctx.on_cursor(10, 10)
    report = ctx.frame()
    assert report.events == 1
    assert one.hovered is True
    assert rec.last("rect").get("color") == ctx.skin.resolve("button", "hover").fill


def test_a_press_and_a_release_on_one_widget_are_a_click_on_it():
    ctx, rec = stage()
    one = labelled(ctx, "one")
    said = heard(one)
    ctx.router.move_to(10, 10)
    ctx.frame()
    ctx.on_button(MOUSE_BUTTONS["left"], ACTIONS["press"], 0)
    ctx.on_button(MOUSE_BUTTONS["left"], ACTIONS["release"], 0)
    ctx.frame()
    assert [name for _who, name, _args in said] == [
        "on_enter", "on_move", "on_focus", "on_press", "on_release", "on_click"]
    assert ctx.focus is one and ctx.hover is one


def test_a_press_on_the_root_takes_the_keyboard_off_what_had_it():
    ctx, rec = stage()
    one = labelled(ctx, "one")
    ctx.grab_focus(one)
    ctx.frame()
    ctx.router.click(190, 90)
    ctx.frame()
    assert ctx.focus is None and one.focused is False
    assert ctx.router.focus is None, "the two layers were re-aimed at each other"


def test_a_drag_started_on_a_widget_keeps_it_after_the_pointer_leaves():
    ctx, rec = stage()
    one = labelled(ctx, "one")
    said = heard(one)
    ctx.router.move_to(10, 10)
    ctx.router.press()
    ctx.router.move_to(190, 90)
    assert ctx.dragging is one
    assert ("one", "on_drag", (180.0, 80.0, 0)) in said, "the movement, not the position"
    assert said[-1][1] == "on_leave", "and it left as the pointer came off it"
    ctx.router.release()
    assert "on_click" not in [name for _who, name, _args in said]


def test_tab_and_shift_tab_move_the_keyboard_through_the_tree():
    ctx, rec = stage()
    one = labelled(ctx, "one", (4, 4, 60, 20))
    two = labelled(ctx, "two", (4, 32, 60, 20))
    ctx.frame()
    ctx.grab_focus(one)
    ctx.on_key(258, 0, ACTIONS["press"], 0)
    ctx.frame()
    assert ctx.focus is two
    ctx.on_key(258, 0, ACTIONS["press"], MODS["shift"])
    ctx.frame()
    assert ctx.focus is one


def test_a_press_after_a_move_in_the_same_frame_lands_where_the_cursor_is():
    """GLFW sends the move and the press in one poll, so a press carries no
    position of its own: it is answered against the pointer the frame ended with."""
    ctx, rec = stage()
    one = labelled(ctx, "one")
    said = heard(one)
    ctx.layout()
    ctx.on_cursor(10, 10)
    ctx.on_button(MOUSE_BUTTONS["left"], ACTIONS["press"], 0)
    ctx.frame()
    assert ctx.focus is one and ctx.hover is one
    assert ("one", "on_press", (10.0, 10.0, 0, 0)) in said


def test_a_widget_that_cannot_take_the_keyboard_is_never_landed_on():
    ctx, rec = stage()
    one = labelled(ctx, "one", (4, 4, 60, 20))
    caption = labelled(ctx, "caption", (4, 32, 60, 20), kind="text")
    two = labelled(ctx, "two", (4, 64, 60, 20))
    assert caption.focusable() is False
    ctx.frame()
    ctx.grab_focus(one)
    assert ctx.focus_next(1) is two, "skipped the caption"
    assert ctx.grab_focus(caption) is False


def test_focus_next_wraps_round_the_row_and_a_row_of_none_is_none():
    ctx, rec = stage()
    assert ctx.focus_next(1) is None
    one = labelled(ctx, "one", (4, 4, 60, 20))
    two = labelled(ctx, "two", (4, 32, 60, 20))
    ctx.frame()
    assert ctx.focus_next(1) is one
    assert ctx.focus_next(1) is two
    assert ctx.focus_next(1) is one, "and back to the start"
    assert ctx.focus_next(-1) is two


def test_a_script_s_grab_focus_is_where_the_keys_really_go():
    """The tree holds the keyboard and the router is aimed at it, not the other way
    round: a ``FOCUS(w)`` from a script moves the ring *and* the typing."""
    ctx, rec = stage()
    one = labelled(ctx, "one")
    two = labelled(ctx, "two", (4, 32, 60, 20))
    ctx.frame()
    ctx.grab_focus("two")
    assert ctx.router.focus is two
    # a router left pointing at the wrong widget is the tree's to overrule
    ctx.router.focus = one
    said = heard(two)
    ctx.on_char("7")
    ctx.frame()
    assert said == [("two", "on_char", ("7",))]
    assert ctx.router.focus is two


def test_a_key_with_no_hotkey_on_it_reaches_the_focused_widget():
    ctx, rec = stage()
    one = labelled(ctx, "one")
    said = heard(one)
    ctx.frame()
    ctx.grab_focus(one)
    ctx.on_key(256, 0, ACTIONS["press"], 0)
    ctx.frame()
    assert ("one", "on_key", ("escape", 0)) in said


def test_a_hotkey_the_script_bound_fires_instead_of_the_focused_widget():
    ctx, rec = stage()
    one = labelled(ctx, "one")
    said = heard(one)
    seen: List[str] = []
    ctx.router.hotkeys["control+q"] = lambda: seen.append("q")
    ctx.frame()
    ctx.grab_focus(one)
    ctx.on_key(ord("Q"), 0, ACTIONS["press"], MODS["control"])
    ctx.frame()
    assert seen == ["q"]
    assert "on_key" not in [name for _who, name, _args in said]


def test_the_wheel_reaches_the_widget_under_the_pointer_as_a_step():
    ctx, rec = stage()
    slide = Widget("slider", name="slide", parent=ctx.root)
    slide.place(4, 4, 120, 24)
    said = heard(slide)
    ctx.frame()
    ctx.router.move_to(10, 10)
    ctx.on_scroll(0.0, 1.0)
    ctx.frame()
    assert said[-1] == ("slide", "on_scroll", (0.0, 16.0, 0))


def test_ctrl_makes_the_wheel_eight_times_finer():
    ctx, rec = stage()
    slide = Widget("slider", name="slide", parent=ctx.root)
    slide.place(4, 4, 120, 24)
    said = heard(slide)
    ctx.frame()
    ctx.router.move_to(10, 10)
    ctx.router.scroll_at(1.0, mods=MODS["control"])
    assert said[-1] == ("slide", "on_scroll", (0.0, 2.0, MODS["control"]))


def test_a_layout_that_moved_a_widget_under_a_still_pointer_re_hovers_it():
    """A hover is a fact about where the pointer *is*: a button that grew into a
    cursor that never moved has to light up in the same frame it grew."""
    ctx, rec = stage()
    ctx.layout()
    ctx.router.move_to(150, 90)
    assert ctx.hover is ctx.root
    wide = labelled(ctx, "wide", (4, 4, 80, 24))
    said = heard(wide)
    pack(wide, "fill")
    ctx.layout()
    assert ctx.hover is wide
    assert said == [("wide", "on_enter", ())]


def test_removing_a_widget_takes_it_out_of_hover_focus_and_capture():
    ctx, rec = stage()
    one = labelled(ctx, "one")
    ctx.frame()
    ctx.router.move_to(10, 10)
    ctx.router.press()
    assert ctx.hover is one and ctx.focus is one and ctx.dragging is one
    ctx.remove("one")
    assert (ctx.hover, ctx.focus, ctx.dragging) == (None, None, None)
    assert ctx.widgets() == [ctx.root]
    assert ctx.frame().events == 0, "and the next frame has nothing to answer"


def test_clearing_the_window_lets_the_pointer_keep_nothing():
    ctx, rec = stage()
    one = labelled(ctx, "one")
    two = labelled(ctx, "two", (4, 32, 60, 20))
    ctx.frame()
    ctx.grab_focus(two)
    ctx.router.move_to(10, 10)
    assert ctx.hover is one
    assert [held.name for held in ctx.clear()] == ["one", "two"]
    assert (ctx.hover, ctx.focus, ctx.dragging) == (None, None, None)


def test_a_close_request_is_answered_and_the_answer_is_recorded():
    ctx, rec = stage()
    assert ctx.may_close is False, "nobody has asked yet"
    assert ctx.on_close_request() is None
    assert ctx.may_close is False, "the request is only recorded when dispatched"
    ctx.frame()
    assert ctx.may_close is True


class Refusing(Panel):
    """A root whose script said "not yet": a confirm dialog, a shutdown not done."""

    __slots__ = ()

    def on_close_request(self):
        return False


def refusing(width: float, height: float) -> UIContext:
    held = Refusing({"label": ""}, name="root")
    held.place(0, 0, width, height)
    return UIContext(RecordingRenderer(), root=held)


def test_a_root_that_refuses_a_close_is_not_recorded_as_allowed():
    ctx = refusing(200, 100)
    assert ctx.on_close_request() is False
    ctx.frame()
    assert ctx.may_close is False, "the dispatch remembered the refusal"


def test_files_dropped_on_the_window_reach_the_root_s_handler():
    ctx, rec = stage()
    said = heard(ctx.root)
    ctx.frame()
    rec.reset()
    ctx.on_drop(["a.pd", "b.wav"])
    ctx.frame()
    assert ("root", "on_drop", (("a.pd", "b.wav"),)) in said
    assert said[-1] == ("root", "on_drop", (("a.pd", "b.wav"),))


def test_a_context_answers_every_call_a_window_s_listener_makes():
    ctx, rec = stage()
    missing = [name for name in LISTENER_NAMES
               if not callable(getattr(ctx, name, None))]
    assert missing == []


def test_a_context_with_keys_off_leaves_the_tab_to_the_script():
    ctx, rec = stage(keys=False)
    one = labelled(ctx, "one")
    said = heard(one)
    ctx.frame()
    ctx.grab_focus(one)
    ctx.on_key(258, 0, ACTIONS["press"], 0)
    ctx.frame()
    assert said[-1] == ("one", "on_key", ("tab", 0))
    assert ctx.focus is one, "nothing moved the keyboard"


def test_release_focus_gives_the_keyboard_up_whatever_the_router_thinks():
    ctx, rec = stage()
    one = labelled(ctx, "one")
    ctx.frame()
    ctx.grab_focus(one)
    assert ctx.release_focus() is True
    assert ctx.focus is None and ctx.router.focus is None
    assert ctx.release_focus() is False


# --------------------------------------------------------------------------
# the same script with no window at all
# --------------------------------------------------------------------------


def front(ctx: UIContext) -> Dict[str, Widget]:
    """The plan's metronome front panel, built the same way in either mode."""
    made: Dict[str, Widget] = {}
    made["led"] = Widget("toggle", {"label": ""}, name="led", parent=ctx.root)
    made["led"].place(12, 12, 20, 20)
    made["bpm"] = Widget("slider", {"label": ""}, name="bpm", parent=ctx.root)
    made["bpm"].place(40, 12, 120, 20)
    pack(made["bpm"], "grow", "fixed")
    made["bpm"].place(40, 12, 120, 20)
    made["run"] = Widget("button", {"label": "Run"}, name="run", parent=ctx.root)
    made["run"].place(40, 40, 80, 24)
    return made


def screen_of(made: Dict[str, Widget]) -> Dict[str, tuple]:
    return {name: spot(held) for name, held in made.items()}


def test_a_headless_frame_lays_the_tree_out_and_records_nothing():
    ctx = UIContext(painting=False, width=200, height=100)
    made = front(ctx)
    report = ctx.frame()
    assert report.ops == 0
    assert isinstance(ctx.renderer, NullRenderer)
    assert spot(made["bpm"]) == (40.0, 12.0, 120.0, 20.0)
    assert report.frames == 1


def test_the_same_script_moves_the_same_boxes_with_and_without_a_window():
    """The plan's parity line: a script never has to ask whether it has a window -
    the events, the geometry and the handlers are the same, and only the ops are not."""
    lit, rec = stage(200, 100)
    dark = UIContext(painting=False, width=200, height=100)
    here, there = front(lit), front(dark)
    said, heard_there = heard(here["run"]), heard(there["run"])
    for pair in (lit, dark):
        pair.frame()
        pair.router.click(60, 50)
        pair.set_size(300, 100)
        pair.frame()
    assert screen_of(here) == screen_of(there)
    assert [entry[1] for entry in said] == [entry[1] for entry in heard_there]
    assert rec.ops, "the windowed one drew the picture the other only computed"


def test_a_led_blinks_invisibly():
    """The plan's own phrase, and the reason state lives in the tree: a toggle with
    no renderer to draw it still changes state, still changes its paint key, and
    still puts no ops anywhere - so the same script runs either way."""
    lit, rec = stage(200, 100)
    dark = UIContext(painting=False, width=200, height=100)
    here = front(lit)["led"]
    there = front(dark)["led"]
    for pair, held in ((lit, here), (dark, there)):
        pair.frame()
        assert held.state_name() == "normal"
        held.set("value", 1)
        report = pair.frame()
        assert held.state_name() == "on", held.path()
        assert (report.ops == 0) is (pair is dark)
    assert lit.ops > 0 and dark.ops == 0


def test_a_headless_context_still_begins_and_ends_each_frame_on_its_renderer():
    ctx = UIContext(painting=False, width=200, height=100)
    assert ctx.frame().ops == 0
    assert ctx.stats()["frames"] == 1
    assert ctx.renderer.frame_count == 1, "NullRenderer counts them too"


# --------------------------------------------------------------------------
# the window's side: the four calls and the listener
# --------------------------------------------------------------------------


class FakeWindow:
    """The four calls :func:`~crude.ui.context.window_context` makes of a window."""

    def __init__(self, size=(320, 200), fb=(640, 400), listener=True):
        self._size, self._fb = size, fb
        self.swaps = 0
        self.listener = None if listener else False

    def size(self):
        return self._size

    def framebuffer_size(self):
        return self._fb

    def swap(self):
        self.swaps += 1

    def set_input_listener(self, held):
        self.listener = held


class BareWindow:
    """A window that is going away: it answers for neither its size nor a listener."""

    def __init__(self, size=(0, 0)):
        self._size = size

    def size(self):
        return self._size

    def framebuffer_size(self):
        return (0, 0)


def test_a_window_s_own_sizes_are_the_context_s():
    win = FakeWindow()
    ctx = window_context(win, RecordingRenderer())
    assert (ctx.width, ctx.height) == (320.0, 200.0)
    assert (ctx.fb_width, ctx.fb_height) == (640, 400)
    assert ctx.dpr == 2.0
    assert ctx.swap == win.swap
    assert win.listener is ctx, "the wiring is the only reason either knows one"


def test_a_frame_from_a_window_context_swaps_that_window():
    win = FakeWindow()
    ctx = window_context(win, RecordingRenderer())
    labelled(ctx, "one")
    assert ctx.frame().swapped is True
    assert win.swaps == 1
    ctx.frame(swap=False)
    assert win.swaps == 1, "held back for one call"
    ctx.frame()
    assert win.swaps == 2


def test_a_window_that_cannot_answer_for_its_size_gets_the_default_rect():
    """A window whose handle is gone answers ``(0, 0)``, and a zero-sized client
    rect is a divide by zero in waiting: the default is the honest answer."""
    for size in ((0, 0), None):
        ctx = window_context(FakeWindow(size=size))
        assert (ctx.width, ctx.height) == DEFAULT_UI_SIZE


def test_a_window_with_no_room_for_a_listener_still_gets_a_context():
    ctx = window_context(BareWindow())
    assert (ctx.width, ctx.height) == DEFAULT_UI_SIZE
    assert ctx.swap is None
    assert ctx.frame().swapped is False


def test_a_real_window_call_reaches_the_tree_through_the_context():
    win = FakeWindow()
    ctx = window_context(win, RecordingRenderer())
    one = labelled(ctx, "one")
    ctx.layout()
    win.listener.on_cursor(10, 10)
    win.listener.on_button(MOUSE_BUTTONS["left"], ACTIONS["press"], 0)
    win.listener.on_button(MOUSE_BUTTONS["left"], ACTIONS["release"], 0)
    win.listener.on_key(256, 0, ACTIONS["press"], 0)
    win.listener.on_char("4")
    win.listener.on_scroll(0.0, 1.0)
    win.listener.on_close_request()
    ctx.frame()
    assert ctx.focus is one
    names = ctx.router.names()
    for word in ("enter", "focus", "press", "release", "click", "key", "text",
                 "scroll", "close"):
        assert word in names, word
    assert ctx.may_close is True


# --------------------------------------------------------------------------
# describing the frame
# --------------------------------------------------------------------------


def test_the_stats_describe_the_frame_a_report_would_print():
    ctx, rec = stage()
    one = labelled(ctx, "one")
    ctx.frame()
    ctx.on_cursor(10, 10)
    ctx.on_button(MOUSE_BUTTONS["left"], ACTIONS["press"], 0)
    ctx.on_button(MOUSE_BUTTONS["left"], ACTIONS["release"], 0)
    ctx.frame()
    said = ctx.stats()
    assert said["size"] == (200.0, 100.0)
    assert said["framebuffer"] == (200, 100)
    assert said["widgets"] == 2 and said["frames"] == 2
    assert said["begun"] is False
    assert said["hover"] == said["focus"] == "root/one"
    assert said["dragging"] is None and said["events"] == 3
    assert one.path() == "root/one"
    assert said["cursor"] == (10.0, 10.0)


def test_str_says_the_size_the_grid_and_the_widget_count():
    ctx, rec = stage(320, 200, fb_width=640, fb_height=400)
    assert str(ctx) == "UIContext(320x200 @ 2 dpr, 1 widget(s))"
    labelled(ctx, "one")
    ctx.frame()
    assert f"{ctx.root.count()} widget(s), {ctx.frames} frame(s), " in str(ctx)
    assert f"{ctx.ops} op(s)" in str(ctx)


def test_describe_hands_back_the_placement_policy_a_script_named():
    ctx, rec = stage()
    one = labelled(ctx, "one")
    pack(one, "fill")
    assert ctx.describe("one") == {"pack": ["fill", "fill"]}
    with pytest.raises(UIContextError) as raised:
        ctx.describe("nothing")
    assert "nothing here is named that" in str(raised.value)


def test_find_reaches_a_widget_by_name_from_either_layer():
    ctx, rec = stage()
    one = labelled(ctx, "one")
    assert ctx.find("one") is one is ctx.root.find("one")
    assert ctx.find("ONE") is one, "names are what a script typed"
    assert ctx.find("nope") is None
