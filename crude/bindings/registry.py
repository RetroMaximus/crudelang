"""The binding registry: the one place where host functions are declared.

A binding is a name, the argument names it exposes to the language, and a plain
Python function taking a :class:`HostContext`::

    @binding("PD_SEND", ["patch", "recv", "value"], area="pd")
    def pd_send(ctx, patch, recv, value): ...

The decorator stores the declaration; ``install(table)`` turns every entry into
a ``BuiltInFunction`` in a symbol table. Nothing else - no ``execute_*``
methods, no ``__getattr__`` probing - is involved, which keeps the surface
greppable and lets ``help_gen`` enumerate it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from ..lang.values import (
    BaseFunction,
    BindingError,
    BuiltInFunction,
    HostHandle,
    List,
    Number,
    String,
    type_name,
)


@dataclass(frozen=True)
class Binding:
    """A declarative host function."""

    name: str
    arg_names: Tuple[str, ...] = ()
    func: Optional[Callable] = None
    area: str = "core"
    optional: int = 0
    doc: str = ""

    def to_builtin(self) -> BuiltInFunction:
        return BuiltInFunction(
            self.name,
            list(self.arg_names),
            _make_runner(self),
            area=self.area,
            optional=self.optional,
        )


REGISTRY: Dict[str, Binding] = {}


def binding(name: str, arg_names=(), area: str = "core", optional: int = 0,
            aliases=(), doc: str = ""):
    """Declare a host function under ``name`` (and every name in ``aliases``)."""

    def decorator(func):
        names = (name,) + tuple(aliases)
        for registered in names:
            REGISTRY[registered] = Binding(
                name=registered,
                arg_names=tuple(arg_names),
                func=func,
                area=area,
                optional=optional,
                doc=doc,
            )
        return func

    return decorator


def _make_runner(entry: Binding):
    def runner(exec_ctx):
        return entry.func(HostContext(exec_ctx, entry))

    return runner


def get_binding(name: str) -> Optional[Binding]:
    return REGISTRY.get(name)


def install(table, area: str = None) -> None:
    """Populate ``table`` with every registered binding (or one area of them)."""
    for name, entry in REGISTRY.items():
        if area is not None and entry.area != area:
            continue
        table.set(name, entry.to_builtin())


class HostContext:
    """What a binding sees: its arguments, coercion helpers and ``fail``.

    Bindings never touch ``RTError``/``RTResult``; they raise
    :class:`BindingError` through :meth:`fail` and the call machinery attaches
    the source span.
    """

    def __init__(self, exec_ctx, entry: Binding):
        self.exec_ctx = exec_ctx
        self.binding = entry
        self.name = entry.name
        self.area = entry.area
        self.symbol_table = exec_ctx.symbol_table

    @property
    def host(self):
        """The host attached to this program (``host.pd``, ``host.audio``...)."""
        table = self.symbol_table
        finder = getattr(table, "find_host", None)
        return finder() if callable(finder) else getattr(table, "host", None)

    def subsystem(self, name: str, what: str = None):
        """Fetch ``host.<name>``, failing with a clear script error if absent."""
        host = self.host
        value = getattr(host, name, None) if host is not None else None
        if value is None:
            raise BindingError(
                f"{self.name}: no {what or name} runtime is attached to this program"
            )
        return value

    # -- argument access ------------------------------------------------
    def provided(self, arg_name: str) -> bool:
        """Did this call actually pass ``arg_name``?

        The values live in a symbol table whose parent is the caller's, so a
        plain lookup would answer with whatever script variable happens to share
        an argument's name. ``BuiltInFunction.execute`` records the names the call
        passed, and an argument outside that set is *not there* - the binding's
        own default, not the program's variable, is what an omitted argument
        means.
        """
        provided = getattr(self.exec_ctx, "provided_args", None)
        return arg_name in provided if provided is not None else True

    def get(self, arg_name: str):
        """Raw argument value, or ``Number.null`` when it was omitted."""
        if not self.provided(arg_name):
            return Number.null
        value = self.symbol_table.get(arg_name)
        return Number.null if value is None else value

    def has(self, arg_name: str) -> bool:
        return self.provided(arg_name) and self.symbol_table.get(arg_name) is not None

    def arg(self, index: int):
        return self.get(self.binding.arg_names[index])

    def arg_names(self):
        return self.binding.arg_names

    def args(self):
        return [self.get(name) for name in self.binding.arg_names]

    # -- errors ---------------------------------------------------------
    def fail(self, message: str):
        raise BindingError(message)

    def bad_type(self, arg_name: str, expected: str, value=None):
        value = self.get(arg_name) if value is None else value
        raise BindingError(
            f"{self.name}: argument '{arg_name}' must be a {expected}, "
            f"got {type_name(value)}"
        )

    # -- coercions ------------------------------------------------------
    def as_number(self, arg_name: str) -> float:
        value = self.get(arg_name)
        if not isinstance(value, Number):
            self.bad_type(arg_name, "number", value)
        return value.value

    def as_int(self, arg_name: str) -> int:
        value = self.as_number(arg_name)
        if isinstance(value, float) and not value.is_integer():
            raise BindingError(
                f"{self.name}: argument '{arg_name}' must be a whole number, got {value}"
            )
        return int(value)

    def as_string(self, arg_name: str) -> str:
        value = self.get(arg_name)
        if not isinstance(value, String):
            self.bad_type(arg_name, "string", value)
        return value.value

    def as_list(self, arg_name: str) -> List:
        value = self.get(arg_name)
        if not isinstance(value, List):
            self.bad_type(arg_name, "list", value)
        return value

    def as_bool(self, arg_name: str) -> bool:
        return self.get(arg_name).is_true()

    def as_function(self, arg_name: str) -> BaseFunction:
        value = self.get(arg_name)
        if not isinstance(value, BaseFunction):
            self.bad_type(arg_name, "function", value)
        return value

    def as_handle(self, arg_name: str, kind: str = None, target_type=None):
        """Unwrap a ``HostHandle``, optionally checking its kind/underlying type."""
        value = self.get(arg_name)
        if not isinstance(value, HostHandle):
            self.bad_type(arg_name, kind or "handle", value)
        if kind is not None and value.kind != kind:
            raise BindingError(
                f"{self.name}: argument '{arg_name}' must be a {kind} handle, "
                f"got {value.kind}"
            )
        if target_type is not None and not isinstance(value.target, target_type):
            raise BindingError(
                f"{self.name}: argument '{arg_name}' does not refer to a "
                f"{getattr(target_type, '__name__', target_type)}"
            )
        return value

    def __repr__(self):
        return f"<binding ctx {self.name} {list(self.binding.arg_names)}>"


def wrap(target, kind: str = "handle", label: str = "") -> HostHandle:
    """Convenience constructor used by every subsystem binding."""
    return HostHandle(target, kind=kind, label=label)
