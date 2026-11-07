"""The renderer that reaches the GPU: batched SDF shapes, textured quads, text.

Adopted from the repository's own ``ui/renderer.py`` - the plan's risk 8 says that
directory is reference-only and that these two files are worth taking *with
edits*, because the batching, the SDF rounded rect and the PIL text bake are
already right. What changed on the way in:

* it is a :class:`~crude.ui.base.Renderer` now, so every argument rule lives in
  the contract and a test can read the same widget on a recorder;
* ``circle``, ``arc``, ``polyline``, ``polygon``, ``atlas``, ``clear`` and
  ``measure`` were added - the shapes the plan names, none of which the old file
  had;
* the clip is the *intersection* of the stack (the contract computes it) instead
  of whichever rect was pushed last, and a clip change flushes the batch so the
  drawing before it is not cut by the rect after it;
* ``upload_frame`` and its OpenCV import are gone: a camera frame is not a
  dependency of this project, and the ring texture a scope needs is served by
  :meth:`~crude.ui.base.Renderer.upload_texture` instead;
* no textures, no shaders and no framebuffer are made until the first frame, so
  constructing one of these with no context current is a silent mistake rather
  than a driver error, and the factory can wrap it in
  :class:`~crude.ui.base.RendererUnavailable`.

The vertex layout is the old one and still the reason a frame is cheap: eleven
floats per vertex (position, uv, the box's size, its corner radius, colour), one
VAO, and one batch per ``(texture, mode)`` change. Mode 0 is a textured quad
tinted by its colour - every glyph, icon and image - mode 1 is the signed
distance box, and mode 2 is a flat colour.
"""

from __future__ import annotations

import ctypes
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from OpenGL.GL import (
    GL_ARRAY_BUFFER, GL_BLEND, GL_CLAMP_TO_EDGE, GL_COLOR_BUFFER_BIT,
    GL_COMPILE_STATUS, GL_DYNAMIC_DRAW, GL_FALSE, GL_FLOAT, GL_FRAGMENT_SHADER,
    GL_LINEAR, GL_LINK_STATUS, GL_NEAREST, GL_ONE, GL_ONE_MINUS_SRC_ALPHA,
    GL_PACK_ALIGNMENT, GL_RGBA, GL_SCISSOR_TEST, GL_SRC_ALPHA, GL_TEXTURE0,
    GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_TEXTURE_MIN_FILTER,
    GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_TRIANGLES, GL_UNPACK_ALIGNMENT,
    GL_UNSIGNED_BYTE, GL_VERTEX_SHADER, glActiveTexture, glAttachShader,
    glBindBuffer, glBindTexture, glBindVertexArray, glBlendFuncSeparate,
    glBufferData, glClear, glClearColor, glCompileShader, glCreateProgram,
    glCreateShader, glDeleteBuffers, glDeleteTextures, glDeleteVertexArrays,
    glDisable, glDrawArrays, glEnable, glEnableVertexAttribArray, glGenBuffers,
    glGenTextures, glGenVertexArrays, glGetProgramInfoLog, glGetProgramiv,
    glGetShaderInfoLog, glGetShaderiv, glGetUniformLocation, glLinkProgram,
    glPixelStorei, glReadPixels, glScissor, glShaderSource, glTexImage2D,
    glTexParameteri, glUniform1i, glUniformMatrix4fv, glUseProgram,
    glVertexAttribPointer, glViewport,
)
from PIL import Image, ImageDraw, ImageFont

from ..base import DrawOp, RGBA, Renderer, RendererUnavailable, texture_key
from ..geometry import (
    arc_points, capsule, circle_bounds, polygon_bounds, polygon_fan,
    polyline_pairs, rect_corners, tapered_quad,
)

#: the three things a batch of vertices can be
TEXTURED = 0
#: a signed-distance box: rounded rect, and a disc when the radius is half the box
DISTANCE = 1
#: whatever colour the vertex carries, nothing else
FLAT = 2

_FLOATS_PER_VERTEX = 11
_STRIDE = _FLOATS_PER_VERTEX * 4

#: the corners of a whole texture, in the order a triangle pair wants
_FULL_UV = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
#: the same three vertices a flat triangle needs - unused in mode 2, but finite
_TRI_UV = ((0.0, 0.0), (1.0, 0.0), (0.5, 1.0))

#: faces the text bake tries, in order; a manifest pins one in the next stage
FONT_CANDIDATES = (
    "arial.ttf", "segoeui.ttf", "DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

VERT = """
#version 330 core
layout(location=0) in vec2 a_pos;
layout(location=1) in vec2 a_uv;
layout(location=2) in vec2 a_size;
layout(location=3) in float a_radius;
layout(location=4) in vec4 a_color;

uniform mat4 u_proj;
out vec2 v_uv;
out vec2 v_size;
out float v_radius;
out vec4 v_color;

void main() {
    v_uv = a_uv;
    v_size = a_size;
    v_radius = a_radius;
    v_color = a_color;
    gl_Position = u_proj * vec4(a_pos, 0.0, 1.0);
}
"""

FRAG = """
#version 330 core
in vec2 v_uv;
in vec2 v_size;
in float v_radius;
in vec4 v_color;
out vec4 frag;

uniform sampler2D u_tex;
uniform int u_round;

float sd_round_box(vec2 p, vec2 hs, float r) {
    vec2 q = abs(p) - hs + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

void main() {
    vec4 tex = texture(u_tex, v_uv);
    if (u_round == 1) {
        vec2 p  = (v_uv - 0.5) * v_size;
        vec2 hs = v_size * 0.5;
        float r = min(v_radius, min(hs.x, hs.y));
        float d = sd_round_box(p, hs, r);
        float a = 1.0 - smoothstep(-0.5, 0.5, d);
        frag = vec4(v_color.rgb, v_color.a * a);
    } else if (u_round == 2) {
        frag = v_color;
    } else {
        frag = vec4(v_color.rgb * tex.rgb, v_color.a * tex.a);
    }
    if (frag.a < 0.001) discard;
}
"""


def ortho(left: float, right: float, bottom: float, top: float,
          near: float = -1.0, far: float = 1.0) -> np.ndarray:
    """A projection that puts logical pixels where the UI says they are.

    The window is mapped ``0..width`` across and ``0..height`` *down*, which is
    the whole reason no widget in this package has to think about GL's y: the
    ortho matrix is the only place the flip happens.
    """
    return np.array([
        [2 / (right - left), 0, 0, -(right + left) / (right - left)],
        [0, 2 / (top - bottom), 0, -(top + bottom) / (top - bottom)],
        [0, 0, -2 / (far - near), -(far + near) / (far - near)],
        [0, 0, 0, 1],
    ], dtype=np.float32)


class GLRenderer(Renderer):
    """A batched 2D renderer on an OpenGL 3.3 core profile.

    The context has to be current before the first frame; :class:`Window` makes
    it so, and :func:`crude.ui.gl.make_renderer` builds the two in that order.
    """

    def __init__(self) -> None:
        super().__init__("gl")
        self._ready = False
        self._prog = 0
        self._u_proj = self._u_tex = self._u_round = -1
        self._vao = self._vbo = 0
        self._white: int = 0
        #: texture key -> the GL handle that holds it
        self._handles: Dict[str, int] = {}
        #: texture key -> the size its pixels were last uploaded at, in device px
        self._sizes: Dict[str, Tuple[int, int]] = {}
        self._batches: List[Tuple[Optional[int], int, np.ndarray, Any]] = []
        self._pending: List[float] = []
        self._pending_tex: Optional[int] = None
        self._pending_mode = DISTANCE
        self._pending_clip: Any = None
        self._proj = np.eye(4, dtype=np.float32)
        self._fonts: Dict[int, Any] = {}
        self._text: Dict[Tuple[str, float], Tuple[int, float, float]] = {}
        #: keys asked for that could not be loaded - S7 turns these into a warning
        self.missing: List[str] = []
        #: the batch and triangle counts of the last draw pass, for ``--stats``
        self.last_batches = 0
        self.last_triangles = 0

    # -- the context ------------------------------------------------------
    def _ensure_gl(self) -> None:
        if self._ready:
            return
        try:
            self._prog = self._build_program(VERT, FRAG)
        except RuntimeError as exc:
            raise RendererUnavailable(f"the shaders would not build: {exc}")
        self._u_proj = glGetUniformLocation(self._prog, "u_proj")
        self._u_tex = glGetUniformLocation(self._prog, "u_tex")
        self._u_round = glGetUniformLocation(self._prog, "u_round")
        self._vao = glGenVertexArrays(1)
        self._vbo = glGenBuffers(1)
        glBindVertexArray(self._vao)
        glBindBuffer(GL_ARRAY_BUFFER, self._vbo)
        for index, (count, offset) in enumerate(
                ((2, 0), (2, 8), (2, 16), (1, 24), (4, 28))):
            glEnableVertexAttribArray(index)
            glVertexAttribPointer(index, count, GL_FLOAT, GL_FALSE, _STRIDE,
                                  ctypes.c_void_p(offset))
        glBindVertexArray(0)
        self._white = self._make_solid_texture()
        self._ready = True

    @staticmethod
    def _compile(source: str, kind: int) -> int:
        shader = glCreateShader(kind)
        glShaderSource(shader, source)
        glCompileShader(shader)
        if not glGetShaderiv(shader, GL_COMPILE_STATUS):
            log = glGetShaderInfoLog(shader)
            raise RuntimeError(log.decode(errors="replace") if log else "no log")
        return shader

    @classmethod
    def _build_program(cls, vertex: str, fragment: str) -> int:
        program = glCreateProgram()
        glAttachShader(program, cls._compile(vertex, GL_VERTEX_SHADER))
        glAttachShader(program, cls._compile(fragment, GL_FRAGMENT_SHADER))
        glLinkProgram(program)
        if not glGetProgramiv(program, GL_LINK_STATUS):
            log = glGetProgramInfoLog(program)
            raise RuntimeError(log.decode(errors="replace") if log else "no log")
        return program

    @staticmethod
    def _make_solid_texture() -> int:
        """One white pixel: the texture every shape that is not an image uses."""
        handle = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, handle)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 1, 1, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, bytes((255, 255, 255, 255)))
        return handle

    # -- a frame ----------------------------------------------------------
    def on_frame_begin(self) -> None:
        self._ensure_gl()
        self._batches = []
        self._pending = []
        self._pending_tex = None
        self._pending_mode = DISTANCE
        self._pending_clip = None
        self._proj = ortho(0, self.width, self.height, 0)

    def on_frame_end(self) -> None:
        """Flush, then draw every batch: one state change per batch, not per shape."""
        self._flush()
        self._draw_batches()

    def _draw_batches(self) -> None:
        """Put the batches collected so far on the framebuffer.

        That is the whole of ``end()``, and :meth:`on_read_pixels` calls it halfway
        through a frame too: a batch still sitting in a Python list has not reached
        the GPU, so a pixel read that skipped this would report the previous frame.
        A frame normally passes through here once, so the counts it leaves behind
        are the frame's; a read that drew early reports what it drew.
        """
        self.last_triangles = sum(len(verts) // _FLOATS_PER_VERTEX
                                  for _, _, verts, _ in self._batches)
        self.last_batches = len(self._batches)
        glViewport(0, 0, self.fb_width, self.fb_height)
        glUseProgram(self._prog)
        glUniformMatrix4fv(self._u_proj, 1, True, self._proj)
        glUniform1i(self._u_tex, 0)
        glActiveTexture(GL_TEXTURE0)
        glEnable(GL_BLEND)
        # separate alpha, so a half-covered edge keeps its coverage instead of
        # mixing it into the colour - the difference between a crisp glyph and a
        # dark halo around it
        glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA,
                            GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        glBindVertexArray(self._vao)
        for tex, mode, verts, clip in self._batches:
            self._set_scissor(clip)
            glBindTexture(GL_TEXTURE_2D, tex or self._white)
            glUniform1i(self._u_round, mode)
            glBindBuffer(GL_ARRAY_BUFFER, self._vbo)
            glBufferData(GL_ARRAY_BUFFER, verts.nbytes, verts, GL_DYNAMIC_DRAW)
            glDrawArrays(GL_TRIANGLES, 0, len(verts) // _FLOATS_PER_VERTEX)
        glDisable(GL_SCISSOR_TEST)
        glBindVertexArray(0)
        self._batches = []

    def _set_scissor(self, clip: Any) -> None:
        """Cut the framebuffer to the rect an entire batch was drawn inside."""
        if clip is None:
            glDisable(GL_SCISSOR_TEST)
            return
        x, y, width, height = clip
        glEnable(GL_SCISSOR_TEST)
        # GL's y grows upwards and the rect's top is the far edge, hence the flip
        glScissor(int(math.floor(x * self.dpr)),
                  int(math.floor(self.fb_height - (y + height) * self.dpr)),
                  max(0, int(math.ceil(width * self.dpr))),
                  max(0, int(math.ceil(height * self.dpr))))

    # -- emitting an op ---------------------------------------------------
    def emit(self, op: DrawOp) -> None:
        """Turn one finished op into vertices. Everything above this is pure maths."""
        params = op.params
        name = op.name
        if name == "clear":
            self._op_clear(params["color"])
        elif name == "rect":
            self._op_rect(params)
        elif name == "circle":
            self._op_circle(params)
        elif name == "quad":
            self._op_quad(params)
        elif name == "line":
            self._op_line(params)
        elif name == "arc":
            self._op_arc(params)
        elif name == "polyline":
            self._op_polyline(params)
        elif name == "polygon":
            self._op_polygon(params)
        elif name == "text":
            self._op_text(params)
        elif name == "atlas":
            self._op_atlas(params)
        else:
            raise RendererUnavailable(f"the GL renderer was handed '{name}', "
                                      f"which it does not know how to draw")

    def _op_clear(self, color: RGBA) -> None:
        glClearColor(color.red, color.green, color.blue, color.alpha)
        glClear(GL_COLOR_BUFFER_BIT)

    def _op_rect(self, params: Dict[str, Any]) -> None:
        color = params["color"]
        self._rounded_box(params["x"], params["y"], params["width"],
                          params["height"], params["radius"], color,
                          params["rotation"])

    def _op_circle(self, params: Dict[str, Any]) -> None:
        # the exact disc: an SDF box whose radius is half its side is a circle,
        # with no segments to sample and no dependence on how big it is
        x, y, width, height = circle_bounds(params["cx"], params["cy"],
                                            params["radius"])
        self._rounded_box(x, y, width, height, min(width, height) * 0.5,
                          params["color"], 0.0)

    def _rounded_box(self, x: float, y: float, width: float, height: float,
                     radius: float, color: RGBA, rotation: float) -> None:
        if width <= 0 or height <= 0:
            return
        corners = rect_corners(x, y, width, height, rotation)
        self._quad(corners, _FULL_UV, (width, height), radius, color,
                   self._white, DISTANCE)

    def _op_quad(self, params: Dict[str, Any]) -> None:
        _, _, width, height = polygon_bounds(params["points"])
        self._quad(params["points"], _FULL_UV, (width, height), 0.0,
                   params["color"], self._white, FLAT)

    def _op_line(self, params: Dict[str, Any]) -> None:
        color = params["color"]
        thickness = params["thickness"]
        second = params["thickness2"]
        p1 = (params["x1"], params["y1"])
        p2 = (params["x2"], params["y2"])
        if second is None or abs(second - thickness) < 0.5:
            self._capsule(p1, p2, thickness, color)
            return
        # a taper has no room for a capsule, so the body is a quad and each end
        # gets a disc of its own width to stand in for the cap
        corners = tapered_quad(p1[0], p1[1], p2[0], p2[1], thickness, second)
        self._quad(corners, _FULL_UV, (0.0, 0.0), 0.0, color,
                   self._white, FLAT)
        self._disc(p1[0], p1[1], thickness * 0.5, color)
        self._disc(p2[0], p2[1], second * 0.5, color)

    def _capsule(self, p1: Tuple[float, float], p2: Tuple[float, float],
                 thickness: float, color: RGBA) -> None:
        cx, cy, length, width, angle = capsule(p1[0], p1[1], p2[0], p2[1],
                                               thickness)
        if length <= 0 or width <= 0:
            return
        self._rounded_box(cx - length * 0.5, cy - width * 0.5, length, width,
                          width * 0.5, color, angle)

    def _disc(self, cx: float, cy: float, radius: float, color: RGBA) -> None:
        x, y, width, height = circle_bounds(cx, cy, radius)
        self._rounded_box(x, y, width, height, radius, color, 0.0)

    def _op_arc(self, params: Dict[str, Any]) -> None:
        color = params["color"]
        thickness = params["thickness"]
        radius = params["radius"]
        sweep = params["sweep"]
        if radius <= 0 or thickness <= 0 or sweep <= 0:
            return
        start = params["start"]
        points = arc_points(params["cx"], params["cy"], radius, start,
                            start + sweep)
        self._stroke(points, thickness, color,
                     round_caps=params["caps"] == "round")

    def _op_polyline(self, params: Dict[str, Any]) -> None:
        color = params["color"]
        thickness = params["thickness"]
        if thickness <= 0 or len(params["points"]) < 2:
            return
        # an open stroke is a path of round-ended capsules, which meet inside the
        # shape and so join without a notch; closing it adds the last segment
        self._stroke(list(params["points"]), thickness, color,
                     round_caps=True, closed=params["closed"])

    def _stroke(self, points: Sequence[Tuple[float, float]], thickness: float,
                color: RGBA, round_caps: bool, closed: bool = False) -> None:
        if round_caps:
            for p1, p2 in polyline_pairs(points, closed):
                self._capsule(p1, p2, thickness, color)
            return
        for p1, p2 in polyline_pairs(points, closed):
            corners = tapered_quad(p1[0], p1[1], p2[0], p2[1],
                                   thickness, thickness)
            self._quad(corners, _FULL_UV, (0.0, 0.0), 0.0, color,
                       self._white, FLAT)

    def _op_polygon(self, params: Dict[str, Any]) -> None:
        triangles = polygon_fan(params["points"])
        if not triangles:
            return
        self._begin_batch(self._white, FLAT)
        for a, b, c in triangles:
            for vertex, uv in ((a, _TRI_UV[0]), (b, _TRI_UV[1]),
                               (c, _TRI_UV[2])):
                self._vertex(vertex[0], vertex[1], uv, (0.0, 0.0), 0.0,
                             params["color"])

    def _op_text(self, params: Dict[str, Any]) -> None:
        text = params["text"]
        size = params["size"]
        if not text or size <= 0:
            return
        tex, width, height = self._text_texture(text, size)
        x, y = params["x"], params["y"]
        corners = ((x, y), (x + width, y), (x + width, y + height),
                   (x, y + height))
        self._quad(corners, _FULL_UV, (width, height), 0.0, params["color"],
                   tex, TEXTURED)

    def _op_atlas(self, params: Dict[str, Any]) -> None:
        handle = self._handle_for(params["texture"])
        if handle is None:
            return
        width, height = params["width"], params["height"]
        if width <= 0 or height <= 0:
            return
        u0, v0, u1, v1 = params["uv"]
        corners = rect_corners(params["x"], params["y"], width, height,
                               params["rotation"])
        uvs = ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
        self._quad(corners, uvs, (width, height), 0.0, params["color"],
                   handle, TEXTURED)

    # -- the vertex path --------------------------------------------------
    def _begin_batch(self, tex: Optional[int], mode: int) -> None:
        """One batch per texture, mode and clip: a state change is the cost.

        The clip belongs in the key because the scissor is set once per batch: a
        shape drawn inside a clip that carried on sharing a batch with the shapes
        drawn after it left would be cut, or left uncut, by whichever rect happened
        to be open when the batch finally flushed.
        """
        if self._pending and (tex != self._pending_tex
                              or mode != self._pending_mode
                              or self.clip != self._pending_clip):
            self._flush()
        self._pending_tex = tex
        self._pending_mode = mode
        self._pending_clip = self.clip

    def _vertex(self, x: float, y: float, uv: Tuple[float, float],
                size: Tuple[float, float], radius: float, color: RGBA) -> None:
        self._pending.extend((x, y, uv[0], uv[1], size[0], size[1], radius,
                              color.red, color.green, color.blue, color.alpha))

    def _quad(self, corners: Sequence[Tuple[float, float]],
              uvs: Sequence[Tuple[float, float]], size: Tuple[float, float],
              radius: float, color: RGBA, tex: Optional[int], mode: int) -> None:
        self._begin_batch(tex, mode)
        for index in (0, 1, 2, 0, 2, 3):
            px, py = corners[index]
            self._vertex(px, py, uvs[index], size, radius, color)

    def _flush(self) -> None:
        if not self._pending:
            return
        self._batches.append((self._pending_tex or self._white,
                              self._pending_mode,
                              np.asarray(self._pending, dtype=np.float32),
                              self._pending_clip))
        self._pending = []

    # -- textures ---------------------------------------------------------
    def on_texture(self, key: str, path: str) -> None:
        """Read one image file and put it on the GPU, once per key."""
        self._ensure_gl()
        try:
            image = Image.open(path).convert("RGBA")
        except (OSError, ValueError):
            self.missing.append(key)
            return
        width, height = image.size
        self._handles[key] = self._upload(key, np.ascontiguousarray(
            np.asarray(image, dtype=np.uint8)), linear=True)
        self._textures[key] = (float(width), float(height))

    def on_upload(self, key: str, array: Any) -> None:
        """Re-fill an existing texture from memory - a scope's ring, every frame.

        New pixels under an old key are the point of it, so the handle is reused.
        A *resize* is a different matter: a quad emitted earlier in this frame
        carries UV numbers that are fractions of the texture as it was at the time,
        so a texture that changes shape - a glyph atlas growing a row does exactly
        that - has to find everything already emitted on the screen first, in the
        order it was emitted, or those quads sample the wrong part of the picture
        until the frame is over. Same rule and same cost as a pixel read.
        """
        if array is None:
            return
        self._ensure_gl()
        arr = np.ascontiguousarray(array, dtype=np.uint8)
        if arr.ndim == 2:
            # one coverage plane: white showing through an alpha of its own value,
            # which is the shape a scope's ring is computed in
            arr = np.dstack([np.full(arr.shape, 255, np.uint8)] * 3 + [arr])
        if arr.ndim != 3 or arr.shape[2] < 4:
            self.missing.append(key)
            return
        if arr.shape[2] != 4:
            arr = arr[:, :, :4]
        held = self._sizes.get(key)
        if held is not None and held != (arr.shape[1], arr.shape[0]):
            if self._begun:
                self._flush()
                self._draw_batches()
        self._handles[key] = self._upload(key, arr, linear=False)

    def _upload(self, key: str, arr: np.ndarray, *, linear: bool) -> int:
        height, width = arr.shape[:2]
        handle = self._handles.get(key) or glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, handle)
        nearest = GL_NEAREST if not linear else GL_LINEAR
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, nearest)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, nearest)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, arr.tobytes())
        self._textures[key] = (float(width), float(height))
        self._sizes[key] = (width, height)
        return handle

    def on_invalidate(self, key: str) -> None:
        # a batch still to be drawn binds this handle, so it goes on the screen
        # before the handle does rather than after
        if self._begun and self._ready and self._handles.get(key):
            self._flush()
            self._draw_batches()
        self._sizes.pop(key, None)
        handle = self._handles.pop(key, None)
        if handle:
            glDeleteTextures(1, [handle])

    def _handle_for(self, key: Any) -> Optional[int]:
        """The GL handle for a texture key, or ``None`` when there is nothing.

        A key the renderer does not know draws nothing rather than raising: a
        missing glyph is a project's own mistake to see, and the plan is explicit
        that the picture must not be lost over it. :attr:`missing` keeps the list
        so the widget layer can say it once instead of every frame.
        """
        if not key:
            return None
        if isinstance(key, int):  # a handle a caller already owns
            return key
        text = str(key)
        handle = self._handles.get(text)
        if handle is None and os.path.isfile(text):
            # a path handed straight to a draw, without a texture() call first
            self.texture(text)
            handle = self._handles.get(texture_key(text))
        if handle is None:
            if text not in self.missing:
                self.missing.append(text)
            return None
        return handle

    # -- pixels -----------------------------------------------------------
    def on_read_pixels(self, x: float, y: float, width: float, height: float,
                       premultiplied: bool) -> Any:
        self._ensure_gl()
        # draw what has been asked for already, or the answer would be about the
        # frame before this one
        self._flush()
        self._draw_batches()
        glPixelStorei(GL_PACK_ALIGNMENT, 1)
        left, top = int(x * self.dpr), int(y * self.dpr)
        right, bottom = int((x + width) * self.dpr), int((y + height) * self.dpr)
        right = min(right, self.fb_width)
        bottom = min(bottom, self.fb_height)
        columns, rows = max(right - left, 0), max(bottom - top, 0)
        if not columns or not rows:
            return np.zeros((0, 0, 4), dtype=np.uint8)
        data = glReadPixels(left, self.fb_height - bottom, columns, rows,
                            GL_RGBA, GL_UNSIGNED_BYTE)
        arr = np.frombuffer(data, dtype=np.uint8).reshape((rows, columns, 4))
        arr = arr[::-1, :, :]
        if premultiplied and arr.size:
            alpha = arr[:, :, 3:4].astype(np.float32) / 255.0
            rgb = arr[:, :, :3].astype(np.float32)
            np.divide(rgb, alpha, out=rgb, where=alpha > 1e-4)
            arr = np.concatenate(
                [np.clip(rgb, 0.0, 255.0).astype(np.uint8), arr[:, :, 3:4]],
                axis=2)
        return np.ascontiguousarray(arr)

    # -- text -------------------------------------------------------------
    def _font(self, size: float) -> Any:
        """The face for a size, chosen once and kept: a bake is not per frame."""
        whole = max(1, int(round(size)))
        if whole in self._fonts:
            return self._fonts[whole]
        font = None
        for candidate in FONT_CANDIDATES:
            try:
                font = ImageFont.truetype(candidate, whole)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()
        self._fonts[whole] = font
        return font

    def measure(self, string: str, size: float) -> Tuple[float, float]:
        """Real metrics once a face can be loaded, so a centred label is centred."""
        if not string or size <= 0:
            return (0.0, 0.0)
        _, width, height = self._text_texture(string, size)
        return (width, height)

    def _text_texture(self, string: str, size: float) -> Tuple[int, float, float]:
        key = (string, round(float(size), 3))
        cached = self._text.get(key)
        if cached is not None:
            return cached
        self._ensure_gl()
        font = self._font(size)
        bbox = font.getbbox(string)
        width = max(1, bbox[2] - bbox[0])
        height = max(1, bbox[3] - bbox[1])
        image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        ImageDraw.Draw(image).text((-bbox[0], -bbox[1]), string, font=font,
                                   fill=(255, 255, 255, 255))
        handle = self._upload(f"text:{string}:{size}", np.ascontiguousarray(
            np.asarray(image, dtype=np.uint8)), linear=True)
        found = (handle, float(width), float(height))
        self._text[key] = found
        return found

    # -- the report -------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        report = super().stats()
        report.update({
            "batches": self.last_batches,
            "triangles": self.last_triangles,
            "text_bakes": len(self._text),
            "missing_textures": len(self.missing),
            "gl": self._ready,
        })
        return report

    def close(self) -> None:
        """Let go of the GPU memory. GLFW tears the context down around us."""
        if not self._ready:
            return
        for handle in self._handles.values():
            glDeleteTextures(1, [handle])
        self._handles.clear()
        self._sizes.clear()
        self._text.clear()
        if self._vbo:
            glDeleteBuffers(1, [self._vbo])
        if self._vao:
            glDeleteVertexArrays(1, [self._vao])
        self._vbo = self._vao = 0
        self._ready = False


__all__ = ["GLRenderer", "RendererUnavailable", "ortho", "TEXTURED", "DISTANCE",
           "FLAT", "FONT_CANDIDATES", "gl_available"]


def gl_available() -> bool:
    """Could this machine make a GL context at all? Never raises.

    A question about the libraries, not about the screen: it is what
    :mod:`crude.commands.project` asks before it tries to open a window, so a
    run can fall back to the console without a traceback in the middle of a load.
    """
    try:
        import OpenGL.GL  # noqa: F401
        return True
    except Exception:  # missing, broken, or a build that will not import
        return False
