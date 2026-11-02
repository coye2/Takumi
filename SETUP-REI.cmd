@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
echo.
echo ================================================================
echo  TAKUMI - REI 1.0 LIGHTWEIGHT MODEL SETUP
 echo  Optimized starting profile for 8 GB NVIDIA GPUs
 echo  No subscription required. Official model download ~3.2 GB.
echo ================================================================
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\Ensure-Sumi-1.0.ps1" -PreferSmallModel
set "result=%errorlevel%"
echo.
if "%result%"=="0" (
 echo Rei 1.0 setup completed. Open Takumi and select Rei 1.0.
) else (
 echo Setup failed with code %result%. Review the Takumi Logs folder.
)
echo.
pause
exit /b %result%
