"""Shared pytest fixtures and import bootstrap.

Rules that hold for every test in this repository:

* the repository root is importable, so ``import crude`` and ``import crudelang``
  work no matter where pytest was started from;
* nothing here touches audio hardware, a GL context, libpd or a serial port -
  those are opt-in through the markers declared in ``pytest.ini``;
* state is never shared between tests: each program builds its own symbol table
  and each project lives in its own ``tmp_path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository checkout under test."""
    return REPO_ROOT


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """An empty directory usable as a crude project root."""
    root = tmp_path / "project"
    root.mkdir()
    return root
