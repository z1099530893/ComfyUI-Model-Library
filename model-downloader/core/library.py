"""模型库归档规则。

先按模型类型决定 ComfyUI 目录，再决定要不要建同名文件夹：

    <库根>\\checkpoints\\<模型名>\\        ← 基础模型
    <库根>\\diffusion_models\\<模型名>\\   ← 纯扩散模型
    <库根>\\loras\\<模型名>\\              ← LoRA
        <模型文件>.safetensors
        示例图_01.jpg ...
        模型使用说明.txt      ← 给人看
        model.json            ← 给程序看（后续 ComfyUI 插件唯一数据源）

    <库根>\\vae\\<模型文件>               ← VAE / CLIP / 放大模型不建文件夹，直接平铺
    <库根>\\text_encoders\\<模型文件>
    <库根>\\upscale_models\\<模型文件>
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin")

# 模型类型是唯一的落点依据：决定 ComfyUI 目录、以及要不要建同名文件夹
MODEL_TYPES: dict[str, dict[str, Any]] = {
    "checkpoint": {"dir": "checkpoints", "label": "基础模型", "folder": True},
    "diffusion": {"dir": "diffusion_models", "label": "扩散模型", "folder": True},
    "lora": {"dir": "loras", "label": "LoRA", "folder": True},
    "vae": {"dir": "vae", "label": "VAE", "folder": False},
    "clip": {"dir": "text_encoders", "label": "CLIP 文本编码器", "folder": False},
    "upscale": {"dir": "upscale_models", "label": "放大模型", "folder": False},
}
TYPE_DIR_TO_TYPE = {spec["dir"]: key for key, spec in MODEL_TYPES.items()}


def normalize_type(value: str | None) -> str:
    """把前端传来的类型或旧的 type_dir 统一成内部类型键。"""
    key = str(value or "").strip().lower()
    if key in MODEL_TYPES:
        return key
    return TYPE_DIR_TO_TYPE.get(key, "checkpoint")


def safe_name(name: str, fallback: str = "未命名", limit: int = 96) -> str:
    cleaned = INVALID_CHARS.sub("_", str(name or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    if not cleaned:
        cleaned = fallback
    return cleaned[:limit].rstrip(" .") or fallback


def plan_target(root: str, plan: dict[str, Any]) -> dict[str, Any]:
    """算出这个模型应该落在哪个目录、叫什么名字。

    模型文件平铺在类型目录下，资料放在同名文件夹里（ComfyUI 只列模型文件，
    不会因为资料文件夹多出一层目录）。
    """
    model_type = normalize_type(plan.get("model_type") or plan.get("type_dir"))
    spec = MODEL_TYPES[model_type]
    type_dir = spec["dir"]
    title = safe_name(plan.get("title") or plan.get("folder_name") or "未命名模型")
    filename = safe_name(plan.get("filename") or f"{title}.safetensors", "model.safetensors", 150)

    base = Path(root)
    # 用户如果直接选中了 loras / checkpoints 这类目录，就不再重复加一层
    if base.name.lower() != type_dir.lower():
        base = base / type_dir

    model_path = base / filename
    # 同名文件已存在且大小不同，说明是另一个版本，用版本名区分
    if model_path.is_file():
        size = int(plan.get("size") or 0)
        try:
            same = size > 0 and model_path.stat().st_size == size
        except OSError:
            same = False
        if not same:
            stem = Path(filename).stem
            version_name = safe_name(plan.get("version_name") or "", "", 40)
            if version_name and version_name.lower() not in stem.lower():
                filename = safe_name(f"{stem} - {version_name}", stem, 150) + Path(filename).suffix
                plan["filename"] = filename
                model_path = base / filename

    asset_dir = base / Path(filename).stem if spec["folder"] else None

    return {
        "model_type": model_type,
        "type_dir": type_dir,
        "folder_mode": bool(spec["folder"]),
        "folder_name": title,
        "model_dir": str(base),
        "model_path": str(model_path),
        "asset_dir": str(asset_dir) if asset_dir else "",
    }


def write_docs(
    model_dir: Path,
    plan: dict[str, Any],
    sha256: str,
    image_files: list[str],
    downloaded_at: str | None = None,
) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    downloaded_at = downloaded_at or datetime.now().astimezone().isoformat(timespec="seconds")

    size = int(plan.get("size") or 0)
    triggers = "、".join(plan.get("trigger_words") or []) or "（无）"
    tags = "、".join(plan.get("tags") or []) or "（无）"
    description = (plan.get("description") or "").strip() or "（作者没有提供文字说明）"

    lines = [
        f"模型名称：{plan.get('title') or ''}",
        f"版本：{plan.get('version_name') or '（未命名）'}",
        f"类型：{MODEL_TYPES[normalize_type(plan.get('model_type'))]['label']}",
        f"基础模型：{plan.get('base_model') or ''}",
        f"作者：{plan.get('author') or '（未知）'}",
        f"发表时间：{plan.get('published_at') or '（未知）'}",
        f"文件：{Path(plan.get('model_path') or plan.get('filename') or '').name}",
        f"大小：{_human(size)}（{size} 字节）",
        f"SHA256：{sha256 or '（未提供）'}",
        f"网页链接：{plan.get('page_url') or ''}",
        f"触发词：{triggers}",
        f"标签：{tags}",
        f"下载时间：{downloaded_at}",
        "",
        "模型说明",
        "----------------------------------------",
        description,
        "",
    ]
    (model_dir / "模型使用说明.txt").write_text("\n".join(lines), encoding="utf-8-sig", newline="\r\n")

    payload = {
        "schema": 1,
        "site": plan.get("site"),
        "model_id": plan.get("model_id"),
        "version_id": plan.get("version_id"),
        "page_url": plan.get("page_url"),
        "title": plan.get("title"),
        "version_name": plan.get("version_name"),
        "type": plan.get("type"),
        "model_type": normalize_type(plan.get("model_type")),
        "type_dir": plan.get("type_dir"),
        "base_model": plan.get("base_model"),
        "author": plan.get("author"),
        "published_at": plan.get("published_at"),
        "trigger_words": plan.get("trigger_words") or [],
        "tags": plan.get("tags") or [],
        "description": plan.get("description") or "",
        "nsfw": bool(plan.get("nsfw")),
        "file": {
            "name": Path(plan.get("model_path") or "").name,
            "size": size,
            "sha256": sha256 or "",
        },
        "images": image_files,
        "downloaded_at": downloaded_at,
    }
    (model_dir / "model.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _human(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}" if unit not in ("B", "KB") else f"{value:.0f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def image_ext(data: bytes, fallback: str = ".jpg") -> str:
    """按文件头判断示例图真实格式。

    Civitai 的 original=true 链接常以 .jpeg 结尾，实际内容却是 PNG；
    按网址后缀命名会让 ComfyUI 读不到元数据。
    """
    head = (data or b"")[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    ext = str(fallback or "").lower()
    return ext if ext in IMAGE_EXTS else ".jpg"


def find_example_image(folder: Path, index: int) -> Path | None:
    for ext in IMAGE_EXTS:
        path = folder / f"示例图_{index:02d}{ext}"
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


def normalize_image_ext(path: Path, fallback: str = ".jpg") -> Path:
    """按真实内容修正扩展名，返回最终路径（不改内容）。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return path
    real = image_ext(head, fallback or path.suffix)
    if path.suffix.lower() == real:
        return path
    target = path.with_suffix(real)
    if target.exists():
        return path
    try:
        os.replace(path, target)
        return target
    except OSError:
        return path
