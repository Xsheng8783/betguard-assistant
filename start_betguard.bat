@echo off
setlocal enabledelayedexpansion

REM Portable: use BAT location as project root
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"

for /f "tokens=*" %%i in ('git describe --tags --always 2^>nul') do set "GIT_TAG=%%i"
if "%GIT_TAG%"=="" set "GIT_TAG=(unknown)"

echo ========================================
echo   Betguard Assistant
echo   Local Review ^& Assist Mode
echo   %GIT_TAG%
echo ========================================
echo.

REM Check if server is already running on port 8765
netstat -ano 2>nul | findstr ":8765" | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
    echo [OK] Server already running on port 8765
    start http://127.0.0.1:8765
    goto :done
)

echo [..] Starting server on port 8765 ...
start "Betguard Server" cmd /c "cd /d %CD% && python -m betguard.webui.app"
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8765

echo [OK] Browser opened. Close server window when done.

:done
endlocal
