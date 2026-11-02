"""``crude glyphs``: the generated icon set, listed, written out and previewed.

Every icon is a path in :data:`crude.ui.glyphs.GLYPHS` until something asks for its
pixels, so the set has no files to ship and no files to look at either. This command
is how both happen without a window: the table prints as text or as JSON, ``--dump``
writes one PNG per icon, ``--atlas`` writes the single texture the renderer uploads,
and ``--preview`` - the one mode that needs GL - shows the whole set in a grid.

Nothing here re-rasterizes anything by hand. :func:`crude.ui.glyphs.dump_icons` and
:meth:`crude.ui.glyphs.GlyphAtlas.array` are the same calls the renderer is built on,
so the PNG a person inspects is byte for byte what the screen showed, which is the
only way a dump can be used to check the art.

The colours come from the skin, because an icon has no colour of its own: the atlas
is white ink on transparency and the tint is a per-draw multiply. So a dump that is
meant to be *looked at* flattens onto the theme's surface with the theme's icon ink,
and ``--raw`` says otherwise - then the file holds the texture exactly as the GPU
gets it, invisible in a viewer and right for feeding back into a build.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..cli import EXIT_LOAD, EXIT_OK, EXIT_RUNTIME, command, configure_parser
from ..ui.base import UIError, as_color

#: what a JSON reader checks to know whether ``layers`` holds dicts and whether
#: ``glyphs`` is the whole table: this string and nothing else
GLYPH_SCHEMA = "crude/glyphs@1"

#: the side, in logical pixels, an icon is written or drawn at when nobody says
DEFAULT_SIZE = 32
#: how many icons across a preview row
DEFAULT_COLUMNS = 8
#: the frame period of the preview loop, so an idle window costs nothing
FRAME_WAIT = 1.0 / 30.0


@command("glyphs", "list, dump or preview the generated icon set")
def glyphs_command(args: argparse.Namespace) -> int:
    """Do the one thing that was asked, against the one table there is."""
    names, problem = _wanted(args.names)
    if problem:
        return _fail(problem)
    skin, problem = _skin(args.theme)
    if problem:
        return _fail(problem)

    if args.show:
        return _show(args.show, as_json=args.json)
    if args.json:
        print(json.dumps(_document(skin, names), indent=2, default=str))
        return EXIT_OK
    if args.dump:
        return _dump(args, skin, names)
    if args.atlas:
        return _atlas(args, skin, names)
    if args.preview:
        return _preview(args, skin, names)
    # nothing but a list was asked for, so one name per line and nothing else on
    # stdout: `crude glyphs | head` and `crude glyphs --names` both read this
    for name in names:
        print(name)
    return EXIT_OK


# -- the table ----------------------------------------------------------


def _document(skin: Any, names: Sequence[str]) -> Dict[str, Any]:
    """The glyph set as one JSON document: the rules, then every picture.

    The rules are in it because a raster is a function of them - ``BOX`` plus
    ``BLEED`` mapped through ``SUPER_SAMPLE`` and snapped up ``LADDER`` - and a
    reader that wants to know why a 20 px icon is 20 device pixels wide can only
    be answered by the numbers the atlas itself works from.
    """
    from ..ui import GLYPHS, icon_names
    from ..ui.glyphs import (ATLAS_BASE, BLEED, BOX, FALLBACK, LADDER, PADDING,
                             SUPER_SAMPLE)

    return {
        "schema": GLYPH_SCHEMA,
        "box": BOX,
        "bleed": BLEED,
        "ladder": list(LADDER),
        "super_sample": SUPER_SAMPLE,
        "padding": PADDING,
        "atlas_base": ATLAS_BASE,
        "fallback": FALLBACK,
        "count": len(GLYPHS),
        "names": list(icon_names()),
        "theme": skin.theme.name,
        "surface": str(skin.colour("surface")),
        "ink": str(skin.ink("icon")),
        "glyphs": [_glyph_document(GLYPHS[name]) for name in (names or GLYPHS)],
    }


def _glyph_document(defn: Any) -> Dict[str, Any]:
    """One row of the table: its name and the layers that make it up."""
    return {
        "name": defn.name,
        "layers": [{"path": layer.path, "fill": layer.fill,
                    "stroke": layer.stroke, "width": layer.width}
                   for layer in defn.layers],
    }


def _show(name: str, *, as_json: bool = False) -> int:
    """``--show NAME``: the path or paths one icon is drawn from.

    Printed rather than rasterized, because the question this answers is "how is
    that shape made", and the answer is a string a person could put in a glyph of
    their own.
    """
    from ..ui import GLYPHS, has_icon, icon_names

    if not has_icon(name):
        return _fail(f"no icon '{name}'; crude glyphs prints the set with no "
                     f"arguments")
    defn = GLYPHS[name]
    if as_json:
        print(json.dumps({"schema": GLYPH_SCHEMA, **_glyph_document(defn)},
                         indent=2, default=str))
        return EXIT_OK
    print(f"{defn.name}  {len(defn.layers)} layer(s)")
    for layer in defn.layers:
        kind = "fill " if layer.fill else ""
        if layer.stroke:
            kind += f"stroke {layer.width:g}"
        print(f"  {kind:<14} {layer.path}")
    print(f"  {len(icon_names())} icon(s) in the set")
    return EXIT_OK


# -- writing files ------------------------------------------------------


def _dump(args: argparse.Namespace, skin: Any,
          names: Sequence[str]) -> int:
    """``--dump DIR``: one PNG per icon, at the size the atlas would rasterize."""
    from ..ui import GlyphAtlas, dump_icons

    over, ink, problem = _plate(args, skin)
    if problem:
        return _fail(problem)
    atlas = GlyphAtlas(dpr=args.dpr)
    try:
        written = dump_icons(atlas, args.dump, size=args.size,
                             names=list(names), over=over, ink=ink)
    except OSError as exc:
        return _fail(f"'{args.dump}' could not be written: "
                     f"{exc.strerror or exc}", EXIT_RUNTIME)
    print(f"{len(written)} PNG(s) at {args.size}px, dpr {args.dpr:g}, "
          f"into '{args.dump}'{_said(over, ink, args.raw)}")
    return EXIT_OK


def _atlas(args: argparse.Namespace, skin: Any, names: Sequence[str]) -> int:
    """``--atlas FILE``: the whole packed texture as one PNG.

    Every icon is asked for first, because an atlas only holds what was drawn -
    and a picture of it that quietly left out the icons nobody rasterized would
    describe a texture no frame ever used.
    """
    from PIL import Image

    from ..ui import GlyphAtlas, flatten_pixels

    over, ink, problem = _plate(args, skin)
    if problem:
        return _fail(problem)
    atlas = GlyphAtlas(dpr=args.dpr)
    for name in names:
        atlas.cell(name, args.size)
    image = Image.fromarray(flatten_pixels(atlas.array(), over, ink),
                            mode="RGBA")
    problem = _write(image, args.atlas)
    if problem:
        return _fail(problem, EXIT_RUNTIME)
    stats = atlas.stats()
    print(f"'{args.atlas}' is the {stats['width']}x{stats['height']} atlas, "
          f"{stats['cells']} cell(s){_said(over, ink, args.raw)}")
    return EXIT_OK


def _write(image: Any, path: str) -> str:
    """Save one PNG; the reason it could not be, said in the person's terms."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        image.save(path)
    except OSError as exc:
        return f"'{path}' could not be written: {exc.strerror or exc}"
    return ""


def _all_names() -> Tuple[str, ...]:
    from ..ui import icon_names

    return icon_names()


def _plate(args: argparse.Namespace, skin: Any) -> Tuple[Any, Any, str]:
    """The two colours a written icon is made of: ``(plate, ink, what is wrong)``.

    ``None`` for the plate is the texture itself, so ``--raw`` is one argument and
    not a second way to composite. A colour named on the command line is read by
    :func:`~crude.ui.base.as_color`, which is the same reader a script's ``STYLE``
    uses - ``#rrggbb`` or one of the names in :data:`~crude.ui.base.NAMED`.
    """
    try:
        over = None if args.raw else as_color(args.over or skin.colour("surface"))
        ink = as_color(args.ink or skin.ink("icon"))
    except UIError as exc:
        return None, None, str(exc)
    return over, ink, ""


def _said(over: Any, ink: Any, raw: bool) -> str:
    """How the last line describes what was flattened onto what."""
    if raw or over is None:
        return ", the raw texture"
    return f", {ink} on {over}"


# -- the window ---------------------------------------------------------


def _preview(args: argparse.Namespace, skin: Any, names: Sequence[str]) -> int:
    """``--preview``: the whole set in a grid, one quad per icon.

    The only mode here that wants a display, so :mod:`crude.ui.gl` is imported
    inside the call like everywhere else that opens a window: a machine with no GL
    can still list, dump and show paths, and this says "no window" instead of
    drawing nothing.
    """
    from ..ui import NullRenderer
    from ..ui.base import WindowUnavailable

    plan = GlyphPreview(NullRenderer(), skin=skin, size=args.size,
                        names=names, columns=args.columns)
    box = plan.layout()
    try:
        from ..ui.gl import open_display

        display = open_display(int(box["width"]), int(box["height"]),
                               f"crude glyphs - {len(names)} icons")
    except WindowUnavailable as exc:
        return _fail(str(exc), EXIT_RUNTIME)
    except Exception as exc:  # a driver that says no in its own words
        return _fail(f"the window could not be opened: {exc}", EXIT_RUNTIME)

    preview = GlyphPreview(display.renderer, display, skin=skin,
                           size=args.size, names=names,
                           columns=args.columns)
    print(f"{len(names)} icon(s) at {args.size:g}px, {box['columns']} across - "
          f"escape or close the window", flush=True)
    try:
        preview.run()
    finally:
        # the window is owed a clean close whether the loop ended on escape, on a
        # driver going away, or on a KeyboardInterrupt
        preview.close()
    return EXIT_OK


class GlyphPreview:
    """Draws the icon set in a grid, using nothing but the renderer contract.

    The same shape as :class:`~crude.ui.splash.SplashCanvas`: a renderer, an
    optional surface that puts the finished frame on screen, and one ``draw`` per
    frame. With no surface it records into whatever renderer it was given, which is
    how a test checks the grid - one plate, one glyph, one label per icon - without
    a GPU.

    Every icon is drawn through :meth:`~crude.ui.base.Renderer.glyph`, so the
    preview shows the texture the program actually uses rather than a copy of the
    pictures, and the atlas it builds is the renderer's own.
    """

    def __init__(self, renderer: Any, surface: Any = None, *, skin: Any = None,
                 size: int = DEFAULT_SIZE, names: Optional[Sequence[str]] = None,
                 columns: int = DEFAULT_COLUMNS) -> None:
        if skin is None:
            from ..ui import Skin

            skin = Skin()
        self.renderer = renderer
        #: what puts a finished frame on screen: a ``Display``, or nothing in a test
        self.surface = surface
        self.skin = skin
        self.size = max(4.0, float(size))
        self.columns = max(1, int(columns))
        self.names = tuple(names) if names else tuple(_all_names())
        #: the frames drawn, which is what a test counts
        self.frames = 0
        #: set when the surface stops answering
        self.gone = False
        self._installed = False

    # -- the geometry ----------------------------------------------------
    def layout(self) -> Dict[str, Any]:
        """The grid: margin, cell, label line, and the window that holds them.

        A row is the icon plus the room its name needs underneath, so a label can
        never be drawn over the next icon, and the width is one cell short of a
        full row when there are fewer icons than ``columns``.
        """
        margin = max(8.0, self.size * 0.5)
        label = max(9.0, self.size * 0.35)
        rows = max(1, -(-len(self.names) // self.columns))
        cells = min(self.columns, len(self.names)) or 1
        cell = self.size + margin * 0.5
        step = cell + label
        return {
            "margin": margin,
            "label": label,
            "cell": cell,
            "step": step,
            "columns": cells,
            "rows": rows,
            "width": margin * 2 + cells * cell,
            "height": margin * 2 + rows * step,
        }

    # -- one frame -------------------------------------------------------
    def draw(self) -> int:
        """Paint the grid once; how many ops went into the frame."""
        if self.gone:
            return 0
        self._pump()
        if self.gone:
            return 0
        box = self.layout()
        if self.surface is not None:
            self.surface.frame()
        else:
            self.renderer.begin(box["width"], box["height"])
        # the grid is laid out from the icons and anchored at the top left, so a
        # window that was dragged wider keeps the same pictures in the same places
        renderer = self.renderer
        skin = self.skin
        renderer.clear(skin.colour("bg"))
        try:
            for offset, name in enumerate(self.names):
                column, row = offset % box["columns"], offset // box["columns"]
                left = box["margin"] + column * box["step"]
                top = box["margin"] + row * box["step"]
                side = box["cell"] - box["margin"] * 0.25
                renderer.rect(left, top, side, side, skin.colour("surface"),
                              radius=side * 0.12)
                renderer.glyph(left + side * 0.15, top + side * 0.15,
                               side * 0.7, side * 0.7, name,
                               color=skin.ink("icon"))
                renderer.text(left + side * 0.5, top + side + box["label"] * 0.2,
                              name, box["label"], skin.ink("caption"),
                              align="center")
        finally:
            if self.surface is not None:
                self.surface.present()
            else:
                self.renderer.end()
        self.frames += 1
        return renderer.op_count

    # -- the loop --------------------------------------------------------
    def run(self, *, frames: Optional[int] = None,
            seconds: float = 0.0) -> int:
        """Draw until the window is closed, or until told; how many frames went by.

        The pump waits between frames rather than the loop sleeping, which is the
        difference between a preview that costs nothing and one that burns a core
        while nobody is looking at it.
        """
        import time

        started = time.monotonic()
        first = True
        drawn = 0
        while True:
            if not self._pump(wait=not first):
                break
            first = False
            self.draw()
            drawn += 1
            if frames is not None and drawn >= int(frames):
                break
            if seconds and (time.monotonic() - started) >= float(seconds):
                break
            if not self._loops:
                # no window to wait for, so a preview over a recorder is one frame
                break
        return drawn

    @property
    def _loops(self) -> bool:
        """Whether there is a window to keep drawing for."""
        return callable(getattr(self.surface, "pump", None))

    def _pump(self, *, wait: bool = False) -> bool:
        """Let the window talk, install the escape key, and report whether it lived."""
        pump = getattr(self.surface, "pump", None)
        if not callable(pump):
            return not self.gone
        if not self._installed:
            self._watch()
        if not pump(wait=FRAME_WAIT if wait else 0.0):
            self.gone = True
            return False
        return True

    def _watch(self) -> None:
        """Give the window something to close itself with."""
        window = getattr(self.surface, "window", None)
        self._installed = True
        if window is None or getattr(window, "on_key", None) is not None:
            # a window that already has a listener is somebody else's, and the
            # preview is not entitled to quietly take the keyboard off it
            return
        window.on_key = self._on_key

    def _on_key(self, key: int, scancode: int, action: int,
                mods: int) -> None:
        """Escape closes the preview, which is the only key it claims."""
        from ..ui import ACTIONS, key_name

        if key_name(key) != "escape":
            return
        if action not in (ACTIONS["press"], ACTIONS["repeat"]):
            return
        window = getattr(self.surface, "window", None)
        if window is not None:
            window.request_close()

    def close(self) -> None:
        """Put the window back; a preview with no surface has nothing to close."""
        close = getattr(self.surface, "close", None)
        if callable(close):
            close()

    def stats(self) -> Dict[str, Any]:
        box = self.layout()
        return {"canvas": "glyphs", "icons": len(self.names),
                "frames": self.frames, "size": self.size,
                "columns": box["columns"], "rows": box["rows"],
                "width": box["width"], "height": box["height"],
                "gone": self.gone, "renderer": self.renderer.stats()}

    def __str__(self) -> str:
        return (f"GlyphPreview({len(self.names)} icons at {self.size:g}px, "
                f"{self.frames} frames{', gone' if self.gone else ''})")


# -- arguments ----------------------------------------------------------


def _wanted(text: Optional[str]) -> Tuple[Tuple[str, ...], str]:
    """``--names a,b`` as a list, or the whole set; ``(names, what is wrong)``.

    An unknown name is refused rather than drawn as the placeholder: a dump is
    usually meant to be looked at afterwards, and thirty-two files of which two are
    empty boxes is a slow way to find a typo.
    """
    from ..ui import has_icon

    if not text:
        return _all_names(), ""
    names: List[str] = []
    for part in str(text).replace(";", ",").split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    if not names:
        return (), "--names was given nothing to draw"
    missing = [name for name in names if not has_icon(name)]
    if missing:
        return (), (f"no icon '{missing[0]}'"
                    + (f" (also {', '.join(missing[1:])})" if len(missing) > 1
                       else "")
                    + "; the set is printed with no arguments")
    return tuple(names), ""


def _skin(theme: Optional[str]) -> Tuple[Any, str]:
    """The skin the colours are read from; ``None`` from the CLI is the default."""
    from ..ui import Skin

    try:
        return Skin(theme), ""
    except UIError as exc:
        return None, str(exc)


def _fail(message: str, code: int = EXIT_LOAD) -> int:
    print(f"crude glyphs: {message}", file=sys.stderr)
    return code


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not a number") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{value} is smaller than one")
    return value


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not a number") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"{value} does not draw anything")
    return value


@configure_parser("glyphs")
def glyphs_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true",
                        help="the glyph table and the rules it is rasterized by, "
                             "as one JSON document")
    parser.add_argument("--show", default=None, metavar="NAME",
                        help="print one icon's path or paths")
    parser.add_argument("--names", default=None, metavar="A,B",
                        help="only these icons, instead of the whole set")
    parser.add_argument("--theme", default=None, metavar="NAME",
                        help="take the plate and ink colours from this theme")

    writer = parser.add_argument_group("writing")
    writer.add_argument("--dump", default=None, metavar="DIR",
                        help="write one PNG per icon into this directory")
    writer.add_argument("--atlas", default=None, metavar="FILE",
                        help="write the whole packed texture as one PNG")
    writer.add_argument("--size", type=_positive_int, default=DEFAULT_SIZE,
                        metavar="N", help="logical pixels per icon "
                                          f"(default {DEFAULT_SIZE})")
    writer.add_argument("--dpr", type=_positive_float, default=1.0, metavar="F",
                        help="device pixels per logical pixel, the same number a "
                             "window reports")
    writer.add_argument("--over", default=None, metavar="COLOUR",
                        help="flatten onto this colour instead of the theme's "
                             "surface")
    writer.add_argument("--ink", default=None, metavar="COLOUR",
                        help="draw the marks in this colour instead of the "
                             "theme's icon ink")
    writer.add_argument("--raw", action="store_true",
                        help="write the texture unchanged: white ink on "
                             "transparency, exactly as the GPU receives it")

    window = parser.add_argument_group("looking")
    window.add_argument("--preview", action="store_true",
                        help="open a window with the set in a grid (needs GL)")
    window.add_argument("--columns", type=_positive_int, default=DEFAULT_COLUMNS,
                        metavar="N", help="icons across the preview grid "
                                          f"(default {DEFAULT_COLUMNS})")
