"""The silent backend: what a test, CI and ``--no-audio`` run on.

``NullBackend`` is the plan's "no audio stream" case, where the runtime tick
*is* the libpd thread. Everything here is therefore about determinism: the same
pull always produces the same block, and nothing happens unless the caller asks.
"""

from __future__ import annotations

import math

import pytest

from crude.audio.base import AudioConfig, AudioError, RecordingRender
from crude.audio.null import NULL_DEVICE, NullBackend


@pytest.fixture
def small():
    return AudioConfig(sample_rate=8000, block_size=4, in_channels=2, out_channels=2)


# -- capability ---------------------------------------------------------


def test_the_silent_backend_is_always_available():
    assert NullBackend.available() is True
    assert NullBackend.live is False
    assert NullBackend.optional is True


def test_it_reports_exactly_one_device_and_it_is_nothing_at_all():
    device = NullBackend.devices()[0]
    assert len(NullBackend.devices()) == 1
    assert device.name == NULL_DEVICE
    assert device.index is None
    assert device.is_duplex
    assert device.host_api == "silent"
    assert NullBackend.default_device("input").name == NULL_DEVICE


# -- the pull side ------------------------------------------------------


def test_rendering_a_block_that_was_never_opened_says_what_to_call(small):
    backend = NullBackend(small, render=RecordingRender(value=0.5))
    with pytest.raises(AudioError, match="call AUDIO_START before rendering a block"):
        backend.process_block()


def test_a_pulled_block_is_the_renderers_block(small):
    render = RecordingRender(value=0.5)
    backend = NullBackend(small, render=render)
    backend.open()

    assert backend.process_block() == 8  # 4 frames * 2 channels
    assert render.calls == [1]
    assert backend.blocks == 1
    assert backend.frames == 4
    assert backend.last_output == [0.5] * 8
    assert backend.taps.rms() == pytest.approx(0.5)
    assert backend.get_prop("seconds") == pytest.approx(4 / 8000)


def test_pull_many_blocks_and_the_counters_add_up(small):
    backend = NullBackend(small, render=RecordingRender(value=0.25))
    backend.open()
    backend.start()

    assert backend.process_blocks(3) == 24
    assert backend.blocks == 3
    assert backend.frames == 12
    assert backend.blocks_requested == 3
    assert backend.get_prop("seconds") == pytest.approx(12 / 8000)
    assert "pulled 3 block(s)" in backend.describe()


def test_a_snapshot_survives_the_next_block(small):
    render = RecordingRender(value=0.5)
    backend = NullBackend(small, render=render)
    backend.open()
    backend.process_block()

    first = backend.output()
    render.value = 0.125
    backend.process_block()

    assert first == [0.5] * 8
    assert backend.output() == [0.125] * 8


def test_a_channel_can_be_read_out_of_the_interleaved_block(small):
    def alternating(ticks, in_buffer, out_buffer):
        for index in range(len(out_buffer)):
            out_buffer[index] = 1.0 if index % 2 == 0 else -1.0

    backend = NullBackend(small, render=alternating)
    backend.open()
    backend.process_block()

    assert backend.output() == [1.0, -1.0] * 4
    assert backend.output(0) == [1.0, 1.0, 1.0, 1.0]
    assert backend.output(1) == [-1.0, -1.0, -1.0, -1.0]
    with pytest.raises(AudioError, match=r"channel 2 is out of range \(this stream has 2\)"):
        backend.output(2)


def test_the_input_can_be_fed_without_a_microphone(small):
    def noise(in_buffer):
        for index in range(len(in_buffer)):
            in_buffer[index] = 0.5 if index % 2 == 0 else 0.0

    backend = NullBackend(small, render=RecordingRender(value=0.0), fill=noise)
    backend.open()
    backend.process_block()

    assert backend.last_input == [0.5, 0.0] * 4
    assert backend.input_taps.rms(0) == pytest.approx(0.5)
    assert backend.input_taps.rms(1) == 0.0
    # Nothing was rendered, so the output meters stay at silence...
    assert backend.taps.rms() == 0.0


def test_a_pause_makes_the_pull_silent_rather_than_stopping_it(small):
    render = RecordingRender(value=0.5)
    backend = NullBackend(small, render=render)
    backend.open()
    backend.pause()

    # A silent block produced nothing, which is what "0" means here.
    assert backend.process_block() == 0
    assert render.calls == []
    assert backend.last_output == [0.0] * 8
    assert backend.silent_blocks == 1
    assert backend.blocks == 0

    backend.resume()
    backend.process_block()
    assert backend.last_output == [0.5] * 8


def test_a_tick_count_is_honoured_because_libpd_renders_whole_blocks(small):
    render = RecordingRender(value=0.5)
    backend = NullBackend(small, render=render)
    backend.open()

    assert backend.process_block(ticks=4) == 32
    assert render.calls == [4]
    assert backend.frames == 16
    assert backend.blocks == 1  # one *pull*, four Pd ticks


def test_a_hot_block_is_reported_as_clipped_not_compressed(small):
    backend = NullBackend(small, render=RecordingRender(value=1.5))
    backend.open()
    backend.process_block()

    assert backend.taps.rms() == pytest.approx(1.5)
    assert backend.taps.peak() == pytest.approx(1.5)
    assert backend.taps.clipped == 8
    assert backend.taps.scope() == [1.5] * 4  # the mix of the two channels
    assert backend.info()["db"] == pytest.approx(20 * math.log10(1.5))


def test_the_scope_forgets_exactly_as_much_as_it_was_built_to_hold(small):
    backend = NullBackend(small, render=RecordingRender(value=0.25), scope_frames=6)
    backend.open()
    backend.process_blocks(4)  # 4 frames per block, so 16 frames of history asked for
    assert len(backend.taps.scope()) == 6
    assert backend.taps.scope(2) == [0.25, 0.25]
    assert backend.taps.scope_capacity == 6
