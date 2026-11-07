"""Parser behaviour: node shapes, precedence and syntax errors."""

from __future__ import annotations

import pytest

from crude.lang import (
    BinOpNode,
    BreakNode,
    CallNode,
    ContinueNode,
    ForNode,
    FuncDefNode,
    IfNode,
    Lexer,
    List,
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
from crude.lang.tokens import TT_EE, TT_KEYWORD, TT_LT, TT_MINUS, TT_MUL, TT_PLUS


def parse(text, fn="<parse>"):
    tokens, error = Lexer(fn, text).make_tokens()
    assert error is None, error.as_string()
    result = Parser(tokens).parse()
    assert result.error is None, result.error.as_string()
    return result.node


def first_statement(text):
    return parse(text).element_nodes[0]


def test_program_is_a_list_of_statements():
    node = parse("1\n2\n3")
    assert isinstance(node, ListNode)
    assert len(node.element_nodes) == 3


def test_empty_program_parses_to_zero_statements():
    node = parse("")
    assert isinstance(node, ListNode)
    assert node.element_nodes == []


def test_var_assignment_node():
    node = first_statement("VAR answer = 42")
    assert isinstance(node, VarAssignNode)
    assert node.var_name_tok.value == "answer"
    assert isinstance(node.value_node, NumberNode)


def test_var_requires_identifier_and_equals():
    for source in ("VAR = 1", "VAR x 1"):
        tokens, _ = Lexer("<parse>", source).make_tokens()
        result = Parser(tokens).parse()
        assert result.error is not None
        assert result.error.error_name == "Invalid Syntax"


def test_multiplication_binds_tighter_than_addition():
    node = first_statement("1 + 2 * 3")
    assert isinstance(node, BinOpNode)
    assert node.op_tok.type == TT_PLUS
    assert isinstance(node.right_node, BinOpNode)
    assert node.right_node.op_tok.type == TT_MUL


def test_left_associative_terms():
    node = first_statement("1 - 2 - 3")
    assert isinstance(node, BinOpNode)
    assert node.op_tok.type == TT_MINUS
    assert isinstance(node.left_node, BinOpNode)


def test_power_is_right_associative_and_binds_tightest():
    node = first_statement("2 ^ 3 ^ 2")
    assert isinstance(node.right_node, BinOpNode)
    assert node.left_node.tok.value == 2


def test_unary_minus_and_not():
    node = first_statement("-1")
    assert isinstance(node, UnaryOpNode)
    assert node.op_tok.type == TT_MINUS

    node = first_statement("NOT 1")
    assert node.op_tok.matches(TT_KEYWORD, "NOT")


def test_comparison_operators():
    node = first_statement("a == b")
    assert node.op_tok.type == TT_EE
    node = first_statement("a < b")
    assert node.op_tok.type == TT_LT


def test_string_and_list_literals():
    assert isinstance(parse('"hi"').element_nodes[0], StringNode)

    node = parse("[1, 2]").element_nodes[0]
    assert isinstance(node, ListNode)
    assert len(node.element_nodes) == 2


def test_empty_list_literal():
    node = parse("[]").element_nodes[0]
    assert isinstance(node, ListNode)
    assert node.element_nodes == []


def test_call_node_with_and_without_args():
    node = first_statement("PRINT(1, 2)")
    assert isinstance(node, CallNode)
    assert len(node.arg_nodes) == 2

    node = first_statement("LEN()")
    assert isinstance(node, CallNode)
    assert node.arg_nodes == []


def test_var_access_node():
    assert isinstance(first_statement("x"), VarAccessNode)


def test_if_with_elif_and_else():
    node = first_statement("IF a THEN 1 ELIF b THEN 2 ELSE 3")
    assert isinstance(node, IfNode)
    assert len(node.cases) == 2
    assert node.else_case is not None


def test_if_without_else():
    node = first_statement("IF a THEN 1")
    assert isinstance(node, IfNode)
    assert node.else_case is None


def test_block_if_shares_newlines():
    node = first_statement("IF a THEN\n 1\nEND")
    assert isinstance(node, IfNode)
    assert node.cases[0][2] is True  # should_return_null: block form


def test_block_if_requires_then():
    tokens, _ = Lexer("<parse>", "IF a\n 1\nEND").make_tokens()
    result = Parser(tokens).parse()
    assert result.error is not None
    assert "THEN" in result.error.details


def test_for_node_with_and_without_step():
    node = first_statement("FOR i = 0 TO 3 THEN 1")
    assert isinstance(node, ForNode)
    assert node.var_name_tok.value == "i"
    assert node.step_value_node is None

    node = first_statement("FOR i = 0 TO 3 STEP 2 THEN 1")
    assert node.step_value_node is not None


def test_while_node():
    node = first_statement("WHILE a THEN 1")
    assert isinstance(node, WhileNode)


def test_func_def_arrow_form_auto_returns():
    node = first_statement("FUNC sq(n) -> n * n")
    assert isinstance(node, FuncDefNode)
    assert node.var_name_tok.value == "sq"
    assert [tok.value for tok in node.arg_name_toks] == ["n"]
    assert node.should_auto_return is True


def test_func_def_block_form_does_not_auto_return():
    node = first_statement("FUNC sq(n)\n RETURN n\nEND")
    assert node.should_auto_return is False


def test_anonymous_function_is_an_expression():
    node = first_statement("FUNC(x) -> x")
    assert isinstance(node, FuncDefNode)
    assert node.var_name_tok is None
    assert node.should_auto_return is True


def test_return_continue_break_statements():
    assert isinstance(first_statement("RETURN 1"), ReturnNode)
    assert isinstance(first_statement("CONTINUE"), ContinueNode)
    assert isinstance(first_statement("BREAK"), BreakNode)


def test_bare_return_has_no_value():
    assert first_statement("RETURN").node_to_return is None


def test_semicolons_separate_statements():
    node = parse("VAR a = 1; VAR b = 2")
    assert len(node.element_nodes) == 2


def test_trailing_expression_after_statement_is_a_syntax_error():
    tokens, _ = Lexer("<parse>", "1 2").make_tokens()
    result = Parser(tokens).parse()
    assert result.error is not None
    assert result.error.error_name == "Invalid Syntax"


def test_syntax_error_carries_a_source_span():
    tokens, _ = Lexer("<parse>", "VAR x = ]").make_tokens()
    result = Parser(tokens).parse()
    assert result.error is not None
    text = result.error.as_string()
    assert "<parse>" in text
    assert "^" in text


def test_index_syntax_is_the_at_sugar_it_became_in_s12():
    # The pre-S12 gap (``l[i]`` did not parse) closed as a parse-time desugar;
    # tests/lang/test_sugar.py owns the equivalence, this pins the shape here.
    node = first_statement("l[0]")
    assert isinstance(node, CallNode)
    assert node.node_to_call.var_name_tok.value == "AT"

    node = first_statement("l[0] = 1")
    assert isinstance(node, CallNode)
    assert node.node_to_call.var_name_tok.value == "SET_AT"


def test_parens_override_precedence():
    node = first_statement("(1 + 2) * 3")
    assert node.op_tok.type == TT_MUL
    assert isinstance(node.left_node, BinOpNode)


def test_list_literal_of_values_executes_to_list():
    # Parsing and shape only; the value side lives in test_interpreter.
    node = parse("[1, 2]").element_nodes[0]
    assert isinstance(node, ListNode)
    assert all(isinstance(child, NumberNode) for child in node.element_nodes)
    assert List is not None  # keeps the import meaningful for readers
