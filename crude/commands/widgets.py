"""``crude widgets``: the prop table, printed.

Every reader of the widget schema reads the same table: ``SET`` validates against
it, a tooltip is one of its hints, ``PD_EMIT`` takes its ``pd_name`` rows and
``docs/widgets.md`` is drawn from it. This command prints *that* table rather than
a copy of it, which is the only way a printed schema stays true - and
``--json`` is exactly :func:`crude.ui.props.document` with the skin's own tokens
next to it.

``--project DIR`` and ``--script FILE`` run a program first, because a project's
``WIDGET_DEFINE("bpm", "slider")`` is part of that project's schema and cannot be
described from a manifest alone. Running it costs no hardware: the host built here
is the one ``crude pd dump`` builds - a :class:`~crude.ui.NullRenderer` under the
window, the in-memory Pd emulation under the patches - so the command works over
SSH and on a machine with no GPU, and never opens a device.

The load pipeline is deliberately *not* run: its phases open a library, a stream
and a window, and a schema wants none of them. The manifest is still read, because
its ``ui.theme`` and ``runtime`` size decide what a project's kinds are laid out
against, and a project's own ``MODE``/``LOG`` keep working.

Stdout is the schema. Everything else - what a script ``LOG``ged, what Pd warned
about, the text of a failure - goes to stderr, so
``crude widgets --json > widgets.json`` is a whole JSON document even when the
script that was run went wrong, and a partial schema is still printed in that case.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import types
from typing import Any, Dict, Optional, Tuple

from ..cli import EXIT_LOAD, EXIT_MANIFEST, EXIT_OK, EXIT_SCRIPT, command, configure_parser
from ..errors import CrudeError
from ..ui.base import UIError

#: the run a schema dump asks for: nothing opened, nothing reported on the load
_NO_HARDWARE = dict(mode="terminal", audio=False, serial=False, progress="none")


@command("widgets", "print the widget schema, with no window and no library")
def widgets_command(args: argparse.Namespace) -> int:
    """Describe the table, after letting a script add to it if one was named."""
    project, path = None, ""
    if args.project:
        try:
            project = _open(args.project)
        except CrudeError as exc:
            return _fail(str(exc), _code(exc))
        path = project.entry_path
        if not os.path.isfile(path):
            return _fail(f"the entry script this project declares, "
                         f"'{os.path.basename(path)}', is not in "
                         f"'{project.directory}'", EXIT_LOAD)
    elif args.script:
        path = os.path.abspath(args.script)
        if not os.path.isfile(path):
            return _fail(f"no script '{args.script}'", EXIT_LOAD)

    # the manifest's theme is the project's own answer; --theme beats it, and a
    # name that is not a theme is Skin's to refuse
    from ..ui import Skin

    try:
        skin = Skin(args.theme or _declared_theme(project))
    except UIError as exc:
        return _fail(str(exc), EXIT_LOAD)

    # a script kind lives for one program, but "one program" is per process here:
    # a kind left by the last command in this interpreter is not this script's
    from ..ui import reset

    reset()
    error, code = _run(path, skin, project) if path else ("", EXIT_OK)

    if project is not None:
        for text in project.notes:
            print(f"  {text}", file=sys.stderr)
    if error:
        print(f"crude widgets: {error}", file=sys.stderr)

    try:
        if args.kind:
            return _one_kind(args, skin, path, project, error, code)
        if args.json:
            print(json.dumps(_document(skin, project, path, error), indent=2,
                             default=str))
            return code
        _listing(skin)
        return code
    finally:
        # the kinds the run registered live in a table that is global to the
        # process, and a dump that left them behind would change what the next
        # command in this interpreter says about the catalog
        reset()


# -- the two views ------------------------------------------------------


def _listing(skin: Any) -> None:
    """One line per kind: what it twins to, how many rows it has, what it is for."""
    from ..ui import KINDS, registered_names

    script = registered_names()
    print(f"{len(KINDS)} kind(s), {len(script)} of them defined by a program"
          f" - theme '{skin.theme.name}', scale '{skin.scale_name}', "
          f"{skin.digest}")
    for name, kind in KINDS.items():
        box = f"[{kind.pd_object}]" if kind.pd_object else ""
        print(f"  {'*' if kind.script else ' '} {name:<11} {box:<11} "
              f"{len(kind.props):>2} prop(s)  {kind.hint}")
    print("  a '*' is a kind the program defined; --kind NAME lists one kind's "
          "props, --json the whole table")


def _one_kind(args: argparse.Namespace, skin: Any, path: str, project: Any,
              error: str, code: int) -> int:
    """``--kind NAME``: one kind's rows, in the order ``SET`` reads them.

    Resolved *after* the run above, because the name may be one the script just
    defined - which is the whole reason this command runs a script at all.
    """
    from ..ui import PropError, get_kind

    try:
        kind = get_kind(args.kind)
    except PropError as exc:
        return _fail(str(exc), EXIT_LOAD)

    if args.json:
        payload = _kind_document(kind)
        payload["schema"] = _schema_name()
        payload["theme"] = skin.theme.name
        if error:
            payload["error"] = error
        print(json.dumps(payload, indent=2, default=str))
        return code

    said = f"{kind.name}"
    if kind.base:
        said += f", a '{kind.base}'"
    if kind.pd_object:
        said += f", emits [{kind.pd_object}]"
    if kind.script:
        said += " - defined by a program"
    print(said)
    if kind.hint:
        print(f"  {kind.hint}")
    if path:
        print(f"  as run by {os.path.basename(path)}")
    if kind.args:
        print(f"  built with: {', '.join(kind.args)}")
    variants = _variants(kind)
    if variants:
        print(f"  kinds built on it: {', '.join(variants)}")
    print(f"  {len(kind.props)} prop(s):")
    for prop in kind.props.values():
        print(f"    {prop}")
    return code


# -- the document -------------------------------------------------------


def _document(skin: Any, project: Any, path: str, error: str) -> Dict[str, Any]:
    """The schema, the skin and the icons, as one JSON document.

    The tokens are in it because ``docs/widgets.md`` and a theme comparison both
    want the colours a role resolves to, and there is no second place they could
    be read from: :meth:`crude.ui.Skin.tokens` is the same call the painters use.
    """
    from ..ui import document, icon_names, registered_names

    out = document()
    out["theme"] = skin.theme.name
    out["scale"] = skin.scale_name
    out["factor"] = skin.scale
    out["digest"] = skin.digest
    out["tokens"] = skin.tokens()
    out["metrics"] = skin.metrics()
    out["glyphs"] = list(icon_names())
    out["script_kinds"] = list(registered_names())
    out["source"] = _source(project, path)
    if error:
        out["error"] = error
    return out


def _kind_document(kind: Any) -> Dict[str, Any]:
    """One kind, on its own, with the kinds built on it named."""
    out = kind.as_dict()
    out["variants"] = _variants(kind)
    return out


def _variants(kind: Any) -> list:
    from ..ui import KINDS

    return [name for name, other in KINDS.items() if other.base == kind.name]


def _source(project: Any, path: str) -> Optional[Dict[str, Any]]:
    """What this dump was merged from, so a reader can tell a catalog from a build."""
    if not path:
        return None
    if project is not None:
        return {"project": project.id, "directory": project.directory,
                "script": os.path.relpath(path, project.directory).replace(
                    os.sep, "/")}
    return {"script": path}


def _schema_name() -> str:
    from ..ui import SCHEMA

    return SCHEMA


# -- running a script, with no hardware ---------------------------------


def _open(where: str) -> Any:
    from ..project import LoadOptions, open_project

    return open_project(where, options=LoadOptions(**_NO_HARDWARE))


def _declared_theme(project: Any) -> Optional[str]:
    """The theme the manifest asks for; ``None`` leaves the default alone."""
    if project is None:
        return None
    return getattr(getattr(project.manifest, "ui", None), "theme", None)


def _run(path: str, skin: Any, project: Any) -> Tuple[str, int]:
    """Run ``path`` against a headless host; ``(what failed, how to end)``.

    The host is a namespace rather than a project's, and is missing exactly the
    subsystems this command cannot provide: ``audio``, ``serial`` and a loaded
    ``resources`` table. A script that asks for one hears "no such runtime", the
    same refusal a bare ``crude pd dump`` gives, which is honest in a way an
    half-loaded project would not be.
    """
    from ..errors import ExitRequest
    from ..lang import Program
    from ..pd import FakeLibpd, PdRuntime, PdSession
    from ..ui import NullRenderer, UIContext

    manifest = getattr(project, "manifest", None)
    size = getattr(manifest, "runtime", None)
    context = UIContext(NullRenderer(), skin=skin, name="widgets",
                        width=getattr(size, "width", None),
                        height=getattr(size, "height", None))
    session = PdSession(FakeLibpd())
    pd = PdRuntime(session, project_dir=os.path.dirname(path),
                   log=lambda message: print(f"  {message}", file=sys.stderr))
    host = types.SimpleNamespace(
        ui=context, pd=pd, project=project,
        clock=getattr(project, "clock", None),
        audio=None, serial=None, resources=None,
        attach_program=pd.attach_program)
    program = Program(path, host=host)
    pd.attach_program(program)

    session.start()
    try:
        _value, error = program.run_file(path)
    except ExitRequest as request:
        # EXIT is the script choosing how the process ends, not a failure, so its
        # code is this command's answer and there is nothing to print
        return "", int(request.code)
    finally:
        session.shutdown()
    return ("", EXIT_OK) if error is None else (error.as_string(), EXIT_SCRIPT)


# -- one place where a failure becomes a line and a code ----------------


def _code(error: BaseException) -> int:
    """2 for a manifest, 3 for a load, whatever the failure itself says."""
    return int(getattr(error, "exit_code", EXIT_MANIFEST))


def _fail(message: str, code: int = EXIT_LOAD) -> int:
    print(f"crude widgets: {message}", file=sys.stderr)
    return code


@configure_parser("widgets")
def widgets_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true",
                        help="the whole schema as one JSON document, on stdout")
    parser.add_argument("--kind", default=None, metavar="KIND",
                       help="list one kind's props instead of every kind")
    parser.add_argument("--theme", default=None, metavar="NAME",
                        help="print the tokens of this theme rather than the "
                             "project's or the default one")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--project", default=None, metavar="DIR",
                       help="run this project's entry script first, so its own "
                            "kinds are in the table (no device, no window)")
    where.add_argument("--script", default=None, metavar="FILE",
                       help="run this script first, with no project around it")
