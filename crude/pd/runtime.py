"""The script-facing Pd runtime: patches, receivers and callbacks.

``PdSession`` knows how to talk to libpd; ``PdRuntime`` is what the bindings use.
It keeps the script-visible patch handles, the ``ON_PD`` handlers, the patches a
script is authoring (:meth:`PdRuntime.build` .. :meth:`PdRuntime.load_authored`)
and the one place where Pd events are turned back into script calls - always on
the interpreter thread, never from the audio callback.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .cache import DEFAULT_BUILD_DIR, BuildCache
from .engine import PdError, PdPatch, PdSession
from .model import PatchDoc, PatchError
from .writer import write_file, write_patch

#: A handler that keeps raising is muted after this many consecutive errors.
MAX_HANDLER_ERRORS = 3


class PdHandler:
    """One ``ON_PD`` registration."""

    def __init__(self, patch_id: str, recv: str, func, kinds: Tuple[str, ...] = None):
        self.patch_id = patch_id
        self.recv = recv
        self.func = func
        self.kinds = kinds or ()
        self.errors = 0
        self.muted = False
        self.calls = 0

    def matches(self, event) -> bool:
        if event.recv != self.recv:
            return False
        if self.kinds and event.kind not in self.kinds:
            return False
        return True

    def __repr__(self):
        return f"<PdHandler {self.patch_id}:{self.recv} {self.func!r}>"


class PdRuntime:
    """Owns the session plus everything the ``PD_*`` bindings need."""

    def __init__(self, session: PdSession, program=None, project_dir: str = None,
                 log=None, build_dir: str = None, rebuild_pd: bool = False):
        self.session = session
        self.program = program
        self.project_dir = project_dir or os.getcwd()
        self.log = log or (lambda message: None)
        self.patches: Dict[str, PdPatch] = {}
        self.handlers: List[PdHandler] = []
        self.errors: List[str] = []
        self.dispatched = 0
        #: patches a script is authoring, by id (``PD_BUILD`` .. ``PD_LOAD``)
        self.docs: Dict[str, PatchDoc] = {}
        self.cache = BuildCache(
            build_dir or os.path.join(self.project_dir, DEFAULT_BUILD_DIR),
            rebuild=rebuild_pd,
            log=self.log,
        )

    # -- wiring ---------------------------------------------------------
    def attach_program(self, program) -> None:
        """Bind this runtime to the program whose functions the handlers call."""
        self.program = program

    # -- authoring ------------------------------------------------------
    def build(self, patch_id: str, width: int = None, height: int = None,
              font: int = None) -> PatchDoc:
        """Start a new authored patch (``PD_BUILD``)."""
        if patch_id in self.docs or patch_id in self.patches:
            raise PdError(f"patch '{patch_id}' already exists")
        doc = PatchDoc(patch_id, width, height, font)
        self.docs[patch_id] = doc
        return doc

    def doc(self, patch_id: str) -> PatchDoc:
        doc = self.docs.get(patch_id)
        if doc is None:
            raise PdError(f"patch '{patch_id}' was not created with PD_BUILD")
        return doc

    def dump(self, patch_id: str) -> str:
        """The ``.pd`` text of an authored patch, validated first (``PD_DUMP``)."""
        doc = self.doc(patch_id)
        for warning in doc.validate():
            self.log(f"patch '{patch_id}': {warning}")
        return write_patch(doc, validate=False)

    def save(self, patch_id: str, path: str = None) -> str:
        """Write an authored patch to ``path``, or into the build cache."""
        doc = self.doc(patch_id)
        if path is None:
            cached, _text = self.cache.write(doc)
            return os.path.normpath(cached)
        if not os.path.isabs(path):
            path = os.path.join(self.project_dir, path)
        return os.path.normpath(write_file(doc, path))

    def load_authored(self, patch_id: str) -> PdPatch:
        """Serialize -> cache -> ``libpd_openfile`` (``PD_LOAD`` of a built patch).

        Validation happens before anything touches libpd, and a failure quotes
        the generated file, because the file - not the script - is what Pd
        tried to open.
        """
        doc = self.doc(patch_id)
        try:
            warnings = doc.validate()
            path, text = self.cache.write(doc)
        except PatchError as exc:
            raise PdError(str(exc))

        for warning in warnings:
            self.log(f"patch '{patch_id}': {warning}")

        if patch_id in self.patches:
            return self.patches[patch_id]

        try:
            patch = self.open_file(patch_id, path)
        except PdError as exc:
            raise PdError(f"{exc}\nin generated patch '{path}':\n{text}")

        # ``$0-wave`` -> ``<dollar zero>-wave`` for PD_ARRAY_* on this instance.
        for name, pd_name in doc.tables().items():
            patch.arrays[name] = pd_name
        self.probe_receivers(patch, doc)
        return patch

    def probe_receivers(self, patch: PdPatch, doc: PatchDoc) -> List[str]:
        """Warn about receivers the authored patch declares but cannot find."""
        missing = {
            patch.resolve_receiver(box.recv)
            for box in doc.boxes
            if box.recv and not self.session.exists(patch.resolve_receiver(box.recv))
        }
        for recv in sorted(missing):
            self.log(f"patch '{patch.id}': receiver '{recv}' is not reachable")
        return sorted(missing)

    # -- patches --------------------------------------------------------
    def open_file(self, patch_id: str, path: str) -> PdPatch:
        if patch_id in self.patches:
            raise PdError(f"patch '{patch_id}' is already open")

        directory = os.path.dirname(os.path.abspath(path)) if path else self.project_dir
        name = os.path.basename(path)
        if not os.path.isfile(os.path.join(directory, name)):
            raise PdError(f"patch file '{path}' does not exist")

        self.session.lib.add_to_search_path(directory)
        patch = self.session.open_patch(patch_id, name, directory)
        self.patches[patch_id] = patch
        return patch

    def close(self, patch_id: str) -> bool:
        self.handlers = [h for h in self.handlers if h.patch_id != patch_id]
        closed = self.session.close_patch(patch_id)
        self.patches.pop(patch_id, None)
        return closed

    def patch(self, patch_id: str) -> PdPatch:
        patch = self.patches.get(patch_id)
        if patch is None:
            raise PdError(f"patch '{patch_id}' is not loaded")
        return patch

    # -- callbacks ------------------------------------------------------
    def on_pd(self, patch_id: str, recv: str, func, kinds=None) -> PdHandler:
        wanted = self.receiver(patch_id, recv)
        handler = PdHandler(patch_id, wanted, func,
                            tuple(kinds) if kinds else ())
        self.handlers.append(handler)
        # libpd only fires its bang/float/list/symbol hooks for a receiver that
        # has been bound; without this an ON_PD handler is registered but never
        # hears the patch's [send], and a metronome whose [metro] bangs a [s tick]
        # stays silent no matter how many blocks the stream renders.
        self.session.bind(wanted)
        return handler

    def off_pd(self, patch_id: str, recv: str, func=None) -> int:
        wanted = self.receiver(patch_id, recv)
        before = len(self.handlers)
        self.handlers = [
            h for h in self.handlers
            if not (h.patch_id == patch_id and h.recv == wanted
                    and (func is None or h.func is func))
        ]
        # release the libpd binding only when nothing still listens: an unbind
        # while another handler wants the same symbol would silence it
        if before != len(self.handlers) and not any(
                h.recv == wanted for h in self.handlers):
            self.session.unbind(wanted)
        return before - len(self.handlers)

    def dispatch(self, events=None) -> int:
        """Deliver queued Pd events to the script handlers (main thread)."""
        if events is None:
            events = self.session.drain_events()

        delivered = 0
        for event in events:
            if event.kind == "print":
                self.log(f"[pd] {event.recv}")
                continue
            for handler in list(self.handlers):
                if handler.muted or not handler.matches(event):
                    continue
                self._call(handler, event)
                delivered += 1
        self.dispatched += delivered
        return delivered

    def _call(self, handler: PdHandler, event) -> None:
        args = list(event.args)
        if event.kind in ("float", "list", "message"):
            args = args
        elif event.kind == "bang":
            args = []

        handler.calls += 1
        if self.program is None:
            return

        _, error = self.program.invoke(handler.func, args)
        if error is None:
            handler.errors = 0
            return

        handler.errors += 1
        message = f"ON_PD {handler.patch_id}:{handler.recv} failed: {error.details}"
        self.errors.append(message)
        self.log(message)
        if handler.errors >= MAX_HANDLER_ERRORS:
            handler.muted = True
            self.log(
                f"ON_PD {handler.patch_id}:{handler.recv} muted after "
                f"{MAX_HANDLER_ERRORS} consecutive errors"
            )

    # -- data plane -----------------------------------------------------
    def receiver(self, patch_id: str, recv: str) -> str:
        """``$0-foo`` belongs to one patch instance: resolve it before Pd sees it."""
        patch = self.patches.get(patch_id)
        return patch.resolve_receiver(recv) if patch is not None else recv

    def send(self, patch_id: str, recv: str, value) -> bool:
        """Queue one message, choosing the libpd call from the value's type."""
        recv = self.receiver(patch_id, recv)
        if isinstance(value, (list, tuple)):
            return self.session.send_list(recv, value, patch_id)
        if isinstance(value, str):
            return self.session.send_symbol(recv, value, patch_id)
        return self.session.send_float(recv, value, patch_id)

    def send_list(self, patch_id: str, recv: str, values) -> bool:
        return self.session.send_list(self.receiver(patch_id, recv), values, patch_id)

    def send_bang(self, patch_id: str, recv: str) -> bool:
        return self.session.send_bang(self.receiver(patch_id, recv), patch_id)

    def send_message(self, patch_id: str, recv: str, message: str, values=()) -> bool:
        return self.session.send_message(
            self.receiver(patch_id, recv), message, values, patch_id
        )

    def send_midi(self, kind: str, *args) -> bool:
        return self.session.send_midi(kind, *args)

    def flush(self) -> int:
        """Run every queued job now (no audio thread - tests, headless)."""
        return self.session.scheduler.apply(self.session.lib)

    # -- arrays ---------------------------------------------------------
    def array_read(self, patch_id: str, name: str, offset: int = 0, count: int = None):
        return self.session.array_read(self.patch(patch_id), name, offset, count)

    def array_write(self, patch_id: str, name: str, values, offset: int = 0):
        return self.session.array_write(self.patch(patch_id), name, values, offset)

    def array_size(self, patch_id: str, name: str) -> int:
        return self.session.array_size(self.patch(patch_id), name)

    def array_resize(self, patch_id: str, name: str, size: int) -> int:
        return self.session.resize_array(self.patch(patch_id), name, size)

    # -- dsp ------------------------------------------------------------
    def dsp(self, enabled: bool) -> None:
        self.session.dsp(enabled)

    def exists(self, recv: str) -> bool:
        return self.session.exists(recv)

    def __repr__(self):
        return (
            f"<PdRuntime patches={sorted(self.patches)} "
            f"authored={sorted(self.docs)} handlers={len(self.handlers)}>"
        )
