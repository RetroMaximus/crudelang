"""``RtMidiBackend`` driven entirely by a stub ``rtmidi`` in ``sys.modules``.

Nothing here needs a controller: :func:`stub_rtmidi` puts a fake module in place
that answers exactly the calls the adapter makes, and the test fires the input
callback by hand to reproduce the native-thread delivery that
:meth:`RtMidiBackend.poll` is built to marshal onto the interpreter thread. When
the module is absent (the autouse default) the adapter reports itself
unavailable instead of raising - the graceful path the plan insists on.
"""

from __future__ import annotations

import pytest

from crude.midi.base import MidiError, MidiUnavailable
from crude.midi.rtmidi_backend import RtMidiBackend, module


# -- capability --------------------------------------------------------

def test_the_backend_is_unavailable_without_the_module():
    # the autouse fixture put ``sys.modules["rtmidi"] = None``
    assert RtMidiBackend.available() is False
    with pytest.raises(MidiUnavailable):
        module()


def test_the_backend_is_available_once_the_module_appears(stub_rtmidi):
    stub_rtmidi()
    assert RtMidiBackend.available() is True


# -- enumeration -------------------------------------------------------

def test_devices_come_back_indexed_per_direction(stub_rtmidi):
    stub_rtmidi(inputs=["Key A", "Key B"], outputs=["Synth"], api="ALSA")
    backend = RtMidiBackend()

    inputs = backend.devices("input")
    assert [device.name for device in inputs] == ["Key A", "Key B"]
    assert [device.index for device in inputs] == [0, 1]
    assert {device.api for device in inputs} == {"ALSA"}
    assert {device.direction for device in inputs} == {"input"}

    outputs = backend.devices("output")
    assert [(device.name, device.index) for device in outputs] == [("Synth", 0)]


def test_listing_ports_closes_the_probe_it_made(stub_rtmidi):
    stub = stub_rtmidi()
    RtMidiBackend().devices("input")
    probe = stub.inputs()[0]
    assert probe.closed is True, "a listing probe is disposable"


def test_a_bad_direction_is_rejected_before_the_library_is_asked(stub_rtmidi):
    stub_rtmidi()
    with pytest.raises(MidiError):
        RtMidiBackend().devices("sideways")


# -- opening and sending ----------------------------------------------

def test_open_output_then_send_reports_the_bytes_written(stub_rtmidi):
    stub = stub_rtmidi(outputs=["Synth"])
    backend = RtMidiBackend()

    port_id = backend.open_output(0)
    written = backend.send(port_id, (0x90, 60, 100))
    assert written == 3
    assert stub.sent == [(0x90, 60, 100)]
    assert backend._outputs[port_id] is stub.outputs()[-1]


def test_an_index_past_the_end_is_a_script_error_not_a_crash(stub_rtmidi):
    stub_rtmidi(outputs=["Synth"])
    backend = RtMidiBackend()
    with pytest.raises(MidiError):
        backend.open_output(7)


def test_sending_to_a_port_that_was_never_opened_fails(stub_rtmidi):
    stub_rtmidi()
    backend = RtMidiBackend()
    with pytest.raises(MidiError):
        backend.send(42, (0x90, 60, 100))


# -- the queue-then-poll handover -------------------------------------

def test_an_input_queues_until_polled_then_calls_the_runtime(stub_rtmidi):
    stub = stub_rtmidi(inputs=["Keyboard"])
    backend = RtMidiBackend()

    seen = []
    port_id = backend.open_input(0, seen.append)
    native = stub.inputs()[-1]

    # the library's callback fires on its own thread: nothing reaches the
    # runtime until poll() drains the queue on the interpreter thread
    native.fire((0x90, 60, 100), stamp=0.5)
    assert seen == []

    backend.poll(port_id)
    assert len(seen) == 1
    message = seen[0]
    assert message.bytes == (0x90, 60, 100)
    assert message.port == port_id
    assert message.delta == 0.5


def test_poll_hands_over_every_packet_queued_since_the_last_one(stub_rtmidi):
    stub = stub_rtmidi(inputs=["Keyboard"])
    backend = RtMidiBackend()

    seen = []
    port_id = backend.open_input(0, seen.append)
    native = stub.inputs()[-1]

    native.fire((0x90, 60, 100))
    native.fire((0x80, 60, 0))
    backend.poll(port_id)
    assert [message.bytes for message in seen] == [(0x90, 60, 100), (0x80, 60, 0)]

    # a poll with nothing queued is a no-op, not a spurious callback
    backend.poll(port_id)
    assert len(seen) == 2


def test_polling_an_unknown_port_is_harmless(stub_rtmidi):
    stub_rtmidi()
    backend = RtMidiBackend()
    assert backend.poll(999) is None


# -- closing -----------------------------------------------------------

def test_closing_an_input_cancels_its_callback_and_closes_the_port(stub_rtmidi):
    stub = stub_rtmidi(inputs=["Keyboard"])
    backend = RtMidiBackend()
    port_id = backend.open_input(0, lambda message: None)
    native = stub.inputs()[-1]

    backend.close(port_id)
    assert native.cancelled is True
    assert native.closed is True


def test_close_with_no_id_releases_every_port(stub_rtmidi):
    stub = stub_rtmidi(inputs=["K"], outputs=["S"])
    backend = RtMidiBackend()
    in_id = backend.open_input(0, lambda message: None)
    out_id = backend.open_output(0)

    backend.close()
    assert backend._inputs == {}
    assert backend._outputs == {}


def test_the_describe_line_counts_what_is_open(stub_rtmidi):
    stub = stub_rtmidi(inputs=["K", "K2"], outputs=["S"])
    backend = RtMidiBackend()
    backend.open_input(0, lambda message: None)
    backend.open_output(0)
    assert backend.describe() == "midi rtmidi: 1 input(s), 1 output(s)"
