"""Compatibility shim for the original single-file interpreter.

The interpreter now lives in the :mod:`crude.lang` package (lexer, parser,
values, interpreter, program) with host functions declared in
:mod:`crude.bindings`. This module keeps the historical import surface working:

    import crudelang
    result, error = crudelang.run("<stdin>", "PRINT(1)")

``run`` uses one long-lived symbol table, exactly like the old module-level
``global_symbol_table``, so ``shell.py``'s REPL still accumulates state.
"""

from __future__ import annotations

from crude.lang import *  # noqa: F401,F403
from crude.lang import run  # noqa: F401
from crude.lang.program import default_symbol_table


def __getattr__(name):
    """``crudelang.global_symbol_table`` resolved lazily.

    It used to be a module-level object; resolving on access keeps it pointing
    at the live table even after ``reset_default_symbol_table()``.
    """
    if name == "global_symbol_table":
        return default_symbol_table()
    raise AttributeError(f"module 'crudelang' has no attribute {name!r}")
