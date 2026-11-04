"""``PD_*`` bindings: the script-facing Pure Data runtime.

Everything here is a thin adapter over :class:`crude.pd.PdRuntime`:

* patches arrive as ``HostHandle`` s (``PD_FILE`` for a hand-written ``.pd``,
  ``PD_BUILD``/``PD_LOAD`` for one the script authored), so a script can hold
  one in a variable and pass it around;
* sending is *queued* - the audio thread owns the data plane, so ``PD_SEND``
  returns immediately and the message leaves with the next block;
* callbacks (``ON_PD``) run on the interpreter thread when the runtime drains
  its inbox, never from the audio callback.

Authoring (``PD_BUILD``, ``PD_OBJ``, ``PD_LINK``, ``PD_LOAD``, ``PD_DUMP``) lives
in :mod:`crude.bindings.pd_author`; this module is the runtime half.
"""

from __future__ import annotations

from ..lang.values import HostHandle, List, Number, String
from ..pd.engine import PdError
from .handles import (
    patch_handle,
    patch_of,
    plain as _plain_impl,
    runtime_of,
    values_from,
)
from .registry import binding

AREA = "pd"


# -- helpers ------------------------------------------------------------


def _runtime(ctx):
    return runtime_of(ctx)


def _patch(ctx, arg_name="patch"):
    return patch_of(ctx, arg_name)


def _values_from(ctx, arg_name):
    """A list argument as plain Python values, or empty when it was omitted."""
    return values_from(ctx, arg_name)


_plain = _plain_impl


def _handle(target) -> HostHandle:
    return patch_handle(target)


# -- patches ------------------------------------------------------------


@binding("PD_FILE", ["id", "path"], area=AREA)
def pd_file(ctx):
    """Open a hand-written ``.pd`` file as patch ``id``."""
    runtime = _runtime(ctx)
    patch_id = ctx.as_string("id")
    path = ctx.as_string("path")
    try:
        patch = runtime.open_file(patch_id, path)
    except PdError as exc:
        raise ctx.fail(f"PD_FILE: {exc}")
    return _handle(patch)


@binding("PD_CLOSE", ["patch"], area=AREA)
def pd_close(ctx):
    patch = _patch(ctx)
    try:
        closed = _runtime(ctx).close(patch.id)
    except PdError as exc:
        raise ctx.fail(f"PD_CLOSE: {exc}")
    return Number(1 if closed else 0)


# -- sending ------------------------------------------------------------


@binding("PD_SEND", ["patch", "recv", "value"], area=AREA)
def pd_send(ctx):
    """Send one float, symbol or list to ``recv`` (coalesced per block)."""
    patch = _patch(ctx)
    recv = ctx.as_string("recv")
    value = _plain(ctx.get("value"))
    _runtime(ctx).send(patch.id, recv, value)
    return Number.null


@binding("PD_SEND_LIST", ["patch", "recv", "values"], area=AREA)
def pd_send_list(ctx):
    patch = _patch(ctx)
    recv = ctx.as_string("recv")
    values = _values_from(ctx, "values")
    _runtime(ctx).send_list(patch.id, recv, values)
    return Number.null


@binding("PD_SEND_BANG", ["patch", "recv"], area=AREA)
def pd_send_bang(ctx):
    patch = _patch(ctx)
    recv = ctx.as_string("recv")
    _runtime(ctx).send_bang(patch.id, recv)
    return Number.null


@binding("PD_SEND_MSG", ["patch", "recv", "message", "values"], area=AREA, optional=1)
def pd_send_msg(ctx):
    patch = _patch(ctx)
    recv = ctx.as_string("recv")
    message = ctx.as_string("message")
    _runtime(ctx).send_message(patch.id, recv, message, _values_from(ctx, "values"))
    return Number.null


@binding("PD_DSP", ["on"], area=AREA)
def pd_dsp(ctx):
    _runtime(ctx).dsp(ctx.as_bool("on"))
    return Number.null


@binding("PD_EXISTS", ["recv"], area=AREA)
def pd_exists(ctx):
    return Number(1 if _runtime(ctx).exists(ctx.as_string("recv")) else 0)


@binding("PD_DOLLARZERO", ["patch"], area=AREA)
def pd_dollarzero(ctx):
    return Number(_patch(ctx).dollar_zero)


# -- callbacks ----------------------------------------------------------


@binding("ON_PD", ["patch", "recv", "fn", "kind"], area=AREA, optional=1)
def on_pd(ctx):
    """Call ``fn`` when Pd sends to ``recv`` (optionally one event kind only)."""
    patch = _patch(ctx)
    recv = ctx.as_string("recv")
    func = ctx.as_function("fn")

    kinds = None
    if ctx.has("kind"):
        kind = ctx.get("kind")
        if isinstance(kind, String):
            kinds = (kind.value,)
        elif isinstance(kind, List):
            kinds = tuple(str(_plain(item)) for item in kind.elements)
        else:
            ctx.bad_type("kind", "string or list", kind)

    handler = _runtime(ctx).on_pd(patch.id, recv, func, kinds)
    return HostHandle(handler, kind="pd_handler", label=f"{patch.id}:{recv}")


@binding("OFF_PD", ["patch", "recv", "fn"], area=AREA, optional=1)
def off_pd(ctx):
    """Remove the handler(s) for ``recv``; with ``fn`` only that one."""
    patch = _patch(ctx)
    recv = ctx.as_string("recv")
    func = ctx.as_function("fn") if ctx.has("fn") else None
    return Number(_runtime(ctx).off_pd(patch.id, recv, func))


# -- arrays -------------------------------------------------------------


@binding("PD_ARRAY_READ", ["patch", "name", "offset", "count"], area=AREA, optional=2)
def pd_array_read(ctx):
    patch = _patch(ctx)
    name = ctx.as_string("name")
    offset = ctx.as_int("offset") if ctx.has("offset") else 0
    count = ctx.as_int("count") if ctx.has("count") else None
    try:
        values = _runtime(ctx).array_read(patch.id, name, offset, count)
    except PdError as exc:
        raise ctx.fail(f"PD_ARRAY_READ: {exc}")
    return List([Number(float(value)) for value in values])


@binding("PD_ARRAY_WRITE", ["patch", "name", "values", "offset"], area=AREA, optional=1)
def pd_array_write(ctx):
    patch = _patch(ctx)
    name = ctx.as_string("name")
    values = _values_from(ctx, "values")
    offset = ctx.as_int("offset") if ctx.has("offset") else 0
    try:
        status = _runtime(ctx).array_write(patch.id, name, values, offset)
    except PdError as exc:
        raise ctx.fail(f"PD_ARRAY_WRITE: {exc}")
    if status != 0:
        raise ctx.fail(
            f"PD_ARRAY_WRITE: array '{name}' rejected {len(values)} values at "
            f"offset {offset}"
        )
    return Number.null


@binding("PD_ARRAY_SIZE", ["patch", "name"], area=AREA)
def pd_array_size(ctx):
    patch = _patch(ctx)
    try:
        return Number(_runtime(ctx).array_size(patch.id, ctx.as_string("name")))
    except PdError as exc:
        raise ctx.fail(f"PD_ARRAY_SIZE: {exc}")


@binding("PD_ARRAY_RESIZE", ["patch", "name", "size"], area=AREA,
         doc="Resize a patch's table in place; answers the size it has now.")
def pd_array_resize(ctx):
    """``PD_ARRAY_RESIZE(p, "sample", 88200)``: the table made to fit the take.

    A script that loads a file cannot know its length when the patch was authored,
    so the table a patch reads has to be sized after the fact. The answer is the
    size the array *has*, read back off Pd rather than the number that was asked
    for, and an array that is not there is refused by name - resizing a name and
    resizing a table are different acts, and only one of them does anything.
    """
    patch = _patch(ctx)
    name = ctx.as_string("name")
    size = ctx.as_int("size")
    if size < 1:
        raise ctx.fail(f"PD_ARRAY_RESIZE: {size} is not a table size; a table "
                       f"holds at least one sample")
    try:
        return Number(_runtime(ctx).array_resize(patch.id, name, size))
    except PdError as exc:
        raise ctx.fail(f"PD_ARRAY_RESIZE: {exc}")


# -- midi ---------------------------------------------------------------
# libpd has no note-off call; Pd's equivalent is a note-on with velocity 0.


@binding("PD_MIDI_NOTEON", ["channel", "pitch", "velocity"], area=AREA)
def pd_midi_noteon(ctx):
    _runtime(ctx).send_midi(
        "noteon", ctx.as_int("channel"), ctx.as_int("pitch"), ctx.as_int("velocity")
    )
    return Number.null


@binding("PD_MIDI_NOTEOFF", ["channel", "pitch"], area=AREA)
def pd_midi_noteoff(ctx):
    _runtime(ctx).send_midi("noteoff", ctx.as_int("channel"), ctx.as_int("pitch"))
    return Number.null


@binding("PD_MIDI_CTL", ["channel", "controller", "value"], area=AREA)
def pd_midi_ctl(ctx):
    _runtime(ctx).send_midi(
        "controlchange",
        ctx.as_int("channel"),
        ctx.as_int("controller"),
        ctx.as_int("value"),
    )
    return Number.null


@binding("PD_MIDI_PGM", ["channel", "value"], area=AREA)
def pd_midi_pgm(ctx):
    _runtime(ctx).send_midi("programchange", ctx.as_int("channel"), ctx.as_int("value"))
    return Number.null


@binding("PD_MIDI_BEND", ["channel", "value"], area=AREA)
def pd_midi_bend(ctx):
    _runtime(ctx).send_midi("pitchbend", ctx.as_int("channel"), ctx.as_int("value"))
    return Number.null


@binding("PD_MIDI_TOUCH", ["channel", "value"], area=AREA)
def pd_midi_touch(ctx):
    _runtime(ctx).send_midi("aftertouch", ctx.as_int("channel"), ctx.as_int("value"))
    return Number.null


@binding("PD_MIDI_POLYTOUCH", ["channel", "pitch", "value"], area=AREA)
def pd_midi_polytouch(ctx):
    _runtime(ctx).send_midi(
        "polyaftertouch", ctx.as_int("channel"), ctx.as_int("pitch"), ctx.as_int("value")
    )
    return Number.null


@binding("PD_MIDI_SYSEX", ["port", "byte"], area=AREA)
def pd_midi_sysex(ctx):
    _runtime(ctx).send_midi("sysex", ctx.as_int("port"), ctx.as_int("byte"))
    return Number.null


@binding("PD_MIDI_REALTIME", ["port", "byte"], area=AREA)
def pd_midi_realtime(ctx):
    _runtime(ctx).send_midi("sysrealtime", ctx.as_int("port"), ctx.as_int("byte"))
    return Number.null


#: Names this module registers, used by the binding-help generator and tests.
BINDINGS = (
    "PD_FILE", "PD_CLOSE",
    "PD_SEND", "PD_SEND_LIST", "PD_SEND_BANG", "PD_SEND_MSG",
    "PD_DSP", "PD_EXISTS", "PD_DOLLARZERO",
    "ON_PD", "OFF_PD",
    "PD_ARRAY_READ", "PD_ARRAY_WRITE", "PD_ARRAY_SIZE", "PD_ARRAY_RESIZE",
    "PD_MIDI_NOTEON", "PD_MIDI_NOTEOFF", "PD_MIDI_CTL", "PD_MIDI_PGM",
    "PD_MIDI_BEND", "PD_MIDI_TOUCH", "PD_MIDI_POLYTOUCH",
    "PD_MIDI_SYSEX", "PD_MIDI_REALTIME",
)
