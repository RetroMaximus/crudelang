"""The fallback backend: ``pyaudio``, for machines where sounddevice cannot work.

``pyaudio`` is an *optional* dependency (and has no wheel for every Python), so
this module imports it lazily exactly like :mod:`crude.audio.sd_backend` imports
``sounddevice`` - a stub in ``sys.modules["pyaudio"]`` is enough to exercise the
whole adapter.

The one difference that leaks into the API is the shape of the samples: PortAudio
hands PyAudio ``bytes`` and wants ``bytes`` back, so every block is packed and
unpacked here (``bytes_to_samples`` / ``samples_to_bytes``) and nowhere else.
PyAudio also opens input and output as a single stream, so a duplex pair with
different channel counts is refused and the caller falls through to sounddevice.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from .base import (
    AudioBackend,
    AudioConfig,
    AudioUnavailable,
    DeviceInfo,
    Renderer,
    bytes_to_samples,
    check_direction,
    copy_samples,
    resolve_device,
    samples_to_bytes,
)

MODULE = "pyaudio"


def module():
    """Import ``pyaudio``, or explain why it cannot be used here."""
    try:
        import pyaudio  # noqa: F401 - the import is the point, then bound
    except ImportError as exc:
        raise AudioUnavailable(f"pyaudio is not installed: {exc}")
    except OSError as exc:
        raise AudioUnavailable(f"pyaudio has no PortAudio to talk to: {exc}")
    import sys

    return sys.modules[MODULE]


def query_devices(direction: str = None) -> List[DeviceInfo]:
    """Every device PyAudio enumerates, optionally filtered by direction."""
    if direction:
        check_direction(direction)
    pa = module()
    try:
        instance = pa.PyAudio()
    except Exception as exc:
        raise AudioUnavailable(f"pyaudio could not initialise PortAudio: {exc}")
    devices: List[DeviceInfo] = []
    try:
        for index in range(instance.get_device_count()):
            entry = instance.get_device_info_by_index(index)
            if not isinstance(entry, dict):
                continue
            devices.append(DeviceInfo.from_mapping(entry, source=MODULE))
    except Exception as exc:
        raise AudioUnavailable(f"pyaudio could not list devices: {exc}")
    finally:
        instance.terminate()

    if direction:
        devices = [device for device in devices if device.supports(direction)]
    return devices


class PyAudioBackend(AudioBackend):
    """A float32 duplex stream driven by PyAudio's callback thread."""

    name = "pyaudio"
    live = True
    #: an optional dependency: not having it is never a failure
    optional = True

    def __init__(self, config: AudioConfig = None, render: Renderer = None,
                 log: Callable[[str], None] = None, scope_frames: int = 1024,
                 device: Tuple[Optional[int], Optional[int]] = None):
        super().__init__(config, render, log=log, scope_frames=scope_frames)
        self._device = device
        self._pa = None
        self._stream = None

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
    def _select(self) -> Tuple[Optional[int], Optional[int]]:
        if self._device is not None:
            return self._device
        return (
            resolve_device(self.config.input_device, "input", self.devices),
            resolve_device(self.config.output_device, "output", self.devices),
        )

    # -- lifecycle ------------------------------------------------------
    def _open(self) -> None:
        pa = module()
        config = self.config

        if config.in_channels and config.out_channels and \
                config.in_channels != config.out_channels:
            raise AudioUnavailable(
                "pyaudio duplex needs matching input and output channel counts; "
                "use sounddevice for split I/O"
            )

        self._pa = pa.PyAudio()
        selected = self._select()
        kwargs = {
            "format": pa.paFloat32,
            "channels": max(config.in_channels, config.out_channels),
            "rate": float(config.sample_rate),
            "frames_per_buffer": config.block_size,
            "input": bool(config.in_channels),
            "output": bool(config.out_channels),
            "stream_callback": self.callback,
        }
        if selected[0] is not None:
            kwargs["input_device_index"] = selected[0]
        if selected[1] is not None:
            kwargs["output_device_index"] = selected[1]

        try:
            self._stream = self._pa.open(**kwargs)
        except AudioUnavailable:
            raise
        except Exception as exc:  # IOError/OSError, ValueError
            self._close_instance()
            raise AudioUnavailable(
                f"pyaudio could not open {config.describe()}: {exc}"
            )

        self.device = self._device_name(selected, int(kwargs["channels"]))

    def _device_name(self, selected: Tuple[Optional[int], Optional[int]],
                     channels: int) -> str:
        index = selected[1] if selected[1] is not None else selected[0]
        if index is None:
            return f"{self.name} default ({channels} ch)"
        try:
            listed = self.devices()
        except AudioUnavailable:  # a device that cannot be described is still open
            return f"device {index}"
        for device in listed:
            if device.index == index:
                return device.name
        return f"device {index}"

    def _close_instance(self) -> None:
        instance, self._pa = self._pa, None
        if instance is not None:
            instance.terminate()

    def _start(self) -> None:
        try:
            self._stream.start_stream()
        except AudioUnavailable:
            raise
        except Exception as exc:
            raise AudioUnavailable(f"pyaudio could not start the stream: {exc}")

    def _stop(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.close()
        self._close_instance()

    # -- the callback ---------------------------------------------------
    def callback(self, in_data, frame_count, time_info, status):
        """The PyAudio callback: unpack, render, repack, keep running."""
        if status:
            self.counts_status(status)

        ticks = self.config.ticks_for(frame_count)
        in_buffer, out_buffer = self.buffers_for(ticks)

        if self.config.in_channels and in_data:
            copy_samples(in_buffer, bytes_to_samples(in_data))

        self.consume(in_buffer, out_buffer, ticks)
        return (samples_to_bytes(out_buffer), module().paContinue)

    def describe(self) -> str:
        return (
            f"audio pyaudio ({self.state()}) "
            f"on '{self.device or self.name}': {self.config.describe()}"
        )
