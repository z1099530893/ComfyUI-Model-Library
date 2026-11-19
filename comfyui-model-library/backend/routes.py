"""模型库的 HTTP 接口（挂在 ComfyUI 自带 aiohttp 服务上）。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

from aiohttp import web
from server import PromptServer  # ComfyUI 顶层 server 模块

from . import scanner, thumbs

routes = PromptServer.instance.routes


def _json(payload: dict, status: int = 200) -> web.Response:
    return web.json_response(
        payload,
        status=status,
        headers={"Cache-Control": "no-store"},
    )


def _image_version(model: dict, index: int) -> int:
    """用图片自身的修改时间做缓存版本，换图后浏览器能拿到新图。"""
    try:
        return int(os.stat(model["images"][index]).st_mtime)
    except (OSError, IndexError):
        return int(model["mtime"])


def _image_url(model: dict, index: int, thumb: bool = False) -> str:
    url = f"/model_library/image?id={model['id']}&i={index}&v={_image_version(model, index)}"
    return url if thumb else url + "&thumb=0"


def _public(model: dict) -> dict:
    keys = (
        "id",
        "type",
        "type_label",
        "node",
        "widget",
        "combo",
        "name",
        "file",
        "base",
        "size",
        "mtime",
        "nsfw",
        "trigger",
        "author",
        "title",
        "version",
        "page_url",
        "tags",
        "has_meta",
    )
    payload = {key: model[key] for key in keys}
    payload["count"] = len(model["images"])
    payload["cover"] = _image_url(model, 0, thumb=True) if model["images"] else ""
    return payload


def _detail(model: dict) -> dict:
    payload = _public(model)
    payload["desc"] = model["desc"]
    payload["doc"] = model["doc"]
    payload["downloaded_at"] = model["downloaded_at"]
    payload["images"] = [
        {
            "url": _image_url(model, index),
            "thumb": _image_url(model, index, thumb=True),
        }
        for index in range(len(model["images"]))
    ]
    return payload


def _summary(cache: dict) -> dict:
    models = cache["models"]
    counts: dict[str, int] = {}
    for model in models:
        counts[model["type"]] = counts.get(model["type"], 0) + 1

    types = [
        {"type": spec["type"], "label": spec["label"], "count": counts[spec["type"]]}
        for spec in scanner.TYPE_SPECS
        if counts.get(spec["type"])
    ]
    return {
        "models": [_public(model) for model in models],
        "types": types,
        "total": len(models),
        "scanned_at": cache["scanned_at"],
    }


async def _scan(force: bool = False) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: scanner.get_models(force))


@routes.get("/model_library/list")
async def model_library_list(request: web.Request) -> web.Response:
    force = request.query.get("refresh") in ("1", "true")
    cache = await _scan(force)
    return _json(_summary(cache))


@routes.post("/model_library/refresh")
async def model_library_refresh(request: web.Request) -> web.Response:
    cache = await _scan(True)
    return _json(_summary(cache))


@routes.get("/model_library/detail")
async def model_library_detail(request: web.Request) -> web.Response:
    model_id = request.query.get("id", "")
    entry = scanner.get_entry(model_id)
    if not entry:
        return _json({"error": "模型不存在，请刷新后重试"}, status=404)
    return _json({"model": _detail(entry)})


def _open_in_folder(path: str) -> None:
    if os.name == "nt":
        subprocess.Popen(f'explorer /select,"{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


@routes.post("/model_library/reveal")
async def model_library_reveal(request: web.Request) -> web.Response:
    model_id = request.query.get("id", "")
    entry = scanner.get_entry(model_id)
    if not entry or not os.path.isfile(entry["path"]):
        return _json({"error": "模型文件不存在"}, status=404)
    try:
        _open_in_folder(os.path.normpath(entry["path"]))
    except OSError as error:
        return _json({"error": f"打开文件夹失败：{error}"}, status=500)
    return _json({"ok": True})


@routes.get("/model_library/image")
async def model_library_image(request: web.Request) -> web.StreamResponse:
    model_id = request.query.get("id", "")
    try:
        index = int(request.query.get("i", "0"))
    except ValueError:
        index = 0

    entry = scanner.get_entry(model_id)
    if not entry:
        raise web.HTTPNotFound()

    images = entry["images"]
    if index < 0 or index >= len(images):
        raise web.HTTPNotFound()

    path = images[index]
    if not os.path.isfile(path):
        raise web.HTTPNotFound()

    headers = {"Cache-Control": "public, max-age=86400"}
    if request.query.get("thumb", "1") != "0":
        loop = asyncio.get_running_loop()
        stat = os.stat(path)
        thumb = await loop.run_in_executor(
            None, thumbs.get_thumb, path, stat.st_mtime, stat.st_size
        )
        if thumb is not None:
            return web.FileResponse(thumb, headers=headers)

    return web.FileResponse(path, headers=headers)
