"""Context, symbol table and the tree-walking interpreter.

The interpreter is a straight port of the tutorial implementation that used to
live in ``crudelang.py``, with these deliberate differences:

* a value is looked up with ``is None`` instead of a truthiness test, so
  ``null``/``0``/``""`` are valid variable contents;
* ``visit_BinOpNode`` always answers, even for an unknown operator token;
* loop bodies can update enclosing variables (they always could - a loop body
  is visited in the caller's context, not in a child scope - so ``VAR`` keeps
  binding in the current scope and *shadowing* across function boundaries
  still behaves exactly like the original interpreter);
* ``SymbolTable.assign`` additionally walks the scope chain for host code that
  needs it; the language's ``VAR`` statement does not use it;
* a loop body that raises **ends the loop**. The tutorial's guard reads
  ``should_return() and loop_should_continue is False``, and a failure leaves
  ``loop_should_continue`` at ``None`` rather than ``False``, so an error in a body
  was swallowed and the condition went on answering the same - a script that
  hangs and grows instead of saying what is wrong. The error is returned first.
"""

from __future__ import annotations

from .errors import RTError
from .result import RTResult
from .tokens import (
    TT_DIV,
    TT_EE,
    TT_GT,
    TT_GTE,
    TT_KEYWORD,
    TT_LT,
    TT_LTE,
    TT_MINUS,
    TT_MUL,
    TT_NE,
    TT_PLUS,
    TT_POW,
)
from .values import Function, List, Number, String


class Context:
    def __init__(self, display_name, parent=None, parent_entry_pos=None):
        self.display_name = display_name
        self.parent = parent
        self.parent_entry_pos = parent_entry_pos
        self.symbol_table = None
        #: the argument names a binding call actually passed, or ``None`` when
        #: this context is not a binding call. A binding must not read an
        #: argument it was not given, because the lookup below would otherwise
        #: find a script variable of the same name and hand it in as the
        #: argument - see :class:`crude.bindings.registry.HostContext`.
        self.provided_args = None


class SymbolTable:
    def __init__(self, parent=None):
        self.symbols = {}
        self.parent = parent
        self.host = None

    def get(self, name):
        value = self.symbols.get(name, None)
        if value is None and self.parent:
            return self.parent.get(name)
        return value

    def set(self, name, value):
        """Bind ``name`` in this scope, shadowing any outer binding."""
        self.symbols[name] = value

    def assign(self, name, value):
        """Bind ``name`` in the nearest scope that already declares it.

        Falls back to the current scope, so ``VAR x = 1`` still introduces a
        variable when nobody has declared it yet.
        """
        table = self
        while table is not None:
            if name in table.symbols:
                table.symbols[name] = value
                return
            table = table.parent
        self.symbols[name] = value

    def has(self, name):
        return self.get(name) is not None

    def find_host(self):
        """The host attached to the outermost table that has one, or ``None``.

        Bindings reach their subsystem (``host.pd``, ``host.audio``) through
        this, so a function frame only needs its parent chain - no global.
        """
        table = self
        while table is not None:
            if table.host is not None:
                return table.host
            table = table.parent
        return None

    def remove(self, name):
        del self.symbols[name]

    def __repr__(self):
        return f"<symbol table {sorted(self.symbols)}>"


class Interpreter:
    def visit(self, node, context):
        method_name = f"visit_{type(node).__name__}"
        method = getattr(self, method_name, self.no_visit_method)
        return method(node, context)

    def no_visit_method(self, node, context):
        raise Exception(f"No visit_{type(node).__name__} method defined")

    # -- literals -------------------------------------------------------
    def visit_NumberNode(self, node, context):
        return RTResult().success(
            Number(node.tok.value).set_context(context).set_pos(node.pos_start, node.pos_end)
        )

    def visit_StringNode(self, node, context):
        return RTResult().success(
            String(node.tok.value).set_context(context).set_pos(node.pos_start, node.pos_end)
        )

    def visit_ListNode(self, node, context):
        res = RTResult()
        elements = []

        for element_node in node.element_nodes:
            elements.append(res.register(self.visit(element_node, context)))
            if res.should_return():
                return res

        return res.success(
            List(elements).set_context(context).set_pos(node.pos_start, node.pos_end)
        )

    # -- variables ------------------------------------------------------
    def visit_VarAccessNode(self, node, context):
        res = RTResult()
        var_name = node.var_name_tok.value
        value = context.symbol_table.get(var_name)

        if value is None:
            return res.failure(RTError(
                node.pos_start, node.pos_end,
                f"'{var_name}' is not defined",
                context,
            ))

        value = value.copy().set_pos(node.pos_start, node.pos_end).set_context(context)
        return res.success(value)

    def visit_VarAssignNode(self, node, context):
        res = RTResult()
        var_name = node.var_name_tok.value
        value = res.register(self.visit(node.value_node, context))
        if res.should_return():
            return res

        context.symbol_table.set(var_name, value)
        return res.success(value)

    # -- operators ------------------------------------------------------
    def visit_BinOpNode(self, node, context):
        res = RTResult()
        left = res.register(self.visit(node.left_node, context))
        if res.should_return():
            return res
        right = res.register(self.visit(node.right_node, context))
        if res.should_return():
            return res

        handlers = {
            TT_PLUS: left.added_to,
            TT_MINUS: left.subbed_by,
            TT_MUL: left.multed_by,
            TT_DIV: left.divided_by,
            TT_POW: left.powed_by,
            TT_EE: left.get_comparison_eq,
            TT_NE: left.get_comparison_ne,
            TT_LT: left.get_comparison_lt,
            TT_GT: left.get_comparison_gt,
            TT_LTE: left.get_comparison_lte,
            TT_GTE: left.get_comparison_gte,
        }

        handler = handlers.get(node.op_tok.type)

        if handler is None and node.op_tok.matches(TT_KEYWORD, "AND"):
            handler = left.anded_by
        elif handler is None and node.op_tok.matches(TT_KEYWORD, "OR"):
            handler = left.ored_by

        if handler is None:
            return res.failure(RTError(
                node.op_tok.pos_start, node.op_tok.pos_end,
                f"Unknown operator: {node.op_tok.type}",
                context,
            ))

        result, error = handler(right)

        if error:
            return res.failure(error)
        if result is None:
            return res.failure(RTError(
                node.pos_start, node.pos_end, "Operation failed", context,
            ))
        return res.success(result.set_pos(node.pos_start, node.pos_end))

    def visit_UnaryOpNode(self, node, context):
        res = RTResult()
        value = res.register(self.visit(node.node, context))
        if res.should_return():
            return res

        error = None

        if node.op_tok.type == TT_MINUS:
            value, error = value.multed_by(Number(-1))
        elif node.op_tok.matches(TT_KEYWORD, "NOT"):
            value, error = value.notted()

        if error:
            return res.failure(error)
        return res.success(value.set_pos(node.pos_start, node.pos_end))

    # -- control flow ---------------------------------------------------
    def visit_IfNode(self, node, context):
        res = RTResult()
        for condition, expr, should_return_null in node.cases:
            condition_value = res.register(self.visit(condition, context))
            if res.should_return():
                return res

            if condition_value.is_true():
                expr_value = res.register(self.visit(expr, context))
                if res.should_return():
                    return res
                return res.success(Number.null if should_return_null else expr_value)

        if node.else_case:
            expr, should_return_null = node.else_case
            else_value = res.register(self.visit(expr, context))
            if res.should_return():
                return res
            return res.success(Number.null if should_return_null else else_value)

        return res.success(Number.null)

    def visit_ForNode(self, node, context):
        res = RTResult()
        elements = []

        start_value = res.register(self.visit(node.start_value_node, context))
        if res.should_return():
            return res

        end_value = res.register(self.visit(node.end_value_node, context))
        if res.should_return():
            return res

        if node.step_value_node:
            step_value = res.register(self.visit(node.step_value_node, context))
            if res.should_return():
                return res
        else:
            step_value = Number(1)

        i = start_value.value

        if step_value.value >= 0:
            def condition():
                return i < end_value.value
        else:
            def condition():
                return i > end_value.value

        while condition():
            context.symbol_table.set(node.var_name_tok.value, Number(i))
            i += step_value.value

            value = res.register(self.visit(node.body_node, context))
            # an error in a body is an answer, not a lap: ``failure`` leaves the two
            # loop flags at ``None``, so the guard below cannot see it, and the
            # condition would go on returning the same truth forever
            if res.error is not None:
                return res
            if res.should_return() and res.loop_should_continue is False and res.loop_should_break is False:
                return res

            if res.loop_should_continue:
                continue

            if res.loop_should_break:
                break

            elements.append(value)

        if node.should_return_null:
            return res.success(Number.null)
        return res.success(
            List(elements).set_context(context).set_pos(node.pos_start, node.pos_end)
        )

    def visit_WhileNode(self, node, context):
        res = RTResult()
        elements = []

        while True:
            condition = res.register(self.visit(node.condition_node, context))
            if res.should_return():
                return res

            if not condition.is_true():
                break

            value = res.register(self.visit(node.body_node, context))
            # see ``visit_ForNode``: a body that raises ends the loop rather than
            # spinning in it with the error thrown away
            if res.error is not None:
                return res
            if res.should_return() and res.loop_should_continue is False and res.loop_should_break is False:
                return res

            if res.loop_should_continue:
                continue

            if res.loop_should_break:
                break

            elements.append(value)

        if node.should_return_null:
            return res.success(Number.null)
        return res.success(
            List(elements).set_context(context).set_pos(node.pos_start, node.pos_end)
        )

    # -- functions ------------------------------------------------------
    def visit_FuncDefNode(self, node, context):
        res = RTResult()

        func_name = node.var_name_tok.value if node.var_name_tok else None
        arg_names = [arg_name.value for arg_name in node.arg_name_toks]
        func_value = (
            Function(func_name, node.body_node, arg_names, node.should_auto_return)
            .set_context(context)
            .set_pos(node.pos_start, node.pos_end)
        )

        if node.var_name_tok:
            context.symbol_table.set(func_name, func_value)

        return res.success(func_value)

    def visit_CallNode(self, node, context):
        res = RTResult()
        args = []

        value_to_call = res.register(self.visit(node.node_to_call, context))
        if res.should_return():
            return res
        value_to_call = value_to_call.copy().set_pos(node.pos_start, node.pos_end)

        for arg_node in node.arg_nodes:
            args.append(res.register(self.visit(arg_node, context)))
            if res.should_return():
                return res

        return_value = res.register(value_to_call.execute(args))
        if res.should_return():
            return res
        return_value = return_value.copy().set_pos(node.pos_start, node.pos_end).set_context(context)
        return res.success(return_value)

    def visit_ReturnNode(self, node, context):
        res = RTResult()
        if node.node_to_return is not None:
            value = res.register(self.visit(node.node_to_return, context))
            if res.should_return():
                return res
        else:
            value = Number.null

        return res.success_return(value)

    def visit_ContinueNode(self, node, context):
        return RTResult().success_continue()

    def visit_BreakNode(self, node, context):
        return RTResult().success_break()
