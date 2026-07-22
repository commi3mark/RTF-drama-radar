@echo off
setlocal
cd /d "%~dp0.."

echo =============================================================
echo TRANSCRIPT GRABBER - GITHUB OUTPUT SYNC
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
echo GET TRANSCRIPTS.bat will upload completed transcripts and indexes.
echo PRIORITY TRANSCRIPTS.txt always remains the local source of truth.
echo.
pause
