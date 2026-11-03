"""The pass a window runs once per frame: layout, the pointer, the tree in order.

:mod:`crude.ui.widget` is a tree that can be drawn, :mod:`crude.ui.layout` is a set
of arithmetic, and neither of them knows that a frame exists. This is the layer that
does, and it is the plan's two orders in one place:

    draw pass:  ``r.begin(w, h, fbw, fbh)`` -> ``ctx.layout()`` -> widgets in
                z-order -> ``r.end()`` -> ``window.swap()``
    tick order: pump input -> drain inboxes -> ``ON_FRAME`` -> dispatch UI events
                -> draw -> swap -> pace

which is why :meth:`UIContext.frame` dispatches *before* it begins: a press has to be
hit-tested against the geometry the person was looking at when they pressed, not
against the tree the resize they just caused produced. The frame is the boundary at
which a new size is taken up. The one place the plan's orders would otherwise be
wrong is the hover, so :meth:`layout` re-asks it after the pass - a layout moves
widgets under a pointer that did not move, and a hover is a fact about where the
pointer is rather than about when it last moved.

What this layer owns, and what it deliberately does not:

* the two sizes, logical and in device pixels, because the layout's one rounding is
  measured against them: :attr:`dpr` is the grid the *resolved* boxes are snapped
  to, and :attr:`snapping` turns that off for a caller that wants to see the logical
  numbers it wrote;
* the :class:`~crude.ui.input.InputRouter`, whose hit test is this tree's - and the
  focus, whose truth is the tree's rather than the router's, so a script that aims
  the keyboard at a widget has it aimed where the script thinks it is;
* :attr:`handlers`, the one place a widget's two slots are written to, so several
  script callbacks can answer one click and an ``ON_FRAME`` has the tick's seconds
  handed to it at the point the plan's tick order puts it;
* nothing about GLFW, a window or the interpreter. The ``on_*`` methods are here in
  the shapes :meth:`~crude.ui.gl.window.Window.set_input_listener` looks for, which
  is the entire coupling, and :attr:`swap` is a callable a window hands in rather
  than a thing this module can call by itself.

A headless run needs none of that and still gets all of the rest: the same tree, the
same events, the same maths, and zero ops recorded, which is the plan's parity line -
a script never has to ask whether it has a window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import RGBA, Renderer, UIError
from .behaviour import behaviour_for
from .geometry import scale_of
from .handlers import Handlers
from .input import InputRouter
from .layout import describe_layout, resolve
from .null_renderer import NullRenderer
from .scroll import content_of, restack, travel_of
from .skin import Skin
from .widget import SHARED_WORKSPACE, Widget, root_panel, top_at
from .workspace import Workspaces

__all__ = ["UIContext", "UIContextError", "FrameReport", "DEFAULT_UI_SIZE",
           "BUDGET_MS"]

#: the client rect a context builds when nobody has said what the window's is: the
#  plan's metronome front panel, which every example starts from
DEFAULT_UI_SIZE = (360.0, 220.0)

#: the two keys a context binds without being asked. Tab order is draw order, and
#  without these two the keyboard has no way into a window at all
_TAB = "tab"
_SHIFT_TAB = "shift+tab"

#: the plan's budget for one frame: 500 widgets' worth of layout and paint on the
#  interpreter thread, warned about above this rather than failed (Risk 19)
BUDGET_MS = 2.0


class UIContextError(UIError):
    """The frame was asked for in an order it cannot answer: no frame, no root."""


@dataclass(frozen=True)
class FrameReport:
    """What one frame did, as the numbers ``crude run --stats`` prints.

    Frozen and returned rather than stored, because the plan's budget question ("is
    this frame over 2 ms?") is asked about the frame that just finished while the
    next one is already being decided, and a caller that had to re-read a mutable
    counter would be measuring the frame in progress.

    :attr:`ms` is that question: the wall time this context's own pass took, in
    milliseconds, with the swap included and nothing outside the frame. It is left
    out of the equality below, because the budget is a measurement and a measurement
    is not a fact about what the frame *drew* - two frames that laid out and painted
    the same picture are the same frame as far as an assertion is concerned, however
    busy the machine was while it did it.
    """

    events: int = 0
    moved: int = 0
    ops: int = 0
    widgets: int = 0
    frames: int = 1
    dpr: float = 1.0
    swapped: bool = False
    #: how many ``ON_FRAME`` handlers this tick ran, when the frame was given a ``dt``
    handlers: int = 0
    ms: float = field(default=0.0, compare=False)

    def as_dict(self) -> Dict[str, Any]:
        return {"events": self.events, "moved": self.moved, "ops": self.ops,
                "widgets": self.widgets, "frames": self.frames,
                "dpr": round(self.dpr, 3), "swapped": self.swapped,
                "handlers": self.handlers,
                "ms": round(self.ms, 3)}

    def over_budget(self, limit: float = BUDGET_MS) -> bool:
        """Whether this frame took longer than the plan's target for one.

        The plan's risk answer is to *warn* rather than to fail: a 500 widget panel
        on a slow machine is a log line and a ``--stats`` number, not a window that
        stopped drawing. The runtime (S11) is what prints the warning; this is the
        question it prints it from.
        """
        return self.ms > float(limit)

    def __str__(self) -> str:
        said = (f"frame {self.frames}: {self.ops} op(s), "
                f"{self.widgets} widget(s), {self.moved} box(es) moved, "
                f"{self.events} event(s) in {self.ms:.2f} ms")
        if self.handlers:
            said += f", {self.handlers} frame handler(s)"
        return said + ("" if self.swapped else ", not swapped")


class UIContext:
    """The tree, the skin, the sizes and the router, run as one frame.

    Build it once, with the renderer the window made - or with none, which is
    :class:`~crude.ui.null_renderer.NullRenderer` and a headless run - and call
    :meth:`frame` per tick. Every part is also public on its own in the order the
    plan gives, because a runtime that has already begun a frame, or wants to drain
    a Pd inbox between the layout and the draw, is the normal case rather than an
    exception to argue with.
    """

    def __init__(self, renderer: Optional[Renderer] = None, *,
                 root: Optional[Widget] = None,
                 skin: Any = None, width: float = None, height: float = None,
                 fb_width: int = None, fb_height: int = None,
                 painting: bool = True, snapping: bool = True,
                 swap: Optional[Callable[[], Any]] = None,
                 name: str = "ui", keys: bool = True) -> None:
        self.name = str(name or "ui")
        #: what the ops go to; ``NullRenderer`` is the headless answer
        self.renderer: Renderer = renderer if renderer is not None else \
            NullRenderer()
        #: the skin the tree is laid out and painted with. A name is a theme, so
        #  ``UIContext(skin="flat")`` is the same object as ``UIContext()`` with a
        #  theme asked for later, and a script's ``STYLE`` edit is a prop away
        self.skin: Skin = skin if isinstance(skin, Skin) else Skin(skin)
        if root is None:
            root = root_panel(
                width if width is not None else DEFAULT_UI_SIZE[0],
                height if height is not None else DEFAULT_UI_SIZE[1])
        if not root.is_container:
            raise UIContextError(f"a window's root holds the other widgets; "
                                 f"'{root.path()}' is a '{root.kind.name}', which "
                                 f"holds nothing")
        if root.parent is not None:
            raise UIContextError(f"'{root.path()}' is inside "
                                 f"'{root.parent.path()}': the UI root *is* the "
                                 "window's client rect, so it has no parent to be "
                                 "measured against")
        #: the tree, which is the window's client rect in logical pixels
        self.root: Widget = root
        #: the two sizes: what a script's numbers mean, and what the screen has
        self.width = float(width if width is not None else root.width)
        self.height = float(height if height is not None else root.height)
        self.fb_width = int(fb_width if fb_width is not None else self.width)
        self.fb_height = int(fb_height if fb_height is not None else self.height)
        #: paint the tree, or only lay it out: ``--mode headless`` answers ``False``
        self.painting = bool(painting)
        #: snap the resolved boxes to :attr:`dpr`, which is the plan's one rounding
        self.snapping = bool(snapping)
        #: the callable that puts a finished frame on screen - ``window.swap``
        self.swap: Optional[Callable[[], Any]] = swap
        #: what the pointer means, aimed at this tree's own hit test
        self.router = InputRouter(self.hit, width=self.width,
                                  height=self.height, root=self.root,
                                  scroll_owner=self.wheel_target)
        #: the script's side of every widget: several handlers per event, the
        #  ``LINK_*`` plumbing that runs before them, and the ``ON_FRAME`` list
        self.handlers = Handlers(name=self.name)
        #: every script-painted face this window has recorded, kept by the bridge
        #  that runs the painters; reached here because a project's ``PAINT_REQUEST``
        #  and a theme change both want to say it from the window rather than from
        #  the handler table
        self.paints = self.handlers.paints
        #: the named scenes this window can show, with the entry script's -
        #  :data:`~crude.ui.workspace.ROOT_WORKSPACE` - as the only one until a
        #  script declares another. Held here because a switch moves subtrees of
        #  *this* tree and nothing else: it is not a second window and not a
        #  second interpreter.
        self.workspaces = Workspaces(self)
        #: the frame counters a report and a log read
        self.frames = 0
        self.events = 0
        self.moved = 0
        self.ops = 0
        #: how long the last whole frame took, in milliseconds
        self.last_ms = 0.0
        self._begun = False
        if keys:
            self.bind_keys()

    # -- the frame, in the plan's order -----------------------------------
    def begin(self) -> Renderer:
        """Open the frame on the renderer, at this context's two sizes."""
        if self._begun:
            raise UIContextError(f"{self.name}: a frame was begun and never "
                                 "ended, so this one would nest inside it")
        self._begun = True
        self.renderer.begin(self.width, self.height, self.fb_width,
                            self.fb_height)
        return self.renderer

    def layout(self) -> int:
        """Lay the tree out for this frame; how many boxes the pass wrote.

        The plan's ``ctx.layout()``, and the only caller this build needs of
        :func:`~crude.ui.layout.resolve`: the root's box is re-placed from
        :attr:`width`/:attr:`height` so a resize taken up in :meth:`frame` reaches
        the anchors, the stacks and the ``PACK`` rules in the same act, and
        :attr:`dpr` is handed over as the one rounding the drawn geometry gets.

        The count is the interesting part on an idle frame: zero means nothing was
        rewritten, which is what a script painter's cache (S7's ``PaintList``) keys
        its honesty on.

        A viewport is settled after the pass rather than inside it: a scroll area's
        ``content`` and ``travel`` are answers about where its rows ended up, so
        asking them before the arrangement would report the frame before this one,
        and asking them during a paint would have the draw pass write props. The
        two numbers only ever move when the content does, so an idle frame costs a
        walk and nothing else.

        A new client rect is answered here as well as by the queued resize event:
        when the root's box and the window's rect disagree, the root is told before
        the pass runs, so whatever a *layer* above the tree places by hand - a
        settings dialog that sizes itself to the window - gets its chance to re-decide
        in the same frame the size changed. A GLFW resize arrives through
        :meth:`dispatch` first and so is not said twice: that pass leaves the root at
        the rect the context already has.
        """
        if (self.root.width, self.root.height) != (self.width, self.height):
            self.root.on_resize(self.width, self.height)
        moved = resolve(self.root, self.skin, width=self.width,
                        height=self.height, dpr=self.grid)
        restack(self.root)
        self.moved = moved
        # the tree may have moved under a pointer that did not
        self.router.rehover()
        return moved

    def dispatch(self) -> int:
        """Hand everything queued since last time to the widgets; how many went.

        The router's ``focus`` is aimed at whichever widget the tree says holds the
        keyboard first: :meth:`~crude.ui.widget.Widget.grab_focus` is the truth a
        script's ``GET(w, "focused")`` reads, and a click is only one of the ways to
        write it. Without this a ``FOCUS(field)`` from a script would move the ring
        and leave the typing where it was.
        """
        held = self.root.find_focus()
        if held is not self.router.focus:
            self.router.focus = held
        count = self.router.dispatch()
        self.events += count
        return count

    def draw(self) -> int:
        """Clear the frame and paint the tree in z-order; how many ops it took.

        The recursion is the widget's own (:meth:`~crude.ui.widget.Widget.paint`),
        because the clip a panel owes its children belongs to the panel: what this
        adds is the clear, the skin's own ``window`` colour, and the answer to
        "should anything be painted at all" - which a headless run says no to and a
        test of a layout says no to in order to read the geometry back.
        """
        if not self._begun:
            raise UIContextError(f"{self.name}: there is no frame to draw into; "
                                 "call begin() first, or frame() for the whole pass")
        if not self.painting:
            self.ops = 0
            return 0
        self.renderer.clear(self.background)
        self.root.paint(self.renderer, self.skin)
        self.ops = self.renderer.op_count
        return self.ops

    def end(self, *, swap: Any = None) -> int:
        """Close the frame and, unless told otherwise, put it on screen."""
        return self._end(swap=swap)[0]

    def abort(self) -> bool:
        """Close a frame a raising handler left open; whether there was one.

        The recovery path for the one failure this layer cannot fix by itself: a
        script's ``ON_PAINT`` that raises mid-draw would otherwise leave
        :attr:`_begun` set, and every frame after it would be refused for a mistake
        the person has already been told about. One call at the top of a runtime's
        ``except`` and the next frame is ordinary again - the ops already emitted
        stay in the frame, which is the honest picture of a half-drawn window.
        """
        if not self._begun:
            return False
        self._begun = False
        self.renderer.end()
        return True

    def frame(self, *, dt: float = None, swap: Any = None) -> FrameReport:
        """One whole frame, in the order the plan fixes; what it amounted to.

        ``dispatch`` first, then ``begin -> layout -> draw -> end``, then the swap:
        see the module docstring for why input is not inside the frame it answers.
        ``swap`` of ``False`` holds the frame back, a callable is swapped with that,
        and saying nothing uses :attr:`swap` if this context was given one.

        ``dt`` is the tick's seconds and is what puts ``ON_FRAME`` where the plan's
        tick order puts it - after the inboxes have been drained and before the
        events are dispatched - so a caller that hands one gets the whole tick from
        one call, and a caller that does not (a test of the draw pass, a single
        repaint after a resize) is not asked to invent a delta it has not measured.
        The frame's own clock is still read here and nowhere else, which is what
        makes :attr:`FrameReport.ms` the number the plan's budget is asked about: it
        covers this pass and not the ``ON_FRAME`` handler that ran before it, because
        those are two separate warnings and only one of them is this layer's.
        """
        started = perf_counter()
        fired = 0 if dt is None else self.handlers.fire_frame(dt)
        events = self.dispatch()
        self.begin()
        moved = self.layout()
        ops = self.draw()
        _, swapped = self._end(swap=swap)
        self.last_ms = (perf_counter() - started) * 1000.0
        return FrameReport(events=events, moved=moved, ops=ops,
                           widgets=self.root.count(), frames=self.frames,
                           dpr=self.dpr, swapped=swapped, ms=self.last_ms,
                           handlers=fired)

    # -- what a frame is measured against ---------------------------------
    @property
    def dpr(self) -> float:
        """Device pixels per logical pixel, from the two sizes this context has."""
        return scale_of(self.fb_width, self.width)

    @property
    def grid(self) -> Optional[float]:
        """The device grid the resolved boxes are snapped to, or ``None``.

        ``None`` when :attr:`snapping` is off, which leaves every number the layout
        produced exactly logical: a test of a ``grow`` fraction wants the 100.0 it
        asked for rather than the 100 the screen would show, and the plan is that
        the rounding is a draw-time decision rather than a layout-time one.
        """
        return self.dpr if self.snapping else None

    @property
    def background(self) -> RGBA:
        """The colour the frame is cleared to: the skin's ``window`` role."""
        return self.skin.colour("window")

    def set_size(self, width: float, height: float, *,
                 fb_width: int = None, fb_height: int = None) -> bool:
        """Take up a new client rect; whether anything changed.

        The framebuffer sizes come with it rather than later, because a window's
        resize and its framebuffer resize are one gesture on most platforms and two
        events in GLFW whose order is not promised: a frame begun between the two
        would be laid out on the grid of the screen the person left.
        """
        before = (self.width, self.height, self.fb_width, self.fb_height)
        self.width = float(width)
        self.height = float(height)
        self.fb_width = int(fb_width if fb_width is not None else self.width)
        self.fb_height = int(fb_height if fb_height is not None else self.height)
        self.router.width = self.width
        self.router.height = self.height
        return before != (self.width, self.height, self.fb_width, self.fb_height)

    # -- the pointer's end ------------------------------------------------
    def hit(self, x: float, y: float) -> Optional[Widget]:
        """The topmost widget at a point of the window: the router's hit test."""
        return top_at(self.root, float(x), float(y))

    def takes_wheel(self, held: Any) -> bool:
        """Whether a wheel is *this* widget's to answer, rather than a box's.

        Two sources of an answer, in the order they are written down: the kind's own
        gesture table (:data:`~crude.ui.behaviour.BEHAVIOURS`), and a script's
        ``ON_SCROLL`` on this particular widget. A viewport that has run out of
        content to slide is then refused rather than skipped, because a box that
        cannot move has no use for the gesture and the person's wheel is meant for
        whoever does - a short list inside a settings page that is itself too tall
        for the window has to leave the wheel to the page.
        """
        if held is None:
            return False
        if behaviour_for(held.kind).get("on_scroll") is None:
            return self.handlers.count(held, "scroll") > 0
        if content_of(held) is not None:
            return travel_of(held) > 0.0
        return True

    def wheel_target(self, held: Any) -> Any:
        """The widget the wheel belongs to: the one hit, or the nearest box that scrolls.

        Every other gesture goes to the widget under the pointer, and that is right
        for a wheel too - until the widget under the pointer is a *row*. A device
        list is a column of toggles laid over the viewport they all live in, so a
        router that stopped at the toggle would leave the list scrollable only on the
        few pixels of its plate no row covers, which is not a scroll area. So the
        question is asked of this tree, where the kinds and the registrations are
        both known, and it walks outward: the first ancestor that can move answers.

        Left as ``None`` for a router with no tree to ask, the wheel stays where the
        pointer is.
        """
        node = held
        while node is not None:
            if self.takes_wheel(node):
                return node
            node = getattr(node, "parent", None)
        return held

    @property
    def hover(self) -> Optional[Widget]:
        """What the pointer is over, as of the last dispatch or :meth:`layout`."""
        return self.router.hover

    @property
    def focus(self) -> Optional[Widget]:
        """Which widget the keyboard is aimed at, as the tree says it."""
        return self.root.find_focus()

    @property
    def dragging(self) -> Optional[Widget]:
        """The widget holding the pointer, whoever the pointer is over."""
        return self.router.capture

    @property
    def cursor(self) -> Tuple[float, float]:
        """Where the pointer is, in logical pixels."""
        return (self.router.x, self.router.y)

    def grab_focus(self, held: Any) -> bool:
        """Aim the keyboard at a widget - the tree's answer and the router's too."""
        found = held if isinstance(held, Widget) else self.find(held)
        if found is None:
            return False
        took = found.grab_focus()
        self.router.focus = self.root.find_focus()
        return took

    def release_focus(self) -> bool:
        """Give up the keyboard; whether somebody had it."""
        held = self.focus
        if held is None:
            self.router.focus = None
            return False
        gave = held.release_focus()
        self.router.focus = None
        return gave

    def focus_next(self, step: int = 1) -> Optional[Widget]:
        """Move the keyboard along the tab order, wrapping; where it landed.

        The order is the draw order, which is what
        :meth:`~crude.ui.widget.Widget.tabbables` says and what a person reading a
        row of controls expects - and ``RAISE`` edits it, because raising a widget
        moves it in the row as well as on the screen.
        """
        pool = self.root.tabbables()
        if not pool:
            return None
        held = self.focus
        if held is None:
            return self._aim(pool[0 if int(step) >= 0 else -1])
        return self._aim(held.next_focus(int(step)))

    def bind_keys(self) -> InputRouter:
        """Install the tab and shift-tab the keyboard needs to be reachable at all.

        Called by the constructor unless a caller said ``keys=False``, which is how a
        project that wants its own navigation - a keybed, a step grid - keeps the
        default out of its way rather than unbinding afterwards.
        """
        self.router.hotkeys.setdefault(_TAB, lambda: self.focus_next(1))
        self.router.hotkeys.setdefault(_SHIFT_TAB, lambda: self.focus_next(-1))
        return self.router

    # -- the script's side -------------------------------------------------
    def attach_program(self, program: Any) -> Any:
        """Tell the handler bridge which program its script functions belong to.

        :func:`crude.lang.program._attach_host` calls this on every host attribute
        that has it, which is how an ``ON_CHANGE`` registered by a running script
        finds its way back into the interpreter - and the reason a context handed to
        a bare ``Program(host=SimpleNamespace(ui=ctx))`` in a test needs no further
        wiring to fire a ``FUNC``.
        """
        self.handlers.attach_program(program)
        return program

    # -- the tree ---------------------------------------------------------
    def add(self, held: Any) -> Widget:
        """Put a widget in the window, at the top of the stack it belongs to.

        Two questions are answered by the one row :attr:`Widget.workspace` holds.
        Where does it go: something stamped
        :data:`~crude.ui.widget.SHARED_WORKSPACE` - the menu bar, a menu's popup, a
        settings dialog - is the window's own and goes at the root, because a scene
        switch must not hide the menu it is reached from; anything else goes into the
        current scene's panel, which is the window's root until a second scene is
        declared. Whose is it: the first add stamps the current scene's name, so a
        later switch can find this widget again without the script saying it twice.
        """
        found = self._one(held)
        if found.workspace == SHARED_WORKSPACE:
            target = self.root
        else:
            target = self.workspaces.container()
            self.workspaces.claim(found)
        added = target.add(found)
        self.router.root = self.root
        return added

    def remove(self, held: Any) -> Widget:
        """Take a widget out of the tree, and out of the pointer's reach.

        The router is told as well as the tree: a hover, a focus or a drag that was
        on that widget - or on anything that was inside it - has no answer to send
        to, and the next dispatch would otherwise be pointing at a freed box.
        The handler bridge is told too, for the same reason and with the same
        severity: a script's ``ON_CLICK`` left registered on a widget nobody is
        drawing any more is how a project fires into its next frame from the last.
        """
        found = self.root.remove(self._one(held))
        self.router.forget(found)
        for gone in (found, *found.descendants()):
            self.handlers.release(gone)
        return found

    def clear(self) -> List[Widget]:
        """Take every widget out of the window; what a rebuilt panel does to its own."""
        gone = self.root.clear()
        for held in gone:
            self.router.forget(held)
            for every in (held, *held.descendants()):
                self.handlers.release(every)
        # ``release`` already retired each widget's own face; this is the sweep for a
        # tree that was rebuilt without going through here, and it costs nothing
        # because the entries the loop above dropped are already gone
        self.paints.clear()
        return gone

    def find(self, name: Any) -> Optional[Widget]:
        """A widget by the name a script gave it, or its kind if it had no name."""
        return self.root.find(name)

    def widgets(self) -> List[Widget]:
        """Every widget in the tree, in draw order: root first, top of the stack last."""
        return list(self.root.walk())

    def top_down(self) -> List[Widget]:
        """Every widget in the order a hit test tries them: topmost first."""
        return self.root.front_to_back()

    # -- the skin ---------------------------------------------------------
    def set_skin(self, skin: Any) -> Skin:
        """Lay the next frame out with a different skin; the one now in charge.

        Nothing else has to be said: the padding, the gap tokens and every colour
        come from it on the next :meth:`layout`, and a widget's paint key holds the
        skin's digest, so a theme change repaints exactly once.
        """
        found = skin if isinstance(skin, Skin) else Skin(skin)
        self.skin = found
        return found

    def set_theme(self, theme: Any) -> Skin:
        """The same skin at a different theme: ``STYLE`` with one argument."""
        return self.set_skin(self.skin.with_theme(theme))

    def describe(self, held: Any) -> Dict[str, Any]:
        """A widget's placement policy as a document - what ``WIDGET`` will print."""
        return describe_layout(self._one(held))

    def tree(self) -> List[str]:
        """The whole tree as lines, the order it is drawn in."""
        return self.root.tree()

    # -- GLFW's side, in the shapes a window looks for --------------------
    def on_cursor(self, x: float, y: float) -> None:
        self.router.on_cursor(x, y)

    def on_button(self, button: int, action: int, mods: int) -> None:
        self.router.on_button(button, action, mods)

    def on_key(self, key: int, scancode: int, action: int, mods: int) -> None:
        self.router.on_key(key, scancode, action, mods)

    def on_char(self, character: str) -> None:
        self.router.on_char(character)

    def on_scroll(self, dx: float, dy: float) -> None:
        self.router.on_scroll(dx, dy)

    def on_resize(self, width: int, height: int) -> None:
        """A new client rect: taken up here, and queued for the root's handlers.

        The size is stored at once rather than waiting for the dispatch, because the
        frame that follows this still happens in the same tick and would otherwise be
        begun at the old one and laid out at the new - the plan's one rounding needs
        both numbers to be the frame the person is looking at.
        """
        self.set_size(width, height)
        self.router.on_resize(width, height)

    def on_framebuffer_resize(self, width: int, height: int) -> None:
        """A new device size, which changes the grid and nothing else."""
        self.fb_width = int(width)
        self.fb_height = int(height)
        self.router.on_framebuffer_resize(width, height)

    def on_drop(self, paths: Any) -> None:
        self.router.on_drop(list(paths or ()))

    def on_close_request(self) -> Optional[bool]:
        """Ask the tree, and through it the script, whether the window may go.

        The router's answer is the one a window acts on: ``False`` holds it open,
        anything else lets it go. The root's own ``on_close_request`` is what a
        project's ``ON_CLOSE`` handler is reached through.
        """
        return self.router.on_close_request()

    @property
    def may_close(self) -> bool:
        """Whether a dispatched close request went unanswered by an objector.

        The window acts on the return value of :meth:`on_close_request`, which is
        decided at once; this is the record of it, and it only says ``True`` once
        the request has actually been through :meth:`dispatch` - a close still
        sitting in the queue is a close nobody has answered yet.
        """
        return bool(self.router.close_allowed)

    # -- describing itself ------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        """The frame's own numbers, in a shape a report or a log can print."""
        return {"name": self.name, "size": (self.width, self.height),
                "framebuffer": (self.fb_width, self.fb_height),
                "dpr": round(self.dpr, 3), "snapping": self.snapping,
                "painting": self.painting, "begun": self._begun,
                "widgets": self.root.count(), "frames": self.frames,
                "last_ops": self.ops, "last_moved": self.moved,
                "last_ms": round(self.last_ms, 3),
                "events": self.events,
                "cursor": (round(self.router.x, 2), round(self.router.y, 2)),
                "hover": self.hover.path() if self.hover else None,
                "focus": self.focus.path() if self.focus else None,
                "dragging": self.dragging.path() if self.dragging else None,
                "handlers": self.handlers.stats(),
                "paints": self.paints.stats()}

    def __str__(self) -> str:
        said = (f"UIContext({self.width:g}x{self.height:g} @ "
                f"{self.dpr:g} dpr, {self.root.count()} widget(s)")
        if self.frames:
            said += f", {self.frames} frame(s), {self.ops} op(s), " \
                    f"{self.last_ms:.2f} ms"
        return said + ")"

    def __repr__(self) -> str:
        return f"<UIContext {self.name!r} {self.width:g}x{self.height:g}>"

    # -- the small internals ----------------------------------------------
    def _one(self, held: Any) -> Widget:
        """A widget, or the widget a script named; the tree answers in one voice."""
        if isinstance(held, Widget):
            return held
        found = self.find(held)
        if found is None:
            raise UIContextError(f"'{held}' is not a widget in this window, and "
                                 "nothing here is named that either")
        return found

    def _aim(self, held: Optional[Widget]) -> Optional[Widget]:
        """Focus a widget that may be ``None``, and hand it back either way."""
        if held is not None:
            held.grab_focus()
            self.router.focus = self.root.find_focus()
        return held

    def _end(self, *, swap: Any = None) -> Tuple[int, bool]:
        """Close the frame, and say whether the callable that shows it was run.

        Three answers to one question, in order of how specifically they were said:
        a callable given to this call, nothing at all when ``False`` was, and
        otherwise the window's own :attr:`swap` if this context was handed one.
        """
        if not self._begun:
            raise UIContextError(f"{self.name}: a frame was ended that was never "
                                 "begun")
        self._begun = False
        ops = self.renderer.end()
        self.frames += 1
        held = swap if callable(swap) else (
            None if swap is False else
            (self.swap if callable(self.swap) else None))
        if held is None:
            return ops, False
        held()
        return ops, True


def window_context(window: Any, renderer: Renderer = None, *,
                   root: Optional[Widget] = None, skin: Any = None,
                   **wanted: Any) -> UIContext:
    """A context built around a window, wired both ways.

    ``UIContext.for_window(window)`` is the same call. It takes the client rect and
    the framebuffer from the window, hands it the swap, and sets itself up as the
    window's input listener, which is the only reason either object knows the other
    exists. A window that cannot answer for its size - closed, or a fake in a test -
    gets the default rect rather than a zero-sized one.
    """
    width, height = _ask(window, "size", DEFAULT_UI_SIZE)
    fb_width, fb_height = _ask(window, "framebuffer_size",
                               (int(width), int(height)))
    held = UIContext(renderer, root=root, skin=skin, width=width, height=height,
                     fb_width=fb_width, fb_height=fb_height,
                     swap=getattr(window, "swap", None), **wanted)
    listener = getattr(window, "set_input_listener", None)
    if callable(listener):
        listener(held)
    return held


def _ask(window: Any, name: str, default: Tuple[float, float]) -> Tuple[float, float]:
    """One of a window's two size calls, or a default for a window without it."""
    found = getattr(window, name, None)
    if not callable(found):
        return default
    said = found()
    if not said or not said[0] or not said[1]:
        return default
    return (float(said[0]), float(said[1]))
