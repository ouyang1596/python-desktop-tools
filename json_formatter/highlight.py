"""给 JSON 文本标出键名、字符串、数字、布尔和 null，不要求整段能解析。"""

from __future__ import annotations

from dataclasses import dataclass

TAGS = {
    "key": "json_key",
    "string": "json_string",
    "number": "json_number",
    "boolean": "json_boolean",
    "null": "json_null",
}

COLORS = {
    "key": ("#1565C0", "#90CAF9"),
    "string": ("#2E7D32", "#A5D6A7"),
    "number": ("#E65100", "#FFCC80"),
    "boolean": ("#6A1B9A", "#CE93D8"),
    "null": ("#546E7A", "#B0BEC5"),
}

_LITERALS = (
    ("false", "boolean"),
    ("true", "boolean"),
    ("null", "null"),
)
_BATCH = 800


@dataclass(frozen=True)
class Span:
    kind: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int


def find_spans(text: str) -> list[Span]:
    """返回高亮区间。行列从 1 和 0 起算，结束位置是半开区间，与 Tk 文本索引一致。"""
    spans: list[Span] = []
    line = 1
    column = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == '"':
            span, index, line, column = _scan_string(text, index, line, column)
            spans.append(span)
            continue
        if char == "-" or char.isdigit():
            span, index, line, column = _scan_number(text, index, line, column)
            if span is not None:
                spans.append(span)
            continue
        if char in "tfn":
            span, index, line, column = _scan_literal(text, index, line, column)
            if span is not None:
                spans.append(span)
            continue
        index, line, column = _advance(text, index, line, column)
    return spans


def configure_highlight(textbox, mode: str) -> None:
    """按浅色或深色设置 tag 前景色，不改文本。"""
    dark = mode.lower() == "dark"
    for kind, tag in TAGS.items():
        light, dark_color = COLORS[kind]
        textbox.tag_configure(tag, foreground=dark_color if dark else light)


def apply_highlight(textbox, text: str) -> None:
    """按当前文本重打高亮 tag。只改 tag，不插入或删除字符。"""
    grouped: dict[str, list[str]] = {kind: [] for kind in TAGS}
    for span in find_spans(text):
        indices = grouped[span.kind]
        indices.append(f"{span.start_line}.{span.start_col}")
        indices.append(f"{span.end_line}.{span.end_col}")
    for kind, tag in TAGS.items():
        textbox.tag_remove(tag, "1.0", "end")
        indices = grouped[kind]
        for start in range(0, len(indices), _BATCH * 2):
            textbox.tag_add(tag, *indices[start : start + _BATCH * 2])


def _scan_string(text: str, index: int, line: int, column: int) -> tuple[Span, int, int, int]:
    start_line, start_col = line, column
    index, line, column = _advance(text, index, line, column)
    closed = False
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\":
            index, line, column = _skip_escaped(text, index, line, column)
            continue
        if char == '"':
            index, line, column = _advance(text, index, line, column)
            closed = True
            break
        if char == "\n":
            break
        index, line, column = _advance(text, index, line, column)
    kind = "key" if closed and _followed_by_colon(text, index) else "string"
    return Span(kind, start_line, start_col, line, column), index, line, column


def _followed_by_colon(text: str, index: int) -> bool:
    length = len(text)
    while index < length and text[index].isspace():
        index += 1
    return index < length and text[index] == ":"


def _scan_number(text: str, index: int, line: int, column: int) -> tuple[Span | None, int, int, int]:
    start_line, start_col = line, column
    length = len(text)
    if text[index] == "-":
        if index + 1 >= length or not text[index + 1].isdigit():
            index, line, column = _advance(text, index, line, column)
            return None, index, line, column
        index, line, column = _advance(text, index, line, column)
    if index >= length or not text[index].isdigit():
        index, line, column = _advance(text, index, line, column)
        return None, index, line, column
    if text[index] == "0":
        index, line, column = _advance(text, index, line, column)
    else:
        while index < length and text[index].isdigit():
            index, line, column = _advance(text, index, line, column)
    if index + 1 < length and text[index] == "." and text[index + 1].isdigit():
        index, line, column = _advance(text, index, line, column)
        while index < length and text[index].isdigit():
            index, line, column = _advance(text, index, line, column)
    if index < length and text[index] in "eE":
        exp = index + 1
        if exp < length and text[exp] in "+-":
            exp += 1
        if exp < length and text[exp].isdigit():
            index, line, column = _advance(text, index, line, column)
            if index < length and text[index] in "+-":
                index, line, column = _advance(text, index, line, column)
            while index < length and text[index].isdigit():
                index, line, column = _advance(text, index, line, column)
    return Span("number", start_line, start_col, line, column), index, line, column


def _scan_literal(text: str, index: int, line: int, column: int) -> tuple[Span | None, int, int, int]:
    if index > 0 and _is_word(text[index - 1]):
        index, line, column = _advance(text, index, line, column)
        return None, index, line, column
    for word, kind in _LITERALS:
        end = index + len(word)
        if not text.startswith(word, index):
            continue
        if end < len(text) and _is_word(text[end]):
            continue
        start_line, start_col = line, column
        for _ in word:
            index, line, column = _advance(text, index, line, column)
        return Span(kind, start_line, start_col, line, column), index, line, column
    index, line, column = _advance(text, index, line, column)
    return None, index, line, column


def _is_word(char: str) -> bool:
    return char.isalnum() or char == "_"


def _advance(text: str, index: int, line: int, column: int) -> tuple[int, int, int]:
    if text[index] == "\n":
        return index + 1, line + 1, 0
    return index + 1, line, column + 1


def _skip_escaped(text: str, index: int, line: int, column: int) -> tuple[int, int, int]:
    index, line, column = _advance(text, index, line, column)
    if index < len(text):
        index, line, column = _advance(text, index, line, column)
    return index, line, column
