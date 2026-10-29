"""Frida 方法调用堆栈窗口。"""

from __future__ import annotations

import sys
import threading
import tkinter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customtkinter as ctk

from screen_density.density import Device, device_label

from frida_trace.tracer import AppInfo, TraceError, Tracer, format_hit, list_apps, list_devices, save_trace_log

FONT = ("Microsoft YaHei UI", 13)
FONT_SM = ("Microsoft YaHei UI", 12)
FONT_TITLE = ("Microsoft YaHei UI", 20)
FONT_CODE = ("Consolas", 13)
STATUS_OK = ("gray20", "gray80")
STATUS_ERROR = ("#c62828", "#ef9a9a")
VALUE_COLOR = ("gray10", "gray90")
EMPTY_COLOR = ("gray50", "gray60")
_NO_PHONE = "未连接"
_NO_APP = "没有正在运行的应用"
_LIST_ROWS = 12


def _widget_inside(widget, parent) -> bool:
    current = widget
    while current is not None:
        if current == parent:
            return True
        current = getattr(current, "master", None)
    return False


class AppMenu(ctk.CTkFrame):
    """可输入的应用框。下拉是列表而不是系统菜单，能用滚轮滑动。"""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self._values: list[str] = [_NO_APP]
        self._popup: tkinter.Toplevel | None = None
        self._listbox: tkinter.Listbox | None = None
        self._click_id: str | None = None
        self.entry = ctk.CTkEntry(self, font=FONT_CODE, height=32)
        self.entry.grid(row=0, column=0, sticky="ew")
        self._button = ctk.CTkButton(self, text="▼", width=32, height=32, font=FONT, command=self._toggle)
        self._button.grid(row=0, column=1, padx=(6, 0))

    def get(self) -> str:
        return self.entry.get()

    def set(self, value: str) -> None:
        self.entry.delete(0, "end")
        if value:
            self.entry.insert(0, value)

    def configure(self, **kwargs):
        values = kwargs.pop("values", None)
        if values is not None:
            self._values = [str(item) for item in values] or [_NO_APP]
            self.close()
        if kwargs:
            super().configure(**kwargs)

    def close(self) -> None:
        self._unbind_click()
        popup = self._popup
        self._popup = None
        self._listbox = None
        if popup is None:
            return
        try:
            popup.destroy()
        except tkinter.TclError:
            pass

    def _toggle(self) -> None:
        if self._popup is not None:
            self.close()
            return
        self._open()

    def _open(self) -> None:
        self.close()
        values = self._values or [_NO_APP]
        bg, fg, select = self._list_colors()
        popup = tkinter.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        try:
            popup.transient(self.winfo_toplevel())
        except tkinter.TclError:
            pass
        popup.configure(bg=bg)
        visible = min(_LIST_ROWS, len(values))
        listbox = tkinter.Listbox(
            popup,
            font=FONT_SM,
            height=visible,
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            exportselection=False,
            bg=bg,
            fg=fg,
            selectbackground=select,
            selectforeground=fg,
        )
        for value in values:
            listbox.insert("end", value)
        scroll = ctk.CTkScrollbar(popup, command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        listbox.grid(row=0, column=0, sticky="nsew")
        popup.grid_columnconfigure(0, weight=1)
        popup.grid_rowconfigure(0, weight=1)
        if len(values) > visible:
            scroll.grid(row=0, column=1, sticky="ns")
        listbox.bind("<ButtonRelease-1>", self._choose)
        listbox.bind("<MouseWheel>", self._on_wheel)
        listbox.bind("<Escape>", lambda _event: self.close())
        popup.bind("<Escape>", lambda _event: self.close())
        self._popup = popup
        self._listbox = listbox
        self.update_idletasks()
        popup.update_idletasks()
        self._bind_wheel(scroll)
        width = max(self.winfo_width(), popup.winfo_reqwidth(), 280)
        height = max(popup.winfo_reqheight(), listbox.winfo_reqheight() + 4)
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        screen_h = self.winfo_screenheight()
        if y + height > screen_h - 8:
            y = max(0, self.winfo_rooty() - height)
        popup.geometry(f"{width}x{height}+{x}+{y}")
        popup.deiconify()
        popup.lift()
        listbox.focus_set()
        self._click_id = tkinter.Misc.bind_all(self, "<Button-1>", self._on_global_click, add="+")

    def _choose(self, _event=None) -> None:
        listbox = self._listbox
        if listbox is None:
            return
        selected = listbox.curselection()
        if not selected:
            return
        self.set(listbox.get(selected[0]))
        self.close()

    def _on_wheel(self, event) -> str:
        listbox = self._listbox
        if listbox is None:
            return "break"
        steps = int(event.delta / 120) or (1 if event.delta > 0 else -1)
        listbox.yview_scroll(-steps, "units")
        return "break"

    def _bind_wheel(self, widget) -> None:
        widget.bind("<MouseWheel>", self._on_wheel)
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _on_global_click(self, event) -> None:
        popup = self._popup
        if popup is None:
            return
        if _widget_inside(event.widget, popup) or _widget_inside(event.widget, self._button):
            return
        self.close()

    def _unbind_click(self) -> None:
        funcid = self._click_id
        self._click_id = None
        _unbind_all(self, "<Button-1>", funcid)

    def _list_colors(self) -> tuple[str, str, str]:
        theme = ctk.ThemeManager.theme
        bg = self._apply_appearance_mode(theme["CTkFrame"]["fg_color"])
        fg = self._apply_appearance_mode(theme["CTkLabel"]["text_color"])
        select = self._apply_appearance_mode(theme["CTkButton"]["fg_color"])
        return str(bg), str(fg), str(select)


def _unbind_all(widget, sequence: str, funcid: str | None) -> None:
    if not funcid:
        return
    try:
        root = widget.winfo_toplevel()
    except tkinter.TclError:
        return
    tkinter.Misc._unbind(root, ("bind", "all", sequence), funcid)


class App(ctk.CTkFrame):
    def __init__(self, master, on_back=None) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self._on_back = on_back
        self._device_token = 0
        self._app_token = 0
        self._trace_token = 0
        self._tracing = False
        self._syncing = False
        self._arm_job = None
        self._phones: dict[str, Device] = {}
        self._serial: str | None = None
        self._app_infos: list[AppInfo] = []
        self._apps_by_label: dict[str, str] = {}
        self._tracer = Tracer()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._build()
        self.bind("<Destroy>", self._on_destroy)

    def pause(self) -> None:
        """离开页面时停止挂钩，并丢掉尚未返回的查询。"""
        self._device_token += 1
        self._app_token += 1
        self._trace_token += 1
        self._tracing = False
        self._tracer.stop()
        self._set_stop_enabled(False)
        self.app_menu.close()
        self._cancel_arm()

    def grid(self, *args, **kwargs):
        super().grid(*args, **kwargs)
        self._arm()

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        self.pause()

    def _arm(self) -> None:
        self._cancel_arm()
        self._arm_job = self.after(0, self._armed_refresh)

    def _armed_refresh(self) -> None:
        self._arm_job = None
        self.refresh()

    def _cancel_arm(self) -> None:
        job = self._arm_job
        self._arm_job = None
        if job is None:
            return
        try:
            self.after_cancel(job)
        except tkinter.TclError:
            pass

    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        if self._on_back is not None:
            ctk.CTkButton(header, text="返回", width=72, command=self._on_back).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(header, text="Frida", font=FONT_TITLE).pack(side="left")
        ctk.CTkButton(header, text="刷新", width=72, command=self.refresh).pack(side="right")

        phone = self._card(self, 1, "手机")
        row = ctk.CTkFrame(phone, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text="设备", font=FONT, anchor="w", width=72).grid(row=0, column=0, sticky="w")
        self.phone_menu = ctk.CTkOptionMenu(
            row,
            values=[_NO_PHONE],
            font=FONT,
            dropdown_font=FONT_SM,
            command=self._on_phone,
        )
        self.phone_menu.set(_NO_PHONE)
        self.phone_menu.grid(row=0, column=1, sticky="ew")

        target = self._card(self, 2, "目标")
        form = ctk.CTkFrame(target, fg_color="transparent")
        form.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        form.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(form, text="应用", font=FONT, anchor="w", width=72).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.app_menu = AppMenu(form)
        self.app_menu.set("")
        self.app_menu.grid(row=0, column=1, sticky="ew", pady=(0, 4))
        ctk.CTkLabel(
            form,
            text="填写包名，例如 com.example.app；不要只填应用名",
            font=FONT_SM,
            anchor="w",
            text_color=EMPTY_COLOR,
        ).grid(row=1, column=1, sticky="w", pady=(0, 8))
        ctk.CTkLabel(form, text="类名", font=FONT, anchor="w", width=72).grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.class_entry = ctk.CTkEntry(form, font=FONT_CODE, placeholder_text="com.example.Foo")
        self.class_entry.grid(row=2, column=1, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(form, text="方法名", font=FONT, anchor="w", width=72).grid(row=3, column=0, sticky="w")
        self.method_entry = ctk.CTkEntry(form, font=FONT_CODE, placeholder_text="onCreate、initView 或 <init>")
        self.method_entry.grid(row=3, column=1, sticky="ew")

        buttons = ctk.CTkFrame(target, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="w", padx=16, pady=(4, 16))
        ctk.CTkButton(buttons, text="开始", width=88, command=self._start).pack(side="left")
        self.stop_button = ctk.CTkButton(buttons, text="停止", width=88, command=self._stop_trace)
        self.stop_button.pack(side="left", padx=(8, 0))
        ctk.CTkButton(buttons, text="清空", width=88, command=self._clear_log).pack(side="left", padx=(8, 0))
        self._set_stop_enabled(False)

        log_card = self._card(self, 3, "调用", sticky="nsew")
        log_card.grid_rowconfigure(1, weight=1)
        self.log_box = ctk.CTkTextbox(log_card, font=FONT_CODE, wrap="none")
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))

        self.status_label = ctk.CTkLabel(self, text="连接手机后选择应用，填写类名和方法名", font=FONT_SM, anchor="w")
        self.status_label.grid(row=4, column=0, sticky="ew", padx=20, pady=(8, 16))

    def _card(self, parent, row: int, title: str, sticky: str = "ew") -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, corner_radius=12)
        card.grid(row=row, column=0, sticky=sticky, padx=20, pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=title, font=FONT, anchor="w").grid(row=0, column=0, sticky="w", padx=16, pady=(12, 4))
        return card

    def refresh(self) -> None:
        self._device_token += 1
        token = self._device_token
        if not self._tracing:
            self._set_status("正在查找手机")
        threading.Thread(target=self._load_devices, args=(token,), daemon=True).start()

    def _load_devices(self, token: int) -> None:
        devices: list[Device] = []
        error = ""
        try:
            devices = list_devices()
        except TraceError as err:
            error = str(err)
        try:
            self.after(0, lambda: self._apply_devices(token, devices, error))
        except (tkinter.TclError, RuntimeError):
            return

    def _apply_devices(self, token: int, devices: list[Device], error: str) -> None:
        if token != self._device_token or not self._alive():
            return
        self._show_phones(devices)
        if error:
            self._fill_apps([])
            self._set_status(error, error=True)
            return
        if not devices:
            self._fill_apps([])
            self._set_status("没有检测到手机，请打开 USB 调试并连接", error=True)
            return
        phone = self._selected_phone()
        if phone is None or phone.state != "device":
            self._fill_apps([])
            self._set_status(_phone_message(phone), error=True)
            return
        self._fetch_apps(phone.serial)

    def _show_phones(self, devices: list[Device]) -> None:
        labels: list[str] = []
        phones: dict[str, Device] = {}
        for device in devices:
            label = device_label(device)
            if label in phones:
                label = f"{label} ({device.serial})"
            phones[label] = device
            labels.append(label)
        self._phones = phones
        if not labels:
            labels = [_NO_PHONE]
        current = self.phone_menu.get()
        selected = current if current in phones else labels[0]
        if selected in phones:
            self._serial = phones[selected].serial
        elif not phones:
            self._serial = None
        self._syncing = True
        try:
            self.phone_menu.configure(values=labels)
            self.phone_menu.set(selected)
        finally:
            self._syncing = False

    def _on_phone(self, label: str) -> None:
        if self._syncing:
            return
        phone = self._phones.get(label)
        serial = phone.serial if phone is not None else None
        if serial == self._serial:
            return
        self._serial = serial
        if self._tracing:
            self._stop_trace()
        if phone is None or phone.state != "device":
            self._fill_apps([])
            self._set_status(_phone_message(phone), error=phone is not None)
            return
        self._fetch_apps(phone.serial)

    def _fetch_apps(self, serial: str) -> None:
        self._app_token += 1
        token = self._app_token
        if not self._tracing:
            self._set_status("正在读取应用")
        threading.Thread(target=self._load_apps, args=(token, serial), daemon=True).start()

    def _load_apps(self, token: int, serial: str) -> None:
        apps: list[AppInfo] = []
        error = ""
        try:
            apps = list_apps(serial)
        except TraceError as err:
            error = str(err)
        try:
            self.after(0, lambda: self._apply_apps(token, serial, apps, error))
        except (tkinter.TclError, RuntimeError):
            return

    def _apply_apps(self, token: int, serial: str, apps: list[AppInfo], error: str) -> None:
        if token != self._app_token or not self._alive():
            return
        phone = self._selected_phone()
        if phone is None or phone.serial != serial:
            return
        self._fill_apps(apps)
        if error:
            self._set_status(error, error=True)
            return
        if self._tracing:
            return
        if apps:
            self._set_status(f"已列出 {len(apps)} 个正在运行的应用")
            return
        self._set_status("没有正在运行的应用。请先打开目标应用，再点刷新")

    def _fill_apps(self, apps: list[AppInfo]) -> None:
        current = self.app_menu.get().strip()
        previous = self._resolve_package(current)
        self._app_infos = list(apps)
        labels: list[str] = []
        mapping: dict[str, str] = {}
        for app in apps:
            label = app.label()
            if label in mapping:
                label = f"{label} ({app.pid})"
            mapping[label] = app.package
            labels.append(label)
        self._apps_by_label = mapping
        if not labels:
            labels = [_NO_APP]
        self.app_menu.configure(values=labels)
        if not apps:
            self.app_menu.set("")
            return
        if current and current != _NO_APP:
            resolved = self._resolve_package(current) or previous
            self.app_menu.set(resolved or current)
            return
        self.app_menu.set("")

    def _start(self) -> None:
        phone = self._selected_phone()
        if phone is None:
            self._set_status("没有检测到手机，请打开 USB 调试并连接", error=True)
            return
        if phone.state != "device":
            self._set_status(_phone_message(phone), error=True)
            return
        class_name = self.class_entry.get().strip()
        method_name = self.method_entry.get().strip()
        package = self._package(class_name)
        if not package:
            self._set_status("请从列表选择应用，或填写包名（例如 com.example.app）", error=True)
            return
        if "." not in package:
            guess = self._package_from_class(class_name)
            hint = f"，类名对应包名可能是 {guess}" if guess else ""
            self._set_status(f"「{package}」不是包名，请选列表里的项或填写包名{hint}", error=True)
            return
        if not class_name or not method_name:
            self._set_status("请填写类名和方法名", error=True)
            return
        self.app_menu.set(package)
        self._trace_token += 1
        token = self._trace_token
        self._tracing = True
        self._set_stop_enabled(True)
        self._set_status(f"正在挂钩 {package}")
        self._append_log(f"开始挂钩 {package} / {class_name}.{method_name}\n")
        self._tracer.start(
            phone.serial,
            package,
            class_name,
            method_name,
            lambda payload, token=token: self._on_event(token, payload),
        )

    def _stop_trace(self) -> None:
        self._trace_token += 1
        was = self._tracing
        self._tracing = False
        self._tracer.stop()
        self._set_stop_enabled(False)
        if not was:
            return
        text = self.log_box.get("1.0", "end-1c")
        if not text.strip():
            self._set_status("已停止")
            return
        phone = self._selected_phone()
        package = self._package(self.class_entry.get().strip())
        app = next((item for item in self._app_infos if item.package == package), None)
        try:
            path = save_trace_log(
                text,
                serial=phone.serial if phone else "",
                model=phone.model if phone else "",
                package=package,
                process=app.name if app and app.name else "",
                pid=app.pid if app else 0,
            )
        except (OSError, TraceError) as err:
            detail = str(err).splitlines()[0] if str(err) else "无法保存日志"
            self._set_status(f"已停止，保存日志失败：{detail}", error=True)
            return
        self._set_status(f"已停止，日志已保存到 {path}")

    def _on_event(self, token: int, payload: dict) -> None:
        try:
            self.after(0, lambda: self._apply_event(token, payload))
        except (tkinter.TclError, RuntimeError):
            return

    def _apply_event(self, token: int, payload: dict) -> None:
        if token != self._trace_token or not self._alive():
            return
        kind = payload.get("type")
        if kind == "waiting":
            self._set_status("等待类加载：请打开对应页面；若已打开，类名可能不对")
            return
        if kind == "hint":
            classes = payload.get("classes")
            names = [str(item) for item in classes] if isinstance(classes, list) else []
            if names:
                shown = "、".join(names[:5])
                self._set_status(f"未找到该类。进程里相近的有：{shown}", error=True)
                self._append_log("未找到目标类，相近类名：\n" + "\n".join(names) + "\n\n")
            else:
                self._set_status("未找到该类，且进程里没有相近类名；请确认包名和类名", error=True)
                self._append_log("未找到目标类，进程里没有相近类名\n\n")
            return
        if kind == "ready":
            count = payload.get("count")
            try:
                count_text = str(int(count))
            except (TypeError, ValueError):
                count_text = "1"
            self._set_status(f"已挂钩 {count_text} 个重载，等待方法被调用")
            self._append_log(f"已挂钩 {count_text} 个重载，等待调用…\n\n")
            return
        if kind == "hit":
            self._append_log(format_hit(payload))
            self._set_status("已打印调用堆栈")
            return
        if kind == "error":
            self._trace_token += 1
            self._tracing = False
            self._tracer.stop()
            self._set_stop_enabled(False)
            message = str(payload.get("message") or "挂钩失败").splitlines()[0]
            self._set_status(message, error=True)
            self._append_log(f"失败：{message}\n\n")

    def _clear_log(self) -> None:
        self.log_box.delete("1.0", "end")

    def _append_log(self, text: str) -> None:
        self.log_box.insert("end", text)
        self.log_box.see("end")

    def _package(self, class_name: str = "") -> str:
        text = self.app_menu.get().strip()
        if not text or text == _NO_APP:
            return self._package_from_class(class_name)
        resolved = self._resolve_package(text)
        if resolved:
            return resolved
        if "." in text:
            return text
        return self._package_from_class(class_name) or text

    def _resolve_package(self, text: str) -> str:
        raw = text.strip()
        if not raw or raw == _NO_APP:
            return ""
        mapped = self._apps_by_label.get(raw)
        if mapped:
            return mapped
        lowered = raw.casefold()
        for app in self._app_infos:
            if app.package == raw or app.package.casefold() == lowered:
                return app.package
            if app.name and app.name.casefold() == lowered:
                return app.package
        for label, package in self._apps_by_label.items():
            if label.casefold() == lowered:
                return package
            if "（" in label and label.endswith("）"):
                name = label[label.find("（") + 1 : -1]
                if name.casefold() == lowered:
                    return package
        return ""

    def _package_from_class(self, class_name: str) -> str:
        name = class_name.strip()
        if not name or "." not in name:
            return ""
        matches = [
            app.package
            for app in self._app_infos
            if name == app.package or name.startswith(app.package + ".")
        ]
        if not matches:
            return ""
        matches.sort(key=len, reverse=True)
        return matches[0]

    def _selected_phone(self) -> Device | None:
        return self._phones.get(self.phone_menu.get())

    def _set_stop_enabled(self, enabled: bool) -> None:
        self.stop_button.configure(state="normal" if enabled else "disabled")

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.configure(text=message, text_color=STATUS_ERROR if error else STATUS_OK)

    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except tkinter.TclError:
            return False


def _phone_message(phone: Device | None) -> str:
    if phone is None:
        return "没有检测到手机，请打开 USB 调试并连接"
    if phone.state == "unauthorized":
        return "请在手机上允许 USB 调试"
    if phone.state == "offline":
        return "设备离线"
    if phone.state == "no permissions":
        return "没有 USB 调试权限"
    if phone.state != "device":
        return f"设备不可用：{phone.state}"
    return ""


def main() -> None:
    root = ctk.CTk()
    root.title("Frida")
    root.geometry("980x820")
    root.minsize(860, 720)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)
    App(root).grid(row=0, column=0, sticky="nsew")
    root.mainloop()


if __name__ == "__main__":
    main()
