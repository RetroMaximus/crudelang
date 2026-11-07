"""The authored-patch document: creation, lookup, validation and properties.

No ``.pd`` text and no libpd here - this is the model the writer, the reader and
the bindings all agree on.
"""

from __future__ import annotations

import pytest

from crude.pd.model import (
    BOX_KINDS,
    DOLLAR_ZERO,
    Link,
    PatchDoc,
    PatchError,
    ends_with_an_open_escape,
    normalize_text,
    scope_table_name,
    unescaped,
    unscope_table_name,
)


# -- names --------------------------------------------------------------


@pytest.mark.parametrize(
    "name, scoped, expected",
    [
        ("wave", True, "$0-wave"),
        ("wave", False, "wave"),
        ("$0-wave", True, "$0-wave"),
        ("$1-wave", True, "$1-wave"),
        ("", True, ""),
    ],
)
def test_table_names_are_scoped_once(name, scoped, expected):
    assert scope_table_name(name, scoped) == expected


def test_unscope_is_the_inverse():
    assert unscope_table_name("$0-wave") == "wave"
    assert unscope_table_name("wave") == "wave"
    assert unscope_table_name(scope_table_name("wave")) == "wave"


@pytest.mark.parametrize(
    "text, expected",
    [("a  b", "a b"), (" a\tb\n", "a b"), ("", ""), ("osc~ 440", "osc~ 440")],
)
def test_normalize_text_collapses_whitespace(text, expected):
    assert normalize_text(text) == expected


# -- boxes --------------------------------------------------------------


def test_a_doc_starts_empty():
    doc = PatchDoc("tone")

    assert doc.boxes == []
    assert doc.links == []
    assert (doc.canvas.width, doc.canvas.height, doc.canvas.font) == (600, 400, 12)


def test_canvas_size_can_be_given_up_front():
    doc = PatchDoc("tone", 320, 240, 10)

    assert (doc.canvas.width, doc.canvas.height, doc.canvas.font) == (320, 240, 10)


def test_box_ids_are_per_kind_and_in_creation_order():
    doc = PatchDoc("tone")
    first = doc.add_object("osc~ 440")
    second = doc.add_object("dac~")
    atom = doc.add_number("freq")

    assert (first.id, second.id, atom.id) == ("obj1", "obj2", "floatatom1")
    assert doc.boxes == [first, second, atom]


def test_a_graph_is_recognized_from_its_text():
    doc = PatchDoc("tone")

    assert doc.add_object("graph").kind == "graph"
    assert doc.add_object("osc~ 440").kind == "obj"
    assert doc.add_graph().kind == "graph"


def test_unknown_kinds_are_refused():
    doc = PatchDoc("tone")

    with pytest.raises(PatchError):
        doc.add_box("knob", 0, 0, "10")


@pytest.mark.parametrize("kind", BOX_KINDS)
def test_every_declared_kind_can_be_created(kind):
    doc = PatchDoc("tone")
    kwargs = {"size": 4} if kind == "table" else {}
    box = doc.add_box(kind, 0, 0, "wave" if kind == "table" else "text", **kwargs)

    assert box.kind == kind
    assert doc.boxes == [box]


def test_duplicate_box_ids_are_refused():
    doc = PatchDoc("tone")
    doc.add_object("osc~ 440")

    with pytest.raises(PatchError, match="already used"):
        doc.add_object("dac~", box_id="obj1")


def test_only_tables_carry_seed_data():
    doc = PatchDoc("tone")

    with pytest.raises(PatchError, match="only a table"):
        doc.add_object("osc~", init=[1.0])


def test_a_table_is_padded_or_trimmed_to_its_size():
    doc = PatchDoc("tone")
    padded = doc.add_table("wave", 4, init=[1.0])
    trimmed = doc.add_table("other", 2, init=[1.0, 2.0, 3.0])
    from_init = doc.add_table("from_init", init=[0.5, 0.25])

    assert padded.init == [1.0, 0.0, 0.0, 0.0]
    assert trimmed.init == [1.0, 2.0]
    assert (from_init.size, from_init.init) == (2, [0.5, 0.25])


def test_a_table_needs_a_size_or_data():
    doc = PatchDoc("tone")

    with pytest.raises(PatchError, match="at least 1 sample"):
        doc.add_table("wave", 0)


def test_a_table_needs_a_name():
    doc = PatchDoc("tone")

    with pytest.raises(PatchError, match="needs a name"):
        doc.add_table("", 8)


def test_tables_are_found_by_their_script_name():
    doc = PatchDoc("tone")
    box = doc.add_table("wave", 4)

    assert doc.table("wave") is box
    assert doc.table("$0-wave") is box
    assert doc.tables() == {"wave": "$0-wave"}
    with pytest.raises(PatchError, match="no table"):
        doc.table("other")


def test_seed_replaces_table_data():
    doc = PatchDoc("tone")
    doc.add_table("wave", 4)

    doc.seed("wave", [1.0, 2.0])

    assert doc.table("wave").init == [1.0, 2.0, 0.0, 0.0]


def test_seed_reports_an_unknown_table():
    doc = PatchDoc("tone")

    with pytest.raises(PatchError, match="no table"):
        doc.seed("wave", [1.0])


def test_the_grid_places_boxes_without_coordinates():
    doc = PatchDoc("tone")

    assert doc.next_position() == (20, 20)
    for index in range(5):
        doc.add_object(f"obj{index}")
    assert doc.next_position() == (20 + 140, 20 + 60)


# -- links --------------------------------------------------------------


def test_links_accept_box_ids_and_default_to_the_first_ports():
    doc = PatchDoc("tone")
    source = doc.add_object("osc~ 440")
    doc.add_object("dac~")

    assert doc.link(source, 0, "obj2") == Link("obj1", 0, "obj2", 0)
    doc.link("obj1", 0, "obj2", 1)

    assert doc.links == [Link("obj1", 0, "obj2", 0), Link("obj1", 0, "obj2", 1)]


def test_a_link_is_only_added_once():
    doc = PatchDoc("tone")
    doc.add_object("osc~ 440")
    doc.add_object("dac~")

    doc.link("obj1", 0, "obj2", 0)
    doc.link("obj1", 0, "obj2", 0)

    assert len(doc.links) == 1


def test_outlets_counts_how_far_a_box_is_wired():
    doc = PatchDoc("tone")
    doc.add_object("osc~ 440")
    doc.add_object("dac~")
    doc.link("obj1", 0, "obj2", 0)
    doc.link("obj1", 1, "obj2", 0)

    assert doc.outlets_of("obj1") == 2
    assert doc.outlets_of("obj2") == 0


def test_unlink_removes_exactly_one_connection():
    doc = PatchDoc("tone")
    doc.add_object("osc~ 440")
    doc.add_object("dac~")
    doc.link("obj1", 0, "obj2", 0)

    assert doc.unlink("obj1", 0, "obj2", 1) is False
    assert doc.unlink("obj1", 0, "obj2", 0) is True
    assert doc.links == []


def test_links_need_whole_ports():
    doc = PatchDoc("tone")
    doc.add_object("osc~ 440")
    doc.add_object("dac~")

    with pytest.raises(PatchError, match="whole numbers"):
        doc.link("obj1", "a", "obj2", 0)


def test_index_and_box_lookup_follow_the_document():
    doc = PatchDoc("tone")
    first = doc.add_object("osc~ 440")
    doc.add_object("dac~")

    assert doc.index("obj1") == 0
    assert doc.index(first) == 0
    with pytest.raises(PatchError, match="no box"):
        doc.box("obj9")


def test_a_box_from_another_patch_is_not_accepted():
    mine = PatchDoc("mine")
    other = PatchDoc("other")
    box = other.add_object("osc~ 440")

    with pytest.raises(PatchError, match="different patch"):
        mine.box(box)


# -- validation ---------------------------------------------------------


def test_validation_returns_warnings_for_a_connected_patch():
    doc = PatchDoc("tone")
    osc = doc.add_object("osc~ 440")
    dac = doc.add_object("dac~")
    doc.link(osc, 0, dac, 0)

    assert doc.validate() == []


def test_an_unconnected_object_is_a_warning_not_an_error():
    doc = PatchDoc("tone")
    doc.add_object("osc~ 440")

    assert doc.validate() == ["box 'obj1' (osc~ 440) is not connected"]


@pytest.mark.parametrize("text", ["r freq", "s tick", "print", "dbg"])
def test_boxes_that_talk_to_pd_by_name_do_not_warn(text):
    doc = PatchDoc("tone")
    doc.add_object(text)

    assert doc.validate() == []


def test_a_canvas_decoration_has_nothing_to_wire():
    """``[cnv]`` is a rectangle: PD_EMIT writes one for every ``PANEL``."""
    doc = PatchDoc("tone")
    doc.add_gui_box("cnv 1 180 90 empty empty mix 20 12 0 12 #e0e0e0 #404040 0",
                    20, 20)

    assert doc.validate() == []


def test_a_bound_atom_does_not_warn():
    doc = PatchDoc("tone")
    doc.add_number("freq", recv="freq")

    assert doc.validate() == []


@pytest.mark.parametrize(
    "add", [PatchDoc.add_object, PatchDoc.add_message, PatchDoc.add_text]
)
def test_a_box_without_text_cannot_be_serialized(add):
    doc = PatchDoc("tone")
    add(doc, "")

    with pytest.raises(PatchError, match="has no text"):
        doc.validate()


def test_two_tables_with_the_same_name_are_an_error():
    doc = PatchDoc("tone")
    doc.add_table("wave", 4)
    doc.add_table("wave", 8)

    with pytest.raises(PatchError, match="declared twice"):
        doc.validate()


def test_a_link_to_a_missing_box_is_an_error():
    doc = PatchDoc("tone")
    osc = doc.add_object("osc~ 440")
    doc.links.append(Link("obj1", 0, "obj9", 0))

    with pytest.raises(PatchError, match="no box 'obj9'"):
        doc.validate()
    assert osc.kind == "obj"


def test_a_negative_port_is_an_error():
    doc = PatchDoc("tone")
    doc.add_object("osc~ 440")
    doc.add_object("dac~")
    doc.links.append(Link("obj1", -1, "obj2", 0))

    with pytest.raises(PatchError, match=r">= 0"):
        doc.validate()


def test_errors_are_collected_into_one_message():
    doc = PatchDoc("tone")
    doc.add_object("")
    doc.add_message("")

    with pytest.raises(PatchError) as excinfo:
        doc.validate()
    assert "box 'obj1'" in str(excinfo.value)
    assert "box 'msg1'" in str(excinfo.value)


# -- script properties --------------------------------------------------


def test_box_properties_are_readable_and_writable():
    doc = PatchDoc("tone")
    box = doc.add_object("osc~ 440", 10, 20)

    assert box.get_prop("kind") == "obj"
    assert box.get_prop("x") == 10
    box.set_prop("x", 33.0)
    box.set_prop("text", "osc~   220")

    assert (box.x, box.text) == (33, "osc~ 220")


def test_a_box_refuses_unknown_and_read_only_properties():
    doc = PatchDoc("tone")
    box = doc.add_table("wave", 4, init=[1.0])

    assert box.get_prop("init") == [1.0, 0.0, 0.0, 0.0]
    box.get_prop("init").append(9.0)
    assert box.init == [1.0, 0.0, 0.0, 0.0]

    with pytest.raises(KeyError):
        box.get_prop("nope")
    with pytest.raises(KeyError):
        box.set_prop("init", [2.0])
    with pytest.raises(KeyError):
        box.set_prop("kind", "obj")


def test_patch_properties_expose_the_canvas_and_the_counts():
    doc = PatchDoc("tone")
    doc.add_object("osc~ 440")

    assert doc.get_prop("id") == "tone"
    assert doc.get_prop("width") == 600
    assert doc.get_prop("boxes") == 1
    assert doc.get_prop("links") == 0

    doc.set_prop("width", 320)
    assert doc.canvas.width == 320

    with pytest.raises(KeyError):
        doc.set_prop("boxes", 99)
    with pytest.raises(KeyError):
        doc.get_prop("nope")


# -- equality -----------------------------------------------------------


def test_link_order_is_not_part_of_equality():
    first = PatchDoc("tone")
    first.add_object("osc~ 440")
    first.add_object("dac~")
    first.link("obj1", 0, "obj2", 0)
    first.link("obj1", 1, "obj2", 0)

    second = PatchDoc("tone")
    second.add_object("osc~ 440")
    second.add_object("dac~")
    second.link("obj1", 1, "obj2", 0)
    second.link("obj1", 0, "obj2", 0)

    assert first == second
    assert first != PatchDoc("other")


def test_box_order_is_part_of_equality():
    first = PatchDoc("tone")
    first.add_object("osc~ 440")
    first.add_object("dac~")

    second = PatchDoc("tone")
    second.add_object("dac~")
    second.add_object("osc~ 440")

    assert first != second


def test_a_box_describes_itself_for_reports():
    doc = PatchDoc("tone")
    box = doc.add_number("freq", 10, 20, recv="freq")

    assert "floatatom" in box.describe()
    assert "at 10,20" in box.describe()
    assert "label 'freq'" in box.describe()
    assert "recv 'freq'" in box.describe()


def test_repr_mentions_the_document():
    doc = PatchDoc("tone")
    doc.add_object("osc~ 440")

    assert "tone" in repr(doc)
    assert "1 boxes" in repr(doc)
    assert DOLLAR_ZERO == "$0-"
    assert "osc~ 440" in repr(doc.boxes[0])


# -- verbatim text (a Pd GUI box) ---------------------------------------
#  A GUI box's argument string is written by crude/pd/pdgui.py in the file's own
#  form, so the document has to hold it without touching it. What is being pinned
#  here is that touching it is impossible on the way in (no whitespace collapsing,
#  so an escaped space survives) and unsafe on the way out (validate is what stops
#  a hand-written verbatim line from ending its own statement early).

A_TOGGLE = (r"tgl 15 0 \$0-run \$0-run my\ label 17 7 0 10 "
            r"#dfdfdf #000000 #000000 1 1")


def test_a_verbatim_box_keeps_its_text_exactly():
    doc = PatchDoc("tone")

    box = doc.add_gui_box(A_TOGGLE, 20, 30, send="$0-run", recv="$0-run",
                          label="my label")

    assert box.raw is True
    assert box.kind == "obj"
    # the escaped space and the \$ are the text: collapsing them would split the
    # label into two atoms and hand Pd a literal backslash
    assert box.text == A_TOGGLE
    # the names the box carries inside its arguments are also on the box, so
    # validate and PD_LOAD's receiver probe can see who it talks to
    assert (box.send, box.recv, box.label) == ("$0-run", "$0-run", "my label")


def test_an_ordinary_box_still_collapses_its_whitespace():
    doc = PatchDoc("tone")

    assert doc.add_object("osc~   440").text == "osc~ 440"


@pytest.mark.parametrize("kind", ["table", "floatatom", "text", "graph"])
def test_only_a_box_whose_whole_payload_is_text_can_be_verbatim(kind):
    doc = PatchDoc("tone")

    with pytest.raises(PatchError, match="cannot be written verbatim"):
        doc.add_box(kind, 0, 0, "wave", size=4, raw=True)


def test_editing_a_verbatim_box_does_not_re_escape_or_collapse_it():
    doc = PatchDoc("tone")
    box = doc.add_gui_box("tgl 15 0 empty empty empty", send="x")

    box.set_prop("text", f"  {A_TOGGLE}  ")

    assert box.text == A_TOGGLE


def test_a_second_statement_on_a_verbatim_line_is_refused():
    doc = PatchDoc("tone")
    doc.add_gui_box("tgl 15 0 empty empty empty; osc~ 440", recv="x")

    with pytest.raises(PatchError, match="unescaped ';'"):
        doc.validate()


def test_a_verbatim_box_has_to_stay_on_one_line():
    doc = PatchDoc("tone")
    doc.add_gui_box("tgl 15 0 empty empty empty\n#X obj 0 0 dac~", recv="x")

    with pytest.raises(PatchError, match="one line"):
        doc.validate()


def test_a_trailing_open_escape_is_refused():
    # the last backslash would escape the ';' the writer appends, joining the
    # next box's line to this one
    doc = PatchDoc("tone")
    doc.add_gui_box("tgl 15 0 empty empty label\\", recv="x")

    with pytest.raises(PatchError, match="unterminated escape"):
        doc.validate()


def test_an_escaped_semicolon_in_a_label_is_not_a_second_statement():
    doc = PatchDoc("tone")
    doc.add_gui_box(r"tgl 15 0 recv1 empty a\;b 17 7 0 10 #dfdfdf #000000 "
                    r"#000000 1 1", recv="recv1")

    assert doc.validate() == []


@pytest.mark.parametrize(
    "text, expected",
    [("a;b", True), (r"a\;b", False), (r"a\\;b", True), ("abc", False)],
)
def test_unescaped_finds_only_a_character_that_is_not_part_of_an_escape(text,
                                                                        expected):
    assert unescaped(text, ";") is expected


@pytest.mark.parametrize(
    "text, expected",
    [("abc", False), ("abc\\", True), ("abc\\\\", False)],
)
def test_ends_with_an_open_escape_counts_the_trailing_backslashes(text, expected):
    assert ends_with_an_open_escape(text) is expected
