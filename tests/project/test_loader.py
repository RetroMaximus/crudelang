"""The pipeline that turns a project directory into a running host.

Every subsystem the loader would otherwise reach for is handed in: ``FakeLibpd``
for libpd, ``NullBackend`` for a device, a media host that only sizes files, a
serial host that records what it was asked to open. That is what lets the whole
fifteen-phase run - and the unwinding of a failed one - be tested on a machine
with no DLL, no sound and no display.

Two of these tests are the stage's acceptance criteria: ``check`` printing the
exact steps a run would take, and the build cache being *reused* on a second
load instead of written again.
"""

from __future__ import annotations

import io
import json
import logging
import os
from typing import Any, Dict, List

import pytest

from crude.audio import NullBackend
from crude.errors import (
    CancelError,
    LoadError,
    ManifestError,
    ResourceError,
    ScriptError,
    EXIT_LOAD,
    EXIT_MANIFEST,
    EXIT_SCRIPT,
)
from crude.pd import FakeLibpd
from crude.project import (
    CHECK_PHASES,
    IMAGE,
    MANIFEST_NAME,
    PHASE_NAMES,
    PRESET,
    CancelToken,
    ConsoleReporter,
    JsonReporter,
    LoadOptions,
    LoadPlan,
    LoadStep,
    NullMedia,
    NullReporter,
    Project,
    SplashReporter,
    DeferredSerial,
    ImageHandle,
    LoadPipeline,
    SoundHandle,
    build_manifest,
    check_project,
    full_plan,
    load_project,
    log_level,
    open_project,
    preset_document,
)

#: "give the project a null backend", as opposed to letting it ask the chain
NO_BACKEND = object()

SCRIPT = 'PRINT("hello from the entry script")\n'


# --------------------------------------------------------------------------
# the projects the tests build
# --------------------------------------------------------------------------


def minimal() -> Dict[str, Any]:
    """The smallest project that loads: one script, no audio, nothing declared.

    ``terminal`` is what makes this cheap: it asks for no device, so a load of
    this project opens nothing a test machine might not have.
    """
    return {
        "id": "x",
        "entry": "main.crude",
        "runtime": {"mode": "terminal"},
        "audio": {"enabled": False},
    }


def payload(**sections) -> Dict[str, Any]:
    """The minimal manifest with one whole section replaced."""
    data = minimal()
    for key, value in sections.items():
        if value is not None:
            data[key] = value
    return data


AUDIO = {"backend": "null", "optional": True}


def with_audio(**sections) -> Dict[str, Any]:
    """A headless project that does want a stream, from the null backend."""
    data = payload(audio=AUDIO, runtime={"mode": "headless"})
    for key, value in sections.items():
        if value is not None:
            data[key] = value
    return data


@pytest.fixture
def build(root):
    """A manifest from the minimal project plus whole sections, for ``LoadOptions``.

    The ``make`` fixture of ``conftest`` merges onto the *example* manifest, which
    is right for validation and wrong for these tests: a question like "does this
    run want serial?" has to be answered about a project that says one thing.
    """
    def made(**sections):
        data = minimal()
        for key, value in sections.items():
            if value is not None:
                data[key] = value
        return build_manifest(data, root=str(root))

    return made


def table_patch(text: str = "#X obj 40 40 table wave 8;") -> str:
    """A hand-written patch whose one table is what an array file fills."""
    return ("#N canvas 0 22 450 300 10;\n"
            "#X obj 40 20 r tick;\n"
            f"{text}\n")


@pytest.fixture(autouse=True)
def no_project_holds_a_log_open():
    """Let go of every project log after each test.

    A logger is named after the project *id*, not its directory, so a handler a
    test left attached would be inherited by the next project of the same name -
    and on Windows it would keep the temporary directory from being removed.
    """
    yield
    for name in list(logging.root.manager.loggerDict):
        if not name.startswith("crude."):
            continue
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


@pytest.fixture
def fake_project(project):
    """Build a real project directory and a :class:`Project` that is not loaded.

    The fakes are the defaults: a ``FakeLibpd``, a ``NullBackend`` and options
    that say nothing to a terminal. ``backend=None`` asks for no backend at all,
    which is how a test makes the pipeline go looking for one.
    """
    def build(data: Dict[str, Any] = None, files: Dict[str, str] = None, *,
              options: LoadOptions = None, lib=None, backend=NO_BACKEND,
              **inject: Any) -> Project:
        project.manifest(data if data is not None else minimal())
        written = {"main.crude": SCRIPT}
        written.update(files or {})
        for name, text in written.items():
            project.file(name, text)

        if backend is NO_BACKEND:
            backend = NullBackend()
        return Project.open(
            str(project.directory),
            options=options if options is not None else LoadOptions(progress="none"),
            lib=lib if lib is not None else FakeLibpd(),
            backend=backend,
            **inject,
        )

    return build


@pytest.fixture
def loaded(fake_project):
    """Run the whole pipeline and hand back the project, or let it raise."""
    def build(*args, reporter=None, **kwargs) -> Project:
        project = fake_project(*args, **kwargs)
        project.load(reporter if reporter is not None else NullReporter())
        return project

    return build


@pytest.fixture
def attempt(fake_project):
    """Load and *not* raise: returns ``(project, error)``, the failure's report."""
    def build(*args, reporter=None, **kwargs):
        project = fake_project(*args, **kwargs)
        try:
            project.load(reporter if reporter is not None else NullReporter())
        except BaseException as exc:  # noqa: BLE001 - the test asserts on it
            return project, exc
        return project, None

    return build


def step(project: Project, name: str):
    """The record one phase left behind."""
    return project.report[name]


def details(project: Project, name: str) -> List[str]:
    return step(project, name).details


def mentioned(project: Project, name: str, text: str) -> bool:
    """Did this phase say anything containing ``text``?"""
    return any(text in line for line in details(project, name))


# --------------------------------------------------------------------------
# what the command line said, against what the manifest said
# --------------------------------------------------------------------------


def test_terminal_mode_does_not_fight_for_the_audio_device(make):
    manifest = make({"runtime": {"mode": "terminal"}})

    assert LoadOptions().wants_audio(manifest) is False
    assert LoadOptions(audio=True).wants_audio(manifest) is True
    assert LoadOptions().audio_backend(manifest) == "null"


def test_a_manifest_that_wants_audio_gets_a_stream(make):
    manifest = make({"runtime": {"mode": "headless"}})

    assert LoadOptions().wants_audio(manifest) is True


def test_no_audio_overrides_even_a_manifest_that_insists(make):
    manifest = make({"runtime": {"mode": "window"}, "audio": {"optional": False}})

    assert LoadOptions(audio=False).wants_audio(manifest) is False
    assert LoadOptions(audio=False).audio_backend(manifest) == "null"
    assert LoadOptions(audio=False).audio_strict(manifest) is False


def test_only_a_project_that_needs_audio_and_meant_it_is_strict(make):
    manifest = make({"runtime": {"mode": "headless"}})

    assert LoadOptions().audio_strict(manifest) is True
    assert LoadOptions().audio_strict(make({"audio": {"optional": True}})) is False
    assert LoadOptions(backend="pyaudio").audio_backend(manifest) == "pyaudio"


def test_the_manifest_names_the_backend_and_the_command_line_can_outrank_it(make):
    manifest = make({"runtime": {"mode": "headless"}, "audio": {"backend": "null"}})

    assert LoadOptions().audio_backend(manifest) == "null"
    assert LoadOptions(backend="sounddevice").audio_backend(manifest) == "sounddevice"


def test_serial_is_off_only_when_the_command_line_says_so(build):
    manifest = build(serial=[{"id": "arduino", "port": "COM3"}])

    assert LoadOptions().wants_serial(manifest) is True
    assert LoadOptions(serial=False).wants_serial(manifest) is False
    assert LoadOptions().wants_serial(build()) is False


def test_midi_is_only_wanted_when_the_manifest_declares_it(build):
    """Absent the ``midi`` key a project never builds a runtime; declaring it opts in."""
    assert LoadOptions().wants_midi(build()) is False

    declared = build(midi={})
    assert LoadOptions().wants_midi(declared) is True
    assert LoadOptions(midi=False).wants_midi(declared) is False

    turned_off = build(midi={"enabled": False})
    assert LoadOptions().wants_midi(turned_off) is False


def test_the_midi_backend_and_strict_come_from_the_declared_block(build):
    declared = build(midi={"backend": "null"})
    assert LoadOptions().midi_backend(declared) == "null"
    assert LoadOptions(midi_backend_name="rtmidi").midi_backend(declared) == "rtmidi"
    # a project that does not want MIDI asks for the silent backend, always
    assert LoadOptions().midi_backend(build()) == "null"

    assert LoadOptions().midi_strict(build(midi={})) is False
    assert LoadOptions().midi_strict(
        build(midi={"optional": False})) is True


def test_the_progress_spec_and_the_frame_rate_come_from_either_side(make):
    manifest = make({"loading": {"progress": "json"},
                     "runtime": {"mode": "terminal", "target_fps": 24}})

    assert LoadOptions().progress_spec(manifest) == "json"
    assert LoadOptions(progress="none").progress_spec(manifest) == "none"
    assert LoadOptions().target_fps(manifest) == 24
    assert LoadOptions(fps=10).target_fps(manifest) == 10


def test_the_mode_the_run_uses_is_the_overridden_one(make):
    manifest = make({"runtime": {"mode": "window"}})

    assert LoadOptions().wanted_mode(manifest) == "window"
    assert LoadOptions(mode="headless").wanted_mode(manifest) == "headless"


def test_the_options_say_what_this_run_decided_for_the_report(build):
    manifest = build(runtime={"mode": "headless"},
                     serial=[{"id": "arduino", "port": "COM3"}],
                     audio=AUDIO)

    assert LoadOptions(progress="json").as_dict(manifest) == {
        "mode": "headless",
        "audio": True,
        "backend": "null",
        "strict_audio": False,
        "serial": True,
        "midi": False,
        "progress": "json",
        "fps": 60,
        "rebuild_pd": False,
    }
    # and the same questions answered for a project that wants no sound
    assert LoadOptions().as_dict(build()) == {
        "mode": "terminal",
        "audio": False,
        "backend": "null",
        "strict_audio": False,
        "serial": False,
        "midi": False,
        "progress": "auto",
        "fps": 60,
        "rebuild_pd": False,
    }


@pytest.mark.parametrize("name,expected", [
    ("none", logging.CRITICAL + 10),
    ("error", logging.ERROR),
    ("WARNING", logging.WARNING),
    ("warn", logging.WARNING),
    ("info", logging.INFO),
    (" debug ", logging.DEBUG),
])
def test_the_log_levels_a_manifest_names(name, expected):
    assert log_level(name) == expected


def test_an_unknown_log_level_and_a_missing_one_are_the_default():
    assert log_level("chatty") == log_level("verbose", default="warning")
    assert log_level(None) == logging.WARNING
    assert log_level("shout", default="debug") == logging.DEBUG
    assert log_level(7) == 7


# --------------------------------------------------------------------------
# a project that has only been opened
# --------------------------------------------------------------------------


def test_opening_a_project_reads_the_manifest_and_nothing_else(fake_project):
    project = fake_project(payload(), files={"assets/note.txt": "hi"})

    assert project.id == "x"
    assert project.name == "x"
    assert project.directory == os.path.abspath(str(project.manifest.project_dir))
    assert project.entry_path.endswith("main.crude")
    assert project.report is None
    assert project.resources is None
    assert project.active is False
    assert project.notes == []


def test_a_missing_manifest_is_refused_before_anything_is_loaded(tmp_path):
    with pytest.raises(ManifestError) as caught:
        Project.open(str(tmp_path / "nowhere"))

    assert caught.value.exit_code == EXIT_MANIFEST
    assert "nowhere" in caught.value.pointer
    assert MANIFEST_NAME in str(caught.value)
    assert "is this a crude project?" in str(caught.value)


def test_a_bad_manifest_keeps_the_pointer_and_the_file_name(project):
    project.manifest(raw=json.dumps({"id": "x", "no_such_key": 1}))

    with pytest.raises(ManifestError) as caught:
        Project.open(str(project.directory))

    assert caught.value.exit_code == EXIT_MANIFEST
    # the pointer is where in the document; the message says which document
    assert caught.value.pointer == "no_such_key"
    assert "no_such_key" in caught.value.message
    assert MANIFEST_NAME in str(caught.value)


def test_a_note_is_kept_for_the_report_and_the_log(fake_project):
    project = fake_project()
    project.setup_logging("info")
    project.note("the windmill is turning")

    assert project.notes == ["the windmill is turning"]
    with open(project.log_path, encoding="utf-8") as handle:
        assert "the windmill is turning" in handle.read()


def test_the_environment_makes_the_two_directories_it_writes_into(fake_project):
    project = fake_project()

    made = project.ensure_directories()

    assert [os.path.basename(path) for path in made] == ["pd", "logs"]
    assert os.path.isdir(project.manifest.build_path())
    assert project.ensure_directories() == []       # nothing left to make
    assert project.created == made                  # and it remembers both


def test_logging_goes_to_a_file_named_after_the_project(fake_project):
    project = fake_project()

    logger = project.setup_logging("debug")

    assert logger.level == logging.DEBUG
    assert project.log_path.endswith(os.path.join("logs", "x.log"))
    assert os.path.isfile(project.log_path)
    # calling it twice does not stack a second handler on the same file
    assert project.setup_logging().handlers == logger.handlers


def test_a_warning_is_a_note_the_log_can_see(fake_project):
    project = fake_project()
    project.setup_logging("warning")
    project.warning("the drum is loose")

    assert project.notes == ["the drum is loose"]
    with open(project.log_path, encoding="utf-8") as handle:
        assert "WARNING the drum is loose" in handle.read()


def test_reading_the_entry_script_names_the_project_it_belongs_to(project):
    project.manifest(minimal())
    project.file("main.crude", SCRIPT)
    os.remove(project.directory / "main.crude")

    with pytest.raises(ResourceError) as caught:
        Project.open(str(project.directory)).entry_source()

    assert "the entry script 'main.crude' of project 'x'" in str(caught.value)


def test_the_host_object_is_what_a_binding_reaches_through(fake_project):
    project = fake_project()

    host = project.host_object()

    assert host.project is project
    assert host.resources is None
    assert host.audio is None
    assert callable(host.attach_program)


def test_describe_says_what_the_manifest_declared_without_loading_it(fake_project):
    project = fake_project(payload())

    assert project.describe() == [
        "project   x 'x'",
        "entry     main.crude",
        "mode      terminal at 60 fps",
        "audio     off",
        "patches   0 declared",
        "serial    off",
        "midi      off",
    ]


def test_an_unloaded_project_has_no_resources_to_give(fake_project):
    project = fake_project()

    for ask in (lambda: project.resource("main.crude"),
               lambda: project.array_values("wave"),
               lambda: project.read_preset("one")):
        with pytest.raises(ResourceError, match="has not been loaded|not loaded"):
            ask()


# --------------------------------------------------------------------------
# the handle types the media phase returns
# --------------------------------------------------------------------------


def test_a_null_media_handle_says_how_big_the_file_is(tmp_path):
    target = tmp_path / "atlas.png"
    target.write_bytes(b"0123456789")

    handle = NullMedia().load_image(FakeEntry("atlas", str(target)))

    assert handle == ImageHandle("atlas", str(target), 10)
    assert handle.describe() == "atlas 10 B"
    assert handle.as_dict() == {"id": "atlas", "path": str(target), "size": 10,
                                "width": 0, "height": 0, "decoded": False,
                                "detail": ""}


def test_a_decoded_handle_shows_its_size_instead(tmp_path):
    handle = ImageHandle("atlas", str(tmp_path / "atlas.png"), 10, 512, 256, True)

    assert handle.describe() == "atlas 512x256"


def test_a_sound_handle_counts_frames_when_it_knows_them(tmp_path):
    assert SoundHandle("tick", "t.wav", 20).describe() == "tick 20 B"
    assert SoundHandle("tick", "t.wav", 20, 441).describe() == "tick 441 frames"


def test_sizing_a_file_that_is_there_is_a_resource_error(tmp_path):
    with pytest.raises(ResourceError) as caught:
        NullMedia().load_sound(FakeEntry("gone", str(tmp_path / "gone.wav")))

    assert "'gone.wav' could not be read" in str(caught.value)


class FakeEntry:
    """The two fields a media host is allowed to read off a declared resource."""

    def __init__(self, ident: str, path: str):
        self.id = ident
        self.path = path


def test_the_deferred_serial_host_records_what_it_was_asked_to_open():
    host = DeferredSerial()

    assert host.open("a declaration") is None
    assert host.opened == ["a declaration"]
    assert host.name == "deferred"
    assert host.close(None) is None


# --------------------------------------------------------------------------
# the whole pipeline, against nothing but fakes
# --------------------------------------------------------------------------


def test_a_terminal_project_runs_every_phase_and_reports_each_one(loaded):
    project = loaded()

    assert project.report.names == PHASE_NAMES
    assert [record.name for record in project.report.done] == list(PHASE_NAMES)
    assert project.report.ok
    assert project.report.percent == 100.0
    assert project.report.summary() == "loaded 15 of 15 steps (100%)"
    assert project.active is True
    assert str(project) == "Project x (terminal, loaded)"


def test_the_audio_and_the_patches_only_arrive_in_the_order_that_works(loaded):
    project = loaded(with_audio())

    assert project.resources is not None
    assert project.audio.opened is True
    assert project.session.started is True
    assert project.pd.session is project.session
    assert project.program is not None
    assert project.ast is not None
    # the report is the proof: audio was open before libpd was even loaded
    assert [record.name for record in project.report.done].index("audio.device") \
        < [record.name for record in project.report.done].index("pd.core")


def test_the_stream_is_opened_but_never_started_and_dsp_is_off(loaded):
    lib = FakeLibpd()
    project = loaded(with_audio(), lib=lib)

    assert project.audio.running is False           # the loop starts it, not us
    assert project.session.dsp_on is False
    assert ("message", "pd", "dsp", (0.0,)) in lib.sent
    assert mentioned(project, "audio.device", "open")
    assert mentioned(project, "runtime.activate", "not started")


def test_the_session_is_handed_the_streams_pause_switch(loaded):
    project = loaded(with_audio())
    backend = project.audio.backend

    with project.session.control():          # the control-plane rule, in one line
        assert backend.paused is True
    assert backend.paused is False


def test_a_device_that_cannot_be_opened_stops_the_load(attempt):
    class Broken:
        name = "broken"
        opened = False
        live = False
        notes: List[str] = []

        def open(self, *args):
            raise RuntimeError("no such device")

        def pause_switch(self):
            return lambda flag: None

    project, error = attempt(with_audio(), backend=Broken())

    assert isinstance(error, LoadError)
    assert error.exit_code == EXIT_LOAD
    assert project.report.failed_name == "audio.device"
    assert "no such device" in str(error)
    # nothing after the device was reached, and nothing was left open
    assert project.report["pd.core"].started is False
    assert project.shutdown_steps == []


def test_the_declared_search_paths_are_registered_with_libpd(loaded):
    lib = FakeLibpd()
    project = loaded(
        with_audio(pd={"search_paths": ["pd", "pd/absynth"]}),
        files={"pd/tick.pd": table_patch()}, lib=lib)

    assert os.path.join(project.directory, "pd") in lib.search_paths
    # a search path that is not there is said, not invented
    assert mentioned(project, "pd.core", "pd/absynth is not there")


# --------------------------------------------------------------------------
# the patches and the arrays that fill them
# --------------------------------------------------------------------------


def test_an_authored_patch_is_written_to_the_cache_and_then_opened(loaded):
    project = loaded(with_audio(pd={"patches": [
        {"id": "synth", "authored": {"width": 400, "height": 300,
                                     "preload": True}}]}))
    name = "pd.build"

    assert project.report[name].ok
    assert mentioned(project, name, "synth: 400x300")
    assert mentioned(project, name, "1 built, 0 reused")
    assert list(project.pd.cache.paths) == ["synth"]
    assert os.path.isfile(project.pd.cache.paths["synth"])
    assert "synth" in project.pd.patches
    assert mentioned(project, "pd.patches", "synth (authored")


def test_a_builder_can_draw_the_patch_the_manifest_only_named(loaded):
    def draw(doc):
        doc.add_box("obj", 40, 40, "r tick")
        doc.add_table("wave", 8)

    project = loaded(
        with_audio(pd={"patches": [{"id": "synth", "authored": {
            "width": 400, "height": 300, "preload": True}}]}),
        builders={"synth": draw})

    assert mentioned(project, "pd.build", "2 box(es), 1 table(s)")
    # the table the builder declared is the array libpd now has
    assert project.pd.array_size("synth", "wave") == 8


def test_an_authored_patch_that_is_not_preloaded_is_built_and_left_to_the_script(
        loaded):
    project = loaded(with_audio(pd={"patches": [
        {"id": "late", "authored": {"width": 300, "height": 200,
                                    "preload": False}}]}))

    assert os.path.isfile(project.pd.cache.paths["late"])
    assert "late" not in project.pd.patches
    assert mentioned(project, "pd.patches", "the script loads it with PD_LOAD")


def test_a_hand_written_patch_is_opened_from_the_file_the_manifest_names(loaded):
    lib = FakeLibpd()
    project = loaded(
        with_audio(pd={"patches": [{"id": "tick", "file": "pd/tick.pd"}]}),
        files={"pd/tick.pd": table_patch()}, lib=lib)

    assert "tick" in project.pd.patches
    assert mentioned(project, "pd.patches", "tick from pd/tick.pd")
    # what the patch declares is now real in the fake: a receiver and a table
    assert "tick" in lib.receivers
    assert project.pd.array_size("tick", "wave") == 8


def test_an_optional_patch_that_is_missing_is_simply_skipped(loaded):
    project = loaded(with_audio(pd={"patches": [
        {"id": "ghost", "file": "pd/ghost.pd", "optional": True}]}))

    assert project.report["resources.validate"].ok
    assert mentioned(project, "pd.patches", "skipped")
    assert "ghost" not in project.pd.patches


def test_a_required_patch_that_is_missing_is_named_by_the_resource_phase(attempt):
    project, error = attempt(with_audio(pd={"patches": [
        {"id": "drums", "file": "pd/drums.pd"}]}))

    assert isinstance(error, ResourceError)
    assert error.exit_code == 3
    assert project.report.failed_name == "resources.validate"
    assert mentioned(project, "resources.validate", "patch 'drums'")


def test_an_array_fills_the_table_of_the_patch_that_declares_it(loaded):
    lib = FakeLibpd()
    project = loaded(
        with_audio(pd={"patches": [{"id": "tick", "file": "pd/tick.pd"}]},
                   resources={"arrays": [{"id": "wave", "file": "assets/array.txt",
                                         "format": "text", "patch": "tick"}]}),
        files={"pd/tick.pd": table_patch("#X obj 40 40 table wave 4;"),
               "assets/array.txt": "1\n2\n3\n4\n5\n"},
        lib=lib)

    assert project.arrays["wave"] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert lib.arrays["wave"] == [1.0, 2.0, 3.0, 4.0]     # the table holds 4
    assert mentioned(project, "pd.arrays", "wave -> tick: 4 of 5 number(s)")
    assert mentioned(project, "pd.arrays", "the table holds 4")
    assert mentioned(project, "pd.arrays", "1 array(s), 5 number(s) parsed")


def test_an_array_for_a_patch_that_is_not_open_is_queued_and_not_lost(loaded):
    project = loaded(
        with_audio(pd={"patches": [{"id": "late", "authored": {
                        "width": 300, "height": 200, "preload": False}}]},
                   resources={"arrays": [{"id": "wave", "file": "assets/array.txt",
                                         "patch": "late"}]}),
        files={"assets/array.txt": "1\n2\n"})

    assert project.arrays["wave"] == [1.0, 2.0]
    assert project.pending_arrays == {"wave": ["late"]}
    assert mentioned(project, "pd.arrays", "patch 'late' is not open; queued")


def test_an_array_with_no_patch_named_is_kept_for_the_script(loaded):
    project = loaded(
        with_audio(resources={"arrays": [{"id": "curve",
                                         "file": "assets/array.txt"}]}),
        files={"assets/array.txt": "1\n2\n3\n"})

    assert project.arrays["curve"] == [1.0, 2.0, 3.0]
    assert project.pending_arrays == {}
    assert mentioned(project, "pd.arrays", "no patch named, kept for the script")


def test_an_array_naming_a_table_that_is_not_there_is_a_warning(attempt):
    project, error = attempt(
        with_audio(pd={"patches": [{"id": "tick", "file": "pd/tick.pd"}]},
                   resources={"arrays": [{"id": "nothere",
                                          "file": "assets/array.txt",
                                          "patch": "tick"}]}),
        files={"pd/tick.pd": table_patch(), "assets/array.txt": "1\n2\n"})

    assert error is None                       # the load still came up
    assert project.report["pd.arrays"].ok
    assert any("array 'nothere'" in note for note in project.notes)
    assert mentioned(project, "pd.arrays", "no table in 'tick'")


# --------------------------------------------------------------------------
# presets, media, serial
# --------------------------------------------------------------------------


def test_the_shipped_patterns_are_read_for_preset_load(loaded):
    project = loaded(
        with_audio(resources={"presets": ["presets/*.json"]}),
        files={"presets/one.json":
               json.dumps(preset_document("one", [1.0, 2.0, 3.0])),
               "presets/two.json": json.dumps(preset_document("two", {"mix": 3}))})

    assert project.presets == {"one": [1.0, 2.0, 3.0], "two": {"mix": 3}}
    assert mentioned(project, "presets", "one from presets/one.json (3 value(s))")
    assert mentioned(project, "presets", "two from presets/two.json (1 key(s))")
    assert mentioned(project, "presets", "2 preset(s) ready")


def test_images_and_sounds_are_sized_and_the_bytes_are_counted(loaded):
    project = loaded(
        with_audio(resources={"images": ["assets/atlas.png"],
                              "sounds": ["assets/tick.wav"]}),
        files={"assets/atlas.png": "0123456789", "assets/tick.wav": "abcd"})

    assert project.images["atlas"].size == 10
    assert project.sounds["tick"].size == 4
    assert mentioned(project, "images", "atlas 10 B")
    assert mentioned(project, "images", "1 image(s), 10 byte(s) read")
    assert mentioned(project, "images", "bytes only")
    assert mentioned(project, "sounds", "1 sound(s), 4 byte(s) read")


def test_a_media_host_can_decode_what_it_wants(loaded):
    class Decoding:
        name = "decoding"

        def load_image(self, entry):
            return ImageHandle(entry.id, entry.path, 8, 512, 256, True, "GL texture")

        def load_sound(self, entry):
            return SoundHandle(entry.id, entry.path, 8, 441)

    project = loaded(
        with_audio(resources={"images": ["assets/atlas.png"],
                              "sounds": ["assets/tick.wav"]}),
        files={"assets/atlas.png": "0123456789", "assets/tick.wav": "abcd"},
        media=Decoding())

    assert mentioned(project, "images", "atlas 512x256")
    assert mentioned(project, "sounds", "tick 441 frames")
    assert not mentioned(project, "images", "bytes only")


def test_the_serial_phase_records_its_links_and_says_who_opens_them(loaded):
    host = DeferredSerial()
    project = loaded(with_audio(serial=[{"id": "arduino", "port": "COM3"}]),
                     serial=host)
    decl = project.manifest.serial[0]

    assert host.opened == [decl]
    assert project.links == [(decl, None)]
    assert mentioned(project, "serial", "arduino COM3 115200 line (not opened)")
    assert any("the serial stage opens the ports" in note
               for note in project.notes)


def test_a_host_that_does_open_links_has_them_recorded_for_the_unwind(loaded):
    class Links:
        name = "hardware"

        def __init__(self):
            self.opened = []
            self.closed = []

        def open(self, decl):
            link = object()
            self.opened.append((decl.id, link))
            return link

        def close(self, link):
            self.closed.append(link)

    host = Links()
    project = loaded(with_audio(serial=[{"id": "arduino", "port": "COM3"}]),
                     serial=host)

    assert len(host.opened) == 1
    assert project.links[0][1] is host.opened[0][1]
    project.shutdown()
    assert host.closed == [host.opened[0][1]]


def test_the_command_line_can_refuse_serial_for_this_run(loaded):
    host = DeferredSerial()
    project = loaded(with_audio(serial=[{"id": "arduino", "port": "COM3"}]),
                     options=LoadOptions(serial=False, progress="none"),
                     serial=host)

    assert host.opened == []
    assert project.links == []
    assert mentioned(project, "serial", "1 link(s) declared, not opened for this run")


# --------------------------------------------------------------------------
# the midi phase
# --------------------------------------------------------------------------


def test_a_project_that_declares_no_midi_never_builds_a_runtime(loaded):
    project = loaded(with_audio())

    assert project.midi is None
    assert mentioned(project, "midi", "MIDI is off for this run")


def test_the_midi_phase_builds_a_runtime_on_the_null_backend(loaded):
    """Declaring ``midi`` opts in; the chain lands on the always-present backend."""
    from crude.midi import MidiRuntime

    project = loaded(with_audio(midi={"backend": "null"}))

    assert isinstance(project.midi, MidiRuntime)
    assert project.midi.backend.name == "null"
    assert mentioned(project, "midi", "null")
    assert project.host_object().midi is project.midi
    # a load that opens something records how to close it
    project.shutdown()


def test_the_command_line_can_refuse_midi_for_this_run(loaded):
    from crude.midi import MidiRuntime

    project = loaded(with_audio(midi={"backend": "null"}),
                     options=LoadOptions(midi=False, progress="none"))

    assert not isinstance(project.midi, MidiRuntime)
    assert mentioned(project, "midi", "MIDI is off for this run")


def test_a_caller_supplied_midi_runtime_is_the_one_the_host_sees(loaded):
    class Stub:
        backend = "injected"

        def attach_program(self, program):
            self.program = program

        def close_all(self):
            pass

    stub = Stub()
    project = loaded(with_audio(midi={"backend": "null"}), midi=stub)

    assert project.midi is stub
    assert project.host_object().midi is stub


# --------------------------------------------------------------------------
# the entry script
# --------------------------------------------------------------------------


def test_the_entry_script_is_parsed_but_never_run_by_the_load(loaded):
    project = loaded(files={"main.crude": "VAR answer = 6 * 7\nPRINT(nope)\n"})

    assert project.report["script.compile"].ok        # a *runtime* error is later
    assert project.ast is not None
    assert mentioned(project, "script.compile", "main.crude: 2 line(s), ")
    assert project.program.name == "main.crude"


def test_the_program_is_the_one_every_subsystem_calls_back_through(loaded):
    project = loaded()

    assert project.pd.program is project.program
    assert project.host_object().project is project
    assert project.host_object().attach_program == project.attach_program


def test_the_loop_can_run_the_script_and_keeps_its_exit_code(loaded, capsys):
    project = loaded(files={"main.crude": "VAR answer = 6 * 7\nPRINT(answer)\n"})

    project.run_script()
    assert capsys.readouterr().out.strip().endswith("42")


def test_a_script_that_breaks_when_it_runs_is_a_script_error(loaded):
    project = loaded(files={"main.crude": "PRINT(nope)\n"})

    with pytest.raises(ScriptError) as caught:
        project.run_script()

    assert caught.value.exit_code == EXIT_SCRIPT
    assert str(caught.value).startswith("main.crude:")


# --------------------------------------------------------------------------
# a load that does not come up
# --------------------------------------------------------------------------


def test_one_refusal_names_every_file_that_is_missing(attempt):
    """Nothing is half-loaded, and no file is left for the person to hunt for."""
    project, exc = attempt(
        with_audio(resources={"images": ["assets/one.png", "assets/two.png"]}))

    assert type(exc) is ResourceError
    assert project.report.failed_name == "resources.validate"
    assert "2 declared file(s) of project 'x' are missing" in str(exc)
    assert "assets/one.png" in str(exc)
    assert "assets/two.png" in str(exc)
    assert step(project, "pd.core").started is False


def test_a_failure_closes_everything_the_earlier_phases_opened(attempt):
    """The whole point of recording a closer for every thing that gets opened."""
    class Links:
        name = "hardware"

        def __init__(self):
            self.opened = []
            self.closed = []

        def open(self, decl):
            link = object()
            self.opened.append(link)
            return link

        def close(self, link):
            self.closed.append(link)

    host = Links()
    backend = NullBackend()
    project, exc = attempt(
        with_audio(
            pd={"patches": [{"id": "late", "authored": {
                "width": 300, "height": 200, "preload": True}}]},
            serial=[{"id": "arduino", "port": "COM3"}],
        ),
        files={"main.crude": "PRINT(\n"},
        backend=backend,
        serial=host,
    )

    # the script is the last phase, so everything else had been opened by then
    assert type(exc) is ScriptError
    assert project.shutdown_steps == []
    assert project.pd.patches == {}
    assert project.session.patches == {}
    assert project.audio.opened is False
    assert backend.opened is False
    assert host.closed == host.opened
    assert project.active is False
    #  audio backend, libpd instance, the patch, the serial link - in that order
    assert mentioned(project, "script.compile",
                     "unwound 4 open thing(s) in reverse order")


def test_a_teardown_that_raises_is_reported_and_the_rest_still_closes(fake_project):
    """A stream that would not stop is a reason to still close the patches above it."""
    project = fake_project()
    closed: List[str] = []

    def bite():
        raise RuntimeError("it bit")

    project.record("the oldest thing", lambda: closed.append("oldest"))
    project.record("the biting thing", bite)
    project.record("the newest thing", lambda: closed.append("newest"))

    messages = project.shutdown()

    assert closed == ["newest", "oldest"]
    assert messages == ["could not close the biting thing: it bit"]
    assert project.notes == ["could not close the biting thing: it bit"]
    assert project.shutdown_steps == []
    assert project.shutdown() == []          # idempotent


# --------------------------------------------------------------------------
# stopping a load on purpose
# --------------------------------------------------------------------------


def test_a_stop_asked_for_before_the_run_starts_says_so(fake_project):
    project = fake_project()
    token = CancelToken()
    token.cancel("the person pressed q")

    with pytest.raises(CancelError) as caught:
        project.load(NullReporter(), token=token)

    assert caught.value.exit_code == EXIT_LOAD
    assert caught.value.reason == "the person pressed q"
    assert project.report.failed_name == "manifest"
    assert project.report.summary() == \
        "cancelled at manifest after 0 of 15 steps (0%)"
    assert project.report.lines()[0].startswith("stop manifest")
    # the first boundary was the first step, so nothing was ever opened
    assert project.audio is None
    assert project.pd is None
    assert project.shutdown_steps == []


def test_a_stop_asked_for_half_way_stops_at_the_next_step(fake_project):
    """Cooperative: the phase in flight ends, and the *next* one refuses to start."""
    project = fake_project()
    token = CancelToken()
    pipeline = project.pipeline(NullReporter(), token=token)
    original = pipeline._phase_pd_core

    def pd_core():
        token.cancel("the splash screen was closed")
        original()

    pipeline._phase_pd_core = pd_core

    with pytest.raises(CancelError):
        pipeline.run()

    assert project.report.failed_name == "pd.build"
    assert [record.name for record in project.report.ran] == list(PHASE_NAMES[:6])
    # manifest 2 + environment 2 + resources 8 + audio 8 + pd.core 5
    assert project.report.percent == 25.0
    assert step(project, "pd.build").cancelled is True
    assert step(project, "pd.patches").started is False
    assert any(line.startswith("stop") for line in project.report.lines())


def test_a_key_pressed_during_a_phase_is_a_stop_and_not_a_crash(fake_project):
    project = fake_project()
    pipeline = project.pipeline(NullReporter())

    def interrupt():
        raise KeyboardInterrupt()

    pipeline._phase_images = interrupt

    with pytest.raises(CancelError) as caught:
        pipeline.run()

    assert caught.value.reason == "stopped by the user"
    assert project.report.failed_name == "images"
    assert project.report.summary() == "cancelled at images after 9 of 15 steps (59%)"


def test_a_phase_that_raises_something_plain_names_the_step_that_failed(fake_project):
    """A bare ``OSError`` says nothing about which step found it, so the report does."""
    project = fake_project()
    pipeline = project.pipeline(NullReporter())

    def boom():
        raise ValueError("boom")

    pipeline._phase_presets = boom

    with pytest.raises(LoadError) as caught:
        pipeline.run()

    assert caught.value.step == "presets"
    assert caught.value.exit_code == EXIT_LOAD
    assert str(caught.value) == "load step 'presets' failed: boom"
    assert project.report.failed_name == "presets"
    assert project.report.error is caught.value


def test_a_plan_a_caller_wrote_by_hand_runs_exactly_its_own_steps(fake_project):
    plan = LoadPlan((
        LoadStep("manifest", 3, "the manifest"),
        LoadStep("blink", 1, "a step nobody owns"),
    ), label="hand written", mode="terminal")
    project = fake_project()

    report = project.pipeline(NullReporter(), plan=plan).run()

    assert report.names == ("manifest", "blink")
    assert report.ok
    assert project.pd is None
    assert project.audio is None
    assert mentioned(project, "manifest", "entry main.crude")
    # a step this pipeline has no work for is said so, not silently skipped
    assert details(project, "blink") == ["nothing for this pipeline to do"]


# --------------------------------------------------------------------------
# crude check
# --------------------------------------------------------------------------


def test_check_runs_only_the_steps_that_need_no_hardware(fake_project):
    project = fake_project(with_audio(  # a project that *would* open a device
        pd={"patches": [{"id": "late", "authored": {
            "width": 300, "height": 200, "preload": True}}]},
        serial=[{"id": "arduino", "port": "COM3"}],
    ))

    report = project.check(NullReporter())

    assert report.names == CHECK_PHASES
    assert report.ok
    assert project.audio is None
    assert project.session is None
    assert project.pd is None
    assert project.links == []
    assert project.active is False


def test_check_prints_the_exact_steps_a_run_would_take(fake_project):
    """The stage's acceptance criterion: ``crude check`` on a good project."""
    project = fake_project()
    reporter = ConsoleReporter(stream=io.StringIO())

    checked = check_project(str(project.directory), reporter)

    assert [line for line in reporter.lines if line.startswith("[")] == [
        "[ 13%] manifest           ok   the manifest",
        "[ 27%] environment        ok   the folders and the log",
        "[ 80%] resources.validate ok   the declared files",
        "[100%] script.compile     ok   the entry script",
    ]
    assert reporter.lines[-1] == "loaded 4 of 4 steps (100%)"
    assert checked.report.percent == 100.0
    assert checked.pd is None


# --------------------------------------------------------------------------
# the build cache
# --------------------------------------------------------------------------

TWO_AUTHORED = {"patches": [
    {"id": "one", "authored": {"width": 300, "height": 200, "preload": True}},
    {"id": "two", "authored": {"width": 320, "height": 200, "preload": True}},
]}


def test_the_second_load_of_a_project_reuses_the_build_cache(fake_project):
    """The other acceptance criterion: patches are rebuilt only when they changed."""
    data = with_audio(pd=TWO_AUTHORED)
    first = fake_project(data)
    first.load(NullReporter())

    assert mentioned(first, "pd.build", "2 built, 0 reused")
    first.shutdown()

    second = fake_project(data)
    second.load(NullReporter())

    assert mentioned(second, "pd.build", "0 built, 2 reused")
    assert second.pd.cache.report()["generated"] == 0
    assert second.pd.cache.report()["reused"] >= 2
    assert sorted(second.pd.patches) == ["one", "two"]
    second.shutdown()


def test_rebuilding_asks_for_every_patch_to_be_written_again(loaded):
    project = loaded(with_audio(pd=TWO_AUTHORED),
                     options=LoadOptions(rebuild_pd=True, progress="none"))

    assert mentioned(project, "pd.build", "rebuilding")
    assert mentioned(project, "pd.build", "2 built, 0 reused")


# --------------------------------------------------------------------------
# who reports, and what the project says about itself afterwards
# --------------------------------------------------------------------------


@pytest.mark.parametrize("spec,expected", [
    ("json", JsonReporter),
    ("none", NullReporter),
    ("console", ConsoleReporter),
    ("auto", ConsoleReporter),      # a terminal project: the console is the answer
    ("splash", ConsoleReporter),    # ... until there is a window to draw on
])
def test_the_progress_spec_picks_the_reporter(fake_project, spec, expected):
    project = fake_project(options=LoadOptions(progress=spec))

    assert type(project.pipeline().reporter) is expected


def test_a_window_project_with_a_canvas_gets_the_splash(fake_project):
    project = fake_project(payload(runtime={"mode": "window"}),
                           options=LoadOptions())

    assert type(project.pipeline(canvas=object()).reporter) is SplashReporter


def test_a_progress_mode_that_does_not_exist_is_refused(fake_project):
    project = fake_project(options=LoadOptions(progress="chatty"))

    with pytest.raises(ValueError) as caught:
        project.pipeline()

    assert "there is no progress mode 'chatty'" in str(caught.value)


def test_the_project_describes_itself_for_the_command_line(loaded):
    project = loaded(
        with_audio(pd=TWO_AUTHORED,
                   resources={"images": ["assets/atlas.png"]},
                   serial=[{"id": "arduino", "port": "COM3"}]),
        files={"assets/atlas.png": "0123456789"})

    assert project.describe() == [
        "project   x 'x'",
        "entry     main.crude",
        "mode      headless at 60 fps",
        "audio     null 44100 Hz, 64 frames/block",
        "patches   2 declared",
        "serial    1 declared",
        "midi      off",
        "resources 1 image, 0 sound, 0 array, 0 preset",
    ]


def test_the_same_story_as_an_object_for_a_machine_to_read(loaded):
    project = loaded(with_audio(pd=TWO_AUTHORED,
                                resources={"images": ["assets/atlas.png"]}),
                     files={"assets/atlas.png": "0123456789"})

    info = project.info()

    assert info["id"] == "x"
    assert info["mode"] == "headless"
    assert info["active"] is True
    assert info["options"] == {"mode": "headless", "audio": True, "backend": "null",
                               "strict_audio": False, "serial": False, "midi": False,
                               "progress": "none", "fps": 60, "rebuild_pd": False}
    assert info["resources"] == {"image": 1, "sound": 0, "array": 0, "preset": 0}
    assert info["patches"] == ["one", "two"]
    assert info["images"] == ["atlas"]
    assert info["audio"]["backend"] == "null"
    assert info["audio"]["opened"] is True
    assert info["audio"]["running"] is False   # the loop starts it, not the load
