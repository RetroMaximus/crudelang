"""The pyserial adapter: the only file that touches ``import serial``.

Kept lazy so a machine without pyserial still imports
:mod:`crude.serial.fake` and :mod:`crude.serial.loop` cleanly and every test
in ``tests/serial/`` passes without the wheel. A bare device name from a
manifest (``COM3``, ``/dev/ttyUSB0``) reaches this module through
:func:`crude.serial.base.open_port`, which calls :func:`register` on the way
past it if nothing has registered the fallback yet.

The adapter is a thin shape change: pyserial's ``Serial`` object already speaks
most of the port protocol; only ``is_open`` is spelled differently (there it
is ``is_open`` as an attribute, not a property, so we wrap it). Errors from
the underlying library become :class:`SerialError` so the bindings never see a
raw ``OSError``.
"""

from __future__ import annotations

from typing import Any, Optional

from .base import SerialError, SerialUnavailable


class PySerialPort:
    """Wraps a ``serial.Serial`` in the port protocol crude uses."""

    name = "pyserial"

    def __init__(self, device: str, baud: int = 115200, *,
                 timeout: float = 0.05,
                 backend_factory: Optional[Any] = None):
        self.device = str(device)
        self.baud = int(baud)
        #: short timeout so the reader thread checks ``stop_event`` often; a
        #  blocking read that never returns is how a shutdown hangs
        self.timeout = float(timeout)
        #: the pyserial class, or ``None`` to look it up lazily on first open
        self._factory = backend_factory
        self._serial: Optional[Any] = None

    # -- module lookup --------------------------------------------------
    @classmethod
    def available(cls) -> bool:
        """Can a pyserial port be built at all on this machine?"""
        try:
            import serial  # noqa: F401 - the lazy probe the plan insists on
        except Exception:
            return False
        return True

    def _ensure_module(self):
        try:
            import serial
        except Exception as exc:  # pragma: no cover - only on a machine without it
            raise SerialUnavailable(
                f"pyserial is not importable ({exc}); install pyserial or use "
                f"a loop:// or memory:// port for this run"
            )
        return serial

    # -- protocol -------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._serial is not None and bool(getattr(self._serial, "is_open", False))

    def open(self) -> None:
        if self.is_open:
            return
        factory = self._factory
        if factory is None:
            module = self._ensure_module()
            factory = module.Serial
        try:
            self._serial = factory(self.device, self.baud, timeout=self.timeout)
        except SerialError:
            raise
        except Exception as exc:
            raise SerialUnavailable(f"could not open '{self.device}': {exc}")

    def close(self) -> None:
        held, self._serial = self._serial, None
        if held is None:
            return
        try:
            held.close()
        except Exception:  # pragma: no cover - fighting back on close is not fatal
            pass

    def write(self, data) -> int:
        if not self.is_open:
            raise SerialError(f"{self.device}: write on a closed port")
        payload = data if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8")
        try:
            return int(self._serial.write(payload))
        except Exception as exc:
            raise SerialError(f"{self.device}: write failed: {exc}")

    def read(self, size: int = 1) -> bytes:
        if not self.is_open:
            raise SerialError(f"{self.device}: read on a closed port")
        try:
            return bytes(self._serial.read(int(size)))
        except Exception as exc:
            raise SerialError(f"{self.device}: read failed: {exc}")

    def in_waiting(self) -> int:
        if not self.is_open:
            return 0
        try:
            return int(getattr(self._serial, "in_waiting", 0) or 0)
        except Exception:  # pragma: no cover - attribute error on a weird device
            return 0

    def describe(self) -> str:
        return f"{self.device} @ {self.baud}"

    def __repr__(self):
        return f"<PySerialPort {self.device} {self.baud} open={self.is_open}>"


def _factory(rest: str, baud: int) -> PySerialPort:
    """The scheme-less fallback the base registry installs on demand."""
    return PySerialPort(rest, baud)


def register() -> None:
    """Add the bare-device fallback to :data:`crude.serial.base.PORT_SCHEMES`."""
    from .base import register_port

    register_port("", _factory)


__all__ = ["PySerialPort", "register"]
