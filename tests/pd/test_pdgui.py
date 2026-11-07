"""``crude/pd/pdgui.py``: the real Pd GUI box, transcribed and pinned.

The stakes here are portability, not presentation: a box ``PD_EMIT`` writes has
to open in desktop Pd as a working control. So the expected layouts are taken
from real Pd output - lines captured from the vendored ``doc`` patches, and the
slot counts read from each object's ``*_save`` in ``src`` - rather than invented.
A template that quietly gained or lost an argument is exactly the bug that would
survive every other test and then make a generated patch open wrong.

The one thing these tests do *not* pin is the cosmetic defaults (border width,
label offset, colours), because a person dragging a box picks those and they do
not affect whether it works. What is fixed is the shape: which slot each value
lands in, how a symbol is escaped, and that a kind with no vanilla template says
so instead of guessing.
"""

from __future__ import annotations

import pytest

from crude.pd.pdgui import (PD_VERSION, GuiError, arguments, box_tag,
                            format_box, has_template, kinds, sized, slots,
                            unverified)

#: every kind the table claims to lay out, with the ``*_save`` slot count read
#  straight from the Pd 0.54 source that writes it.
SOURCE_SHAPES = {
    "bng": 14,      # g_bang.c     ssiis iii sss iiii sss
    "cnv": 13,      # g_mycanvas.c ssiis iii sss iiii ss i
    "hslider": 18,  # g_slider.c   hsl w h min max log isa 3names 4ints 3colors val steady
    "nbx": 18,      # g_numbox.c   ssiis ii ff ii sss iiii sss f i
    "radio": 15,    # g_radio.c    hradio size change isa number 3names 4ints 3colors val
    "tgl": 14,      # g_toggle.c   tgl size isa 3names 4ints 3colors init nonzero
    "vu": 12,       # g_vumeter.c  vu w h rcv lab 4ints 2colors scale isa
    "vslider": 18,  # g_slider.c   same shape as hsl, vertical defaults
}

#: a real ``[hslider]`` captured from doc/7.stuff/soundfile-tools/2.band-pass.pd.
#  Our defaults (w 128, h 15, log 0, isa 0, -2/-8 label offset, 10pt, the three
#  stock colours) match it exactly, so a slider that only set a range reproduces
#  the line byte for byte - the strongest check that the slot order is right.
REAL_HSLIDER = ("hsl 128 15 24 90 0 0 empty empty empty -2 -8 0 10 "
                "#dfdfdf #000000 #000000 0 1")

#: a real ``[vu]`` from doc/7.stuff/tools/testtone.pd (send is absent by design).
REAL_VU_SHAPE = 12


# -- the table's shape --------------------------------------------------


def test_the_stamp_is_the_version_the_layouts_were_read_from():
    assert PD_VERSION == "0.54"


def test_every_kind_lays_out_the_number_of_slots_pd_saves():
    for kind, count in SOURCE_SHAPES.items():
        assert len(slots(kind)) == count, kind
        # arguments() prepends the tag, so the atom list is one longer.
        assert len(arguments(kind)) == count + 1, kind


def test_the_hslider_default_reproduces_a_real_captured_line():
    # the range is the only thing this widget set; everything else is Pd's own.
    assert format_box("hslider", {"min": 24, "max": 90}) == REAL_HSLIDER


def test_the_tags_are_the_symbols_a_saved_file_actually_holds():
    assert box_tag("hslider") == "hsl"
    assert box_tag("vslider") == "vsl"
    assert box_tag("radio") == "hradio"
    # a name that is already the save symbol maps to itself.
    assert box_tag("hradio") == "hradio"
    for tag in ("tgl", "bng", "nbx", "vu", "cnv"):
        assert box_tag(tag) == tag


def test_a_box_named_by_the_symbol_a_saved_file_holds_is_the_same_box():
    # hsl/vsl are what Pd writes and what its object menu says, so a caller that
    # read a patch - or typed the name out of Pd - gets the layout, not a miss.
    assert arguments("hsl", {"min": 24, "max": 90}) == \
        arguments("hslider", {"min": 24, "max": 90})
    assert has_template("vsl") is True
    assert box_tag("hsl") == "hsl"


def test_the_table_and_the_unverified_list_together_are_the_whole_menu():
    covered = set(kinds())
    missing = set(unverified())
    assert not (covered & missing)
    assert {"hslider", "vslider", "tgl", "bng", "nbx", "vu", "cnv", "radio"} <= covered
    # dropdown/knob are not vanilla 0.54; they must be a documented degrade, not
    # a template nobody checked.
    assert {"dropdown", "knob"} == missing


# -- the pd_name -> slot mapping ----------------------------------------


def test_a_widgets_send_receive_and_label_land_in_the_three_name_slots():
    # iemgui_save writes send, then receive, then label; a name is escaped to $-
    # literal form so Pd expands it per instance the way a hand-drawn box does.
    atoms = arguments("tgl", {"send": "$0-run", "recv": "$0-run",
                              "label": "on"})
    assert atoms[3:6] == [r"\$0-run", r"\$0-run", "on"]


def test_the_value_min_and_max_fill_their_own_box_slots():
    atoms = arguments("hslider", {"min": 20, "max": 300, "value": 120,
                                  "log": 1})
    assert atoms[3] == "20" and atoms[4] == "300"     # min, max
    assert atoms[5] == "1"                             # log
    assert atoms[-2] == "120"                          # value sits before steady


def test_a_number_box_fills_only_the_range_it_was_given_and_keeps_pd_sentinels():
    # an unset min/max stays at Pd's own unbounded sentinels.
    assert arguments("nbx", {"value": 5})[3] == "-1e+37"
    assert arguments("nbx", {"value": 5})[4] == "1e+37"
    assert arguments("nbx", {"min": 0, "max": 10, "value": 5})[3:6] == \
        ["0", "10", "0"]


def test_a_meter_has_no_send_slot_at_all():
    # the vu template literally cannot carry a send: it is read-only in Pd, so a
    # widget value must not silently invent a place for one.
    atoms = arguments("vu", {"recv": "level", "label": "L"})
    assert len(atoms) == REAL_VU_SHAPE + 1
    assert atoms[3] == "level" and atoms[4] == "L"


def test_a_radios_button_count_comes_from_the_labels_it_was_given():
    assert arguments("radio", {"labels": ["a", "b", "c"]})[4] == "3"
    assert arguments("radio", {"labels": ()})[4] == "0"
    # no labels at all falls back to the default, never a stray blank atom.
    assert arguments("radio")[4] == "0"


# -- the caller's own door: naming a slot --------------------------------
#  ``PD_GUI`` has no widget behind it, so it addresses the *shape* directly. What
#  these pin is that the props table still answers first wherever both could speak
#  (the one-table rule), and that a slot name is only consulted when no prop claims
#  the slot - which is also why a bad value arriving by that door has to be named
#  rather than raised as a Python error from inside a file write.


def test_a_slot_no_prop_has_an_opinion_about_can_still_be_given_directly():
    assert arguments("hslider", {"width": 200, "height": 20})[1:3] == ["200", "20"]
    assert arguments("tgl", {"size": 19})[1] == "19"
    # the init-and-send flag is a Pd behaviour a widget has no prop for at all
    assert arguments("tgl", {"initandsend": 1})[2] == "1"


def test_the_props_table_answers_first_when_both_name_the_same_slot():
    # a radio's button count is decided by the labels it was given, even when a
    # caller also names the slot beside them
    assert arguments("radio", {"labels": ["a", "b", "c"], "number": 7})[4] == "3"
    # ...and the slot's own name is what fills it when no prop claims it
    assert arguments("radio", {"number": 7})[4] == "7"


def test_a_value_that_is_not_a_number_names_the_slot_instead_of_crashing():
    for values, wanted in (({"min": "wide"}, "min"),
                           ({"log": "yes"}, "whole number")):
        with pytest.raises(GuiError) as caught:
            arguments("hslider", values)
        assert wanted in str(caught.value)

    with pytest.raises(GuiError, match="button count"):
        arguments("radio", {"number": "three"})


# -- geometry: a pixel box onto the size slots a box actually has --------

#: our repo's own toggle, as ``min_libpd_test.py`` holds it - a line real Pd
#  wrote, which makes it a golden for the size slot as well as for the layout.
REPO_TOGGLE = ("tgl 19 0 empty empty empty 17 7 0 10 #dfdfdf #000000 #000000 "
               "0 1")


def test_a_pixel_box_fills_both_axes_of_a_slider():
    values = sized("hslider", {"min": 20, "max": 300}, width=200, height=20)

    assert values == {"min": 20, "max": 300, "width": 200, "height": 20}
    assert format_box("hslider", values).split()[1:3] == ["200", "20"]


def test_a_square_box_takes_the_smaller_axis_as_its_side():
    # a wide GL toggle is still a 24-pixel [tgl], not a 200-pixel one
    assert sized("tgl", width=200, height=24)["size"] == 24
    assert sized("bng", width=200, height=24)["size"] == 24
    assert sized("radio", width=15)["size"] == 15


def test_a_meter_and_a_canvas_are_sized_in_pixels_on_both_axes():
    assert sized("vu", width=19, height=120) == {"width": 19, "height": 120}
    assert sized("cnv", width=160, height=44) == {"width": 160, "height": 44}


def test_a_number_box_is_given_its_height_because_its_width_is_digits():
    # my_numbox_save writes x_numwidth, a count of digits: a pixel width cannot
    # answer that without a font metric only Pd has.
    assert sized("nbx", width=60, height=20) == {"height": 20}
    # named directly, in the slot's own unit, it is the caller's business again
    assert format_box("nbx", sized("nbx", {"width": 8}, height=20)).split()[1] == "8"


def test_geometry_a_caller_already_named_is_left_alone():
    assert sized("hslider", {"width": 40}, width=200, height=20)["width"] == 40
    assert sized("tgl", {"size": 12}, width=100, height=100)["size"] == 12


def test_sizing_leaves_the_callers_own_map_untouched():
    given = {"min": 20}

    assert sized("hslider", given, width=200) == {"min": 20, "width": 200}
    assert given == {"min": 20}


def test_a_box_that_was_never_placed_has_no_size_to_fold_in():
    # 0 is a widget nobody placed, not a 0-pixel box: Pd's own default is right
    assert sized("hslider", width=0, height=0) == {}
    assert format_box("tgl", sized("tgl", width=0, height=0)) == format_box("tgl")


def test_an_impossible_size_says_which_axis_it_was():
    with pytest.raises(GuiError, match="width"):
        sized("hslider", width=-1)
    with pytest.raises(GuiError, match="height"):
        sized("vu", height="tall")
    with pytest.raises(GuiError, match="not a Pd GUI box"):
        sized("nope", width=10)


def test_sizing_a_toggle_reproduces_the_line_a_real_pd_saved():
    assert format_box("tgl", sized("tgl", width=19, height=19)) == REPO_TOGGLE


# -- symbol escaping ----------------------------------------------------


def test_a_blank_name_is_empty_and_not_an_absent_argument():
    # dropping the slot would shift every later argument; "empty" keeps the shape.
    assert format_box("tgl").split()[3:6] == ["empty", "empty", "empty"]


def test_a_name_that_has_to_stay_one_atom_escapes_its_space_and_dollar():
    assert arguments("tgl", {"label": "my label"})[5] == r"my\ label"
    assert arguments("tgl", {"send": "$0-x"})[3] == r"\$0-x"


# -- the degrade and error paths ----------------------------------------


def test_a_kind_with_no_template_says_why_rather_than_guessing():
    assert has_template("dropdown") is False
    assert has_template("knob") is False
    with pytest.raises(GuiError) as caught:
        format_box("dropdown")
    assert "no Pd 0.54 template" in str(caught.value)
    assert "vanilla" in str(caught.value)


def test_an_unknown_kind_names_the_ones_that_are():
    with pytest.raises(GuiError) as caught:
        format_box("nope")
    assert "nope" in str(caught.value)
    assert "hslider" in str(caught.value)


def test_nothing_is_a_template_and_that_is_not_an_error_to_ask():
    assert has_template("") is False
    assert has_template(None) is False


# -- a box line round-trips through the reader --------------------------


def test_an_emitted_box_reads_back_as_a_real_control_box():
    from crude.pd.reader import parse_patch

    line = format_box("tgl", {"send": "$0-run", "recv": "$0-run",
                              "label": "on"})
    text = "#N canvas 0 0 200 200 12;\n" + f"#X obj 20 20 {line};\n"
    doc = parse_patch(text)

    box = next(b for b in doc.boxes if b.kind == "obj")
    assert box.text.startswith("tgl ")
    # the reader expands \$0 back to $0, which is the whole point of escaping it.
    assert "$0-run" in box.text


def test_a_slot_of_the_wrong_shape_is_refused_when_the_table_is_built():
    # a colour default that is not #rrggbb would be written into the file as-is,
    # so it is caught here rather than in a patch that opens with a broken box.
    with pytest.raises(GuiError):
        _force_bad_color()


def _force_bad_color() -> None:
    from crude.pd import pdgui

    broken = ("tgl", (pdgui._slot("bcol", "color", "notacolour"),))
    pdgui._TEMPLATES["__broken__"] = broken
    try:
        pdgui.format_box("__broken__")
    finally:
        del pdgui._TEMPLATES["__broken__"]
