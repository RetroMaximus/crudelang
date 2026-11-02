"""Command line entry point: ``python -m crude <command>``.

A command is a function registered in :data:`COMMANDS`, optionally with argument
wiring registered in :data:`PARSERS`. Keeping the table declarative means a
stage adds a file to :mod:`crude.commands` and nothing else: the help text, the
subparser and the dispatch all come from the registration.

Already here: ``pd`` (patch diagnostics), the project commands ``run``, ``check``
and ``init``, and the two introspection commands ``widgets`` and ``glyphs``.
``repl`` lands with its stage.

Usage mistakes - an unknown ``--mode``, a missing action - are argparse's own,
which ends at 2 like a bad manifest does: both say "what you asked for is not a
thing".
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence

from . import __version__

#: Process exit codes, shared by every command.
EXIT_OK = 0
EXIT_MANIFEST = 2
EXIT_LOAD = 3
EXIT_SCRIPT = 4
EXIT_RUNTIME = 5

# command name -> handler taking the parsed arguments
COMMANDS: Dict[str, Callable[[argparse.Namespace], int]] = {}
# command name -> function that adds that command's arguments to its subparser
PARSERS: Dict[str, Callable[[argparse.ArgumentParser], None]] = {}


def command(name: str, help_text: str = None):
    """Register the decorated function as ``crude <name>``."""

    def decorator(func):
        if help_text:
            func.__doc__ = help_text
        COMMANDS[name] = func
        return func

    return decorator


def configure_parser(name: str):
    """Attach the argument wiring of the command registered under ``name``."""

    def decorator(func):
        PARSERS[name] = func
        return func

    return decorator


def _load_commands() -> None:
    """Import the command modules, which register themselves as they load."""
    from . import commands  # noqa: F401 - the import is the point


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m crude",
        description="crude: script Pure Data, audio, OpenGL widgets and serial.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"crude {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    _load_commands()
    for name in sorted(COMMANDS):
        entry = subparsers.add_parser(name, help=COMMANDS[name].__doc__)
        configure = PARSERS.get(name)
        if configure is not None:
            configure(entry)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))

    if not args.command:
        parser.print_help()
        return EXIT_OK

    handler = COMMANDS.get(args.command)
    if handler is None:  # pragma: no cover - argparse rejects unknown names first
        parser.error(f"unknown command: {args.command}")
        return EXIT_MANIFEST
    return handler(args)


def _available_commands() -> List[str]:
    return sorted(COMMANDS)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
