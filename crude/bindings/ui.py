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
from .core import own_props
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
#  the two words the plan spells differently from the table:
#
#  * ``TOGGLE(label[, on])`` - ``on`` is the latched *value*, which is a 0/1 row
#    no table can name "on" without restating what a toggle holds;
#  * ``ICON(name, size)`` - ``name`` is the ``glyph`` row (the kind's own first
#    ``args`` entry) and ``size`` is the box it is drawn in, which is placement
#    rather than a prop: it lands as the widget's own box and any later
#    ``PLACE`` overrules it, which is the plan's one answer for size.
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
        ("PLOT", "plot"), ("LOGVIEW", "logview"),
        ("ADSR", "adsr"), ("CURVE", "curve"), ("XY_PAD", "xy"),
        ("KEYBED", "keybed"), ("PRESET_BAR", "preset_bar"),
        # the viewport: a panel a script fills with children and the pointer
        # slides, so its one argument is the panel's own label
        ("SCROLL", "scroll"),
    )
}
CONSTRUCTORS["ICON"] = ("icon", ("glyph", "size"), 1)
CONSTRUCTORS["TOGGLE"] = ("toggle", ("label", "on"), 1)

#: the words ``TOGGLE(label, on)`` reads as on and off, the same list
#  :mod:`crude.ui.props` settles a bool prop with
_ON_WORDS = ("true", "yes", "on", "1")
_OFF_WORDS = ("false", "no", "off", "0")


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
    the row at the default the same table declares. The two slots the table
    cannot name - a toggle's ``on``, an icon's ``size`` - are translated here
    and nowhere else, which is what keeps the plan's constructor-equals-``SET``
    rule true for every kind that needs no translation at all.
    """
    window = _window(ctx)
    values = {}
    box = None
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
    """One readable prop, wrapped for the script; a bad name says which kind."""
    try:
        return python_to_value(handle.target.get(prop))
    except UIError as exc:
        raise ctx.fail(f"GET: {exc}")


def _widget_set(ctx, handle, prop, value):
    """Write through the kind's table and answer at the settled value.

    Returning what the widget ended up holding is what keeps a clamp honest: a
    ``SET(w, "value", 500)`` on a slider that tops out at 300 hands back 300
    rather than the number the script asked for and the widget refuses.
    """
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
    """``WIDGET_HAS(w, "value")``: 1 or 0, so a generic handler can branch."""
    held = _as_widget(ctx, "widget")
    prop = ctx.as_string("prop")
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
#  configure (the ``SET``/``GET`` this module owns through ``core``), place, wire,
#  style, introspect. ``SET``/``GET`` belong to :mod:`crude.bindings.core` and so
#  are not listed here even though this module makes them mean widgets.
BINDINGS = (
    "WIDGET", *CONSTRUCTORS,
    "WIDGET_DEFINE", "PROP_DEFINE",
    "PLACE", "NODE_ADD",
    "LINK_VALUE", "LINK_STATE", "UNLINK",
    *(f"ON_{event.upper()}" for event in WIDGET_EVENTS),
    *(f"ON_{event.upper()}" for event in SCRIPT_EVENTS),
    "ON_FRAME", "OFF", "OFF_FRAME",
    "WIDTH", "HEIGHT",
    "P_RECT", "P_CIRCLE", "P_ARC", "P_LINE", "P_POLY", "P_TEXT", "P_GLYPH",
    "P_IMAGE", "P_CLIP", "P_CLIP_END", "PAINT_REQUEST",
    "STYLE",
    "WIDGET_KINDS", "WIDGET_PROPS", "WIDGET_HAS",
    "WORKSPACE", "WORKSPACE_SHOW", "WORKSPACE_NAMES", "WORKSPACE_INDEX",
    "WORKSPACE_ADD", "WORKSPACE_KEEP",
)
