"""Recording the input: the capture buffer, the callback that fills it, the runtime.

The claim this suite pins is the one the plan made about a take: it is *kept*,
where a scope window is thrown away, and the frames past the end of the buffer are
counted rather than lost in silence. Everything runs on the pulled backend with a
``fill`` standing in for a microphone, so the numbers are decided by the test.
"""

from __future__ import annotations

import pytest

from crude.audio import AudioConfig, NullBackend, RecordingRender
from crude.audio.base import AudioError, Capture
from crude.audio.runtime import AudioRuntime

#: what a "microphone" would hand a stereo block: the two sides, told apart
LEFT, RIGHT = 0.5, -0.25


def stereo(buffer) -> None:
    """Fill one interleaved block so channel 0 and channel 1 differ."""
    for index in range(len(buffer)):
        buffer[index] = LEFT if index % 2 == 0 else RIGHT


def flat(value: float):
    def _fill(buffer) -> None:
        for index in range(len(buffer)):
            buffer[index] = value

    return _fill


@pytest.fixture
def config():
    return AudioConfig(sample_rate=48000, block_size=64, in_channels=2,
                       out_channels=2)


@pytest.fixture
def backend(config):
    stream = NullBackend(config, render=RecordingRender(value=0.1), fill=stereo)
    stream.open()
    yield stream
    stream.close()


@pytest.fixture
def runtime(backend, config):
    """A runtime over a pulled stream that has something to record."""
    built = AudioRuntime(config=config, backend=backend)
    yield built
    built.close()


# -- the buffer ---------------------------------------------------------


def test_a_capture_is_silent_until_it_is_armed():
    box = Capture(2)
    assert box.armed is False
    assert box.frames == 0
    assert box.samples() == []
    # pushing into a disarmed capture is not how a take starts
    assert box.push([0.5, 0.5], 1) == 0


def test_arming_allocates_the_buffer_it_was_asked_for():
    box = Capture(2)
    assert box.start(100, 0, rate=48000) == 100
    assert box.capacity == 100
    assert box.armed is True
    assert box.rate == 48000


def test_one_channel_of_a_stereo_block_is_the_one_that_was_asked_for():
    box = Capture(2)
    box.start(10, 1, rate=48000)
    taken = box.push([LEFT, RIGHT] * 3, 2)

    assert taken == 3
    assert box.samples() == [RIGHT] * 3


def test_a_full_buffer_counts_what_it_dropped_instead_of_stalling():
    """The plan's rule for a take that ran longer than its seconds.

    Growing a list inside a device callback is not an option, and refusing the
    block would be worse: the frames past the end are counted, and the script can
    say that the last part of the take never arrived.
    """
    box = Capture(1)
    box.start(4, 0, rate=8000)
    assert box.push([0.1] * 3, 1) == 3
    assert box.push([0.2] * 3, 1) == 1

    assert box.frames == 4
    assert box.overflow == 2
    # the buffer is float32 - the width a device callback carries - so a stored
    # 0.1 comes back as the nearest single-precision number to it
    assert box.samples() == pytest.approx([0.1, 0.1, 0.1, 0.2])


def test_a_take_is_kept_at_the_width_a_device_carries():
    """float32 is what a callback hands over, so nothing is invented on the way in.

    Doubles would store the same sound in twice the memory: a half-second at
    48 kHz is 24000 floats either way, and the 24th bit of a microphone sample is
    not the difference anyone can hear.
    """
    box = Capture(1)
    box.start(2, 0, rate=8000)
    box.push([1.0 / 3, -1.0 / 3], 1)

    assert box.samples()[0] != 1.0 / 3
    assert box.samples()[0] == pytest.approx(1.0 / 3, abs=1e-7)


def test_stopping_keeps_the_take_and_takes_no_more():
    box = Capture(1)
    box.start(8, 0, rate=8000)
    box.push([1.0] * 4, 1)
    assert box.stop() == 4
    assert box.armed is False
    assert box.push([1.0] * 4, 1) == 0
    assert box.samples() == [1.0] * 4


def test_recording_again_starts_from_zero():
    box = Capture(1)
    box.start(8, 0, rate=8000)
    box.push([1.0] * 4, 1)
    box.stop()

    box.start(2, 0, rate=8000)
    assert box.frames == 0
    assert box.overflow == 0
    assert box.samples() == []


def test_resetting_forgets_the_take_as_well_as_arming_it():
    box = Capture(1)
    box.start(8, 0, rate=8000)
    box.push([1.0, 1.0], 1)
    box.reset()

    assert box.frames == 0
    assert box.capacity == 0
    assert box.samples() == []


def test_a_channel_the_stream_does_not_have_is_named_by_the_refusal():
    box = Capture(2)
    with pytest.raises(AudioError, match="cannot record channel 3"):
        box.start(100, 3)
    with pytest.raises(AudioError, match="this stream has 2 input channel"):
        box.start(100, -1)


def test_the_seconds_a_take_took_come_from_the_rate_it_was_armed_at():
    box = Capture(1)
    box.start(9600, 0, rate=48000)
    box.push([0.0] * 4800, 1)
    assert box.seconds == pytest.approx(0.1)
    # a capture with no rate says how many frames it has, not a made-up duration
    bare = Capture(1)
    bare.start(10, 0)
    assert bare.seconds == 0.0


def test_a_capture_describes_itself_the_way_a_report_would_want():
    box = Capture(2)
    assert box.as_dict() == {
        "armed": False, "channel": 0, "frames": 0, "capacity": 0, "overflow": 0,
        "rate": 0,
    }
    box.start(10, 1, rate=44100)
    box.push([0.0] * 4, 2)
    assert box.describe() == "capture armed: channel 1, 2 frame(s) of 10"


# -- the stream that feeds it ------------------------------------------


def test_an_unarmed_stream_feeds_the_capture_no_at_all(backend):
    backend.process_block(2)
    assert backend.capture.frames == 0
    assert backend.capture.armed is False


def test_the_callback_hands_the_input_to_the_capture_whole(backend):
    backend.capture.start(1000, 0, rate=48000)
    backend.process_block(1)

    assert backend.capture.frames == 64  # one block, one frame per stereo pair
    assert backend.capture.samples() == [LEFT] * 64


def test_a_second_channel_is_the_other_half_of_the_same_block(backend):
    backend.capture.start(1000, 1, rate=48000)
    backend.process_block(1)
    assert backend.capture.samples() == [RIGHT] * 64


def test_a_take_that_outruns_its_seconds_reports_the_loss(backend):
    backend.capture.start(100, 0, rate=48000)
    backend.process_block(3)

    assert backend.capture.frames == 100
    assert backend.capture.overflow == 3 * 64 - 100
    assert backend.capture.armed is True  # filling up is not stopping


def test_stopping_a_take_from_the_script_side_takes_it_off_the_stream(backend):
    backend.capture.start(1000, 0, rate=48000)
    backend.process_block(1)
    backend.capture.stop()
    backend.process_block(1)

    assert backend.capture.frames == 64


# -- the runtime the bindings call ------------------------------------


def test_arming_through_the_runtime_uses_the_streams_own_rate(runtime, backend):
    info = runtime.record_start(0.5, 1)

    assert info["capacity"] == 24000  # half a second at 48000
    assert info["channel"] == 1
    assert info["armed"] is True
    assert info["rate"] == 48000
    assert backend.capture.armed is True


def test_a_pulled_stream_says_it_is_not_running_rather_than_pretending(
        runtime, config):
    """``AUDIO_RECORD_START`` on a stopped live stream would capture silence.

    The runtime is pulled here, so there is no thread to start - and the note is
    the price of not being able to tell the two cases apart from the call alone.
    """
    info = runtime.record_start(0.25)
    assert "the stream is not running" in " ".join(runtime.notes)
    assert info["frames"] == 0


def test_the_frames_a_take_captured_are_the_frames_the_stream_carried(runtime):
    runtime.record_start(0.1)
    runtime.tick(2)
    frames = runtime.record_samples()

    assert runtime.record_stop() == 128
    assert frames == [LEFT] * 128
    assert runtime.record_info()["armed"] is False
    # stopping twice is not an error and keeps the take readable
    assert runtime.record_stop() == 128
    assert runtime.record_samples() == [LEFT] * 128


def test_a_take_needs_some_seconds_to_be_a_take(runtime):
    with pytest.raises(AudioError, match="has to be for some time"):
        runtime.record_start(0)


def test_a_stream_with_no_input_has_nothing_to_record_and_says_why(config):
    stream = NullBackend(AudioConfig(sample_rate=48000, in_channels=0,
                                     out_channels=2),
                         render=RecordingRender(value=0.1))
    stream.open()
    quiet = AudioRuntime(config=stream.config, backend=stream)

    with pytest.raises(AudioError, match="no input channels"):
        quiet.record_start(1.0)
    stream.close()


def test_a_runtime_holding_no_device_answers_like_a_silent_meter():
    """The plan's rule for a ``--no-audio`` run: readings are zeros, not errors.

    A recorder widget that reads ``frames`` every frame must not raise, trip the
    handler-mute budget and freeze the window on a machine with no audio.
    """
    quiet = AudioRuntime(config=AudioConfig(sample_rate=44100))

    assert quiet.record_info() == {
        "armed": False, "channel": 0, "frames": 0, "capacity": 0,
        "overflow": 0, "rate": 44100, "seconds": 0.0,
    }
    assert quiet.record_samples() == []
    assert quiet.record_stop() == 0
    with pytest.raises(AudioError, match="audio is not open"):
        quiet.record_start(1.0)


def test_an_armed_capture_survives_a_backend_close(backend):
    backend.capture.start(100, 0, rate=48000)
    backend.process_block(1)
    frames = backend.capture.frames
    backend.close()

    assert backend.capture.frames == frames
