"""Immediate-ish widgets; App routes GLFW events into them."""
from __future__ import annotations
import math
import time
from typing import Callable, Optional
from ui.renderer import Renderer2D

Color = tuple[float, float, float, float]


class Widget:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        self._scroll_offset = 0.0
        self._render_dx = 0.0
        self._render_dy = 0.0

    def contains(self, mx, my):
        sx = self.x + self._render_dx
        sy = self.y + self._render_dy
        return sx <= mx <= sx + self.w and sy <= my <= sy + self.h

    def draw(self, r): ...
    def on_press(self, mx, my): ...
    def on_release(self, mx, my): ...
    def on_drag(self, mx, my): ...

    def scroll_height(self): return self.y + self.h
    def translate(self, dy): self.y += dy


class Label(Widget):
    def __init__(self, x, y, text, size=14, color=(0.85, 0.90, 1.0, 1.0)):
        super().__init__(x, y, 0, 0)
        self.text = text
        self.size = size
        self.color = color

    def draw(self, r):
        r.text(self.x + self._render_dx, self.y + self._render_dy,
               self.text, self.size, self.color)


class EditableValue:
    """Mixin: a slider whose number can be typed instead of dragged.

    The knob still drags as before; the value box on the right is a tiny
    text field. Double-click it (or the bar) to type a number. Enter or a
    click anywhere else commits, Escape cancels — App routes characters
    here through the same focus path it uses for :class:`TextInput`.
    """

    text_editable = True
    VALUE_W = 52.0
    VALUE_H = 18.0
    DOUBLE_CLICK_S = 0.4

    def _init_edit_state(self, show_value):
        self.show_value = bool(show_value)
        self.active = False
        self._buf = ""
        self._last_click = (0.0, -1e9, -1e9)
        # Ctrl-drag is fine-tune: the value follows the pointer at a tenth
        # of the coarse rate, anchored where the press started.
        self.fine = False
        self._press_anchor = float(self.value)
        # Snap granularity, derived from the range so a drag lands on a
        # readable number. 0 disables snapping.
        self.step = self._default_step()

    def _default_step(self) -> float:
        """Finest power-of-ten step a bar can usefully resolve."""
        span = float(self.max_val) - float(self.min_val)
        if span <= 0.0:
            return 0.0
        # ~200 scrub positions across the bar; rounding down a power of
        # ten keeps the snap invisible except right at zero.
        return 10.0 ** math.floor(math.log10(span / 200.0))

    # ---- geometry
    def _bar_geom(self):
        """(left, centre_y, length) of the slider bar."""
        return (self.x + self._render_dx,
                self.y + self._render_dy + self.h * 0.5,
                self._bar_w())

    def _bar_w(self):
        if not self.show_value:
            return self.w
        # The readout lives inside the widget footprint so no panel has to
        # reserve extra width for it.
        return max(40.0, self.w - self.VALUE_W - 6.0)

    def _value_rect(self):
        if not self.show_value:
            return None
        return (self.x + self._render_dx + self.w - self.VALUE_W,
                self.y + self._render_dy + (self.h - self.VALUE_H) * 0.5,
                self.VALUE_W, self.VALUE_H)

    def value_hit(self, mx, my):
        rc = self._value_rect()
        if rc is None:
            return False
        bx, by, bw, bh = rc
        return bx <= mx <= bx + bw and by <= my <= by + bh

    def bar_hit(self, mx, my):
        bx, cy, bw = self._bar_geom()
        return bx <= mx <= bx + bw and cy - 10 <= my <= cy + 10

    # ---- editing
    def begin_edit(self):
        if self.active or not self.show_value:
            return
        self.active = True
        self._buf = self._format_value()

    def cancel(self):
        self.active = False
        self._buf = ""

    def submit(self):
        """Commit the typed number; a bad buffer leaves the value alone."""
        if not self.active:
            return
        self.active = False
        text = self._buf.strip().replace(",", ".")
        self._buf = ""
        if not text:
            return
        try:
            value = float(text)
        except ValueError:
            return
        self.set_value(value)

    def on_char(self, ch):
        if not self.active:
            return
        if ch in "0123456789.-+":
            self._buf += ch

    def on_backspace(self):
        if self.active:
            self._buf = self._buf[:-1]

    def set_value(self, value):
        value = max(float(self.min_val), min(float(self.max_val), float(value)))
        self.value = value
        if self.on_change:
            self.on_change(value)

    def _register_click(self, mx, my):
        now = time.monotonic()
        t, lx, ly = self._last_click
        self._last_click = (now, mx, my)
        return (now - t <= self.DOUBLE_CLICK_S
                and abs(mx - lx) <= 6 and abs(my - ly) <= 6)

    def _press(self, mx, my):
        """Shared press handling. Returns True when the click was used up."""
        # Fine-tune is relative to where the press began, so it is also
        # the value a Ctrl drag starts from.
        self._press_anchor = float(self.value)
        double = self._register_click(mx, my)
        if self.value_hit(mx, my):
            if double:
                self.begin_edit()
            return True
        if double and self.bar_hit(mx, my):
            self.begin_edit()
            return True
        return False

    # ---- drawing
    def _draw_value(self, r):
        rc = self._value_rect()
        if rc is None:
            return
        bx, by, bw, bh = rc
        bg = (0.25, 0.30, 0.38, 1.0) if self.active else (0.17, 0.19, 0.23, 1.0)
        r.rect(bx, by, bw, bh, 4, bg)
        if self.active:
            r.text(bx + 5, by + 4, self._buf, 12, (0.96, 0.98, 1.0, 1.0))
            cw = len(self._buf) * 7
            r.rect(bx + 5 + cw + 1, by + 3, 1, bh - 6, 0,
                   (0.95, 0.97, 1.0, 1.0))
        else:
            r.text(bx + 5, by + 4, self._format_value(), 12,
                   (0.78, 0.85, 0.96, 1.0))

    def _draw_bar(self, r, knob: float = 8.0):
        bx, cy, bw = self._bar_geom()
        r.rect(bx, cy - 2, bw, 4, 2, (0.20, 0.22, 0.26, 1.0))
        span = max(1e-6, (self.max_val - self.min_val))
        t = (self.value - self.min_val) / span
        r.rect(bx, cy - 2, bw * t, 4, 2, (0.28, 0.68, 1.00, 1.0))
        r.rect(bx + bw * t - knob, cy - knob - 1, knob * 2, knob * 2 + 2,
               knob, (0.95, 0.97, 1.00, 1.0))

    def _set_from_x(self, mx):
        bx, _cy, bw = self._bar_geom()
        t = max(0.0, min(1.0, (mx - bx) / max(1.0, bw)))
        target = self.min_val + t * (self.max_val - self.min_val)
        if self.fine:
            # A tenth of the coarse rate, so small offsets are reachable
            # by hand instead of only by typing the number in.
            target = self._press_anchor + (target - self._press_anchor) * 0.1
        self.set_value(self._quantize(target))

    def _quantize(self, value) -> float:
        """Clamp into range, snap to the step and normalise zero."""
        value = max(float(self.min_val), min(float(self.max_val),
                                            float(value)))
        step = float(getattr(self, "step", 0.0) or 0.0)
        if step > 0.0:
            value = round(value / step) * step
            value = max(float(self.min_val), min(float(self.max_val), value))
            if abs(value) < step * 0.5:
                value = 0.0
        elif abs(value) < 1e-9:
            value = 0.0
        return value

    @staticmethod
    def _format_number(value, fmt="{:.2f}") -> str:
        """Format ``value``, never showing a negative zero."""
        text = fmt.format(0.0 if abs(float(value)) < 1e-12 else value)
        if text.startswith("-") and text[1:].strip("0.°sx×% ") == "":
            text = text[1:]
        return text

    def _format_value(self):
        return self._format_number(self.value)


class Slider(EditableValue, Widget):
    def __init__(self, x, y, w, h, value, min_val, max_val,
                 on_change=None, label="", show_value=True):
        super().__init__(x, y, w, h)
        self.value, self.min_val, self.max_val = value, min_val, max_val
        self.on_change = on_change
        self.label = label
        self._dragging = False
        self._init_edit_state(show_value)

    def on_press(self, mx, my):
        if self._press(mx, my):
            return
        self._dragging = True
        self._set_from_x(mx)

    def on_release(self, mx, my): self._dragging = False

    def on_drag(self, mx, my):
        if self._dragging:
            self._set_from_x(mx)

    def draw(self, r):
        self._draw_bar(r)
        self._draw_value(r)
        if self.label and not self.show_value:
            bx, cy, _bw = self._bar_geom()
            r.text(bx + self.w + 10, cy - 8,
                   f"{self.label} {self.value:.2f}", 12,
                   (0.75, 0.82, 0.95, 1.0))


class NumericSlider(EditableValue, Widget):
    def __init__(self, x, y, w, h, value, min_val, max_val,
                 name, on_change=None, fmt="{:.2f}", show_value=True):
        super().__init__(x, y, w, h)
        self.value, self.min_val, self.max_val = value, min_val, max_val
        self.name, self.on_change, self.fmt = name, on_change, fmt
        self._dragging = False
        self._init_edit_state(show_value)

    def _format_value(self):
        return self._format_number(self.value, self.fmt)

    def _bar_geom(self):
        return (self.x + self._render_dx,
                self.y + self._render_dy + 18 + 8,
                self._bar_w())

    def _value_rect(self):
        if not self.show_value:
            return None
        bx, cy, _bw = self._bar_geom()
        return (self.x + self._render_dx + self.w - self.VALUE_W,
                cy - self.VALUE_H * 0.5, self.VALUE_W, self.VALUE_H)

    def on_press(self, mx, my):
        if self._press(mx, my):
            return
        self._dragging = True
        self._set_from_x(mx)

    def on_release(self, mx, my): self._dragging = False

    def on_drag(self, mx, my):
        if self._dragging:
            self._set_from_x(mx)

    def draw(self, r):
        bx, by, _bw = self._bar_geom()
        r.text(bx, by - 26, self.name, 12, (0.82, 0.88, 1.0, 1.0))
        self._draw_bar(r, knob=7.0)
        self._draw_value(r)


class Toggle(Widget):
    def __init__(self, x, y, w, h, value, label="", on_change=None):
        super().__init__(x, y, w, h)
        self.value = value
        self.label = label
        self.on_change = on_change

    def on_press(self, mx, my):
        self.value = not self.value
        if self.on_change:
            self.on_change(self.value)

    def draw(self, r):
        bx = self.x + self._render_dx
        by = self.y + self._render_dy
        track_w = 44.0
        bg = (0.28, 0.68, 1.00, 1.0) if self.value else (0.25, 0.28, 0.33, 1.0)
        r.rect(bx, by, track_w, self.h, self.h * 0.5, bg)
        knob_x = bx + (track_w - self.h) if self.value else bx
        r.rect(knob_x, by, self.h, self.h, self.h * 0.5, (0.95, 0.97, 1.00, 1.0))
        if self.label:
            r.text(bx + track_w + 12, by + self.h * 0.5 - 8,
                   self.label, 14, (0.85, 0.90, 1.0, 1.0))


class Button(Widget):
    def __init__(self, x, y, w, h, label, on_press):
        super().__init__(x, y, w, h)
        self.label = label
        self._cb = on_press
        self._hover = False

    def on_press(self, mx, my): self._cb()

    def draw(self, r):
        bx = self.x + self._render_dx
        by = self.y + self._render_dy
        bg = (0.30, 0.62, 0.95, 1.0) if self._hover else (0.22, 0.50, 0.82, 1.0)
        r.rect(bx, by, self.w, self.h, 8, bg)
        tw = len(self.label) * 7
        r.text(bx + (self.w - tw) * 0.5, by + self.h * 0.5 - 8,
               self.label, 14, (0.98, 0.99, 1.0, 1.0))


class ColorPicker(Widget):
    SLIDER_GAP = 26
    READOUT_H = 26
    HEX_H = 22
    HEX_W = 96.0

    def __init__(self, x, y, w, h, value_hex, on_change=None,
                 show_values=True, mode="hex"):
        total_h = self.SLIDER_GAP * 3 + self.READOUT_H
        super().__init__(x, y, w, total_h)
        self.on_change = on_change
        self.value_hex = value_hex or "#FF00FF"
        self.show_values = show_values
        self.mode = mode  # "hex" or "rgb"
        r, g, b = self._hex_to_rgb(self.value_hex)
        self._r, self._g, self._b = r, g, b

        # The picker draws its own channel readouts, so the sliders keep
        # their full bar width and no in-slider value box.
        self.sliders = [
            Slider(x, y + i * self.SLIDER_GAP, w, h, v, 0.0, 1.0,
                   on_change=lambda val, c=i: self._update(c, val),
                   show_value=False)
            for i, v in enumerate((r, g, b))
        ]
        self._mode_btn: Button | None = None
        # The HEX value is a real field one line below the channels, so a
        # code can be typed instead of dragged.
        self.hex_input = TextInput(
            x, y + self.SLIDER_GAP * 3 + 2, min(self.HEX_W, w), self.HEX_H,
            text=self.value_hex, placeholder="#RRGGBB",
            on_submit=self._submit_hex)

    @staticmethod
    def _hex_to_rgb(s):
        rgb = ColorPicker.parse_hex(s)
        return rgb if rgb is not None else (1.0, 0.0, 1.0)

    @staticmethod
    def parse_hex(text):
        """``#RRGGBB`` or ``RRGGBB`` → (r, g, b) floats, or None."""
        s = (text or "").strip().lstrip("#")
        if len(s) != 6:
            return None
        try:
            return (int(s[0:2], 16) / 255.0,
                    int(s[2:4], 16) / 255.0,
                    int(s[4:6], 16) / 255.0)
        except ValueError:
            return None

    def set_hex(self, text) -> bool:
        """Apply a typed colour. An invalid code reverts to the live one."""
        rgb = self.parse_hex(text)
        if rgb is None:
            self._sync_hex_field()
            return False
        self._apply_rgb(*rgb)
        return True

    def _submit_hex(self, text):
        self.set_hex(text)

    def _sync_hex_field(self):
        self.hex_input.text = self.value_hex
        self.hex_input.cursor = len(self.hex_input.text)

    def active_text_input(self):
        """The nested field that currently owns the keyboard, if any."""
        return self.hex_input if self.hex_input.active else None

    def _apply_rgb(self, r, g, b):
        self._r, self._g, self._b = r, g, b
        for s, v in zip(self.sliders, (r, g, b)):
            s.value = v
        self.value_hex = "#{:02X}{:02X}{:02X}".format(
            int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))
        self._sync_hex_field()
        if self.on_change:
            self.on_change(self.value_hex)

    def _update(self, channel, v):
        channels = [self._r, self._g, self._b]
        channels[channel] = v
        self._apply_rgb(*channels)

    def _toggle_mode(self):
        self.mode = "rgb" if self.mode == "hex" else "hex"

    def on_press(self, mx, my):
        if self.hex_input.contains(mx, my):
            self.hex_input.on_press(mx, my)
            return
        if self._mode_btn is not None:
            bx = self._mode_btn.x + self._render_dx
            by = self._mode_btn.y + self._render_dy
            if (bx <= mx <= bx + self._mode_btn.w
                    and by <= my <= by + self._mode_btn.h):
                self._toggle_mode()
                return
        for s in self.sliders:
            if s.contains(mx, my):
                s.on_press(mx, my)
                return

    def on_release(self, mx, my):
        for s in self.sliders:
            s.on_release(mx, my)

    def on_drag(self, mx, my):
        for s in self.sliders:
            s.on_drag(mx, my)

    def draw(self, r):
        for i, s in enumerate(self.sliders):
            s._render_dx = self._render_dx
            s._render_dy = self._render_dy
            s.draw(r)
            if self.show_values:
                rv = (self._r, self._g, self._b)[i]
                r.text(self.x + self.w + 10 + self._render_dx,
                       s.y + self._render_dy + 2,
                       f"{int(round(rv * 255)):3d}",
                       12, (0.85, 0.90, 1.0, 1.0))
        sw_x = self.x + self.w + 46 + self._render_dx
        sw_y = self.y + self._render_dy
        r.rect(sw_x, sw_y, 20, 20, 4, (self._r, self._g, self._b, 1.0))
        # RGB keeps its painted readout; the HEX value has its own field.
        label = ("" if self.mode == "hex" else
                 f"{int(round(self._r*255))},"
                 f"{int(round(self._g*255))},{int(round(self._b*255))}")
        if label:
            r.text(sw_x + 26, sw_y + 4, label, 12, (0.75, 0.82, 0.95, 1.0))

        # Mode toggle button
        bw = 34
        bx = sw_x + 26 + (len(label) * 7) + 8
        by = sw_y + 2
        self._mode_btn = Button(bx, by, bw, 20,
                                "HEX" if self.mode == "hex" else "RGB",
                                on_press=self._toggle_mode)
        self._mode_btn._render_dx = 0.0
        self._mode_btn._render_dy = 0.0
        self._mode_btn.draw(r)

        # HEX field one line below the sliders; it follows the panel scroll.
        self.hex_input.x = self.x
        self.hex_input.y = self.y + self.SLIDER_GAP * 3 + 2
        self.hex_input._render_dx = self._render_dx
        self.hex_input._render_dy = self._render_dy
        if not self.hex_input.active:
            self._sync_hex_field()
        self.hex_input.draw(r)


class TextInput(Widget):
    """Single-line text entry. Enable by clicking; consume `on_char` and
    `on_backspace` routed by App when this widget is active.

    `on_submit` fires on Enter or when focus is dropped (blur) and
    receives the committed text.
    """

    def __init__(self, x, y, w, h, text="", placeholder="",
                 on_change=None, on_submit=None):
        super().__init__(x, y, w, h)
        self.text = text
        self.placeholder = placeholder
        self.on_change = on_change
        self.on_submit = on_submit
        self.active = False
        self.cursor = len(text)

    def on_press(self, mx, my):
        self.active = True
        self.cursor = len(self.text)

    def on_release(self, mx, my):
        pass

    def on_drag(self, mx, my):
        pass

    def submit(self):
        """Commit and drop focus."""
        was_active = self.active
        self.active = False
        if was_active and self.on_submit:
            self.on_submit(self.text)

    def on_char(self, ch):
        if not self.active:
            return
        self.text = self.text[:self.cursor] + ch + self.text[self.cursor:]
        self.cursor += 1
        if self.on_change:
            self.on_change(self.text)

    def on_backspace(self):
        if not self.active or self.cursor == 0:
            return
        self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
        self.cursor -= 1
        if self.on_change:
            self.on_change(self.text)

    def draw(self, r):
        bx = self.x + self._render_dx
        by = self.y + self._render_dy
        bg = (0.25, 0.30, 0.38, 1.0) if self.active else (0.18, 0.20, 0.25, 1.0)
        r.rect(bx, by, self.w, self.h, 6, bg)
        display = self.text if self.text else self.placeholder
        color = (0.9, 0.95, 1.0, 1.0) if self.text else (0.55, 0.60, 0.70, 1.0)
        r.text(bx + 8, by + self.h * 0.5 - 8, display, 14, color)
        if self.active:
            cw = len(self.text[:self.cursor]) * 7
            r.rect(bx + 8 + cw + 1, by + 6, 1, self.h - 12, 0,
                   (0.95, 0.97, 1.0, 1.0))

class EnvelopeEditor(Widget):
    """Compact ADSR-style editor. Renders a peak graph above two sliders.

    `on_attack` / `on_release` are **value-change callbacks**, called with
    one float argument (milliseconds). They are distinct from the mouse
    event handlers `on_press` / `on_release` / `on_drag`, which `App`
    routes into. Do NOT overwrite the event handlers.
    """

    GRAPH_H = 36
    SLIDER_H = 24
    SLIDER_GAP = 30

    def __init__(self, x, y, w, duration_ms, attack_ms, release_ms,
                 on_attack=None, on_release=None,
                 atk_range=(10.0, 800.0), rel_range=(10.0, 800.0)):
        h = self.GRAPH_H + 8 + self.SLIDER_GAP * 2
        super().__init__(x, y, w, h)
        self.duration_ms = duration_ms
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self._on_attack_change = on_attack
        self._on_release_change = on_release
        self._atk_lo, self._atk_hi = atk_range
        self._rel_lo, self._rel_hi = rel_range
        self._dragging = None

    def _track_rects(self):
        sx = self.x
        sw = self.w
        sy1 = self.y + self.GRAPH_H + 8
        sy2 = sy1 + self.SLIDER_GAP
        return (sx, sy1, sw), (sx, sy2, sw)

    def _set_from_x(self, track, mx, lo, hi):
        x0, _y, w = track
        t = max(0.0, min(1.0, (mx - self._render_dx - x0) / max(1.0, w)))
        return lo + t * (hi - lo)

    def on_press(self, mx, my):
        (a_x, a_y, a_w), (r_x, r_y, r_w) = self._track_rects()
        ly = my - self._render_dy
        if a_y - 4 <= ly <= a_y + self.SLIDER_H + 4:
            self._dragging = "attack"
            self.attack_ms = self._set_from_x(
                (a_x, a_y, a_w), mx, self._atk_lo, self._atk_hi)
            if self._on_attack_change:
                self._on_attack_change(self.attack_ms)
        elif r_y - 4 <= ly <= r_y + self.SLIDER_H + 4:
            self._dragging = "release"
            self.release_ms = self._set_from_x(
                (r_x, r_y, r_w), mx, self._rel_lo, self._rel_hi)
            if self._on_release_change:
                self._on_release_change(self.release_ms)

    def on_release(self, mx, my):
        self._dragging = None

    def on_drag(self, mx, my):
        if self._dragging == "attack":
            (a_x, _a_y, a_w), _ = self._track_rects()
            self.attack_ms = self._set_from_x(
                (a_x, _a_y, a_w), mx, self._atk_lo, self._atk_hi)
            if self._on_attack_change:
                self._on_attack_change(self.attack_ms)
        elif self._dragging == "release":
            _, (r_x, r_y, r_w) = self._track_rects()
            self.release_ms = self._set_from_x(
                (r_x, r_y, r_w), mx, self._rel_lo, self._rel_hi)
            if self._on_release_change:
                self._on_release_change(self.release_ms)

    def draw(self, r):
        bx = self.x + self._render_dx
        by = self.y + self._render_dy
        gw, gh = self.w, self.GRAPH_H

        # Peak graph
        r.rect(bx, by, gw, gh, 4, (0.10, 0.12, 0.16, 1.0))

        atk_px = min(gw * 0.5, self.attack_ms / (self._atk_hi) * gw * 0.5)
        rel_px = min(gw * 0.5, self.release_ms / (self._rel_hi) * gw * 0.5)
        hold_px = max(0.0, gw - atk_px - rel_px)
        top_y = by + 4
        bot_y = by + gh - 4

        # Rise (attack)
        r.line((bx, bot_y), (bx + atk_px, top_y),
               2.0, (0.35, 0.75, 1.0, 1.0))
        # Hold (sustain flat)
        if hold_px > 0:
            r.line((bx + atk_px, top_y), (bx + atk_px + hold_px, top_y),
                   2.0, (0.35, 0.75, 1.0, 1.0))
        # Fall (release)
        r.line((bx + atk_px + hold_px, top_y),
               (bx + atk_px + hold_px + rel_px, bot_y),
               2.0, (0.35, 0.75, 1.0, 1.0))

        # Attack slider
        (a_x, a_y, a_w), (r_x, r_y, r_w) = self._track_rects()
        a_y += self._render_dy
        r_y += self._render_dy
        a_x += self._render_dx
        r_x += self._render_dx
        r.text(a_x, a_y - 14, f"Attack {self.attack_ms:.0f}ms", 11,
               (0.80, 0.88, 1.0, 1.0))
        r.rect(a_x, a_y, a_w, 4, 2, (0.20, 0.22, 0.26, 1.0))
        at = (self.attack_ms - self._atk_lo) / max(1e-6, self._atk_hi - self._atk_lo)
        r.rect(a_x, a_y, a_w * at, 4, 2, (0.28, 0.68, 1.0, 1.0))

        r.text(r_x, r_y - 14, f"Release {self.release_ms:.0f}ms", 11,
               (0.80, 0.88, 1.0, 1.0))
        r.rect(r_x, r_y, r_w, 4, 2, (0.20, 0.22, 0.26, 1.0))
        rt = (self.release_ms - self._rel_lo) / max(1e-6, self._rel_hi - self._rel_lo)
        r.rect(r_x, r_y, r_w * rt, 4, 2, (0.28, 0.68, 1.0, 1.0))