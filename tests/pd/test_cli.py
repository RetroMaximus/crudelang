"""``crude pd``: the authoring stack, reachable from a shell.

These tests call :func:`crude.cli.main` with an argv list, so the argument
wiring is covered as well as the behaviour. Only the ``load`` action needs the
real library, and it is marked for it.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from crude.cli import COMMANDS, EXIT_LOAD, EXIT_OK, EXIT_SCRIPT, build_parser, main

TONE_SCRIPT = (
    'VAR p = PD_BUILD("synth")\n'
    'VAR osc = PD_OBJ(p, "osc~ 440")\n'
    'VAR dac = PD_OBJ(p, "dac~")\n'
    "PD_LINK(p, osc, dac)\n"
    "PD_LINK(p, osc, dac, 0, 1)\n"
    "PD_LOAD(p)\n"
)

OTHER_SCRIPT = (
    'VAR p = PD_BUILD("my patch")\n'
    'VAR osc = PD_OBJ(p, "osc~ 220")\n'
    'PD_TABLE(p, "wave", 8)\n'
    "PD_LOAD(p)\n"
)


@pytest.fixture
def write_script(tmp_path):
    """Put ``source`` in a ``.crude`` file and return its path."""

    def _write(source, name="synth.crude"):
        path = tmp_path / name
        path.write_text(source, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def out_dir(tmp_path):
    directory = tmp_path / "dumped"
    directory.mkdir()
    return directory


# -- registration -------------------------------------------------------


def test_the_pd_command_is_registered():
    build_parser()  # importing the command modules is what registers them

    assert "pd" in COMMANDS
    assert COMMANDS["pd"].__doc__


def test_the_pd_command_appears_in_the_help(capsys):
    assert main([]) == EXIT_OK
    out = capsys.readouterr().out

    assert "usage:" in out
    assert "pd" in out


def test_pd_without_an_action_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        main(["pd"])
    assert excinfo.value.code == 2


# -- show ---------------------------------------------------------------


def test_show_prints_the_boxes_and_the_links(patch_file, capsys):
    assert main(["pd", "show", str(patch_file)]) == EXIT_OK
    lines = capsys.readouterr().out.splitlines()

    assert lines[0] == "patch tone.pd: canvas 300x300 font 12"
    assert lines[1] == "6 boxes, 3 links"
    assert any("'osc~ 440'" in line for line in lines)
    assert any("1:0 -> 2:0" in line for line in lines)


def test_show_reports_what_the_reader_could_not_model(tmp_path, capsys):
    path = tmp_path / "odd.pd"
    path.write_text("#N canvas 0 0 10 10 12;\n#X declare -lib zsa.\n",
                    encoding="utf-8")

    assert main(["pd", "show", str(path)]) == EXIT_OK
    assert "warning: line 2: skipped '#X declare'" in capsys.readouterr().out


def test_show_of_a_file_that_is_not_there(patch_file, capsys):
    missing = str(patch_file.parent / "gone.pd")

    assert main(["pd", "show", missing]) == EXIT_LOAD
    assert "crude pd show" in capsys.readouterr().err


# -- dump ---------------------------------------------------------------


def test_dump_writes_every_authored_patch(write_script, out_dir, capsys):
    script = write_script(TONE_SCRIPT + "VAR q = PD_BUILD(\"other\")\n"
                                         'PD_OBJ(q, "osc~ 880")\n')

    assert main(["pd", "dump", str(script), "--out", str(out_dir)]) == EXIT_OK
    written = capsys.readouterr().out.splitlines()

    assert written == [str(out_dir / "synth.pd"), str(out_dir / "other.pd")]
    with open(out_dir / "synth.pd", "r", encoding="utf-8") as handle:
        text = handle.read()
    assert "#X obj 20 20 osc~ 440;" in text
    assert "#X connect 0 0 1 1;" in text


def test_dump_names_the_file_after_the_patch_id(write_script, out_dir):
    script = write_script(OTHER_SCRIPT)

    assert main(["pd", "dump", str(script), "--out", str(out_dir)]) == EXIT_OK
    assert (out_dir / "my-patch.pd").is_file()

    with open(out_dir / "my-patch.pd", "r", encoding="utf-8") as handle:
        assert "#X obj 160 20 table $0-wave 8;" in handle.read()


def test_dump_defaults_to_the_current_directory(write_script, out_dir, monkeypatch,
                                                capsys):
    script = write_script(TONE_SCRIPT)
    monkeypatch.chdir(out_dir)

    assert main(["pd", "dump", str(script)]) == EXIT_OK

    assert (out_dir / "synth.pd").is_file()
    assert "synth.pd" in capsys.readouterr().out


def test_dump_needs_no_libpd(write_script, out_dir, monkeypatch):
    """The patch text is decided before Pd is asked, so the fake is enough."""
    def refusing(*args, **kwargs):
        raise AssertionError("dump must not load the real library")

    script = write_script(TONE_SCRIPT)
    monkeypatch.setattr("crude.pd.lib.Libpd.load", refusing)

    assert main(["pd", "dump", str(script), "--out", str(out_dir)]) == EXIT_OK
    assert (out_dir / "synth.pd").is_file()


def test_dump_of_a_script_that_authors_nothing_says_so(write_script, out_dir, capsys):
    script = write_script('PRINT("no patches here")\n')

    assert main(["pd", "dump", str(script), "--out", str(out_dir)]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "no patches here" in printed
    assert "authors no patches" in printed
    assert list(out_dir.iterdir()) == []


def test_dump_reports_a_script_error(write_script, out_dir, capsys):
    script = write_script(TONE_SCRIPT + "\nPD_SEND(3, \"freq\", 1)\n")

    assert main(["pd", "dump", str(script), "--out", str(out_dir)]) == EXIT_SCRIPT
    assert "must be a patch" in capsys.readouterr().err
    assert list(out_dir.iterdir()) == []


def test_dump_reports_a_patch_that_cannot_be_written(write_script, out_dir, capsys):
    script = write_script(
        'VAR p = PD_BUILD("synth")\nPD_OBJ(p, "osc~ 440")\nPD_SAVE(p)\n'
        'VAR q = PD_BUILD("broken")\nPD_OBJ(q, "   ")\n'
    )

    assert main(["pd", "dump", str(script), "--out", str(out_dir)]) == EXIT_SCRIPT
    err = capsys.readouterr().err
    assert "patch 'broken'" in err
    assert "has no text" in err
    assert err.count("patch 'broken'") == 1, err
    assert (out_dir / "synth.pd").is_file()


def test_dump_of_a_missing_script(write_script, out_dir, capsys):
    assert main(["pd", "dump", "nope.crude", "--out", str(out_dir)]) == EXIT_LOAD
    assert "no script" in capsys.readouterr().err


# -- load ---------------------------------------------------------------


def test_load_reports_a_missing_file(patch_file, capsys):
    assert main(["pd", "load", str(patch_file.parent / "gone.pd")]) == EXIT_LOAD
    assert "no patch" in capsys.readouterr().err


@pytest.mark.requires_libpd
def test_load_measures_the_sound_of_a_patch(patch_file, capsys):
    assert main(["pd", "load", str(patch_file), "--blocks", "32"]) == EXIT_OK
    out = capsys.readouterr().out

    assert "tone.pd" in out
    assert "2048 frames" in out
    assert "rms 0." in out
    peak = float(out.rsplit("peak ", 1)[1].strip())
    assert peak > 0.01, "the patch should be audible"


# -- the module entry point ---------------------------------------------


def test_the_cli_runs_as_a_module(patch_file):
    completed = subprocess.run(
        [sys.executable, "-m", "crude", "pd", "show", str(patch_file)],
        capture_output=True, text=True, timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert "6 boxes, 3 links" in completed.stdout
