@echo off
setlocal
cd /d "%~dp0"

if not exist "logs" mkdir "logs"

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 run_scheduled_pipeline.py
) else (
    python run_scheduled_pipeline.py
)

exit /b %errorlevel%
