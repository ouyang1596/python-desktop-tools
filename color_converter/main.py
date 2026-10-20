"""颜色转换窗口。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customtkinter as ctk
from PIL import Image, ImageDraw

from color_converter.converter import ColorValue, ConvertError, parse_color, with_alpha_percent

FONT = ("Microsoft YaHei UI", 13)
FONT_SM = ("Microsoft YaHei UI", 12)
FONT_TITLE = ("Microsoft YaHei UI", 20)
FONT_CODE = ("Consolas", 13)
STATUS_OK = ("gray20", "gray80")
STATUS_ERROR = ("#c62828", "#ef9a9a")
VALUE_COLOR = ("gray10", "gray90")
EMPTY_COLOR = ("gray50", "gray60")
_PREVIEW_SIZE = (168, 112)
_FIELDS = (
    ("argb", "#AARRGGBB"),
    ("rgb", "#RRGGBB"),
    ("parse", "parseColor"),
    ("literal", "0xAARRGGBB"),
    ("channels", "RGB"),
    ("alpha", "透明度"),
)


class App(ctk.CTkFrame):
    def __init__(self, master, on_back=None) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self._on_back = on_back
        self._color: ColorValue | None = None
        self._syncing_slider = False
        self._swatch_image: ctk.CTkImage | None = None
        self._values = {key: "" for key, _title in _FIELDS}
        self._labels: dict[str, ctk.CTkLabel] = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        if self._on_back is not None:
            ctk.CTkButton(header, text="返回", width=72, command=self._on_back).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(header, text="颜色转换", font=FONT_TITLE).pack(side="left")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=20, pady=4)
        body.grid_columnconfigure(0, weight=1)

        source = self._card(body, 0, "输入")
        tools = ctk.CTkFrame(source, fg_color="transparent")
        tools.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 4))
        tools.grid_columnconfigure(0, weight=1)
        self.color_entry = ctk.CTkEntry(tools, font=FONT_CODE, placeholder_text="例如 #FF5722 或 rgb(255, 87, 51)")
        self.color_entry.grid(row=0, column=0, sticky="ew")
        self.color_entry.bind("<Return>", lambda _event: self._convert())
        ctk.CTkButton(tools, text="转换", width=72, command=self._convert).grid(row=0, column=1, padx=(8, 0))

        alpha_row = ctk.CTkFrame(source, fg_color="transparent")
        alpha_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 14))
        alpha_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(alpha_row, text="透明度", font=FONT, anchor="w").grid(row=0, column=0, sticky="w")
        self.alpha_slider = ctk.CTkSlider(alpha_row, from_=0, to=100, number_of_steps=100, command=self._on_alpha)
        self.alpha_slider.grid(row=0, column=1, sticky="ew", padx=12)
        self.alpha_slider.set(100)
        self.alpha_entry = ctk.CTkEntry(alpha_row, font=FONT_CODE, width=56, justify="right")
        self.alpha_entry.grid(row=0, column=2, sticky="e")
        self.alpha_entry.insert(0, "100")
        self.alpha_entry.bind("<Return>", lambda _event: self._apply_alpha_entry())
        self.alpha_entry.bind("<FocusOut>", lambda _event: self._apply_alpha_entry())
        ctk.CTkLabel(alpha_row, text="%", font=FONT_CODE).grid(row=0, column=3, padx=(4, 0))

        result = self._card(body, 1, "结果")
        result.grid_columnconfigure(1, weight=1)
        self.swatch_label = ctk.CTkLabel(result, text="—", font=FONT, text_color=EMPTY_COLOR, width=168, height=112)
        self.swatch_label.grid(row=1, column=0, padx=(16, 8), pady=(4, 16), sticky="nw")
        lines = ctk.CTkFrame(result, fg_color="transparent")
        lines.grid(row=1, column=1, sticky="nsew", padx=(0, 16), pady=(0, 12))
        lines.grid_columnconfigure(1, weight=1)
        for index, (key, title) in enumerate(_FIELDS):
            bottom = index == len(_FIELDS) - 1
            ctk.CTkLabel(lines, text=title, font=FONT, anchor="w", width=108).grid(
                row=index, column=0, sticky="w", pady=(0, 8 if bottom else 4)
            )
            label = ctk.CTkLabel(lines, text="—", font=FONT_CODE, anchor="w", text_color=EMPTY_COLOR)
            label.grid(row=index, column=1, sticky="ew", padx=8, pady=(0, 8 if bottom else 4))
            ctk.CTkButton(lines, text="复制", width=64, height=28, command=lambda name=key: self._copy(name)).grid(
                row=index, column=2, pady=(0, 8 if bottom else 4)
            )
            self._labels[key] = label

        self.status_label = ctk.CTkLabel(self, text="输入颜色后转换", font=FONT_SM, anchor="w")
        self.status_label.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 16))

    def _card(self, parent, row: int, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, corner_radius=12)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=title, font=FONT, anchor="w").grid(row=0, column=0, sticky="w", padx=16, pady=(12, 4))
        return card

    def _convert(self) -> None:
        try:
            color = parse_color(self.color_entry.get())
        except ConvertError as err:
            self._color = None
            self._clear_results()
            self._set_status(str(err), error=True)
            return
        self._color = color
        self._set_slider(color.alpha_percent)
        self._show(color)
        self._set_status("转换完成")

    def _on_alpha(self, value: float) -> None:
        percent = max(0, min(100, int(round(value))))
        self._write_alpha_entry(percent)
        self._apply_percent(percent)

    def _apply_alpha_entry(self) -> None:
        if self._syncing_slider:
            return
        raw = self.alpha_entry.get().strip()
        if raw.endswith("%"):
            raw = raw[:-1].strip()
        if not raw.isdigit():
            self._reject_alpha_entry()
            return
        percent = int(raw)
        if percent > 100:
            self._reject_alpha_entry()
            return
        self._set_slider(percent)
        self._apply_percent(percent)

    def _reject_alpha_entry(self) -> None:
        self._write_alpha_entry(self._current_percent())
        self._set_status("透明度需要在 0 到 100 之间", error=True)

    def _apply_percent(self, percent: int) -> None:
        if self._syncing_slider or self._color is None:
            return
        self._color = with_alpha_percent(self._color, percent)
        self._show(self._color)
        self._set_status("转换完成")

    def _show(self, color: ColorValue) -> None:
        self._set_value("argb", color.argb_hex)
        self._set_value("rgb", color.rgb_hex)
        self._set_value("parse", color.parse_color)
        self._set_value("literal", color.literal)
        self._set_value("channels", color.channels)
        self._set_value("alpha", color.alpha_text)
        light = _swatch(color, False)
        dark = _swatch(color, True)
        self._swatch_image = ctk.CTkImage(light_image=light, dark_image=dark, size=_PREVIEW_SIZE)
        self.swatch_label.configure(image=self._swatch_image, text="")

    def _clear_results(self) -> None:
        for key in self._values:
            self._set_value(key, "")
        self._swatch_image = None
        self.swatch_label.configure(image=None, text="—")

    def _current_percent(self) -> int:
        return max(0, min(100, int(round(float(self.alpha_slider.get())))))

    def _write_alpha_entry(self, percent: int) -> None:
        self._syncing_slider = True
        try:
            self.alpha_entry.delete(0, "end")
            self.alpha_entry.insert(0, str(percent))
        finally:
            self._syncing_slider = False

    def _set_slider(self, percent: int) -> None:
        self._syncing_slider = True
        try:
            self.alpha_slider.set(percent)
            self.alpha_entry.delete(0, "end")
            self.alpha_entry.insert(0, str(percent))
        finally:
            self._syncing_slider = False

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


def _swatch(color: ColorValue, dark: bool) -> Image.Image:
    width, height = _PREVIEW_SIZE[0] * 2, _PREVIEW_SIZE[1] * 2
    board = _checkerboard(width, height, dark)
    overlay = Image.new("RGBA", (width, height), (color.red, color.green, color.blue, color.alpha))
    return Image.alpha_composite(board, overlay)


def _checkerboard(width: int, height: int, dark: bool) -> Image.Image:
    first, second = ((48, 48, 48), (66, 66, 66)) if dark else ((232, 232, 232), (255, 255, 255))
    image = Image.new("RGBA", (width, height), first + (255,))
    draw = ImageDraw.Draw(image)
    cell = 16
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            if (x // cell + y // cell) % 2 == 0:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=second + (255,))
    return image


def main() -> None:
    root = ctk.CTk()
    root.title("颜色转换")
    root.geometry("980x680")
    root.minsize(860, 560)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)
    App(root).grid(row=0, column=0, sticky="nsew")
    root.mainloop()


if __name__ == "__main__":
    main()
