"""Interpreter behaviour through whole programs (scope, loops, functions)."""

from __future__ import annotations

import pytest

from crude.lang import Number
from helpers import as_py


# -- assignments and scope ---------------------------------------------
def test_reassignment_updates_the_variable(eval_src):
    assert as_py(eval_src("VAR a = 1\nVAR a = a + 1\na")) == 2


def test_while_body_updates_an_enclosing_counter(eval_src):
    source = "VAR i = 0\nWHILE i < 3 THEN\n VAR i = i + 1\nEND\ni"
    assert as_py(eval_src(source)) == 3


def test_function_assignment_does_not_touch_the_global(eval_src):
    source = (
        "VAR x = 1\n"
        "FUNC f()\n VAR x = 9\n RETURN x\nEND\n"
        "VAR inside = f()\n"
        "inside * 100 + x"
    )
    assert as_py(eval_src(source)) == 901


def test_block_if_shares_the_enclosing_scope(eval_src):
    source = "VAR x = 0\nIF TRUE THEN\n VAR x = 5\nEND\nx"
    assert as_py(eval_src(source)) == 5


def test_inline_if_shares_the_enclosing_scope(eval_src):
    source = "VAR x = 0\nIF TRUE THEN VAR x = 5\nx"
    assert as_py(eval_src(source)) == 5

def test_undefined_variable_reports_its_name(failure):
    error = failure("missing_var")
    assert "'missing_var' is not defined" in error.details
    assert "Traceback" in error.as_string()


# -- control flow -------------------------------------------------------
def test_for_loop_collects_body_values(eval_src):
    assert as_py(eval_src("FOR i = 0 TO 3 THEN i")) == [0, 1, 2]


def test_for_loop_with_step(eval_src):
    assert as_py(eval_src("FOR i = 0 TO 6 STEP 2 THEN i")) == [0, 2, 4]


def test_for_loop_with_negative_step(eval_src):
    assert as_py(eval_src("FOR i = 3 TO 0 STEP -1 THEN i")) == [3, 2, 1]


def test_continue_skips_the_rest_of_the_body(eval_src):
    source = (
        "VAR out = []\n"
        "FOR i = 0 TO 4 THEN\n"
        " IF i == 2 THEN CONTINUE\n"
        " APPEND(out, i)\n"
        "END\n"
        "out"
    )
    assert as_py(eval_src(source)) == [0, 1, 3]


def test_break_leaves_the_loop(eval_src):
    source = (
        "VAR out = []\n"
        "FOR i = 0 TO 10 THEN\n"
        " IF i == 2 THEN BREAK\n"
        " APPEND(out, i)\n"
        "END\n"
        "out"
    )
    assert as_py(eval_src(source)) == [0, 1]


def test_while_loop_collects_body_values(eval_src):
    source = (
        "VAR out = []\n"
        "VAR i = 0\n"
        "WHILE i < 2 THEN\n"
        " APPEND(out, i)\n"
        " VAR i = i + 1\n"
        "END\n"
        "out"
    )
    assert as_py(eval_src(source)) == [0, 1]


def test_block_loops_return_null(eval_src):
    assert as_py(eval_src("FOR i = 0 TO 3 THEN\n i\nEND")) == 0
    assert as_py(eval_src("WHILE 0 THEN\n 1\nEND")) == 0


def test_if_elif_else_picks_the_first_true_branch(eval_src):
    source = (
        "FUNC pick(n)\n"
        " IF n < 0 THEN\n RETURN \"neg\"\n"
        " ELIF n == 0 THEN\n RETURN \"zero\"\n"
        " ELSE\n RETURN \"pos\"\n"
        " END\n"
        "END\n"
    )
    assert as_py(eval_src(source + "pick(-1)")) == "neg"
    assert as_py(eval_src(source + "pick(0)")) == "zero"
    assert as_py(eval_src(source + "pick(2)")) == "pos"


# -- operators ----------------------------------------------------------
def test_arithmetic_precedence_and_power(eval_src):
    assert as_py(eval_src("1 + 2 * 3")) == 7
    assert as_py(eval_src("2 ^ 3 ^ 2")) == 512
    assert as_py(eval_src("(1 + 2) * 3")) == 9


def test_unary_minus_and_not(eval_src):
    assert as_py(eval_src("-5")) == -5
    assert as_py(eval_src("NOT 0")) == 1
    assert as_py(eval_src("NOT 1")) == 0


def test_and_or_short_circuit_by_value(eval_src):
    assert as_py(eval_src("1 AND 0")) == 0
    assert as_py(eval_src("1 OR 0")) == 1


def test_string_equality_works(eval_src):
    assert as_py(eval_src('"a" == "a"')) == 1
    assert as_py(eval_src('"a" != "a"')) == 0
    assert as_py(eval_src('"a" < "b"')) == 1
    assert as_py(eval_src('"a" <= "a"')) == 1


def test_string_concatenation_and_char_index(eval_src):
    assert as_py(eval_src('"loop" + "oop"')) == "loopoop"
    assert as_py(eval_src('VAR s = "abc"\ns / 1')) == "b"


def test_list_indexing_with_the_legacy_operator(eval_src):
    assert as_py(eval_src("VAR l = [7, 8]\nl / 1")) == 8


def test_division_by_zero_is_a_runtime_error(failure):
    assert "Division by zero" in failure("1 / 0").details


# -- functions ----------------------------------------------------------
def test_recursion(eval_src):
    source = "FUNC fact(n)\n IF n <= 1 THEN RETURN 1\n RETURN n * fact(n - 1)\nEND\nfact(5)"
    assert as_py(eval_src(source)) == 120


def test_arrow_function_auto_returns(eval_src):
    assert as_py(eval_src("FUNC sq(n) -> n * n\nsq(7)")) == 49


def test_anonymous_function_value_can_be_called(eval_src):
    assert as_py(eval_src("VAR double = FUNC(x) -> x * 2\ndouble(21)")) == 42


def test_function_without_return_yields_null(eval_src):
    assert as_py(eval_src("FUNC noop()\n VAR a = 1\nEND\nnoop()")) == 0


def test_too_many_arguments_is_reported(failure):
    error = failure("FUNC one(a) -> a\none(1, 2)")
    assert "too many args" in error.details


def test_too_few_arguments_is_reported(failure):
    error = failure("FUNC one(a) -> a\none()")
    assert "too few args" in error.details


def test_calling_a_non_function_is_reported(failure):
    error = failure("VAR n = 1\nn()")
    assert "Illegal operation" in error.details


def test_return_inside_a_loop_exits_the_function(eval_src):
    source = "FUNC first_over(limit)\n FOR i = 0 TO 10 THEN\n  IF i > limit THEN RETURN i\n END\n RETURN -1\nEND\nfirst_over(3)"
    assert as_py(eval_src(source)) == 4


def test_functions_are_values(eval_src):
    assert as_py(eval_src("FUNC id(x) -> x\nIS_FUNC(id)")) == 1


@pytest.mark.parametrize(
    "source,expected",
    [
        ("1 + 1", 2),
        ("2 * 3 + 4", 10),
        ("10 - 2 - 3", 5),
        ('"x" + "y"', "xy"),
    ],
)
def test_expression_results(eval_src, source, expected):
    assert as_py(eval_src(source)) == expected


def test_program_value_is_the_last_statement(eval_src):
    value = eval_src("VAR a = 1\nVAR b = 2\na + b")
    assert isinstance(value, Number)
    assert value.value == 3
