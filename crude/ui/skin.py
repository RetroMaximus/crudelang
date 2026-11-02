"""What every widget looks like: tokens in, roles out, no colour in a widget.

The plan's rule is that **style is a role, never a raw colour** - a script says
``STYLE(w, "role", "danger")`` and a painter says ``P_RECT(p, 0, 0, w, h, 4,
"surface")`` - because the alternative is a hundred widgets each with its own
hex string, and no theme that can reach any of them. This module is the only
place those numbers and colours live.

Three pieces, and it matters which is which:

* **A palette token** is a raw value with a job-neutral name (``surface``,
  ``accent``, ``radius.md``). A :class:`Theme` is a table of them, and is the
  only thing a theme switch changes.
* **A role** is what a widget part *is* (``button``, ``track``, ``text``) and
  says which tokens it uses for its fill, ink, outline and metrics, plus which
  token it switches to when it is on. Roles are one table shared by every theme
  - that is what makes a new widget inherit the look instead of hand-rolling it.
* **A :class:`Skin`** is a theme plus a size scale (``sm``/``md``/``lg``), and is
  the only object a widget talks to. :meth:`Skin.resolve` answers the whole
  question a part asks - background, border, glyph, label, corner, padding - in
  one call, so ``bg + border + glyph + label`` is four draws and no arithmetic.

States (``hover``, ``pressed``, ``disabled``...) are *derived*, not authored: a
theme does not list what a hovered button costs, because that is the same
question for every fill and answering it once here means ``light`` gets working
hover for free. A role that cannot be hovered says so, which is why text does
not brighten when the pointer crosses it. A state is also two halves rather than
one word - what the pointer is doing and what the widget holds are different
things, so ``resolve(w, "on hover")`` is a real question, and the highlight
directions ask the theme which way it is light before they lift anything.

Nothing here touches the device pixel ratio. Layout solves in logical pixels and
rounds once at draw (the plan's risk 20), and the renderer folds the ratio into
its own projection; the skin's :attr:`Skin.scale` is the *size* axis a script
chooses, which is a different thing and must not be confused with a display.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .base import RGBA, UIError, as_color, clamp

__all__ = ["Appearance", "DEFAULT_THEME", "Role", "Skin", "Theme", "describe",
           "COLLOUR_TOKENS", "NUMBER_TOKENS", "ROLES", "SIZES", "STATES",
           "THEMES", "contrast", "luminance", "mix", "shade", "as_theme",
           "as_role", "as_scale", "colour_token_names", "number_token_names",
           "role_names", "state_names", "theme_names"]

#: the states a part can be asked about. ``normal`` is not a look, it is the
#: absence of one, and it is what a widget with no interaction is drawn in.
#:
#: The list is flat but the model behind it is not: ``hover``, ``pressed``,
#: ``focus`` and ``disabled`` are what the pointer and the keyboard are doing
#: *now*, while ``on`` and ``selected`` are what the widget *holds*. A toggle
#: that is on is still hovered while the pointer sits on it, so a state is
#: allowed to be two words - ``"on hover"`` - and :class:`Skin` answers the pair
#: rather than making a widget choose which half matters.
STATES: Tuple[str, ...] = ("normal", "hover", "pressed", "focus", "disabled",
                           "on", "selected")

#: the pointer's half of a state, in the order a widget reports them
_INTERACTIONS: Tuple[str, ...] = ("normal", "hover", "pressed", "focus",
                                  "disabled")

#: the size axis a script picks with ``STYLE(w, "size", "sm")``
SIZES: Dict[str, float] = {"xs": 0.7, "sm": 0.85, "md": 1.0, "lg": 1.2,
                           "xl": 1.45}

#: the palette every theme has to fill in. A missing token is a construction
#: error, so a theme cannot half-exist.
COLLOUR_TOKENS: Tuple[str, ...] = (
    "bg", "surface", "raised", "sunken", "border", "edge", "text", "dim",
    "faint", "accent", "ink", "good", "warn", "bad", "info", "shadow",
)

#: the metrics every theme has to fill in. Dotted names group them without a
#: second table per category.
NUMBER_TOKENS: Tuple[str, ...] = (
    "radius.none", "radius.sm", "radius.md", "radius.lg", "radius.full",
    "border.thin", "border.medium", "border.thick",
    "pad.none", "pad.xs", "pad.sm", "pad.md", "pad.lg",
    "font.micro", "font.caption", "font.body", "font.title",
)

#: the states that mean "use the role's on token", and so change the ink too
_ON_STATES = ("on", "selected")


# --------------------------------------------------------------------------
# colour arithmetic the palette cannot do for itself
# --------------------------------------------------------------------------


def mix(color: Any, other: Any, weight: float = 0.5) -> RGBA:
    """``color`` and ``other`` blended; ``weight`` is how much of ``other``.

    One function rather than per-theme arithmetic, so a theme that wanted its
    focus ring three shades of its own blue gets it by naming the token instead
    of by restating every rule that uses one.
    """
    here = as_color(color)
    there = as_color(other)
    t = clamp(float(weight), 0.0, 1.0)
    return RGBA(here.red + (there.red - here.red) * t,
                here.green + (there.green - here.green) * t,
                here.blue + (there.blue - here.blue) * t,
                here.alpha + (there.alpha - here.alpha) * t)


def shade(color: Any, factor: float) -> RGBA:
    """The same colour multiplied by ``factor``, clipped to what a screen shows.

    Under 1.0 sinks, over 1.0 lifts. Multiplying rather than blending towards
    black keeps a tint's hue, which is why a darkened blue is still blue - but it
    is also why no state rule uses it: a multiplier cannot move a colour that is
    already at an end of the range, so see :func:`_shift` for the highlights.
    """
    here = as_color(color)
    scale = max(0.0, float(factor))
    return RGBA(clamp(here.red * scale, 0.0, 1.0),
               clamp(here.green * scale, 0.0, 1.0),
               clamp(here.blue * scale, 0.0, 1.0), here.alpha)


def luminance(color: Any) -> float:
    """How light a colour is, 0.0 to 1.0 - Rec.709 weights, no gamma lift.

    Not a WCAG number, and not trying to be: it answers the two questions a skin
    actually asks, which is "is this theme light or dark" and "did this state
    move the fill at all". A theme's hover that leaves the luminance where it
    was is a button that does not respond to the pointer, and no amount of
    eyeballing a hex table catches that as cheaply as subtracting two numbers.
    """
    here = as_color(color)
    return (0.2126 * here.red + 0.7152 * here.green + 0.0722 * here.blue)


def contrast(first: Any, second: Any) -> float:
    """The luminance gap between two colours, 0.0 (identical) to 1.0.

    Used to say what "readable" means in a test rather than in a comment.
    """
    return abs(luminance(first) - luminance(second))


def _shift(color: Any, amount: float) -> RGBA:
    """Every channel moved by ``amount``: lighter if positive, darker if not.

    Addition rather than multiplication, because a multiplier cannot move a
    colour that is already near one end of the range - ``#20262e`` times 0.94 is
    a change nobody can see, and times 1.10 is a change that does not exist at
    the other end. Since the :func:`luminance` weights sum to 1, a shift of
    ``0.06`` is a promise about what appears on screen, which is what a state
    highlight has to be.
    """
    here = as_color(color)
    said = clamp(float(amount), -1.0, 1.0)
    return RGBA(clamp(here.red + said, 0.0, 1.0),
                clamp(here.green + said, 0.0, 1.0),
                clamp(here.blue + said, 0.0, 1.0), here.alpha)


def _react(color: Any, amount: float) -> RGBA:
    """Move ``color`` by ``amount``, or by the opposite if it has no room that way.

    One fill in every palette contradicts its own theme's direction, because a
    light theme's knob is the darkest thing in it: a step that would be clipped
    to nothing is taken the other way instead, at the same size. Either way the
    fill moves by about ``|amount|``, which is the whole requirement - the
    alternative is a theme where a button does not answer the pointer.
    """
    here = luminance(color)
    said = float(amount)
    if said > 0.0 and here + said > 1.0 >= here - said:
        said = -said
    elif said < 0.0 and here + said < 0.0 <= here - said:
        said = -said
    return _shift(color, said)


# --------------------------------------------------------------------------
# a theme: the tokens
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    """One set of palette tokens, and the only thing a theme switch changes.

    Values are accepted in any of the forms
    :func:`~crude.ui.base.as_color` takes, so a theme can be written as a table
    of ``#rrggbb`` strings and read back as the floats a shader wants.
    """

    name: str
    colours: Dict[str, RGBA] = field(default_factory=dict)
    numbers: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = [token for token in COLLOUR_TOKENS
                   if token not in self.colours]
        missing += [token for token in NUMBER_TOKENS
                    if token not in self.numbers]
        if missing:
            raise UIError(f"theme '{self.name}' has no value for "
                          f"{', '.join(missing)}")

    def colour(self, token: str) -> RGBA:
        """One palette colour, by token name."""
        try:
            return self.colours[token]
        except KeyError:
            raise UIError(f"theme '{self.name}' has no colour token "
                          f"'{token}'") from None

    def number(self, token: str) -> float:
        """One palette metric, by token name."""
        try:
            return self.numbers[token]
        except KeyError:
            raise UIError(f"theme '{self.name}' has no number token "
                          f"'{token}'") from None

    @property
    def is_light(self) -> bool:
        """Whether this theme's own background is light - derived, not declared.

        A theme cannot get this wrong the way a ``polarity`` field could, and a
        project that writes its own palette gets the right hover direction for
        free, which matters because every state rule below turns on it.
        """
        return luminance(self.colour("bg")) > 0.5

    def names(self) -> Tuple[str, ...]:
        """The two kinds of token a theme is made of."""
        return ("colours", "numbers")

    def token_names(self) -> Tuple[str, ...]:
        """Every token this theme has to answer for, colours then numbers."""
        return (*COLLOUR_TOKENS, *NUMBER_TOKENS)

    def __str__(self) -> str:
        return (f"Theme({self.name}, {len(self.colours)} colours, "
                f"{len(self.numbers)} numbers)")


def _theme(name: str, colours: Mapping[str, str],
           numbers: Mapping[str, float]) -> Theme:
    """Build a theme from the readable table below; validates as it goes."""
    return Theme(name,
                 {token: as_color(value) for token, value in colours.items()},
                 {token: float(value) for token, value in numbers.items()})


# The three themes the plan asks for. dark is the one the reference UI already
# used - a blue-tinted charcoal with a sky-blue accent - so a project that grew
# up in ui/panels.py keeps its look; light is the same roles over paper; flat
# keeps the colours of dark and takes the corners and the shadows away, which is
# the one difference "flat" actually means.
_METRICS = {
    "radius.none": 0.0, "radius.sm": 2.0, "radius.md": 4.0, "radius.lg": 8.0,
    "radius.full": 999.0,
    "border.thin": 1.0, "border.medium": 2.0, "border.thick": 3.0,
    "pad.none": 0.0, "pad.xs": 2.0, "pad.sm": 4.0, "pad.md": 8.0,
    "pad.lg": 12.0,
    "font.micro": 9.0, "font.caption": 11.0, "font.body": 13.0,
    "font.title": 18.0,
}

_FLAT_METRICS = dict(_METRICS, **{
    "radius.sm": 0.0, "radius.md": 0.0, "radius.lg": 0.0, "radius.full": 0.0,
    "border.thin": 1.0, "border.medium": 1.0, "border.thick": 2.0,
})

THEMES: Dict[str, Theme] = {
    "dark": _theme("dark", {
        "bg": "#171a20",
        "surface": "#242933",
        "raised": "#2b323d",
        "sunken": "#1b1f26",
        "border": "#3b424f",
        "edge": "#6f9fd8",
        "text": "#dbe4f0",
        "dim": "#a9b6c9",
        "faint": "#5f6a7b",
        "accent": "#47adff",
        "ink": "#10151b",
        "good": "#4ecb71",
        "warn": "#f0b344",
        "bad": "#f0564f",
        "info": "#47adff",
        "shadow": "#0b0d11",
    }, _METRICS),
    "flat": _theme("flat", {
        "bg": "#171a20",
        "surface": "#22262d",
        "raised": "#2c313a",
        "sunken": "#1a1d23",
        "border": "#3a4048",
        "edge": "#7aa7d9",
        "text": "#e2e6ec",
        "dim": "#9aa4b2",
        "faint": "#565e6b",
        "accent": "#3d9be0",
        "ink": "#10151b",
        "good": "#45bd68",
        "warn": "#e0a63c",
        "bad": "#e04f48",
        "info": "#3d9be0",
        "shadow": "#0b0d11",
    }, _FLAT_METRICS),
    "light": _theme("light", {
        "bg": "#e9ecf1",
        "surface": "#f6f8fb",
        "raised": "#ffffff",
        "sunken": "#dfe3ea",
        "border": "#b9c1cd",
        "edge": "#2f7cc4",
        "text": "#20262e",
        "dim": "#5a6472",
        "faint": "#9aa4b2",
        "accent": "#1d76c8",
        "ink": "#f7fbff",
        "good": "#1f9d4c",
        "warn": "#b7791f",
        "bad": "#c9372f",
        "info": "#1d76c8",
        "shadow": "#9aa4b2",
    }, _METRICS),
}

DEFAULT_THEME = "dark"


# --------------------------------------------------------------------------
# roles: which tokens a part uses
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Role:
    """What one part of a widget is made of, in token names rather than values.

    ``on`` is the token a part switches to when it is on or selected - a toggle,
    an active tab, a filled track - and ``on_ink`` is what is drawn on top of it,
    because a label that reads on ``surface`` does not necessarily read on
    ``accent``. ``states`` is the honest list of what the *pointer* can do to this
    part: text is not hoverable, and saying so is what stops a widget brightening
    a caption because a generic rule said all fills lift. Whether a part has an
    ``on`` at all is carried by ``on``, not repeated here.

    ``face`` says whether the role has a plate at all. It belongs to the role and
    not to a widget, because the question is "is a title a box with words in it or
    just words": a ``fill`` token that happens to be the surface colour underneath
    cannot answer it, and a widget that guessed would draw an opaque panel behind
    every label it was given. ``border`` and ``outline`` are a line somebody drew,
    so they have no plate either.

    ``shows`` is which words a role is drawn to carry: ``"label"`` is the name the
    widget was given, and ``"value"`` is the thing it holds - a ``field`` exists to
    be read off, so a number box shows its number and a folded list shows the entry
    it is pointing at. It is asked of the role and not of the kind because the
    widget's own answer to "what do I hold" is already the same formatter a ``SET``
    writes through; the only question left is whether the box is for reading that or
    for reading a name.
    """

    name: str
    fill: str = "surface"
    ink: str = "text"
    line: str = "border"
    on: str = ""
    on_ink: str = "ink"
    radius: str = "radius.md"
    border: str = "border.thin"
    padding: str = "pad.sm"
    font: str = "font.body"
    glyph: str = ""
    glyph_on: str = ""
    states: Tuple[str, ...] = ("hover", "pressed", "focus", "disabled")
    face: bool = True
    shows: str = "label"

    def responds(self, state: str) -> bool:
        """Whether an interaction changes this part at all."""
        return state in self.states

    def holds(self, state: str) -> bool:
        """Whether ``state`` is a value this part can be switched to."""
        return bool(self.on) and state in _ON_STATES


@dataclass(frozen=True)
class Appearance:
    """Everything one part needs to draw itself, already resolved.

    A widget's paint is ``rect(bg) + rect(border) + glyph + text(ink)`` and none
    of it is a decision; that is the point of handing the four numbers over at
    once instead of letting each widget ask for them in its own order.
    """

    role: str
    state: str
    fill: RGBA
    ink: RGBA
    border: RGBA
    radius: float
    width: float
    padding: float
    font: float
    glyph: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role, "state": self.state, "fill": str(self.fill),
            "ink": str(self.ink), "border": str(self.border),
            "radius": self.radius, "width": self.width,
            "padding": self.padding, "font": self.font, "glyph": self.glyph,
        }

    def __str__(self) -> str:
        return (f"{self.role}/{self.state} fill {self.fill} ink {self.ink} "
                f"line {self.border} r{self.radius:g} f{self.font:g}")


def _role(name: str, **values: Any) -> Role:
    """One row of the table below, with the name filled in twice."""
    return Role(name=name, **values)


#: every role a widget or a painter may name. Grouped the way a widget is built:
#: the faces a panel is made of, the parts a control is made of, the text, and
#: the four intents a script is allowed to mean.
ROLES: Dict[str, Role] = {
    # faces
    "window": _role("window", fill="bg", line="bg", radius="radius.none",
                    padding="pad.md", states=()),
    "panel": _role("panel", fill="surface", line="border", radius="radius.lg",
                   padding="pad.md", states=()),
    "card": _role("card", fill="surface", line="border", radius="radius.lg",
                  padding="pad.lg", states=()),
    "surface": _role("surface", fill="surface", line="border",
                     radius="radius.md", states=()),
    "tooltip": _role("tooltip", fill="raised", ink="text", line="border",
                     radius="radius.sm", padding="pad.xs",
                     font="font.caption", states=()),
    "placeholder": _role("placeholder", fill="sunken", ink="faint",
                         line="bad", radius="radius.sm", states=()),
    # parts of a control
    "button": _role("button", fill="raised", line="border", on="accent",
                    radius="radius.md", padding="pad.sm", glyph=""),
    "toggle": _role("toggle", fill="raised", line="border", on="accent",
                    radius="radius.md", glyph="power", glyph_on="power"),
    "checkbox": _role("checkbox", fill="sunken", line="border", on="accent",
                      radius="radius.sm", border="border.medium",
                      glyph="minus", glyph_on="plus",
                      states=("hover", "focus", "disabled")),
    "switch": _role("switch", fill="sunken", line="border", on="accent",
                    radius="radius.full", border="border.thick",
                    states=("hover", "focus", "disabled")),
    "field": _role("field", fill="sunken", line="border", on="accent",
                   radius="radius.sm", padding="pad.xs", shows="value"),
    "track": _role("track", fill="sunken", line="border",
                   radius="radius.full", padding="pad.none",
                   states=("hover", "focus")),
    "fill": _role("fill", fill="accent", ink="ink", line="accent",
                  radius="radius.full", states=("pressed", "disabled")),
    "thumb": _role("thumb", fill="text", ink="bg", line="shadow",
                   radius="radius.full", padding="pad.none",
                   states=("hover", "pressed", "focus", "disabled")),
    "meter": _role("meter", fill="sunken", line="border",
                   radius="radius.sm", states=("hover", "disabled")),
    "led": _role("led", fill="sunken", ink="faint", line="border", on="good",
                 on_ink="good", radius="radius.full", padding="pad.none",
                 states=("pressed", "disabled")),
    "knob": _role("knob", fill="raised", line="border",
                  radius="radius.full", glyph="chevron-u",
                  states=("hover", "pressed", "focus", "disabled")),
    "grid": _role("grid", fill="sunken", ink="faint", line="border",
                  radius="radius.none", padding="pad.none", states=("hover",)),
    "selection": _role("selection", fill="accent", ink="ink", line="accent",
                       radius="radius.sm", on="accent", states=()),
    "icon": _role("icon", fill="surface", ink="text", line="border",
                  on="accent", radius="radius.md"),
    # text: words on whatever is behind them, with no plate of their own
    "text": _role("text", fill="surface", ink="text", line="border",
                  radius="radius.none", padding="pad.none", states=(), face=False),
    "caption": _role("caption", fill="surface", ink="dim", radius="radius.none",
                    padding="pad.none", font="font.caption", states=(),
                    face=False),
    "hint": _role("hint", fill="surface", ink="faint", radius="radius.none",
                  padding="pad.none", font="font.micro", states=(), face=False),
    "title": _role("title", fill="surface", ink="text", radius="radius.none",
                   padding="pad.none", font="font.title", states=(), face=False),
    "muted": _role("muted", fill="surface", ink="faint", radius="radius.none",
                   padding="pad.none", states=(), face=False),
    # the four intents a script may name, and the parts they colour
    "accent": _role("accent", fill="accent", ink="ink", line="accent",
                    states=("hover", "pressed", "disabled")),
    "ok": _role("ok", fill="good", ink="ink", line="good",
                states=("hover", "pressed", "disabled")),
    "warning": _role("warning", fill="warn", ink="ink", line="warn",
                     states=("hover", "pressed", "disabled")),
    "danger": _role("danger", fill="bad", ink="ink", line="bad",
                    states=("hover", "pressed", "disabled")),
    "error": _role("error", fill="bad", ink="ink", line="bad",
                   states=("hover", "pressed", "disabled")),
    "info": _role("info", fill="info", ink="ink", line="info",
                  states=("hover", "pressed", "disabled")),
    # the two roles that are a bare token, because a painter does ask for them
    "border": _role("border", fill="border", ink="border", line="border",
                    radius="radius.none", padding="pad.none", states=(),
                    face=False),
    "outline": _role("outline", fill="edge", ink="edge", line="edge",
                     radius="radius.none", padding="pad.none", states=(),
                     face=False),
}

#: roles that mean the same thing with a different word, so a script's
#: ``"default"`` is not an error and a widget's ``"bg"`` is not a guess
_ALIASES: Dict[str, str] = {
    "default": "surface",
    "background": "window",
    "bg": "window",
    "label": "text",
    "body": "text",
    "heading": "title",
    "success": "ok",
    "good": "ok",
    "caution": "warning",
    "destructive": "danger",
    "slider": "track",
    "hslider": "track",
    "vslider": "track",
    "number": "field",
    "nbx": "field",
    "radio": "checkbox",
    "dropdown": "field",
    "button-toggle": "toggle",
    "scope": "grid",
    "panel-border": "border",
}


def _on_hover(base: RGBA, theme: "Theme") -> RGBA:
    """The pointer is over it: lift on charcoal, sink on paper.

    A light theme cannot go lighter - its surfaces are already at the top of the
    range - so its hover is the only direction left, and it is deliberately the
    smaller of the two sinks so a hover and a press are not the same colour.
    """
    return _react(base, -0.05 if theme.is_light else 0.06)


def _on_pressed(base: RGBA, theme: "Theme") -> RGBA:
    """It is being pushed: always a sink, and further than the hover got.

    Dark themes have room above and below, so a press moves the opposite way to
    a hover, which is what makes a held button read as pressed rather than as
    merely hovered harder.
    """
    return _react(base, -0.09 if theme.is_light else -0.05)


def _on_focus(base: RGBA, theme: "Theme") -> RGBA:
    """The keyboard is aimed at it: tint towards the theme's own focus edge."""
    return mix(base, theme.colour("edge"), 0.30)


def _on_disabled(base: RGBA, theme: "Theme") -> RGBA:
    """Nothing will come of it: wash towards ``faint``, in either polarity.

    Not a lift or a sink, because a disabled part is meant to recede rather than
    announce itself, and greying out reads the same on charcoal as on paper.
    """
    return mix(base, theme.colour("faint"), 0.38)


#: the state rules, once. ``normal`` is absent on purpose: it is the role's own
#  fill, and every rule below is a change to that, so a state nobody defined is
#  the colour it started with rather than a black rectangle.
_STATE_FILLS = {
    "hover": _on_hover,
    "pressed": _on_pressed,
    "focus": _on_focus,
    "disabled": _on_disabled,
}


# --------------------------------------------------------------------------
# the object a widget talks to
# --------------------------------------------------------------------------


def _join(holds: str, acts: str) -> str:
    """A state as it would have been asked for: ``"on hover"``, ``"focus"``."""
    return " ".join(word for word in (holds, acts) if word) or "normal"


def _split_state(state: Any) -> Tuple[str, str]:
    """``"selected focus"`` into ``("selected", "focus")``; one word, one half.

    Words outside the vocabulary are dropped rather than raised, because a state
    comes out of a widget's flags at draw time and a widget that has a press to
    report should still be drawn in something. ``normal``, ``""`` and ``None``
    are all "no state", and the two halves are kept apart because a value is not
    an interaction: the fill of ``on`` is decided first and then hovered on top.
    """
    holds = acts = ""
    words = str(state or "").lower().replace("/", " ").replace("+", " ").split()
    for word in words:
        if word in _ON_STATES:
            holds = holds or word
        elif word in _INTERACTIONS and word != "normal":
            acts = word
    return holds, acts


class Skin:
    """A theme and a size, asked as roles: ``skin.resolve("button", "hover")``.

    Immutable, and built once per project (and again if a manifest's theme
    changes mid-run, which nothing does). :attr:`digest` is the ``skin_hash`` a
    script painter's cache is keyed by, so a theme switch repaints and a no-op
    re-construction of the same theme does not.

    Every answer is memoised, including the derived ones, which is why a widget
    may ask for ``bg + border + glyph + label`` as four cheap calls instead of
    one awkward tuple.
    """

    def __init__(self, theme: Any = DEFAULT_THEME, scale: str = "md",
                 *, overrides: Optional[Mapping[str, Any]] = None) -> None:
        self.theme = as_theme(theme)
        self.scale_name = as_scale(scale)
        self.scale = SIZES[self.scale_name]
        #: token name -> a value that beats the theme, for a project that wants
        #  one colour different and is not going to fork a theme for it
        self.overrides: Dict[str, Any] = dict(overrides or {})
        self._appearances: Dict[Tuple[str, str], Appearance] = {}
        self.digest = self._make_digest()

    # -- asking for one thing --------------------------------------------
    def colour(self, role: Any, state: str = "normal") -> RGBA:
        """The fill of ``role`` in ``state``. The painter's one-word question."""
        return self.resolve(role, state).fill

    def ink(self, role: Any, state: str = "normal") -> RGBA:
        """What is drawn *on* ``role`` in ``state`` - a label, a glyph."""
        return self.resolve(role, state).ink

    def outline(self, role: Any, state: str = "normal") -> RGBA:
        return self.resolve(role, state).border

    def radius(self, role: Any) -> float:
        return self.resolve(role).radius

    def font(self, role: Any) -> float:
        return self.resolve(role).font

    def padding(self, role: Any) -> float:
        return self.resolve(role).padding

    def glyph(self, role: Any, on: bool = False) -> str:
        """The mark this role draws on itself: ``check`` when on, ``minus`` off.

        Empty means the role has no mark, which is a real answer and not an
        error - a track shows nothing in the middle of itself.
        """
        found = ROLES[as_role(role)]
        return found.glyph_on if on else found.glyph

    # -- asking for everything -------------------------------------------
    def resolve(self, role: Any, state: str = "normal") -> Appearance:
        """The whole look of one part in one state, built once and kept.

        ``state`` is the widget's value and the pointer's action as one string,
        in either order - ``"on hover"`` and ``"hover on"`` are the same
        question - which is what lets a toggle that is on be hovered without
        drawing itself as off. Unknown roles raise with the closest names, and an
        unknown state is the role's normal look rather than a failure: a widget
        that reports a press it has not been told how to show should still draw,
        in a colour nobody has to think about.
        """
        named = as_role(role)
        holds, acts = _split_state(state)
        key = (named, holds, acts)
        found = self._appearances.get(key)
        if found is None:
            found = self._build(ROLES[named], holds, acts)
            self._appearances[key] = found
        return found

    def _build(self, role: Role, holds: str, acts: str) -> Appearance:
        theme = self.theme
        on = bool(holds) and bool(role.on)
        if on:
            base = self._colour(role.on)
            ink = self._colour(role.on_ink or "ink")
            # an on part whose outline was only ever the panel's border takes
            # the accent instead, so an active toggle is one shape, not a
            # button with a highlight and a box round it
            line = base if not role.line or role.line == "border" \
                else self._colour(role.line)
        else:
            base = self._colour(role.fill)
            ink = self._colour(role.ink)
            line = self._colour(role.line)
        rule = _STATE_FILLS.get(acts)
        if rule is not None and role.responds(acts):
            base = rule(base, theme)
            # the outline follows a focus ring and nothing else: a hovered
            # border would be a second highlight on top of the fill's own
            if acts == "focus":
                line = mix(line, theme.colour("edge"), 0.75)
            if acts == "disabled":
                ink = mix(ink, base, 0.55)
        full = self._number("radius.full")
        return Appearance(
            role=role.name,
            # a part with no ``on`` token did not answer the word that asked for
            # it, so it does not claim to be in that state either
            state=_join(holds if on else "", acts),
            fill=base,
            ink=ink,
            border=line,
            radius=min(self._number(role.radius) * self.scale, full),
            width=max(0.0, self._number(role.border) * self.scale),
            padding=max(0.0, self._number(role.padding) * self.scale),
            font=max(1.0, self._number(role.font) * self.scale),
            glyph=role.glyph_on if on else role.glyph,
        )

    def _colour(self, token: str) -> RGBA:
        """A palette token, or a project's override of it."""
        said = str(token)
        if said in self.overrides:
            return as_color(self.overrides[said])
        return self.theme.colour(said)

    def _number(self, token: str) -> float:
        """A metric token, or a project's override of it."""
        said = str(token)
        if said in self.overrides:
            return float(self.overrides[said])
        return self.theme.number(said)

    # -- with a different theme or size ----------------------------------
    def with_theme(self, theme: Any) -> "Skin":
        """Another skin over the same overrides - what ``STYLE``'s theme does."""
        return Skin(theme, self.scale_name, overrides=self.overrides)

    def with_scale(self, scale: str) -> "Skin":
        """Another skin at another size, same theme: ``STYLE(w, "size", "sm")``."""
        return Skin(self.theme, scale, overrides=self.overrides)

    # -- describing itself -----------------------------------------------
    def names(self) -> Tuple[str, ...]:
        """Every role a script or a painter may name."""
        return tuple(ROLES)

    def states(self) -> Tuple[str, ...]:
        """Every state string this skin answers, pairs included."""
        return state_names()

    def tokens(self) -> Dict[str, str]:
        """The palette as it resolves here, for ``crude widgets --json``.

        Strings are :class:`~crude.ui.base.RGBA`'s own ``#rrggbbaa`` form - the
        one thing every colour in the project already prints as, and the one
        :func:`~crude.ui.base.as_color` reads back - rather than a second
        notation invented here.
        """
        return {token: str(self._colour(token)) for token in COLLOUR_TOKENS}

    def metrics(self) -> Dict[str, float]:
        """The same for the numbers, which stay numbers - ``999`` is not a
        radius, it is "as round as this shape allows", and turning it into a
        string loses the one thing that makes it readable.

        Token values with any override applied, but before this skin's size
        scale, which ``as_dict`` reports separately as ``factor``.
        """
        return {token: self._number(token) for token in NUMBER_TOKENS}

    def as_dict(self) -> Dict[str, Any]:
        """The whole skin as one document: a role per state, plus the palette.

        Only the plain states are written out; :func:`state_names` lists the
        pairs, each of which is the composition of two rows that are here.
        """
        return {
            "theme": self.theme.name,
            "scale": self.scale_name,
            "factor": self.scale,
            "digest": self.digest,
            "overrides": {token: str(value)
                          for token, value in self.overrides.items()},
            "tokens": self.tokens(),
            "metrics": self.metrics(),
            "roles": {role.name: {state: self.resolve(role.name, state)
                                  .as_dict() for state in STATES}
                      for role in ROLES.values()},
        }

    def _make_digest(self) -> str:
        """A hash of every value this skin can hand out, which is the safe way
        to key a painter's cache: two skins that resolve identically share a
        digest and two that differ in one shade do not.

        Walked over the seven plain states rather than over their pairs, because
        a pair is the composition of the two halves - ``on hover`` is the hover
        rule applied to the ``on`` fill - so anything that could change a pair
        already changes one of the rows below.
        """
        every: List[str] = [self.theme.name, self.scale_name]
        for role in ROLES.values():
            for state in STATES:
                every.append(str(self._build(role, *_split_state(state))))
        every.extend(f"{token}={value}" for token, value in
                     sorted(self.overrides.items(), key=lambda pair: pair[0]))
        return hashlib.sha1("|".join(every).encode("utf-8")).hexdigest()[:12]

    def __str__(self) -> str:
        return (f"Skin({self.theme.name} {self.scale_name}"
                f"{f' +{len(self.overrides)}' if self.overrides else ''}, "
                f"{len(ROLES)} roles, {self.digest})")

    def __eq__(self, other: Any) -> bool:
        return (isinstance(other, Skin) and other.digest == self.digest)

    def __hash__(self) -> int:
        return hash(self.digest)


# --------------------------------------------------------------------------
# what a script's word means
# --------------------------------------------------------------------------


def theme_names() -> Tuple[str, ...]:
    return tuple(THEMES)


def role_names() -> Tuple[str, ...]:
    """Every role, and every alias, sorted - what a report or a completion lists."""
    return tuple(sorted([*ROLES, *_ALIASES]))


def state_names() -> Tuple[str, ...]:
    """Every state a widget may ask for: the seven words, and their pairs.

    The pairs are the part of the vocabulary a flat list cannot name - a toggle
    that is on *and* hovered - and they are generated rather than written down so
    that the two halves cannot drift apart from each other.
    """
    out = list(STATES)
    for value in _ON_STATES:
        for action in _INTERACTIONS:
            out.append(value if action == "normal" else f"{value} {action}")
    return tuple(dict.fromkeys(out))


def colour_token_names() -> Tuple[str, ...]:
    return COLLOUR_TOKENS


def number_token_names() -> Tuple[str, ...]:
    return NUMBER_TOKENS


def as_theme(value: Any) -> Theme:
    """A theme by name, or the one already built; ``None`` is the default.

    A name that is not in the table is an error that lists the ones that are,
    because a manifest typo otherwise silently repaints the whole project.
    """
    if isinstance(value, Theme):
        return value
    if isinstance(value, Skin):
        return value.theme
    if value is None:
        return THEMES[DEFAULT_THEME]
    said = str(value).strip().lower()
    if not said:
        return THEMES[DEFAULT_THEME]
    found = THEMES.get(said)
    if found is None:
        raise UIError(f"there is no theme '{value}' (known: "
                      f"{', '.join(theme_names())})")
    return found


def as_scale(value: Any) -> str:
    """The size name a script used, checked; anything blank means ``md``."""
    if value is None:
        return "md"
    if isinstance(value, (int, float)):
        # a number is the factor itself, which has no name; round it to a rung
        wanted = float(value)
        closest = min(SIZES.items(), key=lambda pair: abs(pair[1] - wanted))
        return closest[0]
    said = str(value).strip().lower()
    if said in SIZES:
        return said
    raise UIError(f"'{value}' is not a size (known: "
                  f"{', '.join(sorted(SIZES))})")


def as_role(value: Any) -> str:
    """The role name a script meant, following any alias it used.

    The error lists near misses: a painter's ``"serface"`` is a name the author
    can see the shape of, and a role that quietly became ``surface`` would be a
    widget drawn in a colour nobody asked for.
    """
    if isinstance(value, Role):
        return value.name
    if isinstance(value, Appearance):
        return value.role
    if value is None:
        return "surface"
    said = str(value).strip().lower()
    if not said:
        return "surface"
    if said in ROLES:
        return said
    aliased = _ALIASES.get(said)
    if aliased is not None:
        return aliased
    spaced = said.replace(" ", "-").replace("_", "-")
    if spaced in ROLES:
        return spaced
    if spaced in _ALIASES:
        return _ALIASES[spaced]
    near = get_close_matches(said, role_names(), n=4)
    raise UIError(f"'{value}' is not a style role"
                  + (f" (did you mean: {', '.join(near)}?)" if near else
                     f" (known: {', '.join(sorted(ROLES))})"))


def describe(role: Any) -> Dict[str, Any]:
    """One role's table row as a document, for ``crude widgets --json``."""
    found = ROLES[as_role(role)]
    return {
        "role": found.name, "fill": found.fill, "ink": found.ink,
        "line": found.line, "on": found.on or None,
        "radius": found.radius, "border": found.border,
        "padding": found.padding, "font": found.font,
        "glyph": found.glyph or None, "glyph_on": found.glyph_on or None,
        # whether a widget draws a plate behind the words at all, which is the
        # one thing a painter cannot infer from a fill token that exists
        "face": found.face,
        # and which words it draws: its own name, or the number it holds
        "shows": found.shows,
        "states": list(found.states),
        "aliases": sorted(name for name, target in _ALIASES.items()
                          if target == found.name),
    }
