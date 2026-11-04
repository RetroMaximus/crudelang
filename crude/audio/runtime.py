"""The script-facing audio runtime: the backend, the Pd session and the meters.

``AudioBackend`` knows how to move samples. ``PdSession`` knows how to ask libpd
for them. :class:`AudioRuntime` is the object that owns both, and it is the only
thing the ``AUDIO_*`` bindings talk to.

Two jobs make it more than a holder:

* **pause wiring** - the plan's control-plane rule is that nothing may change the
  patch while the stream is rendering, so the runtime hands
  :meth:`PdSession.set_pauser` the backend's pause switch when it opens. From
  then on every ``session.control()`` block silences the stream first and brings
  it back afterwards, whichever thread called it.
* **the pulled tick** - when the stream has no device thread (``--no-audio``, a
  machine with no usable device, every test), the runtime *is* the audio thread:
  :meth:`tick` pulls blocks from
  :class:`~crude.audio.null.NullBackend` and ages the meters, which is what makes
  a test and a live run answer ``AUDIO_LEVEL`` the same way. A ``headless`` run
  is *not* in that group: it has no window, but it opens and plays through the
  same real device a window run does.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .base import (
    DB_FLOOR,
    AudioBackend,
    AudioConfig,
    AudioError,
    DeviceInfo,
    Taps,
)

#: ``AUDIO_LEVEL`` modes, and the tap method each one asks for.
LEVEL_MODES = {
    "rms": "rms",
    "peak": "peak",
    "db": "db",
}


class AudioRuntime:
    """Owns the backend plus everything the audio bindings need."""

    def __init__(self, session=None, config: AudioConfig = None,
                 backend: AudioBackend = None, log: Callable[[str], None] = None,
                 preferred=None, strict: bool = False, scope_frames: int = 1024):
        #: the Pd session whose ``process`` renders every block; ``None`` means
        #: "audio with no patch", which still meters, and stays silent
        self.session = session
        self.config = config if config is not None else AudioConfig()
        self.log = log or (lambda message: None)
        self.preferred = preferred
        self.strict = bool(strict)
        self.scope_frames = max(int(scope_frames), 1)

        #: an already-built backend (a test, or ``--no-audio``) skips the chain
        self._backend = backend
        self.notes: List[str] = []
        self.errors: List[str] = []
        self.ticks = 0
        self.starts = 0
        self.stops = 0

        if backend is not None:
            self.notes = list(getattr(backend, "notes", []) or [])
            self._wire(backend)

    # -- the backend ----------------------------------------------------
    @property
    def backend(self) -> Optional[AudioBackend]:
        return self._backend

    @property
    def opened(self) -> bool:
        return self._backend is not None and self._backend.opened

    @property
    def running(self) -> bool:
        return self._backend is not None and self._backend.running

    @property
    def live(self) -> bool:
        """Is a device thread driving the stream, or is this runtime pulling?"""
        return self._backend is not None and self._backend.live

    def open(self) -> AudioBackend:
        """Open a backend if there is not one yet, and pause-wire it."""
        if self._backend is not None:
            # A backend handed to the runtime (a test, ``--no-audio``) is used as
            # given; the fallback chain is only consulted to *find* one.
            if not self._backend.opened:
                self._backend.open()
            self._wire(self._backend)
            return self._backend

        from . import open_backend  # local: the registry imports this module

        backend = open_backend(
            config=self.config,
            render=self._render_to,
            log=self.log,
            preferred=self.preferred,
            notes=self.notes,
            strict=self.strict,
            scope_frames=self.scope_frames,
        )
        self._backend = backend
        self.notes = backend.notes
        self._wire(backend)
        return backend

    def _render_to(self, ticks: int, in_buffer, out_buffer) -> None:
        """Resolve the session on every block, not the one frozen at open time.

        The ``audio.device`` phase opens the stream one step before ``pd.core``
        makes the libpd session, so the backend cannot capture ``session.process``
        when it is built - it must ask the runtime for the session on each block.
        Capturing the property once left a live stream calling a no-op forever,
        which is why a window opened, the DSP went on and still nothing was heard.
        With no session yet the block is silenced rather than left at whatever the
        reusable buffer happened to hold.
        """
        session = self.session
        if session is None:
            for index in range(len(out_buffer)):
                out_buffer[index] = 0.0
            return
        session.process(ticks, in_buffer, out_buffer)

    def attach_session(self, session) -> None:
        """Hand the runtime the libpd session that ``pd.core`` made after opening.

        The device exists before the session does, so this is the one place that
        joins them: it sets the session the :meth:`_render_to` trampoline reads on
        every block and gives the session this stream's pause switch. Called by
        the loader once libpd is up; harmless to call again.
        """
        self.session = session
        if self._backend is not None:
            self._wire(self._backend)

    def _wire(self, backend: AudioBackend) -> None:
        """Give the session this stream's pause switch (the control-plane rule)."""
        if self.session is not None and hasattr(self.session, "set_pauser"):
            self.session.set_pauser(backend.pause_switch())

    def unwire(self) -> None:
        if self.session is not None and hasattr(self.session, "set_pauser"):
            self.session.set_pauser(None)

    @property
    def render(self) -> Callable[[int, Any, Any], None]:
        """The data plane: only ever called from the audio side."""
        if self.session is None:
            return lambda ticks, in_buffer, out_buffer: None
        return self.session.process

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
        self.unwire()

    def start(self) -> AudioBackend:
        """Open if needed, then let the stream run."""
        backend = self.open()
        try:
            backend.start()
        except AudioError as exc:
            self.errors.append(f"audio could not start: {exc}")
            raise
        self.starts += 1
        return backend

    def stop(self) -> None:
        if self._backend is not None and self._backend.running:
            self._backend.stop()
        self.stops += 1

    # -- the live device switch ---------------------------------------
    def current_devices(self) -> tuple:
        """The ``(input, output)`` the stream is configured for (``None`` = default)."""
        return (self.config.input_device, self.config.output_device)

    #: the block sizes a stream may be restarted at: a device block has to
    #  divide into whole 64-frame Pd ticks, and the dialog only offers these
    BLOCK_SIZES = (64, 128, 256, 512, 1024, 2048)

    def apply_settings(self, *, input_device=None, output_device=None,
                       extra_output_devices=(), extra_input_devices=(),
                       sample_rate=None, block_size=None, delay_ms=None,
                       callbacks=None, restart: bool = True) -> str:
        """Re-open the stream with a whole new page of settings, live.

        This is what the audio dialog's Apply does: devices, sample rate,
        blocksize, the delay and the callback preference, all decided together
        rather than one after another, because every one of them is a reason to
        tear the stream down and open the next one. The rules it keeps:

        * a blocksize that is not a multiple of 64 is refused by name - Pd's
          vector is 64 frames and a block that is not a whole number of ticks
          would silently drop the remainder every callback;
        * the rate the device is opened at is handed to libpd too, through the
          session's :meth:`~crude.pd.engine.PdSession.reconfigure`, because a
          DSP left on the old clock while the speakers run on the new one
          would shift every pitch in the show - measured against the real
          libpd, a re-init re-clocks the DSP and keeps the patches;
        * a stream that cannot be reopened is *said*, in the raised message,
          and the old config stays in effect rather than half-applied;
        * side devices the backend has no stream for are said too, because a
          "multiple devices" switch that a one-device backend quietly ignored
          would leave the person believing two speakers are live;
        * a run that opened no stream at all (``--no-audio``) is not a run that
          applies settings: the page is remembered and *said* as remembered,
          rather than the refusal of a device being overridden by a menu.

        The call answers a plain words string - what changed - which is what
        the dialog shows on its status line, so the person reads exactly what
        the running stream agreed to.
        """
        if block_size is not None and int(block_size) not in self.BLOCK_SIZES:
            raise AudioError(
                f"a block size must be whole Pd ticks - "
                f"{', '.join(str(n) for n in self.BLOCK_SIZES)} - "
                f"got {block_size}"
            )
        kwargs = {"input_device": input_device,
                  "output_device": output_device,
                  "output_devices": tuple(extra_output_devices or ()),
                  "input_devices": tuple(extra_input_devices or ())}
        if sample_rate is not None:
            kwargs["sample_rate"] = int(sample_rate)
        if block_size is not None:
            kwargs["block_size"] = int(block_size)
        if delay_ms is not None:
            kwargs["delay_ms"] = float(delay_ms)
        if callbacks is not None:
            kwargs["callbacks"] = bool(callbacks)
        was_running = self.running
        old = self.config
        self.config = self.config.changed(**kwargs)
        if self._backend is None:
            # nothing was ever opened - a ``--no-audio`` run, or a page applied
            # before the device - so there is no stream to hand the new page to
            # and no reason for a menu to open a device this run refused
            return (f"audio is not open in this run; the settings are "
                    f"remembered for when it is: {self.config.describe()}")
        if was_running:
            self._backend.stop()
        self._backend.close()
        self.unwire()
        self._backend = None
        try:
            backend = self.open()
        except AudioError as exc:
            # the stream would not take the new page: say so, and put the old
            # settings back so what describe() reports is what plays
            self.config = old
            self.notes.append(f"audio: settings were not applied - {exc}")
            raise
        if self.session is not None:
            self.session.reconfigure(sample_rate=self.config.sample_rate)
        if restart and was_running:
            self.start()
        wanted = len(self.config.output_devices) + len(self.config.input_devices)
        said = f"audio on {backend.describe()}"
        if wanted > backend.side_devices():
            said += (f"; {wanted - backend.side_devices()} extra device(s) "
                     f"got no stream of their own from {backend.name}")
        return said

    def switch_devices(self, input_device=None, output_device=None,
                       restart: bool = True) -> AudioBackend:
        """Reopen the stream on different devices, without losing the session.

        A config opened with one device index cannot have that index moved under
        it (the channel counts, block size and rate the taps and libpd were built
        for are fixed at open), so switching means tearing the stream down and
        opening a fresh one: the runtime keeps the same session and pause wiring,
        re-opens through :meth:`open` (which hands the new backend the
        :meth:`_render_to` trampoline), and restarts it if it was running before -
        which is what a configuration menu picking a new output has to feel like
        to the person: the sound moves, it does not stop. ``None`` on either side
        means "use the default of that direction", the same as the manifest.
        """
        was_running = self.running
        self.config = self.config.changed(input_device=input_device,
                                          output_device=output_device)
        if self._backend is not None:
            if was_running:
                self._backend.stop()
            self._backend.close()
            self.unwire()
            self._backend = None
        backend = self.open()
        if restart and was_running:
            self.start()
        return backend

    # -- the pulled tick -------------------------------------------------
    def tick(self, blocks: int = 1) -> int:
        """Render ``blocks`` now when no device thread is doing it for us."""
        backend = self._backend
        if backend is None:
            raise AudioError("audio is not open; call AUDIO_START before ticking")

        blocks = max(int(blocks), 0)
        if not blocks:
            return 0

        if backend.live:
            # A real stream is already rendering on its own thread; a caller that
            # "ticks" here is asking for time to pass, so only the meters age.
            self.ticks += blocks
            self.age(blocks * self.config.block_size / float(self.config.sample_rate))
            return 0

        if not backend.opened:
            backend.open()

        # Age first, then render: a peak measured by the last block of this period
        # is still a peak measured *now*, and the pulled answer must not differ
        # from the live one by one block of decay.
        self.age(blocks * self.config.block_size / float(self.config.sample_rate))
        processed = 0
        for _ in range(blocks):
            backend.process_block(1)
            processed += 1
        self.ticks += processed
        return processed

    def age(self, seconds: float) -> None:
        """Let the peak-hold fall, so a meter behaves the same live or silent."""
        if self._backend is None or seconds <= 0:
            return
        self._backend.taps.decay(seconds)
        self._backend.input_taps.decay(seconds)

    # -- meters ---------------------------------------------------------
    def _taps(self, source: str = "output") -> Taps:
        if self._backend is None:
            raise AudioError("audio is not open; call AUDIO_START first")
        if source == "input":
            return self._backend.input_taps
        return self._backend.taps

    def level(self, channel: int = None, mode: str = "rms",
              source: str = "output") -> float:
        """One meter reading: ``rms``/``peak``/``db`` on one channel or the mix.

        With no device open (a ``--no-audio`` run) the answer is silence, not an
        error: a script that meters its output every frame must keep running and
        read a flat zero rather than raise, trip the handler-mute rule and freeze
        the rest of the frame.
        """
        if self._backend is None:
            return 0.0
        if mode not in LEVEL_MODES:
            raise AudioError(
                f"an audio level mode must be {', '.join(sorted(LEVEL_MODES))}, "
                f"got '{mode}'"
            )
        if source not in ("output", "input"):
            raise AudioError(
                f"an audio level source must be 'output' or 'input', got '{source}'"
            )
        taps = self._taps(source)
        if channel is not None:
            channel = int(channel)
        return getattr(taps, LEVEL_MODES[mode])(channel)

    def waveform(self, frames: int = None, channel: int = None,
                 source: str = "output") -> List[float]:
        """The most recent samples, oldest first - what a scope widget draws.

        No device means no samples: an empty trace, so a scope left on a silent
        run draws a flat line instead of raising every frame.
        """
        if self._backend is None:
            return []
        taps = self._taps(source)
        if frames is not None:
            frames = max(int(frames), 0)
        return taps.scope(frames, channel)

    def scope_capacity(self) -> int:
        return self._taps().scope_capacity

    # -- recording ------------------------------------------------------
    def record_start(self, seconds: float, channel: int = 0) -> Dict[str, Any]:
        """Arm the input capture for ``seconds`` of the stream's own rate.

        The buffer is sized here and never grows afterwards, which is what lets a
        device callback write into it: a recording that runs out of room reports
        it as :attr:`~crude.audio.base.Capture.overflow` rather than as a stall.
        Nothing is cleared on the way in - a capture that is already armed is
        started again from zero, because "keep recording over the last take" is
        not a thing anybody means by the word *record*.
        """
        if self._backend is None:
            raise AudioError("audio is not open; call AUDIO_START first")
        if not self.config.in_channels:
            raise AudioError(
                "this stream was opened with no input channels, so there is "
                "nothing to record; the manifest's audio.in_channels must be 1 "
                "or more"
            )
        span = float(seconds)
        if span <= 0.0:
            raise AudioError(f"a recording has to be for some time, got {seconds}")
        rate = int(self.config.sample_rate)
        frames = self._backend.capture.start(int(round(span * rate)), channel,
                                             rate=rate)
        if not self._backend.running:
            # not an error - a pulled stream has no thread to be running - but a
            # live stream that is stopped will capture silence, and that is worth
            # one line the script can see in its own report
            message = "audio record: the stream is not running, so nothing is arriving"
            self.notes.append(message)
            self.log(message)
        return self.record_info()

    def record_stop(self) -> int:
        """Take the capture off the callback; the frames in it stay readable."""
        if self._backend is None:
            return 0
        return self._backend.capture.stop()

    def record_info(self) -> Dict[str, Any]:
        """The capture as it stands: no stream and no capture answer as zeros."""
        if self._backend is None:
            return {
                "armed": False, "channel": 0, "frames": 0, "capacity": 0,
                "overflow": 0, "rate": int(self.config.sample_rate),
                "seconds": 0.0,
            }
        data = self._backend.capture.as_dict()
        data["seconds"] = self._backend.capture.seconds
        return data

    def record_samples(self) -> List[float]:
        """Every frame captured since the last arm, oldest first."""
        if self._backend is None:
            return []
        return self._backend.capture.samples()

    # -- devices --------------------------------------------------------
    def devices(self, direction: str = None) -> List[DeviceInfo]:
        """Every device every usable backend reports, so a script can pick one."""
        from . import list_devices

        listed = list_devices()
        if direction:
            listed = [device for device in listed if device.supports(direction)]
        return listed

    # -- reporting ------------------------------------------------------
    @property
    def taps(self) -> Taps:
        return self._taps()

    def info(self) -> Dict[str, Any]:
        if self._backend is None:
            return {
                "backend": None,
                "live": False,
                "opened": False,
                "running": False,
                "paused": False,
                "device": "",
                "sample_rate": self.config.sample_rate,
                "block_size": self.config.block_size,
                "in_channels": self.config.in_channels,
                "out_channels": self.out_channels,
                "blocks": 0,
                "frames": 0,
                "xruns": 0,
                "dropped": 0,
                "rms": 0.0,
                "peak": 0.0,
                "db": DB_FLOOR,
                "clipped": 0,
                "ticks": self.ticks,
                "starts": self.starts,
                "stops": self.stops,
                "notes": list(self.notes),
            }
        data = dict(self._backend.info())
        data.update({
            "ticks": self.ticks,
            "starts": self.starts,
            "stops": self.stops,
            "notes": list(self.notes),
        })
        return data

    @property
    def out_channels(self) -> int:
        return self.config.out_channels

    #: properties only the runtime can answer, because they are about the runtime
    RUNTIME_PROPS = ("ticks", "starts", "stops", "notes", "seconds")

    def get_prop(self, name: str):
        if name == "seconds":
            frames = self._backend.frames if self._backend is not None else 0
            return frames / float(self.config.sample_rate)
        if name in self.RUNTIME_PROPS:
            return self.info()[name]
        if self._backend is None:
            raise KeyError(name)
        return self._backend.get_prop(name)

    def set_prop(self, name: str, value) -> None:
        # Read-only, and deliberately so: the channel counts, block size and rate
        # a stream was opened with are the ones libpd and the taps were built for.
        raise KeyError(name)

    def describe(self) -> str:
        if self._backend is None:
            return (
                f"audio (not opened): {self.config.describe()}, "
                f"tried: {', '.join(self.notes) or 'nothing yet'}"
            )
        return self._backend.describe()

    def __str__(self):
        return self.describe()

    def __repr__(self):
        return (
            f"<AudioRuntime backend={self._backend.name if self._backend else None} "
            f"running={self.running} ticks={self.ticks}>"
        )

    # -- context manager -------------------------------------------------
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False
