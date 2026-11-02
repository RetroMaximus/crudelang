"""The optional adapter: ``pyaudio``, and the shapes only it uses.

PyAudio is the fallback for machines where ``sounddevice`` cannot work, and it is
a stricter host: bytes instead of arrays, one stream for the duplex pair, and no
default device list at all. The stub installed by ``stub_pyaudio()`` is the only
way any of this can be exercised - there is no wheel for this Python.
"""

from __future__ import annotations

import ctypes

import pytest

from crude.audio.base import (
    AudioConfig,
    AudioError,
    AudioUnavailable,
    RecordingRender,
    bytes_to_samples,
    samples_to_bytes,
)
from crude.audio.pa_backend import PyAudioBackend, query_devices


# -- capability and discovery -------------------------------------------


def test_the_module_is_optional_in_every_sense(stub_pyaudio):
    assert PyAudioBackend.available() is False
    assert PyAudioBackend.optional is True
    with pytest.raises(AudioUnavailable, match=r"pyaudio is not installed"):
        PyAudioBackend.devices()

    stub_pyaudio()
    assert PyAudioBackend.available() is True


def test_the_pyaudio_device_spelling_becomes_the_same_device_as_any_other(
        stub_pyaudio):
    stub = stub_pyaudio()
    listed = PyAudioBackend.devices()
    assert [device.name for device in listed] == [
        "Microphone (Loopback)", "Speakers (Loopback)", "Studio Capture",
    ]
    assert listed[2].max_input_channels == 2
    assert listed[2].default_sample_rate == 44100
    assert listed[0].host_api == "0"
    # Enumerating must not leave PortAudio initialised behind.
    assert stub.instances[0].terminated is True


def test_a_direction_filter_is_refused_before_the_library_is_asked(stub_pyaudio):
    stub_pyaudio()
    assert [d.index for d in query_devices("input")] == [0, 2]
    with pytest.raises(AudioError, match="direction must be one of"):
        query_devices("sideways")


def test_a_half_described_device_is_listed_anyway(stub_pyaudio):
    stub = stub_pyaudio(devices=[{"name": "Mystery", "index": 0}])
    device = query_devices()[0]
    assert device.name == "Mystery"
    assert not device.is_input and not device.is_output
    assert stub.instances[0].terminated is True


# -- opening ------------------------------------------------------------


def test_the_stream_is_opened_as_float_at_the_configured_rate(config, stub_pyaudio):
    stub_pyaudio()
    backend = PyAudioBackend(config)
    backend.open()

    kwargs = backend._stream.kwargs
    assert kwargs["format"] == 1  # paFloat32
    assert kwargs["channels"] == 2
    assert kwargs["rate"] == 48000.0
    assert kwargs["frames_per_buffer"] == 64
    assert kwargs["input"] is True and kwargs["output"] is True
    assert kwargs["stream_callback"] == backend.callback
    assert backend.device == "pyaudio default (2 ch)"
    backend.close()


def test_split_channel_counts_are_not_this_backends_to_offer(stub_pyaudio):
    stub_pyaudio()
    backend = PyAudioBackend(AudioConfig(in_channels=2, out_channels=8))
    with pytest.raises(AudioUnavailable, match="matching input and output channel counts"):
        backend.open()
    assert backend._pa is None  # the instance was never even created


def test_an_input_only_stream_asks_for_input_only(stub_pyaudio):
    stub_pyaudio()
    backend = PyAudioBackend(AudioConfig(in_channels=2, out_channels=0, block_size=32))
    backend.open()
    assert backend._stream.kwargs["input"] is True
    assert backend._stream.kwargs["output"] is False
    assert backend._stream.kwargs["channels"] == 2
    backend.close()


@pytest.mark.parametrize("wanted,name", [
    (2, "Studio Capture"),
    ("Studio Capture", "Studio Capture"),
    ("studio", "Studio Capture"),
])
def test_a_named_device_becomes_an_index_on_both_sides(wanted, name, stub_pyaudio):
    stub_pyaudio()
    backend = PyAudioBackend(AudioConfig(input_device=wanted, output_device=wanted))
    backend.open()
    assert backend._stream.kwargs["input_device_index"] == 2
    assert backend._stream.kwargs["output_device_index"] == 2
    assert backend.device == name
    backend.close()


def test_a_device_that_will_not_open_is_a_request_to_fall_back(stub_pyaudio):
    stub = stub_pyaudio(open_error="Device is already in use")
    backend = PyAudioBackend()
    with pytest.raises(AudioUnavailable) as caught:
        backend.open()
    assert str(caught.value).startswith("pyaudio could not open 44100 Hz")
    assert "Device is already in use" in str(caught.value)
    assert stub.instances[0].terminated is True


# -- lifecycle ----------------------------------------------------------


def test_start_stop_and_close_also_release_the_library(stub_pyaudio):
    stub_pyaudio()
    backend = PyAudioBackend()
    backend.open()
    stream, instance = backend._stream, backend._pa

    backend.start()
    assert stream.started and backend.running
    backend.stop()
    assert stream.stopped
    backend.close()

    assert stream.closed and instance.terminated
    assert backend._stream is None and backend._pa is None


def test_a_stream_that_refuses_to_start_says_so(stub_pyaudio):
    stub_pyaudio(fail_start="Device is busy")
    backend = PyAudioBackend()
    backend.open()
    with pytest.raises(AudioUnavailable, match="could not start the stream: Device is busy"):
        backend.start()
    backend.close()


# -- the callback -------------------------------------------------------


def test_bytes_go_in_and_bytes_come_back(stub_pyaudio):
    stub_pyaudio()
    seen = []

    def render(ticks, in_buffer, out_buffer):
        assert isinstance(in_buffer, ctypes.c_float * 8)
        seen.append(list(in_buffer))
        for index in range(len(out_buffer)):
            out_buffer[index] = 0.5

    backend = PyAudioBackend(AudioConfig(block_size=4), render=render)
    backend.open()

    payload = samples_to_bytes([0.25, -0.25, 0.5, -0.5, 0.0, 0.0, 0.0, 0.0])
    out_bytes, keep_going = backend._stream.fire(in_data=payload, frame_count=4)

    assert seen == [[0.25, -0.25, 0.5, -0.5, 0.0, 0.0, 0.0, 0.0]]
    assert keep_going == 0  # paContinue
    assert bytes_to_samples(out_bytes) == [0.5] * 8
    assert backend.blocks == 1 and backend.frames == 4
    assert backend.taps.rms() == pytest.approx(0.5)
    backend.close()


def test_a_hardware_block_larger_than_pd_s_is_several_ticks(stub_pyaudio):
    stub_pyaudio()
    render = RecordingRender(value=0.25)
    backend = PyAudioBackend(AudioConfig(block_size=64), render=render)
    backend.open()

    out_bytes, _ = backend._stream.fire(in_data=b"", frame_count=128)
    assert render.calls == [2]
    assert bytes_to_samples(out_bytes) == [0.25] * 256
    backend.close()


def test_the_flags_arrive_as_plain_ints_here(stub_pyaudio):
    stub_pyaudio()
    backend = PyAudioBackend(AudioConfig(block_size=64),
                             render=RecordingRender(value=0.0))
    backend.open()
    backend._stream.fire(in_data=b"", frame_count=64, status=2)   # input overflow
    backend._stream.fire(in_data=b"", frame_count=64, status=4)   # output underflow

    assert (backend.xruns_input, backend.xruns_output, backend.dropped) == (1, 1, 2)
    assert backend.status_flags == ["input overflow", "output underflow"]
    backend.close()


def test_a_paused_stream_renders_silence_into_the_device_buffer(stub_pyaudio):
    stub_pyaudio()
    render = RecordingRender(value=0.5)
    backend = PyAudioBackend(AudioConfig(block_size=4), render=render)
    backend.open()
    backend.pause()

    out_bytes, keep_going = backend._stream.fire(in_data=b"", frame_count=4)
    assert render.calls == []
    assert bytes_to_samples(out_bytes) == [0.0] * 8
    assert keep_going == 0
    backend.close()


def test_describe_names_the_backend_the_state_and_the_device(config, stub_pyaudio):
    stub_pyaudio()
    backend = PyAudioBackend(config)
    assert "audio pyaudio (closed)" in backend.describe()
    backend.open()
    assert "audio pyaudio (open) on 'pyaudio default (2 ch)'" in backend.describe()
    assert "48000 Hz, 64-frame blocks, 2 in / 2 out" in backend.describe()
    backend.start()
    assert "(running)" in backend.describe()
    backend.close()
