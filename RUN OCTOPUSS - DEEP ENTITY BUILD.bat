@echo off
setlocal
cd /d "%~dp0"
title OCTOPUSS - Deep Entity Build

echo.
echo ============================================================================
echo                       OCTOPUSS - DEEP ENTITY BUILD
echo ============================================================================
echo.
echo This performs the full multi-pass character intelligence build.
echo It will first resolve entities, then deeply rebuild every person profile.
echo.

python octopuss\run_octopuss.py --deep-entity-build
set EXITCODE=%ERRORLEVEL%

echo.
if "%EXITCODE%"=="0" (
    echo OCTOPUSS Deep Entity Build completed successfully.
) else (
    echo OCTOPUSS Deep Entity Build finished with errors. Exit code: %EXITCODE%
)
echo.
pause
exit /b %EXITCODE%
