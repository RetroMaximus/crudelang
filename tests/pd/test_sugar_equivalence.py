"""S12 at the far end of the pipe: the sugar runs as the calls it replaces.

The AST tests in ``tests/lang/test_sugar.py`` stop at the parse tree; these go
through the real bindings on the fake libpd, so the claim under test is the
strong one - a ``PATCH`` block and the ``PD_*`` script it desugars to leave
behind byte-identical ``.pd`` text, and a ``WIDGET`` block leaves behind the
same box ``PD_EMIT`` writes for the Tier A lines.
"""

from __future__ import annotations

import types

import pytest

from crude.lang import Program
from crude.lang.values import HostHandle, List, Number, String
from crude.pd import FakeLibpd, PdRuntime, PdSession
from crude.ui import NullRenderer, UIContext


def as_py(value):
    """Convert a runtime value into the plainest Python equivalent."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, List):
        return [as_py(element) for element in value.elements]
    if isinstance(value, HostHandle):
        return value.target
    return value


#: The plan's S12 sketch, written in the sugar ...
PATCH_SUGAR = """
PATCH synth
    CANVAS 900 600
    OBJ osc "osc~ 440" AT 40 40
    OBJ amp "*~ 0.3" AT 40 120
    MSG m "440" AT 200 40
    NUM freq AT 200 100
    TABLE wave 1024 AT 500 40
    TEXT "simple tone" AT 40 10
    LINK m -> osc, amp -> OUT
END
"""

#: ... and the same patch spelled one Tier A call per line.
PATCH_TWIN = """
VAR synth = PD_BUILD("synth", 900, 600)
VAR osc = PD_OBJ(synth, "osc~ 440", 40, 40)
VAR amp = PD_OBJ(synth, "*~ 0.3", 40, 120)
VAR m = PD_MSG(synth, "440", 200, 40)
VAR freq = PD_NUM(synth, "freq", 200, 100)
VAR wave = PD_TABLE(synth, "wave", 1024, 500, 40)
PD_TEXT(synth, "simple tone", 40, 10)
PD_LINK(synth, m, osc)
VAR dac = PD_OBJ(synth, "dac~", 0, 0)
PD_LINK(synth, amp, dac)
"""


# -- PATCH ... END on the real bindings ----------------------------------


def test_a_patch_block_and_its_tier_a_twin_dump_the_same_file(eval_pd):
    # the twin's patch id is renamed because one runtime holds one patch per
    # id: the two spellings share a program, so they must not collide on a name
    # (the variable they bind is the same word in both - only the id differs)
    assert as_py(eval_pd(PATCH_SUGAR + "PD_DUMP(synth)")) == \
        as_py(eval_pd(PATCH_TWIN.replace('"synth"', '"twin"', 1)
                     + "PD_DUMP(synth)"))


def test_the_box_handles_a_patch_block_builds_are_the_twin_s(runtime, tmp_path):
    """Not the same words - the same objects: kinds, labels, positions.

    ``PD_INSPECT`` speaks the patch's own id, so the twin gets a second program
    over a second runtime rather than a renamed id whose report would differ.
    """
    first = Program("<sugar-a>", host=types.SimpleNamespace(pd=runtime))
    value, error = first.run(PATCH_SUGAR + "PD_INSPECT(synth)")
    assert error is None, error.as_string()
    sugar = as_py(value)

    home = tmp_path / "twin-inspect"
    home.mkdir()
    session2 = PdSession(FakeLibpd())
    session2.start()
    other = PdRuntime(session2, project_dir=str(home))
    second = Program("<sugar-b>", host=types.SimpleNamespace(pd=other))
    try:
        value, error = second.run(PATCH_TWIN + "PD_INSPECT(synth)")
        assert error is None, error.as_string()
        assert sugar == as_py(value)
    finally:
        session2.shutdown()


def test_a_patch_the_sugar_authored_loads_like_any_other(eval_pd, runtime):
    """The generated text parses, so the sugar's patch is a real one to Pd."""
    value = eval_pd(PATCH_SUGAR + "PD_LOAD(synth)")
    assert as_py(value).id == "synth"
    assert "synth" in runtime.patches


def test_the_sugar_s_in_and_out_are_the_boxes_the_twin_names(eval_pd):
    """``OUT`` is the ``VAR dac = PD_OBJ(p, "dac~", 0, 0)`` the plan draws."""
    sugar = as_py(eval_pd('PATCH p\n    OBJ o "osc~"\n    LINK o -> OUT\nEND\n'
                          "PD_BOXES(p)"))
    twin = as_py(eval_pd('VAR q = PD_BUILD("q")\nVAR o = PD_OBJ(q, "osc~")\n'
                         'VAR dac = PD_OBJ(q, "dac~", 0, 0)\nPD_LINK(q, o, dac)\n'
                         "PD_BOXES(q)"))
    assert sugar == twin


# -- WIDGET ... END, told through the box it twins -------------------------

WIDGET_SUGAR = """
VAR v = PANEL("mix")
PLACE(v, 100, 50, 200, 100)
WIDGET w = SLIDER("BPM", 20, 300, 120)
    SET "send" "$0-bpm"
    ADD v
    AT 12 30 SIZE 80 10
END
VAR p = PD_BUILD("synth")
PD_EMIT(p, w)
PD_DUMP(p)
"""

WIDGET_TWIN = """
VAR v = PANEL("mix")
PLACE(v, 100, 50, 200, 100)
VAR w = SLIDER("BPM", 20, 300, 120)
SET(w, "send", "$0-bpm")
NODE_ADD(v, w)
PLACE(w, 12, 30, 80, 10)
VAR p = PD_BUILD("synth")
PD_EMIT(p, w)
PD_DUMP(p)
"""


@pytest.fixture
def twin_stage(tmp_path):
    """A second :func:`tests/pd/conftest.stage`-shaped harness to run the twin on.

    The two spellings must land on two runtimes, because one runtime keeps one
    patch per id - and the dump is what is compared, not the storage.
    """
    home = tmp_path / "twin"
    home.mkdir()
    session = PdSession(FakeLibpd())
    session.start()
    runtime = PdRuntime(session, project_dir=str(home))
    context = UIContext(NullRenderer(), width=320, height=200)
    program = Program("<pd-ui-twin>",
                      host=types.SimpleNamespace(pd=runtime, ui=context))

    def say(source):
        value, error = program.run(source)
        assert error is None, error.as_string()
        return value

    yield say
    session.shutdown()


def test_a_widget_block_twins_the_same_box_the_tier_a_lines_do(eval_emit,
                                                               twin_stage):
    """The nested child's screen box is absolute, so the twin proves the tree."""
    assert as_py(eval_emit(WIDGET_SUGAR)) == as_py(twin_stage(WIDGET_TWIN))
