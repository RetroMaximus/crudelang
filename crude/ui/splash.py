"""The splash: a load report, drawn with nothing but the renderer contract.

``SplashReporter`` is handed a canvas and calls ``draw(frame)`` between steps;
this is that canvas. It only ever uses the methods
:class:`~crude.ui.base.Renderer` promises, which is what makes the splash testable
against a :class:`~crude.ui.recording.RecordingRenderer` - the picture a window
project shows is checked by reading the ops back, and the same code then draws
them for real without a word of GL in this file.

What a frame says, and therefore what gets drawn:

* the project's name and the mode it is loading in, at the top;
* the step that is running now, and how far the whole plan has come, as a bar;
* what the step has said so far - the last few lines only, because a splash that
  scrolls off the bottom is a splash nobody reads;
* the plan itself, on the very first frame, so a person can see what is coming;
* a finish or a failure, in a colour that means it.

Nothing here waits, pumps or closes anything on its own: a *surface* - the real
:class:`~crude.ui.gl.Display`, or nothing at all in a test - is what puts the
frame on screen, and a surface that has stopped answering only stops this canvas
drawing, never the load.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from .base import Renderer, UIError
from .geometry import TEXT_ADVANCE, clamp

__all__ = ["SplashCanvas", "PALETTE", "DEFAULT_WIDTH", "DEFAULT_HEIGHT"]

#: the window a splash asks for when the manifest does not say
DEFAULT_WIDTH = 480
DEFAULT_HEIGHT = 260

MARGIN = 24.0
TITLE_SIZE = 22.0
LABEL_SIZE = 13.0
SMALL_SIZE = 11.0
#: how much of the window the bar takes, and how tall it is
BAR_WIDTH = 0.88
BAR_HEIGHT = 8.0
#: the most detail lines that fit under the bar, whatever the project says
MAX_DETAILS = 4

#: the splash's whole colour set: a theme is a different dict, nothing else
PALETTE = {
    "background": "#1b1d22",
    "panel": "#242830",
    "title": "#f2f4f8",
    "label": "#b9c0cc",
    "detail": "#8d95a3",
    "track": "#333945",
    "fill": "#59b0ff",
    "done": "#5fd08a",
    "failed": "#ff6b5e",
}


class SplashCanvas:
    """Draws one :class:`~crude.project.progress.SplashFrame` per ``draw``.

    The frame is taken as it comes - this file does not import the loader, because
    a UI layer that reaches back into the project half is a layer that cannot be
    tested on its own. What it asks of the frame is the reporter's own vocabulary:
    ``event``, ``project``, ``mode``, ``step``, ``label``, ``percent``,
    ``details``, ``steps``, ``done``, ``failed`` and ``message``.
    """

    def __init__(self, renderer: Renderer, surface: Any = None, *,
                 width: float = DEFAULT_WIDTH, height: float = DEFAULT_HEIGHT,
                 palette: Optional[dict] = None) -> None:
        self.renderer = renderer
        #: what puts a finished frame on screen: a ``Display``, or nothing in a test
        self.surface = surface
        self.width = float(width)
        self.height = float(height)
        self.palette = dict(PALETTE if palette is None else palette)
        #: the frames drawn, in order - what a test reads back
        self.frames: List[Any] = []
        #: the last percent this canvas drew, kept so a repaint need not recompute it
        self.percent = 0.0
        #: set when the surface stops answering: the window was closed or died
        self.gone = False
        #: why drawing was given up on, which the reporter turns into console output
        self.error = ""
        self.title = ""

    # -- the reporter's one call ------------------------------------------
    def draw(self, frame: Any) -> int:
        """Paint one frame and show it; how many ops it took.

        Called on the main thread between load steps, which is why this pumps the
        window's events first: a splash that never answers the OS is drawn flat,
        and on Windows it is the difference between a progress bar and a freeze.
        """
        if self.gone:
            return 0
        self._pump()
        if self.gone:
            # the person closed it while this step was running: drawing into a
            # window that has gone is the reporter's problem, not the load's
            return 0
        self._begin_frame()
        # the renderer now knows the box this frame really is, which is the answer
        # a resized window just changed
        self.width, self.height = self.renderer.width, self.renderer.height
        try:
            ops = self._paint(frame)
        finally:
            if self.surface is not None:
                self.surface.present()
            else:
                self.renderer.end()
        self.frames.append(frame)
        self.percent = frame.percent
        return ops

    def _pump(self) -> None:
        """Let the window talk, and notice when it has stopped wanting to."""
        pump = getattr(self.surface, "pump", None)
        if not callable(pump):
            return
        if not pump():
            self.gone = True

    def _begin_frame(self) -> None:
        """Start the frame, at the window's size or at the canvas's own.

        The surface knows how big the window really is; without one, the width
        and height this canvas was given are the whole answer.
        """
        if self.surface is not None:
            self.surface.frame()
        else:
            self.renderer.begin(self.width, self.height)

    def _paint(self, frame: Any) -> int:
        renderer = self.renderer
        colour = self.palette
        width, height = renderer.width, renderer.height
        if width <= 0 or height <= 0:
            raise UIError("the splash has no window to fill: "
                          f"{width} x {height} is not a picture")
        accent = (colour["failed"] if frame.failed else
                  colour["done"] if frame.done else colour["fill"])

        renderer.clear(colour["background"])
        self._heading(frame, width, height)
        self._bar(frame, width, height, accent)
        self._lines(frame, width, height)
        if frame.message and (frame.done or frame.event == "finish"):
            renderer.text(MARGIN, height - MARGIN - SMALL_SIZE * 1.4,
                          self._trim(frame.message, SMALL_SIZE, width - 2 * MARGIN),
                          SMALL_SIZE, colour["label"])
        return renderer.op_count

    # -- the pieces -------------------------------------------------------
    def _heading(self, frame: Any, width: float,
                 height: float) -> None:
        renderer = self.renderer
        name = frame.project or "crude"
        if self.title != name and self.surface is not None:
            setter = getattr(self.surface, "set_title", None)
            if callable(setter):
                setter(name)
            self.title = name
        renderer.text(MARGIN, MARGIN, self._trim(name, TITLE_SIZE, width - 2 * MARGIN),
                      TITLE_SIZE, self.palette["title"])
        said = frame.step or frame.label
        if frame.mode:
            said = f"{said}  ({frame.mode})" if said else frame.mode
        if said:
            renderer.text(MARGIN, MARGIN + TITLE_SIZE + 6.0,
                          self._trim(said, LABEL_SIZE, width - 2 * MARGIN),
                          LABEL_SIZE, self.palette["label"])

    def _bar(self, frame: Any, width: float, height: float,
             accent: Any) -> None:
        """A track, a fill, and the percent it stands for."""
        renderer = self.renderer
        track_width = max(1.0, width * BAR_WIDTH)
        left = (width - track_width) * 0.5
        top = height - MARGIN - BAR_HEIGHT * 3.4
        half = BAR_HEIGHT * 0.5
        renderer.rect(left, top, track_width, BAR_HEIGHT,
                      self.palette["track"], radius=half)
        part = clamp(frame.percent, 0.0, 100.0) / 100.0
        if part > 0:
            # the fill is a rounded box of its own rather than a clipped piece of
            # the track, so 3% is a pill and 100% is a bar, and neither is a square
            renderer.rect(left, top, max(BAR_HEIGHT, track_width * part),
                          BAR_HEIGHT, accent, radius=half)
        renderer.text(left + track_width, top - LABEL_SIZE - 2.0,
                      f"{frame.percent:3.0f}%", LABEL_SIZE,
                      self.palette["label"], align="right")

    def _lines(self, frame: Any, width: float, height: float) -> None:
        """The detail lines, and the plan on the first frame.

        Clipped to the box between the heading and the bar: a project that names
        thirty steps or prints a wall of text cannot make the splash scroll its
        title off the top, which is the one thing a splash has to keep.
        """
        renderer = self.renderer
        wanted: Sequence[str] = ()
        if frame.event == "start" and frame.steps:
            wanted = frame.steps
        elif frame.details:
            wanted = frame.details[-MAX_DETAILS:]
        if not wanted:
            return
        top = MARGIN + TITLE_SIZE + LABEL_SIZE + 18.0
        bottom = height - MARGIN - BAR_HEIGHT * 3.8
        box = (MARGIN, top, width - 2 * MARGIN, max(0.0, bottom - top))
        if box[3] <= SMALL_SIZE:
            return
        with renderer.clipped(*box):
            for offset, line in enumerate(wanted):
                renderer.text(0.0, offset * (SMALL_SIZE + 4.0),
                              self._trim(line, SMALL_SIZE, box[2]),
                              SMALL_SIZE, self.palette["detail"])

    def _trim(self, text: str, size: float, box: float) -> str:
        """Shorten a line to the box it has, by the same estimate every renderer uses.

        Measured rather than guessed would be better, but :meth:`measure` bakes a
        texture - and a splash redraws everything it says sixty times a second, so
        the cheap answer from :data:`~crude.ui.geometry.TEXT_ADVANCE` is the right
        one here.
        """
        text = str(text)
        room = max(0.0, float(box))
        advance = max(0.1, float(size)) * TEXT_ADVANCE
        if room <= advance or len(text) * advance <= room:
            return text
        allowed = max(1, int(room / advance) - 1)
        if len(text) <= allowed:
            return text
        return text[:allowed].rstrip() + "\u2026"

    # -- the rest of a life ----------------------------------------------
    @property
    def events(self) -> Tuple[str, ...]:
        """The kind of each frame drawn, which is what a test compares against."""
        return tuple(frame.event for frame in self.frames)

    def blank(self) -> None:
        """One empty frame, for the moment the load is over and the window stays."""
        if self.gone:
            return
        self._pump()
        if self.gone:
            return
        self._begin_frame()
        self.renderer.clear(self.palette["background"])
        if self.surface is not None:
            self.surface.present()
        else:
            self.renderer.end()

    def stats(self) -> dict:
        return {"canvas": "splash", "frames": len(self.frames),
                "percent": round(self.percent, 1), "gone": self.gone,
                "error": self.error,
                "renderer": self.renderer.stats()}

    def __str__(self) -> str:
        return (f"SplashCanvas({self.width:g}x{self.height:g}, "
                f"{len(self.frames)} frames, {self.percent:.0f}%"
                f"{', gone' if self.gone else ''})")
