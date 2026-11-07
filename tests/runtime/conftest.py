"""Fixtures for the runtime stage: the loop, its modes, and its shutdown.

S11's loop is the first piece of ``crude`` that runs *time* rather than
describing it, so every fixture here hands the loop something a test can
count: a :class:`~crude.runtime.clock.FakeClock` that moves virtual time on
``frame()``, a :class:`~crude.ui.recording.RecordingRenderer` that remembers
every draw op the plan says a frame emits, a :class:`~crude.audio.null.NullBackend`
that lets the headless tick pull blocks instead of waiting for a thread, and
a fake serial host with a counter for ``dispatch``.

Nothing here opens a device, a window or a real port, and nothing here
``sleep``s: the default marker set has to run on a machine with no driver,
so the tests only ever see fakes unless they are behind one of the four
markers named in ``pytest.ini``.
"""

from __future__ import annotations

import types
from typing import Any, Callable, Dict, List, Optional

import pytest

from crude.audio import AudioRuntime
from crude.audio.null import NullBackend
from crude.pd import FakeLibpd, PdRuntime, PdSession
from crude.runtime import Clock, FakeClock, Runtime
from crude.serial import SerialRuntime
from crude.ui.recording import RecordingRenderer


# -- the clock and the renderers ---------------------------------------

@pytest.fixture
def fake_clock() -> FakeClock:
    """A clock whose seconds only move when a frame is asked for."""
    return FakeClock(target_fps=60)


@pytest.fixture
def recording() -> RecordingRenderer:
    """Every draw op, in order, with the frame boundaries kept."""
    return RecordingRenderer()


# -- a host that looks enough like a project ---------------------------

class RecordingDisplay:
    """A :class:`Display` in shape only: enough for ``pump``/``close``/swap.

    The loop calls ``pump`` before every tick and takes ``closed`` as the
    person's answer to whether the run should keep going. A test that wants
    the loop to stop says so here rather than counting real frames.
    """

    def __init__(self, renderer: Optional[RecordingRenderer] = None,
                 *, width: int = 320, height: int = 200,
                 pumps_until_close: Optional[int] = None) -> None:
        self.renderer = renderer if renderer is not None else RecordingRenderer()
        self.width = width
        self.height = height
        self.closed = False
        self.close_calls = 0
        self.pumps = 0
        self.presents = 0
        self.frames = 0
        self.begins = 0
        self._pumps_until_close = pumps_until_close

    def pump(self, *, wait: float = 0.0) -> bool:
        self.pumps += 1
        if self._pumps_until_close is not None and self.pumps >= self._pumps_until_close:
            self.closed = True
            return False
        return not self.closed

    def frame(self) -> None:
        self.frames += 1
        self.begins += 1
        self.renderer.begin(self.width, self.height)

    def present(self) -> int:
        self.presents += 1
        return self.renderer.end(swap=True)

    def swap(self) -> int:
        return self.renderer.end()

    def set_title(self, title: str) -> None:
        self.title = title

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


@pytest.fixture
def display() -> RecordingDisplay:
    """A window the loop can take over: never closes on its own."""
    return RecordingDisplay()


@pytest.fixture
def make_display():
    """A factory for the same fake, so a test can name its own close rule."""
    def build(**kwargs) -> RecordingDisplay:
        return RecordingDisplay(**kwargs)

    return build


@pytest.fixture
def one_shot_display() -> RecordingDisplay:
    """A window the person closes after one pump, so the loop stops itself."""
    return RecordingDisplay(pumps_until_close=1)


# -- the counters a fake Pd and a fake serial hand back ----------------

class CountingSerial:
    """A serial host whose ``dispatch`` returns the count a test pushed in."""

    def __init__(self, counts: Optional[List[int]] = None) -> None:
        self.name = "counting"
        self.counts = list(counts or [])
        self.calls = 0
        self.notes: List[str] = []

    def dispatch(self) -> int:
        self.calls += 1
        if self.counts:
            return int(self.counts.pop(0))
        return 0

    def close_all(self) -> None:
        self.notes.append("closed")


@pytest.fixture
def counting_serial() -> CountingSerial:
    return CountingSerial()


class CountingPd:
    """A Pd host whose ``dispatch`` returns the count a test queued up."""

    def __init__(self, counts: Optional[List[int]] = None) -> None:
        self.counts = list(counts or [])
        self.calls = 0
        self.patches: Dict[str, Any] = {}

    def dispatch(self) -> int:
        self.calls += 1
        if self.counts:
            return int(self.counts.pop(0))
        return 0


@pytest.fixture
def counting_pd() -> CountingPd:
    return CountingPd()


# -- a host a ``Runtime`` can drive ------------------------------------

class BareHost:
    """Enough of :class:`~crude.project.loader.Project` for a loop's tick.

    The loop asks a project for a ``mode``, an ``audio``, a ``pd``, a
    ``serial``, a ``clock``, a ``program``, a ``note``, a ``manifest`` and a
    way to ``shutdown``. Anything else on a real project is a phase's
    business; a test here is the loop's, so this class has no more than
    those on it and the record a shutdown unwinds.
    """

    def __init__(self, *, mode: str = "headless", clock: Optional[Clock] = None,
                 audio: Any = None, pd: Any = None, serial: Any = None,
                 ui: Any = None, display: Any = None, log: Any = None,
                 manifest: Any = None) -> None:
        self.mode = mode
        self.clock = clock if clock is not None else FakeClock(target_fps=60)
        self.audio = audio
        self.pd = pd
        self.serial = serial
        self.ui = ui
        self.display = display
        self.runtime: Any = None
        self.program: Any = None
        self.exit_code = 0
        self.notes: List[str] = []
        self._log: Callable[[str], None] = log or self.notes.append
        self.manifest = manifest if manifest is not None else _default_manifest()
        #: the record ``shutdown`` unwinds, in the order it was recorded
        self.shutdown_steps: List[tuple] = []

    def note(self, message: str) -> None:
        self._log(str(message))
        self.notes.append(str(message))

    def record(self, what, closer) -> None:
        self.shutdown_steps.append((what, closer))

    def shutdown(self) -> List[str]:
        messages: List[str] = []
        while self.shutdown_steps:
            what, closer = self.shutdown_steps.pop()
            try:
                closer()
            except Exception as exc:  # a closer that fails is still reported
                messages.append(f"{what} would not close: {exc}")
        return messages

    def host_object(self):
        return types.SimpleNamespace(
            audio=self.audio, pd=self.pd, serial=self.serial,
            runtime=self.runtime, ui=self.ui, clock=self.clock,
            display=self.display, manifest=self.manifest,
        )


def _default_manifest():
    runtime = types.SimpleNamespace(width=320, height=200, target_fps=60,
                                    vsync=False, mode="headless")
    ui = types.SimpleNamespace(theme="dark")
    return types.SimpleNamespace(runtime=runtime, ui=ui)


@pytest.fixture
def host(fake_clock, counting_pd, counting_serial) -> BareHost:
    """A project-shaped host with a fake clock, a fake Pd and a fake serial."""
    return BareHost(mode="headless", clock=fake_clock,
                    pd=counting_pd, serial=counting_serial)


@pytest.fixture
def make_host(fake_clock, counting_pd, counting_serial):
    """The same shape, built fresh, for a test that needs a second one."""
    def build(**kwargs) -> BareHost:
        kwargs.setdefault("clock", fake_clock)
        kwargs.setdefault("pd", counting_pd)
        kwargs.setdefault("serial", counting_serial)
        return BareHost(**kwargs)

    return build


# -- the audio half: a ``NullBackend`` inside an ``AudioRuntime`` ------

@pytest.fixture
def null_audio() -> AudioRuntime:
    """A runtime over :class:`NullBackend`: opened, never started for real."""
    session = PdSession(FakeLibpd())
    session.start()
    backend = NullBackend(render=session.process)
    runtime = AudioRuntime(session=session, backend=backend)
    runtime.open()
    yield runtime
    runtime.close()


@pytest.fixture
def runtime(host, null_audio) -> Runtime:
    """A headless loop wired to a fake clock, a fake pd, a fake serial, no audio."""
    host.audio = null_audio
    made = Runtime(host, mode="headless", clock=host.clock)
    made.activate(host)
    return made
