"""桌面小工具主界面。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customtkinter as ctk

from anim_preview.main import App as AnimPreviewApp
from color_converter.main import App as ColorConverterApp
from image_compressor.main import App as ImageCompressorApp
from json_formatter.main import App as JsonFormatterApp
from screen_density.main import App as ScreenDensityApp
from svga_preview.main import App as SvgaPreviewApp
from timestamp_converter.main import App as TimestampConverterApp

FONT = ("Microsoft YaHei UI", 13)
FONT_SM = ("Microsoft YaHei UI", 12)
FONT_TITLE = ("Microsoft YaHei UI", 28)
HOME_TITLE = "桌面小工具"

TOOLS = (
    {
        "key": "image",
        "title": "图片压缩",
        "description": "用 TinyPNG 压缩 PNG、JPEG、WebP、AVIF",
    },
    {
        "key": "json",
        "title": "JSON 格式化",
        "description": "缩进排版或压成一行",
    },
    {
        "key": "svga",
        "title": "SVGA 预览",
        "description": "播放 SVGA 动画，查看画布和帧信息",
    },
    {
        "key": "anim",
        "title": "动图播放",
        "description": "播放 GIF、动画 WebP",
    },
    {
        "key": "timestamp",
        "title": "时间戳转换",
        "description": "秒、毫秒与日期时间互转",
    },
    {
        "key": "color",
        "title": "颜色转换",
        "description": "十六进制、RGB 与透明度互转",
    },
    {
        "key": "density",
        "title": "ADB",
        "description": "查看 xh/xxh，安装 apk，截屏录屏到 D:\\adb",
    },
)


class HomeFrame(ctk.CTkFrame):
    def __init__(self, master, on_open) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=0, column=0)
        ctk.CTkLabel(content, text=HOME_TITLE, font=FONT_TITLE).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ctk.CTkLabel(content, text="选择一个工具", font=FONT_SM, text_color=("gray30", "gray70")).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 20)
        )

        for index, tool in enumerate(TOOLS):
            row, column = divmod(index, 2)
            card = _ToolCard(content, tool["title"], tool["description"], command=lambda key=tool["key"]: on_open(key))
            card.grid(row=row + 2, column=column, padx=10, pady=10, sticky="nsew")
            content.grid_columnconfigure(column, weight=1, uniform="tools")


class _ToolCard(ctk.CTkFrame):
    def __init__(self, master, title: str, description: str, command) -> None:
        super().__init__(master, width=320, height=150, corner_radius=12, cursor="hand2")
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        name = ctk.CTkLabel(self, text=title, font=("Microsoft YaHei UI", 18), anchor="w")
        name.grid(row=0, column=0, sticky="ew", padx=20, pady=(24, 8))
        detail = ctk.CTkLabel(
            self,
            text=description,
            font=FONT,
            anchor="w",
            justify="left",
            wraplength=260,
            text_color=("gray30", "gray70"),
        )
        detail.grid(row=1, column=0, sticky="ew", padx=20)
        _bind_click(self, command)


def _bind_click(widget, command) -> None:
    widget.bind("<Button-1>", lambda _event: command())
    try:
        widget.configure(cursor="hand2")
    except Exception:
        pass
    for child in widget.winfo_children():
        _bind_click(child, command)


class Shell(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self.title(HOME_TITLE)
        self.geometry("980x820")
        self.minsize(860, 760)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._titles = {tool["key"]: tool["title"] for tool in TOOLS}
        self.home = HomeFrame(self, self.show_tool)
        self.pages = {
            "image": ImageCompressorApp(self, on_back=self.show_home),
            "json": JsonFormatterApp(self, on_back=self.show_home),
            "svga": SvgaPreviewApp(self, on_back=self.show_home),
            "anim": AnimPreviewApp(self, on_back=self.show_home),
            "timestamp": TimestampConverterApp(self, on_back=self.show_home),
            "color": ColorConverterApp(self, on_back=self.show_home),
            "density": ScreenDensityApp(self, on_back=self.show_home),
        }
        self.show_home()

    def show_tool(self, key: str) -> None:
        self._hide(self.home)
        for page in self.pages.values():
            self._hide(page)
        self.pages[key].grid(row=0, column=0, sticky="nsew")
        self.title(self._titles[key])

    def show_home(self) -> None:
        for page in self.pages.values():
            self._hide(page)
        self.home.grid(row=0, column=0, sticky="nsew")
        self.title(HOME_TITLE)

    @staticmethod
    def _hide(widget) -> None:
        if widget.winfo_manager() != "grid":
            return
        pause = getattr(widget, "pause", None)
        if callable(pause):
            pause()
        widget.grid_remove()


def main() -> None:
    Shell().mainloop()


if __name__ == "__main__":
    main()
