"""Minimal GLFW window + GL context wrapper."""
from __future__ import annotations
import glfw


class Window:
    def __init__(self, width: int, height: int, title: str) -> None:
        if not glfw.init():
            raise RuntimeError("glfw.init() failed")

        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, glfw.TRUE)
        glfw.window_hint(glfw.SAMPLES, 4)

        self.handle = glfw.create_window(width, height, title, None, None)
        if not self.handle:
            glfw.terminate()
            raise RuntimeError("Failed to create GLFW window")

        glfw.make_context_current(self.handle)
        glfw.swap_interval(1)

    def poll(self) -> bool:
        glfw.poll_events()
        return not glfw.window_should_close(self.handle)

    def swap(self) -> None:
        glfw.swap_buffers(self.handle)

    def get_size(self) -> tuple[int, int]:
        """Logical window size — matches GLFW cursor coords and widget coords."""
        w, h = glfw.get_window_size(self.handle)
        return int(w), int(h)

    def get_framebuffer_size(self) -> tuple[int, int]:
        """Framebuffer size — used for glViewport and glScissor."""
        w, h = glfw.get_framebuffer_size(self.handle)
        return int(w), int(h)

    def set_title(self, title: str) -> None:
        glfw.set_window_title(self.handle, title)

    def set_size_callback(self, fn) -> None:
        """fn(width, height) — logical window size, fires on resize/maximize."""
        glfw.set_window_size_callback(
            self.handle, lambda _w, w, h: fn(int(w), int(h)))

    def set_framebuffer_callback(self, fn) -> None:
        """fn(width, height) — pixel size, fires on resize and DPI changes."""
        glfw.set_framebuffer_size_callback(
            self.handle, lambda _w, w, h: fn(int(w), int(h)))

    def destroy(self) -> None:
        glfw.destroy_window(self.handle)
        glfw.terminate()