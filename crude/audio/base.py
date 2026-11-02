"""The audio layer's shared vocabulary: config, devices, metering, backend protocol.

Nothing in this module imports ``sounddevice`` or ``pyaudio`` - the adapters that
do live next door and are only touched when a device is actually wanted. That is
what keeps the whole audio surface testable without hardware, which is the point
of :class:`crude.audio.null.NullBackend`.

Two rules from the plan shape everything below:

* **The callback must never block** (risk 6): it renders, meters and counts, and
  hands anything else to a queue. Metering therefore uses a lock that is
  essentially never contended, and no allocation happens per block.
* **The audio thread owns the data plane** (threading table): the callback is
  the only caller of ``libpd_process_float``, and it reaches libpd through the
  ``render`` callable the session supplies - the audio layer never imports the
  Pd engine.
"""

from __future__ import annotations

import array as _array_module
import ctypes
import math
from collections import deque
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Sequence

#: Pd itself is fixed at 64 frames per tick, so a "block" is 64 frames unless a
#: manifest says otherwise.
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_BLOCK_SIZE = 64
#: Anything quieter than this is reported as silence instead of ``-inf``.
DB_FLOOR = -120.0

#: PortAudio's callback status bits. ``sounddevice`` exposes them as an ``IntFlag``
#: and PyAudio as a plain int, but the values are identical, so one decoder
#: covers both.
INPUT_UNDERFLOW = 1 << 0
INPUT_OVERFLOW = 1 << 1
OUTPUT_UNDERFLOW = 1 << 2
OUTPUT_OVERFLOW = 1 << 3
STATUS_BITS = (
    (INPUT_UNDERFLOW, "input underflow"),
    (INPUT_OVERFLOW, "input overflow"),
    (OUTPUT_UNDERFLOW, "output underflow"),
    (OUTPUT_OVERFLOW, "output overflow"),
)

#: The three things a caller can ask a device list about.
DIRECTIONS = ("input", "output", "duplex")

#: Properties a script may ``GET`` from a backend or from ``AUDIO_INFO``.
INFO_PROPS = (
    "backend",
    "running",
    "paused",
    "opened",
    "sample_rate",
    "block_size",
    "in_channels",
    "out_channels",
    "blocks",
    "frames",
    "xruns",
    "dropped",
    "rms",
    "peak",
    "level",
    "db",
    "live",
    "device",
)


class AudioError(RuntimeError):
    """A device or configuration problem the bindings turn into a script error."""


class AudioUnavailable(AudioError):
    """This backend cannot be used here: no module, no device, no permission.

    The fallback chain treats it as "try the next one"; only ``strict`` requests
    (a manifest that names a backend and does not mark it optional) turn it into
    a failure.
    """


@dataclass
class AudioConfig:
    """What the stream should look like: rates, block size, channel counts."""

    sample_rate: int = DEFAULT_SAMPLE_RATE
    block_size: int = DEFAULT_BLOCK_SIZE
    in_channels: int = 2
    out_channels: int = 2
    input_device: Any = None
    output_device: Any = None

    def __post_init__(self) -> None:
        # Coerce first, then validate: a manifest hands over strings, and
        # "44100" is as acceptable as 44100 while "studio" is not.
        for name in ("sample_rate", "block_size", "in_channels", "out_channels"):
            value = getattr(self, name)
            try:
                coerced = int(value or 0) if name.endswith("channels") else int(value)
            except (TypeError, ValueError):
                raise AudioError(f"'{value}' is not a number, as {name} must be")
            setattr(self, name, max(coerced, 0) if name.endswith("channels") else coerced)

        if self.sample_rate < 1:
            raise AudioError(f"a sample rate must be positive, got {self.sample_rate}")
        if self.block_size < 1:
            raise AudioError(f"a block size must be at least 1 frame, got {self.block_size}")
        if self.in_channels + self.out_channels == 0:
            raise AudioError("an audio stream needs at least one input or one output channel")

    # -- geometry -------------------------------------------------------
    @property
    def out_frame_size(self) -> int:
        """Samples per block on the output side (interleaved)."""
        return self.block_size * max(self.out_channels, 1)

    @property
    def in_frame_size(self) -> int:
        return self.block_size * max(self.in_channels, 1)

    def ticks_for(self, frames: int) -> int:
        """How many Pd ticks ``frames`` is: one block = one tick, normally."""
        return max(int(frames) // self.block_size, 1)

    def changed(self, **kwargs) -> "AudioConfig":
        return replace(self, **kwargs)

    def describe(self) -> str:
        return (
            f"{self.sample_rate} Hz, {self.block_size}-frame blocks, "
            f"{self.in_channels} in / {self.out_channels} out"
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sample_rate": self.sample_rate,
            "block_size": self.block_size,
            "in_channels": self.in_channels,
            "out_channels": self.out_channels,
            "input_device": self.input_device,
            "output_device": self.output_device,
        }

    def __str__(self):
        return self.describe()


@dataclass(frozen=True)
class DeviceInfo:
    """One input/output the host reports, in the shape the script wants to see."""

    name: str
    index: Optional[int] = None
    max_input_channels: int = 0
    max_output_channels: int = 0
    default_sample_rate: int = DEFAULT_SAMPLE_RATE
    host_api: str = ""

    @classmethod
    def from_mapping(cls, data: Dict[str, Any], source: str = "") -> "DeviceInfo":
        """Adapt a ``sounddevice``/``pyaudio`` device dictionary.

        The two libraries spell the same thing differently, so the mapping is
        tried under every alias instead of failing on the first miss.
        """
        def number(*keys, default=0) -> int:
            for key in keys:
                value = data.get(key)
                if value is not None:
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        pass
            return default

        def text(*keys, default="") -> str:
            for key in keys:
                value = data.get(key)
                if value is not None:
                    return str(value)
            return default

        rate = number("default_samplerate", "defaultSampleRate", "samplerate",
                      default=DEFAULT_SAMPLE_RATE)
        raw_index = data.get("index", data.get("device_index", data.get("index_")))
        try:
            index = None if raw_index is None else int(raw_index)
        except (TypeError, ValueError):
            index = None
        return cls(
            name=text("name", default="unnamed device"),
            index=index,
            max_input_channels=number("max_input_channels", "maxInputChannels"),
            max_output_channels=number("max_output_channels", "maxOutputChannels"),
            default_sample_rate=rate or DEFAULT_SAMPLE_RATE,
            host_api=text("hostapi", "hostApi", "host_api", default=source),
        )

    # -- capabilities ---------------------------------------------------
    @property
    def is_input(self) -> bool:
        return self.max_input_channels > 0

    @property
    def is_output(self) -> bool:
        return self.max_output_channels > 0

    @property
    def is_duplex(self) -> bool:
        return self.is_input and self.is_output

    def supports(self, direction: str) -> bool:
        if direction == "input":
            return self.is_input
        if direction == "output":
            return self.is_output
        if direction == "duplex":
            return self.is_duplex
        check_direction(direction)
        return False  # pragma: no cover - check_direction always raises

    def describe(self) -> str:
        where = "in/out" if self.is_duplex else ("in" if self.is_input else "out")
        rate = f"{self.default_sample_rate} Hz" if self.default_sample_rate else "-"
        label = f"[{self.index}] " if self.index is not None else ""
        return (
            f"{label}{self.name} ({where} {max(self.max_input_channels, self.max_output_channels)}, "
            f"{rate}{', ' + self.host_api if self.host_api else ''})"
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "index": self.index,
            "max_input_channels": self.max_input_channels,
            "max_output_channels": self.max_output_channels,
            "default_sample_rate": self.default_sample_rate,
            "host_api": self.host_api,
            "input": self.is_input,
            "output": self.is_output,
        }

    def __str__(self):
        return self.describe()


def check_direction(direction: str) -> str:
    """Validate a direction word before a library is asked about it."""
    if direction not in DIRECTIONS:
        raise AudioError(
            f"a device direction must be one of {', '.join(DIRECTIONS)}, "
            f"got {direction!r}"
        )
    return direction


def resolve_device(wanted, direction: str, devices) -> Optional[int]:
    """``None``/index/name/DeviceInfo -> a host device index or ``None``.

    Both libraries identify a device by an integer index, while a manifest and a
    script would rather say ``"Studio Capture"``, so the spelling is decided in
    one place: an unset selection means "the library's default", a number is
    taken as written, and a name has to match exactly one device that can do
    ``direction``.
    """
    if wanted is None:
        return None
    if isinstance(wanted, DeviceInfo):
        if wanted.index is None:
            raise AudioUnavailable(f"device '{wanted.name}' has no index to open")
        return int(wanted.index)
    if isinstance(wanted, bool):  # a bool is an int in Python, and never meant here
        raise AudioUnavailable(f"an audio {direction} device cannot be True/False")
    if isinstance(wanted, int):
        return int(wanted)
    if isinstance(wanted, str):
        wanted = wanted.strip()
        if not wanted:
            return None
        try:
            return int(wanted)
        except ValueError:
            pass
        listed = list(devices())
        matches = [device for device in listed
                   if device.supports(direction) and device.name == wanted]
        if not matches:
            matches = [device for device in listed
                       if device.supports(direction)
                       and wanted.lower() in device.name.lower()]
        if not matches:
            raise AudioUnavailable(
                f"no {direction} device called '{wanted}' "
                f"({len(listed)} device(s) were listed)"
            )
        if len(matches) > 1:
            raise AudioUnavailable(
                f"'{wanted}' matches several {direction} devices: "
                + ", ".join(f"{device.index}:{device.name}" for device in matches)
            )
        return matches[0].index
    raise AudioUnavailable(f"cannot open an audio {direction} device named {wanted!r}")


# -- sample plumbing ----------------------------------------------------
# The adapters receive whatever their library hands out (a ctypes array, a numpy
# array, a ``bytes`` block) and libpd wants a ``c_float`` array, so the two
# conversions below are the only place that difference is allowed to show.


def read_samples(data, count: int = None) -> List[float]:
    """Any per-block container -> a flat list of floats."""
    if data is None:
        return []
    if isinstance(data, (bytes, bytearray)):
        values = list(bytes_to_samples(data))
    elif hasattr(data, "flat"):  # numpy arrays are 2-D (frames, channels)
        values = list(data.flat)
    elif hasattr(data, "__getitem__"):
        values = [data[i] for i in range(len(data))]
    else:  # pragma: no cover - defensive
        values = list(data)
    if count is not None:
        values = values[:count]
    return [float(value) for value in values]


def bytes_to_samples(data: bytes) -> List[float]:
    """A ``bytes`` block of float32 samples -> a list of floats (PyAudio's shape)."""
    buffer = _array_module.array("f")
    buffer.frombytes(bytes(data))
    return list(buffer)


def samples_to_bytes(values: Sequence[float]) -> bytes:
    """Flat float samples -> the ``bytes`` PyAudio expects back (native endian)."""
    return _array_module.array("f", [float(value) for value in values]).tobytes()


def copy_samples(dest, source) -> int:
    """Container to container, with no intermediate list.

    A device callback walks both containers directly - ``.flat`` for numpy's
    ``(frames, channels)`` shape, indexes otherwise - and stops at whichever
    runs out first, so a short block never writes past the end of a buffer.
    """
    if dest is None or source is None:
        return 0
    items = getattr(source, "flat", None)
    if items is None:
        items = source
    writer = getattr(dest, "flat", None)
    if writer is None:
        writer = dest
    copied = 0
    for index, value in enumerate(items):
        try:
            writer[index] = value
        except (IndexError, TypeError):
            break
        copied = index + 1
    return copied


def status_text(status) -> str:
    """The PortAudio callback flags as readable words (``""`` when nothing)."""
    if not status:
        return ""
    try:
        value = int(status)
    except (TypeError, ValueError):  # pragma: no cover - a stub may pass text
        return str(status).lower()
    return " ".join(word for bit, word in STATUS_BITS if value & bit) or str(value)


# -- metering -----------------------------------------------------------


class Taps:
    """RMS, decaying peak and a scope ring buffer, fed from the audio callback.

    One instance per direction. Everything the script reads is a *snapshot* of
    the last block that arrived, so a headless run and a live run answer the
    same question the same way.
    """

    def __init__(self, channels: int = 2, scope_frames: int = 1024,
                 peak_decay: float = 1.5):
        self.channels = max(int(channels), 1)
        self.peak_decay = float(peak_decay)
        self._lock = RLock()
        self._rms = [0.0] * self.channels
        self._peak = [0.0] * self.channels
        self._scope: deque = deque(maxlen=max(int(scope_frames), 1))
        self.blocks = 0
        self.clipped = 0

    # -- feed -----------------------------------------------------------
    def push(self, samples: Sequence[float], channels: int = None) -> None:
        """Meter one interleaved block."""
        channels = max(int(channels or self.channels), 1)
        if not len(samples):
            return
        with self._lock:
            self.blocks += 1
            per_channel = len(samples) // channels
            for channel in range(min(channels, self.channels)):
                total = 0.0
                highest = 0.0
                for frame in range(per_channel):
                    value = float(samples[frame * channels + channel])
                    total += value * value
                    magnitude = abs(value)
                    if magnitude > 0.999:
                        self.clipped += 1
                    if magnitude > highest:
                        highest = magnitude
                self._rms[channel] = math.sqrt(total / per_channel) if per_channel else 0.0
                # A peak-hold: it rises at once and only falls when the runtime
                # ages it, so a meter widget behaves identically with or without
                # a device.
                self._peak[channel] = max(self._peak[channel], highest)
            # The scope shows the mix, which is what a waveform widget draws.
            self._scope.extend(self._mix(samples, channels))

    @staticmethod
    def _mix(samples: Sequence[float], channels: int) -> List[float]:
        if channels == 1:
            return [float(value) for value in samples]
        frames = len(samples) // channels
        return [
            sum(float(samples[frame * channels + channel]) for channel in range(channels))
            / channels
            for frame in range(frames)
        ]

    # -- read -----------------------------------------------------------
    def _channel(self, channel: Optional[int]) -> List[int]:
        if channel is None:
            return list(range(self.channels))
        if not 0 <= int(channel) < self.channels:
            raise AudioError(
                f"audio channel {channel} is out of range "
                f"(this stream has {self.channels})"
            )
        return [int(channel)]

    def rms(self, channel: int = None) -> float:
        with self._lock:
            values = [self._rms[index] for index in self._channel(channel)]
        return math.sqrt(sum(value * value for value in values) / len(values))

    def peak(self, channel: int = None) -> float:
        with self._lock:
            values = [self._peak[index] for index in self._channel(channel)]
        return max(values) if values else 0.0

    def db(self, channel: int = None) -> float:
        level = self.rms(channel)
        if level <= 0.0:
            return DB_FLOOR
        return max(20.0 * math.log10(level), DB_FLOOR)

    def scope(self, frames: int = None, channel: int = None) -> List[float]:
        """The most recent ``frames`` of the mix, oldest first."""
        with self._lock:
            values = list(self._scope)
        if channel is not None:
            # The scope holds the mix, so a channel request just validates.
            self._channel(channel)
        if frames is None:
            return values
        frames = max(int(frames), 0)
        return values[-frames:] if frames else []

    @property
    def scope_capacity(self) -> int:
        """How many frames of history a widget can ask for at most."""
        return self._scope.maxlen

    def decay(self, seconds: float) -> None:
        """Age the peak hold; the runtime loop calls this once per frame."""
        step = self.peak_decay * max(float(seconds), 0.0)
        with self._lock:
            for index in range(self.channels):
                self._peak[index] = max(self._peak[index] - step, 0.0)

    def reset(self) -> None:
        with self._lock:
            self._rms = [0.0] * self.channels
            self._peak = [0.0] * self.channels
            self._scope.clear()
            self.blocks = 0
            self.clipped = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rms": [round(value, 6) for value in self._rms],
            "peak": [round(value, 6) for value in self._peak],
            "blocks": self.blocks,
            "clipped": self.clipped,
        }

    def __repr__(self):
        return f"<Taps channels={self.channels} blocks={self.blocks}>"


# -- the backend protocol -----------------------------------------------


Renderer = Callable[[int, Any, Any], None]


class AudioBackend:
    """One audio stream: open it, run it, and read what it did.

    Subclasses implement the device-specific half (``_open``, ``_start``,
    ``_stop``, ``_close``) and call :meth:`consume` from wherever their samples
    arrive - the PortAudio callback, or a test that pulls blocks on purpose.
    """

    #: how the script names this backend in ``AUDIO_INFO``/the fallback chain
    name = "base"
    #: ``False`` for pull-based streams: nothing is running by itself
    live = True
    #: a backend whose absence is never an error (the last resort)
    optional = False

    def __init__(self, config: AudioConfig = None, render: Renderer = None,
                 log: Callable[[str], None] = None, scope_frames: int = 1024,
                 taps: Taps = None):
        self.config = config or AudioConfig()
        self.render = render
        self.log = log or (lambda message: None)
        self.taps = taps if taps is not None else Taps(self.config.out_channels, scope_frames)
        self.input_taps = Taps(max(self.config.in_channels, 1), scope_frames)

        self.opened = False
        self.running = False
        self.paused = False
        #: the device or stream name reported by the library, for ``AUDIO_INFO``
        self.device = ""
        self.notes: List[str] = []

        # -- counters, all monotonic and all readable through GET
        self.blocks = 0
        self.frames = 0
        self.silent_blocks = 0
        self.xruns_input = 0
        self.xruns_output = 0
        self.dropped = 0
        self.starts = 0
        self.stops = 0
        #: the words behind every status flag that arrived, oldest first
        self.status_flags: List[str] = []

        self._in_buffer = None
        self._out_buffer = None
        #: per-tick ctypes buffers, so a callback never allocates (see ``buffers_for``)
        self._pool: Dict[int, tuple] = {}

    # -- capability / discovery -----------------------------------------
    @classmethod
    def available(cls) -> bool:
        """Can this backend even be tried on this machine?"""
        return True

    @classmethod
    def devices(cls) -> List[DeviceInfo]:
        return []

    @classmethod
    def default_device(cls, direction: str = "output") -> Optional[DeviceInfo]:
        for device in cls.devices():
            if device.supports(direction):
                return device
        return None

    # -- lifecycle ------------------------------------------------------
    def open(self) -> "AudioBackend":
        """Allocate the stream; idempotent, so ``start()`` can call it."""
        if not self.opened:
            self._open()
            self.opened = True
        self._allocate()
        return self

    def start(self) -> "AudioBackend":
        if not self.opened:
            self.open()
        if not self.running:
            self._start()
            self.running = True
            self.starts += 1
        return self

    def stop(self) -> "AudioBackend":
        if self.running:
            self._stop()
            self.running = False
            self.stops += 1
        return self

    def close(self) -> "AudioBackend":
        if self.opened:
            self.stop()
            self._close()
            self.opened = False
        self._in_buffer = self._out_buffer = None
        self._pool.clear()
        return self

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc_info):
        self.close()
        return False

    # -- pausing (the control plane needs the stream to keep out) --------
    def pause(self) -> None:
        """Stop handing samples to libpd; the device keeps running on silence."""
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def pause_switch(self):
        """``fn(paused: bool)`` - what :meth:`crude.pd.PdSession.set_pauser` wants."""
        def switch(paused: bool) -> None:
            self.pause() if paused else self.resume()

        return switch

    # -- the shared block body ------------------------------------------
    def allocate(self, ticks: int = 1):
        """``(in, out)`` ``c_float`` arrays big enough for ``ticks`` blocks."""
        in_size = self.config.in_frame_size * ticks
        out_size = self.config.out_frame_size * ticks
        return ((ctypes.c_float * max(in_size, 1))(),
                (ctypes.c_float * max(out_size, 1))())

    def _allocate(self) -> None:
        if self._out_buffer is None:
            self._in_buffer, self._out_buffer = self.allocate()

    def buffers_for(self, ticks: int = 1):
        """A reused ``(in, out)`` pair per tick count: no allocation per callback.

        A device callback must not malloc (risk 6), so the ctypes arrays a block
        needs are made once and handed back every time.
        """
        ticks = max(int(ticks), 1)
        cached = self._pool.get(ticks)
        if cached is None:
            cached = self._pool[ticks] = self.allocate(ticks)
        return cached

    def consume(self, in_buffer, out_buffer, ticks: int = 1) -> int:
        """Run one block through the renderer, then meter and count it.

        Paused, or with nothing to render, the output is zeroed and the block is
        tallied as silent: a paused stream must never reach libpd, because the
        control plane may be changing patches underneath it.
        """
        size = len(out_buffer) if out_buffer is not None else 0
        if self.paused or self.render is None:
            for index in range(size):
                out_buffer[index] = 0.0
            self.silent_blocks += 1
            return 0

        self.render(ticks, in_buffer, out_buffer)
        self.blocks += 1
        self.frames += self.config.block_size * ticks
        if size:
            self.taps.push(out_buffer, self.config.out_channels)
        if in_buffer is not None and self.config.in_channels:
            self.input_taps.push(in_buffer, self.config.in_channels)
        return size

    def process_block(self, ticks: int = 1) -> int:
        """Render ``ticks`` blocks now, on whoever's thread is asking.

        A pull-based backend (:attr:`live` is ``False``) has no callback to wait
        for, so this *is* its audio thread; the runtime calls it when the script
        says time should pass. A live backend gets called by the device instead.
        """
        if not self.opened:
            raise AudioError(
                f"{self.name} is not open; call AUDIO_START before rendering a block"
            )
        in_buffer, out_buffer = self.buffers_for(ticks)
        return self.consume(in_buffer, out_buffer, ticks)

    def counts_status(self, status) -> None:
        """Tally a PortAudio-style status flag: both libraries hand the same bits.

        An input overflow means samples were lost, so it counts as a drop; an
        output underflow means the callback was too slow to fill the device.
        """
        if not status:
            return
        try:
            value = int(status)
        except (TypeError, ValueError):  # pragma: no cover - a stub may pass text
            text = str(status).lower()
            value = sum(bit for bit, word in STATUS_BITS if word in text)
        if value & (INPUT_UNDERFLOW | INPUT_OVERFLOW):
            self.xruns_input += 1
        if value & (OUTPUT_UNDERFLOW | OUTPUT_OVERFLOW):
            self.xruns_output += 1
        if value & (INPUT_OVERFLOW | OUTPUT_UNDERFLOW):
            self.dropped += 1
        noted = status_text(value)
        if noted:
            self.status_flags.append(noted)
            self.log(f"audio {self.name}: {noted}")

    # -- device hooks ---------------------------------------------------
    def _open(self) -> None:  # pragma: no cover - overridden everywhere
        raise AudioError(f"{self.name} cannot be opened by the base class")

    def _start(self) -> None:  # pragma: no cover
        raise AudioError(f"{self.name} cannot be started by the base class")

    def _stop(self) -> None:  # pragma: no cover
        raise AudioError(f"{self.name} cannot be stopped by the base class")

    def _close(self) -> None:  # pragma: no cover
        raise AudioError(f"{self.name} cannot be closed by the base class")

    # -- reporting ------------------------------------------------------
    @property
    def xruns(self) -> int:
        return self.xruns_input + self.xruns_output

    def state(self) -> str:
        """``running``/``open``/``closed`` - the three states a stream can be in."""
        if self.running:
            return "running"
        return "open" if self.opened else "closed"

    @property
    def level(self) -> float:
        """Output RMS of the last block, across every channel."""
        return self.taps.rms()

    @property
    def peak(self) -> float:
        return self.taps.peak()

    def info(self) -> Dict[str, Any]:
        return {
            "backend": self.name,
            "live": self.live,
            "opened": self.opened,
            "running": self.running,
            "paused": self.paused,
            "device": self.device or self.name,
            "sample_rate": self.config.sample_rate,
            "block_size": self.config.block_size,
            "in_channels": self.config.in_channels,
            "out_channels": self.config.out_channels,
            "blocks": self.blocks,
            "frames": self.frames,
            "silent_blocks": self.silent_blocks,
            "xruns": self.xruns,
            "xruns_input": self.xruns_input,
            "xruns_output": self.xruns_output,
            "dropped": self.dropped,
            "starts": self.starts,
            "stops": self.stops,
            "rms": self.taps.rms(),
            "level": self.taps.rms(),
            "peak": self.taps.peak(),
            "db": self.taps.db(),
            "clipped": self.taps.clipped,
        }

    # -- script properties (GET / SET) ----------------------------------
    def get_prop(self, name: str):
        if name in INFO_PROPS:
            value = self.info()[name]
            return 1 if value is True else (0 if value is False else value)
        if name == "seconds":
            return self.frames / float(self.config.sample_rate)
        if name == "info":
            return dict(self.info())
        if name == "notes":
            return list(self.notes)
        raise KeyError(name)

    def set_prop(self, name: str, value) -> None:
        # Deliberately read-only: a stream that changed shape mid-run would
        # disagree with the block size libpd and the taps were built for.
        raise KeyError(name)

    def describe(self) -> str:
        return (
            f"audio {self.name} ({self.state()}): {self.config.describe()} "
            f"[{self.blocks} blocks, {self.xruns} xrun(s)]"
        )

    def __str__(self):
        return self.describe()

    def __repr__(self):
        return f"<{self.name} running={self.running} blocks={self.blocks}>"


@dataclass
class RecordingRender:
    """A render callable that records what it was asked for (tests, ``--no-audio``).

    Kept here rather than in the tests because the fallback chain and the CLI
    both need something that behaves like a Pd session without one.
    """

    value: float = 0.0
    calls: List[int] = field(default_factory=list)

    def __call__(self, ticks: int, in_buffer, out_buffer) -> None:
        self.calls.append(ticks)
        for index in range(len(out_buffer)):
            out_buffer[index] = self.value
