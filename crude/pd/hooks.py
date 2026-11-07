"""libpd hook callbacks and the print line assembler.

Two hard rules, both learned the hard way:

* hook arguments are *transient* - the C strings die as soon as the callback
  returns - so every string is copied (``.decode()``) inside the callback body;
* the print hook emits word *fragments*, not lines, so a buffer assembles them
  until a newline arrives.

Neither callback touches the interpreter: they only append to a ``PdInbox``.
"""

from __future__ import annotations

import ctypes
from typing import List

from .inbox import PdEvent, PdInbox

c_char_p = ctypes.c_char_p
c_int = ctypes.c_int
c_float = ctypes.c_float
P_VOID = ctypes.POINTER(None)

PRINT_HOOK = ctypes.CFUNCTYPE(None, c_char_p)
BANG_HOOK = ctypes.CFUNCTYPE(None, c_char_p)
FLOAT_HOOK = ctypes.CFUNCTYPE(None, c_char_p, c_float)
SYMBOL_HOOK = ctypes.CFUNCTYPE(None, c_char_p, c_char_p)
LIST_HOOK = ctypes.CFUNCTYPE(None, c_char_p, c_int, P_VOID)
MESSAGE_HOOK = ctypes.CFUNCTYPE(None, c_char_p, c_char_p, c_int, P_VOID)
NOTEON_HOOK = ctypes.CFUNCTYPE(None, c_int, c_int, c_int)
CONTROLCHANGE_HOOK = ctypes.CFUNCTYPE(None, c_int, c_int, c_int)
CHANNEL_VALUE_HOOK = ctypes.CFUNCTYPE(None, c_int, c_int)
POLYAFTERTOUCH_HOOK = ctypes.CFUNCTYPE(None, c_int, c_int, c_int)

#: ``kind -> CFUNCTYPE``; the kind is also the key used by ``Libpd.set_hook``.
HOOK_TYPES = {
    "print": PRINT_HOOK,
    "bang": BANG_HOOK,
    "float": FLOAT_HOOK,
    "symbol": SYMBOL_HOOK,
    "list": LIST_HOOK,
    "message": MESSAGE_HOOK,
    "noteon": NOTEON_HOOK,
    "controlchange": CONTROLCHANGE_HOOK,
    "programchange": CHANNEL_VALUE_HOOK,
    "pitchbend": CHANNEL_VALUE_HOOK,
    "aftertouch": CHANNEL_VALUE_HOOK,
    "polyaftertouch": POLYAFTERTOUCH_HOOK,
}


class PrintAssembler:
    """Turn Pd's fragment-wise print hook into whole lines."""

    def __init__(self, max_length: int = 8192):
        self.max_length = max_length
        self._buffer = ""

    def feed(self, fragment: str) -> List[str]:
        """Append a fragment, returning every line it completed."""
        if not fragment:
            return []
        self._buffer += fragment
        if "\n" not in self._buffer:
            if len(self._buffer) > self.max_length:  # refuse to grow forever
                self._buffer = self._buffer[-self.max_length:]
            return []

        *lines, tail = self._buffer.split("\n")
        self._buffer = tail
        return lines

    def flush(self) -> List[str]:
        """Emit whatever is buffered without a trailing newline."""
        if not self._buffer:
            return []
        line, self._buffer = self._buffer, ""
        return [line]

    @property
    def pending(self) -> str:
        return self._buffer


class HookBridge:
    """Owns the CFUNCTYPE instances and forwards everything into an inbox.

    ``attach()`` installs the hooks on the libpd wrapper; the bridge must stay
    referenced for as long as the hooks can fire (ctypes does not keep the
    callback alive by itself).
    """

    def __init__(self, lib, inbox: PdInbox = None):
        self.lib = lib
        self.inbox = inbox if inbox is not None else PdInbox()
        self.printer = PrintAssembler()
        self._callbacks = {}
        self.installed = False

    # -- installation ---------------------------------------------------
    def attach(self) -> "HookBridge":
        self._callbacks = {
            "print": PRINT_HOOK(self._on_print),
            "bang": BANG_HOOK(self._on_bang),
            "float": FLOAT_HOOK(self._on_float),
            "symbol": SYMBOL_HOOK(self._on_symbol),
            "list": LIST_HOOK(self._on_list),
            "message": MESSAGE_HOOK(self._on_message),
            "noteon": NOTEON_HOOK(self._on_noteon),
            "controlchange": CONTROLCHANGE_HOOK(self._on_controlchange),
            "programchange": CHANNEL_VALUE_HOOK(self._on_programchange_value),
            "pitchbend": CHANNEL_VALUE_HOOK(self._on_pitchbend_value),
            "aftertouch": CHANNEL_VALUE_HOOK(self._on_aftertouch_value),
            "polyaftertouch": POLYAFTERTOUCH_HOOK(self._on_polyaftertouch),
        }
        for kind, callback in self._callbacks.items():
            self.lib.set_hook(kind, callback)
        self.installed = True
        return self

    def detach(self) -> None:
        for kind in self._callbacks:
            self.lib.set_hook(kind, None)
        self._callbacks = {}
        self.installed = False

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value).decode("utf-8", "replace")
        return str(value)

    def _atoms(self, argc, argv):
        reader = getattr(self.lib, "atom_read", None)
        if reader is None:
            return ()
        try:
            return tuple(reader(argv, argc))
        except Exception:  # pragma: no cover - a broken atom read must never crash audio
            return ()

    # -- hook bodies (audio thread: copy, enqueue, return) ---------------
    def _on_print(self, fragment):
        for line in self.printer.feed(self._text(fragment)):
            self.inbox.push(PdEvent("print", line))

    def _on_bang(self, recv):
        self.inbox.push(PdEvent("bang", self._text(recv)))

    def _on_float(self, recv, value):
        self.inbox.push(PdEvent("float", self._text(recv), (float(value),)))

    def _on_symbol(self, recv, symbol):
        self.inbox.push(PdEvent("symbol", self._text(recv), (self._text(symbol),)))

    def _on_list(self, recv, argc, argv):
        self.inbox.push(PdEvent("list", self._text(recv), self._atoms(argc, argv)))

    def _on_message(self, recv, msg, argc, argv):
        self.inbox.push(
            PdEvent("message", self._text(recv), (self._text(msg),) + self._atoms(argc, argv))
        )

    def _on_noteon(self, channel, pitch, velocity):
        self.inbox.push(PdEvent("noteon", "", (channel, pitch, velocity)))

    def _on_controlchange(self, channel, controller, value):
        self.inbox.push(PdEvent("controlchange", "", (channel, controller, value)))

    def _on_programchange_value(self, channel, value):
        self.inbox.push(PdEvent("programchange", "", (channel, value)))

    def _on_pitchbend_value(self, channel, value):
        self.inbox.push(PdEvent("pitchbend", "", (channel, value)))

    def _on_aftertouch_value(self, channel, value):
        self.inbox.push(PdEvent("aftertouch", "", (channel, value)))

    def _on_polyaftertouch(self, channel, pitch, value):
        self.inbox.push(PdEvent("polyaftertouch", "", (channel, pitch, value)))
