"""``crude glyphs``: the icon set as a table, as files, and as a grid.

The rule these tests hold is the one the command exists for: what it writes is what
the renderer would have drawn. So the expected pixels come out of a
:class:`~crude.ui.glyphs.GlyphAtlas` built the same way and compared against the PNG
on disk, and the expected colours come out of a :class:`~crude.ui.skin.Skin` rather
than being written down - a dump that quietly drifted from the texture is exactly
the failure a dump tool has to catch.

Nothing here needs a GPU. The preview is asserted against a
:class:`~crude.ui.recording.RecordingRenderer` and a hand-rolled surface, and the
``--preview`` mode that would really open a window is checked with
``open_display`` monkeypatched, which is also how "no display here" is proven to be
said rather than crashed.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import numpy as np
import pytest
from PIL import Image

from crude.cli import (EXIT_LOAD, EXIT_MANIFEST, EXIT_OK, EXIT_RUNTIME, main)
from crude.commands.glyphs import GLYPH_SCHEMA, GlyphPreview
from crude.ui import GLYPHS, GlyphAtlas, Skin, icon_names
from crude.ui.base import WindowUnavailable
from crude.ui.input import ACTIONS
from crude.ui.recording import RecordingRenderer

#: the two icons a test asks for when it does not want all thirty-two
FEW = "play,stop"


class Output(NamedTuple):
    """What one command line ended with: its code and its two streams."""

    code: int
    out: str
    err: str

    def lines(self) -> Tuple[List[str], List[str]]:
        return self.out.splitlines(), self.err.splitlines()

    def json(self) -> Dict[str, Any]:
        """The stdout, as the one document it is supposed to be."""
        return json.loads(self.out)


@pytest.fixture
def run(capsys):
    """``run([...])``: the code, and everything the command printed.

    Read once per run: ``readouterr`` takes the buffer rather than peeking at it.
    """
    def go(argv: List[str]) -> Output:
        code = main(argv)
        captured = capsys.readouterr()
        return Output(code, captured.out, captured.err)

    return go


@pytest.fixture
def dark() -> Skin:
    """The skin a run with no ``--theme`` uses, for its two colours."""
    return Skin()


def png(path: str) -> np.ndarray:
    with Image.open(path) as opened:
        assert opened.mode == "RGBA"
        return np.asarray(opened)


def plate(colour: Any) -> Tuple[int, int, int, int]:
    """A skin colour as the bytes a PNG holds."""
    return colour.as_bytes()


# -- the listing ---------------------------------------------------------


def test_the_listing_prints_one_name_per_line_and_nothing_else(run):
    result = run(["glyphs"])
    out, err = result.lines()

    assert result.code == EXIT_OK
    assert out == list(icon_names())
    assert not err


def test_names_can_be_asked_for_as_a_list(run):
    result = run(["glyphs", "--names", f"{FEW},play"])

    assert result.code == EXIT_OK
    assert result.out.splitlines() == ["play", "stop"]


def test_an_icon_that_is_not_in_the_set_is_refused(run):
    result = run(["glyphs", "--names", "nosuch"])

    assert result.code == EXIT_LOAD
    assert not result.out
    assert result.err.startswith("crude glyphs:")
    assert "nosuch" in result.err


def test_nothing_at_all_to_draw_is_refused_rather_than_the_whole_set(run):
    result = run(["glyphs", "--names", " , "])

    assert result.code == EXIT_LOAD
    assert "nothing to draw" in result.err


# -- the table -----------------------------------------------------------


def test_the_json_is_the_table_the_atlas_rasterizes_from(run):
    result = run(["glyphs", "--json"])

    assert result.code == EXIT_OK
    said = result.json()
    assert said["schema"] == GLYPH_SCHEMA
    assert said["count"] == len(GLYPHS)
    assert said["names"] == list(icon_names())
    assert said["box"] == 100.0 and said["bleed"] == 8.0
    assert said["ladder"] == [12, 16, 20, 24, 32]
    assert [row["name"] for row in said["glyphs"]] == list(icon_names())


def test_a_json_row_is_the_layers_the_glyph_is_drawn_with(run):
    result = run(["glyphs", "--json", "--names", FEW])

    assert result.code == EXIT_OK
    said = result.json()
    assert [row["name"] for row in said["glyphs"]] == ["play", "stop"]
    assert said["glyphs"][0]["layers"] == [
        {"path": layer.path, "fill": layer.fill, "stroke": layer.stroke,
         "width": layer.width} for layer in GLYPHS["play"].layers]


def test_show_prints_the_paths_one_icon_is_made_of(run):
    result = run(["glyphs", "--show", "pause"])
    out, _ = result.lines()

    assert result.code == EXIT_OK
    assert out[0] == "pause  2 layer(s)"
    for layer in GLYPHS["pause"].layers:
        assert any(layer.path in line for line in out), layer.path


def test_one_icon_as_json_is_that_icon_alone(run):
    result = run(["glyphs", "--show", "play", "--json"])

    assert result.code == EXIT_OK
    said = result.json()
    assert said["schema"] == GLYPH_SCHEMA
    assert said["name"] == "play"
    assert len(said["layers"]) == 1


def test_an_icon_that_has_no_path_is_refused(run):
    result = run(["glyphs", "--show", "nosuch"])

    assert result.code == EXIT_LOAD
    assert not result.out
    assert "nosuch" in result.err


# -- writing PNGs --------------------------------------------------------


def test_a_dump_writes_the_whole_set_flattened_onto_the_theme(run, tmp_path,
                                                              dark):
    where = str(tmp_path / "icons")

    assert run(["glyphs", "--dump", where]).code == EXIT_OK

    assert sorted(os.listdir(where)) == sorted(f"{name}.png"
                                               for name in icon_names())
    pixels = png(os.path.join(where, "play.png"))
    # the corner of a play triangle is empty box, so what shows there is the plate
    assert tuple(pixels[0, 0]) == plate(dark.colour("surface"))
    # and a flattened file is opaque, which is the whole point of --over
    assert (pixels[..., 3] == 255).all()


def test_the_ink_of_a_dump_is_the_colour_an_icon_is_drawn_in(run, tmp_path,
                                                             dark):
    where = str(tmp_path / "icons")

    run(["glyphs", "--dump", where, "--names", "stop"])

    pixels = png(os.path.join(where, "stop.png"))
    middle = pixels.shape[0] // 2
    assert tuple(pixels[middle, middle]) == plate(dark.ink("icon"))


def test_a_dump_can_be_told_which_icons_and_what_size(run, tmp_path):
    where = str(tmp_path / "few")

    assert run(["glyphs", "--dump", where, "--names", FEW,
                "--size", "12"]).code == EXIT_OK

    assert sorted(os.listdir(where)) == ["play.png", "stop.png"]
    assert png(os.path.join(where, "stop.png")).shape == (12, 12, 4)


def test_the_device_ratio_widens_the_raster_the_way_a_window_would(run,
                                                                   tmp_path):
    where = str(tmp_path / "hidpi")

    assert run(["glyphs", "--dump", where, "--names", "stop", "--size", "12",
                "--dpr", "2"]).code == EXIT_OK

    # 12 logical pixels at 2x is a 24 px raster, which is the device size the
    # ladder lands on and the one the atlas would hold
    assert png(os.path.join(where, "stop.png")).shape == (24, 24, 4)


def test_raw_writes_the_texture_exactly_as_the_renderer_gets_it(run, tmp_path):
    where = str(tmp_path / "raw")

    assert run(["glyphs", "--dump", where, "--names", FEW, "--size", "20",
                "--raw"]).code == EXIT_OK

    atlas = GlyphAtlas()
    for name in ("play", "stop"):
        assert np.array_equal(png(os.path.join(where, f"{name}.png")),
                              atlas.pixels(name, 20))
    # and the empty corner of the texture is empty, not theme-coloured
    assert tuple(png(os.path.join(where, "play.png"))[0, 0]) == (255, 255, 255, 0)


def test_the_two_colours_a_dump_is_flattened_with_can_be_named(run, tmp_path):
    where = str(tmp_path / "mixed")

    assert run(["glyphs", "--dump", where, "--names", "stop", "--over",
                "#ff0000", "--ink", "green"]).code == EXIT_OK

    pixels = png(os.path.join(where, "stop.png"))
    assert tuple(pixels[0, 0]) == (255, 0, 0, 255)
    middle = pixels.shape[0] // 2
    assert pixels[middle, middle][1] > 128 and pixels[middle, middle][0] < 128


def test_a_theme_moves_the_plate_a_dump_is_flattened_onto(run, tmp_path):
    where = str(tmp_path / "light")
    skin = Skin("light")

    assert run(["glyphs", "--dump", where, "--names", "play", "--theme",
                "light"]).code == EXIT_OK

    assert tuple(png(os.path.join(where, "play.png"))[0, 0]) == \
        plate(skin.colour("surface"))


def test_a_colour_that_is_not_one_is_refused_before_anything_is_written(
        run, tmp_path):
    where = str(tmp_path / "none")

    result = run(["glyphs", "--dump", where, "--over", "notacolour"])

    assert result.code == EXIT_LOAD
    assert not result.out
    assert "notacolour" in result.err
    assert not os.path.exists(where)


def test_a_size_of_zero_is_refused_by_the_argument_parser():
    with pytest.raises(SystemExit) as caught:
        main(["glyphs", "--dump", "somewhere", "--size", "0"])

    assert caught.value.code == EXIT_MANIFEST


def test_the_atlas_file_is_the_one_texture_the_renderer_uploads(run, tmp_path,
                                                                dark):
    path = str(tmp_path / "atlas.png")

    assert run(["glyphs", "--atlas", path]).code == EXIT_OK

    atlas = GlyphAtlas()
    for name in icon_names():
        atlas.cell(name, 32)
    written = png(path)
    assert written.shape[:2] == (atlas.height, atlas.width)
    # every icon of every size the atlas knows is a cell in it
    assert len(atlas.cells()) == len(icon_names())
    # flattened, so a person can look at it, and the plate shows between cells
    assert tuple(written[0, 0]) == plate(dark.colour("surface"))


def test_the_atlas_can_be_written_as_the_texture_it_is(run, tmp_path):
    path = str(tmp_path / "texture.png")

    assert run(["glyphs", "--atlas", path, "--names", FEW, "--raw"]).code == EXIT_OK

    atlas = GlyphAtlas()
    for name in ("play", "stop"):
        atlas.cell(name, 32)
    assert np.array_equal(png(path), atlas.array())


# -- the grid ------------------------------------------------------------


def test_the_preview_draws_one_plate_one_icon_and_one_label_each():
    renderer = RecordingRenderer()
    preview = GlyphPreview(renderer, skin=Skin(), names=("play", "stop"),
                           size=32, columns=2)

    ops = preview.draw()

    assert ops == renderer.op_count
    assert renderer.count("rect") == 2
    assert renderer.count("atlas") == 2  # one quad each, out of the shared texture
    assert renderer.texts() == ["play", "stop"]
    assert renderer.first("atlas").get("texture") == "crude:glyphs@1"


def test_the_grid_puts_the_icons_in_rows_of_the_column_count():
    preview = GlyphPreview(RecordingRenderer(), names=icon_names(), size=32,
                           columns=8)

    box = preview.layout()

    assert box["columns"] == 8
    assert box["rows"] == -(-len(GLYPHS) // 8)
    assert box["width"] == box["margin"] * 2 + box["columns"] * box["cell"]
    assert box["height"] >= box["rows"] * box["step"]


def test_the_icons_are_in_the_boxes_the_grid_said_they_would_be():
    renderer = RecordingRenderer()
    preview = GlyphPreview(renderer, names=("play", "stop"), size=32,
                           columns=2)
    box = preview.layout()

    preview.draw()

    plates = renderer.ops_named("rect")
    assert plates[0].get("x") == pytest.approx(box["margin"])
    # the second icon of a two-column row sits one step along, not two margins in
    assert plates[1].get("x") == pytest.approx(box["margin"] + box["step"])


def test_a_preview_with_no_window_draws_the_one_frame():
    preview = GlyphPreview(RecordingRenderer(), names=("play",), size=32)

    assert preview.run() == 1
    assert preview.frames == 1
    assert not preview.gone


class FakeWindow:
    """A window that remembers being asked to close, and takes a key handler."""

    def __init__(self) -> None:
        self.on_key: Optional[Any] = None
        self.closes = 0

    def request_close(self) -> None:
        self.closes += 1


class FakeDisplay:
    """A surface that presses escape the second time it is pumped, then dies."""

    def __init__(self, renderer: Any, *, width: int = 800,
                 height: int = 600) -> None:
        self.window = FakeWindow()
        self.renderer = renderer
        self.width = width
        self.height = height
        self.frames = 0
        self.presents = 0
        self.closed = False
        self.waited: List[float] = []
        self._pumps = 0

    def frame(self) -> "FakeDisplay":
        self.frames += 1
        self.renderer.begin(self.width, self.height)
        return self

    def present(self) -> int:
        self.presents += 1
        return self.renderer.end()

    def pump(self, *, wait: float = 0.0) -> bool:
        self._pumps += 1
        self.waited.append(wait)
        if self._pumps == 2 and self.window.on_key is not None:
            self.window.on_key(256, 0, ACTIONS["press"], 0)
        return self._pumps < 3

    def close(self) -> None:
        self.closed = True


def test_escape_closes_the_preview(run, monkeypatch):
    display = FakeDisplay(RecordingRenderer())
    monkeypatch.setattr("crude.ui.gl.open_display",
                        lambda *args, **kwargs: display)

    assert run(["glyphs", "--preview", "--names", FEW]).code == EXIT_OK
    assert display.window.closes == 1
    assert display.closed is True
    # the loop waits in the OS between frames rather than spinning: a pump that
    # only ever polled would burn a core while nobody was looking at the grid
    assert max(display.waited) > 0


def test_the_preview_window_is_the_size_the_grid_needs(run, monkeypatch):
    seen: List[Tuple[int, int, str]] = []
    display = FakeDisplay(RecordingRenderer())

    def opener(width: int, height: int, title: str, **kwargs: Any) -> Any:
        seen.append((width, height, title))
        return display

    monkeypatch.setattr("crude.ui.gl.open_display", opener)

    assert run(["glyphs", "--preview", "--names", FEW, "--columns", "2",
                "--size", "32"]).code == EXIT_OK

    width, height, title = seen[0]
    box = GlyphPreview(RecordingRenderer(), names=("play", "stop"), size=32,
                       columns=2).layout()
    assert width == int(box["width"]) and height == int(box["height"])
    assert "glyphs" in title


def test_a_machine_with_no_display_is_told_so_and_ends_five(run, monkeypatch):
    def refusing(*args, **kwargs):
        raise WindowUnavailable("glfw says there is no display here")

    monkeypatch.setattr("crude.ui.gl.open_display", refusing)

    result = run(["glyphs", "--preview"])

    assert result.code == EXIT_RUNTIME
    assert not result.out
    assert "no display" in result.err
