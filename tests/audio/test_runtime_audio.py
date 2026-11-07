"""``AudioRuntime``: the pause wiring and the headless tick.

This is where the plan's two threading rules either hold or do not, so both are
pinned here against a real :class:`crude.pd.PdSession` over ``FakeLibpd``:

* the control plane runs with the stream paused (:meth:`tick` never reaches libpd
  while ``session.control()`` is inside its block);
* the audio side is the only data-plane caller, and when no device thread exists
  the runtime *is* that thread.
"""

from __future__ import annotations

import math

import pytest

from crude.audio import (
    AudioConfig,
    AudioError,
    AudioUnavailable,
    NullBackend,
    RecordingRender,
    SoundDeviceBackend,
)
from crude.audio.base import DB_FLOOR
from crude.audio.runtime import AudioRuntime


# -- opening ------------------------------------------------------------


def test_an_injected_backend_is_used_as_it_was_given(config):
    backend = NullBackend(config, render=RecordingRender(value=0.25))
    runtime = AudioRuntime(config=config, backend=backend)

    assert runtime.backend is backend
    assert runtime.open() is backend  # not replaced by a chain-built twin
    assert runtime.opened and not runtime.live


def test_with_no_device_the_runtime_finds_silence_and_keeps_the_reasons(
        config, no_audio_libraries):
    runtime = AudioRuntime(config=config)
    backend = runtime.open()

    assert isinstance(backend, NullBackend)
    assert [note for note in runtime.notes if "not usable" in note] == [
        "audio: sounddevice is not usable on this machine",
        "audio: pyaudio is not usable on this machine",
    ]
    runtime.close()


def test_a_strict_request_fails_but_still_says_why(config, no_audio_libraries):
    runtime = AudioRuntime(config=config, preferred="sounddevice", strict=True)

    with pytest.raises(AudioUnavailable, match="no audio backend could be opened"):
        runtime.open()
    assert "sounddevice is not usable" in " ".join(runtime.notes)


def test_a_configuration_the_backend_cannot_take_is_remembered_as_an_error(
        config, stub_sounddevice):
    stub_sounddevice(fail_start="Device is busy")
    backend = SoundDeviceBackend(config, render=RecordingRender(value=0.0))
    runtime = AudioRuntime(config=config, backend=backend)

    with pytest.raises(AudioUnavailable, match="could not start the stream: Device is busy"):
        runtime.start()
    assert runtime.errors == [
        f"audio could not start: {backend.name} could not start the stream: Device is busy"
    ]
    assert runtime.starts == 0
    runtime.close()


# -- the pause wiring (the control-plane rule) --------------------------


def test_opening_hands_the_session_the_stream_pause_switch(config, pd_session):
    runtime = AudioRuntime(session=pd_session, config=config)
    backend = runtime.open()

    assert pd_session._pauser is not None
    pd_session._pauser(True)
    assert backend.paused is True
    pd_session._pauser(False)
    assert backend.paused is False
    runtime.close()


def test_a_control_plane_block_silences_the_stream(config, pd_session):
    runtime = AudioRuntime(session=pd_session, config=config)
    backend = runtime.open()

    assert backend.paused is False
    with pd_session.control():
        assert backend.paused is True
    assert backend.paused is False
    runtime.close()


def test_closing_takes_the_pause_switch_back_off(config, pd_session):
    runtime = AudioRuntime(session=pd_session, config=config)
    runtime.open()
    runtime.close()

    assert pd_session._pauser is None


def test_rendering_goes_to_the_session_and_nowhere_else(config, pd_session):
    runtime = AudioRuntime(session=pd_session, config=config)
    runtime.open()

    assert runtime.render == pd_session.process
    runtime.tick(3)
    assert pd_session.lib.process_calls == [1, 1, 1]
    runtime.close()


# -- the headless tick --------------------------------------------------


def test_a_tick_renders_exactly_the_blocks_asked_for(audio_runtime, recording):
    assert audio_runtime.tick(4) == 4
    assert recording.calls == [1, 1, 1, 1]
    assert audio_runtime.ticks == 4
    assert audio_runtime.backend.blocks_requested == 4


def test_a_tick_of_nothing_does_nothing(audio_runtime, recording):
    assert audio_runtime.tick(0) == 0
    assert recording.calls == []


def test_ticking_before_starting_is_explained(config):
    runtime = AudioRuntime(config=config)

    with pytest.raises(AudioError, match=r"audio is not open; call AUDIO_START before ticking"):
        runtime.tick()


def test_a_live_stream_is_never_rendered_by_hand(config, stub_sounddevice):
    stub_sounddevice()
    recording = RecordingRender(value=0.5)
    backend = SoundDeviceBackend(config, render=recording)
    runtime = AudioRuntime(config=config, backend=backend)
    runtime.open()

    assert runtime.tick(2) == 0  # the device thread has this job, not us
    assert recording.calls == []
    assert backend.blocks == 0
    assert runtime.ticks == 2
    runtime.close()


def test_a_tick_ages_a_peak_hold_and_never_the_rms(audio_runtime):
    audio_runtime.tick(1)
    assert audio_runtime.level(mode="peak") == pytest.approx(0.25)

    # A peak-hold rises at once and only falls as time passes, so the same meter
    # behaves identically on a live stream and on pulled blocks.
    audio_runtime.age(0.1)
    assert audio_runtime.level(mode="peak") == pytest.approx(0.1)
    audio_runtime.age(1.0)
    assert audio_runtime.level(mode="peak") == 0.0
    assert audio_runtime.level(mode="rms") == pytest.approx(0.25)

    audio_runtime.age(0)  # no time, no change
    assert audio_runtime.level(mode="peak") == 0.0


def test_a_live_tick_only_let_the_meters_age(config, stub_sounddevice):
    stub_sounddevice()
    taps_backend = SoundDeviceBackend(config, render=RecordingRender(value=0.5))
    runtime = AudioRuntime(config=config, backend=taps_backend)
    runtime.open()
    taps_backend.taps.push([0.5] * 128, 2)

    runtime.tick(1)
    assert taps_backend.blocks == 0            # nothing was rendered by hand
    assert taps_backend.taps.peak() == pytest.approx(0.5 - 1.5 * 64 / 48000)
    runtime.close()


def test_a_tick_pulls_silence_from_a_paused_stream(audio_runtime, recording):
    audio_runtime.backend.pause()
    assert audio_runtime.tick(2) == 2
    assert recording.calls == []
    assert audio_runtime.backend.silent_blocks == 2


# -- meters -------------------------------------------------------------


def test_a_block_that_was_rendered_is_a_block_that_was_metered(audio_runtime):
    audio_runtime.tick(1)

    assert audio_runtime.level() == pytest.approx(0.25)
    assert audio_runtime.level(0) == pytest.approx(0.25)
    assert audio_runtime.level(mode="peak") == pytest.approx(0.25)
    assert audio_runtime.level(mode="db") == pytest.approx(20 * math.log10(0.25))


def test_an_input_can_be_metered_without_a_microphone(config):
    def fill(buffer):
        for index in range(len(buffer)):
            buffer[index] = 0.5

    backend = NullBackend(config, render=RecordingRender(value=0.0), fill=fill)
    runtime = AudioRuntime(config=config, backend=backend)
    runtime.tick(1)

    assert runtime.level(0, "rms", "input") == pytest.approx(0.5)
    assert runtime.level(0, "rms", "output") == pytest.approx(0.0)
    assert runtime.level(mode="db", source="output") == DB_FLOOR
    runtime.close()


def test_a_mode_that_is_not_a_meter_mode_is_named_as_one(config):
    runtime = AudioRuntime(config=config, backend=NullBackend(config))

    with pytest.raises(AudioError, match=r"level mode must be db, peak, rms, got 'loud'"):
        runtime.level(mode="loud")
    with pytest.raises(AudioError, match=r"source must be 'output' or 'input', got 'sideways'"):
        runtime.level(source="sideways")
    with pytest.raises(AudioError, match=r"audio channel 5 is out of range \(this stream has 2\)"):
        runtime.level(5)


def test_a_level_without_a_stream_is_silence_not_an_error(config):
    """No device means a flat zero, so a per-frame meter never trips a mute."""
    runtime = AudioRuntime(config=config)

    assert runtime.level() == 0.0
    assert runtime.waveform() == []


def test_a_waveform_is_the_recent_mix_oldest_first(audio_runtime):
    audio_runtime.tick(2)

    samples = audio_runtime.waveform()
    assert len(samples) == 64 * 2  # two blocks of frames, on the mix not per channel
    assert len(samples) <= audio_runtime.scope_capacity()
    assert samples == [0.25] * len(samples)
    assert audio_runtime.waveform(4) == [0.25] * 4
    assert audio_runtime.waveform(0) == []


def test_the_scope_holds_the_frames_the_runtime_was_asked_for(config, no_audio_libraries):
    runtime = AudioRuntime(config=config, scope_frames=32)
    runtime.open()

    assert runtime.scope_capacity() == 32
    runtime.tick(4)  # 4 x 64 frames is far more history than the ring holds
    assert len(runtime.waveform()) == 32
    runtime.close()


def test_a_waveform_channel_is_validated_too(audio_runtime):
    with pytest.raises(AudioError, match="audio channel 9 is out of range"):
        audio_runtime.waveform(4, 9)


# -- devices and reporting ----------------------------------------------


def test_the_device_list_is_every_backends(config, stub_sounddevice, stub_pyaudio):
    stub_sounddevice()
    stub_pyaudio()
    runtime = AudioRuntime(config=config)

    assert [device.name for device in runtime.devices()] == [
        "Microphone (Loopback)", "Speakers (Loopback)", "Studio Capture",
        "Microphone (Loopback)", "Speakers (Loopback)", "Studio Capture",
        "Null Audio Device",
    ]
    assert all(device.is_input for device in runtime.devices("input"))


def test_info_before_and_after_a_stream(audio_runtime):
    audio_runtime.tick(2)
    info = audio_runtime.info()

    assert info["backend"] == "null"
    assert info["live"] is False and info["running"] is False
    assert info["blocks"] == 2 and info["frames"] == 128
    assert info["sample_rate"] == 48000 and info["block_size"] == 64
    assert info["rms"] == pytest.approx(0.25)
    assert info["level"] == info["rms"]
    assert info["ticks"] == 2
    assert info["starts"] == 0 and info["stops"] == 0
    assert isinstance(info["notes"], list)


def test_a_runtime_with_no_stream_still_answers(audio_runtime):
    audio_runtime._backend = None
    info = audio_runtime.info()

    assert info["backend"] is None and info["opened"] is False
    assert info["device"] == "" and info["blocks"] == 0
    assert info["db"] == DB_FLOOR
    assert info["out_channels"] == audio_runtime.config.out_channels


def test_get_reads_the_runtime_first_and_the_backend_after(audio_runtime):
    audio_runtime.tick(3)

    assert audio_runtime.get_prop("ticks") == 3
    assert audio_runtime.get_prop("backend") == "null"
    # Booleans become 0/1 on the way out, the way every other host object does it
    assert audio_runtime.get_prop("running") == 0
    assert audio_runtime.get_prop("opened") == 1
    assert audio_runtime.get_prop("seconds") == pytest.approx(3 * 64 / 48000)
    assert audio_runtime.get_prop("out_channels") == 2
    with pytest.raises(KeyError):
        audio_runtime.get_prop("loudness")


def test_an_audio_object_has_no_writable_properties(audio_runtime):
    with pytest.raises(KeyError):
        audio_runtime.set_prop("sample_rate", 96000)


def test_describe_says_the_backend_or_says_it_is_not_open(audio_runtime, config):
    assert "audio null" in audio_runtime.describe()
    assert "pulled 0 block(s)" in audio_runtime.describe()

    runtime = AudioRuntime(config=config)
    runtime.notes = ["audio: sounddevice is not usable"]
    assert runtime.describe() == (
        "audio (not opened): 48000 Hz, 64-frame blocks, 2 in / 2 out, "
        "tried: audio: sounddevice is not usable"
    )
    assert str(runtime) == runtime.describe()
    assert repr(runtime) == "<AudioRuntime backend=None running=False ticks=0>"
    assert repr(audio_runtime) == (
        "<AudioRuntime backend=null running=False ticks=0>"
    )


def test_a_runtime_used_as_a_context_manager_starts_and_stops(config):
    backend = NullBackend(config, render=RecordingRender(value=0.1))
    with AudioRuntime(config=config, backend=backend) as runtime:
        assert runtime.running and backend.running
    assert backend.opened is False and runtime.stops == 0


# -- the settings page, applied while the show runs ---------------------


def test_the_whole_page_is_decided_once_and_libpd_is_re_clocked_with_it(
        config, pd_session):
    """Devices, rate, blocksize, delay and the callback preference go in together
    - and the DSP follows the rate the speakers got, or every pitch would shift.
    """
    backend = NullBackend(config, render=RecordingRender(value=0.0))
    runtime = AudioRuntime(session=pd_session, config=config, backend=backend)
    runtime.start()

    said = runtime.apply_settings(sample_rate=44100, block_size=128,
                                  delay_ms=10.0, callbacks=False)
    assert runtime.config.sample_rate == 44100
    assert runtime.config.block_size == 128
    assert runtime.config.delay_ms == 10.0
    assert runtime.config.callbacks is False
    assert runtime.config.latency == 0.01
    assert pd_session.sample_rate == 44100, "the session followed the device"
    assert pd_session.lib.audio_format == (2, 2, 44100)
    assert runtime.running, "the sound moved, it did not stop"
    assert said.startswith("audio on "), "the page answers in words"
    runtime.close()


def test_a_rate_that_is_already_the_rate_says_nothing_to_libpd(config, pd_session):
    """A page applied without moving the clock does not re-init the DSP."""
    backend = NullBackend(config, render=RecordingRender(value=0.0))
    runtime = AudioRuntime(session=pd_session, config=config, backend=backend)
    runtime.start()
    before = pd_session.lib.audio_format

    runtime.apply_settings(block_size=256)
    assert pd_session.lib.audio_format == before == (2, 2, 48000)
    runtime.close()


def test_a_blocksize_that_is_not_whole_pd_ticks_is_refused_by_name(config):
    """Pd's vector is 64 frames: a block that is not a whole number of ticks
    would drop the remainder every callback, so the page is refused up front."""
    runtime = AudioRuntime(config=config, backend=NullBackend(config))
    runtime.open()

    with pytest.raises(AudioError, match=r"a block size must be whole Pd ticks - "
                                         r"64, 128, 256, 512, 1024, 2048 - got 96"):
        runtime.apply_settings(block_size=96)
    assert runtime.config.block_size == 64, "nothing was half-applied"
    runtime.close()


def test_a_stream_that_will_not_take_the_page_keeps_the_old_settings(
        config, stub_sounddevice):
    """A refused reopen is said, and what plays is still what played before."""
    stub = stub_sounddevice()
    backend = SoundDeviceBackend(config, render=RecordingRender(value=0.0))
    runtime = AudioRuntime(config=config, backend=backend,
                           preferred="sounddevice", strict=True)
    runtime.start()
    # the next open - the one Apply asks for - is the one that fails
    stub.open_error = "Device is busy"

    with pytest.raises(AudioUnavailable, match="could not open"):
        runtime.apply_settings(sample_rate=96000)
    assert runtime.config.sample_rate == 48000
    assert [note for note in runtime.notes if "settings were not applied" in note]
    runtime.close()


def test_extra_devices_a_backend_cannot_open_are_said_rather_than_claimed(config):
    """The ``null`` stream has one device, so a multi-device page says exactly
    that instead of letting the person believe two speakers are live."""
    runtime = AudioRuntime(config=config, backend=NullBackend(config))
    runtime.open()

    said = runtime.apply_settings(extra_output_devices=(2,),
                                  extra_input_devices=(0,))
    assert "2 extra device(s) got no stream of their own from null" in said
    assert runtime.config.output_devices == (2,)
    assert runtime.config.input_devices == (0,)
    runtime.close()


def test_extra_devices_a_backend_does_open_are_not_commented_on(config,
                                                                stub_sounddevice):
    stub_sounddevice()
    runtime = AudioRuntime(config=config, backend=SoundDeviceBackend(config))
    runtime.open()

    said = runtime.apply_settings(extra_output_devices=(2,),
                                  extra_input_devices=(0,))
    assert "got no stream of their own" not in said
    assert said.count("side device") == 1, "the config line already counts them"
    assert runtime.backend.side_devices() == 2
    runtime.close()


def test_a_run_that_opened_no_stream_remembers_the_page_and_opens_nothing(
        config):
    """``--no-audio`` is not overridden by a menu: the page waits, and says so."""
    runtime = AudioRuntime(config=config)

    said = runtime.apply_settings(sample_rate=96000, block_size=512)
    assert runtime.backend is None
    assert runtime.opened is False
    assert runtime.config.sample_rate == 96000 and runtime.config.block_size == 512
    assert said.startswith("audio is not open in this run")
    assert "96000 Hz, 512-frame blocks" in said
