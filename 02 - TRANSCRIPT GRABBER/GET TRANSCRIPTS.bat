@echo off
cd /d "%~dp0"
python app\get_transcripts.py --publish --limit 10
pause
