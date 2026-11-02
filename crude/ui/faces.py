"""The faces the catalog cannot draw with a plate and a label alone.

:func:`~crude.ui.widget.base_face` answers "a box, a border, a word" for every
kind, and that answer is correct for everything except the synth widgets: a
waveform is not a value box with a caption, and neither is a keyboard. This
module is where those five pictures - and the two graphs beside them - live:
one function per kind, named by kind, in one table.

The split is deliberate. ``widget.py`` owns boxes, states and the skin; a face
owns *only* what happens inside the plate the skin already drew, and takes the
same four arguments the plate does (``widget``, ``renderer``, ``skin``,
``found``) so it reads colours from the same
:class:`~crude.ui.skin.Appearance` as every label does - a scope whose trace
ignores the theme is a bug report from a dark-mode screenshot. A face imports
no widget code and is duck-typed on three things: ``content_box()``, ``get()``
and ``has_value``/``value``, which is what keeps a script painter and a catalog
face answering to exactly the same contract.

Nothing here samples audio or computes an FFT. ``data`` is a prop, and a prop
is what a script wrote into it: the patch does the DSP, ``ON_FRAME`` or a
``PD_EMIT`` handler copies the numbers over, and the face plots what it is
given - empty draws silence, which is the deterministic answer a frame test
can assert against without a running engine.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .geometry import Point, clamp
from .skin import Skin

__all__ = ["FACES", "Face", "face_for"]

#: what a face is called with, and what it returns: the number of ops drawn
Face = Callable[[Any, Any, Skin, Any], int]

#: the semitones inside an octave that have a black key between the whites -
#  the only keyboard fact the drawing layer is allowed to assume
_BLACK = (1, 3, 6, 8, 10)

#: a curve is sampled, not solved: the real face has no bezier op, and 24
#  segments is where the polyline stops being visible as one at any sane size
_CURVE_STEPS = 24


def _levels(values: Sequence[Any], low: float, high: float) -> List[float]:
    """A prop list as plain floats inside ``[low, high]``.

    A face never trusts what a script pushed in, because a ``data`` prop is a
    ``SET`` away from anything: one sample at ``10.0`` would drive the trace off
    the plate and a ``None`` would stop the frame. Coercing here is the only
    clamp between "the table validated a list of numbers" and "the renderer was
    handed numbers", and it costs one pass over a buffer that is drawn anyway.
    """
    out: List[float] = []
    for value in values:
        try:
            held = float(value)
        except (TypeError, ValueError):
            held = 0.0
        out.append(clamp(held, low, high))
    return out


def _plot_waveform(widget: Any, renderer: Any, skin: Skin,
                   found: Any) -> int:
    """The oscilloscope: one trace across the plate, centred on silence.

    Samples are full-scale floats, -1 up to +1 down, spread evenly over the
    content box - the same reading a ``[vd~]`` gives on screen, and the reason
    there is no time axis to place: ``points`` says how many to show, and 0
    says all of them, which is what a rolling buffer with a fixed window wants.
    """
    before = renderer.op_count
    left, top, width, height = widget.content_box()
    if width <= 0.0 or height <= 0.0:
        return 0
    held = _levels(widget.get("data") or (), -1.0, 1.0)
    wanted = int(widget.get("points") or 0)
    if wanted > 0:
        held = held[:wanted]
    middle = top + height * 0.5
    if not held:
        renderer.line((left, middle), (left + width, middle),
                      color=found.ink)
        return renderer.op_count - before
    step = width / max(len(held) - 1, 1)
    points: List[Point] = [
        (left + index * step,
         middle - clamp(value, -1.0, 1.0) * height * 0.5)
        for index, value in enumerate(held)
    ]
    if len(points) == 1:
        renderer.rect(points[0][0] - 0.5, middle - 0.5, 1.0, 1.0, found.ink)
    else:
        renderer.polyline(points, color=found.ink)
    return renderer.op_count - before


def _plot_spectrum(widget: Any, renderer: Any, skin: Skin,
                   found: Any) -> int:
    """The analyser: one bar per bin, grown from the bottom of the plate.

    Bins are magnitudes the script normalised, 0 at the floor and 1 at the
    ceiling of the box; ``bins`` says how many to show, and a count past what
    ``data`` holds draws only what it holds, because a row of empty bars is a
    lie about a spectrum nobody computed.
    """
    before = renderer.op_count
    left, top, width, height = widget.content_box()
    if width <= 0.0 or height <= 0.0:
        return 0
    held = _levels(widget.get("data") or (), 0.0, 1.0)
    wanted = int(widget.get("bins") or 0)
    if wanted > 0:
        held = held[:wanted]
    if not held:
        return renderer.op_count - before
    slot = width / len(held)
    side = slot * 0.7
    for index, value in enumerate(held):
        height_now = clamp(value, 0.0, 1.0) * height
        renderer.rect(left + index * slot + (slot - side) * 0.5,
                      top + height - height_now, side, height_now,
                      found.ink)
    return renderer.op_count - before


def _draw_envelope(widget: Any, renderer: Any, skin: Skin,
                   found: Any) -> int:
    """The ADSR graph: four segments, four props, one lit stage.

    Width is divided in proportion to the four times - attack, decay and
    release as durations, sustain as the hold between decay and release - so
    the picture *is* the numbers, and a script that sets ``attack`` to twice
    what it was sees the first segment twice as wide, which is the only way a
    graph drawn from props stays honest without a second source of truth. The
    value is the stage being edited, and the dot on that segment is what makes
    the vertical drag read before the number does.
    """
    before = renderer.op_count
    left, top, width, height = widget.content_box()
    if width <= 0.0 or height <= 0.0:
        return 0
    times = [max(float(widget.get(name) or 0.0), 0.0)
             for name in ("attack", "decay", "sustain", "release")]
    # all four at zero is a widget that has never been told anything, and a
    # divide by that total is not a graph; one third of the plate is what an
    # even, flat nothing looks like
    total = sum(times) or len(times) / 3.0
    floor = top + height
    peak = top
    sustain = floor - clamp(times[2], 0.0, 1.0) * height
    x0, x1, x2, x3, x4 = left, *[left + width * sum(times[:i + 1]) / total
                                 for i in range(4)]
    renderer.polyline(((x0, floor), (x1, peak), (x2, sustain),
                       (x3, sustain), (x4, floor)), color=found.ink)
    stage = int(clamp(float(widget.value if widget.has_value else 0.0),
                      0.0, 3.0))
    ends = ((x0, floor, x1, peak), (x1, peak, x2, sustain),
            (x2, sustain, x3, sustain), (x3, sustain, x4, floor))
    start_y, end_y = ends[stage][1], ends[stage][3]
    radius = min(4.0, min(width, height) * 0.15)
    renderer.circle(ends[stage][0] + (ends[stage][2] - ends[stage][0]) * 0.5,
                    (start_y + end_y) * 0.5, radius,
                    skin.resolve("selection").fill)
    return renderer.op_count - before


def _draw_curve(widget: Any, renderer: Any, skin: Skin,
                found: Any) -> int:
    """The response curve: one bend, one prop.

    The ends are pinned to the middle of the plate and the control point is
    pushed vertically by ``tension``, so the drawn shape is exactly the number
    the behaviour drags - 0 is the flat line a bypassed curve would draw, and
    the extremes kiss the plate without crossing it (a quadratic bezier sits
    halfway to its control point at its apex).
    """
    before = renderer.op_count
    left, top, width, height = widget.content_box()
    if width <= 0.0 or height <= 0.0:
        return 0
    tension = clamp(float(widget.get("tension") or 0.0), -1.0, 1.0)
    middle = top + height * 0.5
    control_y = middle + tension * height
    points: List[Point] = []
    for index in range(_CURVE_STEPS + 1):
        step = index / _CURVE_STEPS
        rest = 1.0 - step
        points.append((left + width * step,
                       rest * rest * middle
                       + 2.0 * rest * step * control_y
                       + step * step * middle))
    renderer.polyline(points, color=found.ink)
    return renderer.op_count - before


def _draw_pad(widget: Any, renderer: Any, skin: Skin, found: Any) -> int:
    """The XY pad: two guide lines and one dot, at the two props.

    The guides run through the dot rather than across the middle, so the pair
    reads as one coordinate instead of a decoration; ``y`` is stored 0 at the
    bottom because every audio parameter in Pd grows upward, and the flip to
    screen space happens here, at the drawing edge, and nowhere else.
    """
    before = renderer.op_count
    left, top, width, height = widget.content_box()
    if width <= 0.0 or height <= 0.0:
        return 0
    across = left + width * clamp(float(widget.get("x") or 0.0), 0.0, 1.0)
    up = float(widget.get("y") or 0.0)
    down = top + height * (1.0 - clamp(up, 0.0, 1.0))
    guide = skin.resolve("grid", "hover").ink
    renderer.line((across, top), (across, top + height), color=guide)
    renderer.line((left, down), (left + width, down), color=guide)
    radius = min(6.0, min(width, height) * 0.2)
    renderer.circle(across, down, radius, found.ink)
    return renderer.op_count - before


def _draw_keybed(widget: Any, renderer: Any, skin: Skin,
                 found: Any) -> int:
    """The keyboard: naturals across the plate, accidentals on top of them.

    Keys are counted from ``low`` for ``octaves`` octaves and divided as a
    pianist divides them - the white keys take the height, the black keys the
    top 60% of it and the boundary between their neighbours - because a
    keybed that colored stripes instead of keys would be a spectrogram of a
    piano, not one. The held pitch is drawn again in the accent: the press
    must be visible before the note it sent has round-tripped through Pd.

    There is no centered label here, which is the one point where this face
    overrules ``base_face``: words laid over keys make the keys unreadable,
    and a keybed identifies itself by being a keyboard.
    """
    before = renderer.op_count
    left, top, width, height = widget.content_box()
    if width <= 0.0 or height <= 0.0:
        return 0
    low = int(clamp(float(widget.get("low") or 0.0), 0.0, 127.0))
    octaves = max(1, int(round(float(widget.get("octaves") or 1.0))))
    count = min(octaves * 12, max(127 - low + 1, 1))
    naturals = [pitch for pitch in range(low, low + count)
                if (pitch % 12) not in _BLACK]
    if not naturals:
        return renderer.op_count - before
    slot = width / len(naturals)
    white_h = height
    black_h = height * 0.6
    accent = skin.resolve("selection").fill
    held = int(widget.value) if widget.has_value else 0
    edges: Dict[int, float] = {}
    for index, pitch in enumerate(naturals):
        renderer.rect(left + index * slot, top, slot, white_h, found.ink)
        edges[pitch] = left + (index + 1) * slot
        if pitch == held:
            renderer.rect(left + index * slot, top, slot, white_h, accent)
    for pitch in range(low, low + count):
        offset = pitch % 12
        if offset not in _BLACK or (pitch - 1) not in edges:
            continue  # no C in this range to hang the first C# off of
        middle = edges[pitch - 1]
        side = slot * 0.55
        renderer.rect(middle - side * 0.5, top, side, black_h, found.fill)
        if pitch == held:
            renderer.rect(middle - side * 0.5, top, side, black_h, accent)
    return renderer.op_count - before


#: kind name -> the function that draws its inside. A kind that is not here -
#  and every kind with a plate and a word is - is ``base_face``'s business
#  alone, so this table is the only per-kind branch the paint path grows.
FACES: Dict[str, Face] = {
    "waveform": _plot_waveform,
    "spectrum": _plot_spectrum,
    "adsr": _draw_envelope,
    "curve": _draw_curve,
    "xy": _draw_pad,
    "keybed": _draw_keybed,
}


def face_for(kind: Any) -> Optional[Face]:
    """The face this kind is drawn with, or ``None`` for the plate-and-label.

    The lookup walks the chain, not just the name, because a variant inherits
    its parent's picture the same way it inherits its props: a script's
    ``WIDGET_DEFINE("scope", "waveform")`` has no reason to be a bare plate,
    and a synth kind defined over ``adsr`` that wants its own face says so with
    an ``ON_PAINT``, which the tree already puts ahead of everything here.

    ``preset_bar`` is deliberately absent: a row of accent-lit cells *is* a
    radio's plate with the selection role's fill, which the skin already draws
    - the bar changes what a radio looks like, never how it is painted.
    """
    from .props import get_kind
    for name in get_kind(kind).chain():
        found = FACES.get(name)
        if found is not None:
            return found
    return None
