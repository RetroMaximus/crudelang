"""The four example projects, judged the way the plan judges them.

``check`` on all four lists the exact steps, a mistyped manifest key still costs
exit 2 with its pointer, and the build cache is reused on the second run. The
examples are copied into ``tmp_path`` first: a project writes its own log and
its own ``.crude_build`` where it lives, and the source tree is not the place
for either.

What each example can do *today* is part of the record too:

* ``hello_terminal`` and ``fm_synth`` run end to end (in-memory libpd, no
  device), so their assertions are about what they say and what they write;
* ``metronome`` is the plan's widget worked example and ``sensor_bridge`` the
  serial one, so both are checked as far as the stages that have landed: their
  manifests validate, their resources are all there, and their scripts parse.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict

import pytest

from crude.cli import EXIT_LOAD, EXIT_MANIFEST, EXIT_OK, main
from crude.pd import FakeLibpd
from crude.project import CHECK_PHASES, MANIFEST_NAME

#: the repository's ``examples/`` directory, whatever the tests are run from
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

NAMES = ("hello_terminal", "metronome", "fm_synth", "sensor_bridge")


@pytest.fixture(autouse=True)
def no_project_holds_a_log_open():
    """Let go of every project log, so a temporary directory can be removed."""
    yield
    for name in list(logging.root.manager.loggerDict):
        if not name.startswith("crude."):
            continue
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


@pytest.fixture
def copied(tmp_path):
    """Copy one example into ``tmp_path`` and return the copy's path."""
    def grab(name: str) -> str:
        target = tmp_path / name
        shutil.copytree(
            EXAMPLES / name, str(target),
            ignore=shutil.ignore_patterns(".crude_build", "logs"))
        return str(target)

    return grab


@pytest.fixture
def nolib(monkeypatch):
    """Give ``pd.core`` the in-memory library, so no DLL is ever looked for."""
    made = FakeLibpd()
    monkeypatch.setattr("crude.pd.lib.Libpd.load",
                        classmethod(lambda cls, *args, **kwargs: made))
    return made


def declared(name: str) -> Dict[str, Any]:
    """The manifest as it is shipped, before any default is filled in."""
    with open(str(EXAMPLES / name / MANIFEST_NAME), encoding="utf-8") as handle:
        return json.load(handle)


def cache_files(directory: str) -> list:
    """The generated patches a run left behind, by name."""
    built = os.path.join(directory, ".crude_build", "pd")
    return sorted(os.listdir(built)) if os.path.isdir(built) else []


# -- the four projects are the four the plan names ----------------------


def test_the_examples_the_plan_named_are_here():
    for name in NAMES:
        assert (EXAMPLES / name / MANIFEST_NAME).is_file(), name
        assert (EXAMPLES / name / "main.crude").is_file(), name


def test_the_project_id_is_the_name_the_directory_is_known_by():
    # the id also names the log file and the build cache, so it is worth pinning
    assert {name: declared(name)["id"] for name in NAMES} == {
        "hello_terminal": "hello-terminal",
        "metronome": "metronome",
        "fm_synth": "fm-synth",
        "sensor_bridge": "sensor-bridge",
    }


# -- check: the acceptance line, on every one of them -------------------


@pytest.mark.parametrize("name", NAMES)
def test_check_lists_the_exact_steps(copied, name, capsys):
    path = copied(name)

    assert main(["check", path]) == EXIT_OK

    out, _ = capsys.readouterr()
    for number, step in enumerate(CHECK_PHASES, start=1):
        assert f"{number}. {step}" in out, (name, step)
    assert "[100%] script.compile     ok   the entry script" in out
    assert "loaded 4 of 4 steps (100%)" in out
    # a check opens no device, no library and no window, so it never lists them
    assert "audio.device" not in out
    assert "pd.core" not in out


@pytest.mark.parametrize("name", NAMES)
def test_a_check_parses_the_entry_script_and_stops_there(copied, name, capsys):
    path = copied(name)

    assert main(["check", path]) == EXIT_OK

    out, _ = capsys.readouterr()
    assert "main.crude:" in out
    assert "statement(s) parsed" in out
    # the script did not run, so nothing it says and nothing it could trip on
    assert "Runtime Error" not in out


@pytest.mark.parametrize("name", NAMES)
def test_every_example_check_is_a_machine_readable_plan(copied, name, capsys):
    path = copied(name)

    assert main(["check", path, "--json"]) == EXIT_OK

    documents = [json.loads(line) for line in
                 capsys.readouterr().out.splitlines() if line.strip()]
    assert documents[0]["event"] == "start"
    assert [step["name"] for step in documents[0]["steps"]] == list(CHECK_PHASES)
    assert documents[-2]["event"] == "finish"
    assert documents[-2]["ok"] is True, documents[-2]
    assert documents[-1]["event"] == "project"
    assert documents[-1]["project"]["mode"] == declared(name)["runtime"]["mode"]


def test_an_example_with_a_typo_in_its_manifest_exits_two_with_a_pointer(copied,
                                                                        capsys):
    path = copied("hello_terminal")
    where = os.path.join(path, MANIFEST_NAME)
    with open(where, encoding="utf-8") as handle:
        data = json.load(handle)
    data["runtime"]["target_flps"] = 20
    with open(where, "w", encoding="utf-8") as handle:
        json.dump(data, handle)

    assert main(["check", path]) == EXIT_MANIFEST

    _, err = capsys.readouterr()
    assert "runtime.target_flps" in err
    assert "unexpected key 'target_flps'" in err


# -- what each example says about itself -------------------------------


def test_hello_terminal_asks_for_nothing_it_cannot_get(copied, capsys):
    assert main(["check", copied("hello_terminal")]) == EXIT_OK

    out, _ = capsys.readouterr()
    assert "mode terminal at 20 fps" in out
    assert "audio off for this run" in out
    assert "no patches declared" in out
    assert "the project declares no files" in out


def test_fm_synth_ships_the_numbers_and_the_patterns_it_sends(copied, capsys):
    assert main(["check", copied("fm_synth")]) == EXIT_OK

    out, _ = capsys.readouterr()
    assert "resources 0 image, 0 sound, 1 array, 2 preset" in out
    assert "3 declared file(s) are there" in out


def test_sensor_bridge_declares_the_port_and_the_patch_it_arrives_in(copied,
                                                                    capsys):
    assert main(["check", copied("sensor_bridge")]) == EXIT_OK

    out, _ = capsys.readouterr()
    assert "1 patch(es), 0 authored" in out
    assert "1 serial link(s) declared" in out
    assert "mode headless at 50 fps" in out


def test_the_metronome_manifest_is_the_plans_worked_example():
    manifest = declared("metronome")

    assert manifest["runtime"] == {"mode": "window", "width": 360, "height": 220,
                                   "target_fps": 60, "vsync": True}
    assert manifest["audio"]["sample_rate"] == 44100
    assert manifest["audio"]["block_size"] == 64
    assert manifest["audio"]["out_channels"] == 2
    assert manifest["ui"] == {"theme": "dark", "icons": "generated"}


def test_the_sensor_patch_is_a_file_pd_could_open():
    # the manifest declares it, so the loader opens it: it has to be real Pd text
    text = (EXAMPLES / "sensor_bridge" / "pd" / "sensors.pd").read_text(
        encoding="utf-8")

    assert text.startswith("#N canvas ")
    assert "#X obj 20 30 r level;" in text
    assert "#X connect 0 0 1 0;" in text


# -- and what the two finished ones do ---------------------------------


def test_hello_terminal_runs_and_speaks_for_itself(copied, nolib, capsys):
    path = copied("hello_terminal")

    assert main(["run", path, "--progress", "none"]) == EXIT_OK

    out, _ = capsys.readouterr()
    said = out.splitlines()
    assert said[:5] == [
        "hello from a crude project",
        "this run is terminal at 20 fps",
        "1: the script owns its own output",
        "2: the script owns its own output",
        "3: the script owns its own output",
    ]
    # TIME() is the last word, and it is a number of seconds, whatever the clock
    assert said[-1].startswith("the run has taken ")


def test_fm_synth_authors_its_patch_and_fills_its_table(copied, nolib, capsys):
    path = copied("fm_synth")

    assert main(["run", path, "--mode", "headless", "--no-audio",
                 "--progress", "none", "--frames", "0"]) == EXIT_OK

    out, _ = capsys.readouterr()
    assert "modindex: 8 number(s) parsed" in out
    assert "is this patch's $0" in out
    assert "built fm-" in out

    # the numbers the manifest's array file holds are in the table the patch made
    written = [values for name, values in nolib.arrays.items()
               if name.endswith("-modindex")]
    assert written == [[1.0, 0.707, 0.0, -0.707, -1.0, -0.707, 0.0, 0.707]]


def test_the_build_cache_is_written_once_and_reused_after(copied, nolib, capsys):
    path = copied("fm_synth")
    run = ["run", path, "--mode", "headless", "--no-audio",
           "--progress", "none", "--frames", "0"]

    assert main(run) == EXIT_OK
    first = cache_files(path)
    assert len(first) == 1 and first[0].startswith("fm-")

    assert main(run) == EXIT_OK
    capsys.readouterr()

    # the second run wrote nothing: same text, same hash, same file
    assert cache_files(path) == first
    assert "built fm-" not in capsys.readouterr().out


@pytest.mark.parametrize("level,lines,keeps_debug", [
    (None, 0, False),      # the default is warning: the file keeps what went wrong
    ("info", 6, False),    # one line per LOG the script said out loud
    ("debug", 7, True),    # plus the line only the log ever sees
])
def test_a_run_leaves_its_log_behind_and_the_level_decides_how_loud_it_is(
        copied, nolib, level, lines, keeps_debug):
    path = copied("hello_terminal")
    run = ["run", path, "--progress", "none"]
    if level:
        run += ["--log", level]

    assert main(run) == EXIT_OK

    with open(os.path.join(path, "logs", "hello-terminal.log"),
              encoding="utf-8") as handle:
        kept = [line for line in handle.read().splitlines() if line.strip()]
    assert len(kept) == lines
    assert ("counted three times" in "\n".join(kept)) is keeps_debug
    if kept:
        # what LOG says goes to the terminal *and* to the project's own log, in
        # the order the script said it, and the debug line is filed between them
        assert "INFO" in kept[0] and "hello from a crude project" in kept[0]
        assert "the run has taken" in kept[-1]


@pytest.mark.parametrize("name", NAMES)
def test_the_copy_is_the_project_that_writes(copied, name):
    path = copied(name)

    assert main(["check", path, "--json"]) == EXIT_OK

    # everything the phases create is beside the copy: the build cache the
    # patches go into, and the log the environment phase opened
    assert os.path.isdir(os.path.join(path, ".crude_build", "pd"))
    assert os.path.isfile(os.path.join(path, "logs", f"{declared(name)['id']}.log"))


def test_a_missing_declared_file_is_a_load_error_not_a_manifest_one(copied):
    path = copied("fm_synth")
    os.remove(os.path.join(path, "assets", "modindex.txt"))

    assert main(["check", path]) == EXIT_LOAD
