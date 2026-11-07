"""``ui`` bindings: the widget surface a script builds its front panel from.

Everything here is a thin adapter over the GL-free UI layer
(:mod:`crude.ui`): the tree (:mod:`~crude.ui.widget`), the one prop table every
reader agrees on (:mod:`~crude.ui.props`), the placement pass
(:mod:`~crude.ui.layout`) and the frame (:mod:`~crude.ui.context`). Nothing in
this module knows about GLFW or OpenGL, which is the plan's parity line - the
same calls build the same tree whether the run has a window, a headless
``NullRenderer``, or a :class:`~crude.ui.recording.RecordingRenderer` in a test.

The whole surface follows the plan's five-step shape:

* **create** - ``WIDGET(kind)`` is the only real constructor and every catalog
  word (``SLIDER``, ``TEXT``, ``TOGGLE`` ...) is sugar over it, mapping its
  positional arguments onto the kind's own props (:attr:`Kind.args`). A test
  pins that ``SLIDER("BPM", 20, 300, 120)`` and ``WIDGET("slider")`` plus four
  ``SET`` calls leave a widget holding exactly the same props, because they run
  through the same :meth:`~crude.ui.widget.Widget` constructor and therefore the
  same :meth:`~crude.ui.props.Prop.settle` door;
* **configure** - there is one ``SET``/``GET`` in the language
  (:mod:`crude.bindings.core`); this module *owns* those two words for the
  ``widget`` handle kind through :func:`~crude.bindings.core.own_props`, so a
  write validates against the kind's ``PROPS`` table and a read answers at the
  settled value;
* **place** - ``PLACE`` is window-relative (a widget's parent is the root until
  ``NODE_ADD`` moves it into a ``PANEL``);
* **wire** - ``LINK_VALUE``/``LINK_STATE`` mirror one prop onto another through
  the frame's :class:`~crude.ui.handlers.Handlers`, and every ``ON_*`` installs a
  script function for one event;
* **paint** - ``WIDGET_DEFINE``/``PROP_DEFINE`` add a kind the catalog does not
  have, and ``ON_PAINT`` gives it a face. The ``P_*`` primitives draw on a
  :class:`~crude.ui.paint.Canvas`: local logical pixels from the widget's own
  top-left, clipped to its box, and coloured by *role* through the skin, so a
  script never names a colour and still gets the right one on both themes. The
  ops are recorded once and replayed until a prop, a box, the skin or the device
  ratio moves (:mod:`crude.ui.paint`), which is what keeps a script-painted panel
  inside the plan's frame budget;
* **style / introspect** - ``STYLE`` edits the visual rows and the window's
  theme (a style is a role, never a raw colour), and ``WIDGET_KINDS`` /
  ``WIDGET_PROPS`` / ``WIDGET_HAS`` read the same table ``SET`` validates
  against.

A binding reaches the tree through ``host.ui`` - a
:class:`~crude.ui.context.UIContext` - via :meth:`HostContext.subsystem`, so a
bare ``Program(host=SimpleNamespace(ui=ctx))`` in a test needs no further
wiring, and the context's handler bridge already knows the program's functions.
"""

from __future__ import annotations

from ..lang.values import (
    HostHandle,
    List,
    Number,
    String,
    python_to_value,
    type_name,
    value_to_python,
)
from ..ui import (
    HIT,
    KINDS,
    MODS,
    PAINT,
    ROOT_WORKSPACE,
    SCRIPT_EVENTS,
    SHARED_WORKSPACE,
    TYPES,
    WIDGET_EVENTS,
    Canvas,
    UIError,
    Widget,
    Workspace,
    WorkspaceError,
    catalog_names,
    define,
    define_prop,
    get_kind,
    has as prop_exists,
    prop_names,
    registered_names,
)
# the placement pass, imported under its own names so the verbs below can be called
# ``PACK``, ``PAD`` and ``GRID``. Each binding is one call into
# :mod:`crude.ui.layout`: no geometry is worked out in this module, which is the
# plan's rule for this stage - the engine already existed and nothing in a script
# could reach it.
from ..ui import (
    anchor as layout_anchor,
    anchors_of as layout_anchors,
    arrange as layout_arrange,
    grid as layout_grid,
    hstack as layout_hstack,
    pack as layout_pack,
    pack_of as layout_pack_of,
    pad as layout_pad,
    unanchor as layout_unanchor,
    unstack as layout_unstack,
    vstack as layout_vstack,
)
# the group verbs, each one a call into :mod:`crude.ui.group`: a fold, an order and
# a cell are that module's own answers and nothing below is worked out twice. The
# names are read through ``crude.ui`` like every other UI name, because a binding
# imports the package and never the module behind it.
from ..ui import (
    dock as group_dock,
    fold as group_fold,
    groups_of,
    is_group,
    move_by as group_move_by,
    move_to as group_move_to,
    order_of as group_order,
    undock as group_undock,
)
from .core import own_props
from .handles import values_from
from .registry import Binding, REGISTRY, binding

AREA = "ui"

#: what ``GET``/``SET`` see on a widget handle, and the kind this module owns
WIDGET_KIND = "widget"

#: the kind a workspace handle is stamped with, so ``SET``/``GET`` keep answering
#  for widgets and a scene is read and written only through the verbs below
WORKSPACE_KIND = "workspace"

#: the rows ``STYLE`` may write. A style is a role, a size, an icon or a hover
#  word - never a colour and never a value: those belong to ``SET`` and the skin
#  resolves the colours from the role the script chose.
STYLE_PROPS = ("role", "size", "glyph", "glyph_on", "glyph_off", "tooltip")

#: the catalog kinds that come with a constructor of their own. Every entry is
#  sugar over ``WIDGET``; the slots map onto the kind's own ``args``, except for
#  the three words the plan spells differently from the table:
#
#  * ``TOGGLE(label[, on])`` - ``on`` is the latched *value*, which is a 0/1 row
#    no table can name "on" without restating what a toggle holds;
#  * ``ICON(name, size)`` - ``name`` is the ``glyph`` row (the kind's own first
#    ``args`` entry) and ``size`` is the box it is drawn in, which is placement
#    rather than a prop: it lands as the widget's own box and any later
#    ``PLACE`` overrules it, which is the plan's one answer for size;
#  * ``GROUP(title[, parent])`` - ``title`` is a row and ``parent`` is not: it is
#    the widget the section is built inside, which no prop table can hold.
CONSTRUCTORS: dict = {
    name: (kind, tuple(KINDS[kind].args), len(KINDS[kind].args))
    for name, kind in (
        ("PANEL", "panel"), ("TEXT", "text"), ("BUTTON", "button"),
        ("TOGGLE", "toggle"), ("SLIDER", "slider"), ("VSLIDER", "vslider"),
        ("KNOB", "knob"),
        ("NUMBER", "number"), ("METER", "meter"), ("RADIO", "radio"),
        ("DROPDOWN", "dropdown"),
        # the synth words, sugar over WIDGET like the ten above them: their
        # arguments come straight out of the kind's own ``args``, so
        # ``ADSR(label, a, d, s, r)`` and five SETs are the same five numbers
        ("WAVEFORM", "waveform"), ("SPECTRUM", "spectrum"),
        ("PLOT", "plot"), ("LOGVIEW", "logview"), ("CLIP", "clip"),
        ("ADSR", "adsr"), ("CURVE", "curve"), ("XY_PAD", "xy"),
        ("KEYBED", "keybed"), ("PRESET_BAR", "preset_bar"),
        # the viewport: a panel a script fills with children and the pointer
        # slides, so its one argument is the panel's own label
        ("SCROLL", "scroll"),
    )
}
CONSTRUCTORS["ICON"] = ("icon", ("glyph", "size"), 1)
CONSTRUCTORS["TOGGLE"] = ("toggle", ("label", "on"), 1)
# ``GROUP(title[, parent])`` - the section of a page, named by the word in its
# header. ``parent`` is the second slot rather than a prop: it is the widget the
# group is built *inside*, which is a place in the tree and not a value on it, and
# leaving it out puts the group where every other constructor puts a widget - in the
# scene on screen. The kind's own ``args`` are just ``title``, so this is the third
# word whose slots the table cannot spell out on its own.
CONSTRUCTORS["GROUP"] = ("group", ("title", "parent"), 1)

#: the words ``TOGGLE(label, on)`` reads as on and off, the same list
#  :mod:`crude.ui.props` settles a bool prop with
_ON_WORDS = ("true", "yes", "on", "1")
_OFF_WORDS = ("false", "no", "off", "0")

#: the two words ``GET`` answers for every widget out of the placement pass rather
#  than out of a kind's prop table: ``place`` is the list of glues the widget
#  carries, each one the sentence ``ANCHOR`` was given, and ``pack`` its two resize
#  policies. They are not rows - a widget's anchors are rules, not a value, and a
#  flat table cannot say "a list of these, of a type the table has no word for" -
#  so they are read here, refused by ``SET`` here, and ``WIDGET_HAS`` answers them
#  the way ``GET`` does rather than pretending they do not exist.
LAYOUT_WORDS = ("place", "pack")

#: the option names the arrange verbs take after their own arguments, as a flat
#  ``"name", value`` list - the shape ``PD_GUI``'s ``props`` already uses, because
#  a stack has four words left and nobody can guess a positional slot for each.
#  ``dockable`` belongs to a grid alone: it is the offer of a cell to drop into, and
#  a row or a column has nothing to drop into.
STACK_OPTIONS = ("gap", "pad", "align")
GRID_OPTIONS = ("gap", "pad", "align", "dockable")


# -- helpers ------------------------------------------------------------


def _window(ctx):
    """``host.ui`` - the :class:`~crude.ui.context.UIContext` for this program."""
    return ctx.subsystem("ui", "UI")


def _fail(ctx, exc):
    """A :class:`~crude.ui.base.UIError` in the bindings' own voice."""
    raise ctx.fail(f"{ctx.name}: {exc}")


def _handle(widget: Widget) -> HostHandle:
    """Wrap a widget so a script can hold it, name it in a report by its path."""
    return HostHandle(widget, kind=WIDGET_KIND, label=widget.path())


def _as_python(ctx, arg_name: str, expected, phrase: str):
    """An argument as the host object under it, however the script sent it.

    Two shapes are the same widget: the handle a binding handed back, and the
    object a *handler argument* became on the way in - :meth:`Program.invoke`
    wraps every host object with ``python_to_value``, which spells an unknown
    object as a plain ``"handle"``. Accepting only the first would make a
    ``ON_CLICK`` that passes its own widget to a helper function fail for a reason
    the script has no way to see.
    """
    value = ctx.get(arg_name)
    if isinstance(value, expected):
        return value
    if isinstance(value, HostHandle) and isinstance(value.target, expected):
        return value.target
    raise ctx.fail(
        f"{ctx.name}: argument '{arg_name}' must be {phrase}, "
        f"got {type_name(value)}"
    )


def _as_widget(ctx, arg_name: str) -> Widget:
    """A widget handle argument as the :class:`~crude.ui.widget.Widget` under it."""
    return _as_python(ctx, arg_name, Widget, "a widget handle")


def _as_canvas(ctx, arg_name: str) -> Canvas:
    """The ``p`` a ``P_*`` primitive was handed, as the :class:`Canvas` under it.

    Reached only from inside an ``ON_PAINT``, so a ``P_RECT`` in ordinary script
    code fails as "must be a paint handle" rather than as an attribute error on
    whatever ``NULL`` an omitted argument turned into.
    """
    return _as_python(ctx, arg_name, Canvas, "a paint handle (the 'p' an "
                                            "ON_PAINT was handed)")


def _kind_word(ctx, arg_name: str) -> str:
    """A kind name, or the name of the kind a widget handle is standing for.

    Unwrapped through :func:`value_to_python` rather than through
    :func:`_as_python` because a kind is named with a *language* string, which is
    not a Python one, and a widget is accepted so a handler can define a prop on
    the kind it was handed without knowing its name.
    """
    value = ctx.get(arg_name)
    held = value_to_python(value)
    if isinstance(held, Widget):
        return held.kind.name
    if isinstance(held, str):
        return held
    raise ctx.fail(f"{ctx.name}: argument '{arg_name}' must be a kind name or a "
                   f"widget, got {type_name(value)}")


def _optional_value(ctx, arg_name, default=None):
    """A plain Python value for an optional argument, or ``default`` if omitted."""
    if not ctx.has(arg_name):
        return default
    return value_to_python(ctx.get(arg_name))


def _opt_number(ctx, arg_name, default=None):
    """An optional number, validated the way a required one would be.

    Separate from :func:`_optional_value` because a painter's geometry has to be a
    number: ``P_RECT(p, 0, 0, "wide", 10)`` is a script that should be told, not a
    ``TypeError`` from halfway through a draw pass.
    """
    return ctx.as_number(arg_name) if ctx.has(arg_name) else default


# -- create -------------------------------------------------------------


def _construct(ctx, kind_name: str, arg_names):
    """Build one widget from its kind's positional arguments and add it to the tree.

    Every catalog constructor funnels here, so a kind is added in exactly one
    place: the table (:attr:`Kind.args`) decides both the argument names and the
    props they land on, and an omitted argument simply is not written, leaving
    the row at the default the same table declares. The three slots the table
    cannot name - a toggle's ``on``, an icon's ``size``, a group's ``parent`` -
    are translated here and nowhere else, which is what keeps the plan's
    constructor-equals-``SET`` rule true for every kind that needs no translation
    at all.
    """
    window = _window(ctx)
    values = {}
    box = None
    holder = None
    for name in arg_names:
        if not ctx.has(name):
            continue
        given = value_to_python(ctx.get(name))
        if name == "on":
            # a switch's "on" as the language means it: any non-zero number,
            # the words a bool prop takes, and nothing else -
            # ``TOGGLE("Run", "maybe")`` is a script that did not decide, and
            # gets told so
            if isinstance(given, str):
                said = given.strip().lower()
                if said not in _ON_WORDS and said not in _OFF_WORDS:
                    raise ctx.fail(
                        f"{ctx.name}: argument 'on' must be a number or "
                        f"{' or '.join(sorted(_ON_WORDS + _OFF_WORDS))}, "
                        f"got '{given}'"
                    )
                values["value"] = 1.0 if said in _ON_WORDS else 0.0
            elif isinstance(given, bool):
                values["value"] = 1.0 if given else 0.0
            elif isinstance(given, (int, float)):
                values["value"] = 1.0 if given else 0.0
            else:
                raise ctx.fail(
                    f"{ctx.name}: argument 'on' must be a number or a word, "
                    f"got {type_name(ctx.get(name))}"
                )
            continue
        if name == "size":
            if isinstance(given, str):
                try:
                    given = float(given.strip())
                except ValueError:
                    raise ctx.fail(
                        f"{ctx.name}: argument 'size' must be a number of "
                        f"logical pixels, got '{given}'"
                    ) from None
            if not isinstance(given, (int, float)) or isinstance(given, bool):
                raise ctx.fail(
                    f"{ctx.name}: argument 'size' must be a number of logical "
                    f"pixels, got {type_name(ctx.get(name))}"
                )
            side = max(0.0, float(given))
            box = (0.0, 0.0, side, side)
            continue
        if name == "parent":
            # the widget this one is built inside, which is a place in the tree
            # rather than a row on it: ``NODE_ADD`` is the same call on a widget
            # that has already been made, and the two share the refusal below
            holder = _as_python(ctx, "parent", Widget, "a widget handle")
            continue
        values[name] = given
    name = ""
    for word in arg_names:
        given = values.get(word)
        if isinstance(given, str):
            name = given
            break
    try:
        held = Widget(kind_name, values, name=name)
        if box is not None:
            held.place(*box)
        if holder is not None:
            # into the panel the script named, and stamped with the scene it was
            # said in, which is what :meth:`~crude.ui.context.UIContext.add` would
            # have done: a section built into a scene must leave the window with it
            holder.add(held)
            window.workspaces.claim(held)
        else:
            window.add(held)
    except UIError as exc:
        _fail(ctx, exc)
    return _handle(held)


@binding("WIDGET", ["kind", "name"], area=AREA, optional=1,
         doc="Create a widget of ``kind``; the generic every catalog word wraps.")
def widget_ctor(ctx):
    """``WIDGET("slider")``: the generic constructor, the only real one."""
    window = _window(ctx)
    kind_name = ctx.as_string("kind")
    name = ctx.as_string("name") if ctx.has("name") else ""
    try:
        held = Widget(kind_name, {}, name=name)
        window.add(held)
    except UIError as exc:
        _fail(ctx, exc)
    return _handle(held)


def _register_constructor(binding_name: str, kind_name: str,
                          arg_names, optional: int) -> None:
    """Declare one catalog word whose arguments are that kind's own ``args``."""

    def run(ctx, _kind=kind_name, _args=arg_names):
        return _construct(ctx, _kind, _args)

    run.__name__ = f"{binding_name.lower()}_ctor"
    run.__doc__ = (f"{binding_name}({', '.join(arg_names)}): a '{kind_name}' - "
                   f"WIDGET(\"{kind_name}\") plus a SET per argument given.")
    # declared through the registry's own record rather than the decorator, so
    # one loop can state the catalog's words whose argument lists come from the
    # table
    REGISTRY[binding_name] = Binding(
        name=binding_name, arg_names=arg_names, func=run,
        area=AREA, optional=optional, doc=run.__doc__,
    )


for _binding_name, (_kind_name, _arg_names, _optional) in CONSTRUCTORS.items():
    _register_constructor(_binding_name, _kind_name, _arg_names, _optional)


# -- create: kinds a script declares for itself --------------------------


@binding("WIDGET_DEFINE", ["kind", "base", "pd_object", "hint"], area=AREA,
         optional=3,
         doc="Declare a widget kind; with a base it is a variant of one.")
def widget_define(ctx):
    """``WIDGET_DEFINE("bpm_slider", "slider")``: a kind this program owns.

    With a base the new kind *is* the base - its whole prop table, its pointer
    behaviour, its Pd twin - with the defaults free to be restated underneath it,
    and it needs no painter. Without one it is a widget from scratch, which is
    drawn by ``ON_PAINT`` and, until one is registered, falls back on the plate its
    ``role`` names: nothing raises, because the painter may be on the next line.

    A name that is already a catalog kind is refused. The plan's rule is that a
    project defines a kind *beside* ``slider``, never over it - and a kind lives
    for this run only, so ``WIDGET_KINDS`` is the list of what was added.
    """
    name = _kind_word(ctx, "kind")
    try:
        held = define(name,
                      base=_optional_value(ctx, "base", "") or "",
                      pd_object=_optional_value(ctx, "pd_object", "") or "",
                      hint=_optional_value(ctx, "hint", "") or "")
    except UIError as exc:
        _fail(ctx, exc)
    return String(held.name)


@binding("PROP_DEFINE", ["kind", "name", "type", "default", "min", "max",
                         "pd_name", "hint"], area=AREA, optional=4,
         doc="Add or restate one prop on a kind this program defined.")
def prop_define(ctx):
    """``PROP_DEFINE("stepper", "min", "number", 0)``: one row of a script kind.

    Restating an inherited row keeps whatever this call leaves out, so the plan's
    three lines that give a variant a ``min``, a ``max`` and ``ticks`` do not
    restate the hints or the Pd names - and a variant that *had* to would have to
    spell the whole table again to change one number.

    Only a kind this program defined takes part: adding a row to ``slider`` would
    change every slider there is from a project file, at run time.
    """
    kind = _kind_word(ctx, "kind")
    name = ctx.as_string("name")
    held_type = ctx.as_string("type")
    if held_type not in TYPES:
        raise ctx.fail(f"{ctx.name}: '{held_type}' is not a prop type (a row is "
                       f"one of: {', '.join(sorted(TYPES))})")
    try:
        held = define_prop(kind, name, held_type,
                           value_to_python(ctx.get("default")),
                           _optional_value(ctx, "min"),
                           _optional_value(ctx, "max"),
                           _optional_value(ctx, "pd_name"),
                           _optional_value(ctx, "hint"))
    except UIError as exc:
        _fail(ctx, exc)
    # the row may have just become the one a cached face was drawn from
    _repaint(ctx, kind)
    return String(held.name)


def _repaint(ctx, owner) -> None:
    """Redraw every widget of one kind, because its table just changed.

    A prop is not a picture until a painter reads it, but a restated *default*
    moves the value under a widget that is already on screen, and the plan's cache
    must not be allowed to keep a face drawn from a table that no longer exists.
    """
    window = _window(ctx)
    held = getattr(window, "paints", None)
    if held is None:
        return
    for every in window.widgets():
        if every.kind.name == getattr(owner, "name", owner):
            held.invalidate(every)


# -- configure: this module owns SET/GET for widget handles --------------


def _widget_get(ctx, handle, prop):
    """One readable prop, wrapped for the script; a bad name says which kind.

    The two :data:`LAYOUT_WORDS` are answered first, from the placement pass, which
    is the only place a widget's glues and policies are kept - a script asks
    ``GET(w, "place")`` the same way it asks ``GET(w, "value")`` and gets the same
    kind of answer back, a list of what the widget was told.
    """
    held = handle.target
    said = str(prop).strip().lower()
    if said in LAYOUT_WORDS:
        return _layout_read(held, said)
    try:
        return python_to_value(held.get(prop))
    except UIError as exc:
        raise ctx.fail(f"GET: {exc}")


def _layout_read(held, word: str):
    """``GET(w,"place")`` / ``GET(w,"pack")``, read off the pass that owns them."""
    try:
        if word == "place":
            return List([String(str(rule)) for rule in layout_anchors(held)])
        return List([String(name) for name in layout_pack_of(held)])
    except UIError as exc:
        raise ctx.fail(f"GET: {exc}")


def _widget_set(ctx, handle, prop, value):
    """Write through the kind's table and answer at the settled value.

    Returning what the widget ended up holding is what keeps a clamp honest: a
    ``SET(w, "value", 500)`` on a slider that tops out at 300 hands back 300
    rather than the number the script asked for and the widget refuses.

    The two :data:`LAYOUT_WORDS` are refused by name and sent to the verb that
    writes them, because they are not rows: a gluing is a rule the pass keeps, and
    the table has no word for one, so a ``SET`` here would be a script editing a
    layout it cannot see.
    """
    said = str(prop).strip().lower()
    if said == "place":
        raise ctx.fail("SET: 'place' is what a widget is glued to, and only "
                       "ANCHOR() and UNANCHOR() write gluing")
    if said == "pack":
        raise ctx.fail("SET: 'pack' is what a widget does when its container "
                       "moves, and only PACK() writes that")
    try:
        handle.target.set(prop, value_to_python(value))
    except UIError as exc:
        raise ctx.fail(f"SET: {exc}")
    return python_to_value(handle.target.get(prop))


own_props(WIDGET_KIND, read=_widget_get, write=_widget_set,
          claims=lambda held: isinstance(held, Widget))


# -- place --------------------------------------------------------------


@binding("PLACE", ["widget", "x", "y", "width", "height"], area=AREA, optional=2,
         doc="Put a widget at x, y (width, height) in logical pixels; omitted stay.")
def place(ctx):
    """``PLACE(w, x, y, width, height)``: window-relative, or parent-relative."""
    held = _as_widget(ctx, "widget")
    x = ctx.as_number("x")
    y = ctx.as_number("y")
    width = _optional_value(ctx, "width")
    height = _optional_value(ctx, "height")
    moved = held.place(x, y, width, height)
    return Number(1 if moved else 0)


@binding("NODE_ADD", ["parent", "child"], area=AREA,
         doc="Put a widget inside a PANEL; its box becomes parent-relative.")
def node_add(ctx):
    """``NODE_ADD(box, w)``: reparent into a container; the child clips to it."""
    parent = _as_widget(ctx, "parent")
    child = _as_widget(ctx, "child")
    try:
        parent.add(child)
    except UIError as exc:
        _fail(ctx, exc)
    return _handle(child)


# -- place: the rest of the placement pass ------------------------------


def _option_map(ctx, allowed) -> dict:
    """The ``options`` argument as the map :func:`crude.ui.layout.arrange` reads.

    An alternating list of a name and a value, refused when the last name has no
    value and refused *by name* when it is not a word this verb knows. The refusal
    is the point: ``arrange`` drops a key it does not recognise, and a layout option
    that was silently thrown away is a panel that came out different from the one the
    script asked for, which is the kind of mistake nobody finds by looking.
    """
    given = values_from(ctx, "options")
    if len(given) % 2:
        raise ctx.fail(
            f"{ctx.name}: 'options' is a list of name/value pairs and this one "
            f"has {len(given)} item(s), which leaves one without a name")
    held = {}
    for index in range(0, len(given), 2):
        key = given[index]
        if not isinstance(key, str):
            raise ctx.fail(f"{ctx.name}: an 'options' name must be a string, and "
                           f"position {index} holds {key!r}")
        said = key.strip().lower()
        if said not in allowed:
            raise ctx.fail(f"{ctx.name}: '{said}' is not an option it knows "
                           f"(known: {', '.join(allowed)})")
        held[said] = given[index + 1]
    return held


def _column_count(ctx) -> int:
    """The ``columns`` a ``GRID`` was given, as whole cells or as a named refusal.

    :func:`crude.ui.layout.stack` truncates, and a script that wrote ``GRID(box, 2.5)``
    asked for a division somebody else decided, which is why
    :class:`~crude.ui.grid.GridSpec` refuses a fractional count instead of rounding it
    into a cell the pass does not measure. ``0`` stays legal and means "as many as make
    the grid square", because that is what the verb's own docstring says it means.
    """
    if not ctx.has("columns"):
        return 0
    said = ctx.as_number("columns")
    if said != int(said):
        raise ctx.fail(f"{ctx.name}: 'columns' counts cells, so it has to be a "
                       f"whole number, not {said:g}")
    if said < 0:
        raise ctx.fail(f"{ctx.name}: 'columns' of {said:g} is not a number of "
                       "cells; 0 means as many as fit the children there are")
    return int(said)


def _arranged(ctx, direction: str) -> HostHandle:
    """One arrange call: this panel, its gap, and whatever the option list said.

    The same helper behind ``HSTACK``, ``VSTACK`` and ``GRID``, which differ only in
    the direction they hand the pass and in whether a column count is read: the last
    word written wins, so an option named in the list beats the positional argument
    that answered the same question first.
    """
    panel = _as_widget(ctx, "panel")
    spec = {"direction": direction}
    if direction == "grid":
        spec["columns"] = _column_count(ctx)
    if ctx.has("gap"):
        spec["gap"] = _optional_value(ctx, "gap")
    allowed = GRID_OPTIONS if direction == "grid" else STACK_OPTIONS
    options = _option_map(ctx, allowed)
    dockable = options.pop("dockable", None)
    spec.update(options)
    try:
        layout_arrange(panel, spec)
        if dockable is not None:
            panel.set("dockable", dockable)
    except UIError as exc:
        _fail(ctx, exc)
    return _handle(panel)


@binding("ANCHOR", ["widget", "edge", "target", "target_edge", "offset"],
         area=AREA, optional=3,
         doc="Glue one edge of a widget to one edge of another, or of its panel.")
def anchor(ctx):
    """``ANCHOR(w, "right", bar, "right", -16)``: the glue owns that axis from now on.

    Two arguments are the whole engine's own rule: with no ``target`` the widget is
    glued to the panel it lives in (and ``"parent"`` says so in words), and with no
    ``target_edge`` the same edge is used on both sides. Gluing ``left`` and
    ``right`` is a stretch rather than a contradiction, which is how a bar pinned
    to both window edges survives a resize; an anchor is applied after every pack
    and stack, so the axis it names stops being theirs to decide.

    Answers the glue as the sentence ``GET(w, "place")`` reads it back.
    """
    held = _as_widget(ctx, "widget")
    edge = ctx.as_string("edge")
    target = _optional_value(ctx, "target")
    target_edge = _optional_value(ctx, "target_edge")
    offset = _optional_value(ctx, "offset", 0.0)
    try:
        rule = layout_anchor(held, edge, target, target_edge, offset)
    except UIError as exc:
        _fail(ctx, exc)
    return String(str(rule))


@binding("UNANCHOR", ["widget", "edge"], area=AREA, optional=1,
         doc="Take one glue away, or every one; how many left.")
def unanchor(ctx):
    """``UNANCHOR(w)`` / ``UNANCHOR(w, "bottom")``: the axis goes back to pack.

    The widget keeps the box the glue last wrote rather than being placed again by
    hand, which is the same honesty the pass itself has: taking a rule away is not
    the same act as never having had one.
    """
    held = _as_widget(ctx, "widget")
    edge = ctx.as_string("edge") if ctx.has("edge") else None
    try:
        gone = layout_unanchor(held, edge)
    except UIError as exc:
        _fail(ctx, exc)
    return Number(float(gone))


@binding("PACK", ["widget", "policy_x", "policy_y"], area=AREA, optional=1,
         doc="What a widget does when its container moves: fixed, grow or fill.")
def pack(ctx):
    """``PACK(w, "grow", "fixed")``: one word for both axes, or one for each.

    ``fixed`` keeps the size the script gave it, ``grow`` keeps the *fraction* of the
    container that size was, and ``fill`` takes the space nothing else used. An axis
    a script has glued is not a pack answer any more, whatever is stored here.

    Answers the two policies now held, which is what ``GET(w, "pack")`` reads back.
    """
    held = _as_widget(ctx, "widget")
    policy = ctx.as_string("policy_x")
    other = _optional_value(ctx, "policy_y")
    try:
        found = layout_pack(held, policy, other)
    except UIError as exc:
        _fail(ctx, exc)
    return List([String(found[0]), String(found[1])])


@binding("PAD", ["widget", "value"], area=AREA, optional=1,
         doc="A container's own padding, in pixels or a metric token; none gives back.")
def pad(ctx):
    """``PAD(box, 16)`` / ``PAD(box, "lg")`` / ``PAD(box)``: the pixels it landed on.

    A number of logical pixels overrides the skin's padding for this widget, which
    moves where children are placed, what they are clipped to, and where a hit on
    the inner edge stops counting as the panel. A token name is measured against the
    skin this tree was last resolved with, so the first layout has to have run -
    which is said rather than guessed at. Leaving the value out gives the skin's own
    answer back and returns ``0``.
    """
    held = _as_widget(ctx, "widget")
    value = _optional_value(ctx, "value")
    try:
        found = layout_pad(held, value)
    except UIError as exc:
        _fail(ctx, exc)
    return Number(found)


@binding("HSTACK", ["panel", "gap", "options"], area=AREA, optional=2,
         doc="Arrange a panel's children left to right; gap, then pad/align options.")
def hstack(ctx):
    """``HSTACK(box, 8)``: a row, laid out in the order the children were added.

    Children are arranged in z-order, so an arrangement and a stack are the same
    list read two ways - the one a script can reorder by adding, removing or raising
    a child. The ``gap`` is pixels or a metric token, and the option list holds what
    is left of a stack's words, "pad" and "align", which are the same two
    :func:`GRID` takes.
    """
    return _arranged(ctx, "horizontal")


@binding("VSTACK", ["panel", "gap", "options"], area=AREA, optional=2,
         doc="Arrange a panel's children top to bottom; gap, then pad/align options.")
def vstack(ctx):
    """``VSTACK(box, "pad.sm")``: a column, and the same rules as ``HSTACK``."""
    return _arranged(ctx, "vertical")


@binding("GRID", ["panel", "columns", "gap", "options"], area=AREA, optional=3,
         doc="Arrange a panel's children in cells; 0 columns is as square as they.")
def grid(ctx):
    """``GRID(box, 3, "pad.md")``: equal cells, left to right then top to bottom.

    ``columns`` of ``0`` means as square as the child count allows, and a cell is
    the panel's length divided by the columns with the gaps taken out, so nine icons
    in a 200 px panel are nine equal cells rather than nine widths. A child whose
    policy is ``fill`` takes its whole cell; anything else sits inside it at the
    ``align`` option, which a grid defaults to ``"center"``.

    ``"dockable", 1`` in the option list writes the panel's own row, which is what
    makes a cell somewhere a group can be dropped into in stage 4: the cells a
    ``GRID`` arranges and the cells a drop reads are measured by the same
    :class:`~crude.ui.grid.GridSpec`, so the two halves cannot disagree.
    """
    return _arranged(ctx, "grid")


@binding("UNSTACK", ["panel"], area=AREA,
         doc="Stop arranging a panel; its children stay where the pass left them.")
def unstack(ctx):
    """``UNSTACK(box)``: 1 if a stack was taken away, 0 if there was none.

    The children keep the boxes the last pass wrote, because a script that undoes an
    arrangement has not said where they should be instead, and inventing a place
    would be the layout layer overruling the script.
    """
    held = _as_widget(ctx, "panel")
    try:
        gone = layout_unstack(held)
    except UIError as exc:
        _fail(ctx, exc)
    return Number(1 if gone else 0)


# -- groups: a titled section of a page ---------------------------------


def _as_group(ctx, arg_name: str) -> Widget:
    """A widget handle that is a group, refused by the kind it is instead.

    The question is the inheritance chain's rather than the kind name's, so a
    script's ``WIDGET_DEFINE("section", "group")`` takes every verb below exactly
    as the catalog kind does - and a plain panel is refused by the name it has,
    rather than failing somewhere inside a fold it was never given.
    """
    held = _as_widget(ctx, arg_name)
    if not is_group(held):
        raise ctx.fail(f"{ctx.name}: argument '{arg_name}' must be a group "
                       f"handle, and '{held.path()}' is a '{held.kind.name}'")
    return held


def _count(ctx, arg_name: str) -> int:
    """An argument that counts things: a whole number, or a refusal that says so.

    The same rule :func:`_column_count` states for a grid: :meth:`int` would
    truncate ``2.5`` into a cell somebody else decided, and a script that is told
    its group landed in cell 2 when it asked for 2.5 has been answered for it.
    """
    said = ctx.as_number(arg_name)
    if said != int(said):
        raise ctx.fail(f"{ctx.name}: '{arg_name}' counts things, so it has to be "
                       f"a whole number, not {said:g}")
    return int(said)


@binding("GROUP_FOLD", ["group", "folded"], area=AREA, optional=1,
         doc="Close a group to its header, open it, or leave it out to switch.")
def group_fold_ctor(ctx):
    """``GROUP_FOLD(carrier, 1)``: fold it, ``0`` opens it, nothing means the other.

    The prop is written and the box is left to the next pass, which is what makes
    this, ``SET(g, "folded", 1)`` and a hand on the chevron one gesture with three
    spellings: how tall a fold leaves the group is measured from the rows its body
    holds, so a verb that resized the box itself would be a second answer to the one
    question the layout pass is already asked to answer.

    Answers the state the group is in now, so a bare ``GROUP_FOLD(g)`` can be read
    back rather than guessed at.
    """
    held = _as_group(ctx, "group")
    wanted = ctx.as_bool("folded") if ctx.has("folded") else None
    try:
        group_fold(held, wanted)
    except UIError as exc:
        _fail(ctx, exc)
    return Number(1.0 if bool(held.get("folded")) else 0.0)


@binding("GROUP_MOVE", ["group", "position"], area=AREA,
         doc="Re-order a group among the sections it is drawn with; 0 is first.")
def group_move(ctx):
    """``GROUP_MOVE(filter, 0)``: the list an arrangement reads is this list.

    The order among its siblings *is* the order a ``VSTACK``, an ``HSTACK`` and a
    ``GRID`` lay children out in, so moving in the list is the re-arrange: the next
    pass draws the boxes in the new order and nobody has to be told a row moved. A
    position past either end is clamped to the end it is nearer, which is the same
    answer a hand that drags past the last row gets.

    Answers the position the group is at now, which is the clamped one.
    """
    held = _as_group(ctx, "group")
    wanted = _count(ctx, "position")
    try:
        group_move_to(held, wanted)
    except UIError as exc:
        _fail(ctx, exc)
    return Number(float(group_order(held)))


def _moved_by(ctx, steps: int) -> Number:
    """``GROUP_UP`` and ``GROUP_DOWN``: :func:`GROUP_MOVE` at ``±1``.

    One helper because the two are the same call with the sign written the other
    way round, and an arithmetic kept in two places is two chances to be wrong.
    """
    held = _as_group(ctx, "group")
    try:
        group_move_by(held, steps)
    except UIError as exc:
        _fail(ctx, exc)
    return Number(float(group_order(held)))


@binding("GROUP_UP", ["group"], area=AREA,
         doc="One place towards the front of the sections it is drawn with.")
def group_up(ctx):
    """``GROUP_UP(modulator)``: the order it landed on, not the one it asked for.

    At the front of the list there is no place further to go, and a verb that
    answered its own arithmetic would report a move that did not happen.
    """
    return _moved_by(ctx, -1)


@binding("GROUP_DOWN", ["group"], area=AREA,
         doc="One place towards the back of the sections it is drawn with.")
def group_down(ctx):
    """``GROUP_DOWN(modulator)``: ``GROUP_UP`` the other way, same answer."""
    return _moved_by(ctx, 1)


@binding("GROUP_DOCK", ["group", "cell", "panel"], area=AREA, optional=1,
         doc="Put a group in one numbered cell of a dockable grid.")
def group_dock_ctor(ctx):
    """``GROUP_DOCK(carrier, 2)``: the cell it is told is the cell it is drawn in.

    The panel defaults to the one the group already lives in, which is the usual
    case - a section moved between two cells of the same grid. The cell is the
    number ``GRID`` counted from 0, and the group, the panel and the cell must all
    agree about it: a group is picked up only when it says it is ``dockable``, a
    panel has cells only once a ``GRID`` with ``"dockable", 1`` has measured them,
    and a cell is free only if nothing is drawn in it.

    Each of those is refused by name, because "why did my group not move" is a
    question about three separate facts and one error that could mean any of them
    is a bug report rather than a fix. A pointer gets the situation answered the
    other way: a drag released over a taken cell moves nothing at all, because the
    reply a hand can read is the thing not moving.

    Answers the cell now held, which is the one ``GET(g, "cell")`` reads back.
    """
    held = _as_group(ctx, "group")
    cell = _count(ctx, "cell")
    panel = (_as_python(ctx, "panel", Widget, "a widget handle")
             if ctx.has("panel") else None)
    try:
        group_dock(held, cell, panel)
    except UIError as exc:
        _fail(ctx, exc)
    return Number(float(held.get("cell")))


@binding("GROUP_UNDOCK", ["group"], area=AREA,
         doc="Give up the cell; the group stays in its panel and takes a free one.")
def group_undock_ctor(ctx):
    """``GROUP_UNDOCK(carrier)``: only the claim is dropped, never the parentage.

    Where a group *lives* and which cell it is drawn in are two facts, and a dock
    wrote only the second. Giving it up leaves the group in the panel and puts it
    after its siblings, so it takes the next free cell the arrangement has - which
    is the same rule a new child follows and the reason a ``GROUPS`` list keeps
    reading in draw order.

    Answers the cell now held: ``-1`` means no cell of its own, until the next pass
    has arranged the panel it is in.
    """
    held = _as_group(ctx, "group")
    try:
        group_undock(held)
    except UIError as exc:
        _fail(ctx, exc)
    return Number(float(held.get("cell")))


@binding("GROUPS", ["panel"], area=AREA,
         doc="The groups a panel holds, in the order they are drawn.")
def groups_ctor(ctx):
    """``GROUPS(box)``: the array, looked for where an arrangement would put them.

    A group's own children live inside its body, so "the groups this panel holds"
    is answered by looking in the panel that holds its children - which is what
    ``VSTACK`` and ``GRID`` arrange, and what lets a ``View`` menu list the sections
    of a page without knowing whether the page is a plain panel or another group.
    """
    held = _as_widget(ctx, "panel")
    return List([_handle(kid) for kid in groups_of(held)])


# -- wire: links --------------------------------------------------------


@binding("LINK_VALUE", ["source", "target", "two_way"], area=AREA, optional=1,
         doc="Keep two widgets' value equal; two-way unless told otherwise.")
def link_value(ctx):
    """``LINK_VALUE(bpm, field)``: mirror one widget's ``value`` onto another."""
    window = _window(ctx)
    source = _as_widget(ctx, "source")
    target = _as_widget(ctx, "target")
    two_way = ctx.as_bool("two_way") if ctx.has("two_way") else True
    try:
        window.handlers.link(source, "value", target, "value", two_way)
    except UIError as exc:
        _fail(ctx, exc)
    return Number(1 if two_way else 0)


@binding("LINK_STATE", ["source", "prop", "target", "target_prop", "two_way"],
         area=AREA, optional=2,
         doc="Mirror any prop from source onto target; the general LINK_VALUE.")
def link_state(ctx):
    """``LINK_STATE(a, "visible", b)``: mirror any (settable) row, not just value."""
    window = _window(ctx)
    source = _as_widget(ctx, "source")
    prop = ctx.as_string("prop")
    target = _as_widget(ctx, "target")
    target_prop = _optional_value(ctx, "target_prop")
    two_way = ctx.as_bool("two_way") if ctx.has("two_way") else True
    try:
        window.handlers.link(source, prop, target, target_prop, two_way)
    except UIError as exc:
        _fail(ctx, exc)
    return Number(1 if two_way else 0)


@binding("UNLINK", ["widget", "prop"], area=AREA, optional=2,
         doc="Undo links: on one widget, on one row, or every one of them.")
def unlink(ctx):
    """``UNLINK(w)`` / ``UNLINK(w, "value")`` / ``UNLINK()``: how many went."""
    window = _window(ctx)
    held = _as_widget(ctx, "widget") if ctx.has("widget") else None
    prop = ctx.as_string("prop") if ctx.has("prop") else None
    return Number(window.handlers.unlink(held, prop))


# -- wire: handlers -----------------------------------------------------


@binding("ON_FRAME", ["func"], area=AREA,
         doc="Run a function once per tick with the frame's seconds.")
def on_frame(ctx):
    """``ON_FRAME(on_frame)``: one handler per tick, at the plan's place in it."""
    window = _window(ctx)
    func = ctx.as_function("func")
    try:
        window.handlers.on_frame(func)
    except UIError as exc:
        _fail(ctx, exc)
    return Number.null


@binding("OFF_FRAME", ["func"], area=AREA, optional=1,
         doc="Remove frame handlers: just one function, or all of them.")
def off_frame(ctx):
    """``OFF_FRAME(fn)`` / ``OFF_FRAME()``: how many frame handlers went."""
    window = _window(ctx)
    func = ctx.as_function("func") if ctx.has("func") else None
    return Number(window.handlers.off_frame(func))


def _make_on(event: str):
    """Register one ``ON_<EVENT>`` for a widget event the tree fires."""
    binding_name = f"ON_{event.upper()}"

    def run(ctx):
        window = _window(ctx)
        held = _as_widget(ctx, "widget")
        func = ctx.as_function("func")
        try:
            window.handlers.add(held, event, func)
        except UIError as exc:
            _fail(ctx, exc)
        return Number.null

    run.__name__ = binding_name.lower()
    run.__doc__ = f"{binding_name}(w, fn): call fn when a widget gets '{event}'."
    REGISTRY[binding_name] = Binding(
        name=binding_name, arg_names=("widget", "func"), func=run,
        area=AREA, optional=0, doc=run.__doc__,
    )


for _event in WIDGET_EVENTS:
    _make_on(_event)


def _make_ask(event: str, doc: str) -> None:
    """Declare one ``ON_*`` whose *answer* is what the tree draws or fires with.

    Separate from :func:`_make_on` on purpose. A told event is fire-and-forget; an
    asked one retires the face that was cached under it, because the painter that
    has just been registered, changed or taken away is the reason the picture is
    about to be different. Both words still name a real entry in
    :data:`~crude.ui.handlers.EVENTS`, so ``OFF(w, "paint")`` works from here on.
    """
    binding_name = f"ON_{event.upper()}"

    def run(ctx, _event=event):
        window = _window(ctx)
        held = _as_widget(ctx, "widget")
        func = ctx.as_function("func")
        try:
            window.handlers.add(held, _event, func)
            window.paints.invalidate(held)
        except UIError as exc:
            _fail(ctx, exc)
        return Number.null

    run.__name__ = binding_name.lower()
    run.__doc__ = doc
    REGISTRY[binding_name] = Binding(
        name=binding_name, arg_names=("widget", "func"), func=run,
        area=AREA, optional=0, doc=doc,
    )


_make_ask(PAINT, "ON_PAINT(w, fn): draw a widget's own face into the canvas fn "
                 "is handed; recorded once and replayed until it changes.")
_make_ask(HIT, "ON_HIT(w, fn): say which part of a widget takes a click - a word "
               "that ON_CLICK is handed back, or a box that narrows it.")


@binding("PAINT_REQUEST", ["widget"], area=AREA,
         doc="Redraw a widget next frame, whatever its props are doing.")
def paint_request(ctx):
    """``PAINT_REQUEST(w)``: drop one widget's cached face.

    The cache keys on the props, the box, the skin and the device ratio, which
    covers every picture a widget's own table can decide. A painter that reads
    something *outside* it - a value held in a script variable, a level that comes
    from the audio - has to say so here, because an animated ``SET`` invalidates on
    its own and a change the widget never hears about does not.
    """
    window = _window(ctx)
    held = _as_widget(ctx, "widget")
    return Number(window.paints.invalidate(held))


@binding("OFF", ["widget", "event", "func"], area=AREA, optional=3,
         doc="Undo handler registrations; widen by leaving arguments out.")
def off(ctx):
    """``OFF(w)`` / ``OFF(w, "click")`` / ``OFF(w, "click", fn)``: how many went."""
    window = _window(ctx)
    held = _as_widget(ctx, "widget") if ctx.has("widget") else None
    event = ctx.as_string("event") if ctx.has("event") else None
    func = ctx.as_function("func") if ctx.has("func") else None
    try:
        gone = window.handlers.remove(held, event, func)
    except UIError as exc:
        _fail(ctx, exc)
    return Number(gone)


@binding("MOD_HELD", ["word"], area=AREA,
         doc="Is this modifier key down now? shift, control, alt or super.")
def mod_held(ctx):
    """``MOD_HELD("shift")``: the keyboard state a handler needs and is not given.

    A press carries its modifiers to the behaviour that moves the value, but a
    script that decides a thing a frame later - an ``ON_CLICK`` that adds a track to
    the ones already selected on a shift-click, an ``ON_FRAME`` that paints a
    different face while control is down - has no argument holding that word. The
    router's live modifier state is the answer, and this is the only door to it: a
    project that wrote ``0x0001`` to test for a shift-click would be a script that
    breaks the day the mask moves.

    An unknown word is refused rather than answered ``0``, because ``0`` is a real
    answer - *not held* - and a typo must not be allowed to look like one. Having no
    window at all is not an error either: a headless run still has a router, and a
    program never given a UI has nothing to ask, so it is answered ``0`` and the
    script goes on.
    """
    said = ctx.as_string("word").strip().lower()
    mask = MODS.get(said)
    if mask is None:
        raise ctx.fail(f"{ctx.name}: '{said}' is not a modifier (it is one of: "
                       f"{', '.join(sorted(MODS))})")
    window = getattr(ctx.host, "ui", None)
    mods = int(getattr(getattr(window, "router", None), "mods", 0) or 0)
    return Number(1 if mods & mask else 0)


@binding("SENDER", [], area=AREA,
         doc="The widget the handler now running was registered on.")
def sender(ctx):
    """``SENDER()``: which of a hundred widgets made this call.

    An event hands its handler a value, a prop name and the event's own numbers, and
    never the widget - which is fine for one control and impossible for a page built
    by one function. A recorder that makes four tracks with ``make_track(i)``
    registers the same ``FUNC`` on four knobs, and the handler has to know which row
    turned: ``SET(AT(KNOBS, track_of(SENDER())), ...)`` is the whole answer, and the
    alternative is four copies of the row.

    Outside a handler - in the script's own top-level code, or in a function that
    code called - there is no sender, and this answers ``NULL`` rather than the last
    widget that happened to move. A handler registered on the frame has no widget
    either, for the same reason: ``ON_FRAME`` belongs to the tick and not to a box.
    A run with no UI at all answers ``NULL`` too, the way ``MOD_HELD`` answers ``0``
    when there is no keyboard to ask - ``NULL`` is the truth, and an error would be
    a script that cannot be run headless.
    """
    window = getattr(ctx.host, "ui", None)
    handlers = getattr(window, "handlers", None)
    held = handlers.sender() if handlers is not None else None
    if held is None:
        return Number.null
    return _handle(held)


# -- paint: the primitives an ON_PAINT draws with -----------------------

# what every primitive returns. A painter is a list of statements rather than an
# expression chain, and ``NULL`` is what this language says a statement with
# nothing to hand back answered; a script that tried to use a painter's result as
# a value would be building a face out of the wrong thing.


@binding("WIDTH", ["what"], area=AREA,
         doc="The box a widget - or the painter's own canvas - is that wide.")
def width_of(ctx):
    """``WIDTH(w)``: the current width, so a painter scales without a constant."""
    return Number(_measure(ctx, "what", "width"))


@binding("HEIGHT", ["what"], area=AREA,
         doc="The box a widget - or the painter's own canvas - is that high.")
def height_of(ctx):
    """``HEIGHT(w)``: the current height, the other half of a responsive face."""
    return Number(_measure(ctx, "what", "height"))


def _measure(ctx, arg_name: str, axis: str) -> float:
    """One number from either side of the same question: how big is this box?

    A widget and a canvas both answer, because the plan spells it ``WIDTH(w)``
    inside a painter that was handed ``p`` - and the two are the same number, the
    canvas being the widget's own box with its origin at its top-left.
    """
    value = ctx.get(arg_name)
    held = value.target if isinstance(value, HostHandle) else value
    if isinstance(held, (Canvas, Widget)):
        return float(held.width if axis == "width" else held.height)
    raise ctx.fail(f"{ctx.name}: argument '{arg_name}' must be a widget handle or "
                   f"a paint handle, got {type_name(value)}")


@binding("P_RECT", ["p", "x", "y", "width", "height", "radius", "role"],
         area=AREA, optional=2,
         doc="Paint a box on the canvas: local px, clipped to the widget.")
def p_rect(ctx):
    """``P_RECT(p, 0, 0, WIDTH(w), HEIGHT(w), 4, "surface")``: the plate.

    ``(0, 0)`` is the widget's own top-left, and everything drawn here is clipped
    to its box - which is the one rule that makes a script painter safe to put in
    a layout it does not own.
    """
    held = _as_canvas(ctx, "p")
    try:
        held.rect(ctx.as_number("x"), ctx.as_number("y"), ctx.as_number("width"),
                  ctx.as_number("height"), _opt_number(ctx, "radius", 0.0),
                  _optional_value(ctx, "role"))
    except UIError as exc:
        _fail(ctx, exc)
    return Number.null


@binding("P_CIRCLE", ["p", "cx", "cy", "radius", "role"], area=AREA, optional=1,
         doc="Paint a filled disc at a local centre.")
def p_circle(ctx):
    """``P_CIRCLE(p, 12, 12, 8, "accent")``: a knob's cap, a beat's dot."""
    held = _as_canvas(ctx, "p")
    try:
        held.circle(ctx.as_number("cx"), ctx.as_number("cy"),
                    ctx.as_number("radius"), _optional_value(ctx, "role"))
    except UIError as exc:
        _fail(ctx, exc)
    return Number.null


@binding("P_ARC", ["p", "cx", "cy", "radius", "start", "end", "width", "role"],
         area=AREA, optional=2, doc="Paint a ring segment, in radians.")
def p_arc(ctx):
    """``P_ARC(p, cx, cy, r, a0, a1)``: the sweep of a filter or a level.

    ``width`` left out is the role's own border, so a ring drawn on a knob matches
    the ring the knob's plate has rather than being a hairline nobody chose.
    """
    held = _as_canvas(ctx, "p")
    try:
        held.arc(ctx.as_number("cx"), ctx.as_number("cy"),
                 ctx.as_number("radius"), ctx.as_number("start"),
                 _opt_number(ctx, "end"), _opt_number(ctx, "width"),
                 _optional_value(ctx, "role"))
    except UIError as exc:
        _fail(ctx, exc)
    return Number.null


@binding("P_LINE", ["p", "x1", "y1", "x2", "y2", "width", "role"], area=AREA,
         optional=1, doc="Paint a stroke between two local points.")
def p_line(ctx):
    """``P_LINE(p, 4, 12, 44, 12, 2, "track")``: a fader's groove, a needle."""
    held = _as_canvas(ctx, "p")
    try:
        held.line(ctx.as_number("x1"), ctx.as_number("y1"), ctx.as_number("x2"),
                  ctx.as_number("y2"), _opt_number(ctx, "width"),
                  _optional_value(ctx, "role"))
    except UIError as exc:
        _fail(ctx, exc)
    return Number.null


@binding("P_POLY", ["p", "points", "closed", "role"], area=AREA, optional=1,
         doc="Paint through local points; closed is filled, open is stroked.")
def p_poly(ctx):
    """``P_POLY(p, [0, 6, 4, 0, 8, 6], true, "text")``: a triangle, a waveform.

    The points are one flat list of numbers - or a list of pairs, which is what a
    loop tends to build. ``closed`` is the shape question rather than a geometry
    one: a closed path is a *filled* area (which is the only way a script draws a
    triangle) and an open one is a stroke, which is the only way it draws a
    waveform. A closed outline is still available as an open path that comes back
    to where it started.
    """
    held = _as_canvas(ctx, "p")
    said = value_to_python(ctx.get("points"))
    closed = ctx.as_bool("closed") if ctx.has("closed") else False
    try:
        held.poly(said, closed=closed, width=0.0 if closed else None,
                  role=_optional_value(ctx, "role"))
    except UIError as exc:
        _fail(ctx, exc)
    return Number.null


@binding("P_TEXT", ["p", "x", "y", "text", "size", "role", "align"], area=AREA,
         optional=3, doc="Paint one line of words at a local position.")
def p_text(ctx):
    """``P_TEXT(p, 8, 6, STR(GET(w, "value")), 12, "text")``: the number itself.

    ``size`` left out takes the role's own font, which is what makes a painted
    value the same height as the label printed beside it - the reason a painter is
    handed a skin rather than a number.
    """
    held = _as_canvas(ctx, "p")
    try:
        held.text(ctx.as_number("x"), ctx.as_number("y"),
                  _optional_value(ctx, "text", ""), _opt_number(ctx, "size"),
                  _optional_value(ctx, "role"),
                  _optional_value(ctx, "align", "left"))
    except UIError as exc:
        _fail(ctx, exc)
    return Number.null


@binding("P_GLYPH", ["p", "x", "y", "width", "height", "name", "role"],
         area=AREA, optional=1, doc="Paint one generated icon, from the atlas.")
def p_glyph(ctx):
    """``P_GLYPH(p, 44, 4, 16, 16, "minus", "text")``: an arrow nobody has a role for.

    The icon comes from the frame's own atlas and is tinted with the role's ink, so
    a script-drawn stepper and a ``TOGGLE`` beside it use one set of shapes and one
    theme - and a device-ratio change repaints it, because the atlas recuts itself.
    """
    held = _as_canvas(ctx, "p")
    try:
        held.glyph(ctx.as_number("x"), ctx.as_number("y"),
                   ctx.as_number("width"), ctx.as_number("height"),
                   ctx.as_string("name"), _optional_value(ctx, "role"))
    except UIError as exc:
        _fail(ctx, exc)
    return Number.null


@binding("P_IMAGE", ["p", "x", "y", "width", "height", "resource", "role"],
         area=AREA, optional=1, doc="Paint an image file, loaded once by the renderer.")
def p_image(ctx):
    """``P_IMAGE(p, 0, 0, 64, 64, "assets/logo.png")``: art that is not generated.

    Nothing here reads the file: the renderer loads it once and every frame after
    replays the key. A renderer with no decoder answers an empty key and the draw
    is skipped, which is the silence a missing file produces rather than a crash at
    sixty frames a second.
    """
    held = _as_canvas(ctx, "p")
    try:
        held.image(ctx.as_number("x"), ctx.as_number("y"),
                   ctx.as_number("width"), ctx.as_number("height"),
                   _optional_value(ctx, "resource", ""),
                   _optional_value(ctx, "role"))
    except UIError as exc:
        _fail(ctx, exc)
    return Number.null


@binding("P_CLIP", ["p", "x", "y", "width", "height"], area=AREA,
         doc="Cut the painter to a local box; closed by P_CLIP_END.")
def p_clip(ctx):
    """``P_CLIP(p, 4, 4, 32, 32)``: a scroll window, a meter's travel.

    Every clip is an intersection, so a box bigger than the widget is still one
    widget: a painter cannot reach the panel next to it by asking nicely.
    """
    held = _as_canvas(ctx, "p")
    try:
        held.clip(ctx.as_number("x"), ctx.as_number("y"), ctx.as_number("width"),
                  ctx.as_number("height"))
    except UIError as exc:
        _fail(ctx, exc)
    return Number.null


@binding("P_CLIP_END", ["p"], area=AREA, doc="Leave the innermost P_CLIP box.")
def p_clip_end(ctx):
    """``P_CLIP_END(p)``: the close that a painter must balance its opens with.

    One that raises with a clip still open is unwound by the cache rather than
    leaving the recorder clipped, but the list it was holding is thrown away - so
    the ops before the failure are not the face that gets drawn.
    """
    held = _as_canvas(ctx, "p")
    try:
        held.clip_end()
    except UIError as exc:
        _fail(ctx, exc)
    return Number.null


# -- style --------------------------------------------------------------


@binding("STYLE", ["widget", "prop", "value"], area=AREA, optional=2,
         doc="STYLE(w, role/size/glyph, word) or STYLE(theme) for the window.")
def style(ctx):
    """A style is a role, never a colour; one argument changes the window theme.

    ``STYLE(w, "role", "danger")`` and ``STYLE(w, "size", "sm")`` write a visual
    row through the same table ``SET`` validates against, but refuse anything
    that is not a style word, so a script cannot accidentally move a value here.
    ``STYLE("flat")`` with a single string is the whole window's theme
    (:meth:`~crude.ui.context.UIContext.set_theme`).
    """
    window = _window(ctx)
    if not ctx.has("prop"):
        # one argument: a theme name for the window, not a per-widget edit
        if isinstance(ctx.get("widget"), Widget) or (
                isinstance(ctx.get("widget"), HostHandle)
                and ctx.get("widget").kind == WIDGET_KIND):
            raise ctx.fail(
                f"{ctx.name}: STYLE needs the prop and value to style a widget; "
                "one argument changes the theme"
            )
        theme = ctx.as_string("widget")
        skin = window.set_theme(theme)
        return String(skin.theme.name)

    held = _as_widget(ctx, "widget")
    prop = ctx.as_string("prop")
    if prop not in STYLE_PROPS:
        raise ctx.fail(
            f"{ctx.name}: '{prop}' is not a style row (STYLE writes: "
            f"{', '.join(STYLE_PROPS)}; use SET for the rest)"
        )
    value = value_to_python(ctx.get("value"))
    try:
        held.set(prop, value)
    except UIError as exc:
        _fail(ctx, exc)
    return python_to_value(held.get(prop))


# -- introspect ---------------------------------------------------------


@binding("WIDGET_KINDS", [], area=AREA,
         doc="Every widget kind there is: the catalog's, then the script's own.")
def widget_kinds(ctx):
    """``WIDGET_KINDS()``: catalog kinds first, then anything this program defined."""
    names = list(catalog_names()) + list(registered_names())
    return List([String(name) for name in names])


@binding("WIDGET_PROPS", ["kind"], area=AREA,
         doc="Every prop name one kind holds, in the order a report prints them.")
def widget_props(ctx):
    """``WIDGET_PROPS("slider")``: the merged table a script's ``SET`` sees."""
    asked = ctx.get("kind")
    held = asked.target if isinstance(asked, HostHandle) else asked
    try:
        owner = get_kind(held if held is not None else ctx.as_string("kind"))
    except UIError as exc:
        _fail(ctx, exc)
    return List([String(name) for name in owner.props])


@binding("WIDGET_HAS", ["widget", "prop"], area=AREA,
         doc="Whether a kind holds a prop, so a handler can ask before a SET.")
def widget_has(ctx):
    """``WIDGET_HAS(w, "value")``: 1 or 0, so a generic handler can branch.

    The two :data:`LAYOUT_WORDS` answer 1 for every widget, because ``GET`` does
    answer them for every widget: a script that was told a kind "has no 'place'" and
    then watched ``GET(w, "place")`` return its glues would have been lied to about
    the one question the word is asked for.
    """
    held = _as_widget(ctx, "widget")
    prop = ctx.as_string("prop")
    if prop.strip().lower() in LAYOUT_WORDS:
        return Number(1)
    return Number(1 if prop_exists(held, prop) else 0)


# -- workspaces: named scenes in this window ----------------------------


def _scene_handle(held: Workspace) -> HostHandle:
    """Wrap a scene so a script can hold it, and name it in a report by its name."""
    return HostHandle(held, kind=WORKSPACE_KIND, label=held.name)


def _as_scene(ctx, window, arg_name: str) -> Workspace:
    """The scene an argument names: a handle, a name, or a number.

    All three because a script reaches for all three. ``WORKSPACE()`` hands back a
    handle, ``WORKSPACE_SHOW("mixer")`` is what a person writes, and the value an
    ``ON_CLICK`` argument became is whichever of those the script had been holding -
    the same three shapes :func:`_as_widget` answers for, read against this window's
    own list rather than against the tree.
    """
    value = ctx.get(arg_name)
    if isinstance(value, Workspace):
        return value
    if isinstance(value, HostHandle) and isinstance(value.target, Workspace):
        return value.target
    try:
        return window.workspaces.get(value_to_python(value))
    except WorkspaceError as exc:
        raise ctx.fail(f"{ctx.name}: {exc}") from None
    except (TypeError, ValueError):
        raise ctx.fail(
            f"{ctx.name}: argument '{arg_name}' must be a workspace handle, a "
            f"workspace name, or a number, got {type_name(value)}") from None


@binding("WORKSPACE", ["name", "on_enter"], area=AREA, optional=2,
         doc="WORKSPACE() reads the scene; with a name, make it and show it.")
def workspace(ctx):
    """``WORKSPACE("mixer")``: declare a scene and switch to it; ``WORKSPACE()`` reads.

    One word for both questions because the answer to "which scene is this" and the
    act of making it are the same piece of state, and a script that had to spell
    ``WORKSPACE_CREATE`` and ``WORKSPACE_CURRENT`` would be writing a type system
    rather than a front panel. With an ``on_enter`` the scene is one that rebuilds
    itself - the function runs every time the scene is shown, and the scene stops
    being carried across switches (:func:`WORKSPACE_KEEP` puts it back).
    """
    window = _window(ctx)
    if not ctx.has("name"):
        return String(window.workspaces.current_name)
    name = ctx.as_string("name")
    func = ctx.as_function("on_enter") if ctx.has("on_enter") else None
    try:
        held = window.workspaces.declare(name, on_enter=func)
        window.workspaces.show(held.name)
    except (WorkspaceError, UIError) as exc:
        _fail(ctx, exc)
    return _scene_handle(held)


@binding("WORKSPACE_SHOW", ["workspace"], area=AREA,
         doc="Show one scene by name or by number; the name now on screen.")
def workspace_show(ctx):
    """``WORKSPACE_SHOW("mixer")``: clear the window below the menu and put this scene in.

    Every other scene's widgets leave the tree - carried or thrown away, which is
    each one's :func:`WORKSPACE_KEEP` answer - and the target's arrive in the order
    they left. A name the window does not have is refused with the list it does have.
    """
    window = _window(ctx)
    held = _as_scene(ctx, window, "workspace")
    try:
        shown = window.workspaces.show(held.name)
    except (WorkspaceError, UIError) as exc:
        _fail(ctx, exc)
    return String(shown)


@binding("WORKSPACE_NAMES", [], area=AREA,
         doc="Every scene this window has, in the order a menu should list them.")
def workspace_names(ctx):
    """``WORKSPACE_NAMES()``: the array, ``root`` first, always."""
    window = _window(ctx)
    return List([String(name) for name in window.workspaces.names()])


@binding("WORKSPACE_INDEX", [], area=AREA,
         doc="Which number the scene on screen is; WORKSPACE_SHOW takes it back.")
def workspace_index(ctx):
    """``WORKSPACE_INDEX()``: the current scene's position in ``WORKSPACE_NAMES()``."""
    window = _window(ctx)
    return Number(float(window.workspaces.index_of(window.workspaces.current_name)))


@binding("WORKSPACE_ADD", ["workspace", "widget"], area=AREA,
         doc="Put a widget into a scene, whether or not it is on screen.")
def workspace_add(ctx):
    """``WORKSPACE_ADD(mixer, w)``: parent a widget into a scene rather than a panel.

    The scene's own panel is the container, which is why a scene can be built
    before it is ever shown: ``NODE_ADD`` asks a *widget* to hold this one, and this
    asks a *scene* to. The widget's owner is restated as the scene's, so a switch
    takes it out of the window with the rest of them.
    """
    window = _window(ctx)
    held = _as_scene(ctx, window, "workspace")
    child = _as_widget(ctx, "widget")
    if child.workspace == SHARED_WORKSPACE:
        raise ctx.fail(
            f"{ctx.name}: '{child.path()}' belongs to the window, not to a scene: "
            "the menu bar and every popup cannot be moved into a workspace")
    if held.name == ROOT_WORKSPACE:
        # the entry script's scene *is* the window's root, so putting a widget into
        # it is the same act as ``NODE_ADD(root, w)`` and says so in the same words
        parent = window.root
    else:
        parent = window.workspaces.container(held)
    try:
        parent.add(child)
        child.workspace = held.name
    except UIError as exc:
        _fail(ctx, exc)
    return _handle(child)


@binding("WORKSPACE_KEEP", ["workspace", "keep"], area=AREA,
         doc="Carry a scene across switches (1) or rebuild it on entry (0).")
def workspace_keep(ctx):
    """``WORKSPACE_KEEP(mixer, 0)``: what leaving the scene costs.

    ``1`` hides it by taking the subtree out of the tree, so every prop the person
    set - a sampler's tuning, a mixer's faders - is there when the scene comes back.
    ``0`` removes it, releasing every handler it registered, and the scene's
    ``on_enter`` is the only thing that puts it back; a scene with no ``on_enter``
    asked to rebuild arrives empty, which is what the script asked for.
    """
    window = _window(ctx)
    held = _as_scene(ctx, window, "workspace")
    held.keep = ctx.as_bool("keep")
    return Number(1 if held.keep else 0)


# -- the names this module registers ------------------------------------

#: Every ``ui`` binding, in the order a report should read them: create,
#  configure (the ``SET``/``GET`` this module owns through ``core``), place - the
#  box, then the pass's own verbs for gluing, resizing and arranging - wire,
#  style, introspect. ``SET``/``GET`` belong to :mod:`crude.bindings.core` and so
#  are not listed here even though this module makes them mean widgets, and makes
#  them answer ``place`` and ``pack`` out of the layout pass as well.
BINDINGS = (
    "WIDGET", *CONSTRUCTORS,
    "WIDGET_DEFINE", "PROP_DEFINE",
    "PLACE", "NODE_ADD",
    "ANCHOR", "UNANCHOR", "PACK", "PAD",
    "HSTACK", "VSTACK", "GRID", "UNSTACK",
    "GROUP_FOLD", "GROUP_MOVE", "GROUP_UP", "GROUP_DOWN",
    "GROUP_DOCK", "GROUP_UNDOCK", "GROUPS",
    "LINK_VALUE", "LINK_STATE", "UNLINK",
    *(f"ON_{event.upper()}" for event in WIDGET_EVENTS),
    *(f"ON_{event.upper()}" for event in SCRIPT_EVENTS),
    "ON_FRAME", "OFF", "OFF_FRAME", "MOD_HELD", "SENDER",
    "WIDTH", "HEIGHT",
    "P_RECT", "P_CIRCLE", "P_ARC", "P_LINE", "P_POLY", "P_TEXT", "P_GLYPH",
    "P_IMAGE", "P_CLIP", "P_CLIP_END", "PAINT_REQUEST",
    "STYLE",
    "WIDGET_KINDS", "WIDGET_PROPS", "WIDGET_HAS",
    "WORKSPACE", "WORKSPACE_SHOW", "WORKSPACE_NAMES", "WORKSPACE_INDEX",
    "WORKSPACE_ADD", "WORKSPACE_KEEP",
)
