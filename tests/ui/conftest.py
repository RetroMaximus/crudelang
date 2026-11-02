"""Fixtures for the UI tests: nothing here owns a display.

Every file in this directory runs against :mod:`crude.ui`'s GL-free half. The
tests that do need a window and a context are in :mod:`test_gl_renderer` and
:mod:`test_gl_window` and carry ``requires_gl``, which ``pytest.ini`` deselects
by default, so ``pytest`` on a machine with no GPU is a full green run.

The rules the rest of the repository follows hold here too: no ``sleep``, no
network, ``tmp_path`` for anything written, and no state shared between tests -
each test gets its own renderer object.
"""

from __future__ import annotations

import pytest

from crude.ui import NullRenderer, RecordingRenderer


@pytest.fixture
def recording() -> RecordingRenderer:
    """A renderer, mid-frame on a 320 x 200 logical surface at 2x."""
    renderer = RecordingRenderer()
    renderer.begin(320, 200, 640, 400)
    return renderer


@pytest.fixture
def quiet() -> NullRenderer:
    """The headless renderer, also already in a frame."""
    renderer = NullRenderer()
    renderer.begin(320, 200, 640, 400)
    return renderer


@pytest.fixture
def bare() -> RecordingRenderer:
    """A renderer with no frame begun, for the calls that must refuse one."""
    return RecordingRenderer()
