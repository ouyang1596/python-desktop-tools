"""图片压缩窗口。"""

from __future__ import annotations

import os
import sys
import threading
import tkinter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customtkinter as ctk
import tkinter.ttk as ttk
from tkinter import filedialog, messagebox
from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD

from image_compressor.compressor import (
    FREE_MONTHLY_LIMIT,
    CompressError,
    CompressResult,
    compress_file,
    format_saved,
    format_size,
    query_usage,
)
from image_compressor.config import load_api_key, save_api_key

SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".avif"}
OUTPUT_DIR_NAME = "compressed"
FONT = ("Microsoft YaHei UI", 13)
FONT_SM = ("Microsoft YaHei UI", 12)
FONT_TITLE = ("Microsoft YaHei UI", 20)


class App(ctk.CTkFrame):
    def __init__(self, master, on_back=None) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self._on_back = on_back

        self._rows: dict[str, Path] = {}
        self._seq = 0
        self._running = False
        self._key_visible = False
        self._drag_inside = 0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build()
        self._style_tree()
        self._enable_file_drop()

        saved_key = load_api_key()
        if saved_key:
            self.api_entry.insert(0, saved_key)
            self.after(200, self._refresh_quota)

    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        header.grid_columnconfigure(1, weight=1)

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w")
        if self._on_back is not None:
            ctk.CTkButton(title_box, text="返回", width=72, command=self._on_back).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(title_box, text="图片压缩", font=FONT_TITLE).pack(side="left")
        self.quota_label = ctk.CTkLabel(header, text="本月已压缩：— 次", font=FONT_SM)
        self.quota_label.grid(row=0, column=2, sticky="e", padx=(12, 0))

        ctk.CTkLabel(header, text="API Key", font=FONT).grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.api_entry = ctk.CTkEntry(header, show="*", font=FONT, placeholder_text="TinyPNG API Key")
        self.api_entry.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=(12, 0))
        key_actions = ctk.CTkFrame(header, fg_color="transparent")
        key_actions.grid(row=1, column=2, sticky="e", padx=(8, 0), pady=(12, 0))
        self.toggle_btn = ctk.CTkButton(key_actions, text="显示", width=64, command=self._toggle_key)
        self.toggle_btn.pack(side="left")
        self.save_btn = ctk.CTkButton(key_actions, text="保存", width=64, command=self._save_key)
        self.save_btn.pack(side="left", padx=(8, 0))

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        self.add_btn = ctk.CTkButton(tools, text="添加图片", width=100, command=self._add_files)
        self.add_btn.pack(side="left")
        self.add_folder_btn = ctk.CTkButton(tools, text="添加文件夹", width=110, command=self._add_folder)
        self.add_folder_btn.pack(side="left", padx=(8, 0))
        self.remove_btn = ctk.CTkButton(tools, text="移除选中", width=100, command=self._remove_selected)
        self.remove_btn.pack(side="left", padx=(8, 0))
        self.clear_btn = ctk.CTkButton(tools, text="清空", width=72, command=self._clear_files)
        self.clear_btn.pack(side="left", padx=(8, 0))

        table = ctk.CTkFrame(self)
        self.table = table
        table.grid(row=2, column=0, sticky="nsew", padx=20, pady=4)
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)

        columns = ("name", "status", "original", "compressed", "saved", "note")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="extended")
        headings = (
            ("name", "文件", 280, "w"),
            ("status", "状态", 80, "center"),
            ("original", "原大小", 90, "e"),
            ("compressed", "压缩后", 90, "e"),
            ("saved", "节省", 70, "e"),
            ("note", "说明", 260, "w"),
        )
        for key, title, width, anchor in headings:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor, stretch=key in {"name", "note"})
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self._scroll = scroll
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure("done", foreground="#2e7d32")
        self.tree.tag_configure("fail", foreground="#c62828")
        self.tree.tag_configure("running", foreground="#1f6aa5")
        self.drop_hint = ctk.CTkLabel(
            table,
            text="将图片或文件夹拖到这里，或点击添加图片",
            font=FONT,
            text_color=("gray40", "gray70"),
            fg_color="transparent",
        )
        self.drop_hint.place(relx=0.5, rely=0.5, anchor="center")

        output = ctk.CTkFrame(self, fg_color="transparent")
        output.grid(row=3, column=0, sticky="ew", padx=20, pady=(10, 4))
        output.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(output, text="输出目录", font=FONT).grid(row=0, column=0, sticky="w")
        self.output_entry = ctk.CTkEntry(output, font=FONT, placeholder_text="默认保存到图片旁边的 compressed 文件夹")
        self.output_entry.grid(row=0, column=1, sticky="ew", padx=(12, 8))
        self.browse_btn = ctk.CTkButton(output, text="浏览", width=72, command=self._browse_output)
        self.browse_btn.grid(row=0, column=2)

        self.progress = ctk.CTkProgressBar(self)
        self.progress.grid(row=4, column=0, sticky="ew", padx=20, pady=(8, 4))
        self.progress.set(0)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=5, column=0, sticky="ew", padx=20, pady=(4, 16))
        footer.grid_columnconfigure(1, weight=1)
        self.start_btn = ctk.CTkButton(footer, text="开始压缩", width=140, height=36, command=self._start)
        self.start_btn.grid(row=0, column=0, sticky="w")
        self.status_label = ctk.CTkLabel(footer, text="选择图片后点击开始压缩", font=FONT_SM, anchor="w")
        self.status_label.grid(row=0, column=1, sticky="ew", padx=(12, 0))

    def _style_tree(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        dark = ctk.get_appearance_mode() == "Dark"
        background = "#2b2b2b" if dark else "#ffffff"
        foreground = "#f2f2f2" if dark else "#1a1a1a"
        heading_bg = "#1f538d" if dark else "#1f6aa5"
        style.configure(
            "Treeview",
            background=background,
            fieldbackground=background,
            foreground=foreground,
            rowheight=30,
            font=FONT_SM,
            borderwidth=0,
        )
        style.configure("Treeview.Heading", background=heading_bg, foreground="#ffffff", font=FONT_SM, relief="flat")
        style.map("Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])

    def _enable_file_drop(self) -> None:
        TkinterDnD._require(self.winfo_toplevel())
        self._table_border_width = self.table.cget("border_width")
        self._table_border_color = self.table.cget("border_color")
        for widget in self._drop_surfaces():
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropEnter>>", self._on_drop_enter)
            widget.dnd_bind("<<DropPosition>>", self._on_drop_position)
            widget.dnd_bind("<<DropLeave>>", self._on_drop_leave)
            widget.dnd_bind("<<Drop>>", self._on_drop)

    def _drop_surfaces(self):
        hint = self.drop_hint
        surfaces = [
            self.table,
            getattr(self.table, "_canvas", None),
            self.tree,
            self._scroll,
            hint,
            getattr(hint, "_canvas", None),
            getattr(hint, "_label", None),
        ]
        return [widget for widget in surfaces if widget is not None]

    def _on_drop_enter(self, _event):
        if self._running:
            return REFUSE_DROP
        self._drag_inside += 1
        self._set_drop_highlight(True)
        return COPY

    def _on_drop_position(self, _event):
        if self._running:
            return REFUSE_DROP
        return COPY

    def _on_drop_leave(self, _event) -> None:
        self._drag_inside = max(0, self._drag_inside - 1)
        if self._drag_inside == 0:
            self._set_drop_highlight(False)

    def _on_drop(self, event):
        self._drag_inside = 0
        self._set_drop_highlight(False)
        if self._running:
            return REFUSE_DROP
        try:
            paths = self.tk.splitlist(event.data)
        except tkinter.TclError:
            paths = (event.data,) if event.data else ()
        if paths:
            self._add_paths(paths)
        return COPY

    def _set_drop_highlight(self, active: bool) -> None:
        if active:
            self.table.configure(border_width=2, border_color=("#1f6aa5", "#3b8ed0"))
            return
        self.table.configure(border_width=self._table_border_width, border_color=self._table_border_color)

    def _sync_drop_hint(self) -> None:
        if self._rows:
            self.drop_hint.place_forget()
            return
        self.drop_hint.place(relx=0.5, rely=0.5, anchor="center")
        self.drop_hint.lift()

    def _toggle_key(self) -> None:
        self._key_visible = not self._key_visible
        self.api_entry.configure(show="" if self._key_visible else "*")
        self.toggle_btn.configure(text="隐藏" if self._key_visible else "显示")

    def _save_key(self) -> None:
        key = self.api_entry.get().strip()
        if not key:
            messagebox.showwarning("提示", "请先填写 API Key。")
            return
        save_api_key(key)
        self.status_label.configure(text="API Key 已保存")
        self._refresh_quota()

    def _refresh_quota(self) -> None:
        key = self.api_entry.get().strip()
        if not key or self._running:
            return

        def work() -> None:
            try:
                count = query_usage(key)
            except CompressError as err:
                self._ui(lambda message=str(err): self.status_label.configure(text=message))
                return
            self._ui(lambda value=count: self._set_quota(value))

        threading.Thread(target=work, daemon=True).start()

    def _set_quota(self, count: int | None) -> None:
        if count is None:
            self.quota_label.configure(text="本月已压缩：— 次")
            return
        self.quota_label.configure(text=f"本月已压缩：{count} 次（免费额度 {FREE_MONTHLY_LIMIT}）")

    def _add_files(self) -> None:
        selected = filedialog.askopenfilenames(
            title="选择图片",
            filetypes=[
                ("图片", "*.png;*.jpg;*.jpeg;*.webp;*.avif"),
                ("所有文件", "*.*"),
            ],
        )
        if not selected:
            return
        self._add_paths(selected)

    def _add_folder(self) -> None:
        chosen = filedialog.askdirectory(title="选择文件夹")
        if not chosen:
            return
        self._add_paths([chosen])

    def _add_paths(self, selected) -> None:
        skipped: list[str] = []
        empty_dirs: list[str] = []
        added = False
        known = {path.resolve() for path in self._rows.values()}
        folder_root: Path | None = None
        file_parent: Path | None = None
        for raw in selected:
            path = Path(raw)
            if path.is_dir():
                images = iter_images(path)
                if not images:
                    empty_dirs.append(path.name)
                    continue
                if folder_root is None:
                    folder_root = path
                for image in images:
                    if self._remember_image(image, known):
                        added = True
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                skipped.append(path.name)
                continue
            if file_parent is None:
                file_parent = path.parent
            if self._remember_image(path, known):
                added = True
        if added:
            self._refresh_names()
        self._sync_drop_hint()
        output_root = folder_root or file_parent
        if output_root is not None and not self.output_entry.get().strip():
            self.output_entry.insert(0, str(output_root / OUTPUT_DIR_NAME))
        if empty_dirs and not added and not skipped:
            if len(empty_dirs) == 1:
                messagebox.showwarning("提示", "这个文件夹里没有支持的图片。")
            else:
                messagebox.showwarning("提示", "这些文件夹里没有支持的图片：\n" + "\n".join(empty_dirs))
            return
        if empty_dirs:
            skipped.extend(f"{name}（没有支持的图片）" for name in empty_dirs)
        if skipped:
            messagebox.showwarning("提示", "已跳过不支持的文件：\n" + "\n".join(skipped))

    def _remember_image(self, path: Path, known: set[Path]) -> bool:
        resolved = path.resolve()
        if resolved in known:
            return False
        known.add(resolved)
        self._seq += 1
        iid = f"img-{self._seq}"
        self._rows[iid] = path
        self._insert_row(iid, path)
        return True

    def _remove_selected(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        for iid in selected:
            self.tree.delete(iid)
            self._rows.pop(iid, None)
        self._refresh_names()
        self._sync_drop_hint()

    def _clear_files(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._rows.clear()
        self.progress.set(0)
        self._sync_drop_hint()

    def _browse_output(self) -> None:
        current = self.output_entry.get().strip()
        chosen = filedialog.askdirectory(title="选择输出目录", initialdir=current or None)
        if not chosen:
            return
        self.output_entry.delete(0, "end")
        self.output_entry.insert(0, chosen)

    def _start(self) -> None:
        if self._running:
            return
        key = self.api_entry.get().strip()
        if not key:
            messagebox.showwarning("提示", "请先填写 API Key。")
            return
        output = self.output_entry.get().strip()
        if not output:
            messagebox.showwarning("提示", "请选择输出目录。")
            return
        items = self._ordered_items()
        if not items:
            messagebox.showwarning("提示", "请先添加图片。")
            return

        save_api_key(key)
        out_dir = Path(output)
        self._running = True
        self._set_busy(True)
        self.progress.set(0)
        self.status_label.configure(text=f"准备压缩 {len(items)} 张图片")
        threading.Thread(target=self._run_batch, args=(key, items, out_dir), daemon=True).start()

    def _run_batch(self, key: str, items: list[tuple[str, Path]], out_dir: Path) -> None:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            self._ui(lambda: self._finish(0, 0, f"无法创建输出目录：{err}", out_dir, started=False))
            return

        ok = 0
        fail = 0
        stopped: str | None = None
        total = len(items)
        used: set[str] = set()
        for index, (iid, source) in enumerate(items, start=1):
            dest = allocate_output(out_dir, source, used)
            self._ui(lambda row=iid, path=source, current=index, count=total: self._mark_running(row, path, current, count))
            try:
                result = compress_file(key, source, dest)
            except CompressError as err:
                fail += 1
                self._ui(lambda row=iid, message=str(err): self._mark_failed(row, message))
                if err.stop_batch:
                    stopped = str(err)
                    self._ui(lambda done=index, count=total: self.progress.set(done / count))
                    break
            else:
                ok += 1
                self._ui(lambda row=iid, value=result: self._mark_done(row, value))
            self._ui(lambda done=index, count=total: self.progress.set(done / count))

        self._ui(lambda: self._finish(ok, fail, stopped, out_dir, started=True))

    def _mark_running(self, iid: str, path: Path, index: int, total: int) -> None:
        self._update_row(iid, status="压缩中", note="")
        self.status_label.configure(text=f"正在压缩（{index}/{total}）{path.name}")

    def _mark_done(self, iid: str, result: CompressResult) -> None:
        self._update_row(
            iid,
            status="完成",
            original=format_size(result.original_size),
            compressed=format_size(result.compressed_size),
            saved=format_saved(result.original_size, result.compressed_size),
            note="",
        )
        self._set_quota(result.compression_count)

    def _mark_failed(self, iid: str, message: str) -> None:
        self._update_row(iid, status="失败", note=message)

    def _finish(self, ok: int, fail: int, stopped: str | None, out_dir: Path, *, started: bool) -> None:
        self._running = False
        self._set_busy(False)
        if not started:
            self.status_label.configure(text=stopped or "压缩未开始")
            messagebox.showerror("无法压缩", stopped or "压缩未开始")
            return
        summary = f"成功 {ok} 张，失败 {fail} 张。"
        if stopped:
            summary = f"{summary}\n{stopped}"
        summary = f"{summary}\n输出目录：{out_dir}"
        self.status_label.configure(text=summary.replace("\n", " "))
        if ok > 0:
            try:
                os.startfile(out_dir)
            except OSError:
                pass
        messagebox.showinfo("压缩结束", summary)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for widget in (
            self.api_entry,
            self.save_btn,
            self.add_btn,
            self.add_folder_btn,
            self.remove_btn,
            self.clear_btn,
            self.browse_btn,
            self.output_entry,
            self.start_btn,
        ):
            widget.configure(state=state)
        self.start_btn.configure(text="压缩中..." if busy else "开始压缩")

    def _insert_row(self, iid: str, path: Path) -> None:
        try:
            original = format_size(path.stat().st_size)
        except OSError:
            original = "—"
        self.tree.insert("", "end", iid=iid, values=(path.name, "等待", original, "—", "—", ""))

    def _refresh_names(self) -> None:
        names = [path.name for path in self._rows.values()]
        for iid, path in self._rows.items():
            if not self.tree.exists(iid):
                continue
            label = str(path) if names.count(path.name) > 1 else path.name
            current = list(self.tree.item(iid, "values"))
            current[0] = label
            self.tree.item(iid, values=current)

    def _update_row(
        self,
        iid: str,
        *,
        status: str,
        original: str | None = None,
        compressed: str | None = None,
        saved: str | None = None,
        note: str | None = None,
    ) -> None:
        if not self.tree.exists(iid):
            return
        current = self.tree.item(iid, "values")
        values = (
            current[0],
            status,
            original if original is not None else current[2],
            compressed if compressed is not None else current[3],
            saved if saved is not None else current[4],
            note if note is not None else current[5],
        )
        tags = {"完成": ("done",), "失败": ("fail",), "压缩中": ("running",)}.get(status, ())
        self.tree.item(iid, values=values, tags=tags)

    def _ordered_items(self) -> list[tuple[str, Path]]:
        return [(iid, self._rows[iid]) for iid in self.tree.get_children() if iid in self._rows]

    def _ui(self, callback) -> None:
        try:
            if self.winfo_exists():
                self.after(0, callback)
        except tkinter.TclError:
            return


def iter_images(folder: Path) -> list[Path]:
    """递归列出文件夹里的图片，跳过其中的 compressed 目录。"""
    try:
        root = folder.resolve()
    except OSError:
        return []
    skip = root / OUTPUT_DIR_NAME
    found: list[Path] = []

    def on_error(_err: OSError) -> None:
        return

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        current = Path(dirpath)
        if _is_relative_to(current, skip):
            dirnames.clear()
            continue
        dirnames[:] = [name for name in dirnames if not _is_relative_to(current / name, skip)]
        for name in filenames:
            path = current / name
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                found.append(path)
    found.sort(key=lambda path: path.relative_to(root).as_posix().lower())
    return found


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def allocate_output(out_dir: Path, source: Path, used: set[str]) -> Path:
    candidate = out_dir / source.name
    key = os.path.normcase(str(candidate))
    if key not in used:
        used.add(key)
        return candidate
    number = 2
    while True:
        candidate = out_dir / f"{source.stem}_{number}{source.suffix}"
        key = os.path.normcase(str(candidate))
        if key not in used:
            used.add(key)
            return candidate
        number += 1


def main() -> None:
    root = ctk.CTk()
    root.title("图片压缩")
    root.geometry("980x680")
    root.minsize(860, 560)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)
    App(root).grid(row=0, column=0, sticky="nsew")
    root.mainloop()


if __name__ == "__main__":
    main()
