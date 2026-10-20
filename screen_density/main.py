"""屏幕密度查看窗口。"""

from __future__ import annotations

import sys
import threading
import tkinter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customtkinter as ctk

from screen_density.density import Device, ProbeResult, ScreenDensity, device_label, probe

FONT = ("Microsoft YaHei UI", 13)
FONT_SM = ("Microsoft YaHei UI", 12)
FONT_TITLE = ("Microsoft YaHei UI", 20)
FONT_CODE = ("Consolas", 13)
FONT_RESULT = ("Microsoft YaHei UI", 36)
STATUS_OK = ("gray20", "gray80")
STATUS_ERROR = ("#c62828", "#ef9a9a")
VALUE_COLOR = ("gray10", "gray90")
EMPTY_COLOR = ("gray50", "gray60")
_FIELDS = (
    ("qualifier", "目录名", False),
    ("dpi", "当前密度", True),
    ("physical_dpi", "物理密度", False),
    ("scale", "倍数", False),
    ("size", "分辨率", False),
    ("dp", "宽高 dp", False),
    ("model", "型号", False),
    ("serial", "设备 ID", True),
)


class App(ctk.CTkFrame):
    def __init__(self, master, on_back=None) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self._on_back = on_back
        self._token = 0
        self._serial: str | None = None
        self._syncing = False
        self._arm_job = None
        self._by_label: dict[str, str] = {}
        self._values = {"short": "", **{key: "" for key, _title, _copy in _FIELDS}}
        self._labels: dict[str, ctk.CTkLabel] = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()
        self.bind("<Destroy>", self._on_destroy)

    def pause(self) -> None:
        """离开页面时丢弃尚未返回的查询结果。"""
        self._token += 1
        self._cancel_arm()

    def grid(self, *args, **kwargs):
        """显示页面时查询已连接的手机。"""
        super().grid(*args, **kwargs)
        self._arm()

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        self.pause()

    def _arm(self) -> None:
        """等事件循环开始后再查询，避免结果无法写回界面。"""
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
        ctk.CTkLabel(header, text="屏幕密度", font=FONT_TITLE).pack(side="left")
        self.refresh_button = ctk.CTkButton(header, text="刷新", width=72, command=self.refresh)
        self.refresh_button.pack(side="right")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=20, pady=4)
        body.grid_columnconfigure(0, weight=1)

        device = self._card(body, 0, "手机")
        device_row = ctk.CTkFrame(device, fg_color="transparent")
        device_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 14))
        device_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(device_row, text="设备", font=FONT, anchor="w", width=88).grid(row=0, column=0, sticky="w")
        self.device_name = ctk.CTkLabel(device_row, text="未连接", font=FONT_CODE, anchor="w", text_color=EMPTY_COLOR)
        self.device_name.grid(row=0, column=1, sticky="ew")
        self.device_menu = ctk.CTkOptionMenu(
            device_row,
            values=["未连接"],
            font=FONT_SM,
            dropdown_font=FONT_SM,
            command=self._on_device,
            width=280,
        )
        self.device_menu.grid(row=0, column=1, sticky="ew")
        self.device_menu.grid_remove()

        result = self._card(body, 1, "结果")
        summary = ctk.CTkFrame(result, fg_color="transparent")
        summary.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 4))
        summary.grid_columnconfigure(0, weight=1)
        self.short_label = ctk.CTkLabel(summary, text="—", font=FONT_RESULT, anchor="w", text_color=EMPTY_COLOR)
        self.short_label.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(summary, text="复制", width=64, height=28, command=lambda: self._copy("short")).grid(
            row=0, column=1, sticky="e"
        )

        lines = ctk.CTkFrame(result, fg_color="transparent")
        lines.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 12))
        lines.grid_columnconfigure(1, weight=1)
        for index, (key, title, copy) in enumerate(_FIELDS):
            bottom = index == len(_FIELDS) - 1
            pady = (0, 8 if bottom else 4)
            ctk.CTkLabel(lines, text=title, font=FONT, anchor="w", width=88).grid(row=index, column=0, sticky="w", pady=pady)
            label = ctk.CTkLabel(lines, text="—", font=FONT_CODE, anchor="w", text_color=EMPTY_COLOR)
            label.grid(row=index, column=1, sticky="ew", padx=8, pady=pady)
            self._labels[key] = label
            if copy:
                ctk.CTkButton(lines, text="复制", width=64, height=28, command=lambda name=key: self._copy(name)).grid(
                    row=index, column=2, pady=pady
                )

        self.status_label = ctk.CTkLabel(self, text="连接手机后自动查询", font=FONT_SM, anchor="w")
        self.status_label.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 16))

    def _card(self, parent, row: int, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, corner_radius=12)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=title, font=FONT, anchor="w").grid(row=0, column=0, sticky="w", padx=16, pady=(12, 4))
        return card

    def refresh(self) -> None:
        self._token += 1
        token = self._token
        serial = self._serial
        self.refresh_button.configure(state="disabled")
        self._clear_results()
        self._set_status("正在查询…")
        threading.Thread(target=self._query, args=(token, serial), daemon=True).start()

    def _query(self, token: int, serial: str | None) -> None:
        result = probe(serial)
        try:
            self.after(0, lambda token=token, result=result: self._apply(token, result))
        except (tkinter.TclError, RuntimeError):
            return

    def _apply(self, token: int, result: ProbeResult) -> None:
        if token != self._token:
            return
        try:
            if not self.winfo_exists():
                return
        except tkinter.TclError:
            return
        self.refresh_button.configure(state="normal")
        if result.screen is not None:
            self._serial = result.screen.serial
        elif result.devices and all(device.serial != self._serial for device in result.devices):
            self._serial = None
        self._show_devices(result.devices)
        if result.screen is None:
            self._set_status(result.message, error=result.error)
            return
        self._show_screen(result.screen)
        self._set_status(result.message, error=result.error)

    def _on_device(self, label: str) -> None:
        if self._syncing:
            return
        serial = self._by_label.get(label)
        if not serial or serial == self._serial:
            return
        self._serial = serial
        self.refresh()

    def _show_devices(self, devices: tuple[Device, ...]) -> None:
        pairs = _device_choices(devices)
        self._by_label = {label: serial for label, serial in pairs}
        serials = {serial for _label, serial in pairs}
        if pairs and self._serial not in serials:
            self._serial = pairs[0][1]
        if len(pairs) <= 1:
            self.device_menu.grid_remove()
            text = pairs[0][0] if pairs else "未连接"
            self.device_name.configure(text=text, text_color=VALUE_COLOR if pairs else EMPTY_COLOR)
            self.device_name.grid(row=0, column=1, sticky="ew")
            return
        self.device_name.grid_remove()
        labels = [label for label, _serial in pairs]
        selected = next((label for label, serial in pairs if serial == self._serial), labels[0])
        self._syncing = True
        try:
            self.device_menu.configure(values=labels)
            self.device_menu.set(selected)
        finally:
            self._syncing = False
        self.device_menu.grid(row=0, column=1, sticky="ew")

    def _show_screen(self, screen: ScreenDensity) -> None:
        self._set_value("short", screen.short, prominent=True)
        self._set_value("qualifier", screen.qualifier)
        self._set_value("dpi", f"{screen.current_dpi} dpi")
        self._set_value("physical_dpi", f"{screen.physical_dpi} dpi")
        self._set_value("scale", screen.scale)
        self._set_value("size", screen.size_text)
        self._set_value("dp", screen.dp_text)
        self._set_value("model", screen.model)
        self._set_value("serial", screen.serial)

    def _clear_results(self) -> None:
        self._set_value("short", "", prominent=True)
        for key, _title, _copy in _FIELDS:
            self._set_value(key, "")

    def _copy(self, key: str) -> None:
        text = self._values.get(key, "")
        if not text:
            self._set_status("没有可复制的内容", error=True)
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("已复制")

    def _set_value(self, key: str, text: str, *, prominent: bool = False) -> None:
        self._values[key] = text
        label = self.short_label if prominent else self._labels[key]
        if text:
            label.configure(text=text, text_color=VALUE_COLOR)
            return
        label.configure(text="—", text_color=EMPTY_COLOR)

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.configure(text=message, text_color=STATUS_ERROR if error else STATUS_OK)


def _device_choices(devices: tuple[Device, ...]) -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []
    seen: set[str] = set()
    for device in devices:
        label = device_label(device)
        if label in seen:
            label = f"{label} {len(seen) + 1}"
        seen.add(label)
        choices.append((label, device.serial))
    return choices


def main() -> None:
    root = ctk.CTk()
    root.title("屏幕密度")
    root.geometry("980x680")
    root.minsize(860, 560)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)
    App(root).grid(row=0, column=0, sticky="nsew")
    root.mainloop()


if __name__ == "__main__":
    main()
