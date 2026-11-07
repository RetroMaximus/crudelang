"""The splash drawn on paper: every frame a load report asks for, and no GL.

:class:`~crude.ui.splash.SplashCanvas` is the only piece of the UI that runs
*before* a project exists, so what it has to get right is not beauty but these
four things: the ops are the contract's ops, in an order a person can read back;
a window that has been closed stops being drawn into instead of raising; a
project that names thirty steps or says a great deal cannot push the headline off
the top; and a machine with no display at all can still be told exactly what the
splash would have shown.

The canvas is therefore driven against a
:class:`~crude.ui.recording.RecordingRenderer` and a fake surface that is a
handful of counters. The frames are the reporter's own field list, taken as a
plain object - the canvas never imports the project layer, and neither does this
file except once, at the end, to prove the two vocabularies still agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import pytest

from crude.project.progress import SplashFrame
from crude.ui import RecordingRenderer
from crude.ui.base import UIError
from crude.ui.splash import (BAR_HEIGHT, BAR_WIDTH, DEFAULT_HEIGHT,
                             DEFAULT_WIDTH, MAX_DETAILS, PALETTE, MARGIN,
                             SplashCanvas)


@dataclass(frozen=True)
class Frame:
    """The field vocabulary a splash is allowed to ask a frame for."""

    event: str
    project: str = "metronome"
    mode: str = "window"
    step: str = ""
    label: str = ""
    percent: float = 0.0
    details: Tuple[str, ...] = ()
    steps: Tuple[str, ...] = ()
    done: bool = False
    failed: bool = False
    message: str = ""


class Surface:
    """The little bit of a ``Display`` a canvas is permitted to ask for."""

    def __init__(self, renderer: RecordingRenderer, *, width: int = DEFAULT_WIDTH,
                 height: int = DEFAULT_HEIGHT, alive: bool = True) -> None:
        self.renderer = renderer
        self.width = width
        self.height = height
        self.alive = alive
        self.begins = 0
        self.presents = 0
        self.pumps = 0
        self.titles: list = []
        self.closed = False

    def frame(self) -> None:
        self.begins += 1
        self.renderer.begin(self.width, self.height)

    def present(self) -> int:
        self.presents += 1
        return self.renderer.end()

    def pump(self, *, wait: float = 0.0) -> bool:
        self.pumps += 1
        return self.alive

    def set_title(self, title: str) -> None:
        self.titles.append(title)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def paper() -> RecordingRenderer:
    """A renderer with no surface: the canvas begins its own frames."""
    return RecordingRenderer()


@pytest.fixture
def shown():
    """A canvas on a fake window, and that window, as ``(canvas, surface)``."""
    def build(*, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, alive=True,
              **kwargs) -> Tuple[SplashCanvas, Surface]:
        renderer = RecordingRenderer()
        surface = Surface(renderer, width=width, height=height, alive=alive)
        return SplashCanvas(renderer, surface, **kwargs), surface

    return build


def drawn(canvas: SplashCanvas, *frames: Frame) -> RecordingRenderer:
    """Draw every frame and hand back the renderer they landed on."""
    for frame in frames:
        canvas.draw(frame)
    return canvas.renderer


# -- the shape of one frame --------------------------------------------------

def test_a_frame_is_a_clear_a_heading_a_bar_and_a_number(shown):
    canvas, surface = shown()
    ops = drawn(canvas, Frame("progress", step="audio.device", percent=40.0))
    assert ops.names() == ["clear", "text", "text", "rect", "rect", "text"]
    assert surface.begins == 1
    assert surface.presents == 1


def test_the_whole_frame_is_cleared_first(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("progress"))
    first = ops.first()
    assert first.name == "clear"
    assert str(first.get("color")).startswith(PALETTE["background"])


def test_the_project_is_said_at_the_top_left(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("progress", project="metronome"))
    heading = ops.ops_named("text")[0]
    assert heading.get("text") == "metronome"
    assert (heading.get("x"), heading.get("y")) == (MARGIN, MARGIN)


def test_a_project_that_has_not_told_us_its_name_is_still_a_window(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("progress", project=""))
    assert ops.ops_named("text")[0].get("text") == "crude"


def test_the_step_running_now_is_said_under_the_name(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("progress", step="pd.core", label="Pure Data",
                              mode="window"))
    said = ops.ops_named("text")[1].get("text")
    assert "pd.core" in said
    assert "window" in said


def test_a_frame_with_no_step_says_the_label_instead(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("progress", label="warming up"))
    assert "warming up" in ops.ops_named("text")[1].get("text")


def test_the_window_title_follows_the_project_once(shown):
    canvas, surface = shown()
    canvas.draw(Frame("progress", project="deck"))
    canvas.draw(Frame("progress", project="deck"))
    canvas.draw(Frame("progress", project="other"))
    assert surface.titles == ["deck", "other"]


def test_a_frame_reports_how_many_ops_it_took(shown):
    canvas, _ = shown()
    ops = canvas.draw(Frame("progress", percent=25.0))
    assert ops == len(canvas.renderer.frames[-1])


# -- the bar ----------------------------------------------------------------

def test_an_empty_progress_bar_is_a_track_on_its_own(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("progress", percent=0.0))
    assert ops.count("rect") == 1


def test_the_bar_fills_as_the_plan_goes_on(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("progress", percent=50.0))
    track, fill = ops.ops_named("rect")
    assert fill.get("width") == pytest.approx(track.get("width") * 0.5)


def test_a_finished_bar_is_the_whole_track(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("finish", percent=100.0, done=True))
    track, fill = ops.ops_named("rect")
    assert fill.get("width") == pytest.approx(track.get("width"))


def test_a_percent_that_is_not_a_number_is_not_drawn_outside_the_track(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("progress", percent=900.0))
    track, fill = ops.ops_named("rect")
    assert fill.get("width") <= track.get("width") + 0.001


def test_the_bar_is_centred_on_the_width_it_was_given(shown):
    canvas, _ = shown(width=400)
    ops = drawn(canvas, Frame("progress", percent=30.0))
    track = ops.ops_named("rect")[0]
    assert track.get("width") == pytest.approx(400 * BAR_WIDTH)
    assert track.get("x") == pytest.approx((400 - 400 * BAR_WIDTH) / 2)
    assert track.get("height") == BAR_HEIGHT


def test_the_percent_is_said_out_loud_as_well(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("progress", percent=62.4))
    said = ops.ops_named("text")[-1]
    assert said.get("text").strip() == "62%"
    assert said.get("align") == "right"


def test_a_finished_bar_is_green_and_a_failed_one_is_red(shown):
    canvas, _ = shown()
    ok = drawn(canvas, Frame("finish", percent=100.0, done=True))
    assert str(ok.ops_named("rect")[1].get("color")).startswith(PALETTE["done"])

    canvas2, _ = shown()
    bad = drawn(canvas2, Frame("finish", percent=60.0, done=True, failed=True))
    assert str(bad.ops_named("rect")[1].get("color")).startswith(
        PALETTE["failed"])


def test_a_step_that_ended_in_an_error_is_red_before_the_load_is(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("step_end", percent=30.0, done=True, failed=True))
    assert str(ops.ops_named("rect")[1].get("color")).startswith(
        PALETTE["failed"])


# -- what the steps said ----------------------------------------------------

def test_the_very_first_frame_shows_the_plan(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("start", steps=("manifest", "environment",
                                              "resources", "script.compile")))
    # heading, heading, the bar's number, then the plan in order
    assert ops.texts()[-4:] == ["manifest", "environment", "resources",
                                "script.compile"]
    assert ops.texts()[2] == "  0%"


def test_a_plan_is_not_repeated_on_the_second_frame(shown):
    canvas, _ = shown()
    drawn(canvas, Frame("start", steps=("manifest", "environment")),
          Frame("progress", step="manifest"))
    first, second = canvas.renderer.frames
    # the only difference is the list of steps, which was said once
    assert len(second) == len(first) - 2
    assert [op.name for op in second].count("text") == 3


def test_the_last_few_lines_are_what_fit(shown):
    canvas, _ = shown()
    said = [f"line {index}" for index in range(12)]
    ops = drawn(canvas, Frame("detail", details=tuple(said)))
    assert ops.texts()[3:] == said[-MAX_DETAILS:]
    assert ops.count("text") == 3 + MAX_DETAILS


def test_the_detail_lines_are_cut_to_their_own_box(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("detail", details=("one", "two")))
    for op in ops.ops_named("text")[3:]:
        assert op.clip is not None
        assert op.clip[0] == MARGIN
        assert op.clip[2] <= DEFAULT_WIDTH - 2 * MARGIN
    # the heading and the bar are not cut at all
    assert ops.ops_named("text")[0].clip is None


def test_a_frame_with_nothing_to_report_draws_no_lines(shown):
    canvas, _ = shown()
    ops = drawn(canvas, Frame("progress", step="audio.device"))
    assert len(ops.ops_named("text")) == 3


def test_the_summary_line_only_appears_at_the_end(shown):
    canvas, _ = shown()
    middle = drawn(canvas, Frame("detail", message="half way"))
    assert "half way" not in middle.texts()
    canvas2, _ = shown()
    end = drawn(canvas2, Frame("finish", message="loaded 4 of 4 steps",
                               done=True))
    assert end.texts()[-1] == "loaded 4 of 4 steps"


def test_a_long_name_is_shortened_rather_than_run_off_the_window(shown):
    canvas, _ = shown(width=200)
    ops = drawn(canvas, Frame("progress", project="a" * 400))
    heading = ops.ops_named("text")[0].get("text")
    assert heading.endswith("\u2026")
    assert len(heading) < 400


def test_a_line_in_the_middle_of_the_window_is_shortened_too(shown):
    canvas, _ = shown(width=200)
    ops = drawn(canvas, Frame("detail", details=("word " * 200,)))
    line = ops.texts()[3]
    assert line.endswith("\u2026")
    assert len(line) < 200 * 6


def test_a_window_far_too_small_to_draw_in_is_said_so(shown):
    canvas, _ = shown(width=60, height=40)
    canvas.draw(Frame("detail", details=("a line that will not fit",)))
    # the heading and the bar are the splash; the detail box gave up quietly
    assert canvas.renderer.count("text") == 3


# -- a window that is there, and then is not --------------------------------

def test_the_size_drawn_into_is_the_window_s_not_the_canvas_s(shown):
    canvas, surface = shown(width=480, height=260)
    canvas.draw(Frame("progress"))
    surface.width, surface.height = 640, 400
    canvas.draw(Frame("progress", percent=10.0))
    assert (canvas.width, canvas.height) == (640.0, 400.0)
    assert canvas.renderer.sizes[-1][:2] == (640.0, 400.0)


def test_a_surface_that_stops_answering_is_not_an_error(shown):
    canvas, surface = shown()
    canvas.draw(Frame("progress"))
    surface.alive = False
    assert canvas.draw(Frame("progress", percent=20.0)) == 0
    assert canvas.gone is True
    assert canvas.frames[-1].percent == 0.0


def test_a_canvas_that_has_given_up_draws_nothing_more(shown):
    canvas, surface = shown()
    surface.alive = False
    assert canvas.draw(Frame("progress")) == 0
    assert surface.begins == 0
    assert canvas.renderer.ops == []
    canvas.blank()
    assert canvas.renderer.ops == []


def test_the_window_is_pumped_before_every_frame(shown):
    canvas, surface = shown()
    for percent in (10.0, 20.0, 30.0):
        canvas.draw(Frame("progress", percent=percent))
    assert surface.pumps == 3
    assert surface.begins == 3


def test_a_surface_with_no_pump_is_still_a_surface():
    class Bare:
        def __init__(self, renderer):
            self.renderer = renderer

        def frame(self):
            self.renderer.begin(200, 100)

        def present(self):
            return self.renderer.end()

    renderer = RecordingRenderer()
    canvas = SplashCanvas(renderer, Bare(renderer))
    assert canvas.draw(Frame("progress")) > 0
    assert canvas.gone is False


def test_a_window_that_has_no_size_refuses_to_be_painted(shown):
    canvas, surface = shown(width=0, height=0)
    with pytest.raises(UIError):
        canvas.draw(Frame("progress"))
    # the frame was abandoned, but the window was still given back its buffer
    assert canvas.frames == []


# -- with no window at all ---------------------------------------------------

def test_a_canvas_with_no_surface_begins_its_own_frames(paper):
    canvas = SplashCanvas(paper)
    assert canvas.surface is None
    canvas.draw(Frame("progress", step="manifest", percent=12.5))
    assert paper.sizes == [(DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_WIDTH,
                            DEFAULT_HEIGHT)]
    assert len(paper.frames) == 1
    assert canvas.percent == 12.5


def test_the_size_a_surfaceless_canvas_was_given_is_the_one_it_draws(paper):
    canvas = SplashCanvas(paper, width=320, height=200)
    canvas.draw(Frame("progress"))
    assert paper.sizes[-1][:2] == (320.0, 200.0)
    assert (canvas.width, canvas.height) == (320.0, 200.0)


def test_a_headless_run_can_draw_the_whole_load(paper):
    canvas = SplashCanvas(paper)
    plan = ("manifest", "environment", "resources", "script.compile")
    for index, step in enumerate(plan):
        canvas.draw(Frame("step_begin", step=step,
                          percent=(index + 1) * 25.0))
    canvas.draw(Frame("finish", percent=100.0, done=True, message="all done"))
    assert canvas.events == ("step_begin", "step_begin", "step_begin",
                             "step_begin", "finish")
    assert len(paper.frames) == 5
    assert paper.frames[-1][0].name == "clear"


def test_blank_is_one_empty_frame_for_the_moment_after(paper):
    canvas = SplashCanvas(paper)
    canvas.draw(Frame("finish", percent=100.0, done=True))
    canvas.blank()
    assert [op.name for op in paper.frames[-1]] == ["clear"]


def test_a_canvas_that_gave_up_does_not_blank_either(shown):
    canvas, surface = shown()
    surface.alive = False
    canvas.draw(Frame("progress"))
    canvas.blank()
    assert surface.begins == 0


def test_a_palette_is_a_dict_and_nothing_more(paper):
    dark = dict(PALETTE)
    dark["background"] = "#000000"
    dark["fill"] = "#ffffff"
    canvas = SplashCanvas(paper, palette=dark)
    canvas.draw(Frame("progress", percent=10.0))
    assert str(paper.first().get("color")).startswith("#000000")
    assert str(paper.ops_named("rect")[1].get("color")).startswith("#ffffff")
    # the caller's dict is not the canvas's
    dark["background"] = "#123456"
    assert canvas.palette["background"] == "#000000"


def test_a_palette_that_omits_a_colour_is_refused_at_the_first_frame(paper):
    canvas = SplashCanvas(paper, palette={"background": "#000000"})
    with pytest.raises(KeyError):
        canvas.draw(Frame("progress"))


# -- the view a log gets -----------------------------------------------------

def test_the_canvas_remembers_the_frames_it_drew(shown):
    canvas, _ = shown()
    canvas.draw(Frame("start"))
    canvas.draw(Frame("progress", percent=50.0))
    assert canvas.events == ("start", "progress")
    assert canvas.percent == 50.0


def test_stats_describe_a_splash_that_is_still_going(shown):
    canvas, _ = shown()
    canvas.draw(Frame("progress", percent=33.3))
    report = canvas.stats()
    assert report["canvas"] == "splash"
    assert report["frames"] == 1
    assert report["percent"] == 33.3
    assert report["gone"] is False
    assert report["error"] == ""
    assert report["renderer"]["recorded_frames"] == 1


def test_a_canvas_says_what_it_is_holding(shown):
    canvas, _ = shown(width=320, height=200)
    canvas.draw(Frame("progress", percent=80.0))
    assert "320x200" in str(canvas)
    assert "80%" in str(canvas)
    assert "1 frames" in str(canvas)


# -- the two halves really do speak the same language ------------------------

def test_a_real_splash_frame_needs_nothing_the_canvas_was_not_given(shown):
    canvas, surface = shown()
    frame = SplashFrame(event="step_end", project="metronome", mode="window",
                        step="script.compile", label="entry script",
                        percent=100.0, details=("no errors",), done=True,
                        message="loaded 4 of 4 steps")
    ops = canvas.draw(frame)
    assert ops > 0
    assert surface.begins == 1
    assert canvas.frames == [frame]


def test_every_field_a_canvas_asks_of_a_frame_is_one_a_reporter_fills_in():
    wanted = {"event", "project", "mode", "step", "label", "percent", "details",
              "steps", "done", "failed", "message"}
    assert wanted <= set(SplashFrame.as_dict(SplashFrame("start")))
