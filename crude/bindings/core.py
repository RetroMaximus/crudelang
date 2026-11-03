"""The core bindings: everything the original interpreter exposed, plus the
small additions the widget/Pd layers rely on.

Legacy names keep their historical behaviour (including ``APPEND`` mutating in
place and answering ``null``). The additions are:

``GET``/``SET``/``TYPE_OF``   property access on host handles
``AT``/``SET_AT``             index access (the S12 sugar ``l[i]``/``l[i] = v``
                              parses straight onto these two)
``MIN``/``MAX``/``CLAMP``     scalar helpers used by every widget binding
``STR``/``NUM``               explicit conversions

Every subsystem that has handles owns the two words for its own kind through
:func:`own_props` rather than registering a second ``SET``: there is one ``SET``
in the language, and which table it validates against is decided by the handle it
was handed.
"""

from __future__ import annotations

import os

from ..lang.values import (
    BaseFunction,
    HostHandle,
    List,
    Number,
    String,
    python_to_value,
    type_name,
    value_to_python,
)
from .registry import binding

# -- printing and input -------------------------------------------------


@binding("PRINT", ["value"])
def _print(ctx):
    print(str(ctx.get("value")))
    return Number.null


@binding("PRINT_RET", ["value"])
def _print_ret(ctx):
    return String(str(ctx.get("value")))


@binding("INPUT", [])
def _input(ctx):
    return String(input())


@binding("INPUT_INT", [])
def _input_int(ctx):
    while True:
        text = input()
        try:
            return Number(int(text))
        except ValueError:
            print(f"'{text}' must be a integer. Try again!")


@binding("CLEAR", [], aliases=["CLS"])
def _clear(ctx):
    os.system("cls" if os.name == "nt" else "clear")
    return Number.null


# -- type predicates ----------------------------------------------------


@binding("IS_NUM", ["value"])
def _is_num(ctx):
    return Number(1 if isinstance(ctx.get("value"), Number) else 0)


@binding("IS_STR", ["value"])
def _is_str(ctx):
    return Number(1 if isinstance(ctx.get("value"), String) else 0)


@binding("IS_LIST", ["value"])
def _is_list(ctx):
    return Number(1 if isinstance(ctx.get("value"), List) else 0)


@binding("IS_FUNC", ["value"])
def _is_func(ctx):
    return Number(1 if isinstance(ctx.get("value"), BaseFunction) else 0)


@binding("TYPE_OF", ["value"])
def _type_of(ctx):
    return String(type_name(ctx.get("value")))


# -- lists --------------------------------------------------------------


@binding("APPEND", ["list", "value"])
def _append(ctx):
    list_ = ctx.as_list("list")
    list_.elements.append(ctx.get("value"))
    return Number.null


@binding("POP", ["list", "index"])
def _pop(ctx):
    list_ = ctx.as_list("list")
    index = ctx.as_int("index")
    try:
        return list_.elements.pop(index)
    except IndexError:
        raise ctx.fail(
            f"POP: index {index} is out of range for a list of {len(list_.elements)} elements"
        )


@binding("EXTEND", ["listA", "listB"])
def _extend(ctx):
    list_a = ctx.as_list("listA")
    list_b = ctx.as_list("listB")
    list_a.elements.extend(list_b.elements)
    return Number.null


@binding("LEN", ["value"])
def _len(ctx):
    value = ctx.get("value")
    if isinstance(value, List):
        return Number(len(value.elements))
    if isinstance(value, String):
        return Number(len(value.value))
    ctx.bad_type("value", "list or string", value)


@binding("AT", ["value", "index"])
def _at(ctx):
    value = ctx.get("value")
    index = ctx.as_int("index")
    if isinstance(value, List):
        if not -len(value.elements) <= index < len(value.elements):
            raise ctx.fail(
                f"AT: index {index} is out of range for a list of {len(value.elements)} elements"
            )
        return value.elements[index]
    if isinstance(value, String):
        if not -len(value.value) <= index < len(value.value):
            raise ctx.fail(
                f"AT: index {index} is out of range for a string of length {len(value.value)}"
            )
        return String(value.value[index])
    ctx.bad_type("value", "list or string", value)


@binding("SET_AT", ["value", "index", "element"])
def _set_at(ctx):
    value = ctx.get("value")
    if not isinstance(value, List):
        ctx.bad_type("value", "list", value)
    index = ctx.as_int("index")
    if not -len(value.elements) <= index < len(value.elements):
        raise ctx.fail(
            f"SET_AT: index {index} is out of range for a list of {len(value.elements)} elements"
        )
    value.elements[index] = ctx.get("element")
    return Number.null


# -- scalars ------------------------------------------------------------


@binding("MIN", ["a", "b"])
def _min(ctx):
    return Number(min(ctx.as_number("a"), ctx.as_number("b")))


@binding("MAX", ["a", "b"])
def _max(ctx):
    return Number(max(ctx.as_number("a"), ctx.as_number("b")))


@binding("CLAMP", ["value", "low", "high"])
def _clamp(ctx):
    value = ctx.as_number("value")
    low = ctx.as_number("low")
    high = ctx.as_number("high")
    if low > high:
        raise ctx.fail(f"CLAMP: low bound {low} is greater than high bound {high}")
    return Number(min(max(value, low), high))


@binding("STR", ["value"])
def _str(ctx):
    return String(str(ctx.get("value")))


@binding("NUM", ["value"])
def _num(ctx):
    value = ctx.get("value")
    if isinstance(value, Number):
        return Number(value.value)
    if not isinstance(value, String):
        ctx.bad_type("value", "string or number", value)
    text = value.value.strip()
    try:
        return Number(int(text))
    except ValueError:
        pass
    try:
        return Number(float(text))
    except ValueError:
        raise ctx.fail(f"NUM: '{value.value}' is not a number")


# -- host handles -------------------------------------------------------


def _require_handle(ctx, arg_name):
    value = ctx.get(arg_name)
    if not isinstance(value, HostHandle):
        raise ctx.fail(
            f"{ctx.name}: argument '{arg_name}' must be a handle, got {type_name(value)}"
        )
    return value


def _get_prop(target, prop):
    """Read one property; host objects speak plain Python, so wrap it here."""
    getter = getattr(target, "get_prop", None)
    if callable(getter):
        try:
            return True, python_to_value(getter(prop))
        except KeyError:
            return True, None
    if isinstance(target, dict):
        return True, python_to_value(target.get(prop))
    return False, None


def _set_prop(target, prop, value):
    """Write one property, unwrapping the language value for the host side.

    Mirrors :func:`_get_prop`'s contract: a host object that owns a property
    table raises ``KeyError`` on a name that is not in it - that is how it says
    "readable, but not this one" - so the miss is caught here rather than left
    to escape the interpreter as a raw ``KeyError``; ``SET`` then answers with
    its own "no writable properties" line, the same voice ``GET`` uses.
    """
    setter = getattr(target, "set_prop", None)
    if callable(setter):
        try:
            setter(prop, value_to_python(value))
        except KeyError:
            return False
        return True
    if isinstance(target, dict):
        target[prop] = value
        return True
    return False


#: handle kind -> ``read(ctx, handle, prop)`` and ``write(ctx, handle, prop,
#: value)``, installed by whichever subsystem knows what that kind's properties
#: are. The duck-typed paths above cannot tell a widget's prop table from a
#: dictionary's keys, and a second ``SET`` registered later would win the name
#: silently, so the two words stay here and this is how they get told.
_PROP_READERS: dict = {}
_PROP_WRITERS: dict = {}

#: ``[(claims, read, write)]``: the same two tables for a subsystem that recognises
#: its handles by *what they point at* rather than by the word on them. See
#: :func:`own_props`.
_PROP_CLAIMS: list = []


def own_props(kind, read=None, write=None, claims=None):
    """Answer ``GET``/``SET`` for handles of ``kind``; returns the kind.

    Each half is optional, so a read-only subsystem registers only a reader and a
    ``SET`` on its handles keeps answering "no writable properties" the way it did
    before. A callback is handed the :class:`~crude.bindings.registry.HostContext`,
    so it fails in the bindings' own voice - ``ctx.fail(...)`` becomes a script
    error at the call site.

    ``claims`` is for the handles that arrive without a kind word. A host object
    that reaches a script as a *handler argument* is spelled plain ``"handle"``,
    because :func:`~crude.lang.values.python_to_value` knows what it is pointed at
    but not which subsystem defined it, and a ``GET`` inside an ``ON_PAINT`` would
    otherwise refuse the very widget the painter was handed. Given a predicate over
    the target, the kind lookup below falls back to it - the word on the handle
    still wins whenever there is one, so nothing here can steal a name.
    """
    if read is not None:
        _PROP_READERS[kind] = read
    if write is not None:
        _PROP_WRITERS[kind] = write
    if claims is not None:
        _PROP_CLAIMS.append((claims, read, write))
    return kind


def _reader(handle):
    """Whoever speaks for reading this handle's properties, or ``None``."""
    held = _PROP_READERS.get(handle.kind)
    if held is not None:
        return held
    return next((read for claims, read, _write in _PROP_CLAIMS
                 if read is not None and claims(handle.target)), None)


def _writer(handle):
    """Whoever speaks for writing this handle's properties, or ``None``."""
    held = _PROP_WRITERS.get(handle.kind)
    if held is not None:
        return held
    return next((write for claims, _read, write in _PROP_CLAIMS
                 if write is not None and claims(handle.target)), None)


@binding("GET", ["handle", "prop"])
def _get(ctx):
    handle = _require_handle(ctx, "handle")
    prop = ctx.as_string("prop")
    reader = _reader(handle)
    if reader is not None:
        return reader(ctx, handle, prop)
    found, value = _get_prop(handle.target, prop)
    if not found:
        raise ctx.fail(f"GET: {handle.kind} has no readable properties (asked for '{prop}')")
    if value is None:
        raise ctx.fail(f"GET: {handle.kind} has no property '{prop}'")
    return value


@binding("SET", ["handle", "prop", "value"])
def _set(ctx):
    handle = _require_handle(ctx, "handle")
    prop = ctx.as_string("prop")
    writer = _writer(handle)
    if writer is not None:
        return writer(ctx, handle, prop, ctx.get("value"))
    if not _set_prop(handle.target, prop, ctx.get("value")):
        raise ctx.fail(f"SET: {handle.kind} has no writable properties (asked for '{prop}')")
    return Number.null


@binding("RUN", ["fn"])
def _run(ctx):
    from ..lang import program  # local import: program installs these bindings

    path = ctx.as_string("fn")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            script = handle.read()
    except OSError as exc:
        raise ctx.fail(f"RUN: failed to load script \"{path}\": {exc}")

    _, error = program.run(path, script)
    if error:
        raise ctx.fail(f"RUN: failed executing script \"{path}\": {error.as_string()}")
    return Number.null
