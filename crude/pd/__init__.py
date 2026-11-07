"""Pure Data integration: the libpd wrapper, the session and the script API.

The layers, bottom to top:

* :mod:`crude.pd.lib` - ``Libpd``, the ctypes wrapper with one complete,
  typed signature table, plus the DLL bootstrap;
* :mod:`crude.pd.fake` - ``FakeLibpd``, the same protocol in memory, so every
  test above this line runs without a DLL or a device;
* :mod:`crude.pd.inbox` / :mod:`crude.pd.hooks` - the only bridge from the
  audio thread to the interpreter: bounded queues of copied events;
* :mod:`crude.pd.engine` - ``PdSession`` (patch lifecycle, control plane) and
  ``PdScheduler`` (the data plane the audio callback drains);
* :mod:`crude.pd.runtime` - ``PdRuntime``, which owns the patches and turns
  queued events into calls to ``.crude`` handlers;
* :mod:`crude.pd.model` / :mod:`crude.pd.writer` / :mod:`crude.pd.reader` -
  patches a script authors, as text a real Pd can open;
* :mod:`crude.pd.pdgui` - the real Pd GUI box (``tgl``/``hslider``/``nbx``/...),
  its version-stamped argument layout, for ``PD_GUI`` and ``PD_EMIT``;
* :mod:`crude.pd.cache` - the ``.pd`` files those authored patches become.
"""

from .cache import DEFAULT_BUILD_DIR, BuildCache
from .engine import PdError, PdJob, PdPatch, PdScheduler, PdSession
from .fake import FakeLibpd
from .hooks import HookBridge, PrintAssembler
from .inbox import PdEvent, PdInbox
from .lib import Libpd, LibpdNotFound, dll_bootstrap, find_library
from .model import Box, Canvas, Link, PatchDoc, PatchError
from .pdgui import (GuiError, PD_VERSION, arguments, box_tag, format_box,
                    has_template, sized)
from .reader import describe, parse_patch, read_file
from .runtime import MAX_HANDLER_ERRORS, PdHandler, PdRuntime
from .writer import write_file, write_patch

__all__ = [
    "Box",
    "BuildCache",
    "Canvas",
    "DEFAULT_BUILD_DIR",
    "FakeLibpd",
    "GuiError",
    "HookBridge",
    "Libpd",
    "LibpdNotFound",
    "Link",
    "MAX_HANDLER_ERRORS",
    "PD_VERSION",
    "PatchDoc",
    "PatchError",
    "PdError",
    "PdEvent",
    "PdHandler",
    "PdInbox",
    "PdJob",
    "PdPatch",
    "PdRuntime",
    "PdScheduler",
    "PdSession",
    "PrintAssembler",
    "arguments",
    "box_tag",
    "describe",
    "dll_bootstrap",
    "find_library",
    "format_box",
    "has_template",
    "parse_patch",
    "read_file",
    "sized",
    "write_file",
    "write_patch",
]
