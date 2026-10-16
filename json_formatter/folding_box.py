"""带左侧折叠箭头的 JSON 结果框。"""

from __future__ import annotations

import tkinter

import customtkinter as ctk

from json_formatter.folding import Fold, find_folds

_ELLIPSIS = "…"
_HIDE_TAG = "fold_hidden"
_GUTTER_WIDTH = 22


class FoldingTextbox(ctk.CTkTextbox):
    def __init__(self, *args, **kwargs) -> None:
        self._gutter_width = _GUTTER_WIDTH
        super().__init__(*args, **kwargs)
        self._source = ""
        self._folds: list[Fold] = []
        self._collapsed: set[tuple[int, int]] = set()
        self._folds_by_line: dict[int, Fold] = {}
        self._arrow_color = "gray"
        self._redraw_job: str | None = None
        self._gutter = tkinter.Canvas(
            self,
            width=self._apply_widget_scaling(self._gutter_width),
            height=0,
            highlightthickness=0,
            bd=0,
            takefocus=False,
            cursor="arrow",
        )
        self._textbox.tag_configure(_HIDE_TAG, elide=True)
        self._canvas.grid_configure(columnspan=3)
        tkinter.Misc.lower(self._canvas)
        self._create_grid_for_text_and_scrollbars(
            re_grid_textbox=True,
            re_grid_x_scrollbar=True,
            re_grid_y_scrollbar=True,
        )
        self._textbox.configure(yscrollcommand=self._on_text_yscroll)
        self._gutter.bind("<Button-1>", self._on_gutter_click)
        self._gutter.bind("<Motion>", self._on_gutter_motion)
        self._gutter.bind("<MouseWheel>", self._on_gutter_wheel)
        self._textbox.bind("<Configure>", self._schedule_redraw, add="+")
        self._gutter.bind("<Configure>", self._schedule_redraw)
        self._sync_gutter_colors()

    def set_document(self, text: str) -> None:
        self._source = text
        self._folds = find_folds(text)
        self._collapsed = set()
        self._folds_by_line = {}
        for fold in self._folds:
            current = self._folds_by_line.get(fold.start_line)
            if current is None or fold.start_col < current.start_col:
                self._folds_by_line[fold.start_line] = fold
        self._render()

    def expand_all(self) -> bool:
        if not self._folds:
            return False
        self._collapsed = set()
        self._render()
        return True

    def collapse_all(self) -> bool:
        if not self._folds:
            return False
        self._collapsed = {fold.key for fold in self._folds}
        self._render()
        return True

    def _create_grid_for_text_and_scrollbars(
        self,
        re_grid_textbox=False,
        re_grid_x_scrollbar=False,
        re_grid_y_scrollbar=False,
    ):
        if not hasattr(self, "_gutter"):
            super()._create_grid_for_text_and_scrollbars(
                re_grid_textbox,
                re_grid_x_scrollbar,
                re_grid_y_scrollbar,
            )
            return

        corner = self._apply_widget_scaling(max(self._corner_radius, self._border_width + self._border_spacing))
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0, minsize=corner)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0, minsize=corner)
        self._gutter.configure(width=self._apply_widget_scaling(self._gutter_width))

        if re_grid_textbox:
            self._gutter.grid(row=0, column=0, sticky="ns", padx=(corner, 0), pady=(corner, 0))
            self._textbox.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=(corner, 0))

        if re_grid_x_scrollbar:
            if not self._hide_x_scrollbar and self._scrollbars_activated:
                self._x_scrollbar.grid(
                    row=1,
                    column=1,
                    rowspan=1,
                    columnspan=1,
                    sticky="ewn",
                    pady=(3, self._border_spacing + self._border_width),
                    padx=(0, 0),
                )
            else:
                self._x_scrollbar.grid_forget()

        if re_grid_y_scrollbar:
            inset = max(self._corner_radius, self._border_width + self._border_spacing)
            if not self._hide_y_scrollbar and self._scrollbars_activated:
                self._y_scrollbar.grid(
                    row=0,
                    column=2,
                    rowspan=1,
                    columnspan=1,
                    sticky="nsw",
                    padx=(3, self._border_spacing + self._border_width),
                    pady=(inset, 0),
                )
            else:
                self._y_scrollbar.grid_forget()

    def _update_font(self):
        self._textbox.configure(font=self._apply_font_scaling(self._font))
        span = 3 if hasattr(self, "_gutter") else 2
        self._canvas.grid_forget()
        self._canvas.grid(row=0, column=0, rowspan=2, columnspan=span, sticky="nsew")
        tkinter.Misc.lower(self._canvas)

    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        self._sync_gutter_colors()

    def _sync_gutter_colors(self) -> None:
        if not hasattr(self, "_gutter"):
            return
        try:
            if not self._gutter.winfo_exists():
                return
        except tkinter.TclError:
            return
        color = self._bg_color if self._fg_color == "transparent" else self._fg_color
        self._gutter.configure(bg=self._apply_appearance_mode(color))
        self._arrow_color = self._apply_appearance_mode(self._text_color)
        self._redraw_arrows()

    def _render(self) -> None:
        if not self._textbox.winfo_exists():
            return
        previous = str(self._textbox.cget("state"))
        self._textbox.configure(state="normal", autoseparators=False)
        try:
            self._textbox.delete("1.0", "end")
            if self._source:
                self._textbox.insert("1.0", self._source)
            for fold in sorted(self._active_collapsed(), key=lambda item: item.key, reverse=True):
                self._textbox.insert(f"{fold.start_line}.{fold.start_col + 1}", _ELLIPSIS)
                self._textbox.tag_add(
                    _HIDE_TAG,
                    f"{fold.start_line}.{fold.start_col + 2}",
                    f"{fold.end_line}.{fold.end_col}",
                )
            try:
                self._textbox.edit_reset()
            except tkinter.TclError:
                pass
        finally:
            self._textbox.configure(autoseparators=True, state=previous)
        self.update_idletasks()
        self._redraw_arrows()

    def _active_collapsed(self) -> list[Fold]:
        active: list[Fold] = []
        for fold in self._folds:
            if fold.key not in self._collapsed or self._is_hidden(fold):
                continue
            active.append(fold)
        return active

    def _is_hidden(self, fold: Fold) -> bool:
        return any(other.key in self._collapsed and other.contains(fold) for other in self._folds)

    def _on_text_yscroll(self, first: str, last: str) -> None:
        self._y_scrollbar.set(first, last)
        self._redraw_arrows()

    def _schedule_redraw(self, _event=None) -> None:
        if self._redraw_job is not None:
            return
        self._redraw_job = self.after_idle(self._redraw_arrows)

    def _text_y(self, gutter_y: int) -> int:
        try:
            return gutter_y - (self._textbox.winfo_rooty() - self._gutter.winfo_rooty())
        except tkinter.TclError:
            return gutter_y

    def _gutter_y(self, text_y: int) -> int:
        try:
            return text_y + (self._textbox.winfo_rooty() - self._gutter.winfo_rooty())
        except tkinter.TclError:
            return text_y

    def _redraw_arrows(self) -> None:
        self._redraw_job = None
        try:
            if not self.winfo_exists() or not self._gutter.winfo_exists():
                return
        except tkinter.TclError:
            return
        self._gutter.delete("arrow")
        width = int(float(self._gutter.cget("width")))
        size = max(int(self._apply_widget_scaling(4)), 3)
        center_x = width / 2
        for fold in self._folds:
            if self._is_hidden(fold):
                continue
            info = self._textbox.dlineinfo(f"{fold.start_line}.0")
            if info is None:
                continue
            _x, top, _w, height, _baseline = info
            center_y = self._gutter_y(top + height / 2)
            collapsed = fold.key in self._collapsed
            self._draw_triangle(center_x, center_y, collapsed, size)

    def _draw_triangle(self, center_x: float, center_y: float, collapsed: bool, size: int) -> None:
        if collapsed:
            points = (
                center_x - size * 0.35,
                center_y - size,
                center_x - size * 0.35,
                center_y + size,
                center_x + size,
                center_y,
            )
        else:
            points = (
                center_x - size,
                center_y - size * 0.45,
                center_x + size,
                center_y - size * 0.45,
                center_x,
                center_y + size * 0.75,
            )
        self._gutter.create_polygon(*points, fill=self._arrow_color, outline="", tags=("arrow",))

    def _fold_at_y(self, gutter_y: int) -> Fold | None:
        if not self._folds_by_line:
            return None
        text_y = self._text_y(gutter_y)
        index = self._textbox.index(f"@0,{text_y}")
        line = int(str(index).split(".")[0])
        fold = self._folds_by_line.get(line)
        if fold is None or self._is_hidden(fold):
            return None
        info = self._textbox.dlineinfo(f"{fold.start_line}.0")
        if info is None:
            return None
        _x, top, _w, height, _baseline = info
        if text_y < top or text_y >= top + height:
            return None
        return fold

    def _on_gutter_click(self, event) -> None:
        fold = self._fold_at_y(event.y)
        if fold is None:
            return
        if fold.key in self._collapsed:
            self._collapsed.remove(fold.key)
        else:
            self._collapsed.add(fold.key)
        self._render()

    def _on_gutter_motion(self, event) -> None:
        self._gutter.configure(cursor="hand2" if self._fold_at_y(event.y) else "arrow")

    def _on_gutter_wheel(self, event) -> str:
        if event.delta:
            self._textbox.yview_scroll(int(-event.delta / 120), "units")
        return "break"
