"""``crude run`` with a window: who opens it, who draws on it, and who closes it.

The splash is the first thing in this repository that a command line opens for
real, so the wiring is the risk rather than the picture. These tests hand the CLI
a display that is a recorder - :func:`_open_display` is the one call that touches
GLFW, and monkeypatching it is honest - and then ask the questions the S6
acceptance line actually poses:

* does a window project show progress while it loads, and does the window close
  cleanly at the end, whether the load worked or failed;
* does a machine with no display get the same report on the terminal, with the
  reason said out loud rather than a stack trace;
* does a terminal project never ask for a window in the first place.

Nothing in the default run here needs a GPU, and nothing here draws a widget: S6
stops at the splash, and the frame loop that would keep the window open is S11's.
The two tests at the bottom do want a real window and are marked ``requires_gl``,
so they are deselected unless the run asks for them.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

import pytest

from crude.cli import EXIT_OK, main
from crude.commands.project import _open_display, _reporter, _splash, _wants_splash
from crude.project import AUTO, CONSOLE, NONE, SPLASH, LoadOptions, open_project
from crude.project.progress import ConsoleReporter, NullReporter, SplashReporter
from crude.ui.recording import RecordingRenderer
from crude.ui.splash import PALETTE, SplashCanvas

#: a window project that loads with no hardware at all
WINDOW: Dict[str, Any] = {
    "id": "deck",
    "entry": "main.crude",
    "runtime": {"mode": "window", "width": 320, "height": 200, "target_fps": 60},
    "audio": {"enabled": False},
}

QUIET = 'LOG("the deck is up")\n'
BROKEN = 'LOG("unclosed\n'


@pytest.fixture(autouse=True)
def no_project_holds_a_log_open():
    """Let go of every project log, so a temporary directory can be removed."""
    yield
    for name in list(logging.root.manager.loggerDict):
        if not name.startswith("crude."):
            continue
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


@pytest.fixture
def deck(project):
    """Write a project directory and return its path, one section at a time."""
    def build(source: str = QUIET, **sections) -> str:
        data = dict(WINDOW)
        for key, value in sections.items():
            if value is not None:
                data[key] = value
        project.manifest(data)
        project.file("main.crude", source)
        return str(project.directory)

    return build


class FakeDisplay:
    """The half of a ``Display`` the CLI uses, with the window left out."""

    def __init__(self, width: int = 320, height: int = 200) -> None:
        self.renderer = RecordingRenderer()
        self.width = width
        self.height = height
        self.closed = False
        self.titles: List[str] = []
        self.begins = 0
        self.presents = 0

    def frame(self) -> None:
        self.begins += 1
        self.renderer.begin(self.width, self.height)

    def present(self) -> int:
        self.presents += 1
        return self.renderer.end()

    def pump(self, *, wait: float = 0.0) -> bool:
        return not self.closed

    def set_title(self, title: str) -> None:
        self.titles.append(title)

    def close(self) -> None:
        self.closed = True

    def texts(self) -> List[str]:
        return self.renderer.texts()

    def __str__(self) -> str:
        return f"FakeDisplay({self.width}x{self.height})"


@pytest.fixture
def displays(monkeypatch):
    """Every display the CLI opens, and the reason it would not open one."""
    opened: List[FakeDisplay] = []
    state = {"refuse": "", "calls": 0}

    def fake(project) -> Tuple[Any, str]:
        state["calls"] += 1
        if state["refuse"]:
            return None, state["refuse"]
        made = FakeDisplay()
        opened.append(made)
        return made, ""

    monkeypatch.setattr("crude.commands.project._open_display", fake)
    return type("Displays", (), {"made": opened, "state": state})()


def lines(capsys):
    captured = capsys.readouterr()
    return captured.out, captured.err


# -- a window project, run --------------------------------------------------

def test_a_window_project_draws_its_progress_while_it_loads(deck, displays, capsys):
    assert main(["run", deck(), "--frames", "0"]) == EXIT_OK

    out, err = lines(capsys)
    assert "the deck is up" in out
    assert displays.state["calls"] == 1
    display = displays.made[0]
    # the load was reported, drawn, and the script's own words still reached stdout
    assert display.begins > 1
    assert "script.compile" in " ".join(display.texts())


def test_the_very_first_frame_shows_the_plan(deck, displays):
    assert main(["run", deck(), "--frames", "0"]) == EXIT_OK

    display = displays.made[0]
    first = display.renderer.frames[0]
    assert [op.name for op in first][:2] == ["clear", "text"]
    said = [op.get("text", "") for op in first if op.name == "text"]
    assert "deck" in " ".join(said)
    assert "manifest" in " ".join(said)


def test_the_splash_ends_at_a_hundred_percent_in_the_done_colour(deck, displays):
    assert main(["run", deck(), "--frames", "0"]) == EXIT_OK

    display = displays.made[0]
    last = display.renderer.frames[-1]
    assert "100%" in " ".join(op.get("text", "") for op in last)
    boxes = [op for op in last if op.name == "rect"]
    assert str(boxes[-1].get("color")).startswith(PALETTE["done"])


def test_the_window_is_closed_when_the_run_is_over(deck, displays):
    assert main(["run", deck(), "--frames", "0"]) == EXIT_OK

    display = displays.made[0]
    assert display.closed is True


def test_a_load_that_failed_still_closes_the_window_it_opened(deck, displays,
                                                              capsys):
    assert main(["run", deck(BROKEN)]) != EXIT_OK

    out, err = lines(capsys)
    display = displays.made[0]
    assert display.closed is True
    # the splash said what happened before it went
    assert any("script" in text for text in display.texts())
    assert "script.compile" in err or "main.crude" in err


def test_the_window_title_is_the_project(deck, displays):
    assert main(["run", deck(), "--frames", "0"]) == EXIT_OK

    assert displays.made[0].titles[:1] == ["deck"]


# -- the downgrade, said out loud -------------------------------------------

def test_a_machine_with_no_display_runs_headless_instead(deck, displays, capsys):
    displays.state["refuse"] = "there is no display to open a window on"

    assert main(["run", deck(), "--frames", "0"]) == EXIT_OK

    out, err = lines(capsys)
    assert "there is no display to open a window on" in err
    # the plan's message: mode named, reason said, the mode it fell back to named
    assert "mode 'window' is not possible here" in err
    assert "running 'headless' instead" in err
    assert "script.compile" in out
    assert displays.made == []


def test_a_window_project_that_asked_for_console_progress_still_opens_a_window(
        deck, displays, capsys):
    # S11: the frame loop takes over the run's window, so a window-mode project
    # opens the display even when the progress was asked for by name
    assert main(["run", deck(), "--progress", "console",
                 "--frames", "0"]) == EXIT_OK

    _, err = lines(capsys)
    assert "no frame loop yet" not in err
    assert displays.state["calls"] == 1


def test_progress_by_name_beats_the_mode_and_a_terminal_stays_quiet(deck,
                                                                    displays,
                                                                    capsys):
    built = deck(**{"runtime": {"mode": "terminal", "width": 320, "height": 200,
                                "target_fps": 60}})
    assert main(["run", built]) == EXIT_OK

    assert displays.state["calls"] == 0
    assert displays.made == []


def test_a_splash_asked_for_by_name_is_opened_even_in_terminal_mode(deck,
                                                                    displays):
    built = deck(**{"runtime": {"mode": "terminal", "width": 320, "height": 200,
                                "target_fps": 60}})
    assert main(["run", built, "--progress", "splash"]) == EXIT_OK

    assert displays.state["calls"] == 1
    assert "script.compile" in " ".join(displays.made[0].texts())


def test_a_run_that_wants_no_progress_still_opens_a_window_for_the_loop(
        deck, displays, capsys):
    # S11: --progress none is a report decision, not a mode one - a window run
    # still needs its display so the loop has something to take over
    assert main(["run", deck(), "--progress", "none",
                 "--frames", "0"]) == EXIT_OK

    out, err = lines(capsys)
    assert displays.state["calls"] == 1
    assert "script.compile" not in out


# -- the decisions, one at a time -------------------------------------------

def test_only_auto_progress_and_a_window_mode_ask_for_a_splash(deck):
    built = open_project(deck(), options=LoadOptions(mode="window"))
    assert _wants_splash(built, AUTO) is True
    assert _wants_splash(built, SPLASH) is True
    assert _wants_splash(built, CONSOLE) is False
    assert _wants_splash(built, NONE) is False

    quiet = open_project(deck(**{"runtime": {"mode": "terminal", "width": 320,
                                             "height": 200, "target_fps": 60}}),
                         options=LoadOptions(mode="terminal"))
    assert _wants_splash(quiet, AUTO) is False
    assert _wants_splash(quiet, SPLASH) is True


def test_the_reporter_a_window_run_gets_is_the_splash_one(deck, displays):
    built = open_project(deck())
    reporter = _reporter(built)
    assert isinstance(reporter, SplashReporter)
    assert isinstance(reporter.canvas, SplashCanvas)
    assert built.display is displays.made[0]
    # recorded, so a failure three phases later still puts the window back
    assert any(what == "the window" for what, _ in built.shutdown_steps)
    built.shutdown()
    assert displays.made[0].closed is True


def test_the_reporter_a_terminal_run_gets_never_reached_for_a_window(deck,
                                                                     displays):
    built = open_project(deck(**{"runtime": {"mode": "terminal", "width": 320,
                                             "height": 200, "target_fps": 60}}),
                         options=LoadOptions(mode="terminal"))
    assert isinstance(_reporter(built), ConsoleReporter)
    assert displays.state["calls"] == 0


def test_a_run_that_asked_for_nothing_is_a_reporter_that_says_nothing(deck,
                                                                      displays):
    built = open_project(deck(), options=LoadOptions(progress=NONE))
    assert isinstance(_reporter(built), NullReporter)
    # S11: the mode is window, so the loop still gets a window to run in even
    # though no progress was asked to be drawn on it
    assert displays.state["calls"] == 1


def test_a_splash_that_cannot_be_had_is_no_canvas_at_all(deck, displays):
    displays.state["refuse"] = "no display"
    built = open_project(deck())
    assert _splash(built, AUTO) is None
    assert built.display is None
    assert built.shutdown_steps == []


def test_the_display_is_asked_for_at_the_size_the_manifest_declared(deck,
                                                                    monkeypatch):
    asked: List[Tuple[Any, ...]] = []

    def open_it(width, height, title, **kwargs):
        asked.append((width, height, title, kwargs))
        return FakeDisplay(width, height)

    monkeypatch.setattr("crude.ui.gl.open_display", open_it)
    built = open_project(deck())

    display, reason = _open_display(built)
    assert reason == ""
    assert asked == [(320, 200, "deck", {"vsync": True})]
    assert isinstance(display, FakeDisplay)


def test_the_display_sizes_come_from_the_manifest_not_a_default(deck, monkeypatch):
    asked: List[Tuple[Any, ...]] = []

    def open_it(width, height, title, **kwargs):
        asked.append((width, height, title, kwargs))
        return FakeDisplay(width, height)

    built = open_project(deck(**{"runtime": {"mode": "window", "width": 800,
                                             "height": 480, "target_fps": 24,
                                             "vsync": False}}))
    monkeypatch.setattr("crude.ui.gl.open_display", open_it)

    _open_display(built)
    assert asked == [(800, 480, "deck", {"vsync": False})]


def test_a_window_that_refuses_is_a_reason_rather_than_a_crash(deck, monkeypatch):
    from crude.ui.base import WindowUnavailable

    def no_window(*args, **kwargs):
        raise WindowUnavailable("glfw could not start, so no window can be opened")

    monkeypatch.setattr("crude.ui.gl.open_display", no_window)
    built = open_project(deck())

    display, reason = _open_display(built)
    assert display is None
    assert reason == "glfw could not start, so no window can be opened"


def test_a_driver_that_said_no_in_its_own_words_is_still_a_reason(deck,
                                                                  monkeypatch):
    def explode(*args, **kwargs):
        raise OSError("the display server hung up")

    monkeypatch.setattr("crude.ui.gl.open_display", explode)
    built = open_project(deck())

    display, reason = _open_display(built)
    assert display is None
    assert reason.startswith("the window could not be opened:")
    assert "the display server hung up" in reason


# -- what the load really drew ----------------------------------------------

def test_the_frames_drawn_during_a_load_are_the_frames_the_plan_took(deck,
                                                                      displays):
    assert main(["run", deck(), "--frames", "0"]) == EXIT_OK

    display = displays.made[0]
    percent = [text for text in display.texts() if text.strip().endswith("%")]
    # a bar that never moved would be one string repeated, and this is not that
    assert len(set(percent)) > 1
    assert percent[-1].strip() == "100%"


def test_a_load_that_failed_part_way_colours_its_last_frame(deck, displays):
    assert main(["run", deck(BROKEN)]) != EXIT_OK

    display = displays.made[0]
    boxes = [op for op in display.renderer.frames[-1] if op.name == "rect"]
    assert str(boxes[-1].get("color")).startswith(PALETTE["failed"])


# -- on a machine that can really open a window ------------------------------

class Photograph:
    """A display surface that looks at the frame before it is put on screen.

    What a back buffer holds after it has been swapped is undefined in OpenGL and
    unanswered in practice, so the pixels are taken while the frame is still the
    one being drawn - which is also the picture the person is offered.
    """

    def __init__(self, made: Any, spots: Dict[str, Tuple[float, float]]) -> None:
        self.made = made
        self.spots = spots
        self.seen: Dict[str, Tuple[int, ...]] = {}

    def frame(self) -> None:
        self.made.frame()

    def pump(self, *, wait: float = 0.0) -> bool:
        return self.made.pump(wait=wait)

    def set_title(self, title: str) -> None:
        self.made.set_title(title)

    def present(self) -> int:
        renderer = self.made.renderer
        for name, (x, y) in self.spots.items():
            plane = renderer.read_pixels(x, y, 1, 1)
            self.seen[name] = tuple(int(value) for value in plane[0, 0][:3])
        return self.made.present()


@pytest.fixture
def real(monkeypatch):
    """The genuine ``open_display``, with the window kept off the screen.

    Only the invisible flag is changed: everything a window project's run then
    goes through - GLFW, a context, a GL renderer, the splash - is the real thing.
    """
    from crude.ui.gl import open_display as real_open_display

    made: List[Any] = []

    def invisible(width, height, title, **kwargs):
        display = real_open_display(width, height, title, visible=False, **kwargs)
        made.append(display)
        return display

    monkeypatch.setattr("crude.ui.gl.open_display", invisible)
    return type("Real", (), {"made": made})()


@pytest.mark.requires_gl
def test_the_splash_paints_the_same_picture_on_a_real_window(deck, real):
    from crude.project.progress import SplashFrame
    from crude.ui.gl import window_available
    from crude.ui.gl.renderer import GLRenderer

    if not window_available():
        pytest.skip("this machine has no GLFW, so a window cannot be opened")
    built = open_project(deck())
    display, reason = _open_display(built)
    if display is None:  # a session with no driver: skipped, not failed
        pytest.skip(f"this machine will not hand out a window: {reason}")

    # the bar sits at 200 - 24 - 8 * 3.4 = 148.8 and is 8 tall, and at 40% the
    # fill reaches 19.2 + 281.6 * 0.4, so these two points are fill and track
    shot = Photograph(display, {"fill": (60.0, 152.0), "track": (200.0, 152.0)})
    try:
        canvas = SplashCanvas(display.renderer, shot)
        ops = canvas.draw(SplashFrame(event="step", project="deck",
                                      mode="window", step="audio.open",
                                      percent=40.0,
                                      details=("the device is open",)))
        assert ops > 0
        assert isinstance(display.renderer, GLRenderer)
        assert display.renderer.stats()["gl"] is True
        assert display.window.size() == (320, 200)
        # what the fake renderer recorded is what the glass is showing: the accent
        # over the track, in the colours the palette names
        assert shot.seen["fill"] == pytest.approx((89, 176, 255), abs=8)
        assert shot.seen["track"] == pytest.approx((51, 57, 69), abs=8)
    finally:
        display.close()
    assert display.window.alive is False


@pytest.mark.requires_gl
def test_a_run_with_a_real_window_opens_one_and_leaves_nothing_behind(
        deck, real, capsys):
    from crude.ui.gl import window as window_module
    from crude.ui.gl import window_available
    from crude.ui.gl.renderer import GLRenderer

    if not window_available():
        pytest.skip("this machine has no GLFW, so a window cannot be opened")
    before = window_module._open_windows

    assert main(["run", deck(), "--frames", "0"]) == EXIT_OK

    out, err = lines(capsys)
    assert "the deck is up" in out
    assert "reports progress on the terminal" not in err
    if not real.made:  # GLFW is there and the driver still said no
        pytest.skip("this machine will not hand out a window")
    assert len(real.made) == 1
    display = real.made[0]
    assert isinstance(display.renderer, GLRenderer)
    # the splash had a real frame loop of its own through the load, and drew it
    assert display.renderer.last_triangles > 0
    assert display.window.alive is False
    # and GLFW went back: a run that leaves the library initialised would make
    # the next one in this process a lie
    assert window_module._open_windows == before
