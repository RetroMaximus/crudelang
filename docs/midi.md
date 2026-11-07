# MIDI

MIDI is the second real I/O engine after audio, and it is built to the same
rule: **no library on the machine is a note, not a failure**. A script calls the
`MIDI_*` bindings; they talk to one :class:`crude.midi.runtime.MidiRuntime`; the
runtime talks to one :class:`~crude.midi.base.MidiBackend`. Whether that backend
is a real controller or a machine with no `python-rtmidi` installed at all is a
question neither the script nor the tests can tell the difference about - and
that is the point.

This is a port engine, not an auto-router: it enumerates ports, opens an input
or an output, hands inbound messages to a handler, and lets the script send
bytes out. It does not map MIDI notes onto particular widgets - a script does
that itself in an `ON_MIDI` handler.

## The layers

```
crude/midi/
  base.py            MidiBackend ABC, MidiDeviceInfo, MidiMessage,
                     MidiError / MidiUnavailable, DIRECTIONS, check_direction
  rtmidi_backend.py  the real engine; imports ``rtmidi`` lazily, per method
  null_backend.py    the always-available fallback: zero devices, opens nothing
  runtime.py         MidiRuntime - the object the bindings talk to
  __init__.py        the backend registry and the fallback chain
```

Nothing in the package imports `rtmidi` at module scope. Only
`rtmidi_backend.py` does, and only inside the methods that need it, exactly the
way `crude/audio/sd_backend.py` imports `sounddevice`. So `import crude.midi`
works on a machine that has no MIDI library whatsoever.

### `base.py`

`MidiBackend` is the contract: `available()`, `devices(direction)`,
`open_input(index, callback)`, `open_output(index)`, `send(port, message)`,
`close()`. `MidiDeviceInfo` describes one port (`index`, `name`, `direction`,
`api`), and `MidiMessage` is one packet - `bytes` plus a timestamp. Two errors
matter to a script: `MidiError` (a bad index, a closed port, a wrong direction)
and `MidiUnavailable` (no backend could be opened at all).

### The registry and the chain (`__init__.py`)

`BACKENDS` holds the registered classes - `rtmidi` and `null` out of the box -
and `PREFERENCE = ("rtmidi", "null")` is the order they are tried in when
nothing was asked for: the real engine first, silence last.

`open_backend(preferred=None, strict=False)` walks that chain and always lands
somewhere: it tries what was asked for first, then the rest, and ends on the
null backend, which reports zero devices, opens nothing, and never raises for a
missing library. `available()` answers the backends that could actually be tried
here. A *strict* request (`strict=True`) either opens the named backend or
raises `MidiUnavailable` listing every attempt and why each failed - that is
what a manifest that says `"optional": false` on its `midi` block asks for.

`list_devices(direction, backend)` is the convenience the File menu uses: every
port every usable backend reports, for one direction or both, with a backend
that cannot enumerate contributing nothing instead of failing the call.

## The runtime

`MidiRuntime` owns a backend and the ports that are open on it. Inbound
messages reach the registered `ON_MIDI` handlers through the per-frame
`dispatch`, not on the backend's reader thread - the same hand-over the serial
runtime uses, so a handler runs on the interpreter thread where it is safe to
call `PD_SEND`. A handler that keeps raising is muted after
`MAX_HANDLER_ERRORS` (three) strikes, so a broken callback cannot flood the
frame budget; the counter resets on a frame that raises nothing.

A port handle is readable through `GET` (its direction, index, name and whether
it is open) but deliberately not writable - a port's direction and index are
decided when it is opened, the same rule an audio stream follows.

## The bindings

```
MIDI_DEVICES([direction])          MIDI_OPEN(direction, index)
MIDI_SEND(port, data)              MIDI_CLOSE(port)
ON_MIDI(port, fn)                  OFF_MIDI(port, [fn])
```

`MIDI_DEVICES` answers `"index: name"` lines for `"input"`, `"output"`, or both
when no direction is given. `MIDI_OPEN` opens one of those ports by the index
`MIDI_DEVICES` just reported and returns a handle. `MIDI_SEND(port, data)`
writes one packet - a list of MIDI bytes, or a single number, or a string taken
as its UTF-8 bytes - to an open output. `ON_MIDI(port, fn)` registers a handler
and returns a handler handle so `OFF_MIDI` can reach just that one; with no
`fn`, `OFF_MIDI(port)` drops every handler on the port.

## Asking for MIDI: the manifest block

A project opts in with an optional `midi` section. Absent the whole key means
*no MIDI at all*: the loader never builds a runtime, and `MIDI_DEVICES` is not
reachable as a live subsystem.

```json
{
  "midi": {
    "backend": "rtmidi",
    "optional": true,
    "enabled": true
  }
}
```

- `backend` - the preferred backend name; unknown names are rejected at
  `crude check` against the registered set. Omit it to take the preference
  order.
- `enabled` (default `false` when the block is absent, `true` inside a present
  block) - whether this run builds a runtime at all.
- `optional` (default `true`) - `true` means no `python-rtmidi` here is a note,
  not a failure, and the project still loads with no port open. `false` makes
  the request *strict*: failing to open the named backend ends the load.

`--no-midi` turns the whole subsystem off for one run without touching the
manifest, mirroring `--no-audio`.

## When there is no MIDI library

`python-rtmidi` ships no guaranteed wheel for every interpreter (see
`docs/build.md`), so the engine treats its absence as normal. A declared,
optional MIDI project on such a machine opens the null backend, lists zero
devices, and the File menu's MIDI dialog shows one disabled "no MIDI devices"
row rather than crashing. Nothing a script can call raises for a missing
library that the project did not insist on.

## The menu dialog

The shipped windowed examples build a MIDI picker with one call -
`MENU_MIDI_DIALOG(midi_menu)` - which composes two `DROPDOWN`s filled from
`MIDI_DEVICES("input")` and `MIDI_DEVICES("output")` and an Apply that calls
`MIDI_OPEN` on the chosen indices. Because the panel reads its ports only when a
row opens it, wiring the MIDI menu costs no device call at load time - which is
why `crude check` and a headless run parse and build the bar without ever
touching a port, and why a project that never declares MIDI (no runtime on the
host) still gets one disabled "no MIDI devices" row.

## Testing

Every MIDI test is stub-driven: a fake `rtmidi` module is placed in
`sys.modules` (see `tests/midi/`), and `sys.modules["rtmidi"] = None` models
the library being absent. Nothing needs a controller, a DLL, or a device. The
runtime is tested against a fake backend for enumeration, open/send, dispatch
and the mute budget; the bindings are tested for argument handling and for
failing gracefully when no backend is usable.
