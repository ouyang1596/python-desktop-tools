"""通过 adb 读取 Android 屏幕密度，并换算资源目录档位。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


class DensityError(Exception):
    """查询失败时的说明。"""


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    model: str = ""


@dataclass(frozen=True)
class DisplaySize:
    width: int
    height: int


@dataclass(frozen=True)
class ScreenDensity:
    serial: str
    model: str
    physical_dpi: int
    current_dpi: int
    overridden: bool
    qualifier: str
    short: str
    scale: str
    size_text: str
    dp_text: str


@dataclass(frozen=True)
class ProbeResult:
    devices: tuple[Device, ...]
    screen: ScreenDensity | None
    message: str
    error: bool


_BUCKETS = (
    (120, "ldpi", "ldpi"),
    (160, "mdpi", "mdpi"),
    (213, "tvdpi", "tvdpi"),
    (240, "hdpi", "hdpi"),
    (320, "xhdpi", "xh"),
    (480, "xxhdpi", "xxh"),
    (640, "xxxhdpi", "xxxh"),
)
# 分隔符不能以 # 开头，否则设备上的 shell 会把后面整段当成注释。
_SPLIT = "SCREEN_DENSITY_SPLIT"
_DEVICE_LINE = re.compile(r"^(\S+)\s+(no permissions|\S+)")
_MODEL = re.compile(r"\bmodel:(\S+)")
_DISPLAY = re.compile(r"^Display\s+(\d+)\s*:", re.MULTILINE)
_DENSITY = re.compile(r"(Physical|Override) density:\s*(\d+)")
_SIZE = re.compile(r"(Physical|Override) size:\s*(\d+)x(\d+)")
_NO_DEVICE = "没有检测到手机，请打开 USB 调试并连接"
_NO_ADB = "未找到 adb，请安装 Android SDK platform-tools 并加入 PATH"
_UNAUTHORIZED = "手机未授权，请在手机上允许 USB 调试"
_OFFLINE = "设备离线"
_TIMEOUT = "查询超时，请确认手机已解锁并允许 USB 调试"


def bucket_for(dpi: int) -> tuple[str, str]:
    """返回最接近的资源目录名和简称。

    距离相同时选更高档，这样系统缩小图片，而不是把小图放大。
    """
    if dpi <= 0:
        raise DensityError("无法读取屏幕密度")
    _nominal, qualifier, short = min(_BUCKETS, key=lambda item: (abs(item[0] - dpi), -item[0]))
    return qualifier, short


def format_scale(dpi: int) -> str:
    value = (Decimal(dpi) / Decimal(160)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(value, "f").rstrip("0").rstrip(".")


def parse_devices(text: str) -> list[Device]:
    devices: list[Device] = []
    for raw in _normalize(text).splitlines():
        line = raw.strip()
        if not line or line.startswith("*") or line.startswith("List of devices"):
            continue
        match = _DEVICE_LINE.match(line)
        if match is None:
            continue
        model = _MODEL.search(line)
        devices.append(Device(match.group(1), match.group(2), model.group(1) if model else ""))
    return devices


def parse_density(text: str) -> tuple[int, int | None]:
    """返回物理密度，以及覆盖密度；没有覆盖行时第二项为 None。"""
    physical: int | None = None
    override: int | None = None
    for kind, value in _DENSITY.findall(_display_block(_normalize(text), 0)):
        dpi = int(value)
        if dpi <= 0:
            continue
        if kind == "Physical":
            physical = dpi
        else:
            override = dpi
    if physical is None:
        raise DensityError("无法读取屏幕密度")
    return physical, override


def parse_size(text: str) -> tuple[DisplaySize | None, DisplaySize | None]:
    physical: DisplaySize | None = None
    override: DisplaySize | None = None
    for kind, width, height in _SIZE.findall(_display_block(_normalize(text), 0)):
        size = DisplaySize(int(width), int(height))
        if size.width <= 0 or size.height <= 0:
            continue
        if kind == "Physical":
            physical = size
        else:
            override = size
    return physical, override


def screen_from_outputs(
    serial: str,
    density_text: str,
    size_text: str,
    manufacturer: str,
    model: str,
) -> ScreenDensity:
    physical_dpi, override_dpi = parse_density(density_text)
    overridden = override_dpi is not None and override_dpi != physical_dpi
    current_dpi = override_dpi if overridden else physical_dpi
    physical_size, override_size = parse_size(size_text)
    size_overridden = (
        override_size is not None
        and physical_size is not None
        and (override_size.width != physical_size.width or override_size.height != physical_size.height)
    )
    current_size = override_size if size_overridden else physical_size
    if current_size is None:
        current_size = override_size or physical_size
    qualifier, short = bucket_for(current_dpi)
    return ScreenDensity(
        serial=serial,
        model=_model_name(manufacturer, model),
        physical_dpi=physical_dpi,
        current_dpi=current_dpi,
        overridden=overridden,
        qualifier=qualifier,
        short=short,
        scale=format_scale(current_dpi),
        size_text=_size_text(current_size, physical_size if size_overridden else None),
        dp_text=_dp_text(current_size, current_dpi),
    )


def find_adb() -> str:
    found = shutil.which("adb")
    if found:
        return found
    for path in _adb_candidates():
        if path.is_file():
            return str(path)
    raise DensityError(_NO_ADB)


def probe(serial: str | None = None, *, timeout: float = 10) -> ProbeResult:
    """查询已连接手机。serial 为空时使用第一台已授权设备。"""
    try:
        adb = find_adb()
        devices = tuple(parse_devices(_run(adb, ["devices", "-l"], timeout)))
    except DensityError as err:
        return ProbeResult((), None, str(err), True)
    if not devices:
        return ProbeResult((), None, _NO_DEVICE, True)
    chosen = _choose(devices, serial)
    if chosen is None:
        return ProbeResult(devices, None, _missing_message(devices, serial), True)
    if chosen.state != "device":
        return ProbeResult(devices, None, _state_message(chosen.state), True)
    try:
        screen = _read_screen(adb, chosen.serial, timeout)
    except DensityError as err:
        return ProbeResult(devices, None, str(err), True)
    message = "已读取，当前为覆盖密度" if screen.overridden else "已读取"
    return ProbeResult(devices, screen, message, False)


def device_label(device: Device) -> str:
    if device.model:
        name = f"{device.model.replace('_', ' ')} {device.serial}"
    else:
        name = device.serial
    if device.state == "device":
        return name
    return f"{name}（{_state_name(device.state)}）"


def _read_screen(adb: str, serial: str, timeout: float) -> ScreenDensity:
    command = (
        f"wm density; echo {_SPLIT}; "
        f"wm size; echo {_SPLIT}; "
        f"getprop ro.product.manufacturer; echo {_SPLIT}; "
        "getprop ro.product.model"
    )
    parts = _run(adb, ["-s", serial, "shell", command], timeout).split(_SPLIT)
    if len(parts) < 4:
        raise DensityError("无法读取屏幕密度")
    return screen_from_outputs(serial, parts[0], parts[1], parts[2], parts[3])


def _run(adb: str, args: list[str], timeout: float) -> str:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            [adb, *args],
            capture_output=True,
            timeout=timeout,
            check=False,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        raise DensityError(_TIMEOUT) from None
    except OSError:
        raise DensityError("无法启动 adb") from None
    stdout = _normalize(completed.stdout.decode("utf-8", errors="replace"))
    if completed.returncode != 0 and not stdout.strip():
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise DensityError(_clean_adb_error(stderr))
    return stdout


def _adb_candidates() -> list[Path]:
    names = ("adb.exe", "adb")
    roots: list[Path] = []
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Android" / "Sdk")
    return [root / "platform-tools" / name for root in roots for name in names]


def _choose(devices: tuple[Device, ...], serial: str | None) -> Device | None:
    if serial:
        return next((device for device in devices if device.serial == serial), None)
    return next((device for device in devices if device.state == "device"), None)


def _missing_message(devices: tuple[Device, ...], serial: str | None) -> str:
    if serial and all(device.serial != serial for device in devices):
        return "设备已断开，请重新连接"
    if any(device.state == "unauthorized" for device in devices):
        return _UNAUTHORIZED
    if any(device.state == "offline" for device in devices):
        return _OFFLINE
    return "没有可用的手机"


def _state_message(state: str) -> str:
    if state == "unauthorized":
        return _UNAUTHORIZED
    if state == "offline":
        return _OFFLINE
    if state == "no permissions":
        return "没有 USB 调试权限"
    return f"设备不可用：{state}"


def _state_name(state: str) -> str:
    if state == "unauthorized":
        return "未授权"
    if state == "offline":
        return "离线"
    if state == "no permissions":
        return "无权限"
    return state


def _clean_adb_error(stderr: str) -> str:
    lower = stderr.lower()
    if "unauthorized" in lower:
        return _UNAUTHORIZED
    if "offline" in lower:
        return _OFFLINE
    if "no devices" in lower or "no emulators" in lower:
        return _NO_DEVICE
    if not stderr:
        return "adb 执行失败"
    return stderr.splitlines()[0]


def _display_block(text: str, display_id: int) -> str:
    matches = list(_DISPLAY.finditer(text))
    if not matches:
        return text
    for index, match in enumerate(matches):
        if int(match.group(1)) != display_id:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[match.end() : end]
    end = matches[1].start() if len(matches) > 1 else len(text)
    return text[matches[0].end() : end]


def _model_name(manufacturer: str, model: str) -> str:
    brand = " ".join(manufacturer.split())
    name = " ".join(model.split())
    if not brand and not name:
        return "未知设备"
    if not brand:
        return name
    if not name:
        return brand
    if name.lower().startswith(brand.lower()):
        return name
    return f"{brand} {name}"


def _size_text(current: DisplaySize | None, physical: DisplaySize | None) -> str:
    if current is None:
        return ""
    text = f"{current.width}×{current.height}"
    if physical is not None:
        text += f"（物理 {physical.width}×{physical.height}）"
    return text


def _dp_text(size: DisplaySize | None, dpi: int) -> str:
    if size is None or dpi <= 0:
        return ""
    width = _px_to_dp(size.width, dpi)
    height = _px_to_dp(size.height, dpi)
    return f"{width}×{height} dp"


def _px_to_dp(px: int, dpi: int) -> int:
    return int((Decimal(px) * Decimal(160) / Decimal(dpi)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
