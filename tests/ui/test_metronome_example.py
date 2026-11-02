"""The metronome example, end to end, on paper.

This is the plan's S8 acceptance line for ``examples/metronome``: the script the
manual points at is run as shipped, by a real :class:`~crude.lang.Program`
against a real :class:`~crude.pd.runtime.PdRuntime` over ``FakeLibpd`` and a real
:class:`~crude.ui.context.UIContext` over a
:class:`~crude.ui.recording.RecordingRenderer`. Every number a person would
check by hand - where a press a quarter of the way along the slider lands, how
many messages one drag sends, which icon the toggle is showing, how fast the LED
falls - is asserted as a number instead of looked at.

Two rules keep it honest:

* **nothing is built by hand here.** The tree is the script's, the sends are the
  script's, the handlers are the script's, so a regression in the example is a
  failing test rather than a window that looks slightly wrong;
* **nothing needs a GPU, a DLL or a device.** The fake library records what Pd
  would have been sent and the recording renderer holds the ops a screen would
  have shown, which is what lets the headless half be compared with the windowed
  one in one run.

The two subsystems are wired the way a real run wires them - one program whose
``host.pd`` and ``host.ui`` are each told their program - so an ``ON_CHANGE`` and
an ``ON_PD`` are proven to reach the interpreter through that one door.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any, List, Tuple

import pytest

from crude.lang import Program
from crude.lang.values import HostHandle, List as CrudeList, Number, String
from crude.pd import FakeLibpd, PdRuntime, PdSession
from crude.ui import RecordingRenderer, UIContext

#: the repository root, whatever directory pytest was started in
ROOT = Path(__file__).resolve().parents[2]

#: the example exactly as it is shipped: this suite holds no copy of it
EXAMPLE = (ROOT / "examples" / "metronome" / "main.crude").read_text(
    encoding="utf-8")

#: the front panel the script's ``PD_BUILD`` asks for
WIDTH, HEIGHT = 360.0, 220.0

#: GLFW's left button and its two actions, and the enter key
LEFT, PRESS, RELEASE = 0, 1, 0
ENTER = 257


# -- helpers ------------------------------------------------------------


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


def _stage(tmp_path, name: str, renderer: RecordingRenderer, *,
           painting: bool = True):
    """Run the shipped example once, with both halves wired and its log kept.

    ``painting`` is ``--mode headless``: the same tree, the same events, zero
    ops, which is the parity line the headless test checks rather than something
    argued from the renderer's own suite.
    """
    home = tmp_path / name
    home.mkdir()
    lib = FakeLibpd()
    session = PdSession(lib)
    session.start()
    notes: List[str] = []
    runtime = PdRuntime(session, project_dir=str(home), log=notes.append)
    ui = UIContext(renderer, width=WIDTH, height=HEIGHT, painting=painting)
    program = Program("<metronome>",
                      host=types.SimpleNamespace(pd=runtime, ui=ui))

    def say(source: str) -> Any:
        """Type ``source`` at the running script and get the Python back."""
        value, error = program.run(source)
        assert error is None, error.as_string()
        return as_py(value)

    say(EXAMPLE)
    # one layout pass, so a box can be read before the first frame is drawn
    ui.layout()

    yield types.SimpleNamespace(lib=lib, session=session, runtime=runtime,
                                renderer=renderer, ui=ui, program=program,
                                notes=notes, say=say)
    session.shutdown()


@pytest.fixture
def stage(tmp_path):
    """The example in a window that is only a recording renderer."""
    yield from _stage(tmp_path, "windowed", RecordingRenderer())


@pytest.fixture
def headless(tmp_path):
    """The same example with ``painting`` off: nothing drawn, everything sent."""
    yield from _stage(tmp_path, "headless", RecordingRenderer(),
                      painting=False)


def click(stage, held, fraction: float = 0.5) -> int:
    """One whole press at a fraction along the widget: how many events it took.

    The release is part of the gesture, not an afterthought: a button left down
    turns every later cursor move into a drag, and a slider that drags would
    answer the question the next test was asking about a press.
    """
    left, top, width, height = held.content_box()
    stage.ui.on_cursor(left + width * fraction, top + height * 0.5)
    stage.ui.on_button(LEFT, PRESS, 0)
    stage.ui.on_button(LEFT, RELEASE, 0)
    return stage.ui.dispatch()


def dragged(stage, held, extra: float) -> int:
    """Press where the pointer is, move by ``extra``, release; the event count."""
    x, y = stage.ui.cursor
    stage.ui.on_button(LEFT, PRESS, 0)
    stage.ui.dispatch()
    stage.ui.on_cursor(x + extra, y)
    stage.ui.on_button(LEFT, RELEASE, 0)
    return stage.ui.dispatch()


def typed(stage, held, text: str) -> int:
    """Click a box, type into it, press enter; how many events that took."""
    events = click(stage, held)
    for character in text:
        stage.ui.on_char(character)
    stage.ui.on_key(ENTER, 0, PRESS, 0)
    return events + stage.ui.dispatch()


def painted(stage) -> None:
    """One frame, with the recording forgotten so only this frame is visible."""
    stage.renderer.reset()
    stage.ui.frame()


def drawn_text(stage, held) -> List[str]:
    """The strings painted inside one widget's own box, in draw order."""
    left, top, width, height = held.screen_box
    return [op.get("text") for op in stage.renderer.ops_named("text")
            if left <= op.get("x") <= left + width
            and top <= op.get("y") <= top + height]


def drawn_glyph(stage) -> Tuple[Any, Any]:
    """The one icon this frame drew, as ``(texture, uv)``."""
    ops = stage.renderer.ops_named("atlas")
    assert len(ops) == 1, [str(op) for op in ops]
    return (ops[0].get("texture"), ops[0].get("uv"))


def sends(stage, recv: str) -> List[float]:
    """Every float delivered to one receiver, flushing first, in order.

    The dsp request sits in the same list as a four-atom message, so the entry is
    read by position rather than unpacked.
    """
    stage.runtime.flush()
    return [entry[2] for entry in stage.lib.sent
            if entry[0] == "float" and entry[1] == recv]


# -- the panel the script describes -------------------------------------


def test_the_script_builds_the_front_panel_it_describes(stage):
    """The patch is authored, the five widgets are placed, and the dsp is asked
    for: the three things a person has to believe without being told."""
    assert sorted(stage.runtime.patches) == ["metro"]
    assert stage.runtime.doc("metro").validate() == []

    title, led, bpm, field, run = (stage.say(name) for name in
                                   ("title", "led", "bpm", "field", "run"))
    assert stage.ui.root.count() == 6, "the window's root and the script's five"
    # a constructor names a widget from its first string argument, so the slider
    # and the number box are both called BPM and only the tree tells them apart
    assert [held.path() for held in (title, led, bpm, field, run)] == [
        "root/METRONOME", "root/beat", "root/BPM", "root/BPM", "root/Run"]
    assert [held.kind.name for held in (title, led, bpm, field, run)] == [
        "text", "meter", "slider", "number", "toggle"]
    # every PLACE box, in logical pixels, exactly where the script put it
    assert [held.box for held in (title, led, bpm, field, run)] == [
        (16.0, 12.0, 200.0, 20.0),
        (16.0, 40.0, 24.0, 24.0),
        (56.0, 40.0, 200.0, 24.0),
        (264.0, 40.0, 80.0, 24.0),
        (16.0, 84.0, 96.0, 28.0),
    ]

    assert stage.lib.sent == [("message", "pd", "dsp", (1.0,))], "PD_DSP(1)"


def test_the_controls_start_where_the_script_said(stage):
    """The defaults are the constructor's and the props the ``SET`` lines wrote."""
    led, bpm, field, run = (stage.say(name) for name in
                            ("led", "bpm", "field", "run"))

    assert [held.value for held in (led, bpm, field, run)] == \
        [0.0, 120.0, 120.0, 0.0]
    assert (stage.say('GET(bpm, "min")'), stage.say('GET(bpm, "max")')) == \
        (20.0, 300.0)
    assert (stage.say('GET(field, "min")'), stage.say('GET(field, "max")')) == \
        (20.0, 300.0)
    assert stage.say('GET(run, "glyph_off")') == "play"
    assert stage.say('GET(run, "glyph_on")') == "pause"


# -- bullet 1: the BPM slider -------------------------------------------


def test_a_press_a_quarter_along_the_slider_sends_ninety_bpm(stage):
    """The plan's number for this gesture: a quarter of 20..300 is 90."""
    bpm, field = stage.say("bpm"), stage.say("field")

    assert click(stage, bpm, 0.25) == 3, "enter, press, release"
    assert bpm.value == 90.0
    assert field.value == 90.0, "LINK_VALUE means one value in two boxes"
    assert len(stage.runtime.session.scheduler) == 1, "ON_CHANGE queued one send"

    assert sends(stage, "bpm") == [90.0]


def test_one_drag_sends_one_number_however_many_times_the_pointer_moved(stage):
    """A drag is three quarters of the span and one message, not three."""
    bpm = stage.say("bpm")
    click(stage, bpm, 0.25)
    stage.runtime.flush()
    stage.lib.sent.clear()

    assert dragged(stage, bpm, 99.0) >= 1
    assert bpm.value == 230.0, "0.75 of 20..300, from the press at 0.25"
    assert sends(stage, "bpm") == [230.0], "the scheduler coalesced the gesture"


def test_typing_into_the_box_moves_the_slider_and_sends_once(stage):
    """The number box is an input, not a second opinion about the value."""
    bpm, field = stage.say("bpm"), stage.say("field")

    typed(stage, field, "140")
    assert field.value == 140.0
    assert bpm.value == 140.0, "the link runs the other way as well"
    assert sends(stage, "bpm") == [140.0]


# -- bullet 2: the value readout and the toggle's glyph -----------------


def test_the_number_box_draws_its_number_and_the_slider_draws_its_name(stage):
    """The skin's readout rule, on the shipped panel rather than on a fixture.

    A ``field``-role box is drawn to be read off, so it carries the value it
    holds; the slider beside it carries the label a person aims at. Both edit
    one patch receiver and one number, and only one of them says ``120``.
    """
    title, led, bpm, field, run = (stage.say(name) for name in
                                   ("title", "led", "bpm", "field", "run"))

    painted(stage)
    assert drawn_text(stage, title) == ["METRONOME"]
    assert drawn_text(stage, led) == ["beat"]
    assert drawn_text(stage, bpm) == ["BPM"]
    assert drawn_text(stage, field) == ["120"]
    assert drawn_text(stage, run) == ["Run"]
    assert len(stage.renderer.ops_named("text")) == 5, "one readout each"

    # and what the box reads is the value it was clamped to, not the digits that
    # were typed at it, while the slider beside it never changes its mind
    typed(stage, field, "500")
    painted(stage)
    assert field.value == 300.0
    assert drawn_text(stage, field) == ["300"]
    assert drawn_text(stage, bpm) == ["BPM"]


def test_the_run_button_shows_play_until_it_is_pressed_and_then_pause(stage):
    """The plan's icon pair on the example's own toggle: ``play`` stopped,
    ``pause`` running, drawn from the atlas rather than from a picture."""
    run = stage.say("run")

    painted(stage)
    found = run.style(stage.ui.skin).resolve(run.role, run.state_name())
    assert run.glyph_name(found) == "play", "stopped, so it offers play"
    key, uv = stage.renderer.glyph_texture("play", found.font)
    assert drawn_glyph(stage) == (key, uv)

    assert click(stage, run) == 3
    assert run.value == 1.0
    painted(stage)
    found = run.style(stage.ui.skin).resolve(run.role, run.state_name())
    assert run.glyph_name(found) == "pause", "running, so it offers pause"
    key, uv = stage.renderer.glyph_texture("pause", found.font)
    assert drawn_glyph(stage) == (key, uv)

    assert sends(stage, "run") == [1.0]
    assert stage.say("led").value == 0.0, "the other ON_CHANGE cleared the beat"


# -- bullet 3: ON_PD("tick") drives the LED -----------------------------


def test_a_tick_from_pd_lights_the_led_and_the_frames_let_it_fall(stage):
    """Pd keeps time, the script owns the blink, and the decay is the script's.

    Nothing here waits on a timer: the seconds arrive as the ``dt`` a frame is
    handed, so the fall is exact on paper and in a window alike.
    """
    led = stage.say("led")
    assert led.value == 0.0

    assert stage.lib.emit("bang", "tick") is True, "as [s tick] would"
    assert stage.runtime.dispatch() == 1, "the one handler the script wired"
    assert led.value == 1.0

    for _ in range(3):
        stage.ui.frame(dt=0.05)
    assert led.value == pytest.approx(0.1), "six a second, three frames of 0.05"

    stage.ui.frame(dt=0.05)
    assert led.value == 0.0, "the script floors it rather than going negative"


def test_the_beat_is_seen_by_the_led_and_nowhere_else(stage):
    """A bang on a name nobody wired is a bang nobody answers."""
    painted(stage)
    before = list(stage.renderer.ops)

    stage.lib.emit("bang", "beat")
    assert stage.runtime.dispatch() == 0, "the meter's label is not a receiver"
    assert stage.say("led").value == 0.0

    painted(stage)
    assert list(stage.renderer.ops) == before, "the same picture, op for op"


# -- the headless half --------------------------------------------------


def test_a_headless_run_sends_the_same_and_draws_nothing(stage, headless):
    """``--mode headless`` is the plan's parity line, not a second code path."""
    for every in (stage, headless):
        click(every, every.say("bpm"), 0.25)
        every.say('SET(led, "value", 1)')
        every.ui.frame(dt=0.05)
        every.runtime.flush()

    assert headless.lib.sent == stage.lib.sent, "the same numbers either way"
    assert headless.say("led").value == stage.say("led").value
    # the frames were still begun and ended, which is what a painter that cached
    # a face would have relied on; there was simply nothing in them
    assert headless.renderer.frames and not any(headless.renderer.frames)
    assert headless.renderer.ops == []

    windowed, silent = stage.ui.frame(dt=0.05), headless.ui.frame(dt=0.05)
    assert (windowed.widgets, silent.widgets) == (6, 6), "the same tree counted"
    assert (windowed.handlers, silent.handlers) == (1, 1), "ON_FRAME ran twice"
    assert windowed.ops > 0, "the panel a window would show"
    assert silent.ops == 0
    assert headless.say("led").value == stage.say("led").value


# -- bullet 4: the Pd GUI golden and its reopen -------------------------

#: the front panel as a saved Pd file holds it: the same five boxes, in the
#  order the script emitted them, at the cosmetic defaults a freshly dragged
#  box carries. A golden, so a change here is a change in what a real Pure Data
#  window would show for the same script.
GUI_GOLDEN = (
    "#N canvas 0 0 360 220 12;",
    "#X text 16 12 METRONOME;",
    "#X obj 16 40 vu 24 24 tick beat -1 -10 0 12 #404040 #000000 0 0;",
    "#X obj 56 40 hsl 200 24 20 300 0 0 bpm empty BPM -2 -8 0 10 #dfdfdf"
    " #000000 #000000 120 1;",
    "#X obj 264 40 nbx 5 24 20 300 0 0 bpm empty BPM 0 -8 0 10 #dfdfdf"
    " #000000 #000000 120 20;",
    "#X obj 16 84 tgl 28 0 run run Run 17 7 0 10 #dfdfdf #000000 #000000 0 1;",
)


def emitted_panel(stage) -> str:
    """Twin the five GL widgets into a Pd canvas and hand back the file text.

    A GL widget's send and receive are the script's to say: nothing implies
    that the box editing 120 belongs to ``[r bpm]``, and guessing one would make
    the emitted patch's data path an invention rather than an authorship.
    """
    return stage.say(
        'SET(bpm, "send", "bpm")\n'
        'SET(field, "send", "bpm")\n'
        'SET(run, "send", "run")\n'
        'SET(run, "recv", "run")\n'
        'SET(led, "recv", "tick")\n'
        'VAR g = PD_BUILD("metro_gui", 360, 220)\n'
        "PD_EMIT(g, title)\n"
        "PD_EMIT(g, led)\n"
        "PD_EMIT(g, bpm)\n"
        "PD_EMIT(g, field)\n"
        "PD_EMIT(g, run)\n"
        "PD_DUMP(g)\n"
    )


def test_the_four_controls_twin_to_the_vanilla_boxes_a_person_would_drag(stage):
    """Which box each kind answers for is the props table's, and it is one answer."""
    assert [stage.say(name).pd_object for name in
            ("bpm", "field", "run", "led")] == ["hslider", "nbx", "tgl", "vu"]
    # a label has no control box to twin to; PD_EMIT writes the plain object
    assert stage.say("title").pd_object == ""


def test_the_front_panel_emits_exactly_the_pd_file_it_should(stage):
    """Every slot in Pd's own order, at the values the panel holds right now."""
    assert emitted_panel(stage).strip().splitlines() == list(GUI_GOLDEN)
    assert stage.runtime.doc("metro_gui").validate() == []


def test_the_emitted_panel_reopens_with_its_receivers_bound(stage):
    """A ``[vu 24 24 tick beat ...]`` binds "tick" the moment Pd opens the file.

    This is the check the plan asks of an emitted GUI: the twin is not
    decoration. Its receive slot has to arrive as a real receiver, or the
    runtime's own probe - which warns about a receiver a patch names but cannot
    find - would be warning about a panel that opens perfectly well in Pure Data.
    """
    emitted_panel(stage)

    assert stage.say("PD_LOAD(g)") is not None
    assert "metro_gui" in stage.runtime.patches
    assert stage.lib.exists("tick") == 1, "the meter's receive slot is a receiver"
    assert stage.lib.receivers >= {"tick", "bpm", "run"}
    assert [note for note in stage.notes if "not reachable" in note] == []

    # and the beat still lands on the LED, through the audio patch's handler
    stage.lib.emit("bang", "tick")
    assert stage.runtime.dispatch() == 1
    assert stage.say("led").value == 1.0
