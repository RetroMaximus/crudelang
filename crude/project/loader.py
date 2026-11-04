"""The pipeline that turns a project directory into a running host.

Fifteen phases, in the order :mod:`crude.project.progress` names, each doing one
thing and saying what it found:

``manifest`` the declaration is read → ``environment`` the folders and the log
exist → ``resources.validate`` every declared file is there → ``audio.device`` a
stream is open → ``pd.core`` libpd is running → ``pd.build`` the authored patches
are written into the cache → ``pd.patches`` they are opened, with the hand-written
ones → ``pd.arrays`` the number files fill the tables → ``presets`` the pattern
files are read → ``images`` and ``sounds`` the media is loaded → ``serial`` the
links are opened → ``midi`` the MIDI ports are opened, for a project that asks
for them → ``script.compile`` the entry script parses → ``runtime.activate``
the host is handed to the loop.

The order is not arbitrary and is not configurable: each phase only touches what
the ones before it guaranteed. ``pd.arrays`` needs an opened patch because libpd
cannot create an array; ``audio.device`` before ``pd.core`` so the session can be
handed the stream's pause switch the moment it exists, which is the control-plane
rule; ``script.compile`` after the media, because the script is what names the
widgets that draw them.

Two things make this testable on a machine with no DLL, no device and no display:
every subsystem arrives from the outside if the caller wants it to (``lib``,
``backend``, ``media``, ``serial``, ``midi``, ``runtime``), and a phase that is not
in the plan is never run - which is exactly how ``crude check`` answers questions
about a project without touching hardware.

When a phase fails, whatever earlier phases opened is closed in reverse order
before the error reaches the CLI, so a project that cannot load leaves no stream
running and no libpd instance behind.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..errors import EXIT_OK, ExitRequest, ResourceError, ScriptError
from ..runtime.clock import Clock
from .manifest import (
    Manifest,
    PatchDecl,
    load_manifest,
    manifest_path,
)
from .progress import (
    CancelToken,
    LoadPlan,
    LoadReport,
    LoadTracker,
    NullReporter,
    Reporter,
    check_plan,
    full_plan,
    make_reporter,
)
from .resources import ARRAY, IMAGE, KINDS, PRESET, Resource, ResourceTable, SOUND

#: where a project's log goes when it does not say otherwise
DEFAULT_LOG_DIR = "logs"
#: the audio backend used when audio is asked not to open a device
NULL_BACKEND = "null"

#: how the levels a manifest or ``--log`` names map onto ``logging``
LOG_LEVELS = {
    "none": logging.CRITICAL + 10,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}


def log_level(name: Any, default: str = "warning") -> int:
    """A level name as ``logging`` wants it; an unknown name is the default."""
    if isinstance(name, int):
        return name
    return LOG_LEVELS.get(str(name or default).strip().lower(),
                          LOG_LEVELS[default])


# --------------------------------------------------------------------------
# what the CLI was told, over what the manifest said
# --------------------------------------------------------------------------


@dataclass
class LoadOptions:
    """The command line's answer to each thing the manifest also has an opinion about.

    ``None`` means "not said", which is different from ``False``: ``--no-audio``
    refuses the device, and running a project whose manifest wants one is still
    allowed when that project declared it optional. The CLI builds one of these
    and the pipeline never looks at ``sys.argv``.
    """

    #: ``--mode``: overrides ``runtime.mode``, and with it the audio default
    mode: Optional[str] = None
    #: ``--no-audio`` (False) / ``--audio`` (True); None leaves it to the manifest
    audio: Optional[bool] = None
    #: force a backend name, or ``null`` for ``--no-audio``
    backend: Optional[str] = None
    #: ``--no-serial``
    serial: Optional[bool] = None
    #: ``--no-midi`` (False) / None leaves it to whether the manifest has a
    #: ``midi`` section at all - a project that never declared one never builds
    #: a runtime, and one that did can still be told to skip it for this run
    midi: Optional[bool] = None
    #: force a MIDI backend name, or ``null`` for ``--no-midi``
    midi_backend_name: Optional[str] = None
    #: ``--progress auto|console|json|splash|none``
    progress: Optional[str] = None
    #: ``--fps``
    fps: Optional[int] = None
    #: ``--log error|warning|info|debug``
    log: Optional[str] = None
    #: ``--rebuild-pd``: ignore the cached ``.pd`` text and write it again
    rebuild_pd: bool = False
    #: audio channels the script needs even though the manifest did not ask
    extra: Dict[str, Any] = field(default_factory=dict)

    # -- the three questions the pipeline asks --------------------------
    def wanted_mode(self, manifest: Manifest) -> str:
        return self.mode or manifest.mode

    def wants_audio(self, manifest: Manifest) -> bool:
        """Does this run open a stream at all?

        ``runtime.mode: terminal`` means "I am not looking at a window", which on
        a Pi over SSH also means "do not fight for the audio device"; a manifest
        that says ``enabled: false`` means the same thing on purpose.
        """
        if self.audio is not None:
            return self.audio
        mode = self.wanted_mode(manifest)
        if mode == "terminal":
            return False
        return bool(manifest.audio.enabled)

    def wants_serial(self, manifest: Manifest) -> bool:
        if self.serial is not None:
            return self.serial
        return bool(manifest.serial)

    def wants_midi(self, manifest: Manifest) -> bool:
        """Does this run build a MIDI runtime at all?

        Only a project that declares a ``midi`` section, and that has not had
        that switched off for this run; ``midi.enabled: false`` in the manifest
        means the same as saying nothing here - the runtime is not built, and
        a script's ``MIDI_DEVICES()`` is not reachable, exactly as if the
        project had no MIDI support at all.
        """
        if self.midi is not None:
            return self.midi and bool(manifest.midi.enabled)
        return bool(manifest.midi.enabled)

    def midi_backend(self, manifest: Manifest) -> Optional[str]:
        if not self.wants_midi(manifest):
            return NULL_BACKEND
        return self.midi_backend_name or manifest.midi.backend

    def midi_strict(self, manifest: Manifest) -> bool:
        """Only a project that needs MIDI and called it non-optional insists."""
        if not self.wants_midi(manifest):
            return False
        return manifest.midi.strict

    def progress_spec(self, manifest: Manifest) -> str:
        return self.progress or manifest.loading.progress

    def audio_backend(self, manifest: Manifest) -> Optional[str]:
        if not self.wants_audio(manifest):
            return NULL_BACKEND
        return self.backend or manifest.audio.backend

    def audio_strict(self, manifest: Manifest) -> bool:
        """Only a project that needs audio and did not ask for silence insists."""
        if not self.wants_audio(manifest):
            return False
        return manifest.audio.strict

    def sample_rate(self, manifest: Manifest) -> int:
        return manifest.audio.sample_rate

    def target_fps(self, manifest: Manifest) -> int:
        return self.fps or manifest.runtime.target_fps

    def as_dict(self, manifest: Manifest) -> Dict[str, Any]:
        """What this run decided, for the report and for ``crude check``."""
        return {
            "mode": self.wanted_mode(manifest),
            "audio": self.wants_audio(manifest),
            "backend": self.audio_backend(manifest),
            "strict_audio": self.audio_strict(manifest),
            "serial": self.wants_serial(manifest),
            "midi": self.wants_midi(manifest),
            "progress": self.progress_spec(manifest),
            "fps": self.target_fps(manifest),
            "rebuild_pd": self.rebuild_pd,
        }


# --------------------------------------------------------------------------
# what the media and serial phases hand back
# --------------------------------------------------------------------------


@dataclass
class ImageHandle:
    """One declared image, as far as this stage can know about it.

    The bytes are read here; *decoding* and uploading to the GPU is the GL
    stage's job, which replaces :class:`NullMedia` with a renderer that does it.
    Keeping the handle in between means a script can already ask ``RES`` for a
    resource by name and the widget layer can find the same object later.
    """

    id: str
    path: str
    size: int
    width: int = 0
    height: int = 0
    decoded: bool = False
    detail: str = ""

    def describe(self) -> str:
        where = f"{self.width}x{self.height}" if self.decoded else f"{self.size} B"
        return f"{self.id} {where}"

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "path": self.path, "size": self.size,
                "width": self.width, "height": self.height,
                "decoded": self.decoded, "detail": self.detail}


@dataclass
class SoundHandle:
    """One declared sound: read, sized, and left for the sampler to decode."""

    id: str
    path: str
    size: int
    frames: int = 0
    detail: str = ""

    def describe(self) -> str:
        where = f"{self.frames} frames" if self.frames else f"{self.size} B"
        return f"{self.id} {where}"

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "path": self.path, "size": self.size,
                "frames": self.frames, "detail": self.detail}


class NullMedia:
    """The media host that only knows how big a file is.

    Deliberately not a decoder: PIL is a runtime dependency for the *renderer*,
    and a load report that claimed to have decoded a texture it never looked at
    would be worse than one that says how many bytes are waiting for the GPU.
    """

    #: what this host is called in a report, so a note never blames the wrong thing
    name = "file"

    def load_image(self, entry: Any) -> ImageHandle:
        return ImageHandle(entry.id, entry.path, _size(entry.path))

    def load_sound(self, entry: Any) -> SoundHandle:
        return SoundHandle(entry.id, entry.path, _size(entry.path))


def _size(path: str) -> int:
    """A file's size, with the failure the manifest should have caught."""
    try:
        return os.path.getsize(path)
    except OSError as exc:
        raise ResourceError(
            f"'{os.path.basename(path)}' could not be read: {exc.strerror or exc}"
        )


def _close(handler: Any) -> None:
    """Close a log handler, if it will close; a stuck log is not a load failure."""
    try:
        handler.close()
    except Exception:  # pragma: no cover - a handler that fights back
        pass


class DeferredSerial:
    """The serial host for a caller that wants declarations but no links.

    S11 wired the real :class:`~crude.serial.runtime.SerialRuntime` into the
    phase for a run that opens its ports; this stays as the documented stub a
    test (or any caller) can inject when it wants the links *recorded* on the
    project without anything being opened at all. Saying "not opened, the
    serial stage has not arrived" in the report is honest; saying nothing
    would look like the project has no links.
    """

    #: a host that reports itself, so a note can name what did not happen
    name = "deferred"

    def __init__(self) -> None:
        self.opened: List[Any] = []

    def open(self, decl: Any) -> Any:
        self.opened.append(decl)
        return None

    def close(self, link: Any) -> None:
        return None


# --------------------------------------------------------------------------
# the project
# --------------------------------------------------------------------------


class Project:
    """One project directory, its manifest, and the host the phases fill in.

    Everything the pipeline discovers lands here: the resource table, the audio
    runtime, the Pd runtime, the loaded media, the compiled program. A phase that
    opens something also records how to close it, which is what makes
    :meth:`shutdown` and a failed load leave the machine as it found it.
    """

    def __init__(self, manifest: Manifest, *, options: Optional[LoadOptions] = None,
                 log: Optional[Callable[[str], None]] = None,
                 lib: Any = None, backend: Any = None, media: Any = None,
                 serial: Any = None, midi: Any = None, runtime: Any = None,
                 builders: Optional[Dict[str, Callable[[Any], None]]] = None,
                 resources: Optional[ResourceTable] = None,
                 program: Any = None, clock: Any = None):
        self.manifest = manifest
        self.options = options if options is not None else LoadOptions()
        #: subsystems the caller supplied instead of letting the pipeline build
        self.lib = lib
        self.backend = backend
        self.media = media if media is not None else NullMedia()
        self.serial = serial
        self.midi = midi
        self.runtime = runtime
        #: the frame loop's own UI: built with the mode in phase 15, reached by
        #  the ``WIDGET`` bindings through this project as ``host.ui``
        self.ui: Any = None
        #: the window a ``window`` project opened to draw its splash on. The
        #: command line puts it here, and the frame loop takes it over rather than
        #: opening a second window on the same manifest size
        self.display: Optional[Any] = None
        #: patch id -> a callable that fills the authored document (see ``pd.build``)
        self.builders = dict(builders or {})

        self.notes: List[str] = []
        self.log = log if log is not None else self.note
        self.logger: Optional[logging.Logger] = None
        self.log_path: Optional[str] = None
        #: directories this run created, oldest first
        self.created: List[str] = []

        # -- what the phases fill in --------------------------------
        self.resources: Optional[ResourceTable] = resources
        self.audio: Any = None
        self.session: Any = None
        self.pd: Any = None
        self.images: Dict[str, ImageHandle] = {}
        self.sounds: Dict[str, SoundHandle] = {}
        self.presets: Dict[str, Any] = {}
        self.links: List[Tuple[Any, Any]] = []
        self.ports: List[Tuple[Any, Any]] = []
        self.program: Any = program
        self.source: str = ""
        self.ast: Any = None
        self.arrays: Dict[str, List[float]] = {}
        #: arrays whose patch was not opened: filled when that patch loads
        self.pending_arrays: Dict[str, List[str]] = {}
        self.report: Optional[LoadReport] = None

        self.mode: str = self.options.wanted_mode(manifest)
        self.fps: int = self.options.target_fps(manifest)
        self.active: bool = False
        #: what ``EXIT`` in the script said, which is how the process ends
        self.exit_code: int = EXIT_OK
        #: the time ``TIME``/``SLEEP``/``FPS`` answer from; a test hands in a fake
        self.clock = clock if clock is not None else Clock(target_fps=self.fps)
        self.shutdown_steps: List[Tuple[str, Callable[[], Any]]] = []

    # -- discovery ----------------------------------------------------
    @classmethod
    def open(cls, path: str, *, options: Optional[LoadOptions] = None,
             **inject: Any) -> "Project":
        """Read the manifest of ``path`` and stop there - nothing is loaded yet."""
        return cls(load_manifest(path), options=options, **inject)

    @property
    def directory(self) -> str:
        return self.manifest.project_dir

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def name(self) -> str:
        return self.manifest.display_name

    @property
    def entry_path(self) -> str:
        return self.manifest.entry_path

    # -- talking ------------------------------------------------------
    def note(self, message: str) -> None:
        """Something this project wants the person to know, kept for the report."""
        text = str(message).rstrip()
        if text:
            self.notes.append(text)
            logger = self.logger
            if logger is not None:
                logger.info(text)

    def warning(self, message: str) -> None:
        text = str(message)
        self.notes.append(text)
        logger = self.logger
        if logger is not None:
            logger.warning(text)

    def error(self, message: str) -> None:
        """Something bad enough to be searched for in the log later.

        Kept in the notes *and* written at ``error`` level: the report is what the
        person sees now, the file is what they read when the project is gone.
        """
        text = str(message)
        self.notes.append(text)
        logger = self.logger
        if logger is not None:
            logger.error(text)

    # -- the environment --------------------------------------------
    def ensure_directories(self) -> List[str]:
        """Create what the project needs and returns nothing it had to make.

        The Pd build directory and the log directory only: a project's own
        folders (``pd/``, ``assets/``) are expected to be in the repository, and
        creating them here would turn a missing directory into a confusing empty
        one instead of a load error that names it.
        """
        made: List[str] = []
        for path in (self.manifest.build_path(),
                     os.path.join(self.manifest.project_dir, DEFAULT_LOG_DIR)):
            if os.path.isdir(path):
                continue
            os.makedirs(path, exist_ok=True)
            made.append(path)
            self.created.append(path)
        return made

    def setup_logging(self, level: Any = None) -> logging.Logger:
        """The project's log file, and the logger every subsystem writes through.

        The legacy root ``directory_structure.py`` is about the *user's* folders
        (application data, the venv, elevation), not a project's, and a bundled
        build cannot import a module from the repository root - so the project's
        own layout is this: the manifest's directories, created on demand.
        """
        wanted = log_level(level if level is not None else self.options.log)
        logger = logging.getLogger(f"crude.{self.manifest.id}")
        logger.setLevel(wanted)

        name = f"{self.manifest.id}.log"
        self.log_path = os.path.join(self.manifest.project_dir, DEFAULT_LOG_DIR, name)
        # a logger is shared by every project with the same id, so a handler
        # pointing at another directory is replaced rather than stacked on: two
        # handlers would write one project's notes into another project's log
        for handler in list(logger.handlers):
            if getattr(handler, "baseFilename", None) != \
                    os.path.abspath(self.log_path):
                logger.removeHandler(handler)
                _close(handler)

        if not logger.handlers:
            try:
                # the log makes its own folder: a caller that only wants a logger
                # (``Project.open`` then ``note``) should not fail for a missing
                # directory the loading pipeline would have made
                os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
                handler = logging.FileHandler(self.log_path, encoding="utf-8")
            except OSError as exc:  # a log file is not a reason to fail a load
                self.log_path = None
                self.warning(f"log file could not be opened: {exc}")
            else:
                handler.setFormatter(
                    logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
                logger.addHandler(handler)
                logger.propagate = False

        self.logger = logger
        return logger

    def close_logging(self) -> None:
        """Let go of the log file, so a project can be opened again or deleted.

        Only *this* project's handlers are closed, and the call is idempotent:
        a log that is already detached has nothing left to do.
        """
        logger = self.logger
        if logger is None:
            return
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            _close(handler)
        self.logger = None

    # -- the host a script sees ---------------------------------------
    def host_object(self) -> Any:
        """The namespace the ``RES``/``PRESET_*``/``MODE`` bindings reach.

        ``Program(host=...)`` hands this to every binding through
        :class:`~crude.bindings.registry.HostContext`, and :func:`attach_program`
        tells the Pd runtime which interpreter its callbacks must return to.
        """
        import types

        return types.SimpleNamespace(
            project=self,
            audio=self.audio,
            pd=self.pd,
            resources=self.resources,
            serial=self.serial,
            midi=self.midi,
            runtime=self.runtime,
            ui=self.ui,
            clock=self.clock,
            attach_program=self.attach_program,
        )

    def attach_program(self, program: Any) -> None:
        """Hand the program to every subsystem that fires script callbacks."""
        self.program = program
        for value in (self.pd, self.audio, self.serial, self.midi, self.runtime,
                      self.ui):
            attach = getattr(value, "attach_program", None)
            if callable(attach):
                attach(program)

    # -- what a script asks for ---------------------------------------
    def resource(self, name: str) -> Any:
        """The ``RES()`` answer: one declared file, by any name it answers to."""
        if self.resources is None:
            raise ResourceError(
                "this project has not been loaded, so it has no resources yet")
        return self.resources.get(name)

    def resource_path(self, name: str) -> str:
        return self.resources.path(name) if self.resources else ""

    def array_values(self, name: str) -> List[float]:
        if self.resources is None:
            raise ResourceError("the project is not loaded; there are no arrays yet")
        return self.resources.values(name)

    def read_preset(self, name: str) -> Any:
        """A preset's values, from the file the manifest declared."""
        if self.resources is None:
            raise ResourceError("the project is not loaded; there are no presets yet")
        return self.resources.read_preset(name)

    def write_preset(self, name: str, data: Any) -> str:
        if self.resources is None:
            raise ResourceError("the project is not loaded; presets cannot be saved")
        saved = self.resources.write_preset(name, data)
        self.note(f"saved preset '{name}' to '{os.path.basename(saved)}'")
        return saved

    def entry_source(self) -> str:
        """Read the entry script, naming the project when it is not there."""
        path = self.entry_path
        if not os.path.isfile(path):
            raise ResourceError(
                f"the entry script '{self.manifest.entry}' of project "
                f"'{self.manifest.id}' is not there ({path})")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except OSError as exc:
            raise ResourceError(f"'{path}' could not be read: {exc.strerror or exc}")
        except UnicodeDecodeError as exc:
            raise ResourceError(
                f"'{path}' is not a text script; .crude files are plain text ({exc})")

    # -- the pipeline -------------------------------------------------
    def load(self, reporter: Optional[Reporter] = None, *,
             token: Optional[CancelToken] = None, canvas: Any = None,
             stream: Any = None) -> LoadReport:
        """Run every phase, and return the report of what happened."""
        return self.pipeline(reporter, token=token, canvas=canvas,
                             stream=stream).run()

    def check(self, reporter: Optional[Reporter] = None, *, stream: Any = None,
              canvas: Any = None) -> LoadReport:
        """Only the phases that need no hardware: what ``crude check`` runs."""
        pipeline = self.pipeline(reporter, plan=check_plan(**self._plan_about()),
                                 stream=stream, canvas=canvas)
        return pipeline.run()

    def pipeline(self, reporter: Optional[Reporter] = None, *,
                 plan: Optional[LoadPlan] = None, token: Optional[CancelToken] = None,
                 canvas: Any = None, stream: Any = None) -> "LoadPipeline":
        if reporter is None:
            reporter = make_reporter(self.options.progress_spec(self.manifest),
                                     mode=self.mode, canvas=canvas, stream=stream)
        return LoadPipeline(self, reporter=reporter, plan=plan, token=token)

    def _plan_about(self) -> Dict[str, Any]:
        return {"label": self.name, "mode": self.mode}

    def run_script(self) -> Any:
        """Run the compiled entry script; a script error keeps its own exit code.

        ``EXIT`` is the one thing a script may do that is not a failure: it chooses
        how the process ends, so the code lands on :attr:`exit_code` and is
        returned here instead of raising. A zero exit says nothing in the log, since
        a script that finished on purpose has not gone wrong.

        A kind ``WIDGET_DEFINE`` registers lives for one program, but the table that
        holds them is global to the process, so the ones the last run left behind
        are forgotten before this one starts. A project that defines a kind and is
        run twice in one interpreter - the CI matrix running one example in three
        modes, a test that stages an example for every case - is the same project
        both times, not a second one that collides with the first.
        """
        from ..lang import Program
        from ..ui import reset

        reset()
        if self.program is None:
            self.program = Program(self.manifest.entry, host=self.host_object())
        try:
            value, error = self.program.run(self.source)
        except ExitRequest as request:
            self.exit_code = request.code
            if request.reason:
                self.note(request.reason)
            elif request.code:
                self.note(f"{self.manifest.entry} asked to exit with code "
                          f"{request.code}")
            return request.code
        if error is not None:
            raise ScriptError(f"{self.manifest.entry}: {error.as_string()}")
        return value

    # -- undoing ------------------------------------------------------
    def record(self, what: str, closer: Callable[[], Any]) -> None:
        """Say "this is open, and this is how to close it", newest last."""
        self.shutdown_steps.append((what, closer))

    def shutdown(self) -> List[str]:
        """Close everything this project opened, newest first.

        Idempotent and unmuting: a teardown that fails is reported and the rest
        still run, because a stream that would not stop is a reason to still try
        to close the patches underneath it.
        """
        messages: List[str] = []
        while self.shutdown_steps:
            what, closer = self.shutdown_steps.pop()
            try:
                closer()
            except Exception as exc:  # a broken teardown is never the only problem
                message = f"could not close {what}: {exc}"
                messages.append(message)
                self.warning(message)
        # the log goes last: everything above may still have something to say
        self.close_logging()
        self.active = False
        return messages

    def describe(self) -> List[str]:
        """What this project is and what it has, for ``crude check``."""
        manifest = self.manifest
        lines = [
            f"project   {manifest.id} '{manifest.display_name}'",
            f"entry     {manifest.entry}",
            f"mode      {self.mode} at {self.options.target_fps(manifest)} fps",
            f"audio     "
            f"{self.options.audio_backend(manifest) or 'default'} "
            f"{manifest.audio.sample_rate} Hz, "
            f"{manifest.audio.block_size} frames/block"
            if self.options.wants_audio(manifest) else "audio     off",
            f"patches   {len(manifest.pd.patches)} declared",
            f"serial    {len(manifest.serial)} declared"
            if self.options.wants_serial(manifest) else "serial    off",
            f"midi      {self.options.midi_backend(manifest) or 'default'}"
            if self.options.wants_midi(manifest) else "midi      off",
        ]
        if self.resources is not None:
            counts = self.resources.counts()
            lines.append("resources " + ", ".join(
                f"{counts[kind]} {kind}" for kind in KINDS))
        return lines

    def info(self) -> Dict[str, Any]:
        return {
            "id": self.manifest.id,
            "name": self.name,
            "directory": self.directory,
            "manifest": manifest_path(self.directory),
            "mode": self.mode,
            "active": self.active,
            "options": self.options.as_dict(self.manifest),
            "resources": self.resources.counts() if self.resources is not None else None,
            "audio": self.audio.info() if self.audio is not None else None,
            "patches": sorted(self.pd.patches) if self.pd is not None else [],
            "images": sorted(self.images),
            "sounds": sorted(self.sounds),
            "presets": sorted(self.presets),
            "midi": self.midi.info() if getattr(self.midi, "info", None) else None,
            "notes": list(self.notes),
        }

    def __str__(self) -> str:
        return (f"Project {self.manifest.id} ({self.mode}, "
                f"{'loaded' if self.report is not None else 'not loaded'})")


# --------------------------------------------------------------------------
# the pipeline
# --------------------------------------------------------------------------


class LoadPipeline:
    """Runs the phases of one plan, in order, and reports every one of them.

    The pipeline holds no state of its own beyond the tracker: everything it
    discovers goes onto the :class:`Project`, and everything it opens goes onto
    ``project.shutdown_steps`` so :meth:`unwind` can put it back. A phase whose
    name is not in the plan is never called, which is the whole of how
    ``crude check`` avoids touching a device.

    Failure is the interesting case. The step that failed is named in the report
    with the reason, the phases after it never ran, and whatever the earlier ones
    opened is closed in reverse order before the error is raised again - so a
    project that cannot come up leaves no stream running and no libpd instance.
    """

    def __init__(self, project: Project, *, reporter: Optional[Reporter] = None,
                 plan: Optional[LoadPlan] = None,
                 token: Optional[CancelToken] = None) -> None:
        self.project = project
        self.manifest = project.manifest
        self.options = project.options
        self.plan = plan if plan is not None else full_plan(**project._plan_about())
        self.reporter = reporter if reporter is not None else NullReporter()
        self.tracker = LoadTracker(self.plan, self.reporter, token=token)
        self.report: Optional[LoadReport] = None
        #: what a failed unwind could not close, newest first
        self.unwound: List[str] = []

    def __str__(self) -> str:
        return f"LoadPipeline {self.plan} via {self.reporter}"

    # -- reporting helpers ----------------------------------------------
    def detail(self, name: str, text: str) -> None:
        """Mention one thing this phase found, inside the phase's own line."""
        self.tracker.detail(name, text)

    def details(self, name: str, texts: Sequence[str]) -> None:
        self.tracker.details(name, texts)

    def progress(self, name: str, fraction: float) -> None:
        """How far through *this phase* the pipeline is, 0..1."""
        self.tracker.progress(name, fraction)

    def shown(self, path: str) -> str:
        """A path the way a person reading the report would write it."""
        try:
            return os.path.relpath(path, self.manifest.project_dir).replace(os.sep, "/")
        except ValueError:  # pragma: no cover - a path on another drive
            return str(path)

    # -- running ---------------------------------------------------------
    def run(self) -> LoadReport:
        """Every phase of the plan, then the report - or the failure that stopped it.

        The report is written whether or not the load succeeded, and the error is
        raised *after* :meth:`unwind`, so the reporter's output and the exception
        agree about what happened.
        """
        tracker = self.tracker
        project = self.project
        tracker.start()

        failure: Optional[BaseException] = None
        try:
            for name in self.plan.names:
                with tracker.step(name):
                    self._dispatch(name)
        except BaseException as exc:            # noqa: BLE001 - reported, then raised
            failure = exc
            self.unwind()
        finally:
            report = tracker.finish()
            self.report = report
            project.report = report

        if failure is not None:
            raise failure
        return report

    def _dispatch(self, name: str) -> None:
        handler = getattr(self, "_phase_" + name.replace(".", "_"), None)
        if handler is None:
            # a plan a caller built by hand may name a phase this stage has no
            # work for; saying so is better than pretending the step did nothing
            self.detail(name, "nothing for this pipeline to do")
            return
        handler()

    def unwind(self) -> List[str]:
        """Close what the phases opened, newest first, and report what could not."""
        project = self.project
        if not project.shutdown_steps:
            return []
        open_things = len(project.shutdown_steps)
        messages = project.shutdown()
        self.unwound = messages

        # the tracker's own report is the one that knows this run: the project's
        # still holds the previous one when a project is loaded twice
        where = self.tracker.report.failed_name
        if not where and project.report is not None:
            where = project.report.failed_name
        text = f"unwound {open_things} open thing(s) in reverse order"
        if messages:
            text += "; " + "; ".join(messages)
        if where:
            self.detail(where, text)
        return messages

    # -- 1 manifest ------------------------------------------------------
    def _phase_manifest(self) -> None:
        """What the declaration says this project needs, as the report sees it."""
        name = "manifest"
        project, manifest, options = self.project, self.manifest, self.options
        if project.resources is None:
            project.resources = ResourceTable(manifest)
        counts = project.resources.counts()

        self.detail(name, f"{manifest.id} '{manifest.display_name}' "
                          f"from {self.shown(manifest.path)}")
        self.detail(name, f"entry {manifest.entry}")
        self.detail(name, f"mode {project.mode} at "
                          f"{options.target_fps(manifest)} fps")
        if options.wants_audio(manifest):
            self.detail(
                name,
                f"audio {options.audio_backend(manifest) or 'default'} at "
                f"{options.sample_rate(manifest)} Hz, "
                f"{manifest.audio.block_size} frames/block")
        else:
            self.detail(name, "audio off for this run")

        patches = manifest.pd.patches
        authored = len([decl for decl in patches if decl.is_authored])
        self.detail(name, f"{len(patches)} patch(es), {authored} authored"
                          if patches else "no patches declared")
        self.detail(name, "resources " + ", ".join(
            f"{counts[kind]} {kind}" for kind in KINDS))
        if manifest.serial:
            self.detail(name, f"{len(manifest.serial)} serial link(s) declared")
        if manifest.midi.enabled:
            self.detail(name, "midi declared")

    # -- 2 environment ---------------------------------------------------
    def _phase_environment(self) -> None:
        """The folders the project writes into, and the log it writes to.

        This is stdlib ``logging`` pointed at a file inside the project, not the
        legacy root ``directory_structure.py``: that module is about the *user's*
        application folders and cannot be imported from a bundled build.
        """
        name = "environment"
        project = self.project
        made = project.ensure_directories()
        self.detail(name, f"build cache {self.shown(self.manifest.build_path())}")
        for path in made:
            self.detail(name, f"created {self.shown(path)}")

        logger = project.setup_logging()
        where = self.shown(project.log_path) if project.log_path else "not writable"
        self.detail(name, f"log {logging.getLevelName(logger.level)}: {where}")

    # -- 3 resources.validate -------------------------------------------
    def _phase_resources_validate(self) -> None:
        """Every file the manifest names - resources and hand-written patches."""
        name = "resources.validate"
        project, manifest = self.project, self.manifest
        table = project.resources
        if table is None:
            table = project.resources = ResourceTable(manifest)

        problems = list(table.check())
        problems.extend(self._patch_file_problems())
        self.details(name, problems)
        if problems:
            raise ResourceError(
                f"{len(problems)} declared file(s) of project '{manifest.id}' "
                f"are missing:\n" + "\n".join(problems))

        if table.entries:
            self.detail(name, f"{len(table.entries)} declared file(s) are there")
        else:
            self.detail(name, "the project declares no files")

    def _patch_file_problems(self) -> List[str]:
        """Declared ``.pd`` files that are not there, the way ``check`` sees them.

        A hand-written patch is a file like any other, so a missing one is
        reported here rather than waiting for libpd to fail on it - except for a
        patch declared ``optional``, which the loading phase skips quietly.
        """
        lines: List[str] = []
        for decl in self.manifest.pd.patches:
            if decl.is_authored or decl.optional or not decl.file:
                continue
            if not os.path.isfile(self.manifest.resolve(decl.file)):
                lines.append(f"patch '{decl.id}' is declared as '{decl.file}', "
                             f"which is not in '{self.manifest.project_dir}'")
        return lines

    # -- 4 audio.device --------------------------------------------------
    def _phase_audio_device(self) -> None:
        """Open the stream, or say why this run does not want one.

        The runtime is *opened* and not started: who pulls the blocks is the
        loop's decision (S11), and starting a device before the patches exist
        would render silence with the pause switch not yet wired to libpd.
        """
        name = "audio.device"
        project, manifest, options = self.project, self.manifest, self.options
        from ..audio import AudioRuntime

        wants = options.wants_audio(manifest)
        runtime = AudioRuntime(session=None, config=manifest.audio.config(),
                               backend=project.backend, log=project.note,
                               preferred=options.audio_backend(manifest),
                               strict=options.audio_strict(manifest))
        project.audio = runtime

        if not wants:
            self.detail(name, "audio is off for this run; no device was opened")
            return

        backend = runtime.open()
        project.record(f"audio backend '{backend.name}'", runtime.close)
        self.detail(name, f"{backend.describe()}")
        self.details(name, runtime.notes)
        if not backend.live:
            # A run that wanted audio and landed on a pulled backend will make no
            # sound on this machine, whatever else works. That has to be said in
            # the run's own output, not only in a progress detail that scrolls
            # away: silence nobody warned about is indistinguishable from a bug.
            self.detail(name, "a silent stream: the runtime tick renders its "
                              "blocks")
            project.note("audio: this run reached no device - the stream is "
                         "pulled, not played, and nothing will be heard")

    # -- 5 pd.core -------------------------------------------------------
    def _phase_pd_core(self) -> None:
        """libpd is loaded, initialised, and handed the stream's pause switch."""
        name = "pd.core"
        project, manifest, options = self.project, self.manifest, self.options
        from ..pd import PdRuntime, PdSession

        lib = project.lib
        if lib is None:
            from ..pd import Libpd

            lib = Libpd.load()
            self.detail(name, f"libpd {self.shown(lib.path)}")
        else:
            self.detail(name, f"libpd {getattr(lib, 'path', '?')} supplied by the "
                             f"caller")

        session = PdSession(lib,
                            sample_rate=options.sample_rate(manifest),
                            in_channels=manifest.audio.in_channels,
                            out_channels=manifest.audio.out_channels)
        session.start()
        project.session = session
        project.record("the libpd instance", session.shutdown)
        self.detail(name, f"blocksize {session.blocksize_value} at "
                          f"{session.sample_rate} Hz, "
                          f"{session.in_channels} in / {session.out_channels} out")
        self._add_search_paths(lib)

        project.pd = PdRuntime(session, program=project.program,
                               project_dir=manifest.project_dir,
                               log=project.note, build_dir=manifest.build_path(),
                               rebuild_pd=options.rebuild_pd)
        # The stream was opened a phase early with no session to render into;
        # hand it the one just made so its callback reaches libpd (and so a live
        # window is heard, not merely started). Wiring the pauser alone left the
        # render frozen to the no-op it captured at open time.
        if project.audio is not None:
            project.audio.attach_session(session)
        self._wire_pauser(session, project.audio)
        session.dsp(False)
        self.detail(name, "dsp off; a script turns it on with PD_DSP")

    def _add_search_paths(self, lib: Any) -> None:
        """Register ``pd.search_paths`` so an ``[import]`` in a patch resolves."""
        for directory in self.manifest.search_dirs()[1:]:
            shown = self.shown(directory)
            if os.path.isdir(directory):
                lib.add_to_search_path(directory)
                self.detail("pd.core", f"search path {shown}")
            else:
                self.detail("pd.core", f"search path {shown} is not there")

    @staticmethod
    def _wire_pauser(session: Any, audio: Any) -> None:
        """Give the session the stream's pause switch (the control-plane rule).

        :class:`~crude.audio.runtime.AudioRuntime` does this itself when it opens
        a backend, but the order the plan fixes puts the device one phase before
        libpd, so the runtime had no session to hand it to yet.
        """
        if session is None or audio is None:
            return
        backend = getattr(audio, "backend", None)
        if backend is None or not hasattr(session, "set_pauser"):
            return
        session.set_pauser(backend.pause_switch())

    # -- 6 pd.build ------------------------------------------------------
    def _phase_pd_build(self) -> None:
        """Turn each authored patch into a ``.pd`` file in the build cache.

        Nothing reaches libpd here, which is the point: writing the text is the
        slow part a person should see progress through, and a patch that fails
        validation fails in this phase with the generated text quoted, not
        inside ``libpd_openfile``.
        """
        name = "pd.build"
        project, pd = self.project, self.project.pd
        authored = [decl for decl in self.manifest.pd.patches if decl.is_authored]
        if pd is None:  # pragma: no cover - only a hand-written plan skips pd.core
            self.detail(name, "there is no Pd runtime to build patches into")
            return

        cache = pd.cache
        self.detail(name, f"cache {self.shown(cache.directory)}"
                          + (", rebuilding" if cache.rebuild else ""))
        if not authored:
            self.detail(name, "the project declares no authored patches")
            return

        for index, decl in enumerate(authored, start=1):
            doc = pd.build(decl.id, decl.authored.width, decl.authored.height,
                           decl.authored.font)
            builder = project.builders.get(decl.id)
            if builder is not None:
                builder(doc)
            path, _text = cache.write(doc)
            self.detail(name, f"{decl.id}: {decl.authored.width}x"
                              f"{decl.authored.height}, {len(doc.boxes)} box(es), "
                              f"{len(doc.tables())} table(s) -> "
                              f"{self.shown(path)}")
            for warning in doc.warnings:
                project.note(f"patch '{decl.id}': {warning}")
            self.progress(name, index / len(authored))

        report = cache.report()
        self.detail(name, f"{report['generated']} built, {report['reused']} reused")

    # -- 7 pd.patches ----------------------------------------------------
    def _phase_pd_patches(self) -> None:
        """Open the declared patches: authored ones from the cache, files from disk."""
        name = "pd.patches"
        pd = self.project.pd
        decls = list(self.manifest.pd.patches)
        if not decls:
            self.detail(name, "there are no patches to open")
            return

        for index, decl in enumerate(decls, start=1):
            self._open_patch(name, decl, pd)
            self.progress(name, index / len(decls))

        if pd is not None and pd.patches:
            self.detail(name, f"{len(pd.patches)} open: "
                              + ", ".join(sorted(pd.patches)))

    def _open_patch(self, name: str, decl: PatchDecl, pd: Any) -> bool:
        """Open one declared patch; returns whether anything is now open."""
        project = self.project
        if decl.is_authored:
            if not decl.should_preload:
                self.detail(name, f"{decl.id} is built; the script loads it "
                                  f"with PD_LOAD")
                return False
            patch = pd.load_authored(decl.id)
            project.record(f"patch '{decl.id}'", lambda: pd.close(decl.id))
            self.detail(name, f"{decl.id} (authored, $0={patch.dollar_zero})")
            return True

        path = self.manifest.resolve(decl.file or "")
        if not os.path.isfile(path):
            if decl.optional:
                self.detail(name, f"{decl.id} is optional and '{decl.file}' "
                                  f"is not there; skipped")
                return False
            raise ResourceError(f"patch '{decl.id}': '{decl.file}' is not there "
                                f"({path})")
        patch = pd.open_file(decl.id, path)
        project.record(f"patch '{decl.id}'", lambda: pd.close(decl.id))
        self.detail(name, f"{decl.id} from {decl.file} "
                          f"($0={patch.dollar_zero})")
        return True

    # -- 8 pd.arrays -----------------------------------------------------
    def _phase_pd_arrays(self) -> None:
        """Fill the tables the number files belong to.

        libpd has no array-creation call, so an array can only land in a table
        that an opened patch declares - which is why a declaration names its
        patch, and why one whose patch is not open is *queued* rather than lost:
        the script that loads that patch later asks the project to fill it.
        """
        name = "pd.arrays"
        project, pd = self.project, self.project.pd
        table = project.resources
        entries = list(table.of_kind(ARRAY)) if table is not None else []
        if not entries:
            self.detail(name, "the project declares no array files")
            return

        for index, entry in enumerate(entries, start=1):
            self._fill_array(name, entry, pd)
            self.progress(name, index / len(entries))

        if project.arrays:
            numbers = sum(len(values) for values in project.arrays.values())
            self.detail(name, f"{len(project.arrays)} array(s), {numbers} "
                              f"number(s) parsed")

    def _fill_array(self, name: str, entry: Resource, pd: Any) -> None:
        project = self.project
        decl = entry.array
        ident = decl.id if decl is not None else entry.id
        values = project.resources.values(entry.id)
        project.arrays[ident] = values

        where = decl.patch if decl is not None else None
        if not where:
            self.detail(name, f"{ident}: {len(values)} number(s) parsed; no patch "
                              f"named, kept for the script")
            return
        if pd is None or where not in pd.patches:
            project.pending_arrays.setdefault(ident, []).append(where)
            self.detail(name, f"{ident}: patch '{where}' is not open; queued for "
                              f"when it is")
            return

        size = pd.array_size(where, ident)
        if size < 0:
            project.warning(f"array '{ident}': patch '{where}' declares no table "
                            f"of that name")
            self.detail(name, f"{ident}: no table in '{where}'")
            return

        wanted = min(len(values), size)
        status = pd.array_write(where, ident, values[:wanted])
        if status is not None and status < 0:
            project.warning(f"array '{ident}': Pd refused the write "
                            f"(status {status})")
            self.detail(name, f"{ident}: write into '{where}' failed")
            return
        note = "" if wanted == len(values) else f", the table holds {size}"
        self.detail(name, f"{ident} -> {where}: {wanted} of {len(values)} "
                          f"number(s){note}")

    # -- 9 presets -------------------------------------------------------
    def _phase_presets(self) -> None:
        """Read the pattern files a project ships, for ``PRESET_LOAD``."""
        name = "presets"
        project = self.project
        table = project.resources
        entries = list(table.of_kind(PRESET)) if table is not None else []
        if not entries:
            self.detail(name, "the project ships no pattern files")
            return

        for index, entry in enumerate(entries, start=1):
            data = table.read_preset(entry.id)
            project.presets[entry.id] = data
            self.detail(name, f"{entry.id} from {entry.declared} "
                              f"({_shape(data)})")
            self.progress(name, index / len(entries))
        self.detail(name, f"{len(entries)} preset(s) ready")

    # -- 10 images / 11 sounds ------------------------------------------
    def _phase_images(self) -> None:
        """Read every declared image; decoding is the renderer's job."""
        name = "images"
        media = self.project.media
        self._load_media(name, IMAGE, self.project.images, media.load_image)
        if isinstance(media, NullMedia):
            self.detail(name, "bytes only: the renderer decodes and uploads them "
                              "when a window opens")

    def _phase_sounds(self) -> None:
        """Read every declared sound; a sampler decodes them when it wants one."""
        self._load_media("sounds", SOUND, self.project.sounds,
                         self.project.media.load_sound)

    def _load_media(self, name: str, kind: str, store: Dict[str, Any],
                    loader: Callable[[Any], Any]) -> None:
        """One loop for both media kinds: read it, keep the handle, say how big."""
        table = self.project.resources
        entries = list(table.of_kind(kind)) if table is not None else []
        if not entries:
            self.detail(name, f"the project declares no {kind} files")
            return

        total = 0
        for index, entry in enumerate(entries, start=1):
            handle = loader(entry)
            store[entry.id] = handle
            total += int(getattr(handle, "size", 0) or 0)
            self.detail(name, handle.describe())
            self.progress(name, index / len(entries))
        self.detail(name, f"{len(entries)} {kind}(s), {total} byte(s) read")

    # -- 12 serial -------------------------------------------------------
    def _phase_serial(self) -> None:
        """Ask the serial host for each declared link, if this run wants links at all.

        S11 is the stage that opens the ports: with no host injected the phase
        builds the real :class:`~crude.serial.runtime.SerialRuntime`, whose
        links the frame loop drains every tick. A caller that hands in its own
        host is still obeyed exactly - a :class:`DeferredSerial` records the
        declarations and opens nothing, which is what a loader test wants.
        """
        name = "serial"
        project, options = self.project, self.options
        decls = list(self.manifest.serial)
        if not decls:
            self.detail(name, "the project declares no serial links")
            return

        if not options.wants_serial(self.manifest):
            self.detail(name, f"{len(decls)} link(s) declared, not opened for "
                              f"this run")
            return

        if project.serial is None:
            from ..serial import SerialRuntime

            project.serial = SerialRuntime(program=project.program,
                                           log=project.note)
        host = project.serial
        from ..serial import SerialError

        for index, decl in enumerate(decls, start=1):
            try:
                link = host.open(decl)
            except SerialError as exc:
                if not decl.optional:
                    raise
                # the runtime keeps a skipped link registered, so a script's
                # SERIAL_OPEN with an injected port still finds it by id
                link = None
                self.detail(name, f"{decl.id} skipped: {exc}")
            project.links.append((decl, link))
            if link is not None:
                project.record(f"serial link '{decl.id}'",
                               lambda link=link, host=host: host.close(link))
            self.detail(name, f"{decl.describe()}"
                              + ("" if link is not None else " (not opened)"))
            self.progress(name, index / len(decls))

        if getattr(host, "name", "") == "deferred":
            project.note(f"{len(decls)} serial link(s) are declared and recorded; "
                         f"the serial stage opens the ports")

    # -- 13 midi ---------------------------------------------------------
    def _phase_midi(self) -> None:
        """Build the MIDI runtime, for a project that declared a ``midi`` block.

        Unlike ``serial``, there is nothing in the manifest to open here: the
        section only says whether this project wants MIDI at all and which
        backend it would prefer. Ports are opened by the script, when a person
        picks one in the File menu or calls ``MIDI_OPEN`` directly. A project
        that never declared ``midi`` never reaches this phase at all (it is not
        in its plan); a project that declared it optional, on a machine with no
        ``python-rtmidi``, gets the null backend and an empty device list - a
        note, not a failure, exactly like ``--no-audio`` on the stream.
        """
        name = "midi"
        project, options = self.project, self.options

        if not options.wants_midi(self.manifest):
            self.detail(name, "MIDI is off for this run; no backend was opened")
            return

        if project.midi is not None:
            # a caller (a test, a host) injected its own runtime and owns it
            self.detail(name, f"{getattr(project.midi, 'backend', None)} supplied "
                              f"by the caller")
            if project.program is not None:
                project.attach_program(project.program)
            return

        from ..midi import MidiRuntime, MidiUnavailable, open_backend

        # a strict project that named a backend it cannot have raises out of
        # ``open_backend``: that is the load's failure to report, not a silent
        # fallback, so it goes straight back up.
        try:
            backend = open_backend(log=project.note,
                                   preferred=options.midi_backend(self.manifest),
                                   strict=options.midi_strict(self.manifest))
        except MidiUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - a backend's own import bug
            project.warning(f"midi: no backend could be opened ({exc}); "
                            f"continuing with no MIDI")
            backend = None

        runtime = MidiRuntime(backend=backend, program=project.program,
                              log=project.note)
        project.midi = runtime
        if project.program is not None:
            runtime.attach_program(project.program)
        if backend is not None:
            project.record("the MIDI runtime", runtime.close_all)
            self.detail(name, backend.name)
            self.details(name, getattr(backend, "notes", []))
        else:
            self.detail(name, "no MIDI backend; a script's MIDI_DEVICES() "
                              "will be empty")

    # -- 14 script.compile ----------------------------------------------
    def _phase_script_compile(self) -> None:
        """Read the entry script and parse it, without running a statement."""
        name = "script.compile"
        project, manifest = self.project, self.manifest
        source = project.entry_source()
        project.source = source

        program = project.program
        if program is None:
            from ..lang import Program

            program = Program(manifest.entry, host=project.host_object())
            project.program = program

        ast, error = program.parse(source)
        if error is not None:
            raise ScriptError(f"{manifest.entry}: {error.as_string()}")
        project.ast = ast
        project.attach_program(program)

        lines = len(source.splitlines())
        # the entry node is the top-level statement list the parser builds
        statements = len(getattr(ast, "element_nodes", None) or ())
        self.detail(name, f"{manifest.entry}: {lines} line(s), "
                          f"{statements} statement(s) parsed")

    # -- 15 runtime.activate --------------------------------------------
    def _phase_runtime_activate(self) -> None:
        """Hand the filled-in host to the loop that is going to run it.

        S11's half of this phase is the :class:`~crude.runtime.runtime.Runtime`
        itself: with no runtime injected, the pipeline builds one, opens the
        window a ``window`` run promised - downgrading, with a message, when
        this machine cannot give one - and activates it, which builds the UI
        the mode implies. Starting the stream is still deliberately not here:
        a device that begins rendering before the script has wired its patches
        is a device that plays silence loudly; the loop's ``run()`` starts it.
        """
        name = "runtime.activate"
        project, options, manifest = self.project, self.options, self.manifest
        project.mode = options.wanted_mode(manifest)
        project.fps = options.target_fps(manifest)
        # the clock paces what the run decided, not what it was built with: a
        # ``--fps`` on the command line has to reach ``FPS()`` in the script too
        project.clock.target_fps = project.fps

        program = project.program

        if project.runtime is None and project.mode == "window" \
                and project.display is None:
            from ..runtime import open_display as _open_display

            display, reason = _open_display(project)
            if display is None:
                from ..runtime import explain

                project.mode = "headless"
                why = explain("window", reason)
                project.note(why)
                self.detail(name, why)
            else:
                project.display = display
                project.record("the window", display.close)

        if project.runtime is None:
            from ..runtime import Runtime

            project.runtime = Runtime(project, mode=project.mode,
                                      display=project.display,
                                      clock=project.clock)

        runtime = project.runtime
        activate = getattr(runtime, "activate", None)
        if callable(activate):
            activate(project)
        if project.ui is None and runtime is not None:
            project.ui = getattr(runtime, "ui", None)

        if program is not None:
            # the host the program was built with is a snapshot; the loop and
            # its UI landed on the project afterwards, so they are attached now
            # - and after ``activate``, so ``host.ui`` is the built context,
            # not the ``None`` the project carried in from an earlier phase
            host = project.host_object()
            if getattr(program, "host", None) is not None:
                program.host.runtime = host.runtime
                program.host.ui = host.ui
            project.attach_program(program)
            self.detail(name, f"{getattr(program, 'name', 'the script')} is the "
                              f"program Pd and audio callbacks return to")

        self.detail(name, f"{project.mode} at {project.fps} fps")
        audio = project.audio
        backend = getattr(audio, "backend", None)
        # "open" is the runtime's answer, not the presence of a backend object:
        # a terminal run has one it was handed and never opened a device on
        if audio is not None and getattr(audio, "opened", False):
            self.detail(name, f"audio {getattr(backend, 'name', '?')} "
                              f"open, not started")
        else:
            self.detail(name, "audio not open for this run")
        if project.pd is not None:
            self.detail(name, "patches "
                              + (", ".join(sorted(project.pd.patches))
                                 or "none"))
        self.detail(name, f"handed to {type(runtime).__name__}" if runtime
                    else "the entry script has not run yet; the loop does that")
        project.active = True


# --------------------------------------------------------------------------
# the calls a caller outside this package makes
# --------------------------------------------------------------------------


def open_project(path: str, *, options: Optional[LoadOptions] = None,
                 **inject: Any) -> Project:
    """Read ``path``'s manifest into a :class:`Project`; load nothing yet."""
    return Project.open(path, options=options, **inject)


def load_project(path: str, reporter: Optional[Reporter] = None, *,
                 options: Optional[LoadOptions] = None,
                 token: Optional[CancelToken] = None, canvas: Any = None,
                 stream: Any = None, **inject: Any) -> Project:
    """Run every phase of ``path`` and hand back the project that holds the result.

    The project is returned rather than the report because a caller - the CLI, a
    test - wants the host the phases filled in as well as what they said; the
    report is on it as ``project.report``, and the failure is raised.
    """
    project = Project.open(path, options=options, **inject)
    project.load(reporter, token=token, canvas=canvas, stream=stream)
    return project


def check_project(path: str, reporter: Optional[Reporter] = None, *,
                  options: Optional[LoadOptions] = None, stream: Any = None,
                  canvas: Any = None, **inject: Any) -> Project:
    """Run only the phases that need no hardware: ``crude check``."""
    project = Project.open(path, options=options, **inject)
    project.check(reporter, stream=stream, canvas=canvas)
    return project


def _shape(data: Any) -> str:
    """How a preset's values look in one word, for a report line."""
    if isinstance(data, (list, tuple)):
        return f"{len(data)} value(s)"
    if isinstance(data, dict):
        return f"{len(data)} key(s)"
    return type(data).__name__
