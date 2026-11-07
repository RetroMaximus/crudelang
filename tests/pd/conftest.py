"""Fixtures for the Pd suite.

Everything here runs on ``FakeLibpd`` unless a test asks for the real library
through the ``requires_libpd`` marker, so the default test run needs neither a
DLL nor an audio device.
"""

from __future__ import annotations

import types

import pytest

from crude.lang import Program
from crude.pd import (
    FakeLibpd,
    Libpd,
    LibpdNotFound,
    PdRuntime,
    PdSession,
    find_library,
)
from crude.ui import NullRenderer, UIContext

PATCH_TEXT = """#N canvas 0 0 300 300 12;
#X obj 40 40 r freq;
#X obj 40 80 osc~ 440;
#X obj 40 120 *~ 0.3;
#X obj 40 160 dac~;
#X obj 200 40 s tick;
#X obj 200 80 table wave 16;
#X connect 1 0 2 0;
#X connect 2 0 3 0;
#X connect 2 0 3 1;
"""


@pytest.fixture
def patch_text():
    """A tiny but valid hand-written patch."""
    return PATCH_TEXT


@pytest.fixture
def patch_file(tmp_path, patch_text):
    """Write :data:`PATCH_TEXT` to ``tmp_path`` and return the path."""
    path = tmp_path / "tone.pd"
    path.write_text(patch_text, encoding="utf-8")
    return path


@pytest.fixture
def fake_lib():
    return FakeLibpd()


@pytest.fixture
def session(fake_lib):
    session = PdSession(fake_lib)
    session.start()
    yield session
    session.shutdown()


@pytest.fixture
def runtime(session, tmp_path):
    return PdRuntime(session, project_dir=str(tmp_path))


@pytest.fixture
def make_program():
    """A bare program with no host: the PD_* bindings must complain clearly."""

    def _make(name="<test>"):
        return Program(name)

    return _make


@pytest.fixture
def pd_program(runtime):
    """A ``Program`` wired to the fake-backed runtime (``host.pd``)."""
    return Program("<pd-test>", host=types.SimpleNamespace(pd=runtime))


@pytest.fixture
def run_pd(pd_program):
    """``run_pd(source) -> (value, error)`` against the wired program."""

    def _run(source):
        return pd_program.run(source)

    return _run


@pytest.fixture
def eval_pd(run_pd):
    """``eval_pd(source) -> value``; fails the test on an error."""

    def _eval(source):
        value, error = run_pd(source)
        assert error is None, error.as_string()
        return value

    return _eval


@pytest.fixture
def pd_failure(run_pd):
    """``pd_failure(source) -> Error``; fails the test when it succeeds."""

    def _failure(source):
        value, error = run_pd(source)
        assert error is not None, f"expected an error, got {value!r}"
        return error

    return _failure


@pytest.fixture
def stage(tmp_path):
    """A program holding both halves: ``host.pd`` on the fake, a headless ``host.ui``.

    ``PD_EMIT`` is the one Pd binding that takes a widget, so it is the one that
    needs a tree to read a box and a ``pd_object`` off. ``NullRenderer`` keeps that
    GL-free, and the log is a list, because the one case this binding reports
    instead of failing - a widget with no Pd box - is told through it.
    """
    notes = []
    session = PdSession(FakeLibpd())
    session.start()
    runtime = PdRuntime(session, project_dir=str(tmp_path), log=notes.append)
    context = UIContext(NullRenderer(), width=320, height=200)
    program = Program("<pd-ui-test>",
                      host=types.SimpleNamespace(pd=runtime, ui=context))
    yield types.SimpleNamespace(program=program, runtime=runtime, ui=context,
                                notes=notes)
    session.shutdown()


@pytest.fixture
def run_emit(stage):
    """``run_emit(source) -> (value, error)`` against the two-subsystem program."""

    def _run(source):
        return stage.program.run(source)

    return _run


@pytest.fixture
def eval_emit(run_emit):
    """``eval_emit(source) -> value``; fails the test on an error."""

    def _eval(source):
        value, error = run_emit(source)
        assert error is None, error.as_string()
        return value

    return _eval


@pytest.fixture
def emit_failure(run_emit):
    """``emit_failure(source) -> Error``; fails the test when it succeeds."""

    def _failure(source):
        value, error = run_emit(source)
        assert error is not None, f"expected an error, got {value!r}"
        return error

    return _failure


# -- the real library ---------------------------------------------------
# Only reached by tests marked ``requires_libpd``: an unmarked run never loads
# the DLL. Shared, because both the engine port and patch authoring need it.


@pytest.fixture(scope="module")
def api():
    try:
        find_library()
    except LibpdNotFound as exc:  # pragma: no cover - depends on the machine
        pytest.skip(str(exc))
    return Libpd.load()


@pytest.fixture
def live(api):
    session = PdSession(api).start()
    yield session
    session.shutdown()


@pytest.fixture
def live_runtime(api, tmp_path):
    """A ``PdRuntime`` over the real library, wired to a program (``host.pd``)."""
    session = PdSession(api).start()
    runtime = PdRuntime(session, project_dir=str(tmp_path))
    program = Program("<live-pd>", host=types.SimpleNamespace(pd=runtime))
    runtime.attach_program(program)
    yield runtime, program
    session.shutdown()


@pytest.fixture
def live_emit(api, tmp_path):
    """The same, with a headless ``host.ui`` beside it, and its log kept.

    ``PD_EMIT`` needs a widget to twin, so the real-library check for an emitted
    box has to hold both halves - the shape :func:`stage` gives the fake. The size
    is the plan's front panel, because the one panel the repository ships is the
    one the metronome example places.
    """
    notes = []
    session = PdSession(api).start()
    runtime = PdRuntime(session, project_dir=str(tmp_path), log=notes.append)
    context = UIContext(NullRenderer(), width=360, height=220)
    program = Program("<live-emit>",
                      host=types.SimpleNamespace(pd=runtime, ui=context))
    yield runtime, program, notes
    session.shutdown()
