"""Sound-file and recording bindings: ``WAV_*`` and ``AUDIO_RECORD*``.

The audio bindings stop at the stream: meters, devices, start and stop. Anything
that puts sound on a hard drive, or takes it off one, is a *file* question, and
files belong to the project the script is running in - so they live here.

Three rules hold the surface together:

* **a path is the project's path**. A relative ``samples/take.wav`` means the same
  thing it means to ``PRESET_SAVE``: inside the project directory, whatever the
  working directory happens to be when the run starts. The answer a script gets
  back is spelled the way the project spells its own files - forward slashes,
  relative where that is possible - so a log line and a ``RES`` lookup agree.
* **a file is a handle, and its samples are lazy**. ``WAV_READ`` checks the header
  (so picking a file that is not sound fails at the call, in the voice of the
  person who picked it) but decodes nothing until a ``GET`` asks for samples. A
  three-minute stereo file is two million floats, and a script that only wanted
  the length should not have to carry them.
* **recording is armed, not automatic**. ``AUDIO_RECORD_START`` says how many
  seconds may be kept and which input channel to keep them from; the stream fills
  the buffer while it runs and ``AUDIO_RECORD_STOP`` takes it off. Between those
  two calls the handle ``AUDIO_RECORD`` answers ``frames`` as it goes, which is
  what a level-meter-style "the take is still coming in" display needs.

Nothing in this module touches a device: the runtime owns the stream, the
:class:`~crude.audio.base.Capture` inside the backend owns the frames, and
:mod:`crude.audio.wav` owns the encoding.
"""

from __future__ import annotations

import os
from typing import List as PyList

from ..audio.base import AudioError
from ..audio.wav import PCM16, read_wav, read_wav_header, write_wav
from ..lang.values import (
    BindingError,
    HostHandle,
    Number,
    String,
    value_to_python,
)
from .registry import binding

AREA = "sound"

#: what ``GET`` sees on the handle ``WAV_READ`` returns
WAV_KIND = "wav"

#: what ``GET`` sees on the handle ``AUDIO_RECORD`` returns
RECORDER_KIND = "recorder"

#: the properties of a sound file, in the order a help page lists them
WAV_PROPS = (
    "path", "name", "rate", "channels", "bits", "frames", "seconds",
    "samples", "mixed",
)

#: the properties of a recording in progress, or just finished
RECORD_PROPS = (
    "armed", "channel", "frames", "capacity", "overflow", "rate", "seconds",
    "samples",
)


# -- paths --------------------------------------------------------------


def _directory(ctx):
    """The project directory this script is running in, or ``None`` outside one."""
    host = ctx.host
    project = getattr(host, "project", None) if host is not None else None
    return getattr(project, "directory", None) if project is not None else None


def _resolve(ctx, name: str) -> str:
    """One path argument as the absolute file it means.

    Forward and back slashes are both accepted because a script is written by a
    person and read by two operating systems, and a relative path is joined onto
    the project - the same rule ``PRESET_SAVE`` keeps, so a sound and a preset
    saved by the same run land in the same place.
    """
    text = str(name).strip()
    if not text:
        raise ctx.fail(f"{ctx.name}: an empty path is not a file")
    text = os.path.expanduser(text)
    text = text.replace("\\", os.sep).replace("/", os.sep)
    if not os.path.isabs(text):
        base = _directory(ctx) or os.getcwd()
        text = os.path.join(base, text)
    return os.path.normpath(text)


def _shown(ctx, path: str) -> str:
    """A file this run touched, spelled the way the project spells its files.

    Forward slashes, and relative to the project whenever that is honest - the
    same rule :mod:`crude.bindings.project` keeps for ``PRESET_SAVE``, so a path a
    script prints and a path ``RES`` looks up are the same string. A path outside
    the project stays absolute rather than pretending to be ``../`` soup.
    """
    spelled = path.replace(os.sep, "/")
    base = _directory(ctx)
    if base:
        try:
            relative = os.path.relpath(path, base).replace(os.sep, "/")
        except ValueError:  # pragma: no cover - a path on another drive
            return spelled
        # a file outside the project keeps its absolute spelling: a "../" trail
        # is not a name anybody recognises the next morning
        if not relative.startswith(".."):
            return relative
    return spelled


# -- views --------------------------------------------------------------


class WavView:
    """One sound file, read the moment anybody asks what is in it.

    The header is already known - :func:`WAV_READ` refused the file otherwise -
    so the shape of it costs nothing. The samples are decoded once, on the first
    ``GET`` that wants them, and kept: a script that plots a file and then writes
    it back should not decode it twice.
    """

    def __init__(self, path: str, header, shown: str) -> None:
        self.path = path
        self.shown = shown
        self.header = header
        self._data = None

    def data(self):
        if self._data is None:
            try:
                self._data = read_wav(self.path)
            except AudioError as exc:
                # the file was there and readable when WAV_READ checked its header
                # and is not now: a script that picked it in a dialog is the one
                # who has to be told, so this is a script error and not a Python
                # traceback escaping a property read
                raise BindingError(f"wav '{self.shown}' could not be read: {exc}") \
                    from exc
        return self._data

    def get_prop(self, prop: str):
        if prop not in WAV_PROPS:
            raise KeyError(prop)
        if prop == "path":
            return self.shown
        if prop == "name":
            return os.path.basename(self.path)
        if prop == "rate":
            return self.header.rate
        if prop == "channels":
            return self.header.channels
        if prop == "bits":
            return self.header.bits
        if prop == "frames":
            return self.header.frames
        if prop == "seconds":
            return self.header.seconds
        if prop == "samples":
            return self.data().samples
        return self.data().mix()

    def describe(self) -> str:
        return f"{self.shown}: {self.header.describe()}"

    def __str__(self) -> str:
        return self.describe()


class RecorderView:
    """The take in progress, as ``GET`` sees it.

    Read-only, and not only for tidiness: arming and emptying a capture from
    inside a property write would let a script change what the audio callback is
    filling while it is filling it. ``AUDIO_RECORD_START`` and
    ``AUDIO_RECORD_STOP`` are the only ways in, and both are calls a person makes
    on purpose.
    """

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def get_prop(self, prop: str):
        if prop == "samples":
            return self.runtime.record_samples()
        if prop not in RECORD_PROPS:
            raise KeyError(prop)
        return self.runtime.record_info()[prop]

    def describe(self) -> str:
        return f"recorder: {self.runtime.record_info()}"

    def __str__(self) -> str:
        return self.describe()


def _wav_handle(view: WavView) -> HostHandle:
    return HostHandle(view, kind=WAV_KIND, label=view.describe())


def _recorder_handle(runtime) -> HostHandle:
    return HostHandle(RecorderView(runtime), kind=RECORDER_KIND,
                      label="recorder")


def _runtime(ctx):
    return ctx.subsystem("audio", "audio")


def _fail(ctx, exc) -> None:
    raise ctx.fail(f"{ctx.name}: {exc}")


def _sample_list(ctx, arg_name: str) -> PyList:
    """A ``samples`` argument as plain floats, whichever shape it arrived in.

    A list is the usual case. A single number is accepted because a script that
    generates a test tone in a loop appends to one variable, and ``AT``/``POP``
    hand back an element rather than a list - refusing that would force a
    ``LEN``/``AT`` walk through the data just to satisfy the argument type.
    """
    payload = value_to_python(ctx.get(arg_name))
    if isinstance(payload, (list, tuple)):
        values = list(payload)
    elif payload is None:
        values = []
    elif isinstance(payload, (int, float)):
        values = [payload]
    else:
        raise ctx.fail(
            f"{ctx.name}: argument '{arg_name}' must be a list of samples, "
            f"got {type(payload).__name__}"
        )
    try:
        return [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise ctx.fail(f"{ctx.name}: argument '{arg_name}' holds a sample "
                       f"that is not a number: {exc}")


# -- the files ----------------------------------------------------------


@binding("WAV_READ", ["path"], area=AREA)
def wav_read(ctx):
    """A sound file as a handle; its samples are decoded only when asked for."""
    name = ctx.as_string("path")
    path = _resolve(ctx, name)
    try:
        header = read_wav_header(path)
    except (AudioError, OSError) as exc:
        _fail(ctx, exc)
    return _wav_handle(WavView(path, header, _shown(ctx, path)))


@binding("WAV_WRITE", ["path", "samples", "rate", "channels", "bits"],
         area=AREA, optional=3)
def wav_write(ctx):
    """Write samples out as a PCM wav; answers the path the file is really at.

    ``samples`` are ``-1..+1`` floats, interleaved when the file has more than one
    channel, and the width they are quantised to is the ``bits`` argument - 16 by
    default, 8 when a smaller file matters more than the last few decibels.
    """
    path = _resolve(ctx, ctx.as_string("path"))
    values = _sample_list(ctx, "samples")
    rate = ctx.as_int("rate")
    channels = ctx.as_int("channels") if ctx.has("channels") else 1
    if channels < 1:
        # a file cannot have no channels, and quietly writing a mono one under a
        # request for zero would be the binding guessing what the script meant
        raise ctx.fail(f"{ctx.name}: argument 'channels' has to be 1 or more, "
                       f"got {channels}")
    bits = ctx.as_int("bits") if ctx.has("bits") else PCM16
    try:
        frames = write_wav(path, values, rate, channels, bits)
    except (AudioError, OSError) as exc:
        _fail(ctx, exc)
    return String(_shown(ctx, path))


@binding("WAV_LENGTH", ["path"], area=AREA)
def wav_length(ctx):
    """How long a sound file is, in seconds, without decoding it.

    Separate from ``GET(WAV_READ(...), \"seconds\")`` for one reason: a script
    that is deciding *whether* to load a file should not have to load it to ask,
    and this call cannot be talked into carrying two million floats.
    """
    path = _resolve(ctx, ctx.as_string("path"))
    try:
        header = read_wav_header(path)
    except (AudioError, OSError) as exc:
        _fail(ctx, exc)
    return Number(float(header.seconds))


# -- the recording ------------------------------------------------------


@binding("AUDIO_RECORD", [], area=AREA)
def audio_record(ctx):
    """The take in progress (or the last one) as a handle."""
    return _recorder_handle(_runtime(ctx))


@binding("AUDIO_RECORD_START", ["seconds", "channel"], area=AREA, optional=1)
def audio_record_start(ctx):
    """Arm the capture for ``seconds`` of input on one channel.

    The channel defaults to 0 - mono, which is what a sampler loads - and the
    buffer is sized here and never grows, so the frames past ``seconds`` are
    counted in ``overflow`` instead of stalling the stream.
    """
    runtime = _runtime(ctx)
    seconds = ctx.as_number("seconds")
    channel = ctx.as_int("channel") if ctx.has("channel") else 0
    try:
        runtime.record_start(seconds, channel)
    except AudioError as exc:
        _fail(ctx, exc)
    return _recorder_handle(runtime)


@binding("AUDIO_RECORD_STOP", [], area=AREA)
def audio_record_stop(ctx):
    """Stop taking frames in; answers how many were, and they stay readable."""
    runtime = _runtime(ctx)
    try:
        frames = runtime.record_stop()
    except AudioError as exc:
        _fail(ctx, exc)
    return Number(int(frames))


#: Names this module registers, used by the binding-help generator and tests.
BINDINGS = (
    "WAV_READ", "WAV_WRITE", "WAV_LENGTH",
    "AUDIO_RECORD", "AUDIO_RECORD_START", "AUDIO_RECORD_STOP",
)
