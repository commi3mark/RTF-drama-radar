@echo off
cd /d "%~dp0"
if not exist state mkdir state
type nul > "state\stop-worker"
echo Stop requested. The worker will close safely after its current operation.
pause
