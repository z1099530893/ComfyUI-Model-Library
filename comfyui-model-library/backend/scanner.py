"""只读扫描 ComfyUI 的模型目录。

数据来源：
1. ``folder_paths.folder_names_and_paths``——ComfyUI 自己的目录配置（含 extra_model_paths.yaml）；
2. 模型旁边的同名资料夹里的 ``model.json`` / ``模型使用说明.txt``；
3. 目录结构本身——类型目录下的第一层子目录当作「基础模型」分组。

插件只读取文件，不会写入、移动或删除任何模型。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from typing import Any

import folder_paths

MAX_IMAGES = 30
DESC_LIMIT = 6000
DOC_LIMIT = 20000

# ComfyUI 0.45 里可用的扩展名（以 folder_paths 为准，这里只做兜底）
DEFAULT_EXTS = {".safetensors", ".ckpt", ".pt", ".pt2", ".pth", ".bin", ".sft"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

SKIP_DIRS = {"__pycache__", ".git", ".svn", ".venv", "venv", "node_modules"}
SKIP_FILES = {"put_loras_here", "put_checkpoints_here", "put_vae_here", "put_clip_here", "put_unet_here"}

# 侧边栏展示的类型，顺序就是标签顺序；node/widget 为空表示只能浏览
TYPE_SPECS: list[dict[str, Any]] = [
    {
        "key": "checkpoints",
        "type": "checkpoint",
        "label": "基础模型",
        "node": "CheckpointLoaderSimple",
        "widget": "ckpt_name",
    },
    {
        "key": "loras",
        "type": "lora",
        "label": "LoRA",
        "node": "LoraLoader",
        "widget": "lora_name",
    },
    {
        "key": "diffusion_models",
        "type": "diffusion",
        "label": "扩散模型",
        "node": "UNETLoader",
        "widget": "unet_name",
    },
    {
        "key": "vae",
        "type": "vae",
        "label": "VAE",
        "node": "VAELoader",
        "widget": "vae_name",
    },
    {
        "key": "text_encoders",
        "type": "clip",
        "label": "文本编码器",
        "node": "CLIPLoader",
        "widget": "clip_name",
    },
    {"key": "controlnet", "type": "controlnet", "label": "ControlNet"},
    {
        "key": "upscale_models",
        "type": "upscale",
        "label": "放大模型",
        "node": "UpscaleModelLoader",
        "widget": "model_name",
    },
    {"key": "clip_vision", "type": "clip_vision", "label": "CLIP Vision"},
    {"key": "embeddings", "type": "embedding", "label": "Embedding"},
]
TYPE_ORDER = {spec["type"]: index for index, spec in enumerate(TYPE_SPECS)}

# 说明文本里的字段 → 内部字段
TXT_KEYS = {
    "模型名称": "title",
    "版本": "version_name",
    "基础模型": "base_model",
    "作者": "author",
    "发表时间": "published_at",
    "文件": "_file_name",
    "网页链接": "page_url",
    "触发词": "trigger_words",
    "标签": "tags",
    "下载时间": "downloaded_at",
}
EMPTY_VALUES = {"", "（无）", "(无)", "（未知）", "(未知)", "无", "未知"}

_lock = threading.RLock()
_cache: dict[str, Any] = {"models": [], "index": {}, "scanned_at": 0.0, "roots": []}


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def get_models(force: bool = False) -> dict[str, Any]:
    """返回扫描结果缓存；第一次调用或 force=True 时重新扫描。"""
    with _lock:
        if force or _cache["scanned_at"] <= 0:
            _refresh_locked()
        return _cache


def get_entry(model_id: str) -> dict[str, Any] | None:
    """按 id 取单个模型（包含绝对路径等内部字段）。"""
    with _lock:
        if _cache["scanned_at"] <= 0:
            _refresh_locked()
        return _cache["index"].get(model_id)


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #
def _refresh_locked() -> None:
    started = time.perf_counter()
    models: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    roots: list[dict[str, str]] = []
    seen: set[str] = set()

    for spec in TYPE_SPECS:
        folder_key = spec["key"]
        if folder_key not in folder_paths.folder_names_and_paths:
            continue
        dirs, exts = folder_paths.folder_names_and_paths[folder_key]
        exts = {str(ext).lower() for ext in exts}
        exts = {ext for ext in exts if ext in DEFAULT_EXTS} or set(DEFAULT_EXTS)
        for root in dirs:
            try:
                root = os.path.abspath(root)
            except OSError:
                continue
            if not os.path.isdir(root):
                continue
            roots.append({"type": spec["type"], "label": spec["label"], "path": root})
            try:
                found = _walk_root(root, spec, exts)
            except OSError:
                continue
            for model in found:
                key = os.path.normcase(model["path"])
                if key in seen:
                    continue
                seen.add(key)
                models.append(model)
                index[model["id"]] = model

    models.sort(
        key=lambda m: (
            TYPE_ORDER.get(m["type"], 99),
            m["base"].lower(),
            m["name"].lower(),
        )
    )
    _cache.update(
        models=models,
        index=index,
        scanned_at=time.time(),
        roots=roots,
    )
    elapsed = time.perf_counter() - started
    print(f"[ModelLibrary] 扫描完成：{len(models)} 个模型，用时 {elapsed:.2f}s")


def _walk_root(root: str, spec: dict[str, Any], exts: set[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    cache: dict[str, dict[str, Any]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".") and name not in SKIP_DIRS
        )
        names = [
            name
            for name in filenames
            if os.path.splitext(name)[1].lower() in exts and name.lower() not in SKIP_FILES
        ]
        if not names:
            continue

        by_file, child_metas = _index_children(dirpath, cache)
        for filename in sorted(names):
            full = os.path.join(dirpath, filename)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            if stat.st_size <= 0:
                continue

            asset_dir = _resolve_asset_dir(dirpath, filename, by_file)
            meta = child_metas.get(os.path.normcase(asset_dir))
            if meta is None:
                meta = _read_meta(asset_dir, cache)
                if not meta and os.path.normcase(asset_dir) != os.path.normcase(dirpath):
                    meta = _read_meta(dirpath, cache)

            found.append(_describe(full, stat, meta, asset_dir, root, spec))
    return found


def _index_children(folder: str, cache: dict[str, dict[str, Any]]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """为当前目录的每个子资料夹读一遍说明，便于处理「资料夹名 ≠ 模型名」的情况。"""
    by_file: dict[str, str] = {}
    metas: dict[str, dict[str, Any]] = {}
    try:
        children = sorted(os.listdir(folder))
    except OSError:
        return by_file, metas

    for child in children:
        path = os.path.join(folder, child)
        if not os.path.isdir(path) or child.startswith("."):
            continue
        meta = _read_meta(path, cache)
        metas[os.path.normcase(path)] = meta
        filename = _meta_file_name(meta)
        if filename:
            by_file.setdefault(filename.lower(), path)
    return by_file, metas


def _resolve_asset_dir(folder: str, filename: str, by_file: dict[str, str]) -> str:
    candidate = os.path.join(folder, os.path.splitext(filename)[0])
    if os.path.isdir(candidate):
        return candidate
    matched = by_file.get(filename.lower())
    if matched:
        return matched
    return folder


# --------------------------------------------------------------------------- #
# 说明文件
# --------------------------------------------------------------------------- #
def _read_meta(folder: str, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    key = os.path.normcase(folder)
    if key in cache:
        return cache[key]

    meta: dict[str, Any] = {}
    data = _load_json(folder)
    if data:
        for field in (
            "title",
            "version_name",
            "base_model",
            "author",
            "published_at",
            "description",
            "trigger_words",
            "tags",
            "page_url",
            "downloaded_at",
            "nsfw",
        ):
            if field in data:
                meta[field] = data[field]
        file_info = data.get("file")
        if isinstance(file_info, dict):
            meta["_file_name"] = str(file_info.get("name") or "")
        elif isinstance(file_info, str):
            meta["_file_name"] = file_info

    text = _read_doc(folder)
    if text:
        meta["doc"] = text[:DOC_LIMIT]
        for field, value in _parse_doc(text).items():
            meta.setdefault(field, value)

    meta["trigger_words"] = _as_list(meta.get("trigger_words"))
    meta["tags"] = _as_list(meta.get("tags"))
    meta["_has"] = bool(data or text)
    cache[key] = meta
    return meta


def _load_json(folder: str) -> dict[str, Any]:
    path = os.path.join(folder, "model.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_doc(folder: str) -> str:
    """读取说明文本：优先下载器写的说明文件，其次任意一个 txt。"""
    preferred = ("模型使用说明.txt", "说明.txt", "配置介绍.txt")
    names: list[str] = []
    try:
        existing = {name.lower(): name for name in os.listdir(folder)}
    except OSError:
        return ""

    for name in preferred:
        if name.lower() in existing:
            names.append(existing[name.lower()])
    if not names:
        txts = sorted(name for name in existing.values() if name.lower().endswith(".txt"))
        if txts:
            names.append(txts[0])

    for name in names:
        path = os.path.join(folder, name)
        for encoding in ("utf-8-sig", "gbk"):
            try:
                with open(path, "r", encoding=encoding) as handle:
                    return handle.read()
            except (OSError, UnicodeDecodeError):
                continue
    return ""


def _parse_doc(text: str) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for line in text.splitlines():
        if "：" not in line:
            continue
        key, value = line.split("：", 1)
        field = TXT_KEYS.get(key.strip())
        if not field:
            continue
        value = value.strip()
        if value in EMPTY_VALUES:
            continue
        if field in ("trigger_words", "tags"):
            meta[field] = [item.strip() for item in re.split(r"[、,，]", value) if item.strip() and item.strip() not in EMPTY_VALUES]
        else:
            meta[field] = value
    return meta


def _meta_file_name(meta: dict[str, Any]) -> str:
    return str(meta.get("_file_name") or "").strip()


# --------------------------------------------------------------------------- #
# 单个模型
# --------------------------------------------------------------------------- #
def _describe(
    path: str,
    stat: os.stat_result,
    meta: dict[str, Any],
    asset_dir: str,
    root: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    name = os.path.splitext(os.path.basename(path))[0]
    images = _collect_images(asset_dir, path, meta)
    # 目录分组优先：用户自己的文件夹名最直观；平铺模型才回落到 model.json 的基础模型
    base = _base_from_path(path, root) or str(meta.get("base_model") or "").strip()

    return {
        "id": hashlib.sha1(os.path.normcase(path).encode("utf-8")).hexdigest()[:16],
        "path": path,
        "type": spec["type"],
        "type_label": spec["label"],
        "node": spec.get("node", ""),
        "widget": spec.get("widget", ""),
        "combo": os.path.relpath(path, root),
        "name": name,
        "file": os.path.basename(path),
        "base": base or "未分类",
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "images": images,
        "nsfw": _truthy(meta.get("nsfw")),
        "trigger": "、".join(meta.get("trigger_words") or []),
        "author": str(meta.get("author") or ""),
        "title": str(meta.get("title") or ""),
        "version": str(meta.get("version_name") or ""),
        "page_url": str(meta.get("page_url") or ""),
        "tags": [str(tag) for tag in (meta.get("tags") or [])],
        "desc": str(meta.get("description") or "")[:DESC_LIMIT],
        "doc": str(meta.get("doc") or "")[:DOC_LIMIT],
        "downloaded_at": str(meta.get("downloaded_at") or ""),
        "has_meta": bool(meta.get("_has")),
    }


def _base_from_path(path: str, root: str) -> str:
    try:
        relative = os.path.relpath(path, root)
    except ValueError:
        return ""
    parts = relative.split(os.sep)
    if len(parts) < 2:
        return ""
    first = parts[0]
    stem = os.path.splitext(os.path.basename(path))[0]
    # 旧布局：模型放在自己的同名文件夹里，这一层不是基础模型
    if first.lower() == stem.lower():
        return ""
    return first


def _collect_images(asset_dir: str, model_path: str, meta: dict[str, Any]) -> list[str]:
    try:
        names = sorted(os.listdir(asset_dir))
    except OSError:
        return []

    model_dir = os.path.dirname(model_path)
    stem = os.path.splitext(os.path.basename(model_path))[0].lower()
    only_model_images = (
        os.path.normcase(asset_dir) == os.path.normcase(model_dir)
        and os.path.basename(asset_dir).lower() != stem
    )

    images: list[str] = []
    for name in names:
        if os.path.splitext(name)[1].lower() not in IMAGE_EXTS:
            continue
        full = os.path.join(asset_dir, name)
        if not os.path.isfile(full):
            continue
        if only_model_images:
            image_stem = os.path.splitext(name)[0].lower()
            if not (
                image_stem == stem
                or name.lower().startswith(stem + ".")
                or image_stem.startswith(stem + "_")
            ):
                continue
        images.append(full)
        if len(images) >= MAX_IMAGES:
            break

    return _order_images(images, meta)


def _order_images(images: list[str], meta: dict[str, Any]) -> list[str]:
    order: dict[str, int] = {}
    for index, item in enumerate(meta.get("images") or []):
        base = os.path.basename(str(item))
        order.setdefault(base.lower(), index)
        order.setdefault(os.path.splitext(base)[0].lower(), index)

    def sort_key(path: str) -> tuple[int, int, str]:
        name = os.path.basename(path)
        stem = os.path.splitext(name)[0].lower()
        listed = order.get(name.lower(), order.get(stem, 10**6))
        priority = 0
        if stem.startswith("masonry"):
            priority = -2
        elif name.lower().startswith(("cover", "封面", "preview")):
            priority = -1
        return listed, priority, name.lower()

    return sorted(images, key=sort_key)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[、,，]", value) if item.strip() and item.strip() not in EMPTY_VALUES]
    return []


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}
