"""The session: job scheduling, the control plane and ``$0`` resolution.

``FakeLibpd`` records every message it receives, so these tests assert on the
*sequence of libpd calls*, which is what the audio thread would have made.
"""

from __future__ import annotations

import ctypes
import threading

import pytest

from crude.pd import FakeLibpd, PdPatch, PdScheduler, PdSession
from crude.pd.engine import COALESCED_KINDS, MIDI_KINDS, PdError, PdJob


# -- scheduler ----------------------------------------------------------


def test_float_jobs_coalesce_per_receiver():
    scheduler = PdScheduler()
    scheduler.submit("float", "freq", (100.0,))
    scheduler.submit("float", "freq", (200.0,))
    scheduler.submit("float", "cutoff", (800.0,))

    jobs = scheduler.drain()
    assert [(job.kind, job.recv, job.args) for job in jobs] == [
        ("float", "freq", (200.0,)),
        ("float", "cutoff", (800.0,)),
    ]


def test_bang_and_symbol_coalesce_too():
    scheduler = PdScheduler()
    scheduler.submit("bang", "tick")
    scheduler.submit("bang", "tick")
    scheduler.submit("symbol", "note", ("c4",))
    scheduler.submit("symbol", "note", ("d4",))

    jobs = scheduler.drain()
    assert jobs == [
        PdJob("bang", "tick", (), None),
        PdJob("symbol", "note", ("d4",), None),
    ]


def test_lists_and_messages_are_never_coalesced():
    scheduler = PdScheduler()
    scheduler.submit("list", "seq", (1.0,))
    scheduler.submit("list", "seq", (2.0,))
    scheduler.submit("message", "seq", ("set", 1.0))

    assert len(scheduler.drain()) == 3


def test_midi_jobs_keep_their_order():
    scheduler = PdScheduler()
    scheduler.submit("noteon", "", (0, 60, 100))
    scheduler.submit("noteoff", "", (0, 60))

    jobs = scheduler.drain()
    assert [job.kind for job in jobs] == ["noteon", "noteoff"]
    assert set(job.kind for job in jobs) <= set(MIDI_KINDS)


def test_coalescing_survives_a_drain():
    scheduler = PdScheduler()
    scheduler.submit("float", "freq", (1.0,))
    scheduler.drain()
    scheduler.submit("float", "freq", (2.0,))
    scheduler.submit("float", "freq", (3.0,))

    assert [job.args for job in scheduler.drain()] == [(3.0,)]


def test_scheduler_is_bounded_and_counts_drops():
    scheduler = PdScheduler(capacity=2)
    assert scheduler.submit("list", "a", (1.0,)) is True
    assert scheduler.submit("list", "b", (2.0,)) is True
    assert scheduler.submit("list", "c", (3.0,)) is False

    assert scheduler.dropped == 1
    assert len(scheduler) == 2


def test_scheduler_applies_every_job_kind():
    lib = FakeLibpd()
    scheduler = PdScheduler()
    scheduler.submit("float", "freq", (440.0,))
    scheduler.submit("bang", "tick")
    scheduler.submit("symbol", "note", ("c4",))
    scheduler.submit("list", "seq", (1.0, 2.0))
    scheduler.submit("message", "seq", ("set", 3.0))
    scheduler.submit("noteon", "", (0, 60, 100))

    assert scheduler.apply(lib) == 6
    assert lib.sent == [
        ("float", "freq", 440.0),
        ("bang", "tick"),
        ("symbol", "note", "c4"),
        ("list", "seq", (1.0, 2.0)),
        ("message", "seq", "set", (3.0,)),
    ]
    assert lib.midi == [("noteon", 0, 60, 100)]
    assert scheduler.executed == 6


def test_scheduler_rejects_an_unknown_job_kind():
    lib = FakeLibpd()
    scheduler = PdScheduler()
    scheduler.submit("teleport", "x")

    with pytest.raises(PdError):
        scheduler.apply(lib)


def test_coalesced_kinds_are_the_expected_ones():
    assert set(COALESCED_KINDS) == {"float", "bang", "symbol"}


# -- patch names --------------------------------------------------------


def test_patch_resolves_dollar_zero_array_names():
    patch = PdPatch("p", "x.pd", ".", handle=1, dollar_zero=1003)

    assert patch.resolve_name("$0-wave") == "1003-wave"
    assert patch.register_array("wave") == "1003-wave"
    assert patch.resolve_name("wave") == "1003-wave"
    assert patch.resolve_name("global") == "global"
    assert patch.dollar_zero_name == "$0=1003"


def test_patch_register_array_accepts_an_explicit_pd_name():
    patch = PdPatch("p", "x.pd", ".", handle=1, dollar_zero=7)
    patch.register_array("wave", "mytable")
    assert patch.resolve_name("wave") == "mytable"


# -- session ------------------------------------------------------------


def test_session_start_initialises_the_library():
    lib = FakeLibpd()
    session = PdSession(lib, sample_rate=48000, in_channels=1, out_channels=2)
    session.start()

    assert lib.initialised is True
    assert lib.audio_format == (1, 2, 48000)
    assert session.blocksize_value == 64


def test_session_opens_and_closes_a_patch(patch_file):
    lib = FakeLibpd()
    session = PdSession(lib).start()

    patch = session.open_patch("tone", patch_file.name, str(patch_file.parent))
    assert patch.dollar_zero > 1000
    assert session.patch("tone") is patch

    assert session.close_patch("tone") is True
    assert session.close_patch("tone") is False
    with pytest.raises(PdError):
        session.patch("tone")


def test_session_never_asks_libpd_for_a_missing_file(tmp_path):
    """``libpd_openfile`` on a bad path crashes the process: we check first."""
    session = PdSession(FakeLibpd()).start()

    with pytest.raises(PdError) as excinfo:
        session.open_patch("tone", "nope.pd", str(tmp_path))
    assert "does not exist" in str(excinfo.value)


def test_session_rejects_a_patch_that_libpd_cannot_open(patch_file):
    lib = FakeLibpd(accept_unknown_files=False)
    session = PdSession(lib).start()

    with pytest.raises(PdError):
        session.open_patch("tone", patch_file.name, str(patch_file.parent))


#: A front panel of control boxes and nothing else: no ``[r]`` anywhere, which is
#: exactly what an emitted GL widget twin writes.
GUI_PATCH = "\n".join((
    "#N canvas 0 0 300 200 12;",
    "#X obj 20 20 vu 15 60 tick beat -1 -10 0 12 #404040 #000000 0 0;",
    "#X obj 20 100 hsl 128 15 0 300 0 0 bpm empty BPM -2 -8 0 10 #dfdfdf"
    " #000000 #000000 120 1;",
    "#X obj 200 20 tgl 15 0 run run Run 17 7 0 10 #dfdfdf #000000 #000000 0 1;",
    "#X obj 200 100 nbx 5 14 -1e+37 1e+37 0 0 empty empty BPM 0 -8 0 10"
    " #dfdfdf #000000 #000000 0 20;",
)) + "\n"


def test_a_gui_box_declares_the_name_in_its_receive_slot(tmp_path):
    """A control box binds a receiver in Pd, so the fake has to bind one too.

    ``[r foo]`` is the easy case. A ``[vu]``/``[tgl]`` keeps the name it listens
    on in a slot whose position is different for every kind - a meter has no send
    slot at all, a toggle puts its side first - and a patch of emitted twins has
    no ``[r]`` in it anywhere, so a fake that only understood ``[r]`` would tell
    a test that the panel nobody can reach is a panel that opens. The number box
    here is the other half of the rule: its receive slot is ``empty``, and a
    label is not a receiver.
    """
    path = tmp_path / "panel.pd"
    path.write_text(GUI_PATCH, encoding="utf-8")
    lib = FakeLibpd()
    session = PdSession(lib).start()

    session.open_patch("panel", path.name, str(tmp_path))

    assert lib.receivers == {"tick", "run"}
    assert session.exists("tick") is True
    assert session.exists("bpm") is False, "a send name is not a receiver"


def test_session_binds_and_unbinds_receivers():
    lib = FakeLibpd()
    session = PdSession(lib).start()

    session.bind("ext")
    assert session.exists("ext") is True
    assert session.bound_receivers == ["ext"]

    session.unbind("ext")
    assert session.exists("ext") is False
    assert session.bound_receivers == []


def test_control_pauses_and_resumes_the_stream():
    states = []
    lib = FakeLibpd()
    session = PdSession(lib).start()
    session.set_pauser(states.append)

    with session.control():
        states.append("inside")

    assert states == [True, "inside", False]


def test_control_resumes_the_stream_even_when_the_body_raises():
    states = []
    session = PdSession(FakeLibpd()).start()
    session.set_pauser(states.append)

    with pytest.raises(ValueError):
        with session.control():
            raise ValueError("boom")

    assert states == [True, False]


def test_strict_threads_rejects_a_foreign_thread(monkeypatch):
    monkeypatch.setenv("CRUDE_PD_STRICT_THREADS", "1")
    session = PdSession(FakeLibpd()).start()
    errors = []

    def worker():
        try:
            with session.control():
                pass
        except PdError as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert errors and "does not own" in str(errors[0])


def test_dsp_switches_pd_dsp_through_a_message():
    lib = FakeLibpd()
    session = PdSession(lib).start()

    session.dsp(True)
    assert lib.sent == [("message", "pd", "dsp", (1.0,))]
    assert session.dsp_on is True

    session.dsp(False)
    assert lib.sent[-1] == ("message", "pd", "dsp", (0.0,))
    assert session.dsp_on is False


def test_sends_are_queued_until_the_next_audio_tick():
    lib = FakeLibpd()
    session = PdSession(lib).start()

    session.send_float("freq", 440.0)
    assert lib.sent == []
    assert session.scheduler.pending == 1

    session.process(1, *buffers(session))
    assert lib.sent == [("float", "freq", 440.0)]
    assert lib.process_calls == [1]


def test_process_offline_returns_an_interleaved_buffer():
    lib = FakeLibpd(blocksize=4)
    session = PdSession(lib, out_channels=2)
    session.start()
    session.blocksize_value = lib.blocksize()

    samples = session.process_offline(ticks=2)
    assert len(samples) == 4 * 2 * 2
    assert set(samples) == {0.0}
    assert lib.process_calls == [2]


def test_process_offline_surfaces_a_libpd_failure():
    lib = FakeLibpd()
    lib.process_float = lambda ticks, in_buffer, out_buffer: 1
    session = PdSession(lib).start()

    with pytest.raises(PdError):
        session.process_offline(1)


def test_shutdown_closes_patches_and_unbinds(patch_file):
    lib = FakeLibpd()
    session = PdSession(lib).start()
    session.open_patch("tone", patch_file.name, str(patch_file.parent))
    session.bind("ext")

    session.shutdown()

    assert session.patches == {}
    assert session.bound_receivers == []
    assert lib.bound == set()
    assert lib.files == {}


def buffers(session):
    """Stereo float buffers sized for one block of ``session``."""
    size = session.blocksize_value * max(session.out_channels, 1)
    return (ctypes.c_float * size)(), (ctypes.c_float * size)()


def test_session_arrays_use_the_resolved_name(patch_file):
    lib = FakeLibpd()
    session = PdSession(lib).start()
    patch = session.open_patch("tone", patch_file.name, str(patch_file.parent))
    lib.add_array(f"{patch.dollar_zero}-wave", [0.0])

    session.array_write(patch, "wave", [0.5], 0)
    assert session.array_read(patch, "wave") == [0.5]
    assert session.array_size(patch, "wave") == 1


def test_session_falls_back_to_a_global_array_name(patch_file):
    lib = FakeLibpd()
    session = PdSession(lib).start()
    patch = session.open_patch("tone", patch_file.name, str(patch_file.parent))
    lib.add_array("wave", [1.0, 2.0])

    assert session.array_read(patch, "wave") == [1.0, 2.0]
    assert patch.arrays["wave"] == "wave"
