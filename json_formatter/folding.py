"""扫描格式化后的 JSON，找出可以折叠的多行对象和数组。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fold:
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    open_char: str

    @property
    def key(self) -> tuple[int, int]:
        return (self.start_line, self.start_col)

    def contains(self, other: Fold) -> bool:
        return (self.start_line, self.start_col) < (other.start_line, other.start_col) and (
            self.end_line,
            self.end_col,
        ) > (other.end_line, other.end_col)


def find_folds(text: str) -> list[Fold]:
    """返回跨多行的括号对。行列从 1 和 0 起算，与 Tk 文本索引一致。"""
    folds: list[Fold] = []
    stack: list[tuple[int, int, str]] = []
    line = 1
    column = 0
    index = 0
    length = len(text)
    in_string = False

    while index < length:
        char = text[index]
        if in_string:
            if char == "\\":
                index, line, column = _skip_escaped(text, index, line, column)
                continue
            if char == '"':
                in_string = False
            index, line, column = _advance(text, index, line, column)
            continue
        if char == '"':
            in_string = True
            index, line, column = _advance(text, index, line, column)
            continue
        if char in "{[":
            stack.append((line, column, char))
            index, line, column = _advance(text, index, line, column)
            continue
        if char in "}]":
            if stack:
                start_line, start_col, open_char = stack.pop()
                if start_line != line:
                    folds.append(Fold(start_line, start_col, line, column, open_char))
            index, line, column = _advance(text, index, line, column)
            continue
        index, line, column = _advance(text, index, line, column)
    return folds


def _advance(text: str, index: int, line: int, column: int) -> tuple[int, int, int]:
    if text[index] == "\n":
        return index + 1, line + 1, 0
    return index + 1, line, column + 1


def _skip_escaped(text: str, index: int, line: int, column: int) -> tuple[int, int, int]:
    index, line, column = _advance(text, index, line, column)
    if index < len(text):
        index, line, column = _advance(text, index, line, column)
    return index, line, column
