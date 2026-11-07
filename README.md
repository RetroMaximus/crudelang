# crude

**A tiny, expressive language and runtime for scripting audio, graphics and
hardware.**

`crude` glues together the pieces a live sound / interactive-graphics project
needs — [Pure Data](https://puredata.info/) synthesis via `libpd`, an
OpenGL widget interface, real-time audio I/O, and serial devices — behind one
small scripting language and a single command-line tool.

You write a `.crude` script that builds a patch, lays out widgets, and wires
events together; `crude` loads it, runs the frame loop, and drives the audio
callback. The same script runs headless in a terminal, in a window, or with no
display at all.

```
crude 0.1.0 · Python 3.12+ · Windows / Linux / macOS
```

---

## Contents

- [Why crude](#why-crude)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Your first project](#your-first-project)
- [Running a project](#running-a-project)
- [The language at a glance](#the-language-at-a-glance)
- [Compiling and building](#compiling-and-building)
- [Building the `libpd` library](#building-the-libpd-library)
- [Packaging `crude` with PyInstaller](#packaging-crude-with-pyinstaller)
- [How `crude` finds `libpd`](#how-crude-finds-libpd)
- [Command reference](#command-reference)
- [Exit codes](#exit-codes)
- [Project manifest](#project-manifest)
- [Repository layout](#repository-layout)
- [Running the tests](#running-the-tests)
- [Documentation](#documentation)
- [License](#license)

---

## Why crude

Audio-graphics projects usually mean juggling a synthesis environment, a GUI
toolkit, an audio driver and a serial library, each with its own mental model.
`crude` collapses that into **one script**:

- **Signal + code in one file.** Author Pure Data patches
  (`PD_BUILD` / `PD_OBJ` / `PD_LINK`) directly in the script and reach them from
  ordinary logic (`PD_SEND`, `ON_PD`).
- **Immediate-mode-friendly widgets.** Declare a UI (`TEXT`, `SLIDER`, `METER`,
  `TOGGLE`, `NUMBER`, …), place it (`PLACE`), and react (`ON_CHANGE`,
  `ON_FRAME`, `ON_HIT`) — rendered through OpenGL.
- **Runs anywhere.** `terminal`, `headless` and `window` modes: the same script
  that lights up a window also runs on a machine with no display or over SSH.
- **Deterministic exit codes** and a `check` command that validates a project
  without touching any hardware.

## How it works

A **project** is a directory containing:

```
myproject/
├── crude.manifest.json   # declares mode, audio, window size, search paths …
├── main.crude            # the entry script named by the manifest
└── pd/                   # (optional) .pd patches the script references
```

`crude run myproject` loads it in phases — manifest → environment → resources →
script → runtime → frame loop — then executes the entry script. The script
describes *what* exists (patches, widgets, connections); the runtime owns the
audio callback and the draw loop.

```
   ┌────────────┐   messages    ┌────────────┐   DSP    ┌──────────┐
   │ .crude     │ ────────────► │ libpd      │ ───────► │ audio    │
   │ script     │ ◄──────────── │ (Pure Data)│          │ device   │
   └────┬───────┘   events/hooks└────────────┘          └──────────┘
        │  widgets / frames
        ▼
   ┌────────────┐
   │ OpenGL UI  │   + serial devices, when declared
   └────────────┘
```

## Quick start

> Requires **Python 3.12 or newer**. The runtime and its dependencies are
> validated against CPython 3.14.

```bash
# 1. get the source
git clone https://github.com/your-org/crudelang.git
cd crudelang

# 2. an isolated environment
python -m venv .venv
#   Windows:  .venv\Scripts\activate
#   Unix:     source .venv/bin/activate

# 3. runtime dependencies (add requirements-dev.txt for tests & packaging)
pip install -r requirements.txt

# 4. provide the libpd shared library  (see "Building the libpd library")
#    fastest path: drop libpd.dll / libpd.so / libpd.dylib into libs/

# 5. confirm it runs
python -m crude --version
python -m crude --help
```

## Your first project

Scaffold a project that already loads, then run it:

```bash
python -m crude init hello --mode terminal
python -m crude check hello        # validate without any hardware
python -m crude run  hello         # runs the generated main.crude
```

`init` writes a `crude.manifest.json` and a starter `main.crude`:

```crude
LOG("hello from hello, running in " + MODE() + " mode")
VAR beat = 0
WHILE beat < 4 THEN
    VAR beat = beat + 1
    LOG("tick " + STR(beat))
END
```

Want to see a real audio + widgets project? Run the bundled example:

```bash
python -m crude run examples/metronome
```

## Running a project

```bash
python -m crude run <project-dir> [overrides]
```

A project's defaults come from its manifest; any of them can be overridden for
a single run without editing anything on disk:

| Flag | Meaning |
| --- | --- |
| `--mode {terminal,headless,window}` | override `runtime.mode` for this run |
| `--audio` / `--no-audio` | force a device open / open no device |
| `--backend {sounddevice,pyaudio,null}` | which audio backend to start with |
| `--no-serial` | do not open the declared serial links |
| `--fps N` | frame rate to aim at |
| `--progress {auto,console,json,splash,none}` | how to report the load steps |
| `--log LEVEL` | what to write to the project's log file |
| `--rebuild-pd` | ignore the cached generated `.pd` text and write it again |
| `--frames N` | pump `N` frames and stop (`0` = load + shutdown only; great for CI) |
| `--stats` | print what the clock measured when the script ended |

If a `window` can't be opened (no GPU, no display, SSH), `run` **downgrades
automatically** and tells you why on stderr, then continues in the next mode
that works — so headless runs never fail just because the manifest asked for a
window.

`check` runs only the hardware-free phases and lists the load plan:

```bash
python -m crude check examples/metronome --json    # machine-readable
```

## The language at a glance

`crude` scripts are line-oriented and read close to plain English. The pieces
below are enough to understand every bundled example.

**Values & control**

```crude
VAR bpm = 120                     # variable
IF bpm > 200 THEN VAR hot = 1     # condition
WHILE x < 10 THEN  VAR x = x + 1  END
FUNC on_frame(dt)                 # function used by ON_FRAME
    SET(led, "value", 0)
END
```

**Pure Data patches** (built into `.pd`, loaded into `libpd`)

```crude
VAR p    = PD_BUILD("metro", 360, 220)   # a patch canvas
VAR metro = PD_OBJ(p, "metro 500", 30, 80)
PD_LINK(p, metro, out)                     # wire objects together
PD_LOAD(p)                                  # instantiate it
PD_SEND(p, "bpm", v)                        # script -> patch
PD_DSP(1)                                   # start audio processing
ON_PD(p, "tick", FUNC() -> SET(led, "value", 1))   # patch -> script
```

**Widgets & layout**

```crude
VAR title = TEXT("METRONOME")
VAR bpm   = SLIDER("BPM", 20, 300, 120)
VAR field = NUMBER("BPM", 20, 300, 120)
VAR run   = TOGGLE("Run")
PLACE(bpm, 56, 40, 200, 24)
LINK_VALUE(bpm, field)                      # two-way binding
ON_CHANGE(bpm, FUNC(v) -> PD_SEND(p, "bpm", v))
ON_FRAME(on_frame)                          # per-frame tick
```

The full, authoritative list of every command and widget — with exact
signatures — lives in [`docs/bindings.md`](docs/bindings.md) and
[`docs/widgets.md`](docs/widgets.md), and is kept pinned to the runtime by an
automated test.

## Compiling and building

"Building" can mean three different things in `crude`; here is each one.

1. **Running a script** needs no separate compile step — `crude run` loads and
   executes the `.crude` file directly (see [Running a project](#running-a-project)).

2. **Compiling the Pure Data patches.** `PD_BUILD` … `PD_LOAD` generate `.pd`
   patch text that `libpd` loads. The generated text is cached under the
   manifest's `pd.build_dir` (default `.crude_build/pd`) and reused between runs.
   Force a fresh generation with `crude run --rebuild-pd`, or generate and
   inspect standalone with the `crude pd` command.

3. **Packaging the `crude` tool itself** into a standalone executable with
   PyInstaller — see [Packaging crude with PyInstaller](#packaging-crude-with-pyinstaller).

## Building the `libpd` library

`crude`'s synthesis runs on **libpd**, the Pure Data engine as a shared library.
`crude` does **not** ship it and does **not** download it: you either build it
from source or reuse a copy already on your machine.

The build helper searches common install locations automatically. If it finds
nothing, build from source — the vendored recipes live in `scripts/`:

```bash
# Windows (from an MSYS2 / MinGW shell; builds against third-party/libpd)
bash scripts/pd_dll_build.sh

# *Note before building libpd it is suggested to build pure-data then move it to
# the libpd source directory.

# Linux / macOS
bash scripts/libpd_build.sh
```

Both scripts compile `libpd`, then copy the result (and, on Windows, the MinGW
runtime DLLs it links against) into `libs/`. Once a matching
`libpd.dll` / `libpd.so` / `libpd.dylib` is in `libs/`, everything just works.

If you build elsewhere, or already have libpd installed, the fastest path is to
tell the build where it is — see the next two sections.

## Packaging `crude` with PyInstaller

`build.py` at the repository root turns `crude` into a standalone executable
for the machine it runs on, and handles the `libs/` bundling for you:

```bash
pip install -r requirements-dev.txt   # brings in PyInstaller

python build.py                        # one-dir bundle for the current OS
python build.py --onefile              # a single self-contained executable
python build.py --verify               # after building, run the exe's --version
python build.py --dry-run              # print the plan, build nothing

python build.py --libpd /path/to/libpd.so   # stage this library into libs/ first
python build.py --skip-libs                 # libs/ is already correct, leave it
```

What `build.py` does, in order:

1. **Detects the platform** and the library name it needs.
2. **Ensures `libs/` holds the right libpd.** If it is missing, it searches the
   usual places (an existing `third-party/libpd` build, MSYS2/MinGW, a Pure Data
   install, Homebrew, system library directories) and copies the library — plus
   the Windows MinGW runtime siblings `libpd.dll` depends on — into `libs/`. If
   nothing is found it stops with the exact `scripts/*.sh` command to build it.
3. **Runs PyInstaller**, collecting the whole `crude` package and bundling
   `libs/` under a `libs/` prefix inside the executable.
4. **Optionally smoke-tests** the result (`--verify`).

The output lands in `dist/`:

| Mode | Executable |
| --- | --- |
| one-dir (default) | `dist/crude/crude` (`crude.exe` on Windows) |
| one-file (`--onefile`) | `dist/crude` (`dist/crude.exe`) |

PyInstaller bundles **the machine it runs on** — cross-building a Windows `.exe`
from Linux is not automatic. Build each target on its own OS (or CI runner).

The `--add-binary` layout written here is exactly what the runtime's library
lookup expects, so a frozen build finds `libpd` the same way a checkout does.

## How `crude` finds `libpd`

At runtime (:mod:`crude.pd.lib`) the shared library is resolved in this order —
useful when a packaged build runs on a machine without your development setup:

1. `$CRUDE_LIBPD_PATH` — either the file itself or a directory containing it;
2. `libs/` next to the `crude` package (a normal checkout);
3. the PyInstaller bundle — `sys._MEIPASS/libs/` (a `build.py` executable);
4. `libs/` under the current working directory.


On Windows the loader also registers the library's directory with
`os.add_dll_directory` so the MinGW runtime siblings resolve; on
Linux/macOS the `.so` / `.dylib` pulls its dependencies from the system paths.

## Command reference

`crude <command>` (or `python -m crude <command>` from a checkout, or the
`crude` executable from `build.py`):

| Command | What it does |
| --- | --- |
| `run [project]` | load a project, run its entry script, exit with its code |
| `check [project]` | validate a project and list its load steps — no device, no window |
| `init <name>` | write a starter project (`--dir`, `--mode`, `--force`) |
| `pd` | inspect, generate and audition `.pd` patches |
| `widgets` | print the widget schema (`--json`, `--kind`, `--theme`) |
| `glyphs` | list, dump or preview the generated icon set |

Add `--help` to any command for its full set of flags.

## Exit codes

`crude` reports *what class* of thing went wrong, not just that something did:

| Code | Meaning |
| --- | --- |
| `0` | success |
| `2` | bad manifest **or** a command-line mistake |
| `3` | a load phase failed |
| `4` | the script raised |
| `5` | a runtime / environment failure |

## Project manifest

`crude.manifest.json` describes a project. Every key has a default, so a minimal
manifest is just an `id` and an `entry`:

```json
{
  "$schema": "crude/manifest@1",
  "id": "metronome",
  "name": "Metronome",
  "entry": "main.crude",
  "runtime": { "mode": "window", "width": 360, "height": 220,
               "target_fps": 60, "vsync": true },
  "ui":      { "theme": "dark", "icons": "generated" },
  "audio":   { "backend": "sounddevice", "sample_rate": 44100,
               "block_size": 64, "in_channels": 0, "out_channels": 2 },
  "pd":      { "search_paths": ["pd"], "build_dir": ".crude_build/pd" },
  "loading": { "progress": "auto" }
}
```

The complete field-by-field reference is in
[`docs/manifest.md`](docs/manifest.md).

## Repository layout

```
crudelang/
├── crude/              # the runtime: lang, bindings, ui, audio, pd, serial,
│                       #   project loader, cli and commands
├── examples/           # worked projects (metronome, fm_synth, …)
├── libs/               # the libpd shared library + its runtime siblings
├── scripts/            # libpd build recipes (pd_dll_build.sh, libpd_build.sh)
├── tests/              # the pytest suite
├── docs/               # language reference (hand-written) + generated API docs
├── build.py            # package crude with PyInstaller (Win/Linux/macOS)
├── crudelang.py        # compatibility shim for the historical scripts
├── pytest.ini          # test runner config
├── README.md           # this file
└── requirements*.txt   # runtime / dev / optional dependencies
```

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

Tests that need audio hardware, a display, a GPU or a serial device are marked
and deselected by default (`pytest.ini`), so the suite is green on a headless
machine. The language-reference docs are pinned against the runtime's binding
registry by `tests/lang/test_docs_pinned_to_registry.py`, so a renamed or
mis-documented binding fails CI rather than quietly drifting.

## Documentation

| Guide                                          | Topic                                               |
|------------------------------------------------|-----------------------------------------------------|
| [`docs/scripting.md`](docs/scripting.md)       | the language: statements, expressions, control flow |
| [`docs/bindings.md`](docs/bindings.md)         | every binding and its signature                     |
| [`docs/build.md`](docs/build.md)               | Optional runtime dependencies                       |
| [`docs/crudelang.md`](docs/crudelang.md)       | module-level object                                 |
| [`docs/widgets.md`](docs/widgets.md)           | widget kinds, properties, events                    |
| [`docs/manifest.md`](docs/manifest.md)         | the project manifest, field by field                |
| [`docs/architecture.md`](docs/architecture.md) | how the phases and subsystems fit together          |
| [`docs/glyphs.md`](docs/glyphs.md)             | the generated icon set                              | 

`docs/*.md` files named after a Python module (`crudelang`, `build`, `shell`,
…) are generated API references; the language guides above are hand-written and
kept in sync with the code by tests.

## License

No open-source license has been declared for this repository yet. Add a
`LICENSE` file at the top level (and update this section) before distributing.
Pure Data / libpd and the bundled third-party components carry their own
licenses.
