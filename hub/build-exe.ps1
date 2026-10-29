# Build DockHub.exe (windowed, system tray). Run from hub/:
#   powershell -ExecutionPolicy Bypass -File .\build-exe.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Building DockHub.exe ..."
uv sync --group dev
uv run pyinstaller --noconfirm .\dock-hub.spec

$exe = Join-Path $PSScriptRoot "dist\DockHub.exe"
if (-not (Test-Path $exe)) {
    throw "Build failed: $exe not found"
}
Write-Host ""
Write-Host "OK: $exe"
Write-Host "Tray: open Mini Hub setup (LAN), quit."
