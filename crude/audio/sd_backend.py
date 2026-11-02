"""The default backend: ``sounddevice`` (PortAudio) as crude sees it.

This is the only module in the package that imports a device library, and it
imports it *lazily* - inside the methods that need it - so that importing
:mod:`crude.audio` never fails on a machine without PortAudio, and a test can
put a stub in ``sys.modules["sounddevice"]`` and drive the callback by hand.

The callback is the hot path, so it does four things and nothing else: tally the
status flags, copy samples into the reusable ctypes buffers libpd wants, run the
renderer (which is ``PdSession.process``, and therefore the only place the data
plane is entered), copy the result back. No allocation, no queue, no interpreter.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from .base import (
    AudioBackend,
    AudioConfig,
    AudioUnavailable,
    DeviceInfo,
    Renderer,
    check_direction,
    copy_samples,
    resolve_device,
)

MODULE = "sounddevice"


def module():
    """Import ``sounddevice``, or explain why it cannot be used here."""
    try:
        import sounddevice  # noqa: F401 - the import is the point, then bound
    except ImportError as exc:
        raise AudioUnavailable(f"sounddevice is not installed: {exc}")
    except OSError as exc:
        # PortAudio itself missing: the import gets past the Python package and
        # fails on the shared library, which is an OSError, not an ImportError.
        raise AudioUnavailable(f"sounddevice has no PortAudio to talk to: {exc}")
    import sys

    return sys.modules[MODULE]


def _as_records(listed) -> List[dict]:
    """``query_devices()`` answers a list of dicts, or one dict for an index."""
    if isinstance(listed, dict):
        return [listed]
    records = []
    for entry in listed:
        if isinstance(entry, dict):
            records.append(entry)
    return records


def query_devices(direction: str = None) -> List[DeviceInfo]:
    """Every device PortAudio reports, optionally filtered by direction.

    The library's ``kind`` argument means "the *default* device of this kind", so
    it is of no use for a list: everything is read and then filtered on channel
    counts, which is also how ``duplex`` becomes answerable.
    """
    if direction:
        check_direction(direction)
    sd = module()
    try:
        listed = sd.query_devices()
    except Exception as exc:  # sounddevice raises ValueError/RuntimeError here
        raise AudioUnavailable(f"sounddevice could not list devices: {exc}")

    devices = [DeviceInfo.from_mapping(entry, source=MODULE) for entry in _as_records(listed)]
    if direction:
        devices = [device for device in devices if device.supports(direction)]
    return devices


class SoundDeviceBackend(AudioBackend):
    """A float32 stream driven by PortAudio's callback thread.

    PortAudio has three stream shapes and ``sounddevice`` has one class per
    shape: a *duplex* ``Stream`` cannot have a zero-channel side (the library
    builds a parameter block per direction, and PortAudio rejects a direction
    with no channels), so an output-only or input-only configuration is opened
    as ``OutputStream``/``InputStream`` - which also means a different callback
    signature per shape, and the adapter picks all three together here.
    """

    name = "sounddevice"
    live = True

    def __init__(self, config: AudioConfig = None, render: Renderer = None,
                 log: Callable[[str], None] = None, scope_frames: int = 1024,
                 device: Tuple[Optional[int], Optional[int]] = None):
        super().__init__(config, render, log=log, scope_frames=scope_frames)
        #: ``(input index, output index)`` decided once, at open time
        self._device = device
        self._stream = None
        #: ``duplex``/``output``/``input`` - the PortAudio shape, set at open
        self.kind = "duplex"

    # -- capability -----------------------------------------------------
    @classmethod
    def available(cls) -> bool:
        try:
            module()
        except AudioUnavailable:
            return False
        return True

    @classmethod
    def devices(cls) -> List[DeviceInfo]:
        return query_devices()

    # -- device selection ------------------------------------------------
    def _resolve(self, wanted, direction: str) -> Optional[int]:
        return resolve_device(wanted, direction, self.devices)

    def _select(self) -> Tuple[Optional[int], Optional[int]]:
        if self._device is not None:
            return self._device
        return (
            self._resolve(self.config.input_device, "input"),
            self._resolve(self.config.output_device, "output"),
        )

    # -- lifecycle ------------------------------------------------------
    def _plan(self) -> Tuple[str, object, object]:
        """``(kind, device argument, channels argument)``, per PortAudio shape."""
        config = self.config
        selected = self._select()
        if config.in_channels and config.out_channels:
            return "duplex", selected, (config.in_channels, config.out_channels)
        if config.out_channels:
            return "output", selected[1], config.out_channels
        return "input", selected[0], config.in_channels

    def _open(self) -> None:
        sd = module()
        config = self.config
        kind, device, channels = self._plan()
        maker = {"duplex": sd.Stream, "output": sd.OutputStream,
                 "input": sd.InputStream}[kind]
        callback = {"duplex": self.callback, "output": self.output_callback,
                    "input": self.input_callback}[kind]
        self.kind = kind

        try:
            self._stream = maker(
                device=device,
                samplerate=config.sample_rate,
                blocksize=config.block_size,
                channels=channels,
                dtype="float32",
                callback=callback,
                finished_callback=self._finished,
            )
        except AudioUnavailable:
            raise
        except Exception as exc:  # ValueError, RuntimeError, PortAudio errors
            self._stream = None
            raise AudioUnavailable(
                f"sounddevice could not open {config.describe()}: {exc}"
            )

        self.device = self._device_name(_index_of(kind, device))

    def _device_name(self, index: Optional[int]) -> str:
        """What ``AUDIO_INFO`` calls the stream: a device name, never a guess."""
        if index is None:
            return self._default_device_name()
        try:
            records = _as_records(module().query_devices())
        except Exception:  # cosmetic: a device that cannot be described is open
            return f"device {index}"
        for entry in records:
            info = DeviceInfo.from_mapping(entry, source=MODULE)
            if info.index == index:
                return info.name
        return f"device {index}"

    def _default_device_name(self) -> str:
        kind = "output" if self.config.out_channels else "input"
        try:
            records = _as_records(module().query_devices(kind=kind))
        except Exception:
            return f"{self.name} (default)"
        if len(records) == 1:
            name = DeviceInfo.from_mapping(records[0], source=MODULE).name
            return f"{name} (default)"
        return f"{self.name} (default)"

    def _finished(self) -> None:
        # PortAudio's "stream stopped" hook: nothing to do, but it must exist so
        # closing the stream from another thread cannot deadlock on a missing
        # callback.
        return None

    def _start(self) -> None:
        try:
            self._stream.start()
        except AudioUnavailable:
            raise
        except Exception as exc:
            raise AudioUnavailable(f"sounddevice could not start the stream: {exc}")

    def _stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.close()

    # -- the callback ---------------------------------------------------
    def callback(self, indata, outdata, frames, time_info, status) -> None:
        """The duplex callback: ``(indata, outdata, frames, time, status)``."""
        self._block(indata, outdata, frames, status)

    def output_callback(self, outdata, frames, time_info, status) -> None:
        """An output-only stream is called with ``(outdata, frames, time, status)``."""
        self._block(None, outdata, frames, status)

    def input_callback(self, indata, frames, time_info, status) -> None:
        """An input-only stream is called with ``(indata, frames, time, status)``."""
        self._block(indata, None, frames, status)

    def _block(self, indata, outdata, frames, status) -> None:
        """The one hot path, whatever shape the stream turned out to have."""
        if status:
            self.counts_status(status)

        ticks = self.config.ticks_for(frames)
        in_buffer, out_buffer = self.buffers_for(ticks)

        if indata is not None and self.config.in_channels:
            copy_samples(in_buffer, indata)

        self.consume(in_buffer, out_buffer, ticks)
        if outdata is not None:
            copy_samples(outdata, out_buffer)

    def describe(self) -> str:
        return (
            f"audio sounddevice ({self.state()}) "
            f"on '{self.device or self.name}': {self.config.describe()}"
        )


def _index_of(kind: str, device) -> Optional[int]:
    """The one device index a stream of this ``kind`` was actually given."""
    if kind == "duplex":
        # a duplex pair is named by whatever side it really is on
        return device[1] if device[1] is not None else device[0]
    return device
