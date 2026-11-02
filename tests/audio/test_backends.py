"""The fallback chain: the plan's "device absent" rule, in one place.

Risk 7 of the plan is that no audio device must never be an error - the chain has
to end in ``NullBackend`` with a warning. These tests are also the only place the
*shape* of the chain is pinned: what it tries, what it records, and when it is
allowed to raise.
"""

from __future__ import annotations

import pytest

from crude.audio import (
    BACKENDS,
    PREFERENCE,
    AudioBackend,
    AudioConfig,
    AudioError,
    AudioUnavailable,
    NullBackend,
    PyAudioBackend,
    SoundDeviceBackend,
    available_backends,
    audio_chain,
    backend_class,
    backend_names,
    list_devices,
    open_backend,
    register_backend,
)
from crude.audio.base import RecordingRender


# -- the registry -------------------------------------------------------


def test_the_three_backends_are_registered_in_the_order_the_plan_named():
    assert backend_names() == ("sounddevice", "pyaudio", "null")
    assert PREFERENCE == ("sounddevice", "pyaudio", "null")
    assert backend_class("null") is NullBackend
    assert backend_class("sounddevice") is SoundDeviceBackend
    assert backend_class("pyaudio") is PyAudioBackend


def test_a_backend_that_does_not_exist_says_which_ones_do():
    with pytest.raises(AudioError, match=r"no audio backend named 'wespeaker' "
                                         r"\(known backends: sounddevice, pyaudio, null\)"):
        backend_class("wespeaker")


def test_a_new_backend_can_join_the_chain_and_leave_again():
    class ProbeBackend(AudioBackend):
        name = "probe"
        live = False

        def _open(self):
            self.device = "probe"

    try:
        register_backend(ProbeBackend)
        assert backend_class("probe") is ProbeBackend
        assert ProbeBackend in BACKENDS.values()
    finally:
        BACKENDS.pop("probe", None)

    assert "probe" not in BACKENDS


# -- availability -------------------------------------------------------


def test_no_libraries_means_only_silence_is_available(no_audio_libraries):
    assert available_backends() == ["null"]


def test_each_library_added_makes_one_more_backend_triable(stub_pyaudio,
                                                           stub_sounddevice):
    stub_sounddevice()
    assert available_backends() == ["sounddevice", "null"]
    stub_pyaudio()
    assert available_backends() == ["sounddevice", "pyaudio", "null"]


# -- the chain ----------------------------------------------------------


def test_the_chain_is_the_preference_with_the_asked_for_backend_first():
    assert audio_chain() == ("sounddevice", "pyaudio", "null")
    assert audio_chain("pyaudio") == ("pyaudio", "sounddevice", "null")
    assert audio_chain(NullBackend) == ("null", "sounddevice", "pyaudio")
    assert audio_chain(["pyaudio", "sounddevice"]) == ("pyaudio", "sounddevice", "null")
    assert audio_chain("null") == ("null", "sounddevice", "pyaudio")


def test_strict_means_this_one_or_nothing():
    assert audio_chain("sounddevice", strict=True) == ("sounddevice",)
    assert audio_chain(["pyaudio", "null"], strict=True) == ("pyaudio", "null")
    with pytest.raises(AudioError, match="a strict audio request has to name a backend"):
        audio_chain(None, strict=True)


@pytest.mark.parametrize("preferred,message", [
    (5, "must be named as a string or a class, got int"),
    (object(), "must be named as a string or a class, got object"),
])
def test_a_backend_question_has_to_be_asked_in_backend_names(preferred, message):
    with pytest.raises(AudioError, match=message):
        audio_chain(preferred)


def test_a_class_that_has_no_name_of_its_own_is_refused():
    class Nameless(AudioBackend):
        pass

    with pytest.raises(AudioError, match="Nameless does not say what it is called"):
        audio_chain(Nameless)


# -- opening ------------------------------------------------------------


def test_with_no_hardware_the_chain_lands_on_silence_and_says_why(
        no_audio_libraries):
    heard = []
    backend = open_backend(log=heard.append)

    assert isinstance(backend, NullBackend)
    assert backend.opened is True and backend.live is False
    assert [note for note in heard if "not usable" in note] == [
        "audio: sounddevice is not usable on this machine",
        "audio: pyaudio is not usable on this machine",
    ]
    assert backend.notes == heard
    backend.close()


def test_a_backend_that_opens_is_the_answer_and_there_is_no_noise(stub_sounddevice):
    stub_sounddevice()
    heard = []
    backend = open_backend(render=RecordingRender(value=0.0), log=heard.append)

    assert isinstance(backend, SoundDeviceBackend)
    assert heard == [] and backend.notes == []
    backend.close()


def test_every_failure_is_recorded_on_the_way_down(stub_pyaudio, stub_sounddevice):
    stub_sounddevice(open_error="no such device 7")
    stub_pyaudio(open_error="Device is already in use")
    heard = []

    backend = open_backend(log=heard.append)

    assert isinstance(backend, NullBackend)
    assert len(heard) == 2  # both libraries were available, neither could open
    assert "sounddevice fell back: sounddevice could not open" in heard[0]
    assert "no such device 7" in heard[0]
    assert "pyaudio fell back: pyaudio could not open" in heard[1]
    assert "Device is already in use" in heard[1]
    assert backend.notes == heard
    backend.close()


def test_a_strict_request_raises_and_lists_every_attempt(stub_sounddevice):
    stub_sounddevice(open_error="no such device 7")
    with pytest.raises(AudioUnavailable) as caught:
        open_backend(preferred="sounddevice", strict=True)

    message = str(caught.value)
    assert message.startswith("no audio backend could be opened: ")
    assert "sounddevice fell back" in message and "no such device 7" in message


def test_an_unknown_backend_name_is_a_note_not_a_crash(no_audio_libraries):
    heard = []
    backend = open_backend(preferred="wespeaker", log=heard.append)
    assert isinstance(backend, NullBackend)
    assert heard[0] == ("audio: there is no backend named 'wespeaker' "
                        "(known: sounddevice, pyaudio, null)")
    backend.close()


def test_an_unknown_backend_name_is_fatal_when_it_was_demanded(no_audio_libraries):
    with pytest.raises(AudioUnavailable, match="there is no backend named 'wespeaker'"):
        open_backend(preferred="wespeaker", strict=True)


def test_a_configuration_mistake_is_not_the_backends_fault_to_absorb(stub_sounddevice):
    class Fussy(SoundDeviceBackend):
        name = "fussy"

        def _open(self):
            raise AudioError("this machine wants an odd number of channels")

    BACKENDS["fussy"] = Fussy
    try:
        stub_sounddevice()
        with pytest.raises(AudioError, match="odd number of channels"):
            open_backend(preferred="fussy", strict=True)
    finally:
        BACKENDS.pop("fussy", None)


def test_the_config_and_the_renderer_reach_whatever_backend_wins(stub_sounddevice):
    stub_sounddevice()
    render = RecordingRender(value=0.5)
    config = AudioConfig(sample_rate=22050, block_size=32)
    backend = open_backend(config=config, render=render, scope_frames=128)

    assert backend.config is config
    assert backend.render is render
    assert backend.taps.scope_capacity == 128
    assert backend.taps.channels == config.out_channels
    backend.close()


# -- device listing across backends ------------------------------------


def test_a_script_sees_the_devices_of_every_library_it_has(stub_pyaudio,
                                                           stub_sounddevice):
    stub_sounddevice()
    stub_pyaudio()
    listed = list_devices()

    names = [device.name for device in listed]
    assert names.count("Studio Capture") == 2  # once per library
    assert "Null Audio Device" in names
    assert [device.index for device in listed if device.supports("duplex")] == [2, 2, None]


def test_a_direction_narrows_the_list_and_a_bad_word_does_not(stub_sounddevice):
    stub_sounddevice()
    assert all(device.is_input for device in list_devices("input"))
    assert all(device.is_output for device in list_devices("output"))
    assert all(device.is_duplex for device in list_devices("duplex"))
    assert list_devices("duplex")  # the one device that can do both
    with pytest.raises(AudioError, match="direction must be one of"):
        list_devices("backward")


def test_one_broken_library_does_not_break_the_listing(stub_sounddevice, monkeypatch):
    stub_sounddevice()

    def broken():
        raise AudioUnavailable("pyaudio is present but has no PortAudio")

    monkeypatch.setattr(PyAudioBackend, "available", classmethod(lambda cls: True))
    monkeypatch.setattr(PyAudioBackend, "devices", staticmethod(broken))

    names = [device.name for device in list_devices()]
    assert "Speakers (Loopback)" in names       # sounddevice still answered
    assert "Null Audio Device" in names         # and so did the silent one


def test_an_explicit_backend_name_limits_the_listing(stub_pyaudio, stub_sounddevice):
    stub_pyaudio()
    stub_sounddevice()
    assert all(device.name == "Null Audio Device" for device in list_devices(backend="null"))
    with pytest.raises(AudioError, match="no audio backend named 'serial'"):
        list_devices(backend="serial")
