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
* **the headless tick** - with no device there is no callback thread, so the
  runtime *is* the audio thread: :meth:`tick` pulls blocks from
  :class:`~crude.audio.null.NullBackend` and ages the meters, which is what makes
  a test and a live run answer ``AUDIO_LEVEL`` the same way.
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
            render=self.render,
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

    # -- the headless tick ----------------------------------------------
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
        # is still a peak measured *now*, and the headless answer must not differ
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
        """One meter reading: ``rms``/``peak``/``db`` on one channel or the mix."""
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
        """The most recent samples, oldest first - what a scope widget draws."""
        taps = self._taps(source)
        if frames is not None:
            frames = max(int(frames), 0)
        return taps.scope(frames, channel)

    def scope_capacity(self) -> int:
        return self._taps().scope_capacity

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
