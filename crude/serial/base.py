"""The serial layer's shared vocabulary: the port protocol and the URI dispatcher.

Nothing here imports ``serial`` - the pyserial adapter lives in
:mod:`crude.serial.pyserial` and is only touched when a real port is actually
wanted. That is what keeps the whole serial surface testable without hardware,
in the same way :mod:`crude.audio.null` keeps the audio surface testable without
a sound card: the plan's rule (fake-first) says a missing device is a warning,
not a failure, and a test should never need ``/dev/ttyUSB0`` to exist.

A :class:`SerialPort` is the smallest shape a link can drive:

* ``open`` / ``close`` / ``is_open`` - the lifecycle the link's reader thread
  obeys and reconnects through,
* ``write(data)`` - returns the number of bytes the port accepted,
* ``read(size)`` - the blocking read the thread runs on, bounded by whatever
  timeout the concrete port has; a port that has nothing never returns partial
  frames because it is expected to have drained its own buffer first,
* ``in_waiting()`` - the count of bytes the port would return without blocking,
  which the link uses on the ``pump_once`` path (the deterministic one a test
  drives) to decide whether to try a read at all.

Every URI scheme the runtime understands is registered here so a project can
say ``loop://synth`` in a manifest and a test can inject a
:class:`~crude.serial.fake.MemorySerialPort` with no other plumbing.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple


class SerialError(RuntimeError):
    """A port or codec problem the bindings turn into a script error."""


class SerialUnavailable(SerialError):
    """This port could not be opened here: no module, no device, no permission.

    The runtime treats it as "warn and skip" when the manifest marks the link
    optional, and as a load failure when it does not.
    """


#: The port protocol a link is willing to drive. Concrete ports do not
#  inherit from this on purpose - a duck-typed port is as good as a subclass,
#  which is what lets a test hand over a plain object with the same methods.
PROTOCOL = ("open", "close", "is_open", "write", "read", "in_waiting")


def check_port(port: Any) -> Any:
    """Fail early with a clear message when an object is not a port."""
    for name in PROTOCOL:
        if not hasattr(port, name):
            raise SerialError(
                f"serial port {port!r} does not speak the port protocol "
                f"(missing .{name}())"
            )
    return port


# -- the URI registry ---------------------------------------------------

#: scheme -> ``(factory(uri, baud) -> port)``; ``""`` is the fallback for a
#  bare device name like ``COM3`` or ``/dev/ttyUSB0`` (which pyserial opens).
PORT_SCHEMES: Dict[str, Callable[[str, int], Any]] = {}


def register_port(scheme: str, factory: Callable[[str, int], Any]) -> None:
    """Add a URI scheme a link may be asked to open.

    The empty string is the "no scheme" fallback; pyserial registers itself
    there the first time :func:`open_port` is called and the module is
    importable.
    """
    PORT_SCHEMES[scheme] = factory


def split_uri(uri: str) -> Tuple[str, str]:
    """``(scheme, rest)`` for a port URI; ``scheme`` is ``""`` when there is none.

    ``loop://board`` splits into ``("loop", "board")``, ``COM3`` into
    ``("", "COM3")``, and a Windows path with a colon in it (``COM:1``) into
    ``("", "COM:1")`` because the part before ``://`` is empty - a scheme only
    counts if it is followed by ``//``.
    """
    text = str(uri)
    marker = text.find("://")
    if marker <= 0:
        return "", text
    return text[:marker].lower(), text[marker + 3:]


def open_port(uri: str, baud: int = 115200) -> Any:
    """Turn a manifest's port string into a live port object.

    Unknown schemes are refused rather than passed to pyserial: a typo'd
    ``memory://`` would otherwise open a real ``memory:`` device on some
    systems, and a link should fail with the scheme it was given.
    """
    scheme, rest = split_uri(uri)
    if scheme:
        factory = PORT_SCHEMES.get(scheme)
        if factory is None:
            known = ", ".join(sorted(name for name in PORT_SCHEMES if name))
            raise SerialError(
                f"there is no serial scheme '{scheme}://' "
                f"(registered: {known or 'none'}); a bare device name like "
                f"'COM3' or '/dev/ttyUSB0' does not need a scheme"
            )
        return factory(rest, int(baud))

    # No scheme: hand to pyserial through the fallback factory it registers.
    fallback = PORT_SCHEMES.get("")
    if fallback is None:
        _ensure_default()
        fallback = PORT_SCHEMES.get("")
    if fallback is None:
        raise SerialUnavailable(
            f"cannot open '{uri}': pyserial is not importable and no port "
            f"handler was registered for a bare device name"
        )
    return fallback(str(uri), int(baud))


def _ensure_default() -> None:
    """Register the pyserial fallback the first time a real port is wanted.

    Kept out of module import so this file imports without pyserial installed
    and a fake-only test never pays for the ``serial`` symbol.
    """
    from .pyserial import register as _register_pyserial

    _register_pyserial()


__all__ = [
    "SerialError",
    "SerialUnavailable",
    "PROTOCOL",
    "check_port",
    "PORT_SCHEMES",
    "register_port",
    "split_uri",
    "open_port",
]
