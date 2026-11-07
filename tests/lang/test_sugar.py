"""S12: the Tier B sugar must add grammar and nothing else.

Every test here pins one half of the plan's S12 acceptance line: the parser
accepts the ``PATCH``/``WIDGET``/``ON``/``[i]`` forms, and each desugars to
*exactly* the AST the Tier A spelling builds - same node classes, same call
names, same argument order, so the interpreter, the bindings and the runtime
cannot tell the two scripts apart.
"""

from __future__ import annotations

from crude.lang import (
    BinOpNode,
    BreakNode,
    CallNode,
    ContinueNode,
    ForNode,
    FuncDefNode,
    IfNode,
    Lexer,
    ListNode,
    NumberNode,
    Parser,
    ReturnNode,
    StringNode,
    UnaryOpNode,
    VarAccessNode,
    VarAssignNode,
    WhileNode,
)
from crude.lang.parser import ON_EVENTS, PATCH_ITEMS, WIDGET_ITEMS
from crude.lang.tokens import TT_IDENTIFIER, TT_KEYWORD


def parse(text, fn="<sugar>"):
    tokens, error = Lexer(fn, text).make_tokens()
    assert error is None
    result = Parser(tokens).parse()
    assert result.error is None, result.error.as_string()
    return result.node


def parse_error(text):
    tokens, error = Lexer("<sugar>", text).make_tokens()
    assert error is None, error.as_string()
    result = Parser(tokens).parse()
    assert result.error is not None, f"expected a syntax error, got {result.node!r}"
    return result.error


def first_statement(text):
    return parse(text).element_nodes[0]


def plain(value):
    """A runtime value all the way down to plain Python, for run assertions."""
    elements = getattr(value, "elements", None)
    if elements is not None:
        return [plain(item) for item in elements]
    raw = getattr(value, "value", value)
    return raw


def shape(node):
    """An AST as a position-free tuple, for the equivalence assertions.

    Source spans differ between the two spellings by construction (the sugar
    synthesises tokens); everything else - node class, token value, argument
    order - has to match exactly, which is what this normalises to.
    """
    if node is None:
        return None
    if isinstance(node, NumberNode):
        return ("num", node.tok.value)
    if isinstance(node, StringNode):
        return ("str", node.tok.value)
    if isinstance(node, VarAccessNode):
        return ("var", node.var_name_tok.value)
    if isinstance(node, VarAssignNode):
        return ("assign", node.var_name_tok.value, shape(node.value_node))
    if isinstance(node, CallNode):
        return ("call", shape(node.node_to_call),
                [shape(arg) for arg in node.arg_nodes])
    if isinstance(node, ListNode):
        return ("list", [shape(item) for item in node.element_nodes])
    if isinstance(node, BinOpNode):
        return ("binop", node.op_tok.type,
                shape(node.left_node), shape(node.right_node))
    if isinstance(node, UnaryOpNode):
        return ("unop", node.op_tok.type, shape(node.node))
    if isinstance(node, FuncDefNode):
        name = node.var_name_tok.value if node.var_name_tok else None
        return ("func", name, [tok.value for tok in node.arg_name_toks],
                shape(node.body_node), node.should_auto_return)
    if isinstance(node, IfNode):
        return ("if", [(shape(c), shape(b)) for c, b, _ in node.cases],
                shape(node.else_case[0]) if node.else_case else None)
    if isinstance(node, ForNode):
        return ("for", node.var_name_tok.value, shape(node.start_value_node),
                shape(node.end_value_node), shape(node.step_value_node),
                shape(node.body_node))
    if isinstance(node, WhileNode):
        return ("while", shape(node.condition_node), shape(node.body_node))
    if isinstance(node, ReturnNode):
        return ("return", shape(node.node_to_return))
    if isinstance(node, ContinueNode):
        return ("continue",)
    if isinstance(node, BreakNode):
        return ("break",)
    raise AssertionError(f"unexpected node {node!r}")


def desugared(text):
    """Parse a program whose only statement is a block, return the block's shape."""
    block = first_statement(text)
    assert isinstance(block, ListNode), \
        "the block must desugar to a statement list, not a new node type"
    return shape(block)


# -- the words stay identifiers ------------------------------------------

def test_the_sugar_words_are_not_keywords():
    """The lexer is untouched: Tier A calls that share names keep working."""
    tokens, error = Lexer("<sugar>", "TEXT AT SET WIDGET NUM LINK PATCH").make_tokens()
    assert error is None
    assert all(token.type == TT_IDENTIFIER for token in tokens[:-1])  # minus EOF


def test_tier_a_calls_still_parse_as_calls():
    node = first_statement('TEXT("label")')
    assert isinstance(node, CallNode)
    assert node.node_to_call.var_name_tok.value == "TEXT"

    node = first_statement('VAR w = WIDGET("stepper")')
    assert isinstance(node, VarAssignNode)
    assert isinstance(node.value_node, CallNode)

    node = first_statement("AT(spec, 0)")
    assert isinstance(node, CallNode)
    assert node.node_to_call.var_name_tok.value == "AT"


def test_var_and_if_are_still_keyword_statements():
    """The pre-S12 keyword table did not grow either."""
    assert isinstance(first_statement("VAR x = 1"), VarAssignNode)
    assert isinstance(first_statement("IF a THEN 1"), IfNode)


# -- PATCH ... END --------------------------------------------------------

PATCH_PLAN = """
PATCH synth
    CANVAS 900 600
    OBJ osc "osc~ 440" AT 40 40
    OBJ amp "*~ 0.3" AT 40 120 ID amp
    MSG m "440" AT 200 40
    NUM freq AT 200 100
    TABLE wave 1024 AT 500 40
    TEXT "simple tone" AT 40 10
    LINK m -> osc, amp -> OUT
END
"""

PATCH_TWIN = """
VAR synth = PD_BUILD("synth", 900, 600)
VAR osc = PD_OBJ(synth, "osc~ 440", 40, 40)
VAR amp = PD_OBJ(synth, "*~ 0.3", 40, 120)
VAR m = PD_MSG(synth, "440", 200, 40)
VAR freq = PD_NUM(synth, "freq", 200, 100)
VAR wave = PD_TABLE(synth, "wave", 1024, 500, 40)
PD_TEXT(synth, "simple tone", 40, 10)
PD_LINK(synth, m, osc)
VAR dac = PD_OBJ(synth, "dac~", 0, 0)
PD_LINK(synth, amp, dac)
"""


def test_the_plan_s_patch_block_desugars_to_the_plan_s_calls():
    assert desugared(PATCH_PLAN) == shape(parse(PATCH_TWIN))


def test_a_patch_without_canvas_omits_the_size_arguments():
    assert desugared('PATCH tone\n    OBJ o "osc~ 220"\nEND') == shape(
        parse('VAR tone = PD_BUILD("tone")\nVAR o = PD_OBJ(tone, "osc~ 220")'))


def test_canvas_may_come_after_the_items_it_sizes():
    assert desugared('PATCH p2\n    OBJ o "osc~" AT 1 2\n    CANVAS 300 400\nEND') == shape(
        parse('VAR p2 = PD_BUILD("p2", 300, 400)\nVAR o = PD_OBJ(p2, "osc~", 1, 2)'))


def test_canvas_font_is_the_third_pd_build_argument():
    assert desugared("PATCH p3\n    CANVAS 300 400 24\nEND") == shape(
        parse('VAR p3 = PD_BUILD("p3", 300, 400, 24)'))


def test_link_chains_walk_one_arrow_at_a_time():
    assert desugared('PATCH p4\n    OBJ a "in"\n    OBJ b "mid"\n    OBJ c "out"\n'
                     "    LINK a -> b -> c\nEND") == shape(
        parse('VAR p4 = PD_BUILD("p4")\nVAR a = PD_OBJ(p4, "in")\n'
              'VAR b = PD_OBJ(p4, "mid")\nVAR c = PD_OBJ(p4, "out")\n'
              "PD_LINK(p4, a, b)\nPD_LINK(p4, b, c)"))


def test_in_and_out_each_make_their_box_once_per_patch():
    assert desugared('PATCH p5\n    OBJ g "osc~"\n    LINK IN -> g, g -> OUT, g -> OUT\nEND') == shape(
        parse('VAR p5 = PD_BUILD("p5")\nVAR g = PD_OBJ(p5, "osc~")\n'
              'VAR adc = PD_OBJ(p5, "adc~", 0, 0)\nPD_LINK(p5, adc, g)\n'
              'VAR dac = PD_OBJ(p5, "dac~", 0, 0)\nPD_LINK(p5, g, dac)\n'
              "PD_LINK(p5, g, dac)"))


def test_symb_is_pd_symb_and_binds_its_name():
    assert desugared('PATCH p6\n    SYMB who AT 5 6\nEND') == shape(
        parse('VAR p6 = PD_BUILD("p6")\nVAR who = PD_SYMB(p6, "who", 5, 6)'))


def test_id_may_name_an_item_the_lead_left_anonymous():
    assert desugared('PATCH p7\n    OBJ "bng" AT 1 2 ID hit\nEND') == shape(
        parse('VAR p7 = PD_BUILD("p7")\nVAR hit = PD_OBJ(p7, "bng", 1, 2)'))


def test_a_one_line_patch_is_still_a_patch():
    assert desugared("PATCH p8 END") == shape(parse('VAR p8 = PD_BUILD("p8")'))


def test_semicolons_separate_patch_items_like_any_other_statement():
    assert desugared('PATCH p9; OBJ o "x" AT 1 2; END') == shape(
        parse('VAR p9 = PD_BUILD("p9")\nVAR o = PD_OBJ(p9, "x", 1, 2)'))


# -- PATCH syntax errors ---------------------------------------------------

def test_a_patch_that_never_ends_is_refused():
    assert "Expected 'END'" in parse_error("PATCH q\n    OBJ o \"x\"\n").details


def test_an_unknown_item_word_names_the_ones_that_exist():
    error = parse_error("PATCH q\n    FROB o\nEND")
    assert "FROB" in error.details or "OBJ" in error.details


def test_id_must_agree_with_the_name_the_line_carries():
    assert "ID" in parse_error('PATCH q\n    OBJ o "x" AT 1 2 ID other\nEND').details


def test_a_link_needs_someone_on_both_sides_of_the_arrow():
    parse_error("PATCH q\n    OBJ o \"x\"\n    LINK o\nEND")


def test_link_endpoints_must_be_names():
    parse_error('PATCH q\n    OBJ o "x"\n    LINK o -> "dac~"\nEND')


# -- WIDGET ... END ---------------------------------------------------------

WIDGET_PLAN = """
WIDGET k = KNOB("Pitch", 20, 2000, 440)
    AT 24 64 SIZE 96 96
    SET "bipolar" 1
    ADD panel
    LINK_VALUE other
    ON CHANGE on_pitch
    ON CLICK FUNC() -> PRINT("hit")
END
"""

WIDGET_TWIN = """
VAR k = KNOB("Pitch", 20, 2000, 440)
PLACE(k, 24, 64, 96, 96)
SET(k, "bipolar", 1)
NODE_ADD(panel, k)
LINK_VALUE(k, other)
ON_CHANGE(k, on_pitch)
ON_CLICK(k, FUNC() -> PRINT("hit"))
"""


def test_the_widget_block_desugars_line_by_line_in_order():
    assert desugared(WIDGET_PLAN) == shape(parse(WIDGET_TWIN))


def test_at_without_size_is_the_two_argument_place():
    assert desugared('WIDGET w2 = SLIDER("s", 0, 1, 0)\n    AT 8 9\nEND') == shape(
        parse('VAR w2 = SLIDER("s", 0, 1, 0)\nPLACE(w2, 8, 9)'))


def test_link_state_names_the_property_it_mirrors():
    assert desugared('WIDGET w3 = TOGGLE("t")\n    LINK_STATE "visible" other\nEND') == shape(
        parse('VAR w3 = TOGGLE("t")\nLINK_STATE(w3, "visible", other)'))


def test_on_frame_inside_a_block_keeps_the_global_one_argument_form():
    assert desugared("WIDGET w4 = TEXT(\"t\")\n    ON FRAME tick\nEND") == shape(
        parse('VAR w4 = TEXT("t")\nON_FRAME(tick)'))


def test_a_widget_block_may_close_on_its_open_line():
    assert desugared("WIDGET w5 = TEXT(\"t\") END") == shape(
        parse('VAR w5 = TEXT("t")'))


def test_widget_blocks_live_inside_control_flow_like_any_statement():
    node = first_statement('IF a THEN\nWIDGET w = TEXT("t")\n    AT 1 2\nEND\nEND')
    assert isinstance(node, IfNode)


def test_size_without_at_is_refused():
    assert "AT" in parse_error('WIDGET w = TEXT("t")\n    SIZE 4 4\nEND').details


def test_an_unknown_widget_item_word_is_refused():
    parse_error('WIDGET w = TEXT("t")\n    SHRINK 4\nEND')


def test_an_unknown_event_word_names_the_events_that_exist():
    error = parse_error('WIDGET w = TEXT("t")\n    ON TELETEXT fn\nEND')
    assert "event" in error.details.lower()


# -- ON w EVENT fn ------------------------------------------------------------

def test_the_on_line_is_the_registration_call_it_replaces():
    assert shape(first_statement("ON k CHANGE fn")) == shape(
        first_statement("ON_CHANGE(k, fn)"))

    assert shape(first_statement("ON k PAINT draw")) == shape(
        first_statement("ON_PAINT(k, draw)"))

    assert shape(first_statement("ON w FRAME tick")) == shape(
        first_statement("ON_FRAME(tick)"))


def test_the_on_line_accepts_an_anonymous_handler():
    assert shape(first_statement('ON k CLICK FUNC(x) -> PRINT(x)')) == shape(
        first_statement('ON_CLICK(k, FUNC(x) -> PRINT(x))'))


def test_on_must_be_followed_by_a_real_event():
    assert "event" in parse_error("ON k HUM fn").details.lower()


def test_the_on_words_match_the_events_the_ui_tree_actually_fires():
    """``crude.ui`` owns the event list; the parser mirrors it without importing
    it (``crude.lang`` must stay host-agnostic), so this pins the two together."""
    from crude.ui.handlers import EVENTS as UI_EVENTS

    assert {event.upper() for event in UI_EVENTS} == set(ON_EVENTS)


def test_the_block_item_words_are_all_plain_identifiers():
    for word in PATCH_ITEMS + WIDGET_ITEMS + ("PATCH", "IN", "OUT", "ID",
                                              "SIZE", "OF", "END"):
        tokens, error = Lexer("<sugar>", word).make_tokens()
        assert error is None
        if word == "END":
            assert tokens[0].matches(TT_IDENTIFIER, word) or \
                tokens[0].matches(TT_KEYWORD, word)
        else:
            assert tokens[0].type == TT_IDENTIFIER


# -- the [i] postfix ------------------------------------------------------------

def test_subscript_is_the_at_call_the_plan_says():
    assert shape(first_statement("spec[i]")) == shape(first_statement("AT(spec, i)"))
    assert shape(first_statement("a[i][j]")) == shape(first_statement("AT(AT(a, i), j)"))
    assert shape(first_statement("f(x)[0]")) == shape(first_statement("AT(f(x), 0)"))
    assert shape(first_statement("[1, 2][0]")) == shape(first_statement("AT([1, 2], 0)"))


def test_subscript_assignment_is_set_at():
    assert shape(first_statement("spec[i] = 5")) == shape(
        first_statement("SET_AT(spec, i, 5)"))
    assert shape(first_statement("a[i][j] = v")) == shape(
        first_statement("SET_AT(AT(a, i), j, v)"))


def test_the_index_still_takes_full_expressions():
    assert shape(first_statement("a[i + 1]")) == shape(first_statement("AT(a, i + 1)"))


def test_equals_after_anything_else_is_still_a_syntax_error():
    parse_error("x = 5")
    parse_error("a[0] == 1 = 2")


def test_calls_chain_now_that_postfix_loops():
    assert shape(first_statement("f(1)(2)")) == (
        "call", ("call", ("var", "f"), [("num", 1)]), [("num", 2)])


# -- no new semantics --------------------------------------------------------

NODE_TYPES = (
    NumberNode, StringNode, VarAccessNode, VarAssignNode, CallNode, ListNode,
    BinOpNode, UnaryOpNode, FuncDefNode, IfNode, ForNode, WhileNode,
    ReturnNode, ContinueNode, BreakNode,
)


def _node_classes(node, found):
    if isinstance(node, NODE_TYPES):
        found.add(type(node))
    for attr in ("element_nodes", "arg_nodes"):
        for child in getattr(node, attr, None) or []:
            _node_classes(child, found)
    for attr in ("value_node", "left_node", "right_node", "node", "body_node",
                 "node_to_call", "condition_node", "start_value_node",
                 "end_value_node", "step_value_node"):
        child = getattr(node, attr, None)
        if child is not None:
            _node_classes(child, found)
    if isinstance(node, IfNode):
        for condition, body, _ in node.cases:
            _node_classes(condition, found)
            _node_classes(body, found)
    return found


def test_the_sugar_builds_only_node_types_the_language_already_had():
    found = set()
    _node_classes(parse(PATCH_PLAN), found)
    _node_classes(parse(WIDGET_PLAN), found)
    _node_classes(parse("ON k CHANGE fn\nspec[i]\nspec[i] = 2"), found)
    assert found <= set(NODE_TYPES)


def test_the_sugar_runs_as_the_same_calls_through_user_defined_functions(eval_src):
    """No new semantics, demonstrated where semantics live: at runtime.

    ``PD_*`` are redefined here as ordinary script functions - bindings are
    global-table entries the same way - and the PATCH block lands on them
    call for call, with the arguments the Tier A twin would have passed.
    """
    value = eval_src("""
VAR log = []
FUNC PD_BUILD(id, w, h)
    APPEND(log, ["build", id, w, h])
    RETURN id
END
FUNC PD_OBJ(p, text, x, y)
    APPEND(log, ["obj", text, x, y])
    RETURN text
END
FUNC PD_LINK(p, src, dst)
    APPEND(log, ["link", src, dst])
    RETURN 0
END
PATCH tone
    CANVAS 100 200
    OBJ o "osc~ 440" AT 1 2
    LINK o -> OUT
END
log
""")
    assert plain(value) == [
        ["build", "tone", 100, 200],
        ["obj", "osc~ 440", 1, 2],
        ["obj", "dac~", 0, 0],
        ["link", "osc~ 440", "dac~"],
    ]


def test_a_subscript_reads_like_the_at_call_it_replaces(eval_src):
    # GET answers with a fresh handle each time, so the list itself is compared
    assert plain(eval_src('VAR spec = ["a", "b"]\nspec[1]')) == "b"
    assert plain(eval_src('VAR spec = [[1, 2], [3, 4]]\nspec[1][0]')) == 3
    assert plain(eval_src('VAR s = "hey"\ns[1]')) == "e"


def test_a_subscript_assignment_writes_like_the_set_at_call_it_replaces(eval_src):
    assert plain(eval_src("VAR a = [1, 2, 3]\na[1] = 9\na")) == [1, 9, 3]
    assert plain(eval_src("VAR a = [[1], [2]]\na[1][0] = 9\na")) == [[1], [9]]
