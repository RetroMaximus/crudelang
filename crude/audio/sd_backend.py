"""The default backend: ``sounddevice`` (PortAudio) as crude sees it.

This is the only module in the package that imports a device library, and it
imports it *lazily* - inside the methods that need it - so that importing
:mod:`crude.audio` never fails on a machine without PortAudio, and a test can
put a stub in ``sys.modules["sounddevice"]`` and drive the callback by hand.

The callback is the hot path, so it does four things and nothing else: tally the
status flags, copy samples into the reusable ctypes buffers libpd wants, run the
renderer (which is ``PdSession.process``, and therefore the only place the data
plane is entered), copy the result back. No allocation, no queue, no interpreter.

Unchecking the dialog's "use callbacks if possible" asks for the other shape Port
Audio offers: a stream with no callback at all, which only moves when somebody
calls ``read``/``write``. That is what :meth:`SoundDeviceBackend._pump` is for -
the same block body, run from crude's own thread instead of the device's, which
is exactly what Pure Data's ``-nocallback`` mode does.
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional, Tuple

from .base import (
    INPUT_OVERFLOW,
    OUTPUT_UNDERFLOW,
    AudioBackend,
    AudioConfig,
    AudioUnavailable,
    DeviceInfo,
    Renderer,
    check_direction,
    copy_samples,
    read_samples,
    resolve_device,
    samples_to_bytes,
)

MODULE = "sounddevice"

#: how long a shutdown waits for the polling thread to leave a blocked ``read``
PUMP_JOIN_SECONDS = 2.0


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

    The dialog's callback switch chooses the *other* half of that pair of
    decisions: the ``Raw`` classes have no callback and are driven with
    ``read``/``write`` from crude's own thread, so a block is only ever rendered
    when this side asks for one. The shape, the buffers and the renderer are the
    same either way - only who pulls the block changes.
    """

    name = "sounddevice"
    live = True

    #: one stream class per shape, per who-drives-the-stream answer
    CLASSES = {
        False: {"duplex": "Stream", "output": "OutputStream", "input": "InputStream"},
        True: {"duplex": "RawStream", "output": "RawOutputStream",
               "input": "RawInputStream"},
    }

    def __init__(self, config: AudioConfig = None, render: Renderer = None,
                 log: Callable[[str], None] = None, scope_frames: int = 1024,
                 device: Tuple[Optional[int], Optional[int]] = None):
        super().__init__(config, render, log=log, scope_frames=scope_frames)
        #: ``(input index, output index)`` decided once, at open time
        self._device = device
        self._stream = None
        #: extra output streams the rendered block is copied to (multi-device)
        self._side_streams = []
        #: ``(stream, buffer)`` per extra input device: the callback keeps the
        #  latest block of each side microphone here and the render callback
        #  mixes it into the block the session reads
        self._side_inputs = []
        #: ``duplex``/``output``/``input`` - the PortAudio shape, set at open
        self.kind = "duplex"
        #: True when the stream was opened without a callback and ``_pump`` is
        #  the only thing that makes samples move (the "no callbacks" setting)
        self.polled = False
        self._pump_thread = None
        self._pumped = threading.Event()

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

    def _arguments(self, kind: str, device, channels) -> dict:
        """How the library is asked for a stream of this shape and mode.

        Both modes get the same numbers - rate, block size, channels, dtype and
        the timing target the dialog's delay set - because a device driven by
        ``read``/``write`` is not a different clock, only a different puller. The
        callback and its finished hook are what the polled mode must not have:
        handing a ``Raw`` stream a callback would put the renderer back on
        PortAudio's thread and make the switch a lie.
        """
        config = self.config
        arguments = {
            "device": device,
            "samplerate": config.sample_rate,
            "blocksize": config.block_size,
            "channels": channels,
            "dtype": "float32",
        }
        latency = config.latency
        if latency is not None:
            arguments["latency"] = latency
        if not self.polled:
            arguments["callback"] = {
                "duplex": self.callback, "output": self.output_callback,
                "input": self.input_callback,
            }[kind]
            arguments["finished_callback"] = self._finished
        return arguments

    def _open(self) -> None:
        sd = module()
        config = self.config
        kind, device, channels = self._plan()
        self.kind = kind
        self.polled = not config.callbacks
        maker = getattr(sd, self.CLASSES[self.polled][kind])

        try:
            self._stream = maker(**self._arguments(kind, device, channels))
        except AudioUnavailable:
            raise
        except Exception as exc:  # ValueError, RuntimeError, PortAudio errors
            self._stream = None
            raise AudioUnavailable(
                f"sounddevice could not open {config.describe()}: {exc}"
            )

        self._open_sides()
        self.device = self._device_name(_index_of(kind, device))

    def _open_sides(self) -> None:
        """One extra stream per side device, in whichever direction it speaks.

        A side output is deliberately *callback-less*: PortAudio's own thread
        drains its ring buffer, and the render callback only ever calls
        ``write`` - which copies into that buffer and never blocks on the
        device - so opening a second pair of speakers does not put a second
        copy of the renderer anywhere. A side input is the other shape: it must
        have a callback, because that is the only way samples arrive unasked,
        and the callback's whole job is to copy the block into a buffer the
        render callback adds in - so Pd hears both microphones as one input.

        A side that will not open fails the whole request: a silently missing
        speaker, or a microphone nobody is listening to, is exactly the fake
        this project refuses.
        """
        sd = module()
        config = self.config
        self._side_streams = []
        self._side_inputs = []
        if config.out_channels:
            self._open_side_outputs(sd, config)
        if config.in_channels:
            self._open_side_inputs(sd, config)

    def _side_latency(self) -> dict:
        """The timing target a side stream is asked for, if there is one.

        The same page of settings applies to every device in the show: a delay
        handed to the primary stream is handed to the side ones too, or the
        devices would not be running on one clock.
        """
        latency = self.config.latency
        return {} if latency is None else {"latency": latency}

    def _open_side_outputs(self, sd, config) -> None:
        wanted = [index for index in
                  (resolve_device(held, "output", query_devices)
                   for held in config.output_devices)
                  if index is not None
                  and index != resolve_device(config.output_device, "output",
                                              query_devices)]
        extra = self._side_latency()
        # a side output never has a callback in either mode: it is written to,
        # so the only thing the switch changes here is the buffer shape write()
        # takes - a ``Raw`` one wants bytes, a plain one the device's own block
        maker = sd.RawOutputStream if self.polled else sd.OutputStream
        try:
            for index in wanted:
                self._side_streams.append(maker(
                    device=index,
                    samplerate=config.sample_rate,
                    blocksize=config.block_size,
                    channels=config.out_channels,
                    dtype="float32",
                    **extra,
                ))
        except Exception as exc:
            self._close_sides()
            raise AudioUnavailable(
                f"sounddevice could not open side output {index}: {exc}"
            )

    def _open_side_inputs(self, sd, config) -> None:
        """One stream per extra input device, callback-driven or polled.

        With callbacks on the only way samples arrive unasked is a callback, so
        each side gets one whose whole job is to copy its block into the buffer
        the render callback mixes in. With callbacks off the sides are opened
        ``Raw`` like the primary stream and :meth:`_pump` reads them itself, so
        the switch means what it says for every device in the show.
        """
        primary = self._select()[0]
        maker = sd.RawInputStream if self.polled else sd.InputStream
        for held in config.input_devices:
            index = resolve_device(held, "input", query_devices)
            if index is None or index == primary:
                continue
            # one buffer per side, allocated here and only ever written into by
            # the pump or the side's own callback: the hot path must not malloc
            block = self.allocate(1)[0]
            arguments = {"device": index, "samplerate": config.sample_rate,
                         "blocksize": config.block_size,
                         "channels": config.in_channels, "dtype": "float32"}
            latency = config.latency
            if latency is not None:
                arguments["latency"] = latency
            if not self.polled:
                arguments["callback"] = self._side_callback(block)
            try:
                stream = maker(**arguments)
            except Exception as exc:
                self._close_sides()
                raise AudioUnavailable(
                    f"sounddevice could not open side input {index}: {exc}"
                )
            self._side_inputs.append((stream, block))

    def _side_callback(self, block):
        """The callback a side microphone is opened with: copy, and nothing else."""
        def on_input(indata, frames, time_info, status, _block=block) -> None:
            copy_samples(_block, indata)

        return on_input

    def _close_sides(self) -> None:
        streams, self._side_streams = self._side_streams, []
        inputs, self._side_inputs = self._side_inputs, []
        for stream in streams:
            try:
                stream.close()
            except Exception:  # pragma: no cover - closing is best-effort
                pass
        for stream, _block in inputs:
            try:
                stream.close()
            except Exception:  # pragma: no cover - closing is best-effort
                pass

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
            for stream in self._side_streams:
                stream.start()
            for stream, _block in self._side_inputs:
                stream.start()
        except AudioUnavailable:
            raise
        except Exception as exc:
            raise AudioUnavailable(f"sounddevice could not start the stream: {exc}")
        if self.polled:
            # nothing will ever call this backend again unless it calls itself,
            # so the pump is the difference between a stream and a sounding one
            self._pumped.clear()
            self._pump_thread = threading.Thread(
                target=self._pump, name="crude-audio-poll", daemon=True)
            self._pump_thread.start()

    def _stop(self) -> None:
        # told between blocks first, then the stream is stopped: a read parked on
        # a device that is going away is woken by the stop, which is how the pump
        # gets to leave without being waited for in vain
        self._pumped.set()
        if self._stream is not None:
            self._stream.stop()
        self._join_pump()
        for stream in self._side_streams:
            stream.stop()
        for stream, _block in self._side_inputs:
            stream.stop()

    def _join_pump(self) -> None:
        """Wait, bounded, for the polling thread to have left.

        The wait has a ceiling because a device stuck inside a kernel call must
        not be able to hang a shutdown: the thread is a daemon, so it dies with
        the interpreter rather than with the last block it never got.
        """
        thread, self._pump_thread = self._pump_thread, None
        if thread is None or not thread.is_alive():
            return
        thread.join(timeout=PUMP_JOIN_SECONDS)
        if thread.is_alive():  # pragma: no cover - only a wedged device gets here
            self.notes.append("audio: the polling thread did not leave the device "
                              f"within {PUMP_JOIN_SECONDS:g} s")

    def _close(self) -> None:
        self._pumped.set()
        self._join_pump()
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.close()
        self._close_sides()

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
        self._mix_sides(in_buffer)

        self.consume(in_buffer, out_buffer, ticks)
        if outdata is not None:
            copy_samples(outdata, out_buffer)
        for stream in self._side_streams:
            # ``write`` copies into PortAudio's ring buffer - the same samples,
            # a second device later, with no second render and no blocking
            stream.write(outdata)

    # -- the polled path ------------------------------------------------
    def _pump(self) -> None:
        """Polling mode's audio thread: one block at a time until told to stop.

        Reached only with the callback switch off, and it is the whole reason
        that switch can be offered at all: a stream opened without a callback
        does nothing until somebody calls ``read``/``write``, so this loop is
        what makes the device move. Both calls block until PortAudio is ready
        for a block, and that is the pacing - no sleep, no timer, and the same
        block body a callback would have run.

        A device that stops answering must not take the interpreter down with
        it: the first failure is tallied, said out loud and ends the thread,
        leaving the stream open so ``AUDIO_INFO`` can still be asked what
        happened - a silent fallback is the one thing worse than a stalled one.
        """
        frames = self.config.block_size
        while not self._pumped.is_set():
            try:
                self._polled_block(frames)
            except Exception as exc:  # PortAudioError, OSError, unplugged device
                if self._pumped.is_set():
                    # shutting down: the stop is what made the device refuse the
                    # read, so reporting it as a fault would be reporting ourselves
                    return
                self.notes.append(f"audio: the polling thread stopped - {exc}")
                self.log(f"audio {self.name}: polling stopped: {exc}")
                return

    def _polled_block(self, frames: int) -> None:
        """Read one block, render it, write it back - the callback's body, pulled.

        ``read`` hands out raw bytes rather than the device-shaped buffer a
        callback is given, so the conversion the PyAudio backend already uses is
        the one thing between them and libpd. This thread is not PortAudio's
        own, so the bytes it copies are no risk to a deadline; the buffers the
        renderer works in are still the pooled ctypes arrays.
        """
        ticks = self.config.ticks_for(frames)
        in_buffer, out_buffer = self.buffers_for(ticks)

        if self.kind in ("duplex", "input") and self.config.in_channels:
            data, overflowed = self._stream.read(frames)
            if overflowed:
                self.counts_status(INPUT_OVERFLOW)
            copy_samples(in_buffer, read_samples(bytes(data)))
        for stream, block in self._side_inputs:
            # every side microphone is asked for its block on the same beat, so
            # the show stays on one clock even with nothing in a callback
            data, _overflowed = stream.read(frames)
            copy_samples(block, read_samples(bytes(data)))
        self._mix_sides(in_buffer)

        self.consume(in_buffer, out_buffer, ticks)
        if self.kind in ("duplex", "output") and self.config.out_channels:
            payload = samples_to_bytes(out_buffer)
            if self._stream.write(payload):
                self.counts_status(OUTPUT_UNDERFLOW)
            for stream in self._side_streams:
                stream.write(payload)

    def _mix_sides(self, in_buffer) -> None:
        """Add every side microphone's latest block into the block Pd reads.

        The buffers are interleaved with the same channel count and block size
        as the primary stream - that is what they were opened with - so the mix
        is sample by sample from index 0. A side stream's callback runs on a
        PortAudio thread, and every Python callback holds the GIL, so the copy
        and this read cannot land on each other mid-sample.
        """
        for _stream, block in self._side_inputs:
            for index in range(min(len(in_buffer), len(block))):
                in_buffer[index] = in_buffer[index] + block[index]

    def side_devices(self) -> int:
        """How many extra devices this backend really put on streams."""
        return len(self._side_streams) + len(self._side_inputs)

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
