"""The headless renderer: the same calls, the same rules, nothing kept.

The plan's headless parity rule says a widget tree exists and paints in every
mode, so a script never has to ask which mode it is in. That only holds if the
renderer a headless run gets accepts exactly what the window one does - so this
is mostly a test that the null renderer is *not* a stub: it enforces the frame,
it clips, it measures, and it counts what it was told in the same units.
"""

from __future__ import annotations

import pytest

from crude.ui import RecordingRenderer, UIError
from crude.ui.null_renderer import NullRenderer
from ui_helpers import KNOB_OPS, draw_a_knob


def a_recorder() -> RecordingRenderer:
    """A recorder on the same surface the null renderer was given."""
    renderer = RecordingRenderer()
    renderer.begin(320, 200, 640, 400)
    return renderer


def a_null() -> NullRenderer:
    renderer = NullRenderer()
    renderer.begin(320, 200, 640, 400)
    return renderer


# -- what it tallies -------------------------------------------------------


def test_every_call_is_counted_and_nothing_is_kept(quiet):
    draw_a_knob(quiet)
    assert quiet.total() == 4
    assert quiet.by_name() == {name: 1 for name in KNOB_OPS}
    assert not hasattr(quiet, "ops"), "a headless run must not grow a frame list"


def test_the_count_of_one_kind_is_answered_even_for_a_kind_it_never_heard_of(quiet):
    quiet.circle(1, 1, 2)
    assert quiet.count("circle") == 1
    assert quiet.count("text") == 0
    assert quiet.count() == 1, "no name means every op, not none of them"


def test_the_frames_it_saw_are_counted_as_they_were_told(quiet):
    quiet.end()
    quiet.begin(320, 200, 640, 400)
    quiet.rect(0, 0, 4, 4)
    quiet.end()
    assert (quiet.begins, quiet.ends, quiet.frame_count) == (2, 2, 2)


def test_an_open_clip_is_counted_and_does_not_stay_open(quiet):
    with quiet.clipped(0, 0, 10, 10):
        quiet.rect(0, 0, 20, 20)
    assert quiet.clips == 1
    assert quiet.clip is None


def test_a_frame_does_not_leave_a_clip_behind_for_the_next_one(quiet):
    quiet.push_clip(0, 0, 10, 10)
    quiet.end()
    assert quiet.clip is None
    quiet.begin(320, 200)
    assert quiet.clip is None


def test_pixels_cannot_be_read_from_a_run_that_sees_nothing(quiet):
    assert quiet.read_pixels(0, 0, 8, 8) is None
    assert quiet.reads == 1


def test_resetting_the_tallies_leaves_the_textures_a_project_loaded_alone(quiet):
    key = quiet.texture("assets/knob_atlas.png")
    quiet.rect(0, 0, 1, 1)
    quiet.reset()
    assert quiet.total() == 0
    assert key in quiet.texture_keys()


def test_measuring_answers_the_same_question_a_layout_would_ask(quiet):
    assert quiet.measure("BPM", 14) == a_recorder().measure("BPM", 14)


def test_the_report_a_headless_run_prints_says_so(quiet):
    draw_a_knob(quiet)
    report = quiet.stats()
    assert report["renderer"] == "null"
    assert report["total"] == 4
    assert report["ops"] == 4
    assert str(quiet) == "NullRenderer 4 op(s) in 0 frame(s)"


# -- the rules still apply -------------------------------------------------


def test_a_headless_run_refuses_a_draw_with_no_frame_open():
    with pytest.raises(UIError, match="outside a frame"):
        NullRenderer().rect(0, 0, 10, 10)


def test_the_arguments_are_normalised_by_the_same_rules_as_everywhere_else(quiet):
    # it throws the op away, but it still had to agree the call was legal
    quiet.rect(0, 0, -10, -10, radius=99)
    quiet.arc(0, 0, 5, 1, 0, 1, caps="flat")
    quiet.text(0, 0, "440", 12, align="centre")
    assert quiet.total() == 3


def test_a_bad_colour_fails_a_headless_run_exactly_as_it_fails_a_window(quiet):
    with pytest.raises(UIError, match="not a colour"):
        quiet.rect(0, 0, 10, 10, color="#nope")
    assert quiet.total() == 0, "a refused call is not a call that happened"


def test_nothing_is_held_between_frames_however_long_a_run_goes(quiet):
    for _ in range(200):
        quiet.end()
        quiet.begin(320, 200, 640, 400)
        draw_a_knob(quiet)
    assert quiet.frame_count == 200
    assert quiet.total() == 200 * len(KNOB_OPS)
    # the tallies are four names and a few numbers, not two hundred frames
    assert len(quiet.by_name()) == len(KNOB_OPS)


# -- parity ----------------------------------------------------------------


@pytest.mark.parametrize("calls", [
    draw_a_knob,
    lambda r: (r.clear(), r.rect(0, 0, 20, 20, radius=4), r.line((0, 0), (5, 5), 2)),
    lambda r: (r.polyline([(0, 0), (4, 4), (8, 0)], closed=True),
               r.polygon([(0, 0), (4, 4), (8, 0)]),
               r.atlas(0, 0, 12, 12, "glyphs", (0.0, 0.0, 0.25, 0.5))),
    lambda r: (r.circle(5, 5, 4), r.arc(5, 5, 4, 2, 0, 3),
               r.quad((0, 0), (1, 0), (1, 1), (0, 1))),
])
def test_the_same_widget_draws_the_same_number_of_ops_in_every_mode(calls):
    """The parity the plan asks for: no mode branch, because nothing differs."""
    recorder = a_recorder()
    null = a_null()
    calls(recorder)
    calls(null)
    assert null.total() == len(recorder.ops)
    assert null.by_name() == {name: recorder.names().count(name)
                              for name in sorted(set(recorder.names()))}


def test_the_two_renderers_agree_about_which_calls_are_illegal():
    recorder = a_recorder()
    null = a_null()
    for bad in (lambda r: r.rect(0, 0, 1, 1, color=object()),
                lambda r: r.arc(0, 0, 1, 1, 0, 1, caps="pointy"),
                lambda r: r.pop_clip(),
                lambda r: r.quad((0, 0, 0), (1, 0), (1, 1), (0, 1))):
        with pytest.raises(UIError):
            bad(recorder)
        with pytest.raises(UIError):
            bad(null)
    assert recorder.ops == []
