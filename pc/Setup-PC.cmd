@echo off
rem Double-click me. Windows asks for admin permission once, then runs setup-pc.ps1.
cd /d "%~dp0"
net session >nul 2>&1
if not errorlevel 1 goto run
if "%~1"=="" (
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
) else (
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '%*'"
)
exit /b
:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-pc.ps1" %*
echo.
pause
