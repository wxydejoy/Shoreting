@echo off
REM 一键更新 Windows 探针：git pull -> 重启 pythonw(-m dock_hub --probe) -> 健康检查。
REM 用法：双击，或在仓库任意位置运行 hub\update-probe.cmd
setlocal
cd /d "%~dp0"

echo == 1/4 拉取最新代码
git pull --ff-only
if errorlevel 1 (
    echo    --ff-only 失败，试 GitHub 直连（仓库是 public，不需要登录）
    git fetch https://github.com/wxydejoy/Shoreting.git main || goto :fail
    git merge --ff-only FETCH_HEAD || goto :fail
)

echo == 2/4 同步依赖（有 uv 才做，没有就跳过）
where uv >nul 2>nul && (uv sync || goto :fail) || echo    未安装 uv，跳过

echo == 3/4 重启探针进程
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'dock_hub' -and $_.CommandLine -match '--probe' } | ForEach-Object { Write-Host ('    停掉 PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"
start "" wscript.exe //nologo "%~dp0run-probe.vbs"

echo == 4/4 健康检查
timeout /t 3 /nobreak >nul
curl -s -m 5 http://127.0.0.1:17892/health
echo.
echo 完成。如果上面没有 JSON，去任务管理器看 pythonw 是否在跑，或手动运行 run-probe.bat。
pause
exit /b 0

:fail
echo.
echo 更新失败：把上面的报错发出来看。
pause
exit /b 1
