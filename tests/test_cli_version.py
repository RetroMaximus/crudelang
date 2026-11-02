"""S0: the CLI exists, prints a version and exits 0."""

from __future__ import annotations

import subprocess
import sys

import pytest

import crude
from crude.cli import main


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert crude.__version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "usage:" in out
    assert "command" in out


def test_module_entry_point_runs() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "crude", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0
    assert crude.__version__ in completed.stdout
