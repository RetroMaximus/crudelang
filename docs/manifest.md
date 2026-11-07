# Manifest

Every crude project is a directory with a `crude.manifest.json` in it. The
manifest says what the project *is*: the runtime mode, the widgets, the
patches, the resources, the audio shape, the serial links, and how the load
should talk to the person while it is happening. Nothing in the manifest
runs code; every key has a place in the fourteen-phase pipeline that reads
it. This page is the schema as :mod:`crude.project.manifest` implements it.

## Top level

| Key          | Type          | Default                     | Read by phase         |
| ------------ | ------------- | --------------------------- | --------------------- |
| `$schema`    | string        | `"crude/manifest@1"`        | 1 `project.check`     |
| `id`         | string        | `"untitled"`                | 1                     |
| `name`       | string        | same as `id`                | 1                     |
| `entry`      | path          | `"main.crude"`              | 1, 13 `script.compile`|
| `runtime`    | object        | see below                   | 1, 14 `runtime.activate` |
| `ui`         | object        | see below                   | 1, 14                 |
| `audio`      | object        | see below                   | 5 `audio.config`, 11 `audio.device` |
| `pd`         | object        | see below                   | 6-9 (`pd.*`)          |
| `serial`     | array         | `[]`                        | 12 `serial`           |
| `resources`  | object        | see below                   | 2 `resources.read`, 3 `arrays.parse`, 7 `pd.presets` |
| `loading`    | object        | `{"progress": "auto"}`      | the reporter, wired before phase 1 |

A key that is not on this list is a `ManifestError` (exit 2). That is
deliberate: the point of the schema is to notice typos *before* a run that
silently used the default instead.

## `$schema`

A string `"crude/manifest@N"` where `N` is a positive integer no greater
than the runtime's own version. A manifest from a newer crude is refused
with a message that says *which* version this runtime is, so a person
reading an old project on a new machine sees an actionable answer. Absent
means the current version.

## `id`, `name`, `entry`

`id` is used as a directory name (for the build cache and for the log file),
so it is restricted to letters, digits, `-`, `_` and `.`. `name` is what the
window title and the reporters say; `entry` must be a `.crude` path inside
the project.

Every path in the manifest is *relative* to the project root: absolute
paths, `~` paths, and any path that walks outside the project (with `..`)
are manifest errors. The wildcards `*` and `?` are allowed only in
`resources.presets` and only in the file-name segment.

## `runtime`

```
{
  "mode": "window",
  "width": 360,
  "height": 220,
  "target_fps": 60,
  "vsync": true
}
```

`mode` is one of `"terminal"`, `"headless"`, `"window"`. See
`architecture.md` for what each does; the CLI's `--mode` overrides it per
run, and a mode a machine cannot do downgrades one step (window → headless
→ terminal) with a message. `width`/`height` are the window's pixels (and
the client rect a headless run uses for the widget tree). `target_fps` is
what the loop paces against; `--fps` overrides it. `vsync` is a hint GLFW
is allowed to ignore; a headless run has no vsync to talk about.

## `ui`

```
{ "theme": "dark", "icons": "generated", "font": null,
  "workspaces": ["sampler", "sequencer", "mixer", "config"] }
```

`theme` names a skin in :mod:`crude.ui.skin`. `icons` is `"generated"` by
default, which means the built-in glyph set draws every icon; a project
that ships its own icons can point this at a folder. `font` is an optional
project-relative path to a bitmap or TTF the widget renderer uses; with
`null` the built-in default is loaded from the glyph atlas.

`workspaces` is optional and names only: the scenes a project has, in the
order a `View` menu should list them and `WORKSPACE_INDEX()` should count.
`root` is always index 0 and is not declared here (an entry script's scene is
the window's own root); a duplicate, or the reserved `shared` - the stamp the
menu bar and every popup carry - is a manifest error. The list is advisory in
the same way `midi` is: the runtime reserves those names on the context before
the script runs, so `WORKSPACE("mixer")` fills in the reserved scene instead of
colliding with it, and a project that names a scene it never draws simply has an
empty page. Absent means only `root`, which is every example that does not
mention workspaces.

## `audio`

```
{
  "backend": "sounddevice",
  "sample_rate": 44100,
  "block_size": 64,
  "in_channels": 2,
  "out_channels": 2,
  "input_device": null,
  "output_device": null,
  "optional": false,
  "enabled": true
}
```

`backend` names one of the registered audio backends
(:func:`crude.audio.backend_names`); an unknown name is a manifest error
even before the device is opened. `input_device` and `output_device` are
either a zero-based index or a substring of the device name; `null` means
"whatever the backend defaults to". `optional: true` means a fallback to
silence is a *note*, not a load failure. `enabled: false` opens no device
at all: a run that only wants the metering taps and the pulled tick sets
this to `false`.

## `pd`

```
{
  "search_paths": ["pd"],
  "build_dir": ".crude_build/pd",
  "patches": [
    { "id": "sensors", "file": "pd/sensors.pd", "preload": true },
    { "id": "fm",      "authored": { "width": 520, "height": 320, "preload": true } }
  ]
}
```

`search_paths` is what libpd's `addsearchpath` gets; the project root is
always first. `build_dir` is where the loader writes generated `.pd` files
one at a time; a hash of the patch text names each one, so a rebuild that
did not change the patch is a no-op. `--rebuild-pd` forces a rewrite.

A patch entry is either a `file` (a hand-written `.pd` on disk) or
`authored` (a size for a document the script fills via `PD_BUILD` and
friends). Both may `preload`; `optional: true` on a `file` means a missing
one is a note, not a load failure. Ids are unique within `patches`, and
`resources.arrays[i].patch` must refer to one of them.

## `serial`

An array of links:

```
[
  { "id": "arduino", "port": "COM3", "baud": 115200,
    "codec": "line", "optional": true }
]
```

`port` is a scheme-or-name: a bare `COM3` or `/dev/ttyUSB0` reaches
pyserial; a `loop://name` or `memory://name` reaches a fake port the tests
and the CI use. `codec` names an entry in the serial codec registry
(`"line"`, `"raw"`, `"packet"` today); adding a codec needs no schema
change because the manifest only checks that *a* name is given. `optional`
is `true` by default, so a machine without `COM3` carries on with the link
missing and the script's `SERIAL_OPEN("arduino")` finds nothing rather
than crashing the load.

## `resources`

```
{
  "images":   ["assets/*.png"],
  "sounds":   ["assets/kick.wav"],
  "arrays":   [{ "id": "wave", "file": "assets/wave.txt",
                 "format": "text", "patch": "fm" }],
  "presets":  ["presets/*.json"]
}
```

`images` and `sounds` are lists of project-relative paths, checked for
existence at the `resources.read` phase. `presets` allows `*` in the file
name, resolved at `pd.presets`. `arrays` is the one kind with a shape: a
`format` of `"text"` is a column of numbers, `"pd"` is a Pd `array save`
file, `"csv"` is one line of them. `patch` (optional) names which declared
patch owns the table; a mismatch is a manifest error rather than a load
error, because it is the manifest's own story not adding up.

## `loading`

```
{ "progress": "auto" }
```

`auto` picks the reporter for the mode: `splash` in `window`, `console` in
`headless`, `none` in `terminal`. The other four values are
`"console"`, `"json"`, `"splash"`, `"none"`. A run that asked for `splash`
on a machine without a window shows the console reporter and says why.

The CLI's `--progress` overrides this per run; `--progress none` is what
every CI cell uses so the reporter's own drawing is never what is under
test.

## How a mistake is reported

Every validation failure carries a *pointer* to where in the document the
problem is (`pd.patches[0].id`, `resources.arrays[2].patch`) and a message
that says what to type. Exit code is `2`, and stderr has the pointer and
the message:

```
crude check: manifest: unexpected key 'target_flps' in runtime.target_fps;
known here: height, mode, target_fps, vsync, width (in ./crude.manifest.json)
```

A missing `crude.manifest.json` is the same code, and a manifest that is
not valid JSON points at the line the parser gave up on. The rule is: the
CLI never guesses a substitute for a key the person typed wrong.
