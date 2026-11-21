"""把下载好的模型接入 ComfyUI 模型目录。

默认只做链接，不复制模型：

- 自动：同盘优先硬链接，失败再尝试软链接（跨盘通常走这里）。
- 硬链接：直接 os.link，失败就报告。
- 软链接：直接 os.symlink，需要 Windows 开发者模式或相应权限。
- 映射配置：写入 ComfyUI 的 extra_model_paths.yaml，重启 ComfyUI 后生效。

源文件已经在 ComfyUI 的 models 目录内时直接跳过，避免自己链自己。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .library import MODEL_TYPES

TYPE_DIRS = {spec["dir"].lower(): key for key, spec in MODEL_TYPES.items()}
YAML_NAME = "extra_model_paths.yaml"
MARK_START = "# >>> model-downloader managed block >>>"
MARK_END = "# <<< model-downloader managed block <<<"
BLOCK_RE = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), re.S)


def comfy_base_dir(root: str | Path) -> Path | None:
    """comfy_dirs 里可能选的是便携版根目录、ComfyUI 目录或 models 目录。"""
    path = Path(root)
    if (path / "ComfyUI" / "main.py").is_file():
        return path / "ComfyUI"
    if (path / "main.py").is_file():
        return path
    if path.name.lower() == "comfyui":
        return path
    if (path / "ComfyUI").is_dir():
        return path / "ComfyUI"
    return None


def models_root(root: str | Path) -> Path | None:
    base = comfy_base_dir(root)
    if base and (base / "models").is_dir():
        return base / "models"
    path = Path(root)
    if path.name.lower() == "models":
        return path
    if (path / "models").is_dir():
        return path / "models"
    if path.name.lower() in TYPE_DIRS:
        return path.parent
    return None


def link_model(source: str | Path, model_type: str, settings: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "mode": str(settings.get("link_mode") or "auto"),
        "linked": 0,
        "skipped": 0,
        "errors": [],
        "details": [],
    }
    if not settings.get("auto_link"):
        result["skipped"] = 1
        result["message"] = "未开启自动接入"
        return result

    source = Path(source)
    spec = MODEL_TYPES.get(str(model_type or ""))
    if not spec or not source.is_file():
        result["skipped"] = 1
        result["message"] = "该类型不支持自动接入"
        return result

    roots = [Path(str(p)) for p in (settings.get("comfy_dirs") or []) if str(p).strip()]
    if not roots:
        result["skipped"] = 1
        result["message"] = "未配置 ComfyUI 目录"
        return result

    if result["mode"] == "yaml":
        return _write_yaml(source, roots, settings)

    for root in roots:
        mroot = models_root(root)
        if not mroot or not mroot.is_dir():
            result["errors"].append(f"{root}：找不到 ComfyUI/models 目录")
            continue
        if _is_inside(source, mroot):
            result["skipped"] += 1
            result["details"].append({"root": str(root), "ok": True, "message": "已在 ComfyUI 模型目录内"})
            continue
        target_dir = mroot / spec["dir"] / _subdir_of(source, spec["dir"])
        dest = target_dir / source.name
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result["errors"].append(f"{target_dir}：无法创建目录（{exc}）")
            continue
        if dest.exists():
            try:
                same = os.path.samefile(source, dest)
            except OSError:
                same = False
            if same:
                result["skipped"] += 1
                result["details"].append({"root": str(root), "ok": True, "message": "链接已存在"})
            else:
                result["errors"].append(f"{dest}：已存在同名文件，未覆盖")
            continue

        kind, error = _create_link(source, dest, result["mode"])
        if error:
            result["errors"].append(error)
            continue
        result["linked"] += 1
        result["details"].append({"root": str(root), "ok": True, "kind": kind, "message": f"已创建{kind}"})

    result["ok"] = not result["errors"]
    if not result["linked"] and not result["errors"]:
        result["message"] = "无需接入"
    return result


def _create_link(source: Path, dest: Path, mode: str) -> tuple[str, str]:
    if mode in ("auto", "hardlink"):
        try:
            os.link(source, dest)
            return "硬链接", ""
        except OSError as exc:
            if mode == "hardlink":
                return "", f"{dest}：硬链接失败（{exc}）"
    if mode in ("auto", "symlink"):
        try:
            os.symlink(source, dest, target_is_directory=False)
            return "软链接", ""
        except OSError as exc:
            return "", f"{dest}：软链接失败（{exc}），可改用映射配置"
    return "", f"{dest}：不支持的方式 {mode}"


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _subdir_of(source: Path, type_dir: str) -> Path:
    """取出模型文件在类型目录下的相对子路径，接入时保持同样的文件夹结构。"""
    parts = source.parts
    for index in range(len(parts) - 1, -1, -1):
        if parts[index].lower() == type_dir.lower():
            return Path(*parts[index + 1 : -1])
    return Path()


def _library_paths(settings: dict[str, Any]) -> dict[str, list[str]]:
    paths: dict[str, list[str]] = {}
    raw_roots = list(settings.get("library_dirs") or [])
    default = settings.get("default_download_dir")
    if default:
        raw_roots.append(default)
    for raw in raw_roots:
        base = Path(str(raw))
        if not base.is_dir():
            continue
        if base.name.lower() in TYPE_DIRS:
            _add(paths, base.name.lower(), base)
            continue
        for type_dir in TYPE_DIRS:
            folder = base / type_dir
            if folder.is_dir():
                _add(paths, type_dir, folder)
    return paths


def _add(paths: dict[str, list[str]], type_dir: str, folder: Path) -> None:
    items = paths.setdefault(type_dir, [])
    text = str(folder.resolve()).replace("\\", "/")
    if text not in items:
        items.append(text)


def _write_yaml(source: Path, roots: list[Path], settings: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "mode": "yaml",
        "linked": 0,
        "skipped": 0,
        "errors": [],
        "details": [],
    }
    paths = _library_paths(settings)
    if not paths:
        result["ok"] = False
        result["errors"].append("没有可映射的模型库目录")
        return result

    lines = [MARK_START, "model_downloader:"]
    for type_dir, items in paths.items():
        lines.append(f"    {type_dir}: |")
        lines.extend(f"        {item}" for item in items)
    lines.append(MARK_END)
    block = "\n".join(lines)

    for root in roots:
        base = comfy_base_dir(root)
        if not base or not base.is_dir():
            result["errors"].append(f"{root}：找不到 ComfyUI 目录")
            continue
        yaml_path = base / YAML_NAME
        try:
            text = yaml_path.read_text(encoding="utf-8") if yaml_path.is_file() else ""
        except OSError as exc:
            result["errors"].append(f"{yaml_path}：读取失败（{exc}）")
            continue
        if MARK_START in text:
            text = BLOCK_RE.sub(block, text)
        elif re.search(r"(?m)^model_downloader\s*:", text):
            result["errors"].append(f"{yaml_path}：已存在 model_downloader 配置，未修改")
            continue
        else:
            text = text.rstrip() + ("\n\n" if text.strip() else "") + block + "\n"
        try:
            yaml_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            result["errors"].append(f"{yaml_path}：写入失败（{exc}）")
            continue
        result["linked"] += 1
        result["details"].append({"root": str(root), "ok": True, "kind": "映射配置", "message": str(yaml_path)})

    result["ok"] = not result["errors"]
    result["written"] = bool(result["linked"])
    result["message"] = "映射配置已写入，重启 ComfyUI 后生效" if result["written"] else ""
    return result
