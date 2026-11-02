"""The script-facing runtime: handlers, muting, arrays and patches.

These tests exercise the pieces of :class:`crude.pd.PdRuntime` that the
``PD_*`` bindings sit on, without going through the language.
"""

from __future__ import annotations

import pytest

from crude.pd import FakeLibpd, PdRuntime, PdSession
from crude.pd.engine import PdError
from crude.pd.inbox import PdEvent


class RecordingProgram:
    """Stands in for ``Program``: records invocations, can always fail."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def invoke(self, func, args=()):
        self.calls.append((func, list(args)))
        if self.fail:
            from crude.lang.errors import RTError

            return None, RTError(None, None, "handler exploded", None)
        return None, None


@pytest.fixture
def wired(patch_file):
    lib = FakeLibpd()
    session = PdSession(lib).start()
    runtime = PdRuntime(session, project_dir=str(patch_file.parent))
    program = RecordingProgram()
    runtime.attach_program(program)
    yield runtime, session, lib, program
    session.shutdown()


def test_open_file_registers_the_patch(wired, patch_file):
    runtime, session, lib, _ = wired
    patch = runtime.open_file("tone", str(patch_file))

    assert runtime.patches == {"tone": patch}
    assert patch.name == "tone.pd"
    assert str(patch_file.parent) in lib.search_paths


def test_open_file_rejects_a_missing_patch(wired):
    runtime, _, _, _ = wired
    with pytest.raises(PdError, match="does not exist"):
        runtime.open_file("tone", "nope/missing.pd")


def test_open_file_rejects_a_duplicate_id(wired, patch_file):
    runtime, _, _, _ = wired
    runtime.open_file("tone", str(patch_file))
    with pytest.raises(PdError, match="already open"):
        runtime.open_file("tone", str(patch_file))


def test_close_removes_the_patch_and_its_handlers(wired, patch_file):
    runtime, _, _, _ = wired
    runtime.open_file("tone", str(patch_file))
    runtime.on_pd("tone", "freq", object())

    assert runtime.close("tone") is True
    assert runtime.patches == {}
    assert runtime.handlers == []


def test_float_events_reach_the_handler(wired):
    runtime, _, _, program = wired
    handler_func = object()
    runtime.on_pd("tone", "freq", handler_func)

    delivered = runtime.dispatch([PdEvent("float", "freq", (440.5,))])

    assert delivered == 1
    assert program.calls == [(handler_func, [440.5])]


def test_bang_events_call_the_handler_without_arguments(wired):
    runtime, _, _, program = wired
    runtime.on_pd("tone", "tick", "handler")

    runtime.dispatch([PdEvent("bang", "tick")])

    assert program.calls == [("handler", [])]


def test_events_for_other_receivers_are_ignored(wired):
    runtime, _, _, program = wired
    runtime.on_pd("tone", "freq", "handler")

    assert runtime.dispatch([PdEvent("float", "other", (1.0,))]) == 0
    assert program.calls == []


def test_kind_filter_narrows_the_handler(wired):
    runtime, _, _, program = wired
    runtime.on_pd("tone", "freq", "handler", kinds=("bang",))

    runtime.dispatch([PdEvent("float", "freq", (1.0,))])
    runtime.dispatch([PdEvent("bang", "freq")])

    assert program.calls == [("handler", [])]


def test_off_pd_removes_one_or_every_handler(wired):
    runtime, _, _, _ = wired
    first, second = object(), object()
    runtime.on_pd("tone", "freq", first)
    runtime.on_pd("tone", "freq", second)

    assert runtime.off_pd("tone", "freq", first) == 1
    assert runtime.off_pd("tone", "freq") == 1


def test_print_events_go_to_the_log_not_to_handlers(wired):
    runtime, _, _, program = wired
    logged = []
    runtime.log = logged.append
    runtime.on_pd("tone", "freq", "handler")

    runtime.dispatch([PdEvent("print", "hello from pd")])

    assert logged == ["[pd] hello from pd"]
    assert program.calls == []


def test_a_failing_handler_is_muted_after_three_errors(wired):
    runtime, _, _, program = wired
    program.fail = True
    handler = runtime.on_pd("tone", "freq", "handler")

    for _ in range(5):
        runtime.dispatch([PdEvent("float", "freq", (1.0,))])

    assert handler.errors >= 3
    assert handler.muted is True
    assert len(runtime.errors) == 3        # no calls once muted
    assert program.calls and len(program.calls) == 3


def test_a_handler_recovers_after_a_success(wired):
    runtime, _, _, program = wired
    program.fail = True
    handler = runtime.on_pd("tone", "freq", "handler")
    runtime.dispatch([PdEvent("float", "freq", (1.0,))])
    assert handler.errors == 1

    program.fail = False
    runtime.dispatch([PdEvent("float", "freq", (2.0,))])

    assert handler.errors == 0
    assert handler.muted is False


def test_dispatch_drains_the_session_inbox(wired):
    runtime, session, lib, program = wired
    runtime.on_pd("tone", "freq", "handler")
    session.inbox.push(PdEvent("float", "freq", (3.0,)))

    assert runtime.dispatch() == 1
    assert session.inbox.pending == 0
    assert program.calls == [("handler", [3.0])]


def test_send_chooses_the_libpd_call_by_value_type(wired):
    runtime, _, lib, _ = wired
    runtime.send("tone", "freq", 440.0)
    runtime.send("tone", "note", "c4")
    runtime.send("tone", "seq", [1.0, 2.0])
    runtime.flush()

    assert lib.sent == [
        ("float", "freq", 440.0),
        ("symbol", "note", "c4"),
        ("list", "seq", (1.0, 2.0)),
    ]


def test_send_message_passes_the_selector(wired):
    runtime, _, lib, _ = wired
    runtime.send_message("tone", "seq", "set", [800.0])
    runtime.flush()

    assert lib.sent == [("message", "seq", "set", (800.0,))]


def test_midi_goes_through_the_scheduler(wired):
    runtime, _, lib, _ = wired
    runtime.send_midi("noteon", 0, 60, 100)
    runtime.send_midi("noteoff", 0, 60)
    runtime.flush()

    assert lib.midi == [("noteon", 0, 60, 100), ("noteon", 0, 60, 0)]


def test_flush_runs_queued_jobs_without_audio(wired):
    runtime, session, lib, _ = wired
    runtime.send_bang("tone", "tick")

    assert runtime.flush() == 1
    assert lib.sent == [("bang", "tick")]
    assert session.scheduler.pending == 0


def test_patch_lookup_reports_a_missing_patch(wired):
    runtime, _, _, _ = wired
    with pytest.raises(PdError, match="not loaded"):
        runtime.patch("nope")


def test_arrays_resolve_dollar_zero_names(wired, patch_file):
    runtime, _, lib, _ = wired
    patch = runtime.open_file("tone", str(patch_file))
    lib.add_array(f"{patch.dollar_zero}-wave", [0.0, 0.0])

    runtime.array_write("tone", "wave", [0.5, -0.5], 0)

    assert runtime.array_read("tone", "wave") == [0.5, -0.5]
    assert runtime.array_size("tone", "wave") == 2


def test_dsp_and_exists_reach_the_session(wired):
    runtime, session, lib, _ = wired
    runtime.dsp(True)
    lib.add_receiver("freq")

    assert session.dsp_on is True
    assert runtime.exists("freq") is True
    assert runtime.exists("nothing") is False


def test_repr_lists_patches_and_handlers(wired, patch_file):
    runtime, _, _, _ = wired
    runtime.open_file("tone", str(patch_file))
    runtime.on_pd("tone", "freq", "handler")

    text = repr(runtime)
    assert "tone" in text and "handlers=1" in text
