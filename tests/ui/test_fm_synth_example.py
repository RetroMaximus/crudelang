"""The fm_synth example, on paper, as the plan's acceptance line.

``examples/fm_synth`` is where the whole four-stage stack first exists in something
a person can run: ``root`` keeps the front panel with Play on it, ``voices`` carries
the FM parameters inside a scroll area, the ``View`` menu lists both, that page is
built out of the layout verbs, and its three sections are groups - foldable,
re-orderable from a menu, and dockable into the spare cell of the grid they sit in.
This file runs the shipped script - no copy of it lives here - and asks the plan's
questions of the tree it builds.

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

The layout verbs are asserted here rather than only in their own file, because the
question this example answers is whether a script can build a page out of them:
grids and stacks that place the boxes and the help lines, ``fill`` rows that are as
wide as the viewport, glues that carry the scope and the status line through a
resize, and ``GET`` reading every one of those decisions back.

The groups are asserted the same way, because stage 4's acceptance line is this
scene: three groups in a dockable grid, the fold and the reorder driven by menu rows
that a click runs through the handler bridge, a header dragged to the free cell, and
the refusal a drop onto a taken cell gives in words.
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
from crude.ui.group import (body_of, chevron_box, folded_of, header_box,
                            header_of)
from crude.ui.grid import spec_of
from crude.ui.menu import BAR_HEIGHT, Menu, MenuBar
from crude.ui.scroll import view_of, view_width

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


def settle(stage, frames: int = 6) -> int:
    """Layout until a frame answers that nothing moved; how many frames it took.

    The scene's own geometry is already right after the first pass - every test that
    needs it says so below - but a group's ``cell`` and ``order`` are rows measured
    after the arrangement, so the numbers a script reads are only all true one frame
    later, which is exactly what a live window does anyway.
    """
    moved = [stage.ui.layout() for _ in range(frames)]
    assert moved[-1] == 0, f"the scene never settled: {moved}"
    return len(moved)


def painted(stage):
    """One frame of the shipped tree, recorded rather than drawn."""
    held = RecordingRenderer()
    held.begin(640, 420, 640, 420)
    stage.ui.root.paint(held, stage.ui.skin)
    return held


def pick(stage, menu: str, label: str):
    """Trigger one shipped menu row, through the popup's own button."""
    held = menu_of(stage.say(menu))
    held.show()
    row = [item for item in held.items if item.label == label][0]
    row.trigger()
    return row


def refusal(stage, source: str) -> str:
    """What the bindings answer when a script asks for something impossible.

    :meth:`say` insists a script line worked, so the error is collected here - whole,
    because the traceback is the first line and the sentence the plan asks for is the
    one after ``Runtime Error: ``.
    """
    value, error = stage.program.run(source)
    assert error is not None, f"{source} was accepted: {value}"
    return error.as_string()


def sections(stage) -> List[Widget]:
    """The three groups the voices page is built out of, in script order."""
    return [widget(stage.say(word)) for word in
            ("carrier", "modulator", "filter")]


def open_box_at(open_boxes, groups, there):
    """The box this group had before the fold, read off the list taken above."""
    return open_boxes[groups.index(there)]


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
    # what went *inside* the viewport is three blocks - the grid of sections, the
    # graph and a column of notes - each holding its own children, because a
    # NODE_ADD under a scroll is routed to the content panel rather than landing
    # beside it
    deck = widget(stage.say("deck"))
    blocks = [deck] + [widget(stage.say(word)) for word in ("graph", "notes")]
    assert all(block.parent is page.content_child for block in blocks)
    assert page.content_child.children == blocks, \
        "the shipped NODE_ADD order is the content order"
    # the deck holds the three groups and nothing else, and a group's children are
    # the two panels it was built out of - the script never had to dodge them
    groups = sections(stage)
    assert deck.children == groups
    for there in groups:
        assert there.kind.name == "group"
        assert there.parent is deck
        assert there.children == [header_of(there), body_of(there)]
        assert there.workspace == VOICES
    # each control went into the group the script named, which is the same claim
    # ``NODE_ADD`` makes under a viewport - the body, not beside the box
    assert [kid.name for kid in body_of(groups[0]).children] == ["Level"]
    assert [kid.name for kid in body_of(groups[1]).children] == ["Index"]
    assert [kid.name for kid in body_of(groups[2]).children] == ["Cutoff"]
    assert body_of(groups[0]).children[0] is widget(stage.say("gate"))
    assert body_of(groups[1]).children[0] is widget(stage.say("index"))
    assert body_of(groups[2]).children[0] is widget(stage.say("cut"))
    assert blocks[2].children == [widget(stage.say(word)) for word in
                                  ("note_index", "note_level", "note_cut",
                                   "note_view")]
    # the front panel left, and left as a carry: the same objects, off the tree
    assert widget(stage.say("play")).parent is None


def test_the_page_is_taller_than_the_viewport_and_reaches_its_bottom(stage):
    """Travel is measured off the shipped placements, and a scroll uses all of it."""
    show(stage, VOICES)
    settle(stage)
    page = widget(stage.say("page"))
    note = widget(stage.say("note_view"))

    # the deck's two rows of cells, the gap under it, the graph and the column:
    # every one of those numbers is placed by the script and measured by the pass,
    # so the page is 590 tall in a window that gives it 370
    assert float(page.get("content")) == pytest.approx(590.0)
    travel = float(page.get("travel"))
    assert travel == pytest.approx(590.0 - view_of(page))
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


# -- the layout verbs the example runs ----------------------------------


def test_the_sections_are_a_dockable_grid_the_script_named_and_the_engine_solved(stage):
    """Three groups, four cells: the script named two columns, the rows are what the
    children need, and the cell left over is the one a hand can drop a box into."""
    show(stage, VOICES)
    settle(stage)
    deck = widget(stage.say("deck"))
    groups = sections(stage)
    knobs = [widget(stage.say(word)) for word in ("gate", "index", "cut")]

    assert [float(deck.get(word)) for word in
            ("grid_columns", "grid_rows", "grid_gap")] == [2.0, 2.0, 18.0]
    assert float(deck.get("dockable")) == 1.0
    assert [float(there.get("dockable")) for there in groups] == [1.0, 1.0, 1.0]
    # the cell a child is drawn in and its place in the list are both measured rows,
    # read back from the tree rather than from the order the script wrote them in
    assert [float(there.get("cell")) for there in groups] == [0.0, 1.0, 2.0]
    assert [float(there.get("order")) for there in groups] == [0.0, 1.0, 2.0]

    left, top, width, height = deck.content_box()
    cell = (width - 18.0) / 2.0
    row = (height - 18.0) / 2.0
    assert cell == pytest.approx(290.0)
    assert row == pytest.approx(106.0), "the deck's own 230 is two open boxes"
    for slot, there in enumerate(groups):
        assert there.screen_box == pytest.approx(
            (left + (slot % 2) * (cell + 18.0),
             top + (slot // 2) * (row + 18.0), cell, 106.0))

    # a group is as tall as its band plus what its body holds, which is the one
    # height in this scene no line of the script writes
    for there, knob in zip(groups, knobs):
        assert there.height == pytest.approx(header_box(there)[3] + 18.0 +
                                             knob.height)
        assert knob.parent is body_of(there)
        assert knob.width == 64.0 and knob.height == 64.0, \
            "the size a script placed is the size its body keeps"
        # one ``align center`` row per body: the dial in the middle of the box, the
        # same x-arithmetic a cell used to give the knob when the three of them sat
        # in the grid directly
        room = body_of(there)
        assert knob.screen_box[0] == pytest.approx(
            room.screen_box[0] + (room.width - 64.0) / 2.0, abs=1.0)


def test_the_grid_offers_one_cell_nobody_is_sitting_in(stage):
    """``GRID`` gives a panel as many cells as its children need, and three boxes in
    two columns therefore make a fourth - the cell the script left empty so that a
    drag has somewhere to go, and the one a number past it is refused for.
    """
    show(stage, VOICES)
    settle(stage)
    deck = widget(stage.say("deck"))

    assert [there.name for there in stage.say("GROUPS(deck)")] == \
        ["carrier", "modulator", "filter"]
    assert float(deck.get("grid_columns")) * float(deck.get("grid_rows")) == 4.0
    assert [float(there.get("cell")) for there in sections(stage)] == [0.0, 1.0, 2.0]

    said = refusal(stage, "GROUP_DOCK(filter, 4)")
    assert "Runtime Error: GROUP_DOCK: " in said
    assert "has 4 cells, counted from 0, and 4 is not one of them" in said


def test_the_note_lines_are_one_column_with_one_gap(stage):
    """``VSTACK(notes, 20, ["align", "stretch"])``: a stride, and lines as wide as
    the column instead of centred at the width each one was placed at."""
    show(stage, VOICES)
    notes = widget(stage.say("notes"))
    lines = [widget(stage.say(word)) for word in
             ("note_index", "note_level", "note_cut", "note_view")]

    assert notes.children == lines
    left, top, width, _height = notes.screen_box
    for slot, line in enumerate(lines):
        assert line.parent is notes
        assert line.height == 20.0
        assert line.screen_box[0] == pytest.approx(left)
        assert line.width == pytest.approx(width)
        assert line.screen_box[1] == pytest.approx(top + slot * 40.0)


def test_a_row_that_fills_is_as_wide_as_the_room_the_viewport_gives(stage):
    """``PACK(block, "fill", "fixed")`` is measured against the content panel, which
    is the width the viewport hands it - not against the window this file names."""
    show(stage, VOICES)
    page = widget(stage.say("page"))
    blocks = [widget(stage.say(word)) for word in ("deck", "graph", "notes")]

    for block in blocks:
        assert block.width == pytest.approx(view_width(page) - 24.0)

    stage.ui.set_size(900, 700)
    stage.ui.layout()
    # the pass's own order, which the plan fixes: the page is glued, so its new box
    # is written by the anchor sweep that runs after the rows were arranged inside
    # it, and the rows catch up on the next frame - the one a live window is drawing
    # anyway, sixteen milliseconds later and never seen
    stage.ui.layout()
    for block in blocks:
        assert block.width == pytest.approx(view_width(page) - 24.0)
        assert block.screen_box[0] + block.width <= \
            page.content_box()[0] + view_width(page), "the row stops in the frame"


def test_what_a_layout_verb_wrote_GET_reads_back(stage):
    """The verbs are facts about the tree, not one-way calls: every decision this
    script made is readable off the widget it was made about."""
    assert stage.say('GET(gain, "pack")') == ["grow", "fixed"]
    assert stage.say('GET(scope, "place")') == [
        "left -> parent.left +336", "right -> parent.right -24"]
    assert stage.say('GET(log, "place")') == [
        "left -> parent.left", "right -> parent.right", "bottom -> parent.bottom"]
    show(stage, VOICES)
    assert stage.say('GET(notes, "place")') == [], "a stack is not a glue"
    assert float(stage.say('GET(deck, "grid_columns")')) == 2.0
    assert float(stage.say('GET(deck, "grid_rows")')) == 2.0


def test_the_scope_and_the_status_line_follow_the_window(stage):
    """The regression the glues are the answer to: numbers that only fitted the
    640 x 420 window this script was written in, and the same page at 900 x 700."""
    scope = widget(stage.say("scope"))
    log = widget(stage.say("log"))
    gain = widget(stage.say("gain"))

    stage.ui.layout()
    assert scope.screen_box == pytest.approx((336, 52, 280, 150))
    assert log.screen_box == pytest.approx((0, 390, 640, 30))
    # the line is the page's width and inside the page it is glued to, rather than
    # running past the bottom edge the way a hand-written y would have
    assert log.width == pytest.approx(stage.ui.root.content_box()[2])
    assert log.screen_box[1] + log.height <= stage.ui.root.content_box()[3]

    stage.ui.set_size(900, 700)
    stage.ui.layout()
    # the right glue buys the scope the 260 px a placed 280 would have left over
    assert scope.screen_box == pytest.approx((336, 52, 540, 150))
    assert log.screen_box == pytest.approx((0, 670, 900, 30))
    # and ``grow`` is the same answer on one more control: 150 px of a 640 px page
    # is 211 px of a 900 px one, so the throw keeps its resolution
    assert gain.width == pytest.approx(150.0 * 900.0 / 640.0, abs=1.0)


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


# -- the groups on the voices page --------------------------------------


def test_the_fold_row_closes_every_body_and_the_open_row_gives_them_back(stage):
    """A menu row is the same gesture a hand on the chevron is, on all three boxes.

    The rows call ``GROUP_FOLD`` and nothing else: the script never resizes a group,
    because a fold is a measurement - the body's room is taken away and the box is
    what the band measures - and the pass writes the height back on the next frame.
    """
    show(stage, VOICES)
    settle(stage)
    groups = sections(stage)
    open_boxes = [there.screen_box for there in groups]

    pick(stage, "section_menu", "Fold every section")
    settle(stage)

    assert [float(there.get("folded")) for there in groups] == [1.0, 1.0, 1.0]
    for there in groups:
        assert not body_of(there).visible
        assert there.height == pytest.approx(header_box(there)[3] + 18.0)
        # ``align start`` on the grid: a closed band sits at the top of the cell its
        # open self measured, rather than in the middle of the room it left
        assert there.screen_box[:2] == pytest.approx(
            open_box_at(open_boxes, groups, there)[:2])
    # the cells do not shrink with their boxes: the deck is placed at two rows of
    # 106, and the room a page holds is the page's own, not a fold's
    page = widget(stage.say("page"))
    assert float(page.get("content")) == pytest.approx(590.0)

    words = painted(stage).texts()
    assert {"carrier", "modulator", "filter"} <= set(words)
    assert "Level" not in words and "Cutoff" not in words, \
        "a hidden body is neither painted nor clickable"
    assert painted(stage).count("atlas") == 3, "the three marks are still there"

    pick(stage, "section_menu", "Open every section")
    settle(stage)
    for there, before in zip(groups, open_boxes):
        assert there.screen_box == pytest.approx(before)
    assert all(body_of(there).visible for there in groups)


def test_a_chevron_under_the_pointer_folds_the_box_it_belongs_to(stage):
    """The mark the face draws and the target the gesture tests are one rectangle.

    The header panel is invisible on purpose, so a press in the band is claimed by
    the group - which is what makes the mark clickable without a second widget in
    the box to keep in the tree, in the clip and in the tab order.
    """
    show(stage, VOICES)
    settle(stage)
    carrier = widget(stage.say("carrier"))
    mark = chevron_box(carrier)
    assert mark[2] > 0.0 and mark[3] > 0.0

    stage.ui.router.click(mark[0] + mark[2] / 2.0, mark[1] + mark[3] / 2.0)
    settle(stage)

    assert folded_of(carrier)
    assert carrier.height == pytest.approx(header_box(carrier)[3] + 18.0)
    assert not body_of(carrier).visible
    # the other two boxes were not asked about
    assert [folded_of(there) for there in sections(stage)[1:]] == [False, False]

    stage.ui.router.click(mark[0] + mark[2] / 2.0, mark[1] + mark[3] / 2.0)
    settle(stage)
    assert not folded_of(carrier), "the same mark opens it again"


def test_the_reorder_rows_change_the_order_the_grid_lays_the_cells_out_in(stage):
    """``GROUP_MOVE`` is a re-arrange: the z-order is what a grid enumerates, so the
    cell a box is drawn in follows the list rather than the other way round.
    """
    show(stage, VOICES)
    settle(stage)
    deck = widget(stage.say("deck"))
    groups = sections(stage)

    pick(stage, "section_menu", "Filter first")
    settle(stage)
    assert [there.name for there in deck.children] == [
        "filter", "carrier", "modulator"]
    assert [float(there.get("cell")) for there in groups] == [1.0, 2.0, 0.0]
    assert groups[2].screen_box == pytest.approx(
        (deck.content_box()[0], deck.content_box()[1], 290.0, 106.0))

    pick(stage, "section_menu", "Carrier first")
    settle(stage)
    assert [there.name for there in deck.children] == [
        "carrier", "filter", "modulator"]
    assert [float(there.get("cell")) for there in groups] == [0.0, 2.0, 1.0]
    assert [float(there.get("order")) for there in groups] == [0.0, 2.0, 1.0]


def test_the_accordion_row_is_the_prop_on_the_panel_holding_the_groups(stage):
    """`One at a time` is the holder's rule, and the row writing it is a toggle.

    The checkable row latches its own value before the script's function runs, and
    the function reads that value back off the handle ``MENU_CHECK`` handed it - so
    the row is the prop, in both directions, with nothing kept anywhere else.
    """
    show(stage, VOICES)
    settle(stage)
    deck = widget(stage.say("deck"))
    groups = sections(stage)
    assert float(deck.get("accordion")) == 0.0

    row = pick(stage, "section_menu", "One at a time")
    assert row.checked
    assert float(deck.get("accordion")) == 1.0

    stage.say("GROUP_FOLD(carrier, 1)")
    settle(stage)
    assert [float(there.get("folded")) for there in groups] == [1.0, 0.0, 0.0]

    stage.say("GROUP_FOLD(carrier, 0)")
    settle(stage)
    # opening one closed the others: the accordion is the holder's answer about the
    # set, and it fires on the transition rather than on every frame
    assert [float(there.get("folded")) for there in groups] == [0.0, 1.0, 1.0]
    assert [there.height for there in groups] == [106.0, 42.0, 42.0]

    row_again = pick(stage, "section_menu", "One at a time")
    assert not row_again.checked and row is row_again
    assert float(deck.get("accordion")) == 0.0


def test_a_band_dragged_over_the_grid_lands_on_the_free_cell(stage):
    """The whole gesture on the shipped page: capture, outlines, drop, cell.

    Every number here is measured off the script's own ``GRID``, and the pointer goes
    through the router rather than onto a function: this is the test that says the
    scene a person runs is the scene the gesture works on.
    """
    show(stage, VOICES)
    settle(stage)
    deck = widget(stage.say("deck"))
    carrier = widget(stage.say("carrier"))
    band = header_box(carrier)

    stage.ui.router.move_to(band[0] + 20.0, band[1] + band[3] / 2.0)
    stage.ui.router.press()
    settle(stage)

    assert stage.ui.router.capture is carrier
    assert float(deck.get("docking")) == 1.0, "a drag is held"
    assert painted(stage).count("polyline") == 4, "all four cells are worth drawing"

    spare = spec_of(deck).rect_of(3, deck.content_box())
    assert spare == pytest.approx((341.0, 173.0, 290.0, 106.0))
    stage.ui.router.move_to(spare[0] + spare[2] / 2.0, spare[1] + spare[3] / 2.0)
    stage.ui.router.release()
    settle(stage)

    assert float(carrier.get("cell")) == 3.0
    assert carrier.screen_box == pytest.approx(spare)
    assert [there.name for there in deck.children] == [
        "modulator", "filter", "carrier"]
    assert [float(there.get("cell")) for there in sections(stage)] == [3.0, 1.0, 2.0]
    assert float(deck.get("docking")) == 0.0, "the hand let go"
    assert painted(stage).count("polyline") == 0

    assert float(stage.say("GROUP_UNDOCK(carrier)")) == -1.0
    settle(stage)
    # giving the claim up does not put the box back where it came from - it takes the
    # first cell the pass finds free, which is the one its own old cell vacated
    assert [float(there.get("cell")) for there in sections(stage)] == [0.0, 1.0, 2.0]


def test_a_cell_that_is_taken_is_refused_rather_than_stacked(stage):
    """The same fact two ways: a hand is answered by nothing moving, a script by a
    sentence that names the cell, the panel and what is already sitting in it.
    """
    show(stage, VOICES)
    settle(stage)
    groups = sections(stage)
    before = [there.screen_box for there in groups]
    band = header_box(groups[0])

    stage.ui.router.move_to(band[0] + 20.0, band[1] + band[3] / 2.0)
    stage.ui.router.press()
    settle(stage)
    deck = widget(stage.say("deck"))
    target = spec_of(deck).rect_of(1, groups[1].parent.content_box())
    stage.ui.router.move_to(target[0] + target[2] / 2.0,
                            target[1] + target[3] / 2.0)
    stage.ui.router.release()
    settle(stage)

    assert [there.screen_box for there in groups] == before, "nothing moved"
    assert float(groups[0].get("cell")) == 0.0

    said = refusal(stage, "GROUP_DOCK(carrier, 1)")
    assert "Runtime Error: GROUP_DOCK: " in said
    assert "cell 1 of " in said and "already holds" in said
    assert "it is a cell, not a stack" in said


def test_the_sections_menu_is_its_own_because_view_is_recomposed(stage):
    """``MENU_WORKSPACES`` throws its popup away and rebuilds it from the scenes there
    are now, so a fold row written beside it would vanish the next time a person
    clicked View - which is why this example writes six rows of its own.
    """
    view = menu_of(stage.say("view_menu"))
    section = menu_of(stage.say("section_menu"))
    bar = bar_of(stage.say("bar"))

    assert rows(section) == ["Fold every section", "Open every section",
                             "Carrier first", "Modulator first", "Filter first",
                             "One at a time"]
    section.show()
    assert rows(view) == [ROOT_WORKSPACE, VOICES], "the scene rows are not there"
    view.show()
    assert rows(view) == [ROOT_WORKSPACE, VOICES], "and they survive being opened"
    assert [menu.title for menu in bar.menus] == [
        "File", "View", "Audio", "MIDI", "Serial", "Sections"]


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
