"""What the person is doing, worked out from a queue of numbers, with no window.

Everything here is a fake: the hit test is a rect lookup, the widgets are
recorders, and the events are pushed in through the same ``on_cursor`` and
``on_button`` methods GLFW would call. That is the point. The router is the piece
of the UI that decides *who* hears *what*, and a decision like "a drag belongs to
the widget the pointer started on, not the one it happens to be over" is fully
testable without a GPU - and would be almost impossible to assert against a real
cursor, because it is about the calls a widget never receives as much as the ones
it does.

So the tests below are mostly about order and about silence: which handler ran
first, which one ran instead of another, and which ones did not run at all.
"""

from __future__ import annotations

import pytest

from crude.ui.input import (ACTIONS, BUTTON_NAMES, DRAG_FINE, KEY_NAMES, MODS,
                            MOUSE_BUTTONS, SCROLL_NOTCH, InputEvent, InputRouter,
                            button_name, fine_delta, is_fine, key_name)


class Widget:
    """Everything a widget can be asked for, recorded in the order it was asked."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = []

    def _said(self, what: str, *args) -> None:
        self.calls.append((what,) + args)

    def on_enter(self) -> None:
        self._said("enter")

    def on_leave(self) -> None:
        self._said("leave")

    def on_move(self, x, y) -> None:
        self._said("move", x, y)

    def on_press(self, x, y, button, mods) -> None:
        self._said("press", x, y, button, mods)

    def on_drag(self, dx, dy, mods) -> None:
        self._said("drag", dx, dy, mods)

    def on_release(self, x, y, button, mods) -> None:
        self._said("release", x, y, button, mods)

    def on_click(self, x, y, button) -> None:
        self._said("click", x, y, button)

    def on_scroll(self, dx, dy, mods) -> None:
        self._said("scroll", dx, dy, mods)

    def on_key(self, name, mods) -> None:
        self._said("key", name, mods)

    def on_char(self, text) -> None:
        self._said("char", text)

    def on_focus(self) -> None:
        self._said("focus")

    def on_blur(self) -> None:
        self._said("blur")

    def of(self, what: str):
        return [call for call in self.calls if call[0] == what]

    @property
    def heard(self):
        return [call[0] for call in self.calls]

    def __repr__(self) -> str:
        return f"Widget({self.name})"


class NoWheel(Widget):
    """A knob that can be dragged but has no wheel answer of its own."""

    on_scroll = None


class Silent:
    """A label: something that is *there* and is asked for nothing."""


class Stage:
    """Boxes in a window, so a hit test is a lookup rather than a guess."""

    def __init__(self, width: float = 200.0, height: float = 100.0) -> None:
        self.width = width
        self.height = height
        self.boxes = []

    def place(self, widget, x, y, w, h):
        self.boxes.append(((x, y, w, h), widget))
        return widget

    def hit(self, x, y):
        for (left, top, w, h), widget in reversed(self.boxes):
            if left <= x < left + w and top <= y < top + h:
                return widget
        return None

    def router(self, **kwargs) -> InputRouter:
        return InputRouter(self.hit, width=self.width, height=self.height,
                           **kwargs)


@pytest.fixture
def stage() -> Stage:
    return Stage()


@pytest.fixture
def knob(stage) -> Widget:
    return stage.place(Widget("knob"), 10, 10, 40, 40)


@pytest.fixture
def pad(stage) -> Widget:
    return stage.place(Widget("pad"), 100, 50, 40, 40)


@pytest.fixture
def router(stage) -> InputRouter:
    return stage.router()


# -- callbacks queue, dispatch decides -------------------------------------

def test_a_callback_queues_and_tells_no_one(router, knob):
    router.on_cursor(20, 20)
    assert knob.calls == []
    assert router.hover is None
    assert len(router.queue) == 1


def test_dispatch_answers_everything_queued_since_last_time(router, knob):
    router.on_cursor(20, 20)
    router.on_button(MOUSE_BUTTONS["left"], ACTIONS["press"], 0)
    assert router.dispatch() == 2
    assert router.queue == []
    assert router.events_seen == 2
    assert router.dispatched == 2


def test_an_empty_frame_dispatches_nothing(router):
    assert router.dispatch() == 0
    assert router.names() == []


def test_a_run_of_moves_is_answered_as_the_last_one(router, knob):
    # a 1000 Hz mouse at 60 fps is sixteen positions a frame, and one hover test
    for x in (12.0, 20.0, 33.0):
        router.on_cursor(x, 20)
    assert router.dispatch() == 1
    assert knob.of("move") == [("move", 33.0, 20.0)]


def test_a_run_of_moves_still_moves_the_whole_distance(router, knob):
    router.move_to(20, 20)
    router.press()
    knob.calls = []
    for x in (25.0, 30.0, 40.0):
        router.on_cursor(x, 20)
    router.dispatch()
    # the widget is told the total from where the pointer was, not the last notch
    assert knob.of("drag") == [("drag", 20.0, 0.0, 0)]


# -- hover ------------------------------------------------------------------

def test_moving_onto_a_widget_enters_it(router, knob):
    router.move_to(20, 20)
    assert knob.calls[0] == ("enter",)
    assert router.hover is knob
    assert router.names() == ["enter", "move"]


def test_moving_off_again_leaves_it(router, knob):
    router.move_to(20, 20)
    router.move_to(190, 95)
    assert knob.of("leave") == [("leave",)]
    assert router.hover is None


def test_a_pointer_outside_every_box_makes_no_event(router, knob):
    router.move_to(190, 95)
    router.move_to(195, 98)
    assert router.names() == []


def test_a_widget_that_answers_nothing_still_takes_a_hover(stage):
    label = stage.place(Silent(), 10, 10, 20, 20)
    router = stage.router()
    router.move_to(15, 15)
    assert router.hover is label
    assert router.names() == ["enter", "move"]


def test_moving_within_one_widget_is_a_move_not_an_enter(router, knob):
    router.move_to(20, 20)
    router.move_to(30, 30)
    assert len(knob.of("enter")) == 1
    assert len(knob.of("move")) == 2


def test_the_hover_follows_the_last_position_of_the_frame(router, knob, pad):
    router.on_cursor(20, 20)
    router.on_cursor(110, 60)
    router.dispatch()
    assert knob.calls == []
    assert pad.of("enter") == [("enter",)]


def test_only_the_topmost_box_at_a_point_is_hit(stage):
    back = stage.place(Widget("back"), 0, 0, 100, 100)
    front = stage.place(Widget("front"), 10, 10, 20, 20)
    router = stage.router()
    assert router.target_at(15, 15) is front
    assert router.target_at(50, 50) is back
    assert router.target_at(150, 50) is None


# -- press, focus, capture ---------------------------------------------------

def test_a_press_focuses_and_is_reported_to_the_widget(router, knob):
    router.move_to(20, 20)
    router.press()
    assert knob.of("focus") == [("focus",)]
    assert knob.of("press") == [("press", 20.0, 20.0, 0, 0)]
    assert router.focus is knob
    assert router.capture is knob


def test_a_press_on_another_widget_blurs_the_first(router, knob, pad):
    router.click(20, 20)
    router.click(110, 60)
    assert knob.of("blur") == [("blur",)]
    assert pad.of("focus") == [("focus",)]
    assert router.focus is pad


def test_a_press_on_empty_space_takes_the_focus_away(router, knob):
    router.click(20, 20)
    router.click(190, 95)
    assert knob.of("blur") == [("blur",)]
    assert router.focus is None


def test_focusing_the_same_widget_twice_says_it_once(router, knob):
    router.click(20, 20)
    router.click(25, 25)
    assert len(knob.of("focus")) == 1
    assert knob.of("blur") == []


def test_a_press_keeps_the_drag_after_the_pointer_leaves(router, knob, pad):
    router.move_to(20, 20)
    router.press()
    router.move_to(120, 70)
    assert knob.of("drag") == [("drag", 100.0, 50.0, 0)]
    assert pad.of("drag") == []


def test_a_drag_is_told_the_movement_rather_than_the_position(router, knob):
    router.move_to(20, 20)
    router.press()
    knob.calls = []
    router.move_to(25, 22)
    assert knob.of("drag") == [("drag", 5.0, 2.0, 0)]


def test_ctrl_makes_a_drag_fine(router, knob):
    router.move_to(20, 20)
    router.on_button(MOUSE_BUTTONS["left"], ACTIONS["press"], MODS["control"])
    router.dispatch()
    router.move_to(36, 20)
    assert knob.of("drag") == [("drag", 16.0 / DRAG_FINE, 0.0, MODS["control"])]


def test_a_release_over_the_same_widget_is_a_click(router, knob):
    router.click(20, 20)
    assert knob.of("click") == [("click", 20.0, 20.0, 0)]
    assert router.capture is None


def test_a_release_somewhere_else_ends_the_drag_without_a_click(router, knob):
    router.move_to(20, 20)
    router.press()
    router.move_to(190, 95)
    router.release()
    assert knob.of("release") == [("release", 190.0, 95.0, 0, 0)]
    assert knob.of("click") == []


def test_a_release_ends_the_drag_and_updates_the_hover(router, knob, pad):
    router.move_to(20, 20)
    router.press()
    router.move_to(110, 60)
    router.release()
    assert router.hover is pad
    assert knob.of("leave") == [("leave",)]
    assert pad.of("enter") == [("enter",)]


def test_a_stray_release_is_not_a_click(router, knob):
    router.move_to(20, 20)
    router.release()
    assert knob.of("release") == []
    assert knob.of("click") == []
    assert router.capture is None


def test_a_right_click_is_still_a_click(router, knob):
    router.click(20, 20, MOUSE_BUTTONS["right"])
    assert knob.of("press") == [("press", 20.0, 20.0, 1, 0)]
    assert knob.of("click") == [("click", 20.0, 20.0, 1)]


def test_a_release_ends_the_drag_whichever_button_it_came_on(router, knob):
    router.move_to(20, 20)
    router.press(MOUSE_BUTTONS["left"])
    router.press(MOUSE_BUTTONS["middle"])
    router.release(MOUSE_BUTTONS["left"])
    assert router.capture is None
    assert knob.of("release") == [("release", 20.0, 20.0, 0, 0)]
    # the other button coming up on its own is a stray, not a second click
    knob.calls = []
    router.release(MOUSE_BUTTONS["middle"])
    assert knob.calls == []
    assert router.buttons == set()


def test_a_click_helper_is_the_whole_gesture(router, knob):
    assert router.click(20, 20) is knob
    assert knob.heard == ["enter", "move", "focus", "press", "release", "click"]


# -- the wheel ---------------------------------------------------------------

def test_a_wheel_is_a_fixed_number_of_pixels(router, knob):
    router.move_to(20, 20)
    router.scroll_at(1.0)
    assert knob.of("scroll") == [("scroll", 0.0, SCROLL_NOTCH, 0)]


def test_a_wheel_without_a_wheel_handler_is_a_coarse_drag(router):
    quiet = NoWheel("slider")
    box = (60, 10, 30, 30)
    stage = Stage()
    stage.boxes.append((box, quiet))
    router = stage.router()
    router.move_to(70, 20)
    router.scroll_at(-2.0)
    assert quiet.of("scroll") == []
    assert quiet.of("drag") == [("drag", 0.0, -2.0 * SCROLL_NOTCH, 0)]
    assert router.names() == ["enter", "move", "scroll-as-drag"]


def test_ctrl_makes_the_wheel_fine_too(router, knob):
    router.move_to(20, 20)
    router.scroll_at(1.0, mods=MODS["control"])
    assert knob.of("scroll") == [
        ("scroll", 0.0, SCROLL_NOTCH / DRAG_FINE, MODS["control"])]


def test_the_wheel_goes_under_the_pointer_rather_than_the_focus(router, knob, pad):
    router.click(20, 20)
    router.move_to(110, 60)
    router.scroll_at(1.0)
    assert knob.of("scroll") == []
    assert pad.of("scroll") == [("scroll", 0.0, SCROLL_NOTCH, 0)]


def test_a_wheel_over_nothing_is_answered_by_no_one(router, knob):
    router.click(20, 20)
    router.move_to(190, 95)
    router.scroll_at(1.0)
    assert knob.of("scroll") == []
    assert knob.of("drag") == []
    assert router.names()[-1] == "leave"


# -- the modifier word that outlives its event --------------------------------

def test_the_router_holds_which_modifiers_are_down_right_now(router, knob):
    """``mods`` is live state, not an argument that happens to be in hand.

    :func:`crude.bindings.ui.mod_held` reads this, and it is read *after* the
    dispatch that set it: a click handler runs inside the dispatch and can use the
    word it was handed, but a frame handler - and a script that asks which key the
    person is holding while they drag - has to ask the router what it still knows.
    So the word survives being answered, and a bare pointer move must not invent one.
    """
    assert router.mods == 0, "nothing was ever pressed"
    router.on_button(MOUSE_BUTTONS["left"], ACTIONS["press"], MODS["shift"])
    router.dispatch()
    assert router.mods == MODS["shift"]
    router.on_cursor(30, 30)
    router.dispatch()
    assert router.mods == MODS["shift"], "the cursor carries no keys"
    # a frame boundary is not the person letting go: reset forgets the frame
    router.reset()
    assert router.mods == MODS["shift"]
    # and GLFW reports the state *after* the key came up, which is the whole answer
    shift = next(code for code, word in KEY_NAMES.items() if word == "shift")
    router.on_key(shift, 0, ACTIONS["release"], 0)
    router.dispatch()
    assert router.mods == 0


def test_two_keys_down_are_two_bits_in_the_one_word(router, knob):
    """A word is read a bit at a time, so shift-click can also be a fine drag.

    The router only ever stores the combined number; every reader on the widget side
    tests one bit of it. Pinning that here is what keeps ``MOD_HELD("shift")`` from
    being written as ``mods == 1`` by the next person to need it.
    """
    both = MODS["shift"] | MODS["control"]
    router.move_to(20, 20)
    router.on_button(MOUSE_BUTTONS["left"], ACTIONS["press"], both)
    router.dispatch()
    assert router.mods == both
    assert router.mods & MODS["shift"] and router.mods & MODS["control"]
    assert not router.mods & MODS["alt"], "a word answers for keys nobody pressed"
    # the fine delta reads its own bit and leaves the other one for the script
    knob.calls = []
    router.move_to(36, 20)
    assert knob.of("drag") == [("drag", 16.0 / DRAG_FINE, 0.0, both)]


def test_only_the_pointer_knows_where_it_is_and_the_gestures_do_not(router, knob):
    """A move carries a position; a drag and a wheel carry a movement and a word.

    That asymmetry is GLFW's, not this router's, and a widget that wants to act on
    *what the pointer is over* - a clip zooming about the sample under the cursor -
    has to have remembered the place it was told, because nothing will tell it again.
    """
    router.move_to(20, 20)
    router.press()
    knob.calls = []
    router.move_to(30, 25)
    router.scroll_at(1.0)
    assert knob.of("drag") == [("drag", 10.0, 5.0, 0)]
    assert knob.of("scroll") == [("scroll", 0.0, SCROLL_NOTCH, 0)]
    # the place the pointer reached is in the move and nowhere else: 30, 25 is the
    # cursor, and neither gesture's numbers are the cursor
    assert knob.of("move") == [("move", 30.0, 25.0)]
    for call in knob.of("drag") + knob.of("scroll"):
        assert 30.0 not in call[1:3] and 25.0 not in call[1:3], call
    router.release()
    assert knob.of("release") == [("release", 30.0, 25.0, 0, 0)]


# -- keys and characters ------------------------------------------------------

def test_a_key_goes_to_what_has_focus(router, knob):
    router.click(20, 20)
    router.key_typed("up")
    assert knob.of("key") == [("key", "up", 0)]


def test_a_hotkey_is_asked_before_the_focused_widget(router, knob):
    heard = []
    router.hotkeys["up"] = lambda: heard.append("up")
    router.click(20, 20)
    router.key_typed("up")
    assert heard == ["up"]
    assert knob.of("key") == []
    assert router.names()[-1] == "hotkey"


def test_a_hotkey_can_insist_on_its_modifiers(router, knob):
    heard = []
    router.hotkeys["control+s"] = lambda: heard.append("save")
    router.click(20, 20)
    router.on_key(ord("S"), 0, ACTIONS["press"], MODS["control"])
    router.dispatch()
    assert heard == ["save"]
    assert knob.of("key") == []


def test_a_bare_hotkey_does_not_answer_a_modified_key(router):
    heard = []
    router.hotkeys["s"] = lambda: heard.append("s")
    router.on_key(ord("S"), 0, ACTIONS["press"], MODS["control"])
    router.dispatch()
    assert heard == []


def test_a_released_key_is_not_a_second_event(router, knob):
    router.click(20, 20)
    knob.calls = []
    router.on_key(ord("A"), 0, ACTIONS["release"], 0)
    assert router.dispatch() == 1
    assert knob.calls == []


def test_a_repeated_key_is_a_key_like_any_other(router, knob):
    router.click(20, 20)
    knob.calls = []
    router.on_key(259, 0, ACTIONS["repeat"], 0)      # GLFW's KEY_BACKSPACE
    router.dispatch()
    assert knob.of("key") == [("key", "backspace", 0)]


def test_characters_reach_the_focus_in_order(router, knob):
    router.click(20, 20)
    router.type_text("hi")
    assert knob.of("char") == [("char", "h"), ("char", "i")]


def test_a_character_is_not_a_hotkey(router, knob):
    heard = []
    router.hotkeys["s"] = lambda: heard.append("s")
    router.click(20, 20)
    router.type_text("s")
    assert heard == []
    assert knob.of("char") == [("char", "s")]


def test_typing_into_nothing_happens_to_nothing(router, knob):
    router.type_text("abc")
    router.key_typed("f5")
    assert knob.calls == []
    assert router.names() == []


def test_a_widget_that_hears_no_keys_can_still_be_focused(stage):
    label = stage.place(Silent(), 10, 10, 20, 20)
    router = stage.router()
    router.click(15, 15)
    router.type_text("q")
    router.key_typed("enter")
    assert router.focus is label


def test_an_unknown_key_name_is_refused(router):
    with pytest.raises(ValueError):
        router.key_typed("bogus")


def test_the_words_handlers_use_are_the_same_coming_and_going(router, knob):
    router.click(20, 20)
    router.key_typed("f5")
    router.key_typed("delete")
    router.key_typed("k")
    assert knob.of("key") == [("key", "f5", 0), ("key", "delete", 0),
                              ("key", "k", 0)]


# -- the window's own news ----------------------------------------------------

def test_a_resize_reaches_the_root(stage):
    class Root:
        def __init__(self):
            self.size = None

        def on_resize(self, width, height):
            self.size = (width, height)

    root = Root()
    router = stage.router(root=root)
    router.on_resize(320, 200)
    router.dispatch()
    assert root.size == (320.0, 200.0)
    assert (router.width, router.height) == (320.0, 200.0)
    assert router.names() == ["resize"]


def test_a_resize_with_no_root_still_sizes_the_window(router, knob):
    router.on_resize(320, 200)
    router.dispatch()
    assert (router.width, router.height) == (320.0, 200.0)
    assert router.names() == []


def test_the_framebuffer_size_is_the_ratio(stage):
    router = stage.router()
    router.on_framebuffer_resize(400, 200)
    router.dispatch()
    assert router.dpr == 2.0


def test_a_ratio_needs_a_window_to_measure_against():
    router = InputRouter()
    router.on_framebuffer_resize(400, 200)
    router.dispatch()
    assert router.dpr == 1.0


def test_a_resize_keeps_the_ratio_it_was_measured_at(stage):
    router = stage.router()
    router.on_framebuffer_resize(400, 200)
    router.on_resize(400, 200)
    router.dispatch()
    assert router.dpr == 2.0
    assert (router.width, router.height) == (400.0, 200.0)


def test_a_drop_lands_on_the_root(stage):
    class Root:
        def __init__(self):
            self.paths = None

        def on_drop(self, paths):
            self.paths = paths

    root = Root()
    router = stage.router(root=root)
    router.on_drop(["a.pd", "b.wav"])
    router.dispatch()
    assert root.paths == ("a.pd", "b.wav")
    assert router.names() == ["drop"]


def test_a_drop_lands_on_the_widget_under_the_pointer_as_well(stage):
    """``ON_DROP(w, fn)`` is a real door: the file goes to what it landed on.

    The drop callback carries no position, so the answer is where the pointer
    was last measured - and the window is asked too, because a project that
    opens anything dropped anywhere has no widget to thank for it.
    """
    class Clip(Widget):
        def on_drop(self, paths):
            self._said("drop", paths)

    class Root:
        def __init__(self):
            self.paths = None

        def on_drop(self, paths):
            self.paths = paths

    root = Root()
    held = stage.place(Clip("clip"), 100, 50, 40, 40)
    router = stage.router(root=root)
    router.move_to(110, 60)
    router.on_drop(["take.wav"])
    router.dispatch()
    assert held.of("drop") == [("drop", ("take.wav",))]
    assert root.paths == ("take.wav",)
    # the widget is asked first, so a log reads in the order the answers came
    assert router.names()[-2:] == ["drop", "drop"]
    assert router.log[-2][2] is held and router.log[-1][2] is root


def test_a_drop_on_the_window_s_own_box_is_answered_once(stage):
    """A pointer over the root is not two targets: the window hears one drop."""
    class Root(Widget):
        def on_drop(self, paths):
            self._said("drop", paths)

    root = Root("root")
    stage.place(root, 0, 0, 200, 100)
    router = stage.router(root=root)
    router.move_to(110, 60)
    router.on_drop(["take.wav"])
    router.dispatch()
    assert root.of("drop") == [("drop", ("take.wav",))]
    assert router.names().count("drop") == 1


def test_a_close_request_is_only_answered_once_it_is_dispatched(router):
    assert router.on_close_request() is None
    assert router.close_allowed is False
    router.dispatch()
    assert router.close_allowed is True
    assert router.names() == ["close"]


def test_the_root_can_keep_its_window_open(stage):
    class Root:
        def on_close_request(self):
            return False

    router = stage.router(root=Root())
    assert router.on_close_request() is False
    assert len(router.queue) == 1


def test_a_root_that_answers_anything_else_is_let_go(stage):
    class Root:
        def on_close_request(self):
            return None

    router = stage.router(root=Root())
    assert router.on_close_request() is None


# -- the tables the plan says are GLFW's ---------------------------------------

def test_the_action_numbers_are_glfw_s():
    assert ACTIONS == {"release": 0, "press": 1, "repeat": 2}


def test_the_modifier_bits_are_glfw_s():
    assert MODS == {"shift": 1, "control": 2, "alt": 4, "super": 8}


def test_the_mouse_buttons_are_glfw_s():
    assert MOUSE_BUTTONS == {"left": 0, "right": 1, "middle": 2}
    assert [BUTTON_NAMES[i] for i in (0, 1, 2)] == ["left", "right", "middle"]


def test_a_key_number_becomes_the_word_a_handler_says():
    assert key_name(256) == "escape"
    assert key_name(257) == "enter"
    assert key_name(258) == "tab"
    assert key_name(259) == "backspace"
    assert key_name(261) == "delete"
    assert key_name(263) == "left"
    assert key_name(290) == "f1"
    assert key_name(301) == "f12"
    assert key_name(320) == "keypad 0"
    assert key_name(325) == "keypad 5"
    assert key_name(335) == "keypad enter"
    assert key_name(ord("A")) == "a"
    assert key_name(99999) == "key 99999"


def test_a_button_the_table_does_not_know_names_itself():
    assert button_name(17) == "button 17"


def test_the_names_are_the_modifiers_coming_and_going(router, knob):
    assert InputRouter.combo("s", MODS["shift"] | MODS["control"]) == \
        "control+shift+s"
    assert InputRouter.combo("f5", 0) == "f5"
    assert InputRouter.combo("tab", MODS["alt"] | MODS["super"]) == "alt+super+tab"


def test_fine_and_coarse_agree_with_the_router():
    assert is_fine(MODS["control"]) is True
    assert is_fine(MODS["alt"]) is False
    assert fine_delta(8, 4, MODS["control"]) == (1.0, 0.5)
    assert fine_delta(8, 4, 0) == (8.0, 4.0)
    assert DRAG_FINE == 8.0


#: every name :data:`crude.ui.input.KEY_NAMES` claims, spelled as the GLFW constant
#: it says it is. Only the marked test below can check it, and that is the point:
#: a table of other people's numbers needs a copy of theirs to be measured against.
GLFW_KEY_ATTRS = {
    "KEY_ESCAPE": "escape", "KEY_ENTER": "enter", "KEY_TAB": "tab",
    "KEY_BACKSPACE": "backspace", "KEY_INSERT": "insert",
    "KEY_DELETE": "delete", "KEY_RIGHT": "right", "KEY_LEFT": "left",
    "KEY_DOWN": "down", "KEY_UP": "up", "KEY_PAGE_UP": "page up",
    "KEY_PAGE_DOWN": "page down", "KEY_HOME": "home", "KEY_END": "end",
    "KEY_CAPS_LOCK": "caps lock", "KEY_SCROLL_LOCK": "scroll lock",
    "KEY_NUM_LOCK": "num lock", "KEY_PRINT_SCREEN": "print screen",
    "KEY_PAUSE": "pause", "KEY_KP_ENTER": "keypad enter",
}
for _index in range(1, 13):
    GLFW_KEY_ATTRS[f"KEY_F{_index}"] = f"f{_index}"
for _index in range(10):
    GLFW_KEY_ATTRS[f"KEY_KP_{_index}"] = f"keypad {_index}"
for _side in ("LEFT", "RIGHT"):
    for _modifier in ("SHIFT", "CONTROL", "ALT", "SUPER"):
        GLFW_KEY_ATTRS[f"KEY_{_side}_{_modifier}"] = _modifier.lower()


@pytest.mark.requires_gl
def test_the_numbers_are_what_the_installed_glfw_says():
    import glfw

    from crude.ui.gl.window import glfw_constants

    real = glfw_constants()
    assert real["PRESS"] == ACTIONS["press"]
    assert real["RELEASE"] == ACTIONS["release"]
    assert real["REPEAT"] == ACTIONS["repeat"]
    assert real["MOD_SHIFT"] == MODS["shift"]
    assert real["MOD_CONTROL"] == MODS["control"]
    assert real["MOD_ALT"] == MODS["alt"]
    assert real["MOD_SUPER"] == MODS["super"]
    assert real["MOUSE_BUTTON_LEFT"] == MOUSE_BUTTONS["left"]
    assert real["MOUSE_BUTTON_RIGHT"] == MOUSE_BUTTONS["right"]
    assert real["MOUSE_BUTTON_MIDDLE"] == MOUSE_BUTTONS["middle"]
    assert real["KEY_SPACE"] == ord(" ")
    # the whole table, not a sample of it: one name added at the top of a hand-
    # copied list shifts every number below it, and only GLFW can say so
    assert KEY_NAMES == {getattr(glfw, attr): word
                         for attr, word in GLFW_KEY_ATTRS.items()}
    assert key_name(glfw.KEY_TAB) == "tab"
    assert key_name(glfw.KEY_PAGE_DOWN) == "page down"


# -- the view a test or a log gets --------------------------------------------

def test_an_event_carries_what_it_was_given():
    event = InputEvent("move", {"x": 1.0, "y": 2.0})
    assert event.get("x") == 1.0
    assert event.get("z") is None
    assert event.get("z", 0) == 0
    assert "move" in str(event)


def test_the_log_is_the_recorder_s_view(router, knob, pad):
    router.click(20, 20)
    router.move_to(110, 60)
    assert router.hits("press") == [knob]
    assert router.hits("enter") == [knob, pad]
    assert "leave" in router.names()


def test_stats_say_what_the_router_is_doing(router, knob):
    router.move_to(20, 20)
    router.press()
    assert router.stats() == {
        "events": 2, "dispatched": 2, "queued": 0, "hover": True,
        "focus": True, "dragging": True, "hotkeys": 0,
        "cursor": (20.0, 20.0)}


def test_reset_forgets_the_frame_but_not_the_person(router, knob):
    router.move_to(20, 20)
    router.press()
    router.on_cursor(30, 30)
    router.reset()
    assert router.queue == []
    assert router.log == []
    assert router.buttons == set()
    assert router.capture is None
    assert router.hover is knob
    assert router.focus is knob


def test_a_router_describes_itself(router, knob):
    router.move_to(20, 20)
    assert "knob" in str(router)
    router.press()
    assert "drag=yes" in str(router)
    assert "0 queued" in str(InputRouter())


def test_a_router_with_no_hit_test_hits_nothing():
    router = InputRouter()
    router.on_cursor(10, 10)
    router.dispatch()
    assert router.hover is None
    assert router.target_at(10, 10) is None
    assert (router.width, router.height) == (0.0, 0.0)
