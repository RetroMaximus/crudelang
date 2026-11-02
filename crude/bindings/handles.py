"""Unwrapping the handles the Pd bindings accept.

Shared by :mod:`crude.bindings.pd` (the runtime half) and
:mod:`crude.bindings.pd_author` (authoring): both take the same ``patch``
argument, which may wrap an open :class:`~crude.pd.engine.PdPatch` *or* the
:class:`~crude.pd.model.PatchDoc` a script is still building. ``PD_BUILD`` hands
back the document and ``PD_LOAD`` the open patch, but a script keeps using the
same variable either way, so the unwrapping has to be tolerant in exactly one
place. :func:`widget_of` lives here for the same reason: it is the one argument
``PD_EMIT`` takes that is not a patch at all.
"""

from __future__ import annotations

from typing import List as PyList

from ..lang.values import HostHandle, List, Number, String, type_name
from ..pd.engine import PdError, PdPatch
from ..pd.model import Box, PatchDoc, PatchError

#: The handle kind scripts see for a patch, authored or loaded.
PATCH_KIND = "patch"
BOX_KIND = "pd_box"


def runtime_of(ctx):
    """``host.pd`` or a script error that says what is missing."""
    return ctx.subsystem("pd", "Pd")


def plain(value):
    """A crude value into the Python the engine expects."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, List):
        return [plain(item) for item in value.elements]
    if isinstance(value, HostHandle):
        return value.target
    return value


def values_from(ctx, arg_name) -> PyList:
    """A list argument as plain Python values, or empty when it was omitted."""
    if not ctx.has(arg_name):
        return []
    value = ctx.get(arg_name)
    if isinstance(value, List):
        return [plain(item) for item in value.elements]
    return [plain(value)]


def patch_handle(target) -> HostHandle:
    """Wrap an open patch or an authored document as one handle kind."""
    return HostHandle(target, kind=PATCH_KIND, label=target.id)


def box_handle(doc: PatchDoc, box: Box) -> HostHandle:
    return HostHandle(box, kind=BOX_KIND, label=f"{doc.id}:{box.id}")


def patch_id_of(ctx, arg_name: str = "patch") -> str:
    """The id behind a patch handle, loaded or not."""
    handle = ctx.as_handle(arg_name, kind=PATCH_KIND)
    if isinstance(handle.target, (PdPatch, PatchDoc)):
        return handle.target.id
    raise ctx.fail(
        f"{ctx.name}: argument '{arg_name}' is a {type_name(handle.target)}, "
        f"expected a patch"
    )


def patch_of(ctx, arg_name: str = "patch") -> PdPatch:
    """The *loaded* patch behind ``arg_name``.

    An authored document is accepted by id: telling the script to load it is
    more useful than claiming the handle is the wrong type.
    """
    runtime = runtime_of(ctx)
    handle = ctx.as_handle(arg_name, kind=PATCH_KIND)

    if isinstance(handle.target, PatchDoc):
        try:
            return runtime.patch(handle.target.id)
        except PdError as exc:
            raise ctx.fail(
                f"{ctx.name}: {exc} - call PD_LOAD('{handle.target.id}') first"
            )
    if isinstance(handle.target, PdPatch):
        return handle.target
    raise ctx.fail(
        f"{ctx.name}: argument '{arg_name}' is a {type_name(handle.target)}, "
        f"expected a patch"
    )


def doc_of(ctx, arg_name: str = "patch") -> PatchDoc:
    """The authored document behind a patch handle (``PD_BUILD`` required)."""
    handle = ctx.as_handle(arg_name, kind=PATCH_KIND)
    if isinstance(handle.target, PatchDoc):
        return handle.target
    if isinstance(handle.target, PdPatch):
        # A patch opened from a file has no document to edit, so the runtime's
        # answer has to reach the script in the bindings' voice rather than as
        # a Python error the interpreter would choke on.
        try:
            return runtime_of(ctx).doc(handle.target.id)
        except (PdError, PatchError) as exc:
            raise ctx.fail(
                f"{ctx.name}: {exc} - only a patch still being built can be "
                f"edited"
            )
    raise ctx.fail(
        f"{ctx.name}: argument '{arg_name}' is a {type_name(handle.target)}, "
        f"expected a patch built with PD_BUILD"
    )


def box_of(ctx, arg_name: str, doc: PatchDoc) -> Box:
    """A box handle returned by the ``PD_OBJ`` family, checked against ``doc``."""
    handle = ctx.as_handle(arg_name, kind=BOX_KIND)
    box = handle.target
    if box not in doc.boxes:
        raise ctx.fail(
            f"{ctx.name}: argument '{arg_name}' ({box.id}) is not a box of "
            f"patch '{doc.id}'"
        )
    return box


def widget_of(ctx, arg_name: str):
    """The widget behind ``arg_name``, whichever shape it arrived in.

    ``PD_EMIT`` is the one Pd binding that takes a widget, and a widget reaches it
    as two shapes that are the same object: the handle :mod:`crude.bindings.ui`
    handed the script, and the wrapper :meth:`Program.invoke` puts around the
    widget when a *handler* passes its own widget on to a function. Accepting only
    the first would make ``PD_EMIT`` fail inside an ``ON_CLICK`` for a reason the
    script has no way to see.

    The import is local on purpose: this is the Pd side, and unwrapping a patch
    should not pull the whole UI package in behind it.
    """
    from ..ui.widget import Widget  # local: keeps the Pd bindings UI-light

    value = ctx.get(arg_name)
    if isinstance(value, Widget):
        return value
    if isinstance(value, HostHandle) and isinstance(value.target, Widget):
        return value.target
    raise ctx.fail(
        f"{ctx.name}: argument '{arg_name}' must be a widget handle, "
        f"got {type_name(value)}"
    )
