@echo off
setlocal EnableExtensions EnableDelayedExpansion
title CLEAN RTF-DRAMA-RADAR REPOSITORY

echo.
echo ============================================================
echo   RTF-DRAMA-RADAR CLEANUP
echo   Keeps Drama Radar. Removes local transcript/intelligence systems.
echo ============================================================
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo ERROR: Git is not installed or is not on PATH.
    pause
    exit /b 1
)

for /f "delims=" %%R in ('git remote get-url origin 2^>nul') do set "REMOTE=%%R"
if not defined REMOTE (
    echo ERROR: Run this file from the root of the RTF-drama-radar working copy.
    pause
    exit /b 1
)

echo Remote: !REMOTE!
echo !REMOTE! | findstr /i /c:"commi3mark/RTF-drama-radar" >nul
if errorlevel 1 (
    echo.
    echo REFUSING TO RUN: this is not the RTF-drama-radar repository.
    pause
    exit /b 1
)

git status --porcelain > "%TEMP%\rtf-radar-status.txt"
for %%A in ("%TEMP%\rtf-radar-status.txt") do if %%~zA GTR 0 (
    echo.
    echo REFUSING TO RUN: the working tree has uncommitted changes.
    echo Commit or stash them first, then run this again.
    type "%TEMP%\rtf-radar-status.txt"
    del "%TEMP%\rtf-radar-status.txt" >nul 2>nul
    pause
    exit /b 1
)
del "%TEMP%\rtf-radar-status.txt" >nul 2>nul

echo.
echo Updating main...
git switch main
if errorlevel 1 goto :fail

git pull --ff-only origin main
if errorlevel 1 goto :fail

for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%T"
set "BACKUP=backup-before-radar-cleanup-!STAMP!"

echo.
echo Creating and publishing recovery branch:
echo   !BACKUP!
git branch "!BACKUP!"
if errorlevel 1 goto :fail

git push origin "!BACKUP!"
if errorlevel 1 goto :fail

echo.
echo Removing systems that no longer belong in this repository...

if exist "02 - TRANSCRIPT GRABBER" (
    git rm -r -- "02 - TRANSCRIPT GRABBER"
    if errorlevel 1 goto :fail
) else (
    echo   02 - TRANSCRIPT GRABBER was already absent.
)

if exist "03 - EPISODE INTELLIGENCE" (
    git rm -r -- "03 - EPISODE INTELLIGENCE"
    if errorlevel 1 goto :fail
) else (
    echo   03 - EPISODE INTELLIGENCE was already absent.
)

echo.
git status --short

git diff --cached --quiet
if not errorlevel 1 (
    echo.
    echo Nothing needed cleaning. The repository was already radar-only.
    pause
    exit /b 0
)

echo.
echo Committing cleanup...
git commit -m "Clean repository to Drama Radar only"
if errorlevel 1 goto :fail

echo.
echo Pushing cleaned main...
git push origin main
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo CLEANUP COMPLETE
echo.
echo Kept:
echo   .github workflows
echo   01 - DRAMA RADAR
echo   root repository files
echo.
echo Removed:
echo   02 - TRANSCRIPT GRABBER
echo   03 - EPISODE INTELLIGENCE
echo.
echo Recovery branch:
echo   !BACKUP!
echo ============================================================
pause
exit /b 0

:fail
echo.
echo CLEANUP STOPPED. Nothing was force-pushed.
echo The recovery branch may already exist on GitHub:
echo   !BACKUP!
pause
exit /b 1
