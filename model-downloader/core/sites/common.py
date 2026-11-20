"""站点适配器共用的小工具：选文件、猜类型、清理说明文字。"""

from __future__ import annotations

import html
import re
from typing import Any

MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin")
EXT_RANK = {".safetensors": 0, ".ckpt": 1, ".pt": 2, ".pth": 2, ".bin": 3}
SKIP_NAMES = ("optimizer", "training_args", "scheduler", "tokenizer", "vocab", "merges")
COMPONENT_DIRS = (
    "vae/",
    "text_encoder/",
    "text_encoder_2/",
    "unet/",
    "transformer/",
    "tokenizer/",
    "scheduler/",
    "feature_extractor/",
    "safety_checker/",
)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def ext_of(path: str) -> str:
    name = str(path or "").replace("\\", "/").rsplit("/", 1)[-1]
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def pick_model_file(entries: list[dict[str, Any]], scope: str = "") -> dict[str, Any] | None:
    """从仓库文件列表里挑一个最像主模型的文件。

    优先根目录、safetensors；指定了子目录时只看该目录。
    """
    scope = str(scope or "").strip("/")
    candidates: list[dict[str, Any]] = []
    for item in entries:
        path = str(item.get("path") or "").replace("\\", "/").lstrip("/")
        if not path:
            continue
        if scope and not (path == scope or path.startswith(scope + "/")):
            continue
        ext = ext_of(path)
        if ext not in MODEL_EXTS:
            continue
        name = path.rsplit("/", 1)[-1].lower()
        if name.startswith(SKIP_NAMES):
            continue
        if not scope and "/" in path:
            continue
        if not scope and path.lower().startswith(COMPONENT_DIRS):
            continue
        candidates.append(
            {
                "path": path,
                "size": int(item.get("size") or 0),
                "sha256": str(item.get("sha256") or "").lower(),
            }
        )

    if not candidates and not scope:
        # 没有根目录模型时，再看一层子目录，但排除 diffusers 的分组件目录
        for item in entries:
            path = str(item.get("path") or "").replace("\\", "/").lstrip("/")
            if "/" not in path or path.lower().startswith(COMPONENT_DIRS):
                continue
            if ext_of(path) in MODEL_EXTS:
                candidates.append(
                    {
                        "path": path,
                        "size": int(item.get("size") or 0),
                        "sha256": str(item.get("sha256") or "").lower(),
                    }
                )
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (EXT_RANK.get(ext_of(item["path"]), 9), -item["size"], item["path"].lower())
    )
    return candidates[0]


def guess_model_type(
    path: str,
    title: str = "",
    tags: list[str] | None = None,
    hint: str = "",
) -> str:
    """按文件路径、标题、标签和站点类型猜内部模型类型。"""
    path_low = str(path or "").replace("\\", "/").lower()
    tag_low = " ".join(str(tag).lower() for tag in (tags or []))
    hint_low = str(hint or "").strip().lower()
    name = path_low.rsplit("/", 1)[-1]

    if "lora" in path_low or "lycoris" in path_low or "locon" in path_low or "lora" in tag_low:
        return "lora"
    if "controlnet" in path_low or "controlnet" in tag_low or hint_low == "controlnet":
        return "controlnet"
    if path_low.startswith("vae/") or name.startswith("vae") or hint_low == "vae":
        return "vae"
    if "text_encoder" in path_low or "clip" in path_low or "clip" in tag_low or hint_low == "clip":
        return "clip"
    if any(word in path_low for word in ("upscale", "esrgan", "swinir")) or any(
        word in tag_low for word in ("upscale", "esrgan", "swinir")
    ):
        return "upscale"
    if any(word in path_low for word in ("unet/", "diffusion_models/", "transformer/")):
        return "diffusion"
    if hint_low in ("diffusion", "unet"):
        return "diffusion"
    return "checkpoint"


def pick_base_model(value: Any, tags: list[str] | None = None) -> str:
    items: list[str] = []
    if isinstance(value, str):
        items.append(value)
    elif isinstance(value, list):
        items.extend(str(item) for item in value if item)
    elif isinstance(value, dict):
        items.extend(str(item) for item in value.values() if isinstance(item, str))
    for tag in tags or []:
        text = str(tag).strip()
        if text.lower().startswith("base_model:"):
            items.append(text.split(":")[-1])
    for item in items:
        text = item.strip()
        if text and text.lower() not in ("unknown", "none", "null"):
            return text
    return ""


def clean_text(text: Any, limit: int = 4000) -> str:
    raw = str(text or "")
    if not raw:
        return ""
    raw = re.sub(r"<script.*?</script>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<style.*?</style>", " ", raw, flags=re.I | re.S)
    raw = TAG_RE.sub(" ", raw)
    raw = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", raw)
    raw = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", raw)
    raw = html.unescape(raw)
    raw = SPACE_RE.sub(" ", raw).strip()
    return raw[:limit]


def pick_trigger_words(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[,，、\n]", value)
    elif isinstance(value, list):
        parts = value
    else:
        parts = []
    return [str(part).strip() for part in parts if str(part).strip()]
