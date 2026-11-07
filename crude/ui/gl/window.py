"""A GLFW window, its context, and the events that come out of it.

Adopted from ``ui/window.py`` in the repository root, which is a good small
wrapper: it knows the hints that matter (3.3 core forward-compat, four samples,
logical size for input and framebuffer size for the viewport). What it could not
do is anything this project needs:

* **Reference counting.** ``glfw.init()`` and ``glfw.terminate()`` are process
  wide, so the old ``destroy()`` terminated GLFW under any other window that
  might still be open. A count means the last one out really does turn the
  lights off, and a window that never opened does not get to call terminate.
* **Events.** Input arrives through GLFW callbacks, which fire during
  ``poll_events`` - so the window holds the callbacks and hands what they say to
  whatever is listening (:class:`crude.ui.input.InputRouter`), rather than
  owning any of the meaning itself.
* **Failure.** No display, no GLFW or a driver that refuses a context is a
  :class:`~crude.ui.base.WindowUnavailable`, which the CLI turns into a console
  progress report instead of a traceback.

The coordinate rule the plan fixes lives here too: :func:`size` is in logical
pixels, the same space the cursor and every widget are in, and :func:`framebuffer_size`
is in device pixels and only ever feeds the viewport and the scissor.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from ..base import WindowUnavailable

#: the hints the old wrapper used, kept because they are the ones that work:
#: a core profile that PyOpenGL can draw against, and multisampling so a knob
#: edge is not a staircase
CONTEXT_MAJOR = 3
CONTEXT_MINOR = 3
SAMPLES = 4

#: how many windows this process has open: GLFW's own init and terminate are
#   process-wide, so the first window starts it and the last one stops it
_open_windows = 0


class Window:
    """One window, its context, and the plain hooks its events come through."""

    def __init__(self, width: int = 800, height: int = 600,
                 title: str = "crude", *, vsync: bool = True,
                 visible: bool = True) -> None:
        global _open_windows
        try:
            import glfw
        except ImportError as exc:  # the dependency is there or the run is headless
            raise WindowUnavailable(
                f"glfw is not installed, so there is no window ({exc})")
        self._glfw = glfw
        self.handle = None
        self._own_handle = False
        self._counted = False
        self.title = title

        started_here = _open_windows == 0
        if started_here and not glfw.init():
            raise WindowUnavailable(
                "GLFW could not start: there is no display to open a window on")

        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, CONTEXT_MAJOR)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, CONTEXT_MINOR)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, glfw.TRUE)
        glfw.window_hint(glfw.SAMPLES, SAMPLES)
        glfw.window_hint(glfw.VISIBLE, glfw.TRUE if visible else glfw.FALSE)

        handle = glfw.create_window(int(width), int(height), title, None, None)
        if not handle:
            # the init this window started has to be undone again, or a run that
            # tried a window and fell back would leave GLFW half up
            if started_here:
                glfw.terminate()
            raise WindowUnavailable(
                f"a {int(width)} x {int(height)} window could not be opened")
        self.handle = handle
        self._own_handle = True
        _open_windows += 1
        self._counted = True
        glfw.make_context_current(handle)
        self.set_vsync(vsync)

        #: what the window's own size is, in logical pixels
        self.on_resize: Optional[Callable[[int, int], None]] = None
        #: what the pixel size is: a DPI change arrives here, not in on_resize
        self.on_framebuffer_resize: Optional[Callable[[int, int], None]] = None
        #: the cursor, in logical pixels, y down from the top-left of this window
        self.on_cursor: Optional[Callable[[float, float], None]] = None
        #: a mouse button: ``(button, action, mods)``, actions as GLFW spells them
        self.on_button: Optional[Callable[[int, int, int], None]] = None
        #: a key: ``(key, scancode, action, mods)``
        self.on_key: Optional[Callable[[int, int, int, int], None]] = None
        #: a character that made it through the keyboard layout, as text
        self.on_char: Optional[Callable[[str], None]] = None
        #: a scroll wheel: ``(x, y)`` in logical steps
        self.on_scroll: Optional[Callable[[float, float], None]] = None
        #: asked before the window is allowed to go: return ``False`` to stay open
        self.on_close_request: Optional[Callable[[], bool]] = None
        #: files dropped onto the window, as paths
        self.on_drop: Optional[Callable[[List[str]], None]] = None
        self._install_callbacks()

    # -- GLFW wiring ------------------------------------------------------
    def _install_callbacks(self) -> None:
        glfw = self._glfw
        handle = self.handle

        def size(_window, width, height):
            if self.on_resize:
                self.on_resize(int(width), int(height))

        def framebuffer(_window, width, height):
            if self.on_framebuffer_resize:
                self.on_framebuffer_resize(int(width), int(height))

        def cursor(_window, x, y):
            if self.on_cursor:
                self.on_cursor(float(x), float(y))

        def button(_window, button_, action, mods):
            if self.on_button:
                self.on_button(int(button_), int(action), int(mods))

        def key(_window, key_, scancode, action, mods):
            if self.on_key:
                self.on_key(int(key_), int(scancode), int(action), int(mods))

        def char(_window, codepoint):
            if self.on_char:
                try:
                    self.on_char(chr(int(codepoint)))
                except (ValueError, OverflowError):
                    pass  # a codepoint no wider a character is not an event

        def scroll(_window, x, y):
            if self.on_scroll:
                self.on_scroll(float(x), float(y))

        def drop(_window, paths):
            if self.on_drop:
                self.on_drop(list(paths))

        def close(_window):
            # a request is not a close: a project that is still loading, or that
            # wants to ask first, can say no and the window stays
            if self.on_close_request and self.on_close_request() is False:
                return
            glfw.set_window_should_close(_window, True)

        glfw.set_window_size_callback(handle, size)
        glfw.set_framebuffer_size_callback(handle, framebuffer)
        glfw.set_cursor_pos_callback(handle, cursor)
        glfw.set_mouse_button_callback(handle, button)
        glfw.set_key_callback(handle, key)
        glfw.set_char_callback(handle, char)
        glfw.set_scroll_callback(handle, scroll)
        glfw.set_drop_callback(handle, drop)
        glfw.set_window_close_callback(handle, close)

    def _release_init(self) -> None:
        """One window less: GLFW is only worth turning off once none are left."""
        global _open_windows
        if not self._counted:
            return
        self._counted = False
        _open_windows = max(0, _open_windows - 1)
        if not _open_windows:
            self._glfw.terminate()

    # -- the frame --------------------------------------------------------
    def poll(self, *, wait: float = 0.0) -> bool:
        """Pump the events that happened since last time; ``False`` when closed.

        ``wait`` blocks in the OS for up to that many seconds instead of
        returning at once, which is how a window at 30 fps costs nothing between
        frames - and why no call in this file needs a ``sleep`` of its own.
        """
        if not self.alive:
            return False
        if wait:
            self._glfw.wait_events_timeout(float(wait))
        else:
            self._glfw.poll_events()
        return not self.closed

    def swap(self) -> None:
        """Put the frame that was just drawn on screen."""
        if self.alive:
            self._glfw.swap_buffers(self.handle)

    def make_context_current(self) -> bool:
        if not self.alive:
            return False
        self._glfw.make_context_current(self.handle)
        return True

    # -- what the window is -----------------------------------------------
    def size(self) -> Tuple[int, int]:
        """The client area in logical pixels - the space widgets live in."""
        if not self.alive:
            return (0, 0)
        width, height = self._glfw.get_window_size(self.handle)
        return (int(width), int(height))

    def framebuffer_size(self) -> Tuple[int, int]:
        """The same area in device pixels, for the viewport and the scissor."""
        if not self.alive:
            return (0, 0)
        width, height = self._glfw.get_framebuffer_size(self.handle)
        return (int(width), int(height))

    def cursor_pos(self) -> Tuple[float, float]:
        """Where the pointer is now, in logical pixels, the same space as size()."""
        if not self.alive:
            return (0.0, 0.0)
        x, y = self._glfw.get_cursor_pos(self.handle)
        return (float(x), float(y))

    @property
    def width(self) -> int:
        return self.size()[0]

    @property
    def height(self) -> int:
        return self.size()[1]

    @property
    def dpr(self) -> float:
        """Device pixels per logical pixel, from the two sizes GLFW gives."""
        width, _ = self.size()
        fb_width, _ = self.framebuffer_size()
        return (fb_width / width) if width else 1.0

    @property
    def alive(self) -> bool:
        return self._own_handle and self.handle is not None

    @property
    def closed(self) -> bool:
        return bool(self.alive and self._glfw.window_should_close(self.handle))

    @property
    def focused(self) -> bool:
        return bool(self.alive and self._glfw.get_window_attrib(
            self.handle, self._glfw.FOCUSED))

    def set_title(self, title: str) -> None:
        self.title = str(title)
        if self.alive:
            self._glfw.set_window_title(self.handle, self.title)

    def set_vsync(self, enabled: bool) -> None:
        """``True`` swaps once per refresh, which is the plan's ``runtime.vsync``."""
        if self.alive:
            self._glfw.swap_interval(1 if enabled else 0)

    def set_size_callback(self, callback: Callable[[int, int], None]) -> None:
        self.on_resize = callback

    def set_framebuffer_callback(
            self, callback: Callable[[int, int], None]) -> None:
        self.on_framebuffer_resize = callback

    def set_input_listener(self, listener: Any) -> "Window":
        """Hand every event to something that queues it - the :class:`InputRouter`.

        Anything with the matching ``on_*`` methods will do, which is what lets a
        test listen with a list instead of a router.
        """
        for name in ("on_resize", "on_framebuffer_resize", "on_cursor",
                     "on_button", "on_key", "on_char", "on_scroll",
                     "on_close_request", "on_drop"):
            found = getattr(listener, name, None)
            if callable(found):
                setattr(self, name, found)
        return self

    def request_close(self) -> None:
        """Close on the next poll, as though the person had pressed the box."""
        if self.alive:
            self._glfw.set_window_should_close(self.handle, True)

    def destroy(self) -> None:
        """Put the window - and, last of all, GLFW - back. Twice is fine."""
        if self.alive:
            self._glfw.destroy_window(self.handle)
            self.handle = None
            self._own_handle = False
        self._release_init()

    def __enter__(self) -> "Window":
        return self

    def __exit__(self, *_) -> None:
        self.destroy()

    def __del__(self) -> None:
        # a window that was never closed still has to give GLFW back
        try:
            self.destroy()
        except Exception:
            pass

    def __str__(self) -> str:
        state = "open" if self.alive else "closed"
        return f"Window '{self.title}' {self.width}x{self.height} ({state})"


def glfw_constants() -> Dict[str, int]:
    """The GLFW numbers a router and a script both need, once, without glfw.

    Key names and actions are GLFW's own integers; the input layer maps them to
    the words a ``.crude`` script says (``"enter"``, ``"down"``), and a machine
    with no GLFW installed still has to be able to talk about an event it faked.
    """
    try:
        import glfw
    except ImportError:
        return {}
    wanted = ("PRESS", "RELEASE", "REPEAT", "MOD_SHIFT", "MOD_CONTROL",
              "MOD_ALT", "MOD_SUPER", "KEY_SPACE", "KEY_ENTER", "KEY_ESCAPE",
              "KEY_BACKSPACE", "KEY_TAB", "KEY_LEFT", "KEY_RIGHT", "KEY_UP",
              "KEY_DOWN", "KEY_HOME", "KEY_END", "KEY_DELETE", "KEY_INSERT",
              "MOUSE_BUTTON_LEFT", "MOUSE_BUTTON_RIGHT", "MOUSE_BUTTON_MIDDLE")
    return {name: int(getattr(glfw, name)) for name in wanted
            if hasattr(glfw, name)}


__all__ = ["Window", "WindowUnavailable", "glfw_constants", "CONTEXT_MAJOR",
           "CONTEXT_MINOR", "SAMPLES"]
