"""The sound_recorder example: five tracks out of one builder, and a crop that
lands where the hand put it.

``examples/sound_recorder`` is the example that answers the question the widget
stages left open - what is a ``clip`` *for*. It takes a sample in (from a declared
wav or from the input), plots it, and lets a person cut it with four marks:
coarse start, fine start, coarse end, fine end. The pair is the point. A knob that
spans a whole take in one hundred steps cannot stop on a transient, and a knob that
spans eight samples cannot reach the end of a two-second recording; giving a person
both is how a crop lands *on* the click at 0.6 s rather than near it.

The rest of the brief is a multi-track rule, and it is the reason this file exists
rather than a smaller one:

* **one builder, five rows.** ``make_track(index, flags)`` is the only place a row
  is assembled, and the ten parallel lists are what a handler walks to answer
  "which track is this?" - so a control that is not built yet is a *named* flag
  that is refused, not a second copy of a row with the mute button left out;
* **the visible range, not the whole desk.** The rows live in a ``SCROLL`` viewport
  sized for four of them and the fifth is genuinely off the bottom of it;
* **selection is what an edit touches.** A click picks one row, a shift-click adds
  or removes one, and a knob, a dragged mark, a recording or a loaded file is
  applied to the selected rows and to nobody else - which is asserted by watching
  the rows *outside* the selection keep their emptiness;
* **the knobs are small.** Deliberately: a 100 x 88 dial with both its words placed
  *inside* the plate is a knob whose label and readout are painted in the same box
  the dial is drawn in, and the frame is measured here rather than promised. The
  big dials in ``fm_synth`` place their words outside the plate and get clipped;
  that is a known bug in that example, deliberately deferred, and no reason to
  repeat it here.

The stage is the same shape as ``test_fm_synth_example.py``'s: the example's own
manifest read by the loader's own ``load_manifest``, the shipped ``main.crude`` run
by the shipped ``Program``, and behind them the only fakes a machine with no device
can have - ``FakeLibpd`` for Pd, an **opened** ``NullBackend`` for the stream (a
recording has to have an input to record), ``RecordingRenderer`` for the screen.

Two things are deliberately not done here. Nothing writes into the source tree:
every stage runs on a copy of the example inside ``tmp_path``, so ``WAV_WRITE``
lands its ``samples/takeN.wav`` next to the copy - and one assertion below checks
that the shipped directory is still without one. And nothing plays back: the take is
written into the patch's table, which is the half a ``[tabplay~]`` would read, and
that is asserted as a table rather than as a sound.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import types
from pathlib import Path
from typing import Any, List, Sequence, Tuple

import pytest

from crude.audio import AudioConfig, NullBackend, RecordingRender
from crude.audio.runtime import AudioRuntime
from crude.audio.wav import read_wav, write_wav
from crude.lang import Program
from crude.lang.values import HostHandle, List as CrudeList, Number, String
from crude.pd import FakeLibpd, PdRuntime, PdSession
from crude.project import LoadOptions, Project, ResourceTable, load_manifest
from crude.runtime import FakeClock, Runtime
from crude.ui import RecordingRenderer, UIContext, Widget
from crude.ui.clip import pixel_of, sample_at, samples_per_pixel
from crude.ui.input import MODS
from crude.ui.menu import Menu, MenuBar, MenuItem

#: the repository root, whatever directory pytest was started in
ROOT = Path(__file__).resolve().parents[2]

#: the example as shipped: manifest and entry script, read from the same files a
#  ``crude run examples/sound_recorder`` reads
EXAMPLE = ROOT / "examples" / "sound_recorder"
SOURCE = (EXAMPLE / "main.crude").read_text(encoding="utf-8")

manifest = load_manifest(str(EXAMPLE / "crude.manifest.json"))
WIDTH = int(manifest.runtime.width)
HEIGHT = int(manifest.runtime.height)

#: the rate the shipped take and the staged stream both run at
RATE = int(manifest.audio.sample_rate)

#: the words ``make_track`` knows how to build, and the four it names as later
BUILT = ["markers", "record", "load", "save", "select"]
LATER = ["mute", "solo", "mixer", "play"]

#: one block of the staged stream, so a recording test can say "forty blocks"
BLOCK = int(manifest.audio.block_size)


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


def bar_of(value: Any) -> MenuBar:
    """The ``MENUBAR`` answer: a bar, whose panel is the widget in the tree."""
    assert isinstance(value, MenuBar), value
    return value


def at(stage, word: str, i: int) -> Widget:
    """One widget out of one of the script's parallel lists."""
    return widget(stage.say(word)[i])


def rows_of(menu: Menu) -> List[str]:
    """The labels a menu is holding, in order."""
    return [item.label for item in menu.items]


def marks(stage, i: int) -> List[float]:
    """One clip's four marks, as the script reads them."""
    return stage.say(f'GET(AT(CLIPS, {i}), "markers")')


def window(stage, i: int) -> Tuple[float, float]:
    """One clip's visible window: ``(first, span)``, ``0`` meaning everything."""
    return (stage.say(f'GET(AT(CLIPS, {i}), "start")'),
            stage.say(f'GET(AT(CLIPS, {i}), "count")'))


def knobs(stage, i: int) -> List[float]:
    """One row's four knob values: coarse start, fine start, coarse, fine end."""
    return [stage.say(f'GET(AT({word}, {i}), "value")')
            for word in ("KC_S", "KF_S", "KC_E", "KF_E")]


def lengths(stage) -> List[int]:
    """How many samples are on each row, in row order."""
    return [len(take) for take in stage.say("TAKES")]


def all_knobs(stage) -> List[Widget]:
    """The twenty dials the page holds, in row order."""
    return [at(stage, word, i) for word in ("KC_S", "KF_S", "KC_E", "KF_E")
            for i in range(len(stage.say("ROWS")))]


# -- the frame ----------------------------------------------------------

def painted(stage, held: Widget = None) -> RecordingRenderer:
    """One frame of the shipped tree - or of one widget of it - recorded."""
    frame = RecordingRenderer()
    frame.begin(WIDTH, HEIGHT, WIDTH, HEIGHT)
    (held if held is not None else stage.ui.root).paint(frame, stage.ui.skin)
    return frame


def words_on(stage, held: Widget) -> List[Tuple[str, Tuple[float, float, float, float]]]:
    """Every string this widget paints, with the box it is painted into.

    The box is the *measured* one, not the anchor: :meth:`Renderer.text` resolves
    its alignment against :meth:`measure` and stores the shifted ``x``, so the left
    edge in the op is where the word really starts and ``measure`` says how far it
    runs. That is the only pair of numbers that can answer "is it on screen".
    """
    frame = painted(stage, held)
    out = []
    for op in frame.ops_named("text"):
        width, height = frame.measure(op.get("text"), op.get("size"))
        out.append((op.get("text"), (op.get("x"), op.get("y"),
                                     op.get("x") + width, op.get("y") + height)))
    return out


def press(stage, x: float, y: float, mods: int = 0) -> int:
    """One whole left click at a point of the window; how many events it took."""
    stage.ui.on_cursor(x, y)
    stage.ui.on_button(0, 1, mods)
    stage.ui.on_button(0, 0, mods)
    return stage.ui.dispatch()


def click_on(stage, held: Widget, *, shift: bool = False,
             dx: float = 0.5, dy: float = 0.5) -> int:
    """One whole click at a fraction of one widget's own plate."""
    left, top, width, height = held.content_box()
    return press(stage, left + width * dx, top + height * dy,
                 MODS["shift"] if shift else 0)


def drag_along(stage, x: float, y: float, moved: float) -> None:
    """Press at ``x``, carry the pointer ``moved`` pixels right, let go.

    Three dispatches rather than one, because a drag is a movement and the widget
    under the press has to be chosen before anything moves.
    """
    stage.ui.on_cursor(x, y)
    stage.ui.on_button(0, 1, 0)
    stage.ui.dispatch()
    stage.ui.on_cursor(x + moved, y)
    stage.ui.dispatch()
    stage.ui.on_button(0, 0, 0)
    stage.ui.dispatch()


def drag_mark(stage, i: int, index: int, moved: float) -> None:
    """Grab one mark of one clip by its own pixel and carry it."""
    clip = widget(stage.say("CLIPS")[i])
    left, top, width, height = clip.content_box()
    drag_along(stage, pixel_of(clip, marks(stage, i)[index]),
               top + height / 2.0, moved)


def pick(stage, word: str, label: str) -> MenuItem:
    """Trigger one row of a shipped menu, through the popup's own item."""
    held = menu_of(stage.say(word))
    held.show()
    row = [item for item in held.items if item.label == label][0]
    row.trigger()
    held.hide()
    return row


def settle(ui, frames: int = 8) -> None:
    """Layout until a frame answers that nothing moved."""
    moved = [ui.layout() for _ in range(frames)]
    assert moved[-1] == 0, f"the page never settled its layout: {moved}"


def filling(buffer) -> None:
    """The input a staged stream hands back: a plain alternating saw.

    A recording test needs the captured samples to be *some* numbers, and needs
    them not to be silence, or a saved take would agree with nothing. Left and
    right of a mono input are the two channels the manifest asks for, so the plane
    that arrives is this wave's own.
    """
    for index in range(len(buffer)):
        buffer[index] = 0.5 if index % 2 == 0 else -0.25


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
           painting: bool = True, sounds: Sequence[Tuple[str, int, int]] = ()):
    """The shipped example, run once, inside a *copy* of itself.

    The copy is not tidiness: the example writes ``samples/takeN.wav`` relative to
    its own project directory, and a test that ran the shipped file would leave a
    take in the repository. The build cache and the log directory are ignored on
    the way in, so the copy is a fresh project rather than last week's half-run,
    and :class:`PdRuntime` is pointed at it, so the generated patch lands there too.

    ``sounds`` adds declared files to the copy's manifest - a sine of the given
    length and rate is generated into ``assets/`` - because one of the brief's
    rules is that a second sound must be a second menu row out of the *same*
    builder loop rather than a second copy of the script.

    The stream is opened rather than merely built: a take has to arrive from an
    input, and the capture pulls it from the backend on every ``tick``.
    """
    home = tmp_path / name
    shutil.copytree(EXAMPLE, str(home),
                    ignore=shutil.ignore_patterns(".crude_build", "logs"))

    listed = list(manifest.resources.sounds)
    for word, frames, rate in sounds:
        write_wav(str(home / "assets" / f"{word}.wav"),
                  [math.sin(2 * math.pi * 3 * index / rate) for index in range(frames)],
                  rate)
        listed.append(f"assets/{word}.wav")
    where = home / "crude.manifest.json"
    if sounds:
        data = json.loads(where.read_text(encoding="utf-8"))
        data["resources"]["sounds"] = listed
        where.write_text(json.dumps(data), encoding="utf-8")
    # always the *copy's* manifest, so the project's own directory is the copy:
    # a resource path, a build cache and a saved take all hang off that one number
    held_manifest = load_manifest(str(where))

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
                         in_channels=int(manifest.audio.in_channels),
                         out_channels=int(manifest.audio.out_channels))
    stream = NullBackend(config, render=RecordingRender(value=0.1), fill=filling)
    stream.open()
    project.audio = AudioRuntime(config=config, backend=stream)

    project.ui = UIContext(renderer, width=float(held_manifest.runtime.width),
                           height=float(held_manifest.runtime.height),
                           painting=painting)

    loop = Runtime(project, mode="headless", clock=project.clock, ui=project.ui)
    loop.activate(project)

    program = Program(held_manifest.entry, host=project.host_object())
    project.attach_program(program)

    def say(source: str) -> Any:
        """Type ``source`` at the running script and get the Python back."""
        value, error = program.run(source)
        assert error is None, f"{source}\n  {error.as_string()}"
        return as_py(value)

    def held(word: str) -> Widget:
        return widget(say(word))

    say(SOURCE)
    project.pd.flush()
    settle(project.ui)
    # nothing in the load may fail quietly: a handler that raised is a bug in the
    # example, not a detail of the frame it happened to run in
    assert project.ui.handlers.errors == [], project.ui.handlers.errors

    namespace = types.SimpleNamespace(
        project=project, manifest=held_manifest, lib=lib, session=session,
        pd=project.pd, renderer=renderer, ui=project.ui, loop=loop,
        program=program, stream=stream, home=home, notes=notes, say=say,
        held=held)
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
    yield from _stage(tmp_path, "headless", RecordingRenderer(), painting=False)


@pytest.fixture
def two_sounds(tmp_path):
    """The example with a second wav declared, generated into the copy."""
    yield from _stage(tmp_path, "twosounds", RecordingRenderer(),
                      sounds=(("hit", 800, 8000),))


# -- one builder, five rows ---------------------------------------------

def test_five_rows_came_out_of_one_builder(stage):
    """The brief's reuse rule, read off the tree: five rows that are one shape.

    ``make_track`` is the only place a row is assembled, so every row holds the
    same six children in the same order at the same offsets, and the only thing a
    flag changes is whether a handler is registered on them. A second copy of the
    row - the thing this example exists to avoid - would show up here as a shape
    or an offset that differs.
    """
    rows = [widget(held) for held in stage.say("ROWS")]
    count = stage.say("TRACK_COUNT")

    assert len(rows) == count == 5
    shapes = [[child.kind.name for child in held.children] for held in rows]
    assert shapes == [["button", "clip", "knob", "knob", "knob", "knob"]] * count

    # one row measured from its own plate looks exactly like every other one
    offsets = [[(round(child.content_box()[0] - held.content_box()[0], 6),
                 round(child.content_box()[1] - held.content_box()[1], 6),
                 round(child.content_box()[2], 6),
                 round(child.content_box()[3], 6))
                for child in held.children] for held in rows]
    assert all(row == offsets[0] for row in offsets), offsets

    # and the ten parallel lists the handlers walk are as long as the rows are
    for word in ("BARS", "CLIPS", "KC_S", "KF_S", "KC_E", "KF_E", "TAKES",
                 "RATES", "STEPS", "FLAGS", "SOURCES"):
        assert len(stage.say(word)) == count, word
    assert stage.say("FLAGS")[0] == BUILT


def test_the_rows_are_the_visible_range_of_a_longer_desk(stage):
    """"A scrollable visible range", said as geometry: the fifth row is off it.

    The viewport is 556 px and five rows of 136 are 680, which is the whole point
    of the ``SCROLL`` rather than five names on the page. One row hangs below the
    fold - enough to prove the desk is longer than the window, not so many that
    the page is a list nobody can see the end of.
    """
    deck = stage.held("tracks")
    rows = [widget(held) for held in stage.say("ROWS")]
    left, top, width, height = deck.screen_box
    pitch = stage.say("TRACK_H")

    assert deck.kind.name == "scroll"
    assert [round(held.screen_box[1] - rows[0].screen_box[1], 6) for held in rows] \
        == [0.0, pitch, pitch * 2, pitch * 3, pitch * 4]
    assert len(rows) * pitch > height, "five rows do not fit, by design"

    below = [index for index, held in enumerate(rows)
             if held.screen_box[1] + held.screen_box[3] > top + height]
    assert below == [4], ([held.screen_box for held in rows], top + height)


def test_the_knobs_are_small_and_every_word_of_them_is_on_the_plate(stage):
    """The knobs are deliberately not the big ones, and the words are inside.

    This is the line the brief asked for in its own words: a dial whose name and
    whose readout are placed *inside* the plate is charged for out of the same box
    the dial is drawn in, so both fit and neither is clipped. ``fm_synth`` places
    its labels ``"top"`` - outside the plate, on the face's overflow - and at that
    example's panel heights the words disappear; nothing here may repeat it, so
    the check is on the painted frame, measured, not on a prop alone.
    """
    dials = all_knobs(stage)
    size = (stage.say("KNOB_W"), stage.say("KNOB_H"))
    row_height = stage.say("TRACK_H")

    assert len(dials) == 20
    assert size == (100.0, 88.0)
    assert size[1] < row_height, "a dial taller than its row cannot be shown"

    for dial in dials:
        assert dial.get("label_place") == "inside", dial.get("label_place")
        assert dial.get("value_place") == "inside", dial.get("value_place")

        plate = dial.screen_box
        left, top, width, height = plate
        row = dial.parent.screen_box
        assert row[0] <= left and left + width <= row[0] + row[2], plate
        assert row[1] <= top and top + height <= row[1] + row[3], plate

        said = words_on(stage, dial)
        # the name of the control and the number it stands for: two lines, both
        # whole, both inside the plate the dial was built at
        assert len(said) == 2, said
        for text, box in said:
            assert text.strip(), repr(text)
            assert left <= box[0] and box[2] <= left + width, (text, box, plate)
            assert top <= box[1] and box[3] <= top + height, (text, box, plate)

    # and the four of them, side by side, still leave the row's right edge inside
    right = max(dial.screen_box[0] + dial.screen_box[2] for dial in dials[:4])
    assert right <= stage.held("tracks").screen_box[0] + \
        stage.held("tracks").screen_box[2]


def test_a_flag_that_is_not_built_is_named_and_builds_nothing(stage):
    """A flag is a request for a control, and an unbuilt one is refused out loud.

    Half a row that quietly lacks a mute button is worse than no row at all: it
    looks like a track and behaves like a bug. So ``make_track`` asks
    :func:`bad_flags` first, builds nothing when the answer is not zero, and the
    sentence names the flag, the five that are built and the four that are on the
    list for later.
    """
    before = len(stage.say("ROWS"))

    assert not stage.say('make_track(9, ["mute"])'), "a refused row is not built"
    assert len(stage.say("ROWS")) == before, "a refused row must not be half-built"
    assert stage.say("bad_flags([\"markers\", \"record\"])") == 0
    assert stage.say("bad_flags([\"solo\"])") == 1

    said = stage.project.notes[-1]
    assert "mute" in said, said
    assert ", ".join(BUILT) in said, said
    assert ", ".join(LATER) in said, said
    assert stage.say('wants(0, "record")') == 1
    assert stage.say('wants(0, "mute")') == 0


# -- the take, and the four numbers that cut it -------------------------

def test_the_page_opens_on_a_real_declared_take(stage):
    """The shipped tone, read off the disk, on the first row and nobody else's.

    The file is generated by ``scripts/make_tone_wav.py`` and declared in the
    manifest, so the wave on the plate is a wav a person could open elsewhere -
    which is what the comparison below is for.
    """
    held = read_wav(str(EXAMPLE / "assets" / "tone.wav"))

    assert stage.say("OPEN_IDS") == ["tone"]
    assert lengths(stage) == [88200, 0, 0, 0, 0]
    assert stage.say("RATES") == [RATE, 0, 0, 0, 0]
    assert stage.say("SOURCES") == ["tone", "empty", "empty", "empty", "empty"]
    assert stage.say("SELECTED") == [0]

    take = stage.say("AT(TAKES, 0)")
    assert len(take) == held.frames == 88200
    assert take[:64] == pytest.approx(held.samples[:64], abs=1e-9)
    assert [str(bar.get("label")) for bar in stage.say("BARS")][0] == "tone 1"
    assert marks(stage, 0) == [0, 0, 88199, 88199]


def test_the_knobs_answer_on_the_take_they_are_showing(stage):
    """A coarse knob walks the take in hundred steps; a fine one in samples.

    The range is not a constant: it is the length of the take divided up, which is
    why an empty row has no ruler at all (``min == max == 0``) rather than the
    0..100 the constructor had to be born with, and why a row of 800 samples walks
    eight at a time.
    """
    step = stage.say("STEPS")[0]

    assert step == 882 == math.floor(88200 / 100)
    assert stage.say("STEPS") == [882, 1, 1, 1, 1]
    assert stage.say("coarse_step(AT(TAKES, 0))") == 882
    assert stage.say("coarse_step([1, 2, 3])") == 1, "a short take is walked one sample"

    start = at(stage, "KC_S", 0)
    fine = at(stage, "KF_S", 0)
    assert (start.get("min"), start.get("max"), start.get("step")) == (0.0, 88199.0, step)
    assert (fine.get("min"), fine.get("max"), fine.get("step")) == (-step, step, 1.0)
    assert start.get("decimals") == 0, "a sample index is a whole number"

    # a row with nothing on it has no ruler, so it cannot answer a phantom sample
    empty = at(stage, "KC_S", 1)
    assert (empty.get("min"), empty.get("max"), empty.get("step")) == (0.0, 0.0, 1.0)
    assert knobs(stage, 1) == [0, 0, 0, 0]
    assert marks(stage, 1) == []
    assert stage.say("crop(1)") == []


def test_a_coarse_turn_writes_both_marks_of_its_pair(stage):
    """Four knobs, four marks, and only two numbers behind each end.

    The marks on the plate are the pair read out: the rough line is the coarse
    knob and the crop edge is the coarse plus the fine. Nothing here can make them
    disagree, because there is nothing else to disagree with.
    """
    assert marks(stage, 0) == [0, 0, 88199, 88199]

    stage.say('SET(AT(KC_S, 0), "value", 5000)')
    assert marks(stage, 0) == [5000, 5000, 88199, 88199]

    stage.say('SET(AT(KF_S, 0), "value", 7)')
    assert marks(stage, 0) == [5000, 5007, 88199, 88199]

    stage.say('SET(AT(KC_E, 0), "value", 60000)')
    stage.say('SET(AT(KF_E, 0), "value", -11)')
    assert marks(stage, 0) == [5000, 5007, 60000, 59989]
    assert knobs(stage, 0) == [5000, 7, 60000, -11]

    # and the crop is the two *fine* marks, both of them inside it
    cut = stage.say("crop(0)")
    assert len(cut) == 59989 - 5007 + 1
    assert cut[:3] == pytest.approx(stage.say("AT(TAKES, 0)")[5007:5010], abs=1e-9)


def test_dragging_the_crop_edge_honours_the_hand_and_takes_the_knob_with_it(stage):
    """A mark dragged further than one coarse step moves the coarse knob too.

    The pair is two numbers, so a hand that wants sample 17 933 when the coarse
    knob is on 5 000 cannot have it by winding the fine knob to 12 933 - the fine
    knob only reaches one step either way. So the dragged mark is the request and
    the coarse number follows it, which is what keeps the lines on the plate and
    the four readouts in agreement however far a person drags.

    The fine knob is wound to 300 first, because two marks on the same sample are
    one pixel and a hand cannot grab one of two lines drawn on top of each other.
    """
    step = stage.say("STEPS")[0]
    stage.say('SET(AT(KC_S, 0), "value", 5000)')
    stage.say('SET(AT(KF_S, 0), "value", 300)')
    clip = at(stage, "CLIPS", 0)
    edge = marks(stage, 0)[1]
    assert edge == 5300, "the crop edge is the pair read out"
    aimed = sample_at(clip, pixel_of(clip, edge) + 100.0)

    drag_mark(stage, 0, 1, 100.0)

    after = marks(stage, 0)
    assert after[1] == pytest.approx(aimed, abs=1.0), (after, aimed)
    assert after[1] - after[0] == step, "the fine knob is at its limit, as it must be"
    assert knobs(stage, 0)[:2] == [after[0], step]


def test_a_rough_mark_cannot_be_dragged_past_one_coarse_step(stage):
    """The other half of the same rule: the rough line is a *hint*, not a crop.

    What gets saved is cut between marks 2 and 4, so a rough line dragged four
    steps past its edge is held at one step - the furthest the pair can say -
    rather than quietly moving the crop by an amount the fine knob could never
    have named.
    """
    step = stage.say("STEPS")[0]
    stage.say('SET(AT(KC_S, 0), "value", 5000)')
    clip = at(stage, "CLIPS", 0)
    across = samples_per_pixel(clip)

    drag_mark(stage, 0, 0, 40.0)
    wanted = 5000 + 40.0 * across

    after = marks(stage, 0)
    assert wanted - after[1] > step, "the hand asked for well past one step"
    assert after[1] == 5000, "the crop edge did not move"
    assert after[0] == 5000 + step, after
    assert knobs(stage, 0)[:2] == [after[0], 0 - step]


def test_the_wheel_zooms_about_the_sample_under_the_pointer(stage):
    """One notch halves the window with the pointer's sample held still.

    A zoom that scaled about its own left edge would slide the waveform out from
    under the cursor, so the sample the pointer is on is the one number a zoom is
    measured against - and one notch back is the whole take again, which is what
    the ``Show the whole take`` row says the same way a script can.
    """
    clip = widget(stage.say("CLIPS")[0])
    stage.say("set_follow(0)")
    left, top, width, height = clip.content_box()
    x = left + width * 0.25
    aimed = sample_at(clip, x)
    stage.ui.router.move_to(x, top + height / 2.0)
    stage.ui.router.scroll_at(1)

    first, count = window(stage, 0)
    assert count == 88200 / 2
    assert sample_at(clip, x) == pytest.approx(aimed, abs=1.0), (first, count)

    stage.ui.router.scroll_at(-1)
    assert window(stage, 0) == (0.0, 0.0), "one step back is the whole take"

    # a window written by a script is the same window a hand reads
    stage.say('SET(AT(CLIPS, 0), "count", 4000)')
    stage.say("show_whole()")
    assert window(stage, 0) == (0.0, 0.0)


# -- selection ----------------------------------------------------------

def test_a_plain_click_picks_one_row_and_a_shift_click_adds_or_removes_one(stage):
    """The DAW's own selection gesture, through the router rather than a call.

    A shift-click that emptied the selection would leave the page with nothing for
    the next edit to touch, so the last row out falls back to the first - the same
    answer a person expects when they shift-click the row they are already on.
    """
    bars = [widget(held) for held in stage.say("BARS")]

    def roles() -> List[str]:
        return [str(bar.get("role")) for bar in bars]

    assert stage.say("SELECTED") == [0]
    assert roles() == ["selection", "button", "button", "button", "button"]

    click_on(stage, bars[3])
    assert stage.say("SELECTED") == [3]

    click_on(stage, bars[1], shift=True)
    assert stage.say("SELECTED") == [3, 1]
    assert roles() == ["button", "selection", "button", "selection", "button"]

    click_on(stage, bars[3], shift=True)
    assert stage.say("SELECTED") == [1]

    click_on(stage, bars[1], shift=True)
    assert stage.say("SELECTED") == [0], "the page is never left with nothing selected"


def test_an_edit_reaches_the_selected_rows_and_no_other(stage):
    """Multi-select is the whole reason a handler has to know who sent it.

    Two rows loaded, one knob turned: both selected rows get the crop, and the two
    rows outside the selection keep their emptiness - no marks, no ruler, no
    samples. That is asserted positively (the pair moved) and negatively (nobody
    else did), because the failure this rule has is a silent edit to a row that
    was never asked for.
    """
    click_on(stage, at(stage, "BARS", 2), shift=True)
    assert stage.say("SELECTED") == [0, 2]

    stage.say('load_sound("tone")')
    assert lengths(stage) == [88200, 0, 88200, 0, 0]
    assert marks(stage, 0) == marks(stage, 2) == [0, 0, 88199, 88199]

    stage.say('SET(AT(KC_S, 0), "value", 5000)')
    assert marks(stage, 0) == marks(stage, 2) == [5000, 5000, 88199, 88199]
    assert knobs(stage, 0) == knobs(stage, 2)

    for i in (1, 3, 4):
        assert marks(stage, i) == [], i
        assert knobs(stage, i) == [0, 0, 0, 0], i
        assert lengths(stage)[i] == 0, i


def test_the_view_rows_do_what_the_page_does(stage):
    """The menu is a second door to the same functions, not a parallel implementation.

    ``Select every track`` is :func:`select_all`, and the ``Follow playhead`` check
    row writes the clip's own ``follow`` prop - the one the layout pass reads - so
    ticking it off is the same as a script setting it, and the mark on the row is
    the page's state rather than the menu's memory.
    """
    pick(stage, "view_menu", "Select every track")
    assert stage.say("SELECTED") == [0, 1, 2, 3, 4]
    assert [str(bar.get("role")) for bar in stage.say("BARS")] == ["selection"] * 5

    pick(stage, "view_menu", "Select only the first")
    assert stage.say("SELECTED") == [0]
    assert at(stage, "CLIPS", 0).get("follow") != 0

    row = pick(stage, "view_menu", "Follow playhead")
    assert row.checked is False
    assert at(stage, "CLIPS", 0).get("follow") == 0

    pick(stage, "view_menu", "Follow playhead")
    assert at(stage, "CLIPS", 0).get("follow") == 1


# -- in and out ---------------------------------------------------------

def test_a_take_lands_on_the_selected_rows_and_on_no_other(stage):
    """Record: arm, watch it arrive, stop, and the crop is laid out.

    The live half is throttled by ``DRAW_STEP`` on purpose - copying 88 000 floats
    into five clips every frame is not a picture, it is a stall - so the first
    thirty blocks leave the tone on the plate and the fortieth after it hands the
    clip the arrival, with the cursor on the newest sample. On stop, only the
    selected row is given the take, and it is given its *own* copy.
    """
    stage.say("select_only(0)")
    assert stage.say("record_on()") == 1
    assert stage.say('GET(AUDIO_RECORD(), "armed")') == 1
    assert stage.say('GET(AUDIO_RECORD(), "capacity")') == \
        stage.say("RECORD_SECONDS") * RATE

    stage.project.audio.tick(30)
    stage.say("frame(0.016)")
    assert len(at(stage, "CLIPS", 0).get("data")) == 88200, "not yet DRAW_STEP frames"
    assert len(stage.held("scope").get("data")) == 256
    assert stage.held("meter").get("value") > 0.0, "the input is not silence"

    stage.project.audio.tick(40)
    stage.say("frame(0.016)")
    arrived = 70 * BLOCK
    clip = at(stage, "CLIPS", 0)
    assert len(clip.get("data")) == arrived
    assert clip.get("playhead") == arrived - 1
    assert str(at(stage, "BARS", 0).get("label")) == "live 1"

    frames = stage.say("record_off()")
    assert frames == arrived
    assert lengths(stage) == [arrived, 0, 0, 0, 0]
    assert stage.say("RATES") == [RATE, 0, 0, 0, 0]
    assert stage.say("SOURCES") == ["live", "empty", "empty", "empty", "empty"]
    assert marks(stage, 0) == [0, 0, arrived - 1, arrived - 1]
    assert stage.say("STEPS")[0] == math.floor(arrived / 100)

    # what was captured is what the staged stream was handing the input, and what
    # a row that was never asked for kept as nothing
    take = stage.say("AT(TAKES, 0)")
    assert max(abs(value) for value in take) == pytest.approx(0.5, abs=1e-9)
    assert len({round(value, 6) for value in take}) == 2


def test_the_status_line_survives_a_take_that_has_samples_but_no_marks(stage):
    """A crop is laid out when a take *stops*, so a line about it must not assume one.

    Every row selected, four of them empty: the arrival gives all five samples and
    the marks only the rows that have a crop. A status line that read the marks of
    a row that has none is the crash this pins - ``frame`` is run through the
    script's own entry point, because that is where it happened.
    """
    stage.say("select_all()")
    stage.say("record_on()")
    stage.project.audio.tick(70)
    stage.say("frame(0.016)")

    said = [str(line) for line in stage.held("log").get("lines")]
    assert len(said) == 1
    assert said[0].startswith("RECORDING - 5 tracks, 5 selected - live 5: ")
    assert "4480 samples at 44100 Hz" in said[0], said[0]
    assert "crop" not in said[0], "this row has samples and no marks yet"

    assert stage.say("record_off()") == 70 * BLOCK
    assert lengths(stage) == [70 * BLOCK] * 5
    assert marks(stage, 4) == [0, 0, 70 * BLOCK - 1, 70 * BLOCK - 1]


def test_saving_writes_the_crop_and_only_the_crop(stage):
    """``samples/takeN.wav`` is the two fine marks, at the rate the take was taken at.

    The file is read back off the disk with the same codec a person would use, and
    compared with what the page says the crop is. A row with nothing on it writes
    nothing: five rows selected and one take on the page is one file, not five
    silences.
    """
    stage.say('SET(AT(KC_S, 0), "value", 1000)')
    stage.say('SET(AT(KF_S, 0), "value", 5)')
    stage.say('SET(AT(KC_E, 0), "value", 1100)')
    stage.say('SET(AT(KF_E, 0), "value", -3)')
    cut = stage.say("crop(0)")

    assert marks(stage, 0) == [1000, 1005, 1100, 1097]
    assert len(cut) == 1097 - 1005 + 1, "both edge marks are inside the crop"

    stage.say("select_all()")
    assert stage.say("save_selected()") == 5

    written = sorted(path.name for path in (stage.home / "samples").glob("*.wav"))
    assert written == ["take1.wav"], written
    found = read_wav(str(stage.home / "samples" / "take1.wav"))
    assert found.frames == len(cut)
    assert found.rate == RATE
    # 16-bit quantisation: a value the page holds as 0.5 is the nearest of 65 536
    assert found.samples == pytest.approx(cut, abs=1.0 / 32767)

    assert not (EXAMPLE / "samples").exists(), "a test must not write in the tree"


def test_a_second_declared_sound_is_a_second_menu_row_the_same_function_loads(two_sounds):
    """``RESOURCES("sound")`` one row per declared file, and ``SENDER()`` says which.

    The manifest of the copy declares a second wav - generated for the test, 800
    samples at 8000 Hz - and the menu grew a row without ``main.crude`` being read
    again. One function serves both rows because it asks which row ran, which is
    the same reason one builder serves five tracks.
    """
    menu = menu_of(two_sounds.say("file_menu"))

    assert two_sounds.say("OPEN_IDS") == ["tone", "hit"]
    assert rows_of(menu)[:3] == ["open tone", "open hit", "Record"]
    assert lengths(two_sounds) == [88200, 0, 0, 0, 0]

    pick(two_sounds, "file_menu", "open hit")

    assert lengths(two_sounds) == [800, 0, 0, 0, 0]
    assert two_sounds.say("RATES")[0] == 8000
    assert two_sounds.say("STEPS") == [8, 1, 1, 1, 1]
    assert marks(two_sounds, 0) == [0, 0, 799, 799]
    assert str(at(two_sounds, "BARS", 0).get("label")) == "hit 1"
    assert two_sounds.project.notes[-1] == \
        "loaded hit - 800 samples at 8000 Hz onto 1 track(s)"

    # and the ruler followed the take it was given: eight samples a coarse step
    assert at(two_sounds, "KC_S", 0).get("step") == 8


def test_the_current_take_is_written_into_the_patch_s_table(stage):
    """The table a ``[tabplay~]`` would read, resized and filled by the take.

    There is no playback yet and this is the honest half of that sentence: the take
    does reach the patch, as the numbers Pd was handed, through one table that is
    resized rather than replaced - which is what ``PD_ARRAY_RESIZE`` was added for.
    """
    named = [name for name in stage.lib.arrays if name.endswith("-sample")]

    assert len(named) == 1, list(stage.lib.arrays)
    assert len(stage.lib.arrays[named[0]]) == 88200

    held = read_wav(str(EXAMPLE / "assets" / "tone.wav"))
    assert stage.lib.arrays[named[0]][:64] == \
        pytest.approx(held.samples[:64], abs=1e-9)

    # a shorter take lands in the same table, resized, with nothing left past its end
    stage.say('load_sound("tone")')
    stage.say("record_on()")
    stage.project.audio.tick(10)
    stage.say("record_off()")
    assert [name for name in stage.lib.arrays if name.endswith("-sample")] == named
    assert len(stage.lib.arrays[named[0]]) == 10 * BLOCK


def test_the_patch_is_generated_into_the_copy_and_says_only_what_it_did(stage):
    """The authored patch, built once, in the project directory the test made.

    A ``PD_BUILD`` with no unreachable receivers in it is the difference between a
    patch that works and one that quietly drops every send, so the runtime's own
    log is read: the built file, and nothing else.
    """
    built = sorted(path.name for path in
                   (stage.home / ".crude_build" / "pd").glob("*.pd"))
    said = " ".join(stage.notes).lower()

    assert len(built) == 1, built
    assert built[0].startswith("recorder-"), built
    assert any(word in said for word in ("built", "recorder")), stage.notes
    assert "unimplemented" not in said and "could not" not in said, stage.notes
    assert (stage.home / ".crude_build" / "pd" / built[0]).is_file()


# -- the page around the rows ------------------------------------------

def test_the_help_page_is_the_script_s_own_words_and_opens_a_door(stage):
    """A help page built out of ``LOGVIEW`` lines, opened by the menu and by the button.

    The text is the example's own documentation of the pair rule and of the four
    controls that are flags rather than widgets, so it is asserted by content: a
    person who has never seen this file should be able to find what a mark means
    and what is missing.
    """
    page = stage.held("help_page")
    lines = [str(line) for line in page.get("lines")]
    said = " ".join(lines).lower()

    assert stage.held("help").get("visible") == 0
    assert lines[0].startswith("SOUND RECORDER - five tracks, one builder")
    for word in LATER:
        assert word in said, word
    assert "coarse" in said and "fine" in said

    pick(stage, "help_menu", "Using this recorder")
    assert stage.held("help").get("visible") == 1
    assert page.get("scroll") == len(lines), "the page opens at its last line"

    pick(stage, "help_menu", "Close help")
    assert stage.held("help").get("visible") == 0
    click_on(stage, stage.held("help_close"))
    assert stage.held("help").get("visible") == 0


def test_the_menu_offers_the_three_files_and_the_device_dialog(stage):
    """File, View and Help - and the rows say what the page can actually do.

    ``Audio devices`` is the one row that opens a dialog rather than calling a
    function, and it is here because the recorder is the example that needs an
    input: a person with three sound cards has to be able to pick one without a
    manifest edit.
    """
    assert rows_of(menu_of(stage.say("file_menu"))) == \
        ["open tone", "Record", "Save WAV", "Audio devices", "Quit"]
    assert rows_of(menu_of(stage.say("view_menu"))) == \
        ["Select every track", "Select only the first", "Show the whole take",
         "Follow playhead"]
    assert rows_of(menu_of(stage.say("help_menu"))) == \
        ["Using this recorder", "Close help"]

    bar = bar_of(stage.say("bar"))
    assert [menu.title for menu in bar.menus] == ["File", "View", "Help"]
    assert [menu.name for menu in bar.menus] == ["File", "View", "Help"]


def test_a_headless_run_builds_the_same_page_and_draws_nothing(stage, headless):
    """The same tree with the paint switched off, down to the take on the plate.

    ``--mode headless`` is one code path and not a second instrument: the recorder
    loads its wav, builds five rows and puts the crop on the marks exactly as a
    window does, and asks its renderer for nothing.
    """
    for every in (stage, headless):
        every.say('SET(AT(KC_S, 0), "value", 5000)')

    assert lengths(headless) == lengths(stage)
    assert marks(headless, 0) == marks(stage, 0)
    assert headless.say("SELECTED") == stage.say("SELECTED") == [0]
    assert [child.kind.name for child in headless.say("ROWS")[0].children] == \
        [child.kind.name for child in stage.say("ROWS")[0].children]
    assert headless.ui.root.count() == stage.ui.root.count()
    assert headless.ui.handlers.errors == [] == stage.ui.handlers.errors
    assert headless.renderer.ops == []
