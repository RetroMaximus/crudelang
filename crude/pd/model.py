"""The patch a script authors: a document, not a drawing.

libpd has no API for building boxes in memory - its only entry point is
``libpd_openfile`` - so an authored patch is a document that is serialized to
standard ``.pd`` text (:mod:`crude.pd.writer`), cached, and opened like any
hand-written file. This module holds that document:

* :class:`PatchDoc` - the canvas, its boxes and its links, with script-visible
  ids that never reach the file format;
* :class:`Box` - one ``obj``/``msg``/``floatatom``/``symbolatom``/``text``/
  ``table``/``graph``;
* :class:`Link` - ``src outlet -> dst inlet``.

Everything here is plain data plus validation. The two rules the rest of the
stack relies on:

* **insertion order is output order** - boxes keep the order a script created
  them, so serialization is reproducible without sorting;
* **a table declared without an explicit scope is ``$0-`` prefixed** - two
  instances of the same patch must not share an array, so ``PD_TABLE(p,
  "wave", 16)`` becomes ``table $0-wave 16`` and :class:`PdPatch` resolves the
  name through ``libpd_getdollarzero`` at load time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

#: Canvas defaults for an authored patch.
DEFAULT_WIDTH = 600
DEFAULT_HEIGHT = 400
DEFAULT_FONT = 12

#: Where a box goes when the script does not say: a readable grid, so a patch
#: built entirely with ``PD_OBJ(p, "osc~ 440")`` still opens sensibly in Pd.
GRID_ORIGIN = 20
GRID_X = 140
GRID_Y = 60
GRID_COLUMNS = 4

#: Box kinds the writer knows how to serialize.
BOX_KINDS = ("obj", "msg", "floatatom", "symbolatom", "text", "table", "graph")

#: Kinds whose text may be stored *verbatim*: it goes onto the line exactly as it
#  was handed over, with no escaping and no whitespace collapsing. Only the two
#  kinds whose whole payload is object text qualify - a ``floatatom`` line is
#  assembled from its slots, and a ``table`` name has to be scoped, so taking
#  their text as finished would skip the rules that make them valid.
#  :mod:`crude.pd.pdgui` is the only producer of verbatim text in the package: a
#  Pd GUI box is written in the file's own form already (a ``\$0`` in it means
#  ``$0`` to Pd), so escaping it a second time would corrupt the name.
RAW_TEXT_KINDS = ("obj", "msg")

#: Kinds that carry label/receive/send slots in the ``.pd`` text.
GATOM_KINDS = ("floatatom", "symbolatom")

#: Kinds that are meaningless unless something is connected to them. Tables and
#: graphs are not in it: Pd shows them side by side without a patch cord, and a
#: script reaches a table by name, so an unconnected one is normal.
LINKABLE_KINDS = ("obj", "msg", "floatatom", "symbolatom")

#: Kinds whose ``text`` is a symbol rather than free text.
NAMED_KINDS = ("table",)

#: Prefix that scopes a table name to one patch instance.
DOLLAR_ZERO = "$0-"

#: What a script may read and write on a box handle with ``GET``/``SET``.
BOX_PROPS = ("kind", "x", "y", "text", "send", "recv", "label", "size")
BOX_WRITE_PROPS = ("x", "y", "text", "send", "recv", "label", "size")

#: And on a patch handle.
PATCH_PROPS = ("id", "width", "height", "font", "boxes", "links")


class PatchError(RuntimeError):
    """An authored patch that cannot be serialized as written."""


def normalize_text(text) -> str:
    """Collapse whitespace, so the writer's output round-trips exactly."""
    return " ".join(str(text).split())


def scope_table_name(name: str, scoped: bool = True) -> str:
    """``wave`` -> ``$0-wave``; names that already carry ``$`` are left alone."""
    name = normalize_text(name)
    if not scoped or not name or name.startswith("$"):
        return name
    return DOLLAR_ZERO + name


def unscope_table_name(name: str) -> str:
    """The script-visible name of a table (``$0-wave`` -> ``wave``)."""
    return name[len(DOLLAR_ZERO):] if name.startswith(DOLLAR_ZERO) else name


def _is_named_io(box: "Box") -> bool:
    """``[r freq]`` / ``[s freq]`` reach Pd by name, so they need no wires."""
    head = box.text.split(" ", 1)[0] if box.kind == "obj" else ""
    return head in ("r", "s", "send", "receive", "print", "dbg")


#: An object that has nothing to wire: ``[cnv]`` is a coloured rectangle, and a
#: box whose whole job is to be looked at is finished as it stands. ``PD_EMIT``
#: writes one for every ``PANEL``, and ``PD_GUI`` for every decoration, so
#: without this the report would call a deliberate rectangle an unfinished one.
DECORATION_TAGS = ("cnv",)


def _is_decoration(box: "Box") -> bool:
    """``[cnv ...]``: a box that is connected to nothing by design."""
    head = box.text.split(" ", 1)[0] if box.kind == "obj" else ""
    return head in DECORATION_TAGS


def _backslash_run(text: str, before: int) -> int:
    """How many backslashes end the run that sits just left of ``before``."""
    count = 0
    walk = before - 1
    while walk >= 0 and text[walk] == "\\":
        count += 1
        walk -= 1
    return count


def unescaped(text: str, character: str) -> bool:
    """Whether ``character`` appears in ``text`` standing on its own.

    An even number of backslashes in front of it means it is not part of an
    escape, so ``a\\;b`` holds an escaped semicolon (one symbol, safe) while
    ``a;b`` ends the line (two atoms, not). This is the same rule
    :func:`crude.pd.writer.escape_text` implements by construction, needed here to
    check text that was written by hand instead.
    """
    index = text.find(character)
    while index != -1:
        if _backslash_run(text, index) % 2 == 0:
            return True
        index = text.find(character, index + 1)
    return False


def ends_with_an_open_escape(text: str) -> bool:
    """Whether ``text`` trails an odd number of backslashes.

    The last one would then escape the ``;`` the writer puts after the box, which
    joins the following line to this one - the quietest possible way to corrupt a
    patch, so it is refused rather than repaired.
    """
    return _backslash_run(text, len(text)) % 2 == 1


@dataclass
class Canvas:
    """The ``#N canvas`` header."""

    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    font: int = DEFAULT_FONT


@dataclass
class Box:
    r"""One box on the canvas.

    ``text`` is what follows the coordinates in the ``.pd`` line: the object
    text for ``obj``, the message for ``msg``, the label for ``text`` and the
    (already resolved) table name for ``table``. ``init`` is the seed data of a
    table, always exactly ``size`` long once the box is created.

    ``raw`` says that text is already in the form the file wants and is written
    as it stands. Nothing a script types is raw - only :mod:`crude.pd.pdgui`
    sets it, because a GUI box's argument string is *already* escaped the way a
    saved patch holds it, and running it through the writer's escaping a second
    time would turn ``\$0-run`` into ``\\$0-run`` and split ``my\ label`` into two
    atoms. :meth:`PatchDoc.validate` is what makes skipping the escaping safe.
    """

    id: str
    kind: str
    x: int = 0
    y: int = 0
    text: str = ""
    send: str = ""
    recv: str = ""
    label: str = ""
    size: int = 0
    init: List[float] = field(default_factory=list)
    raw: bool = False

    @property
    def is_table(self) -> bool:
        return self.kind == "table"

    @property
    def table_name(self) -> str:
        """The name Pd knows (``$0-wave`` for a scoped table)."""
        return self.text

    # -- script properties (GET / SET) ----------------------------------
    def get_prop(self, name: str):
        if name in BOX_PROPS:
            value = getattr(self, name)
            if name in ("x", "y", "size"):
                value = int(value)
            return value
        if name == "init":
            return list(self.init)
        raise KeyError(name)

    def set_prop(self, name: str, value) -> None:
        if name not in BOX_WRITE_PROPS:
            raise KeyError(name)
        if name in ("x", "y", "size"):
            value = int(value)
        elif name in ("text", "send", "recv", "label"):
            # a raw box's text is the file's own bytes, so collapsing its
            # whitespace would undo what an escaped space in it is for
            value = (str(value).strip() if self.raw and name == "text"
                     else normalize_text(value))
        setattr(self, name, value)

    def describe(self) -> str:
        parts = [f"{self.kind:<10}", f"at {self.x},{self.y}"]
        if self.text:
            parts.append(repr(self.text))
        if self.size:
            parts.append(f"size {self.size}")
        if self.label:
            parts.append(f"label {self.label!r}")
        if self.recv:
            parts.append(f"recv {self.recv!r}")
        if self.send:
            parts.append(f"send {self.send!r}")
        return " ".join(parts)

    def __repr__(self):
        return f"<Box {self.id} {self.kind} {self.text!r}>"


@dataclass(frozen=True)
class Link:
    """A ``#X connect src outlet dst inlet`` line."""

    src: str
    outlet: int
    dst: str
    inlet: int

    def describe(self) -> str:
        return f"connect {self.src}:{self.outlet} -> {self.dst}:{self.inlet}"

    def __repr__(self):
        return f"<Link {self.src}:{self.outlet} -> {self.dst}:{self.inlet}>"


class PatchDoc:
    """A patch under construction; :func:`crude.pd.writer.write_patch` emits it."""

    def __init__(self, id: str, width: int = None, height: int = None,
                 font: int = None):
        self.id = str(id)
        self.canvas = Canvas(
            DEFAULT_WIDTH if width is None else int(width),
            DEFAULT_HEIGHT if height is None else int(height),
            DEFAULT_FONT if font is None else int(font),
        )
        self.boxes: List[Box] = []
        self.links: List[Link] = []
        self.meta: Dict[str, object] = {}
        #: non-fatal findings from the last :meth:`validate` or from the reader
        self.warnings: List[str] = []
        self._counters: Dict[str, int] = {}

    # -- script properties (GET / SET) ----------------------------------
    def get_prop(self, name: str):
        if name == "id":
            return self.id
        if name == "width":
            return self.canvas.width
        if name == "height":
            return self.canvas.height
        if name == "font":
            return self.canvas.font
        if name == "boxes":
            return len(self.boxes)
        if name == "links":
            return len(self.links)
        raise KeyError(name)

    def set_prop(self, name: str, value) -> None:
        if name in ("width", "height", "font"):
            setattr(self.canvas, name, int(value))
            return
        if name == "boxes" or name == "links" or name == "id":
            raise KeyError(name)
        raise KeyError(name)

    # -- creation -------------------------------------------------------
    def add_box(self, kind: str, x: int = 0, y: int = 0, text: str = "",
                send: str = "", recv: str = "", label: str = "",
                init: Sequence[float] = None, size: int = 0,
                box_id: str = None, raw: bool = False) -> Box:
        if kind not in BOX_KINDS:
            raise PatchError(
                f"patch '{self.id}': unknown box kind '{kind}' "
                f"(expected one of {', '.join(BOX_KINDS)})"
            )
        if raw and kind not in RAW_TEXT_KINDS:
            raise PatchError(
                f"patch '{self.id}': a '{kind}' box cannot be written verbatim "
                f"(only {', '.join(RAW_TEXT_KINDS)} carry their text as it is)"
            )
        box = Box(
            id=box_id or self._next_id(kind),
            kind=kind,
            x=int(x),
            y=int(y),
            text=str(text).strip() if raw else normalize_text(text),
            send=normalize_text(send),
            recv=normalize_text(recv),
            label=normalize_text(label),
            raw=bool(raw),
        )
        if box.id in {existing.id for existing in self.boxes}:
            raise PatchError(f"patch '{self.id}': box id '{box.id}' is already used")

        if kind == "table":
            box.size = self._table_size(size, init)
            box.init = self._table_data(box.size, init)
        elif init:
            raise PatchError(
                f"patch '{self.id}': only a table can carry seed data "
                f"(box '{box.id}' is a {kind})"
            )

        self.boxes.append(box)
        return box

    def add_object(self, text: str, x: int = 0, y: int = 0, **kwargs) -> Box:
        kind = "graph" if normalize_text(text) == "graph" else "obj"
        return self.add_box(kind, x, y, text, **kwargs)

    def add_gui_box(self, text: str, x: int = 0, y: int = 0,
                    send: str = "", recv: str = "",
                    label: str = "") -> Box:
        """A Pd GUI box, written exactly as :mod:`crude.pd.pdgui` laid it out.

        The text arrives already in file form, so this is the only way to get a
        verbatim box into a document: it refuses a kind that has no template to
        go wrong with and keeps the names the box carries in its own arguments in
        the ``send``/``recv``/``label`` slots too, which is what lets
        :meth:`validate` and ``PD_LOAD``'s receiver probe see that the emitted box
        actually has someone to talk to.
        """
        return self.add_box("obj", x, y, text, send=send, recv=recv,
                            label=label, raw=True)

    def add_message(self, text: str, x: int = 0, y: int = 0, **kwargs) -> Box:
        return self.add_box("msg", x, y, text, **kwargs)

    def add_number(self, label: str = "", x: int = 0, y: int = 0, **kwargs) -> Box:
        return self.add_box("floatatom", x, y, "", label=label, **kwargs)

    def add_symbol(self, label: str = "", x: int = 0, y: int = 0, **kwargs) -> Box:
        return self.add_box("symbolatom", x, y, "", label=label, **kwargs)

    def add_text(self, text: str, x: int = 0, y: int = 0, **kwargs) -> Box:
        return self.add_box("text", x, y, text, **kwargs)

    def add_table(self, name: str, size: int = 0, x: int = 0, y: int = 0,
                  init: Sequence[float] = None, scoped: bool = True,
                  **kwargs) -> Box:
        resolved = scope_table_name(name, scoped)
        if not resolved:
            raise PatchError(f"patch '{self.id}': a table needs a name")
        return self.add_box("table", x, y, resolved, size=size, init=init, **kwargs)

    def add_graph(self, x: int = 0, y: int = 0, **kwargs) -> Box:
        return self.add_box("graph", x, y, "", **kwargs)

    def next_position(self) -> Tuple[int, int]:
        """The next free slot on the layout grid (``PD_OBJ`` without coordinates)."""
        row, column = divmod(len(self.boxes), GRID_COLUMNS)
        return (GRID_ORIGIN + column * GRID_X, GRID_ORIGIN + row * GRID_Y)

    def seed(self, name: str, values: Sequence[float]) -> Box:
        """Replace (or add) the seed data of the table called ``name``."""
        box = self.table(name)
        box.init = self._table_data(box.size, values)
        return box

    # -- links ----------------------------------------------------------
    def link(self, src, outlet: int = 0, dst=None, inlet: int = 0) -> Link:
        source = self.box(src) if not isinstance(src, Box) else src
        target = self.box(dst) if not isinstance(dst, Box) else dst
        try:
            outlet = int(outlet)
            inlet = int(inlet)
        except (TypeError, ValueError):
            raise PatchError(
                f"patch '{self.id}': outlet/inlet must be whole numbers, "
                f"got {outlet!r}/{inlet!r}"
            )
        link = Link(source.id, outlet, target.id, inlet)
        if link not in self.links:
            self.links.append(link)
        return link

    def unlink(self, src, outlet: int = 0, dst=None, inlet: int = 0) -> bool:
        source = self.box(src) if not isinstance(src, Box) else src
        target = self.box(dst) if not isinstance(dst, Box) else dst
        candidate = Link(source.id, int(outlet), target.id, int(inlet))
        if candidate in self.links:
            self.links.remove(candidate)
            return True
        return False

    # -- lookup ---------------------------------------------------------
    def box(self, box_id) -> Box:
        if isinstance(box_id, Box):
            return self._known(box_id)
        for box in self.boxes:
            if box.id == box_id:
                return box
        raise PatchError(f"patch '{self.id}': no box with id '{box_id}'")

    def _known(self, box: Box) -> Box:
        if box in self.boxes:
            return box
        raise PatchError(
            f"patch '{self.id}': box '{box.id}' belongs to a different patch"
        )

    def index(self, box_id) -> int:
        return self.boxes.index(self.box(box_id))

    def table(self, name: str) -> Box:
        wanted = unscope_table_name(normalize_text(name))
        for box in self.boxes:
            if box.is_table and unscope_table_name(box.table_name) == wanted:
                return box
        raise PatchError(f"patch '{self.id}': no table called '{name}'")

    def tables(self) -> Dict[str, str]:
        """``script name -> Pd name`` for every table on the canvas."""
        return {
            unscope_table_name(box.table_name): box.table_name
            for box in self.boxes
            if box.is_table
        }

    def outlets_of(self, box_id) -> int:
        """How many outlets a box has been wired from (0 when it is a sink)."""
        box = self.box(box_id)
        return max((link.outlet + 1 for link in self.links if link.src == box.id),
                   default=0)

    # -- validation -----------------------------------------------------
    def validate(self) -> List[str]:
        """Raise :class:`PatchError` on anything unloadable, return warnings.

        Hard errors are the ones that make the file invalid or the intent
        ambiguous (missing text, duplicate ids, a link to a box that does not
        exist, a table of zero length). Everything Pd itself will resolve or
        simply ignore - unknown object classes, boxes nobody wired - is a
        warning, because the patch still opens and the author may be building
        it in steps.
        """
        errors: List[str] = []
        warnings: List[str] = []

        seen_ids = set()
        table_names: Dict[str, str] = {}
        for box in self.boxes:
            if box.id in seen_ids:
                errors.append(f"duplicate box id '{box.id}'")
            seen_ids.add(box.id)

            if box.kind in ("obj", "msg", "text", "table") and not box.text:
                errors.append(f"box '{box.id}' ({box.kind}) has no text")
            if box.raw:
                # the verbatim path skips the writer's escaping, so what escaping
                # exists to contain has to be checked here instead. A structural
                # character or a trailing open escape would end the line early and
                # have the rest of the patch read as boxes nobody authored.
                if "\n" in box.text or "\r" in box.text:
                    errors.append(
                        f"box '{box.id}': verbatim text has to be one line"
                    )
                elif unescaped(box.text, ";"):
                    errors.append(
                        f"box '{box.id}': verbatim text cannot hold an "
                        f"unescaped ';' (it ends the line)"
                    )
                elif ends_with_an_open_escape(box.text):
                    errors.append(
                        f"box '{box.id}': verbatim text ends in an unterminated "
                        f"escape"
                    )
            if box.kind == "table":
                if box.size < 1:
                    errors.append(f"table '{box.text}' must be at least 1 sample long")
                if box.table_name in table_names:
                    errors.append(f"table '{box.text}' is declared twice")
                table_names[box.table_name] = box.id

        for link in self.links:
            if link.src not in seen_ids:
                errors.append(f"link {link.describe()}: no box '{link.src}'")
            if link.dst not in seen_ids:
                errors.append(f"link {link.describe()}: no box '{link.dst}'")
            if link.outlet < 0 or link.inlet < 0:
                errors.append(
                    f"link {link.describe()}: outlet and inlet must be >= 0"
                )

        connected = {link.src for link in self.links} | {link.dst for link in self.links}
        for box in self.boxes:
            if box.kind not in LINKABLE_KINDS or box.id in connected:
                continue
            # A box that talks to Pd by name is doing something on its own: no
            # wire needed, so no warning.
            if box.recv or box.send or _is_named_io(box) or _is_decoration(box):
                continue
            warnings.append(f"box '{box.id}' ({box.text or box.kind}) is not connected")

        if errors:
            raise PatchError(f"patch '{self.id}': " + "; ".join(errors))
        self.warnings = warnings
        return warnings

    # -- helpers --------------------------------------------------------
    def _next_id(self, kind: str) -> str:
        counter = self._counters.get(kind, 0) + 1
        self._counters[kind] = counter
        return f"{kind}{counter}"

    @staticmethod
    def _table_size(size: int, init: Sequence[float]) -> int:
        try:
            size = int(size or 0)
        except (TypeError, ValueError):
            raise PatchError(f"a table size must be a whole number, got {size!r}")
        if size <= 0:
            size = len(init) if init else 0
        if size < 1:
            raise PatchError("a table must be at least 1 sample long")
        return size

    @staticmethod
    def _table_data(size: int, init: Sequence[float]) -> List[float]:
        values = [float(value) for value in (init or ())]
        if len(values) > size:
            return values[:size]
        return values + [0.0] * (size - len(values))

    # -- dunder ---------------------------------------------------------
    def __eq__(self, other):
        if not isinstance(other, PatchDoc):
            return NotImplemented
        # Boxes keep their order (it is what ``connect`` indices refer to); the
        # link *set* is what matters, since the writer sorts them by position.
        return (
            self.id == other.id
            and self.canvas == other.canvas
            and self.boxes == other.boxes
            and set(self.links) == set(other.links)
        )

    def __repr__(self):
        return (
            f"<PatchDoc {self.id} {len(self.boxes)} boxes "
            f"{len(self.links)} links>"
        )
