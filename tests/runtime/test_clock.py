"""The clock behind ``TIME``, ``SLEEP`` and ``FPS``.

The rule these tests exist to hold: a fake clock must be usable *everywhere* a
real one is, without a single real millisecond passing. So the live clock is
tested against a hand-written "monotonic" and a recorded wait - nothing here is
allowed to sleep, not even briefly.
"""

from __future__ import annotations

import time

import pytest

from crude.runtime import DEFAULT_FPS, Clock, FakeClock


def live_clock(*, start: float = 100.0, target_fps: int = 60):
    """A :class:`Clock` whose seconds and whose waiting are both scripted.

    Returns ``(clock, state, waits)``: ``state["now"]`` is what the clock reads as
    the current time, and ``waits`` collects every delay it was asked to hold - a
    fake ``time.sleep`` that still moves the clock on, the way a real one does.
    """
    state = {"now": start}
    waits = []

    def source() -> float:
        return state["now"]

    def wait(delay: float) -> None:
        waits.append(delay)
        state["now"] += delay

    return Clock(target_fps=target_fps, source=source, wait=wait), state, waits


# -- what a clock is ----------------------------------------------------


def test_the_default_aim_is_the_rate_the_example_projects_use():
    assert DEFAULT_FPS == 60
    assert Clock().target_fps == DEFAULT_FPS


def test_a_real_clock_measures_and_a_fake_only_listens():
    assert Clock.live is True
    assert FakeClock.live is False
    # the fake is a clock, so anything typed against one accepts the other
    assert isinstance(FakeClock(), Clock)


def test_a_clock_uses_the_process_sleep_when_no_pause_is_handed_in(monkeypatch):
    """``wait=None`` means the process's own sleep, and only a real clock wants it."""
    called = []
    monkeypatch.setattr(time, "sleep", lambda delay: called.append(delay))

    clock = Clock()

    assert clock.sleep(0.125) == 0.125
    assert called == [0.125]


# -- the live clock -----------------------------------------------------


def test_a_clock_counts_from_the_moment_it_is_made():
    clock, state, _ = live_clock(start=100.0)

    assert clock.now() == 0.0
    state["now"] = 100.5
    assert clock.now() == 0.5


def test_sleeping_holds_the_delay_and_says_how_long():
    clock, state, waits = live_clock()

    assert clock.sleep(0.25) == 0.25
    assert waits == [0.25]
    assert clock.slept == 0.25
    assert clock.now() == 0.25


def test_the_time_asked_for_adds_up_across_calls():
    clock, _, waits = live_clock()

    clock.sleep(0.5)
    clock.sleep(1.25)

    assert waits == [0.5, 1.25]
    assert clock.slept == pytest.approx(1.75)
    assert clock.now() == pytest.approx(1.75)


def test_a_delay_that_would_run_time_backwards_is_held_at_zero():
    """The clamp is for host callers; a *script* is refused by the binding first."""
    clock, state, waits = live_clock()
    state["now"] = 100.0

    assert clock.sleep(-2.0) == 0.0
    assert waits == [0.0]
    assert clock.slept == 0.0
    assert clock.now() == 0.0


def test_frames_are_only_a_rate_once_time_has_passed():
    clock, _, _ = live_clock()

    assert clock.fps() == 0.0
    assert clock.frame() == 1
    # one frame and no elapsed time is not yet a measurable rate
    assert clock.fps() == 0.0


def test_the_measured_rate_is_frames_over_the_time_they_took():
    clock, state, _ = live_clock()

    for _ in range(30):
        clock.frame()
    state["now"] += 0.5

    assert clock.frames == 30
    assert clock.fps() == pytest.approx(60.0)


def test_a_clock_describes_what_it_counted():
    clock, state, _ = live_clock(target_fps=24)
    clock.frame()
    state["now"] += 0.04

    assert str(clock) == "Clock 1 frame(s) in 0.040s, aiming at 24 fps"


# -- the fake clock -----------------------------------------------------


def test_a_fake_clock_never_asks_the_process_to_wait(monkeypatch):
    def never(delay):
        raise AssertionError(f"a fake clock waited {delay} seconds")

    monkeypatch.setattr(time, "sleep", never)

    clock = FakeClock()
    assert clock.sleep(5.0) == 5.0
    assert clock.slept == 5.0
    assert clock.now() == 5.0


def test_a_fake_clock_starts_where_the_test_said():
    assert FakeClock().now() == 0.0
    assert FakeClock(start=10.0).now() == 10.0


def test_running_frames_moves_virtual_time_at_the_aimed_rate():
    clock = FakeClock(target_fps=60)

    for _ in range(10):
        clock.frame()

    assert clock.frames == 10
    assert clock.now() == pytest.approx(10 / 60)
    assert clock.fps() == pytest.approx(60.0)


def test_time_can_be_moved_on_without_a_frame_being_counted():
    clock = FakeClock()

    assert clock.advance(0.5) == 0.5
    assert clock.now() == 0.5
    assert clock.frames == 0
    assert clock.fps() == 0.0


def test_a_fake_clock_also_refuses_to_run_time_backwards():
    clock = FakeClock(start=2.0)

    assert clock.advance(-1.0) == 0.0
    assert clock.now() == 2.0


def test_a_frame_rate_of_zero_counts_without_dividing_by_zero():
    clock = FakeClock(target_fps=0)

    assert clock.frame() == 1
    assert clock.now() == 0.0
    assert clock.fps() == 0.0


def test_a_fake_clock_describes_itself_as_the_thing_it_is():
    clock = FakeClock(target_fps=15)
    clock.frame()

    assert str(clock) == "FakeClock 1 frame(s) in 0.067s, aiming at 15 fps"
