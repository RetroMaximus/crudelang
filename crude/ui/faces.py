"""The faces the catalog cannot draw with a plate and a label alone.

:func:`~crude.ui.widget.base_face` answers "a box, a border, a word" for every
kind, and that answer is correct for everything except the widgets that are a
picture: a waveform is not a value box with a caption, neither is a keyboard,
and neither is a slider whose thumb is the value. This module is where those
pictures live - the synth widgets, the ranges with a thumb, the meter, the
radio's cells and the dial - one function per kind, named by kind, in one
table.

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

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .clip import envelope, marker_pixels, pixel_of, window_of
from .geometry import TAU, Point, TEXT_HEIGHT, clamp, rect_corners
from .group import (MARK_FOLDED, MARK_OPEN, TITLE_PAD, chevron_box,
                    foldable_of, folded_of, header_box, title_of)
from .grid import cells as grid_cells
from .scroll import thumb_rect
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


#: the edges a name or a readout can be pushed outside the box on. ``inside``
#  and ``none`` are handled by the caller; a side is drawn by :func:`placed_text`.
SIDES = ("top", "bottom", "left", "right")


def placement(widget: Any, which: str) -> Tuple[str, float, float]:
    """Where a word goes and how far to nudge it: ``(mode, dx, dy)``.

    ``which`` is ``"label"`` or ``"value"``; the two read the matching pair of
    rows the table gives every kind, at the inside default when a widget is too
    small to have been told otherwise. Reading through the props rather than the
    ``_state`` map means a script kind that restated a row still answers.
    """
    props = widget.kind.props
    if f"{which}_place" in props:
        place = widget.get(f"{which}_place") or "inside"
    else:
        place = "inside"
    dx = float(widget.get(f"{which}_dx")) if f"{which}_dx" in props else 0.0
    dy = float(widget.get(f"{which}_dy")) if f"{which}_dy" in props else 0.0
    return place, dx, dy


def placed_text(widget: Any, renderer: Any, found: Any, text: Any,
                place: str, dx: float = 0.0, dy: float = 0.0,
                *, size: float = None) -> int:
    """Draw one word against the side of the widget's box - outside it.

    Anchoring is on ``screen_box`` (absolute), so "outside the knob" means
    outside the knob's own frame, in the panel margin around it. The clip the
    widget was resolved with is grown on that side (see
    :meth:`~crude.ui.widget.Widget._resolve`) so the word is not cut. ``inside``
    and ``none`` are the caller's business and answer zero here.
    """
    if not text or place not in SIDES:
        return 0
    size = found.font if size is None else size
    line = size * TEXT_HEIGHT
    left, top, width, height = widget.screen_box
    gap = max(found.width, 1.0)
    mid_y = top + (height - line) * 0.5
    if place == "top":
        renderer.text(left + width * 0.5 + dx, top - line - gap + dy,
                      str(text), size, found.ink, align="center")
    elif place == "bottom":
        renderer.text(left + width * 0.5 + dx, top + height + gap + dy,
                      str(text), size, found.ink, align="center")
    elif place == "left":
        renderer.text(left - gap + dx, mid_y + dy, str(text), size,
                      found.ink, align="right")
    else:  # right
        renderer.text(left + width + gap + dx, mid_y + dy, str(text), size,
                      found.ink, align="left")
    return 1


def draw_word(widget: Any, renderer: Any, found: Any, text: Any, which: str,
              size: float, inside: Callable[[], int]) -> int:
    """Draw one word at wherever its ``which`` placement says.

    ``inside`` is a closure holding the face's own centred placement, called
    when the word stays in the box; a side hands it to :func:`placed_text`, and
    ``none`` draws nothing. This is the one branch every face shares, so a knob
    and a slider agree about what ``label_place='left'`` means.
    """
    if not text:
        return 0
    place, dx, dy = placement(widget, which)
    if place == "none":
        return 0
    if place in SIDES:
        return placed_text(widget, renderer, found, text, place, dx, dy,
                           size=size)
    return inside()


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


def _draw_plot(widget: Any, renderer: Any, skin: Skin, found: Any) -> int:
    """The line graph: one polyline over the value list, autoscaled to what it holds.

    A waveform plots samples against a fixed -1..+1 full scale; a plot has no such
    scale, so it reads its own span off the data and stretches the tallest peak to
    the top of the plate and the lowest trough to the bottom - which is what lets
    one kind graph a modindex array (0..12) and a filter sweep (20..20000) with the
    same code, because the array's own min and max become the axis. A flat series
    (every value equal) would divide by zero, so it is given a one-unit span centred
    on the value and draws as the straight line it is. The baseline is drawn where the
    value 0 falls inside the span, which is the one grid line that means something on
    a graph of signed numbers.
    """
    before = renderer.op_count
    left, top, width, height = widget.content_box()
    if width <= 0.0 or height <= 0.0:
        return 0
    held: List[float] = []
    for value in (widget.get("data") or ()):
        try:
            held.append(float(value))
        except (TypeError, ValueError):
            held.append(0.0)
    wanted = int(widget.get("points") or 0)
    if wanted > 0:
        held = held[:wanted]
    if held:
        low, high = min(held), max(held)
    else:
        low, high = 0.0, 1.0
    if high <= low:
        low, high = low - 0.5, high + 0.5
    span = high - low
    guide = skin.resolve("grid", "hover").ink
    renderer.line((left, top + height), (left + width, top + height), color=guide)
    if low <= 0.0 <= high:
        zero = top + height * (high - 0.0) / span
        renderer.line((left, zero), (left + width, zero), color=guide)
    if not held:
        return renderer.op_count - before
    step = width / max(len(held) - 1, 1)
    points: List[Point] = [
        (left + index * step, top + height * (high - value) / span)
        for index, value in enumerate(held)
    ]
    if len(points) == 1:
        renderer.rect(points[0][0] - 0.5, points[0][1] - 0.5, 1.0, 1.0, found.ink)
    else:
        renderer.polyline(points, color=found.ink)
    return renderer.op_count - before


def _draw_logview(widget: Any, renderer: Any, skin: Skin, found: Any) -> int:
    """The scrollable text: the newest lines that fit, the wheel moving up for older.

    ``lines`` is a list a script appends to; the plate is a window onto its tail,
    oldest at the top and the newest at the bottom when ``scroll`` is 0, exactly as a
    terminal reads. Scrolling counts whole lines up from the newest, so the number is
    a position in the log rather than a pixel offset, and a window that grew taller
    between frames still shows the same lines it was asked for. Words past the box are
    left off by the widget's own clip - a log view truncates, it does not wrap.
    """
    before = renderer.op_count
    left, top, width, height = widget.content_box()
    if width <= 0.0 or height <= 0.0:
        return 0
    rows = [str(line) for line in (widget.get("lines") or ())]
    if not rows:
        return renderer.op_count - before
    line_h = found.font * TEXT_HEIGHT
    fit = max(1, int(height // line_h))
    scroll = int(round(float(widget.get("scroll") or 0.0)))
    # the wheel may travel only as far as the oldest *page*, not the oldest single
    # line: clamping to len(rows) - 1 would scroll the newest lines off and leave a
    # near-empty plate showing one row, so the top of the box is the real limit
    scroll = max(0, min(scroll, max(0, len(rows) - fit)))
    bottom = len(rows) - 1 - scroll
    start = bottom - (fit - 1)
    for slot in range(fit):
        index = start + slot
        if index < 0:
            continue
        if index >= len(rows):
            break
        renderer.text(left, top + slot * line_h, rows[index], found.font,
                      found.ink, align="left")
    return renderer.op_count - before


def _draw_clip(widget: Any, renderer: Any, skin: Skin, found: Any) -> int:
    """The sample window: an envelope, the marks in it, and the cursor on it.

    Three things are drawn and all three are read out of :mod:`crude.ui.clip`, the
    same module the gesture asks its questions of, so the line a hand can grab is
    the line the eye was shown - the rule a scroll thumb, a grid cell and a group
    chevron each keep, kept a fourth time here.

    The sound is a min/max bar per column rather than a polyline, because a clip is
    a *file*: 200 000 samples arrive as 200 000 vertices for a plate two hundred
    pixels wide, and what a person reads off a recorded take is where the peaks are.
    Only the columns the window is folded into draw, so a take longer than the box
    is still one bar per pixel and a window slid past its end draws the empty plate
    it is, rather than a line arguing about samples that are not there.

    The numbers above the marks are the whole reason four markers can be told
    apart: a clip's marks are one list in the order the script wrote it, and a
    panel whose first entry is the rough start says so on the plate rather than
    only in its source. They are held back on a box too short for a line of text,
    which is the same courtesy a knob's label is not asked to pay when there is no
    room for it.
    """
    before = renderer.op_count
    left, top, width, height = widget.content_box()
    if width <= 0.0 or height <= 0.0:
        return 0
    guide = skin.resolve("grid", "hover").ink
    middle = top + height * 0.5
    renderer.line((left, top + height), (left + width, top + height),
                  color=guide)
    renderer.line((left, middle), (left + width, middle), color=guide)
    held = envelope(widget)
    if not held:
        # silence, or a window onto nothing: the same flat trace a scope with an
        # empty buffer draws, so a clip built before its take arrived is not blank
        renderer.line((left, middle), (left + width, middle), color=found.ink)
    else:
        half = height * 0.5
        slot = width / len(held)
        side = max(1.0, slot * 0.7)
        for across, low, high in held:
            floor = middle - clamp(low, -1.0, 1.0) * half
            ceiling = middle - clamp(high, -1.0, 1.0) * half
            renderer.rect(across - side * 0.5, ceiling, side,
                          max(1.0, floor - ceiling), found.ink)
    mark = skin.resolve("accent")
    line_h = found.font * TEXT_HEIGHT
    for index, across in marker_pixels(widget):
        renderer.line((across, top), (across, top + height), color=mark.fill)
        if height >= line_h:
            # numbered by the marker's own place in the list the script wrote, not
            # by its rank among the marks this window happens to show: scroll away
            # from the rough start and the two you can still see are 3 and 4, which
            # is what a knob wired to ``markers[2]`` says they are
            renderer.text(across, top, str(int(index) + 1), found.font, mark.fill,
                          align="center")
    cursor = float(widget.get("playhead") or 0.0)
    first, span = window_of(widget)
    if cursor >= 0.0 and span > 0.0 and first <= cursor <= first + span:
        across = pixel_of(widget, cursor)
        renderer.line((across, top), (across, top + height),
                      color=skin.resolve("thumb").fill)
    return renderer.op_count - before


def _draw_scroll(widget: Any, renderer: Any, skin: Skin, found: Any) -> int:
    """A viewport's thumb: one bar in the track the content slides down.

    Nothing else is drawn, which is the whole point of the face: the rows are the
    widget's children and paint themselves through the tree, the plate is what
    ``base_face`` already gave the panel, and a scroll area that also drew a track
    would be a frame inside a frame. The thumb is missing when the content fits - a
    bar that cannot move is not information - and is missing when ``bar`` is off,
    which leaves the wheel and the keys working on a viewport that simply has no
    decoration on it.

    The rect comes from :func:`~crude.ui.scroll.thumb_rect`, the same function the
    gesture tests a press against, so the mark a hand can grab is exactly the mark
    the eye was shown and the two cannot drift apart between builds.
    """
    before = renderer.op_count
    if not widget.get("bar"):
        return 0
    held = thumb_rect(widget)
    if held is None:
        return 0
    renderer.rect(held[0], held[1], held[2], held[3],
                  skin.resolve("thumb").fill)
    return renderer.op_count - before


def _draw_grid(widget: Any, renderer: Any, skin: Skin, found: Any) -> int:
    """A panel's face: its own words, and its cells while a drop is possible.

    The label is drawn by :meth:`~crude.ui.widget.Widget.paint_label` because
    registering a face *replaces* that call in ``base_face``, and a panel that lost
    its caption when it gained the ability to be a dock would be a regression
    dressed up as a feature. Everything else is decoration, and the decoration is
    kept back until it is wanted: an ordinary grid draws nothing extra, and one
    marked ``dockable`` shows its cells only while a drag is in progress - the one
    moment the outlines are an answer to a question the person is asking rather
    than a frame painted inside a frame.

    The rectangles come from :func:`crude.ui.grid.cells`, which reads the rows the
    layout pass wrote when it arranged them, so the outline a person is aiming at
    is the box the child was actually given and not a second arithmetic that
    happens to agree with it today.
    """
    before = renderer.op_count
    widget.paint_label(renderer, found)
    if not widget.get("dockable") or not widget.get("docking"):
        return renderer.op_count - before
    edge = max(1.0, float(found.width or 0.0))
    for box in grid_cells(widget):
        if box[2] <= 0.0 or box[3] <= 0.0:
            continue
        renderer.polyline(rect_corners(*box), thickness=edge,
                          color=found.border, closed=True)
    return renderer.op_count - before


def _draw_group(widget: Any, renderer: Any, skin: Skin, found: Any) -> int:
    """A group's header: the word it is named by, the mark that folds it, a rule.

    No plate is drawn here. ``base_face`` has already given the group the panel's
    own frame, and a second frame painted inside it is the thing this module has
    always refused about a viewport; what a header needs that a panel does not have
    is three marks - the title, the chevron, and the line under them, which is what
    keeps a folded group visibly the same box it was before it folded.

    Both rectangles come out of :mod:`crude.ui.group`: the band from
    :func:`~crude.ui.group.header_box` and the mark from
    :func:`~crude.ui.group.chevron_box`, which are the same two functions the
    gesture tests a press against. A mark a hand can hit being exactly the mark the
    eye was shown is how a scroll thumb and a grid cell are drawn too, and a group
    is the third place that promise is kept.

    The words are drawn here rather than left to :meth:`paint_label`, which
    registering a face replaces, because a group's caption belongs at the top of
    its box: centred in the whole thing, it would move when the body folded and
    would sit on top of the knobs the group was built to hold.
    """
    before = renderer.op_count
    left, top, wide, high = header_box(widget)
    if wide <= 0.0 or high <= 0.0:
        return 0
    mark = skin.resolve("caption")
    said = title_of(widget)
    if said:
        middle = top + (high - mark.font * TEXT_HEIGHT) * 0.5
        renderer.text(left + TITLE_PAD, middle, said, mark.font, mark.ink,
                      align="left")
    if foldable_of(widget):
        box = chevron_box(widget)
        if box[2] > 0.0:
            renderer.glyph(box[0], box[1], box[2], box[3],
                           MARK_FOLDED if folded_of(widget) else MARK_OPEN,
                           mark.ink)
    if not folded_of(widget):
        renderer.line((left, top + high), (left + wide, top + high),
                      color=found.border)
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


#: the dial's sweep and where it starts. Angles are the renderer's own - 0 to
#  the right, y down, so a positive sine is *down* - which puts the lower left
#  at 3π/4 (cos<0, sin>0) and the lower right at π/4. Starting there and sweeping
#  three quarters of a turn runs 3π/4 → π (left) → π/2 up through the top → 0
#  (right) → π/4: minimum at the left hand, maximum at the right, and the one
#  quarter turn of gap under the dial where nothing ever points. The sweep is
#  kept under a full turn on purpose - the renderer closes any arc whose sweep
#  reaches TAU into a ring, and a ring has no gap to read the value off.
_KNOB_START = 0.375 * TAU
_KNOB_SWEEP = 0.75 * TAU


def _is_vertical(widget: Any) -> bool:
    """Whether this range is drawn top-down, which its kind name answers.

    ``vslider`` is the only catalog kind that is, and the face lookup walks the
    chain into ``slider`` for anything defined over it - so the name, not a
    second prop, stays the one fact about orientation.
    """
    return "vslider" in widget.kind.chain()


def _draw_slider(widget: Any, renderer: Any, skin: Skin, found: Any) -> int:
    """The range with a thumb: track, fill up to the value, thumb on the value.

    One painter serves the horizontal and the vertical box, because the
    behaviour module already established that a ``vslider`` is ``slider`` read
    one way round - here that reading is the longer edge taken as the ruler.
    The thumb is a slab across the track (Pd puts its bar *at* the value, not
    around it), and the accent runs from the zero end of the track to the
    thumb, so the picture is the number: 0 shows no accent, the maximum shows
    none of the trough.

    The label is Pd's own way round - written across the trough itself, so a
    24-pixel slider still says its name - and a vertical box, where the word
    has a free end to sit on, keeps it above the track instead.
    """
    before = renderer.op_count
    left, top, width, height = widget.content_box()
    if width <= 0.0 or height <= 0.0:
        return 0
    from .behaviour import fraction_of
    vertical = _is_vertical(widget)
    text = widget.label or found.glyph
    label_place = placement(widget, "label")[0]
    band = (found.font * TEXT_HEIGHT
            if (text and vertical and label_place == "inside") else 0.0)
    thick = min(width if vertical else height, 24.0)
    ruler = max(0.0, (height if vertical else width) - band)
    side = max(3.0, min(thick * 0.6, ruler * 0.16))
    travel = max(0.0, ruler - side)
    point = clamp(fraction_of(widget, widget.value), 0.0, 1.0)
    along = travel * point
    fill = skin.resolve("fill").fill
    thumb = skin.resolve("thumb").fill
    if vertical:
        x, y = left + (width - thick) * 0.5, top + band
        renderer.rect(x, y, thick, ruler, found.border, radius=thick * 0.5)
        # the zero end is the floor: the accent grows upward from it and the
        # thumb's near edge rides the same distance up from the bottom
        renderer.rect(x, y + ruler - along, thick, along, fill)
        renderer.rect(x, y + ruler - along - side, thick, side, thumb)
    else:
        x, y = left, top + (height - thick) * 0.5
        renderer.rect(x, y, ruler, thick, found.border, radius=thick * 0.5)
        # the zero end is the left: the accent runs from it to the thumb and
        # the thumb's near edge is the value, so low reads left and high right
        renderer.rect(x, y, along, thick, fill)
        renderer.rect(x + along, y, side, thick, thumb)
    if text:
        def inside():
            if vertical:
                renderer.text(left + width * 0.5, top, text, found.font,
                              found.ink, align="center")
            else:
                renderer.text(left + ruler * 0.5, y + (thick - found.font) * 0.5,
                              text, found.font, found.ink, align="center")
            return 1
        draw_word(widget, renderer, found, text, "label", found.font, inside)
    return renderer.op_count - before


def _draw_meter(widget: Any, renderer: Any, skin: Skin, found: Any) -> int:
    """The level read out: the trough lit from its zero end to the value.

    Orientation comes from the box rather than the kind, because a ``[vu]`` is
    drawn as often on a mixer's channel strip as across one, and the long edge
    is where a level has room to be seen. There is no thumb: a meter is read,
    not grabbed, and the behaviour table already refuses it the drag. The word
    goes on over the level through the tree's own ``paint_label``, measured and
    centred, because a 24-pixel ``[vu]`` whose name runs past its box is drawn
    with the same care a button's caption is.
    """
    before = renderer.op_count
    left, top, width, height = widget.content_box()
    if width <= 0.0 or height <= 0.0:
        return 0
    from .behaviour import fraction_of
    point = clamp(fraction_of(widget, widget.value), 0.0, 1.0)
    level = skin.resolve("fill").fill
    if width >= height:
        renderer.rect(left, top, width * point, height, level)
    else:
        renderer.rect(left, top + height * (1.0 - point), width,
                      height * point, level)
    if widget.label:
        widget.paint_label(renderer, found)
    return renderer.op_count - before


def _draw_radio(widget: Any, renderer: Any, skin: Skin, found: Any) -> int:
    """The choices laid out at once: one cell per label, the held one lit.

    The grid is :func:`~crude.ui.behaviour._cell_at`'s arithmetic - ``columns``
    across, as many rows as the labels need, the last row's spare cells simply
    absent - so the cell a person presses is provably the cell that was drawn.

    The face owns its whole interior, including the plate: a radio holding a
    non-first index resolves to the ``on`` state, and the checkbox (and the
    preset bar's selection) role paints that state in the accent - which would
    swallow the unlit cells the moment anything was chosen. So a neutral plate
    is laid down first, in colours read from stable roles rather than from
    ``found``, and only then the cells: the held index in the accent, the rest
    raised. A cell with a word gets the word. ``preset_bar`` inherits all of
    this unchanged, because it is a radio's cells wearing a different role.
    """
    before = renderer.op_count
    sl, st, sw, sh = widget.screen_box
    left, top, width, height = widget.content_box()
    words = list(widget.get("labels") or ())
    if sw <= 0.0 or sh <= 0.0 or width <= 0.0 or height <= 0.0 or not words:
        return 0
    # colours the widget's own on-state cannot move: read from stable roles
    frame = skin.resolve("border").fill
    plate = skin.resolve("track").fill
    cell_off = skin.resolve("button").fill
    accent = skin.resolve("fill").fill
    lit_ink = skin.resolve("fill").ink
    edge = min(found.width, sw * 0.5, sh * 0.5)
    if edge > 0:
        renderer.rect(sl, st, sw, sh, frame, radius=found.radius)
        renderer.rect(sl + edge, st + edge, max(0.0, sw - 2 * edge),
                      max(0.0, sh - 2 * edge), plate,
                      radius=found.radius - edge if found.radius else 0.0)
    else:
        renderer.rect(sl, st, sw, sh, plate, radius=found.radius)
    from .behaviour import _columns
    across = _columns(widget)
    if not widget.get("columns"):
        # columns 0 is Pd's "one row per entry", which the hit test already
        # reads as one column; the face says the same thing as many columns
        # as there are words, because a bar of cells is what was promised
        across = len(words)
    rows = max(1, -(-len(words) // across))
    held = int(round(float(widget.value))) if widget.has_value else -1
    cw, ch = width / across, height / rows
    gap = min(cw, ch) * 0.1
    for index, word in enumerate(words):
        row, col = divmod(index, across)
        x, y = left + col * cw + gap, top + row * ch + gap
        w, h = max(0.0, cw - 2 * gap), max(0.0, ch - 2 * gap)
        lit = index == held
        renderer.rect(x, y, w, h, accent if lit else cell_off,
                      radius=found.radius)
        said = str(word) if word else str(index)
        renderer.text(x + w * 0.5, y + max(0.0, (h - found.font) * 0.5),
                      said, found.font, lit_ink if lit else found.ink,
                      align="center")
    return renderer.op_count - before


def _draw_knob(widget: Any, renderer: Any, skin: Skin, found: Any) -> int:
    """The dial: a 270 degree ring of ticks, the turned part lit, a pointer.

    The face is the answer vanilla Pd never had to give - a dial is not a box
    in 0.54 - so the geometry comes from the widget's own numbers: the value's
    fraction of the range sets the pointer inside the sweep above, ticks are
    drawn at the ``ticks`` prop or ten when it says 0, and the value arc runs
    counter-clockwise from the lower left exactly as far as the value is.
    Dragging stays vertical like a ``vslider``, because a dial's pointer has
    no position for the mouse to jump to.
    """
    before = renderer.op_count
    left, top, width, height = widget.content_box()
    if width <= 0.0 or height <= 0.0:
        return 0
    from .behaviour import fraction_of
    cx = left + width * 0.5
    label_size = found.font * 0.75
    value_size = found.font * 0.75
    label_place = placement(widget, "label")[0]
    value_place = placement(widget, "value")[0]
    # a name or a number pushed to a side leaves the dial the whole box to turn
    # in; only words staying "inside" cost it a strip of its height, which is
    # why a 25x25 knob with label_place='top' still gets a full-size dial
    label_h = (label_size * TEXT_HEIGHT
               if widget.label and label_place == "inside" else 0.0)
    value_h = (value_size * TEXT_HEIGHT
               if widget.has_value and value_place == "inside" else 0.0)
    pad = max(found.width, 1.0)
    dial_top = top + label_h
    dial_height = max(0.0, height - label_h - value_h)
    radius = min(width, dial_height) * 0.5 - pad
    if radius <= 0.0:
        return renderer.op_count - before
    cy = dial_top + dial_height * 0.5
    point = clamp(fraction_of(widget, widget.value), 0.0, 1.0)
    count = int(widget.get("ticks") or 0) or 10
    angle_at = lambda at: _KNOB_START + _KNOB_SWEEP * clamp(at, 0.0, 1.0)
    for step in range(count + 1):
        at = step / count
        outer = radius + pad
        cosine, sine = math.cos(angle_at(at)), math.sin(angle_at(at))
        renderer.line((cx + (outer - pad * 2) * cosine,
                       cy + (outer - pad * 2) * sine),
                      (cx + outer * cosine, cy + outer * sine),
                      thickness=pad, color=found.border)
    ring = radius - min(radius * 0.18, 5.0)
    renderer.arc(cx, cy, ring, thickness=pad * 1.5,
                 start=_KNOB_START, end=_KNOB_START + _KNOB_SWEEP,
                 color=found.border)
    if point > 0.0:
        renderer.arc(cx, cy, ring, thickness=pad * 1.5, start=_KNOB_START,
                     end=angle_at(point), color=skin.resolve("fill").fill)
    face = radius - pad * 4
    renderer.circle(cx, cy, face, found.fill)
    inner = face - max(face * 0.55, 4.0)
    pointer = angle_at(point)
    renderer.line((cx + inner * math.cos(pointer),
                   cy + inner * math.sin(pointer)),
                  (cx + face * math.cos(pointer), cy + face * math.sin(pointer)),
                  thickness=pad * 2, color=found.ink)
    middle = left + width * 0.5

    def label_inside():
        renderer.text(middle, top, widget.label, label_size, found.ink,
                      align="center")
        return 1

    def value_inside():
        renderer.text(middle, top + height - value_size, widget.readout(),
                      value_size, found.ink, align="center")
        return 1

    if widget.label:
        draw_word(widget, renderer, found, widget.label, "label", found.font,
                  label_inside)
    if widget.has_value:
        draw_word(widget, renderer, found, widget.readout(), "value",
                  found.font, value_inside)
    return renderer.op_count - before


#: kind name -> the function that draws its inside. A kind that is not here -
#  and every kind with a plate and a word is - is ``base_face``'s business
#  alone, so this table is the only per-kind branch the paint path grows.
#  ``vslider`` and ``preset_bar`` are deliberately not keys: the first is
#  slider read one way round and the second is a radio wearing the selection
#  role, and the chain lookup below is what says so.
FACES: Dict[str, Face] = {
    "waveform": _plot_waveform,
    "spectrum": _plot_spectrum,
    "plot": _draw_plot,
    "logview": _draw_logview,
    # the canvas with a position: drawn entirely out of the arithmetic the
    # gesture and the follow pass use, so no number on the plate is a second
    # opinion about where a sample is
    "clip": _draw_clip,
    "adsr": _draw_envelope,
    "curve": _draw_curve,
    "xy": _draw_pad,
    "keybed": _draw_keybed,
    "slider": _draw_slider,
    "meter": _draw_meter,
    "radio": _draw_radio,
    "knob": _draw_knob,
    # a viewport is a panel with a mark on it: the plate and the clip come from the
    # base face and this only answers the thumb
    "scroll": _draw_scroll,
    # a group is a panel with a header on it: its plate is the base face's, its
    # body is a panel that paints itself, and this answers the band between them
    "group": _draw_group,
    # a panel is the plate and the word and nothing else, until it is marked
    # dockable and something is being dragged - which is the only decoration the
    # face below ever adds, so an ordinary panel is drawn exactly as it was before
    "panel": _draw_grid,
}


def face_for(kind: Any) -> Optional[Face]:
    """The face this kind is drawn with, or ``None`` for the plate-and-label.

    The lookup walks the chain, not just the name, because a variant inherits
    its parent's picture the same way it inherits its props: a script's
    ``WIDGET_DEFINE("scope", "waveform")`` has no reason to be a bare plate,
    and a synth kind defined over ``adsr`` that wants its own face says so with
    an ``ON_PAINT``, which the tree already puts ahead of everything here. A
    ``vslider`` reaches ``slider`` and is drawn top-down by the face's own
    reading of the kind, and a ``preset_bar`` reaches ``radio`` - the bar *is*
    a radio's cells, and its selection role is what makes them accent-lit.
    """
    from .props import get_kind
    for name in get_kind(kind).chain():
        found = FACES.get(name)
        if found is not None:
            return found
    return None
