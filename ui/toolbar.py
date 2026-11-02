"""Toolbars. A horizontal strip of word buttons, plus the 2-column icon
palette used by the editor. Reusable everywhere."""
from __future__ import annotations
from typing import Callable
from ui.renderer import Renderer2D


class ToolbarItem:
    """Base for anything a toolbar can hold."""
    def __init__(self, x: float, y: float, w: float, h: float) -> None:
        self.x, self.y, self.w, self.h = x, y, w, h

    def contains(self, mx: float, my: float) -> bool:
        return self.x <= mx <= self.x + self.w and self.y <= my <= self.y + self.h

    def draw(self, r: Renderer2D, active: bool) -> None: ...
    def click(self) -> None: ...


class ToolButton(ToolbarItem):
    def __init__(self, x, y, w, h, label: str,
                 on_click: Callable[[], None],
                 tooltip: str = "") -> None:
        super().__init__(x, y, w, h)
        self.label = label
        self._cb = on_click
        self.tooltip = tooltip or label

    def draw(self, r: Renderer2D, active: bool) -> None:
        bg = (0.30, 0.62, 0.95, 1.0) if active else (0.20, 0.23, 0.28, 1.0)
        r.rect(self.x, self.y, self.w, self.h, 8, bg)
        # Rough horizontal centering
        tw = len(self.label) * 7
        r.text(self.x + (self.w - tw) * 0.5, self.y + self.h * 0.5 - 8,
               self.label, 14, (0.95, 0.97, 1.0, 1.0))

    def click(self) -> None:
        self._cb()


class Separator(ToolbarItem):
    def __init__(self, x, y, h):
        super().__init__(x, y, 1, h)

    def draw(self, r: Renderer2D, active: bool) -> None:
        r.rect(self.x, self.y + 6, 1, self.h - 12, 0.5, (0.35, 0.40, 0.50, 0.9))

    def click(self) -> None:
        pass


class Toolbar:
    """Horizontal strip of items. Handles hit-testing and active state."""

    def __init__(self, x: float, y: float, h: float,
                 bg: tuple[float, float, float, float] = (0.14, 0.16, 0.20, 0.95)) -> None:
        self.x, self.y, self.h = x, y, h
        self.bg = bg
        self.items: list[ToolbarItem] = []
        self.width = 0
        self._pad_x = 10
        self._pad_y = 8

    def add(self, item: ToolbarItem) -> None:
        """Position and add. Caller passes *dummy* x,y — we lay out sequentially."""
        ix = self.x + self._pad_x + self.width
        item.x = ix
        item.y = self.y + self._pad_y
        self.items.append(item)
        self.width += item.w + 6

    def total_width(self) -> float:
        return self.width + self._pad_x * 2

    def draw(self, r: Renderer2D, active_item: ToolbarItem | None = None) -> None:
        w = self.total_width()
        r.rect(self.x, self.y, w, self.h, 10, self.bg)
        for item in self.items:
            item.draw(r, item is active_item)

    def hit(self, mx: float, my: float) -> ToolbarItem | None:
        if not (self.x <= mx <= self.x + self.total_width()
                and self.y <= my <= self.y + self.h):
            return None
        for item in self.items:
            if item.contains(mx, my):
                return item
        return None


# ----------------------------------------------------------------------
# Icon palette (Sketchbook / Krita style: square cells, two columns)
# ----------------------------------------------------------------------

BG_IDLE = (0.20, 0.23, 0.28, 1.0)
BG_HOVER = (0.26, 0.31, 0.39, 1.0)
BG_ACTIVE = (0.30, 0.62, 0.95, 1.0)
ICON_PX = 24.0


class IconButton(ToolbarItem):
    """Square icon cell with a tooltip and a text fallback.

    The icon is drawn through the renderer's PNG texture cache; when the
    asset is missing the short ``label`` is drawn instead so the palette
    still works with an empty ``assets/icons`` folder.
    """

    def __init__(self, x, y, size, icon: str, label: str,
                 on_click: Callable[[], None], icons=None,
                 tooltip: str = "") -> None:
        super().__init__(x, y, size, size)
        self.icon = icon
        self.label = label
        self._cb = on_click
        self.icons = icons
        self.tooltip = tooltip or label
        self.hover = False
        # A cell that stays lit while its mode is on, independent of the
        # transient hover highlight the grid repaints every frame.
        self.pinned = False

    def _texture(self):
        if self.icons is None:
            return None
        return self.icons.get(self.icon)

    def draw(self, r: Renderer2D, active: bool) -> None:
        if active or self.pinned:
            bg = BG_ACTIVE
        elif self.hover:
            bg = BG_HOVER
        else:
            bg = BG_IDLE
        r.rect(self.x, self.y, self.w, self.h, 8, bg)
        tex = self._texture()
        if tex is not None:
            pad = (self.w - ICON_PX) * 0.5
            r.textured_quad(self.x + pad, self.y + pad, ICON_PX, ICON_PX,
                            texture=tex[0])
            return
        tw = len(self.label) * 7
        r.text(self.x + (self.w - tw) * 0.5, self.y + self.h * 0.5 - 8,
               self.label, 13, (0.95, 0.97, 1.0, 1.0))

    def click(self) -> None:
        self._cb()


class _GridGap(ToolbarItem):
    """Invisible spacer that pads a row out before a group break.

    The group hairline is painted by :meth:`VerticalToolGrid.draw`, which
    knows the full grid width; this cell only reserves the slot.
    """

    def __init__(self, x, y, w, h):
        super().__init__(x, y, w, h)

    def draw(self, r: Renderer2D, active: bool) -> None:
        pass

    def click(self) -> None:
        pass


class VerticalToolGrid:
    """Docked 2-column palette of :class:`IconButton` cells.

    Items are placed row-major. ``add_row_break`` forces the next item onto
    a fresh row and paints a hairline separator, which is how tool groups
    are kept apart.
    """

    def __init__(self, x: float = 0.0, y: float = 0.0, columns: int = 2,
                 cell: float = 36.0, gap: float = 6.0, pad: float = 8.0,
                 bg: tuple[float, float, float, float] = (0.14, 0.16, 0.20, 0.95),
                 ) -> None:
        self.x, self.y = x, y
        self.columns = max(1, int(columns))
        self.cell, self.gap, self.pad = cell, gap, pad
        self.bg = bg
        self.items: list[ToolbarItem] = []
        self._slot = 0          # next free (row, col) index
        self._row_break = False

    # ---- layout ------------------------------------------------------
    def move(self, x: float, y: float, max_w: float = 0.0,
             max_h: float = 0.0) -> None:
        """Reposition the grid; cells keep their grid order."""
        self.x, self.y = x, y
        for i, item in enumerate(self.items):
            item.x = self.x + self.pad + (i % self.columns) * (self.cell + self.gap)
            item.y = self.y + self.pad + (i // self.columns) * (self.cell + self.gap)

    def _place(self, item: ToolbarItem) -> None:
        idx = len(self.items)
        item.w = item.h = self.cell
        item.x = self.x + self.pad + (idx % self.columns) * (self.cell + self.gap)
        item.y = self.y + self.pad + (idx // self.columns) * (self.cell + self.gap)

    def add(self, item: ToolbarItem) -> ToolbarItem:
        if self._row_break and self.items:
            # Pad out the current row so the next item starts a new one.
            while len(self.items) % self.columns != 0:
                spacer = _GridGap(0.0, 0.0, self.cell, self.cell)
                self._place(spacer)
                self.items.append(spacer)
            self._row_break = False
        self._place(item)
        self.items.append(item)
        return item

    def add_row_break(self) -> None:
        self._row_break = True

    def width(self) -> float:
        return self.pad * 2 + self.columns * self.cell + (self.columns - 1) * self.gap

    def height(self) -> float:
        rows = max(1, (len(self.items) + self.columns - 1) // self.columns)
        return self.pad * 2 + rows * self.cell + (rows - 1) * self.gap

    # ---- drawing / hits ----------------------------------------------
    def draw(self, r: Renderer2D, active_item: ToolbarItem | None = None,
             hover_item: ToolbarItem | None = None) -> None:
        r.rect(self.x, self.y, self.width(), self.height(), 12, self.bg)
        separators: set[float] = set()
        for item in self.items:
            if isinstance(item, _GridGap):
                separators.add(round(item.y + item.h + self.gap * 0.5, 1))
                continue
            if isinstance(item, IconButton):
                item.hover = item is hover_item
            item.draw(r, item is active_item)
        for y in sorted(separators):
            r.rect(self.x + self.pad, y, self.width() - self.pad * 2, 1, 0.5,
                   (0.35, 0.40, 0.50, 0.7))

    def item_at(self, mx: float, my: float) -> ToolbarItem | None:
        """Hover/hit target under the pointer; group gaps are skipped."""
        if not (self.x <= mx <= self.x + self.width()
                and self.y <= my <= self.y + self.height()):
            return None
        for item in reversed(self.items):
            if item.contains(mx, my):
                return None if isinstance(item, _GridGap) else item
        return None

    def hit(self, mx: float, my: float) -> ToolbarItem | None:
        return self.item_at(mx, my)