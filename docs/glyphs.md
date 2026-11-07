# Glyphs

A glyph is a small vector icon the widget renderer draws through a
texture atlas. The plan's Rule 16 is the whole reason the atlas exists:
one raster per `(name, size, dpr, weight)` tuple, snapped to a ladder of
sizes so a project that never changes its DPI gets a stable atlas across
runs. This page is the shape of the atlas, the built-in glyph set, and
the two CLI calls that make it visible.

## The atlas

The renderer never draws a glyph as a path. Every glyph is rasterised
once, packed into a single texture, and every later draw is one textured
quad with UV coordinates that point at the packed cell. The atlas object
lives in :mod:`crude.ui.glyph` and is owned by the renderer; a script
that says `P_GLYPH(p, 12, 12, 24, 24, "play")` reaches it through the
same renderer the widget tree uses.

**Cache key.** A glyph is looked up by the tuple `(name, size, dpr,
weight)`. Sizes snap to `12, 16, 20, 24, 32`; a request for `18` becomes
`20`, and a request for `15` becomes `16`. A weight is `"normal"` or
`"bold"` and only matters for the few glyphs that carry a stroke. A dpr
of `2.0` on a HiDPI display gets its own cell so the raster is not
rescaled by the compositor.

**Packing.** Cells go into the atlas through a shelf packer: a row is
filled left-to-right, a new row starts when the shelf cannot fit, and the
atlas grows downward. Nothing ever overlaps, and a cell's UV bounds are
recorded by the packer so a draw can pick them up without a second pass.
An overflow (a glyph bigger than the current width) rebuilds the atlas
with the next rung of the ladder; that is the only reason an atlas is
ever torn down.

**Tint.** Every atlas quad is drawn *multiplied* by a colour. The raster
itself is grayscale; a widget's `accent` role resolves to an rgba float4,
and the tint is passed to the shader as a uniform. That is what lets a
slider's handle and its fill share one glyph but read as two colours, and
why a skin's theme swap costs no re-rasterisation.

**Missing glyph.** An unknown name falls back to `circle` at the requested
size, painted in the `mute` role, and the loop notes it once. A script
that says `ICON("not-a-glyph")` gets a placeholder rather than a crash;
the plan's Rule 22 - one dict per kind is the only schema - means the
glyph set *is* the answer to "which names are here", and the fallback is
loud enough that a real typo lands on the report.

## The built-in set

Roughly two hundred glyphs ship in the built-in set, grouped by what a
widget would use them for. `crude glyphs --dump DIR` writes every one of
them as a PNG so a person can look at the set without a GPU; `crude
glyphs --preview` opens an atlas grid window (marked `requires_gl`) that
shows the packer's shelves at the current dpr.

Categories the set covers, in the names the widget catalog actually uses:

- **transport** - `play`, `pause`, `stop`, `record`, `rewind`, `forward`,
  `eject`, `loop-on`, `loop-off`
- **mixer** - `mute-on`, `mute-off`, `solo`, `arm`, `fader`, `pan-left`,
  `pan-right`
- **envelope / curve** - `adsr-shape`, `attack-hold`, `sustain`,
  `linear`, `exp-up`, `exp-down`, `sine`, `square`, `saw-up`, `saw-down`,
  `triangle`
- **io** - `in`, `out`, `midi-in`, `midi-out`, `usb`, `bluetooth`,
  `wifi`, `chain`, `unlink`
- **edit** - `plus`, `minus`, `pencil`, `eraser`, `copy`, `paste`,
  `undo`, `redo`, `save`, `open`, `close`, `search`
- **nav** - `chevron-up`, `chevron-down`, `chevron-left`, `chevron-right`,
  `dot`, `bullet`, `menu`, `settings`
- **state** - `check`, `cross`, `warn`, `info`, `heart`, `star`
- **grid** - `grid-3x3`, `grid-4x4`, `piano`, `roll`, `seq-16`, `seq-8`,
  `keyboard`, `drum-pad`
- **misc** - `circle`, `square`, `triangle`, `pill`, `waveform`, `scope`,
  `meter-peak`, `db`, `hz`, `note`, `sharp`, `flat`

The list itself lives in :mod:`crude.ui.glyph.set` as a tuple of `(name,
category, path)`, and `crude glyphs --list --json` reads it back. A
project that wants a glyph the set does not have puts a PNG under
`assets/icons/` and says `ui.icons: "generated"` in the manifest - the
generated set is still the base layer, and a per-widget PNG override
becomes a single extra cell in the atlas rather than a rebuild.

## Authoring a glyph

A glyph is a vector path the plan's parser understands. A path is a
sequence of `M`, `L`, `C`, `A` and `Z` commands, with a coordinate space
of 24 units square, and an origin at the top-left. The parser lives in
:mod:`crude.ui.glyph.path` and produces a `Polyline`, an `Arc` or a
`Polygon` (all three are renderer primitives, so a glyph draw is *the
same code path* as a widget's `P_RECT` under the hood).

Every glyph has:

```python
{
  "name": "play",
  "category": "transport",
  "path": "M 8 4 L 20 12 L 8 20 Z",
  "closed": True,
  "filled": True,
  "stroke": 0,             # 0 = no stroke; 1.5 or 2 for outlined glyphs
  "baseline": 18,          # where a mixed run of glyphs aligns
}
```

Deterministic rasterisation is the plan's Rule 16's second half: the same
path at the same size and dpr produces the same pixel bytes on every
machine, which is what a test can pin against a hash. `tests/ui/test_glyph_atlas.py`
walks a table of `(name, size, dpr)` triples and checks each one's SHA-256
against a golden; a change to the rasteriser that alters a pixel changes
the hash and the test tells you *which* glyph moved.

## Per-DPR and DPI

The atlas keys on `(name, size, dpr, weight)`. A 1x display gets the
"small" rung (12, 16, 20) and a 2x retina display gets the same names
rasterised at `(size × 2)` pixels and packed with the dpr recorded. A
resize between monitors rebuilds the atlas - the packer keeps a per-dpr
cache so a *switch back* costs nothing - and every widget's `P_GLYPH`
call reads the new UVs on the next frame.

The layout math in `widgets.md` is in logical pixels, so a glyph's UV
cell is what changes when DPI changes, not the widget's box.

## What the CLI shows you

```
crude glyphs --list [--json]     every name, category, and path in the set
crude glyphs --dump DIR          write each one as DIR/name-<size>-<dpr>.png
crude glyphs --preview           atlas grid window (requires_gl marker)
```

`--dump` is what a designer uses to look at the set without a GPU: it
walks every name at every rung and writes a PNG, which means a change to
a rasteriser is a diff in a folder rather than a "did this pixel move"
argument. `--preview` opens an actual window with the atlas laid out on
the packer's shelves so a person can eyeball a rebuild.

## Testing the atlas

`tests/ui/test_glyph_atlas.py` covers the plan's six acceptance lines:

- the path parser splits `M`/`L`/`C`/`A`/`Z` and reports a bad command
  with the column it was at;
- the raster is a deterministic hash of `(name, size, dpr, weight)` on
  the CI's Pillow build;
- the packer never overlaps two cells and grows the atlas downward;
- a UV readback hits the intended cell for every rung the ladder knows;
- tint independence: the same cell drawn under four roles produces four
  distinct RGBA outputs;
- a missing-glyph fallback returns `circle` at the requested size, once,
  with the note the loop reports;
- per-DPR cache keys do not alias a 1x and a 2x cell at the same size;
- the atlas only rebuilds when a size *ladder* overflows, not on every
  DPI hint change.

Nothing in the default marker set needs GLFW: the tests read the raster
and the packer, which are pure Python + Pillow, and the real GL draw is
tested in the `requires_gl` case that only runs when a person asks for
it.
