"""Android 色值解析与格式化。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class ConvertError(Exception):
    """转换失败时的说明。"""


@dataclass(frozen=True)
class ColorValue:
    red: int
    green: int
    blue: int
    alpha: int

    @property
    def argb_hex(self) -> str:
        return f"#{self.alpha:02X}{self.red:02X}{self.green:02X}{self.blue:02X}"

    @property
    def rgb_hex(self) -> str:
        return f"#{self.red:02X}{self.green:02X}{self.blue:02X}"

    @property
    def parse_color(self) -> str:
        return f'Color.parseColor("{self.argb_hex}")'

    @property
    def literal(self) -> str:
        return f"0x{self.alpha:02X}{self.red:02X}{self.green:02X}{self.blue:02X}"

    @property
    def channels(self) -> str:
        return f"{self.red} {self.green} {self.blue}"

    @property
    def alpha_percent(self) -> int:
        return _alpha_to_percent(self.alpha)

    @property
    def alpha_text(self) -> str:
        return f"{self.alpha_percent}%（{self.alpha}）"


_PARSE_COLOR = re.compile(
    r"""
    ^(?:[\w.]+\.)?parseColor\s*\(\s*
    (?P<quote>['"])
    (?P<value>.*?)
    (?P=quote)
    \s*\)\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)
_RGB = re.compile(
    r"""
    ^rgba?\s*\(\s*
    (?P<r>\d{1,3})\s*,\s*
    (?P<g>\d{1,3})\s*,\s*
    (?P<b>\d{1,3})
    (?:\s*,\s*(?P<a>[^)]+))?
    \s*\)\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)
_HEX_DIGITS = re.compile(r"^[0-9A-Fa-f]+$")


def parse_color(text: str) -> ColorValue:
    """解析 Android 色值。

    接受 #RGB、#ARGB、#RRGGBB、#AARRGGBB（可省略 #）、0x 加 6 或 8 位、
    Color.parseColor("...")、rgb() 和 rgba()。短写按 Android 规则展开。
    """
    raw = text.strip()
    if not raw:
        raise ConvertError("请输入颜色")
    if len(raw) > 120:
        raise ConvertError("无法解析这个颜色")
    wrapped = _PARSE_COLOR.match(raw)
    if wrapped is not None:
        return parse_color(wrapped.group("value"))
    if raw[:3].lower() == "rgb":
        return _parse_rgb(raw)
    return _parse_hex(raw)


def with_alpha_percent(color: ColorValue, percent: int) -> ColorValue:
    """保留 RGB，把透明度改成 0 到 100 的百分比。"""
    if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
        raise ConvertError("透明度需要在 0 到 100 之间")
    return ColorValue(color.red, color.green, color.blue, _percent_to_alpha(percent))


def _parse_hex(text: str) -> ColorValue:
    prefix = text[:2].lower()
    if prefix == "0x":
        digits = text[2:]
        if len(digits) not in (6, 8) or _HEX_DIGITS.fullmatch(digits) is None:
            raise ConvertError("无法解析这个颜色")
    else:
        digits = text[1:] if text.startswith("#") else text
        if len(digits) not in (3, 4, 6, 8) or _HEX_DIGITS.fullmatch(digits) is None:
            raise ConvertError("无法解析这个颜色")
    alpha, red, green, blue = _expand_hex(digits)
    return ColorValue(red, green, blue, alpha)


def _expand_hex(digits: str) -> tuple[int, int, int, int]:
    digits = digits.upper()
    if len(digits) == 3:
        red, green, blue = (part * 2 for part in digits)
        alpha = "FF"
    elif len(digits) == 4:
        alpha, red, green, blue = (part * 2 for part in digits)
    elif len(digits) == 6:
        red, green, blue = digits[0:2], digits[2:4], digits[4:6]
        alpha = "FF"
    else:
        alpha, red, green, blue = digits[0:2], digits[2:4], digits[4:6], digits[6:8]
    return int(alpha, 16), int(red, 16), int(green, 16), int(blue, 16)


def _parse_rgb(text: str) -> ColorValue:
    match = _RGB.fullmatch(text)
    if match is None:
        raise ConvertError("无法解析这个颜色")
    is_rgba = text.lower().startswith("rgba")
    alpha_token = match.group("a")
    if is_rgba == (alpha_token is None):
        raise ConvertError("无法解析这个颜色")
    red = _channel(match.group("r"))
    green = _channel(match.group("g"))
    blue = _channel(match.group("b"))
    alpha = 255 if alpha_token is None else _parse_alpha(alpha_token)
    return ColorValue(red, green, blue, alpha)


def _channel(token: str) -> int:
    value = int(token)
    if value > 255:
        raise ConvertError("颜色分量超出 0 到 255")
    return value


def _parse_alpha(token: str) -> int:
    token = token.strip()
    if token.endswith("%"):
        try:
            percent = Decimal(token[:-1].strip())
        except InvalidOperation:
            raise ConvertError("无法解析这个颜色") from None
        if percent < 0 or percent > 100:
            raise ConvertError("透明度超出范围")
        return _quantize(percent * Decimal(255) / Decimal(100))
    try:
        value = Decimal(token)
    except InvalidOperation:
        raise ConvertError("无法解析这个颜色") from None
    if value < 0:
        raise ConvertError("透明度超出范围")
    if "." in token or value <= 1:
        if value > 1:
            raise ConvertError("透明度超出范围")
        return _quantize(value * Decimal(255))
    if value > 255 or value != value.to_integral_value():
        raise ConvertError("透明度超出范围")
    return int(value)


def _alpha_to_percent(alpha: int) -> int:
    return _quantize(Decimal(alpha) * Decimal(100) / Decimal(255))


def _percent_to_alpha(percent: int) -> int:
    return _quantize(Decimal(percent) * Decimal(255) / Decimal(100))


def _quantize(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
