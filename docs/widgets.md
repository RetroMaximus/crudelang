[`Back to Documentation`](https://github.com/RetroMaximus/crudelang/blob/main/README.md#documentation)

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
`TOGGLE`, `METER`, `NUMBER`, `VSLIDER`, `KNOB`, `RADIO`, `DROPDOWN`,
`XY_PAD`, `KEYBED`, `ADSR`, `CURVE`, `SPECTRUM`, `WAVEFORM`, `PLOT`,
`LOGVIEW`, `CLIP`, `PRESET_BAR`, `PANEL`, `SCROLL`, `ICON`, `GROUP` - builds one
`Widget` and returns a `HostHandle(kind="widget")` the script holds onto.
`WIDGET(kind, ...)` is the generic form every catalog helper wraps, and it is
how a kind with no sugar word of its own is built - `WIDGET("logview")` and
`WIDGET("plot")` are the same widgets `LOGVIEW(label)` and `PLOT(label)` build.

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
`GET`/`SET` rules. The arguments past the default are `min`, `max`, `pd_name`
and `hint`, and there is no way to skip one: a row with no range says `""`
where the two numbers would go, which is read as *unbounded at that end* and
not as `0`. Only a word that is neither a number nor empty is refused, by the
row's own name.

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

`PLACE(w, x, y, width, height)` says where a widget stands. Everything else here
says how it stays there while the window does not. The numbers are logical pixels;
the renderer rounds once at draw, so a script reading `GET(w, "width")` sees the
number it wrote, and the whole tree is re-resolved from the window's own size every
frame (`crude.ui.layout.resolve`, run by :meth:`UIContext.layout`) - which is why a
resize is answered without any script handling one. `NODE_ADD(parent, child)` puts
a widget under a panel: the child's box is relative to the parent and it is
clipped to it.

The other four verbs are the same Python functions a host caller uses, with the
same words and the same refusals:

| Call | What it decides |
| ---- | --------------- |
| `ANCHOR(w, edge[, target][, target_edge][, offset])` | glues one edge of `w` to one edge of another widget, or - with no `target`, which `"parent"` says in words - to the panel `w` lives in |
| `PACK(w, policy_x[, policy_y])` | what `w` does when its container moves: `fixed` keeps the size, `grow` keeps the *fraction* of the container that size was, `fill` takes what nothing else used |
| `PAD(w[, value])` | how far this container's children start inside it, in pixels or as a skin token; no argument gives the skin's own answer back |
| `HSTACK` / `VSTACK` / `GRID(panel, ...)` | arranges a panel's children left to right, top to bottom, or in cells of one size; `UNSTACK(panel)` stops |

An edge is `left`, `right`, `top`, `bottom`, `center-x` or `center-y`, and the
short forms the engine already answers (`"l"`, `"r"`, `"cx"`, `"middle"`) work
here too; a policy may be said as `keep`, `scale`, `expand` or `stretch`, and an
alignment as `start`, `center`/`centre`/`middle`, `end` or `stretch`, because
:func:`crude.ui.layout.as_edge`, `as_policy` and `as_alignment` are the only
translators, for a script and a Python caller alike.

Two rules on one axis are not a contradiction. Gluing `left` *and* `right` is a
stretch - that is how a bar pinned to both window edges is the window's width on
the frame after a resize - while gluing a middle to an edge on the same axis is
refused, because the pass would have to pick one. A glue is applied after padding,
after pack and after any stack, and it *owns* the axis it names: a widget in a row
that is also glued on `x` is placed on `x` by its glue alone and still arranged on
`y` by the row, which is what a script that wrote both almost certainly meant.
Taking a glue away (`UNANCHOR(w[, edge])`) hands the axis back to pack and
answers how many rules left; the widget keeps the box the glue last wrote rather
than being re-placed by hand.

`GET(w, "place")` reads a widget's glues back in edge order as the sentences
`ANCHOR` answered with (`"right -> root/box.right -8"`), and `GET(w, "pack")` its
two policies, so every verb's answer can be checked by asking the layout what it
was told. Neither word is a `SET` target: they are rules the pass keeps, not rows
of a prop table, and the refusal names the verb that writes them - as does an
unknown option in a stack's name/value list, which prints the options that verb
has.

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

`ON_DROP` is the one handler a widget shares rather than takes. A drop
callback arrives with paths and no position, so the pointer is wherever the
router last measured it: the widget under that point answers first, and the
window answers as well unless it *is* the widget under the point. A clip that
wants only the files dropped on it and a project that opens anything dropped
anywhere are therefore both satisfiable, in that order.

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

A kind a script defined lives for one program run, and a run is the boundary:
whatever the last entry script registered is forgotten before this one starts,
so a project that defines `badge` can be loaded twice in one interpreter - the
CI matrix taking one example through three modes, a test staging an example
again per case - without the second run being refused its own name. The worked
example is `examples/all_gui`, whose badge page is the only script-drawn kind
any shipped project defines.

## Canvases

Five kinds draw whatever a script pushes into their `data` / `lines` rows
rather than a single value - they turn a list into a picture, and the
script feeds them from `ON_FRAME`:

- `WAVEFORM(label)` - a rolling trace of sample values, the output scope.
  `SET(w, "data", AUDIO_WAVEFORM(frames))` shows what the patch is playing,
  and a flat line when it is not.
- `SPECTRUM(label)` - bands of the same sample list.
- `PLOT(label)` (or `WIDGET("plot")`) - a line graph of *any* value list,
  autoscaled to the span the list itself holds: its own min and max set the
  axis, not a fixed full scale, so `SET(graph, "data",
  PD_ARRAY_READ(patch, "carwave"))` graphs an array of whatever units it
  carries. A flat series is a level line, not a divide by zero; an empty
  list draws the frame and no trace.
- `LOGVIEW(label)` (or `WIDGET("logview")`) - a scrollable text area. The
  script appends rows to its `lines` prop and the newest that fit are shown;
  the wheel moves `scroll` to read older ones. It has no `value` row and no
  Pd twin - it is a place to read, not to send.
- `CLIP(label)` (or `WIDGET("clip")`) - a sample with an axis in *samples*.
  A scope is a rolling trace that knows nothing about where a reading was
  taken; a recorded or loaded take has to be navigated, zoomed and marked, so
  a clip keeps a window (`start`/`count`), a list of draggable marks
  (`markers`) and a cursor (`playhead`). See "The clip" below.

`plot`, `logview` and `clip` are role `grid` kinds with an empty `pd_object`, so
they reach the same `GET`/`SET` schema as the rest of the catalog but are
never emitted to a `.pd` file.

## The clip

`CLIP(label)` is a canvas with a position. Everything about it is one ruler -
:mod:`crude.ui.clip`'s `sample_at` and `pixel_of` - that the face, the pointer
and a script's `GET` all read, so a mark a hand closes on is the mark the eye
was shown and the window a hand dragged is the window `GET(c, "start")` answers
with. The worked example is `examples/sound_recorder`: a clip per track, four
marks wired to four knobs as two coarse/fine pairs, and the coarse step the
script re-reads whenever the take changes.

| Prop       | Read as                                              |
| ---------- | ---------------------------------------------------- |
| `data`     | every sample the clip holds, oldest first - the whole take, not the window drawn from it; empty draws the flat line of silence, and a value that is not a number is drawn as silence rather than dropped out of the list |
| `start`    | the first sample the window shows; the clip keeps it inside what `data` holds |
| `count`    | how many samples the window shows; `0` is *all of them, including what arrives next*, which is what a zoomed-out clip holds while a recording is still running |
| `markers`  | sample positions drawn as lines and grabbed by the pointer, in the order the script listed them and numbered from 1 at the top of the plate; any length, so four are a rough/fine pair at each end and two are a loop |
| `playhead` | the sample the cursor sits on; `-1` draws no cursor at all, the way a `-1` elsewhere in the table means "no limit" |
| `follow`   | keep the cursor inside the window by sliding it, stopping at either end of the take instead of centring a cursor that is not there |
| `points`   | the most columns the face folds the window into; `0` is one per pixel of the box, so a 200 000-sample file draws as ~700 bars rather than a polyline the painter would choke on |

The window is two numbers and not a hidden camera. `count` wider than the take
is kept as it was written and resolved when it is drawn, so a file still
loading is fully shown without anyone re-writing the row; `start` is clamped
against the width it is about to have, and the clamp runs through one door
(`set_window`), which is why a `SET` and a hand cannot leave two different
windows behind. Sample numbers are whole at both write doors: a mark dragged
across two hundred pixels lands on a number a knob wired to `markers[2]` can
compare with.

Three gestures, none of which writes a `value` - a clip holds none:

- a press within six pixels of a mark drags *that* mark and no other; holding
  control divides the movement, so the fine adjustment is sub-sample;
- a press on empty plate grabs the take and slides it, and a window that
  already *is* the take refuses rather than freezing `count` into today's
  length. The press does not jump the playhead: the cursor is the script's to
  place, and a project that wants the jump can take it with `ON_CLICK`, which
  runs after this and may write over it;
- the wheel zooms about the sample under the pointer, in ratio steps, down to
  a floor of one sample and out to the whole take. A wheel carries a movement
  and no position, so the clip remembers the place it was last told by
  `ON_MOVE`; a clip that was never told - a wheel pushed by a script, a tree
  that moved under a cursor that did not - aims at the middle of the plate.

A mark the window has scrolled past is neither drawn nor grabbable, and a
number that is off the plate is not re-numbered by the marks that happen to be
visible: scroll away from the rough start and the two you can still see are 3
and 4. A plate too short for a line of text keeps the lines and gives up the
numbers, because the marks are the information and the numbering is decoration.

`follow` is a layout pass, not a painter. A draw may not write a prop, so
`crude.ui.clip.follow_all` runs beside the restack and the grid reconcile in
`UIContext.layout` and answers how many windows it actually moved - a frame
with a parked cursor or a fully-shown take counts zero and writes nothing.

`clip` here is the widget; `P_CLIP(p, x, y, width, height)` / `P_CLIP_END(p)`
are a painter's clipping rectangle and have nothing to do with it, and
`Widget`'s own `clip` (the box a child is drawn inside) is a third thing. The
three names cannot be merged, so the rows are spelled out: a prop table entry
or a `SET` word of `"start"`, `"count"` or `"markers"` is the widget's, and a
`clip` argument to a paint call is not.

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
The one half of that settle that is *not* an answer about the rows - how wide
the content may be - is taken up before any box is counted (`crude.ui.scroll.fit_content`
is the pass's first step), because a row packed `fill` is sized against the
content panel's box and a panel resized after its rows were arranged would leave
every row one window behind.

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

## Grids and cells

`GRID(box, 2, 10)` divides `box`'s content area into cells of one size and hands
each child its cell, left to right then top to bottom. The rows are what the
children fill: two columns and four children is a 2 x 2 grid, and `columns` of `0`
means as many as make the grid square rather than as many as there are room for, so
five children get three columns and two rows - with the sixth cell measured,
drawable and empty rather than invented. A child whose policy is `fill` takes its
whole cell; anything else sits inside it at the row's `align`, which a grid
defaults to `"center"`.

The arithmetic is one module's, not two. `crude.ui.grid.GridSpec(columns, rows,
gap)` answers `rect_of(index, box)` and `index_at(x, y, box)` for any box, and the
layout pass calls it rather than keeping its own division - so the cell a stack
arranged and the cell a drop would be counted against are literally the same
function of the same rectangle. `index_at` answers `None` for a point in a gap or
outside the panel: the space between two cells belongs to neither, and a drop that
landed there is refused in words rather than snapped somewhere the pointer was not.

A panel that has been arranged answers for itself, out of the rows the pass wrote:

| Prop           | Read as                                                |
| -------------- | ------------------------------------------------------ |
| `grid_columns` | how many columns this panel's cells are counted in, as the pass arranged them; 0 until it has |
| `grid_rows`    | how many rows of cells there are, the empty ones included |
| `grid_gap`     | the pixels between two cells, measured from whatever the stack called for - a token included |
| `dockable`     | whether this panel offers its cells as somewhere a group can be dropped into; the one row here a script writes, and what `GRID(box, 2, 10, ["dockable", 1])` sets |
| `docking`      | a drag is in progress in this window, which is the only moment a `dockable` panel draws its cell outlines |

The first three and the last are written by the layout pass and by the frame, not
by a hand: they are the four members of `crude.ui.props.MEASURED` that are not a
viewport's, so a `SET` refuses them the way it refuses a scroll area's `travel` -
"is measured by the layout pass, not written by a script". A script that named its
own column count would be describing a grid nothing made, and `UNSTACK(box)` takes
the rows back to zero rather than leaving them at the shape of an arrangement that
no longer exists.

The outlines are kept back until they are wanted. An ordinary grid - dockable or
not - draws nothing extra over the panel's own face, because a frame painted
inside every frame is decoration, not information; a `dockable` panel shows its
cells only while a drag is in progress, and the rectangles it draws are read out of
the same rows a `GET` answers with. A panel's face still paints its own label when
it grows this ability, since registering a face replaces that call rather than
adding to it.

## Groups

`GROUP("carrier", deck)` is a section of a page: a band with a word on it and a
body the controls live in. The kind is a panel (`base="panel"`), so it clips, holds
children and is arranged exactly as one is - but a script never sees the two panels
it is built out of. `"{name}#header"` and `"{name}#body"` are made by the tree, and
`NODE_ADD(g, w)` hands `w` to the body through the same `content_child` rule a
viewport uses, so the header is not something a script has to dodge and the clip,
the hit test, the tree walk and the paint order still see one real child each.

| Prop        | Read as                                              |
| ----------- | ---------------------------------------------------- |
| `title`     | the word drawn in the band; empty falls back to the widget's own `label`, and a group with neither draws no word at all |
| `folded`    | 1 shows the band alone. Written by the chevron, by `GROUP_FOLD` and by `SET(g, "folded", 1)` - three spellings of one prop |
| `foldable`  | whether the band offers the fold at all: off draws no chevron and a click where it used to be folds nothing |
| `accordion` | on the panel that *holds* the groups: opening one folds its siblings, so the page is always one section tall |
| `dockable`  | that this group may be picked up by its band and dropped into a cell of a `dockable` grid |
| `cell`      | which cell of its panel's grid it was arranged into, counted from 0; `-1` until a pass has measured one, and read-only after that |
| `order`     | where it stands among its siblings, which is the order a stack, a column or a grid lays them out in; read-only |

**A fold is a measurement, not a memory.** A group is as tall as its band plus what
its body holds - the bottom of the rows inside it, read the way
:mod:`crude.ui.scroll` reads a viewport - so a group of three 30 px knobs is 132
logical pixels tall (`24` band, `9` of skin inset twice, `90` of knobs) and folded
is 42, and opening it again needs nothing remembered. The same rule answers the
other case: a group whose height belongs to its container - packed `fill` or `grow`,
or glued on `top` and `bottom` - keeps that height when it folds, because the body
is hidden and collapsed while the cell is nobody else's to resize. `GROUPS(g)` lists
the groups inside a group, and a nested group that was folded stays folded when its
parent opens: `visible` is written on the body, and the pass never reaches past it.

The worked example is `examples/all_gui`: its deck is four sections in a
dockable three-column grid, and every verb below has a row under the grid that
says back the cell it took or the refusal it was given - `Style` is not
dockable on purpose, so the refusal is a fact on the page rather than a claim
in a manual.

`accordion` is the one row read on a widget other than the one it names. "Opening
one closes its siblings" is a statement about a set, and a nested group's parent is
an ordinary body panel that was never asked to be anything, so the rule lives on the
holder. It fires when something *opens*: a fold that folded every other group on the
page would be a gesture nobody made.

**The order is the arrangement.** `GROUP_MOVE(g, 0)`, `GROUP_UP(g)` and `GROUP_DOWN(g)`
re-order the children of the panel the group lives in, and that list *is* the order a
`VSTACK`, an `HSTACK` and a `GRID` read, so a move is a re-arrange on the next pass
and there is no second order to keep in step. A position past either end is clamped
and answered with the place it took, because a script restoring a saved layout has no
idea how many sections a page has. `order` reads that place back, and it survives
being handed to another panel: the position is where the widget stands in the list
its own container arranges from.

**Docking is the grid's arithmetic, not a new one.** `GRID(deck, 3, 18, ["dockable", 1])`
makes a panel offer its cells; a group that says `dockable` is captured by a press on
its band, the cell under the pointer is `index_at`'s answer, and the release writes
the claim the next pass draws. A cell holds one group: `GROUP_DOCK` refuses a taken
cell, an unknown number, a panel with no cells and a group that never said it could
be carried, each by name, while a pointer gets the answer a hand can read - a drop
onto a taken cell moves nothing at all. `GROUP_UNDOCK(g)` gives up the claim and
leaves the parentage alone, so the group takes the next free cell its order in the
list comes to, the same rule a new child follows. The cells counted are the children
extended by whatever somebody claimed, which is why four groups in three columns is
six cells and one of them is somewhere to drop: the number is read from the declared
grid before any frame has measured it, so a `GROUP_DOCK` on the line after a `GRID`
is not a panel with no cells.

The band is drawn by the group's own face - plate, title, one chevron, and a rule
under the band that an open group has and a folded one does not, because there is
nothing under a band with no body. The `#header` panel is invisible with role
`caption` on purpose: :meth:`~crude.ui.widget.Widget.hit` skips an invisible child,
so a press in the band is claimed by the group, the one widget with a behaviour for
it, and there is no event forwarding anywhere in :mod:`crude.ui.group`. The two
chevrons are the atlas's own `chevron-d` and `chevron-r`, one per state.

Two facts a script has to know to get a box it can see. The pass is
:meth:`~crude.ui.context.UIContext.layout`, which is what the frame calls: stacks,
anchors, folds and the `cell`/`order` rows are all answered there, so a number read
before a layout is a number that has not been measured yet. And a row or a grid
*inside* a body settles on the second frame, because the body's width comes from the
anchors - the last sweep - while its rows are arranged in the second. Finally, a
dockable group wants `PACK(g, "fill", "fixed")`: a cell gives a `fixed` child only
the size it already has, so a group with no width of its own is drawn no width, and
its band is a band nothing can be grabbed from.

Like `scroll`, a group has no Pd twin: `pd_object` stays empty, `PD_EMIT` never
writes one, and a plain panel has no `folded` row to be refused one.

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
