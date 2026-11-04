"""Host bindings, declared once and installed into a program's symbol table.

Importing this package registers every core binding (``bindings/core.py``);
later stages add ``pd``/``audio``/``sound``/``ui``/``serial`` modules whose
decorators register themselves the same way, then ``install(table, area=...)``
can restrict a table to one area when a runtime does not support it.
"""

from __future__ import annotations

from .registry import (  # noqa: F401
    REGISTRY,
    Binding,
    HostContext,
    binding,
    get_binding,
    install,
    wrap,
)
from . import core  # noqa: F401  (registers the core bindings on import)
from . import pd  # noqa: F401  (registers the PD_* / ON_PD bindings)
from . import pd_author  # noqa: F401  (registers the patch-authoring bindings)
from . import audio  # noqa: F401  (registers the AUDIO_* bindings)
from . import sound  # noqa: F401  (registers the WAV_* / AUDIO_RECORD* bindings)
from . import midi  # noqa: F401  (registers the MIDI_* / ON_MIDI bindings)
from . import project  # noqa: F401  (registers RES / PRESET_* / the run bindings)
from . import ui  # noqa: F401  (registers the widget bindings and SET/GET's widget half)
from . import menu  # noqa: F401  (registers the MENU_* in-window menu, area "ui")
from . import serial  # noqa: F401  (registers the SERIAL_* / ON_SERIAL bindings)

__all__ = [
    "REGISTRY",
    "Binding",
    "HostContext",
    "binding",
    "get_binding",
    "install",
    "wrap",
    "core",
    "pd",
    "pd_author",
    "audio",
    "sound",
    "midi",
    "project",
    "ui",
    "menu",
    "serial",
]
