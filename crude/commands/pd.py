"""``crude pd``: look at patches without opening Pd.

Three actions, in the order they are useful:

``show``
    Read a ``.pd`` file and print the boxes, the links and whatever the reader
    could not model. Works on any patch, hand-written or generated.

``dump``
    Run a ``.crude`` script and write every patch it authors as ``.pd``. The
    script runs against the in-memory Pd emulation, so no ``libpd`` and no audio
    device are needed - the patch text is decided before Pd is ever asked.

``load``
    Open a ``.pd`` in the real libpd, render a few blocks and report what came
    out. This is the one action that needs the library.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import types
from typing import List

from ..cli import EXIT_LOAD, EXIT_OK, EXIT_RUNTIME, EXIT_SCRIPT, command, configure_parser
from ..pd.cache import slug
from ..pd.model import PatchError
from ..pd.reader import describe, read_file
from ..pd.writer import write_file


@command("pd", "inspect, generate and audition .pd patches")
def pd_command(args: argparse.Namespace) -> int:
    """Dispatch to the action argparse selected."""
    return args.handler(args)


@configure_parser("pd")
def pd_parser(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", metavar="action", required=True)

    show = actions.add_parser("show", help="print a .pd file as a box and link table")
    show.add_argument("file", help="the .pd file to read")
    show.set_defaults(handler=_show)

    dump = actions.add_parser("dump", help="write the patches a script authors")
    dump.add_argument("script", help="the .crude script to run")
    dump.add_argument("--out", default=None,
                      help="directory to write into (default: the current one)")
    dump.set_defaults(handler=_dump)

    load = actions.add_parser("load", help="open a .pd in libpd and measure it")
    load.add_argument("file", help="the .pd file to open")
    load.add_argument("--blocks", type=int, default=4,
                      help="64-frame blocks to render (default 4)")
    load.set_defaults(handler=_load)


# -- show ---------------------------------------------------------------


def _show(args: argparse.Namespace) -> int:
    try:
        doc = read_file(args.file)
    except OSError as exc:
        return _error(f"show: {exc.filename or args.file}: {exc.strerror or exc}")

    for line in describe(doc):
        print(line)
    return EXIT_OK


# -- dump ---------------------------------------------------------------


def _dump(args: argparse.Namespace) -> int:
    from ..lang import Program
    from ..pd import FakeLibpd, PdRuntime, PdSession

    script = os.path.abspath(args.script)
    if not os.path.isfile(script):
        return _error(f"dump: no script '{args.script}'")

    directory = os.path.abspath(args.out or os.getcwd())
    session = PdSession(FakeLibpd())
    runtime = PdRuntime(
        session,
        project_dir=os.path.dirname(script),
        log=lambda message: print(f"  {message}", file=sys.stderr),
    )
    program = Program(script, host=types.SimpleNamespace(pd=runtime))
    runtime.attach_program(program)

    session.start()
    try:
        _value, error = program.run_file(script)
    finally:
        session.shutdown()
    if error is not None:
        return _error(f"dump: {error.as_string()}", EXIT_SCRIPT)

    if not runtime.docs:
        print(f"{os.path.basename(script)} authors no patches "
              f"(PD_BUILD creates them, PD_LOAD would write them)")
        return EXIT_OK

    failures = 0
    for doc in runtime.docs.values():
        target = os.path.join(directory, f"{slug(doc.id)}.pd")
        try:
            print(write_file(doc, target))
        except (PatchError, OSError) as exc:
            # PatchError already names the patch, so the message is not repeated.
            failures += 1
            print(f"dump: {exc}", file=sys.stderr)
    return EXIT_OK if not failures else EXIT_SCRIPT


# -- load ---------------------------------------------------------------


def _load(args: argparse.Namespace) -> int:
    from ..pd import Libpd, LibpdNotFound, PdSession, find_library
    from ..pd.engine import PdError

    path = os.path.abspath(args.file)
    if not os.path.isfile(path):
        return _error(f"load: no patch '{args.file}'")

    try:
        api = Libpd.load(find_library())
    except LibpdNotFound as exc:
        return _error(f"load: {exc}", EXIT_RUNTIME)

    session = PdSession(api)
    try:
        session.start()
        patch = session.open_patch(os.path.splitext(os.path.basename(path))[0],
                                   os.path.basename(path), os.path.dirname(path))
        session.dsp(True)
        samples = session.process_offline(ticks=max(args.blocks, 1))
    except PdError as exc:
        return _error(f"load: {exc}", EXIT_RUNTIME)
    finally:
        # dsp off first: leaving the library running with a closed patch is how
        # a command-line tool ends up printing Pd's errors onto the user's tty.
        if session.dsp_on:
            session.dsp(False)
        session.shutdown()

    print(_measure(samples, session, patch.name, args.blocks))
    return EXIT_OK


def _measure(samples: List[float], session, name: str, blocks: int) -> str:
    channels = max(session.out_channels, 1)
    frames = len(samples) // channels if samples else 0
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) if samples else 0.0
    peak = max((abs(sample) for sample in samples), default=0.0)
    seconds = frames / float(session.sample_rate)
    return (f"{name}: {blocks} block(s), {frames} frames "
            f"({seconds:.4f}s) rms {rms:.6f} peak {peak:.6f}")


def _error(message: str, code: int = EXIT_LOAD) -> int:
    """Every CLI failure says what it could not do, on stderr, with an exit code."""
    print(f"crude pd {message}", file=sys.stderr)
    return code
