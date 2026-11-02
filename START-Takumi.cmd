@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Takumi - Start
set "INSTALLED=%LOCALAPPDATA%\Programs\Takumi\Takumi.exe"
set "BUILT=%~dp0dist\Takumi-win-x64\Takumi.exe"
if exist "%INSTALLED%" goto RUN_INSTALLED
if exist "%BUILT%" goto RUN_BUILT

echo Takumi has not been compiled on this computer yet.
echo Starting the native Windows setup with diagnostics...
echo.
call "%~dp0REPAIR-Takumi.cmd"
exit /b %errorlevel%

:RUN_INSTALLED
set "EXE=%INSTALLED%"
goto RUN_APP

:RUN_BUILT
set "EXE=%BUILT%"
goto RUN_APP

:RUN_APP
echo Starting native Takumi application:
echo %EXE%
start "" "%EXE%"
if errorlevel 1 goto APP_FAILED
rem Give the window time to open and catch an immediate process failure.
timeout /t 3 /nobreak >nul
tasklist /FI "IMAGENAME eq Takumi.exe" 2>nul | find /I "Takumi.exe" >nul
if errorlevel 1 goto APP_FAILED
exit /b 0

:APP_FAILED
echo.
echo Takumi exited immediately or Windows could not launch the EXE.
echo Inspect %%LOCALAPPDATA%%\Takumi\Logs for the desktop startup log.
echo To repair the installation, double-click REPAIR-Takumi.cmd in this folder.
echo.
pause
exit /b 1
