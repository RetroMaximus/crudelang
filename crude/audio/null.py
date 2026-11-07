"""The silent backend: audio without a device.

``NullBackend`` is what the plan calls the "no audio stream" case - ``--no-audio``,
a machine whose real backends all failed (the run then says out loud that it
reached no device), CI and every test. It is *not* what a ``headless`` run means:
headless only drops the window, and opens the real device if this one exists.
Instead of a PortAudio thread pushing
blocks at it, the caller pulls: :meth:`NullBackend.process_block` *is* the libpd
thread, run exactly when the script says so.

Two things follow from that, and both are the reason this backend exists:

* **determinism** - how many blocks happen, what the input carried and therefore
  what the meters read is decided by the caller, not by a device clock;
* **parity** - the block runs through the shared :meth:`AudioBackend.consume`,
  so what a unit test measures is what the real callback does on a sound card.
"""

from __future__ import annotations

from typing import Callable, List, Sequence

from .base import (
    DEFAULT_SAMPLE_RATE,
    AudioBackend,
    AudioConfig,
    AudioError,
    DeviceInfo,
    Renderer,
    read_samples,
)

#: The device every backend agrees to have: nothing at all.
NULL_DEVICE = "Null Audio Device"


class NullBackend(AudioBackend):
    """A pull-based silent stream: blocks happen when the caller says so."""

    name = "null"
    live = False
    optional = True

    def __init__(self, config: AudioConfig = None, render: Renderer = None,
                 log: Callable[[str], None] = None, scope_frames: int = 1024,
                 fill: Callable[[Sequence[float]], None] = None):
        super().__init__(config, render, log=log, scope_frames=scope_frames)
        #: called with the input buffer before a block is rendered, so a test can
        #: feed the microphone without having one
        self.fill = fill
        self.blocks_requested = 0
        #: snapshots of the most recent block, for tests and ``crude audio``
        self.last_output: List[float] = []
        self.last_input: List[float] = []

    # -- capability -----------------------------------------------------
    @classmethod
    def available(cls) -> bool:
        return True

    @classmethod
    def devices(cls) -> List[DeviceInfo]:
        return [
            DeviceInfo(
                name=NULL_DEVICE,
                index=None,
                max_input_channels=2,
                max_output_channels=2,
                default_sample_rate=DEFAULT_SAMPLE_RATE,
                host_api="silent",
            )
        ]

    # -- lifecycle ------------------------------------------------------
    def _open(self) -> None:
        self.device = NULL_DEVICE

    def _start(self) -> None:
        pass

    def _stop(self) -> None:
        pass

    def _close(self) -> None:
        self.last_output = []
        self.last_input = []

    # -- the pull side --------------------------------------------------
    def process_block(self, ticks: int = 1) -> int:
        """Render ``ticks`` blocks now; returns how many samples came out."""
        if not self.opened:
            raise AudioError(
                "the null audio stream is not open; call AUDIO_START before "
                "rendering a block"
            )

        in_buffer, out_buffer = self.allocate(ticks)
        if self.fill is not None and self.config.in_channels:
            self.fill(in_buffer)
        self.blocks_requested += ticks

        written = self.consume(in_buffer, out_buffer, ticks)
        # Copies, not the buffers: the caller keeps these after the next block
        # overwrites them.
        self.last_input = read_samples(in_buffer)
        self.last_output = read_samples(out_buffer)
        return written

    def process_blocks(self, count: int = 1) -> int:
        """Pull ``count`` blocks one at a time, as the runtime loop would."""
        total = 0
        for _ in range(max(int(count), 0)):
            total += self.process_block(1)
        return total

    def output(self, channel: int = None) -> List[float]:
        """The last rendered block: every sample, or one de-interleaved channel."""
        samples = list(self.last_output)
        if channel is None:
            return samples
        channels = max(self.config.out_channels, 1)
        if not 0 <= int(channel) < channels:
            raise AudioError(
                f"audio channel {channel} is out of range "
                f"(this stream has {channels})"
            )
        return [samples[i] for i in range(int(channel), len(samples), channels)]

    def describe(self) -> str:
        state = "running" if self.running else ("open" if self.opened else "closed")
        return (
            f"audio null ({state}): {self.config.describe()}, "
            f"pulled {self.blocks_requested} block(s)"
        )
