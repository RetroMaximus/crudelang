"""Docked scene timeline — transport, ruler, and one row per channel.

The panel is dockable to any side; while docked only its canvas-facing
edge resizes. Every row is a whole channel (expandable to per-property
sub-rows):

* ``CHIBI``  → pose snapshots
* ``CAMERA`` → presentation zoom / pan
* ``CHIBI · BASE / CLOTHES / ACCESSORIES`` → one fold-out per chibi
  document layer group, one row per layer inside it
* one row per scene layer → transform + opacity + visibility

Interactions are routed by :class:`App` through ``hit_test``: the returned
objects implement ``on_press`` / ``on_drag`` / ``on_release``.
"""
from __future__ import annotations

import math

from model.timeline import (CHIBI_TARGET, CAMERA_TARGET, CHIBI_LAYER_KIND,
                            EASINGS, has_prop)
from ui.menu import MenuItem
from ui.panels import _PanelBase, PANEL_BG, SECTION
from ui.widgets import Button

TRANSPORT_H = 34
RULER_H = 22
ROW_H = 24
GUTTER_W = 180
PAD = 8
# Gutter column layout: toggle | caret | label.
TOGGLE_X = 6
CARET_X = 30
LABEL_X = 48
INDENT = 14

MONO_FG = (0.80, 0.88, 1.00, 1.0)
DIM_FG = (0.55, 0.60, 0.70, 1.0)
TRACK_BG_A = (0.155, 0.170, 0.210, 1.0)
TRACK_BG_B = (0.135, 0.148, 0.185, 1.0)
TRACK_SEL = (0.200, 0.270, 0.380, 1.0)
KEY_FILL = (0.78, 0.66, 0.34, 1.0)
KEY_SEL = (1.00, 0.86, 0.38, 1.0)
PLAYHEAD = (0.30, 0.90, 1.00, 1.0)
# Synthetic target for the "CHIBI LAYERS" divider row.
CHIBI_LAYERS_TARGET = "chibi_layers"
# The chibi document is split by layer group, so clothes and accessories
# are not buried under the body art that outnumbers them. Props are not
# chibi layers at all: that group folds out the active scene's layers.
CHIBI_GROUPS = (("base", "CHIBI · BASE"),
                ("clothes", "CHIBI · CLOTHES"),
                ("accessories", "CHIBI · ACCESSORIES"),
                ("props", "SCENE · PROPS"))
CHIBI_GROUP_KIND = "chibi_group"
CHIBI_GROUP_TARGET = "chibi_group"


def chibi_group_target(group: str) -> str:
    """Expansion / row target for one chibi layer group."""
    return f"{CHIBI_GROUP_TARGET}:{group}"


# ---------------------------------------------------------------
# Hit objects
# ---------------------------------------------------------------

class _TimelineHit:
    def __init__(self, panel):
        self.panel = panel

    def on_press(self, mx, my): pass
    def on_drag(self, mx, my): pass
    def on_release(self, mx, my): pass


class _PlayheadHit(_TimelineHit):
    """Click/drag anywhere in the ruler or empty track to scrub."""

    def on_press(self, mx, my):
        self.panel.app.timeline_seek(self.panel._x_to_time(mx))

    def on_drag(self, mx, my):
        self.panel.app.timeline_seek(self.panel._x_to_time(mx))


class _RowHit(_TimelineHit):
    def __init__(self, panel, row):
        super().__init__(panel)
        self.row = row

    def on_press(self, mx, my):
        p = self.panel
        p.selected_kind = self.row["kind"]
        p.selected_target = self.row["target"]
        p.selected_key = None


class _TrackToggleHit(_TimelineHit):
    def __init__(self, panel, row):
        super().__init__(panel)
        self.row = row

    def on_press(self, mx, my):
        row = self.row
        track = row["track"]
        if track is None:
            tl = self.panel._timeline()
            if tl is not None:
                track = tl.ensure_track(row["target"], row["kind"])
                row["track"] = track
        if track is not None:
            track.enabled = not track.enabled
        self.panel.rebuild()


class _AddKeyHit(_TimelineHit):
    def __init__(self, panel, row):
        super().__init__(panel)
        self.row = row

    def on_press(self, mx, my):
        self.panel.app.timeline_key_row(self.row)


class _ExpandHit(_TimelineHit):
    """Caret in the gutter: show/hide a row's property sub-rows."""

    def __init__(self, panel, row):
        super().__init__(panel)
        self.row = row

    def on_press(self, mx, my):
        self.panel.toggle_expanded(self.row)


class _AddPropHit(_TimelineHit):
    """The ``+`` on a property sub-row keys just that property."""

    def __init__(self, panel, row):
        super().__init__(panel)
        self.row = row

    def on_press(self, mx, my):
        app = self.panel.app
        row = self.row
        key_prop = getattr(app, "timeline_key_prop", None)
        if key_prop is not None:
            key_prop(row["kind"], row["target"], row["prop"])


class _AddGroupHit(_TimelineHit):
    """The ``+`` on an empty chibi group adds a layer to that group."""

    def __init__(self, panel, row):
        super().__init__(panel)
        self.row = row

    def on_press(self, mx, my):
        add = getattr(self.panel.app, "add_chibi_layer", None)
        if add is not None:
            add(self.row.get("group_name") or "base")


class _KeyHit(_TimelineHit):
    """Select + retime a single key. One undo per drag."""

    def __init__(self, panel, row, track, key, prop=None):
        super().__init__(panel)
        self.row = row
        self.track = track
        self.key = key
        self.prop = prop
        self._start_x = 0.0
        self._moved = False
        self._pushed = False

    def on_press(self, mx, my):
        p = self.panel
        p.selected_kind = self.row["kind"]
        p.selected_target = self.row["target"]
        p.selected_prop = self.prop
        p.selected_key = (self.track, self.key)
        self._start_x = mx
        self._moved = False
        self._pushed = False

    def on_drag(self, mx, my):
        p = self.panel
        tl = p._timeline()
        if tl is None:
            return
        if not self._moved:
            if abs(mx - self._start_x) < 1.5:
                return
            # Snapshot before the first mutation so one drag = one undo.
            p.app.push_scene_undo()
            self._pushed = True
            self._moved = True
        t = tl.snap(p._x_to_time(mx))
        if abs(t - self.key.t) > 1e-9:
            self.key.t = t
            self.track.sort_keys()


# ---------------------------------------------------------------
# Panel
# ---------------------------------------------------------------

class TimelinePanel(_PanelBase):
    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)
        self.rows: list[dict] = []
        self._buttons: list[Button] = []
        self.px_per_sec = 60.0
        self.scroll_t = 0.0
        self.row_scroll = 0.0
        self.selected_kind: str | None = None
        self.selected_target: str | None = None
        self.selected_prop: str | None = None
        self.selected_key = None          # (track, Keyframe) | None
        # (kind, target) of the rows whose property sub-rows are showing.
        # The base chibi group starts open; the other two stay folded.
        self._expanded: set[tuple[str, str]] = {
            (CHIBI_GROUP_KIND, chibi_group_target("base"))}
        self._readout_x = x + 10
        self.rebuild()

    # ---- document access -------------------------------------------
    def _scene(self):
        scenes = getattr(self.app, "scenes", None)
        if not scenes:
            return None
        return scenes.get(self.app.active_scene_name)

    def _timeline(self):
        scene = self._scene()
        return scene.timeline if scene is not None else None

    # ---- geometry ---------------------------------------------------
    def _contains(self, mx, my):
        return (self.x <= mx <= self.x + self.w
                and self.y <= my <= self.y + self.h)

    def _transport_rect(self):
        return (self.x, self.y + 40, self.w, TRANSPORT_H)

    def _ruler_y(self):
        return self.y + 40 + TRANSPORT_H

    def _rows_viewport(self):
        top = self._ruler_y() + RULER_H
        return (self.x, top, self.w, max(0.0, self.y + self.h - top - 6))

    def _time_to_x(self, t):
        return self.x + GUTTER_W + (float(t) - self.scroll_t) * self.px_per_sec

    def _x_to_time(self, mx):
        return self.scroll_t + (mx - (self.x + GUTTER_W)) / self.px_per_sec

    def _time_at(self, mx) -> float:
        """The snapped, clamped time under the pointer."""
        t = max(0.0, self._x_to_time(mx))
        tl = self._timeline()
        if tl is None:
            return t
        return max(0.0, min(float(tl.duration), tl.snap(t)))

    def row_at(self, my):
        """The row under ``my`` anywhere in the rows band, or None."""
        vx, vy, vw, vh = self._rows_viewport()
        if not (vy <= my <= vy + vh):
            return None
        idx = int((my - vy + self.row_scroll) // ROW_H)
        if 0 <= idx < len(self.rows):
            return self.rows[idx]
        return None

    def clear_selection(self) -> None:
        """Drop the row and key selection."""
        self.selected_kind = None
        self.selected_target = None
        self.selected_prop = None
        self.selected_key = None

    def _row_top(self, idx):
        return self._rows_viewport()[1] + idx * ROW_H - self.row_scroll

    def _max_row_scroll(self):
        vh = self._rows_viewport()[3]
        return max(0.0, len(self.rows) * ROW_H - vh)

    def _ruler_step(self):
        for s in (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0):
            if s * self.px_per_sec >= 52.0:
                return s
        return 120.0

    # ---- panel protocol --------------------------------------------
    def footer_action(self):
        pass

    def on_scroll(self, dy, mx, my):
        if not self._contains(mx, my):
            return False
        vx, vy, vw, vh = self._rows_viewport()
        if vx <= mx <= vx + GUTTER_W and vy <= my <= vy + vh:
            self.row_scroll = max(
                0.0, min(self._max_row_scroll(),
                         self.row_scroll - dy * ROW_H))
            return True
        if mx < self.x + GUTTER_W:
            return True     # header / gutter — consume, no zoom
        t_at = self._x_to_time(mx)
        new_pps = max(20.0, min(400.0, self.px_per_sec * (1.1 ** dy)))
        if abs(new_pps - self.px_per_sec) > 1e-6:
            self.px_per_sec = new_pps
            anchor = mx - (self.x + GUTTER_W)
            self.scroll_t = max(0.0, t_at - anchor / self.px_per_sec)
        return True

    def max_scroll(self):
        return self._max_row_scroll()

    # ---- build ------------------------------------------------------
    def rebuild(self):
        self._snapshot_build_origin()
        scene = self._scene()
        tl = scene.timeline if scene is not None else None
        tracks = tl.tracks if tl is not None else []
        # The prop the prop editor holds a draft of: its row is tagged so
        # the canvas and the timeline always name the same prop.
        _target = getattr(self.app, "prop_target", None)
        draft_name = _target[1] if _target else None

        parents: list[dict] = []
        parents.append({
            "kind": "chibi", "target": CHIBI_TARGET, "label": "CHIBI",
            "track": next((t for t in tracks if t.kind == "chibi"), None),
            "missing": False, "visible": True,
        })
        parents.append({
            "kind": "camera", "target": CAMERA_TARGET, "label": "CAMERA",
            "track": next((t for t in tracks if t.kind == "camera"), None),
            "missing": False, "visible": True,
        })

        names: set[str] = set()
        if scene is not None:
            for L in sorted(scene.layers, key=lambda x: x.z, reverse=True):
                names.add(L.name)
                parents.append({
                    "kind": "layer", "target": L.name, "label": L.name,
                    "track": tl.track_for(L.name, "layer") if tl else None,
                    "missing": False, "visible": L.visible,
                    "draft": L.name == draft_name,
                })

        # Chibi document layers get their own fold-out per layer group,
        # so clothes and accessories can be animated alongside the pose
        # instead of one long undifferentiated list. Empty groups still
        # appear, with a ``+`` that creates a layer in that group.
        chibi_layers = list(getattr(getattr(self.app, "chibi_doc", None),
                                    "layers", []) or [])
        orphan_chibi = [tr for tr in tracks
                        if tr.kind == CHIBI_LAYER_KIND
                        and tr.target not in {L.name for L in chibi_layers}]
        for group, label in CHIBI_GROUPS:
            group_layers = sorted(
                (L for L in chibi_layers
                 if getattr(L, "group", "base") == group),
                key=lambda L: L.z, reverse=True)
            group_orphans = orphan_chibi if group == "base" else []
            # Props are scene layers, so the last group folds out the
            # active scene's document instead of a chibi group nothing
            # writes to any more.
            scene_props = (sorted(scene.layers, key=lambda L: L.z,
                                  reverse=True)
                           if (group == "props" and scene is not None) else [])
            group_row = {
                "kind": CHIBI_GROUP_KIND,
                "target": chibi_group_target(group),
                "label": label, "track": None, "missing": False,
                "visible": True, "group": True, "group_name": group,
                "empty": (not group_layers and not group_orphans
                          and not scene_props),
            }
            group_row["expanded"] = self._is_expanded(group_row)
            parents.append(group_row)
            if not group_row["expanded"]:
                continue
            for L in group_layers:
                parents.append({
                    "kind": CHIBI_LAYER_KIND, "target": L.name,
                    "label": L.name,
                    "track": (tl.track_for(L.name, CHIBI_LAYER_KIND)
                              if tl else None),
                    "missing": False, "visible": L.visible,
                    "group_name": group,
                })
            for L in scene_props:
                parents.append({
                    "kind": "layer", "target": L.name, "label": L.name,
                    "track": (tl.track_for(L.name, "layer")
                              if tl else None),
                    "missing": False, "visible": L.visible,
                    "group_name": group,
                    "draft": L.name == draft_name,
                })
            for tr in group_orphans:
                parents.append({
                    "kind": CHIBI_LAYER_KIND, "target": tr.target,
                    "label": tr.target, "track": tr,
                    "missing": True, "visible": True,
                    "group_name": group,
                })

        if tl is not None:
            for tr in tl.tracks:
                if tr.kind == "layer" and tr.target not in names:
                    parents.append({
                        "kind": "layer", "target": tr.target,
                        "label": tr.target, "track": tr,
                        "missing": True, "visible": True,
                    })

        rows: list[dict] = []
        for parent in parents:
            parent["depth"] = 0
            parent["prop"] = None
            parent["parent"] = None
            parent["expanded"] = self._is_expanded(parent)
            rows.append(parent)
            if not parent["expanded"]:
                continue
            for path in self._property_paths(parent, scene):
                rows.append({
                    "kind": parent["kind"], "target": parent["target"],
                    "label": path, "track": parent["track"],
                    "missing": parent["missing"],
                    "visible": parent["visible"],
                    "depth": 1, "prop": path, "parent": parent,
                    "expanded": False,
                })

        self.rows = rows
        self.row_scroll = max(0.0, min(self._max_row_scroll(), self.row_scroll))
        if (self.selected_target is not None
                and not any(r["target"] == self.selected_target
                            and r["kind"] == self.selected_kind
                            for r in rows)):
            self.selected_target = None
            self.selected_kind = None
            self.selected_prop = None
            self.selected_key = None
        self._layout_buttons()

    # ---- property tree ----------------------------------------------
    def _is_expanded(self, row) -> bool:
        return (row["kind"], row["target"]) in self._expanded

    def toggle_expanded(self, row) -> None:
        """Caret click: fold/unfold a row's property sub-rows."""
        key = (row["kind"], row["target"])
        if key in self._expanded:
            self._expanded.discard(key)
        else:
            self._expanded.add(key)
        self.rebuild()

    def _property_paths(self, row, scene) -> list[str]:
        """Dotted paths to list under a row (empty when the app can't say)."""
        fn = getattr(self.app, "property_paths", None)
        if fn is None:
            return []
        return list(fn(row["kind"], scene, row["track"]))

    def _layout_buttons(self):
        app = self.app
        tl = self._timeline()
        bx = self.x + 10
        by = self.y + 46
        bh = 22
        self._buttons = []

        def add(label, bw, cb):
            nonlocal bx
            b = Button(bx, by, bw, bh, label, on_press=cb)
            b._render_dx = 0.0
            b._render_dy = 0.0
            self._buttons.append(b)
            bx += bw + 4
            return b

        add("|<", 30, app.timeline_restart)
        add("<", 26, lambda: app.timeline_step_frames(-1))
        add("Pause" if app.timeline_playing else "Play", 54,
            app.timeline_toggle_play)
        add(">", 26, lambda: app.timeline_step_frames(1))
        add("Loop*" if (tl is not None and tl.loop) else "Loop", 52,
            app.toggle_loop)

        bx += 10
        add("+Layer", 58, app.timeline_key_selected_layer)
        pose_btn = add("+Pose", 54, lambda: None)
        pose_btn._cb = lambda b=pose_btn: app._open_timeline_pose_menu(
            b.x, b.y + b.h)
        add("+Cam", 50, app.timeline_key_camera)
        add("Auto*" if app.autokey_enabled else "Auto", 50,
            app.toggle_autokey)

        bx += 10
        onion = getattr(app, "onion", None) or {}
        add("Onion*" if onion.get("enabled") else "Onion", 56,
            app.toggle_onion)
        add("On-", 34, lambda: app.nudge_onion(-1))
        add("On+", 34, lambda: app.nudge_onion(1))

        # Dock controls: the timeline is only visible in the editor, so
        # its home is a dock; these buttons change which one.
        bx += 10
        current = getattr(self, "dock_region", None)
        add("Top*" if current == "top" else "Top", 44,
            lambda: app._dock_panel(self, "top"))
        add("Bottom*" if current == "bottom" else "Bottom", 62,
            lambda: app._dock_panel(self, "bottom"))
        add("Float", 48, lambda: app._undock_panel(self))

        self._readout_x = bx + 8

    # ---- hit testing ------------------------------------------------
    def _key_at(self, mx, my):
        if not self._contains(mx, my):
            return None
        vx, vy, vw, vh = self._rows_viewport()
        if not (vx + GUTTER_W <= mx <= vx + vw and vy <= my <= vy + vh):
            return None
        idx = int((my - vy + self.row_scroll) // ROW_H)
        if not (0 <= idx < len(self.rows)):
            return None
        row = self.rows[idx]
        track = row["track"]
        if track is None:
            return None
        prop = row.get("prop")
        for k in track.keys:
            if prop and not has_prop(k.value, prop):
                continue
            if abs(self._time_to_x(k.t) - mx) <= 7:
                return _KeyHit(self, row, track, k, prop)
        return None

    def hit_test(self, mx, my):
        if not self._contains(mx, my):
            return None

        for b in self._buttons:
            if b.contains(mx, my):
                return b

        vx, vy, vw, vh = self._rows_viewport()
        if vy <= my <= vy + vh:
            idx = int((my - vy + self.row_scroll) // ROW_H)
            if 0 <= idx < len(self.rows):
                row = self.rows[idx]
                if row.get("group"):
                    # Group divider: the caret folds it, an empty group's
                    # ``+`` adds a layer to it, anything else is inert.
                    if (row.get("empty")
                            and vx + GUTTER_W - 30 <= mx <= vx + GUTTER_W - 6):
                        return _AddGroupHit(self, row)
                    if vx + CARET_X - 2 <= mx <= vx + CARET_X + 12:
                        return _ExpandHit(self, row)
                    return _TimelineHit(self)
                is_prop = bool(row.get("prop"))
                if vx + TOGGLE_X - 2 <= mx <= vx + TOGGLE_X + 22 and not is_prop:
                    return _TrackToggleHit(self, row)
                if vx + CARET_X - 2 <= mx <= vx + CARET_X + 12 and not is_prop:
                    return _ExpandHit(self, row)
                if vx + GUTTER_W - 30 <= mx <= vx + GUTTER_W - 6:
                    if is_prop:
                        return _AddPropHit(self, row)
                    return _AddKeyHit(self, row)
                if vx <= mx <= vx + GUTTER_W:
                    return _RowHit(self, row)
                hit = self._key_at(mx, my)
                if hit is not None:
                    return hit
                return _PlayheadHit(self)
            return None

        ry = self._ruler_y()
        if ry <= my <= ry + RULER_H and mx >= vx + GUTTER_W:
            return _PlayheadHit(self)
        return None

    def build_context_items(self, mx, my):
        """Right-click menu for the rows band.

        Over a key: the key's own menu (easing, copy to every layer,
        duplicate, delete). Anywhere else in the rows: insert a keyframe
        for the whole rig, for the row under the pointer, for the pose or
        for the camera — each stamped with the snapped time under the
        pointer, not the playhead.
        """
        if my < self._rows_viewport()[1]:
            return None
        app = self.app
        t = self._time_at(mx)
        hit = self._key_at(mx, my)
        items: list[MenuItem] = []
        if hit is not None:
            track, key, prop = hit.track, hit.key, hit.prop
            for e in EASINGS:
                mark = "● " if key.easing == e else "   "
                items.append(MenuItem(
                    f"{mark}Easing: {e.title()}",
                    lambda tr=track, k=key, ee=e:
                        app.timeline_set_easing(tr, k, ee)))
            items.append(MenuItem(
                f"Apply to All Layers at {t:.2f}s",
                lambda r=hit.row, k=key, tt=t:
                    app.timeline_apply_key_to_all(
                        r["kind"], r["target"], tt, key=k)))
            if prop:
                items.append(MenuItem(
                    "Delete Property Key",
                    lambda tr=track, k=key, p=prop:
                        app.timeline_delete_prop(tr, k, p)))
            else:
                items.append(MenuItem("Duplicate Key",
                                      lambda tr=track, k=key:
                                          app.timeline_duplicate_key(tr, k)))
                items.append(MenuItem("Delete Key",
                                      lambda tr=track, k=key:
                                          app.timeline_delete_key(tr, k)))
            items.append(MenuItem("Deselect", self.clear_selection))
            return items

        row = self.row_at(my)
        items.append(MenuItem(
            f"Insert Keyframe (Global) @ {t:.2f}s",
            lambda tt=t: app.timeline_key_global(tt)))
        if row is not None and not row.get("group"):
            label = row.get("label") or row.get("target")
            items.append(MenuItem(
                f"Insert Keyframe ({label}) @ {t:.2f}s",
                lambda r=row, tt=t: app.timeline_key_row(r, tt)))
        items.append(MenuItem(
            f"Insert Pose Key @ {t:.2f}s",
            lambda tt=t: app.timeline_key_pose(None, tt)))
        items.append(MenuItem(
            f"Insert Camera Key @ {t:.2f}s",
            lambda tt=t: app.timeline_key_camera(tt)))
        return items

    # ---- drawing ----------------------------------------------------
    def draw(self, r, _w, _h):
        r.rect(self.x, self.y, self.w, self.h, 14, PANEL_BG)
        r.text(self.x + 20, self.y + 12, "TIMELINE", 14, SECTION)
        # Resize grip on the top edge (this dock resizes upward only).
        r.rect(self.x + self.w * 0.5 - 22, self.y + 2, 44, 4, 2,
               (0.45, 0.55, 0.75, 1.0))
        self._draw_fold_button(r)
        self._draw_stream_button(r)

        self._layout_buttons()
        for b in self._buttons:
            b.draw(r)

        tl = self._timeline()
        dur = tl.duration if tl is not None else 0.0
        r.text(self._readout_x, self.y + 50,
               f"{self.app.playhead:.2f}s / {dur:.2f}s", 12, MONO_FG)

        self._draw_ruler(r)
        self._draw_rows(r)

    def _draw_ruler(self, r):
        vx, vy, vw, vh = self._rows_viewport()
        ry = self._ruler_y()
        gx = self.x + GUTTER_W
        gw = max(0.0, self.x + self.w - gx - PAD)
        r.rect(self.x, ry, self.w, RULER_H, 0, (0.115, 0.130, 0.170, 1.0))
        r.rect(self.x, ry, self.w, 1, 0, (0.25, 0.30, 0.40, 1.0))
        if gw <= 0:
            return
        r.push_clip(gx, ry, gw, RULER_H)
        tl = self._timeline()
        dur = tl.duration if tl is not None else 0.0
        # End-of-timeline marker
        if dur > 0:
            ex = self._time_to_x(dur)
            if gx <= ex <= gx + gw:
                r.rect(ex, ry + 2, 1.5, RULER_H - 4, 0,
                       (0.90, 0.45, 0.45, 0.9))
        step = self._ruler_step()
        t = math.floor(self.scroll_t / step) * step
        end = self.scroll_t + gw / self.px_per_sec + step
        while t <= end:
            if t >= 0.0:
                x = self._time_to_x(t)
                r.rect(x, ry + RULER_H - 8, 1.0, 8, 0,
                       (0.50, 0.60, 0.80, 1.0))
                r.text(x + 3, ry + 2, f"{t:g}", 10, DIM_FG)
            t += step
        r.pop_clip()

    def _draw_rows(self, r):
        vx, vy, vw, vh = self._rows_viewport()
        if vh <= 0:
            return
        r.push_clip(vx, vy, vw, vh)
        for i, row in enumerate(self.rows):
            top = self._row_top(i)
            if top + ROW_H < vy:
                continue
            if top > vy + vh:
                break
            self._draw_row(r, i, row, top)
        r.pop_clip()

        # Gutter divider
        r.rect(vx + GUTTER_W - 1.5, vy, 1.5, vh, 0, (0.30, 0.36, 0.46, 1.0))

        # Playhead line spanning the rows
        px = self._time_to_x(self.app.playhead)
        if vx + GUTTER_W - 2 <= px <= vx + vw:
            r.rect(px - 0.75, vy, 1.5, vh, 0, PLAYHEAD)
            hy = self._ruler_y() + RULER_H - 7
            r.rect(px - 5, hy, 10, 10, 2, PLAYHEAD,
                   rotation=math.pi * 0.25)

        self._draw_row_scrollbar(r, vx, vy, vw, vh)

    def _draw_row(self, r, i, row, top):
        vx, _, vw, _ = self._rows_viewport()
        depth = int(row.get("depth") or 0)
        prop = row.get("prop")
        if row.get("group"):
            r.rect(vx, top, vw, ROW_H, 0, (0.115, 0.130, 0.170, 1.0))
            r.text(vx + 8, top + 5,
                   "v" if row.get("expanded") else ">", 11, DIM_FG)
            r.text(vx + CARET_X + 6, top + 5, row["label"], 11, SECTION)
            if row.get("empty"):
                r.text(vx + GUTTER_W - 74, top + 6, "no layers ·", 10, DIM_FG)
                r.text(vx + GUTTER_W - 20, top + 5, "+", 13, MONO_FG)
            return
        selected = (self.selected_target == row["target"]
                    and self.selected_kind == row["kind"]
                    and self.selected_prop == prop)
        bg = TRACK_SEL if selected else (TRACK_BG_A if i % 2 == 0 else TRACK_BG_B)
        if depth:
            bg = (bg[0] * 0.85, bg[1] * 0.85, bg[2] * 0.85, 1.0)
        r.rect(vx, top, vw, ROW_H, 0, bg)

        track = row["track"]
        enabled = track is None or track.enabled

        if not depth:
            # Eye / mute toggle
            ex = vx + TOGGLE_X
            r.rect(ex, top + (ROW_H - 14) * 0.5, 20, 14, 4,
                   (0.28, 0.68, 1.0, 1.0) if enabled else (0.25, 0.28, 0.33, 1.0))
            r.text(ex + 7, top + 4, "o" if enabled else "-", 10,
                   (0.95, 0.97, 1.0, 1.0))
            # Expand caret for the property sub-rows
            r.text(vx + CARET_X + 2, top + 5,
                   "v" if row.get("expanded") else ">", 11, DIM_FG)

        label = row["label"]
        if row["missing"]:
            label += "  (missing)"
        if row.get("draft"):
            label += "  · draft"
        col = MONO_FG if row.get("visible", True) else DIM_FG
        if not enabled or depth:
            col = DIM_FG
        lab_x = vx + LABEL_X + depth * INDENT
        r.text(lab_x, top + 5, self._elide(label, (vx + GUTTER_W - 34) - lab_x),
               12, col)

        # Row "+" — key the current state (or just this property) here
        akx = vx + GUTTER_W - 28
        r.rect(akx, top + 4, 20, ROW_H - 8, 4, (0.22, 0.50, 0.82, 1.0))
        r.text(akx + 7, top + 5, "+", 12, (0.98, 0.99, 1.0, 1.0))

        if track is None:
            r.text(vx + GUTTER_W + 8, top + 5, "(no keys)", 11, DIM_FG)
            return

        cy = top + ROW_H * 0.5
        sel = self.selected_key
        for k in track.keys:
            kx = self._time_to_x(k.t)
            if kx < vx + GUTTER_W - 8 or kx > vx + vw + 8:
                continue
            holds = prop is None or has_prop(k.value, prop)
            if not holds:
                # The key exists but carries no value for this property.
                r.rect(kx - 2, cy - 2, 4, 4, 1, (0.50, 0.44, 0.28, 0.55))
                continue
            is_sel = (sel is not None and sel[1] is k and holds
                      and (prop is None or prop == self.selected_prop))
            size = 12 if is_sel else 9
            colour = KEY_SEL if is_sel else KEY_FILL
            if not enabled:
                colour = (colour[0] * 0.5, colour[1] * 0.5,
                          colour[2] * 0.5, 1.0)
            if is_sel:
                # Selection ring around the picked key.
                ring = size + 4
                r.rect(kx - ring * 0.5, cy - ring * 0.5, ring, ring, 2,
                       (1.00, 0.93, 0.58, 0.55), rotation=math.pi * 0.25)
            r.rect(kx - size * 0.5, cy - size * 0.5, size, size, 2,
                   colour, rotation=math.pi * 0.25)

    @staticmethod
    def _elide(text: str, max_px: float, char_px: float = 6.4) -> str:
        """Truncate a label with an ellipsis so it fits the gutter."""
        if max_px <= 0:
            return ""
        fit = int(max_px // char_px)
        if len(text) <= fit:
            return text
        if fit <= 1:
            return "\u2026"
        return text[:fit - 1] + "\u2026"

    def _draw_row_scrollbar(self, r, vx, vy, vw, vh):
        max_s = self._max_row_scroll()
        if max_s <= 0:
            return
        content_h = len(self.rows) * ROW_H
        track_x = vx + vw - 6
        r.rect(track_x, vy, 4, vh, 2, (0.22, 0.24, 0.30, 1.0))
        thumb_h = max(24, vh * vh / max(1.0, content_h))
        thumb_y = vy + (vh - thumb_h) * (self.row_scroll / max_s)
        r.rect(track_x, thumb_y, 4, thumb_h, 2, (0.45, 0.55, 0.75, 1.0))
