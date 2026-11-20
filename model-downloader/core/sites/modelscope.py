"""ModelScope（魔搭）适配器。

支持模型主页、文件页和 resolve 直链。公开模型直接解析；
私有模型可填写 ModelScope Token。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

from ..library import MODEL_TYPES
from .common import clean_text, guess_model_type, pick_base_model, pick_model_file, pick_trigger_words

API_BASE = "https://modelscope.cn"
USER_AGENT = "ModelDownloader/0.6 (+https://modelscope.cn)"
TYPE_HINTS = {
    "lora": "lora",
    "lycoris": "lora",
    "vae": "vae",
    "unet": "diffusion",
    "diffusion": "diffusion",
    "controlnet": "controlnet",
    "text_encoder": "clip",
    "clip": "clip",
    "upscaler": "upscale",
    "upscale": "upscale",
}


class AuthError(Exception):
    """模型需要登录或授权。"""


def matches(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "modelscope.cn" or host.endswith(".modelscope.cn")


def parse_url(url: str) -> dict[str, Any] | None:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if not parts or parts[0] != "models" or len(parts) < 3:
        return None
    namespace, name = parts[1], parts[2]
    rest = parts[3:]
    query = parse_qs(parsed.query)
    revision = (query.get("Revision") or query.get("revision") or ["master"])[0] or "master"
    has_revision = bool(query.get("Revision") or query.get("revision"))
    path = ""
    if rest:
        if rest[0] == "resolve" and len(rest) >= 2:
            revision = rest[1]
            has_revision = True
            path = "/".join(rest[2:])
        elif rest[0] == "file" and len(rest) >= 3:
            has_revision = True
            if rest[1] == "view":
                revision = rest[2]
                path = "/".join(rest[3:])
            else:
                revision = rest[1]
                path = "/".join(rest[2:])
    return {
        "repo_id": f"{namespace}/{name}",
        "namespace": namespace,
        "name": name,
        "revision": revision or "master",
        "has_revision": has_revision,
        "path": path,
    }


def resolve(url: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    parsed = parse_url(url)
    if not parsed:
        return {
            "ok": False,
            "code": "unsupported",
            "message": "认不出这个 ModelScope 链接。请使用模型主页、文件页或 resolve 直链。",
        }

    token = str((settings.get("site_tokens") or {}).get("modelscope") or "").strip()
    proxy = str(settings.get("proxy") or "").strip()
    repo_id = parsed["repo_id"]
    revision = parsed["revision"]

    try:
        info = _get_json(
            f"{API_BASE}/api/v1/models/{quote(repo_id, safe='/')}", token, proxy
        )
        data = info.get("Data") if isinstance(info.get("Data"), dict) else {}
        files = _list_files(repo_id, revision, token, proxy)
    except AuthError:
        return {
            "ok": False,
            "code": "auth",
            "site": "modelscope",
            "message": "这个 ModelScope 模型需要授权。请到「设置 → 站点凭证」填写 ModelScope Token，然后重新解析。",
        }
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 404:
            return {"ok": False, "code": "not_found", "message": "ModelScope 上找不到这个模型或文件。"}
        return {"ok": False, "code": "network", "message": f"ModelScope 返回 HTTP {status}，稍后再试。"}
    except requests.RequestException as exc:
        return {
            "ok": False,
            "code": "network",
            "message": f"连不上 ModelScope：{exc.__class__.__name__}。检查网络或代理后再试。",
        }
    except (TypeError, ValueError):
        return {"ok": False, "code": "bad_data", "message": "ModelScope 返回的数据无法识别，稍后再试。"}

    if not data:
        return {"ok": False, "code": "bad_data", "message": "ModelScope 没有返回模型资料，稍后再试。"}

    explicit_path = parsed["path"]
    file = None
    if explicit_path:
        file = next((item for item in files if item["path"] == explicit_path), None)
    else:
        file = pick_model_file(files)
    if not file:
        return {
            "ok": False,
            "code": "no_file",
            "message": "这个模型仓库里没有找到可直接下载的模型文件。",
        }

    path = str(file["path"])
    filename = path.rsplit("/", 1)[-1]
    title = str(data.get("ChineseName") or data.get("Name") or parsed["name"] or filename)
    tags = [str(tag) for tag in (data.get("Tags") or []) if tag]
    hint = str(data.get("ModelType") or "").lower()
    model_type = TYPE_HINTS.get(hint) or guess_model_type(path, title, tags, hint)
    if model_type not in MODEL_TYPES:
        return {
            "ok": False,
            "code": "unsupported_type",
            "message": "暂不支持这个 ModelScope 模型类型。目前支持基础模型、扩散模型、LoRA、VAE、CLIP 和放大模型。",
        }

    if not parsed["has_revision"]:
        # URL 没指定版本时才采用仓库的默认分支，避免把显式分支覆盖成 master
        revision = str(data.get("Revision") or revision or "master")
    triggers = pick_trigger_words(data.get("TriggerWords"))
    base_model = pick_base_model(data.get("BaseModel"), tags)
    author = ""
    organization = data.get("Organization")
    if isinstance(organization, dict):
        author = str(organization.get("Name") or organization.get("FullName") or "")
    if not author and isinstance(data.get("CreatedBy"), dict):
        author = str(data["CreatedBy"].get("Name") or "")

    return {
        "ok": True,
        "site": "modelscope",
        "site_name": "ModelScope",
        "has_token": bool(token),
        "model_id": repo_id,
        "version_id": revision,
        "page_url": f"{API_BASE}/models/{repo_id}",
        "title": title,
        "version_name": revision,
        "type": str(data.get("ModelType") or "Model"),
        "model_type": model_type,
        "type_dir": MODEL_TYPES[model_type]["dir"],
        "base_model": base_model or "未知",
        "folder_name": title,
        "filename": filename,
        "download_url": _download_url(repo_id, revision, path),
        "size": int(file.get("size") or 0),
        "sha256": _sha256(file.get("sha256")),
        "author": author,
        "published_at": _timestamp(data.get("CreatedTime") or data.get("LastUpdatedTime")),
        "description": clean_text(data.get("Description") or data.get("ReadMeContent") or ""),
        "trigger_words": triggers,
        "tags": tags,
        "nsfw": any(str(tag).lower() in ("nsfw", "not-for-all-audiences") for tag in tags),
        "images": _cover_images(data.get("CoverImages")),
    }


def _get_json(url: str, token: str, proxy: str) -> Any:
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = requests.get(url, headers=headers, timeout=(15, 45), proxies=proxies)
    if resp.status_code in (401, 403):
        raise AuthError()
    resp.raise_for_status()
    return resp.json()


def _list_files(repo_id: str, revision: str, token: str, proxy: str) -> list[dict[str, Any]]:
    payload = _get_json(
        f"{API_BASE}/api/v1/models/{quote(repo_id, safe='/')}/repo/files"
        f"?Revision={quote(revision, safe='')}&Recursive=true",
        token,
        proxy,
    )
    data = payload.get("Data") if isinstance(payload.get("Data"), dict) else {}
    result: list[dict[str, Any]] = []
    for item in data.get("Files") or []:
        if not isinstance(item, dict) or item.get("Type") != "blob":
            continue
        path = str(item.get("Path") or item.get("Name") or "").replace("\\", "/")
        if not path:
            continue
        result.append(
            {
                "path": path,
                "size": int(item.get("Size") or 0),
                "sha256": str(item.get("Sha256") or ""),
            }
        )
    return result


def _download_url(repo_id: str, revision: str, path: str) -> str:
    safe_path = "/".join(quote(part, safe="") for part in path.split("/"))
    return f"{API_BASE}/models/{repo_id}/resolve/{quote(revision, safe='')}/{safe_path}"


def _sha256(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"[0-9a-f]{64}", text) else ""


def _timestamp(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    if number > 10_000_000_000:
        number //= 1000
    try:
        return datetime.fromtimestamp(number).astimezone().isoformat(timespec="seconds")
    except (OSError, ValueError):
        return ""


def _cover_images(value: Any) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        url = ""
        if isinstance(item, str):
            url = item
        elif isinstance(item, dict):
            url = str(item.get("Url") or item.get("URL") or item.get("url") or "")
        if url:
            images.append({"url": url, "nsfw": False, "width": 0, "height": 0})
        if len(images) >= 8:
            break
    return images
