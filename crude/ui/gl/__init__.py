"""The GL half of the UI: two factories, and neither of them promises a display.

Nothing in this package is importable by :mod:`crude.ui` - that would put GLFW and
PyOpenGL between ``import crude`` and every test in the repository. A caller
reaches the real thing through :func:`make_renderer` or :func:`open_display`, both
of which import inside the call and turn an ``ImportError``, a failed ``glfw.init``
or a driver that refuses a context into
:class:`~crude.ui.base.RendererUnavailable`, so the CLI can say "no window, the
terminal instead" in one place.

A fake test never imports this file. The two that do are marked ``requires_gl``,
which the default run deselects.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional

from ..base import Renderer, RendererUnavailable, WindowUnavailable

__all__ = ["Display", "gl_available", "window_available", "make_renderer",
           "open_window", "open_display", "RendererUnavailable",
           "WindowUnavailable"]


def gl_available() -> bool:
    """Could this machine draw? A question about the libraries, not the screen."""
    try:
        import OpenGL.GL  # noqa: F401
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401,F401
    except Exception:  # missing, broken, or a build that will not import
        return False
    return True


def window_available() -> bool:
    """As :func:`gl_available`, and GLFW can be started too."""
    if not gl_available():
        return False
    try:
        import glfw  # noqa: F401
    except Exception:
        return False
    return True


def _renderer_module():
    if not gl_available():
        raise RendererUnavailable(
            "PyOpenGL, numpy or Pillow is missing, so there is nothing to draw with"
        )
    from . import renderer
    return renderer


def make_renderer() -> Renderer:
    """A :class:`~crude.ui.gl.renderer.GLRenderer`, ready for a context.

    The context must already be current - :func:`open_display` makes both in the
    right order, and the renderer does not touch the driver until its first frame.
    """
    return _renderer_module().GLRenderer()


def open_window(width: int = 800, height: int = 600, title: str = "crude",
                **kwargs: Any) -> Any:
    """A GLFW window, or :class:`~crude.ui.base.WindowUnavailable`."""
    if not window_available():
        raise WindowUnavailable("glfw is not usable on this machine, so a "
                                "window cannot be opened")
    from .window import Window
    try:
        return Window(width, height, title, **kwargs)
    except WindowUnavailable:
        raise
    except Exception as exc:  # a driver that says no in its own words
        raise WindowUnavailable(f"the window could not be opened: {exc}") from exc


class Display(NamedTuple):
    """A window and the renderer that draws into it, for one clean close."""

    window: Any
    renderer: Renderer

    def frame(self) -> "Display":
        """Begin the frame at the size the window actually is now."""
        width, height = self.window.size()
        fb_width, fb_height = self.window.framebuffer_size()
        self.renderer.begin(width, height, fb_width, fb_height)
        return self

    def present(self) -> int:
        """Finish the frame and show it; how many draw calls went into it."""
        ops = self.renderer.end()
        self.window.swap()
        return ops

    def pump(self, *, wait: float = 0.0) -> bool:
        """Let the window answer the OS; ``False`` once it has been closed.

        Anything that draws between two pieces of work - the splash most of all -
        has to call this or the window goes grey, and the answer is how the drawer
        learns that the person has had enough and closed it.
        """
        return self.window.poll(wait=wait)

    def set_title(self, title: str) -> None:
        """Say who the window is on the person's behalf."""
        self.window.set_title(str(title))

    def close(self) -> None:
        """Let go of the GPU memory, then the window. Twice is fine, as ever."""
        try:
            self.renderer.close()
        finally:
            self.window.destroy()

    def __str__(self) -> str:
        return f"Display({self.window})"


def open_display(width: int = 800, height: int = 600, title: str = "crude",
                 *, vsync: bool = True, visible: bool = True,
                 listener: Optional[Any] = None) -> Display:
    """The window mode's whole display surface: window, context, renderer.

    Everything here needs the step before it - GLFW, then a context, then a
    renderer that can build shaders against that context - which is why one call
    does all three, and why a failure of any of them is the same
    :class:`~crude.ui.base.WindowUnavailable` the run falls back on.
    """
    window = open_window(width, height, title, vsync=vsync, visible=visible)
    try:
        renderer = make_renderer()
    except RendererUnavailable:
        window.destroy()
        raise
    if listener is not None:
        window.set_input_listener(listener)
    return Display(window, renderer)
