"""``crude.manifest.json``: what a project may say, and how refusal is reported.

The behaviour worth pinning is the promise the loader relies on: a manifest that
builds is *complete and safe* (every field has its right type, no path escapes
the project), and a manifest that does not build says which key is wrong.
"""

from __future__ import annotations

import json
import os

import pytest

from crude.errors import ManifestError
from crude.project import (
    DEFAULT_SCHEMA,
    MANIFEST_NAME,
    ArrayDecl,
    AudioSection,
    AuthoredPatch,
    LoadingConfig,
    Manifest,
    PatchDecl,
    PdSection,
    Resources,
    RuntimeConfig,
    SerialDecl,
    UiConfig,
    build_manifest,
    find_project,
    known_backends,
    load_manifest,
    manifest_path,
)


# --------------------------------------------------------------------------
# a whole manifest
# --------------------------------------------------------------------------

def test_the_plan_example_builds(root, data):
    manifest = build_manifest(data, root=str(root))

    assert manifest.id == "fm-synth"
    assert manifest.name == "FM Synth"
    assert manifest.entry == "main.crude"
    assert manifest.runtime == RuntimeConfig("window", 1024, 640, 60, True)
    assert manifest.ui == UiConfig("dark", "generated", "assets/font/ui.ttf")
    assert manifest.pd.build_dir == ".crude_build/pd"
    assert [patch.id for patch in manifest.pd.patches] == ["drums", "synth"]
    assert manifest.serial[0] == SerialDecl("arduino", "COM3", 115200, "line", True)
    assert manifest.resources.presets == ("presets/*.json",)
    assert manifest.loading == LoadingConfig("auto")


def test_everything_optional_has_a_default(root):
    """An empty object is a valid manifest: a project can say nothing."""
    manifest = build_manifest({}, root=str(root))

    assert manifest.id == "project"
    assert manifest.name == "project"
    assert manifest.entry == "main.crude"
    assert manifest.runtime.mode == "window"
    assert manifest.audio.in_channels == 2
    assert manifest.pd.search_paths == ("pd",)
    assert manifest.pd.patches == ()
    assert manifest.serial == ()
    assert manifest.resources == Resources()
    assert manifest.loading.progress == "auto"
    assert manifest.schema == DEFAULT_SCHEMA


def test_a_name_defaults_to_the_id(root):
    manifest = build_manifest({"id": "metronome"}, root=str(root))

    assert manifest.name == "metronome"
    assert manifest.display_name == "metronome"


def test_the_project_directory_travels_with_the_manifest(root):
    manifest = build_manifest({"id": "metronome"}, root=str(root))

    assert manifest.project_dir == os.path.abspath(str(root))
    assert manifest.path == os.path.join(manifest.project_dir, MANIFEST_NAME)
    assert manifest.resolve("assets/array.txt") == os.path.normpath(
        os.path.join(manifest.project_dir, "assets/array.txt"))
    assert manifest.entry_path.endswith(os.path.join("", "main.crude"))
    assert manifest.build_path().endswith(os.path.join(".crude_build", "pd"))


def test_the_search_path_leads_with_the_project(root):
    manifest = build_manifest({"pd": {"search_paths": ["pd", "lib/synth"]}},
                              root=str(root))
    dirs = manifest.search_dirs()

    assert dirs[0] == manifest.project_dir
    assert all(part in dirs for part in
               (manifest.resolve("pd"), manifest.resolve("lib/synth")))


def test_str_and_repr_say_what_was_declared(root):
    manifest = build_manifest({"id": "metronome",
                               "pd": {"patches": [{"id": "a", "file": "a.pd"}]}},
                              root=str(root))

    assert "metronome" in str(manifest)
    assert "1 patch" in str(manifest)


# --------------------------------------------------------------------------
# unknown keys: the point of a schema is to notice typos
# --------------------------------------------------------------------------

def test_an_unknown_top_level_key_is_refused(error):
    caught = error({"id": "x", "target_flps": 60},
                   contains="unexpected key 'target_flps'")

    assert caught.pointer.endswith("target_flps")


def test_the_message_names_the_keys_that_are_known_there(error):
    caught = error({"id": "x", "runtm": {}}, contains="runtime")

    assert "loading" in str(caught)  # the whole list is quoted


def test_several_unknown_keys_are_listed_once_each(error):
    error({"id": "x", "alpha": 1, "beta": 2}, contains="'alpha', 'beta'")


def test_an_unknown_key_inside_a_section_points_at_the_section(error):
    caught = error({"runtime": {"mode": "window", "targer_fps": 30}},
                   contains="unexpected key 'targer_fps' in runtime")

    assert caught.pointer == "runtime.targer_fps"


def test_an_unknown_key_inside_a_listed_patch_keeps_the_index(error):
    caught = error({"pd": {"patches": [{"id": "a", "file": "a.pd"},
                                        {"id": "b", "files": "b.pd"}]}},
                   contains="pd.patches[1]")

    assert caught.pointer == "pd.patches[1].files"


# --------------------------------------------------------------------------
# $schema
# --------------------------------------------------------------------------

def test_the_schema_tag_is_optional(root):
    manifest = build_manifest({"id": "x"}, root=str(root))

    assert manifest.schema == DEFAULT_SCHEMA


def test_a_manifest_from_the_future_is_refused(error):
    error({"$schema": "crude/manifest@99"},
          contains="newer than this runtime understands")


def test_a_schema_that_is_not_crudes_is_refused(error):
    error({"$schema": "pd/patch@1"}, contains="not a crude manifest")


def test_a_schema_without_a_version_is_refused(error):
    error({"$schema": "crude/manifest@"}, contains="does not say which version")


def test_a_schema_version_of_zero_names_no_version(error):
    error({"$schema": "crude/manifest@0"}, contains="names no manifest version")


def test_a_schema_tag_is_not_a_number(error):
    error({"$schema": 1}, contains="must be a string")


# --------------------------------------------------------------------------
# id, name, entry
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad_id", ["with space", "with/slash", "with\\slash", ".."])
def test_an_id_that_cannot_be_a_directory_is_refused(error, bad_id):
    error({"id": bad_id}, contains="cannot be used as a directory name")


@pytest.mark.parametrize("good_id", ["fm-synth", "metronome", "a.b_c", "9"])
def test_ids_a_project_would_actually_use_are_accepted(root, good_id):
    assert build_manifest({"id": good_id}, root=str(root)).id == good_id


def test_an_entry_outside_the_project_is_refused(error):
    error({"entry": "../other/main.crude"}, contains="reaches outside the project")


def test_an_absolute_entry_is_refused(error):
    error({"entry": "/etc/main.crude"}, contains="must be relative to the project")


def test_a_windows_absolute_entry_is_refused(error):
    error({"entry": "C:/projects/x/main.crude"}, contains="must be relative")


def test_an_entry_must_be_a_crude_script(error):
    error({"entry": "main.py"}, contains="should be a .crude script")


def test_an_entry_is_normalised(root):
    manifest = build_manifest({"entry": "./src\\main.crude"}, root=str(root))

    assert manifest.entry == "src/main.crude"


def test_an_empty_entry_falls_back_to_the_default(root):
    assert build_manifest({"entry": "  "}, root=str(root)).entry == "main.crude"


# --------------------------------------------------------------------------
# runtime
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["terminal", "headless", "window"])
def test_the_three_modes_are_the_three_modes(root, mode):
    manifest = build_manifest({"runtime": {"mode": mode}}, root=str(root))

    assert manifest.runtime.mode == mode
    assert manifest.runtime.needs_window is (mode == "window")


def test_an_unknown_mode_names_the_choices(error):
    error({"runtime": {"mode": "fullscreen"}},
          contains="must be one of terminal, headless, window")


@pytest.mark.parametrize("field", ["width", "height", "target_fps"])
def test_the_window_geometry_has_to_be_positive(error, field):
    error({"runtime": {field: 0}}, contains=f"{field} must be at least 1, got 0")


def test_a_string_number_is_a_number_a_scripter_can_write(root):
    manifest = build_manifest({"runtime": {"width": "800", "target_fps": "30"}},
                              root=str(root))

    assert manifest.runtime.width == 800
    assert manifest.runtime.target_fps == 30


def test_a_fractional_frame_rate_is_refused(error):
    error({"runtime": {"target_fps": 59.94}}, contains="whole number")


def test_vsync_accepts_a_written_true(root):
    manifest = build_manifest({"runtime": {"vsync": "true"}}, root=str(root))

    assert manifest.runtime.vsync is True
    assert build_manifest({"runtime": {"vsync": "false"}},
                          root=str(root)).runtime.vsync is False


def test_vsync_refuses_a_number(error):
    error({"runtime": {"vsync": 3}}, contains="must be true or false")


def test_runtime_must_be_an_object(error):
    error({"runtime": ["window"]}, contains="runtime must be a JSON object")


# --------------------------------------------------------------------------
# ui
# --------------------------------------------------------------------------

def test_the_font_is_a_project_relative_file(root):
    manifest = build_manifest({"ui": {"font": "assets/font/ui.ttf"}}, root=str(root))

    assert manifest.ui.font == "assets/font/ui.ttf"


def test_a_font_outside_the_project_is_refused(error):
    error({"ui": {"font": "C:/Windows/Fonts/arial.ttf"}}, contains="absolute")


def test_no_font_means_the_builtin_one(root):
    assert build_manifest({"ui": {}}, root=str(root)).ui.font is None


def test_icons_may_name_a_folder_or_be_generated(root):
    manifest = build_manifest({"ui": {"icons": "assets/icons"}}, root=str(root))

    assert manifest.ui.icons == "assets/icons"


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------

def test_an_audio_section_describes_a_stream(root):
    manifest = build_manifest({"audio": {"sample_rate": 48000, "block_size": 32,
                                         "in_channels": 1, "out_channels": 2}},
                             root=str(root))
    config = manifest.audio.config()

    assert (config.sample_rate, config.block_size) == (48000, 32)
    assert (config.in_channels, config.out_channels) == (1, 2)


def test_the_backend_name_is_checked_against_the_registry(root):
    manifest = build_manifest({"audio": {"backend": "sounddevice"}}, root=str(root))

    assert manifest.audio.backend in known_backends()


def test_an_audio_backend_that_does_not_exist_is_refused(error):
    error({"audio": {"backend": "wasapi"}}, contains="no audio backend named 'wasapi'")


def test_a_device_may_be_an_index_or_a_name(root):
    manifest = build_manifest({"audio": {"input_device": 3,
                                         "output_device": "Studio Capture"}},
                             root=str(root))

    assert manifest.audio.input_device == 3
    assert manifest.audio.output_device == "Studio Capture"


def test_a_device_cannot_be_a_boolean(error):
    error({"audio": {"input_device": True}}, contains="cannot be true/false")


def test_zero_channels_on_one_side_is_a_real_stream(root):
    manifest = build_manifest({"audio": {"in_channels": 0, "out_channels": 2}},
                             root=str(root))

    assert manifest.audio.config().in_channels == 0


def test_the_sample_rate_has_to_be_positive(error):
    error({"audio": {"sample_rate": 0}}, contains="sample_rate must be at least 1")


def test_optional_audio_does_not_have_to_work(root):
    manifest = build_manifest({"audio": {"optional": True}}, root=str(root))

    assert manifest.audio.strict is False


def test_required_audio_has_to_work(root):
    manifest = build_manifest({"audio": {"optional": False}}, root=str(root))

    assert manifest.audio.strict is True


def test_disabled_audio_is_never_strict(root):
    manifest = build_manifest({"audio": {"enabled": False, "optional": False}},
                             root=str(root))

    assert manifest.audio.enabled is False
    assert manifest.audio.strict is False


# --------------------------------------------------------------------------
# pd
# --------------------------------------------------------------------------

def test_a_file_patch_and_an_authored_patch_are_different_things(root):
    manifest = build_manifest({"pd": {"patches": [
        {"id": "drums", "file": "pd/drums.pd"},
        {"id": "synth", "authored": {"width": 320, "height": 200}},
    ]}}, root=str(root))
    drums, synth = manifest.pd.patches

    assert drums.file == "pd/drums.pd" and not drums.is_authored
    assert synth.authored == AuthoredPatch(320, 200, None, True)
    assert synth.is_authored and synth.should_preload


def test_a_patch_has_to_say_where_it_comes_from(error):
    error({"pd": {"patches": [{"id": "lonely"}]}},
          contains="needs either a 'file' to open or an 'authored' block")


def test_a_patch_cannot_come_from_both_places(error):
    error({"pd": {"patches": [{"id": "both", "file": "a.pd",
                                "authored": {"width": 10, "height": 10}}]}},
          contains="cannot be both a 'file' and 'authored'")


def test_a_patch_needs_an_id(error):
    error({"pd": {"patches": [{"file": "a.pd"}]}}, contains="needs an id")


def test_the_same_id_twice_is_ambiguous(error):
    caught = error({"pd": {"patches": [{"id": "a", "file": "a.pd"},
                                        {"id": "a", "file": "b.pd"}]}},
                   contains="declared twice")

    assert caught.pointer == "pd.patches[1].id"
    assert "first at pd.patches[0]" in str(caught)


def test_an_authored_patch_preloads_unless_told_otherwise(root):
    manifest = build_manifest({"pd": {"patches": [
        {"id": "late", "authored": {"preload": False}},
    ]}}, root=str(root))

    assert manifest.pd.patches[0].authored.preload is False
    assert manifest.pd.patches[0].should_preload is False


def test_a_file_patch_preloads_only_when_asked(root):
    lazy = build_manifest({"pd": {"patches": [{"id": "a", "file": "a.pd"}]}},
                          root=str(root)).pd.patches[0]
    keen = build_manifest({"pd": {"patches": [{"id": "a", "file": "a.pd",
                                               "preload": True}]}},
                          root=str(root)).pd.patches[0]

    assert lazy.should_preload is False
    assert keen.should_preload is True


def test_a_patch_path_stays_inside_the_project(error):
    error({"pd": {"patches": [{"id": "a", "file": "../shared/a.pd"}]}},
          contains="reaches outside")


def test_search_paths_may_be_empty(root):
    manifest = build_manifest({"pd": {"search_paths": []}}, root=str(root))

    assert manifest.pd.search_paths == ()


def test_a_search_path_cannot_be_a_wildcard(error):
    error({"pd": {"search_paths": ["pd/*"]}}, contains="cannot contain wildcards")


def test_the_build_directory_is_relative(root):
    manifest = build_manifest({"pd": {"build_dir": "build/pd"}}, root=str(root))

    assert manifest.build_path() == manifest.resolve("build/pd")


def test_a_patch_describes_itself_for_the_check_report(root):
    manifest = build_manifest({"pd": {"patches": [
        {"id": "drums", "file": "pd/drums.pd", "optional": True},
        {"id": "synth", "authored": {"width": 900, "height": 600}},
    ]}}, root=str(root))

    assert manifest.pd.patches[0].describe() == "drums (pd/drums.pd optional)"
    assert manifest.pd.patches[1].describe() == "synth (authored 900x600 preload)"


def test_patches_must_be_an_array(error):
    error({"pd": {"patches": {"id": "a"}}}, contains="patches must be a JSON array")


# --------------------------------------------------------------------------
# serial
# --------------------------------------------------------------------------

def test_a_serial_link_is_optional_by_default(root):
    manifest = build_manifest({"serial": [{"id": "arduino", "port": "COM3"}]},
                             root=str(root))

    assert manifest.serial[0].optional is True
    assert manifest.serial[0].codec == "line"
    assert manifest.serial[0].baud == 115200


def test_a_link_without_a_port_is_useless(error):
    error({"serial": [{"id": "arduino"}]}, contains="needs a port")


def test_a_link_without_an_id_cannot_be_named(error):
    error({"serial": [{"port": "COM3"}]}, contains="needs an id")


def test_a_baud_rate_has_to_be_a_number(error):
    error({"serial": [{"id": "a", "port": "COM3", "baud": "fast"}]},
          contains="baud must be a number")


def test_a_codec_name_is_left_to_the_codec_registry(root):
    """S10 owns the codecs, so the manifest only insists one is named."""
    manifest = build_manifest({"serial": [{"id": "a", "port": "loop://",
                                           "codec": "something-new"}]},
                             root=str(root))

    assert manifest.serial[0].codec == "something-new"


def test_serial_must_be_a_list_of_links(error):
    error({"serial": {"id": "a", "port": "COM3"}},
          contains="serial must be a JSON array")


# --------------------------------------------------------------------------
# resources
# --------------------------------------------------------------------------

def test_resource_paths_are_relative(root):
    manifest = build_manifest({"resources": {"images": ["assets/a.png"],
                                             "sounds": ["assets/tick.wav"]}},
                             root=str(root))

    assert manifest.resources.images == ("assets/a.png",)
    assert manifest.resources.sounds == ("assets/tick.wav",)


def test_a_single_path_may_be_written_as_a_bare_string(error):
    error({"resources": {"images": "assets/a.png"}},
          contains="images must be a JSON array")


def test_only_the_last_part_of_a_preset_may_be_a_glob(error):
    error({"resources": {"presets": ["presets/*/one.json"]}},
          contains="may only use '*' in the file name")


def test_a_glob_is_allowed_in_the_preset_name(root):
    manifest = build_manifest({"resources": {"presets": ["presets/*.json"]}},
                             root=str(root))

    assert manifest.resources.presets == ("presets/*.json",)


def test_an_image_may_not_be_a_glob(error):
    error({"resources": {"images": ["assets/*.png"]}},
          contains="cannot contain wildcards")


def test_an_array_needs_a_name_and_a_file(error):
    error({"resources": {"arrays": [{"file": "assets/array.txt"}]}},
          contains="needs a name")
    error({"resources": {"arrays": [{"id": "wave"}]}}, contains="needs a file")


def test_a_resource_path_that_is_not_a_string_is_refused(error):
    error({"resources": {"sounds": [44]}},
          contains="sounds path must be a path string")


def test_an_array_format_is_chosen_from_a_short_list(root):
    manifest = build_manifest({"resources": {"arrays": [
        {"id": "wave", "file": "a.txt", "format": "pd"},
    ]}}, root=str(root))

    assert manifest.resources.arrays[0] == ArrayDecl("wave", "a.txt", "pd")


def test_an_unknown_array_format_is_refused(error):
    error({"resources": {"arrays": [{"id": "wave", "file": "a.txt",
                                     "format": "yaml"}]}},
          contains="must be one of text, pd, csv")


def test_an_array_may_name_the_patch_it_fills(root):
    manifest = build_manifest({
        "pd": {"patches": [{"id": "synth", "authored": {}}]},
        "resources": {"arrays": [{"id": "wave", "file": "a.txt",
                                  "patch": "synth"}]},
    }, root=str(root))

    assert manifest.resources.arrays[0].patch == "synth"


def test_an_array_that_names_an_undeclared_patch_is_refused(error):
    """libpd cannot create an array, so a stray patch id is a dead end."""
    caught = error({
        "pd": {"patches": [{"id": "synth", "authored": {}}]},
        "resources": {"arrays": [{"id": "wave", "file": "a.txt",
                                  "patch": "nowhere"}]},
    }, contains="which pd.patches does not declare")

    assert caught.pointer == "resources.arrays[0].patch"
    assert "declared: synth" in str(caught)


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

@pytest.mark.parametrize("progress", ["auto", "console", "json", "splash", "none"])
def test_every_reporter_a_manifest_can_ask_for(root, progress):
    manifest = build_manifest({"loading": {"progress": progress}}, root=str(root))

    assert manifest.loading.progress == progress


def test_a_reporter_that_does_not_exist_is_named_with_the_real_ones(error):
    error({"loading": {"progress": "smoke"}},
          contains="must be one of auto, console, json, splash, none")


# --------------------------------------------------------------------------
# writing one back
# --------------------------------------------------------------------------

def test_the_defaults_are_written_out_so_a_reader_sees_every_field(root):
    manifest = build_manifest({"id": "x"}, root=str(root))

    data = manifest.as_dict()
    assert data["runtime"]["target_fps"] == 60
    assert data["audio"]["sample_rate"] == 44100
    assert data["pd"]["patches"] == []
    assert data["loading"] == {"progress": "auto"}


def test_a_manifest_survives_being_written_and_read(root, data):
    original = build_manifest(data, root=str(root))

    text = original.to_json()
    assert json.loads(text)["id"] == "fm-synth"

    again = build_manifest(json.loads(text), root=str(root))
    assert again == original


def test_the_rewritten_file_keeps_the_declaration_order(root, data):
    manifest = build_manifest(data, root=str(root))

    assert list(manifest.as_dict()) == [
        "$schema", "id", "name", "entry", "runtime", "ui", "audio", "pd",
        "serial", "resources", "loading",
    ]


# --------------------------------------------------------------------------
# reading a file
# --------------------------------------------------------------------------

def test_a_project_directory_is_found_by_its_manifest(project):
    project.manifest()

    manifest = project.load()

    assert manifest.id == "fm-synth"
    assert manifest.project_dir == os.path.abspath(str(project.directory))


def test_the_manifest_file_itself_is_accepted_as_the_project(project):
    target = project.manifest()

    assert load_manifest(str(target)).id == "fm-synth"
    assert manifest_path(str(project.directory)) == str(target)
    assert manifest_path(str(target)) == str(target)


def test_a_directory_that_is_not_a_project_says_so(project):
    with pytest.raises(ManifestError) as caught:
        project.load()

    assert MANIFEST_NAME in str(caught.value)
    assert caught.value.pointer == str(project.directory / MANIFEST_NAME)


def test_a_manifest_that_is_not_json_quotes_the_line(project):
    project.manifest(raw='{\n  "id": "x",\n  "entry": main.crude,\n}\n')

    with pytest.raises(ManifestError) as caught:
        project.load()

    assert "not valid JSON" in str(caught.value)
    assert "line 3" in str(caught.value)
    assert caught.value.pointer == str(project.directory / MANIFEST_NAME)


def test_a_manifest_of_the_wrong_shape_is_not_a_project(project):
    target = project.manifest(raw='["not", "a", "manifest"]')

    with pytest.raises(ManifestError) as caught:
        project.load()

    assert "a manifest is a JSON object, not an array" in str(caught.value)
    # there is no key to blame in a document that is not one
    assert caught.value.pointer == ""
    assert str(target) in str(caught.value)


def test_a_refused_manifest_names_the_file_and_the_key(project):
    """The pointer locates the key, the message names the document."""
    target = project.manifest({"id": "x", "runtm": {}})

    with pytest.raises(ManifestError) as caught:
        project.load()

    assert caught.value.pointer == "runtm"
    assert str(target) in str(caught.value)


def test_a_directory_in_place_of_the_manifest_is_not_a_project(project):
    """``os.path.isfile`` alone would let a folder named like the file through."""
    (project.directory / MANIFEST_NAME).mkdir()

    with pytest.raises(ManifestError) as caught:
        project.load()

    assert "is this a crude project?" in str(caught.value)


def test_a_manifest_with_a_bad_encoding_is_reported(project, monkeypatch):
    """An unreadable file is a manifest problem, with the file named."""
    target = project.manifest()
    real_open = open

    def guarded(file, *args, **kwargs):
        if os.path.basename(str(file)) == MANIFEST_NAME:
            raise OSError("denied")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded)

    with pytest.raises(ManifestError) as caught:
        load_manifest(str(target))

    assert "could not be read" in str(caught.value)
    assert str(target) in caught.value.pointer


def test_find_project_accepts_a_directory_or_a_manifest_or_neither(project):
    target = project.manifest()
    empty = project.directory / "empty"
    empty.mkdir()

    assert find_project(str(project.directory)) == (os.path.abspath(str(project.directory)),
                                                   str(target))
    assert find_project(str(target))[1] == str(target)
    found_dir, found_file = find_project(str(empty))
    assert found_file is None
    assert found_dir == os.path.abspath(str(empty))


def test_find_project_on_a_file_that_is_not_a_manifest(tmp_path):
    stray = tmp_path / "notes.txt"
    stray.write_text("hello", encoding="utf-8")

    directory, found = find_project(str(stray))

    assert found is None
    assert directory == os.path.abspath(str(tmp_path))
