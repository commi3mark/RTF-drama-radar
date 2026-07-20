@echo off
setlocal
cd /d "%~dp0"

echo =============================================================
echo STALINVO - GITHUB TRANSCRIPT SELECTION SYNC
echo =============================================================
echo.
echo This creates a private sync mirror inside the Transcript Grabber state folder.
echo Git may ask you to sign in to GitHub once.
echo.
python app\github_sync.py setup
if errorlevel 1 (
    echo.
    echo SETUP FAILED.
    echo Confirm Git is installed and sign in when prompted.
    pause
    exit /b 1
)
echo.
echo GITHUB SYNC READY.
echo Automatic transcript runs will now pull your selected-transcripts.txt queue,
echo collect only those selections on Stalinvo, and upload completed transcripts.
echo.
pause
