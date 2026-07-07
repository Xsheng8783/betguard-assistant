@echo off
chcp 65001 >nul
title Betguard Assistant
cd /d "%~dp0.."

echo.
echo ========================================
echo   Betguard Assistant v0.5.7
echo   本地投注輔助工具
echo ========================================
echo.

set PYTHONPATH=%cd%\src

echo 正在啟動 Betguard...
echo 瀏覽器將會自動開啟 http://127.0.0.1:8765/
echo.
echo 按 Ctrl-C 可以停止 Betguard
echo.

start http://127.0.0.1:8765/

python -X utf8 -m betguard.webui.app

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ========================================
    echo   啟動失敗！
    echo   請執行 install_or_repair.bat 修復
    echo ========================================
    echo.
    pause
)
