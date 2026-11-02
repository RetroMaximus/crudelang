"""Config panel, body-part properties panel, modal Dialog scaffold."""
from __future__ import annotations

from pathlib import Path

from ui.renderer import Renderer2D
from ui.widgets import (Widget, Label, Slider, NumericSlider, Toggle,
                        Button, ColorPicker, TextInput)
PANEL_BG = (0.14, 0.16, 0.20, 0.95)
SECTION  = (0.55, 0.70, 0.95, 1.0)
FG       = (0.80, 0.88, 1.00, 1.0)

FOOTER_H = 54
RESIZE_H = 8
SCROLL_STEP = 40
# Height of the title bar; a folded panel is exactly this tall.
HEADER_H = 40

# Which edge of a docked panel faces the canvas (the only resizable one).
_DOCK_EDGE = {"top": "bottom", "bottom": "top",
              "left": "right", "right": "left"}


class _FooterHit:
    x = 0.0
    y = 0.0
    w = 0.0
    h = 0.0
    _render_dx = 0.0
    _render_dy = 0.0
    _scroll_offset = 0.0

    def __init__(self, panel):
        self._panel = panel

    def on_press(self, mx, my):
        self._panel.footer_action()

    def on_release(self, mx, my): pass
    def on_drag(self, mx, my): pass


SECTION_HEADER_H = 24.0


def _widget_bottom(w) -> float:
    """Bottom of a widget's footprint, giving labels a nominal height."""
    return w.y + (w.h if w.h > 0 else 18.0)


# Joints a prop can hang from. The waist comes first: belts, sheaths and
# bags ride it, and it is the joint a torso-only pose holds steady.
PROP_JOINTS = ("waist", "head",
               "wrist_l", "wrist_r", "elbow_l", "elbow_r",
               "hip_l", "hip_r", "knee_l", "knee_r",
               "ankle_l", "ankle_r")


def prop_anchor_widgets(widgets, app, layer, px, pw, cy) -> float:
    """ANCHOR section: is this prop static, or does it ride a joint?

    A static prop stays where it was placed and is what a pose or an
    emote pins a joint *to*; a dynamic prop follows the joint it hangs
    from. Both answers, the chips that give one and the way back out are
    in one block, so the scene inspector and the layer panel agree.
    """
    anchor = getattr(layer, "anchor", None)
    widgets.append(Label(px, cy, "ANCHOR", 12, SECTION))
    cy += 22
    if anchor:
        state = f"Dynamic — follows {anchor}"
    else:
        state = "Static — a pose pins a joint to it"
    widgets.append(Label(px, cy, state, 11, FG))
    cy += 20
    if anchor:
        follow = getattr(layer, "base_angle", None) is not None
        widgets.append(Button(
            px, cy, pw, 24, "Clear Anchor  (make static)",
            on_press=lambda n=layer.name: getattr(
                app, "set_prop_anchor", lambda *a: None)(n, None)))
        cy += 30
        widgets.append(Toggle(
            px, cy, pw, 22, follow, label="Turn with the bone",
            on_change=lambda v, n=layer.name: getattr(
                app, "set_prop_bone_rotation", lambda *a: None)(n, v)))
        cy += 30
    widgets.append(Label(px, cy, "Follow a joint:", 11, FG))
    cy += 18
    cols = 3
    cw = (pw - 2 * 4) / cols
    for i, jname in enumerate(PROP_JOINTS):
        row, col = divmod(i, cols)
        widgets.append(Button(
            px + col * (cw + 4), cy + row * 28, cw, 24, jname,
            on_press=lambda j=jname, n=layer.name: getattr(
                app, "set_prop_anchor", lambda *a: None)(n, j)))
    rows = (len(PROP_JOINTS) + cols - 1) // cols
    return cy + rows * 28 + 8


class SectionHeader(Widget):
    """Fold-out header for one group of widgets inside a panel.

    The chevron folds the group away; dragging anywhere else on the row
    reorders it against its siblings. Sections never leave their panel.
    """

    H = SECTION_HEADER_H
    CHEVRON_W = 22.0

    def __init__(self, panel, x, y, w, title, collapsed=False):
        super().__init__(x, y, w, self.H)
        self.panel = panel
        self.title = title
        self.collapsed = bool(collapsed)
        self.hover = False
        self.dragging = False

    # ---- geometry helpers

    def _screen(self):
        return (self.x + self._render_dx, self.y + self._render_dy)

    def chevron_hit(self, mx, my) -> bool:
        sx, sy = self._screen()
        return sx <= mx <= sx + self.CHEVRON_W and sy <= my <= sy + self.h

    # ---- events

    def on_press(self, mx, my):
        if self.chevron_hit(mx, my):
            self.panel.toggle_section(self.title)
            return
        self.dragging = True
        self.panel.begin_section_drag(self, my)

    def on_drag(self, mx, my):
        if self.dragging:
            self.panel.drag_section(self, my)

    def on_release(self, mx, my):
        if self.dragging:
            self.dragging = False
            self.panel.end_section_drag(self, my)

    # ---- drawing

    def draw(self, r):
        sx, sy = self._screen()
        mx, my = getattr(self.panel.app, "_mouse", (0.0, 0.0))
        self.hover = (sx <= mx <= sx + self.w and sy <= my <= sy + self.h)
        bg = (0.20, 0.23, 0.29, 1.0) if self.hover else (0.17, 0.19, 0.24, 1.0)
        r.rect(sx, sy, self.w, self.h, 5, bg)
        cx, cy = sx + self.CHEVRON_W * 0.5, sy + self.h * 0.5
        if self.collapsed:
            r.line((cx - 3, cy - 4), (cx + 2, cy), 1.8, SECTION)
            r.line((cx + 2, cy), (cx - 3, cy + 4), 1.8, SECTION)
        else:
            r.line((cx - 4, cy - 2), (cx, cy + 2), 1.8, SECTION)
            r.line((cx, cy + 2), (cx + 4, cy - 2), 1.8, SECTION)
        r.text(sx + self.CHEVRON_W + 4, sy + 5, self.title, 12, SECTION)
        # Grip dots hint that the row can be dragged around the panel.
        gx = sx + self.w - 14
        for i in range(2):
            for j in range(3):
                r.rect(gx + j * 4, sy + self.h * 0.5 - 5 + i * 10, 2, 2, 1,
                       (0.45, 0.52, 0.65, 0.8 if self.hover else 0.45))


class _PanelBase:
    def __init__(self, app, x, y, w, h):
        self.app = app
        self.x, self.y, self.w, self.h = x, y, w, h
        self._build_x = x
        self._build_y = y
        self.widgets: list[Widget] = []
        self.scroll_y = 0.0
        self._content_h = 0.0
        self._resizing = False
        self._dragging = False
        self._drag_offset = (0.0, 0.0)
        # Which dock this panel lives in: top / bottom / left / right or
        # None while floating. Drives which edge can be dragged.
        self.dock_region: str | None = "right"
        # Folded panels paint only their header and swallow every widget
        # and footer event, so no panel needs its own guard.
        self.collapsed = False
        # Inner section fold-outs: which group is folded and the order the
        # user dragged the groups into. Persisted per panel class.
        self._section_collapsed: dict[str, bool] = {}
        self._section_order: list[str] = []
        self._section_drag: str | None = None
        self._sections_dirty = True
        # Untouched build output. Section layout always works from this
        # list, so re-applying it can never shift a body twice.
        self._raw_widgets: list[Widget] = []
        # Kept out of a future stream capture when set.
        self.exclude_from_stream = False
        # Fallback store for stubs that have no config attached.
        self._dock_memory: dict = {}
        self._load_sections()
        self._load_stream_exclusion()

    def _snapshot_build_origin(self):
        self._build_x = self.x
        self._build_y = self.y
        self._sections_dirty = True
        self._raw_widgets = []

    # ---- section state -------------------------------------------------

    def _dock_cfg(self) -> dict:
        """This panel's persisted ``dock`` dict.

        Panels built without a config (tests, headless stubs) keep their
        state in memory only.
        """
        config = getattr(self.app, "config", None)
        data = getattr(config, "data", None)
        if not isinstance(data, dict):
            data = self._dock_memory
        return data.setdefault("dock", {})

    def _sections_cfg(self) -> dict:
        sections = self._dock_cfg().setdefault("sections", {})
        return sections.setdefault(type(self).__name__, {})

    def _load_sections(self) -> None:
        cfg = self._sections_cfg()
        self._section_order = [str(t) for t in cfg.get("order", [])]
        collapsed = cfg.get("collapsed", {}) or {}
        self._section_collapsed = {str(k): bool(v) for k, v in collapsed.items()}

    def _save_sections(self) -> None:
        cfg = self._sections_cfg()
        cfg["order"] = list(self._section_order)
        cfg["collapsed"] = dict(self._section_collapsed)
        save = getattr(getattr(self.app, "config", None), "save", None)
        if save is not None:
            save()

    def toggle_section(self, title: str) -> None:
        self._section_collapsed[title] = not self._section_collapsed.get(
            title, False)
        self._save_sections()
        self.rebuild()

    def begin_section_drag(self, header, my: float) -> None:
        self._section_drag = header.title

    def end_section_drag(self, header, my: float) -> None:
        self._section_drag = None

    def drag_section(self, header, my: float) -> None:
        """Swap a dragged section with the one under the pointer."""
        if self._section_drag is None:
            return
        target = None
        for w in self.widgets:
            if not isinstance(w, SectionHeader) or w is header:
                continue
            top = w.y + w._render_dy
            if top <= my <= top + w.h:
                target = w.title
                break
        if target is None or target == self._section_drag:
            return
        order = [w.title for w in self.widgets
                 if isinstance(w, SectionHeader)]
        if self._section_drag not in order or target not in order:
            return
        src = order.index(self._section_drag)
        dst = order.index(target)
        order.pop(src)
        # Dropping onto a neighbour has to cross it: once the dragged
        # section is out of the list the target owns that slot, so the
        # insert lands below it when moving down and above it when moving
        # up — a plain remove-then-insert sits still.
        order.insert(dst, self._section_drag)
        self._section_order = order
        self._save_sections()
        self.rebuild()

    # ---- stream exclusion ----------------------------------------------

    def _load_stream_exclusion(self) -> None:
        names = self._dock_cfg().get("stream_excluded") or []
        if isinstance(names, (list, tuple, set)):
            self.exclude_from_stream = type(self).__name__ in names

    def _save_stream_exclusion(self) -> None:
        dock = self._dock_cfg()
        names = set(dock.get("stream_excluded") or ())
        key = type(self).__name__
        if self.exclude_from_stream:
            names.add(key)
        else:
            names.discard(key)
        dock["stream_excluded"] = sorted(names)
        save = getattr(getattr(self.app, "config", None), "save", None)
        if save is not None:
            save()

    def stream_toggle_rect(self):
        """The small stream button left of the fold chevron."""
        return (self.x + self.w - 62, self.y + HEADER_H * 0.5 - 10, 20, 20)

    def stream_hit(self, mx, my) -> bool:
        tx, ty, tw, th = self.stream_toggle_rect()
        return tx <= mx <= tx + tw and ty <= my <= ty + th

    def toggle_stream_exclusion(self) -> None:
        """Keep this panel out of the stream, or let it back in."""
        self.exclude_from_stream = not self.exclude_from_stream
        self._save_stream_exclusion()
        notify = getattr(self.app, "_stream_exclusion_changed", None)
        if notify is not None:
            notify(self)

    def _widgets_bottom(self) -> float:
        """Lowest widget edge in build space (``_build_y``-relative)."""
        return max((_widget_bottom(w) for w in self.widgets),
                   default=self._build_y)

    def _snapshot_raw(self) -> None:
        """Freeze the untouched build output for the section pass.

        ``rebuild()`` starts with ``_snapshot_build_origin()``, which
        clears this list, so a rebuild always re-snapshots what it just
        built.
        """
        if not self._raw_widgets:
            self._raw_widgets = list(self.widgets)

    def _ensure_sections(self) -> None:
        """Run the section post-pass once per rebuild."""
        if not self._sections_dirty:
            return
        self._sections_dirty = False
        try:
            self._apply_sections()
        except Exception:
            # A malformed panel must still draw; sections are cosmetic.
            pass

    def _finalize_layout(self, set_content: bool = True) -> None:
        """Apply sections and derive the scroll height in one place.

        Called at the end of a panel's ``rebuild()`` so widget positions
        and ``_content_h`` are correct before the first draw, instead of
        being patched lazily while the panel is already on screen.
        ``set_content=False`` keeps a panel's own content height (the
        layer panel measures only its lower half).
        """
        self._snapshot_raw()
        self._sections_dirty = False
        try:
            self._apply_sections()
        except Exception:
            self.widgets = list(self._raw_widgets)
        if set_content:
            self._content_h = max(0.0, self._widgets_bottom()
                                  - self._build_y + 16)

    def _apply_sections(self) -> None:
        """Turn SECTION labels into fold-out headers.

        Panels build a flat widget list, so section markers are recognised
        by their shared styling. Each marker owns the widgets that follow
        it until the next one: folding a section drops its body and closes
        the gap, and the stored order lets a drag reorder the groups
        without ever leaving the panel.

        Everything built *before* the first marker (titles, capture
        buttons, live readouts) is kept and stays where it was built.
        The pass always starts from ``_raw_widgets``, so calling it again
        lays the same panel out identically instead of shifting it.
        """
        self._snapshot_raw()
        raw = list(self._raw_widgets)
        markers = [(i, w) for i, w in enumerate(raw)
                   if isinstance(w, Label)
                   and getattr(w, "size", 14) <= 12
                   and tuple(w.color) == tuple(SECTION)]
        if not markers:
            self.widgets = raw
            return
        groups: list[dict] = []
        for k, (idx, label) in enumerate(markers):
            end = (markers[k + 1][0] if k + 1 < len(markers)
                   else len(raw))
            groups.append({"title": label.text,
                           "body": raw[idx + 1:end]})
        if self._section_order:
            rank = {t: i for i, t in enumerate(self._section_order)}
            groups.sort(key=lambda g: rank.get(g["title"], len(rank)))
        x = self._build_x + 20
        width = max(120.0, self.w - 60.0)
        cursor = markers[0][1].y
        laid: list[Widget] = list(raw[:markers[0][0]])
        for group in groups:
            collapsed = bool(self._section_collapsed.get(group["title"]))
            header = SectionHeader(self, x, cursor, width,
                                   group["title"], collapsed)
            laid.append(header)
            cursor += SECTION_HEADER_H + 2
            body = group["body"]
            if not body or collapsed:
                cursor += 4
                continue
            first_y = min(w.y for w in body)
            for w in body:
                w.y = cursor + (w.y - first_y)
                laid.append(w)
            cursor = max(_widget_bottom(w) for w in body) + 8
        self.widgets = laid
        bottom = max((_widget_bottom(w) for w in laid), default=self._build_y)
        self._content_h = bottom - self._build_y + 16

    def _viewport(self):
        if self.collapsed:
            return (self.x, self.y + HEADER_H, self.w, 0.0)
        return (self.x, self.y + HEADER_H,
                self.w, self.h - HEADER_H - FOOTER_H)

    def _footer_rect(self):
        return (self.x + 20, self.y + self.h - FOOTER_H + 12,
                self.w - 40, 34)

    # ---- fold-out header ------------------------------------------------

    def header_toggle_rect(self):
        """The chevron button at the right end of the title bar."""
        return (self.x + self.w - 36, self.y + HEADER_H * 0.5 - 10, 20, 20)

    def header_hit(self, mx, my) -> bool:
        """True when a click should fold or unfold this panel.

        Expanded panels fold from the chevron only; the centre grip stays
        a drag handle. A folded bar is clickable anywhere but there.
        """
        if not (self.x <= mx <= self.x + self.w
                and self.y <= my <= self.y + HEADER_H):
            return False
        if self.drag_zone(mx, my):
            return False
        tx, ty, tw, th = self.header_toggle_rect()
        if tx <= mx <= tx + tw and ty <= my <= ty + th:
            return True
        return self.collapsed

    def header_click(self) -> None:
        self.collapsed = not self.collapsed
        notify = getattr(self.app, "_panel_collapsed_changed", None)
        if notify is not None:
            notify(self)

    def footer_action(self): pass

    def max_scroll(self):
        if self.collapsed:
            return 0.0
        self._ensure_sections()
        _, _, _, vh = self._viewport()
        return max(0.0, self._content_h - vh)

    def on_scroll(self, dy, mx, my):
        vx, vy, vw, vh = self._viewport()
        if not (vx <= mx <= vx + vw and vy <= my <= vy + vh):
            return False
        self.scroll_y = max(0.0, min(self.max_scroll(),
                                     self.scroll_y - dy * SCROLL_STEP))
        return True

    def drag_zone(self, mx, my) -> bool:
        kx = self.x + self.w * 0.5 - 20
        ky = self.y
        return (kx <= mx <= kx + 40) and (ky <= my <= ky + 10)

    def begin_drag(self, mx, my):
        self._dragging = True
        self._drag_offset = (mx - self.x, my - self.y)

    def do_drag(self, mx, my):
        if not self._dragging: return
        ox, oy = self._drag_offset
        self.x = mx - ox
        self.y = my - oy

    def end_drag(self):
        self._dragging = False

    def _owns_dock_edge(self, edge: str) -> bool:
        """True when this panel's edge *is* the dock's boundary.

        Panels sharing a dock are stacked, so only the outermost one
        faces the canvas; the separators between two panels are internal
        and re-balance the stack instead of resizing the dock.
        """
        workspace = getattr(self.app, "workspace", None)
        rect = (workspace.rect(self.dock_region)
                if workspace is not None and self.dock_region else None)
        if rect is None:
            return True
        _dx, dy, _dw, dh = rect
        if edge == "top":
            return abs(self.y - dy) <= 1.0
        return abs((self.y + self.h) - (dy + dh)) <= 1.0

    def resize_zone(self, mx, my):
        """Only the canvas-facing edge resizes while docked."""
        if self.collapsed:
            return None
        edge = _DOCK_EDGE.get(self.dock_region)
        if edge is not None:
            if edge in ("top", "bottom"):
                if not self._owns_dock_edge(edge):
                    return None
                if abs(my - (self.y if edge == "top" else self.y + self.h)) <= RESIZE_H \
                        and self.x <= mx <= self.x + self.w:
                    return edge
                return None
            if abs(mx - (self.x + self.w if edge == "right" else self.x)) <= RESIZE_H \
                    and self.y <= my <= self.y + self.h:
                return edge
            return None
        # Floating panels keep the old free resize, with the left edge too.
        if abs(my - self.y) <= RESIZE_H and self.x <= mx <= self.x + self.w:
            return "top"
        if abs(my - (self.y + self.h)) <= RESIZE_H and self.x <= mx <= self.x + self.w:
            return "bottom"
        if abs(mx - (self.x + self.w)) <= RESIZE_H and self.y <= my <= self.y + self.h:
            return "right"
        if abs(mx - self.x) <= RESIZE_H and self.y <= my <= self.y + self.h:
            return "left"
        return None

    def begin_resize(self, edge):
        self._resizing = True

    def _dock_size_for(self, edge, mx, my) -> float | None:
        """Translate a separator drag into the owning dock's new size."""
        region = self.dock_region
        if region is None:
            return None
        workspace = getattr(self.app, "workspace", None)
        rect = workspace.rect(region) if workspace is not None else None
        if rect is not None:
            # Measured against the dock, not the panel: a stacked panel
            # does not start at the dock's edge.
            dx, dy, dw, dh = rect
            if region == "bottom" and edge == "top":
                return dy + dh - my
            if region == "top" and edge == "bottom":
                return my - dy
            if region == "left" and edge == "right":
                return mx - dx
            if region == "right" and edge == "left":
                return dx + dw - mx
            return None
        if region == "bottom" and edge == "top":
            return self.y + self.h - my
        if region == "top" and edge == "bottom":
            return my - self.y
        if region == "left" and edge == "right":
            return mx - self.x
        if region == "right" and edge == "left":
            return self.x + self.w - mx
        return None

    def do_resize(self, edge, mx, my):
        if not self._resizing:
            return
        size = self._dock_size_for(edge, mx, my)
        if size is not None:
            self.app.resize_dock(self.dock_region, size)
            return
        if edge == "top":
            new_y = min(my, self.y + self.h - 120)
            self.h += self.y - new_y
            self.y = new_y
        elif edge == "bottom":
            self.h = max(120, my - self.y)
        elif edge == "right":
            self.w = max(200, mx - self.x)
        elif edge == "left":
            new_x = min(mx, self.x + self.w - 200)
            self.w += self.x - new_x
            self.x = new_x

    def end_resize(self):
        self._resizing = False
        if self.dock_region is not None:
            self.app._apply_workspace_layout(rebuild=True)

    def _draw_shell(self, r, title):
        if self.collapsed:
            r.rect(self.x, self.y, self.w, HEADER_H, 12, PANEL_BG)
        else:
            r.rect(self.x, self.y, self.w, self.h, 14, PANEL_BG)
        r.text(self.x + 20, self.y + 13, title, 14, SECTION)
        r.rect(self.x + self.w * 0.5 - 20, self.y + 2, 40, 5, 2.5,
               (0.55, 0.65, 0.85, 1.0))
        self._draw_fold_button(r)
        self._draw_stream_button(r)

    def _draw_stream_button(self, r):
        tx, ty, tw, th = self.stream_toggle_rect()
        r.rect(tx, ty, tw, th, 5, (0.22, 0.26, 0.33, 1.0))
        if self.exclude_from_stream:
            col = (0.95, 0.58, 0.38, 1.0)
        else:
            col = (0.45, 0.52, 0.64, 1.0)
        # A little monitor glyph; slashed when the panel is excluded.
        r.rect(tx + 4, ty + 6, 12, 8, 2, col)
        r.rect(tx + 8, ty + 14, 4, 2, 1, col)
        if self.exclude_from_stream:
            r.line((tx + 4, ty + 6), (tx + 16, ty + 14), 1.6,
                   (0.98, 0.90, 0.85, 1.0))

    def _draw_fold_button(self, r):
        tx, ty, tw, th = self.header_toggle_rect()
        r.rect(tx, ty, tw, th, 6, (0.22, 0.26, 0.33, 1.0))
        cx, cy = tx + tw * 0.5, ty + th * 0.5
        if self.collapsed:
            # Chevron pointing right: click to unfold.
            r.line((cx - 3, cy - 5), (cx + 2, cy), 2.0, SECTION)
            r.line((cx + 2, cy), (cx - 3, cy + 5), 2.0, SECTION)
        else:
            # Chevron pointing down: click to fold away.
            r.line((cx - 5, cy - 2), (cx, cy + 3), 2.0, SECTION)
            r.line((cx, cy + 3), (cx + 5, cy - 2), 2.0, SECTION)

    def _draw_scrollbar(self, r):
        if self.collapsed:
            return
        max_s = self.max_scroll()
        if max_s <= 0: return
        vx, vy, vw, vh = self._viewport()
        track_x = vx + vw - 6
        r.rect(track_x, vy, 4, vh, 2, (0.22, 0.24, 0.30, 1.0))
        thumb_h = max(24, vh * vh / self._content_h)
        thumb_y = vy + (vh - thumb_h) * (self.scroll_y / max_s)
        r.rect(track_x, thumb_y, 4, thumb_h, 2, (0.45, 0.55, 0.75, 1.0))

    def _draw_widgets(self, r):
        if self.collapsed:
            return
        self._ensure_sections()
        vx, vy, vw, vh = self._viewport()
        dx = self.x - self._build_x
        dy = self.y - self._build_y
        r.push_clip(vx, vy, vw, vh)
        for w in self.widgets:
            w._render_dx = dx
            w._render_dy = dy - self.scroll_y
            screen_y = w.y + w._render_dy
            if screen_y + w.h < vy: continue
            if screen_y > vy + vh: continue
            w.draw(r)
        r.pop_clip()
        self._draw_scrollbar(r)

    def _draw_footer(self, r, label):
        if self.collapsed:
            return
        fx, fy, fw, fh = self._footer_rect()
        r.rect(fx, fy, fw, fh, 8, (0.22, 0.50, 0.82, 1.0))
        tw = len(label) * 7
        r.text(fx + (fw - tw) * 0.5, fy + fh * 0.5 - 8,
               label, 14, (0.98, 0.99, 1.0, 1.0))

    def hit_test(self, mx, my):
        if self.collapsed:
            return None
        # Sections move widgets around, so make sure the post-pass has
        # already run before comparing against widget geometry.
        self._ensure_sections()
        fx, fy, fw, fh = self._footer_rect()
        if fx <= mx <= fx + fw and fy <= my <= fy + fh:
            return _FooterHit(self)
        vx, vy, vw, vh = self._viewport()
        if not (vx <= mx <= vx + vw and vy <= my <= vy + vh):
            return None
        dx = self.x - self._build_x
        dy = self.y - self._build_y
        content_mx = mx - dx
        content_my = my - dy + self.scroll_y
        for w in self.widgets:
            if (w.x <= content_mx <= w.x + w.w
                    and w.y <= content_my <= w.y + w.h):
                w._scroll_offset = self.scroll_y
                w._render_dx = dx
                w._render_dy = dy - self.scroll_y
                return w
        return None


class BodyPartPanel(_PanelBase):
    # unchanged from v0.9.5
    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)

    def footer_action(self):
        self.app.set_selected_part(None)

    def rebuild(self):
        self._snapshot_build_origin()
        self.widgets = []
        self.scroll_y = 0.0
        part = self.app.selected_part
        if part is None:
            self._content_h = 0.0
            return
        rig = self.app.rig
        px = self.x + 20
        pw = self.w - 80
        cy = self.y + 48
        self.widgets.append(Label(px, cy, part.upper().replace("_", " "), 14, SECTION)); cy += 24
        if part == "rig":
            self.widgets.append(Label(px, cy, "Rig has no global scale.", 12, FG)); cy += 24
        else:
            obj = getattr(rig, part)
            self.widgets.append(Toggle(px, cy, pw, 22, obj.enabled, label="Enabled",
                                       on_change=lambda v, o=obj: setattr(o, "enabled", v)))
            cy += 36
            self._add_number(px, cy, pw, "Opacity", obj, "opacity", 0.0, 1.0); cy += 52
            if part == "head":
                self._add_number(px, cy, pw, "Radius", obj, "radius", 0.05, 0.30); cy += 52
                self._add_number(px, cy, pw, "Offset X", obj, "offset_x", -0.10, 0.10); cy += 52
                self._add_number(px, cy, pw, "Offset Y", obj, "offset_y", -0.10, 0.10); cy += 52
                self._add_number(px, cy, pw, "Eye size", obj, "eye_size", 0.10, 0.60); cy += 52
                self._add_number(px, cy, pw, "Eye gap", obj, "eye_gap", 0.0, 0.60); cy += 52
                self._add_number(px, cy, pw, "Eye vertical", obj, "eye_vertical", -0.2, 0.4); cy += 52
            elif part == "neck":
                self._add_number(px, cy, pw, "Thickness", obj, "thickness", 0.01, 0.10); cy += 52
                self._add_number(px, cy, pw, "Length scale", obj, "length_scale", 0.3, 1.4); cy += 52
                self._add_number(px, cy, pw, "Offset X", obj, "offset_x", -0.20, 0.20); cy += 52
                self._add_number(px, cy, pw, "Offset Y", obj, "offset_y", -0.20, 0.20); cy += 52
            elif part == "torso":
                self._add_number(px, cy, pw, "Thickness", obj, "thickness", 0.02, 0.20); cy += 52
                self._add_number(px, cy, pw, "Length", obj, "length", 0.10, 0.60); cy += 52
                self._add_number(px, cy, pw, "Start overlap", obj, "start_overlap", 0.8, 1.5); cy += 52
                self._add_number(px, cy, pw, "End overlap", obj, "end_overlap", 0.8, 1.5); cy += 52
                self._add_number(px, cy, pw, "Offset X", obj, "offset_x", -0.20, 0.20); cy += 52
                self._add_number(px, cy, pw, "Offset Y", obj, "offset_y", -0.20, 0.20); cy += 52
            else:
                self._add_number(px, cy, pw, "Thickness", obj, "thickness", 0.01, 0.15); cy += 52
                self._add_number(px, cy, pw, "Start overlap", obj, "start_overlap", 0.8, 1.3); cy += 52
                self._add_number(px, cy, pw, "End overlap", obj, "end_overlap", 0.7, 1.2); cy += 52
                self._add_number(px, cy, pw, "Offset X", obj, "offset_x", -0.10, 0.10); cy += 52
                self._add_number(px, cy, pw, "Offset Y", obj, "offset_y", -0.10, 0.10); cy += 52
            self.widgets.append(Label(px, cy, "DETECTION", 12, SECTION)); cy += 22
            self._add_number(px, cy, pw, "Min visibility", obj.detection,
                             "min_visibility", 0.0, 1.0); cy += 52
            self.widgets.append(Label(px, cy, "SMOOTHING", 12, SECTION)); cy += 22
            self._add_number(px, cy, pw, "Attack (frames)", obj.smoothing,
                             "attack", 1.0, 30.0); cy += 52
            self._add_number(px, cy, pw, "Release (frames)", obj.smoothing,
                             "release", 1.0, 60.0); cy += 52
        self._finalize_layout()

    def _add_number(self, x, y, w, name, obj, attr, lo, hi):
        cur = float(getattr(obj, attr))
        def cb(v, o=obj, a=attr): setattr(o, a, v)
        self.widgets.append(NumericSlider(x, y, w, 30, cur, lo, hi, name, on_change=cb))

    def draw(self, r, _w, _h):
        if self.app.selected_part is None: return
        self._draw_shell(r, "PROPERTIES")
        self._draw_widgets(r)
        self._draw_footer(r, "Close")


class JointPanel(_PanelBase):
    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)

    def footer_action(self):
        self.app.set_selected_joint(None)

    def rebuild(self):
        self._snapshot_build_origin()
        self.widgets = []
        self.scroll_y = 0.0
        name = getattr(self.app, "selected_joint", None)
        if name is None:
            self._content_h = 0.0
            return
        jr = getattr(self.app.rig.joints, name, None)
        if jr is None:
            self._content_h = 0.0
            return
        px, pw = self.x + 20, self.w - 80
        cy = self.y + 48
        self.widgets.append(Label(px, cy, f"JOINT: {name}", 14, SECTION));
        cy += 24
        self.widgets.append(Toggle(px, cy, pw, 22, jr.enabled, label="Enabled",
                                   on_change=lambda v, o=jr: setattr(o, "enabled", v)))
        cy += 36
        self._add_number(px, cy, pw, "Offset X", jr, "offset_x", -0.30, 0.30);
        cy += 52
        self._add_number(px, cy, pw, "Offset Y", jr, "offset_y", -0.30, 0.30);
        cy += 52

        # toward_mix only affects shoulder placement
        if name in ("shoulder_l", "shoulder_r"):
            self._add_number(px, cy, pw, "Toward mix", jr, "toward_mix",
                             0.0, 1.0);
            cy += 52

        # segment_length is read by _build_chibi_joints for these joints
            # segment_length is read by _build_chibi_joints for these joints
        if name in ("head", "neck_top", "waist",
                    "hip_l", "hip_r",
                    "elbow_l", "elbow_r", "wrist_l", "wrist_r",
                    "knee_l", "knee_r", "ankle_l", "ankle_r"):
            self._add_number(px, cy, pw, "Segment length", jr, "segment_length",
                             0.0, 0.40);
            cy += 52

        self.widgets.append(Label(px, cy, "SMOOTHING", 12, SECTION));
        cy += 22
        self._add_number(px, cy, pw, "Attack (frames)", jr.smoothing,
                         "attack", 1.0, 30.0);
        cy += 52
        self._add_number(px, cy, pw, "Release (frames)", jr.smoothing,
                         "release", 1.0, 60.0);
        cy += 52
        self._finalize_layout()

    def _add_number(self, x, y, w, name, obj, attr, lo, hi):
        cur = float(getattr(obj, attr))
        def cb(v, o=obj, a=attr): setattr(o, a, v)
        self.widgets.append(NumericSlider(x, y, w, 30, cur, lo, hi, name, on_change=cb))

    def draw(self, r, _w, _h):
        if getattr(self.app, "selected_joint", None) is None: return
        self._draw_shell(r, "JOINT")
        self._draw_widgets(r)
        self._draw_footer(r, "Close")


class ConfigPanel(_PanelBase):
    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)
        # Goes through rebuild() so the section post-pass has a build
        # origin to work against on the very first frame.
        self.rebuild()

    def footer_action(self):
        self.app.toggle_mode()

    def rebuild(self):
        self._snapshot_build_origin()
        self.widgets = []
        self.scroll_y = 0.0
        self._build()
        self._finalize_layout()

    def _build(self):
        cfg_m = self.app.config.data["model"]
        cfg_t = self.app.config.data["tracking"]
        px, pw = self.x + 20, self.w - 40
        cy = self.y + 48
        self.widgets.append(Label(px, cy, "SCENE", 12, SECTION)); cy += 24
        self.widgets.append(Label(px, cy, "Background", 12, FG)); cy += 20
        self.widgets.append(ColorPicker(
            px, cy, pw - 100, 18,
            self.app.config.data["scene"].get("bg", "#FF00FF"),
            on_change=self._on_bg_color))
        # Reserves the sliders plus the HEX field below them.
        cy += 112
        self.widgets.append(Label(px, cy, "MODEL TYPE", 12, SECTION)); cy += 24
        for name, label in [("simple", "Simple"), ("chibi", "Chibi")]:
            selected = cfg_m.get("type") == name
            mark = "● " if selected else "   "
            self.widgets.append(Button(px, cy, pw, 26, mark + label,
                                       on_press=lambda n=name: self.app.set_model(n)))
            cy += 32
        cy += 8
        self.widgets.append(Label(px, cy, "CAMERA FRAMING", 12, SECTION)); cy += 24
        for name in ("closeup", "portrait", "mid", "landscape"):
            selected = cfg_m.get("framing") == name
            mark = "● " if selected else "   "
            self.widgets.append(Button(px, cy, pw, 26, mark + name.title(),
                                       on_press=lambda n=name: self.app.set_framing(n)))
            cy += 32
        cy += 8
        self.widgets.append(Label(px, cy, "MODEL", 12, SECTION)); cy += 24
        self.widgets.append(Label(px, cy, "Opacity (edit)", 12, FG)); cy += 20
        self.widgets.append(Slider(px, cy, pw, 18, cfg_m["opacity_edit"], 0, 1,
                                   on_change=self._on_edit_opacity)); cy += 34
        self.widgets.append(Label(px, cy, "Opacity (present)", 12, FG)); cy += 20
        self.widgets.append(Slider(px, cy, pw, 18, cfg_m["opacity_present"], 0, 1,
                                   on_change=self._on_present_opacity)); cy += 44
        self.widgets.append(Label(px, cy, "Zoom", 12, FG)); cy += 20
        self.widgets.append(Slider(px, cy, pw, 18, cfg_m.get("zoom", 1.0), 0.5, 2.5,
                                   on_change=self._on_zoom)); cy += 44
        self.widgets.append(Label(px, cy, "TRACKING", 12, SECTION)); cy += 24
        self.widgets.append(Toggle(px, cy, pw, 22, cfg_t["hands"],
                                   label="Hand Tracking",
                                   on_change=self._on_hands)); cy += 40
        self.widgets.append(Label(px, cy, "Detection confidence", 12, FG)); cy += 20
        self.widgets.append(Slider(px, cy, pw, 18, cfg_t["min_detection_confidence"],
                                   0.1, 0.9, on_change=self._on_confidence)); cy += 20
        self.widgets.append(Label(px, cy, "(restart to apply)", 11,
                                  (0.6, 0.65, 0.75, 1.0))); cy += 24
        self.widgets.append(Label(px, cy, "POSES", 12, SECTION)); cy += 24
        self.widgets.append(Button(px, cy, pw, 26, "Open Pose Library",
                                   on_press=self.app._toggle_pose_library))
        cy += 34
        self.widgets.append(Label(px, cy, "CAMERAS", 12, SECTION))
        cy += 24
        for cam_cfg in self.app.config.data["cameras"]:
            cid = cam_cfg["id"]
            selected = self.app._active_camera_id == cid
            mark = "● " if selected else "   "
            self.widgets.append(Button(
                px, cy, pw, 26, f"{mark}{cid} (idx {cam_cfg['index']})",
                on_press=lambda n=cid: self.app.set_active_camera(n)))
            cy += 32
        cy += 34

        self.widgets.append(Label(px, cy, "CAPTURE ASPECT", 12, SECTION))
        cy += 24
        for label, (cw, ch) in (("16:9  1280×720", (1280, 720)),
                                ("16:9  1920×1080", (1920, 1080)),
                                ("4:3   640×480", (640, 480)),
                                ("4:3   1280×960", (1280, 960))):
            self.widgets.append(Button(
                px, cy, pw, 26, label,
                on_press=lambda ww=cw, hh=ch:
                self.app.set_capture_resolution(ww, hh)))
            cy += 32
        cy += 8

        self.widgets.append(Label(px, cy, "CHIBI LAYERS", 12, SECTION))
        cy += 24
        for layer in self.app.chibi_doc.layers:
            label = layer.name
            if layer.kind == "paint" and layer.strokes:
                label += f"  ·{len(layer.strokes)}p"
            elif not layer.visible:
                label += "  [hidden]"
            if layer.name == self.app.editor.selected_layer_name:
                label = "▸ " + label
            self.widgets.append(Button(
                px, cy, pw, 26, label,
                on_press=lambda n=layer.name: self.app.set_selected_layer_name(n)))
            cy += 32

        self.widgets.append(Label(px, cy, "CHIBI JOINT MAP", 12, SECTION))
        cy += 24
        _JOINT_LABELS = {
            "head": "Head",
            "neck_top": "Neck Top",
            "chest": "Chest",
            "waist": "Waist",
            "shoulder_l": "Shoulder L",
            "shoulder_r": "Shoulder R",
            "elbow_l": "Elbow L",
            "elbow_r": "Elbow R",
            "wrist_l": "Wrist L",
            "wrist_r": "Wrist R",
            "hip_l": "Hip L",
            "hip_r": "Hip R",
            "knee_l": "Knee L",
            "knee_r": "Knee R",
            "ankle_l": "Ankle L",
            "ankle_r": "Ankle R",
        }
        for name, label in _JOINT_LABELS.items():
            self.widgets.append(Button(
                px, cy, pw, 26, label,
                on_press=lambda n=name: self.app.set_selected_joint(n)))
            cy += 32
        self._content_h = cy - self.y

    def _on_edit_opacity(self, v):    self.app.config.data["model"]["opacity_edit"] = v
    def _on_present_opacity(self, v): self.app.config.data["model"]["opacity_present"] = v
    def _on_hands(self, v):           self.app.config.data["tracking"]["hands"] = v
    def _on_zoom(self, v):            self.app.config.data["model"]["zoom"] = v
    def _on_confidence(self, v):      self.app.config.data["tracking"]["min_detection_confidence"] = v
    def _on_bg_color(self, h):        self.app.config.data["scene"]["bg"] = h
    def set_active_camera(self, cid):
        if cid in self.cameras:
            self._active_camera_id = cid
            self._last_frame = self._frames.get(cid)

    def draw(self, r, _w, _h):
        self._draw_shell(r, "CONFIGURATION")
        self._draw_widgets(r)
        self._draw_footer(r, "Presentation Mode")


class Dialog:
    def __init__(self, title, x, y, w, h):
        self.title, self.x, self.y, self.w, self.h = title, x, y, w, h
        self.visible = False
        self.widgets = []

    def show(self): self.visible = True
    def hide(self): self.visible = False

    def draw(self, r, sw, sh):
        if not self.visible: return
        r.rect(0, 0, sw, sh, 0, (0, 0, 0, 0.55))
        r.rect(self.x, self.y, self.w, self.h, 14, (0.14, 0.16, 0.20, 1.0))
        r.text(self.x + 20, self.y + 14, self.title, 14, SECTION)
        self._draw_body(r)

    def _draw_body(self, _r): pass

class _SplitterHit:
    def __init__(self, panel):
        self._panel = panel

    def on_press(self, mx, my):
        self._panel._splitter_dragging = True

    def on_release(self, mx, my):
        self._panel._splitter_dragging = False

    def on_drag(self, mx, my):
        if self._panel._splitter_dragging:
            self._panel._set_splitter_from_y(my)


class _ThumbStrip(Widget):
    """One horizontally scrolling row of emote / expression thumbnails.

    The strip owns its tiles so it can clip them to its own rect: the
    properties area scrolls vertically, and a sideways row inside it has
    to scroll on its own axis without bleeding over the panel. The scroll
    offset lives on the panel, so a rebuild keeps the row where the user
    left it.
    """

    TILE_W = 56
    TILE_H = 48
    GAP = 4

    def __init__(self, x, y, w, h, panel, kind, tiles):
        super().__init__(x, y, w, h)
        self.panel = panel
        self.kind = kind                  # "emote" | "expression"
        self.tiles: list[tuple] = tiles    # (label, entry) — entry None = ＋

    # ---- geometry ----------------------------------------------------

    @property
    def scroll_x(self) -> float:
        return float(self.panel._strip_scroll.get(self.kind, 0.0))

    @scroll_x.setter
    def scroll_x(self, value: float) -> None:
        self.panel._strip_scroll[self.kind] = max(
            0.0, min(self.max_scroll(), float(value)))

    def content_w(self) -> float:
        return len(self.tiles) * (self.TILE_W + self.GAP)

    def max_scroll(self) -> float:
        return max(0.0, self.content_w() - self.w)

    def scroll(self, dy) -> bool:
        before = self.scroll_x
        self.scroll_x = before - dy * SCROLL_STEP * 2.0
        return self.scroll_x != before

    def _origin(self):
        return (self.x + self._render_dx, self.y + self._render_dy)

    def _tile_x(self, index: int) -> float:
        return (self._origin()[0] + index * (self.TILE_W + self.GAP)
                - self.scroll_x)

    def tile_at(self, mx, my):
        """(label, entry) of the tile under the pointer, if any."""
        bx, by = self._origin()
        if not (bx <= mx <= bx + self.w and by <= my <= by + self.h):
            return None
        index = int((mx - bx + self.scroll_x) // (self.TILE_W + self.GAP))
        if not 0 <= index < len(self.tiles):
            return None
        tx = self._tile_x(index)
        if not (tx <= mx <= tx + self.TILE_W):
            return None           # landed in the gap between two tiles
        return self.tiles[index]

    # ---- events -------------------------------------------------------

    def on_press(self, mx, my):
        tile = self.tile_at(mx, my)
        if tile is None:
            return
        _label, entry = tile
        app = self.panel.app
        if entry is None:
            if self.kind == "emote":
                app._capture_current_as_pose()
            else:
                app.capture_current_expression()
        elif self.kind == "emote":
            app._activate_pose_by_name(entry.name)
        else:
            app.apply_expression(entry.name)

    def context_items(self, mx, my) -> list:
        tile = self.tile_at(mx, my)
        if tile is None or tile[1] is None:
            return []
        menu = getattr(self.panel.app, "strip_tile_menu", None)
        return list(menu(self.kind, tile[1]) or []) if menu else []

    # ---- drawing ------------------------------------------------------

    def _is_active(self, entry) -> bool:
        if entry is None:
            return False
        app = self.panel.app
        if self.kind == "emote":
            # Idles live in the base pose, not in active_pose, so the
            # highlight has to ask what the chibi is actually wearing.
            current = getattr(app, "current_pose", None)
            if callable(current):
                return current() is entry
            return getattr(app.model, "active_pose", None) is entry
        return getattr(app, "active_expression", None) is entry

    def draw(self, r):
        bx, by = self._origin()
        if self.w <= 0 or self.h <= 0:
            return
        r.push_clip(bx, by, self.w, self.h)
        mx, my = getattr(self.panel.app, "_mouse", (0.0, 0.0))
        for i, (_label, entry) in enumerate(self.tiles):
            tx = self._tile_x(i)
            if tx + self.TILE_W < bx or tx > bx + self.w:
                continue
            hover = (tx <= mx <= tx + self.TILE_W
                     and by <= my <= by + self.TILE_H)
            self._draw_tile(r, tx, by, entry, hover,
                            self._is_active(entry))
        r.pop_clip()
        # A faint chevron on the edge says the row keeps going.
        if self.max_scroll() > 0.0:
            if self.scroll_x > 0.0:
                r.text(bx + 2, by + self.TILE_H * 0.5 - 7, "‹", 11,
                       (0.75, 0.82, 0.95, 0.8))
            if self.scroll_x < self.max_scroll():
                r.text(bx + self.w - 8, by + self.TILE_H * 0.5 - 7, "›", 11,
                       (0.75, 0.82, 0.95, 0.8))

    def _draw_tile(self, r, tx, ty, entry, hover, active) -> None:
        bg = (0.22, 0.28, 0.38, 1.0) if active else (
            (0.20, 0.24, 0.31, 1.0) if hover else (0.16, 0.18, 0.23, 1.0))
        r.rect(tx, ty, self.TILE_W, self.TILE_H, 6, bg)
        inner_w, inner_h = self.TILE_W - 2, self.TILE_H - 14
        thumb = getattr(entry, "thumb", "") if entry is not None else ""
        tex = r.load_texture(thumb) if thumb else None
        if tex is not None:
            handle, iw, ih = tex
            scale = max(inner_w / max(1, iw), inner_h / max(1, ih))
            r.textured_quad(tx + 1 + (inner_w - iw * scale) * 0.5,
                            ty + 1 + (inner_h - ih * scale) * 0.5,
                            iw * scale, ih * scale, texture=handle)
        else:
            glyph = "＋" if entry is None else "?"
            colour = ((0.70, 0.80, 0.95, 1.0) if entry is None
                      else (0.45, 0.52, 0.66, 1.0))
            r.text(tx + self.TILE_W * 0.5 - 5, ty + 8, glyph, 15, colour)
        if hover:
            r.rect(tx, ty, self.TILE_W, self.TILE_H, 6, (1.0, 1.0, 1.0, 0.08))
        if active:
            for (ox, oy, ow, oh) in ((tx, ty, self.TILE_W, 2),
                                     (tx, ty + self.TILE_H - 2, self.TILE_W, 2),
                                     (tx, ty, 2, self.TILE_H),
                                     (tx + self.TILE_W - 2, ty, 2, self.TILE_H)):
                r.rect(ox, oy, ow, oh, 1, (0.30, 0.68, 1.00, 1.0))
        if entry is not None:
            label = entry.name
            max_chars = max(3, int((self.TILE_W - 6) / 5.4))
            if len(label) > max_chars:
                label = label[:max_chars - 1] + "\u2026"
            r.text(tx + 3, ty + self.TILE_H - 13, label, 10,
                   (0.95, 0.98, 1.0, 1.0) if active
                   else (0.80, 0.86, 0.98, 1.0))


class LayerPanel(_PanelBase):
    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)
        self.split_ratio = 0.42
        self._splitter_dragging = False
        # Until the splitter is dragged it follows the tool-properties
        # content instead of a fixed fraction of the panel.
        self._splitter_dragged = False
        self._props_widgets: list[Widget] = []
        self._props_content_h = 0.0
        self._props_scroll_y = 0.0
        self._strips: dict[str, "_ThumbStrip"] = {}
        self._strip_scroll: dict[str, float] = {"emote": 0.0,
                                                "expression": 0.0}

    def _ed(self):
        """The editor this panel inspects: chibi, scene or prop."""
        active = getattr(self.app, "_active_editor", None)
        if callable(active):
            ed = active()
            if ed is not None:
                return ed
        return getattr(self.app, "editor", None)

    def _layer_ed(self):
        """The editor whose layers this panel lists.

        The Props chip points at the active scene — props are scene
        layers, not chibi paint layers — so the list and the selection
        come from there while the chibi editor keeps the tools.
        """
        layer_ed = getattr(self.app, "_layer_editor", None)
        if callable(layer_ed):
            return layer_ed()
        return self._ed()

    def _splitter_y(self):
        if self.collapsed:
            return self.y + HEADER_H
        top = self.y + 40
        total = self.h - 40 - FOOTER_H
        if self._splitter_dragged:
            return top + total * self.split_ratio
        # No dead space: the tool properties take exactly what they need
        # (floored so the strips never collapse away).
        wanted = max(self._props_content_h + 10.0, 140.0)
        return top + min(total * self.split_ratio, wanted)

    def _props_viewport(self):
        sy = self._splitter_y()
        return (self.x, self.y + 40, self.w, max(0.0, sy - (self.y + 40)))

    def _layers_viewport(self):
        sy = self._splitter_y()
        return (self.x, sy + 6, self.w,
                max(0.0, self.y + self.h - FOOTER_H - sy - 6))

    def _set_splitter_from_y(self, my):
        top = self.y + 40
        total = max(1.0, self.h - 40 - FOOTER_H)
        rel = my - top
        self.split_ratio = max(0.10, min(0.90, rel / total))
        # Once the user has chosen a split, the content no longer moves it.
        self._splitter_dragged = True
        self.rebuild()

    def footer_action(self):
        ed = self._layer_ed()
        if ed is not None and ed.selected_layer() is not None:
            self.app.editor_deselect()
        else:
            self.app.editor_active = False

    def on_scroll(self, dy, mx, my):
        if self.collapsed:
            return False
        # A thumbnail strip scrolls sideways, even inside the vertical
        # properties viewport it is drawn in.
        for strip in self._strips.values():
            if strip.contains(mx, my):
                strip.scroll(dy)
                return True
        vx, vy, vw, vh = self._props_viewport()
        if vx <= mx <= vx + vw and vy <= my <= vy + vh:
            self._props_scroll_y = max(
                0.0, min(self._props_max_scroll(),
                         self._props_scroll_y - dy * SCROLL_STEP))
            return True
        vx, vy, vw, vh = self._layers_viewport()
        if vx <= mx <= vx + vw and vy <= my <= vy + vh:
            self.scroll_y = max(0.0, min(self.max_scroll(),
                                         self.scroll_y - dy * SCROLL_STEP))
            return True
        return False

    def _props_max_scroll(self):
        return max(0.0, self._props_content_h - self._props_viewport()[3])

    def max_scroll(self):
        if self.collapsed:
            return 0.0
        _, _, _, vh = self._layers_viewport()
        return max(0.0, self._content_h - vh)

    def rebuild(self):
        self._snapshot_build_origin()
        self.widgets = []
        self._props_widgets = []
        self.scroll_y = 0.0
        self._props_scroll_y = 0.0
        ed = self._ed()
        if ed is None:
            self._content_h = 0.0
            self._props_content_h = 0.0
            return

        px = self.x + 20
        pw = self.w - 80
        self._build_tool_props(px, pw)

        led = self._layer_ed()
        if led is None:
            self._content_h = 0.0
            return
        cy = self._splitter_y() + 8
        L = led.selected_layer()
        if L is None:
            self._build_list(px, pw, cy, ed, led)
        else:
            self._build_details(px, pw, cy, L, led)
        self._finalize_layout(set_content=False)

    # ---- tool properties
    def _build_tool_props(self, px, pw):
        ed = self._ed()
        # Strips are rebuilt from scratch: leaving a stale one behind would
        # keep the poses clickable while the prop canvas is up.
        self._strips = {}
        if ed is None:
            self._props_content_h = 0.0
            return
        tool = ed.active.name if ed.active else "select"
        cy = self.y + 46
        self._props_widgets.append(Label(px, cy, f"TOOL · {tool.upper()}", 12, SECTION))
        cy += 22

        if tool in ("brush", "pen"):
            self._props_widgets.append(Label(px, cy, "Color", 11, FG)); cy += 18
            self._props_widgets.append(ColorPicker(
                px, cy, pw - 110, 18, ed.primary_color,
                on_change=self._on_primary_color))
            cy += 112

        if tool in ("brush", "eraser", "blur"):
            attr = "eraser_size" if tool == "eraser" else "brush_size"
            self._props_widgets.append(NumericSlider(
                px, cy, pw, 30, getattr(ed, attr), 0.005, 0.20,
                "Size", on_change=lambda v, a=attr: setattr(ed, a, v)))
            cy += 52

        if getattr(self.app, "prop_edit_active", False):
            # Same panel, different document: the chibi's emotes and faces
            # do not apply to a prop, so the prop's own controls take the
            # space instead of being buried under them.
            self._build_prop_props(px, cy, pw, ed)
            return

        self._props_widgets.append(Button(
            px, cy, pw, 22, "Expressions ▸",
            on_press=self.app._toggle_expression_panel))
        cy += 26

        # ---- Emotes: poses that play and release, one scrolling row.
        self._props_widgets.append(Label(px, cy, "EMOTES", 11, FG))
        cy += 16
        self._strips["emote"] = _ThumbStrip(
            px, cy, pw, _ThumbStrip.TILE_H, self, "emote",
            self._emote_tiles())
        self._props_widgets.append(self._strips["emote"])
        cy += _ThumbStrip.TILE_H + 8

        # ---- Expressions: the saved faces.
        self._props_widgets.append(Label(px, cy, "EXPRESSIONS", 11, FG))
        cy += 16
        self._strips["expression"] = _ThumbStrip(
            px, cy, pw, _ThumbStrip.TILE_H, self, "expression",
            self._expression_tiles())
        self._props_widgets.append(self._strips["expression"])
        cy += _ThumbStrip.TILE_H + 8

        if tool == "select":
            self._props_widgets.append(Label(
                px, cy, "Click a layer below to inspect its properties.", 11, FG))
            cy += 20

        self._props_content_h = max(0.0, cy - (self.y + 40))

    def _build_prop_props(self, px, cy, pw, ed):
        """The prop document's own controls, above the layer list."""
        doc = ed.doc
        name = str(getattr(doc, "name", "") or "Prop")
        self._props_widgets.append(Label(
            px, cy, f"PROP · {name.upper()}", 12, SECTION))
        cy += 22
        self._props_widgets.append(Label(
            px, cy, "Name (Enter to rename)", 11, FG))
        cy += 18
        self._props_widgets.append(TextInput(
            px, cy, pw, 26, text=name, placeholder="Prop name",
            on_submit=self._rename_prop))
        cy += 32
        half = (pw - 4) / 2
        self._props_widgets.append(Button(
            px, cy, half, 24, "＋ Layer",
            on_press=self.app.prop_add_layer))
        self._props_widgets.append(Button(
            px + half + 4, cy, half, 24, "Apply",
            on_press=self.app.apply_prop_changes))
        cy += 30
        self._props_widgets.append(Label(
            px, cy, "Only this prop draws here:", 10, FG))
        cy += 16
        self._props_widgets.append(Label(
            px, cy, "scene and chibi stay hidden.", 10, FG))
        cy += 22
        self._props_widgets.append(Button(
            px, cy, pw, 22, "Close Prop Editor",
            on_press=self.app.exit_prop_edit))
        cy += 26
        self._props_content_h = max(0.0, cy - (self.y + 40))

    def _rename_prop(self, text):
        """Rename the working prop; Apply commits it to the library."""
        ed = self._ed()
        name = (text or "").strip()
        if ed is None or not name or name == getattr(ed.doc, "name", ""):
            self.rebuild()
            return
        ed.doc.name = name
        self.rebuild()
        notify = getattr(self.app, "_show_toast", None)
        if notify is not None:
            notify(f"Prop renamed to '{name}' — Apply to keep it")

    def _emote_tiles(self) -> list[tuple]:
        """(label, entry) pairs for the emote strip; ``None`` is its ＋.

        The idles are stances rather than timed emotes, but they are the
        poses a user reaches for most, so they follow the emotes here.
        """
        lib = getattr(self.app, "pose_library", None)
        poses = [p for p in (getattr(lib, "poses", None) or ())
                 if p.category == "Emote" or p.is_idle]
        tiles = [(p.name, p) for p in sorted(
            poses, key=lambda p: (bool(p.is_idle), p.name))]
        tiles.append(("＋", None))
        return tiles

    def _expression_tiles(self) -> list[tuple]:
        """(label, entry) pairs for the expression strip."""
        lib = getattr(self.app, "expression_library", None)
        faces = sorted(getattr(lib, "poses", None) or (),
                       key=lambda e: e.name)
        tiles = [(e.name, e) for e in faces]
        tiles.append(("＋", None))
        return tiles

    def strip_rects(self) -> dict[str, tuple]:
        """On-screen rect of each strip (valid after one draw)."""
        return {kind: (s.x + s._render_dx, s.y + s._render_dy, s.w, s.h)
                for kind, s in self._strips.items()}

    def context_items_for(self, mx, my) -> list:
        """Right-click entries for the strip tile under the pointer."""
        if self.collapsed:
            return []
        vx, vy, vw, vh = self._props_viewport()
        if not (vx <= mx <= vx + vw and vy <= my <= vy + vh):
            return []
        for strip in self._strips.values():
            items = strip.context_items(mx, my)
            if items:
                return items
        return []

    def _on_primary_color(self, hex_str):
        ed = self._ed()
        if ed is not None:
            ed.primary_color = hex_str

    # ---- layers
    def _build_list(self, px, pw, cy, ed, led=None):
        led = led if led is not None else ed
        prop_mode = bool(getattr(self.app, "prop_edit_active", False))
        # The Props chip reaches into the active scene: props are scene
        # layers, so that group lists the scene document unfiltered.
        scene_mode = (not prop_mode
                      and led is getattr(self.app, "scene_editor", None))
        if prop_mode:
            self.widgets.append(Label(px, cy, "PROP LAYERS", 12, SECTION))
            cy += 22
        else:
            # Four groups, two chips per row: the names stay readable at
            # the panel's narrow width.
            groups = (("base", "Base"), ("clothes", "Clothes"),
                      ("accessories", "Accessories"), ("props", "Props"))
            gw = (pw - 4) / 2
            for i, (g, title) in enumerate(groups):
                sel = ed.current_group == g
                mark = "● " if sel else ""
                self.widgets.append(Button(
                    px + (i % 2) * (gw + 4), cy + (i // 2) * 28, gw, 24,
                    mark + title,
                    on_press=lambda n=g: self.app._set_editor_group(n)))
            cy += 28 * ((len(groups) + 1) // 2) + 2
            self.widgets.append(Label(
                px, cy, "SCENE PROPS" if scene_mode else "LAYERS",
                12, SECTION))
            cy += 22
        self.widgets.append(Label(px, cy, "Click to select", 10, FG)); cy += 20

        if prop_mode or scene_mode:
            # A prop has a single group of its own, and a scene's prop
            # layers all live in it, so nothing is filtered.
            layers = sorted(led.doc.layers, key=lambda x: x.z, reverse=True)
        else:
            layers = sorted(
                [L for L in led.doc.layers
                 if getattr(L, "group", "base") == ed.current_group],
                key=lambda x: x.z, reverse=True)

        for layer in layers:
            label = layer.name
            if not layer.visible:
                label += "  [hidden]"
            if getattr(layer, "in_front", False):
                label += "  [front]"
            if (prop_mode or scene_mode) and getattr(layer, "anchor", None):
                label += f"  →{layer.anchor}"
            if layer.strokes:
                label += f"  ·{len(layer.strokes)}p"
            is_selected = (layer.name == led.selected_layer_name)
            if is_selected:
                label = "▸ " + label
            self.widgets.append(Button(
                px, cy, pw, 24, label,
                on_press=lambda n=layer.name: self.app._select_layer(n)))
            cy += 28
        if scene_mode and not layers:
            self.widgets.append(Label(
                px, cy, "This scene has no props yet.", 10, FG))
            cy += 20
        self._content_h = cy - (self._splitter_y() + 8)

    def _build_details(self, px, pw, cy, L, led=None):
        led = led if led is not None else self._layer_ed()
        prop_mode = bool(getattr(self.app, "prop_edit_active", False))
        scene_mode = (not prop_mode
                      and led is getattr(self.app, "scene_editor", None))
        self.widgets.append(Label(px, cy, f"LAYER: {L.name}", 14, SECTION)); cy += 24
        self.widgets.append(Button(px, cy, pw, 24, "◀ Back to list",
                                   on_press=lambda: self.app.editor_deselect()))
        cy += 30
        self.widgets.append(Label(px, cy, "Name (Enter to rename)", 11, FG))
        cy += 18
        self.widgets.append(TextInput(
            px, cy, pw, 26, text=L.name, placeholder="Layer name",
            on_submit=lambda txt, o=L: self._rename_layer(o, txt)))
        cy += 32
        self.widgets.append(Button(px, cy, pw, 24, "Duplicate Layer",
                                   on_press=lambda o=L: self._duplicate_layer(o)))
        cy += 30
        self.widgets.append(Label(px, cy, f"kind: {L.kind}", 12, FG)); cy += 22
        self.widgets.append(Label(px, cy, f"group: {getattr(L, 'group', 'base')}",
                                  10, FG)); cy += 22
        self.widgets.append(Toggle(px, cy, pw, 22, L.visible, label="Visible",
                                   on_change=lambda v, o=L: setattr(o, "visible", v)))
        cy += 36
        self._add_number(px, cy, pw, "Opacity", L, "opacity", 0.0, 1.0); cy += 52
        self._add_number(px, cy, pw, "Width", L, "w", 0.005, 0.60); cy += 52
        self._add_number(px, cy, pw, "Height", L, "h", 0.005, 0.60); cy += 52
        self._add_number(px, cy, pw, "Length Scale", L, "length_scale", 0.10, 2.00); cy += 52
        self._add_number(px, cy, pw, "Corner R", L, "r", 0.0, 0.30); cy += 52
        self._add_number(px, cy, pw, "Rotation", L, "rotation", -3.14, 3.14); cy += 52
        self._add_number(px, cy, pw, "Offset X", L, "dx", -0.30, 0.30); cy += 52
        self._add_number(px, cy, pw, "Offset Y", L, "dy", -0.30, 0.30); cy += 52
        self._add_number(px, cy, pw, "Z-order", L, "z", -50, 50); cy += 52
        if L.strokes:
            self.widgets.append(Label(px, cy, f"strokes: {len(L.strokes)}", 11, FG)); cy += 20
            self.widgets.append(Button(px, cy, pw, 24, "Clear Strokes",
                                       on_press=lambda: self._clear_paint(L)))
            cy += 30
        if L.kind == "image":
            self.widgets.append(Label(px, cy, f"png: {Path(L.texture_path).name if L.texture_path else '(none)'}",
                                      10, FG)); cy += 22

        if L.kind == "hand":
            self._add_number(px, cy, pw, "Palm Scale",
                             L, "hand_palm_scale", 0.30, 2.00)
            cy += 52
            self._add_number(px, cy, pw, "Finger Width",
                             L, "hand_finger_width", 0.30, 2.00)
            cy += 52
            self._add_number(px, cy, pw, "Finger Length",
                             L, "hand_finger_length", 0.30, 2.00)
            cy += 52
            self._add_number(px, cy, pw, "Palm Offset X",
                             L, "hand_palm_dx", -0.20, 0.20)
            cy += 52
            self._add_number(px, cy, pw, "Palm Offset Y",
                             L, "hand_palm_dy", -0.20, 0.20)
            cy += 52
            self._add_number(px, cy, pw, "Hand Rotation (deg)",
                             L, "hand_rotation", -180.0, 180.0,
                             fmt="{:+.1f}°")
            cy += 52
            self._add_number(px, cy, pw, "Palm Rotation (deg)",
                             L, "hand_palm_rotation", -180.0, 180.0,
                             fmt="{:+.1f}°")
            cy += 52
            if not prop_mode and not scene_mode:
                cy = self._finger_block(px, pw, cy, L)

        for label, i in (("R", 0), ("G", 1), ("B", 2)):
            self.widgets.append(Label(px, cy, f"Fill {label}", 12, FG)); cy += 20
            self._add_rgb_slider(px, cy, pw, L, i); cy += 26
        cy += 10
        if scene_mode:
            cy = prop_anchor_widgets(self.widgets, self.app, L, px, pw, cy)
        half = (pw - 4) / 2
        self.widgets.append(Button(px, cy, half, 26,
                                   "Hide" if L.visible else "Show",
                                   on_press=lambda o=L: self._toggle_hidden(o)))
        self.widgets.append(Button(px + half + 4, cy, half, 26, "Clear",
                                   on_press=lambda o=L: self._clear_layer(o)))
        cy += 34
        if prop_mode:
            # Authored in place: the stage can stay behind the art, so the
            # prop is judged where it actually hangs.
            ctx = getattr(self.app, "prop_context", None)
            if ctx is not None:
                self.widgets.append(Toggle(
                    px, cy, pw, 22, bool(ctx()),
                    label="Show stage behind",
                    on_change=lambda v: getattr(
                        self.app, "toggle_prop_context", lambda: None)()))
                cy += 30
            # A prop layer exists only in the prop document, so unlike a
            # chibi part it can be removed outright.
            self.widgets.append(Button(px, cy, pw, 26, "Apply to Library",
                                       on_press=self.app.apply_prop_changes))
            cy += 34
            self.widgets.append(Button(
                px, cy, pw, 26, "Delete Layer",
                on_press=lambda n=L.name: self.app.prop_delete_layer(n)))
            cy += 34
        elif scene_mode:
            # A scene prop is its own layer: it keys on the scene's
            # layer track and its art is authored in the prop editor.
            self.widgets.append(Button(
                px, cy, pw, 26, "Prop Edit…",
                on_press=lambda n=L.name: getattr(
                    self.app, "edit_scene_prop", lambda *a: None)(n)))
            cy += 34
            self.widgets.append(Button(
                px, cy, pw, 26, "Key Transform @ Playhead",
                on_press=lambda n=L.name: self.app.timeline_key_layer(n)))
            cy += 34
            self.widgets.append(Button(
                px, cy, pw, 26, "Delete Prop Layer",
                on_press=lambda n=L.name: self.app.delete_scene_layer(n)))
            cy += 34
        else:
            # Chibi layers are never deleted, only cleared or hidden: their
            # names anchor poses, timeline tracks and joint links.
            self.widgets.append(Button(px, cy, pw, 26, "Key Pose @ Playhead",
                                       on_press=self.app.timeline_key_current_pose))
            cy += 34
            self.widgets.append(Button(px, cy, pw, 26, "Reset Chibi to Default",
                                       on_press=self.app._reset_chibi))
            cy += 34
            self.widgets.append(Button(px, cy, pw, 26, "Save PNG (transparent)",
                                       on_press=self.app.export_object_png))
            cy += 34

        self._content_h = cy - (self._splitter_y() + 8)

    def _finger_block(self, px, pw, cy, L):
        """Finger-pose controls for one hand layer.

        The rings only exist while the joint tool is on, so the block says
        how to get there instead of pretending to be the only way in. The
        quick buttons only edit the pending drags, which is what Apply
        then writes into the pose — the same path a dragged ring takes.
        """
        enter = getattr(self.app, "enter_finger_edit", None)
        if enter is None:
            return cy
        active = getattr(self.app, "finger_edit", None) or {}
        posing = active.get("layer") == L.name
        self.widgets.append(Label(px, cy, "FINGERS", 12, SECTION)); cy += 22
        self.widgets.append(Label(
            px, cy,
            "Drag a ring, or right-click the palm" if posing
            else "Joint tool [J], then right-click the palm",
            10, FG))
        cy += 20
        stop = getattr(self.app, "exit_finger_edit", None)
        self.widgets.append(Button(
            px, cy, pw, 24, "Done with fingers" if posing else "Pose fingers",
            on_press=((lambda: stop()) if posing and stop is not None
                      else (lambda o=L: enter(o)))))
        cy += 30
        # Two quick poses and two bookkeeping actions, two per row.
        pairs = (("Curl", "curl_fingers"), ("Spread", "spread_fingers"),
                 ("Reset hand", "reset_fingers"),
                 ("Mirror", "mirror_fingers"))
        half = (pw - 4) / 2
        for i in range(0, len(pairs), 2):
            row = pairs[i:i + 2]
            for j, (label, attr) in enumerate(row):
                cb = getattr(self.app, attr, None)
                self.widgets.append(Button(
                    px + j * (half + 4), cy, half, 24, label,
                    on_press=(lambda f=cb: f()) if cb is not None
                    else (lambda: None)))
            cy += 28
        return cy

    def _add_number(self, x, y, w, name, obj, attr, lo, hi, fmt="{:.2f}"):
        cur = float(getattr(obj, attr))

        def cb(v, o=obj, a=attr): setattr(o, a, v)

        self.widgets.append(NumericSlider(x, y, w, 30, cur, lo, hi,
                                          name, on_change=cb, fmt=fmt))

    def _rename_layer(self, L, text):
        new_name = (text or "").strip()
        ed = self._layer_ed()
        if (ed is None or not new_name or new_name == L.name
                or ed.doc.find(new_name) is not None):
            self.rebuild()
            return
        ed.push_undo(ed.doc)
        L.name = new_name
        ed.selected_layer_name = new_name
        self.rebuild()

    def _duplicate_layer(self, L):
        import copy
        ed = self._layer_ed()
        if ed is None:
            return
        ed.push_undo(ed.doc)
        new_L = copy.deepcopy(L)
        base = f"{L.name}_copy"
        name = base
        n = 1
        while ed.doc.find(name) is not None:
            name = f"{base}{n}"
            n += 1
        new_L.name = name
        new_L.z = max((x.z for x in ed.doc.layers), default=0) + 1
        ed.doc.layers.append(new_L)
        ed.doc.sort_layers()
        ed.selected_layer_name = name
        self.rebuild()

    def _toggle_hidden(self, L):
        L.visible = not L.visible
        self.app.layer_panel.rebuild()

    def _clear_layer(self, L):
        L.visible = False
        L.w = 0.001
        L.h = 0.001
        L.opacity = 0.0
        L.points = []
        L.strokes = []
        L.stroke_colors = []
        self.app.layer_panel.rebuild()

    def _clear_paint(self, L):
        L.strokes = []
        L.stroke_colors = []
        self.app.layer_panel.rebuild()

    def _add_rgb_slider(self, x, y, w, L, channel):
        import scene
        cur = scene.hex_to_rgba(L.fill) if L.fill != "none" else (1.0, 1.0, 1.0, 1.0)
        def cb(v, o=L, c=channel):
            import scene as _s
            base = _s.hex_to_rgba(o.fill) if o.fill != "none" else (1.0, 1.0, 1.0, 1.0)
            rgb = list(base[:3])
            rgb[c] = v
            o.fill = "#{:02X}{:02X}{:02X}".format(
                int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255))
        self.widgets.append(Slider(x, y, w, 18, cur[channel], 0.0, 1.0, on_change=cb))




    # ---- draw / hit-test
    def _draw_widgets(self, r):
        if self.collapsed:
            return
        dx = self.x - self._build_x
        dy = self.y - self._build_y

        # Tool properties
        vx, vy, vw, vh = self._props_viewport()
        if vh > 0:
            r.push_clip(vx, vy, vw, vh)
            for w in self._props_widgets:
                w._render_dx = dx
                w._render_dy = dy - self._props_scroll_y
                sy = w.y + w._render_dy
                if sy + w.h < vy: continue
                if sy > vy + vh: continue
                w.draw(r)
            r.pop_clip()

        # Splitter bar
        sy = self._splitter_y()
        r.rect(self.x + 8, sy - 1.5, self.w - 16, 3, 1.5, (0.35, 0.42, 0.55, 1.0))

        # Layers
        vx, vy, vw, vh = self._layers_viewport()
        if vh > 0:
            r.push_clip(vx, vy, vw, vh)
            for w in self.widgets:
                w._render_dx = dx
                w._render_dy = dy - self.scroll_y
                sy = w.y + w._render_dy
                if sy + w.h < vy: continue
                if sy > vy + vh: continue
                w.draw(r)
            r.pop_clip()
            self._draw_scrollbar_for(r, vx, vy, vw, vh, self.scroll_y,
                                     self._content_h)

    def _draw_scrollbar_for(self, r, vx, vy, vw, vh, scroll_y, content_h):
        max_s = max(0.0, content_h - vh)
        if max_s <= 0: return
        track_x = vx + vw - 6
        r.rect(track_x, vy, 4, vh, 2, (0.22, 0.24, 0.30, 1.0))
        thumb_h = max(24, vh * vh / max(1.0, content_h))
        thumb_y = vy + (vh - thumb_h) * (scroll_y / max_s)
        r.rect(track_x, thumb_y, 4, thumb_h, 2, (0.45, 0.55, 0.75, 1.0))

    def hit_test(self, mx, my):
        if self.collapsed:
            return None
        fx, fy, fw, fh = self._footer_rect()
        if fx <= mx <= fx + fw and fy <= my <= fy + fh:
            return _FooterHit(self)
        dx = self.x - self._build_x
        dy = self.y - self._build_y
        # Tool properties (sliders, strip tiles) live above the splitter.
        vx, vy, vw, vh = self._props_viewport()
        if vx <= mx <= vx + vw and vy <= my <= vy + vh:
            for w in self._props_widgets:
                w._render_dx = dx
                w._render_dy = dy - self._props_scroll_y
                if w.contains(mx, my):
                    return w
            return None
        vx, vy, vw, vh = self._viewport()
        if not (vx <= mx <= vx + vw and vy <= my <= vy + vh):
            return None
        content_mx = mx - dx
        content_my = my - dy + self.scroll_y
        for w in self.widgets:
            if (w.x <= content_mx <= w.x + w.w
                    and w.y <= content_my <= w.y + w.h):
                w._scroll_offset = self.scroll_y
                w._render_dx = dx
                w._render_dy = dy - self.scroll_y
                return w
        return None

    def draw(self, r, _w, _h):
        ed = self._layer_ed()
        if ed is None: return
        self._draw_shell(r, "LAYER")
        self._draw_widgets(r)
        label = "Close" if ed.selected_layer() is not None else "Hide"
        self._draw_footer(r, label)


class _PoseTile(Widget):
    """One pose in the library grid: snapshot thumbnail + name.

    Poses store a snapshot taken the last time they were applied in the
    chibi editor. Until one exists the tile shows a placeholder so the
    grid keeps its shape and the gap is obvious.
    """

    THUMB_H = 72
    PAD = 4

    def __init__(self, x, y, w, h, pose, panel):
        super().__init__(x, y, w, h)
        self.pose = pose
        self.panel = panel
        self.hover = False

    def on_press(self, mx, my):
        self.panel.app._activate_pose_by_name(self.pose.name)

    def on_release(self, mx, my): pass
    def on_drag(self, mx, my): pass

    def _is_active(self) -> bool:
        current = self.panel.app._active_library_pose()
        return current is not None and current.name == self.pose.name

    def _painter(self):
        return self.panel.app

    def draw(self, r):
        bx = self.x + self._render_dx
        by = self.y + self._render_dy
        mx, my = getattr(self._painter(), "_mouse", (0.0, 0.0))
        self.hover = (bx <= mx <= bx + self.w and by <= my <= by + self.h)
        active = self._is_active()
        if active:
            bg = (0.22, 0.28, 0.38, 1.0)
        elif self.hover:
            bg = (0.20, 0.24, 0.31, 1.0)
        else:
            bg = (0.16, 0.18, 0.23, 1.0)
        r.rect(bx, by, self.w, self.h, 8, bg)

        pad = self.PAD
        tw, th = self.w - pad * 2, self.THUMB_H
        tx, ty = bx + pad, by + pad
        tex = r.load_texture(self.pose.thumb) if self.pose.thumb else None
        if tex is not None:
            handle, iw, ih = tex
            r.push_clip(tx, ty, tw, th)
            scale = max(tw / max(1, iw), th / max(1, ih))
            dw, dh = iw * scale, ih * scale
            r.textured_quad(tx + (tw - dw) * 0.5, ty + (th - dh) * 0.5,
                            dw, dh, texture=handle)
            r.pop_clip()
        else:
            r.rect(tx, ty, tw, th, 6, (0.11, 0.12, 0.16, 1.0))
            r.text(tx + tw * 0.5 - 5, ty + th * 0.5 - 16, "?", 20,
                   (0.45, 0.52, 0.66, 1.0))
            r.text(tx + 6, ty + th - 16, "no snapshot", 10,
                   (0.42, 0.48, 0.60, 1.0))
        if self.hover:
            r.rect(tx, ty, tw, th, 6, (1.0, 1.0, 1.0, 0.08))
        if active:
            # A bright frame marks the pose that is currently playing.
            for (ox, oy, ow, oh) in ((bx, by, self.w, 2),
                                     (bx, by + self.h - 2, self.w, 2),
                                     (bx, by, 2, self.h),
                                     (bx + self.w - 2, by, 2, self.h)):
                r.rect(ox, oy, ow, oh, 1, (0.30, 0.68, 1.00, 1.0))

        label = self.pose.name
        max_chars = max(4, int((self.w - pad * 2) / 6.2))
        if len(label) > max_chars:
            label = label[:max_chars - 1] + "\u2026"
        r.text(bx + pad + 1, by + self.h - 20, label, 12,
               (0.95, 0.98, 1.0, 1.0) if active else (0.80, 0.86, 0.98, 1.0))


class PoseLibraryPanel(_PanelBase):
    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)

    def footer_action(self):
        self.app.pose_library_open = False
        self.app._layout_dirty = True

    def rebuild(self):
        self._snapshot_build_origin()
        self.widgets = []
        self.scroll_y = 0.0
        lib = getattr(self.app, "pose_library", None)
        if lib is None:
            self._content_h = 0.0; return
        px, pw = self.x + 20, self.w - 80
        cy = self.y + 48
        self.widgets.append(Label(px, cy, "POSE LIBRARY", 14, SECTION)); cy += 24
        self.widgets.append(Button(px, cy, pw, 26, "＋ Capture Current",
                                   on_press=self.app._capture_current_as_pose)); cy += 34
        self.widgets.append(Button(px, cy, pw, 26, "＋ Capture as Idle (current framing)",
                                   on_press=self.app._capture_as_idle)); cy += 34
        active = self.app._active_library_pose()
        if active is not None:
            # Deliberately not SECTION-coloured: `_apply_sections` uses
            # that colour to recognise fold-out markers.
            self.widgets.append(Label(px, cy, f"Active: {active.name}", 12,
                                      (0.62, 0.76, 0.98, 1.0))); cy += 22
            self.widgets.append(NumericSlider(px, cy, pw, 30, active.duration_s, 0.0, 10.0,
                                              "Duration",
                                              on_change=lambda v, p=active: setattr(p, "duration_s", v)))
            cy += 52
            # NEW — ADSR-style blend timing
            from ui.widgets import EnvelopeEditor
            env = EnvelopeEditor(
                px, cy, pw, active.duration_s * 1000.0,
                active.attack_ms, active.release_ms,
                on_attack=lambda v, p=active: setattr(p, "attack_ms", v),
                on_release=lambda v, p=active: setattr(p, "release_ms", v))
            self.widgets.append(env)
            cy += env.h + 8

            # cy += 52
            if not active.is_idle:
                self.widgets.append(Button(px, cy, pw, 24, "Restart Timer",
                                           on_press=lambda n=active.name:
                                               self.app._activate_pose_by_name(n)))
                cy += 30
            cy += 8
        # Whether a held pose gives way when the user actually moves.
        # Release is per body part, not per limb type: arms, legs, head and
        # torso each let go on their own as soon as tracking moves them.
        self.widgets.append(Toggle(
            px, cy, pw, 24, bool(getattr(self.app, "idle_release", True)),
            "Follow tracking on motion",
            on_change=lambda _v: self.app.toggle_idle_release()))
        cy += 30
        self.widgets.append(Label(
            px, cy, "Every body part the camera can see", 10, FG)); cy += 14
        self.widgets.append(Label(
            px, cy, "lets go when it moves; the rest hold", 10, FG)); cy += 18
        self.widgets.append(Label(px, cy, "Click to apply · Right-click for menu", 10, FG))
        cy += 20

        # ---- Pose grid: thumbnail tiles, two or more per row
        columns = max(2, int((pw + 10) // 106))
        gutter = 10
        tile_w = min(96.0, (pw - gutter * (columns - 1)) / columns)
        tile_h = 96.0
        tree = lib.by_category()
        for cat in sorted(tree.keys()):
            self.widgets.append(Label(px, cy, cat.upper(), 12, SECTION))
            cy += 22
            for subcat in sorted(tree[cat].keys()):
                if subcat:
                    self.widgets.append(Label(px + 6, cy, subcat, 11, FG))
                    cy += 18
                col = 0
                for pose in sorted(tree[cat][subcat], key=lambda p: p.name):
                    self.widgets.append(_PoseTile(
                        px + col * (tile_w + gutter), cy, tile_w, tile_h,
                        pose, self))
                    col += 1
                    if col >= columns:
                        col = 0
                        cy += tile_h + gutter
                if col:             # flush the partial row
                    cy += tile_h + gutter
            cy += 6
        if not lib.poses:
            self.widgets.append(Label(px, cy, "(No poses)", 11, FG)); cy += 20
        cy += 12
        self.widgets.append(Label(px, cy, "CATEGORIES", 12, SECTION)); cy += 22
        for cat in sorted(lib.categories.keys()):
            subcats = lib.categories.get(cat, [])
            sub_text = ", ".join(subcats) if subcats else "(no sub-categories)"
            self.widgets.append(Label(px, cy, f"• {cat}", 11, FG)); cy += 16
            self.widgets.append(Label(px + 12, cy, sub_text, 10,
                                      (0.6, 0.65, 0.75, 1.0))); cy += 18
        self._finalize_layout()

    def _draw_hover_tip(self, r):
        """Description bubble for the pose tile under the pointer."""
        mx, my = getattr(self.app, "_mouse", (0.0, 0.0))
        _vx, vy, _vw, vh = self._viewport()
        if not (vy <= my <= vy + vh):
            return
        tile = None
        for w in self.widgets:
            if isinstance(w, _PoseTile) and w.contains(mx, my):
                tile = w
                break
        if tile is None:
            return
        pose = tile.pose
        lines = [pose.name]
        where = " / ".join(x for x in (pose.category, pose.subcategory) if x)
        if where:
            lines.append(where)
        lines.append(f"duration {pose.duration_s:.1f}s" if pose.duration_s
                     else "sticky (idle)")
        lines.append(f"attack {pose.attack_ms:.0f}ms  release {pose.release_ms:.0f}ms")
        lines.append(f"{len(pose.joint_targets)} joint target(s)")
        bw = max(len(s) for s in lines) * 7 + 20
        bh = len(lines) * 17 + 12
        win_w, win_h = self.app.window.get_size()
        bx = min(mx + 14, max(0, win_w - bw - 8))
        by = min(my + 16, max(0, win_h - bh - 8))
        r.rect(bx, by, bw, bh, 8, (0.10, 0.12, 0.16, 0.97))
        for i, s in enumerate(lines):
            r.text(bx + 10, by + 6 + i * 17, s, 12,
                   SECTION if i == 0 else FG)

    def draw(self, r, _w, _h):
        self._draw_shell(r, "POSES")
        self._draw_widgets(r)
        self._draw_hover_tip(r)
        self._draw_footer(r, "Close")

class ExpressionManagerPanel(_PanelBase):
    """Live face signals, saved expressions + per-layer drive bindings.

    Three sections:
    1. SAVED EXPRESSIONS — one click applies a saved face.
    2. LIVE SIGNALS — read-only display of skeleton.face values.
    3. DRIVE BINDS — for the currently selected layer, 5 rows (H/W/X/Y/R),
       each showing the bound signal and letting the user pick signal from
       a dropdown-ish button grid.
    """

    SIGNALS = (
        "eye_open_l", "eye_open_r", "mouth_open",
        "brow_raise_l", "brow_raise_r",
        "head_yaw", "head_pitch", "head_roll",
    )
    DRIVE_FIELDS = ("drive_h", "drive_w", "drive_x", "drive_y", "drive_r")

    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)
        self._editing_field: str | None = None
        # Row button -> the expression it applies, for the row menus.
        self._expr_rows: dict = {}

    def footer_action(self):
        self.app.expression_panel_open = False
        self.app._layout_dirty = True

    def expression_at(self, mx, my):
        """The saved expression whose row is under the pointer, if any."""
        for btn, expr in self._expr_rows.items():
            if btn.contains(mx, my):
                return expr
        return None

    def rebuild(self):
        self._snapshot_build_origin()
        self.widgets = []
        self._expr_rows = {}
        self.scroll_y = 0.0
        px = self.x + 20
        pw = self.w - 80
        cy = self.y + 48

        self.widgets.append(Label(px, cy, "EXPRESSIONS", 14, SECTION))
        cy += 24

        self.widgets.append(Button(
            px, cy, pw, 26, "＋ Capture Expression",
            on_press=self.app.capture_current_expression))
        cy += 32

        # --- Saved faces: one row per expression, click to wear it.
        self.widgets.append(Label(px, cy, "SAVED EXPRESSIONS", 12, SECTION))
        cy += 22
        lib = getattr(self.app, "expression_library", None)
        faces = sorted(getattr(lib, "poses", None) or (),
                       key=lambda e: e.name)
        active = getattr(self.app, "active_expression", None)
        for expr in faces:
            mark = "● " if expr is active else "   "
            btn = Button(px, cy, pw, 24, mark + expr.name,
                         on_press=lambda n=expr.name:
                             self.app.apply_expression(n))
            self._expr_rows[btn] = expr
            self.widgets.append(btn)
            cy += 28
        if not faces:
            self.widgets.append(Label(
                px, cy, "No saved expressions yet", 10, FG))
            cy += 20
        self.widgets.append(Button(px, cy, pw, 24, "Clear Expression",
                                   on_press=self.app.clear_expression))
        cy += 32

        # --- Live signals (read-only display, updated per-frame in draw)
        self.widgets.append(Label(px, cy, "LIVE SIGNALS", 12, SECTION))
        cy += 20
        for name in self.SIGNALS:
            self.widgets.append(_SignalReadout(
                px, cy, pw, 20, name,
                lambda: getattr(self.app.skeleton, "face", {})))
            cy += 24

        # --- Signal smoothing (per-signal attack/release)
        cy += 8
        self.widgets.append(Label(px, cy, "SIGNAL SMOOTHING", 12, SECTION))
        cy += 20
        cfg = self.app.config.data.setdefault("face_smoothing", {})
        for name in self.SIGNALS:
            atk = cfg.get(f"{name}_atk", 3.0)
            rel = cfg.get(f"{name}_rel", 6.0)
            self.widgets.append(NumericSlider(
                px, cy, pw, 30, atk, 1.0, 30.0, f"{name}  attack",
                on_change=lambda v, n=name, c=cfg: c.__setitem__(f"{n}_atk", v)))
            cy += 52
            self.widgets.append(NumericSlider(
                px, cy, pw, 30, rel, 1.0, 30.0, f"{name}  release",
                on_change=lambda v, n=name, c=cfg: c.__setitem__(f"{n}_rel", v)))
            cy += 52
        cy += 8

        self.widgets.append(Label(px, cy, "DRIVE BINDS", 12, SECTION))
        cy += 20
        L = self.app.editor.selected_layer()
        if L is None:
            self.widgets.append(Label(
                px, cy, "Select a layer to bind signals", 10, FG))
            cy += 20
        else:
            self.widgets.append(Label(px, cy, f"Layer: {L.name}", 11, FG))
            cy += 20
            for field in self.DRIVE_FIELDS:
                self.widgets.append(_DriveBindRow(
                    px, cy, pw, 26, L, field, self))
                cy += 32
            # Reset all
            self.widgets.append(Button(
                px, cy, pw, 24, "Clear All Drives",
                on_press=lambda: self._clear_drives(L)))
            cy += 32

        # --- Signal picker (shown when a drive is being edited)
        if self._editing_field is not None:
            self.widgets.append(Label(
                px, cy, f"Pick signal for {self._editing_field}:", 11, FG))
            cy += 20
            gw = (pw - 4) / 2
            for i, sig in enumerate(self.SIGNALS):
                cx = px + (i % 2) * (gw + 4)
                ry = cy + (i // 2) * 26
                self.widgets.append(Button(
                    cx, ry, gw, 22, sig,
                    on_press=lambda s=sig: self._bind(s)))
            cy += ((len(self.SIGNALS) + 1) // 2) * 26 + 8
            self.widgets.append(Button(
                px, cy, pw, 22, "Cancel",
                on_press=self._cancel_edit))
            cy += 26
        self._finalize_layout()

    def _bind(self, signal_name: str):
        L = self.app.editor.selected_layer()
        if L is None or self._editing_field is None:
            return
        fld = self._editing_field
        current = getattr(L, fld)
        # Preserve base/factor if the drive already exists, else default
        base, factor = (current[1], current[2]) if current else (0.0, 0.04)
        self.app.editor.push_undo(self.app.editor.doc)
        setattr(L, fld, (signal_name, base, factor))
        self._editing_field = None
        self.rebuild()

    def _cancel_edit(self):
        self._editing_field = None
        self.rebuild()

    def _clear_drives(self, L):
        self.app.editor.push_undo(self.app.editor.doc)
        for fld in self.DRIVE_FIELDS:
            setattr(L, fld, None)
        self.rebuild()

    def begin_field_edit(self, field):
        self._editing_field = field
        self.rebuild()

    def draw(self, r, _w, _h):
        self._draw_shell(r, "EXPRESSIONS")
        self._draw_widgets(r)
        self._draw_footer(r, "Close")


class _SignalReadout(Widget):
    def __init__(self, x, y, w, h, name, signals_getter):
        super().__init__(x, y, w, h)
        self.name = name
        self._signals_getter = signals_getter

    def draw(self, r):
        bx = self.x + self._render_dx
        by = self.y + self._render_dy
        sig = self._signals_getter() or {}
        val = sig.get(self.name, 0.0)

        signed = self.name.startswith("head_")
        lo = -1.0 if signed else 0.0
        t = max(0.0, min(1.0, (val - lo) / (1.0 - lo)))
        r.text(bx, by + 2, self.name, 11, (0.80, 0.88, 1.0, 1.0))
        r.rect(bx + self.w * 0.55, by + 8, self.w * 0.45, 4, 2,
               (0.20, 0.22, 0.26, 1.0))
        r.rect(bx + self.w * 0.55, by + 8, self.w * 0.45 * t, 4, 2,
               (0.28, 0.68, 1.0, 1.0))
        r.text(bx + self.w * 0.55 + self.w * 0.45 + 4, by + 2,
               f"{val:+.2f}", 10, (0.75, 0.82, 0.95, 1.0))


class _DriveBindRow(Widget):
    def __init__(self, x, y, w, h, layer, field, panel):
        super().__init__(x, y, w, h)
        self.layer = layer
        self.field = field
        self.panel = panel

    def draw(self, r):
        bx = self.x + self._render_dx
        by = self.y + self._render_dy
        fld_short = self.field.replace("drive_", "").upper()
        drive = getattr(self.layer, self.field)
        r.rect(bx, by, self.w, self.h, 4, (0.16, 0.18, 0.22, 1.0))
        r.text(bx + 6, by + 4, fld_short, 11, (0.80, 0.88, 1.0, 1.0))
        if drive is None:
            r.text(bx + 36, by + 4, "unbound — click to bind",
                   10, (0.60, 0.65, 0.75, 1.0))
        else:
            sig, base, factor = drive
            r.text(bx + 36, by + 4, f"{sig}  base={base:+.3f} k={factor:+.3f}",
                   10, (0.85, 0.92, 1.0, 1.0))

    def on_press(self, mx, my):
        self.panel.begin_field_edit(self.field)

class ScenePanel(_PanelBase):
    """Layer panel for the currently-active scene.

    Orders, from the top: the scene stack, scene settings, the scene's
    BASE POSE, then the prop list / selected-prop details. The base pose
    sits directly above SCENE LAYERS because the props are positioned
    around a posed chibi, not a T-posed one.
    """

    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)

    def footer_action(self):
        ed = getattr(self.app, "scene_editor", None)
        if ed is not None and ed.selected_layer() is not None:
            ed.selected_layer_name = None
            self.rebuild()
        else:
            self.app.scene_edit_active = False

    def rebuild(self):
        self._snapshot_build_origin()
        self.widgets = []
        self.scroll_y = 0.0
        ed = getattr(self.app, "scene_editor", None)
        if ed is None:
            self._content_h = 0.0
            return
        px = self.x + 20
        pw = self.w - 80
        cy = self.y + 48
        scene = self.app.scenes.get(self.app.active_scene_name)
        tl = scene.timeline if scene is not None else None

        # --- Scene stack (back to front): select / visibility / depth
        self.widgets.append(Label(px, cy, "SCENES", 12, SECTION)); cy += 22
        for name, doc in self._ordered_scenes():
            selected = (name == self.app.active_scene_name)
            mark = "● " if selected else "   "
            row_w = pw - 96
            self.widgets.append(Button(
                px, cy, row_w, 24, mark + name,
                on_press=lambda n=name: self.app.set_active_scene(n)))
            self.widgets.append(Button(
                px + row_w + 4, cy, 32, 24,
                "o" if doc.visible else "-",
                on_press=lambda n=name: self.app.toggle_scene_visible(n)))
            self.widgets.append(Label(
                px + row_w + 42, cy + 5, f"z{doc.z:g}", 11, FG))
            cy += 28
            if not selected:
                continue
            self.widgets.append(NumericSlider(
                px + 12, cy, pw - 68, 30, float(doc.opacity), 0.0, 1.0,
                "Opacity",
                on_change=lambda v, n=name: self.app.set_scene_opacity(n, v)))
            cy += 40
            half = (pw - 16) / 2
            self.widgets.append(Button(
                px + 12, cy, half, 22, "Raise",
                on_press=lambda n=name: self.app.raise_scene(n)))
            self.widgets.append(Button(
                px + 16 + half, cy, half, 22, "Lower",
                on_press=lambda n=name: self.app.lower_scene(n)))
            cy += 30
        self.widgets.append(Button(
            px, cy, pw, 24, "＋ New Scene",
            on_press=lambda: self.app.add_scene()))
        cy += 30
        self.widgets.append(Button(
            px, cy, pw, 24, "Duplicate Scene",
            on_press=lambda n=self.app.active_scene_name:
                self.app.duplicate_scene(n)))
        cy += 30
        if len(self.app.scenes) > 1:
            self.widgets.append(Button(
                px, cy, pw, 24, f"Delete '{self.app.active_scene_name}'",
                on_press=lambda n=self.app.active_scene_name:
                    self.app.delete_scene(n)))
            cy += 32
        cy += 8

        # --- Scene settings (name / timing / playback)
        self.widgets.append(Label(px, cy, "SCENE SETTINGS", 12, SECTION))
        cy += 22
        self.widgets.append(Label(px, cy, "Name (Enter to rename)", 11, FG))
        cy += 18
        self.widgets.append(TextInput(
            px, cy, pw, 26,
            text=self.app.active_scene_name,
            placeholder="Scene name",
            on_submit=self._rename_scene))
        cy += 32

        if tl is not None:
            self.widgets.append(NumericSlider(
                px, cy, pw, 30, float(tl.duration), 1.0, 60.0,
                "Duration (s)", on_change=self._set_duration,
                fmt="{:.1f}s"))
            cy += 52

            self.widgets.append(Label(px, cy, "Frame rate", 11, FG))
            cy += 18
            gw = (pw - 12) / 4
            for i, fps in enumerate((12, 24, 30, 60)):
                mark = "● " if int(tl.fps) == fps else ""
                self.widgets.append(Button(
                    px + i * (gw + 4), cy, gw, 24, f"{mark}{fps}",
                    on_press=lambda f=fps: self._set_fps(f)))
            cy += 32

            self.widgets.append(Toggle(
                px, cy, pw, 22, bool(tl.loop), label="Loop playback",
                on_change=self._set_loop))
            cy += 32
            self.widgets.append(Toggle(
                px, cy, pw, 22, bool(self.app.scenes[
                    self.app.active_scene_name].autoplay),
                label="Autoplay in presentation",
                on_change=self._set_autoplay))
            cy += 32
            self.widgets.append(Button(
                px, cy, pw, 24,
                "Hide Timeline" if self.app.timeline_visible else "Show Timeline",
                on_press=self.app._toggle_timeline))
            cy += 32
        cy += 8

        # --- Base pose: what the chibi holds while this scene is shown
        self.widgets.append(Label(px, cy, "BASE POSE", 12, SECTION)); cy += 22
        self.widgets.append(Label(
            px, cy, f"Chibi: {self.app.scene_base_pose_name()}", 12, FG))
        cy += 20
        pose_btn = Button(
            px, cy, pw, 24, "Choose Pose…", on_press=lambda: None)
        pose_btn._cb = lambda b=pose_btn: self.app.open_scene_pose_menu(
            b.x, b.y + b.h)
        self.widgets.append(pose_btn)
        cy += 30
        half = (pw - 4) / 2
        self.widgets.append(Button(
            px, cy, half, 22, "Use Current",
            on_press=self.app.capture_scene_base_pose))
        self.widgets.append(Button(
            px + half + 4, cy, half, 22, "T-pose",
            on_press=lambda: self.app.set_scene_base_pose(None)))
        cy += 30
        cy += 8

        # --- Layer list or selected-layer details
        self.widgets.append(Label(px, cy, "SCENE LAYERS", 12, SECTION)); cy += 22
        L = ed.selected_layer()
        if L is None:
            layers = sorted(ed.doc.layers, key=lambda x: x.z, reverse=True)
            for layer in layers:
                label = layer.name
                if not layer.visible:
                    label += "  [hidden]"
                if layer.in_front:
                    label += "  [front]"
                if layer.strokes:
                    label += f"  ·{len(layer.strokes)}p"
                if layer.name == ed.selected_layer_name:
                    label = "▸ " + label
                self.widgets.append(Button(
                    px, cy, pw, 24, label,
                    on_press=lambda n=layer.name: self._select(n)))
                cy += 28
            self.widgets.append(Button(
                px, cy, pw, 24, "＋ New Layer",
                on_press=self._new_layer))
            cy += 28
            self.widgets.append(Button(
                px, cy, pw, 24, "Import PNG…",
                on_press=self.app._import_png))


            cy += 28
        else:
            self.widgets.append(Label(px, cy, f"LAYER: {L.name}", 14, SECTION))
            cy += 24
            self.widgets.append(Button(
                px, cy, pw, 24, "◀ Back to layers",
                on_press=lambda: self._deselect()))
            cy += 32
            self.widgets.append(Label(px, cy, "Name (Enter to rename)", 11, FG))
            cy += 18
            self.widgets.append(TextInput(
                px, cy, pw, 26, text=L.name, placeholder="Layer name",
                on_submit=lambda txt, o=L: self._rename_layer(o, txt)))
            cy += 32
            self.widgets.append(Label(px, cy, f"kind: {L.kind}", 12, FG))
            cy += 22
            self.widgets.append(Toggle(
                px, cy, pw, 22, L.visible, label="Visible",
                on_change=lambda v, o=L: setattr(o, "visible", v)))
            cy += 30

            third = (pw - 8) / 3
            self.widgets.append(Button(
                px, cy, third, 24, "Duplicate",
                on_press=self._duplicate_layer))
            self.widgets.append(Button(
                px + third + 4, cy, third, 24, "Raise",
                on_press=lambda: self._shift_layer(+1)))
            self.widgets.append(Button(
                px + 2 * (third + 4), cy, third, 24, "Lower",
                on_press=lambda: self._shift_layer(-1)))
            cy += 32

            # --- Render order relative to the chibi
            self.widgets.append(Label(px, cy, "ORDER VS CHIBI", 12, SECTION))
            cy += 22
            side_w = (pw - 4) / 2
            for i, (label, value) in enumerate((("Behind", False),
                                               ("In front", True))):
                mark = "● " if bool(L.in_front) == value else ""
                self.widgets.append(Button(
                    px + i * (side_w + 4), cy, side_w, 24, mark + label,
                    on_press=lambda v=value, o=L:
                        self.app.set_layer_in_front(o, v)))
            cy += 32

            if L.kind == "image":
                # A transparent PNG keeps its alpha all the way through
                # import → selection → cut/paste: every pixel op works on
                # RGBA and writes a fresh file.
                self.widgets.append(Label(px, cy, "IMAGE", 12, SECTION))
                cy += 22
                quarter = (pw - 12) / 4
                for i, (label, cb) in enumerate((
                        ("Cut", self.app.selection_cut),
                        ("Copy", self.app.selection_copy),
                        ("Paste", self.app.selection_paste),
                        ("Clear", self.app.selection_delete_pixels))):
                    self.widgets.append(Button(
                        px + i * (quarter + 4), cy, quarter, 22, label,
                        on_press=cb))
                cy += 30
                sel = getattr(ed, "selection", None)
                if sel is not None and sel.layer_name == L.name:
                    box = sel.bbox()
                    text = (f"Selection {box[2] - box[0]}×{box[3] - box[1]} px"
                            if box else "Selection empty")
                else:
                    text = "No selection — use M / Q / W"
                self.widgets.append(Label(px, cy, text, 11, FG))
                cy += 20
                self.widgets.append(Button(
                    px, cy, pw, 22, "Deselect",
                    on_press=self.app.selection_none))
                cy += 30

            self.widgets.append(Button(
                px, cy, pw, 24, "Key Transform @ Playhead",
                on_press=self._key_layer))
            cy += 34
            self._add_number(px, cy, pw, "Opacity",
                             L, "opacity", 0.0, 1.0); cy += 52
            self._add_number(px, cy, pw, "Width",
                             L, "w", 0.005, 1.50); cy += 52
            self._add_number(px, cy, pw, "Height",
                             L, "h", 0.005, 1.50); cy += 52
            self._add_number(px, cy, pw, "Corner R",
                             L, "r", 0.0, 0.50); cy += 52
            self._add_number(px, cy, pw, "Rotation",
                             L, "rotation", -3.14, 3.14); cy += 52
            self._add_number(px, cy, pw, "Offset X",
                             L, "dx", -0.20, 1.20); cy += 52
            self._add_number(px, cy, pw, "Offset Y",
                             L, "dy", -0.20, 1.20); cy += 52
            self._add_number(px, cy, pw, "Z-order",
                             L, "z", -50, 50); cy += 52
            for label, i in (("R", 0), ("G", 1), ("B", 2)):
                self.widgets.append(Label(px, cy, f"Fill {label}", 12, FG))
                cy += 20
                self._add_rgb_slider(px, cy, pw, L, i); cy += 26
            cy += 10
            cy = prop_anchor_widgets(self.widgets, self.app, L, px, pw, cy)
            cy = self._build_joint_links(scene, L, px, pw, cy)
            cy += 10
            half = (pw - 4) / 2
            self.widgets.append(Button(
                px, cy, half, 26, "Hide" if L.visible else "Show",
                on_press=lambda o=L: self._toggle_hidden(o)))
            self.widgets.append(Button(
                px + half + 4, cy, half, 26, "Delete",
                on_press=self._delete_layer))
            cy += 34
            self.widgets.append(Button(
                px, cy, pw, 26, "Save PNG (transparent)",
                on_press=self.app.export_object_png))
            cy += 34
        self._finalize_layout()

    # ---- helpers
    def _ordered_scenes(self) -> list[tuple[str, object]]:
        """Visible scenes back-to-front, then any hidden ones."""
        ordered = list(self.app._scene_stack())
        seen = {name for name, _ in ordered}
        ordered += [(name, doc)
                    for name, doc in sorted(self.app.scenes.items())
                    if name not in seen]
        return ordered

    JOINT_CHIPS = PROP_JOINTS

    def _build_joint_links(self, scene, L, px, pw, cy):
        """PINNED JOINTS section of the layer details.

        Lists every pin that targets ``L`` with offset sliders and a remove
        button, then a chip row for binding a joint to this prop.
        """
        self.widgets.append(Label(px, cy, "PINNED JOINTS", 12, SECTION))
        cy += 22
        links = list(scene.joint_links) if scene is not None else []
        mine = [(i, ln) for i, ln in enumerate(links)
                if ln.get("layer") == L.name]
        if not mine:
            self.widgets.append(Label(px, cy, "Not pinned to any joint", 11, FG))
            cy += 20
        for idx, link in mine:
            self.widgets.append(Label(
                px, cy, f"\u25c6 {link.get('joint', '?')}  ·  {L.name}", 12, FG))
            self.widgets.append(Button(
                px + pw - 26, cy - 4, 26, 20, "\u2715",
                on_press=lambda k=idx: self.app.remove_joint_link(k)))
            cy += 22
            self.widgets.append(NumericSlider(
                px, cy, pw, 28, float(link.get("dx", 0.0)), -0.5, 0.5,
                "Offset X", on_change=lambda v, k=idx: (
                    self.app.set_joint_link_offset(k, dx=v))))
            cy += 38
            self.widgets.append(NumericSlider(
                px, cy, pw, 28, float(link.get("dy", 0.0)), -0.5, 0.5,
                "Offset Y", on_change=lambda v, k=idx: (
                    self.app.set_joint_link_offset(k, dy=v))))
            cy += 38
        self.widgets.append(Label(
            px, cy, "Pin a joint to this prop (static):", 11, FG))
        cy += 18
        cols = 3
        cw = (pw - 2 * 4) / cols
        for i, jname in enumerate(self.JOINT_CHIPS):
            row, col = divmod(i, cols)
            self.widgets.append(Button(
                px + col * (cw + 4), cy + row * 28, cw, 24, jname,
                on_press=lambda j=jname, n=L.name:
                    self.app.add_joint_link(j, n)))
        rows = (len(self.JOINT_CHIPS) + cols - 1) // cols
        return cy + rows * 28 + 8

    def _select(self, name):
        self.app.scene_editor.selected_layer_name = name
        self.rebuild()

    def _deselect(self):
        self.app.scene_editor.selected_layer_name = None
        self.rebuild()

    def _toggle_hidden(self, L):
        L.visible = not L.visible
        self.rebuild()

    def _rename_scene(self, text):
        self.app.rename_scene(text)

    def _set_duration(self, value):
        scene = self.app.scenes.get(self.app.active_scene_name)
        if scene is None:
            return
        scene.timeline.duration = max(1.0, float(value))
        self.app.playhead = min(self.app.playhead, scene.timeline.duration)

    def _set_fps(self, fps: int):
        scene = self.app.scenes.get(self.app.active_scene_name)
        if scene is None:
            return
        scene.timeline.fps = max(1, int(fps))
        self.app.playhead = scene.timeline.snap(self.app.playhead)
        self.rebuild()

    def _set_loop(self, value: bool):
        scene = self.app.scenes.get(self.app.active_scene_name)
        if scene is not None:
            scene.timeline.loop = bool(value)

    def _set_autoplay(self, value: bool):
        scene = self.app.scenes.get(self.app.active_scene_name)
        if scene is not None:
            scene.autoplay = bool(value)

    def _rename_layer(self, layer, text: str):
        # The app owns the rename so the hierarchy and the browser carry
        # keys and joint pins over exactly the same way.
        self.app.rename_scene_layer(layer.name, text)
        self.rebuild()

    def _duplicate_layer(self):
        L = self.app.scene_editor.selected_layer()
        if L is not None:
            self.app.duplicate_scene_layer(L.name)

    def _shift_layer(self, direction: int):
        L = self.app.scene_editor.selected_layer()
        if L is not None:
            self.app.restack_scene_layer(L.name, direction)

    def _key_layer(self):
        ed = self.app.scene_editor
        L = ed.selected_layer()
        if L is None:
            return
        self.app.timeline_key_layer(L.name)

    def _delete_layer(self):
        name = self.app.scene_editor.selected_layer_name
        if name is not None:
            self.app.delete_scene_layer(name)

    def _new_layer(self):
        # One code path for a new prop layer: the app owns the naming,
        # the undo snapshot and the timeline refresh.
        add = getattr(self.app, "add_scene_layer", None)
        if add is not None:
            add()
            self.rebuild()
            return
        from model.chibi_doc import Layer
        ed = self.app.scene_editor
        ed.push_undo(ed.doc)
        n = len(ed.doc.layers) + 1
        name = f"prop_{n}"
        while ed.doc.find(name) is not None:
            n += 1
            name = f"prop_{n}"
        L = Layer(
            kind="round_rect",
            anchor=None, anchor_end=None,
            dx=0.5, dy=0.6, w=0.15, h=0.10, r=0.02,
            fill="#6B4A2E",
            name=name,
            z=(max((L.z for L in ed.doc.layers), default=0) + 1),
        )
        ed.doc.layers.append(L)
        ed.doc.sort_layers()
        ed.selected_layer_name = name
        self.app.scenes[self.app.active_scene_name] = ed.doc
        self.rebuild()

    def _add_number(self, x, y, w, name, obj, attr, lo, hi, fmt="{:.2f}"):
        cur = float(getattr(obj, attr))
        def cb(v, o=obj, a=attr): setattr(o, a, v)
        self.widgets.append(NumericSlider(x, y, w, 30, cur, lo, hi,
                                          name, on_change=cb, fmt=fmt))

    def _add_rgb_slider(self, x, y, w, L, channel):
        import scene
        cur = scene.hex_to_rgba(L.fill) if L.fill != "none" else (1.0, 1.0, 1.0, 1.0)
        def cb(v, o=L, c=channel):
            import scene as _s
            base = _s.hex_to_rgba(o.fill) if o.fill != "none" else (1.0, 1.0, 1.0, 1.0)
            rgb = list(base[:3])
            rgb[c] = v
            o.fill = "#{:02X}{:02X}{:02X}".format(
                int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255))
        self.widgets.append(Slider(x, y, w, 18, cur[channel], 0.0, 1.0, on_change=cb))

    def draw(self, r, _w, _h):
        self._draw_shell(r, "SCENE")
        self._draw_widgets(r)
        ed = getattr(self.app, "scene_editor", None)
        label = ("Close" if (ed is not None and ed.selected_layer() is not None)
                 else "Hide")
        self._draw_footer(r, label)