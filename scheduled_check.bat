@echo off
REM ---------------------------------------------------------------------------
REM Unattended daily check: did the cloud collector fire, and did it catch
REM prices before first pitch? Run by Windows Task Scheduler; see
REM COMMANDS.md for how to change the time or remove it.
REM
REM Nothing here costs API credits and nothing here writes to the database.
REM It pulls the repo, reads archive/, and writes a report you can open later.
REM
REM Deliberately NOT machine_daily.bat: that one pauses for a keypress and
REM refuses to run with uncommitted edits, both of which are correct for a
REM double-click and wrong for a task running while nobody is watching.
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
  "%PY%" run_daily.py cronstatus
)

REM Keep a dated copy so a bad night can be compared against a good one.
copy /y "%LOG%" "logs\cronstatus-%DATE:~-4%%DATE:~4,2%%DATE:~7,2%.txt" >nul 2>&1
exit /b 0
