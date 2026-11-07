
"""Token types, keyword table and the Token record used by lexer and parser."""

from __future__ import annotations

import string


DIGITS = '0123456789'
LETTERS = string.ascii_letters
LETTERS_DIGITS = LETTERS + DIGITS

# TOKENS

TT_INT = 'INT'
TT_FLOAT = 'FLOAT'
TT_STRING = 'STRING'
TT_IDENTIFIER = 'IDENTIFIER'
TT_KEYWORD = 'KEYWORD'
TT_PLUS = 'PLUS'
TT_MINUS = 'MINUS'
TT_MUL = 'MUL'
TT_DIV = 'DIV'
TT_POW = 'POW'
TT_EQ = 'EQ'
TT_LPAREN = 'LPAREN'
TT_RPAREN = 'RPAREN'
TT_LSQUARE = 'LSQUARE'
TT_RSQUARE = 'RSQUARE'
TT_EE = 'EE'
TT_NE = 'NE'
TT_LT = 'LT'
TT_GT = 'GT'
TT_LTE = 'LTE'
TT_GTE = 'GTE'
TT_COMMA = 'COMMA'
TT_ARROW = 'ARROW'
TT_NEWLINE = 'NEWLINE'
TT_EOF = 'EOF'

# TT_LANGLE       = 'LANGLE'
# TT_RANGLE       = 'RANGLE'
# TT_LBRACE       = 'LBRACE'
# TT_RBRACE       = 'RBRACE'
# TT_LBRACKET     = 'LBRACKET'
# TT_RBRACKET     = 'RBRACKET'

# WHILE i < 10 THEN; VAR i = i + 1; IF i = 4 THEN CONTINUE; IF i == 8 THEN BREAK; VAR a = a + 1; END
# FOR i = 0 TO 10 THEN; IF i == 4 THEN CONTINUE ELIF i == 8 THEN BREAK; VAR a = a + i; END
# FUNC test(); VAR foo = 5; RETURN foo; END

KEYWORDS = [
    'VAR',
    'AND',
    'OR',
    'NOT',
    'IF',
    'ELIF',
    'ELSE',
    'FOR',
    'TO',
    'STEP',
    'WHILE',
    'FUNC',
    'THEN',
    'END',
    'RETURN',
    'CONTINUE',
    'BREAK'
]


class Token:
    def __init__(self, type_, value=None, pos_start=None, pos_end=None):
        self.type = type_
        self.value = value

        if pos_start:
            self.pos_start = pos_start.copy()
            self.pos_end = pos_start.copy()
            self.pos_end.advance()

        if pos_end:
            self.pos_end = pos_end

    def matches(self, type_, value):
        return self.type == type_ and self.value == value

    def __repr__(self):
        if self.value: return f'{self.type}:{self.value}'
        return f'{self.type}'
