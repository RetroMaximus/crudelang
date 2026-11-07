"""Switching the running stream onto another device, live.

A stream opened with a device index cannot have that index moved under it - the
channel counts, block size and rate the taps and libpd were built for are frozen
at open - so ``AudioRuntime.switch_devices`` tears the stream down and opens a
fresh one through the same fallback chain ``open`` uses, keeping the session and
the pause wiring, and restarting it if it was running. Every test here drives the
stub ``sounddevice`` in :mod:`tests.audio.conftest`; nothing touches a device.
"""

from __future__ import annotations

from typing import List

from crude.audio import AudioConfig, AudioRuntime


def _runtime(config: AudioConfig, session=None) -> AudioRuntime:
    # ``preferred=sounddevice`` walks the real chain and lands on the stub, which
    # records every stream it was asked to build
    return AudioRuntime(session=session, config=config, preferred="sounddevice")


def test_a_switch_reopens_a_fresh_stream_on_the_picked_device(stub_sounddevice,
                                                              config):
    stub = stub_sounddevice()
    runtime = _runtime(config)
    runtime.start()
    first = stub.streams[0]
    assert first.kwargs["device"] == (None, None)

    backend = runtime.switch_devices(input_device=0, output_device=1)

    assert backend is runtime.backend
    assert backend.running is True
    reopened = stub.streams[1]
    assert reopened.kwargs["device"] == (0, 1), "the pick, resolved at open"
    assert first.closed is True, "the old stream is let go, not left running"
    assert runtime.current_devices() == (0, 1)
    runtime.close()


def test_a_switch_a_stream_that_is_not_playing_stays_not_playing(stub_sounddevice,
                                                                 config):
    stub = stub_sounddevice()
    runtime = _runtime(config)
    runtime.open()  # opened, never started

    backend = runtime.switch_devices(output_device=1)

    assert backend is runtime.backend
    assert runtime.running is False
    assert stub.streams[-1].started is False
    runtime.close()


def test_a_switch_keeps_the_session_reachable_from_the_new_stream(stub_sounddevice,
                                                                  config):
    """The reopened backend must render through the *same* session, not a stale one.

    The whole point of the ``_render_to`` trampoline is that a stream opened before
    ``pd.core`` still finds the session; a switch reopens the stream after the
    session exists, and it must keep finding it.
    """
    calls: List[int] = []

    class Session:
        def set_pauser(self, _switch):
            pass

        def process(self, ticks, _in, out):
            calls.append(ticks)
            for index in range(len(out)):
                out[index] = 0.5

    stub_sounddevice()
    runtime = _runtime(config, session=None)
    runtime.attach_session(Session())
    runtime.start()

    runtime.switch_devices(input_device=0, output_device=1)

    out = [0.0, 0.0]
    runtime.backend.render(2, None, out)
    assert calls == [2]
    assert out == [0.5, 0.5]
    runtime.close()


def test_use_devices_binding_switches_and_says_what_it_picked(
        stub_sounddevice, audio_program_for, config):
    stub = stub_sounddevice()
    runtime = _runtime(config)
    program = audio_program_for(runtime)

    value, error = program.run("AUDIO_START()")
    assert error is None, error.as_string()

    _, error = program.run("AUDIO_USE_DEVICES(0, 1)")
    assert error is None, error.as_string()
    assert stub.streams[-1].kwargs["device"] == (0, 1)
    assert runtime.running is True

    listed, error = program.run("AUDIO_DEVICE()")
    assert error is None
    assert [element.value for element in listed.elements] == ["0", "1"]
    runtime.close()
