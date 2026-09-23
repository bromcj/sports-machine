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
REM Deliberately NOT machine_daily.bat: that one pauses for a keypress and
REM refuses to run with uncommitted edits, both correct for a double-click
REM and wrong for a task running while nobody is watching.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

set PY=C:\Users\BromC\AppData\Local\Python\pythoncore-3.14-64\python.exe
set LOG=logs\cronstatus-latest.txt
if not exist logs mkdir logs

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
  REM Build today's predictions locally - the cloud cannot, it has no
  REM Statcast file or model. Free: no API credits.
  "%PY%" run_daily.py predict
  echo.
  "%PY%" run_daily.py paper
  echo.
  "%PY%" run_daily.py cronstatus
)

REM Keep a dated copy so a bad night can be compared against a good one.
copy /y "%LOG%" "logs\cronstatus-%DATE:~-4%%DATE:~4,2%%DATE:~7,2%.txt" >nul 2>&1
exit /b 0
