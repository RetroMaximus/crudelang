"""Reading ``.pd`` text back into a :class:`~crude.pd.model.PatchDoc`.

The reader is deliberately *tolerant*: it understands everything
:mod:`crude.pd.writer` emits (so ``parse(serialize(doc)) == doc`` holds) plus
the hand-written patches a project ships, and it warns instead of failing on
constructs it does not model (subpatches, ``declare``, ``coords``, GUI boxes it
cannot map, ...). That makes it usable for diagnostics - ``crude pd show``,
``PD_INSPECT`` - where "tell me what is in this file" matters more than
fidelity.

What is parsed:

* ``#N canvas x y w h font;`` (the first one is the patch's own canvas);
* ``#X obj|msg|text|floatatom|symbolatom`` and ``#X connect``;
* ``#A <onset> ...`` seed data for the preceding table;
* ``#X restore`` closes a subpatch, whose contents are skipped.

Not preserved, by design: window position, gatom width/min/max, per-box editor
state (the trailing ``f 5`` style arguments) and any message-box ``;`` payload.
"""

from __future__ import annotations

import os
from typing import List, Optional

from .model import Box, PatchDoc

#: ``#X`` heads that carry box content we model.
BOX_HEADS = ("obj", "msg", "text", "floatatom", "symbolatom")

#: ``#X`` heads that are valid but not modelled; they are skipped silently.
SKIPPED_HEADS = (
    "connect",
    "restore",
    "array",
    "coords",
    "f",
    "pop",
    "declare",
    "listbox",
)


def unescape(atom: str) -> str:
    """Turn ``a\\;b`` back into ``a;b`` (the inverse of the writer's escape)."""
    out = []
    escaped = False
    for char in atom:
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            out.append(char)
    if escaped:
        out.append("\\")
    return "".join(out)


def split_atoms(text: str) -> List[str]:
    """Split a ``#X`` line body into unescaped atoms."""
    atoms = []
    current = []
    escaped = False
    for char in text:
        if escaped:
            current.append("\\")
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char.isspace():
            if current:
                atoms.append(unescape("".join(current)))
                current = []
        else:
            current.append(char)
    if current:
        atoms.append(unescape("".join(current)))
    return atoms


def _line_body(line: str) -> str:
    line = line.strip()
    if line.endswith(";"):
        line = line[:-1]
    return line


def _int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _slot(value: str) -> str:
    return "" if value in ("-", "") else value


def parse_patch(text: str, patch_id: str = "<patch>") -> PatchDoc:
    """Parse ``text`` into a :class:`PatchDoc`; unknown constructs become warnings."""
    doc = PatchDoc(patch_id)
    warnings: List[str] = []
    boxes_by_index: List[Box] = []
    last_table: Optional[Box] = None
    depth = 0
    seen_header = False

    for number, raw in enumerate(text.splitlines(), start=1):
        body = _line_body(raw)
        if not body:
            continue

        if body.startswith("#N"):
            head = body.split()
            kind = head[1] if len(head) > 1 else ""
            if kind == "canvas":
                if not seen_header:
                    seen_header = True
                    values = [_int(token) for token in head[2:]]
                    if len(values) >= 4:
                        doc.canvas.width = values[2]
                        doc.canvas.height = values[3]
                    if len(values) >= 5:
                        doc.canvas.font = values[4]
                else:
                    depth += 1
            else:
                warnings.append(f"line {number}: ignored '#N {kind}'")
            continue

        if not body.startswith("#"):
            warnings.append(f"line {number}: ignored text {body[:40]!r}")
            continue

        head = body.split(None, 1)
        tag = head[0]
        rest = head[1] if len(head) > 1 else ""

        if tag == "#A":
            if depth or last_table is None:
                warnings.append(f"line {number}: '#A' data without a table")
                continue
            _apply_array_data(last_table, split_atoms(rest), number, warnings)
            continue

        if tag != "#X":
            warnings.append(f"line {number}: ignored '{tag}'")
            continue

        atoms = split_atoms(rest)
        if not atoms:
            warnings.append(f"line {number}: empty '#X' line")
            continue

        kind = atoms[0]
        if kind == "restore":
            if depth:
                depth -= 1
            else:
                warnings.append(f"line {number}: '#X restore' without a subpatch")
            continue
        if depth:
            continue
        if kind == "connect":
            _add_link(doc, boxes_by_index, atoms[1:], number, warnings)
            continue
        if kind == "coords":
            continue
        if kind == "obj":
            last_table = _add_object(doc, boxes_by_index, atoms[1:], number, warnings)
            continue
        if kind in BOX_HEADS:
            last_table = None
            _add_generic(doc, boxes_by_index, kind, atoms[1:], number, warnings)
            continue

        if kind in SKIPPED_HEADS:
            warnings.append(f"line {number}: skipped '#X {kind}' (not modelled)")
        else:
            warnings.append(f"line {number}: unknown '#X {kind}' (skipped)")

    doc.warnings = warnings
    return doc


def _add_generic(doc: PatchDoc, boxes: List[Box], kind: str, atoms: List[str],
                 number: int, warnings: List[str]) -> None:
    if len(atoms) < 2:
        warnings.append(f"line {number}: '#X {kind}' has no coordinates")
        return
    x, y = _int(atoms[0]), _int(atoms[1])

    if kind in ("floatatom", "symbolatom"):
        # x y width min max labelpos label receive send
        label = _slot(atoms[6]) if len(atoms) > 6 else ""
        recv = _slot(atoms[7]) if len(atoms) > 7 else ""
        send = _slot(atoms[8]) if len(atoms) > 8 else ""
        box = doc.add_box(kind, x, y, label=label, recv=recv, send=send)
    else:
        box = doc.add_box(kind, x, y, " ".join(atoms[2:]))

    boxes.append(box)


def _add_object(doc: PatchDoc, boxes: List[Box], atoms: List[str],
                number: int, warnings: List[str]) -> Optional[Box]:
    """An ``#X obj`` line; returns the box when it is a table (for ``#A``)."""
    if len(atoms) < 2:
        warnings.append(f"line {number}: '#X obj' has no coordinates")
        return None
    x, y = _int(atoms[0]), _int(atoms[1])
    body = " ".join(atoms[2:])

    if atoms[2:3] == ["table"] and len(atoms) in (4, 5):
        name = atoms[3]
        size = _int(atoms[4]) if len(atoms) == 5 else 0
        box = doc.add_box("table", x, y, name, size=size or 1)
        boxes.append(box)
        return box

    if body == "graph":
        box = doc.add_box("graph", x, y, "")
    else:
        box = doc.add_box("obj", x, y, body)
    boxes.append(box)
    return None


def _apply_array_data(box: Box, atoms: List[str], number: int,
                      warnings: List[str]) -> None:
    if not atoms:
        warnings.append(f"line {number}: '#A' line without an onset")
        return
    onset = _int(atoms[0])
    values = []
    for atom in atoms[1:]:
        try:
            values.append(float(atom))
        except ValueError:
            warnings.append(f"line {number}: '#A' atom {atom!r} is not a number")
            return
    if onset < 0 or onset >= box.size:
        warnings.append(
            f"line {number}: '#A' onset {onset} is outside table '{box.table_name}'"
        )
        return
    end = min(onset + len(values), box.size)
    box.init[onset:end] = values[: end - onset]


def _add_link(doc: PatchDoc, boxes: List[Box], atoms: List[str], number: int,
              warnings: List[str]) -> None:
    if len(atoms) < 4:
        warnings.append(f"line {number}: '#X connect' needs four numbers")
        return
    src, outlet, dst, inlet = (_int(atom) for atom in atoms[:4])
    if not (0 <= src < len(boxes)) or not (0 <= dst < len(boxes)):
        warnings.append(
            f"line {number}: '#X connect {src} {outlet} {dst} {inlet}' "
            f"refers to a missing box"
        )
        return
    doc.link(boxes[src], outlet, boxes[dst], inlet)


def read_file(path: str, patch_id: str = None) -> PatchDoc:
    """Parse the ``.pd`` file at ``path``."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    return parse_patch(text, patch_id or os.path.basename(path))


def describe(doc: PatchDoc) -> List[str]:
    """A box/link table for ``crude pd show`` and ``PD_INSPECT``."""
    canvas = doc.canvas
    lines = [
        f"patch {doc.id}: canvas {canvas.width}x{canvas.height} font {canvas.font}",
        f"{len(doc.boxes)} boxes, {len(doc.links)} links",
    ]

    for position, box in enumerate(doc.boxes):
        lines.append(f"[{position}] {box.describe()}")

    index = {box.id: position for position, box in enumerate(doc.boxes)}
    for link in sorted(
        doc.links,
        key=lambda item: (index[item.src], item.outlet, index[item.dst], item.inlet),
    ):
        lines.append(
            f"    {index[link.src]}:{link.outlet} -> {index[link.dst]}:{link.inlet}"
        )

    for warning in doc.warnings:
        lines.append(f"warning: {warning}")
    return lines
