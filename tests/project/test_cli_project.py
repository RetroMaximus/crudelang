"""``crude run``, ``crude check`` and ``crude init``, as a shell types them.

The commands are deliberately thin: everything they decide belongs to the loader
and the manifest, so what is tested here is the *surface* - which flag reaches
which :class:`~crude.project.LoadOptions` field, what lands on stdout versus
stderr, and which exit code comes out of which class of failure. ``check`` needs
no hardware at all, and ``run`` gets the in-memory library instead of a DLL.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

import pytest

from crude.cli import COMMANDS, EXIT_MANIFEST, EXIT_OK, build_parser, main
from crude.commands.project import LOG_NAMES, _options
from crude.errors import EXIT_LOAD, EXIT_SCRIPT
from crude.pd import FakeLibpd
from crude.project import (
    CHECK_PHASES,
    DEFAULT_SCHEMA,
    LOG_LEVELS,
    MANIFEST_NAME,
    LoadOptions,
)
from crude.project.manifest import TOP_LEVEL_KEYS

#: the cheapest project that loads: no window, no device, nothing declared
STARTER: Dict[str, Any] = {
    "id": "deck",
    "entry": "main.crude",
    "runtime": {"mode": "terminal", "target_fps": 60},
    "audio": {"enabled": False},
}

QUIET = 'LOG("the deck is up")\n'
BOOM = 'LOG("up")\nPRINT(RES("absent"))\n'
EXITED = 'LOG("stopping")\nEXIT(7)\n'
BROKEN = 'LOG("unclosed\n'

AUTHORED = {"patches": [{"id": "synth",
                         "authored": {"width": 300, "height": 200}}]}


@pytest.fixture(autouse=True)
def no_project_holds_a_log_open():
    """Let go of every project log, so a temporary directory can be removed.

    The CLI opens the log through the ``environment`` phase and closes it again
    on the way out; a test that asserts on that close has to be able to tell the
    difference between *this* project's handler and one it inherited.
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
def deck(project):
    """Write a project directory and return its path, one section at a time."""
    def build(source: str = QUIET, **sections) -> str:
        data = dict(STARTER)
        for key, value in sections.items():
            if value is not None:
                data[key] = value
        project.manifest(data)
        project.file("main.crude", source)
        return str(project.directory)

    return build


@pytest.fixture
def nolib(monkeypatch):
    """Give ``pd.core`` the in-memory library, so no DLL is ever looked for."""
    made = FakeLibpd()
    monkeypatch.setattr("crude.pd.lib.Libpd.load",
                        classmethod(lambda cls, *args, **kwargs: made))
    return made


def lines(capsys):
    """``(stdout, stderr)``, so a test can say which stream a word went to."""
    captured = capsys.readouterr()
    return captured.out, captured.err


def events(text: str):
    """The JSONL documents on a stream - how a machine reads ``--json``."""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# -- registration --------------------------------------------------------


def test_the_project_commands_are_registered():
    build_parser()  # importing the command modules is what registers them

    for name in ("run", "check", "init"):
        assert name in COMMANDS, name
        assert COMMANDS[name].__doc__, name


def test_only_the_stages_that_finished_have_a_command():
    build_parser()

    assert sorted(COMMANDS) == ["check", "glyphs", "init", "pd", "run",
                                "widgets"]


def test_the_project_commands_appear_in_the_help(capsys):
    assert main([]) == EXIT_OK

    out, _ = lines(capsys)
    assert "run" in out and "check" in out and "init" in out


# -- the flags, and what they become ------------------------------------


def test_the_run_flags_become_the_options_the_pipeline_reads():
    args = build_parser().parse_args([
        "run", "deck", "--mode", "headless", "--no-audio", "--backend", "null",
        "--no-serial", "--progress", "json", "--fps", "30", "--log", "info",
        "--rebuild-pd",
    ])

    assert _options(args) == LoadOptions(
        mode="headless", audio=False, backend="null", serial=False,
        progress="json", fps=30, log="info", rebuild_pd=True,
    )


def test_a_run_that_says_nothing_leaves_every_choice_to_the_manifest():
    args = build_parser().parse_args(["run"])

    assert args.project == "."
    assert _options(args) == LoadOptions()


def test_audio_can_be_asked_for_as_well_as_refused():
    assert build_parser().parse_args(["run", "--audio"]).audio is True
    assert build_parser().parse_args(["run", "--no-audio"]).audio is False
    assert build_parser().parse_args(["run"]).audio is None


def test_the_log_levels_are_the_ones_the_loader_can_answer_with():
    assert set(LOG_NAMES) == set(LOG_LEVELS)


def test_an_unknown_mode_is_refused_before_the_project_is_read(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["run", ".", "--mode", "garden"])

    assert caught.value.code == EXIT_MANIFEST
    _, err = lines(capsys)
    assert "invalid choice: 'garden'" in err


def test_a_frame_rate_of_zero_is_refused_rather_than_ignored(capsys):
    """``--fps 0`` would read as "not said" and quietly keep the manifest's rate."""
    with pytest.raises(SystemExit) as caught:
        main(["run", ".", "--fps", "0"])

    assert caught.value.code == EXIT_MANIFEST
    _, err = lines(capsys)
    assert "is not a frame rate" in err


# -- check ---------------------------------------------------------------


def test_check_lists_the_steps_a_run_would_take_first(deck, capsys):
    assert main(["check", deck()]) == EXIT_OK

    out, _ = lines(capsys)
    assert "deck (terminal) - 4 steps, 15 weight" in out
    assert "1. manifest" in out
    assert "4. script.compile" in out
    assert "[100%] script.compile     ok   the entry script" in out
    assert "loaded 4 of 4 steps (100%)" in out
    # the steps that would have opened something are not in a check at all
    assert "audio.device" not in out
    assert "pd.core" not in out


def test_check_ends_with_the_project_as_the_manifest_declares_it(deck, capsys):
    assert main(["check", deck()]) == EXIT_OK

    out, _ = lines(capsys)
    assert "project   deck 'deck'" in out
    assert "mode      terminal at 60 fps" in out
    assert "audio     off" in out
    assert "resources 0 image, 0 sound, 0 array, 0 preset" in out


def test_check_reads_the_entry_script_without_running_it(deck, capsys):
    assert main(["check", deck()]) == EXIT_OK

    out, _ = lines(capsys)
    assert "main.crude: 1 line(s), 1 statement(s) parsed" in out
    assert "the deck is up" not in out


def test_a_check_never_asks_for_the_library_or_a_device(deck, monkeypatch):
    def refusing(*args, **kwargs):
        raise AssertionError("check must not open anything")

    monkeypatch.setattr("crude.project.loader.LoadPipeline._phase_audio_device",
                        refusing)
    monkeypatch.setattr("crude.project.loader.LoadPipeline._phase_pd_core",
                        refusing)

    assert main(["check", deck()]) == EXIT_OK


def test_a_project_that_is_not_there_is_a_manifest_error(tmp_path, capsys):
    missing = str(tmp_path / "nope")

    assert main(["check", missing]) == EXIT_MANIFEST

    _, err = lines(capsys)
    assert f"there is no {MANIFEST_NAME} in '{missing}'" in err
    assert "is this a crude project?" in err


def test_a_bad_manifest_exits_two_and_points_at_the_key(project, capsys):
    project.manifest(raw=json.dumps({
        "id": "deck",
        "runtime": {"mode": "terminal", "target_flps": 60},
    }))
    project.file("main.crude", QUIET)

    assert main(["check", str(project.directory)]) == EXIT_MANIFEST

    _, err = lines(capsys)
    assert "crude check: runtime.target_flps" in err
    assert "unexpected key 'target_flps'" in err
    assert MANIFEST_NAME in err


def test_a_declared_file_that_is_not_there_fails_the_resource_phase(deck, capsys):
    path = deck(resources={"images": ["assets/gone.png"]})

    assert main(["check", path]) == EXIT_LOAD

    out, err = lines(capsys)
    assert "resources.validate FAIL" in out
    assert "failed at resources.validate after 2 of 4 steps" in out
    assert "assets/gone.png" in err


def test_a_script_that_does_not_compile_is_a_script_error(deck, capsys):
    path = deck(source=BROKEN)

    assert main(["check", path]) == EXIT_SCRIPT

    out, err = lines(capsys)
    assert "failed at script.compile after 3 of 4 steps" in out
    assert "crude check: main.crude:" in err
    assert "Invalid Syntax" in err


def test_json_check_is_a_stream_another_program_can_read(deck, capsys):
    assert main(["check", deck(), "--json"]) == EXIT_OK

    documents = events(lines(capsys)[0])
    assert documents[0]["event"] == "start"
    assert [step["name"] for step in documents[0]["steps"]] == list(CHECK_PHASES)
    assert documents[-2]["event"] == "finish"
    assert documents[-2]["ok"] is True
    assert documents[-1]["event"] == "project"
    assert documents[-1]["project"]["id"] == "deck"
    assert documents[-1]["project"]["resources"] == {
        "image": 0, "sound": 0, "array": 0, "preset": 0}


def test_a_project_the_command_line_does_not_name_is_the_current_directory(
        deck, monkeypatch, capsys):
    monkeypatch.chdir(deck())

    assert main(["check", "--json"]) == EXIT_OK

    assert events(lines(capsys)[0])[0]["label"] == "deck"


# -- run -----------------------------------------------------------------


def test_run_takes_the_project_through_every_phase_and_back_out(deck, nolib,
                                                                capsys):
    assert main(["run", deck()]) == EXIT_OK

    out, _ = lines(capsys)
    assert "loaded 14 of 14 steps (100%)" in out
    assert "[100%] runtime.activate" in out
    assert "the deck is up" in out


def test_the_script_speaks_once_and_the_phases_do_not_repeat_it(deck, nolib,
                                                                capsys):
    assert main(["run", deck()]) == EXIT_OK

    assert lines(capsys)[0].count("the deck is up") == 1


def test_run_answers_the_code_the_script_asked_for(deck, nolib, capsys):
    assert main(["run", deck(source=EXITED), "--progress", "none"]) == 7

    out, _ = lines(capsys)
    assert out.splitlines() == ["stopping",
                                "main.crude asked to exit with code 7"]


def test_a_script_that_raises_is_exit_four(deck, nolib, capsys):
    assert main(["run", deck(source=BOOM), "--progress", "none"]) == EXIT_SCRIPT

    out, err = lines(capsys)
    assert out == "up\n"
    assert "crude run: main.crude:" in err
    assert "there is no resource 'absent'" in err


def test_a_project_that_cannot_come_up_is_exit_three(deck, nolib, capsys):
    path = deck(resources={"images": ["assets/gone.png"]})

    assert main(["run", path, "--progress", "none"]) == EXIT_LOAD

    _, err = lines(capsys)
    assert "crude run:" in err
    assert "assets/gone.png" in err


def test_a_failed_load_leaves_no_log_open(deck, nolib):
    path = deck(resources={"images": ["assets/gone.png"]})
    assert main(["run", path, "--progress", "none"]) == EXIT_LOAD

    assert logging.getLogger("crude.deck").handlers == []


def test_no_audio_is_an_answer_even_for_a_project_that_wants_a_stream(
        deck, nolib, capsys):
    path = deck(audio={"enabled": True, "backend": "null", "optional": True},
                runtime={"mode": "headless", "target_fps": 30})

    assert main(["run", path, "--no-audio", "--frames", "0"]) == EXIT_OK

    assert "audio is off for this run; no device was opened" in lines(capsys)[0]


def test_a_headless_project_gets_a_stream_it_never_starts(deck, nolib, capsys):
    path = deck(audio={"enabled": True, "backend": "null", "optional": True},
                runtime={"mode": "headless", "target_fps": 30})

    assert main(["run", path, "--frames", "0"]) == EXIT_OK

    out = lines(capsys)[0]
    assert "null" in out
    assert "open, not started" in out


def test_the_frame_rate_the_run_aims_at_reaches_the_script(deck, nolib, capsys):
    path = deck(source='LOG("fps " + STR(FPS()))\n')

    assert main(["run", path, "--fps", "30", "--progress", "none"]) == EXIT_OK

    assert lines(capsys)[0] == "fps 30\n"


def test_stats_says_what_the_clock_measured_when_the_script_ended(deck, nolib,
                                                                 capsys):
    assert main(["run", deck(), "--progress", "none", "--stats"]) == EXIT_OK

    out = lines(capsys)[0]
    assert "stats     Clock" in out
    assert "aiming at 60 fps" in out


def test_mode_tells_the_script_which_run_this_is(deck, nolib, capsys):
    path = deck(source="LOG(MODE())\n")

    assert main(["run", path, "--mode", "headless", "--progress", "none",
                 "--frames", "0"]) == EXIT_OK

    assert lines(capsys)[0] == "headless\n"


def test_a_window_run_with_no_display_downgrades_with_a_message(
        deck, nolib, capsys, monkeypatch):
    # S11: the frame loop is here, so a window run now reaches for a display
    # and, when the machine cannot give one, is auto-downgraded with a reason
    monkeypatch.setattr(
        "crude.commands.project._open_display",
        lambda project: (None, "there is no display to open a window on"))

    assert main(["run", deck(), "--mode", "window",
                 "--progress", "none", "--frames", "0"]) == EXIT_OK

    _, err = lines(capsys)
    assert "mode 'window' is not possible here" in err
    assert "running 'headless' instead" in err
    assert "no frame loop yet" not in err


def test_splash_progress_comes_out_of_the_terminal_with_a_word(deck, nolib,
                                                               capsys,
                                                               monkeypatch):
    # a window asked for by name, on a machine that cannot have one: the load
    # carries on in words, because losing the pretty half is no reason to lose it
    monkeypatch.setattr(
        "crude.commands.project._open_display",
        lambda project: (None, "there is no display to open a window on"))

    assert main(["run", deck(), "--progress", "splash",
                 "--frames", "0"]) == EXIT_OK

    out, err = lines(capsys)
    assert "there is no display to open a window on" in err
    assert "loaded 14 of 14 steps" in out


def test_json_progress_turns_what_the_script_said_into_events(deck, nolib,
                                                              capsys):
    path = deck(source='LOG("the deck is up")\nLOG("counted 3", "debug")\n')

    assert main(["run", path, "--progress", "json"]) == EXIT_OK

    documents = events(lines(capsys)[0])
    assert [doc for doc in documents if doc["event"] == "note"] == [
        {"event": "note", "text": "the deck is up"}]
    assert documents[-1]["event"] == "note"
    assert [doc for doc in documents if doc["event"] == "finish"][0]["ok"] is True


def test_the_log_file_holds_what_the_script_wrote_at_debug(deck, nolib, capsys):
    path = deck(source='LOG("counted 3 blocks", "debug")\n')

    assert main(["run", path, "--progress", "none", "--log", "debug"]) == EXIT_OK

    assert lines(capsys)[0] == ""
    with open(os.path.join(path, "logs", "deck.log"), encoding="utf-8") as handle:
        text = handle.read()
    assert "counted 3 blocks" in text
    assert "DEBUG" in text


def test_rebuild_pd_reaches_the_cache_the_build_phase_writes_into(deck, nolib,
                                                                  capsys):
    path = deck(pd=AUTHORED)

    assert main(["run", path, "--progress", "none"]) == EXIT_OK
    assert main(["run", path, "--rebuild-pd"]) == EXIT_OK

    out, _ = lines(capsys)
    assert "cache .crude_build/pd, rebuilding" in out
    assert "1 built, 0 reused" in out


# -- init ----------------------------------------------------------------


def test_init_writes_a_project_that_checks_clean_the_next_second(tmp_path,
                                                                 capsys):
    directory = str(tmp_path / "hello")

    assert main(["init", "hello", "--dir", str(tmp_path)]) == EXIT_OK
    assert f"next: python -m crude check {directory}" in lines(capsys)[0]
    assert main(["check", directory]) == EXIT_OK


def test_the_written_manifest_is_the_one_the_validator_read(tmp_path):
    assert main(["init", "hello", "--dir", str(tmp_path)]) == EXIT_OK

    with open(tmp_path / "hello" / MANIFEST_NAME, encoding="utf-8") as handle:
        data = json.load(handle)

    assert set(data) == set(TOP_LEVEL_KEYS)
    assert data["$schema"] == DEFAULT_SCHEMA
    assert data["id"] == data["name"] == "hello"
    assert data["entry"] == "main.crude"
    assert data["runtime"]["mode"] == "terminal"
    assert data["audio"]["enabled"] is True


def test_the_mode_init_starts_in_is_the_one_it_writes(tmp_path):
    assert main(["init", "wide", "--dir", str(tmp_path),
                 "--mode", "window"]) == EXIT_OK

    with open(tmp_path / "wide" / MANIFEST_NAME, encoding="utf-8") as handle:
        assert json.load(handle)["runtime"]["mode"] == "window"


def test_the_starter_script_runs_like_any_other_project(tmp_path, nolib, capsys):
    assert main(["init", "hello", "--dir", str(tmp_path)]) == EXIT_OK
    capsys.readouterr()

    assert main(["run", str(tmp_path / "hello"), "--progress", "none"]) == EXIT_OK

    assert lines(capsys)[0].splitlines() == [
        "hello from hello, running in terminal mode",
        "tick 1", "tick 2", "tick 3", "tick 4",
    ]


def test_init_refuses_to_write_over_a_project_it_did_not_ask_for(tmp_path,
                                                                 capsys):
    assert main(["init", "hello", "--dir", str(tmp_path)]) == EXIT_OK
    capsys.readouterr()

    assert main(["init", "hello", "--dir", str(tmp_path)]) == EXIT_MANIFEST
    assert "--force" in lines(capsys)[1]

    assert main(["init", "hello", "--dir", str(tmp_path), "--force"]) == EXIT_OK


def test_init_will_not_overwrite_a_script_it_was_not_asked_to_touch(tmp_path,
                                                                    capsys):
    directory = tmp_path / "hello"
    directory.mkdir()
    (directory / "main.crude").write_text('LOG("mine")\n', encoding="utf-8")

    assert main(["init", "hello", "--dir", str(tmp_path)]) == EXIT_MANIFEST

    _, err = lines(capsys)
    assert "main.crude" in err
    assert (directory / "main.crude").read_text(encoding="utf-8") == 'LOG("mine")\n'
    assert not (directory / MANIFEST_NAME).exists()


def test_a_name_that_cannot_be_an_id_is_a_manifest_error(tmp_path, capsys):
    assert main(["init", "my project", "--dir", str(tmp_path)]) == EXIT_MANIFEST

    _, err = lines(capsys)
    assert "cannot be used as a directory name" in err
    assert "id" in err
