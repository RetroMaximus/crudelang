"""The project bindings: what a script may ask about the thing it is running in.

Three groups, all of them answers about the project rather than about a subsystem:

* **files** - ``RES`` reaches one declared file, by any name the manifest gives it,
  and ``PRESET_LOAD``/``PRESET_SAVE`` move a pattern in and out of the preset
  directory the project declared rather than the working directory the run happens
  to have;
* **time** - ``TIME``, ``SLEEP`` and ``FPS`` go through the project's
  :class:`~crude.runtime.clock.Clock`, which is what lets a test drive a whole
  script without a second of real waiting;
* **the run** - ``MODE`` says which mode was chosen, ``LOG`` puts a line in the
  report *and* the log file, and ``EXIT`` ends the process with the code the script
  picked.

Nothing here imports libpd, sounddevice or GL: the same nine calls work in a
terminal run over SSH and in a window, because they only ever talk to the
:class:`~crude.project.Project` behind the host.
"""

from __future__ import annotations

import os

from ..errors import ExitRequest, ResourceError
from ..lang.values import (
    HostHandle,
    List,
    Number,
    String,
    python_to_value,
    value_to_python,
)
from ..project.resources import ARRAY
from .registry import binding

AREA = "project"

#: what ``GET``/``SET`` see on the handle ``RES`` returns
RESOURCE_KIND = "resource"

#: what ``GET``/``SET`` see on the handle an object-shaped preset arrives in
PRESET_HANDLE_KIND = "preset"

#: the only message a script gets when it is run outside a project
NO_PROJECT = "this script is not running inside a crude project"

#: what ``GET`` reads off a resource: the manifest's own words, plus the numbers
RESOURCE_PROPS = (
    "id", "kind", "name", "path", "declared", "exists", "size",
    "values", "count", "patch", "format",
)

#: the level names ``LOG`` accepts, folded onto what each one does
LOG_LEVELS = {
    "debug": "debug",
    "info": "info",
    "warn": "warn",
    "warning": "warn",
    "error": "error",
}


# -- helpers ------------------------------------------------------------


def _project(ctx):
    """``host.project``, or a refusal that tells the script where it is running."""
    host = ctx.host
    project = getattr(host, "project", None) if host is not None else None
    if project is None:
        raise ctx.fail(f"{ctx.name}: {NO_PROJECT}")
    return project


def _clock(ctx):
    """The clock this run measures time with - the host's, or its project's."""
    host = ctx.host
    clock = getattr(host, "clock", None) if host is not None else None
    if clock is None:
        clock = getattr(_project(ctx), "clock", None)
    if clock is None:
        raise ctx.fail(f"{ctx.name}: {NO_PROJECT}")
    return clock


def _shown(project, path: str) -> str:
    """A file this project wrote, spelled the way the project spells its files."""
    try:
        return os.path.relpath(path, project.directory).replace(os.sep, "/")
    except ValueError:  # pragma: no cover - a path on another drive
        return os.path.basename(path)


def _size(path: str) -> int:
    """Bytes in a declared file; ``0`` when it is not there to count."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _json_ready(value):
    """Plain Python ``json`` can write, or a refusal naming what cannot be saved.

    A handle or a function in a preset would be written as whatever its ``repr``
    happened to be, and a preset is read back on another run - so only numbers,
    text, lists and dicts of them are allowed through. A language value may still
    be in there, because ``SET`` on a loaded preset writes the script's own value
    into the bag, so those are unwrapped here rather than refused.
    """
    if isinstance(value, (Number, String, List)):
        return _json_ready(value_to_python(value))
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    raise TypeError(
        f"a {type(value).__name__} cannot be saved in a preset; "
        f"save numbers, text and lists of them"
    )


class ResourceView:
    """One declared file, as ``GET`` sees it.

    Read-only on purpose: a resource's path is the manifest's business, and a
    script that wants a different file asks for it by name. The array questions are
    answered by every kind, not refused: a sound has no table to read, so it says
    so with an empty list rather than an error the caller would have to predict.
    """

    def __init__(self, project, resource) -> None:
        self.project = project
        self.resource = resource

    def values(self):
        if self.resource.kind != ARRAY:
            return []
        return self.project.array_values(self.resource.id)

    def get_prop(self, prop: str):
        if prop not in RESOURCE_PROPS:
            raise KeyError(prop)
        resource = self.resource
        if prop == "id":
            return resource.id
        if prop == "kind":
            return resource.kind
        if prop == "name":
            return resource.name
        if prop == "path":
            # Pd's own file objects ([readsf~], [soundfiler~]) reject a Windows
            # backslash path with "Invalid argument", so the path a script hands
            # them is forward-slashed - Python and os.path accept it just the
            # same, so this is the safe direction to normalise.
            return str(resource.path).replace("\\", "/")
        if prop == "declared":
            return resource.declared
        if prop == "exists":
            return 1 if resource.exists else 0
        if prop == "size":
            return _size(resource.path)
        if prop == "values":
            return self.values()
        if prop == "count":
            return len(self.values())
        if prop == "patch":
            return (resource.array.patch or "") if resource.array is not None else ""
        return resource.array.format if resource.array is not None else ""

    def __str__(self) -> str:
        return self.resource.describe()


# -- files --------------------------------------------------------------


@binding("RES", ["name"], area=AREA)
def res(ctx):
    """One declared file as a handle: ``GET(RES("tick"), "path")``."""
    name = ctx.as_string("name")
    project = _project(ctx)
    try:
        resource = project.resource(name)
    except ResourceError as exc:
        raise ctx.fail(f"RES: {exc}")
    return HostHandle(ResourceView(project, resource), kind=RESOURCE_KIND,
                      label=f"{resource.kind}:{resource.id}")


@binding("PRESET_LOAD", ["name"], area=AREA)
def preset_load(ctx):
    """A saved pattern, as the values it holds and nothing else."""
    name = ctx.as_string("name")
    try:
        data = _project(ctx).read_preset(name)
    except ResourceError as exc:
        raise ctx.fail(f"PRESET_LOAD: {exc}")
    value = python_to_value(data)
    if isinstance(value, HostHandle):
        # an object-shaped preset is a bag of props worth reading by name, so the
        # handle says what it is instead of arriving as an anonymous "handle"
        value.kind = PRESET_HANDLE_KIND
        value.label = f"preset:{name}"
    return value


@binding("PRESET_SAVE", ["name", "data"], area=AREA)
def preset_save(ctx):
    """Save a pattern where the project keeps its presets; answers that path."""
    name = ctx.as_string("name")
    project = _project(ctx)
    try:
        payload = _json_ready(value_to_python(ctx.get("data")))
    except TypeError as exc:
        raise ctx.fail(f"PRESET_SAVE: {exc}")
    try:
        saved = project.write_preset(name, payload)
    except (ResourceError, OSError) as exc:
        raise ctx.fail(f"PRESET_SAVE: {exc}")
    return String(_shown(project, saved))


# -- the run -------------------------------------------------------------


@binding("EXIT", ["code"], area=AREA, optional=1)
def request_exit(ctx):
    """Stop the run on purpose; ``EXIT()`` is a clean end, ``EXIT(3)`` is not."""
    code = ctx.as_int("code") if ctx.has("code") else 0
    if not 0 <= code <= 255:
        raise ctx.fail(f"EXIT: an exit code has to be 0..255, got {code}")
    raise ExitRequest(code)


@binding("MODE", [], area=AREA)
def mode(ctx):
    """Which mode this run is in: ``terminal``, ``headless`` or ``window``."""
    return String(_project(ctx).mode)


@binding("LOG", ["message", "level"], area=AREA, optional=1)
def log(ctx):
    """Say something out loud: into the report the person sees and into the log."""
    text = str(ctx.get("message"))
    level = ctx.as_string("level") if ctx.has("level") else "info"
    named = LOG_LEVELS.get(level.strip().lower())
    if named is None:
        raise ctx.fail(
            f"LOG: there is no log level '{level}' "
            f"(known: {', '.join(sorted(LOG_LEVELS))})"
        )
    if not text.strip():
        # an empty line is not something to note, and answering 0 says it was not said
        return Number(0)

    project = _project(ctx)
    if named == "debug":
        logger = project.logger
        if logger is not None:
            logger.debug(text)
        return Number(0)
    if named == "info":
        project.note(text)
    elif named == "warn":
        project.warning(text)
    else:
        project.error(text)
    return Number(1)


# -- time ----------------------------------------------------------------


@binding("TIME", [], area=AREA)
def time_now(ctx):
    """Seconds since this run started - a duration, never a calendar."""
    return Number(float(_clock(ctx).now()))


@binding("FPS", [], area=AREA)
def fps(ctx):
    """The frame rate this run aims at; measuring the real one is the loop's job.

    Whole, because this is a decision and not a measurement: a manifest says
    ``60`` and ``--fps`` refuses anything that is not a whole number of frames a
    second, so a script printing ``FPS()`` never sees ``60.0``.
    """
    host = ctx.host
    project = getattr(host, "project", None) if host is not None else None
    if project is not None:
        return Number(int(project.fps))
    return Number(int(_clock(ctx).target_fps))


@binding("SLEEP", ["seconds"], area=AREA)
def sleep(ctx):
    """Hold for a while, and answer how long was actually held."""
    seconds = ctx.as_number("seconds")
    if seconds < 0:
        raise ctx.fail(f"SLEEP: a delay cannot be negative (got {seconds})")
    return Number(float(_clock(ctx).sleep(seconds)))


#: Names this module registers, used by the binding-help generator and tests.
BINDINGS = (
    "RES", "PRESET_LOAD", "PRESET_SAVE", "EXIT", "TIME", "FPS",
    "SLEEP", "LOG", "MODE",
)
