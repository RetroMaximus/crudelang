"""The frame loop: the plan's tick order, run once or sixty times a second.

:mod:`crude.project.loader` builds a host and marks it active; nothing before
this module ever *ran* it. A :class:`Runtime` is that running: one project, one
clock, and the plan's tick order repeated until something stops it -

    pump input → drain PdInbox → drain serial inbox → drain MIDI inbox →
    run ``ON_FRAME`` handlers → dispatch UI events → draw → swap → pace

with shutdown in the plan's order too: stop the stream, close the patches,
let the project's undo stack put the rest back, and the window last. The loop
owns only the window and the tick; every closer it needs is already recorded on
the project by the phase that opened the thing.

The loop is built entirely out of calls that already existed:
:meth:`~crude.pd.runtime.PdRuntime.dispatch` and
:meth:`~crude.serial.runtime.SerialRuntime.dispatch` drain the inboxes,
:meth:`~crude.ui.context.UIContext.frame` is the whole draw half (its
``dt=`` puts ``ON_FRAME`` exactly where the order says),
:``Display.pump`` answers "is the window still here", and
:meth:`~crude.audio.runtime.AudioRuntime.tick` renders the blocks a pulled
stream would have rendered on its own thread. Nothing here imports GLFW at
module scope: :func:`open_display` reaches :mod:`crude.ui.gl` inside the call,
and a machine with no driver simply gets ``owned=False`` and a reason.

A ``terminal`` run does not loop at all (see :mod:`crude.runtime.modes`): the
script speaks, the report finishes, the process answers. That is why
:meth:`run` returns without a single frame when the mode says no, and why a
bounded ``run(max_frames=N)`` is the test's way through ``headless`` and
``window`` - a script that never calls ``EXIT`` is ordinary here, and only a
closed window or a frame bound stops the tick on a machine with no person.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

from ..errors import EXIT_OK, EXIT_RUNTIME, ExitRequest
from .clock import Clock
from .modes import Mode, explain, possible


class RuntimeError_(Exception):
    """The loop could not run: exit code 5, the plan's *runtime* class.

    Named with an underscore because ``RuntimeError`` belongs to Python; a
    ``NameError`` under a script's ``ON_FRAME`` is a script problem, and only
    this class means "the machine could not do what the mode asked".
    """

    exit_code = EXIT_RUNTIME


class Runtime:
    """One project, run: the tick order above, paced by the clock it was given."""

    def __init__(self, project: Any = None, *, mode: str = None,
                 clock: Clock = None, display: Any = None, ui: Any = None,
                 blocks: int = None, budget: float = None,
                 log: Callable[[str], None] = None) -> None:
        self.project = project
        #: the mode this run answers to. Given here it wins over the project's;
        #  a *change* to it re-reads the project's undo stack, because the CLI
        #  applies its own downgrade before the load even starts
        self._mode = Mode.of(
            mode if mode is not None else getattr(project, "mode", "terminal"))
        #: the loop paces the clock it is given, and reads frames from it, so a
        #  FakeClock test sees virtual time and never a real millisecond
        self.clock = clock if clock is not None else \
            getattr(project, "clock", None) or Clock()
        #: a window handed in is a window taken over - the splash's own, most
        #  of all, since the plan says there is no second one on a project
        self._display = display
        #: ...and a display the loop opened itself is the loop's to close
        self.display_owned = display is not None
        #: ``blocks=0`` means no audio in the tick at all; ``None`` says the
        #  stream's own numbers decide how many a frame is worth (see below)
        self.blocks_per_frame = blocks
        #: the plan's budget for one frame; ``None`` reads ``BUDGET_MS``
        self.budget = budget
        self.log = log or getattr(project, "note", None) or (lambda t: None)

        # -- state -------------------------------------------------------
        self.ui = ui
        self.active = False
        #: whether :meth:`activate` has already wired this loop once
        self.activated = False
        self.running = False
        self.closed = False
        #: whether the window said it is gone: the loop's own reason to stop
        self.window_gone = False
        self.exit_code = EXIT_OK
        self.frames = 0
        self.ticks = 0
        #: when the last frame began, on the clock's own seconds
        self._last = 0.0
        self.events = 0
        self.serial_events = 0
        self.midi_events = 0
        self.frame_reports: List[Any] = []
        self.budget_hits = 0
        self.paces = 0.0
        self.notes: List[str] = []

    # -- the mode ---------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode.name

    @mode.setter
    def mode(self, name: str) -> str:
        self._mode = Mode.of(name)
        project = self.project
        if project is not None:
            try:
                project.mode = self._mode.name
            except (AttributeError, TypeError):  # a host without a mode field
                pass
        return self._mode.name

    @property
    def wants_loop(self) -> bool:
        """Does this mode pump frames at all? ``terminal`` says no."""
        return self._mode.loops

    # -- wiring -----------------------------------------------------------
    def activate(self, project: Any = None) -> "Runtime":
        """Take over a project: its window if there is one, the UI it implies.

        Called by phase 15 (and by any caller that hands a runtime in), this
        decides the mode - asking :mod:`crude.runtime.modes` whether a window
        is *possible* here, and downgrading with a message when it is not -
        builds the :class:`~crude.ui.context.UIContext` the ``WIDGET`` bindings
        reach through ``host.ui``, and hands the loop back to the project so
        the script's ``MODE()`` and the CLI's stats see the same object.

        It starts nothing: the phase's own rule is that a stream which begins
        rendering before the script has wired its patches plays silence loudly.
        """
        if project is not None and project is not self.project:
            self.project = project
            if self.clock is None:
                self.clock = project.clock
        project = self.project
        if project is None:
            raise RuntimeError_("there is no project to run")
        if self.activated:
            return self
        self.activated = True

        if self._mode.wants_window and self._display is None:
            display, reason = open_display(project)
            if display is None:
                self.mode, _ = possible(
                    project.mode, has_display=False, reason=reason)
                self.notes.append(explain("window", reason))
                self.log(self.notes[-1])
            else:
                self._display = display
                self.display_owned = True
                try:
                    project.record("the window the loop opened", display.close)
                except AttributeError:  # a host that records nothing
                    pass

        # every mode gets a UI: ``terminal`` does not paint or dispatch, but
        # the widget bindings still need a ``host.ui`` to place a tree into,
        # and the plan's parity line says the same script runs in every mode
        if self.ui is None:
            self.ui = self._build_ui()
        self._seed_scenes(self.ui)
        if project is not None:
            try:
                project.runtime = self
            except (AttributeError, TypeError):
                pass
        self.active = True
        return self

    def deactivate(self) -> "Runtime":
        """Stop answering to the project, leaving the close to do the rest."""
        if self.running:
            self.stop()
        self.active = False
        return self

    # -- the halves a run is made of --------------------------------------
    @property
    def display(self) -> Any:
        return self._display

    def _build_ui(self) -> Any:
        """The context this run draws through - window or parity, never both.

        ``window`` takes over the display it was given (or opened): its
        renderer, its swap, its size, wired back as the window's listener.
        ``headless`` gets a :class:`~crude.ui.null_renderer.NullRenderer`, a
        client rect out of the manifest, and no swap: the plan's parity line -
        the same tree, the same events, the same maths, zero ops on screen.
        """
        from crude.ui.context import UIContext, window_context

        painting = bool(self._mode.paints)
        if self._display is not None:
            renderer = getattr(self._display, "renderer", None) \
                if painting else None
            if renderer is None:
                if painting:
                    self._fallback_renderer()
                renderer = self._null_renderer()
            # ``Display`` is a wrapper over the GLFW ``Window``; the client rect,
            # the swap that presents a frame and the input listener all live on
            # that window, not on the wrapper. Handing window_context the
            # wrapper left ``swap`` unresolved, so a frame was drawn and never
            # shown - and the pointer never reached the tree. Unwrap it, and
            # fall back to the object itself for a fake that already is a window.
            window = getattr(self._display, "window", self._display)
            return window_context(window, renderer)

        renderer = self._gl_renderer_or_null() if painting else \
            self._null_renderer()
        painting = painting and not _is_null(renderer)
        return UIContext(renderer, skin=self._theme(), painting=painting,
                         **self._rect())

    def _null_renderer(self):
        from crude.ui.null_renderer import NullRenderer

        return NullRenderer()

    def _gl_renderer(self):
        from crude.ui.gl import make_renderer

        return make_renderer()

    def _gl_renderer_or_null(self):
        try:
            return self._gl_renderer()
        except Exception:  # a driver that says no: parity rather than failure
            return self._null_renderer()

    def _fallback_renderer(self):
        """Say out loud that a window was promised and cannot be honoured."""
        self.notes.append("there is no GL renderer for this window, so the "
                          "frame will not paint")
        self.log(self.notes[-1])
        return None

    def _project_field(self, name: str, default: Any = None) -> Any:
        project = self.project
        return getattr(project, name, default) if project is not None else default

    def _theme(self) -> Optional[str]:
        manifest = self._project_field("manifest")
        ui = getattr(manifest, "ui", None)
        return getattr(ui, "theme", None)

    def _rect(self) -> dict:
        manifest = self._project_field("manifest")
        runtime = getattr(manifest, "runtime", None)
        width = getattr(runtime, "width", None)
        height = getattr(runtime, "height", None)
        return {} if not width or not height else {
            "width": float(width), "height": float(height)}

    def _scenes(self) -> Tuple[str, ...]:
        """The scene names ``ui.workspaces`` said, or none: names and order only."""
        manifest = self._project_field("manifest")
        ui = getattr(manifest, "ui", None)
        return tuple(getattr(ui, "workspaces", None) or ())

    def _seed_scenes(self, ui: Any) -> None:
        """Reserve the manifest's scene names on the context, before the script runs.

        Advisory exactly like ``midi`` is: a project that lists scenes gets those
        numbers held from frame one, so a ``View`` menu and a script that declares
        them in another order still agree, and a project that lists a scene it never
        draws has an empty page rather than a load failure. A host UI that is not a
        real context has no ``workspaces`` to ask, and is left alone.
        """
        names = self._scenes()
        if not names:
            return
        seed = getattr(getattr(ui, "workspaces", None), "seed", None)
        if not callable(seed):
            return
        try:
            seed(names)
        except Exception as exc:  # a name the window cannot take: said, not fatal
            self.notes.append(f"workspace declaration skipped: {exc}")
            self.log(self.notes[-1])

    # -- the audio side of a frame ----------------------------------------
    def blocks_for_frame(self) -> int:
        """How many stream blocks one frame is worth when nobody counted them.

        A pulled stream has no callback thread, so the loop's tick *is* it: the
        honest answer is the one a real callback would give, ``sample_rate /
        (fps × block_size)`` blocks a frame. The loop reads the stream's own
        numbers, so ``--fps`` and the manifest's block size both move it, and
        ``max(1, ...)`` keeps a frame rendering at least one block: a headless
        run that renders nothing is a headless run with no audio.
        """
        if self.blocks_per_frame is not None:
            return max(int(self.blocks_per_frame), 0)
        audio = self._project_field("audio")
        config = getattr(audio, "config", None)
        fps = int(getattr(self.clock, "target_fps", 0) or 0)
        if config is None or fps <= 0:
            return 1
        block = max(int(config.block_size), 1)
        return max(1, round(int(config.sample_rate) / (fps * block)))

    def start_audio(self) -> None:
        """Let the stream run, when this mode says it should.

        A live backend is started for real; a pulled one only gets the same
        ``running`` answer - its blocks are rendered by :meth:`step`, which is
        where a headless run's audio actually happens.
        """
        audio = self._project_field("audio")
        if audio is None or not self._mode.audio_started:
            return
        if getattr(audio, "running", False):
            return
        if not getattr(audio, "live", False):
            # A pulled (null) backend has no OS thread to start: its blocks are
            # rendered by :meth:`tick_audio` on the loop's own beat, so nothing
            # is gained by calling ``start`` and it would double the meters.
            return
        try:
            audio.start()
        except Exception as exc:  # a stream that will not run is not no stream
            self.notes.append(f"audio could not run: {exc}")
            self.log(self.notes[-1])

    def stop_audio(self) -> None:
        """The plan's first shutdown step, whether the loop started it or not."""
        audio = self._project_field("audio")
        if audio is None:
            return
        try:
            audio.stop()
        except Exception as exc:  # pragma: no cover - a stream that fights back
            self.notes.append(f"audio could not stop: {exc}")

    # -- the tick ----------------------------------------------------------
    def pump(self) -> bool:
        """Feed the OS to the window; ``False`` once the person has closed it."""
        display = self._display
        if display is None:
            return True
        try:
            alive = bool(display.pump())
        except Exception as exc:  # a window that dies loudly is still gone
            self.log(f"the window stopped answering: {exc}")
            alive = False
        if not alive:
            self.window_gone = True
        return alive

    def drain_pd(self) -> int:
        """Deliver everything the patches sent since the last frame."""
        pd = self._project_field("pd")
        if pd is None:
            return 0
        return int(pd.dispatch())

    def drain_serial(self) -> int:
        """Deliver everything the links read since the last frame."""
        serial = self._project_field("serial")
        dispatch = getattr(serial, "dispatch", None)
        if not callable(dispatch):  # no links, or a host that cannot deliver
            return 0
        return int(dispatch())

    def drain_midi(self) -> int:
        """Deliver everything the MIDI ports received since the last frame.

        The same shape as :meth:`drain_serial` and for the same reason: a
        backend's own thread only ever queues, and the interpreter gets the
        messages here, one drain per tick. A project with no ``midi`` section
        has no runtime to ask, so this is a plain zero and says nothing.
        """
        midi = self._project_field("midi")
        dispatch = getattr(midi, "dispatch", None)
        if not callable(dispatch):  # no ports, or a host that cannot deliver
            return 0
        return int(dispatch())

    def step(self) -> Optional[Any]:
        """One frame in the plan's order; its report, or ``None`` when skipped.

        A window that says it is gone stops the tick *before* any of it runs:
        drawing into a closed window is the bug the plan's pump-first order
        exists to avoid. The report is the
        :class:`~crude.ui.context.FrameReport` when there is a UI, and it is
        the one place the plan's 2 ms budget is asked (and warned) per frame.
        """
        if not self.pump():
            return None
        self.drain_pd()
        self.serial_events += self.drain_serial()
        self.midi_events += self.drain_midi()

        started = self.clock.now()
        dt = max(0.0, started - self._last)
        self._last = started

        report = None
        if self.ui is not None:
            report = self.ui.frame(dt=dt)
            self.events += report.events
            self.frame_reports.append(report)
            if report.over_budget(self._budget()):
                self.budget_hits += 1
                if self.budget_hits == 1 or not self.clock.live:
                    self.log(f"frame {report.frames} over budget: "
                             f"{report.ms:.2f} ms")

        self.tick_audio()
        self.pace()
        self.clock.frame()
        self.frames += 1
        self.ticks += 1
        return report

    def _budget(self) -> float:
        if self.budget is not None:
            return float(self.budget)
        from crude.ui.context import BUDGET_MS

        self.budget = float(BUDGET_MS)
        return self.budget

    def tick_audio(self) -> None:
        """Render the frame's blocks, if anybody is pulling them.

        A stream that was never opened is not a stream to tick: a run that
        said ``--no-audio`` (or a manifest that said ``enabled: false``) has
        no blocks in any frame, and the loop says nothing rather than naming
        an absence every frame.
        """
        audio = self._project_field("audio")
        blocks = self.blocks_for_frame()
        if audio is None or blocks <= 0 or not getattr(audio, "opened", False):
            return
        try:
            audio.tick(blocks)
        except Exception as exc:  # a block that fails is named, not fatal
            self.log(f"audio tick failed: {exc}")

    def pace(self) -> float:
        """Hold the frame to the rate the run aims at; the seconds it held.

        Only a live clock paces: a test's FakeClock moves virtual time on the
        moment its ``frame()`` is called, so sleeping toward a virtual deadline
        would either wait for nothing or spin forever.
        """
        fps = int(getattr(self.clock, "target_fps", 0) or 0)
        if not self._mode.frame_paced or fps <= 0 or not self.clock.live:
            return 0.0
        span = 1.0 / fps
        hold = span - (self.clock.now() - self._last)
        if hold <= 0:
            return 0.0
        self.paces += hold
        return float(self.clock.sleep(hold))

    # -- start, stop, run ---------------------------------------------------
    def start(self) -> "Runtime":
        """Bring the loop up without running a frame of it yet."""
        if not self.active:
            self.activate()
        if self.project is None:
            raise RuntimeError_("there is no project to run")
        self.running = True
        self.closed = False
        self._last = self.clock.now()
        self.start_audio()
        return self

    def stop(self, code: int = None) -> "Runtime":
        """Answer the tick's over: the plan's shutdown order, in the project."""
        self.running = False
        if code is not None:
            self.exit_code = int(code)
        if self.project is None:
            self.closed = True
            return self
        self.stop_audio()
        try:
            self.project.shutdown()
        except AttributeError:  # a bare host: nothing recorded, nothing to undo
            pass
        self.close_display()
        self.active = False
        self.activated = False
        self.closed = True
        return self

    def close_display(self) -> None:
        """glfw's turn, last: only a window the loop opened is the loop's to close."""
        display, self._display = self._display, None
        if display is not None and self.display_owned:
            try:
                display.close()
            except Exception as exc:  # pragma: no cover - a window that fights
                self.notes.append(f"the window would not close: {exc}")
        self.ui = None

    def run(self, max_frames: int = None) -> int:
        """The loop, until something says enough; the process's exit code.

        Four things stop it, in the order they can happen: the window closed,
        a frame bound reached (``max_frames``, the way a test walks a headless
        run without a real second going by), the script's ``EXIT`` - which
        counts as a frame handler raising :class:`~crude.errors.ExitRequest`,
        landing its code on the project as much as on the loop - and a person
        pressing Ctrl-C, which is the plan's cancel, not a crash.

        A ``terminal`` mode never enters the loop at all: the script has already
        run, and the answer is the code it left behind.
        """
        project = self.project
        if not self.wants_loop:
            self.exit_code = int(getattr(project, "exit_code", self.exit_code))
            return self.exit_code
        self.start()
        limit = None if max_frames is None else max(int(max_frames), 0)
        try:
            while self.running:
                if limit is not None and self.frames >= limit:
                    break
                self.step()
                if self.window_gone:
                    break
        except ExitRequest as request:
            self.exit_code = request.code
            if project is not None:
                try:
                    project.exit_code = request.code
                except (AttributeError, TypeError):
                    pass
            if request.reason:
                self.log(request.reason)
        except KeyboardInterrupt:
            self.log("stopped by the user")
        finally:
            self.stop()
        return self.exit_code

    # -- reporting -----------------------------------------------------------
    def stats(self) -> dict:
        """What this run amounts to, for ``--stats`` and for a test's assertion."""
        audio = self._project_field("audio")
        return {
            "mode": self.mode,
            "frames": self.frames,
            "ticks": self.ticks,
            "events": self.events,
            "serial": self.serial_events,
            "midi": self.midi_events,
            "budget_hits": self.budget_hits,
            "paced": round(self.paces, 6),
            "audio_ticks": getattr(audio, "ticks", 0),
            "fps": round(self.clock.fps(), 3) if self.clock is not None else 0.0,
            "seconds": round(self.clock.now(), 6) if self.clock is not None else 0.0,
            "window_gone": self.window_gone,
            "exit_code": self.exit_code,
        }

    def describe(self) -> str:
        said = (f"runtime {self.mode}: {self.frames} frame(s), "
                f"{self.events} event(s)")
        if self.window_gone:
            said += ", window closed"
        if self.budget_hits:
            said += f", {self.budget_hits} frame(s) over budget"
        return said

    def __str__(self) -> str:
        return self.describe()

    def __repr__(self) -> str:
        return (f"<Runtime {self.mode} frames={self.frames} "
                f"running={self.running}>")

    # -- context manager -----------------------------------------------------
    def __enter__(self) -> "Runtime":
        self.activate()
        return self

    def __exit__(self, *exc_info) -> bool:
        self.stop()
        return False


def _is_null(renderer: Any) -> bool:
    from crude.ui.null_renderer import NullRenderer

    return isinstance(renderer, NullRenderer)


# --------------------------------------------------------------------------
# the one call that touches a driver
# --------------------------------------------------------------------------


def open_display(project: Any, *, title: str = None,
                 vsync: bool = None) -> Tuple[Any, str]:
    """The window mode's display, or the reason this machine cannot have one.

    Everything GL lives inside :func:`crude.ui.gl.open_display` - GLFW, a
    context, a renderer - and every answer here is the same shape: the thing
    that opens, and the sentence that says why it did not. The caller decides
    whether that sentence is a downgrade (:func:`crude.runtime.modes.explain`
    has the words) or a failure; this call never raises.
    """
    manifest = getattr(project, "manifest", None)
    runtime = getattr(manifest, "runtime", None)
    width = int(getattr(runtime, "width", 800) or 800)
    height = int(getattr(runtime, "height", 600) or 600)
    if vsync is None:
        vsync = bool(getattr(runtime, "vsync", True))
    from crude.ui.base import WindowUnavailable
    from crude.ui.gl import open_display as _open

    try:
        return _open(width, height,
                     title or getattr(project, "name", "crude"),
                     vsync=vsync), ""
    except WindowUnavailable as exc:
        return None, str(exc)
    except Exception as exc:  # a driver that says no in its own words
        return None, f"the window could not be opened: {exc}"


__all__ = ["Runtime", "RuntimeError_", "open_display"]
