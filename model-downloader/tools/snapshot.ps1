# 里程碑快照：把当前源码打成一个 zip，放在 _snapshots 下，用于随时回滚。
# 用法：powershell -ExecutionPolicy Bypass -File tools\snapshot.ps1 -Label M0-ui
param(
  [string]$Label = "manual"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $root "_snapshots"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$zip = Join-Path $outDir ("model-downloader_{0}_{1}.zip" -f $stamp, $Label)

$tmp = Join-Path $env:TEMP ("md_snapshot_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

$exclude = @(".venv", "__pycache__", "_snapshots", ".git", "build", "dist")
Get-ChildItem -Path $root -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
  Copy-Item $_.FullName -Destination $tmp -Recurse -Force
}

Compress-Archive -Path (Join-Path $tmp "*") -DestinationPath $zip -CompressionLevel Optimal
Remove-Item $tmp -Recurse -Force

Write-Output "快照已生成: $zip"
