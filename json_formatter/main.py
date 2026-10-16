"""JSON 格式化窗口。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customtkinter as ctk
from tkinter import filedialog, messagebox

from json_formatter.formatter import FormatError, format_json, minify_json

FONT = ("Microsoft YaHei UI", 13)
FONT_SM = ("Microsoft YaHei UI", 12)
FONT_TITLE = ("Microsoft YaHei UI", 20)
FONT_CODE = ("Consolas", 13)
STATUS_OK = ("gray20", "gray80")
STATUS_ERROR = ("#c62828", "#ef9a9a")


def quoted_content_span(line: str, column: int) -> tuple[int, int] | None:
    """双击落在当前行的 JSON 字符串上时，返回引号内内容的半开区间。"""
    if column < 0:
        return None
    quotes: list[int] = []
    for index, char in enumerate(line):
        if char != '"':
            continue
        slashes = 0
        cursor = index - 1
        while cursor >= 0 and line[cursor] == "\\":
            slashes += 1
            cursor -= 1
        if slashes % 2 == 0:
            quotes.append(index)
    for open_at, close_at in zip(quotes[0::2], quotes[1::2]):
        if open_at <= column <= close_at:
            return open_at + 1, close_at
    return None


def _select_quoted_on_double_click(event):
    widget = event.widget
    line_no, column = widget.index(f"@{event.x},{event.y}").split(".")
    line = widget.get(f"{line_no}.0", f"{line_no}.end")
    span = quoted_content_span(line, int(column))
    if span is None:
        return None
    start, end = span
    widget.tag_remove("sel", "1.0", "end")
    widget.tag_add("sel", f"{line_no}.{start}", f"{line_no}.{end}")
    widget.mark_set("insert", f"{line_no}.{end}")
    return "break"


class App(ctk.CTkFrame):
    def __init__(self, master, on_back=None) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self._on_back = on_back

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build()

    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        if self._on_back is not None:
            ctk.CTkButton(header, text="返回", width=72, command=self._on_back).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(header, text="JSON 格式化", font=FONT_TITLE).pack(side="left")

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        ctk.CTkButton(tools, text="打开文件", width=100, command=self._open_file).pack(side="left")
        ctk.CTkButton(tools, text="格式化", width=88, command=self._format).pack(side="left", padx=(8, 0))
        ctk.CTkButton(tools, text="压缩为一行", width=110, command=self._minify).pack(side="left", padx=(8, 0))
        ctk.CTkButton(tools, text="复制结果", width=100, command=self._copy_result).pack(side="left", padx=(8, 0))
        ctk.CTkButton(tools, text="保存", width=72, command=self._save_file).pack(side="left", padx=(8, 0))

        indent_box = ctk.CTkFrame(tools, fg_color="transparent")
        indent_box.pack(side="right")
        ctk.CTkLabel(indent_box, text="缩进", font=FONT).pack(side="left", padx=(0, 8))
        self.indent_switch = ctk.CTkSegmentedButton(indent_box, values=["2 空格", "4 空格"], font=FONT_SM)
        self.indent_switch.set("2 空格")
        self.indent_switch.pack(side="left")

        editors = ctk.CTkFrame(self, fg_color="transparent")
        editors.grid(row=2, column=0, sticky="nsew", padx=20, pady=4)
        editors.grid_columnconfigure(0, weight=1)
        editors.grid_columnconfigure(1, weight=1)
        editors.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(editors, text="输入", font=FONT, anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(editors, text="结果", font=FONT, anchor="w").grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=(0, 6))
        self.input_box = ctk.CTkTextbox(editors, font=FONT_CODE, wrap="none", undo=True)
        self.input_box.grid(row=1, column=0, sticky="nsew")
        self.output_box = ctk.CTkTextbox(editors, font=FONT_CODE, wrap="none", undo=True)
        self.output_box.grid(row=1, column=1, sticky="nsew", padx=(12, 0))
        self.input_box.bind("<Double-Button-1>", _select_quoted_on_double_click)
        self.output_box.bind("<Double-Button-1>", _select_quoted_on_double_click)

        self.status_label = ctk.CTkLabel(self, text="粘贴 JSON，或打开文件后格式化", font=FONT_SM, anchor="w")
        self.status_label.grid(row=3, column=0, sticky="ew", padx=20, pady=(8, 16))

    def _open_file(self) -> None:
        chosen = filedialog.askopenfilename(
            title="打开 JSON",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if not chosen:
            return
        path = Path(chosen)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self._set_status("文件不是 UTF-8 编码，无法打开。", error=True)
            return
        except OSError as err:
            self._set_status(f"无法读取文件：{err}", error=True)
            return
        self._set_text(self.input_box, text)
        self._set_text(self.output_box, "")
        self._set_status(f"已打开 {path.name}")

    def _format(self) -> None:
        self._render(format_json, self._indent(), success="格式化完成")

    def _minify(self) -> None:
        self._render(minify_json, success="已压缩为一行")

    def _render(self, convert, *args, success: str) -> None:
        try:
            result = convert(self._text(self.input_box), *args)
        except FormatError as err:
            self._set_status(str(err), error=True)
            return
        self._set_text(self.output_box, result)
        self._set_status(success)

    def _copy_result(self) -> None:
        text = self._text(self.output_box)
        if not text.strip():
            self._set_status("没有可复制的内容", error=True)
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("已复制")

    def _save_file(self) -> None:
        text = self._text(self.output_box)
        if not text.strip():
            messagebox.showwarning("提示", "请先格式化，右侧还没有结果。")
            return
        chosen = filedialog.asksaveasfilename(
            title="保存 JSON",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if not chosen:
            return
        path = Path(chosen)
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as err:
            self._set_status(f"无法保存文件：{err}", error=True)
            return
        self._set_status(f"已保存 {path.name}")

    def _indent(self) -> int:
        return 4 if str(self.indent_switch.get()).startswith("4") else 2

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.configure(text=message, text_color=STATUS_ERROR if error else STATUS_OK)

    @staticmethod
    def _text(widget: ctk.CTkTextbox) -> str:
        return widget.get("1.0", "end-1c")

    @staticmethod
    def _set_text(widget: ctk.CTkTextbox, text: str) -> None:
        widget.configure(autoseparators=False)
        try:
            widget.edit_separator()
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            widget.edit_separator()
        finally:
            widget.configure(autoseparators=True)


def main() -> None:
    root = ctk.CTk()
    root.title("JSON 格式化")
    root.geometry("980x680")
    root.minsize(860, 560)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)
    App(root).grid(row=0, column=0, sticky="nsew")
    root.mainloop()


if __name__ == "__main__":
    main()
