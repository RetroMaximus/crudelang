"""One table per kind: what a widget's props are, and what a wrong one costs.

A widget's properties are the only surface a script has, so the description of
them has to be in one place or the four things that read it disagree: ``SET``
validation, the tooltip a hover shows, ``crude widgets --json`` /
``docs/widgets.md``, and the argument mapping ``PD_EMIT`` uses to write a real
Pd box. The plan's line is that all four read the same table, which is what this
module is: the table, and the small number of operations on it.

Five things it decides, in the order they matter:

* **What a value means.** ``value`` on a slider is a float between that widget's
  own ``min`` and ``max``, on a toggle it is 0 or 1, on a dropdown it is an
  *index* into ``labels``. That is per-kind information, so it lives per-kind, and
  a variant that only changes the defaults of a catalog kind says so by naming the
  rows it cares about.
* **What a wrong value does.** A number outside the range is *clamped*, because
  that is what the Pd box does and a handler that scales a level past the end is
  routine, not a mistake. A value of the wrong type is an error, because
  ``SET(w, "max", "wide")`` has no sane reading. Both go through
  :meth:`Prop.settle`, and a constructor and ``SET`` go through that one call,
  which is the plan's constructor-equals-setter rule as a consequence of there
  being one door rather than two that have to be kept the same.
* **What the script may not touch.** ``hovered``, ``pressed``, ``focused`` and
  ``selected`` belong to the pointer and the keyboard. They are rows rather than
  separate widget fields so that they are documented, dumped and hashed with
  everything else - a hover changes what a painter draws, so a paint cache keyed
  only on the props a script can write would replay an unhovered widget over a
  hovered one - and marked so ``SET`` refuses them instead of letting a script
  write a state the input system overrules next frame.
* **What a kind says for itself.** Its base, the Pd box it twins to, which of the
  value rows it has at all, and the order its constructor's arguments come in.

Nothing here knows about the interpreter: the bindings turn a script's ``Number``
and ``String`` into Python values before this layer sees them, which is what
keeps the whole of :mod:`crude.ui` usable - and testable - with no language at
all attached, and no widget built yet.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from typing import (Any, Dict, Iterable, List, Mapping, Optional, Sequence,
                    Tuple)

from .base import UIError
from .glyphs import icon_names
from .skin import SIZES, role_names

__all__ = ["KINDS", "PD_BOXES", "SCHEMA", "State", "TYPES", "Kind", "Prop",
           "PropError", "catalog_names", "define", "define_prop", "digest",
           "document", "exists", "get_kind", "has", "hint_for", "pd_arguments",
           "pd_object_for", "prop_names", "props_of", "registered_names",
           "reset", "settle", "settled", "states", "value_of"]

#: the four types a script may name in ``PROP_DEFINE``. Anything the language can
#: already hold, so no coercion here has to guess.
TYPES: Tuple[str, ...] = ("number", "string", "bool", "list")

#: the Pd GUI boxes this layer knows how to make a twin for. A prop whose
#: ``pd_name`` is empty has no box argument and is drawn by the widget alone.
PD_BOXES: Tuple[str, ...] = ("bng", "cnv", "dropdown", "hslider", "nbx", "radio",
                            "tgl", "vu", "vslider")

_TRUE_WORDS = ("true", "yes", "on", "1")
_FALSE_WORDS = ("false", "no", "off", "0")


class PropError(UIError):
    """A prop that does not exist, or a value that cannot mean anything.

    A subclass of :class:`~crude.ui.base.UIError` so a caller that only knows the
    UI layer's one error type still catches it, while the bindings can tell the
    two apart when they decide what to call a script error.
    """


def _is_number(value: Any) -> bool:
    """A number that is not a bool.

    ``True`` is a 1 to Python, so without this a ``bool`` would satisfy every
    numeric test in the file. Each type's branch in :meth:`Prop.coerce` still
    accepts a bool when it means one - the point of separating them is that the
    row decides what a bool is worth, not whichever test happened to come first.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _name(value: Any) -> str:
    """A prop or kind name as the table stores it.

    The rest of :mod:`crude.ui` is forgiving about how a script spells a word
    - ``SET(w, "Label", "x")`` is the same row as ``label`` - and a table that
    was case-sensitive would have its own set of typos to report.
    """
    return str(value if value is not None else "").strip().lower()


def _words(choices: Sequence[str]) -> str:
    """A choice list as an error message reads it, empty string and all."""
    return ", ".join("''" if not word else f"'{word}'" for word in choices)


class Prop:
    """One row of one kind's table.

    Frozen, and the table it belongs to is never edited in place: a variant kind
    gets a copy with different rows, so a project's ``bpm_slider`` cannot change
    what every ``slider`` in every other project means.
    """

    __slots__ = ("kind", "name", "type", "default", "minimum", "maximum",
                 "bounds", "pd_name", "hint", "choices", "readonly",
                 "inherited")

    def __init__(self, kind: str, name: str, type: str = "number",
                 default: Any = None, minimum: Optional[float] = None,
                 maximum: Optional[float] = None, pd_name: str = "",
                 hint: str = "", bounds: Sequence[str] = ("", ""),
                 choices: Sequence[str] = (), readonly: bool = False,
                 inherited: str = "") -> None:
        type = _name(type) or "number"
        name = _name(name)
        if type not in TYPES:
            raise PropError(f"'{kind}.{name}' is a '{type}' prop, and a prop is "
                            f"{' or '.join(TYPES)}")
        if not name:
            raise PropError(f"a prop of '{kind}' has no name")
        if minimum is not None and maximum is not None:
            if float(minimum) > float(maximum):
                raise PropError(f"'{kind}.{name}' has an inverted range: min "
                                f"{minimum} is above max {maximum}")
        ranged = (minimum is not None or maximum is not None
                  or any(str(word) for word in (bounds or ("", ""))))
        if ranged and type != "number":
            raise PropError(f"'{kind}.{name}' is a {type} prop, so a min or max "
                            "does not mean anything")
        bounds = tuple(str(word) for word in (bounds or ("", "")))[:2]
        if len(bounds) < 2:
            bounds = (bounds[0] if bounds else "", "")

        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "minimum", None if minimum is None
                           else float(minimum))
        object.__setattr__(self, "maximum", None if maximum is None
                           else float(maximum))
        object.__setattr__(self, "bounds", (bounds[0], bounds[1]))
        object.__setattr__(self, "pd_name", str(pd_name or ""))
        object.__setattr__(self, "hint", str(hint or ""))
        object.__setattr__(self, "choices", tuple(choices))
        object.__setattr__(self, "readonly", bool(readonly))
        object.__setattr__(self, "inherited", str(inherited or ""))
        # the default is stored as it would arrive from SET, so a widget that was
        # never given a value is not a special case anywhere else
        object.__setattr__(self, "default", self.settle(default, default=True))

    def __setattr__(self, name: str, value: Any) -> None:
        """Refused: a row is the table, and the table is not edited in place.

        A variant kind gets a :meth:`copy` with different fields, so a project's
        ``bpm_slider`` cannot move ``slider``'s defaults for everyone else by
        writing to a row it was only handed. An ``AttributeError`` would be the
        frozen-dataclass answer, but the useful one says what to do instead.
        """
        raise PropError(f"'{self.kind}.{self.name}' is a row of the table; "
                        f"change it with copy({name}=...) rather than writing it")

    # -- the one door ----------------------------------------------------
    def copy(self, **changes: Any) -> "Prop":
        """This row with a few fields swapped, run through validation again.

        Every kind owns its own copies rather than sharing the template: a
        project's ``bpm_slider`` restating ``value`` must not be able to move what
        ``slider`` means for everyone else, and a variant that only changes a
        default still inherits the hint and the Pd name it was built from.
        """
        held = {slot: getattr(self, slot) for slot in self.__slots__}
        for key in changes:
            if key not in held:
                raise PropError(f"a prop has no '{key}' to change (it has: "
                                f"{', '.join(self.__slots__)})")
        held.update(changes)
        return Prop(**held)

    def coerce(self, value: Any) -> Any:
        """The value as this prop's type, or a :class:`PropError` that says both.

        Deliberately narrow. A number accepts a numeric string because a text
        field hands over text and a list of Pd floats arrives as numbers; a
        string accepts a number because ``SET(w, "label", 440)`` is a musician
        writing a label. Anything that needs an opinion - a list for a number, a
        function for anything - is refused rather than guessed at.
        """
        if self.type == "number":
            if _is_number(value):
                return float(value)
            if isinstance(value, bool):
                return 1.0 if value else 0.0
            if isinstance(value, str):
                try:
                    return float(value.strip())
                except ValueError:
                    raise self._bad(value) from None
            raise self._bad(value)
        if self.type == "string":
            if isinstance(value, str):
                return value
            if _is_number(value):
                # 440.0 labels itself "440", not "440.0", which is the difference
                # between a box that reads like a patch and one that does not
                return f"{float(value):g}"
            if isinstance(value, bool):
                return "true" if value else "false"
            raise self._bad(value)
        if self.type == "bool":
            if isinstance(value, bool):
                return value
            if _is_number(value):
                return float(value) != 0.0
            if isinstance(value, str):
                said = value.strip().lower()
                if said in _TRUE_WORDS:
                    return True
                if said in _FALSE_WORDS:
                    return False
            raise self._bad(value)
        if isinstance(value, (list, tuple)):
            return list(value)
        raise self._bad(value)

    def _empty(self) -> Any:
        """What 'unset' means for this type.

        An unset prop is a value of its type, not ``None``: a list is empty rather
        than missing, so nothing downstream has to test for both, and the list is
        built here rather than shared out of a table.
        """
        if self.type == "number":
            return 0.0
        if self.type == "string":
            return ""
        if self.type == "bool":
            return False
        return []

    def _bound(self, row: str, values: Optional[Mapping[str, Any]],
               high: bool) -> Optional[float]:
        """One end of a range that a *sibling prop* holds, or ``None``.

        This is what makes a slider's ``value`` mean 20..300 rather than 0..1: the
        range is two props on the same widget, so the row points at them by name
        and the limit is read from whatever the widget currently holds. A list row
        named here bounds by its length, which is how a dropdown's value is an
        index into ``labels`` without a second rule for it.
        """
        if not row or values is None:
            return None
        found = values.get(row)
        if isinstance(found, State):
            found = found.value
        if isinstance(found, (list, tuple)):
            return float(max(0, len(found) - 1)) if high else 0.0
        if _is_number(found):
            return float(found)
        return None

    def settle(self, value: Any, default: bool = False,
               values: Optional[Mapping[str, Any]] = None) -> Any:
        """Coerce, then bring a number inside the range this prop declares.

        Clamping rather than raising is the Pd box's behaviour, and the one that
        lets a handler write ``value * 1.5`` to a meter without a range check.
        The caller sees the settled value back, so an overshoot is never lost -
        ``SET`` returns it and ``GET`` reads it.

        ``values`` is the rest of the widget's props, which a bounded row reads
        its range from - see :meth:`_bound`. Without it only the limits written
        in the table apply, which is exactly right for a default.

        ``default=True`` is the constructor's path, where nothing can be out of
        range and a bad ``choices`` word is the table's mistake, not the script's.
        """
        if value is None:
            found = self._empty()
        else:
            found = self.coerce(value)
        if self.type == "number":
            low = self._bound(self.bounds[0], values, False)
            high = self._bound(self.bounds[1], values, True)
            low = self.minimum if low is None else low
            high = self.maximum if high is None else high
            if low is not None and found < low:
                found = low
            if high is not None and found > high:
                found = high
        if self.choices and found not in self.choices:
            if default:
                raise PropError(f"'{self.kind}.{self.name}' cannot default to "
                                f"{_shown(found)}; it is one of "
                                f"{_words(self.choices)}")
            raise PropError(f"{_shown(found)} is not a {self.kind}.{self.name}; "
                            f"it is one of {_words(self.choices)}")
        return found

    def _bad(self, value: Any) -> PropError:
        return PropError(f"'{self.kind}.{self.name}' is a {self.type}, not a "
                         f"{type(value).__name__} ({_shown(value)})")

    def would_move(self, current: Any, value: Any) -> bool:
        """Whether settling ``value`` would leave this prop where it is.

        ``LINK_VALUE`` needs the answer without writing, and a guard that writes
        and re-reads is how a two-way link becomes an echo.
        """
        return self.settle(value) != self.settle(current)

    # -- describing itself ----------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "type": self.type,
                               "default": self.default,
                               "read_only": self.readonly}
        if self.minimum is not None:
            out["min"] = self.minimum
        if self.maximum is not None:
            out["max"] = self.maximum
        if any(self.bounds):
            out["bounds"] = [word or None for word in self.bounds]
        if self.pd_name:
            out["pd_name"] = self.pd_name
        if self.hint:
            out["hint"] = self.hint
        if self.choices:
            out["choices"] = list(self.choices)
        if self.inherited:
            out["inherited"] = self.inherited
        return out

    def __str__(self) -> str:
        said = f"{self.kind}.{self.name}: {self.type} = {_shown(self.default)}"
        if self.minimum is not None or self.maximum is not None:
            said += (f" [{self.minimum if self.minimum is not None else '-'}, "
                     f"{self.maximum if self.maximum is not None else '-'}]")
        elif any(self.bounds):
            said += (f" [{self.bounds[0] or '-'}, {self.bounds[1] or '-'}]")
        if self.pd_name:
            said += f" -> Pd '{self.pd_name}'"
        if self.readonly:
            said += " (read only)"
        return said

    def __repr__(self) -> str:
        return f"<Prop {self.kind}.{self.name} {self.type}>"


def _shown(value: Any) -> str:
    """A value in the words a report has room for."""
    if isinstance(value, (list, tuple)):
        count = len(value)
        return f"[{count}]" if count else "[]"
    if isinstance(value, str):
        return f"'{value}'" if len(value) <= 24 else f"'{value[:21]}...'"
    return str(value)


@dataclass
class State:
    """The mutable half of one prop: what it holds, and whether it was written.

    The table is shared and frozen; the value is per-widget, so this is the only
    thing a widget owns about its properties.
    """

    value: Any
    written: bool = False


class Kind:
    """A widget kind: its prop table, its base, and the Pd box it twins to.

    ``props`` is the merged view a script sees - the rows inherited from ``base``
    with this kind's own on top of them - and ``declared`` is the subset this kind
    stated, which is what lets ``define_prop`` tell a child overriding its
    parent's ``min`` from a child saying ``min`` twice.
    """

    __slots__ = ("name", "base", "pd_object", "hint", "props", "declared",
                 "args", "script")

    def __init__(self, name: str, base: str = "", pd_object: str = "",
                 hint: str = "", props: Optional[Mapping[str, Prop]] = None,
                 declared: Optional[Sequence[str]] = None,
                 args: Sequence[str] = (), script: bool = False) -> None:
        self.name = name
        self.base = base
        self.pd_object = pd_object
        self.hint = hint
        self.props: Dict[str, Prop] = dict(props or {})
        self.declared: List[str] = list(declared or ())
        self.args: Tuple[str, ...] = tuple(args)
        self.script = script

    # -- asking ----------------------------------------------------------
    def has(self, name: Any) -> bool:
        return _name(name) in self.props

    def get(self, name: Any) -> Prop:
        try:
            return self.props[_name(name)]
        except KeyError:
            raise PropError(self._no_such(name)) from None

    def _no_such(self, name: Any) -> str:
        """The plan's "closest valid props" line, for a script that mis-typed one.

        The whole table is listed because it is short and because a range control
        has rows that differ by two letters - a person who wrote ``val`` wants
        ``value`` and ``labels`` next to ``label`` is a person who wrote the wrong
        one for their kind. The near-miss is named first as well, which is the part
        that saves a read of the list: ``difflib`` scores by edit distance, so
        ``vlue`` says ``value`` and ``minimun`` says ``minimum``.
        """
        near = sorted(self.props)
        said = f"a '{self.name}' has no prop '{name}' (it has: {', '.join(near)})"
        asked = _name(name)
        if asked:
            for found in difflib.get_close_matches(asked, near, n=1):
                said += f" - did you mean '{found}'?"
        return said

    def owns(self, name: Any) -> bool:
        """Whether this kind stated the row itself rather than inheriting it."""
        return _name(name) in self.declared

    def defaults(self) -> Dict[str, Any]:
        """Every prop at its default, which is what a constructor starts from."""
        return {name: prop.default for name, prop in self.props.items()}

    def settable(self, name: Any) -> Prop:
        """:meth:`get`, plus the refusal a script's ``SET`` should get."""
        prop = self.get(name)
        if prop.readonly:
            raise PropError(f"'{self.name}.{prop.name}' is written by the "
                            "pointer or the keyboard, not by a script")
        return prop

    def states(self, values: Optional[Mapping[str, Any]] = None) -> Dict[str,
                                                                          State]:
        """The mutable half: every row at its default, or at what was written.

        A widget holds one of these, and it is the only per-widget state the prop
        layer has, which is why the table above can stay shared and frozen. A row
        the kind does not have - or one the pointer owns - is refused here rather
        than dropped, so a constructor reports the same two mistakes a ``SET``
        does, and a script cannot arrive at a hovered widget by naming the row in
        the arguments it built it with.

        Its own output is therefore not a valid input to it: the four state rows
        come in through the door the input system uses, and :func:`digest` and
        :func:`pd_arguments` read a finished map as it stands.
        """
        given = {_name(key): value for key, value in (values or {}).items()}
        out = {name: State(prop.default) for name, prop in self.props.items()}
        for name in self._ordered(given):
            prop = self.settable(name)
            out[prop.name] = State(prop.settle(given[name], values=out), True)
        return out

    def _ordered(self, names: Iterable[str]) -> List[str]:
        """The rows written, with the ones other rows take their range from first.

        A slider built with ``value=120, min=20, max=300`` has to settle ``min``
        and ``max`` before ``value``, or the value is clamped to the range the
        *defaults* declared and the widget arrives holding 1.0 - an ordering bug
        that only shows up when a constructor states its props in the order that
        reads best, which is the order the plan's examples happen to use.
        """
        bounds = set()
        for prop in self.props.values():
            bounds.update(word for word in prop.bounds if word)
        wrote = [_name(name) for name in names]
        early = [name for name in wrote if name in bounds]
        return early + [name for name in wrote if name not in early]

    def settle(self, name: Any, value: Any,
               states: Optional[Mapping[str, Any]] = None) -> Any:
        """The value this prop would hold if ``name`` were set to ``value``.

        ``states`` may be a widget's own map, a settled dict, or nothing; a
        bounded row reads its limits from whatever the siblings currently hold.
        """
        held = {key: (found.value if isinstance(found, State) else found)
                for key, found in (states or {}).items()}
        return self.settable(name).settle(value, values=held or None)

    def chain(self) -> List[str]:
        """This kind, then its base, then that base - the order a lookup walks."""
        seen, out, name = set(), [], self.name
        while name and name not in seen:
            seen.add(name)
            out.append(name)
            kind = KINDS.get(name)
            name = kind.base if kind else ""
        return out

    # -- describing itself -----------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.name, "base": self.base or None,
                "pd_object": self.pd_object or None, "hint": self.hint,
                "script": self.script, "args": list(self.args),
                # what a variant changed, as well as what it ended up with: a dump
                # of the merged table alone cannot tell a reader which rows a
                # project's own kind restated on top of its base
                "declared": list(self.declared),
                "props": [prop.as_dict() for prop in self.props.values()]}

    def __str__(self) -> str:
        said = f"Kind({self.name}, {len(self.props)} props"
        if self.base:
            said += f", from {self.base}"
        if self.pd_object:
            said += f", emits [{self.pd_object}]"
        if self.script:
            said += ", defined by script"
        return said + ")"

    def __repr__(self) -> str:
        return f"<Kind {self.name}>"


# --------------------------------------------------------------------------
# the rows every widget has, whatever it is
# --------------------------------------------------------------------------

def _row(name: str, type: str = "number", default: Any = None, hint: str = "",
         pd: str = "", rng: Optional[Sequence[Any]] = None,
         bounds: Sequence[str] = ("", ""), choices: Sequence[str] = (),
         ro: bool = False) -> Prop:
    """One row of the tables below, before it knows which kind owns it.

    Named rather than positional on purpose. ``Prop`` takes a range before a Pd
    name because that is the order ``PROP_DEFINE`` spells it, and a table written
    with bare commas mis-times one field per row - a hint that lands in ``pd_name``
    is a tooltip that turns into a Pd argument, quietly, for the whole catalog.
    """
    low, high = rng if rng is not None else (None, None)
    return Prop("*", name, type, default, low, high, pd, hint, bounds, choices,
                ro)


#: the plumbing every widget has, whatever it is. ``value`` and the range are
#  deliberately missing: what a value *means* is the one thing a knob, a label
#  and a panel cannot agree on, so each of them says it for itself below.
_COMMON: Tuple[Prop, ...] = (
    _row("label", "string", "",
         "Text drawn on or beside the widget; also the Pd box's own label.",
         pd="label"),
    _row("send", "string", "",
         "Pd symbol the widget sends its value to when it changes.", pd="send"),
    _row("recv", "string", "",
         "Pd symbol the widget listens to, so a patch can move it back.",
         pd="recv"),
    _row("pd_object", "string", "",
         "Which Pd box to write as a twin; '' means draw only, and PD_EMIT "
         "resolves it before it lays out any arguments.",
         choices=("", *PD_BOXES)),
    _row("role", "string", "surface",
         "The skin role this widget paints itself with - 'danger', 'track', "
         "'panel'. A style is a role, never a colour.", choices=role_names()),
    _row("size", "string", "md",
         "The skin's size axis; 'sm' through 'lg' scale padding, fonts and "
         "corners together.", choices=tuple(sorted(SIZES))),
    _row("tooltip", "string", "",
         "Shown when the pointer rests on the widget."),
    _row("visible", "bool", True,
         "Off means not drawn and not hit-tested; the value is still kept."),
    _row("enabled", "bool", True,
         "Off means drawn disabled: no pointer, no keyboard, no change."),
    _row("glyph", "string", "",
         "The icon drawn in the widget, overriding the role's own mark.",
         choices=("", *icon_names())),
    _row("glyph_on", "string", "",
         "The icon for the on value, so TOGGLE('Run') shows play and pause.",
         choices=("", *icon_names())),
    _row("glyph_off", "string", "",
         "The icon for the off value.", choices=("", *icon_names())),
    # the states the input system owns. They are in the table so they are
    # documented and readable, and marked so a script cannot write one and then
    # wonder why it moved back next frame.
    _row("hovered", "bool", False, "The pointer is over it.", ro=True),
    _row("pressed", "bool", False, "A button is held down on it.", ro=True),
    _row("focused", "bool", False, "The keyboard is aimed at it.", ro=True),
    _row("selected", "bool", False, "It is the one picked in its group.",
         ro=True),
)

#: the rows about a value, written once and copied onto each kind that has one,
#  because a slider and a number box agree about min/max/step and disagree only
#  about the defaults. ``value`` names its range by row rather than holding one:
#  a slider's is 20..300 because two of its own props say so.
_VALUE: Dict[str, Prop] = {
    "value": _row("value", "number", 0.0,
                  "What the widget holds - a position, a level, an index.",
                  pd="value", bounds=("min", "max")),
    "min": _row("min", "number", 0.0,
                "The lowest value the widget will take or show.", pd="min"),
    "max": _row("max", "number", 1.0,
                "The highest value the widget will take or show.", pd="max"),
    "step": _row("step", "number", 0.0,
                 "How far one click or one arrow key moves; 0 is continuous.",
                 pd="step", rng=(0.0, None)),
    "log": _row("log", "bool", False,
                "Scale the range logarithmically, the way a filter knob wants.",
                pd="log"),
    "ticks": _row("ticks", "number", 0.0,
                  "Marks drawn along the range; 0 takes the default few.",
                  rng=(0.0, None)),
    "editable": _row("editable", "bool", True,
                     "Whether the value can be typed into it or only dragged."),
}

#: the rows about a list of choices - the two kinds whose value is an index
#  rather than a position, and the one that lays its rows out in a grid.
_ITEMS: Dict[str, Prop] = {
    "labels": _row("labels", "list", (),
                   "One entry per row, as text or as a Pd symbol; the value is "
                   "an index into this.", pd="labels"),
    "columns": _row("columns", "number", 0.0,
                    "How many columns the rows are laid out in; 0 is one row per "
                    "entry.", pd="columns", rng=(0.0, None)),
}

#: the rows only the synth widgets ask for. None of them has a ``pd`` name: a
#  ``[vline]`` has no attack, a ``[bng]`` has no bins, and a prop the box cannot
#  store must not be smuggled into its arguments, so these are drawn-and-read
#  rows - visible to ``GET``/``SET``/the painter and invisible to ``PD_EMIT``,
#  which skips a row with no ``pd_name`` rather than inventing an argument for
#  it. They carry no range either except where a real limit exists - MIDI is
#  0..127 by specification, while a ``points`` count of 0 is the face's own
#  "plot what you are given", not a clamp waiting to catch a mistake.
_SYNTH: Dict[str, Prop] = {
    "data": _row("data", "list", (),
                 "The samples or magnitudes the face plots, oldest first; empty "
                 "draws the flat line of silence."),
    "points": _row("points", "number", 0.0,
                   "How many samples the waveform draws; 0 is as many as "
                   "``data`` holds."),
    "bins": _row("bins", "number", 0.0,
                 "How many bars the spectrum draws; 0 is as many as ``data`` "
                 "holds."),
    "attack": _row("attack", "number", 0.0,
                   "The envelope's attack, in the patch's own units of time.",
                   rng=(0.0, None)),
    "decay": _row("decay", "number", 0.0,
                  "The envelope's decay, in the same units as attack.",
                  rng=(0.0, None)),
    "sustain": _row("sustain", "number", 0.0,
                    "The level the envelope holds between decay and release.",
                    rng=(0.0, 1.0)),
    "release": _row("release", "number", 0.0,
                    "The envelope's release, in the same units as attack.",
                    rng=(0.0, None)),
    "tension": _row("tension", "number", 0.0,
                    "How hard the curve bends: -1 one way, 1 the other, 0 a "
                    "straight line.", rng=(-1.0, 1.0)),
    "x": _row("x", "number", 0.5,
              "The pad's horizontal output, 0 at the left edge and 1 at the "
              "right.", rng=(0.0, 1.0)),
    "y": _row("y", "number", 0.5,
              "The pad's vertical output, 0 at the bottom edge and 1 at the "
              "top.", rng=(0.0, 1.0)),
    "low": _row("low", "number", 60.0,
                "The MIDI number of the keybed's lowest key.",
                rng=(0.0, 127.0)),
    "octaves": _row("octaves", "number", 2.0,
                    "How many octaves of keys the bed holds.",
                    rng=(1.0, None)),
    "channel": _row("channel", "number", 1.0,
                    "The MIDI channel the keybed plays on; the script that "
                    "sends passes it to ``PD_MIDI_NOTEON``.", rng=(1.0, 16.0)),
    "velocity": _row("velocity", "number", 100.0,
                     "How hard a struck key lands, as MIDI velocity.",
                     rng=(0.0, 127.0)),
}

#: every row a kind may ask for by name, in one place, so asking for a row that
#  is not there is an error that names the rows that are
_TEMPLATE: Dict[str, Prop] = {**_VALUE, **_ITEMS, **_SYNTH}


# --------------------------------------------------------------------------
# the registry: the catalog, then whatever the running program defines
# --------------------------------------------------------------------------

#: every kind there is, in the order it was declared: the catalog's own first,
#  then whatever the running program defined. One dict, one order, so a dump of
#  the table reads the way the table was written.
KINDS: Dict[str, Kind] = {}

#: the names that came with the package. ``reset()`` keeps these and drops
#  everything else, because the plan's rule is that a registered kind lives for
#  one program run only.
_CATALOG: List[str] = []

#: what a dump of this table calls itself, so a reader can tell the schema from a
#  draft of it
SCHEMA = "crude/widgets@1"

#: "not given", as distinct from ``None``, which is a script saying "unset it"
_OMITTED = object()


def _keep(given: Any, was: Any) -> Any:
    """A field a declaration left out, taken from the row it is restating."""
    return was if given is None else given


def get_kind(value: Any) -> Kind:
    """The kind behind a name, a :class:`Kind`, or a widget that has one.

    Every function here takes the kind in any of those three forms, so the
    bindings can hand over the widget a script named instead of unwrapping it at
    each call - and so ``crude/ui/widget.py`` never has to re-expose the table.
    """
    if isinstance(value, Kind):
        return value
    held = getattr(value, "kind", None)
    if isinstance(held, Kind):
        return held
    name = _name(held if held is not None else value)
    try:
        return KINDS[name]
    except KeyError:
        said = f"'{value}' is not a widget kind (there are: {', '.join(KINDS)})"
        if name:
            near = difflib.get_close_matches(name, list(KINDS), n=1)
            if near:
                said += f" - did you mean '{near[0]}'?"
        raise PropError(said) from None


def exists(value: Any) -> bool:
    """Whether a kind means anything, without raising.

    ``None`` and a name that is not there both answer ``False``; a widget whose own
    kind has been reset out from under it does too, rather than raising in the
    middle of a draw pass that is already winding down.
    """
    try:
        get_kind(value)
    except PropError:
        return False
    return True


def has(kind: Any, name: Any) -> bool:
    """``WIDGET_HAS(w, "prop")``.

    A kind that does not exist is still an error, so a typo in the middle of an
    ``IF`` cannot answer "no such prop" for a question nobody meant to ask.
    """
    return get_kind(kind).has(name)


def props_of(kind: Any) -> Tuple[Prop, ...]:
    """Every row of one kind, in the order a report should print them."""
    return tuple(get_kind(kind).props.values())


def prop_names(kind: Any) -> Tuple[str, ...]:
    """Every row's name, which is what ``WIDGET_PROPS`` answers with."""
    return tuple(get_kind(kind).props)


def hint_for(kind: Any, name: Any) -> str:
    """The sentence a tooltip or a docs page shows for one prop.

    Read out of the same row ``SET`` validates against, which is the only way a
    tooltip stays true: a hint written somewhere else goes stale the first time a
    range changes and nobody notices until someone hovers the widget.
    """
    return get_kind(kind).get(name).hint


def catalog_names() -> Tuple[str, ...]:
    """The kinds that came with the package, in declaration order."""
    return tuple(_CATALOG)


def registered_names() -> Tuple[str, ...]:
    """The kinds a script added, which ``reset()`` is going to take away."""
    return tuple(name for name in KINDS if name not in _CATALOG)


def _kind(name: Any, pd_object: str = "", base: Any = "", hint: str = "",
          args: Sequence[str] = (), rows: Sequence[str] = (),
          script: bool = False, **tweak: Mapping[str, Any]) -> Kind:
    """Declare one kind, build its table, and put it in the registry.

    ``rows`` is which of the value rows the kind has. The ones it does not name are
    not in its table at all: a ``panel`` has no ``value`` to clamp, and a table
    that quietly carried one would let ``SET(panel, "value", 3)`` succeed on a
    widget that ignores it. ``tweak`` then restates named rows, and naming a row
    the kind does not have is an error rather than an instruction nobody checked.

    Kinds are declared in dependency order, because a base has to exist to be
    inherited from: ``vslider`` is built on ``slider`` and is one line saying that
    the same table belongs to a different box.
    """
    name = _name(name)
    if not name:
        raise PropError("a widget kind has no name")
    if name in KINDS:
        raise PropError(f"'{name}' is already a widget kind")

    if base:
        parent = get_kind(base)
        props = {row.name: row.copy(kind=name, inherited=parent.name)
                 for row in parent.props.values()}
        declared: List[str] = []
        # a variant is the same widget with different defaults, so the box it
        # twins to and the arguments it is built with come along unless this
        # declaration says otherwise - which is the plan's "inherits pd_object"
        pd_object = pd_object or parent.pd_object
        args = tuple(args) or parent.args
    else:
        props = {row.name: row.copy(kind=name) for row in _COMMON}
        declared = list(props)

    for asked in rows:
        template = _TEMPLATE.get(_name(asked))
        if template is None:
            raise PropError(f"'{asked}' is not a row a kind can ask for (it is "
                            f"one of: {', '.join(_TEMPLATE)})")
        props[template.name] = template.copy(kind=name)
        if template.name not in declared:
            declared.append(template.name)

    for asked, changes in tweak.items():
        prop = props.get(_name(asked))
        if prop is None:
            raise PropError(f"'{name}' has no '{asked}' row to change; name it "
                            "in its rows= list first")
        if not isinstance(changes, Mapping):
            raise PropError(f"'{name}.{prop.name}' is being changed to "
                            f"{changes!r}, and a change is a mapping of the "
                            f"row's fields ({', '.join(prop.__slots__)})")
        props[prop.name] = prop.copy(**changes)
        if prop.name not in declared:
            declared.append(prop.name)

    # nothing may point a range at a row the kind does not have, and nothing may
    # name a constructor argument that no row can hold
    for prop in props.values():
        for word in prop.bounds:
            if word and word not in props:
                raise PropError(f"'{name}.{prop.name}' takes its range from "
                                f"'{word}', which '{name}' does not have")
    missing = [word for word in args if word not in props]
    if missing:
        raise PropError(f"'{name}' cannot build a constructor from "
                        f"{', '.join(missing)} - no such prop")

    kind = Kind(name, base=_name(base) if base else "", pd_object=pd_object,
                hint=hint, props=props, declared=declared, args=args,
                script=script)
    # the box the kind twins to is also the row a script may overrule, so it is
    # stamped rather than repeated at every declaration
    given = tweak.get("pd_object") if isinstance(tweak.get("pd_object"),
                                                 Mapping) else {}
    if pd_object and "default" not in given:
        kind.props["pd_object"] = kind.props["pd_object"].copy(default=pd_object)
    KINDS[name] = kind
    if not script:
        _CATALOG.append(name)
    return kind


def define(name: Any, base: Any = "", pd_object: str = "", hint: str = "",
           rows: Sequence[str] = (), args: Sequence[str] = ()) -> Kind:
    """``WIDGET_DEFINE(kind[, base])`` - a kind the running program adds.

    With a base it is a variant: the base's whole table, input handling and Pd
    twin, with the defaults free to be restated below it. Without one it is a new
    widget, which the drawing layer will require a painter for; this module does
    not check that, because it cannot tell a script-drawn kind from one whose
    ``ON_PAINT`` arrives on the next line.

    A name that is already taken is refused, catalog or otherwise. ``define`` is
    the only door, so a collision with a built-in and a second definition of the
    script's own kind are both reported the same way - and the plan wants that in
    a script error, which the bindings turn this into.
    """
    found = _name(name)
    if found in _CATALOG:
        raise PropError(f"'{found}' is a built-in widget kind; a script defines "
                        f"a kind beside it, not over it")
    if found in KINDS:
        raise PropError(f"'{found}' is already defined by this program")
    return _kind(found, pd_object=pd_object, base=base, hint=hint, rows=rows,
                 args=args, script=True)


def define_prop(kind: Any, name: Any, type: str = "", default: Any = _OMITTED,
                minimum: Optional[float] = None, maximum: Optional[float] = None,
                pd_name: Optional[str] = None, hint: Optional[str] = None,
                bounds: Optional[Sequence[str]] = None,
                choices: Optional[Sequence[str]] = None,
                readonly: Optional[bool] = None) -> Prop:
    """``PROP_DEFINE`` - one row added to, or restated on, a kind a script owns.

    A row the kind *inherited* is overridden, keeping whatever the new declaration
    leaves out: the plan's three lines that give ``bpm_slider`` a ``min``, a
    ``max`` and ``ticks`` do not restate the hints or the Pd names, and a variant
    that lost them would have to spell the whole table again to change one number.
    The row still says which kind its wording came from, so a dump can show a
    variant restating a default without claiming to have invented the prop.
    A row this kind already stated itself is a duplicate, and that is an error -
    two declarations for one name is a script that did not mean to say it twice.

    Only a script kind takes part in this. Adding a prop to ``slider`` would be
    ``PROP_DEFINE`` on a kind the program did not write, changing what every slider
    everywhere means, from a project file, at run time.
    """
    owner = get_kind(kind)
    if owner.name in _CATALOG:
        raise PropError(f"'{owner.name}' is a built-in kind; a script adds a "
                        "prop to a kind it defined itself")
    name = _name(name)
    if not name:
        raise PropError(f"a prop of '{owner.name}' has no name")
    if owner.owns(name):
        raise PropError(f"'{owner.name}.{name}' is already defined; a prop is "
                        "declared once per kind")

    held = owner.props.get(name)
    if held is None:
        # a row nobody has: the range it points at is this kind's own min/max, if
        # it has both, which is what makes a script-drawn stepper clamp its value
        # to the bounds the script declared above it
        if bounds is None and all(word in owner.props for word
                                  in ("min", "max")):
            bounds = ("min", "max")
        prop = Prop(owner.name, name, type or "number",
                    0.0 if default is _OMITTED else default, minimum, maximum,
                    pd_name or "", hint or "", bounds or ("", ""),
                    choices or ())
    else:
        prop = held.copy(kind=owner.name, type=type or held.type,
                         default=held.default if default is _OMITTED else default,
                         minimum=_keep(minimum, held.minimum),
                         maximum=_keep(maximum, held.maximum),
                         pd_name=_keep(pd_name, held.pd_name),
                         hint=_keep(hint, held.hint),
                         bounds=_keep(bounds, held.bounds),
                         choices=_keep(choices, held.choices),
                         readonly=_keep(readonly, held.readonly))

    owner.props[prop.name] = prop
    owner.declared.append(prop.name)
    _below(owner.name, prop)
    return prop


def _below(name: str, prop: Prop, seen: Optional[set] = None) -> None:
    """Hand a row down to the kinds that inherited it, unless they restated it.

    A kind built on a base gets a *copy* of the base's table, so a prop added to
    the base afterwards would otherwise leave every variant built before it one
    row short - and quietly, because the variant still resolves the rows it has.
    """
    seen = seen if seen is not None else set()
    seen.add(name)
    for other in list(KINDS.values()):
        if other.base == name and other.name not in seen:
            if prop.name not in other.declared:
                other.props[prop.name] = prop.copy(kind=other.name,
                                                   inherited=other.base)
            _below(other.name, prop, seen)


def reset() -> int:
    """Forget every kind a script defined, and say how many went.

    This is the run boundary the plan asks for. The catalog is not rebuilt, so a
    reset cannot pick up a different table on the second pass and cannot lose a
    widget that a headless test is halfway through drawing.
    """
    gone = [name for name in KINDS if name not in _CATALOG]
    for name in gone:
        KINDS.pop(name)
    return len(gone)


# --------------------------------------------------------------------------
# the four things every reader of the table wants
# --------------------------------------------------------------------------


def settle(kind: Any, name: Any, value: Any,
           values: Optional[Mapping[str, Any]] = None) -> Any:
    """What ``SET(w, name, value)`` would leave in the widget, or an error.

    ``values`` is the widget's own map - props, states, either - and a bounded row
    reads its range from it, which is the difference between a slider that clamps
    to its ``min``/``max`` and one that clamps to 0..1 whatever the script set.
    """
    return get_kind(kind).settle(name, value, values)


def settled(kind: Any, values: Optional[Mapping[str, Any]] = None
            ) -> Dict[str, Any]:
    """Every row at its default, with ``values`` settled on top of them.

    The whole of a widget's props, so a preset, a Pd box or a report never has to
    know which of them the script bothered to mention. An unknown name is refused
    here, which is what makes a constructor's validation and ``SET``'s the same
    check instead of two that drift apart.
    """
    return {name: found.value
            for name, found in get_kind(kind).states(values).items()}


def states(kind: Any, values: Optional[Mapping[str, Any]] = None
           ) -> Dict[str, State]:
    """One :class:`State` per row: what it holds, and whether it was written.

    What a widget keeps. ``written`` is why a preset can save only what a person
    changed instead of every default the program happens to ship today.
    """
    return get_kind(kind).states(values)


def value_of(kind: Any, values: Optional[Mapping[str, Any]], name: Any) -> Any:
    """The one value ``GET(w, name)`` reads, out of a widget's prop map.

    Thin on purpose. The point is that a read goes through this table too, so a
    prop that has been renamed here is not still readable under its old name and
    ``GET`` cannot answer ``None`` for a widget that has never been set.
    """
    prop = get_kind(kind).get(name)
    if values is None or prop.name not in values:
        return prop.default
    found = values[prop.name]
    return found.value if isinstance(found, State) else found


def _merge(owner: Kind, values: Optional[Mapping[str, Any]] = None
           ) -> Dict[str, Any]:
    """A finished prop map, with anything it left out filled from the table.

    The two readers that take a widget's own values use this instead of
    :func:`settled`, which refuses the four rows the pointer owns: a map that came
    off a widget has already been through validation, and a name no row answers to
    in it is not a script's typo but a preset from before a prop was renamed, so it
    is reported as one rather than dropped and quietly left out of a hash.
    """
    held = owner.defaults()
    for name, found in (values or {}).items():
        key = _name(name)
        if key not in held:
            raise PropError(f"a '{owner.name}' has no prop '{name}' to read "
                            f"(it has: {', '.join(sorted(held))})")
        held[key] = found.value if isinstance(found, State) else found
    return held


def pd_object_for(kind: Any, values: Optional[Mapping[str, Any]] = None) -> str:
    """Which Pd box this widget has a twin for, in the plan's order.

    The prop first, because a script that wrote ``SET(st, "pd_object", "nbx")``
    meant it; then the kind's own box, which is what a variant inherits without any
    new drawing code; then ``''``, which the caller turns into plain ``[r send]`` /
    ``[s recv]`` wiring and one warning, so the data path survives a kind that has
    no Pd object even if the box does not.
    """
    owner = get_kind(kind)
    if values is not None and "pd_object" in values:
        found = values["pd_object"]
        if isinstance(found, State):
            found = found.value
        return str(found or "")
    return owner.pd_object


def pd_arguments(kind: Any, values: Optional[Mapping[str, Any]] = None
                 ) -> Dict[str, Any]:
    """The props a real Pd box is asked to hold, as ``pd_name`` -> value.

    Not positional. The argument *order* of an ``[hslider]`` belongs to patch
    authoring, and copying it here would be a second, uncheckable version of a fact
    this table cannot verify. What is guaranteed is narrower and is the thing that
    actually drifts: every row the table gives a ``pd_name`` for shows up here and
    every row without one does not, so a prop added for the drawing alone is
    invisible to Pd by construction rather than by luck.

    A string or list still at its empty value is left out, so an unused ``send``
    does not put a blank symbol in the patch. Every value that is sent is put
    through the row it came from, so a hand-written map gets the same clamped,
    coerced number the widget itself would report.
    """
    owner = get_kind(kind)
    held = _merge(owner, values)
    out: Dict[str, Any] = {}
    for prop in owner.props.values():
        if not prop.pd_name:
            continue
        found = prop.settle(held[prop.name], values=held)
        if found == "" or found == []:
            continue
        out[prop.pd_name] = found
    return out


def digest(kind: Any, values: Optional[Mapping[str, Any]] = None) -> str:
    """The ``props_hash`` half of the PaintList key: same values, same string.

    Every prop counts, including the four the pointer owns - a hover changes what a
    painter draws, so a cache that skipped it would replay an unhovered widget over
    a hovered one and the bug would only appear on a control somebody was looking
    at. The kind is in the hash because two kinds can hold the same six numbers and
    draw different things with them.
    """
    owner = get_kind(kind)
    held = _merge(owner, values)
    said = [owner.name]
    for name in sorted(held):
        said.append(f"{name}={held[name]!r}")
    return hashlib.sha1("\x1f".join(said).encode("utf-8")).hexdigest()[:12]


def document(kind: Any = None) -> Dict[str, Any]:
    """The schema ``crude widgets --json`` prints and ``docs/widgets.md`` is drawn
    from.

    The same objects ``SET`` validates against, described: one dump, one table, no
    second list of props to keep in step with the first.
    """
    if kind is not None:
        return get_kind(kind).as_dict()
    return {"schema": SCHEMA, "types": list(TYPES), "pd_boxes": list(PD_BOXES),
            "kinds": [found.as_dict() for found in KINDS.values()]}


# --------------------------------------------------------------------------
# the catalog: every kind a script can name without defining one
# --------------------------------------------------------------------------

# the faces a window is made of. None of them holds a value, which is the point:
# a panel that answered GET(w, "value") would be a panel with a default nobody set
_kind("panel", "cnv", hint="A box other widgets live in; children clip to it.",
      args=("label",), role={"default": "panel"})
_kind("text", "", hint="One line of text, drawn in the skin's ink.",
      args=("label",), role={"default": "text"})
_kind("icon", "", hint="A glyph from the atlas, sized by the box it is in.",
      args=("glyph",), role={"default": "icon"})

# the two that hold 0 or 1, and differ only in whether they stay there
_kind("button", "bng", hint="A momentary: one press sends 1 and falls back.",
      args=("label",), rows=("value",), role={"default": "button"},
      value={"default": 0.0, "minimum": 0.0, "maximum": 1.0,
             "bounds": ("", "")})
_kind("toggle", "tgl",
      hint="A latching switch, with the play/pause pair it draws.",
      args=("label",), rows=("value",), role={"default": "toggle"},
      value={"default": 0.0, "minimum": 0.0, "maximum": 1.0,
             "bounds": ("", "")},
      glyph_off={"default": "play"}, glyph_on={"default": "pause"})

# the range controls, which agree on everything except the box they twin to
_kind("slider", "hslider",
      hint="A horizontal range; drag, click, or arrow it.",
      args=("label", "min", "max", "value"),
      rows=("value", "min", "max", "step", "log", "ticks", "editable"),
      role={"default": "track"})
_kind("vslider", "vslider", base="slider",
      hint="A slider drawn top-down; every prop is slider's own.")
# vanilla Pd has no dial: a knob is an [hslider] the skin draws round, so the
# twin, the args and the range rows all come from slider, and only the face,
# the gesture and the "no typing into a dial" default are this kind's own
_kind("knob", base="slider",
      hint="A rotary range; drag up or scroll to raise it, arrows either way.",
      role={"default": "knob"}, editable={"default": False},
      glyph={"default": ""})
_kind("number", "nbx", hint="A value that is typed as well as dragged.",
      args=("label", "min", "max", "value"),
      rows=("value", "min", "max", "step", "log", "editable"),
      role={"default": "field"})
_kind("meter", "vu", hint="A level read out, not set: no typing, no dragging.",
      args=("label", "min", "max", "value"), rows=("value", "min", "max"),
      role={"default": "meter"})

# the two whose value is an index into the list they were given
_kind("radio", "radio", hint="A set of choices laid out at once.",
      args=("labels", "value"), rows=("value", "labels", "columns"),
      role={"default": "checkbox"},
      value={"minimum": 0.0, "maximum": None, "bounds": ("", "labels")})
_kind("dropdown", "dropdown", hint="One choice, folded until it is opened.",
      args=("labels", "value"), rows=("value", "labels"),
      role={"default": "field"},
      glyph={"default": "chevron-d"},
      value={"minimum": 0.0, "maximum": None, "bounds": ("", "labels")})

# the synth widgets. None of them twins to a Pd box - a waveform, an envelope or
# a keybed has no [hslider] equivalent - so each says ``pd_object ""`` and the
# data path stays the script's own: ``ON_CHANGE`` into a ``PD_SEND``, or
# ``PD_MIDI_NOTEON`` for the keybed. They are built on the same conventions as
# the metronome panel's controls: rows from this table, a role from the skin,
# and a face that reads only props a script can ``SET``.
_kind("waveform", "", hint="A rolling trace of samples, drawn from data a "
      "script pushes in.", args=("label",), rows=("data", "points"),
      role={"default": "grid"})
_kind("spectrum", "", hint="Magnitudes as bars; the script does the FFT and "
      "writes the bins.", args=("label",), rows=("data", "bins"),
      role={"default": "grid"})
# adsr and keybed hold a value but own no min/max rows, so value restates its
# own bounds the way button and toggle do - the range is the table's promise,
# not something a script can move out from under the face
_kind("adsr", "", hint="An envelope a, d, s and r on one graph; the value is "
      "the stage being edited, 0 for attack through 3 for release.",
      args=("label", "attack", "decay", "sustain", "release"),
      rows=("value", "attack", "decay", "sustain", "release"),
      role={"default": "grid"},
      value={"default": 0.0, "minimum": 0.0, "maximum": 3.0,
             "bounds": ("", "")})
_kind("curve", "", hint="A single bendable response curve; the tension is the "
      "shape, dragged vertically.", args=("label", "tension"),
      rows=("tension",), role={"default": "grid"})
_kind("xy", "", hint="Two continuous values at once, wherever the pointer is.",
      args=("label", "x", "y"), rows=("x", "y"), role={"default": "grid"})
_kind("keybed", "", hint="A piano keyboard; the value is the pitch held down, "
      "0 when nothing is.", args=("label", "low", "octaves"),
      rows=("value", "low", "octaves", "channel", "velocity"),
      role={"default": "grid"},
      value={"default": 0.0, "minimum": 0.0, "maximum": 127.0,
             "bounds": ("", "")})
# a preset bar is a radio wearing the accent: same labels, same index, same
# cell grid - only the face of it changes
_kind("preset_bar", "", base="radio", hint="The preset strip: a radio's "
      "choices drawn as one row of accent-lit cells.",
      role={"default": "selection"})
