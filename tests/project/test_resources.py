"""The resource table: names, array files and presets.

Array parsing is the part with real failure modes - a stray word in a file a
person edited by hand - so it gets the most tests, and every message is checked
to name the file and the line, because that is what makes the error fixable.
"""

from __future__ import annotations

import json
import os

import pytest

from crude.errors import ResourceError
from crude.project import (
    ARRAY,
    IMAGE,
    KINDS,
    PRESET,
    PRESET_KIND,
    PRESET_VERSION,
    SOUND,
    Resource,
    ResourceTable,
    check_kind,
    parse_array_csv,
    parse_array_file,
    parse_array_text,
    parse_numbers,
    parse_pd_array,
    preset_document,
    preset_values,
    read_array_file,
)


# --------------------------------------------------------------------------
# numbers in text
# --------------------------------------------------------------------------

def test_a_column_of_numbers_is_the_obvious_case():
    assert parse_numbers("1\n2\n3\n") == [1.0, 2.0, 3.0]


def test_any_mix_of_separators_means_the_same_file():
    assert parse_numbers("1, 2  3\t4\n5") == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_comments_and_blank_lines_are_not_data():
    text = "# a wave\n0.5   # half\n\n-0.5\n"

    assert parse_numbers(text) == [0.5, -0.5]


def test_the_number_forms_a_synthesiser_uses_are_all_accepted():
    assert parse_numbers("1e3 -2.5e-1 +3 .25") == [1000.0, -0.25, 3.0, 0.25]


def test_a_stray_word_names_the_line_it_is_on():
    with pytest.raises(ResourceError) as caught:
        parse_numbers("1\n2\nloud\n4\n", source="assets/array.txt")

    assert "assets/array.txt:3: 'loud' is not a number" in str(caught.value)


def test_the_error_says_how_much_was_read():
    with pytest.raises(ResourceError) as caught:
        parse_numbers("1 2 x")

    assert "expected 2 numbers before it" in str(caught.value)


def test_a_line_number_from_the_middle_of_a_file_is_honest():
    with pytest.raises(ResourceError) as caught:
        parse_numbers("x", source="f.txt", first_line=40)

    assert "f.txt:40" in str(caught.value)


def test_an_empty_run_is_not_an_error_here():
    """The *file* being empty is refused by the caller; a chunk may be blank."""
    assert parse_numbers("# nothing\n\n") == []


def test_text_and_csv_are_the_same_thing_until_a_header_shows_up():
    assert parse_array_text("1,2,3") == [1.0, 2.0, 3.0]
    assert parse_array_csv("time,amp\n0,1\n1,0.5") == [0.0, 1.0, 1.0, 0.5]


def test_a_csv_that_is_only_a_header_is_refused():
    with pytest.raises(ResourceError) as caught:
        parse_array_csv("time,amp")

    assert "neither the header nor the body" in str(caught.value)


def test_a_csv_that_is_wrong_on_line_two_is_wrong():
    with pytest.raises(ResourceError) as caught:
        parse_array_csv("time,amp\n0,loud")

    assert ":2:" in str(caught.value)


# --------------------------------------------------------------------------
# Pd's own array format
# --------------------------------------------------------------------------

def test_the_seed_lines_the_writer_emits_are_the_seed_lines_the_reader_reads():
    text = "#X array wave 4 float 0;\n#A 0 0.1 0.2 0.3 0.4;\n"

    name, values = parse_pd_array(text)

    assert name == "wave"
    assert values == pytest.approx([0.1, 0.2, 0.3, 0.4])


def test_the_header_is_optional():
    _name, values = parse_pd_array("#A 0 1 2;")

    assert values == [1.0, 2.0]


def test_several_data_lines_are_one_array():
    _name, values = parse_pd_array("#A 0 1 2;\n#A 2 3 4;\n")

    assert values == [1.0, 2.0, 3.0, 4.0]


def test_the_first_number_of_a_data_line_is_where_it_starts():
    """``#A 1 2 3`` is index 1 then values 2 and 3, not three values at 0."""
    _name, values = parse_pd_array("#A 1 2 3;")

    assert values == [0.0, 2.0, 3.0]


def test_an_index_beyond_the_data_pads_the_way_pd_shows_it():
    _name, values = parse_pd_array("#A 0 1;\n#A 3 9;\n")

    assert values == [1.0, 0.0, 0.0, 9.0]


def test_the_other_lines_of_a_patch_file_are_ignored_not_refused():
    text = ("#N canvas 0 22 450 300 10;\n"
            "#X array wave 2 float 1;\n"
            "#A 0 0.5 0.5;\n"
            "#X coords 0 -1 1 1 85 60 1;\n"
            "#X restore 50 50 pd wave;\n")

    assert parse_pd_array(text)[1] == [0.5, 0.5]


def test_a_line_that_is_not_an_array_line_is_a_bad_file():
    with pytest.raises(ResourceError) as caught:
        parse_pd_array("1 2 3\n", source="wave.txt")

    assert "'1' is not an array line" in str(caught.value)
    assert "wave.txt:1" in str(caught.value)


def test_an_indexed_line_without_a_number_is_refused():
    with pytest.raises(ResourceError) as caught:
        parse_pd_array("#A first 1 2;")

    assert "'first' is not an array index" in str(caught.value)


def test_a_data_line_without_an_index_is_refused():
    """The index is mandatory in Pd's format, so a bare ``#A`` says nothing."""
    with pytest.raises(ResourceError) as caught:
        parse_pd_array("#A ;")

    assert "needs the index" in str(caught.value)


def test_a_negative_index_is_refused():
    with pytest.raises(ResourceError) as caught:
        parse_pd_array("#A -1 1 2;")

    assert "cannot be negative" in str(caught.value)


def test_words_inside_a_data_line_are_refused():
    with pytest.raises(ResourceError) as caught:
        parse_pd_array("#A 0 1 loud 3;")

    assert "holds numbers only" in str(caught.value)


# --------------------------------------------------------------------------
# formats and files
# --------------------------------------------------------------------------

@pytest.mark.parametrize("format,text,expected", [
    ("text", "1 2", [1.0, 2.0]),
    ("csv", "a,b\n1,2", [1.0, 2.0]),
    ("pd", "#A 0 1 2;", [1.0, 2.0]),
])
def test_one_door_for_the_three_formats(format, text, expected):
    assert parse_array_file(text, format) == expected


def test_an_unknown_format_says_what_there_is():
    with pytest.raises(ResourceError) as caught:
        parse_array_file("1", "yaml")

    assert "no array format 'yaml' (known: text, csv, pd)" in str(caught.value)


def test_reading_a_file_from_disk_gives_the_numbers(project, make_table):
    project.file("assets/array.txt", "0.0\n0.5\n1.0\n")
    table = make_table({"arrays": [{"id": "wave", "file": "assets/array.txt"}]},
                       extra={"pd": {"patches": [{"id": "p", "authored": {}}]}})

    assert table.values("wave") == [0.0, 0.5, 1.0]


def test_an_empty_array_file_is_a_broken_project(project, make_table):
    project.file("assets/array.txt", "# nothing here\n")
    table = make_table({"arrays": [{"id": "wave", "file": "assets/array.txt"}]})

    with pytest.raises(ResourceError) as caught:
        table.values("wave")

    assert "contains no numbers" in str(caught.value)


def test_a_file_that_is_not_there_is_reported_with_its_path(make_table):
    table = make_table({"arrays": [{"id": "wave", "file": "assets/gone.txt"}]})

    with pytest.raises(ResourceError) as caught:
        table.values("wave")

    message = str(caught.value)
    assert "could not be read" in message
    assert "gone.txt" in message
    assert "no numbers" not in message


def test_a_format_read_from_disk_is_the_format_that_is_used(project, make_table):
    project.file("wave.txt", "#X array wave 2 float 0;\n#A 0 7 8;")
    table = make_table({"arrays": [{"id": "wave", "file": "wave.txt",
                                   "format": "pd"}]})

    assert table.values("wave") == [7.0, 8.0]


def read_array_missing(path):
    """Helper for the direct file API: a missing file names itself."""
    with pytest.raises(ResourceError) as caught:
        read_array_file(path)
    return str(caught.value)


def test_read_array_file_is_usable_on_its_own(tmp_path):
    target = tmp_path / "numbers.txt"
    target.write_text("1 2 3", encoding="utf-8")

    assert read_array_file(str(target)) == [1.0, 2.0, 3.0]
    assert "could not be read" in read_array_missing(str(tmp_path / "nope.txt"))


def test_a_binary_file_is_not_a_list_of_numbers(tmp_path):
    target = tmp_path / "bytes.txt"
    target.write_bytes(b"\xff\xfe\x00\x01")

    with pytest.raises(ResourceError) as caught:
        read_array_file(str(target))

    assert "is not a text file" in str(caught.value)


# --------------------------------------------------------------------------
# presets
# --------------------------------------------------------------------------

def test_a_preset_remembers_what_it_is():
    document = preset_document("init", [1.0, 2.0])

    assert document == {PRESET_KIND: PRESET_VERSION, "id": "init",
                        "data": [1.0, 2.0]}
    assert preset_values(document) == [1.0, 2.0]


def test_a_hand_written_list_is_still_a_preset():
    assert preset_values([0.0, 1.0]) == [0.0, 1.0]


def test_a_preset_from_a_future_version_is_not_guessed_at():
    with pytest.raises(ResourceError) as caught:
        preset_values({PRESET_KIND: 99, "data": [1]}, source="p.json")

    assert "preset version 99" in str(caught.value)
    assert f"reads {PRESET_VERSION}" in str(caught.value)


def test_saving_a_preset_writes_the_declared_folder(project, make_table):
    table = make_table({"presets": ["presets/*.json"]})

    path = table.write_preset("bass", [1.0, 2.0, 3.0])

    assert os.path.dirname(path) == os.path.join(str(project.directory), "presets")
    assert json.loads(open(path, encoding="utf-8").read())["data"] == [1.0, 2.0, 3.0]
    assert table.read_preset("bass") == [1.0, 2.0, 3.0]


def test_a_saved_preset_is_addressable_like_a_shipped_one(project, make_table):
    table = make_table({"presets": ["presets/*.json"]})
    table.write_preset("lead", {"cutoff": 800})

    assert "lead" in table.presets()
    assert table.get("lead").kind == PRESET


def test_a_preset_written_by_hand_in_the_folder_is_found(project, make_table):
    project.file("presets/init.json", json.dumps({"crude_preset": 1,
                                                  "id": "init", "data": [4, 5]}))
    project.file("presets/notes.txt", "not a preset")
    table = make_table({"presets": ["presets/*.json"]})

    assert table.presets() == ["init"]
    assert table.read_preset("init") == [4, 5]


def test_a_project_with_no_presets_folder_yet_can_still_save_one(project,
                                                                make_table):
    table = make_table({"presets": ["presets/*.json"]})

    assert table.presets() == []
    assert table.preset_dirs == [os.path.join(str(project.directory), "presets")]

    path = table.write_preset("first", [1])
    assert os.path.isfile(path)


def test_a_preset_name_that_is_not_a_file_name_is_made_into_one(project,
                                                                make_table):
    table = make_table({"presets": ["presets/*.json"]})

    path = table.write_preset("../escape/evil", [1])

    assert os.path.basename(path) == "escape-evil.json"
    assert os.path.dirname(path) == os.path.join(str(project.directory), "presets")


def test_with_no_presets_declared_the_default_folder_is_used(project, make_table):
    table = make_table({"images": ["a.png"]})

    path = table.write_preset("init", [1])

    assert path == os.path.join(str(project.directory), "presets", "init.json")


def test_reading_a_preset_that_does_not_exist_names_it(project, make_table):
    table = make_table({"presets": ["presets/*.json"]})

    with pytest.raises(ResourceError) as caught:
        table.read_preset("missing")

    assert "preset 'missing' could not be read" in str(caught.value)


def test_a_preset_that_is_not_json_says_so(project, make_table):
    project.file("presets/broken.json", "{oops")
    table = make_table({"presets": ["presets/*.json"]})

    with pytest.raises(ResourceError) as caught:
        table.read_preset("broken")

    assert "is not JSON" in str(caught.value)


# --------------------------------------------------------------------------
# the table itself
# --------------------------------------------------------------------------

@pytest.fixture
def shipped(project):
    """A project that has every kind of file the manifest can name."""
    project.file("assets/knob_atlas.png", "png")
    project.file("assets/metrotick.wav", "wav")
    project.file("assets/array.txt", "0 1 2 3")
    project.file("presets/init.json", json.dumps([1, 2]))
    return project


def resources_payload():
    return {
        "images": ["assets/knob_atlas.png"],
        "sounds": ["assets/metrotick.wav"],
        "arrays": [{"id": "wave", "file": "assets/array.txt", "patch": "synth"}],
        "presets": ["presets/*.json"],
    }


def pd_payload():
    return {"pd": {"patches": [{"id": "synth", "authored": {"width": 100,
                                                            "height": 100}}]}}


def test_a_resource_answers_to_every_name_a_script_might_try(shipped, make_table):
    table = make_table(resources_payload(), extra=pd_payload())

    for spelling in ("assets/knob_atlas.png", "knob_atlas.png", "knob_atlas"):
        assert table.get(spelling).kind == IMAGE
    assert table.get("wave").kind == ARRAY
    assert table.get("init").kind == PRESET


def test_the_declared_id_of_an_array_beats_its_file_name(shipped, make_table):
    table = make_table(resources_payload(), extra=pd_payload())

    assert table.get("wave").id == "wave"
    assert table.get("assets/array.txt").id == "wave"
    assert table.get("array").id == "wave"


def test_paths_come_back_absolute(shipped, make_table):
    table = make_table(resources_payload(), extra=pd_payload())

    assert table.path("wave") == os.path.join(table.root, "assets", "array.txt")


def test_kinds_are_grouped_for_the_loader(shipped, make_table):
    table = make_table(resources_payload(), extra=pd_payload())

    assert [entry.id for entry in table.of_kind(IMAGE)] == ["knob_atlas"]
    assert [entry.id for entry in table.of_kind(SOUND)] == ["metrotick"]
    assert [entry.id for entry in table.of_kind(ARRAY)] == ["wave"]
    assert [entry.id for entry in table.of_kind(PRESET)] == ["init"]


def test_an_unknown_kind_is_refused_before_it_confuses_a_loop(make_table):
    with pytest.raises(ResourceError) as caught:
        make_table().of_kind("videos")

    assert "no resource kind 'videos'" in str(caught.value)
    assert check_kind(IMAGE) == IMAGE


def test_the_counts_say_how_much_there_is_to_load(shipped, make_table):
    table = make_table(resources_payload(), extra=pd_payload())

    assert table.counts() == {IMAGE: 1, SOUND: 1, ARRAY: 1, PRESET: 1}
    assert set(table.counts()) == set(KINDS)
    assert len(table) == 4
    assert all(isinstance(entry, Resource) for entry in table)
    assert "array': 1" in repr(table)


def test_a_missing_file_is_listed_once_per_declaration(project, make_table):
    project.file("assets/here.png", "x")
    table = make_table({"images": ["assets/gone.png", "assets/here.png"]})

    missing = table.missing()

    assert [entry.declared for entry in missing] == ["assets/gone.png"]
    assert len(table.check()) == 1
    assert "is declared as 'assets/gone.png'" in table.check()[0]


def test_an_empty_project_is_valid(make_table):
    table = make_table()

    assert table.entries == []
    assert table.missing() == []
    assert "declares no resources" in str(_error_for(table, "anything"))


def _error_for(table, name):
    try:
        table.get(name)
    except ResourceError as exc:
        return exc
    raise AssertionError(f"'{name}' should not have resolved")


def test_an_unknown_resource_lists_the_ones_that_exist(shipped, make_table):
    table = make_table(resources_payload(), extra=pd_payload())

    with pytest.raises(ResourceError) as caught:
        table.get("flute")

    assert "there is no resource 'flute'" in str(caught.value)
    assert "knob_atlas, metrotick, wave" in str(caught.value)


def test_a_name_that_could_mean_two_files_says_so(project, make_table):
    project.file("assets/tick.wav", "a")
    project.file("loops/tick.wav", "b")
    table = make_table({"sounds": ["assets/tick.wav", "loops/tick.wav"]})

    with pytest.raises(ResourceError) as caught:
        table.get("tick")

    assert "'tick' is ambiguous" in str(caught.value)
    assert "assets/tick.wav, loops/tick.wav" in str(caught.value)
    # the full path still means one thing
    assert table.get("assets/tick.wav").path.endswith("tick.wav")


def test_backslashes_in_a_script_are_the_same_path(shipped, make_table):
    table = make_table(resources_payload(), extra=pd_payload())

    assert table.get("assets\\knob_atlas.png").kind == IMAGE


def test_an_empty_name_is_refused_without_a_lookup(make_table):
    with pytest.raises(ResourceError) as caught:
        make_table().get("")

    assert "needs a name or a path" in str(caught.value)


def test_find_is_get_without_the_exception(project, make_table):
    project.file("a.png", "x")
    table = make_table({"images": ["a.png"]})

    assert table.find("a").declared == "a.png"
    assert table.find("b") is None


def test_arrays_know_which_patch_they_fill(shipped, make_table):
    table = make_table(resources_payload(), extra=pd_payload())

    assert [entry.id for entry in table.arrays_for("synth")] == ["wave"]
    assert table.arrays_for("other") == []


def test_an_array_which_is_not_an_array_is_refused(shipped, make_table):
    table = make_table(resources_payload(), extra=pd_payload())

    with pytest.raises(ResourceError) as caught:
        table.values("metrotick")

    assert "is a sound, not an array of numbers" in str(caught.value)


def test_a_file_is_parsed_once_and_then_remembered(project, make_table):
    project.file("assets/array.txt", "1 2 3")
    table = make_table({"arrays": [{"id": "wave", "file": "assets/array.txt"}]})

    assert table.values("wave") == [1.0, 2.0, 3.0]

    project.file("assets/array.txt", "9 9 9")

    assert table.values("wave") == [1.0, 2.0, 3.0]
    assert table.values("wave") is not table.values("wave")  # a copy, not the cache


def test_the_table_describes_itself_for_the_check_report(shipped, make_table):
    table = make_table(resources_payload(), extra=pd_payload())
    lines = table.describe()

    assert len(lines) == 4
    assert lines[0].split() == ["image", "knob_atlas", "assets/knob_atlas.png"]
    assert lines[2].split() == ["array", "wave", "assets/array.txt"]
    assert "(missing)" not in "\n".join(lines)


def test_a_resource_line_carries_the_information_a_script_wants(shipped,
                                                               make_table):
    entry = make_table(resources_payload(), extra=pd_payload()).get("wave")

    assert str(entry) == "Resource array:wave (assets/array.txt)"
    assert entry.name == "array"
    assert entry.exists is True
    assert entry.as_dict() == {"id": "wave", "kind": "array",
                               "declared": "assets/array.txt",
                               "path": entry.path, "exists": True}
    assert entry.array.patch == "synth"
