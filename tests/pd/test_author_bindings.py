"""Authoring a patch from a script: ``PD_BUILD`` through ``PD_LOAD``.

Written as ``.crude`` source, because that is the interface being tested - the
argument names, the handles that come back, and what the generated ``.pd`` text
looks like. libpd is the fake, so ``PD_LOAD`` really parses the generated file
and declares the receivers and tables it finds in it.
"""

from __future__ import annotations

import os

import pytest

from crude.lang.values import HostHandle, List, Number, String

#: The patch used by most of the tests below, as the script would write it.
SYNTH = (
    'VAR p = PD_BUILD("synth")\n'
    'VAR osc = PD_OBJ(p, "osc~ 440")\n'
    'VAR dac = PD_OBJ(p, "dac~")\n'
    'VAR freq = PD_NUM(p, "freq")\n'
    "PD_LINK(p, freq, osc)\n"
    "PD_LINK(p, osc, dac)\n"
    "PD_LINK(p, osc, dac, 0, 1)\n"
)

SYNTH_TEXT = (
    "#N canvas 0 0 600 400 12;\n"
    "#X obj 20 20 osc~ 440;\n"
    "#X obj 160 20 dac~;\n"
    "#X floatatom 300 20 5 0 0 0 freq freq -;\n"
    "#X connect 0 0 1 0;\n"
    "#X connect 0 0 1 1;\n"
    "#X connect 2 0 0 0;\n"
)


def as_py(value):
    """Convert a runtime value into the plainest Python equivalent."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, List):
        return [as_py(element) for element in value.elements]
    if isinstance(value, HostHandle):
        return value.target
    return value


# -- building -----------------------------------------------------------


def test_an_authored_patch_lands_on_the_grid(eval_pd, runtime):
    value = eval_pd(SYNTH + "PD_DUMP(p)")

    assert as_py(value) == SYNTH_TEXT
    assert list(runtime.docs) == ["synth"]
    assert runtime.patches == {}  # nothing was written or loaded yet


def test_boxes_keep_the_ids_the_bindings_report(eval_pd):
    value = eval_pd(SYNTH + "PD_BOXES(p)")

    assert as_py(value) == ["obj1", "obj2", "floatatom1"]


def test_coordinates_are_taken_as_given(eval_pd):
    value = eval_pd(
        'VAR p = PD_BUILD("synth")\n'
        'PD_OBJ(p, "osc~ 440", 250, 60)\n'
        'PD_OBJ(p, "dac~", 250, 100)\n'
        "PD_DUMP(p)"
    )

    assert "#X obj 250 60 osc~ 440;" in as_py(value)
    assert "#X obj 250 100 dac~;" in as_py(value)


def test_the_canvas_can_be_sized(eval_pd):
    value = eval_pd('PD_DUMP(PD_BUILD("synth", 320, 240, 10))')

    assert as_py(value).startswith("#N canvas 0 0 320 240 10;\n")


def test_every_box_kind_has_its_own_call(eval_pd):
    value = eval_pd(
        'VAR p = PD_BUILD("synth")\n'
        'PD_OBJ(p, "osc~ 440")\n'
        'PD_MSG(p, "set $1")\n'
        'PD_NUM(p, "freq")\n'
        'PD_SYMB(p, "note")\n'
        'PD_TEXT(p, "a comment")\n'
        'PD_OBJ(p, "graph")\n'
        'PD_TABLE(p, "wave", 8)\n'
        "PD_DUMP(p)"
    )
    text = as_py(value)

    assert "#X obj 20 20 osc~ 440;" in text
    assert "#X msg 160 20 set $1;" in text
    assert "#X floatatom 300 20 5 0 0 0 freq freq -;" in text
    assert "#X symbolatom 440 20 10 0 0 0 note note -;" in text
    assert "#X text 20 80 a comment;" in text
    assert "#X obj 160 80 graph;" in text
    assert "#X obj 300 80 table $0-wave 8;" in text


def test_the_number_box_is_settable_by_name(eval_pd):
    """``PD_NUM(p, "freq")`` binds the box to ``freq`` so ``PD_SEND`` reaches it."""
    value = eval_pd(SYNTH + 'PD_INSPECT(p, freq)')

    assert as_py(value) == (
        "floatatom1: floatatom  at 300,20 label 'freq' recv 'freq'"
    )


# -- tables -------------------------------------------------------------


def test_a_table_is_scoped_to_the_patch_instance(eval_pd):
    value = eval_pd('VAR p = PD_BUILD("synth")\nPD_TABLE(p, "wave", 16)\nPD_DUMP(p)')

    assert "#X obj 20 20 table $0-wave 16;" in as_py(value)


def test_a_table_can_be_left_global_and_seeded(eval_pd):
    value = eval_pd(
        'VAR p = PD_BUILD("synth")\n'
        'PD_TABLE(p, "wave", 4, 20, 20, [1, 2], 0)\n'
        "PD_DUMP(p)"
    )
    text = as_py(value)

    assert "#X obj 20 20 table wave 4;" in text
    assert "#A 0 1 2 0 0;" in text


def test_seed_replaces_the_table_data_before_the_load(eval_pd):
    value = eval_pd(
        'VAR p = PD_BUILD("synth")\n'
        'PD_TABLE(p, "wave", 4)\n'
        'PD_SEED(p, "wave", [0.5, 0.25])\n'
        "PD_DUMP(p)"
    )

    assert "#A 0 0.5 0.25 0 0;" in as_py(value)


def test_seed_names_the_table_it_cannot_find(pd_failure):
    error = pd_failure(
        'VAR p = PD_BUILD("synth")\nPD_SEED(p, "wave", [1, 2])'
    )
    assert "no table called 'wave'" in error.as_string()


# -- wiring -------------------------------------------------------------


def test_link_answers_with_what_it_connected(eval_pd):
    value = eval_pd(SYNTH + "PD_LINK(p, osc, dac, 1, 0)")

    assert as_py(value) == "connect obj1:1 -> obj2:0"


def test_a_link_is_only_written_once(eval_pd):
    value = eval_pd(SYNTH + "PD_LINK(p, osc, dac)\nPD_DUMP(p)")

    assert as_py(value).count("#X connect 0 0 1 0;") == 1


def test_boxes_can_be_linked_by_id_as_well(eval_pd):
    value = eval_pd(SYNTH + 'PD_LINK(p, "floatatom1", "obj1", 0, 0)\nPD_DUMP(p)')

    assert "#X connect 2 0 0 0;" in as_py(value)


def test_unlink_answers_with_whether_there_was_a_cord(eval_pd, run_pd):
    value, error = run_pd(SYNTH + "PD_UNLINK(p, osc, dac, 0, 1)")
    assert error is None, error.as_string()
    assert as_py(value) == 1

    value = eval_pd("PD_UNLINK(p, osc, dac, 0, 1)")
    assert as_py(value) == 0


def test_unlinking_leaves_the_rest_of_the_patch(eval_pd):
    value = eval_pd(SYNTH + "PD_UNLINK(p, osc, dac, 0, 0)\nPD_DUMP(p)")

    text = as_py(value)
    assert "#X connect 0 0 1 0;" not in text
    assert "#X connect 0 0 1 1;" in text


# -- reports ------------------------------------------------------------


def test_inspect_describes_the_whole_patch(eval_pd):
    value = eval_pd(SYNTH + "PD_INSPECT(p)")
    lines = as_py(value)

    assert lines[0] == "patch synth: canvas 600x400 font 12"
    assert lines[1] == "3 boxes, 3 links"
    assert lines[2].startswith("[0] obj")
    assert "    0:0 -> 1:0" in lines
    assert lines[-1] == "    2:0 -> 0:0"


def test_inspect_shows_the_warnings_a_load_would_log(eval_pd):
    value = eval_pd(
        'VAR p = PD_BUILD("synth")\nPD_OBJ(p, "osc~ 440")\nPD_INSPECT(p)'
    )

    assert as_py(value)[-1] == (
        "warning: box 'obj1' (osc~ 440) is not connected"
    )


def test_the_box_kinds_are_listed_for_help(eval_pd):
    from crude.pd.model import BOX_KINDS

    assert as_py(eval_pd("PD_KINDS()")) == list(BOX_KINDS)


# -- loading ------------------------------------------------------------


def test_load_writes_caches_and_opens_the_patch(eval_pd, run_pd, runtime, fake_lib):
    value, error = run_pd(SYNTH + 'PD_TABLE(p, "wave", 16)\nPD_LOAD(p)')
    assert error is None, error.as_string()

    patch = as_py(value)
    assert patch.id == "synth"
    assert runtime.patches["synth"] is patch

    # the file libpd opened is the cached one, not something the script wrote
    assert patch.name == os.path.basename(runtime.cache.paths["synth"])
    assert patch.directory == os.path.dirname(runtime.cache.paths["synth"])
    assert patch.name.startswith("synth-")
    with open(os.path.join(patch.directory, patch.name), "r",
              encoding="utf-8") as handle:
        assert "#X obj 440 20 table $0-wave 16;" in handle.read()

    # the fake parsed that file, so the patch really does declare these
    assert fake_lib.exists("freq")
    assert f"{patch.dollar_zero}-wave" in fake_lib.arrays


def test_loading_twice_is_the_same_patch(eval_pd, runtime):
    first = eval_pd(SYNTH + "PD_LOAD(p)")
    second = eval_pd("PD_LOAD(p)")

    assert as_py(first) is as_py(second)
    assert len(runtime.patches) == 1
    assert as_py(eval_pd("PD_LOAD(p) == PD_LOAD(p)")) == 1


def test_a_loaded_patch_can_be_loaded_by_its_id(eval_pd, runtime):
    eval_pd(SYNTH + "PD_LOAD(p)")

    value = eval_pd('VAR live = PD_LOAD("synth")\nPD_DOLLARZERO(live)')
    assert as_py(value) == runtime.patches["synth"].dollar_zero
    assert isinstance(value, Number)


def test_dollar_zero_names_resolve_through_the_loaded_patch(eval_pd, runtime,
                                                            fake_lib):
    eval_pd(SYNTH + 'PD_TABLE(p, "wave", 16)\nPD_LOAD(p)')
    dollar_zero = runtime.patches["synth"].dollar_zero

    assert as_py(eval_pd('PD_ARRAY_SIZE(p, "wave")')) == 16

    eval_pd('PD_ARRAY_WRITE(p, "wave", [1, 2, 3])')
    assert fake_lib.arrays[f"{dollar_zero}-wave"] == [1.0, 2.0, 3.0] + [0.0] * 13
    assert as_py(eval_pd('AT(PD_ARRAY_READ(p, "wave", 1, 2), 0)')) == 2.0


def test_sending_to_a_scoped_name_reaches_the_right_instance(eval_pd, runtime,
                                                             fake_lib):
    eval_pd(SYNTH + "PD_LOAD(p)")
    dollar_zero = runtime.patches["synth"].dollar_zero

    eval_pd('PD_SEND(p, "$0-wave", 1)')
    runtime.flush()

    assert fake_lib.sent == [("float", f"{dollar_zero}-wave", 1.0)]


def test_sending_to_a_receiver_the_patch_declares(eval_pd, runtime, fake_lib):
    eval_pd(SYNTH + "PD_LOAD(p)")

    eval_pd('PD_SEND(p, "freq", 220)')
    runtime.flush()

    assert fake_lib.sent == [("float", "freq", 220.0)]


def test_an_authored_patch_reaches_the_script_through_on_pd(eval_pd, runtime,
                                                            fake_lib):
    eval_pd(
        SYNTH + "PD_LOAD(p)\n"
        "FUNC seen(v) -> PD_SEND(p, \"answered\", v)\n"
        'ON_PD(p, "freq", seen)'
    )

    fake_lib.emit("float", "freq", 440.0)
    assert runtime.dispatch() == 1

    runtime.flush()
    assert fake_lib.sent[-1] == ("float", "answered", 440.0)


def test_a_loaded_patch_cannot_be_edited_anymore(pd_failure):
    error = pd_failure(SYNTH + "PD_LOAD(p)\nPD_OBJ(p, \"dac~\")")
    assert "already loaded" in error.as_string()
    assert "PD_LOAD freezes" in error.as_string()


def test_a_report_is_still_possible_after_the_load(eval_pd):
    value = eval_pd(SYNTH + "PD_LOAD(p)\nPD_DUMP(p)")

    assert as_py(value) == SYNTH_TEXT


# -- saving -------------------------------------------------------------


def test_save_writes_the_patch_where_the_script_asks(eval_pd, runtime, tmp_path):
    target = (tmp_path / "patches" / "synth.pd").as_posix()

    value = eval_pd(SYNTH + f'PD_SAVE(p, "{target}")')

    assert as_py(value) == os.path.normpath(target)
    with open(target, "r", encoding="utf-8") as handle:
        assert handle.read() == SYNTH_TEXT


def test_save_without_a_path_goes_into_the_build_cache(eval_pd, runtime):
    value = eval_pd(SYNTH + "PD_SAVE(p)")

    path = as_py(value)
    assert os.path.isfile(path)
    assert runtime.cache.paths["synth"] == os.path.abspath(path)
    assert os.path.basename(path).startswith("synth-")


# -- properties through GET and SET -------------------------------------


def test_box_properties_are_readable_and_writable(eval_pd):
    value = eval_pd(SYNTH + 'SET(osc, "x", 300)\nGET(osc, "x")')
    assert as_py(value) == 300

    value = eval_pd('GET(p, "boxes")')
    assert as_py(value) == 3

    value = eval_pd('GET(dac, "kind")')
    assert as_py(value) == "obj"


def test_a_written_property_reaches_the_generated_text(eval_pd):
    value = eval_pd(SYNTH + 'SET(freq, "recv", "gate")\nPD_DUMP(p)')

    assert "floatatom 300 20 5 0 0 0 freq gate -;" in as_py(value)


def test_a_patch_property_can_be_resized(eval_pd):
    value = eval_pd(SYNTH + 'SET(p, "width", 320)\nPD_DUMP(p)')

    assert as_py(value).startswith("#N canvas 0 0 320 400 12;\n")


def test_a_box_id_is_not_a_property(pd_failure):
    error = pd_failure(SYNTH + 'GET(osc, "id")')
    assert "no property 'id'" in error.as_string()


def test_the_patch_handle_reports_its_type(eval_pd):
    assert as_py(eval_pd(SYNTH + "TYPE_OF(p)")) == "patch"
    assert as_py(eval_pd("TYPE_OF(osc)")) == "pd_box"
    assert as_py(eval_pd("TYPE_OF(freq)")) == "pd_box"


# -- errors -------------------------------------------------------------


def test_building_the_same_patch_twice_is_refused(pd_failure):
    error = pd_failure(SYNTH + 'PD_BUILD("synth")')
    assert "already exists" in error.as_string()


def test_a_load_without_a_build_says_so(pd_failure):
    error = pd_failure('PD_LOAD("synth")')
    assert "was not created with PD_BUILD" in error.as_string()


def test_a_number_is_not_a_patch(pd_failure):
    error = pd_failure('PD_OBJ(12, "osc~ 440")')
    assert "'patch' must be a patch" in error.as_string()


def test_a_loaded_patch_is_not_a_document(pd_failure, patch_file):
    error = pd_failure(
        f'VAR p = PD_FILE("tone", "{patch_file.as_posix()}")\nPD_OBJ(p, "osc~")'
    )
    assert "was not created with PD_BUILD" in error.as_string()
    assert "still being built" in error.as_string()


def test_a_file_patch_cannot_be_dumped_either(pd_failure, patch_file):
    """The same gap reported through a read-only call: no document, no text."""
    error = pd_failure(f'PD_DUMP(PD_FILE("tone", "{patch_file.as_posix()}"))')
    assert "patch 'tone' was not created with PD_BUILD" in error.as_string()


def test_a_box_from_another_patch_is_not_wired_in(pd_failure):
    error = pd_failure(
        SYNTH
        + 'VAR other = PD_BUILD("other")\nVAR x = PD_OBJ(other, "dac~")\n'
        "PD_LINK(p, osc, x)"
    )
    assert "is not a box of patch 'synth'" in error.as_string()


def test_an_unknown_box_id_is_named(pd_failure):
    error = pd_failure(SYNTH + 'PD_LINK(p, osc, "obj9")')
    assert "no box with id 'obj9'" in error.as_string()


def test_linking_needs_the_patch(pd_failure):
    error = pd_failure('PD_LINK("obj1", "obj2")')
    assert "too few args" in error.as_string()


def test_an_empty_box_cannot_be_loaded(pd_failure):
    error = pd_failure('VAR p = PD_BUILD("synth")\nPD_OBJ(p, "   ")\nPD_LOAD(p)')
    assert "has no text" in error.as_string()


def test_a_table_needs_room(pd_failure):
    error = pd_failure('VAR p = PD_BUILD("synth")\nPD_TABLE(p, "wave", 0)')
    assert "at least 1 sample" in error.as_string()


def test_authoring_needs_a_runtime(make_program):
    _, error = make_program().run('PD_BUILD("synth")')
    assert error is not None
    assert "no Pd runtime" in error.as_string()


def test_a_port_has_to_be_a_whole_number(pd_failure):
    error = pd_failure(SYNTH + "PD_LINK(p, osc, dac, 1.5, 0)")
    assert "whole number" in error.as_string()


# -- GUI boxes ----------------------------------------------------------

#: A patch with nothing on it yet, so each box below is the only one it holds.
GUI = 'VAR p = PD_BUILD("synth")\n'


def gui_dump(eval_pd, call):
    """The whole generated text of a patch built by ``call``."""
    return as_py(eval_pd(GUI + call + "\nPD_DUMP(p)"))


def test_a_slider_is_written_exactly_as_pd_saves_one(eval_pd):
    text = gui_dump(
        eval_pd,
        'PD_GUI(p, "hslider", 20, 60, 200, 20, '
        '["min", 20, "max", 300, "value", 120, "label", "BPM"])',
    )

    assert text == (
        "#N canvas 0 0 600 400 12;\n"
        "#X obj 20 60 hsl 200 20 20 300 0 0 empty empty BPM -2 -8 0 10 "
        "#dfdfdf #000000 #000000 120 1;\n"
    )


def test_the_symbols_are_already_escaped_the_way_a_file_holds_them(eval_pd):
    text = gui_dump(
        eval_pd,
        'PD_GUI(p, "tgl", 20, 20, 24, 24, ["send", "$0-run", "recv", "$0-run", '
        '"label", "my label"])',
    )

    assert r"#X obj 20 20 tgl 24 0 \$0-run \$0-run my\ label 17 7 0 10 " in text
    # the point of the verbatim path: the writer did not escape it a second time
    assert r"\\$0-run" not in text


def test_the_names_the_script_wrote_are_the_ones_the_box_reports(eval_pd):
    line = as_py(eval_pd(
        GUI
        + 'VAR t = PD_GUI(p, "tgl", 20, 20, 24, 24, ["send", "$0-run", '
        '"recv", "$0-run", "label", "my label"])\n'
        "PD_INSPECT(p, t)"
    ))

    # the file holds the escapes; the script sees what it typed
    assert "send '$0-run'" in line
    assert "recv '$0-run'" in line
    assert "label 'my label'" in line


@pytest.mark.parametrize("call, expected", [
    # a square box has one side, so the smaller axis is the one that fits
    ('PD_GUI(p, "tgl", 20, 20, 200, 24)', "tgl 24 0 empty empty empty"),
    # a name in the props list is the slot's own unit and wins over the geometry
    ('PD_GUI(p, "tgl", 20, 20, 200, 20, ["size", 19])', "tgl 19 0 empty empty empty"),
    ('PD_GUI(p, "hslider", 20, 20, 200, 20, ["width", 90, "height", 12])',
     "hsl 90 12 0 1"),
    # a number box's width is a digit count, so no pixel guess may rewrite it
    ('PD_GUI(p, "nbx", 20, 20, 200, 20)', "nbx 5 20 "),
    ('PD_GUI(p, "nbx", 20, 20, 200, 20, ["width", 8])', "nbx 8 20 "),
    ('PD_GUI(p, "vu", 20, 20, 19, 120, ["recv", "$0-lvl"])',
     r"vu 19 120 \$0-lvl empty -1 -10 0 12 #404040 #000000 0 0"),
    ('PD_GUI(p, "bng", 20, 20, 30, 30, ["send", "$0-hit"])',
     r"bng 30 250 50 0 \$0-hit empty empty"),
])
def test_the_geometry_lands_in_the_arguments_the_box_actually_has(eval_pd, call,
                                                                 expected):
    assert expected in gui_dump(eval_pd, call)


def test_a_radio_counts_the_labels_it_is_given(eval_pd):
    text = gui_dump(
        eval_pd,
        'PD_GUI(p, "radio", 20, 20, 0, 0, ["labels", ["1x", "2x", "3x"], '
        '"send", "$0-mult"])',
    )

    assert (r"#X obj 20 20 hradio 15 0 0 3 \$0-mult empty empty -31 9 0 10 "
            r"#dfdfdf #000000 #000000 0;") in text


def test_a_panel_is_a_canvas_with_a_border(eval_pd):
    text = gui_dump(
        eval_pd,
        'PD_GUI(p, "cnv", 20, 20, 0, 0, ["label", "mix", "width", 180, '
        '"height", 90, "border", 2])',
    )

    assert ("#X obj 20 20 cnv 2 180 90 empty empty mix 20 12 0 12 "
            "#e0e0e0 #404040 0;") in text


def test_a_comment_is_a_label_and_nothing_else(eval_pd):
    text = gui_dump(
        eval_pd, 'PD_GUI(p, "comment", 10, 10, 0, 0, ["text", "tempo"])')

    assert "#X text 10 10 tempo;" in text
    assert "cnv" not in text


def test_a_gui_box_lands_on_the_grid_when_the_script_leaves_it_out(eval_pd):
    text = gui_dump(eval_pd, 'PD_GUI(p, "tgl")\nPD_GUI(p, "tgl")')

    assert "#X obj 20 20 tgl 15 0 empty empty empty" in text
    assert "#X obj 160 20 tgl 15 0 empty empty empty" in text


def test_a_gui_box_is_a_box_like_any_other(eval_pd):
    value = eval_pd(GUI + 'VAR h = PD_GUI(p, "hslider", 20, 60, 200, 20, '
                        '["min", 20, "max", 300])\nPD_BOXES(p)')
    assert as_py(value) == ["obj1"]

    # a box handle, so GET/SET work on it the same way they do on any other
    assert as_py(eval_pd('GET(h, "kind")')) == "obj"

    text = as_py(eval_pd('SET(h, "x", 300)\nPD_DUMP(p)'))
    assert "#X obj 300 60 hsl 200 20 20 300" in text


def test_a_gui_box_reaches_the_loaded_patch(eval_pd, runtime):
    """The generated file parses, so the emitted box is a real one to Pd."""
    value = eval_pd(
        GUI + 'VAR h = PD_GUI(p, "hslider", 20, 60, 200, 20, '
              '["send", "$0-bpm", "min", 20, "max", 300, "value", 120])\n'
              "PD_LOAD(p)"
    )
    patch = as_py(value)

    assert patch.id == "synth"
    assert runtime.patches["synth"] is patch
    # the box libpd read is the one the script laid out, escapes and all
    assert r"hsl 200 20 20 300 0 0 \$0-bpm empty empty" in as_py(eval_pd("PD_DUMP(p)"))


@pytest.mark.parametrize("call, expected", [
    ('PD_GUI(p, "nope", 20, 20)', "is not a Pd GUI box this table knows"),
    ('PD_GUI(p, "dropdown", 20, 20)', "no Pd 0.54 template for 'dropdown'"),
    ('PD_GUI(p, "knob", 20, 20)', "not a vanilla Pd 0.54 object"),
    ('PD_GUI(p, "hslider", 20, 20, 100, 20, ["min"])',
     "leaves one without a name"),
    ('PD_GUI(p, "hslider", 20, 20, 100, 20, [1, 20])',
     "a 'props' name must be a string"),
    # a prop the widgets have but a Pd box has no argument for
    ('PD_GUI(p, "hslider", 20, 20, 100, 20, ["step", 5])',
     "an [hsl] box has no 'step' argument"),
    ('PD_GUI(p, "hslider", 20, 20, 100, 20, ["min", "wide"])',
     "a Pd 'min' argument wants a number"),
    ('PD_GUI(p, "hslider", 20, 20, -100, 20)', "cannot be negative"),
    ('PD_GUI(p, "comment", 20, 20)', "a comment needs its text"),
])
def test_a_box_the_call_cannot_describe_says_so(pd_failure, call, expected):
    error = pd_failure(GUI + call)

    assert expected in error.as_string()


def test_a_gui_box_is_frozen_with_the_rest_of_the_patch(pd_failure):
    error = pd_failure(GUI + 'PD_GUI(p, "tgl", 20, 20)\nPD_LOAD(p)\n'
                             'PD_GUI(p, "tgl", 40, 40)')

    assert "already loaded" in error.as_string()


def test_the_box_kinds_are_listed_from_the_table_they_come_from(eval_pd):
    from crude.pd import pdgui

    assert as_py(eval_pd("PD_GUI_KINDS()")) == list(pdgui.kinds())


# -- emitting a widget's twin -------------------------------------------

#: An emitted box is only interesting as the twin of something, so every call
#: below is made against a real widget tree as well as a real (fake) Pd runtime.
CANVAS = "#N canvas 0 0 600 400 12;\n"

SLIDER = ('VAR w = SLIDER("BPM", 20, 300, 120)\nSET(w, "send", "$0-bpm")\n'
          'PLACE(w, 20, 60, 200, 20)\n')


def emitted(eval_emit, widget, call="PD_EMIT(p, w)"):
    """The whole text of a patch that holds one emitted widget."""
    return as_py(eval_emit('VAR p = PD_BUILD("synth")\n'
                           + widget.rstrip("\n") + "\n" + call + "\nPD_DUMP(p)"))


def test_the_emitted_slider_is_the_box_the_script_drew(eval_emit):
    assert emitted(eval_emit, SLIDER) == CANVAS + (
        r"#X obj 20 60 hsl 200 20 20 300 0 0 \$0-bpm empty BPM -2 -8 0 10 "
        r"#dfdfdf #000000 #000000 120 1;" + "\n"
    )


@pytest.mark.parametrize("widget, line", [
    (SLIDER,
     r"#X obj 20 60 hsl 200 20 20 300 0 0 \$0-bpm empty BPM -2 -8 0 10 "
     r"#dfdfdf #000000 #000000 120 1;"),
    ('VAR w = VSLIDER("LEVEL", 0, 1, 0.5)\nSET(w, "recv", "$0-lvl")\n'
     'PLACE(w, 30, 20, 20, 120)',
     r"#X obj 30 20 vsl 20 120 0 1 0 0 empty \$0-lvl LEVEL 0 -9 0 10 "
     r"#dfdfdf #000000 #000000 0.5 1;"),
    # a switch carries both names, which is what lets it be set as well as read
    ('VAR w = TOGGLE("Run", 1)\nSET(w, "send", "$0-run")\n'
     'SET(w, "recv", "$0-run")\nPLACE(w, 20, 20, 24, 24)',
     r"#X obj 20 20 tgl 24 0 \$0-run \$0-run Run 17 7 0 10 #dfdfdf #000000 "
     r"#000000 1 1;"),
    # a number box's width is a digit count, so the widget's pixels only reach
    # the axis the box really measures in pixels
    ('VAR w = NUMBER("freq", 0, 1000, 440)\nSET(w, "send", "$0-f")\n'
     'PLACE(w, 40, 40, 60, 22)',
     r"#X obj 40 40 nbx 5 22 0 1000 0 0 \$0-f empty freq 0 -8 0 10 #dfdfdf "
     r"#000000 #000000 440 20;"),
    ('VAR w = METER("out", 0, 1)\nSET(w, "recv", "$0-l")\n'
     'PLACE(w, 10, 10, 19, 120)',
     r"#X obj 10 10 vu 19 120 \$0-l out -1 -10 0 12 #404040 #000000 0 0;"),
    ('VAR w = BUTTON("hit")\nSET(w, "send", "$0-hit")\n'
     'PLACE(w, 20, 20, 30, 30)',
     r"#X obj 20 20 bng 30 250 50 0 \$0-hit empty hit 17 7 0 10 #dfdfdf "
     r"#000000 #000000;"),
    # a radio's square side is the smaller axis, and its count comes off the list
    ('VAR w = RADIO(["1x", "2x", "3x"], 1)\nSET(w, "send", "$0-mult")\n'
     'PLACE(w, 20, 20, 120, 60)',
     r"#X obj 20 20 hradio 60 0 0 3 \$0-mult empty empty -31 9 0 10 #dfdfdf "
     r"#000000 #000000 1;"),
    ('VAR w = PANEL("mix")\nPLACE(w, 20, 20, 180, 90)',
     "#X obj 20 20 cnv 1 180 90 empty empty mix 20 12 0 12 #e0e0e0 #404040 0;"),
    # a kind that holds nothing is a line on the canvas, which is what Pd's own
    # comment is
    ('VAR w = TEXT("tempo")\nPLACE(w, 10, 10, 60, 14)',
     "#X text 10 10 tempo;"),
])
def test_every_catalog_kind_emits_the_box_the_two_tables_name(eval_emit, widget,
                                                              line):
    assert emitted(eval_emit, widget) == CANVAS + line + "\n"


def test_the_twin_is_at_the_box_the_widget_was_drawn_in(eval_emit):
    """A child's screen box is absolute, and so is the twin's position."""
    text = emitted(
        eval_emit,
        'VAR v = PANEL("mix")\nPLACE(v, 100, 50, 200, 100)\n'
        'VAR w = SLIDER("BPM", 0, 1, 0.25)\nSET(w, "send", "$0-i")\n'
        'NODE_ADD(v, w)\nPLACE(w, 12, 30, 80, 10)\n',
    )

    assert (r"#X obj 112 80 hsl 80 10 0 1 0 0 \$0-i empty BPM -2 -8 0 10 "
            r"#dfdfdf #000000 #000000 0.25 1;") in text


def test_the_call_can_move_the_twin_off_the_widget(eval_emit):
    text = emitted(eval_emit, SLIDER, "PD_EMIT(p, w, 300, 400, 90, 12)")

    assert "#X obj 300 400 hsl 90 12 20 300" in text


def test_an_emitted_box_is_a_box_like_any_other(eval_emit):
    value = eval_emit('VAR p = PD_BUILD("synth")\n' + SLIDER
                      + 'VAR h = PD_EMIT(p, w)\nPD_BOXES(p)')
    assert as_py(value) == ["obj1"]

    assert as_py(eval_emit('GET(h, "kind")')) == "obj"
    assert as_py(eval_emit('SET(h, "y", 90)\nPD_DUMP(p)')).startswith(
        CANVAS + r"#X obj 20 90 hsl ")


def test_an_emitted_panel_leaves_the_patch_with_nothing_to_report(stage, eval_emit):
    """A decoration is finished as it stands, so a dump says nothing about it."""
    text = emitted(eval_emit, 'VAR w = PANEL("mix")\nPLACE(w, 20, 20, 180, 90)\n')

    assert "cnv 1 180 90" in text
    assert stage.notes == []


def test_an_emitted_slider_survives_the_load(stage, eval_emit):
    """The generated file parses, so Pd sees a control and not a comment."""
    text = as_py(eval_emit('VAR p = PD_BUILD("synth")\n' + SLIDER
                           + 'PD_EMIT(p, w)\nPD_LOAD(p)\nPD_DUMP(p)'))

    assert r"hsl 200 20 20 300 0 0 \$0-bpm empty BPM" in text
    assert stage.runtime.patches["synth"].dollar_zero > 0


# -- the widget that has no box ----------------------------------------

NO_BOX = ('VAR w = SLIDER("BPM", 0, 1, 0.5)\nSET(w, "send", "$0-a")\n'
          'SET(w, "recv", "$0-b")\nSET(w, "pd_object", "")\n'
          'PLACE(w, 20, 20, 100, 20)\n')


def test_a_widget_with_no_box_still_wires_the_patch(stage, eval_emit):
    text = emitted(eval_emit, NO_BOX)

    # the names are object text, so they are written unescaped, the way [r]/[s]
    # have always been written
    assert CANVAS + "#X obj 20 20 r $0-a;\n#X obj 160 20 s $0-b;\n" == text
    assert len(stage.notes) == 1
    assert "'root/BPM' (kind 'slider') has no Pd box" in stage.notes[0]


def test_the_degrade_is_reported_once_per_patch(stage, eval_emit):
    emitted(eval_emit, NO_BOX, "PD_EMIT(p, w)\nPD_EMIT(p, w)")

    assert len(stage.notes) == 1


def test_a_kind_the_table_cannot_lay_out_is_wiring_once_cleared(eval_emit):
    """The dropdown error's own escape hatch, taken."""
    text = emitted(
        eval_emit,
        'VAR w = DROPDOWN(["a", "b"], 1)\nSET(w, "send", "$0-s")\n'
        'SET(w, "pd_object", "")\nPLACE(w, 20, 20, 100, 24)\n',
    )

    assert text == CANVAS + "#X obj 20 20 r $0-s;\n"


# -- what the call refuses ---------------------------------------------


def test_a_box_with_no_template_is_named_and_not_dropped(emit_failure):
    error = emit_failure(
        'VAR p = PD_BUILD("synth")\n'
        'VAR w = DROPDOWN(["a", "b"], 0)\nSET(w, "send", "$0-s")\n'
        'PLACE(w, 20, 20, 100, 24)\nPD_EMIT(p, w)'
    )

    said = error.as_string()
    assert "'root/dropdown' asks for a [dropdown] box" in said
    assert "no Pd 0.54 template" in said
    assert "'pd_object' prop" in said


def test_a_value_with_no_name_would_be_wired_to_nothing(emit_failure):
    error = emit_failure(
        'VAR p = PD_BUILD("synth")\nVAR w = SLIDER("BPM", 0, 1, 0)\n'
        'PLACE(w, 20, 20, 100, 20)\nPD_EMIT(p, w)'
    )

    assert "has no 'send' or 'recv' name" in error.as_string()


def test_a_decoration_with_nothing_to_say_is_refused(emit_failure):
    error = emit_failure(
        'VAR p = PD_BUILD("synth")\nVAR w = ICON("play", 20)\n'
        'PLACE(w, 5, 5, 20, 20)\nPD_EMIT(p, w)'
    )

    assert "no Pd box, no value and no 'label'" in error.as_string()


def test_a_panel_needs_no_names_to_have_a_twin(eval_emit):
    """Only a widget that holds a value has to say who it holds it for."""
    assert "cnv" in emitted(eval_emit, 'VAR w = PANEL("mix")\n'
                                      'PLACE(w, 20, 20, 180, 90)\n')


def test_a_number_is_not_a_widget(emit_failure):
    error = emit_failure('VAR p = PD_BUILD("synth")\nPD_EMIT(p, 12)')

    assert "argument 'widget' must be a widget handle" in error.as_string()


def test_a_patch_is_not_a_widget(emit_failure):
    """The two handles a script holds are not interchangeable, whichever way round."""
    error = emit_failure('VAR p = PD_BUILD("synth")\nPD_EMIT(p, p)')

    said = error.as_string()
    assert "argument 'widget' must be a widget handle" in said
    assert "got patch" in said


def test_a_widget_cannot_be_emitted_onto_a_loaded_patch(emit_failure):
    error = emit_failure(
        'VAR p = PD_BUILD("synth")\n' + SLIDER + 'PD_EMIT(p, w)\nPD_LOAD(p)\n'
        'PD_EMIT(p, w)'
    )

    assert "already loaded" in error.as_string()


def test_a_widget_reaches_the_call_as_a_handle_or_as_the_object_it_was_passed(stage):
    """``widget_of`` is the one place that knows a widget has two shapes.

    :meth:`Program.invoke` wraps every host object it is handed, so a widget that
    arrives out of a handler is a plain handle around the same object rather than
    the handle the constructor returned. Tested at this level because no script
    syntax can tell the two apart.
    """
    import types

    from crude.bindings.handles import widget_of
    from crude.bindings.registry import REGISTRY, HostContext
    from crude.lang.values import BindingError, HostHandle, Number

    value, error = stage.program.run(SLIDER)
    assert error is None, error.as_string()
    drawn = next(one for one in stage.ui.widgets() if one.name == "BPM")

    def context(argument):
        table = types.SimpleNamespace(get=lambda name: argument)
        call = types.SimpleNamespace(symbol_table=table,
                                     provided_args=frozenset({"widget"}))
        return HostContext(call, REGISTRY["PD_EMIT"])

    assert widget_of(context(drawn), "widget") is drawn
    wrapped = HostHandle(drawn, kind="handle")
    assert widget_of(context(wrapped), "widget") is drawn

    with pytest.raises(BindingError) as caught:
        widget_of(context(Number(3)), "widget")
    assert "must be a widget handle" in str(caught.value)



# -- the registry -------------------------------------------------------


@pytest.mark.parametrize("name", [
    "PD_BUILD", "PD_OBJ", "PD_MSG", "PD_NUM", "PD_SYMB", "PD_TEXT", "PD_GUI",
    "PD_EMIT", "PD_TABLE", "PD_SEED", "PD_LINK", "PD_UNLINK", "PD_BOXES",
    "PD_DUMP", "PD_SAVE", "PD_INSPECT", "PD_LOAD", "PD_KINDS", "PD_GUI_KINDS",
])
def test_the_authoring_bindings_are_registered_in_the_pd_area(name):
    from crude.bindings.pd_author import BINDINGS
    from crude.bindings.registry import REGISTRY

    assert name in BINDINGS
    assert name in REGISTRY, name
    assert REGISTRY[name].area == "pd", name
    assert REGISTRY[name].func.__doc__, name


@pytest.mark.parametrize("name", [
    "PD_OBJ", "PD_MSG", "PD_NUM", "PD_SYMB", "PD_TEXT", "PD_GUI", "PD_EMIT",
    "PD_TABLE", "PD_LINK", "PD_UNLINK", "PD_BOXES", "PD_DUMP", "PD_SAVE",
    "PD_INSPECT", "PD_LOAD",
])
def test_every_box_call_takes_the_patch_first(name):
    from crude.bindings.registry import REGISTRY

    assert REGISTRY[name].arg_names[0] == "patch", name


def test_the_box_calls_all_default_their_coordinates():
    from crude.bindings.registry import REGISTRY

    for name in ("PD_OBJ", "PD_MSG", "PD_NUM", "PD_SYMB", "PD_TEXT"):
        entry = REGISTRY[name]
        assert entry.arg_names == ("patch", entry.arg_names[1], "x", "y"), name
        assert entry.optional == 2, name


def test_the_emit_call_takes_the_widget_after_the_patch():
    """``PD_EMIT(p, w)``, and every part of the box it may override."""
    from crude.bindings.registry import REGISTRY

    entry = REGISTRY["PD_EMIT"]
    assert entry.arg_names == ("patch", "widget", "x", "y", "width", "height")
    assert entry.optional == 4
