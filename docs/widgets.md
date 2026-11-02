# Widgets

A widget is a box on the screen with a name, a value, a set of props, and
a way to reach a Pure Data receiver. Every widget in the catalog is the
same class from :mod:`crude.ui.widget`; what changes is the *kind* - the
prop table, the painter, and the Pd template it maps to. A script-defined
kind is registered the same way at runtime, so a project's custom kind is
the plan's one schema all the way down.

This page is the plan's Tier A widget surface. The prop registry is the
source of truth for what "kind X accepts prop Y" means; `help_gen` writes
the reference table from `crude.ui.catalog` on import, and `WIDGET_PROPS`
and `WIDGET_KINDS` reach the same table from a script.

## What a widget is

```
Widget {
    id: str
    kind: str                  # one of WIDGET_KINDS()
    box: Box                   # x, y, width, height, in logical pixels
    anchor: Anchor             # how the box moves on resize
    props: {name: value}       # the kind's own schema
    children: [Widget]         # PANEL and a script-defined kind may have some
    hit_regions: [Region]      # ON_HIT routes here
    paint_list: PaintList      # what ON_PAINT emitted, cached until dirty
    listener: fn(event)        # the UIContext sets this on focus / click
}
```

Every constructor the catalog names - `TEXT`, `BUTTON`, `SLIDER`,
`TOGGLE`, `METER`, `NUMBER`, `VSLIDER`, `RADIO`, `DROPDOWN`, `XY_PAD`,
`KEYBED`, `ADSR`, `CURVE`, `SPECTRUM`, `WAVEFORM`, `PRESET_BAR`, `PANEL`,
`ICON` - builds one `Widget` and returns a `HostHandle(kind="widget")` the
script holds onto. `WIDGET(kind, ...)` is the generic form every catalog
helper wraps.

## Props

A prop is one entry in the kind's `PROPS` dict. The schema is:

```python
Prop(
    name="value",
    type="float",
    default=0.0,
    range=(0.0, 1.0),
    role="accent",   # optional style role
    writable=True,   # False for read-only kinds like METER's peak
    pd="value",      # the Pd inlet this maps to, if any
)
```

`GET(w, "value")` and `SET(w, "value", x)` are the only two calls a script
makes against a prop. `SET` validates against the schema and rejects a
value with a `BindingError` that names the widget, the prop, and what the
range was. Every prop write invalidates the widget's `PaintList` unless
the kind marks the prop `cosmetic=false` (only for props a painter never
reads, like a Pd inlet index).

`PROP_DEFINE(kind, name, type, default, ...)` lets a script's own kind
declare the same shape at runtime, so a custom widget answers to the same
`GET`/`SET` rules.

## Style roles

A *role* is a name for a colour the skin resolves at draw time. The
built-in roles are:

| Role       | Read as                |
| ---------- | ---------------------- |
| `base`     | the widget's own body  |
| `accent`   | the value it carries   |
| `ink`      | the label and glyph    |
| `mute`     | disabled-state tint    |
| `focus`    | the focus ring         |
| `hover`    | the pointer-highlight  |
| `danger`   | record / stop          |
| `success`  | on / engaged           |

`STYLE(w, "accent", "#3aa655")` sets the role on one widget; a script can
also override the whole skin. A custom kind reads them through the `P_*`
calls, whose last argument is a *role* word (`"accent"`) rather than a
literal colour - a word that is not a role is read as a colour.

## Placement and layout

`PLACE(w, x, y, width, height)` is the plan's one layout call. The numbers
are in logical pixels; the renderer rounds once at draw, and anchors
re-resolve on resize and DPI change. `NODE_ADD(parent, child)` puts a
widget under a panel; the child's box is relative to the parent.

An `Anchor` pins a widget's edges to the parent's edges. The default
anchor is `(left=0, top=0, right=0, bottom=0)` - a fixed box that moves
with the top-left. `PLACE`'s sugar sets the anchor from a keyword the
script passes, and `crude.ui.layout.PACK` builds a stack. `DPI` rounding
happens at draw time only: the widget's own box is logical, so a script
reading `GET(w, "width")` sees the number it wrote.

`LINK_VALUE(a, b)` mirrors a numeric prop between two widgets and guards
against a feedback loop with a one-hop depth check plus an epsilon
compare. `LINK_STATE(a, prop, b)` is the general form. `UNLINK(a)` breaks
every link a widget is on.

## Handlers

An `ON_*` binding is a `Function` value the interpreter holds in a
per-widget handler list. Every one of them is called by the same dispatch
:func:`UIContext.frame` runs on a frame; the plan's 2 ms budget covers
them all.

```
ON_CLICK(w, fn)      fires once on a full press-and-release inside the box
ON_PRESS / ON_RELEASE fire on either half of that, with a pointer event
ON_DRAG(w, fn)       fires on every move between press and release
ON_SCROLL(w, fn)     wheel events, with `delta` in the handler's arg
ON_CHANGE(w, fn)     fires whenever a value prop actually moved
ON_HIT(w, fn)          routes a click on a sub-region; the return names it
ON_FOCUS / ON_BLUR   keyboard focus, one at a time
ON_KEY / ON_CHAR     global key events, whether or not a widget has focus
ON_ENTER / ON_LEAVE  hover, for widgets with a hover role
ON_MOVE / ON_RESIZE  pointer move, window resize
ON_DROP(w, fn)       a resource dropped on the widget
ON_CLOSE(w, fn)        the window is going away
ON_FRAME(fn)         every frame, once, with `dt` in seconds
```

`OFF_FRAME(fn)` removes a frame handler. The widget form peels back as you
drop arguments: `OFF(w, "click", fn)` removes one handler by identity,
`OFF(w, "click")` every handler for that event, and `OFF(w)` the whole list
off the widget.

A handler that raises is reported by the loop and the widget's `mute`
counter grows; a widget that raises three frames in a row paints a red
placeholder and mutes until a `SET(w, "mute", false)` or a `PAINT_REQUEST`
brings it back. That is the plan's Rule 21: a script painter on the hot
path is a support burden, so the runtime gives it a way to fail *loudly*
and stop.

## Custom kinds

`WIDGET_DEFINE(kind, base, ...)` registers a new kind at runtime, either
on top of a catalog base (inherit the prop table and the hit regions) or
from scratch. The kind's painter is `ON_PAINT(w, fn)`, and its regions are
what each `ON_HIT(w, fn)` returns. `PAINT_REQUEST(w)` invalidates the
`PaintList` cache so the painter is called again on the next frame.

A custom kind may name a `pd_object` prop for its Pd twin. `PD_GUI(patch,
kind, x, y, ...)` builds a Pd GUI box the same script builds a widget
for; `PD_EMIT(patch, widget, [x, y, w, h])` wires them with one call. When a
custom kind has no `pd_object` template, `PD_EMIT` degrades to plain
send/recv and warns; the plan's Rule 17.

`WIDGET_KINDS()` returns the catalog plus every registered custom kind;
`WIDGET_PROPS(kind)` is the schema a script can read; `WIDGET_HAS(w, prop)`
is the check that a widget's own kind holds one named prop.

## Pd-parity widgets

Thirteen kinds have a Pd twin: `tgl bng hslider vslider nbx vu cnv radio
dropdown` plus `meter` `xy` `kbd` `adc`. Each one declares:

- its **arg layout** (the `.pd` file's argument order, pinned to Pd 0.54);
- its **prop→arg** mapping, which `PD_EMIT` walks;
- a golden `.pd` file under `tests/ui/goldens/`, so a change to the
  template that alters the saved bytes is a failing test rather than a
  silent drift.

The mapping table is `crude.ui.pdgui.TEMPLATES`. A script that reads
`PD_GUI_KINDS()` gets the tuple back with the arg names in order.

## Hit testing and z-order

A widget's `hit_regions` are the answer to "which part did the pointer
land on?". `ON_HIT(w, fn)` is the *asked* form - the handler's return names
the part - and the region is a rect in the widget's own space, resolved
through the anchor and the parent chain. Z-order is the paint order the
layout produced; a `click` walks the paint list backward and stops at the
first region the pointer is inside. `PANEL` clips its children to its own
box; a child's hit region
outside the parent's rect is not hit.

## Cache and repaint

A widget's `PaintList` is invalidated by:

- a `SET` on any prop the kind marks `cosmetic=true` (which is most of them);
- `PAINT_REQUEST(w)`, which is the script's way to say "my painter read
  something outside the prop table";
- a resize or DPI change on the widget's parent.

A `PaintList` that is not dirty costs nothing to replay: the renderer is
fed the same list of ops, so a static tree is not repainted from scratch
every frame. The plan's budget (Rule 21) is measured on the *build* of
the list, not its replay.
