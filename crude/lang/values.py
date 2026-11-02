"""Runtime values: numbers, strings, lists, functions and host handles.

Two invariants matter for everything that follows:

* an operation returns ``(value, error)`` - never raises - so the interpreter
  can attach a traceback and a source span;
* ``copy()`` is what variable reads return. ``HostHandle`` therefore copies to
  *itself*: a script holding a widget or a patch must keep referring to the same
  object, while numbers/strings/lists stay value types.
"""

from __future__ import annotations

from .errors import RTError
from .result import RTResult


class BindingError(Exception):
    """Raised by a host binding to report a user-facing runtime error.

    ``BuiltInFunction.execute`` converts it into an ``RTError`` positioned at the
    call site, so bindings never have to know about spans or tracebacks.
    """


def _is_number(value) -> bool:
    return isinstance(value, Number)


def type_name(value) -> str:
    """Human readable type of a value, used by TYPE_OF and error messages."""
    if isinstance(value, Number):
        return "number"
    if isinstance(value, String):
        return "string"
    if isinstance(value, List):
        return "list"
    if isinstance(value, BaseFunction):
        return "function"
    if isinstance(value, HostHandle):
        return value.kind
    return type(value).__name__


def value_eq(left, right) -> bool:
    """Structural equality used by == and by list comparisons."""
    if isinstance(left, Number) and isinstance(right, Number):
        return left.value == right.value
    if isinstance(left, String) and isinstance(right, String):
        return left.value == right.value
    if isinstance(left, HostHandle) and isinstance(right, HostHandle):
        return left is right or left.target is right.target
    if isinstance(left, List) and isinstance(right, List):
        if len(left.elements) != len(right.elements):
            return False
        return all(value_eq(a, b) for a, b in zip(left.elements, right.elements))
    return left is right


class Value:
    def __init__(self):
        self.pos_start = None
        self.pos_end = None
        self.context = None

    def set_pos(self, pos_start=None, pos_end=None):
        self.pos_start = pos_start
        self.pos_end = pos_end
        return self

    def set_context(self, context=None):
        self.context = context
        return self

    def added_to(self, other):
        return None, self.illegal_operation(other)

    def subbed_by(self, other):
        return None, self.illegal_operation(other)

    def multed_by(self, other):
        return None, self.illegal_operation(other)

    def divided_by(self, other):
        return None, self.illegal_operation(other)

    def powed_by(self, other):
        return None, self.illegal_operation(other)

    def get_comparison_eq(self, other):
        return Number(int(value_eq(self, other))), None

    def get_comparison_ne(self, other):
        return Number(int(not value_eq(self, other))), None

    def get_comparison_lt(self, other):
        return None, self.illegal_operation(other)

    def get_comparison_gt(self, other):
        return None, self.illegal_operation(other)

    def get_comparison_lte(self, other):
        return None, self.illegal_operation(other)

    def get_comparison_gte(self, other):
        return None, self.illegal_operation(other)

    def anded_by(self, other):
        return None, self.illegal_operation(other)

    def ored_by(self, other):
        return None, self.illegal_operation(other)

    def notted(self, other=None):
        return None, self.illegal_operation(other)

    def execute(self, args):
        # Calling a non-callable value is a runtime error, not a Python one.
        return RTResult().failure(self.illegal_operation())

    def copy(self):
        raise Exception("No copy method defined.")

    def is_true(self):
        return False

    def illegal_operation(self, other=None):
        other = other if other is not None else self
        end = getattr(other, "pos_end", None) or self.pos_end
        context = self.context or getattr(other, "context", None)
        return RTError(self.pos_start, end, "Illegal operation", context)


class Number(Value):
    def __init__(self, value):
        super().__init__()
        self.value = value

    # -- arithmetic -----------------------------------------------------
    def added_to(self, other):
        if isinstance(other, Number):
            return Number(self.value + other.value).set_context(self.context), None
        return None, self.illegal_operation(other)

    def subbed_by(self, other):
        if isinstance(other, Number):
            return Number(self.value - other.value).set_context(self.context), None
        return None, self.illegal_operation(other)

    def multed_by(self, other):
        if isinstance(other, Number):
            return Number(self.value * other.value).set_context(self.context), None
        return None, self.illegal_operation(other)

    def divided_by(self, other):
        if isinstance(other, Number):
            if other.value == 0:
                return None, RTError(
                    other.pos_start or self.pos_start,
                    other.pos_end or self.pos_end,
                    "Division by zero",
                    self.context,
                )
            return Number(self.value / other.value).set_context(self.context), None
        return None, self.illegal_operation(other)

    def powed_by(self, other):
        if isinstance(other, Number):
            return Number(self.value ** other.value).set_context(self.context), None
        return None, self.illegal_operation(other)

    # -- comparison -----------------------------------------------------
    def get_comparison_eq(self, other):
        if isinstance(other, Number):
            return Number(int(self.value == other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    def get_comparison_ne(self, other):
        if isinstance(other, Number):
            return Number(int(self.value != other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    def get_comparison_lt(self, other):
        if isinstance(other, Number):
            return Number(int(self.value < other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    def get_comparison_gt(self, other):
        if isinstance(other, Number):
            return Number(int(self.value > other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    def get_comparison_lte(self, other):
        if isinstance(other, Number):
            return Number(int(self.value <= other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    def get_comparison_gte(self, other):
        if isinstance(other, Number):
            return Number(int(self.value >= other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    # -- logic ----------------------------------------------------------
    def anded_by(self, other):
        if isinstance(other, Number):
            return Number(int(bool(self.value) and bool(other.value))).set_context(self.context), None
        return None, self.illegal_operation(other)

    def ored_by(self, other):
        if isinstance(other, Number):
            return Number(int(bool(self.value) or bool(other.value))).set_context(self.context), None
        return None, self.illegal_operation(other)

    def notted(self, other=None):
        return Number(0 if self.value else 1).set_context(self.context), None

    def copy(self):
        return Number(self.value).set_pos(self.pos_start, self.pos_end).set_context(self.context)

    def is_true(self):
        return self.value != 0

    def __str__(self):
        return _format_number(self.value)

    def __repr__(self):
        return _format_number(self.value)


def _format_number(value) -> str:
    """Print a number exactly like Python does (3 -> "3", 3.0 -> "3.0").

    Pd-side formatting is a separate concern (``crude.pd.writer.fmt_atom``).
    """
    if isinstance(value, bool):
        return str(int(value))
    return str(value)


Number.null = Number(0)
Number.false = Number(0)
Number.true = Number(1)
Number.math_PI = Number(3.141592653589793)


class String(Value):
    def __init__(self, value):
        super().__init__()
        self.value = value

    def added_to(self, other):
        if isinstance(other, String):
            return String(self.value + other.value).set_context(self.context), None
        return None, self.illegal_operation(other)

    def multed_by(self, other):
        if isinstance(other, Number):
            return String(self.value * int(other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    def divided_by(self, other):
        """``s / i`` yields the i-th character (mirror of list indexing)."""
        if isinstance(other, Number):
            try:
                return String(self.value[int(other.value)]).set_context(self.context), None
            except IndexError:
                return None, RTError(
                    self.pos_start, self.pos_end,
                    "Character index out of bounds", self.context,
                )
        return None, self.illegal_operation(other)

    def get_comparison_eq(self, other):
        if isinstance(other, String):
            return Number(int(self.value == other.value)).set_context(self.context), None
        return Number(0).set_context(self.context), None

    def get_comparison_ne(self, other):
        if isinstance(other, String):
            return Number(int(self.value != other.value)).set_context(self.context), None
        return Number(1).set_context(self.context), None

    def get_comparison_lt(self, other):
        if isinstance(other, String):
            return Number(int(self.value < other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    def get_comparison_gt(self, other):
        if isinstance(other, String):
            return Number(int(self.value > other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    def get_comparison_lte(self, other):
        if isinstance(other, String):
            return Number(int(self.value <= other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    def get_comparison_gte(self, other):
        if isinstance(other, String):
            return Number(int(self.value >= other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    def anded_by(self, other):
        return Number(int(bool(self.value) and other.is_true())).set_context(self.context), None

    def ored_by(self, other):
        return Number(int(bool(self.value) or other.is_true())).set_context(self.context), None

    def notted(self, other=None):
        return Number(0 if self.value else 1).set_context(self.context), None

    def copy(self):
        return String(self.value).set_pos(self.pos_start, self.pos_end).set_context(self.context)

    def is_true(self):
        return len(self.value) > 0

    def __str__(self):
        return self.value

    def __repr__(self):
        return f'"{self.value}"'


class List(Value):
    def __init__(self, elements):
        super().__init__()
        self.elements = elements

    def added_to(self, other):
        new_list = self.copy()
        new_list.elements = list(self.elements)
        new_list.elements.append(other)
        return new_list, None

    def subbed_by(self, other):
        if isinstance(other, Number):
            new_list = self.copy()
            try:
                new_list.elements.pop(int(other.value))
                return new_list, None
            except IndexError:
                return None, RTError(
                    other.pos_start or self.pos_start,
                    other.pos_end or self.pos_end,
                    "Element at this index could not be removed: index out of bounds.",
                    self.context,
                )
        return None, self.illegal_operation(other)

    def multed_by(self, other):
        if isinstance(other, List):
            new_list = self.copy()
            new_list.elements = list(self.elements)
            new_list.elements.extend(other.elements)
            return new_list, None
        return None, self.illegal_operation(other)

    def divided_by(self, other):
        """Legacy index access: ``l / i``."""
        if isinstance(other, Number):
            try:
                return self.elements[int(other.value)], None
            except IndexError:
                return None, RTError(
                    other.pos_start or self.pos_start,
                    other.pos_end or self.pos_end,
                    "Element at this index could not be retrieved: index out of bounds.",
                    self.context,
                )
        return None, self.illegal_operation(other)

    def get_comparison_lt(self, other):
        if isinstance(other, Number):
            return Number(int(len(self.elements) < other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    def get_comparison_gt(self, other):
        if isinstance(other, Number):
            return Number(int(len(self.elements) > other.value)).set_context(self.context), None
        return None, self.illegal_operation(other)

    # -- helpers used by AT / SET_AT / LEN ---------------------------------
    def get(self, index: int):
        return self.elements[index]

    def set(self, index: int, value) -> None:
        self.elements[index] = value

    def copy(self):
        # Deliberately shares ``elements`` with the original: this language's
        # mutation builtins (APPEND, EXTEND, SET_AT) receive a copy of the
        # argument and rely on writing through to the variable's list.
        return List(self.elements).set_pos(self.pos_start, self.pos_end).set_context(self.context)

    def is_true(self):
        return len(self.elements) > 0

    def __str__(self):
        return ", ".join([str(x) for x in self.elements])

    def __repr__(self):
        return f'[{", ".join([repr(x) for x in self.elements])}]'


class BaseFunction(Value):
    def __init__(self, name):
        super().__init__()
        self.name = name or "<anonymous>"
        self.arg_names = []

    def generate_new_context(self):
        from .interpreter import Context, SymbolTable  # local import avoids a cycle

        new_context = Context(self.name, self.context, self.pos_start)
        new_context.symbol_table = SymbolTable(new_context.parent.symbol_table if new_context.parent else None)
        return new_context

    def check_args(self, arg_names, args, optional=0):
        res = RTResult()

        if len(args) > len(arg_names):
            return res.failure(RTError(
                self.pos_start, self.pos_end,
                f"{len(args) - len(arg_names)} too many args passed into '{self}'.",
                self.context,
            ))

        if len(args) < len(arg_names) - optional:
            return res.failure(RTError(
                self.pos_start, self.pos_end,
                f"{len(arg_names) - len(args)} too few args passed into '{self}'.",
                self.context,
            ))

        return res.success(None)

    def populate_args(self, arg_names, args, exec_ctx):
        for i in range(len(args)):
            arg_value = args[i]
            arg_value.set_context(exec_ctx)
            exec_ctx.symbol_table.set(arg_names[i], arg_value)

    def check_and_populate_args(self, arg_names, args, exec_ctx, optional=0):
        res = RTResult()
        res.register(self.check_args(arg_names, args, optional))
        if res.should_return():
            return res
        self.populate_args(arg_names, args, exec_ctx)
        return res.success(None)

    def __repr__(self):
        return f"<function {self.name}>"


class Function(BaseFunction):
    def __init__(self, name, body_node, arg_names, should_auto_return):
        super().__init__(name)
        self.body_node = body_node
        self.arg_names = list(arg_names)
        self.should_auto_return = should_auto_return

    def execute(self, args):
        from .interpreter import Interpreter  # local import avoids a cycle

        res = RTResult()
        interpreter = Interpreter()
        exec_ctx = self.generate_new_context()

        res.register(self.check_and_populate_args(self.arg_names, args, exec_ctx))
        if res.should_return():
            return res

        value = res.register(interpreter.visit(self.body_node, exec_ctx))
        if res.should_return() and res.func_return_value is None:
            return res

        ret_value = (value if self.should_auto_return else None) or res.func_return_value or Number.null
        return res.success(ret_value)

    def copy(self):
        return (
            Function(self.name, self.body_node, self.arg_names, self.should_auto_return)
            .set_context(self.context)
            .set_pos(self.pos_start, self.pos_end)
        )


class BuiltInFunction(BaseFunction):
    """A host binding: name + declared argument names + a plain Python function.

    ``func`` receives the execution context (whose symbol table holds one entry
    per declared argument name) and returns a Value, or raises ``BindingError``.
    """

    def __init__(self, name, arg_names=None, func=None, area="core", optional=0):
        super().__init__(name)
        self.arg_names = list(arg_names or [])
        self.func = func
        self.area = area
        self.optional = optional

    def execute(self, args):
        res = RTResult()
        exec_ctx = self.generate_new_context()

        res.register(self.check_and_populate_args(self.arg_names, args, exec_ctx, self.optional))
        if res.should_return():
            return res

        # Only the names this call passed are its arguments. Positional args are
        # set in this context and the rest are left alone, so an omitted optional
        # argument would otherwise be read straight out of the caller's scope by
        # name - a script with ``VAR level = 3`` would set every bare ``LOG``'s
        # level, and a binding would be arguing with the program's variables.
        exec_ctx.provided_args = frozenset(self.arg_names[:len(args)])

        if self.func is None:
            return res.failure(RTError(
                self.pos_start, self.pos_end,
                f"'{self.name}' has no implementation registered",
                exec_ctx,
            ))

        try:
            value = self.func(exec_ctx)
        except BindingError as exc:
            return res.failure(RTError(self.pos_start, self.pos_end, str(exc), exec_ctx))

        if value is None:
            value = Number.null
        return res.success(value)

    def copy(self):
        return (
            BuiltInFunction(self.name, self.arg_names, self.func, self.area, self.optional)
            .set_pos(self.pos_start, self.pos_end)
            .set_context(self.context)
        )

    def __repr__(self):
        return f"<built-in function {self.name}>"


class HostHandle(Value):
    """Opaque reference to a host object (widget, patch, resource, serial link).

    Copies return ``self`` so that reading a variable never duplicates - or
    detaches - the underlying object.
    """

    def __init__(self, target, kind="handle", label=""):
        super().__init__()
        self.target = target
        self.kind = kind
        self.label = label

    def copy(self):
        return self

    def is_true(self):
        return True

    def get_comparison_eq(self, other):
        return Number(int(value_eq(self, other))).set_context(self.context), None

    def get_comparison_ne(self, other):
        return Number(int(not value_eq(self, other))).set_context(self.context), None

    def __str__(self):
        return self.label or f"<{self.kind}>"

    def __repr__(self):
        return f"<{self.kind}:{self.label or hex(id(self.target))}>"


# -- host boundary -------------------------------------------------------
# Callbacks travel host -> script (``Program.invoke``) and arguments travel
# script -> host (``PD_SEND_LIST``, presets, ...), so both directions need one
# canonical conversion instead of ad-hoc ``isinstance`` ladders everywhere.


def python_to_value(obj):
    """Wrap a native Python object as a language value."""
    if isinstance(obj, Value):
        return obj
    if isinstance(obj, bool):
        return Number(1 if obj else 0)
    if isinstance(obj, (int, float)):
        return Number(obj)
    if obj is None:
        return Number.null
    if isinstance(obj, str):
        return String(obj)
    if isinstance(obj, (list, tuple)):
        return List([python_to_value(item) for item in obj])
    return HostHandle(obj)


def value_to_python(value):
    """Unwrap a language value into plain Python (handles become their target)."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, List):
        return [value_to_python(item) for item in value.elements]
    if isinstance(value, HostHandle):
        return value.target
    return value
