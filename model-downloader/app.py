"""模型下载器 - 程序入口。

M1：接入 Civitai 公开模型全链路（解析 → 元数据 → 断点续传 → 归档）。
HuggingFace、ModelScope 与模型库扫描在后续里程碑接入。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sys
import time
import traceback
from ctypes import wintypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

if getattr(sys, "frozen", False):
    # PyInstaller onedir：web / assets 随程序放在 _internal 下
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
sys.path.insert(0, str(ROOT))

from core import library, scanner, sites  # noqa: E402
from core.config import Config  # noqa: E402
from core.downloader import Downloader  # noqa: E402
from core.organizer import Organizer  # noqa: E402

APP_NAME = "模型下载器"
APP_VERSION = "0.4.2"

_ICON_HANDLES: list[int] = []


def _apply_window_icons(title: str) -> bool:
    """给 Windows 标题栏和任务栏分别设置图标。

    pywebview 在 Windows 上不支持 icon= 参数，只能等窗口创建后发 WM_SETICON。
    标题栏用灰云剪影，任务栏 / Alt-Tab 用黑底下载箭头。
    """
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.SendMessageW.restype = ctypes.c_ssize_t

    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False

    WM_SETICON = 0x0080
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    # 0 = 小图标（标题栏），1 = 大图标（任务栏 / Alt-Tab）
    metrics = {
        0: (user32.GetSystemMetrics(49), user32.GetSystemMetrics(50)),
        1: (user32.GetSystemMetrics(11), user32.GetSystemMetrics(12)),
    }
    applied = False
    for kind, filename in ((0, "titlebar.ico"), (1, "app.ico")):
        path = ROOT / "assets" / filename
        if not path.is_file():
            continue
        cx, cy = metrics[kind]
        handle = user32.LoadImageW(None, str(path), IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
        if not handle:
            continue
        user32.SendMessageW(hwnd, WM_SETICON, kind, handle)
        _ICON_HANDLES.append(handle)
        applied = True
    return applied


def _disk_info(path: str) -> dict[str, Any]:
    target = path or os.path.abspath(os.sep)
    try:
        usage = shutil.disk_usage(target)
        return {"ok": True, "total": usage.total, "free": usage.free}
    except OSError:
        return {"ok": False, "total": 0, "free": 0}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _send_to_recycle_bin(paths: list[str]) -> None:
    """批量送进回收站（不是永久删除），超长路径自动加 \\\\?\\ 前缀。"""
    resolved: list[str] = []
    for raw in paths:
        item = os.path.abspath(raw)
        if len(item) > 250 and not item.startswith("\\\\?\\"):
            item = "\\\\?\\" + item
        resolved.append(item)
    joined = "\0".join(resolved) + "\0\0"
    op = _SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = joined
    op.fFlags = 0x40 | 0x10 | 0x4 | 0x400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code != 0:
        raise OSError(f"回收站操作失败（错误码 {code}）")


def _doc_mentions(path: Path, filename: str) -> bool:
    """说明文件里的「文件：xxx」是否指向这个模型（用来认出手动改名后的资料夹）。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return False
    wanted = filename.strip().lower()
    for line in text.splitlines():
        if line.startswith("文件：") and line[3:].strip().lower() == wanted:
            return True
    return False


class Api:
    """暴露给前端 JavaScript 的本机能力。"""

    def __init__(self) -> None:
        self.config = Config()
        # pywebview 会递归枚举 js_api 的公开属性，窗口对象必须保持私有，
        # 否则会递归进 .NET 原生控件并触发 RecursionError。
        self._window = None
        self._asset_base = ""
        self.config._serializable = False
        self._downloader = Downloader(
            self.config.path.parent / "tasks.json",
            lambda: self.config.data,
        )
        self._organizer = Organizer(
            self.config.path.parent / "hashes.json",
            lambda: self.config.data,
        )

    # --- 基础信息 ---
    def get_state(self) -> dict[str, Any]:
        settings = self.config.data
        probe = settings.get("default_download_dir") or ""
        return {
            "app": {"name": APP_NAME, "version": APP_VERSION},
            "settings": settings,
            "disk": _disk_info(probe),
            "config_path": str(self.config.path),
            "cache": self._organizer.info(),
        }

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        return {"settings": self.config.update(patch or {})}

    # --- 解析与下载 ---
    def parse_url(self, url: str, directory: str = "") -> dict[str, Any]:
        """只解析，不下载。返回统一的下载计划供界面预览。"""
        try:
            result = sites.resolve(url, self.config.data)
        except Exception as exc:  # noqa: BLE001 - 站点异常不能把界面打崩
            return {"ok": False, "code": "internal", "message": f"解析失败：{exc}"}
        if not result.get("ok"):
            return result
        target_dir = (directory or self.config.data.get("default_download_dir") or "").strip()
        if target_dir:
            result.update(library.plan_target(target_dir, result))
        return result

    def create_task(self, plan: dict[str, Any], directory: str = "") -> dict[str, Any]:
        if not isinstance(plan, dict) or not plan.get("ok") or not plan.get("download_url"):
            return {"ok": False, "message": "下载计划无效，请重新解析链接"}
        target_dir = (directory or self.config.data.get("default_download_dir") or "").strip()
        if not target_dir:
            return {"ok": False, "message": "请先选择保存目录"}

        # 用户只能改模型名：文件夹名、文件名都跟着它走（中文也可以）
        title = library.safe_name(plan.get("title") or "", "未命名模型")
        original = str(plan.get("filename") or "")
        ext = Path(original).suffix or ".safetensors"
        plan = dict(plan)
        plan["model_type"] = library.normalize_type(plan.get("model_type") or plan.get("type_dir"))
        plan["type_dir"] = library.MODEL_TYPES[plan["model_type"]]["dir"]
        plan["title"] = title
        plan["folder_name"] = title
        plan["filename"] = f"{title}{ext}"

        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "message": f"无法创建目录：{exc}"}
        self.config.remember_dir(target_dir)
        task = self._downloader.add(plan, target_dir)
        return {"ok": True, "task": task, "settings": self.config.data}

    def get_tasks(self) -> dict[str, Any]:
        return {"ok": True, "tasks": self._downloader.snapshot()}

    def task_action(self, task_id: str, action: str) -> dict[str, Any]:
        return self._downloader.action(str(task_id), str(action))

    def clear_done(self) -> dict[str, Any]:
        return self._downloader.clear_done()

    def rename_task(self, task_id: str, title: str, folder: str, filename: str) -> dict[str, Any]:
        title = library.safe_name(title or "", "未命名模型")
        original = str(filename or "")
        ext = Path(original).suffix or ".safetensors"
        return self._downloader.rename(str(task_id), title, title, f"{title}{ext}")

    # --- 模型库：编辑与删除 ---
    def update_model(self, model_path: str, patch: dict[str, Any]) -> dict[str, Any]:
        """改模型名（文件、同名资料夹、说明一起改），也允许单个模型改类型。"""
        src = Path(str(model_path or ""))
        if not src.is_file() or src.suffix.lower() not in library.MODEL_EXTS:
            return {"ok": False, "message": "找不到模型文件"}
        if not self._is_allowed(src):
            return {"ok": False, "message": "这个文件不在模型库扫描范围内"}

        old_type = ""
        type_anchor: Path | None = None
        for parent in [src.parent, *src.parent.parents]:
            key = library.TYPE_DIR_TO_TYPE.get(parent.name.lower())
            if key:
                old_type = key
                type_anchor = parent
                break
        if not type_anchor:
            return {"ok": False, "message": "该文件不在支持的类型目录中，暂不支持编辑"}

        for task in self._downloader.snapshot():
            if task.get("path", "").lower() == str(src).lower() and task.get("status") in (
                "queued",
                "running",
                "paused",
                "verifying",
            ):
                return {"ok": False, "message": "这个模型还有未完成的下载任务，不能编辑"}

        patch = patch or {}
        requested = str(patch.get("model_type") or "").strip().lower()
        if requested and requested not in library.MODEL_TYPES:
            return {"ok": False, "message": "未知的模型类型"}
        new_type = requested or old_type
        new_type_dir = library.MODEL_TYPES[new_type]["dir"]

        new_title = library.safe_name(str(patch.get("title") or src.stem), src.stem)
        models_root = type_anchor.parent
        new_base = models_root / new_type_dir
        new_path = new_base / f"{new_title}{src.suffix}"
        legacy_dir = src.parent if src.parent.name.lower() == src.stem.lower() else None
        old_asset = src.parent / src.stem
        new_asset = new_base / new_title
        has_assets = legacy_dir is not None or old_asset.is_dir()

        same_file = os.path.normcase(str(new_path)) == os.path.normcase(str(src))
        same_asset = os.path.normcase(str(new_asset)) == os.path.normcase(str(old_asset))
        if new_path.exists() and not same_file:
            return {"ok": False, "message": f"目标位置已有同名文件：{new_path.name}"}
        if new_asset.exists() and not same_asset:
            return {"ok": False, "message": f"目标位置已有同名文件夹：{new_asset.name}"}

        meta = _read_json((legacy_dir if legacy_dir is not None else old_asset) / "model.json") or {}

        # 先改文件，再改资料夹；资料夹失败就回滚文件名
        moved_file = False
        if str(new_path) != str(src):
            try:
                new_base.mkdir(parents=True, exist_ok=True)
                if same_file:
                    tmp = src.with_name(src.name + ".renaming")
                    os.replace(src, tmp)
                    os.replace(tmp, new_path)
                else:
                    os.replace(src, new_path)
                moved_file = True
            except OSError as exc:
                return {"ok": False, "message": f"重命名失败：{exc}"}

        if has_assets:
            source_asset = legacy_dir if legacy_dir is not None else old_asset
            if str(source_asset) != str(new_asset):
                try:
                    new_base.mkdir(parents=True, exist_ok=True)
                    if same_asset:
                        tmp = source_asset.with_name(source_asset.name + ".renaming")
                        os.replace(source_asset, tmp)
                        os.replace(tmp, new_asset)
                    else:
                        os.replace(source_asset, new_asset)
                except OSError as exc:
                    if moved_file:
                        try:
                            os.replace(new_path, src)
                        except OSError:
                            pass
                    return {"ok": False, "message": f"资料夹改名失败：{exc}"}

        triggers = patch.get("trigger_words")
        if triggers is None:
            triggers = meta.get("trigger_words") or []
        elif isinstance(triggers, str):
            triggers = [t.strip() for t in triggers.replace("，", ",").split(",") if t.strip()]
        else:
            triggers = [str(t).strip() for t in triggers if str(t).strip()]

        # 站点标题是资料，不是显示名：改名时只动文件，不覆盖原始标题
        site_title = str(meta.get("title") or "").strip() if meta.get("site") else ""
        plan = dict(meta)
        plan.update(
            {
                "title": site_title or new_title,
                "version_name": str(patch.get("version_name", meta.get("version_name") or "")),
                "model_type": new_type,
                "type_dir": new_type_dir,
                "base_model": str(patch.get("base_model", meta.get("base_model") or "")),
                "trigger_words": triggers,
                "description": str(patch.get("description", meta.get("description") or "")),
                "nsfw": bool(patch.get("nsfw", meta.get("nsfw") or False)),
                "filename": new_path.name,
                "model_path": str(new_path),
                "size": new_path.stat().st_size if new_path.exists() else int(meta.get("size") or 0),
            }
        )
        if new_asset.is_dir():
            names = sorted(p.name for p in new_asset.glob("示例图_*"))
            library.write_docs(
                new_asset,
                plan,
                str(meta.get("sha256") or ""),
                names,
                downloaded_at=meta.get("downloaded_at"),
            )
        return {"ok": True, "path": str(new_path), "dir": str(new_base)}

    def delete_models(self, paths: list[str]) -> dict[str, Any]:
        """把模型文件连同它自己的资料夹送进回收站。"""
        if not isinstance(paths, list) or not paths:
            return {"ok": False, "message": "没有选中任何模型"}
        deleted: list[str] = []
        failed: list[dict[str, str]] = []
        for raw in paths:
            target = Path(str(raw or ""))
            if (
                not target.is_file()
                or target.suffix.lower() not in library.MODEL_EXTS
                or not self._is_allowed(target)
            ):
                failed.append({"path": str(raw), "message": "文件不存在或不在模型库范围内"})
                continue
            try:
                _send_to_recycle_bin([str(item) for item in self._delete_items(target)])
                deleted.append(str(target))
            except OSError as exc:
                failed.append({"path": str(target), "message": str(exc)})
        return {"ok": bool(deleted), "deleted": deleted, "failed": failed}

    def _delete_items(self, target: Path) -> list[Path]:
        """决定一次删除要送进回收站哪些路径。

        兼容三种情况：
        - 模型文件平铺，旁边有同名资料夹；
        - 模型自己在资料夹里（旧布局，或资料夹被手动改名）；
        - 文件改了名，资料夹还留着旧名字。
        """
        parent = target.parent
        sidecar = parent / target.stem
        if sidecar.is_dir() and self._can_delete_folder(sidecar) and not self._has_models(sidecar):
            return [target, sidecar]
        if self._is_own_folder(parent, target):
            return [parent]
        try:
            children = list(parent.iterdir())
        except OSError:
            children = []
        for child in children:
            if not child.is_dir() or child == sidecar:
                continue
            if not self._can_delete_folder(child) or self._has_models(child):
                continue
            meta = _read_json(child / "model.json")
            file_meta = meta.get("file") if isinstance(meta.get("file"), dict) else {}
            matched = str(file_meta.get("name") or "").lower() == target.name.lower()
            if not matched:
                matched = _doc_mentions(child / "模型使用说明.txt", target.name)
            if matched:
                return [target, child]
        return [target]

    def _allowed_roots(self) -> list[str]:
        roots: list[str] = []
        for key in ("library_dirs", "comfy_dirs"):
            roots.extend(self.config.data.get(key) or [])
        default_dir = self.config.data.get("default_download_dir")
        if default_dir:
            roots.append(default_dir)
        resolved: list[str] = []
        for root in roots:
            try:
                resolved.append(os.path.normcase(os.path.abspath(str(root))))
            except (OSError, ValueError):
                continue
        return resolved

    def _can_delete_folder(self, folder: Path) -> bool:
        if not folder.is_dir() or folder.is_symlink():
            return False
        if folder.name.lower() in library.TYPE_DIR_TO_TYPE:
            return False
        key = os.path.normcase(os.path.abspath(str(folder)))
        return key not in self._allowed_roots()

    def _has_models(self, folder: Path) -> bool:
        try:
            return any(
                entry.is_file() and entry.suffix.lower() in library.MODEL_EXTS
                for entry in folder.iterdir()
            )
        except OSError:
            return True

    def _is_own_folder(self, folder: Path, target: Path) -> bool:
        """资料夹里只有这一个模型，并且名字或资料能对上。"""
        if not self._can_delete_folder(folder):
            return False
        try:
            models = [
                entry
                for entry in folder.iterdir()
                if entry.is_file() and entry.suffix.lower() in library.MODEL_EXTS
            ]
        except OSError:
            return False
        if len(models) != 1 or models[0].name.lower() != target.name.lower():
            return False
        if folder.name.lower() == target.stem.lower():
            return True
        meta = _read_json(folder / "model.json")
        file_meta = meta.get("file") if isinstance(meta.get("file"), dict) else {}
        if str(file_meta.get("name") or "").lower() == target.name.lower():
            return True
        return _doc_mentions(folder / "模型使用说明.txt", target.name)

    def _is_allowed(self, path: Path) -> bool:
        target = os.path.normcase(os.path.abspath(str(path)))
        for base in self._allowed_roots():
            if target == base or target.startswith(base.rstrip("\\") + "\\"):
                return True
        return False

    def scan_library(self) -> dict[str, Any]:
        dirs = list(self.config.data.get("library_dirs") or [])
        if not dirs:
            return {"ok": True, "models": [], "dirs": []}
        try:
            models = scanner.scan(dirs)
        except OSError as exc:
            return {"ok": False, "message": f"扫描失败：{exc}", "models": [], "dirs": dirs}
        for model in models:
            paths = [self._cover_url(p) for p in (model.get("image_paths") or [])]
            model["image_paths"] = paths
            model["cover"] = paths[0] if paths else ""
        return {"ok": True, "models": models, "dirs": dirs}

    def _cover_url(self, path: str) -> str:
        from urllib.parse import quote

        if not path:
            return ""
        if not self._asset_base:
            return Path(path).as_uri()
        return f"{self._asset_base}/thumb?path={quote(path)}"

    # --- 整理本地模型库（补数据） ---
    def organize_preview(self, paths: list[str] | None = None) -> dict[str, Any]:
        dirs = list(self.config.data.get("library_dirs") or [])
        if paths:
            allowed = [str(p) for p in paths if self._is_allowed(Path(str(p)))]
            if not allowed:
                return {"ok": False, "message": "选中的模型不在模型库扫描范围内", "items": []}
            return {"ok": True, "items": self._organizer.preview(allowed), "dirs": []}
        if not dirs:
            return {"ok": True, "items": [], "dirs": []}
        return {"ok": True, "items": self._organizer.preview(None, dirs), "dirs": dirs}

    def organize_start(
        self,
        paths: list[str] | None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        dirs = list(self.config.data.get("library_dirs") or [])
        if paths:
            allowed = [str(p) for p in paths if self._is_allowed(Path(str(p)))]
            if not allowed:
                return {"ok": False, "message": "选中的模型不在模型库扫描范围内"}
            return self._organizer.start(allowed, options)
        if not dirs:
            return {"ok": False, "message": "请先在设置里添加模型库扫描范围"}
        return self._organizer.start(None, options, dirs)

    def get_organize(self) -> dict[str, Any]:
        return {"ok": True, "task": self._organizer.snapshot()}

    def cache_info(self) -> dict[str, Any]:
        return {"ok": True, "cache": self._organizer.info()}

    def clear_cache(self) -> dict[str, Any]:
        return self._organizer.clear_cache()

    def organize_action(self, action: str) -> dict[str, Any]:
        return self._organizer.action(str(action))

    # --- 目录选择 ---
    def pick_folder(self, start_dir: str = "") -> dict[str, Any]:
        import webview

        directory = start_dir if start_dir and os.path.isdir(start_dir) else ""
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=directory)
        if not result:
            return {"ok": False, "path": ""}
        path = result[0] if isinstance(result, (list, tuple)) else result
        recent = self.config.remember_dir(str(path))
        return {"ok": True, "path": str(path), "recent_dirs": recent}

    def add_library_dir(self) -> dict[str, Any]:
        import webview

        result = self._window.create_file_dialog(
            webview.FOLDER_DIALOG,
            directory=self.config.data.get("default_download_dir") or "",
        )
        if not result:
            return {"ok": False}
        path = os.path.abspath(str(result[0] if isinstance(result, (list, tuple)) else result))
        dirs = [p for p in self.config.data["library_dirs"] if os.path.normcase(p) != os.path.normcase(path)]
        dirs.append(path)
        self.config.update({"library_dirs": dirs})
        return {"ok": True, "library_dirs": self.config.data["library_dirs"]}

    def add_comfy_dir(self) -> dict[str, Any]:
        import webview

        result = self._window.create_file_dialog(
            webview.FOLDER_DIALOG,
            directory=self.config.data.get("default_download_dir") or "",
        )
        if not result:
            return {"ok": False}
        path = os.path.abspath(str(result[0] if isinstance(result, (list, tuple)) else result))
        dirs = [p for p in self.config.data["comfy_dirs"] if os.path.normcase(p) != os.path.normcase(path)]
        dirs.append(path)
        self.config.update({"comfy_dirs": dirs})
        return {"ok": True, "comfy_dirs": self.config.data["comfy_dirs"]}

    def open_path(self, path: str) -> dict[str, Any]:
        if not path or not os.path.exists(path):
            return {"ok": False, "message": "目录不存在"}
        try:
            os.startfile(path)  # type: ignore[attr-defined]
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "message": str(exc)}

    def open_url(self, url: str) -> dict[str, Any]:
        import webbrowser

        if not str(url).lower().startswith(("https://", "http://")):
            return {"ok": False, "message": "链接无效"}
        try:
            webbrowser.open(str(url))
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "message": str(exc)}

    def get_disk(self, path: str) -> dict[str, Any]:
        return _disk_info(path)


class QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, *args: Any) -> None:  # noqa: D102
        pass


def make_handler(roots_getter: Any, page_dir: Path = WEB_DIR) -> type[SimpleHTTPRequestHandler]:
    """网页文件 + 本地示例图。WebView2 不允许页面直接读 file:// 图片，统一走本地端口。"""

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(page_dir), **kwargs)

        def log_message(self, *args: Any) -> None:  # noqa: D102
            pass

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/thumb"):
                self._serve_thumb()
                return
            super().do_GET()

        def _serve_thumb(self) -> None:
            from urllib.parse import parse_qs, unquote, urlparse

            query = parse_qs(urlparse(self.path).query)
            raw = unquote(query.get("path", [""])[0])
            target = Path(raw)
            if not target.is_file() or target.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                self.send_error(404)
                return
            try:
                resolved = target.resolve()
            except OSError:
                self.send_error(404)
                return
            allowed = False
            for root in roots_getter() or []:
                try:
                    if resolved.is_relative_to(Path(root).resolve()):
                        allowed = True
                        break
                except (OSError, ValueError):
                    continue
            if not allowed:
                self.send_error(403)
                return
            import mimetypes

            try:
                data = resolved.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(resolved))[0] or "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=600")
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass

    return Handler


def serve(port: int) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), QuietHandler)
    print(f"预览地址 http://127.0.0.1:{port}/")
    httpd.serve_forever()


def main() -> None:
    import threading

    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--serve", type=int, metavar="PORT", help="仅启动网页预览（不打开桌面窗口）")
    args = parser.parse_args()

    if args.serve:
        serve(args.serve)
        return

    import webview

    api = Api()
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(lambda: api.config.data.get("library_dirs") or [])
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    api._asset_base = f"http://127.0.0.1:{httpd.server_address[1]}"

    # 页面也走本地端口，而不是 file://，否则 WebView2 会拦截页面里的本地图片请求
    api._window = webview.create_window(
        APP_NAME,
        f"{api._asset_base}/index.html",
        js_api=api,
        width=1320,
        height=860,
        min_size=(1040, 680),
        background_color="#FFFFFF",
        hidden=True,
    )

    def _startup() -> None:
        # 先隐藏窗口把图标设好，再显示，避免先闪一下 Python 默认图标
        for _ in range(20):
            if _apply_window_icons(APP_NAME):
                break
            time.sleep(0.1)
        try:
            api._window.show()
        except Exception:  # noqa: BLE001
            pass

    webview.start(_startup, debug=bool(os.environ.get("MODEL_DOWNLOADER_DEBUG")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 用 pythonw 启动时没有控制台，把崩溃栈写到本机日志，便于排查。
        try:
            log_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ModelDownloader"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "app.log").write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        raise
