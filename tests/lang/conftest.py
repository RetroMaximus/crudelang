"""Fixtures for the language suite.

Every test gets a *fresh* ``Program`` (and therefore a fresh symbol table), so
no test can leak a variable into another one.
"""

from __future__ import annotations

import pytest

from crude.lang import Program


@pytest.fixture
def make_program():
    def _make(name="<test>"):
        return Program(name)

    return _make


@pytest.fixture
def run_src(make_program):
    """``run_src(source) -> (value, error)`` against a fresh program."""

    def _run(source, name="<test>"):
        return make_program(name).run(source)

    return _run


@pytest.fixture
def eval_src(run_src):
    """``eval_src(source) -> value``; fails the test on a parse/runtime error."""

    def _eval(source):
        value, error = run_src(source)
        assert error is None, error.as_string()
        return value

    return _eval


@pytest.fixture
def failure(run_src):
    """``failure(source) -> Error``; fails the test when the program succeeds."""

    def _failure(source):
        value, error = run_src(source)
        assert error is not None, f"expected an error, got {value!r}"
        return error

    return _failure
