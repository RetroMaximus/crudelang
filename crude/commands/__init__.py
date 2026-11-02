"""CLI command modules.

Importing this package registers every command the current stage has finished:
each module decorates its handler with :func:`crude.cli.command`, so the table in
:mod:`crude.cli` is the only place commands are listed. A stage adds a module
here and a line below - nothing else changes.
"""

from __future__ import annotations

from . import pd  # noqa: F401 - registers "pd"
from . import project  # noqa: F401 - registers "run", "check" and "init"
from . import widgets  # noqa: F401 - registers "widgets"
from . import glyphs  # noqa: F401 - registers "glyphs"

__all__ = ["pd", "project", "widgets", "glyphs"]
