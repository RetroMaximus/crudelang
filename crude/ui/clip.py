"""The arithmetic of a clip: samples, pixels, a window and marks inside it.

A :class:`~crude.ui.widget.Widget` of kind ``clip`` is the first canvas in this
toolkit that has a *position*. A ``waveform`` is a rolling scope - it shows the
buffer it was handed and knows nothing about where that buffer came from - and a
``plot`` stretches its own span over the plate. A recorded take is different: it
has a length, the person needs to be somewhere in it, and the place where the
sound starts has to be hit precisely enough to cut a sample. That is four numbers
(``start``, ``count``, ``markers``, ``playhead``) and one rule about them, and the
rule is this module's whole content: **a sample and a pixel are the same place
seen from two sides**, so every conversion goes through here and nowhere else.

Four rules carry it, in the same shape :mod:`crude.ui.scroll` is written in:

* **The props are the fact.** ``start`` and ``count`` *are* the view; there is no
  camera behind them and no transform a face keeps to itself. A script that
  writes ``SET(c, "count", 1000)`` and a hand that pushes the wheel have changed
  one thing, and ``GET`` answers the number the screen was drawn with - the same
  promise a viewport's ``offset`` makes.
* **Nothing is resolved until it is asked for.** ``count`` 0 means "the whole
  take" and ``window_of`` answers what that turned out to be *this frame*, so a
  recording that is still growing is still fully shown, and a clip with no data
  has a window of nothing rather than a division by zero. The clamp is here, once,
  and the face, the gesture and the follow pass all read the same answer.
* **A painter never writes a prop.** :func:`envelope` and :func:`marker_pixels`
  are pure, and the one rule that cannot be honoured from inside a drawing - keep
  the playhead in view - is a pass the layout runs (:func:`follow_all`), because
  ``follow`` moves ``start``, and a draw pass that moved a prop would be a frame
  that repaints itself forever.
* **The pixels the face uses and the pixels a press is measured against are the
  same pixels.** :func:`nearest_marker` answers from the same mapping
  :func:`marker_pixels` draws with, which is how a mark a hand can grab is
  exactly the mark the eye was shown - the scroll thumb, the grid cell, the group
  chevron and the clip marker are one rule stated four times.

``crude.ui.widget`` is not imported here, and must not be: this module's owner
imports it. Everything is duck-typed on ``content_box()``, ``get()`` and ``set()``
instead, exactly as :mod:`crude.ui.scroll` and every face in
:mod:`crude.ui.faces` already are.

"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

__all__ = ["CLIP_KIND", "GRAB", "ZOOM_STEP", "MIN_WINDOW", "clip_of",
           "frames_of", "samples_of", "window_of", "plot_box", "sample_at",
           "pixel_of", "samples_per_pixel", "columns_of", "envelope",
           "marker_pixels", "nearest_marker", "set_marker", "clamp_window",
           "set_window", "zoom_at", "pan_from", "apply_follow", "follow_all"]

#: the kind whose window this module resolves. Named here rather than imported
#  from :mod:`crude.ui.widget`, which imports this module.
CLIP_KIND = "clip"

#: how near a marker's line a press has to land to take it, in logical pixels.
#  A marker is one pixel wide and a pointer is not, so the grab is deliberately
#  wider than the mark: six pixels each way is the same tolerance a scroll bar's
#  column is given, and a mark that could only be hit by an exact pixel would be
#  decoration a hand cannot use.
GRAB = 6.0

#: how much one wheel notch changes the window: one step halves it, one step back
#  doubles it. Two is the factor because a zoom that is a *ratio* feels the same at
#  every scale - 400 000 samples down to 200 000 is as big a step as 200 down to
#  100, and a fixed subtraction would either never reach a sample or never leave it.
ZOOM_STEP = 2.0

#: the narrowest window there is. One sample: a clip zoomed past the end of a
#  waveform has nothing left to draw, and a window of zero would divide by it.
MIN_WINDOW = 1.0


def _whole(value: Any) -> float:
    """A sample count as a sample: the number a ``GET`` should answer with.

    Every mapping here multiplies a pixel count by a sample count, and an answer
    of ``199999.9999997`` is not information about a take that holds 200 000
    samples - it is the binary float the arithmetic left behind. A window and a
    marker are both counts of *whole* samples, so the rounding happens at the one
    door each is written through rather than in every caller that has to remember
    to ask for it, and a marker dragged with the mouse lands on a sample a script
    can compare with the number a knob was set to. ``0`` answers ``0.0``, never
    ``-0.0``, the way :func:`crude.ui.behaviour._tidy` does for a value.
    """
    found = round(float(value))
    return 0.0 if found == 0 else float(found)


def clip_of(widget: Any) -> Optional[Any]:
    """This widget, if it is a clip; ``None`` if it is not.

    The mirror of :func:`crude.ui.scroll.content_of`, and asked the same way for
    the same reason: :func:`follow_all` walks a whole tree and only some of it is
    made of clips. The chain is checked rather than the name so a project's
    ``WIDGET_DEFINE("take", "clip")`` is resolved by this module exactly as the
    kind it was built on is - a variant inherits the props, the face and the
    gestures, and it would be a strange inheritance that lost the arithmetic.
    """
    kind = getattr(widget, "kind", None)
    if kind is None or CLIP_KIND not in kind.chain():
        return None
    return widget


def frames_of(clip: Any) -> float:
    """How many samples the clip holds: the length of ``data``.

    A float, because every number here ends up multiplied against a pixel. An
    empty ``data`` is a real state - a clip built before the take arrived, or one
    a script has not filled yet - and it answers ``0.0`` rather than raising: the
    face draws silence and the gesture moves nothing, which is the honest picture
    of a window onto nothing.
    """
    return float(len(clip.get("data") or ()))


def samples_of(clip: Any) -> List[float]:
    """``data`` as numbers, in order, anything unreadable folded to silence.

    A face never trusts a prop list - :func:`crude.ui.faces._levels` says why, and
    this is the same discipline one layer down: ``data`` is a ``SET`` away from
    ``["a", None, 2]``, and the fold is where a sample is *chosen*, so a TypeError
    here would be a frame that stopped drawing. A non-number becomes 0.0, which is
    the sample of silence, and keeps its place in the window rather than shifting
    every marker after it.
    """
    out: List[float] = []
    for value in (clip.get("data") or ()):
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def window_of(clip: Any) -> Tuple[float, float]:
    """``(first, span)`` of the visible window, resolved against what is there.

    ``count`` 0 is the whole take, and a count longer than the take is the take -
    neither is written back, so a script that asked for "everything" is still
    asking for everything after a recording grows. ``first`` is clamped so the
    window can never start past the end, and a clip with no data has a window of
    ``(0.0, 0.0)``: there is nothing to see, and the callers below all divide only
    after checking the span.
    """
    total = frames_of(clip)
    asked = float(clip.get("count") or 0.0)
    span = total if asked <= 0.0 else min(asked, total)
    first = float(clip.get("start") or 0.0)
    if first < 0.0:
        first = 0.0
    if first > total - span:
        first = max(0.0, total - span)
    return first, span


def plot_box(clip: Any) -> Tuple[float, float, float, float]:
    """The box the window is drawn in: the widget's own content box.

    Not the whole box, which is the rule every other canvas follows: the skin's
    padding and the plate's border are not samples, and a marker placed against
    the outer edge would sit a few pixels off the sound it claims to mark.
    """
    return clip.content_box()


def sample_at(clip: Any, x: Any) -> float:
    """The sample one pixel of the box means.

    The forward half of the mapping, and deliberately not guarded against a
    pointer outside the box: a drag that leaves the plate keeps going (the router
    gives a widget the *movement*, not the position, and a marker that froze at
    the edge would be a clip that refuses the end of its own sample), so it is
    :func:`set_marker` that clamps and not this.
    """
    left, _top, width, _height = plot_box(clip)
    first, span = window_of(clip)
    if width <= 0.0 or span <= 0.0:
        return first
    return first + (float(x) - left) / width * span


def pixel_of(clip: Any, sample: Any) -> float:
    """The pixel one sample of the window means: :func:`sample_at` inverted."""
    left, _top, width, _height = plot_box(clip)
    first, span = window_of(clip)
    if span <= 0.0:
        return left
    return left + (float(sample) - first) / span * width


def samples_per_pixel(clip: Any) -> float:
    """What one pixel of the box is worth in samples, at this zoom.

    A drag and a wheel carry a *movement*, never a position, so the only
    arithmetic a hand can do on a clip is to convert the pixels it moved into the
    samples they mean - which is this one division, asked at the width the box has
    and the span the window has, both of them read through the same
    :func:`window_of` the face draws with. A zoomed-in clip therefore answers a
    small number and a marker can be placed a few samples at a time; a clip whose
    window is empty answers ``0.0``, because there is no ruler there.
    """
    _first, span = window_of(clip)
    _left, _top, width, _height = plot_box(clip)
    if width <= 0.0 or span <= 0.0:
        return 0.0
    return span / width


def columns_of(clip: Any) -> int:
    """How many vertical strips the face folds the window into.

    One per pixel of the box, unless ``points`` names a smaller budget - which is
    the whole reason a 200 000-sample take can be drawn at all: a polyline with a
    point per sample is a quarter of a megabyte of coordinates, and the plate it
    arrives on is a few hundred pixels wide. The number is a count of *columns*,
    each of which becomes one min/max pair; it is never zero, because a box of no
    width has nothing to draw and the caller stops there rather than dividing.
    """
    _left, _top, width, _height = plot_box(clip)
    if width <= 0.0:
        return 0
    held = int(width)
    budget = int(float(clip.get("points") or 0.0))
    if budget > 0:
        held = min(held, budget)
    return max(1, held)


def envelope(clip: Any) -> List[Tuple[float, float, float]]:
    """The visible window as ``(x, lowest, highest)`` per column.

    Min/max per strip, not one point per sample: a sample waveform is symmetric
    about silence and a single line through 200 000 samples would both be slow and
    hide the sound - the peaks are what a person is aiming at when they reach for
    a marker. The values come back at full scale and unclamped, because clamping a
    sample to the plate is the drawing's decision (:func:`crude.ui.faces._levels`
    makes it) and a fold that clamped too would put the limit in two places.

    Pure, and empty when there is nothing to fold, which is what lets the plate
    draw the flat line of silence instead of a graph of nothing.
    """
    held = samples_of(clip)
    first, span = window_of(clip)
    total = float(len(held))
    left, _top, width, _height = plot_box(clip)
    count = columns_of(clip)
    if not held or span <= 0.0 or count <= 0 or width <= 0.0:
        return []
    out: List[Tuple[float, float, float]] = []
    step = span / count
    slot = width / count
    for column in range(count):
        low = int(min(max(first + column * step, 0.0), total))
        high = int(min(max(first + (column + 1) * step, 0.0), total))
        if high <= low:
            high = min(low + 1, len(held))
        if low >= high:
            break  # the window sits past the end of the sample; there is no more
        chunk = held[low:high] or held[low:low + 1]
        if not chunk:
            break
        out.append((left + (column + 0.5) * slot, min(chunk), max(chunk)))
    return out


def marker_pixels(clip: Any) -> List[Tuple[float, float]]:
    """``(index, x)`` for every marker the window shows.

    A marker outside the window is left out rather than pinned to the nearest
    edge: a line clamped to the frame would be a mark claiming the first sample of
    the strip is where the take starts, and a person who has scrolled away from
    their loop points wants the strip to say so. The indices are the list's own,
    because it is the list :func:`set_marker` writes back.
    """
    first, span = window_of(clip)
    _left, _top, _width, _height = plot_box(clip)
    out: List[Tuple[float, float]] = []
    for index, value in enumerate(clip.get("markers") or ()):
        try:
            held = float(value)
        except (TypeError, ValueError):
            continue
        if held < first or held > first + span:
            continue
        out.append((float(index), pixel_of(clip, held)))
    return out


def nearest_marker(clip: Any, x: Any) -> Optional[float]:
    """Which marker a pixel takes, or ``None`` when the press grabs the window.

    The same pixels :func:`marker_pixels` draws, and the same ``GRAB`` tolerance
    in both directions from them: nearest wins rather than first-found, so two
    markers a pixel apart are settled by which one the pointer was nearer the
    middle of, and one that is not on screen cannot be taken at all.
    """
    wanted = float(x)
    best: Optional[float] = None
    room = None
    for index, across in marker_pixels(clip):
        held = abs(across - wanted)
        if held > GRAB:
            continue
        if room is None or held < room:
            best, room = index, held
    return best


def set_marker(clip: Any, index: Any, sample: Any) -> float:
    """Move one marker to ``sample``; the position it landed at.

    Clamped to the sample it belongs to - 0 at the first, ``data``'s length at the
    end - and written as a whole list, because ``markers`` is one row and a
    partial edit of a list prop is not a thing the table can settle. An unchanged
    write is not a change: :meth:`~crude.ui.widget.Widget.set` compares and stays
    silent, which is what keeps a marker dragged to the place a knob had already
    put it from echoing back into the knob that moved it.
    """
    held = [float(value) for value in (clip.get("markers") or ())]
    wanted = int(index)
    if not held or not 0 <= wanted < len(held):
        return 0.0
    total = frames_of(clip)
    found = _whole(sample)
    if total > 0.0:
        found = min(max(found, 0.0), total)
    held[wanted] = found
    clip.set("markers", held)
    return found


def clamp_window(clip: Any, start: Any, count: Any) -> Tuple[float, float]:
    """``(start, count)`` as this clip would hold them, given what it holds.

    The one clamp, in words once: a count of 0 (or less) is the whole take and
    stays written as 0, so "show everything" survives a recording that grows; any
    other count is kept exactly as asked for - a window wider than the sample is
    resolved by :func:`window_of` at draw time rather than being quietly shortened
    in the row - and the first sample is pulled back so the window never starts
    past the end.
    """
    total = frames_of(clip)
    asked = _whole(count)
    if asked <= 0.0:
        return 0.0, 0.0
    span = min(asked, total) if total > 0.0 else asked
    return max(0.0, min(_whole(start), max(0.0, total - span))), asked


def set_window(clip: Any, start: Any, count: Any) -> Tuple[float, float]:
    """Write the window, clamped; the ``(start, count)`` the clip ended up with.

    Both rows in one call, because they are one fact: a zoom that moved the first
    sample and not the width would be a frame that draws the wrong half of the
    take between the two writes. Order matters for the same reason a constructor
    settles ``min`` before ``value`` - the first is clamped against the width the
    clip is about to have, so the count goes first.
    """
    held_start, held_count = clamp_window(clip, start, count)
    if held_count > 0.0:
        clip.set("count", held_count)
    else:
        clip.set("count", 0.0)
    clip.set("start", held_start)
    return float(clip.get("start") or 0.0), float(clip.get("count") or 0.0)


def zoom_at(clip: Any, x: Any, notches: Any) -> Tuple[float, float]:
    """Zoom by ``notches`` steps with the sample under ``x`` held still.

    Holding the pointer's sample fixed is the whole of what makes a zoom usable:
    a window that scaled about its own left edge would slide the waveform out from
    under the cursor every time the wheel moved, and the person would be chasing
    the peak they were trying to reach. The step is a ratio (see
    :data:`ZOOM_STEP`), the span never goes below one sample, and zooming out
    past the take simply *is* the take - :func:`clamp_window` writes the 0 that
    means everything, so a clip that has been zoomed out carries no number that
    will go stale when the next sample arrives.
    """
    first, span = window_of(clip)
    if span <= 0.0:
        return first, span
    held = float(notches)
    if not held:
        return first, span  # a wheel that did not move is not a zoom
    wanted = span / (ZOOM_STEP ** held)
    total = frames_of(clip)
    if wanted < MIN_WINDOW:
        wanted = MIN_WINDOW
    if total > 0.0 and wanted >= total:
        return set_window(clip, 0.0, 0.0)
    aimed = sample_at(clip, x)
    # the pointer's fraction of the window, kept as the window changes size
    share = (aimed - first) / span
    return set_window(clip, aimed - wanted * share, wanted)


def pan_from(clip: Any, anchor: Any, pull: Any) -> Tuple[float, float]:
    """Slide the window back by ``pull`` samples from where the grab started.

    The hand is holding the *take*, not a scrollbar, so a drag right moves the
    view left along it - the same reading a viewport's thumb arrives at from the
    other side. The anchor is the first sample the window had when the press
    closed on it and ``pull`` is everything the pointer has moved since, in
    samples: a drag carries a movement, so the total is what a gesture has to
    keep, and a window measured from an anchor cannot drift by an increment the
    way one added up from increments would.

    A window that already is the whole take slides nowhere, and is left alone:
    ``count`` 0 means "everything, including what arrives next", and rewriting it
    as the length the take happens to have today would freeze a recording that is
    still growing.
    """
    first, span = window_of(clip)
    total = frames_of(clip)
    if span <= 0.0 or span >= total:
        return first, span
    return set_window(clip, float(anchor) - float(pull), span)


def apply_follow(clip: Any) -> bool:
    """Slide the window so the playhead is centred; whether anything moved.

    The rule every timeline editor uses and the reason it is a pass rather than a
    prop's own behaviour: centring is *clamped*, so the view stops sliding at the
    start of the take and at its end instead of inventing samples that are not
    there, and a cursor that has been switched off (``playhead`` -1) or a clip that
    already shows the whole take cannot move at all - both write nothing, and an
    unchanged write is silent, which is what keeps an idle frame idle.

    Returns ``True`` only when the window was rewritten, because the layout pass
    counts the boxes it moved and this belongs in the same total.
    """
    if clip_of(clip) is None or not clip.get("follow"):
        return False
    held = float(clip.get("playhead") or 0.0)
    if held < 0.0:
        return False
    _first, span = window_of(clip)
    total = frames_of(clip)
    if span <= 0.0 or total <= 0.0 or span >= total:
        return False
    before = (float(clip.get("start") or 0.0), float(clip.get("count") or 0.0))
    return set_window(clip, held - span * 0.5, span) != before


def follow_all(root: Any) -> int:
    """Every clip under ``root``, followed; how many windows were rewritten.

    Run by the layout pass next to :func:`~crude.ui.scroll.restack`, for the
    reason restack is run there: the answer depends on the box the widget was
    given, so a face would be asked to do it and a painter may not write a prop.
    A frame with no clips in it costs one walk and no arithmetic.
    """
    held = 0
    for widget in root.walk():
        found = clip_of(widget)
        if found is not None and apply_follow(found):
            held += 1
    return held
