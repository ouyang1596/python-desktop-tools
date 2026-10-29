"""连接手机上的 frida-server，挂钩 Java 方法并记录参数、返回值和调用堆栈。"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import threading
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from adbutils import AdbClient
from adbutils.errors import AdbError

from screen_density.density import SAVE_DIR, DensityError, Device, find_adb


class TraceError(Exception):
    """连接或挂钩失败时的说明。"""


@dataclass(frozen=True)
class AppInfo:
    name: str
    package: str
    pid: int

    def label(self) -> str:
        name = self.name.strip()
        if name and name != self.package:
            return f"{self.package}（{name}）"
        return self.package


_NO_DEVICE = "没有检测到手机，请打开 USB 调试并连接"
_NO_ADB = "未找到 adb，请安装 Android SDK platform-tools 并加入 PATH"
_FRIDA_MISSING = "本机未安装 frida，请先执行 pip install -r requirements.txt"
_FRIDA_DOWN = "连不上 frida-server。请确认手机上已启动，且版本与本机 frida 一致"
_FRIDA_NO_DEVICE = "Frida 未发现这台手机。请确认 frida-server 已在该设备上运行"
_ARG_LIMIT = 400
_PATTERN_LIMIT = 40
_TOO_WIDE = "模式太宽，请把类名或方法名写具体一点"
_NEED_TARGET = "请填写类名和方法名"

_BOOTSTRAP = r"""
function loadFridaBridge(name) {
  var bridge;
  send({type: "frida:load-bridge", name: name});
  recv("frida:bridge-loaded", function (message) {
    bridge = Script.evaluate(
      "/frida/bridges/" + message.filename,
      "(function () {\n" + message.source + "\nObject.defineProperty(globalThis, '" + name + "', { value: bridge });\nreturn bridge;\n})();"
    );
  }).wait();
  return bridge;
}
Object.defineProperty(globalThis, "Java", {
  enumerable: true,
  configurable: true,
  get: function () {
    return loadFridaBridge("Java");
  }
});
"""

_SCRIPT = r"""
var config = __CONFIG__;
var hooked = false;
var failed = false;
var announcedWait = false;
var announcedHint = false;
var pending = false;
var depth = 0;
var attempts = 0;
var timer = null;

function stopTimer() {
  if (timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}

function describe(value) {
  try {
    if (value === null || value === undefined) return String(value);
    var text = String(value);
    if (text.length > 200) return text.slice(0, 200) + "…";
    return text;
  } catch (e) {
    return "<无法显示>";
  }
}

function typeName(t) {
  if (!t) return "?";
  if (t.className) return String(t.className);
  if (t.name) return String(t.name);
  return String(t);
}

function simpleName(name) {
  var parts = String(name || "").split(".");
  return parts.length ? parts[parts.length - 1] : "";
}

function loaders() {
  try {
    return Java.enumerateClassLoadersSync();
  } catch (e) {
    try {
      return [Java.classFactory.loader];
    } catch (e2) {
      return [];
    }
  }
}

function useWithLoader(loader) {
  if (loader) {
    try {
      if (loader.findClass) loader.findClass(config.className);
      else if (loader.loadClass) loader.loadClass(config.className);
    } catch (e) {
      throw e;
    }
  }
  if (loader && Java.ClassFactory && Java.ClassFactory.get) {
    return Java.ClassFactory.get(loader).use(config.className);
  }
  if (loader) {
    var previous = Java.classFactory.loader;
    Java.classFactory.loader = loader;
    try {
      return Java.use(config.className);
    } finally {
      Java.classFactory.loader = previous;
    }
  }
  return Java.use(config.className);
}

function resolveClass() {
  try {
    return Java.use(config.className);
  } catch (e) {
  }
  var found = loaders();
  for (var i = 0; i < found.length; i++) {
    try {
      return useWithLoader(found[i]);
    } catch (e) {
    }
  }
  return null;
}

function similarClasses() {
  var needle = simpleName(config.className);
  if (!needle) return [];
  var matches = [];
  try {
    var names = Java.enumerateLoadedClassesSync();
    for (var i = 0; i < names.length; i++) {
      var name = String(names[i]);
      if (name === config.className) continue;
      if (name.indexOf(needle) < 0) continue;
      matches.push(name);
      if (matches.length >= 8) break;
    }
  } catch (e) {
  }
  return matches;
}

function describeReturn(overload, value) {
  var ret = overload.returnType;
  var name = ret ? typeName(ret) : "";
  if (name === "void" || name === "V") return "void";
  return describe(value);
}

function captureCall(overload, argList, withStack) {
  var parts = [];
  var types = [];
  var stack = "";
  depth += 1;
  try {
    for (var i = 0; i < argList.length; i++) parts.push(describe(argList[i]));
    var argTypes = overload.argumentTypes || [];
    for (var j = 0; j < argTypes.length; j++) types.push(typeName(argTypes[j]));
    if (withStack) {
      try {
        var Log = Java.use("android.util.Log");
        var Exception = Java.use("java.lang.Exception");
        stack = String(Log.getStackTraceString(Exception.$new()));
      } catch (e) {
        stack = String(e);
      }
    }
  } catch (e) {
  } finally {
    depth -= 1;
  }
  return {args: parts.join(", "), types: types.join(", "), stack: stack};
}

function installOverload(overload, className, displayName, withStack) {
  overload.implementation = function () {
    if (depth > 0) return overload.apply(this, arguments);
    var argList = [];
    for (var i = 0; i < arguments.length; i++) argList.push(arguments[i]);
    var meta = captureCall(overload, argList, withStack);
    var thrown = null;
    var result;
    try {
      result = overload.apply(this, argList);
    } catch (err) {
      thrown = err;
    }
    depth += 1;
    try {
      var payload = {
        type: "hit",
        signature: className + "." + displayName + "(" + meta.types + ")",
        args: meta.args
      };
      if (withStack) payload.stack = meta.stack;
      if (thrown) payload.thrown = describe(thrown);
      else payload.retval = describeReturn(overload, result);
      send(payload);
    } catch (e) {
    } finally {
      depth -= 1;
    }
    if (thrown) throw thrown;
    return result;
  };
}

function hookOverloads(clazz, className, fridaName, displayName, withStack) {
  var method = clazz[fridaName];
  if (!method || !method.overloads || method.overloads.length === 0) return 0;
  var overloads = method.overloads;
  for (var n = 0; n < overloads.length; n++) {
    installOverload(overloads[n], className, displayName, withStack);
  }
  return overloads.length;
}

function hookClass(clazz) {
  var count = hookOverloads(clazz, config.className, config.methodName, config.displayName, true);
  if (!count) {
    send({type: "error", message: "方法不存在：" + config.className + "." + config.displayName});
    return "missing";
  }
  send({type: "ready", count: count});
  return "ok";
}

var seen = {};
var visited = {};
var hookedCount = 0;
var capSent = false;
var announcedReady = false;
var loaderHooked = false;
var classRe = null;
var methodRe = null;

function sendReady(capped) {
  var first = !announcedReady;
  announcedReady = true;
  send({
    type: "ready",
    count: hookedCount,
    pattern: true,
    capped: !!capped,
    first: first
  });
}

function notifyCap() {
  if (capSent) return;
  capSent = true;
  sendReady(true);
}

function hookNamed(clazz, className, fridaName) {
  var key = className + "\n" + fridaName;
  if (seen[key]) return false;
  if (className === "java.lang.ClassLoader" && fridaName === "loadClass") return false;
  if (hookedCount >= config.limit) {
    notifyCap();
    return false;
  }
  var display = fridaName === "$init" ? "<init>" : fridaName;
  var method = clazz[fridaName];
  if (!method || !method.overloads || method.overloads.length === 0) return false;
  var installed = 0;
  try {
    for (var n = 0; n < method.overloads.length; n++) {
      installOverload(method.overloads[n], className, display, false);
      installed += 1;
    }
  } catch (e) {
    if (!installed) return false;
  }
  seen[key] = true;
  hookedCount += 1;
  return true;
}

function considerClass(className, announce) {
  if (capSent || !className || visited[className]) return;
  if (className.indexOf("/") >= 0 && className.indexOf(".") < 0) {
    className = className.replace(/\//g, ".");
  }
  if (!className || visited[className]) return;
  if (className.charAt(0) === "[") {
    visited[className] = true;
    return;
  }
  if (!classRe.test(className)) {
    visited[className] = true;
    return;
  }
  var clazz;
  try {
    clazz = Java.use(className);
  } catch (e) {
    if (announce) visited[className] = true;
    return;
  }
  visited[className] = true;
  var wanted = {};
  try {
    var declared = clazz.class.getDeclaredMethods();
    for (var i = 0; i < declared.length; i++) {
      var reflected = declared[i];
      if ((reflected.getModifiers() & 1024) !== 0) continue;
      var name = String(reflected.getName());
      if (!methodRe.test(name)) continue;
      wanted[name === "<init>" ? "$init" : name] = true;
    }
  } catch (e) {
  }
  if (config.includeInit) wanted["$init"] = true;
  var added = false;
  for (var fridaName in wanted) {
    if (hookNamed(clazz, className, fridaName)) added = true;
    if (capSent) break;
  }
  if (announce && added && !capSent) sendReady(false);
}

function scanLoaded() {
  var names = Java.enumerateLoadedClassesSync();
  for (var i = 0; i < names.length; i++) {
    if (capSent) break;
    try {
      considerClass(String(names[i]), false);
    } catch (e) {
    }
  }
  if (hookedCount > 0 && !announcedReady) sendReady(false);
}

function later(fn) {
  if (typeof setImmediate === "function") setImmediate(fn);
  else setTimeout(fn, 0);
}

function hookClassLoader() {
  if (loaderHooked || capSent) return;
  loaderHooked = true;
  var ClassLoader = Java.use("java.lang.ClassLoader");
  var overloads = ClassLoader.loadClass.overloads;
  for (var i = 0; i < overloads.length; i++) {
    (function (overload) {
      overload.implementation = function () {
        var result = overload.apply(this, arguments);
        if (capSent || !arguments.length) return result;
        var name = String(arguments[0]);
        later(function () {
          if (capSent || !name) return;
          Java.perform(function () {
            try {
              considerClass(name, true);
            } catch (e) {
            }
          });
        });
        return result;
      };
    })(overloads[i]);
  }
}

function startPattern() {
  classRe = new RegExp(config.classRe);
  methodRe = new RegExp(config.methodRe);
  scanLoaded();
  hookClassLoader();
  if (hookedCount === 0) send({type: "waiting"});
}

function scan() {
  if (!Java.available) {
    send({type: "error", message: "当前进程没有 Java 运行时"});
    return "missing";
  }
  var clazz = resolveClass();
  if (clazz) return hookClass(clazz);
  attempts += 1;
  if (!announcedHint && attempts >= 6) {
    announcedHint = true;
    send({type: "hint", classes: similarClasses()});
  }
  return "absent";
}

function finish(result) {
  if (result === "ok") {
    hooked = true;
    stopTimer();
    return;
  }
  if (result === "missing") {
    failed = true;
    stopTimer();
    return;
  }
  if (!announcedWait) {
    announcedWait = true;
    send({type: "waiting"});
  }
}

function work() {
  if (hooked || failed) return;
  var result = "absent";
  try {
    result = scan();
  } catch (e) {
    send({type: "error", message: "挂钩失败：" + String(e)});
    result = "missing";
  }
  finish(result);
}

function tryHook() {
  if (hooked || failed || pending) return;
  pending = true;
  try {
    Java.perform(function () {
      pending = false;
      work();
    });
  } catch (e) {
    pending = false;
    send({type: "error", message: "挂钩失败：" + String(e)});
    failed = true;
    stopTimer();
  }
}

function boot() {
  if (config.mode === "pattern") {
    Java.perform(function () {
      if (!Java.available) {
        send({type: "error", message: "当前进程没有 Java 运行时"});
        return;
      }
      try {
        startPattern();
      } catch (e) {
        send({type: "error", message: "挂钩失败：" + String(e)});
      }
    });
    return;
  }
  tryHook();
  if (!hooked && !failed) timer = setInterval(tryHook, 500);
}

try {
  boot();
} catch (e) {
  send({type: "error", message: "挂钩失败：" + String(e)});
}
"""


def list_devices() -> list[Device]:
    """已连接的手机。不读取屏幕密度。"""
    try:
        find_adb()
    except DensityError as err:
        raise TraceError(str(err) or _NO_ADB) from None
    try:
        infos = AdbClient(socket_timeout=8).list(extended=True)
    except (AdbError, OSError, socket.timeout, TimeoutError) as err:
        raise TraceError(_adb_message(err)) from None
    devices: list[Device] = []
    for info in infos:
        tags = info.tags or {}
        model = tags.get("model", "") if isinstance(tags, dict) else ""
        state = info.state
        if state == "no":
            state = "no permissions"
        devices.append(Device(info.serial, state, model))
    return devices


def list_apps(serial: str) -> list[AppInfo]:
    """正在运行的应用。进程名可以是应用名，不必等于包名。"""
    frida = _import_frida()
    device = _usb_device(frida, serial)
    try:
        applications = device.enumerate_applications()
        processes = device.enumerate_processes()
    except Exception as err:
        raise TraceError(_frida_message(err)) from None
    apps = [
        (
            str(getattr(app, "identifier", "") or ""),
            str(getattr(app, "name", "") or ""),
            int(getattr(app, "pid", 0) or 0),
        )
        for app in applications
    ]
    procs = [
        (str(getattr(process, "name", "") or ""), int(getattr(process, "pid", 0) or 0))
        for process in processes
    ]
    return _running_from(apps, procs)


def _clip(text: str) -> str:
    if len(text) > _ARG_LIMIT:
        return text[:_ARG_LIMIT] + "…"
    return text


def _has_literal(pattern: str) -> bool:
    return any(ch not in "*. " for ch in pattern)


def _glob_regex(pattern: str) -> str:
    return "^" + "".join(".*" if ch == "*" else re.escape(ch) for ch in pattern) + "$"


def _glob_matches(pattern: str, text: str) -> bool:
    return re.search(_glob_regex(pattern), text) is not None


def prepare_trace(class_name: str, method_name: str) -> dict:
    """精确挂钩；类名或方法名含 * 时改为模式跟踪。"""
    class_name = class_name.strip()
    method_name = method_name.strip()
    if not class_name:
        raise TraceError(_NEED_TARGET)
    if "*" not in class_name and "*" not in method_name:
        if not method_name:
            raise TraceError(_NEED_TARGET)
        display = method_name
        return {
            "mode": "exact",
            "className": class_name,
            "methodName": "$init" if display == "<init>" else display,
            "displayName": display,
            "classRe": "",
            "methodRe": "",
            "includeInit": False,
            "limit": _PATTERN_LIMIT,
        }
    if not method_name:
        method_name = "*"
    if not _has_literal(class_name) and not _has_literal(method_name):
        raise TraceError(_TOO_WIDE)
    return {
        "mode": "pattern",
        "className": class_name,
        "methodName": method_name,
        "displayName": method_name,
        "classRe": _glob_regex(class_name),
        "methodRe": _glob_regex(method_name),
        "includeInit": _glob_matches(method_name, "<init>") or _glob_matches(method_name, "$init"),
        "limit": _PATTERN_LIMIT,
    }


def format_hit(payload: dict, *, now: datetime | None = None) -> str:
    """把一次调用整理成可追加到日志的文本。"""
    moment = now or datetime.now()
    signature = str(payload.get("signature") or "").strip()
    args = _clip(" ".join(str(payload.get("args") or "").split()))
    lines = [f"{moment:%H:%M:%S}  {signature}"]
    if args:
        lines.append(f"参数: {args}")
    if "thrown" in payload:
        thrown = _clip(" ".join(str(payload.get("thrown") or "").split()))
        lines.append(f"抛出: {thrown}")
    elif "retval" in payload:
        retval = _clip(" ".join(str(payload.get("retval") if payload.get("retval") is not None else "").split()))
        lines.append(f"返回: {retval}")
    stack = str(payload.get("stack") or "").strip()
    if stack:
        lines.append(stack)
    return "\n".join(lines) + "\n\n"


def not_running_message(package: str) -> str:
    return f"请先打开 {package}，点刷新后再开始"


_TIME_LINE = re.compile(r"^(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})(?:\.\d+)?\s+\S")
_STATUS_PREFIXES = ("开始挂钩", "已挂钩", "失败：", "失败:", "未找到")
_HOOK_TARGET = re.compile(r"^开始挂钩\s+(\S+)\s+/")
_PROP_LINE = re.compile(r"^\[([^\]]+)\]: \[(.*)\]$")


def save_trace_log(
    text: str,
    *,
    now: datetime | None = None,
    serial: str = "",
    model: str = "",
    package: str = "",
    process: str = "",
    pid: int = 0,
) -> Path:
    """把调用日志写成 Android Studio 可打开的 .logcat，保存到 D:\\adb。"""
    moment = now or datetime.now()
    logged = _package_from_log(text)
    app_id = logged or package.strip()
    if process.strip() and (not logged or logged == package.strip()):
        process_name = process.strip()
    else:
        process_name = app_id
    if logged and logged != package.strip():
        pid = 0
    document = _logcat_document(
        text,
        now=moment,
        serial=serial.strip(),
        model=model.strip(),
        package=app_id,
        process=process_name,
        pid=pid,
    )
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = moment.strftime("%Y%m%d_%H%M%S")
    names = [f"frida_{stamp}.logcat"]
    names.extend(f"frida_{stamp}_{index}.logcat" for index in range(2, 100))
    for name in names:
        path = SAVE_DIR / name
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        try:
            os.write(fd, payload)
        except OSError:
            os.close(fd)
            path.unlink(missing_ok=True)
            raise
        os.close(fd)
        return path
    raise TraceError("无法保存日志")


def _package_from_log(text: str) -> str:
    for line in text.splitlines():
        match = _HOOK_TARGET.match(line.strip())
        if match:
            return match.group(1)
    return ""


def _logcat_document(
    text: str,
    *,
    now: datetime,
    serial: str,
    model: str,
    package: str,
    process: str,
    pid: int,
) -> dict:
    messages = [_beginning_message()]
    seen_seconds: dict[int, int] = {}
    for moment, body in _stamp_blocks(_log_blocks(text), now):
        seconds = int(moment.timestamp())
        nanos = seen_seconds.get(seconds, 0)
        seen_seconds[seconds] = nanos + 1_000_000
        messages.append(
            {
                "header": {
                    "logLevel": "ERROR" if body.startswith("失败") else "INFO",
                    "pid": pid if pid > 0 else 0,
                    "tid": pid if pid > 0 else 0,
                    "applicationId": package,
                    "processName": process,
                    "tag": "Frida",
                    "timestamp": {"seconds": seconds, "nanos": nanos},
                },
                "message": body,
            }
        )
    return {
        "metadata": {
            "device": {"physicalDevice": _physical_device(serial, model)},
            "filter": "",
            "projectApplicationIds": [package] if package else [],
        },
        "logcatMessages": messages,
    }


def _beginning_message() -> dict:
    return {
        "header": {
            "logLevel": "INFO",
            "pid": 0,
            "tid": 0,
            "applicationId": "",
            "processName": "",
            "tag": "",
            "timestamp": {"seconds": 0, "nanos": 0},
        },
        "message": "--------- beginning of main",
    }


def _physical_device(serial: str, model: str) -> dict:
    props = _device_props(serial)
    sdk_text = props.get("ro.build.version.sdk", "")
    try:
        sdk = int(sdk_text)
    except ValueError:
        sdk = 0
    return {
        "serialNumber": serial,
        "isOnline": True,
        "release": props.get("ro.build.version.release", ""),
        "apiLevel": {"majorVersion": sdk, "minorVersion": 0},
        "featureLevel": sdk,
        "manufacturer": props.get("ro.product.manufacturer", ""),
        "model": props.get("ro.product.model", "") or model,
        "type": "HANDHELD",
    }


def _device_props(serial: str) -> dict[str, str]:
    if not serial:
        return {}
    try:
        raw = AdbClient(socket_timeout=3).device(serial).shell("getprop", timeout=3)
    except (AdbError, OSError, socket.timeout, TimeoutError, ValueError, TypeError):
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    props: dict[str, str] = {}
    for line in str(raw).splitlines():
        match = _PROP_LINE.match(line.strip())
        if match:
            props[match.group(1)] = match.group(2)
    return props


def _log_blocks(text: str) -> list[tuple[tuple[int, int, int] | None, str]]:
    blocks: list[list[str]] = []
    clocks: list[tuple[int, int, int] | None] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if not line.strip():
            current = None
            continue
        if current is None or _is_log_entry(line):
            current = [line]
            blocks.append(current)
            clocks.append(_clock_from_line(line))
        else:
            current.append(line)
    result: list[tuple[tuple[int, int, int] | None, str]] = []
    for clock, lines in zip(clocks, blocks):
        body = "\n".join(lines).strip()
        if body:
            result.append((clock, body))
    if not result and text.strip():
        result.append((None, text.strip()))
    return result


def _is_log_entry(line: str) -> bool:
    stripped = line.strip()
    return _TIME_LINE.match(stripped) is not None or stripped.startswith(_STATUS_PREFIXES)


def _clock_from_line(line: str) -> tuple[int, int, int] | None:
    match = _TIME_LINE.match(line.strip())
    if match is None:
        return None
    return int(match.group("h")), int(match.group("m")), int(match.group("s"))


def _stamp_blocks(
    blocks: list[tuple[tuple[int, int, int] | None, str]],
    now: datetime,
) -> list[tuple[datetime, str]]:
    parsed: list[datetime | None] = []
    for clock, _body in blocks:
        if clock is None:
            parsed.append(None)
            continue
        hour, minute, second = clock
        moment = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
        if moment > now + timedelta(minutes=2):
            moment -= timedelta(days=1)
        parsed.append(moment)
    timed = [item for item in parsed if item is not None]
    anchor = min(timed) if timed else now
    leading = 0
    for item in parsed:
        if item is not None:
            break
        leading += 1
    start = anchor - timedelta(seconds=leading)
    stamped: list[tuple[datetime, str]] = []
    for index, ((_clock, body), moment) in enumerate(zip(blocks, parsed)):
        if moment is None and index < leading:
            moment = start + timedelta(seconds=index)
        elif moment is None:
            moment = now
        stamped.append((moment, body))
    return stamped


class Tracer:
    """挂钩一个方法，或按模式挂钩一批方法。再次开始或 stop 会卸掉上一次。"""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._holder: dict[str, object] = {}

    def start(
        self,
        serial: str,
        package: str,
        config: dict,
        on_event: Callable[[dict], None],
    ) -> None:
        self.stop()
        holder: dict[str, object] = {}
        stop = threading.Event()
        self._holder = holder
        self._stop = stop
        threading.Thread(
            target=self._run,
            args=(serial, package, config, on_event, holder, stop),
            daemon=True,
        ).start()

    def stop(self) -> None:
        self._stop.set()
        _unload(self._holder)

    def _run(
        self,
        serial: str,
        package: str,
        config: dict,
        on_event: Callable[[dict], None],
        holder: dict[str, object],
        stop: threading.Event,
    ) -> None:
        session = None
        try:
            frida = _import_frida()
            device = _usb_device(frida, serial)
            session = _attach(device, package)
            if stop.is_set():
                return
            bridge = _java_bridge_source()
            script = session.create_script(_build_script(config))

            def on_message(message, _data) -> None:
                if _serve_java_bridge(message, script, bridge):
                    return
                if stop.is_set():
                    return
                kind = message.get("type")
                if kind == "send":
                    payload = message.get("payload")
                    if isinstance(payload, dict):
                        on_event(payload)
                    return
                if kind == "error":
                    desc = message.get("description") or message.get("stack") or "脚本错误"
                    on_event({"type": "error", "message": str(desc).splitlines()[0]})

            script.on("message", on_message)
            holder["session"] = session
            holder["script"] = script
            if stop.is_set():
                return
            script.load()
            stop.wait()
        except TraceError as err:
            if not stop.is_set():
                on_event({"type": "error", "message": str(err)})
        except Exception as err:
            if not stop.is_set():
                on_event({"type": "error", "message": _frida_message(err)})
        finally:
            if session is not None and holder.get("session") is None:
                try:
                    session.detach()
                except Exception:
                    pass
            _unload(holder)


def _build_script(config: dict) -> str:
    payload = json.dumps(config, ensure_ascii=False)
    return _BOOTSTRAP + _SCRIPT.replace("__CONFIG__", payload, 1)


def _running_from(apps: list[tuple[str, str, int]], processes: list[tuple[str, int]]) -> list[AppInfo]:
    names: dict[str, str] = {}
    for package, name, _pid in apps:
        if package and package not in names:
            names[package] = name
    by_name = {name.casefold(): package for package, name in names.items() if name}
    chosen: dict[str, AppInfo] = {}
    for package, name, pid in apps:
        if pid > 0 and package:
            chosen[package] = AppInfo(name, package, pid)
    for pname, pid in processes:
        if pid <= 0 or not pname:
            continue
        base = pname.split(":", 1)[0]
        package = ""
        if pname in names:
            package = pname
        elif base in names:
            package = base
        else:
            package = by_name.get(pname.casefold(), "") or by_name.get(base.casefold(), "")
        if not package and "." in base:
            package = base
        if not package or package in chosen:
            continue
        chosen[package] = AppInfo(names.get(package, ""), package, pid)
    found = list(chosen.values())
    found.sort(key=lambda item: (item.name or item.package).casefold())
    return found


def _running_pid(device, package: str) -> int | None:
    """包名对应的进程。优先用应用列表里的 pid，进程名可能是应用名。"""
    target = package.strip()
    if not target:
        return None
    app_name = ""
    try:
        applications = device.enumerate_applications()
    except Exception as err:
        raise TraceError(_frida_message(err)) from None
    for app in applications:
        identifier = str(getattr(app, "identifier", "") or "")
        if identifier != target:
            continue
        if not app_name:
            app_name = str(getattr(app, "name", "") or "")
        pid = int(getattr(app, "pid", 0) or 0)
        if pid > 0:
            return pid
    try:
        processes = device.enumerate_processes()
    except Exception as err:
        raise TraceError(_frida_message(err)) from None
    named: list[tuple[str, int]] = []
    for process in processes:
        pname = str(getattr(process, "name", "") or "")
        pid = int(getattr(process, "pid", 0) or 0)
        if pname and pid > 0:
            named.append((pname, pid))
    for pname, pid in named:
        if pname == target:
            return pid
    children = [pid for pname, pid in named if pname.startswith(target + ":")]
    if children:
        return children[0]
    if app_name:
        folded = app_name.casefold()
        for pname, pid in named:
            if ":" in pname:
                continue
            if pname.casefold() == folded:
                return pid
    try:
        process = device.get_process(target)
        pid = int(getattr(process, "pid", 0) or 0)
        if pid > 0:
            return pid
    except Exception:
        pass
    return None


def _attach(device, package: str):
    pid = _running_pid(device, package)
    if pid is None:
        raise TraceError(not_running_message(package))
    try:
        return device.attach(pid)
    except Exception as err:
        message = _frida_message(err)
        lower = message.lower()
        if "not found" in lower:
            raise TraceError(not_running_message(package)) from None
        raise TraceError(message) from None


def _usb_device(frida, serial: str):
    try:
        devices = frida.enumerate_devices()
    except Exception as err:
        raise TraceError(_frida_message(err)) from None
    for device in devices:
        if _device_type(device) == "usb" and device.id == serial:
            return device
    raise TraceError(_FRIDA_NO_DEVICE)


def _device_type(device) -> str:
    kind = getattr(device, "type", "")
    return str(getattr(kind, "value", kind)).lower()


def _import_frida():
    try:
        import frida
    except ImportError:
        raise TraceError(_FRIDA_MISSING) from None
    return frida


def _java_bridge_source() -> str:
    """Frida 17 的命令行会自带 Java 桥，create_script 不会。跟 frida -l 一样在运行时加载。"""
    spec = importlib.util.find_spec("frida_tools")
    origin = getattr(spec, "origin", None) if spec is not None else None
    path = Path(origin).resolve().parent / "bridges" / "java.js" if origin else None
    if path is None or not path.is_file():
        raise TraceError("找不到 Java 运行时。请执行 pip install frida-tools，并与本机 frida 版本一致")
    return path.read_text(encoding="utf-8")


def _serve_java_bridge(message: dict, script, source: str) -> bool:
    if message.get("type") != "send":
        return False
    payload = message.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "frida:load-bridge":
        return False
    name = str(payload.get("name") or "Java")
    script.post({"type": "frida:bridge-loaded", "filename": f"{name.lower()}.js", "source": source})
    return True


def _unload(holder: dict[str, object]) -> None:
    script = holder.pop("script", None)
    session = holder.pop("session", None)
    if script is not None:
        try:
            script.unload()
        except Exception:
            pass
    if session is not None:
        try:
            session.detach()
        except Exception:
            pass


def _adb_message(err: BaseException) -> str:
    text = str(err).strip()
    lower = text.lower()
    if "unauthorized" in lower:
        return "请在手机上允许 USB 调试"
    if "offline" in lower:
        return "设备离线"
    if "no devices" in lower or "no emulators" in lower or "cannot find any android device" in lower:
        return _NO_DEVICE
    if isinstance(err, (TimeoutError, socket.timeout)) or "timeout" in lower:
        return "查询超时，请确认手机已解锁并允许 USB 调试"
    if text:
        return text.splitlines()[0]
    return "查询手机失败"


def _frida_message(err: BaseException) -> str:
    text = str(err).strip()
    lower = text.lower()
    if isinstance(err, ImportError) or "no module named" in lower and "frida" in lower:
        return _FRIDA_MISSING
    if "major version" in lower or "protocol mismatch" in lower or "unexpected server version" in lower:
        return "frida 版本与手机上的 frida-server 不一致"
    if (
        "unable to connect" in lower
        or "unable to communicate" in lower
        or "connection refused" in lower
        or "connection closed" in lower
        or "server closed" in lower
    ):
        return _FRIDA_DOWN
    if "timeout" in lower or "timed out" in lower:
        return "连接 frida-server 超时，请确认手机上已启动且版本一致"
    if text:
        return text.splitlines()[0]
    return "挂钩失败"
