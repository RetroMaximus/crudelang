"""The fm_synth example, on paper, as the plan's stage 2 acceptance line.

``examples/fm_synth`` is where two named scenes first exist in something a person
can run: ``root`` keeps the front panel with Play on it, ``voices`` carries the FM
parameters inside a scroll area, and the ``View`` menu lists both. This file runs
the shipped script - no copy of it lives here - and asks the plan's questions of
the tree it builds.

The stage is a real one, in the same shape as ``test_metronome_example.py`` and
one step further: a :class:`~crude.project.Project` loaded from the example's own
manifest, so the ``ui.workspaces`` key is read by the validator and seeded by the
real :class:`~crude.runtime.Runtime` the way a ``crude run`` seeds it. Behind that
are the fakes a machine without a GPU, a DLL or a device can only have -
``FakeLibpd``, ``NullBackend``, ``RecordingRenderer`` - and none of them is doing
the work being tested: the scenes, the carry, the scroll and the menu rows are all
the product's own code.

What is asserted, in the plan's words: ``root`` keeps the front panel, ``voices``
carries the FM parameters, and there is a ``View`` menu of the two names - plus
the parts of stage 1 that only show up once a script puts a scroll area on a page
of its own: travel that is real, a wheel that reaches it, and numbers that survive
a switch.
"""

from __future__ import annotations

import logging
import types
from pathlib import Path
from typing import Any, List

import pytest

from crude.audio import AudioConfig, NullBackend
from crude.audio.runtime import AudioRuntime
from crude.lang import Program
from crude.lang.values import HostHandle, List, Number, String
from crude.pd import FakeLibpd, PdRuntime, PdSession
from crude.project import LoadOptions, Project, ResourceTable, load_manifest
from crude.runtime import FakeClock, Runtime
from crude.ui import ROOT_WORKSPACE, RecordingRenderer, UIContext, Widget
from crude.ui.menu import BAR_HEIGHT, Menu, MenuBar
from crude.ui.scroll import view_of

#: the repository root, whatever directory pytest was started in
ROOT = Path(__file__).resolve().parents[2]

#: the example as shipped: manifest and entry script, read from the same files a
#  ``crude run examples/fm_synth`` reads
EXAMPLE = ROOT / "examples" / "fm_synth"
MANIFEST = EXAMPLE / "crude.manifest.json"
SOURCE = (EXAMPLE / "main.crude").read_text(encoding="utf-8")

#: the second page this example declares, in its manifest and in its script
VOICES = "voices"


# -- helpers ------------------------------------------------------------


def as_py(value: Any) -> Any:
    """A language value as the plain Python object it stands for."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, List):
        return [as_py(item) for item in value.elements]
    if isinstance(value, HostHandle):
        return value.target
    return value


def widget(value: Any) -> Widget:
    """The widget a script's variable stands for, insisting that it is one."""
    assert isinstance(value, Widget), value
    return value


def menu_of(value: Any) -> Menu:
    assert isinstance(value, Menu), value
    return value


def bar_of(value: Any) -> MenuBar:
    """The ``MENUBAR`` answer: a bar, whose panel is the widget in the tree."""
    assert isinstance(value, MenuBar), value
    return value


def lines_of(stage) -> List[str]:
    """What the example's log view is showing, read the way a script reads it."""
    return [str(line) for line in stage.say('GET(log, "lines")')]


# -- the stage ----------------------------------------------------------


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


def _stage(tmp_path, name: str, renderer: RecordingRenderer, *,
           painting: bool = True):
    """The shipped example, run once, inside a project loaded from its manifest.

    Everything the pipeline would have opened is handed in instead: the in-memory
    library for Pd, a pulled null stream for audio, a recorder for the screen. The
    project itself is real - its manifest is validated by the same
    :func:`~crude.project.manifest.load_manifest` the CLI uses, and its resource
    table parses the same ``assets/modindex.txt`` and ``presets/bell.json`` - so
    the scene names the script's ``WORKSPACE("voices")`` adopts are the names the
    manifest declared.
    """
    manifest = load_manifest(str(MANIFEST))
    project = Project(manifest,
                      options=LoadOptions(progress="none", mode="headless"),
                      resources=ResourceTable(manifest),
                      clock=FakeClock(target_fps=int(manifest.runtime.target_fps)))

    lib = FakeLibpd()
    session = PdSession(lib)
    session.start()
    notes: List[str] = []
    home = tmp_path / name
    home.mkdir()
    project.pd = PdRuntime(session, project_dir=str(home), log=notes.append)
    project.audio = AudioRuntime(config=AudioConfig(), backend=NullBackend())
    project.ui = UIContext(renderer, width=float(manifest.runtime.width),
                           height=float(manifest.runtime.height),
                           painting=painting)

    # the loop is built and activated before the script runs, because that is the
    # order a real run has it in: phase 15 wires the UI and seeds the manifest's
    # scene names, and only then does the entry script get an interpreter
    loop = Runtime(project, mode="headless", clock=project.clock, ui=project.ui)
    loop.activate(project)

    program = Program(manifest.entry, host=project.host_object())
    project.attach_program(program)

    def say(source: str) -> Any:
        """Type ``source`` at the running script and get the Python back."""
        value, error = program.run(source)
        assert error is None, error.as_string()
        return as_py(value)

    say(SOURCE)
    # the patch's queued work (the .pd file, the table fill) lands on a real run's
    # first scheduler pass, so it is flushed here rather than assumed
    project.pd.flush()
    project.ui.layout()

    namespace = types.SimpleNamespace(
        project=project, manifest=manifest, lib=lib, session=session,
        renderer=renderer, ui=project.ui, loop=loop, program=program,
        notes=notes, say=say)
    yield namespace

    project.audio.close()
    session.shutdown()
    project.close_logging()


@pytest.fixture
def stage(tmp_path):
    """The example in a window that is only a recording renderer."""
    yield from _stage(tmp_path, "windowed", RecordingRenderer())


@pytest.fixture
def headless(tmp_path):
    """The same example with ``painting`` off: the same tree, zero ops."""
    yield from _stage(tmp_path, "headless", RecordingRenderer(),
                      painting=False)


def show(stage, name: str) -> str:
    """Switch pages the way a script does, and give the layout back its frame."""
    answer = stage.say(f'WORKSPACE_SHOW("{name}")')
    stage.ui.layout()
    return answer


def rows(menu) -> List[str]:
    """The labels a menu is holding, in order."""
    return [item.label for item in menu.items]


# -- the two pages ------------------------------------------------------


def test_the_example_declared_its_second_page_in_its_manifest(stage):
    """The ``ui.workspaces`` key is read, validated and seeded before the script runs.

    This is the line a paper stand-in could not prove: the names come from the
    example's own ``crude.manifest.json`` and reach the window through the real
    runtime's seeding pass, which is why the script's ``WORKSPACE("voices")``
    adopts the reserved name instead of being refused for a duplicate.
    """
    assert stage.manifest.ui.workspaces == (VOICES,)
    assert stage.ui.workspaces.names() == [ROOT_WORKSPACE, VOICES]
    assert stage.ui.workspaces.index_of(VOICES) == 1


def test_the_run_opens_on_the_front_page(stage):
    """The script ends with ``WORKSPACE_SHOW("root")``: what a person sees first has
    Play on it, and the page they do not see is carried rather than drawn."""
    held = stage.ui.workspaces

    assert held.current_name == ROOT_WORKSPACE
    assert held.get(VOICES).attached is False
    # the front panel's widgets are the window's own children, as they always were
    play = widget(stage.say("play"))
    assert play.parent is stage.ui.root
    assert play.workspace == ROOT_WORKSPACE
    # and the voices page is not in the tree at all
    assert stage.ui.find("fm parameters") is None


def test_the_voices_page_is_a_scene_of_its_own_with_the_fm_parameters_on_it(stage):
    show(stage, VOICES)
    held = stage.ui.workspaces

    assert held.current_name == VOICES
    panel = held.get(VOICES).panel
    assert panel is not None and panel.parent is stage.ui.root
    page = widget(stage.say("page"))
    assert page.parent is panel
    assert page.kind.name == "scroll"
    assert page.workspace == VOICES
    # every knob went into the viewport's own content panel, not alongside it, and
    # in the order the script added them - which is the order the wheel moves through
    inside = [widget(stage.say(word)) for word in
              ("index", "gate", "cut", "graph", "note_index", "note_level",
               "note_cut", "note_view")]
    assert all(control.parent is page.content_child for control in inside)
    assert page.content_child.children == inside, \
        "the shipped NODE_ADD order is the content order"
    # the front panel left, and left as a carry: the same objects, off the tree
    assert widget(stage.say("play")).parent is None


def test_the_page_is_taller_than_the_viewport_and_reaches_its_bottom(stage):
    """Travel is measured off the shipped placements, and a scroll uses all of it."""
    show(stage, VOICES)
    page = widget(stage.say("page"))
    note = widget(stage.say("note_view"))

    assert float(page.get("content")) == pytest.approx(440.0)
    travel = float(page.get("travel"))
    assert travel == pytest.approx(440.0 - view_of(page))
    assert travel > 0.0, "the shipped page does not fit its viewport"

    # at the top the last line of the page is below the viewport's bottom edge
    assert note.screen_box[1] > page.screen_box[1] + view_of(page)
    # and the wheel - the router's own scroll, over the page - can bring it back
    left, top, width, _ = page.screen_box
    stage.ui.on_cursor(left + width - 20.0, top + 20.0)
    stage.ui.dispatch()
    for _ in range(20):
        stage.ui.on_scroll(0.0, -1.0)
        stage.ui.dispatch()

    assert float(page.get("offset")) == pytest.approx(-travel), "the bottom holds"
    assert note.screen_box[1] < page.screen_box[1] + view_of(page)


def test_a_scene_glued_below_the_bar_fills_the_window_at_two_sizes(stage):
    """The page's geometry is the layout engine's answer, not a placed constant."""
    show(stage, VOICES)
    panel = stage.ui.workspaces.get(VOICES).panel

    def room():
        left, top, width, height = stage.ui.root.content_box()
        return (left, top + BAR_HEIGHT, width, height - BAR_HEIGHT)

    stage.ui.layout()
    assert panel.screen_box == pytest.approx(room())
    stage.ui.set_size(900, 700)
    stage.ui.layout()
    assert panel.screen_box == pytest.approx(room())


# -- what a switch keeps ------------------------------------------------


def test_the_knobs_keep_their_numbers_over_a_switch(stage):
    """``keep`` is a carry, not a memory test: a page that went away comes back set."""
    index = widget(stage.say("index"))
    show(stage, VOICES)
    stage.say('SET(index, "value", 11)')
    assert float(index.get("value")) == 11.0

    show(stage, ROOT_WORKSPACE)
    assert stage.ui.find("fm parameters") is None
    show(stage, VOICES)

    again = widget(stage.say("index"))
    assert again is index, "the same widget, not a rebuilt one"
    assert float(again.get("value")) == 11.0
    assert stage.ui.find("fm parameters") is widget(stage.say("page"))


def test_a_frame_reports_the_same_numbers_from_either_page(stage):
    """The script's ``ON_FRAME`` reads carried widgets, so the status line lies on
    neither page: the log is on ``root`` and the index is on ``voices``."""
    play = widget(stage.say("play"))
    stage.say('SET(play, "value", 1)')

    stage.loop.step()
    assert stage.ui.workspaces.current_name == ROOT_WORKSPACE
    said = lines_of(stage)
    assert "playing @ pitch 69" in said[0] and "index 7" in said[0]

    # the log view lives on root and the index knob lives on voices: whichever page
    # is on screen, one frame reads both, because a carry moved the widgets and not
    # their numbers
    show(stage, VOICES)
    stage.loop.step()
    assert lines_of(stage) == said


# -- the View menu ------------------------------------------------------


def test_the_view_menu_lists_both_pages_and_ticks_the_one_on_screen(stage):
    """The rows are recomposed as the popup opens, so the tick is always the page
    a person is looking at - including the one the script left showing at the end."""
    menu = menu_of(stage.say("view_menu"))

    assert rows(menu) == [ROOT_WORKSPACE, VOICES]
    menu.show()
    assert [item.checked for item in menu.items] == [True, False]

    show(stage, VOICES)
    menu.show()                       # the rows are recomposed as the popup opens
    assert [item.checked for item in menu.items] == [False, True]


def test_picking_a_row_in_the_menu_turns_the_page(stage):
    """A click on the row, through the popup's own button, is a ``WORKSPACE_SHOW``."""
    menu = menu_of(stage.say("view_menu"))
    menu.show()
    stage.ui.layout()

    target = [item for item in menu.items if item.label == VOICES][0]
    target.trigger()

    assert stage.ui.workspaces.current_name == VOICES
    assert stage.ui.find("fm parameters") is not None
    assert menu.is_open() is False, "picking a row closes the popup, as any row does"


def test_the_bar_and_its_menus_are_the_windows_own_and_survive_a_switch(stage):
    """A scene cannot take the menu down with it: the bar is stamped ``shared``."""
    bar = bar_of(stage.say("bar"))
    before = list(stage.ui.root.children)

    show(stage, VOICES)
    after = list(stage.ui.root.children)

    assert bar.panel.workspace == "shared"
    assert bar.panel in after
    # the bar keeps its place at the bottom of the stack: a scene that comes back is
    # added after it, which is where a widget added after the bar has always landed
    assert after.index(bar.panel) == before.index(bar.panel)
    assert all(child.workspace in ("shared", VOICES) for child in after)


# -- the plan's parity line ---------------------------------------------


def test_a_run_that_paints_nothing_still_has_both_pages(headless):
    """``--mode headless`` of the same script: the same tree, the same scenes, and
    not one op drawn - so a scene switch is not a drawing feature."""
    assert headless.ui.workspaces.names() == [ROOT_WORKSPACE, VOICES]

    show(headless, VOICES)
    page = widget(headless.say("page"))
    assert page.kind.name == "scroll"
    assert float(page.get("travel")) > 0.0
    assert headless.renderer.ops == []
