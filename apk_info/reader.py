"""用 aapt 读取 apk 的包名、版本、SDK、ABI 和权限。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_NO_AAPT = "未找到 aapt，请安装 Android SDK build-tools"
_NOT_APK = "请选择 apk 文件"
_NO_PACKAGE = "没有读到包名"
_FIELD = re.compile(r"([A-Za-z0-9_.-]+)\s*[:=]\s*'((?:\\.|[^'])*)'")
_QUOTED = re.compile(r"'((?:\\.|[^'])*)'")
_LABEL_KEYS = (
    "application-label-zh-CN",
    "application-label-zh_CN",
    "application-label-zh",
    "application-label",
)


class ApkError(Exception):
    pass


@dataclass(frozen=True)
class Permission:
    name: str
    from_sdk_23: bool


@dataclass(frozen=True)
class ApkInfo:
    label: str
    package: str
    version_name: str
    version_code: str
    min_sdk: str
    target_sdk: str
    compile_sdk: str
    abis: tuple[str, ...]
    permissions: tuple[Permission, ...]
    size: int


def read_apk(path: Path, *, timeout: float = 60) -> ApkInfo:
    if path.suffix.lower() != ".apk" or not path.is_file():
        raise ApkError(_NOT_APK)
    try:
        size = path.stat().st_size
    except OSError as err:
        raise ApkError(f"无法读取文件：{err}") from err
    stdout = _dump_badging(find_aapt(), path, timeout)
    info = parse_badging(stdout)
    if not info.package:
        raise ApkError(_NO_PACKAGE)
    return ApkInfo(
        label=info.label,
        package=info.package,
        version_name=info.version_name,
        version_code=info.version_code,
        min_sdk=info.min_sdk,
        target_sdk=info.target_sdk,
        compile_sdk=info.compile_sdk,
        abis=info.abis,
        permissions=info.permissions,
        size=size,
    )


def find_aapt() -> str:
    for name in ("aapt2", "aapt"):
        found = shutil.which(name)
        if found:
            return found
    for root in _sdk_roots():
        tools = root / "build-tools"
        if not tools.is_dir():
            continue
        versions = [item for item in tools.iterdir() if item.is_dir()]
        versions.sort(key=_version_key, reverse=True)
        for version in versions:
            for name in ("aapt2.exe", "aapt2", "aapt.exe", "aapt"):
                path = version / name
                if path.is_file():
                    return str(path)
    raise ApkError(_NO_AAPT)


def parse_badging(text: str) -> ApkInfo:
    labels: dict[str, str] = {}
    package = ""
    version_name = ""
    version_code = ""
    min_sdk = ""
    target_sdk = ""
    compile_sdk = ""
    abis: list[str] = []
    permissions: list[Permission] = []
    seen: set[tuple[str, bool]] = set()
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("uses-implied-"):
            continue
        if line.startswith("package:"):
            fields = _fields(line)
            package = fields.get("name", package)
            version_name = fields.get("versionName", version_name)
            version_code = fields.get("versionCode", version_code)
            compile_sdk = fields.get("compileSdkVersion", compile_sdk)
            continue
        if line.startswith("targetSdkVersion:"):
            target_sdk = _first_quoted(line) or target_sdk
            continue
        if line.startswith("sdkVersion:") or line.startswith("minSdkVersion:"):
            min_sdk = _first_quoted(line) or min_sdk
            continue
        if line.startswith("native-code:"):
            abis = _quoted(line)
            continue
        if line.startswith("uses-permission-sdk-23:"):
            _add_permission(permissions, seen, _fields(line).get("name", ""), True)
            continue
        if line.startswith("uses-permission:"):
            _add_permission(permissions, seen, _fields(line).get("name", ""), False)
            continue
        if line.startswith("application-label"):
            key, _, _rest = line.partition(":")
            value = _first_quoted(line)
            if value:
                labels[key] = value
    return ApkInfo(
        label=_label(labels),
        package=package,
        version_name=version_name,
        version_code=version_code,
        min_sdk=min_sdk,
        target_sdk=target_sdk,
        compile_sdk=compile_sdk,
        abis=tuple(abis),
        permissions=tuple(permissions),
        size=0,
    )


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.2f} MB"


def _dump_badging(tool: str, path: Path, timeout: float) -> str:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            [tool, "dump", "badging", str(path)],
            capture_output=True,
            timeout=timeout,
            check=False,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        raise ApkError("读取超时") from None
    except OSError:
        raise ApkError("无法启动 aapt") from None
    stdout = completed.stdout.decode("utf-8", errors="replace")
    if completed.returncode != 0 and "package:" not in stdout:
        raise ApkError(_failure(completed.stderr.decode("utf-8", errors="replace")))
    return stdout


def _failure(stderr: str) -> str:
    detail = ""
    for line in stderr.replace("\r\n", "\n").split("\n"):
        text = line.strip()
        if text:
            detail = text
            break
    if len(detail) > 180:
        detail = detail[:180] + "…"
    if not detail:
        return "aapt 执行失败"
    return f"aapt 执行失败：{detail}"


def _add_permission(items: list[Permission], seen: set[tuple[str, bool]], name: str, from_sdk_23: bool) -> None:
    if not name:
        return
    key = (name, from_sdk_23)
    if key in seen:
        return
    seen.add(key)
    items.append(Permission(name, from_sdk_23))


def _label(labels: dict[str, str]) -> str:
    for key in _LABEL_KEYS:
        value = labels.get(key, "")
        if value:
            return value
    return ""


def _fields(line: str) -> dict[str, str]:
    return {key: _unescape(value) for key, value in _FIELD.findall(line)}


def _quoted(line: str) -> list[str]:
    return [_unescape(value) for value in _QUOTED.findall(line)]


def _first_quoted(line: str) -> str:
    values = _quoted(line)
    return values[0] if values else ""


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


def _sdk_roots() -> list[Path]:
    roots: list[Path] = []
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Android" / "Sdk")
    adb = shutil.which("adb")
    if adb:
        platform = Path(adb).resolve().parent
        if platform.name.lower() == "platform-tools":
            roots.append(platform.parent)
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _version_key(path: Path) -> tuple:
    head = path.name.split("-", 1)[0]
    numbers = tuple(int(part) for part in head.split(".") if part.isdigit())
    stable = 0 if "-" in path.name else 1
    return (numbers, stable)
