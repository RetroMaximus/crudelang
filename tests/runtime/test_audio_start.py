"""Starting the stream: the loop must bring a live backend up for real.

The "nothing is heard after the splash" bug lived in :meth:`Runtime.start_audio`
- its guard early-returned for a *live* backend, so the OS stream was opened by
the loader but never started, PortAudio's callback never fired, and libpd
rendered nothing. These tests pin the corrected rule: a live backend gets
``start()``; a pulled one is left to the frame tick; an already-running one is
left alone.

A second, quieter silence bug lived in how the opened stream is connected to
libpd: the device is opened a phase before the session exists, so capturing
``session.process`` at open time froze the callback to a no-op and the started
stream still made nothing. The runtime now resolves the session on every block,
which the last two tests pin.
"""

from __future__ import annotations

from typing import List

from crude.runtime import Runtime


class FakeAudio:
    """An ``AudioRuntime`` in shape only: it counts ``start``/``stop`` calls."""

    def __init__(self, *, live: bool, running: bool = False) -> None:
        self.live = live
        self.running = running
        self.opened = True
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1
        self.running = True

    def stop(self) -> None:
        self.stops += 1
        self.running = False


def _loop_with(audio: FakeAudio, mode: str = "window"):
    class Host:
        def __init__(self):
            self.audio = audio
            self.mode = mode
            self.notes: List[str] = []
            self.clock = None

        def note(self, message: str):
            self.notes.append(str(message))

        def record(self, what, closer):
            pass

        def shutdown(self):
            return []

    host = Host()
    loop = Runtime(host, mode=mode)
    loop._display = None
    return loop, host


def test_a_live_backend_is_started_for_real():
    """The fix: a stream a live backend is holding has to be told to run."""
    audio = FakeAudio(live=True, running=False)
    loop, _ = _loop_with(audio)

    loop.start_audio()

    assert audio.starts == 1
    assert audio.running is True


def test_a_pulled_backend_is_left_to_the_frame_tick():
    """A null backend renders in ``tick_audio``; starting it would double meters."""
    audio = FakeAudio(live=False, running=False)
    loop, _ = _loop_with(audio)

    loop.start_audio()

    assert audio.starts == 0


def test_an_already_running_backend_is_not_started_twice():
    audio = FakeAudio(live=True, running=True)
    loop, _ = _loop_with(audio)

    loop.start_audio()

    assert audio.starts == 0


def test_a_terminal_run_does_not_start_the_stream_at_all():
    """``audio_started`` is false for terminal, so the loop says nothing."""
    audio = FakeAudio(live=True, running=False)
    loop, _ = _loop_with(audio, mode="terminal")

    loop.start_audio()

    assert audio.starts == 0


def test_a_backend_that_refuses_to_start_is_named_not_fatal():
    class Sick(FakeAudio):
        def start(self):
            raise RuntimeError("no PortAudio here")

    audio = Sick(live=True)
    loop, host = _loop_with(audio)

    loop.start_audio()  # must not raise

    assert any("audio could not run" in n for n in loop.notes)


# -- the opened stream has to reach libpd, not a captured no-op ----------


class FakeSession:
    """A Pd session in shape only: remembers the blocks it was asked to render."""

    def __init__(self) -> None:
        self.calls: List[int] = []

    def process(self, ticks, in_buffer, out_buffer) -> None:
        self.calls.append(ticks)
        for index in range(len(out_buffer)):
            out_buffer[index] = 0.5


def test_a_block_before_the_session_exists_is_silenced_not_stale():
    """The device opens first; until a session is attached, out must be zero."""
    from crude.audio import AudioConfig
    from crude.audio.runtime import AudioRuntime

    runtime = AudioRuntime(session=None, config=AudioConfig())
    out = [1.0, 1.0, 1.0]
    runtime._render_to(1, None, out)
    assert out == [0.0, 0.0, 0.0]


def test_the_render_resolves_a_session_attached_after_the_stream_was_opened():
    """The fix: a session wired up a phase late is still heard, block by block."""
    from crude.audio import AudioConfig
    from crude.audio.runtime import AudioRuntime

    runtime = AudioRuntime(session=None, config=AudioConfig())
    session = FakeSession()
    runtime.attach_session(session)

    out = [1.0, 1.0]
    runtime._render_to(2, None, out)
    assert session.calls == [2], "the live callback reached libpd's process"
    assert out == [0.5, 0.5]
