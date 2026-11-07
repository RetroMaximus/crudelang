"""Program assembly: symbol tables, and the legacy one-shot ``run()``.

Two entry points, on purpose:

* ``Program(...)`` owns its own symbol table, so tests and the runtime never
  leak variables between programs;
* ``run(fn, text)`` keeps the historical module-level table so the REPL in
  ``shell.py`` accumulates state exactly like the original interpreter did.
"""

from __future__ import annotations

from .errors import RTError
from .interpreter import Context, Interpreter, SymbolTable
from .lexer import Lexer
from .parser import Parser
from .values import List, Number, python_to_value, type_name

_CONSTANTS = {
    "null": Number.null,
    "False": Number.false,
    "TRUE": Number.true,
    "MATH_PI": Number.math_PI,
}


def _not_callable(value):
    return RTError(None, None, f"{type_name(value)} is not callable", None)


def _attach_host(program, host):
    """Tell the host - and every subsystem hanging off it - its program.

    Callbacks (``ON_PD``, ``ON_CHANGE``, ``ON_SERIAL``) are script functions, so
    the subsystem that fires them needs a way back into the interpreter. A host
    may implement ``attach_program`` itself and forward; otherwise each
    attribute that knows how is told directly.
    """
    if host is None:
        return

    attach = getattr(host, "attach_program", None)
    if callable(attach):
        attach(program)

    for value in list(getattr(host, "__dict__", {}).values()):
        attach = getattr(value, "attach_program", None)
        if callable(attach):
            attach(program)


def build_symbol_table(host=None):
    """A fresh global table: language constants plus every registered binding.

    ``host`` is carried for later stages (subsystem access for bindings); the
    core language itself never needs it.
    """
    from ..bindings import install  # local: keeps ``crude.lang`` import-light

    table = SymbolTable()
    for name, value in _CONSTANTS.items():
        table.set(name, value)
    if host is not None:
        table.host = host
    install(table)
    return table


class Program:
    """A parsed-and-run unit of ``.crude`` source with its own globals."""

    def __init__(self, name="<program>", symbol_table=None, host=None):
        self.name = name
        self.host = host
        self.symbol_table = symbol_table if symbol_table is not None else build_symbol_table(host)
        if host is not None and symbol_table is not None:
            self.symbol_table.host = host
        _attach_host(self, host)

    def parse(self, text):
        """Return ``(node, error)`` for ``text``."""
        tokens, error = Lexer(self.name, text).make_tokens()
        if error:
            return None, error

        ast = Parser(tokens).parse()
        if ast.error:
            return None, ast.error

        return ast.node, None

    def execute(self, text):
        """Run ``text`` and return ``(statement_list_value, error)``.

        The top-level node is a statement list, so the raw result is a ``List``
        with one element per statement - that is what the REPL inspects.
        """
        node, error = self.parse(text)
        if error:
            return None, error

        result = Interpreter().visit(node, self.context())
        return result.value, result.error

    def run(self, text):
        """Run ``text`` and return ``(last_statement_value, error)``."""
        value, error = self.execute(text)
        if error:
            return None, error
        if isinstance(value, List):
            return (value.elements[-1] if value.elements else Number.null), None
        return value, None

    def run_file(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.name = path
        return self.run(text)

    def execute_file(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.name = path
        return self.execute(text)

    def invoke(self, func, args=()):
        """Call a script function from host code; returns ``(value, error)``.

        This is how Pd events, widget callbacks and timers re-enter the
        language: the caller stays on the interpreter thread and only hands
        plain Python values (floats, strings, lists) across.
        """
        if not hasattr(func, "execute"):
            return None, _not_callable(func)

        values = [python_to_value(arg) for arg in args]
        if getattr(func, "context", None) is None:
            func.set_context(self.context())

        result = func.execute(values)
        return result.value, result.error

    def context(self):
        """A context whose symbol table is this program's globals."""
        context = Context(self.name)
        context.symbol_table = self.symbol_table
        return context

    def __repr__(self):
        return f"<Program {self.name}>"


_default_symbol_table = None


def default_symbol_table():
    """The shared table behind the legacy ``run()`` (REPL state lives here)."""
    global _default_symbol_table
    if _default_symbol_table is None:
        _default_symbol_table = build_symbol_table()
    return _default_symbol_table


def reset_default_symbol_table():
    """Drop REPL state (used by tests and by ``Program`` consumers)."""
    global _default_symbol_table
    _default_symbol_table = None


def run(fn, text):
    """Legacy API: run ``text`` against the shared symbol table.

    Returns the raw statement list (``[value, ...]``), which is what
    ``shell.py`` has always consumed.
    """
    return Program(fn, symbol_table=default_symbol_table()).execute(text)


def __getattr__(name):
    # ``crudelang.global_symbol_table`` was a module-level object in the
    # original implementation; keep the name working for external scripts.
    if name == "global_symbol_table":
        return default_symbol_table()
    raise AttributeError(name)
