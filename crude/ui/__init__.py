"""The UI half of the host: a renderer contract, the shapes behind it, and fakes.

A script never imports this package - it calls the widget bindings, which talk to
a :class:`~crude.ui.base.Renderer`. What the package promises is a split:

* **GL-free by default.** Everything exported here - the contract, the maths, the
  recorders, the input router, the splash - works with no display, no GLFW and no
  PyOpenGL, because importing :mod:`crude.ui` has to succeed on a machine that
  cannot give those, and every fake test is written against that fact.
* **The GL lives in :mod:`crude.ui.gl`.** It is reached through
  :func:`crude.ui.gl.open_window` and :func:`crude.ui.gl.make_renderer`, both of
  which import GLFW and PyOpenGL inside the call and answer
  :class:`~crude.ui.base.RendererUnavailable` when either is missing. Nothing in
  this ``__init__`` touches them, so ``import crude.ui`` never can.

The renderer a run gets follows the mode: ``window`` asks for the GL one and
falls back, ``headless`` and ``terminal`` take
:class:`~crude.ui.null_renderer.NullRenderer` and still build and paint their
whole widget tree, and a test takes :class:`~crude.ui.recording.RecordingRenderer`
and reads what was drawn.
"""

from __future__ import annotations

from .base import (
    BLACK,
    CAPS,
    DEFAULT_TEXT_SIZE,
    DEFAULT_THICKNESS,
    DRAW_METHODS,
    NAMED,
    RENDERER_OPS,
    TRANSPARENT,
    WHITE,
    DrawOp,
    RGBA,
    Renderer,
    RendererUnavailable,
    UIError,
    WindowUnavailable,
    as_color,
    texture_key,
    texture_size_of,
)
from .behaviour import (BEHAVIOURS, GESTURE_BUTTON, PAGE_FACTOR, behaviour_for,
                        fraction_of, install_behaviour, logarithmic, on_event,
                        point_fraction, snap_to_step, span_of, typable, value_at)
from .context import (
    BUDGET_MS,
    DEFAULT_UI_SIZE,
    FrameReport,
    UIContext,
    UIContextError,
    window_context,
)
from .faces import FACES, Face, face_for
from .geometry import (
    ALIGNMENTS,
    ARC_TOLERANCE,
    FULL_SWEEP,
    TAU,
    Point,
    Rect,
    alignment,
    align_x,
    arc_points,
    arc_segments,
    arc_sweep,
    capsule,
    circle_bounds,
    clamp,
    clip_overlap,
    polygon_bounds,
    polygon_fan,
    polyline_pairs,
    rect_corners,
    scale_of,
    tapered_quad,
    text_bounds,
    wrap_angle,
)
from .glyphs import (
    BLEED,
    BOX,
    FALLBACK,
    GLYPHS,
    LADDER,
    GlyphAtlas,
    GlyphDef,
    GlyphError,
    dump_icons,
    flatten_pixels,
    has_icon,
    icon_names,
    parse_path,
    raster,
    snap_size,
)
from .handlers import (
    EVENTS,
    FRAME,
    HIT,
    MAX_HANDLER_ERRORS,
    MAX_LINK_DEPTH,
    PAINT,
    SCRIPT_EVENTS,
    WIDGET_EVENTS,
    Handler,
    HandlerError,
    Handlers,
    Link,
    event_name,
)
from .input import (
    ACTIONS,
    BUTTON_NAMES,
    KEY_NAMES,
    MODS,
    MOUSE_BUTTONS,
    InputEvent,
    InputRouter,
    button_name,
    fine_delta,
    is_fine,
    key_name,
)
from .layout import (
    AXIS,
    DEFAULT_GAP,
    EDGES,
    LAYOUT_TOKENS,
    PACK_POLICIES,
    STACK_ALIGNMENTS,
    STACK_DIRECTIONS,
    TOLERANCE,
    Anchor,
    Layout,
    LayoutError,
    Stack,
    anchor,
    anchors_of,
    arrange,
    as_alignment,
    as_direction,
    as_edge,
    as_policy,
    clear_layout,
    describe_layout,
    grid,
    hstack,
    layout_of,
    metric,
    pack,
    pack_of,
    pad,
    place,
    resolve,
    snap,
    snap_box,
    stack,
    unanchor,
    unstack,
    vstack,
)
from .null_renderer import NullRenderer
from .paint import (
    FILL,
    INK,
    LINE,
    MAX_PAINT_OPS,
    MAX_PAINT_WIDGETS,
    Canvas,
    PaintList,
    in_region,
    region_of,
)
from .props import (KINDS, PD_BOXES, SCHEMA, State, TYPES, Kind, Prop,
                    PropError, catalog_names, define, define_prop, digest,
                    document, exists, get_kind, has, hint_for, pd_arguments,
                    pd_object_for, prop_names, props_of, registered_names,
                    reset, settle, settled, states, value_of)
from .recording import RecordingRenderer
from .ring import (
    DEFAULT_BAND,
    DEFAULT_RADIUS,
    DEFAULT_SEGMENTS,
    MAX_SIZE,
    RingTexture,
)
from .skin import (
    COLLOUR_TOKENS,
    DEFAULT_THEME,
    NUMBER_TOKENS,
    ROLES,
    SIZES,
    STATES,
    THEMES,
    Appearance,
    Role,
    Skin,
    Theme,
    as_role,
    as_scale,
    as_theme,
    colour_token_names,
    contrast,
    describe,
    luminance,
    mix,
    number_token_names,
    role_names,
    shade,
    state_names,
    theme_names,
)
from .splash import DEFAULT_HEIGHT, DEFAULT_WIDTH, SplashCanvas
from .menu import Menu, MenuBar, MenuItem
from .widget import (CONTAINER_KIND, POINTER, Panel, Widget, WidgetError,
                     format_value, root_panel, top_at)

__all__ = [
    # contract
    "Renderer", "DrawOp", "RGBA", "RENDERER_OPS", "DRAW_METHODS", "CAPS",
    "NAMED", "WHITE", "BLACK", "TRANSPARENT", "as_color",
    "DEFAULT_TEXT_SIZE", "DEFAULT_THICKNESS",
    "texture_key", "texture_size_of",
    # errors
    "UIError", "RendererUnavailable", "WindowUnavailable",
    # geometry
    "TAU", "FULL_SWEEP", "ARC_TOLERANCE", "ALIGNMENTS", "Point", "Rect",
    "clamp", "scale_of", "rect_corners", "capsule", "tapered_quad",
    "wrap_angle", "arc_sweep", "arc_segments", "arc_points", "circle_bounds",
    "polyline_pairs", "polygon_fan", "polygon_bounds", "clip_overlap",
    "text_bounds", "align_x", "alignment",
    # the fakes every test and every headless run uses
    "RecordingRenderer", "NullRenderer",
    # the ring a scope is drawn from, and the splash a window project starts on
    "RingTexture", "DEFAULT_SEGMENTS", "DEFAULT_RADIUS", "DEFAULT_BAND",
    "MAX_SIZE",
    "SplashCanvas", "DEFAULT_WIDTH", "DEFAULT_HEIGHT",
    # the icons, generated and atlased from paths, with no image file anywhere
    "GlyphAtlas", "GlyphDef", "GlyphError", "GLYPHS", "icon_names",
    "has_icon", "dump_icons", "flatten_pixels", "parse_path", "raster",
    "snap_size",
    "LADDER", "FALLBACK", "BOX", "BLEED",
    # what the pointer and the keyboard mean, with no window to have said it in
    "InputRouter", "InputEvent", "ACTIONS", "MODS", "MOUSE_BUTTONS",
    "KEY_NAMES", "BUTTON_NAMES", "key_name", "button_name", "is_fine",
    "fine_delta",
    # the skin every widget is painted from: tokens, themes, roles, states
    "Skin", "Theme", "Role", "Appearance", "THEMES", "ROLES", "STATES",
    "SIZES", "DEFAULT_THEME", "COLLOUR_TOKENS", "NUMBER_TOKENS", "as_theme",
    "as_role", "as_scale", "mix", "shade", "luminance", "contrast",
    "describe", "theme_names", "role_names", "state_names",
    "colour_token_names", "number_token_names",
    # the one prop table every reader agrees on: SET, tooltips, docs, PD_EMIT
    "KINDS", "Kind", "Prop", "PropError", "State", "TYPES", "PD_BOXES",
    "SCHEMA", "get_kind", "exists", "has", "define", "define_prop", "reset",
    "catalog_names", "registered_names", "props_of", "prop_names", "hint_for",
    "settle", "settled", "states", "value_of", "pd_object_for", "pd_arguments",
    "digest", "document",
    # the tree itself: boxes, z-order, hit tests, focus, and a face to paint
    "Widget", "Panel", "WidgetError", "POINTER", "CONTAINER_KIND",
    "format_value", "root_panel", "top_at",
    # an in-window menu bar, built from the widgets above and nothing else
    "MenuBar", "Menu", "MenuItem",
    # the faces the plate-and-label face cannot answer with: the synth kinds'
    # tables, looked up the same inheritance-chain way as props and behaviours
    "FACES", "Face", "face_for",
    # what a gesture means per kind, which is the other half of that tree: the
    # table a press, a drag, a wheel and a typed number are answered from
    "BEHAVIOURS", "behaviour_for", "install_behaviour", "on_event",
    "GESTURE_BUTTON", "PAGE_FACTOR", "span_of", "logarithmic",
    "fraction_of", "value_at", "snap_to_step", "point_fraction", "typable",
    # the placement pass: place, pack, anchors, stacks, and the device grid
    "place", "pack", "pack_of", "pad", "anchor", "anchors_of", "unanchor",
    "stack", "hstack", "vstack", "grid", "arrange", "unstack", "resolve",
    "layout_of", "clear_layout", "describe_layout", "metric", "snap",
    "snap_box", "Layout", "Stack", "Anchor", "LayoutError", "EDGES", "AXIS",
    "PACK_POLICIES", "STACK_DIRECTIONS", "STACK_ALIGNMENTS", "LAYOUT_TOKENS",
    "DEFAULT_GAP", "TOLERANCE",
    "as_policy", "as_edge", "as_direction", "as_alignment",
    # the pass that makes a frame: layout, the pointer, the tree in z-order
    "UIContext", "UIContextError", "FrameReport", "DEFAULT_UI_SIZE",
    "BUDGET_MS", "window_context",
    # what a script wired to the tree: several handlers per event, and the links
    "Handlers", "Handler", "Link", "HandlerError", "EVENTS", "WIDGET_EVENTS",
    "SCRIPT_EVENTS", "FRAME", "PAINT", "HIT", "MAX_HANDLER_ERRORS",
    "MAX_LINK_DEPTH", "event_name",
    # a widget a script paints for itself: the canvas it draws on and the cache
    # its ops land in until the face changes
    "Canvas", "PaintList", "region_of", "in_region", "MAX_PAINT_WIDGETS",
    "MAX_PAINT_OPS", "FILL", "INK", "LINE",
]
