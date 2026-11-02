"""Batched 2D renderer. SDF-rounded rectangles, quads, capsule lines, text."""
from __future__ import annotations
import ctypes
import math
import os
import numpy as np
from OpenGL.GL import *
from PIL import Image, ImageDraw, ImageFont

_VERT = """
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

_FRAG = """
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

_STRIDE = 11 * 4
ClipRect = tuple[float, float, float, float]


def _ortho(l, r, b, t, n, f):
    return np.array([
        [2/(r-l), 0,       0,       -(r+l)/(r-l)],
        [0,       2/(t-b), 0,       -(t+b)/(t-b)],
        [0,       0,      -2/(f-n), -(f+n)/(f-n)],
        [0,       0,       0,        1          ],
    ], dtype=np.float32)


class Renderer2D:
    def __init__(self) -> None:
        self._prog = self._build_program(_VERT, _FRAG)
        self._u_proj  = glGetUniformLocation(self._prog, "u_proj")
        self._u_tex   = glGetUniformLocation(self._prog, "u_tex")
        self._u_round = glGetUniformLocation(self._prog, "u_round")

        self._vao = glGenVertexArrays(1)
        self._vbo = glGenBuffers(1)
        self._bind_vertex_layout()

        self._white_tex = self._make_solid_texture()
        self._camera_tex = None
        self._camera_size = (0, 0)

        self._text_cache = {}
        self._font_cache = {}
        self._texture_cache: dict[str, tuple[int, int, int]] = {}
        self._dyn_textures: dict[str, int] = {}

        self._clip_stack: list[ClipRect] = []
        self._batches: list[tuple[int, int, np.ndarray, ClipRect | None]] = []
        self._pending_tex = None
        self._pending_round = 1
        self._pending: list[float] = []

        # Offscreen target: an FBO the snapshot passes draw into so they
        # can keep a real alpha channel instead of the window's backdrop.
        self._off_tex = 0
        self._off_fbo = 0
        self._off_size = (0, 0)
        self._off_prev: tuple | None = None

        self.width = 0
        self.height = 0
        self.fb_width = 0
        self.fb_height = 0
        self._sx = 1.0
        self._sy = 1.0
        self._proj = np.eye(4, dtype=np.float32)

    # --- GL setup
    @staticmethod
    def _compile(src, kind):
        sh = glCreateShader(kind)
        glShaderSource(sh, src)
        glCompileShader(sh)
        if not glGetShaderiv(sh, GL_COMPILE_STATUS):
            raise RuntimeError(glGetShaderInfoLog(sh).decode())
        return sh

    @classmethod
    def _build_program(cls, vs, fs):
        prog = glCreateProgram()
        glAttachShader(prog, cls._compile(vs, GL_VERTEX_SHADER))
        glAttachShader(prog, cls._compile(fs, GL_FRAGMENT_SHADER))
        glLinkProgram(prog)
        if not glGetProgramiv(prog, GL_LINK_STATUS):
            raise RuntimeError(glGetProgramInfoLog(prog).decode())
        return prog

    def _bind_vertex_layout(self):
        glBindVertexArray(self._vao)
        glBindBuffer(GL_ARRAY_BUFFER, self._vbo)
        for idx, (size, offset) in enumerate(
            [(2, 0), (2, 8), (2, 16), (1, 24), (4, 28)]
        ):
            glEnableVertexAttribArray(idx)
            glVertexAttribPointer(idx, size, GL_FLOAT, GL_FALSE,
                                  _STRIDE, ctypes.c_void_p(offset))
        glBindVertexArray(0)

    def _make_solid_texture(self):
        tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, tex)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 1, 1, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, bytes([255, 255, 255, 255]))
        return tex

    # --- frame
    def begin(self, width, height, fb_width=None, fb_height=None):
        self.width = max(1, width)
        self.height = max(1, height)
        self.fb_width = fb_width if fb_width is not None else self.width
        self.fb_height = fb_height if fb_height is not None else self.height
        self._sx = self.fb_width / self.width
        self._sy = self.fb_height / self.height
        self._proj = _ortho(0, self.width, self.height, 0, -1, 1)
        self._batches.clear()
        self._clip_stack.clear()
        self._pending_tex = None
        self._pending = []

    def end(self):
        self._flush()
        glViewport(0, 0, self.fb_width, self.fb_height)
        glUseProgram(self._prog)
        glUniformMatrix4fv(self._u_proj, 1, True, self._proj)
        glUniform1i(self._u_tex, 0)
        glActiveTexture(GL_TEXTURE0)
        glEnable(GL_BLEND)
        # Separate alpha: a draw into a cleared offscreen target then keeps
        # a correct coverage alpha instead of losing it to the colour mix.
        glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA,
                            GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        glBindVertexArray(self._vao)
        for tex, round_mode, verts, clip in self._batches:
            if clip is not None:
                x, y, w, h = clip
                glEnable(GL_SCISSOR_TEST)
                glScissor(int(x * self._sx),
                          int(self.fb_height - (y + h) * self._sy),
                          int(w * self._sx),
                          int(h * self._sy))
            else:
                glDisable(GL_SCISSOR_TEST)
            glBindTexture(GL_TEXTURE_2D, tex)
            glUniform1i(self._u_round, round_mode)
            glBindBuffer(GL_ARRAY_BUFFER, self._vbo)
            glBufferData(GL_ARRAY_BUFFER, verts.nbytes, verts, GL_DYNAMIC_DRAW)
            glDrawArrays(GL_TRIANGLES, 0, len(verts) // 11)
        glDisable(GL_SCISSOR_TEST)
        glBindVertexArray(0)

    # --- offscreen target
    def begin_offscreen(self, w, h, clear=(0.0, 0.0, 0.0, 0.0)):
        """Point drawing at an ``w × h`` texture with an alpha channel.

        The window framebuffer is remembered so :meth:`end_offscreen` can
        give it back. ``fb_width``/``fb_height`` stay at the offscreen
        size afterwards on purpose: :meth:`read_pixels` flips against
        them, and the next :meth:`begin` restores the window's.
        """
        w = max(1, int(w))
        h = max(1, int(h))
        if self._off_prev is None:
            self._off_prev = (self.fb_width, self.fb_height,
                              self.width, self.height)
        if self._off_tex == 0 or self._off_size != (w, h):
            self._release_offscreen()
            self._off_tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self._off_tex)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA,
                         GL_UNSIGNED_BYTE, None)
            self._off_fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, self._off_fbo)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, self._off_tex, 0)
            self._off_size = (w, h)
        glBindFramebuffer(GL_FRAMEBUFFER, self._off_fbo)
        glViewport(0, 0, w, h)
        glClearColor(float(clear[0]), float(clear[1]),
                     float(clear[2]), float(clear[3]))
        glClear(GL_COLOR_BUFFER_BIT)
        self.begin(w, h, w, h)

    def end_offscreen(self, read=True):
        """Flush the batch into the texture and go back to the window.

        Returns the target's pixels as a top-down RGBA array when ``read``
        is true. The read has to happen here: once the target is unbound
        ``read_pixels`` would be reading the window instead.
        """
        self.end()
        arr = None
        if read:
            w, h = self._off_size
            arr = self.read_pixels(0, 0, w, h, premultiplied=True)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        prev = self._off_prev or (self.fb_width, self.fb_height, 0, 0)
        glViewport(0, 0, prev[0], prev[1])
        self._off_prev = None
        return arr

    def _release_offscreen(self):
        if self._off_fbo:
            glDeleteFramebuffers(1, [self._off_fbo])
            self._off_fbo = 0
        if self._off_tex:
            glDeleteTextures(1, [self._off_tex])
            self._off_tex = 0
        self._off_size = (0, 0)

    def push_clip(self, x, y, w, h):
        self._flush()
        self._clip_stack.append((x, y, w, h))

    def pop_clip(self):
        self._flush()
        if self._clip_stack:
            self._clip_stack.pop()

    # --- primitives
    def rect(self, x, y, w, h, radius, color, rotation=0.0):
        if w <= 0 or h <= 0:
            return
        self._switch_batch(self._white_tex, 1)
        cx, cy = x + w * 0.5, y + h * 0.5
        ca, sa = math.cos(rotation), math.sin(rotation)
        local = [(-w/2, -h/2), (w/2, -h/2), (w/2, h/2), (-w/2, h/2)]
        corners = [(cx + lx * ca - ly * sa, cy + lx * sa + ly * ca)
                   for lx, ly in local]
        uv = [(0, 0), (1, 0), (1, 1), (0, 1)]
        r, g, b, a = color
        for i in (0, 1, 2, 0, 2, 3):
            px, py = corners[i]
            u, v = uv[i]
            self._pending.extend([px, py, u, v, w, h, radius, r, g, b, a])

    def quad(self, p1, p2, p3, p4, color):
        self._switch_batch(self._white_tex, 2)
        r, g, b, a = color
        order = [p1, p2, p3, p1, p3, p4]
        uv = [(0, 0), (1, 0), (1, 1), (0, 1)]
        for i, (px, py) in enumerate(order):
            u, v = uv[i % 4]
            self._pending.extend([px, py, u, v, 0.0, 0.0, 0.0, r, g, b, a])

    def line(self, p1, p2, thickness, color, thickness2=None):
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = math.hypot(dx, dy)
        if length < 0.5:
            return
        angle = math.atan2(dy, dx)
        cx = (p1[0] + p2[0]) * 0.5
        cy = (p1[1] + p2[1]) * 0.5
        if thickness2 is None or abs(thickness2 - thickness) < 0.5:
            self.rect(cx - length * 0.5, cy - thickness * 0.5,
                      length, thickness, thickness * 0.5, color, rotation=angle)
            return
        nx = -math.sin(angle)
        ny =  math.cos(angle)
        hw1 = thickness  * 0.5
        hw2 = thickness2 * 0.5
        p1a = (p1[0] + nx * hw1, p1[1] + ny * hw1)
        p1b = (p1[0] - nx * hw1, p1[1] - ny * hw1)
        p2a = (p2[0] + nx * hw2, p2[1] + ny * hw2)
        p2b = (p2[0] - nx * hw2, p2[1] - ny * hw2)
        self.quad(p1a, p2a, p2b, p1b, color)
        self.rect(p1[0] - hw1, p1[1] - hw1, hw1*2, hw1*2, hw1, color)
        self.rect(p2[0] - hw2, p2[1] - hw2, hw2*2, hw2*2, hw2, color)

    def textured_quad(self, x, y, w, h, texture=None):
        tex = texture if texture is not None else self._camera_tex
        if tex is None or w <= 0 or h <= 0:
            return
        self._switch_batch(tex, 0)
        corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        uv = [(0, 0), (1, 0), (1, 1), (0, 1)]
        for i in (0, 1, 2, 0, 2, 3):
            px, py = corners[i]
            u, v = uv[i]
            self._pending.extend([px, py, u, v, w, h, 0.0, 1.0, 1.0, 1.0, 1.0])

    def text(self, x, y, s, size, color):
        if not s:
            return
        tex, tw, th = self._text_texture(s, size)
        self._switch_batch(tex, 0)
        corners = [(x, y), (x + tw, y), (x + tw, y + th), (x, y + th)]
        uv = [(0, 0), (1, 0), (1, 1), (0, 1)]
        r, g, b, a = color
        for i in (0, 1, 2, 0, 2, 3):
            px, py = corners[i]
            u, v = uv[i]
            self._pending.extend([px, py, u, v, tw, th, 0.0, r, g, b, a])

    def upload_frame(self, bgr, mirror=False):
        import cv2
        if mirror:
            bgr = cv2.flip(bgr, 1)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        if self._camera_tex is None:
            self._camera_tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self._camera_tex)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glBindTexture(GL_TEXTURE_2D, self._camera_tex)
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0, GL_RGB,
                     GL_UNSIGNED_BYTE, rgb.tobytes())
        self._camera_size = (w, h)

    def camera_size(self):
        return self._camera_size

    # --- PNG texture cache
    def load_texture(self, path: str) -> tuple[int, int, int] | None:
        """Returns (gl_handle, width, height) or None on failure.

        The cache key is the absolute path plus the file's mtime, so a
        snapshot written over an existing PNG (a re-captured pose
        thumbnail) is picked up instead of serving the old image.
        """
        abs_path = os.path.abspath(path)
        try:
            stamp = os.stat(abs_path).st_mtime_ns
        except OSError:
            return None
        key = (abs_path, stamp)
        cached = self._texture_cache.get(key)
        if cached is not None:
            return cached
        # The file changed under us: drop the stale entry's texture.
        for old_key in [k for k in self._texture_cache
                        if isinstance(k, tuple) and k[0] == abs_path]:
            self._texture_cache.pop(old_key, None)
        try:
            img = Image.open(abs_path).convert("RGBA")
        except (OSError, ValueError):
            return None
        w, h = img.size
        tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, tex)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, img.tobytes())
        result = (tex, w, h)
        self._texture_cache[key] = result
        return result

    def invalidate_texture(self, path: str) -> None:
        """Forget every cached upload of ``path`` (after overwriting it)."""
        abs_path = os.path.abspath(path)
        for key in [k for k in self._texture_cache
                    if (k[0] if isinstance(k, tuple) else k) == abs_path]:
            self._texture_cache.pop(key, None)

    def upload_texture(self, key: str, arr) -> tuple[int, int, int] | None:
        """Upload an RGBA numpy array as a texture keyed by ``key``.

        Unlike :meth:`load_texture` this is for data that is not a file on
        disk — a selection mask, for instance. The same GL texture is
        reused for a key, so a per-frame caller only pays the upload. The
        array's first row is its top row, matching ``textured_quad``'s
        uv layout.
        """
        if arr is None:
            return None
        arr = np.ascontiguousarray(arr, dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[2] != 4:
            return None
        h, w = arr.shape[:2]
        if w <= 0 or h <= 0:
            return None
        tex = self._dyn_textures.get(key)
        if tex is None:
            tex = glGenTextures(1)
            self._dyn_textures[key] = tex
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glBindTexture(GL_TEXTURE_2D, tex)
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, arr.tobytes())
        return tex, w, h

    def read_pixels(self, x: int, y: int, w: int, h: int,
                    premultiplied: bool = False) -> np.ndarray:
        """Read an RGBA region from the framebuffer. Y is top-left.

        ``premultiplied`` divides the colour back by the coverage, for a
        read of a transparent offscreen target: the blend that produced
        it mixed the colour with nothing, so a half-covered edge pixel
        would otherwise come back too dark.
        """
        glPixelStorei(GL_PACK_ALIGNMENT, 1)
        data = glReadPixels(int(x), int(self.fb_height - (y + h)),
                            int(w), int(h), GL_RGBA, GL_UNSIGNED_BYTE)
        arr = np.frombuffer(data, dtype=np.uint8).reshape((h, w, 4))
        arr = arr[::-1, :, :]
        if premultiplied and arr.size:
            alpha = arr[:, :, 3:4].astype(np.float32) / 255.0
            rgb = arr[:, :, :3].astype(np.float32)
            np.divide(rgb, alpha, out=rgb, where=alpha > 1e-4)
            arr = np.concatenate(
                [np.clip(rgb, 0.0, 255.0).astype(np.uint8),
                 arr[:, :, 3:4]], axis=2)
        return arr

    # --- text bakes
    def _font(self, size):
        if size in self._font_cache:
            return self._font_cache[size]
        candidates = ["arial.ttf", "DejaVuSans.ttf",
                      "/System/Library/Fonts/Helvetica.ttc",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
        font = None
        for c in candidates:
            try:
                font = ImageFont.truetype(c, size)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()
        self._font_cache[size] = font
        return font

    def _text_texture(self, s, size):
        key = (s, size)
        if key in self._text_cache:
            return self._text_cache[key]
        font = self._font(size)
        bbox = font.getbbox(s)
        w = max(1, bbox[2] - bbox[0])
        h = max(1, bbox[3] - bbox[1])
        img = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        ImageDraw.Draw(img).text((-bbox[0], -bbox[1]), s, font=font,
                                 fill=(255, 255, 255, 255))
        tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, tex)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, img.tobytes())
        self._text_cache[key] = (tex, w, h)
        return self._text_cache[key]

    def _switch_batch(self, tex, round_mode):
        if self._pending and (tex != self._pending_tex or round_mode != self._pending_round):
            self._flush()
        self._pending_tex = tex
        self._pending_round = round_mode

    def _flush(self):
        if not self._pending:
            return
        verts = np.asarray(self._pending, dtype=np.float32)
        clip = self._clip_stack[-1] if self._clip_stack else None
        self._batches.append((self._pending_tex or self._white_tex,
                              self._pending_round, verts, clip))
        self._pending = []