"""Authoring bindings: build a patch from ``.crude`` instead of drawing one.

These are the script-facing half of :mod:`crude.pd.model` and
:mod:`crude.pd.writer`. The shape is always the same:

.. code-block:: crude

    VAR p = PD_BUILD("synth")
    VAR osc  = PD_OBJ(p, "osc~ 440")
    VAR dac  = PD_OBJ(p, "dac~")
    VAR freq = PD_NUM(p, "freq")
    PD_LINK(p, freq, osc)
    PD_LINK(p, osc, dac)
    PD_LINK(p, osc, dac, 0, 1)
    PD_LOAD(p)

* every ``PD_*`` box call returns a **box handle**, usable as the argument of
  ``PD_LINK`` (beside the patch) and by ``GET``/``SET`` (``SET(osc, "x", 300)``);
* a GUI control is ``PD_GUI``, which writes the box exactly as Pd saves it, from
  the argument table in :mod:`crude.pd.pdgui`; a widget already on the screen
  becomes the same box through ``PD_EMIT``, named by the props table;
* ``PD_BUILD`` returns a patch handle around the document under construction and
  ``PD_LOAD`` returns one around the loaded patch - the same variable keeps
  working for ``PD_SEND``, because both resolve to the patch by id;
* nothing is written to disk until ``PD_LOAD`` (or ``PD_SAVE``), and once a
  patch is loaded its document is frozen: editing it further would silently
  diverge from what Pd has, so the mutation bindings say so instead.

Coordinates are optional everywhere. Omitted, boxes land on a grid
(:meth:`PatchDoc.next_position <crude.pd.model.PatchDoc.next_position>`), which
is what makes a fully scripted patch readable when someone opens the generated
file in Pd to see what the script did.
"""

from __future__ import annotations

from typing import List as PyList

from ..lang.values import HostHandle, List, Number, String
from ..pd.engine import PdError, PdPatch
from ..pd.model import BOX_KINDS, PatchDoc, PatchError
from ..pd.pdgui import (
    GuiError, PD_VERSION, box_tag, format_box, has_template, kinds as gui_kinds,
    sized, slots, unverified,
)
from ..pd.reader import describe
from .handles import (
    BOX_KIND as BOX_HANDLE_KIND,
    box_handle,
    box_of,
    doc_of,
    patch_handle,
    patch_id_of,
    runtime_of,
    values_from,
    widget_of,
)
from .registry import binding

AREA = "pd"

#: Both halves of the authoring stack raise their own error type: the document
#: (`PatchError`) and the runtime around it (`PdError`). To a script they are
#: the same thing - a message about the patch it just described. A GUI box's
#: template table (`GuiError`) is the third voice saying the same thing, and it
#: is a `ValueError`, so it has to be named here to stay a script error instead
#: of escaping as a Python traceback.
PATCH_FAULT = (PatchError, PdError, GuiError)


# -- helpers ------------------------------------------------------------
def _runtime(ctx):
    return runtime_of(ctx)


def _doc(ctx, arg_name: str = "patch") -> PatchDoc:
    """The document a mutation applies to, refusing a loaded patch."""
    doc = doc_of(ctx, arg_name)
    if doc.id in _runtime(ctx).patches:
        raise ctx.fail(
            f"{ctx.name}: patch '{doc.id}' is already loaded - PD_LOAD freezes "
            f"it, so this change would not reach Pd"
        )
    return doc


def _place(ctx, doc: PatchDoc):
    """``x``/``y`` when the script gave them, otherwise the next grid slot."""
    default_x, default_y = doc.next_position()
    x = ctx.as_int("x") if ctx.has("x") else default_x
    y = ctx.as_int("y") if ctx.has("y") else default_y
    return x, y


def _add(ctx, kind: str, text: str = "", **kwargs) -> HostHandle:
    """One box call: create it in the document and hand back a handle."""
    doc = _doc(ctx)
    x, y = _place(ctx, doc)
    try:
        box = doc.add_box(kind, x, y, text, **kwargs)
    except PATCH_FAULT as exc:
        raise ctx.fail(f"{ctx.name}: {exc}")
    return box_handle(doc, box)


def _reference(ctx, arg_name: str, doc: PatchDoc):
    """A box handle or a box id - scripts link by either."""
    value = ctx.get(arg_name)
    if isinstance(value, HostHandle) and value.kind == BOX_HANDLE_KIND:
        return box_of(ctx, arg_name, doc)
    if isinstance(value, String):
        try:
            return doc.box(value.value)
        except PATCH_FAULT as exc:
            raise ctx.fail(f"{ctx.name}: {exc}")
    ctx.bad_type(arg_name, "box handle or box id", value)


def _numbers(ctx, arg_name) -> PyList:
    """A list argument as floats, empty when omitted."""
    return [float(value) for value in values_from(ctx, arg_name)]


#: The "GUI boxes" that are not iemgui boxes: bare canvas comments, which Pd
#: writes as ``#X text`` and which have no arguments at all - just text.
COMMENT_KINDS = ("comment", "text")


def _props_map(ctx) -> dict:
    """The ``props`` argument as a ``name -> value`` map.

    An alternating list, the same shape ``SET`` bulk loops use: a GUI box's
    arguments differ per kind, and a twenty-argument binding signature would be
    a table nobody could read. Names are matched against the props table's
    ``pd_name`` first and the slot's own name after, which is
    :func:`crude.pd.pdgui.arguments`' order, so the list cannot quietly disagree
    with a widget map.
    """
    given = values_from(ctx, "props")
    if len(given) % 2:
        raise ctx.fail(
            f"{ctx.name}: 'props' is a list of name/value pairs and this one has "
            f"{len(given)} item(s), which leaves one without a name"
        )
    held = {}
    for index in range(0, len(given), 2):
        key = given[index]
        if not isinstance(key, str):
            raise ctx.fail(
                f"{ctx.name}: a 'props' name must be a string, and position "
                f"{index} holds {key!r}"
            )
        held[key.strip().lower()] = given[index + 1]
    return held


def _check_names(ctx, kind: str, held: dict) -> None:
    """Refuse a name this box cannot hold, rather than dropping it quietly."""
    possible = {slot.name for slot in slots(kind)}
    possible |= {slot.field for slot in slots(kind) if slot.field}
    unknown = sorted(name for name in held if name not in possible)
    if unknown:
        raise ctx.fail(
            f"{ctx.name}: an [{box_tag(kind)}] box has no "
            f"{', '.join(repr(name) for name in unknown)} argument - it holds "
            f"{', '.join(sorted(possible))}"
        )


def _comment(ctx, doc: PatchDoc, x: int, y: int, held: dict):
    """A bare label: the one canvas object whose only argument is text."""
    text = held.get("text") or held.get("label")
    if not text:
        raise ctx.fail(
            f"{ctx.name}: a comment needs its text, as "
            f"PD_GUI(p, \"comment\", x, y, 0, 0, [\"text\", \"...\"])"
        )
    return doc.add_box("text", x, y, str(text))


# -- the Pd twin of a widget --------------------------------------------

#: ``PatchDoc.meta`` key holding the widgets this patch has already been warned
#: about, so a nameless widget emitted in a loop costs one sentence, not one per
#: call. Per document, which is also why it dies with the patch instead of
#: leaking into the next program run.
EMIT_WARNED = "pd_emit_warned"

#: What a widget's two names become when there is no box to carry them. The widget
#: *sends* its value out under ``send``, so ``[r send]`` is the object in the patch
#: that hears it; it *listens* on ``recv``, so ``[s recv]`` is how the patch can
#: push a value back. This is the plan's wiring rule for a kind with no Pd object.
WIRING_TAGS = (("send", "r"), ("recv", "s"))


def _placed(given: dict, name: str, from_widget: float) -> int:
    """One coordinate: what the call said, or what the widget's own box says."""
    value = given.get(name)
    return int(round(from_widget if value is None else value))


def _emit_box(ctx, doc: PatchDoc, widget, kind: str, values: dict, given: dict):
    """The real control box, laid out by the template table and written verbatim.

    The values are the props table's own (:meth:`~crude.ui.widget.Widget.pd_arguments`),
    so which prop lands in which Pd argument stays :mod:`crude.ui.props`' decision;
    this only asks the table for the shape. A prop the table names that this box has
    no argument for is dropped without complaint, which is the opposite of
    ``PD_GUI``'s rule and for the same reason it is not a contradiction: there the
    script asked for ``step`` by name, here the widget asked for it and a Pd box
    simply has no such argument.
    """
    if not has_template(kind):
        reason = unverified().get(
            kind, f"this table lays out: {', '.join(gui_kinds())}")
        raise ctx.fail(
            f"{ctx.name}: '{widget.path()}' asks for a [{kind}] box and there is "
            f"no Pd {PD_VERSION} template for it ({reason}) - set its "
            "'pd_object' prop to \"\" for wiring with no box, or to a box that "
            "exists"
        )
    if widget.has_value and not (values.get("send") or values.get("recv")):
        raise ctx.fail(
            f"{ctx.name}: '{widget.path()}' has no 'send' or 'recv' name, so its "
            f"[{box_tag(kind)}] twin would be wired to nothing"
        )

    screen = widget.screen_box
    line = format_box(kind, sized(kind, values,
                                  given.get("width") or screen[2],
                                  given.get("height") or screen[3]))
    return doc.add_gui_box(
        line,
        _placed(given, "x", screen[0]),
        _placed(given, "y", screen[1]),
        send=str(values.get("send") or ""),
        recv=str(values.get("recv") or ""),
        label=str(values.get("label") or ""),
    )


def _emit_wiring(ctx, doc: PatchDoc, widget, values: dict, given: dict):
    """No box for this widget: the label it is, or the wiring it can still do.

    Two honest answers, and the props table decides which. A kind that holds no
    value (``text``, ``icon``) is a line on the canvas, which is what Pd's own
    ``#X text`` is for. A kind that does hold one but has no box - a script
    ``WIDGET_DEFINE``d kind, or a catalog kind a script cleared - still has names
    to talk to the patch by, so it gets ``[r send]``/``[s recv]``: the data path
    survives even though the control does not, and the widget keeps being the
    thing the person clicks.
    """
    if not widget.has_value:
        said = str(values.get("label") or "")
        if not said:
            raise ctx.fail(
                f"{ctx.name}: '{widget.path()}' has no Pd box, no value and no "
                f"'label', so there is nothing here to write"
            )
        return doc.add_text(said, _placed(given, "x", widget.screen_box[0]),
                            _placed(given, "y", widget.screen_box[1]))

    named = [(name, tag) for name, tag in WIRING_TAGS if values.get(name)]
    if not named:
        raise ctx.fail(
            f"{ctx.name}: '{widget.path()}' has no 'pd_object' box and no 'send' "
            f"or 'recv' name, so there is nothing here to wire to the patch"
        )

    _warn_emit(ctx, doc, widget)
    first = None
    for name, tag in named:
        # wiring is plumbing, not a visual twin: it goes on the authoring grid
        # unless the call said where, and the second object never lands on top
        # of the first
        if given.get("x") is not None and given.get("y") is not None:
            x, y = given["x"], given["y"]
        else:
            x, y = doc.next_position()
        box = doc.add_object(f"{tag} {values[name]}", x, y)
        first = first or box
    return first


def _warn_emit(ctx, doc: PatchDoc, widget) -> None:
    """One sentence per widget per patch for the degrade path, through the log."""
    seen = doc.meta.setdefault(EMIT_WARNED, set())
    key = f"{widget.path()}|{widget.kind.name}"
    if key in seen:
        return
    seen.add(key)
    _runtime(ctx).log(
        f"PD_EMIT: '{widget.path()}' (kind '{widget.kind.name}') has no Pd box - "
        f"emitted its [r]/[s] wiring instead; '{doc.id}' reports this once"
    )


# -- the document -------------------------------------------------------


@binding("PD_BUILD", ["id", "width", "height", "font"], area=AREA, optional=3)
def pd_build(ctx):
    """Start authoring a patch; the returned handle accepts ``PD_OBJ`` & friends."""
    runtime = _runtime(ctx)
    patch_id = ctx.as_string("id")
    kwargs = {}
    for name, key in (("width", "width"), ("height", "height"), ("font", "font")):
        if ctx.has(name):
            kwargs[key] = ctx.as_int(name)
    try:
        doc = runtime.build(patch_id, **kwargs)
    except PATCH_FAULT as exc:
        raise ctx.fail(f"PD_BUILD: {exc}")
    return patch_handle(doc)


@binding("PD_OBJ", ["patch", "text", "x", "y"], area=AREA, optional=2)
def pd_obj(ctx):
    """An object box: ``PD_OBJ(p, "osc~ 440")`` (``graph`` becomes a graph)."""
    doc = _doc(ctx)
    x, y = _place(ctx, doc)
    text = ctx.as_string("text")
    try:
        box = doc.add_object(text, x, y)
    except PATCH_FAULT as exc:
        raise ctx.fail(f"PD_OBJ: {exc}")
    return box_handle(doc, box)


@binding("PD_MSG", ["patch", "text", "x", "y"], area=AREA, optional=2)
def pd_msg(ctx):
    """A message box: ``PD_MSG(p, "set $1")``."""
    return _add(ctx, "msg", ctx.as_string("text"))


@binding("PD_NUM", ["patch", "name", "x", "y"], area=AREA, optional=2)
def pd_num(ctx):
    """A number box called ``name``: labelled, and settable with ``PD_SEND``."""
    name = ctx.as_string("name")
    return _add(ctx, "floatatom", label=name, recv=name)


@binding("PD_SYMB", ["patch", "name", "x", "y"], area=AREA, optional=2)
def pd_symb(ctx):
    """A symbol box called ``name`` (the string counterpart of ``PD_NUM``)."""
    name = ctx.as_string("name")
    return _add(ctx, "symbolatom", label=name, recv=name)


@binding("PD_TEXT", ["patch", "text", "x", "y"], area=AREA, optional=2)
def pd_text(ctx):
    """A comment on the canvas. Never connected, so never warned about."""
    return _add(ctx, "text", ctx.as_string("text"))


@binding("PD_GUI", ["patch", "kind", "x", "y", "width", "height", "props"],
         area=AREA, optional=5)
def pd_gui(ctx):
    """A real Pd control box: ``PD_GUI(p, "hslider", 20, 60, 200, 20,
    ["min", 20, "max", 300, "value", 120, "label", "BPM"])``.

    The box is written exactly as Pd saves one, from the template table in
    :mod:`crude.pd.pdgui` - the same table ``PD_EMIT`` uses for a widget, and the
    only place a box's argument order exists. ``props`` is a list of name/value
    pairs, each named by the props table's ``pd_name`` (``min``, ``send``,
    ``labels``, ...) or by the box's own argument (``width``, ``size``,
    ``initandsend``); a name the box cannot hold is an error, never a drop.
    ``width``/``height`` fill the box's pixel arguments when ``props`` does not
    name them, and for a square box (``tgl``, ``bng``) the smaller side is the
    size, because a box has one of those.

    ``"comment"`` is the exception: a bare ``#X text`` label, whose text comes
    from ``props``. A ``dropdown`` or ``knob`` says so out loud - vanilla Pd has
    no such box, so there is no template to copy.
    """
    doc = _doc(ctx)
    kind = ctx.as_string("kind").strip().lower()
    x, y = _place(ctx, doc)
    width = ctx.as_number("width") if ctx.has("width") else None
    height = ctx.as_number("height") if ctx.has("height") else None
    held = _props_map(ctx)

    try:
        if kind in COMMENT_KINDS:
            return box_handle(doc, _comment(ctx, doc, x, y, held))
        held = sized(kind, held, width, height)
        _check_names(ctx, kind, held)
        box = doc.add_gui_box(
            format_box(kind, held),
            x,
            y,
            send=str(held.get("send") or ""),
            recv=str(held.get("recv") or ""),
            label=str(held.get("label") or ""),
        )
    except PATCH_FAULT as exc:
        raise ctx.fail(f"{ctx.name}: {exc}")
    return box_handle(doc, box)


@binding("PD_EMIT", ["patch", "widget", "x", "y", "width", "height"],
         area=AREA, optional=4)
def pd_emit(ctx):
    """The Pd twin of a widget that is already on the screen.

    ``PD_EMIT(p, w)`` asks the two tables the widget's own drawing asks:
    :func:`crude.ui.props.pd_object_for` for which box it twins to - the widget's
    ``pd_object`` prop, else the kind's inherited one - and
    :func:`crude.ui.props.pd_arguments` for the values that box wants, keyed by
    ``pd_name``. So ``SLIDER`` becomes ``[hsl``, ``NUMBER`` ``[nbx]``, ``TOGGLE``
    ``[tgl]``, ``METER`` ``[vu]``, ``BUTTON`` ``[bng]``, ``PANEL`` ``[cnv]``, and
    the box carries the widget's own ``send``/``recv`` names, which is what makes
    the slider and the Pd box drive the same receivers.

    Position comes from the widget's box on the screen, so a patch opened in Pd
    looks like the window it came from; ``x``, ``y``, ``width`` and ``height``
    override it, and ``width``/``height`` reach only the size arguments the box
    really measures in pixels (see :func:`crude.pd.pdgui.sized`).

    Where there is no box, the call says what it did instead of writing nothing:
    a ``pd_object`` that names a box with no Pd template, and a widget that holds
    a value with neither name, are both errors naming the widget and the prop; a
    widget with names but no box (a script-defined kind) takes the plan's degrade
    path - ``[r send]``/``[s recv]`` wiring and one warning per widget per patch -
    and a ``TEXT`` or ``ICON``, which holds nothing, becomes a ``#X text`` label.

    Emitting the same widget twice appends a second box: the emitter keeps no
    registry of widgets, so a script that rebuilds should rebuild the document
    with it.
    """
    doc = _doc(ctx)
    widget = widget_of(ctx, "widget")
    given = {name: (int(ctx.as_number(name)) if ctx.has(name) else None)
             for name in ("x", "y", "width", "height")}
    values = widget.pd_arguments()
    box_kind = widget.pd_object

    try:
        box = (_emit_box(ctx, doc, widget, box_kind, values, given) if box_kind
               else _emit_wiring(ctx, doc, widget, values, given))
    except PATCH_FAULT as exc:
        raise ctx.fail(f"{ctx.name}: {exc}")
    return box_handle(doc, box)


@binding("PD_TABLE", ["patch", "name", "size", "x", "y", "values", "scoped"],
         area=AREA, optional=4)
def pd_table(ctx):
    """A named array.

    ``PD_TABLE(p, "wave", 16)`` writes ``table $0-wave 16``: the name is scoped
    to this patch instance unless ``scoped`` is 0, so two copies of a patch do
    not overwrite each other's samples. Scripts keep calling it ``"wave"`` -
    ``PD_ARRAY_WRITE`` resolves the prefix.
    """
    doc = _doc(ctx)
    x, y = _place(ctx, doc)
    name = ctx.as_string("name")
    size = ctx.as_int("size")
    values = _numbers(ctx, "values")
    scoped = ctx.as_bool("scoped") if ctx.has("scoped") else True
    try:
        box = doc.add_table(name, size, x, y, init=values or None, scoped=scoped)
    except PATCH_FAULT as exc:
        raise ctx.fail(f"PD_TABLE: {exc}")
    return box_handle(doc, box)


@binding("PD_SEED", ["patch", "name", "values"], area=AREA)
def pd_seed(ctx):
    """Replace the contents of table ``name`` before it is loaded."""
    doc = _doc(ctx)
    values = _numbers(ctx, "values")
    try:
        box = doc.seed(ctx.as_string("name"), values)
    except PATCH_FAULT as exc:
        raise ctx.fail(f"PD_SEED: {exc}")
    return box_handle(doc, box)


@binding("PD_LINK", ["patch", "src", "dst", "outlet", "inlet"], area=AREA, optional=2)
def pd_link(ctx):
    """Wire ``src`` to ``dst`` (``outlet``/``inlet`` default to 0).

    ``PD_LINK(p, freq, osc)`` is outlet 0 to inlet 0; a ``dac~`` needs the
    second inlet as well, which is ``PD_LINK(p, osc, dac, 0, 1)``. Boxes may be
    handles or the ids ``PD_BOXES`` lists.
    """
    doc = _doc(ctx)
    src = _reference(ctx, "src", doc)
    dst = _reference(ctx, "dst", doc)
    outlet = ctx.as_int("outlet") if ctx.has("outlet") else 0
    inlet = ctx.as_int("inlet") if ctx.has("inlet") else 0
    try:
        link = doc.link(src, outlet, dst, inlet)
    except PATCH_FAULT as exc:
        raise ctx.fail(f"PD_LINK: {exc}")
    return String(link.describe())


@binding("PD_UNLINK", ["patch", "src", "dst", "outlet", "inlet"], area=AREA, optional=2)
def pd_unlink(ctx):
    """Remove a connection; answers 1 when there was one to remove."""
    doc = _doc(ctx)
    src = _reference(ctx, "src", doc)
    dst = _reference(ctx, "dst", doc)
    outlet = ctx.as_int("outlet") if ctx.has("outlet") else 0
    inlet = ctx.as_int("inlet") if ctx.has("inlet") else 0
    try:
        return Number(1 if doc.unlink(src, outlet, dst, inlet) else 0)
    except PATCH_FAULT as exc:
        raise ctx.fail(f"PD_UNLINK: {exc}")


@binding("PD_BOXES", ["patch"], area=AREA)
def pd_boxes(ctx):
    """Every box id on the canvas, in creation order (``GET`` needs the handle)."""
    doc = doc_of(ctx)
    return List([String(box.id) for box in doc.boxes])


# -- output -------------------------------------------------------------


@binding("PD_DUMP", ["patch"], area=AREA)
def pd_dump(ctx):
    """The ``.pd`` text an authored patch would write, as one string."""
    doc = doc_of(ctx)
    try:
        text = _runtime(ctx).dump(doc.id)
    except PATCH_FAULT as exc:
        raise ctx.fail(f"PD_DUMP: {exc}")
    return String(text)


@binding("PD_SAVE", ["patch", "path"], area=AREA, optional=1)
def pd_save(ctx):
    """Write the patch: to ``path``, or into the build cache when omitted."""
    doc = doc_of(ctx)
    path = ctx.as_string("path") if ctx.has("path") else None
    try:
        saved = _runtime(ctx).save(doc.id, path)
    except PATCH_FAULT as exc:
        raise ctx.fail(f"PD_SAVE: {exc}")
    return String(saved)


@binding("PD_INSPECT", ["patch", "box"], area=AREA, optional=1)
def pd_inspect(ctx):
    """What is on the canvas: one box's description, or the whole patch."""
    doc = doc_of(ctx)
    if ctx.has("box"):
        box = _reference(ctx, "box", doc)
        return String(f"{box.id}: {box.describe()}")
    try:
        warnings = doc.validate()
    except PATCH_FAULT as exc:
        raise ctx.fail(f"PD_INSPECT: {exc}")
    # The warnings are shown rather than logged: asking for a report is asking
    # to hear what is odd about this patch.
    doc.warnings = warnings
    return List([String(line) for line in describe(doc)])


@binding("PD_LOAD", ["patch"], area=AREA)
def pd_load(ctx):
    """Serialize, cache and open an authored patch; answers the live patch.

    Accepts the handle ``PD_BUILD`` returned or the patch's id as a string, and
    is a no-op when the patch is already open.
    """
    runtime = _runtime(ctx)
    value = ctx.get("patch")

    if isinstance(value, String):
        patch_id = value.value
        try:
            runtime.doc(patch_id)
        except PATCH_FAULT as exc:
            raise ctx.fail(f"PD_LOAD: {exc}")
    elif isinstance(value, HostHandle) and value.kind == "patch":
        patch_id = patch_id_of(ctx, "patch")
        target = value.target
        if isinstance(target, PdPatch):
            return value  # already loaded: the same handle keeps working
    else:
        ctx.bad_type("patch", "patch built with PD_BUILD", value)

    try:
        patch = runtime.load_authored(patch_id)
    except PATCH_FAULT as exc:
        raise ctx.fail(f"PD_LOAD: {exc}")
    return patch_handle(patch)


@binding("PD_KINDS", [], area=AREA)
def pd_kinds(ctx):
    """The box kinds ``PD_OBJ`` and friends can create (mostly for help text)."""
    return List([String(kind) for kind in BOX_KINDS])


@binding("PD_GUI_KINDS", [], area=AREA)
def pd_gui_kinds(ctx):
    """The widget kinds ``PD_GUI`` and ``PD_EMIT`` can make a real box for.

    Straight out of :func:`crude.pd.pdgui.kinds`, which is the template table's
    own key list: a script asking what is available gets the same names the
    table answers with, and no second copy to drift.
    """
    return List([String(kind) for kind in gui_kinds()])


#: Names this module registers, used by the binding-help generator and tests.
BINDINGS = (
    "PD_BUILD", "PD_OBJ", "PD_MSG", "PD_NUM", "PD_SYMB", "PD_TEXT", "PD_GUI",
    "PD_EMIT", "PD_TABLE", "PD_SEED", "PD_LINK", "PD_UNLINK", "PD_BOXES",
    "PD_DUMP", "PD_SAVE", "PD_INSPECT", "PD_LOAD",
    "PD_KINDS", "PD_GUI_KINDS",
)
