"""时间戳转换窗口。"""

from __future__ import annotations

import sys
import time
import tkinter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customtkinter as ctk

from timestamp_converter.converter import (
    ConvertError,
    current_clock,
    datetime_to_timestamps,
    timestamp_to_datetimes,
)

FONT = ("Microsoft YaHei UI", 13)
FONT_SM = ("Microsoft YaHei UI", 12)
FONT_TITLE = ("Microsoft YaHei UI", 20)
FONT_CODE = ("Consolas", 13)
STATUS_OK = ("gray20", "gray80")
STATUS_ERROR = ("#c62828", "#ef9a9a")
VALUE_COLOR = ("gray10", "gray90")
EMPTY_COLOR = ("gray50", "gray60")
_UNITS = {"自动": "auto", "秒": "second", "毫秒": "millisecond"}
_ZONES = {"本地": "local", "UTC": "utc"}


class App(ctk.CTkFrame):
    def __init__(self, master, on_back=None) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self._on_back = on_back
        self._job = None
        self._clock_token = 0
        self._values = {
            "clock_local": "",
            "clock_seconds": "",
            "clock_millis": "",
            "stamp_local": "",
            "stamp_utc": "",
            "stamp_iso": "",
            "date_seconds": "",
            "date_millis": "",
        }
        self._labels: dict[str, ctk.CTkLabel] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()
        self.bind("<Destroy>", self._on_destroy)

    def pause(self) -> None:
        """离开页面时停止刷新当前时间。"""
        self._clock_token += 1
        job = self._job
        self._job = None
        if job is None:
            return
        try:
            self.after_cancel(job)
        except tkinter.TclError:
            pass

    def grid(self, *args, **kwargs):
        """显示页面时开始刷新当前时间。"""
        super().grid(*args, **kwargs)
        self._start_clock()

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        self.pause()

    def _start_clock(self) -> None:
        if self._job is not None:
            return
        self._tick()

    def _tick(self) -> None:
        token = self._clock_token
        try:
            if not self.winfo_exists():
                return
            local, seconds, millis = current_clock()
            if token != self._clock_token:
                return
            self._set_value("clock_local", local)
            self._set_value("clock_seconds", seconds)
            self._set_value("clock_millis", millis)
        except tkinter.TclError:
            return
        if token != self._clock_token:
            return
        delay = max(1, int((1.0 - time.time() % 1.0) * 1000))
        self._job = self.after(delay, self._tick)

    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        if self._on_back is not None:
            ctk.CTkButton(header, text="返回", width=72, command=self._on_back).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(header, text="时间戳转换", font=FONT_TITLE).pack(side="left")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=20, pady=4)
        body.grid_columnconfigure(0, weight=1)

        clock = self._card(body, 0, "当前时间")
        self._result_line(
            clock,
            1,
            (("clock_local", "本地时间"), ("clock_seconds", "秒"), ("clock_millis", "毫秒")),
            bottom=True,
        )

        stamp = self._card(body, 1, "时间戳 → 日期")
        stamp_tools = ctk.CTkFrame(stamp, fg_color="transparent")
        stamp_tools.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 4))
        stamp_tools.grid_columnconfigure(0, weight=1)
        self.stamp_entry = ctk.CTkEntry(stamp_tools, font=FONT_CODE, placeholder_text="例如 1700000000")
        self.stamp_entry.grid(row=0, column=0, sticky="ew")
        self.stamp_entry.bind("<Return>", lambda _event: self._convert_stamp())
        self.unit_switch = ctk.CTkSegmentedButton(stamp_tools, values=["自动", "秒", "毫秒"], font=FONT_SM, height=28)
        self.unit_switch.set("自动")
        self.unit_switch.grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(stamp_tools, text="转换", width=72, command=self._convert_stamp).grid(row=0, column=2, padx=(8, 0))
        self._result_line(stamp, 2, (("stamp_local", "本地时间"), ("stamp_utc", "UTC")))
        self._result_line(stamp, 3, (("stamp_iso", "ISO 8601"),), bottom=True)

        date = self._card(body, 2, "日期 → 时间戳")
        date_tools = ctk.CTkFrame(date, fg_color="transparent")
        date_tools.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 4))
        date_tools.grid_columnconfigure(0, weight=1)
        self.date_entry = ctk.CTkEntry(date_tools, font=FONT_CODE, placeholder_text="例如 2026-10-16 14:30:00")
        self.date_entry.grid(row=0, column=0, sticky="ew")
        self.date_entry.bind("<Return>", lambda _event: self._convert_date())
        self.zone_switch = ctk.CTkSegmentedButton(date_tools, values=["本地", "UTC"], font=FONT_SM, height=28)
        self.zone_switch.set("本地")
        self.zone_switch.grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(date_tools, text="转换", width=72, command=self._convert_date).grid(row=0, column=2, padx=(8, 0))
        self._result_line(date, 2, (("date_seconds", "秒"), ("date_millis", "毫秒")), bottom=True)

        self.status_label = ctk.CTkLabel(self, text="输入时间戳或日期时间后转换", font=FONT_SM, anchor="w")
        self.status_label.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 16))

    def _card(self, parent, row: int, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, corner_radius=12)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=title, font=FONT, anchor="w").grid(row=0, column=0, sticky="w", padx=16, pady=(12, 4))
        return card

    def _result_line(self, parent, row: int, items: tuple[tuple[str, str], ...], *, bottom: bool = False) -> None:
        line = ctk.CTkFrame(parent, fg_color="transparent")
        line.grid(row=row, column=0, sticky="ew", padx=16, pady=(2, 12 if bottom else 2))
        for index, (key, title) in enumerate(items):
            col = index * 3
            line.grid_columnconfigure(col + 1, weight=1)
            ctk.CTkLabel(line, text=title, font=FONT, anchor="w").grid(
                row=0, column=col, sticky="w", padx=(12 if index else 0, 0)
            )
            label = ctk.CTkLabel(line, text="—", font=FONT_CODE, anchor="w", text_color=EMPTY_COLOR)
            label.grid(row=0, column=col + 1, sticky="ew", padx=8)
            ctk.CTkButton(line, text="复制", width=64, height=28, command=lambda name=key: self._copy(name)).grid(
                row=0, column=col + 2
            )
            self._labels[key] = label

    def _convert_stamp(self) -> None:
        try:
            result = timestamp_to_datetimes(self.stamp_entry.get(), _UNITS[str(self.unit_switch.get())])
        except ConvertError as err:
            self._set_value("stamp_local", "")
            self._set_value("stamp_utc", "")
            self._set_value("stamp_iso", "")
            self._set_status(str(err), error=True)
            return
        self._set_value("stamp_local", result.local)
        self._set_value("stamp_utc", result.utc)
        self._set_value("stamp_iso", result.iso)
        self._set_status("转换完成")

    def _convert_date(self) -> None:
        try:
            result = datetime_to_timestamps(self.date_entry.get(), _ZONES[str(self.zone_switch.get())])
        except ConvertError as err:
            self._set_value("date_seconds", "")
            self._set_value("date_millis", "")
            self._set_status(str(err), error=True)
            return
        self._set_value("date_seconds", result.seconds)
        self._set_value("date_millis", result.milliseconds)
        if result.embedded_zone:
            self._set_status("转换完成，已使用字符串中的时区")
            return
        self._set_status("转换完成")

    def _copy(self, key: str) -> None:
        text = self._values[key]
        if not text:
            self._set_status("没有可复制的内容", error=True)
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("已复制")

    def _set_value(self, key: str, text: str) -> None:
        self._values[key] = text
        label = self._labels[key]
        if text:
            label.configure(text=text, text_color=VALUE_COLOR)
            return
        label.configure(text="—", text_color=EMPTY_COLOR)

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.configure(text=message, text_color=STATUS_ERROR if error else STATUS_OK)


def main() -> None:
    root = ctk.CTk()
    root.title("时间戳转换")
    root.geometry("980x680")
    root.minsize(860, 560)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)
    App(root).grid(row=0, column=0, sticky="nsew")
    root.mainloop()


if __name__ == "__main__":
    main()
