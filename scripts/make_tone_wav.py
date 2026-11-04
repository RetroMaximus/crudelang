"""Writes ``examples/sound_recorder/assets/tone.wav`` - and says what it wrote.

The sound recorder needs a file to open. Recording from a microphone is a gesture
a person makes, not one a test can make, and an example that ships no sound has
no ``Open`` menu row that means anything. So this script generates one: two seconds
at 44.1 kHz, mono, sixteen-bit, with three features in it for a marker to be lined
up against - a note that starts hard and settles, a click most of the way through,
and a short fade at either end so the file does not click where it begins.

It is deterministic. Run it twice and the second file has the same samples as the
first, which is what lets ``tests/ui/test_sound_recorder_example.py`` compare the
shipped asset against this script's own output and call the difference a bug.

    python scripts/make_tone_wav.py

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
TARGET = ROOT / "examples" / "sound_recorder" / "assets" / "tone.wav"

RATE = 44100
SECONDS = 2.0
HZ = 220.0

#: how much of the note's own amplitude the second feature keeps: the click is a
#  transient, and a transient that is quieter than the tone under it is not a mark
CLICK_AT = 0.6
CLICK_LEVEL = 0.9
FADE = 0.01

#: the take is scaled to this peak rather than clamped at 1.0: a transient that
#  hit the ceiling would be flat-topped samples, and a file a marker is lined up
#  against is a poor reference if its own edges are distorted
PEAK = 0.9


def envelope(index: int, total: int) -> float:
    """The note's own shape: a hard start that settles, and no click at either end.

    An exponential attack-free decay is what a struck tone looks like, and it gives
    the first marker something to land on at sample zero rather than a ramp to aim
    at. The fades are :data:`FADE` seconds at each end, because a wave that stops
    mid-cycle is a pop that a person hears before they hear the sound.
    """
    seconds = index / float(RATE)
    body = 0.35 + 0.65 * math.exp(-3.0 * seconds)
    edge = min(1.0, seconds / FADE, (total - index) / float(RATE) / FADE)
    return body * max(0.0, edge)


def samples() -> list:
    """The whole take, as ``-1..+1`` floats: the tone, the click, the fades.

    Every sample is built first and the peak is scaled out afterwards, so the
    click stays a click instead of becoming a clamp, and the file the example
    ships has the same shape whatever the tone's own level turned out to be.
    """
    total = int(round(RATE * SECONDS))
    out = []
    for index in range(total):
        seconds = index / float(RATE)
        value = envelope(index, total) * math.sin(2.0 * math.pi * HZ * seconds)
        # the click: one millisecond of a kilohertz, no smoothing. It is not here
        # to be pleasant; it is here because a marker has to have an edge to sit on
        since = seconds - CLICK_AT
        if 0.0 <= since < 0.001:
            value += CLICK_LEVEL * math.sin(2.0 * math.pi * 1000.0 * since)
        out.append(value)
    loudest = max(abs(value) for value in out) or 1.0
    gain = PEAK / loudest
    return [value * gain for value in out]


def main() -> int:
    from crude.audio.wav import write_wav

    held = samples()
    frames = write_wav(str(TARGET), held, RATE, channels=1)
    # read it straight back: the report is what the file is, not what this script
    # meant to write into it, and a codec that had clipped or padded would say so
    check = read_wav_header_shape()
    print(f"{TARGET.relative_to(ROOT)}: {frames} frames, "
          f"{check['channels']} channel, {check['bits']}-bit, {check['rate']} Hz, "
          f"{frames / float(RATE):.2f} s, peak {max(abs(value) for value in held):.3f}")
    return 0


def read_wav_header_shape() -> dict:
    """The shipped file's own header, as the four numbers a person would check."""
    from crude.audio.wav import read_wav_header

    data = read_wav_header(str(TARGET))
    return {"channels": data.channels, "bits": data.bits, "rate": data.rate}


if __name__ == "__main__":
    sys.exit(main())
