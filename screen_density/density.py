"""通过 adb 读取 Android 屏幕密度，并换算资源目录档位。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
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
    android_version: str


@dataclass(frozen=True)
class ProbeResult:
    devices: tuple[Device, ...]
    screen: ScreenDensity | None
    message: str
    error: bool


@dataclass(frozen=True)
class Recording:
    """正在进行的录屏。remote 是手机上的临时文件。"""

    serial: str
    remote: str
    process: subprocess.Popen[bytes]
    errors: bytearray
    reader: threading.Thread


@dataclass(frozen=True)
class RemoteEntry:
    """手机目录中的一项。path 是绝对路径。"""

    name: str
    path: str
    is_dir: bool


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
_INSTALL_TIMEOUT = "安装超时"
_PUSH_TIMEOUT = "复制超时"
_REMOTE_DIR = "/sdcard"
_FAILURE = re.compile(r"Failure \[[^\]]+\]")
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SAVE_DIR = Path(r"D:\adb")
_PNG_MAGIC = b"\x89PNG"
_RECORD_LIMIT_SECONDS = "180"
_SCREENSHOT_TIMEOUT = "截屏超时"
_SAVE_TIMEOUT = "保存超时"
_RECORD_TIMEOUT = "停止录屏超时"
_LIST_TIMEOUT = "读取目录超时"


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
    release: str = "",
    sdk: str = "",
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
        android_version=_android_version(release, sdk),
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


def install_apk(serial: str, path: str, *, timeout: float = 300) -> None:
    """把 apk 覆盖安装到指定手机。已安装时用 -r 替换。"""
    apk = Path(path)
    if not apk.is_file() or apk.suffix.lower() != ".apk":
        raise DensityError("请拖入 apk 文件")
    adb = find_adb()
    stdout, stderr, code = _execute(
        adb,
        ["-s", serial, "install", "-r", str(apk)],
        timeout,
        _INSTALL_TIMEOUT,
    )
    text = _normalize(f"{stdout}\n{stderr}")
    if code == 0 and re.search(r"\bSuccess\b", text):
        return
    raise DensityError(install_failure_message(text))


def install_failure_message(text: str) -> str:
    match = _FAILURE.search(text)
    if match:
        return f"安装失败：{match.group(0)}"
    lines = [
        line.strip()
        for line in _normalize(text).splitlines()
        if line.strip() and "Performing Streamed Install" not in line
    ]
    if not lines:
        return "安装失败"
    return f"安装失败：{lines[-1]}"


def push_file(serial: str, path: str, *, timeout: float = 300) -> None:
    """把文件复制到手机的 /sdcard，保留原文件名。同名文件会被覆盖。"""
    source = Path(path)
    name = source.name
    if not source.is_file() or not name or name in {".", ".."}:
        raise DensityError("请拖入文件")
    adb = find_adb()
    stdout, stderr, code = _execute(
        adb,
        ["-s", serial, "push", str(source), f"{_REMOTE_DIR}/{name}"],
        timeout,
        _PUSH_TIMEOUT,
    )
    if code == 0:
        return
    raise DensityError(push_failure_message(f"{stdout}\n{stderr}"))


def push_failure_message(text: str) -> str:
    lines = [line.strip() for line in _normalize(text).splitlines() if line.strip()]
    if not lines:
        return "复制失败"
    return f"复制失败：{lines[-1]}"


def list_remote(serial: str, path: str, *, timeout: float = 15) -> tuple[RemoteEntry, ...]:
    """列出手机目录。名称以 / 结尾的是文件夹。"""
    remote = _clean_remote(path)
    adb = find_adb()
    stdout, stderr, code = _execute(
        adb,
        ["-s", serial, "shell", f"ls -1Ap {_shell_quote(remote)}"],
        timeout,
        _LIST_TIMEOUT,
    )
    if code != 0:
        raise DensityError(_list_failure(stderr.strip() or stdout.strip()))
    return tuple(_parse_remote_listing(remote, stdout))


def pull_remote_file(serial: str, remote: str, *, timeout: float = 300) -> Path:
    """把手机上的文件保存到 D:\\adb，保留原文件名。重名时追加 _2、_3。"""
    name = _windows_filename(_remote_basename(remote))
    if not name or name in {".", ".."}:
        raise DensityError("无法保存该文件")
    adb = find_adb()
    local = _reserve_named(name)
    _pull_file(adb, serial, remote, local, timeout, allow_empty=True)
    return local


def capture_screenshot(serial: str, *, timeout: float = 30) -> Path:
    """截取当前画面并保存为 PNG，返回本机路径。"""
    adb = find_adb()
    data, stderr, _code = _execute_binary(
        adb,
        ["-s", serial, "exec-out", "screencap", "-p"],
        timeout,
        _SCREENSHOT_TIMEOUT,
    )
    if data.startswith(_PNG_MAGIC):
        return _write_bytes(_reserve_path("截屏", ".png"), data)
    return _screenshot_via_pull(adb, serial, timeout, stderr)


def start_recording(serial: str) -> Recording:
    """开始录屏。最长 3 分钟，到时手机上的 screenrecord 会自己退出。"""
    adb = find_adb()
    remote = _remote_temp(".mp4")
    process = _popen(
        adb,
        ["-s", serial, "shell", "screenrecord", "--time-limit", _RECORD_LIMIT_SECONDS, remote],
    )
    errors = bytearray()
    reader = threading.Thread(target=_read_stream, args=(process.stderr, errors), daemon=True)
    reader.start()
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        return Recording(serial=serial, remote=remote, process=process, errors=errors, reader=reader)
    reader.join(timeout=1)
    raise DensityError(_capture_failure(errors.decode("utf-8", errors="replace"), "录屏失败"))


def stop_recording(recording: Recording, *, timeout: float = 30) -> Path:
    """结束录屏，把视频拉到本机后删除手机上的临时文件。进程已退出时直接拉取。"""
    adb = find_adb()
    try:
        _stop_record_process(recording, adb, timeout)
        recording.reader.join(timeout=1)
        local = _reserve_path("录屏", ".mp4")
        _pull_file(adb, recording.serial, recording.remote, local, timeout)
        if _is_mp4(local):
            return local
        local.unlink(missing_ok=True)
        detail = recording.errors.decode("utf-8", errors="replace")
        raise DensityError(_capture_failure(detail, "录屏失败"))
    finally:
        _remove_remote(adb, recording.serial, recording.remote)


def _stop_record_process(recording: Recording, adb: str, timeout: float) -> None:
    process = recording.process
    if process.poll() is not None:
        return
    try:
        _execute(
            adb,
            ["-s", recording.serial, "shell", "kill -2 $(pidof screenrecord)"],
            10,
            _RECORD_TIMEOUT,
        )
    except DensityError:
        pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise DensityError(_RECORD_TIMEOUT) from None


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
        f"getprop ro.product.model; echo {_SPLIT}; "
        f"getprop ro.build.version.release; echo {_SPLIT}; "
        "getprop ro.build.version.sdk"
    )
    parts = _run(adb, ["-s", serial, "shell", command], timeout).split(_SPLIT)
    if len(parts) < 6:
        raise DensityError("无法读取屏幕密度")
    return screen_from_outputs(serial, parts[0], parts[1], parts[2], parts[3], parts[4], parts[5])


def _run(adb: str, args: list[str], timeout: float) -> str:
    stdout, stderr, code = _execute(adb, args, timeout, _TIMEOUT)
    if code != 0 and not stdout.strip():
        raise DensityError(_clean_adb_error(stderr.strip()))
    return stdout


def _execute(adb: str, args: list[str], timeout: float, timeout_message: str) -> tuple[str, str, int]:
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
        raise DensityError(timeout_message) from None
    except OSError:
        raise DensityError("无法启动 adb") from None
    stdout = _normalize(completed.stdout.decode("utf-8", errors="replace"))
    stderr = _normalize(completed.stderr.decode("utf-8", errors="replace"))
    return stdout, stderr, completed.returncode


def _execute_binary(adb: str, args: list[str], timeout: float, timeout_message: str) -> tuple[bytes, str, int]:
    """执行 adb 并保留原始标准输出，供截屏这类二进制结果使用。"""
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
        raise DensityError(timeout_message) from None
    except OSError:
        raise DensityError("无法启动 adb") from None
    stderr = _normalize(completed.stderr.decode("utf-8", errors="replace"))
    return completed.stdout, stderr, completed.returncode


def _popen(adb: str, args: list[str]) -> subprocess.Popen[bytes]:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        return subprocess.Popen(
            [adb, *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            **kwargs,
        )
    except OSError:
        raise DensityError("无法启动 adb") from None


def _read_stream(stream, sink: bytearray) -> None:
    if stream is None:
        return
    sink.extend(stream.read())


def _screenshot_via_pull(adb: str, serial: str, timeout: float, prior_error: str) -> Path:
    remote = _remote_temp(".png")
    try:
        stdout, stderr, code = _execute(
            adb,
            ["-s", serial, "shell", "screencap", "-p", remote],
            timeout,
            _SCREENSHOT_TIMEOUT,
        )
        if code != 0:
            detail = stderr or stdout or prior_error
            raise DensityError(_capture_failure(detail, "截屏失败"))
        local = _reserve_path("截屏", ".png")
        _pull_file(adb, serial, remote, local, timeout)
        if not _is_png_file(local):
            local.unlink(missing_ok=True)
            raise DensityError(_capture_failure(prior_error, "截屏失败"))
        return local
    finally:
        _remove_remote(adb, serial, remote)


def _pull_file(adb: str, serial: str, remote: str, local: Path, timeout: float, *, allow_empty: bool = False) -> None:
    stdout, stderr, code = _execute(
        adb,
        ["-s", serial, "pull", remote, str(local)],
        timeout,
        _SAVE_TIMEOUT,
    )
    if code == 0 and local.is_file() and (allow_empty or local.stat().st_size > 0):
        return
    local.unlink(missing_ok=True)
    raise DensityError(_pull_failure_message(f"{stdout}\n{stderr}"))


def _pull_failure_message(text: str) -> str:
    lines = [line.strip() for line in _normalize(text).splitlines() if line.strip()]
    if not lines:
        return "保存失败"
    return f"保存失败：{lines[-1]}"


def _remove_remote(adb: str, serial: str, remote: str) -> None:
    try:
        _execute(adb, ["-s", serial, "shell", "rm", "-f", remote], 10, "保存失败")
    except DensityError:
        return


def _remote_temp(suffix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return f"{_REMOTE_DIR}/python-desktop-tools-{stamp}{suffix}"


def _clean_remote(path: str) -> str:
    text = path.strip().replace("\\", "/")
    if not text.startswith("/"):
        text = f"/{text}"
    while "//" in text:
        text = text.replace("//", "/")
    if len(text) > 1:
        text = text.rstrip("/")
    return text or "/"


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _parse_remote_listing(directory: str, text: str) -> list[RemoteEntry]:
    entries: list[RemoteEntry] = []
    for raw in _normalize(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        is_dir = line.endswith("/")
        name = line[:-1] if is_dir else line
        if not name or name in {".", ".."}:
            continue
        entries.append(RemoteEntry(name=name, path=_join_remote(directory, name), is_dir=is_dir))
    entries.sort(key=lambda item: (not item.is_dir, item.name.casefold()))
    return entries


def _join_remote(directory: str, name: str) -> str:
    if directory == "/":
        return f"/{name}"
    return f"{directory}/{name}"


def _remote_basename(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def _windows_filename(name: str) -> str:
    return _INVALID_FILENAME.sub("_", name).rstrip(" .")


def _reserve_named(filename: str) -> Path:
    """占一个不重名的本地文件。已有同名文件时追加 _2、_3。"""
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    stem, suffix = _split_filename(filename)
    names = [f"{stem}{suffix}"]
    names.extend(f"{stem}_{index}{suffix}" for index in range(2, 100))
    for name in names:
        path = SAVE_DIR / name
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        os.close(fd)
        return path
    raise DensityError("无法保存文件")


def _split_filename(filename: str) -> tuple[str, str]:
    if filename.startswith(".") and filename.count(".") == 1:
        return filename, ""
    dot = filename.rfind(".")
    if dot <= 0:
        return filename, ""
    return filename[:dot], filename[dot:]


def _list_failure(text: str) -> str:
    cleaned = _clean_adb_error(text.strip())
    if cleaned in {_UNAUTHORIZED, _OFFLINE, _NO_DEVICE, "没有 USB 调试权限"}:
        return cleaned
    if not text.strip() or cleaned == "adb 执行失败":
        return "读取目录失败"
    return f"读取目录失败：{cleaned.splitlines()[-1]}"


def _reserve_path(kind: str, suffix: str) -> Path:
    """占一个不重名的本地文件。同一秒已有文件时追加 _2、_3。"""
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    names = [f"{kind}_{stamp}{suffix}"]
    names.extend(f"{kind}_{stamp}_{index}{suffix}" for index in range(2, 100))
    for name in names:
        path = SAVE_DIR / name
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        os.close(fd)
        return path
    raise DensityError("无法保存文件")


def _write_bytes(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def _is_png_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(_PNG_MAGIC)).startswith(_PNG_MAGIC)
    except OSError:
        return False


def _is_mp4(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"ftyp" in handle.read(32)
    except OSError:
        return False


def _capture_failure(text: str, fallback: str) -> str:
    cleaned = _clean_adb_error(text.strip())
    if cleaned in {_UNAUTHORIZED, _OFFLINE, _NO_DEVICE, "没有 USB 调试权限"}:
        return cleaned
    if not text.strip() or cleaned == "adb 执行失败":
        return fallback
    return f"{fallback}：{cleaned.splitlines()[0]}"


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


def _android_version(release: str, sdk: str) -> str:
    name = " ".join(release.split())
    level = " ".join(sdk.split())
    if name and level:
        return f"{name}（API {level}）"
    if name:
        return name
    if level:
        return f"API {level}"
    return ""


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
