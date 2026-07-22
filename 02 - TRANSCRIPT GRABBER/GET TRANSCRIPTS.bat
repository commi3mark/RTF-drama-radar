@echo off
setlocal
cd /d "%~dp0"
title RTF Transcript Grabber - Priority, Piper, Sleepers and Archive
python app\run_priority_transcripts.py
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (
  echo Transcript Grabber finished and is now OFF.
) else (
  echo Transcript Grabber stopped with exit code %RESULT%.
)
echo.
pause
exit /b %RESULT%
