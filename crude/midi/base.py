"""MIDI: the vocabulary every backend has to answer to, and nothing else.

Nothing here imports ``rtmidi``. That lives in :mod:`crude.midi.rtmidi_backend`
and is looked up lazily, inside the methods that need it, so importing this
package works on a machine with no MIDI library at all - the same rule
:mod:`crude.audio` follows for ``sounddevice`` and ``pyaudio``.

A :class:`MidiBackend` is deliberately smaller than the library it wraps:
enumerate one direction, open one port, send one packet, close. Everything
above that - routing a message to a script's ``ON_MIDI`` handler, muting a
handler that keeps raising - belongs to
:class:`crude.midi.runtime.MidiRuntime`, not to a backend, so a second engine
(a file-based one, a WebMIDI bridge) only has to answer the five verbs below.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Callable, List, Tuple


class MidiError(RuntimeError):
    """A port or message problem the bindings turn into a script error."""


class MidiUnavailable(MidiError):
    """This backend could not be used here: no module, no API, no permission.

    The same rule the audio layer runs on - a missing MIDI library is a note,
    not a failure - so :func:`crude.midi.open_backend` treats this the way
    ``crude.audio.open_backend`` treats an :class:`~crude.audio.AudioUnavailable`:
    try the next thing in the chain, and land on
    :class:`~crude.midi.null_backend.NullMidiBackend` if there is nothing else.
    """


#: which way a port runs - the same words the audio layer uses for a device.
DIRECTIONS = ("input", "output")


def check_direction(direction: str) -> str:
    if direction not in DIRECTIONS:
        raise MidiError(
            f"a MIDI direction must be one of {', '.join(DIRECTIONS)}, "
            f"got '{direction}'"
        )
    return direction


@dataclass
class MidiDeviceInfo:
    """One port a backend reports, in the shape ``MIDI_DEVICES`` gives back."""

    name: str
    index: int
    direction: str
    api: str = ""

    def describe(self) -> str:
        detail = f"{self.direction}"
        if self.api:
            detail += f", {self.api}"
        return f"[{self.index}] {self.name} ({detail})"

    def __str__(self) -> str:
        return self.describe()


@dataclass
class MidiMessage:
    """One raw MIDI packet: the status byte and whatever data bytes follow it.

    Nothing here decodes a message into "note 60 on channel 2" - that is the
    script's job (or a future binding's); this only carries the bytes and the
    two properties almost every decoder starts from, ``command`` and ``channel``.
    """

    bytes: Tuple[int, ...]
    delta: float = 0.0
    port: int = None

    @property
    def status(self) -> int:
        return self.bytes[0] if self.bytes else 0

    @property
    def command(self) -> int:
        """The status byte's high nibble: note on/off, controller, and so on."""
        return self.status & 0xF0

    @property
    def channel(self) -> int:
        """The low nibble, 0-15; ``-1`` for a system message, which has none."""
        status = self.status
        if status >= 0xF0:
            return -1
        return status & 0x0F

    def describe(self) -> str:
        return " ".join(f"{byte:02X}" for byte in self.bytes)

    def __str__(self) -> str:
        return self.describe()


class MidiBackend(abc.ABC):
    """The smallest surface a MIDI library has to answer to."""

    #: what a manifest or ``MIDI_OPEN`` would call this backend in the chain
    name = ""
    #: does opening this backend ever reach real hardware
    live = True

    @classmethod
    @abc.abstractmethod
    def available(cls) -> bool:
        """Could this backend be tried on this machine at all?"""

    @classmethod
    @abc.abstractmethod
    def devices(cls, direction: str) -> List[MidiDeviceInfo]:
        """Every port this library sees, one direction at a time."""

    @abc.abstractmethod
    def open_input(self, index: int,
                   callback: Callable[["MidiMessage"], None]) -> int:
        """Start reading port ``index``; every message goes to ``callback``.

        Returns the id this backend uses for the port everywhere else
        (``poll``, ``send``, ``close``). A backend whose library calls back on
        its *own* thread must not run ``callback`` there - it has to queue the
        message and hand it over from :meth:`poll` instead, so the runtime
        never runs script code on a thread it does not own.
        """

    @abc.abstractmethod
    def open_output(self, index: int) -> int:
        """Open ``index`` for sending; the port id ``send`` takes back."""

    @abc.abstractmethod
    def send(self, port_id: int, message: Tuple[int, ...]) -> int:
        """Write one packet to an open output port; how many bytes went out."""

    @abc.abstractmethod
    def close(self, port_id: int = None) -> None:
        """Close one port, or every port this backend has open."""

    def poll(self, port_id: int) -> None:
        """Hand whatever arrived since the last call to the input's callback.

        Called on the interpreter thread, once per frame, by
        :meth:`crude.midi.runtime.MidiRuntime.dispatch`. A backend whose
        library already delivers on its own thread (and so had to queue)
        overrides this; one that has no ports at all leaves the no-op here.
        """
        return None

    def describe(self) -> str:
        return f"midi {self.name or type(self).__name__}"

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"


__all__ = [
    "MidiError",
    "MidiUnavailable",
    "DIRECTIONS",
    "check_direction",
    "MidiDeviceInfo",
    "MidiMessage",
    "MidiBackend",
]
