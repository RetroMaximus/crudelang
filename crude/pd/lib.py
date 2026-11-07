"""Typed ctypes bindings for the vendored ``libs/libpd.dll`` (Pd 0.54).

One table - :data:`SIGNATURES` - carries the full ``argtypes``/``restype`` for
every C function this project calls. Nothing here probes the library with
``getattr``: calling a libpd function without ``argtypes`` can take the whole
process down on Windows (reproduced), so the rule is "declared once, applied
once, tested for completeness".

The DLL also ships a few things we deliberately do not wrap (instance API,
queued hooks, GUI functions); :func:`missing_symbols` reports exactly which of
the *declared* functions a given library file lacks.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Dict, Iterable, List, Optional, Tuple

c_int = ctypes.c_int
c_float = ctypes.c_float
c_double = ctypes.c_double
c_char_p = ctypes.c_char_p
c_void_p = ctypes.c_void_p
P_FLOAT = ctypes.POINTER(ctypes.c_float)
P_DOUBLE = ctypes.POINTER(ctypes.c_double)
P_SHORT = ctypes.POINTER(ctypes.c_short)
P_VOID = ctypes.POINTER(None)

#: ``name -> (restype, [argtype, ...])``. Keep this alphabetical and complete.
SIGNATURES: Dict[str, Tuple[object, List[object]]] = {
    # -- lifecycle ------------------------------------------------------
    "libpd_init": (c_int, []),
    "libpd_init_audio": (c_int, [c_int, c_int, c_int]),
    "libpd_blocksize": (c_int, []),
    # -- patches --------------------------------------------------------
    "libpd_openfile": (c_void_p, [c_char_p, c_char_p]),
    "libpd_closefile": (c_int, [c_void_p]),
    "libpd_getdollarzero": (c_int, [c_void_p]),
    # -- search path ----------------------------------------------------
    "libpd_add_to_search_path": (c_int, [c_char_p]),
    "libpd_clear_search_path": (c_int, []),
    # -- messages -------------------------------------------------------
    "libpd_start_message": (c_int, [c_int]),
    "libpd_add_float": (None, [c_float]),
    "libpd_add_symbol": (None, [c_char_p]),
    "libpd_finish_message": (c_int, [c_char_p, c_char_p]),
    "libpd_finish_list": (c_int, [c_char_p]),
    "libpd_exists": (c_int, [c_char_p]),
    "libpd_bind": (c_int, [c_char_p]),
    "libpd_unbind": (c_int, [c_char_p]),
    "libpd_bang": (c_int, [c_char_p]),
    "libpd_float": (c_int, [c_char_p, c_float]),
    "libpd_symbol": (c_int, [c_char_p, c_char_p]),
    "libpd_list": (c_int, [c_char_p, c_int, P_VOID]),
    "libpd_message": (c_int, [c_char_p, c_char_p, c_int, P_VOID]),
    # -- midi -----------------------------------------------------------
    "libpd_noteon": (c_int, [c_int, c_int, c_int]),
    "libpd_controlchange": (c_int, [c_int, c_int, c_int]),
    "libpd_programchange": (c_int, [c_int, c_int]),
    "libpd_pitchbend": (c_int, [c_int, c_int]),
    "libpd_aftertouch": (c_int, [c_int, c_int]),
    "libpd_polyaftertouch": (c_int, [c_int, c_int, c_int]),
    "libpd_sysex": (c_int, [c_int, c_int]),
    "libpd_sysrealtime": (c_int, [c_int, c_int]),
    "libpd_midibyte": (c_int, [c_int, c_int]),
    # -- arrays ---------------------------------------------------------
    "libpd_read_array": (c_int, [P_FLOAT, c_char_p, c_int, c_int]),
    "libpd_write_array": (c_int, [c_char_p, c_int, P_FLOAT, c_int]),
    "libpd_arraysize": (c_int, [c_char_p]),
    "libpd_resize_array": (c_int, [c_char_p, ctypes.c_long]),
    # -- audio ----------------------------------------------------------
    "libpd_process_float": (c_int, [c_int, P_FLOAT, P_FLOAT]),
    "libpd_process_short": (c_int, [c_int, P_SHORT, P_SHORT]),
    "libpd_process_raw": (c_int, [P_FLOAT, P_FLOAT]),
    # -- hooks (takes a CFUNCTYPE instance) ------------------------------
    "libpd_set_printhook": (None, [c_void_p]),
    "libpd_set_banghook": (None, [c_void_p]),
    "libpd_set_floathook": (None, [c_void_p]),
    "libpd_set_symbolhook": (None, [c_void_p]),
    "libpd_set_listhook": (None, [c_void_p]),
    "libpd_set_messagehook": (None, [c_void_p]),
    "libpd_set_noteonhook": (None, [c_void_p]),
    "libpd_set_controlchangehook": (None, [c_void_p]),
    "libpd_set_programchangehook": (None, [c_void_p]),
    "libpd_set_pitchbendhook": (None, [c_void_p]),
    "libpd_set_aftertouchhook": (None, [c_void_p]),
    "libpd_set_polyaftertouchhook": (None, [c_void_p]),
    # -- atom accessors used from the list/message hooks -----------------
    "libpd_is_float": (c_int, [P_VOID]),
    "libpd_is_symbol": (c_int, [P_VOID]),
    "libpd_get_float": (c_float, [P_VOID]),
    "libpd_get_symbol": (c_char_p, [P_VOID]),
    "libpd_next_atom": (P_VOID, [P_VOID]),
}

HOOK_SETTERS = {
    "print": "libpd_set_printhook",
    "bang": "libpd_set_banghook",
    "float": "libpd_set_floathook",
    "symbol": "libpd_set_symbolhook",
    "list": "libpd_set_listhook",
    "message": "libpd_set_messagehook",
    "noteon": "libpd_set_noteonhook",
    "controlchange": "libpd_set_controlchangehook",
    "programchange": "libpd_set_programchangehook",
    "pitchbend": "libpd_set_pitchbendhook",
    "aftertouch": "libpd_set_aftertouchhook",
    "polyaftertouch": "libpd_set_polyaftertouchhook",
}


class LibpdNotFound(RuntimeError):
    """Raised when no usable ``libpd`` shared library can be located."""


def _candidate_paths() -> List[str]:
    """Search order: env override, repo ``libs/``, PyInstaller bundle, cwd."""
    candidates: List[str] = []

    override = os.environ.get("CRUDE_LIBPD_PATH")
    if override:
        if os.path.isdir(override):
            for name in ("libpd.dll", "libpd.so", "libpd.dylib"):
                candidates.append(os.path.join(override, name))
        else:
            candidates.append(override)

    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))
    bundle = getattr(sys, "_MEIPASS", None)

    roots = [os.path.join(repo, "libs")]
    if bundle:
        roots.extend([os.path.join(bundle, "libs"), bundle])
    roots.append(os.path.join(os.getcwd(), "libs"))

    for root in roots:
        for name in ("libpd.dll", "libpd.so", "libpd.dylib"):
            candidates.append(os.path.join(root, name))

    return candidates


def find_library(path: Optional[str] = None) -> str:
    """Locate the shared library, or raise :class:`LibpdNotFound`."""
    for candidate in ([path] if path else []) + _candidate_paths():
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise LibpdNotFound(
        "libpd not found; set CRUDE_LIBPD_PATH or place libpd.dll in libs/"
    )


def dll_bootstrap(path: Optional[str] = None):
    """Load the library with its sibling DLLs on the search path.

    Returns ``(api, resolved_path)``. The sibling directory (Windows) has to be
    registered first: ``libpd.dll`` links against libstdc++/libwinpthread,
    which Python does not look for by default.
    """
    resolved = find_library(path)
    directory = os.path.dirname(resolved)

    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(directory)
        except OSError:  # pragma: no cover - directory already registered
            pass

    # A bare ``ctypes.CDLL(path)`` still resolves siblings through PATH on some
    # Windows setups, so prepend the directory for good measure.
    os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")

    return ctypes.CDLL(resolved), resolved


def missing_symbols(api) -> List[str]:
    """Names from :data:`SIGNATURES` that ``api`` does not export."""
    missing = []
    for name in SIGNATURES:
        try:
            getattr(api, name)
        except AttributeError:
            missing.append(name)
    return missing


def apply_signatures(api, names: Iterable[str] = None) -> None:
    """Apply the declared types; raises ``AttributeError`` for a missing symbol."""
    for name in (SIGNATURES if names is None else names):
        restype, argtypes = SIGNATURES[name]
        function = getattr(api, name)
        function.restype = restype
        function.argtypes = list(argtypes)


class Libpd:
    """Thin semantic wrapper over the C API: one method per declared function.

    The engine and the tests talk to this interface; :class:`FakeLibpd`
    implements exactly the same methods.
    """

    def __init__(self, api, path: str = "<memory>"):
        self.api = api
        self.path = path
        self._hooks = {}
        self.blocksize_value = 64
        self.dollar_zero_counter = 1000

    # -- construction ---------------------------------------------------
    @classmethod
    def load(cls, path: Optional[str] = None) -> "Libpd":
        api, resolved = dll_bootstrap(path)
        apply_signatures(api)
        missing = missing_symbols(api)
        if missing:
            raise LibpdNotFound(
                f"{os.path.basename(resolved)} is missing required symbols: "
                + ", ".join(sorted(missing))
            )
        return cls(api, resolved)

    # -- lifecycle ------------------------------------------------------
    def init(self) -> int:
        return self.api.libpd_init()

    def init_audio(self, in_channels: int, out_channels: int, sample_rate: int) -> int:
        return self.api.libpd_init_audio(in_channels, out_channels, sample_rate)

    def blocksize(self) -> int:
        return self.api.libpd_blocksize()

    # -- patches --------------------------------------------------------
    def open_file(self, name: str, directory: str):
        return self.api.libpd_openfile(name.encode(), directory.encode())

    def close_file(self, handle) -> int:
        return self.api.libpd_closefile(handle)

    def dollar_zero(self, handle) -> int:
        return self.api.libpd_getdollarzero(handle)

    def add_to_search_path(self, path: str) -> int:
        return self.api.libpd_add_to_search_path(path.encode())

    def clear_search_path(self) -> int:
        return self.api.libpd_clear_search_path()

    # -- messages -------------------------------------------------------
    def start_message(self, length: int) -> int:
        return self.api.libpd_start_message(length)

    def add_float(self, value: float) -> None:
        self.api.libpd_add_float(float(value))

    def add_symbol(self, value: str) -> None:
        self.api.libpd_add_symbol(value.encode())

    def finish_message(self, recv: str, msg: str) -> int:
        return self.api.libpd_finish_message(recv.encode(), msg.encode())

    def finish_list(self, recv: str) -> int:
        return self.api.libpd_finish_list(recv.encode())

    def bind(self, recv: str) -> int:
        return self.api.libpd_bind(recv.encode())

    def unbind(self, recv: str) -> int:
        return self.api.libpd_unbind(recv.encode())

    def exists(self, recv: str) -> int:
        return self.api.libpd_exists(recv.encode())

    def bang(self, recv: str) -> int:
        return self.api.libpd_bang(recv.encode())

    def float(self, recv: str, value: float) -> int:
        return self.api.libpd_float(recv.encode(), float(value))

    def symbol(self, recv: str, value: str) -> int:
        return self.api.libpd_symbol(recv.encode(), value.encode())

    # -- midi -----------------------------------------------------------
    def noteon(self, channel: int, pitch: int, velocity: int) -> int:
        return self.api.libpd_noteon(channel, pitch, velocity)

    def noteoff(self, channel: int, pitch: int) -> int:
        # libpd 0.54 has no libpd_noteoff: a zero velocity is the note-off.
        return self.api.libpd_noteon(channel, pitch, 0)

    def controlchange(self, channel: int, controller: int, value: int) -> int:
        return self.api.libpd_controlchange(channel, controller, value)

    def programchange(self, channel: int, value: int) -> int:
        return self.api.libpd_programchange(channel, value)

    def pitchbend(self, channel: int, value: int) -> int:
        return self.api.libpd_pitchbend(channel, value)

    def aftertouch(self, channel: int, value: int) -> int:
        return self.api.libpd_aftertouch(channel, value)

    def polyaftertouch(self, channel: int, pitch: int, value: int) -> int:
        return self.api.libpd_polyaftertouch(channel, pitch, value)

    def sysex(self, port: int, byte: int) -> int:
        return self.api.libpd_sysex(port, byte)

    def sysrealtime(self, port: int, byte: int) -> int:
        return self.api.libpd_sysrealtime(port, byte)

    # -- arrays ---------------------------------------------------------
    def read_array(self, name: str, offset: int = 0, count: int = None) -> List[float]:
        size = self.array_size(name)
        if size < 0:
            return []
        if count is None:
            count = max(size - offset, 0)
        if offset < 0 or count < 0 or offset + count > size:
            raise ValueError(
                f"array '{name}': offset {offset} + count {count} exceeds size {size}"
            )
        buffer = (c_float * max(count, 1))()
        status = self.api.libpd_read_array(buffer, name.encode(), offset, count)
        if status != 0:
            raise ValueError(f"array '{name}': read failed with status {status}")
        return [buffer[i] for i in range(count)]

    def write_array(self, name: str, offset: int, values: Iterable[float]) -> int:
        values = [float(v) for v in values]
        buffer = (c_float * max(len(values), 1))(*values)
        return self.api.libpd_write_array(name.encode(), offset, buffer, len(values))

    def array_size(self, name: str) -> int:
        return self.api.libpd_arraysize(name.encode())

    def resize_array(self, name: str, size: int) -> int:
        return self.api.libpd_resize_array(name.encode(), int(size))

    # -- audio ----------------------------------------------------------
    def process_float(self, ticks: int, in_buffer, out_buffer) -> int:
        return self.api.libpd_process_float(ticks, in_buffer, out_buffer)

    # -- hooks ----------------------------------------------------------
    def set_hook(self, kind: str, callback) -> None:
        """Install a hook. ``callback`` is a CFUNCTYPE instance (or ``None``)."""
        setter = HOOK_SETTERS[kind]
        if callback is None:
            getattr(self.api, setter)(None)
            self._hooks.pop(kind, None)
            return
        # The wrapper must outlive the call: keep a reference for the process.
        self._hooks[kind] = callback
        getattr(self.api, setter)(ctypes.cast(callback, c_void_p))

    def has_hook(self, kind: str) -> bool:
        return kind in self._hooks

    # -- atoms (only valid inside list/message hooks) --------------------
    def atom_read(self, atoms, argc: int) -> List:
        values = []
        atom = atoms
        for _ in range(argc):
            if self.api.libpd_is_float(atom):
                values.append(self.api.libpd_get_float(atom))
            elif self.api.libpd_is_symbol(atom):
                symbol = self.api.libpd_get_symbol(atom)
                values.append(symbol.decode() if symbol else "")
            else:
                values.append(None)
            atom = self.api.libpd_next_atom(atom)
        return values

    def __repr__(self):
        return f"<Libpd {self.path}>"
