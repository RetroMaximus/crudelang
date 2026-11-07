<a id="top"></a>

## Optional runtime dependencies

The bundler below collects libpd and the packages a project needs to run. Two
dependencies are deliberately *optional* rather than hard pins, because the
engine degrades gracefully when neither is installed:

- `sounddevice` - the live audio backend. Without it the runtime falls back to
  the :class:`~crude.audio.NullBackend`, so a build still runs (silently) and
  every `AUDIO_*` binding still answers from its taps.
- `python-rtmidi` - the MIDI backend. `crude.midi` imports `rtmidi` lazily,
  inside the methods that need it; when the module is absent
  `RtMidiBackend.available()` is false, `MIDI_DEVICES(...)` lists nothing, and
  the File menu's MIDI dialog shows one disabled "no MIDI devices" row instead
  of raising. See `midi.md`.

`requirements.txt` lists `python-rtmidi` under a comment, not pinned, for the
same reason: no cp314 wheel is guaranteed to exist, so it must never be a hard
install requirement that fails a `pip install` on a newer interpreter. A machine
whose wheel is present (the cp312 wheel imports cleanly today) gets real ports
with no further configuration.

## Table of Contents

### Functions:

[`current_platform`](#func-current_platform) | [`_program_files`](#func-_program_files) | [`search_dirs`](#func-search_dirs) | [`find_installed_libpd`](#func-find_installed_libpd) | [`_runtime_siblings`](#func-_runtime_siblings) | [`ensure_library`](#func-ensure_library) | [`_bundle_files`](#func-_bundle_files) | [`build_command`](#func-build_command) | [`exe_path`](#func-exe_path) | [`run_build`](#func-run_build) | [`verify_exe`](#func-verify_exe) | [`parse_args`](#func-parse_args) | [`main`](#func-main) | [`_module_present`](#func-_module_present)



<a id="func-current_platform"></a>
## Function: `current_platform`

### Help:
> Map ``sys.platform`` onto the three names this script understands.

### Usage:
```python
()
```

[Back to top](#top)



<a id="func-_program_files"></a>
## Function: `_program_files`

### Help:
> The Windows Program Files roots, whichever drive they live on.

### Usage:
```python
()
```

[Back to top](#top)



<a id="func-search_dirs"></a>
## Function: `search_dirs`

### Help:
> Candidate directories that plausibly already hold a libpd, in priority
order. Deliberately a bounded, hand-picked list rather than a whole-disk
walk: fast, predictable, and it never reaches into unrelated user files.

### Usage:
```python
(platform)
```

[Back to top](#top)



<a id="func-find_installed_libpd"></a>
## Function: `find_installed_libpd`

### Help:
> First libpd found in the search dirs, or ``None``.

### Usage:
```python
(platform)
```

[Back to top](#top)



<a id="func-_runtime_siblings"></a>
## Function: `_runtime_siblings`

### Help:
> MinGW runtime DLLs that live next to ``source`` and must travel with it.
Only meaningful on Windows; empty elsewhere (ELF/Mach-O resolve their own
dependencies from system paths).

### Usage:
```python
(
    source,
    platform
)
```

[Back to top](#top)



<a id="func-ensure_library"></a>
## Function: `ensure_library`

### Help:
> Guarantee ``libs/<libpd>`` exists for ``platform``; return its path.

``override`` is an explicit path the user pointed at. ``skip`` means "leave
libs/ exactly as it is" (used when the operator already staged the library).
Raises ``SystemExit`` with build-from-source guidance when nothing is found.

### Usage:
```python
(
    platform,
    override,
    skip,
    quiet=False
)
```

[Back to top](#top)



<a id="func-_bundle_files"></a>
## Function: `_bundle_files`

### Help:
> Everything in libs/ that should travel inside the executable.

### Usage:
```python
(platform)
```

[Back to top](#top)



<a id="func-build_command"></a>
## Function: `build_command`

### Help:
> Assemble the ``python -m PyInstaller ...`` argument list.

### Usage:
```python
(
    platform,
    onefile,
    add_libs,
    clean,
    entry
)
```

[Back to top](#top)



<a id="func-exe_path"></a>
## Function: `exe_path`

### Help:
> Where the built executable lands for the chosen bundling mode.

### Usage:
```python
(
    platform,
    onefile
)
```

[Back to top](#top)



<a id="func-run_build"></a>
## Function: `run_build`

### Help:
> Run the assembled PyInstaller command, streaming its output.

### Usage:
```python
(
    cmd,
    quiet=False
)
```

[Back to top](#top)



<a id="func-verify_exe"></a>
## Function: `verify_exe`

### Help:
> Prove the frozen binary starts and reaches its libraries: run --version.

Returns ``True`` on a clean exit. The check is deliberately shallow - a real
audio/window run needs devices - but ``--version`` still forces PyInstaller's
import machinery to resolve the collected package, which is the part a bundle
most often gets wrong.

### Usage:
```python
(
    path,
    quiet=False
)
```

[Back to top](#top)



<a id="func-parse_args"></a>
## Function: `parse_args`

### Help:
> No help provided.

### Usage:
```python
(argv)
```

[Back to top](#top)



<a id="func-main"></a>
## Function: `main`

### Help:
> No help provided.

### Usage:
```python
(argv=None)
```

[Back to top](#top)



<a id="func-_module_present"></a>
## Function: `_module_present`

### Help:
> No help provided.

### Usage:
```python
(name)
```

[Back to top](#top)

