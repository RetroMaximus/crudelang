"""What the person is doing, worked out from what GLFW says they did.

A GLFW callback fires while events are being pumped, in the middle of the frame
that asked for them, so the rule here is: **callbacks only queue**. The widget
layer gets its events in :meth:`InputRouter.dispatch`, once per frame, at the
point the plan's tick order reserves for it - after ``ON_FRAME`` and before the
draw. That ordering is the whole reason a handler can read a widget's value that
was set earlier in the same frame and see something consistent.

The dispatch chain is the plan's, in order: hover, hit test, focus, widget
handlers. Each raw event resolves to a target through a ``hit_test`` callable the
widget layer supplies (in a test, a dict lookup), and the target is any object
with the matching ``on_*`` methods - missing ones are simply not called, which is
what lets a label take a hover and a knob take a drag without either declaring a
protocol.

Coordinates are logical pixels, the same space the window and the renderer are
in, because GLFW's cursor and its client size are already the same numbers; the
framebuffer size changes nothing here. What the router adds is the meaning:

* a **press** focuses and captures, so the drag belongs to the widget the pointer
  started on even when it wanders off the edge - which is how a slider can be
  dragged from anywhere;
* a **release** over the same widget is a **click**;
* a **scroll** is a coarse drag, and **Ctrl** makes a drag fine - the plan's
  answer to a 0..1 parameter that is hard to land on with a 40 pixel widget;
* a **key** goes to the hotkeys first and then to whatever has focus, and a
  **character** goes to the focused widget alone, layout and shift already
  resolved by GLFW.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

__all__ = ["InputRouter", "InputEvent", "KEY_NAMES", "MODS", "ACTIONS",
           "MOUSE_BUTTONS", "BUTTON_NAMES", "key_name", "button_name",
           "is_fine", "fine_delta", "DRAG_FINE"]

#: GLFW's own action numbers, fixed by its API rather than by this file
ACTIONS = {"release": 0, "press": 1, "repeat": 2}
#: and its modifier bits
MODS = {"shift": 0x0001, "control": 0x0002, "alt": 0x0004, "super": 0x0008}
#: the three buttons a widget can be clicked with
MOUSE_BUTTONS = {"left": 0, "right": 1, "middle": 2}
#: what a logged event calls a button
BUTTON_NAMES = {0: "left", 1: "right", 2: "middle", 3: "button 3",
                4: "button 4", 5: "button 5", 6: "button 6", 7: "button 7",
                8: "button 8"}

#: one wheel notch moves a coarse drag this many logical pixels
SCROLL_NOTCH = 16.0
#: Ctrl-drag, and Ctrl-scroll, divide the movement by this
DRAG_FINE = 8.0

#: the keys that have no character of their own. These are GLFW's numbers, which
#: its API fixes, so a machine with no GLFW installed can still describe an event
#: a test faked - and :func:`key_name` falls back to ``chr`` for the printable
#: range rather than pretending not to know a letter. ``test_input_router`` reads
#: the lot back off an installed GLFW, because a table copied by hand drifts by
#: one the moment a name is added at the top of it.
KEY_NAMES: Dict[int, str] = {
    256: "escape", 257: "enter", 258: "tab", 259: "backspace",
    260: "insert", 261: "delete", 262: "right", 263: "left", 264: "down",
    265: "up", 266: "page up", 267: "page down", 268: "home", 269: "end",
    280: "caps lock", 281: "scroll lock", 282: "num lock",
    283: "print screen", 284: "pause",
    335: "keypad enter",
    340: "shift", 341: "control", 342: "alt", 343: "super",
    344: "shift", 345: "control", 346: "alt", 347: "super",
}
for _index in range(12):  # F1..F12, the hotkey row
    KEY_NAMES[290 + _index] = f"f{_index + 1}"
for _index in range(10):  # the keypad digits GLFW numbers separately
    KEY_NAMES[320 + _index] = f"keypad {chr(ord('0') + _index)}"


def key_name(code: int) -> str:
    """One GLFW key as the word a ``.crude`` handler compares against."""
    found = KEY_NAMES.get(int(code))
    if found:
        return found
    number = int(code)
    if 32 <= number < 127:
        return chr(number).lower()
    return f"key {number}"


def button_name(button: int) -> str:
    return BUTTON_NAMES.get(int(button), f"button {int(button)}")


def is_fine(mods: int) -> bool:
    """Ctrl held: the movement is meant to be small and deliberate."""
    return bool(int(mods) & MODS["control"])


def fine_delta(dx: float, dy: float, mods: int) -> Tuple[float, float]:
    """A drag's movement, divided down when Ctrl says the person is being careful.

    The factor is applied here rather than in each widget so that every knob,
    slider and pad in a build slows down by the same amount on the same keypress.
    """
    if is_fine(mods):
        return (float(dx) / DRAG_FINE, float(dy) / DRAG_FINE)
    return (float(dx), float(dy))


@dataclass(frozen=True)
class InputEvent:
    """One queued thing that happened, before anyone has been told about it."""

    name: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def __str__(self) -> str:
        return f"InputEvent({self.name}, {dict(self.params)})"


class InputRouter:
    """The queue GLFW fills, and the one pass that turns it into widget calls."""

    def __init__(self, hit_test: Optional[Callable[[float, float], Any]] = None,
                 *, width: float = 0.0, height: float = 0.0,
                 hotkeys: Optional[Dict[str, Callable[..., Any]]] = None,
                 root: Any = None,
                 scroll_owner: Optional[Callable[[Any], Any]] = None) -> None:
        #: where the pointer is, in logical pixels, y down from the window's top
        self.x = 0.0
        self.y = 0.0
        self.width = float(width)
        self.height = float(height)
        self.hit_test = hit_test or (lambda _x, _y: None)
        #: the widget under the pointer with no button down
        self.hover: Any = None
        #: the widget that took the last press, and gets keys and characters
        self.focus: Any = None
        #: the widget a drag belongs to, whoever the pointer is currently over
        self.capture: Any = None
        #: ``widget -> the widget the wheel belongs to``, given the one under the
        #  pointer. The layer that knows which boxes can slide installs this (a
        #  :class:`~crude.ui.context.UIContext` does); left out, the wheel goes
        #  where every other event goes - straight to whatever was hit.
        self.scroll_owner = scroll_owner
        #: ``"f1"``, ``"control+s"`` -> callable, filled by ``ON_KEY``, which is
        #  the layer that may accept a shorter spelling and normalise it to this
        self.hotkeys: Dict[str, Callable[..., Any]] = dict(hotkeys or {})
        #: the UI root, asked last so a global handler can see an ignored event
        self.root = root
        self.queue: List[InputEvent] = []
        #: every dispatch decision made, in order: ``(event, name, target)``
        self.log: List[Tuple[InputEvent, str, Any]] = []
        #: which buttons are down right now, so a stray release is not a drag end
        self.buttons: Set[int] = set()
        self.mods = 0
        self._dpr = 1.0
        #: the window asked whether it may go: ``True`` once nothing objects
        self.close_allowed = False
        #: the answer the last request gave, kept for the dispatch that records it
        self._refused = False
        self.events_seen = 0
        self.dispatched = 0

    # -- GLFW's side: queue and say nothing -------------------------------
    def on_cursor(self, x: float, y: float) -> None:
        """A cursor position, in logical pixels - the window's own space."""
        self.queue.append(InputEvent("move", {"x": float(x), "y": float(y)}))

    def on_button(self, button: int, action: int, mods: int) -> None:
        """A button, wherever the pointer happens to be *when it is answered*.

        The position is deliberately not snapshotted here: GLFW sends a cursor move
        and the press that followed it in the same poll, so a press that carried the
        position from the start of the frame would be hit-tested against a place the
        pointer had already left - and a click that lands somewhere other than where
        the cursor is drawn is a bug nobody can reproduce on purpose.
        """
        self.mods = int(mods)
        self.queue.append(InputEvent("button", {
            "button": int(button), "action": int(action), "mods": int(mods)}))

    def on_key(self, key: int, scancode: int, action: int, mods: int) -> None:
        self.mods = int(mods)
        self.queue.append(InputEvent("key", {
            "key": int(key), "name": key_name(key), "scancode": int(scancode),
            "action": int(action), "mods": int(mods)}))

    def on_char(self, character: str) -> None:
        """A character that survived the keyboard layout: text, not a key code."""
        text = str(character)
        if text:
            self.queue.append(InputEvent("char", {"text": text}))

    def on_scroll(self, dx: float, dy: float) -> None:
        """The wheel at the pointer's dispatch-time position, as a press is."""
        self.queue.append(InputEvent("scroll", {
            "dx": float(dx), "dy": float(dy), "mods": self.mods}))

    def on_resize(self, width: int, height: int) -> None:
        self.queue.append(InputEvent("resize", {
            "width": float(width), "height": float(height), "dpr": self.dpr}))

    def on_framebuffer_resize(self, width: int, height: int) -> None:
        self.queue.append(InputEvent("framebuffer", {
            "width": float(width), "height": float(height)}))

    def on_drop(self, paths: List[str]) -> None:
        self.queue.append(InputEvent("drop", {"paths": tuple(paths)}))

    def on_close_request(self) -> Optional[bool]:
        """Remember the request, and let the window go unless something objects.

        Returning ``False`` here is how a project keeps its window open - a
        confirm dialog, or a shutdown that has to finish first. Everything else,
        including a splash still drawing, answers ``None``: the close proceeds.

        The answer is also stored, because :attr:`close_allowed` is written by the
        dispatch rather than by this call: a router that recorded "a close was
        dispatched" as "a close was allowed" would tell a log that an objecting
        root let the window go.
        """
        self.queue.append(InputEvent("close", {}))
        handler = getattr(self.root, "on_close_request", None)
        self._refused = callable(handler) and handler() is False
        return False if self._refused else None

    # -- the widget layer's side ------------------------------------------
    @property
    def dpr(self) -> float:
        """Device pixels per logical pixel; a framebuffer event sets it."""
        return self._dpr

    def target_at(self, x: float, y: float) -> Any:
        """The topmost widget at this point - hit testing is the widget layer's."""
        return self.hit_test(float(x), float(y))

    def _call(self, target: Any, name: str, *args: Any) -> bool:
        """Ask one target for one handler, and call it if it has one."""
        if target is None:
            return False
        handler = getattr(target, name, None)
        if not callable(handler):
            return False
        handler(*args)
        return True

    def rehover(self) -> bool:
        """Ask again what the pointer is over; whether the answer changed.

        The draw pass calls this after the layout, because a layout moved the tree
        under a cursor that never moved: a resize, a stack that re-ran, a widget
        added between two frames. Without it a button that grew into the pointer
        stays unlit, and the lit one is somewhere else on the screen, until the
        person shakes the mouse to find out - and a hover is a fact about where the
        pointer is, not about when it last moved.
        """
        before = self.hover
        self._hover(self.target_at(self.x, self.y), InputEvent("hover", {}))
        return self.hover is not before

    def forget(self, widget: Any) -> bool:
        """Take a widget - and anything inside it - out of hover, focus and capture.

        A router that kept pointing at a removed widget would type into a widget
        that is no longer in the window and finish a drag on a box that has been
        freed. The tree is the truth about what exists; this is how the draw pass
        tells the router that something stopped existing.
        """
        gone = False
        for name in ("hover", "focus", "capture"):
            held = getattr(self, name)
            if held is None:
                continue
            inside = held is widget or (callable(
                getattr(held, "is_in", None)) and held.is_in(widget))
            if inside:
                setattr(self, name, None)
                gone = True
        return gone

    def _log(self, event: InputEvent, name: str, target: Any) -> None:
        self.log.append((event, name, target))

    def _drain(self) -> List[InputEvent]:
        """Take the queue out, with a run of cursor moves cut down to its last.

        A 1000 Hz mouse can offer sixteen positions to a 60 fps frame, and the
        fifteenth hover test is not information: the final position is, and the
        drag wants the total movement from the first one to the last.
        """
        events = self.queue
        self.queue = []
        kept: List[InputEvent] = []
        for event in events:
            if (event.name == "move" and kept and kept[-1].name == "move"):
                kept[-1] = event
            else:
                kept.append(event)
        return kept

    def dispatch(self) -> int:
        """Hand everything queued since last time to the widgets; how many went."""
        events = self._drain()
        self.events_seen += len(events)
        for event in events:
            self._one(event)
        self.dispatched += len(events)
        return len(events)

    def _one(self, event: InputEvent) -> None:
        name = event.name
        if name == "move":
            self._moved(event)
        elif name == "button":
            self._button(event)
        elif name == "key":
            self._key(event)
        elif name == "char":
            self._text(event)
        elif name == "scroll":
            self._scrolled(event)
        elif name == "resize":
            self._resized(event)
        elif name == "framebuffer":
            self._dpr = (float(event.get("width") or 0) / self.width
                         if self.width else 1.0)
            self._log(event, "framebuffer", None)
        elif name == "drop":
            self._dropped(event)
        elif name == "close":
            self.close_allowed = not self._refused
            self._log(event, "close", None)

    def _dropped(self, event: InputEvent) -> None:
        """A dropped file is the widget under the pointer's, and the window's.

        GLFW's drop callback carries paths and no position, so the pointer is
        wherever the router last left it - the same reading a hover between two
        moves uses, and the only place an ``ON_DROP(w, fn)`` can be aimed at.
        The widget answers first and the window is asked as well, because a
        project that opens a file dropped *anywhere* has no widget to thank for
        it, and a clip that wants only the drops that land on it must not have
        to argue with the window about who the file belongs to.
        """
        paths = event.get("paths", ())
        target = self.target_at(self.x, self.y)
        if target is not None and target is not self.root:
            self._call(target, "on_drop", paths)
            self._log(event, "drop", target)
        self._call(self.root, "on_drop", paths)
        if self.root is not None:
            self._log(event, "drop", self.root)

    def _moved(self, event: InputEvent) -> None:
        x, y = float(event.get("x", self.x)), float(event.get("y", self.y))
        dx, dy = x - self.x, y - self.y
        self.x, self.y = x, y
        if self.capture is not None:
            # the drag is the widget's, not the pointer's: it keeps it after the
            # cursor leaves the box, and it is told the movement, not the position
            moved = fine_delta(dx, dy, self.mods)
            self._call(self.capture, "on_drag", moved[0], moved[1], self.mods)
            self._log(event, "drag", self.capture)
        self._hover(self.target_at(x, y), event, x=x, y=y)

    def _hover(self, hovered: Any, event: InputEvent, *, x: Optional[float] = None,
               y: Optional[float] = None) -> None:
        """Move the hover from one widget to another, saying so to both.

        Called for a pointer that moved *and* for a release that changed what is
        under the pointer without the pointer moving - a drag that ended somewhere
        else has to leave the widget it was dragging, or a second widget lights up.
        """
        if hovered is not self.hover:
            left, self.hover = self.hover, hovered
            self._call(left, "on_leave")
            self._call(hovered, "on_enter")
            # a widget that does not exist is not something that was left or
            # entered: a pointer outside every box has not moved between two
            if left is not None:
                self._log(event, "leave", left)
            if hovered is not None:
                self._log(event, "enter", hovered)
        if x is not None and hovered is not None:
            self._call(hovered, "on_move", x, y)
            self._log(event, "move", hovered)

    def _button(self, event: InputEvent) -> None:
        button = int(event.get("button", 0))
        action = int(event.get("action", ACTIONS["press"]))
        mods = int(event.get("mods", self.mods))
        x = float(event.get("x", self.x))
        y = float(event.get("y", self.y))
        if action == ACTIONS["press"]:
            self.buttons.add(button)
            target = self.target_at(x, y)
            if target is not self.focus:
                previous, self.focus = self.focus, target
                self._call(previous, "on_blur")
                self._call(target, "on_focus")
                if previous is not None:
                    self._log(event, "blur", previous)
                if target is not None:
                    self._log(event, "focus", target)
            self.capture = target
            self._call(target, "on_press", x, y, button, mods)
            self._log(event, "press", target)
        else:
            if not self.buttons:
                # a release with nothing down is a stray, but the pointer still
                # has to know what it is over, so fall through to the hover pass
                self.capture = None
            else:
                self.buttons.discard(button)
                dragged = self.capture
                self.capture = None
                target = self.target_at(x, y)
                self._call(dragged, "on_release", x, y, button, mods)
                self._log(event, "release", dragged)
                if target is dragged and dragged is not None:
                    # a click is a press and a release on the same widget, which is
                    # not the same event as either of them
                    self._call(dragged, "on_click", x, y, button)
                    self._log(event, "click", dragged)
        if self.capture is None:
            # whatever the pointer is over may have changed under it: a drag that
            # ended somewhere else has to leave the widget it was dragging. No
            # position is offered with it - a release is not a second move
            self._hover(self.target_at(x, y), event)

    def _key(self, event: InputEvent) -> None:
        name = str(event.get("name", ""))
        mods = int(event.get("mods", self.mods))
        action = int(event.get("action", ACTIONS["press"]))
        combo = self.combo(name, mods)
        if action == ACTIONS["release"]:
            return  # a key is one event, not two: only the press is a press
        handler = self.hotkeys.get(combo)
        if handler is None and not mods:
            # a hotkey given as a bare name means that key on its own: Ctrl+S is a
            # different command, and must not fire the one bound to plain S
            handler = self.hotkeys.get(name)
        if handler is not None:
            handler()
            self._log(event, "hotkey", combo)
            return
        self._call(self.focus, "on_key", name, mods)
        if self.focus is not None:
            self._log(event, "key", self.focus)

    def _text(self, event: InputEvent) -> None:
        self._call(self.focus, "on_char", str(event.get("text", "")))
        if self.focus is not None:
            self._log(event, "text", self.focus)

    def _scrolled(self, event: InputEvent) -> None:
        mods = int(event.get("mods", self.mods))
        x = float(event.get("x", self.x))
        y = float(event.get("y", self.y))
        target = self.target_at(x, y)
        steps = (float(event.get("dx", 0.0)) * SCROLL_NOTCH,
                 float(event.get("dy", 0.0)) * SCROLL_NOTCH)
        moved = fine_delta(steps[0], steps[1], mods)
        owner = target
        if self.scroll_owner is not None:
            # a wheel is the one gesture that is not necessarily the hit widget's:
            # the pointer is on a *row*, and the thing that moves is the box the row
            # is inside. Whoever was given the question knows the answer.
            owner = self.scroll_owner(target)
            if owner is None:
                owner = target
        if not self._call(owner, "on_scroll", moved[0], moved[1], mods):
            # no wheel handler is not no answer: a wheel is a coarse drag, and a
            # widget that understands a drag understands a scroll already
            self._call(owner, "on_drag", moved[0], moved[1], mods)
            if owner is not None:
                self._log(event, "scroll-as-drag", owner)
        else:
            self._log(event, "scroll", owner)

    def _resized(self, event: InputEvent) -> None:
        self.width = float(event.get("width", self.width))
        self.height = float(event.get("height", self.height))
        # the ratio is the framebuffer's to set and this one's to leave alone: a
        # resize that carries a stale snapshot must not undo a measurement
        self._call(self.root, "on_resize", self.width, self.height)
        if self.root is not None:
            self._log(event, "resize", self.root)

    # -- the words handlers use -------------------------------------------
    @staticmethod
    def combo(name: str, mods: int) -> str:
        """``"control+s"``, in a fixed modifier order, for the hotkey table."""
        parts = []
        for key in ("control", "alt", "shift", "super"):
            if int(mods) & MODS[key]:
                parts.append(key)
        parts.append(str(name))
        return "+".join(parts)

    def press(self, button: int = MOUSE_BUTTONS["left"], *,
              mods: int = 0) -> None:
        """A click from a test or a script: the same path GLFW would take."""
        self.on_button(button, ACTIONS["press"], mods)
        self.dispatch()

    def release(self, button: int = MOUSE_BUTTONS["left"], *,
                mods: int = 0) -> None:
        self.on_button(button, ACTIONS["release"], mods)
        self.dispatch()

    def move_to(self, x: float, y: float) -> None:
        """Point at ``x, y`` and let hover and drag see it, in one dispatch."""
        self.on_cursor(x, y)
        self.dispatch()

    def click(self, x: float, y: float,
              button: int = MOUSE_BUTTONS["left"], *, mods: int = 0) -> Any:
        """Move, press and release at one point - what a person does in one go."""
        self.move_to(x, y)
        self.on_button(button, ACTIONS["press"], mods)
        self.on_button(button, ACTIONS["release"], mods)
        self.dispatch()
        return self.target_at(x, y)

    def type_text(self, text: str) -> None:
        """Every character of ``text`` to the focused widget, in order."""
        for character in str(text):
            self.on_char(character)
        self.dispatch()

    def key_typed(self, name: str, *, mods: int = 0) -> None:
        """One named key, by the word a handler compares against."""
        codes = {value: code for code, value in KEY_NAMES.items()}
        code = codes.get(str(name))
        if code is None and len(str(name)) == 1:
            code = ord(str(name).upper())
        if code is None:
            raise ValueError(f"'{name}' is not a key this router knows")
        self.on_key(code, 0, ACTIONS["press"], mods)
        self.dispatch()

    def scroll_at(self, dy: float, *, mods: int = 0) -> None:
        """The wheel, in notches, at the pointer's current position."""
        self.mods = int(mods)
        self.on_scroll(0.0, float(dy))
        self.dispatch()

    def reset(self) -> None:
        """Forget the queue and the log: a test's boundary between two frames."""
        self.queue = []
        self.log = []
        self.buttons = set()
        self.capture = None

    def names(self) -> List[str]:
        """What the last dispatch decided, in order - the recorder's view."""
        return [entry[1] for entry in self.log]

    def hits(self, name: str) -> List[Any]:
        """Every target an action called ``name`` was sent to."""
        return [entry[2] for entry in self.log if entry[1] == name]

    def stats(self) -> Dict[str, Any]:
        return {"events": self.events_seen, "dispatched": self.dispatched,
                "queued": len(self.queue), "hover": self.hover is not None,
                "focus": self.focus is not None,
                "dragging": self.capture is not None,
                "hotkeys": len(self.hotkeys),
                "cursor": (round(self.x, 2), round(self.y, 2))}

    def __str__(self) -> str:
        return (f"InputRouter({len(self.queue)} queued, hover="
                f"{self.hover!r}, focus={self.focus!r}, "
                f"drag={'yes' if self.capture is not None else 'no'})")
