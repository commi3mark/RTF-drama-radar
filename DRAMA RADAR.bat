@echo off
setlocal
cd /d "%~dp0"

title Drama Radar

echo.
echo Launching Drama Radar...
echo.

py run_drama_radar_all.py

set EXITCODE=%ERRORLEVEL%

echo.
if "%EXITCODE%"=="0" (
    echo Drama Radar finished successfully.
) else (
    echo Drama Radar finished with errors. Exit code: %EXITCODE%
)

echo.
pause
exit /b %EXITCODE%
