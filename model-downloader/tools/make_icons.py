"""生成软件图标（标题栏灰云 + 任务栏黑底箭头）。

只在需要重做图标时运行：
    .venv\\Scripts\\python.exe tools\\make_icons.py
依赖 Pillow（仅生成资源时需要，程序运行不需要）。
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"

GRAY = (139, 139, 134, 255)
TILE = (27, 27, 25, 255)
WHITE = (255, 255, 255, 255)
STROKE = (255, 255, 255, 24)
SS = 4  # 每个尺寸先按 4 倍绘制，再缩小，保证边缘干净


def _cloud_mask(size: int, simplified: bool) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    k = size / 1024

    def box(values: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(v * k for v in values)

    draw.rounded_rectangle(box((120, 560, 904, 750)), radius=95 * k, fill=255)
    if simplified:
        bumps = ((235, 560, 135), (465, 455, 215), (755, 545, 145))
    else:
        bumps = ((225, 560, 120), (370, 485, 165), (560, 455, 185), (760, 540, 140))
    for cx, cy, r in bumps:
        draw.ellipse(box((cx - r, cy - r, cx + r, cy + r)), fill=255)

    # 箭头从云朵里镂空：粗箭杆 + 宽三角头，16px 也不会糊成十字
    if simplified:
        half, head, top, head_y, tip = 60, 160, 340, 500, 660
    else:
        half, head, top, head_y, tip = 52, 142, 350, 515, 655
    points = [
        (512 - half, top),
        (512 + half, top),
        (512 + half, head_y),
        (512 + head, head_y),
        (512, tip),
        (512 - head, head_y),
        (512 - half, head_y),
    ]
    draw.polygon([(x * k, y * k) for x, y in points], fill=0)
    radius = half * k
    draw.ellipse(
        (512 * k - radius, top * k - radius, 512 * k + radius, top * k + radius), fill=0
    )
    return mask


def render_titlebar(size: int, simplified: bool) -> Image.Image:
    work = size * SS
    mask = _cloud_mask(work, simplified)
    image = Image.new("RGBA", (work, work), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (work, work), GRAY), (0, 0), mask)
    return image.resize((size, size), Image.LANCZOS)


def _tile_arrow(draw: ImageDraw.ImageDraw, ox: float, oy: float, glyph: float) -> None:
    width = max(1, int(glyph * 1.8 / 24))

    def pt(x: float, y: float) -> tuple[float, float]:
        return (ox + x / 24 * glyph, oy + y / 24 * glyph)

    def line(points: list[tuple[float, float]], joint: str | None = None) -> None:
        draw.line([pt(x, y) for x, y in points], fill=WHITE, width=width, joint=joint)

    line([(12, 3), (12, 13)])
    line([(8, 9), (12, 13), (16, 9)], joint="curve")
    line([(4, 17), (4, 19)])
    line([(6, 21), (18, 21)])
    line([(20, 19), (20, 17)])

    def ellipse_at(x: float, y: float) -> None:
        px, py = pt(x, y)
        r = width / 2
        draw.ellipse((px - r, py - r, px + r, py + r), fill=WHITE)

    # 圆角托盘的两个下角
    for bbox, start, end in (
        ((4, 17, 8, 21), 90, 180),
        ((16, 17, 20, 21), 0, 90),
    ):
        x0, y0, x1, y1 = bbox
        p0 = pt(x0, y0)
        p1 = pt(x1, y1)
        draw.arc((p0[0], p0[1], p1[0], p1[1]), start=start, end=end, fill=WHITE, width=width)

    for x, y in ((12, 3), (8, 9), (16, 9), (4, 17), (20, 17), (12, 13)):
        ellipse_at(x, y)


def render_app(size: int) -> Image.Image:
    work = size * SS
    k = work / 1024
    image = Image.new("RGBA", (work, work), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    pad = 61 * k
    tile = (pad, pad, work - pad, work - pad)
    radius = (tile[2] - tile[0]) * 0.30
    draw.rounded_rectangle(tile, radius=radius, fill=TILE)

    # 极淡内描边，避免黑色方块在深色任务栏里糊成一片
    edge = max(1, int(size * 0.012 * SS))
    inset = edge / 2
    draw.rounded_rectangle(
        (tile[0] + inset, tile[1] + inset, tile[2] - inset, tile[3] - inset),
        radius=max(1, radius - inset),
        outline=STROKE,
        width=edge,
    )

    glyph = (tile[2] - tile[0]) * 0.56
    ox = work / 2 - glyph / 2
    oy = work / 2 - glyph / 2
    _tile_arrow(draw, ox, oy, glyph)
    return image.resize((size, size), Image.LANCZOS)


def write_ico(path: Path, images: list[Image.Image]) -> None:
    """自己拼 ICO 容器，让不同尺寸可以来自不同的绘制版本。"""
    frames = sorted(images, key=lambda item: item.width)
    payloads: list[bytes] = []
    for image in frames:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        payloads.append(buffer.getvalue())

    offset = 6 + 16 * len(frames)
    entries = b""
    for image, data in zip(frames, payloads):
        width = image.width if image.width < 256 else 0
        height = image.height if image.height < 256 else 0
        entries += struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(frames)) + entries + b"".join(payloads))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    titlebar = [
        render_titlebar(16, simplified=True),
        render_titlebar(20, simplified=True),
        render_titlebar(24, simplified=True),
        render_titlebar(32, simplified=False),
    ]
    app = [render_app(size) for size in (32, 48, 64, 128, 256)]

    write_ico(OUT / "titlebar.ico", titlebar)
    write_ico(OUT / "app.ico", app)
    render_titlebar(256, simplified=False).save(OUT / "titlebar.png")
    render_app(256).save(OUT / "app.png")

    for name in ("titlebar.ico", "app.ico"):
        with Image.open(OUT / name) as image:
            print(name, sorted(image.info.get("sizes", [])))
    print("图标已生成：", OUT)


if __name__ == "__main__":
    main()
