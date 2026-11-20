# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir 打包配置。"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "assets"), "assets"),
]
binaries = []
hiddenimports = [
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
]

# pywebview 的 JS、pythonnet / clr_loader 运行时由各自的 hook 收集，
# 这里再显式收集一次，避免打包后出现白屏或缺少 DLL。
for package in ("webview", "clr_loader"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    ["app.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ModelDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ROOT / "assets" / "app.ico"),
    version=str(ROOT / "tools" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ModelDownloader",
)
