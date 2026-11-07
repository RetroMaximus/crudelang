"""The ``PD_*`` surface as scripts see it: through the language, not the API.

Every test writes real ``.crude`` source, so the argument names, the coercion
errors and the handle plumbing are all covered - not just the Python calls.
"""

from __future__ import annotations

import pytest

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


def test_pd_file_returns_a_patch_handle(eval_pd, patch_file):
    value = eval_pd(f'PD_FILE("tone", "{patch_file.as_posix()}")')

    assert isinstance(value, HostHandle)
    assert value.kind == "patch"
    assert value.target.id == "tone"


def test_type_of_reports_a_patch(eval_pd, patch_file):
    value = eval_pd(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\nTYPE_OF(p)'
    )
    assert as_py(value) == "patch"


def test_dollar_zero_is_visible_to_scripts(eval_pd, patch_file):
    value = eval_pd(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\nPD_DOLLARZERO(p)'
    )
    assert as_py(value) >= 1000


def test_send_queues_a_float_that_lands_on_the_next_block(run_pd, runtime, fake_lib, patch_file):
    _, error = run_pd(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\nPD_SEND(p, "freq", 440)'
    )
    assert error is None, error.as_string() if error else ""
    assert fake_lib.sent == []           # queued, not sent

    runtime.flush()
    assert fake_lib.sent == [("float", "freq", 440.0)]


def test_send_coalesces_a_knob_drag(run_pd, runtime, fake_lib, patch_file):
    _, error = run_pd(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\n'
        'PD_SEND(p, "freq", 100)\n'
        'PD_SEND(p, "freq", 200)\n'
        'PD_SEND(p, "freq", 300)'
    )
    assert error is None

    runtime.flush()
    assert fake_lib.sent == [("float", "freq", 300.0)]


def test_send_dispatches_on_the_value_type(run_pd, runtime, fake_lib, patch_file):
    _, error = run_pd(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\n'
        'PD_SEND(p, "note", "c4")\n'
        'PD_SEND(p, "seq", [1, 2, 3])'
    )
    assert error is None

    runtime.flush()
    assert fake_lib.sent == [
        ("symbol", "note", "c4"),
        ("list", "seq", (1.0, 2.0, 3.0)),
    ]


def test_send_list_bang_and_message(run_pd, runtime, fake_lib, patch_file):
    _, error = run_pd(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\n'
        'PD_SEND_LIST(p, "seq", [4, 5])\n'
        'PD_SEND_BANG(p, "tick")\n'
        'PD_SEND_MSG(p, "seq", "set", [800])'
    )
    assert error is None

    runtime.flush()
    assert fake_lib.sent == [
        ("list", "seq", (4.0, 5.0)),
        ("bang", "tick"),
        ("message", "seq", "set", (800.0,)),
    ]


def test_exists_sees_receivers_bound_inside_the_patch(eval_pd, fake_lib, patch_file):
    fake_lib.add_receiver("freq")
    value = eval_pd(f'PD_EXISTS("freq")')
    assert as_py(value) == 1


def test_dsp_switch_reaches_pd(eval_pd, fake_lib):
    eval_pd("PD_DSP(1)")
    assert fake_lib.sent == [("message", "pd", "dsp", (1.0,))]

    eval_pd("PD_DSP(0)")
    assert fake_lib.sent[-1] == ("message", "pd", "dsp", (0.0,))


def test_on_pd_feeds_pd_events_back_into_the_script(run_pd, runtime, fake_lib, patch_file):
    _, error = run_pd(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\n'
        'FUNC echo(v) -> PD_SEND(p, "out", v * 2)\n'
        'ON_PD(p, "freq", echo)'
    )
    assert error is None

    fake_lib.emit("float", "freq", 220.0)
    assert runtime.dispatch() == 1

    runtime.flush()
    assert fake_lib.sent == [("float", "out", 440.0)]


def test_on_pd_can_filter_by_event_kind(run_pd, runtime, fake_lib, patch_file):
    _, error = run_pd(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\n'
        'FUNC only_bang() -> PD_SEND(p, "out", 1)\n'
        'ON_PD(p, "tick", only_bang, "bang")'
    )
    assert error is None

    fake_lib.emit("float", "tick", 9.0)
    fake_lib.emit("bang", "tick")
    runtime.dispatch()

    runtime.flush()
    assert fake_lib.sent == [("float", "out", 1.0)]


def test_off_pd_stops_the_callback(run_pd, runtime, fake_lib, patch_file):
    _, error = run_pd(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\n'
        'FUNC echo(v) -> PD_SEND(p, "out", v)\n'
        'ON_PD(p, "freq", echo)\n'
        'PRINT(OFF_PD(p, "freq"))'
    )
    assert error is None

    fake_lib.emit("float", "freq", 1.0)
    assert runtime.dispatch() == 0


def test_a_raising_handler_is_reported_and_muted(run_pd, runtime, fake_lib, patch_file):
    _, error = run_pd(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\n'
        'FUNC boom(v) -> 1 / 0\n'
        'ON_PD(p, "freq", boom)'
    )
    assert error is None

    for _ in range(4):
        fake_lib.emit("float", "freq", 1.0)
        runtime.dispatch()

    assert len(runtime.errors) == 3
    assert runtime.handlers[0].muted is True


def test_arrays_round_trip_through_the_patch(run_pd, runtime, fake_lib, patch_file):
    _, error = run_pd(f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")')
    assert error is None

    patch = runtime.patch("tone")
    fake_lib.add_array(f"{patch.dollar_zero}-wave", [0.0, 0.0, 0.0])

    value, error = run_pd(
        'PD_ARRAY_WRITE(p, "wave", [0.25, 0.5, 0.75])\n'
        'VAR values = PD_ARRAY_READ(p, "wave")\n'
        'PRINT(PD_ARRAY_SIZE(p, "wave"))\n'
        'AT(values, 1)'
    )
    assert error is None, error.as_string() if error else ""
    assert as_py(value) == 0.5
    assert fake_lib.arrays[f"{patch.dollar_zero}-wave"] == [0.25, 0.5, 0.75]


def test_array_write_reports_a_rejected_write(run_pd, fake_lib, patch_file):
    _, error = run_pd(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\n'
        'PD_ARRAY_WRITE(p, "nope", [1, 2])'
    )
    assert error is not None
    assert "rejected" in error.as_string()


def test_a_table_is_made_to_fit_a_longer_take_and_keeps_its_head(run_pd, runtime,
                                                                eval_pd,
                                                                fake_lib,
                                                                patch_file):
    """The size a patch was authored at is not the length of the file loaded.

    The answer is the array's own size read back rather than the number asked for,
    and growing keeps what was already written - a recorder that resized a table
    only to find the first half of its take gone would be worse off than one that
    could not resize at all.
    """
    _, error = run_pd(f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")')
    assert error is None
    patch = runtime.patch("tone")
    held = f"{patch.dollar_zero}-wave"
    fake_lib.add_array(held, [1.0, 2.0, 3.0])

    assert as_py(eval_pd('PD_ARRAY_RESIZE(p, "wave", 5)')) == 5, \
        "the size it has, not the size that was asked for"
    assert fake_lib.arrays[held][:3] == [1.0, 2.0, 3.0], "the head stayed put"
    assert as_py(eval_pd('PD_ARRAY_SIZE(p, "wave")')) == 5

    # a write that could not fit before the resize fits now, and a shrink truncates
    _, error = run_pd('PD_ARRAY_WRITE(p, "wave", [1, 2, 3, 4, 5])')
    assert error is None
    assert fake_lib.arrays[held] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert as_py(eval_pd('PD_ARRAY_RESIZE(p, "wave", 2)')) == 2
    assert fake_lib.arrays[held] == [1.0, 2.0]


def test_resizing_a_name_with_no_table_says_which_name(pd_failure, patch_file):
    """A resize that made nothing is not a success, so the refusal names the array."""
    error = pd_failure(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\n'
        'PD_ARRAY_RESIZE(p, "nope", 8)'
    )
    assert "'nope' is not on patch" in error.as_string()


@pytest.mark.parametrize("size", [0, -4])
def test_a_table_has_to_hold_a_sample(pd_failure, patch_file, size):
    error = pd_failure(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\n'
        f'PD_ARRAY_RESIZE(p, "wave", {size})'
    )
    assert "not a table size" in error.as_string()


def test_midi_bindings_reach_libpd(run_pd, runtime, fake_lib):
    _, error = run_pd(
        "PD_MIDI_NOTEON(0, 60, 100)\n"
        "PD_MIDI_NOTEOFF(0, 60)\n"
        "PD_MIDI_CTL(1, 7, 42)\n"
        "PD_MIDI_PGM(0, 3)\n"
        "PD_MIDI_BEND(0, 8000)\n"
        "PD_MIDI_TOUCH(0, 12)\n"
        "PD_MIDI_POLYTOUCH(0, 60, 99)\n"
        "PD_MIDI_SYSEX(0, 240)\n"
        "PD_MIDI_REALTIME(0, 248)"
    )
    assert error is None, error.as_string() if error else ""

    runtime.flush()
    assert fake_lib.midi == [
        ("noteon", 0, 60, 100),
        ("noteon", 0, 60, 0),          # libpd has no note-off call
        ("controlchange", 1, 7, 42),
        ("programchange", 0, 3),
        ("pitchbend", 0, 8000),
        ("aftertouch", 0, 12),
        ("polyaftertouch", 0, 60, 99),
        ("sysex", 0, 240),
        ("sysrealtime", 0, 248),
    ]


def test_close_releases_the_patch(run_pd, runtime, patch_file):
    value, error = run_pd(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\n'
        "PD_CLOSE(p)"
    )
    assert error is None
    assert as_py(value) == 1
    assert runtime.patches == {}


# -- error surfaces -----------------------------------------------------


def test_a_number_is_not_a_patch(pd_failure):
    error = pd_failure('PD_SEND(12, "freq", 1)')
    assert "'patch' must be a patch" in error.as_string()


def test_missing_file_names_the_path(pd_failure):
    error = pd_failure('PD_FILE("tone", "nope/missing.pd")')
    assert "does not exist" in error.as_string()


def test_a_handler_must_be_a_function(pd_failure, patch_file):
    error = pd_failure(f'ON_PD(PD_FILE("tone", "{patch_file.as_posix()}"), "freq", 5)')
    assert "'fn' must be a function" in error.as_string()


def test_too_few_arguments_are_reported(pd_failure):
    error = pd_failure("PD_SEND_BANG()")
    assert "too few args" in error.as_string()


def test_an_unloaded_patch_names_the_patch(pd_failure):
    error = pd_failure('PD_SEND(123, "freq", 1)')
    assert "must be a patch" in error.as_string()


def test_pd_bindings_need_a_runtime(make_program):
    """A plain REPL program has no ``host.pd``: the error must say so."""
    value, error = make_program().run('PD_EXISTS("freq")')
    assert error is not None
    assert "no Pd runtime" in error.as_string()


def test_pd_dollar_zero_needs_a_patch(pd_failure):
    error = pd_failure('PD_DOLLARZERO(1)')
    assert "'patch' must be a patch" in error.as_string()


def test_the_registry_lists_every_pd_binding():
    from crude.bindings.pd import BINDINGS
    from crude.bindings.registry import REGISTRY

    for name in BINDINGS:
        assert name in REGISTRY, name
        assert REGISTRY[name].area == "pd", name


@pytest.mark.parametrize("name", ["PD_SEND", "PD_SEND_LIST", "ON_PD", "PD_ARRAY_READ"])
def test_every_pd_binding_declares_argument_names(name):
    from crude.bindings.registry import REGISTRY

    assert REGISTRY[name].arg_names, name
    assert all(isinstance(arg, str) for arg in REGISTRY[name].arg_names)
