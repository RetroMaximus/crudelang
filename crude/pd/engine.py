"""The Pd session: patch lifecycle, the job scheduler and the audio tick.

Everything an audio callback is allowed to do goes through
:class:`PdScheduler`: the callback drains queued jobs into libpd and then calls
``process_float``. Everything that changes libpd's configuration (opening a
patch, binding a receiver, switching DSP) goes through
:meth:`PdSession.control`, which pauses the stream and - in strict mode -
asserts it is running on the thread that owns the session.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Dict, List, NamedTuple, Optional

from .hooks import HookBridge
from .inbox import PdEvent, PdInbox

#: Jobs the scheduler coalesces per ``(kind, patch, recv)``: a knob drag must
#: cost one message per block, not one per UI frame.
COALESCED_KINDS = ("float", "bang", "symbol")

#: Job kinds that map one-to-one onto a MIDI call on the libpd wrapper. These
#: are never coalesced - a note-on immediately followed by a note-off has to
#: stay two messages.
MIDI_KINDS = (
    "noteon",
    "noteoff",
    "controlchange",
    "programchange",
    "pitchbend",
    "aftertouch",
    "polyaftertouch",
    "sysex",
    "sysrealtime",
)


class PdJob(NamedTuple):
    kind: str
    recv: str
    args: tuple
    patch_id: Optional[str] = None


class PdError(RuntimeError):
    """A Pd-level failure that the bindings translate into a script error."""


class PdPatch:
    """One open patch: its handle, its ``$0``, and its arrays."""

    def __init__(self, patch_id, name, directory, handle, dollar_zero):
        self.id = patch_id
        self.name = name
        self.directory = directory
        self.handle = handle
        self.dollar_zero = dollar_zero
        self.arrays: Dict[str, str] = {}

    # -- $0 handling ----------------------------------------------------
    def resolve_receiver(self, recv: str) -> str:
        """``$0-foo`` -> ``<dollar zero>-foo``; anything else is taken as written."""
        if recv and recv.startswith("$0-"):
            return f"{self.dollar_zero}-{recv[3:]}"
        return recv

    def resolve_name(self, name: str) -> str:
        """Map a script-visible array name to the name Pd knows.

        Authored patches declare tables as ``$0-<name>``; scripts keep saying
        ``wave`` and this is the one place that inserts the dollar zero.
        """
        pd_name = self.arrays.get(name, name)
        return self.resolve_receiver(pd_name)

    def register_array(self, name: str, pd_name: str = None) -> str:
        """Remember how ``name`` is spelled inside Pd; returns the resolved name."""
        self.arrays[name] = pd_name or (name if name.startswith("$0-") else f"$0-{name}")
        return self.resolve_name(name)

    @property
    def dollar_zero_name(self) -> str:
        return f"$0={self.dollar_zero}"

    def __repr__(self):
        return f"<PdPatch {self.id} $0={self.dollar_zero}>"


class PdScheduler:
    """A tiny bounded queue of data-plane jobs with per-key coalescing."""

    def __init__(self, capacity: int = 512):
        self.capacity = capacity
        self._order: List[PdJob] = []
        self._index: Dict[tuple, int] = {}
        self.dropped = 0
        self.executed = 0

    def submit(self, kind: str, recv: str, args: tuple = (), patch_id=None) -> bool:
        key = (kind, patch_id, recv)

        if kind in COALESCED_KINDS:
            position = self._index.get(key)
            if position is not None:
                self._order[position] = PdJob(kind, recv, args, patch_id)
                return True

        if len(self._order) >= self.capacity:
            self.dropped += 1
            return False

        self._order.append(PdJob(kind, recv, args, patch_id))
        if kind in COALESCED_KINDS:
            self._index[key] = len(self._order) - 1
        return True

    def drain(self) -> List[PdJob]:
        jobs = self._order
        self._order = []
        self._index = {}
        return jobs

    def apply(self, lib) -> int:
        """Execute every queued job against ``lib``; returns how many ran."""
        jobs = self.drain()
        for job in jobs:
            if job.kind == "float":
                lib.float(job.recv, job.args[0])
            elif job.kind == "bang":
                lib.bang(job.recv)
            elif job.kind == "symbol":
                lib.symbol(job.recv, job.args[0])
            elif job.kind == "list":
                lib.start_message(len(job.args))
                for value in job.args:
                    self._add(lib, value)
                lib.finish_list(job.recv)
            elif job.kind == "message":
                message, values = job.args[0], job.args[1:]
                lib.start_message(len(values))
                for value in values:
                    self._add(lib, value)
                lib.finish_message(job.recv, message)
            elif job.kind in MIDI_KINDS:
                getattr(lib, job.kind)(*job.args)
            else:  # pragma: no cover - guarded by the bindings
                raise PdError(f"unknown Pd job kind '{job.kind}'")
        self.executed += len(jobs)
        return len(jobs)

    @staticmethod
    def _add(lib, value):
        if isinstance(value, str):
            lib.add_symbol(value)
        else:
            lib.add_float(float(value))

    @property
    def pending(self) -> int:
        return len(self._order)

    def clear(self) -> None:
        self._order = []
        self._index = {}

    def __len__(self):
        return len(self._order)


class PdSession:
    """Owns one libpd instance for the lifetime of a program."""

    def __init__(self, lib, sample_rate: int = 44100, in_channels: int = 2,
                 out_channels: int = 2, inbox: PdInbox = None):
        self.lib = lib
        self.sample_rate = sample_rate
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.inbox = inbox if inbox is not None else PdInbox()
        self.hooks = HookBridge(lib, self.inbox)
        self.scheduler = PdScheduler()
        self.patches: Dict[str, PdPatch] = {}
        self.bound_receivers: List[str] = []
        self.blocksize_value = 64
        self.dsp_on = False
        self.started = False
        self.owner_thread = threading.get_ident()
        self.strict_threads = bool(os.environ.get("CRUDE_PD_STRICT_THREADS"))
        self._pauser = None

    # -- lifecycle ------------------------------------------------------
    def set_pauser(self, pauser) -> None:
        """``pauser(paused: bool)`` - the audio backend's pause switch."""
        self._pauser = pauser

    def start(self) -> "PdSession":
        self.lib.init()
        self.lib.init_audio(self.in_channels, self.out_channels, self.sample_rate)
        self.blocksize_value = self.lib.blocksize() or 64
        self.hooks.attach()
        self.started = True
        return self

    def shutdown(self) -> None:
        self.scheduler.clear()
        self.hooks.detach()
        for patch_id in list(self.patches):
            self.close_patch(patch_id)
        for recv in list(self.bound_receivers):
            self.unbind(recv)
        self.started = False

    def reconfigure(self, sample_rate: int = None, in_channels: int = None,
                    out_channels: int = None) -> None:
        """Re-clock and/or re-shape libpd's DSP without tearing the show down.

        ``libpd_init_audio`` may be called again on a live libpd: measured
        against the real DLL, a re-init at half the rate moved a 440 Hz
        ``[osc~]``'s crossing rate exactly as a re-clocked DSP predicts, and
        the patches stayed loaded. The call therefore happens under the
        stream's own pause (the control-plane rule), and a session that was
        asked for the same numbers it already has says nothing at all.
        """
        rate = int(sample_rate or self.sample_rate)
        count_in = self.in_channels if in_channels is None else int(in_channels)
        count_out = self.out_channels if out_channels is None \
            else int(out_channels)
        if (rate, count_in, count_out) == (self.sample_rate, self.in_channels,
                                           self.out_channels):
            return
        with self.control():
            self.lib.init_audio(count_in, count_out, rate)
        self.sample_rate = rate
        self.in_channels = count_in
        self.out_channels = count_out

    @contextmanager
    def control(self):
        """Run a control-plane operation with the stream paused."""
        if self.strict_threads and threading.get_ident() != self.owner_thread:
            raise PdError(
                "libpd control-plane call from a thread that does not own the "
                "Pd session"
            )
        paused = False
        if self._pauser is not None:
            self._pauser(True)
            paused = True
        try:
            yield self
        finally:
            if paused:
                self._pauser(False)

    # -- patches --------------------------------------------------------
    def open_patch(self, patch_id: str, name: str, directory: str) -> PdPatch:
        path = os.path.join(directory, name) if directory else name
        if not os.path.isfile(path):
            # Checked here, and not left to libpd: on Windows ``libpd_openfile``
            # does not fail politely for a path that is not there, it walks a
            # null pointer and takes the whole process down with it.
            raise PdError(f"patch file '{path}' does not exist")

        with self.control():
            handle = self.lib.open_file(name, directory)
            if not handle:
                raise PdError(f"libpd could not open '{name}' in '{directory}'")
            dollar_zero = self.lib.dollar_zero(handle)
        patch = PdPatch(patch_id, name, directory, handle, dollar_zero)
        self.patches[patch_id] = patch
        return patch

    def close_patch(self, patch_id) -> bool:
        patch = self.patches.pop(patch_id, None)
        if patch is None:
            return False
        with self.control():
            self.lib.close_file(patch.handle)
        return True

    def patch(self, patch_id) -> PdPatch:
        patch = self.patches.get(patch_id)
        if patch is None:
            raise PdError(f"patch '{patch_id}' is not loaded")
        return patch

    # -- receivers ------------------------------------------------------
    def bind(self, recv: str) -> int:
        with self.control():
            status = self.lib.bind(recv)
        if status == 0 and recv not in self.bound_receivers:
            self.bound_receivers.append(recv)
        return status

    def unbind(self, recv: str) -> int:
        with self.control():
            status = self.lib.unbind(recv)
        if recv in self.bound_receivers:
            self.bound_receivers.remove(recv)
        return status

    def exists(self, recv: str) -> bool:
        return bool(self.lib.exists(recv))

    # -- data plane (queued, drained by the audio tick) -----------------
    def send_float(self, recv: str, value: float, patch_id=None) -> bool:
        return self.scheduler.submit("float", recv, (float(value),), patch_id)

    def send_bang(self, recv: str, patch_id=None) -> bool:
        return self.scheduler.submit("bang", recv, (), patch_id)

    def send_symbol(self, recv: str, value: str, patch_id=None) -> bool:
        return self.scheduler.submit("symbol", recv, (value,), patch_id)

    def send_list(self, recv: str, values, patch_id=None) -> bool:
        return self.scheduler.submit("list", recv, tuple(values), patch_id)

    def send_message(self, recv: str, message: str, values=(), patch_id=None) -> bool:
        return self.scheduler.submit("message", recv, (message,) + tuple(values), patch_id)

    def send_midi(self, kind: str, *args, patch_id=None) -> bool:
        return self.scheduler.submit(kind, "", tuple(args), patch_id)

    # -- audio ----------------------------------------------------------
    def dsp(self, enabled: bool) -> int:
        with self.control():
            self.lib.start_message(1)
            self.lib.add_float(1.0 if enabled else 0.0)
            status = self.lib.finish_message("pd", "dsp")
        self.dsp_on = bool(enabled)
        return status

    def process(self, ticks: int = 1, in_buffer=None, out_buffer=None) -> int:
        """One audio tick: run queued jobs, then let Pd render ``ticks`` blocks."""
        self.scheduler.apply(self.lib)
        return self.lib.process_float(ticks, in_buffer, out_buffer)

    def process_offline(self, ticks: int = 1) -> List[float]:
        """Render ``ticks`` blocks without a device, returning interleaved output."""
        import ctypes

        frames = self.blocksize_value * ticks
        channels = max(self.out_channels, 1)
        size = frames * channels
        in_buffer = (ctypes.c_float * max(size, 1))()
        out_buffer = (ctypes.c_float * max(size, 1))()
        status = self.process(ticks, in_buffer, out_buffer)
        if status != 0:
            raise PdError(f"libpd_process_float failed with status {status}")
        return [out_buffer[i] for i in range(size)]

    def drain_events(self) -> List[PdEvent]:
        return self.inbox.drain()

    # -- arrays ---------------------------------------------------------
    def array_name(self, patch: PdPatch, name: str) -> str:
        """Resolve ``name`` against the arrays that actually exist.

        A bare name is tried as ``$0-<name>`` first (the authored default) and
        then as-is (hand-written global tables); the winner is cached on the
        patch, so the probe costs one ``libpd_arraysize`` per name.
        """
        if patch is None:
            return name
        if name in patch.arrays:
            return patch.resolve_name(name)

        candidates = self._array_candidates(name)
        for candidate in candidates:
            resolved = patch.resolve_name(candidate)
            if self.lib.array_size(resolved) >= 0:
                patch.arrays[name] = candidate
                return resolved

        patch.arrays[name] = candidates[0]
        return patch.resolve_name(candidates[0])

    @staticmethod
    def _array_candidates(name: str):
        if name.startswith("$0-"):
            return [name]
        return [f"$0-{name}", name]

    def array_read(self, patch: PdPatch, name: str, offset: int = 0, count: int = None):
        return self.lib.read_array(self.array_name(patch, name), offset, count)

    def array_write(self, patch: PdPatch, name: str, values, offset: int = 0) -> int:
        return self.lib.write_array(self.array_name(patch, name), offset, values)

    def array_size(self, patch: PdPatch, name: str) -> int:
        return self.lib.array_size(self.array_name(patch, name))

    def resize_array(self, patch: PdPatch, name: str, size: int) -> int:
        """Grow or shrink a table in place, and answer the size it has now.

        The name goes through :meth:`array_name` like every other array call, so a
        table authored as ``$0-sample`` is resized by the word ``sample``. It has to
        *exist* first: ``libpd_resize_array`` on a symbol with no array behind it is
        a silent failure in one build and a new table in another, and a script that
        thought it had resized something would be wrong either way. So the refusal
        names the array, and the size that comes back is the array's own answer
        rather than the number that was asked for.

        Growing keeps what was already there (Pd resizes in place), which is what
        lets a recorder load a longer take into the table a patch is reading.
        """
        resolved = self.array_name(patch, name)
        if self.lib.array_size(resolved) < 0:
            raise PdError(f"array '{name}' is not on patch "
                          f"'{getattr(patch, 'id', '?')}' - resize an existing "
                          f"table, not a name")
        status = self.lib.resize_array(resolved, int(size))
        if status != 0:
            raise PdError(f"array '{name}' refused a resize to {size} "
                          f"(status {status})")
        return self.lib.array_size(resolved)

    def __repr__(self):
        return f"<PdSession patches={sorted(self.patches)} dsp={self.dsp_on}>"
