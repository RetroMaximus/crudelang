"""Source excerpt rendering for error messages.

Same output as the historical ``string_w_arrows.str_w_arrows`` (which stays on
disk untouched for the old scripts), but without the bug in the original that
assigned to ``pos_end.ln`` while computing the line count.
"""

from __future__ import annotations

from .position import Position


def str_w_arrows(text: str, pos_start: Position, pos_end: Position) -> str:
    """Underline ``pos_start..pos_end`` inside the source excerpt of ``text``."""
    result = ""

    idx_start = max(text.rfind("\n", 0, pos_start.idx), 0)
    idx_end = text.find("\n", idx_start + 1)
    if idx_end < 0:
        idx_end = len(text)

    line_count = max(pos_end.ln - pos_start.ln + 1, 1)

    for i in range(line_count):
        line = text[idx_start:idx_end]
        col_start = pos_start.col if i == 0 else 0
        col_end = pos_end.col if i == line_count - 1 else len(line) - 1

        result += line + "\n"
        result += " " * col_start + "^" * max(col_end - col_start, 1)

        idx_start = idx_end
        idx_end = text.find("\n", idx_start + 1)
        if idx_end < 0:
            idx_end = len(text)

    return result.replace("\t", "")
