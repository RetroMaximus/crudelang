"""Guided tour: spotlight one part of the window at a time.

A step is a small dict — ``title``, ``body``, ``rect`` and optionally
``enter``. ``rect`` is a callable ``(w, h) -> (x, y, w, h)`` read while
drawing, so the spotlight follows a dock the user resized or folded since
the tour started instead of pointing at a stale rectangle. ``None`` means
"no spotlight", which is how the opening and closing cards work.

``enter`` is a callable run whenever a step becomes current. A section
about the timeline cannot highlight a dock that is not on screen, so each
step asks the app to open the editor, the scene editor or presentation
mode first; without that the card would float in the middle of a window
with nothing to point at.

The card carries Previous / n-of-N / Next / Skip; Left and Right (plus
Enter) do the same from the keyboard and Esc leaves the tour.
"""
from __future__ import annotations

import glfw

from ui.renderer import Renderer2D
from ui.widgets import Button

CARD_W = 430.0
CARD_PAD = 18.0
TEXT_PX = 7.2
BODY_SIZE = 14
LINE_H = 20.0
BTN_H = 32.0

DIM = (0.02, 0.03, 0.05, 0.62)
SPOT = (0.30, 0.62, 0.98, 0.98)
CARD = (0.13, 0.15, 0.19, 0.99)
TITLE = (0.55, 0.70, 0.95, 1.0)
FG = (0.92, 0.95, 1.0, 1.0)
HINT = (0.62, 0.68, 0.78, 1.0)


def _wrap(text: str, size: int, width: float) -> list[str]:
    """Greedy word wrap; the renderer has no text measurement of its own."""
    per_char = max(1.0, float(size) * TEXT_PX / 14.0)
    max_chars = max(8, int(width / per_char))
    lines: list[str] = []
    for paragraph in str(text or "").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = words[0]
        for word in words[1:]:
            if len(line) + 1 + len(word) <= max_chars:
                line = f"{line} {word}"
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


class Tour:
    """Spotlight overlay stepping through ``app.tour_steps()``."""

    def __init__(self, app) -> None:
        self.app = app
        self.visible = False
        self.steps: list[dict] = []
        self.index = 0
        self._card: tuple | None = None
        self._spot: tuple | None = None
        self._buttons: dict[str, Button] = {}

    # ---- lifecycle ------------------------------------------------------

    def open(self, steps: list | None = None) -> bool:
        if steps is None:
            getter = getattr(self.app, "tour_steps", None)
            steps = getter() if getter is not None else []
        self.steps = [s for s in (steps or []) if isinstance(s, dict)]
        if not self.steps:
            return False
        self.index = 0
        self.visible = True
        self._card = None
        self._spot = None
        self._enter()
        return True

    def close(self, finished: bool = True) -> None:
        was_open = self.visible
        self.visible = False
        self.steps = []
        self._card = None
        self._spot = None
        self._buttons = {}
        if finished and was_open:
            notify = getattr(self.app, "tour_finished", None)
            if notify is not None:
                notify()

    def next(self) -> None:
        if not self.visible:
            return
        if self.index >= len(self.steps) - 1:
            self.close()
            return
        self.index += 1
        self._enter()

    def prev(self) -> None:
        if self.visible and self.index > 0:
            self.index -= 1
            self._enter()

    def current(self) -> dict:
        if not self.visible or not self.steps:
            return {}
        return self.steps[min(self.index, len(self.steps) - 1)]

    @property
    def label(self) -> str:
        return f"{self.index + 1} / {len(self.steps)}"

    def _enter(self) -> None:
        """Run the current step's ``enter`` hook. A bad hook never stops
        the tour: the card still points wherever the step says."""
        step = self.current()
        hook = step.get("enter") if isinstance(step, dict) else None
        if not callable(hook):
            return
        try:
            hook()
        except Exception:
            pass

    # ---- geometry -------------------------------------------------------

    def _spotlight(self, w, h) -> tuple | None:
        step = self.current()
        rect = None
        getter = step.get("rect")
        if callable(getter):
            try:
                rect = getter(w, h)
            except Exception:
                rect = None
        if rect is None:
            return None
        x, y, rw, rh = (float(v) for v in rect)
        pad = 6.0
        x = max(0.0, x - pad)
        y = max(0.0, y - pad)
        rw = max(28.0, min(float(w) - x, rw + pad * 2))
        rh = max(28.0, min(float(h) - y, rh + pad * 2))
        return (x, y, rw, rh)

    def _body_lines(self) -> list[str]:
        return _wrap(self.current().get("body", ""), BODY_SIZE,
                     CARD_W - CARD_PAD * 2)

    def _layout(self, w, h) -> tuple:
        spot = self._spotlight(w, h)
        self._spot = spot
        lines = self._body_lines()
        card_h = 46.0 + len(lines) * LINE_H + 54.0
        if spot is not None:
            sx, sy, sw, sh = spot
            below = sy + sh + 14.0
            if below + card_h <= h - 12.0:
                cy = below
            else:
                cy = max(12.0, sy - card_h - 14.0)
            cx = sx + sw * 0.5 - CARD_W * 0.5
            cx = max(12.0, min(cx, float(w) - CARD_W - 12.0))
        else:
            cx = (float(w) - CARD_W) * 0.5
            cy = max(12.0, (float(h) - card_h) * 0.5)
        self._card = (cx, cy, CARD_W, card_h)
        by = cy + card_h - 46.0
        last = self.index >= len(self.steps) - 1
        self._buttons = {
            "skip": Button(cx + CARD_PAD, by, 78, BTN_H, "Skip",
                           on_press=self.close),
            "next": Button(cx + CARD_W - CARD_PAD - 96, by, 96, BTN_H,
                           "Finish" if last else "Next >",
                           on_press=self.next),
            "prev": Button(cx + CARD_W - CARD_PAD - 96 - 90, by, 86, BTN_H,
                           "< Prev", on_press=self.prev),
        }
        return self._card

    # ---- input ----------------------------------------------------------

    def on_press(self, mx: float, my: float) -> bool:
        """Modal: every click belongs to the tour while it is open."""
        if not self.visible:
            return False
        for btn in self._buttons.values():
            if btn.contains(mx, my):
                btn.on_press(mx, my)
                return True
        return True

    def on_key(self, key) -> bool:
        if not self.visible:
            return False
        if key == glfw.KEY_LEFT:
            self.prev()
        elif key in (glfw.KEY_RIGHT, glfw.KEY_ENTER, glfw.KEY_KP_ENTER):
            self.next()
        elif key == glfw.KEY_ESCAPE:
            self.close()
        return True

    # ---- drawing --------------------------------------------------------

    def draw(self, r: Renderer2D, w: int, h: int) -> None:
        if not self.visible or not self.steps:
            return
        cx, cy, cw, ch = self._layout(w, h)
        spot = self._spot
        if spot is None:
            r.rect(0, 0, w, h, 0, DIM)
        else:
            # Four bands instead of a stencil: the hole lands exactly on
            # the widget, whatever its size.
            sx, sy, sw, sh = spot
            r.rect(0, 0, w, sy, 0, DIM)
            r.rect(0, sy + sh, w, max(0.0, float(h) - sy - sh), 0, DIM)
            r.rect(0, sy, sx, sh, 0, DIM)
            r.rect(sx + sw, sy, max(0.0, float(w) - sx - sw), sh, 0, DIM)
            r.rect(sx, sy, sw, 2, 0, SPOT)
            r.rect(sx, sy + sh - 2, sw, 2, 0, SPOT)
            r.rect(sx, sy, 2, sh, 0, SPOT)
            r.rect(sx + sw - 2, sy, 2, sh, 0, SPOT)

        r.rect(cx + 3, cy + 4, cw, ch, 12, (0.0, 0.0, 0.0, 0.35))
        r.rect(cx, cy, cw, ch, 12, CARD)
        r.rect(cx, cy, cw, 2, 0, SPOT)
        r.text(cx + CARD_PAD, cy + 14,
               str(self.current().get("title", "")), 15, TITLE)
        r.text(cx + cw - CARD_PAD - 54, cy + 16, self.label, 12, HINT)
        ty = cy + 44.0
        for line in self._body_lines():
            r.text(cx + CARD_PAD, ty, line, BODY_SIZE, FG)
            ty += LINE_H
        for btn in self._buttons.values():
            btn.draw(r)
