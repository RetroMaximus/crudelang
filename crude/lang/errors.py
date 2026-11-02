
"""Lexer, parser and runtime errors, rendered with a source excerpt."""

from __future__ import annotations

from .formatting import str_w_arrows


# Errors

class Error:
    def __init__(self, pos_start, pos_end, error_name, details):
        self.pos_start = pos_start
        self.pos_end = pos_end
        self.error_name = error_name
        self.details = details

    def as_string(self):
        result = f'{self.error_name}: {self.details}'
        result += f'\nfile {self.pos_start.fn}, @ln:{self.pos_start.ln + 1}'
        result += '\n\n' + str_w_arrows(self.pos_start.ftxt, self.pos_start, self.pos_end)
        return result


class IllegalCharError(Error):
    def __init__(self, pos_start, pos_end, details):
        super().__init__(pos_start, pos_end, 'Illegal Character', details)


class ExpectedCharError(Error):
    def __init__(self, pos_start, pos_end, details):
        super().__init__(pos_start, pos_end, 'Expected character', details)


class InvalidSyntaxError(Error):
    def __init__(self, pos_start, pos_end, details=''):
        super().__init__(pos_start, pos_end, 'Invalid Syntax', details)


class RTError(Error):
    def __init__(self, pos_start, pos_end, details, context):
        super().__init__(pos_start, pos_end, 'Runtime Error', details)
        self.context = context

    def as_string(self):
        result = self.generate_trace()
        result += f'{self.error_name}: {self.details}'
        result += '\n\n' + str_w_arrows(self.pos_start.ftxt, self.pos_start, self.pos_end)

        return result

    def generate_trace(self):
        result = ''
        pos = self.pos_start
        ctx = self.context

        while ctx:
            result = f'    File {pos.fn}, @ln {str(pos.ln + 1)}, in {ctx.display_name}\n'
            pos = ctx.parent_entry_pos
            ctx = ctx.parent  # <-- FIXED: use ctx.parent, not ctx.parent_entry_pos

        return 'Traceback (most recent call last):\n' + result
