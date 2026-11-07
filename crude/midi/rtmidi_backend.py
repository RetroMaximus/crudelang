"""The MIDI engine's only real backend: ``python-rtmidi``, imported lazily.

This is the only module in the package that touches a device library, and it
touches it inside the methods that need it - the same rule
:mod:`crude.audio.sd_backend` follows for ``sounddevice``, so importing
:mod:`crude.midi` never fails on a machine that does not have it, and a test
can put a stub in ``sys.modules["rtmidi"]`` and drive ``MidiIn``/``MidiOut`` by
hand without a controller plugged in.

``rtmidi``'s input callback runs on its own native thread, exactly like
PortAudio's does for audio, so nothing here ever calls script code from that
thread: the callback only puts ``(stamp, packet)`` on a queue, and
:meth:`RtMidiBackend.poll` - called on the interpreter thread, once per frame,
by :class:`~crude.midi.runtime.MidiRuntime` - is what runs the callback that was
handed to ``open_input``.
"""

from __future__ import annotations

import queue
import sys
import threading
from typing import Callable, Dict, List, Tuple

from .base import (
    MidiBackend,
    MidiDeviceInfo,
    MidiError,
    MidiMessage,
    MidiUnavailable,
    check_direction,
)

MODULE = "rtmidi"


def module():
    """Import ``rtmidi``, or explain why it cannot be used here.

    ``python-rtmidi`` ships no guaranteed wheel for every Python (see
    ``docs/build.md``); a machine that cannot install it is the ordinary case
    this layer is built for, not an exceptional one, so this raises the
    "unavailable" error and never a raw ``ImportError``.
    """
    try:
        import rtmidi  # noqa: F401 - the import is the point, then bound
    except ImportError as exc:
        raise MidiUnavailable(f"python-rtmidi is not installed: {exc}")
    except OSError as exc:
        # the extension module itself loads but the system MIDI API behind it
        # is missing (no ALSA on a slim Linux image, say)
        raise MidiUnavailable(f"rtmidi has no system MIDI API to talk to: {exc}")
    return sys.modules[MODULE]


class RtMidiBackend(MidiBackend):
    """``python-rtmidi``'s ``MidiIn``/``MidiOut`` behind the layer's own verbs."""

    name = "rtmidi"
    live = True

    def __init__(self, log: Callable[[str], None] = None):
        self.log = log or (lambda message: None)
        #: ``port_id -> (MidiIn, queue, callback)`` for every open input
        self._inputs: Dict[int, Tuple[object, "queue.Queue", Callable]] = {}
        #: ``port_id -> MidiOut``
        self._outputs: Dict[int, object] = {}
        self._next_id = 0
        self._lock = threading.RLock()

    # -- capability -----------------------------------------------------
    @classmethod
    def available(cls) -> bool:
        try:
            module()
        except MidiUnavailable:
            return False
        return True

    @classmethod
    def devices(cls, direction: str) -> List[MidiDeviceInfo]:
        check_direction(direction)
        rt = module()
        maker = rt.MidiIn if direction == "input" else rt.MidiOut
        try:
            probe = maker()
        except Exception as exc:  # pragma: no cover - the library itself failing
            raise MidiUnavailable(
                f"rtmidi could not open an API to list {direction} ports: {exc}"
            )
        try:
            names = list(probe.get_ports())
            api = str(probe.get_current_api())
        except Exception as exc:
            raise MidiUnavailable(
                f"rtmidi could not list {direction} ports: {exc}"
            )
        finally:
            try:
                probe.close_port()
            except Exception:  # pragma: no cover - a probe that will not close
                pass
            del probe
        return [
            MidiDeviceInfo(name=str(title), index=index, direction=direction, api=api)
            for index, title in enumerate(names)
        ]

    def _new_id(self) -> int:
        with self._lock:
            self._next_id += 1
            return self._next_id

    # -- the ports ------------------------------------------------------
    def open_input(self, index: int,
                   callback: Callable[[MidiMessage], None]) -> int:
        rt = module()
        try:
            port = rt.MidiIn()
            port.open_midiport(int(index))
        except ValueError as exc:
            raise MidiError(f"there is no MIDI input port {index}") from exc
        except Exception as exc:
            raise MidiUnavailable(f"rtmidi could not open input {index}: {exc}")

        messages: "queue.Queue" = queue.Queue()

        def _on_message(stamp, packet):
            # runs on rtmidi's own thread: enqueue only, never touch the script
            messages.put((stamp, packet))

        port.set_callback(_on_message)
        port_id = self._new_id()
        with self._lock:
            self._inputs[port_id] = (port, messages, callback)
        return port_id

    def poll(self, port_id: int) -> None:
        """Hand everything queued since the last call to this input's callback.

        Runs on the interpreter thread, from the runtime's per-frame dispatch -
        that is the whole reason a queue sits between ``_on_message`` and the
        script's ``ON_MIDI`` function.
        """
        entry = self._inputs.get(port_id)
        if entry is None:
            return
        _port, messages, callback = entry
        while True:
            try:
                stamp, packet = messages.get_nowait()
            except queue.Empty:
                return
            message = MidiMessage(
                bytes=tuple(int(byte) & 0xFF for byte in packet),
                delta=float(stamp or 0.0),
                port=port_id,
            )
            callback(message)

    def open_output(self, index: int) -> int:
        rt = module()
        try:
            port = rt.MidiOut()
            port.open_midiport(int(index))
        except ValueError as exc:
            raise MidiError(f"there is no MIDI output port {index}") from exc
        except Exception as exc:
            raise MidiUnavailable(f"rtmidi could not open output {index}: {exc}")
        port_id = self._new_id()
        with self._lock:
            self._outputs[port_id] = port
        return port_id

    def send(self, port_id: int, message: Tuple[int, ...]) -> int:
        port = self._outputs.get(port_id)
        if port is None:
            raise MidiError(f"there is no open MIDI output port {port_id}")
        packet = [int(byte) & 0xFF for byte in message]
        try:
            port.send_message(packet)
        except Exception as exc:
            raise MidiError(f"rtmidi could not send to port {port_id}: {exc}")
        return len(packet)

    def close(self, port_id: int = None) -> None:
        with self._lock:
            ids = ([port_id] if port_id is not None
                   else list(self._inputs) + list(self._outputs))
            for key in ids:
                if key in self._inputs:
                    port, _messages, _callback = self._inputs.pop(key)
                    try:
                        port.cancel_callback()
                        port.close_port()
                    except Exception as exc:  # pragma: no cover - a port that fights
                        self.log(f"rtmidi: closing input {key} raised {exc!r}")
                elif key in self._outputs:
                    port = self._outputs.pop(key)
                    try:
                        port.close_port()
                    except Exception as exc:  # pragma: no cover
                        self.log(f"rtmidi: closing output {key} raised {exc!r}")

    def describe(self) -> str:
        return (f"midi rtmidi: {len(self._inputs)} input(s), "
                f"{len(self._outputs)} output(s)")


__all__ = ["RtMidiBackend", "module", "MODULE"]
