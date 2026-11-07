"""Codecs: how a stream of bytes becomes a stream of frames the script can hold.

A link's reader thread always gets *bytes*, but what the script wants in an
``ON_SERIAL`` handler depends on what is on the other end of the wire:

* a sensor that answers ``"42\\n"`` wants a **line** codec - the script gets
  the string ``"42"``;
* a MIDI-ish thing that pushes fixed packets wants a **raw** codec - the
  script gets the bytes;
* a device that sends ``"1,2,3\\n"`` and Pd wants three floats wants a **list**
  codec - the script gets ``[1.0, 2.0, 3.0]``.

Every codec is a small object with three members: ``name``, ``encode(value)``
(a script's write becomes bytes) and ``feed(chunk)`` which buffers and returns
a list of *frames* ready for the inbox. A codec owns its own buffer, so one
codec instance belongs to one link.

The plan's manifest section says a codec is any name the serial stage knows;
adding a new one never needs a schema change. The registry here is that stage.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


class CodecError(ValueError):
    """A codec was asked to encode or decode something it cannot."""


class Codec:
    """Base codec: subclasses override :meth:`feed` and :meth:`encode`."""

    name: str = "raw"

    def __init__(self, terminator: bytes = b"\n"):
        self.buffer: bytearray = bytearray()
        self.terminator = terminator
        self.frames = 0
        self.dropped = 0

    # -- read path ------------------------------------------------------
    def feed(self, chunk) -> List[Any]:
        """Push ``chunk`` into the buffer, return every frame completed by it."""
        raise NotImplementedError

    # -- write path -----------------------------------------------------
    def encode(self, value: Any) -> bytes:
        """Turn a script value into bytes for the wire."""
        raise NotImplementedError

    # -- shared helpers -------------------------------------------------
    def _split_on_terminator(self) -> List[bytes]:
        """Pop every terminated line off the buffer; leaves the remainder in place."""
        out: List[bytes] = []
        while True:
            index = self.buffer.find(self.terminator)
            if index < 0:
                return out
            line = bytes(self.buffer[:index])
            del self.buffer[:index + len(self.terminator)]
            out.append(line)

    def flush(self) -> List[Any]:
        """Frames from whatever is left at end of stream (no terminator)."""
        if not self.buffer:
            return []
        leftover = bytes(self.buffer)
        self.buffer.clear()
        return [self.decode_partial(leftover)]

    def decode_partial(self, data: bytes) -> Any:
        """The last-chunk interpretation; ``line`` uses this for EOF flush."""
        return _to_text(data)

    def __repr__(self):
        return f"<{type(self).__name__} {self.name} frames={self.frames}>"


# -- the four built-ins -------------------------------------------------

class RawCodec(Codec):
    """Every read chunk is one frame, untouched. Fixed-packet protocols live here.

    ``encode`` still goes through :func:`_as_bytes` so a script can write a
    string as a header; the codec does not add a terminator because the wire
    is exactly the bytes the device speaks.
    """

    name = "raw"

    def feed(self, chunk) -> List[Any]:
        payload = bytes(chunk)
        if not payload:
            return []
        self.frames += 1
        return [payload]

    def encode(self, value: Any) -> bytes:
        return _as_bytes(value)


class LineCodec(Codec):
    """Split on ``\\n``; the script gets a ``str`` (UTF-8, whitespace-stripped).

    This is the plan's default codec and what an Arduino ``Serial.println``
    sends back.
    """

    name = "line"

    def feed(self, chunk) -> List[Any]:
        self.buffer.extend(chunk)
        out: List[Any] = []
        for line in self._split_on_terminator():
            text = _to_text(line).strip()
            self.frames += 1
            out.append(text)
        return out

    def encode(self, value: Any) -> bytes:
        return _as_bytes(value) + self.terminator


class IntCodec(Codec):
    """One integer per line; a non-integer is dropped with the count kept."""

    name = "int"

    def feed(self, chunk) -> List[Any]:
        self.buffer.extend(chunk)
        out: List[Any] = []
        for line in self._split_on_terminator():
            try:
                out.append(int(_to_text(line).strip()))
                self.frames += 1
            except ValueError:
                self.dropped += 1
        return out

    def encode(self, value: Any) -> bytes:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise CodecError(f"int codec cannot encode {value!r}: {exc}")
        return str(number).encode("ascii") + self.terminator


class FloatCodec(Codec):
    """One float per line; same tolerance rule as :class:`IntCodec`."""

    name = "float"

    def feed(self, chunk) -> List[Any]:
        self.buffer.extend(chunk)
        out: List[Any] = []
        for line in self._split_on_terminator():
            try:
                out.append(float(_to_text(line).strip()))
                self.frames += 1
            except ValueError:
                self.dropped += 1
        return out

    def encode(self, value: Any) -> bytes:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise CodecError(f"float codec cannot encode {value!r}: {exc}")
        return repr(number).encode("ascii") + self.terminator


class ListCodec(Codec):
    """A comma-separated line of numbers -> ``[float, ...]``.

    Used for ``list 1,2,3`` sensor protocols and for Pd's own
    ``[list ...]``-style messages when they cross a serial bridge.
    """

    name = "list"

    def feed(self, chunk) -> List[Any]:
        self.buffer.extend(chunk)
        out: List[Any] = []
        for line in self._split_on_terminator():
            parts = [part.strip() for part in _to_text(line).split(",")]
            parts = [part for part in parts if part]
            try:
                values = [float(part) for part in parts]
            except ValueError:
                self.dropped += 1
                continue
            self.frames += 1
            out.append(values)
        return out

    def encode(self, value: Any) -> bytes:
        if isinstance(value, (list, tuple)):
            body = ",".join(str(_plain_number(item)) for item in value)
        else:
            body = str(_plain_number(value))
        return body.encode("ascii") + self.terminator


#: Every codec the runtime registers on import; a project can add another with
#  :func:`register_codec` and the manifest will accept it by name.
CODECS: Dict[str, Callable[..., Codec]] = {
    cls.name: cls for cls in (RawCodec, LineCodec, IntCodec, FloatCodec, ListCodec)
}


def register_codec(cls: Callable[..., Codec], name: str = None) -> Callable[..., Codec]:
    """Add a codec class to the registry, keyed by name (default: ``cls.name``)."""
    key = name or getattr(cls, "name", None) or cls.__name__
    CODECS[key] = cls
    return cls


def codec_names() -> Tuple[str, ...]:
    return tuple(sorted(CODECS))


def codec_for(name: str, **kwargs) -> Codec:
    """Build a fresh codec by name; unknown names fail with the list of valid ones."""
    cls = CODECS.get(name)
    if cls is None:
        raise CodecError(
            f"there is no serial codec named '{name}' "
            f"(known: {', '.join(codec_names())})"
        )
    return cls(**kwargs)


# -- shared coercion ----------------------------------------------------

def _as_bytes(data) -> bytes:
    """Accept the shapes a script hands in: ``bytes``, ``str``, or a list of ints."""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        return data.encode("utf-8")
    if isinstance(data, (list, tuple)):
        return bytes(int(b) & 0xFF for b in data)
    if isinstance(data, (int, float)):
        return str(data).encode("ascii")
    raise CodecError(f"a serial write wants bytes or a string, got {type(data).__name__}")


def _to_text(data: bytes) -> str:
    """Decode bytes leniently; the plan says a frame that is not UTF-8 is a warning, not a crash."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _plain_number(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


__all__ = [
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
]
