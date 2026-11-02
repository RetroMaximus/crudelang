
"""Recursive-descent parser: tokens -> AST.

`ParserResult` carries the backtracking bookkeeping (`to_reverse_count`) that
lets `statements()` stop cleanly at END/EOF; `Parser` is a direct descendant of
the tutorial grammar, so the accepted language is unchanged by the S1 split.
"""

from __future__ import annotations

from .errors import InvalidSyntaxError
from .nodes import *
from .tokens import *

# -- Tier B sugar (S12) ---------------------------------------------------
#
# The two block DSLs and the `ON` line match these words as plain
# identifiers, never lexer keywords: TEXT, NUM, AT, SET and WIDGET are live
# binding names, and a KEYWORDS table that claimed them would change the
# shape of every Tier A script. Inside a PATCH or WIDGET block the word
# means the DSL; everywhere else it still means the call it always did.
# Nothing below invents a node type or a runtime behaviour: each item
# desugars to the exact CallNode/VarAssignNode its Tier A spelling builds.

PATCH_ITEMS = ("CANVAS", "OBJ", "MSG", "NUM", "SYMB", "TABLE", "TEXT", "LINK")
WIDGET_ITEMS = ("AT", "SET", "ADD", "LINK_VALUE", "LINK_STATE", "ON")

#: events an ``ON`` line may name. This mirrors ``crude.ui.handlers.EVENTS``
#: (a test pins the two lists together, so the parser needs no import from
#  the UI package), and every word here is an existing ``ON_*`` binding.
ON_EVENTS = frozenset((
    "CHANGE", "ENTER", "LEAVE", "MOVE", "PRESS", "RELEASE", "CLICK",
    "FOCUS", "BLUR", "KEY", "CHAR", "SCROLL", "DRAG", "RESIZE",
    "DROP", "CLOSE", "FRAME", "PAINT", "HIT",
))


class ParserResult:
    def __init__(self):
        self.error = None
        self.node = None
        self.last_register_advance_count = 0
        self.advance_count = 0
        self.to_reverse_count = 0

    def register_advancement(self):
        self.advance_count += 1

    def register(self, res):
        self.last_register_advance_count = res.advance_count
        self.advance_count += res.advance_count
        if res.error: self.error = res.error
        return res.node

    def try_register(self, res):
        if res.error:
            self.to_reverse_count = res.advance_count
            return None
        return self.register(res)

    def success(self, node):
        self.node = node
        return self

    def failure(self, error):
        if not self.error or self.advance_count == 0:
            self.error = error

        return self


# PARSER

class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.tok_idx = -1
        self.advance()

    def advance(self):
        self.tok_idx += 1
        self.update_current_tok()
        return self.current_tok

    def reverse(self, amount=1):
        self.tok_idx -= amount
        self.update_current_tok()
        return self.current_tok

    def update_current_tok(self):
        if self.tok_idx >= 0 and self.tok_idx < len(self.tokens):
            self.current_tok = self.tokens[self.tok_idx]

    def parse(self):
        res = self.statements()
        if not res.error and self.current_tok.type != TT_EOF:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start,
                self.current_tok.pos_end,
                "Expected '+', '-', '*' or '/', '^', '==', '!=', '<', '>', '<=', '>=', 'AND', or 'OR'"))
        return res

    #######################################################

    def statements(self):
        res = ParserResult()
        statements = []
        pos_start = self.current_tok.pos_start.copy()

        # Skip leading newlines
        while self.current_tok.type == TT_NEWLINE:
            res.register_advancement()
            self.advance()

        # If nothing follows (EOF or another newline), return empty list
        if self.current_tok.type == TT_EOF or self.current_tok.type == TT_NEWLINE:
            return res.success(ListNode(statements, pos_start, self.current_tok.pos_end.copy()))

        # Parse first statement
        statement = res.register(self.statement())
        if res.error: return res
        statements.append(statement)

        # Parse more statements separated by newlines
        more_statements = True
        while True:
            newline_count = 0
            while self.current_tok.type == TT_NEWLINE:
                res.register_advancement()
                self.advance()
                newline_count += 1
            if newline_count == 0:
                more_statements = False

            if not more_statements: break
            statement = res.try_register(self.statement())
            if not statement:
                self.reverse(res.to_reverse_count)
                more_statements = False
                continue
            statements.append(statement)

        return res.success(ListNode(
            statements,
            pos_start,
            self.current_tok.pos_end.copy()
        ))

    def statement(self):
        res = ParserResult()
        pos_start = self.current_tok.pos_start.copy()

        if self.current_tok.matches(TT_KEYWORD, 'RETURN'):
            res.register_advancement()
            self.advance()

            expr = res.try_register(self.expr())
            if not expr:
                self.reverse(res.to_reverse_count)
            return res.success(ReturnNode(expr, pos_start, self.current_tok.pos_start.copy()))

        if self.current_tok.matches(TT_KEYWORD, 'CONTINUE'):
            res.register_advancement()
            self.advance()
            return res.success(ContinueNode(pos_start, self.current_tok.pos_start.copy()))

        if self.current_tok.matches(TT_KEYWORD, 'BREAK'):
            res.register_advancement()
            self.advance()
            return res.success(BreakNode(pos_start, self.current_tok.pos_start.copy()))

        # S12 block sugar; each check also looks one token ahead so a Tier A
        # statement that merely *starts* with one of these words (a call,
        # `WIDGET("stepper")`) keeps travelling down to `expr()` untouched
        tok = self.current_tok
        if tok.type == TT_IDENTIFIER:
            ahead = self._peek()
            if tok.value == 'PATCH' and ahead is not None \
                    and ahead.type == TT_IDENTIFIER:
                return self.patch_dsl()
            if (tok.value == 'WIDGET' and ahead is not None
                    and ahead.type == TT_IDENTIFIER
                    and self._peek(2) is not None
                    and self._peek(2).type == TT_EQ):
                return self.widget_dsl()
            if tok.value == 'ON' and ahead is not None \
                    and ahead.type == TT_IDENTIFIER:
                return self.on_statement()

        expr = res.register(self.expr())
        if res.error:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                "Expected 'RETURN', 'CONTINUE', 'BREAK', 'VAR', 'IF', 'FOR', 'WHILE', 'FUNC', int, float, identifier, '+', '-', '(', '[' or 'NOT'"
            ))

        # `a[i] = v` is `SET_AT(a, i, v)`; only a read built by the postfix
        # sugar takes the `=`, so every other statement ends where it did
        if self.current_tok.type == TT_EQ and getattr(expr, 'index_sugar', False):
            res.register_advancement()
            self.advance()
            base_node, index_node = expr.arg_nodes
            value = res.register(self.expr())
            if res.error: return res
            return res.success(self._sugar_call(expr, 'SET_AT',
                                               [base_node, index_node, value]))

        return res.success(expr)

    def expr(self):
        res = ParserResult()

        if self.current_tok.matches(TT_KEYWORD, 'VAR'):
            res.register_advancement()
            self.advance()

            if self.current_tok.type != TT_IDENTIFIER:
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start,
                    self.current_tok.pos_end,
                    "Expected identifier"
                ))

            var_name = self.current_tok
            res.register_advancement()
            self.advance()

            if self.current_tok.type != TT_EQ:
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start,
                    self.current_tok.pos_end,
                    "Expected '='"
                ))

            res.register_advancement()
            self.advance()
            expr = res.register(self.expr())
            if res.error: return res
            return res.success(VarAssignNode(var_name, expr))

        node = res.register(self.bin_op(self.comp_expr, ((TT_KEYWORD, "AND"), (TT_KEYWORD, "OR"))))

        if res.error:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                "Expected 'VAR', 'IF', 'FOR', 'WHILE', 'FUNC', int, float, identifier, '+', '-', '(', '[' or 'NOT'"
            ))

        return res.success(node)

    def list_expr(self):
        res = ParserResult()
        element_nodes = []

        pos_start = self.current_tok.pos_start.copy()

        if self.current_tok.type != TT_LSQUARE:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end, f"Expected '['"
            ))

        res.register_advancement()
        self.advance()

        if self.current_tok.type == TT_RSQUARE:
            res.register_advancement()
            self.advance()
        else:
            element_nodes.append(res.register(self.expr()))
            if res.error:
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end,
                    "Expected ']', 'VAR', 'IF', 'FOR', 'WHILE', 'FUNC', 'int', 'float', identifier, '+', '-', '(', '[' or 'NOT'"
                ))

            while self.current_tok.type == TT_COMMA:
                res.register_advancement()
                self.advance()

                element_nodes.append(res.register(self.expr()))
                if res.error: return res

            if self.current_tok.type != TT_RSQUARE:
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end, f"Expected ',' or ']'"
                ))

            res.register_advancement()
            self.advance()

        return res.success(ListNode(
            element_nodes,
            pos_start,
            self.current_tok.pos_end.copy()
        ))

    def if_expr(self):
        res = ParserResult()
        all_cases = res.register(self.if_expr_cases('IF'))
        if res.error: return res
        cases, else_case = all_cases
        return res.success(IfNode(cases, else_case))

    def if_expr_b(self):
        return self.if_expr_cases('ELIF')

    def if_expr_c(self):
        res = ParserResult()
        else_case = None

        if self.current_tok.matches(TT_KEYWORD, 'ELSE'):
            res.register_advancement()
            self.advance()

            if self.current_tok.type == TT_NEWLINE:
                res.register_advancement()
                self.advance()

                statements = res.register(self.statements())
                if res.error: return res
                else_case = (statements, True)

                if self.current_tok.matches(TT_KEYWORD, 'END'):
                    res.register_advancement()
                    self.advance()
                else:
                    return res.failure(InvalidSyntaxError(
                        self.current_tok.pos_start, self.current_tok.pos_end,
                        "Expected 'END'"
                    ))
            else:
                expr = res.register(self.statement())
                if res.error: return res
                else_case = (expr, False)

        return res.success(else_case)

    def if_expr_b_or_c(self):
        res = ParserResult()
        cases, else_case = [], None

        if self.current_tok.matches(TT_KEYWORD, 'ELIF'):
            all_cases = res.register(self.if_expr_b())
            if res.error: return res
            cases, else_case = all_cases
        else:
            else_case = res.register(self.if_expr_c())
            if res.error: return res

        return res.success((cases, else_case))

    def if_expr_cases(self, case_keyword):
        res = ParserResult()
        cases = []
        else_case = None

        if not self.current_tok.matches(TT_KEYWORD, case_keyword):
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                f'Expected {case_keyword}'
            ))

        res.register_advancement()
        self.advance()

        condition = res.register(self.expr())
        if res.error: return res

        if not self.current_tok.matches(TT_KEYWORD, 'THEN'):
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                f"Expected 'THEN'"
            ))

        res.register_advancement()
        self.advance()

        if self.current_tok.type == TT_NEWLINE:
            res.register_advancement()
            self.advance()

            statements = res.register(self.statements())
            if res.error: return res
            cases.append((condition, statements, True))

            if self.current_tok.matches(TT_KEYWORD, 'END'):
                res.register_advancement()
                self.advance()
            else:
                all_cases = res.register(self.if_expr_b_or_c())
                if res.error: return res
                new_cases, else_case = all_cases
                cases.extend(new_cases)
        else:
            expr = res.register(self.statement())
            if res.error: return res
            cases.append((condition, expr, False))

            all_cases = res.register(self.if_expr_b_or_c())
            if res.error: return res
            new_cases, else_case = all_cases
            cases.extend(new_cases)

        return res.success((cases, else_case))

    def for_expr(self):
        res = ParserResult()

        if not self.current_tok.matches(TT_KEYWORD, 'FOR'):
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                f"Expected 'FOR'"

            ))

        res.register_advancement()
        self.advance()

        if self.current_tok.type != TT_IDENTIFIER:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                "Expected 'IDENTIFIER'"
            ))

        var_name = self.current_tok
        res.register_advancement()
        self.advance()

        if self.current_tok.type != TT_EQ:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                "Expected '='"
            ))

        res.register_advancement()
        self.advance()

        start_value = res.register(self.expr())
        if res.error: return res

        if not self.current_tok.matches(TT_KEYWORD, 'TO'):
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                "Expected 'TO'"
            ))

        res.register_advancement()
        self.advance()

        end_value = res.register(self.expr())
        if res.error: return res

        if self.current_tok.matches(TT_KEYWORD, 'STEP'):
            res.register_advancement()
            self.advance()

            step_value = res.register(self.expr())
            if res.error: return res
        else:
            step_value = None

        if not self.current_tok.matches(TT_KEYWORD, 'THEN'):
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end, "Expected 'THEN'"
            ))

        res.register_advancement()
        self.advance()

        if self.current_tok.type == TT_NEWLINE:
            res.register_advancement()
            self.advance()

            body = res.register(self.statements())
            if res.error: return res

            if not self.current_tok.matches(TT_KEYWORD, 'END'):
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end,
                    "Expected 'END'"
                ))

            res.register_advancement()
            self.advance()

            return res.success(ForNode(var_name, start_value, end_value, step_value, body, True))

        body = res.register(self.statement())
        if res.error: return res

        return res.success(ForNode(var_name, start_value, end_value, step_value, body, False))

    def while_expr(self):
        res = ParserResult()

        if not self.current_tok.matches(TT_KEYWORD, 'WHILE'):
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                f"Expected 'WHILE'"

            ))

        res.register_advancement()
        self.advance()

        condition = res.register(self.expr())
        if res.error: return res

        if not self.current_tok.matches(TT_KEYWORD, 'THEN'):
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                f"Expected 'THEN'"
            ))

        res.register_advancement()
        self.advance()

        if self.current_tok.type == TT_NEWLINE:
            res.register_advancement()
            self.advance()

            body = res.register(self.statements())
            if res.error: return res

            # while self.current_tok.type == TT_NEWLINE:
            #     res.register_advancement()
            #     self.advance()

            if not self.current_tok.matches(TT_KEYWORD, 'END'):
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end,
                    "Expected 'END'"
                ))

            res.register_advancement()
            self.advance()

            return res.success(WhileNode(condition, body, True))

        body = res.register(self.statement())
        if res.error: return res

        return res.success(WhileNode(condition, body, False))

    def power(self):
        return self.bin_op(self.call, (TT_POW,), self.factor)

    def call(self):
        res = ParserResult()
        node = res.register(self.atom())
        if res.error: return res

        # postfix loop: `(...)` calls and `[...]` indexes, each feeding the
        # next, so `f(x)[0]` and `a[i][j]` both fall out of the one shape
        while True:
            if self.current_tok.type == TT_LPAREN:
                res.register_advancement()
                self.advance()
                arg_nodes = []

                if self.current_tok.type == TT_RPAREN:
                    res.register_advancement()
                    self.advance()
                else:
                    arg_nodes.append(res.register(self.expr()))
                    if res.error:
                        return res.failure(InvalidSyntaxError(
                            self.current_tok.pos_start, self.current_tok.pos_end,
                            "Expected ')', 'VAR', 'IF', 'FOR', 'WHILE', 'FUNC', 'int', 'float', identifier, '+', '-', '(', '[' or 'NOT'"
                        ))

                    while self.current_tok.type == TT_COMMA:
                        res.register_advancement()
                        self.advance()

                        arg_nodes.append(res.register(self.expr()))
                        if res.error: return res

                    if self.current_tok.type != TT_RPAREN:
                        return res.failure(InvalidSyntaxError(
                            self.current_tok.pos_start, self.current_tok.pos_end, f"Expected ',' or ')'"
                        ))

                    res.register_advancement()
                    self.advance()

                node = CallNode(node, arg_nodes)
                continue

            if self.current_tok.type == TT_LSQUARE:
                open_tok = self.current_tok
                res.register_advancement()
                self.advance()
                index = res.register(self.expr())
                if res.error: return res
                if self.current_tok.type != TT_RSQUARE:
                    return res.failure(InvalidSyntaxError(
                        self.current_tok.pos_start, self.current_tok.pos_end,
                        "Expected ']'"
                    ))
                res.register_advancement()
                self.advance()
                # S12: `a[i]` is the plan's spelling of `AT(a, i)`
                node = self._sugar_call(open_tok, 'AT', [node, index])
                node.index_sugar = True
                continue

            break

        return res.success(node)

    def atom(self):
        res = ParserResult()
        tok = self.current_tok

        if tok.type in (TT_INT, TT_FLOAT):
            res.register_advancement()
            self.advance()
            return res.success(NumberNode(tok))

        if tok.type in (TT_INT, TT_STRING):
            res.register_advancement()
            self.advance()
            return res.success(StringNode(tok))

        elif tok.type == TT_IDENTIFIER:
            res.register_advancement()
            self.advance()
            return res.success(VarAccessNode(tok))

        elif tok.type == TT_LPAREN:
            res.register_advancement()
            self.advance()
            expr = res.register(self.expr())
            if res.error: return res
            if self.current_tok.type == TT_RPAREN:
                res.register_advancement()
                self.advance()
                return res.success(expr)
            else:
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end, "Expected ')'"
                ))

        elif tok.type == TT_LSQUARE:
            list_expr = res.register(self.list_expr())
            if res.error: return res
            return res.success(list_expr)

        elif tok.matches(TT_KEYWORD, 'IF'):
            if_expr = res.register(self.if_expr())
            if res.error: return res
            return res.success(if_expr)

        elif tok.matches(TT_KEYWORD, 'FOR'):
            for_expr = res.register(self.for_expr())
            if res.error: return res
            return res.success(for_expr)

        elif tok.matches(TT_KEYWORD, 'WHILE'):
            while_expr = res.register(self.while_expr())
            if res.error: return res
            return res.success(while_expr)

        elif tok.matches(TT_KEYWORD, 'FUNC'):
            func_def = res.register(self.func_def())
            if res.error: return res
            return res.success(func_def)

        return res.failure(InvalidSyntaxError(
            tok.pos_start, tok.pos_end,
            "Expected int, float, identifier, '+', '-' or '(', '[', 'IF', 'FOR', 'WHILE', 'FUNC'"))

    def factor(self):
        res = ParserResult()
        tok = self.current_tok

        if tok.type in (TT_PLUS, TT_MINUS):
            res.register_advancement()
            self.advance()
            factor = res.register(self.factor())
            if res.error: return res
            return res.success(UnaryOpNode(tok, factor))

        return self.power()

    def term(self):
        return self.bin_op(self.factor, (TT_MUL, TT_DIV))

    def comp_expr(self):
        res = ParserResult()

        if self.current_tok.matches(TT_KEYWORD, "NOT"):
            op_tok = self.current_tok
            res.register_advancement()
            self.advance()

            node = res.register(self.comp_expr())
            if res.error: return res
            return res.success(UnaryOpNode(op_tok, node))

        node = res.register(
            self.bin_op(self.arith_expr, (TT_EE, TT_NE, TT_LT, TT_GT, TT_LTE, TT_GTE))
        )

        if res.error:
            return res.failure(
                InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end,
                    "Expected int, float, identifier, '+', '-', '(', '[' or 'NOT'"
                )
            )
        return res.success(node)

    def arith_expr(self):
        return self.bin_op(self.term, (TT_PLUS, TT_MINUS))

    def bin_op(self, func_a, ops, func_b=None):

        if func_b is None:
            func_b = func_a

        res = ParserResult()
        left = res.register(func_a())
        if res.error: return res

        while self.current_tok.type in ops or (self.current_tok.type, self.current_tok.value) in ops:
            op_tok = self.current_tok
            res.register_advancement()
            self.advance()
            right = res.register(func_b())
            if res.error: return res

            left = BinOpNode(left, op_tok, right)

        return res.success(left)

    #######################################################
    # S12 sugar: the pieces above are expression-level; the block
    # forms below are statements that build statements, and every one
    # of them ends as a CallNode/VarAssignNode the Tier A surface builds
    # from the same words. No new node type exists in this section.
    #######################################################

    def _peek(self, offset=1):
        """The token `offset` ahead, or None past the end of the stream."""
        index = self.tok_idx + offset
        if 0 <= index < len(self.tokens):
            return self.tokens[index]
        return None

    @staticmethod
    def _sugar_tok(type_, value, like):
        """A synthesised token wearing the word that asked for it.

        Positions matter to the error reporter even when a word was invented
        here, so every made token borrows its span from a real one.
        """
        return Token(type_, value, like.pos_start, like.pos_end)

    def _sugar_call(self, like, name, arg_nodes):
        """A ``CallNode`` on a binding the DSL desugars onto.

        ``like`` is any node or token whose position the made call should
        carry; ``name`` is looked up by the interpreter exactly like the
        Tier A call it replaces - that is the whole point of this file.
        """
        callee = VarAccessNode(self._sugar_tok(TT_IDENTIFIER, name, like))
        return CallNode(callee, arg_nodes)

    # -- PATCH ... END ----------------------------------------------------

    def patch_dsl(self):
        """`PATCH name NEWLINE item* END` as the PD_* statements it replaces."""
        res = ParserResult()
        head_tok = self.current_tok             # the word PATCH itself
        res.register_advancement()
        self.advance()

        if self.current_tok.type != TT_IDENTIFIER:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                "Expected a patch name after 'PATCH'"
            ))
        patch_tok = self.current_tok
        res.register_advancement()
        self.advance()

        items = []
        if self.current_tok.type == TT_NEWLINE:
            while True:
                while self.current_tok.type == TT_NEWLINE:
                    res.register_advancement()
                    self.advance()
                if self.current_tok.matches(TT_KEYWORD, 'END'):
                    res.register_advancement()
                    self.advance()
                    break
                if self.current_tok.type == TT_EOF:
                    return res.failure(InvalidSyntaxError(
                        self.current_tok.pos_start, self.current_tok.pos_end,
                        "Expected 'END'"
                    ))
                item = res.register(self.patch_item())
                if res.error: return res
                items.append(item)
                if self.current_tok.type != TT_NEWLINE:
                    return res.failure(InvalidSyntaxError(
                        self.current_tok.pos_start, self.current_tok.pos_end,
                        "Expected a new line or 'END'"
                    ))
                res.register_advancement()
                self.advance()
        elif self.current_tok.matches(TT_KEYWORD, 'END'):
            res.register_advancement()
            self.advance()
        else:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                "Expected a new line or 'END' after the patch name"
            ))

        return self.patch_statements(head_tok, patch_tok, items)

    def patch_item(self):
        """One line inside a PATCH block, kept as data until the block ends.

        The CANVAS line can follow the items that need its numbers, and an
        `IN`/`OUT` word owes the patch one adc~/dac~ box however often the
        script says it, so the desugaring happens once over the whole list
        in :meth:`patch_statements` rather than line by line here.
        """
        res = ParserResult()
        tok = self.current_tok
        if tok.type != TT_IDENTIFIER or tok.value not in PATCH_ITEMS:
            return res.failure(InvalidSyntaxError(
                tok.pos_start, tok.pos_end,
                "Expected " + ", ".join(f"'{word}'" for word in PATCH_ITEMS[:-1])
                + f" or '{PATCH_ITEMS[-1]}'"
            ))
        word = tok.value
        res.register_advancement()
        self.advance()

        if word == 'CANVAS':
            width = res.register(self.expr())
            if res.error: return res
            height = res.register(self.expr())
            if res.error: return res
            font = None
            if self.current_tok.type != TT_NEWLINE:
                font = res.register(self.expr())
                if res.error: return res
            return res.success({'kind': word, 'tok': tok,
                               'width': width, 'height': height, 'font': font})

        if word == 'LINK':
            chains = []
            while True:
                chain = [res.register(self.link_ref())]
                if res.error: return res
                while self.current_tok.type == TT_ARROW:
                    res.register_advancement()
                    self.advance()
                    nxt = res.register(self.link_ref())
                    if res.error: return res
                    chain.append(nxt)
                if len(chain) < 2:
                    return res.failure(InvalidSyntaxError(
                        tok.pos_start, self.current_tok.pos_end,
                        "Expected '->' and a box after the link name"
                    ))
                chains.append(chain)
                if self.current_tok.type == TT_COMMA:
                    res.register_advancement()
                    self.advance()
                    continue
                break
            return res.success({'kind': word, 'tok': tok, 'chains': chains})

        item = {'kind': word, 'tok': tok, 'name': None, 'payload': None,
                'coords': None, 'id': None}

        if word in ('OBJ', 'MSG'):
            if self.current_tok.type == TT_IDENTIFIER:
                item['name'] = self.current_tok
                res.register_advancement()
                self.advance()
            item['payload'] = res.register(self.expr())
            if res.error: return res
        elif word == 'TEXT':
            item['payload'] = res.register(self.expr())
            if res.error: return res
        else:  # NUM, SYMB and TABLE are named rather than worded
            if self.current_tok.type != TT_IDENTIFIER:
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end,
                    f"Expected a name after '{word}'"
                ))
            item['name'] = self.current_tok
            res.register_advancement()
            self.advance()
            if word == 'TABLE':
                item['payload'] = res.register(self.expr())
                if res.error: return res

        if self.current_tok.type == TT_IDENTIFIER and self.current_tok.value == 'AT':
            res.register_advancement()
            self.advance()
            x = res.register(self.expr())
            if res.error: return res
            y = res.register(self.expr())
            if res.error: return res
            item['coords'] = (x, y)

        if self.current_tok.type == TT_IDENTIFIER and self.current_tok.value == 'ID':
            res.register_advancement()
            self.advance()
            if self.current_tok.type != TT_IDENTIFIER:
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end,
                    "Expected a name after 'ID'"
                ))
            if item['name'] is not None \
                    and self.current_tok.value != item['name'].value:
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end,
                    f"'ID' must match the name the {word} line carries"
                ))
            item['id'] = self.current_tok
            res.register_advancement()
            self.advance()

        return res.success(item)

    def link_ref(self):
        """One end of a LINK arrow: a box name, or the IN/OUT convention."""
        res = ParserResult()
        tok = self.current_tok
        if tok.type != TT_IDENTIFIER:
            return res.failure(InvalidSyntaxError(
                tok.pos_start, tok.pos_end,
                "Expected a box name, 'IN' or 'OUT'"
            ))
        res.register_advancement()
        self.advance()
        return res.success(tok)

    def patch_statements(self, head_tok, patch_tok, items):
        """The one pass that turns parsed items into the Tier A statements."""
        res = ParserResult()
        statements = []

        canvas = next((item for item in items if item['kind'] == 'CANVAS'), None)
        build_args = [StringNode(self._sugar_tok(TT_STRING, patch_tok.value,
                                                 patch_tok))]
        if canvas is not None:
            build_args.append(canvas['width'])
            build_args.append(canvas['height'])
            if canvas['font'] is not None:
                build_args.append(canvas['font'])
        # the block's own name is the variable the script holds the patch by
        statements.append(VarAssignNode(
            patch_tok, self._sugar_call(head_tok, 'PD_BUILD', build_args)))

        names = {'OBJ': 'PD_OBJ', 'MSG': 'PD_MSG', 'NUM': 'PD_NUM',
                 'SYMB': 'PD_SYMB', 'TABLE': 'PD_TABLE', 'TEXT': 'PD_TEXT'}
        io_boxes = {'IN': ('adc', 'adc~'), 'OUT': ('dac', 'dac~')}
        created = []

        def ref(tok):
            """A link endpoint as an expression; IN/OUT get their shared box."""
            if tok.value in io_boxes:
                var_name, text = io_boxes[tok.value]
                if var_name not in created:
                    created.append(var_name)
                    var_tok = self._sugar_tok(TT_IDENTIFIER, var_name, tok)
                    statements.append(VarAssignNode(var_tok, self._sugar_call(
                        tok, 'PD_OBJ', [
                            VarAccessNode(patch_tok),
                            StringNode(self._sugar_tok(TT_STRING, text, tok)),
                            NumberNode(self._sugar_tok(TT_INT, 0, tok)),
                            NumberNode(self._sugar_tok(TT_INT, 0, tok)),
                        ])))
                return VarAccessNode(self._sugar_tok(TT_IDENTIFIER, var_name, tok))
            return VarAccessNode(tok)

        for item in items:
            kind = item['kind']
            if kind == 'CANVAS':
                continue
            if kind == 'LINK':
                for chain in item['chains']:
                    for source, target in zip(chain, chain[1:]):
                        statements.append(self._sugar_call(
                            item['tok'], 'PD_LINK',
                            [VarAccessNode(patch_tok), ref(source), ref(target)]))
                continue

            args = [VarAccessNode(patch_tok)]
            if kind in ('NUM', 'SYMB', 'TABLE'):
                # the script's identifier is the receiver/table name Pd sees
                args.append(StringNode(self._sugar_tok(
                    TT_STRING, item['name'].value, item['name'])))
            if kind in ('OBJ', 'MSG', 'TEXT', 'TABLE'):
                args.append(item['payload'])
            if item['coords'] is not None:
                args.extend(item['coords'])

            call = self._sugar_call(item['tok'], names[kind], args)
            bind_to = item['name'] if item['name'] is not None else item['id']
            if bind_to is not None:
                statements.append(VarAssignNode(bind_to, call))
            else:
                statements.append(call)

        return res.success(ListNode(
            statements, head_tok.pos_start, self.current_tok.pos_end.copy()))

    # -- WIDGET ... END ---------------------------------------------------

    def widget_dsl(self):
        """`WIDGET k = expr` lines of AT/SET/ADD/ON sugar, desugared in order.

        Every line emits exactly the Tier A call a script would write in the
        same order it wrote it, so the block adds no sequence of its own.
        """
        res = ParserResult()
        head_tok = self.current_tok             # the word WIDGET
        res.register_advancement()
        self.advance()

        if self.current_tok.type != TT_IDENTIFIER:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                "Expected a widget name after 'WIDGET'"
            ))
        var_tok = self.current_tok
        res.register_advancement()
        self.advance()

        if self.current_tok.type != TT_EQ:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                "Expected '=' after the widget name"
            ))
        res.register_advancement()
        self.advance()

        value = res.register(self.expr())
        if res.error: return res
        statements = [VarAssignNode(var_tok, value)]

        if self.current_tok.type == TT_NEWLINE:
            error = self.widget_items(res, var_tok, statements)
            if error: return error
        elif self.current_tok.matches(TT_KEYWORD, 'END'):
            res.register_advancement()
            self.advance()
        else:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                "Expected a new line or 'END' after the widget expression"
            ))

        return res.success(ListNode(
            statements, head_tok.pos_start, self.current_tok.pos_end.copy()))

    def widget_items(self, res, var_tok, statements):
        """Item lines up to `END`; None when the block closed, the error otherwise."""
        while True:
            while self.current_tok.type == TT_NEWLINE:
                res.register_advancement()
                self.advance()
            if self.current_tok.matches(TT_KEYWORD, 'END'):
                res.register_advancement()
                self.advance()
                return None
            if self.current_tok.type == TT_EOF:
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end,
                    "Expected 'END'"
                ))
            tok = self.current_tok
            if tok.type != TT_IDENTIFIER or tok.value not in WIDGET_ITEMS:
                return res.failure(InvalidSyntaxError(
                    tok.pos_start, tok.pos_end,
                    "Expected " + ", ".join(f"'{word}'" for word in WIDGET_ITEMS[:-1])
                    + f" or '{WIDGET_ITEMS[-1]}'"
                ))
            res.register_advancement()
            self.advance()
            node = res.register(self.widget_item(tok, var_tok))
            if res.error: return res
            if node is not None:
                statements.append(node)
            if self.current_tok.type != TT_NEWLINE:
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end,
                    "Expected a new line or 'END'"
                ))
            res.register_advancement()
            self.advance()

    def widget_item(self, tok, var_tok):
        """One WIDGET-block line as the call it replaces."""
        res = ParserResult()
        word = tok.value

        if word == 'AT':
            x = res.register(self.expr())
            if res.error: return res
            y = res.register(self.expr())
            if res.error: return res
            args = [VarAccessNode(var_tok), x, y]
            if self.current_tok.type == TT_IDENTIFIER \
                    and self.current_tok.value == 'SIZE':
                res.register_advancement()
                self.advance()
                width = res.register(self.expr())
                if res.error: return res
                height = res.register(self.expr())
                if res.error: return res
                args.extend([width, height])
            return res.success(self._sugar_call(tok, 'PLACE', args))

        if word == 'SET':
            prop = res.register(self.expr())
            if res.error: return res
            value = res.register(self.expr())
            if res.error: return res
            return res.success(self._sugar_call(
                tok, 'SET', [VarAccessNode(var_tok), prop, value]))

        if word == 'ADD':
            parent = res.register(self.expr())
            if res.error: return res
            return res.success(self._sugar_call(
                tok, 'NODE_ADD', [parent, VarAccessNode(var_tok)]))

        if word == 'LINK_VALUE':
            other = res.register(self.expr())
            if res.error: return res
            return res.success(self._sugar_call(
                tok, 'LINK_VALUE', [VarAccessNode(var_tok), other]))

        if word == 'LINK_STATE':
            prop = res.register(self.expr())
            if res.error: return res
            other = res.register(self.expr())
            if res.error: return res
            return res.success(self._sugar_call(
                tok, 'LINK_STATE', [VarAccessNode(var_tok), prop, other]))

        # ON: the event word picks the ON_* binding; FRAME has no widget
        # to key on, so it keeps its one-argument Tier A shape even here
        event_tok = self.current_tok
        event = event_tok.value.upper() if event_tok.type == TT_IDENTIFIER else ''
        if event not in ON_EVENTS:
            return res.failure(InvalidSyntaxError(
                event_tok.pos_start, event_tok.pos_end,
                "Expected an event word: " + ", ".join(sorted(ON_EVENTS))
            ))
        res.register_advancement()
        self.advance()
        handler = res.register(self.expr())
        if res.error: return res
        if event == 'FRAME':
            return res.success(self._sugar_call(tok, 'ON_FRAME', [handler]))
        return res.success(self._sugar_call(
            tok, f'ON_{event}', [VarAccessNode(var_tok), handler]))

    # -- ON w EVENT fn ------------------------------------------------------

    def on_statement(self):
        """`ON w EVENT handler` as `ON_EVENT(w, handler)`."""
        res = ParserResult()
        head_tok = self.current_tok             # the word ON
        res.register_advancement()
        self.advance()

        widget_tok = self.current_tok
        if widget_tok.type != TT_IDENTIFIER:
            return res.failure(InvalidSyntaxError(
                widget_tok.pos_start, widget_tok.pos_end,
                "Expected the widget to wire after 'ON'"
            ))
        res.register_advancement()
        self.advance()

        event_tok = self.current_tok
        event = event_tok.value.upper() if event_tok.type == TT_IDENTIFIER else ''
        if event not in ON_EVENTS:
            return res.failure(InvalidSyntaxError(
                event_tok.pos_start, event_tok.pos_end,
                "Expected an event word: " + ", ".join(sorted(ON_EVENTS))
            ))
        res.register_advancement()
        self.advance()

        handler = res.register(self.expr())
        if res.error: return res
        if event == 'FRAME':
            # a frame handler belongs to the context, not the widget
            return res.success(self._sugar_call(head_tok, 'ON_FRAME', [handler]))
        return res.success(self._sugar_call(
            head_tok, f'ON_{event}', [VarAccessNode(widget_tok), handler]))

    def func_def(self):
        res = ParserResult()

        if not self.current_tok.matches(TT_KEYWORD, 'FUNC'):
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                f"Expected 'FUNC'"
            ))

        res.register_advancement()
        self.advance()

        # Function name (optional, for anonymous functions)
        var_name_tok = None
        if self.current_tok.type == TT_IDENTIFIER:
            var_name_tok = self.current_tok
            res.register_advancement()
            self.advance()
        elif self.current_tok.type != TT_LPAREN:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                f"Expected identifier or '('"
            ))

        # Opening parenthesis
        if self.current_tok.type != TT_LPAREN:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                f"Expected '('"
            ))
        res.register_advancement()
        self.advance()

        # Parse arguments
        arg_name_toks = []
        if self.current_tok.type == TT_RPAREN:
            # No arguments
            res.register_advancement()
            self.advance()
        else:
            # First argument
            if self.current_tok.type != TT_IDENTIFIER:
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end,
                    "Expected identifier"
                ))
            arg_name_toks.append(self.current_tok)
            res.register_advancement()
            self.advance()

            # Additional arguments after commas
            while self.current_tok.type == TT_COMMA:
                res.register_advancement()
                self.advance()
                if self.current_tok.type != TT_IDENTIFIER:
                    return res.failure(InvalidSyntaxError(
                        self.current_tok.pos_start, self.current_tok.pos_end,
                        "Expected identifier"
                    ))
                arg_name_toks.append(self.current_tok)
                res.register_advancement()
                self.advance()

            # Expect closing parenthesis
            if self.current_tok.type != TT_RPAREN:
                return res.failure(InvalidSyntaxError(
                    self.current_tok.pos_start, self.current_tok.pos_end,
                    "Expected ')'"
                ))
            res.register_advancement()
            self.advance()

        # Arrow token (must be present)
        if self.current_tok.type == TT_ARROW:

            res.register_advancement()
            self.advance()

            # Function body
            body = res.register(self.expr())
            if res.error: return res

            return res.success(FuncDefNode(
                var_name_tok,
                arg_name_toks,
                body,
                True
            ))

        if self.current_tok.type != TT_NEWLINE:
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                f"Expected '->' of NEWLINE"
            ))
        res.register_advancement()
        self.advance()

        body = res.register(self.statements())
        if res.error: return res

        if not self.current_tok.matches(TT_KEYWORD, 'END'):
            return res.failure(InvalidSyntaxError(
                self.current_tok.pos_start, self.current_tok.pos_end,
                f"Expected 'END'"
            ))

        res.register_advancement()
        self.advance()

        return res.success(FuncDefNode(
            var_name_tok,
            arg_name_toks,
            body,
            False
        ))



# RUNTIME RESULT
