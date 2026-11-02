"""The control-flow result record shared by the interpreter and the values.

`RTResult` is the tutorial's signal bus: a single object carries "a value", "an
error", "return with this value", "continue" and "break". Values and builtins
need it, so it lives in its own module to keep ``values`` and ``interpreter``
free of an import cycle.
"""

from __future__ import annotations


class RTResult:
    def __init__(self):
        self.func_return_value = None
        self.loop_should_continue = None
        self.loop_should_break = None
        self.value = None
        self.error = None

    def reset(self):
        self.value = None
        self.error = None
        self.func_return_value = None
        self.loop_should_continue = False
        self.loop_should_break = False

    def register(self, res: "RTResult"):
        self.error = res.error
        self.func_return_value = res.func_return_value
        self.loop_should_continue = res.loop_should_continue
        self.loop_should_break = res.loop_should_break
        return res.value

    def success(self, value):
        self.reset()
        self.value = value
        return self

    def success_return(self, value):
        self.reset()
        self.func_return_value = value
        return self

    def success_continue(self):
        self.reset()
        self.loop_should_continue = True
        return self

    def success_break(self):
        self.reset()
        self.loop_should_break = True
        return self

    def failure(self, error):
        self.error = error
        return self

    def should_return(self):
        return (
            self.error
            or self.func_return_value
            or self.loop_should_continue
            or self.loop_should_break
        )
