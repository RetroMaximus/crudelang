"""The no-MIDI backend: what every test and every ``--no-midi`` run lands on.

Same shape as the audio layer's ``NullBackend``: it is always "available", it
always has zero devices, and nothing opened against it is an error worth
crashing on - so a script written against a real controller runs unchanged on a
machine that has none, and a test never needs ``python-rtmidi`` importable at
all. An output opened anyway records what was "sent" so a test can read it
back, exactly like the null audio stream records its blocks.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from .base import (
    MidiBackend,
    MidiDeviceInfo,
    MidiError,
    MidiMessage,
    MidiUnavailable,
)


class NullMidiBackend(MidiBackend):
    """A backend with no ports to open, and no library to fail without."""

    name = "null"
    live = False

    def __init__(self, log: Callable[[str], None] = None):
        self.log = log or (lambda message: None)
        #: ``(port_id, packet)`` for everything ever sent, so a test can read
        #  what a script "played" without a synth attached
        self.sent: List[Tuple[int, Tuple[int, ...]]] = []
        self._outputs: Dict[int, int] = {}
        self._next_id = 0

    # -- capability -----------------------------------------------------
    @classmethod
    def available(cls) -> bool:
        return True

    @classmethod
    def devices(cls, direction: str) -> List[MidiDeviceInfo]:
        return []

    # -- the ports ------------------------------------------------------
    def open_input(self, index: int,
                   callback: Callable[[MidiMessage], None]) -> int:
        raise MidiUnavailable("the null MIDI backend has no input ports to open")

    def open_output(self, index: int) -> int:
        self._next_id += 1
        self._outputs[self._next_id] = index
        return self._next_id

    def send(self, port_id: int, message: Tuple[int, ...]) -> int:
        if port_id not in self._outputs:
            raise MidiError(f"there is no open MIDI output port {port_id}")
        packet = tuple(int(byte) & 0xFF for byte in message)
        self.sent.append((port_id, packet))
        return len(packet)

    def close(self, port_id: int = None) -> None:
        if port_id is None:
            self._outputs.clear()
        else:
            self._outputs.pop(port_id, None)

    def describe(self) -> str:
        return "midi null (silent): no ports"


__all__ = ["NullMidiBackend"]
