"""What a script wired to the tree, and the one place that fires it.

:mod:`crude.ui.widget` has two slots on every widget - ``on_change`` and
``on_event`` - and fires exactly one callable through each. That is not enough for
a script: the plan's metronome registers two ``ON_CHANGE`` handlers on the same
toggle, one to send to Pd and one to kill the LED, and a hover wants the same
handler for entering and leaving. So the slots belong to this module, which turns
one callable into a list, and the list is the thing ``OFF`` and a report read.

Four rules carry it:

* **The tree stays language-free.** Nothing here imports ``Number`` or ``RTError``.
  A registered handler is a Python callable, and a *program* is whatever has
  ``invoke(func, args) -> (value, error)`` - the same duck-typing
  :mod:`crude.lang.program` uses to find a host. A test of a widget tree can hand
  over a ``lambda`` and never build an interpreter; a script's handler arrives
  through the bindings and is called the other way. Which of the two a registration
  is, is read off the callable - a script function carries an ``execute`` and a host
  callable does not - so a ``lambda`` the toolkit registered itself runs directly
  whether or not a project's script is attached to the same window.
* **Arguments are the event's, and then the event's name.** A change hands over
  ``(value, prop, "change")`` and a click ``(region, x, y, button, "click")``. A
  handler is called with as many as it was written to take, which is what lets the
  plan's ``FUNC(v) -> …`` and a ``FUNC(v, prop) -> …`` both be ``ON_CHANGE`` handlers,
  and is why ``ON_HOVER`` - registered under two events that have no arguments of
  their own - has the word to give. The click's ``region`` is the one argument that
  comes from a handler rather than from the pointer: what ``ON_HIT`` answered, or
  ``NULL`` when nothing narrowed it.
* **A raising handler is not a raising frame.** Three consecutive failures mute one
  handler, say so in the log, and keep the frame: the plan's painter answer, run
  through the same numbers ``crude.pd.runtime`` uses for ``ON_PD``. One exception is
  let through rather than counted: :class:`~crude.errors.ExitRequest`, because a
  script asking the run to stop is control flow and the loop is the only caller that
  knows what the code it carries means.
* **A link is plumbing, not a handler.** ``LINK_VALUE`` mirrors a prop onto another
  widget *before* the script's handler sees the change, and both directions are
  guarded twice over - a value that would not move is not written, and a chain
  deeper than :data:`MAX_LINK_DEPTH` stops - because a two-way link whose epsilon
  check is missing is infinite recursion with a friendly name.
* **Two events are asked rather than told.** :data:`PAINT` and :data:`HIT` are in
  :data:`EVENTS` so they can be registered and unregistered like anything else,
  but neither is fired through :meth:`fire`, because both exist for their *answer*
  - the ops a widget's face is made of, and the region a click is allowed to land
  in. They are dispatched from the widget's own slots (:meth:`_on_paint`,
  :meth:`_region`), which is the only place a return value has anywhere to go.

Nothing here owns a frame or a window. :class:`~crude.ui.context.UIContext` holds
one of these, and the runtime (S11) is what calls :meth:`Handlers.fire_frame` at the
point the plan's tick order puts ``ON_FRAME``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..errors import ExitRequest
from .base import Renderer, UIError
from .paint import Canvas, PaintList, in_region, region_of
from .skin import Skin
from .widget import Widget

__all__ = ["Handlers", "Handler", "HandlerError", "Link", "EVENTS",
           "WIDGET_EVENTS", "SCRIPT_EVENTS", "FRAME", "PAINT", "HIT",
           "MAX_HANDLER_ERRORS", "MAX_LINK_DEPTH", "event_name"]

#: A handler that keeps raising is muted after this many consecutive errors - the
#  same budget ``crude.pd.runtime`` gives an ``ON_PD`` handler, so "three strikes"
#  means one thing across the whole host surface.
MAX_HANDLER_ERRORS = 3

#: How far a link may chase its own echo. A two-way pair never reaches this (the
#  epsilon check stops it at one hop) so the number is a fuse for a *chain* -
#  ``a -> b -> c -> a`` - which would otherwise recurse until the stack gave out.
MAX_LINK_DEPTH = 4

#: the events a widget is told about, named as ``Widget`` fires them minus its
#  ``on_`` prefix, in the order the pointer's own methods call them
WIDGET_EVENTS = ("change", "enter", "leave", "move", "press", "release",
                 "click", "focus", "blur", "key", "char", "scroll", "drag",
                 "resize", "drop", "close")

#: not a widget event: it runs once per tick with the frame's seconds and belongs
#  to the context, so it has no widget to be keyed by
FRAME = "frame"

#: the two events a script asks of its own widget rather than reacts to. ``PAINT``
#  answers "what is this widget's face made of" and ``HIT`` answers "is the pointer
#  on the part of me that takes a click" - both are answered through the widget's
#  own slots, so they are deliberately *not* in :data:`WIDGET_EVENTS`, which is the
#  list the ``ON_*`` words of the catalog are generated from and the list a pointer
#  pass could be told to fire.
PAINT, HIT = "paint", "hit"
SCRIPT_EVENTS = (PAINT, HIT)

#: every name ``ON_*`` and ``OFF`` may use
EVENTS = WIDGET_EVENTS + (FRAME,) + SCRIPT_EVENTS

#: a frame handler's key - the one entry :data:`FRAME` is stored under
_NO_WIDGET = 0


class HandlerError(UIError):
    """A wiring mistake: an event nobody fires, a link to a widget with no such row."""


def event_name(fired: Any) -> str:
    """``"on_enter"`` as ``"enter"``, and anything already bare left alone.

    The tree fires ``on_*`` because that is the name of the method that fires it;
    a script says ``ON_ENTER``, and the table in between should not have to list
    both spellings. An unknown word is refused here rather than stored under a name
    that can never be looked up again.
    """
    said = str(fired or "").strip().lower()
    if said.startswith("on_"):
        said = said[3:]
    if said == "onchange":
        said = "change"
    if said not in EVENTS:
        raise HandlerError(f"'{fired}' is not an event this tree fires (it fires: "
                           f"{', '.join(EVENTS)})")
    return said


def _fit(func: Any, args: Tuple[Any, ...]) -> Tuple[Any, ...]:
    """The event's arguments, cut down to what ``func`` was written to take.

    ``arg_names`` is how a script function states its own arity, and this is the
    only place it is read - so ``FUNC(v)`` gets the value, ``FUNC(v, prop)`` gets
    the prop too, and ``FUNC()`` gets nothing, all from one registration table.
    A plain Python callable says nothing about its arity this way and is handed
    everything, which is what a test's ``lambda *said: ...`` expects.
    """
    declared = getattr(func, "arg_names", None)
    if not isinstance(declared, (list, tuple)) or len(declared) >= len(args):
        return args
    return tuple(args[:len(declared)])


def _shown(error: Any) -> str:
    """An error as one log line, in whatever words it offers."""
    text = getattr(error, "details", None)
    return str(text if text else error)


@dataclass
class Handler:
    """One registration: a widget, an event, and the callable that answers it.

    ``func`` is what was registered - a script function, or a Python callable - and
    is only ever compared by identity, which is how ``OFF(w, "change", fn)`` finds
    the one registration a script means. The counters are the plan's mute rule and
    the number ``crude run --stats`` has room for.
    """

    widget: Optional[Widget]
    event: str
    func: Callable[..., Any]
    calls: int = 0
    errors: int = 0
    muted: bool = False

    def __str__(self) -> str:
        where = self.widget.path() if self.widget is not None else "frame"
        said = f"{where} {self.event}"
        if self.muted:
            said += " (muted)"
        return f"{said} - {self.calls} call(s)"

    def __repr__(self) -> str:
        return f"<Handler {self.event} {self.widget!r}>"


@dataclass
class Link:
    """One ``LINK_*``: ``source.prop`` is kept equal to ``target.target_prop``.

    The row is named per end because a variant kind whose value row is called
    something else still wants to follow a slider, and one row is a fine name for
    the pair to be undone by. ``two_way`` is the plan's "mirrors it both ways"; a
    one-way link is the same record asked to run in only one direction.
    """

    source: Widget
    prop: str
    target: Widget
    target_prop: Optional[str] = None
    two_way: bool = True

    @property
    def near(self) -> str:
        """The row on the end a change arrived from, or ``""`` for neither."""
        return self.prop

    def other(self, widget: Widget, prop: str) -> Optional[Tuple[Widget, str]]:
        """The end to write, if ``(widget, prop)`` is the end that moved.

        ``None`` means this link does not speak for that row at all, which is the
        common case and costs one comparison.
        """
        if widget is self.source and (prop == self.prop or not self.prop):
            return self.target, self.target_prop or self.prop
        if (self.two_way and widget is self.target
                and prop == (self.target_prop or self.prop)):
            return self.source, self.prop
        return None

    def holds(self, widget: Widget) -> bool:
        """Whether this link is about that widget, either end."""
        return widget is self.source or (self.two_way and widget is self.target)

    def as_dict(self) -> Dict[str, Any]:
        return {"from": f"{self.source.path()}.{self.prop}",
                "to": f"{self.target.path()}.{self.target_prop or self.prop}",
                "two_way": self.two_way}

    def __str__(self) -> str:
        said = (f"{self.source.path()}.{self.prop} <-> "
                f"{self.target.path()}.{self.target_prop or self.prop}")
        return said if self.two_way else said.replace("<->", "->")

    def __repr__(self) -> str:
        return f"<Link {self}>"


class Handlers:
    """The registrations, the links, and the one dispatcher over both.

    Held by a :class:`~crude.ui.context.UIContext` as :attr:`UIContext.handlers`,
    which is what makes it per-window rather than per-process: a second project in
    the same interpreter gets a second set of lists and cannot fire into the first
    one's widgets.
    """

    def __init__(self, program: Any = None,
                 log: Optional[Callable[[str], Any]] = None,
                 *, name: str = "ui",
                 paints: Optional[PaintList] = None) -> None:
        #: whatever has ``invoke(func, args) -> (value, error)``; only registrations
        #  that are script functions are handed to it, and a plain Python callable is
        #  always called directly - by a test, and by the toolkit's own handlers
        self.program = program
        #: which window this bridge belongs to, so a log line says where it came from
        self.name = str(name or "ui")
        self.log = log or (lambda message: None)
        #: ``(widget uid, event) -> handlers``, in registration order
        self._by_key: Dict[Tuple[int, str], List[Handler]] = {}
        #: the three slots each adopted widget had before this bridge took them over
        self._adopted: Dict[int, Tuple[Optional[Callable], Optional[Callable],
                                       Optional[Callable]]] = {}
        self._links: List[Link] = []
        #: every script face's recorded ops, kept between frames. Owned here rather
        #  than by the context because invalidating one is what ``release`` does, and
        #  a widget leaving the tree is exactly the moment its face goes stale.
        self.paints = paints if paints is not None else PaintList(
            log=self.log, name=self.name)
        #: every message this bridge has said out loud, newest last - what a test
        #  asserts on and what ``crude run --stats`` counts
        self.errors: List[str] = []
        self.fired = 0
        self.muted = 0
        self._depth = 0
        #: the widget each running handler was registered on, outermost call first.
        #  A list rather than one slot because a handler may fire another - a
        #  painter called from inside a ``ON_FRAME`` - and the inner one is the
        #  sender while it runs. :meth:`sender` reads the end of it.
        self._senders: List[Optional[Widget]] = []

    # -- wiring ---------------------------------------------------------
    def attach_program(self, program: Any) -> None:
        """Bind this bridge to the program whose functions the handlers call.

        The convention :func:`crude.lang.program._attach_host` already follows for
        every subsystem hanging off a host, so a script's ``ON_CHANGE`` works
        whether the context was built by a runtime or by a test.
        """
        self.program = program
        return program

    def adopt(self, widget: Widget) -> Widget:
        """Take over one widget's slots; whatever was in the two event slots is kept.

        Idempotent, and remembered: a widget that is adopted twice does not fire its
        handlers twice, and the callable that was in the slot first - a kind's own
        behaviour, in S8 - runs *before* the registrations, so a script's
        ``ON_CLICK`` sees the value the click produced rather than the one it
        started from.

        The paint slot is taken over rather than chained, and the difference is the
        reason ``paint_face`` asks :meth:`_on_paint` instead of drawing a catalog
        face itself: a previous painter would have to run *and* be answered, which
        is two faces in one box. Every registration for :data:`PAINT` is looked up
        on the frame instead, and a widget with none draws its kind's own face.
        """
        held = self._adopted.get(widget.uid)
        if held is None:
            self._adopted[widget.uid] = (widget.on_event, widget.on_change,
                                         widget.on_paint)
            widget.on_event = self._on_event
            widget.on_change = self._changed
            widget.on_paint = self._on_paint
        return widget

    def release(self, widget: Widget) -> bool:
        """Give a widget its own slots back, and forget its registrations.

        What a rebuilt panel does to the widget it took out: the tree may still hold
        the object, and a handler that fires from a widget nobody is drawing any
        more is how a project leaks a script function into its next frame.
        """
        gone = self.remove(widget)
        # the face it painted is as good as gone too: a cache keyed on a widget that
        # is no longer answering to this bridge is a face waiting to be replayed
        # onto whatever reuses the box
        self.paints.invalidate(widget)
        held = self._adopted.pop(widget.uid, None)
        if held is None:
            return gone > 0
        widget.on_event, widget.on_change, widget.on_paint = held
        return gone > 0

    def add(self, widget: Optional[Widget], event: Any,
            func: Callable[..., Any]) -> Handler:
        """Register ``func`` for one event; the registration, so it can be undone.

        Several handlers per widget per event is the point of this class, and order
        is registration order: the plan's metronome sends to Pd before it clears the
        LED because that is the order the two lines are written in.
        """
        if func is None:
            raise HandlerError(f"'{event}' cannot be wired to nothing")
        name = event_name(event)
        if name == FRAME and widget is not None:
            raise HandlerError(f"'{widget.path()}' is not how a frame handler is "
                               f"registered; {FRAME} belongs to the window")
        if name != FRAME and widget is None:
            raise HandlerError(f"'{name}' needs the widget it is registered on")
        if widget is not None:
            self.adopt(widget)
        held = Handler(widget, name, func)
        self._by_key.setdefault(self._key(widget, name), []).append(held)
        return held

    def on_frame(self, func: Callable[..., Any]) -> Handler:
        """``ON_FRAME(fn)``: one handler per tick, at the plan's place in it."""
        return self.add(None, FRAME, func)

    def remove(self, widget: Any = None, event: Any = None,
               func: Callable[..., Any] = None) -> int:
        """Undo registrations; how many went.

        Anything left out widens the question - ``remove(w)``, ``remove(w,
        "click")``, ``remove(func=fn)`` - because a script that has torn a panel
        down wants one call, not a loop over the events it never wrote down.
        """
        held = None
        if widget is not None:
            held = widget if isinstance(widget, Widget) else None
            if held is None:
                raise HandlerError(f"'{widget}' is not a widget to unregister")
        wanted = event_name(event) if event is not None else None
        gone = 0
        for key in list(self._by_key):
            if wanted is not None and key[1] != wanted:
                continue
            if held is not None and key[0] != self._uid(held):
                continue
            keep = [row for row in self._by_key[key]
                    if func is not None and row.func is not func]
            if len(keep) == len(self._by_key[key]):
                continue
            gone += len(self._by_key[key]) - len(keep)
            if key[1] == PAINT:
                # a face recorded from a painter that is no longer registered is not
                # the face the widget would draw now, and ``OFF(w, "paint")`` means
                # the widget went back to its kind's own one
                self.paints.invalidate(key[0])
            if keep:
                self._by_key[key] = keep
            else:
                self._by_key.pop(key, None)
        return gone

    def off_frame(self, func: Callable[..., Any] = None) -> int:
        """Take away frame handlers; every one of them, or just ``func``."""
        return self.remove(None, FRAME, func)

    def handlers(self, widget: Any = None, event: Any = None
                 ) -> Tuple[Handler, ...]:
        """The registrations, in the order they were made and would be called.

        Asking for :data:`FRAME` with no widget means the frame's own list rather
        than every list there is: a frame handler has no widget to be keyed by, and
        ``OFF_FRAME`` and ``EVENTS`` both ask the question this answers.
        """
        wanted = event_name(event) if event is not None else None
        if widget is None and wanted == FRAME:
            held: Optional[int] = _NO_WIDGET
        else:
            held = self._uid(widget) if widget is not None else None
        out: List[Handler] = []
        for (uid, name), rows in self._by_key.items():
            if held is not None and uid != held:
                continue
            if wanted is not None and name != wanted:
                continue
            out.extend(rows)
        return tuple(out)

    def count(self, widget: Any = None, event: Any = None) -> int:
        """How many handlers are wired - one widget's, one event's, or all."""
        return len(self.handlers(widget, event))

    # -- the slots this module owns -------------------------------------
    def _on_event(self, widget: Widget, name: Any, *args: Any) -> None:
        """What :meth:`~crude.ui.widget.Widget._fire` calls: the tree's ``on_*``.

        The widget's own previous handler goes first when it had one, then every
        registration for the event, and an argument list the event does not have is
        not invented - a handler written for three gets the three the tree sent.

        A click is the one event that is asked about before it is told: an
        ``ON_HIT`` decides both whether the click belongs to this widget and what
        it is called, and its answer goes in *front* of the pointer's arguments -
        which is how the plan's stepper has an "up" half and a "down" half in one
        widget without putting two widgets in it.
        """
        said = str(name or "")
        before = self._adopted.get(widget.uid, (None, None, None))[0]
        if callable(before):
            before(widget, said, *args)
        found = event_name(said) if said else ""
        if not found or found == FRAME or found in SCRIPT_EVENTS:
            return
        if found == "click":
            wanted, region = self._region(widget, args)
            if not wanted:
                return
            self.fire(widget, found, region, *args)
            return
        self.fire(widget, found, *args)

    def _changed(self, widget: Widget, prop: Any, value: Any) -> None:
        """What :meth:`~crude.ui.widget.Widget._write` calls: one prop moved.

        Links first: the mirror is plumbing, and a script's handler should see the
        pair already agreeing, the way the plan's "drag the slider, the number box
        reads 30" is one gesture rather than two.
        """
        before = self._adopted.get(widget.uid, (None, None, None))[1]
        if callable(before):
            before(widget, prop, value)
        self._propagate(widget, str(prop), value)
        self.fire(widget, "change", value, prop)

    def _on_paint(self, widget: Widget, renderer: Renderer,
                  skin: Skin) -> int:
        """What :meth:`~crude.ui.widget.Widget.paint_face` calls: the widget's face.

        With nothing registered for :data:`PAINT`, the answer is the catalog face -
        which is the whole reason the slot can be installed on every adopted widget
        and not only on the script's own kinds: a slider adopting a bridge must go
        on looking like a slider. With registrations, their ops go through the
        :class:`~crude.ui.paint.PaintList`, so the script's Python runs once per
        change of face rather than once per frame.

        The number of ops drawn is the answer, because a frame's op count is what
        the context adds up; a painter that failed has already been logged and
        replaced with a placeholder, and no exception leaves here. A painter is
        handed ``(canvas, widget, "paint")``, so the plan's ``FUNC(p)`` and its
        ``FUNC(p, w)`` - the second being the one that reads a prop to decide what
        to draw - are both the same registration.
        """
        rows = self._by_key.get(self._key(widget, PAINT), ())
        if not rows:
            return widget.base_face(renderer, skin)

        def draw(canvas: Canvas) -> bool:
            """Run every registered painter; ``False`` if any of them could not.

            All of them run even after one fails, because a muted handler must still
            be counted as a call for the three-strike rule to move, and a half-drawn
            list is thrown away by the caller rather than cached.
            """
            done = True
            for handler in list(rows):
                ok, _answer = self._call(handler, (canvas, widget, PAINT))
                if not ok:
                    done = False
            return done

        return self.paints.paint(widget, renderer, skin, draw)

    def _region(self, widget: Widget,
                args: Tuple[Any, ...]) -> Tuple[bool, Any]:
        """Whether a click is wanted, and the region to hand ``ON_CLICK`` for it.

        The point is moved into the widget's own coordinates before the script is
        asked, so the numbers a hit handler answers with are the numbers its painter
        drew with - and the rest of the click's arguments are left exactly as the
        tree sent them, because ``ON_CLICK`` has no reason to re-check a region it
        was just told.

        With no registration the answer is ``(True, None)``: the whole box, no name,
        which is what a plain button's ``ON_CLICK`` gets as a ``NULL`` it can ignore.
        Every registration is asked, and any one of them may refuse; the last that
        answered something specific is the region that gets handed on, so a widget
        can have a general ``ON_HIT`` and a particular one registered after it.

        A handler that failed - raised, or muted - is skipped rather than being
        allowed to veto: a bug in a script's hit handler is not a reason a control
        stops working, and it has already been logged.
        """
        rows = self._by_key.get(self._key(widget, HIT), ())
        if not rows:
            return True, None
        left, top = widget.screen_box[0], widget.screen_box[1]
        x = float(args[0]) if len(args) > 0 else 0.0
        y = float(args[1]) if len(args) > 1 else 0.0
        button = args[2] if len(args) > 2 else 0
        said: Any = None
        for handler in list(rows):
            ok, answer = self._call(handler, (x - left, y - top, button, HIT))
            if not ok:
                continue
            region = region_of(answer)
            if not in_region(region, x, y, widget):
                return False, None
            if region is not None:
                said = region
        return True, said

    # -- firing ---------------------------------------------------------
    def fire(self, widget: Optional[Widget], event: Any, *args: Any) -> int:
        """Call every handler for one event; how many answered.

        Public because a script's own code fires events too - a step sequencer
        telling its rows to blink - and refused for :data:`SCRIPT_EVENTS`, whose
        answer is the point of asking and cannot be returned through a count.
        """
        found = event_name(event)
        if found in SCRIPT_EVENTS:
            raise HandlerError(f"'{found}' is not fired: the tree asks it and draws "
                               f"with the answer, so it runs on the widget's own "
                               f"slot rather than through fire()")
        held = widget if found != FRAME else None
        if found != FRAME and widget is None:
            raise HandlerError(f"'{found}' needs the widget to fire it on")
        rows = self._by_key.get(self._key(held, found), ())
        done = 0
        for handler in list(rows):
            if self._invoke(handler, args + (found,)):
                done += 1
        return done

    def fire_frame(self, dt: float) -> int:
        """Run the ``ON_FRAME`` handlers with this tick's seconds; how many did."""
        return self.fire(None, FRAME, float(dt))

    def sender(self) -> Optional[Widget]:
        """The widget the handler now running was registered on, or ``None``.

        :meth:`fire` hands a handler its value, its prop and its event, and never
        the widget - a ``FUNC(v)`` on a slider and a ``FUNC(v)`` on a knob are told
        the same way. A script that builds one row out of one function therefore
        has four identical handlers and no way to say which row made the call, so
        the widget the registration belongs to is kept on a stack for the length of
        the call and answered here. Outside any handler the answer is ``None``,
        which is the truth rather than a guess, and a frame handler's is too: it
        was registered on no widget.
        """
        return self._senders[-1] if self._senders else None

    def _invoke(self, handler: Handler, args: Tuple[Any, ...]) -> bool:
        """One call, answered with whether it worked - the count ``fire`` keeps."""
        return self._call(handler, args)[0]

    def _call(self, handler: Handler,
              args: Tuple[Any, ...]) -> Tuple[bool, Any]:
        """One call, with the plan's mute rule bolted on, and its return value.

        A Python exception is caught as well as a language error, because a handler
        that raises out of :meth:`fire` would leave a frame half-drawn and take the
        window down with it; the failure becomes a log line and a counter instead,
        which is what "warn, do not fail" means at this layer. The value is only
        ever used by the two asking events; a told one throws it away.
        """
        handler.calls += 1
        self.fired += 1
        if handler.muted:
            return False, None

        said = _fit(handler.func, args)
        value: Any = None
        error: Any = None
        invoke = getattr(self.program, "invoke", None)
        # two kinds of registration reach here, and the choice between them is a
        # fact about the callable, not a fact about whether a script is attached:
        # a script's ``FUNC`` can only be run by the interpreter, and a host
        # callable - a settings dialog watching its window - can only be run here.
        # Asking the program first handed the host function to ``invoke`` in every
        # real run, and ``invoke`` answers "function is not callable" for anything
        # with no ``execute``, so the dialog stopped following the window and the
        # bridge logged a bug that was its own rather than the project's.
        script = hasattr(handler.func, "execute")
        # the widget this registration belongs to, for the length of the call only:
        # ``SENDER()`` reads the end of the stack, and a handler that fires another
        # sees the inner registration while it runs and this one afterwards
        self._senders.append(handler.widget)
        try:
            if script and callable(invoke):
                value, error = invoke(handler.func, list(said))
            elif callable(handler.func):
                value = handler.func(*said)
        except ExitRequest:
            # ``EXIT`` from a widget handler - a menu's Quit row, a button on a stop
            # page - is the script asking the run to end, which is the one failure a
            # frame is not allowed to absorb: counted as an error here it would mute
            # the row that asked, log a bug that never happened, and leave a window
            # that will not close. The ``finally`` still puts the sender stack back.
            raise
        except Exception as exc:  # a script handler must not outlive the frame
            error = exc
        finally:
            self._senders.pop()

        if error is None:
            handler.errors = 0
            return True, value

        handler.errors += 1
        message = (f"{self.name}: ON_{handler.event.upper()} "
                   f"{handler.widget.path() if handler.widget is not None else 'frame'}"
                   f" failed: {_shown(error)}")
        self.errors.append(message)
        self.log(message)
        if handler.errors >= MAX_HANDLER_ERRORS and not handler.muted:
            handler.muted = True
            self.muted += 1
            self.log(f"{self.name}: ON_{handler.event.upper()} muted after "
                     f"{MAX_HANDLER_ERRORS} consecutive errors")
        return False, None

    # -- links ----------------------------------------------------------
    def link(self, source: Widget, prop: str, target: Widget,
             target_prop: str = None, two_way: bool = True) -> Link:
        """Keep one widget's row equal to another's; the link, for ``UNLINK``.

        Both ends are checked against their own kind's table now, so a script that
        links a ``text``'s ``value`` is told at the wiring line rather than at the
        first drag - and a row the target may not be written to (the pointer's four)
        is refused there, which is the same refusal ``SET`` gets.
        """
        name = str(prop or "").strip().lower()
        near = str(target_prop or name).strip().lower()
        if not name:
            raise HandlerError("a link needs the prop to follow")
        if source is target and name == near:
            raise HandlerError(f"'{source.path()}.{name}' cannot be linked to "
                               "itself")
        for held, asked, which in ((source, name, "first"), (target, near, "second")):
            try:
                held.kind.settable(asked)
            except UIError as exc:
                raise HandlerError(f"the {which} widget of this link: {exc}") from None
        # both ends have to be watched for the mirror to be written at all, and the
        # target is watched too because a two-way link reads back from it
        self.adopt(source)
        self.adopt(target)
        held = Link(source, name, target, near, bool(two_way))
        self._links.append(held)
        return held

    def unlink(self, widget: Any = None, prop: str = None) -> int:
        """Undo links; how many went. Anything left out widens the question."""
        held = widget if isinstance(widget, Widget) else None
        if widget is not None and held is None:
            raise HandlerError(f"'{widget}' is not a widget to unlink")
        name = str(prop).strip().lower() if prop else None
        keep = [row for row in self._links
                if not ((held is None or row.holds(held))
                        and (name is None or name in (row.prop,
                                                      row.target_prop or row.prop)))]
        gone = len(self._links) - len(keep)
        self._links = keep
        return gone

    def links(self, widget: Any = None) -> Tuple[Link, ...]:
        """Every link, or the ones that mention one widget."""
        held = widget if isinstance(widget, Widget) else None
        if held is None and widget is not None:
            raise HandlerError(f"'{widget}' is not a widget to look links up on")
        return tuple(row for row in self._links
                     if held is None or row.holds(held))

    def _propagate(self, widget: Widget, prop: str, value: Any) -> None:
        """Write the other end of every link this row is one side of."""
        if self._depth >= MAX_LINK_DEPTH:
            # a chain that came back round to the start: stop, say so once, and
            # leave the values the person is looking at rather than a crash
            if self._depth == MAX_LINK_DEPTH:
                message = (f"{self.name}: links stopped at depth {MAX_LINK_DEPTH} on "
                           f"'{widget.path()}.{prop}': a link chain that returns "
                           "to where it started is not followed further")
                self.errors.append(message)
                self.log(message)
            return
        self._depth += 1
        try:
            for held in list(self._links):
                other = held.other(widget, prop)
                if other is None:
                    continue
                target, name = other
                try:
                    # ``Widget.set`` answers "did it move", which is the epsilon
                    # check: a mirror that is already right does not write, so it
                    # does not fire, so a two-way pair settles in one hop
                    target.set(name, value)
                except UIError as exc:
                    message = f"{self.name}: link {held} failed: {exc}"
                    self.errors.append(message)
                    self.log(message)
        finally:
            self._depth -= 1

    # -- describing -----------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        """The numbers a report prints about what is wired up."""
        return {"handlers": sum(len(rows) for rows in self._by_key.values()),
                "keys": len(self._by_key), "links": len(self._links),
                "adopted": len(self._adopted), "fired": self.fired,
                "muted": self.muted, "errors": len(self.errors),
                "cached_paints": len(self.paints)}

    def __str__(self) -> str:
        said = (f"Handlers({self.name}: {self.count()} handler(s) on "
                f"{len(self._adopted)} widget(s), {len(self._links)} link(s)")
        if self.fired:
            said += f", {self.fired} fired"
        if len(self.paints):
            said += f", {len(self.paints)} painted face(s)"
        if self.errors:
            said += f", {len(self.errors)} error(s)"
        return said + ")"

    def __repr__(self) -> str:
        return f"<Handlers {self.name!r} {self.count()} handler(s)>"

    # -- the small internals --------------------------------------------
    @staticmethod
    def _uid(widget: Any) -> int:
        """A widget's key, or ``0`` for the frame."""
        if isinstance(widget, Widget):
            return widget.uid
        return _NO_WIDGET

    @staticmethod
    def _key(widget: Optional[Widget], event: str) -> Tuple[int, str]:
        return (Handlers._uid(widget), event)
