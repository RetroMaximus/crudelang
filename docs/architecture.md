# Architecture

`crude` is a small runtime with a wide load surface: a project directory, a
manifest, an entry script, and a pipeline that fills a host in fourteen phases
before a single frame is pumped. This page is the map the code follows; every
other doc file in this directory drills into one of the layers named here.

## The five layers

1. **Language** - :mod:`crude.lang` is the interpreter. Numbers, strings,
   lists, functions, the ``VAR``/``WHILE``/``FUNC`` grammar, and the
   ``Program`` symbol table every ``ON_*`` handler closes over. Nothing above
   it can move a value that this layer does not have a class for, and nothing
   below it knows a project exists.
2. **Bindings** - :mod:`crude.bindings` registers the words a script speaks
   against the host: ``LOG``, ``MODE``, ``RES``, ``PRESET_LOAD``, ``PD_BUILD``,
   ``PD_SEND``, ``WIDGET``, ``TEXT``, ``SERIAL_OPEN``. Each binding declares
   its argument names and its area, and reaches the host through
   :class:`crude.bindings.registry.HostContext`. `bindings.md` is kept by
   hand against that same registry.
3. **Subsystems** - :mod:`crude.pd`, :mod:`crude.audio`, :mod:`crude.serial`,
   :mod:`crude.ui`. Each owns one kind of state the frame loop drains or
   paints: patches and receivers, the sample stream, the byte links, the
   widget tree. Each has a real class and a fake one behind the same
   protocol, so the tests never touch hardware.
4. **Project** - :mod:`crude.project` is the loader. A ``Manifest`` is read,
   a ``Project`` is built around it, and the pipeline runs its fourteen phases
   in order, filling ``audio``, ``pd``, ``serial``, ``program``, ``ui``,
   ``runtime``, ``display`` in the order a run needs them. A failure at any
   phase becomes a report line, an exit code, and a project that still knows
   how to shut itself down.
5. **Runtime** - :mod:`crude.runtime` is the loop, the three modes, and the
   shutdown order. ``Runtime.step`` is the plan's tick; ``Runtime.run`` is
   the plan's bounded or unbounded frame; ``crude.runtime.modes`` says which
   of ``terminal``, ``headless``, ``window`` a machine can actually do.

The layers meet at the ``Project``: it is the object that has an ``audio`` and
a ``pd`` and a ``serial`` and a ``program`` and a ``ui`` and a ``runtime`` on
it, and every one of those is reachable by a script through one host namespace
:func:`Project.host_object` builds.

## What a run looks like

```
open_project(path)                     read the manifest, do nothing else
   |
   v
Project.load(reporter)                 fourteen phases:
   1  project.check                      the manifest validates
   2  resources.read                     the file table is built
   3  arrays.parse                       array.txt -> List[float]
   4  environment                        log file, build cache, directories
   5  audio.config                       backend + sample rate + block size
   6  pd.core                            libpd is loaded (real or fake)
   7  pd.presets                         preset/*.json is read
   8  pd.build                           authored patches are written to .pd
   9  pd.load                            declared and authored patches open
   10 media                              images and sounds the manifest named
   11 audio.device                       the stream is opened, not started
   12 serial                             the declared links are opened
   13 script.compile                     entry.crude is parsed
   14 runtime.activate                   the loop takes over the host
   |
   v
Project.run_script()                   every statement at the top level runs,
                                       ``ON_*`` handlers land in the registry,
                                       and the interpreter is left holding a
                                       program whose host is the project
   |
   v
Runtime.run(max_frames=N)              the loop, until the window closes, the
                                       bound is reached, the script calls
                                       ``EXIT``, or Ctrl-C is pressed
   |
   v
Runtime.stop() -> project.shutdown()   the plan's shutdown order
```

A ``crude check`` runs only the phases that need no device or window (1-3
and 13); everything above stays honest about the fact that nothing was
opened.

## The tick, in the plan's order

:func:`Runtime.step` is one frame:

```
pump input          → the display says whether the person closed the window
drain PdInbox       → every [send] the patches did since the last frame
drain serial inbox  → every line every link read since the last frame
ON_FRAME handlers   → the ``frame(dt=)`` call that ``ui.frame`` runs
dispatch UI events  → the same ``frame`` half walks hit-testing and focus
draw                → the renderer is fed ops by the widget tree's painter
swap                → the window's buffer is turned over if one is open
pace                → :meth:`Clock.sleep` holds the frame to ``target_fps``
```

Nothing here is a thread of its own: the audio stream pulls blocks in
:meth:`Runtime.tick_audio` when the run has no device thread (``--no-audio``,
or a machine where no real backend opened), and a live sounddevice
callback renders them outside the loop - a ``headless`` run with a working
device is in that second group, heard, not simulated. Pd's audio thread and the interpreter
thread are separated by :class:`PdInbox`, and the serial reader thread is
separated from both by :class:`SerialInbox`, which is why a ``ON_PD`` handler
can safely call ``PD_SEND`` without a lock.

## The shutdown order

The plan names four steps, and they happen in this order:

1. Stop the stream (:func:`Runtime.stop_audio`) so no more blocks arrive.
2. Close the patches (:func:`Project.shutdown` unwinds every closer each
   phase recorded, including ``libpd_releaseloop`` at the very end).
3. Unbind the serial readers (:meth:`SerialRuntime.close_all`) so the reader
   threads stop before the interpreter does.
4. Terminate GLFW (:meth:`Runtime.close_display`) - last, because a swap
   into a closed context is the crash the whole order exists to avoid.

Every phase that opened something recorded a closer on the project. The loop
owns only the window and the tick; the project's own undo stack does the
rest. A failure in any closer is reported on stderr and the next one still
runs, because a half-torn-down project is not a reason to leak the other
half.

## Exit codes

The plan's five numbers, all in :mod:`crude.errors`:

| Code | Name             | Meaning                                              |
| ---- | ---------------- | ---------------------------------------------------- |
| 0    | ``EXIT_OK``      | the loop ended, the window closed, or ``EXIT(0)``    |
| 2    | ``EXIT_MANIFEST``| the manifest does not validate; pointer is on stderr |
| 3    | ``EXIT_LOAD``    | a phase failed after the manifest was fine           |
| 4    | ``EXIT_SCRIPT``  | the interpreter raised while running or in a handler |
| 5    | ``EXIT_RUNTIME`` | the loop could not do what the mode asked            |

An :class:`ExitRequest` a script's ``EXIT(N)`` raises carries ``N`` back
through ``Runtime.run`` and lands on ``Project.exit_code``. A ``KeyboardInterrupt``
is the plan's cancel - ``EXIT_OK`` with a "stopped by the user" note, not a
crash.

## Where the pieces are

| Path                 | What it is                                              |
| -------------------- | ---------------------------------------- |
| ``crude/lang``       | the interpreter: lexer, parser, AST, values, Program   |
| ``crude/bindings``   | one module per binding area, one ``@binding`` per name  |
| ``crude/pd``         | ``RealLibpd``/``FakeLibpd``, ``PdSession``, ``PdInbox`` |
| ``crude/audio``      | ``AudioRuntime``, backends, the Null pull path          |
| ``crude/serial``     | ``SerialRuntime``, links, codecs, the loop:// port      |
| ``crude/ui``         | widgets, layout, glyph atlas, ``UIContext``, renderers  |
| ``crude/project``    | manifest, loader, phases, the reporter protocol         |
| ``crude/runtime``    | ``Clock``, ``Runtime``, the mode table                  |
| ``crude/cli``        | argparse, the command registry, the exit codes          |
| ``crude/commands``   | one file per subcommand: ``run``, ``check``, ``init``   |
| ``examples``         | the five worked examples, four of them the plan's       |
| ``tests/<area>``     | fakes and asserts, one directory per layer above        |

Each of those areas has its own doc under ``docs/`` where the plan says so.
`bindings.md`, `widgets.md` and `glyphs.md` are written by hand and kept in
line with the registry and the prop table; `manifest.md` is written from the
dataclasses in :mod:`crude.project.manifest`; `scripting.md` is the plan's
Tier A surface as a small tutorial.
