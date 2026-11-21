"""模型库扫描。

只读取用户明确列出的目录（设置 → 模型库扫描范围），其他位置一概不碰。
优先使用下载时写下的 model.json；老模型没有 json 时回退到
「模型使用说明.txt」和目录名推断，保证已经存在的库也能直接显示。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

MODEL_EXTS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
# ComfyUI 内部预览用的小 VAE，不作为正常模型展示
SKIP_DIRS = {"vae_approx"}

# 目录名 → 类型键
DIR_TYPE = {
    "loras": "lora",
    "lycoris": "lora",
    "checkpoints": "checkpoint",
    "vae": "vae",
    "vae_approx": "vae_approx",
    "upscale_models": "upscale",
    "text_encoders": "clip",
    "clip": "clip",
    "clip_vision": "clip_vision",
    "diffusion_models": "diffusion",
    "unet": "diffusion",
    "controlnet": "controlnet",
    "embeddings": "embedding",
}

TYPE_LABEL = {
    "checkpoint": "Checkpoint 模型",
    "diffusion": "UNET 模型",
    "lora": "LoRA 模型",
    "vae": "VAE 模型",
    "upscale": "Upscale 模型",
    "clip": "CLIP 模型",
    "controlnet": "ControlNet 模型",
    "clip_vision": "CLIP Vision 模型",
    "embedding": "Embedding 模型",
    "other": "其他模型",
}

# 展示顺序：卡片、类型筛选都据此排序
TYPE_ORDER = {
    key: index
    for index, key in enumerate(
        (
            "checkpoint",
            "diffusion",
            "lora",
            "vae",
            "clip",
            "upscale",
            "controlnet",
            "clip_vision",
            "embedding",
            "other",
        )
    )
}

# 站点/旧文档里五花八门的写法 → 类型键
RAW_TYPE = {
    "lora": "lora",
    "locon": "lora",
    "dora": "lora",
    "lycoris": "lora",
    "checkpoint": "checkpoint",
    "checkpointtrained": "checkpoint",
    "vae": "vae",
    "upscaler": "upscale",
    "upscale": "upscale",
    "textencoder": "clip",
    "clip": "clip",
    "diffusion": "diffusion",
    "unet": "diffusion",
    "controlnet": "controlnet",
    "embedding": "embedding",
    "textualinversion": "embedding",
}

TXT_KEYS = {
    "模型名称": "title",
    "版本": "version_name",
    "类型": "type",
    "基础模型": "base_model",
    "作者": "author",
    "发表时间": "published_at",
    "SHA256": "sha256",
    "网页链接": "page_url",
    "触发词": "trigger_words",
}


def scan(dirs: list[str], limit: int = 4000) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    cache: dict[str, dict[str, Any]] = {}
    for root in dirs or []:
        base = Path(root)
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(
                d
                for d in dirnames
                if not d.startswith(".") and d != "__pycache__" and d.lower() not in SKIP_DIRS
            )
            folder = Path(dirpath)
            models = [f for f in filenames if Path(f).suffix.lower() in MODEL_EXTS]
            if not models:
                continue
            for filename in models:
                full = folder / filename
                key = str(full).lower()
                if key in seen:
                    continue
                seen.add(key)
                # 当前布局是「模型文件 + 同名文件夹放图和说明」；上一版临时布局「模型名文件夹里放一切」也兼容
                sidecar = folder / full.stem
                asset_dir = sidecar if sidecar.is_dir() else folder
                cache_key = str(asset_dir).lower()
                meta = cache.get(cache_key)
                if meta is None:
                    meta = _read_meta(asset_dir) or _read_meta(folder)
                    cache[cache_key] = meta
                results.append(_describe(full, meta, asset_dir))
                if len(results) >= limit:
                    return results
    results.sort(
        key=lambda m: (
            TYPE_ORDER.get(m["type_key"], 99),
            m["base"].lower(),
            m["name"].lower(),
        )
    )
    return results


def _read_meta(folder: Path) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    json_path = folder / "model.json"
    if json_path.is_file():
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                meta = raw
        except (OSError, ValueError):
            meta = {}
    txt_path = folder / "模型使用说明.txt"
    if txt_path.is_file():
        try:
            meta["doc"] = txt_path.read_text(encoding="utf-8-sig")[:20000]
        except OSError:
            pass
    if not meta:
        meta = _read_txt(txt_path)
    return meta


def _read_txt(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    meta: dict[str, Any] = {"doc": text[:20000]}
    for line in text.splitlines():
        if "：" not in line:
            continue
        key, value = line.split("：", 1)
        field = TXT_KEYS.get(key.strip())
        if not field:
            continue
        value = value.strip()
        if field == "trigger_words":
            value = [w.strip() for w in re.split(r"[、,]", value) if w.strip() and w.strip() != "（无）"]
        meta[field] = value
    return meta


DOC_EMPTY_TEXT = "（作者没有提供文字说明）"


def _desc_from_doc(doc: str) -> str:
    """从说明文档里取「模型说明」正文，兼容只有纯文本的老文档。"""
    text = str(doc or "")
    if not text.strip():
        return ""
    lines = text.splitlines()
    body = text.strip()
    for index, line in enumerate(lines):
        if line.strip() == "模型说明":
            rest = [
                item
                for item in lines[index + 1 :]
                if item.strip() and set(item.strip()) != {"-"}
            ]
            body = "\n".join(rest).strip()
            break
    return "" if body == DOC_EMPTY_TEXT else body


def _describe(path: Path, meta: dict[str, Any], asset_dir: Path | None = None) -> dict[str, Any]:
    folder = path.parent
    asset_dir = asset_dir or folder
    type_dir = _find_type_dir(folder)
    type_key = _normalize_type(meta.get("model_type") or meta.get("type"), type_dir)

    base = str(meta.get("base_model") or "")
    if not base:
        base = _guess_base(folder, type_dir)

    images = []
    for file in sorted(asset_dir.iterdir()):
        if file.suffix.lower() in IMAGE_EXTS and file.is_file():
            images.append(str(file))
            if len(images) >= 12:
                break

    try:
        stat = path.stat()
        size = stat.st_size
        mtime = stat.st_mtime
    except OSError:
        size = 0
        mtime = 0

    description = str(meta.get("description") or "").strip()
    if not description:
        # 老模型只有 txt 没有 json 说明，用文档里的正文补齐，保证编辑页和详情页同源
        description = _desc_from_doc(str(meta.get("doc") or ""))

    return {
        # 显示名永远跟随文件名：用户手动改名后，补资料不能再把名字改回站点标题
        "name": path.stem,
        "file": path.name,
        "version": str(meta.get("version_name") or ""),
        "type": TYPE_LABEL.get(type_key, "模型"),
        "type_key": type_key,
        "base": base or "未分类",
        "author": str(meta.get("author") or ""),
        "size": size,
        "mtime": mtime,
        "images": len(images),
        "cover": images[0] if images else "",
        "image_paths": images,
        "trigger": "、".join(meta.get("trigger_words") or []) if isinstance(meta.get("trigger_words"), list) else str(meta.get("trigger_words") or ""),
        "source": str(meta.get("page_url") or ""),
        "desc": description,
        "doc": str(meta.get("doc") or ""),
        "nsfw": bool(meta.get("nsfw")),
        "has_meta": bool(meta),
        "dir": str(asset_dir),
        "path": str(path),
    }


def _find_type_dir(folder: Path) -> str:
    for part in reversed(folder.parts):
        lowered = part.lower()
        if lowered in DIR_TYPE:
            return lowered
    return ""


def _normalize_type(raw: Any, type_dir: str) -> str:
    key = RAW_TYPE.get(str(raw or "").strip().lower().replace(" ", ""), "")
    if key:
        return key
    return DIR_TYPE.get(type_dir, "other")


def _guess_base(folder: Path, type_dir: str) -> str:
    # 结构：<根>/loras/<基础模型>/<模型名>/文件；取类型目录的下一级作为基础模型
    if not type_dir:
        return ""
    parts = folder.parts
    for index, part in enumerate(parts):
        if part.lower() == type_dir and index + 1 < len(parts):
            return parts[index + 1]
    return ""
