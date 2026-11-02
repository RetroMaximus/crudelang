"""The audio layer's vocabulary: config, devices, meters, the block body.

These are the parts every backend shares, so they are tested once, here, instead
of once per adapter.
"""

from __future__ import annotations

import ctypes
import math

import pytest

from crude.audio.base import (
    DB_FLOOR,
    DEFAULT_BLOCK_SIZE,
    DEFAULT_SAMPLE_RATE,
    INFO_PROPS,
    INPUT_OVERFLOW,
    INPUT_UNDERFLOW,
    OUTPUT_OVERFLOW,
    OUTPUT_UNDERFLOW,
    AudioBackend,
    AudioConfig,
    AudioError,
    AudioUnavailable,
    DeviceInfo,
    RecordingRender,
    Taps,
    bytes_to_samples,
    copy_samples,
    read_samples,
    resolve_device,
    samples_to_bytes,
    status_text,
)
from crude.audio.null import NULL_DEVICE, NullBackend


# -- configuration ------------------------------------------------------


def test_the_defaults_are_pd_s_geometry():
    config = AudioConfig()
    assert config.sample_rate == DEFAULT_SAMPLE_RATE
    assert config.block_size == DEFAULT_BLOCK_SIZE
    assert (config.in_channels, config.out_channels) == (2, 2)


def test_a_manifest_hands_over_strings_and_gets_numbers_back():
    config = AudioConfig(sample_rate="48000", block_size="128", in_channels="1")
    assert config.sample_rate == 48000
    assert config.block_size == 128
    assert config.in_channels == 1
    assert isinstance(config.out_channels, int)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"sample_rate": 0}, "a sample rate must be positive, got 0"),
        ({"block_size": 0}, "a block size must be at least 1 frame, got 0"),
        ({"in_channels": 0, "out_channels": 0},
         "an audio stream needs at least one input or one output channel"),
        ({"sample_rate": "studio"}, "'studio' is not a number, as sample_rate must be"),
        ({"in_channels": None, "out_channels": None},
         "an audio stream needs at least one input or one output channel"),
    ],
)
def test_a_config_that_makes_no_sense_says_which_part(kwargs, message):
    with pytest.raises(AudioError) as caught:
        AudioConfig(**kwargs)
    assert str(caught.value) == message


def test_the_frame_sizes_are_the_block_multiplied_by_the_channels():
    config = AudioConfig(block_size=64, in_channels=2, out_channels=1)
    assert config.out_frame_size == 64
    assert config.in_frame_size == 128


def test_a_device_callback_may_arrive_with_any_frame_count():
    config = AudioConfig(block_size=64)
    assert config.ticks_for(64) == 1
    assert config.ticks_for(256) == 4
    # Shorter than one Pd tick is still one tick: libpd cannot render half a block.
    assert config.ticks_for(32) == 1


def test_changed_leaves_the_original_alone():
    config = AudioConfig(sample_rate=44100)
    other = config.changed(sample_rate=48000, input_device="Studio Capture")
    assert config.sample_rate == 44100
    assert other.sample_rate == 48000
    assert other.input_device == "Studio Capture"
    assert other.describe() == config.describe().replace("44100", "48000")


def test_describe_says_everything_a_log_line_needs():
    config = AudioConfig(sample_rate=48000, block_size=128, in_channels=1, out_channels=2)
    assert config.describe() == "48000 Hz, 128-frame blocks, 1 in / 2 out"
    assert str(config) == config.describe()
    assert config.as_dict()["out_channels"] == 2


# -- devices ------------------------------------------------------------


def test_sounddevice_and_pyaudio_spell_the_same_device_differently():
    from_sd = DeviceInfo.from_mapping(
        {"name": "Speakers", "index": 1, "max_input_channels": 0,
         "max_output_channels": 2, "default_samplerate": 48000.0,
         "hostapi": "Windows DirectSound"},
        source="sounddevice",
    )
    from_pa = DeviceInfo.from_mapping(
        {"name": "Speakers", "device_index": 1, "maxInputChannels": 0,
         "maxOutputChannels": 2, "defaultSampleRate": 48000.0,
         "hostApi": "Windows DirectSound"},
        source="pyaudio",
    )
    assert from_sd == from_pa
    assert from_sd.is_output and not from_sd.is_input
    assert not from_sd.is_duplex


def test_an_unusable_device_description_still_becomes_a_device():
    device = DeviceInfo.from_mapping({"name": "Mystery"}, source="sounddevice")
    assert device.index is None
    assert device.max_input_channels == 0
    assert device.default_sample_rate == DEFAULT_SAMPLE_RATE
    assert device.host_api == "sounddevice"
    assert DeviceInfo.from_mapping({}, source="").name == "unnamed device"


def test_directions_are_the_only_three_a_caller_can_ask_for():
    device = DeviceInfo("Both", index=2, max_input_channels=2, max_output_channels=2)
    assert device.supports("input") and device.supports("output")
    assert device.supports("duplex") and device.is_duplex
    with pytest.raises(AudioError, match="direction must be one of input, output, duplex"):
        device.supports("midi")


def test_describe_is_the_line_a_script_prints():
    device = DeviceInfo("Studio Capture", index=2, max_input_channels=8,
                        max_output_channels=8, default_sample_rate=44100,
                        host_api="MME")
    assert device.describe() == "[2] Studio Capture (in/out 8, 44100 Hz, MME)"
    assert DeviceInfo("Silence", max_output_channels=1).describe().startswith("Silence (out 1")


# -- sample plumbing ----------------------------------------------------


def test_read_samples_takes_whatever_the_library_handed_over():
    array = (ctypes.c_float * 4)(0.0, 0.25, -0.5, 1.0)
    assert read_samples(array) == [0.0, 0.25, -0.5, 1.0]
    assert read_samples([1, 2, 3], count=2) == [1.0, 2.0]
    assert read_samples(None) == []

    packed = samples_to_bytes([0.5, -0.25])
    assert read_samples(packed) == [0.5, -0.25]


def test_a_numpy_shaped_block_is_read_through_flat():
    block = TwoFrames([(0.1, 0.2), (0.3, 0.4)])
    assert read_samples(block, count=2) == [pytest.approx(0.1), pytest.approx(0.2)]
    assert read_samples(block)[-1] == pytest.approx(0.4)


def test_bytes_and_samples_round_trip():
    values = [0.0, 0.5, -1.0, 0.25]
    assert bytes_to_samples(samples_to_bytes(values)) == values


def test_copy_samples_never_runs_past_the_shorter_side():
    dest = (ctypes.c_float * 4)()
    source = (ctypes.c_float * 2)(1.0, 2.0)
    assert copy_samples(dest, source) == 2
    assert list(dest)[:2] == [1.0, 2.0]
    assert copy_samples(None, source) == 0
    assert copy_samples(dest, None) == 0


def test_a_two_dimensional_source_copies_through_flat_too():
    dest = (ctypes.c_float * 4)()
    assert copy_samples(dest, TwoFrames(((0.5, 0.5), (0.25, -0.25)))) == 4
    assert list(dest) == [0.5, 0.5, 0.25, -0.25]


class TwoFrames:
    """A numpy ``(frames, channels)`` block: iteration happens on ``.flat``."""

    def __init__(self, rows):
        self.flat = [value for row in rows for value in row]


def test_the_callback_flags_are_the_same_bits_in_both_libraries():
    assert status_text(0) == ""
    assert status_text(INPUT_UNDERFLOW) == "input underflow"
    assert status_text(INPUT_OVERFLOW | OUTPUT_UNDERFLOW) == "input overflow output underflow"
    assert status_text("Output Overflow") == "output overflow"


def test_an_unknown_flag_is_reported_as_the_number_it_is():
    assert status_text(16) == "16"


# -- metering -----------------------------------------------------------


def test_a_block_of_one_value_meters_exactly_that_value():
    taps = Taps(channels=2)
    taps.push([0.5] * 8, 2)
    assert taps.rms() == pytest.approx(0.5)
    assert taps.rms(0) == pytest.approx(0.5)
    assert taps.peak() == pytest.approx(0.5)
    assert taps.db() == pytest.approx(20 * math.log10(0.5))
    assert taps.blocks == 1


def test_the_channels_are_metered_apart_and_the_mix_is_for_the_scope():
    taps = Taps(channels=2)
    taps.push([1.0, 0.0, 0.5, 0.0], 2)  # frames: (1,0) and (0.5,0)
    assert taps.rms(0) == pytest.approx(math.sqrt((1.0 + 0.25) / 2))
    assert taps.rms(1) == 0.0
    assert taps.peak(0) == 1.0
    assert taps.scope() == [pytest.approx(0.5), pytest.approx(0.25)]
    assert taps.scope(1) == [pytest.approx(0.25)]
    assert taps.scope(0) == []
    assert taps.scope_capacity == 1024


def test_a_quiet_stream_is_silence_at_the_floor_not_minus_infinity():
    taps = Taps(channels=1)
    assert taps.rms() == 0.0
    assert taps.db() == DB_FLOOR


def test_clipping_is_counted_not_clamped():
    taps = Taps(channels=1)
    taps.push([1.2, -1.1, 0.5], 1)
    assert taps.clipped == 2
    assert taps.peak() == pytest.approx(1.2)


def test_a_peak_holds_until_the_runtime_ages_it():
    taps = Taps(channels=1, peak_decay=1.0)
    taps.push([0.9, 0.9], 1)
    taps.push([0.1, 0.1], 1)
    assert taps.peak() == pytest.approx(0.9)
    assert taps.rms() == pytest.approx(0.1)
    taps.decay(0.5)
    assert taps.peak() == pytest.approx(0.4)
    taps.decay(10.0)
    assert taps.peak() == 0.0


def test_the_scope_is_a_ring_buffer_that_forgets():
    taps = Taps(channels=1, scope_frames=4)
    taps.push([1.0, 2.0, 3.0, 4.0], 1)
    taps.push([5.0, 6.0], 1)
    assert taps.scope() == [3.0, 4.0, 5.0, 6.0]
    taps.reset()
    assert taps.scope() == []
    assert taps.blocks == 0
    assert taps.clipped == 0


def test_reading_a_channel_that_is_not_there_says_how_many_are():
    taps = Taps(channels=2)
    with pytest.raises(AudioError, match=r"channel 2 is out of range \(this stream has 2\)"):
        taps.rms(2)
    with pytest.raises(AudioError, match="out of range"):
        taps.scope(channel=5)


def test_an_empty_block_is_ignored_rather_than_dividing_by_zero():
    taps = Taps(channels=2)
    taps.push([], 2)
    assert taps.blocks == 0
    assert taps.rms() == 0.0


# -- the shared block body ----------------------------------------------


def make_backend(render=None, config=None, **kwargs):
    return NullBackend(config or AudioConfig(block_size=4, in_channels=2, out_channels=2),
                       render=render, **kwargs)


def test_consume_renders_counts_and_meters_one_block():
    render = RecordingRender(value=0.5)
    backend = make_backend(render)
    in_buffer, out_buffer = backend.allocate()
    assert backend.consume(in_buffer, out_buffer) == 8
    assert render.calls == [1]
    assert backend.blocks == 1
    assert backend.frames == 4
    assert backend.taps.rms() == pytest.approx(0.5)
    assert list(out_buffer) == [0.5] * 8


def test_a_paused_stream_never_reaches_the_renderer():
    render = RecordingRender(value=0.5)
    backend = make_backend(render)
    in_buffer, out_buffer = backend.allocate()
    out_buffer[0] = 1.0

    backend.pause()
    assert backend.consume(in_buffer, out_buffer) == 0
    assert render.calls == []
    assert list(out_buffer) == [0.0] * 8
    assert backend.silent_blocks == 1
    assert backend.blocks == 0

    backend.resume()
    backend.consume(in_buffer, out_buffer)
    assert render.calls == [1]


def test_with_nothing_to_render_the_block_is_still_silence():
    backend = make_backend(render=None)
    in_buffer, out_buffer = backend.allocate()
    assert backend.consume(in_buffer, out_buffer) == 0
    assert list(out_buffer) == [0.0] * 8


def test_the_input_is_metered_separately_from_the_output():
    seen = []

    def render(ticks, in_buffer, out_buffer):
        seen.append(list(in_buffer))
        for index in range(len(out_buffer)):
            out_buffer[index] = 0.0

    backend = make_backend(render)
    in_buffer, out_buffer = backend.allocate()
    for index in range(4):  # two of this block's four frames on channel 0
        in_buffer[index] = 1.0 if index % 2 == 0 else 0.0
    backend.consume(in_buffer, out_buffer)

    assert backend.taps.rms() == 0.0
    assert backend.input_taps.rms(0) == pytest.approx(1.0 / math.sqrt(2))
    assert backend.input_taps.rms(1) == 0.0
    assert backend.taps.blocks == 1
    assert backend.input_taps.blocks == 1
    assert seen == [[1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]]


def test_the_status_flags_are_tallied_by_direction():
    backend = make_backend()
    backend.counts_status(INPUT_OVERFLOW)
    assert (backend.xruns_input, backend.xruns_output, backend.dropped) == (1, 0, 1)
    backend.counts_status(OUTPUT_UNDERFLOW)
    assert (backend.xruns_input, backend.xruns_output, backend.dropped) == (1, 1, 2)
    backend.counts_status(OUTPUT_OVERFLOW)
    assert (backend.xruns_input, backend.xruns_output, backend.dropped) == (1, 2, 2)
    assert backend.xruns == 3
    assert backend.status_flags == ["input overflow", "output underflow",
                                    "output overflow"]
    # Nothing at all is not a flag: no counter moves.
    backend.counts_status(0)
    assert backend.xruns == 3


def test_priming_output_is_counted_as_nothing_much():
    backend = make_backend()
    backend.counts_status(16)
    assert backend.xruns == 0
    assert backend.status_flags == ["16"]


def test_a_flag_the_adapter_could_not_decode_is_still_logged():
    heard = []
    backend = make_backend(log=heard.append)
    backend.counts_status(INPUT_UNDERFLOW | OUTPUT_OVERFLOW)
    assert heard == ["audio null: input underflow output overflow"]


def test_the_block_buffers_are_reused_because_a_callback_must_not_allocate():
    backend = make_backend()
    first_in, first_out = backend.buffers_for(1)
    assert backend.buffers_for(1)[0] is first_in
    assert backend.buffers_for(2)[1] is not first_out
    assert len(backend.buffers_for(2)[1]) == 8 * 2
    assert len(backend.buffers_for(0)[1]) == 8  # a tick count is never less than one
    backend.close()
    assert backend.buffers_for(1)[0] is not first_in


def test_open_start_stop_close_are_idempotent_and_counted():
    backend = make_backend()
    backend.open()
    assert backend.opened and backend.device == NULL_DEVICE
    backend.start()
    backend.start()
    assert backend.running and backend.starts == 1
    backend.stop()
    backend.stop()
    assert not backend.running and backend.stops == 1
    backend.close()
    assert not backend.opened
    backend.close()


def test_a_backend_can_be_used_as_a_context_manager():
    with make_backend() as backend:
        assert backend.opened
    assert not backend.opened


def test_the_base_class_does_not_pretend_it_can_open_a_device():
    base = AudioBackend(AudioConfig(block_size=4))
    with pytest.raises(AudioError, match="base cannot be opened by the base class"):
        base.open()


def test_a_pull_based_backend_renders_whenever_the_caller_asks(config):
    """``process_block`` is the whole protocol a device-free backend needs.

    Nothing below is null-specific, which is what lets the runtime tick any
    silent backend - including one a test or a plugin made up.
    """
    class Pullable(AudioBackend):
        name = "pullable"
        live = False

        def _open(self):
            self.device = "pullable"

        def _close(self):
            pass

    backend = Pullable(config.changed(in_channels=0), render=RecordingRender(value=0.5))
    with pytest.raises(AudioError, match="pullable is not open; call AUDIO_START"):
        backend.process_block()

    backend.open()
    assert backend.process_block(2) == config.block_size * config.out_channels * 2
    # one call is one block, however many ticks it carried
    assert backend.blocks == 1 and backend.frames == 128
    assert backend.taps.rms() == pytest.approx(0.5)
    assert backend.input_taps.blocks == 0  # a stream with no input meters nothing
    backend.process_block(1)
    assert backend.blocks == 2 and backend.frames == 192
    backend.close()


def test_every_reported_property_can_be_read_and_none_can_be_written():
    backend = make_backend()
    backend.open()
    for name in INFO_PROPS:
        assert backend.get_prop(name) is not None
    assert backend.get_prop("running") == 0  # booleans are 1/0 for the script
    assert backend.get_prop("seconds") == 0.0
    assert backend.get_prop("info")["backend"] == "null"
    assert backend.get_prop("notes") == []

    with pytest.raises(KeyError):
        backend.get_prop("nonsense")
    with pytest.raises(KeyError):
        backend.set_prop("sample_rate", 8000)


def test_describe_mentions_the_state_and_the_counters():
    backend = make_backend()
    assert "audio null (closed)" in backend.describe()
    assert "44100 Hz" in backend.describe()
    backend.open()
    assert "audio null (open)" in str(backend)
    backend.start()
    assert "audio null (running)" in backend.describe()
    assert repr(backend).startswith("<null running=")


# -- device selection ---------------------------------------------------


DEVICES = [
    DeviceInfo("Microphone", index=0, max_input_channels=2),
    DeviceInfo("Speakers", index=1, max_output_channels=2),
    DeviceInfo("Studio Capture", index=2, max_input_channels=8, max_output_channels=8),
    DeviceInfo("USB Capture", index=3, max_input_channels=2),
]


def listed():
    return list(DEVICES)


def test_an_unset_selection_is_the_library_s_default():
    assert resolve_device(None, "output", listed) is None
    assert resolve_device("   ", "output", listed) is None


def test_a_number_is_taken_as_written():
    assert resolve_device(2, "input", listed) == 2
    assert resolve_device("2", "input", listed) == 2


def test_a_name_is_matched_exactly_then_as_a_substring():
    assert resolve_device("Speakers", "output", listed) == 1
    assert resolve_device("studio", "input", listed) == 2  # a case-insensitive hit
    assert resolve_device(DeviceInfo("Speakers", index=1), "output", listed) == 1


def test_a_device_that_cannot_be_opened_says_why():
    with pytest.raises(AudioUnavailable, match="no output device called 'ghost'"):
        resolve_device("ghost", "output", listed)
    with pytest.raises(AudioUnavailable, match="matches several input devices"):
        resolve_device("capture", "input", listed)
    with pytest.raises(AudioUnavailable, match="has no index to open"):
        resolve_device(DeviceInfo("Silent"), "input", listed)
    with pytest.raises(AudioUnavailable, match="cannot be True/False"):
        resolve_device(True, "input", listed)
    with pytest.raises(AudioUnavailable, match="cannot open an audio input device"):
        resolve_device(0.5, "input", listed)
