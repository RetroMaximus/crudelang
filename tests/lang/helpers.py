"""Shared helpers for the ``tests/lang`` suite."""

from __future__ import annotations

from crude.lang import List, Number, String


def as_py(value):
    """Convert a runtime value into the plainest Python equivalent."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, List):
        return [as_py(element) for element in value.elements]
    return value


def numbers(*values):
    return [Number(value) for value in values]
