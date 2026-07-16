@echo off
cd /d "%~dp0"
python transcripts\get_transcripts.py --publish --limit 10
pause
