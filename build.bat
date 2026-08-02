@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 开始打包绿色版...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1"
if errorlevel 1 (
  echo.
  echo 打包失败。
  pause
  exit /b 1
)
echo.
pause
