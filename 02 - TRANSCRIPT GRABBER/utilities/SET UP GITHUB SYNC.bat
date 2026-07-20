@echo off
setlocal
cd /d "%~dp0"

echo =============================================================
echo DRAMA RADAR - GITHUB TRANSCRIPT SYNC SETUP
echo =============================================================
echo.
echo This creates a private sync mirror inside radar\brain.
echo Your working MK2 folder does not need to become a Git repository.
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
echo Automatic transcript runs will now pull the latest Radar feed,
echo collect transcripts on Stalinvo, and upload new transcripts.
echo.
pause
