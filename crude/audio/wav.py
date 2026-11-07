"""Uncompressed ``.wav`` in and out, with no dependency beyond the standard library.

A recorder that cannot put a file on disk has only paraphrased audio, so this
module is the codec: :func:`read_wav` decodes PCM into the same ``-1..+1`` floats
every other part of the audio layer already speaks, and :func:`write_wav` encodes
them back into a file another program can open.

Two decisions are worth stating because they are the ones a caller cannot see:

* **floats in, floats out.** The language's samples are always ``-1..+1``, whether
  they came from a device block, a Pd table or a file, so the bit depth is a
  property of the *file* and never of the script. A 16-bit file read at ``0.5``
  and written back at 8 bits is the same number until the quantiser says otherwise.
* **only what ``wave`` really understands.** Plain PCM at 8 or 16 bits, any
  channel count. A 24-bit file, a 32-bit float file or a compressed one is
  refused by name, with what was found in the message - the alternative is
  decoding garbage as if it were sound, which is the kind of fake this project
  refuses rather than the kind it rounds off.

``wave`` is not thread-safe and this module is never called from an audio
callback: it belongs to whoever asks for a file, which is the script's thread.
"""

from __future__ import annotations

import array as _array
import os
import sys
import wave
from dataclasses import dataclass, field
from typing import List, Sequence

from .base import AudioError

#: The two PCM widths this codec reads and writes. Everything else in a RIFF
#: ``fmt `` chunk is either a bigger integer (24-bit), a float (32-bit) or a
#: codec, and all three are refused rather than guessed at.
PCM8 = 8
PCM16 = 16
WIDTHS = (PCM8, PCM16)

#: the largest positive value each width can hold, as the integer a float is
#  scaled by on the way out
FULL_SCALE = {PCM8: 127.0, PCM16: 32767.0}

#: the smallest ``sampwidth`` in bytes a RIFF chunk can carry and still be PCM
_BYTE_WIDTHS = {1: PCM8, 2: PCM16}


def _refuse(what: str) -> AudioError:
    """One message, spelled the same wherever a file turns out not to be sound."""
    return AudioError(f"not a wav file crude can read: {what}")


@dataclass
class WavData:
    """One wav file: its shape, and - when it was read whole - one float list
    per channel.

    The planes are *de-interleaved* because that is what everything downstream
    wants - a sampler loads one channel into one table, and a plot draws one
    channel at a time - while a file, a device block and a ``.wav`` header are
    all interleaved. ``frames`` is the length of a plane, not of every plane
    added together, and it is stored rather than counted so a caller can say how
    long a sample is without walking two million floats.
    """

    path: str = ""
    rate: int = 0
    channels: int = 1
    bits: int = PCM16
    frames: int = 0
    planes: List[List[float]] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        """How long the sound is, at the rate the file said it would be played."""
        return self.frames / float(self.rate) if self.rate else 0.0

    @property
    def samples(self) -> List[float]:
        """Channel 0 - the plane a mono sampler loads and a plot draws."""
        return list(self.planes[0]) if self.planes else []

    def mix(self) -> List[float]:
        """Every plane averaged into one, the way :class:`~crude.audio.base.Taps`
        scopes a stereo stream. One channel in, the same channel out."""
        if not self.planes:
            return []
        if len(self.planes) == 1:
            return list(self.planes[0])
        across = zip(*self.planes)
        return [sum(values) / float(len(values)) for values in across]

    def describe(self) -> str:
        return (f"{self.rate} Hz, {self.channels} channel(s), {self.bits}-bit, "
                f"{self.frames} frame(s) ({self.seconds:.3f} s)")

    def __str__(self) -> str:
        return f"WavData {os.path.basename(self.path) or 'memory'}: {self.describe()}"


# -- decode ----------------------------------------------------------------


def _checked(path) -> tuple:
    """The shape of a file, every refusal paid for, its samples left on disk.

    Both readers start here, so "is this a wav crude can read" is asked - and
    answered - in exactly one place, whether or not the caller goes on to decode
    two million floats.
    """
    name = os.fspath(path)
    if not os.path.isfile(name):
        raise _refuse(f"'{name}' is not a file that is there")

    try:
        handle = wave.open(name, "rb")
    except Exception as exc:  # wave raises what its C reader found, in its own words
        raise _refuse(f"'{name}' could not be opened as a wav file ({exc})") from exc

    with handle:
        channels = handle.getnchannels()
        sampwidth = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.getnframes()
        comptype = handle.getcomptype()

    if comptype != "NONE":
        raise _refuse(f"'{name}' is compressed as {comptype!r}, and crude reads "
                      f"uncompressed PCM only")
    bits = _BYTE_WIDTHS.get(sampwidth)
    if bits is None:
        raise _refuse(f"'{name}' is {sampwidth * 8}-bit; crude reads "
                      f"{PCM8}-bit and {PCM16}-bit PCM")
    if channels < 1:
        raise _refuse(f"'{name}' claims {channels} channel(s)")
    if not rate:
        raise _refuse(f"'{name}' has no sample rate to play it back at")
    return name, int(rate), int(channels), bits, int(frames)


def read_wav_header(path) -> WavData:
    """A file's shape, without its sound: a :class:`WavData` with no planes.

    A dialog that lists a folder wants a length and a rate, not the samples, and
    the difference between this and :func:`read_wav` is the difference between a
    header and a whole file.
    """
    name, rate, channels, bits, frames = _checked(path)
    return WavData(path=name, rate=rate, channels=channels, bits=bits,
                   frames=frames)


def read_wav(path) -> WavData:
    """A wav file as a :class:`WavData`; anything that is not PCM says so.

    The refusal messages name what was found (a sample width, a compression
    type, no file at all) because the caller is usually a person who picked the
    file in a dialog and deserves to know which of their assumptions was wrong.

    A file whose header promises more frames than its data chunk holds is read as
    the part that is really there: :attr:`WavData.frames` counts decoded samples,
    so the truncation shows up as a shorter sound rather than as a crash or as
    silence padded onto the end.
    """
    name, rate, channels, bits, frames = _checked(path)
    with wave.open(name, "rb") as handle:
        raw = handle.readframes(frames) if frames else b""

    planes = _deinterleave(_unpack(raw, bits, channels), channels)
    return WavData(
        path=name,
        rate=rate,
        channels=channels,
        bits=bits,
        frames=max((len(plane) for plane in planes), default=0),
        planes=planes,
    )


def _unpack(raw: bytes, bits: int, channels: int) -> List[float]:
    """Interleaved PCM bytes -> interleaved ``-1..+1`` floats."""
    if not raw:
        return []
    if bits == PCM8:
        # 8-bit PCM is *unsigned* in a wav: 128 is silence, 0 and 255 are the ends
        return [(value - 128) / 128.0 for value in raw]

    values = _array.array("h")
    values.frombytes(raw)
    # RIFF is little-endian; a big-endian machine has to turn the words over
    # before ``array`` can be read as samples.
    if sys.byteorder != "little":
        values.byteswap()
    trimmed = len(values) - (len(values) % max(channels, 1))
    return [values[i] / FULL_SCALE[PCM16] for i in range(trimmed)]


def _deinterleave(values: Sequence[float], channels: int) -> List[List[float]]:
    """One flat interleaved run -> one list per channel."""
    if channels <= 1:
        return [list(values)]
    planes: List[List[float]] = [[] for _ in range(channels)]
    for index, value in enumerate(values):
        planes[index % channels].append(value)
    return planes


# -- encode ---------------------------------------------------------------


def write_wav(path, samples: Sequence[float], rate: int, channels: int = 1,
              bits: int = PCM16) -> int:
    """Write frames of ``-1..+1`` floats as a PCM wav; answers frames written.

    ``samples`` is interleaved the way a device block is, so a script that built
    a stereo file writes ``[left, right, left, right, ...]`` and one that wants a
    mono file - the usual case for a sampler - leaves ``channels`` at 1. A value
    outside full scale is clamped rather than wrapped, because a wrapped sample
    is a click at every one of the crossings.
    """
    name = os.fspath(path)
    rate = int(rate)
    channels = max(int(channels), 1)
    if rate < 1:
        raise AudioError(f"a wav file needs a sample rate to be played back at, "
                         f"got {rate}")
    if bits not in WIDTHS:
        raise AudioError(f"a wav file must be written as "
                         f"{' or '.join(f'{width}-bit' for width in WIDTHS)} PCM, "
                         f"not {bits}")
    values = [float(value) for value in samples]
    if len(values) % channels:
        raise AudioError(
            f"{len(values)} sample(s) do not divide into {channels} channel(s); "
            f"write {len(values) // channels * channels} or widen the file"
        )

    folder = os.path.dirname(name)
    if folder:
        os.makedirs(folder, exist_ok=True)

    frames = len(values) // channels
    # The file is opened here rather than handed to ``wave`` as a name, because a
    # path the system refuses (a folder that cannot be written, a name with
    # characters this OS will not take) then comes back as one clean ``OSError``
    # instead of a half-built writer whose ``__del__`` adds a second, stranger
    # error to the report.
    with open(name, "wb") as raw:
        with wave.open(raw, "wb") as handle:
            handle.setnchannels(channels)
            handle.setsampwidth(bits // 8)
            handle.setframerate(rate)
            handle.writeframes(_pack(values, bits) if frames else b"")
    return frames


def _pack(values: Sequence[float], bits: int) -> bytes:
    """Interleaved floats -> interleaved PCM bytes, clamped and quantised."""
    scale = FULL_SCALE[bits]
    if bits == PCM8:
        return bytes(int(_quantise(value, scale)) + 128 for value in values)

    packed = _array.array("h", (int(_quantise(value, scale)) for value in values))
    if sys.byteorder != "little":
        packed.byteswap()
    return packed.tobytes()


def _quantise(value: float, scale: float) -> float:
    """One sample as the integer that file width holds, never past either end."""
    if value != value:  # a NaN would otherwise survive round() as a TypeError
        return 0.0
    return max(-scale, min(scale, round(value * scale)))


def plane_range(data: WavData, start: int = 0, count: int = 0,
                channel: int = 0) -> List[float]:
    """A slice of one plane, the way a clip view asks for what is on screen.

    Out-of-range ends are brought back rather than refused, because a marker can
    be dragged past the end of a file by a hand and the answer to that is the
    part of the sample that *is* there.
    """
    if not data.planes:
        return []
    plane = data.planes[min(max(int(channel), 0), len(data.planes) - 1)]
    low = min(max(int(start), 0), len(plane))
    if count <= 0:
        return plane[low:]
    return plane[low:low + int(count)]
