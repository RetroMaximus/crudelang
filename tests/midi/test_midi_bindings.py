"""The ``MIDI_*`` verbs the way a script calls them, through the language.

Each test writes real ``.crude`` source so the argument names, the coercions (a
port handle, a list of MIDI bytes), the handle plumbing and the read-only
``GET`` surface are pinned at the language boundary, not just the Python calls.
The runtime under the program is the in-process ``FakeMidiBackend``; a whole
run never imports ``rtmidi``.
"""

from __future__ import annotations

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


# -- the registry's shape ----------------------------------------------

def test_the_registry_lists_every_midi_binding():
    from crude.bindings.registry import REGISTRY
    from crude.bindings.midi import BINDINGS

    assert BINDINGS == (
        "MIDI_DEVICES", "MIDI_OPEN", "MIDI_SEND", "MIDI_CLOSE",
        "ON_MIDI", "OFF_MIDI",
    )
    for name in BINDINGS:
        assert name in REGISTRY, name
        assert REGISTRY[name].area == "midi", name


def test_every_midi_binding_declares_its_signature():
    from crude.bindings.registry import REGISTRY

    expected = {
        "MIDI_DEVICES": (("direction",), 1),
        "MIDI_OPEN": (("direction", "index"), 0),
        "MIDI_SEND": (("port", "data"), 0),
        "MIDI_CLOSE": (("port",), 0),
        "ON_MIDI": (("port", "fn"), 0),
        "OFF_MIDI": (("port", "fn"), 1),
    }
    for name, (arg_names, optional) in expected.items():
        entry = REGISTRY[name]
        assert entry.arg_names == arg_names, name
        assert entry.optional == optional, name


# -- MIDI_DEVICES ------------------------------------------------------

def test_midi_devices_lists_one_direction_as_readable_lines(eval_midi):
    value = eval_midi('MIDI_DEVICES("input")')
    lines = as_py(value)
    assert lines == ["[0] USB MIDI Keyboard (input, Fake)"]


def test_midi_devices_with_no_argument_lists_both_directions(eval_midi):
    lines = as_py(eval_midi("MIDI_DEVICES()"))
    assert any("(input" in line for line in lines)
    assert any("(output" in line for line in lines)


def test_a_machine_with_no_midi_reports_an_empty_list(eval_midi, fake_backend):
    fake_backend._inputs = []
    fake_backend._outputs = []
    assert as_py(eval_midi('MIDI_DEVICES("input")')) == []


def test_a_bad_direction_is_a_script_error(midi_failure):
    error = midi_failure('MIDI_DEVICES("sideways")')
    assert "input" in error.as_string() and "output" in error.as_string()


# -- MIDI_OPEN / MIDI_SEND / MIDI_CLOSE --------------------------------

def test_midi_open_returns_a_midi_port_handle(run_midi):
    value, error = run_midi('MIDI_OPEN("output", 0)')
    assert error is None, error.as_string() if error else ""
    assert isinstance(value, HostHandle)
    assert value.kind == "midi"
    assert value.target.direction == "output"


def test_midi_send_writes_the_bytes_and_returns_the_count(run_midi, fake_backend):
    value, error = run_midi(
        "VAR port = MIDI_OPEN(\"output\", 0)\n"
        "MIDI_SEND(port, [144, 60, 100])"
    )
    assert error is None, error.as_string() if error else ""
    assert as_py(value) == 3
    assert [packet for _port, packet in fake_backend.sent] == [(144, 60, 100)]


def test_midi_send_accepts_a_single_number_or_a_string(run_midi, fake_backend):
    _, error = run_midi(
        'VAR port = MIDI_OPEN("output", 0)\n'
        "MIDI_SEND(port, 248)"
    )
    assert error is None, error.as_string() if error else ""
    assert fake_backend.sent[-1][1] == (248,)

    _, error = run_midi("MIDI_SEND(port, \"hi\")")
    assert error is None, error.as_string() if error else ""
    assert fake_backend.sent[-1][1] == tuple(b"hi")


def test_sending_to_an_input_is_a_script_error(midi_failure, run_midi):
    run_midi('VAR port = MIDI_OPEN("input", 0)')
    error = midi_failure("MIDI_SEND(port, [144, 60, 100])")
    assert "cannot send" in error.as_string()


def test_a_plain_number_is_not_a_port(midi_failure):
    error = midi_failure("MIDI_SEND(12, [144, 60, 100])")
    assert "'port' must be a MIDI port" in error.as_string()


def test_midi_close_reports_whether_it_was_open(run_midi, fake_backend):
    value, error = run_midi(
        'VAR port = MIDI_OPEN("output", 0)\n'
        "MIDI_CLOSE(port)"
    )
    assert error is None, error.as_string() if error else ""
    assert as_py(value) == 1

    value, error = run_midi("MIDI_CLOSE(port)")
    assert error is None, error.as_string() if error else ""
    assert as_py(value) == 0


# -- GET on a port handle ----------------------------------------------

def test_get_reads_a_ports_properties_and_it_is_read_only(run_midi, eval_midi,
                                                          midi_failure):
    run_midi('VAR port = MIDI_OPEN("output", 0)')
    assert as_py(eval_midi('GET(port, "direction")')) == "output"
    assert as_py(eval_midi('GET(port, "index")')) == 0
    assert as_py(eval_midi('GET(port, "opened")')) == 1

    error = midi_failure('GET(port, "nope")')
    assert "no property 'nope'" in error.as_string()

    error = midi_failure('SET(port, "index", 3)')
    assert "no writable properties" in error.as_string()


# -- ON_MIDI / OFF_MIDI ------------------------------------------------

def test_on_midi_registers_a_handler_and_off_removes_it(run_midi, midi_runtime):
    _, error = run_midi(
        'VAR port = MIDI_OPEN("input", 0)\n'
        "FUNC noop(v) -> 0\n"
        "ON_MIDI(port, noop)"
    )
    assert error is None, error.as_string() if error else ""
    assert len(midi_runtime.handlers) == 1

    value, error = run_midi("OFF_MIDI(port)")
    assert error is None, error.as_string() if error else ""
    assert as_py(value) == 1
    assert midi_runtime.handlers == []


def test_a_handler_must_be_a_function(run_midi, midi_failure):
    run_midi('VAR port = MIDI_OPEN("input", 0)')
    error = midi_failure("ON_MIDI(port, 5)")
    assert "'fn' must be a function" in error.as_string()


def test_an_inbound_message_reaches_the_scripts_function(run_midi, midi_runtime,
                                                         fake_backend):
    _, error = run_midi(
        'VAR port = MIDI_OPEN("input", 0)\n'
        "FUNC on_note(packet) -> 0\n"
        "ON_MIDI(port, on_note)"
    )
    assert error is None, error.as_string() if error else ""

    port = midi_runtime.ports[next(iter(midi_runtime.ports))]
    fake_backend.inject(port.id, (144, 60, 100))
    assert midi_runtime.dispatch() == 1
    # the one handler the script registered ran once, and the port counted it
    assert midi_runtime.handlers[0].calls == 1
    assert port.messages_in == 1
