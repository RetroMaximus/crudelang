"""Scene hierarchy and asset browser panels.

Both sit in the bottom dock, to the left of the timeline: the hierarchy
answers "what is on stage, in what order", the browser answers "what can
I put on stage". A panel reserves a fixed column of its own by declaring
``column_width``; ``App._dock_columns`` lays those columns out left to
right ahead of everything stacked beside them.

Rows are rebuilt with the panel, so every callback closes over the
document as it is right now rather than a stale selection.
"""
from __future__ import annotations

from ui.menu import MenuItem, sep
from ui.panels import _PanelBase, FG, SECTION
from ui.widgets import Button, Label, TextInput, Widget

ROW_H = 26.0
EYE_W = 22.0
INDENT = 12.0
COLUMN_W = 250.0
# Columns read left to right in this order, whatever order the dock
# happens to list them in: the hierarchy first, then the browser.
COLUMN_ORDER = 0
BROWSER_ORDER = 1

DIM = (0.56, 0.62, 0.74, 1.0)
ROW_BG = (0.150, 0.170, 0.210, 1.0)
ROW_HOVER = (0.180, 0.210, 0.270, 1.0)
ROW_SEL = (0.200, 0.280, 0.400, 1.0)
EYE_ON = (0.55, 0.80, 1.00, 1.0)
CHIP = (0.90, 0.76, 0.40, 1.0)

CHIBI_GROUPS = (("base", "Base"), ("clothes", "Clothes"),
                ("accessories", "Accessories"), ("props", "Props"))


def _hex_rgba(text: str):
    """``#RRGGBB`` → a float rgba tuple, or None for a named/blank fill.

    The renderer takes one colour shape everywhere, so the alpha is part
    of the value rather than appended at the draw call.
    """
    s = str(text or "").strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0,
                int(s[4:6], 16) / 255.0, 1.0)
    except ValueError:
        return None


def _fit(text: str, width: float, per_char: float = 7.0) -> str:
    """Trim a label to the pixels it has, keeping a trailing ellipsis."""
    text = str(text or "")
    room = max(3, int(width / per_char))
    if len(text) <= room:
        return text
    return text[:max(3, room - 1)] + "…"


class _TreeRow(Widget):
    """One row of the hierarchy or of the browser list.

    The eye box on the left toggles visibility without selecting the row;
    every other pixel selects it. ``payload`` names what the row points
    at so a context menu can rebuild the same actions.
    """

    def __init__(self, panel, x, y, w, label, on_click=None, on_eye=None,
                 visible=True, selected=False, depth=0, chip="",
                 payload=None, accent=None):
        super().__init__(x, y, w, ROW_H)
        self.panel = panel
        self.label = str(label)
        self.on_click_cb = on_click
        self.on_eye_cb = on_eye
        self.visible = bool(visible)
        self.selected = bool(selected)
        self.depth = int(depth)
        self.chip = str(chip or "")
        self.payload = dict(payload or {})
        self.accent = accent

    # ---- geometry -------------------------------------------------------
    def _screen(self):
        return (self.x + self._render_dx, self.y + self._render_dy)

    def eye_rect(self):
        sx, sy = self._screen()
        return (sx + 4.0, sy + 4.0, float(EYE_W) - 8.0, ROW_H - 8.0)

    # ---- events ---------------------------------------------------------
    def on_press(self, mx, my):
        ex, ey, ew, eh = self.eye_rect()
        if (self.on_eye_cb is not None
                and ex <= mx <= ex + ew and ey <= my <= ey + eh):
            self.on_eye_cb()
            return
        if self.on_click_cb is not None:
            self.on_click_cb()

    # ---- drawing --------------------------------------------------------
    def draw(self, r):
        sx, sy = self._screen()
        mouse = getattr(self.panel.app, "_mouse", (0.0, 0.0))
        hover = (sx <= mouse[0] <= sx + self.w
                 and sy <= mouse[1] <= sy + self.h)
        bg = ROW_SEL if self.selected else (
            ROW_HOVER if hover else ROW_BG)
        r.rect(sx, sy, self.w, self.h, 5, bg)
        if self.on_eye_cb is not None:
            ex, ey, ew, eh = self.eye_rect()
            r.rect(ex, ey, ew, eh, 3, (0.10, 0.12, 0.15, 1.0))
            if self.visible:
                r.rect(ex + 3, ey + 3, ew - 6, eh - 6, 2, EYE_ON)
        if self.accent is not None:
            r.rect(sx + EYE_W + 1, sy + 6, 3, self.h - 12, 1.5, self.accent)
        tw = self.w - EYE_W - 14 - (len(self.chip) * 6 + 10 if self.chip else 0)
        tx = sx + EYE_W + 6 + self.depth * INDENT
        r.text(tx, sy + self.h * 0.5 - 8, _fit(self.label, tw), 13,
               FG if self.visible else DIM)
        if self.chip:
            cw = len(self.chip) * 6
            r.text(sx + self.w - cw - 8, sy + self.h * 0.5 - 8, self.chip,
                   11, CHIP if self.selected else DIM)


class SceneHierarchyPanel(_PanelBase):
    """Stage stack: scenes front to back, the active scene's props, the
    chibi document and its layer groups."""

    column_width = COLUMN_W
    column_order = COLUMN_ORDER

    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)
        self.rows: list[_TreeRow] = []
        # Chibi groups start folded so the props above stay in reach.
        self.expanded: set[str] = set()
        self.rebuild()

    # ---- document access -----------------------------------------------
    def _scenes(self) -> dict:
        scenes = getattr(self.app, "scenes", None)
        return scenes if isinstance(scenes, dict) else {}

    def _front_first(self, scenes) -> list[tuple[str, object]]:
        return sorted(scenes.items(),
                      key=lambda kv: (float(getattr(kv[1], "z", 0.0)), kv[0]),
                      reverse=True)

    def _active_scene(self):
        return self._scenes().get(getattr(self.app, "active_scene_name", None))

    def _prop_target_name(self):
        """The scene prop the prop editor is authoring, or None."""
        target = getattr(self.app, "prop_target", None)
        return target[1] if target else None

    # ---- build ----------------------------------------------------------
    def rebuild(self):
        self._snapshot_build_origin()
        self.widgets = []
        self.rows = []
        self.scroll_y = 0.0
        px = self.x + 14
        pw = max(120.0, self.w - 28)
        cy = self.y + 46
        app = self.app
        if getattr(app, "prop_edit_active", False):
            # The draft sits on top of the tree rather than replacing it:
            # a prop is authored *in place*, so the stage it belongs to
            # has to stay in reach while the canvas is open.
            cy = self._build_prop_section(px, pw, cy)
        scenes = self._scenes()
        active = getattr(app, "active_scene_name", None)

        self.widgets.append(Label(px, cy, "STAGE STACK", 12, SECTION))
        cy += 22
        if not scenes:
            self.widgets.append(Label(px, cy, "No scenes yet", 11, DIM))
            cy += 20
        for name, doc in self._front_first(scenes):
            selected = name == active
            row = self._add(self.rows, px, cy, pw,
                            ("● " if selected else "") + str(name),
                            on_click=lambda n=name: app.set_active_scene(n),
                            on_eye=lambda n=name: self._toggle_scene(n),
                            visible=bool(getattr(doc, "visible", True)),
                            selected=selected,
                            chip=f"z{float(getattr(doc, 'z', 0.0)):g}",
                            payload={"kind": "scene", "name": name})
            cy += ROW_H + 2
            if not selected or row is None:
                continue
            layers = sorted(getattr(doc, "layers", []) or [],
                            key=lambda L: float(getattr(L, "z", 0.0)),
                            reverse=True)
            if not layers:
                self.widgets.append(Label(
                    px + EYE_W + 6 + INDENT, cy, "empty — add a prop", 11, DIM))
                cy += 20
            editing = self._prop_target_name()
            for layer in layers:
                cy = self._layer_row(px, pw, cy, layer, depth=1,
                                     kind="scene_layer", scope="scene",
                                     mark_editing=(layer.name == editing))
        cy += 6

        # --- Chibi document: groups fold out to their layers.
        doc = getattr(app, "chibi_doc", None)
        chibi_layers = list(getattr(doc, "layers", []) or [])
        self.widgets.append(Label(px, cy, "CHIBI", 12, SECTION))
        cy += 22
        if not chibi_layers:
            self.widgets.append(Label(px, cy, "No chibi layers", 11, DIM))
            cy += 20
        in_chibi = bool(getattr(app, "editor_active", False)
                        and not getattr(app, "scene_edit_active", False))
        for group, title in CHIBI_GROUPS:
            members = [L for L in chibi_layers
                       if getattr(L, "group", "base") == group]
            if group == "props" and not members:
                # Props are scene layers now, and the stage stack above
                # already lists them: an always-empty chip would only
                # suggest the chibi still owns them. A legacy document
                # that does have some keeps its group.
                continue
            opened = group in self.expanded
            self._add(self.rows, px, cy, pw,
                      f"{title}", on_click=lambda g=group: self._fold_group(g),
                      visible=any(getattr(L, "visible", True) for L in members),
                      chip=("v " if opened else "> ") + str(len(members)),
                      payload={"kind": "group", "name": group})
            cy += ROW_H + 2
            if not opened:
                continue
            for layer in sorted(members, key=lambda L: getattr(L, "z", 0),
                               reverse=True):
                cy = self._layer_row(px, pw, cy, layer, depth=2,
                                     kind="chibi_layer", scope="chibi",
                                     selected_name=getattr(
                                         getattr(app, "editor", None),
                                         "selected_layer_name", None),
                                     highlight=in_chibi)

        self._finalize_layout()

    def _build_prop_section(self, px, pw, cy) -> float:
        """The prop editor's hierarchy: the draft's layers, nothing else."""
        ed = getattr(self.app, "prop_editor", None)
        doc = getattr(ed, "doc", None)
        layers = list(getattr(doc, "layers", []) or [])
        title = str(getattr(doc, "name", "Prop"))
        self.widgets.append(Label(px, cy, f"PROP · {title.upper()}", 12, SECTION))
        cy += 22
        if not layers:
            self.widgets.append(Label(px, cy, "No layers — draw on the canvas",
                                      11, DIM))
            cy += 20
        selected = getattr(ed, "selected_layer_name", None)
        for layer in sorted(layers,
                            key=lambda L: float(getattr(L, "z", 0.0)),
                            reverse=True):
            cy = self._layer_row(px, pw, cy, layer, depth=1,
                                 kind="prop_layer", scope="prop",
                                 selected_name=selected, highlight=True)
        cy += 8
        if self._prop_target_name() is not None:
            self.widgets.append(Label(
                px, cy, "Authored in place: Apply writes it", 11, DIM))
            cy += 18
            self.widgets.append(Label(
                px, cy, "back onto the matching scene prop.", 11, DIM))
        else:
            self.widgets.append(Label(
                px, cy, "A library draft with no home on stage.", 11, DIM))
        return cy + 20

    def _add(self, bucket, x, y, w, label, **kw):
        row = _TreeRow(self, x, y, w, label, **kw)
        self.widgets.append(row)
        bucket.append(row)
        return row

    def _layer_row(self, px, pw, cy, layer, depth, kind, scope,
                   selected_name=None, highlight=False, mark_editing=False):
        name = str(getattr(layer, "name", "?"))
        selected = bool(selected_name is not None and name == selected_name
                        and highlight)
        chips = []
        if getattr(layer, "in_front", False):
            chips.append("front")
        if mark_editing:
            chips.append("editing")
        if len(getattr(layer, "strokes", []) or ()):
            chips.append(f"{len(layer.strokes)}p")
        self._add(self.rows, px, cy, pw, name,
                  on_click=lambda n=name, s=scope: self._focus(n, s),
                  on_eye=lambda o=layer: self._toggle_layer(o, scope),
                  visible=bool(getattr(layer, "visible", True)),
                  selected=selected, depth=depth,
                  chip=" ".join(chips),
                  accent=_hex_rgba(getattr(layer, "fill", "")),
                  payload={"kind": kind, "name": name, "layer": layer})
        return cy + ROW_H + 2

    # ---- actions ---------------------------------------------------------
    def _toggle_scene(self, name: str) -> None:
        toggle = getattr(self.app, "toggle_scene_visible", None)
        if toggle is not None:
            toggle(name)
        self.rebuild()

    def _toggle_layer(self, layer, scope: str) -> None:
        # Visibility is cheap to flip back, so it never pushes undo.
        setattr(layer, "visible", not bool(getattr(layer, "visible", True)))
        self.refresh()

    def _focus(self, name: str, scope: str) -> None:
        command = {"prop": "focus_prop_layer",
                   "scene": "focus_scene_layer"}.get(scope, "focus_chibi_layer")
        focus = getattr(self.app, command, None)
        if focus is not None:
            focus(name)
        else:
            self.refresh()

    def _fold_group(self, group: str) -> None:
        if group in self.expanded:
            self.expanded.discard(group)
        else:
            self.expanded.add(group)
        self.rebuild()

    def refresh(self) -> None:
        """Rebuild without going through the app's layout pass."""
        self.rebuild()

    def row_at(self, mx, my):
        for row in self.rows:
            if row.contains(mx, my):
                return row
        return None

    # ---- panel protocol --------------------------------------------------
    def footer_action(self):
        app = self.app
        if self._scenes() and not getattr(app, "editor_active", False):
            toggle = getattr(app, "_toggle_editor", None)
            if toggle is not None:
                toggle()
            return
        toggle = getattr(app, "_toggle_scene_edit", None)
        if toggle is not None:
            toggle()

    def context_items_for(self, mx, my) -> list:
        row = self.row_at(mx, my)
        if row is None or not row.payload:
            return []
        kind = row.payload.get("kind")
        name = row.payload.get("name", "")
        app = self.app
        if kind == "scene":
            scenes = self._scenes()
            return [
                MenuItem(f"Scene: {name}", None, enabled=False),
                sep(),
                MenuItem("Show" if not row.visible else "Hide",
                         lambda: self._toggle_scene(name)),
                MenuItem("Duplicate Scene",
                         lambda n=name: app.duplicate_scene(n)),
                MenuItem("Delete Scene",
                         lambda n=name: app.delete_scene(n),
                         enabled=len(scenes) > 1 and
                         getattr(app, "active_scene_name", None) == name),
            ]
        if kind == "scene_layer":
            layer = row.payload.get("layer")
            front = bool(getattr(layer, "in_front", False))
            return [
                MenuItem(f"Prop: {name}", None, enabled=False),
                sep(),
                MenuItem("Select in Scene Editor",
                         lambda n=name: self._focus(n, "scene")),
                MenuItem("Show" if not row.visible else "Hide",
                         lambda o=layer: self._toggle_layer(o, "scene")),
                MenuItem("In Front of Chibi",
                         lambda: self._set_front(layer, not front),
                         checked=front),
                MenuItem("Key at Playhead", lambda n=name: self._key(n)),
                sep(),
                MenuItem("Duplicate", lambda n=name: self._duplicate(n)),
                MenuItem("Bring Forward", lambda n=name: self._restack(n, 1)),
                MenuItem("Send Backward", lambda n=name: self._restack(n, -1)),
                MenuItem("Delete", lambda n=name: self._delete(n)),
            ]
        if kind == "chibi_layer":
            layer = row.payload.get("layer")
            front = bool(getattr(layer, "in_front", False))
            return [
                MenuItem(f"Chibi Layer: {name}", None, enabled=False),
                sep(),
                MenuItem("Select in Chibi Editor",
                         lambda n=name: self._focus(n, "chibi")),
                MenuItem("Show" if not row.visible else "Hide",
                         lambda o=layer: self._toggle_layer(o, "chibi")),
                MenuItem("In Front of Body",
                         lambda: self._set_front(layer, not front),
                         checked=front),
            ]
        if kind == "prop_layer":
            layer = row.payload.get("layer")
            lib = getattr(app, "prop_library", None)
            editing = lib.find(getattr(app, "prop_asset_id", None)) \
                if lib is not None else None
            return [
                MenuItem(f"Prop Layer: {name}", None, enabled=False),
                sep(),
                MenuItem("Select in Prop Editor",
                         lambda n=name: self._focus(n, "prop")),
                MenuItem("Show" if not row.visible else "Hide",
                         lambda o=layer: self._toggle_layer(o, "prop")),
                sep(),
                MenuItem("Apply to Scene"
                         if getattr(app, "prop_target", None)
                         else "Apply to Library",
                         lambda: getattr(app, "apply_prop_changes",
                                        lambda: None)()),
                MenuItem("Duplicate Layer",
                         lambda n=name: self._run_prop(
                             "prop_duplicate_layer", n)),
                MenuItem("Delete Layer",
                         lambda n=name: self._run_prop(
                             "prop_delete_layer", n)),
                sep(),
                MenuItem("Close Prop Editor",
                         lambda: getattr(app, "exit_prop_edit",
                                         lambda: None)()),
                MenuItem(f"Library Entry: {editing.name if editing else 'unsaved'}",
                         None, enabled=False),
            ]
        if kind == "group":
            return [
                MenuItem(f"Group: {name.title()}", None, enabled=False),
                sep(),
                MenuItem("Fold" if name in self.expanded else "Unfold",
                         lambda g=name: self._fold_group(g)),
            ]
        return []

    def _set_front(self, layer, value: bool) -> None:
        setter = getattr(self.app, "set_layer_in_front", None)
        if setter is not None:
            setter(layer, value)
        self.refresh()

    def _key(self, name: str) -> None:
        keyer = getattr(self.app, "timeline_key_layer", None)
        if keyer is not None:
            keyer(name)

    def _duplicate(self, name: str) -> None:
        duplicator = getattr(self.app, "duplicate_scene_layer", None)
        if duplicator is not None:
            duplicator(name)
        self.refresh()

    def _delete(self, name: str) -> None:
        remover = getattr(self.app, "delete_scene_layer", None)
        if remover is not None:
            remover(name)
        self.refresh()

    def _restack(self, name: str, direction: int) -> None:
        restack = getattr(self.app, "restack_scene_layer", None)
        if restack is not None:
            restack(name, direction)
        self.refresh()

    def _run_prop(self, method: str, *args) -> None:
        """Call a prop command by name, then redraw the tree."""
        command = getattr(self.app, method, None)
        if command is not None:
            command(*args)
        self.refresh()

    # ---- drawing ---------------------------------------------------------
    def draw(self, r, _w, _h):
        self._draw_shell(r, "HIERARCHY")
        self._draw_widgets(r)
        self._draw_footer(r, "Scene Edit" if not getattr(
            self.app, "scene_edit_active", False) else "Chibi Edit")


class AssetBrowserPanel(_PanelBase):
    """Searchable list of everything that can be dropped on stage: the
    chibi document's layers and the active scene's props."""

    column_width = COLUMN_W
    column_order = BROWSER_ORDER

    FILTERS = (("All", "all"), ("Chibi", "chibi"),
               ("Scene", "scene"), ("Props", "props"))

    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)
        self.query = ""
        self.filter = "all"
        self.rows: list[_TreeRow] = []
        self._search: TextInput | None = None
        self.rebuild()

    # ---- document access ------------------------------------------------
    def _active_scene(self):
        scenes = getattr(self.app, "scenes", None)
        if not isinstance(scenes, dict):
            return None
        return scenes.get(getattr(self.app, "active_scene_name", None))

    def _chibi_layers(self) -> list:
        doc = getattr(self.app, "chibi_doc", None)
        return list(getattr(doc, "layers", []) or [])

    def _scene_layers(self) -> list:
        scene = self._active_scene()
        return list(getattr(scene, "layers", []) or []) if scene else []

    def _prop_assets(self) -> list:
        assets = getattr(self.app, "prop_assets", None)
        return list(assets()) if callable(assets) else []

    def _matches(self, name: str) -> bool:
        return not self.query or self.query.lower() in str(name).lower()

    # ---- build ----------------------------------------------------------
    def rebuild(self):
        self._snapshot_build_origin()
        self.widgets = []
        self.rows = []
        self.scroll_y = 0.0
        px = self.x + 14
        pw = max(120.0, self.w - 28)
        cy = self.y + 46

        self.widgets.append(Label(px, cy, "ASSET LIBRARY", 12, SECTION))
        cy += 22
        self._search = TextInput(
            px, cy, pw, 28, text=self.query,
            placeholder="Search — Enter applies", on_submit=self._apply_query)
        self.widgets.append(self._search)
        cy += 34

        gap = 4.0
        chip_w = (pw - gap * (len(self.FILTERS) - 1)) / len(self.FILTERS)
        for i, (label, key) in enumerate(self.FILTERS):
            mark = "● " if self.filter == key else ""
            self.widgets.append(Button(
                px + i * (chip_w + gap), cy, chip_w, 24, mark + label,
                on_press=lambda k=key: self._set_filter(k)))
        cy += 32

        if self.filter in ("all", "chibi"):
            cy = self._build_group(
                px, pw, cy, "CHIBI LAYERS",
                [L for L in self._chibi_layers() if self._matches(
                    getattr(L, "name", ""))], "chibi")
        if self.filter in ("all", "scene"):
            cy = self._build_group(
                px, pw, cy, "SCENE PROPS",
                [L for L in self._scene_layers() if self._matches(
                    getattr(L, "name", ""))], "scene")
            self.widgets.append(Button(
                px, cy, pw, 24, "＋ Import PNG…",
                on_press=self._import))
            cy += 30
        if self.filter in ("all", "props"):
            cy = self._build_prop_group(px, pw, cy)
            self.widgets.append(Button(
                px, cy, pw, 24, "＋ New Prop", on_press=self._new_prop))
            cy += 30

        self._finalize_layout()

    def _build_group(self, px, pw, cy, title, layers, scope) -> float:
        self.widgets.append(Label(px, cy, f"{title}  ({len(layers)})", 11, DIM))
        cy += 20
        if not layers:
            self.widgets.append(Label(
                px + 6, cy, "nothing matches this search", 11, DIM))
            cy += 22
            return cy
        current = self._selected_name(scope)
        for layer in sorted(layers,
                            key=lambda L: float(getattr(L, "z", 0.0)),
                            reverse=True):
            name = str(getattr(layer, "name", "?"))
            row = _TreeRow(
                self, px, cy, pw, name,
                on_click=lambda n=name, s=scope: self._focus(n, s),
                visible=bool(getattr(layer, "visible", True)),
                selected=(name == current), depth=1,
                chip=str(getattr(layer, "kind", ""))[:7],
                accent=_hex_rgba(getattr(layer, "fill", "")),
                payload={"kind": f"{scope}_asset", "name": name,
                         "layer": layer, "scope": scope})
            self.widgets.append(row)
            self.rows.append(row)
            cy += ROW_H + 2
        return cy + 6

    def _build_prop_group(self, px, pw, cy) -> float:
        """The prop library: art authored on its own canvas."""
        assets = [a for a in self._prop_assets() if self._matches(a.name)]
        self.widgets.append(Label(
            px, cy, f"PROP LIBRARY  ({len(assets)})", 11, DIM))
        cy += 20
        if not assets:
            self.widgets.append(Label(
                px + 6, cy, "no props yet — use ＋ New Prop", 11, DIM))
            cy += 22
            return cy
        editing = getattr(self.app, "prop_asset_id", None)
        for asset in assets:
            name = asset.name
            row = _TreeRow(
                self, px, cy, pw, name,
                on_click=lambda a=asset: self._edit_prop(a.id),
                visible=True, selected=(asset.id == editing), depth=1,
                chip=f"{len(getattr(asset.doc, 'layers', []) or [])}L",
                payload={"kind": "prop_asset", "name": name, "asset": asset})
            self.widgets.append(row)
            self.rows.append(row)
            cy += ROW_H + 2
        return cy + 6

    def _selected_name(self, scope: str):
        app = self.app
        if scope == "chibi":
            return getattr(getattr(app, "editor", None),
                           "selected_layer_name", None)
        return getattr(getattr(app, "scene_editor", None),
                       "selected_layer_name", None)

    # ---- actions ---------------------------------------------------------
    def _apply_query(self, text: str) -> None:
        self.query = str(text or "").strip()
        self.rebuild()

    def _set_filter(self, key: str) -> None:
        self.filter = key
        self.rebuild()

    def _focus(self, name: str, scope: str) -> None:
        focus = (getattr(self.app, "focus_chibi_layer", None)
                 if scope == "chibi"
                 else getattr(self.app, "focus_scene_layer", None))
        if focus is not None:
            focus(name)
        else:
            self.rebuild()

    def _import(self) -> None:
        opener = getattr(self.app, "_import_png", None)
        if opener is not None:
            opener()

    def _edit_prop(self, asset_id: str) -> None:
        """Open a library prop in the prop editor."""
        opener = getattr(self.app, "edit_prop_asset", None)
        if opener is not None:
            opener(asset_id)
        self.rebuild()

    def _new_prop(self) -> None:
        starter = getattr(self.app, "new_prop", None)
        if starter is not None:
            starter()

    def row_at(self, mx, my):
        for row in self.rows:
            if row.contains(mx, my):
                return row
        return None

    # ---- panel protocol --------------------------------------------------
    def footer_action(self):
        keyer = getattr(self.app, "timeline_key_selected_layer", None)
        if keyer is not None:
            keyer()

    def context_items_for(self, mx, my) -> list:
        row = self.row_at(mx, my)
        if row is None or not row.payload:
            return []
        name = row.payload.get("name", "")
        scope = row.payload.get("scope", "chibi")
        asset = row.payload.get("asset")
        if asset is not None:
            return [
                MenuItem(f"Prop: {name}", None, enabled=False),
                sep(),
                MenuItem("Edit Prop", lambda a=asset: self._edit_prop(a.id)),
                sep(),
                MenuItem("Duplicate Prop", lambda a=asset: self._run(
                    "duplicate_prop_asset", a.id)),
                MenuItem("Delete Prop", lambda a=asset: self._run(
                    "delete_prop_asset", a.id)),
            ]
        items = [
            MenuItem(f"Asset: {name}", None, enabled=False),
            sep(),
            MenuItem("Select on Stage", lambda: self._focus(name, scope)),
            MenuItem("Key at Playhead",
                     lambda n=name, s=scope: self._key(n, s)),
            sep(),
            MenuItem("In Front of Chibi",
                     lambda: self._set_front(row.payload.get("layer"), True)),
            MenuItem("Behind the Chibi",
                     lambda: self._set_front(row.payload.get("layer"), False)),
        ]
        if scope == "scene":
            # Only props can be copied or removed; chibi layer names anchor
            # poses, keys and joint pins.
            items.extend([
                sep(),
                MenuItem("Duplicate Prop", lambda n=name: self._run(
                    "duplicate_scene_layer", n)),
                MenuItem("Delete Prop", lambda n=name: self._run(
                    "delete_scene_layer", n)),
            ])
        return items

    def _run(self, method: str, *args) -> None:
        """Call an app command by name, then redraw the list."""
        command = getattr(self.app, method, None)
        if command is not None:
            command(*args)
        self.rebuild()

    def _key(self, name: str, scope: str) -> None:
        self._focus(name, scope)
        keyer = getattr(self.app, "timeline_key_selected_layer", None)
        if keyer is not None:
            keyer()

    def _set_front(self, layer, value: bool) -> None:
        if layer is None:
            return
        setter = getattr(self.app, "set_layer_in_front", None)
        if setter is not None:
            setter(layer, value)
        self.rebuild()

    # ---- drawing ---------------------------------------------------------
    def draw(self, r, _w, _h):
        self._draw_shell(r, "ASSET BROWSER")
        self._draw_widgets(r)
        self._draw_footer(r, "Key @ Playhead")
