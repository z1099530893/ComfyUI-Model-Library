"""整理本地模型库：给已有模型补写资料。

只做三件事：计算 SHA256 → 按哈希反查 Civitai → 把说明、model.json、示例图
写进模型旁边的同名资料夹。绝不移动、改名或删除模型文件。

哈希结果按「路径 + 大小 + 修改时间」缓存，下次扫描同一批模型不用重算。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests

from . import library, scanner, sites
from .sites import civitai

USER_AGENT = "ModelDownloader/0.2 (+https://civitai.com)"
CHUNK = 4 * 1024 * 1024
IMAGE_TIMEOUT = 30
MAX_IMAGES = 12
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class _Cancelled(Exception):
    pass


class Organizer:
    """一次只跑一个整理任务，后台线程串行处理，界面轮询进度。"""

    def __init__(self, cache_path: Path, settings_getter: Callable[[], dict[str, Any]]) -> None:
        self._cache_path = Path(cache_path)
        self._settings = settings_getter
        self._lock = threading.RLock()
        self._cache: dict[str, dict[str, Any]] = {}
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._task: dict[str, Any] | None = None
        self._load_cache()

    # ------------------------------------------------------------------
    # 预览
    # ------------------------------------------------------------------
    def preview(self, paths: list[str] | None, dirs: list[str] | None = None) -> list[dict[str, Any]]:
        if paths:
            targets = [Path(str(p)) for p in paths]
        else:
            targets = [Path(m["path"]) for m in scanner.scan(dirs or [])]
        items: list[dict[str, Any]] = []
        for path in targets:
            if not path.is_file() or path.suffix.lower() not in library.MODEL_EXTS:
                continue
            items.append(self._describe(path))
        items.sort(key=lambda it: (it["type_key"], it["name"].lower()))
        return items

    def _describe(self, path: Path) -> dict[str, Any]:
        type_key = _local_type(path)
        spec = library.MODEL_TYPES[type_key]
        asset = _asset_dir(path, type_key)
        has_meta = bool(asset and (asset / "model.json").is_file())
        images = _image_names(asset) if asset else []
        try:
            stat = path.stat()
            size = stat.st_size
            mtime = int(stat.st_mtime)
        except OSError:
            size = 0
            mtime = 0
        entry = self._cache.get(_key(path)) or {}
        cached = bool(
            entry.get("sha256")
            and entry.get("size") == size
            and int(entry.get("mtime") or 0) == mtime
        )
        match = entry.get("match") if cached and entry.get("checked") else None
        return {
            "path": str(path),
            "name": path.stem,
            "file": path.name,
            "type_key": type_key,
            "type": spec["label"],
            "dir": str(asset or path.parent),
            "folder_mode": bool(spec["folder"]),
            "has_meta": has_meta,
            "images": len(images),
            "size": size,
            "mtime": mtime,
            "cached": cached,
            "checked": bool(cached and entry.get("checked")),
            "match": {
                "title": str(match.get("title") or ""),
                "author": str(match.get("author") or ""),
                "version_name": str(match.get("version_name") or ""),
            }
            if match
            else None,
        }

    # ------------------------------------------------------------------
    # 任务控制
    # ------------------------------------------------------------------
    def start(
        self,
        paths: list[str] | None,
        options: dict[str, Any] | None,
        dirs: list[str] | None = None,
    ) -> dict[str, Any]:
        options = options or {}
        opts = {
            "download_images": bool(options.get("download_images", True)),
            "overwrite": bool(options.get("overwrite", False)),
            "source_url": str(options.get("source_url") or "").strip(),
        }
        with self._lock:
            if self._task and self._task.get("status") in ("queued", "running", "paused"):
                return {"ok": False, "message": "已经有一个整理任务在进行"}
            items = self.preview(paths, dirs)
            if not items:
                return {"ok": False, "message": "没有可整理的模型"}
            # 手动来源网址只对单个模型生效，先用站点解析确认网址可用
            if opts["source_url"] and len(items) == 1:
                resolved = sites.resolve(opts["source_url"], self._settings())
                if not resolved.get("ok"):
                    return {
                        "ok": False,
                        "message": resolved.get("message") or "这个网址无法解析",
                    }
                opts["source_plan"] = resolved
            else:
                opts["source_url"] = ""
            self._task = {
                "id": f"o{int(time.time() * 1000)}",
                "status": "queued",
                "stage": "准备中",
                "total": len(items),
                "index": 0,
                "name": "",
                "item_done": 0,
                "item_size": 0,
                "written": 0,
                "skipped": 0,
                "no_match": 0,
                "failed": 0,
                "message": "",
                "log": [],
                "started_at": _now_text(),
                "finished_at": "",
            }
            self._cancel.clear()
            self._pause.clear()
            thread = threading.Thread(
                target=self._run, args=(items, opts), name="organizer", daemon=True
            )
            thread.start()
            return {"ok": True, "task": self.snapshot()}

    def snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._task:
                return None
            task = dict(self._task)
            task["log"] = [dict(item) for item in self._task["log"]]
            return task

    def action(self, name: str) -> dict[str, Any]:
        with self._lock:
            task = self._task
            if not task:
                return {"ok": False, "message": "没有整理任务"}
            status = task.get("status")
            if name == "pause":
                if status not in ("queued", "running"):
                    return {"ok": False, "message": "当前状态不能暂停"}
                self._pause.set()
                task["status"] = "paused"
                task["stage"] = "已暂停"
            elif name == "resume":
                if status != "paused":
                    return {"ok": False, "message": "当前状态不能继续"}
                self._pause.clear()
                task["status"] = "running"
                task["stage"] = "继续执行"
            elif name == "cancel":
                if status not in ("queued", "running", "paused"):
                    return {"ok": False, "message": "任务已经结束"}
                self._cancel.set()
                self._pause.clear()
                task["status"] = "cancelled"
                task["stage"] = "已取消"
                task["finished_at"] = _now_text()
            elif name == "clear":
                if status in ("queued", "running", "paused"):
                    return {"ok": False, "message": "任务还在进行"}
                self._task = None
            else:
                return {"ok": False, "message": "未知操作"}
            return {"ok": True, "task": self.snapshot()}

    # ------------------------------------------------------------------
    # 后台线程
    # ------------------------------------------------------------------
    def _run(self, items: list[dict[str, Any]], opts: dict[str, Any]) -> None:
        try:
            for index, item in enumerate(items, 1):
                with self._lock:
                    task = self._task
                    if not task or task.get("status") == "cancelled" or self._cancel.is_set():
                        return
                    task["index"] = index - 1
                    task["name"] = item["name"]
                    task["stage"] = "准备中"
                    task["item_done"] = 0
                    task["item_size"] = item["size"]
                try:
                    state, message, wrote = self._process(item, opts)
                except _Cancelled:
                    return
                except civitai.AuthError:
                    state, message, wrote = "fail", "Civitai 授权失败，请在设置里检查 API Key", False
                except requests.RequestException as exc:
                    state, message, wrote = "fail", f"连不上 Civitai：{exc.__class__.__name__}", False
                except OSError as exc:
                    state, message, wrote = "fail", f"文件操作失败：{exc}", False
                except Exception as exc:  # noqa: BLE001 - 单个模型失败不能中断整批
                    state, message, wrote = "fail", str(exc) or exc.__class__.__name__, False
                with self._lock:
                    task = self._task
                    if not task:
                        return
                    task["index"] = index
                    task["stage"] = message
                    if wrote:
                        task["written"] += 1
                    if state == "nomatch":
                        task["no_match"] += 1
                    elif state == "skip":
                        task["skipped"] += 1
                    elif state == "fail":
                        task["failed"] += 1
                    task["log"].append(
                        {"name": item["name"], "state": state, "message": message}
                    )
                    if len(task["log"]) > 200:
                        del task["log"][:50]
                self._save_cache()
            with self._lock:
                task = self._task
                if not task or task.get("status") == "cancelled":
                    return
                task["status"] = "done"
                task["stage"] = "全部完成"
                task["finished_at"] = _now_text()
        finally:
            self._save_cache()

    def _process(self, item: dict[str, Any], opts: dict[str, Any]) -> tuple[str, str, bool]:
        path = Path(item["path"])
        spec = library.MODEL_TYPES[item["type_key"]]
        # VAE / CLIP / 放大模型与下载逻辑保持一致：不建资料夹，也就不补资料
        if not spec["folder"]:
            return "skip", "该类型不生成资料夹", False

        asset = Path(item["dir"])
        # 顺手修正老版本存错的示例图扩展名（PNG 被存成了 .jpeg）
        repaired = _repair_images(asset)
        if repaired:
            _sync_images_json(asset)

        if item["has_meta"] and not opts["overwrite"]:
            message = "已有资料，未覆盖"
            if repaired:
                message += "（已修正示例图扩展名）"
            return "skip", message, False

        self._stage("计算 SHA256")
        sha = self._hash(path)

        warning = ""
        if opts.get("source_plan"):
            match = dict(opts["source_plan"])
            self._remember(path, sha, match)
        else:
            entry = self._cache.get(_key(path)) or {}
            if entry.get("sha256") == sha and entry.get("checked"):
                match = entry.get("match")
            else:
                self._stage("查询 Civitai")
                match = civitai.resolve_hash(sha, self._settings())
                self._remember(path, sha, match)

        if match:
            site_sha = str(match.get("sha256") or "").lower()
            if site_sha and site_sha != sha:
                warning = "（网址记录的文件哈希与本地不一致，请确认是否同一文件）"

        plan = dict(match) if match else _minimal_plan(path, item)
        # 类型以文件所在目录为准（下载时已经定好），不用站点返回值覆盖
        plan["model_type"] = item["type_key"]
        plan["type_dir"] = spec["dir"]
        plan["filename"] = path.name
        plan["model_path"] = str(path)
        plan["size"] = item["size"]

        names = _image_names(asset) if asset.exists() else []
        if opts["download_images"] and match and match.get("images"):
            names = self._download_images(asset, match.get("images") or [])

        if item["has_meta"] and not match:
            return "nomatch", "Civitai 未匹配，保留原资料", False

        old = _read_json(asset / "model.json")
        downloaded_at = str(old.get("downloaded_at") or "") or None
        asset.mkdir(parents=True, exist_ok=True)
        library.write_docs(asset, plan, sha, names, downloaded_at=downloaded_at)
        if match:
            label = "已按指定网址写入说明与 model.json" if opts.get("source_plan") else "已写入说明与 model.json"
            return "done", label + warning, True
        return "nomatch", "Civitai 未匹配，已写入基础资料", True

    # ------------------------------------------------------------------
    # 哈希与缓存
    # ------------------------------------------------------------------
    def _hash(self, path: Path) -> str:
        stat = path.stat()
        size = stat.st_size
        mtime = int(stat.st_mtime)
        entry = self._cache.get(_key(path)) or {}
        if (
            entry.get("sha256")
            and entry.get("size") == size
            and int(entry.get("mtime") or 0) == mtime
        ):
            self._set_size(size)
            self._set_done(size)
            return str(entry["sha256"])

        digest = hashlib.sha256()
        done = 0
        self._set_size(size)
        self._set_done(0)
        with open(path, "rb") as fh:
            while True:
                self._wait()
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                done += len(chunk)
                self._set_done(done)
        sha = digest.hexdigest()
        with self._lock:
            key = _key(path)
            current = self._cache.get(key) or {}
            if current.get("size") != size or int(current.get("mtime") or 0) != mtime:
                current = {}
            current.update({"size": size, "mtime": mtime, "sha256": sha})
            self._cache[key] = current
        return sha

    def _remember(self, path: Path, sha: str, match: dict[str, Any] | None) -> None:
        try:
            stat = path.stat()
            size = stat.st_size
            mtime = int(stat.st_mtime)
        except OSError:
            size = 0
            mtime = 0
        with self._lock:
            key = _key(path)
            entry = self._cache.get(key) or {}
            entry.update(
                {
                    "size": size,
                    "mtime": mtime,
                    "sha256": sha,
                    "checked": True,
                    "match": match,
                    "checked_at": _now_text(),
                }
            )
            self._cache[key] = entry

    def _load_cache(self) -> None:
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        files = raw.get("files") if isinstance(raw, dict) else None
        if isinstance(files, dict):
            self._cache = {k: v for k, v in files.items() if isinstance(v, dict)}

    def _save_cache(self) -> None:
        with self._lock:
            payload = json.dumps(
                {"version": 1, "files": self._cache}, ensure_ascii=False, indent=2
            )
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_path.with_name(self._cache_path.name + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._cache_path)
        except OSError:
            pass

    def info(self) -> dict[str, Any]:
        try:
            size = self._cache_path.stat().st_size
        except OSError:
            size = 0
        with self._lock:
            count = len(self._cache)
        return {"count": count, "size": size, "path": str(self._cache_path)}

    def clear_cache(self) -> dict[str, Any]:
        with self._lock:
            if self._task and self._task.get("status") in ("queued", "running", "paused"):
                return {
                    "ok": False,
                    "message": "整理任务进行中，结束后再清理缓存",
                    "cache": self.info(),
                }
            self._cache = {}
            try:
                self._cache_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                return {"ok": False, "message": f"删除缓存失败：{exc}", "cache": self.info()}
            return {"ok": True, "cache": self.info()}

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    def _wait(self) -> None:
        while self._pause.is_set() and not self._cancel.is_set():
            time.sleep(0.2)
        if self._cancel.is_set():
            raise _Cancelled()

    def _stage(self, text: str) -> None:
        with self._lock:
            if self._task:
                self._task["stage"] = text

    def _set_size(self, size: int) -> None:
        with self._lock:
            if self._task:
                self._task["item_size"] = size

    def _set_done(self, done: int) -> None:
        with self._lock:
            if self._task:
                self._task["item_done"] = done

    def _download_images(self, asset: Path, images: list[dict[str, Any]]) -> list[str]:
        asset.mkdir(parents=True, exist_ok=True)
        proxy = str(self._settings().get("proxy") or "").strip()
        proxies = {"http": proxy, "https": proxy} if proxy else None
        pool = images[:MAX_IMAGES]
        for index, img in enumerate(pool, 1):
            self._wait()
            url = str((img or {}).get("url") or "")
            if not url:
                continue
            fallback = _image_ext(url)
            existing = library.find_example_image(asset, index)
            if existing is not None:
                library.normalize_image_ext(existing, fallback)
                continue
            self._stage(f"下载示例图 {index}/{len(pool)}")
            try:
                resp = requests.get(
                    url,
                    timeout=IMAGE_TIMEOUT,
                    proxies=proxies,
                    headers={"User-Agent": USER_AGENT},
                )
                resp.raise_for_status()
                data = resp.content
                ext = library.image_ext(data, fallback)
                (asset / f"示例图_{index:02d}{ext}").write_bytes(data)
            except (requests.RequestException, OSError):
                continue
        # 只把真正存在的示例图写进 model.json
        return _image_names(asset)


# ----------------------------------------------------------------------
# 模块级工具
# ----------------------------------------------------------------------
def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path))).lower()


def _local_type(path: Path) -> str:
    for parent in [path.parent, *path.parent.parents]:
        key = library.TYPE_DIR_TO_TYPE.get(parent.name.lower())
        if key:
            return key
    for parent in [path.parent, *path.parent.parents]:
        key = scanner.DIR_TYPE.get(parent.name.lower())
        if key and key in library.MODEL_TYPES:
            return key
    return "checkpoint"


def _asset_dir(path: Path, type_key: str) -> Path | None:
    if not library.MODEL_TYPES[type_key]["folder"]:
        return None
    parent = path.parent
    # 兼容「模型名文件夹里放一切」的临时布局
    if parent.name.lower() == path.stem.lower():
        return parent
    return parent / path.stem


def _image_names(asset: Path | None) -> list[str]:
    if not asset or not asset.is_dir():
        return []
    return sorted(
        p.name
        for p in asset.glob("示例图_*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def _repair_images(asset: Path) -> list[str]:
    """把扩展名与实际内容不符的示例图改正，返回改过的文件名。"""
    if not asset.is_dir():
        return []
    renamed: list[str] = []
    for path in list(asset.glob("示例图_*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
            continue
        target = library.normalize_image_ext(path)
        if target != path:
            renamed.append(target.name)
    return renamed


def _sync_images_json(asset: Path) -> None:
    """改过扩展名后，同步 model.json 里的图片列表（只动 images 字段）。"""
    path = asset / "model.json"
    meta = _read_json(path)
    if not meta:
        return
    names = _image_names(asset)
    if meta.get("images") == names:
        return
    meta["images"] = names
    try:
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _minimal_plan(path: Path, item: dict[str, Any]) -> dict[str, Any]:
    spec = library.MODEL_TYPES[item["type_key"]]
    return {
        "site": "",
        "model_id": None,
        "version_id": None,
        "page_url": "",
        "title": path.stem,
        "version_name": "",
        "type": "",
        "model_type": item["type_key"],
        "type_dir": spec["dir"],
        "base_model": "",
        "author": "",
        "published_at": "",
        "trigger_words": [],
        "tags": [],
        "description": "没有在 Civitai 找到对应记录，这里只写入了本地文件信息。",
        "nsfw": False,
        "filename": path.name,
        "model_path": str(path),
        "size": int(item.get("size") or 0),
    }


def _image_ext(url: str) -> str:
    lowered = url.split("?")[0].lower()
    for ext in (".jpeg", ".jpg", ".png", ".webp"):
        if lowered.endswith(ext):
            return ext
    return ".jpg"


def _now_text() -> str:
    return datetime.now().strftime("%m-%d %H:%M")
