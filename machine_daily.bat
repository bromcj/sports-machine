@echo off
REM Double-click me: syncs the cloud's collected data into your local database.
cd /d "%~dp0"

REM A plain `git pull --rebase` aborts with "cannot pull with rebase: You have
REM unstaged changes" if anything local was edited, and then the merge below
REM never runs - silently, as far as a double-clicked window is concerned.
git diff --quiet
if errorlevel 1 goto dirty
git diff --staged --quiet
if errorlevel 1 goto dirty

echo Pulling latest from GitHub...
git pull --rebase origin main
if errorlevel 1 goto pullfail
echo Merging cloud archive into local database...
python merge_archive.py
echo.
REM Back up AFTER the merge, so the newest backup includes what just arrived.
REM A failure here is reported but does not fail the sync - the merge already
REM succeeded and that is the part you cannot redo.
echo Backing up the database...
python backup.py
echo.
echo Done. Close this window whenever.
goto end

:dirty
echo.
echo ---------------------------------------------------------------
echo  You have local edits that aren't saved to git yet.
echo  Nothing was changed. To continue, either:
echo.
echo    git stash          (set your edits aside, restore later)
echo    git commit -am "wip"   (save them permanently)
echo.
echo  Then double-click this file again.
echo ---------------------------------------------------------------
goto end

:pullfail
echo.
echo ---------------------------------------------------------------
echo  The pull from GitHub failed - your database was NOT touched.
echo  Most likely no internet, or the rebase hit a conflict.
echo  Run this to see what happened:   git status
echo ---------------------------------------------------------------

:end
pause
