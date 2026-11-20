"""Hugging Face 适配器。

支持模型主页、tree 目录页、blob 文件页和 resolve 直链。
公开仓库直接解析；gated / 私有仓库需要用户在设置里填写 HF Token。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, unquote, urlparse

import requests

from ..library import MODEL_TYPES
from .common import clean_text, guess_model_type, pick_base_model, pick_model_file, pick_trigger_words

API_BASE = "https://huggingface.co"
USER_AGENT = "ModelDownloader/0.6 (+https://huggingface.co)"
FILE_MODES = {"blob", "resolve", "raw"}
UNSUPPORTED_PREFIXES = {"datasets", "spaces"}


class AuthError(Exception):
    """仓库需要登录或授权。"""


def matches(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "huggingface.co" or host.endswith(".huggingface.co")


def parse_url(url: str) -> dict[str, Any] | None:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if parts and parts[0] == "models":
        parts = parts[1:]
    if len(parts) < 2 or parts[0].lower() in UNSUPPORTED_PREFIXES:
        return None
    owner, repo = parts[0], parts[1]
    rest = parts[2:]
    revision = "main"
    mode = ""
    path = ""
    if rest and rest[0] in FILE_MODES and len(rest) >= 2:
        mode = rest[0]
        revision = rest[1]
        path = "/".join(rest[2:])
    elif rest and rest[0] == "tree" and len(rest) >= 2:
        mode = "tree"
        revision = rest[1]
        path = "/".join(rest[2:])
    return {
        "repo_id": f"{owner}/{repo}",
        "owner": owner,
        "repo": repo,
        "revision": revision or "main",
        "mode": mode,
        "path": path,
    }


def resolve(url: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    parsed = parse_url(url)
    if not parsed:
        return {
            "ok": False,
            "code": "unsupported",
            "message": "认不出这个 Hugging Face 链接。请使用模型主页、文件页或 resolve 直链。",
        }

    token = str((settings.get("site_tokens") or {}).get("huggingface") or "").strip()
    proxy = str(settings.get("proxy") or "").strip()
    repo_id = parsed["repo_id"]
    revision = parsed["revision"]

    try:
        info = _get_json(
            f"{API_BASE}/api/models/{quote(repo_id, safe='/')}?blobs=true&revision={quote(revision, safe='')}",
            token,
            proxy,
        )
        tree = _get_json(
            f"{API_BASE}/api/models/{quote(repo_id, safe='/')}/tree/{quote(revision, safe='')}?recursive=true",
            token,
            proxy,
        )
    except AuthError:
        return {
            "ok": False,
            "code": "auth",
            "site": "huggingface",
            "message": "这个 Hugging Face 仓库需要授权。请到「设置 → 站点凭证」填写 HF Token，然后重新解析。",
        }
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 404:
            return {"ok": False, "code": "not_found", "message": "Hugging Face 上找不到这个仓库或文件。"}
        return {"ok": False, "code": "network", "message": f"Hugging Face 返回 HTTP {status}，稍后再试。"}
    except requests.RequestException as exc:
        return {
            "ok": False,
            "code": "network",
            "message": f"连不上 Hugging Face：{exc.__class__.__name__}。国内网络通常需要代理，可在设置里填写。",
        }
    except (TypeError, ValueError):
        return {"ok": False, "code": "bad_data", "message": "Hugging Face 返回的数据无法识别，稍后再试。"}

    entries = _tree_entries(tree if isinstance(tree, list) else [])
    if not entries:
        entries = _sibling_entries(info)

    explicit_path = parsed["path"] if parsed["mode"] in ("blob", "resolve", "raw") else ""
    file = None
    if explicit_path:
        file = next((item for item in entries if item["path"] == explicit_path), None)
    else:
        scope = parsed["path"] if parsed["mode"] == "tree" else ""
        file = pick_model_file(entries, scope)
    if not file:
        return {
            "ok": False,
            "code": "no_file",
            "message": "这个仓库里没有找到可直接下载的模型文件（可能是 diffusers 分组件仓库或数据集）。",
        }

    path = str(file["path"])
    filename = path.rsplit("/", 1)[-1]
    tags = [str(tag) for tag in (info.get("tags") or []) if tag]
    card = info.get("cardData") if isinstance(info.get("cardData"), dict) else {}
    title = str(card.get("model_name") or info.get("model_name") or parsed["repo"] or filename)
    model_type = guess_model_type(path, title, tags, str(info.get("pipeline_tag") or ""))
    if model_type not in MODEL_TYPES:
        return {
            "ok": False,
            "code": "unsupported_type",
            "message": "暂不支持这个 Hugging Face 模型类型。目前支持基础模型、扩散模型、LoRA、VAE、CLIP 和放大模型。",
        }

    readme = _readme(repo_id, revision, token, proxy)
    triggers = []
    for key in ("instance_prompt", "trigger_words", "trained_words"):
        triggers.extend(pick_trigger_words(card.get(key)))
    base_model = pick_base_model(card.get("base_model"), tags)
    nsfw = any(str(tag).lower() in ("nsfw", "not-for-all-audiences", "explicit") for tag in tags)

    return {
        "ok": True,
        "site": "huggingface",
        "site_name": "Hugging Face",
        "has_token": bool(token),
        "model_id": repo_id,
        "version_id": revision,
        "page_url": f"{API_BASE}/{repo_id}",
        "title": title,
        "version_name": revision,
        "type": "Model",
        "model_type": model_type,
        "type_dir": MODEL_TYPES[model_type]["dir"],
        "base_model": base_model or "未知",
        "folder_name": title,
        "filename": filename,
        "download_url": _download_url(repo_id, revision, path),
        "size": int(file.get("size") or 0),
        "sha256": _sha256(file.get("sha256")),
        "author": str(info.get("author") or parsed["owner"]),
        "published_at": str(info.get("createdAt") or info.get("lastModified") or ""),
        "description": clean_text(readme),
        "trigger_words": list(dict.fromkeys(triggers)),
        "tags": tags,
        "nsfw": nsfw,
        "images": [],
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


def _get_text(url: str, token: str, proxy: str) -> str:
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = requests.get(url, headers=headers, timeout=(10, 30), proxies=proxies)
    if resp.status_code in (401, 403):
        raise AuthError()
    resp.raise_for_status()
    return resp.text


def _tree_entries(items: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        path = str(item.get("path") or "")
        if not path:
            continue
        lfs = item.get("lfs") if isinstance(item.get("lfs"), dict) else {}
        result.append(
            {
                "path": path,
                "size": int(item.get("size") or lfs.get("size") or 0),
                "sha256": str(lfs.get("oid") or ""),
            }
        )
    return result


def _sibling_entries(info: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in info.get("siblings") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("rfilename") or "")
        if not path:
            continue
        lfs = item.get("lfs") if isinstance(item.get("lfs"), dict) else {}
        result.append(
            {
                "path": path,
                "size": int(item.get("size") or lfs.get("size") or 0),
                "sha256": str(lfs.get("oid") or ""),
            }
        )
    return result


def _readme(repo_id: str, revision: str, token: str, proxy: str) -> str:
    try:
        text = _get_text(
            f"{API_BASE}/{repo_id}/raw/{quote(revision, safe='')}/README.md", token, proxy
        )
    except (AuthError, requests.RequestException, OSError):
        return ""
    if text.startswith("---"):
        parts = re.split(r"(?m)^---\s*$", text, maxsplit=2)
        if len(parts) >= 3:
            text = parts[2]
    return text


def _download_url(repo_id: str, revision: str, path: str) -> str:
    safe_path = "/".join(quote(part, safe="") for part in path.split("/"))
    return f"{API_BASE}/{repo_id}/resolve/{quote(revision, safe='')}/{safe_path}?download=true"


def _sha256(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"[0-9a-f]{64}", text) else ""
