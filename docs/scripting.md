[`Back to Documentation`](https://github.com/RetroMaximus/crudelang/blob/main/README.md#documentation)

# Scripting

A crude script is the top-level of a `.crude` file. The interpreter reads
it once at load, runs every statement in order, and leaves the handlers
its `ON_*` calls registered on the loop. The frame loop is the only thing
that runs after `main.crude` returns; nothing in the script is a main
function, and there is no `import`. This page is the Tier A surface as a
short tutorial; `bindings.md` is the reference for every name it uses.

## Values

Four kinds:

| Kind       | Written as              | Read as                |
| ---------- | ----------------------- | ---------------------- |
| `Number`   | `42`, `3.14`, `-1`      | `NUM(...)`, arithmetic |
| `String`   | `"text"`, `"a" == "b"`  | `STR(...)`, `LEN(...)` |
| `List`     | `[1, 2, 3]`             | `AT(l, i)`, `APPEND`   |
| `Function` | `FUNC(x) -> x * 2`      | `ON_*`, `FUNC name(v)` |

A `null` is a `Number(0)`; a bare name that has never been `VAR`'d is an
error that names itself (`'nope' is not defined`) rather than a quiet `null`,
so a typo is reported instead of run with. Assignment is nearest-binding:
`VAR x = 1` inside a `WHILE` body updates the outer `x` if the outer binding is
the nearest one named `x`, which is the plan's Rule 24.

The comparison surface (`==`, `!=`, `<`, `>`, `<=`, `>=`) works on
numbers, strings and lists. `"a" == "a"` is a real answer, and `"a" !=
"b"` too; the legacy interpreter said "Illegal operation" and S1 fixed
that. Lists compare by length first, then element-wise.

## Statements

```
VAR name = expr               # bind or re-bind a name
IF expr THEN ... END          # with ELIF / ELSE arms
IF expr THEN expr             # one-line if-expr
WHILE expr THEN ... END
FOR i = a TO b [STEP n] THEN ... END
FUNC name(args) -> expr       # one-line body
FUNC name(args)               # block form
    ...
END
BREAK                          # inside FOR/WHILE
CONTINUE                       # inside FOR/WHILE
expr                           # a call at the top level is a statement
```

A statement's last expression is its value; a `FUNC` returns the last
expression its body evaluated. `PRINT_RET` is the only builtin that
prints *and* carries a value.

A statement in a loop body that raises **ends the loop** and reports the error,
exactly as one outside a loop does. It is not kept and re-tried: the condition
would answer the same the next time too, and a script that cannot answer a
statement inside a `WHILE` used to sit in the body collecting nothing until the
process was killed. The practical rule for an author is the one the shape
follows: a handler a `WHILE` body names has to be a `FUNC` the script has
declared *above* the loop.

## A minimal script

```
LOG("hello from a crude project")
LOG("this run is " + MODE() + " at " + STR(FPS()) + " fps")

VAR line = 0
WHILE line < 3 THEN
    VAR line = line + 1
    LOG(STR(line) + ": the script owns its own output")
END

LOG("the run has taken " + STR(TIME()) + " second(s)")
```

Every word here is in `bindings.md`. `LOG` reaches stdout and the
project's log file; a `"debug"` second argument keeps it off stdout but
still writes it if the CLI's `--log debug` was said. `MODE()` reads the
run's mode; `FPS()` reads the loop's target. `EXIT(N)` raises an
`ExitRequest` the loop catches on the way out and the process's exit code
becomes `N`.

## Handlers

A handler is a `Function` value the runtime calls later. Every `ON_*`
binding registers one; the interpreter holds the same `Program` symbol
table the script that registered it ran in, so a handler closes over the
names it saw.

```
FUNC on_bpm(v)
    PD_SEND(p, "bpm", v)
    SET(field, "value", v)
END
ON_CHANGE(bpm, on_bpm)
```

Anonymous handlers fit on one line with the `->` arrow form:

```
ON_CHANGE(run, FUNC(v) -> PD_SEND(p, "run", v))
ON_PD(p, "tick", FUNC() -> SET(led, "value", 1))
ON_FRAME(FUNC(dt) -> SET(led, "value", MAX(0, GET(led, "value") - dt * 6.0)))
```

A handler that raises is reported by the loop; the plan's budget is the
one that matters. `ON_FRAME` shares the interpreter thread with every
other handler, so a heavy computation belongs in Pd, not in the frame.
`SET(w, "mute", true)` is the script's way to say "stop calling this one".

Two kinds of registration reach that call, and the difference is a fact
about the callable rather than a fact about whether a script is attached:
a `Function` carries an `execute` and only the interpreter can run it, and
a plain Python callable - a settings dialog watching its window, a test's
`lambda` - only the bridge can. An attached `Program` is asked for the
first and stepped over for the second; handing both to `Program.invoke`
muted every host callable and logged "function is not callable" as if the
project had written it.

Every handler has an `OFF_` twin: `OFF_FRAME(fn)`, `OFF_PD(patch, receiver,
[fn])`, `OFF_SERIAL(link, [fn])`, `OFF(widget[, event[, fn]])`. The widget
form peels back a handler list - `OFF(w, "click", fn)` one, `OFF(w,
"click")` that event, `OFF(w)` the whole of it.

## Patches

An *authored* patch is built by the script; a *declared* one is opened by
the loader. Both live in the same namespace afterwards.

```
VAR p = PD_BUILD("fm", 520, 320)
VAR r_pitch = PD_OBJ(p, "r pitch", 30, 30)
VAR mtof    = PD_OBJ(p, "mtof 0.", 30, 60)
VAR car     = PD_OBJ(p, "osc~",    30, 120)
VAR carwave = PD_TABLE(p, "carwave", 260, 300, 120)
PD_LINK(p, r_pitch, mtof)
PD_LINK(p, mtof, car)
PD_LOAD(p)

PD_SEND(p, "pitch", 69)
PD_SEND_BANG(p, "trigger")
PD_ARRAY_WRITE(p, "carwave", GET(RES("sqr"), "values"))
```

Every send is *enqueued* to the Pd inbox and delivered on the next
`Runtime.step`; a script that says `PD_SEND` inside an `ON_PD` handler is
safe because the interpreter thread is the one that owns the queue.

A declared patch (`pd.patches` in the manifest) is opened during loading
and reaches the script through `PD_FILE` or by id; the loader records a
closer, and `crude run` puts the patch back on the way out.

## Widgets

A tree of widgets is built by the catalog calls in `widgets.md`:

```
VAR led = METER("beat", 0, 1, 0)
VAR bpm = SLIDER("BPM", 20, 300, 120)
VAR field = NUMBER("BPM", 20, 300, 120)
PLACE(led,   16, 40, 24, 24)
PLACE(bpm,   56, 40, 200, 24)
PLACE(field, 264, 40, 80, 24)
LINK_VALUE(bpm, field)
```

`GET(w, "prop")` and `SET(w, "prop", value)` are the *only* two calls the
script makes against a widget's state. Everything else - the paint list,
the anchor resolution, the hit region - is the runtime's business.

A script-defined kind is one `WIDGET_DEFINE` plus an `ON_PAINT` and any
number of `ON_HIT`s:

```
WIDGET_DEFINE("my-led", "meter")
PROP_DEFINE("my-led", "blink", "float", 0.0)
FUNC face(p)
    P_RECT(p, 0, 0, WIDTH(p), HEIGHT(p), 0, "base")
    P_CIRCLE(p, WIDTH(p) * 0.5, HEIGHT(p) * 0.5, HEIGHT(p) * 0.4, "accent")
END
ON_PAINT(w, face)
```

## Serial

One link at a time:

```
VAR port = SERIAL_OPEN("arduino")     # by manifest id
VAR port = SERIAL_OPEN("diy", "loop://diy", 115200, "line")   # script-declared

FUNC on_line(text)
    PD_SEND(p, "level", NUM(text))
END
ON_SERIAL(port, on_line)

SERIAL_WRITE(port, "ping\n")
```

A `line` codec hands the handler one line of text per call; a `raw` codec
hands bytes; a `packet` codec hands a length-prefixed list. The reader
thread enqueues and the loop dispatches - a handler runs on the
interpreter thread, so a `PD_SEND` inside one is safe.

## Time and pacing

`TIME()` answers seconds on the run's clock. A `FakeClock` in a test
moves time only when `frame()` is called, so a test that says
`--frames 0` sees no wall time pass. `SLEEP(seconds)` uses the same clock
and does nothing on a virtual one, which is why the plan's "no sleep" CI
rule holds.

`FPS()` is the loop's target the CLI or the manifest set. A `--fps 30`
override is *not* a change to a running loop's pacing - the loop reads
the clock's target, and the clock was built with the manifest's number -
so it is the number a script would `LOG` about, not the pacing.

## Modes are not script-visible except by name

A script cannot ask to switch modes at runtime; `MODE()` is read-only,
and the CLI decides the mode *before* the load starts. A project that
behaves differently in `terminal` than in `window` writes the same tree
either way and lets the mode decide what is drawn - that is the plan's
parity line, and the whole reason `TEXT("METRONOME")` works in a
`--mode terminal` run even though nothing is drawn.

## Testing a script

A test that wants to see what a script *did* runs the CLI, not the
interpreter:

```python
from crude.cli import main, EXIT_OK

assert main(["run", path, "--mode", "headless", "--frames", "1",
             "--no-audio", "--progress", "none"]) == EXIT_OK
```

The `--frames 1` gets a full tick's worth of dispatch through `step()` so
an `ON_FRAME` handler runs once, and the `--no-audio`/`--progress none`
pair keeps the test off every subsystem that could reach hardware. A
`FakeLibpd` on the monkeypatched `Libpd.load` gives a whole run no DLL
search. `RecordingDisplay` in `tests/runtime/conftest.py` is the fake
`Display` the loop's tests hand itself; `RecordingRenderer` gives a test
the exact list of draw ops a frame produced, which is how every widget
assertion in `tests/ui` works.

## Tier B sugar

S12 added the sugar the plan kept back for last: two block DSLs and a
postfix subscript. Each form desugars at parse time into exactly the
Tier A calls this page has named all along, so the runtime cannot tell
the tiers apart and a file can mix them freely.

### PATCH blocks

```
PATCH synth
    CANVAS 900 600
    OBJ osc "osc~ 440" AT 40 40
    OBJ amp "*~ 0.3"   AT 40 120
    NUM freq           AT 200 100
    TABLE wave 1024    AT 500 40
    TEXT "simple tone" AT 40 10
    LINK osc -> amp -> OUT
    LINK IN -> osc
END
```

is how the Patches section would say:

```
VAR synth = PD_BUILD("synth", 900, 600)
VAR osc = PD_OBJ(synth, "osc~ 440", 40, 40)
VAR amp = PD_OBJ(synth, "*~ 0.3", 40, 120)
PD_NUM(synth, "freq", 200, 100)
PD_TABLE(synth, "wave", 1024, 500, 40)
PD_TEXT(synth, "simple tone", 40, 10)
PD_LINK(synth, osc, amp)
VAR dac = PD_OBJ(synth, "dac~", 0, 0)
PD_LINK(synth, amp, dac)
VAR adc = PD_OBJ(synth, "adc~", 0, 0)
PD_LINK(synth, adc, osc)
```

- The block's name does double duty: it is the patch's id and the
  variable the boxes bind into. A bare `PATCH name END` is just the
  `PD_BUILD`.
- `CANVAS w h [font]` owes no statement of its own; its numbers are
  collected whatever line they sit on and feed the `PD_BUILD`.
- `OBJ`/`MSG` may lead with a name (`OBJ osc "osc~ 440"`) - named or
  `ID`'d lines bind the variable, nameless ones emit the bare call.
  `NUM`, `SYMB` and `TABLE` are named first because the name is the
  receiver or table id Pd sees; `TEXT` carries only its string.
- `AT x y` is optional on every box line; a box without it lands on
  the authoring grid's next slot, and the injected `IN`/`OUT` boxes sit
  at `0 0`.
- `ID ref` on a line that already has a name must match it, and on a
  `TEXT` line is the only way to get a variable at all. A mismatch is
  a parse error, not a silent alias.
- `LINK a -> b -> c, d -> e` pairs each chain up - `a -> b`, `b -> c` -
  and every pair is one `PD_LINK(synth, src, dst)` on outlet/inlet 0.
- `IN` and `OUT` are the adc~/dac~ convention: one shared box injected
  on first use, its inlet or outlet then reused by every other link end.

### WIDGET blocks

```
WIDGET bpm = SLIDER("BPM", 20, 300, 120)
    AT 56 40 SIZE 200 24
    ADD deck
    LINK_VALUE field
    ON CHANGE FUNC(v) -> PD_SEND(p, "bpm", v)
END
```

`WIDGET` only opens a block when `=` follows the name, so
`WIDGET("stepper")` is still just the generic catalog call. Every line
inside desugars in the order it is written; the block rewrites nothing:

| Line                | Becomes                              |
| ------------------- | ------------------------------------ |
| `AT x y [SIZE w h]` | `PLACE(bpm, x, y)` or `PLACE(bpm, x, y, w, h)` |
| `SET prop v`        | `SET(w, prop, v)`                    |
| `ADD parent`        | `NODE_ADD(parent, w)`                |
| `LINK_VALUE other`  | `LINK_VALUE(w, other)`               |
| `LINK_STATE prop other` | `LINK_STATE(w, prop, other)`     |
| `ON EVENT handler`  | `ON_<EVENT>(w, handler)`             |

The event words are `CHANGE ENTER LEAVE MOVE PRESS RELEASE CLICK FOCUS
BLUR KEY CHAR SCROLL DRAG RESIZE DROP CLOSE FRAME PAINT HIT`, one per
`ON_*` binding; the list is pinned against `crude.ui.handlers` by a test
so it cannot drift. `FRAME` is the exception that proves the shape: a
frame handler belongs to the context, not the widget, so `ON FRAME fn`
desugars to the one-argument `ON_FRAME(fn)` even inside a block.

### ON statements

At the top level, `ON` followed by a widget and an event word is the
wiring statement the Handlers section writes as a call:

```
ON bpm CHANGE on_bpm        # ON_CHANGE(bpm, on_bpm)
```

`ON` with a `(` after it - the ordinary call form - still falls through
to `expr()` untouched, which is what the two-token lookahead is for.

### Subscripts

`l[i]` is `AT(l, i)`, and a subscript on the left of `=` is the write:

```
VAR spec = ["min", 20, "max", 300]
SET(s, spec[0], NUM(spec[1]))     # reads  -> AT(spec, 0)
spec[1] = 24                      # writes -> SET_AT(spec, 1, 24)
```

The subscript is postfix, so it chains onto anything: `m[0][1]`,
`FTOGS[2]`, and `l[i] == v` all read like the calls they are. The same
postfix loop gives `f(1)(2)` call chaining for free.

### What sugar is not

The lexer never changed: every sugar word is a plain identifier, and
`TEXT`, `NUM`, `AT` and `SET` stay the live binding names they have
always been - the words mean the DSL only inside their own block, and
mean the call everywhere else (`NUM` is even a block item word, so
inside `PATCH` it is the atom-box line and everywhere else the number
conversion). No node type was added; both tiers
produce the same AST, pinned by the equivalence tests in
`tests/lang/test_sugar.py`, `tests/pd/test_sugar_equivalence.py` and
`tests/ui/test_sugar_ui.py`. Nothing here is required: the examples
stay Tier A, and every Tier A script is already a legal Tier B one.
