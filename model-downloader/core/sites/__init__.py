"""站点适配器注册表。

每个适配器负责两件事：识别自己能不能处理这个链接，以及把链接解析成
一份统一的「下载计划」。上层（下载引擎、界面）只认计划，不认站点。

后续接入 HuggingFace / ModelScope 时，只需在这里注册新模块。
"""

from __future__ import annotations

from typing import Any

from . import civitai

ADAPTERS = {"civitai": civitai}


def resolve(url: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """把模型网页地址解析成统一的下载计划。"""
    settings = settings or {}
    url = (url or "").strip()
    if not url:
        return {"ok": False, "code": "empty", "message": "请先粘贴模型网页地址"}
    for adapter in ADAPTERS.values():
        if adapter.matches(url):
            return adapter.resolve(url, settings)
    return {
        "ok": False,
        "code": "unsupported",
        "message": "这个链接暂时不支持。当前版本只支持 Civitai 模型页链接。",
    }
