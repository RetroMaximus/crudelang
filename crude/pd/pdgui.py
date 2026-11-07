"""The real Pd GUI box, as ``crude`` writes one: a table of argument slots.

An authored patch is normally audio objects. A *GUI* box is different: it is the
thing a person clicks in Pd itself - ``[tgl]``, ``[hslider]``, ``[nbx]`` - and its
arguments are a fixed, ordered, version-specific layout that Pd both reads and
writes. This module is the one place that layout lives, so that:

* ``PD_GUI`` can append a real control box to a patch, and
* ``PD_EMIT`` can turn a GL widget into the Pd box it twins to,

without either one hand-writing an argument string that would silently drift from
what Pd expects. The layout here is read straight out of the vendored Pd 0.54
sources - each kind's ``*_save`` calls ``binbuf_addv`` with a format string, and
that string *is* the slot order - so the text produced here is byte-for-byte what
a Pd that saved the same box would have written.

Two rules make this safe:

* **the values come from the props table, not from here.** Which Pd argument a
  widget's ``value`` or ``min`` lands in is decided by :mod:`crude.ui.props`
  (``pd_name``), and ``PD_EMIT`` hands those in as a ``pd_name -> value`` map.
  This module only knows the *shape* of a box - the slot order, each slot's type,
  and the cosmetic defaults a freshly-dragged Pd box carries. That is the plan's
  one-table rule (risk 22): there is no second copy of "what a slider's arguments
  are" to keep in step. ``PD_GUI`` has no widget to ask, so a slot it names itself
  (a width, a height, a toggle's side) is addressed by the slot's own name - which
  is shape, this table's own business, and never a second opinion about a prop.
* **a kind with no template says so rather than guessing.** ``dropdown`` and
  ``knob`` are not vanilla Pd 0.54 objects (no ``*_save`` in ``src/``), so
  :func:`has_template` answers ``False`` and the caller takes the plan's degrade
  path: emit plain ``[r send]``/``[s recv]`` wiring and warn once, so the data path
  survives even though the box does not (risk 17).

Symbols here are written the way a saved file holds them: a blank name is
``empty``, and a ``$`` is escaped to ``\\$`` so Pd reads the name back unexpanded.
Numbers go through :func:`crude.pd.writer.fmt_number`, the same formatter every
other atom in a generated patch uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .writer import escape_atom, fmt_number

__all__ = ["GuiError", "PD_VERSION", "SLOT_TYPES", "arguments", "box_tag",
           "format_box", "has_template", "kinds", "sized", "slots", "unverified"]

#: The Pd these layouts were read from. A box written for another version may
#: order its slots differently, which is exactly the drift the stamp guards: a
#: mismatch is a loud error at emit time, not a patch that opens wrong.
PD_VERSION = "0.54"

#: how one slot is written into the box text.
#:   int    - a whole number, no decimal point
#:   float  - a number through ``fmt_number`` (6 significant digits, no exponent
#:            unless extreme)
#:   sym    - a symbol: blank becomes ``empty``, a ``$`` is escaped
#:   color  - a literal ``#rrggbb``; only ever a default here, colours are the
#:            widget's own business and are not Pd arguments ``PD_EMIT`` maps to
#:   count  - the length of a list prop (a radio's ``number`` of buttons)
SLOT_TYPES = ("int", "float", "sym", "color", "count")

#: "not a name", in the one spelling Pd uses for an empty send/receive/label slot.
EMPTY = "empty"


class GuiError(ValueError):
    """A box this table cannot lay out: no kind, or no template for it."""


@dataclass(frozen=True)
class Slot:
    """One argument of one box, in the position Pd's own save writes it.

    ``field`` is the ``pd_name`` in :mod:`crude.ui.props` that fills this slot -
    ``send``, ``value``, ``min`` and so on - or ``None`` for a slot a widget has
    no prop for (the label offset, the flash timing, the colours). A ``None`` field
    takes the default a freshly-dragged Pd box carries, which is what keeps a bare
    ``PD_GUI`` box looking like one out of Pd's menu; ``PD_GUI`` can still name one
    of them directly, by the slot's own ``name`` (see :func:`_given`).
    """

    name: str
    style: str
    default: Any
    field: Optional[str] = None

    def __post_init__(self) -> None:
        if self.style not in SLOT_TYPES:
            raise GuiError(f"a slot's style is one of {', '.join(SLOT_TYPES)}, "
                           f"not '{self.style}'")


def _slot(name: str, style: str, default: Any, field: str = None) -> Slot:
    return Slot(name, style, default, field)


# The shared tail every iemgui box saves last: label offset, font, and colours.
# Kept as a helper so a change to the cosmetic defaults is one edit, and so the
# ordering above each box's own slots reads as the distinct part of that box.
def _font_tail(ldx: int, ldy: int, fontsize: int, fstyle: int = 0,
               bcol: str = "#dfdfdf", fcol: str = "#000000",
               lcol: str = "#000000") -> Tuple[Slot, ...]:
    return (
        _slot("ldx", "int", ldx),
        _slot("ldy", "int", ldy),
        _slot("fontstyle", "int", fstyle),
        _slot("fontsize", "int", fontsize),
        _slot("bcol", "color", bcol),
        _slot("fcol", "color", fcol),
        _slot("lcol", "color", lcol),
    )


def _names() -> Tuple[Slot, Slot, Slot]:
    """The send / receive / label triple, in the order ``iemgui_save`` writes it."""
    return (_slot("send", "sym", "", "send"),
            _slot("recv", "sym", "", "recv"),
            _slot("label", "sym", "", "label"))


# --------------------------------------------------------------------------
# the templates: kind -> (the symbol Pd writes, its ordered slots)
#
# One entry per vanilla 0.54 GUI object, transcribed from that object's *_save.
# The kind names are the ones :data:`crude.ui.props.PD_BOXES` uses, so an emit
# that resolved ``pd_object`` to "hslider" finds its template here by that name.
# --------------------------------------------------------------------------

#: A toggle. ``size`` is the box's side; ``init`` is the value at load and
#: ``nonzero`` the number it sends when on (1 unless a script says otherwise).
_TGL = ("tgl", (
    _slot("size", "int", 15),
    _slot("initandsend", "int", 0),
    *_names(),
    *_font_tail(17, 7, 10),
    _slot("value", "float", 0.0, "value"),
    _slot("nonzero", "float", 1.0),
))

#: A momentary bang. ``flash``/``hang`` are the on/off pulse timing in ms.
_BNG = ("bng", (
    _slot("size", "int", 15),
    _slot("flash", "int", 250),
    _slot("hang", "int", 50),
    _slot("initandsend", "int", 0),
    *_names(),
    *_font_tail(17, 7, 10),
))

# A slider is the wide one: a range, a log flag, and the value at load. The
# orientation only changes which of w/h is the long axis and where the label
# sits, so vslider reuses the shape with its own defaults.
_HSL = ("hsl", (
    _slot("width", "int", 128),
    _slot("height", "int", 15),
    _slot("min", "float", 0.0, "min"),
    _slot("max", "float", 1.0, "max"),
    _slot("log", "int", 0, "log"),
    _slot("initandsend", "int", 0),
    *_names(),
    *_font_tail(-2, -8, 10),
    _slot("value", "float", 0.0, "value"),
    _slot("steady", "int", 1),
))

_VSL = ("vsl", (
    _slot("width", "int", 15),
    _slot("height", "int", 128),
    _slot("min", "float", 0.0, "min"),
    _slot("max", "float", 1.0, "max"),
    _slot("log", "int", 0, "log"),
    _slot("initandsend", "int", 0),
    *_names(),
    *_font_tail(0, -9, 10),
    _slot("value", "float", 0.0, "value"),
    _slot("steady", "int", 1),
))

# A number box: the range is unbounded unless the widget has one, so the
# defaults are Pd's own sentinels (-1e+37 / 1e+37), and ``log_height`` is the
# pixels-per-decade a log box uses (not a value a widget exposes).
_NBX = ("nbx", (
    _slot("width", "int", 5),
    _slot("height", "int", 14),
    _slot("min", "float", -1e37, "min"),
    _slot("max", "float", 1e37, "max"),
    _slot("log", "int", 0, "log"),
    _slot("initandsend", "int", 0),
    *_names(),
    *_font_tail(0, -8, 10),
    _slot("value", "float", 0.0, "value"),
    _slot("logheight", "int", 20),
))

# A vu meter only ever receives - it has no send slot at all - and it saves
# background and label colours, not a foreground one.
_VU = ("vu", (
    _slot("width", "int", 15),
    _slot("height", "int", 120),
    _slot("recv", "sym", "", "recv"),
    _slot("label", "sym", "", "label"),
    _slot("ldx", "int", -1),
    _slot("ldy", "int", -10),
    _slot("fontstyle", "int", 0),
    _slot("fontsize", "int", 12),
    _slot("bcol", "color", "#404040"),
    _slot("lcol", "color", "#000000"),
    _slot("scale", "int", 0),
    _slot("initandsend", "int", 0),
))

# A canvas: a label frame other boxes sit in. Its first argument is the border
# width, then the drawn width and height; like the vu it saves two colours.
_CNV = ("cnv", (
    _slot("border", "int", 1),
    _slot("width", "int", 100),
    _slot("height", "int", 60),
    *_names(),
    _slot("ldx", "int", 20),
    _slot("ldy", "int", 12),
    _slot("fontstyle", "int", 0),
    _slot("fontsize", "int", 12),
    _slot("bcol", "color", "#e0e0e0"),
    _slot("lcol", "color", "#404040"),
    _slot("initandsend", "int", 0),
))

# A radio's button count is the length of the labels list, so its ``number`` slot
# is a "count" over the ``labels`` prop rather than a stored default. The two
# orientations share everything but the tag and the label offset.
_RADIO = ("hradio", (
    _slot("size", "int", 15),
    _slot("change", "int", 0),
    _slot("initandsend", "int", 0),
    _slot("number", "count", 0, "labels"),
    *_names(),
    *_font_tail(-31, 9, 10),
    _slot("value", "float", 0.0, "value"),
))

_VRADIO = ("vradio", (
    _slot("size", "int", 15),
    _slot("change", "int", 0),
    _slot("initandsend", "int", 0),
    _slot("number", "count", 0, "labels"),
    *_names(),
    *_font_tail(16, 0, 10),
    _slot("value", "float", 0.0, "value"),
))

#: every kind this table can lay out, keyed by the ``PD_BOXES`` name an emit
#  resolves a widget to. The tag on the left of each template is what Pd writes.
_TEMPLATES: Dict[str, Tuple[str, Tuple[Slot, ...]]] = {
    "bng": _BNG,
    "cnv": _CNV,
    "hslider": _HSL,
    "nbx": _NBX,
    "radio": _RADIO,
    "tgl": _TGL,
    "vu": _VU,
    "vslider": _VSL,
    # reachable when a caller names an orientation, or the box by the symbol a
    # saved file holds, rather than by the name the props table uses:
    "hradio": _RADIO,
    "vradio": _VRADIO,
    "hsl": _HSL,
    "vsl": _VSL,
}

#: the names this table deliberately does *not* cover, and why. Kept explicit so
#: "no template" is a documented decision a caller can branch on, not a miss: a
#: script that emits one of these gets the degrade path, and the reason is here.
_UNVERIFIED: Dict[str, str] = {
    "dropdown": "not a vanilla Pd 0.54 object (no save template in src/)",
    "knob": "not a vanilla Pd 0.54 object (no save template in src/)",
}

#: the size slots of each box that are measured in pixels. Every box is, on both
#  axes, except a number box: ``my_numbox_save`` writes ``x_numwidth`` - a *digit*
#  count, floored at ``MINDIGITS`` - and the pixels it draws in only exist once Pd
#  has measured the font, so a widget's pixel width has no honest place to go and
#  converting one would mean inventing a metric we cannot observe. Everything the
#  other boxes hold in pixels, they hold in both axes.
_PIXEL_AXES: Dict[str, Tuple[str, ...]] = {"nbx": ("height",)}

#: the default, as the save routines of every other box are written.
_BOTH_AXES = ("width", "height")


def kinds() -> Tuple[str, ...]:
    """The box kinds a ``PD_EMIT`` can write as a real control, in table order.

    Includes the aliases, because they are the same box asked for by another name -
    ``hsl`` is what a saved file writes and what Pd's own object menu calls it.
    """
    return tuple(sorted(_TEMPLATES))


def unverified() -> Dict[str, str]:
    """The known GUI box names with no template, each with the reason."""
    return dict(_UNVERIFIED)


def has_template(kind: Any) -> bool:
    """Whether this table can lay out ``kind`` at all.

    The caller decides what to do with a ``False``: ``PD_EMIT`` falls back to bare
    ``[r]``/``[s]`` wiring and warns once. Empty and ``None`` are simply "no box",
    which the caller handles before it ever asks.
    """
    return str(kind or "") in _TEMPLATES


def box_tag(kind: Any) -> str:
    """The symbol Pd writes for this kind (``hslider`` -> ``hsl``)."""
    return _template(kind)[0]


def slots(kind: Any) -> Tuple[Slot, ...]:
    """The ordered argument slots of one box, for introspection and the CLI."""
    return _template(kind)[1]


def _template(kind: Any) -> Tuple[str, Tuple[Slot, ...]]:
    try:
        return _TEMPLATES[str(kind)]
    except KeyError:
        if str(kind) in _UNVERIFIED:
            raise GuiError(f"no Pd {PD_VERSION} template for '{kind}' "
                           f"({_UNVERIFIED[str(kind)]})") from None
        raise GuiError(
            f"'{kind}' is not a Pd GUI box this table knows "
            f"(it lays out: {', '.join(kinds())})") from None


def arguments(kind: Any, values: Optional[Mapping[str, Any]] = None
              ) -> List[str]:
    """The box's argument atoms, in Pd's order, as strings.

    ``values`` is the ``pd_name -> value`` map :func:`crude.ui.props.pd_arguments`
    produces for a widget (plus anything the caller overrides). A slot whose field
    is not in it, or which has no field at all, falls back to the default a freshly
    created Pd box carries - so a widget that only set a ``value`` still emits a
    complete, correctly-ordered ``[hslider]``.
    """
    held = values or {}
    out = [box_tag(kind)]
    for slot in slots(kind):
        out.append(_atom(slot, _given(slot, held)))
    return out


def sized(kind: Any, values: Optional[Mapping[str, Any]] = None,
          width: Any = None, height: Any = None) -> Dict[str, Any]:
    """``values``, with a pixel box folded into the size slots that box really has.

    A GL widget and a ``PD_GUI`` call both speak in pixels; a Pd box keeps its size
    in whatever its own object measures - ``[hsl]`` and ``[vu]`` a width and a
    height, ``[tgl]``/``[bng]``/a radio one square side, and an ``[nbx]`` a digit
    count plus a pixel height. So a pixel box is offered to the axes that can take
    it and the rest is left at the default a freshly-dragged box carries. That is
    deliberately not a complaint: our window is not a Pd canvas (the emitted box
    exists for portability, the GL widget stays the thing a person clicks), so a
    twin whose number box is a few characters narrower than the widget is cosmetic,
    while a guessed pixels-per-digit would be a fact about Pd's font we made up.

    Three rules let a caller hand over a widget's raw box without checking it first:

    * a slot the ``values`` map already names is left alone, so an explicit
      ``["width", 8]`` - in the slot's own unit - wins over a geometry guess;
    * ``0`` means never placed, which is skipped, while a negative or non-numeric
      dimension is a bug and raises;
    * a square box takes the smaller of the two axes, so a wide toggle does not
      emit a 200-pixel ``[tgl]``.
    """
    by_name = {slot.name: slot for slot in slots(kind)}
    held = dict(values or {})
    given = {"width": width, "height": height}

    if "size" in by_name and "width" not in by_name:
        # one square side: the smallest axis the caller gave still fits inside the
        # box it was drawn in, which a toggle's single argument has to do for both
        sides = [axis for axis in (_dimension(kind, name, given[name])
                                   for name in _BOTH_AXES) if axis]
        if sides and "size" not in held:
            held["size"] = min(sides)
        return held

    axes = _PIXEL_AXES.get(box_tag(kind), _BOTH_AXES)
    for name in _BOTH_AXES:
        amount = _dimension(kind, name, given[name])
        if not amount or name not in by_name or name not in axes:
            continue
        held.setdefault(name, amount)
    return held


def format_box(kind: Any, values: Optional[Mapping[str, Any]] = None) -> str:
    """The object text of one box - the part after ``#X obj x y``.

    The result is already in file form, so it goes into a document through
    :meth:`PatchDoc.add_gui_box <crude.pd.model.PatchDoc.add_gui_box>`, which writes
    a verbatim box. Handing it to ``add_object`` instead would re-run the writer's
    object-text escaping on atoms that are escaped already (a ``\\$0`` would double
    up into a literal backslash plus an expansion). Kept as one space-joined string
    because that is exactly what a golden line is.
    """
    return " ".join(arguments(kind, values))


# --------------------------------------------------------------------------
# formatting one slot
# --------------------------------------------------------------------------

def _atom(slot: Slot, raw: Any) -> str:
    if slot.style == "count":
        return str(_count(raw, slot.default))
    if raw is None or (slot.style == "sym" and str(raw) == ""):
        return _default(slot)
    return _render(slot, raw)


def _given(slot: Slot, held: Mapping[str, Any]) -> Any:
    """What a slot should hold: the prop that maps onto it, else the caller's own
    naming of the slot, else nothing.

    The field comes first, because which widget prop lands in which Pd argument is
    :mod:`crude.ui.props`' decision and the one-table rule says it wins. The slot's
    own name is a second door, and only ``PD_GUI`` uses it: that call has no widget
    to ask, so it addresses the *shape* directly, which is this table's own
    business. The two doors cannot disagree, because every ``pd_name`` the props
    table gives that also names a slot here (``value``, ``min``, ``max``, ``log``,
    ``send``, ``recv``, ``label``) means the same one thing in both, and the words
    that do not (a widget's ``size`` is a skin word, and carries no ``pd_name`` at
    all) are not slot names here.
    """
    if slot.field is not None and slot.field in held:
        return held[slot.field]
    return held.get(slot.name)


def _dimension(kind: Any, axis: str, value: Any) -> Optional[int]:
    """A pixel dimension as the whole number of them Pd writes, ``None`` for none.

    ``0`` is not a size but the absence of one - a widget that was never placed,
    whose box should stay at the default - so it answers ``None`` rather than
    emitting a box no canvas can draw. A negative or a non-number is a caller bug
    and says so with the kind and the axis in the sentence.
    """
    if value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise GuiError(f"'{kind}': a box's {axis} must be a number of pixels, "
                       f"not {value!r}") from None
    if amount < 0:
        raise GuiError(f"'{kind}': a box's {axis} cannot be negative ({value!r})")
    whole = int(round(amount))
    return whole if whole > 0 else None


def _render(slot: Slot, raw: Any) -> str:
    if slot.style == "sym":
        return _symbol(raw)
    if slot.style == "color":
        return _color(raw)
    if slot.style == "int":
        return str(_whole(slot, raw))
    # float goes through the patch's own number formatter, so 440 stays 440 and
    # the 6-significant-digit rule is shared with every other atom we write.
    return _number(slot, raw)


def _whole(slot: Slot, raw: Any) -> int:
    """A number slot's value as a whole number, or an error that names the slot.

    Both numeric branches need this because a value can now arrive two ways: the
    props table's coerced float, or a word a script put in a ``pd_name`` of its own
    (``PROP_DEFINE("stepper", "size", "string", "md", ..., "size")`` is legal and
    would otherwise raise a Python ``ValueError`` from the middle of a file write).
    A ``GuiError`` says which slot and with what, which is the answer a script can
    act on.
    """
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        raise GuiError(f"a Pd '{slot.name}' argument wants a whole number, "
                       f"not {raw!r}") from None


def _number(slot: Slot, raw: Any) -> str:
    """A number slot's value through the patch's own formatter, or a named error."""
    try:
        return fmt_number(raw)
    except (TypeError, ValueError):
        raise GuiError(f"a Pd '{slot.name}' argument wants a number, "
                       f"not {raw!r}") from None


def _default(slot: Slot) -> str:
    value = slot.default
    if slot.style == "sym":
        return _symbol(value)
    if slot.style == "color":
        # a colour default only reaches here (colour slots carry no field), so the
        # #rrggbb check lives on this path too - a malformed authored default is a
        # template bug, caught before it can be written into a patch.
        return _color(value)
    if slot.style == "int":
        return str(int(value))
    if slot.style == "count":
        return str(int(value))
    return fmt_number(value)


def _symbol(value: Any) -> str:
    """A send/receive/label as Pd writes it: blank is ``empty``, a ``$`` is escaped.

    ``escape_atom`` already protects ``\\ ; , $``; a space would split the atom in
    the box text, so it is escaped too, which is how Pd holds a label like ``my
    label`` in a single slot.
    """
    text = str(value if value is not None else "")
    if not text:
        return EMPTY
    return escape_atom(text).replace(" ", "\\ ")


def _color(value: Any) -> str:
    """A ``#rrggbb`` colour, passed through; anything else is a template bug."""
    text = str(value)
    if not (len(text) == 7 and text.startswith("#")):
        raise GuiError(f"a colour slot wants '#rrggbb', got '{text}'")
    return text


def _count(value: Any, default: Any) -> int:
    """How many buttons a radio has: the length of its labels, else a count.

    The props table hands over the list and ``PD_GUI`` can hand over a number, which
    is why both are accepted. Anything else is refused rather than quietly replaced
    by the default, because a radio whose button count was guessed wrong has slots
    that do not line up with the labels Pd will read.
    """
    if value is None:
        return int(default)
    if isinstance(value, (list, tuple)):
        return len(value)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise GuiError(f"a Pd radio wants its button count as a number or as "
                       f"labels, not {value!r}") from None
