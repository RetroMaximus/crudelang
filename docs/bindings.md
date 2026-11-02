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
NUM(value)             STR(value)
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

## Project

`MODE()` says which of `terminal`/`headless`/`window` this run landed on;
`FPS()` reads the loop's own target; `TIME()` is the clock's seconds;
`EXIT(code)` raises an `ExitRequest` the loop catches on the way out.
`RES(id)` looks a declared resource up in the project's resource table
(`GET(RES("modindex"), "values")` is how `fm_synth` reads its array), and
`PRESET_LOAD(name)`/`PRESET_SAVE(name, values)` are the two halves of a
preset file. `WIDTH(w)`/`HEIGHT(w)` read a widget's - or the painter's own canvas's -
box, the number a script scales to without a hard-coded constant. `RUN(fn)`
loads another `.crude` script by path and runs it in-process.

```
MODE()   FPS()   TIME()   WIDTH(w)   HEIGHT(w)   EXIT(code)
RES(id)   PRESET_LOAD(name)   PRESET_SAVE(name, values)   RUN(fn)
```

## Pd

The patch half. Everything here reaches
:class:`crude.pd.runtime.PdRuntime`, which is a thin wrapper around the
session and the inbox. A script never hears `libpd_*` — the runtime owns
the receive hooks, the print-hook line assembler, and the control-plane
pause.

Receivers, sends, arrays, and lifecycle:

```
PD_FILE(id, path)                 PD_LOAD(patch_or_file)     PD_CLOSE(patch)
PD_EXISTS(name)                   PD_DOLLARZERO(patch)
PD_SEND(patch, receiver, value)   PD_SEND_BANG(patch, receiver)
PD_SEND_LIST(patch, receiver, list)   PD_SEND_MSG(...)
PD_ARRAY_READ(patch, name, start, count)
PD_ARRAY_WRITE(patch, name, values)   PD_ARRAY_SIZE(patch, name)
PD_DSP(on_off)                    PD_SEED(patch, name, values)
PD_SAVE(patch, path)              PD_DUMP(patch)             PD_INSPECT(patch)
PD_UNLINK(...)                    PD_BOXES(patch)            PD_KINDS()
```

Authoring (what `fm_synth` and `metronome` do):

```
PD_BUILD(id, width, height)       PD_OBJ(patch, class_and_args, x, y)
PD_MSG(patch, text, x, y)         PD_NUM(patch, name, x, y)
PD_TEXT(patch, text, x, y)        PD_TABLE(patch, name, size, x, y)
PD_SYMB(patch, name, x, y)        PD_LINK(patch, from, to, [outlet], [inlet])
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

## Audio

The stream, not the samples. A script does not push blocks: `tick_audio`
does that on the loop's frame.

```
AUDIO_INFO()                       AUDIO_DEVICES([direction])
AUDIO_START([backend])             AUDIO_STOP()
AUDIO_LEVEL([channel, mode])       AUDIO_WAVEFORM([frames, channel])
```

`AUDIO_LEVEL`'s `mode` is `"rms"` (default) or `"peak"`; `AUDIO_WAVEFORM`
returns a list of numbers copied out of the last `frames` blocks. Both
answers come from taps the runtime holds, so a headless run over the
:class:`NullBackend` still has a waveform to read.

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
SPECTRUM       WAVEFORM         PRESET_BAR(...)   PANEL(...)
```

Placement and layout:

```
PLACE(widget, x, y, width, height)   NODE_ADD(parent, child)
STYLE(widget, role, value)           LINK_VALUE(a, b)
LINK_STATE(source, prop, target[, target_prop][, two_way])
UNLINK(a)
```

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
`"codec"` and `"id"`. A `loop://` port is always registered, so
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
