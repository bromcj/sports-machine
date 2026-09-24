@echo off
REM Double-click me: syncs the cloud's collected data into your local database.
goto start
REM The two lines below are where a run still reading the PREVIOUS version of
REM this file lands after its `git pull` (cmd resumes at a byte offset). They
REM must stay at that offset; the padding line above them keeps it there.
REM Everything from :start down is the real flow; new runs jump straight there.
REM (pad xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx)
if errorlevel 1 goto pullfail
goto merge

:start
REM Run from a copy in %TEMP%: the pull below can rewrite this file mid-run.
if /i "%~1"=="--from-copy" goto from_copy
copy /y "%~f0" "%TEMP%\sportsmachine-machine-daily.bat" >nul 2>&1
if errorlevel 1 goto in_place
call "%TEMP%\sportsmachine-machine-daily.bat" --from-copy "%~dp0."
exit /b %errorlevel%
:in_place
cd /d "%~dp0"
goto setup
:from_copy
cd /d "%~2"
:setup

REM STATUS.md and the gate-2 block of validation.json are rewritten by the
REM machine itself; see scheduled_check.bat. Anything else counts as an edit,
REM and a plain `git pull --rebase` would abort on it - silently, as far as a
REM double-clicked window is concerned.
git checkout -- STATUS.md >nul 2>&1
git diff --quiet -- validation.json
if errorlevel 1 (
  python model\validation.py --only-paper-changed
  if not errorlevel 1 git checkout -- validation.json
)
git diff --quiet
if errorlevel 1 goto dirty
git diff --staged --quiet
if errorlevel 1 goto dirty

echo Pulling latest from GitHub...
git pull --rebase origin main
if errorlevel 1 goto pullfail
:merge
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
echo  This folder has edits that aren't in git. Nothing was changed.
echo.
echo  This is the production folder: edit in sports-machine-dev and
echo  push from there. To throw the local edits away instead:
echo.
echo    git stash          (sets them aside; git stash pop restores)
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
