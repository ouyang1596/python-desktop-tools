"""把 GIF、WebP 解码成一帧帧 RGBA 图像。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

MAX_PREVIEW_EDGE = 960
_MIN_DURATION = 20
_FALLBACK_DURATION = 100
_FORMATS = {".gif": "GIF", ".webp": "WEBP"}


class AnimError(Exception):
    """文件不是可播放的 GIF 或 WebP。"""


@dataclass(frozen=True)
class Animation:
    frames: list[Image.Image]
    durations: list[int]
    width: int
    height: int
    loop: int
    format: str
    name: str
    file_size: int


def load(path: Path | str, on_progress=None) -> Animation:
    file_path = Path(path)
    expected = _FORMATS.get(file_path.suffix.lower())
    if expected is None:
        raise AnimError("请选择 GIF 或 WebP 文件")
    try:
        file_size = file_path.stat().st_size
    except OSError as err:
        raise AnimError(f"无法打开：{err}") from err
    try:
        with Image.open(file_path) as image:
            if image.format != expected:
                raise AnimError("文件内容与扩展名不符")
            frames, durations = _read_frames(image, on_progress)
            width, height = image.size
            loop = _loop_count(image)
            label = "WebP" if expected == "WEBP" else "GIF"
    except AnimError:
        raise
    except UnidentifiedImageError as err:
        raise AnimError("无法识别该图片") from err
    except OSError as err:
        raise AnimError(f"无法打开：{err}") from err
    if width < 1 or height < 1:
        raise AnimError("图片尺寸无效")
    return Animation(
        frames=_fit_preview(frames, width, height),
        durations=durations,
        width=width,
        height=height,
        loop=loop,
        format=label,
        name=file_path.name,
        file_size=file_size,
    )


def _read_frames(image: Image.Image, on_progress) -> tuple[list[Image.Image], list[int]]:
    total = getattr(image, "n_frames", 1) or 1
    frames: list[Image.Image] = []
    durations: list[int] = []
    for index in range(total):
        image.seek(index)
        # Pillow 9.1+ 在 seek/load 时已按 disposal 合成整帧。
        # 这里只复制结果；再叠一次 disposal 会把画面套两次。
        frames.append(image.convert("RGBA").copy())
        durations.append(_duration(image.info.get("duration")))
        if on_progress is not None:
            on_progress(index + 1, total)
    if not frames:
        raise AnimError("文件里没有可播放的帧")
    return frames, durations


def _duration(raw) -> int:
    try:
        duration = int(raw)
    except (TypeError, ValueError):
        return _FALLBACK_DURATION
    if duration < _MIN_DURATION:
        return _FALLBACK_DURATION
    return duration


def _loop_count(image: Image.Image) -> int:
    raw = image.info.get("loop")
    if raw is None:
        return 1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def _fit_preview(frames: list[Image.Image], width: int, height: int) -> list[Image.Image]:
    edge = max(width, height)
    if edge <= MAX_PREVIEW_EDGE:
        return frames
    scale = MAX_PREVIEW_EDGE / edge
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return [frame.resize(size, Image.Resampling.BILINEAR) for frame in frames]
