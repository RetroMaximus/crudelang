"""What a load is going to do, what it did, and how the user hears about it.

The pipeline (:mod:`crude.project.loader`, next door) does not print anything. It
works through a :class:`LoadPlan` - the fifteen weighted phases the manifest
promised - and tells a *reporter* about each step. That split is what makes the
same loading sequence usable in three very different places:

* a person at a terminal, who wants one line per phase and a reason when it stops;
* a machine, which wants JSONL it can parse without regexes;
* a window, which wants a splash drawn between steps.

Every number a reporter shows comes from the plan rather than from elapsed time,
so a run on CI and a run on a Pi report the same percentage for the same work:
the weights are the project's statement of what is expensive, and they add up to
100 for the full pipeline (:data:`PHASES`).

Nothing here imports libpd, an audio device or GLFW. The splash reporter takes a
canvas object from the caller and only ever calls ``draw(frame)`` on it, so it is
testable against a list before there is a renderer to test it against.
"""

from __future__ import annotations

import contextlib
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence, TextIO, Tuple

from ..errors import CancelError, CrudeError, LoadError

#: how wide a phase name is in a console line (``resources.validate`` is longest)
NAME_WIDTH = 18
#: the indent of a detail line: everything before a step's own name
INDENT = " " * 7


# --------------------------------------------------------------------------
# the phases
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadStep:
    """One phase: a name the reporters print, and the share of the work it is worth."""

    name: str
    #: an integer weight, relative to the other steps of the plan
    weight: int
    #: what to call this phase in prose, for a person rather than a log
    label: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "weight": self.weight, "label": self.label}

    def __str__(self) -> str:
        return self.name


#: The fixed fifteen phases, in the order the pipeline runs them (weights 100).
#:
#: The weights are not decoration. ``images`` is 18 because decoding a texture
#: atlas is the slowest thing a project does, and ``manifest`` is 2 because it is
#: one small file; a person watching a progress bar should feel the bar slow down
#: exactly where the work is.
PHASES: Tuple[LoadStep, ...] = (
    LoadStep("manifest", 2, "the manifest"),
    LoadStep("environment", 2, "the folders and the log"),
    LoadStep("resources.validate", 8, "the declared files"),
    LoadStep("audio.device", 8, "the audio device"),
    LoadStep("pd.core", 5, "Pure Data"),
    LoadStep("pd.build", 8, "building the patches"),
    LoadStep("pd.patches", 12, "the patches"),
    LoadStep("pd.arrays", 10, "the arrays"),
    LoadStep("presets", 4, "the presets"),
    LoadStep("images", 18, "the images"),
    LoadStep("sounds", 13, "the sounds"),
    LoadStep("serial", 2, "the serial links"),
    LoadStep("midi", 2, "the MIDI ports"),
    LoadStep("script.compile", 3, "the entry script"),
    LoadStep("runtime.activate", 3, "the runtime"),
)

#: every phase name, in order - what ``crude check --json`` lists for a step
PHASE_NAMES: Tuple[str, ...] = tuple(step.name for step in PHASES)

#: the phases of ``crude check``: everything that can be answered without a
#: device, a display or libpd, plus the script parse that needs none of them.
CHECK_PHASES: Tuple[str, ...] = (
    "manifest",
    "environment",
    "resources.validate",
    "script.compile",
)


def check_steps() -> Tuple[LoadStep, ...]:
    """The :data:`PHASES` entries ``crude check`` runs, in the same order."""
    return tuple(step for step in PHASES if step.name in CHECK_PHASES)


# --------------------------------------------------------------------------
# a plan: the steps that will run, and what each is worth so far
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadPlan:
    """The steps of one run, with the arithmetic the reporters need.

    A plan is normally the full :data:`PHASES` (which sum to 100) or the check
    subset. Percentages are always *relative to this plan*, so a four-step check
    finishes at 100% too, and a reporter never has to know which kind it is on.
    """

    steps: Tuple[LoadStep, ...] = PHASES
    #: the project's display name, for a header or a splash title
    label: str = ""
    #: the runtime mode the manifest asked for (``terminal``/``headless``/``window``)
    mode: str = "window"

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))
        seen: List[str] = []
        for step in self.steps:
            if not isinstance(step, LoadStep):
                raise ValueError(f"a plan step must be a LoadStep, not {step!r}")
            if not step.weight:
                raise ValueError(f"plan step '{step.name}' needs a weight")
            if step.name in seen:
                raise ValueError(f"plan step '{step.name}' appears twice")
            seen.append(step.name)
        if not self.steps:
            raise ValueError("a plan with no steps cannot report a percentage")

    # -- shape ----------------------------------------------------------
    @property
    def names(self) -> Tuple[str, ...]:
        return tuple(step.name for step in self.steps)

    @property
    def total(self) -> int:
        return sum(step.weight for step in self.steps)

    def find(self, name: str) -> Optional[LoadStep]:
        """The step called ``name``, or ``None`` - the pipeline asks before it trusts."""
        for step in self.steps:
            if step.name == name:
                return step
        return None

    def step(self, name: str) -> LoadStep:
        """The step called ``name``, refusing if the plan does not have it."""
        found = self.find(name)
        if found is None:
            raise ValueError(
                f"this plan has no step '{name}' (it has: {', '.join(self.names)})"
            )
        return found

    def subset(self, *names: str) -> "LoadPlan":
        """A new plan with only ``names``, in this plan's order, weights renormalised
        by being measured against the new total."""
        wanted = set(names)
        unknown = sorted(wanted - set(self.names))
        if unknown:
            raise ValueError(
                f"no such step{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}"
            )
        return LoadPlan(
            tuple(step for step in self.steps if step.name in wanted),
            label=self.label,
            mode=self.mode,
        )

    # -- arithmetic -----------------------------------------------------
    def weight(self, name: str) -> int:
        return self.step(name).weight

    def percent_before(self, name: str) -> float:
        """0..100 of the plan done *before* ``name`` runs."""
        return 100.0 * self._cumulative(self.step(name).name) / self.total

    def percent_after(self, name: str) -> float:
        """0..100 once ``name`` is finished."""
        step = self.step(name)
        return 100.0 * (self._cumulative(step.name) + step.weight) / self.total

    def _cumulative(self, name: str) -> int:
        total = 0
        for step in self.steps:
            if step.name == name:
                return total
            total += step.weight
        raise ValueError(f"no such step: {name}")

    def percent(self, name: str, fraction: float = 1.0) -> float:
        """Where the bar stands when ``name`` is ``fraction`` of the way through."""
        step = self.step(name)
        clamp = max(0.0, min(1.0, float(fraction)))
        base = 100.0 * self._cumulative(step.name) / self.total
        return base + (100.0 * step.weight / self.total) * clamp

    # -- telling someone -------------------------------------------------
    def describe(self) -> List[str]:
        """One line per step: `` 1. manifest           2%   the manifest``."""
        width = len(str(len(self.steps)))
        lines = []
        for number, step in enumerate(self.steps, start=1):
            lines.append(
                f"{number:>{width}}. {step.name:<{NAME_WIDTH}} "
                f"{step.weight:>3}%   {step.label}".rstrip()
            )
        return lines

    def as_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "mode": self.mode,
            "total": self.total,
            "steps": [step.as_dict() for step in self.steps],
        }

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self):
        return iter(self.steps)

    def __contains__(self, name: object) -> bool:
        return any(step.name == name for step in self.steps)

    def __str__(self) -> str:
        return f"LoadPlan {self.label or '?'} ({len(self.steps)} steps, {self.mode})"


def full_plan(label: str = "", mode: str = "window") -> LoadPlan:
    """The plan for ``crude run``: all fifteen phases."""
    return LoadPlan(PHASES, label=label, mode=mode)


def check_plan(label: str = "", mode: str = "terminal") -> LoadPlan:
    """The plan for ``crude check``: the four phases that need no hardware."""
    return LoadPlan(check_steps(), label=label, mode=mode)


# --------------------------------------------------------------------------
# what happened
# --------------------------------------------------------------------------


@dataclass
class StepRecord:
    """One step as it was run: its details, how far it got, and what stopped it."""

    step: LoadStep
    #: 0..1 of *this step*; a step that reports progress sets it, the rest end at 1
    fraction: float = 0.0
    #: what the step said while it ran, in order
    details: List[str] = field(default_factory=list)
    #: the exception that ended the run, if one did
    error: Optional[BaseException] = None
    started: bool = False
    done: bool = False

    @property
    def name(self) -> str:
        return self.step.name

    @property
    def ok(self) -> bool:
        return self.done and self.error is None

    @property
    def cancelled(self) -> bool:
        return isinstance(self.error, CancelError)

    @property
    def message(self) -> str:
        """The error as a person should read it, without a class name in the way."""
        if self.error is None:
            return ""
        return str(self.error) or type(self.error).__name__

    @property
    def summary(self) -> str:
        """The one line that stands for this step: its error, or its first detail."""
        if self.error is not None:
            return self.message
        for entry in self.details:
            if entry:
                return entry
        return self.step.label

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "weight": self.step.weight,
            "label": self.step.label,
            "fraction": round(self.fraction, 4),
            "details": list(self.details),
            "started": self.started,
            "done": self.done,
            "ok": self.ok,
            "error": self.message or None,
            "cancelled": self.cancelled,
        }

    def __str__(self) -> str:
        state = "ok" if self.ok else ("failed" if self.error else "pending")
        return f"StepRecord {self.name} ({state})"


class LoadReport:
    """Every step of a plan, as it was asked to run."""

    def __init__(self, plan: LoadPlan):
        self.plan = plan
        #: records in plan order; a dict keyed by name as well, for the CLI
        self.records: List[StepRecord] = [StepRecord(step) for step in plan.steps]
        self._by_name: Dict[str, StepRecord] = {r.name: r for r in self.records}

    # -- lookup ---------------------------------------------------------
    def __getitem__(self, name: str) -> StepRecord:
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"this report has no step '{name}'") from None

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self):
        return iter(self.records)

    @property
    def names(self) -> Tuple[str, ...]:
        return self.plan.names

    @property
    def ran(self) -> List[StepRecord]:
        """The steps that were started, in order."""
        return [record for record in self.records if record.started]

    @property
    def done(self) -> List[StepRecord]:
        return [record for record in self.records if record.done]

    @property
    def failed(self) -> Optional[StepRecord]:
        """The step that ended the run, or ``None`` when the load succeeded."""
        for record in self.records:
            if record.error is not None:
                return record
        return None

    @property
    def ok(self) -> bool:
        return self.failed is None and all(record.done for record in self.records)

    @property
    def percent(self) -> float:
        """0..100 of the plan, counting a half-finished step as half a step.

        Only steps that were started count, so a run that stopped at phase 4 does
        not report the phases it never reached, and a step that stopped part way
        through is worth only the part that happened.
        """
        reached = 0.0
        for record in self.records:
            if not record.started:
                continue
            reached += record.step.weight * (1.0 if record.ok else record.fraction)
        return 100.0 * reached / self.plan.total

    @property
    def failed_name(self) -> str:
        record = self.failed
        return record.name if record else ""

    @property
    def error(self) -> Optional[BaseException]:
        record = self.failed
        return record.error if record else None

    # -- wording --------------------------------------------------------
    def summary(self) -> str:
        """One line a CLI can print at the end, with the percentage it reached."""
        count = len(self.plan)
        finished = len([record for record in self.records if record.ok])
        if self.ok:
            return f"loaded {count} of {count} steps ({self.percent:.0f}%)"
        record = self.failed
        if record is None:
            # a run that was cut short without any phase failing: nothing broke,
            # the plan simply did not get to the end of itself
            return (f"stopped after {finished} of {count} steps "
                    f"({self.percent:.0f}%); {count - finished} never ran")
        where = record.name
        stopped = "cancelled at" if record.cancelled else "failed at"
        return f"{stopped} {where} after {finished} of {count} steps " \
            f"({self.percent:.0f}%)"

    def lines(self) -> List[str]:
        """One ``status name  message`` line per step that was started."""
        lines = []
        for record in self.records:
            if not record.started:
                continue
            state = "ok  " if record.ok else ("stop" if record.cancelled else "FAIL")
            lines.append(f"{state} {record.name:<{NAME_WIDTH}} {record.summary}".rstrip())
        return lines

    def as_dict(self) -> Dict[str, Any]:
        record = self.failed
        return {
            "project": self.plan.label,
            "mode": self.plan.mode,
            "ok": self.ok,
            "percent": round(self.percent, 2),
            "failed": self.failed_name or None,
            "cancelled": bool(record is not None and record.cancelled),
            "error": None if record is None else record.message,
            "summary": self.summary(),
            "steps": [entry.as_dict() for entry in self.records],
        }

    def to_json(self, indent: int = 0) -> str:
        return json.dumps(self.as_dict(), indent=indent or None, sort_keys=True)

    def __str__(self) -> str:
        return f"LoadReport {self.summary()}"


# --------------------------------------------------------------------------
# cancellation
# --------------------------------------------------------------------------


class CancelToken:
    """A flag the pipeline checks between steps.

    Loading is cooperative: a phase already inside libpd or a texture decode runs
    to its end, and the next step refuses to start. That is deliberate - the
    alternative is unwinding a half-opened device from an interrupt - and it is
    why the token carries a reason: the report says what stopped it.
    """

    def __init__(self) -> None:
        self._reason = ""

    @property
    def cancelled(self) -> bool:
        return bool(self._reason)

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self, reason: str = "the load was cancelled") -> None:
        """Ask for the run to stop; the first reason asked for is the one kept."""
        if not self._reason:
            self._reason = reason

    def raise_if_cancelled(self) -> None:
        if self._reason:
            raise CancelError(self._reason)

    def __bool__(self) -> bool:
        return self.cancelled

    def __str__(self) -> str:
        return f"CancelToken {'cancelled: ' + self._reason if self._reason else 'armed'}"


# --------------------------------------------------------------------------
# reporters
# --------------------------------------------------------------------------

AUTO = "auto"
CONSOLE = "console"
JSON = "json"
NONE = "none"
SPLASH = "splash"

#: the values ``loading.progress`` and ``--progress`` accept; the manifest checks
#: them against ``PROGRESS_MODES``, and a test pins that the two lists agree
SPECS: Tuple[str, ...] = (AUTO, CONSOLE, JSON, NONE, SPLASH)


def _percent_text(value: float) -> str:
    """A percentage as three characters, so a column of them lines up."""
    return f"{round(value):>3}"


class Reporter:
    """The protocol every reporter implements, as no-ops.

    A reporter is told, never asked: the pipeline calls :meth:`start` once, then
    ``step_begin`` / ``step_detail`` / ``step_progress`` / ``step_end`` per phase,
    then :meth:`finish` once - including when the run stopped early. Subclasses
    override only what they can show, so the null reporter is one line of code and
    a JSON reporter never has to wonder whether a step was cancelled.
    """

    def start(self, plan: LoadPlan) -> None:
        """The plan that is about to run, so a reporter can say how far to go."""
        return None

    def step_begin(self, step: LoadStep) -> None:
        return None

    def step_detail(self, step: LoadStep, text: str) -> None:
        """Something worth naming inside this step: a file, a device, a patch."""
        return None

    def step_progress(self, step: LoadStep, fraction: float) -> None:
        """``fraction`` of *this step*, 0..1; only slow steps bother to report it."""
        return None

    def step_end(self, step: LoadStep, record: StepRecord) -> None:
        return None

    def finish(self, report: LoadReport) -> None:
        return None

    def __str__(self) -> str:
        return f"{type(self).__name__}()"


class NullReporter(Reporter):
    """Silence: for tests, for a library caller, and for ``--progress none``."""


class ConsoleReporter(Reporter):
    """One line per phase, and the details it mentioned underneath it.

    A step is reported when it *ends*, so a line that appears means the line is
    true: nothing is printed for work that has not happened, and a stalled phase
    shows no output at all rather than a percentage that never moves.
    """

    def __init__(self, stream: Optional[TextIO] = None, *, show_plan: bool = False):
        #: anything with ``write``; ``None`` means the terminal it is running on
        self.stream = stream if stream is not None else sys.stdout
        self.show_plan = show_plan
        self.plan: Optional[LoadPlan] = None
        #: every line written, in order - how the tests read what a person saw
        self.lines: List[str] = []
        self._pending: List[str] = []

    # -- output ----------------------------------------------------------
    def write(self, text: str) -> None:
        self.lines.append(text)
        self.stream.write(text + "\n")
        # a person watching a load wants the line the moment it is true
        flush = getattr(self.stream, "flush", None)
        if flush is not None:
            flush()

    def _at(self, step: LoadStep, record: Optional[StepRecord] = None) -> float:
        """The percentage this step's line should carry."""
        if self.plan is None:
            return 0.0
        fraction = 1.0 if record is None or record.ok else record.fraction
        return self.plan.percent(step.name, fraction)

    # -- the protocol ----------------------------------------------------
    def start(self, plan: LoadPlan) -> None:
        self.plan = plan
        if not self.show_plan:
            return
        title = plan.label or "crude project"
        self.write(f"{title} ({plan.mode}) - {len(plan)} steps, {plan.total} weight")
        for line in plan.describe():
            self.write(f"{INDENT}{line}")

    def step_begin(self, step: LoadStep) -> None:
        self._pending = []

    def step_detail(self, step: LoadStep, text: str) -> None:
        self._pending.append(str(text))

    def step_end(self, step: LoadStep, record: StepRecord) -> None:
        state = "ok" if record.ok else ("stop" if record.cancelled else "FAIL")
        message = record.message if record.error is not None else step.label
        self.write(
            f"[{_percent_text(self._at(step, record))}%] "
            f"{step.name:<{NAME_WIDTH}} {state:<4} {message}".rstrip()
        )
        for detail in self._pending:
            self.write(f"{INDENT}{detail}")
        self._pending = []

    def finish(self, report: LoadReport) -> None:
        if self.plan is None:
            self.plan = report.plan
        # details of a step that never ended still belong to the user
        for detail in self._pending:
            self.write(f"{INDENT}{detail}")
        self._pending = []
        self.write(report.summary())


class JsonReporter(Reporter):
    """One JSON object per line (JSONL): the same events, for a machine.

    Every event carries the percentage as well as the step, so a reader can drive
    a bar without knowing the weights, and the last line is the whole
    :meth:`LoadReport.as_dict` - a summary either way the run went.
    """

    def __init__(self, stream: Optional[TextIO] = None):
        self.stream = stream if stream is not None else sys.stdout
        self.plan: Optional[LoadPlan] = None
        #: the documents emitted, in order, as objects (a test reads these)
        self.events: List[Dict[str, Any]] = []

    def emit(self, document: Dict[str, Any]) -> None:
        self.events.append(document)
        self.stream.write(json.dumps(document, sort_keys=True, default=str) + "\n")
        flush = getattr(self.stream, "flush", None)
        if flush is not None:
            flush()

    def _at(self, step: LoadStep, record: Optional[StepRecord] = None) -> float:
        if self.plan is None:
            return 0.0
        fraction = 1.0 if record is None or record.ok else record.fraction
        return self.plan.percent(step.name, fraction)

    def start(self, plan: LoadPlan) -> None:
        self.plan = plan
        document = {"event": "start"}
        document.update(plan.as_dict())
        self.emit(document)

    def step_begin(self, step: LoadStep) -> None:
        self.emit({
            "event": "step_begin",
            "step": step.name,
            "weight": step.weight,
            "label": step.label,
            "percent": round(self.plan.percent_before(step.name), 2)
            if self.plan else 0.0,
        })

    def step_detail(self, step: LoadStep, text: str) -> None:
        self.emit({"event": "detail", "step": step.name, "text": str(text)})

    def step_progress(self, step: LoadStep, fraction: float) -> None:
        self.emit({
            "event": "progress",
            "step": step.name,
            "fraction": round(max(0.0, min(1.0, float(fraction))), 4),
            "percent": round(self.plan.percent(step.name, fraction), 2)
            if self.plan else 0.0,
        })

    def step_end(self, step: LoadStep, record: StepRecord) -> None:
        self.emit({
            "event": "step_end",
            "step": step.name,
            "ok": record.ok,
            "cancelled": record.cancelled,
            "error": record.message or None,
            "details": list(record.details),
            "percent": round(self._at(step, record), 2),
        })

    def finish(self, report: LoadReport) -> None:
        if self.plan is None:
            self.plan = report.plan
        document = {"event": "finish"}
        document.update(report.as_dict())
        self.emit(document)


# --------------------------------------------------------------------------
# the splash
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SplashFrame:
    """Everything a splash needs to draw, in one immutable value.

    The window stage (S6) gets its own renderer; keeping the frame here means the
    reporter's tests can assert on a list of these without a GL context, and the
    renderer only has to turn a frame into triangles.
    """

    #: ``start`` / ``step_begin`` / ``detail`` / ``progress`` / ``step_end`` / ``finish``
    event: str
    project: str = ""
    mode: str = ""
    step: str = ""
    label: str = ""
    #: 0..100 of the whole plan
    percent: float = 0.0
    #: what the current step has said so far
    details: Tuple[str, ...] = ()
    #: the plan's own lines, on ``start``
    steps: Tuple[str, ...] = ()
    done: bool = False
    failed: bool = False
    #: the summary line, on ``finish``
    message: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event,
            "project": self.project,
            "mode": self.mode,
            "step": self.step,
            "label": self.label,
            "percent": round(self.percent, 2),
            "details": list(self.details),
            "steps": list(self.steps),
            "done": self.done,
            "failed": self.failed,
            "message": self.message,
        }

    def __str__(self) -> str:
        return f"SplashFrame {self.event} {self.percent:>3.0f}% {self.step or self.project}"


class SplashReporter(Reporter):
    """Progress drawn between steps, on the thread that owns the window.

    There is no loader thread: the pipeline runs on the main thread and the splash
    is repainted from the reporter, which is why a step boundary is a frame
    boundary. That keeps loading deterministic - the same project always draws the
    same frames in the same order - and makes this class testable with a canvas
    that is a list.

    A canvas is anything with ``draw(frame)``. If it raises - no display, no GL,
    a window that failed to open - the reporter says so once through the
    :class:`ConsoleReporter` it was given and continues there, because losing the
    progress report is not a reason to lose the load.
    """

    def __init__(self, canvas: Any, *, fallback: Optional[Reporter] = None):
        if canvas is None:
            raise ValueError(
                "a splash reporter needs something to draw on; ask for the console "
                "reporter instead"
            )
        self.canvas = canvas
        self.fallback = fallback if fallback is not None else ConsoleReporter()
        self.plan: Optional[LoadPlan] = None
        #: the frames the canvas accepted, in order
        self.frames: List[SplashFrame] = []
        #: why the canvas was given up on, or ``""`` while it is still drawing
        self.canvas_error = ""
        self._pending: List[str] = []
        self._started = False

    @property
    def degraded(self) -> bool:
        """Has the canvas been given up on?"""
        return bool(self.canvas_error)

    # -- drawing ----------------------------------------------------------
    def _paint(self, frame: SplashFrame) -> bool:
        if self.degraded:
            return False
        try:
            self.canvas.draw(frame)
        except Exception as exc:  # a renderer must never be the reason a load fails
            self.canvas_error = str(exc) or type(exc).__name__
            return False
        self.frames.append(frame)
        return True

    def _console(self) -> Reporter:
        """The fallback, started with the plan it missed."""
        if not self._started:
            self._started = True
            if self.plan is not None:
                self.fallback.start(self.plan)
        return self.fallback

    def _at(self, step: LoadStep, fraction: float = 1.0) -> float:
        return self.plan.percent(step.name, fraction) if self.plan else 0.0

    def _frame(self, event: str, *, step: str = "", label: str = "",
               percent: float = 0.0, message: str = "", done: bool = False,
               failed: bool = False, steps: Tuple[str, ...] = ()) -> SplashFrame:
        return SplashFrame(
            event=event,
            project=self.plan.label if self.plan else "",
            mode=self.plan.mode if self.plan else "",
            step=step,
            label=label,
            percent=percent,
            details=tuple(self._pending),
            steps=steps,
            done=done,
            failed=failed,
            message=message,
        )

    # -- the protocol -----------------------------------------------------
    def start(self, plan: LoadPlan) -> None:
        self.plan = plan
        frame = SplashFrame(
            event="start", project=plan.label, mode=plan.mode,
            steps=tuple(plan.describe()),
        )
        if not self._paint(frame):
            self._started = False
            self.fallback.start(plan)
            self._started = True

    def step_begin(self, step: LoadStep) -> None:
        self._pending = []
        frame = self._frame("step_begin", step=step.name, label=step.label,
                            percent=self._at(step, 0.0))
        if not self._paint(frame):
            self._console().step_begin(step)

    def step_detail(self, step: LoadStep, text: str) -> None:
        self._pending.append(str(text))
        frame = self._frame("detail", step=step.name, label=step.label,
                            percent=self._at(step, 0.0))
        if not self._paint(frame):
            self._console().step_detail(step, text)

    def step_progress(self, step: LoadStep, fraction: float) -> None:
        part = max(0.0, min(1.0, float(fraction)))
        frame = self._frame("progress", step=step.name, label=step.label,
                            percent=self._at(step, part))
        if not self._paint(frame):
            self._console().step_progress(step, part)

    def step_end(self, step: LoadStep, record: StepRecord) -> None:
        percent = self._at(step, 1.0 if record.ok else record.fraction)
        frame = self._frame("step_end", step=step.name, label=step.label,
                            percent=percent, message=record.message,
                            done=True, failed=record.error is not None)
        if not self._paint(frame):
            self._console().step_end(step, record)

    def finish(self, report: LoadReport) -> None:
        frame = self._frame("finish", percent=report.percent,
                            message=report.summary(), done=True,
                            failed=not report.ok)
        if not self._paint(frame):
            self._console().finish(report)
        # the window is not ours to close: the splash stays until the runtime says
        # otherwise, which is how a finished bar and an opening window overlap


def make_reporter(spec: str = AUTO, *, mode: str = "window", canvas: Any = None,
                  stream: Optional[TextIO] = None,
                  show_plan: bool = False) -> Reporter:
    """The reporter ``loading.progress`` (or ``--progress``) asks for.

    ``auto`` is the manifest's answer and the interesting one: a window project
    gets the splash - as soon as there is a window to draw on - and anything else
    gets the terminal, which is also what a Pi over SSH wants.
    """
    chosen = (spec or AUTO).strip().lower()
    if chosen not in SPECS:
        raise ValueError(
            f"there is no progress mode '{chosen}' (known: {', '.join(SPECS)})"
        )
    if chosen == NONE:
        return NullReporter()
    if chosen == JSON:
        return JsonReporter(stream)
    if chosen == CONSOLE:
        return ConsoleReporter(stream, show_plan=show_plan)
    if chosen == SPLASH:
        if canvas is None:
            return ConsoleReporter(stream, show_plan=show_plan)
        return SplashReporter(canvas, fallback=ConsoleReporter(stream))
    # auto
    if (mode or "").strip().lower() == "window" and canvas is not None:
        return SplashReporter(canvas, fallback=ConsoleReporter(stream))
    return ConsoleReporter(stream, show_plan=show_plan)


# --------------------------------------------------------------------------
# the object a pipeline actually holds
# --------------------------------------------------------------------------


def _as_failure(step: str, error: BaseException) -> BaseException:
    """Keep a crude error's own exit code; describe anything else as a load failure.

    ``ManifestError`` (2), a load phase's own error (3) and a script problem (4)
    already know how the process should end, so the tracker must not re-label them
    - but a raw ``OSError`` from inside a phase says nothing about where it came
    from, and :class:`LoadError` is what names the step.
    """
    if isinstance(error, KeyboardInterrupt):
        return CancelError("stopped by the user")
    if isinstance(error, CrudeError):
        return error
    return LoadError(step, error)


class LoadTracker:
    """A plan, a reporter and a report, already wired together.

    The pipeline's whole relationship with progress reporting is here: say what
    phase is starting, mention what it found, and let :meth:`step` mark the end.
    The tracker refuses the mistakes a hand-written pipeline makes - starting a
    phase the plan does not have, two phases at once, forgetting to report a
    failure - so those bugs surface in tests rather than in a half-loaded project.
    """

    def __init__(self, plan: LoadPlan, reporter: Optional[Reporter] = None,
                 *, token: Optional[CancelToken] = None):
        self.plan = plan
        self.reporter = reporter if reporter is not None else NullReporter()
        self.report = LoadReport(plan)
        self.token = token if token is not None else CancelToken()
        self.started = False
        self.finished = False
        self._current: Optional[StepRecord] = None

    # -- setup ----------------------------------------------------------
    def start(self) -> "LoadTracker":
        self.reporter.start(self.plan)
        self.started = True
        return self

    def record(self, name: str) -> StepRecord:
        return self.report[name]

    @property
    def percent(self) -> float:
        return self.report.percent

    @property
    def running(self) -> str:
        """The phase in flight, or an empty string between phases."""
        current = self._current
        return "" if current is None or current.done else current.name

    # -- the phases -----------------------------------------------------
    def begin(self, name: str) -> StepRecord:
        step = self.plan.step(name)
        record = self.report[name]
        if self._current is not None and not self._current.done:
            raise RuntimeError(
                f"'{self._current.name}' has not finished, so '{name}' cannot start; "
                f"close a step with complete() or fail()"
            )
        if self.token.cancelled:
            # the stop was asked for, so this phase is reported as reached and not run
            record.started = True
            record.error = CancelError(self.token.reason)
            record.done = True
            self._current = None
            self.reporter.step_begin(step)
            self.reporter.step_end(step, record)
            raise CancelError(self.token.reason)
        record.started = True
        record.fraction = 0.0
        self._current = record
        self.reporter.step_begin(step)
        return record

    def detail(self, name: str, text: str) -> None:
        step = self.plan.step(name)
        message = str(text)
        if not message:
            return
        self.report[name].details.append(message)
        self.reporter.step_detail(step, message)

    def details(self, name: str, texts: Sequence[str]) -> None:
        """Several details at once, which is how a phase reports a list of files."""
        for text in texts:
            self.detail(name, text)

    def progress(self, name: str, fraction: float) -> None:
        step = self.plan.step(name)
        record = self.report[name]
        record.fraction = max(0.0, min(1.0, float(fraction)))
        self.reporter.step_progress(step, record.fraction)

    def complete(self, name: str) -> StepRecord:
        step = self.plan.step(name)
        record = self.report[name]
        if not record.started:
            raise RuntimeError(f"'{name}' was never begun")
        if record.done:
            return record
        record.fraction = 1.0
        record.done = True
        self._current = None
        self.reporter.step_end(step, record)
        return record

    def fail(self, name: str, error: BaseException) -> StepRecord:
        step = self.plan.step(name)
        record = self.report[name]
        if not record.started:
            record.started = True
            self.reporter.step_begin(step)
        if record.error is None:
            record.error = _as_failure(name, error)
        record.done = True
        self._current = None
        self.reporter.step_end(step, record)
        return record

    @contextlib.contextmanager
    def step(self, name: str) -> "Iterator[StepRecord]":
        """``with tracker.step("pd.core"):`` - the phase, reported either way.

        A cancellation checked at the boundary happens *before* the body runs, so
        the phase is still named in the report and the body never touches a
        half-opened subsystem.

        What leaves the block is what the phase is *called*: a phase that hit a
        raw ``OSError`` reports :class:`LoadError` naming that phase, because
        "[Errno 2] no such file" on its own does not say which step found it.
        """
        record = self.begin(name)
        try:
            yield record
        except BaseException as exc:
            if not record.done:
                self.fail(name, exc)
            failure = record.error if record.error is not None else exc
            if failure is exc:
                raise
            raise failure from exc
        self.complete(name)

    # -- the end --------------------------------------------------------
    def finish(self) -> LoadReport:
        """Hand the report to the reporter, once, whatever happened before it."""
        if self.finished:
            return self.report
        self.finished = True
        current = self._current
        if current is not None and not current.done:
            self.fail(current.name, CancelError("the load did not reach the end"))
        self.reporter.finish(self.report)
        return self.report

    def __str__(self) -> str:
        return (f"LoadTracker {self.plan.label or '?'} ({self.plan.mode}) via "
                f"{type(self.reporter).__name__} at {self.percent:.0f}%")
