@echo off
setlocal
set "TASKNAME=Drama Radar - Auto Transcripts"
schtasks /Change /TN "%TASKNAME%" /DISABLE
if errorlevel 1 (
    echo Could not disable the task. It may not be installed.
) else (
    echo Automatic transcript recovery disabled.
)
pause
