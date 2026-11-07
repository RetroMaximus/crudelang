"""Writes ``examples/all_gui/assets/wave.txt`` - and says what it wrote.

The GUI showcase has no patch behind it, so the two graph widgets on its canvases
page cannot be fed from ``PD_ARRAY_READ`` the way ``fm_synth`` feeds its wavetable.
They need a series of numbers that exists *outside* the script: there are no math
verbs in the language (``MIN``/``MAX``/``CLAMP``/``FLOOR``/``ROUND`` are all
:mod:`crude.bindings.core` has), so a wave computed in a script would be a script
doing arithmetic it has no business doing.

So this file generates one: 1024 values of a fundamental with three partials over
it, scaled to a peak of 0.95. The partials are the point. A pure sine is a shape
every scope shows the same way; a wave with an odd harmonic and an inharmonic one
in it has a visible envelope, which is what makes the showcase's second plot - the
same series through ``CLAMP``, flat-topped at +/- 0.5 - a different picture rather
than the same one with a different title.

It is deterministic. Run it twice and the second file has the same digits as the
first, which is what lets ``tests/ui/test_all_gui_example.py`` compare the shipped
asset against this script's own output and call the difference a bug.

    python scripts/make_wave_txt.py

The file is written beside the example it belongs to, wherever the repository
lives, so the command works from any working directory.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

#: the repository root, whatever directory this script was run from
ROOT = Path(__file__).resolve().parents[1]

#: a script in a folder is run with *that folder* on ``sys.path``, so the package
#  it belongs to has to be pointed at rather than hoped for
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: where the asset goes, and what the example's manifest declares it as
TARGET = ROOT / "examples" / "all_gui" / "assets" / "wave.txt"

#: how many values the table holds, and how many are written per line - eight,
#  because that is the column width ``fm_synth``'s tables use and a reader who has
#  seen one of these files should not have to re-learn the shape of another
COUNT = 1024
PER_LINE = 8

#: the partials, as (cycles per table, amplitude): four cycles of the fundamental
#  and three over it - a twelfth, a thirtieth, and 2.5, which is deliberately not a
#  whole number, so the cycle does not line up with itself and the table has a seam
#  a zoomed clip can show
PARTIALS = ((4.0, 1.0), (12.0, 0.45), (30.0, 0.22), (2.5, 0.30))

#: the peak the series is scaled to. Not 1.0, because a table whose loudest value
#  is exactly full scale reads as a file that was clamped on the way in, and the
#: showcase's whole purpose is to be a picture a person can check by eye
PEAK = 0.95


def values() -> list:
    """The series as ``-1..+1`` floats, scaled so its own peak is :data:`PEAK`."""
    raw = []
    for index in range(COUNT):
        phase = index / float(COUNT)
        total = 0.0
        for cycles, level in PARTIALS:
            total += level * math.sin(2.0 * math.pi * cycles * phase)
        raw.append(total)
    loudest = max(abs(value) for value in raw) or 1.0
    gain = PEAK / loudest
    return [value * gain for value in raw]


def text(series: list) -> str:
    """The file's whole content: the header comment and the column of numbers."""
    lines = [
        "# wave.txt: the series the GUI showcase graphs, in the shape",
        "# crude.project.resources parses: '#' starts a comment and any mix of",
        "# whitespace or commas separates the numbers.",
        "#",
        f"# {COUNT} values, written by scripts/make_wave_txt.py: four cycles of a",
        "# fundamental with a twelfth, a thirtieth and - the one that gives the",
        "# wave its envelope - a two-and-a-halfth over it, scaled so its own peak",
        "# is 0.95 rather than a value that was clamped on the way in.",
        "#",
        "# The showcase graphs this twice: once as it is, and once through CLAMP",
        "# at +/- 0.5. The second picture is the flat-topped one, which is what",
        "# makes the pair a demonstration of a PLOT's autoscale rather than two",
        "# copies of the same line.",
        "",
    ]
    for start in range(0, len(series), PER_LINE):
        chunk = series[start:start + PER_LINE]
        lines.append(", ".join(f"{value:.5f}" for value in chunk))
    return "\n".join(lines) + "\n"


def main() -> int:
    series = values()
    where = TARGET
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(text(series), encoding="utf-8")

    # read it back through the parser the runtime uses, so the report is about the
    # file that is on disk rather than about the list this script held in memory
    from crude.project.resources import parse_array_text

    back = parse_array_text(where.read_text(encoding="utf-8"), str(where))
    peak = max(abs(value) for value in back)
    over = len([value for value in back if abs(value) > 0.5])
    print(f"{where.relative_to(ROOT)}: {len(back)} value(s), peak {peak:.5f}, "
          f"{over} beyond +/-0.5 (the CLAMP that the showcase's second plot shows)")
    if len(back) != COUNT or abs(peak - PEAK) > 1e-4:
        print("the file that came back is not the series that went in",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
