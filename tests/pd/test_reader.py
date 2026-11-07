"""Reading ``.pd`` text back into a document - the tolerant half of the stack.

The reader has two jobs: understand everything the writer emits (so
``parse(write(doc)) == doc`` holds, which is what the build cache relies on) and
explain a hand-written patch well enough to be useful for ``crude pd show``.
Nothing here should raise; anything it cannot model becomes a warning.
"""

from __future__ import annotations

import pytest

from crude.pd.model import Link, PatchDoc
from crude.pd.reader import (
    describe,
    parse_patch,
    read_file,
    split_atoms,
    unescape,
)
from crude.pd.writer import write_patch


# -- helpers ------------------------------------------------------------


def build_tone_doc() -> PatchDoc:
    """A patch that exercises every box kind the writer knows."""
    doc = PatchDoc("tone", 300, 300, 12)
    osc = doc.add_object("osc~ 440", 40, 40)
    gain = doc.add_object("*~ 0.3", 40, 80)
    dac = doc.add_object("dac~", 40, 120)
    freq = doc.add_number("freq", 40, 200, recv="freq")
    doc.add_message("set $1", 160, 200)
    doc.add_table("wave", 8, 200, 40, init=[0, 0.5, 1, 0.5, 0, -0.5, -1, -0.5])
    doc.add_text("note: a ; test, $1", 200, 200)
    doc.link(osc, 0, gain, 0)
    doc.link(gain, 0, dac, 0)
    doc.link(gain, 0, dac, 1)
    doc.link(freq, 0, osc, 0)
    return doc


def kinds(doc):
    return [box.kind for box in doc.boxes]


# -- the atom level -----------------------------------------------------


def test_unescape_gives_a_literal_atom_back():
    assert unescape("a\\;b") == "a;b"
    assert unescape("\\$0-wave") == "$0-wave"
    assert unescape("plain") == "plain"
    assert unescape("a\\\\b") == "a\\b"


def test_a_dangling_backslash_is_kept_rather_than_dropped():
    assert unescape("ends with one \\") == "ends with one \\"


def test_split_atoms_splits_on_whitespace_only():
    assert split_atoms("obj 40 40 osc~ 440") == ["obj", "40", "40", "osc~", "440"]
    assert split_atoms("a   b") == ["a", "b"]
    assert split_atoms("") == []


def test_an_escaped_space_stays_inside_its_atom():
    assert split_atoms("msg 1 hi\\ there") == ["msg", "1", "hi there"]


def test_split_atoms_unescapes_as_it_splits():
    assert split_atoms("text 20 note: a \\; b") == ["text", "20", "note:", "a", ";", "b"]


# -- the header ---------------------------------------------------------


def test_the_first_canvas_line_describes_the_patch(patch_text):
    doc = parse_patch(patch_text, "tone")

    assert doc.id == "tone"
    assert (doc.canvas.width, doc.canvas.height, doc.canvas.font) == (300, 300, 12)
    assert doc.warnings == []


def test_a_canvas_line_without_numbers_leaves_the_defaults():
    doc = parse_patch("#N canvas;\n#X obj 1 2 osc~ 440;\n#X obj 3 4 dac~;\n")

    assert (doc.canvas.width, doc.canvas.height, doc.canvas.font) == (600, 400, 12)
    assert len(doc.boxes) == 2


def test_a_header_that_is_not_a_canvas_is_a_warning():
    doc = parse_patch("#N obj 1 2;\n")

    assert doc.warnings == ["line 1: ignored '#N obj'"]


def test_text_that_is_not_a_directive_is_reported():
    doc = parse_patch("#N canvas 0 0 10 10 12;\nhello world;\n")

    assert doc.warnings == ["line 2: ignored text 'hello world'"]
    assert doc.boxes == []


def test_an_unknown_directive_tag_is_reported():
    doc = parse_patch("#B foo 1 2;\n")

    assert doc.warnings == ["line 1: ignored '#B'"]


# -- boxes --------------------------------------------------------------


def test_boxes_keep_the_file_order(patch_text):
    doc = parse_patch(patch_text, "tone")

    assert [box.id for box in doc.boxes] == [
        "obj1", "obj2", "obj3", "obj4", "obj5", "table1",
    ]
    assert [box.text for box in doc.boxes] == [
        "r freq", "osc~ 440", "*~ 0.3", "dac~", "s tick", "wave",
    ]
    assert (doc.boxes[1].x, doc.boxes[1].y) == (40, 80)


def test_message_text_boxes_and_comments(patch_text):
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X msg 1 2 set 440;\n"
                      "#X text 3 4 a comment here;\n")

    assert kinds(doc) == ["msg", "text"]
    assert doc.boxes[0].text == "set 440"
    assert doc.boxes[1].text == "a comment here"


def test_a_graph_is_recognized_from_its_object_line():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n#X obj 11 12 graph;\n")

    assert doc.boxes[0].kind == "graph"
    assert doc.boxes[0].text == ""


def test_a_table_line_becomes_a_table_of_the_right_length():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n#X obj 20 20 table wave 16;\n")

    box = doc.boxes[0]
    assert box.kind == "table"
    assert box.text == "wave"
    assert box.size == 16
    assert box.init == [0.0] * 16


def test_a_table_without_a_size_is_one_sample():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n#X obj 20 20 table wave;\n")

    assert doc.boxes[0].size == 1


def test_the_escaped_dollar_of_a_scoped_table_name_is_stripped():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n#X obj 20 20 table \\$0-wave 8;\n")

    assert doc.boxes[0].text == "$0-wave"
    assert doc.tables() == {"wave": "$0-wave"}


def test_a_table_with_extra_arguments_is_an_ordinary_object():
    """``[table a b c]`` is a real Pd object, not an array declaration."""
    doc = parse_patch("#N canvas 0 0 10 10 12;\n#X obj 20 20 table wave 8 extra;\n")

    assert doc.boxes[0].kind == "obj"
    assert doc.boxes[0].text == "table wave 8 extra"


def test_a_gatom_fills_its_label_receive_and_send_slots():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X floatatom 40 200 5 0 0 0 freq freq fb;\n"
                      "#X symbolatom 60 200 10 0 0 0 note - -;\n")

    number, symbol = doc.boxes
    assert (number.kind, number.label, number.recv, number.send) == (
        "floatatom", "freq", "freq", "fb",
    )
    assert (symbol.kind, symbol.label, symbol.recv, symbol.send) == (
        "symbolatom", "note", "", "",
    )


def test_a_gatom_without_slots_is_just_a_box():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n#X floatatom 40 200;\n")

    box = doc.boxes[0]
    assert (box.kind, box.x, box.y) == ("floatatom", 40, 200)
    assert (box.label, box.recv, box.send) == ("", "", "")


def test_a_box_line_without_coordinates_is_a_warning():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n#X obj 20;\n#X text;\n")

    assert doc.boxes == []
    assert doc.warnings == [
        "line 2: '#X obj' has no coordinates",
        "line 3: '#X text' has no coordinates",
    ]


# -- array data ---------------------------------------------------------


def test_array_lines_seed_the_table_above_them():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X obj 20 20 table wave 4;\n"
                      "#A 0 1 2 3 4;\n")

    assert doc.boxes[0].init == [1.0, 2.0, 3.0, 4.0]


def test_array_data_can_start_part_way_into_a_table():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X obj 20 20 table wave 4;\n"
                      "#A 0 0 0 0 0;\n"
                      "#A 2 9;\n")

    assert doc.boxes[0].init == [0.0, 0.0, 9.0, 0.0]


def test_array_data_is_clipped_to_the_table():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X obj 20 20 table wave 4;\n"
                      "#A 2 1 2 3 4 5;\n")

    assert doc.boxes[0].init == [0.0, 0.0, 1.0, 2.0]


def test_array_data_outside_the_table_is_a_warning():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X obj 20 20 table wave 4;\n"
                      "#A 6 1 2;\n")

    assert doc.boxes[0].init == [0.0] * 4
    assert doc.warnings == ["line 3: '#A' onset 6 is outside table 'wave'"]


def test_array_data_needs_a_number():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X obj 20 20 table wave 4;\n"
                      "#A 0 1 two;\n")

    assert doc.boxes[0].init == [0.0] * 4
    assert doc.warnings == ["line 3: '#A' atom 'two' is not a number"]


def test_array_data_without_a_table_is_a_warning():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n#A 0 1 2;\n")

    assert doc.warnings == ["line 2: '#A' data without a table"]


def test_an_empty_array_line_is_a_warning():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X obj 20 20 table wave 4;\n"
                      "#A;\n")

    assert doc.warnings == ["line 3: '#A' line without an onset"]


def test_an_object_line_between_a_table_and_its_data_clears_the_link():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X obj 20 20 table one 2;\n"
                      "#X obj 20 40 table two 2;\n"
                      "#X obj 20 60 osc~ 440;\n"
                      "#A 0 1 1;\n")

    assert [box.init for box in doc.boxes] == [[0.0, 0.0], [0.0, 0.0], []]
    assert doc.warnings == ["line 5: '#A' data without a table"]


# -- links --------------------------------------------------------------


def test_connect_lines_are_read_as_links_by_position(patch_text):
    doc = parse_patch(patch_text, "tone")

    assert doc.links == [
        Link("obj2", 0, "obj3", 0),
        Link("obj3", 0, "obj4", 0),
        Link("obj3", 0, "obj4", 1),
    ]
    assert doc.outlets_of("obj2") == 1


def test_a_link_to_a_box_that_is_not_there_is_a_warning():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X obj 1 2 osc~ 440;\n"
                      "#X connect 0 0 9 0;\n")

    assert doc.links == []
    assert doc.warnings == [
        "line 3: '#X connect 0 0 9 0' refers to a missing box"
    ]


def test_a_short_connect_line_is_a_warning():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X obj 1 2 osc~ 440;\n"
                      "#X connect 0 0 0;\n")

    assert doc.warnings == ["line 3: '#X connect' needs four numbers"]


# -- what is skipped ----------------------------------------------------


def test_a_subpatch_is_skipped_up_to_its_restore():
    doc = parse_patch("#N canvas 0 0 300 300 12;\n"
                      "#X obj 10 10 osc~ 440;\n"
                      "#N canvas 30 30 450 270 10;\n"
                      "#X obj 10 10 r inner;\n"
                      "#X restore 10 40 pd inner;\n"
                      "#X obj 20 20 dac~;\n")

    assert [box.text for box in doc.boxes] == ["osc~ 440", "dac~"]
    assert doc.canvas.width == 300
    assert doc.warnings == []


def test_data_inside_a_subpatch_is_not_applied_to_the_parent():
    doc = parse_patch("#N canvas 0 0 300 300 12;\n"
                      "#N canvas 30 30 450 270 10;\n"
                      "#X obj 10 10 table inner 4;\n"
                      "#A 0 1 2 3 4;\n"
                      "#X restore 10 40 pd inner;\n")

    assert doc.boxes == []


def test_a_restore_without_a_subpatch_is_a_warning():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n#X restore 10 40 pd nope;\n")

    assert doc.warnings == ["line 2: '#X restore' without a subpatch"]


def test_coordinates_are_skipped_without_a_word():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X obj 1 2 osc~ 440;\n"
                      "#X coords 0 -1 1 1 85 60 1 0 0;\n")

    assert len(doc.boxes) == 1
    assert doc.warnings == []


@pytest.mark.parametrize(
    "line, message",
    [
        ("#X declare -lib moe;", "skipped '#X declare' (not modelled)"),
        ("#X array wave float 8;", "skipped '#X array' (not modelled)"),
        ("#X listbox 20 20 0 0 0 0 - -;", "skipped '#X listbox' (not modelled)"),
        ("#X pop;", "skipped '#X pop' (not modelled)"),
        ("#X vradio 20 1 0 90 x y;", "unknown '#X vradio' (skipped)"),
        ("#X;", "empty '#X' line"),
    ],
)
def test_constructs_that_are_not_modelled_become_warnings(line, message):
    doc = parse_patch("#N canvas 0 0 10 10 12;\n" + line + "\n")

    assert doc.warnings == [f"line 2: {message}"]


# -- round-tripping -----------------------------------------------------


def test_an_authored_patch_reads_back_as_itself():
    doc = build_tone_doc()

    assert parse_patch(write_patch(doc), doc.id) == doc


def test_reading_an_authored_patch_produces_the_same_text():
    doc = build_tone_doc()
    text = write_patch(doc)

    assert write_patch(parse_patch(text, doc.id)) == text


def test_a_hand_written_patch_survives_a_read_write_read(patch_text):
    first = parse_patch(patch_text, "tone")
    second = parse_patch(write_patch(first), "tone")

    assert first == second
    assert second.warnings == []


def test_the_reader_does_not_re_scope_a_name_it_read():
    """``table wave`` from a hand-written file must not become ``$0-wave``."""
    doc = parse_patch("#N canvas 0 0 10 10 12;\n#X obj 20 20 table wave 4;\n")

    assert "#X obj 20 20 table wave 4;" in write_patch(doc, validate=False)


# -- files and reports --------------------------------------------------


def test_read_file_takes_the_patch_id_from_the_path(patch_file):
    doc = read_file(str(patch_file))

    assert doc.id == "tone.pd"
    assert len(doc.boxes) == 6
    assert read_file(str(patch_file), "manual").id == "manual"


def test_describe_lists_the_boxes_links_and_warnings(patch_text):
    doc = parse_patch(patch_text + "#X connect 0 0 7 0;\n", "tone")
    lines = describe(doc)

    assert lines[0] == "patch tone: canvas 300x300 font 12"
    assert lines[1] == "6 boxes, 3 links"
    assert lines[2].startswith("[0] obj")
    assert "'r freq'" in lines[2]
    assert "at 40,40" in lines[2]
    assert "    1:0 -> 2:0" in lines
    assert lines[-1] == (
        "warning: line 11: '#X connect 0 0 7 0' refers to a missing box"
    )


def test_describe_shows_what_a_table_holds():
    doc = parse_patch("#N canvas 0 0 10 10 12;\n"
                      "#X obj 20 20 table wave 4;\n"
                      "#A 0 1 2 3 4;\n", "wave")

    assert "size 4" in describe(doc)[2]


def test_describe_numbers_boxes_by_their_position_in_the_file():
    doc = PatchDoc("tone")
    doc.add_object("osc~ 440", 10, 10)
    doc.add_object("dac~", 10, 40)
    doc.link("obj1", 0, "obj2", 1)

    assert describe(doc)[-1] == "    0:0 -> 1:1"
