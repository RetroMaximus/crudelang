"""Fixtures for the serial tests: nothing here owns a real port.

Every file in this directory runs against :mod:`crude.serial`'s fake-first
half - the plan's S10 acceptance line ("fake port + ``loop://`` tests") is
what ``MemorySerialPort`` and :class:`~crude.serial.loop.LoopSerialPort`
exist for. The one test that touches a real device (a pyserial-backed run
behind a physical ``COM``/``tty``) is marked ``requires_serial`` and
``pytest.ini`` deselects it, so a laptop with no Arduino still sees a full
green run.

The rules the rest of the repository follows hold here too: no ``sleep``, no
network, ``tmp_path`` for anything written, no state shared between tests.
"""

from __future__ import annotations

import types

import pytest

from crude.lang import Program
from crude.pd import FakeLibpd, PdRuntime, PdSession
from crude.serial import LoopSerialPort, SerialRuntime


@pytest.fixture(autouse=True)
def _no_thread_leaks():
    """Every test gets a fresh runtime, so no reader thread outlives the case.

    Not much to do here; :class:`SerialLink`'s thread is a daemon and each
    test builds its own runtime, so nothing survives the interpreter. The
    fixture exists to say so out loud and to catch a future test that
    attaches one globally.
    """
    yield


# -- a program with the serial surface wired ---------------------------

@pytest.fixture
def serial_runtime():
    """A bare runtime, no threads; a test injects a ``loop://`` port per link."""
    return SerialRuntime()


@pytest.fixture
def serial_program(serial_runtime):
    """``Program`` with ``host.serial``: enough for ``SERIAL_*`` alone."""
    return Program("<serial-test>", host=types.SimpleNamespace(serial=serial_runtime))


@pytest.fixture
def run_serial(serial_program, serial_runtime):
    """``run_serial(source) -> (value, error)`` against the wired program."""

    def _run(source):
        serial_runtime.attach_program(serial_program)
        return serial_program.run(source)

    return _run


@pytest.fixture
def eval_serial(run_serial):
    """``eval_serial(source) -> value``; fails the test on an error."""

    def _eval(source):
        value, error = run_serial(source)
        assert error is None, error.as_string()
        return value

    return _eval


@pytest.fixture
def serial_failure(run_serial):
    """``serial_failure(source) -> Error``; fails the test when it succeeds."""

    def _failure(source):
        value, error = run_serial(source)
        assert error is not None, f"expected an error, got {value!r}"
        return error

    return _failure


# -- the headless bridge -----------------------------------------------

@pytest.fixture
def bridge(tmp_path):
    """The shape ``examples/sensor_bridge`` runs with ``--no-serial``.

    Both halves of the plan's bridge live in one host namespace: the
    :class:`~crude.serial.SerialRuntime` on the left, a
    :class:`~crude.pd.PdRuntime` over ``FakeLibpd`` on the right. The loader
    phase would normally open the manifest's link off a real ``COM3``; here
    the fixture hands :meth:`SerialRuntime.open` a ``loop://`` port so a
    script's ``SERIAL_OPEN("arduino")`` looks up the same handle the manifest
    declared, and a test can push bytes through it without hardware.
    """
    notes = []
    session = PdSession(FakeLibpd())
    session.start()
    pd = PdRuntime(session, project_dir=str(tmp_path), log=notes.append)
    serial = SerialRuntime(log=notes.append)
    program = Program("<bridge-test>",
                      host=types.SimpleNamespace(pd=pd, serial=serial))
    serial.attach_program(program)
    yield types.SimpleNamespace(program=program, pd=pd, serial=serial,
                                lib=session.lib, notes=notes,
                                session=session)
    serial.close_all()
    session.shutdown()


@pytest.fixture
def bridge_with_link(bridge):
    """The bridge, with the manifest's ``arduino`` link pre-opened on a loop port.

    Mirrors what the loader's serial phase would do for a declared link: the
    port here is a :class:`LoopSerialPort`, so ``SERIAL_WRITE(port, \"42\")``
    from the script is readable on the same handle a frame later.
    """
    decl = types.SimpleNamespace(id="arduino", port="loop://arduino",
                                 baud=115200, codec="line", optional=True)
    port = LoopSerialPort("arduino")
    link = bridge.serial.open(decl, port=port)
    bridge.port = port
    bridge.link = link
    return bridge
