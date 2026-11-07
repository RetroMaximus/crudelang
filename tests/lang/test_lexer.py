"""Lexer behaviour: token kinds, positions and the error cases."""

from __future__ import annotations

import pytest

from crude.lang import Lexer
from crude.lang.tokens import (
    TT_ARROW,
    TT_COMMA,
    TT_DIV,
    TT_EE,
    TT_EOF,
    TT_EQ,
    TT_FLOAT,
    TT_GT,
    TT_GTE,
    TT_IDENTIFIER,
    TT_INT,
    TT_KEYWORD,
    TT_LPAREN,
    TT_LSQUARE,
    TT_LT,
    TT_LTE,
    TT_MINUS,
    TT_MUL,
    TT_NE,
    TT_NEWLINE,
    TT_PLUS,
    TT_POW,
    TT_RPAREN,
    TT_RSQUARE,
    TT_STRING,
)


def lex(text, fn="<lex>"):
    tokens, error = Lexer(fn, text).make_tokens()
    assert error is None, error.as_string()
    return tokens


def kinds(text):
    return [token.type for token in lex(text)]


def test_integers_and_floats():
    tokens = lex("1 2.5 0")
    assert [t.type for t in tokens] == [TT_INT, TT_FLOAT, TT_INT, TT_EOF]
    assert [t.value for t in tokens[:-1]] == [1, 2.5, 0]
    assert isinstance(tokens[0].value, int)
    assert isinstance(tokens[1].value, float)


def test_string_value_and_escapes():
    assert lex(r'"hi\nthere"')[0].value == "hi\nthere"
    assert lex(r'"a\tb"')[0].value == "a\tb"
    assert lex(r'"say \"hi\""')[0].value == 'say "hi"'


def test_second_dot_is_an_illegal_character():
    tokens, error = Lexer("<lex>", "1.2.3").make_tokens()
    assert tokens == []
    assert error.error_name == "Illegal Character"
    assert "'.'" in error.details


def test_keywords_are_case_sensitive():
    tokens = lex("VAR var If")
    assert tokens[0].matches(TT_KEYWORD, "VAR")
    assert tokens[1].type == TT_IDENTIFIER
    assert tokens[2].type == TT_IDENTIFIER


def test_identifiers_allow_digits_and_underscore():
    token = lex("my_var2")[0]
    assert token.type == TT_IDENTIFIER
    assert token.value == "my_var2"


def test_minus_and_arrow():
    assert kinds("-")[0] == TT_MINUS
    assert kinds("->")[0] == TT_ARROW


@pytest.mark.parametrize(
    "text,expected",
    [
        ("==", TT_EE),
        ("!=", TT_NE),
        ("<", TT_LT),
        ("<=", TT_LTE),
        (">", TT_GT),
        (">=", TT_GTE),
        ("=", TT_EQ),
        ("+", TT_PLUS),
        ("*", TT_MUL),
        ("/", TT_DIV),
        ("^", TT_POW),
        ("(", TT_LPAREN),
        (")", TT_RPAREN),
        ("[", TT_LSQUARE),
        ("]", TT_RSQUARE),
        (",", TT_COMMA),
    ],
)
def test_operators(text, expected):
    assert kinds(text)[0] == expected


def test_semicolon_and_newline_both_produce_newline_tokens():
    assert kinds("1;2") == [TT_INT, TT_NEWLINE, TT_INT, TT_EOF]
    assert kinds("1\n2") == [TT_INT, TT_NEWLINE, TT_INT, TT_EOF]


def test_comment_is_skipped():
    assert kinds("1 # ignored\n2") == [TT_INT, TT_NEWLINE, TT_INT, TT_EOF]


def test_comment_at_end_of_file_without_newline():
    # Regression: the original looped forever on a trailing comment.
    tokens = lex("1 # trailing")
    assert [t.type for t in tokens][-2:] == [TT_INT, TT_EOF]


def test_positions_track_line_and_column():
    tokens = lex("1\n  2")
    first, _, second = tokens[0], tokens[1], tokens[2]
    assert (first.pos_start.ln, first.pos_start.col) == (0, 0)
    assert (second.pos_start.ln, second.pos_start.col) == (1, 2)


def test_illegal_character_reports_position():
    tokens, error = Lexer("<lex>", "1 @ 2").make_tokens()
    assert tokens == []
    assert error.error_name == "Illegal Character"
    assert error.pos_start.col == 2


def test_bang_without_equals_is_rejected():
    tokens, error = Lexer("<lex>", "1 ! 2").make_tokens()
    assert tokens == []
    assert error.error_name == "Expected character"
    assert "after '!'" in error.details


def test_eof_is_always_last():
    assert kinds("") == [TT_EOF]
    assert kinds("1")[-1] == TT_EOF
