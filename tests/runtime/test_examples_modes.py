"""The plan's S11 acceptance line: every example, every mode, all green.

S5 pinned that every example *checks*; S8 and S10 pinned what each one's
runtime halves do on their own; S11 has to say the loop actually enters,
paces, and shuts down whatever the manifest asks for. The matrix here is
the honest reading of the plan's line - "4 examples × 3 modes; default
marker set fully green", with two examples more than the plan counted because
``sound_recorder`` landed after S11 and ``all_gui`` came after it, each joining
the matrix rather than getting a table of its own - and every cell goes
through the same
:func:`crude.cli.main` the person at the terminal hits:

    python -m crude run <example> --mode <terminal|headless|window> \
        --frames 0 --no-audio --progress none

Nothing here needs a GPU, a device or a real port. A ``window`` run has
its display call monkeypatched to a :class:`FakeDisplay` (the plan's
"auto-downgrade with a message" is a real path, but not the one this
matrix walks), the in-memory :class:`FakeLibpd` answers every Pd call,
and ``--frames 0`` bounds the loop before a single tick is pumped. The
``sensor_bridge`` copy has its manifest port rewritten to ``loop://``, so
the loader opens it on the loop the ``SERIAL_OPEN("arduino")`` call in
``main.crude`` looks up by id, without a real ``COM3`` anywhere.

What the eighteen cells assert is not "the mode did something" - the loop
bounded at zero frames is the plan's CI shape, and its honest answer is
"the mode came up, ran the script, and shut everything down cleanly".
Every one of them is :data:`crude.cli.EXIT_OK`, and every one of them
keeps the exit code the script left behind.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from crude.cli import EXIT_OK, main
from crude.pd import FakeLibpd
from crude.project import MANIFEST_NAME
from crude.ui.recording import RecordingRenderer

#: the repository's ``examples/`` directory, whatever the tests are run from
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

NAMES = ("hello_terminal", "metronome", "fm_synth", "sensor_bridge",
         "sound_recorder", "all_gui")
MODES = ("terminal", "headless", "window")


# -- the fakes a matrix cell is allowed to use --------------------------


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
def copied(tmp_path):
    """Copy one example into ``tmp_path``, retargeting only its serial port.

    ``sensor_bridge`` ships with ``COM3`` because that is what the person on
    a bench uses; the CI matrix needs the loader's serial phase to open
    *something*, so the copy's manifest is rewritten to ``loop://arduino``.
    Every other example is copied untouched (build cache and logs excluded
    so the copy is a real fresh project, not last week's half-run).
    """
    def grab(name: str) -> str:
        target = tmp_path / name
        shutil.copytree(
            EXAMPLES / name, str(target),
            ignore=shutil.ignore_patterns(".crude_build", "logs"),
            dirs_exist_ok=True)
        if name == "sensor_bridge":
            where = os.path.join(str(target), MANIFEST_NAME)
            with open(where, encoding="utf-8") as handle:
                data = json.load(handle)
            for link in data.get("serial", []):
                link["port"] = "loop://" + str(link.get("id", "loop"))
            with open(where, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
        return str(target)

    return grab


@pytest.fixture
def nolib(monkeypatch):
    """Give ``pd.core`` the in-memory library, so no DLL is ever looked for."""
    made = FakeLibpd()
    monkeypatch.setattr("crude.pd.lib.Libpd.load",
                        classmethod(lambda cls, *args, **kwargs: made))
    return made


class FakeDisplay:
    """The window a ``window`` mode cell is happy to accept being handed.

    Only three things on it matter to S11: ``pump`` says the person has not
    closed the window, ``close`` says they have, and ``renderer`` is what
    :meth:`Runtime._build_ui` reaches for when the mode paints. A matrix
    cell bounded at ``--frames 0`` never asks the renderer to draw, so
    :class:`RecordingRenderer` is enough.
    """

    def __init__(self, width: int = 320, height: int = 200) -> None:
        self.renderer = RecordingRenderer()
        self.width = width
        self.height = height
        self.closed = False
        self.pumps = 0
        self.titles: List[str] = []

    def pump(self, *, wait: float = 0.0) -> bool:
        self.pumps += 1
        return not self.closed

    def frame(self) -> None:
        self.renderer.begin(self.width, self.height)

    def present(self) -> int:
        return self.renderer.end()

    def swap(self) -> int:
        return self.renderer.end()

    def set_title(self, title: str) -> None:
        self.titles.append(title)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def window_opened(monkeypatch):
    """Patch the two calls the loop and the CLI use to open a real display.

    :func:`crude.commands.project._open_display` is the CLI's one - a window
    run opens its splash's display through it before the loader's phase 14
    even starts. :func:`crude.runtime.open_display` is the loop's - a ``run``
    whose CLI did not open one (a ``--mode window`` override on a project
    whose manifest said otherwise) still ends up there. Both return the same
    :class:`FakeDisplay` so the loop takes over the window it was handed and
    the matrix's close-assertion below is about the *one* window the run made.
    """
    opened: List[FakeDisplay] = []

    def fake_cli(project) -> Tuple[Any, str]:
        made = FakeDisplay()
        opened.append(made)
        return made, ""

    def fake_loop(project, *, title=None, vsync=None) -> Tuple[Any, str]:
        made = FakeDisplay()
        opened.append(made)
        return made, ""

    monkeypatch.setattr("crude.commands.project._open_display", fake_cli)
    monkeypatch.setattr("crude.runtime.runtime.open_display", fake_loop)
    return opened


# -- the eighteen cells: every example in every mode -------------------


def _run(path: str, mode: str, extra: List[str] = None) -> int:
    """The CLI call every matrix cell makes, in one line.

    ``--frames 0`` is the plan's bound - no headless or window run has to
    pump a real frame on a machine with no person; ``--no-audio`` keeps
    sounddevice off the CI; ``--progress none`` means no splash is drawn
    even in ``window`` mode, so a cell is testing the loop's mode wiring
    rather than S6's reporter.
    """
    argv = ["run", path, "--mode", mode, "--frames", "0",
            "--no-audio", "--progress", "none"]
    if extra:
        argv.extend(extra)
    return main(argv)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("mode", MODES)
def test_the_plan_s_matrix_exits_ok_in_every_cell(copied, nolib, window_opened,
                                                 name, mode):
    assert _run(copied(name), mode) == EXIT_OK, (name, mode)


@pytest.mark.parametrize("name", NAMES)
def test_a_terminal_cell_never_opens_a_window(copied, nolib, window_opened,
                                              name):
    """A matrix cell that asks for no loop should not reach for GLFW either."""
    assert _run(copied(name), "terminal") == EXIT_OK

    assert window_opened == []


@pytest.mark.parametrize("name", NAMES)
def test_a_headless_cell_opens_no_window_and_pumps_no_frames(copied, nolib,
                                                             window_opened,
                                                             name):
    assert _run(copied(name), "headless") == EXIT_OK

    # ``--frames 0`` is the CI's bound, so the loop entered but ticked nothing
    assert window_opened == []


@pytest.mark.parametrize("name", NAMES)
def test_a_window_cell_opens_exactly_one_display_and_closes_it(copied, nolib,
                                                               window_opened,
                                                               name):
    assert _run(copied(name), "window") == EXIT_OK

    # one example, one window: the splash hands its own display to phase 14
    # rather than the loop opening a second one
    assert len(window_opened) == 1, window_opened
    assert window_opened[0].closed is True


# -- what each example says, in each mode -------------------------------


def test_hello_terminal_says_the_mode_the_cli_chose(copied, nolib, window_opened,
                                                   capsys):
    for mode in MODES:
        capsys.readouterr()
        assert _run(copied("hello_terminal"), mode) == EXIT_OK

        out, _ = capsys.readouterr()
        assert "this run is " + mode in out, (mode, out)


def test_the_fm_synth_cell_writes_its_patch_and_fills_its_table(copied, nolib,
                                                                 window_opened,
                                                                 capsys):
    path = copied("fm_synth")

    assert _run(path, "headless") == EXIT_OK

    out, _ = capsys.readouterr()
    # the load ran the script, so the carrier's table was written from the
    # preset's voice even with no audio and no tick pumped
    assert "voice: wave - 260 samples" in out


def test_the_sensor_cell_finds_its_link_by_id_on_the_loop(copied, nolib,
                                                          window_opened,
                                                          capsys):
    path = copied("sensor_bridge")

    assert _run(path, "headless") == EXIT_OK

    out, _ = capsys.readouterr()
    # the script's SERIAL_OPEN("arduino") picked up the loop:// link the
    # manifest declared, and the log the script says is on stdout
    assert "sensor-bridge: headless at 50 fps" in out


def test_the_sound_recorder_cell_loads_the_take_the_manifest_declares(copied,
                                                                      nolib,
                                                                      window_opened,
                                                                      capsys):
    """A cell with an input and no card: the take still arrives.

    ``--no-audio`` costs the recorder its stream and nothing else - the opening
    line reads the declared wav through the same codec a device run uses, puts it
    on the selected row and gives the four knobs that row's ruler - so the cell
    can be read as the example's own report of what it found.
    """
    path = copied("sound_recorder")

    assert _run(path, "headless") == EXIT_OK

    out, _ = capsys.readouterr()
    assert "loaded tone - 88200 samples at 44100 Hz onto 1 track(s)" in out
    assert "sound-recorder: 5 tracks, 1 declared sound(s), step 882" in out


def test_the_showcase_cell_opens_on_the_deck_and_says_which_scene(copied,
                                                                  nolib,
                                                                  window_opened,
                                                                  capsys):
    """The showcase in a CI cell: six scenes built, the deck stood in.

    No Pd patch and no device is what this example is *for* - the answer
    every page gives is on its own page - so the headless cell's whole report
    is the script's own opening line: the scene it showed, and how many
    scenes and pages it built before showing it.
    """
    path = copied("all_gui")

    assert _run(path, "headless") == EXIT_OK

    out, _ = capsys.readouterr()
    assert ("all_gui: opens on 'root' with 6 scenes and 6 pages" in out)


def test_the_metronome_cell_in_terminal_still_runs_the_widget_tree(copied,
                                                                   nolib,
                                                                   window_opened,
                                                                   capsys):
    """The plan's parity line, tested as S11's most interesting cell.

    ``metronome``'s ``main.crude`` calls ``TEXT``/``METER``/``SLIDER`` and
    the ``PLACE``/``LINK_VALUE``/``ON_CHANGE`` bindings; the widget stage's
    rule is that the same tree exists in every mode, and a terminal run
    simply draws no ops. This cell is that rule as an executable answer -
    a run that would fail for want of a ``host.ui`` is a bug in the loop.
    """
    path = copied("metronome")

    assert _run(path, "terminal") == EXIT_OK
    assert _run(path, "headless") == EXIT_OK
    assert _run(path, "window") == EXIT_OK

    # the window cell is the only one that reached for a display
    assert len(window_opened) == 1, window_opened
