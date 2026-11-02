"""The project bindings as a script sees them: files, time, and the run.

Every test writes ``.crude`` source against a real :class:`~crude.project.Project`
in a ``tmp_path``, with the clock faked and no device, window or libpd in sight.
That is the point of the area: the same nine calls answer the same way whether the
project came up in a terminal over SSH or in a window, because they only talk to
the project behind the host.
"""

from __future__ import annotations

import json
import logging
import os
import types
from typing import Any, Dict

import pytest

from crude.bindings.project import (
    BINDINGS,
    NO_PROJECT,
    RESOURCE_PROPS,
    ResourceView,
)
from crude.bindings.registry import REGISTRY
from crude.errors import EXIT_OK, ExitRequest, ScriptError
from crude.lang import Program
from crude.lang.values import HostHandle, List, Number, String
from crude.project import (
    LoadOptions,
    LoadPipeline,
    Project,
    ResourceTable,
    build_manifest,
)
from crude.runtime import Clock, FakeClock

#: how a script says "the part I care about", so a failure names the right binding
ARRAYS = {
    "arrays": [{"id": "wave", "file": "assets/array.txt", "format": "text"}]
}

FILES = {
    "assets/array.txt": "0 0.25 0.5 1\n",
    "assets/knob_atlas.png": "an atlas of eleven bytes",
    "presets/loop.json": json.dumps(
        {"crude_preset": 1, "id": "loop", "data": [1, 2, 3]}),
    "presets/knob.json": json.dumps(
        {"crude_preset": 1, "id": "knob", "data": {"bpm": 140, "gain": 0.8}}),
}


def defaults(**sections) -> Dict[str, Any]:
    """A project that declares one array, one image and two presets."""
    data: Dict[str, Any] = {
        "id": "x",
        "entry": "main.crude",
        "runtime": {"mode": "headless", "target_fps": 24},
        "audio": {"enabled": False},
        "resources": {"images": ["assets/knob_atlas.png"],
                      "presets": ["presets/*.json"], **ARRAYS},
    }
    for key, value in sections.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def as_py(value):
    """A language value back to the Python a test can compare against."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, List):
        return [as_py(item) for item in value.elements]
    if isinstance(value, HostHandle):
        return value.target
    return value


def value_of(project, source):
    """Run ``source`` as a script inside ``project`` and hand back the raw value."""
    program = Program("<bindings>", host=project.host_object())
    value, error = program.run(source)
    assert error is None, error.as_string()
    return value


def on(project, source):
    """The same, in plain Python: ``on(p, 'GET(RES("wave"), "count")')`` -> ``4``."""
    return as_py(value_of(project, source))


@pytest.fixture(autouse=True)
def no_project_holds_a_log_open():
    """Let go of every project log, so a temporary directory can be deleted."""
    yield
    for name in list(logging.root.manager.loggerDict):
        if not name.startswith("crude."):
            continue
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


@pytest.fixture
def host_for(project):
    """Build the directory, the manifest and an unloaded :class:`Project`."""
    def build(files: Dict[str, str] = None, options: LoadOptions = None,
              clock: Any = None, real_clock: bool = False, **sections) -> Project:
        payload = defaults(**sections)
        project.manifest(payload)
        written = dict(FILES)
        written.update(files or {})
        for name, text in written.items():
            project.file(name, text)

        manifest = build_manifest(payload, root=str(project.directory))
        made = Project(manifest,
                       options=options if options is not None
                       else LoadOptions(progress="none"),
                       resources=ResourceTable(manifest))
        if not real_clock:
            made.clock = clock if clock is not None else FakeClock()
        return made

    return build


@pytest.fixture
def refuse(host_for):
    """The message a script gets when ``source`` is refused by a binding."""
    def cannot(source: str, **kwargs) -> str:
        made = host_for(**kwargs)
        program = Program("<bindings>", host=made.host_object())
        _, error = program.run(source)
        assert error is not None, f"'{source}' was expected to be refused"
        return error.as_string()

    return cannot


@pytest.fixture
def bare():
    """A program with no project behind it - a script typed into the REPL."""
    return Program("<bare>")


# -- the registry -------------------------------------------------------


def test_the_plan_named_exactly_these_project_bindings():
    assert BINDINGS == (
        "RES", "PRESET_LOAD", "PRESET_SAVE", "EXIT", "TIME", "FPS",
        "SLEEP", "LOG", "MODE",
    )
    for name in BINDINGS:
        assert name in REGISTRY, name
        assert REGISTRY[name].area == "project", name


@pytest.mark.parametrize("name,args,optional", [
    ("RES", ("name",), 0),
    ("PRESET_LOAD", ("name",), 0),
    ("PRESET_SAVE", ("name", "data"), 0),
    ("EXIT", ("code",), 1),
    ("TIME", (), 0),
    ("FPS", (), 0),
    ("SLEEP", ("seconds",), 0),
    ("LOG", ("message", "level"), 1),
    ("MODE", (), 0),
])
def test_every_project_binding_declares_its_arguments(name, args, optional):
    entry = REGISTRY[name]
    assert entry.arg_names == args
    assert entry.optional == optional


# -- RES ----------------------------------------------------------------


def test_a_file_answers_to_every_name_the_manifest_gives_it(host_for):
    made = host_for()

    assert on(made, 'GET(RES("wave"), "id")') == "wave"
    assert on(made, 'GET(RES("array.txt"), "id")') == "wave"
    assert on(made, 'GET(RES("assets/array.txt"), "declared")') == "assets/array.txt"
    assert on(made, 'GET(RES("knob_atlas"), "kind")') == "image"


def test_a_resource_answers_the_manifests_own_words(host_for):
    made = host_for()

    assert on(made, 'GET(RES("wave"), "kind")') == "array"
    assert on(made, 'GET(RES("wave"), "name")') == "array"
    assert on(made, 'GET(RES("wave"), "exists")') == 1
    assert str(value_of(made, 'RES("wave")')) == "array:wave"
    path = on(made, 'GET(RES("wave"), "path")')
    assert os.path.normpath(path) == os.path.normpath(
        os.path.join(str(made.directory), "assets", "array.txt"))


def test_an_array_answers_its_numbers(host_for):
    made = host_for()

    assert on(made, 'GET(RES("wave"), "values")') == [0.0, 0.25, 0.5, 1.0]
    assert on(made, 'GET(RES("wave"), "count")') == 4
    assert on(made, 'GET(RES("wave"), "format")') == "text"
    assert on(made, 'GET(RES("wave"), "patch")') == ""


def test_a_file_that_is_not_an_array_has_no_numbers_to_answer(host_for):
    """Not an error: a sound simply has no table, and the script can ask anyway."""
    made = host_for()

    assert on(made, 'GET(RES("knob_atlas"), "values")') == []
    assert on(made, 'GET(RES("knob_atlas"), "count")') == 0
    assert on(made, 'GET(RES("knob_atlas"), "format")') == ""


def test_the_numbers_are_read_once_and_kept(host_for):
    """The table parses an array file the first time and never again."""
    made = host_for()

    assert on(made, 'GET(RES("wave"), "values")') == [0.0, 0.25, 0.5, 1.0]
    with open(os.path.join(made.directory, "assets", "array.txt"), "w",
              encoding="utf-8") as handle:
        handle.write("9 9 9\n")

    assert on(made, 'GET(RES("wave"), "values")') == [0.0, 0.25, 0.5, 1.0]
    assert on(made, 'GET(RES("wave"), "count")') == 4


def test_the_size_is_the_file_and_a_missing_one_is_zero(host_for):
    made = host_for(files={},
                    resources={"images": ["assets/knob_atlas.png", "assets/gone.png"],
                               **ARRAYS})

    atlas = len(FILES["assets/knob_atlas.png"])
    assert on(made, 'GET(RES("knob_atlas"), "size")') == atlas
    assert on(made, 'GET(RES("gone"), "size")') == 0
    assert on(made, 'GET(RES("gone"), "exists")') == 0


def test_a_name_that_is_not_declared_lists_the_ones_that_are(refuse):
    error = refuse('RES("nope")')

    assert "RES: there is no resource 'nope' in this project" in error
    assert "declared: knob, knob_atlas, loop, wave" in error


def test_an_ambiguous_name_is_refused_rather_than_guessed(refuse):
    error = refuse(
        'RES("tick")',
        resources={"images": ["a/tick.png", "b/tick.png"], **ARRAYS})

    assert "'tick' is ambiguous" in error
    assert "ask for one by its full path" in error
    assert "a/tick.png, b/tick.png" in error


def test_a_resource_answers_only_what_the_manifest_knows(refuse):
    error = refuse('GET(RES("wave"), "loudness")')

    assert "GET: resource has no property 'loudness'" in error


def test_a_resource_cannot_be_written_to(refuse):
    error = refuse('SET(RES("wave"), "path", "elsewhere.txt")')

    assert "SET: resource has no writable properties (asked for 'path')" in error


def test_res_wants_the_name_of_a_file(refuse):
    error = refuse("RES(5)")

    assert "RES: argument 'name' must be a string, got number" in error


def test_the_properties_a_resource_answers_are_the_declared_ones():
    assert RESOURCE_PROPS == (
        "id", "kind", "name", "path", "declared", "exists", "size",
        "values", "count", "patch", "format",
    )


def test_a_resource_view_blames_the_manifest_for_nothing(host_for):
    """An unknown property is a ``KeyError``: that is how ``GET`` says "no such"."""
    made = host_for()
    view = ResourceView(made, made.resources.get("wave"))

    assert str(view) == view.resource.describe()
    with pytest.raises(KeyError):
        view.get_prop("colour")


def test_a_script_outside_a_project_says_so(bare):
    _, error = bare.run('RES("wave")')

    assert error is not None
    assert f"RES: {NO_PROJECT}" in error.as_string()


# -- presets ------------------------------------------------------------


def test_a_saved_pattern_comes_back_as_the_same_list(host_for):
    made = host_for()

    assert on(made, 'PRESET_LOAD("loop")') == [1, 2, 3]
    assert on(made, 'PRESET_SAVE("again", [4, 5, 6])') == "presets/again.json"
    assert on(made, 'PRESET_LOAD("again")') == [4, 5, 6]


def test_a_preset_lands_where_the_manifest_keeps_them(host_for):
    made = host_for()
    saved = on(made, 'PRESET_SAVE("pattern", [1, 2, 3])')

    assert saved == "presets/pattern.json"
    assert os.path.isfile(os.path.join(made.directory, "presets", "pattern.json"))
    with open(os.path.join(made.directory, "presets", "pattern.json"),
              encoding="utf-8") as handle:
        assert json.load(handle)["data"] == [1, 2, 3]
    assert "pattern" in made.resources.presets()


def test_a_preset_a_script_just_made_is_addressable_like_a_shipped_one(host_for):
    made = host_for()
    on(made, 'PRESET_SAVE("pattern", [1])')

    assert on(made, 'GET(RES("pattern"), "kind")') == "preset"
    assert on(made, 'GET(RES("pattern"), "exists")') == 1


def test_an_object_shaped_preset_is_a_bag_the_script_can_read(host_for):
    made = host_for()
    handle = value_of(made, 'PRESET_LOAD("knob")')

    assert isinstance(handle, HostHandle)
    assert handle.kind == "preset"
    assert str(handle) == "preset:knob"
    assert on(made, 'GET(PRESET_LOAD("knob"), "bpm")') == 140
    assert on(made, 'GET(PRESET_LOAD("knob"), "gain")') == 0.8


def test_a_loaded_preset_can_be_edited_and_saved_back(host_for):
    made = host_for()
    script = ('VAR k = PRESET_LOAD("knob")\n'
              'SET(k, "bpm", 150)\n'
              'PRESET_SAVE("tweaked", k)\n')
    _, error = Program("<bindings>", host=made.host_object()).run(script)
    assert error is None, error.as_string()

    with open(os.path.join(made.directory, "presets", "tweaked.json"),
              encoding="utf-8") as handle:
        assert json.load(handle)["data"] == {"bpm": 150, "gain": 0.8}
    assert on(made, 'GET(PRESET_LOAD("tweaked"), "bpm")') == 150


def test_a_preset_written_by_hand_without_the_wrapper_is_still_read(host_for):
    made = host_for(files={"presets/hand.json": "[7, 8, 9]\n"})

    assert on(made, 'PRESET_LOAD("hand")') == [7, 8, 9]


def test_a_preset_the_runtime_cannot_read_says_which_version_it_is(refuse):
    error = refuse('PRESET_LOAD("future")',
                   files={"presets/future.json": json.dumps(
                       {"crude_preset": 2, "id": "future", "data": [1]})})

    assert "is preset version 2, this runtime reads 1" in error


def test_a_preset_that_is_not_there_names_the_file(refuse):
    error = refuse('PRESET_LOAD("nope")')

    assert "PRESET_LOAD: preset 'nope' could not be read from" in error


def test_a_preset_that_is_not_json_is_the_files_fault(refuse):
    error = refuse('PRESET_LOAD("broken")', files={"presets/broken.json": "{not json"})

    assert "is not JSON" in error


def test_saving_creates_the_folder_a_project_never_declared_presets_in(host_for):
    made = host_for(resources={"images": ["assets/knob_atlas.png"], **ARRAYS})

    assert on(made, 'PRESET_SAVE("first", [1])') == "presets/first.json"
    assert os.path.isfile(os.path.join(made.directory, "presets", "first.json"))


def test_a_function_cannot_be_saved_in_a_preset(refuse):
    error = refuse('VAR f = FUNC(x) -> x\nPRESET_SAVE("bad", f)\n')

    assert "PRESET_SAVE: a Function cannot be saved in a preset" in error


def test_a_resource_handle_cannot_be_saved_in_a_preset(refuse):
    error = refuse('VAR r = RES("wave")\nPRESET_SAVE("bad", r)\n')

    assert "cannot be saved in a preset" in error
    assert "ResourceView" in error


def test_a_preset_needs_a_name_to_save_it_under(refuse):
    error = refuse('PRESET_SAVE(7, [1])')

    assert "argument 'name' must be a string, got number" in error


# -- the run -------------------------------------------------------------


def test_mode_names_the_mode_this_run_is_in(host_for):
    made = host_for()

    assert on(made, "MODE()") == "headless"
    assert on(host_for(options=LoadOptions(mode="terminal", progress="none")),
              "MODE()") == "terminal"


def test_log_puts_the_same_words_in_the_report_and_the_log(host_for):
    made = host_for()

    assert on(made, 'LOG("the deck is up")') == 1
    assert on(made, 'LOG("a level too loud", "warn")') == 1
    assert on(made, 'LOG("the patch is gone", "error")') == 1
    assert made.notes == ["the deck is up", "a level too loud", "the patch is gone"]


def test_a_debug_line_is_for_the_file_and_not_for_the_report(host_for):
    made = host_for()

    assert on(made, 'LOG("counted 3 blocks", "debug")') == 0
    assert made.notes == []


def test_a_log_line_reaches_the_projects_own_log_file(host_for):
    made = host_for()
    made.setup_logging("debug")
    try:
        assert on(made, 'LOG("written at debug", "debug")') == 0
        assert on(made, 'LOG("written as an error", "error")') == 1
    finally:
        made.close_logging()

    with open(made.log_path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert any("DEBUG" in line and "written at debug" in line for line in lines)
    assert any("ERROR" in line and "written as an error" in line for line in lines)
    assert not any("WARNING" in line for line in lines)


def test_nothing_to_say_is_not_said(host_for):
    made = host_for()

    assert on(made, 'LOG("")') == 0
    assert on(made, 'LOG("   ")') == 0
    assert made.notes == []


def test_a_level_that_is_not_a_level_lists_the_ones_that_are(refuse):
    error = refuse('LOG("hello", "shout")')

    assert "LOG: there is no log level 'shout' (known: debug, error, info, " \
           "warn, warning)" in error


def test_the_level_has_to_be_spelled_as_text(refuse):
    error = refuse('LOG("hello", 2)')

    assert "LOG: argument 'level' must be a string, got number" in error


def test_a_variable_named_level_is_not_every_logs_level(host_for):
    """The optional argument a call left out belongs to the binding, not the script.

    A ``.crude`` program that happens to keep a number in ``level`` used to fail
    on its next bare ``LOG``, because the omitted argument was looked up by name
    and found in the caller's scope. ``tests/lang/test_binding_args.py`` holds the
    rule; this is the binding the rule was found through.
    """
    made = host_for()

    assert on(made, 'VAR level = 3\nLOG("the deck is up")') == 1
    assert made.notes == ["the deck is up"]


def test_a_variable_named_code_is_not_every_exits_code(host_for):
    made = host_for()
    program = Program("<bindings>", host=made.host_object())

    with pytest.raises(ExitRequest) as caught:
        program.run("VAR code = 7\nEXIT()")

    assert caught.value.code == EXIT_OK


# -- ending the run ------------------------------------------------------


def test_exit_carries_the_code_the_script_chose(host_for):
    made = host_for()
    program = Program("<bindings>", host=made.host_object())

    with pytest.raises(ExitRequest) as caught:
        program.run("EXIT(7)")

    assert caught.value.code == 7


def test_an_exit_with_no_code_is_a_clean_end(host_for):
    made = host_for()
    program = Program("<bindings>", host=made.host_object())

    with pytest.raises(ExitRequest) as caught:
        program.run("EXIT()")

    assert caught.value.code == EXIT_OK


@pytest.mark.parametrize("source,expected", [
    ("EXIT(300)", "an exit code has to be 0..255, got 300"),
    ("EXIT(-1)", "an exit code has to be 0..255, got -1"),
    ("EXIT(1.5)", "argument 'code' must be a whole number, got 1.5"),
])
def test_an_exit_code_has_to_be_one_a_process_can_answer_with(refuse, source,
                                                              expected):
    assert expected in refuse(source)


def test_a_script_that_exits_ends_the_run_with_that_code(host_for):
    made = host_for()
    made.source = 'LOG("stopping on purpose")\nEXIT(3)\n'

    assert made.run_script() == 3
    assert made.exit_code == 3
    assert made.notes == ["stopping on purpose",
                          "main.crude asked to exit with code 3"]


def test_a_clean_exit_says_nothing_in_the_notes(host_for):
    made = host_for()
    made.source = "EXIT()\n"

    assert made.run_script() == EXIT_OK
    assert made.exit_code == EXIT_OK
    assert made.notes == []


def test_a_script_that_simply_finishes_keeps_the_value_it_answered_with(host_for):
    """Without ``EXIT`` the run answers what the script answered, and exits 0."""
    made = host_for()
    made.source = "41 + 1\n"

    assert as_py(made.run_script()) == 42
    assert made.exit_code == EXIT_OK


def test_a_script_that_raises_is_still_a_script_error(host_for):
    made = host_for()
    made.source = 'RES("nope")\n'

    with pytest.raises(ScriptError) as caught:
        made.run_script()

    assert "there is no resource 'nope'" in str(caught.value)


# -- time ----------------------------------------------------------------


def test_time_answers_the_clock_and_never_the_wall(host_for):
    made = host_for(clock=FakeClock())

    assert on(made, "TIME()") == 0.0
    made.clock.advance(1.5)
    assert on(made, "TIME()") == 1.5


def test_sleep_moves_the_clock_without_anything_waiting(host_for, monkeypatch):
    def never(delay):
        raise AssertionError(f"a project test waited {delay} seconds")

    monkeypatch.setattr("time.sleep", never)
    made = host_for(clock=FakeClock())

    assert on(made, "SLEEP(0.25)") == 0.25
    assert on(made, "TIME()") == 0.25
    assert made.clock.slept == 0.25


def test_sleep_refuses_to_go_backwards(refuse):
    error = refuse("SLEEP(-1)")

    assert "SLEEP: a delay cannot be negative (got -1)" in error


def test_sleep_wants_a_number_of_seconds(refuse):
    error = refuse('SLEEP("a while")')

    assert "SLEEP: argument 'seconds' must be a number, got string" in error


def test_fps_answers_the_rate_the_manifest_aimed_at(host_for):
    assert on(host_for(), "FPS()") == 24
    assert on(host_for(options=LoadOptions(fps=30, progress="none")), "FPS()") == 30


def test_the_frame_rate_prints_as_a_whole_number(host_for):
    """``FPS()`` is a decision, not a measurement, so it never reads ``24.0``."""
    made = host_for()

    assert isinstance(value_of(made, "FPS()"), Number)
    assert on(made, "STR(FPS())") == "24"
    assert on(host_for(options=LoadOptions(fps=30, progress="none")),
              "STR(FPS())") == "30"


def test_fps_without_a_project_falls_back_to_what_the_clock_aims_at():
    program = Program("<no project>",
                      host=types.SimpleNamespace(clock=FakeClock(target_fps=15)))
    value, error = program.run("FPS()")

    assert error is None, error.as_string()
    assert as_py(value) == 15


def test_the_clock_the_project_makes_is_the_clock_the_script_sees(host_for):
    made = host_for(real_clock=True)

    assert isinstance(made.clock, Clock)
    assert made.clock.live is True
    assert made.clock.target_fps == 24
    assert made.host_object().clock is made.clock
    # a run that has not begun has taken no time, whatever clock it uses
    assert on(made, "TIME()") == pytest.approx(0.0, abs=0.5)


def test_the_frame_rate_a_run_decides_on_is_the_one_the_clock_aims_at(host_for):
    made = host_for(clock=FakeClock(target_fps=15),
                    options=LoadOptions(fps=30, progress="none"))
    LoadPipeline(made)._phase_runtime_activate()

    assert made.fps == 30
    assert made.clock.target_fps == 30
    assert on(made, "FPS()") == 30
