"""Value model: arithmetic, (string) comparison, lists, handles and errors."""

from __future__ import annotations

import pytest

from crude.lang import HostHandle, List, Number, RTError, String, type_name, value_eq


# -- numbers ------------------------------------------------------------
def test_number_arithmetic_and_division_by_zero():
    result, error = Number(2).added_to(Number(3))
    assert error is None and result.value == 5

    result, error = Number(6).divided_by(Number(3))
    assert error is None and result.value == 2.0

    _, error = Number(1).divided_by(Number(0))
    assert isinstance(error, RTError)
    assert "Division by zero" in error.details


def test_number_comparisons_and_logic():
    assert Number(2).get_comparison_lt(Number(3))[0].value == 1
    assert Number(2).get_comparison_gte(Number(3))[0].value == 0
    assert Number(0).anded_by(Number(1))[0].value == 0
    assert Number(0).ored_by(Number(1))[0].value == 1
    assert Number(0).notted()[0].value == 1


def test_number_truthiness_and_formatting():
    assert Number(0).is_true() is False
    assert Number(1).is_true() is True
    assert str(Number(3)) == "3"
    assert str(Number(3.0)) == "3.0"
    assert str(Number(2.5)) == "2.5"


def test_number_of_a_string_is_an_illegal_operation():
    _, error = Number(1).added_to(String("a"))
    assert isinstance(error, RTError)
    assert error.details == "Illegal operation"


# -- strings ------------------------------------------------------------
def test_string_concatenation_and_repetition():
    assert String("ab").added_to(String("cd"))[0].value == "abcd"
    assert String("ab").multed_by(Number(2))[0].value == "abab"


def test_string_char_index():
    assert String("abc").divided_by(Number(1))[0].value == "b"
    _, error = String("abc").divided_by(Number(9))
    assert isinstance(error, RTError)


@pytest.mark.parametrize(
    "left,right,op,expected",
    [
        ("a", "a", "get_comparison_eq", 1),
        ("a", "b", "get_comparison_eq", 0),
        ("a", "b", "get_comparison_ne", 1),
        ("a", "b", "get_comparison_lt", 1),
        ("b", "a", "get_comparison_lt", 0),
        ("b", "a", "get_comparison_gt", 1),
        ("a", "a", "get_comparison_lte", 1),
        ("a", "a", "get_comparison_gte", 1),
    ],
)
def test_string_comparison(left, right, op, expected):
    result, error = getattr(String(left), op)(String(right))
    assert error is None
    assert result.value == expected


def test_string_is_never_equal_to_a_number():
    assert String("a").get_comparison_eq(Number(1))[0].value == 0
    assert String("a").get_comparison_ne(Number(1))[0].value == 1


def test_string_truthiness_is_length_based():
    assert String("").is_true() is False
    assert String("0").is_true() is True


# -- lists --------------------------------------------------------------
def test_list_append_does_not_mutate_the_original():
    original = List([Number(1)])
    result, error = original.added_to(Number(2))
    assert error is None
    assert [v.value for v in result.elements] == [1, 2]
    assert [v.value for v in original.elements] == [1]


def test_list_copy_shares_elements_so_mutation_builtins_write_through():
    original = List([Number(1)])
    copy = original.copy()
    copy.elements.append(Number(2))
    assert len(original.elements) == 2


def test_list_get_set_and_index_operator():
    values = List([Number(1), Number(2)])
    assert values.get(1).value == 2
    values.set(0, Number(9))
    assert values.elements[0].value == 9
    assert values.divided_by(Number(0))[0].value == 9


def test_list_index_out_of_bounds_is_an_error():
    _, error = List([]).divided_by(Number(0))
    assert isinstance(error, RTError)
    assert "index out of bounds" in error.details.lower()


def test_list_comparison_compares_length_to_number():
    assert List([Number(1)]).get_comparison_lt(Number(2))[0].value == 1


# -- handles ------------------------------------------------------------
class _Thing:
    def __init__(self):
        self.name = "thing"


def test_host_handle_copy_is_identity():
    thing = _Thing()
    handle = HostHandle(thing, kind="widget", label="knob")
    assert handle.copy() is handle
    assert handle.is_true() is True
    assert str(handle) == "knob"
    assert type_name(handle) == "widget"


def test_host_handle_equality_is_identity_based():
    thing = _Thing()
    assert value_eq(HostHandle(thing), HostHandle(thing)) is True
    assert value_eq(HostHandle(_Thing()), HostHandle(_Thing())) is False


def test_type_name_covers_every_value_kind():
    assert type_name(Number(1)) == "number"
    assert type_name(String("s")) == "string"
    assert type_name(List([])) == "list"
    assert type_name(Number(1).copy()) == "number"


# -- errors -------------------------------------------------------------
def test_illegal_operation_tolerates_values_without_positions():
    # A value that was never positioned must still produce an RTError.
    error = String("a").illegal_operation(Number(1))
    assert isinstance(error, RTError)
    assert error.details == "Illegal operation"


def test_value_eq_is_structural_for_lists():
    left = List([Number(1), String("a")])
    right = List([Number(1), String("a")])
    assert value_eq(left, right) is True
    right.elements.append(Number(2))
    assert value_eq(left, right) is False
