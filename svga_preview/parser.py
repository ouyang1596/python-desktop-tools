"""读取 SVGA 1.x / 2.x 文件。"""

from __future__ import annotations

import json
import zipfile
import zlib
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from svga_preview import svga_pb2

_MAX_BYTES = 256 * 1024 * 1024
_MAX_CANVAS = 8192
_IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")
_KEEP = "keep"


class SvgaError(Exception):
    """文件无法作为 SVGA 打开。"""


@dataclass
class Transform:
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: float = 0.0
    ty: float = 0.0


@dataclass
class Layout:
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass
class Color:
    r: float
    g: float
    b: float
    a: float


@dataclass
class Shape:
    kind: str
    d: str = ""
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    corner: float = 0.0
    fill: Color | None = None
    stroke: Color | None = None
    stroke_width: float = 0.0
    line_cap: str = "butt"
    line_join: str = "miter"
    dash: tuple[float, float, float] = (0.0, 0.0, 0.0)
    transform: Transform = field(default_factory=Transform)


@dataclass
class Frame:
    alpha: float
    layout: Layout
    transform: Transform
    clip_path: str
    shapes: list[Shape]


@dataclass
class Sprite:
    image_key: str
    matte_key: str
    frames: list[Frame]


@dataclass
class Movie:
    version: str
    width: float
    height: float
    fps: int
    frame_count: int
    images: dict[str, bytes]
    sprites: list[Sprite]
    has_audio: bool
    name: str
    file_size: int


def load(path: str | Path) -> Movie:
    file = Path(path)
    try:
        data = file.read_bytes()
    except OSError as err:
        raise SvgaError(f"无法读取文件：{err}") from err
    return loads(data, file.name, len(data))


def loads(data: bytes, name: str = "", file_size: int | None = None) -> Movie:
    if not data:
        raise SvgaError("这不是有效的 SVGA 文件")
    size = len(data) if file_size is None else file_size
    if data.startswith(b"PK"):
        return _load_zip(data, name, size)
    movie = _parse_movie_bytes(data)
    return _from_proto(movie, {}, name, size)


def _load_zip(data: bytes, name: str, size: int) -> Movie:
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as err:
        raise SvgaError("这不是有效的 SVGA 文件") from err
    with archive:
        total = sum(info.file_size for info in archive.infolist())
        if total > _MAX_BYTES:
            raise SvgaError("文件过大，无法预览")
        files: dict[str, bytes] = {}
        for info in archive.infolist():
            if info.is_dir():
                continue
            base = _basename(info.filename)
            if not base or base.startswith(".") or base.startswith("__"):
                continue
            files[base] = archive.read(info)
    if "movie.binary" in files:
        movie = _parse_movie_bytes(files["movie.binary"])
        return _from_proto(movie, files, name, size)
    if "movie.spec" in files:
        return _from_spec(files["movie.spec"], files, name, size)
    raise SvgaError("这不是有效的 SVGA 文件")


def _parse_movie_bytes(blob: bytes):
    candidates: list[bytes] = []
    if _looks_zlib(blob):
        inflated = _inflate(blob)
        if inflated:
            candidates.append(inflated)
        candidates.append(blob)
    else:
        candidates.append(blob)
        inflated = _inflate(blob)
        if inflated and inflated != blob:
            candidates.append(inflated)
    last_error: Exception | None = None
    for raw in candidates:
        if len(raw) > _MAX_BYTES:
            raise SvgaError("文件过大，无法预览")
        movie = _try_movie(raw)
        if isinstance(movie, Exception):
            last_error = movie
            continue
        if movie is not None:
            return movie
    if last_error is not None:
        raise SvgaError("SVGA 内容无法解析") from last_error
    raise SvgaError("SVGA 内容无法解析")


def _try_movie(raw: bytes):
    movie = svga_pb2.MovieEntity()
    try:
        movie.ParseFromString(raw)
    except Exception as err:
        return err
    params = movie.params
    if params.viewBoxWidth > 0 and params.viewBoxHeight > 0 and (params.frames > 0 or movie.sprites):
        return movie
    return None


def _looks_zlib(data: bytes) -> bool:
    if len(data) < 2:
        return False
    cmf, flg = data[0], data[1]
    return (cmf & 0x0F) == 8 and ((cmf << 8) + flg) % 31 == 0


def _inflate(data: bytes) -> bytes | None:
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            decoder = zlib.decompressobj(wbits)
        except zlib.error:
            continue
        try:
            result = decoder.decompress(data, _MAX_BYTES)
        except zlib.error:
            continue
        if decoder.unconsumed_tail:
            raise SvgaError("文件过大，无法预览")
        if result:
            return result
    return None


def _from_proto(movie, zip_files: dict[str, bytes], name: str, size: int) -> Movie:
    params = movie.params
    has_audio = bool(movie.audios) or any(str(key).startswith("audio") for key in movie.images)
    images = _images_from_proto(movie.images, zip_files)
    sprites = [_sprite_from_proto(sprite) for sprite in movie.sprites]
    return _finish(
        version=movie.version or "2.0",
        width=params.viewBoxWidth,
        height=params.viewBoxHeight,
        fps=params.fps,
        frame_count=params.frames,
        images=images,
        sprites=sprites,
        has_audio=has_audio,
        name=name,
        file_size=size,
    )


def _images_from_proto(raw_images, zip_files: dict[str, bytes]) -> dict[str, bytes]:
    images: dict[str, bytes] = {}
    for key, raw in raw_images.items():
        key = str(key)
        if key.startswith("audio"):
            continue
        data = bytes(raw)
        if _is_image(data):
            images[key] = data
            continue
        filename = _maybe_filename(data)
        if filename:
            blob = zip_files.get(_basename(filename))
            if blob and _is_image(blob):
                images[key] = blob
    for filename, blob in zip_files.items():
        if filename in ("movie.binary", "movie.spec") or filename in images:
            continue
        if _is_image(blob):
            images[filename] = blob
    return images


def _sprite_from_proto(sprite) -> Sprite:
    frames = [_frame_from_proto(frame) for frame in sprite.frames]
    _resolve_keep(frames)
    return Sprite(image_key=sprite.imageKey or "", matte_key=sprite.matteKey or "", frames=frames)


def _frame_from_proto(frame) -> Frame:
    layout = frame.layout
    shapes = [_shape_from_proto(shape) for shape in frame.shapes]
    return Frame(
        alpha=float(frame.alpha),
        layout=Layout(layout.x, layout.y, layout.width, layout.height),
        transform=_transform_from_message(frame.transform) if frame.HasField("transform") else Transform(),
        clip_path=frame.clipPath or "",
        shapes=shapes,
    )


def _shape_from_proto(shape) -> Shape:
    if int(shape.type) == svga_pb2.ShapeEntity.KEEP:
        return Shape(kind=_KEEP)
    styles = shape.styles
    fill = _color_from_message(styles.fill) if styles.HasField("fill") else None
    stroke = _color_from_message(styles.stroke) if styles.HasField("stroke") else None
    common = dict(
        fill=fill,
        stroke=stroke,
        stroke_width=float(styles.strokeWidth),
        line_cap=_LINE_CAP.get(int(styles.lineCap), "butt"),
        line_join=_LINE_JOIN.get(int(styles.lineJoin), "miter"),
        dash=(float(styles.lineDashI), float(styles.lineDashII), float(styles.lineDashIII)),
        transform=_transform_from_message(shape.transform) if shape.HasField("transform") else Transform(),
    )
    which = shape.WhichOneof("args")
    if which == "rect" or int(shape.type) == svga_pb2.ShapeEntity.RECT:
        rect = shape.rect
        return Shape(kind="rect", x=rect.x, y=rect.y, w=rect.width, h=rect.height, corner=rect.cornerRadius, **common)
    if which == "ellipse" or int(shape.type) == svga_pb2.ShapeEntity.ELLIPSE:
        ellipse = shape.ellipse
        return Shape(kind="ellipse", x=ellipse.x, y=ellipse.y, rx=ellipse.radiusX, ry=ellipse.radiusY, **common)
    return Shape(kind="shape", d=shape.shape.d or "", **common)


def _from_spec(blob: bytes, zip_files: dict[str, bytes], name: str, size: int) -> Movie:
    try:
        spec = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise SvgaError("SVGA 内容无法解析") from err
    if not isinstance(spec, dict):
        raise SvgaError("SVGA 内容无法解析")
    movie = spec.get("movie") or {}
    view_box = movie.get("viewBox") or {}
    images: dict[str, bytes] = {}
    declared = spec.get("images") or {}
    if isinstance(declared, dict):
        for key, filename in declared.items():
            blob_image = zip_files.get(_basename(str(filename))) or zip_files.get(_basename(str(key)))
            if blob_image and _is_image(blob_image):
                images[str(key)] = blob_image
    sprites = []
    declared_sprites = spec.get("sprites") if isinstance(spec.get("sprites"), list) else []
    for sprite in declared_sprites:
        if not isinstance(sprite, dict):
            continue
        frames = [_frame_from_json(frame) for frame in sprite.get("frames") or [] if isinstance(frame, dict)]
        _resolve_keep(frames)
        sprites.append(
            Sprite(
                image_key=str(sprite.get("imageKey") or ""),
                matte_key=str(sprite.get("matteKey") or ""),
                frames=frames,
            )
        )
    return _finish(
        version=str(spec.get("ver") or "1.0"),
        width=float(view_box.get("width") or 0),
        height=float(view_box.get("height") or 0),
        fps=int(movie.get("fps") or 0),
        frame_count=int(movie.get("frames") or 0),
        images=images,
        sprites=sprites,
        has_audio=bool(spec.get("audios")),
        name=name,
        file_size=size,
    )


def _frame_from_json(raw: dict) -> Frame:
    layout = raw.get("layout") or {}
    shapes_raw = raw.get("shapes") or []
    shapes = [_shape_from_json(shape) for shape in shapes_raw if isinstance(shape, dict)]
    return Frame(
        alpha=float(raw.get("alpha") or 0),
        layout=Layout(
            _number(layout, "x"),
            _number(layout, "y"),
            _number(layout, "width"),
            _number(layout, "height"),
        ),
        transform=_transform_from_json(raw.get("transform")),
        clip_path=str(raw.get("clipPath") or ""),
        shapes=shapes,
    )


def _shape_from_json(raw: dict) -> Shape:
    kind = str(raw.get("type") or "shape").lower()
    if kind == _KEEP:
        return Shape(kind=_KEEP)
    args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
    styles = raw.get("styles") if isinstance(raw.get("styles"), dict) else {}
    dash = styles.get("lineDash") or [0, 0, 0]
    if not isinstance(dash, list):
        dash = [0, 0, 0]
    while len(dash) < 3:
        dash.append(0)
    fill = _color_from_json(styles.get("fill"))
    stroke = _color_from_json(styles.get("stroke"))
    common = dict(
        fill=fill,
        stroke=stroke,
        stroke_width=_number(styles, "strokeWidth"),
        line_cap=str(styles.get("lineCap") or "butt").lower(),
        line_join=str(styles.get("lineJoin") or "miter").lower(),
        dash=(float(dash[0] or 0), float(dash[1] or 0), float(dash[2] or 0)),
        transform=_transform_from_json(raw.get("transform")),
    )
    if kind == "rect":
        return Shape(
            kind="rect",
            x=_number(args, "x"),
            y=_number(args, "y"),
            w=_number(args, "width"),
            h=_number(args, "height"),
            corner=_number(args, "cornerRadius"),
            **common,
        )
    if kind == "ellipse":
        radius_x = _number(args, "radiusX")
        radius_y = _number(args, "radiusY", radius_x)
        return Shape(kind="ellipse", x=_number(args, "x"), y=_number(args, "y"), rx=radius_x, ry=radius_y, **common)
    return Shape(kind="shape", d=str(args.get("d") or ""), **common)


def _finish(
    *,
    version: str,
    width: float,
    height: float,
    fps: int,
    frame_count: int,
    images: dict[str, bytes],
    sprites: list[Sprite],
    has_audio: bool,
    name: str,
    file_size: int,
) -> Movie:
    width = float(width)
    height = float(height)
    if width <= 0 or height <= 0:
        raise SvgaError("画布尺寸无效")
    if width > _MAX_CANVAS or height > _MAX_CANVAS:
        raise SvgaError("画布尺寸过大，无法预览")
    if frame_count <= 0:
        frame_count = max((len(sprite.frames) for sprite in sprites), default=0)
    if frame_count <= 0:
        raise SvgaError("文件里没有可播放的帧")
    if fps <= 0:
        fps = 20
    return Movie(
        version=version or "—",
        width=width,
        height=height,
        fps=int(fps),
        frame_count=int(frame_count),
        images=images,
        sprites=sprites,
        has_audio=has_audio,
        name=name,
        file_size=file_size,
    )


def _resolve_keep(frames: list[Frame]) -> None:
    previous: list[Shape] = []
    for frame in frames:
        if frame.shapes and frame.shapes[0].kind == _KEEP:
            frame.shapes = previous
            continue
        frame.shapes = [shape for shape in frame.shapes if shape.kind != _KEEP]
        previous = frame.shapes


def _transform_from_message(message) -> Transform:
    def pick(attr: str, default: float) -> float:
        if message.HasField(attr):
            return float(getattr(message, attr))
        return default

    return Transform(
        a=pick("a", 1.0),
        b=pick("b", 0.0),
        c=pick("c", 0.0),
        d=pick("d", 1.0),
        tx=pick("tx", 0.0),
        ty=pick("ty", 0.0),
    )


def _transform_from_json(raw) -> Transform:
    if not isinstance(raw, dict):
        return Transform()

    def pick(attr: str, default: float) -> float:
        if attr not in raw or raw[attr] is None:
            return default
        return float(raw[attr])

    return Transform(
        a=pick("a", 1.0),
        b=pick("b", 0.0),
        c=pick("c", 0.0),
        d=pick("d", 1.0),
        tx=pick("tx", 0.0),
        ty=pick("ty", 0.0),
    )


def _color_from_message(message) -> Color:
    alpha = float(message.a) if message.HasField("a") else 1.0
    return Color(float(message.r), float(message.g), float(message.b), alpha)


def _color_from_json(raw) -> Color | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return None
    alpha = float(raw[3]) if len(raw) > 3 and raw[3] is not None else 1.0
    return Color(float(raw[0]), float(raw[1]), float(raw[2]), alpha)


def _number(raw, key: str, default: float = 0.0) -> float:
    if not isinstance(raw, dict) or raw.get(key) is None:
        return default
    return float(raw[key])


def _is_image(data: bytes) -> bool:
    if data.startswith(_IMAGE_MAGIC):
        return True
    return len(data) > 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"


def _maybe_filename(data: bytes) -> str | None:
    if not data or len(data) > 240 or b"\x00" in data:
        return None
    try:
        text = data.decode("ascii")
    except UnicodeError:
        return None
    if not text or any(char in text for char in "\r\n\t"):
        return None
    return text


def _basename(name: str) -> str:
    return name.replace("\\", "/").rstrip("/").split("/")[-1]


_LINE_CAP = {0: "butt", 1: "round", 2: "square"}
_LINE_JOIN = {0: "miter", 1: "round", 2: "bevel"}
