"""An omitted binding argument is the binding's default, not the caller's variable.

A binding call runs in its own symbol table whose parent is the caller's, so a
name the call never passed used to be looked *up* and found two scopes away. With
``VAR level = 3`` in the script, every bare ``LOG("hi")`` failed with "argument
'level' must be a string, got number", and ``VAR code = 7`` made ``EXIT()`` end
the process with 7. ``BuiltInFunction.execute`` now records which names the call
passed and :class:`~crude.bindings.registry.HostContext` refuses to read any
other name as an argument.

This is pinned here rather than left to the bindings' own tests because every
widget binding in the next stage has optional arguments called ``text``, ``x``,
``y``, ``value`` and ``size`` - exactly the names a script uses for its own
variables. The calls in a ``.crude`` program must not change shape because of a
variable the script happens to own.
"""

from __future__ import annotations

import types

import pytest

from crude.bindings.registry import Binding, HostContext
from crude.lang import Program, build_symbol_table
from crude.lang.interpreter import Context
from crude.lang.values import Number, String

#: the two answers the probe below gives: the argument, or its own default
GIVEN = "given:"
DEFAULT = "default"


def _probe(ctx):
    """Say which of its two arguments this call actually passed."""
    if ctx.has("extra"):
        return String(f"{GIVEN}{ctx.get('extra')}")
    return String(DEFAULT)


#: one optional argument, so a call may pass one, and the other may be captured
PROBE = Binding("PROBE", ("value", "extra"), _probe, area="test", optional=1)


def script(source: str, *bindings: Binding):
    """Run ``source`` against a program whose globals hold only these bindings."""
    table = build_symbol_table()
    for entry in bindings or (PROBE,):
        table.set(entry.name, entry.to_builtin())
    value, error = Program("<binding args>", symbol_table=table).run(source)
    assert error is None, error.as_string()
    return value.value if isinstance(value, String) else value


def refused(source: str) -> str:
    """The message a program gets when a binding call is not legal."""
    table = build_symbol_table()
    table.set(PROBE.name, PROBE.to_builtin())
    _, error = Program("<binding args>", symbol_table=table).run(source)
    assert error is not None, "the source was expected to be refused"
    return error.as_string()


# -- the captured argument ------------------------------------------------


def test_a_variable_of_the_same_name_is_not_the_omitted_argument():
    assert script('VAR extra = 3\nPROBE(1)') == DEFAULT


def test_an_omitted_argument_arrives_as_null_rather_than_as_the_variable():
    # ``get`` is what every coercion goes through, so it answers the same way
    assert script('VAR extra = "loud"\nSTR(PROBE(1))') == DEFAULT


def test_the_argument_a_call_does_pass_is_the_one_the_binding_sees():
    assert script('VAR extra = 3\nPROBE(1, "loud")') == f"{GIVEN}loud"


def test_a_variable_handed_to_the_argument_is_still_a_normal_variable():
    # the fix is about names that were *not* passed, not about reading variables
    assert script('VAR extra = 3\nPROBE(1, extra)') == f"{GIVEN}3"


def test_a_binding_call_inside_a_function_is_no_different():
    source = (
        "FUNC talk()\n"
        "    VAR extra = 9\n"
        "    RETURN PROBE(1)\n"
        "END\n"
        "STR(talk())\n"
    )
    assert script(source) == DEFAULT


def test_the_arguments_a_full_call_passes_are_all_provided():
    assert script('VAR value = 2\nPROBE(value)') == DEFAULT
    assert script("PROBE(2, 5)") == f"{GIVEN}5"


def test_a_required_argument_that_was_not_given_is_still_refused():
    assert "too few args" in refused("PROBE()")


def test_a_variable_the_binding_never_asked_for_is_still_the_programs_own():
    # the script loses nothing: its variables are its own, as they were
    assert script('VAR extra = 3\nSTR(extra + 1)') == "4"


# -- what the context can still see ---------------------------------------


def test_the_callers_scope_is_reachable_when_a_binding_asks_for_it_on_purpose():
    """The fall-through is gone from ``get``/``has``, not from the interpreter.

    ``symbol_table`` is still the call's own table, so a binding that genuinely
    wants the program's variables can look them up by name and say so in its own
    code, instead of every omitted argument doing it by accident.
    """
    inner = build_symbol_table()
    inner.parent = build_symbol_table()
    inner.parent.set("extra", Number(3))
    ctx = HostContext(types.SimpleNamespace(provided_args=frozenset({"value"}),
                                           symbol_table=inner), PROBE)

    assert ctx.has("extra") is False
    assert ctx.get("extra") == Number.null
    assert inner.get("extra").value == 3


@pytest.mark.parametrize("provided,expected", [
    (frozenset({"value"}), False),
    (frozenset({"value", "extra"}), True),
    (None, True),
])
def test_the_context_asks_the_call_which_arguments_it_passed(provided, expected):
    """``provided`` is the whole question, and ``None`` means "not a binding".

    A script function's context never records an argument set, because a
    ``Function`` has no optional arguments: everything it declares is bound.
    """
    ctx = HostContext(types.SimpleNamespace(provided_args=provided,
                                            symbol_table=build_symbol_table()),
                      PROBE)
    assert ctx.provided("extra") is expected


def test_a_context_from_the_interpreter_carries_no_argument_set():
    """Only a binding call marks arguments as provided - the rest is scope."""
    assert Context("<test>").provided_args is None


def test_the_binding_declares_which_of_its_arguments_may_be_left_out():
    assert PROBE.arg_names == ("value", "extra")
    assert PROBE.optional == 1
