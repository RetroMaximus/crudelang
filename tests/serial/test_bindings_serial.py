"""``SERIAL_*`` the way a script sees it: through the language, not the API.

Every test writes real ``.crude`` source so argument names, coercions, the
handle plumbing, and the ``GET`` surface are all pinned - not just the Python
calls. The plan's S10 acceptance line, "headless bridge example", is the last
group of tests: the same shape ``examples/sensor_bridge/main.crude`` runs,
driven on a ``loop://`` port so no Arduino is required.
"""

from __future__ import annotations

import types

import pytest

from crude.lang.values import HostHandle, List, Number, String
from crude.serial import LoopSerialPort, SerialLink


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


# -- the registry's shape -----------------------------------------------

def test_the_registry_lists_every_serial_binding():
    from crude.bindings.registry import REGISTRY
    from crude.bindings.serial import BINDINGS

    assert BINDINGS == (
        "SERIAL_OPEN", "SERIAL_WRITE", "SERIAL_CLOSE",
        "ON_SERIAL", "OFF_SERIAL",
    )
    for name in BINDINGS:
        assert name in REGISTRY, name
        assert REGISTRY[name].area == "serial", name


@pytest.mark.parametrize(
    "name, arg_names, optional",
    [
        ("SERIAL_OPEN", ("id", "port", "baud", "codec"), 3),
        ("SERIAL_WRITE", ("link", "data"), 0),
        ("SERIAL_CLOSE", ("link",), 0),
        ("ON_SERIAL", ("link", "fn", "kind"), 1),
        ("OFF_SERIAL", ("link", "fn"), 1),
    ],
)
def test_every_serial_binding_declares_its_signature(name, arg_names, optional):
    """The exact table S10 froze; a change here is a change to the language."""
    from crude.bindings.registry import REGISTRY

    entry = REGISTRY[name]
    assert entry.arg_names == arg_names, name
    assert entry.optional == optional, name


# -- SERIAL_OPEN --------------------------------------------------------

def test_serial_open_with_a_uri_returns_a_live_link_handle(run_serial, serial_runtime):
    value, error = run_serial('SERIAL_OPEN("board", "loop://board")')
    assert error is None, error.as_string() if error else ""

    assert isinstance(value, HostHandle)
    assert value.kind == "serial"
    link = value.target
    assert isinstance(link, SerialLink)
    assert link.opened is True


def test_serial_open_takes_baud_and_codec_when_the_script_says_so(run_serial, serial_runtime):
    _, error = run_serial('SERIAL_OPEN("board", "loop://board", 9600, "int")')
    assert error is None, error.as_string() if error else ""

    link = serial_runtime.link("board")
    assert link.baud == 9600
    assert link.codec_name == "int"
    assert link.codec.name == "int"


def test_serial_open_by_id_alone_picks_up_a_link_the_loader_opened(run_serial, serial_runtime):
    """The 1-arg form the sensor-bridge script uses; no port restated."""
    decl = types.SimpleNamespace(id="arduino", port="loop://arduino",
                                 baud=115200, codec="line", optional=True)
    opened = serial_runtime.open(decl, port=LoopSerialPort("arduino"))

    value, error = run_serial('SERIAL_OPEN("arduino")')
    assert error is None, error.as_string() if error else ""
    assert value.target is opened


def test_serial_open_by_id_names_what_it_could_not_find(serial_failure):
    error = serial_failure('SERIAL_OPEN("nope")')
    message = error.as_string()
    assert "'nope'" in message
    assert "known: none" in message


def test_an_unknown_scheme_is_a_script_error_naming_the_scheme(serial_failure):
    error = serial_failure('SERIAL_OPEN("board", "nonesuch://x")')
    assert "nonesuch" in error.as_string()


# -- SERIAL_WRITE / SERIAL_CLOSE ----------------------------------------

def test_serial_write_returns_the_bytes_the_port_accepted(run_serial, serial_runtime):
    _, error = run_serial(
        'VAR port = SERIAL_OPEN("board", "loop://board")\n'
        'SERIAL_WRITE(port, "hello")'
    )
    assert error is None, error.as_string() if error else ""

    link = serial_runtime.link("board")
    assert link.bytes_written == 6
    assert link.pump_once() == ["hello"]


def test_a_write_then_pump_produces_one_frame_the_script_can_read(run_serial, serial_runtime):
    _, error = run_serial(
        'VAR port = SERIAL_OPEN("board", "loop://board")\n'
        'SERIAL_WRITE(port, "level 42")'
    )
    assert error is None, error.as_string() if error else ""

    link = serial_runtime.link("board")
    assert link.pump_once() == ["level 42"]
    events = link.drain()
    assert [event.value for event in events] == ["level 42"]
    assert all(event.kind == "frame" for event in events)


def test_writing_to_a_closed_link_is_a_script_error(run_serial, serial_runtime):
    _, error = run_serial(
        'VAR port = SERIAL_OPEN("board", "loop://board")\n'
        "SERIAL_CLOSE(port)\n"
        'SERIAL_WRITE(port, "hi")'
    )
    assert error is not None
    assert "not open" in error.as_string()


def test_serial_close_reports_whether_it_was_open_and_releases_the_link(run_serial, serial_runtime):
    """``SERIAL_CLOSE`` is the plan's 1/0 answer, and a second close says 0."""
    value, error = run_serial(
        'VAR port = SERIAL_OPEN("board", "loop://board")\n'
        "SERIAL_CLOSE(port)"
    )
    assert error is None, error.as_string() if error else ""
    assert as_py(value) == 1
    assert serial_runtime.links == {}

    # the handle still points at the link object; a second close is a
    # "was it open when I asked?" 0, not a "that name is unknown" error
    value, error = run_serial("SERIAL_CLOSE(port)")
    assert error is None, error.as_string() if error else ""
    assert as_py(value) == 0


def test_a_number_is_not_a_link(serial_failure, run_serial):
    # the script variable ``port`` never got set, so the call sees a plain 12
    error = serial_failure('SERIAL_WRITE(12, "hi")')
    assert "'link' must be a serial link" in error.as_string()


def test_a_handler_must_be_a_function(run_serial, serial_failure):
    run_serial('VAR port = SERIAL_OPEN("board", "loop://board")')
    error = serial_failure("ON_SERIAL(port, 5)")
    assert "'fn' must be a function" in error.as_string()


# -- GET on a link handle ------------------------------------------------

def test_get_reads_the_link_properties_the_plan_pinned(run_serial, eval_serial):
    _, error = run_serial(
        'VAR port = SERIAL_OPEN("board", "loop://board", 9600, "line")'
    )
    assert error is None

    assert as_py(eval_serial('GET(port, "id")')) == "board"
    assert as_py(eval_serial('GET(port, "port")')) == "loop://board"
    assert as_py(eval_serial('GET(port, "baud")')) == 9600
    assert as_py(eval_serial('GET(port, "codec")')) == "line"
    assert as_py(eval_serial('GET(port, "opened")')) == 1


def test_get_counts_the_frames_and_bytes_a_link_has_moved(run_serial, eval_serial, serial_runtime):
    _, error = run_serial(
        'VAR port = SERIAL_OPEN("board", "loop://board")\n'
        'SERIAL_WRITE(port, "hello")'
    )
    assert error is None

    assert as_py(eval_serial('GET(port, "bytes_written")')) == 6
    serial_runtime.link("board").pump_once()
    assert as_py(eval_serial('GET(port, "frames")')) == 1
    assert as_py(eval_serial('GET(port, "bytes_read")')) == 6
    assert as_py(eval_serial('GET(port, "pending")')) == 1


def test_get_on_an_unknown_property_says_so_in_the_languages_voice(run_serial, serial_failure):
    run_serial('VAR port = SERIAL_OPEN("board", "loop://board")')
    error = serial_failure('GET(port, "nope")')
    assert "no property 'nope'" in error.as_string()


def test_set_can_move_baud_and_the_link_reopens_at_the_new_rate(run_serial, serial_runtime,
                                                                  serial_failure):
    """``SET(port, "baud", n)`` closes and reopens the device at the new rate.

    A link is single-baud, so the File menu's Serial dialog configures each
    declared link this way rather than restating the whole ``SERIAL_OPEN``.
    """
    _, error = run_serial(
        'VAR port = SERIAL_OPEN("board", "loop://board", 115200)'
    )
    assert error is None, error.as_string() if error else ""
    link = serial_runtime.link("board")
    before = link.port

    _, error = run_serial('SET(port, "baud", 9600)')
    assert error is None, error.as_string() if error else ""

    assert link.baud == 9600
    assert link.opened is True, "the link comes back up on the new rate"
    assert link.port is not before, "a fresh port object, not the old one re-labelled"
    after = link.port

    _, error = run_serial('SET(port, "codec", "int")')
    assert error is None, error.as_string() if error else ""
    assert link.codec_name == "int"
    assert link.port is after, "a codec change leaves the device alone"

    error = serial_failure('SET(port, "frames", 1)')
    assert "no writable properties" in error.as_string()


# -- ON_SERIAL / OFF_SERIAL ---------------------------------------------

def test_on_serial_returns_a_handler_handle_the_script_can_keep(run_serial, serial_runtime):
    _, error = run_serial(
        'VAR port = SERIAL_OPEN("board", "loop://board")\n'
        "FUNC noop(v) -> 0\n"
        "ON_SERIAL(port, noop)"
    )
    assert error is None, error.as_string() if error else ""
    assert len(serial_runtime.handlers) == 1


def test_dispatch_after_a_write_fires_the_registered_handler_once(run_serial, serial_runtime):
    _, error = run_serial(
        'VAR port = SERIAL_OPEN("board", "loop://board")\n'
        "VAR hits = 0\n"
        "FUNC bump(v) -> SET(hits, 1)\n"
        "ON_SERIAL(port, bump)\n"
        'SERIAL_WRITE(port, "one")'
    )
    assert error is None, error.as_string() if error else ""

    link = serial_runtime.link("board")
    assert link.pump_once() == ["one"]
    assert serial_runtime.dispatch() == 1


def test_off_serial_returns_how_many_handlers_went_away(run_serial, serial_runtime):
    """``OFF_SERIAL(port)`` drops every handler on that link and says how many."""
    _, error = run_serial(
        'VAR port = SERIAL_OPEN("board", "loop://board")\n'
        "FUNC a(v) -> 0\n"
        "FUNC b(v) -> 0\n"
        "ON_SERIAL(port, a)\n"
        "ON_SERIAL(port, b)\n"
        "OFF_SERIAL(port)"
    )
    assert error is None, error.as_string() if error else ""
    assert as_py(_) == 2
    assert serial_runtime.handlers == []


# -- the headless bridge (S10 acceptance) --------------------------------

def test_a_frame_from_the_port_lands_on_the_pd_send(bridge_with_link):
    """The full bridge: ``SERIAL_WRITE`` -> reader -> ``ON_SERIAL`` -> ``PD_SEND``.

    Same shape as ``examples/sensor_bridge/main.crude``: the script opens the
    declared link by id, registers a handler that turns the incoming line into
    a number, and pushes it to a Pd receiver. One test drives one frame
    through and reads what the fake libpd saw.
    """
    program = bridge_with_link.program
    serial = bridge_with_link.serial
    pd = bridge_with_link.pd
    lib = bridge_with_link.lib

    _, error = program.run(
        'VAR p = PD_BUILD("voice", 100, 100)\n'
        'VAR d = PD_OBJ(p, "dac~")\n'
        "PD_LOAD(p)\n"
        'VAR port = SERIAL_OPEN("arduino")\n'
        'FUNC on_line(text) -> PD_SEND(p, "level", NUM(text))\n'
        "ON_SERIAL(port, on_line)\n"
        'SERIAL_WRITE(port, "42")\n'
    )
    assert error is None, error.as_string() if error else ""

    # one reader tick, then the interpreter-tick dispatch
    link = serial.link("arduino")
    assert link.pump_once() == ["42"]
    assert serial.dispatch() == 1

    pd.flush()
    assert lib.sent == [("float", "level", 42.0)]


def test_the_bridge_pump_survives_several_lines_in_a_row(bridge_with_link):
    """Two lines in one pump: both reach the handler; the second wins per recv.

    ``PD_SEND`` coalesces per block (the S9 rule Pd itself uses for a knob
    drag), so two lines sent to the same receiver before a ``flush`` land as
    the last value only - which is what the bridge wants: an intermediate
    sensor sample is noise, the current one is the signal.
    """
    program = bridge_with_link.program
    serial = bridge_with_link.serial
    pd = bridge_with_link.pd
    lib = bridge_with_link.lib

    _, error = program.run(
        'VAR p = PD_BUILD("voice", 100, 100)\n'
        'VAR d = PD_OBJ(p, "dac~")\n'
        "PD_LOAD(p)\n"
        'VAR port = SERIAL_OPEN("arduino")\n'
        'FUNC on_line(text) -> PD_SEND(p, "level", NUM(text))\n'
        "ON_SERIAL(port, on_line)\n"
    )
    assert error is None, error.as_string() if error else ""

    # two writes, one pump: the codec sees "1\\n2\\n" and yields both frames
    link = serial.link("arduino")
    link.write("1")
    link.write("2")
    assert link.pump_once() == ["1", "2"]
    assert serial.dispatch() == 2

    pd.flush()
    # per-receiver coalescing keeps the newest sample and drops the older
    assert lib.sent == [("float", "level", 2.0)]


def test_a_link_the_script_closes_stops_calling_the_handler(bridge_with_link):
    program = bridge_with_link.program
    serial = bridge_with_link.serial

    _, error = program.run(
        'VAR p = PD_BUILD("voice", 100, 100)\n'
        'VAR d = PD_OBJ(p, "dac~")\n'
        "PD_LOAD(p)\n"
        'VAR port = SERIAL_OPEN("arduino")\n'
        'FUNC on_line(text) -> PD_SEND(p, "level", NUM(text))\n'
        "ON_SERIAL(port, on_line)\n"
        "SERIAL_CLOSE(port)\n"
    )
    assert error is None, error.as_string() if error else ""
    assert serial.handlers == []
    assert serial.links == {}


def test_a_handler_that_raises_is_reported_and_muted(bridge_with_link):
    """Same rule ``ON_PD`` uses - three strikes and the handler stops the noise."""
    program = bridge_with_link.program
    serial = bridge_with_link.serial

    _, error = program.run(
        'VAR port = SERIAL_OPEN("arduino")\n'
        "FUNC boom(v) -> 1 / 0\n"
        "ON_SERIAL(port, boom)\n"
    )
    assert error is None, error.as_string() if error else ""

    link = serial.link("arduino")
    for _ in range(5):
        link.write("1")
        link.pump_once()
        serial.dispatch()

    assert len(serial.errors) == 3
    assert serial.handlers[0].muted is True
