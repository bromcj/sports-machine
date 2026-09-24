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
REM That is why there are five identical daily triggers rather than seven
REM league-specific ones: a schedule that has to be kept in step with the NFL
REM and NBA calendars is a schedule that will quietly drift out of step.
REM
REM Runs from the LOCAL machine, not GitHub Actions, because Actions fires
REM hours late (measured on this repo: a 14:00 cron at 17:58) and the whole
REM value of these pulls is that they land near kickoff.
REM
REM Costs credits. The cap is enforced inside collect.py, which stops BEFORE
REM the request that would pass it.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

echo [%DATE% %TIME%] NFL
python props\collect.py --run --sport nfl

echo [%DATE% %TIME%] NBA
python props\collect.py --run --sport nba

echo [%DATE% %TIME%] done
