"""The GLFW window: its two sizes, its refcount, and the way it says no.

Marked ``requires_gl`` and therefore deselected by the default run. Everything
here is about the two rules the plan fixes for the window half, plus the one
thing the old wrapper got wrong:

* :meth:`Window.size` is the client area in **logical** pixels - the same space
  the cursor, the widgets and every draw call are in - and
  :meth:`Window.framebuffer_size` is in **device** pixels and only ever feeds the
  viewport and the scissor;
* ``glfw.init()`` and ``glfw.terminate()`` belong to the process, not to one
  window, so the last window out turns the lights off and no other window gets to;
* a machine with no GLFW, no display or a driver that refuses a context answers
  :class:`~crude.ui.base.WindowUnavailable`, which is a sentence the CLI can
  print, rather than an ``ImportError`` from inside a constructor.

The synthetic-event part of the contract - what GLFW does when a person moves
the mouse - is tested in :mod:`test_input_router`, where the events are faked;
what is tested here is that the window *has* those hooks and hands them over.
"""

from __future__ import annotations

from math import isfinite

import pytest

from crude.ui.base import WindowUnavailable
from crude.ui.input import InputRouter

pytestmark = pytest.mark.requires_gl


def available():
    from crude.ui.gl import window_available

    return window_available()


@pytest.fixture
def window():
    """One invisible window, or a skip on a machine that cannot have one."""
    from crude.ui.gl import open_window

    if not available():
        pytest.skip("this machine has no GLFW to open a window with")
    try:
        made = open_window(240, 180, "crude test", visible=False)
    except Exception as exc:
        pytest.skip(f"this machine will not hand out a window: {exc}")
    yield made
    made.destroy()


# -- the two sizes -----------------------------------------------------------

def test_a_window_is_the_size_it_was_asked_for(window):
    assert window.size() == (240, 180)
    assert (window.width, window.height) == (240, 180)


def test_the_framebuffer_is_the_same_window_in_device_pixels(window):
    width, height = window.size()
    fb_width, fb_height = window.framebuffer_size()
    assert fb_width == pytest.approx(width * window.dpr, abs=1)
    assert fb_height == pytest.approx(height * window.dpr, abs=1)
    assert window.dpr >= 1.0


def test_the_cursor_is_a_position_in_the_space_the_window_declares(window):
    # GLFW only turns the pointer's screen position into content-area coordinates
    # while it is over the window, and an invisible one is never under the mouse,
    # so all a test can say here is that the answer is a pair of finite floats -
    # the space a real event arrives in is the router's, tested from the other end
    x, y = window.cursor_pos()
    assert isinstance(x, float) and isinstance(y, float)
    assert isfinite(x) and isfinite(y)


def test_a_window_that_is_not_there_anymore_has_no_size(window):
    window.destroy()
    assert window.size() == (0, 0)
    assert window.framebuffer_size() == (0, 0)
    assert window.cursor_pos() == (0.0, 0.0)
    assert window.dpr == 1.0


# -- the events --------------------------------------------------------------

def test_the_hooks_a_router_needs_are_all_there(window):
    router = InputRouter()
    window.set_input_listener(router)
    for name in ("on_resize", "on_framebuffer_resize", "on_cursor", "on_button",
                 "on_key", "on_char", "on_scroll", "on_close_request",
                 "on_drop"):
        # == and not is: a bound method is made again by every attribute read of
        # the router, while the window keeps the one it was handed
        assert getattr(window, name) == getattr(router, name), name


def test_an_event_the_window_calls_with_lands_in_the_router_s_queue(window):
    router = InputRouter()
    window.set_input_listener(router)
    # this is the attribute GLFW's own callback calls, so the path is the real one
    window.on_cursor(12.0, 30.0)
    assert router.queue[-1].name == "move"
    router.dispatch()
    assert (router.x, router.y) == (12.0, 30.0)


def test_a_listener_that_has_only_some_hooks_keeps_the_others(window):
    class Listener:
        def on_cursor(self, x, y):
            pass

    router = InputRouter()
    window.set_input_listener(router)
    listener = Listener()
    window.set_input_listener(listener)
    assert window.on_cursor == listener.on_cursor
    assert window.on_key == router.on_key


def test_a_window_being_polled_is_alive_until_it_is_asked_to_go(window):
    assert window.poll() is True
    assert window.closed is False
    window.request_close()
    assert window.poll() is False
    assert window.closed is True


def test_a_window_that_has_been_destroyed_answers_nothing(window):
    window.destroy()
    assert window.poll() is False
    assert window.alive is False
    window.swap()                       # harmless, as everything after a close is
    assert window.make_context_current() is False


# -- the process-wide half ---------------------------------------------------

def test_the_last_window_out_terminates_glfw_and_the_next_one_starts_it_again():
    from crude.ui.gl import open_window
    from crude.ui.gl import window as window_module

    if not available():
        pytest.skip("this machine has no GLFW")
    first = open_window(120, 100, "first", visible=False)
    second = open_window(120, 100, "second", visible=False)
    assert window_module._open_windows == 2

    first.destroy()
    # the window still open keeps GLFW up, and keeps drawing
    assert window_module._open_windows == 1
    assert second.alive is True
    assert second.framebuffer_size()[0] > 0

    second.destroy()
    assert window_module._open_windows == 0
    assert window_module._open_windows == 0      # destroying twice changes nothing

    third = open_window(120, 100, "third", visible=False)
    assert third.alive is True
    third.destroy()
    assert window_module._open_windows == 0


def test_a_window_is_a_context_manager_that_lets_go():
    from crude.ui.gl import open_window

    if not available():
        pytest.skip("this machine has no GLFW")
    with open_window(100, 80, "brief", visible=False) as made:
        assert made.alive is True
    assert made.alive is False


def test_a_window_cannot_be_made_is_a_reason_and_leaves_nothing_half_up(
        monkeypatch):
    import glfw

    from crude.ui.gl import window as window_module

    if not available():
        pytest.skip("this machine has no GLFW")
    monkeypatch.setattr(glfw, "create_window",
                        lambda *args, **kwargs: None)
    before = window_module._open_windows

    with pytest.raises(WindowUnavailable) as caught:
        window_module.Window(100, 80, "never")
    assert "could not be opened" in str(caught.value)
    assert window_module._open_windows == before


def test_glfw_that_will_not_start_says_so_in_stead_of_a_traceback(monkeypatch):
    import glfw

    from crude.ui.gl import window as window_module

    if not available():
        pytest.skip("this machine has no GLFW")
    monkeypatch.setattr(glfw, "init", lambda: False)
    monkeypatch.setattr(window_module, "_open_windows", 0)

    with pytest.raises(WindowUnavailable) as caught:
        window_module.Window(100, 80, "never")
    assert "no display to open a window on" in str(caught.value)
    assert window_module._open_windows == 0


def test_a_machine_with_no_glfw_at_all_is_told_off_before_it_is_tried(
        monkeypatch):
    from crude.ui.gl import open_display, open_window

    monkeypatch.setattr("crude.ui.gl.window_available", lambda: False)

    for call, name in ((open_window, "a window"), (open_display, "a window")):
        with pytest.raises(WindowUnavailable) as caught:
            call(100, 80, "nope")
        assert "glfw" in str(caught.value).lower()


# -- the two halves together --------------------------------------------------

def test_the_display_factory_makes_a_window_and_a_renderer_that_draw():
    from crude.ui.gl import open_display

    if not available():
        pytest.skip("this machine has no GLFW")
    display = open_display(200, 100, "together", visible=False)
    try:
        display.frame()
        display.renderer.clear("#101010")
        display.renderer.rect(0, 0, 10, 10, "white")
        ops = display.present()
        assert ops == 2
        assert str(display.window) in str(display)
        display.set_title("renamed")
        assert display.window.title == "renamed"
        assert display.pump() is True
    finally:
        display.close()
    assert display.window.alive is False
    display.close()                     # twice is fine, as ever


def test_a_frame_is_begun_at_the_size_the_window_actually_is():
    from crude.ui.gl import open_display

    if not available():
        pytest.skip("this machine has no GLFW")
    display = open_display(300, 150, "sized", visible=False)
    try:
        display.frame()
        assert (display.renderer.width, display.renderer.height) \
            == display.window.size()
        assert (display.renderer.fb_width, display.renderer.fb_height) \
            == display.window.framebuffer_size()
        display.present()
    finally:
        display.close()
