"""The sound-file and recording bindings, driven as a script drives them.

Three things are pinned here that a Python-level test of the codec cannot see:
that a path means the *project's* directory, that a file's samples are decoded
only when a ``GET`` asks for them, and that a recording answers the same way a
meter does when there is no device - as zeros, not as an error.
"""

from __future__ import annotations

import math
import os
import types

import pytest

from crude.audio import AudioConfig, NullBackend, RecordingRender, read_wav
from crude.audio.runtime import AudioRuntime
from crude.audio.wav import write_wav
from crude.bindings.registry import REGISTRY
from crude.bindings.sound import BINDINGS
from crude.lang import Program
from crude.lang.values import BindingError, HostHandle, List, Number, String

LEFT, RIGHT = 0.5, -0.25


def text(path) -> str:
    """A path spelled the way a ``.crude`` string literal can hold it.

    A backslash is the language's escape character, so a Windows path written into
    a line of source loses its separators; forward slashes are what the bindings
    accept and what they answer with everywhere.
    """
    return str(path).replace("\\", "/")


def stereo(buffer) -> None:
    for index in range(len(buffer)):
        buffer[index] = LEFT if index % 2 == 0 else RIGHT


def as_py(value):
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, List):
        return [as_py(item) for item in value.elements]
    if isinstance(value, HostHandle):
        return value.target
    return value


@pytest.fixture
def project_dir(tmp_path):
    """A project directory that is *not* the working directory."""
    folder = tmp_path / "proj"
    (folder / "samples").mkdir(parents=True)
    return folder


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """The run's cwd, somewhere else: a relative path must not land in it."""
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    monkeypatch.chdir(folder)
    return folder


@pytest.fixture
def backend(project_dir):
    stream = NullBackend(AudioConfig(sample_rate=48000, block_size=64,
                                     in_channels=2, out_channels=2),
                         render=RecordingRender(value=0.1), fill=stereo)
    stream.open()
    yield stream
    stream.close()


@pytest.fixture
def runtime(backend):
    built = AudioRuntime(config=backend.config, backend=backend)
    yield built
    built.close()


@pytest.fixture
def program(runtime, project_dir, elsewhere):
    host = types.SimpleNamespace(audio=runtime,
                                 project=types.SimpleNamespace(
                                     directory=str(project_dir)))
    return Program("<sound-test>", host=host)


@pytest.fixture
def say(program):
    def _run(source):
        value, error = program.run(source)
        assert error is None, error.as_string()
        return value

    return _run


@pytest.fixture
def fails(program):
    def _run(source):
        value, error = program.run(source)
        assert error is not None, f"expected a failure, got {value!r}"
        return error.as_string()

    return _run


# -- the surface --------------------------------------------------------


def test_the_plan_named_exactly_these_sound_bindings():
    assert BINDINGS == (
        "WAV_READ", "WAV_WRITE", "WAV_LENGTH",
        "AUDIO_RECORD", "AUDIO_RECORD_START", "AUDIO_RECORD_STOP",
    )
    for name in BINDINGS:
        assert name in REGISTRY, name
        assert REGISTRY[name].area == "sound", name


@pytest.mark.parametrize("name,args,optional", [
    ("WAV_READ", ("path",), 0),
    ("WAV_WRITE", ("path", "samples", "rate", "channels", "bits"), 3),
    ("WAV_LENGTH", ("path",), 0),
    ("AUDIO_RECORD", (), 0),
    ("AUDIO_RECORD_START", ("seconds", "channel"), 1),
    ("AUDIO_RECORD_STOP", (), 0),
])
def test_every_sound_binding_declares_its_arguments(name, args, optional):
    entry = REGISTRY[name]
    assert entry.arg_names == args
    assert entry.optional == optional


# -- writing a file -----------------------------------------------------


def test_a_written_file_is_answered_by_the_projects_own_path(say, project_dir,
                                                             elsewhere):
    answer = as_py(say('WAV_WRITE("samples/take.wav", [0.0, 0.5, -0.5], 44100)'))

    assert answer == "samples/take.wav"
    on_disk = project_dir / "samples" / "take.wav"
    assert on_disk.is_file()
    # the working directory the run happened to start in stays empty
    assert not (elsewhere / "samples").exists()
    assert not (elsewhere / "take.wav").exists()

    found = read_wav(str(on_disk))
    assert found.rate == 44100
    assert found.frames == 3
    assert found.samples[1] == pytest.approx(0.5, abs=1 / 32000)


def test_an_absolute_path_is_honoured_and_answered_as_written(say, tmp_path):
    wanted = text(tmp_path / "outside" / "abs.wav")
    answer = as_py(say(f'WAV_WRITE("{wanted}", [0.25], 8000)'))

    # outside the project it keeps the absolute spelling it was given
    assert answer == wanted
    assert os.path.isabs(answer.replace("/", os.sep))
    assert read_wav(wanted).samples[0] == pytest.approx(0.25, abs=1 / 32000)


def test_a_folder_is_made_when_a_file_is_written_into_one(say, project_dir):
    """``samples/`` is the recorder's own folder, and nobody has to pre-make it."""
    answer = as_py(say('WAV_WRITE("new/deep/two.wav", [0.0], 8000)'))

    assert answer == "new/deep/two.wav"
    assert (project_dir / "new" / "deep" / "two.wav").is_file()


def test_a_stereo_file_is_written_from_interleaved_samples(say, project_dir):
    path = text(project_dir / "stereo.wav")
    say(f'WAV_WRITE("{path}", [0.5, -0.5, 0.25, -0.25], 48000, 2)')

    found = read_wav(path)
    assert found.channels == 2
    assert found.frames == 2
    assert found.planes[0][0] == pytest.approx(0.5, abs=1 / 32000)
    assert found.planes[1][0] == pytest.approx(-0.5, abs=1 / 32000)


def test_a_file_can_be_written_eight_bit(say, project_dir):
    path = text(project_dir / "small.wav")
    say(f'WAV_WRITE("{path}", [0.5, -0.5], 22050, 1, 8)')
    assert read_wav(path).bits == 8


def test_a_sample_list_can_be_one_number(say, project_dir):
    """``AT`` hands back an element, not a list; saving one sample is still a file."""
    path = text(project_dir / "one.wav")
    say(f'WAV_WRITE("{path}", 0.5, 8000)')
    assert read_wav(path).frames == 1


def test_a_width_that_is_not_pcm_is_refused_with_the_argument(fails):
    assert "8-bit or 16-bit PCM, not 24" in fails(
        'WAV_WRITE("bad.wav", [0.0], 8000, 1, 24)')


def test_a_channel_count_of_zero_is_refused_rather_than_guessed(fails):
    assert "'channels' has to be 1 or more, got 0" in fails(
        'WAV_WRITE("bad.wav", [0.0], 8000, 0)')


def test_a_sample_that_is_not_a_number_is_named(fails, project_dir):
    assert "holds a sample that is not a number" in fails(
        f'WAV_WRITE("{text(project_dir / "bad.wav")}", [0.5, "loud"], 8000)')


def test_an_empty_path_is_not_a_file(fails):
    assert "an empty path is not a file" in fails('WAV_WRITE("", [0.0], 8000)')


# -- reading a file -----------------------------------------------------


@pytest.fixture
def take(project_dir):
    """A real stereo file, written the way a file from the world arrives."""
    path = project_dir / "sample.wav"
    values = []
    for index in range(32):
        values.append(LEFT)
        values.append(RIGHT)
    write_wav(text(path), values, 44100, 2)
    return text(path)


def test_reading_a_file_hands_back_a_handle_and_its_shape(say, take):
    handle = say(f'WAV_READ("{take}")')

    assert isinstance(handle, HostHandle)
    assert handle.kind == "wav"
    assert as_py(say(f'GET(WAV_READ("{take}"), "rate")')) == 44100
    assert as_py(say(f'GET(WAV_READ("{take}"), "channels")')) == 2
    assert as_py(say(f'GET(WAV_READ("{take}"), "bits")')) == 16
    assert as_py(say(f'GET(WAV_READ("{take}"), "frames")')) == 32
    assert as_py(say(f'GET(WAV_READ("{take}"), "seconds")')) == pytest.approx(
        32 / 44100)
    assert as_py(say(f'GET(WAV_READ("{take}"), "name")')) == "sample.wav"
    # the path a script gets back is the project's own spelling of the file
    assert as_py(say(f'GET(WAV_READ("{take}"), "path")')) == "sample.wav"


def test_the_samples_are_decoded_only_when_somebody_asks(say, take):
    handle = say(f'WAV_READ("{take}")')
    view = handle.target
    assert view._data is None  # the header read carried nothing

    samples = as_py(say(f'GET(WAV_READ("{take}"), "samples")'))
    assert samples == pytest.approx([LEFT] * 32, abs=1 / 32000)

    # and a file that is asked twice is still decoded once
    view.get_prop("samples")
    first = view._data
    view.get_prop("samples")
    assert view._data is first


def test_a_mixdown_averages_both_channels(say, take):
    mixed = as_py(say(f'GET(WAV_READ("{take}"), "mixed")'))
    assert mixed == pytest.approx([(LEFT + RIGHT) / 2] * 32, abs=1 / 32000)


def test_a_length_can_be_asked_without_the_sound(say, take):
    assert as_py(say(f'WAV_LENGTH("{take}")')) == pytest.approx(32 / 44100)


def test_a_file_that_is_not_there_says_which_one(fails, tmp_path):
    gone = text(tmp_path / "nope" / "ghost.wav")
    assert "is not a file that is there" in fails(f'WAV_READ("{gone}")')


def test_a_file_that_is_not_sound_is_refused_by_the_call(fails, project_dir):
    notes = project_dir / "notes.txt"
    notes.write_text("lyrics, not a waveform", encoding="utf-8")
    assert "not a wav file crude can read" in fails(f'WAV_READ("{text(notes)}")')


def test_a_sound_file_is_read_only(fails, take):
    assert "wav has no writable properties" in fails(
        f'SET(WAV_READ("{take}"), "rate", 8000)')


def test_a_property_a_wav_does_not_have_is_named(fails, take):
    assert "wav has no property 'loudness'" in fails(
        f'GET(WAV_READ("{take}"), "loudness")')


def test_a_file_that_changed_under_a_reader_is_said_in_the_scripts_voice(say,
                                                                          take):
    """The header was checked, then the file went: a script error, not a crash."""
    handle = say(f'WAV_READ("{take}")')
    os.remove(take)

    with pytest.raises(BindingError, match="could not be read"):
        handle.target.get_prop("samples")


# -- recording ----------------------------------------------------------


def test_a_take_is_armed_by_seconds_of_the_streams_rate(say, runtime):
    handle = say("AUDIO_RECORD_START(0.5)")

    assert isinstance(handle, HostHandle)
    assert handle.kind == "recorder"
    assert as_py(say('GET(AUDIO_RECORD(), "capacity")')) == 24000
    assert as_py(say('GET(AUDIO_RECORD(), "armed")')) == 1
    assert as_py(say('GET(AUDIO_RECORD(), "channel")')) == 0


def test_the_frames_of_a_take_arrive_as_the_stream_runs(say, runtime):
    say("AUDIO_RECORD_START(0.1)")
    runtime.tick(2)

    assert as_py(say('GET(AUDIO_RECORD(), "frames")')) == 128
    assert as_py(say('AUDIO_RECORD_STOP()')) == 128
    assert as_py(say('GET(AUDIO_RECORD(), "frames")')) == 128
    assert as_py(say('GET(AUDIO_RECORD(), "armed")')) == 0
    assert as_py(say('GET(AUDIO_RECORD(), "samples")'))[:2] == [LEFT, LEFT]


def test_the_second_input_channel_can_be_recorded_instead(say, runtime):
    say("AUDIO_RECORD_START(0.1, 1)")
    runtime.tick(1)
    say("AUDIO_RECORD_STOP()")

    assert as_py(say('GET(AUDIO_RECORD(), "channel")')) == 1
    assert as_py(say('GET(AUDIO_RECORD(), "samples")')) == [RIGHT] * 64


def test_a_take_that_outlasts_its_seconds_says_how_much_was_lost(say, runtime):
    say("AUDIO_RECORD_START(0.001)")  # 48 frames of room, blocks of 64
    runtime.tick(2)
    say("AUDIO_RECORD_STOP()")

    assert as_py(say('GET(AUDIO_RECORD(), "frames")')) == 48
    assert as_py(say('GET(AUDIO_RECORD(), "overflow")')) == 128 - 48


def test_a_take_can_be_measured_in_seconds(say, runtime):
    say("AUDIO_RECORD_START(0.5)")
    runtime.tick(1)
    assert as_py(say('GET(AUDIO_RECORD(), "seconds")')) == pytest.approx(64 / 48000)


def test_a_channel_the_stream_does_not_have_is_refused(fails, runtime):
    assert "cannot record channel 4" in fails("AUDIO_RECORD_START(0.1, 4)")


def test_the_seconds_of_a_take_have_to_be_a_number(fails, runtime):
    assert "'seconds' must be a number" in fails('AUDIO_RECORD_START("soon")')


def test_a_take_needs_some_time(fails, runtime):
    assert "has to be for some time" in fails("AUDIO_RECORD_START(0)")


def test_stopping_a_take_that_was_never_started_is_not_an_error(say):
    assert as_py(say("AUDIO_RECORD_STOP()")) == 0


def test_recording_without_a_device_refuses_to_start(config):
    """An *action* on a stream that is not there is an error; a *reading* is not.

    ``AUDIO_RECORD_START`` is a decision a script made on purpose, and silence
    from a recorder with no microphone is not what "started" means.
    """
    line = Program("<quiet>", host=types.SimpleNamespace(
        audio=AudioRuntime(config=config), project=None))
    value, error = line.run("AUDIO_RECORD_START(0.1)")

    assert value is None
    assert "audio is not open" in error.as_string()


def test_a_take_read_without_a_device_answers_zeros(config):
    """The plan's frame-handler rule, applied to a recorder widget.

    A window that reads ``frames`` every frame on a ``--no-audio`` run must keep
    running and read an empty take, not raise and be muted by the three-strike
    budget.
    """
    line = Program("<quiet>", host=types.SimpleNamespace(
        audio=AudioRuntime(config=AudioConfig(sample_rate=44100)), project=None))

    def read(source):
        value, error = line.run(source)
        assert error is None, error.as_string()
        return as_py(value)

    assert read("AUDIO_RECORD_STOP()") == 0
    assert read('GET(AUDIO_RECORD(), "frames")') == 0
    assert read('GET(AUDIO_RECORD(), "armed")') == 0
    assert read('GET(AUDIO_RECORD(), "samples")') == []
    assert read('GET(AUDIO_RECORD(), "capacity")') == 0
    assert math.isclose(read('GET(AUDIO_RECORD(), "rate")'), 44100)


def test_a_recorder_has_no_writable_properties(fails):
    assert "recorder has no writable properties" in fails(
        'SET(AUDIO_RECORD(), "frames", 100)')


def test_a_property_a_recorder_does_not_have_is_named(fails):
    assert "recorder has no property 'magic'" in fails(
        'GET(AUDIO_RECORD(), "magic")')
