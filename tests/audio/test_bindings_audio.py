"""The ``AUDIO_*`` surface as a script sees it.

Every test writes ``.crude`` source, so argument names, coercion errors, handle
plumbing and the wording of a failure are all covered from the language side -
which is the side the plan promises a device and the silent backend cannot be
told apart from.
"""

from __future__ import annotations

import math
import types

import pytest

from crude.audio import AudioConfig, NullBackend, RecordingRender
from crude.audio.runtime import AudioRuntime
from crude.bindings.audio import BINDINGS
from crude.bindings.registry import REGISTRY
from crude.lang import Program
from crude.lang.values import HostHandle, List, Number, String


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


def eval_on(program, source):
    value, error = program.run(source)
    assert error is None, error.as_string()
    return value


@pytest.fixture
def chain_runtime(config):
    """A runtime with nothing chosen yet: ``AUDIO_START`` has to run the chain."""
    runtime = AudioRuntime(config=config)
    yield runtime
    runtime.close()


@pytest.fixture
def chain_program(chain_runtime, audio_program_for):
    return audio_program_for(chain_runtime)


# -- the registry -------------------------------------------------------


def test_the_plan_named_exactly_these_audio_bindings():
    assert BINDINGS == (
        "AUDIO_INFO", "AUDIO_DEVICES", "AUDIO_USE_DEVICES", "AUDIO_DEVICE",
        "AUDIO_START", "AUDIO_STOP",
        "AUDIO_LEVEL", "AUDIO_WAVEFORM",
    )
    for name in BINDINGS:
        assert name in REGISTRY, name
        assert REGISTRY[name].area == "audio", name


@pytest.mark.parametrize("name,args,optional", [
    ("AUDIO_INFO", (), 0),
    ("AUDIO_DEVICES", ("direction",), 1),
    ("AUDIO_USE_DEVICES", ("input", "output"), 2),
    ("AUDIO_DEVICE", (), 0),
    ("AUDIO_START", ("backend",), 1),
    ("AUDIO_STOP", (), 0),
    ("AUDIO_LEVEL", ("channel", "mode", "source"), 3),
    ("AUDIO_WAVEFORM", ("frames", "channel", "source"), 3),
])
def test_every_audio_binding_declares_its_arguments(name, args, optional):
    entry = REGISTRY[name]
    assert entry.arg_names == args
    assert entry.optional == optional


# -- AUDIO_INFO and GET -------------------------------------------------


def test_audio_info_hands_back_the_stream_as_a_handle(eval_audio):
    value = eval_audio("AUDIO_INFO()")

    assert isinstance(value, HostHandle)
    assert value.kind == "audio"
    assert value.label.startswith("audio null")


def test_a_handle_is_readable_by_property(eval_audio, audio_runtime):
    audio_runtime.tick(2)
    value = eval_audio('GET(AUDIO_INFO(), "blocks")')
    assert as_py(value) == 2
    assert as_py(eval_audio('GET(AUDIO_INFO(), "backend")')) == "null"
    assert as_py(eval_audio('GET(AUDIO_INFO(), "device")')) == "Null Audio Device"
    assert as_py(eval_audio('GET(AUDIO_INFO(), "rms")')) == pytest.approx(0.25)
    assert as_py(eval_audio('GET(AUDIO_INFO(), "sample_rate")')) == 48000
    # booleans cross the host boundary as 0/1, like everywhere else in the language
    assert as_py(eval_audio('GET(AUDIO_INFO(), "live")')) == 0
    assert as_py(eval_audio('GET(AUDIO_INFO(), "running")')) == 0


def test_a_property_that_does_not_exist_is_named(audio_failure):
    error = audio_failure('GET(AUDIO_INFO(), "loudness")')
    assert "audio has no property 'loudness'" in error.as_string()


def test_only_a_handle_can_be_asked_for_a_property(audio_failure):
    error = audio_failure('GET(12, "rms")')
    assert "must be a handle, got number" in error.as_string()


def test_an_audio_stream_has_no_writable_properties(audio_failure):
    """A stream's shape is frozen at open, so ``SET`` says so in ``GET``'s voice,
    not as a raw ``KeyError`` escaping the interpreter."""
    error = audio_failure('SET(AUDIO_INFO(), "sample_rate", 96000)')
    assert "audio has no writable properties (asked for 'sample_rate')" in error.as_string()


# -- AUDIO_START / AUDIO_STOP -------------------------------------------


def test_starting_without_a_device_lands_on_silence_and_says_why(
        chain_program, chain_runtime, no_audio_libraries):
    value, error = chain_program.run("AUDIO_START()")
    assert error is None, error.as_string()

    assert isinstance(value, HostHandle)
    assert isinstance(chain_runtime.backend, NullBackend)
    assert chain_runtime.running
    assert len(chain_runtime.notes) == 2  # both libraries were skipped, out loud
    assert as_py(eval_on(chain_program, 'GET(AUDIO_INFO(), "running")')) == 1

    stopped, error = chain_program.run("AUDIO_STOP()")
    assert error is None
    assert as_py(stopped) == 1
    assert not chain_runtime.running
    # stopping twice is not an error, it just says nothing was running
    again, error = chain_program.run("AUDIO_STOP()")
    assert error is None
    assert as_py(again) == 0


def test_a_backend_can_be_asked_for_by_name(chain_program, chain_runtime,
                                             stub_sounddevice):
    stub_sounddevice()
    value, error = chain_program.run('AUDIO_START("sounddevice")')

    assert error is None, error.as_string()
    assert chain_runtime.backend.name == "sounddevice"
    assert chain_runtime.live is True
    assert as_py(eval_on(chain_program, 'GET(AUDIO_INFO(), "live")')) == 1
    chain_program.run("AUDIO_STOP()")


def test_an_unavailable_backend_is_a_preference_not_a_demand(
        chain_program, chain_runtime, no_audio_libraries):
    value, error = chain_program.run('AUDIO_START("sounddevice")')

    assert error is None, error.as_string()
    assert chain_runtime.backend.name == "null"
    assert "sounddevice is not usable on this machine" in " ".join(chain_runtime.notes)


def test_a_backend_name_has_to_be_a_name(audio_failure):
    error = audio_failure("AUDIO_START(5)")
    assert "'backend' must be a string, got number" in error.as_string()


# -- AUDIO_LEVEL --------------------------------------------------------


def test_a_level_is_read_the_same_way_with_or_without_a_card(
        eval_audio, audio_runtime):
    audio_runtime.tick(1)

    assert as_py(eval_audio("AUDIO_LEVEL()")) == pytest.approx(0.25)
    assert as_py(eval_audio("AUDIO_LEVEL(1)")) == pytest.approx(0.25)
    assert as_py(eval_audio('AUDIO_LEVEL(1, "peak")')) == pytest.approx(0.25)
    assert as_py(eval_audio('AUDIO_LEVEL(0, "db")')) == pytest.approx(
        20 * math.log10(0.25))


def test_a_mode_that_is_not_a_meter_mode_is_listed(audio_failure):
    error = audio_failure('AUDIO_LEVEL(0, "loud")')
    assert (
        "AUDIO_LEVEL: an audio level mode must be db, peak, rms, got 'loud'"
        in error.as_string()
    )


def test_a_channel_has_to_be_one_this_stream_has(audio_failure):
    error = audio_failure("AUDIO_LEVEL(5)")
    assert "audio channel 5 is out of range (this stream has 2)" in error.as_string()


def test_a_channel_has_to_be_a_whole_number(audio_failure):
    error = audio_failure("AUDIO_LEVEL(1.5)")
    assert "'channel' must be a whole number" in error.as_string()


def test_a_mode_has_to_be_text(audio_failure):
    error = audio_failure('AUDIO_LEVEL(0, 2)')
    assert "'mode' must be a string, got number" in error.as_string()


def test_a_level_without_a_stream_reads_silence(audio_program_for, config):
    """An open runtime holding no device answers 0, it does not raise or mute."""
    program = audio_program_for(AudioRuntime(config=config))
    value, error = program.run("AUDIO_LEVEL()")

    assert error is None
    assert as_py(value) == 0.0


def test_the_input_meter_is_a_separate_tap(eval_audio, audio_runtime):
    """``"input"`` reads what came off the device, not what went to the speaker.

    The two taps hold different numbers here on purpose: the render fills the
    output with 0.25 and nothing is fed into the input, so a flat 0 against a 0.25
    is the proof that the argument chose the other side of the stream.
    """
    audio_runtime.tick(1)

    assert as_py(eval_audio('AUDIO_LEVEL(0, "rms", "output")')) == pytest.approx(0.25)
    assert as_py(eval_audio('AUDIO_LEVEL(0, "rms", "input")')) == pytest.approx(0.0)
    assert as_py(eval_audio('AUDIO_LEVEL(0, "peak")')) == pytest.approx(0.25)


def test_a_source_that_is_neither_side_of_the_stream_is_named(audio_failure):
    error = audio_failure('AUDIO_LEVEL(0, "rms", "microphone")')
    assert "must be 'output' or 'input', got 'microphone'" in error.as_string()


# -- AUDIO_WAVEFORM -----------------------------------------------------


def test_a_waveform_is_a_list_of_numbers(eval_audio, audio_runtime):
    audio_runtime.tick(2)
    value = eval_audio("AUDIO_WAVEFORM()")

    assert isinstance(value, List)
    assert len(value.elements) == 128
    assert as_py(eval_audio("AUDIO_WAVEFORM(4)")) == [0.25] * 4
    assert as_py(eval_audio("AUDIO_WAVEFORM(0)")) == []
    assert as_py(eval_audio("AUDIO_WAVEFORM(2, 1)")) == [0.25] * 2


def test_a_waveform_channel_is_validated(audio_failure):
    error = audio_failure("AUDIO_WAVEFORM(4, 9)")
    assert "audio channel 9 is out of range" in error.as_string()


def test_a_waveform_without_a_stream_is_empty_not_an_error(audio_program_for,
                                                            config):
    """A scope that runs every frame reads a flat trace, never a mute, on a
    ``--no-audio`` run: the whole reason the frame handler stays alive."""
    program = audio_program_for(AudioRuntime(config=config))
    value, error = program.run("AUDIO_WAVEFORM(256)")

    assert error is None
    assert as_py(value) == []


def test_a_waveform_can_be_read_off_the_input(eval_audio, audio_runtime):
    audio_runtime.tick(1)

    assert as_py(eval_audio('AUDIO_WAVEFORM(4, 0, "output")')) == [0.25] * 4
    assert as_py(eval_audio('AUDIO_WAVEFORM(4, 0, "input")')) == [0.0] * 4


# -- AUDIO_DEVICES ------------------------------------------------------


def test_the_device_list_is_every_device_the_machine_has(
        chain_program, stub_sounddevice, stub_pyaudio):
    stub_sounddevice()
    stub_pyaudio()
    listed = as_py(eval_on(chain_program, "AUDIO_DEVICES()"))

    assert len(listed) == 7  # three per library, plus the silent one
    # each line says what a script needs in order to pick one: index, direction,
    # channel count, rate and host API
    assert listed[0].startswith("[0] Microphone (Loopback) (in 2, 48000 Hz, ")
    assert listed[1].startswith("[1] Speakers (Loopback) (out 2, 48000 Hz, ")
    assert listed[2].startswith("[2] Studio Capture (in/out 8, 44100 Hz, MME)")
    # the two libraries spell one machine differently, in one shape all the same
    assert listed[3].startswith("[0] Microphone (Loopback) (in 2, 48000 Hz, ")
    assert listed[4].startswith("[1] Speakers (Loopback) (out 2, 48000 Hz, ")
    assert listed[5].startswith("[2] Studio Capture (in/out 2, 44100 Hz, ")
    assert listed[6].startswith("Null Audio Device (in/out 2, 44100 Hz, silent)")


def test_a_direction_narrows_the_list(chain_program, stub_sounddevice):
    stub_sounddevice()
    inputs = as_py(eval_on(chain_program, 'AUDIO_DEVICES("input")'))
    # two sounddevice inputs and the silent backend, which can do both
    assert len(inputs) == 3
    assert "Microphone" in inputs[0] and "Studio Capture" in inputs[1]
    assert inputs[2].startswith("Null Audio Device")
    assert all("(in " in line or "(in/out " in line for line in inputs)

    outputs = as_py(eval_on(chain_program, 'AUDIO_DEVICES("output")'))
    assert not any("Microphone" in line for line in outputs)
    duplex = as_py(eval_on(chain_program, 'AUDIO_DEVICES("duplex")'))
    assert len(duplex) == 2  # Studio Capture and the silent device


def test_a_direction_is_one_of_three_words(chain_program, no_audio_libraries):
    _, error = chain_program.run('AUDIO_DEVICES("midi")')
    assert (
        "AUDIO_DEVICES: a device direction must be one of input, output, duplex, "
        "got 'midi'" in error.as_string()
    )


# -- the runtime has to be there ----------------------------------------


@pytest.mark.parametrize("source", [
    "AUDIO_INFO()", "AUDIO_DEVICES()", "AUDIO_USE_DEVICES(0, 1)", "AUDIO_DEVICE()",
    "AUDIO_START()", "AUDIO_STOP()", "AUDIO_LEVEL()", "AUDIO_WAVEFORM()",
])
def test_an_unwired_program_says_which_binding_needed_audio(source):
    """A plain REPL program has no ``host.audio``: the error must say so."""
    _, error = Program("<audio-test>", host=types.SimpleNamespace()).run(source)

    assert error is not None, source
    name = source.split("(")[0]
    assert f"{name}: no audio runtime is attached to this program" in error.as_string()


def test_a_program_with_no_host_at_all_says_the_same():
    _, error = Program("<audio-test>").run("AUDIO_INFO()")
    assert "no audio runtime is attached" in error.as_string()


# -- a whole script -----------------------------------------------------


def test_a_script_can_drive_the_audio_surface_end_to_end(config):
    backend = NullBackend(
        config, render=RecordingRender(value=0.5),
        fill=lambda buffer: None,
    )
    runtime = AudioRuntime(config=config, backend=backend)
    program = Program("<audio-test>", host=types.SimpleNamespace(audio=runtime))

    value, error = program.run(
        'VAR h = AUDIO_START("null")\n'
        'PRINT(GET(h, "backend"))\n'
        "AUDIO_LEVEL()\n"
    )
    assert error is None, error.as_string()
    assert as_py(value) == 0.0  # nothing has been ticked from the host side yet

    runtime.tick(2)
    level, error = program.run("AUDIO_LEVEL(0)")
    assert error is None
    assert as_py(level) == pytest.approx(0.5)
    assert as_py(eval_on(program, "AUDIO_WAVEFORM(3)")) == [0.5] * 3
    assert as_py(eval_on(program, "AUDIO_STOP()")) == 1
