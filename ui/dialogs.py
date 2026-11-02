"""Modal dialogs. File browser for save / load / import, the pose
name/category prompt used when a new pose is captured, the yes/no
prompt used for mode switches, the application shell dialogs
(preferences, help info, shortcut reference), and the batch export
picker."""
from __future__ import annotations
import json
from pathlib import Path

from export.batch import (BatchOptions, CHIBI_PARTS, DEFAULT_DEST,
                          MANIFEST_NAME, SIZES, build_items)
from ui.renderer import Renderer2D
from ui.widgets import (Button, TextInput, Label, NumericSlider, Toggle)

SECTION = (0.55, 0.70, 0.95, 1.0)
FG = (0.82, 0.88, 1.00, 1.0)
HINT = (0.70, 0.77, 0.90, 1.0)


def _wrap(text: str, size: int, width: float) -> list[str]:
    """Greedy word wrap using the UI font's rough advance."""
    lines: list[str] = []
    current = ""
    for word in (text or "").split():
        trial = word if not current else f"{current} {word}"
        if len(trial) * size * 0.56 <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit(text: str, width: float, per_char: float = 7.0) -> str:
    """Clip ``text`` to ``width`` pixels, ending with an ellipsis."""
    budget = max(1, int(width / max(1.0, per_char)))
    text = str(text or "")
    return text if len(text) <= budget else text[:budget - 1] + "\u2026"


class FileBrowserDialog:
    W = 720
    H = 540

    def __init__(self, app):
        self.app = app
        self.visible = False
        self.mode = "save"
        self.ext_filter = ".json"
        self.current_dir = Path.cwd()
        self.selected_file: Path | None = None
        self.scroll_y = 0.0
        self._entry_hits: list[tuple] = []
        self._crumb_hits: list[tuple] = []
        self._cancel_btn: Button | None = None
        self._confirm_btn: Button | None = None
        self._up_btn: Button | None = None
        self._back_btn: Button | None = None
        self._history: list[Path] = []
        self.filename_input = TextInput(0, 0, 400, 32, "",
                                        "filename.json")

    def open(self, mode="save", start_dir=None, ext_filter=".json"):
        self.mode = mode
        self.ext_filter = ext_filter
        self.visible = True
        start = Path(start_dir) if start_dir else Path.cwd()
        self.current_dir = start.resolve() if start.exists() else Path.cwd().resolve()
        self.selected_file = None
        self.scroll_y = 0.0
        self._history = []
        self.filename_input.text = ""
        self.filename_input.cursor = 0
        self.filename_input.active = False

    def close(self):
        self.visible = False
        self.filename_input.active = False

    def rect(self, sw: int, sh: int) -> tuple:
        """Panel rect for the given screen size (also used for the stream)."""
        return ((sw - self.W) * 0.5, (sh - self.H) * 0.5, self.W, self.H)

    def _entries(self):
        items = []
        try:
            for child in sorted(self.current_dir.iterdir()):
                if child.name.startswith("."):
                    continue
                if child.is_dir():
                    items.append((child.name + "/", child, True))
                elif child.suffix.lower() == self.ext_filter:
                    items.append((child.name, child, False))
        except (PermissionError, OSError):
            pass
        return items

    def _set_dir(self, new_dir: Path):
        if new_dir == self.current_dir:
            return
        self._history.append(self.current_dir)
        self.current_dir = new_dir
        self.scroll_y = 0
        self.selected_file = None

    def draw(self, r: Renderer2D, sw: int, sh: int):
        if not self.visible:
            return
        r.rect(0, 0, sw, sh, 0, (0.0, 0.0, 0.0, 0.6))
        x = (sw - self.W) * 0.5
        y = (sh - self.H) * 0.5
        r.rect(x, y, self.W, self.H, 12, (0.14, 0.16, 0.20, 0.99))
        title = {"save": "Save Project", "load": "Load Project",
                 "import": "Import PNG"}.get(self.mode, "File")
        r.text(x + 20, y + 14, title, 16, (0.55, 0.70, 0.95, 1.0))

        # Navigation row: [←] [Up] breadcrumb
        nav_y = y + 40
        self._back_btn = Button(x + 20, nav_y, 34, 28, "←",
                                 on_press=self._go_back)
        self._back_btn.draw(r)
        self._up_btn = Button(x + 60, nav_y, 44, 28, "Up",
                              on_press=self._go_up)
        self._up_btn.draw(r)

        # Breadcrumb
        crumb_x = x + 116
        crumb_y = nav_y + 6
        self._crumb_hits = []
        parts = list(self.current_dir.parts)
        for i, part in enumerate(parts):
            label = part if part else "/"
            if i == 0 and part == self.current_dir.anchor:
                label = part
            cw = len(label) * 7 + 8
            r.text(crumb_x, crumb_y, label, 12, (0.75, 0.82, 0.95, 1.0))
            self._crumb_hits.append((crumb_x, crumb_y, cw, 16, i))
            crumb_x += cw + 4
            if i < len(parts) - 1:
                r.text(crumb_x - 2, crumb_y, "/", 12, (0.5, 0.55, 0.65, 1.0))
                crumb_x += 6

        list_x = x + 20
        list_y = y + 80
        list_w = self.W - 40
        list_h = self.H - 80 - 120
        r.rect(list_x, list_y, list_w, list_h, 8, (0.10, 0.12, 0.15, 1.0))

        entries = self._entries()
        total_h = len(entries) * 28
        max_scroll = max(0, total_h - list_h)
        self.scroll_y = max(0, min(max_scroll, self.scroll_y))

        r.push_clip(list_x, list_y, list_w, list_h)
        self._entry_hits = []
        for i, (name, path, is_dir) in enumerate(entries):
            ey = list_y + 4 + i * 28 - self.scroll_y
            if ey + 24 < list_y or ey > list_y + list_h:
                continue
            selected = (self.selected_file == path)
            bg = ((0.28, 0.42, 0.62, 1.0) if selected
                  else (0.16, 0.18, 0.22, 1.0))
            r.rect(list_x + 4, ey, list_w - 8, 26, 6, bg)
            label = ("[D] " if is_dir else "[F] ") + name
            r.text(list_x + 12, ey + 6, label, 13, (0.90, 0.95, 1.0, 1.0))
            self._entry_hits.append((list_x + 4, ey, list_w - 8, 26, path, is_dir))
        r.pop_clip()

        in_y = y + self.H - 108
        if self.mode in ("save", "import"):
            r.text(x + 20, in_y + 8, "Filename:", 12, (0.80, 0.88, 1.0, 1.0))
            self.filename_input.x = x + 100
            self.filename_input.y = in_y
            self.filename_input.w = self.W - 140
            self.filename_input.h = 32
            self.filename_input._render_dx = 0
            self.filename_input._render_dy = 0
            self.filename_input.draw(r)
        else:
            sel = self.selected_file
            name = sel.name if sel else "(no file selected)"
            r.text(x + 20, in_y + 8, f"Selected: {name}", 12,
                   (0.80, 0.88, 1.0, 1.0))

        btn_y = y + self.H - 56
        self._cancel_btn = Button(x + self.W - 330, btn_y, 100, 36,
                                  "Cancel", on_press=self.close)
        self._cancel_btn.draw(r)
        confirm_label = {"save": "Save", "load": "Load",
                         "import": "Import"}.get(self.mode, "OK")
        self._confirm_btn = Button(x + self.W - 220, btn_y, 100, 36,
                                   confirm_label, on_press=self._confirm)
        self._confirm_btn.draw(r)

    def on_press(self, mx, my):
        if not self.visible:
            return
        # Breadcrumb click
        for bx, by, bw, bh, idx in self._crumb_hits:
            if bx <= mx <= bx + bw and by <= my <= by + bh:
                parts = list(self.current_dir.parts)
                new_path = Path(*parts[:idx + 1])
                self._set_dir(new_path)
                return
        # Entry click
        for ex, ey, ew, eh, path, is_dir in self._entry_hits:
            if ex <= mx <= ex + ew and ey <= my <= ey + eh:
                if is_dir:
                    self._set_dir(path)
                else:
                    self.selected_file = path
                    if self.mode in ("save", "import"):
                        self.filename_input.text = path.name
                        self.filename_input.cursor = len(path.name)
                return
        # Text input
        if self.mode in ("save", "import") and self.filename_input.contains(mx, my):
            self.filename_input.on_press(mx, my)
            return
        else:
            self.filename_input.active = False
        # Buttons
        if self._cancel_btn and self._cancel_btn.contains(mx, my):
            self._cancel_btn.on_press(mx, my); return
        if self._confirm_btn and self._confirm_btn.contains(mx, my):
            self._confirm_btn.on_press(mx, my); return
        if self._up_btn and self._up_btn.contains(mx, my):
            self._up_btn.on_press(mx, my); return
        if self._back_btn and self._back_btn.contains(mx, my):
            self._back_btn.on_press(mx, my); return

    def on_scroll(self, dy):
        self.scroll_y -= dy * 30
        self.scroll_y = max(0, self.scroll_y)

    def on_char(self, ch):
        if self.visible and self.mode in ("save", "import"):
            self.filename_input.on_char(ch)

    def on_backspace(self):
        if self.visible and self.mode in ("save", "import"):
            self.filename_input.on_backspace()

    def on_enter(self):
        if self.visible:
            self._confirm()

    def _go_back(self):
        if not self._history:
            return
        self.current_dir = self._history.pop()
        self.scroll_y = 0
        self.selected_file = None

    def _go_up(self):
        parent = self.current_dir.parent
        if parent != self.current_dir:
            self._set_dir(parent)

    def _confirm(self):
        if self.mode == "save":
            name = self.filename_input.text.strip()
            if not name:
                return
            if not name.endswith(self.ext_filter):
                name += self.ext_filter
            target = self.current_dir / name
            self.app.save_snapshot_to(target)
            self.close()
        elif self.mode == "load":
            if self.selected_file:
                self.app.load_snapshot_from(self.selected_file)
                self.close()
        elif self.mode == "import":
            if self.selected_file:
                self.app.import_png_as_layer(self.selected_file)
                self.close()


class SavePoseDialog:
    """Ask for a pose's name, category and sub-category before saving.

    A pose captured from whatever the chibi is currently holding has no
    identity yet, so it needs a name and a home before it joins the
    library. The same prompt doubles as the rename dialog.
    """

    W = 470
    H = 302
    #: Fixed homes for a pose. Idle implies sticky + per-framing.
    CATEGORIES = ("Idle", "Emote", "Pose")

    def __init__(self, app):
        self.app = app
        self.visible = False
        self.title = "Save pose as"
        self.confirm_label = "Save"
        self.category = "Pose"
        self._on_save = None
        self._cancel_btn: Button | None = None
        self._confirm_btn: Button | None = None
        self._category_hits: list[tuple] = []
        self.name_input = TextInput(0, 0, 420, 32, "", "pose name")
        self.subcategory_input = TextInput(0, 0, 420, 32, "",
                                           "sub-category (optional)")

    # ---- lifecycle -----------------------------------------------------

    def open(self, name: str, on_save, title: str = "Save pose as",
             category: str = "Pose", subcategory: str = "",
             confirm: str = "Save") -> None:
        self.title = title
        self.confirm_label = confirm
        self.category = category if category in self.CATEGORIES else "Pose"
        self._on_save = on_save
        self.name_input.text = name or ""
        self.name_input.cursor = len(self.name_input.text)
        self.name_input.active = True
        self.subcategory_input.text = subcategory or ""
        self.subcategory_input.cursor = len(self.subcategory_input.text)
        self.subcategory_input.active = False
        self.visible = True

    def close(self) -> None:
        self.visible = False
        self.name_input.active = False
        self.subcategory_input.active = False

    def rect(self, sw: int, sh: int) -> tuple:
        """Panel rect for the given screen size (also used for the stream)."""
        return ((sw - self.W) * 0.5, (sh - self.H) * 0.5, self.W, self.H)

    # ---- input ---------------------------------------------------------

    def on_char(self, ch: str) -> None:
        if not self.visible:
            return
        if self.name_input.active:
            self.name_input.on_char(ch)
        elif self.subcategory_input.active:
            self.subcategory_input.on_char(ch)

    def on_backspace(self) -> None:
        if not self.visible:
            return
        if self.name_input.active:
            self.name_input.on_backspace()
        elif self.subcategory_input.active:
            self.subcategory_input.on_backspace()

    def on_enter(self) -> None:
        if self.visible:
            self._confirm()

    def on_press(self, mx: float, my: float) -> None:
        if not self.visible:
            return
        for cx, cy, cw, ch, label in self._category_hits:
            if cx <= mx <= cx + cw and cy <= my <= cy + ch:
                self.category = label
                return
        if self.name_input.contains(mx, my):
            self.name_input.active = True
            self.subcategory_input.active = False
            self.name_input.on_press(mx, my)
            return
        if self.subcategory_input.contains(mx, my):
            self.subcategory_input.active = True
            self.name_input.active = False
            self.subcategory_input.on_press(mx, my)
            return
        self.name_input.active = False
        self.subcategory_input.active = False
        if self._cancel_btn is not None and self._cancel_btn.contains(mx, my):
            self._cancel_btn.on_press(mx, my)
            return
        if self._confirm_btn is not None and self._confirm_btn.contains(mx, my):
            self._confirm_btn.on_press(mx, my)

    def _confirm(self) -> None:
        if self._on_save is None:
            self.close()
            return
        self._on_save(self.name_input.text, self.category,
                      self.subcategory_input.text.strip())

    # ---- drawing -------------------------------------------------------

    def draw(self, r: Renderer2D, sw: int, sh: int) -> None:
        if not self.visible:
            return
        r.rect(0, 0, sw, sh, 0, (0.0, 0.0, 0.0, 0.6))
        x = (sw - self.W) * 0.5
        y = (sh - self.H) * 0.5
        r.rect(x, y, self.W, self.H, 12, (0.14, 0.16, 0.20, 0.99))
        r.text(x + 20, y + 14, self.title, 16, (0.55, 0.70, 0.95, 1.0))

        r.text(x + 20, y + 46, "Name", 12, (0.80, 0.88, 1.0, 1.0))
        self.name_input.x = x + 20
        self.name_input.y = y + 64
        self.name_input.w = self.W - 40
        self.name_input.h = 32
        self.name_input._render_dx = 0
        self.name_input._render_dy = 0
        self.name_input.draw(r)

        r.text(x + 20, y + 106, "Sub-category", 12, (0.80, 0.88, 1.0, 1.0))
        self.subcategory_input.x = x + 20
        self.subcategory_input.y = y + 124
        self.subcategory_input.w = self.W - 40
        self.subcategory_input.h = 32
        self.subcategory_input._render_dx = 0
        self.subcategory_input._render_dy = 0
        self.subcategory_input.draw(r)

        r.text(x + 20, y + 170, "Category", 12, (0.80, 0.88, 1.0, 1.0))
        bw = (self.W - 40 - 2 * 8) / 3.0
        self._category_hits = []
        for i, label in enumerate(self.CATEGORIES):
            bx = x + 20 + i * (bw + 8)
            by = y + 188
            selected = label == self.category
            r.rect(bx, by, bw, 30, 6,
                   (0.22, 0.50, 0.82, 1.0) if selected
                   else (0.20, 0.22, 0.27, 1.0))
            text = ("● " if selected else "   ") + label
            r.text(bx + bw * 0.5 - len(label) * 3.6, by + 8, text, 13,
                   (0.98, 0.99, 1.0, 1.0) if selected
                   else (0.78, 0.85, 0.96, 1.0))
            self._category_hits.append((bx, by, bw, 30, label))

        btn_y = y + self.H - 52
        self._cancel_btn = Button(x + self.W - 260, btn_y, 110, 34,
                                  "Cancel", on_press=self.close)
        self._cancel_btn.draw(r)
        self._confirm_btn = Button(x + self.W - 140, btn_y, 120, 34,
                                   self.confirm_label, on_press=self._confirm)
        self._confirm_btn.draw(r)


class ConfirmDialog:
    """Yes / no prompt drawn above every other layer.

    The message is short by design: it exists so a destructive or
    modal-feeling action (switching modes) asks once before it happens.
    Enter confirms, Esc cancels, and the click outside the buttons is
    swallowed so nothing underneath reacts while the prompt is up.
    """

    W = 470
    H = 186

    def __init__(self, app):
        self.app = app
        self.visible = False
        self.message = ""
        self.confirm_label = "Switch"
        self.cancel_label = "Cancel"
        self._on_confirm = None
        self._cancel_btn: Button | None = None
        self._confirm_btn: Button | None = None

    # ---- lifecycle -----------------------------------------------------

    def open(self, message: str, on_confirm=None, confirm: str = "Switch",
             cancel: str = "Cancel") -> None:
        self.message = message or ""
        self.confirm_label = confirm
        self.cancel_label = cancel
        self._on_confirm = on_confirm
        self.visible = True

    def close(self) -> None:
        self.visible = False
        self._on_confirm = None

    # ---- input ---------------------------------------------------------

    def rect(self, sw: int, sh: int) -> tuple:
        """Panel rect for the given screen size (also used for the stream)."""
        return ((sw - self.W) * 0.5, (sh - self.H) * 0.5, self.W, self.H)

    def hit_test(self, mx: float, my: float) -> bool:
        """A modal prompt owns every click while it is open."""
        return bool(self.visible)

    def on_press(self, mx: float, my: float) -> None:
        if not self.visible:
            return
        if self._cancel_btn is not None and self._cancel_btn.contains(mx, my):
            self._cancel_btn.on_press(mx, my)
            return
        if self._confirm_btn is not None and self._confirm_btn.contains(mx, my):
            self._confirm_btn.on_press(mx, my)

    def on_enter(self) -> None:
        if self.visible:
            self._confirm()

    def _confirm(self) -> None:
        callback = self._on_confirm
        self.close()
        if callback is not None:
            callback()

    # ---- drawing -------------------------------------------------------

    @staticmethod
    def _wrap(text: str, size: int, width: float) -> list[str]:
        return _wrap(text, size, width)

    def draw(self, r: Renderer2D, sw: int, sh: int) -> None:
        if not self.visible:
            return
        r.rect(0, 0, sw, sh, 0, (0.0, 0.0, 0.0, 0.6))
        x, y, w, h = self.rect(sw, sh)
        r.rect(x, y, w, h, 12, (0.14, 0.16, 0.20, 0.99))
        r.rect(x + 1, y + 1, w - 2, 34, 10, (0.18, 0.22, 0.30, 1.0))
        r.text(x + 16, y + 11, "Confirm", 15, (0.55, 0.70, 0.95, 1.0))

        ty = y + 52
        for line in self._wrap(self.message, 14, w - 40):
            r.text(x + 20, ty, line, 14, (0.92, 0.95, 1.0, 1.0))
            ty += 22

        btn_y = y + h - 52
        self._cancel_btn = Button(x + w - 260, btn_y, 110, 34,
                                  self.cancel_label, on_press=self.close)
        self._cancel_btn.draw(r)
        self._confirm_btn = Button(x + w - 140, btn_y, 120, 34,
                                   self.confirm_label, on_press=self._confirm)
        self._confirm_btn.draw(r)


class InfoDialog:
    """Single-button message box for About / help text."""

    W = 560
    H = 300

    def __init__(self, app):
        self.app = app
        self.visible = False
        self.title = "About"
        self.message = ""
        self._ok_btn: Button | None = None

    def open(self, title: str, message: str) -> None:
        self.title = title or "About"
        self.message = message or ""
        self.visible = True

    def close(self) -> None:
        self.visible = False

    def rect(self, sw: int, sh: int) -> tuple:
        return ((sw - self.W) * 0.5, (sh - self.H) * 0.5, self.W, self.H)

    def on_press(self, mx: float, my: float) -> None:
        if not self.visible:
            return
        if self._ok_btn is not None and self._ok_btn.contains(mx, my):
            self._ok_btn.on_press(mx, my)
            return
        x, y, w, h = self._rect_cache if getattr(self, "_rect_cache", None) else self.rect(*self.app.window.get_size())
        if not (x <= mx <= x + w and y <= my <= y + h):
            self.close()

    def on_enter(self) -> None:
        if self.visible:
            self.close()

    def draw(self, r: Renderer2D, sw: int, sh: int) -> None:
        if not self.visible:
            return
        r.rect(0, 0, sw, sh, 0, (0.0, 0.0, 0.0, 0.6))
        x, y, w, h = self.rect(sw, sh)
        self._rect_cache = (x, y, w, h)
        r.rect(x, y, w, h, 12, (0.14, 0.16, 0.20, 0.99))
        r.rect(x + 1, y + 1, w - 2, 34, 10, (0.18, 0.22, 0.30, 1.0))
        r.text(x + 16, y + 11, self.title, 15, SECTION)
        ty = y + 52
        for line in _wrap(self.message, 14, w - 40):
            r.text(x + 20, ty, line, 14, (0.92, 0.95, 1.0, 1.0))
            ty += 21
        self._ok_btn = Button(x + w - 130, y + h - 52, 110, 34,
                              "Close", on_press=self.close)
        self._ok_btn.draw(r)


class ShortcutsDialog:
    """Keyboard reference, grouped by area and scrollable."""

    W = 780
    H = 580
    ROW_H = 22.0
    HEAD_H = 30.0

    SECTIONS = (
        ("Global", (
            ("F1", "Guided tour"),
            ("Ctrl+,", "Preferences"),
            ("Ctrl+S", "Save project"),
            ("Ctrl+Shift+S", "Save project as…"),
            ("Ctrl+O", "Load project…"),
            ("Ctrl+N", "New scene"),
            ("Tab", "Presentation mode"),
            ("Esc", "Close menu / exit the editor"),
        )),
        ("Editor tools", (
            ("V", "Select"),
            ("B", "Brush"),
            ("E", "Eraser"),
            ("N", "Pen"),
            ("L", "Line"),
            ("R", "Rectangle"),
            ("O", "Ellipse"),
            ("P", "Colour picker"),
            ("M", "Marquee select"),
            ("Q", "Lasso select"),
            ("W", "Magic wand"),
            ("J", "Joint edit"),
        )),
        ("Editing", (
            ("Ctrl+Z", "Undo"),
            ("Ctrl+Shift+Z", "Redo"),
            ("Ctrl+Y", "Redo (alternate)"),
            ("Ctrl+C", "Copy selection"),
            ("Ctrl+X", "Cut selection"),
            ("Ctrl+V", "Paste selection"),
            ("Ctrl+A", "Select all pixels"),
            ("Delete", "Clear selected pixels / key"),
        )),
        ("Timeline", (
            ("Space", "Play / pause"),
            ("← / →", "Step one frame"),
            ("Shift+← / →", "Nudge one second"),
            ("Home / End", "Go to start / end"),
            ("T", "Show / hide the timeline"),
            ("Ctrl+E", "Export the selected object"),
        )),
        ("View & presentation", (
            ("Wheel", "Zoom the canvas"),
            ("Middle drag", "Pan the canvas"),
            ("Right drag", "Pan the canvas (editor)"),
            ("Home", "Return to the idle pose"),
        )),
    )

    def __init__(self, app):
        self.app = app
        self.visible = False
        self.scroll = 0.0
        self._close_btn: Button | None = None
        self._rect_cache: tuple | None = None

    def open(self) -> None:
        self.visible = True
        self.scroll = 0.0

    def close(self) -> None:
        self.visible = False

    def rect(self, sw: int, sh: int) -> tuple:
        return ((sw - self.W) * 0.5, (sh - self.H) * 0.5, self.W, self.H)

    def _rows_h(self) -> float:
        return sum(self.HEAD_H + len(rows) * self.ROW_H
                   for _title, rows in self.SECTIONS)

    def on_scroll(self, dy: float) -> bool:
        if not self.visible:
            return False
        view = self.H - 120
        self.scroll = max(0.0, min(max(0.0, self._rows_h() - view),
                                   self.scroll - dy * 40.0))
        return True

    def on_press(self, mx: float, my: float) -> None:
        if not self.visible:
            return
        if self._close_btn is not None and self._close_btn.contains(mx, my):
            self._close_btn.on_press(mx, my)
            return
        rect = self._rect_cache
        if rect is not None:
            x, y, w, h = rect
            if not (x <= mx <= x + w and y <= my <= y + h):
                self.close()

    def on_enter(self) -> None:
        if self.visible:
            self.close()

    def draw(self, r: Renderer2D, sw: int, sh: int) -> None:
        if not self.visible:
            return
        r.rect(0, 0, sw, sh, 0, (0.0, 0.0, 0.0, 0.6))
        x, y, w, h = self.rect(sw, sh)
        self._rect_cache = (x, y, w, h)
        r.rect(x, y, w, h, 12, (0.14, 0.16, 0.20, 0.99))
        r.rect(x + 1, y + 1, w - 2, 34, 10, (0.18, 0.22, 0.30, 1.0))
        r.text(x + 16, y + 11, "Keyboard Shortcuts", 15, SECTION)
        view_x, view_y = x + 20, y + 52
        view_w, view_h = w - 40, h - 110
        r.rect(view_x, view_y, view_w, view_h, 8, (0.10, 0.12, 0.15, 1.0))
        r.push_clip(view_x, view_y, view_w, view_h)
        col_w = (view_w - 24) / 2
        cx, cy = view_x + 14, view_y + 10 - self.scroll
        for title, rows in self.SECTIONS:
            r.text(cx, cy + 6, title.upper(), 12, SECTION)
            cy += self.HEAD_H
            for keys, desc in rows:
                r.text(cx, cy + 3, keys, 12, (0.95, 0.86, 0.55, 1.0))
                r.text(cx + 118, cy + 3, desc, 12, FG)
                cy += self.ROW_H
            cy += 12
            if cy - self.scroll > view_y + view_h and cx < view_x + col_w:
                cx = view_x + col_w + 24
                cy = view_y + 10 - self.scroll
        r.pop_clip()
        self._close_btn = Button(x + w - 130, y + h - 50, 110, 34,
                                 "Close", on_press=self.close)
        self._close_btn.draw(r)


class PreferencesDialog:
    """Application settings, edited on a copy and applied on Save.

    Every tab writes into ``self.draft`` so Cancel really cancels, and
    the widgets are built once per tab/size change: rebuilding them each
    frame would drop the caret out of a field being typed into.
    """

    TABS = ("General", "Editor", "Timeline", "Stream")
    W = 860
    H = 600

    def __init__(self, app):
        self.app = app
        self.visible = False
        self.tab = "General"
        self.draft: dict = {}
        self.widgets: list = []
        self._tab_hits: list[tuple] = []
        self._text_inputs: list = []
        self._rect: tuple | None = None
        self._dirty = True
        self._save_btn: Button | None = None
        self._cancel_btn: Button | None = None
        self.status = ""

    # ---- lifecycle -----------------------------------------------------

    def open(self) -> None:
        data = getattr(self.app.config, "data", {}) or {}
        try:
            self.draft = json.loads(json.dumps(data))
        except (TypeError, ValueError):
            self.draft = dict(data)
        self._ensure_defaults()
        self.tab = "General"
        self.status = ""
        self._camera_choice = None
        self.visible = True
        self._dirty = True

    def close(self) -> None:
        self.visible = False
        self._blur_text()

    def _ensure_defaults(self) -> None:
        stream = self.draft.setdefault("stream", {})
        for key, fallback in (("enabled", False), ("port", 8912),
                              ("token", ""), ("fps", 30),
                              ("scale", 100), ("bitrate", 6000),
                              ("audio", False), ("audio_device", ""),
                              ("ffmpeg_path", ""), ("mask", "background"),
                              ("lan", False), ("lan_ip", "")):
            stream.setdefault(key, fallback)
        self.draft.setdefault("window", {}).setdefault("title", "crud32D")
        self.draft.setdefault("model", {})
        self.draft.setdefault("tracking", {})
        self.draft.setdefault("scene", {})
        onion = self.draft.setdefault(
            "onion", {"enabled": False, "before": 2, "after": 1,
                      "step": 3, "opacity": 0.25})
        onion.setdefault("enabled", False)
        self.draft.setdefault("dock", {}).setdefault("sizes", {})

    def rect(self, sw: int, sh: int) -> tuple:
        return ((sw - self.W) * 0.5, (sh - self.H) * 0.5, self.W, self.H)

    # ---- draft access --------------------------------------------------

    def _get(self, *path, default=None):
        node = self.draft
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return default if node is None else node

    def _set(self, value, *path) -> None:
        node = self.draft
        for key in path[:-1]:
            nxt = node.get(key)
            if not isinstance(nxt, dict):
                nxt = {}
                node[key] = nxt
            node = nxt
        node[path[-1]] = value

    # ---- input ---------------------------------------------------------

    def _active_text(self):
        return next((ti for ti in self._text_inputs if ti.active), None)

    def _blur_text(self) -> None:
        for ti in self._text_inputs:
            if ti.active:
                ti.submit()
            ti.active = False

    def on_char(self, ch: str) -> None:
        ti = self._active_text()
        if ti is not None:
            ti.on_char(ch)

    def on_backspace(self) -> None:
        ti = self._active_text()
        if ti is not None:
            ti.on_backspace()

    def on_enter(self) -> None:
        if not self.visible:
            return
        ti = self._active_text()
        if ti is not None:
            ti.submit()
            return
        self._confirm()

    def on_press(self, mx: float, my: float) -> None:
        if not self.visible:
            return
        for tx, ty, tw, th, name in self._tab_hits:
            if tx <= mx <= tx + tw and ty <= my <= ty + th:
                if name != self.tab:
                    self._blur_text()
                    self.tab = name
                    self._dirty = True
                return
        for w in self.widgets:
            if isinstance(w, TextInput) and w.contains(mx, my):
                self._blur_text()
                w.active = True
                w.on_press(mx, my)
                return
        for w in self.widgets:
            if isinstance(w, Label):
                continue
            if w.contains(mx, my):
                self._blur_text()
                w.on_press(mx, my)
                return
        self._blur_text()
        if self._save_btn is not None and self._save_btn.contains(mx, my):
            self._save_btn.on_press(mx, my)
            return
        if self._cancel_btn is not None and self._cancel_btn.contains(mx, my):
            self._cancel_btn.on_press(mx, my)

    def _confirm(self) -> None:
        self._blur_text()
        apply = getattr(self.app, "apply_preferences", None)
        message = apply(self.draft) if apply is not None else None
        if message:
            self.status = str(message)
            return
        # The active camera is a runtime choice, not a config value, so it
        # is replayed after the draft has been accepted.
        choice = getattr(self, "_camera_choice", None)
        if choice:
            pick = getattr(self.app, "set_active_camera", None)
            if pick is not None:
                pick(choice)
        self.close()

    # ---- widget construction -------------------------------------------

    def _ensure_layout(self, sw: int, sh: int):
        rect = tuple(self.rect(sw, sh))
        if rect == self._rect and not self._dirty:
            return rect
        self._rect = rect
        self._dirty = False
        self.widgets = []
        self._text_inputs = []
        x, y, w, h = rect
        cw = (w - 40 - 28) / 2
        left = _PrefsColumn(self, x + 20, y + 92, cw)
        right = _PrefsColumn(self, x + 20 + cw + 28, y + 92, cw)
        self._build_tab(left, right)
        return rect

    def _build_tab(self, left: "_PrefsColumn", right: "_PrefsColumn") -> None:
        if self.tab == "General":
            self._build_general(left, right)
        elif self.tab == "Editor":
            self._build_editor(left, right)
        elif self.tab == "Timeline":
            self._build_timeline(left, right)
        else:
            self._build_stream(left, right)

    def _build_general(self, left: "_PrefsColumn", right: "_PrefsColumn") -> None:
        left.heading("WINDOW & CAPTURE")
        left.text("Window title", self._get("window", "title", default="crud32D"),
                  lambda v: self._set(v.strip() or "crud32D", "window", "title"))
        cameras = self._get("cameras", default=[]) or []
        if cameras:
            left.buttons(
                ("640×480", "1280×720", "1920×1080"),
                f"{cameras[0].get('width', 1280)}×{cameras[0].get('height', 720)}",
                self._set_capture)
        left.slider("Tracking confidence",
                    self._get("tracking", "min_detection_confidence",
                              default=0.5), 0.10, 0.95,
                    lambda v: self._set(round(v, 2), "tracking",
                                        "min_detection_confidence"))
        for key, label in (("pose", "Body tracking"),
                           ("hands", "Hand tracking"),
                           ("face", "Face tracking")):
            left.toggle(label,
                        self._get("tracking", key, default=True),
                        lambda v, k=key: self._set(bool(v), "tracking", k))

        right.heading("SCENE")
        right.text("Background colour (#RRGGBB)",
                   self._get("scene", "bg", default="#FF00FF"),
                   lambda v: self._set(v.strip(), "scene", "bg"))
        right.toggle("Chroma key the background",
                     self._get("scene", "chroma_key_enabled", default=True),
                     lambda v: self._set(bool(v), "scene", "chroma_key_enabled"))
        right.slider("Editor dim",
                     self._get("scene", "edit_dim", default=0.35),
                     0.0, 1.0,
                     lambda v: self._set(round(v, 2), "scene", "edit_dim"))
        right.heading("STARTUP")
        right.toggle("Open the timeline with the editor",
                     self._get("timeline_open", default=True),
                     lambda v: self._set(bool(v), "timeline_open"))
        right.toggle("Show the guided tour on first run",
                     not bool(self._get("tour", "seen", default=False)),
                     self._set_tour_enabled)

    def _set_capture(self, label: str) -> None:
        try:
            w, h = (int(part) for part in label.replace(" ", "").split("×"))
        except ValueError:
            return
        cameras = self.draft.get("cameras") or []
        for cam in cameras:
            if cam.get("role", "primary") == "primary":
                cam["width"], cam["height"] = w, h
        self.status = f"Capture: {w}×{h} (applied on save)"

    def _set_tour_enabled(self, value: bool) -> None:
        self._set(not bool(value), "tour", "seen")

    def _build_editor(self, left: "_PrefsColumn", right: "_PrefsColumn") -> None:
        framing = self._get("model", "framing", default="portrait")
        left.heading("CAMERA FRAMING")
        left.buttons(("closeup", "portrait", "mid", "landscape"), framing,
                     lambda v: self._set(v, "model", "framing"))
        left.slider("Chibi opacity — editing",
                    self._get("model", "opacity_edit", default=0.6),
                    0.0, 1.0,
                    lambda v: self._set(round(v, 2), "model", "opacity_edit"))
        left.slider("Chibi opacity — presentation",
                    self._get("model", "opacity_present", default=1.0),
                    0.0, 1.0,
                    lambda v: self._set(round(v, 2), "model", "opacity_present"))
        left.slider("Zoom", self._get("model", "zoom", default=1.0),
                    0.3, 3.0,
                    lambda v: self._set(round(v, 2), "model", "zoom"))
        left.toggle("Show hands in presentation",
                    self._get("model", "show_hands_in_presentation",
                             default=False),
                    lambda v: self._set(bool(v), "model",
                                        "show_hands_in_presentation"))

        right.heading("IDLE HANDLING")
        right.buttons(("track", "hold"),
                      self._get("model", "idle", "mode", default="track"),
                      lambda v: self._set(v, "model", "idle", "mode"))
        right.slider("Return to idle after (s)",
                     self._get("model", "idle", "return_s", default=2.0),
                     0.0, 10.0,
                     lambda v: self._set(round(v, 2), "model", "idle",
                                         "return_s"))
        right.slider("Release distance",
                     self._get("model", "idle", "release", default=0.045),
                     0.005, 0.30,
                     lambda v: self._set(round(v, 3), "model", "idle",
                                         "release"), fmt="{:.3f}")
        right.heading("ACTIVE CAMERA")
        ids = [str(c.get("id", "front"))
               for c in (self._get("cameras", default=[]) or [])]
        if ids:
            right.buttons(tuple(ids), getattr(self.app, "_active_camera_id", ids[0]),
                          self._set_active_camera)

    def _set_active_camera(self, cid: str) -> None:
        self.status = f"Active camera: {cid} (applied on save)"
        self._camera_choice = cid

    def _build_timeline(self, left: "_PrefsColumn", right: "_PrefsColumn") -> None:
        sizes = self.draft.setdefault("dock", {}).setdefault("sizes", {})
        left.heading("TIMELINE")
        left.toggle("Open with the editor",
                    self._get("timeline_open", default=True),
                    lambda v: self._set(bool(v), "timeline_open"))
        left.slider("Dock height",
                    float(sizes.get("bottom",
                                    self._get("timeline_height", default=260.0)
                                    or 260.0)),
                    160.0, 560.0,
                    lambda v: sizes.__setitem__("bottom", round(v, 1)),
                    fmt="{:.0f}")
        onion = self._get("onion", default={}) or {}
        left.heading("ONION SKIN")
        left.toggle("Show ghost frames", bool(onion.get("enabled", False)),
                    lambda v: self._set(bool(v), "onion", "enabled"))
        left.slider("Ghosts before", onion.get("before", 2), 0.0, 6.0,
                    lambda v: self._set(int(round(v)), "onion", "before"),
                    fmt="{:.0f}")
        left.slider("Ghosts after", onion.get("after", 1), 0.0, 6.0,
                    lambda v: self._set(int(round(v)), "onion", "after"),
                    fmt="{:.0f}")

        right.heading("ONION DETAIL")
        right.slider("Frame step", onion.get("step", 3), 1.0, 12.0,
                     lambda v: self._set(int(round(v)), "onion", "step"),
                     fmt="{:.0f}")
        right.slider("Opacity", onion.get("opacity", 0.25), 0.05, 0.80,
                     lambda v: self._set(round(v, 2), "onion", "opacity"))
        right.heading("NOTE")
        right.note("Timeline height also follows the dock splitter; "
                   "saving here only sets the size used on the next start.")

    def _build_stream(self, left: "_PrefsColumn", right: "_PrefsColumn") -> None:
        left.heading("OBS BROWSER SOURCE")
        left.toggle("Enable the browser-source stream",
                    self._get("stream", "enabled", default=False),
                    lambda v: self._set(bool(v), "stream", "enabled"))
        left.text("Local port", self._get("stream", "port", default=8912),
                  self._set_port, placeholder="8912")
        left.text("Access token", self._get("stream", "token", default=""),
                  lambda v: self._set(v.strip(), "stream", "token"),
                  placeholder="(generated when empty)")
        left.buttons(("Regenerate token",), "", lambda _v: self._regen_token())
        left.toggle("Accept OBS from another machine (LAN)",
                    self._get("stream", "lan", default=False), self._set_lan)
        left.note("Address: " + self._draft_stream_origin())
        left.toggle("Include microphone audio",
                    self._get("stream", "audio", default=False),
                    lambda v: self._set(bool(v), "stream", "audio"))
        left.text("Audio device (blank = system default)",
                  self._get("stream", "audio_device", default=""),
                  lambda v: self._set(v.strip(), "stream", "audio_device"),
                  placeholder="system default")

        right.heading("VIDEO")
        right.buttons(("15", "24", "30", "60"),
                      str(self._get("stream", "fps", default=30)),
                      lambda v: self._set(int(v), "stream", "fps"))
        right.slider("Output scale (%)",
                     self._get("stream", "scale", default=100),
                     25.0, 100.0,
                     lambda v: self._set(int(round(v)), "stream", "scale"),
                     fmt="{:.0f}")
        right.slider("Video bitrate (kbps)",
                     self._get("stream", "bitrate", default=6000),
                     500.0, 12000.0,
                     lambda v: self._set(int(round(v)), "stream", "bitrate"),
                     fmt="{:.0f}")
        right.text("ffmpeg path (blank = PATH)",
                   self._get("stream", "ffmpeg_path", default=""),
                   lambda v: self._set(v.strip(), "stream", "ffmpeg_path"),
                   placeholder="ffmpeg")
        right.heading("SECURITY")
        right.note("On loopback the stream is only reachable from this "
                   "machine. The LAN switch binds every interface so OBS "
                   "elsewhere can load it; every route still needs the "
                   "access token.")

    def _draft_stream_origin(self) -> str:
        """The address OBS loads, from the values being edited.

        The LAN address is probed through the app so the dialog never
        guesses: with the switch off this is the loopback OBS shares with
        us, with it on it is the address another machine can reach. The
        access token travels as a query string on top of this.
        """
        port = self._get("stream", "port", default=8912)
        if self._get("stream", "lan", default=False):
            pinned = str(self._get("stream", "lan_ip", default="") or "")
            probe = getattr(self.app, "_local_ip", None)
            host = pinned.strip() or (probe() if callable(probe) else "") \
                or "0.0.0.0"
        else:
            host = "127.0.0.1"
        return f"http://{host}:{port}/"

    def _set_lan(self, value) -> None:
        """Turn the LAN switch on/off and record the address it hands out."""
        on = bool(value)
        self._set(on, "stream", "lan")
        if on:
            probe = getattr(self.app, "_local_ip", None)
            ip = probe() if callable(probe) else ""
            if ip:
                self._set(ip, "stream", "lan_ip")
        self._dirty = True
        self.status = ("Browser source reachable on the LAN" if on
                       else "Browser source limited to this machine")

    def _set_port(self, text: str) -> None:
        try:
            port = int(str(text).strip())
        except (TypeError, ValueError):
            self.status = "Port must be a number between 1024 and 65535"
            return
        if not (1024 <= port <= 65535):
            self.status = "Port must be a number between 1024 and 65535"
            return
        self._set(port, "stream", "port")

    def _regen_token(self) -> None:
        import secrets
        token = secrets.token_urlsafe(24)
        self._set(token, "stream", "token")
        self._dirty = True
        self.status = "New access token generated"

    # ---- drawing -------------------------------------------------------

    def draw(self, r: Renderer2D, sw: int, sh: int) -> None:
        if not self.visible:
            return
        r.rect(0, 0, sw, sh, 0, (0.0, 0.0, 0.0, 0.6))
        x, y, w, h = self._ensure_layout(sw, sh)
        r.rect(x, y, w, h, 12, (0.14, 0.16, 0.20, 0.99))
        r.rect(x + 1, y + 1, w - 2, 36, 10, (0.18, 0.22, 0.30, 1.0))
        r.text(x + 18, y + 12, "Preferences", 15, SECTION)

        self._tab_hits = []
        tx, ty = x + 20, y + 50
        for name in self.TABS:
            tw = len(name) * 8 + 26
            active = name == self.tab
            r.rect(tx, ty, tw, 28, 6,
                   (0.24, 0.50, 0.82, 1.0) if active
                   else (0.19, 0.21, 0.26, 1.0))
            r.text(tx + 13, ty + 7, name, 13,
                   (0.98, 0.99, 1.0, 1.0) if active else FG)
            self._tab_hits.append((tx, ty, float(tw), 28.0, name))
            tx += tw + 6

        for widget in self.widgets:
            widget._render_dx = 0.0
            widget._render_dy = 0.0
            widget.draw(r)

        if self.status:
            for i, line in enumerate(_wrap(self.status, 12, w - 320)[:2]):
                r.text(x + 20, y + h - 40 + i * 15, line, 12,
                       (0.95, 0.84, 0.52, 1.0))
        self._cancel_btn = Button(x + w - 270, y + h - 48, 110, 34,
                                  "Cancel", on_press=self.close)
        self._cancel_btn.draw(r)
        self._save_btn = Button(x + w - 150, y + h - 48, 130, 34,
                                "Save", on_press=self._confirm)
        self._save_btn.draw(r)


class _PrefsColumn:
    """Cursor-based column builder for a preferences tab."""

    def __init__(self, dialog: PreferencesDialog, x: float, y: float,
                 w: float) -> None:
        self.dlg = dialog
        self.x, self.y, self.w = x, y, w

    def _add(self, widget, slot: float):
        self.dlg.widgets.append(widget)
        self.y += slot
        return widget

    def heading(self, text: str) -> None:
        self._add(Label(self.x, self.y, text, 12, SECTION), 28.0)

    def note(self, text: str) -> None:
        for line in _wrap(text, 11, self.w):
            self._add(Label(self.x, self.y, line, 11, HINT), 16.0)
        self.y += 8.0

    def toggle(self, label: str, value, setter) -> Toggle:
        widget = Toggle(self.x, self.y, self.w, 20, bool(value),
                        label=label, on_change=lambda v: setter(v))
        return self._add(widget, 32.0)

    def slider(self, label: str, value, lo: float, hi: float, setter,
               fmt: str = "{:.2f}") -> NumericSlider:
        widget = NumericSlider(self.x, self.y, self.w, 34,
                               float(value), float(lo), float(hi), label,
                               on_change=lambda v: setter(v), fmt=fmt)
        return self._add(widget, 52.0)

    def text(self, label: str, value, setter, placeholder: str = "") -> TextInput:
        self._add(Label(self.x, self.y, label, 12, FG), 18.0)
        widget = TextInput(self.x, self.y, self.w, 30, text=str(value or ""),
                           placeholder=placeholder or label,
                           on_submit=lambda t: setter(t))
        self.dlg._text_inputs.append(widget)
        return self._add(widget, 40.0)

    def buttons(self, labels, current, setter, height: float = 26.0) -> None:
        labels = list(labels)
        gap = 4.0
        count = max(1, len(labels))
        bw = (self.w - gap * (count - 1)) / count
        for i, label in enumerate(labels):
            mark = "● " if label == current else ""
            self.dlg.widgets.append(Button(
                self.x + i * (bw + gap), self.y, bw, height, mark + label,
                on_press=lambda l=label: setter(l)))
        self.y += height + 10.0


class BatchExportDialog:
    """Pick what to export, then write a PNG per item and a manifest.

    The list is built from the live documents when the dialog opens: the
    chibi (whole, or per layer group), every scene, and every prop in the
    library. Each row switches off on its own, so exporting one prop does
    not mean exporting the other twenty as well.

    Nothing is drawn from the window: the exporter renders each item into
    the renderer's offscreen target, so the PNGs are transparent and the
    result does not depend on what the editor happens to be showing.
    """

    W = 760
    H = 660
    ROW_H = 28.0

    def __init__(self, app):
        self.app = app
        self.visible = False
        self.items: list = []
        self.checks: dict[str, bool] = {}
        self.options = BatchOptions()
        self.scroll = 0.0
        self.status = ""
        self._rows: list[tuple] = []
        self._size_hits: list[tuple] = []
        self._rect: tuple | None = None
        self._export_btn: Button | None = None
        self._cancel_btn: Button | None = None
        self._reset_btn: Button | None = None
        self._trim_toggle: Toggle | None = None
        self.dest_input = TextInput(0, 0, 400, 30, "", "export folder")

    # ---- lifecycle -----------------------------------------------------

    def open(self) -> None:
        """Rebuild the plan from the documents as they are right now."""
        parts = tuple(key for key, _label in CHIBI_PARTS)
        self.items = list(build_items(
            getattr(self.app, "chibi_doc", None),
            getattr(self.app, "scenes", None),
            getattr(self.app, "prop_library", None),
            whole_chibi=True, parts=parts))
        self.checks = {item.filename: True for item in self.items}
        data = getattr(getattr(self.app, "config", None), "data", {}) or {}
        self.options = BatchOptions.from_dict(data.get("batch_export"))
        self.dest_input.text = str(self.options.dest)
        self.dest_input.cursor = len(self.dest_input.text)
        self.dest_input.active = False
        self.scroll = 0.0
        self.status = ""
        self.visible = True

    def close(self) -> None:
        self.visible = False
        self.dest_input.active = False

    def rect(self, sw: int, sh: int) -> tuple:
        return ((sw - self.W) * 0.5, (sh - self.H) * 0.5, self.W, self.H)

    # ---- selection -----------------------------------------------------

    def selected_items(self) -> list:
        return [i for i in self.items if self.checks.get(i.filename, True)]

    def current_options(self) -> BatchOptions:
        """The options as the fields show them, ready to hand to the app."""
        dest = (self.dest_input.text or "").strip() or str(DEFAULT_DEST)
        self.options.dest = Path(dest)
        return self.options

    def toggle_item(self, filename: str) -> None:
        self.checks[filename] = not self.checks.get(filename, True)
        self.status = ""

    def _set_size(self, width: int, height: int) -> None:
        self.options.width = int(width)
        self.options.height = int(height)

    def _set_trim(self, value) -> None:
        self.options.trim = bool(value)

    def _reset_dest(self) -> None:
        self.dest_input.text = str(DEFAULT_DEST)
        self.dest_input.cursor = len(self.dest_input.text)

    def _max_scroll(self, view_h: float) -> float:
        return max(0.0, len(self.items) * self.ROW_H - view_h)

    # ---- export --------------------------------------------------------

    def export(self) -> None:
        """Hand the selection to the app and report what landed."""
        items = self.selected_items()
        if not items:
            self.status = "Tick at least one item to export"
            return
        options = self.current_options()
        run = getattr(self.app, "export_batch", None)
        if run is None:
            self.status = "Export is unavailable"
            return
        try:
            doc = run(items, options)
        except Exception as e:      # a broken batch must not kill the app
            self.status = f"Export failed: {e}"
            return
        plural = "" if doc.get("count") == 1 else "s"
        self.status = (f"Wrote {doc.get('count', 0)} PNG{plural} and "
                       f"{MANIFEST_NAME} → {options.dest}")
        if doc.get("skipped"):
            self.status += f" ({len(doc['skipped'])} skipped)"
        config = getattr(self.app, "config", None)
        if config is not None and hasattr(config, "data"):
            config.data["batch_export"] = options.to_dict()
            save = getattr(config, "save", None)
            if callable(save):
                save()

    # ---- events --------------------------------------------------------

    def on_press(self, mx: float, my: float) -> None:
        if not self.visible:
            return
        for bx, by, bw, bh, filename in self._rows:
            if bx <= mx <= bx + bw and by <= my <= by + bh:
                self.toggle_item(filename)
                return
        if self.dest_input.contains(mx, my):
            self.dest_input.on_press(mx, my)
            return
        self.dest_input.active = False
        for bx, by, bw, bh, width, height in self._size_hits:
            if bx <= mx <= bx + bw and by <= my <= by + bh:
                self._set_size(width, height)
                return
        if self._trim_toggle is not None:
            if self._trim_toggle.contains(mx, my):
                self._trim_toggle.on_press(mx, my)
                return
        for btn in (self._reset_btn, self._export_btn, self._cancel_btn):
            if btn is not None and btn.contains(mx, my):
                btn.on_press(mx, my)
                return

    def on_scroll(self, dy: float) -> bool:
        if not self.visible:
            return False
        view_h = self.H - 70 - 210
        self.scroll = max(0.0, min(self._max_scroll(view_h),
                                   self.scroll - dy * self.ROW_H))
        return True

    def on_char(self, ch: str) -> None:
        if self.visible:
            self.dest_input.on_char(ch)

    def on_backspace(self) -> None:
        if self.visible:
            self.dest_input.on_backspace()

    def on_enter(self) -> None:
        if self.visible:
            self.export()

    def on_escape(self) -> None:
        if self.visible:
            self.close()

    # ---- drawing -------------------------------------------------------

    def draw(self, r: Renderer2D, sw: int, sh: int) -> None:
        if not self.visible:
            return
        r.rect(0, 0, sw, sh, 0, (0.0, 0.0, 0.0, 0.6))
        x, y, w, h = self.rect(sw, sh)
        self._rect = (x, y, w, h)
        r.rect(x, y, w, h, 12, (0.14, 0.16, 0.20, 0.99))
        r.rect(x + 1, y + 1, w - 2, 36, 10, (0.18, 0.22, 0.30, 1.0))
        r.text(x + 18, y + 12, "Batch PNG Export", 15, SECTION)
        r.text(x + 18, y + 46,
               "One transparent PNG per item, plus " + MANIFEST_NAME + ".",
               12, HINT)

        list_x, list_y = x + 18, y + 70
        list_w = w - 36
        list_h = h - 70 - 210
        r.rect(list_x, list_y, list_w, list_h, 8, (0.10, 0.12, 0.15, 1.0))
        self.scroll = max(0.0, min(self._max_scroll(list_h), self.scroll))
        self._rows = []
        r.push_clip(list_x, list_y, list_w, list_h)
        if not self.items:
            r.text(list_x + 14, list_y + 12, "Nothing to export yet", 12, HINT)
        cy = list_y + 6 - self.scroll
        for item in self.items:
            if cy + self.ROW_H >= list_y and cy <= list_y + list_h:
                self._draw_row(r, list_x, list_w, cy, item)
            cy += self.ROW_H
        r.pop_clip()

        oy = y + h - 204
        r.text(x + 18, oy, "SIZE", 12, SECTION)
        self._size_hits = []
        sx = x + 72
        for label, bw, bh in SIZES:
            active = (self.options.width, self.options.height) == (bw, bh)
            btn = Button(sx, oy - 6, 66, 24, ("* " if active else "") + label,
                         on_press=lambda a=bw, b=bh: self._set_size(a, b))
            btn._render_dx = btn._render_dy = 0.0
            btn.draw(r)
            self._size_hits.append((sx, oy - 6, 66.0, 24.0, bw, bh))
            sx += 70
        self._trim_toggle = Toggle(x + 300, oy - 4, 44, 20,
                                   self.options.trim,
                                   label="Trim transparent margins",
                                   on_change=self._set_trim)
        self._trim_toggle._render_dx = self._trim_toggle._render_dy = 0.0
        self._trim_toggle.draw(r)

        r.text(x + 18, oy + 42, "FOLDER", 12, SECTION)
        self.dest_input.x = x + 84
        self.dest_input.y = oy + 34
        self.dest_input.w = w - 320
        self.dest_input.h = 30
        self.dest_input._render_dx = 0.0
        self.dest_input._render_dy = 0.0
        self.dest_input.draw(r)
        self._reset_btn = Button(x + w - 220, oy + 34, 100, 30,
                                 "Use ./exports", on_press=self._reset_dest)
        self._reset_btn._render_dx = self._reset_btn._render_dy = 0.0
        self._reset_btn.draw(r)

        chosen = len(self.selected_items())
        r.text(x + 18, oy + 78,
               f"{chosen} of {len(self.items)} items selected", 12, FG)
        if self.status:
            for i, line in enumerate(_wrap(self.status, 12, w - 340)[:2]):
                r.text(x + 18, oy + 96 + i * 15, line, 12,
                       (0.95, 0.84, 0.52, 1.0))
        self._cancel_btn = Button(x + w - 270, y + h - 48, 110, 34,
                                  "Close", on_press=self.close)
        self._cancel_btn._render_dx = self._cancel_btn._render_dy = 0.0
        self._cancel_btn.draw(r)
        self._export_btn = Button(x + w - 150, y + h - 48, 130, 34,
                                  "Export", on_press=self.export)
        self._export_btn._render_dx = self._export_btn._render_dy = 0.0
        self._export_btn.draw(r)

    def _draw_row(self, r, x, w, y, item) -> None:
        """One item: a tick box, its name and what it will contain."""
        on = self.checks.get(item.filename, True)
        r.rect(x + 4, y, w - 8, self.ROW_H - 2, 5,
               (0.155, 0.170, 0.210, 1.0) if on
               else (0.115, 0.125, 0.150, 1.0))
        bx, by, size = x + 14, y + 6, 14
        r.rect(bx, by, size, size, 3,
               (0.22, 0.50, 0.82, 1.0) if on else (0.20, 0.22, 0.27, 1.0))
        if on:
            r.rect(bx + 3, by + 3, size - 6, size - 6, 2,
                   (0.97, 0.99, 1.0, 1.0))
        label = item.name
        if item.groups:
            label = f"{item.name}  [{len(item.layers_drawn())} layers]"
        r.text(bx + size + 10, y + 6, label, 13, FG if on else HINT)
        r.text(x + w - 200, y + 7, _fit(item.filename, 120, 5.6), 11, HINT)
        r.text(x + w - 74, y + 7, item.kind.upper(), 11,
               (0.90, 0.76, 0.40, 1.0) if on else HINT)
        self._rows.append((x + 4, y, w - 8, self.ROW_H - 2, item.filename))
