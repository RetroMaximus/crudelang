"""The acceptance test for authoring: a patch built by a script, run by real Pd.

Deselected by default; run with ``pytest -m requires_libpd tests/pd``. Nothing
here is about the text format any more - the file has been written, cached and
opened by the time these tests start, and what matters is whether the patch
makes the sound the script described.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from crude.pd.model import Link

#: the repository root, whatever directory pytest was started in
ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.requires_libpd

#: A fixed frequency straight to the DAC: what "the patch works" means here.
TONE = (
    'VAR p = PD_BUILD("tone")\n'
    'VAR osc = PD_OBJ(p, "osc~ 440")\n'
    'VAR dac = PD_OBJ(p, "dac~")\n'
    "PD_LINK(p, osc, dac)\n"
    "PD_LINK(p, osc, dac, 0, 1)\n"
    "PD_LOAD(p)\n"
    "PD_DSP(1)\n"
)

#: The same tone, retuneable through a number box the script authored, with a
#: scoped table so ``$0`` resolution and seeding are checked in one load.
RETUNE = (
    'VAR p = PD_BUILD("retune")\n'
    'VAR osc = PD_OBJ(p, "osc~ 440")\n'
    'VAR dac = PD_OBJ(p, "dac~")\n'
    'VAR freq = PD_NUM(p, "freq")\n'
    'PD_TABLE(p, "wave", 16)\n'
    "PD_LINK(p, freq, osc)\n"
    "PD_LINK(p, osc, dac)\n"
    "PD_LINK(p, osc, dac, 0, 1)\n"
    "PD_LOAD(p)\n"
    "PD_DSP(1)\n"
)


def say(program, source):
    """Run ``source`` and return its value, failing the test on any error."""
    value, error = program.run(source)
    assert error is None, error.as_string()
    return value


def goertzel(samples, frequency, sample_rate) -> float:
    """Energy at one frequency - a DFT bin without computing the whole spectrum."""
    count = len(samples)
    if not count:
        return 0.0
    coeff = 2.0 * math.cos(2.0 * math.pi * frequency / sample_rate)
    previous_1 = previous_2 = 0.0
    for sample in samples:
        current = sample + coeff * previous_1 - previous_2
        previous_2, previous_1 = previous_1, current
    return math.sqrt(
        abs(previous_1 * previous_1 + previous_2 * previous_2
            - coeff * previous_1 * previous_2)
    )


def render(runtime, ticks=64):
    """Render ``ticks`` blocks and keep the channel the patch actually drives."""
    session = runtime.session
    samples = session.process_offline(ticks=ticks)
    left = samples[0::max(session.out_channels, 1)]
    return left, session.sample_rate


# -- the sound ----------------------------------------------------------


def test_an_authored_patch_opens_and_reports_a_dollar_zero(live_runtime):
    runtime, program = live_runtime

    say(program, TONE)

    patch = runtime.patches["tone"]
    assert patch.dollar_zero > 0
    assert patch.name.startswith("tone-")
    assert patch.name.endswith(".pd")


def test_the_authored_patch_makes_a_440_hz_tone(live_runtime):
    runtime, program = live_runtime

    say(program, TONE)

    samples, sample_rate = render(runtime)
    assert max(abs(sample) for sample in samples) > 0.1, "the patch is silent"

    at_440 = goertzel(samples, 440.0, sample_rate)
    for other in (110.0, 220.0, 880.0, 1000.0, 4400.0):
        assert at_440 > 20 * goertzel(samples, other, sample_rate), other


def test_a_receiver_the_script_authored_reaches_the_oscillator(live_runtime):
    runtime, program = live_runtime

    say(program, RETUNE)
    assert say(program, 'PD_EXISTS("freq")').value == 1

    say(program, 'PD_SEND(p, "freq", 440)')
    samples, sample_rate = render(runtime)
    assert goertzel(samples, 440.0, sample_rate) > (
        20 * goertzel(samples, 220.0, sample_rate)
    )

    say(program, 'PD_SEND(p, "freq", 220)')
    samples, sample_rate = render(runtime)
    assert goertzel(samples, 220.0, sample_rate) > (
        20 * goertzel(samples, 440.0, sample_rate)
    )


# -- names, scope and data ----------------------------------------------


def test_a_scoped_table_exists_under_pds_own_name(live_runtime):
    runtime, program = live_runtime

    say(program, RETUNE)
    patch = runtime.patches["retune"]

    # the script says "wave"; Pd only ever hears "<$0>-wave"
    assert int(say(program, 'PD_ARRAY_SIZE(p, "wave")').value) == 16
    assert runtime.session.lib.array_size(f"{patch.dollar_zero}-wave") == 16
    assert runtime.session.lib.array_size("wave") < 0


def test_a_seeded_table_arrives_with_its_data(live_runtime):
    runtime, program = live_runtime

    say(program,
        'VAR p = PD_BUILD("wave")\n'
        'VAR osc = PD_OBJ(p, "osc~ 440")\n'
        'VAR dac = PD_OBJ(p, "dac~")\n'
        'PD_TABLE(p, "wave", 8, 20, 20, [1, 2, 3, 4])\n'
        "PD_LINK(p, osc, dac)\n"
        "PD_LOAD(p)\n"
    )

    values = say(program, 'PD_ARRAY_READ(p, "wave")')
    assert [element.value for element in values.elements] == [
        1.0, 2.0, 3.0, 4.0, 0.0, 0.0, 0.0, 0.0,
    ]

    say(program, 'PD_ARRAY_WRITE(p, "wave", [9], 7)')
    assert say(program, 'AT(PD_ARRAY_READ(p, "wave", 7, 1), 0)').value == 9.0


def test_the_patch_needs_no_receiver_that_is_not_there(live_runtime):
    """Every receiver the authored patch declares can actually be found."""
    runtime, program = live_runtime

    say(program, RETUNE)

    patch = runtime.patches["retune"]
    assert runtime.probe_receivers(patch, runtime.doc("retune")) == []
    assert runtime.exists("freq")


# -- the same script, run for real --------------------------------------


#: A tone the script can turn up and down, with one control that is the twin of a
#: widget rather than a box the script laid out by hand.
EMITTED = (
    'VAR p = PD_BUILD("mix")\n'
    'VAR osc = PD_OBJ(p, "osc~ 440")\n'
    'VAR gain = PD_OBJ(p, "*~")\n'
    'VAR dac = PD_OBJ(p, "dac~")\n'
    "PD_LINK(p, osc, gain)\n"
    "PD_LINK(p, gain, dac)\n"
    "PD_LINK(p, gain, dac, 0, 1)\n"
    'VAR w = SLIDER("level", 0, 1, 1)\nSET(w, "recv", "$0-lvl")\n'
    'PLACE(w, 20, 60, 200, 20)\n'
    "PD_EMIT(p, w)\n"
    'VAR ear = PD_OBJ(p, "r $0-lvl", 20, 120)\n'
    "PD_LINK(p, ear, gain, 0, 1)\n"
    "PD_LOAD(p)\n"
    "PD_DSP(1)\n"
)


def test_the_generated_file_is_an_ordinary_patch(live_runtime):
    """What the script wrote is a patch a person can open in Pd and read back."""
    from crude.pd.reader import read_file

    runtime, program = live_runtime

    say(program, RETUNE)

    doc = read_file(runtime.cache.paths["retune"])
    assert doc.warnings == []
    assert [box.kind for box in doc.boxes] == [
        "obj", "obj", "floatatom", "table",
    ]
    assert doc.table("wave").size == 16
    assert doc.links and set(doc.links) == {
        Link("floatatom1", 0, "obj1", 0),
        Link("obj1", 0, "obj2", 0),
        Link("obj1", 0, "obj2", 1),
    }


def test_an_emitted_slider_is_part_of_a_patch_real_pd_runs(live_emit):
    """The twin is not a comment: Pd opened it, and the patch it is in makes sound."""
    runtime, program, _notes = live_emit

    say(program, EMITTED)

    say(program, 'PD_SEND(p, "$0-lvl", 1)')
    samples, sample_rate = render(runtime)
    assert goertzel(samples, 440.0, sample_rate) > (
        20 * goertzel(samples, 220.0, sample_rate)
    )

    say(program, 'PD_SEND(p, "$0-lvl", 0)')
    samples, _rate = render(runtime)
    assert max(abs(sample) for sample in samples) < 0.01, "still audible"


def test_the_emitted_box_is_written_exactly_as_the_table_laid_it_out(live_emit):
    runtime, program, _notes = live_emit

    say(program, EMITTED)

    with open(runtime.cache.paths["mix"], encoding="utf-8") as handle:
        written = handle.read()
    assert (r"#X obj 20 60 hsl 200 20 0 1 0 0 empty \$0-lvl level -2 -8 0 10 "
            r"#dfdfdf #000000 #000000 1 1;") in written

    # the name the slider listens on is a receiver Pd really has, so the patch
    # needs nothing that is not there
    patch = runtime.patches["mix"]
    assert runtime.probe_receivers(patch, runtime.doc("mix")) == []


#: The shipped example, and the panel its own widgets twin to. The send and
#: receive names are said here rather than read off the widgets, because a GL
#: widget editing a number is not yet a promise about which receiver it belongs
#: to - the script's ``ON_CHANGE`` is, and the script is what says it.
PANEL = (
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
    "PD_LOAD(g)\n"
)


def test_the_metronome_front_panel_reopens_in_real_pd(live_emit):
    """The plan's other half of the GUI line: a dumped panel, opened by Pd.

    Every box here is a control with no ``[r]`` beside it, so the whole patch is
    wired by receive slots alone - and Pd binds those the moment it creates the
    object (``vu_new`` calls ``pd_bind`` on its receive symbol, as a toggle's and
    a slider's ``new`` do), which is the behaviour ``FakeLibpd`` promises and the
    reason the runtime's own probe is allowed to trust it.
    """
    runtime, program, notes = live_emit

    example = (ROOT / "examples" / "metronome" / "main.crude").read_text(
        encoding="utf-8")
    say(program, example)
    say(program, PANEL)

    assert "metro_gui" in runtime.patches
    assert runtime.exists("tick"), "the meter's receive slot is a receiver"
    assert runtime.exists("run") and runtime.exists("bpm")
    assert runtime.probe_receivers(runtime.patches["metro_gui"],
                                   runtime.doc("metro_gui")) == []
    assert [note for note in notes if "not reachable" in note] == []

    # the audio patch and its panel coexist as two canvases in one Pd, which is
    # the arrangement ``--mode windowed`` has to keep working for every project
    assert sorted(runtime.patches) == ["metro", "metro_gui"]
    assert runtime.doc("metro").validate() == []
    assert runtime.doc("metro_gui").validate() == []
