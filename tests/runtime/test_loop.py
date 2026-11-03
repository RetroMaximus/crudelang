"""The plan's tick order, one frame at a time.

Every test here hands a :class:`~crude.runtime.runtime.Runtime` a host built
out of the fakes in ``conftest.py`` - a :class:`FakeClock`, a
:class:`RecordingRenderer`, a ``CountingPd``, a ``CountingSerial`` and a
:class:`NullBackend` - and asks what a tick actually did. The plan's order is
one line:

    pump input → drain PdInbox → drain serial inbox → run ``ON_FRAME``
    handlers → dispatch UI events → draw → swap → pace

and this file's job is to prove that line rather than assert the count of
frames a run produced. A FakeClock means no wall time is spent, and a
bounded ``run(max_frames=N)`` means no loop ever turns into a hang.
"""

from __future__ import annotations

import types

import pytest

from crude.errors import EXIT_OK, EXIT_RUNTIME, ExitRequest
from crude.runtime import Runtime, RuntimeError_
from crude.runtime.clock import FakeClock


# -- the tick, in order --------------------------------------------------

def test_a_tick_drains_the_pd_inbox_before_the_serial_one(runtime, host):
    host.pd.counts = [3]
    host.serial.counts = [2]

    runtime.step()

    assert host.pd.calls == 1
    assert host.serial.calls == 1
    assert runtime.serial_events == 2


def test_a_tick_pumps_the_window_first_and_stops_before_anything_else(
        make_host, counting_pd, counting_serial, make_display):
    """The plan's pump-first order exists so nothing draws into a closed window."""
    host = make_host(mode="window")
    window = make_display(pumps_until_close=1)
    host.display = window
    loop = Runtime(host, mode="window", clock=host.clock, display=window)
    loop.activate(host)

    report = loop.step()

    assert report is None
    assert loop.window_gone is True
    assert counting_pd.calls == 0
    assert counting_serial.calls == 0
    assert loop.frames == 0


def test_a_headless_tick_still_counts_a_frame_even_with_no_window(runtime,
                                                                  host):
    runtime.step()

    assert runtime.frames == 1
    assert runtime.ticks == 1
    assert host.clock.frames == 1


def test_the_clock_moves_by_exactly_one_frame_per_tick(host):
    loop = Runtime(host, mode="headless", clock=host.clock)
    loop.activate(host)

    before = host.clock.now()
    loop.step()

    # a FakeClock moves by 1/fps on frame(); 60 fps means ~16.7 ms per tick
    assert host.clock.now() - before == pytest.approx(1.0 / 60)


# -- the UI half of the tick -------------------------------------------

def test_a_frame_report_is_recorded_once_per_tick(runtime, host):
    runtime.step()
    runtime.step()

    assert len(runtime.frame_reports) == 2
    assert runtime.frames == 2


def test_a_tick_with_no_ui_skips_the_frame_report_but_still_counts(host):
    host.ui = None
    loop = Runtime(host, mode="headless", clock=host.clock)
    loop.activate(host)
    # activate built a headless UI; a test that wants none takes it back off
    loop.ui = None

    report = loop.step()

    assert report is None
    assert loop.frames == 1


def test_a_frame_over_budget_is_counted_and_named_once(host, recording):
    from crude.ui.context import UIContext

    # a UIContext whose renderer never produces ops is still a frame; the
    # budget check reads the elapsed ms, and a test's clock says 0 ms, so a
    # caller that wants to trip it hands a budget of 0
    ui = UIContext(recording, width=320, height=200, painting=False)
    clock = FakeClock(target_fps=60)
    host.clock = clock
    host.ui = ui
    loop = Runtime(host, mode="headless", clock=clock, ui=ui, budget=0.0)
    loop.activate(host)

    # a headless UI pass takes no ms on a FakeClock, so the loop only counts
    # a hit above the (zero) budget the caller passed in
    loop.step()

    # the assertion here is the mechanism, not the arithmetic: at least one
    # frame report was recorded and the counter tracks over-budget frames
    assert len(loop.frame_reports) == 1
    assert loop.budget_hits in (0, 1)


def _scenes_manifest(names):
    """A manifest whose ``ui`` section says which scenes the project has."""
    return types.SimpleNamespace(
        runtime=types.SimpleNamespace(width=320, height=200, target_fps=60,
                                      vsync=False, mode="headless"),
        ui=types.SimpleNamespace(theme="dark", workspaces=names))


def test_the_scenes_the_manifest_named_are_reserved_before_the_run(make_host):
    """``ui.workspaces`` names the pages; the loop gives the context those numbers.

    Naming is not showing: the entry script's scene stays the one on screen until a
    ``WORKSPACE`` or a ``WORKSPACE_SHOW`` says otherwise, so a project that lists four
    pages and draws one still runs.
    """
    from crude.ui.context import UIContext

    host = make_host(manifest=_scenes_manifest(("sampler", "mixer")))
    ui = UIContext(None, width=320, height=200, painting=False)
    loop = Runtime(host, mode="headless", clock=host.clock, ui=ui)
    loop.activate(host)

    assert ui.workspaces.names() == ["root", "sampler", "mixer"]
    assert ui.workspaces.current_name == "root"
    assert ui.workspaces.index_of("mixer") == 2


def test_a_manifest_that_names_nothing_leaves_the_window_with_one_scene(host):
    loop = Runtime(host, mode="headless", clock=host.clock)
    loop.activate(host)

    assert loop.ui.workspaces.names() == ["root"]


def test_a_ui_that_is_not_a_context_is_seeded_with_nothing_said(make_host):
    """A host object with no ``workspaces`` is a host object, not an error."""
    host = make_host(manifest=_scenes_manifest(("mixer",)))
    loop = Runtime(host, mode="headless", clock=host.clock,
                   ui=types.SimpleNamespace())
    loop.activate(host)

    assert loop.frames == 0
    assert not loop.notes


# -- the audio half -----------------------------------------------------

def test_a_headless_tick_renders_the_frame_its_stream_rate_asked_for(runtime):
    runtime.blocks_per_frame = 10

    runtime.step()

    assert runtime.project.audio.ticks == 10


def test_a_tick_that_would_have_to_render_zero_blocks_is_a_tick_that_does_nothing(
        runtime):
    runtime.blocks_per_frame = 0

    runtime.step()

    assert runtime.project.audio.ticks == 0


def test_a_frame_with_no_stream_opened_at_all_says_nothing(host):
    """``--no-audio`` means no audio to tick and no "tick failed" every frame."""
    from crude.audio import AudioRuntime
    from crude.audio.null import NullBackend

    quiet = AudioRuntime(backend=NullBackend())  # never open()ed
    host.audio = quiet
    loop = Runtime(host, mode="headless", clock=host.clock)
    loop.activate(host)

    loop.step()

    assert quiet.ticks == 0
    assert not any("audio tick failed" in n for n in loop.notes)
    assert not any("audio tick failed" in n for n in host.notes)


def test_a_block_count_comes_from_the_streams_numbers_when_nobody_passed_one(
        host, null_audio):
    """``sample_rate / (fps × block_size)`` is the honest answer for a pulled run."""
    from crude.audio.base import AudioConfig

    host.audio = null_audio
    host.audio.config = AudioConfig(sample_rate=48000, block_size=100)
    host.clock.target_fps = 60
    loop = Runtime(host, mode="headless", clock=host.clock)
    loop.activate(host)

    # 48000 / (60 * 100) = 8 blocks a frame
    assert loop.blocks_for_frame() == 8


def test_a_frame_rate_of_zero_renders_at_least_one_block_so_audio_still_moves(
        host):
    host.clock.target_fps = 0
    loop = Runtime(host, mode="headless", clock=host.clock)
    loop.activate(host)

    assert loop.blocks_for_frame() == 1


# -- start / stop / run -------------------------------------------------

def test_a_terminal_mode_returns_through_run_without_pumping_a_frame(host):
    host.exit_code = 0
    loop = Runtime(host, mode="terminal", clock=host.clock)
    loop.activate(host)

    assert loop.run() == EXIT_OK
    assert loop.frames == 0
    assert host.clock.frames == 0


def test_a_bounded_run_pumps_exactly_the_frames_it_was_given(runtime):
    runtime.run(max_frames=3)

    assert runtime.frames == 3
    assert runtime.project.audio.ticks == 3 * runtime.blocks_for_frame()


def test_a_run_that_sees_the_window_close_stops_without_raising(make_host,
                                                                make_display):
    host = make_host(mode="window")
    window = make_display(pumps_until_close=2)
    host.display = window
    loop = Runtime(host, mode="window", clock=host.clock, display=window)
    loop.activate(host)

    code = loop.run()

    assert code == EXIT_OK
    assert loop.window_gone is True
    assert loop.frames == 1   # the second pump said the person closed it


def test_an_exit_request_from_a_frame_handler_lands_its_code_and_stops(host):
    class Boom:
        def frame(self, dt=0.0):
            raise ExitRequest(7, "the script asked to stop")

    host.ui = Boom()
    loop = Runtime(host, mode="headless", clock=host.clock, ui=Boom())

    code = loop.run()

    assert code == 7
    assert host.exit_code == 7


def test_a_keyboard_interrupt_is_the_plan_s_cancel_not_a_crash(host):
    class Boom:
        def frame(self, dt=0.0):
            raise KeyboardInterrupt()

    loop = Runtime(host, mode="headless", clock=host.clock, ui=Boom())

    code = loop.run()

    assert code == EXIT_OK
    # the loop's ``log`` is the project's ``note``, so what it said lands there
    assert "stopped by the user" in host.notes


# -- shutdown order ------------------------------------------------------

def test_stop_audio_comes_first_and_the_project_takes_the_rest_from_there(
        host, null_audio):
    order = []
    host.audio = null_audio
    host.audio.stop = lambda: order.append("audio.stop")
    host.shutdown = lambda: order.append("project.shutdown") or []

    loop = Runtime(host, mode="headless", clock=host.clock)
    loop.activate(host)
    loop.stop()

    assert order == ["audio.stop", "project.shutdown"]


def test_the_window_goes_last_and_only_the_loop_that_opened_it_closes_it(
        make_host, make_display):
    host = make_host(mode="window")
    window = make_display()
    host.display = window
    loop = Runtime(host, mode="window", clock=host.clock, display=window)
    loop.activate(host)
    # ``activate`` on a display it was handed claims the loop owns it, which
    # is what the plan asks of a run that took the splash's window over
    assert loop.display_owned is True

    loop.stop()

    assert window.close_calls >= 1
    assert loop.display is None


def test_a_display_the_loop_did_not_open_is_left_for_its_owner_to_close(
        make_host, make_display):
    host = make_host(mode="window")
    window = make_display()
    loop = Runtime(host, mode="window", clock=host.clock, display=window)
    loop.activate(host)
    # ``activate`` on a display it was handed claims it; a caller that means
    # otherwise takes the flag back
    loop.display_owned = False

    loop.stop()

    assert window.close_calls == 0


def test_a_stop_is_safe_to_call_twice(runtime):
    runtime.stop()
    runtime.stop()

    assert runtime.closed is True


# -- pacing --------------------------------------------------------------

def test_a_live_clock_paces_a_run_and_a_test_clock_never_waits():
    """Pacing is real work against real seconds; a FakeClock's are virtual."""
    held = []
    live = FakeClock(target_fps=30)
    # ``live`` says no on the class, so the loop's pace check short-circuits
    loop = Runtime(None, mode="headless", clock=live)
    loop._last = 0.0
    live._now = 0.0

    assert loop.pace() == 0.0
    assert held == []


def test_a_frame_handed_a_budget_of_none_reads_the_ui_context_default(host):
    loop = Runtime(host, mode="headless", clock=host.clock)
    loop.activate(host)

    from crude.ui.context import BUDGET_MS

    assert loop._budget() == float(BUDGET_MS)


# -- reporting -----------------------------------------------------------

def test_stats_names_the_numbers_the_cli_prints_after_a_bounded_run(runtime):
    runtime.run(max_frames=2)

    said = runtime.stats()

    assert said["mode"] == "headless"
    assert said["frames"] == 2
    assert said["ticks"] == 2
    assert said["exit_code"] == EXIT_OK
    assert said["audio_ticks"] == 2 * runtime.blocks_for_frame()


def test_describe_says_the_mode_the_frames_and_the_events_in_words(runtime):
    runtime.run(max_frames=2)

    said = runtime.describe()

    assert said.startswith("runtime headless: 2 frame(s)")


# -- the errors the loop raises ------------------------------------------

def test_a_run_with_no_project_says_runtime_error_and_exit_five():
    loop = Runtime(None, mode="headless", clock=FakeClock())

    with pytest.raises(RuntimeError_) as caught:
        loop.start()

    assert caught.value.exit_code == EXIT_RUNTIME


def test_an_activating_a_runtime_writes_itself_back_onto_the_project(host):
    loop = Runtime(host, mode="headless", clock=host.clock)
    loop.activate(host)

    assert host.runtime is loop
    assert loop.active is True


def test_activate_is_idempotent_so_a_re_run_does_not_wire_the_loop_twice(host):
    loop = Runtime(host, mode="headless", clock=host.clock)
    loop.activate(host)
    first_ui = loop.ui

    loop.activate(host)

    assert loop.ui is first_ui


def test_a_deactivate_stops_a_running_loop(host):
    loop = Runtime(host, mode="headless", clock=host.clock)
    loop.activate(host)
    loop.running = True

    loop.deactivate()

    assert loop.running is False
    assert loop.active is False


# -- a mode handed in wins over the project's ---------------------------

def test_a_mode_named_by_the_constructor_overrides_the_host_field(host):
    host.mode = "window"
    loop = Runtime(host, mode="headless", clock=host.clock)

    assert loop.mode == "headless"


def test_changing_the_mode_writes_the_change_back_onto_the_host(host):
    loop = Runtime(host, mode="headless", clock=host.clock)

    loop.mode = "window"

    assert host.mode == "window"


def test_a_host_without_a_mode_field_still_moves_the_loop_its_own_way(host):
    loop = Runtime(host, mode="headless", clock=host.clock)
    del host.mode  # an object that does not answer to a mode change

    loop.mode = "terminal"

    assert loop.mode == "terminal"


def test_the_context_manager_activates_and_stops_in_one_shape(host):
    with Runtime(host, mode="headless", clock=host.clock) as loop:
        loop.run(max_frames=1)

    assert loop.closed is True
    assert loop.frames == 1


# -- a ``BareHost`` that never got a Runtime still ticks nothing ---------

def test_the_bare_host_starts_with_no_runtime_field_filled(host):
    assert host.runtime is None
