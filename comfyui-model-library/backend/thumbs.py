"""示例图缩略图缓存。

浏览器直接加载几 MB 的原图会把侧边栏拖慢，这里在首次访问时生成
512px 的 JPEG 缓存，放在插件目录的 cache/thumbs 下，可随时删除重建。
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

THUMB_SIZE = 512
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "thumbs"

_lock = threading.Lock()


def cache_name(path: str, mtime: float, size: int) -> str:
    raw = f"{os.path.normcase(path)}|{int(mtime)}|{size}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() + ".jpg"


def get_thumb(path: str, mtime: float, size: int) -> Path | None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    target = CACHE_DIR / cache_name(path, mtime, size)
    if target.is_file() and target.stat().st_size > 0:
        return target

    with _lock:
        if target.is_file() and target.stat().st_size > 0:
            return target
        try:
            return _build(path, target)
        except Exception:
            return None


def _build(path: str, target: Path) -> Path | None:
    from PIL import Image, ImageOps

    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (32, 32, 34))
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        else:
            image = image.convert("RGB")
        image.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
        image.save(target, "JPEG", quality=85, optimize=True)

    return target if target.is_file() else None
