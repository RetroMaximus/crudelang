"""Serializing a :class:`~crude.pd.model.PatchDoc` to Pd's text format.

The output is the legacy ``.pd`` dialect every Pd version reads::

    #N canvas 0 0 600 400 12;
    #X obj 40 40 osc~ 440;
    #X msg 40 10 set 440;
    #X floatatom 40 120 5 0 0 0 freq - -;
    #X obj 40 200 table $0-wave 16;
    #A 0 0 0.5 1 0.5 0 -0.5 -1 -0.5;
    #X text 40 10 a comment;
    #X connect 1 0 0 0;

Two rules make the result reproducible:

* boxes are written in insertion order, links sorted by
  ``(source index, outlet, destination index, inlet)`` - the same order Pd
  itself uses, and stable across runs, so the output is golden-testable and the
  content hash in :mod:`crude.pd.cache` only changes when the patch changes;
* every atom goes through :func:`escape_atom` / :func:`escape_text`, never
  string interpolation, because ``;`` ``\\`` ``,`` and ``$`` are all structural
  in this format (a stray ``;`` truncates the line, an unescaped ``,`` splits an
  object's arguments). This is the single most dangerous part of authoring, so
  it lives in one place with its own tests.
"""

from __future__ import annotations

import math
import os
from typing import List

from .model import GATOM_KINDS, Box, PatchDoc, normalize_text

#: Characters that always need a backslash, whatever the context.
ALWAYS_ESCAPED = "\\;"

#: Characters that need a backslash only when they must stay literal.
ATOM_ESCAPED = ",$"

#: Box width Pd writes for a gatom (only cosmetic, but keeps diffs small).
GATOM_WIDTH = {"floatatom": 5, "symbolatom": 10}

#: ``-`` means "empty slot" in a gatom's label/receive/send fields.
EMPTY = "-"


# -- atoms --------------------------------------------------------------


def _escape(atom: str, characters: str) -> str:
    out = []
    for char in atom:
        if char in characters:
            out.append("\\")
        out.append(char)
    return "".join(out)


def escape_atom(atom) -> str:
    """Escape one *symbol* atom so Pd reads it back verbatim.

    ``a;b`` -> ``a\\;b``, ``$1`` -> ``\\$1``, ``a b`` stays a single atom only
    if the caller also escapes the space (``escape_text`` never does - object
    text is a sequence of atoms by definition).
    """
    return _escape(str(atom), ALWAYS_ESCAPED + ATOM_ESCAPED)


def escape_name(name) -> str:
    """Escape a name that may legitimately be a dollar argument (``$0-wave``)."""
    return _escape(str(name), ALWAYS_ESCAPED + ",")


def escape_text(text) -> str:
    """Escape an object/message body: only ``\\`` and ``;`` are structural.

    Commas and dollars are *left alone on purpose*: in ``[metro 500 1, bang]``
    and ``[set $1]`` they are Pd syntax, not literal characters. A script that
    wants a literal comma or dollar in a message uses :func:`escape_atom`.
    """
    atoms = normalize_text(text).split(" ")
    return " ".join(_escape(atom, ALWAYS_ESCAPED) for atom in atoms if atom)


def fmt_number(value) -> str:
    """Format a number the way Pd does: ``%g``, i.e. 6 significant digits."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{value}' is not a number")
    if not math.isfinite(number):
        raise ValueError(f"'{value}' cannot be written to a patch (not finite)")
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return f"{number:.6g}"


def fmt_atom(value) -> str:
    """Format one atom of a message or of ``#A`` data: number or symbol."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return fmt_number(value)
    return escape_atom(value)


# -- boxes --------------------------------------------------------------


def write_box(box: Box) -> List[str]:
    """The ``.pd`` lines for one box (a table may also emit ``#A`` data)."""
    x, y = box.x, box.y

    if box.kind == "graph":
        return [f"#X obj {x} {y} graph;"]
    if box.kind == "obj":
        return [f"#X obj {x} {y} {_body(box)};"]
    if box.kind == "msg":
        return [f"#X msg {x} {y} {_body(box)};"]
    if box.kind == "text":
        return [f"#X text {x} {y} {escape_text(box.text)};"]
    if box.kind in GATOM_KINDS:
        return [
            f"#X {box.kind} {x} {y} {GATOM_WIDTH[box.kind]} 0 0 0 "
            f"{_slot(box.label)} {_slot(box.recv)} {_slot(box.send)};"
        ]
    if box.kind == "table":
        lines = [
            f"#X obj {x} {y} table {escape_name(box.table_name)} {box.size};"
        ]
        lines.extend(write_table_data(box))
        return lines

    raise ValueError(f"cannot serialize box kind '{box.kind}'")


def _body(box: Box) -> str:
    r"""The object or message text of a box, escaped unless it is already file form.

    A verbatim (:attr:`~crude.pd.model.Box.raw`) box is written as it stands. That
    is the whole point of the flag - a GUI box's ``\$0-run`` has to stay ``\$0-run``,
    because escaping it again would hand Pd a literal backslash - and the reason
    :meth:`~crude.pd.model.PatchDoc.validate` checks a raw line for the structural
    characters escaping would otherwise have contained.
    """
    return box.text if box.raw else escape_text(box.text)


def _slot(value: str) -> str:
    return escape_atom(value) if value else EMPTY


def write_table_data(box: Box) -> List[str]:
    """``#A 0 ...;`` for a seeded table, nothing for an all-zero one."""
    if not any(box.init):
        return []
    return ["#A 0 " + " ".join(fmt_atom(value) for value in box.init) + ";"]


# -- patches ------------------------------------------------------------


def write_patch(doc: PatchDoc, validate: bool = True) -> str:
    """Serialize ``doc``; validation errors raise :class:`~crude.pd.model.PatchError`."""
    if validate:
        doc.validate()

    canvas = doc.canvas
    lines = [f"#N canvas 0 0 {canvas.width} {canvas.height} {canvas.font};"]

    for box in doc.boxes:
        lines.extend(write_box(box))

    index = {box.id: position for position, box in enumerate(doc.boxes)}
    ordered = sorted(
        doc.links,
        key=lambda link: (index[link.src], link.outlet, index[link.dst], link.inlet),
    )
    for link in ordered:
        lines.append(
            f"#X connect {index[link.src]} {link.outlet} "
            f"{index[link.dst]} {link.inlet};"
        )

    return "\n".join(lines) + "\n"


def write_file(doc: PatchDoc, path: str, validate: bool = True) -> str:
    """Write ``doc`` to ``path`` (LF endings) and return the path."""
    text = write_patch(doc, validate=validate)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path
