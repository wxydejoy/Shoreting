@echo off
REM 一键更新 Windows 端：git pull -> 重打 DockHub.exe -> 重启计划任务 -> 验活。
REM 用法：双击，或在仓库任意位置运行 hub\update-probe.cmd
setlocal
cd /d "%~dp0"

echo == 1/3 拉取最新代码
git pull --ff-only
if errorlevel 1 (
    echo    --ff-only 失败，试 GitHub 直连（仓库是 public，不需要登录）
    git fetch https://github.com/wxydejoy/Shoreting.git main || goto :fail
    git merge --ff-only FETCH_HEAD || goto :fail
)

echo == 2/3 重打 DockHub.exe（build-exe.ps1 含 uv sync，约 1 分钟）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-exe.ps1" || goto :fail

echo == 3/3 重启并验活
taskkill /f /im DockHub.exe >nul 2>nul
schtasks /run /tn HelmDockHub
timeout /t 6 /nobreak >nul
curl -s -m 5 http://127.0.0.1:17892/health
echo.
echo 上面 JSON 的 revision 就是这次打包用的提交号。
pause
exit /b 0

:fail
echo.
echo 更新失败：把上面的报错发出来看。
pause
exit /b 1
