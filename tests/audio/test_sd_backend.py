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
    read_samples,
    samples_to_bytes,
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


# -- the settings page: more than one device, and the latency preference --


def test_an_extra_output_device_gets_its_own_stream_and_the_same_block(
        stub_sounddevice):
    """Two speakers are two streams and one render: the block is copied, not
    computed twice, and the copy is a ``write``, never a second callback."""
    stub = stub_sounddevice()
    backend = SoundDeviceBackend(AudioConfig(in_channels=0, out_channels=2,
                                             output_device="Speakers",
                                             output_devices=[2]))
    backend.open()

    assert len(stub.streams) == 2, "the primary stream plus one side"
    side = backend._side_streams[0]
    assert side.kind == "output"
    assert side.kwargs["device"] == 2
    assert side.kwargs["channels"] == 2
    assert side.callback is None, "a side stream has no renderer of its own"
    assert backend.side_devices() == 1

    outdata = backend._stream.fire(frames=64, out_channels=2)
    assert side.written == [outdata], "the rendered block, handed to the second device"

    backend.start()
    assert side.started and backend.running
    backend.stop()
    assert side.stopped
    backend.close()
    assert side.closed and backend._side_streams == []


def test_a_second_toggle_for_the_device_already_playing_opens_no_side_stream(
        stub_sounddevice):
    """A device that is already the stream's own is not opened under it twice."""
    stub_sounddevice()
    backend = SoundDeviceBackend(AudioConfig(in_channels=0, out_channels=2,
                                             output_device=2, output_devices=[2]))
    backend.open()

    assert backend._side_streams == []
    assert backend.side_devices() == 0
    backend.close()


def test_a_side_output_that_will_not_open_fails_the_whole_request(stub_sounddevice):
    """A silently missing speaker is a lie, so the page is refused out loud."""
    stub = stub_sounddevice()
    real = stub.OutputStream

    def maker(**kwargs):
        if kwargs.get("device") == 2:
            raise RuntimeError("Device is busy")
        return real(**kwargs)

    stub.OutputStream = maker
    backend = SoundDeviceBackend(AudioConfig(in_channels=0, out_channels=2,
                                             output_devices=[2]))
    with pytest.raises(AudioUnavailable,
                       match="could not open side output 2: Device is busy"):
        backend.open()
    assert backend._side_streams == []


def test_an_extra_input_device_is_mixed_into_the_block_libpd_reads(
        fake_buffer, stub_sounddevice):
    """Two microphones, one input: the side stream's callback fills a buffer and
    the render callback adds it, so the session still hears one block."""
    stub_sounddevice()
    seen = []

    def render(ticks, in_buffer, out_buffer):
        seen.append(list(in_buffer))

    backend = SoundDeviceBackend(AudioConfig(block_size=4, in_channels=2,
                                             out_channels=2, input_device=2,
                                             input_devices=[0]), render=render)
    backend.open()

    assert len(backend._side_inputs) == 1
    side, _block = backend._side_inputs[0]
    assert side.kind == "input"
    assert side.kwargs["device"] == 0
    assert side.kwargs["channels"] == 2
    assert side.callback is not None, "a microphone only speaks when called"
    assert backend.side_devices() == 1

    side.fire(indata=fake_buffer([0.125] * 8), frames=4)
    backend._stream.fire(indata=fake_buffer([0.25] * 8), frames=4)
    assert seen == [[0.375] * 8], "one block, both devices"

    backend.close()
    assert side.closed and backend._side_inputs == []


def test_the_delay_the_page_asked_for_is_the_latency_the_stream_gets(
        config, stub_sounddevice):
    stub_sounddevice()
    backend = SoundDeviceBackend(config.changed(delay_ms=25.0, callbacks=False))
    backend.open()

    assert backend._stream.kwargs["latency"] == 0.025
    backend.close()


def test_the_callback_switch_picks_which_thread_pulls_the_block(
        config, stub_sounddevice):
    """On is PortAudio's thread calling the renderer; off is crude's own loop.

    The switch decides the shape of the stream, not a word in a latency field:
    the library has no idea what ``"callback"`` means as a timing target and
    answers ``must be real number, not str``, which is exactly how a first run of
    the metronome example ended up with a slider and no sound.
    """
    stub_sounddevice()
    backend = SoundDeviceBackend(config.changed(callbacks=True))
    backend.open()
    assert backend.polled is False
    assert backend._stream.raw is False, "a callback stream, not a Raw one"
    assert backend._stream.kwargs["callback"] == backend.callback
    assert "latency" not in backend._stream.kwargs, "no delay, no preference"
    backend.close()

    backend = SoundDeviceBackend(config.changed(callbacks=False))
    backend.open()
    assert backend.polled is True
    assert backend._stream.raw is True, "a Raw stream: nothing moves unless asked"
    assert backend._stream.callback is None, "a callback here would be the switch lying"
    backend.close()


def test_a_timing_target_the_library_cannot_hold_is_refused_on_the_way_in(
        config, stub_sounddevice):
    """The crash the stub now guards: a word where ctypes wants a double.

    ``must be real number, not str`` is what PortAudio's own argument check
    answers with, and it reaches a person as "no audio backend could be opened" -
    a fake that recorded whatever it was handed would have let that ship.
    """
    stub = stub_sounddevice()
    with pytest.raises(TypeError, match="must be a real number, not str"):
        stub.OutputStream(channels=2, samplerate=48000, blocksize=64,
                          dtype="float32", latency="callback")

    backend = SoundDeviceBackend(config.changed(delay_ms=0.0, callbacks=True))
    backend.open()
    assert "latency" not in backend._stream.kwargs
    backend.close()


def test_a_polled_stream_renders_the_block_it_reads_and_writes_the_one_it_made(
        stub_sounddevice):
    """With no callback the pump is the audio thread: bytes in, Pd, bytes out."""
    stub_sounddevice()
    seen = []

    def render(ticks, in_buffer, out_buffer):
        seen.append(list(in_buffer))
        for index in range(len(out_buffer)):
            out_buffer[index] = 0.5

    backend = SoundDeviceBackend(AudioConfig(block_size=4, in_channels=2,
                                             out_channels=2, callbacks=False),
                                 render=render)
    backend.open()
    stream = backend._stream
    stream.push(samples_to_bytes([0.25] * 8))

    backend._polled_block(4)

    assert seen == [[0.25] * 8], "the block it asked for is the block Pd saw"
    assert read_samples(stream.written[0]) == [0.5] * 8
    assert backend.blocks == 1 and backend.frames == 4
    backend.close()


def test_a_polled_stream_counts_what_the_device_reports_back(stub_sounddevice):
    """A blocking read/write is the only place an xrun gets told, so it is heard.

    A callback is handed a status flag; a polled call simply answers that the
    buffer ran over or dry. Both have to reach the same counters, or the same
    fault would be visible in one mode and invisible in the other.
    """
    stub_sounddevice()
    backend = SoundDeviceBackend(AudioConfig(block_size=4, in_channels=2,
                                             out_channels=2, callbacks=False),
                                 render=RecordingRender(value=0.25))
    backend.open()
    stream = backend._stream

    stream.overflow = True
    stream.push(samples_to_bytes([0.0] * 8))
    backend._polled_block(4)
    assert backend.xruns_input == 1

    stream.overflow = False
    stream.underflow = True
    stream.push(samples_to_bytes([0.0] * 8))
    backend._polled_block(4)
    assert backend.xruns_output == 1
    assert backend.dropped == 2, "both are samples lost"
    assert "input overflow" in backend.status_flags[0]
    backend.close()


def test_a_polled_stream_reads_a_side_microphone_on_the_same_beat(
        stub_sounddevice):
    """Without callbacks the sides are polled too - nothing in the show is in a
    callback, and two microphones still arrive as one block."""
    stub_sounddevice()
    seen = []

    def render(ticks, in_buffer, out_buffer):
        seen.append(list(in_buffer))

    backend = SoundDeviceBackend(AudioConfig(block_size=4, in_channels=2,
                                             out_channels=0, input_device=2,
                                             input_devices=[0], callbacks=False),
                                 render=render)
    backend.open()

    side, _block = backend._side_inputs[0]
    assert side.raw is True, "the switch applies to every device, not just the first"
    assert side.callback is None
    assert backend.side_devices() == 1

    backend._stream.push(samples_to_bytes([0.25] * 8))
    side.push(samples_to_bytes([0.125] * 8))
    backend._polled_block(4)

    assert seen == [[0.375] * 8], "one block, both microphones"
    backend.close()


def test_the_polling_thread_is_woken_by_the_stream_it_is_reading(
        config, stub_sounddevice):
    """``stop()`` has to end a read that is parked on the device, not outlive it.

    A polled stream is a thread waiting on hardware, so the shutdown is the part
    that has to be proved: the thread is asked to leave, the stream is stopped
    under it, and the join waits for it to have gone - with a read that came back
    because *we* stopped it deliberately not logged as a device fault.
    """
    stub_sounddevice()
    backend = SoundDeviceBackend(config.changed(callbacks=False))
    backend.open()
    stream = backend._stream
    assert stream.blocking, "a Raw stream is paced by the device, not by a loop"

    backend.start()
    assert backend._pump_thread is not None, "a polled stream needs a thread to pull"

    backend.stop()
    assert backend._pump_thread is None, "the shutdown waited for the thread"
    assert backend.notes == [], "a stop we asked for is not a fault"
    backend.close()


def test_a_polling_thread_a_device_refuses_says_so_and_leaves(stub_sounddevice):
    """A dead device must not spin, and must not be quiet about it either."""
    stub_sounddevice()
    backend = SoundDeviceBackend(AudioConfig(block_size=4, in_channels=2,
                                             out_channels=2, callbacks=False))
    backend.open()
    stream = backend._stream

    def gone(frames):
        raise RuntimeError("device is gone")

    stream.read = gone
    backend._pump()  # returns, because the loop ends on the first refused block

    assert backend.notes == ["audio: the polling thread stopped - device is gone"]
    assert stream.written == [], "nothing was handed to a device that refused to speak"
    backend.close()


def test_the_latency_preference_is_asked_of_the_side_devices_too(stub_sounddevice):
    """Every device in a show runs on one clock, or they drift apart."""
    stub_sounddevice()
    backend = SoundDeviceBackend(AudioConfig(in_channels=0, out_channels=2,
                                             output_devices=[2], delay_ms=12.5))
    backend.open()

    assert backend._stream.kwargs["latency"] == 0.0125
    assert backend._side_streams[0].kwargs["latency"] == 0.0125
    backend.close()
