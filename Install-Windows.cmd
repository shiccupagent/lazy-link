@echo off
rem Lazy Link: set this Windows computer up as the small (controller) computer. No admin needed; safe to run again.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*
set RC=%ERRORLEVEL%
echo.
pause
exit /b %RC%
