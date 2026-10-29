"""通过 adb 读取 Android 屏幕密度，并换算资源目录档位。"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import BinaryIO

import adbutils
from adbutils import AdbClient, AdbConnection
from adbutils.errors import AdbError, AdbInstallError, AdbSyncError, AdbTimeout


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
    process: _AdbProcess
    errors: bytearray
    reader: threading.Thread


@dataclass(frozen=True)
class LogCapture:
    """正在进行的 logcat 采集。path 是本机文本。"""

    serial: str
    path: Path
    process: _AdbProcess
    errors: bytearray
    reader: threading.Thread
    err_reader: threading.Thread
    handle: BinaryIO
    lock: threading.Lock
    lines: list[int]
    package: str


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
_LIST_FAIL = "SCREEN_DENSITY_LIST_FAIL"
_INSTALL_REMOTE = "/data/local/tmp/python-desktop-tools-install.apk"
_resolved_adb: str | None = None
_UNAUTHORIZED = "手机未授权，请在手机上允许 USB 调试"
_OFFLINE = "设备离线"
_TIMEOUT = "查询超时，请确认手机已解锁并允许 USB 调试"
_INSTALL_TIMEOUT = "安装超时"
_PUSH_TIMEOUT = "复制超时"
_REMOTE_DIR = "/sdcard"
_FAILURE = re.compile(r"Failure \[[^\]]+\]")
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SAVE_DIR = Path(r"D:\adb")
SCRCPY_EXE = Path(r"D:\soft\scrcpy-win64-v5.0.1\scrcpy.exe")
_PNG_MAGIC = b"\x89PNG"
_RECORD_LIMIT_SECONDS = "180"
_SCREENSHOT_TIMEOUT = "截屏超时"
_SAVE_TIMEOUT = "保存超时"
_RECORD_TIMEOUT = "停止录屏超时"
_LOG_TIMEOUT = "停止抓取日志超时"
_LOG_CLEAR_TIMEOUT = "清空日志缓冲超时"
_APP_TIMEOUT = "读取当前应用超时"
_LIST_TIMEOUT = "读取目录超时"
_DIRECT_ADB_MESSAGES = frozenset({
    _UNAUTHORIZED,
    _OFFLINE,
    _NO_DEVICE,
    "没有 USB 调试权限",
    "无法启动 adb",
    _NO_ADB,
})
_LOG_LEVELS = frozenset({"V", "D", "I", "W", "E", "F"})
_LOG_BOM = b"\xef\xbb\xbf"
_LOG_EMPTY = "没有匹配到日志"
_LOG_FLUSH_SECONDS = 0.2
_LOG_FLUSH_BYTES = 256 * 1024
_LOG_FORMAT = "threadtime,usec"
_LOG_FORMAT_PLAIN = "threadtime"
_LOG_BUFFERS = ("main", "system", "crash")
_LOG_ARG_ERROR = re.compile(
    r"invalid format|unknown buffer|unrecognized option|unknown option|invalid option|unknown command|usage: logcat",
    re.IGNORECASE,
)
_LOG_LINE = re.compile(br"\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_UID_LINE = re.compile(r"^Uid:\s*(\d+)")
_FOCUS_QUERY = "dumpsys window | grep -e mCurrentFocus= -e mFocusedApp="
_PKG_BEFORE_SLASH = re.compile(r"([a-zA-Z][\w]*(?:\.[a-zA-Z_][\w]*)+)/")


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
    """本机 adb。先按 PATH 和 SDK 查找，没有时用 adbutils 自带的 adb。"""
    global _resolved_adb
    if _resolved_adb:
        return _resolved_adb
    if not os.environ.get("ADBUTILS_ADB_PATH"):
        found = shutil.which("adb")
        if not found:
            for path in _adb_candidates():
                if path.is_file():
                    found = str(path)
                    break
        if found:
            os.environ["ADBUTILS_ADB_PATH"] = found
    try:
        resolved = adbutils.adb_path()
    except AdbError:
        raise DensityError(_NO_ADB) from None
    os.environ["ADBUTILS_ADB_PATH"] = resolved
    _resolved_adb = resolved
    return resolved


def start_scrcpy(serial: str) -> subprocess.Popen[bytes]:
    """启动 scrcpy 镜像指定手机。同一设备是否已在运行由调用方判断。"""
    if not SCRCPY_EXE.is_file():
        raise DensityError(f"未找到 scrcpy：{SCRCPY_EXE}")
    env = os.environ.copy()
    env["ADB"] = find_adb()
    try:
        return subprocess.Popen(
            [str(SCRCPY_EXE), "-s", serial],
            cwd=str(SCRCPY_EXE.parent),
            env=env,
        )
    except OSError:
        raise DensityError("无法启动 scrcpy") from None


def probe(serial: str | None = None, *, timeout: float = 10) -> ProbeResult:
    """查询已连接手机。serial 为空时使用第一台已授权设备。"""
    try:
        devices = tuple(_list_devices(timeout))
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
        screen = _read_screen(chosen.serial, timeout)
    except DensityError as err:
        return ProbeResult(devices, None, str(err), True)
    message = "已读取，当前为覆盖密度" if screen.overridden else "已读取"
    return ProbeResult(devices, screen, message, False)


def install_apk(serial: str, path: str, *, timeout: float = 300) -> None:
    """把 apk 覆盖安装到指定手机。已安装时用 -r 替换。"""
    apk = Path(path)
    if not apk.is_file() or apk.suffix.lower() != ".apk":
        raise DensityError("请拖入 apk 文件")
    device = _device(serial, timeout)
    try:
        device.sync.push(apk, _INSTALL_REMOTE)
        device.install_remote(_INSTALL_REMOTE, clean=True, flags=["-r"])
    except AdbInstallError as err:
        _remove_remote(serial, _INSTALL_REMOTE)
        raise DensityError(install_failure_message(err.output)) from None
    except (AdbError, AdbSyncError, OSError, socket.timeout, TimeoutError) as err:
        _remove_remote(serial, _INSTALL_REMOTE)
        raise _transfer_error(err, _INSTALL_TIMEOUT, install_failure_message) from None


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
    device = _device(serial, timeout)
    try:
        device.sync.push(source, f"{_REMOTE_DIR}/{name}")
    except (AdbError, AdbSyncError, OSError, socket.timeout, TimeoutError) as err:
        raise _transfer_error(err, _PUSH_TIMEOUT, push_failure_message) from None


def push_failure_message(text: str) -> str:
    lines = [line.strip() for line in _normalize(text).splitlines() if line.strip()]
    if not lines:
        return "复制失败"
    return f"复制失败：{lines[-1]}"


def list_remote(serial: str, path: str, *, timeout: float = 15) -> tuple[RemoteEntry, ...]:
    """列出手机目录。目录（含指向目录的符号链接）以 / 结尾。"""
    remote = _clean_remote(path)
    result = _shell(serial, _list_shell(remote), timeout, _LIST_TIMEOUT)
    failed = _listing_error(result)
    if failed:
        raise DensityError(failed)
    return tuple(_parse_remote_listing(remote, result.stdout))


def pull_remote_file(serial: str, remote: str, *, timeout: float = 300) -> Path:
    """把手机上的文件保存到 D:\\adb，保留原文件名。重名时追加 _2、_3。"""
    name = _windows_filename(_remote_basename(remote))
    if not name or name in {".", ".."}:
        raise DensityError("无法保存该文件")
    local = _reserve_named(name)
    _pull_file(serial, remote, local, timeout, allow_empty=True)
    return local


def pull_remote_into(serial: str, remote: str, dest: Path, *, timeout: float = 120) -> None:
    """把手机文件拉到 dest。不写入 D:\\adb，调用方用完后自行删除。"""
    cleaned = _clean_remote(remote)
    name = _remote_basename(cleaned)
    if not name or name in {".", ".."}:
        raise DensityError("无法读取该文件")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _pull_file(serial, cleaned, dest, timeout)


def capture_screenshot(serial: str, *, timeout: float = 30) -> Path:
    """截取当前画面并保存为 PNG，返回本机路径。"""
    data = _shell_bytes(serial, ["screencap", "-p"], timeout, _SCREENSHOT_TIMEOUT)
    if data.startswith(_PNG_MAGIC):
        return _write_bytes(_reserve_path("截屏", ".png"), data)
    detail = data.decode("utf-8", errors="replace")
    return _screenshot_via_pull(serial, timeout, detail)


def start_recording(serial: str) -> Recording:
    """开始录屏。最长 3 分钟，到时手机上的 screenrecord 会自己退出。"""
    remote = _remote_temp(".mp4")
    process = _open_stream(
        serial,
        ["screenrecord", "--time-limit", _RECORD_LIMIT_SECONDS, remote],
    )
    errors = bytearray()
    reader = threading.Thread(target=_read_process_output, args=(process, errors), daemon=True)
    reader.start()
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        return Recording(serial=serial, remote=remote, process=process, errors=errors, reader=reader)
    reader.join(timeout=1)
    raise DensityError(_capture_failure(errors.decode("utf-8", errors="replace"), "录屏失败"))


def stop_recording(recording: Recording, *, timeout: float = 30) -> Path:
    """结束录屏，把视频拉到本机后删除手机上的临时文件。进程已退出时直接拉取。"""
    try:
        _stop_record_process(recording, timeout)
        recording.reader.join(timeout=1)
        local = _reserve_path("录屏", ".mp4")
        _pull_file(recording.serial, recording.remote, local, timeout)
        if _is_mp4(local):
            return local
        local.unlink(missing_ok=True)
        detail = recording.errors.decode("utf-8", errors="replace")
        raise DensityError(_capture_failure(detail, "录屏失败"))
    finally:
        _remove_remote(recording.serial, recording.remote)


def start_logcat(
    serial: str,
    *,
    keyword: str = "",
    level: str = "V",
    app_only: bool = False,
) -> LogCapture:
    """开始把 logcat 写入本机文本。等级为该级及以上；关键字在本机按整行做「或」过滤，多段用 | 分隔。开始前会清空手机上的日志缓冲。"""
    chosen = level.strip().upper()
    if chosen not in _LOG_LEVELS:
        raise DensityError("无法识别的日志等级")
    package = ""
    pids: list[str] = []
    uids: list[str] = []
    if app_only:
        package = _foreground_package(serial)
        pids = _app_pids(serial, package)
        if not pids:
            raise DensityError(f"当前应用没有在运行：{package}")
        uids = _app_uids(serial, pids)
    path = _reserve_path("日志", ".txt")
    try:
        handle = path.open("w+b", buffering=_LOG_FLUSH_BYTES)
    except OSError:
        path.unlink(missing_ok=True)
        raise DensityError("无法保存文件") from None
    lines = [0]
    lock = threading.Lock()
    attempts = _logcat_attempts(chosen, uids, pids)
    last_errors = bytearray()
    try:
        handle.write(_LOG_BOM)
        handle.flush()
        _clear_logcat(serial)
        for index, args in enumerate(attempts):
            if index:
                _rewind_log_handle(handle, lines, lock)
            capture = _spawn_logcat(args, serial, path, handle, lines, lock, keyword, package)
            try:
                capture.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                return capture
            if not _join_log_readers(capture):
                if _written_log_looks_real(handle):
                    return capture
                break
            last_errors = capture.errors
            if index + 1 < len(attempts) and _should_retry_logcat(capture):
                continue
            if _written_log_looks_real(handle):
                return capture
            break
    except DensityError:
        _discard_log_file(handle, path)
        raise
    except OSError:
        _discard_log_file(handle, path)
        raise DensityError("无法保存文件") from None
    _discard_log_file(handle, path)
    raise DensityError(_capture_failure(last_errors.decode("utf-8", errors="replace"), "抓取日志失败"))


def stop_logcat(capture: LogCapture, *, timeout: float = 5) -> Path:
    """结束 logcat，返回已写入的本机文本。没有日志时视为失败。"""
    process = capture.process
    died_on_its_own = process.poll() is not None
    try:
        if not died_on_its_own:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    raise DensityError(_LOG_TIMEOUT) from None
        stdout = process.stdout
        if stdout is not None:
            try:
                stdout.close()
            except OSError:
                pass
        capture.reader.join(timeout=3)
        capture.err_reader.join(timeout=1)
    finally:
        _close_log_handle(capture)
    with capture.lock:
        count = capture.lines[0]
    if count == 0:
        detail = capture.errors.decode("utf-8", errors="replace")
        capture.path.unlink(missing_ok=True)
        if died_on_its_own:
            raise DensityError(_capture_failure(detail, "抓取日志失败"))
        raise DensityError(_LOG_EMPTY)
    return capture.path


def _logcat_attempts(
    level: str,
    uids: list[str],
    pids: list[str],
) -> list[list[str]]:
    preferred = _logcat_command(
        level,
        uids=uids,
        pids=pids,
        log_format=_LOG_FORMAT,
        buffers=_LOG_BUFFERS,
    )
    fallback = _logcat_command(
        level,
        uids=[],
        pids=pids,
        log_format=_LOG_FORMAT_PLAIN,
        buffers=(),
    )
    if fallback == preferred:
        return [preferred]
    return [preferred, fallback]


def _logcat_command(
    level: str,
    *,
    uids: list[str],
    pids: list[str],
    log_format: str,
    buffers: tuple[str, ...],
) -> list[str]:
    args = ["logcat", "-v", log_format]
    for name in buffers:
        args.extend(["-b", name])
    if uids:
        args.extend(f"--uid={uid}" for uid in uids)
    else:
        args.extend(f"--pid={pid}" for pid in pids)
    args.append(f"*:{level}")
    return args


def _spawn_logcat(
    args: list[str],
    serial: str,
    path: Path,
    handle: BinaryIO,
    lines: list[int],
    lock: threading.Lock,
    keyword: str,
    package: str,
) -> LogCapture:
    errors = bytearray()
    process = _open_stream(serial, args)
    err_reader = threading.Thread(target=_read_stream, args=(process.stderr, errors), daemon=True)
    reader = threading.Thread(
        target=_read_logcat,
        args=(process.stdout, handle, lock, lines, keyword, process, errors),
        daemon=True,
    )
    err_reader.start()
    reader.start()
    return LogCapture(
        serial=serial,
        path=path,
        process=process,
        errors=errors,
        reader=reader,
        err_reader=err_reader,
        handle=handle,
        lock=lock,
        lines=lines,
        package=package,
    )


def _join_log_readers(capture: LogCapture) -> bool:
    capture.reader.join(timeout=3)
    capture.err_reader.join(timeout=1)
    return not capture.reader.is_alive()


def _should_retry_logcat(capture: LogCapture) -> bool:
    detail = capture.errors.decode("utf-8", errors="replace")
    cleaned = _clean_adb_error(detail.strip())
    if cleaned in {_UNAUTHORIZED, _OFFLINE, _NO_DEVICE, "没有 USB 调试权限"}:
        return False
    if _LOG_ARG_ERROR.search(detail):
        return True
    with capture.lock:
        count = capture.lines[0]
    if count == 0:
        return True
    return not _written_log_looks_real(capture.handle)


def _written_log_looks_real(handle: BinaryIO) -> bool:
    handle.flush()
    pos = handle.tell()
    try:
        handle.seek(len(_LOG_BOM))
        sample = handle.read(4096)
    finally:
        handle.seek(pos)
    return _LOG_LINE.search(sample) is not None


def _rewind_log_handle(handle: BinaryIO, lines: list[int], lock: threading.Lock) -> None:
    with lock:
        handle.flush()
        handle.seek(len(_LOG_BOM))
        handle.truncate()
        lines[0] = 0


def _discard_log_file(handle: BinaryIO, path: Path) -> None:
    try:
        if not handle.closed:
            handle.close()
    except OSError:
        pass
    path.unlink(missing_ok=True)


def _foreground_package(serial: str) -> str:
    result = _shell(serial, _FOCUS_QUERY, 15, _APP_TIMEOUT)
    package = _parse_foreground_package(result.stdout)
    if package:
        return package
    combined = f"{result.stderr}\n{result.stdout}"
    if "not found" in combined and "grep" in combined:
        result = _shell(serial, "dumpsys window", 15, _APP_TIMEOUT)
        package = _parse_foreground_package(result.stdout)
        if package:
            return package
    if result.code != 0 and not result.stdout.strip():
        detail = result.stderr or result.stdout
        raise DensityError(_capture_failure(detail, "无法识别当前应用"))
    raise DensityError("无法识别当前应用")


def _parse_foreground_package(text: str) -> str | None:
    """从 dumpsys window 里取前台包名。mCurrentFocus 为空时用 mFocusedApp。"""
    return _labeled_package(text, "mCurrentFocus=") or _labeled_package(text, "mFocusedApp=")


def _labeled_package(text: str, label: str) -> str | None:
    for line in text.splitlines():
        if label not in line:
            continue
        match = _PKG_BEFORE_SLASH.search(line)
        if match is not None:
            return match.group(1)
    return None


def _app_pids(serial: str, package: str) -> list[str]:
    result = _shell(serial, ["pidof", package], 10, _APP_TIMEOUT)
    pids = re.findall(r"\d+", result.stdout)
    if pids:
        return pids
    if result.code != 0 and result.stderr.strip():
        raise DensityError(_capture_failure(result.stderr, "无法识别当前应用"))
    return []


def _app_uids(serial: str, pids: list[str]) -> list[str]:
    """读取这些进程的 UID。同一 UID 的进程重启后仍能被 logcat --uid 跟上。"""
    if any(not pid.isdigit() for pid in pids):
        return []
    script = "; ".join(f"grep '^Uid:' /proc/{pid}/status" for pid in pids)
    try:
        result = _shell(serial, script, 10, _APP_TIMEOUT)
    except DensityError:
        return []
    uids: list[str] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        match = _UID_LINE.match(line.strip())
        if match is None:
            continue
        uid = match.group(1)
        if uid in seen:
            continue
        seen.add(uid)
        uids.append(uid)
    return uids


def _clear_logcat(serial: str) -> None:
    """清空 main、system、crash 缓冲。旧系统不认识分缓冲清空时，退回一次整体清空。"""
    cleared = _clear_logcat_buffers(serial, _LOG_BUFFERS)
    if cleared is None:
        return
    if _LOG_ARG_ERROR.search(cleared):
        cleared = _clear_logcat_buffers(serial, ())
        if cleared is None:
            return
    raise DensityError(_capture_failure(cleared, "清空日志失败"))


def _clear_logcat_buffers(serial: str, buffers: tuple[str, ...]) -> str | None:
    args = ["logcat"]
    for name in buffers:
        args.extend(["-b", name])
    args.append("-c")
    result = _shell(serial, args, 10, _LOG_CLEAR_TIMEOUT)
    if result.code == 0:
        return None
    return result.stderr or result.stdout


def _close_log_handle(capture: LogCapture) -> None:
    with capture.lock:
        if capture.handle.closed:
            return
        capture.handle.flush()
        capture.handle.close()


def _stop_record_process(recording: Recording, timeout: float) -> None:
    process = recording.process
    if process.poll() is not None:
        return
    try:
        _shell(recording.serial, "kill -2 $(pidof screenrecord)", 10, _RECORD_TIMEOUT)
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


def _read_screen(serial: str, timeout: float) -> ScreenDensity:
    command = (
        f"wm density; echo {_SPLIT}; "
        f"wm size; echo {_SPLIT}; "
        f"getprop ro.product.manufacturer; echo {_SPLIT}; "
        f"getprop ro.product.model; echo {_SPLIT}; "
        f"getprop ro.build.version.release; echo {_SPLIT}; "
        "getprop ro.build.version.sdk"
    )
    result = _shell(serial, command, timeout, _TIMEOUT)
    if result.code != 0 and not result.stdout.strip():
        raise DensityError(_clean_adb_error(result.stderr.strip()))
    parts = result.stdout.split(_SPLIT)
    if len(parts) < 6:
        raise DensityError("无法读取屏幕密度")
    return screen_from_outputs(serial, parts[0], parts[1], parts[2], parts[3], parts[4], parts[5])


class _AdbProcess:
    """长时间 shell 流。poll、wait、terminate 给录屏和日志判断是否还在跑。"""

    def __init__(self, connection: AdbConnection) -> None:
        self._connection = connection
        self._done = threading.Event()
        self._closed = False
        self.returncode: int | None = None
        try:
            connection.conn.settimeout(None)
        except OSError:
            pass
        self.stdout = connection.conn.makefile("rb")
        self.stderr = None

    def poll(self) -> int | None:
        if self._done.is_set():
            return 0 if self.returncode is None else self.returncode
        return None

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("adb", 0 if timeout is None else timeout)
        return 0 if self.returncode is None else self.returncode

    def terminate(self) -> None:
        self.kill()

    def kill(self) -> None:
        self._close()
        self.finish()

    def finish(self) -> None:
        self._close()
        if self.returncode is None:
            self.returncode = 0
        self._done.set()

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.stdout.close()
        except OSError:
            pass
        try:
            self._connection.close()
        except OSError:
            pass


@dataclass
class _ShellResult:
    stdout: str
    stderr: str
    code: int


def _device(serial: str, timeout: float | None = None) -> adbutils.AdbDevice:
    find_adb()
    return AdbClient(socket_timeout=timeout).device(serial)


def _list_devices(timeout: float) -> list[Device]:
    find_adb()
    try:
        infos = AdbClient(socket_timeout=timeout).list(extended=True)
    except (AdbError, OSError, socket.timeout, TimeoutError) as err:
        raise _from_adb_error(err, _TIMEOUT) from None
    devices: list[Device] = []
    for info in infos:
        tags = info.tags or {}
        model = tags.get("model", "") if isinstance(tags, dict) else ""
        state = info.state
        if state == "no":
            state = "no permissions"
        devices.append(Device(info.serial, state, model))
    return devices


def _shell(
    serial: str,
    command: str | list[str],
    timeout: float,
    timeout_message: str,
) -> _ShellResult:
    try:
        result = _device(serial).shell2(command, timeout=timeout, v2=True)
    except (AdbError, OSError, socket.timeout, TimeoutError, ValueError) as err:
        raise _from_adb_error(err, timeout_message) from None
    if result.stdout or result.stderr:
        stdout, stderr = result.stdout, result.stderr
    else:
        stdout, stderr = result.output, ""
    return _ShellResult(_normalize(stdout), _normalize(stderr), result.returncode)


def _shell_bytes(
    serial: str,
    command: str | list[str],
    timeout: float,
    timeout_message: str,
) -> bytes:
    try:
        data = _device(serial).shell(command, timeout=timeout, encoding=None)
    except (AdbError, OSError, socket.timeout, TimeoutError) as err:
        raise _from_adb_error(err, timeout_message) from None
    if isinstance(data, bytes):
        return data
    return str(data).encode("utf-8", errors="replace")


def _open_stream(serial: str, command: list[str]) -> _AdbProcess:
    try:
        connection = _device(serial).shell(command, stream=True)
    except (AdbError, OSError, socket.timeout, TimeoutError) as err:
        raise _from_adb_error(err, "无法启动 adb") from None
    if not isinstance(connection, AdbConnection):
        raise DensityError("无法启动 adb")
    return _AdbProcess(connection)


def _listing_error(result: _ShellResult) -> str | None:
    for line in result.stdout.splitlines():
        if not line.startswith(_LIST_FAIL):
            continue
        detail = line[len(_LIST_FAIL) :].strip() or result.stderr.strip()
        return _list_failure(detail)
    if result.code != 0:
        return _list_failure(result.stderr.strip() or result.stdout.strip())
    return None


def _error_text(err: BaseException) -> str:
    if isinstance(err, AdbInstallError):
        return err.output
    parts: list[str] = []
    for part in err.args:
        if isinstance(part, bytes):
            text = part.decode("utf-8", errors="replace")
        else:
            text = str(part)
        if text.strip():
            parts.append(text)
    return "\n".join(parts) if parts else str(err)


def _from_adb_error(err: BaseException, timeout_message: str) -> DensityError:
    if isinstance(err, (AdbTimeout, socket.timeout, TimeoutError)):
        return DensityError(timeout_message)
    text = _error_text(err)
    lower = text.lower()
    if "no adb exe" in lower or "could be found" in lower:
        return DensityError(_NO_ADB)
    if "connect to adb server" in lower:
        return DensityError("无法启动 adb")
    return DensityError(_clean_adb_error(text))


def _transfer_error(
    err: BaseException,
    timeout_message: str,
    wrap,
) -> DensityError:
    failure = _from_adb_error(err, timeout_message)
    message = str(failure)
    if message == timeout_message or message in _DIRECT_ADB_MESSAGES:
        return failure
    return DensityError(wrap(message))


def _read_stream(stream, sink: bytearray) -> None:
    if stream is None:
        return
    try:
        sink.extend(stream.read())
    except (OSError, ValueError):
        return


def _read_process_output(process: _AdbProcess, sink: bytearray) -> None:
    try:
        data = process.stdout.read()
        if data:
            sink.extend(data)
    except (OSError, ValueError):
        return
    finally:
        process.finish()


def _read_logcat(
    stream,
    handle: BinaryIO,
    lock: threading.Lock,
    lines: list[int],
    keyword: str,
    process: _AdbProcess,
    errors: bytearray,
) -> None:
    """把 logcat 按整行过滤后写入文件，大约每 0.2 秒刷一次盘。"""
    if stream is None:
        process.finish()
        return
    needles = tuple(part.casefold() for part in (item.strip() for item in keyword.split("|")) if part)
    pending = bytearray()
    pending_lines = 0
    stop = threading.Event()

    def flush_pending() -> bool:
        nonlocal pending_lines
        with lock:
            if handle.closed:
                pending.clear()
                pending_lines = 0
                return False
            if not pending:
                return True
            handle.write(pending)
            handle.flush()
            lines[0] += pending_lines
            pending.clear()
            pending_lines = 0
            return True

    def timed_flush() -> None:
        while not stop.wait(_LOG_FLUSH_SECONDS):
            if not flush_pending():
                return

    flusher = threading.Thread(target=timed_flush, daemon=True)
    flusher.start()
    try:
        for raw in stream:
            text = raw.decode("utf-8", errors="replace")
            if _LOG_ARG_ERROR.search(text):
                errors.extend(raw)
            if needles:
                folded = text.casefold()
                if not any(needle in folded for needle in needles):
                    continue
            with lock:
                if handle.closed:
                    return
                pending.extend(raw)
                pending_lines += 1
                oversized = len(pending) >= _LOG_FLUSH_BYTES
            if oversized and not flush_pending():
                return
    except (OSError, ValueError):
        return
    finally:
        stop.set()
        flusher.join(timeout=1)
        flush_pending()
        process.finish()


def _screenshot_via_pull(serial: str, timeout: float, prior_error: str) -> Path:
    remote = _remote_temp(".png")
    try:
        result = _shell(serial, ["screencap", "-p", remote], timeout, _SCREENSHOT_TIMEOUT)
        if result.code != 0:
            detail = result.stderr or result.stdout or prior_error
            raise DensityError(_capture_failure(detail, "截屏失败"))
        local = _reserve_path("截屏", ".png")
        _pull_file(serial, remote, local, timeout)
        if not _is_png_file(local):
            local.unlink(missing_ok=True)
            raise DensityError(_capture_failure(prior_error, "截屏失败"))
        return local
    finally:
        _remove_remote(serial, remote)


def _pull_file(serial: str, remote: str, local: Path, timeout: float, *, allow_empty: bool = False) -> None:
    device = _device(serial, timeout)
    try:
        device.sync.pull(remote, local)
    except (AdbError, AdbSyncError, OSError, socket.timeout, TimeoutError) as err:
        if local.is_file():
            local.unlink(missing_ok=True)
        raise _transfer_error(err, _SAVE_TIMEOUT, _pull_failure_message) from None
    if local.is_file() and (allow_empty or local.stat().st_size > 0):
        return
    if local.is_file():
        local.unlink(missing_ok=True)
    raise DensityError("保存失败")


def _pull_failure_message(text: str) -> str:
    lines = [line.strip() for line in _normalize(text).splitlines() if line.strip()]
    if not lines:
        return "保存失败"
    return f"保存失败：{lines[-1]}"


def _remove_remote(serial: str, remote: str) -> None:
    try:
        _shell(serial, ["rm", "-f", remote], 10, "保存失败")
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


def _list_shell(remote: str) -> str:
    """列目录，并把指向目录的符号链接也标成目录。

    /sdcard 在 / 下是符号链接。ls -p 只看链接本身，不会加 /，
    从 /sdcard 点上级回到 / 之后就点不回去。test -d 会跟着链接走。
    """
    quoted = _shell_quote(remote)
    token = _LIST_FAIL
    return (
        f"if cd {quoted} 2>/dev/null; then "
        f"if list=$(ls -1A 2>&1); then "
        "printf '%s\\n' \"$list\" | while IFS= read -r name; do "
        "[ -n \"$name\" ] || continue; "
        "if [ -d \"$name\" ]; then printf '%s/\\n' \"$name\"; "
        "else printf '%s\\n' \"$name\"; fi; "
        "done; "
        f"else printf '%s %s\\n' {token} \"$list\"; fi; "
        f"else err=$(cd {quoted} 2>&1); printf '%s %s\\n' {token} \"$err\"; fi"
    )


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
    if (
        "no devices" in lower
        or "no emulators" in lower
        or "can't find any android device" in lower
        or "cannot find any android device" in lower
    ):
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
