@echo off
REM Double-click me: syncs the cloud's collected data into your local database.
cd /d "%~dp0"
echo Pulling latest from GitHub...
git pull --rebase origin main
echo Merging cloud archive into local database...
python merge_archive.py
echo.
echo Done. Close this window whenever.
pause
