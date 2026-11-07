"""S12's ``WIDGET``/``ON`` sugar on a live tree.

The lang suite proves the block parses to the twin's AST; these tests run both
spellings through two fresh programs over recording renderers and compare what
the host ends up holding: the same tree, the same frame, the same handlers
fired. No display, no OpenGL - the same ``UIContext`` shape the binding tests
use, because the sugar's promise is about behaviour, not pixels.

(The basename is unique across the test tree, as ``ui_helpers.py`` notes:
pytest keeps one module per basename.)
"""

from __future__ import annotations

import types

import pytest

from crude.lang import Program
from crude.lang.values import HostHandle, List, Number, String
from crude.ui import UIContext, RecordingRenderer


def as_py(value):
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


def stage():
    """A fresh program over a fresh UIContext on a RecordingRenderer."""
    renderer = RecordingRenderer()
    ctx = UIContext(renderer, width=320, height=200)
    program = Program("<sugar-ui>", host=types.SimpleNamespace(ui=ctx))

    def say(source):
        value, error = program.run(source)
        assert error is None, error.as_string()
        return as_py(value)

    return types.SimpleNamespace(program=program, ctx=ctx, renderer=renderer,
                                 say=say)


@pytest.fixture
def panel():
    yield stage()


#: One of everything the block offers, in the plan's shape ...
BLOCK = """
VAR v = PANEL("deck")
PLACE(v, 8, 32, 304, 160)
VAR bump = METER("beat", 0, 1, 0)
VAR shown = NUMBER("shown", 20, 300, 120)
NODE_ADD(v, shown)
PLACE(shown, 8, 60, 120, 24)
WIDGET bpm = SLIDER("BPM", 20, 300, 120)
    AT 8 8 SIZE 200 24
    ADD v
    LINK_VALUE shown
    ON CHANGE FUNC(val) -> SET(bump, "value", val / 300)
END
"""

#: ... and the same front panel spelled one Tier A call per line.
TWIN = """
VAR v = PANEL("deck")
PLACE(v, 8, 32, 304, 160)
VAR bump = METER("beat", 0, 1, 0)
VAR shown = NUMBER("shown", 20, 300, 120)
NODE_ADD(v, shown)
PLACE(shown, 8, 60, 120, 24)
VAR bpm = SLIDER("BPM", 20, 300, 120)
PLACE(bpm, 8, 8, 200, 24)
NODE_ADD(v, bpm)
LINK_VALUE(bpm, shown)
ON_CHANGE(bpm, FUNC(val) -> SET(bump, "value", val / 300))
"""


def test_a_widget_block_builds_the_tree_the_tier_a_lines_build():
    sugar, twin = stage(), stage()
    sugar.say(BLOCK)
    twin.say(TWIN)
    sugar.ctx.layout()
    twin.ctx.layout()

    assert [w.path() for w in sugar.ctx.root.children] == \
        [w.path() for w in twin.ctx.root.children]
    bpm = sugar.say("bpm")
    assert bpm.path() == twin.say("bpm").path() == "root/deck/BPM"
    assert bpm.screen_box == twin.say("bpm").screen_box


def test_a_widget_block_draws_the_frame_the_tier_a_lines_draw():
    """The same tree in, the same ops out - the recorder is the proof."""
    sugar, twin = stage(), stage()
    sugar.say(BLOCK)
    twin.say(TWIN)
    sugar.ctx.frame(dt=0.0)
    twin.ctx.frame(dt=0.0)

    # a DrawOp is a frozen (name, params, clip) record, so the frames compare whole
    assert sugar.renderer.ops == twin.renderer.ops


def test_the_handlers_a_block_registers_fire_like_the_twin_s(panel):
    panel.say(BLOCK)
    panel.say('SET(bpm, "value", 150)')
    assert panel.say('GET(bump, "value")') == 0.5


def test_the_on_line_fires_the_handler_it_replaces(panel):
    panel.say(
        'VAR tg = TOGGLE("Run")\n'
        'VAR led = METER("beat", 0, 1, 0)\n'
        'ON tg CHANGE FUNC(v) -> SET(led, "value", v)\n'
    )
    panel.say('SET(tg, "value", 1)')
    assert panel.say('GET(led, "value")') == 1.0


def test_a_frame_handler_registered_in_a_block_ticks_like_the_twin():
    sugar, twin = stage(), stage()
    sugar.say(
        'VAR n = NUMBER("ticks", 0, 1000, 0)\n'
        "WIDGET t = TEXT(\"t\")\n    ON FRAME "
        "FUNC(dt) -> SET(n, \"value\", GET(n, \"value\") + 1)\nEND\n"
    )
    twin.say(
        'VAR n = NUMBER("ticks", 0, 1000, 0)\n'
        'VAR t = TEXT("t")\n'
        "ON_FRAME(FUNC(dt) -> SET(n, \"value\", GET(n, \"value\") + 1))\n"
    )
    for _ in range(2):
        sugar.ctx.frame(dt=0.016)
        twin.ctx.frame(dt=0.016)

    assert sugar.say('GET(n, "value")') == twin.say('GET(n, "value")') == 2.0


def test_a_state_row_mirrored_by_a_block_moves_its_target(panel):
    panel.say(
        'VAR a = TOGGLE("a")\nVAR b = TOGGLE("b")\n'
        'WIDGET w = TOGGLE("w")\n    LINK_STATE "value" a\nEND\n'
        'LINK_STATE(b, "value", a)\n'
    )
    panel.say('SET(a, "value", 1)')
    assert panel.say('GET(w, "value")') == 1.0
    assert panel.say('GET(b, "value")') == 1.0
