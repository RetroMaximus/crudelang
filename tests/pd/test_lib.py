"""The typed signature table: the one thing that silently kills a process.

A ctypes call without ``argtypes`` corrupts the stack instead of raising, so
the contract is pinned statically: every symbol the wrapper touches must be
declared, every declaration must be fully typed, and the fake must implement
exactly the same protocol.
"""

from __future__ import annotations

import ctypes
import inspect
import re

import pytest

from crude.pd import FakeLibpd, Libpd, LibpdNotFound, find_library
from crude.pd import hooks, lib


def test_signatures_cover_every_wrapper_call():
    """No ``self.api.libpd_*`` call may exist outside :data:`SIGNATURES`."""
    source = inspect.getsource(lib)
    used = set(re.findall(r"api\.(libpd_\w+)", source))
    assert used, "the regex should find the wrapper's API calls"
    undeclared = sorted(used - set(lib.SIGNATURES))
    assert undeclared == [], f"undeclared libpd calls: {undeclared}"


def test_every_signature_is_fully_typed():
    for name, (restype, argtypes) in lib.SIGNATURES.items():
        assert name.startswith("libpd_"), name
        assert isinstance(argtypes, list), name
        for argtype in argtypes:
            assert isinstance(argtype, type) or hasattr(argtype, "_type_"), (
                name,
                argtype,
            )
        assert restype is None or isinstance(restype, type) or hasattr(restype, "_type_"), (
            name,
            restype,
        )


def test_pointer_arguments_are_never_bare_c_void_p_for_atoms():
    """Atom pointer params are ``POINTER(None)``; float buffers are ``P_FLOAT``."""
    assert lib.SIGNATURES["libpd_read_array"][1][0] is lib.P_FLOAT
    assert lib.SIGNATURES["libpd_write_array"][1][2] is lib.P_FLOAT
    assert lib.SIGNATURES["libpd_process_float"][1][1:] == [lib.P_FLOAT, lib.P_FLOAT]


def test_hook_setters_match_hook_kinds():
    assert set(lib.HOOK_SETTERS) == set(hooks.HOOK_TYPES)


def test_hook_sigs_are_void_with_a_callback_pointer():
    for setter in lib.HOOK_SETTERS.values():
        restype, argtypes = lib.SIGNATURES[setter]
        assert restype is None, setter
        assert argtypes == [ctypes.c_void_p], setter


def test_fake_implements_the_libpd_protocol():
    """``FakeLibpd`` must be a duck-typed stand-in for ``Libpd``."""
    def public(cls):
        return {
            name
            for name, member in inspect.getmembers(cls, inspect.isfunction)
            if not name.startswith("_")
        }

    missing = public(Libpd) - public(FakeLibpd) - {"load"}
    assert missing == set(), f"FakeLibpd is missing {sorted(missing)}"


def test_fake_covers_every_hook_kind():
    fake = FakeLibpd()
    for kind, setter in lib.HOOK_SETTERS.items():
        fake.set_hook(kind, lambda *args: None)
        assert fake.has_hook(kind), setter


def test_find_library_accepts_an_explicit_path(tmp_path):
    fake_dll = tmp_path / "libpd.dll"
    fake_dll.write_bytes(b"")
    assert find_library(str(fake_dll)) == str(fake_dll.resolve())


def test_find_library_reports_a_missing_library(monkeypatch):
    monkeypatch.setattr(lib, "_candidate_paths", lambda: [])
    monkeypatch.delenv("CRUDE_LIBPD_PATH", raising=False)
    with pytest.raises(LibpdNotFound):
        find_library("/nonexistent/libpd.dll")


def test_missing_symbols_detects_a_partial_api():
    class Partial:
        def __init__(self):
            self.libpd_init = lambda: 0

    missing = lib.missing_symbols(Partial())
    assert "libpd_init" not in missing
    assert "libpd_process_float" in missing
