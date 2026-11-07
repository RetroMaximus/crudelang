"""``examples/all_gui``: the catalog on screen, and a word that never leaves its room.

This is the example the workspace and group halves of the stack never had - nothing
else shipped a ``WORKSPACE*`` call at all - and it is written as a *coverage page*
rather than as an application. Two rules hold the whole file together and both are
asserted here rather than discussed:

* **every kind in the catalog is built on some page.** The test walks the script's
  own ``PAGES`` and not ``ui.root``, because a hidden scene is detached from the
  root and a walk of the tree would quietly under-count: the point of the pin is
  that the day a twenty-fifth kind lands in :mod:`crude.ui.props`, this example
  fails until somebody shows it. The script's own ``badge`` is counted separately,
  because the catalog is the shipped list and a kind a file wrote is not in it.
* **no word is ever clipped.** Every string the tree paints is measured with the
  renderer's own ``measure`` against the clip the widget was given, that clip
  against the parent's content box and the window's rect, and a word placed
  ``"inside"`` against the plate that pays for it. A row the fold has taken off a
  viewport is skipped, because a thing nobody can see is not a thing that is cut.

The page set is one scene per family - the deck (groups, dockable grid, the group
verbs), controls (every value kind), layout (the geometry verbs as what they do),
canvases (the five picture widgets and the log), custom (a kind this file wrote) and
help - plus the menu bar that switches between them.

Two things are deliberately not done here. Nothing is wired to a voice: there is no
``PATCH``, no serial link and no MIDI port, so every control's answer is on the page
it was moved on - a readout, a link, a log line, a redrawn canvas. Pd wiring stays
demonstrated by ``metronome`` and ``fm_synth``. And nothing writes into the source
tree: every stage runs on a copy of the example inside ``tmp_path``, so a preset bar,
a dropped ``.wav`` or the log directory all hang off the copy, and one assertion
below checks the shipped directory is still clean.

The gestures are driven through the router - the same ``on_cursor`` / ``on_button`` /
``on_scroll`` / ``on_key`` a window hands to the tree - and the menu rows are
triggered through the popup's own item, rather than calling the script's function
wherever a hand has a door. Where a refusal is the thing being shown (``SET(w,
"place")`` and dropping a text file on a clip) the engine error is asserted as the
answer and cleared, so no test can leave one for the next.
"""

from __future__ import annotations

import logging
import shutil
import types
from pathlib import Path
from typing import Any, List, Sequence, Tuple

import pytest

from crude.audio import AudioConfig, NullBackend, RecordingRender
from crude.audio.runtime import AudioRuntime
from crude.audio.wav import write_wav
from crude.errors import ExitRequest
from crude.lang import Program
from crude.lang.values import HostHandle, List as CrudeList, Number, String
from crude.pd import FakeLibpd, PdRuntime, PdSession
from crude.project import LoadOptions, Project, ResourceTable, load_manifest
from crude.runtime import FakeClock, Runtime
from crude.ui import RecordingRenderer, UIContext, Widget, reset
from crude.ui.clip import pixel_of, window_of
from crude.ui.grid import cells
from crude.ui.group import HEADER_HEIGHT, chevron_box, header_box
from crude.ui.input import KEY_NAMES, SCROLL_NOTCH
from crude.ui.layout import layout_of
from crude.ui.menu import Menu
from crude.ui.props import catalog_names

#: the repository root, whatever directory pytest was started in
ROOT = Path(__file__).resolve().parents[2]

#: the example as shipped: manifest and entry script, read from the same files a
#  ``crude run examples/all_gui`` reads
EXAMPLE = ROOT / "examples" / "all_gui"
SOURCE = (EXAMPLE / "main.crude").read_text(encoding="utf-8")

manifest = load_manifest(str(EXAMPLE / "crude.manifest.json"))
WIDTH = int(manifest.runtime.width)
HEIGHT = int(manifest.runtime.height)

#: a window small enough that a dialog sized from it has to fit or be refused
TINY = (480, 360)

RATE = int(manifest.audio.sample_rate)
BLOCK = int(manifest.audio.block_size)

#: the scenes the manifest reserves plus the root the deck is, in the order the
#  window lists them; the entry script's own ``WORKSPACE`` calls make the five
SCENES = ("root", "controls", "layout", "canvases", "custom", "help")

#: the row a settings dialog shows when the run has no runtime to ask
NO_DEVICES = "(no devices)"

#: one wheel notch, in the pixel units the router carries
NOTCH = SCROLL_NOTCH


# -- small readings -----------------------------------------------------

def as_py(value: Any) -> Any:
    """A language value as the plain Python object it stands for."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, CrudeList):
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


def subtree(held: Widget) -> List[Widget]:
    """This widget and everything under it, in draw order."""
    out = [held]
    for kid in held.children:
        out.extend(subtree(kid))
    return out


def shown_subtree(held: Widget) -> List[Widget]:
    """``subtree`` with the parts a person cannot see left out.

    The menu bar's popups and both settings dialogs are children of the window's
    root and every one of them starts hidden, so a walk that measured them would be
    holding a word to a plate that is not on screen - and to the box the popup was
    given the last time it was shown, in a window that may since have changed size.
    """
    if not held.get("visible"):
        return []
    out = [held]
    for kid in held.children:
        out.extend(shown_subtree(kid))
    return out


def fold_of(held: Widget):
    """The clip of the nearest viewport above this widget, or ``None``.

    A scroll cuts its content at its edge and that is the whole of what a viewport
    is, so a word the offset has moved off the fold is *not shown* rather than
    broken.
    """
    parent = held.parent
    while parent is not None:
        if "scroll" in parent.kind.chain():
            return parent.clip
        parent = parent.parent
    return None


def inside(box: Sequence[float], whole: Sequence[float],
           slack: float = 0.51) -> bool:
    """Whether one rect sits inside another, to the half pixel the pass rounds at."""
    return (whole[0] - slack <= box[0] and whole[1] - slack <= box[1]
            and box[0] + box[2] <= whole[0] + whole[2] + slack
            and box[1] + box[3] <= whole[1] + whole[3] + slack)


# -- the frame ----------------------------------------------------------

def painted(stage, held: Widget) -> RecordingRenderer:
    """One frame of the shipped tree - or of one widget of it - recorded."""
    frame = RecordingRenderer()
    ui = stage.ui
    frame.begin(ui.width, ui.height, ui.width, ui.height)
    held.paint(frame, ui.skin)
    return frame


def _key(op) -> tuple:
    return (op.get("text"), round(op.get("x") or 0.0, 2),
            round(op.get("y") or 0.0, 2), op.get("size"))


def own_words(stage, held: Widget) -> List[Tuple[str, Tuple[float, ...]]]:
    """Only the strings this widget paints for itself, not the ones under it.

    ``paint`` is recursive, so a panel's frame holds every word below it as well as
    its own; a word is held to the room *its* widget was given, which means the
    children's ops have to be taken back out first. The box is the measured one -
    :meth:`Renderer.text` resolves its alignment against :meth:`measure` and stores
    the shifted ``x``, so the left edge in the op and the width ``measure`` answers
    are the only pair of numbers that can say "is it on screen".
    """
    skip = set()
    for kid in held.children:
        for op in painted(stage, kid).ops_named("text"):
            skip.add(_key(op))
    frame = painted(stage, held)
    out = []
    for op in frame.ops_named("text"):
        if _key(op) in skip:
            continue
        width, height = frame.measure(op.get("text"), op.get("size"))
        out.append((op.get("text"), (op.get("x"), op.get("y"), width, height)))
    return out


def stacked_children(held: Widget) -> List[Widget]:
    """The children of a widget the arrangement pass is in charge of, in order.

    Only a widget with a stack of its own has an answer here: a panel whose children
    were each ``PLACE``d by hand may overlap them on purpose, so a row that overlaps
    there is the script's doing rather than the pass's.
    """
    if layout_of(held).stack is None:
        return []
    return [kid for kid in held.children if kid.get("visible")]


def overlaps(held: Widget) -> List[str]:
    """Two children of one stacked row sharing pixels, which no stack may do."""
    out = []
    kids = stacked_children(held)
    for first, second in zip(kids, kids[1:]):
        a, b = first.screen_box, second.screen_box
        wide = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
        high = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
        if wide > 0.5 and high > 0.5:
            out.append(f"{first.path()} and {second.path()} overlap by "
                       f"{round(wide, 1)}x{round(high, 1)}")
    return out


def geometry_problems(stage, start: Widget = None, *, boxes: bool = True
                      ) -> List[str]:
    """Every way a shown widget in this window can be drawn wrong, as sentences.

    The two pins' measurement, written once so every page is held to it: a clip that
    leaves its parent, a word that leaves its clip, an inside-word that leaves the
    plate, a hidden label that is painted anyway, and two rows of one stack sharing
    pixels. Off-fold rows are left out, because a viewport overflowing is what a
    viewport is.

    ``boxes`` off keeps only the word rules and is what a menu popup is measured
    with: a popup is placed over whatever is under it on purpose, so holding its
    rows to a parent's content box would be holding a menu to not being a menu.
    """
    ui = stage.ui
    window = (0.0, 0.0, ui.width, ui.height)
    bad: List[str] = []
    for held in shown_subtree(start if start is not None else ui.root):
        box = held.screen_box
        clip = held.clip
        parent = held.parent
        fold = fold_of(held)
        if fold is not None and not inside(box, fold):
            continue
        if not boxes:
            bad.extend(overlaps(held))
        else:
            for line in overlaps(held):
                bad.append(f"{held.path()}: {line}")
            if not inside(clip, window):
                bad.append(f"{held.path()} clip {tuple(round(v, 1) for v in clip)} "
                           f"leaves the window {window}")
            if (parent is not None and parent.kind.name != "scroll"
                    and not inside(clip, parent.content_box())):
                bad.append(f"{held.path()} clip {tuple(round(v, 1) for v in clip)} "
                           f"leaves parent content "
                           f"{tuple(round(v, 1) for v in parent.content_box())}")
        places = (str(held.get("label_place")), str(held.get("value_place")))
        for text, rect in own_words(stage, held):
            if not inside(rect, clip):
                bad.append(f"{held.path()} word {text!r} at "
                           f"{tuple(round(v, 1) for v in rect)} outside clip "
                           f"{tuple(round(v, 1) for v in clip)}")
            if all(place in ("inside", "none", "None")
                   for place in places) and not inside(rect, box):
                bad.append(f"{held.path()} inside-word {text!r} at "
                           f"{tuple(round(v, 1) for v in rect)} leaves the plate "
                           f"{tuple(round(v, 1) for v in box)}")
        if held.get("label_place") == "none" and str(held.get("label")):
            said = [text for text, _ in own_words(stage, held)]
            if any(str(held.get("label")) in text for text in said):
                bad.append(f"{held.path()} hides a label and still paints {said}")
    return bad


# -- the pointer --------------------------------------------------------

def settle(ui, frames: int = 8) -> None:
    """Frames the way the runtime runs one: ON_FRAME, dispatch, layout, draw.

    ``ui.layout()`` alone settles the boxes but never runs the page's own
    ``ON_FRAME`` readouts, and a test that printed those would be printing the
    numbers the script asked for rather than the ones the pass answered with.
    """
    moved = [ui.frame(dt=1 / 60).moved for _ in range(frames)]
    assert moved[-1] == 0, f"the page never settled its layout: {moved}"


def press(stage, x: float, y: float, mods: int = 0) -> int:
    """One whole left click at a point of the window; how many events it took."""
    stage.ui.on_cursor(x, y)
    stage.ui.on_button(0, 1, mods)
    stage.ui.on_button(0, 0, mods)
    return stage.ui.dispatch()


def click_on(stage, held: Widget, *, dx: float = 0.5, dy: float = 0.5) -> int:
    """One whole click at a fraction of one widget's own plate."""
    left, top, width, height = held.content_box()
    return press(stage, left + width * dx, top + height * dy)


def drag(stage, x: float, y: float, moved: float, down: float = 0.0) -> None:
    """Press at ``x``, carry the pointer, let go - three dispatches, one gesture."""
    stage.ui.on_cursor(x, y)
    stage.ui.on_button(0, 1, 0)
    stage.ui.dispatch()
    stage.ui.on_cursor(x + moved, y + down)
    stage.ui.dispatch()
    stage.ui.on_button(0, 0, 0)
    stage.ui.dispatch()


def drag_range(stage, held: Widget, *, dx: float = 0.0, dy: float = 0.0) -> None:
    """A drag from the middle of one range's plate, in pixels."""
    drag(stage, *_mid(held), dx, dy)


def _mid(held: Widget) -> Tuple[float, float]:
    left, top, width, height = held.content_box()
    return (left + width / 2.0, top + height / 2.0)


def _band(held: Widget) -> Tuple[float, float]:
    """The middle of a rect the toolkit measured for one widget's own band."""
    left, top, width, height = held
    return (left + width / 2.0, top + height / 2.0)


def move_to(stage, x: float, y: float) -> None:
    """The pointer, resting over a point - the only event that carries a position."""
    stage.ui.on_cursor(x, y)
    stage.ui.dispatch()


def wheel(stage, held: Widget, notches: float,
          dx: float = 0.5, dy: float = 0.5) -> None:
    """One wheel over one widget, at the pixel the pointer was left at."""
    left, top, width, height = held.content_box()
    move_to(stage, left + width * dx, top + height * dy)
    stage.ui.on_scroll(0, notches * NOTCH)
    stage.ui.dispatch()


def key(stage, name: str, action: str = "press") -> None:
    """One key, by the word the toolkit names it with."""
    code = [k for k, v in KEY_NAMES.items() if v == name][0]
    actions = {"release": 0, "press": 1, "repeat": 2}
    stage.ui.on_key(code, 0, actions[action], 0)
    stage.ui.dispatch()


def pick(stage, word: str, label: str):
    """Trigger one row of a shipped menu, through the popup's own item."""
    held = menu_of(stage.say(word))
    held.show()
    row = [item for item in held.items if item.label == label]
    assert row, f"{held.title} has no row {label!r}: " \
                f"{[item.label for item in held.items]}"
    row[0].trigger()
    held.hide()
    return row[0]


def rows_of(stage, word: str) -> List[str]:
    """The labels one shipped menu is holding when it is opened.

    Read from the open popup rather than the closed one, because the workspace
    menu re-reads the window's own list as it opens - which is the whole point of
    the row that adds a scene made after the menu was built.
    """
    held = menu_of(stage.say(word))
    held.show()
    labels = [item.label for item in held.items]
    held.hide()
    return labels


def visible_dialogs(ui) -> List[Widget]:
    """The settings panels the window is showing, however they were opened.

    A dialog's panel is named after the row that owns it (``Settings#audio``), so
    the one thing every one of them has and nothing else in the tree has is a scroll
    body called ``#body`` under a caption row called ``#title``.
    """
    return [kid for kid in ui.root.children
            if kid.get("visible") and kid.kind.name == "panel"
            and any(inner.kind.name == "scroll" and inner.name.endswith("#body")
                    for inner in kid.children)]


def shown_words(stage, held: Widget) -> List[str]:
    """Every word this widget and everything visible under it paints."""
    out = []
    for kid in shown_subtree(held):
        out.extend(text for text, _ in own_words(stage, kid))
    return out


def show(stage, scene: str) -> None:
    """Put one scene on the window and let the frames answer for its boxes."""
    assert stage.say(f'WORKSPACE_SHOW("{scene}")') is not None
    settle(stage.ui)


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


def _stage(tmp_path, name: str, *, painting: bool = True,
           size: Tuple[float, float] = (WIDTH, HEIGHT)):
    """The shipped example, run once, inside a *copy* of itself.

    The copy is not tidiness: this example reads its array and preset resources
    relative to its own project directory, ``PRESET_BAR`` writes its log directory
    there, and a ``.wav`` dropped on the clip is read from there. The build cache and
    the log directory are ignored on the way in, so the copy is a fresh project rather
    than last week's half-run.

    ``painting`` off is the headless half of the parity test: the same tree, the same
    handlers, no ops at all. ``size`` is the window the context is built at, which is
    how one test can prove a dialog sized from its window fits a small one.
    """
    home = tmp_path / name
    shutil.copytree(EXAMPLE, str(home),
                    ignore=shutil.ignore_patterns(".crude_build", "logs"))

    # always the *copy's* manifest and the copy's script: a resource path, a build
    # cache, a log directory and a written wav all hang off the project directory
    where = home / "crude.manifest.json"
    held_manifest = load_manifest(str(where))
    source = (home / "main.crude").read_text(encoding="utf-8")

    project = Project(held_manifest,
                      options=LoadOptions(progress="none", mode="headless"),
                      resources=ResourceTable(held_manifest),
                      clock=FakeClock(
                          target_fps=int(held_manifest.runtime.target_fps)))

    lib = FakeLibpd()
    session = PdSession(lib)
    session.start()
    notes: List[str] = []
    project.pd = PdRuntime(session, project_dir=str(home), log=notes.append)

    config = AudioConfig(sample_rate=RATE, block_size=BLOCK,
                         in_channels=int(held_manifest.audio.in_channels),
                         out_channels=int(held_manifest.audio.out_channels))
    # opened, not merely built: a scope reads the stream through the audio runtime,
    # and an input that was never started would answer silence for the wrong reason
    stream = NullBackend(config, render=RecordingRender(value=0.1))
    stream.open()
    project.audio = AudioRuntime(config=config, backend=stream)

    renderer = RecordingRenderer()
    ui = UIContext(renderer, width=float(size[0]), height=float(size[1]),
                   painting=painting)
    project.ui = ui

    loop = Runtime(project, mode="headless", clock=project.clock, ui=ui)
    loop.activate(project)

    program = Program(held_manifest.entry, host=project.host_object())
    project.attach_program(program)

    def say(text: str) -> Any:
        """Type ``text`` at the running script and get the Python back."""
        value, error = program.run(text)
        assert error is None, f"{text}\n  {error.as_string()}"
        return as_py(value)

    def held(word: str) -> Widget:
        return widget(say(word))

    # this is a fresh run of the project, and a run boundary belongs to whoever
    # starts the run: ``Project.run_script`` forgets the kinds the last program's
    # ``WIDGET_DEFINE`` left in the process-wide table before it executes, and a
    # fixture that builds a ``Program`` by hand owes the same - otherwise the
    # second stage of this file collides with the first example's own ``badge``
    reset()
    say(source)
    project.pd.flush()
    settle(ui)
    # nothing in the load may fail quietly: a handler that raised is a bug in the
    # example, not a detail of the frame it happened to run in
    assert ui.handlers.errors == [], ui.handlers.errors

    namespace = types.SimpleNamespace(
        project=project, manifest=held_manifest, lib=lib, session=session,
        pd=project.pd, renderer=renderer, ui=ui, loop=loop, program=program,
        audio=project.audio, home=home, notes=notes, say=say, held=held)
    yield namespace

    project.audio.close()
    session.shutdown()
    project.close_logging()
    # and the same courtesy on the way out: the badge this run defined must not
    # be the kind some later file in the session finds already taken
    reset()


@pytest.fixture
def stage(tmp_path):
    """The example in a window that is only a recording renderer."""
    yield from _stage(tmp_path, "windowed")


@pytest.fixture
def headless(tmp_path):
    """The same example with ``painting`` off: the same tree, zero ops."""
    yield from _stage(tmp_path, "headless", painting=False)


@pytest.fixture
def tiny(tmp_path):
    """The example in a 480 x 360 window, which a dialog has to fit inside."""
    yield from _stage(tmp_path, "tiny", size=TINY)


# -- the two pins -------------------------------------------------------

def _page_kinds(stage) -> Tuple[List[Widget], set]:
    """The script's own page list and every kind built on it.

    Walked from ``PAGES`` rather than from ``ui.root``: a scene that is not the
    shown one is detached from the root, and a walk of the root would count only
    the page a person happens to be looking at - which is exactly how a missing
    kind would hide.
    """
    pages = [widget(item) for item in stage.say("PAGES")]
    kinds = set()
    for page in pages:
        for kid in subtree(page):
            kinds.add(kid.kind.name)
    return pages, kinds


def test_every_kind_in_the_catalog_is_on_a_page(stage) -> None:
    """The pin the showcase exists for.

    The day a twenty-sixth kind lands in :mod:`crude.ui.props` this fails until
    somebody shows it on a page - which is the whole reason the example is a
    catalog and not an application.
    """
    pages, kinds = _page_kinds(stage)
    assert len(pages) == len(SCENES), [page.name for page in pages]
    missing = sorted(set(catalog_names()) - kinds)
    assert missing == [], f"these catalog kinds are never built: {missing}"
    # ``badge`` is this file's own kind: it is not in the shipped catalog, and a
    # test that ignored it would let the custom page go unshown
    assert "badge" in kinds, "the kind this example wrote is gone from the pages"


def test_no_word_is_clipped_on_any_page(stage) -> None:
    """The standing rule, measured rather than promised.

    Every scene is opened in turn and the whole shown tree is walked: no clip
    leaves the window or its parent, no painted word leaves its clip, no word
    placed ``inside`` leaves the plate that pays for it, no stacked row overlaps
    another, and the one control told to hide its label really does not paint it.
    Rows the fold has taken off a viewport are skipped - unseen is not cut.
    """
    for scene in SCENES:
        show(stage, scene)
        assert geometry_problems(stage) == [], scene


# -- the deck ------------------------------------------------------------

DECK_TITLES = ["Movement", "Values", "Style", "Signals"]

#: the numbers every deck assertion reads a group by - the title, never a position,
#  because the group verbs move draw order and a test may not be counting on it
_ROW = 'AT(GROUPS(deck), {})'


def _deck_rows(stage) -> List[str]:
    return [stage.say(f'GET({_ROW.format(i)}, "title")') for i in range(4)]


def _row_of(stage, title: str) -> str:
    return _ROW.format(_deck_rows(stage).index(title))


def test_the_deck_ships_four_groups_in_three_columns(stage) -> None:
    assert _deck_rows(stage) == DECK_TITLES
    assert [stage.say(f'GET({_ROW.format(i)}, "cell")') for i in range(4)] == \
        [0.0, 1.0, 2.0, 3.0]
    assert [stage.say(f'GET({_ROW.format(i)}, "order")') for i in range(4)] == \
        [0.0, 1.0, 2.0, 3.0]
    # the third section says it is not dockable, which is what gives the row of
    # dock buttons a refusal to print rather than a page of only successes
    assert [stage.say(f'GET({_ROW.format(i)}, "dockable")') for i in range(4)] == \
        [1.0, 1.0, 0.0, 1.0]
    heights = [stage.say(f'HEIGHT({_ROW.format(i)})') for i in range(4)]
    assert heights == pytest.approx([142.0, 190.0, 134.0, 92.0], abs=0.6)
    # three columns, four groups: two cells exist as somewhere to drop
    assert len(cells(stage.held("deck"))) == 6
    assert float(stage.say("cell_count()")) == 6.0
    assert stage.say("free_words()") == "4, 5"
    assert stage.say('GET(deck_cap, "label")') == \
        "four sections, three columns, cells 4 and 5 free"


def test_a_dock_row_refuses_a_taken_cell(stage) -> None:
    """A cell is not a stack: the second dock into an occupied cell says so."""
    stage.say('SET(m_pick, "value", 1)')
    click_on(stage, stage.held("b_four"))
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "label")') == "Values docked into cell 4"
    assert stage.say(f'GET({_row_of(stage, "Values")}, "cell")') == 4.0
    assert stage.say("free_words()") == "1, 5"
    # and the other group, still in its own cell, is refused the cell Values took
    index = _deck_rows(stage).index("Movement")
    stage.say(f'SET(m_pick, "value", {index})')
    click_on(stage, stage.held("b_four"))
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "label")') == \
        "cell 4 already holds Values - a cell is not a stack"
    assert stage.say(f'GET({_row_of(stage, "Movement")}, "cell")') == 0.0


def test_the_undockable_section_refuses_both_doors(stage) -> None:
    """The row and the hand: ``Style`` says no to each, in its own way."""
    stage.say('SET(m_pick, "value", 2)')
    click_on(stage, stage.held("b_four"))
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "label")') == \
        "Style is not dockable, so nothing picks it up"
    assert stage.say(f'GET({_row_of(stage, "Style")}, "cell")') == 2.0

    # by hand: a drop never raises - it answers nothing, and nothing has moved
    style = stage.held(_row_of(stage, "Style"))
    from_x, from_y = _band(header_box(style))
    to_x, to_y = _band(cells(stage.held("deck"))[5])
    drag(stage, from_x, from_y, to_x - from_x, to_y - from_y)
    settle(stage.ui)
    assert stage.say(f'GET({_row_of(stage, "Style")}, "cell")') == 2.0
    assert stage.say("free_words()") == "4, 5"


def test_a_hand_drop_takes_the_free_cell(stage) -> None:
    """The gesture the grid exists for, driven by the pointer rather than a row."""
    movement = stage.held(_ROW.format(0))
    from_x, from_y = _band(header_box(movement))
    to_x, to_y = _band(cells(stage.held("deck"))[5])
    drag(stage, from_x, from_y, to_x - from_x, to_y - from_y)
    settle(stage.ui)
    assert stage.say(f'GET({_row_of(stage, "Movement")}, "cell")') == 5.0
    # it gave up cell 0 and cell 5 was free: the same two free cells, moved
    assert stage.say("free_words()") == "0, 4"


def test_undock_then_the_free_cell_then_the_read(stage) -> None:
    """The three remaining deck rows, and the order the grid re-reads."""
    stage.say('SET(m_pick, "value", 0)')
    click_on(stage, stage.held("b_five"))
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "label")') == "Movement docked into cell 5"

    click_on(stage, stage.held("b_off"))
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "label")') == \
        ("Movement gave up its cell and sits after its siblings - now cell -1")
    # the verb's -1 is an answer, not a settled fact: the next pass cells the
    # group again, so what the row reads back is a real cell by the frame after

    click_on(stage, stage.held("b_fill"))
    settle(stage.ui)
    said = stage.say('GET(deck_cap, "label")')
    assert said.startswith("Movement took the first free cell, "), said
    took = float(said.rsplit(" ", 1)[1])
    assert stage.say(f'GET({_row_of(stage, "Movement")}, "cell")') == took
    assert len(stage.say("free_words()").split(", ")) == 2

    click_on(stage, stage.held("b_read"))
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "label")') == \
        "GROUPS reads 4 sections in draw order"


def test_the_order_rows_move_the_chosen_section(stage) -> None:
    """``GROUP_UP``, ``GROUP_DOWN`` and ``GROUP_MOVE``, read back as ``order``."""
    stage.say('SET(m_pick, "value", 3)')
    click_on(stage, stage.held("b_up"))
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "label")') == "Signals is drawn at position 2"
    assert stage.say(f'GET({_row_of(stage, "Signals")}, "order")') == 2.0

    click_on(stage, stage.held("b_down"))
    settle(stage.ui)
    assert stage.say(f'GET({_row_of(stage, "Signals")}, "order")') == 3.0

    click_on(stage, stage.held("b_first"))
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "label")') == \
        "Signals is first; the grid re-reads the list"
    assert _deck_rows(stage) == ["Signals", "Movement", "Values", "Style"]


def test_folding_measures_the_header_band(stage) -> None:
    """A folded section is a header: 24 px of band plus the panel's own insets."""
    movement = stage.held(_ROW.format(0))

    press(stage, *_band(chevron_box(movement)))
    settle(stage.ui)
    assert stage.say(f'GET({_row_of(stage, "Movement")}, "folded")') == 1.0
    high = stage.say(f'HEIGHT({_row_of(stage, "Movement")})')
    assert HEADER_HEIGHT < high <= HEADER_HEIGHT + 24.0, high

    press(stage, *_band(chevron_box(movement)))
    settle(stage.ui)
    assert stage.say(f'HEIGHT({_row_of(stage, "Movement")})') == \
        pytest.approx(142.0, abs=0.6)

    # and all four at once, through the rows that fold and open the whole deck
    click_on(stage, stage.held("b_all"))
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "label")') == \
        "4 sections folded to their headers"
    for title in DECK_TITLES:
        high = stage.say(f'HEIGHT({_row_of(stage, title)})')
        assert HEADER_HEIGHT < high <= HEADER_HEIGHT + 24.0, (title, high)

    click_on(stage, stage.held("b_open"))
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "label")') == \
        "4 sections open, each measured from its rows"
    assert [stage.say(f'HEIGHT({_row_of(stage, t)})') for t in DECK_TITLES] == \
        pytest.approx([142.0, 190.0, 134.0, 92.0], abs=0.6)


# -- the controls page ---------------------------------------------------

#: every interactive kind the page is built to hold, under the name the script
#: gives it - the kind list the coverage pin counts, spelled out here so a lost
#: widget fails with a name rather than as a missing set entry
CONTROL_KINDS = {
    "c_btn": "button", "c_tog": "toggle", "c_icon": "icon",
    "c_slid": "slider", "c_vsl": "vslider", "c_knb": "knob",
    "c_num": "number", "c_meter": "meter", "c_radio": "radio",
    "c_drop": "dropdown", "c_xy": "xy", "c_keys": "keybed",
    "c_adsr": "adsr", "c_curve": "curve", "c_bar": "preset_bar",
}


def test_the_button_toggles_and_the_icon_take_a_press(stage) -> None:
    """Three press kinds, three sentences, and the page's kind list on one line."""
    show(stage, "controls")
    assert {name: stage.held(name).kind.name
            for name in CONTROL_KINDS} == CONTROL_KINDS

    click_on(stage, stage.held("c_btn"))
    settle(stage.ui)
    assert stage.say('GET(c_out1, "label")') == "BUTTON: 1 press(es)"

    click_on(stage, stage.held("c_tog"))
    settle(stage.ui)
    assert stage.say('GET(c_out1, "label")') == \
        "TOGGLE on - its glyph changed hands"

    # an icon holds no value, so its click writes the one prop it does have
    click_on(stage, stage.held("c_icon"))
    settle(stage.ui)
    assert stage.say('GET(c_out1, "label")') == "ICON now draws stop"
    assert stage.say('GET(c_icon, "glyph")') == "stop"


def test_the_linked_pair_moves_together_and_the_row_breaks_it(stage) -> None:
    """``LINK_VALUE`` shown as a mirror, ``UNLINK`` shown as its absence.

    The drag is the probe's own geometry - a press at a fifth of the plate and a
    carry of three tenths - because that lands the level on exactly 0.5, which is
    the one number both halves of the sentence can be pinned against.
    """
    show(stage, "controls")
    slid = stage.held("c_slid")
    left, top, width, height = slid.content_box()
    drag(stage, left + width * 0.2, top + height / 2, width * 0.3)
    settle(stage.ui)
    assert stage.say('GET(c_slid, "value")') == \
        stage.say('GET(c_knb, "value")') == stage.say('GET(c_meter, "value")')
    assert stage.say('GET(c_out2, "label")') == "Level 0.5 - Tone 0.5 - Peak 0.5"

    # the mirror runs back the other way, and three frames of it agree is the
    # proof the pair does not feed itself around
    knb = stage.held("c_knb")
    kleft, ktop, kwidth, kheight = knb.content_box()
    drag(stage, kleft + kwidth / 2, ktop + kheight / 2, -kwidth * 0.2)
    settle(stage.ui, 3)
    assert stage.say('GET(c_slid, "value")') == stage.say('GET(c_knb, "value")')

    click_on(stage, stage.held("c_unlink"))
    settle(stage.ui)
    assert stage.say('GET(c_out6, "label")') == "2 link(s) broken from Level"
    stage.say('SET(c_slid, "value", 0.25)')
    settle(stage.ui)
    assert stage.say('GET(c_slid, "value")') == 0.25
    assert stage.say('GET(c_knb, "value")') != 0.25

    click_on(stage, stage.held("c_relink"))
    settle(stage.ui)
    assert stage.say('GET(c_out6, "label")') == \
        "Level mirrors Tone again, and drives Peak"
    stage.say('SET(c_slid, "value", 0.9)')
    settle(stage.ui)
    assert stage.say('GET(c_knb, "value")') == 0.9
    assert stage.say('GET(c_meter, "value")') == 0.9


def test_radio_and_dropdown_answer_by_the_word_they_hold(stage) -> None:
    """The two list kinds report the choice, not the index."""
    show(stage, "controls")
    # two columns of choices: three quarters of the plate's width is the last
    # cell of the second row, and the page holds four choices
    click_on(stage, stage.held("c_radio"), dx=0.75, dy=0.5)
    settle(stage.ui)
    assert stage.say('GET(c_radio, "value")') == 3.0
    assert stage.say('GET(c_out3, "label")') == "RADIO Noise"

    # the dropdown starts on Medium and a click is the cycle the router runs
    click_on(stage, stage.held("c_drop"))
    settle(stage.ui)
    assert stage.say('GET(c_out3, "label")') == "DROPDOWN Large"
    click_on(stage, stage.held("c_drop"))
    settle(stage.ui)
    assert stage.say('GET(c_out3, "label")') == "DROPDOWN Small"


def test_the_shapes_and_the_preset_bar_move_their_own_rows(stage) -> None:
    """Four picture-value kinds and the file-backed one, each in its sentence."""
    show(stage, "controls")
    pad = stage.held("c_xy")
    left, top, width, height = pad.content_box()
    drag(stage, left + width * 0.5, top + height * 0.5,
         width * 0.2, -height * 0.2)
    settle(stage.ui)
    assert stage.say('GET(c_xy, "x")') == pytest.approx(0.7)
    assert stage.say('GET(c_xy, "y")') == pytest.approx(0.7)
    assert stage.say('GET(c_out4, "label")') == "Pad y 0.7"

    for prop, said in (("attack", "Envelope attack 0.5"),
                       ("sustain", "Envelope sustain 0.2")):
        stage.say(f'SET(c_adsr, "{prop}", {0.5 if prop == "attack" else 0.2})')
        settle(stage.ui)
        assert stage.say('GET(c_out4, "label")') == said
    stage.say('SET(c_curve, "tension", 0.6)')
    settle(stage.ui)
    assert stage.say('GET(c_out4, "label")') == "Shape tension 0.6"

    click_on(stage, stage.held("c_keys"), dx=0.3, dy=0.6)
    settle(stage.ui)
    assert stage.say('GET(c_out4, "label")') == "Keys value 0.0"

    # the bar's second cell is the shipped ``wide.json``; a preset is only worth
    # showing because the numbers it moved are readable afterwards
    click_on(stage, stage.held("c_bar"), dy=0.75)
    settle(stage.ui)
    assert stage.say('AT(PRESETS, 1)') == "wide"
    assert stage.say('GET(c_out4, "label")') == \
        "preset wide - seven widgets moved"
    assert stage.say('GET(c_slid, "value")') == 0.75
    assert stage.say('GET(c_num, "value")') == 1180.0
    assert stage.say('GET(c_knb, "value")') == 0.75
    # nothing was written into anybody's preset directory: the copy still holds
    # exactly the two files the example ships
    assert sorted(p.name for p in (stage.home / "presets").glob("*.json")) == \
        ["panel.json", "wide.json"]


def test_the_label_rail_says_where_each_word_went(stage) -> None:
    """All five placements, each in a cell the layout gives the margin for."""
    show(stage, "controls")
    for dial, said in (("r_top", "Top label top, value inside"),
                       ("r_left", "Left label left, value inside"),
                       ("r_right", "Right label inside, value right"),
                       ("r_bot", "Bottom label inside, value bottom"),
                       ("r_none", "Hidden label none, value inside")):
        stage.say(f'SET({dial}, "value", 0.7)')
        settle(stage.ui)
        assert stage.say('GET(c_out5, "label")') == said, dial
    # the hidden one keeps its promise: the word it was told never reaches
    # the frame - which is the standing rule, shown by its own negative
    said = [text for text, _ in own_words(stage, stage.held("r_none"))]
    assert not any("Hidden" in text for text in said), said


def test_the_number_field_takes_the_keyboard_and_the_hover_rows_lit(stage) -> None:
    """Focus, arrows, typed characters, Enter's clamp - and the hover pair.

    Focus arrives by the click path: the Tab path moves the keyboard without
    running either handler, which is an engine asymmetry this example reports
    rather than papers over.
    """
    show(stage, "controls")
    num = stage.held("c_num")
    click_on(stage, num)
    settle(stage.ui)
    assert stage.ui.focus is num
    assert stage.say('GET(c_foc, "label")') == "the field has the keyboard"

    key(stage, "up")
    assert stage.say('GET(c_key, "label")') == "key: up"
    assert stage.say('GET(c_num, "value")') == 441.0

    stage.ui.on_char("7")
    stage.ui.dispatch()
    assert stage.say('GET(c_chr, "label")') == "char: 7"
    # the keystroke is a buffer, not a commit: the value holds until Enter
    assert stage.say('GET(c_num, "value")') == 441.0
    key(stage, "enter")
    assert stage.say('GET(c_num, "value")') == 20.0
    assert stage.say('GET(c_out2, "label")') == "NUMBER 20 - decimals 0"

    click_on(stage, stage.held("c_tog"))
    settle(stage.ui)
    assert stage.say('GET(c_foc, "label")') == "the keyboard moved on"
    assert stage.ui.focus is not num

    assert stage.say('GET(c_hover, "label")') == \
        "a button says when the pointer comes and goes"
    btn = stage.held("c_btn")
    move_to(stage, *_mid(btn))
    assert stage.say('GET(c_hover, "label")') == \
        "the pointer is on the button - its hover role is lit"
    move_to(stage, *_mid(num))
    assert stage.say('GET(c_hover, "label")') == \
        "the pointer left - the role went back to normal"


# -- the layout page ------------------------------------------------------

LAYOUT_BUTTONS = ("l_gmore", "l_gless", "l_align", "l_align", "l_align", "l_align",
                  "l_padmore", "l_padless", "l_narrow", "l_widen", "l_wside",
                  "l_nside", "l_unglue", "l_reglue", "l_end", "l_top")


def layout_reads(stage) -> dict:
    return {name: stage.say(f'GET({name}, "label")')
            for name in ("l_out1", "l_out2", "l_out3", "l_out4", "l_sread",
                         "l_place", "l_packw", "l_cap")}


def test_the_layout_page_ships_its_boxes_and_its_numbers(stage) -> None:
    """The page as it settles: every band where the pass put it, every readout
    saying the number the pass answered with rather than the one asked for."""
    show(stage, "layout")
    boxes = {name: tuple(round(v, 1) for v in stage.held(name).screen_box)
             for name in ("l_page", "l_stacks", "l_grid", "l_pack", "l_anchor",
                          "l_read", "l_colband", "l_view", "l_cap", "l_bar",
                          "l_side", "l_tag", "l_r4", "l_padkid", "l_pk_fix",
                          "l_pk_grow", "l_pk_fill")}
    assert boxes["l_page"] == (0.0, 32.0, 1180.0, 688.0)
    assert boxes["l_stacks"] == (6.0, 38.0, 700.0, 146.0)
    assert boxes["l_view"] == (712.0, 70.0, 462.0, 348.0)
    # the stretched bar stops eight short of the button it is glued to, and that
    # gap is the anchor's own offset - the proof the glue wrote the width
    assert boxes["l_bar"] == (6.0, 510.0, 1002.0, 26.0)
    assert boxes["l_side"] == (1016.0, 510.0, 150.0, 26.0)
    # three policies in one row of a known box: only ``fill`` reached past its
    # content, and ``grow`` stopped at its hint
    assert boxes["l_pk_fix"] == (6.0, 393.0, 120.0, 26.0)
    assert boxes["l_pk_grow"] == (134.0, 393.0, 120.0, 26.0)
    assert boxes["l_pk_fill"] == (262.0, 393.0, 444.0, 26.0)

    said = layout_reads(stage)
    assert said["l_out1"] == \
        "gap 8 asked, the fill cell 146 px - align center: a 26 button in a 40 row"
    assert said["l_out2"] == \
        ("the grid cells are 138 x 54 and 138 x 54 - one formula, so they "
         "cannot differ | PAD 12 leaves the child 226")
    assert said["l_out3"] == "the box is 700 px: fixed 120 - grow 120 - fill 444"
    assert said["l_out4"] == ("the stretched bar is 1002 px, the side button "
                              "150 px, and the tag keeps its own 220 px")
    assert said["l_sread"] == ("the view is 330 px, so it shows 8 of 12 rows - "
                               "content 476 - travel 146 - offset 0")
    assert said["l_packw"] == ("GET(w,'pack'): fixed fixed/fixed - grow "
                               "grow/fixed - fill fill/fixed")
    assert "reads left -> parent.left" in said["l_place"]
    assert said["l_cap"] == ("the layout page: gap, align, cells, padding, "
                             "policies, glues and a viewport")


def test_the_geometry_rows_rewrite_their_own_measurements(stage) -> None:
    """Every button on the page, in the order the numbers depend on each other.

    The align cycle is one sweep because ``stretch`` leaves the row it sized -
    the later sentences read "a 40 button", and a test that clicked the rows in
    another order would be asserting a different page.
    """
    show(stage, "layout")
    expectations = {
        "l_gmore": ("HSTACK(l_row, 12) - the row is arranged again",
                    "l_out1", "gap 12 asked, the fill cell 134 px - "
                    "align center: a 26 button in a 40 row"),
        "l_gless": ("HSTACK(l_row, 8) - the row is arranged again",
                    "l_out1", "gap 8 asked, the fill cell 146 px - "
                    "align center: a 26 button in a 40 row"),
        # the four cycle of ``ALIGNS``, each named by the caption it leaves
        "l_align@0": ("align asked end", "l_out1",
                    "gap 8 asked, the fill cell 146 px - "
                    "align end: a 26 button in a 40 row"),
        "l_align@1": ("align asked stretch", "l_out1",
                    "gap 8 asked, the fill cell 146 px - "
                    "align stretch: a 40 button in a 40 row"),
        "l_align@2": ("align asked start", "l_out1",
                    "gap 8 asked, the fill cell 146 px - "
                    "align start: a 40 button in a 40 row"),
        "l_align@3": ("align asked center", "l_out1",
                    "gap 8 asked, the fill cell 146 px - "
                    "align center: a 40 button in a 40 row"),
        "l_padmore": ("PAD(l_padbox, 16) - the panel owns its children's room",
                      "l_out2", None),
        "l_padless": ("PAD(l_padbox, 12) - the panel owns its children's room",
                      "l_out2", None),
        "l_narrow": ("the pack box is placed 340 px wide", "l_out3",
                     "the box is 340 px: fixed 120 - grow 58 - fill 146"),
        "l_widen": ("the pack box is placed 700 px wide", "l_out3",
                    "the box is 700 px: fixed 120 - grow 120 - fill 444"),
        # the glued bar against the button it stops at: moving the edge moves
        # the bar by exactly what the button took
        "l_wside": ("the side button is 300 px; the bar's glue is its edge",
                    "l_out4", "the stretched bar is 852 px, the side button "
                    "300 px, and the tag keeps its own 220 px"),
        "l_nside": ("the side button is 150 px; the bar's glue is its edge",
                    "l_out4", "the stretched bar is 1002 px, the side button "
                    "150 px, and the tag keeps its own 220 px"),
        "l_unglue": ("4 glues taken away, and the three keep the boxes the "
                     "glues last wrote - undoing a rule is not placing them",
                     "l_place", None),
        "l_reglue": ("the four glues are back, and each owns the axis it "
                     "names", "l_place", None),
        "l_end": ("the offset is the whole travel, which is the only way to "
                  "row twelve", "l_sread", None),
        "l_top": ("offset 0 shows the top of the content again", "l_sread", None),
    }
    aligns = 0
    for name in LAYOUT_BUTTONS:
        click_on(stage, stage.held(name))
        settle(stage.ui)
        key = f"{name}@{aligns}" if name == "l_align" else name
        if name == "l_align":
            aligns += 1
        cap, line, wanted = expectations[key]
        said = layout_reads(stage)
        assert said["l_cap"] == cap, name
        if wanted is not None:
            assert said[line] == wanted, name
    # the sweeps that only moved a number still leave that number behind for
    # the last read: the pad came back to 12 and the glues came back as glues
    said = stage.say('GET(l_out2, "label")')
    assert said.endswith("PAD 12 leaves the child 226"), said
    assert stage.say('GET(l_view, "offset")') == 0.0
    place = layout_reads(stage)["l_place"]
    assert "reads not glued" not in place


def test_the_wheel_carries_the_viewport_and_off_fold_rows_stay_quiet(stage) -> None:
    """A viewport is a fold, not a scrollbar: the ask outruns the travel, and a
    row off the fold is not drawn, so clicking where it *would* be answers nothing."""
    show(stage, "layout")
    view = stage.held("l_view")
    wheel(stage, view, -120 / NOTCH)
    settle(stage.ui)
    assert stage.say('GET(l_sread, "label")') == \
        ("the view is 330 px, so it shows 8 of 12 rows - content 476 - "
         "travel 146 - offset -146")
    # -120 pixels asked as one event; twelve notches would have been -1440, and
    # the pass holds whatever the travel allows rather than pretending to more
    assert stage.say('GET(l_cap, "label")') == \
        "the wheel asked -1920 notch(es); the pass holds the offset at -146"
    wheel(stage, view, 120 / NOTCH)
    settle(stage.ui)
    assert stage.say('GET(l_view, "offset")') == 0.0

    rows = stage.say("LROWS")
    last = widget(rows[-1])
    before = stage.say('GET(l_cap, "label")')
    left, top, width, height = last.screen_box
    press(stage, left + width / 2, top + height / 2)
    settle(stage.ui)
    assert stage.say('GET(l_cap, "label")') == before
    # the travel is the only door: put the row on the fold and the same press
    # reaches the handler that was always wired to it
    stage.say('SET(l_view, "offset", -GET(l_view, "travel"))')
    settle(stage.ui)
    click_on(stage, last)
    settle(stage.ui)
    assert stage.say('GET(l_cap, "label")') == "a viewport row answered: row 12"


def test_a_resize_moves_the_glued_bar_and_leaves_the_placed_row(stage) -> None:
    """The difference between a glue and a ``PLACE``, shown by the window lying."""
    show(stage, "layout")
    stage.ui.on_resize(900, 600)
    stage.ui.dispatch()
    settle(stage.ui, 6)
    assert tuple(round(v, 1) for v in stage.held("l_page").screen_box) == \
        (0.0, 32.0, 900.0, 568.0)
    assert tuple(round(v, 1) for v in stage.held("l_bar").screen_box) == \
        (6.0, 510.0, 722.0, 26.0)
    # the row that was placed at a width, not a glue, does not know the window moved
    assert tuple(round(v, 1) for v in stage.held("l_stacks").screen_box) == \
        (6.0, 38.0, 700.0, 146.0)
    assert stage.say('GET(l_out4, "label")') == \
        ("the stretched bar is 722 px, the side button 150 px, and the tag "
         "keeps its own 220 px")
    stage.ui.on_resize(WIDTH, HEIGHT)
    stage.ui.dispatch()
    settle(stage.ui, 6)
    assert tuple(round(v, 1) for v in stage.held("l_page").screen_box) == \
        (0.0, 32.0, 1180.0, 688.0)


def test_asking_set_for_place_refuses_by_naming_the_door(stage) -> None:
    """The refusal the page shows rather than describes.

    ``l_refuse`` raises on purpose, and the engine error *is* the readout - so
    the test reads the error, says what the caption says about it, and clears
    the list so no later assertion in this file inherits it.
    """
    show(stage, "layout")
    del stage.ui.handlers.errors[:]
    click_on(stage, stage.held("l_refuse"))
    settle(stage.ui)
    assert stage.say('GET(l_cap, "label")') == \
        ("asking SET(l_bar, 'place') - the answer is an engine error, "
         "not a readout")
    assert any("SET: 'place' is what a widget is glued to, and only "
               "ANCHOR() and UNANCHOR() write gluing" in line
               for line in stage.ui.handlers.errors), stage.ui.handlers.errors
    del stage.ui.handlers.errors[:]


# -- the canvases page ----------------------------------------------------

#: the sentences the clip's rows write, in the order the buttons are clicked
CANVAS_BUTTONS = ("cv_mstart", "cv_mend", "cv_pair", "cv_whole")


def clip_window(stage) -> str:
    return stage.say('GET(cv_out3, "label")')


def test_the_five_plates_hold_the_array_and_the_pump(stage) -> None:
    """The two scopes, the two plots and the clip - with the stream as the only hand.

    A run that opened no device answers a flat trace, and that is the honest
    answer; the pump below is what proves the plates are wired rather than painted.
    """
    show(stage, "canvases")
    kinds = {name: stage.held(name).kind.name for name in
             ("cv_wave", "cv_spec", "cv_plot", "cv_clamp", "cv_clip", "cv_log")}
    assert kinds == {"cv_wave": "waveform", "cv_spec": "spectrum",
                     "cv_plot": "plot", "cv_clamp": "plot",
                     "cv_clip": "clip", "cv_log": "logview"}
    # the declared array resource reaches the plot whole - 1024 values, one pass
    assert len(stage.say('GET(cv_plot, "data")')) == 1024
    assert len(stage.say('GET(cv_clamp, "data")')) == 1024
    assert stage.say('GET(cv_out2, "label")') == \
        ("the array's own peak is 0.95, read off 1024 values in one pass; the "
         "copy beside it is CLAMPed to -0.5 .. 0.5, and a plot stretches "
         "whatever span its own list holds")
    assert stage.say('GET(cv_out1, "label")') == \
        ("in peak 0.0 - out peak 0.0 - the trace holds 0 of the 256 asked, "
         "across a plate 574 px wide - 0 bins of 32")
    assert clip_window(stage) == \
        ("window 0 to 1024 of 1024 - mark 1 300 - mark 2 720 - cursor -1 - "
         "584 columns asked for")

    # two blocks of the stream: the frame handler asks for 256 and is answered
    # 128 - "not everything it asked for, exactly what it was given"
    assert stage.project.audio.tick(2) == 2
    settle(stage.ui, 2)
    assert len(stage.say('GET(cv_wave, "data")')) == 128
    assert stage.say('GET(cv_out1, "label")') == \
        ("in peak 0.0 - out peak 0.1 - the trace holds 128 of the 256 asked, "
         "across a plate 574 px wide - 4 bins of 32")


def test_the_cursor_the_marks_and_the_log_rows_answer_their_own_rows(stage) -> None:
    """The six clip rows, each pinned to the sentence it writes."""
    show(stage, "canvases")
    stage.say('SET(cv_cur, "value", 400)')
    settle(stage.ui)
    assert stage.say('GET(cv_clip, "playhead")') == 400.0
    assert stage.say('GET(cv_cap, "label")') == \
        ("the slider parked the cursor on 400 - the pass slides the window "
         "along behind it next frame")

    expectations = {
        "cv_mstart": ("mark 1 now holds the cursor: sample 400 - the number "
                      "on the plate is 1",
                      "window 0 to 1024 of 1024 - mark 1 400 - mark 2 720 - "
                      "cursor 400 - 584 columns asked for"),
        "cv_mend": ("mark 2 now holds the cursor: sample 400 - the number "
                    "on the plate is 2",
                    "window 0 to 1024 of 1024 - mark 1 400 - mark 2 400 - "
                    "cursor 400 - 584 columns asked for"),
        "cv_pair": ("the window is the pair: 400 for 1 sample - the marks "
                    "outside it are not drawn",
                    "window 400 to 401 of 1024 - mark 1 400 - mark 2 400 - "
                    "cursor 400 - 584 columns asked for"),
        "cv_whole": ("count 0 is not a window: the pass resolves it against "
                     "what data holds",
                     "window 0 to 1024 of 1024 - mark 1 400 - mark 2 400 - "
                     "cursor 400 - 584 columns asked for"),
    }
    for name in CANVAS_BUTTONS:
        click_on(stage, stage.held(name))
        settle(stage.ui)
        cap, window = expectations[name]
        assert stage.say('GET(cv_cap, "label")') == cap, name
        assert clip_window(stage) == window, name

    # the log: newest at the foot by default, and the last row by asking past it
    click_on(stage, stage.held("cv_new"))
    settle(stage.ui)
    assert stage.say('GET(cv_cap, "label")') == \
        "scroll 0 sits at the newest row, the way a terminal does"
    assert stage.say('GET(cv_log, "scroll")') == 0.0
    click_on(stage, stage.held("cv_old"))
    settle(stage.ui)
    rows = len(stage.say('GET(cv_log, "lines")'))
    scroll = stage.say('GET(cv_log, "scroll")')
    assert scroll == float(rows - 1)
    # the ask is the row count as the handler saw it, which is one short of the
    # page this very click appended - so "asked" and "reads" name the same number
    assert stage.say('GET(cv_cap, "label")') == \
        f"asked {int(scroll)} rows up; the plate holds it at its own page - " \
        f"GET reads {int(scroll)}"
    said = stage.say('GET(cv_lread, "label")')
    assert said == f"scroll {int(scroll)} - {rows} rows held, newest at the plate's foot"


def test_a_press_a_wheel_and_a_mark_drag_on_the_plate(stage) -> None:
    """The gestures no script can fake: the click's own pixel and the drag's own mark."""
    show(stage, "canvases")
    stage.say('SET(cv_cur, "value", 400)')
    for name in ("cv_mstart", "cv_mend"):
        click_on(stage, stage.held(name))
        settle(stage.ui)

    clip = stage.held("cv_clip")
    left, top, width, height = clip.content_box()
    # a quarter of the plate is a quarter of the take, to the sample the pixel
    # stands for - which is why this pin keeps every digit the arithmetic gives
    press(stage, left + width * 0.25, top + height / 2)
    settle(stage.ui)
    assert stage.say('GET(cv_clip, "playhead")') == 255.56089193825042
    assert stage.say('GET(cv_cur, "value")') == 255.56089193825042
    assert stage.say('GET(cv_cap, "label")') == \
        "a press at 298 px parked the cursor on sample 256 of 1024"

    # a wheel over a window that is already the whole take zooms nothing
    wheel(stage, clip, -120 / NOTCH, dx=0.75)
    settle(stage.ui)
    assert clip_window(stage) == \
        ("window 0 to 1024 of 1024 - mark 1 400 - mark 2 400 - cursor 256 - "
         "584 columns asked for")

    # the only gesture that writes the mark list without being asked: the drag
    at = pixel_of(clip, stage.say('GET(cv_clip, "markers")')[0])
    drag(stage, at, top + height / 2, height * 0.6)
    settle(stage.ui)
    assert stage.say('GET(cv_clip, "markers")') == [437.0, 400.0]
    assert stage.say('GET(cv_cap, "label")') == \
        "a press at 504 px parked the cursor on sample 436 of 1024"


def test_a_dropped_wav_lands_on_the_clip_and_a_text_file_refuses(stage) -> None:
    """``ON_DROP`` with a real sound, and ``WAV_READ`` refusing one that is not.

    The written file goes into the *copy's* directory; the assertion that the
    shipped example still holds no wav is what makes that staging a fact rather
    than a courtesy.
    """
    show(stage, "canvases")
    clip = stage.held("cv_clip")
    left, top, width, height = clip.content_box()
    written = stage.home / "dropped.wav"
    write_wav(str(written),
              [0.4 * ((index % 7) - 3) / 3.0 for index in range(700)], 8000)
    stage.ui.on_cursor(left + width / 2, top + height / 2)
    stage.ui.on_drop([str(written)])
    stage.ui.dispatch()
    settle(stage.ui)
    assert len(stage.say('GET(cv_clip, "data")')) == 700
    assert stage.say('GET(cv_clip, "markers")') == [233, 466]
    assert clip_window(stage) == \
        ("window 0 to 700 of 700 - mark 1 233 - mark 2 466 - cursor 0 - "
         "584 columns asked for")
    # the cursor slider follows the take it is pointing into, not the old one
    assert stage.say('GET(cv_cur, "max")') == 699.0
    assert stage.say('GET(cv_cap, "label")') == \
        ("dropped.wav: 700 samples, 700 frames at 8000 Hz - the mix is mono, "
         "the marks are a third apart")
    assert list(EXAMPLE.glob("*.wav")) == []

    del stage.ui.handlers.errors[:]
    stage.ui.on_drop([str(stage.home / "assets" / "wave.txt")])
    stage.ui.dispatch()
    settle(stage.ui)
    assert stage.say('GET(cv_cap, "label")') == \
        f"dropped on the take: {stage.home / 'assets' / 'wave.txt'}"
    assert any("WAV_READ" in line and "RIFF" in line
               for line in stage.ui.handlers.errors), stage.ui.handlers.errors
    del stage.ui.handlers.errors[:]

    # back to the shipped array, so nothing below measures a page left mid-gesture
    stage.say('SET(cv_clip, "data", WAVE)')
    stage.say('SET(cv_clip, "markers", [MARK_A, MARK_B])')
    stage.say('SET(cv_clip, "count", 0)')
    stage.say('SET(cv_clip, "start", 0)')
    stage.say('SET(cv_clip, "playhead", -1)')
    settle(stage.ui)
    assert clip_window(stage) == \
        ("window 0 to 1024 of 1024 - mark 1 300 - mark 2 720 - cursor -1 - "
         "584 columns asked for")


# -- the custom kind page --------------------------------------------------

CU_BADGES = ("cu_a", "cu_b", "cu_c", "cu_d", "cu_bare")


def press_at(stage, name: str, dx: float) -> None:
    """A press at a pixel offset into the badge's own plate - the hit rule's test."""
    left, top, _width, height = stage.held(name).screen_box
    press(stage, left + dx, top + height / 2)
    settle(stage.ui, 2)


def test_the_badge_paints_the_ops_its_painter_runs(stage) -> None:
    """The painter's own frame, counted against a plain panel's."""
    show(stage, "custom")
    counts = {}
    words = {}
    for name in CU_BADGES + ("cu_plain",):
        frame = painted(stage, stage.held(name))
        counts[name] = tuple(len(frame.ops_named(kind)) for kind in
                             ("rect", "arc", "circle", "line", "text", "atlas"))
        words[name] = [text for text, _ in own_words(stage, stage.held(name))]
    # rect, arc, circle, line, text, atlas - the primitives ON_PAINT asked for
    assert counts["cu_a"] == (1, 1, 1, 1, 2, 1)
    # the dark plate leaves the circle and the atlas mark out, as its painter says
    assert counts["cu_b"] == (1, 1, 0, 1, 2, 0)
    assert counts["cu_d"] == (3, 1, 0, 1, 3, 0)
    # the panel it is built on paints its box and its one word, and nothing else
    assert counts["cu_plain"] == (2, 0, 0, 0, 1, 0)
    assert words["cu_a"] == ["Movement", "the lamp is lit"]
    assert words["cu_b"] == ["Values", "the lamp is dark"]
    assert words["cu_bare"] == ["tag", "the lamp is dark"]
    assert words["cu_plain"] == ["tag"]


def test_the_hit_handlers_name_their_regions(stage) -> None:
    """Two names, one name, a box, a refusal and nothing at all - by the pixel."""
    show(stage, "custom")
    press_at(stage, "cu_a", 20)
    assert stage.say('GET(cu_out1, "label")') == \
        ("one ON_HIT, two names: the plate answered 'lamp', and the lamp "
         "followed because the script said it should")
    assert stage.say('GET(cu_a, "lit")') == 0

    press_at(stage, "cu_a", 120)
    assert stage.say('GET(cu_out1, "label")') == \
        ("one ON_HIT, two names: the plate answered 'word' - the word half "
         "is a word, so nothing moved")
    assert stage.say('GET(cu_a, "lit")') == 0

    press_at(stage, "cu_b", 120)
    assert stage.say('GET(cu_out1, "label")') == \
        ("a name narrows what a click means, not where it may land: it "
         "answered 'body' from either half of the plate")
    assert stage.say('GET(cu_b, "lit")') == 1

    press_at(stage, "cu_c", 20)
    said = stage.say('GET(cu_out1, "label")')
    assert said == ("the box this handler answered took the press; right of "
                    "its rule it says [0, 0, 0, 0] and nothing is written "
                    "here at all")
    # a refused press is a press that says nothing: the line holds its ground
    press_at(stage, "cu_c", 150)
    assert stage.say('GET(cu_out1, "label")') == said

    press_at(stage, "cu_d", 20)
    assert stage.say('GET(cu_out1, "label")') == \
        ("no ON_HIT on this plate, so a click arrives with no region - it "
         "reads '0', which is this language's NULL")

    press_at(stage, "cu_kid", 20)
    assert stage.say('GET(cu_out1, "label")') == \
        ("the button inside the badge took its own press - a kind built on "
         "panel holds children the way a panel does")
    # and none of it ever touched the badge nobody told anything
    assert stage.say('GET(cu_bare, "label")') == "tag"


def test_the_paint_cache_remembers_what_it_was_never_told(stage) -> None:
    """``PAINT_REQUEST`` shown by its absence: a state flip asks nothing."""
    show(stage, "custom")
    base = stage.say("AT(CU_PAINTS, 0)")
    assert stage.say("AT(CU_MODE, 0)") == 0
    assert stage.say("AT(CU_SHOWN, 0)") == 0
    asked = ("the toggle writes state no prop holds, so it asked 5 plates "
             "and 5 gave up the face they had")

    click_on(stage, stage.held("cu_mode"))
    settle(stage.ui)
    assert stage.say("AT(CU_MODE, 0)") == 1
    assert stage.say("AT(CU_SHOWN, 0)") == 1
    assert stage.say("AT(CU_PAINTS, 0)") > base
    assert stage.say('GET(cu_out2, "label")') == asked
    assert stage.ui.paints.stats()["cached"] == 5

    stage.say('SET(cu_mode, "value", 0)')
    settle(stage.ui)
    assert stage.say("AT(CU_SHOWN, 0)") == 0
    assert stage.say('GET(cu_out2, "label")') == asked

    # the silent button changes the very same state - and no face is re-asked
    before = stage.say("AT(CU_PAINTS, 0)")
    click_on(stage, stage.held("cu_silent"))
    settle(stage.ui)
    assert stage.say("AT(CU_MODE, 0)") == 1
    assert stage.say("AT(CU_SHOWN, 0)") == 0
    assert stage.say("AT(CU_PAINTS, 0)") == before
    assert stage.say('GET(cu_out2, "label")') == \
        ("the state reads 1 and the last face was drawn from 0 - nothing "
         "was asked, so nothing was painted")

    click_on(stage, stage.held("cu_ask"))
    settle(stage.ui)
    assert stage.say("AT(CU_SHOWN, 0)") == 1
    assert stage.say("AT(CU_PAINTS, 0)") > before
    assert stage.say('GET(cu_out2, "label")') == \
        ("5 faces dropped, so the next frame runs the painter from the state "
         "it reads now: 1")


def test_the_page_lists_its_own_catalog(stage) -> None:
    """The script literally recites the table the coverage pin reads."""
    show(stage, "custom")
    kinds = stage.say("WIDGET_KINDS()")
    assert kinds[-1] == "badge"
    assert stage.say('GET(cu_r1, "label")') == \
        (f"WIDGET_KINDS() answers {len(kinds)} names - the last of them is "
         "'badge', the kind this file wrote")
    props = stage.say('WIDGET_PROPS("badge")')
    panel_props = stage.say('WIDGET_PROPS("panel")')
    assert len(props) == len(panel_props) + 1, "badge is a panel plus lit"
    assert stage.say('GET(cu_r2, "label")') == \
        (f"WIDGET_PROPS('badge') answers {len(props)} rows - the "
         f"{len(panel_props)} a panel brings, plus lit")
    for name, said in (
        ("cu_r3", "WIDGET_HAS(cu_bare, 'lit') reads 1 and 'value' reads 0"),
        ("cu_r4", "what each plate was built holding: Movement lit - Values "
                  "dark - Style lit - Signals dark - tag dark"),
        ("cu_r5", "and the badge's base is 'panel', which is why the fourth "
                  "plate holds a button and a bare build still says 'tag'"),
        ("cu_out3", "a plain panel told the word 'tag', and a badge nobody "
                    "ever told - the word it paints is the default "
                    "PROP_DEFINE restated above"),
        ("cu_cap", "five plates this file drew, from two rows it declared for "
                   "them - and a button that lives inside one of them, "
                   "because the base is panel"),
        ("cu_n1", "one ON_HIT answers this plate with two names: left of the "
                  "rule is 'lamp', right of it is 'word', and both halves "
                  "take the click"),
        ("cu_n2", "the second plate answers one name for the whole box, so "
                  "either half of it means exactly the same to the handler "
                  "that pressed it"),
        ("cu_n3", "the third answers a box: press right of its rule and the "
                  "click is refused, which is the one answer a plate gives by "
                  "saying nothing"),
        ("cu_p1", "the hatch across a plate is drawn from CU_MODE - script "
                  "state, no prop, and so invisible to the key the face is "
                  "cached under"),
        ("cu_p2", "the toggle asks for a new face; the button beside it only "
                  "moves the state, so the plates keep the hatch they were "
                  "recorded with"),
        ("cu_p3", "P_IMAGE wants an image file and this project ships none, "
                  "so the mark on a lit plate is P_GLYPH, drawn out of the "
                  "generated atlas"),
    ):
        assert stage.say(f'GET({name}, "label")') == said, name
    assert stage.say('GET(cu_out4, "label")') == \
        ("this row was written before a frame ran, when the painter had "
         "recorded 0 faces from mode 0")


# -- the menu bar -----------------------------------------------------------

#: the Deck popup's rows, in order, as the bar itself lists them
DECK_ROWS = ["Fold or open the chosen section", "Move it up one",
             "Move it down one", "Send it to the front",
             "Dock it into cell 4", "Dock it into cell 5", "Give up its cell",
             "Dock it into the free cell", "Re-read the sections",
             "Fold every section", "Open every section"]

DECK_SAID = {
    "Fold or open the chosen section": "Values folded to its header",
    "Move it up one": "Values is drawn at position 0",
    "Move it down one": "Values is drawn at position 1",
    "Send it to the front": "Values is first; the grid re-reads the list",
    "Dock it into cell 4": "Values docked into cell 4",
    "Dock it into cell 5": "Values docked into cell 5",
    "Give up its cell": ("Values gave up its cell and sits after its "
                         "siblings - now cell -1"),
    "Dock it into the free cell": "Values took the first free cell, 4",
    "Re-read the sections": "GROUPS reads 4 sections in draw order",
    "Fold every section": "4 sections folded to their headers",
    "Open every section": "4 sections open, each measured from its rows",
}


def test_the_bar_ships_five_menus_that_stay_inside_the_window(stage) -> None:
    """The menu's own geometry: five titles, their rows, and no word escaping."""
    show(stage, "root")
    bar = stage.say("bar")
    assert [menu.title for menu in bar.menus] == \
        ["Go", "Deck", "View", "Settings", "Help"]
    assert [len(menu.items) for menu in bar.menus] == [6, 11, 4, 3, 2]
    window = (0.0, 0.0, stage.ui.width, stage.ui.height)
    for menu in bar.menus:
        menu.show()
        settle(stage.ui, 2)
        assert menu.is_open()
        assert inside(menu.popup.screen_box, window), menu.title
        # a popup is placed over the page on purpose, so it is held to the word
        # rules alone - which is the standing rule, applied where it can bite
        assert geometry_problems(stage, menu.popup, boxes=False) == [], menu.title
        menu.hide()
        settle(stage.ui, 1)
    # one at a time: showing the second is closing the first
    bar.menus[0].show()
    bar.menus[1].show()
    assert bar.open.title == "Deck"
    assert not bar.menus[0].is_open()
    bar.close_all()
    # and the serial door is genuinely not on this bar: no link is declared
    assert [item.label for item in menu_of(stage.say("set_menu")).items] == \
        ["Audio settings", "MIDI settings", "Quit"]


def test_the_go_rows_are_the_window_own_list(stage) -> None:
    """``MENU_WORKSPACES`` re-reads as it opens, so a scene made late is on it."""
    assert rows_of(stage, "go_menu") == list(SCENES)
    go = menu_of(stage.say("go_menu"))
    go.show()
    assert [item.label for item in go.items if item.checked] == ["root"]
    go.hide()

    pick(stage, "go_menu", "layout")
    settle(stage.ui)
    assert stage.say("WORKSPACE()") == "layout"
    assert stage.say("WORKSPACE_INDEX()") == 2.0
    # the scene that went away is detached, not merely hidden: the walk of the
    # window cannot reach it, which is why the coverage pin walks ``PAGES``
    living = {id(item) for item in subtree(stage.ui.root)}
    assert id(stage.held("AT(PAGES, 2)")) in living
    assert id(stage.held("AT(PAGES, 0)")) not in living

    stage.say('VAR late = WORKSPACE("late")')
    assert rows_of(stage, "go_menu")[-1] == "late"
    assert stage.say("WORKSPACE_NAMES()") == list(SCENES) + ["late"]


def test_every_deck_row_goes_through_the_popup(stage) -> None:
    """All eleven Deck rows, triggered as a hand triggers them, with ``Values``
    chosen - and the settled cells the whole sweep leaves behind."""
    assert rows_of(stage, "deck_menu") == DECK_ROWS
    stage.say('SET(m_pick, "value", 1)')
    for label in DECK_ROWS:
        pick(stage, "deck_menu", label)
        settle(stage.ui, 3)
        assert stage.say('GET(deck_cap, "label")') == DECK_SAID[label], label
    assert [(stage.say(f'GET(AT(GROUPS(deck), {i}), "cell")'),
             stage.say(f'GET(AT(GROUPS(deck), {i}), "order")'))
            for i in range(4)] == \
        [(1.0, 0.0), (2.0, 1.0), (3.0, 2.0), (4.0, 3.0)]


def test_the_view_rows_move_the_deck_and_the_font(stage) -> None:
    """The three View sentences and the one that switches the scene itself."""
    pick(stage, "view_menu", "Re-arrange the deck")
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "label")') == \
        ("Movement moved to the last position, which is 3 - the grid "
         "re-reads the list before it draws")

    pick(stage, "view_menu", "Scroll to the last row")
    settle(stage.ui, 4)
    assert stage.say('GET(l_sread, "label")') == \
        ("the view is 330 px, so it shows 8 of 12 rows - content 476 - "
         "travel 146 - offset -146")
    assert stage.say("WORKSPACE()") == "layout"

    pick(stage, "view_menu", "Enlarge the font")
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "size")') == "lg"
    assert stage.say('GET(deck_cap, "label")') == \
        ("STYLE(deck_cap, 'size', 'lg') - one caption's words, not the "
         "window's, because no verb scales the whole tree")
    pick(stage, "view_menu", "Rein the font in")
    settle(stage.ui)
    assert stage.say('GET(deck_cap, "size")') == "md"


def test_the_settings_dialogs_open_small_and_apply_closes_them(tiny) -> None:
    """Both dialogs, in a 480 x 360 window - sized from it, and inside it.

    The Audio dialog's ``(no devices)`` count belongs to the machine running the
    test, so the pin is the structural one: a page that fits, the title it was
    opened with, the row every audio page must hold - and an Apply that packs
    the page away by itself. The MIDI page is the one that can promise
    ``(no devices)`` twice: no MIDI runtime is declared in this project.
    """
    show(tiny, "root")
    pick(tiny, "set_menu", "Audio settings")
    settle(tiny.ui, 6)
    dialogs = visible_dialogs(tiny.ui)
    assert len(dialogs) == 1
    panel = dialogs[0]
    assert inside(panel.screen_box, (0.0, 0.0, tiny.ui.width, tiny.ui.height))
    words = shown_words(tiny, panel)
    assert words[0] == "Audio settings"
    assert "Sample rate (Hz)" in words
    apply_row = [kid for kid in shown_subtree(panel)
                 if kid.kind.name == "button" and kid.get("label") == "Apply"]
    assert apply_row, "the audio dialog ships no Apply"
    click_on(tiny, apply_row[0])
    settle(tiny.ui)
    assert visible_dialogs(tiny.ui) == []

    pick(tiny, "set_menu", "MIDI settings")
    settle(tiny.ui, 6)
    dialogs = visible_dialogs(tiny.ui)
    assert len(dialogs) == 1
    words = shown_words(tiny, dialogs[0])
    assert words[0] == "MIDI settings"
    assert words.count(NO_DEVICES) == 2
    apply_row = [kid for kid in shown_subtree(dialogs[0])
                 if kid.kind.name == "button" and kid.get("label") == "Apply"]
    click_on(tiny, apply_row[0])
    settle(tiny.ui)
    assert visible_dialogs(tiny.ui) == []
    assert tiny.ui.handlers.errors == []


def test_the_quit_row_is_an_exit_request_and_leaves_no_popup(stage) -> None:
    with pytest.raises(ExitRequest) as raised:
        pick(stage, "set_menu", "Quit")
    assert raised.value.code == 0
    assert not menu_of(stage.say("set_menu")).is_open()


# -- the help wall ----------------------------------------------------------

def test_the_help_wall_opens_carries_and_closes(stage) -> None:
    """Both doors the plan names: the menu's row opens, the page's button closes.

    The wheel values are the probe's own pixels - a log's scroll is notched in
    lines, and the wall is longer than the plate, so the arithmetic belongs to
    the toolkit rather than to this file.
    """
    show(stage, "custom")
    lines = stage.say("HS_WORDS")
    count = len(lines)
    assert stage.say('GET(hs_log, "lines")') is not None
    assert len(stage.say('GET(hs_log, "lines")')) == count

    pick(stage, "help_menu", "What every page is for")
    settle(stage.ui, 3)
    assert stage.say("WORKSPACE()") == "help"
    assert stage.say("WORKSPACE_INDEX()") == 5.0
    assert stage.say("AT(HS_FROM, 0)") == 4.0
    assert stage.say('GET(hs_out, "label")') == \
        (f"opened from scene 4 - {count} lines, and the plate is at the top "
         "of them")
    log = stage.held("hs_log")
    assert lines[0] in shown_words(stage, log)

    left, top, width, height = log.content_box()
    move_to(stage, left + width / 2, top + height / 2)
    stage.ui.on_scroll(0, 240)
    stage.ui.dispatch()
    settle(stage.ui, 2)
    assert stage.say('GET(hs_log, "scroll")') == float(count - 1)
    assert lines[0] in shown_words(stage, log)
    stage.ui.on_scroll(0, -480)
    stage.ui.dispatch()
    settle(stage.ui, 2)
    assert stage.say('GET(hs_log, "scroll")') == 0.0
    said = shown_words(stage, log)
    assert lines[-1] in said and lines[-2] in said

    # the page's own button carries the window back to where it came from
    click_on(stage, stage.held("hs_back"))
    settle(stage.ui)
    assert stage.say("WORKSPACE()") == "custom"
    assert stage.say('GET(hs_out, "label")') == \
        "the window is on 'custom' again - carried, not rebuilt"

    # and the second visit is closed by the menu's other row, not by the page
    pick(stage, "help_menu", "What every page is for")
    settle(stage.ui, 3)
    assert stage.say("WORKSPACE()") == "help"
    pick(stage, "help_menu", "Close the help page")
    settle(stage.ui, 3)
    assert stage.say("WORKSPACE()") == "custom"
    assert f"the wall above holds {count} rows" in stage.say('GET(hs_cap, "label")')


def test_the_wall_is_the_examples_own_words(stage) -> None:
    """The page says what is *not* here, in the words the file wrote for it."""
    show(stage, "help")
    lines = stage.say("HS_WORDS")
    assert lines[0] == "THE DECK - THE ROOT SCENE, AND THE GROUP VERBS"
    assert lines[1] == ("four sections in a grid of three columns: six "
                        "cells, four filled")
    assert any("The serial dialog is not offered:" in line for line in lines)
    assert any(line.startswith("Help opens and closes this page")
               for line in lines)


# -- the headless half -------------------------------------------------------

def test_the_headless_run_builds_the_same_tree_with_no_ops(headless) -> None:
    """The same handlers, the same pages, zero paint calls.

    ``painting=False`` is a window that records nothing; the tree, the handlers
    and the layout pass all still run, so the only difference a test may accept
    is the empty frame.
    """
    pages, kinds = _page_kinds(headless)
    assert len(pages) == len(SCENES)
    assert set(catalog_names()) - kinds == set()
    assert "badge" in kinds
    assert headless.say("WORKSPACE_NAMES()") == list(SCENES)
    for scene in SCENES:
        headless.say(f'WORKSPACE_SHOW("{scene}")')
        settle(headless.ui)
    assert headless.renderer.ops == []
    assert headless.ui.handlers.errors == []
