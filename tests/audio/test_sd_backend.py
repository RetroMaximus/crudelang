"""The default adapter: what ``sounddevice`` hands the audio layer.

The stub in :mod:`tests.audio.conftest` records the calls the adapter makes and
lets the test *be* the PortAudio thread, so the callback marshalling - the one
piece of this stage that cannot be rehearsed on a device - is covered here.
"""

from __future__ import annotations

import ctypes
import math

import pytest

from crude.audio.base import (
    AudioConfig,
    AudioError,
    AudioUnavailable,
    RecordingRender,
)
from crude.audio.sd_backend import SoundDeviceBackend, query_devices


# -- capability and discovery -------------------------------------------


def test_the_stub_module_is_all_this_needs_to_look_available(stub_sounddevice):
    stub_sounddevice()
    assert SoundDeviceBackend.available() is True


def test_a_machine_without_the_module_is_described_not_crashed(no_audio_libraries):
    assert SoundDeviceBackend.available() is False
    with pytest.raises(AudioUnavailable, match=r"sounddevice is not installed"):
        SoundDeviceBackend.devices()


def test_the_device_table_is_read_as_devices_not_as_dictionaries(stub_sounddevice):
    stub_sounddevice()
    listed = SoundDeviceBackend.devices()
    assert [device.name for device in listed] == [
        "Microphone (Loopback)", "Speakers (Loopback)", "Studio Capture",
    ]
    assert listed[0].max_input_channels == 2
    assert listed[1].is_output and not listed[1].is_input
    assert listed[2].default_sample_rate == 44100
    assert listed[2].host_api == "MME"


def test_a_direction_filter_leaves_only_what_a_device_can_actually_do(stub_sounddevice):
    stub = stub_sounddevice()
    assert [device.index for device in query_devices("input")] == [0, 2]
    assert [device.index for device in query_devices("output")] == [1, 2]
    assert [device.index for device in query_devices("duplex")] == [2]
    assert stub.query_calls == [None, None, None]  # the whole table, filtered here

    with pytest.raises(AudioError, match="direction must be one of"):
        query_devices("midi")


def test_a_library_that_cannot_answer_is_reported_as_unavailable(stub_sounddevice):
    stub = stub_sounddevice()

    def broken(kind=None):
        raise RuntimeError("PortAudio is not ready")

    stub.query_devices = broken
    with pytest.raises(AudioUnavailable, match="could not list devices"):
        SoundDeviceBackend.devices()


# -- opening ------------------------------------------------------------


def test_the_stream_is_asked_for_exactly_the_configuration(config, stub_sounddevice):
    stub_sounddevice()
    backend = SoundDeviceBackend(config)
    backend.open()

    assert backend.kind == "duplex"
    kwargs = backend._stream.kwargs
    assert kwargs["samplerate"] == 48000
    assert kwargs["blocksize"] == 64
    assert kwargs["channels"] == (2, 2)
    assert kwargs["dtype"] == "float32"
    assert kwargs["device"] == (None, None)
    assert kwargs["callback"] == backend.callback
    assert kwargs["finished_callback"] == backend._finished
    backend.close()


@pytest.mark.parametrize("config,kind,channels,callback", [
    (AudioConfig(in_channels=0, out_channels=2), "output", 2, "output_callback"),
    (AudioConfig(in_channels=1, out_channels=0), "input", 1, "input_callback"),
    (AudioConfig(in_channels=2, out_channels=2), "duplex", (2, 2), "callback"),
])
def test_a_stream_with_one_side_missing_is_not_a_duplex_stream(
        config, kind, channels, callback, stub_sounddevice):
    """PortAudio refuses a zero-channel direction on a duplex ``Stream``, so the
    shape - the class, the channel argument and the callback signature together -
    is decided from the configuration."""
    stub_sounddevice()
    backend = SoundDeviceBackend(config)
    backend.open()

    assert backend.kind == kind
    assert backend._stream.kind == kind
    assert backend._stream.kwargs["channels"] == channels
    assert backend._stream.kwargs["callback"] == getattr(backend, callback)
    backend.close()


def test_an_input_only_stream_names_the_device_it_listens_to(stub_sounddevice):
    stub_sounddevice()
    backend = SoundDeviceBackend(AudioConfig(in_channels=2, out_channels=0,
                                             input_device="studio"))
    backend.open()

    assert backend._stream.kind == "input"
    assert backend._stream.kwargs["device"] == 2  # one index, not a pair
    assert backend.device == "Studio Capture"
    backend.close()


def test_the_default_device_is_named_rather_than_guessed(stub_sounddevice):
    stub_sounddevice()
    backend = SoundDeviceBackend(AudioConfig(in_channels=0, out_channels=2))
    backend.open()
    assert backend.device == "Speakers (Loopback) (default)"
    backend.close()


@pytest.mark.parametrize("wanted,name", [
    (2, "Studio Capture"),
    ("2", "Studio Capture"),
    ("Studio Capture", "Studio Capture"),
    ("studio", "Studio Capture"),
])
def test_a_device_can_be_ask_for_by_index_or_by_name(wanted, name, stub_sounddevice):
    stub_sounddevice()
    config = AudioConfig(input_device=wanted, output_device=wanted)
    backend = SoundDeviceBackend(config)
    backend.open()
    assert backend.device == name
    assert backend._stream.kwargs["device"] == (2, 2)
    backend.close()


def test_a_device_that_is_not_there_is_explained_with_the_list(stub_sounddevice):
    stub_sounddevice()
    backend = SoundDeviceBackend(AudioConfig(output_device="Headphones"))
    with pytest.raises(AudioUnavailable, match=r"no output device called 'Headphones' "
                                               r"\(3 device\(s\) were listed\)"):
        backend.open()


def test_an_ambiguous_name_refuses_to_pick(config, stub_sounddevice, device_record):
    stub_sounddevice(devices=[
        device_record("Speakers (built-in)", 1, outputs=2),
        device_record("Speakers (dock)", 2, outputs=2),
    ])
    backend = SoundDeviceBackend(config.changed(output_device="speakers"))
    with pytest.raises(AudioUnavailable, match="matches several output devices"):
        backend.open()


def test_a_portaudio_refusal_becomes_a_fallback_request(stub_sounddevice):
    stub_sounddevice(open_error="Invalid device parameters for requested stream mode")
    backend = SoundDeviceBackend(AudioConfig(in_channels=8, out_channels=8))
    with pytest.raises(AudioUnavailable) as caught:
        backend.open()
    message = str(caught.value)
    assert message.startswith("sounddevice could not open 44100 Hz, 64-frame blocks")
    assert "Invalid device parameters" in message
    assert backend._stream is None
    assert backend.opened is False


# -- lifecycle ----------------------------------------------------------


def test_start_stop_and_close_reach_the_stream(config, stub_sounddevice):
    stub_sounddevice()
    backend = SoundDeviceBackend(config)
    backend.open()
    stream = backend._stream
    backend.start()
    assert stream.started and backend.running

    backend.stop()
    assert stream.stopped and not backend.running

    backend.close()
    assert stream.closed
    assert backend._stream is None and backend._out_buffer is None


def test_a_stream_that_refuses_to_start_says_so(stub_sounddevice):
    stub_sounddevice(fail_start="Device is busy")
    backend = SoundDeviceBackend()
    backend.open()
    with pytest.raises(AudioUnavailable, match="could not start the stream: Device is busy"):
        backend.start()
    backend.close()


# -- the callback -------------------------------------------------------


def test_a_block_is_marshalled_into_libpd_and_back(fake_buffer, stub_sounddevice):
    stub_sounddevice()
    seen = []

    def render(ticks, in_buffer, out_buffer):
        assert ticks == 1
        assert isinstance(in_buffer, ctypes.c_float * (4 * 2))  # this config's block
        seen.append(list(in_buffer))
        for index in range(len(out_buffer)):
            out_buffer[index] = 0.5

    backend = SoundDeviceBackend(AudioConfig(block_size=4, sample_rate=48000),
                                render=render)
    backend.open()

    indata = fake_buffer([0.25, -0.25, 0.5, -0.5, 0.0, 0.0, 0.0, 0.0])
    outdata = backend._stream.fire(indata=indata, frames=4, out_channels=2)

    assert seen == [[0.25, -0.25, 0.5, -0.5, 0.0, 0.0, 0.0, 0.0]]
    assert outdata.flat == [0.5] * 8
    assert backend.blocks == 1
    assert backend.frames == 4
    assert backend.taps.rms() == pytest.approx(0.5)
    # channel 0 carries 0.25, 0.5, 0, 0 over the block's four frames
    assert backend.input_taps.rms(0) == pytest.approx(
        math.sqrt((0.25 ** 2 + 0.5 ** 2) / 4))
    assert backend.input_taps.rms(1) == pytest.approx(
        math.sqrt((0.25 ** 2 + 0.5 ** 2) / 4))
    backend.close()


def test_a_larger_hardware_block_is_several_pd_ticks(fake_buffer, stub_sounddevice):
    stub_sounddevice()
    render = RecordingRender(value=0.25)
    backend = SoundDeviceBackend(AudioConfig(block_size=64), render=render)
    backend.open()

    backend._stream.fire(indata=fake_buffer([0.0] * 256), frames=128, out_channels=2)
    assert render.calls == [2]  # 128 frames is two of Pd's 64-frame blocks
    assert backend.frames == 128
    backend.close()


def test_an_output_only_stream_does_not_reach_for_an_input(config, stub_sounddevice):
    stub_sounddevice()
    render = RecordingRender(value=0.5)
    backend = SoundDeviceBackend(config.changed(in_channels=0, out_channels=2),
                                 render=render)
    backend.open()

    outdata = backend._stream.fire(frames=64, out_channels=2)
    assert render.calls == [1]
    assert outdata.flat == [0.5] * 128
    backend.close()


def test_an_input_only_stream_has_nothing_to_copy_back(fake_buffer, stub_sounddevice):
    stub_sounddevice()
    seen = []

    def render(ticks, in_buffer, out_buffer):
        seen.append(list(in_buffer))
        for index in range(len(out_buffer)):
            out_buffer[index] = 0.5  # nobody is playing this back

    backend = SoundDeviceBackend(AudioConfig(in_channels=2, out_channels=0,
                                             block_size=4), render=render)
    backend.open()
    assert backend._stream.fire(indata=fake_buffer([0.25, -0.25, 0.5, -0.5, 0, 0, 0, 0]),
                                frames=4) is None

    assert seen == [[0.25, -0.25, 0.5, -0.5, 0.0, 0.0, 0.0, 0.0]]
    assert backend.blocks == 1
    assert backend.input_taps.rms(0) == pytest.approx(
        math.sqrt((0.25 ** 2 + 0.5 ** 2) / 4))
    backend.close()


def test_the_buffers_a_callback_uses_are_never_allocated_in_it(fake_buffer,
                                                               stub_sounddevice):
    stub_sounddevice()
    seen = []

    def render(ticks, in_buffer, out_buffer):
        seen.append(id(out_buffer))

    backend = SoundDeviceBackend(AudioConfig(block_size=64), render=render)
    backend.open()
    for _ in range(5):
        backend._stream.fire(indata=fake_buffer([0.0] * 128), frames=64)
    backend.close()

    assert len(seen) == 5
    assert len(set(seen)) == 1


def test_xruns_and_drops_are_counted_from_the_flags_the_device_sets(fake_buffer,
                                                                    stub_sounddevice):
    stub_sounddevice()
    backend = SoundDeviceBackend(AudioConfig(block_size=64),
                                 render=RecordingRender(value=0.0))
    backend.open()
    backend._stream.fire(indata=fake_buffer([0.0] * 128), frames=64, status=1)  # in under
    backend._stream.fire(indata=fake_buffer([0.0] * 128), frames=64, status=2)  # in over
    backend._stream.fire(indata=fake_buffer([0.0] * 128), frames=64, status=4)  # out under
    backend._stream.fire(indata=fake_buffer([0.0] * 128), frames=64, status=8)  # out over

    assert (backend.xruns_input, backend.xruns_output) == (2, 2)
    assert backend.dropped == 2
    assert backend.status_flags == ["input underflow", "input overflow",
                                    "output underflow", "output overflow"]
    assert backend.blocks == 4  # a flag never stops the block from rendering
    backend.close()


def test_a_paused_stream_hands_the_device_silence_and_skips_libpd(fake_buffer,
                                                                  stub_sounddevice):
    stub_sounddevice()
    render = RecordingRender(value=0.5)
    backend = SoundDeviceBackend(AudioConfig(block_size=4), render=render)
    backend.open()
    backend.pause()

    outdata = backend._stream.fire(indata=fake_buffer([0.5] * 8), frames=4, out_channels=2)
    assert render.calls == []
    assert outdata.flat == [0.0] * 8
    assert backend.silent_blocks == 1
    backend.close()


def test_finishing_early_is_harmless(stub_sounddevice):
    stub_sounddevice()
    backend = SoundDeviceBackend()
    backend.open()
    assert backend._finished() is None
    backend.close()


def test_describe_names_the_backend_the_state_and_the_device(config, stub_sounddevice):
    stub_sounddevice()
    backend = SoundDeviceBackend(config)
    assert "audio sounddevice (closed)" in backend.describe()
    backend.open()
    assert "audio sounddevice (open) on 'Speakers (Loopback) (default)'" in backend.describe()
    assert "48000 Hz, 64-frame blocks, 2 in / 2 out" in backend.describe()
    backend.start()
    assert "(running)" in backend.describe()
    backend.close()
