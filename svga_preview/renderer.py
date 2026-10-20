"""把 SVGA 影片画成一帧帧 RGBA 图像。"""

from __future__ import annotations

import math
from io import BytesIO

from PIL import Image, ImageChops, UnidentifiedImageError

from svga_preview.parser import Color, Frame, Movie, Shape, Sprite, Transform
from svga_preview.paths import fill_contours, parse_contours, stroke_contours


def render_movie(movie: Movie, on_progress=None) -> list[Image.Image]:
    preview_scale = 1.0
    size = (
        max(1, int(round(movie.width * preview_scale))),
        max(1, int(round(movie.height * preview_scale))),
    )
    images = _open_images(movie.images)
    operations = _operations(movie.sprites)
    frames: list[Image.Image] = []
    total = movie.frame_count
    for index in range(total):
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        for kind, payload, matte in operations:
            if kind == "sprite":
                _draw_sprite(canvas, payload, index, images, preview_scale)
                continue
            content = Image.new("RGBA", size, (0, 0, 0, 0))
            for sprite in payload:
                _draw_sprite(content, sprite, index, images, preview_scale)
            matte_frame = _frame_at(matte, index) if matte is not None else None
            if matte is not None and matte_frame is not None and matte_frame.alpha > 0:
                mask = Image.new("RGBA", size, (0, 0, 0, 0))
                _draw_sprite(mask, matte, index, images, preview_scale)
                content = _destination_in(content, mask)
            _composite(canvas, content, (0, 0))
        frames.append(canvas)
        if on_progress is not None:
            on_progress(index + 1, total)
    return frames


def _operations(sprites: list[Sprite]) -> list[tuple]:
    has_matte = bool(sprites) and sprites[0].image_key.endswith(".matte")
    if not has_matte:
        return [("sprite", sprite, None) for sprite in sprites]
    mattes = {sprite.image_key: sprite for sprite in sprites if sprite.image_key.endswith(".matte")}
    ops: list[tuple] = []
    index = 0
    count = len(sprites)
    while index < count:
        sprite = sprites[index]
        if sprite.image_key.endswith(".matte"):
            index += 1
            continue
        key = sprite.matte_key
        if not key:
            ops.append(("sprite", sprite, None))
            index += 1
            continue
        group = [sprite]
        index += 1
        while index < count and not sprites[index].image_key.endswith(".matte") and sprites[index].matte_key == key:
            group.append(sprites[index])
            index += 1
        ops.append(("matte", group, mattes.get(key)))
    return ops


def _draw_sprite(target: Image.Image, sprite: Sprite, index: int, images: dict[str, Image.Image], preview_scale: float) -> None:
    frame = _frame_at(sprite, index)
    if frame is None or frame.alpha <= 0:
        return
    clip = frame.clip_path.strip()
    if not clip:
        _draw_contents(target, sprite, frame, images, preview_scale)
        return
    layer = Image.new("RGBA", target.size, (0, 0, 0, 0))
    _draw_contents(layer, sprite, frame, images, preview_scale)
    mask = _clip_mask(clip, frame.transform, preview_scale, target.size)
    if mask is not None:
        layer = _apply_mask(layer, mask)
    _composite(target, layer, (0, 0))


def _draw_contents(target, sprite: Sprite, frame: Frame, images: dict[str, Image.Image], preview_scale: float) -> None:
    bitmap = _bitmap_for(images, sprite.image_key)
    if bitmap is not None and frame.layout.width > 0 and frame.layout.height > 0:
        _draw_bitmap(target, bitmap, frame, preview_scale)
    for shape in frame.shapes:
        _draw_shape(target, shape, frame, preview_scale)


def _draw_bitmap(target: Image.Image, bitmap: Image.Image, frame: Frame, preview_scale: float) -> None:
    width, height = bitmap.size
    if width <= 0 or height <= 0:
        return
    layout_scale = (frame.layout.width / width, 0.0, 0.0, 0.0, frame.layout.height / height, 0.0)
    matrix = _mul(_mul(_scale(preview_scale), _mat(frame.transform)), layout_scale)
    source = bitmap if frame.alpha >= 0.999 else _with_alpha(bitmap, frame.alpha)
    _warp_onto(target, source, matrix)


def _draw_shape(target: Image.Image, shape: Shape, frame: Frame, preview_scale: float) -> None:
    fill = _color_bytes(shape.fill, frame.alpha)
    stroke = _color_bytes(shape.stroke, frame.alpha)
    stroke_width = shape.stroke_width if stroke is not None else 0.0
    if fill is None and stroke_width <= 0:
        return
    if shape.kind == "rect":
        if shape.w == 0 or shape.h == 0:
            return
        bounds = (shape.x, shape.y, shape.x + shape.w, shape.y + shape.h)
        _raster_shape(target, bounds, stroke_width, shape.transform, frame.transform, preview_scale, lambda image, mapping, local_scale: _paint_rect(image, shape, mapping, local_scale, fill, stroke))
        return
    if shape.kind == "ellipse":
        if shape.rx == 0 or shape.ry == 0:
            return
        bounds = (shape.x - shape.rx, shape.y - shape.ry, shape.x + shape.rx, shape.y + shape.ry)
        _raster_shape(target, bounds, stroke_width, shape.transform, frame.transform, preview_scale, lambda image, mapping, local_scale: _paint_ellipse(image, shape, mapping, local_scale, fill, stroke))
        return
    if shape.kind != "shape" or not shape.d.strip():
        return
    try:
        contours = parse_contours(shape.d)
    except ValueError:
        return
    points = [point for contour in contours for point in contour]
    if not points:
        return
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    bounds = (min(xs), min(ys), max(xs), max(ys))
    _raster_shape(
        target,
        bounds,
        stroke_width,
        shape.transform,
        frame.transform,
        preview_scale,
        lambda image, mapping, local_scale: _paint_path(image, contours, mapping, local_scale, shape, fill, stroke),
    )


def _raster_shape(target, bounds, stroke_width, shape_transform, frame_transform, preview_scale, painter) -> None:
    min_x, min_y, max_x, max_y = bounds
    pad = max(stroke_width, 0.0) + 2.0
    origin_x = min_x - pad
    origin_y = min_y - pad
    raw_w = max(max_x - min_x, 0.0) + pad * 2
    raw_h = max(max_y - min_y, 0.0) + pad * 2
    if raw_w < 1 or raw_h < 1:
        return
    local_scale = min(1.0, 1024.0 / max(raw_w, raw_h))
    size = (max(1, int(math.ceil(raw_w * local_scale))), max(1, int(math.ceil(raw_h * local_scale))))
    image = Image.new("RGBA", size, (0, 0, 0, 0))

    def mapping(x: float, y: float) -> tuple[float, float]:
        return (x - origin_x) * local_scale, (y - origin_y) * local_scale

    painter(image, mapping, local_scale)
    if image.getbbox() is None:
        return
    pixel = (1.0 / local_scale, 0.0, origin_x, 0.0, 1.0 / local_scale, origin_y)
    matrix = _mul(_mul(_mul(_scale(preview_scale), _mat(frame_transform)), _mat(shape_transform)), pixel)
    _warp_onto(target, image, matrix)


def _paint_rect(image, shape: Shape, mapping, local_scale: float, fill, stroke) -> None:
    from PIL import ImageDraw

    x0, y0 = mapping(shape.x, shape.y)
    x1, y1 = mapping(shape.x + shape.w, shape.y + shape.h)
    box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    draw = ImageDraw.Draw(image)
    width = max(1, int(round(shape.stroke_width * local_scale))) if stroke is not None and shape.stroke_width > 0 else 0
    radius = max(0, shape.corner * local_scale)
    outline = stroke if width else None
    if radius > 0:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width or 1)
        return
    if width:
        draw.rectangle(box, fill=fill, outline=outline, width=width)
        return
    draw.rectangle(box, fill=fill)


def _paint_ellipse(image, shape: Shape, mapping, local_scale: float, fill, stroke) -> None:
    from PIL import ImageDraw

    x0, y0 = mapping(shape.x - shape.rx, shape.y - shape.ry)
    x1, y1 = mapping(shape.x + shape.rx, shape.y + shape.ry)
    box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    width = max(1, int(round(shape.stroke_width * local_scale))) if stroke is not None and shape.stroke_width > 0 else 0
    if width:
        ImageDraw.Draw(image).ellipse(box, fill=fill, outline=stroke, width=width)
        return
    ImageDraw.Draw(image).ellipse(box, fill=fill)


def _paint_path(image, contours, mapping, local_scale: float, shape: Shape, fill, stroke) -> None:
    mapped = [[mapping(x, y) for x, y in contour] for contour in contours]
    if fill is not None:
        fill_contours(image, mapped, fill)
    if stroke is not None and shape.stroke_width > 0:
        dash = tuple(value * local_scale for value in shape.dash)
        stroke_contours(image, mapped, stroke, shape.stroke_width * local_scale, dash, shape.line_join)


def _clip_mask(path: str, transform: Transform, preview_scale: float, size: tuple[int, int]) -> Image.Image | None:
    try:
        contours = parse_contours(path)
    except ValueError:
        return None
    if not contours:
        return None
    matrix = _mul(_scale(preview_scale), _mat(transform))
    mapped = [[_apply(matrix, x, y) for x, y in contour] for contour in contours]
    painted = Image.new("RGBA", size, (0, 0, 0, 0))
    fill_contours(painted, mapped, (255, 255, 255, 255))
    return painted.getchannel("A")


def _warp_onto(canvas: Image.Image, image: Image.Image, matrix) -> None:
    inverse = _invert(matrix)
    if inverse is None:
        return
    width, height = image.size
    corners = [_apply(matrix, x, y) for x, y in ((0, 0), (width, 0), (width, height), (0, height))]
    min_x = math.floor(min(point[0] for point in corners)) - 1
    min_y = math.floor(min(point[1] for point in corners)) - 1
    max_x = math.ceil(max(point[0] for point in corners)) + 1
    max_y = math.ceil(max(point[1] for point in corners)) + 1
    canvas_w, canvas_h = canvas.size
    min_x = max(0, min_x)
    min_y = max(0, min_y)
    max_x = min(canvas_w, max_x)
    max_y = min(canvas_h, max_y)
    if max_x <= min_x or max_y <= min_y:
        return
    ia, ic, itx, ib, id_, ity = inverse
    data = (
        ia,
        ic,
        ia * min_x + ic * min_y + itx,
        ib,
        id_,
        ib * min_x + id_ * min_y + ity,
    )
    warped = image.transform(
        (max_x - min_x, max_y - min_y),
        Image.Transform.AFFINE,
        data,
        resample=Image.Resampling.BILINEAR,
        fillcolor=(0, 0, 0, 0),
    )
    _composite(canvas, warped, (min_x, min_y))


def _composite(base: Image.Image, layer: Image.Image, xy: tuple[int, int]) -> None:
    if layer.getbbox() is None:
        return
    x, y = xy
    base_w, base_h = base.size
    layer_w, layer_h = layer.size
    if x >= base_w or y >= base_h or x + layer_w <= 0 or y + layer_h <= 0:
        return
    src_x = max(0, -x)
    src_y = max(0, -y)
    dst_x = max(0, x)
    dst_y = max(0, y)
    right = min(base_w, x + layer_w)
    bottom = min(base_h, y + layer_h)
    if right <= dst_x or bottom <= dst_y:
        return
    cropped = layer.crop((src_x, src_y, src_x + (right - dst_x), src_y + (bottom - dst_y)))
    region = base.crop((dst_x, dst_y, right, bottom))
    base.paste(Image.alpha_composite(region, cropped), (dst_x, dst_y))


def _destination_in(content: Image.Image, matte: Image.Image) -> Image.Image:
    red, green, blue, alpha = content.split()
    alpha = ImageChops.multiply(alpha, matte.getchannel("A"))
    return Image.merge("RGBA", (red, green, blue, alpha))


def _apply_mask(image: Image.Image, mask: Image.Image) -> Image.Image:
    red, green, blue, alpha = image.split()
    alpha = ImageChops.multiply(alpha, mask)
    return Image.merge("RGBA", (red, green, blue, alpha))


def _with_alpha(image: Image.Image, alpha: float) -> Image.Image:
    red, green, blue, channel = image.split()
    cover = Image.new("L", image.size, max(0, min(255, int(round(alpha * 255)))))
    return Image.merge("RGBA", (red, green, blue, ImageChops.multiply(channel, cover)))


def _open_images(raw: dict[str, bytes]) -> dict[str, Image.Image]:
    opened: dict[str, Image.Image] = {}
    for key, data in raw.items():
        try:
            image = Image.open(BytesIO(data))
            rgba = image.convert("RGBA")
        except (UnidentifiedImageError, OSError, ValueError):
            continue
        if rgba is not image:
            image.close()
        opened[key] = rgba
    return opened


def _bitmap_for(images: dict[str, Image.Image], image_key: str) -> Image.Image | None:
    if image_key.endswith(".matte"):
        base = image_key[:-6]
        if base in images:
            return images[base]
    return images.get(image_key)


def _frame_at(sprite: Sprite | None, index: int) -> Frame | None:
    if sprite is None or index < 0 or index >= len(sprite.frames):
        return None
    return sprite.frames[index]


def _color_bytes(color: Color | None, frame_alpha: float):
    if color is None:
        return None
    alpha = color.a * frame_alpha
    if alpha <= 0:
        return None

    def channel(value: float) -> int:
        if value > 1:
            return max(0, min(255, int(round(value))))
        return max(0, min(255, int(round(value * 255))))

    return channel(color.r), channel(color.g), channel(color.b), channel(alpha)


def _mat(transform: Transform):
    return (transform.a, transform.c, transform.tx, transform.b, transform.d, transform.ty)


def _scale(value: float):
    return (value, 0.0, 0.0, 0.0, value, 0.0)


def _mul(first, second):
    a1, c1, x1, b1, d1, y1 = first
    a2, c2, x2, b2, d2, y2 = second
    return (
        a1 * a2 + c1 * b2,
        a1 * c2 + c1 * d2,
        a1 * x2 + c1 * y2 + x1,
        b1 * a2 + d1 * b2,
        b1 * c2 + d1 * d2,
        b1 * x2 + d1 * y2 + y1,
    )


def _apply(matrix, x: float, y: float) -> tuple[float, float]:
    a, c, tx, b, d, ty = matrix
    return a * x + c * y + tx, b * x + d * y + ty


def _invert(matrix):
    a, c, tx, b, d, ty = matrix
    det = a * d - c * b
    if abs(det) < 1e-8:
        return None
    inv = 1.0 / det
    ia = d * inv
    ic = -c * inv
    ib = -b * inv
    id_ = a * inv
    itx = -(ia * tx + ic * ty)
    ity = -(ib * tx + id_ * ty)
    return ia, ic, itx, ib, id_, ity
