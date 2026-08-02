@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"

set "GIT_TAG=(unknown)"
for /f "delims=" %%i in ('git describe --tags --always 2^>nul') do set "GIT_TAG=%%i"

echo ========================================
echo   Betguard Assistant
echo   Local Review and Assist Mode
echo   %GIT_TAG%
echo ========================================
echo.

set "OCCUPIED_PID="
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8765" ^| findstr "LISTENING"') do set "OCCUPIED_PID=%%p"

if defined OCCUPIED_PID goto port_in_use

echo [START] Starting server on port 8765...
start "Betguard Server" cmd /k "python -m betguard.webui.app"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765/"
echo [OK] Browser opened.
exit /b 0

:port_in_use
echo ========================================
echo Port 8765 is already in use.
echo Existing PID: %OCCUPIED_PID%
echo Startup cancelled to avoid another version.
echo Close the old Betguard or Python server.
echo ========================================
pause
exit /b 1
