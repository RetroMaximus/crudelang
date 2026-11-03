"""The load plan, the report it produces, and the four ways to hear about it.

Progress reporting is arithmetic and formatting, so there is nothing here but
numbers and strings: the percentages are the manifest's weights, not elapsed
time, which is what lets a test pin the exact text a person sees. The splash
reporter is tested against a canvas that is a list, because the real GL splash is
the next stage's job.
"""

from __future__ import annotations

import io
import json

import pytest

from crude.errors import (
    CancelError,
    LoadError,
    ManifestError,
    ResourceError,
    EXIT_LOAD,
    EXIT_MANIFEST,
)
from crude.project import (
    AUTO,
    CHECK_PHASES,
    CONSOLE,
    JSON,
    NONE,
    PHASES,
    PHASE_NAMES,
    PROGRESS_MODES,
    SPECS,
    SPLASH,
    CancelToken,
    ConsoleReporter,
    JsonReporter,
    LoadPlan,
    LoadReport,
    LoadStep,
    LoadTracker,
    NullReporter,
    Reporter,
    SplashFrame,
    SplashReporter,
    StepRecord,
    check_plan,
    check_steps,
    full_plan,
    make_reporter,
)

#: the percentage each phase reaches when it finishes, in order
AFTER = [2, 4, 12, 20, 25, 33, 45, 55, 59, 77, 90, 92, 94, 97, 100]


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class RecordingReporter(Reporter):
    """Every protocol call, as a tuple, so a test can read the sequence."""

    def __init__(self):
        self.calls = []

    def start(self, plan):
        self.calls.append(("start", plan.label, len(plan)))

    def step_begin(self, step):
        self.calls.append(("begin", step.name))

    def step_detail(self, step, text):
        self.calls.append(("detail", step.name, text))

    def step_progress(self, step, fraction):
        self.calls.append(("progress", step.name, round(fraction, 4)))

    def step_end(self, step, record):
        self.calls.append(("end", step.name, record.ok, round(record.fraction, 4)))

    def finish(self, report):
        self.calls.append(("finish", report.ok, round(report.percent, 2)))

    def __str__(self):
        return "RecordingReporter()"

    @property
    def names(self):
        return [call[1] for call in self.calls if len(call) > 1]

    def count(self, kind):
        return len([call for call in self.calls if call[0] == kind])


class FakeCanvas:
    """A splash canvas that is a list, optionally broken from the third draw on."""

    def __init__(self, *, fail_from: int = None, error: Exception = None):
        self.frames = []
        self.fail_from = fail_from
        self.error = error or RuntimeError("no GL context")

    def draw(self, frame):
        if self.fail_from is not None and len(self.frames) >= self.fail_from:
            raise self.error
        self.frames.append(frame)

    @property
    def events(self):
        return [frame.event for frame in self.frames]

    def percent_at(self, event):
        for frame in self.frames:
            if frame.event == event:
                return frame.percent
        raise AssertionError(f"no {event} frame was drawn")


def run_all(tracker, *, details=False):
    """Walk a tracker through every phase of its plan, and return the report."""
    tracker.start()
    for name in tracker.plan.names:
        with tracker.step(name):
            if details:
                tracker.detail(name, f"one {name}")
    return tracker.finish()


# --------------------------------------------------------------------------
# the phases
# --------------------------------------------------------------------------


def test_the_pipeline_has_the_fifteen_phases_in_the_order_promised():
    assert PHASE_NAMES == (
        "manifest", "environment", "resources.validate", "audio.device",
        "pd.core", "pd.build", "pd.patches", "pd.arrays", "presets", "images",
        "sounds", "serial", "midi", "script.compile", "runtime.activate",
    )
    assert len(PHASES) == 15


def test_the_weights_add_up_to_exactly_one_hundred_percent():
    assert sum(step.weight for step in PHASES) == 100
    assert full_plan().total == 100


def test_no_phase_is_worth_nothing_and_every_one_is_named_in_prose():
    for step in PHASES:
        assert step.weight > 0, step
        assert step.label, step
        assert str(step) == step.name  # a step prints as its name, in a log


def test_the_slow_work_owns_the_big_weights():
    weights = {step.name: step.weight for step in PHASES}
    assert weights["images"] == 18
    assert weights["sounds"] == 13
    assert weights["pd.patches"] == 12
    assert weights["manifest"] == 2
    assert weights["environment"] == 2


def test_check_is_the_four_phases_that_need_no_hardware():
    assert CHECK_PHASES == ("manifest", "environment", "resources.validate",
                            "script.compile")
    assert tuple(step.name for step in check_steps()) == CHECK_PHASES
    assert tuple(step.weight for step in check_steps()) == (2, 2, 8, 3)


def test_the_progress_names_match_the_ones_the_manifest_accepts():
    assert set(SPECS) == set(PROGRESS_MODES)
    assert SPECS == (AUTO, CONSOLE, JSON, NONE, SPLASH)


def test_a_step_carries_its_own_description():
    step = PHASES[0]
    assert step.as_dict() == {"name": "manifest", "weight": 2,
                              "label": "the manifest"}
    assert str(step) == "manifest"
    assert step == LoadStep("manifest", 2, "the manifest")


# --------------------------------------------------------------------------
# a plan
# --------------------------------------------------------------------------


@pytest.fixture
def plan():
    return full_plan(label="fm-synth", mode="window")


def test_a_plan_holds_the_phases_and_what_they_are_worth(plan):
    assert plan.names == PHASE_NAMES
    assert len(plan) == 15
    assert list(iter(plan)) == list(PHASES)
    assert "pd.core" in plan
    assert "not.a.phase" not in plan
    assert plan.weight("pd.core") == 5
    assert str(plan) == "LoadPlan fm-synth (15 steps, window)"


def test_finding_a_step_by_name_says_nothing_when_it_is_not_there(plan):
    assert plan.find("images").weight == 18
    assert plan.find("videos") is None


def test_asking_for_a_step_that_is_not_planned_lists_what_is(plan):
    with pytest.raises(ValueError) as caught:
        plan.step("videos")

    message = str(caught.value)
    assert "no step 'videos'" in message
    assert "it has: manifest, environment" in message


@pytest.mark.parametrize("steps,contains", [
    ((LoadStep("a", 1), LoadStep("a", 2)), "appears twice"),
    ((LoadStep("a", 0),), "needs a weight"),
    ((LoadStep("a", 1), "b"), "must be a LoadStep"),
    ((), "no steps"),
])
def test_a_plan_that_cannot_report_a_percentage_is_refused(steps, contains):
    with pytest.raises(ValueError, match=contains):
        LoadPlan(steps)


def test_each_phase_finishes_at_the_percent_it_should(plan):
    reached = [plan.percent_after(name) for name in PHASE_NAMES]
    assert reached == [float(value) for value in AFTER]


def test_a_phase_starts_where_the_one_before_it_finished(plan):
    assert plan.percent_before("manifest") == 0
    for name, expected in zip(PHASE_NAMES[1:], AFTER[:-1]):
        assert plan.percent_before(name) == float(expected)


def test_half_a_step_is_worth_half_its_weight(plan):
    # images is worth 18 of 100 and starts at 59
    assert plan.percent("images", 0.5) == pytest.approx(68.0)
    assert plan.percent("images", 0.0) == 59.0
    assert plan.percent("images", 1.0) == 77.0


def test_a_step_cannot_report_past_itself(plan):
    assert plan.percent("manifest", 7.0) == 2.0
    assert plan.percent("manifest", -2.0) == 0.0


def test_a_subset_is_measured_against_itself_so_check_ends_at_a_hundred():
    subset = check_plan(label="fm-synth")
    assert subset.names == CHECK_PHASES
    assert subset.total == 15
    assert subset.percent_after("manifest") == pytest.approx(100 * 2 / 15)
    assert subset.percent_after("script.compile") == 100


def test_taking_a_subset_keeps_who_the_plan_was_for(plan):
    subset = plan.subset("script.compile", "manifest")
    assert subset.names == ("manifest", "script.compile")  # plan order, not asked order
    assert subset.label == "fm-synth"
    assert subset.mode == "window"


def test_asking_a_plan_for_a_phase_it_does_not_run_says_so(plan):
    with pytest.raises(ValueError, match="no such step: videos"):
        plan.subset("videos")
    with pytest.raises(ValueError, match="no such steps: b, videos"):
        plan.subset("videos", "b")


def test_a_plan_describes_itself_as_a_numbered_list(plan):
    lines = plan.describe()
    assert len(lines) == 15
    assert lines[0].startswith(" 1. manifest")
    assert lines[0].endswith("the manifest")
    # number, name, weight, label - and the weights line up in a column
    assert lines[0].split() == ["1.", "manifest", "2%", "the", "manifest"]
    assert lines[9].split() == ["10.", "images", "18%", "the", "images"]
    # the names all start in the same column, which is the point of the padding
    assert lines[0].index("manifest") == lines[1].index("environment")


def test_the_plan_list_a_check_prints_is_short_and_numbered_too():
    lines = check_plan(label="fm-synth").describe()

    assert [line.split()[1] for line in lines] == list(CHECK_PHASES)
    assert lines[0].startswith("1. manifest")  # one digit, so no padding needed


def test_a_plan_is_a_document_for_the_json_reporter(plan):
    document = plan.as_dict()
    assert document["label"] == "fm-synth"
    assert document["mode"] == "window"
    assert document["total"] == 100
    assert document["steps"][0] == {"name": "manifest", "weight": 2,
                                    "label": "the manifest"}


def test_a_check_plan_is_a_terminal_plan_by_default():
    subset = check_plan()
    assert subset.mode == "terminal"
    assert subset.label == ""
    assert len(subset) == 4


# --------------------------------------------------------------------------
# a step record and a report
# --------------------------------------------------------------------------


@pytest.fixture
def record():
    return StepRecord(PHASES[3])


def test_a_record_starts_empty_and_means_it(record):
    assert record.name == "audio.device"
    assert record.started is False
    assert record.ok is False
    assert record.error is None
    assert record.message == ""
    assert record.summary == "the audio device"  # the label stands in
    assert str(record) == "StepRecord audio.device (pending)"


def test_a_record_that_finished_is_the_only_kind_that_is_ok(record):
    record.started = True
    record.done = True
    record.fraction = 1.0

    assert record.ok is True
    assert record.summary == "the audio device"
    assert str(record) == "StepRecord audio.device (ok)"


def test_a_failure_loses_its_class_name_but_keeps_its_text(record):
    record.started = True
    record.done = True
    record.error = ResourceError("")

    assert record.ok is False
    assert record.message == "ResourceError"
    assert record.summary == "ResourceError"
    assert "(failed)" in str(record)


def test_a_cancelled_record_says_who_stopped_it(record):
    record.error = CancelError("stopped by the user")

    assert record.cancelled is True
    assert record.summary == "stopped by the user"
    assert record.as_dict()["cancelled"] is True


def test_a_record_is_a_document_with_the_same_numbers_as_the_line(record):
    record.details.append("output: default")
    document = record.as_dict()

    assert document["name"] == "audio.device"
    assert document["weight"] == 8
    assert document["details"] == ["output: default"]
    assert document["ok"] is False
    assert document["error"] is None


@pytest.fixture
def report(plan):
    return LoadReport(plan)


def test_a_fresh_report_has_one_record_per_step_in_order(report, plan):
    assert len(report) == 15
    assert [entry.name for entry in report] == list(plan.names)
    assert report.percent == 0
    assert report.ok is False
    assert report.failed is None
    assert report.names == plan.names


def test_a_report_answers_for_a_step_by_name_and_refuses_any_other(report):
    assert report["pd.core"].step.weight == 5
    with pytest.raises(KeyError, match="no step 'videos'"):
        report["videos"]
    assert "pd.core" in report


def test_a_run_that_stopped_early_is_worth_only_the_part_that_happened(report):
    report["manifest"].started = report["manifest"].done = True
    report["manifest"].fraction = 1.0
    report["environment"].started = True
    report["environment"].fraction = 0.5

    assert report.percent == pytest.approx(3.0)
    assert [entry.name for entry in report.ran] == ["manifest", "environment"]
    assert [entry.name for entry in report.done] == ["manifest"]
    assert report.ok is False


def test_a_step_that_failed_after_half_its_work_does_not_count_as_finished(report):
    report["manifest"].started = report["manifest"].done = True
    report["environment"].started = True
    report["environment"].done = True
    report["environment"].error = LoadError("environment", OSError("disk gone"))
    report["environment"].fraction = 0.5

    assert report.percent == pytest.approx(3.0)
    assert report.failed_name == "environment"
    assert report.failed.ok is False


def test_a_report_that_never_failed_and_never_finished_is_not_ok(report):
    for entry in report:
        entry.started = entry.done = True
        entry.fraction = 1.0

    assert report.ok is True
    assert report.percent == 100


def test_the_summary_says_how_far_the_run_got(report):
    assert report.summary() == "stopped after 0 of 15 steps (0%); 15 never ran"

    for entry in report:
        entry.started = entry.done = True
        entry.fraction = 1.0
    assert report.summary() == "loaded 15 of 15 steps (100%)"


def test_a_run_that_ran_out_of_steps_without_failing_says_so(report):
    entry = report["manifest"]
    entry.started = entry.done = True
    entry.fraction = 1.0

    assert report.summary() == "stopped after 1 of 15 steps (2%); 14 never ran"


def test_a_cancellation_is_worded_differently_from_a_failure(report):
    entry = report["pd.core"]
    entry.started = entry.done = True
    entry.error = CancelError("stopped by the user")

    summary = report.summary()
    assert "cancelled at pd.core" in summary
    assert "failed at" not in summary


def test_a_report_lines_up_the_steps_it_ran(report):
    entry = report["manifest"]
    entry.started = entry.done = True
    entry.fraction = 1.0
    entry.details.append("crude/manifest@1")
    bad = report["environment"]
    bad.started = bad.done = True
    bad.error = OSError("read-only")

    lines = report.lines()
    assert len(lines) == 2
    assert lines[0].startswith("ok   manifest")
    assert "crude/manifest@1" in lines[0]
    assert lines[1].startswith("FAIL environment")
    assert "read-only" in lines[1]


def test_a_report_document_is_json_ready(report):
    entry = report["manifest"]
    entry.started = entry.done = True
    entry.fraction = 1.0
    document = report.as_dict()

    assert document["project"] == "fm-synth"
    assert document["mode"] == "window"
    assert document["ok"] is False
    assert document["failed"] is None
    assert document["error"] is None
    assert document["cancelled"] is False
    assert len(document["steps"]) == 15
    assert json.loads(report.to_json())["percent"] == round(report.percent, 2)


def test_a_report_str_is_its_summary(report):
    assert str(report) == f"LoadReport {report.summary()}"


# --------------------------------------------------------------------------
# the tracker: a plan, a reporter and a report, wired together
# --------------------------------------------------------------------------


@pytest.fixture
def recorded():
    return RecordingReporter()


@pytest.fixture
def tracker(plan, recorded):
    return LoadTracker(plan, recorded)


def console_line(percent, name, state, message):
    """One console line, as the reporter writes it."""
    return f"[{percent:>3}%] {name:<18} {state:<4} {message}".rstrip()


def test_a_whole_run_reports_every_phase_once_in_order(tracker, recorded):
    report = run_all(tracker)

    kinds = [call[0] for call in recorded.calls]
    assert kinds == ["start"] + ["begin", "end"] * 15 + ["finish"]
    assert [call[1] for call in recorded.calls if call[0] == "begin"] == list(PHASE_NAMES)
    assert report.ok is True
    assert report.percent == 100
    assert recorded.calls[-1] == ("finish", True, 100.0)


def test_a_run_needs_no_start_call_at_all_if_the_pipeline_forgot(recorded, plan):
    tracker = LoadTracker(plan, recorded)
    with tracker.step("manifest"):
        pass

    assert recorded.calls[0] == ("begin", "manifest")


def test_a_phase_that_raises_is_named_in_the_error_and_the_report(tracker, recorded):
    """The pipeline's own shape: walk the phases and stop at the first failure."""
    tracker.start()
    error = None
    for name in PHASE_NAMES:
        try:
            with tracker.step(name):
                if name == "images":
                    raise OSError("pillow cannot read knob_atlas.png")
        except LoadError as exc:
            error = exc
            break
    report = tracker.finish()

    assert error.step == "images"
    assert "knob_atlas.png" in str(error)
    assert error.exit_code == EXIT_LOAD
    assert report.failed_name == "images"
    assert report.percent == 59.0  # up to images, none of it
    assert report.ok is False
    assert [entry.name for entry in report.ran] == list(PHASE_NAMES[:10])
    assert recorded.count("end") == 10
    assert recorded.count("begin") == 10


def test_a_manifest_error_keeps_its_own_exit_code_instead_of_becoming_a_load_error(
        tracker):
    tracker.start()

    with pytest.raises(ManifestError) as caught:
        with tracker.step("manifest"):
            raise ManifestError("there is no id", "id")

    assert caught.value.exit_code == EXIT_MANIFEST
    assert isinstance(tracker.report["manifest"].error, ManifestError)


def test_a_resource_error_is_already_a_load_error_and_is_not_wrapped_twice(tracker):
    tracker.start()

    with pytest.raises(ResourceError):
        with tracker.step("resources.validate"):
            raise ResourceError("'assets/array.txt' contains no numbers")

    record = tracker.report["resources.validate"]
    assert isinstance(record.error, ResourceError)
    assert record.message == "'assets/array.txt' contains no numbers"


def test_a_key_from_the_user_is_a_cancellation_and_not_a_crash(tracker):
    tracker.start()

    with pytest.raises(CancelError) as caught:
        with tracker.step("audio.device"):
            raise KeyboardInterrupt

    assert isinstance(caught.value.__cause__, KeyboardInterrupt)
    record = tracker.report["audio.device"]
    assert record.error is caught.value
    assert record.cancelled is True
    assert record.message == "stopped by the user"


def test_a_cancel_token_stops_the_next_phase_before_its_body_runs(tracker, recorded):
    tracker.start()
    ran = []

    with tracker.step("manifest"):
        ran.append("manifest")
    tracker.token.cancel("the window was closed")

    with pytest.raises(CancelError, match="the window was closed"):
        with tracker.step("environment"):
            ran.append("environment")

    report = tracker.finish()
    assert ran == ["manifest"]
    assert report["environment"].cancelled is True
    assert report["environment"].started is True
    assert report.failed_name == "environment"
    assert report.percent == 2.0
    assert "cancelled at environment" in report.summary()
    assert ("end", "environment", False, 0.0) in recorded.calls


def test_asking_to_cancel_between_phases_is_honoured_at_the_boundary(tracker):
    tracker.start()
    with tracker.step("manifest"):
        tracker.token.cancel()

    assert tracker.running == ""
    with pytest.raises(CancelError, match="the load was cancelled"):
        tracker.begin("pd.core")


def test_the_first_reason_asked_for_is_the_one_kept(tracker):
    tracker.token.cancel("no display")

    tracker.token.cancel("something else")

    assert tracker.token.reason == "no display"
    assert tracker.token.cancelled is True
    assert bool(tracker.token) is True


def test_a_token_is_armed_until_anyone_cancels_it():
    token = CancelToken()

    assert token.cancelled is False
    assert token.reason == ""
    assert bool(token) is False
    assert str(token) == "CancelToken armed"
    token.raise_if_cancelled()  # nothing to raise yet

    token.cancel("done already")
    assert str(token) == "CancelToken cancelled: done already"
    with pytest.raises(CancelError, match="done already"):
        token.raise_if_cancelled()


def test_two_phases_at_once_is_a_bug_in_the_pipeline_not_a_report(tracker):
    tracker.start()
    tracker.begin("pd.core")

    with pytest.raises(RuntimeError, match="'pd.core' has not finished"):
        tracker.begin("pd.patches")


def test_finishing_a_phase_that_never_started_is_refused(tracker):
    with pytest.raises(RuntimeError, match="was never begun"):
        tracker.complete("manifest")


def test_a_phase_that_ends_twice_only_ends_once(tracker, recorded):
    tracker.start()
    tracker.begin("manifest")
    first = tracker.complete("manifest")
    second = tracker.complete("manifest")

    assert first is second
    assert recorded.count("end") == 1


def test_failing_a_phase_that_never_started_still_names_it(tracker, recorded):
    tracker.start()
    record = tracker.fail("presets", OSError("presets is a file"))

    assert record.started is True
    assert record.message.endswith("presets is a file")
    assert recorded.calls[:3] == [("start", "fm-synth", 15),
                                  ("begin", "presets"),
                                  ("end", "presets", False, 0.0)]


def test_a_failure_is_only_recorded_once(tracker):
    tracker.start()
    with tracker.step("manifest"):
        pass
    tracker.begin("environment")
    disk = OSError("disk gone")
    first = tracker.fail("environment", disk)
    again = tracker.fail("environment", ValueError("and then this"))

    # the raw error is described as the step that hit it, and kept as the cause
    assert again.error is first.error
    assert isinstance(again.error, LoadError)
    assert again.error.cause is disk
    assert again.message == "load step 'environment' failed: disk gone"


def test_the_details_a_phase_gives_are_kept_and_told(tracker, recorded):
    tracker.start()
    with tracker.step("resources.validate"):
        tracker.detail("resources.validate", "4 images")
        tracker.detail("resources.validate", "")
        tracker.details("resources.validate", ["1 sound", "1 array"])

    assert tracker.report["resources.validate"].details == [
        "4 images", "1 sound", "1 array"]
    assert [call for call in recorded.calls if call[0] == "detail"] == [
        ("detail", "resources.validate", "4 images"),
        ("detail", "resources.validate", "1 sound"),
        ("detail", "resources.validate", "1 array"),
    ]


def test_progress_inside_a_phase_moves_the_percentage(tracker, recorded):
    tracker.start()
    with tracker.step("images"):
        tracker.progress("images", 0.5)
        # half of an 18% step, and nothing else of the plan ran
        assert tracker.percent == pytest.approx(9.0)
        assert tracker.plan.percent("images", 0.5) == 68.0
        tracker.progress("images", 9.0)  # a silly caller is clamped, not trusted

    assert recorded.calls == [
        ("start", "fm-synth", 15),
        ("begin", "images"), ("progress", "images", 0.5),
        ("progress", "images", 1.0), ("end", "images", True, 1.0),
    ]


def test_a_run_that_ends_while_a_phase_is_open_says_which_one(tracker):
    tracker.start()
    tracker.begin("sounds")

    report = tracker.finish()

    assert report.failed_name == "sounds"
    assert "did not reach the end" in report["sounds"].message
    assert tracker.finished is True


def test_the_report_is_told_once_however_often_the_pipeline_calls_finish(tracker,
                                                                        recorded):
    run_all(tracker)

    again = tracker.finish()
    assert again is tracker.report
    assert recorded.count("finish") == 1


def test_a_tracker_without_a_reporter_still_keeps_the_report(plan):
    tracker = LoadTracker(plan)

    assert isinstance(tracker.reporter, NullReporter)
    report = run_all(tracker)
    assert report.ok is True


def test_a_tracker_knows_what_is_running(tracker):
    assert tracker.running == ""
    tracker.start()
    tracker.begin("pd.build")

    assert tracker.running == "pd.build"
    tracker.complete("pd.build")
    assert tracker.running == ""


def test_str_of_a_tracker_is_a_line_a_log_can_carry(tracker):
    tracker.start()
    with tracker.step("manifest"):
        pass

    assert str(tracker) == "LoadTracker fm-synth (window) via RecordingReporter at 2%"


def test_a_check_run_reports_its_own_four_steps_as_a_whole_run(recorded):
    tracker = LoadTracker(check_plan(label="fm-synth"), recorded)
    report = run_all(tracker)

    assert report.ok is True
    assert report.percent == 100
    assert report.summary() == "loaded 4 of 4 steps (100%)"
    assert [call[1] for call in recorded.calls if call[0] == "begin"] == \
        list(CHECK_PHASES)


# --------------------------------------------------------------------------
# the console reporter
# --------------------------------------------------------------------------


@pytest.fixture
def out():
    return io.StringIO()


@pytest.fixture
def console(out):
    return ConsoleReporter(out)


@pytest.fixture
def console_run(tracker, console, out):
    """A whole successful run, reported to a buffer instead of a terminal."""
    fresh = LoadTracker(tracker.plan, console)
    report = run_all(fresh)
    return report, console, out


def test_a_successful_run_is_one_line_per_phase_and_a_summary(console_run):
    _report, console, out = console_run

    assert len(console.lines) == 16
    assert console.lines[0] == console_line(2, "manifest", "ok", "the manifest")
    assert console.lines[0] == "[  2%] manifest           ok   the manifest"
    assert console.lines[3] == console_line(20, "audio.device", "ok",
                                            "the audio device")
    assert console.lines[14] == console_line(100, "runtime.activate", "ok",
                                             "the runtime")
    assert console.lines[-1] == "loaded 15 of 15 steps (100%)"
    assert out.getvalue().splitlines() == console.lines


def test_the_percent_column_always_lines_up(console_run):
    _report, console, _out = console_run

    assert {line.index("%]") for line in console.lines[:15]} == {4}


def test_a_phase_says_what_it_found_under_itself(tracker, console):
    fresh = LoadTracker(tracker.plan, console)
    fresh.start()
    with fresh.step("resources.validate"):
        fresh.detail("resources.validate", "assets/knob_atlas.png")
        fresh.detail("resources.validate", "assets/array.txt")
    fresh.finish()

    assert console.lines == [
        console_line(12, "resources.validate", "ok", "the declared files"),
        "       assets/knob_atlas.png",
        "       assets/array.txt",
        "stopped after 1 of 15 steps (8%); 14 never ran",
    ]


def test_a_failure_line_carries_the_reason_and_the_run_stops_there(tracker, console):
    fresh = LoadTracker(tracker.plan, console)
    fresh.start()
    for name in ("manifest", "environment", "resources.validate"):
        with fresh.step(name):
            pass
    with pytest.raises(LoadError):
        with fresh.step("audio.device"):
            raise LoadError("audio.device", RuntimeError("no device"))
    fresh.finish()

    assert console.lines[0].startswith("[  2%] manifest")
    assert console.lines[-2] == console_line(12, "audio.device", "FAIL",
                                             "load step 'audio.device' failed: no device")
    assert console.lines[-1] == "failed at audio.device after 3 of 15 steps (12%)"
    assert any("FAIL" in line for line in console.lines)


def test_a_cancellation_is_marked_apart_from_a_failure(tracker, console):
    fresh = LoadTracker(tracker.plan, console)
    fresh.start()
    with fresh.step("manifest"):
        pass
    fresh.token.cancel("stopped by the user")
    with pytest.raises(CancelError):
        fresh.begin("environment")
    fresh.finish()

    assert console.lines[-2] == console_line(2, "environment", "stop",
                                             "stopped by the user")
    assert "cancelled at environment" in console.lines[-1]


def test_a_quiet_reporter_prints_the_plan_first_when_asked(out, plan):
    reporter = ConsoleReporter(out, show_plan=True)
    reporter.start(plan)

    assert reporter.lines[0] == "fm-synth (window) - 15 steps, 100 weight"
    assert reporter.lines[1].strip().split() == ["1.", "manifest", "2%", "the",
                                                 "manifest"]
    assert len(reporter.lines) == 16


def test_a_reporter_with_no_plan_yet_still_finishes_the_run(console, plan):
    report = LoadReport(plan)
    report["manifest"].started = report["manifest"].done = True
    report["manifest"].fraction = 1.0

    console.finish(report)

    assert console.lines == ["stopped after 1 of 15 steps (2%); 14 never ran"]


def test_details_of_a_step_that_never_ended_are_still_shown(tracker, console):
    fresh = LoadTracker(tracker.plan, console)
    fresh.start()
    fresh.begin("presets")
    fresh.detail("presets", "init.json")
    fresh.finish()

    assert console.lines[0].startswith("[ 55%] presets")
    assert "did not reach the end" in console.lines[0]
    assert console.lines[1] == "       init.json"
    assert "cancelled at presets" in console.lines[-1]


def test_a_console_reporter_shows_the_fraction_it_reached_when_a_step_breaks(
        tracker, console):
    fresh = LoadTracker(tracker.plan, console)
    fresh.start()
    with pytest.raises(LoadError):
        with fresh.step("images"):
            fresh.progress("images", 0.5)
            raise OSError("one atlas is broken")
    fresh.finish()

    assert console.lines[-2] == console_line(68, "images", "FAIL",
                                             "load step 'images' failed: "
                                             "one atlas is broken")


# --------------------------------------------------------------------------
# the JSON reporter
# --------------------------------------------------------------------------


@pytest.fixture
def jsonl(out):
    return JsonReporter(out), out


def test_a_json_run_is_one_document_per_event(jsonl, plan):
    reporter, out = jsonl
    tracker = LoadTracker(plan, reporter)
    run_all(tracker)

    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [event["event"] for event in events] == (
        ["start"] + ["step_begin", "step_end"] * 15 + ["finish"]
    )
    assert events[0]["label"] == "fm-synth"
    assert events[0]["total"] == 100
    assert len(events[0]["steps"]) == 15


def test_every_json_event_carries_the_percentage_it_was_at(jsonl, plan):
    reporter, out = jsonl
    tracker = LoadTracker(plan, reporter)
    tracker.start()
    with tracker.step("manifest"):
        tracker.detail("manifest", "crude/manifest@1")
    with tracker.step("environment"):
        tracker.progress("environment", 0.5)
    tracker.finish()

    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [event["event"] for event in events] == [
        "start", "step_begin", "detail", "step_end",
        "step_begin", "progress", "step_end", "finish",
    ]
    # a detail is text, not a measurement, so it carries no percentage
    assert [event["percent"] for event in events if "percent" in event] == [
        0,     # step_begin manifest
        2,     # step_end manifest
        2,     # step_begin environment
        3,     # progress: half of a 2% step
        4,     # step_end environment
        4,     # finish
    ]
    assert events[2] == {"event": "detail", "step": "manifest",
                         "text": "crude/manifest@1"}


def test_the_machine_report_ends_with_the_whole_run(jsonl, plan):
    reporter, out = jsonl
    tracker = LoadTracker(plan, reporter)
    run_all(tracker)

    finish = json.loads(out.getvalue().splitlines()[-1])
    assert finish["event"] == "finish"
    assert finish["ok"] is True
    assert finish["percent"] == 100
    assert finish["failed"] is None
    assert finish["summary"] == "loaded 15 of 15 steps (100%)"
    assert len(finish["steps"]) == 15
    assert finish["steps"][9]["label"] == "the images"


def test_a_failure_is_a_document_a_build_server_can_branch_on(jsonl, plan):
    reporter, out = jsonl
    tracker = LoadTracker(plan, reporter)
    tracker.start()
    for name in ("manifest", "environment", "resources.validate", "audio.device"):
        with tracker.step(name):
            pass
    with pytest.raises(LoadError):
        with tracker.step("pd.core"):
            raise RuntimeError("libpd.dll is not there")
    tracker.finish()

    finish = json.loads(out.getvalue().splitlines()[-1])
    assert finish["ok"] is False
    assert finish["failed"] == "pd.core"
    assert finish["cancelled"] is False
    assert "libpd.dll" in finish["error"]
    assert finish["percent"] == 20
    ends = [event for event in reporter.events if event["event"] == "step_end"]
    assert ends[-1]["ok"] is False
    assert ends[-1]["error"] == "load step 'pd.core' failed: libpd.dll is not there"


def test_a_cancellation_is_marked_in_json_too(jsonl, plan):
    reporter, _out = jsonl
    tracker = LoadTracker(plan, reporter)
    tracker.start()
    tracker.token.cancel("the window was closed")

    with pytest.raises(CancelError):
        tracker.begin("manifest")
    tracker.finish()

    finish = reporter.events[-1]
    assert finish["event"] == "finish"
    assert finish["cancelled"] is True
    assert finish["failed"] == "manifest"


def test_the_json_reporter_keeps_the_documents_as_well_as_writing_them(jsonl, plan):
    reporter, out = jsonl
    reporter.start(plan)
    reporter.step_begin(plan.step("manifest"))

    assert reporter.events[0] == json.loads(out.getvalue().splitlines()[0])
    assert reporter.events[1]["event"] == "step_begin"
    assert reporter.events[1]["step"] == "manifest"


# --------------------------------------------------------------------------
# the splash reporter
# --------------------------------------------------------------------------


def test_a_splash_is_a_frame_at_every_step_boundary(plan):
    canvas = FakeCanvas()
    tracker = LoadTracker(plan, SplashReporter(canvas))
    run_all(tracker)

    assert canvas.events[0] == "start"
    assert canvas.frames[0].steps == tuple(plan.describe())
    assert canvas.events[1] == "step_begin"
    assert canvas.frames[1].step == "manifest"
    assert canvas.frames[1].label == "the manifest"
    assert canvas.frames[1].percent == 0
    assert canvas.frames[-2].event == "step_end"
    assert canvas.frames[-2].step == "runtime.activate"
    assert canvas.frames[-2].percent == 100
    assert canvas.events[-1] == "finish"
    assert canvas.frames[-1].message == "loaded 15 of 15 steps (100%)"
    assert canvas.frames[-1].done is True
    assert canvas.frames[-1].failed is False


def test_a_splash_frame_names_the_project_it_is_loading(plan):
    canvas = FakeCanvas()
    tracker = LoadTracker(plan, SplashReporter(canvas))
    run_all(tracker)

    assert all(frame.project == "fm-synth" for frame in canvas.frames)
    assert all(frame.mode == "window" for frame in canvas.frames)


def test_what_a_phase_found_is_on_every_frame_after_it(plan):
    canvas = FakeCanvas()
    reporter = SplashReporter(canvas)
    tracker = LoadTracker(plan, reporter)
    tracker.start()
    with tracker.step("pd.patches"):
        tracker.detail("pd.patches", "drums.pd")
        tracker.detail("pd.patches", "synth (authored)")

    drawn = [frame for frame in canvas.frames if frame.step == "pd.patches"]
    assert drawn[0].details == ()
    assert drawn[1].details == ("drums.pd",)
    assert drawn[2].details == ("drums.pd", "synth (authored)")


def test_a_failure_is_drawn_rather_than_only_printed(plan):
    canvas = FakeCanvas()
    tracker = LoadTracker(plan, SplashReporter(canvas))
    tracker.start()
    for name in ("manifest", "environment", "resources.validate"):
        with tracker.step(name):
            pass
    with pytest.raises(LoadError):
        with tracker.step("audio.device"):
            raise RuntimeError("no device")
    tracker.finish()

    last = canvas.frames[-1]
    assert last.failed is True
    assert "no device" in canvas.frames[-2].message
    assert last.message == "failed at audio.device after 3 of 15 steps (12%)"
    # the bar stays where the finished work is, not where the broken step hoped to get
    assert canvas.frames[-2].percent == 12


def test_a_splash_that_cannot_draw_hands_over_to_the_console(out, plan):
    canvas = FakeCanvas()
    console = ConsoleReporter(out)
    tracker = LoadTracker(plan, SplashReporter(canvas, fallback=console))
    canvas.error = RuntimeError("no GL context on this machine")
    canvas.fail_from = 0

    report = run_all(tracker)

    assert canvas.frames == []
    assert report.ok is True
    assert console.lines[0] == console_line(2, "manifest", "ok", "the manifest")
    assert console.lines[-1] == "loaded 15 of 15 steps (100%)"


def test_a_splash_that_dies_halfway_does_not_lose_the_rest_of_the_run(out, plan):
    canvas = FakeCanvas(fail_from=2)
    console = ConsoleReporter(out)
    reporter = SplashReporter(canvas, fallback=console)
    tracker = LoadTracker(plan, reporter)
    tracker.start()
    # the start frame and the first step are drawn, then the context is lost
    for name in ("manifest", "environment", "resources.validate"):
        with tracker.step(name):
            pass
    tracker.finish()

    assert reporter.degraded is True
    assert reporter.canvas_error == "no GL context"
    assert len(canvas.frames) == 2
    # the console picked up every event from the failure onwards
    assert console.lines[0] == console_line(2, "manifest", "ok", "the manifest")
    assert console.lines[-1] == "stopped after 3 of 15 steps (12%); 12 never ran"
    assert len([line for line in console.lines if line.startswith("[")]) == 3


def test_a_canvas_is_only_asked_once_after_it_fails(out, plan):
    canvas = FakeCanvas(fail_from=1)
    reporter = SplashReporter(canvas, fallback=ConsoleReporter(out))
    tracker = LoadTracker(plan, reporter)
    tracker.start()
    with tracker.step("manifest"):
        pass
    drawn = len(canvas.frames)
    tracker.finish()

    assert reporter.degraded is True
    assert len(canvas.frames) == drawn  # the canvas is not asked again


def test_a_splash_needs_something_to_draw_on():
    with pytest.raises(ValueError, match="needs something to draw on"):
        SplashReporter(None)


def test_a_frame_is_a_document():
    frame = SplashFrame("step_end", project="fm-synth", step="images",
                        percent=68.0, details=("a.png",), done=True)

    assert frame.as_dict()["details"] == ["a.png"]
    assert frame.as_dict()["percent"] == 68.0
    assert str(frame) == "SplashFrame step_end  68% images"
    assert SplashFrame("start").project == ""


# --------------------------------------------------------------------------
# choosing a reporter
# --------------------------------------------------------------------------


def test_a_window_project_with_a_window_gets_a_splash():
    reporter = make_reporter(AUTO, mode="window", canvas=FakeCanvas())

    assert isinstance(reporter, SplashReporter)
    assert isinstance(reporter.fallback, ConsoleReporter)


def test_a_window_project_before_the_window_exists_is_reported_in_text():
    assert isinstance(make_reporter(AUTO, mode="window"), ConsoleReporter)


@pytest.mark.parametrize("mode", ["terminal", "headless", "window"])
def test_anything_that_is_not_a_window_starts_in_the_terminal(mode):
    reporter = make_reporter(AUTO, mode=mode)

    assert isinstance(reporter, ConsoleReporter)
    assert reporter.show_plan is False


@pytest.mark.parametrize("spec,expected", [
    ("console", ConsoleReporter),
    ("json", JsonReporter),
    ("none", NullReporter),
])
def test_an_explicit_answer_beats_the_mode(spec, expected):
    assert isinstance(make_reporter(spec, mode="window", canvas=FakeCanvas()), expected)


def test_a_splash_asked_for_without_a_window_falls_back_instead_of_failing():
    assert isinstance(make_reporter(SPLASH, mode="window"), ConsoleReporter)


def test_the_names_a_person_types_are_not_case_sensitive():
    assert isinstance(make_reporter(" Console "), ConsoleReporter)
    assert isinstance(make_reporter("", mode="headless"), ConsoleReporter)
    assert isinstance(make_reporter(None, mode="headless"), ConsoleReporter)


def test_an_unknown_progress_name_lists_the_ones_that_work():
    with pytest.raises(ValueError) as caught:
        make_reporter("progress")

    message = str(caught.value)
    assert "no progress mode 'progress'" in message
    assert "auto, console, json, none, splash" in message


def test_the_reporter_a_manifest_chooses_is_the_reporter_that_runs(make, out):
    manifest = make({"loading": {"progress": "json"}})
    reporter = make_reporter(manifest.loading.progress, mode=manifest.mode,
                             stream=out)

    assert isinstance(reporter, JsonReporter)
    assert reporter.stream is out


def test_the_console_a_check_command_wants_shows_the_plan_first(out):
    reporter = make_reporter(CONSOLE, mode="terminal", stream=out, show_plan=True)
    reporter.start(check_plan(label="fm-synth"))

    assert reporter.lines[0] == "fm-synth (terminal) - 4 steps, 15 weight"
    assert [line.split()[1] for line in reporter.lines[1:]] == list(CHECK_PHASES)


def test_the_check_run_a_person_sees_lists_exactly_the_four_steps(out):
    reporter = ConsoleReporter(out)
    tracker = LoadTracker(check_plan(label="fm-synth"), reporter)
    run_all(tracker)

    assert reporter.lines == [
        console_line(13, "manifest", "ok", "the manifest"),
        console_line(27, "environment", "ok", "the folders and the log"),
        console_line(80, "resources.validate", "ok", "the declared files"),
        console_line(100, "script.compile", "ok", "the entry script"),
        "loaded 4 of 4 steps (100%)",
    ]
