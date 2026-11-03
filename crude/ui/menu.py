"""An in-window menu bar, composed entirely from ordinary widgets.

There is no native menu here and no new widget face: GLFW windows in this
project expose none, so a running program's only menu is one built out of the
catalog the script already has - ``PANEL``s for the bar and each popup,
``BUTTON``s (or ``TOGGLE``s for a checkable row) for the items, and the
``visible`` prop for whether a popup is showing. Everything in this module is
that composition, run through :class:`~crude.ui.context.UIContext` exactly as a
script would drive it by hand, which is why it needs no renderer, no window and
no GPU to be tested.

The ideas the plan names, one class each:

* :class:`MenuBar` - a strip along the top of the window holding the menu
  titles. It owns the "only one popup open at a time" rule.
* :class:`Menu` - one title on the bar plus the hidden popup that hangs under
  it. Clicking the title toggles the popup; the popup is a child of the window's
  root rather than of the bar so it can overlap the project's own widgets.
* :class:`MenuItem` - one row in a popup. Clicking it hides the popup and then
  does whatever the row was for.
* :class:`MenuDialog` - a settings panel hung off a menu item: a title row at
  the top, then fields (a ``DROPDOWN`` for a choice, a ``NUMBER`` for an editable
  value, a ``TOGGLE`` for a switch, a grid of quick buttons, a scrollable list of
  per-device toggles), then the action buttons - ``Apply`` plus whatever else the
  bindings ask for (``Rescan``, ``Cancel``). Opening it re-reads the fields from
  a host-side provider, and applying hands the picked values to a host-side
  callback - so a configuration row that lists the machine's audio or MIDI
  ports, or edits a serial baud, is still nothing but ordinary widgets composed
  here and wired to the runtime by the bindings.

How a click is handled is the one non-obvious part, and it follows the layer
beneath rather than fighting it. :mod:`crude.ui.behaviour` already owns every
interactive widget's ``on_event`` slot (that is what makes a ``toggle`` latch and
a ``button`` bounce), so a menu must *chain* onto that slot, never replace it:
:class:`MenuItem` saves the behaviour it found and calls it before doing its own
work, so a checkable row still flips its value. The row's script callback is then
registered on top of that chain through the context's :class:`~crude.ui.handlers.Handlers`
bridge (``handlers.add(button, "click", fn)``), which the bridge runs *after* the
``on_event`` chain - so the popup is already hidden by the time the project's
function is called.

The language-facing half is :mod:`crude.bindings.menu`; this module stays
language-free like the rest of :mod:`crude.ui`, taking plain Python callables so
a paper test can drive a whole menu with three lambdas and no interpreter.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, List, Optional, Tuple

from .handlers import event_name
from .layout import anchor, grid, hstack, pack, pad
from .scroll import measure, reconcile, scroll_by
from .widget import Widget

__all__ = ["MenuBar", "Menu", "MenuItem", "MenuDialog"]

#: the height of the bar strip and of the popup rows, in logical pixels - fixed
#  numbers because a menu has to be the same size whether or not a layout pass
#  has run yet, and a skin token would make a paper test depend on a theme.
#  They are measured to hold a whole word: the bar's and the popup's own padding
#  is zeroed below (the framework insets its rows by hand), so what a row has to
#  clear is its button's plate plus one line of the skin's body font at the
#  largest scale - 16.25 logical pixels - and a title's click box wants a little
#  air around that.
BAR_HEIGHT = 32.0
ITEM_HEIGHT = 28.0

#: how wide a menu title is: its label's length at a rough monospace estimate,
#  plus the bar's own side padding, never narrower than a tap target
CHAR_WIDTH = 8.0
TITLE_PAD = 16.0
MIN_TITLE_WIDTH = 64.0

#: the popup's own insets and the width floor a short label still gets
POPUP_PAD = 6.0
POPUP_MIN_WIDTH = 120.0

#: a dialog's width and the height of its rows, in logical pixels - a
#  configuration panel is measured the same way a popup is, so it needs no skin
#  and no layout pass to be placed or to be tested. The panel is wider than a
#  menu popup because it carries whole settings rows - device names, a sample
#  rate, a blocksize - and it is *sized by the window it opens in*, not by a
#  height it decides for itself: the page is what it is, and the room the window
#  gives it is spent on a scroll area rather than on content off the bottom edge.
DIALOG_WIDTH = 320.0
CAPTION_HEIGHT = 14.0
FIELD_HEIGHT = 24.0
APPLY_HEIGHT = 24.0
ROW_GAP = 6.0
TITLE_HEIGHT = 20.0
BUTTON_GAP = 8.0
LIST_ROW_HEIGHT = 22.0
LIST_VIEW_HEIGHT = 110.0

#: how far a dialog stays inside the window's client rect, on every side
DIALOG_MARGIN = 8.0

#: the shortest page that is still worth opening - a settings panel narrower than
#  this cannot show its own title and one button, and a window that small gets a
#  dialog that ends at its edge rather than one that overflows past it
MIN_DIALOG_HEIGHT = 220.0

#: the height a dialog *prefers*: a tall window does not get a taller settings
#  page than this, because a form stretched to 900 pixels is one long column of
#  captions and their gaps with nothing in them
DIALOG_MAX_HEIGHT = 420.0

#: the widest a page is allowed to get when a wide window offers more room: wide
#  enough for a device name and a rate, no wider than a column a person can read
DIALOG_MAX_WIDTH = 480.0

#: the least room a scroll area is ever given. A page whose fixed rows already
#  fill the window still gets a viewport a wheel can move rather than a body of
#  nothing - the thumb and the wheel are how the rest of the page is reached
MIN_BODY_HEIGHT = 44.0


def _title_width(label: str) -> float:
    """How wide a bar title is drawn: the label plus padding, with a floor."""
    return max(MIN_TITLE_WIDTH, len(str(label or "")) * CHAR_WIDTH + TITLE_PAD)


def _is_click(name: Any) -> bool:
    """Whether an event slot call is the one a menu reacts to.

    The slot is handed the tree's own spelling (``"on_click"``) when the widget is
    not wired to the handler bridge and the same word once the bridge has wrapped
    it, so both spellings mean the same thing here.
    """
    try:
        return event_name(name) == "click"
    except Exception:
        return False


class MenuBar:
    """A strip of menu titles across the top of a window.

    Built lazily-visible: the bar panel itself is always shown, but a popup only
    overlaps the rest of the window while it is open, and opening one closes
    whichever was open before it - so at most one menu's items are ever on screen.
    """

    def __init__(self, ctx: Any, name: str = "menu") -> None:
        self.ctx = ctx
        self.name = str(name or "menu")
        #: the menus, in bar order (left to right)
        self.menus: List[Menu] = []
        #: whichever menu is open right now, or ``None`` when none is
        self.open: Optional[Menu] = None
        #: the strip the titles live on, placed at the top of the window
        self.panel = Widget("panel", {"label": self.name}, name=self.name)
        ctx.add(self.panel)
        # the bar places its titles by hand at full height, so the panel's own
        # skin padding has to go: a child placed at (0,0) with the strip's whole
        # height would otherwise be clipped to the leftover interior - at 26px
        # and ``pad.md`` that is 8px of word, and the rest of every label is
        # scissored away.
        pad(self.panel, 0.0)
        self.panel.place(0.0, 0.0, _title_width(self.name), BAR_HEIGHT)
        # the next title's left edge, in bar-relative pixels
        self._cursor = 0.0

    def menu(self, title: str, name: str = "") -> "Menu":
        """Add one titled menu to the bar; the :class:`Menu` it made."""
        held = Menu(self, title, name=name)
        self.menus.append(held)
        # the strip grows with its widest contents so a long menu bar stays whole
        self.panel.place(None, None, self._cursor, None)
        return held

    def close_others(self, keep: "Menu") -> None:
        """Hide every popup but ``keep`` - the one-at-a-time rule."""
        for held in self.menus:
            if held is not keep and held.is_open():
                held.hide()

    def close_all(self) -> None:
        """Hide every popup; what clicking outside a menu would do, if asked."""
        for held in self.menus:
            if held.is_open():
                held.hide()

    def __str__(self) -> str:
        titles = ", ".join(menu.title for menu in self.menus) or "empty"
        return f"MenuBar({self.name}: {titles})"

    def __repr__(self) -> str:
        return f"<MenuBar {self.name!r} {len(self.menus)} menu(s)>"


class Menu:
    """One title on the bar and the popup of items that hangs under it."""

    def __init__(self, bar: MenuBar, title: str, name: str = "") -> None:
        self.bar = bar
        self.ctx = bar.ctx
        self.title = str(title or "")
        self.name = str(name or title or "menu")
        #: the item rows, in popup order (top to bottom)
        self.items: List[MenuItem] = []

        # the title is a plain button on the bar. Its own behaviour (a momentary
        # bounce) is chained ahead of the toggle so nothing about a button changes;
        # a click opens or closes this menu's popup.
        width = _title_width(self.title)
        self.button = Widget("button", {"label": self.title},
                             name=f"{self.name}#bar")
        bar.panel.add(self.button)
        self.button.place(bar._cursor, 0.0, width, BAR_HEIGHT)
        bar._cursor += width
        self._width = width
        self._own_previous = self.button.on_event
        self.button.on_event = self._on_bar_click

        # the popup is a child of the window's root, not of the bar, so it can
        # overlap the project's widgets; it starts hidden and raised when shown.
        # Its rows are placed by hand inside POPUP_PAD, so the panel's own
        # padding goes for the same reason the bar's went.
        self.popup = Widget("panel", {"label": self.title},
                            name=f"{self.name}#popup")
        self.ctx.add(self.popup)
        pad(self.popup, 0.0)
        self.popup.place(self.button.x, BAR_HEIGHT, width, POPUP_PAD)
        self.popup.set("visible", False)

    # -- the title's own click -------------------------------------------
    def _on_bar_click(self, widget: Any, name: Any, *args: Any) -> None:
        """The bar title's slot: the kind's behaviour first, then a toggle."""
        previous = getattr(self, "_own_previous", None)
        if callable(previous):
            previous(widget, name, *args)
        if _is_click(name):
            if self.is_open():
                self.hide()
            else:
                self.show()

    # -- open and close ---------------------------------------------------
    def show(self) -> "Menu":
        """Raise this popup and put every other one away."""
        self.bar.close_others(self)
        self.bar.open = self
        self.popup.raise_to_top()
        self.popup.set("visible", True)
        return self

    def hide(self) -> "Menu":
        self.popup.set("visible", False)
        if self.bar.open is self:
            self.bar.open = None
        return self

    def is_open(self) -> bool:
        return bool(self.popup.get("visible"))

    # -- the rows ---------------------------------------------------------
    def add_item(self, label: str, *, callback: Optional[Callable[..., Any]] = None,
                 checkable: bool = False, on: bool = False,
                 name: str = "") -> MenuItem:
        """Add one row to the popup; the :class:`MenuItem`.

        ``checkable`` rows are ``toggle``s whose value the click latches before
        hidden the popup.
        """
        held = MenuItem(self, label, callback=callback, checkable=checkable,
                        on=on, name=name)
        self.items.append(held)
        self._measure()
        return held

    def add_dialog(self, label: str, fields, *, provider=None, on_apply=None,
                   buttons=None, on_cancel=None, name: str = "") -> "MenuDialog":
        """Add a row that opens a dialog of controls; the :class:`MenuDialog`.

        The row hides the popup and opens the panel in its place. ``fields`` is a
        plain list of ``{"kind": ..., "label": ...}`` rows - ``dropdown``,
        ``number``, ``toggle``, ``list`` or ``buttons`` - and ``provider`` /
        ``on_apply`` are plain Python callables: the bindings use them to read the
        machine's ports and to hand the pick to a runtime, which is why a dialog
        needs no interpreter and no device to be tested. ``buttons`` names extra
        action buttons beside ``Apply`` - ``(label, callable)`` pairs, or the bare
        word ``"Cancel"``, which closes the panel without applying anything.
        """
        dialog = MenuDialog(self, label, fields, provider=provider,
                            on_apply=on_apply, buttons=buttons,
                            on_cancel=on_cancel, name=name)
        dialog.item = self.add_item(label, callback=lambda _row: dialog.open())
        return dialog

    def _measure(self) -> None:
        """Re-stack the rows and grow the popup to fit them, top to bottom."""
        if self.items:
            self._width = max(MIN_TITLE_WIDTH, POPUP_MIN_WIDTH,
                              max(_title_width(item.label) for item in self.items))
        left = POPUP_PAD
        top = POPUP_PAD
        for index, item in enumerate(self.items):
            item.button.place(left, top + index * ITEM_HEIGHT,
                              self._width - 2 * POPUP_PAD, ITEM_HEIGHT)
        height = top + len(self.items) * ITEM_HEIGHT + POPUP_PAD
        self.popup.place(None, None, self._width, height)

    def __str__(self) -> str:
        rows = ", ".join(item.label for item in self.items) or "empty"
        state = "open" if self.is_open() else "closed"
        return f"Menu({self.title}: {state}; {rows})"

    def __repr__(self) -> str:
        return f"<Menu {self.title!r} {len(self.items)} item(s)>"


class MenuItem:
    """One row in a menu's popup.

    A row is a ``button`` unless it is checkable, in which case it is a ``toggle``
    whose value the click latches before anything else happens - the behaviour is
    chained, not replaced, so ``MENU_CHECK`` reports the state the person just set.
    """

    def __init__(self, menu: Menu, label: str, *,
                 callback: Optional[Callable[..., Any]] = None,
                 checkable: bool = False, on: bool = False,
                 name: str = "") -> None:
        self.menu = menu
        self.label = str(label or "")
        self.checkable = bool(checkable)
        #: the host-side callable run after the popup has hidden, if any
        self.callback = callback

        kind = "toggle" if self.checkable else "button"
        values = {"label": self.label}
        self.button = Widget(kind, values,
                             name=str(name or f"{menu.name}#{self.label}"))
        if self.checkable:
            self.button.set("value", 1 if on else 0)
        menu.popup.add(self.button)

        #: whatever owned this row's slot first - the kind's own behaviour, which
        #  a checkable row needs to flip its value - chained ahead of the menu's
        self._previous = self.button.on_event
        self.button.on_event = self._on_click

    # -- the row's click ---------------------------------------------------
    def _on_click(self, widget: Any, name: Any, *args: Any) -> None:
        """The row's slot: the kind's behaviour, then hide, then the callback."""
        if callable(self._previous):
            self._previous(widget, name, *args)
        if not _is_click(name):
            return
        self.menu.hide()
        if self.callback is not None:
            self.callback(self)

    def trigger(self) -> None:
        """Do what a click would, without a pointer - for a host-side shortcut."""
        self.button.on_click(0.0, 0.0)

    @property
    def checked(self) -> bool:
        """Whether a checkable row is on; ``False`` for a plain one."""
        if not self.checkable:
            return False
        return bool(self.button.get("value"))

    def __str__(self) -> str:
        said = f"MenuItem({self.label}"
        if self.checkable:
            said += ", on" if self.checked else ", off"
        return said + ")"

    def __repr__(self) -> str:
        return f"<MenuItem {self.label!r} {self.menu.title!r}>"


class MenuDialog:
    """A settings panel a menu row opens, fills and applies.

    It is the same trick as a popup: a ``panel`` parented to the window's root,
    hidden until the row picks it, raised over everything when shown. Its own
    label is empty on purpose and the title is the first *row* - a panel draws
    its label in the vertical middle of its box, which on a settings panel is
    nowhere near the top, while a ``title``-role text row is exactly where a
    person expects a dialog's name.

    Each field is a caption and one block of controls, and a field dict may carry
    a ``provider`` key naming its own entry in the provider's ``dict`` answer:

    * ``dropdown`` - a folded choice; ``chosen`` maps the row back to a payload
    * ``number`` - an editable value, ``min``/``max``/``value`` honoured
    * ``toggle`` - one ``TOGGLE``; ``chosen`` answers ``True``/``False``
    * ``buttons`` - a grid of quick-set buttons (``columns`` wide) that call the
      field's ``on_button(position)`` and then re-fill, so a tap of ``48`` can
      write the sample-rate field it belongs beside
    * ``list`` - a scrollable column of per-device toggles: an outer clip panel
      with an inner panel slid by the wheel, one ``TOGGLE`` per entry. ``chosen``
      answers the list of enabled payloads, which is what makes a multi-device
      pick possible and a single one a special case of the same widget.

    The bottom row is ``Apply`` plus whatever ``buttons`` named - a bare
    ``"Cancel"`` closes without applying, a ``(label, fn)`` pair calls ``fn`` -
    and the row is pinned to the *bottom of the panel* rather than to the end of
    the content: the panel is measured from the window it opens in, and a page
    longer than the room that leaves is scrolled by the body's own wheel, so no
    settings page is ever cut off at the bottom edge of the screen.

    Nothing here knows what a device index or a baud is: a field carries the
    parallel ``payloads`` list the provider fills and :meth:`chosen` maps a pick
    back onto, so the bindings can hand a runtime the value it expects. The
    provider is called every time the dialog opens (a port can be plugged in
    between shows) and never at build time, so creating a menu costs no device
    call and a headless run is safe with no audio, no MIDI and no serial
    hardware.
    """

    def __init__(self, menu: "Menu", title: str, fields, *,
                 provider: Optional[Callable[[Any], Any]] = None,
                 on_apply: Optional[Callable[[List[Any]], Any]] = None,
                 buttons: Any = None,
                 on_cancel: Optional[Callable[[], Any]] = None,
                 name: str = "") -> None:
        self.menu = menu
        self.ctx = menu.ctx
        self.title = str(title or "")
        self.provider = provider
        self.on_apply = on_apply
        self.on_cancel = on_cancel
        #: the row that opens this dialog, set by :meth:`Menu.add_dialog`
        self.item: Optional[MenuItem] = None
        #: the field dicts, used as they were given: a quick-button grid closes
        #  over one of them and a provider pours into them, so the dialog, the
        #  buttons and the binding have to be looking at the very same objects
        self.fields = list(fields or ())
        #: one entry per field: its single control, or ``None`` for a section,
        #  a button grid or a list (a list's widgets live in ``lists``) - the
        #  positional indexing the first dialogs' tests used still answers
        self.controls: List[Optional[Widget]] = [None] * len(self.fields)
        #: per dropdown/list field, the payload behind each label; ``None`` else
        self.payloads: List[Optional[List[Any]]] = [None] * len(self.fields)
        #: per list field (by field index): its viewport, inner panel, toggles
        self.lists: dict = {}
        #: the value last written onto each field's control - what a refresh
        #  compares against before it dares touch what a person is typing into
        self.written: List[Any] = [None] * len(self.fields)
        #: fields the provider spoke for during the refresh running now
        self._said: set = set()
        #: list fields the provider handed a ``selected`` to during this refresh -
        #  a machine saying which devices it is using now outranks what is toggled
        self._reseed: set = set()

        # the panel carries no label: the title below is the dialog's name, and
        # a panel's own label would float in the vertical middle of the box
        self.popup = Widget("panel", {"label": ""},
                            name=str(name or f"{menu.name}#dialog"))
        self.ctx.add(self.popup)
        # rows are placed by hand inside POPUP_PAD - the panel's own padding
        # would clip them the same way it clips a menu popup's
        pad(self.popup, 0.0)
        self.popup.set("visible", False)

        # the running y inside the body's content: every block is measured from
        # the edge of the room the scroll area gives it, and the scroll area is
        # what decides how much of it a person can see at once
        top = 0.0

        # the title: the first row, at the top of the panel, in the skin's
        # title role - the one place a dialog's name can be read is where a
        # person looks for it before touching anything else in it. It is also the
        # handle the whole page is dragged by (see :meth:`_wire_drag`), which is
        # why it is a row of its own and not a label on the panel.
        self.title_widget = Widget("text", {"label": self.title,
                                            "role": "title"},
                                   name=f"{self.popup.name}#title")
        self.popup.add(self.title_widget)

        # the body: the one scroll area the page is made of. Every field block
        # goes inside it, so a settings page longer than the window is *reached*
        # with the wheel instead of running off the bottom edge, and its padding is
        # the margin the rows are measured from.
        self.body = Widget("scroll", {"label": ""},
                           name=f"{self.popup.name}#body")
        self.popup.add(self.body)
        pad(self.body, POPUP_PAD)

        # the two rows kept *outside* the body, so neither is ever scrolled away
        # or clipped by it: what the last click said, and the buttons that decide
        # the next one. Both are glued to the panel's bottom edge, which is what
        # makes the body exactly the room between them and the title.
        self.status = Widget("text", {"label": "", "role": "caption"},
                             name=f"{self.popup.name}#status")
        self.popup.add(self.status)
        self.actions = Widget("panel", {"label": ""},
                              name=f"{self.popup.name}#actions")
        self.popup.add(self.actions)
        pad(self.actions, 0.0)
        # the row divides itself: the same equal share the arithmetic in
        # :meth:`_frame` writes by hand, so a panel that has never seen a layout
        # pass and one that has put Apply, Rescan and Cancel in three equal boxes
        hstack(self.actions, BUTTON_GAP)

        #: where a drag left the panel, or ``None`` for "under the row that opened
        #  it" - which is where it goes again the next time the menu is used
        self._placed: Optional[tuple] = None
        #: the pointer's position while the title is being held down
        self._held: Optional[tuple] = None

        self.blocks: List[dict] = []
        for index, field in enumerate(self.fields):
            # every block is measured inside the body's content: the rows the
            # wheel slides are not measured against the panel's own corner
            block = self._build_field(index, field, 0.0, top, self._inner())
            if block is None:
                continue
            self.blocks.append(block)
            top = block["bottom"] + ROW_GAP

        # the action row: Apply, then whatever else the bindings asked for -
        # the buttons share the row, each as wide as the others
        self.action_buttons: List[Widget] = []
        self.extra_buttons: List[tuple] = []
        wanted = [("Apply", self._apply_now)]
        for extra in (buttons or ()):
            if isinstance(extra, str):
                word = extra.lower()
                if word == "cancel":
                    wanted.append(("Cancel", self._cancel_now))
                elif word == "rescan":
                    # the word is a behaviour, not a decoration: a button named
                    # Rescan has to go and read the machine again, in place
                    wanted.append((extra, self._rescan_now))
                else:
                    wanted.append((extra, None))
            else:
                label, fn = extra
                wanted.append((str(label), fn))
        self._action_row(wanted)

        self._glue()
        self._wire_drag()
        self._watch_resize()
        self._frame()

    # -- the panel's own geometry ----------------------------------------
    def _wide(self) -> float:
        """How wide the page may be: the window's, less a margin, in a band.

        ``DIALOG_WIDTH`` is the width a page *asks* for and ``DIALOG_MAX_WIDTH``
        the widest it is allowed to become, because a settings column a metre
        across is one word per line with the rest of it empty. What a narrow
        window has left wins in the other direction: the page ends inside the
        client rect whatever the window's own width happens to be.
        """
        room = max(0.0, float(self.ctx.width) - 2 * DIALOG_MARGIN)
        wanted = min(DIALOG_MAX_WIDTH, max(DIALOG_WIDTH, room))
        return max(0.0, min(room, wanted))

    def _inner(self) -> float:
        """The width one row of the page gets: the panel's less both margins."""
        return max(0.0, self._wide() - 2 * POPUP_PAD)

    def _natural(self) -> float:
        """The height the page would take if nothing were clipped away."""
        content = max(MIN_BODY_HEIGHT, measure(self.body)[0])
        return (POPUP_PAD + TITLE_HEIGHT + ROW_GAP + content + ROW_GAP
                + CAPTION_HEIGHT + 2.0 + APPLY_HEIGHT + POPUP_PAD)

    def _corner(self, width: float, height: float) -> Tuple[float, float]:
        """Where the panel goes: under its menu row, or wherever it was dragged.

        Clamped on the way, so a page thrown at a window corner stops at the edge
        rather than ending half out of the window: the room it is given and the
        room it is drawn in are the same rectangle.
        """
        ctx = self.ctx
        across = max(DIALOG_MARGIN,
                     float(ctx.width) - float(width) - DIALOG_MARGIN)
        down = max(DIALOG_MARGIN,
                   float(ctx.height) - float(height) - DIALOG_MARGIN)
        near = (float(self.menu.button.x) if self._placed is None
                else float(self._placed[0]))
        far = (BAR_HEIGHT + DIALOG_MARGIN if self._placed is None
               else float(self._placed[1]))
        return (min(max(near, DIALOG_MARGIN), across),
                min(max(far, DIALOG_MARGIN), down))

    def _frame(self) -> float:
        """Size and place the panel from the window; the height it ended up with.

        The numbers come out of ``ctx.width``/``ctx.height`` rather than from a
        height this class decides for itself, and they are *also* written as
        anchors (:meth:`_glue`) - the same answer twice on purpose, because most
        of what builds a dialog never runs a layout pass. The panel is the room
        the window leaves with a margin off it, capped at the height a form is
        worth having; what the cap takes away comes off the *page* and not off
        the bottom of the screen, because the body is a scroll area and the two
        rows under it are measured inwards from the panel's far edge.
        """
        width = self._wide()
        top = BAR_HEIGHT + DIALOG_MARGIN
        room = max(0.0, float(self.ctx.height) - top - DIALOG_MARGIN)
        height = max(0.0, min(room, min(DIALOG_MAX_HEIGHT,
                                        max(MIN_DIALOG_HEIGHT,
                                            self._natural()))))
        x, y = self._corner(width, height)
        if self._placed is not None:
            # a drag that ran past the edge is remembered at the edge, so the next
            # resize neither loses it nor invents a place nobody asked for
            self._placed = (x, y)
        self.popup.place(x, y, width, height)
        across = max(0.0, width - 2 * POPUP_PAD)
        self.title_widget.place(POPUP_PAD, POPUP_PAD, across, TITLE_HEIGHT)
        actions_y = max(0.0, height - POPUP_PAD - APPLY_HEIGHT)
        status_y = max(0.0, actions_y - 2.0 - CAPTION_HEIGHT)
        body_top = POPUP_PAD + TITLE_HEIGHT + ROW_GAP
        self.body.place(0.0, body_top, width,
                        max(MIN_BODY_HEIGHT, status_y - ROW_GAP - body_top))
        self.status.place(POPUP_PAD, status_y, across, CAPTION_HEIGHT)
        self.actions.place(POPUP_PAD, actions_y, across, APPLY_HEIGHT)
        count = max(1, len(self.action_buttons))
        cell = max(0.0, (across - (count - 1) * BUTTON_GAP) / count)
        for position, button in enumerate(self.action_buttons):
            button.place(position * (cell + BUTTON_GAP), 0.0, cell,
                         APPLY_HEIGHT)
        # the rows are where they are, so the body can finally be told what it is
        # showing: its content, its travel, and the offset clamped between the two
        reconcile(self.body)
        return height

    def _glue(self) -> None:
        """Write the page's geometry a second time, in edges.

        Every offset here is the number :meth:`_frame` placed the same row at, so
        the hand and the glue cannot disagree; what the glue adds is the frame
        after a window resize - the body stretched between the title's bottom and
        the caption's top, the caption and the buttons holding their distance from
        the panel's bottom edge, and the scroll band left as exactly the room
        between them, which is :meth:`_frame` again without another loop over the
        rows. A row glued to the panel is glued to the space the panel gives its
        children, which here is its whole box: the padding is written by hand.
        """
        anchor(self.title_widget, "left", offset=POPUP_PAD)
        anchor(self.title_widget, "right", offset=-POPUP_PAD)
        anchor(self.title_widget, "top", offset=POPUP_PAD)
        anchor(self.body, "left")
        anchor(self.body, "right")
        anchor(self.body, "top", self.title_widget, "bottom", ROW_GAP)
        anchor(self.body, "bottom", self.status, "top", -ROW_GAP)
        anchor(self.status, "left", offset=POPUP_PAD)
        anchor(self.status, "right", offset=-POPUP_PAD)
        anchor(self.status, "bottom", self.actions, "top", -2.0)
        anchor(self.actions, "left", offset=POPUP_PAD)
        anchor(self.actions, "right", offset=-POPUP_PAD)
        anchor(self.actions, "bottom", offset=-POPUP_PAD)

    def _wire_drag(self) -> None:
        """Let the title be grabbed: the page moves and its contents follow.

        The router already owns the gesture for the whole of it - the widget under
        the press ``capture``s the pointer, so a title drag cannot leak into a row
        the pointer happens to pass over - and the kind's own ``on_event`` slot is
        chained onto rather than replaced, the way a menu row chains the toggle it
        found, so a script that gave the title an ``ON_CLICK`` still gets it.
        Nothing is stored but the corner: :meth:`move` is the one place a panel is
        allowed to be put, and an anchored child needs no second half of the
        gesture because the glue reads the panel every frame.
        """
        previous = self.title_widget.on_event

        def on_event(widget, name, *args, _prev=previous) -> None:
            if callable(_prev):
                _prev(widget, name, *args)
            try:
                said = event_name(name)
            except Exception:
                return
            if said == "press" and len(args) >= 3 and not int(args[2] or 0):
                self._held = (float(args[0]), float(args[1]))
            elif said == "drag" and self._held is not None and len(args) >= 2:
                self.move(self.popup.x + float(args[0]),
                          self.popup.y + float(args[1]))
            elif said in ("release", "click"):
                self._held = None

        self.title_widget.on_event = on_event

    def move(self, x: Any, y: Any) -> Tuple[float, float]:
        """Put the panel's corner at ``x, y``, kept in the window; where it went.

        A drag and a re-open both answer through here, so "where the page is" is
        one fact with one clamp rather than a rule each.
        """
        self._placed = (float(x), float(y))
        self._frame()
        return self._placed

    def _watch_resize(self) -> None:
        """Re-measure the page when the window it hangs in changes size.

        The anchors do the rows, but the panel's own height is a ``min`` of the
        room the window leaves and the height a form is worth, which no pair of
        glued edges can say - so the one number the glue cannot answer is asked
        for directly, and the answer stops being asked for the moment the panel
        has left the tree.

        The event's own name comes as a third argument, the way every registration
        on this bridge is called, so the slot takes it rather than raising: a
        handler that fails here is logged by the bridge and muted, and a dialog
        whose page stopped following the window would be the silent version of that
        bug.
        """
        ctx = self.ctx
        root = ctx.root

        def on_resize(width, height, *said) -> None:
            if self.popup.parent is not root:
                ctx.handlers.remove(root, "resize", on_resize)
                return
            self._frame()

        ctx.handlers.add(root, "resize", on_resize)

    # -- building one field -------------------------------------------------
    def _caption(self, text: str, left: float, top: float, inner: float,
                 tag: str = ""):
        """One caption row; the y under it and the widget that drew it."""
        if not text:
            return top, None
        label = Widget("text", {"label": text},
                       name=f"{self.popup.name}#label{tag}")
        self.body.add(label)
        label.place(left, top, inner, CAPTION_HEIGHT)
        # the width is the row's to keep when the page is given more of it: the
        # caption is glued across the body by the pack policy below and placed at
        # the same full width by hand, so both answers are the one number
        pack(label, "fill", "fixed")
        return top + CAPTION_HEIGHT, label

    def _build_field(self, index: int, field: dict, left: float, top: float,
                    inner: float) -> Optional[dict]:
        """Build one field's block at ``top``; its record with the y it ended at.

        The block is built inside the body, so ``left``/``inner`` are measured from
        the edge of the content the wheel slides and ``top`` is a place in that
        content rather than a place in the window.
        """
        kind = str(field.get("kind", "dropdown"))
        if kind == "section":
            bottom, label = self._caption(str(field.get("label", "")), left,
                                          top, inner, str(index))
            return {"index": index, "kind": kind, "y": top, "bottom": bottom,
                    "label": label}
        if kind == "toggle":
            control = Widget("toggle", {"label": str(field.get("label", "")),
                                       "value": 1 if field.get("value") else 0},
                             name=f"{self.popup.name}#toggle{index}")
            self.body.add(control)
            control.place(left, top, inner, FIELD_HEIGHT)
            pack(control, "fill", "fixed")
            self.controls[index] = control
            return {"index": index, "kind": kind, "control": control,
                    "label": None, "y": top, "bottom": top + FIELD_HEIGHT}
        if kind == "number":
            bottom, label, control = self._caption_caption(
                field, index, left, top, inner)
            return {"index": index, "kind": kind, "control": control,
                    "label": label, "y": top, "bottom": bottom}
        if kind == "buttons":
            return self._build_button_grid(index, field, left, top, inner)
        if kind == "list":
            return self._build_list(index, field, left, top, inner)
        # the default: a folded choice, exactly as the first dialogs shipped
        bottom, label, control = self._caption_caption(field, index, left, top,
                                                       inner)
        return {"index": index, "kind": "dropdown", "control": control,
                "label": label, "y": top, "bottom": bottom}

    def _caption_caption(self, field: dict, index: int, left: float,
                         top: float, inner: float):
        """Caption + one standard control (a ``number`` or a ``dropdown``)."""
        caption = str(field.get("label", ""))
        y, label = self._caption(caption, left, top, inner, str(index))
        if field.get("kind") == "number":
            # a range the field did not state is left to the kind's own default:
            # writing 0 for "no maximum" would clamp the value to nothing, which
            # is exactly how a settings field ends up showing 0 instead of 44100
            spec = {"label": caption, "value": field.get("value", 0)}
            for word in ("min", "max", "step"):
                if word in field:
                    spec[word] = field[word]
            control = Widget("number", spec, name=f"{self.popup.name}#value{index}")
            self.payloads[index] = None
        else:
            control = Widget("dropdown", {
                "labels": list(field.get("choices", ()) or ()),
                "value": int(field.get("value", 0)),
            }, name=f"{self.popup.name}#choice{index}")
            self.payloads[index] = list(field.get("payloads", ()) or ())
        self.body.add(control)
        control.place(left, y, inner, FIELD_HEIGHT)
        pack(control, "fill", "fixed")
        self.controls[index] = control
        return y + FIELD_HEIGHT, label, control

    def _build_button_grid(self, index: int, field: dict, left: float,
                           top: float, inner: float) -> dict:
        """A quick-set grid: ``labels`` across ``columns`` rows of equal buttons.

        The cells are the layout engine's to divide: one holder panel across the
        body's width, arranged by :func:`~crude.ui.layout.grid`, is the same
        answer the per-button arithmetic used to be - and it is the answer that
        re-cells itself when the page is made wider, which a hand-divided row of
        fixed cells is not. The buttons fill their cells, so the row heights stay
        ``FIELD_HEIGHT`` and only the widths move with the panel.
        """
        y, label = self._caption(str(field.get("label", "")), left, top, inner,
                                 str(index))
        labels = [str(text) for text in (field.get("labels", ()) or ())]
        columns = max(1, int(field.get("columns", 4)))
        rows = (len(labels) + columns - 1) // columns if labels else 0
        height = rows * (FIELD_HEIGHT + BUTTON_GAP) - BUTTON_GAP if rows else 0.0
        holder = Widget("panel", {"label": ""},
                        name=f"{self.popup.name}#cells{index}")
        self.body.add(holder)
        pad(holder, 0.0)
        grid(holder, columns, gap=BUTTON_GAP)
        holder.place(left, y, inner, max(0.0, height))
        pack(holder, "fill", "fixed")
        buttons: List[Widget] = []
        cell = (inner - (columns - 1) * BUTTON_GAP) / columns if labels else 0.0
        for position, text in enumerate(labels):
            button = Widget("button", {"label": text},
                            name=f"{self.popup.name}#quick{index}-{position}")
            holder.add(button)
            row, column = divmod(position, columns)
            button.place(column * (cell + BUTTON_GAP),
                         row * (FIELD_HEIGHT + BUTTON_GAP),
                         cell, FIELD_HEIGHT)
            pack(button, "fill", "fill")
            self._wire_quick(button, field, position)
            buttons.append(button)
        return {"index": index, "kind": "buttons", "buttons": buttons,
                "label": label, "y": top, "bottom": y + max(0.0, height)}

    def _wire_quick(self, button: Widget, field: dict, position: int) -> None:
        """Chain a quick button's bounce onto the field's own ``on_button``."""
        previous = button.on_event

        def on_event(widget, name, *args, _prev=previous, _field=field,
                     _pos=position) -> None:
            if callable(_prev):
                _prev(widget, name, *args)
            if not _is_click(name):
                return
            handler = _field.get("on_button")
            if handler is not None:
                handler(_pos)
                self.refresh()

        button.on_event = on_event

    def _build_list(self, index: int, field: dict, left: float, top: float,
                    inner: float) -> dict:
        """A scrollable column of per-device toggles: the ``scroll`` kind itself.

        What this class used to keep by hand - a viewport, an inner panel, an
        offset, a travel recomputed on every rebuild - is one widget of the catalog
        now: the rows go to the viewport's own content, the wheel writes its
        ``offset`` prop, and its ``travel`` is the measure of the two boxes. The
        viewport's padding is the margin the rows used to be inset by, which is why
        the travel that comes out of it is the number the dialog used to work out.
        Nothing is chained onto the widget's ``on_event`` here: the kind already
        answers a wheel, and a second answer would move the list twice.
        """
        y, label = self._caption(str(field.get("label", "")), left, top, inner,
                                 str(index))
        viewport = Widget("scroll", {"label": ""},
                          name=f"{self.popup.name}#list{index}")
        self.body.add(viewport)
        # the inset first and the box second, because the padding is measured into
        # the resolved box: placed first, the viewport would report a content the
        # margin has never been taken off and a travel too big by twice the pad
        pad(viewport, POPUP_PAD)
        pack(viewport, "fill", "fixed")
        viewport.place(left, y, inner, LIST_VIEW_HEIGHT)
        state = {"viewport": viewport, "inner": viewport.content_child,
                 "rows": [], "offset": 0.0, "travel": 0.0, "field": field,
                 "y": top}
        self.lists[index] = state
        self._rebuild_rows(index)
        self.payloads[index] = list(field.get("payloads", ()) or ())
        return {"index": index, "kind": "list", "y": top, "label": label,
                "bottom": y + LIST_VIEW_HEIGHT, "control": None}

    def _rebuild_rows(self, index: int) -> None:
        """One toggle per entry, stacked in the inner panel; selections kept.

        A row a person turned on stays on across a rescan: the pick is carried
        over by payload, so a device list that grew (or shrank) neither rewinds
        the choices made since the panel opened nor claims a device that went
        away. Only a list nobody has touched yet is seeded from the provider's
        ``selected`` - which is what makes the first open read the running
        stream and every later one read the person's own edits.
        """
        state = self.lists[index]
        field = state["field"]
        labels = [str(text) for text in (field.get("choices", ()) or ())]
        payloads = list(field.get("payloads", ()) or ())
        signature = tuple(repr(payload) for payload in payloads)
        previous = list(state.get("payloads_live") or ())
        known = {repr(payload) for payload in payloads}
        chosen = set()
        if index not in self._reseed:
            for position, toggle in state["rows"]:
                if not bool(toggle.get("value")) or position >= len(previous):
                    continue
                if repr(previous[position]) in known:
                    chosen.add(previous[position])
        if not chosen and (index in self._reseed
                           or signature != state.get("signature")):
            wanted = field.get("selected")
            if wanted is not None:
                chosen = {payloads[position]
                          for position in wanted
                          if 0 <= position < len(payloads)}
        state["signature"] = signature
        state["payloads_live"] = payloads
        for _position, toggle in state["rows"]:
            toggle.detach()
        state["rows"] = []
        width = state["viewport"].width - 2 * POPUP_PAD
        for position, text in enumerate(labels):
            on = (payloads[position] in chosen) if position < len(payloads) \
                else False
            toggle = Widget("toggle", {"label": text, "value": 1 if on else 0},
                            name=f"{state['viewport'].name}#row{position}")
            state["inner"].add(toggle)
            toggle.place(0.0, position * LIST_ROW_HEIGHT, width, LIST_ROW_HEIGHT)
            # a row keeps the viewport's whole width when the page is widened: the
            # content panel is glued to it, and a device name should not have to be
            # re-placed by hand to use the room a bigger window gave the list
            pack(toggle, "fill", "fixed")
            state["rows"].append((position, toggle))
        # the viewport measures itself: content, travel, the clamped offset, and
        # the content panel's own box - all four from the rows that are in it now
        _content, travel = reconcile(state["viewport"])
        state["travel"] = travel
        state["offset"] = float(state["viewport"].get("offset") or 0.0)

    # -- scrolling one list --------------------------------------------------
    def scroll_to(self, index: int, delta: float) -> float:
        """Slide a list's rows by ``delta`` notches; the offset it landed at.

        The arithmetic is the scroll area's now - one notch is the widget's own
        ``notch`` prop, left at the two rows a wheel step used to move, and the
        clamp to ``[-travel, 0]`` is the prop write inside
        :func:`crude.ui.scroll.slide`. The verb stays because the bindings and the
        dialog's own tests call it, and because ``offset`` is the number they are
        asking about either way.
        """
        state = self.lists.get(index)
        if state is None:
            return 0.0
        viewport = state["viewport"]
        state["offset"] = scroll_by(viewport, float(delta))
        state["travel"] = float(viewport.get("travel") or 0.0)
        return state["offset"]

    def list_selection(self, index: int) -> List[Any]:
        """The payloads whose toggle is on, in list order."""
        state = self.lists.get(index)
        if state is None:
            return []
        payloads = list(state["field"].get("payloads", ()) or ())
        out = []
        for position, toggle in state["rows"]:
            if bool(toggle.get("value")) and position < len(payloads):
                out.append(payloads[position])
        return out

    # -- the action row -------------------------------------------------------
    def _action_row(self, wanted) -> None:
        """Apply and the extra buttons, sharing the row evenly, in their own panel.

        The row is the panel :meth:`_frame` measures against the page's width and
        :meth:`_glue` pins to the page's bottom edge: a settings panel that grew
        an inch keeps its buttons one inch further down rather than gaining a
        band of empty plate under them.
        """
        inner = self._inner()
        count = max(1, len(wanted))
        cell = (inner - (count - 1) * BUTTON_GAP) / count
        for position, (label, fn) in enumerate(wanted):
            button = Widget("button", {"label": label},
                            name=f"{self.popup.name}#{label.lower()}")
            self.actions.add(button)
            button.place(position * (cell + BUTTON_GAP), 0.0,
                         cell, APPLY_HEIGHT)
            # the even share is the same one ``HSTACK`` divides when a pass runs:
            # every button fills, so the slack is split straight down the middle
            pack(button, "fill", "fixed")
            previous = button.on_event

            def on_event(widget, name, *args, _prev=previous, _fn=fn) -> None:
                if callable(_prev):
                    _prev(widget, name, *args)
                if _is_click(name) and _fn is not None:
                    _fn()

            button.on_event = on_event
            self.action_buttons.append(button)
            if fn is not self._apply_now and fn is not self._cancel_now:
                self.extra_buttons.append((label, fn))

    def _cancel_now(self) -> None:
        """Cancel: pack the panel away and tell the bindings, applying nothing."""
        self.close()
        if self.on_cancel is not None:
            self.on_cancel()

    def rescan(self) -> str:
        """Read the machine again without closing the panel; what it now shows.

        A pick already made is kept: the lists re-seed only when the devices
        themselves changed (see :meth:`_rebuild_rows`), so plugging something in
        mid-dialog neither loses the row a person just chose nor hides a port
        that appeared.
        """
        self.refresh()
        total = 0
        for index in sorted(self.lists):
            total += len(self.lists[index]["rows"])
        return f"rescanned: {total} row(s) listed"

    def _rescan_now(self) -> None:
        self.status.set("label", self.rescan())

    # -- showing and hiding ----------------------------------------------
    def is_open(self) -> bool:
        return bool(self.popup.get("visible"))

    def open(self) -> "MenuDialog":
        """Re-read the fields and raise the panel under the row that opened it.

        The panel is measured again on the way: a window that changed size since
        the last time this menu was used gets the page it has room for now. A
        place a person dragged the page to is forgotten here, because "open the
        audio settings" has exactly one answer a pointer should not have to find
        again - and the row the menu is hanging under is that answer.
        """
        self.refresh()
        self.menu.bar.close_all()
        self._placed = None
        self._frame()
        self.popup.raise_to_top()
        self.popup.set("visible", True)
        return self

    def close(self) -> "MenuDialog":
        self.popup.set("visible", False)
        return self

    # -- the controls ------------------------------------------------------
    def _fill(self, filled: Any) -> None:
        """Hand the provider's answer to the fields that named a provider key.

        A provider may answer the old plain ``list`` (one entry per field, filled
        in order, which is what a two-dropdown dialog always did) or a ``dict``
        keyed by each field's own ``provider`` name - the shape a settings panel
        with quick buttons and lists needs, because a button row and a label are
        fields with nothing to fill and a sample-rate group writes two fields.

        Every field the answer spoke for is remembered in ``_said``: a value the
        machine named is written onto its control, and one it stayed quiet about
        is left exactly as the person has it.
        """
        keyed = isinstance(filled, dict)
        for index, field in enumerate(self.fields):
            if keyed:
                held = filled.get(field.get("provider"))
                if not isinstance(held, dict):
                    continue
                field.update(held)
                if "value" in held:
                    self._said.add(index)
                if "selected" in held:
                    self._reseed.add(index)
                elif "selected" in field:
                    # the machine stopped saying it: do not let a stale answer
                    # from an earlier open keep seeding the rows
                    del field["selected"]
            elif index < len(filled):
                entry = filled[index]
                if isinstance(entry, dict):
                    field.update(entry)
                    if "value" in entry:
                        self._said.add(index)
                    if "selected" in entry:
                        self._reseed.add(index)
                elif field.get("kind") == "number":
                    field["value"] = entry
                    self._said.add(index)
                else:
                    field["choices"] = entry

    def _says(self, index: int, field: dict) -> bool:
        """Whether this refresh may move the control a field answers with.

        Either the provider named a value for it just now, or the field's own
        value is not the one last written - which is how a quick button that only
        edits the field dict still reaches the number beside it. What neither
        covers is a plain refresh between keystrokes: the person's edit stays.
        """
        if index in self._said:
            return True
        if "value" not in field:
            return False
        held = self.written[index]
        return held is None or field.get("value") != held

    def _write(self, index: int, control: Optional[Widget], value: Any) -> None:
        """Write one value through and remember that it is what we put there."""
        if control is None:
            return
        control.set("value", value)
        self.written[index] = value

    def refresh(self) -> "MenuDialog":
        """Ask the provider for fresh values and write them onto the controls.

        Two provider shapes answer: the first dialogs' ``provider(fields)``, which
        mutates the field list in place and returns nothing, and a no-argument
        callable whose ``dict``/``list`` answer is poured over the fields by
        :meth:`_fill`. The framework calls whichever it was given, so a binding
        written either way fills the same controls.
        """
        self._said = set()
        self._reseed = set()
        if self.provider is not None:
            try:
                takes_fields = bool(
                    inspect.signature(self.provider).parameters)
            except (TypeError, ValueError):
                takes_fields = False
            filled = self.provider(self.fields) if takes_fields \
                else self.provider()
            if filled is not None:
                self._fill(filled)
        for block in self.blocks:
            index = block["index"]
            field = self.fields[index]
            kind = field.get("kind", "dropdown")
            control = block.get("control")
            if kind == "number":
                if self._says(index, field):
                    self._write(index, control, field.get("value", 0))
            elif kind == "toggle":
                if self._says(index, field):
                    self._write(index, control, 1 if field.get("value") else 0)
            elif kind == "list":
                self._rebuild_rows(index)
            elif kind == "dropdown":
                if control is not None:
                    control.set("labels",
                                list(field.get("choices", ()) or ()))
                    if self._says(index, field):
                        self._write(index, control, int(field.get("value", 0)))
                    self.payloads[index] = list(field.get("payloads", ()) or ())
        # a list that grew is a page that got longer, and a page that got longer
        # may no longer fit the room the window gives it: the body is re-measured
        # against the rows it has now, so the thumb and the travel say what is
        # there rather than what was there when the panel was first built
        self._frame()
        return self

    def _control_for(self, index: int) -> Optional[Widget]:
        """The single control a numbered/toggle/dropdown field answers with."""
        for block in self.blocks:
            if block["index"] == index:
                return block.get("control")
        return None

    def chosen(self) -> List[Any]:
        """One value per field: a payload list, a toggle's bool, a number.

        Quick-button grids and section captions answer ``None`` - they act the
        moment they are clicked, and there is nothing for Apply to read off them.
        """
        out: List[Any] = []
        for index, field in enumerate(self.fields):
            kind = field.get("kind", "dropdown")
            if kind == "list":
                out.append(self.list_selection(index))
                continue
            if kind in ("buttons", "section"):
                out.append(None)
                continue
            control = self._control_for(index)
            if kind == "toggle":
                out.append(bool(control.get("value")) if control else False)
                continue
            if kind == "number":
                out.append(float(control.get("value") or 0.0)
                           if control else 0.0)
                continue
            held = self.payloads[index] if index < len(self.payloads) else None
            position = int(control.get("value") or 0) if control else 0
            out.append(held[position] if held and 0 <= position < len(held)
                       else None)
        return out

    # -- the Apply click ---------------------------------------------------
    def _apply_now(self) -> None:
        """Press Apply without a pointer - for a host-side shortcut or a test.

        The binding's answer is said on the status line: words it returns are
        what the runtime agreed to, and an exception it raises stays on screen
        with the panel open rather than vanishing into a swallowed error -
        a setting that was not applied has to be readable as not applied.
        """
        said = ""
        if self.on_apply is not None:
            try:
                answer = self.on_apply(self.chosen())
                said = str(answer) if answer else ""
            except Exception as exc:
                self.status.set("label", f"{type(exc).__name__}: {exc}")
                return
        self.status.set("label", said)
        self.close()

    # keep the first dialogs' calling convention: pressing the Apply button
    apply_now = _apply_now

    def __str__(self) -> str:
        rows = ", ".join(str(field.get("label", "")) for field in self.fields)
        state = "open" if self.is_open() else "closed"
        return f"MenuDialog({self.title}: {state}; {rows})"

    def __repr__(self) -> str:
        return f"<MenuDialog {self.title!r} {len(self.fields)} field(s)>"
