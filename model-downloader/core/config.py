"""应用配置的读写。

配置是"本机偏好"，不是模型数据；模型数据永远以文件系统为准。
默认存放在 %LOCALAPPDATA%\\ModelDownloader\\config.json，方便整个程序目录拷贝到别的电脑使用。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "default_download_dir": "",
    "recent_dirs": [],
    "library_dirs": [],
    "comfy_dirs": [],
    "link_mode": "auto",
    "auto_link": True,
    "card_scale": 1,
    "library_view": "grid",
    "library_sort": "name",
    "library_sort_desc": False,
    "site_tokens": {"civitai": "", "huggingface": ""},
    "proxy": "",
    "blur_nsfw": True,
    "scan_on_startup": True,
}

MAX_RECENT_DIRS = 8


def config_path() -> Path:
    override = os.environ.get("MODEL_DOWNLOADER_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "ModelDownloader" / "config.json"


class Config:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_path()
        self.data: dict[str, Any] = json.loads(json.dumps(DEFAULT_SETTINGS))
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            for key, default in DEFAULT_SETTINGS.items():
                value = raw.get(key, default)
                if isinstance(default, dict) and isinstance(value, dict):
                    merged = dict(default)
                    merged.update(value)
                    self.data[key] = merged
                elif isinstance(default, list) and isinstance(value, list):
                    self.data[key] = value
                else:
                    self.data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, self.path)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        for key in DEFAULT_SETTINGS:
            if key not in patch:
                continue
            default = DEFAULT_SETTINGS[key]
            value = patch[key]
            if isinstance(default, dict) and isinstance(value, dict):
                self.data[key].update(value)
            elif isinstance(default, list) and isinstance(value, list):
                self.data[key] = value[:200]
            else:
                self.data[key] = value
        self.save()
        return self.data

    def remember_dir(self, path: str) -> list[str]:
        path = os.path.abspath(path)
        recent = [p for p in self.data["recent_dirs"] if os.path.normcase(p) != os.path.normcase(path)]
        recent.insert(0, path)
        self.data["recent_dirs"] = recent[:MAX_RECENT_DIRS]
        self.data["default_download_dir"] = path
        self.save()
        return self.data["recent_dirs"]
