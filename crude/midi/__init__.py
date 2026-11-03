"""MIDI: one engine, several backends, and a default that is no MIDI at all.

This package is the MIDI half of the host. A script never imports it - it calls
the ``MIDI_*`` bindings, which talk to :class:`crude.midi.runtime.MidiRuntime`,
which talks to one :class:`~crude.midi.base.MidiBackend`.

The rule the whole layer is built around is the audio layer's rule: **no MIDI
library is a note, not a failure**. ``python-rtmidi`` ships no guaranteed wheel
for every Python (see ``docs/build.md``); :func:`open_backend` therefore tries
what was asked for first, then the rest, and always lands on
:class:`~crude.midi.null_backend.NullMidiBackend` - which reports zero devices,
opens no input, and never raises for a missing library. Only a *strict* request
raises, and only after naming every backend it tried and why each one failed.

Nothing here imports ``rtmidi`` at module scope: only
:mod:`crude.midi.rtmidi_backend` does, and only inside the methods that need it.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple, Type

from .base import (
    DIRECTIONS,
    MidiBackend,
    MidiDeviceInfo,
    MidiError,
    MidiMessage,
    MidiUnavailable,
    check_direction,
)
from .null_backend import NullMidiBackend
from .rtmidi_backend import RtMidiBackend

__all__ = [
    "MidiBackend",
    "MidiDeviceInfo",
    "MidiError",
    "MidiMessage",
    "MidiUnavailable",
    "NullMidiBackend",
    "RtMidiBackend",
    "DIRECTIONS",
    "check_direction",
    "BACKENDS",
    "PREFERENCE",
    "register_backend",
    "backend_class",
    "backend_names",
    "available_backends",
    "midi_chain",
    "list_devices",
    "open_backend",
    "MidiRuntime",
    "MidiPort",
    "MidiHandler",
]


# -- the registry -------------------------------------------------------

BACKENDS: Dict[str, Type[MidiBackend]] = {}

#: the order they are tried in when nothing was asked for: the real engine
#  first, silence last.
PREFERENCE: Tuple[str, ...] = ("rtmidi", "null")


def register_backend(cls: Type[MidiBackend], name: str = None) -> Type[MidiBackend]:
    """Add a backend class to the chain; the class decorator form too."""
    key = name or getattr(cls, "name", None) or cls.__name__
    BACKENDS[key] = cls
    return cls


for _cls in (RtMidiBackend, NullMidiBackend):
    register_backend(_cls)


def backend_names() -> Tuple[str, ...]:
    """Every registered backend, preference order first."""
    known = tuple(name for name in PREFERENCE if name in BACKENDS)
    return known + tuple(name for name in BACKENDS if name not in known)


def backend_class(name: str) -> Type[MidiBackend]:
    cls = BACKENDS.get(name)
    if cls is None:
        raise MidiError(
            f"there is no MIDI backend named '{name}' "
            f"(known backends: {', '.join(backend_names())})"
        )
    return cls


def available_backends() -> List[str]:
    """The backends that could be tried on this machine, in preference order."""
    return [name for name in backend_names() if BACKENDS[name].available()]


#: the plan names this simply "available"; it is the same call, under the
#  shorter word the bindings and the File menu actually use.
available = available_backends


def midi_chain(preferred=None, strict: bool = False) -> Tuple[str, ...]:
    """The names to try: what was asked for, then the rest, and always ``null``.

    ``preferred`` may be a name, a class, or a list of either; ``strict`` means
    "this one or nothing" - the chain stops at what was asked for and never
    reaches ``null``.
    """
    names = _as_names(preferred)
    if strict:
        if not names:
            raise MidiError("a strict MIDI request has to name a backend")
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
        if not name or name == MidiBackend.name:
            raise MidiError(
                f"{preferred.__name__} does not say what it is called in the chain"
            )
        return [name]
    if isinstance(preferred, (list, tuple)):
        names = []
        for entry in preferred:
            names.extend(_as_names(entry))
        return names
    raise MidiError(
        f"a MIDI backend must be named as a string or a class, "
        f"got {type(preferred).__name__}"
    )


# -- discovery ----------------------------------------------------------

def list_devices(direction: str = None, backend: str = None) -> List[MidiDeviceInfo]:
    """Every port every usable backend reports, for one direction or both.

    A backend that cannot enumerate (no module, no API) contributes nothing
    instead of failing the call - the same rule as the fallback chain.
    """
    directions = [direction] if direction else list(DIRECTIONS)
    if direction:
        check_direction(direction)
    names = [backend] if backend else list(BACKENDS)
    found: List[MidiDeviceInfo] = []
    for name in names:
        cls = backend_class(name)
        try:
            if not cls.available():
                continue
            for way in directions:
                found.extend(cls.devices(way))
        except MidiError:
            continue
    return found


# -- the fallback chain -------------------------------------------------

def open_backend(log: Callable[[str], None] = None, preferred=None,
                 notes: List[str] = None, strict: bool = False) -> MidiBackend:
    """Open the first backend that works, recording why the others did not.

    With ``strict`` the named backend either opens or :class:`MidiUnavailable`
    is raised listing every attempt - which is what a manifest that says
    ``"optional": false`` on its ``midi`` block asks for.
    """
    log = log or (lambda message: None)
    notes = notes if notes is not None else []
    chain = midi_chain(preferred, strict=strict)

    for name in chain:
        cls = BACKENDS.get(name)
        if cls is None:
            _note(notes, log, f"midi: there is no backend named '{name}' "
                              f"(known: {', '.join(backend_names())})")
            continue
        if not cls.available():
            _note(notes, log, f"midi: {name} is not usable on this machine")
            continue
        try:
            backend = cls(log=log)
        except MidiUnavailable as exc:
            _note(notes, log, f"midi: {name} fell back: {exc}")
            continue
        backend.notes = list(notes)  # type: ignore[attr-defined]
        return backend

    detail = "; ".join(notes)
    raise MidiUnavailable(
        f"no MIDI backend could be opened: {detail}" if detail
        else "no MIDI backend could be opened"
    )


def _note(notes: List[str], log: Callable[[str], None], message: str) -> None:
    notes.append(message)
    log(message)


# -- the script-facing runtime ------------------------------------------

from .runtime import MidiHandler, MidiPort, MidiRuntime  # noqa: E402,F401
