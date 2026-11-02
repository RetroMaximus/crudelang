"""The recorder: what a frame looked like, as a list a test can read.

Nothing here needs a display, which is the point - every widget assertion in the
stages after this one is a read of the lists below, so the behaviour worth
pinning is that the recording is faithful: the order is the order it was drawn
in, the clip attached to an op is the one that was open at the time, and a frame
that was ended is a frame that is kept whole.
"""

from __future__ import annotations

import os

import pytest

from crude.ui import DrawOp, RecordingRenderer, texture_key
from ui_helpers import KNOB_OPS, draw_a_knob


# -- what was recorded -----------------------------------------------------


def test_a_frame_of_four_calls_is_recorded_as_four_ops_in_order(recording):
    draw_a_knob(recording)
    assert recording.names() == list(KNOB_OPS)
    assert recording.op_count == 4


def test_an_op_can_be_read_by_name_and_by_position(recording):
    draw_a_knob(recording)
    assert len(recording.ops_named("rect")) == 1
    assert recording.count("circle") == 1
    assert recording.count("no such thing") == 0
    assert recording.first().name == "rect"
    assert recording.last().name == "text"
    assert recording.last("arc").get("radius") == pytest.approx(16.0)
    assert recording.last("nothing") is None
    assert recording.first("nothing") is None


def test_the_values_a_widget_drew_are_read_back_as_they_were_said(recording):
    draw_a_knob(recording, value=0.25)
    assert recording.texts() == ["0.25"]


def test_the_boxes_are_the_layout_a_test_of_placement_compares_against(recording):
    draw_a_knob(recording, x=20, y=30, size=50)
    assert recording.rects() == [(20.0, 30.0, 50.0, 50.0)]


def test_an_atlas_box_counts_as_a_box_too(recording):
    recording.atlas(1, 2, 3, 4, "glyphs")
    assert recording.rects() == [(1.0, 2.0, 3.0, 4.0)]


# -- frames ----------------------------------------------------------------


def test_each_finished_frame_is_kept_apart_from_the_ones_before_it(recording):
    draw_a_knob(recording)
    recording.end()
    recording.begin(320, 200, 640, 400)
    recording.clear()
    recording.end()
    assert [len(ops) for ops in recording.frames] == [4, 1]
    assert recording.frame_ops(0)[0].name == "rect"
    assert recording.frame_ops(1)[0].name == "clear"


def test_the_size_of_every_frame_is_remembered(recording):
    recording.end()
    recording.begin(800, 600, 1600, 1200)
    recording.end()
    assert recording.sizes == [(320.0, 200.0, 640, 400), (800.0, 600.0, 1600, 1200)]


def test_the_recording_answers_the_question_of_one_frame_or_of_the_whole_run(recording):
    draw_a_knob(recording)
    recording.end()
    recording.begin(320, 200, 640, 400)
    draw_a_knob(recording)
    assert len(recording.ops) == 8
    assert str(recording) == "RecordingRenderer 8 op(s) in 1 frame(s)"


def test_resetting_lets_the_next_frame_be_read_on_its_own(recording):
    draw_a_knob(recording)
    recording.end()
    recording.reset()
    assert recording.ops == []
    assert recording.frames == []
    assert recording.sizes == []
    recording.begin(320, 200)
    recording.circle(1, 1, 2)
    assert [op.name for op in recording.ops] == ["circle"]


# -- clipping and order ----------------------------------------------------


def test_a_clip_is_attached_to_what_was_drawn_inside_it_and_only_that(recording):
    recording.rect(0, 0, 10, 10)
    with recording.clipped(5, 5, 20, 20):
        recording.rect(0, 0, 100, 100)
    recording.rect(0, 0, 10, 10)
    clips = [op.clip for op in recording.ops]
    assert clips[0] is None
    assert clips[1] == (5.0, 5.0, 20.0, 20.0)
    assert clips[2] is None


def test_z_order_is_the_order_of_the_list_because_nothing_is_resorted(recording):
    recording.rect(0, 0, 10, 10, color="#ff0000")
    recording.rect(0, 0, 10, 10, color="#0000ff")
    assert [op.get("color").blue for op in recording.ops] == [0.0, 1.0]


# -- textures and readbacks ------------------------------------------------


def test_the_recorder_remembers_the_files_it_was_asked_to_load(recording):
    key = recording.texture("assets/knob_atlas.png")
    assert recording.loaded == [(key, "assets/knob_atlas.png")]
    assert key == texture_key("assets/knob_atlas.png")
    recording.texture("assets/knob_atlas.png")
    assert len(recording.loaded) == 1, "the same file twice is one load"


def test_the_same_path_asked_for_twice_is_still_one_load(recording):
    here = os.path.abspath("assets/x.png")
    assert recording.texture(here) == recording.texture(here)
    assert len(recording.loaded) == 1


def test_an_upload_is_recorded_with_the_shape_it_was_handed(recording):
    class Pixels:
        shape = (16, 64, 4)

    recording.upload_texture("scope", Pixels())
    assert recording.uploaded == [("scope", (64.0, 16.0))]


def test_a_read_is_recorded_and_answered_with_whatever_was_pretended(recording):
    recording.pixels = "pretend pixels"
    assert recording.read_pixels(4, 5, 6, 7, premultiplied=True) == "pretend pixels"
    assert recording.reads == [(4.0, 5.0, 6.0, 7.0, True)]


def test_a_read_of_a_whole_frame_fills_in_the_frames_own_size(recording):
    recording.read_pixels()
    assert recording.reads == [(0.0, 0.0, 320.0, 200.0, False)]


# -- the report ------------------------------------------------------------


def test_what_it_counted_is_reported_apart_from_what_a_gl_renderer_would(recording):
    draw_a_knob(recording)
    report = recording.stats()
    assert report["renderer"] == "recording"
    assert report["ops"] == 4
    assert report["recorded"] == 4
    assert report["dpr"] == 2.0
    assert report["textures"] == 0


def test_an_op_is_a_value_that_compares_equal_to_the_same_draw(recording):
    first = RecordingRenderer()
    first.begin(10, 10)
    second = RecordingRenderer()
    second.begin(10, 10)
    # two ways of saying one colour, because an op is compared by what it means
    first.rect(1, 2, 3, 4, color="#ff0000")
    second.rect(1, 2, 3, 4, color=(1.0, 0.0, 0.0))
    assert first.ops == second.ops
    assert isinstance(first.ops[0], DrawOp)
