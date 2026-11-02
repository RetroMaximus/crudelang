"""The three modes and what they promise a run.

:mod:`crude.project.manifest` already validates that a ``runtime.mode`` is one
of the three names; the plan's S11 stage is the *consequences* of that choice.
These tests read the table :mod:`crude.runtime.modes` writes and pin the two
things the loop and the CLI actually ask: does this run pump a frame at all,
and what happens when a mode cannot be run on the machine we landed on.

Nothing here touches a window, an audio device or a driver: the whole point
of keeping the mode table this small is that it is importable on a machine
that has none of them.
"""

from __future__ import annotations

import pytest

from crude.runtime import (
    DOWN_ORDER,
    HEADLESS,
    TERMINAL,
    WINDOW,
    Mode,
    MODES,
    downgrade,
    explain,
    mode_of,
    possible,
    wants_loop,
)


# -- the three rows the plan names ------------------------------------

def test_the_three_modes_are_the_three_the_manifest_validates():
    assert set(MODES) == {TERMINAL, HEADLESS, WINDOW}


def test_terminal_is_the_only_one_that_refuses_a_frame_loop():
    assert MODES[TERMINAL].loops is False
    assert MODES[HEADLESS].loops is True
    assert MODES[WINDOW].loops is True


def test_window_is_the_only_one_that_puts_pixels_on_a_screen():
    assert MODES[WINDOW].wants_window is True
    assert MODES[WINDOW].paints is True
    assert MODES[HEADLESS].wants_window is False
    assert MODES[TERMINAL].wants_window is False


def test_headless_still_runs_handlers_without_a_window_or_a_paint():
    mode = MODES[HEADLESS]
    assert mode.loops and mode.audio_started
    assert not mode.wants_window and not mode.paints


def test_a_terminal_run_never_starts_a_stream_from_the_loop():
    assert MODES[TERMINAL].audio_started is False
    assert MODES[TERMINAL].frame_paced is False


# -- reading the table by name ----------------------------------------

def test_a_name_looks_its_mode_up_and_an_unknown_one_is_the_terminal():
    assert mode_of("window") is MODES[WINDOW]
    assert mode_of("Window") is MODES[WINDOW]
    assert mode_of("  headless  ") is MODES[HEADLESS]
    assert mode_of("garden") is MODES[TERMINAL]
    assert mode_of("") is MODES[TERMINAL]


def test_wants_loop_is_the_question_the_cli_asks_before_it_enters():
    assert wants_loop("headless") is True
    assert wants_loop("window") is True
    assert wants_loop("terminal") is False
    # a mode the table does not know is the mode that answers nothing aloud
    assert wants_loop("garden") is False


def test_a_mode_describes_itself_in_the_words_the_report_uses():
    assert MODES[TERMINAL].describe().startswith("terminal, no frame loop")
    assert "window" in MODES[WINDOW].describe()
    assert "no drawing" in MODES[HEADLESS].describe()


# -- the way down ------------------------------------------------------

def test_the_down_order_is_the_plan_the_explain_message_reads():
    assert DOWN_ORDER == (WINDOW, HEADLESS, TERMINAL)


@pytest.mark.parametrize("wanted,expected", [
    (WINDOW, HEADLESS),
    (HEADLESS, TERMINAL),
    (TERMINAL, TERMINAL),   # the bottom of the ladder stays put
    ("garden", TERMINAL),   # an unknown mode is the mode that says nothing
])
def test_a_downgrade_walks_one_step_down_the_ladder(wanted, expected):
    assert downgrade(wanted) == expected


# -- is this mode possible here? -------------------------------------

def test_a_window_needs_a_display_and_gets_headless_when_there_is_none():
    mode, why = possible(WINDOW, has_display=False,
                         reason="there is no GLFW on this machine")
    assert mode == HEADLESS
    assert why == "there is no GLFW on this machine"


def test_a_window_that_can_have_its_display_keeps_the_mode_it_was_asked_for():
    mode, why = possible(WINDOW, has_display=True)
    assert mode == WINDOW
    assert why == ""


def test_a_headless_run_with_nothing_left_to_do_becomes_a_terminal():
    mode, why = possible(HEADLESS, has_display=False, has_audio=False)
    assert mode == TERMINAL
    assert "no audio" in why and "no display" in why


def test_a_mode_the_table_does_not_know_is_answered_by_the_terminal():
    mode, why = possible("garden")
    assert mode == TERMINAL
    assert "no mode 'garden'" in why


def test_a_window_with_no_display_and_no_reason_still_says_why_it_moved():
    mode, why = possible(WINDOW, has_display=False)
    assert mode == HEADLESS
    # the module's own default answer, so a caller that forgot to say why
    # still gets a sentence rather than an empty string
    assert why and "window" not in why.lower().split("cannot")[0]


# -- the message the CLI reads out -----------------------------------

def test_explain_names_both_the_mode_it_could_not_run_and_the_one_it_did():
    said = explain(WINDOW, "there is no display to open a window on")
    assert "mode 'window' is not possible here" in said
    assert "there is no display to open a window on" in said
    assert "running 'headless' instead" in said


def test_explain_for_a_terminal_run_tells_you_it_is_the_last_stop():
    said = explain(TERMINAL, "nothing else will run here")
    assert "running 'terminal' instead" in said


# -- a ``Mode`` handed a name still says what it is ------------------

def test_a_mode_can_be_rebuilt_from_its_own_name():
    for name, mode in MODES.items():
        assert Mode.of(name) is mode
        assert str(mode).startswith(name)
