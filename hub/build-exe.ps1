# Build DockHub.exe (windowed, system tray). Run from hub/:
#   powershell -ExecutionPolicy Bypass -File .\build-exe.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Building DockHub.exe ..."
# 把当前提交号烤进 exe：探针 /health 的 revision 就来自这个文件
$rev = git rev-parse --short HEAD
if ($LASTEXITCODE -ne 0 -or -not $rev) { throw "git rev-parse failed - 在仓库里打包" }
Set-Content -Path (Join-Path $PSScriptRoot "_revision.txt") -Value $rev -NoNewline -Encoding ascii
Write-Host "Revision: $rev"

uv sync --group dev
uv run pyinstaller --noconfirm .\dock-hub.spec

$exe = Join-Path $PSScriptRoot "dist\DockHub.exe"
if (-not (Test-Path $exe)) {
    throw "Build failed: $exe not found"
}
Write-Host ""
Write-Host "OK: $exe"
Write-Host "Tray: open Mini Hub setup (LAN), quit."
