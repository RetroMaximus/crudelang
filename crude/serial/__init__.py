"""Serial: a link, a codec, a reader thread, an inbox, and four script bindings.

This package is the serial half of the host. A script never imports it - it
calls the ``SERIAL_*`` bindings, which talk to a
:class:`~crude.serial.runtime.SerialRuntime`, which owns one
:class:`~crude.serial.link.SerialLink` per declared device.

The plan's S10 rule everything below is built around: **a missing port is a
warning, not a failure**, so a manifest that marks a link optional comes up
without it, and :class:`~crude.serial.fake.MemorySerialPort` and
``loop://`` carry the whole surface into a test without pyserial, a device or
a second process.

Nothing here imports :mod:`serial` at module scope; :mod:`crude.serial.pyserial`
does it lazily the first time a real port is opened, which is why a machine
without the wheel can still ``import crude.serial`` and every test in
``tests/serial`` passes.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Tuple, Type

from .base import (
    PORT_SCHEMES,
    PROTOCOL,
    SerialError,
    SerialUnavailable,
    check_port,
    open_port,
    register_port,
    split_uri,
)
from .codecs import (
    CODECS,
    Codec,
    CodecError,
    FloatCodec,
    IntCodec,
    LineCodec,
    ListCodec,
    RawCodec,
    codec_for,
    codec_names,
    register_codec,
)
from .fake import MemorySerialPort, pair
from .inbox import SerialEvent, SerialInbox
from .link import SerialLink
from .loop import LoopSerialPort
from .runtime import SerialHandler, SerialRuntime

# The loop:// port and its registry are always available - it is the whole
# reason a bridge example can be tested without pyserial. Memory ports are
# injected directly by a test, so they do not need a URI scheme of their own,
# but a "memory:" alias is convenient in a manifest for a demonstration run.
LoopSerialPort  # re-export keeps linters from pruning the import
register_port("loop", lambda rest, baud: LoopSerialPort(rest or "loop"))
register_port("memory", lambda rest, baud: MemorySerialPort(rest or "memory"))


__all__ = [
    # base
    "SerialError",
    "SerialUnavailable",
    "PROTOCOL",
    "check_port",
    "PORT_SCHEMES",
    "register_port",
    "split_uri",
    "open_port",
    # ports
    "MemorySerialPort",
    "LoopSerialPort",
    "pair",
    # codecs
    "Codec",
    "CodecError",
    "RawCodec",
    "LineCodec",
    "IntCodec",
    "FloatCodec",
    "ListCodec",
    "CODECS",
    "register_codec",
    "codec_names",
    "codec_for",
    # inbox
    "SerialEvent",
    "SerialInbox",
    # link + runtime
    "SerialLink",
    "SerialRuntime",
    "SerialHandler",
]
