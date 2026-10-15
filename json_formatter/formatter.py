"""把 JSON 文本格式化或压缩成一行。"""

from __future__ import annotations

import json

class FormatError(Exception):
    def __init__(self, message: str, *, line: int, column: int):
        super().__init__(message)
        self.line = line
        self.column = column


_MESSAGE_PREFIXES = (
    ("Expecting value", "这里应该是一个值"),
    ("Expecting property name enclosed in double quotes", "属性名需要用双引号"),
    ("Expecting ':' delimiter", "这里缺少冒号"),
    ("Expecting ',' delimiter", "这里缺少逗号"),
    ("Unterminated string", "字符串没有结束"),
    ("Invalid control character", "字符串里有非法控制字符"),
    ("Invalid \\uXXXX escape", "非法 Unicode 转义"),
    ("Invalid \\escape", "非法转义"),
    ("Extra data", "结尾还有多余内容"),
)


def format_json(text: str, indent: int = 2) -> str:
    if indent not in (2, 4):
        raise ValueError("缩进只能是 2 或 4")
    return json.dumps(_parse(text), indent=indent, ensure_ascii=False) + "\n"


def minify_json(text: str) -> str:
    return json.dumps(_parse(text), separators=(",", ":"), ensure_ascii=False)


def _parse(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        detail = _chinese_message(err.msg)
        message = f"第 {err.lineno} 行，第 {err.colno} 列：{detail}"
        raise FormatError(message, line=err.lineno, column=err.colno) from err


def _chinese_message(message: str) -> str:
    for prefix, text in _MESSAGE_PREFIXES:
        if message.startswith(prefix):
            return text
    return message
