@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Takumi - Installation and Repair
if not exist "%~dp0scripts\Install-Takumi.ps1" (
  echo ERROR: The install files are missing.
  echo You must right-click the ZIP and choose Extract All first.
  echo Run REPAIR-Takumi.cmd from the extracted Takumi folder.
  echo.
  pause
  exit /b 2
)
where powershell.exe >nul 2>&1
if errorlevel 1 (
  echo ERROR: Windows PowerShell was not found.
  pause
  exit /b 3
)
echo.
echo ================= TAKUMI INSTALL / REPAIR =================
echo This terminal keeps errors visible and saves a setup log.
echo Downloads and installs external model dependencies when missing.
echo ===========================================================
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Install-Takumi.ps1"
set "RETURN_CODE=%ERRORLEVEL%"
echo.
echo Installer exit code: %RETURN_CODE%
echo Diagnostic logs: %LOCALAPPDATA%\Takumi\Logs
echo Press any key to close this installer.
pause >nul
exit /b %RETURN_CODE%
