@echo off
REM ---------------------------------------------------------------------------
REM The unattended local job. Windows Task Scheduler runs it twice a day; see
REM COMMANDS.md for how to change the times or remove it.
REM
REM   ~11:30am   merge the cloud's morning pull, PLACE paper bets, report
REM   ~10:00pm   merge the day's pulls, SETTLE finished paper bets, report
REM
REM One script, two triggers - bets/paper.py works out what is actually due.
REM It needs data/ (gitignored), so gate 2 can only be fed from this machine.
REM Costs no API credits and never places a real bet.
REM ---------------------------------------------------------------------------

REM RUN FROM A COPY. `git pull` can rewrite this file mid-run, and cmd reads
REM it by byte offset. A copy in %TEMP% is never pulled over.
if /i "%~1"=="--from-copy" goto from_copy
copy /y "%~f0" "%TEMP%\sportsmachine-scheduled-check.bat" >nul 2>&1
if errorlevel 1 goto in_place
call "%TEMP%\sportsmachine-scheduled-check.bat" --from-copy "%~dp0."
exit /b
:in_place
cd /d "%~dp0"
goto setup
:from_copy
cd /d "%~2"
:setup

set PY=C:\Users\BromC\AppData\Local\Python\pythoncore-3.14-64\python.exe
set LOG=logs\cronstatus-latest.txt
if not exist logs mkdir logs

REM Tracked files this job rewrites itself. STATUS.md is the cloud's. The
REM gate-2 block of validation.json is recomputed by `paper` below, so it is
REM discarded only if nothing else changed; a gate-1 record or an arm() is
REM never thrown away - the guard refuses instead.
git checkout -- STATUS.md >nul 2>&1
git diff --quiet -- validation.json
if errorlevel 1 (
  "%PY%" model\validation.py --only-paper-changed
  if not errorlevel 1 git checkout -- validation.json
)

REM REFUSE TO RUN ON A DIRTY FOLDER, loudly: ALERTS.md, a desktop alert and
REM a failing exit code. See docs/production-setup.md.
git diff --quiet
if errorlevel 1 goto dirty
git diff --staged --quiet
if errorlevel 1 goto dirty
goto proceed

:dirty
> "%LOG%" (
  echo Generated %DATE% %TIME%
  echo.
  echo REFUSED TO RUN: this folder has uncommitted changes.
  echo   %CD%
  echo.
  echo Nothing was pulled, predicted, bet, settled or scored.
  echo Commit the change from the development folder, or discard it here.
  echo.
  echo Uncommitted:
  git status --short
)
type "%LOG%"
"%PY%" -c "import datetime as d, notify; notify.deliver([{'level': 'ERROR', 'check': 'scheduled job', 'detail': 'REFUSED to run: uncommitted changes in the production folder. Nothing was pulled, bet or settled. See logs/cronstatus-latest.txt'}], d.datetime.now(d.timezone.utc).isoformat(timespec='seconds'))"
exit /b 1
REM Padding. The run that first pulls in a new version of this file is still
REM reading the OLD one, and carries on at the old byte offset of the line
REM after `git pull`. Keep that offset unchanged when editing above this line.
REM (pad xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx)

:proceed
git pull --rebase --autostash origin main >nul 2>&1
if errorlevel 1 (
  set PULLNOTE=could not pull from GitHub - this report may be stale
) else (
  set PULLNOTE=
)

> "%LOG%" (
  echo Generated %DATE% %TIME%
  if defined PULLNOTE echo WARNING: %PULLNOTE%
  echo.
  REM Bring the cloud's pulls into the local database first, so paper bets
  REM are placed against prices that actually arrived.
  "%PY%" merge_archive.py
  echo.
  REM Yesterday's and today's results. Nothing else fetches a completed
  REM game: both score pulls asked only for today, and the cloud's morning
  REM run fires hours before any game that day ends. Without this, paper
  REM bets never settle. Free: ESPN and MLB Stats, no odds credits.
  "%PY%" run_daily.py finals
  echo.
  REM Build today's predictions locally - the cloud cannot, it has no
  REM Statcast file or model. Free: no API credits.
  "%PY%" run_daily.py predict
  echo.
  "%PY%" run_daily.py paper
  echo.
  REM Every finished game scored against the market, bet or not. Reports
  REM only - it clears no gate. Free.
  "%PY%" run_daily.py market
  echo.
  "%PY%" run_daily.py cronstatus
  echo.
  REM Whole-system health, appended to logs\health.jsonl, and delivered to
  REM ALERTS.md plus a desktop alert for anything serious (notify.py).
  "%PY%" monitor.py
)

REM Keep a dated copy per run, so a bad night can be compared against a good
REM one. The hour is in the name: the 10pm copy used to overwrite the 11:30.
set HH=%TIME:~0,2%
set HH=%HH: =0%
copy /y "%LOG%" "logs\cronstatus-%DATE:~-4%%DATE:~4,2%%DATE:~7,2%-%HH%.txt" >nul 2>&1
exit /b 0
