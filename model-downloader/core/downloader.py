"""下载引擎。

一个后台线程串行处理队列，逐块写盘并上报进度；界面通过轮询读取状态，
这样既不需要把窗口对象传进线程，也不会因为界面卡顿影响下载。

特性：
- 断点续传：先写 <文件名>.part，用 HTTP Range 接着下
- 完整性校验：下完计算 SHA256，与站点提供的哈希比对
- 暂停 / 继续 / 取消 / 重试
- 磁盘空间预检
- 下载完成后补齐示例图、模型使用说明.txt、model.json
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests

from . import library, linker

USER_AGENT = "ModelDownloader/0.2 (+https://civitai.com)"
CHUNK = 1024 * 1024
IMAGE_TIMEOUT = 30


class _Paused(Exception):
    pass


class _Cancelled(Exception):
    pass


class _AuthRequired(Exception):
    pass


class _Failed(Exception):
    pass


class Downloader:
    def __init__(self, state_path: Path, settings_getter: Callable[[], dict[str, Any]]) -> None:
        self._state_path = Path(state_path)
        self._settings = settings_getter
        self._lock = threading.RLock()
        self._tasks: list[dict[str, Any]] = []
        self._ctl: dict[str, dict[str, threading.Event]] = {}
        self._speed: dict[str, tuple[float, int]] = {}
        self._last_save = 0.0
        self._save_lock = threading.Lock()
        self._stop = threading.Event()
        self._load()
        self._thread = threading.Thread(target=self._loop, name="downloader", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def add(self, plan: dict[str, Any], root: str) -> dict[str, Any]:
        plan = dict(plan)
        target = library.plan_target(root, plan)
        plan.update(target)

        with self._lock:
            for task in self._tasks:
                if (
                    task.get("path", "").lower() == plan["model_path"].lower()
                    and task.get("status") in ("queued", "running", "paused", "verifying")
                ):
                    return dict(task)

            task = {
                "id": f"t{int(time.time() * 1000)}{random.randint(10, 99)}",
                "name": plan.get("title") or plan.get("filename") or "未命名模型",
                "version_name": plan.get("version_name") or "",
                "site": plan.get("site") or "",
                "site_name": plan.get("site_name") or plan.get("site") or "",
                "type": plan.get("type") or "",
                "base": plan.get("base_model") or "",
                "dir": plan["model_dir"],
                "path": plan["model_path"],
                "root": root,
                "url": plan.get("download_url") or "",
                "size": int(plan.get("size") or 0),
                "done": 0,
                "speed": 0,
                "images_total": len(plan.get("images") or []) if plan.get("folder_mode", True) else 0,
                "images_done": 0,
                "status": "queued",
                "stage": "等待开始",
                "error": "",
                "error_code": "",
                "warning": "",
                "created_at": _now_text(),
                "finished_at": "",
                "plan": plan,
            }
            self._tasks.append(task)
            self._ctl[task["id"]] = {
                "pause": threading.Event(),
                "cancel": threading.Event(),
            }
        self._save(force=True)
        return dict(task)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(t) for t in self._tasks]

    def action(self, task_id: str, action: str) -> dict[str, Any]:
        if action == "pause_all":
            for task in self.snapshot():
                if task["status"] in ("queued", "running"):
                    self.action(task["id"], "pause")
            return {"ok": True}

        with self._lock:
            task = next((t for t in self._tasks if t["id"] == task_id), None)
            if not task:
                return {"ok": False, "message": "任务不存在"}

            if action == "pause":
                if task["status"] == "running":
                    task["status"] = "paused"
                    task["stage"] = "已暂停"
                    task["speed"] = 0
                    self._ctl[task_id]["pause"].set()
                elif task["status"] == "queued":
                    task["status"] = "paused"
                    task["stage"] = "已暂停"
                else:
                    return {"ok": False, "message": "当前状态不能暂停"}

            elif action in ("resume", "retry"):
                if task["status"] not in ("paused", "error"):
                    return {"ok": False, "message": "当前状态不能继续"}
                ctl = self._ctl.setdefault(
                    task_id, {"pause": threading.Event(), "cancel": threading.Event()}
                )
                ctl["pause"].clear()
                ctl["cancel"].clear()
                task["status"] = "queued"
                task["stage"] = "等待开始"
                task["error"] = ""
                task["error_code"] = ""
                task["finished_at"] = ""

            elif action == "cancel":
                ctl = self._ctl.get(task_id)
                if ctl:
                    ctl["cancel"].set()
                self._tasks = [t for t in self._tasks if t["id"] != task_id]
                self._ctl.pop(task_id, None)
                _remove_part(task)
                self._save(force=True)
                return {"ok": True}

            elif action == "remove":
                if task["status"] not in ("done", "error"):
                    return {"ok": False, "message": "只能移除已完成或失败的任务"}
                self._tasks = [t for t in self._tasks if t["id"] != task_id]
                self._ctl.pop(task_id, None)
                self._save(force=True)
                return {"ok": True}

            else:
                return {"ok": False, "message": "未知操作"}

        self._save(force=True)
        return {"ok": True}

    def clear_done(self) -> dict[str, Any]:
        with self._lock:
            self._tasks = [t for t in self._tasks if t["status"] != "done"]
            self._save(force=True)
        return {"ok": True}

    def rename(self, task_id: str, title: str, folder: str, filename: str) -> dict[str, Any]:
        """修改尚未完成的任务的模型名 / 文件夹名 / 文件名（支持中文）。"""
        with self._lock:
            task = next((t for t in self._tasks if t["id"] == task_id), None)
            if not task:
                return {"ok": False, "message": "任务不存在"}
            if task["status"] not in ("queued", "paused", "error"):
                return {"ok": False, "message": "只有排队中、已暂停或失败的任务可以改名"}

            plan = dict(task.get("plan") or {})
            plan["title"] = title
            plan["folder_name"] = folder
            plan["filename"] = filename
            root = task.get("root") or task.get("dir") or ""
            target = library.plan_target(root, plan)
            plan.update(target)

            old_path = Path(task["path"])
            new_path = Path(target["model_path"])
            old_asset = Path((task.get("plan") or {}).get("asset_dir") or "")
            if new_path != old_path:
                old_part = old_path.with_name(old_path.name + ".part")
                if old_part.exists():
                    try:
                        new_path.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(old_part, new_path.with_name(new_path.name + ".part"))
                    except OSError:
                        pass
            new_asset = Path(target.get("asset_dir") or "")
            if old_asset.name and new_asset.name and old_asset != new_asset and old_asset.is_dir():
                try:
                    new_asset.parent.mkdir(parents=True, exist_ok=True)
                    if not new_asset.exists():
                        os.replace(old_asset, new_asset)
                except OSError:
                    pass

            task["plan"] = plan
            task["name"] = plan.get("title") or task["name"]
            task["dir"] = target["model_dir"]
            task["path"] = target["model_path"]
            self._save(force=True)
            return {"ok": True, "task": dict(task)}

    def stop(self) -> None:
        self._stop.set()

    def shutdown(self) -> None:
        """关窗口收尾：未完成任务标为暂停并立即落盘，下次启动继续。"""
        with self._lock:
            for task in self._tasks:
                if task.get("status") in ("queued", "running"):
                    task["status"] = "paused"
                    task["stage"] = "已暂停（程序退出）"
                    task["speed"] = 0
            self._save(force=True)
        for ctl in list(self._ctl.values()):
            ctl["pause"].set()
        self.stop()

    # ------------------------------------------------------------------
    # 队列线程
    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            task = self._next_queued()
            if task is None:
                time.sleep(0.3)
                continue
            self._run(task)

    def _next_queued(self) -> dict[str, Any] | None:
        with self._lock:
            for task in self._tasks:
                if task["status"] == "queued":
                    task["status"] = "running"
                    task["stage"] = "准备中"
                    task["error"] = ""
                    task["error_code"] = ""
                    task["speed"] = 0
                    return task
        return None

    def _run(self, task: dict[str, Any]) -> None:
        task_id = task["id"]
        ctl = self._ctl.setdefault(task_id, {"pause": threading.Event(), "cancel": threading.Event()})
        self._save(force=True)

        try:
            if ctl["cancel"].is_set():
                raise _Cancelled()
            self._precheck(task)
            self._download_model(task)
            self._verify(task)
            self._download_images(task)
            self._write_docs(task)
            self._link(task)
            with self._lock:
                if not self._exists(task_id):
                    return
                task["status"] = "done"
                task["stage"] = ""
                task["speed"] = 0
                task["done"] = task["size"] or task["done"]
                task["finished_at"] = _now_text()
        except _Paused:
            with self._lock:
                if self._exists(task_id):
                    task["status"] = "paused"
                    task["stage"] = "已暂停"
                    task["speed"] = 0
        except _Cancelled:
            self._discard(task)
            return
        except _AuthRequired as exc:
            self._fail(task, "auth", str(exc))
        except _Failed as exc:
            self._fail(task, "error", str(exc))
        except requests.RequestException as exc:
            self._fail(task, "network", f"网络中断：{exc.__class__.__name__}，已自动重试 3 次")
        except OSError as exc:
            self._fail(task, "disk", f"写入失败：{exc}")
        except Exception as exc:  # noqa: BLE001 - 任何意外都要落到任务状态里，不能吞掉
            self._fail(task, "error", f"下载失败：{exc}")
        finally:
            self._speed.pop(task_id, None)
            self._save(force=True)

    def _fail(self, task: dict[str, Any], code: str, message: str) -> None:
        with self._lock:
            if not self._exists(task["id"]):
                return
            task["status"] = "error"
            task["stage"] = ""
            task["speed"] = 0
            task["error"] = message
            task["error_code"] = code

    def _exists(self, task_id: str) -> bool:
        return any(t["id"] == task_id for t in self._tasks)

    def _discard(self, task: dict[str, Any]) -> None:
        with self._lock:
            if self._exists(task["id"]):
                self._tasks = [t for t in self._tasks if t["id"] != task["id"]]
            self._ctl.pop(task["id"], None)
        _remove_part(task)

    # ------------------------------------------------------------------
    # 具体步骤
    # ------------------------------------------------------------------
    def _precheck(self, task: dict[str, Any]) -> None:
        dest = Path(task["path"])
        parent = _existing_parent(dest.parent)
        size = int(task.get("size") or 0)
        if not size:
            return
        try:
            usage = shutil.disk_usage(parent)
        except OSError:
            return
        if usage.free < size * 1.05:
            raise _Failed(
                f"磁盘空间不足：需要约 {_human(size)}，可用 {_human(usage.free)}，请换个目录。"
            )

    def _download_model(self, task: dict[str, Any]) -> None:
        dest = Path(task["path"])
        # 模型文件已经下好（例如上次在下载示例图时被关闭），直接跳过，避免重下几 GB
        if dest.exists() and dest.stat().st_size > 0:
            size = dest.stat().st_size
            if not task.get("size") or size == int(task["size"]):
                with self._lock:
                    task["size"] = size
                    task["done"] = size
                    task["stage"] = "模型已存在"
                return

        attempt = 0
        while True:
            try:
                self._stream(task)
                return
            except (_Paused, _Cancelled, _AuthRequired, _Failed):
                raise
            except requests.RequestException:
                attempt += 1
                if attempt >= 3:
                    raise
                with self._lock:
                    task["stage"] = f"网络波动，第 {attempt + 1} 次重试…"
                time.sleep(min(2 * attempt, 6))

    def _stream(self, task: dict[str, Any]) -> None:
        dest = Path(task["path"])
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)

        settings = self._settings()
        site = str(task.get("site") or "")
        tokens = settings.get("site_tokens") or {}
        token_key = site if site in ("civitai", "huggingface", "modelscope") else "civitai"
        token = str(tokens.get(token_key) or "").strip()
        headers = {"User-Agent": USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        existing = part.stat().st_size if part.exists() else 0
        if existing:
            headers["Range"] = f"bytes={existing}-"

        proxy = str(settings.get("proxy") or "").strip()
        proxies = {"http": proxy, "https": proxy} if proxy else None

        with requests.get(
            task["url"], headers=headers, stream=True, timeout=(20, 60), proxies=proxies
        ) as resp:
            if resp.status_code in (401, 403):
                site_name = str(task.get("site_name") or "站点")
                raise _AuthRequired(
                    f"这个模型需要 {site_name} 授权。请到「设置 → 站点凭证」填写对应 Token，然后点重试。"
                )
            if resp.status_code == 416:
                part.unlink(missing_ok=True)
                self._stream(task)
                return
            resp.raise_for_status()

            total = int(task.get("size") or 0)
            if resp.status_code == 206:
                content_range = resp.headers.get("Content-Range") or ""
                if "/" in content_range:
                    try:
                        total = int(content_range.rsplit("/", 1)[1])
                    except ValueError:
                        pass
                mode, done = "ab", existing
            else:
                existing = 0
                done = 0
                length = resp.headers.get("Content-Length")
                if length and length.isdigit():
                    total = int(length)
                mode = "wb"

            with self._lock:
                task["size"] = total
                task["done"] = done
                task["stage"] = "下载模型"
            self._save()

            with open(part, mode) as fh:
                for chunk in resp.iter_content(CHUNK):
                    if not chunk:
                        continue
                    if self._ctl[task["id"]]["cancel"].is_set():
                        raise _Cancelled()
                    if self._ctl[task["id"]]["pause"].is_set():
                        raise _Paused()
                    fh.write(chunk)
                    done += len(chunk)
                    self._progress(task, done, total)

    def _progress(self, task: dict[str, Any], done: int, total: int) -> None:
        now = time.time()
        with self._lock:
            if not self._exists(task["id"]):
                raise _Cancelled()
            task["done"] = done
            if total and task.get("size") != total:
                task["size"] = total
            last_time, last_done = self._speed.get(task["id"], (now, done))
            elapsed = now - last_time
            if elapsed >= 0.5:
                instant = max(0.0, (done - last_done) / elapsed)
                prev = float(task.get("speed") or 0)
                task["speed"] = instant if prev <= 0 else prev * 0.65 + instant * 0.35
                self._speed[task["id"]] = (now, done)
        self._save()

    def _verify(self, task: dict[str, Any]) -> None:
        dest = Path(task["path"])
        part = dest.with_name(dest.name + ".part")
        source = part if part.exists() else dest
        if not source.exists():
            raise _Failed("下载文件丢失，请重试。")

        with self._lock:
            task["stage"] = "校验完整性"
            task["speed"] = 0
        self._save(force=True)

        digest = hashlib.sha256()
        with open(source, "rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK * 4), b""):
                if self._ctl[task["id"]]["cancel"].is_set():
                    raise _Cancelled()
                digest.update(chunk)
        sha256 = digest.hexdigest()

        expected = str((task.get("plan") or {}).get("sha256") or "").lower()
        if expected and sha256 != expected:
            source.unlink(missing_ok=True)
            raise _Failed("文件哈希与站点记录不一致，可能下载损坏，已删除临时文件，请重试。")

        if source is part:
            os.replace(part, dest)
        with self._lock:
            task["sha256"] = sha256
            task["stage"] = "整理文件"

    def _download_images(self, task: dict[str, Any]) -> None:
        # 资料放在同名文件夹里；VAE / CLIP / 放大模型没有资料夹，也就不下示例图
        asset_dir = str((task.get("plan") or {}).get("asset_dir") or "")
        if not asset_dir:
            return
        images = (task.get("plan") or {}).get("images") or []
        if not images:
            return
        model_dir = Path(asset_dir)
        model_dir.mkdir(parents=True, exist_ok=True)

        failed = 0
        proxy = str(self._settings().get("proxy") or "").strip()
        proxies = {"http": proxy, "https": proxy} if proxy else None

        for index, img in enumerate(images, 1):
            if self._ctl[task["id"]]["cancel"].is_set():
                raise _Cancelled()
            url = str(img.get("url") or "")
            fallback = _image_ext(url)
            existing = library.find_example_image(model_dir, index)
            if existing is not None:
                # 老版本把 PNG 存成了 .jpeg，这里顺手改正
                library.normalize_image_ext(existing, fallback)
                with self._lock:
                    task["images_done"] = index
                continue
            with self._lock:
                task["stage"] = f"下载示例图 {index}/{len(images)}"
            try:
                resp = requests.get(
                    url, timeout=IMAGE_TIMEOUT, proxies=proxies, headers={"User-Agent": USER_AGENT}
                )
                resp.raise_for_status()
                data = resp.content
                ext = library.image_ext(data, fallback)
                (model_dir / f"示例图_{index:02d}{ext}").write_bytes(data)
                with self._lock:
                    task["images_done"] = index
            except (requests.RequestException, OSError):
                failed += 1
        if failed and failed == len(images):
            with self._lock:
                task["warning"] = "示例图下载失败，可稍后重试或手动保存。"
        with self._lock:
            task["images_done"] = len(images) - failed

    def _write_docs(self, task: dict[str, Any]) -> None:
        # 资料夹不存在（平铺模型）就只留模型文件
        asset_dir = str((task.get("plan") or {}).get("asset_dir") or "")
        if not asset_dir:
            return
        with self._lock:
            task["stage"] = "写入说明文档"
        plan = dict(task.get("plan") or {})
        plan["model_path"] = task["path"]
        model_dir = Path(asset_dir)
        names = sorted(p.name for p in model_dir.glob("示例图_*")) if model_dir.exists() else []
        library.write_docs(
            model_dir,
            plan,
            str(task.get("sha256") or ""),
            names,
            downloaded_at=task.get("created_at"),
        )

    def _link(self, task: dict[str, Any]) -> None:
        settings = self._settings() or {}
        if not settings.get("auto_link"):
            return
        plan = task.get("plan") or {}
        model_type = str(plan.get("model_type") or "")
        if not model_type:
            return
        with self._lock:
            task["stage"] = "接入 ComfyUI"
        try:
            result = linker.link_model(Path(task["path"]), model_type, settings)
        except Exception as exc:  # noqa: BLE001 - 接入失败不能影响下载结果
            result = {
                "ok": False,
                "mode": str(settings.get("link_mode") or "auto"),
                "linked": 0,
                "skipped": 0,
                "errors": [f"接入失败：{exc}"],
                "details": [],
            }
        with self._lock:
            if self._exists(task["id"]):
                task["link"] = result

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if item.get("status") in ("running", "verifying"):
                item["status"] = "paused"
                item["stage"] = "已暂停"
                item["speed"] = 0
            self._tasks.append(item)
            self._ctl[item["id"]] = {"pause": threading.Event(), "cancel": threading.Event()}

    def _save(self, force: bool = False) -> None:
        now = time.time()
        with self._lock:
            if not force and now - self._last_save < 1.5:
                return
            self._last_save = now
            payload = json.dumps(self._tasks, ensure_ascii=False, indent=2)
        try:
            with self._save_lock:
                self._state_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._state_path.with_name(self._state_path.name + ".tmp")
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, self._state_path)
        except OSError:
            pass


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------

def _now_text() -> str:
    return datetime.now().strftime("%m-%d %H:%M")


def _human(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


def _existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def _remove_part(task: dict[str, Any]) -> None:
    try:
        dest = Path(task.get("path") or "")
        if dest.name:
            dest.with_name(dest.name + ".part").unlink(missing_ok=True)
    except OSError:
        pass


def _image_ext(url: str) -> str:
    path = url.split("?")[0].lower()
    for ext in (".jpeg", ".jpg", ".png", ".webp"):
        if path.endswith(ext):
            return ext
    return ".jpg"
