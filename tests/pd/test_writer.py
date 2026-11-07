"""Turning a document into ``.pd`` text - and the escaping that makes it safe.

The golden file is compared as text, so a change in the writer shows up as a
diff in ``tests/pd/golden/tone.pd``: that file is also what a human opens in Pd
to check what the generator is doing.
"""

from __future__ import annotations

import math
import os

import pytest

from crude.pd.model import PatchDoc, PatchError
from crude.pd.writer import (
    escape_atom,
    escape_name,
    escape_text,
    fmt_atom,
    fmt_number,
    write_box,
    write_file,
    write_patch,
    write_table_data,
)

GOLDEN = os.path.join(os.path.dirname(__file__), "golden", "tone.pd")


@pytest.fixture
def golden_text():
    with open(GOLDEN, "r", encoding="utf-8", newline="\n") as handle:
        return handle.read()


@pytest.fixture
def tone_doc():
    """The patch the golden file describes."""
    doc = PatchDoc("tone", 300, 300, 12)
    osc = doc.add_object("osc~ 440", 40, 40)
    gain = doc.add_object("*~ 0.3", 40, 80)
    dac = doc.add_object("dac~", 40, 120)
    freq = doc.add_number("freq", 40, 200, recv="freq")
    msg = doc.add_message("set $1", 160, 200)
    doc.add_table("wave", 8, 200, 40, init=[0, 0.5, 1, 0.5, 0, -0.5, -1, -0.5])
    doc.add_text("note: a ; test, $1", 200, 200)

    doc.link(osc, 0, gain, 0)
    doc.link(gain, 0, dac, 0)
    doc.link(gain, 0, dac, 1)
    doc.link(freq, 0, osc, 0)
    doc.link(msg, 0, osc, 0)
    return doc


# -- the golden ---------------------------------------------------------


def test_the_golden_patch_is_written_exactly_as_saved(tone_doc, golden_text):
    assert write_patch(tone_doc) == golden_text


def test_output_is_deterministic(tone_doc, golden_text):
    for _ in range(3):
        assert write_patch(tone_doc) == golden_text


def test_links_are_sorted_by_box_position_even_when_declared_later():
    doc = PatchDoc("tone")
    dac = doc.add_object("dac~", 10, 10)
    osc = doc.add_object("osc~ 440", 10, 60)

    doc.link(osc, 0, dac, 1)
    doc.link(osc, 0, dac, 0)

    text = write_patch(doc)
    assert text.endswith("#X connect 1 0 0 0;\n#X connect 1 0 0 1;\n")


def test_the_file_ends_with_a_newline(tone_doc):
    assert write_patch(tone_doc).endswith(";\n")


# -- escaping -----------------------------------------------------------


@pytest.mark.parametrize(
    "atom, expected",
    [
        ("wave", "wave"),
        ("$0-wave", "\\$0-wave"),
        ("a;b", "a\\;b"),
        ("a,b", "a\\,b"),
        ("a\\b", "a\\\\b"),
        ("set $1", "set \\$1"),
        ("", ""),
    ],
)
def test_escape_atom_keeps_a_symbol_literal(atom, expected):
    assert escape_atom(atom) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("wave", "wave"),
        ("$0-wave", "$0-wave"),
        ("a;b", "a\\;b"),
        ("a,b", "a\\,b"),
        ("a\\b", "a\\\\b"),
    ],
)
def test_escape_name_leaves_dollar_arguments_alone(name, expected):
    assert escape_name(name) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("osc~ 440", "osc~ 440"),
        ("set $1", "set $1"),
        ("bang", "bang"),
        ("a ; b", "a \\; b"),
        ("a,b", "a,b"),
        ("a  b", "a b"),
        ("a\\b", "a\\\\b"),
    ],
)
def test_escape_text_keeps_pd_syntax_but_not_statements(text, expected):
    """`;`` and ``\\`` are structural; ``,`` and ``$`` are Pd's own syntax."""
    assert escape_text(text) == expected


def test_the_writer_escapes_through_the_box_lines(tone_doc):
    text = write_patch(tone_doc)

    assert "#X msg 160 200 set $1;" in text
    assert "#X text 200 200 note: a \\; test, $1;" in text
    assert "#X obj 200 40 table $0-wave 8;" in text


@pytest.mark.parametrize(
    "value, expected",
    [
        (440, "440"),
        (440.0, "440"),
        (-2.5, "-2.5"),
        (0, "0"),
        (0.1, "0.1"),
        (1 / 3, "0.333333"),
        (1e9, "1000000000"),
        (1e-7, "1e-07"),
    ],
)
def test_numbers_are_written_the_way_pd_does(value, expected):
    assert fmt_number(value) == expected


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_a_number_pd_cannot_hold_is_refused(value):
    with pytest.raises(ValueError, match="not finite"):
        fmt_number(value)


def test_a_non_number_is_refused():
    with pytest.raises(ValueError, match="is not a number"):
        fmt_number("hertz")


@pytest.mark.parametrize(
    "value, expected",
    [(True, "1"), (False, "0"), (2.5, "2.5"), ("a;b", "a\\;b"), ("wave", "wave")],
)
def test_atoms_are_numbers_or_escaped_symbols(value, expected):
    assert fmt_atom(value) == expected


# -- boxes --------------------------------------------------------------


def test_every_kind_gets_its_own_line_shape():
    doc = PatchDoc("tone")
    doc.add_object("osc~ 440", 1, 2)
    doc.add_message("1 2 3", 3, 4)
    doc.add_text("hello", 5, 6)
    doc.add_number("freq", 7, 8, recv="freq", send="fb")
    doc.add_symbol("note", 9, 10)
    doc.add_graph(11, 12)
    doc.add_table("wave", 4, 13, 14)

    lines = [line for box in doc.boxes for line in write_box(box)]

    assert lines == [
        "#X obj 1 2 osc~ 440;",
        "#X msg 3 4 1 2 3;",
        "#X text 5 6 hello;",
        "#X floatatom 7 8 5 0 0 0 freq freq fb;",
        "#X symbolatom 9 10 10 0 0 0 note - -;",
        "#X obj 11 12 graph;",
        "#X obj 13 14 table $0-wave 4;",
    ]


def test_an_empty_gatom_slot_is_a_dash():
    doc = PatchDoc("tone")

    line = write_box(doc.add_number(""))[0]

    assert line.endswith("- - -;")


def test_a_table_only_writes_its_data_when_it_is_seeded():
    doc = PatchDoc("tone")
    empty = doc.add_table("empty", 4)
    seeded = doc.add_table("seeded", 4, init=[1.0, 2.0])

    assert write_table_data(empty) == []
    assert write_table_data(seeded) == ["#A 0 1 2 0 0;"]


def test_an_unknown_kind_cannot_be_written():
    doc = PatchDoc("tone")
    box = doc.add_object("osc~")
    box.kind = "mystery"

    with pytest.raises(ValueError, match="mystery"):
        write_box(box)


# -- validation and files -----------------------------------------------


def test_an_invalid_patch_is_not_written(tone_doc):
    tone_doc.add_object("")

    with pytest.raises(PatchError, match="has no text"):
        write_patch(tone_doc)


def test_validation_can_be_skipped_when_the_caller_already_did_it():
    doc = PatchDoc("tone")
    doc.add_object("", 20, 20)

    assert write_patch(doc, validate=False).endswith("#X obj 20 20 ;\n")


def test_write_file_uses_lf_endings_and_makes_directories(tone_doc, tmp_path):
    path = tmp_path / "deep" / "er" / "tone.pd"
    written = write_file(tone_doc, str(path))

    assert written == str(path)
    with open(path, "rb") as handle:
        assert handle.read() == write_patch(tone_doc).encode("utf-8")


def test_write_file_refuses_an_invalid_patch(tone_doc, tmp_path):
    tone_doc.add_message("")

    with pytest.raises(PatchError):
        write_file(tone_doc, str(tmp_path / "tone.pd"))
    assert not (tmp_path / "tone.pd").exists()


# -- verbatim text -------------------------------------------------------

#: what ``format_box("tgl", {"send": "$0-run", "recv": "$0-run"})`` lays out: a
#  GUI box in the file's own form, which is the only text that may not be escaped
#  a second time on the way out.
GUI_TOGGLE = (r"tgl 15 0 \$0-run \$0-run my\ label 17 7 0 10 #dfdfdf #000000 "
              r"#000000 1 1")


def test_a_verbatim_box_is_written_exactly_as_the_template_laid_it_out():
    from crude.pd.pdgui import format_box

    doc = PatchDoc("tone")
    line = format_box("hslider", {"min": 24, "max": 90})
    box = doc.add_gui_box(line, 40, 60)

    assert write_box(box) == [f"#X obj 40 60 {line};"]


def test_the_escaping_the_verbatim_path_avoids_would_have_doubled_the_backslash():
    """The same text, written as an ordinary object box, is not what Pd reads back."""
    doc = PatchDoc("tone")
    verbatim = doc.add_gui_box(GUI_TOGGLE, 1, 2)
    escaped = doc.add_object(GUI_TOGGLE, 1, 2)

    assert write_box(verbatim)[0] == f"#X obj 1 2 {GUI_TOGGLE};"
    # escape_text protects the backslash by adding one, which is how \$0 becomes a
    # literal backslash in front of $0 and the receiver name stops matching
    assert r"\\$0-run" in write_box(escaped)[0]
    assert r"my\ label" not in write_box(escaped)[0]


def test_a_verbatim_message_box_is_written_as_it_stands():
    doc = PatchDoc("tone")
    box = doc.add_box("msg", 1, 2, r"set \$1", raw=True)

    assert write_box(box) == [r"#X msg 1 2 set \$1;"]


def test_a_gui_box_in_a_patch_is_written_and_reads_back_as_one_line():
    """End to end on the text: the file Pd opens is what the reader parses."""
    from crude.pd.reader import parse_patch

    doc = PatchDoc("tone", 300, 300, 12)
    doc.add_gui_box(GUI_TOGGLE, 40, 40, send="$0-run", recv="$0-run",
                    label="my label")
    doc.add_object("dac~", 40, 120)
    doc.link("obj1", 0, "obj2", 0)

    text = write_patch(doc)
    assert f"#X obj 40 40 {GUI_TOGGLE};" in text

    parsed = parse_patch(text)
    box = parsed.boxes[0]
    assert box.text.startswith("tgl 15 0")
    # the reader expands the escaped dollar, which is the form a patch really holds
    assert "$0-run" in box.text
    assert "my label" in box.text
    assert len([line for line in text.splitlines() if line.startswith("#X obj")]) == 2
