"""``SERIAL_*`` bindings: the script-facing serial surface.

Everything here is a thin adapter over :class:`crude.serial.SerialRuntime`;
the whole point of S10 is that a script cannot tell whether it is talking to
an Arduino or to a ``loop://`` echo, and neither can the tests. So nothing
here imports ``serial``, and nothing here starts a thread - the runtime does,
and the reader thread does.

The plan's four verbs:

* ``SERIAL_OPEN(id, port[, baud][, codec])`` -> a link handle,
* ``SERIAL_WRITE(link, data)`` -> the bytes the port accepted,
* ``SERIAL_CLOSE(link)`` -> whether it was open,
* ``ON_SERIAL(link, fn[, kind])`` -> a handler handle (so ``OFF`` can reach it).

A link handle is ``GET``-able and partly ``SET``-able (see
:data:`crude.serial.link._LINK_PROPS` and :data:`crude.serial.link.WRITABLE_PROPS`):
``baud`` and ``port`` close and reopen the device at the new setting rather than
leave the reader thread and the codec disagreeing about what a byte means, and
``codec`` swaps in place without touching the port.
"""

from __future__ import annotations

from ..lang.values import HostHandle, List, Number, String
from ..serial.base import SerialError
from .registry import binding

AREA = "serial"

#: what ``GET``/``SET`` see on a serial link handle
SERIAL_KIND = "serial"


# -- helpers ------------------------------------------------------------


def _runtime(ctx):
    return ctx.subsystem("serial", "serial")


def _link(ctx, arg_name="link"):
    value = ctx.get(arg_name)
    if not isinstance(value, HostHandle) or value.kind != SERIAL_KIND:
        ctx.bad_type(arg_name, "serial link", value)
    return value.target


def _fail(ctx, exc) -> None:
    raise ctx.fail(f"{ctx.name}: {exc}")


def _handle(link) -> HostHandle:
    return HostHandle(link, kind=SERIAL_KIND, label=link.describe())


def _as_plain(value):
    """A language value as the plain Python object a codec expects."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, List):
        return [_as_plain(item) for item in value.elements]
    if isinstance(value, HostHandle):
        return value.target
    return value


# -- the link -----------------------------------------------------------


@binding("SERIAL_OPEN", ["id", "port", "baud", "codec"], area=AREA, optional=3)
def serial_open(ctx):
    """Open a link by id and URI, or look one up by id alone.

    Two shapes the plan needs to cover:

    * ``SERIAL_OPEN(\"arduino\", \"loop://arduino\", 115200, \"line\")`` - the
      script declares everything itself, so the runtime opens a fresh link;
    * ``SERIAL_OPEN(\"arduino\")`` - the link was already opened for the
      project's manifest by the loader phase; the script is picking it up by
      id rather than restating a port the person already configured.
    """
    runtime = _runtime(ctx)
    link_id = ctx.as_string("id")

    if not ctx.has("port"):
        held = runtime.links.get(link_id)
        if held is None:
            known = ", ".join(sorted(runtime.links)) or "none"
            _fail(ctx, SerialError(
                f"there is no serial link '{link_id}' open for this project "
                f"(known: {known}); pass a port to SERIAL_OPEN to make one"
            ))
        return _handle(held)

    uri = ctx.as_string("port")
    baud = ctx.as_int("baud") if ctx.has("baud") else 115200
    codec = ctx.as_string("codec") if ctx.has("codec") else "line"

    decl = _Decl(link_id, uri, baud, codec, optional=True)
    try:
        link = runtime.open(decl, codec=codec)
    except SerialError as exc:
        _fail(ctx, exc)
    return _handle(link)


@binding("SERIAL_WRITE", ["link", "data"], area=AREA)
def serial_write(ctx):
    """Send bytes or a script value; the codec decides whether to add framing."""
    link = _link(ctx)
    payload = _as_plain(ctx.get("data"))
    try:
        written = link.write(payload)
    except SerialError as exc:
        _fail(ctx, exc)
    return Number(float(written))


@binding("SERIAL_CLOSE", ["link"], area=AREA)
def serial_close(ctx):
    """Stop the reader and close the port; whether it was open before."""
    runtime = _runtime(ctx)
    link = _link(ctx)
    try:
        closed = runtime.close(link)
    except SerialError as exc:
        _fail(ctx, exc)
    return Number(1 if closed else 0)


# -- callbacks ----------------------------------------------------------


@binding("ON_SERIAL", ["link", "fn", "kind"], area=AREA, optional=1)
def on_serial(ctx):
    """Call ``fn`` with every frame that arrives on ``link`` (optionally one kind)."""
    runtime = _runtime(ctx)
    link = _link(ctx)
    func = ctx.as_function("fn")
    kinds = None
    if ctx.has("kind"):
        value = ctx.get("kind")
        if isinstance(value, String):
            kinds = (value.value,)
        elif isinstance(value, List):
            kinds = tuple(str(_as_plain(item)) for item in value.elements)
        else:
            ctx.bad_type("kind", "string or list", value)
    handler = runtime.on_serial(link, func, kinds)
    return HostHandle(handler, kind="serial_handler", label=link.id)


@binding("OFF_SERIAL", ["link", "fn"], area=AREA, optional=1)
def off_serial(ctx):
    """Remove the handler(s) for ``link``; with ``fn`` only that one."""
    runtime = _runtime(ctx)
    link = _link(ctx)
    func = ctx.as_function("fn") if ctx.has("fn") else None
    return Number(float(runtime.off_serial(link, func)))


# -- a small manifest-shape stand-in ------------------------------------


class _Decl:
    """The attributes :meth:`SerialRuntime.open` reads off a declaration.

    ``SERIAL_OPEN`` is called with a link the manifest did not declare, so a
    tiny object with the same fields is built here rather than reaching into
    :mod:`crude.project.manifest` for a dataclass that only exists for loaded
    projects.
    """

    def __init__(self, link_id: str, port: str, baud: int, codec: str,
                 optional: bool = True):
        self.id = link_id
        self.port = port
        self.baud = int(baud)
        self.codec = codec
        self.optional = optional

    def describe(self) -> str:
        return f"{self.id} {self.port} {self.baud} {self.codec}"


#: Names this module registers, used by the binding-help generator and tests.
BINDINGS = (
    "SERIAL_OPEN", "SERIAL_WRITE", "SERIAL_CLOSE",
    "ON_SERIAL", "OFF_SERIAL",
)
