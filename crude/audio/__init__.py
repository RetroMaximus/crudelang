"""Audio: one stream, several backends, and a chain that always lands on silence.

This package is the audio half of the host. A script never imports it - it calls
the ``AUDIO_*`` bindings, which talk to :class:`crude.audio.runtime.AudioRuntime`,
which talks to one :class:`~crude.audio.base.AudioBackend`.

The rule the whole layer is built around (plan risk 7): **a missing device is a
warning, not a failure**. :func:`open_backend` therefore walks a chain - the
backend a manifest asked for first, then the preferred order - and ends in
:class:`~crude.audio.null.NullBackend`, which renders blocks on demand with no
hardware at all. Only a *strict* request raises, and only after naming every
backend it tried and why each one failed. "Warning" is the operative word: every
fallback is logged by name, and a run that wanted audio and landed on the pulled
backend is told - in its own output, by the loader - that nothing will be heard.
Silence is never presented as working sound.

The package also carries audio's file side: :mod:`crude.audio.wav` reads and
writes uncompressed PCM, and :class:`~crude.audio.base.Capture` is where the
frames a script records go. Both are reached through the ``WAV_*`` and
``AUDIO_RECORD*`` bindings rather than by a script importing them.

Nothing here imports ``sounddevice`` or ``pyaudio`` at module scope: the adapters
do it lazily, so importing this package works on a machine with no PortAudio and
a test can stub the library in ``sys.modules`` and drive the callback by hand.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Tuple, Type

from .base import (
    DB_FLOOR,
    DEFAULT_BLOCK_SIZE,
    DEFAULT_SAMPLE_RATE,
    INFO_PROPS,
    AudioBackend,
    AudioConfig,
    AudioError,
    AudioUnavailable,
    Capture,
    DeviceInfo,
    RecordingRender,
    Renderer,
    Taps,
    bytes_to_samples,
    check_direction,
    copy_samples,
    read_samples,
    resolve_device,
    samples_to_bytes,
    status_text,
)
from .null import NULL_DEVICE, NullBackend
from .pa_backend import PyAudioBackend
from .sd_backend import SoundDeviceBackend
from .wav import (
    PCM8,
    PCM16,
    WIDTHS,
    WavData,
    plane_range,
    read_wav,
    read_wav_header,
    write_wav,
)

__all__ = [
    "AudioBackend",
    "AudioConfig",
    "AudioError",
    "AudioUnavailable",
    "Capture",
    "DeviceInfo",
    "NullBackend",
    "PyAudioBackend",
    "RecordingRender",
    "SoundDeviceBackend",
    "Taps",
    "NULL_DEVICE",
    "DB_FLOOR",
    "DEFAULT_SAMPLE_RATE",
    "DEFAULT_BLOCK_SIZE",
    "INFO_PROPS",
    "Renderer",
    "read_samples",
    "bytes_to_samples",
    "samples_to_bytes",
    "copy_samples",
    "check_direction",
    "status_text",
    "resolve_device",
    "PCM8",
    "PCM16",
    "WIDTHS",
    "WavData",
    "read_wav",
    "read_wav_header",
    "write_wav",
    "plane_range",
    "BACKENDS",
    "PREFERENCE",
    "register_backend",
    "backend_class",
    "backend_names",
    "available_backends",
    "audio_chain",
    "list_devices",
    "open_backend",
    "AudioRuntime",
]


# -- the registry -------------------------------------------------------

BACKENDS: Dict[str, Type[AudioBackend]] = {}

#: the order they are tried in when nothing was asked for: the default dependency
#: first, the optional one second, and silence last.
PREFERENCE: Tuple[str, ...] = ("sounddevice", "pyaudio", "null")


def register_backend(cls: Type[AudioBackend], name: str = None) -> Type[AudioBackend]:
    """Add a backend class to the chain; the class decorator form too."""
    key = name or getattr(cls, "name", None) or cls.__name__
    BACKENDS[key] = cls
    return cls


for _cls in (SoundDeviceBackend, PyAudioBackend, NullBackend):
    register_backend(_cls)


def backend_names() -> Tuple[str, ...]:
    """Every registered backend, preference order first."""
    known = tuple(name for name in PREFERENCE if name in BACKENDS)
    return known + tuple(name for name in BACKENDS if name not in known)


def backend_class(name: str) -> Type[AudioBackend]:
    cls = BACKENDS.get(name)
    if cls is None:
        raise AudioError(
            f"there is no audio backend named '{name}' "
            f"(known backends: {', '.join(backend_names())})"
        )
    return cls


def available_backends() -> List[str]:
    """The backends that could be tried on this machine, in preference order."""
    return [name for name in backend_names() if BACKENDS[name].available()]


def audio_chain(preferred=None, strict: bool = False) -> Tuple[str, ...]:
    """The names to try: what was asked for, then the rest, and always ``null``.

    ``preferred`` may be a name, a class, or a list of either - a manifest names
    one backend, a test may want to order the chain itself. ``strict`` means
    "this one or nothing": the chain stops at what was asked for.
    """
    names = _as_names(preferred)
    if strict:
        if not names:
            raise AudioError("a strict audio request has to name a backend")
        return tuple(names)

    ordered = list(names)
    for name in PREFERENCE:
        if name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def _as_names(preferred) -> List[str]:
    """A name, a class, or a list of either, as the names the chain tries."""
    if preferred is None:
        return []
    if isinstance(preferred, str):
        return [preferred]
    if isinstance(preferred, type):
        name = getattr(preferred, "name", "")
        if not name or name == AudioBackend.name:
            raise AudioError(
                f"{preferred.__name__} does not say what it is called in the chain"
            )
        return [name]
    if isinstance(preferred, Iterable):
        names = []
        for entry in preferred:
            names.extend(_as_names(entry))
        return names
    raise AudioError(
        f"an audio backend must be named as a string or a class, "
        f"got {type(preferred).__name__}"
    )


# -- discovery ----------------------------------------------------------

def list_devices(direction: str = None, backend: str = None) -> List[DeviceInfo]:
    """Every device every usable backend reports.

    Device lists are per library, so a machine with both sees both. A backend
    that cannot enumerate (no module, no PortAudio) contributes nothing instead
    of failing the call - the same rule as the fallback chain.
    """
    if direction:
        check_direction(direction)
    names = [backend] if backend else list(BACKENDS)
    found: List[DeviceInfo] = []
    for name in names:
        cls = backend_class(name)
        try:
            if not cls.available():
                continue
            found.extend(cls.devices())
        except AudioError:
            continue

    if direction:
        found = [device for device in found if device.supports(direction)]
    return found


# -- the fallback chain -------------------------------------------------

def open_backend(config: AudioConfig = None, render: Renderer = None,
                 log: Callable[[str], None] = None, preferred=None,
                 notes: List[str] = None, strict: bool = False,
                 scope_frames: int = 1024) -> AudioBackend:
    """Open the first backend that works, recording why the others did not.

    Returns a running (opened) backend. With ``strict`` the named backend either
    opens or :class:`AudioUnavailable` is raised listing every attempt, which is
    what a manifest with ``"optional": false`` asks for.
    """
    log = log or (lambda message: None)
    notes = notes if notes is not None else []
    config = config if config is not None else AudioConfig()
    chain = audio_chain(preferred, strict=strict)

    for name in chain:
        cls = BACKENDS.get(name)
        if cls is None:
            _note(notes, log, f"audio: there is no backend named '{name}' "
                              f"(known: {', '.join(backend_names())})")
            continue
        if not cls.available():
            _note(notes, log, f"audio: {name} is not usable on this machine")
            continue
        try:
            backend = cls(config=config, render=render, log=log,
                          scope_frames=scope_frames)
            backend.open()
        except AudioUnavailable as exc:
            _note(notes, log, f"audio: {name} fell back: {exc}")
            continue
        # ``AudioError`` (a bad configuration) is deliberately not caught: it is
        # the caller's mistake, not this backend's, and every backend would fail.

        backend.notes = list(notes)
        return backend

    detail = "; ".join(notes)
    raise AudioUnavailable(
        f"no audio backend could be opened: {detail}" if detail
        else "no audio backend could be opened"
    )


def _note(notes: List[str], log: Callable[[str], None], message: str) -> None:
    notes.append(message)
    log(message)


# -- the script-facing runtime ------------------------------------------

from .runtime import AudioRuntime  # noqa: E402  (imports the registry above)
