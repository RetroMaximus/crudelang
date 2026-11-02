"""Program assembly, per-program isolation and the ``crudelang`` shim."""

from __future__ import annotations

import crudelang
from crude.lang import Number, Program, default_symbol_table, reset_default_symbol_table
from helpers import as_py

from crude.lang import run as legacy_run


def test_two_programs_do_not_share_variables(make_program):
    first = make_program()
    second = make_program()

    first.run("VAR only_in_first = 1")
    _, error = second.run("only_in_first")

    assert error is not None
    assert "not defined" in error.details


def test_program_state_persists_across_runs(make_program):
    program = make_program()
    program.run("VAR counter = 41")
    value, error = program.run("counter + 1")
    assert error is None
    assert as_py(value) == 42


def test_explicit_symbol_table_is_used(make_program):
    from crude.lang import SymbolTable

    table = SymbolTable()
    table.set("preset", Number(7))
    program = Program("<test>", symbol_table=table)
    value, error = program.run("preset * 2")
    assert error is None
    assert as_py(value) == 14


def test_legacy_run_shares_one_symbol_table():
    reset_default_symbol_table()
    try:
        legacy_run("<stdin>", "VAR shared = 5")
        value, error = legacy_run("<stdin>", "shared")
        assert error is None
        assert as_py(value) == [5]

        legacy_run("<stdin>", "VAR shared = 6")
        value, _ = legacy_run("<stdin>", "shared")
        assert as_py(value) == [6]
    finally:
        reset_default_symbol_table()


def test_reset_default_symbol_table_drops_state():
    legacy_run("<stdin>", "VAR temp = 1")
    reset_default_symbol_table()
    _, error = legacy_run("<stdin>", "temp")
    assert error is not None


def test_default_symbol_table_is_a_singleton():
    reset_default_symbol_table()
    try:
        assert default_symbol_table() is default_symbol_table()
    finally:
        reset_default_symbol_table()


def test_program_reports_parse_errors_without_raising(make_program):
    value, error = make_program().run("VAR = 1")
    assert value is None
    assert error.error_name == "Invalid Syntax"


def test_program_reports_runtime_errors_without_raising(make_program):
    value, error = make_program().run("nope")
    assert value is None
    assert error.error_name == "Runtime Error"


def test_parse_returns_node_or_none(make_program):
    program = make_program()
    node, error = program.parse("1 + 1")
    assert error is None and node is not None

    node, error = program.parse("1 +")
    assert node is None and error is not None


def test_run_file_reads_and_renames(tmp_path, make_program):
    script = tmp_path / "hello.crude"
    script.write_text('PRINT("from file")', encoding="utf-8")

    program = make_program()
    value, error = program.run_file(str(script))
    assert error is None
    assert program.name == str(script)


def test_empty_source_yields_null(make_program):
    value, error = make_program().run("")
    assert error is None
    assert as_py(value) == 0


def test_execute_returns_the_statement_list(make_program):
    value, error = make_program().execute("VAR a = 1\na + 1")
    assert error is None
    assert as_py(value) == [1, 2]


# -- the compatibility shim -------------------------------------------
def test_shim_reexports_the_language_surface():
    for name in ("run", "Lexer", "Parser", "Interpreter", "Number", "String", "List", "Value", "Program"):
        assert hasattr(crudelang, name), name

def test_shim_global_symbol_table_is_the_shared_table():
    reset_default_symbol_table()
    try:
        assert crudelang.global_symbol_table is default_symbol_table()
    finally:
        reset_default_symbol_table()


def test_shim_run_returns_the_statement_list():
    reset_default_symbol_table()
    try:
        value, error = crudelang.run("<stdin>", "1 + 1")
        assert error is None
        assert as_py(value) == [2]

        value, error = crudelang.run("<stdin>", "boom")
        assert value is None
        assert error is not None
    finally:
        reset_default_symbol_table()


def test_shim_keeps_repl_state_like_the_original():
    reset_default_symbol_table()
    try:
        crudelang.run("<stdin>", "VAR k = 4")
        value, error = crudelang.run("<stdin>", "k * 2")
        assert error is None
        assert as_py(value) == [8]
    finally:
        reset_default_symbol_table()


# -- parity with the historical behaviour ----------------------------
def test_testscript_crude_behaves_like_the_original(repo_root, capsys):
    program = Program("testscript.crude")
    value, error = program.run_file(str(repo_root / "testscript.crude"))
    assert error is None

    out = capsys.readouterr().out.splitlines()
    assert out[0] == "loop"
    assert out[1] == "WE ARE JOINING loop, spoop!!!"
    assert out[2:] == ["loop, spoop"] * 5
