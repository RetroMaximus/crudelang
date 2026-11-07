[`Back to Documentation`](https://github.com/RetroMaximus/crudelang/blob/main/README.md#documentation)

# Bindings

A binding is one word a script can call. Every binding is declared once,
in :mod:`crude.bindings`, with a decorator:

```python
@binding("PD_SEND", ["patch", "recv", "value"], area="pd")
def pd_send(ctx): ...
```

The decorator adds the name to `REGISTRY`, an area tag groups it for
selection, and `install(table, area=...)` puts the ones a runtime supports
into the program's symbol table. No CLI command prints the registry: this
page is hand-written and kept in line with it by the tests that import
`crude.bindings.REGISTRY`. The count in `REGISTRY` is the plan's Tier A
surface.

## Argument rules

- Every argument is named in the decorator's list; an omitted trailing
  argument belongs to the *binding*, not to whatever the caller had bound
  the name to (see :class:`HostContext.provided`).
- `optional=N` says the last `N` arguments may be omitted; the binding's
  own default is what the omitted argument means.
- `aliases=["CLS"]` registers a second name for the same function.
- `area=` tags the binding for `install(table, area=...)` and for how this
  page groups it.

A binding never guesses. A `TEXT` call without a `host.ui` on it fails
with `"TEXT: no UI runtime is attached to this program"` and the interpreter
raises a `BindingError`, not a `null`. That way a script that runs in
`window` mode fails loudly in `terminal` mode rather than doing something
quiet and wrong.

## Areas

The registry tags every binding with one of these `area=` values:

| Area        | What it reaches           | Module                |
| ----------- | ------------------------- | --------------------- |
| `core`      | the interpreter itself    | `bindings/core.py`    |
| `pd`        | libpd, the session, inboxes | `bindings/pd.py`    |
| `pd.author` | patch authoring            | `bindings/pd_author.py` |
| `pd.midi`   | the MIDI send shorthands  | inside `bindings/pd.py` |
| `audio`     | the sample stream          | `bindings/audio.py`   |
| `sound`     | wav files and a take       | `bindings/sound.py`   |
| `midi`      | the MIDI ports             | `bindings/midi.py`    |
| `project`   | resources, presets, mode  | `bindings/project.py` |
| `ui`        | widgets, layout, style    | `bindings/ui.py`      |
| `serial`    | links and their callbacks | `bindings/serial.py`  |
| `painter`   | the `P_*` op emitters     | inside `bindings/ui.py` |

## Core

Numbers, strings, lists, functions, and the shape of what the interpreter
sees. These never touch a subsystem, so they work in every mode.

```
PRINT(value)           PRINT_RET(value)
INPUT()                INPUT_INT()
CLEAR()  alias: CLS
IS_NUM(value)          IS_STR(value)
IS_LIST(value)         IS_FUNC(value)      TYPE_OF(value)
APPEND(list, value)    POP(list, index)    EXTEND(listA, listB)
LEN(value)             AT(value, index)    SET_AT(value, index, element)
MIN(a, b)              MAX(a, b)           CLAMP(value, low, high)
FLOOR(value)           ROUND(value)       NUM(value)             STR(value)
SLEEP(seconds)
```

`AT` and `SET_AT` are S1's additions: the legacy `l / i` indexing still
works on a list but not on a string, and `LEN(str)` was a real bug before
S1 fixed it. Since S12 the two also own the subscript sugar: `l[i]`
parses to `AT(l, i)` and `l[i] = v` to `SET_AT(l, i, v)`, so the call
and the brackets are the same binding. `MIN`, `MAX`, `CLAMP`, `NUM`,
`STR` are the plan's Tier A sugar the snippets in the plan depend on.
`SLEEP(seconds)` on a :class:`FakeClock` in a test moves virtual time and
returns without waiting for a real millisecond.

`FLOOR` and `ROUND` are the two ways a division becomes an index. The language
has no integer type, so `len / 100` is a float and `AT` would take its chances
with whatever the binary answer came out as; `FLOOR` always goes down -
`FLOOR(-0.5)` is `-1` - and `ROUND` goes to the nearest whole, exact halves to
the even side, which is the same rounding a clip marker is written with, so a
knob and the line drawn on the screen cannot disagree about which sample a
number names. Both answer a whole number, not a float that merely looks like
one.

## Project

`MODE()` says which of `terminal`/`headless`/`window` this run landed on;
`FPS()` reads the loop's own target; `TIME()` is the clock's seconds;
`EXIT(code)` raises an `ExitRequest` the loop catches on the way out. It is let
through wherever the script asked, including out of a widget handler: a menu's
Quit row ends the run with the code it was given rather than being logged as a
handler that failed and muted for asking.
`RES(id)` looks a declared resource up in the project's resource table
(`GET(RES("sqr"), "values")` is how `fm_synth` reads one of its arrays), and
`RESOURCES([kind])` answers the ids that table holds - all of them, or only the
ones of `image`/`sound`/`array`/`preset` - so a menu can list the files the
manifest declares instead of a list typed next to it. Asking for a kind that is
not one of those four is refused with the four named. `PRESET_LOAD(name)` and
`PRESET_SAVE(name, values)` are the two halves of a preset file. `WIDTH(w)`/`HEIGHT(w)` read a widget's - or the painter's own canvas's -
box, the number a script scales to without a hard-coded constant. `RUN(fn)`
loads another `.crude` script by path and runs it in-process.

```
MODE()   FPS()   TIME()   WIDTH(w)   HEIGHT(w)   EXIT(code)
RES(id)   RESOURCES([kind])   PRESET_LOAD(name)   PRESET_SAVE(name, values)   RUN(fn)
```

## Pd

The patch half. Everything here reaches
:class:`crude.pd.runtime.PdRuntime`, which is a thin wrapper around the
session and the inbox. A script never hears `libpd_*` — the runtime owns
the receive hooks, the print-hook line assembler, and the control-plane
pause.

Receivers, sends, arrays, and lifecycle:

```
PD_FILE(id, path)                           PD_LOAD(patch_or_file)          PD_CLOSE(patch)
PD_EXISTS(name)                             PD_DOLLARZERO(patch)
PD_SEND(patch, receiver, value)             PD_SEND_BANG(patch, receiver)
PD_SEND_LIST(patch, receiver, list)         PD_SEND_MSG(...)
PD_ARRAY_READ(patch, name, start, count)
PD_ARRAY_WRITE(patch, name, values)         PD_ARRAY_SIZE(patch, name)
PD_ARRAY_RESIZE(patch, name, size)          PD_SEED(patch, name, values)
PD_DSP(on_off)
PD_SAVE(patch, path)                        PD_DUMP(patch)                  PD_INSPECT(patch)
PD_UNLINK(...)                              PD_BOXES(patch)                 PD_KINDS()
```

Authoring (what `fm_synth` and `metronome` do):

```
PD_BUILD(id, width, height)                 PD_OBJ(patch, class_and_args, x, y)
PD_MSG(patch, text, x, y)                   PD_NUM(patch, name, x, y)
PD_TEXT(patch, text, x, y)                  PD_TABLE(patch, name, size, x, y)
PD_SYMB(patch, name, x, y)                  PD_LINK(patch, from, to, [outlet], [inlet])
PD_EMIT(patch, widget, [x, y, w, h])
PD_GUI(patch, kind, x, y, [w, h, props])   PD_GUI_KINDS()
```

The S12 `PATCH name ... END` block is sugar over exactly these calls -
`CANVAS` feeds `PD_BUILD`, `OBJ/MSG/NUM/SYMB/TABLE/TEXT` each emit
their own, `LINK a -> b` is `PD_LINK` - with `scripting.md` as the
grammar's page.

MIDI (the nine send shorthands `libpd` gives):

```
PD_MIDI_NOTEON / NOTEOFF / PGM / CTL / BEND / TOUCH / POLYTOUCH
PD_MIDI_REALTIME / PD_MIDI_SYSEX
```

Handlers:

```
ON_PD(patch, receiver, fn)        OFF_PD(patch, receiver, [fn])
```

Every `PD_SEND` is enqueued to `PdInbox` and delivered on the next
`Runtime.step`, not on the caller's thread; that is what lets a script
call it from inside a `sounddevice` callback without tripping the control
plane. `ON_PD` handlers are dispatched in the same tick, and they get the
interpreter thread back, so calling `PD_SEND` from inside one is safe.

A table is authored at a size (`PD_TABLE(patch, name, size, x, y)`) and
`PD_ARRAY_RESIZE` changes it afterwards: the array is grown or shrunk in place,
whatever it already held up to the new length stays, and the answer is the size
the array has now, read back off Pd rather than the number that was asked for.
A name with no array behind it is refused by name - a script that thought it had
resized something would otherwise be carrying a table that was never there - and
so is a size below one sample. That verb is how a recorder fits a patch's sample
table to a file whose length nobody knew when the patch was written.

## Audio

The stream, not the samples. A script does not push blocks: `tick_audio`
does that on the loop's frame.

```
AUDIO_INFO()                       AUDIO_DEVICES([direction])
AUDIO_USE_DEVICES([input], [output])   AUDIO_DEVICE()
AUDIO_START([backend])             AUDIO_STOP()
AUDIO_LEVEL([channel, mode, source])   AUDIO_WAVEFORM([frames, channel, source])
```

`AUDIO_LEVEL`'s `mode` is `"rms"` (default), `"peak"` or `"db"`; `AUDIO_WAVEFORM`
returns a list of numbers copied out of the last `frames` blocks. Both
answers come from taps the runtime holds, so a run on the
:class:`NullBackend` - pulled, no device thread, nothing audible - still has
a waveform to read.

Both meters also take a `source`: `"output"` (the default, what went to the
speaker) or `"input"`, what came off the microphone. They are separate taps on
separate buffers, so a recorder can show what is arriving while the patch is
making something else entirely.

`AUDIO_DEVICES([direction])` lists the machine's inputs (`"input"`), outputs
(`"output"`) or both, one `"index: name"` line each. `AUDIO_USE_DEVICES(input,
output)` takes two of those indices and reopens the live stream on them - stop,
close, open a fresh backend, re-attach the running session, and start again if
it was running - so the File menu's Audio dialog can change devices without a
restart. Omitted arguments keep the current device. `AUDIO_DEVICE()` reads back
the `"input"`/`"output"` indices now in use.

## Sound files and recording

Audio's file side: a wav on disk, and a take off the input. These live apart from
`AUDIO_*` because a path means the *project's* directory, not the working
directory the run happened to start in - the same rule `PRESET_SAVE` keeps.

```
WAV_READ(path)                     WAV_LENGTH(path)
WAV_WRITE(path, samples, rate[, channels, bits])
AUDIO_RECORD()                     AUDIO_RECORD_STOP()
AUDIO_RECORD_START(seconds, [channel])
```

A relative `path` is joined onto the project directory, and both separators work
in the string a script writes, so `"samples/take.wav"` and
`"samples\\take.wav"` are the same file. A backslash is the language's escape
character, so a whole path written into a line of source needs forward slashes -
which is also what the bindings answer with: `WAV_WRITE` returns the file's path
spelled the way the project spells it, relative when it is inside the project.
The folder a file is written into is made on the way.

`WAV_READ` answers a `wav` handle and checks the header right there, so picking a
file that is not sound fails at the call. Its samples are decoded only when a
`GET` asks for them, and then kept, so reading `frames` over a folder of files
costs a header each and not the audio. The readable props are `path`, `name`,
`rate`, `channels`, `bits`, `frames`, `seconds`, `samples` (channel 0, as
`-1..+1` floats) and `mixed` (every channel averaged into one). The handle is
read-only.

Only uncompressed PCM at 8 or 16 bits is read: a 24-bit file, a 32-bit float one
or a compressed one is refused with what was found in its header named in the
message, because decoding them wrong produces noise that still plays.

`AUDIO_RECORD_START(seconds, [channel])` arms the input capture - one channel,
`0` by default, a buffer sized from `seconds` and the stream's own rate - and
answers a `recorder` handle. `AUDIO_RECORD()` hands back that handle at any
time, and `GET` reads `armed`, `channel`, `frames`, `capacity`, `overflow`,
`rate`, `seconds` and `samples` off it; `AUDIO_RECORD_STOP()` takes the take off
the callback and answers the frame count, leaving the frames readable. A buffer
that fills up is not an error: the extra frames are counted in `overflow` and
dropped, which is the honest version of "the last part never arrived". The
handle has no writable properties, because writing `armed` from a script thread
would be a change to what the audio callback is filling.

Recording is the one audio action that refuses rather than answering zeros: with
no stream open, `AUDIO_RECORD_START` fails with `"audio is not open; call
AUDIO_START first"`. A *reading* - `GET(AUDIO_RECORD(), "frames")` in a widget
that runs every frame - answers `0` on a silent run, the way `AUDIO_LEVEL` does,
so a recorder window still works where there is no microphone.

## MIDI

Real ports, not the `libpd` send shorthands (those are `PD_MIDI_*`, under Pd
above). Everything here reaches :class:`crude.midi.runtime.MidiRuntime`, whose
backend is `python-rtmidi` when it imports and a null one otherwise, so a
machine with no MIDI library simply lists no devices rather than failing.

```
MIDI_DEVICES([direction])          MIDI_OPEN(direction, index)
MIDI_SEND(port, data)              MIDI_CLOSE(port)
ON_MIDI(port, fn)                  OFF_MIDI(port, [fn])
```

`MIDI_DEVICES([direction])` answers `"index: name"` lines for `"input"`,
`"output"`, or both when no direction is given. `MIDI_OPEN(direction, index)`
opens one of those ports and returns a handle a port index and direction are
fixed to (the handle is readable, not writable). `MIDI_SEND(port, data)` writes
one packet - a list of MIDI bytes - to an open output. `ON_MIDI(port, fn)`
calls `fn` with each inbound message, handed over on the frame's dispatch from
the backend's reader thread; it shares the serial runtime's three-strike mute
budget, so a handler that keeps raising mutes itself rather than flooding the
loop. `OFF_MIDI(port, [fn])` drops the handlers, all of them or just `fn`.

## UI / widgets

Two halves: the tree (`WIDGET`, `TEXT`, `SLIDER`, ...) and its behaviour
(`PLACE`, `LINK_VALUE`, `ON_CHANGE`, ...). See `widgets.md` for the prop
table; this is the name list.

Catalog constructors — every one takes a label or an id first, then the
kind's own args:

```
WIDGET(kind, ...)  (the generic form every catalog function is a helper for)
TEXT(label)   BUTTON(label)   TOGGLE(label)   ICON(name)
SLIDER(label, lo, hi, value)   VSLIDER(...)   NUMBER(label, lo, hi, value)
METER(label, lo, hi, value)    RADIO(values)   DROPDOWN(values)
PANEL(title)   XY_PAD(label, x, y)   KEYBED(label, low, octaves)   ADSR(...)   CURVE(...)
SPECTRUM       WAVEFORM         PLOT(label)         LOGVIEW(label)
CLIP(label)   PRESET_BAR(...)   PANEL(...)   SCROLL(label)   GROUP(title[, parent])
```

Placement and layout:

```
PLACE(widget, x, y, width, height)   NODE_ADD(parent, child)
ANCHOR(widget, edge[, target][, target_edge][, offset])
UNANCHOR(widget[, edge])             PACK(widget, policy_x[, policy_y])
PAD(widget[, value])
HSTACK(panel[, gap][, options])      VSTACK(panel[, gap][, options])
GRID(panel[, columns][, gap][, options])      UNSTACK(panel)
STYLE(widget, role, value)           LINK_VALUE(a, b)
LINK_STATE(source, prop, target[, target_prop][, two_way])
UNLINK(a)
```

These are `crude.ui.layout` with one call per verb and no geometry of their own,
so a script and a Python caller go through the same door. `ANCHOR` glues one edge
of a widget to one edge of another - or of the panel the widget lives in, which is
what leaving `target` out means, and `"parent"` says so in words. `left`, `right`,
`top`, `bottom`, `center-x` and `center-y` are the edges, and the short forms
(``"l"``, ``"cx"``) work here as they do anywhere else in the engine. Gluing both
ends of one axis is a stretch rather than a fight, which is how a bar pinned left
*and* right stays the window's width through a resize; gluing a middle to an edge
is a contradiction and says so. `PACK` answers what a widget does when its
container moves - `fixed`, `grow` for the same fraction of the container, `fill`
for whatever nothing else used - and `PAD` how far a container's children start
inside it, in pixels or as a skin token (`PAD(w)` gives the token's own answer
back). `HSTACK`/`VSTACK`/`GRID` arrange a panel's children in a row, a column or
cells of one size, and `UNSTACK` stops arranging them; an anchor is applied after
pack and after any stack, so the axis a script glued stops being theirs to decide.

`options` is a flat list of name/value pairs, and the words depend on the verb:
`gap`, `pad` and `align` for the two stacks, and `dockable` as a fourth for
`GRID`, which is how a panel is marked out as somewhere a group can be dropped
(`widgets.md`, "Grids and cells"). A name outside that list is refused with the
list printed in the message, because an option that was quietly ignored is a panel
that came out different from the one the script asked for. Whatever the last word
written was wins, so a `"gap"` in the list beats the positional argument that
answered the same question first.

`GET(w, "place")` reads a widget's glues back as the sentences `ANCHOR` answered
with and `GET(w, "pack")` its two policies, so a script can check what it just
told the layout to do. Neither word is a `SET` target: `place` and `pack` are
rules the pass keeps rather than rows of a table, and the refusal names the verb
that writes them.

Custom kinds and props:

```
WIDGET_DEFINE(kind, base, pd_object, hint)   PROP_DEFINE(kind, name, type, default, ...)
PAINT_REQUEST(widget)            WIDGET_KINDS()   WIDGET_PROPS(kind)
WIDGET_HAS(widget, prop)
```

Handlers:

```
ON_FRAME(fn)              OFF_FRAME([fn])
ON_CHANGE(w, fn)          ON_CLICK(w, fn)          ON_HIT(w, fn)
ON_PAINT(w, fn)           ON_KEY(w, fn)            ON_CHAR(w, fn)
ON_FOCUS(w, fn)           ON_BLUR(w, fn)           ON_PRESS(w, fn)
ON_RELEASE(w, fn)         ON_DRAG(w, fn)           ON_SCROLL(w, fn)
ON_ENTER(w, fn)           ON_LEAVE(w, fn)          ON_MOVE(w, fn)
ON_RESIZE(w, fn)          ON_DROP(w, fn)           ON_CLOSE(w, fn)
OFF(widget[, event[, fn]])  OFF_PD(...)            OFF_SERIAL(...)
```

Every one but `ON_FRAME` takes the widget first: only a frame handler
belongs to the context rather than a widget. `ON_HIT`/`ON_PAINT` are
*asked* (the handler's return is the answer - a hit region, a painted
face) and the other sixteen are *told*, but all nineteen register
through the same shape the `ON_EVENTS` list holds.

Tier B sugar (S12) reaches these same names, never new ones: a
`WIDGET w = expr ... END` block's `ON EVENT handler` line, and the
top-level `ON w EVENT handler` statement, both desugar to the `ON_*`
call above (`FRAME` keeping its one-argument shape). `scripting.md`
owns the full block grammar.

State a handler was never handed:

```
MOD_HELD(word)                     SENDER()
```

`MOD_HELD("shift")` answers `1` while that key is down and `0` while it is not,
read off the router's live modifier word; the words are `shift`, `control`,
`alt` and `super`, and any other is refused by name rather than answered `0`,
because `0` is a real answer and a typo must not look like one. A press hands
its modifiers to the behaviour that moves a value, but a script deciding a
thing a frame later - a row click that *adds* a track to the ones already
selected, a face that paints differently while control is down - has no
argument holding that word, and a project that wrote `0x0001` for it would
break the day the mask moves. With no window attached the answer is `0`: a
headless run has nothing to ask.

`SENDER()` answers the widget the handler now running was registered on, or
`null` when no handler is running. A handler is handed a value, a prop name, a
pointer - never the widget itself, because when one `ON_CHANGE` is written once
and put on a hundred widgets the hundred calls are literally the same function.
That is fine for a page of one-off controls and impossible for a builder that
makes a row per track: the row has to know which row it is. So a script walks
the arrays it built and compares each handle with `SENDER()` - handles are
compared by what they point at, so `AT(clips, i) == SENDER()` is an identity
test, not a name match. During a nested call (a painter fired from inside a
frame handler) the answer is the *inner* registration, the one whose arguments
are on the way in; a widget's own `ON_FRAME` is registered on no widget and
answers `null`.

Painter ops (the `P_*` names a custom kind's `ON_PAINT` uses):

```
P_RECT(p, x, y, width, height, [radius], [role])
P_CIRCLE(p, cx, cy, radius, [role])
P_ARC(p, cx, cy, radius, start, end, [width], [role])
P_LINE(p, x1, y1, x2, y2, width, [role])
P_POLY(p, points, closed, [role])
P_TEXT(p, x, y, text, [size], [role], [align])
P_GLYPH(p, x, y, width, height, name, [role])
P_IMAGE(p, x, y, width, height, resource, [role])
P_CLIP(p, x, y, width, height)   P_CLIP_END(p)
```

Every `P_*` call appends to the widget's `PaintList`, which is drawn
during the frame's `draw` step; the cache is invalidated by a `SET` on the
widget or by `PAINT_REQUEST(widget)`.

## Menus

A running window's only menu is one built from the catalog it already has -
`PANEL`s and `BUTTON`s and the `visible` prop - not a native bar (see
`widgets.md`). These names are in the `ui` area; the bar and its popups are
ordinary widgets, so a menu is a composition the tests can drive with no GPU.

```
MENUBAR([name])                    MENU(bar, title)
MENU_ADD(menu, label, fn)          MENU_CHECK(menu, label, [on], fn)
MENU_SHOW(menu)                    MENU_HIDE(menu)
MENU_WORKSPACES(menu)              WORKSPACE_SHOW(name_or_index)
MENU_AUDIO_DIALOG(menu[, title])   MENU_MIDI_DIALOG(menu[, title])
MENU_SERIAL_DIALOG(menu, link[, title])
```

`MENUBAR([name])` puts a strip along the top of the window and returns the bar;
`MENU(bar, title)` adds one title and its hidden popup. `MENU_ADD` appends a
plain row that hides the popup and runs `fn`; `MENU_CHECK` appends a tickable
row (a `TOGGLE`) whose `fn` is told the state the click just set, with `on`
the initial tick. Opening one popup closes whichever was open, and a closed
popup paints nothing, so the bar costs only its titles until it is clicked.

Three of the rows are ready-made configuration dialogs, so picking the item
opens a page of controls with one `Apply` button rather than running a
bare callback. `MENU_AUDIO_DIALOG(menu[, title])` lists the audio runtime's
input and output ports as two scrollable columns of `TOGGLE`s - a device is
picked by its row and switched off by the same row - and, on Apply, switches
the running stream onto the picked device indices - exactly what
`AUDIO_USE_DEVICES` does. `MENU_MIDI_DIALOG(menu[, title])` lists the MIDI
ports per direction and opens the pick with `MIDI_OPEN`. `MENU_SERIAL_DIALOG(menu,
link[, title])` shows one `NUMBER` seeded at a link's current rate and applies
`SET(link, "baud", n)`.
The ports are read only when a panel opens, never when the menu is built, so a
project with no sound card, no MIDI library and no serial cable still builds its
menu and runs headless; with no runtime the list shows one disabled
`(no devices)` row and applying does nothing.

Each page is sized from the window it is opened in, keeps its `Apply` row at the
bottom and scrolls the rest (`widgets.md`, "Menus and dialogs"), so a small
window shows a whole dialog rather than a cut-off one.

`MENU_WORKSPACES(menu)` makes a menu out of the scenes the window has: one row
per workspace, the current one ticked, each row switching to its scene (see
below). The rows are re-read every time the popup opens, so a scene declared
after the menu was built is on the list without the script rebuilding it by
hand.

## Workspaces

A workspace is a named scene inside the one window - a subtree of the same
widget tree, not a second window and not a second interpreter. The entry
script's scene is always `"root"` at index 0, and its panel is the window's
root, so a script that never mentions a workspace is exactly what it always
was. The menu bar belongs to the window rather than to a scene, and every
scene fills the window *below* the bar, which is what a switch clears.

```
WORKSPACE()                        WORKSPACE(name[, on_enter])
WORKSPACE_SHOW(name_or_index)      WORKSPACE_NAMES()
WORKSPACE_INDEX()                  WORKSPACE_ADD(workspace, widget)
WORKSPACE_KEEP(workspace, keep)
```

`WORKSPACE()` answers with the current scene's name; `WORKSPACE("mixer")`
makes that scene and shows it, and `WORKSPACE("mixer", build_mixer)` makes one
that runs `build_mixer` every time it is shown instead of being carried. The
handle it returns names the scene in the other four verbs, and so does its name
or its number, whichever a script happens to be holding.

`WORKSPACE_SHOW("mixer")` and `WORKSPACE_SHOW(2)` are the same gesture: the
scenes that are not the target leave the tree, the target's arrive in the order
they left, and an unknown name or number is refused with the list the window
does have. `WORKSPACE_NAMES()` is that list (`root` first, always) and
`WORKSPACE_INDEX()` the number of the one on screen, so a `View` menu and a
loop over the array are the same two calls. The number `WORKSPACE_INDEX()`
answers carries a float, as every number here does, and `WORKSPACE_SHOW` reads
that back as the scene's number rather than as a scene called `"2.0"`; a part of
a number such as `1.5` is refused as a number, not guessed at as a name.

`WORKSPACE_ADD(mixer, w)` parents a widget into a scene whether or not that
scene is on screen, which is how a project builds four pages and shows one.
`NODE_ADD(parent, child)` still works for the current scene, and a widget added
by either route is stamped with the scene it was added to: that stamp is all
ownership is, and a widget whose stamp is the window's own (the menu bar, a
popup) cannot be moved into a scene at all.

`WORKSPACE_KEEP(mixer, 1)` is the carry: leaving the scene takes its subtree out
of the tree with every prop and every `ON_CLICK` still on it, so a sampler's
settings survive a jump to the mixer and come back unchanged - and a handler
registered in a hidden scene cannot fire, because a widget with no parent is a
widget no hit test can find. `WORKSPACE_KEEP(mixer, 0)` is the rebuild: leaving
removes the subtree and releases its handlers, and the scene's `on_enter` is
what puts it back. Declaring an `on_enter` says "rebuild me" by itself.

The `ui.workspaces` manifest key (`manifest.md`) reserves the names and the
order before the script runs; `root` is index 0 whatever the list says, and a
script that declares a reserved scene fills it in rather than colliding with it.
The worked example is `examples/all_gui`: five reserved scenes and the deck
built before any of them is stood in, a `Go` menu that re-reads the list on
every open, and a coverage test that walks the script's own `PAGES` because a
hidden scene is not under the root for a walk to find.

## Groups

A group is a section of a page - a band with a word on it and a body the controls
live in - that folds, re-orders among its siblings and can be dropped into a cell of
a `dockable` grid. These eight names are in the `ui` area; the box itself, the two
panels it is built from and the props it answers with are `widgets.md`'s "Groups".

```
GROUP(title[, parent])                 GROUP_FOLD(group[, folded])
GROUP_MOVE(group, position)            GROUP_UP(group)
GROUP_DOWN(group)                      GROUP_DOCK(group, cell[, panel])
GROUP_UNDOCK(group)                    GROUPS(panel)
```

`GROUP("carrier", deck)` is `WIDGET("group")` plus the two panels and a `SET` of the
title: the word is the header's and the widget's name, which is why `deck/carrier` is
the path a report prints. `parent` is the second slot rather than a prop - it is the
widget the section is built *inside*, and leaving it out puts the group in the scene
on screen like every other constructor does. Children go to the body: a
`NODE_ADD(carrier, knob)` never has to know the band is there.

`GROUP_FOLD(carrier, 1)` closes the group to its band, `0` opens it and leaving the
second word out switches it; all three are the chevron, and the answer is the state
the group is in now rather than a success flag, so a menu row can decide whether its
own caption should read Close or Open. Nothing is resized by the verb: how tall a
fold leaves the box is measured by the layout pass from the rows the body holds, the
same number a `SET(g, "folded", 1)` and a hand on the mark leave behind.

`GROUP_MOVE(filter, 0)` re-orders the group among the sections it is drawn with, and
`GROUP_UP` / `GROUP_DOWN` are that call at one step. The list they change is the list
`VSTACK`, `HSTACK` and `GRID` arrange from, so the next pass draws the boxes in the
new order and no script has to be told a row moved. A position past either end is
clamped, and the answer is the place the group took - the same number
`GET(group, "order")` reads back.

`GROUP_DOCK(carrier, 2)` puts the group in the cell `GRID` counted, in the panel the
group already lives in unless a third argument names another; the answer is the cell
it holds, which is what `GET(group, "cell")` says. Four refusals, four facts: the
group does not say `dockable`, the panel has no cells, the number is not one of them,
or somebody is already drawn in it - each named in the message, because "why did my
group not move" is not one question and one error that could mean any of them is a
bug report rather than a fix. A pointer is told the same
thing the other way: a drag released over a taken cell moves nothing at all.
`GROUP_UNDOCK(carrier)` gives up the claim and nothing else - the group keeps its
parent and takes the next free cell its order comes to - and `GROUPS(box)` is the
array of groups a panel holds, in draw order, which is what a `View` menu loops over
to fold or move every section of a page it did not build.

## Common handle calls

The two every widget answer through, from `bindings/core.py`:

```
SET(handle, prop, value)     GET(handle, prop)
```

`handle` is any kind the interpreter knows: a widget, a patch, a serial
link, an array, a preset, a resource. The prop is a string; the value is
whatever the prop's type table accepts.

## Serial

One link at a time; the runtime is `crude.serial.SerialRuntime`.

```
SERIAL_OPEN(id)                                     by manifest id
SERIAL_OPEN(id, port[, baud][, codec])               script-declared link
SERIAL_WRITE(link, data)                             a codec-framed send
SERIAL_CLOSE(link)
ON_SERIAL(link, fn[, kind])                          every frame that arrives
OFF_SERIAL(link, [fn])
```

The link handle answers to `GET`/`SET` for `"open"`, `"port"`, `"baud"`,
`"codec"` and `"id"`. `SET(link, "baud", n)` is writable: a link is
single-baud, so it closes the device and reopens it at the new rate - which is
how the File menu's Serial dialog sets an RX or TX link's speed, one link per
rate. A `loop://` port is always registered, so
`SERIAL_OPEN("x", "loop://x")` never needs a real device, which is how the
S10 tests run and how the CI matrix runs `sensor_bridge`.

## Registering a new binding

Add one `@binding(...)` in the module that owns the area, name the new
function in that module's `BINDINGS` tuple, and add the module to
`crude/bindings/__init__.py` if it is a new area. Nothing else: the
registry is a dict and `install` walks it, so a new name is available to
every runtime whose area selects it, with no further wiring. `REGISTRY` is
the single source of truth; this page lists the names by hand, so a new
binding belongs here too.
