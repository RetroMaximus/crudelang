"""The serial ports and the codecs, in isolation.

S10's plan line is "``SerialLink`` reader thread -> inbox, codecs,
reconnect/backoff, ``SERIAL_*``/``ON_SERIAL``" with an acceptance test that
says *"fake port + ``loop://`` tests"*. This file pins the two halves that
test is built out of: the ports the link can drive (the plan's named fake,
:class:`~crude.serial.fake.MemorySerialPort`, plus the ``loop://`` echo the
headless bridge example needs), and the codecs that turn bytes into the
frames a script actually sees.

Nothing here starts a thread; a link is :mod:`test_link_runtime`'s subject.
The port suite is only about the *contract* a port has to honour, so a
reviewer can read the exact behaviour a real device must reproduce.
"""

from __future__ import annotations

import pytest

from crude.serial import (
    CODECS,
    FloatCodec,
    IntCodec,
    LineCodec,
    ListCodec,
    LoopSerialPort,
    MemorySerialPort,
    RawCodec,
    SerialError,
    SerialUnavailable,
    check_port,
    codec_for,
    codec_names,
    open_port,
    pair,
    register_codec,
    split_uri,
)


# -- the port protocol ---------------------------------------------------

def test_a_memory_port_answers_the_protocol_a_link_needs():
    """The plan's fake port is a duck-typed object, not a hidden subclass."""
    port = MemorySerialPort("board")
    # :func:`check_port` inspects the six names a link calls; if any is
    # missing the link would fail at open() rather than here.
    assert check_port(port) is port
    port.open()
    assert port.is_open is True
    assert port.in_waiting() == 0
    assert port.write(b"hi") == 2
    assert port.peek_tx() == b"hi"
    port.push_rx(b"42\n")
    assert port.in_waiting() == 3
    assert port.read(3) == b"42\n"
    port.close()
    assert port.is_open is False


def test_a_port_that_does_not_speak_the_protocol_is_named_and_not_used():
    """A typo'd fake fails with the missing attribute, not ``AttributeError``."""
    class NotAPort:
        def open(self): pass

    # the protocol tuple checks ``close`` next, so the missing member the
    # error names is ``close`` - a stable, greppable message either way
    with pytest.raises(SerialError, match=r"missing \.close\(\)"):
        check_port(NotAPort())


def test_write_on_a_closed_memory_port_is_an_error_not_a_silent_zero():
    """Plan risk 6's cousin: a write nobody heard is the worst kind of bug."""
    port = MemorySerialPort("board")
    with pytest.raises(SerialError, match="closed port"):
        port.write(b"hi")


def test_a_memory_port_can_be_told_to_refuse_to_open():
    """``fail_open`` is what the reconnect test needs, without a real cable."""
    port = MemorySerialPort("dead", fail_open=True)
    with pytest.raises(SerialUnavailable):
        port.open()


def test_a_memory_port_can_be_told_to_stop_answering():
    """The other failure a real device shows: open, then silent on the wire."""
    port = MemorySerialPort("flaky", fail_read=True)
    port.open()
    port.push_rx(b"hello\n")
    with pytest.raises(SerialError, match="stopped answering"):
        port.read(1)


def test_two_memory_ports_wired_together_reach_each_other():
    """:func:`pair` is how a bridge test writes "the script's side" and "the device's side"."""
    left, right = pair("bridge")
    left.open()
    right.open()
    assert left.write(b"ping\n") == 5
    assert right.in_waiting() == 5
    assert right.read(5) == b"ping\n"
    # and the other direction: the same wiring goes both ways
    assert right.write(b"pong\n") == 5
    assert left.read(5) == b"pong\n"


# -- the loop:// port ----------------------------------------------------

def test_a_loop_port_answers_what_the_script_wrote_to_it():
    """The headless bridge's whole trick: an echo port that never blocks."""
    port = LoopSerialPort("synth")
    port.open()
    assert port.write(b"level 12\n") == 9
    assert port.in_waiting() == 9
    assert port.read(9) == b"level 12\n"
    assert port.in_waiting() == 0


def test_a_loop_can_be_seeded_before_a_link_opens_it():
    """:meth:`push` is the test-side write that does not need the "must be open" rule."""
    port = LoopSerialPort("sensor")
    port.push(b"3.14\n")
    port.open()
    assert port.read(5) == b"3.14\n"


def test_drain_reads_a_loop_and_clears_it_in_one_call():
    port = LoopSerialPort()
    port.push(b"a")
    port.push(b"b")
    assert port.drain() == b"ab"
    assert port.drain() == b""


# -- the URI dispatcher --------------------------------------------------

@pytest.mark.parametrize("uri,expect", [
    ("loop://board", ("loop", "board")),
    ("memory://x", ("memory", "x")),
    ("COM3", ("", "COM3")),
    ("/dev/ttyUSB0", ("", "/dev/ttyUSB0")),
    # a scheme is only a scheme when "//" follows it, so a device path with
    # a colon in it does not misroute to some half-registered handler
    ("COM:1", ("", "COM:1")),
])
def test_a_port_uri_splits_into_scheme_and_the_rest(uri, expect):
    assert split_uri(uri) == expect


def test_the_loop_scheme_is_registered_by_import():
    """``import crude.serial`` is what makes ``loop://`` available."""
    port = open_port("loop://demo")
    assert isinstance(port, LoopSerialPort)
    assert port.describe() == "loop://demo"


def test_the_memory_scheme_reaches_the_plan_s_named_fake():
    port = open_port("memory://board")
    assert isinstance(port, MemorySerialPort)


def test_an_unknown_scheme_is_refused_with_the_list_of_valid_ones():
    """A typo'd scheme fails here, not silently in some library's device list."""
    with pytest.raises(SerialError, match="no serial scheme 'serial://'"):
        open_port("serial://anything")


# -- the codecs ----------------------------------------------------------

def test_the_codec_registry_holds_the_plan_s_five():
    """line/raw/int/float/list; anything else a manifest names is a script error."""
    assert codec_names() == ("float", "int", "line", "list", "raw")
    for name in codec_names():
        assert codec_for(name).name == name


def test_an_unknown_codec_names_the_ones_that_are_there():
    with pytest.raises(ValueError, match="no serial codec named 'midi'"):
        codec_for("midi")


def test_the_line_codec_hands_a_script_one_string_per_line():
    codec = LineCodec()
    assert codec.feed(b"hello\nwor") == ["hello"]
    assert codec.feed(b"ld\n") == ["world"]
    # the buffer kept "wor" and only produced "world" when the terminator landed
    assert codec.frames == 2


def test_a_line_terminator_may_be_a_crlf_for_a_windows_device():
    codec = LineCodec(terminator=b"\r\n")
    assert codec.feed(b"hi\r\nthere\r\n") == ["hi", "there"]


def test_the_line_codec_strips_surrounding_whitespace_before_the_handler_sees_it():
    """``42 `` and ``42`` mean the same thing to a Pd receiver; do not fight that."""
    codec = LineCodec()
    assert codec.feed(b"  42  \n") == ["42"]


def test_the_raw_codec_hands_the_script_the_exact_bytes_of_every_read():
    """Fixed-packet protocols are the reason the raw codec exists at all."""
    codec = RawCodec()
    assert codec.feed(b"\xf0\x01\x02") == [b"\xf0\x01\x02"]
    assert codec.feed(b"") == []


def test_the_int_codec_drops_a_line_that_is_not_an_integer_and_counts_it():
    """A plan risk in one place: the firmware's debug print should not be a crash."""
    codec = IntCodec()
    assert codec.feed(b"42\ndebug: hi\n7\n") == [42, 7]
    assert codec.dropped == 1


def test_the_float_codec_hands_the_script_floats_and_rejects_garbage_quietly():
    codec = FloatCodec()
    assert codec.feed(b"3.14\nnan-no\n2.5\n") == [3.14, 2.5]
    assert codec.dropped == 1


def test_the_list_codec_splits_a_comma_separated_line_into_floats():
    """``list 1,2,3`` style - what Pd's own ``[list]`` crosses a serial bridge as."""
    codec = ListCodec()
    assert codec.feed(b"1,2,3\n") == [[1.0, 2.0, 3.0]]
    # one bad field and the whole frame is dropped, but the count says so
    assert codec.feed(b"a,b\n") == []
    assert codec.dropped == 1


def test_a_line_codec_adds_the_terminator_on_encode_and_raw_does_not():
    """The asymmetry a script relies on: writing a line and writing bytes differ."""
    assert LineCodec().encode("hi") == b"hi\n"
    assert RawCodec().encode("hi") == b"hi"
    assert IntCodec().encode(42) == b"42\n"
    assert FloatCodec().encode(0.5) == b"0.5\n"
    assert ListCodec().encode([1, 2, 3]) == b"1,2,3\n"


def test_the_int_codec_refuses_a_non_integer_rather_than_lying_about_it():
    with pytest.raises(ValueError, match="cannot encode"):
        IntCodec().encode("not a number")


def test_a_codec_round_trips_what_the_same_codec_encoded():
    """The line codec's encode+feed identity, kept out of the link suite on purpose."""
    codec = LineCodec()
    frames = codec.feed(codec.encode("level 42"))
    assert frames == ["level 42"]


def test_a_project_can_add_a_codec_and_the_registry_sees_it():
    """The plan's manifest comment: "adding a codec needs no schema change"."""
    class UppercaseCodec(LineCodec):
        name = "upper"

        def feed(self, chunk):
            return [frame.upper() for frame in super().feed(chunk)]

    try:
        register_codec(UppercaseCodec)
        assert "upper" in codec_names()
        assert codec_for("upper").feed(b"hi\n") == ["HI"]
    finally:
        CODECS.pop("upper", None)


# -- the coercion helper every fake port shares --------------------------

def test_a_write_accepts_bytes_a_str_or_a_list_of_ints():
    """``_as_bytes`` is the codec's and the port's shared shape; pin the three."""
    port = MemorySerialPort("board")
    port.open()
    port.write(b"\x01\x02")
    port.write("hi")
    port.write([0xFF, 0x00])
    assert port.take_tx() == b"\x01\x02hi\xff\x00"


def test_a_write_that_is_not_bytes_a_str_or_a_list_is_a_clear_error():
    port = MemorySerialPort("board")
    port.open()
    with pytest.raises(SerialError, match="wants bytes or a string"):
        port.write({"not": "a payload"})
