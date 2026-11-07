@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Takumi - Sumi 1.1 Model Setup
if not exist "%~dp0scripts\Ensure-Sumi-1.0.ps1" (
  echo ERROR: This file must be run from a completely extracted Takumi release.
  pause
  exit /b 2
)
echo.
echo    Takumi  /  Sumi 1.1

echo    Installs official local dependencies and downloads the model.
echo    No online AI account or paid API is needed.
echo    Download is approximately 8.3 GB on first setup.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Ensure-Sumi-1.0.ps1"
set "RESULT=%ERRORLEVEL%"
echo.
echo Sumi setup exited with code %RESULT%.
echo Setup logs: %LOCALAPPDATA%\Takumi\Logs
echo.
pause
exit /b %RESULT%
