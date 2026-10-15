"""SVGA 预览窗口。"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customtkinter as ctk
from PIL import Image, ImageDraw
from tkinter import filedialog

from svga_preview.parser import Movie, SvgaError, load
from svga_preview.renderer import render_movie

FONT = ("Microsoft YaHei UI", 13)
FONT_SM = ("Microsoft YaHei UI", 12)
FONT_TITLE = ("Microsoft YaHei UI", 20)
STATUS_OK = ("gray20", "gray80")
STATUS_ERROR = ("#c62828", "#ef9a9a")


class App(ctk.CTkFrame):
    def __init__(self, master, on_back=None) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self._on_back = on_back
        self._movie: Movie | None = None
        self._frames: list[Image.Image] = []
        self._frame_index = 0
        self._playing = False
        self._job = None
        self._token = 0
        self._loading = False
        self._polling = False
        self._inbox: queue.Queue = queue.Queue()
        self._slider_lock = False
        self._photo = None
        self._resize_job = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build()

    def pause(self) -> None:
        self._playing = False
        job = self._job
        self._job = None
        if job is not None:
            try:
                self.after_cancel(job)
            except tkinter.TclError:
                pass
        try:
            if self.winfo_exists():
                self.play_btn.configure(text="播放")
        except tkinter.TclError:
            return

    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        if self._on_back is not None:
            ctk.CTkButton(header, text="返回", width=72, command=self._on_back).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(header, text="SVGA 预览", font=FONT_TITLE).pack(side="left")

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        self.open_btn = ctk.CTkButton(tools, text="打开文件", width=100, command=self._open)
        self.open_btn.pack(side="left")
        self.play_btn = ctk.CTkButton(tools, text="播放", width=72, state="disabled", command=self._toggle)
        self.play_btn.pack(side="left", padx=(8, 0))
        self.loop_switch = ctk.CTkSwitch(tools, text="循环", font=FONT)
        self.loop_switch.pack(side="left", padx=(16, 0))
        self.loop_switch.select()
        self.speed_switch = ctk.CTkSegmentedButton(tools, values=["0.5x", "1x", "2x"], font=FONT_SM)
        self.speed_switch.set("1x")
        self.speed_switch.pack(side="right")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=20, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self.stage = ctk.CTkFrame(body, corner_radius=12)
        self.stage.grid(row=0, column=0, sticky="nsew")
        self.stage.grid_columnconfigure(0, weight=1)
        self.stage.grid_rowconfigure(0, weight=1)
        self.preview = ctk.CTkLabel(self.stage, text="打开一个 SVGA 文件", font=FONT, text_color=("gray30", "gray70"))
        self.preview.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.stage.bind("<Configure>", self._on_resize)

        info = ctk.CTkFrame(body, width=230)
        info.grid(row=0, column=1, sticky="ns", padx=(12, 0))
        info.grid_propagate(False)
        info.grid_columnconfigure(1, weight=1)
        self._info = {}
        fields = (
            ("file", "文件"),
            ("version", "版本"),
            ("canvas", "画布"),
            ("fps", "帧率"),
            ("frames", "总帧"),
            ("sprites", "图层"),
            ("size", "大小"),
            ("audio", "音频"),
        )
        for row, (key, title) in enumerate(fields):
            ctk.CTkLabel(info, text=title, font=FONT_SM, text_color=("gray30", "gray70")).grid(
                row=row, column=0, sticky="nw", padx=(16, 8), pady=(14 if row == 0 else 8, 0)
            )
            value = ctk.CTkLabel(info, text="—", font=FONT_SM, anchor="w", justify="left", wraplength=140)
            value.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=(14 if row == 0 else 8, 0))
            self._info[key] = value

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=20, pady=(10, 4))
        bar.grid_columnconfigure(0, weight=1)
        self.slider = ctk.CTkSlider(bar, from_=0, to=1, number_of_steps=1, state="disabled", command=self._on_slider)
        self.slider.grid(row=0, column=0, sticky="ew")
        self.frame_label = ctk.CTkLabel(bar, text="0 / 0", width=88, font=FONT_SM, anchor="e")
        self.frame_label.grid(row=0, column=1, padx=(12, 0))

        self.status_label = ctk.CTkLabel(self, text="打开 SVGA 文件后即可播放", font=FONT_SM, anchor="w")
        self.status_label.grid(row=4, column=0, sticky="ew", padx=20, pady=(8, 16))

    def _open(self) -> None:
        chosen = filedialog.askopenfilename(
            title="打开 SVGA",
            filetypes=[("SVGA", "*.svga"), ("所有文件", "*.*")],
        )
        if not chosen:
            return
        self._load_path(chosen)

    def _load_path(self, chosen: str) -> None:
        self._token += 1
        token = self._token
        self._loading = True
        self.pause()
        self.open_btn.configure(state="disabled")
        self.play_btn.configure(state="disabled")
        self._set_status("正在解析…")
        threading.Thread(target=self._load_worker, args=(Path(chosen), token), daemon=True).start()
        self._ensure_poll()

    def _load_worker(self, path: Path, token: int) -> None:
        try:
            movie = load(path)

            def progress(done: int, total: int) -> None:
                if done != total and done % 3 != 0:
                    return
                self._inbox.put((token, "progress", (done, total)))

            frames = render_movie(movie, progress)
        except SvgaError as err:
            self._inbox.put((token, "error", str(err)))
            return
        except Exception as err:
            self._inbox.put((token, "error", f"无法预览：{err}"))
            return
        self._inbox.put((token, "done", (movie, frames)))

    def _ensure_poll(self) -> None:
        if self._polling:
            return
        self._polling = True
        self._poll_inbox()

    def _poll_inbox(self) -> None:
        while True:
            try:
                token, kind, payload = self._inbox.get_nowait()
            except queue.Empty:
                break
            if token != self._token:
                continue
            if kind == "progress":
                done, total = payload
                self._set_status(f"正在渲染 {done}/{total}")
            elif kind == "error":
                self._fail(token, payload)
            elif kind == "done":
                movie, frames = payload
                self._show_loaded(token, movie, frames)
        try:
            if self._loading and self.winfo_exists():
                self.after(30, self._poll_inbox)
                return
        except tkinter.TclError:
            pass
        self._polling = False

    def _show_loaded(self, token: int, movie: Movie, frames: list[Image.Image]) -> None:
        if token != self._token or not self.winfo_exists():
            return
        self._loading = False
        self._movie = movie
        self._frames = frames
        self._frame_index = 0
        self._fill_info(movie)
        count = len(frames)
        self._slider_lock = True
        if count > 1:
            self.slider.configure(from_=0, to=count - 1, number_of_steps=count - 1, state="normal")
        else:
            self.slider.configure(from_=0, to=1, number_of_steps=1, state="disabled")
        self.slider.set(0)
        self._slider_lock = False
        self.open_btn.configure(state="normal")
        self.play_btn.configure(state="normal", text="播放")
        self._show_frame(0)
        self._set_status(f"已打开 {movie.name}" if movie.name else "已打开")
        if count > 1 and self.winfo_viewable():
            self._play()

    def _fail(self, token: int, message: str) -> None:
        if token != self._token or not self.winfo_exists():
            return
        self._loading = False
        self.open_btn.configure(state="normal")
        self.play_btn.configure(state="normal" if self._frames else "disabled")
        self._set_status(message, error=True)

    def _toggle(self) -> None:
        if self._playing:
            self.pause()
            return
        self._play()

    def _play(self) -> None:
        if not self._frames:
            return
        if len(self._frames) == 1:
            self._show_frame(0)
            return
        if self._frame_index >= len(self._frames) - 1:
            self._show_frame(0)
        self._playing = True
        self.play_btn.configure(text="暂停")
        self._schedule()

    def _schedule(self) -> None:
        movie = self._movie
        if movie is None:
            return
        delay = max(1, int(1000 / (movie.fps * self._speed())))
        self._job = self.after(delay, self._tick)

    def _tick(self) -> None:
        self._job = None
        if not self._playing or not self._frames:
            return
        try:
            visible = self.winfo_viewable()
        except tkinter.TclError:
            return
        if not visible:
            self.pause()
            return
        nxt = self._frame_index + 1
        if nxt >= len(self._frames):
            if self._loop_on():
                nxt = 0
            else:
                self.pause()
                return
        self._show_frame(nxt)
        if self._playing:
            self._schedule()

    def _on_slider(self, value) -> None:
        if self._slider_lock or not self._frames:
            return
        self.pause()
        self._show_frame(int(round(float(value))), from_slider=True)

    def _show_frame(self, index: int, *, from_slider: bool = False) -> None:
        if not self._frames:
            return
        index = max(0, min(index, len(self._frames) - 1))
        self._frame_index = index
        if not from_slider:
            self._slider_lock = True
            self.slider.set(index)
            self._slider_lock = False
        self.frame_label.configure(text=f"{index + 1} / {len(self._frames)}")
        self._draw_preview()

    def _draw_preview(self) -> None:
        self._resize_job = None
        if not self._frames or not self.winfo_exists():
            return
        width = self.stage.winfo_width() - 16
        height = self.stage.winfo_height() - 16
        if width < 40 or height < 40:
            return
        dark = ctk.get_appearance_mode() == "Dark"
        composed = _fit_frame(self._frames[self._frame_index], width, height, dark)
        self._photo = ctk.CTkImage(light_image=composed, dark_image=composed, size=composed.size)
        self.preview.configure(image=self._photo, text="")

    def _on_resize(self, event) -> None:
        if event.widget is not self.stage or not self._frames:
            return
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(60, self._draw_preview)

    def _fill_info(self, movie: Movie) -> None:
        self._info["file"].configure(text=movie.name or "—")
        self._info["version"].configure(text=movie.version or "—")
        self._info["canvas"].configure(text=f"{_trim(movie.width)} × {_trim(movie.height)}")
        self._info["fps"].configure(text=f"{movie.fps} fps")
        self._info["frames"].configure(text=str(movie.frame_count))
        self._info["sprites"].configure(text=str(len(movie.sprites)))
        self._info["size"].configure(text=_format_size(movie.file_size))
        self._info["audio"].configure(text="含音频，预览不播放" if movie.has_audio else "无")

    def _speed(self) -> float:
        text = str(self.speed_switch.get())
        if text.startswith("0.5"):
            return 0.5
        if text.startswith("2"):
            return 2.0
        return 1.0

    def _loop_on(self) -> bool:
        return bool(self.loop_switch.get())

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.configure(text=message, text_color=STATUS_ERROR if error else STATUS_OK)

def _fit_frame(frame: Image.Image, width: int, height: int, dark: bool) -> Image.Image:
    board = _checkerboard(width, height, dark)
    scale = min(width / frame.width, height / frame.height)
    resized = frame.resize(
        (max(1, int(frame.width * scale)), max(1, int(frame.height * scale))),
        Image.Resampling.BILINEAR,
    )
    x = (width - resized.width) // 2
    y = (height - resized.height) // 2
    board.paste(resized, (x, y), resized)
    return board


def _checkerboard(width: int, height: int, dark: bool) -> Image.Image:
    first, second = ((48, 48, 48), (66, 66, 66)) if dark else ((232, 232, 232), (255, 255, 255))
    image = Image.new("RGBA", (width, height), first + (255,))
    draw = ImageDraw.Draw(image)
    cell = 12
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            if (x // cell + y // cell) % 2 == 0:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=second + (255,))
    return image


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.2f} MB"


def _trim(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def main() -> None:
    root = ctk.CTk()
    root.title("SVGA 预览")
    root.geometry("980x680")
    root.minsize(860, 560)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)
    App(root).grid(row=0, column=0, sticky="nsew")
    root.mainloop()


if __name__ == "__main__":
    main()
