"""The live audio test: the same code, on a real device.

Deselected by default (``-m "not requires_audio"`` in ``pytest.ini``); run it with
``pytest tests/audio -m requires_audio`` on a machine that has ``sounddevice``.
Nothing here sleeps: a block's arrival is waited for on an ``Event`` the audio
callback itself sets, so a machine with no usable output fails with a message
instead of hanging.
"""

from __future__ import annotations

import importlib
import math
import sys
import threading

import pytest

from crude.audio import (
    AudioConfig,
    AudioRuntime,
    SoundDeviceBackend,
    list_devices,
    open_backend,
)
from crude.audio.base import DB_FLOOR

#: how long a real PortAudio thread gets to deliver its first blocks
TIMEOUT = 10.0

pytestmark = pytest.mark.requires_audio


@pytest.fixture
def real_sounddevice(monkeypatch):
    """Undo the suite-wide "no audio libraries" guard for this module only."""
    # The guard leaves ``sys.modules["sounddevice"] = None``, which would make the
    # import below fail for a reason that is not the machine's, so clear it first.
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    try:
        module = importlib.import_module("sounddevice")
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"sounddevice is not importable: {exc}")
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return module


@pytest.fixture
def live_output(real_sounddevice):
    """A running output stream, fed by a render callable that reports arrival."""
    arrived = threading.Event()
    enough = threading.Event()
    seen = []

    def render(ticks, in_buffer, out_buffer):
        seen.append(ticks)
        if len(seen) >= 16:
            enough.set()
        if len(seen) == 1:
            arrived.set()
        for index in range(len(out_buffer)):
            out_buffer[index] = 0.05

    config = AudioConfig(sample_rate=48000, block_size=64, in_channels=0,
                         out_channels=2)
    backend = open_backend(config=config, render=render,
                           preferred="sounddevice", strict=True)
    backend.start()
    yield backend, arrived, enough, seen
    backend.close()


def test_the_machine_has_something_to_play_through(real_sounddevice):
    devices = [device for device in list_devices("output")
                if device.name != "Null Audio Device"]
    assert devices, "this machine reports no output device at all"
    assert all(device.name for device in devices)
    assert any(device.max_output_channels >= 2 for device in devices)


def test_a_real_stream_renders_on_a_thread_the_script_never_sees(live_output):
    backend, arrived, enough, seen = live_output

    assert arrived.wait(TIMEOUT), "PortAudio never called back"
    assert enough.wait(TIMEOUT), "PortAudio stopped calling back"
    assert backend.blocks >= 16
    assert backend.frames == backend.blocks * backend.config.block_size
    assert set(seen) == {1}  # one Pd tick per 64-frame block
    assert backend.running and backend.live
    assert "(running)" in backend.describe()
    assert backend.device  # the library told us what it opened


def test_the_meters_read_the_same_on_a_card_as_in_headless(live_output):
    backend, arrived, _enough, _seen = live_output
    assert arrived.wait(TIMEOUT), "PortAudio never called back"

    assert backend.taps.rms() == pytest.approx(0.05, abs=0.01)
    assert backend.taps.peak() >= 0.04
    assert backend.taps.db() == pytest.approx(20 * math.log10(0.05), abs=1.0)
    scope = backend.taps.scope(64)
    assert len(scope) == 64
    assert pytest.approx(sum(scope) / len(scope), abs=0.01) == 0.05
    assert backend.silent_blocks == 0


def test_the_default_configuration_is_a_real_stream_too(real_sounddevice):
    """What a project with no ``audio`` section in its manifest asks for:
    nothing named, the default device, and the duplex pair it defaults to."""
    arrived = threading.Event()
    blocks = []

    def render(ticks, in_buffer, out_buffer):
        blocks.append(ticks)
        if len(blocks) >= 8:
            arrived.set()

    backend = open_backend(render=render)
    try:
        assert isinstance(backend, SoundDeviceBackend), backend.notes
        backend.start()
        assert arrived.wait(TIMEOUT), "PortAudio never called back on the default pair"
        assert backend.blocks >= 8
        assert backend.config.in_channels == backend.config.out_channels == 2
    finally:
        backend.close()


def test_a_settings_page_hands_the_device_only_what_it_can_hold(real_sounddevice):
    """The crash only a real library catches: a word where a double belongs.

    The stubs used to record any ``latency`` they were handed, so a page that
    answered the callback switch with the string ``"callback"`` reached ctypes
    and came back as ``must be a real number, not str`` - which is how a first
    run of the metronome example got a window, a slider and no sound.
    """
    for delay_ms in (0.0, 25.0):
        config = AudioConfig(sample_rate=44100, block_size=64, in_channels=0,
                             out_channels=2, delay_ms=delay_ms)
        latency = config.latency
        assert latency is None or isinstance(latency, float), latency
        backend = open_backend(config=config, render=_silence,
                               preferred="sounddevice", strict=True)
        try:
            assert backend.opened is True, backend.notes
            assert backend.blocks == 0
        finally:
            backend.close()


def test_the_other_half_of_the_callback_switch_runs_on_a_real_card(
        real_sounddevice):
    """Unchecking "use callbacks if possible" is a stream with nothing in it.

    PortAudio is then only moved by ``read``/``write``, which is what Pure Data's
    ``-nocallback`` mode means and what this backend's own thread does - so the
    proof is that blocks arrive on a thread the device never calls back into,
    and that ``stop()`` leaves no thread parked on a closed stream.
    """
    enough = threading.Event()
    seen = []

    def render(ticks, in_buffer, out_buffer):
        seen.append(ticks)
        _silence(ticks, in_buffer, out_buffer)
        if len(seen) >= 8:
            enough.set()

    config = AudioConfig(sample_rate=48000, block_size=64, in_channels=0,
                         out_channels=2, callbacks=False)
    assert config.latency is None, "no delay, so nothing handed to ctypes"
    backend = open_backend(config=config, render=render,
                           preferred="sounddevice", strict=True)
    try:
        assert backend.polled is True
        assert type(backend._stream).__name__ == "RawOutputStream"
        backend.start()
        thread = backend._pump_thread
        assert thread is not None and thread.is_alive(), "nothing is pulling the stream"
        assert enough.wait(TIMEOUT), "the polling thread never rendered a block"
        assert backend.blocks >= 8
        assert backend.xruns == 0, f"xruns: {backend.status_flags}"
    finally:
        backend.stop()
    assert backend._pump_thread is None, "the shutdown left a thread on the device"
    backend.close()


def _silence(ticks, in_buffer, out_buffer) -> None:
    """Render nothing: a hardware test must not put a tone through the speakers."""
    for index in range(len(out_buffer)):
        out_buffer[index] = 0.0


class QuietSession:
    """The two halves of a Pd session the audio layer is allowed to see.

    ``set_pauser`` and ``process`` are exactly what :class:`AudioRuntime` asks of
    a session, so this is also the proof that the runtime never reached past them
    into libpd - the device thread calls this, and nothing else does.
    """

    def __init__(self, wants: int = 8):
        self.wants = wants
        self.calls = []
        self.arrived = threading.Event()
        self.pauser = None

    def set_pauser(self, pauser):
        self.pauser = pauser

    def process(self, ticks, in_buffer, out_buffer):
        self.calls.append(ticks)
        if len(self.calls) >= self.wants:
            self.arrived.set()
        return 0


def test_a_runtime_on_a_real_card_is_still_an_audio_runtime(real_sounddevice):
    """The script surface is identical; only the clock differs."""
    session = QuietSession()
    runtime = AudioRuntime(
        session=session,
        config=AudioConfig(sample_rate=48000, block_size=64, in_channels=0,
                           out_channels=2),
        preferred="sounddevice",
        strict=True,
    )
    backend = runtime.start()
    try:
        assert session.arrived.wait(TIMEOUT), "the device thread never rendered"
        assert backend.blocks >= 8
        assert runtime.live is True and backend.live is True
        assert session.pauser is not None  # the pause switch reached the session

        info = runtime.info()
        assert info["backend"] == "sounddevice"
        assert info["running"] is True and info["live"] is True
        assert info["sample_rate"] == 48000
        assert info["blocks"] >= 8

        # A session that renders nothing reads as silence, exactly as the silent
        # backend does in the fake tests: the same answers, a different clock.
        assert runtime.level() == pytest.approx(0.0)
        assert runtime.level(mode="db") == DB_FLOOR
        assert runtime.waveform(8) == [0.0] * 8
        assert runtime.tick(2) == 0  # the device thread renders, not us
        assert runtime.ticks == 2
    finally:
        runtime.stop()
        runtime.close()
    assert backend.opened is False
