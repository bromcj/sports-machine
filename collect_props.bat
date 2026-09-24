@echo off
REM ---------------------------------------------------------------------------
REM Phase 0 collection. Windows Task Scheduler runs this several times a day.
REM
REM It is deliberately dumb about the calendar: the events list is FREE, so the
REM task can fire every day at every slot and spend nothing on the days with no
REM games. props/collect.py pulls a game only when kickoff is inside its lead
REM window and the game has not already been recorded at a nearer time, so
REM every game gets exactly one snapshot and an extra run costs zero.
REM
REM Runs from the LOCAL machine, not GitHub Actions, because Actions fires
REM hours late (measured on this repo: a 14:00 cron at 17:58) and the whole
REM value of these pulls is that they land near kickoff.
REM
REM Costs credits. The cap is a running total for the calendar month, kept in
REM data\props_live\requests.jsonl; collect.py stops BEFORE the request that
REM would pass it. Output is appended to logs\collect.log - the task itself
REM always reports success, so that file is where a failure shows.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

set PY=C:\Users\BromC\AppData\Local\Python\pythoncore-3.14-64\python.exe
if not exist logs mkdir logs

>> logs\collect.log 2>&1 (
  echo [%DATE% %TIME%] NFL
  "%PY%" props\collect.py --run --sport nfl
  echo [%DATE% %TIME%] NBA
  "%PY%" props\collect.py --run --sport nba
  echo [%DATE% %TIME%] done
)
