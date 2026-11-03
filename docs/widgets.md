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
`KEYBED`, `ADSR`, `CURVE`, `SPECTRUM`, `WAVEFORM`, `PLOT`, `LOGVIEW`,
`PRESET_BAR`, `PANEL`, `SCROLL`, `ICON` - builds one `Widget` and returns a
`HostHandle(kind="widget")` the script holds onto. `WIDGET(kind, ...)` is
the generic form every catalog helper wraps, and it is how a kind with no
sugar word of its own is built - `WIDGET("logview")` and `WIDGET("plot")`
are the same widgets `LOGVIEW(label)` and `PLOT(label)` build.

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

## Label and readout placement

A tiny control has no room to write its own name inside its box - a 25x25
knob cannot fit "Index" and its readout in the dial. Six `_COMMON` rows
grow onto *every* kind so a word can leave the box: `label_place` and
`value_place` (the name and the numeric readout) each take one of
`inside`, `top`, `bottom`, `left`, `right` or `none`, defaulting to
`inside` so nothing that reads the tables today changes; `label_dx` /
`label_dy` / `value_dx` / `value_dy` are number nudges in logical pixels,
applied last.

A side placement anchors the word against the widget's `screen_box` in the
surrounding margin, and the draw clip widens to reach it - only the clip,
never the hit rectangle - so a grown label cannot steal a neighbour's
click. `none` drops the word entirely. Because these are prop rows they are
validated by `SET`, dumped by `crude widgets --json`, and skipped by
`PD_EMIT` (they carry no `pd=` inlet name).

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

## Canvases

Four kinds draw whatever a script pushes into their `data` / `lines` rows
rather than a single value - they turn a list into a picture, and the
script feeds them from `ON_FRAME`:

- `WAVEFORM(label)` - a rolling trace of sample values, the output scope.
  `SET(w, "data", AUDIO_WAVEFORM(frames))` shows what the patch is playing,
  and a flat line when it is not.
- `SPECTRUM(label)` - bands of the same sample list.
- `PLOT(label)` (or `WIDGET("plot")`) - a line graph of *any* value list,
  autoscaled to the span the list itself holds: its own min and max set the
  axis, not a fixed full scale, so `SET(graph, "data",
  PD_ARRAY_READ(patch, "modindex"))` graphs an array of whatever units it
  carries. A flat series is a level line, not a divide by zero; an empty
  list draws the frame and no trace.
- `LOGVIEW(label)` (or `WIDGET("logview")`) - a scrollable text area. The
  script appends rows to its `lines` prop and the newest that fit are shown;
  the wheel moves `scroll` to read older ones. It has no `value` row and no
  Pd twin - it is a place to read, not to send.

`plot` and `logview` are role `grid` kinds with an empty `pd_object`, so
they reach the same `GET`/`SET` schema as the rest of the catalog but are
never emitted to a `.pd` file.

## The scroll area

`SCROLL(label)` (or `WIDGET("scroll")`) is a `panel` that slides what is
inside it. Its own box never moves; the children go to one inner panel -
the widget's `content_child`, named `"{name}#rows"` - and that panel is
moved, so `NODE_ADD(s, w)` on a scroll area puts `w` in the content rather
than in the frame and everything the frame owes a child still holds: the
clip, the hit test, the tree walk and the paint order.

| Prop       | Read as                                              |
| ---------- | ---------------------------------------------------- |
| `offset`   | pixels the content has been slid up; 0 shows the top, and it is never positive |
| `content`  | the height the rows ask for - written by the layout pass, read-only for a script |
| `travel`   | `content` minus the view, never less than zero; also read-only |
| `bar`      | 1 draws the thumb along the right edge, 0 draws nothing and changes no gesture |
| `notch`    | pixels one wheel notch slides; the default is two of the rows below |
| `rows`     | the row height the thumb and the arrows are counted in |

`offset` is the fact and the inner panel's box is its shadow: `SET(s,
"offset", 0)` jumps to the newest row exactly as a hand on the wheel does,
and the next layout pass re-clamps whatever a script wrote to the travel
the rows actually have. `content` and `travel` answer themselves after the
layout pass (`crude.ui.scroll.reconcile`, run over the whole tree by
`restack`), which is why a script that wrote them would only be overwritten.

Every gesture writes the one prop and is installed by the kind, so a script
that registers `ON_SCROLL` of its own is chained after it rather than
replacing it: the wheel moves `notch` a step, a drag on the thumb slides it
from where it was grabbed, a click on the empty track pages towards the
click, `PageUp`/`PageDown` move a view and `Up`/`Down` move one `rows`,
`Home`/`End` go to the two ends. The wheel goes the log view's way, not a
browser's: a notch pushed away from the person moves the window up the
content, which is what the dialog's device lists and the `logview` above
already do.

A row the viewport has slid past its edge is neither drawn nor clickable:
the clip that reaches it is empty, and `hit` refuses a point outside it, so
a list cannot answer a click the person cannot see. This `scroll` is not the
`logview`'s `scroll` prop - that one counts lines of text and belongs to a
face that plots them; these are pixels.

Like `plot` and `logview`, a viewport has no Pd twin: `pd_object` stays
empty, `PD_EMIT` never writes one, and the role it inherits is `panel`,
which keeps it out of the tab order.

## Menus and dialogs

There is no native menu bar in this project's GLFW windows, so a running
program's menu is composed entirely from the widgets above: the bar is a
`PANEL` of `BUTTON`s along the top, each title owns a popup `PANEL` that is a
child of the window's root (not of the bar) so it can overlap the project's own
widgets, and each row is a `BUTTON` - or a `TOGGLE` for a tickable row. Whether
a popup is showing is the `visible` prop, toggled on a title click; a closed
popup paints nothing, so the whole menu costs a handful of widgets until it is
opened. Only one popup is on screen at a time: opening one hides whichever was
before it.

`MENUBAR(name)`, `MENU(bar, title)`, `MENU_ADD(menu, label, fn)`,
`MENU_CHECK(menu, label, on, fn)`, `MENU_SHOW(menu)` and `MENU_HIDE(menu)`,
plus the three dialog rows `MENU_AUDIO_DIALOG(menu)`, `MENU_MIDI_DIALOG(menu)`
and `MENU_SERIAL_DIALOG(menu, link)` (buildings in `bindings/menu.py`, area
`ui`) drive exactly that composition
through `UIContext` - `add` for the bar and popups, `NODE_ADD`-style parentage
for the overlap, and the `Handlers` bridge for a row's click - so nothing needs
a renderer, a window or a GPU to be tested. The `crude.ui.menu` classes hold
the same behaviour for a host that would rather build a menu without the
language.

A settings dialog is the same parts, and the `crude.ui.menu` `MenuDialog` -
hung off a menu row by `Menu.add_dialog` - decides what size they are from the
window it is opened in rather than from a constant. `DIALOG_WIDTH` and
`DIALOG_MAX_HEIGHT` are what a page *prefers*; the height actually taken is the
room between the menu bar and the client rect's bottom edge, held between
`MIN_DIALOG_HEIGHT` and that preference and clamped so the panel always ends
inside the window. A 240 px window therefore gets a page that fits in 240 px
and a 900 px one gets the preferred height rather than a stretched box, and a
`ctx.set_size` answer is the same arithmetic re-run: the status caption and the
action row are anchored to the popup's bottom edge, so Apply / Rescan / Cancel
stay at the bottom and the scrollable middle band is exactly the room left
between them and the fields above.

What is inside is one `SCROLL` body, so a long form is read with the wheel
rather than cut off, and the device lists are `SCROLL`s of their own - one row
per port, each a `TOGGLE` where a device may be switched on individually, as
Pure Data's own Media settings do. `Menu.scroll_to(index, delta)` and
`Menu.list_selection(index)` are the two doors a script and the tests still
come through; they are thin shims over the widget now. Grabbing the title row
moves the page - the popup is placed inside the client rect and re-clamped as
it is dragged, and because the rows are anchored to the popup they follow it
without a second arithmetic pass. A dragged page keeps its place until the menu
row opens it again.

The shipped examples use one dialog each for audio devices (filled from
`AUDIO_DEVICES`, applied with `AUDIO_USE_DEVICES`), MIDI ports (from
`MIDI_DEVICES`, or one disabled row when the list is empty, applied with
`MIDI_OPEN`) and a serial link's rate (`NUMBER` per link, applied
with `SET(link, "baud", n)`), plus Save / Load built from `PRESET_SAVE` and
`PRESET_LOAD`. Each is opened by the one binding named for it, so the lists
are read from the runtime only when the panel opens and never at load.

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
