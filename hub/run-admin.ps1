# 以管理员、无黑窗口启动岸亭 Hub（CPU 温度需要管理员）。
# 日志：%USERPROFILE%\.config\dock-hub\hub.log
# 启动记录：%USERPROFILE%\.config\dock-hub\start.log
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $env:USERPROFILE ".config\dock-hub"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$boot = Join-Path $logDir "start.log"

function Write-Boot([string]$msg) {
    "$(Get-Date -Format o) $msg" | Out-File $boot -Append -Encoding utf8
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`""
    )
    exit 0
}

try {
    Write-Boot "elevated start from $here"
    Get-NetTCPConnection -LocalPort 17892 -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Get-CimInstance Win32_Process |
        Where-Object {
            ($_.Name -eq 'DockHub.exe') -or (
                $_.Name -match '^python' -and $_.CommandLine -and (
                    $_.CommandLine -match 'dock_hub' -or $_.CommandLine -match 'run_hub\.pyw'
                )
            )
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1

    $exe = Join-Path $here "dist\DockHub.exe"
    if (Test-Path -LiteralPath $exe) {
        Write-Boot "exe=$exe"
        $proc = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) -PassThru
        Write-Boot "started pid=$($proc.Id)"
        Start-Sleep -Seconds 2
        if ($proc.HasExited) {
            Write-Boot "WARNING exited early code=$($proc.ExitCode)"
        }
        return
    }

    $cfg = Join-Path $here ".venv\pyvenv.cfg"
    if (-not (Test-Path -LiteralPath $cfg)) {
        throw "找不到 $cfg，先在 hub 目录运行 uv sync（或先 build-exe.ps1）"
    }
    $homeLine = Get-Content -LiteralPath $cfg | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
    if (-not $homeLine) {
        throw "pyvenv.cfg 里没有 home="
    }
    $pyHome = ($homeLine -replace '^\s*home\s*=\s*', '').Trim()
    $pythonw = Join-Path $pyHome "pythonw.exe"
    $script = Join-Path $here "run_hub.pyw"
    Write-Boot "pythonw=$pythonw script=$script"
    if (-not (Test-Path -LiteralPath $pythonw)) { throw "找不到 $pythonw" }
    if (-not (Test-Path -LiteralPath $script)) { throw "找不到 $script" }

    $proc = Start-Process -FilePath $pythonw -ArgumentList @(
        "-u",
        $script
    ) -WorkingDirectory $here -WindowStyle Hidden -PassThru
    Write-Boot "started pid=$($proc.Id)"
    Start-Sleep -Seconds 2
    if ($proc.HasExited) {
        Write-Boot "WARNING exited early code=$($proc.ExitCode)"
    }
} catch {
    Write-Boot "ERROR $($_.Exception.Message)"
    throw
}
