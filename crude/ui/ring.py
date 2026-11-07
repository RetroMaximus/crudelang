"""A scope's ring, kept as one texture that numpy redraws instead of a stroke.

A circular waveform with any useful number of samples is thousands of short line
segments, and a widget that costs a frame in geometry is a widget that cannot be
in a build. The other way round is to treat the ring as an image: the trace's
radius at every angle is a table, the pixels that fall near that radius are
covered, and the whole thing reaches the screen as one atlas blit - numpy per
frame instead of a batch per sample.

That is all this file is: a *model* of such a texture, in plain numbers, with no
GL in it. It answers a coverage plane and a key, and any
:class:`~crude.ui.base.Renderer` can be told to use them, so a test can build a
scope, feed it a sine and read the pixels or the recorded op without a display.

* **White, tinted at draw time** - the plane is coverage only, the same convention
  a glyph uses, so :meth:`draw` colours it and a theme stays a colour change.
* **One upload per change** - :meth:`upload` does nothing until something moved,
  and refills an existing texture rather than allocating a new one, so a frame
  with a silent input touches the GPU not at all.
* **DPI is a scale, not a resize** - :meth:`set_scale` rebuilds at
  ``radius * scale`` device pixels while :attr:`bounds` stays in logical ones.
"""

from __future__ import annotations

import math
from itertools import count
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np

from .base import WHITE, Renderer
from .geometry import TAU, clamp

#: sample points around the circle: fewer than this and a fast signal aliases
DEFAULT_SEGMENTS = 180
#: the radius the trace sits at when the signal is at zero, in logical pixels
DEFAULT_RADIUS = 48.0
#: how thick the trace is, in logical pixels
DEFAULT_BAND = 3.0
#: a texture this wide is already bigger than the widget it will be blitted into
MAX_SIZE = 2048

#: every ring gets its own key unless the caller names one
_serial = count(1)


def _finite(values: Any) -> List[float]:
    """The numbers in ``values``, with anything the device did not answer as zero.

    An audio callback that has not run yet hands over a buffer of ``nan`` or
    ``inf``, and a ring is not the place to decide what that means: silence is.
    """
    if values is None:
        return []
    if isinstance(values, (int, float)):
        return [float(values)]
    out: List[float] = []
    for value in np.asarray(list(values), dtype=np.float64).ravel().tolist():
        out.append(value if math.isfinite(value) else 0.0)
    return out


def resample(values: Sequence[float], segments: int) -> np.ndarray:
    """``values`` stretched or squeezed to exactly ``segments`` points.

    Linear, and wrapping at the seam: the last point is joined back to the first,
    because a ring has no end - so one period of a sine fed in as ``[0, 1, 0, -1]``
    becomes a closed trace rather than a step where angle 0 meets itself.
    """
    segments = max(1, int(segments))
    source = np.asarray(_finite(values), dtype=np.float64)
    if source.size == 0:
        return np.zeros(segments, dtype=np.float64)
    if source.size == 1:
        return np.full(segments, float(source[0]), dtype=np.float64)
    closed = np.concatenate([source, source[:1]])
    steps = np.linspace(0.0, source.size, segments, endpoint=False)
    low = np.floor(steps).astype(np.int64)
    weight = steps - low
    return closed[low] * (1.0 - weight) + closed[low + 1] * weight


class RingTexture:
    """One circular scope trace, as a texture key and the pixels behind it."""

    def __init__(self, radius: float = DEFAULT_RADIUS, *,
                 band: float = DEFAULT_BAND,
                 deflection: Optional[float] = None,
                 segments: int = DEFAULT_SEGMENTS,
                 scale: float = 1.0,
                 key: Optional[str] = None) -> None:
        self.radius = max(0.0, float(radius))
        self.band = max(0.5, float(band))
        #: how far a full-scale sample moves the trace out from :attr:`radius`
        self.deflection = (self.band * 2.0 if deflection is None
                           else max(0.0, float(deflection)))
        self.segments = max(2, int(segments))
        self.scale = max(0.01, float(scale))
        #: the texture's own name: handed to the renderer once, then refilled
        self.key = str(key) if key else f"ring:{next(_serial)}"
        self.dirty = True
        #: the ring has never been uploaded anywhere
        self.uploads = 0
        self._values = np.zeros(self.segments, dtype=np.float64)
        self._build()

    # -- the shape of the picture -----------------------------------------
    def _build(self) -> None:
        """Measure every pixel's angle and distance from the centre, once.

        These two tables are the whole cost of a rebuild; a frame afterwards only
        moves the radius each pixel is being asked to reach.
        """
        side = self.bounds[0] * self.scale
        if side > MAX_SIZE:
            # the scale is folded back rather than the picture cut short: a ring
            # missing its outer third is a broken scope, a slightly soft one is not
            self.scale = max(0.01, MAX_SIZE / self.bounds[0])
            side = self.bounds[0] * self.scale
        self._size = int(max(1, math.ceil(side)))
        centre = self._size * 0.5
        axis = np.arange(self._size, dtype=np.float64) + 0.5 - centre
        self._distance = np.hypot(axis[np.newaxis, :], axis[:, np.newaxis])
        # y runs down here as it does everywhere else in this package, so an angle
        # measured against these pixels is the angle a widget asked for
        self._angle = np.arctan2(axis[:, np.newaxis],
                                 axis[np.newaxis, :]) % TAU
        self._step = self.segments / TAU
        # one device pixel of soft edge, whatever the scale, so a thin ring at 1x
        # is not a dotted line and a thick one at 3x is not a hard stair
        self.feather = max(0.75, self.scale)
        self.dirty = True

    @property
    def bounds(self) -> Tuple[float, float]:
        """The box the ring fills, in logical pixels - square, always."""
        side = (self.radius + self.deflection + self.band) * 2.0
        return (side, side)

    @property
    def size(self) -> float:
        """The width of that box, so a widget can place itself around it."""
        return self.bounds[0]

    @property
    def pixel_size(self) -> Tuple[int, int]:
        """The texture's own size, in device pixels."""
        return (self._size, self._size)

    def set_shape(self, *, radius: Optional[float] = None,
                  band: Optional[float] = None,
                  deflection: Optional[float] = None,
                  segments: Optional[int] = None) -> "RingTexture":
        """Change the geometry - a layout pass that resized the widget lands here."""
        changed = False
        if radius is not None and float(radius) != self.radius:
            self.radius = max(0.0, float(radius))
            changed = True
        if band is not None and float(band) != self.band:
            self.band = max(0.5, float(band))
            changed = True
        if deflection is not None and float(deflection) != self.deflection:
            self.deflection = max(0.0, float(deflection))
            changed = True
        if segments is not None and int(segments) != self.segments:
            self.segments = max(2, int(segments))
            self._values = resample(self._values.tolist(), self.segments)
            changed = True
        if changed:
            self._build()
        return self

    def set_scale(self, scale: float) -> "RingTexture":
        """Re-cut the texture for another device pixel ratio."""
        scale = max(0.01, float(scale))
        if scale != self.scale:
            self.scale = scale
            self._build()
        return self

    # -- the signal -------------------------------------------------------
    @property
    def values(self) -> Tuple[float, ...]:
        """The sample at each of the :attr:`segments` points, at full scale."""
        return tuple(self._values.tolist())

    def set_values(self, values: Any, *, low: float = -1.0,
                   high: float = 1.0) -> "RingTexture":
        """Take one block of signal, whatever range it was measured in.

        ``low`` and ``high`` are the values that mean "the trace is at its
        innermost and its outermost", so a waveform goes in as it comes off the tap
        (``-1..1``) and a level goes in as ``0..1`` with neither pre-scaled. More or
        fewer samples than the ring has points are resampled, and nothing is ever
        allowed past full scale - a clipped ring is honest about being clipped.
        """
        lo, hi = float(low), float(high)
        if hi == lo:
            hi = lo + 1.0
        # -1..1 inside, so the deflection is one number and the clamp is one rule
        scaled = [(value - lo) / (hi - lo) * 2.0 - 1.0
                  for value in _finite(values)]
        self._values = np.clip(resample(scaled, self.segments), -1.0, 1.0)
        self.dirty = True
        return self

    def set_level(self, value: float) -> "RingTexture":
        """One number held all the way round: a ring as a bar graph."""
        return self.set_values([value], low=0.0, high=1.0)

    def push(self, value: float) -> "RingTexture":
        """Scroll by one sample and add this one at the front.

        The rolling view a scope wants when samples arrive one by one: newest at
        angle zero, oldest about to come round and meet it.
        """
        rolled = np.roll(self._values, 1)
        rolled[0] = clamp(float(value), -1.0, 1.0)
        self._values = rolled
        self.dirty = True
        return self

    def clear(self) -> "RingTexture":
        """Silence: the trace back on a flat circle at :attr:`radius`."""
        self._values = np.zeros(self.segments, dtype=np.float64)
        self.dirty = True
        return self

    def target_radius(self, angle: float) -> float:
        """How far out the trace sits at ``angle``, in logical pixels.

        Interpolated between the sample points, so a caller checking where a peak
        landed does not have to know how many points the ring happens to have.
        """
        position = (float(angle) % TAU) * self._step
        low = int(math.floor(position)) % self.segments
        weight = position - math.floor(position)
        first = float(self._values[low])
        second = float(self._values[(low + 1) % self.segments])
        return self.radius + self.deflection * (first + (second - first) * weight)

    # -- the pixels -------------------------------------------------------
    def coverage(self) -> np.ndarray:
        """A ``(size, size)`` plane of how covered each device pixel is, 0..255.

        A pixel belongs to the trace as far as it is near the radius the signal asks
        for at *its own* angle, so the whole test is one distance against ``band``:
        no path, no scanline, no per-sample loop. Everything in here is measured in
        device pixels, because that is what the tables were built in.
        """
        wanted = (self.radius + self.deflection * self._values_at_angles()) \
            * self.scale
        stray = np.abs(self._distance - wanted)
        alpha = np.clip((self.band * 0.5 * self.scale + self.feather - stray)
                        / self.feather, 0.0, 1.0)
        # smoothstep, because a linear falloff still reads as a stair on a curve
        alpha = alpha * alpha * (3.0 - 2.0 * alpha)
        return (alpha * 255.0).round().astype(np.uint8)

    def pixels(self) -> np.ndarray:
        """The ring as an RGBA image: white, with :meth:`coverage` for its alpha."""
        flat = np.full((self._size, self._size), 255, np.uint8)
        return np.dstack([flat, flat, flat, self.coverage()])

    def _values_at_angles(self) -> np.ndarray:
        """Every pixel's sample, linear between the two either side of it."""
        position = self._angle * self._step
        low = np.floor(position).astype(np.int64) % self.segments
        weight = position - np.floor(position)
        first = self._values[low]
        second = self._values[(low + 1) % self.segments]
        return first + (second - first) * weight

    # -- the renderer -----------------------------------------------------
    def upload(self, renderer: Renderer) -> str:
        """Give the ring's pixels to ``renderer``, only if they have moved.

        The plane goes in through :meth:`~crude.ui.base.Renderer.upload_texture`,
        which is the contract's way of refilling a texture that is already on the
        GPU: a scope at sixty frames a second allocates nothing per frame.
        """
        if self.dirty or self.key not in renderer.texture_keys():
            renderer.upload_texture(self.key, self.coverage())
            self.dirty = False
            self.uploads += 1
        return self.key

    def draw(self, renderer: Renderer, x: float, y: float, *,
             colour: Any = WHITE, width: Optional[float] = None,
             height: Optional[float] = None) -> None:
        """Blit the ring into ``x, y``, in logical pixels, tinted ``colour``.

        One op for a thousand samples is the entire point, and the tint is the
        widget's to choose: the texture itself is white.
        """
        self.upload(renderer)
        side = self.size
        renderer.atlas(x, y, side if width is None else float(width),
                       side if height is None else float(height),
                       self.key, color=colour)

    def draw_centred(self, renderer: Renderer, cx: float, cy: float, *,
                     colour: Any = WHITE) -> None:
        """The same blit with the ring's own centre at ``cx, cy``."""
        side = self.size
        self.draw(renderer, cx - side * 0.5, cy - side * 0.5, colour=colour)

    def __str__(self) -> str:
        return (f"RingTexture('{self.key}' r={self.radius:g} "
                f"band={self.band:g} out={self.deflection:g} "
                f"{self.segments} pts, {self._size}px, "
                f"{'needs' if self.dirty else 'holding'} upload)")


__all__ = ["RingTexture", "resample", "DEFAULT_SEGMENTS", "DEFAULT_RADIUS",
           "DEFAULT_BAND", "MAX_SIZE"]
