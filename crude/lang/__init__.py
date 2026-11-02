"""The crudelang front end: lexer, parser, values, interpreter and program.

``from crude.lang import *`` is the supported way to reach the language - the
old ``crudelang.py`` module is a thin compatibility shim over this package.
"""

from __future__ import annotations

from .tokens import *  # noqa: F401,F403
from .position import *  # noqa: F401,F403
from .errors import *  # noqa: F401,F403
from .formatting import *  # noqa: F401,F403
from .result import *  # noqa: F401,F403
from .lexer import *  # noqa: F401,F403
from .nodes import *  # noqa: F401,F403
from .parser import *  # noqa: F401,F403
from .values import *  # noqa: F401,F403
from .interpreter import *  # noqa: F401,F403
from .program import *  # noqa: F401,F403
from . import program  # noqa: F401  (explicit module handle for late imports)

from .program import (  # noqa: F401
    Program,
    build_symbol_table,
    default_symbol_table,
    reset_default_symbol_table,
    run,
)
