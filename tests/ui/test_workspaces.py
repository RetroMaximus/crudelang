"""Named scenes in one window: who owns a widget, and what a switch moves.

:mod:`crude.ui.workspace` is the plan's stage 2, and the promise it has to keep is
small but exact - *a script that never mentions a workspace behaves exactly as it
did before* - so the first half of this file is the paper half (a context, a few
widgets and plain Python callables, no interpreter) and the second half drives the
same tree through ``WORKSPACE_*`` calls written the way a person would write them.

What gets asserted, in the plan's own words:

* creating a scene switches to it, and ``root`` alone changes nothing;
* an off-screen scene keeps the prop values the person set (``keep`` is a carry,
  not a memory test), while a scene told to rebuild re-runs its ``on_enter``;
* a handler registered in a hidden scene does not fire - proved with a real click
  through :class:`~crude.ui.input.InputRouter`, not by inspecting a table;
* a scene's panel fills the window below the menu bar, at two window sizes, with
  the anchors rather than with arithmetic;
* the menu bar, a menu's popup and a dialog's page are the window's own and stay
  on screen when the scenes change; and a scene cannot swallow one of them.

The numbers are round on purpose: a 400 x 240 window with a bar of ``BAR_HEIGHT``
has a scene of ``240 - BAR_HEIGHT`` under it, and a 320 x 200 widget area is
big enough for a toggle at ``(10, 10, 120, 24)`` to be clicked at its centre.
"""

from __future__ import annotations

import types

import pytest

import crude.bindings  # noqa: F401 - registers every area, including ui
from crude.bindings.menu import MENU_KIND
from crude.bindings.ui import WIDGET_KIND, WORKSPACE_KIND
from crude.lang import Program
from crude.lang.values import HostHandle, List, Number, String
from crude.ui import (ROOT_WORKSPACE, SHARED_WORKSPACE, UIContext, Widget,
                      Workspace, WorkspaceError)
from crude.ui.layout import anchors_of
from crude.ui.menu import BAR_HEIGHT, MenuBar


# -- helpers ------------------------------------------------------------


def as_py(value):
    """A language value as the plain Python object it stands for."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, List):
        return [as_py(item) for item in value.elements]
    if isinstance(value, HostHandle):
        return value.target
    return value


def scene(value):
    """Unwrap a ``WORKSPACE`` answer, and insist it was a scene at all."""
    assert isinstance(value, HostHandle), f"expected a handle, got {value!r}"
    assert value.kind == WORKSPACE_KIND, value.kind
    return value.target


def widget(value):
    assert isinstance(value, HostHandle), value
    assert value.kind == WIDGET_KIND, value.kind
    return value.target


def menu_of(value):
    assert isinstance(value, HostHandle), value
    assert value.kind == MENU_KIND, value.kind
    return value.target


def control(ctx: UIContext, kind: str = "toggle", name: str = "one",
            box=(10.0, 10.0, 120.0, 24.0), **props) -> Widget:
    """One widget added the way a script adds it: through the context, not by hand."""
    held = Widget(kind, {"label": name, **props}, name=name)
    ctx.add(held)
    held.place(*box)
    ctx.layout()
    return held


def centre(held: Widget) -> tuple:
    box = held.screen_box
    return (box[0] + box[2] / 2.0, box[1] + box[3] / 2.0)


@pytest.fixture
def ctx() -> UIContext:
    return UIContext(width=400, height=240)


@pytest.fixture
def stage():
    """A program over a 320 x 200 context - the shape a real project's host has."""
    window = UIContext(width=320, height=200)
    program = Program("<workspace-test>", host=types.SimpleNamespace(ui=window))
    return program, window


@pytest.fixture
def run(stage):
    program, _ = stage

    def _run(source):
        value, error = program.run(source)
        assert error is None, error.as_string()
        return value

    return _run


@pytest.fixture
def eval_ui(run):
    return lambda source: as_py(run(source))


@pytest.fixture
def ui_failure(stage):
    program, _ = stage

    def _fail(source):
        value, error = program.run(source)
        assert error is not None, f"expected a failure, got {value!r}"
        return error.as_string()

    return _fail


# -- the one scene a window starts with --------------------------------


def test_a_window_has_the_entry_scripts_scene_and_nothing_else(ctx):
    """``root`` is there from the first frame, is index 0, and *is* the window's root."""
    held = ctx.workspaces

    assert held.names() == [ROOT_WORKSPACE]
    assert held.current_name == ROOT_WORKSPACE
    assert held.index_of("root") == 0
    assert len(held) == 1
    assert held.current.is_root is True
    assert held.current.panel is None
    assert held.container() is ctx.root
    # and the scene that is not there cannot be shown
    with pytest.raises(WorkspaceError):
        held.get("mixer")


def test_showing_the_only_scene_says_so_and_changes_nothing(ctx):
    """A switch to the scene already on screen is a no-op with a name for an answer."""
    control(ctx)
    before = list(ctx.root.children)

    assert ctx.workspaces.show("root") == ROOT_WORKSPACE
    assert ctx.root.children == before
    assert [held.workspace for held in ctx.root.children] == [ROOT_WORKSPACE], \
        "the one widget this window has belongs to the one scene it has"


def test_every_widget_added_is_stamped_with_the_scene_it_landed_in(ctx):
    """One row on the widget answers both questions a switch has to ask."""
    first = control(ctx, name="in_root")
    assert first.workspace == ROOT_WORKSPACE

    held = ctx.workspaces.declare("mixer")
    ctx.workspaces.show(held.name)
    second = control(ctx, name="in_mixer", box=(20.0, 20.0, 120.0, 24.0))

    assert second.workspace == "mixer"
    # it went into the scene's panel rather than alongside it
    assert second.parent is ctx.workspaces.container(held)
    assert second.parent is not ctx.root
    # and the entry script's widget is still stamped with the scene that made it
    assert first.workspace == ROOT_WORKSPACE


def test_the_reserved_names_are_refused_by_name(ctx):
    """No second ``root``, no ``shared``, no scene declared twice."""
    held = ctx.workspaces
    held.declare("mixer")

    for name, says in ((ROOT_WORKSPACE, "always that one at index 0"),
                       (SHARED_WORKSPACE, "the window's own"),
                       ("mixer", "already a workspace")):
        with pytest.raises(WorkspaceError) as caught:
            held.declare(name)
        assert says in str(caught.value), name


def test_an_unknown_scene_is_refused_by_listing_the_known_ones(ctx):
    held = ctx.workspaces
    held.declare("mixer")

    with pytest.raises(WorkspaceError) as caught:
        held.get("bass")
    assert "'bass'" in str(caught.value)
    assert "root, mixer" in str(caught.value)

    for wrong in ("", None, "  ", True, 47):
        with pytest.raises((WorkspaceError, TypeError)):
            held.get(wrong)


def test_a_scene_is_shown_by_its_name_by_its_number_or_by_the_object(ctx):
    """``WORKSPACE_SHOW("mixer")``, ``(1)`` and the handle ``WORKSPACE`` handed back."""
    held = ctx.workspaces
    mixer = held.declare("mixer")

    assert held.show(1) == "mixer"
    assert held.show("0") == ROOT_WORKSPACE
    assert held.show(mixer) == "mixer"
    assert held.show(held.rows[0]) == ROOT_WORKSPACE
    with pytest.raises(WorkspaceError):
        held.show(7)


def test_a_scene_number_that_arrives_as_a_float_is_still_a_number(ctx):
    """``WORKSPACE_INDEX()`` answers a float, and the door it comes back to reads one.

    Every number this language carries is a float, so the round trip the bindings
    promise - take the current scene's number, show it again - reached :meth:`get`
    as ``1.0``. Read as a name it is a scene called "1.0" and the promise is a lie;
    read as a number it is the second scene, which is what the script meant.
    """
    held = ctx.workspaces
    held.declare("mixer")

    assert held.get(1.0) is held.rows[1]
    assert held.show(0.0) == ROOT_WORKSPACE
    assert held.index_of(1.0) == 1

    with pytest.raises(WorkspaceError) as caught:
        held.get(1.5)
    assert "not a scene number" in str(caught.value), (
        "a part of a scene is not a scene, and says so rather than listing names")


# -- what a switch moves ------------------------------------------------


def test_a_switch_leaves_the_bar_and_the_popup_on_screen_and_moves_the_rest(ctx):
    """The window's own widgets are stamped the window's own, and stay."""
    bar = MenuBar(ctx, "File")
    menu = bar.menu("Audio")
    mine = control(ctx, name="deck")
    ctx.workspaces.declare("mixer")
    ctx.workspaces.show("mixer")

    assert bar.panel in ctx.root.children
    assert menu.popup in ctx.root.children
    assert bar.panel.workspace == SHARED_WORKSPACE
    assert menu.popup.workspace == SHARED_WORKSPACE
    # the entry scene's widget is out of the tree, and still owns its name
    assert mine not in ctx.root.children
    assert mine.workspace == ROOT_WORKSPACE
    assert mine.parent is None

    ctx.workspaces.show(ROOT_WORKSPACE)
    assert mine in ctx.root.children
    assert ctx.root.children.index(mine) > ctx.root.children.index(bar.panel), (
        "a scene that comes back is put on top of the stack again, which is where "
        "a widget added after the bar has always landed")


def test_a_carried_scene_keeps_the_values_the_person_set(ctx):
    """``keep`` is the plan's "a sampler's settings survive a jump to the mixer"."""
    held = ctx.workspaces
    mixer = held.declare("mixer")
    held.show("mixer")
    fader = control(ctx, "slider", "level", (10.0, 10.0, 200.0, 24.0),
                    min=0.0, max=100.0)
    fader.set("value", 73.0)
    assert fader.get("value") == 73.0

    held.show(ROOT_WORKSPACE)
    assert fader.parent is mixer.panel, "it went with the panel it was inside"
    assert mixer.panel.parent is None, "and that panel is out of the window's tree"
    assert mixer.stash == [mixer.panel]

    held.show("mixer")
    back = ctx.find("level")
    assert back is fader
    assert back.get("value") == 73.0, "the carry kept the number, not a copy of it"


def test_a_rebuilt_scene_runs_its_entry_function_every_time_it_is_shown(ctx):
    """``keep=0``: leaving releases the subtree, and only ``on_enter`` puts it back."""
    entries = []

    def build() -> None:
        entries.append(len(entries))
        ctx.add(Widget("toggle", {"label": "again"}, name="again"))

    held = ctx.workspaces
    mixer = held.declare("mixer", on_enter=build)
    assert mixer.keep is False, "declaring an on_enter is a script saying rebuild me"

    held.show("mixer")
    assert entries == [0]
    assert ctx.find("again") is not None

    held.show(ROOT_WORKSPACE)
    assert ctx.find("again") is None, "the release took it, and its handler, with it"

    held.show("mixer")
    assert entries == [0, 1]
    assert ctx.find("again") is not None

    # showing the scene that is already on screen does not rebuild it a second time
    held.show("mixer")
    assert entries == [0, 1]


def test_a_carried_scene_can_be_told_to_rebuild_and_back(ctx):
    """``WORKSPACE_KEEP`` is one flag, and the flag decides what leaving costs."""
    held = ctx.workspaces
    mixer = held.declare("mixer")
    assert mixer.keep is True
    held.show("mixer")
    control(ctx, "toggle", "kept")

    mixer.keep = False
    held.show(ROOT_WORKSPACE)
    assert mixer.stash == [], "a release has nothing to carry back"
    assert mixer.panel is None, "and the panel went with the subtree it held"
    assert ctx.find("kept") is None

    # carrying again starts from an empty page: what was released is really gone
    mixer.keep = True
    held.show("mixer")
    assert mixer.panel is not None
    assert mixer.panel.children == []
    assert ctx.find("kept") is None
    held.show(ROOT_WORKSPACE)
    assert len(mixer.stash) == 1, "the empty panel is all there is to carry now"


def test_the_scene_panel_is_glued_to_the_bar_and_the_window_at_two_sizes(ctx):
    """The geometry is the layout engine's: four anchors, and a resize is answered."""
    bar = MenuBar(ctx, "File")
    held = ctx.workspaces
    mixer = held.declare("mixer")
    held.show("mixer")
    panel = held.container(mixer)
    ctx.layout()

    glued = {rule.edge: rule for rule in anchors_of(panel)}
    assert set(glued) == {"left", "right", "top", "bottom"}
    assert glued["top"].target is bar.panel, "the bar's own panel, not a constant"

    room = ctx.root.content_box()
    assert panel.screen_box == (room[0], room[1] + BAR_HEIGHT,
                                room[2], room[3] - BAR_HEIGHT)

    ctx.set_size(800, 600)
    ctx.layout()
    room = ctx.root.content_box()
    assert panel.screen_box == (room[0], room[1] + BAR_HEIGHT,
                                room[2], room[3] - BAR_HEIGHT), (
        "the same four glues re-resolved: the scene grew by exactly the room the "
        "window gave back, and still starts at the bar's bottom edge")


def test_a_scene_declared_before_the_bar_still_starts_below_it(ctx):
    """Declaration order is not a script's problem: the bar re-glues what it finds."""
    held = ctx.workspaces
    mixer = held.declare("mixer")
    held.show("mixer")
    bar = MenuBar(ctx, "File")
    ctx.layout()

    panel = held.container(mixer)
    assert held.bar is bar.panel
    room = ctx.root.content_box()
    assert panel.screen_box == (room[0], room[1] + BAR_HEIGHT,
                                room[2], room[3] - BAR_HEIGHT)


def test_a_window_with_no_bar_gives_a_scene_the_whole_rect(ctx):
    held = ctx.workspaces
    mixer = held.declare("mixer")
    held.show("mixer")
    ctx.layout()

    assert held.bar is None
    assert held.container(mixer).screen_box == ctx.root.content_box()


# -- the names a project declared before the script ran ----------------


def test_the_manifests_names_are_reserved_and_the_script_fills_them_in(ctx):
    """``ui.workspaces`` says the names; ``WORKSPACE("mixer")`` adopts rather than fights."""
    held = ctx.workspaces
    held.seed(("sampler", "mixer"))
    assert held.names() == [ROOT_WORKSPACE, "sampler", "mixer"]
    assert held.current_name == ROOT_WORKSPACE, "naming a scene is not showing it"
    assert held.describe()[2].endswith("(declared)")

    mixer = held.declare("mixer")
    assert mixer is held.get("mixer")
    assert mixer.seeded is False
    assert held.index_of("mixer") == 2, "the manifest's order, not the script's"
    # a second claim of the same name is still the mistake it always was
    with pytest.raises(WorkspaceError):
        held.declare("mixer")


def test_seeding_leaves_alone_what_the_window_already_has(ctx):
    held = ctx.workspaces
    held.declare("mixer")

    assert [row.name for row in held.seed(("root", "shared", "mixer", "live"))] \
           == ["live"]
    assert held.names() == [ROOT_WORKSPACE, "mixer", "live"]


# -- the bindings, as a script uses them -------------------------------


def test_workspace_answers_the_current_name_until_one_is_declared(eval_ui):
    assert eval_ui("WORKSPACE()") == ROOT_WORKSPACE
    assert eval_ui("WORKSPACE_INDEX()") == 0.0
    assert eval_ui("WORKSPACE_NAMES()") == [ROOT_WORKSPACE]


def test_declaring_a_scene_makes_it_current_and_numbers_it(run, eval_ui):
    handle = scene(run('VAR m = WORKSPACE("mixer")\nm'))
    assert handle.name == "mixer"

    assert eval_ui("WORKSPACE()") == "mixer"
    assert eval_ui("WORKSPACE_NAMES()") == ["root", "mixer"]
    assert eval_ui("WORKSPACE_INDEX()") == 1.0


def test_a_scene_reaches_show_by_its_handle_by_its_name_and_by_its_number(run,
                                                                         eval_ui):
    run('VAR m = WORKSPACE("mixer")\nWORKSPACE_SHOW("root")\n')
    assert eval_ui("WORKSPACE()") == ROOT_WORKSPACE

    assert eval_ui("WORKSPACE_SHOW(m)") == "mixer", "the handle WORKSPACE answered"
    eval_ui('WORKSPACE_SHOW("root")')
    assert eval_ui("WORKSPACE_SHOW(1)") == "mixer", "its number"
    eval_ui("WORKSPACE_SHOW(0)")
    assert eval_ui('WORKSPACE_SHOW("mixer")') == "mixer", "and its name"
    assert eval_ui('WORKSPACE_SHOW("0")') == ROOT_WORKSPACE, (
        "a window may have a scene called \"2\": the name is tried first")


def test_a_scene_number_a_script_stored_comes_back_through_the_door(run, eval_ui):
    """``WORKSPACE_SHOW(WORKSPACE_INDEX())`` is the docs' round trip, not a refusal.

    The number is held in a variable rather than written out, which is the shape a
    `View` menu and a loop over the scene list actually use - and the value a script
    gets back out of the bindings is a float, not the literal it would have typed.
    """
    run('WORKSPACE("mixer")\nWORKSPACE("sequencer")\n')
    assert eval_ui("WORKSPACE_INDEX()") == 2.0

    run("VAR was = WORKSPACE_INDEX()\nWORKSPACE_SHOW(\"root\")\n")
    assert eval_ui("WORKSPACE()") == ROOT_WORKSPACE
    assert eval_ui("WORKSPACE_SHOW(was)") == "sequencer", (
        "the number it left is the number it came back on")


def test_a_widget_built_after_a_switch_lives_in_the_new_scene(run, eval_ui,
                                                              stage):
    """``SLIDER(...)`` still means "put it on screen", and screen means this scene."""
    _, window = stage
    run('WORKSPACE("mixer")\nVAR f = SLIDER("level", 0, 100, 42)\n'
        'PLACE(f, 10, 10, 200, 24)\n')
    fader = widget(run("f"))
    assert fader.workspace == "mixer"
    assert fader.parent.name == "mixer"
    assert window.find("level") is fader
    assert eval_ui('GET(f, "value")') == 42.0

    eval_ui('WORKSPACE_SHOW("root")')
    assert window.find("level") is None, "the scene it belongs to is off screen"
    eval_ui('WORKSPACE_SHOW("mixer")')
    assert window.find("level") is fader
    assert fader.get("value") == 42.0, "carried, not remembered"


def test_a_handler_in_a_hidden_scene_does_not_fire(run, stage):
    """The plan's line, proved with a click: nobody can hit what is not in the tree."""
    _, window = stage
    run(
        'WORKSPACE("mixer")\n'
        'VAR led = METER("seen", 0, 10, 0)\n'
        'VAR b = BUTTON("pick")\n'
        "PLACE(b, 10, 10, 120, 24)\n"
        'ON_CLICK(b, FUNC(r) -> SET(led, "value", GET(led, "value") + 1))\n')
    window.layout()
    # the widgets are held, not looked up: a detached scene is not in the tree to
    # be found by name, which is exactly the fact this test is about
    led = widget(run("led"))
    button = widget(run("b"))
    at = centre(button)
    assert window.hit(*at) is button

    window.router.click(*at)
    assert led.get("value") == 1.0, "on screen, so the row answers"

    run('WORKSPACE_SHOW("root")')
    window.layout()
    assert window.hit(*at) is not button
    window.router.click(*at)
    assert led.get("value") == 1.0, "carried off screen, and unreachable"

    run('WORKSPACE_SHOW("mixer")')
    window.layout()
    window.router.click(*at)
    assert led.get("value") == 2.0, "and it answers again when it is back"


def test_workspace_add_builds_a_scene_that_is_not_on_screen(run, eval_ui, stage):
    """``WORKSPACE_ADD`` asks a *scene* to hold a widget, which ``NODE_ADD`` cannot."""
    _, window = stage
    run(
        'VAR m = WORKSPACE("mixer")\n'
        'WORKSPACE_SHOW("root")\n'
        'VAR b = BUTTON("later")\n'
        "WORKSPACE_ADD(m, b)\n"
        "PLACE(b, 8, 8, 60, 20)\n"
    )
    button = widget(run("b"))
    assert button.parent.name == "mixer"
    assert button.workspace == "mixer"
    assert window.find("later") is None, "the scene is still off screen"

    eval_ui('WORKSPACE_SHOW("mixer")')
    assert window.find("later") is button


def test_a_scene_cannot_swallow_a_widget_that_belongs_to_the_window(run,
                                                                    ui_failure,
                                                                    stage):
    """The menu bar is a real widget with the window's own stamp, and it stays its own."""
    program, window = stage
    run('VAR b = MENUBAR("File")\nVAR m = WORKSPACE("mixer")\n')
    bar = as_py(run("b"))
    assert bar.panel.workspace == SHARED_WORKSPACE
    # the panel a bar is built out of is not a handle a script was handed, so the
    # test gives it one: the same shape an ON_CLICK argument arrives in
    program.symbol_table.set(
        "plate", HostHandle(bar.panel, kind=WIDGET_KIND, label=bar.panel.path()))

    said = ui_failure("WORKSPACE_ADD(m, plate)")
    assert "belongs to the window" in said
    assert bar.panel in window.root.children, "the refusal left it where it was"


def test_keep_zero_in_a_script_re_runs_the_entry_function(run, eval_ui, stage):
    """A ``WORKSPACE(name, fn)`` scene is rebuilt, and the rebuild is what it drew."""
    _, window = stage
    run(
        'VAR n = NUMBER("count", 0, 100, 0)\n'
        'FUNC build()\n'
        '    VAR t = TEXT("fresh")\n'
        "    SET(n, \"value\", GET(n, \"value\") + 1)\n"
        "END\n"
        'VAR m = WORKSPACE("live", build)\n'
        'WORKSPACE_SHOW("root")\n'
    )
    assert window.find("fresh") is None
    assert eval_ui('GET(n, "value")') == 1.0

    eval_ui('WORKSPACE_SHOW("live")')
    assert window.find("fresh") is not None
    assert eval_ui('GET(n, "value")') == 2.0


def test_workspace_keep_answers_the_state_it_set(eval_ui):
    run_src = 'VAR m = WORKSPACE("mixer")\nWORKSPACE_ADD(m, TEXT("x"))'
    eval_ui(run_src)
    assert eval_ui("WORKSPACE_KEEP(m, 0)") == 0
    assert eval_ui("WORKSPACE_KEEP(m, 1)") == 1
    assert eval_ui("WORKSPACE_KEEP(m, 0)") == 0


def test_a_missing_or_wrong_scene_name_says_what_the_window_has(run, ui_failure):
    run('WORKSPACE("mixer")\n')

    said = ui_failure('WORKSPACE_SHOW("bass")')
    assert "no workspace named 'bass'" in said
    assert "root, mixer" in said, "an unknown name lists the known ones"

    said = ui_failure("WORKSPACE_SHOW(7)")
    assert "no workspace number 7" in said
    assert "root, mixer" in said

    said = ui_failure("WORKSPACE_SHOW(1.5)")
    assert "not a scene number" in said

    assert "needs a name" in ui_failure('WORKSPACE("")')
    assert "too few" in ui_failure("WORKSPACE_SHOW()")
    assert "too many" in ui_failure('WORKSPACE_SHOW("mixer", "mixer")')


def test_a_scene_name_may_be_reused_by_a_script_that_declared_it_first(ui_failure):
    said = ui_failure('WORKSPACE("mixer")\nWORKSPACE("mixer")')
    assert "already a workspace" in said


# -- the menu of scenes ------------------------------------------------


def rows(menu):
    return [item.label for item in menu.items]


def test_menu_workspaces_lists_every_scene_and_marks_the_current_one(run,
                                                                     eval_ui,
                                                                     stage):
    """One verb, and a ``View`` menu that knows the window's scenes."""
    _, window = stage
    run('VAR b = MENUBAR("File")\nVAR v = MENU(b, "View")\n'
        'WORKSPACE("mixer")\nWORKSPACE_SHOW("root")\n')
    menu = menu_of(run("v"))
    assert eval_ui("MENU_WORKSPACES(v)") == 2.0, "the answer is the rows it made"
    assert rows(menu) == ["root", "mixer"]
    assert [item.label for item in menu.items if item.checked] == ["root"]
    assert menu.opening, "and it is asked again every time the popup opens"
    assert window.handlers.errors == []


def test_picking_a_workspace_row_switches_the_scene_and_hides_the_popup(run, stage):
    _, window = stage
    run('VAR b = MENUBAR("File")\nVAR v = MENU(b, "View")\n'
        'WORKSPACE("mixer")\nWORKSPACE_SHOW("root")\nMENU_WORKSPACES(v)\n')
    menu = menu_of(run("v"))
    menu.show()
    row = [item for item in menu.items if item.label == "mixer"][0]
    row.trigger()

    assert menu.is_open() is False
    assert window.workspaces.current_name == "mixer"
    assert window.handlers.errors == []


def test_the_rows_are_re_read_when_the_popup_opens(run, stage):
    """A scene declared after the menu was built is on the list without a rebuild."""
    _, window = stage
    run('VAR b = MENUBAR("File")\nVAR v = MENU(b, "View")\nMENU_WORKSPACES(v)\n')
    menu = menu_of(run("v"))
    assert rows(menu) == ["root"]

    run('VAR late = WORKSPACE("sequencer")\nWORKSPACE_SHOW("root")\n')
    menu.show()
    assert rows(menu) == ["root", "sequencer"]
    assert [item.label for item in menu.items if item.checked] == ["root"]


def test_a_row_for_a_scene_that_vanished_says_so_instead_of_raising(run, stage):
    """The pointer dispatch runs a callback, and a callback must not stop a frame."""
    _, window = stage
    run('VAR b = MENUBAR("File")\nVAR v = MENU(b, "View")\n'
        'WORKSPACE("mixer")\nWORKSPACE_SHOW("root")\nMENU_WORKSPACES(v)\n')
    menu = menu_of(run("v"))
    menu.show()
    assert rows(menu) == ["root", "mixer"]

    # the scene goes after the rows were composed, so the row still names a scene
    # the window no longer has - and the click has to be a log line, not a raise
    gone = window.workspaces.get("mixer")
    window.workspaces.rows.remove(gone)
    window.workspaces._by_name.pop("mixer")
    row = [item for item in menu.items if item.label == "mixer"][0]
    row.trigger()

    assert menu.is_open() is False
    assert window.workspaces.current_name == ROOT_WORKSPACE
    assert any("mixer" in line for line in window.handlers.errors), (
        "the refusal is a line in the log, not an exception through the click")
