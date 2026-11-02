"""Application menu bar: the File / Edit / View … row across the top.

The bar owns layout, hover and hit testing only; the menu contents come
from the app as a callable, so every open builds fresh items and check
marks reflect the state at that moment. Popups are ordinary
:class:`ui.menu.Menu` instances, which already support nesting.
"""
from __future__ import annotations

from typing import Callable

from ui.renderer import Renderer2D

BAR_H = 30.0
PAD_X = 10.0
GAP = 2.0
TEXT_PX = 7.6

TEXT = (0.86, 0.91, 0.98, 1.0)
DIM = (0.62, 0.68, 0.78, 1.0)
HOVER = (0.22, 0.30, 0.42, 1.0)
ACTIVE = (0.25, 0.52, 0.86, 1.0)


class MenuBar:
    """Horizontal row of menu titles with hover-to-switch behaviour."""

    def __init__(self, app) -> None:
        self.app = app
        self.entries: list[tuple[str, Callable[[], list]]] = []
        self._hits: list[tuple[float, float, float, float, str]] = []
        self._hover: str | None = None
        self.x = 0.0
        self.y = 0.0
        self.h = BAR_H
        self.width = 0.0
        # Title whose popup is open, or None. App clears it on close.
        self.active: str | None = None

    # ---- contents -------------------------------------------------------

    def add(self, title: str, items_fn: Callable[[], list]) -> None:
        self.entries.append((title, items_fn))

    def clear(self) -> None:
        self.entries.clear()
        self._hits = []

    def items_for(self, title: str) -> list:
        for name, fn in self.entries:
            if name == title:
                try:
                    return list(fn() or [])
                except TypeError:
                    return []
        return []

    # ---- geometry -------------------------------------------------------

    def _title_w(self, title: str) -> float:
        return len(title) * TEXT_PX + PAD_X * 2

    def layout(self, x: float, y: float, h: float = BAR_H) -> None:
        """Seat the bar at ``x``/``y``; call on resize and on first draw."""
        self.x, self.y, self.h = float(x), float(y), float(h)
        self._hits = []
        cx = self.x
        for title, _fn in self.entries:
            w = self._title_w(title)
            self._hits.append((cx, self.y, w, self.h, title))
            cx += w + GAP
        self.width = cx - self.x

    def hit(self, mx: float, my: float) -> str | None:
        """Title under the pointer, or None."""
        if not (self.y <= my <= self.y + self.h):
            return None
        for x, y, w, h, title in self._hits:
            if x <= mx <= x + w and y <= my <= y + h:
                return title
        return None

    # ---- input ----------------------------------------------------------

    def on_move(self, mx: float, my: float) -> str | None:
        """Track hover and switch the open menu, classic menu-bar style."""
        title = self.hit(mx, my)
        self._hover = title
        if (self.active is not None and title is not None
                and title != self.active):
            self.open(title)
        return title

    def on_press(self, mx: float, my: float) -> bool:
        """Open (or close) the menu under the pointer. True when handled."""
        title = self.hit(mx, my)
        if title is None:
            return False
        if self.active == title and getattr(self.app.menu, "visible", False):
            self.close()
            return True
        self.open(title)
        return True

    def open(self, title: str) -> bool:
        items = self.items_for(title)
        if not items:
            return False
        x, y, w, _h, _t = next(
            (hit for hit in self._hits if hit[4] == title),
            (self.x, self.y, self.width, self.h, title))
        sw, sh = self.app.window.get_size()
        self.app.menu.open_at(x, y + self.h - 2.0, items, sw, sh)
        self.active = title
        self.app.menu_source = f"menubar:{title}"
        return True

    def close(self) -> None:
        self.active = None
        self.app.menu.close()
        self.app.menu_source = None

    # ---- drawing --------------------------------------------------------

    def draw(self, r: Renderer2D) -> None:
        for x, y, w, h, title in self._hits:
            if self.active == title:
                r.rect(x, y + 4, w, h - 6, 5, ACTIVE)
            elif self._hover == title:
                r.rect(x, y + 4, w, h - 6, 5, HOVER)
            col = TEXT if (self.active == title or self._hover == title) else DIM
            r.text(x + PAD_X, y + 8, title, 14, col)
