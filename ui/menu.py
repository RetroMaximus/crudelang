"""Popup menus: the context menu and the menus hanging off the menu bar.

An item is a row in a vertical popup. Rows can carry a shortcut hint, a
check mark, or a whole child menu that opens on hover; separators are
rows too, so a menu is still one flat list. Children are ordinary
:class:`Menu` objects, which keeps nesting arbitrary (Export ▸ PNG ▸ …)
and lets one click handler serve every level.
"""
from __future__ import annotations
from typing import Callable

from ui.renderer import Renderer2D

ITEM_H = 26
SEP_H = 9
PAD_X = 12
CHECK_W = 18
ARROW_W = 16
MIN_W = 190
MAX_W = 460
TEXT_PX = 7.2          # rough advance of the 14 px UI font, used for width

BG = (0.16, 0.18, 0.22, 0.99)
BORDER = (0.32, 0.40, 0.52, 0.85)
HOVER = (0.30, 0.62, 0.95, 0.9)
TEXT = (0.95, 0.97, 1.0, 1.0)
DIM = (0.45, 0.50, 0.60, 1.0)
HINT = (0.55, 0.62, 0.75, 1.0)


class MenuItem:
    """One row: an action, a separator, or a submenu.

    ``enabled`` only gates actions; a disabled row still draws so labels
    and shortcuts can serve as a readout ("Chibi · 12 layers").
    """

    def __init__(self, label: str, on_click: Callable[[], None] | None = None,
                 enabled: bool = True, shortcut: str | None = None,
                 checked: bool = False, separator: bool = False,
                 submenu: list["MenuItem"] | None = None) -> None:
        self.label = label
        self.on_click = on_click
        self.enabled = enabled
        self.shortcut = shortcut
        self.checked = checked
        self.separator = separator
        self.submenu = submenu

    @property
    def is_submenu(self) -> bool:
        return bool(self.submenu)


def sep() -> MenuItem:
    """A horizontal rule between groups of rows."""
    return MenuItem("", separator=True)


def _text_px(text: str) -> float:
    return len(text or "") * TEXT_PX


class Menu:
    """Vertical popup. `open_at` positions it and shows it.

    The root menu owns at most one open child at a time; the child owns
    its own child, so a hover chain of any depth is just recursion. Every
    input helper routes into the chain, which is what lets App keep using
    a single `menu.on_click(...)` call.
    """

    def __init__(self) -> None:
        self.visible = False
        self.x = 0.0
        self.y = 0.0
        self.width = float(MIN_W)
        self.items: list[MenuItem] = []
        self.child: "Menu | None" = None
        self._child_index = -1
        self._hover = -1
        self._screen = (0, 0)

    # ---- lifecycle ------------------------------------------------------

    def open_at(self, x: float, y: float, items: list[MenuItem],
                screen_w: int, screen_h: int) -> None:
        self.close()
        self.items = list(items or [])
        if not self.items:
            return
        self._screen = (screen_w, screen_h)
        self.width = self._measure()
        self.x, self.y = float(x), float(y)
        self._clamp()
        self.visible = True
        self._hover = -1

    def open_below(self, x: float, y: float, items: list[MenuItem],
                   screen_w: int, screen_h: int) -> None:
        """Same as :meth:`open_at`; kept for menu-bar call sites."""
        self.open_at(x, y, items, screen_w, screen_h)

    def close(self) -> None:
        if self.child is not None:
            self.child.close()
        self.child = None
        self._child_index = -1
        self.visible = False
        self.items = []
        self._hover = -1

    # ---- geometry -------------------------------------------------------

    def _measure(self) -> float:
        widest = MIN_W
        for item in self.items:
            if item.separator:
                continue
            w = _text_px(item.label) + PAD_X * 2 + (CHECK_W if item.checked else 0)
            if item.shortcut:
                w += _text_px(item.shortcut) + 28
            if item.is_submenu:
                w += ARROW_W
            widest = max(widest, w)
        return min(MAX_W, widest)

    def height(self) -> float:
        total = 8.0
        for item in self.items:
            total += SEP_H if item.separator else ITEM_H
        return total

    def _item_top(self, index: int) -> float:
        y = self.y + 4.0
        for i, item in enumerate(self.items[:index]):
            y += SEP_H if item.separator else ITEM_H
        return y

    def _item_rect(self, index: int) -> tuple[float, float, float, float]:
        item = self.items[index]
        h = SEP_H if item.separator else ITEM_H
        return (self.x + 4, self._item_top(index), self.width - 8, h)

    def _clamp(self) -> None:
        sw, sh = self._screen
        if self.x + self.width > sw - 4:
            self.x = max(4.0, sw - self.width - 4)
        if self.y + self.height() > sh - 4:
            self.y = max(4.0, sh - self.height() - 4)

    def _own_rect(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.width, self.height())

    def contains(self, mx: float, my: float) -> bool:
        """True inside this popup or anywhere in its child chain."""
        if not self.visible:
            return False
        x, y, w, h = self._own_rect()
        if x <= mx <= x + w and y <= my <= y + h:
            return True
        return self.child is not None and self.child.contains(mx, my)

    def _item_at(self, mx: float, my: float) -> int:
        x, y, w, h = self._own_rect()
        if not (x <= mx <= x + w and y <= my <= y + h):
            return -1
        for i in range(len(self.items)):
            ix, iy, iw, ih = self._item_rect(i)
            if iy <= my <= iy + ih:
                return i
        return -1

    # ---- child menus ----------------------------------------------------

    def _open_child(self, index: int) -> None:
        item = self.items[index]
        if not item.is_submenu:
            return
        if self.child is not None and self._child_index == index:
            return
        child = Menu()
        ix, iy, _iw, _ih = self._item_rect(index)
        child.open_at(self.x + self.width - 6.0, iy - 4.0, item.submenu,
                      *self._screen)
        # Flip to the left when the child would leave the window.
        if (child.x + child.width > self._screen[0] - 4
                and self.x - child.width + 6.0 > 4.0):
            child.x = self.x - child.width + 6.0
        self.child = child
        self._child_index = index

    def _close_child(self) -> None:
        if self.child is not None:
            self.child.close()
        self.child = None
        self._child_index = -1

    # ---- input ----------------------------------------------------------

    def on_move(self, mx: float, my: float) -> None:
        if not self.visible:
            return
        if self.child is not None and self.child.contains(mx, my):
            self.child.on_move(mx, my)
            return
        i = self._item_at(mx, my)
        item = self.items[i] if 0 <= i < len(self.items) else None
        if item is not None and item.enabled and item.is_submenu:
            self._hover = i
            self._open_child(i)
            return
        # Pointer left the open submenu's row: drop it so the next hover
        # is unambiguous.
        if self.child is not None and i != self._child_index:
            self._close_child()
        self._hover = i

    def on_click(self, mx: float, my: float) -> bool:
        """Return True if the click was consumed by the menu chain."""
        if not self.visible:
            return False
        if self.child is not None and self.child.contains(mx, my):
            consumed = self.child.on_click(mx, my)
            if not self.child.visible:
                # A leaf row in the child ran, so the whole chain goes
                # away: leaving the parent up would strand an empty popup.
                self.close()
            return consumed
        i = self._item_at(mx, my)
        if i < 0:
            if self.contains(mx, my):
                return True
            self.close()
            return False
        item = self.items[i]
        if item.separator:
            return True
        if not item.enabled:
            return True
        if item.is_submenu:
            self._hover = i
            self._open_child(i)
            return True
        cb = item.on_click
        self.close()
        if cb is not None:
            cb()
        return True

    # ---- drawing --------------------------------------------------------

    def draw(self, r: Renderer2D) -> None:
        if not self.visible:
            return
        h = self.height()
        r.rect(self.x, self.y, self.width, h, 8, BG)
        # A thin outline keeps the popup readable over the bright canvas.
        r.rect(self.x, self.y, self.width, 1, 0, BORDER)
        r.rect(self.x, self.y + h - 1, self.width, 1, 0, BORDER)
        r.rect(self.x, self.y, 1, h, 0, BORDER)
        r.rect(self.x + self.width - 1, self.y, 1, h, 0, BORDER)
        for i, item in enumerate(self.items):
            ix, iy, iw, ih = self._item_rect(i)
            if item.separator:
                r.rect(ix + 8, iy + ih * 0.5, iw - 16, 1, 0,
                       (0.30, 0.36, 0.46, 0.8))
                continue
            hovered = (i == self._hover and item.enabled)
            if hovered:
                r.rect(ix, iy, iw, ih, 4, HOVER)
            col = TEXT if item.enabled else DIM
            tx = ix + PAD_X
            if item.checked:
                self._draw_check(r, tx + 3.0, iy + ih * 0.5, col)
                tx += CHECK_W
            r.text(tx, iy + 6, item.label, 14, col)
            if item.shortcut:
                sx = ix + iw - PAD_X - _text_px(item.shortcut) \
                    - (ARROW_W if item.is_submenu else 0)
                r.text(sx, iy + 7, item.shortcut, 12,
                       HINT if item.enabled else DIM)
            if item.is_submenu:
                ax = ix + iw - 12.0
                ay = iy + ih * 0.5
                r.line((ax - 3, ay - 4), (ax + 1, ay), 1.6, col)
                r.line((ax + 1, ay), (ax - 3, ay + 4), 1.6, col)
        if self.child is not None:
            self.child.draw(r)

    @staticmethod
    def _draw_check(r: Renderer2D, x: float, cy: float, col) -> None:
        """A tick drawn from lines, so it needs no glyph in the font."""
        r.line((x, cy), (x + 3, cy + 4), 2.0, col)
        r.line((x + 3, cy + 4), (x + 9, cy - 5), 2.0, col)
