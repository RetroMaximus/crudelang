"""What the three modes mean once there is a loop to run them in.

:mod:`crude.project.manifest` owns the *names* - ``terminal``, ``headless``,
``window`` - and validates them. This module owns the *consequences*: whether
this run pumps a frame at all, whether it needs a window, and what happens when
the machine cannot give one.

Keeping that in one small place is what lets the loader, the CLI and the runtime
agree without importing each other, and it is the plan's "auto-downgrade with a
message when a mode is impossible" written as a function rather than as three
scattered conditionals: a window that cannot be opened becomes ``headless``, a
``headless`` with no audio device left becomes ``terminal``, and the reason comes
back with it so the caller can *say* what it did.

Nothing here opens a window or touches GL: :func:`possible` is told whether the
display worked, because deciding that is a call into :mod:`crude.ui.gl` and a
mode table must stay importable on a machine with no driver at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

#: the mode names, in the order a downgrade walks down them
TERMINAL = "terminal"
HEADLESS = "headless"
WINDOW = "window"

DOWN_ORDER = (WINDOW, HEADLESS, TERMINAL)

#: what a run that could not open its window is told to say
NO_DISPLAY = "this machine cannot open a window"


@dataclass(frozen=True)
class Mode:
    """One runtime mode, as the loop reads it.

    Three questions and nothing else, because a mode is a *policy*, not a
    subsystem: whether a frame is worth running, whether a window is needed to
    run it in, and whether a script's widgets should reach the screen.
    """

    name: str
    #: does this run have a frame loop at all? ``terminal`` is run-and-exit: the
    #  script speaks, the report finishes, the process answers
    loops: bool = True
    #: is a window part of the plan? ``headless`` runs every handler and renders
    #  every block with none, which is the plan's Pi-over-SSH case
    wants_window: bool = False
    #: may the draw pass put pixels on screen? ``False`` is the plan's parity
    #  line: the tree, the events and the maths are identical, only the ops stop
    paints: bool = False

    @classmethod
    def of(cls, name: str) -> "Mode":
        """The mode a manifest or ``--mode`` names; the terminal is the fallback."""
        return MODES.get(str(name or "").strip().lower(), MODES[TERMINAL])

    # -- the two questions the loop and the CLI ask ------------------
    @property
    def frame_paced(self) -> bool:
        """Does the clock hold this run to a frame rate?

        Only a live clock and a loop both exist - a test's
        :class:`~crude.runtime.clock.FakeClock` advances virtual time itself, so
        pacing on wall seconds would make it wait for nothing.
        """
        return self.loops

    @property
    def audio_started(self) -> bool:
        """Does this run let the stream run? A terminal says nothing aloud."""
        return self.loops

    def describe(self) -> str:
        said = [self.name]
        if not self.loops:
            said.append("no frame loop")
        if self.wants_window:
            said.append("window")
        elif self.paints:
            said.append("draws without a window")
        else:
            said.append("no drawing")
        return ", ".join(said)

    def __str__(self) -> str:
        return self.describe()


#: the three modes, and what each one promises
MODES = {
    TERMINAL: Mode(TERMINAL, loops=False),
    HEADLESS: Mode(HEADLESS),
    WINDOW: Mode(WINDOW, wants_window=True, paints=True),
}


def mode_of(name: str) -> Mode:
    """``Mode.of`` with the name the loader already uses."""
    return Mode.of(name)


def wants_loop(name: str) -> bool:
    """Does a run of this mode pump frames? ``crude run`` asks before it loops."""
    return Mode.of(name).loops


def downgrade(name: str) -> str:
    """The next mode down: window → headless → terminal (which is final)."""
    current = str(name or "").strip().lower()
    for index, held in enumerate(DOWN_ORDER):
        if held == current and index + 1 < len(DOWN_ORDER):
            return DOWN_ORDER[index + 1]
    return TERMINAL


def possible(name: str, *, has_display: bool = True, has_audio: bool = True,
             reason: str = "") -> Tuple[str, str]:
    """Can this mode be run here? Returns ``(mode, why_it_could_not)``.

    The answer is the mode this run *should* use - the one asked for when it is
    possible, otherwise the next one down - and the sentence that says why the
    change happened, which is the plan's "with a message": a person who asked
    for a window over SSH is told about the downgrade rather than watching a
    blank screen wonder where it went.
    """
    wanted = str(name or "").strip().lower()
    mode = wanted if wanted in MODES else TERMINAL
    if wanted not in MODES:
        return TERMINAL, f"there is no mode '{wanted}'; running in terminal"
    if MODES[mode].wants_window and not has_display:
        down = downgrade(mode)
        return down, reason or NO_DISPLAY
    if mode == HEADLESS and not has_audio and not has_display:
        # nothing to draw and nothing to render: the frame loop would only spin
        return TERMINAL, "there is no audio to run and no display to draw on"
    return mode, ""


def explain(name: str, reason: str) -> str:
    """One line for stderr, naming both the old mode and the new one."""
    return (f"crude run: mode '{name}' is not possible here ({reason}); "
            f"running '{downgrade(name)}' instead")


__all__ = ["Mode", "MODES", "TERMINAL", "HEADLESS", "WINDOW", "DOWN_ORDER",
           "mode_of", "wants_loop", "downgrade", "possible", "explain"]
