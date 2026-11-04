"""``AUDIO_*`` bindings: the script-facing audio surface.

Everything here is a thin adapter over :class:`crude.audio.AudioRuntime`, and the
whole point of stage 4 is that a script cannot tell whether it is talking to a
sound card or to the silent backend: the same calls, the same meters, the same
answers. So nothing here imports ``sounddevice`` or ``pyaudio``, and nothing here
starts a thread - the runtime does, and the audio callback does.

The handle ``AUDIO_INFO`` returns is readable with ``GET`` (``rms``, ``peak``,
``db``, ``blocks``, ``device`` ...: see
:data:`crude.audio.base.INFO_PROPS`). It is deliberately *not* writable: changing
a channel count or a block size under a running stream would leave libpd and the
meters disagreeing about the shape of a block.

Both meters take a ``source`` of ``"output"`` or ``"input"``, defaulting to the
output; recording itself lives in :mod:`crude.bindings.sound`, because a take on
disk is a file and a file belongs to the language, not to the stream.
"""

from __future__ import annotations

from ..audio.base import AudioError
from ..lang.values import HostHandle, List, Number, String
from .registry import binding

AREA = "audio"

#: what ``GET``/``SET`` see on an audio handle
AUDIO_KIND = "audio"


# -- helpers ------------------------------------------------------------


def _runtime(ctx):
    return ctx.subsystem("audio", "audio")


def _fail(ctx, exc) -> None:
    raise ctx.fail(f"{ctx.name}: {exc}")


def _handle(runtime) -> HostHandle:
    return HostHandle(runtime, kind=AUDIO_KIND, label=runtime.describe())


def _optional_int(ctx, arg_name: str):
    if not ctx.has(arg_name):
        return None
    return ctx.as_int(arg_name)


# -- the stream ---------------------------------------------------------


@binding("AUDIO_INFO", [], area=AREA)
def audio_info(ctx):
    """The audio stream as a handle: ``GET(AUDIO_INFO(), \"rms\")`` and friends."""
    return _handle(_runtime(ctx))


@binding("AUDIO_DEVICES", ["direction"], area=AREA, optional=1)
def audio_devices(ctx):
    """Every input/output the usable backends report."""
    direction = ctx.as_string("direction") if ctx.has("direction") else None
    try:
        devices = _runtime(ctx).devices(direction)
    except AudioError as exc:
        _fail(ctx, exc)
    return List([String(device.describe()) for device in devices])


@binding("AUDIO_USE_DEVICES", ["input", "output"], area=AREA, optional=2)
def audio_use_devices(ctx):
    """Switch the running stream onto new devices, live.

    Each argument is a device *index* as ``AUDIO_DEVICES()`` reports it (its
    leading ``[n]``); an omitted one means "keep the default for that direction".
    The stream is torn down and reopened on the pick, and restarted if it was
    already playing, so a configuration menu changes where sound goes without
    stopping the show.
    """
    runtime = _runtime(ctx)
    input_device = _optional_int(ctx, "input")
    output_device = _optional_int(ctx, "output")
    try:
        runtime.switch_devices(input_device, output_device)
    except AudioError as exc:
        _fail(ctx, exc)
    return _handle(runtime)


@binding("AUDIO_DEVICE", [], area=AREA)
def audio_device(ctx):
    """The devices this stream is on now, as ``[input, output]``: each a device
    name, or ``default`` when that side uses the machine default."""
    runtime = _runtime(ctx)
    current = runtime.current_devices()
    return List([String(str(side) if side is not None else "default")
                 for side in current])


@binding("AUDIO_START", ["backend"], area=AREA, optional=1)
def audio_start(ctx):
    """Open and run the stream; without a device this is the silent backend."""
    runtime = _runtime(ctx)
    if ctx.has("backend"):
        # Asked for by name once: a script can say "null" in CI and "sounddevice"
        # at home, and still get silence rather than a crash if it cannot be had.
        runtime.preferred = ctx.as_string("backend")
    try:
        runtime.start()
    except AudioError as exc:
        _fail(ctx, exc)
    return _handle(runtime)


@binding("AUDIO_STOP", [], area=AREA)
def audio_stop(ctx):
    """Stop the stream; the patch stays loaded and nothing is lost."""
    runtime = _runtime(ctx)
    was_running = bool(runtime.running)
    try:
        runtime.stop()
    except AudioError as exc:
        _fail(ctx, exc)
    return Number(1 if was_running else 0)


# -- the meters ---------------------------------------------------------


@binding("AUDIO_LEVEL", ["channel", "mode", "source"], area=AREA, optional=3)
def audio_level(ctx):
    """One meter reading: ``rms`` (default), ``peak`` or ``db``.

    ``source`` defaults to ``"output"``, the side the loudspeaker is on; pass
    ``"input"`` to meter the microphone the same way. The runtime has always
    answered for both, and a recorder that cannot see what is coming in is flying
    blind.
    """
    channel = _optional_int(ctx, "channel")
    mode = ctx.as_string("mode") if ctx.has("mode") else "rms"
    source = ctx.as_string("source") if ctx.has("source") else "output"
    try:
        return Number(float(_runtime(ctx).level(channel, mode, source)))
    except AudioError as exc:
        _fail(ctx, exc)


@binding("AUDIO_WAVEFORM", ["frames", "channel", "source"], area=AREA, optional=3)
def audio_waveform(ctx):
    """The most recent samples, oldest first - what a scope widget draws.

    ``source`` is ``"output"`` unless a script says ``"input"``, and the trace it
    gets is the one the device actually carried, not a copy of what was sent to it.
    """
    frames = _optional_int(ctx, "frames")
    channel = _optional_int(ctx, "channel")
    source = ctx.as_string("source") if ctx.has("source") else "output"
    try:
        samples = _runtime(ctx).waveform(frames, channel, source)
    except AudioError as exc:
        _fail(ctx, exc)
    return List([Number(float(sample)) for sample in samples])


#: Names this module registers, used by the binding-help generator and tests.
BINDINGS = (
    "AUDIO_INFO", "AUDIO_DEVICES", "AUDIO_USE_DEVICES", "AUDIO_DEVICE",
    "AUDIO_START", "AUDIO_STOP",
    "AUDIO_LEVEL", "AUDIO_WAVEFORM",
)
