"""The core bindings, exercised through the language."""

from __future__ import annotations

import pytest

from crude.bindings import REGISTRY, binding, install
from crude.bindings.registry import HostContext
from crude.lang import BindingError, BuiltInFunction, HostHandle, Number, SymbolTable
from helpers import as_py


# -- output and predicates ---------------------------------------------
def test_print_writes_and_returns_null(eval_src, capsys):
    value = eval_src('PRINT("hello")')
    assert as_py(value) == 0
    assert capsys.readouterr().out == "hello\n"


def test_print_ret_returns_the_rendered_value(eval_src):
    assert as_py(eval_src("PRINT_RET([1, 2])")) == "1, 2"


@pytest.mark.parametrize(
    "source,expected",
    [
        ("IS_NUM(1)", 1),
        ('IS_NUM("1")', 0),
        ('IS_STR("1")', 1),
        ("IS_STR(1)", 0),
        ("IS_LIST([])", 1),
        ("IS_LIST(1)", 0),
        ("IS_FUNC(FUNC(x) -> x)", 1),
        ("IS_FUNC(1)", 0),
    ],
)
def test_type_predicates(eval_src, source, expected):
    assert as_py(eval_src(source)) == expected


def test_type_of_names_each_kind(eval_src):
    assert as_py(eval_src("TYPE_OF(1)")) == "number"
    assert as_py(eval_src('TYPE_OF("s")')) == "string"
    assert as_py(eval_src("TYPE_OF([])")) == "list"
    assert as_py(eval_src("TYPE_OF(FUNC(x) -> x)")) == "function"


# -- lists -------------------------------------------------------------
def test_append_mutates_in_place_and_returns_null(eval_src):
    value = as_py(eval_src("VAR l = []\nAPPEND(l, 1)\nAPPEND(l, 2)\nLEN(l)"))
    assert value == 2


def test_append_result_is_null(eval_src):
    assert as_py(eval_src("VAR l = []\nAPPEND(l, 1)")) == 0


def test_pop_returns_the_element_and_shrinks_the_list(eval_src):
    assert as_py(eval_src("VAR l = [1, 2]\nPOP(l, 0)")) == 1
    assert as_py(eval_src("VAR l = [1, 2]\nPOP(l, 0)\nLEN(l)")) == 1


def test_pop_out_of_range_reports_the_index(failure):
    assert "out of range" in failure("VAR l = [1]\nPOP(l, 5)").details


def test_extend_concatenates_elements_not_lists(eval_src):
    source = "VAR a = [1]\nVAR b = [2, 3]\nEXTEND(a, b)\nLEN(a)"
    assert as_py(eval_src(source)) == 3


def test_len_of_lists_and_strings(eval_src):
    assert as_py(eval_src("LEN([1, 2, 3])")) == 3
    assert as_py(eval_src('LEN("hello")')) == 5
    assert as_py(eval_src('LEN("")')) == 0


def test_len_of_a_number_names_the_expected_types(failure):
    error = failure("LEN(5)")
    assert "list or string" in error.details
    assert "number" in error.details


def test_at_reads_lists_and_strings(eval_src):
    assert as_py(eval_src("AT([7, 8], 1)")) == 8
    assert as_py(eval_src("AT([7, 8], -1)")) == 8
    assert as_py(eval_src('AT("hello", 1)')) == "e"


def test_at_out_of_range_reports_the_bounds(failure):
    assert "out of range" in failure("AT([7, 8], 9)").details


def test_set_at_writes_through_the_variable(eval_src):
    source = "VAR l = [1, 2]\nSET_AT(l, 0, 9)\nAT(l, 0)"
    assert as_py(eval_src(source)) == 9


def test_set_at_on_a_string_is_rejected(failure):
    assert "must be a list" in failure('SET_AT("ab", 0, "c")').details


# -- scalars -----------------------------------------------------------
def test_min_max_clamp(eval_src):
    assert as_py(eval_src("MIN(2, 7)")) == 2
    assert as_py(eval_src("MAX(2, 7)")) == 7
    assert as_py(eval_src("CLAMP(5, 0, 3)")) == 3
    assert as_py(eval_src("CLAMP(-5, 0, 3)")) == 0


def test_clamp_with_inverted_bounds_is_rejected(failure):
    assert "greater than high bound" in failure("CLAMP(5, 3, 0)").details


def test_floor_and_round_turn_a_division_into_an_index(eval_src):
    """The language has no integer type, so a count divided is a float.

    ``FLOOR`` is the way down to an index and ``ROUND`` is the way to the nearest
    whole, which is what a sample position, a row number or a byte offset has to
    be before it can be handed to ``AT``.
    """
    assert as_py(eval_src("FLOOR(7 / 2)")) == 3
    assert as_py(eval_src("FLOOR(8 / 2)")) == 4
    assert as_py(eval_src("ROUND(7 / 2)")) == 4
    assert as_py(eval_src("ROUND(3)")) == 3


def test_floor_goes_down_across_zero_and_round_goes_to_the_nearest(eval_src):
    """``FLOOR`` is a floor and not a truncation - the difference is one whole."""
    assert as_py(eval_src("FLOOR(0.5)")) == 0
    assert as_py(eval_src("FLOOR(-0.5)")) == -1
    assert as_py(eval_src("FLOOR(-7 / 2)")) == -4
    assert as_py(eval_src("ROUND(-0.5)")) == 0, "a whole zero, never -0.0"


def test_round_is_the_rounding_a_marker_is_written_with(eval_src):
    """Exact halves go to the even side, the way :func:`crude.ui.clip._whole` rounds.

    A clip drag writes a whole sample through that rule, so a script that rounds a
    marker with ``ROUND`` and a widget that wrote it must answer the same sample -
    two roundings that disagree would be a knob whose number does not match the
    line drawn on the screen beside it.
    """
    assert as_py(eval_src("ROUND(0.5)")) == 0
    assert as_py(eval_src("ROUND(1.5)")) == 2
    assert as_py(eval_src("ROUND(2.5)")) == 2
    assert as_py(eval_src("ROUND(2.4)")) == 2
    assert as_py(eval_src("ROUND(2.6)")) == 3


def test_floor_answers_a_whole_number_not_a_float_that_merely_looks_like_one(eval_src):
    assert as_py(eval_src("FLOOR(4.0)")) == 4
    assert as_py(eval_src('TYPE_OF(FLOOR(4.5))')) == "number"
    # an index is printed as an index: 4, not 4.0, in a status line or a filename
    assert eval_src('STR(ROUND(4.4))').value == "4"
    assert eval_src('STR(FLOOR(4.4))').value == "4"


def test_floor_and_round_refuse_what_is_not_a_number(failure):
    assert "must be a number" in failure('FLOOR("2")').details
    assert "must be a number" in failure('ROUND([2])').details


def test_str_and_num_convert(eval_src):
    assert as_py(eval_src("STR(42)")) == "42"
    assert as_py(eval_src("NUM(42)")) == 42
    assert as_py(eval_src('NUM("42")')) == 42
    assert as_py(eval_src('NUM("4.5")')) == 4.5


def test_num_rejects_garbage(failure):
    assert "is not a number" in failure('NUM("abc")').details


def test_num_requires_number_or_string(failure):
    assert "must be a string or number" in failure("NUM([1])").details


# -- handles -----------------------------------------------------------
class _Props:
    """Minimal stand-in for a host object: the fallback GET/SET protocol.

    The kind is deliberately not one any subsystem owns (``audio``, ``widget``,
    ``patch`` and friends each install their own reader/writer through
    :func:`crude.bindings.core.own_props`), so this exercises the default path
    in :mod:`crude.bindings.core` that asks the target for ``get_prop`` /
    ``set_prop``.
    """

    def __init__(self):
        self.values = {"value": Number(3)}

    def get_prop(self, name):
        return self.values[name]

    def set_prop(self, name, value):
        self.values[name] = value


def test_get_and_set_use_the_handle_protocol(make_program):
    program = make_program()
    target = _Props()
    program.symbol_table.set("W", HostHandle(target, kind="propbag", label="knob"))

    value, error = program.run('GET(W, "value")')
    assert error is None
    assert as_py(value) == 3

    _, error = program.run('SET(W, "value", 9)')
    assert error is None
    assert as_py(target.values["value"]) == 9

    _, error = program.run('GET(W, "missing")')
    assert error is not None
    assert "no property" in error.details


def test_get_requires_a_handle(failure):
    assert "must be a handle" in failure('GET(1, "value")').details


def test_set_requires_a_writable_target(make_program):
    program = make_program()
    program.symbol_table.set("H", HostHandle(object()))
    _, error = program.run('SET(H, "value", 1)')
    assert error is not None
    assert "writable" in error.details


# -- argument checking and the registry -------------------------------
def test_too_few_arguments_names_the_builtin(failure):
    error = failure("LEN()")
    assert "too few args" in error.details
    assert "LEN" in error.as_string()


def test_too_many_arguments_is_rejected(failure):
    assert "too many args" in failure("LEN([1], 2)").details


def test_every_binding_is_uppercase_and_callable():
    for name, entry in REGISTRY.items():
        assert name == name.upper(), name
        assert isinstance(entry.arg_names, tuple)
        assert all(isinstance(arg, str) for arg in entry.arg_names)
        builtin = entry.to_builtin()
        assert isinstance(builtin, BuiltInFunction)
        assert builtin.arg_names == list(entry.arg_names)


def test_legacy_names_are_all_registered():
    expected = {
        "PRINT", "PRINT_RET", "INPUT", "INPUT_INT", "CLEAR", "CLS",
        "IS_NUM", "IS_STR", "IS_LIST", "IS_FUNC",
        "APPEND", "POP", "EXTEND", "LEN", "RUN",
        "GET", "SET", "TYPE_OF", "AT", "SET_AT", "MIN", "MAX", "CLAMP", "STR", "NUM",
    }
    assert expected <= set(REGISTRY)


def test_installing_into_two_tables_creates_independent_builtins():
    first = SymbolTable()
    second = SymbolTable()
    install(first)
    install(second)
    assert first.get("LEN") is not second.get("LEN")


def test_binding_decorator_registers_aliases(make_program):
    @binding("TEST_ECHO", ["value"], aliases=["TEST_ECHO_ALIAS"])
    def _echo(ctx):
        return ctx.get("value")

    try:
        assert REGISTRY["TEST_ECHO_ALIAS"].func is REGISTRY["TEST_ECHO"].func
        program = make_program()

        value, error = program.run("TEST_ECHO_ALIAS(4)")
        assert error is None
        assert as_py(value) == 4

        value, error = program.run("TEST_ECHO(5)")
        assert error is None
        assert as_py(value) == 5
    finally:
        del REGISTRY["TEST_ECHO"]
        del REGISTRY["TEST_ECHO_ALIAS"]


def test_missing_implementation_is_reported_not_crashing():
    builtin = BuiltInFunction("NO_BODY", [], None)
    result = builtin.execute([])
    assert result.error is not None
    assert "no implementation" in result.error.details


def test_host_context_coercions_report_the_argument_name():
    from crude.lang import Context, String

    context = Context("<test>")
    context.symbol_table = SymbolTable()
    context.symbol_table.set("value", String("nope"))
    host = HostContext(context, REGISTRY["CLAMP"])

    with pytest.raises(BindingError) as excinfo:
        host.as_number("value")
    assert "value" in str(excinfo.value)
    assert "number" in str(excinfo.value)
