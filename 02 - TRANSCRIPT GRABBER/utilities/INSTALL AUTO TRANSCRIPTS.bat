@echo off
setlocal
cd /d "%~dp0"
set "TASKNAME=Drama Radar - Auto Transcripts"
set "RUNNER=%~dp0GET TRANSCRIPTS AUTO.bat"

echo Installing automatic transcript recovery...
echo.
echo Schedule: every hour
echo Batch size: 10 videos per run
echo Log: radar\brain\auto-transcripts.log
echo.

schtasks /Create /TN "%TASKNAME%" /TR "\"%RUNNER%\"" /SC HOURLY /MO 1 /F
if errorlevel 1 (
    echo.
    echo INSTALL FAILED.
    echo Try right-clicking this file and choosing Run as administrator.
    pause
    exit /b 1
)

schtasks /Run /TN "%TASKNAME%" >nul 2>&1

echo.
echo AUTO TRANSCRIPTS ENABLED.
echo The first recovery run has been started now.
echo Future runs will happen every hour while Stalinvo is available.
echo Livestreams are held until six hours after ending.
echo Failed items are moved behind fresh eligible videos.
echo Existing cooldown protection will pause requests if YouTube blocks the IP.
echo.
echo Use STOP AUTO TRANSCRIPTS.bat to disable it.
pause
