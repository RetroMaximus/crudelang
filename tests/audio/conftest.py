"""Fixtures for the audio suite: stubs instead of hardware.

The default run must not open a device, so every test here drives the adapters
with a fake module in ``sys.modules``: :func:`stub_sounddevice` and
:func:`stub_pyaudio` answer exactly the calls the adapters make, and the tests
call the callback the stub recorded - which is the only way a PortAudio thread
can be reproduced deterministically.

``sounddevice`` happens to be installed on the dev machine and ``pyaudio`` is not
installable on this Python at all; both facts are irrelevant here, because the
adapters look the library up in ``sys.modules`` on every call.
"""

from __future__ import annotations

import array
import sys
import threading
import types

import pytest

from crude.audio import AudioConfig, NullBackend
from crude.audio.runtime import AudioRuntime
from crude.lang import Program

#: how long a parked ``read()`` waits before calling a missed wake-up a bug
READ_PARK_SECONDS = 5.0

#: what the stubs report: one input, one output, one of each, one broken
SD_DEVICES = [
    {"name": "Microphone (Loopback)", "index": 0, "max_input_channels": 2,
     "max_output_channels": 0, "default_samplerate": 48000.0, "hostapi": "Windows DirectSound"},
    {"name": "Speakers (Loopback)", "index": 1, "max_input_channels": 0,
     "max_output_channels": 2, "default_samplerate": 48000.0, "hostapi": "Windows DirectSound"},
    {"name": "Studio Capture", "index": 2, "max_input_channels": 8,
     "max_output_channels": 8, "default_samplerate": 44100.0, "hostapi": "MME"},
]

PA_DEVICES = [
    {"name": "Microphone (Loopback)", "index": 0, "maxInputChannels": 2,
     "maxOutputChannels": 0, "defaultSampleRate": 48000.0, "hostApi": 0},
    {"name": "Speakers (Loopback)", "index": 1, "maxInputChannels": 0,
     "maxOutputChannels": 2, "defaultSampleRate": 48000.0, "hostApi": 0},
    {"name": "Studio Capture", "index": 2, "maxInputChannels": 2,
     "maxOutputChannels": 2, "defaultSampleRate": 44100.0, "hostApi": 2},
]


class FakeBuffer:
    """The ``(frames, channels)`` numpy shape, without numpy.

    Only two things are ever asked of it: iteration over ``.flat``, and indexing
    to write - which is exactly what :func:`crude.audio.base.copy_samples` does.
    """

    def __init__(self, values=None, size=0):
        values = list(values or [])
        self.flat = values + [0.0] * max(size - len(values), 0)

    def __len__(self):
        return len(self.flat)

    def __getitem__(self, index):
        return self.flat[index]

    def __setitem__(self, index, value):
        self.flat[index] = float(value)


class StubStream:
    """``sounddevice``'s six stream shapes: remembers how it was built.

    Three of them are callback-driven and three are the ``Raw`` ones a polled
    backend reads and writes. The stub keeps the two apart the way the library
    does: ``callback`` is only ever recorded for a callback shape, ``read()``
    hands out bytes for a ``Raw`` one, and ``fire()`` drives the callback of a
    test that wants to stand in for PortAudio's own thread.
    """

    def __init__(self, kind="duplex", raw=False, **kwargs):
        self.kind = kind
        #: True for a ``RawStream``/``RawOutputStream``/``RawInputStream``
        self.raw = raw
        self.kwargs = kwargs
        self.callback = kwargs.get("callback")
        self.finished_callback = kwargs.get("finished_callback")
        self.started = False
        self.stopped = False
        self.closed = False
        self.raise_on_start = None
        #: every block a test saw this stream handed to ``write()``
        self.written: list = []
        #: bytes still to be handed out by ``read()``, oldest first
        self.inblocks: list = []
        #: what the next ``read()``/``write()`` reports: an overflow, an underflow
        self.overflow = False
        self.underflow = False
        #: a ``Raw`` stream is paced by its hardware, so a read waits for the
        #  block the device has not delivered yet - the way a real one does, and
        #  the only thing that keeps a polled backend from spinning in a test
        self.blocking = raw
        self._released = threading.Event()
        #: frames per channel that ``read()`` turns into bytes
        channels = kwargs.get("channels") or 0
        self._read_channels = max(channels[0] if isinstance(channels, tuple)
                                  else channels, 1)

    def push(self, data) -> None:
        """Deliver a block, the way a device would: queue it and wake the reader."""
        self.inblocks.append(data)
        self._released.set()

    def write(self, data):
        """``OutputStream.write``: the ring-buffer copy a side device gets.

        A ``Raw`` stream answers with ``underflowed`` - the one bit of PortAudio
        that only a polled write gets to hear - so the stub hands back whatever
        the test set, and ``None`` otherwise, which is what the plain class does.
        """
        self.written.append(data)
        return self.underflow if self.raw else None

    def read(self, frames):
        """``RawStream.read``: ``(bytes, overflowed)``, waiting for the device.

        A blocking read parks until a block is delivered or the stream is
        stopped, and a stream stopped under a parked reader comes back as an
        error - the one shape of shutdown a polling thread has to be able to tell
        from a fault, and the reason ``_pump`` looks at the stop flag before it
        complains about what it caught.
        """
        while True:
            if self.stopped or self.closed:
                raise RuntimeError("stream stopped")
            if self.inblocks or not self.blocking:
                break
            if not self._released.wait(timeout=READ_PARK_SECONDS):
                raise AssertionError("a parked read was never woken by stop()")
            self._released.clear()
        if self.inblocks:
            data = self.inblocks.pop(0)
        else:
            data = array.array("f", [0.0] * (frames * self._read_channels))
        return bytes(data), self.overflow

    def start(self):
        if getattr(self, "raise_on_start", None):
            raise RuntimeError(self.raise_on_start)
        self.started = True

    def stop(self):
        self.stopped = True
        self._released.set()

    def close(self):
        self.closed = True
        self._released.set()

    # -- driving the callback from a test -------------------------------
    def fire(self, indata=None, frames=64, status=0, out_channels=2):
        """Call back exactly as this shape of PortAudio stream would."""
        if self.kind == "output":
            outdata = FakeBuffer(size=frames * out_channels)
            self.callback(outdata, frames, None, status)
            return outdata
        if self.kind == "input":
            self.callback(indata, frames, None, status)
            return None
        outdata = FakeBuffer(size=frames * out_channels)
        self.callback(indata, outdata, frames, None, status)
        return outdata


def check_latency(latency):
    """Refuse what PortAudio cannot put in a C ``double``.

    The real library takes a number of seconds, the words ``"low"``/``"high"`` it
    looks up itself, or nothing at all - a preference of any other shape reaches
    ctypes and comes back as ``TypeError: must be real number, not str``, which is
    exactly the crash this stub exists to catch. A fake that accepts whatever it
    is handed turns a device bug into a passing test.
    """
    if latency is None or latency in ("low", "high"):
        return None
    if isinstance(latency, bool) or not isinstance(latency, (int, float)):
        raise TypeError(f"latency must be a real number, not {type(latency).__name__}")
    return latency


class StubSoundDevice(types.ModuleType):
    """Just enough of ``sounddevice`` for the adapter, plus what it recorded."""

    def __init__(self, devices=None, kind=None, open_error=None, fail_start=None):
        super().__init__("sounddevice")
        self.devices = list(SD_DEVICES if devices is None else devices)
        self.kind = dict(SPEAKERS_BY_KIND if kind is None else kind)
        self.open_error = open_error
        self.fail_start = fail_start
        self.streams: list = []
        self.query_calls: list = []

    def query_devices(self, kind=None):
        self.query_calls.append(kind)
        if kind is None or kind == "all":
            return list(self.devices)
        wanted = self.kind.get(kind)
        if wanted is None:
            raise ValueError(f"No device matches kind '{kind}'")
        for entry in self.devices:
            if entry["index"] == wanted:
                return dict(entry)
        raise ValueError(f"No device matches kind '{kind}'")

    def _stream(self, kind, kwargs, raw=False):  # noqa: N802
        if self.open_error is not None:
            raise RuntimeError(self.open_error)
        check_latency(kwargs.get("latency"))
        if raw and kwargs.get("callback") is not None:
            raise TypeError("a Raw stream is driven by read()/write(), "
                            "so it is not given a callback")
        stream = StubStream(kind, raw=raw, **kwargs)
        stream.raise_on_start = self.fail_start
        self.streams.append(stream)
        return stream

    def Stream(self, **kwargs):
        return self._stream("duplex", kwargs)

    def OutputStream(self, **kwargs):
        return self._stream("output", kwargs)

    def InputStream(self, **kwargs):
        return self._stream("input", kwargs)

    def RawStream(self, **kwargs):
        return self._stream("duplex", kwargs, raw=True)

    def RawOutputStream(self, **kwargs):
        return self._stream("output", kwargs, raw=True)

    def RawInputStream(self, **kwargs):
        return self._stream("input", kwargs, raw=True)


SPEAKERS_BY_KIND = {"output": 1, "input": 0, "duplex": 2}


def sd_record(name: str, index: int, inputs: int = 0, outputs: int = 0,
              rate: float = 48000.0, hostapi: str = "MME") -> dict:
    """One device the way ``sounddevice.query_devices()`` spells it."""
    return {
        "name": name,
        "index": index,
        "max_input_channels": inputs,
        "max_output_channels": outputs,
        "default_samplerate": rate,
        "hostapi": hostapi,
    }


def pa_record(name: str, index: int, inputs: int = 0, outputs: int = 0,
              rate: float = 48000.0, hostApi: int = 0) -> dict:
    """The same device the way ``pyaudio`` spells it."""
    return {
        "name": name,
        "index": index,
        "maxInputChannels": inputs,
        "maxOutputChannels": outputs,
        "defaultSampleRate": rate,
        "hostApi": hostApi,
    }


class StubPyAudioStream:
    """``pyaudio.Stream``: same idea, bytes in and bytes out."""

    def __init__(self, **kwargs):
        self.callback = kwargs.get("stream_callback")
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.closed = False
        self.raise_on_start = None

    def start_stream(self):
        if getattr(self, "raise_on_start", None):
            raise IOError(self.raise_on_start)
        self.started = True

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def fire(self, in_data=b"", frame_count=64, status=0):
        return self.callback(in_data, frame_count, None, status)


class StubPyAudio(types.ModuleType):
    """Just enough of ``pyaudio``: ``paFloat32``, ``paContinue`` and one instance."""

    paFloat32 = 1
    paContinue = 0
    paComplete = 1

    def __init__(self, devices=None, open_error=None):
        super().__init__("pyaudio")
        self.devices = list(PA_DEVICES if devices is None else devices)
        self.open_error = open_error
        self.instances: list = []
        self.streams: list = []

    def PyAudio(self, *args, **kwargs):  # noqa: N802
        instance = _StubPyAudioInstance(self, *args, **kwargs)
        self.instances.append(instance)
        return instance


class _StubPyAudioInstance:
    def __init__(self, module, *args, **kwargs):
        self.module = module
        self.terminated = False

    def get_device_count(self):
        return len(self.module.devices)

    def get_device_info_by_index(self, index):
        return dict(self.module.devices[index])

    def open(self, **kwargs):
        if self.module.open_error is not None:
            raise IOError(self.module.open_error)
        stream = StubPyAudioStream(**kwargs)
        stream.raise_on_start = getattr(self.module, "fail_start", None)
        self.module.streams.append(stream)
        return stream

    def terminate(self):
        self.terminated = True


# -- installing the stubs ------------------------------------------------


@pytest.fixture
def stub_sounddevice(monkeypatch):
    """``sys.modules["sounddevice"] = a stub``; the stub itself is returned."""

    def _install(devices=None, kind=None, open_error=None, fail_start=None):
        stub = StubSoundDevice(devices, kind, open_error, fail_start)
        monkeypatch.setitem(sys.modules, "sounddevice", stub)
        return stub

    return _install


@pytest.fixture
def stub_pyaudio(monkeypatch):
    def _install(devices=None, open_error=None, fail_start=None):
        stub = StubPyAudio(devices, open_error)
        stub.fail_start = fail_start
        monkeypatch.setitem(sys.modules, "pyaudio", stub)
        return stub

    return _install


@pytest.fixture
def fake_buffer():
    """The ``(frames, channels)`` block shape a device callback arrives in."""
    return FakeBuffer


@pytest.fixture
def device_record():
    """``device_record("Speakers", 1, outputs=2)`` -> a sounddevice dict."""
    return sd_record


@pytest.fixture(autouse=True)
def no_real_audio_libraries(monkeypatch):
    """This suite never touches the machine: both libraries start out missing.

    A test that wants an adapter's library installs a stub with
    ``stub_sounddevice()`` or ``stub_pyaudio()``, which overrides this - so
    forgetting the stub fails loudly instead of opening a real stream.
    """
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    monkeypatch.setitem(sys.modules, "pyaudio", None)


@pytest.fixture
def no_audio_libraries(no_real_audio_libraries):
    """Neither device library usable: the chain has to land on ``null``."""
    return None


# -- the pieces a test wires up ----------------------------------------


@pytest.fixture
def config():
    return AudioConfig(sample_rate=48000, block_size=64, in_channels=2, out_channels=2)


@pytest.fixture
def pd_session():
    """A real ``PdSession`` over ``FakeLibpd``: the pause wiring needs one.

    ``AudioRuntime`` duck-types ``set_pauser``/``process``, so the tests exercise
    the same two methods the Pd stage guarantees - without importing that stage's
    fixtures twice.
    """
    from crude.pd import FakeLibpd, PdSession

    lib = FakeLibpd()
    session = PdSession(lib, sample_rate=48000)
    session.start()
    yield session
    session.shutdown()


@pytest.fixture
def null_backend(config):
    backend = NullBackend(config)
    backend.open()
    yield backend
    backend.close()


@pytest.fixture
def recording():
    from crude.audio import RecordingRender

    return RecordingRender(value=0.25)


@pytest.fixture
def audio_runtime(config, recording):
    """A runtime over the silent backend: pulled, deterministic, no device."""
    backend = NullBackend(config, render=recording)
    runtime = AudioRuntime(config=config, backend=backend)
    yield runtime
    runtime.close()


@pytest.fixture
def audio_program(audio_runtime):
    return Program("<audio-test>", host=types.SimpleNamespace(audio=audio_runtime))


@pytest.fixture
def audio_program_for():
    """``audio_program_for(runtime)`` - wire any runtime up as ``host.audio``."""

    def _make(runtime, name="<audio-test>"):
        return Program(name, host=types.SimpleNamespace(audio=runtime))

    return _make


@pytest.fixture
def run_audio(audio_program):
    def _run(source):
        return audio_program.run(source)

    return _run


@pytest.fixture
def eval_audio(run_audio):
    def _eval(source):
        value, error = run_audio(source)
        assert error is None, error.as_string()
        return value

    return _eval


@pytest.fixture
def audio_failure(run_audio):
    def _failure(source):
        value, error = run_audio(source)
        assert error is not None, f"expected an error, got {value!r}"
        return error

    return _failure
