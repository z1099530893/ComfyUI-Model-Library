# 构建 Windows 免安装目录版，并生成 zip。
# 用法：powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1

param(
    [string]$Version = "0.5.0"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "找不到 .venv，请先按 README 创建虚拟环境并安装依赖。"
}

& $python -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) {
    throw "安装打包依赖失败。"
}

& $python -m PyInstaller --noconfirm --clean model-downloader.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 构建失败。"
}

$releaseDir = Join-Path $root "release\ModelDownloader-v$Version"
if (Test-Path $releaseDir) {
    Remove-Item $releaseDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
Copy-Item (Join-Path $root "dist\ModelDownloader\*") $releaseDir -Recurse -Force

$zip = Join-Path $root "dist\ModelDownloader-v$Version-win64.zip"
if (Test-Path $zip) {
    Remove-Item $zip -Force
}
Compress-Archive -Path $releaseDir -DestinationPath $zip -CompressionLevel Optimal

Write-Host "构建完成：$releaseDir"
Write-Host "发布包：$zip"
