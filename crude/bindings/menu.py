"""``MENU_*`` bindings: a reusable in-window menu, built from the widget layer.

The File menu every windowed example asks for - save and load a preset, pick the
audio devices, open a MIDI controller, set a serial baud - is not a special thing
the host draws: it is the ordinary ``BUTTON``/``PANEL``/``DROPDOWN`` widgets the
script already knows, arranged by :mod:`crude.ui.menu` and wired through the same
handler bridge ``ON_CLICK`` uses. This module is only the language-facing door onto
that: create a bar, add menus and items, and show or hide one.

The verbs:

* ``MENUBAR(name)`` -> a bar across the top of the window,
* ``MENU(bar, title)`` -> one menu on that bar (a title button plus a hidden popup),
* ``MENU_ADD(menu, label, fn)`` -> a plain item that runs ``fn`` when picked,
* ``MENU_CHECK(menu, label, on, fn)`` -> a tickable item, ``fn`` told the new state,
* ``MENU_SHOW(menu)`` / ``MENU_HIDE(menu)`` -> put a popup up or away,
* ``MENU_AUDIO_DIALOG(menu)`` / ``MENU_MIDI_DIALOG(menu)`` -> items that open
  settings panels in the Pure Data "Media" shape: the machine's devices in
  scrollable lists (a per-device toggle each, and a "multiple devices" switch
  that lets a show play or listen through more than one at once), a sample rate
  field with quick buttons, a delay in milliseconds, blocksize buttons, a "use
  callbacks if possible" switch, and Apply / Rescan / Cancel buttons. Apply
  walks ``AUDIO_USE_DEVICES`` / ``MIDI_OPEN`` underneath - the same runtimes the
  plain verbs reach.
* ``MENU_SERIAL_DIALOG(menu, link)`` -> an item that opens a ``NUMBER`` for one
  link's baud and applies it with ``SET(link, "baud", n)``.

The dialog rows are the whole point of the File menu: they let a running
program pick its devices and its stream shape without a native dialog, and they
cost no device call to create - the ports are read when the panel opens, so a
project with no audio, no MIDI library and no serial cable still builds its menu
and runs with no window. Each is one ``MenuDialog`` from :mod:`crude.ui.menu`:
a ``provider`` reads the runtime's ``devices`` into the fields (mapping a choice
back to a device *index*, which is what the runtimes open on) and an
``on_apply`` hands that pick to ``apply_settings`` / ``open`` / ``set_prop``.
When a runtime is absent the provider shows one disabled "no devices" row and
applying says so on the dialog's status line instead of doing something.

A bar and a menu are host handles (``"menubar"`` and ``"menu"``), read-only: they
have no props to write, because everything worth saying about a menu is said with
these verbs. An item, by contrast, is handed back as the widget handle it is built
from, so ``STYLE`` and ``PLACE`` can reach a particular row - and the ``fn`` a row
runs is registered the same way ``ON_CLICK`` registers one, which is why a picked
item hides its popup first (the menu's own behaviour, chained ahead in the widget's
event slot) and only then calls back into the script.
"""

from __future__ import annotations

from ..lang.values import (
    BaseFunction,
    HostHandle,
    Number,
    value_to_python,
)
from ..ui.menu import Menu, MenuBar
from ..ui.widget import Widget
from ..ui.workspace import WorkspaceError
from .registry import binding

AREA = "ui"

#: the handle kinds a bar and a menu answer as; both are read-only
BAR_KIND = "menubar"
MENU_KIND = "menu"


# -- helpers ------------------------------------------------------------


def _window(ctx):
    """``host.ui`` - the context a menu is composed inside."""
    return ctx.subsystem("ui", "UI")


def _fail(ctx, exc) -> None:
    raise ctx.fail(f"{ctx.name}: {exc}")


def _bar_handle(bar: MenuBar) -> HostHandle:
    return HostHandle(bar, kind=BAR_KIND, label=bar.name)


def _menu_handle(menu: Menu) -> HostHandle:
    return HostHandle(menu, kind=MENU_KIND, label=menu.title)


def _widget_handle(widget: Widget) -> HostHandle:
    """An item as the widget handle ``STYLE``/``PLACE``/``GET`` already speak."""
    from .ui import WIDGET_KIND
    return HostHandle(widget, kind=WIDGET_KIND, label=widget.path())


def _bar(ctx, arg_name: str = "bar") -> MenuBar:
    value = ctx.get(arg_name)
    if not isinstance(value, HostHandle) or value.kind != BAR_KIND:
        ctx.bad_type(arg_name, "menu bar", value)
    return value.target


def _menu(ctx, arg_name: str = "menu") -> Menu:
    value = ctx.get(arg_name)
    if not isinstance(value, HostHandle) or value.kind != MENU_KIND:
        ctx.bad_type(arg_name, "menu", value)
    return value.target


def _optional_bool(ctx, arg_name: str, default: bool) -> bool:
    if not ctx.has(arg_name):
        return default
    held = value_to_python(ctx.get(arg_name))
    if isinstance(held, str):
        return held.strip().lower() in ("true", "yes", "on", "1")
    return bool(held)


# -- the bar and its menus ----------------------------------------------


@binding("MENUBAR", ["name"], area=AREA, optional=1,
         doc="A menu bar across the top of the window; MENU adds titles to it.")
def menubar(ctx):
    """``MENUBAR("File")``: the strip the menus hang off.

    The bar is a ``PANEL`` and the window's own layout places nothing in it, so it
    simply sits at the top-left; a project that wants it elsewhere moves the handle
    it returns with ``PLACE``.
    """
    window = _window(ctx)
    name = ctx.as_string("name") if ctx.has("name") else "menu"
    return _bar_handle(MenuBar(window, name=name))


@binding("MENU", ["bar", "title"], area=AREA,
         doc="Add a titled menu to a bar; a button plus its hidden popup.")
def menu_ctor(ctx):
    """``MENU(bar, "Audio")``: one title on the bar and its popup of items."""
    bar = _bar(ctx)
    title = ctx.as_string("title")
    return _menu_handle(bar.menu(title))


# -- the items ----------------------------------------------------------


def _wire_item(ctx, window, item, func):
    """Register a script row callback the way ``ON_CLICK`` does.

    The row's own event slot already hides the popup; the bridge runs that slot
    first and this function after, so the project's code never sees a stale menu
    still on screen.
    """
    if not isinstance(func, BaseFunction):
        ctx.bad_type("fn", "function", func)
    try:
        window.handlers.add(item.button, "click", func)
    except Exception as exc:  # a HandlerError is a wiring mistake, say it plainly
        _fail(ctx, exc)


@binding("MENU_ADD", ["menu", "label", "fn"], area=AREA,
         doc="A plain item: pick it to hide the popup and run fn.")
def menu_add(ctx):
    """``MENU_ADD(m, "Save preset", on_save)``: one clickable row.

    The item's widget handle comes back, so a project can style or re-place a
    particular row; the popup hides itself before ``fn`` runs.
    """
    window = _window(ctx)
    menu = _menu(ctx)
    label = ctx.as_string("label")
    func = ctx.as_function("fn")
    item = menu.add_item(label)
    _wire_item(ctx, window, item, func)
    return _widget_handle(item.button)


@binding("MENU_CHECK", ["menu", "label", "on", "fn"], area=AREA, optional=1,
         doc="A tickable item: it latches on/off and fn is told the new state.")
def menu_check(ctx):
    """``MENU_CHECK(m, "Monitor input", 0, on_toggle)``: a toggle in a menu.

    The row is a ``TOGGLE``, so a click flips its value before anything else;
    ``fn`` is registered as its ``ON_CLICK`` and is handed the click's arguments,
    where a value of 1 means the person just turned it on.
    """
    window = _window(ctx)
    menu = _menu(ctx)
    label = ctx.as_string("label")
    on = _optional_bool(ctx, "on", False)
    func = ctx.as_function("fn")
    item = menu.add_item(label, checkable=True, on=on)
    _wire_item(ctx, window, item, func)
    return _widget_handle(item.button)


# -- showing and hiding -------------------------------------------------


@binding("MENU_SHOW", ["menu"], area=AREA,
         doc="Open one menu's popup over the window, closing any other.")
def menu_show(ctx):
    """``MENU_SHOW(m)``: raise the popup; the one-open-at-a-time rule holds."""
    menu = _menu(ctx)
    menu.show()
    return Number(1 if menu.is_open() else 0)


@binding("MENU_HIDE", ["menu"], area=AREA,
         doc="Put one menu's popup away without hiding the bar itself.")
def menu_hide(ctx):
    """``MENU_HIDE(m)``: hide the popup; its items stay built for the next time."""
    menu = _menu(ctx)
    menu.hide()
    return Number(0 if menu.is_open() else 1)


# -- the configuration dialogs ------------------------------------------

#: the one row a dropdown shows when its runtime has nothing to offer, so a
#  machine with no sound card or no ``python-rtmidi`` says that instead of
#  drawing an empty box; applying it says so on the status line
NO_DEVICES = "(no devices)"

#: Pure Data's quick sample rates, in kHz, as the "audio settings" dialog
#  offers them; every button multiplies by 1000 for the field beside it
SAMPLE_RATES_KHZ = (7.83, 14.1, 20.3, 26.3, 32.5, 44.1, 48, 96)

#: the blocksize buttons: whole numbers of Pd's 64-frame ticks - a device
#  block that is not (2048 is the last that fits a page) would silently drop
#  the remainder of every callback
BLOCK_SIZES = (64, 128, 256, 512, 1024, 2048)


def _list_field(label: str, key: str) -> dict:
    """One scrollable device list: a per-device toggle, ``key`` in the provider."""
    return {"kind": "list", "label": label, "provider": key}


def _quick_rates(field: dict) -> dict:
    """A row of quick buttons that write one number field's value in Hz."""
    def push(position: int) -> None:
        field["value"] = round(SAMPLE_RATES_KHZ[position] * 1000.0)

    return {"kind": "buttons", "label": "", "columns": 4,
            "labels": [f"{text:g}" for text in SAMPLE_RATES_KHZ],
            "on_button": push}


def _quick_blocks(field: dict) -> dict:
    """A row of quick buttons that write a blocksize field's exact number."""
    def push(position: int) -> None:
        field["value"] = float(BLOCK_SIZES[position])

    return {"kind": "buttons", "label": "", "columns": 4,
            "labels": [str(size) for size in BLOCK_SIZES],
            "on_button": push}


def _audio_fields() -> list:
    """The Pure Data "Media" page as field rows: devices, rate, delay, blocks.

    Every number says the range it will take, because a ``number`` widget clamps
    its value to its own ``min``/``max``: a field left at the kind's default
    would hold 1.0 and show a sample rate of 0 Hz.
    """
    rate = {"kind": "number", "label": "Sample rate (Hz)", "provider": "rate",
            "min": 1, "max": 192000, "step": 1, "value": 44100}
    block = {"kind": "number", "label": "Block size (frames)",
             "provider": "block", "min": 1, "max": 65536, "step": 1, "value": 64}
    delay = {"kind": "number", "label": "Delay (ms)", "provider": "delay",
             "min": 0, "max": 5000, "step": 1, "value": 0}
    fields = [
        {"kind": "toggle", "label": "Multiple devices", "provider": "multi",
         "value": False},
        _list_field("Output devices", "outs"),
        _list_field("Input devices", "ins"),
        {"kind": "dropdown", "label": "Output device", "provider": "out"},
        {"kind": "dropdown", "label": "Input device", "provider": "in"},
        rate, _quick_rates(rate),
        delay,
        block, _quick_blocks(block),
        {"kind": "toggle", "label": "Use callbacks if possible",
         "provider": "callbacks", "value": True},
    ]
    return fields


def _midi_fields() -> list:
    """The MIDI page: one scrollable port list per direction, multi-switch."""
    return [
        {"kind": "toggle", "label": "Multiple ports", "provider": "multi",
         "value": False},
        _list_field("Input ports", "ins"),
        _list_field("Output ports", "outs"),
    ]


def _entries(runtime, direction: str, wrap=None):
    """One direction's ports as ``(labels, payloads)``; empty when unusable."""
    labels, payloads = [], []
    if runtime is not None:
        try:
            for device in runtime.devices(direction):
                labels.append(str(device.describe()))
                held = getattr(device, "index", None)
                payloads.append(wrap(direction, held) if wrap else held)
        except Exception:
            labels, payloads = [], []
    return labels, payloads


def _position_of(payloads, wanted) -> int:
    """Where a device index sits in a payload list; the first row if not there."""
    for position, held in enumerate(payloads):
        if held == wanted:
            return position
    return 0


def _selected_indices(payloads, devices) -> list:
    """The list positions whose device index is one that is actually in use."""
    wanted = {index for index in devices if index is not None}
    return [position for position, held in enumerate(payloads)
            if held in wanted]


@binding("MENU_AUDIO_DIALOG", ["menu", "title"], area=AREA, optional=1,
         doc="An item that opens the audio settings panel.")
def menu_audio_dialog(ctx):
    """``MENU_AUDIO_DIALOG(audio_menu)``: the stream's settings, live.

    The panel lists every output and input the runtime reports - one toggle per
    device, inside a scrollable area - behind a "multiple devices" switch: with
    it off the two dropdowns pick the stream's one pair, with it on every
    toggled device plays or listens together (the extra outputs ride side
    streams that copy the rendered block, the extra inputs have their blocks
    mixed into the one the session reads, so there is still exactly one
    renderer). The rest of the page mimics Pure Data's "Media": a sample rate
    with quick buttons, a delay in milliseconds, blocksize buttons and the
    callback preference, all applied together by ``apply_settings`` while the
    show keeps running.
    """
    menu = _menu(ctx)
    title = ctx.as_string("title") if ctx.has("title") else "Audio settings..."
    audio = getattr(ctx.host, "audio", None)
    state = {"seeded": False}

    def forget():
        # Cancel throws the page away: the next open reads the running
        # stream's real settings again instead of the edits that were declined
        state["seeded"] = False

    def provider():
        out_labels, out_payloads = _entries(audio, "output")
        in_labels, in_payloads = _entries(audio, "input")
        answer = {}
        if not out_labels:
            out_labels, out_payloads = [NO_DEVICES], [None]
        if not in_labels:
            in_labels, in_payloads = [NO_DEVICES], [None]
        answer["outs"] = {"choices": out_labels, "payloads": out_payloads}
        answer["ins"] = {"choices": in_labels, "payloads": in_payloads}
        answer["out"] = {"choices": out_labels, "payloads": out_payloads}
        answer["in"] = {"choices": in_labels, "payloads": in_payloads}
        config = getattr(audio, "config", None) if audio is not None else None
        if not state["seeded"] and audio is not None and config is not None:
            current_out = getattr(config, "output_device", None)
            current_in = getattr(config, "input_device", None)
            answer["out"]["value"] = _position_of(out_payloads, current_out)
            answer["in"]["value"] = _position_of(in_payloads, current_in)
            side_outs = tuple(getattr(config, "output_devices", ()) or ())
            answer["outs"]["selected"] = \
                _selected_indices(out_payloads,
                                  (current_out,) + side_outs) \
                or [answer["out"]["value"]]
            side_ins = tuple(getattr(config, "input_devices", ()) or ())
            answer["ins"]["selected"] = \
                _selected_indices(in_payloads,
                                  (current_in,) + side_ins) \
                or [answer["in"]["value"]]
            answer["multi"] = {"value": bool(side_outs or side_ins)}
            answer["rate"] = {"value": float(config.sample_rate)}
            answer["block"] = {"value": float(config.block_size)}
            answer["delay"] = {"value": float(config.delay_ms)}
            answer["callbacks"] = {"value": bool(config.callbacks)}
            state["seeded"] = True
        return answer

    def on_apply(values):
        if audio is None:
            return "audio: there is no audio runtime in this run"
        by_label = {str(field.get("label", "")): position
                    for position, field in enumerate(dialog.fields)}
        multi = values[by_label["Multiple devices"]]
        outs = values[by_label["Output devices"]]
        ins = values[by_label["Input devices"]]
        one_out = values[by_label["Output device"]]
        one_in = values[by_label["Input device"]]
        rate = values[by_label["Sample rate (Hz)"]]
        delay = values[by_label["Delay (ms)"]]
        block = values[by_label["Block size (frames)"]]
        callbacks = values[by_label["Use callbacks if possible"]]
        if multi:
            output_device = outs[0] if outs else None
            extras_out = outs[1:] if len(outs) > 1 else ()
            input_device = ins[0] if ins else None
            extras_in = ins[1:] if len(ins) > 1 else ()
        else:
            output_device, extras_out = one_out, ()
            input_device, extras_in = one_in, ()
        apply_settings = getattr(audio, "apply_settings", None)
        if apply_settings is None:
            audio.switch_devices(input_device, output_device)
            return f"audio on {audio.describe()}"
        return apply_settings(input_device=input_device,
                              output_device=output_device,
                              extra_output_devices=extras_out,
                              extra_input_devices=extras_in,
                              sample_rate=int(rate), block_size=int(block),
                              delay_ms=delay, callbacks=callbacks)

    fields = _audio_fields()
    dialog = menu.add_dialog(title, fields, provider=provider,
                             on_apply=on_apply, buttons=["Rescan", "Cancel"],
                             on_cancel=forget,
                             name=f"{menu.name}#audio")
    return _widget_handle(dialog.popup)


@binding("MENU_MIDI_DIALOG", ["menu", "title"], area=AREA, optional=1,
         doc="An item that opens the MIDI port panel.")
def menu_midi_dialog(ctx):
    """``MENU_MIDI_DIALOG(midi_menu)``: open controllers, one or several.

    Each direction's ports are a scrollable list with a toggle on every row,
    behind the "multiple ports" switch: with it on, every toggled row is
    opened; with it off the first toggled row per direction is the port.
    The toggles seed from the ports that are open right now. Apply reconciles:
    a toggled port that is closed gets opened, an un-toggled port that is open
    gets closed, and a machine with no MIDI library says so on the status line
    instead of doing something.
    """
    menu = _menu(ctx)
    title = ctx.as_string("title") if ctx.has("title") else "MIDI ports..."
    midi = getattr(ctx.host, "midi", None)
    state = {"seeded": False}

    def forget():
        state["seeded"] = False

    def open_indices(direction: str):
        held = set()
        if midi is None:
            return held
        for port in getattr(midi, "ports", {}).values():
            if getattr(port, "direction", "") == direction:
                held.add(getattr(port, "index", None))
        held.discard(None)
        return held

    def provider():
        answer = {}
        for key, direction in (("ins", "input"), ("outs", "output")):
            labels, payloads = _entries(midi, direction,
                                        wrap=lambda way, held: (way, held))
            if not labels:
                labels, payloads = [NO_DEVICES], [(direction, None)]
            answer[key] = {"choices": labels, "payloads": payloads}
            if not state["seeded"]:
                answer[key]["selected"] = _selected_indices(
                    payloads, {(direction, index)
                               for index in open_indices(direction)})
        state["seeded"] = True
        return answer

    def on_apply(values):
        if midi is None:
            return "midi: there is no MIDI runtime in this run"
        by_label = {str(field.get("label", "")): position
                    for position, field in enumerate(dialog.fields)}
        ins = [pick for pick in values[by_label["Input ports"]]
               if pick and pick[1] is not None]
        outs = [pick for pick in values[by_label["Output ports"]]
                if pick and pick[1] is not None]
        if not values[by_label["Multiple ports"]]:
            # the switch is not a decoration: off means one port each way, and a
            # second toggled row is read as the person changing their mind
            ins, outs = ins[:1], outs[:1]
        wanted = ins + outs
        # every port this dialog can speak for, so an un-toggled row that is
        # open right now is known enough to be closed
        known = []
        for direction in ("input", "output"):
            labels, payloads = _entries(midi, direction,
                                        wrap=lambda way, held: (way, held))
            known.extend(pick for pick in payloads
                         if pick and pick[1] is not None)
        said = []
        keep = {pick for pick in wanted}
        for direction, index in wanted:
            try:
                midi.open(direction, index)
                said.append(f"opened {direction} {index}")
            except Exception as exc:
                said.append(f"{direction} {index} refused: {exc}")
        for port in list(getattr(midi, "ports", {}).values()):
            pick = (getattr(port, "direction", ""), getattr(port, "index", None))
            if pick in keep or pick not in known:
                continue
            try:
                midi.close(port)
                said.append(f"closed {pick[0]} {pick[1]}")
            except Exception as exc:  # a port that would not go is said, not hidden
                said.append(f"{pick[0]} {pick[1]} would not close: {exc}")
        return "; ".join(said) if said else "nothing to open was picked"

    dialog = menu.add_dialog(title, _midi_fields(), provider=provider,
                             on_apply=on_apply, buttons=["Rescan", "Cancel"],
                             on_cancel=forget,
                             name=f"{menu.name}#midi")
    return _widget_handle(dialog.popup)


@binding("MENU_SERIAL_DIALOG", ["menu", "link", "title"], area=AREA, optional=1,
         doc="An item that opens a baud field for one serial link.")
def menu_serial_dialog(ctx):
    """``MENU_SERIAL_DIALOG(serial_menu, port)``: re-time a link while it runs.

    The number starts at the link's current baud; Apply writes it back through the
    same ``SET(link, "baud", n)`` a script would use, which closes and reopens the
    device at the new rate - one dialog per declared link.
    """
    menu = _menu(ctx)
    link = ctx.as_handle("link", "serial").target
    title = ctx.as_string("title") if ctx.has("title") else \
        f"Baud ({getattr(link, 'id', 'link')})"
    baud = int(getattr(link, "baud", 115200) or 115200)
    fields = [{"kind": "number", "label": "Baud rate",
               "min": 300, "max": 1000000, "value": baud}]

    def on_apply(values):
        try:
            link.set_prop("baud", int(values[0]))
        except Exception:
            pass

    dialog = menu.add_dialog(title, fields, on_apply=on_apply,
                             name=f"{menu.name}#serial")
    return _widget_handle(dialog.popup)


# -- the workspace switch ------------------------------------------------


def _switch(window, name: str) -> None:
    """Show one scene from a menu row, and say it if the scene has gone.

    A row is built from a name read a moment ago and clicked a minute later, and a
    script may have no way to take the row away again - so a scene that is not there
    any more is a log line rather than an exception travelling back out through the
    pointer's dispatch, which would leave the popup open and the frame unfinished.
    """
    try:
        window.workspaces.show(name)
    except WorkspaceError as exc:
        bridge = getattr(window, "handlers", None)
        errors = getattr(bridge, "errors", None)
        if isinstance(errors, list):
            errors.append(f"menu: {exc}")
        speak = getattr(bridge, "log", None)
        if callable(speak):
            speak(f"menu: {exc}")


@binding("MENU_WORKSPACES", ["menu"], area=AREA,
         doc="One row per workspace, the current one ticked; picking one shows it.")
def menu_workspaces(ctx):
    """``MENU_WORKSPACES(view_menu)``: the scene switch, with no loop in the script.

    The rows are rebuilt every time the popup opens, from the window's own list, so
    a project that declares a scene late - or from a handler - is in the menu the
    next time the person clicks ``View`` without having to be asked to refresh it.
    Each row is a checkable item whose tick means "this is what is on screen", and
    picking a row is ``WORKSPACE_SHOW`` for that name.
    """
    window = _window(ctx)
    menu = _menu(ctx)

    def rebuild(held: Menu) -> None:
        """Throw the rows away and compose them from the scenes there are now."""
        held.clear_items()
        for name in window.workspaces.names():
            held.add_item(name, callback=lambda _row, said=name: _switch(window, said),
                          checkable=True,
                          on=(name == window.workspaces.current_name))

    if rebuild not in menu.opening:
        menu.opening.append(rebuild)
    rebuild(menu)
    return Number(float(len(menu.items)))


#: Names this module registers, used by the binding-help generator and tests.
BINDINGS = (
    "MENUBAR", "MENU", "MENU_ADD", "MENU_CHECK", "MENU_SHOW", "MENU_HIDE",
    "MENU_WORKSPACES",
    "MENU_AUDIO_DIALOG", "MENU_MIDI_DIALOG", "MENU_SERIAL_DIALOG",
)
