"""``crude widgets``: the schema, printed, with no window and no library.

Every assertion here is about the one rule the command exists for: what it prints
is the table the bindings validate against, not a description of it. So the
expected values are read out of :mod:`crude.ui.props` and :mod:`crude.ui.skin`
rather than written down twice - a test that carried its own copy of the schema
would pass happily the day the two disagreed, which is the failure this command is
meant to make impossible.

The project half is written here rather than borrowed from ``tests/project``: the
fixture that builds a manifest lives in that directory's ``conftest`` and these
tests are in this one, and a CLI test that cannot write its own two files is a CLI
test that has to be run from the other half of the repository.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, NamedTuple, Tuple

import pytest

from crude.cli import (EXIT_LOAD, EXIT_MANIFEST, EXIT_OK, EXIT_SCRIPT, COMMANDS,
                       build_parser, main)
from crude.ui import (KINDS, SCHEMA, Skin, catalog_names, document, get_kind,
                      icon_names, reset)

#: a project's own kind: a slider with one more number on it
DEFINE = (
    'WIDGET_DEFINE("bpm", "slider")\n'
    'PROP_DEFINE("bpm", "beats", "number", 4, 1, 16)\n'
    'VAR w = WIDGET("bpm")\n'
    "PLACE(w, 10, 10, 200, 30)\n"
    'LOG("bpm is defined")\n'
)

#: the same kind, with no project around it
STANDALONE = (
    'WIDGET_DEFINE("stepper")\n'
    'PROP_DEFINE("stepper", "value", "number", 0, 0, 100)\n'
    'VAR w = WIDGET("stepper")\n'
    "PLACE(w, 10, 10, 80, 40)\n"
)

BAD_WIDGET = 'VAR w = WIDGET("no-such-kind")\n'


class Output(NamedTuple):
    """What one command line ended with: its code and its two streams.

    Read once per run, because :meth:`pytest.CaptureFixture.readouterr` takes the
    buffer rather than peeking at it - a test that asked twice would find the
    second answer empty and start asserting against nothing.
    """

    code: int
    out: str
    err: str

    def lines(self) -> Tuple[List[str], List[str]]:
        return self.out.splitlines(), self.err.splitlines()

    def json(self) -> Dict[str, Any]:
        """The stdout, as the one document it is supposed to be."""
        return json.loads(self.out)


@pytest.fixture
def run(capsys):
    """``run([...])``: the code, and everything the command printed."""
    def go(argv: List[str]) -> Output:
        code = main(argv)
        captured = capsys.readouterr()
        return Output(code, captured.out, captured.err)

    return go


@pytest.fixture(autouse=True)
def clean_kinds():
    """Script kinds are process-global, so each test starts with the catalog."""
    reset()
    yield
    reset()


@pytest.fixture
def project(tmp_path):
    """Write a manifest and its entry script; hand back the directory."""
    def make(source: str = DEFINE, *, ui: Dict[str, Any] = None,
             entry: str = "main.crude", raw: str = None) -> str:
        directory = tmp_path / "deck"
        directory.mkdir(exist_ok=True)
        payload: Dict[str, Any] = {
            "id": "deck",
            "entry": entry,
            "runtime": {"mode": "terminal", "width": 400, "height": 240},
        }
        if ui is not None:
            payload["ui"] = ui
        target = directory / "crude.manifest.json"
        target.write_text(raw if raw is not None else json.dumps(payload),
                          encoding="utf-8")
        if source is not None:
            (directory / entry).write_text(source, encoding="utf-8")
        return str(directory)

    return make


@pytest.fixture
def script(tmp_path):
    """Write one loose script and hand back its path."""
    def write(source: str, name: str = "loose.crude") -> str:
        path = tmp_path / name
        path.write_text(source, encoding="utf-8")
        return str(path)

    return write


# -- registration --------------------------------------------------------


def test_the_schema_commands_are_registered():
    build_parser()

    for name in ("widgets", "glyphs"):
        assert name in COMMANDS, name
        assert COMMANDS[name].__doc__, name


# -- the listing ---------------------------------------------------------


def test_the_listing_names_every_kind_and_what_it_twins_to(run):
    result = run(["widgets"])
    out, _ = result.lines()

    assert result.code == EXIT_OK
    assert out[0].startswith(f"{len(KINDS)} kind(s), 0 of them defined")
    for name in catalog_names():
        assert any(name in line for line in out), name
    assert any("[hslider]" in line for line in out)
    assert "a '*' is a kind the program defined" in out[-1]


def test_the_listing_prints_the_hint_the_tooltips_are_read_from(run):
    result = run(["widgets"])
    out, _ = result.lines()

    assert result.code == EXIT_OK
    hint = get_kind("slider").hint
    assert any(hint in line for line in out), hint


# -- the document --------------------------------------------------------


def test_the_json_is_the_document_the_bindings_validate_against(run):
    result = run(["widgets", "--json"])

    assert result.code == EXIT_OK
    said = result.json()
    assert said["schema"] == SCHEMA
    assert [kind["kind"] for kind in said["kinds"]] == list(catalog_names())
    # the same table :func:`crude.ui.document` builds, row for row
    assert said["kinds"] == json.loads(json.dumps(document()["kinds"],
                                                 default=str))


def test_the_json_carries_the_skin_the_colours_are_read_from(run):
    result = run(["widgets", "--json"])

    assert result.code == EXIT_OK
    said = result.json()
    skin = Skin()
    assert said["theme"] == skin.theme.name
    assert said["scale"] == skin.scale_name
    assert said["digest"] == skin.digest
    assert said["tokens"] == skin.tokens()
    assert said["metrics"] == skin.metrics()
    assert said["glyphs"] == list(icon_names())
    assert said["source"] is None


def test_a_theme_beats_the_one_the_project_declares(run, project):
    result = run(["widgets", "--project", project(ui={"theme": "light"}),
                  "--theme", "flat", "--json"])

    assert result.code == EXIT_OK
    said = result.json()
    assert said["theme"] == Skin("flat").theme.name
    assert said["tokens"] == Skin("flat").tokens()
    # and the human view says which theme it is holding
    listing = run(["widgets", "--project", project(ui={"theme": "light"}),
                   "--theme", "flat"])
    assert "theme 'flat'" in listing.lines()[0][0]


def test_a_project_without_a_theme_in_its_manifest_keeps_the_default(run,
                                                                     project):
    result = run(["widgets", "--project", project(), "--json"])

    assert result.code == EXIT_OK
    assert result.json()["theme"] == Skin().theme.name


def test_the_manifests_theme_is_used_when_nobody_says_otherwise(run, project):
    result = run(["widgets", "--project", project(ui={"theme": "light"}),
                  "--json"])

    assert result.code == EXIT_OK
    said = result.json()
    assert said["theme"] == "light"
    assert said["digest"] == Skin("light").digest


def test_a_theme_that_is_not_one_is_refused_before_anything_is_printed(run):
    result = run(["widgets", "--theme", "garden"])

    assert result.code == EXIT_LOAD
    assert not result.out
    assert result.err.startswith("crude widgets:")
    assert "garden" in result.err


# -- one kind ------------------------------------------------------------


def test_one_kind_prints_its_props_in_the_order_set_reads_them(run):
    result = run(["widgets", "--kind", "slider"])
    out, _ = result.lines()

    assert result.code == EXIT_OK
    kind = get_kind("slider")
    assert out[0] == f"slider, emits [{kind.pd_object}]"
    assert f"  {len(kind.props)} prop(s):" in out
    for prop in kind.props.values():
        assert any(str(prop) in line for line in out), str(prop)


def test_a_base_kind_names_the_kinds_built_on_it(run):
    result = run(["widgets", "--kind", "slider"])
    out, _ = result.lines()

    assert any("kinds built on it" in line and "vslider" in line for line in out)


def test_one_kind_as_json_is_that_kinds_own_document(run):
    result = run(["widgets", "--kind", "meter", "--json"])

    assert result.code == EXIT_OK
    said = result.json()
    assert said["schema"] == SCHEMA
    assert said["kind"] == "meter"
    assert said["theme"] == Skin().theme.name
    assert [row["name"] for row in said["props"]] == list(get_kind("meter").props)


def test_a_kind_that_is_not_there_is_refused_with_the_names_close_to_it(run):
    result = run(["widgets", "--kind", "sliderr"])

    assert result.code == EXIT_LOAD
    assert not result.out
    assert "sliderr" in result.err
    assert "slider" in result.err


# -- running a script ----------------------------------------------------


def test_a_project_kind_is_in_the_table_the_command_prints(run, project):
    result = run(["widgets", "--project", project()])
    out, _ = result.lines()

    assert result.code == EXIT_OK
    assert "1 of them defined by a program" in out[0]
    row = next(line for line in out if "bpm" in line)
    assert "*" in row and "[hslider]" in row


def test_a_project_kind_can_be_asked_for_by_name(run, project):
    result = run(["widgets", "--project", project(), "--kind", "bpm", "--json"])

    assert result.code == EXIT_OK
    said = result.json()
    assert said["base"] == "slider"
    assert said["script"] is True
    assert said["declared"] == ["beats"]
    beats = next(row for row in said["props"] if row["name"] == "beats")
    assert (beats["default"], beats["min"], beats["max"]) == (4.0, 1.0, 16.0)


def test_a_json_run_is_one_document_with_the_scripts_words_on_stderr(run,
                                                                     project):
    result = run(["widgets", "--project", project(), "--json"])

    assert result.code == EXIT_OK
    assert result.json()["source"]["project"] == "deck"
    assert "bpm is defined" in result.err
    # the schema is a document on its own: a note or a warning in the middle of it
    # would stop a reader, so everything that is not the table went elsewhere
    assert result.out.lstrip().startswith("{")
    assert result.out.rstrip().endswith("}")


def test_a_loose_script_needs_no_project_around_it(run, script):
    result = run(["widgets", "--script", script(STANDALONE), "--kind",
                  "stepper"])
    out, _ = result.lines()

    assert result.code == EXIT_OK
    assert out[0] == "stepper - defined by a program"
    assert any("stepper.value: number" in line for line in out)


def test_a_script_that_asks_for_a_project_hears_that_there_is_none(run, script):
    """``LOG`` belongs to a project, and ``--script`` deliberately has none."""
    result = run(["widgets", "--script", script('LOG("hello")\n')])

    assert result.code == EXIT_SCRIPT
    assert result.err.startswith("crude widgets:")
    assert "not running inside a crude project" in result.err


def test_a_script_that_fails_still_prints_the_schema_it_got_to(run, script):
    result = run(["widgets", "--script", script(BAD_WIDGET), "--json"])

    assert result.code == EXIT_SCRIPT
    said = result.json()
    assert said["script_kinds"] == []
    assert "no-such-kind" in said["error"]


def test_a_script_that_ends_itself_ends_the_command_with_its_code(run, script):
    result = run(["widgets", "--script", script(f"{STANDALONE}EXIT(7)\n")])

    # EXIT is the script's choice rather than a failure, so nothing is said about
    # it, and the table the script built is still printed
    assert result.code == 7
    assert "stepper" in result.out
    assert not result.err


def test_a_script_is_run_with_no_library_and_no_device(run, script, monkeypatch):
    def refusing(*args, **kwargs):
        raise AssertionError("a schema dump must not open libpd")

    monkeypatch.setattr("crude.pd.lib.Libpd.load", refusing)

    assert run(["widgets", "--script", script(STANDALONE)]).code == EXIT_OK


def test_a_run_leaves_the_process_global_table_as_it_found_it(run, script):
    """The kinds a script registers are the run's, not the interpreter's."""
    run(["widgets", "--script", script(STANDALONE)])

    assert "stepper" not in KINDS


# -- the arguments -------------------------------------------------------


def test_a_script_and_a_project_cannot_both_be_named():
    with pytest.raises(SystemExit) as caught:
        main(["widgets", "--project", ".", "--script", "loose.crude"])

    assert caught.value.code == EXIT_MANIFEST


def test_a_script_that_is_not_there_is_refused(run):
    result = run(["widgets", "--script", "nowhere.crude"])

    assert result.code == EXIT_LOAD
    assert not result.out
    assert "nowhere.crude" in result.err


def test_a_project_that_does_not_declare_its_script_is_refused(run, tmp_path):
    directory = tmp_path / "hollow"
    directory.mkdir()
    (directory / "crude.manifest.json").write_text(json.dumps(
        {"id": "hollow", "entry": "gone.crude",
         "runtime": {"mode": "terminal"}}), encoding="utf-8")

    result = run(["widgets", "--project", str(directory)])

    assert result.code == EXIT_LOAD
    assert "gone.crude" in result.err


def test_a_manifest_that_is_wrong_ends_two(run, project):
    where = project(raw=json.dumps({"id": "deck", "runtime":
                                   {"mode": "terminal", "target_flps": 60}}))

    result = run(["widgets", "--project", where])

    assert result.code == EXIT_MANIFEST
    assert not result.out
    assert result.err.startswith("crude widgets:")
    assert "target_flps" in result.err
