@echo off
chcp 65001 >nul
title Betguard Assistant - 安裝/修復
cd /d "%~dp0.."

echo.
echo ========================================
echo   Betguard Assistant - 安裝與修復
echo ========================================
echo.

echo [1/3] 安裝 Python 依賴...
python -m pip install -e . --quiet
if %ERRORLEVEL% NEQ 0 (
    echo ❌ pip install 失敗
    echo 請確認 Python 3.11+ 已安裝，並加入 PATH
    pause
    exit /b 1
)
echo ✅ Python 依賴完成

echo.
echo [2/3] 安裝 Playwright Chromium（下牌網站受控瀏覽器）...
python -m playwright install chromium
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Playwright chromium 安裝失敗
    echo 請確認有網路連線
    pause
    exit /b 1
)
echo ✅ Playwright 完成

echo.
echo [3/3] 執行快速測試...
set PYTHONPATH=%cd%\src
python -X utf8 -m pytest tests/ -x -q --tb=short 2>nul
if %ERRORLEVEL% EQU 0 (
    echo ✅ 測試通過
) else (
    echo ⚠️  部分測試未通過，但 Betguard 可能仍可正常使用
)

echo.
echo ========================================
echo   安裝完成！
echo   請執行 start_betguard.bat 啟動
echo ========================================
echo.
pause
