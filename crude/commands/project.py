"""``crude run``, ``crude check`` and ``crude init``: a project from a shell.

Three commands, three questions:

``run``
    Load every phase, run the entry script, and end with the code that script
    asked for. ``--mode``/``--no-audio``/``--backend``/``--no-serial``/
    ``--progress``/``--fps``/``--log``/``--rebuild-pd`` override the manifest for
    this one run and change nothing on disk.
``check``
    The four phases that need no hardware - manifest, environment, resources,
    script - listed step by step, and ``--json`` for a machine to read.
``init``
    Write a project that loads: a manifest the validator accepted, and a script
    that says something when it runs.

Every failure is one line on stderr plus an exit code that says *what class* of
thing went wrong: 2 manifest, 3 load, 4 script, 5 runtime, taken from the
exception rather than parsed out of its text. A command line mistake (an unknown
``--mode``) is argparse's own usage error, which also ends at 2: both mean "what
you asked for is not a thing".
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from ..cli import EXIT_MANIFEST, EXIT_OK, command, configure_parser
from ..errors import CancelError, CrudeError, EXIT_RUNTIME, ManifestError
from ..project import (
    AUTO,
    CONSOLE,
    JSON,
    MANIFEST_NAME,
    MODES,
    SPLASH,
    SPECS,
    CancelToken,
    ConsoleReporter,
    JsonReporter,
    LoadOptions,
    Manifest,
    NullReporter,
    Project,
    Reporter,
    build_manifest,
    known_backends,
    known_midi_backends,
    make_reporter,
    open_project,
)
from ..ui.base import WindowUnavailable

#: the levels ``--log`` accepts, in the order worth showing in a help message
LOG_NAMES = ("none", "error", "warn", "warning", "info", "debug")


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


@command("run", "load a project, run its entry script, exit with its code")
def run_command(args: argparse.Namespace) -> int:
    """Bring the project up, let the script speak, run its frames, put everything back.

    The project is shut down whether the run worked or not: a load that failed
    half way has already unwound itself, and a script that called ``EXIT`` has
    chosen the code this returns. Between the script and the answer sits the
    loop: ``terminal`` walks past it (its mode does not pump frames), a
    ``headless`` or ``window`` run ticks until the window closes, until the
    ``--frames`` bound the CI hands it, or until the script says ``EXIT``.
    """
    project: Optional[Project] = None
    try:
        try:
            project = open_project(args.project, options=_options(args))
            reporter = _reporter(project)
            project.load(reporter, token=CancelToken())
            said = len(project.notes)
            try:
                project.run_script()
            finally:
                # what a script managed to say before it failed is still the
                # person's output, and is often the only clue to the error
                _speak(project, reporter, said)
            code = _loop(project, args)
            if args.stats:
                print(f"stats     {project.clock}")
                if project.runtime is not None:
                    print(f"stats     {project.runtime}")
            return code
        except KeyboardInterrupt:
            raise CancelError("stopped by the user")
        return project.exit_code
    except CrudeError as exc:
        return _error("run", exc)
    finally:
        if project is not None:
            _close(project, "run")


def _loop(project: Project, args: argparse.Namespace) -> int:
    """Enter the frame loop - the whole tick order, or none of it.

    A script that ended with ``EXIT`` has already chosen the code and stopped
    the run before its first frame: the loop is not asked to change a
    conversation the script already finished. ``--frames`` is how a headless
    or window project is run *bounded* - a test, or a machine with no person
    at the keyboard - and a terminal mode answers through :meth:`run` without
    pumping a frame at all.
    """
    runtime = project.runtime
    if runtime is None or project.exit_code != EXIT_OK:
        return project.exit_code
    project.exit_code = runtime.run(max_frames=args.frames)
    return project.exit_code


def _options(args: argparse.Namespace) -> LoadOptions:
    """The command line's answers, with everything it did not say left unset."""
    return LoadOptions(
        mode=args.mode,
        audio=args.audio,
        backend=args.backend,
        serial=False if args.no_serial else None,
        midi=False if args.no_midi else None,
        midi_backend_name=args.midi_backend,
        progress=args.progress,
        fps=args.fps,
        log=args.log,
        rebuild_pd=args.rebuild_pd,
    )


def _reporter(project: Project) -> Reporter:
    """The progress this run asked for, drawn on whatever can show it.

    A splash needs a window, and a window needs GLFW, a driver and a display -
    none of which a machine reached over SSH has. This is the plan's "auto-downgrade
    with a message when a mode is impossible": say on stderr what could not be
    done, and carry the report on the terminal, because losing the pretty half of a
    progress report is no reason to lose the load.
    """
    spec = project.options.progress_spec(project.manifest)
    canvas = _splash(project, spec)
    return make_reporter(spec, mode=project.mode, canvas=canvas)


def _wants_splash(project: Project, spec: str) -> bool:
    """Was a window asked for, in words or by the mode?"""
    if spec == SPLASH:
        return True
    return spec == AUTO and project.mode == "window"


def _open_display(project: Project) -> Tuple[Any, str]:
    """The window this run draws on, and the reason there is not one.

    The manifest's own size and ``vsync`` are used, which is what lets the frame
    loop take over the same window instead of opening a second one. Nothing here
    imports GL at module scope: :mod:`crude.ui.gl` is what puts GLFW and PyOpenGL
    behind a call, and this is that call.
    """
    from crude.ui.gl import open_display

    runtime = project.manifest.runtime
    try:
        return open_display(runtime.width, runtime.height, project.name,
                           vsync=runtime.vsync), ""
    except WindowUnavailable as exc:
        return None, str(exc)
    except Exception as exc:  # a driver that says no in its own words
        return None, f"the window could not be opened: {exc}"


def _splash(project: Project, spec: str) -> Optional[Any]:
    """A canvas to draw on, or ``None`` - with the reason said out loud.

    A window run needs a display whether or not the person asked for a splash
    by name: S11's frame loop takes over whatever is on ``project.display``,
    and a run that opened a second one for the tick would fight the first for
    the same keys. So a ``window`` mode opens the display here once, and the
    canvas is only handed back when the progress spec actually asked for one.

    A display that cannot be opened is the plan's *auto-downgrade with a
    message*: the mode drops a step, :func:`crude.runtime.explain` says why on
    stderr, and the loader's phase 14 finds the new mode already set and does
    not reach for a window a second time.
    """
    want_canvas = _wants_splash(project, spec)
    if project.mode != "window" and not want_canvas:
        return None
    if project.display is not None:
        # a caller already opened the window (a replay, a re-run); reuse it
        if not want_canvas:
            return None
        from crude.ui.splash import SplashCanvas

        return SplashCanvas(project.display.renderer, project.display)
    display, reason = _open_display(project)
    if display is None:
        if project.mode == "window":
            from ..runtime import downgrade, explain

            print(explain(project.mode, reason), file=sys.stderr)
            project.mode = downgrade(project.mode)
            try:
                project.options.mode = project.mode
            except AttributeError:  # a caller that has no options on it
                pass
        else:
            print(f"crude run: {reason}, so this run reports progress on the "
                  f"terminal", file=sys.stderr)
        return None
    project.display = display
    project.record("the window", display.close)
    if not want_canvas:
        return None
    from crude.ui.splash import SplashCanvas

    return SplashCanvas(display.renderer, display)


def _speak(project: Project, reporter: Reporter, count: int) -> None:
    """What the project said since ``count``, on the stream the run is using.

    ``LOG`` is how a terminal project prints, so those lines are stdout. Only a
    JSON run is different: a bare line in the middle of a stream of objects would
    stop the reader, so the words go into an event of their own.
    """
    notes = project.notes[count:]
    if isinstance(reporter, JsonReporter):
        for text in notes:
            reporter.emit({"event": "note", "text": text})
        return
    for text in notes:
        print(text)


def _close(project: Project, what: str) -> List[str]:
    """Put the project back, and mention on stderr anything that would not close."""
    messages = project.shutdown()
    for message in messages:
        print(f"crude {what}: {message}", file=sys.stderr)
    return messages


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------


@command("check", "validate a project and list its load steps, with no device and no window")
def check_command(args: argparse.Namespace) -> int:
    """Run only the phases that need no hardware, and say what each one found.

    The plan of a check is the interesting part: the steps it lists are exactly
    the ones a ``run`` of this project would take first, and the ones it does not
    mention are the ones that would have opened something.
    """
    options = LoadOptions(progress=JSON if args.json else CONSOLE)
    try:
        project = open_project(args.project, options=options)
    except CrudeError as exc:
        return _error("check", exc)

    reporter: Reporter
    if args.json:
        reporter = JsonReporter(sys.stdout)
    else:
        reporter = ConsoleReporter(sys.stdout, show_plan=True)

    code = EXIT_OK
    try:
        project.check(reporter)
    except CrudeError as exc:
        code = _error("check", exc)

    if args.json:
        reporter.emit({"event": "project", "lines": project.describe(),
                       "project": project.info()})
    else:
        for line in project.describe():
            print(line)
        _speak(project, reporter, 0)
    _close(project, "check")
    return code


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------


@command("init", "write a starter project: a manifest and an entry script")
def init_command(args: argparse.Namespace) -> int:
    """Create a project directory that the very next command can check.

    The manifest is validated *before* it is written, so ``init`` cannot leave a
    project that ``check`` would refuse, and a name that cannot be a project id is
    the manifest validator's own error, with its pointer and exit code 2.
    """
    directory = os.path.abspath(os.path.join(args.dir, args.name))
    try:
        manifest = build_manifest(_template(args.name, args.mode), root=directory)
    except ManifestError as exc:
        return _error("init", exc)

    if not args.force:
        for existing in (manifest.path, manifest.entry_path):
            if os.path.isfile(existing):
                return _fail("init", f"'{existing}' is already there; --force "
                                     f"writes over it", EXIT_MANIFEST)

    try:
        _write_project(manifest, _starter(manifest))
    except OSError as exc:
        return _fail("init", f"the project could not be written: "
                             f"{exc.filename or directory}: {exc.strerror or exc}",
                     EXIT_RUNTIME)

    print(f"created project '{manifest.id}' in {directory}")
    print(f"  {MANIFEST_NAME}")
    print(f"  {manifest.entry}")
    print(f"next: python -m crude check {directory}")
    return EXIT_OK


def _template(name: str, mode: str) -> Dict[str, Any]:
    """What ``init`` asks the validator to build.

    Deliberately three keys: the defaults a project does not mention are written
    out into the file anyway, so a person can read every knob next to their name.
    """
    return {
        "id": name,
        "entry": "main.crude",
        "runtime": {"mode": mode},
    }


def _write_project(manifest: Manifest, source: str) -> None:
    """The two files that make a directory a project."""
    os.makedirs(os.path.dirname(manifest.entry_path), exist_ok=True)
    with open(manifest.path, "w", encoding="utf-8") as handle:
        handle.write(manifest.to_json() + "\n")
    with open(manifest.entry_path, "w", encoding="utf-8") as handle:
        handle.write(source)


def _starter(manifest: Manifest) -> str:
    """The entry script ``init`` writes: something that runs in every mode."""
    return (
        f"# main.crude: the entry script of '{manifest.id}'.\n"
        "#\n"
        "# LOG writes to the terminal and to the project's log file, MODE() and\n"
        "# FPS() say what this run is, and RES/PRESET_LOAD reach the files the\n"
        "# manifest declares. Widgets come with the window stage; until then\n"
        "# every project runs the same script.\n"
        "\n"
        f'LOG("hello from {manifest.id}, running in " + MODE() + " mode")\n'
        "VAR beat = 0\n"
        "WHILE beat < 4 THEN\n"
        "    VAR beat = beat + 1\n"
        '    LOG("tick " + STR(beat))\n'
        "END\n"
        "\n"
        "# A script that ends without EXIT leaves the process to answer 0.\n"
    )


# --------------------------------------------------------------------------
# the argument wiring
# --------------------------------------------------------------------------


def _positive(text: str) -> int:
    """``--fps``: a whole number of frames a second, and at least one.

    Checked here rather than left to ``LoadOptions``, because ``--fps 0`` would
    read as "not said" there and quietly keep the manifest's rate.
    """
    try:
        number = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"'{text}' is not a whole number of frames a second")
    if number < 1:
        raise argparse.ArgumentTypeError(
            f"'{text}' is not a frame rate; use 1 or more")
    return number


def _frames(text: str) -> int:
    """``--frames``: a whole number of ticks, and zero is a real answer.

    ``0`` is what a test that wants the load and the shutdown but not a single
    frame hands in; a negative bound is a mistake, not a way of saying "no
    limit" (that is what leaving ``--frames`` off means).
    """
    try:
        number = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"'{text}' is not a whole number of frames")
    if number < 0:
        raise argparse.ArgumentTypeError(
            f"'{text}' is not a frame count; use 0 or more, or leave it off")
    return number


@configure_parser("run")
def run_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project", nargs="?", default=".",
                        help="the project to run (default: the current directory)")
    _add_overrides(parser)
    parser.add_argument("--frames", type=_frames, default=None, metavar="N",
                        help="pump N frames and stop (0 runs the load and the "
                             "shutdown without a single tick; a window run "
                             "without this ticks until its window closes)")
    parser.add_argument("--stats", action="store_true",
                        help="print what the clock measured when the script ended")


@configure_parser("check")
def check_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project", nargs="?", default=".",
                        help="the project to check (default: the current directory)")
    parser.add_argument("--json", action="store_true",
                        help="one JSON object per line, instead of the terminal's")


@configure_parser("init")
def init_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", help="the directory, and the id, to create")
    parser.add_argument("--dir", default=".",
                        help="where to create it (default: the current directory)")
    parser.add_argument("--mode", choices=MODES, default="terminal",
                        help="the mode the new project starts in "
                             "(default: terminal, which needs no display)")
    parser.add_argument("--force", action="store_true",
                        help="write over an existing manifest and entry script")


def _add_overrides(parser: argparse.ArgumentParser) -> None:
    """The flags that override the manifest for one run."""
    parser.add_argument("--mode", choices=MODES, default=None,
                        help="override runtime.mode for this run")
    sound = parser.add_mutually_exclusive_group()
    sound.add_argument("--audio", dest="audio", action="store_true", default=None,
                       help="open a device even though the manifest does not ask")
    sound.add_argument("--no-audio", dest="audio", action="store_false",
                       help="open no device at all")
    parser.add_argument("--backend", choices=known_backends(), default=None,
                        help="which audio backend to start with")
    parser.add_argument("--no-serial", action="store_true",
                        help="do not open the declared serial links")
    parser.add_argument("--no-midi", action="store_true",
                        help="do not open the declared MIDI backend at all")
    parser.add_argument("--midi-backend", choices=known_midi_backends(),
                        default=None, help="which MIDI backend to start with")
    parser.add_argument("--progress", choices=SPECS, default=None,
                        help="how to report the load steps")
    parser.add_argument("--fps", type=_positive, default=None, metavar="N",
                        help="frame rate to aim at, overriding runtime.target_fps")
    parser.add_argument("--log", choices=LOG_NAMES, default=None, metavar="LEVEL",
                        help="what to write to the project's log file")
    parser.add_argument("--rebuild-pd", action="store_true",
                        help="ignore the cached .pd text and write it again")


# --------------------------------------------------------------------------
# one place where a failure becomes a line and a code
# --------------------------------------------------------------------------


def _fail(what: str, message: str, code: int = EXIT_RUNTIME) -> int:
    """Every CLI failure says what it could not do, on stderr, with an exit code."""
    print(f"crude {what}: {message}", file=sys.stderr)
    return code


def _error(what: str, error: BaseException) -> int:
    """The plan's exit codes, read off the failure instead of parsed out of it.

    A :class:`~crude.errors.ManifestError` says 2 and carries the pointer into the
    file, a load phase's failure says 3, a script says 4, and anything else about
    the machine says 5.
    """
    return _fail(what, str(error), int(getattr(error, "exit_code", EXIT_RUNTIME)))
