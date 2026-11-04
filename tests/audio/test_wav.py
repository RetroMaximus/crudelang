"""The wav codec: what goes in must come out, and what does not must be named.

Everything here is Python-level - the bindings are tested in
``test_bindings_sound.py`` - because the two claims that matter are about the
bytes: that a round trip preserves sound to within the width it was written at,
and that a file this codec cannot read is refused for the reason it cannot.
"""

from __future__ import annotations

import array
import os
import sys
import wave

import pytest

from crude.audio import AudioError, plane_range, read_wav, read_wav_header, write_wav
from crude.audio.wav import (
    PCM16,
    PCM8,
    WavData,
    _pack,
    _quantise,
    _unpack,
)


def patch(path, offset: int, value: bytes) -> None:
    """Rewrite a few header bytes, the way a file from the world arrives."""
    with open(path, "r+b") as handle:
        handle.seek(offset)
        handle.write(value)


@pytest.fixture
def wav_file(tmp_path):
    """A path inside ``tmp_path``: nothing here writes into the source tree."""
    return str(tmp_path / "take.wav")


# -- a round trip -------------------------------------------------------


def test_a_mono_file_survives_being_written_and_read(wav_file):
    tone = [0.5 * ((index % 8) / 4.0 - 1.0) for index in range(128)]
    assert write_wav(wav_file, tone, 44100) == 128

    found = read_wav(wav_file)
    assert found.rate == 44100
    assert found.channels == 1
    assert found.bits == PCM16
    assert found.frames == 128
    assert len(found.samples) == 128
    # 16-bit quantisation is one part in 32767: the shape is intact
    for wanted, got in zip(tone, found.samples):
        assert got == pytest.approx(wanted, abs=1 / 32000)


def test_either_end_of_full_scale_and_silence_are_exact(wav_file):
    write_wav(wav_file, [-1.0, 0.0, 1.0], 8000)
    assert read_wav(wav_file).samples == [-1.0, 0.0, pytest.approx(1.0)]


def test_a_sample_past_full_scale_is_clamped_and_never_wraps(wav_file):
    """A value outside ``-1..+1`` is a mistake, and a wrap is a click.

    ``3.0`` mod-ing back to ``-1`` would put a full-scale discontinuity into the
    sound, so the codec pins it to the end of the scale instead.
    """
    write_wav(wav_file, [3.0, -2.5, 0.25], 8000)
    got = read_wav(wav_file).samples
    assert got[0] == pytest.approx(1.0, abs=1 / 32000)
    assert got[1] == pytest.approx(-1.0, abs=1 / 32000)
    assert got[2] == pytest.approx(0.25, abs=1 / 32000)


def test_an_eight_bit_file_measures_silence_at_the_midpoint(wav_file):
    """8-bit PCM is *unsigned*: 128 is zero, so a round trip has 1/128 of slack."""
    samples = [-1.0, -0.5, 0.0, 0.5, 0.25]
    write_wav(wav_file, samples, 22050, 1, PCM8)

    found = read_wav(wav_file)
    assert found.bits == PCM8
    assert found.frames == len(samples)
    for wanted, got in zip(samples, found.samples):
        assert got == pytest.approx(wanted, abs=1.0 / 128)


def test_a_stereo_file_is_de_interleaved_into_two_planes(wav_file):
    left = [index / 10.0 for index in range(6)]
    right = [-value for value in left]
    interleaved = [value for pair in zip(left, right) for value in pair]
    assert write_wav(wav_file, interleaved, 48000, 2) == 6

    found = read_wav(wav_file)
    assert found.channels == 2
    assert found.planes[0] == pytest.approx(left, abs=1 / 32000)
    assert found.planes[1] == pytest.approx(right, abs=1 / 32000)
    # a mirror-image pair averages to silence, which is what a mixdown means
    assert found.mix() == pytest.approx([0.0] * 6, abs=1 / 32000)


def test_a_mono_mixdown_is_the_plane_itself(wav_file):
    write_wav(wav_file, [0.1, 0.2, 0.3], 8000)
    assert read_wav(wav_file).mix() == read_wav(wav_file).samples


def test_a_frame_count_that_does_not_fill_a_channel_is_refused(wav_file):
    with pytest.raises(AudioError, match="do not divide into 2 channel"):
        write_wav(wav_file, [0.1, 0.2, 0.3], 8000, 2)


def test_an_empty_sound_is_still_a_file(wav_file):
    """Zero frames is a valid wav, and reading it back says so without a crash."""
    assert write_wav(wav_file, [], 44100) == 0
    found = read_wav(wav_file)
    assert found.frames == 0
    assert found.samples == []
    assert found.seconds == 0.0


def test_a_file_makes_the_folder_it_was_asked_for(tmp_path):
    path = str(tmp_path / "samples" / "deep" / "take.wav")
    write_wav(path, [0.0, 0.1], 8000)
    assert os.path.isfile(path)


def test_the_width_a_file_is_written_at_is_the_one_it_is_read_at(wav_file):
    for width in (PCM8, PCM16):
        write_wav(wav_file, [0.4, -0.4], 11025, 1, width)
        assert read_wav(wav_file).bits == width


def test_a_width_that_is_not_pcm_is_refused_by_the_writer(wav_file):
    with pytest.raises(AudioError, match="8-bit or 16-bit PCM, not 24"):
        write_wav(wav_file, [0.1], 8000, 1, 24)


def test_a_file_needs_a_rate_to_be_a_file(wav_file):
    with pytest.raises(AudioError, match="needs a sample rate"):
        write_wav(wav_file, [0.1], 0)


# -- the header alone ---------------------------------------------------


def test_a_header_can_be_read_without_the_sound(wav_file):
    # 200 interleaved samples across two channels is 100 frames, and a frame is
    # what ``frames`` counts - the same unit a clip's markers are measured in
    interleaved = [index / 8.0 for index in range(200)]
    write_wav(wav_file, interleaved, 16000, 2)

    header = read_wav_header(wav_file)
    assert header.frames == 100
    assert header.channels == 2
    assert header.planes == []
    assert header.samples == []
    assert header.mix() == []
    assert header.seconds == pytest.approx(100 / 16000)


def test_the_seconds_a_file_is_measured_in_come_from_its_own_rate(wav_file):
    write_wav(wav_file, [0.0] * 4800, 48000)
    assert read_wav(wav_file).seconds == pytest.approx(0.1)


def test_a_file_written_at_the_wrong_rate_is_still_honest_about_it(wav_file):
    """The rate in the header is the answer a script gets, whatever it hoped for."""
    write_wav(wav_file, [0.0] * 1000, 44100)
    patch(wav_file, 24, array.array("I", [22050]).tobytes())
    found = read_wav(wav_file)
    assert found.rate == 22050
    assert found.seconds == pytest.approx(1000 / 22050)


# -- refusals -----------------------------------------------------------


def test_a_file_that_is_not_there_names_the_path_it_was_given(tmp_path):
    missing = str(tmp_path / "ghost.wav")
    with pytest.raises(AudioError, match="is not a file that is there"):
        read_wav(missing)


def test_a_directory_is_not_a_sound(tmp_path):
    folder = tmp_path / "take.wav"
    folder.mkdir()
    with pytest.raises(AudioError, match="is not a file that is there"):
        read_wav(str(folder))


def test_text_is_refused_as_not_a_wav_file(wav_file):
    with open(wav_file, "w", encoding="utf-8") as handle:
        handle.write("this is a letter about a recording, not a recording")

    with pytest.raises(AudioError, match="not a wav file crude can read"):
        read_wav(wav_file)


def test_a_twenty_four_bit_file_is_named_and_not_decoded(wav_file):
    """The width the plan refused to guess at: reading it wrong is worse than not.

    Three bytes per sample are signed-magnitude little-endian with no standard
    scale, and decoding them as 16-bit would produce noise that plays.
    """
    with wave.open(wav_file, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(3)
        handle.setframerate(44100)
        handle.writeframes(b"\x00\x80\x00" * 8)

    with pytest.raises(AudioError, match="is 24-bit; crude reads"):
        read_wav(wav_file)


def test_a_float_file_is_refused_for_the_format_it_claims(wav_file):
    write_wav(wav_file, [0.5] * 8, 44100)
    patch(wav_file, 20, b"\x03\x00")  # WAVE_FORMAT_IEEE_FLOAT

    with pytest.raises(AudioError, match="could not be opened"):
        read_wav(wav_file)


def test_a_compressed_file_is_refused_for_the_codec_it_claims(wav_file):
    write_wav(wav_file, [0.5] * 8, 44100)
    patch(wav_file, 20, b"\x06\x00")  # WAVE_FORMAT_ALAW

    with pytest.raises(AudioError, match="could not be opened"):
        read_wav(wav_file)


def test_a_truncated_file_gives_back_the_frames_that_are_there(wav_file):
    """A short file is a short sound, not padded silence and not a crash."""
    write_wav(wav_file, [0.1] * 64, 44100)
    with open(wav_file, "r+b") as handle:
        handle.truncate(44 + 16)  # the header still promises 64 frames

    assert read_wav_header(wav_file).frames == 64
    assert read_wav(wav_file).frames == 8


def test_a_channel_count_of_zero_is_refused(wav_file):
    write_wav(wav_file, [0.0] * 4, 8000)
    patch(wav_file, 22, b"\x00\x00")

    # whichever side of the read says it - ``wave`` validates a header, and the
    # codec checks again before it de-interleaves - the refusal names the
    # channel count rather than crashing on a modulo by zero
    with pytest.raises(AudioError) as caught:
        read_wav(wav_file)
    assert "channel" in str(caught.value)


# -- the pieces underneath ---------------------------------------------


def test_the_unpacker_turns_bytes_over_on_a_big_endian_machine():
    """``array`` reads native words, so the swap is what makes RIFF portable."""
    low, mid, high = _unpack(array.array("h", [-32768, 0, 32767]).tobytes(),
                             PCM16, 1)
    assert low == pytest.approx(-32768 / 32767)
    assert mid == 0.0
    assert high == pytest.approx(1.0)
    assert _unpack(bytes([0, 128, 255]), PCM8, 1) == pytest.approx([-1.0, 0.0,
                                                                    127 / 128])
    assert _unpack(b"", PCM16, 1) == []


def test_a_dropped_sample_count_is_odd_for_a_stereo_run_and_trimmed():
    """A block that ends mid-frame cannot make a plane, so it is not invented."""
    values = _unpack(array.array("h", [100, 200, 300]).tobytes(), PCM16, 2)
    assert len(values) == 2


def test_a_nan_is_written_as_silence(wav_file):
    """``round(float('nan'))`` is a ``TypeError``: a NaN must not kill a save."""
    write_wav(wav_file, [float("nan"), 0.5], 8000)
    assert read_wav(wav_file).samples[0] == 0.0


def test_the_packer_holds_the_scale_it_was_given():
    assert _pack([2.0, -2.0], PCM16) == _pack([1.0, -1.0], PCM16)


def test_a_plane_range_is_clamped_to_what_exists():
    data = WavData(rate=8000, frames=10, planes=[[index / 10.0
                                                  for index in range(10)]])

    assert plane_range(data, 2, 3) == [0.2, 0.3, 0.4]
    assert plane_range(data, 8, 100) == [0.8, 0.9]
    assert plane_range(data, -5, 2) == [0.0, 0.1]
    assert plane_range(data, 0, 0) == data.samples
    assert plane_range(data, 99, 4) == []
    assert plane_range(WavData(rate=8000, frames=10), 0, 4) == []


def test_a_plane_range_can_ask_for_the_other_side_of_a_stereo_file():
    left = [0.0, 0.25, 0.5, 0.75]
    data = WavData(rate=8000, channels=2, frames=4, planes=[left, [-v for v in left]])

    assert plane_range(data, 1, 2, 1) == [-0.25, -0.5]
    # a channel a file does not have is the channel it does, rather than a crash
    assert plane_range(data, 0, 2, 9) == data.planes[-1][:2]


# -- the shape of the answer -------------------------------------------


def test_a_wav_data_thing_describes_itself_in_words_a_person_can_read():
    data = WavData(path="C:/x/take.wav", rate=48000, channels=2, bits=16,
                   frames=48000)
    assert str(data) == ("WavData take.wav: 48000 Hz, 2 channel(s), 16-bit, "
                         "48000 frame(s) (1.000 s)")


def test_the_byte_order_a_file_is_read_in_is_the_one_it_was_written_in(wav_file):
    """RIFF is little-endian, so a native ``array`` of ``h`` is the data chunk.

    The words are the codec's own quantisation; what is pinned here is that the
    bytes land in the order a RIFF reader expects, whichever way this machine
    happens to store a C ``short``.
    """
    samples = [-0.75, 0.25, 0.5]
    write_wav(wav_file, samples, 8000)
    with open(wav_file, "rb") as handle:
        body = handle.read()[44:]

    wanted = array.array("h", [int(_quantise(value, 32767.0)) for value in samples])
    assert len(body) == 2 * len(samples)
    if sys.byteorder != "little":
        wanted.byteswap()
    assert body == wanted.tobytes()
