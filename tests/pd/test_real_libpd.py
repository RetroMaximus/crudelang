"""The real library: the stage-2 probe, ported to a repeatable test.

Deselected by default (``-m "not requires_libpd"``); run with
``pytest -m requires_libpd tests/pd`` on a machine that has ``libs/libpd.dll``.
"""

from __future__ import annotations

import ctypes

import pytest

from crude.lang import Program
from crude.pd import PdRuntime, PdSession
from crude.pd.engine import PdError

pytestmark = pytest.mark.requires_libpd

# ``api`` and ``live`` come from tests/pd/conftest.py, where the authoring tests
# use them too.


def test_a_second_session_starts_on_the_same_library(api):
    """libpd is a process-wide singleton: ``init`` answers 0 once, then -1.

    Every session after the first therefore sees a non-zero status that is not
    a failure, and ``PdSession.start`` has to treat it as success - otherwise
    the second patch of a run would never load.
    """
    first = PdSession(api).start()

    assert api.init() == -1
    first.shutdown()

    second = PdSession(api).start()
    assert api.blocksize() == 64
    assert second.blocksize_value == 64
    second.shutdown()


def test_open_file_returns_a_handle_and_a_dollar_zero(api, patch_file):
    api.init()
    handle = api.open_file(patch_file.name, str(patch_file.parent))
    assert handle
    assert api.dollar_zero(handle) > 0
    assert api.close_file(handle) == 0


def test_open_file_of_a_missing_patch_is_caught_before_libpd(api, tmp_path):
    """Never reach ``libpd_openfile`` with a path that is not there.

    On this build it does not return ``NULL`` - it faults the process, and a
    script with a typo in a path must not be able to kill the runtime.
    """
    api.init()
    session = PdSession(api).start()

    with pytest.raises(PdError) as excinfo:
        session.open_patch("ghost", "nope.pd", str(tmp_path))
    assert "does not exist" in str(excinfo.value)
    session.shutdown()


def test_bind_and_float_fire_the_float_hook(live, patch_file):
    patch = live.open_patch("tone", patch_file.name, str(patch_file.parent))
    live.bind("probe")
    live.send_float("probe", 42.5)
    live.send_bang("probe")
    live.process(1, *buffers(live))

    events = live.drain_events()
    floats = [event for event in events if event.kind == "float"]
    assert floats and floats[0].recv == "probe"
    assert floats[0].args[0] == pytest.approx(42.5)
    assert any(event.kind == "bang" for event in events)
    assert patch.handle


def test_process_float_renders_the_patch(live, patch_file):
    live.open_patch("tone", patch_file.name, str(patch_file.parent))
    live.dsp(True)

    samples = live.process_offline(ticks=4)
    assert len(samples) == live.blocksize_value * 4 * live.out_channels
    assert any(abs(sample) > 0.0 for sample in samples), "the tone should be audible"


def test_array_round_trip(live, patch_file):
    patch = live.open_patch("tone", patch_file.name, str(patch_file.parent))
    name = f"{patch.dollar_zero}-wave"

    live.lib.resize_array(name, 8)
    live.array_write(patch, "wave", [0.0, 0.5, 1.0], 0)

    assert live.array_size(patch, "wave") >= 8
    assert live.array_read(patch, "wave", 0, 3) == [0.0, 0.5, 1.0]


def test_script_surface_on_the_real_library(api, patch_file):
    """The same script that runs on the fake must run here."""
    import types

    session = PdSession(api).start()
    runtime = PdRuntime(session, project_dir=str(patch_file.parent))
    program = Program("<live>", host=types.SimpleNamespace(pd=runtime))

    _, error = program.run(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\n'
        'PD_SEND(p, "freq", 440)\n'
        'FUNC echo(v) -> PD_SEND(p, "out", v)\n'
        'ON_PD(p, "probe", echo)\n'
        "PD_DSP(1)"
    )
    assert error is None, error.as_string() if error else ""

    assert session.process_offline(2) is not None

    # Bind a receiver ourselves, post one float, render a block: the handler
    # must be called once with the value Pd delivered.
    session.bind("probe")
    session.send_float("probe", 3.5)
    session.process(1, *buffers(session))
    delivered = runtime.dispatch(session.drain_events())

    assert runtime.errors == []
    assert delivered >= 1
    assert runtime.handlers[0].calls == delivered

    runtime.flush()
    session.shutdown()


def buffers(session):
    size = session.blocksize_value * max(session.out_channels, 1)
    return (ctypes.c_float * size)(), (ctypes.c_float * size)()
