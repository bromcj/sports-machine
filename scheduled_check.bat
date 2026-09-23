@echo off
REM ---------------------------------------------------------------------------
REM The unattended local job. Windows Task Scheduler runs it twice a day; see
REM COMMANDS.md for how to change the times or remove it.
REM
REM   ~11:30am   merge the cloud's morning pull, PLACE paper bets, report
REM   ~10:00pm   merge the day's pulls, SETTLE finished paper bets, report
REM
REM One script, two triggers - bets/paper.py works out what is actually due,
REM so neither run needs to know which one it is.
REM
REM Why this exists at all: placing a paper bet needs the trained model and
REM the Statcast file, and both live in data/, which is gitignored and must
REM stay out of a public repo. The cloud runner therefore CANNOT build
REM predictions - its own log says "no predictions: No Statcast parquet" - so
REM gate 2 can only ever be fed from this machine.
REM
REM Costs no API credits and never places a real bet. bets/engine.py still
REM refuses every real wager until all three gates pass.
REM
REM Deliberately NOT machine_daily.bat: that one pauses for a keypress,
REM which is right for a double-click and wrong for an unattended task.
REM It DOES share the refusal on uncommitted edits - see below.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

set PY=C:\Users\BromC\AppData\Local\Python\pythoncore-3.14-64\python.exe
set LOG=logs\cronstatus-latest.txt
if not exist logs mkdir logs

REM ---------------------------------------------------------------------------
REM REFUSE TO RUN ON A DIRTY FOLDER.
REM
REM This job runs unattended at 11:30am and 10pm. Pointed at a folder somebody
REM is editing, a half-finished change becomes production: it places paper
REM bets, writes to the database and settles wagers using whatever happened to
REM be saved at that moment.
REM
REM Refusing is the whole reason a separate production checkout exists - see
REM docs/production-setup.md. The refusal is LOGGED, because a job that
REM silently does nothing is worse than one that fails loudly.
REM ---------------------------------------------------------------------------
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
  echo.
  echo If this is your development folder that is expected - the scheduled
  echo task should point at a separate production checkout instead. See
  echo docs/production-setup.md
  echo.
  echo Uncommitted:
  git status --short
)
type "%LOG%"
exit /b 0

:proceed

REM Pull quietly so the archive is current. If this fails - no internet, a
REM conflict, local edits - carry on anyway: a slightly stale report is far
REM more useful than no report, and the report says when it was generated.
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
  REM Whole-system health, appended to logs\health.jsonl. Nothing is sent
  REM anywhere yet - see DELIVERY in monitor.py.
  "%PY%" monitor.py
)

REM Keep a dated copy so a bad night can be compared against a good one.
copy /y "%LOG%" "logs\cronstatus-%DATE:~-4%%DATE:~4,2%%DATE:~7,2%.txt" >nul 2>&1
exit /b 0
