"""Civitai 站点适配器。

只用官方公开 API：https://civitai.com/api/v1/models/{id}
公开模型不需要任何凭证；受限模型需要用户在「设置 → 站点凭证」里粘贴
自己的 Civitai API Key。我们不会代替用户登录，也不会保存账号密码。
"""

from __future__ import annotations

import html
import re
from typing import Any

import requests

from ..library import MODEL_TYPES

API_BASE = "https://civitai.com/api/v1"
USER_AGENT = "ModelDownloader/0.2 (+https://civitai.com)"

MODEL_PATH = re.compile(r"/models/(\d+)", re.I)
VERSION_QUERY = re.compile(r"[?&]modelVersionId=(\d+)", re.I)
DOWNLOAD_PATH = re.compile(r"/api/download/models/(\d+)", re.I)

# Civitai 的模型类型 → 内部模型类型（只保留基础模型 / 扩散模型 / LoRA / VAE / 放大模型）
CIVITAI_TYPES = {
    "LORA": "lora",
    "LoCon": "lora",
    "DoRA": "lora",
    "Checkpoint": "checkpoint",
    "Checkpoint Trained": "checkpoint",
    "VAE": "vae",
    "Upscaler": "upscale",
    "Other": "checkpoint",
}

# 文件名/标题里出现这些词，说明是纯扩散模型（UNet/DiT），而不是全家桶 checkpoint
DIFFUSION_HINTS = ("unet", "diffusion")

IMAGE_EXT = re.compile(r"\.(jpe?g|png|webp)$", re.I)
TAG_RE = re.compile(r"<[^>]+>")


class AuthError(Exception):
    """站点要求授权（401 / 403）。"""


def matches(url: str) -> bool:
    return "civitai.com" in url.lower() or "civitai.red" in url.lower()


def parse_url(url: str) -> dict[str, Any] | None:
    version = VERSION_QUERY.search(url)
    model = MODEL_PATH.search(url)
    if model:
        return {
            "model_id": int(model.group(1)),
            "version_id": int(version.group(1)) if version else None,
        }
    # 用户直接粘贴下载直链时，只能拿到版本号
    download = DOWNLOAD_PATH.search(url)
    if download:
        return {"model_id": None, "version_id": int(download.group(1))}
    return None


def resolve(url: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    parsed = parse_url(url)
    if not parsed:
        return {
            "ok": False,
            "code": "unsupported",
            "message": "认不出这个 Civitai 链接。请使用模型页地址，例如 https://civitai.com/models/3014612",
        }

    token = str((settings.get("site_tokens") or {}).get("civitai") or "").strip()
    proxy = str(settings.get("proxy") or "").strip()

    try:
        if parsed["version_id"] and not parsed["model_id"]:
            version = _get(f"/model-versions/{parsed['version_id']}", token, proxy)
            model = _get(f"/models/{version.get('modelId')}", token, proxy)
        else:
            model = _get(f"/models/{parsed['model_id']}", token, proxy)
            version = _pick_version(model, parsed["version_id"])
    except AuthError:
        return {
            "ok": False,
            "code": "auth",
            "site": "civitai",
            "message": "这个模型需要 Civitai 授权。请到「设置 → 站点凭证」粘贴你的 API Key，然后重新解析。",
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "code": "network",
            "message": f"连不上 Civitai：{exc.__class__.__name__}。国内网络通常需要代理，可在设置里填写。",
        }
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "code": "bad_data", "message": "Civitai 返回的数据无法识别，稍后再试。"}

    if not version:
        return {"ok": False, "code": "no_version", "message": "这个模型还没有发布任何版本。"}

    file = _pick_file(version)
    if not file:
        return {
            "ok": False,
            "code": "no_file",
            "message": "这个版本没有可直接下载的模型文件（可能是训练数据集或工作流）。",
        }

    site_type = str(model.get("type") or "Other")
    base_model = str(version.get("baseModel") or "")
    title = str(model.get("name") or file.get("name") or "未命名模型")
    filename = str(file.get("name") or "")
    if not filename:
        filename = f"{title}.safetensors"

    model_type = _model_type(site_type, filename, title)
    if not model_type:
        return {
            "ok": False,
            "code": "unsupported_type",
            "message": (
                f"暂不支持「{site_type}」类型的模型。"
                "目前支持：基础模型、扩散模型、LoRA、VAE、CLIP 文本编码器、放大模型。"
            ),
        }

    images = _pick_images(version)
    version_id = int(version.get("id") or 0)
    model_id = int(model.get("id") or 0)

    return {
        "ok": True,
        "site": "civitai",
        "site_name": "Civitai",
        "has_token": bool(token),
        "model_id": model_id,
        "version_id": version_id,
        "page_url": _page_url(model_id, version_id),
        "title": title,
        "version_name": str(version.get("name") or ""),
        "type": site_type,
        "model_type": model_type,
        "type_dir": MODEL_TYPES[model_type]["dir"],
        "base_model": base_model or "未知",
        "folder_name": title,
        "filename": filename,
        "download_url": str(file.get("downloadUrl") or ""),
        "size": int(float(file.get("sizeKB") or 0) * 1024),
        "sha256": str((file.get("hashes") or {}).get("SHA256") or "").lower(),
        "author": str((model.get("creator") or {}).get("username") or ""),
        "published_at": str(version.get("publishedAt") or model.get("createdAt") or ""),
        "description": _clean_text(model.get("description") or version.get("description") or ""),
        "trigger_words": [str(w).strip() for w in (version.get("trainedWords") or []) if str(w).strip()],
        "tags": [str(t) for t in (model.get("tags") or []) if t],
        "nsfw": bool(model.get("nsfw")) or any(img["nsfw"] for img in images),
        "images": images,
    }


def resolve_hash(sha256: str, settings: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """按 SHA256 反查 Civitai 上的模型版本。

    找不到（404 / 私有模型）返回 None，由调用方写入基础资料；
    授权失败抛 AuthError，网络问题抛 requests.RequestException。
    """
    settings = settings or {}
    digest = str(sha256 or "").strip().lower()
    if len(digest) != 64:
        return None
    token = str((settings.get("site_tokens") or {}).get("civitai") or "").strip()
    proxy = str(settings.get("proxy") or "").strip()

    try:
        version = _get(f"/model-versions/by-hash/{digest}", token, proxy)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 404:
            return None
        raise

    model: dict[str, Any] = version.get("model") if isinstance(version.get("model"), dict) else {}
    if not model.get("name"):
        model_id = int(version.get("modelId") or 0)
        if model_id:
            try:
                model = _get(f"/models/{model_id}", token, proxy)
            except requests.HTTPError:
                model = {}

    local_name = str(version.get("name") or "")
    file_name = ""
    for item in version.get("files") or []:
        if isinstance(item, dict) and str(item.get("name") or "").lower().endswith(".safetensors"):
            file_name = str(item.get("name") or "")
            break

    site_type = str(model.get("type") or "Other")
    title = str(model.get("name") or "未命名模型")
    version_id = int(version.get("id") or 0)
    model_id = int(version.get("modelId") or model.get("id") or 0)

    return {
        "ok": True,
        "site": "civitai",
        "site_name": "Civitai",
        "model_id": model_id,
        "version_id": version_id,
        "page_url": _page_url(model_id, version_id),
        "title": title,
        "version_name": local_name,
        "type": site_type,
        "model_type": _model_type(site_type, file_name or title, title),
        "base_model": str(version.get("baseModel") or ""),
        "author": str((model.get("creator") or {}).get("username") or ""),
        "published_at": str(version.get("publishedAt") or model.get("createdAt") or ""),
        "description": _clean_text(model.get("description") or version.get("description") or ""),
        "trigger_words": [
            str(w).strip() for w in (version.get("trainedWords") or []) if str(w).strip()
        ],
        "tags": [str(t) for t in (model.get("tags") or []) if t],
        "nsfw": bool(model.get("nsfw"))
        or any(bool(img.get("nsfw")) for img in (version.get("images") or []) if isinstance(img, dict)),
        "images": _pick_images(version),
        "sha256": digest,
    }


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------

def _get(path: str, token: str, proxy: str) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = requests.get(API_BASE + path, headers=headers, timeout=30, proxies=proxies)
    if resp.status_code in (401, 403):
        raise AuthError(path)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("unexpected payload")
    return data


def _pick_version(model: dict[str, Any], version_id: int | None) -> dict[str, Any] | None:
    versions = model.get("modelVersions") or []
    if not isinstance(versions, list) or not versions:
        return None
    if version_id:
        for item in versions:
            if int(item.get("id") or 0) == version_id:
                return item
        return None
    return versions[0]


def _pick_file(version: dict[str, Any]) -> dict[str, Any] | None:
    files = [f for f in (version.get("files") or []) if isinstance(f, dict) and f.get("downloadUrl")]
    if not files:
        return None
    usable = [
        f
        for f in files
        if str(f.get("type") or "Model") in ("Model", "Pruned Model", "Archive")
        and str(f.get("name") or "").lower().endswith((".safetensors", ".ckpt", ".pt", ".pth", ".bin"))
    ]
    pool = usable or files
    safetensors = [f for f in pool if str(f.get("name") or "").lower().endswith(".safetensors")]
    if safetensors:
        pool = safetensors
    primary = [f for f in pool if f.get("primary")]
    return (primary or pool)[0]


def _pick_images(version: dict[str, Any]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for item in version.get("images") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "image") != "image":
            continue
        url = str(item.get("url") or "")
        if not url or not IMAGE_EXT.search(url.split("?")[0]):
            continue
        images.append(
            {
                "url": url,
                "nsfw": bool(item.get("nsfw")),
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
            }
        )
        if len(images) >= 12:
            break
    return images


def _model_type(site_type: str, filename: str, title: str) -> str:
    """Civitai 类型 → 内部模型类型；返回空串表示暂不支持。"""
    key = CIVITAI_TYPES.get(site_type)
    if key == "checkpoint":
        # Civitai 不区分全家桶和纯 UNet，只能靠文件名兜底；用户可在预览里改
        text = f"{filename} {title}".lower()
        if any(hint in text for hint in DIFFUSION_HINTS):
            return "diffusion"
    return key or ""


def _page_url(model_id: int, version_id: int) -> str:
    if model_id:
        url = f"https://civitai.com/models/{model_id}"
        if version_id:
            url += f"?modelVersionId={version_id}"
        return url
    return f"https://civitai.com/api/download/models/{version_id}"


def _clean_text(raw: str) -> str:
    text = html.unescape(str(raw or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|h\d)>", "\n", text, flags=re.I)
    text = TAG_RE.sub("", text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
