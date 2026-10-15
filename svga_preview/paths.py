"""把 SVG 路径画到 Pillow 图像上。"""

from __future__ import annotations

import math
import re

from PIL import Image, ImageDraw

_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.\d+|\d+\.|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def parse_contours(path: str) -> list[list[tuple[float, float]]]:
    tokens = _TOKEN.findall(path or "")
    contours: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    index = 0
    cx = cy = sx = sy = 0.0
    prev = ""
    ctrl: tuple[float, float] | None = None

    def read(count: int) -> list[float]:
        nonlocal index
        values: list[float] = []
        for _ in range(count):
            if index >= len(tokens) or _is_command(tokens[index]):
                raise ValueError("路径参数不足")
            values.append(float(tokens[index]))
            index += 1
        return values

    while index < len(tokens):
        token = tokens[index]
        if _is_command(token):
            command = token
            index += 1
        elif prev in "MmLlHhVvCcSsQqTtAa":
            command = "L" if prev == "M" else "l" if prev == "m" else prev
        else:
            break

        if command in "Zz":
            if current:
                if (cx, cy) != (sx, sy):
                    current.append((sx, sy))
                contours.append(current)
            current = []
            cx, cy = sx, sy
            ctrl = None
            prev = command
            continue

        if command in "Mm":
            x, y = read(2)
            if command == "m" and (current or contours):
                x += cx
                y += cy
            if current:
                contours.append(current)
            cx, cy = x, y
            sx, sy = x, y
            current = [(cx, cy)]
            ctrl = None
            prev = command
            continue

        if command in "Ll":
            x, y = read(2)
            if command == "l":
                x += cx
                y += cy
            current.append((x, y))
            cx, cy = x, y
            ctrl = None
        elif command in "Hh":
            x = read(1)[0]
            if command == "h":
                x += cx
            current.append((x, cy))
            cx = x
            ctrl = None
        elif command in "Vv":
            y = read(1)[0]
            if command == "v":
                y += cy
            current.append((cx, y))
            cy = y
            ctrl = None
        elif command in "Cc":
            x1, y1, x2, y2, x, y = read(6)
            if command == "c":
                x1 += cx
                y1 += cy
                x2 += cx
                y2 += cy
                x += cx
                y += cy
            _cubic(current, (cx, cy), (x1, y1), (x2, y2), (x, y))
            ctrl = (x2, y2)
            cx, cy = x, y
        elif command in "Ss":
            x2, y2, x, y = read(4)
            if command == "s":
                x2 += cx
                y2 += cy
                x += cx
                y += cy
            if prev in "CcSs" and ctrl is not None:
                x1, y1 = 2 * cx - ctrl[0], 2 * cy - ctrl[1]
            else:
                x1, y1 = cx, cy
            _cubic(current, (cx, cy), (x1, y1), (x2, y2), (x, y))
            ctrl = (x2, y2)
            cx, cy = x, y
        elif command in "Qq":
            x1, y1, x, y = read(4)
            if command == "q":
                x1 += cx
                y1 += cy
                x += cx
                y += cy
            _quadratic(current, (cx, cy), (x1, y1), (x, y))
            ctrl = (x1, y1)
            cx, cy = x, y
        elif command in "Tt":
            x, y = read(2)
            if command == "t":
                x += cx
                y += cy
            if prev in "QqTt" and ctrl is not None:
                x1, y1 = 2 * cx - ctrl[0], 2 * cy - ctrl[1]
            else:
                x1, y1 = cx, cy
            _quadratic(current, (cx, cy), (x1, y1), (x, y))
            ctrl = (x1, y1)
            cx, cy = x, y
        elif command in "Aa":
            rx, ry, angle, large, sweep, x, y = read(7)
            if command == "a":
                x += cx
                y += cy
            current.extend(_arc(cx, cy, rx, ry, angle, large >= 0.5, sweep >= 0.5, x, y))
            cx, cy = x, y
            ctrl = None
        else:
            raise ValueError(f"不支持的路径命令 {command}")
        prev = command

    if current:
        contours.append(current)
    return contours


def fill_contours(image: Image.Image, contours: list[list[tuple[float, float]]], color: tuple[int, int, int, int]) -> None:
    mask = _scanline_mask(image.size, [_closed(contour) for contour in contours])
    if color[3] < 255:
        mask = mask.point(lambda value, alpha=color[3]: value * alpha // 255)
    paint = Image.new("RGBA", image.size, color[:3] + (255,))
    paint.putalpha(mask)
    image.alpha_composite(paint)


def stroke_contours(
    image: Image.Image,
    contours: list[list[tuple[float, float]]],
    color: tuple[int, int, int, int],
    width: float,
    dash: tuple[float, float, float],
    line_join: str,
) -> None:
    if width <= 0 or color[3] <= 0:
        return
    draw = ImageDraw.Draw(image)
    joint = "curve" if line_join == "round" else None
    line_width = max(1, int(round(width)))
    dash_len, gap_len, offset = dash
    for contour in contours:
        if len(contour) < 2:
            continue
        if dash_len <= 0 and gap_len <= 0:
            draw.line(contour, fill=color, width=line_width, joint=joint)
            continue
        _dashed(draw, contour, color, line_width, dash_len, gap_len, offset, joint)


def _scanline_mask(size: tuple[int, int], contours: list[list[tuple[float, float]]]) -> Image.Image:
    width, height = size
    mask = Image.new("L", size, 0)
    edges: list[tuple[float, float, float, float, int]] = []
    for contour in contours:
        for (x0, y0), (x1, y1) in zip(contour, contour[1:]):
            if abs(y1 - y0) < 1e-6:
                continue
            direction = 1
            if y0 > y1:
                x0, y0, x1, y1 = x1, y1, x0, y0
                direction = -1
            edges.append((y0, y1, x0, x1, direction))
    if not edges:
        return mask
    draw = ImageDraw.Draw(mask)
    for y in range(height):
        ymid = y + 0.5
        hits: list[tuple[float, int]] = []
        for y0, y1, x0, x1, direction in edges:
            if y0 <= ymid < y1:
                t = (ymid - y0) / (y1 - y0)
                hits.append((x0 + t * (x1 - x0), direction))
        if not hits:
            continue
        hits.sort(key=lambda item: item[0])
        winding = 0
        start: float | None = None
        for x, direction in hits:
            if winding != 0 and start is not None and x > start:
                left = max(0, int(math.ceil(start)))
                right = min(width - 1, int(math.floor(x)))
                if right >= left:
                    draw.line((left, y, right, y), fill=255)
            winding += direction
            start = x
    return mask


def _closed(contour: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(contour) >= 2 and contour[0] != contour[-1]:
        return contour + [contour[0]]
    return contour


def _cubic(out, p0, p1, p2, p3) -> None:
    _flatten(out, p0, p1, p2, p3, 0)


def _flatten(out, p0, p1, p2, p3, depth: int) -> None:
    if depth >= 8 or _flat(p0, p1, p2, p3):
        out.append(p3)
        return
    p01 = _mid(p0, p1)
    p12 = _mid(p1, p2)
    p23 = _mid(p2, p3)
    p012 = _mid(p01, p12)
    p123 = _mid(p12, p23)
    p0123 = _mid(p012, p123)
    _flatten(out, p0, p01, p012, p0123, depth + 1)
    _flatten(out, p0123, p123, p23, p3, depth + 1)


def _quadratic(out, p0, p1, p2) -> None:
    c1 = (p0[0] + 2 / 3 * (p1[0] - p0[0]), p0[1] + 2 / 3 * (p1[1] - p0[1]))
    c2 = (p2[0] + 2 / 3 * (p1[0] - p2[0]), p2[1] + 2 / 3 * (p1[1] - p2[1]))
    _cubic(out, p0, c1, c2, p2)


def _flat(p0, p1, p2, p3) -> bool:
    return _distance(p1, p0, p3) <= 0.35 and _distance(p2, p0, p3) <= 0.35


def _distance(point, start, end) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    return abs((point[0] - start[0]) * dy - (point[1] - start[1]) * dx) / length


def _mid(p0, p1) -> tuple[float, float]:
    return (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2


def _arc(x1, y1, rx, ry, angle, large, sweep, x2, y2) -> list[tuple[float, float]]:
    if math.hypot(x2 - x1, y2 - y1) < 1e-6:
        return []
    if abs(rx) < 1e-6 or abs(ry) < 1e-6:
        return [(x2, y2)]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(angle % 360)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)
    dx = (x1 - x2) / 2
    dy = (y1 - y2) / 2
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy
    radius_scale = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if radius_scale > 1:
        radius_scale = math.sqrt(radius_scale)
        rx *= radius_scale
        ry *= radius_scale
    sign = 1.0 if large != sweep else -1.0
    numerator = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = sign * math.sqrt(max(0.0, numerator / denominator)) if denominator else 0.0
    cxp = coef * rx * y1p / ry
    cyp = coef * -ry * x1p / rx
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2

    def vector_angle(ux, uy, vx, vy) -> float:
        dot = ux * vx + uy * vy
        norm = math.hypot(ux, uy) * math.hypot(vx, vy)
        value = math.acos(max(-1.0, min(1.0, dot / norm))) if norm else 0.0
        if ux * vy - uy * vx < 0:
            value = -value
        return value

    theta = vector_angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = vector_angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and delta > 0:
        delta -= math.tau
    elif sweep and delta < 0:
        delta += math.tau
    steps = max(4, int(abs(delta) / (math.pi / 18)))
    points: list[tuple[float, float]] = []
    for step in range(1, steps + 1):
        t = theta + delta * step / steps
        cos_t = math.cos(t)
        sin_t = math.sin(t)
        x = cx + rx * cos_t * cos_phi - ry * sin_t * sin_phi
        y = cy + rx * cos_t * sin_phi + ry * sin_t * cos_phi
        points.append((x, y))
    if points:
        points[-1] = (x2, y2)
    return points


def _dashed(draw, contour, color, width, dash_len, gap_len, offset, joint) -> None:
    pattern = dash_len + gap_len
    if pattern <= 0:
        return
    cursor = offset % pattern
    for (x0, y0), (x1, y1) in zip(contour, contour[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1e-6:
            continue
        traveled = 0.0
        while traveled < length - 1e-6:
            pos = cursor % pattern
            drawing = pos < dash_len
            room = (dash_len - pos) if drawing else (pattern - pos)
            step = min(room, length - traveled)
            if drawing and step > 0:
                t0 = traveled / length
                t1 = (traveled + step) / length
                draw.line(
                    [(x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0), (x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1)],
                    fill=color,
                    width=width,
                    joint=joint,
                )
            traveled += step
            cursor += step


def _is_command(token: str) -> bool:
    return len(token) == 1 and token.isalpha()
