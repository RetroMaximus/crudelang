"""An in-memory libpd: same interface as :class:`crude.pd.lib.Libpd`.

Everything the engine and the bindings rely on is modelled here, so the whole
Pd surface is testable without a DLL, a device or a patch file:

* receivers are a set; sending to a bound receiver is recorded in ``sent``;
* arrays are plain lists of floats;
* hooks are stored and can be *fired* with :meth:`FakeLibpd.emit`, which is how
  a test simulates "Pd sent us a bang";
* ``process_float`` records the call and writes silence (or the callbacks'
  contribution) into the output buffer.

Loading a patch that really is on disk also *declares* what that patch declares
- ``[r foo]`` becomes a receiver, a ``[vu 15 15 foo label ...]`` binds ``foo`` in
its receive slot, ``table $0-wave 16`` becomes a 16-sample array named after this
patch's dollar zero - because that is exactly what libpd does, and without it a
test could never tell an authored patch from an empty one. Fictional paths (the
common case in engine tests) just open an empty patch.
"""

from __future__ import annotations

import ctypes
import os
from typing import Dict, List, Optional


#: Objects whose argument is a receiver name, for :meth:`FakeLibpd._declare_patch`.
_RECEIVER_OBJECTS = ("r", "receive")


def _expand(name: str, dollar_zero: int) -> str:
    """``$0-wave`` -> ``1003-wave`` - what Pd itself stores as the symbol."""
    if name.startswith("$0-"):
        return f"{dollar_zero}-{name[3:]}"
    return name


def _gui_receiver(atoms: List[str]) -> str:
    """The name a GUI box binds: ``[vu 15 15 tick beat ...]`` listens for "tick".

    A vanilla iemgui box keeps its send, receive and label as ordered atoms, but
    where they sit differs by kind - a ``[vu]`` has no send slot at all, a
    ``[tgl]`` puts its side first - so the position is read out of
    :mod:`crude.pd.pdgui`, the one table that knows a box's shape, rather than
    counted from here. ``empty`` is Pd's own spelling of "no name", and a box
    this table has no template for declares nothing.
    """
    from .pdgui import EMPTY, GuiError, slots  # local: the fake stays import-light

    if not atoms:
        return ""
    try:
        held = slots(atoms[0])
    except GuiError:
        return ""
    for index, slot in enumerate(held):
        if slot.name == "recv":
            # the object's own name is atom zero, so slot ``i`` is atom ``i + 1``
            said = atoms[index + 1] if index + 1 < len(atoms) else EMPTY
            return "" if said == EMPTY else said
    return ""


def _receiver_of(box) -> str:
    """The name a box makes Pd listen for: ``[r foo]``, or an atom's receive slot."""
    if box.recv:
        return box.recv
    if box.kind == "obj":
        atoms = box.text.split()
        if len(atoms) > 1 and atoms[0] in _RECEIVER_OBJECTS:
            return atoms[1]
        return _gui_receiver(atoms)
    return ""


class FakeLibpd:
    def __init__(self, blocksize: int = 64, accept_unknown_files: bool = True):
        self.blocksize_value = blocksize
        self.accept_unknown_files = accept_unknown_files
        self.path = "<fake>"

        self.initialised = False
        self.audio_format = None
        self.hooks = {}
        self.bound = set()
        self.receivers = set()
        self.files: Dict[tuple, object] = {}
        self.search_paths: List[str] = []
        self.arrays: Dict[str, List[float]] = {}
        self.dollar_zero_counter = 1000
        self._dollar_zero: Dict[object, int] = {}

        #: every message we delivered to Pd, in order
        self.sent: List[tuple] = []
        self.midi: List[tuple] = []
        self.process_calls: List[int] = []
        self.message_buffer: List = []
        self.message_size = 0
        self.atoms: tuple = ()
        self.on_process = None

    # -- test helpers ----------------------------------------------------
    def add_array(self, name: str, values) -> None:
        self.arrays[name] = [float(v) for v in values]

    def add_receiver(self, name: str) -> None:
        """Pretend a loaded patch contains ``[r name]``."""
        self.receivers.add(name)

    def emit(self, kind: str, recv: str = "", *args) -> bool:
        """Fire one of the installed hooks, as libpd would from Pd.

        Atoms are only valid while the hook runs, so - like libpd - the fake
        parks them and :meth:`atom_read` picks them up from inside the callback.
        """
        hook = self.hooks.get(kind)
        if hook is None:
            return False
        if kind == "print":
            hook(str(recv).encode())
        elif kind == "bang":
            hook(str(recv).encode())
        elif kind == "float":
            hook(str(recv).encode(), float(args[0]))
        elif kind == "symbol":
            hook(str(recv).encode(), str(args[0]).encode())
        elif kind == "list":
            self.atoms = tuple(args)
            hook(str(recv).encode(), len(args), None)
        elif kind == "message":
            self.atoms = tuple(args[1:])
            hook(str(recv).encode(), str(args[0]).encode(), len(args) - 1, None)
        else:  # midi-style hooks take plain ints
            hook(*[int(a) for a in ((recv,) + args) if a != ""])
        return True

    def sent_kinds(self):
        return [entry[0] for entry in self.sent]

    # -- lifecycle -------------------------------------------------------
    def init(self) -> int:
        self.initialised = True
        return 0

    def init_audio(self, in_channels: int, out_channels: int, sample_rate: int) -> int:
        self.audio_format = (in_channels, out_channels, sample_rate)
        return 0

    def blocksize(self) -> int:
        return self.blocksize_value

    # -- patches ---------------------------------------------------------
    def open_file(self, name: str, directory: str):
        if not self.accept_unknown_files and (name, directory) not in self.files:
            return None
        handle = self.files.get((name, directory))
        if handle is None:
            handle = object()
            self.files[(name, directory)] = handle
            self._dollar_zero[handle] = self._next_dollar_zero()
            self._declare_patch(os.path.join(directory, name), self._dollar_zero[handle])
        return handle

    def close_file(self, handle) -> int:
        for key, value in list(self.files.items()):
            if value is handle:
                del self.files[key]
                self._dollar_zero.pop(handle, None)
                return 0
        return -1

    def dollar_zero(self, handle) -> int:
        """Stable per patch, like ``libpd_getdollarzero``."""
        if handle not in self._dollar_zero:
            self._dollar_zero[handle] = self._next_dollar_zero()
        return self._dollar_zero[handle]

    def _next_dollar_zero(self) -> int:
        self.dollar_zero_counter += 1
        return self.dollar_zero_counter

    def _declare_patch(self, path: str, dollar_zero: int) -> None:
        """Give a patch on disk the receivers and tables it declares.

        A GUI box counts as a declarer exactly as it does in Pd: the name in its
        receive slot is bound the moment the canvas is opened, which is what lets
        a test of an emitted front panel ask whether the twin is wired at all.
        """
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as stream:
                text = stream.read()
        except OSError:
            return

        from .reader import parse_patch  # local: the fake stays import-light

        doc = parse_patch(text, os.path.basename(path))
        for box in doc.boxes:
            declared = _receiver_of(box)
            if declared:
                self.receivers.add(_expand(declared, dollar_zero))
            elif box.kind == "table":
                name = _expand(box.table_name, dollar_zero)
                self.arrays.setdefault(name, list(box.init) or [0.0] * box.size)

    def add_to_search_path(self, path: str) -> int:
        self.search_paths.append(path)
        return 0

    def clear_search_path(self) -> int:
        self.search_paths.clear()
        return 0

    # -- messages --------------------------------------------------------
    def start_message(self, length: int) -> int:
        self.message_buffer = []
        self.message_size = length
        return 0

    def add_float(self, value: float) -> None:
        self.message_buffer.append(float(value))

    def add_symbol(self, value: str) -> None:
        self.message_buffer.append(str(value))

    def finish_message(self, recv: str, msg: str) -> int:
        self.sent.append(("message", recv, msg, tuple(self.message_buffer)))
        self.message_buffer = []
        return 0 if recv in self.bound else 0

    def finish_list(self, recv: str) -> int:
        self.sent.append(("list", recv, tuple(self.message_buffer)))
        self.message_buffer = []
        return 0

    def bind(self, recv: str) -> int:
        self.bound.add(recv)
        return 0

    def unbind(self, recv: str) -> int:
        self.bound.discard(recv)
        return 0

    def exists(self, recv: str) -> int:
        return 1 if recv in self.bound or recv in self.receivers else 0

    def bang(self, recv: str) -> int:
        self.sent.append(("bang", recv))
        return 0

    def float(self, recv: str, value: float) -> int:
        self.sent.append(("float", recv, float(value)))
        return 0

    def symbol(self, recv: str, value: str) -> int:
        self.sent.append(("symbol", recv, str(value)))
        return 0

    # -- midi ------------------------------------------------------------
    def _midi(self, kind, *args):
        self.midi.append((kind,) + args)
        return 0

    def noteon(self, channel, pitch, velocity):
        return self._midi("noteon", channel, pitch, velocity)

    def noteoff(self, channel, pitch):
        return self._midi("noteon", channel, pitch, 0)

    def controlchange(self, channel, controller, value):
        return self._midi("controlchange", channel, controller, value)

    def programchange(self, channel, value):
        return self._midi("programchange", channel, value)

    def pitchbend(self, channel, value):
        return self._midi("pitchbend", channel, value)

    def aftertouch(self, channel, value):
        return self._midi("aftertouch", channel, value)

    def polyaftertouch(self, channel, pitch, value):
        return self._midi("polyaftertouch", channel, pitch, value)

    def sysex(self, port, byte):
        return self._midi("sysex", port, byte)

    def sysrealtime(self, port, byte):
        return self._midi("sysrealtime", port, byte)

    # -- arrays ----------------------------------------------------------
    def read_array(self, name: str, offset: int = 0, count: int = None) -> List[float]:
        values = self.arrays.get(name)
        if values is None:
            return []
        if count is None:
            count = max(len(values) - offset, 0)
        return list(values[offset:offset + count])

    def write_array(self, name: str, offset: int, values) -> int:
        values = [float(v) for v in values]
        target = self.arrays.get(name)
        if target is None:
            return -1
        if offset < 0 or offset + len(values) > len(target):
            return -1
        target[offset:offset + len(values)] = values
        return 0

    def array_size(self, name: str) -> int:
        values = self.arrays.get(name)
        return -1 if values is None else len(values)

    def resize_array(self, name: str, size: int) -> int:
        values = self.arrays.setdefault(name, [])
        if size < len(values):
            del values[size:]
        else:
            values.extend([0.0] * (size - len(values)))
        return 0

    # -- audio -----------------------------------------------------------
    def process_float(self, ticks: int, in_buffer=None, out_buffer=None) -> int:
        self.process_calls.append(ticks)
        if self.on_process is not None:
            self.on_process(ticks, in_buffer, out_buffer)
        if out_buffer is not None and hasattr(out_buffer, "__len__"):
            for index in range(len(out_buffer)):
                out_buffer[index] = 0.0
        return 0

    # -- hooks -----------------------------------------------------------
    def set_hook(self, kind: str, callback) -> None:
        if callback is None:
            self.hooks.pop(kind, None)
        else:
            self.hooks[kind] = callback

    def has_hook(self, kind: str) -> bool:
        return kind in self.hooks

    def atom_read(self, atoms, argc: int) -> List:
        """Atoms of the list/message hook currently firing (libpd-like)."""
        return list(self.atoms)[:argc]

    def __repr__(self):
        return f"<FakeLibpd bound={sorted(self.bound)} arrays={sorted(self.arrays)}>"
