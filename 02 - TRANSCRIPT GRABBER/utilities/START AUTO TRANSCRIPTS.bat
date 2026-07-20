@echo off
setlocal
set "TASKNAME=Drama Radar - Auto Transcripts"
schtasks /Change /TN "%TASKNAME%" /ENABLE
if errorlevel 1 (
    echo Could not enable the task. Run INSTALL AUTO TRANSCRIPTS.bat first.
) else (
    schtasks /Run /TN "%TASKNAME%" >nul 2>&1
    echo Automatic transcript recovery enabled and started.
)
pause
