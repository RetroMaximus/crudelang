"""The fm_synth example, on paper: one page, three sections, one help dialog.

``examples/fm_synth`` is the example that says the four-stage stack works as a
toolkit rather than as a trick: ``root`` is the whole instrument - a two-operator
FM voice, a wavetable per oscillator, an envelope per oscillator - and the control
surface is built out of three groups in a dockable grid *on that page*. There is no
second scene. An earlier draft put the sections on a ``voices`` workspace behind a
``Sections`` menu, which lost the Index, Level and Cutoff knobs out of sight and
answered a layout question with a page switch; this file pins the redesign: the
sections are on the page, every knob a section owns is drawn on it, and the menu
that used to change the page is a ``Help`` menu that opens a scrollable page the
script built out of ordinary widgets.

The stage is a real one, in the same shape as ``test_metronome_example.py``: a
:class:`~crude.project.Project` loaded from the example's own manifest, the shipped
``main.crude`` run by the shipped ``Program``, and behind them the fakes a machine
without a GPU, a DLL or an audio device can only have - ``FakeLibpd``,
``NullBackend``, ``RecordingRenderer``. None of them is doing the work being tested:
the grid, the fold, the dock, the envelope lists and the wheel are the product's.

What is asserted, in the order a person would find it:

* the page is one page - no declared workspace, one scene, three groups in a
  two-column grid with the fourth cell left free for a band to be dropped into;
* no section is a box of one knob - every section holds three controls, and the two
  knobs that answer one question were put in one panel by the script;
* the widgets are in the panels they were built for (the side column's transport is
  *inside* the side column, which is the one thing ``PLACE`` cannot say twice);
* the patch the script authored has the receivers the knobs write to, two 260-sample
  tables, and the arithmetic a synthesist expects: ``ratio`` is the harmonic plus
  one, ``index`` is the deviation in multiples of the modulator, an ADSR row is a
  fraction of the span and the span is milliseconds - because ``vline~`` wants
  milliseconds;
* folding, re-ordering, the accordion, dragging a band to the free cell and the
  refusal a taken cell gives, all driven through the router rather than a function;
* the Help menu and its dialog: closed means out of the pointer's way, opening puts
  the text at its first line, the wheel walks it to the last, and the Close button
  is the same door as the menu row.

No test here calls ``PRESET_SAVE``: it writes into the project directory, and a test
must not leave a file in the source tree. The round trip is asserted with the two
presets the example ships.
"""

from __future__ import annotations

import json
import logging
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

from crude.audio import AudioConfig, NullBackend
from crude.audio.runtime import AudioRuntime
from crude.lang import Program
from crude.lang.values import HostHandle, List, Number, String
from crude.pd import FakeLibpd, PdRuntime, PdSession
from crude.project import LoadOptions, Project, ResourceTable, load_manifest
from crude.runtime import FakeClock, Runtime
from crude.ui import ROOT_WORKSPACE, RecordingRenderer, UIContext, Widget
from crude.ui.behaviour import behaviour_for
from crude.ui.group import body_of, chevron_box, folded_of, header_box
from crude.ui.grid import spec_of
from crude.ui.input import SCROLL_NOTCH
from crude.ui.menu import Menu, MenuBar, MenuItem

#: the repository root, whatever directory pytest was started in
ROOT = Path(__file__).resolve().parents[2]

#: the example as shipped: manifest and entry script, read from the same files a
#  ``crude run examples/fm_synth`` reads
EXAMPLE = ROOT / "examples" / "fm_synth"
MANIFEST = EXAMPLE / "crude.manifest.json"
SOURCE = (EXAMPLE / "main.crude").read_text(encoding="utf-8")

#: the window the manifest asks for; every box below is measured inside it
manifest = load_manifest(str(MANIFEST))
WIDTH = int(manifest.runtime.width)
HEIGHT = int(manifest.runtime.height)

#: the seven control-rate receivers the patch declares and the panel writes to
RECEIVERS = ["cenv", "cutoff", "gain", "index", "menv", "pitch", "ratio"]

#: one cycle, the guard sample before it and the three taps after: the length every
#  wavetable in this example is authored at, so [tabread4~] never reads off an end
TABLE = 260


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


def rows(menu: Menu) -> List[str]:
    """The labels a menu is holding, in order."""
    return [item.label for item in menu.items]


def lines_of(stage) -> List[str]:
    """What the example's log view is showing, read the way a script reads it."""
    return [str(line) for line in stage.say('GET(log, "lines")')]


def settle(stage, frames: int = 6) -> int:
    """Layout until a frame answers that nothing moved; how many frames it took.

    A group's ``cell`` and ``order`` are rows measured *after* the arrangement, so
    the numbers a script reads are only all true one pass later - which is what a
    live window does anyway, and why a gesture test calls this rather than trusting
    the box it pressed on.
    """
    moved = [stage.ui.layout() for _ in range(frames)]
    assert moved[-1] == 0, f"the scene never settled: {moved}"
    return len(moved)


def painted(stage) -> RecordingRenderer:
    """One frame of the shipped tree, recorded rather than drawn.

    A fresh recorder every call: a cached face left over from the last frame would
    say a line is still on the screen after the widget that drew it went away.
    """
    held = RecordingRenderer()
    held.begin(WIDTH, HEIGHT, WIDTH, HEIGHT)
    stage.ui.root.paint(held, stage.ui.skin)
    return held


def shown_words(stage, words: List[str]) -> List[str]:
    """The help lines this frame paints, in draw order - the visible window."""
    painted_frame = painted(stage)
    said = set(words)
    return [text for text in painted_frame.texts() if text in said]


def press(stage, x: float, y: float) -> int:
    """One whole left click at a point of the window; how many events it took."""
    stage.ui.on_cursor(x, y)
    stage.ui.on_button(0, 1, 0)
    stage.ui.on_button(0, 0, 0)
    return stage.ui.dispatch()


def click(stage, held: Widget, dx: float = 0.5, dy: float = 0.5) -> int:
    """One whole click at a fraction of one widget's own plate."""
    left, top, width, height = held.content_box()
    return press(stage, left + width * dx, top + height * dy)


def drag_vertical(stage, held: Widget, moved: float) -> int:
    """Press the middle of a dial and move it ``moved`` pixels down; the count.

    A ``knob`` is a vertical range that never jumps, so the only gesture that means
    anything is the drag - and the number a person gets is measured against the
    knob's own height, which is why this asks the widget where its plate is rather
    than taking a pixel count from the test.
    """
    left, top, width, height = held.content_box()
    x, y = left + width / 2.0, top + height / 2.0
    stage.ui.on_cursor(x, y)
    stage.ui.on_button(0, 1, 0)
    stage.ui.dispatch()
    stage.ui.on_cursor(x, y + moved)
    stage.ui.dispatch()
    stage.ui.on_button(0, 0, 0)
    return stage.ui.dispatch()


def pickup(stage, held: Widget) -> None:
    """Hold a section by its band, the way a hand does: press inside the header."""
    band = header_box(held)
    stage.ui.router.move_to(band[0] + 20.0, band[1] + band[3] / 2.0)
    stage.ui.router.press()
    settle(stage)


def carry(stage, box) -> None:
    """Move the held band over a cell and let go there."""
    stage.ui.router.move_to(box[0] + box[2] / 2.0, box[1] + box[3] / 2.0)
    stage.ui.router.release()
    settle(stage)


def drain(stage, recv: str = "") -> List[tuple]:
    """What the scheduler hands to Pd now, and nothing that went before.

    A send is queued against a receiver and lands on the next pass, so a test that
    reads ``lib.sent`` without flushing is reading last frame's panel. The flush is
    the point: it is the same one a running window takes once per block.
    """
    stage.pd.flush()
    found = [entry for entry in stage.lib.sent if not recv or entry[1] == recv]
    stage.lib.sent.clear()
    return found


def quiet(stage) -> None:
    """Land everything the load put in the queue, then forget it."""
    stage.pd.flush()
    stage.lib.sent.clear()


def lists_to(recv: str, entries: List[tuple]) -> List[tuple]:
    """The ``vline~`` lists one receiver was handed, as plain tuples."""
    return [entry[2] for entry in entries if entry[0] == "list" and entry[1] == recv]


def floats_to(recv: str, entries: List[tuple]) -> List[float]:
    """The numbers one receiver was handed."""
    return [entry[2] for entry in entries
            if entry[0] == "float" and entry[1] == recv]


def pick(stage, word: str, label: str) -> MenuItem:
    """Trigger one row of a shipped menu, through the popup's own item.

    The row is opened and closed on the way, because that is the path a hand takes -
    :meth:`MenuItem.trigger` is what the popup calls, and a test that called the
    script's function instead would be testing the function rather than the menu.
    """
    held = menu_of(stage.say(word))
    held.show()
    row = [item for item in held.items if item.label == label][0]
    row.trigger()
    held.hide()
    return row


def sections(stage) -> List[Widget]:
    """The three groups the page is built out of, in the script's own order."""
    return [widget(stage.say(word)) for word in
            ("voice", "modifier", "envelopes")]


def controls(group: Widget) -> List[str]:
    """The kinds inside one section that answer a hand, in draw order.

    This is the assertion the brief asked for - "do not put a single knob in its own
    group" - made checkable: a section whose answer is one control is a list, not a
    panel, and no fold or dock on it is worth the header it is drawn with.
    """
    return [held.kind.name for held in group.descendants()
            if behaviour_for(held.kind) and held.kind.name != "group"]


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
    project is real, so the manifest is validated by the same loader the CLI uses
    and the five ``resources.arrays`` entries are parsed by the same resource table
    the run reads - which is how a voice written into a table is the file's samples
    and not a fixture's invention.
    """
    held_manifest = load_manifest(str(MANIFEST))
    project = Project(held_manifest,
                      options=LoadOptions(progress="none", mode="headless"),
                      resources=ResourceTable(held_manifest),
                      clock=FakeClock(
                          target_fps=int(held_manifest.runtime.target_fps)))

    lib = FakeLibpd()
    session = PdSession(lib)
    session.start()
    notes: List[str] = []
    home = tmp_path / name
    home.mkdir()
    pd = PdRuntime(session, project_dir=str(home), log=notes.append)
    project.pd = pd
    project.audio = AudioRuntime(config=AudioConfig(), backend=NullBackend())
    project.ui = UIContext(renderer, width=float(held_manifest.runtime.width),
                           height=float(held_manifest.runtime.height),
                           painting=painting)

    # the loop is built and activated before the script runs, because that is the
    # order a real run has it in: the UI is wired, then the entry script is read
    loop = Runtime(project, mode="headless", clock=project.clock,
                   ui=project.ui)
    loop.activate(project)

    program = Program(held_manifest.entry, host=project.host_object())
    project.attach_program(program)

    def say(source: str) -> Any:
        """Type ``source`` at the running script and get the Python back."""
        value, error = program.run(source)
        assert error is None, error.as_string()
        return as_py(value)

    def held(word: str) -> Widget:
        return widget(say(word))

    say(SOURCE)
    # the patch's queued work (the .pd file, the table fill) lands on a real run's
    # first scheduler pass, so it is flushed here rather than assumed
    pd.flush()
    moved = [project.ui.layout() for _ in range(6)]
    assert moved[-1] == 0, f"the shipped page never settled its layout: {moved}"

    namespace = types.SimpleNamespace(
        project=project, manifest=held_manifest, lib=lib, session=session,
        pd=pd, renderer=renderer, ui=project.ui, loop=loop, program=program,
        notes=notes, say=say, held=held)
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


def refusal(stage, source: str) -> str:
    """What the bindings answer when a script asks for something impossible.

    :meth:`say` insists a script line worked, so the error is collected here - whole,
    because the sentence is the point: a cell that is taken names the cell, the panel
    and what is already sitting in it.
    """
    value, error = stage.program.run(source)
    assert error is not None, f"{source} was accepted: {value}"
    return error.as_string()


# -- one page -----------------------------------------------------------

def test_the_example_declares_no_second_page(stage):
    """The redesign's first line: the sections are on ``root``, so there is one scene.

    Nothing in the manifest names a workspace and nothing in the script asks for
    one, which is the state the plan was written to leave available - a project that
    uses none of it gets exactly the window it asked for.
    """
    assert manifest.ui.workspaces == ()
    assert stage.ui.workspaces.names() == [ROOT_WORKSPACE]
    assert stage.ui.workspaces.current_name == ROOT_WORKSPACE


def test_the_three_sections_are_groups_in_the_deck_on_that_page(stage):
    """Each section is a real ``group``, a child of the grid it was built into."""
    deck = stage.held("deck")
    groups = sections(stage)

    assert [held.kind.name for held in groups] == ["group", "group", "group"]
    assert [held.name for held in groups] == [
        "voice", "modulator", "envelopes"]
    assert [held.parent for held in groups] == [deck, deck, deck]
    assert deck.parent is stage.ui.root
    assert [held.screen_box for held in groups] == [
        (16.0, 44.0, 393.0, 214.0),
        (427.0, 44.0, 393.0, 214.0),
        (16.0, 307.0, 393.0, 172.0),
    ]
    # a group measures its own height from what it holds; the deck does the cells
    assert [held.height for held in groups] == [214.0, 214.0, 172.0]


def test_the_deck_is_a_dockable_two_column_grid_with_a_cell_to_spare(stage):
    """Three sections in two columns: four cells, one of them left for a hand."""
    deck = stage.held("deck")

    assert [float(deck.get(word)) for word in
            ("grid_columns", "grid_gap", "dockable", "accordion")] == \
        [2.0, 18.0, 1.0, 0.0]
    assert [float(held.get("cell")) for held in sections(stage)] == [0.0, 1.0, 2.0]
    assert [float(held.get("dockable")) for held in sections(stage)] == \
        [1.0, 1.0, 1.0]
    spare = spec_of(deck).rect_of(3, deck.content_box())
    assert spare == pytest.approx((427.0, 307.0, 393.0, 245.0))


def test_no_section_is_a_box_of_one_knob(stage):
    """The brief's rule, written as an assertion: three controls per section.

    ``voice`` is the strip that picks the table plus the two knobs that shape what
    that table makes; ``modulator`` is the harmonic strip plus the index and the
    depth; ``envelopes`` is one ADSR per oscillator and the span they are measured
    over. A section of one control would be a labelled widget, and the fold, the
    reorder and the dock this page gives every section would be decoration.
    """
    assert [controls(held) for held in sections(stage)] == [
        ["radio", "knob", "knob"],
        ["radio", "knob", "knob"],
        ["adsr", "adsr", "knob"],
    ]
    # and the two knobs that answer one question share a box, rather than each
    # getting a panel - and then a section - to itself
    voice, modifier, envelopes = sections(stage)
    vknobs, mknobs, erow = (stage.held(word) for word in
                            ("vknobs", "mknobs", "erow"))
    assert {stage.held("level").parent, stage.held("cutoff").parent} == {vknobs}
    assert {stage.held("index").parent, stage.held("depth").parent} == {mknobs}
    assert {stage.held("cadsr").parent, stage.held("madsr").parent,
            stage.held("span").parent} == {erow}
    assert [vknobs.parent, mknobs.parent, erow.parent] == \
        [body_of(voice), body_of(modifier), body_of(envelopes)], \
        "a group's own children live in its body"


def test_the_side_column_holds_the_transport_it_was_built_for(stage):
    """``PLACE`` is read against the parent a ``NODE_ADD`` chose - so every control in
    the right-hand column has to be *in* it, or it lands on the window's own
    coordinates and on top of the sections beside it.

    This is the regression the redesign caught in its own probe: seven widgets were
    built and placed and never added to the panel, and the page still measured out
    perfectly - until a pointer went where the waveform should have been and found
    the Play toggle.
    """
    side = stage.held("side")
    transport = ("title", "pitch", "gain", "play", "dsp", "scope", "graph")

    assert [stage.held(word).parent for word in transport] == [side] * 7
    assert [held.screen_box for held in (stage.held(word) for word in transport)]\
        == [
        (836.0, 44.0, 268.0, 22.0),    # title
        (836.0, 74.0, 140.0, 26.0),    # pitch
        (836.0, 108.0, 268.0, 26.0),   # gain
        (836.0, 142.0, 130.0, 30.0),   # play
        (982.0, 142.0, 122.0, 30.0),   # dsp
        (836.0, 180.0, 268.0, 180.0),  # scope
        (836.0, 368.0, 268.0, 184.0),  # graph
    ]
    # the plot is the one widget that grows: its bottom edge is the panel's
    graph = stage.held("graph")
    assert graph.screen_box[1] + graph.screen_box[3] == pytest.approx(
        side.screen_box[1] + side.screen_box[3])


# -- the patch the script wrote -----------------------------------------

def test_the_patch_declares_the_receivers_the_controls_write_to(stage):
    """Seven ``[r name]`` boxes, two 260-sample tables, and a $0 that scopes them."""
    dollar_zero = stage.say("PD_DOLLARZERO(p)")

    assert sorted(stage.lib.receivers) == RECEIVERS
    assert sorted(stage.lib.arrays) == [f"{dollar_zero:.0f}-carwave",
                                        f"{dollar_zero:.0f}-modwave"]
    assert [len(stage.lib.arrays[name]) for name in sorted(stage.lib.arrays)] == \
        [TABLE, TABLE]
    # the modulator is a sine in every voice, and the carrier started on the
    # table the shipped bell asks for: both written from the manifest's own arrays
    assert stage.lib.arrays[f"{dollar_zero:.0f}-modwave"] == pytest.approx(
        stage.say('GET(RES("sin"), "values")'))
    assert stage.lib.arrays[f"{dollar_zero:.0f}-carwave"] == pytest.approx(
        stage.say('GET(RES("wave"), "values")'))


def test_the_run_opens_on_the_shipped_bell(stage):
    """The last line of the script is ``apply_preset("bell")``, so the panel a person
    sees first is a preset rather than a set of constructor defaults."""
    values = {word: stage.say(f'GET({word}, "value")') for word in
              ("pitch", "gain", "waves", "harm", "index", "level", "depth",
               "cutoff", "span")}

    assert values == {"pitch": 69.0, "gain": 0.2, "waves": 4.0, "harm": 1.0,
                      "index": 5.0, "level": 1.0, "depth": 0.8,
                      "cutoff": 8000.0, "span": 1800.0}
    assert [stage.say(f'GET(cadsr, "{row}")') for row in
            ("attack", "decay", "sustain", "release")] == \
        [0.03, 0.45, 0.0, 0.75]
    assert [stage.say(f'GET(madsr, "{row}")') for row in
            ("attack", "decay", "sustain", "release")] == \
        [0.02, 0.35, 0.0, 0.5]
    # and the stream is open, because the script ends with ``PD_DSP(1)``
    stage.pd.flush()
    assert ("message", "pd", "dsp", (1.0,)) in stage.lib.sent


def test_the_run_tells_the_patch_what_the_panel_holds(stage):
    """The quiet bug that made the shipped synth inaudible, pinned.

    ``ON_CHANGE`` is the only thing that normally carries a value to the patch, and a
    prop written to the number it already holds is not a change - so three of the
    bell's seventeen numbers reached the panel and never reached the patch:
    ``[r pitch]`` stayed empty and ``[mtof 0.]`` ran the carrier at 8.18 Hz, below
    human hearing, with the Gain knob turning up a rumble. ``apply_preset`` ends with
    ``sync()``, which sends from the panel rather than from whatever happened to move,
    and the receivers below are the ones no hand ever touched.
    """
    told = {entry[1]: entry[2] for entry in drain(stage) if entry[0] == "float"}

    assert told["pitch"] == 69.0, "the carrier's own frequency, from the panel"
    assert told["ratio"] == 2.0, "the bell is 2f, not the widget's default 1f"
    assert told["gain"] == pytest.approx(0.2)
    # the two that a real change carried anyway, so this file stays honest about
    # which numbers were actually missing before
    assert told["cutoff"] == 8000.0
    assert told["index"] == 5.0


# -- the voice and the harmonic -----------------------------------------

def test_a_press_on_the_voice_strip_rewrites_the_table_the_carrier_reads(stage):
    """Five voices are one write each: the carrier's oscillator never stops reading.

    The press goes through the router onto the strip, and the answer is read back out
    of the library the way the plot reads it - ``PD_ARRAY_READ`` on the table Pd owns,
    not the list the script handed over.
    """
    quiet(stage)
    waves = stage.held("waves")
    left, top, width, height = waves.content_box()

    # a five-wide strip is five cells across its own plate: the middle is the square
    assert press(stage, left + width * 0.5, top + height / 2.0) == 3
    assert waves.value == 2.0
    assert stage.lib.arrays["1001-carwave"] == pytest.approx(
        list(stage.say('GET(RES("sqr"), "values")')))
    # the strip is a choice, so a press on the cell already held says nothing twice
    assert press(stage, left + width * 0.5, top + height / 2.0) == 3
    assert waves.value == 2.0

    assert press(stage, left + width * 0.05, top + height / 2.0) == 3
    assert waves.value == 0.0
    assert stage.lib.arrays["1001-carwave"] == pytest.approx(
        list(stage.say('GET(RES("sin"), "values")')))
    assert len(stage.lib.arrays["1001-carwave"]) == TABLE


def test_the_plot_shows_the_table_read_back_out_of_pd(stage):
    """``PD_ARRAY_READ`` is what the frame feeds the graph, so the picture is Pd's."""
    stage.say('SET(waves, "value", 3)')
    quiet(stage)
    stage.loop.step()
    graph = stage.held("graph")

    assert list(graph.get("data")) == pytest.approx(
        list(stage.say('PD_ARRAY_READ(p, "carwave")')))
    assert len(graph.get("data")) == TABLE


def test_the_harmonic_strip_is_the_ratio_one_higher_than_its_index(stage):
    """``1f`` is index 0 and a ratio of 1: the modulator at the carrier's own pitch.

    Whole-number ratios are what make the sidebands land on harmonics rather than
    between them, which is the difference between an FM bell and FM noise - so the
    arithmetic the script does (``v + 1``) is the arithmetic a synthesist expects.
    """
    quiet(stage)
    harm = stage.held("harm")
    left, top, width, height = harm.content_box()

    assert press(stage, left + width * 0.9, top + height / 2.0) == 3
    assert harm.value == 4.0
    assert floats_to("ratio", drain(stage)) == [5.0]

    assert press(stage, left + width * 0.1, top + height / 2.0) == 3
    assert harm.value == 0.0
    assert floats_to("ratio", drain(stage)) == [1.0]


def test_the_index_and_depth_knobs_are_the_deviation_and_the_modulator_peak(stage):
    """Two numbers the help page names: deviation is index x modulator Hz, and a
    modulator whose envelope is closed leaves the voice bare."""
    quiet(stage)
    stage.say('SET(index, "value", 7.5)')
    assert floats_to("index", drain(stage)) == [7.5]

    depth = stage.held("depth")
    assert stage.say('GET(depth, "max")') == 1.0
    # 21.5 px down a knob whose plate is 86 px tall is a quarter of its range
    assert drag_vertical(stage, depth, 21.5) >= 1
    assert depth.value == pytest.approx(0.55)
    assert lists_to("menv", drain(stage))[-1] == (0.0, 900.0, 0.0)


# -- the envelopes ------------------------------------------------------

def test_an_adsr_press_writes_a_fraction_and_the_span_says_the_milliseconds(stage):
    """The panel holds fractions; ``Span ms`` is what gives them a unit.

    A press in the first quarter of the graph picks the attack stage and writes the
    height of the press - three quarters down the plate is a quarter up the range, so
    ``0.25``. The list that reaches ``[vline~]`` is the same number multiplied by the
    span: milliseconds, because that is the unit ``vline~`` reads, which is the one
    thing about this patch a draft got wrong by dividing by a thousand.
    """
    quiet(stage)
    cadsr = stage.held("cadsr")
    left, top, width, height = cadsr.content_box()

    assert press(stage, left + width * 0.1, top + height * 0.75) == 3
    assert cadsr.get("attack") == pytest.approx(0.25)
    assert float(cadsr.get("value")) == 0.0, "the stage the press picked"
    # stopped, so the change re-sent the release rather than an attack
    assert lists_to("cenv", drain(stage)) == [(0.0, 1350.0, 0.0)]

    # the release is the same fraction of the same span: 0.75 of 1800 ms
    stage.say('SET(span, "value", 1000)')
    assert lists_to("cenv", drain(stage)) == [(0.0, 750.0, 0.0)]
    stage.say('SET(span, "value", 1800)')
    drain(stage)


def test_playing_opens_both_envelopes_and_stopping_releases_them(stage):
    """One toggle, four lists: the carrier and the modulator each own a ``[vline~]``.

    The triples are ``(target, milliseconds, delay)`` and the script sends the whole
    attack-decay-sustain in one message - level to the attack time, then sustain to
    the decay time starting after the attack - so nothing in the patch has to be told
    when a ramp finishes. The release is a single triple to zero, from wherever the
    signal happens to be.
    """
    quiet(stage)
    play = stage.held("play")

    assert click(stage, play) == 3
    assert play.value == 1.0
    rows = drain(stage)
    # bell: attack 0.03 x 1800 = 54 ms, sustain 0.0, decay 0.45 x 1800 = 810 ms
    assert lists_to("cenv", rows) == [
        (1.0, 54.0, 0.0, 0.0, 810.0, 54.0)]
    # modulator: depth 0.8, attack 0.02 x 1800 = 36, decay 0.35 x 1800 = 630
    assert lists_to("menv", rows) == [
        (0.8, 36.0, 0.0, 0.0, 630.0, 36.0)]

    assert click(stage, play) == 3
    assert play.value == 0.0
    rows = drain(stage)
    assert lists_to("cenv", rows) == [(0.0, 1350.0, 0.0)]
    assert lists_to("menv", rows) == [(0.0, 900.0, 0.0)]


def test_the_level_knob_is_the_peak_the_carrier_envelope_opens_to(stage):
    """The dial a knob is: a drag measured against the knob's own height, and one
    number at the head of the list ``[vline~]`` runs.

    While the voice is stopped a change to Level cannot be heard, so the script
    re-sends the *release* - which starts at zero because a release runs from wherever
    the signal happens to be - and the peak only appears in the attack list the next
    press of Play sends. Both halves are asserted, because the difference between them
    is the whole reason the envelope is a list rather than a number.
    """
    level = stage.held("level")
    quiet(stage)

    assert level.value == 1.0
    _left, _top, _width, height = level.content_box()
    # 43 px of an 86 px plate is half the range, and the knob never jumps
    assert drag_vertical(stage, level, height / 2.0) >= 1
    assert level.value == pytest.approx(0.5)
    assert lists_to("cenv", drain(stage)) == [(0.0, 1350.0, 0.0)]

    assert click(stage, stage.held("play")) == 3
    assert lists_to("cenv", drain(stage))[-1] == pytest.approx(
        (0.5, 54.0, 0.0, 0.0, 810.0, 54.0))


def test_the_cutoff_knob_is_logarithmic_and_sends_hertz(stage):
    """20..20000 Hz on a dial is a log range, or a quarter turn is ten octaves."""
    cutoff = stage.held("cutoff")
    quiet(stage)

    assert stage.say('GET(cutoff, "log")') == 1.0
    assert (stage.say('GET(cutoff, "min")'), stage.say('GET(cutoff, "max")')) == \
        (20.0, 20000.0)
    stage.say('SET(cutoff, "value", 400)')
    assert floats_to("cutoff", drain(stage)) == [400.0]
    # the readout shows the digits a person reads off a filter, not 400.000000004
    assert stage.say('GET(cutoff, "decimals")') == 0.0


# -- the frame ----------------------------------------------------------

def test_a_frame_writes_the_status_line_and_the_numbers_it_is_playing(stage):
    """The status band is the frame's, so a stopped voice and a playing one say so.

    ``STR`` of what the panel holds is the whole content: the line is the patch's
    state as the script sees it, which is why a wrong ``ratio`` arithmetic would show
    up here before it showed up in the sound.
    """
    quiet(stage)
    stage.loop.step()
    assert lines_of(stage) == ["stopped - press Play/Stop"]

    click(stage, stage.held("play"))
    stage.loop.step()
    assert lines_of(stage) == [
        "playing wave @ pitch 69.0, 2.0f, index 5.0, cutoff 8000.0, gain 0.2"]

    # the DSP toggle is the frame's other opinion: the same numbers, prefixed
    stage.say('SET(dsp, "value", 0)')
    assert ("message", "pd", "dsp", (0.0,)) in drain(stage)
    stage.loop.step()
    assert lines_of(stage)[0].startswith("dsp off - playing wave")
    assert stage.ui.handlers.errors == []


def test_the_scope_reads_the_stream_and_the_plot_reads_the_table(stage):
    """Both canvases are fed by the frame, from the two places a picture of a sound
    can come from: the output the audio runtime holds, and the array Pd holds.

    The null backend answers no blocks, so on this stage the scope is empty and the
    plot is not - which is the honest parity between a machine with a sound card and
    a machine without one.
    """
    stage.loop.step()
    scope, graph = stage.held("scope"), stage.held("graph")

    assert [held.kind.name for held in (scope, graph)] == \
        ["waveform", "plot"]
    assert list(graph.get("data")) == pytest.approx(
        list(stage.say('PD_ARRAY_READ(p, "carwave")')))
    assert len(scope.get("data") or ()) == 0, "the null stream fills nothing"


# -- fold, reorder, dock ------------------------------------------------

def test_a_chevron_click_folds_a_section_to_its_band(stage):
    """The mark at the end of the header, pressed by the router: the body goes away
    and the section keeps exactly the band it is dragged by."""
    voice = stage.held("voice")
    mark = chevron_box(voice)
    before = [held.screen_box for held in sections(stage)[1:]]

    assert press(stage, mark[0] + mark[2] / 2.0, mark[1] + mark[3] / 2.0) == 3
    settle(stage)

    assert folded_of(voice) is True
    assert voice.height == pytest.approx(header_box(voice)[3] + 18.0)
    assert voice.height == pytest.approx(42.0)
    assert body_of(voice).visible is False
    assert "Level" not in painted(stage).texts(), "the knobs went with the body"
    # the other two were not asked about
    assert [held.screen_box for held in sections(stage)[1:]] == before

    assert press(stage, mark[0] + mark[2] / 2.0, mark[1] + mark[3] / 2.0) == 3
    settle(stage)
    assert folded_of(voice) is False
    assert "Level" in painted(stage).texts()


def test_the_reorder_rows_change_the_order_the_grid_lays_the_cells_out_in(stage):
    """``GROUP_MOVE`` re-arranges the list the grid enumerates, so the cell a box is
    drawn in follows the order rather than the other way round."""
    deck = stage.held("deck")

    pick(stage, "view_menu", "Envelopes first")
    settle(stage)
    assert [held.name for held in deck.children] == [
        "envelopes", "voice", "modulator"]
    voice, modifier, envelopes = sections(stage)
    assert [float(held.get("cell")) for held in
            (voice, modifier, envelopes)] == [1.0, 2.0, 0.0]
    assert [float(held.get("order")) for held in
            (voice, modifier, envelopes)] == [1.0, 2.0, 0.0]
    assert envelopes.screen_box == pytest.approx((16.0, 44.0, 393.0, 172.0))

    pick(stage, "view_menu", "Voice first")
    settle(stage)
    assert [held.name for held in deck.children] == [
        "voice", "envelopes", "modulator"]
    assert [float(held.get("cell")) for held in
            (voice, modifier, envelopes)] == [0.0, 2.0, 1.0]


def test_the_accordion_row_is_the_prop_on_the_panel_holding_the_sections(stage):
    """`One at a time` is the holder's rule and the row that writes it is a toggle:
    the checkable row latches its value before the script's function reads it back."""
    deck = stage.held("deck")
    voice, modifier, envelopes = sections(stage)

    assert float(deck.get("accordion")) == 0.0
    row = pick(stage, "view_menu", "One at a time")
    assert row.checked and float(deck.get("accordion")) == 1.0

    pick(stage, "view_menu", "Fold every section")
    settle(stage)
    assert [float(held.get("folded")) for held in
            (voice, modifier, envelopes)] == [1.0, 1.0, 1.0]
    assert [held.height for held in (voice, modifier, envelopes)] == \
        [42.0, 42.0, 42.0]

    stage.say("GROUP_FOLD(modifier, 0)")
    settle(stage)
    # opening one closed the other two: the holder's answer about the set, fired on
    # the transition rather than on every frame
    assert [float(held.get("folded")) for held in
            (voice, modifier, envelopes)] == [1.0, 0.0, 1.0]
    assert [held.height for held in (voice, modifier, envelopes)] == \
        [42.0, 214.0, 42.0]

    again = pick(stage, "view_menu", "One at a time")
    assert not again.checked and again is row
    assert float(deck.get("accordion")) == 0.0


def test_a_band_dragged_over_the_grid_lands_on_the_free_cell(stage):
    """The whole gesture on the shipped page: capture, four outlined cells, drop.

    The outlines are the only thing a hand is given while a band is held - a group's
    box belongs to the layout pass, so a ghost following the pointer would be one
    widget drawn in two places - and the release moves it into exactly the cell the
    outline was under. Two of the six polylines in that frame are the two envelope
    graphs; the other four are the cells.
    """
    deck = stage.held("deck")
    envelopes = stage.held("envelopes")

    pickup(stage, envelopes)
    assert stage.ui.router.capture is envelopes
    assert float(deck.get("docking")) == 1.0, "a drag is held"
    assert painted(stage).count("polyline") == 6, "four cells, two envelope graphs"

    spare = spec_of(deck).rect_of(3, deck.content_box())
    carry(stage, spare)

    assert float(envelopes.get("cell")) == 3.0
    # the cell's width, the group's own height, aligned to the cell's top edge
    assert envelopes.screen_box == pytest.approx((427.0, 307.0, 393.0, 172.0))
    assert float(deck.get("docking")) == 0.0, "the hand let go"
    assert painted(stage).count("polyline") == 2
    assert [float(held.get("cell")) for held in sections(stage)] == \
        [0.0, 1.0, 3.0]

    assert float(stage.say("GROUP_UNDOCK(envelopes)")) == -1.0
    settle(stage)
    # giving the claim up takes the first cell the pass finds free, which is the one
    # the drop left behind rather than the one it came from
    assert [float(held.get("cell")) for held in sections(stage)] == [0.0, 1.0, 2.0]


def test_a_cell_that_is_taken_is_refused_rather_than_stacked(stage):
    """The same fact two ways: a hand gets the box not moving, a script gets a
    sentence that names the cell, the panel and what is already in it."""
    voice, modifier, envelopes = sections(stage)
    before = [held.screen_box for held in (voice, modifier, envelopes)]

    pickup(stage, voice)
    carry(stage, spec_of(stage.held("deck")).rect_of(
        int(modifier.get("cell")), stage.held("deck").content_box()))

    assert [held.screen_box for held in sections(stage)] == before, "nothing moved"
    assert float(voice.get("cell")) == 0.0

    said = refusal(stage, "GROUP_DOCK(envelopes, 1)")
    assert "Runtime Error: GROUP_DOCK: " in said
    assert "cell 1 of " in said and "already holds" in said
    assert "it is a cell, not a stack" in said


# -- the help menu and its page ----------------------------------------

def test_the_help_menu_took_the_place_of_the_sections_menu(stage):
    """Six menus, and the one that used to change the page now explains the instrument.

    ``View`` is left with the rows the page is actually made of - fold, open, the
    three orders, the accordion - because a menu that recomposes itself from the
    scenes there are no longer has to be the sections' neighbour.
    """
    bar = bar_of(stage.say("bar"))

    assert [menu.title for menu in bar.menus] == [
        "File", "View", "Audio", "MIDI", "Serial", "Help"]
    assert rows(menu_of(stage.say("help_menu"))) == [
        "Using this synth", "Close help"]
    assert rows(menu_of(stage.say("view_menu"))) == [
        "Fold every section", "Open every section", "Voice first",
        "Modulator first", "Envelopes first", "One at a time"]
    assert rows(menu_of(stage.say("file_menu"))) == [
        "Save preset", "Load bell", "Load brass"]


def test_the_help_page_is_a_dialog_of_ordinary_widgets_built_before_the_bar(stage):
    """No new widget kind and no second scene: a panel, a title, a log view, a button.

    The page is built before ``MENUBAR`` because there is no ``RAISE`` verb - the
    order widgets are added in is the order they are drawn in, so the bar and its
    popups have to come later than the page they are meant to sit above.
    """
    help_panel = stage.held("help")
    children = [stage.held(word) for word in
                ("help_title", "help_page", "help_close")]

    assert help_panel.kind.name == "panel"
    assert [held.kind.name for held in children] == \
        ["text", "logview", "button"]
    assert [held.parent for held in children] == [help_panel] * 3
    assert [held.screen_box for held in children] == [
        (161.0, 65.0, 798.0, 22.0),
        (161.0, 95.0, 798.0, 392.0),
        (161.0, 497.0, 110.0, 26.0),
    ]
    assert float(stage.say('GET(help, "visible")')) == 0.0
    words = stage.say("words")
    assert len(words) == 44
    assert list(stage.held("help_page").get("lines")) == words

    order = [child for child in stage.ui.root.children]
    assert order.index(help_panel) < order.index(bar_of(stage.say("bar")).panel), \
        "the page is under the bar"


def test_a_closed_dialog_is_not_in_the_pointer_s_way(stage):
    """``visible`` off takes the whole subtree out of the hit test.

    The page is glued over the deck, so with the dialog shut the point its Close
    button occupies belongs to the panel behind it - the deck - and a point further
    under where the page was belongs to the Depth knob. Open again and the same two
    points are the dialog's: the page is one prop wide out of the pointer's way.
    """
    mark = stage.held("help_close").screen_box
    there = (mark[0] + 5.0, mark[1] + 5.0)
    dial = stage.held("depth")
    box = dial.content_box()
    over = (box[0] + box[2] / 2.0, box[1] + box[3] / 2.0)

    assert stage.ui.hit(*there) is stage.held("deck")
    assert stage.ui.hit(*over) is dial
    assert press(stage, there[0], there[1]) == 3
    assert [held.get("cell") for held in sections(stage)] == [0.0, 1.0, 2.0], \
        "a click on a plain panel moved nothing"
    assert stage.ui.handlers.errors == []

    assert stage.say("open_help()") == 1.0
    settle(stage)
    assert stage.ui.hit(*there) is stage.held("help_close")
    assert stage.ui.hit(*over) is stage.held("help_page"), "the page is in front"


def test_opening_the_help_puts_the_page_at_its_first_line(stage):
    """A log view counts ``scroll`` up from its newest row, so a page opened to be
    read from the top is a scroll of as many lines as there are.

    The face clamps what it draws to the rows that fit, which is the other half of
    the number: twenty-four of forty-four lines are on the screen at once, the first
    of them this file's own heading.
    """
    words = stage.say("words")

    pick(stage, "help_menu", "Using this synth")
    settle(stage)
    page = stage.held("help_page")

    assert float(stage.say('GET(help, "visible")')) == 1.0
    assert page.get("scroll") == pytest.approx(len(words))
    seen = shown_words(stage, words)
    assert len(seen) == 24, "the pane's own height in lines"
    assert seen[0] == words[0]
    assert words[-1] not in seen


def test_the_wheel_walks_the_page_to_its_last_line(stage):
    """The wheel the dialog was built for: a notch pushed away moves the window up
    the log, and the clamp is the same one the face draws with.

    Sixty notches is more than there are lines to move, and the number stops at zero
    - the newest row at the bottom - which is also where the last line of the text
    finally appears and the first has gone.
    """
    words = stage.say("words")
    stage.say("open_help()")
    settle(stage)
    page = stage.held("help_page")
    left, top, _width, _height = page.screen_box
    stage.ui.on_cursor(left + 30.0, top + 30.0)
    stage.ui.dispatch()

    for _ in range(60):
        stage.ui.on_scroll(0.0, -SCROLL_NOTCH)
        stage.ui.dispatch()

    assert page.get("scroll") == pytest.approx(0.0)
    seen = shown_words(stage, words)
    assert seen[-1] == words[-1]
    assert words[0] not in seen
    assert stage.ui.handlers.errors == []


def test_the_close_button_and_the_menu_row_are_the_same_door(stage):
    """One prop, written by two hands: the button through the router, the row through
    the popup's own item."""
    stage.say("open_help()")
    settle(stage)
    mark = stage.held("help_close").screen_box

    assert press(stage, mark[0] + mark[2] / 2.0, mark[1] + mark[3] / 2.0) == 3
    assert float(stage.say('GET(help, "visible")')) == 0.0
    words = stage.say("words")
    assert shown_words(stage, words) == []
    # the instrument is itself again: the voice section's caption is on the screen
    assert "waveform" in painted(stage).texts()

    pick(stage, "help_menu", "Using this synth")
    settle(stage)
    assert float(stage.say('GET(help, "visible")')) == 1.0
    pick(stage, "help_menu", "Close help")
    assert float(stage.say('GET(help, "visible")')) == 0.0


# -- presets ------------------------------------------------------------

def test_a_preset_is_seventeen_numbers_in_the_order_the_file_holds(stage):
    """``Save preset`` and ``Load`` are the same door, so the shipped files are the
    format: pitch, gain, voice, harmonic, index, level, depth, cutoff, span, then a
    carrier and a modulator envelope of four fractions each.

    Nothing here calls ``PRESET_SAVE`` - it writes into the project directory, and a
    test has no business leaving a file in the source tree - so the round trip is
    read back out of the panel after a load.
    """
    for word in ("bell", "brass"):
        on_disk = json.loads((EXAMPLE / "presets" / f"{word}.json"
                              ).read_text(encoding="utf-8"))
        assert len(on_disk["data"]) == 17, word

        assert stage.say(f'apply_preset("{word}")') == word
        assert stage.say("preset_data()") == pytest.approx(on_disk["data"])

    # and the panel agrees with the file it just read, prop by prop - the loop left
    # the brass preset on the knobs, so these are brass's own numbers
    assert stage.say('GET(pitch, "value")') == 60.0
    assert stage.say('GET(waves, "value")') == 3.0
    assert stage.say('GET(cadsr, "release")') == pytest.approx(0.3)


def test_loading_a_preset_moves_the_controls_and_sends_what_they_hold(stage):
    """A load is seventeen writes, and every write is the gesture a hand would have
    made: the receivers land at the new numbers and the carrier's table is rewritten."""
    quiet(stage)

    stage.say('apply_preset("brass")')
    rows_written = drain(stage)

    assert floats_to("pitch", rows_written) == [60.0]
    assert floats_to("gain", rows_written) == [0.35]
    assert floats_to("ratio", rows_written) == [1.0], "saw, 1f"
    assert floats_to("index", rows_written) == [2.0]
    assert floats_to("cutoff", rows_written) == [3000.0]
    assert stage.lib.arrays["1001-carwave"] == pytest.approx(
        list(stage.say('GET(RES("saw"), "values")')))
    # both envelopes re-sent their release, because the voice is stopped: 0.3 and
    # 0.2 of a 700 ms span
    assert lists_to("cenv", rows_written)[-1] == (0.0, 210.0, 0.0)
    assert lists_to("menv", rows_written)[-1] == (0.0, 140.0, 0.0)


# -- the headless half --------------------------------------------------

def test_a_headless_run_builds_the_same_page_and_draws_nothing(stage, headless):
    """``--mode headless`` is one code path with the paint switched off, not a second
    instrument: the same tree, the same sends, no ops.

    The gesture that proves it is a real one - a click on Play - because a draft that
    only compared widget counts would pass with the handler bridge wired one way in a
    window and the other way without one.
    """
    for every in (stage, headless):
        quiet(every)
        click(every, every.held("play"))
        every.loop.step()

    assert headless.lib.sent == stage.lib.sent, "the same numbers either way"
    assert lines_of(headless) == lines_of(stage)
    assert [float(held.get("cell")) for held in sections(headless)] == \
        [float(held.get("cell")) for held in sections(stage)]
    assert headless.ui.root.count() == stage.ui.root.count() == 126
    assert headless.ui.workspaces.names() == [ROOT_WORKSPACE]
    assert headless.renderer.ops == []
    assert headless.ui.handlers.errors == [] == stage.ui.handlers.errors
