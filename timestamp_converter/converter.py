"""Unix 时间戳与日期时间互转。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from zoneinfo import ZoneInfo


class ConvertError(Exception):
    """转换失败时的说明。"""


@dataclass(frozen=True)
class DateTimes:
    local: str
    utc: str
    iso: str


@dataclass(frozen=True)
class Timestamps:
    seconds: str
    milliseconds: str
    embedded_zone: bool = False


_NAIVE_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S.%f",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
)
_TZ_SUFFIX = re.compile(
    r"""
    \s*
    (?:
        (?P<z>[Zz])
        |
        (?P<sign>[+-])(?P<hh>\d{2}):(?P<mm>\d{2})
        |
        (?P<sign4>[+-])(?P<hh4>\d{2})(?P<mm4>\d{2})
    )
    $
    """,
    re.VERBOSE,
)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def timestamp_to_datetimes(value: str, unit: str) -> DateTimes:
    """把时间戳转成本地时间、UTC 和 ISO 8601。

    unit 取 auto、second 或 millisecond。auto 时，整数部分不超过 10 位按秒，否则按毫秒。
    """
    micro = _to_microseconds(_parse_seconds(value, unit))
    try:
        moment = _EPOCH + timedelta(microseconds=micro)
        local = moment.astimezone(_local_zone())
    except (OverflowError, ValueError, OSError):
        raise ConvertError("时间戳超出可表示范围") from None
    return DateTimes(local=_format_wall(local), utc=_format_wall(moment), iso=_format_iso(local))


def datetime_to_timestamps(text: str, zone: str) -> Timestamps:
    """把日期时间转成秒和毫秒时间戳。

    zone 取 local 或 utc。字符串自带 Z 或数值偏移时，以字符串中的时区为准。
    """
    raw = text.strip()
    if not raw:
        raise ConvertError("请输入日期时间")
    if len(raw) > 64:
        raise ConvertError("无法解析这个日期时间")
    embedded, body = _split_zone(raw)
    naive = _parse_naive(body)
    if embedded is not None:
        aware = naive.replace(tzinfo=embedded)
    elif zone == "utc":
        aware = naive.replace(tzinfo=timezone.utc)
    elif zone == "local":
        aware = naive.replace(tzinfo=_local_zone())
    else:
        raise ConvertError("时区只能是本地或 UTC")
    try:
        seconds, milliseconds = _format_timestamps(aware)
    except (OverflowError, ValueError, OSError):
        raise ConvertError("时间戳超出可表示范围") from None
    return Timestamps(seconds, milliseconds, embedded is not None)


def current_clock() -> tuple[str, str, str]:
    """当前这一秒的本地时间、秒时间戳和毫秒时间戳。"""
    local = datetime.now(timezone.utc).astimezone(_local_zone()).replace(microsecond=0)
    seconds, milliseconds = _format_timestamps(local)
    return _format_wall(local), seconds, milliseconds


def _local_zone() -> tzinfo:
    """本机时区。

    不调用无参数的 astimezone：Windows 上它大约只能表示 1970 到 3001，Unix 纪元会失败。
    """
    current = datetime.now().astimezone()
    found = current.tzinfo
    key = getattr(found, "key", None)
    if isinstance(key, str):
        return ZoneInfo(key)
    if found is None:
        return timezone.utc
    return found


def _parse_seconds(value: str, unit: str) -> Decimal:
    text = value.strip()
    if not text:
        raise ConvertError("请输入时间戳")
    if len(text) > 40:
        raise ConvertError("时间戳超出可表示范围")
    number = _decimal(text)
    if abs(number) >= Decimal("1e20"):
        raise ConvertError("时间戳超出可表示范围")
    if _resolve_unit(number, unit) == "second":
        return number
    return number / Decimal(1000)


def _decimal(text: str) -> Decimal:
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ConvertError("时间戳需要是数字") from None
    if not number.is_finite():
        raise ConvertError("时间戳需要是数字")
    return number


def _resolve_unit(number: Decimal, unit: str) -> str:
    if unit == "auto":
        digits = len(format(abs(number).to_integral_value(ROUND_DOWN), "f"))
        return "second" if digits <= 10 else "millisecond"
    if unit in ("second", "millisecond"):
        return unit
    raise ConvertError("单位只能是自动、秒或毫秒")


def _to_microseconds(seconds: Decimal) -> int:
    try:
        return int((seconds * Decimal(1_000_000)).to_integral_value(ROUND_HALF_UP))
    except (OverflowError, ValueError):
        raise ConvertError("时间戳超出可表示范围") from None


def _split_zone(text: str) -> tuple[timezone | None, str]:
    match = _TZ_SUFFIX.search(text)
    if match is None:
        return None, text
    if match.group("z"):
        found = timezone.utc
    elif match.group("sign"):
        found = _fixed_zone(match.group("sign"), int(match.group("hh")), int(match.group("mm")))
    else:
        found = _fixed_zone(match.group("sign4"), int(match.group("hh4")), int(match.group("mm4")))
    return found, text[: match.start()].strip()


def _fixed_zone(sign: str, hours: int, minutes: int) -> timezone:
    if hours > 23 or minutes > 59:
        raise ConvertError("时区偏移无效")
    offset = timedelta(hours=hours, minutes=minutes)
    if sign == "-":
        offset = -offset
    return timezone(offset)


def _parse_naive(body: str) -> datetime:
    normalized = re.sub(r"\s+", " ", body.replace("T", " ").replace("t", " ")).strip()
    if not normalized:
        raise ConvertError("无法解析这个日期时间")
    for fmt in _NAIVE_FORMATS:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise ConvertError("无法解析这个日期时间")


def _format_timestamps(moment: datetime) -> tuple[str, str]:
    delta = moment.astimezone(timezone.utc) - _EPOCH
    total_us = delta.days * 86_400 * 1_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    sign = "-" if total_us < 0 else ""
    magnitude = abs(total_us)
    seconds, rem_us = divmod(magnitude, 1_000_000)
    if rem_us == 0:
        seconds_text = f"{sign}{seconds}"
    else:
        seconds_text = f"{sign}{seconds}.{f'{rem_us:06d}'.rstrip('0')}"
    millis = (magnitude + 500) // 1000
    return seconds_text, f"{sign}{millis}"


def _format_wall(moment: datetime) -> str:
    text = moment.strftime("%Y-%m-%d %H:%M:%S")
    if moment.microsecond == 0:
        return text
    if moment.microsecond % 1000 == 0:
        return f"{text}.{moment.microsecond // 1000:03d}"
    return f"{text}.{moment.microsecond:06d}"


def _format_iso(moment: datetime) -> str:
    if moment.microsecond == 0:
        spec = "seconds"
    elif moment.microsecond % 1000 == 0:
        spec = "milliseconds"
    else:
        spec = "microseconds"
    return moment.isoformat(timespec=spec)
