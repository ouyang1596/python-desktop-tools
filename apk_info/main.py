"""APK 信息窗口。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customtkinter as ctk
import tkinter
from tkinter import filedialog
from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD

from apk_info.reader import ApkError, ApkInfo, format_size, read_apk

FONT = ("Microsoft YaHei UI", 13)
FONT_SM = ("Microsoft YaHei UI", 12)
FONT_TITLE = ("Microsoft YaHei UI", 20)
FONT_CODE = ("Consolas", 13)
STATUS_OK = ("gray20", "gray80")
STATUS_ERROR = ("#c62828", "#ef9a9a")
VALUE_COLOR = ("gray10", "gray90")
EMPTY_COLOR = ("gray50", "gray60")
_FIELDS = (
    ("label", "应用名"),
    ("package", "包名"),
    ("version_name", "versionName"),
    ("version_code", "versionCode"),
    ("min_sdk", "minSdk"),
    ("target_sdk", "targetSdk"),
    ("abi", "ABI"),
    ("size", "大小"),
)


class App(ctk.CTkFrame):
    def __init__(self, master, on_back=None) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self._on_back = on_back
        self._token = 0
        self._values = {key: "" for key, _title in _FIELDS}
        self._labels: dict[str, ctk.CTkLabel] = {}
        self._permission_text = ""
        self._drag_inside = 0
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._build()

    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        if self._on_back is not None:
            ctk.CTkButton(header, text="返回", width=72, command=self._on_back).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(header, text="APK 信息", font=FONT_TITLE).pack(side="left")

        source = self._card(self, 1, "文件")
        tools = ctk.CTkFrame(source, fg_color="transparent")
        tools.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 4))
        ctk.CTkButton(tools, text="打开 APK", width=110, command=self._open_file).pack(side="left")

        self.drop_zone = ctk.CTkFrame(source, height=72, corner_radius=8)
        self.drop_zone.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 16))
        self.drop_zone.grid_propagate(False)
        self.drop_zone.grid_columnconfigure(0, weight=1)
        self.drop_zone.grid_rowconfigure(0, weight=1)
        self.drop_label = ctk.CTkLabel(self.drop_zone, text="把 apk 拖到这里", font=FONT, text_color=EMPTY_COLOR)
        self.drop_label.grid(row=0, column=0)
        self._drop_border_width = self.drop_zone.cget("border_width")
        self._drop_border_color = self.drop_zone.cget("border_color")
        self._enable_file_drop()

        result = self._card(self, 2, "信息")
        result.grid_columnconfigure(0, weight=1)
        lines = ctk.CTkFrame(result, fg_color="transparent")
        lines.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))
        lines.grid_columnconfigure(1, weight=1)
        for index, (key, title) in enumerate(_FIELDS):
            bottom = index == len(_FIELDS) - 1
            ctk.CTkLabel(lines, text=title, font=FONT, anchor="w", width=120).grid(
                row=index, column=0, sticky="w", pady=(0, 8 if bottom else 4)
            )
            label = ctk.CTkLabel(lines, text="—", font=FONT_CODE, anchor="w", text_color=EMPTY_COLOR)
            label.grid(row=index, column=1, sticky="ew", padx=8, pady=(0, 8 if bottom else 4))
            ctk.CTkButton(lines, text="复制", width=64, height=28, command=lambda name=key: self._copy(name)).grid(
                row=index, column=2, pady=(0, 8 if bottom else 4)
            )
            self._labels[key] = label

        permissions = self._card(self, 3, "", sticky="nsew")
        permissions.grid_rowconfigure(1, weight=1)
        title_row = ctk.CTkFrame(permissions, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        ctk.CTkLabel(title_row, text="权限", font=FONT, anchor="w").pack(side="left")
        ctk.CTkButton(title_row, text="复制", width=64, height=28, command=self._copy_permissions).pack(side="right")
        self.permission_box = ctk.CTkTextbox(permissions, font=FONT_CODE, wrap="none", height=140)
        self.permission_box.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self._set_permissions("")

        self.status_label = ctk.CTkLabel(self, text="打开或拖入一个 apk", font=FONT_SM, anchor="w")
        self.status_label.grid(row=4, column=0, sticky="ew", padx=20, pady=(8, 16))

    def _card(self, parent, row: int, title: str, sticky: str = "ew") -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, corner_radius=12)
        card.grid(row=row, column=0, sticky=sticky, padx=20, pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)
        if title:
            ctk.CTkLabel(card, text=title, font=FONT, anchor="w").grid(row=0, column=0, sticky="w", padx=16, pady=(12, 4))
        return card

    def _open_file(self) -> None:
        chosen = filedialog.askopenfilename(
            title="打开 APK",
            filetypes=[("APK", "*.apk"), ("所有文件", "*.*")],
        )
        if not chosen:
            return
        self._load(Path(chosen))

    def _load(self, path: Path) -> None:
        self._token += 1
        token = self._token
        self._clear_results()
        self._set_status(f"正在读取 {path.name}")
        threading.Thread(target=self._read, args=(token, path), daemon=True).start()

    def _read(self, token: int, path: Path) -> None:
        info: ApkInfo | None = None
        error = ""
        try:
            info = read_apk(path)
        except ApkError as err:
            error = str(err)
        try:
            self.after(0, lambda token=token, info=info, error=error, name=path.name: self._apply(token, info, error, name))
        except (tkinter.TclError, RuntimeError):
            return

    def _apply(self, token: int, info: ApkInfo | None, error: str, name: str) -> None:
        if token != self._token:
            return
        try:
            if not self.winfo_exists():
                return
        except tkinter.TclError:
            return
        if error or info is None:
            self._clear_results()
            self._set_status(error or "没有读到包名", error=True)
            return
        self._set_value("label", info.label)
        self._set_value("package", info.package)
        self._set_value("version_name", info.version_name)
        self._set_value("version_code", info.version_code)
        self._set_value("min_sdk", info.min_sdk)
        self._set_value("target_sdk", info.target_sdk)
        self._set_value("abi", "、".join(info.abis) if info.abis else "无原生库")
        self._set_value("size", format_size(info.size))
        lines = []
        for item in info.permissions:
            lines.append(f"{item.name}（API 23+）" if item.from_sdk_23 else item.name)
        self._set_permissions("\n".join(lines) if lines else "无")
        self._set_status(f"已读取 {name}")

    def _copy(self, key: str) -> None:
        text = self._values[key]
        if not text:
            self._set_status("没有可复制的内容", error=True)
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("已复制")

    def _copy_permissions(self) -> None:
        text = self._permission_text
        if not text:
            self._set_status("没有可复制的内容", error=True)
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("已复制")

    def _clear_results(self) -> None:
        for key in self._values:
            self._set_value(key, "")
        self._set_permissions("")

    def _set_value(self, key: str, text: str) -> None:
        self._values[key] = text
        label = self._labels[key]
        if text:
            label.configure(text=text, text_color=VALUE_COLOR)
            return
        label.configure(text="—", text_color=EMPTY_COLOR)

    def _set_permissions(self, text: str) -> None:
        self._permission_text = text
        self.permission_box.configure(state="normal")
        self.permission_box.delete("1.0", "end")
        if text:
            self.permission_box.insert("1.0", text)
        self.permission_box.configure(state="disabled")

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.configure(text=message, text_color=STATUS_ERROR if error else STATUS_OK)

    def _enable_file_drop(self) -> None:
        TkinterDnD._require(self.winfo_toplevel())
        for widget in self._drop_surfaces():
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropEnter>>", self._on_drop_enter)
            widget.dnd_bind("<<DropPosition>>", self._on_drop_position)
            widget.dnd_bind("<<DropLeave>>", self._on_drop_leave)
            widget.dnd_bind("<<Drop>>", self._on_drop)

    def _drop_surfaces(self):
        zone = self.drop_zone
        label = self.drop_label
        surfaces = [
            zone,
            getattr(zone, "_canvas", None),
            label,
            getattr(label, "_canvas", None),
            getattr(label, "_label", None),
        ]
        return [widget for widget in surfaces if widget is not None]

    def _on_drop_enter(self, _event):
        self._drag_inside += 1
        self._set_drop_highlight(True)
        return COPY

    def _on_drop_position(self, _event):
        return COPY

    def _on_drop_leave(self, _event) -> None:
        self._drag_inside = max(0, self._drag_inside - 1)
        if self._drag_inside == 0:
            self._set_drop_highlight(False)

    def _on_drop(self, event):
        self._drag_inside = 0
        self._set_drop_highlight(False)
        try:
            paths = self.tk.splitlist(event.data)
        except tkinter.TclError:
            paths = (event.data,) if event.data else ()
        apks = [Path(path) for path in paths if Path(path).suffix.lower() == ".apk" and Path(path).is_file()]
        if not apks:
            self._set_status("请选择 apk 文件", error=True)
            return COPY
        self._load(apks[0])
        return COPY

    def _set_drop_highlight(self, active: bool) -> None:
        if active:
            self.drop_zone.configure(border_width=2, border_color=("#1f6aa5", "#3b8ed0"))
            return
        self.drop_zone.configure(border_width=self._drop_border_width, border_color=self._drop_border_color)


def main() -> None:
    root = ctk.CTk()
    root.title("APK 信息")
    root.geometry("980x820")
    root.minsize(860, 720)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)
    App(root).grid(row=0, column=0, sticky="nsew")
    root.mainloop()


if __name__ == "__main__":
    main()
