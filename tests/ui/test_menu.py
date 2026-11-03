"""The in-window menu: the ``crude.ui.menu`` objects and the ``MENU_*`` bindings.

Two layers get tested here because the feature spans both. The UI half is driven
with plain Python callbacks and no interpreter (a menu is a tree of widgets, so a
paper test can build one and click a row with three lambdas); the binding half
runs ``.crude`` through a :class:`~crude.lang.Program` over a
:class:`~crude.ui.context.UIContext` on a ``RecordingRenderer``, the same shape as
:mod:`tests.ui.test_bindings_ui` - no display, no GPU.

The contract that matters is the click order: a bar title toggles its popup and
closes any other, and picking an item hides the popup *before* the project's
callback runs. The last test covers the device dialog the File menu opens - a
``DROPDOWN`` filled from ``AUDIO_DEVICES`` - against a stub audio runtime, because
populating a menu's rows with the machine's real ports is the whole point.
"""

from __future__ import annotations

import types

import pytest

import crude.bindings  # noqa: F401 - registers every area, including the menu
from crude.bindings.registry import REGISTRY
from crude.bindings.menu import BAR_KIND, MENU_KIND
from crude.lang import Program
from crude.lang.values import HostHandle, List, Number, String
from crude.ui import UIContext, Widget
from crude.ui.menu import (
    APPLY_HEIGHT, DIALOG_MARGIN, DIALOG_MAX_HEIGHT, LIST_ROW_HEIGHT,
    LIST_VIEW_HEIGHT, MenuBar, POPUP_PAD, TITLE_HEIGHT,
)


# -- helpers ------------------------------------------------------------


def as_py(value):
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, List):
        return [as_py(item) for item in value.elements]
    if isinstance(value, HostHandle):
        return value.target
    return value


def widget_of(value):
    assert isinstance(value, HostHandle), value
    assert value.kind == "widget", value.kind
    return value.target


class FakeAudioDevice:
    def __init__(self, index, name, direction):
        self.index = index
        self._said = f"[{index}] {name} ({direction})"

    def describe(self):
        return self._said


class FakeAudioConfig:
    """What a running stream says about itself, as the dialog seeds from it."""

    def __init__(self):
        self.output_device = 0
        self.input_device = 0
        self.output_devices = ()
        self.input_devices = ()
        self.sample_rate = 44100
        self.block_size = 64
        self.delay_ms = 0
        self.callbacks = True


class FakeAudioRuntime:
    """Just enough of an audio runtime for the dialog to read and to apply."""

    def __init__(self):
        self.switches = []
        self.settings = []
        self.config = FakeAudioConfig()

    def devices(self, direction=None):
        ways = (direction,) if direction else ("input", "output")
        found = []
        for way in ways:
            names = ("Microphone", "Line in") if way == "input" \
                else ("Speakers", "Headphones")
            found.extend(FakeAudioDevice(i, n, way)
                         for i, n in enumerate(names))
        return found

    def switch_devices(self, input_device=None, output_device=None, **_kw):
        self.switches.append((input_device, output_device))

    def apply_settings(self, **kwargs):
        self.settings.append(kwargs)
        return "audio on [0] Speakers at " \
            f"{kwargs.get('sample_rate')} Hz"

    def describe(self):
        return "[0] Speakers"


class FakeSwitchAudio(FakeAudioRuntime):
    """A runtime with no settings page: the dialog falls back to switching."""

    def __init__(self):
        super().__init__()
        self.config = None

    apply_settings = None


class FakeMidiDevice:
    def __init__(self, index, name, direction):
        self.index = index
        self.direction = direction
        self._said = f"[{index}] {name} ({direction})"

    def describe(self):
        return self._said


class FakeMidiPort:
    """The shape of ``MidiPort`` the dialog speaks to: direction, index, open."""

    def __init__(self, port_id, direction, index, name=""):
        self.id = port_id
        self.direction = direction
        self.index = index
        self.name = name
        self.opened = True

    def describe(self):
        return f"{self.direction} {self.name} #{self.id}"


class FakeMidiRuntime:
    """A MIDI runtime with one port per direction, recording ``open`` calls."""

    def __init__(self):
        self.opens = []
        self.closes = []
        self.ports = {}

    def devices(self, direction):
        names = ("Keytar",) if direction == "input" else ("Synth",)
        return [FakeMidiDevice(i, n, direction) for i, n in enumerate(names)]

    def open(self, direction, index, name=""):
        self.opens.append((direction, index))
        port = FakeMidiPort(len(self.ports) + 1, direction, index, name)
        self.ports[port.id] = port
        return port

    def close(self, port):
        self.closes.append(getattr(port, "id", port))
        self.ports.pop(getattr(port, "id", port), None)
        return True


class FakeSerialLink:
    def __init__(self, link_id, baud):
        self.id = link_id
        self.baud = int(baud)
        self.sets = []

    def describe(self):
        return f"{self.id} loop {self.baud} line"

    def set_prop(self, name, value):
        self.sets.append((name, value))
        if name == "baud":
            self.baud = int(value)


class FakeSerialRuntime:
    """A serial runtime whose ``open`` makes a link that records baud writes."""

    def __init__(self):
        self.links = {}

    def open(self, decl, codec="line"):
        link = FakeSerialLink(decl.id, decl.baud)
        self.links[decl.id] = link
        return link


@pytest.fixture
def ctx():
    return UIContext(width=400, height=240)


# -- the UI objects, with no interpreter -------------------------------

def test_a_bar_and_a_menu_compose_plain_widgets(ctx):
    bar = MenuBar(ctx, "File")
    menu = bar.menu("Audio")
    assert isinstance(menu.button, Widget) and menu.button.kind.name == "button"
    assert menu.popup.kind.name == "panel"
    assert menu.button in bar.panel.children
    # the popup hangs off the window's root, not the bar, so it can overlap
    assert menu.popup in ctx.root.children
    # and it starts hidden
    assert menu.is_open() is False
    assert menu.popup.get("visible") is False


def test_show_reveals_and_hide_packs_away(ctx):
    bar = MenuBar(ctx, "File")
    menu = bar.menu("Audio")
    menu.show()
    assert menu.is_open() is True
    assert menu.popup.get("visible") is True
    menu.hide()
    assert menu.is_open() is False


def test_a_title_click_toggles_its_popup(ctx):
    bar = MenuBar(ctx, "File")
    menu = bar.menu("Audio")
    menu.button.on_click(1, 1)          # a click is a press and a release
    assert menu.is_open() is True
    menu.button.on_click(1, 1)
    assert menu.is_open() is False


def test_opening_one_menu_closes_the_one_that_was(ctx):
    bar = MenuBar(ctx, "File")
    audio = bar.menu("Audio")
    midi = bar.menu("MIDI")
    audio.show()
    midi.show()
    assert audio.is_open() is False
    assert midi.is_open() is True


def test_picking_an_item_hides_then_runs_its_callback(ctx):
    bar = MenuBar(ctx, "File")
    menu = bar.menu("File")
    seen = []
    item = menu.add_item("Save", callback=lambda row: seen.append(row.label))
    menu.show()
    item.button.on_click(0, 0)
    assert menu.is_open() is False          # hidden first ...
    assert seen == ["Save"]                 # ... and then the callback ran


def test_a_checkable_row_latches_before_the_callback_reads_it(ctx):
    bar = MenuBar(ctx, "File")
    menu = bar.menu("Audio")
    states = []
    item = menu.add_item("Monitor", checkable=True, on=False,
                         callback=lambda row: states.append(row.checked))
    menu.show()
    item.button.on_click(0, 0)
    assert item.checked is True
    assert states == [True]                 # the toggle flipped before we looked


def test_trigger_clicks_a_row_without_a_pointer(ctx):
    bar = MenuBar(ctx, "File")
    menu = bar.menu("File")
    fired = []
    item = menu.add_item("Quit", callback=lambda row: fired.append(1))
    item.trigger()
    assert fired == [1]


# -- the bindings, as a script uses them -------------------------------


@pytest.fixture
def stage():
    ctx = UIContext(width=400, height=240)
    program = Program("<menu-test>", host=types.SimpleNamespace(ui=ctx))
    return program, ctx


@pytest.fixture
def run(stage):
    program, _ = stage

    def _run(source):
        value, error = program.run(source)
        assert error is None, error.as_string()
        return value

    return _run


def test_menubar_returns_a_bar_handle(run):
    value = run('MENUBAR("File")')
    assert isinstance(value, HostHandle) and value.kind == BAR_KIND


def test_menu_returns_a_menu_handle(run):
    value = run('MENU(MENUBAR("File"), "Audio")')
    assert isinstance(value, HostHandle) and value.kind == MENU_KIND


def test_menu_add_returns_the_item_widget_handle(run):
    value = run('''
        VAR b = MENUBAR("File")
        VAR m = MENU(b, "File")
        MENU_ADD(m, "Save", FUNC() -> PRINT("saved"))
    ''')
    assert isinstance(value, HostHandle) and value.kind == "widget"


def test_show_then_a_real_click_on_the_title_toggles_the_popup(run, stage):
    _, ctx = stage
    run('MENU_SHOW(MENU(MENUBAR("File"), "Audio"))')
    popup = ctx.find("Audio#popup")
    assert popup.get("visible") is True
    # the bar title hides it again
    ctx.find("Audio#bar").on_click(1, 1)
    assert popup.get("visible") is False


def test_picking_a_script_row_hides_the_popup_and_runs_the_function(run, stage):
    _, ctx = stage
    run('''
        VAR b = MENUBAR("File")
        VAR m = MENU(b, "File")
        VAR row = MENU_ADD(m, "Rename", FUNC() -> SET(row, "label", "did it"))
        MENU_SHOW(m)
    ''')
    item = ctx.find("File#Rename")
    assert item.get("label") == "Rename"
    assert ctx.find("File#popup").get("visible") is True
    item.on_click(0, 0)
    assert ctx.find("File#popup").get("visible") is False
    assert item.get("label") == "did it"


def test_menu_check_makes_a_toggle_row(run, stage):
    _, ctx = stage
    run('''
        VAR b = MENUBAR("File")
        VAR m = MENU(b, "Audio")
        VAR t = MENU_CHECK(m, "Monitor", 0, FUNC() -> PRINT("toggled"))
    ''')
    toggle = ctx.find("Audio#Monitor")
    assert toggle.kind.name == "toggle"
    assert toggle.get("value") == 0
    toggle.on_click(0, 0)
    assert toggle.get("value") == 1


def test_a_device_dropdown_is_filled_from_audio_devices(run, stage):
    """The File menu's audio dialog: rows come straight off ``AUDIO_DEVICES``."""
    ctx = UIContext(width=400, height=240)
    audio = FakeAudioRuntime()
    program = Program("<menu-audio>", host=types.SimpleNamespace(ui=ctx, audio=audio))
    value, error = program.run('''
        VAR b = MENUBAR("File")
        VAR m = MENU(b, "Audio")
        VAR row = MENU_ADD(m, "Devices", FUNC() -> PRINT("configure"))
        VAR pick = DROPDOWN(AUDIO_DEVICES("input"), 0)
    ''')
    assert error is None, error.as_string() if error else None
    dropdown = as_py(value)
    assert isinstance(dropdown, Widget) and dropdown.kind.name == "dropdown"
    labels = dropdown.get("labels")
    assert [str(line) for line in labels] == \
        ["[0] Microphone (input)", "[1] Line in (input)"]


# -- the configuration dialogs -----------------------------------------


def _program(**extras):
    """A program whose host has a UI plus whatever subsystems a dialog needs."""
    ctx = UIContext(width=400, height=240)
    host = types.SimpleNamespace(ui=ctx, **extras)
    return Program("<dialog-test>", host=host), ctx


def _run(program, source):
    value, error = program.run(source)
    assert error is None, error.as_string() if error else None
    return value


def test_a_dialog_reads_its_choices_from_the_provider_and_applies_payloads(ctx):
    """The dialog's contract, with no interpreter: fill on open, map on apply.

    The provider is not run until the panel opens, a dropdown's chosen value is the
    payload behind the label (not the row position), and applying closes the panel.
    """
    applied = []

    def provider(fields):
        fields[0]["choices"] = ["a", "b"]
        fields[0]["payloads"] = [10, 11]
        fields[1]["choices"] = ["c"]
        fields[1]["payloads"] = [20]

    bar = MenuBar(ctx, "menu")
    menu = bar.menu("Audio")
    dialog = menu.add_dialog(
        "Devices",
        [{"kind": "dropdown", "label": "In"}, {"kind": "dropdown", "label": "Out"}],
        provider=provider, on_apply=lambda values: applied.append(values))

    assert dialog.is_open() is False
    assert applied == []                     # creating it touched no runtime
    dialog.open()
    assert dialog.is_open() is True
    assert list(dialog.controls[0].get("labels")) == ["a", "b"]
    dialog.controls[0].set("value", 1)       # pick the second input -> payload 11
    dialog.apply_now()
    assert applied == [[11, 20]]
    assert dialog.is_open() is False, "apply packs the panel away"


def test_a_number_dialog_field_applies_its_value(ctx):
    """A ``number`` field hands the raw value straight to ``on_apply``."""
    applied = []
    bar = MenuBar(ctx, "menu")
    menu = bar.menu("Serial")
    dialog = menu.add_dialog(
        "Baud", [{"kind": "number", "label": "Baud",
                  "min": 300, "max": 1000000, "value": 115200}],
        on_apply=lambda values: applied.append(values))
    dialog.open()
    assert dialog.controls[0].get("value") == 115200.0
    dialog.controls[0].set("value", 9600)
    dialog.apply_now()
    assert applied == [[9600.0]]


def test_the_title_sits_at_the_top_of_the_panel(ctx):
    """A dialog's name is its first row, not a panel label floating mid-box."""
    bar = MenuBar(ctx, "menu")
    dialog = bar.menu("Audio").add_dialog(
        "Devices", [{"kind": "number", "label": "Rate",
                     "min": 1, "max": 192000, "value": 44100}])
    assert dialog.popup.get("label") == "" or str(dialog.popup.get("label")) == ""
    assert dialog.title_widget.y == POPUP_PAD
    assert dialog.title_widget.get("role") == "title"
    # the title is the only row measured from the panel's own corner: everything
    # else is inside the body, at the edge of the content the wheel slides, and
    # the body itself starts under the title
    assert dialog.blocks[0]["y"] == 0.0
    assert dialog.body.y >= POPUP_PAD + TITLE_HEIGHT
    assert dialog.blocks[0]["label"].is_in(dialog.body)


def test_a_list_field_answers_every_toggled_payload(ctx):
    """A device list is one toggle per row; ``chosen`` is the list of payloads."""
    bar = MenuBar(ctx, "menu")
    menu = bar.menu("Audio")
    applied = []
    dialog = menu.add_dialog(
        "Devices",
        [{"kind": "list", "label": "Outputs", "provider": "outs"}],
        provider=lambda: {"outs": {"choices": ["a", "b", "c"],
                                   "payloads": [7, 8, 9],
                                   "selected": [0, 2]}},
        on_apply=lambda values: applied.append(values))
    dialog.open()
    assert dialog.chosen() == [[7, 9]]
    dialog.lists[0]["rows"][1][1].on_click(0, 0)      # toggle "b" on as well
    assert dialog.chosen() == [[7, 8, 9]]
    dialog.apply_now()
    assert applied == [[[7, 8, 9]]]


def test_a_list_scrolls_inside_its_viewport(ctx):
    """More devices than fit: the wheel slides the rows and stops at the end."""
    bar = MenuBar(ctx, "menu")
    dialog = bar.menu("Audio").add_dialog(
        "Devices",
        [{"kind": "list", "label": "Outputs", "provider": "outs"}],
        provider=lambda: {"outs": {"choices": [f"d{n}" for n in range(10)],
                                   "payloads": list(range(10))}})
    dialog.open()
    viewport = dialog.lists[0]["viewport"]
    assert viewport.height == LIST_VIEW_HEIGHT
    assert len(dialog.lists[0]["rows"]) == 10        # all of them are built ...
    travel = dialog.lists[0]["travel"]
    assert travel == 10 * LIST_ROW_HEIGHT - (LIST_VIEW_HEIGHT - 2 * POPUP_PAD)
    offset = dialog.scroll_to(0, -1)
    assert offset < 0.0
    # the content panel is placed *at* the offset prop, and the viewport's own
    # padding is what insets it: the box and the number a script reads are one
    assert dialog.lists[0]["inner"].y == offset
    for _ in range(20):                               # ... and the wheel clamps
        offset = dialog.scroll_to(0, -1)
    assert round(offset, 3) == round(-travel, 3)
    for _ in range(30):
        offset = dialog.scroll_to(0, 1)
    assert offset == 0.0, "scrolling back up stops at the first row"


def test_a_long_page_is_scrolled_rather_than_squeezed(ctx):
    """Four device lists do not shrink: the body takes the overflow.

    The dialog used to hand every list a smaller window when the page grew too
    tall for the panel. A scroll area makes that unnecessary - each list keeps
    the view it was built with, the panel still ends inside the window, and the
    wheel reaches the rows the window has no room for.
    """
    bar = MenuBar(ctx, "menu")
    dialog = bar.menu("Audio").add_dialog(
        "Devices",
        [{"kind": "list", "label": f"List {n}", "provider": f"p{n}"}
         for n in range(4)],
        provider=lambda: {f"p{n}": {
            "choices": [f"o{i}" for i in range(12)],
            "payloads": list(range(12))} for n in range(4)})
    dialog.open()
    assert dialog.popup.height <= DIALOG_MAX_HEIGHT
    assert (dialog.popup.y + dialog.popup.height
            <= ctx.height - DIALOG_MARGIN)
    for index in range(4):
        assert dialog.lists[index]["viewport"].height == LIST_VIEW_HEIGHT
    content = float(dialog.body.get("content"))
    travel = float(dialog.body.get("travel"))
    assert travel > 0.0, "the page is longer than the room it was given"
    assert travel == content - (dialog.body.height - 2 * POPUP_PAD)
    # and the wheel answers for the page, not only for one list inside it
    dialog.body.on_scroll(0.0, -3 * 16.0, 0)
    assert dialog.body.get("offset") == max(-travel, -3 * 44.0)


def test_a_small_window_gets_a_page_that_ends_inside_it():
    """A 360x220 window: the panel is the room it was given, not a fixed box.

    The regression this guards is the one a person sees first - a settings page
    whose buttons are below the bottom edge of the screen, unreachable because
    nothing in the dialog knew how tall the window was.
    """
    ctx = UIContext(width=360, height=220)
    dialog = MenuBar(ctx, "menu").menu("Audio").add_dialog(
        "Devices", [{"kind": "number", "label": "Rate", "min": 1,
                     "max": 192000, "value": 44100}],
        buttons=["Rescan", "Cancel"])
    dialog.open()
    assert dialog.popup.y + dialog.popup.height <= 220 - DIALOG_MARGIN
    assert dialog.popup.x + dialog.popup.width <= 360 - DIALOG_MARGIN
    # the row's own screen box, not its y inside the panel: what a person is
    # asking is where the buttons are on the window
    row = dialog.actions.screen_box
    assert row[1] + row[3] == pytest.approx(
        dialog.popup.y + dialog.popup.height - POPUP_PAD)


def test_the_buttons_are_pinned_to_the_bottom_of_a_tall_page():
    """A 900x700 window: the buttons sit at the panel's bottom, not its rows'."""
    ctx = UIContext(width=900, height=700)
    dialog = MenuBar(ctx, "menu").menu("Audio").add_dialog(
        "Devices", [{"kind": "number", "label": "Rate", "min": 1,
                     "max": 192000, "value": 44100}],
        buttons=["Rescan", "Cancel"])
    dialog.open()
    ctx.layout()
    assert dialog.actions.screen_box[1] == pytest.approx(
        dialog.popup.y + dialog.popup.height - POPUP_PAD - APPLY_HEIGHT)
    assert [button.y for button in dialog.action_buttons] == [0.0, 0.0, 0.0]
    assert dialog.popup.height <= DIALOG_MAX_HEIGHT, \
        "a tall window does not get a stretched form"


def test_a_resize_keeps_the_buttons_at_the_bottom_and_grows_the_band():
    """``set_size`` and one layout pass: the scroll area takes the new room.

    Nothing re-builds the page: the anchors the dialog wrote once are what the
    pass reads, which is why the caption and the buttons do not need to be moved
    by hand a second time and why the band between them is the only thing that
    changes size.
    """
    ctx = UIContext(width=400, height=240)
    dialog = MenuBar(ctx, "menu").menu("Audio").add_dialog(
        "Devices", [{"kind": "number", "label": "Rate", "min": 1,
                     "max": 192000, "value": 44100}],
        buttons=["Rescan", "Cancel"])
    dialog.open()
    ctx.layout()
    before = dialog.body.height
    ctx.set_size(900, 700)
    ctx.layout()
    assert dialog.body.height > before
    assert dialog.actions.screen_box[1] == pytest.approx(
        dialog.popup.y + dialog.popup.height - POPUP_PAD - APPLY_HEIGHT)
    assert dialog.popup.y + dialog.popup.height <= 700 - DIALOG_MARGIN
    said = dialog.status.screen_box
    assert said[1] + said[3] <= dialog.actions.screen_box[1]


def test_a_title_drag_moves_the_page_and_clamps_at_the_edge():
    """Grabbing the title row moves the panel, and everything glued to it goes.

    The rows are not slid by hand: the drag writes the panel's corner and the
    next pass re-reads every child from it, which is the same mechanism a resize
    runs through. A drag past the edge stops at the edge rather than putting half
    a settings page outside the window.
    """
    ctx = UIContext(width=900, height=700)
    dialog = MenuBar(ctx, "menu").menu("Audio").add_dialog(
        "Devices", [{"kind": "list", "label": "Outputs", "provider": "outs"}],
        provider=lambda: {"outs": {"choices": [f"d{n}" for n in range(4)],
                                   "payloads": list(range(4))}},
        buttons=["Rescan", "Cancel"])
    dialog.open()
    ctx.layout()
    row = dialog.body.content_child.children[0]
    was = (dialog.popup.x, dialog.popup.y, row.screen_box[1])
    dialog.title_widget.on_press(was[0] + 20.0, was[1] + 5.0, 0, 0)
    dialog.title_widget.on_drag(13.0, 27.0, 0)
    dialog.title_widget.on_release(was[0] + 33.0, was[1] + 32.0, 0, 0)
    ctx.layout()
    assert dialog.popup.x == pytest.approx(was[0] + 13.0)
    assert dialog.popup.y == pytest.approx(was[1] + 27.0)
    assert row.screen_box[1] == pytest.approx(was[2] + 27.0)
    # and a page thrown at the corner is caught by the client rect, not lost
    dialog.move(9000.0, 9000.0)
    assert dialog.popup.x + dialog.popup.width == pytest.approx(
        900 - DIALOG_MARGIN)
    assert dialog.popup.y + dialog.popup.height == pytest.approx(
        700 - DIALOG_MARGIN)


def test_a_quick_button_writes_the_field_and_re_reads(ctx):
    """A button grid calls its field's ``on_button`` and the page follows it."""
    seen = []
    rate = {"kind": "number", "label": "Rate", "provider": "rate",
            "min": 1, "max": 192000, "value": 1}
    dialog = MenuBar(ctx, "menu").menu("Audio").add_dialog(
        "Devices",
        [rate, {"kind": "buttons", "label": "", "columns": 2,
                "labels": ["44.1", "48"],
                "on_button": lambda position: seen.append(position)}],
        provider=lambda: {"rate": {"value": 48000}})
    dialog.open()
    assert dialog.controls[0].get("value") == 48000.0
    dialog.blocks[1]["buttons"][0].on_click(0, 0)
    assert seen == [0]


def test_a_refused_setting_stays_on_the_screen(ctx):
    """An apply that raises leaves the panel open and says what went wrong."""
    def refuse(_values):
        raise RuntimeError("no such sample rate")

    dialog = MenuBar(ctx, "menu").menu("Audio").add_dialog(
        "Devices", [{"kind": "number", "label": "Rate", "min": 1,
                     "max": 192000, "value": 8000}],
        on_apply=refuse)
    dialog.open()
    dialog.apply_now()
    assert dialog.is_open() is True
    assert "RuntimeError: no such sample rate" in str(
        dialog.status.get("label"))


def test_an_answer_from_the_runtime_is_read_on_the_status_line(ctx):
    """Words the binding returns are what the person gets to keep."""
    dialog = MenuBar(ctx, "menu").menu("Audio").add_dialog(
        "Devices", [{"kind": "number", "label": "Rate", "min": 1,
                     "max": 192000, "value": 8000}],
        on_apply=lambda _values: "audio on [0] Speakers")
    dialog.open()
    dialog.apply_now()
    assert str(dialog.status.get("label")) == "audio on [0] Speakers"
    assert dialog.is_open() is False, "a setting that applied may pack away"


def test_a_refresh_between_edits_does_not_rewind_them(ctx):
    """A provider that says nothing about a number leaves the edit alone."""
    dialog = MenuBar(ctx, "menu").menu("Audio").add_dialog(
        "Devices", [{"kind": "number", "label": "Rate", "provider": "rate",
                     "min": 1, "max": 192000, "value": 44100}],
        provider=lambda: {})
    dialog.open()
    dialog.controls[0].set("value", 22050)
    dialog.refresh()
    assert dialog.controls[0].get("value") == 22050.0


def test_rescanning_keeps_a_pick_and_shows_a_new_device(ctx):
    """Rescan re-reads the lists: the same devices keep the toggles they have."""
    shown = [["a", "b"], ["a", "b", "c"]]
    payloads = [[1, 2], [1, 2, 3]]
    calls = []

    def provider():
        position = min(len(calls), 1)     # the first read sees two, then three
        calls.append(1)
        return {"outs": {"choices": shown[position],
                         "payloads": payloads[position]}}

    dialog = MenuBar(ctx, "menu").menu("Audio").add_dialog(
        "Devices", [{"kind": "list", "label": "Outputs", "provider": "outs"}],
        provider=provider, buttons=["Rescan", "Cancel"])
    dialog.open()
    dialog.lists[0]["rows"][1][1].on_click(0, 0)          # pick "b"
    assert dialog.chosen() == [[2]]
    said = dialog.rescan()
    assert dialog.chosen() == [[2]], "a rescan is not a rewind"
    assert "3 row(s)" in said, "the device that appeared is listed now"


def test_menu_audio_dialog_applies_the_picked_device_index():
    """One dropdown per direction, and Apply hands the runtime the indexes."""
    audio = FakeSwitchAudio()
    program, ctx = _program(audio=audio)
    _run(program, '''
        VAR b = MENUBAR("menu")
        VAR m = MENU(b, "Audio")
        MENU_AUDIO_DIALOG(m, "Devices")
    ''')
    ctx.find("Audio#Devices").on_click(0, 0)          # open -> provider fills
    out_pick = ctx.find("Audio#audio#choice3")         # the output dropdown
    assert list(out_pick.get("labels")) == \
        ["[0] Speakers (output)", "[1] Headphones (output)"]
    input_pick = ctx.find("Audio#audio#choice4")       # the input dropdown
    assert list(input_pick.get("labels")) == \
        ["[0] Microphone (input)", "[1] Line in (input)"]
    input_pick.set("value", 1)
    ctx.find("Audio#audio#apply").on_click(0, 0)
    assert audio.switches == [(1, 0)], "input second port, output default"


def test_menu_audio_dialog_seeds_every_field_from_the_running_stream():
    """Opening the page reads the live stream: devices, rate, block, delay."""
    audio = FakeAudioRuntime()
    program, ctx = _program(audio=audio)
    _run(program, '''
        VAR b = MENUBAR("menu")
        VAR m = MENU(b, "Audio")
        MENU_AUDIO_DIALOG(m, "Devices")
    ''')
    ctx.find("Audio#Devices").on_click(0, 0)
    assert ctx.find("Audio#audio#value5").get("value") == 44100.0
    assert ctx.find("Audio#audio#value7").get("value") == 0.0
    assert ctx.find("Audio#audio#value8").get("value") == 64.0
    assert bool(ctx.find("Audio#audio#toggle10").get("value")) is True
    # the device in use is the one row already toggled on in each list
    assert bool(ctx.find("Audio#audio#list1#row0").get("value")) is True
    assert bool(ctx.find("Audio#audio#list1#row1").get("value")) is False
    assert bool(ctx.find("Audio#audio#list2#row0").get("value")) is True


def test_menu_audio_dialog_multiple_devices_applies_every_toggled_row():
    """With the switch on, each toggled device is handed to the runtime."""
    audio = FakeAudioRuntime()
    program, ctx = _program(audio=audio)
    _run(program, '''
        VAR b = MENUBAR("menu")
        VAR m = MENU(b, "Audio")
        MENU_AUDIO_DIALOG(m, "Devices")
    ''')
    ctx.find("Audio#Devices").on_click(0, 0)
    ctx.find("Audio#audio#toggle0").on_click(0, 0)      # multiple devices on
    ctx.find("Audio#audio#list1#row1").on_click(0, 0)   # headphones too
    ctx.find("Audio#audio#list2#row1").on_click(0, 0)   # and line in
    ctx.find("Audio#audio#apply").on_click(0, 0)
    assert len(audio.settings) == 1
    said = audio.settings[0]
    assert said["output_device"] == 0
    assert tuple(said["extra_output_devices"]) == (1,)
    assert said["input_device"] == 0
    assert tuple(said["extra_input_devices"]) == (1,)
    assert said["sample_rate"] == 44100
    assert said["block_size"] == 64


def test_menu_audio_dialog_without_the_switch_keeps_one_device():
    """Two toggled rows and the switch off is still a single-device stream."""
    audio = FakeAudioRuntime()
    program, ctx = _program(audio=audio)
    _run(program, '''
        VAR b = MENUBAR("menu")
        VAR m = MENU(b, "Audio")
        MENU_AUDIO_DIALOG(m, "Devices")
    ''')
    ctx.find("Audio#Devices").on_click(0, 0)
    ctx.find("Audio#audio#list1#row1").on_click(0, 0)   # toggled, switch off
    ctx.find("Audio#audio#apply").on_click(0, 0)
    assert audio.settings[0]["output_device"] == 0
    assert tuple(audio.settings[0]["extra_output_devices"]) == ()


def test_menu_audio_dialog_quick_buttons_write_the_fields_beside_them():
    """The Pure Data quick rates and blocksisms set the number they belong to."""
    audio = FakeAudioRuntime()
    program, ctx = _program(audio=audio)
    _run(program, '''
        VAR b = MENUBAR("menu")
        VAR m = MENU(b, "Audio")
        MENU_AUDIO_DIALOG(m, "Devices")
    ''')
    ctx.find("Audio#Devices").on_click(0, 0)
    rates = [button.get("label") for button
             in ctx.find("Audio#audio").descendants()
             if "#quick6" in button.name]
    assert [str(text) for text in rates] == \
        ["7.83", "14.1", "20.3", "26.3", "32.5", "44.1", "48", "96"]
    ctx.find("Audio#audio#quick6-6").on_click(0, 0)     # the "48" button
    assert ctx.find("Audio#audio#value5").get("value") == 48000.0
    ctx.find("Audio#audio#quick9-5").on_click(0, 0)     # the "2048" button
    assert ctx.find("Audio#audio#value8").get("value") == 2048.0


def test_menu_audio_dialog_rescan_keeps_the_page_and_the_picks():
    """Rescan re-reads the machine in place; it neither applies nor rewinds."""
    audio = FakeAudioRuntime()
    program, ctx = _program(audio=audio)
    _run(program, '''
        VAR b = MENUBAR("menu")
        VAR m = MENU(b, "Audio")
        MENU_AUDIO_DIALOG(m, "Devices")
    ''')
    ctx.find("Audio#Devices").on_click(0, 0)
    ctx.find("Audio#audio#list1#row1").on_click(0, 0)
    ctx.find("Audio#audio#rescan").on_click(0, 0)
    assert ctx.find("Audio#audio").get("visible") is True, "it stays open"
    assert audio.settings == [], "a rescan applies nothing"
    assert bool(ctx.find("Audio#audio#list1#row1").get("value")) is True
    # a device that appeared since the page opened is listed by the rescan
    audio.devices = lambda direction=None: [
        FakeAudioDevice(0, "Speakers", "output"),
        FakeAudioDevice(1, "Headphones", "output"),
        FakeAudioDevice(2, "Monitor", "output"),
    ]
    ctx.find("Audio#audio#rescan").on_click(0, 0)
    assert str(ctx.find("Audio#audio#status").get("label")).startswith(
        "rescanned:")
    assert ctx.find("Audio#audio#list1#row2") is not None


def test_menu_audio_dialog_cancel_applies_nothing_and_is_forgotten():
    """Cancel throws the edits away; the next open reads the stream again."""
    audio = FakeAudioRuntime()
    program, ctx = _program(audio=audio)
    _run(program, '''
        VAR b = MENUBAR("menu")
        VAR m = MENU(b, "Audio")
        MENU_AUDIO_DIALOG(m, "Devices")
    ''')
    ctx.find("Audio#Devices").on_click(0, 0)
    ctx.find("Audio#audio#value5").set("value", 8000)
    ctx.find("Audio#audio#cancel").on_click(0, 0)
    assert audio.settings == []
    assert ctx.find("Audio#audio").get("visible") is False
    ctx.find("Audio#Devices").on_click(0, 0)
    assert ctx.find("Audio#audio#value5").get("value") == 44100.0, \
        "the declined edit is not remembered as the setting"


def test_menu_audio_dialog_without_audio_shows_one_disabled_row():
    """No audio runtime is not a crash: the lists say so and applying does naught."""
    program, ctx = _program()
    _run(program, '''
        VAR b = MENUBAR("menu")
        VAR m = MENU(b, "Audio")
        MENU_AUDIO_DIALOG(m, "Devices")
    ''')
    ctx.find("Audio#Devices").on_click(0, 0)
    assert str(ctx.find("Audio#audio#list1#row0").get("label")) == \
        "(no devices)"
    assert [str(text) for text in
            ctx.find("Audio#audio#choice3").get("labels")] == ["(no devices)"]
    ctx.find("Audio#audio#apply").on_click(0, 0)        # no runtime, no error


def test_menu_midi_dialog_opens_both_directions():
    """The MIDI dialog turns a toggled row into ``open(direction, index)``."""
    midi = FakeMidiRuntime()
    program, ctx = _program(midi=midi)
    _run(program, '''
        VAR b = MENUBAR("menu")
        VAR m = MENU(b, "MIDI")
        MENU_MIDI_DIALOG(m, "Ports")
    ''')
    ctx.find("MIDI#Ports").on_click(0, 0)
    ctx.find("MIDI#midi#list1#row0").on_click(0, 0)     # Keytar (input)
    ctx.find("MIDI#midi#list2#row0").on_click(0, 0)     # Synth (output)
    ctx.find("MIDI#midi#apply").on_click(0, 0)
    assert midi.opens == [("input", 0), ("output", 0)]


def test_menu_midi_dialog_closes_a_port_whose_row_went_back_off():
    """Apply reconciles: an open port nobody toggled any more gets closed."""
    midi = FakeMidiRuntime()
    midi.open("input", 0, "Keytar")
    program, ctx = _program(midi=midi)
    _run(program, '''
        VAR b = MENUBAR("menu")
        VAR m = MENU(b, "MIDI")
        MENU_MIDI_DIALOG(m, "Ports")
    ''')
    ctx.find("MIDI#Ports").on_click(0, 0)
    assert bool(ctx.find("MIDI#midi#list1#row0").get("value")) is True, \
        "the open port's row starts toggled on"
    ctx.find("MIDI#midi#list1#row0").on_click(0, 0)     # off again
    ctx.find("MIDI#midi#apply").on_click(0, 0)
    assert midi.closes == [1]
    assert midi.ports == {}


def test_menu_midi_dialog_without_midi_is_a_disabled_row():
    program, ctx = _program()
    _run(program, '''
        VAR b = MENUBAR("menu")
        VAR m = MENU(b, "MIDI")
        MENU_MIDI_DIALOG(m, "Ports")
    ''')
    ctx.find("MIDI#Ports").on_click(0, 0)
    assert str(ctx.find("MIDI#midi#list1#row0").get("label")) == \
        "(no devices)"
    assert str(ctx.find("MIDI#midi#list2#row0").get("label")) == \
        "(no devices)"
    ctx.find("MIDI#midi#apply").on_click(0, 0)


def test_menu_serial_dialog_writes_the_new_baud_to_the_link():
    """A baud field applies through the same ``set_prop`` a ``SET(link)`` uses."""
    serial = FakeSerialRuntime()
    program, ctx = _program(serial=serial)
    _run(program, '''
        VAR b = MENUBAR("menu")
        VAR s = MENU(b, "Serial")
        VAR port = SERIAL_OPEN("arduino", "loop://arduino", 115200)
        MENU_SERIAL_DIALOG(s, port, "Baud")
    ''')
    ctx.find("Serial#Baud").on_click(0, 0)
    field = ctx.find("Serial#serial#value0")
    assert field.get("value") == 115200.0, "seeded at the link's current baud"
    field.set("value", 9600)
    ctx.find("Serial#serial#apply").on_click(0, 0)
    assert serial.links["arduino"].sets == [("baud", 9600)]


# -- the registry -------------------------------------------------------

def test_the_menu_module_names_exactly_these_bindings():
    from crude.bindings.menu import BINDINGS
    assert BINDINGS == (
        "MENUBAR", "MENU", "MENU_ADD", "MENU_CHECK", "MENU_SHOW", "MENU_HIDE",
        "MENU_AUDIO_DIALOG", "MENU_MIDI_DIALOG", "MENU_SERIAL_DIALOG",
    )


def test_every_menu_binding_is_registered_under_the_ui_area():
    from crude.bindings.menu import BINDINGS
    for name in BINDINGS:
        assert name in REGISTRY, name
        assert REGISTRY[name].area == "ui", name


@pytest.mark.parametrize("name,args,optional", [
    ("MENUBAR", ("name",), 1),
    ("MENU", ("bar", "title"), 0),
    ("MENU_ADD", ("menu", "label", "fn"), 0),
    ("MENU_CHECK", ("menu", "label", "on", "fn"), 1),
    ("MENU_SHOW", ("menu",), 0),
    ("MENU_HIDE", ("menu",), 0),
    ("MENU_AUDIO_DIALOG", ("menu", "title"), 1),
    ("MENU_MIDI_DIALOG", ("menu", "title"), 1),
    ("MENU_SERIAL_DIALOG", ("menu", "link", "title"), 1),
])
def test_every_menu_binding_declares_its_arguments(name, args, optional):
    entry = REGISTRY[name]
    assert entry.arg_names == args
    assert entry.optional == optional
